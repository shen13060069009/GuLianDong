# -*- coding: utf-8 -*-
"""P0-17 高亮覆盖层验证（含对齐精度客观校验）

流程：
    造一个聊天窗口 → 取屏 → OCR → 匹配 → 在高亮层上画框 → 截屏

关键：怎么客观证明「框画准了」？
不能只看截图（人眼容易自我说服）。本探针用两步硬校验：

  1. 反查法：把每个高亮框从原图裁出来，单独跑一次 OCR，
     如果框是准的，裁出来的图应当只包含那个股票名（或它的绝大部分）。
  2. 越界法：检查每个框的坐标是否落在图像范围内、是否互相重叠。

两步都通过，才说明渲染层的坐标链路是对的。
"""
import ctypes
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel

from src.capture import grab_window, window_info
from src.matcher import build_default
from src.ocr import StockOCR
from src.overlay import Overlay

OUT = os.path.join(ROOT, "out")
os.makedirs(OUT, exist_ok=True)
IMG = os.path.join(OUT, "synthetic_chat.png")


class ChatWindow(QLabel):
    """模拟一个聊天窗口（内容为 P0-11 生成的合成聊天图）"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("模拟聊天窗口（验证用）")
        self.setPixmap(QPixmap(IMG))
        self.setFixedSize(760, 820)
        self.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.setStyleSheet("background:#f7f8fa;")


def main():
    if not os.path.exists(IMG):
        print("缺 %s，请先跑 probe/p11_e2e_pipeline.py" % IMG)
        return

    app = QApplication(sys.argv)
    win = ChatWindow()
    win.move(300, 140)
    win.show()
    for _ in range(20):
        app.processEvents()
        time.sleep(0.03)
    time.sleep(0.5)
    app.processEvents()

    hwnd = int(win.winId())
    info = window_info(hwnd)
    print("=" * 100)
    print("P0-17 高亮覆盖层验证")
    print("=" * 100)
    print("  测试窗口 hwnd=0x%08X  客户区 %dx%d @(%d,%d)"
          % (hwnd, info.width, info.height, info.client[0], info.client[1]))

    # ---------- 链路 ----------
    arr = grab_window(info)
    print("  取屏: %s" % (("OK %dx%d" % (arr.shape[1], arr.shape[0])) if arr is not None else "失败"))
    if arr is None:
        return
    arr = np.ascontiguousarray(arr)

    ocr = StockOCR(verbose=True)
    t0 = time.perf_counter()
    boxes = ocr.recognize(arr)
    t_ocr = (time.perf_counter() - t0) * 1000

    matcher = build_default(verbose=True)
    t0 = time.perf_counter()
    raw = []
    for b in boxes:
        n = max(len(b.text), 1)
        for m in matcher.match(b.text):
            x0 = b.x0 + int(b.w * m.start / n)
            x1 = b.x0 + int(b.w * m.end / n)
            raw.append((b, m, (max(x0 - 1, 0), max(b.y0 - 3, 0),
                               x1 - x0 + 3, b.h + 6)))
    t_match = (time.perf_counter() - t0) * 1000
    print("  OCR %.0f ms / %d 块    匹配 %.2f ms / %d 处"
          % (t_ocr, len(boxes), t_match, len(raw)))

    # ---------- 对齐校验 1：越界与重叠 ----------
    print("\n[校验 1] 框坐标合法性与重叠检查")
    bad_bounds = []
    for b, m, (x, y, w, h) in raw:
        if x < 0 or y < 0 or x + w > arr.shape[1] or y + h > arr.shape[0]:
            bad_bounds.append((m.surface, (x, y, w, h)))
    overlaps = 0
    rects = [(x, y, w, h) for _, _, (x, y, w, h) in raw]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            ax, ay, aw, ah = rects[i]
            bx, by, bw, bh = rects[j]
            if not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay):
                overlaps += 1
    print("      越界框 %d 个%s" % (len(bad_bounds),
                                   ("  " + str(bad_bounds[:3])) if bad_bounds else "  ✓"))
    print("      相互重叠 %d 对%s" % (overlaps, "  ✓" if overlaps == 0 else ""))

    # ---------- 对齐校验 2：反查法 ----------
    print("\n[校验 2] 反查法：把每个框裁出来单独 OCR，看是否只含目标词")
    print("      %-14s %-14s %-10s %s" % ("期望词", "裁出后识别", "是否命中", "框坐标"))
    print("      " + "-" * 80)
    ok_cnt = 0
    for b, m, (x, y, w, h) in raw:
        pad = 2
        sub = arr[max(y - pad, 0):y + h + pad, max(x - pad, 0):x + w + pad]
        if sub.size == 0:
            continue
        sub_boxes = ocr.recognize(np.ascontiguousarray(sub))
        sub_text = "".join(sb.text for sb in sub_boxes)
        good = m.surface in sub_text
        ok_cnt += 1 if good else 0
        print("      %-14s %-14s %-10s (%d,%d,%d,%d)"
              % (m.surface, sub_text[:13] or "(空)", "✓" if good else "✗", x, y, w, h))
    total = len(raw)
    print("\n      反查通过率: %d/%d = %.0f%%" % (ok_cnt, total, ok_cnt / max(total, 1) * 100))

    # ---------- 渲染覆盖层 ----------
    print("\n[3] 渲染高亮覆盖层")
    overlay = Overlay(clickable=True)
    items = []
    for b, m, rect in raw:
        state = "concept" if m.layer == "L3" else "flat"
        c = m.primary
        items.append({
            "rect": rect,
            "state": state,
            "label": c.name if c else m.surface,
            "code": ("%s %s" % (m.kind.upper(), c.code)) if c else m.kind.upper(),
            "match": m,
        })
    overlay.attach_to(info)
    overlay.set_highlights(items)
    print("      已挂载 %d 个高亮词，目标 %s" % (len(items), info))

    for _ in range(30):
        app.processEvents()
        time.sleep(0.02)
    time.sleep(0.4)
    app.processEvents()

    # ---------- 截屏 ----------
    import mss
    from PIL import Image
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0])
        full = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    path = os.path.join(OUT, "p17_overlay_result.png")
    full.save(path)

    # 裁出窗口区域，便于查看
    l, t = info.client[0], info.client[1]
    crop = full.crop((l, t, l + info.width, t + info.height))
    crop_path = os.path.join(OUT, "p17_overlay_window.png")
    crop.save(crop_path)

    print("      全屏截图: %s" % path)
    print("      窗口截图: %s" % crop_path)

    # ---------- 判定 ----------
    print("\n" + "=" * 100)
    print("判定")
    print("=" * 100)
    passed = (len(bad_bounds) == 0 and overlaps == 0 and ok_cnt == total and total > 0)
    print("  越界框      : %d %s" % (len(bad_bounds), "✓" if not bad_bounds else "✗"))
    print("  重叠对      : %d %s" % (overlaps, "✓" if overlaps == 0 else "✗"))
    print("  反查通过率  : %.0f%% %s" % (ok_cnt / max(total, 1) * 100, "✓" if ok_cnt == total else "✗"))
    print("  渲染高亮数  : %d" % len(items))
    print("  → 覆盖层坐标链路 %s" % ("正确 ✓" if passed else "存在问题 ✗"))

    QTimer.singleShot(1200, app.quit)
    app.exec()
    overlay.remove_mouse_hook()


if __name__ == "__main__":
    main()
