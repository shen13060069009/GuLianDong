# -*- coding: utf-8 -*-
"""P25 真实 Qt 覆盖层离屏渲染验收

p22 的合成图是 numpy 近似画的，只能看个大概。这一步用
**真正的 Overlay.paintEvent** 渲染到 QImage，再叠到真机帧上 —— 
看到什么就等价于屏幕上会显示什么（唯一差别是没有 DWM 合成）。

同时输出亮底/暗底两套配色的文字可读性指标，用于定档复核。

用法: python probe/p25_overlay_render.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image

import main as app
from src.overlay import Overlay

CROSS = os.path.join(ROOT, "out", "cross")
OUT = os.path.join(ROOT, "out", "render")
os.makedirs(OUT, exist_ok=True)

# 每帧取一处命中做放大对照；没有命中的帧跳过
CASES = [
    ("飞书_1325x993_frame.png", "飞书亮底"),
    ("微信4.x_1086x924_frame.png", "微信暗底"),
    ("钉钉_1024x640_frame.png", "钉钉亮底"),
]


def luma(px):
    r, g, b = [float(c) / 255 for c in px[:3]]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    x, y = sorted([luma(a), luma(b)], reverse=True)
    return (x + 0.05) / (y + 0.05)


def render_overlay(items, w, h):
    """用真实 Overlay 渲染到 RGBA 数组"""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImage

    ov = Overlay(clickable=False)
    ov.resize(QSize(w, h))
    ov.set_highlights(items)
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    ov.render(img)
    bpl = img.bytesPerLine()
    # 坑：bytesPerLine 是「字节数/行」，reshape 时要先除以 4 换成像素数
    buf = np.frombuffer(img.constBits(), dtype=np.uint8)
    buf = buf[:bpl * h].reshape(h, bpl // 4, 4)[:, :w, :]
    ov.deleteLater()
    return buf.copy()


def measure(arr, items, label):
    """文字可读性：高亮区里「文字 vs 填充」与「填充 vs 原底色」"""
    print("\n  %s" % label)
    for it in items:
        x, y, w, h = it["rect"]
        x, y = max(x, 0), max(y, 0)
        sub = arr[y:y + h, x:x + w].reshape(-1, 3).astype(float)
        if sub.size == 0:
            continue
        g = sub.mean(axis=1)
        bg = tuple(int(v) for v in sub[g <= np.percentile(g, 55)].mean(axis=0))
        tx = tuple(int(v) for v in sub[g >= np.percentile(g, 88)].mean(axis=0))
        print("    %-8s rect=%-20s %-4s  原底色RGB%-16s 原文字对比度 %.2f"
              % (it["label"], str(it["rect"]), "暗底" if it.get("dark") else "亮底",
                 str(bg), contrast(bg, tx)))


def main():
    from PySide6.QtWidgets import QApplication
    qapp = QApplication.instance() or QApplication(sys.argv)

    cfg = app.load_config()
    matcher = app.make_matcher(cfg, verbose=False)
    ocr = app.make_ocr(cfg, verbose=False)

    summary = []
    for fname, tag in CASES:
        path = os.path.join(CROSS, fname)
        if not os.path.exists(path):
            print("跳过（缺帧）", fname)
            continue
        arr = np.asarray(Image.open(path).convert("RGB"))
        H, W = arr.shape[:2]
        coef = getattr(ocr, "det_coef", 0.85)
        lo, hi = getattr(ocr, "det_range", (640, 2400))
        ocr.reconfigure(det_limit=max(lo, min(hi, int(max(W, H) * coef))))
        boxes = ocr.recognize(arr)

        # 用 keep 档跑，保证弱词也进入画面，能同时验证配色
        cfg2 = app.load_config()
        cfg2.setdefault("match", {})["weak_mode"] = "keep"
        m2 = app.make_matcher(cfg2, verbose=False)
        items = app.match_boxes(boxes, m2, arr)

        print("=" * 78)
        print("[%s] %dx%d  文本框 %d  命中 %d" % (tag, W, H, len(boxes), len(items)))
        if not items:
            print("  无命中，跳过渲染")
            summary.append((tag, 0, "-", "-"))
            continue
        for it in items:
            print("   %-10s %-8s %-4s rect=%s"
                  % (it["label"], it["code"],
                     "暗底" if it.get("dark") else "亮底", it["rect"]))

        over = render_overlay(items, W, H)
        a = over[:, :, 3:4].astype(np.float32) / 255.0
        # ARGB32_Premultiplied → 通道序为 BGRA
        rgb = over[:, :, [2, 1, 0]].astype(np.float32)
        comp = (rgb * a + arr.astype(np.float32) * (1 - a)).astype(np.uint8)
        outp = os.path.join(OUT, fname.replace("_frame.png", "_qt.png"))
        Image.fromarray(comp).save(outp)
        print("   → %s" % os.path.relpath(outp, ROOT))

        # 放大对照
        it0 = items[0]
        x, y, w, h = it0["rect"]
        x0, y0 = max(x - 100, 0), max(y - 26, 0)
        x1, y1 = min(x + w + 140, W), min(y + h + 26, H)
        sc = 3
        t1 = Image.fromarray(arr[y0:y1, x0:x1]).resize(((x1 - x0) * sc, (y1 - y0) * sc), Image.LANCZOS)
        t2 = Image.fromarray(comp[y0:y1, x0:x1]).resize(((x1 - x0) * sc, (y1 - y0) * sc), Image.LANCZOS)
        card = Image.new("RGB", (t1.width, t1.height * 2 + 46), (245, 246, 248))
        from PIL import ImageDraw
        d = ImageDraw.Draw(card)
        d.text((4, 4), "原始帧", fill=(30, 34, 40))
        card.paste(t1, (0, 20))
        d.text((4, t1.height + 26), "叠加真实 Qt 高亮层", fill=(30, 34, 40))
        card.paste(t2, (0, t1.height + 42))
        zp = os.path.join(OUT, fname.replace("_frame.png", "_zoom.png"))
        card.save(zp)
        print("   → %s  （3 倍放大：原图 / 叠加后）" % os.path.relpath(zp, ROOT))
        measure(arr, items, "文字可读性（基于原始帧采样）")
        summary.append((tag, len(items), "暗底" if it0.get("dark") else "亮底",
                        os.path.relpath(zp, ROOT)))

    print("\n" + "=" * 78)
    print("%-14s %-6s %-6s %s" % ("帧", "命中", "主题", "放大对照图"))
    print("-" * 78)
    for s in summary:
        print("%-14s %-6s %-6s %s" % s)


if __name__ == "__main__":
    main()
