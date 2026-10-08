# -*- coding: utf-8 -*-
"""P0-41 纯 CPU 下限制 onnxruntime 线程数：把「瞬间吃满」变成「温和常驻」

P0-40 发现：纯 CPU 单帧虽然压到了 220ms，但仍占用 14 个核 ——
即那 220ms 里几乎把整机 CPU 占满。用户感知不是「平均占用」而是
「每次识别时机器卡一下」。根因是 onnxruntime CPU EP 默认
intra_op_num_threads = 全部核心，单次推理就把所有核拉满。

解法：显式设 intra_op_num_threads / inter_op_num_threads。
代价是墙钟变长，收益是「核占用」断崖式下降 —— 这才是普通机器能带动的关键。

用法：
    python probe/p41_cpu_threads.py [窗口]
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main as app  # noqa: E402
from src.capture import find_windows, grab_window  # noqa: E402

NCORE = os.cpu_count() or 1


def patch_threads(n_intra, n_inter=1):
    """替换 rapidocr 的 session 构造，注入线程数与 CPU 偏好"""
    import rapidocr_onnxruntime.utils as ru
    from onnxruntime import (GraphOptimizationLevel, InferenceSession,
                             SessionOptions)

    def __init__(self, config):
        opt = SessionOptions()
        opt.log_severity_level = 4
        opt.enable_cpu_mem_arena = False
        opt.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
        if n_intra and n_intra > 0:
            opt.intra_op_num_threads = n_intra
        if n_inter and n_inter > 0:
            opt.inter_op_num_threads = n_inter
        eps = [("CPUExecutionProvider",
                {"arena_extend_strategy": "kSameAsRequested"})]
        self._verify_model(config["model_path"])
        self.session = InferenceSession(config["model_path"],
                                        sess_options=opt, providers=eps)

    ru.OrtInferSession.__init__ = __init__


def build(det, batch, threads):
    import src.ocr as O
    # StockOCR.__init__ 内部会调模块级 _patch_ort_provider，这里整体替换掉
    O._patch_ort_provider = lambda use_gpu: patch_threads(threads)
    return O.StockOCR(gpu=False, det_limit=det, rec_batch=batch,
                      use_cls=False, verbose=False)


def run(ocr, arr, frames=4):
    ocr.recognize(arr)
    cp0, w0 = time.process_time(), time.perf_counter()
    n = 0
    for _ in range(frames):
        n += len(ocr.recognize(arr))
    cp1, w1 = time.process_time(), time.perf_counter()
    return ((w1 - w0) / frames * 1000,
            (cp1 - cp0) / (w1 - w0),
            n / frames)


def main():
    cfg = app.load_config()
    wins = find_windows(cfg["sources"])
    if not wins:
        print("没有聊天窗口")
        return 2
    info = [w for w in wins if "微信" in repr(w)] or wins[:1]
    info = info[0]
    full = grab_window(info)
    H, W = full.shape[:2]
    crop = full[0:int(H * 0.85), 230:W]

    print("=" * 100)
    print("P0-41 限制 onnxruntime 线程数的影响（纯 CPU，本机 %d 逻辑核）" % NCORE)
    print("=" * 100)
    print("  窗口 %s   消息区裁剪 %dx%d" % (info, crop.shape[1], crop.shape[0]))
    print("  固定 det640 / rec_batch=1 / 消息区裁剪，只变线程数\n")
    print("  %-16s %10s %9s %11s %13s %12s"
          % ("intra_op 线程", "单帧 ms", "占用核", "帧CPU秒", "峰值机器占用", "机器剩余"))
    print("  " + "-" * 76)

    rows = []
    for th, tag in [(0, "默认(全部核心)"), (8, "8"), (4, "4"), (2, "2"), (1, "1")]:
        ocr = build(640, 1, th)
        wall, cores, hit = run(ocr, crop)
        rows.append((tag, wall, cores))
        peak = min(100.0, 100.0 * cores / NCORE)
        print("  %-16s %10.0f %9.2f %11.2f %12.0f%% %11.1f 核"
              % (tag, wall, cores, wall * cores / 1000, peak, NCORE - cores))
        del ocr

    print("\n" + "=" * 100)
    print("换算到常见机器（按「1 秒 1 条消息」）")
    print("=" * 100)
    print("  %-16s %9s %12s %14s %14s" % ("线程设置", "单帧 ms", "峰值占核心", "4核机峰值", "8核机峰值"))
    print("  " + "-" * 70)
    for tag, wall, cores in rows:
        print("  %-16s %9.0f %12.1f %13s %13s"
              % (tag, wall, cores,
                 "%.0f%%" % min(100, 100.0 * cores / 4),
                 "%.0f%%" % min(100, 100.0 * cores / 8)))
    print("  " + "-" * 70)
    print("  说明：「峰值」= 识别那一下吃到多少核。峰值越小，机器越不会卡顿。")
    print("        墙钟会更长，但只要短于消息间隔，用户感知不到。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
