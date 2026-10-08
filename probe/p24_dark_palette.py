# -*- coding: utf-8 -*-
"""P24 微信暗色主题：正向控制实验 + 配色对比度量化

两个问题要回答：

1. 微信 0 命中，是「OCR 没读到」还是「读到但被弱词策略挡掉」？
   → 拿同一帧，分别用 keep / context / drop 三档跑，看命中数变化。
     若 keep 能命中而 context 为 0，说明 OCR 是好的，是策略在生效。

2. 暗色主题下高亮到底可不可见？
   → 在同一处算「高亮像素 - 背景像素」的亮度差 ΔL 与对比度比值。
     浅底配色在微信深底上 ΔL 只有个位数等于白画，必须量化出来。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image

import main as app

FRAME = os.path.join(ROOT, "out", "cross", "微信4.x_1086x924_frame.png")


def luma(px):
    """相对亮度（WCAG 简化式）"""
    r, g, b = [float(c) / 255 for c in px[:3]]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(c1, c2):
    a, b = sorted([luma(c1), luma(c2)], reverse=True)
    return (a + 0.05) / (b + 0.05)


def blend(bg, tint, alpha):
    return tuple(int(bg[i] * (1 - alpha) + tint[i] * alpha) for i in range(3))


# 与 src/overlay.py 的 PALETTE / PALETTE_DARK 保持一致
LIGHT_FILL = ((255, 214, 64), 0.38)     # 浅底档：暖黄 38%
DARK_FILL = ((86, 132, 255), 0.55)      # 暗底档：亮蓝 55%


def main():
    if not os.path.exists(FRAME):
        print("缺帧图，先跑 probe/p22_cross_app.py")
        return
    arr = np.asarray(Image.open(FRAME).convert("RGB"))
    cfg = app.load_config()
    ocr = app.make_ocr(cfg, verbose=False)
    ocr.reconfigure(det_limit=max(640, min(2400, int(max(arr.shape[:2]) * 0.85))))
    boxes = ocr.recognize(arr)
    print("帧 %dx%d  OCR 文本框 %d" % (arr.shape[1], arr.shape[0], len(boxes)))

    # ---------------- 1 正向控制：三档对比 ----------------
    # 注意：不能在 make_matcher 之后改 weak_mode —— drop 档是「建词典时就不收弱词」，
    # 建完再改属性只会让策略空转（第一版就踩了这个坑，drop 档误报 1 命中）。
    print("\n[1] 弱词策略三档对比（同一帧、同一份 OCR 结果）")
    print("    %-9s %-7s %s" % ("档位", "命中", "内容"))
    print("    " + "-" * 72)
    detail = {}
    for mode in ("keep", "context", "drop"):
        cfg2 = app.load_config()
        cfg2.setdefault("match", {})["weak_mode"] = mode
        m = app.make_matcher(cfg2, verbose=False)
        assert m.weak_mode == mode, "档位没生效：%s" % m.weak_mode
        items = app.match_boxes(boxes, m, arr)
        detail[mode] = items
        txt = "、".join("%s(%s)%s" % (it["label"], it["code"],
                                     "[弱]" if it.get("weak") else "")
                       for it in items) or "（无）"
        print("    %-9s %-7d %s" % (mode, len(items), txt))
    if detail["keep"] and not detail["context"]:
        print("    → keep 有命中、context 归零：OCR 读到了，是弱词策略在拦 ✓")
    elif not detail["keep"]:
        print("    → 三档都为 0：这一帧确实没有股票词（不是漏读）")

    # ---------------- 2 配色对比度 ----------------
    print("\n[2] 高亮配色在真实背景上的对比度")
    if detail["keep"]:
        it = detail["keep"][0]
    else:
        # 没有命中就取聊天列表里那块深色区域做样本
        it = {"rect": (196, 776, 44, 34)}
    x, y, w, h = it["rect"]
    sub = arr[max(y, 0):y + h, max(x, 0):x + w]
    bg = tuple(int(v) for v in sub.reshape(-1, 3).mean(axis=0))
    print("    采样区 rect=%s  背景均值 RGB%s  亮度 L=%.3f" % (it["rect"], bg, luma(bg)))

    print("\n    %-10s %-22s %-10s %-10s %s"
          % ("档位", "高亮色", "相对亮度", "对比度", "可见性"))
    print("    " + "-" * 74)
    for name, (tint, alpha) in (("浅底配色", LIGHT_FILL), ("暗底配色", DARK_FILL)):
        hl = blend(bg, tint, alpha)
        cr = contrast(hl, bg)
        dl = abs(luma(hl) - luma(bg))
        verdict = "清晰" if cr >= 1.5 else ("勉强" if cr >= 1.15 else "几乎不可见")
        print("    %-10s RGB%-19s %-10.3f %-10.2f %s"
              % (name, str(hl), luma(hl), cr, verdict))
    print("\n    （对比度 = 高亮后亮度 / 背景亮度，1.00 表示完全看不出）")

    # ---------------- 3 出图对比 ----------------
    out_dir = os.path.join(ROOT, "out", "cross")
    for mode in ("keep", "context"):
        items = detail[mode]
        if not items:
            continue
        out = arr.astype(np.float32).copy()
        for it2 in items:
            x, y, w, h = it2["rect"]
            x2, y2 = min(x + w, out.shape[1] - 1), min(y + h, out.shape[0] - 1)
            tint, alpha = DARK_FILL if it2.get("dark") else LIGHT_FILL
            line = (150, 190, 255) if it2.get("dark") else (37, 99, 235)
            out[y:y2, x:x2] = out[y:y2, x:x2] * (1 - alpha) + np.array(tint) * alpha
            out[max(y2 - 3, y):y2, x:x2] = line
        p = os.path.join(out_dir, "微信4.x_%s_adaptive.png" % mode)
        Image.fromarray(out.astype(np.uint8)).save(p)
        print("    → %s" % os.path.relpath(p, ROOT))

    # ---------------- 4 视觉对照（同一块区域 × 三种配色）----------------
    x, y, w, h = it["rect"]
    x0, y0 = max(x - 90, 0), max(y - 22, 0)
    x1, y1 = min(x + w + 130, arr.shape[1]), min(y + h + 22, arr.shape[0])
    tiles = []
    rect_local = (x - x0, y - y0, w, h)
    for label, spec in (("原图（微信暗色）", None),
                        ("浅底配色", LIGHT_FILL),
                        ("暗底配色", DARK_FILL)):
        t = arr[y0:y1, x0:x1].astype(np.float32).copy()
        if spec is not None:
            tint, alpha = spec
            line = (37, 99, 235) if label == "浅底配色" else (150, 190, 255)
            rx, ry, rw, rh = rect_local
            t[ry:ry + rh, rx:rx + rw] = t[ry:ry + rh, rx:rx + rw] * (1 - alpha) + np.array(tint) * alpha
            t[max(ry + rh - 3, ry):ry + rh, rx:rx + rw] = line
        t = t.astype(np.uint8)
        tiles.append((label, t))

    scale = 3
    gap = 14
    tw = (x1 - x0) * scale
    th = (y1 - y0) * scale
    card = Image.new("RGB", (tw + gap * 2, (th + 34) * len(tiles) + gap), (245, 246, 248))
    from PIL import ImageDraw
    d = ImageDraw.Draw(card)
    for i, (label, t) in enumerate(tiles):
        yy = gap + i * (th + 34)
        im = Image.fromarray(t).resize((tw, th), Image.LANCZOS)
        card.paste(im, (gap, yy + 26))
        d.text((gap, yy + 6), label, fill=(30, 34, 40))
    p = os.path.join(out_dir, "微信4.x_配色对照.png")
    card.save(p)
    print("    → %s  （三档对照，3 倍放大）" % os.path.relpath(p, ROOT))


if __name__ == "__main__":
    main()
