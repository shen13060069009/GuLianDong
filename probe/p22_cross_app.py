# -*- coding: utf-8 -*-
"""P22 跨端真机验证：微信 4.x / 钉钉 / 飞书

对每个识别源窗口：
  1. PrintWindow(PW_RENDERFULLCONTENT) 抓帧，检查是否黑屏
  2. 全窗 OCR，统计文本框数
  3. 匹配，列出全部命中（坐标 + 层 + 候选 + 所在原文）
  4. 存帧图 + 高亮合成预览图，供人工核对

用法: python probe/p22_cross_app.py [app关键字]
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image

from src.capture import find_windows, grab_window
import main as app

OUT = os.path.join(ROOT, "out", "cross")
os.makedirs(OUT, exist_ok=True)


def blackness(arr):
    """暗像素占比 + 均值，判断 PrintWindow 是否返回全黑"""
    g = arr.mean(axis=2)
    return float((g < 12).mean()), float(g.mean())


def compose(arr, items):
    """把高亮框烧到帧上（纯 numpy，不依赖 Qt 合成器）

    配色与 src/overlay.py 的 PALETTE / PALETTE_DARK 保持一致：
    暗色主题（微信 4.x）用高透明度填充 + 亮色描边，否则深底上看不见。
    """
    LIGHT = (255, 214, 64)      # 亮底：暖黄填充
    DARK = (86, 132, 255)       # 暗底：亮蓝填充（对比度更高）
    out = arr.astype(np.float32)
    H, W = out.shape[:2]
    for it in items:
        x, y, w, h = it["rect"]
        x2, y2 = min(x + w, W - 1), min(y + h, H - 1)
        x, y = max(x, 0), max(y, 0)
        if x2 <= x or y2 <= y:
            continue
        dark = bool(it.get("dark"))
        tint = np.array(DARK if dark else LIGHT, dtype=np.float32)
        out[y:y2, x:x2] = out[y:y2, x:x2] * (0.45 if dark else 0.62) + tint * (0.55 if dark else 0.38)
        line = (150, 190, 255) if dark else (37, 99, 235)
        out[max(y2 - 3, y):y2, x:x2] = line
    return out.astype(np.uint8)


def find_box_text(boxes, it):
    """反查命中所在的 OCR 原文"""
    rx, ry, rw, rh = it["rect"]
    cx, cy = rx + rw / 2, ry + rh / 2
    best, bd = "", 1e9
    for b in boxes:
        if b.x0 <= cx <= b.x1 and b.y0 - 8 <= cy <= b.y1 + 8:
            return b.text
        d = abs((b.x0 + b.x1) / 2 - cx) + abs((b.y0 + b.y1) / 2 - cy)
        if d < bd:
            bd, best = d, b.text
    return best


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    cfg = app.load_config()
    matcher = app.make_matcher(cfg, verbose=True)

    wins = find_windows(min_size=(200, 150), visible_only=True)
    if only:
        k = only.lower()
        wins = [w for w in wins
                if k in (w.app or "").lower() or k in w.exe.lower()]
    if not wins:
        print("没有找到识别源窗口")
        return

    print()
    rows = []
    for w in wins:
        tag = "%s_%dx%d" % ((w.app or w.exe).replace(" ", ""), w.width, w.height)
        print("=" * 78)
        print("[%s] hwnd=0x%08X  %s  %dx%d @(%d,%d)  title=%r"
              % (w.app, w.hwnd, w.exe, w.width, w.height,
                 w.client[0], w.client[1], w.title))

        t0 = time.perf_counter()
        arr = grab_window(w)
        t_grab = (time.perf_counter() - t0) * 1000
        if arr is None or arr.size == 0:
            print("  !! 抓帧失败")
            rows.append((w.app, t_grab, 0, 0, 0, "抓帧失败"))
            continue
        dark, mean = blackness(arr)
        print("  抓帧 %.0fms  %dx%d  暗像素 %.1f%%  灰度均值 %.1f"
              % (t_grab, arr.shape[1], arr.shape[0], dark * 100, mean))
        Image.fromarray(arr).save(os.path.join(OUT, "%s_frame.png" % tag))

        if dark > 0.92:
            print("  !! 近乎全黑 —— PrintWindow 对该窗口无效")
            rows.append((w.app, t_grab, 0, 0, 0, "全黑"))
            continue

        ocr = app.make_ocr(cfg, verbose=False)
        coef = getattr(ocr, "det_coef", 0.85)
        lo, hi = getattr(ocr, "det_range", (640, 2400))
        ls = max(arr.shape[1], arr.shape[0])
        want = max(lo, min(hi, int(ls * coef)))
        ocr.reconfigure(det_limit=want)
        print("  det_limit=%d（长边 %d × %.2f，夹在 [%d,%d]）"
              % (want, ls, coef, lo, hi))

        t1 = time.perf_counter()
        boxes = ocr.recognize(arr)
        t_ocr = (time.perf_counter() - t1) * 1000
        t2 = time.perf_counter()
        items = app.match_boxes(boxes, matcher, arr)
        t_mat = (time.perf_counter() - t2) * 1000
        print("  OCR %.0fms  %d 个文本框   匹配 %.1fms   命中 %d 处"
              % (t_ocr, len(boxes), t_mat, len(items)))
        for it in items:
            cand = " 候选=%s" % (it["candidates"],) if it["ambiguous"] else ""
            flag = " [弱]" if it.get("weak") else ""
            dark = "暗底" if it.get("dark") else "亮底"
            print("    %-10s %-8s %-3s %-5s %-5s %-4s rect=%-20s 原文=%r%s"
                  % (it["label"], it["code"], it["layer"], it["kind"], dark, flag.strip(),
                     str(it["rect"]), find_box_text(boxes, it)[:36], cand))

        comp = compose(arr, items)
        Image.fromarray(comp).save(os.path.join(OUT, "%s_hl.png" % tag))
        print("  → out/cross/%s_frame.png (%dx%d)" % (tag, arr.shape[1], arr.shape[0]))
        print("  → out/cross/%s_hl.png" % tag)
        rows.append((w.app, t_grab, t_ocr, len(boxes), len(items), "OK"))

    print()
    print("=" * 78)
    print("%-10s %-9s %-9s %-8s %-7s %s"
          % ("端", "抓帧ms", "OCRms", "文本框", "命中", "状态"))
    print("-" * 78)
    for r in rows:
        print("%-10s %-9.0f %-9.0f %-8d %-7d %s"
              % (r[0], r[1], r[2], r[3], r[4], r[5]))


if __name__ == "__main__":
    main()
