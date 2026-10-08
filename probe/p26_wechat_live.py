# -*- coding: utf-8 -*-
"""P26 微信真机端到端：真实覆盖层 × 暗色窗口 × 前台判定

p22/p25 分别验了「能抓帧」和「画得对」，但都不是真机上可见的效果。
这一步跑完整链路：

    PrintWindow 抓帧 → OCR → 匹配（keep 档，保证有词可画）
      → 真实 Overlay 挂到微信窗口 → 抢微信到前台
      → 等覆盖层跟随 → BitBlt 抓整屏 → 数高亮像素

验收点：
  1. 覆盖层窗口几何 == 微信客户区几何（对齐）
  2. 微信在前台时覆盖层可见（遮挡保护不误杀）
  3. 截屏里能在预期坐标找到高亮像素（真的合成上屏了）

用法: python probe/p26_wechat_live.py [秒数]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image

import main as app
from src.capture import find_windows, grab_window
from src.linkage import force_foreground
from src.overlay import Overlay

grab_desktop = app.grab_desktop

OUT = os.path.join(ROOT, "out", "live")
os.makedirs(OUT, exist_ok=True)

# 亮底/暗底两套描边色的代表像素，判定时都接受
LINE_COLORS = {
    "light": (37, 99, 235),     # PALETTE['flat']['line']
    "dark": (255, 214, 64),     # PALETTE_DARK['flat']['line']
}


def count_highlight_px(arr, rect, origin, tol=70):
    """在整屏截图的对应位置数高亮描边像素"""
    x, y, w, h = rect
    sx, sy = x + origin[0], y + origin[1]
    # 往外扩 4px，因为描边压在框线上
    sub = arr[max(sy - 4, 0):sy + h + 4, max(sx - 4, 0):sx + w + 4].astype(np.int16)
    if sub.size == 0:
        return 0, None
    best, bestname = 0, None
    for name, c in LINE_COLORS.items():
        d = np.abs(sub - np.array(c, dtype=np.int16)).sum(axis=2)
        n = int((d < tol).sum())
        if n > best:
            best, bestname = n, name
    return best, bestname


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    qapp = QApplication.instance() or QApplication(sys.argv)
    cfg = app.load_config()
    # 强制 keep：微信那一帧只有「老百姓」一个弱词，context 档会被拦掉，
    # 这里要验的是「画得上去」，所以放行全部弱词。
    cfg.setdefault("match", {})["weak_mode"] = "keep"
    matcher = app.make_matcher(cfg, verbose=False)
    ocr = app.make_ocr(cfg, verbose=False)

    target = None
    for w in find_windows(min_size=(200, 150), visible_only=True):
        if w.exe.lower() == "weixin.exe":
            target = w
            break
    if target is None:
        print("微信窗口没找到，先打开微信")
        return
    print("目标 [%s] 0x%08X %dx%d @(%d,%d)  title=%r"
          % (target.app, target.hwnd, target.width, target.height,
             target.client[0], target.client[1], target.title))

    arr = grab_window(target)
    coef = getattr(ocr, "det_coef", 0.85)
    lo, hi = getattr(ocr, "det_range", (640, 2400))
    ls = max(arr.shape[1], arr.shape[0])
    ocr.reconfigure(det_limit=max(lo, min(hi, int(ls * coef))))
    boxes = ocr.recognize(arr)
    items = app.match_boxes(boxes, matcher, arr)
    print("OCR %d 框，命中 %d 处：%s"
          % (len(boxes), len(items),
             "、".join("%s(%s)%s" % (i["label"], i["code"],
                                    "暗底" if i.get("dark") else "亮底") for i in items)))

    # ---- 抢前台（走 linkage 已验证过的 AttachThreadInput 技巧）----
    ok = force_foreground(target.hwnd, retries=3)
    print("抢前台: %s" % ("成功" if ok else "失败"))

    ov = Overlay(clickable=False)
    ov.attach_to(target)
    ov.set_highlights(items)
    ov.show()

    report = {}

    def stage1():
        """等对齐全稳下来再检查"""
        t = ov._target
        print("\n[对齐] 覆盖层 %dx%d@(%d,%d)   目标客户区 %dx%d@(%d,%d)   %s"
              % (ov.width(), ov.height(), ov.x(), ov.y(),
                 t.width, t.height, t.client[0], t.client[1],
                 "✓ 一致" if (ov.x(), ov.y(), ov.width(), ov.height())
                 == (t.client[0], t.client[1], t.width, t.height) else "✗ 不符"))
        print("[前台] 目标窗口在前台 = %s   覆盖层可见 = %s"
              % (ov._is_foreground(), ov.isVisible()))
        report["fg"] = ov._is_foreground()
        report["aligned"] = ((ov.x(), ov.y(), ov.width(), ov.height())
                             == (t.client[0], t.client[1], t.width, t.height))

    def stage2():
        """整屏截图，数高亮像素"""
        desk, origin = grab_desktop()
        p = os.path.join(OUT, "wechat_live_desktop.png")
        Image.fromarray(desk).save(p)
        print("\n[截屏] %dx%d  虚拟桌面原点(%d,%d) → %s"
              % (desk.shape[1], desk.shape[0], origin[0], origin[1],
                 os.path.relpath(p, ROOT)))
        total = 0
        for it in items:
            n, col = count_highlight_px(desk, it["rect"], origin)
            total += n
            print("   %-10s rect=%-20s 高亮像素 %4d  色系=%s"
                  % (it["label"], str(it["rect"]), n, col or "-"))
        report["px"] = total

        # 裁剪微信区域出图
        x0 = target.client[0] - origin[0]
        y0 = target.client[1] - origin[1]
        crop = desk[max(y0, 0):y0 + target.height, max(x0, 0):x0 + target.width]
        if crop.size:
            cp = os.path.join(OUT, "wechat_live_crop.png")
            Image.fromarray(crop).save(cp)
            print("   → %s" % os.path.relpath(cp, ROOT))
        finish()

    def finish():
        print("\n" + "=" * 70)
        print("对齐 %s | 前台判定 %s | 屏上高亮像素 %d"
              % ("✓" if report.get("aligned") else "✗",
                 "✓" if report.get("fg") else "✗",
                 report.get("px", 0)))
        print("=" * 70)
        if report.get("px"):
            print("结论：覆盖层真的合成上屏了 ✓（暗色窗口下仍可见）")
        else:
            print("结论：屏幕上没找到高亮像素 ✗")
        ov.close()
        qapp.quit()

    QTimer.singleShot(int(secs * 1000 * 0.5), stage1)
    QTimer.singleShot(int(secs * 1000), stage2)
    return qapp.exec()


if __name__ == "__main__":
    sys.exit(main() or 0)
