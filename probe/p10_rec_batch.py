# -*- coding: utf-8 -*-
"""P0-10 rec 批处理与 GPU 联合优化

P0-9 定位出真正瓶颈：rec 段占 76%（69 个文本块 × 9.3ms 串行）。
rapidocr 默认 rec_batch_num=6，且按宽度分桶后每批独立推理。

本探针测试：
  1. rec_batch_num 放大到 12/24/48 的效果（CPU）
  2. CPU vs DirectML 在最优 batch 下的对比
  3. 只识别「聊天消息区」能省多少（排除工具栏/会话列表）

在 stocklens-dml 环境运行（同时具备 CPU 与 DML 两种 EP）。
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
import rapidocr_onnxruntime.utils as ru
from onnxruntime import GraphOptimizationLevel, InferenceSession, SessionOptions

_ORIG = ru.OrtInferSession.__init__
HAS_DML = "DmlExecutionProvider" in ort.get_available_providers()


def make_patch(use_dml):
    def __init__(self, config):
        opt = SessionOptions()
        opt.log_severity_level = 4
        opt.enable_cpu_mem_arena = False
        opt.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
        eps = []
        if use_dml:
            eps.append(("DmlExecutionProvider", {"device_id": 0}))
        eps.append(("CPUExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"}))
        self._verify_model(config["model_path"])
        self.session = InferenceSession(config["model_path"], sess_options=opt, providers=eps)
    return __init__


def build(use_dml, **kw):
    ru.OrtInferSession.__init__ = make_patch(use_dml) if use_dml else _ORIG
    from rapidocr_onnxruntime import RapidOCR
    # rapidocr 1.2.3 的 bug：传 det_*/rec_* 参数时，update_*_params 里会直接读
    # ['model_path']，不一起给 model_path 就 KeyError
    if any(k.startswith("det_") for k in kw) and "det_model_path" not in kw:
        kw["det_model_path"] = None
    if any(k.startswith("rec_") for k in kw) and "rec_model_path" not in kw:
        kw["rec_model_path"] = None
    return RapidOCR(**kw)


def hot(eng, arr, reps=3):
    eng(arr)
    best = None
    for _ in range(reps):
        t0 = time.perf_counter()
        out = eng(arr)
        dt = (time.perf_counter() - t0) * 1000
        el = out[1] if isinstance(out, tuple) and len(out) > 1 else None
        res = out[0] if isinstance(out, tuple) else out
        if best is None or dt < best[0]:
            best = (dt, el, res)
    return best


def show(label, dt, el, res):
    n = len(res or [])
    seg = ""
    if el:
        try:
            v = [float(x) * 1000 for x in el]
            seg = "det=%.0f cls=%.0f rec=%.0f" % (v[0], v[1] if len(v) > 1 else 0,
                                                  v[2] if len(v) > 2 else 0)
        except Exception:
            seg = str(el)
    print("  %-30s %-10s %-8d %s" % (label, "%.0f ms" % dt, n, seg))


def main():
    full = Image.open(SRC).convert("RGB")
    W, H = full.size
    # 全窗 / 聊天区（去掉顶部工具栏）/ 更窄的消息区
    chat = full.crop((230, 80, min(W, 1000), H))
    narrow = full.crop((230, 80, min(W, 700), H))
    print("=" * 104)
    print("P0-10 rec 批处理与 GPU 联合优化   DML可用=%s" % HAS_DML)
    print("=" * 104)
    print("  全窗 %dx%d | 聊天区 %dx%d | 窄区 %dx%d\n"
          % (W, H, chat.size[0], chat.size[1], narrow.size[0], narrow.size[1]))

    arr_full = np.asarray(full)
    arr_chat = np.asarray(chat)
    arr_narrow = np.asarray(narrow)

    print("  %-30s %-10s %-8s %s" % ("配置", "总耗时", "文本块", "分段"))
    print("  " + "-" * 98)

    # --- 1. rec_batch_num 扫描（CPU）---
    print("\n[1] rec_batch_num 扫描（CPU，聊天区）")
    for bn in (6, 12, 24, 48):
        try:
            eng = build(False, rec_batch_num=bn)
            dt, el, res = hot(eng, arr_chat)
            show("CPU rec_batch=%d" % bn, dt, el, res)
        except Exception as e:
            print("  rec_batch=%-3d 失败: %s" % (bn, str(e)[:70]))

    # --- 2. CPU vs DML 在最优 batch 下 ---
    print("\n[2] CPU vs DirectML（聊天区，rec_batch=24）")
    for use_dml in (False, True):
        if use_dml and not HAS_DML:
            continue
        try:
            eng = build(use_dml, rec_batch_num=24)
            dt, el, res = hot(eng, arr_chat)
            show("%s rec_batch=24" % ("DML" if use_dml else "CPU"), dt, el, res)
        except Exception as e:
            print("  %s 失败: %s" % ("DML" if use_dml else "CPU", str(e)[:70]))

    # --- 3. 区域收窄的影响 ---
    print("\n[3] 区域范围的影响（rec_batch=24）")
    for label, arr in (("全窗", arr_full), ("聊天区", arr_chat), ("窄区", arr_narrow)):
        try:
            eng = build(False, rec_batch_num=24)
            dt, el, res = hot(eng, arr)
            show("CPU %s" % label, dt, el, res)
        except Exception as e:
            print("  %s 失败: %s" % (label, str(e)[:70]))

    # --- 4. 组合最优 ---
    print("\n[4] 组合最优（DML + rec_batch=24 + det640）")
    if HAS_DML:
        try:
            eng = build(True, rec_batch_num=24,
                        det_limit_side_len=640, det_limit_type="max")
            dt, el, res = hot(eng, arr_chat)
            show("DML+rec24+det640", dt, el, res)
        except Exception as e:
            print("  失败: %s" % str(e)[:70])
    ru.OrtInferSession.__init__ = _ORIG


if __name__ == "__main__":
    main()
