# -*- coding: utf-8 -*-
"""P0-40 纯 CPU 下怎么把 OCR 调回可用：参数扫描 + 裁剪收益

P0-39 实测：纯 CPU 全窗默认参数 691 ms/帧、吃 12 个核。
普通办公机带不动。这里逐项找可压缩的空间：

  · rec_batch     P0 老结论说 CPU 上小 batch 更快（crop padding 是白算）
  · det_limit     输入长边，直接决定 det 与 rec 的输入面积
  · 裁剪           只把「消息区」送 OCR，别把侧边栏/工具栏/输入框也算上

用法：
    python probe/p40_cpu_tuning.py
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import main as app  # noqa: E402
from src.capture import find_windows, grab_window  # noqa: E402

NCORE = os.cpu_count() or 1


def pick(cfg):
    wins = find_windows(cfg["sources"])
    if not wins:
        return None
    for w in wins:
        if "微信" in repr(w):
            return w
    return wins[0]


def run(ocr, arr, frames=4):
    for _ in range(1):
        ocr.recognize(arr)
    cp0, w0 = time.process_time(), time.perf_counter()
    n = 0
    for _ in range(frames):
        n += len(ocr.recognize(arr))
    cp1, w1 = time.process_time(), time.perf_counter()
    wall = (w1 - w0) / frames * 1000
    cores = (cp1 - cp0) / (w1 - w0)
    return wall, cores, n / frames


def main():
    cfg = app.load_config()
    info = pick(cfg)
    if info is None:
        print("没有聊天窗口")
        return 2
    full = grab_window(info)
    H, W = full.shape[:2]

    # 消息区裁剪：去掉左侧会话栏（~230px）与底部输入区（~15%高）
    # 微信 4.x 实测布局：左栏会话列表 + 右侧消息流
    CROP = (230, 0, W, int(H * 0.85))
    crop = full[0:int(H * 0.85), 230:W]

    print("=" * 104)
    print("P0-40 纯 CPU 参数扫描（本机 %d 逻辑核，仅用 CPUExecutionProvider）" % NCORE)
    print("=" * 104)
    print("  窗口 %dx%d     全窗像素 %d      消息区裁剪 %s → %dx%d，像素 %d (%.0f%%)"
          % (W, H, W * H, CROP, crop.shape[1], crop.shape[0],
             crop.shape[1] * crop.shape[0],
             100.0 * crop.shape[1] * crop.shape[0] / (W * H)))

    from src.ocr import StockOCR
    rows = []

    # ---------------- 全窗参数扫描 ----------------
    print("\n" + "-" * 104)
    print("A. 全窗（现状）逐项压参")
    print("-" * 104)
    print("  %-30s %10s %10s %12s %10s" % ("配置", "单帧 ms", "占用核", "帧CPU秒", "命中/帧"))
    print("  " + "-" * 78)
    for det, batch, tag in [
        (773, 24, "det773 batch24  ← 默认"),
        (773, 6,  "det773 batch6"),
        (773, 1,  "det773 batch1"),
        (640, 1,  "det640 batch1"),
        (480, 1,  "det480 batch1"),
    ]:
        ocr = StockOCR(gpu=False, det_limit=det, rec_batch=batch,
                       use_cls=False, verbose=False)
        wall, cores, hit = run(ocr, full)
        rows.append((tag, wall, cores, hit, "全窗"))
        print("  %-30s %10.0f %10.2f %12.2f %10.1f"
              % (tag, wall, cores, wall * cores / 1000, hit))
        del ocr

    # ---------------- 裁剪 ----------------
    print("\n" + "-" * 104)
    print("B. 只把「消息区」送 OCR（同样全分辨率，只是剪掉无关区域）")
    print("-" * 104)
    print("  %-30s %10s %10s %12s %10s" % ("配置", "单帧 ms", "占用核", "帧CPU秒", "命中/帧"))
    print("  " + "-" * 78)
    for det, batch, tag in [
        (640, 1,  "裁剪 det640 batch1"),
        (480, 1,  "裁剪 det480 batch1"),
        (420, 1,  "裁剪 det420 batch1"),
    ]:
        ocr = StockOCR(gpu=False, det_limit=det, rec_batch=batch,
                       use_cls=False, verbose=False)
        wall, cores, hit = run(ocr, crop)
        rows.append((tag, wall, cores, hit, "裁剪"))
        print("  %-30s %10.0f %10.2f %12.2f %10.1f"
              % (tag, wall, cores, wall * cores / 1000, hit))
        del ocr

    # ---------------- 汇总 ----------------
    print("\n" + "=" * 104)
    print("汇总：按「1 秒 1 条消息」折算的 CPU 占用")
    print("=" * 104)
    base = rows[0]
    print("  %-30s %10s %12s %14s %14s" % ("配置", "单帧 ms", "帧CPU秒", "20核机上", "4核机上"))
    print("  " + "-" * 86)
    for tag, wall, cores, hit, kind in rows:
        cps = wall * cores / 1000
        print("  %-30s %10.0f %12.2f %11.1f%% %13s"
              % (tag, wall, cps, 100.0 * cps / NCORE,
                 ("%.0f%%" % min(100, 100.0 * cps / 4))))
    print("  " + "-" * 86)
    print("  基线（默认全窗）单帧成本 %.2f 帧·CPU秒；最优 %.2f；省 %.1fx"
          % (base[3], rows[-1][3], base[3] / max(rows[-1][3], 1e-9)))
    print()
    print("  注：4 核机上墙钟会更慢（并行度只有 4），但 CPU 秒成本同量级。")
    print("      判断「带不带得动」看两个数：单帧 CPU 秒（总量）和占用核（卡不卡）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
