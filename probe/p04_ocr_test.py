# -*- coding: utf-8 -*-
"""P0-4 OCR 通路验证

拿 P0-3 已经验证可用的 PrintWindow(PW_RENDERFULLCONTENT) 截图，
跑 RapidOCR，确认能真正吐出中文文本 + 坐标框。

同时测两件事：
  1. 识别质量（文本是否可读、坐标框是否合理）
  2. 单帧耗时（决定轮询频率和 CPU 占用）
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP = os.path.join(ROOT, "out", "captures")

from rapidocr_onnxruntime import RapidOCR

engine = RapidOCR()


def run_one(path, label, max_pixels=None):
    img = Image.open(path).convert("RGB")
    if max_pixels:
        w, h = img.size
        scale = (max_pixels / (w * h)) ** 0.5
        if scale < 1:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    arr = np.asarray(img)

    t0 = time.perf_counter()
    out = engine(arr)
    dt = (time.perf_counter() - t0) * 1000

    # rapidocr 不同版本返回 (result, elapse) 或直接 result
    res = out[0] if isinstance(out, tuple) else out
    elapse = out[1] if isinstance(out, tuple) and len(out) > 1 else None

    print("\n" + "=" * 100)
    print("[%s]  %s" % (label, os.path.basename(path)))
    print("  图像尺寸 %dx%d   像素 %s   识别耗时 %.0f ms" %
          (img.size[0], img.size[1], "%.2f M" % (img.size[0] * img.size[1] / 1e6), dt))
    if elapse:
        print("  引擎内部分段: %s" % elapse)
    if not res:
        print("  !! 未识别到任何文本")
        return 0, dt, []

    print("  识别到 %d 个文本块，前 25 条：" % len(res))
    lines = []
    for item in res[:25]:
        box, text, score = item[0], item[1], item[2]
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        conf = float(score)
        print("    (%5d,%5d)-(%5d,%5d)  w=%-4d conf=%.2f  %s"
              % (x0, y0, x1, y1, int(x1 - x0), conf, text))
        lines.append((text, (int(x0), int(y0), int(x1), int(y1)), conf))
    return len(res), dt, lines


def main():
    targets = [
        ("Feishu_C.png", "飞书 — Electron，聊天类界面"),
        ("实时消息中心_C.png", "实时消息中心 — Electron，列表类界面"),
        ("QQ_C.png", "QQ — Electron"),
        ("msedge_C.png", "Edge — Chromium，网页"),
        ("TdxW_C.png", "通达信 — Win32 自绘，行情界面"),
    ]
    total = 0
    times = []
    for fn, label in targets:
        p = os.path.join(CAP, fn)
        if not os.path.exists(p):
            print("跳过（缺文件）: %s" % fn)
            continue
        n, dt, _ = run_one(p, label)
        total += n
        if n > 0:
            times.append((label, dt))

    print("\n" + "=" * 100)
    print("汇总")
    print("=" * 100)
    print("  %-34s %-12s" % ("窗口", "OCR 耗时"))
    for label, dt in times:
        print("  %-34s %.0f ms" % (label, dt))
    print("\n  全部图片合计识别出 %d 个文本块" % total)

    if times:
        avg = sum(t for _, t in times) / len(times)
        print("  平均单帧 %.0f ms  →  若按 2 帧/秒轮询，OCR 占单核约 %.0f%%"
              % (avg, avg / 1000 * 2 * 100))


if __name__ == "__main__":
    main()
