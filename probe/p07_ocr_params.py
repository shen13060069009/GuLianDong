# -*- coding: utf-8 -*-
"""P0-7 OCR 深度调参（用正确的参数名）

P0-5 用错了参数名（max_side_len 在 rapidocr 里不存在），结论作废。
翻源码后确认真实参数：
    Global.min_height / width_height_ratio
    Det.limit_side_len（默认 736）
    Det.limit_type（默认 'min'，即限制短边）
    Det.score_mode（'fast' / 'slow'，DB 后处理的近似模式）
    Det.use_dilation
    Det.unclip_ratio

另外从 utils.py 发现 OrtInferSession 里写死了
    sess_opt.enable_cpu_mem_arena = False
关掉 ORT 内存池会让每次推理重新申请内存，这很可能是慢的真凶，
本探针通过 monkey patch 把它打开对比。

同时测 DB 后处理里最贵的一项：unclip（多边形外扩）。
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "out", "captures", "Feishu_C.png")

import rapidocr_onnxruntime.utils as ru
from onnxruntime import (GraphOptimizationLevel, InferenceSession,
                         SessionOptions, get_available_providers)

_ORIG_INIT = ru.OrtInferSession.__init__


def patch_session(mem_arena=None, threads=None):
    """monkey patch OrtInferSession.__init__，用于验证内存池 / 线程数的影响"""
    def __init__(self, config):
        if mem_arena is None and threads is None:
            return _ORIG_INIT(self, config)
        opt = SessionOptions()
        opt.log_severity_level = 4
        opt.enable_cpu_mem_arena = True if mem_arena is None else mem_arena
        opt.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
        if threads:
            opt.intra_op_num_threads = threads
        eps = [("CPUExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"})]
        self._verify_model(config["model_path"])
        self.session = InferenceSession(config["model_path"], sess_options=opt, providers=eps)
    ru.OrtInferSession.__init__ = __init__


def build(**kw):
    from rapidocr_onnxruntime import RapidOCR
    # rapidocr 1.2.3 的 bug：只传 det_* 参数时 update_det_params 会 KeyError，
    # 必须同时给出 det_model_path（None 表示沿用内置模型）
    if any(k.startswith("det_") for k in kw) and "det_model_path" not in kw:
        kw["det_model_path"] = None
    try:
        return RapidOCR(**kw), None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def timed(engine, arr, reps=2):
    engine(arr)
    ts = []
    res = []
    for _ in range(reps):
        t0 = time.perf_counter()
        out = engine(arr)
        ts.append((time.perf_counter() - t0) * 1000)
        res = out[0] if isinstance(out, tuple) else out
    return (res or []), min(ts), sum(ts) / len(ts)


def stats(res):
    if not res:
        return 0, 0, 0.0
    return len(res), sum(len(r[1]) for r in res), float(np.mean([float(r[2]) for r in res]))


def main():
    print("=" * 104)
    print("P0-7 OCR 深度调参")
    print("=" * 104)
    print("  可用 EP: %s" % get_available_providers())

    if not os.path.exists(SRC):
        print("缺样本 %s" % SRC)
        return

    full = Image.open(SRC).convert("RGB")
    W, H = full.size
    region = full.crop((230, 80, min(W, 1000), H))
    arr_full = np.asarray(full)
    arr_reg = np.asarray(region)
    print("  全窗 %dx%d (%.2f M px)   裁剪区 %dx%d (%.2f M px)"
          % (W, H, W * H / 1e6, region.size[0], region.size[1],
             region.size[0] * region.size[1] / 1e6))

    cases = [
        ("基线（原样）", arr_reg, dict(patch=None), {}),
        ("mem_arena=ON", arr_reg, dict(patch=dict(mem_arena=True)), {}),
        ("mem_arena=ON +4线程", arr_reg, dict(patch=dict(mem_arena=True, threads=4)), {}),
        ("mem_arena=ON +8线程", arr_reg, dict(patch=dict(mem_arena=True, threads=8)), {}),
        ("det 长边=960", arr_reg, dict(patch=dict(mem_arena=True)),
         {"det_limit_side_len": 960, "det_limit_type": "max"}),
        ("det 长边=640", arr_reg, dict(patch=dict(mem_arena=True)),
         {"det_limit_side_len": 640, "det_limit_type": "max"}),
        ("det 长边=480", arr_reg, dict(patch=dict(mem_arena=True)),
         {"det_limit_side_len": 480, "det_limit_type": "max"}),
        ("det640 + slow后处理", arr_reg, dict(patch=dict(mem_arena=True)),
         {"det_limit_side_len": 640, "det_limit_type": "max",
          "det_score_mode": "slow"}),
        ("det640 + 关cls + 8线程", arr_reg, dict(patch=dict(mem_arena=True, threads=8)),
         {"det_limit_side_len": 640, "det_limit_type": "max", "use_angle_cls": False}),
        ("全窗 + mem_arena=ON", arr_full, dict(patch=dict(mem_arena=True)), {}),
    ]

    print()
    print("  %-24s %-8s %-8s %-9s %-11s %s" % ("配置", "文本块", "字符数", "平均置信", "热态耗时", "备注"))
    print("  " + "-" * 98)

    results = []
    for label, arr, popt, kw in cases:
        if popt.get("patch") is not None:
            patch_session(**popt["patch"])
        else:
            ru.OrtInferSession.__init__ = _ORIG_INIT

        eng, err = build(**kw)
        if eng is None:
            print("  %-24s 不支持: %s" % (label, err))
            continue
        try:
            res, best, avg = timed(eng, arr)
        except Exception as e:
            print("  %-24s 失败: %s" % (label, type(e).__name__))
            continue
        n, chars, conf = stats(res)
        note = ""
        results.append((label, n, best, avg, conf))
        print("  %-24s %-8d %-8d %-9.3f %-11s %s"
              % (label, n, chars, conf, "%.0f ms" % best, note))

    ru.OrtInferSession.__init__ = _ORIG_INIT

    if results:
        base = results[0]
        print()
        print("  %-24s %-12s %-12s %s" % ("对比", "耗时", "相对基线", "文本块变化"))
        print("  " + "-" * 98)
        for label, n, best, avg, conf in results:
            print("  %-24s %-12s %-12s %+d"
                  % (label, "%.0f ms" % best, "%.2fx" % (best / base[2]), n - base[1]))


if __name__ == "__main__":
    main()
