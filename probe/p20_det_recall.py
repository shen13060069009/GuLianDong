# -*- coding: utf-8 -*-
"""P0-20 det_limit ↔ 召回 权衡（最终定档）

p19 结论：GPU 上 det_limit 直接决定 rec 工作量（文本块越多越慢）——
    飞书 1325x993：det640→517ms/81块  det900→606ms/88块
                   det1126→951ms/90块  det2000→1176ms/93块

但「块少」不等于「股票名没丢」。本探针把 OCR + 匹配串起来，
直接看每档的【命中数 / 命中内容 / 耗时】，用数据定最终系数。

用法：
    python probe/p20_det_recall.py [窗口索引] [重复轮数]
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main as app  # noqa: E402
from src.capture import find_windows, grab_window  # noqa: E402

# 飞书窗口里已知的真实命中（作为召回金标准）
TRUTH = {"国泰海通", "宋城演艺"}


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    reps = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    cfg = app.load_config()
    wins = find_windows(cfg["sources"])
    if not wins:
        print("没发现聊天窗口")
        return 1
    info = wins[min(idx, len(wins) - 1)]
    arr = grab_window(info)
    long_side = max(info.width, info.height)
    print("=" * 104)
    print("P0-20 det_limit ↔ 召回 权衡   %s %dx%d  长边 %d"
          % (info.app, arr.shape[1], arr.shape[0], long_side))
    print("=" * 104)

    ocr = app.make_ocr(cfg, verbose=False)
    matcher = app.make_matcher(cfg, verbose=False)

    print("\n  %-8s %-7s %-9s %-6s %-6s %s"
          % ("coef", "det", "稳态ms", "命中", "真实", "命中内容"))
    print("  " + "-" * 96)
    rows = []
    for coef in (0.45, 0.55, 0.65, 0.75, 0.85, 1.00, 1.20):
        det = max(320, min(2400, int(long_side * coef)))
        # 关键：analyze_window 内部会按 det_coef 自适应重算 det_limit，
        # 会把这里的档位覆盖回 0.85×长边。用 det_range 钉死成单点，让试验真正生效。
        ocr.det_range = (det, det)
        ocr.reconfigure(det_limit=det)
        items = []
        ts = []
        for i in range(reps + 1):
            t0 = time.perf_counter()
            items = app.analyze_window(info, ocr, matcher, arr)
            dt = (time.perf_counter() - t0) * 1000
            if i > 0:                      # 第 1 轮丢弃（含首次调用开销）
                ts.append(dt)
        labels = [it["label"] for it in items]
        truth = TRUTH & set(labels)
        rows.append((coef, det, min(ts), len(items), len(truth),
                     len(TRUTH), labels))
        print("  %-8.2f %-7d %-9.0f %-6d %-6s %s"
              % (coef, det, min(ts), len(items),
                 "%d/%d" % (len(truth), len(TRUTH)),
                 "、".join(labels) if labels else "—"))

    print("\n  真实命中（金标准）: %s" % "、".join(sorted(TRUTH)))
    good = [r for r in rows if r[4] == r[5]]
    if good:
        best = min(good, key=lambda r: r[2])
        print("  → 召回满分里最快的一档：coef=%.2f (det=%d) %.0fms"
              % (best[0], best[1], best[2]))
    else:
        print("  → 没有任何一档拿到满分召回，需要更大 det_limit 或换取屏区域")
    return 0


if __name__ == "__main__":
    sys.exit(main())
