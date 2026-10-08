# -*- coding: utf-8 -*-
"""P0-39 GPU(DirectML) vs 纯 CPU 的 CPU 占用对比 + 真实节流下的平均负载

P0-38 回答了「忙时吃多少」，但有两处不足，这里补上：
  1. P0-38 的空转是满速轮询（67 次/秒），不是程序真实的 poll_interval=0.25
  2. 那台机器有 DirectML，走的是 GPU；普通办公机大概率没独显 → 走 CPU

用法：
    python probe/p39_cpu_modes.py
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main as app  # noqa: E402
from src.capture import ChangeGate, find_windows, grab_window  # noqa: E402

NCORE = os.cpu_count() or 1


def pick_window(cfg):
    wins = find_windows(cfg["sources"])
    if not wins:
        return None
    for w in wins:
        if "微信" in repr(w):
            return w
    return wins[0]


def bench_mode(label, gpu_arg, cfg, info, frames=8):
    from src.ocr import StockOCR
    o = cfg.get("ocr", {})
    ocr = StockOCR(gpu=gpu_arg,
                   det_limit=o.get("det_limit", 900),
                   rec_batch=o.get("rec_batch", 24),
                   use_cls=o.get("use_cls", False),
                   verbose=False)
    ocr = app.configure_ocr(ocr, cfg)

    arr = grab_window(info)
    # 预热（首次推理要建 DML 会话 / 分配 CPU arena）
    for _ in range(2):
        app.analyze_window(info, ocr, matcher, arr)

    # ---- 忙时：连打 ----
    cp0, w0 = time.process_time(), time.perf_counter()
    dt_list, hit = [], 0
    for _ in range(frames):
        t0 = time.perf_counter()
        items = app.analyze_window(info, ocr, matcher, arr)
        dt_list.append((time.perf_counter() - t0) * 1000)
        hit += len(items)
    cp1, w1 = time.process_time(), time.perf_counter()

    wall = (w1 - w0) / frames * 1000
    cores = (cp1 - cp0) / (w1 - w0)
    cpu_s_per_frame = (cp1 - cp0) / frames

    # ---- 空转：按真实 poll_interval 节流 ----
    gate = ChangeGate()
    gate.changed(arr)
    poll = cfg.get("poll_interval", 0.25) or 0.25
    cp0, w0 = time.process_time(), time.perf_counter()
    polls = 0
    t_end = time.time() + 6.0
    while time.time() < t_end:
        a = grab_window(info)
        if a is not None:
            gate.changed(a)
        polls += 1
        time.sleep(poll)
    cp1, w1 = time.process_time(), time.perf_counter()
    idle_cores = (cp1 - cp0) / (w1 - w0)
    idle_rate = polls / (w1 - w0)

    print("  %-14s det_limit=%-5d  单帧 %6.0f ms  忙时 %5.2f 核  单帧成本 %6.0f ms·核"
          % (label, ocr.det_limit, wall, cores, cpu_s_per_frame * 1000))
    print("  %-14s 空转 %.1f 次/秒  占用 %.3f 核   (窗口 %s)"
          % ("", idle_rate, idle_cores, "%dx%d" % (arr.shape[1], arr.shape[0])))
    return dict(label=label, wall=wall, cores=cores,
                cpu_s=cpu_s_per_frame, idle=idle_cores, hit=hit / frames)


def main():
    cfg = app.load_config()
    global matcher
    matcher = app.make_matcher(cfg, verbose=False)
    info = pick_window(cfg)
    if info is None:
        print("没有聊天窗口在跑")
        return 2

    from src.ocr import _available_eps
    eps = _available_eps()

    print("=" * 100)
    print("P0-39 GPU vs 纯 CPU  CPU 占用对比")
    print("=" * 100)
    print("  本机逻辑核心 %d     可用 EP: %s" % (NCORE, ", ".join(eps)))
    print("  测试窗口 %s" % info)
    print("  poll_interval %ss   → 程序每秒轮询 %.0f 次" % (cfg.get("poll_interval"), 1.0 / cfg.get("poll_interval", .25)))
    print()

    results = []
    print("-" * 100)
    if "DmlExecutionProvider" in eps:
        results.append(bench_mode("GPU/DirectML", "dml", cfg, info))
    results.append(bench_mode("纯 CPU", False, cfg, info))

    # ---------------- 按消息频率推算平均占用 ----------------
    print("\n" + "=" * 100)
    print("按「聊天消息到达频率」推算平均 CPU（每条消息触发 1 次 OCR）")
    print("=" * 100)
    rates = [(0.1, "群很安静 6 秒 1 条"), (0.5, "正常 2 秒 1 条"),
             (1.0, "活跃 1 秒 1 条"), (3.0, "刷屏 1 秒 3 条"),
             (10.0, "极端 1 秒 10 条")]
    hdr = "  %-18s" % "消息频率"
    for r in results:
        hdr += " %18s" % r["label"]
    print(hdr)
    print("  " + "-" * (18 + 19 * len(results)))
    for rate, desc in rates:
        line = "  %-18s" % ("%.1f 条/秒" % rate)
        for r in results:
            core = rate * r["cpu_s"] + r["idle"]
            line += " %11.2f核 %5.1f%%" % (core, 100.0 * core / NCORE)
        print(line + "   ← " + desc)

    print("\n  注：上表百分比 = 占满 %d 个逻辑核的多少。" % NCORE)
    print("      若换成 4 核老机器（4 线程），把上面的「核」数 ÷ 4 就是占用率。")
    print()
    print("  换成 4 线程机器时的占用率：")
    line = "  %-18s" % "消息频率"
    for r in results:
        line += " %18s" % r["label"]
    print(line)
    print("  " + "-" * (18 + 19 * len(results)))
    for rate, desc in rates:
        line = "  %-18s" % ("%.1f 条/秒" % rate)
        for r in results:
            core = rate * r["cpu_s"] + r["idle"]
            line += " %11.2f核 %5.1f%%" % (core, min(100.0, 100.0 * core / 4))
        print(line)
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
