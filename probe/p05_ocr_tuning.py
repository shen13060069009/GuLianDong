# -*- coding: utf-8 -*-
"""P0-5 OCR 性能调优

P0-4 暴露出 det 阶段吃满 4.3 秒的问题（默认识别长边 2000px，1.3M 像素全量硬啃）。
本探针系统性地扫参数，找出「可接受的准确率 + 可接受的耗时」的工作点。

扫描维度：
  1. max_side_len（下采样目标长边）：1600 / 1280 / 960 / 640
  2. intra_op_num_threads（ONNX 线程数）
  3. 区域裁剪（只识别聊天区，不识别左侧会话列表）
  4. use_cls 关闭（方向分类对横排 UI 文本是多余的）

每个配置跑两次，取第二次为热态耗时。
"""
import os
import platform
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP = os.path.join(ROOT, "out", "captures")
SRC = os.path.join(CAP, "Feishu_C.png")


def cpu_info():
    try:
        import subprocess
        out = subprocess.run(
            ["wmic", "cpu", "get", "Name,NumberOfCores,NumberOfLogicalProcessors", "/format:list"],
            capture_output=True, text=True, timeout=15,
        ).stdout
        return " ".join(l.strip() for l in out.splitlines() if l.strip())
    except Exception as e:
        return "CPU 信息读取失败: %s" % e


def make_engine(**kw):
    from rapidocr_onnxruntime import RapidOCR
    try:
        return RapidOCR(**kw), kw
    except TypeError as e:
        return None, str(e)


def run(engine, arr, warmup=True):
    if warmup:
        engine(arr)
    t0 = time.perf_counter()
    out = engine(arr)
    dt = (time.perf_counter() - t0) * 1000
    res = out[0] if isinstance(out, tuple) else out
    return (res or []), dt


def text_stats(res):
    n = len(res)
    chars = sum(len(r[1]) for r in res)
    avg_conf = float(np.mean([float(r[2]) for r in res])) if res else 0.0
    return n, chars, avg_conf


def main():
    print("=" * 100)
    print("P0-5 OCR 性能调优")
    print("=" * 100)
    print("  CPU      : %s" % cpu_info())
    print("  逻辑核心 : %d" % (os.cpu_count() or 0))
    print("  Python   : %s" % platform.python_version())
    print("  样本     : %s" % os.path.basename(SRC))

    if not os.path.exists(SRC):
        print("缺少样本文件，先跑 p03。")
        return

    full = Image.open(SRC).convert("RGB")
    W, H = full.size
    # 飞书布局：左侧会话列表约 230px，右侧为聊天区（模拟真实采集时的区域裁剪）
    CROP = (230, 80, min(W, 1000), H)
    region = full.crop(CROP)
    print("  原图     : %dx%d  (%.2f M px)" % (W, H, W * H / 1e6))
    print("  裁剪区   : %dx%d  (%.2f M px)" % (region.size[0], region.size[1],
                                             region.size[0] * region.size[1] / 1e6))

    cases = [
        ("基线 默认参数", full, {}),
        ("max_side=1600", full, {"max_side_len": 1600}),
        ("max_side=1280", full, {"max_side_len": 1280}),
        ("max_side=960", full, {"max_side_len": 960}),
        ("max_side=640", full, {"max_side_len": 640}),
        ("max_side=960 + 关cls", full, {"max_side_len": 960, "use_cls": False}),
        ("裁剪区 960", region, {"max_side_len": 960}),
        ("裁剪区 640", region, {"max_side_len": 640}),
        ("裁剪区 960 + 关cls", region, {"max_side_len": 960, "use_cls": False}),
        ("裁剪区 960 + 8线程", region, {"max_side_len": 960, "use_cls": False,
                                        "intra_op_num_threads": 8}),
        ("裁剪区 960 + 4线程", region, {"max_side_len": 960, "use_cls": False,
                                        "intra_op_num_threads": 4}),
    ]

    print()
    print("  %-24s %-8s %-8s %-8s %-9s %-8s %s"
          % ("配置", "文本块", "字符数", "平均置信", "热态耗时", "折算CPU", "备注"))
    print("  " + "-" * 96)

    best = None
    for label, img, kw in cases:
        eng, info = make_engine(**kw)
        if eng is None:
            print("  %-24s 参数不支持: %s" % (label, info))
            continue
        arr = np.asarray(img)
        try:
            res, dt = run(eng, arr)
        except Exception as e:
            print("  %-24s 运行失败: %s" % (label, e))
            continue
        n, chars, conf = text_stats(res)
        # 按 2 帧/秒轮询估算单核占用
        cpu = dt / 1000 * 2 * 100
        note = ""
        if label == "基线 默认参数":
            note = "参照"
        print("  %-24s %-8d %-8d %-8.3f %-9.0fms %-8.0f%% %s"
              % (label, n, chars, conf, dt, cpu, note))
        if n > 0 and (best is None or dt < best[2]):
            best = (label, n, dt, conf)

    print()
    if best:
        print("  最快可用配置: %s  (%.0f ms, %d 个文本块)" % (best[0], best[2], best[1]))
        print("  若 2 帧/秒轮询，OCR 占单核约 %.0f%%" % (best[2] / 1000 * 2 * 100))


if __name__ == "__main__":
    main()
