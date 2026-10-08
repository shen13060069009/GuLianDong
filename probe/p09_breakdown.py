# -*- coding: utf-8 -*-
"""P0-9 OCR 耗时成分拆解

P0-8 显示 DirectML 只带来 1.70x 加速，远低于纯推理应有的倍数，
怀疑瓶颈是 DB 后处理（阈值化 + 连通域 + pyclipper 外扩，全是 CPU 活）。

本探针把 det / cls / rec 三段耗时分开测量，并测试 det 后处理参数的影响，
用来判断优化该投向哪里。
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "out", "captures", "Feishu_C.png")


def build(**kw):
    from rapidocr_onnxruntime import RapidOCR
    if any(k.startswith("det_") for k in kw) and "det_model_path" not in kw:
        kw["det_model_path"] = None
    return RapidOCR(**kw)


def hot_run(eng, arr, reps=3):
    eng(arr)
    best = None
    for _ in range(reps):
        t0 = time.perf_counter()
        out = eng(arr)
        dt = (time.perf_counter() - t0) * 1000
        elapse = out[1] if isinstance(out, tuple) and len(out) > 1 else None
        res = out[0] if isinstance(out, tuple) else out
        if best is None or dt < best[0]:
            best = (dt, elapse, res)
    return best


def fmt_elapse(e):
    if not e:
        return "n/a"
    try:
        vals = [float(x) * 1000 for x in e]
    except Exception:
        return str(e)
    names = ["det", "cls", "rec"]
    return "  ".join("%s=%.0fms" % (names[i] if i < 3 else "x%d" % i, v)
                     for i, v in enumerate(vals))


def main():
    full = Image.open(SRC).convert("RGB")
    W, H = full.size
    region = full.crop((230, 80, min(W, 1000), H))
    arr = np.asarray(region)
    arr_full = np.asarray(full)
    print("=" * 100)
    print("P0-9 OCR 耗时成分拆解")
    print("=" * 100)
    print("  裁剪区 %dx%d / 全窗 %dx%d\n" % (region.size[0], region.size[1], W, H))

    cases = [
        ("基线 裁剪区", arr, {}),
        ("全窗", arr_full, {}),
        ("det长边=640", arr, {"det_limit_side_len": 640, "det_limit_type": "max"}),
        ("关cls", arr, {"use_angle_cls": False}),
        ("只det（不识别）", arr, {"use_text_det": True, "use_angle_cls": False}),
        ("max_cand=100", arr, {"det_max_candidates": 100}),
        ("unclip=1.2", arr, {"det_unclip_ratio": 1.2}),
        ("no_dilation", arr, {"det_use_dilation": False}),
        ("box_thresh=0.6", arr, {"det_box_thresh": 0.6}),
        ("组合优化", arr, {"det_limit_side_len": 640, "det_limit_type": "max",
                           "det_max_candidates": 100, "det_unclip_ratio": 1.2,
                           "det_use_dilation": False, "use_angle_cls": False,
                           "det_model_path": None}),
    ]

    print("  %-16s %-10s %-9s %s" % ("配置", "总耗时", "文本块", "分段拆解"))
    print("  " + "-" * 94)
    for label, img, kw in cases:
        try:
            eng = build(**kw)
        except Exception as e:
            print("  %-16s 构建失败: %s" % (label, str(e)[:60]))
            continue
        try:
            dt, elapse, res = hot_run(eng, img)
        except Exception as e:
            print("  %-16s 失败: %s: %s" % (label, type(e).__name__, str(e)[:60]))
            continue
        n = len(res or [])
        print("  %-16s %-10s %-9d %s" % (label, "%.0f ms" % dt, n, fmt_elapse(elapse)))


if __name__ == "__main__":
    main()
