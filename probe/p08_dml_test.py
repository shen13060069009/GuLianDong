# -*- coding: utf-8 -*-
"""P0-8 DirectML GPU 加速验证

在独立 venv（stocklens-dml）里运行，通过 monkey patch 把 rapidocr 的
OrtInferSession 换成 DmlExecutionProvider，对比 RTX 3070 与 CPU 的 OCR 耗时。

注意：OCR 的 det 阶段每次输入尺寸都不同，DirectML 对动态 shape 可能需要
重新编译算子图，这恰恰可能是它翻车的地方——所以必须实测。
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image

ROOT = r"C:\Users\Administrator\WorkBuddy\2026-10-06-13-12-12\stocklens"
SRC = os.path.join(ROOT, "out", "captures", "Feishu_C.png")

import onnxruntime as ort
print("onnxruntime:", ort.__version__)
print("可用 EP:", ort.get_available_providers())

import rapidocr_onnxruntime.utils as ru
from onnxruntime import GraphOptimizationLevel, InferenceSession, SessionOptions

_ORIG = ru.OrtInferSession.__init__


def patched_factory(eps):
    def __init__(self, config):
        opt = SessionOptions()
        opt.log_severity_level = 4
        opt.enable_cpu_mem_arena = False
        opt.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
        self._verify_model(config["model_path"])
        self.session = InferenceSession(config["model_path"], sess_options=opt, providers=eps)
    return __init__


def run_case(label, eps, arr, reps=3, **kw):
    ru.OrtInferSession.__init__ = patched_factory(eps) if eps else _ORIG
    from rapidocr_onnxruntime import RapidOCR
    if any(k.startswith("det_") for k in kw) and "det_model_path" not in kw:
        kw["det_model_path"] = None
    try:
        eng = RapidOCR(**kw)
    except Exception as e:
        print("  %-28s 构建失败: %s" % (label, e))
        return None
    try:
        eng(arr)  # warmup
        ts = []
        res = []
        for _ in range(reps):
            t0 = time.perf_counter()
            out = eng(arr)
            ts.append((time.perf_counter() - t0) * 1000)
            res = out[0] if isinstance(out, tuple) else out
        used = eng.text_det.session.get_providers() if hasattr(eng, "text_det") else []
    except Exception as e:
        print("  %-28s 推理失败: %s: %s" % (label, type(e).__name__, str(e)[:120]))
        return None
    n = len(res or [])
    chars = sum(len(r[1]) for r in (res or []))
    print("  %-28s 热态 %6.0f ms   文本块 %3d   字符 %4d   EP=%s"
          % (label, min(ts), n, chars, used[:1]))
    return min(ts), n, chars


def main():
    if not os.path.exists(SRC):
        print("缺样本:", SRC)
        return
    full = Image.open(SRC).convert("RGB")
    W, H = full.size
    region = full.crop((230, 80, min(W, 1000), H))
    arr_reg = np.asarray(region)
    arr_full = np.asarray(full)
    print("裁剪区 %dx%d / 全窗 %dx%d\n" % (region.size[0], region.size[1], W, H))

    print("=" * 96)
    print("对比结果")
    print("=" * 96)
    print("  %-28s %-14s %-8s %s" % ("配置", "耗时", "文本块", "EP"))

    cpu_reg = run_case("CPU / 裁剪区", None, arr_reg)
    dml_reg = run_case("DML / 裁剪区", [("DmlExecutionProvider", {"device_id": 0})], arr_reg)
    cpu_full = run_case("CPU / 全窗", None, arr_full)
    dml_full = run_case("DML / 全窗", [("DmlExecutionProvider", {"device_id": 0})], arr_full)
    dml_fast = run_case("DML / 裁剪区 det640", [("DmlExecutionProvider", {"device_id": 0})],
                        arr_reg, det_limit_side_len=640, det_limit_type="max")

    print()
    if cpu_reg and dml_reg:
        print("  裁剪区加速比: %.2fx  (%.0f ms → %.0f ms)"
              % (cpu_reg[0] / dml_reg[0], cpu_reg[0], dml_reg[0]))
        print("  文本块一致性: CPU=%d  DML=%d  差 %+d"
              % (cpu_reg[1], dml_reg[1], dml_reg[1] - cpu_reg[1]))
    if cpu_full and dml_full:
        print("  全窗加速比:   %.2fx  (%.0f ms → %.0f ms)"
              % (cpu_full[0] / dml_full[0], cpu_full[0], dml_full[0]))


if __name__ == "__main__":
    main()
