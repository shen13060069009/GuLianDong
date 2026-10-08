# -*- coding: utf-8 -*-
"""P0-38 CPU 占用实测：这个工具到底吃多少 CPU，普通机器能不能带

不猜，全部实测。分四个维度：

  A. 单帧成本拆解  取屏 / 门控 / det / rec 各占多少 ms
  B. 进程 CPU       time.process_time() 增量 ÷ 墙钟 → 「吃掉几个核」
  C. 整机占用       GetSystemTimes 内核+用户+空闲增量 → 「整机负载几个百分点」
  D. 真实占空比    连续采样 N 秒，统计画面真正变化的帧占比
                   → 这是最关键的指标：OCR 只在变化时触发，
                     实际 CPU = 忙时占用 × 占空比

用法：
    python probe/p38_cpu_profile.py [秒] [帧数]
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

import main as app  # noqa: E402
from src.capture import ChangeGate, find_windows, grab_window  # noqa: E402


# ---------------------------------------------------------------- 整机 CPU
class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


def _ft(v):
    return (v.dwHighDateTime << 32) | v.dwLowDateTime


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetSystemTimes.argtypes = [ctypes.POINTER(_FILETIME)] * 3


def sys_times():
    """返回 (busy_ticks, total_ticks)，kernel 里含 idle，要减掉"""
    idle, kern, user = _FILETIME(), _FILETIME(), _FILETIME()
    _k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
    total = _ft(kern) + _ft(user)
    return total - _ft(idle), total


def cpu_count():
    return os.cpu_count() or 1


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    frames = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    NCORE = cpu_count()

    cfg = app.load_config()
    ocr = app.make_ocr(cfg, verbose=False)
    matcher = app.make_matcher(cfg, verbose=False)

    # ---- 引擎档位 ----
    from src.ocr import _available_eps
    eps = _available_eps()
    print("=" * 96)
    print("P0-38 CPU 占用实测")
    print("=" * 96)
    print("  逻辑核心        %d" % NCORE)
    print("  OCR 执行后端    %s" % ("DirectML (GPU)" if ocr.use_gpu else "CPU"))
    print("  可用 EP         %s" % ", ".join(eps))
    print("  det_limit       %d      rec_batch %d      angle_cls %s"
          % (ocr.det_limit, ocr.rec_batch, ocr.use_cls))

    wins = find_windows(cfg["sources"])
    if not wins:
        print("\n没有聊天窗口在跑，改用离屏样本测")
        return 2
    info = wins[0]
    for w in wins:
        if "微信" in repr(w):
            info = w
    print("  测试窗口        %s" % info)

    # ============================================================ A 单帧成本
    print("\n" + "-" * 96)
    print("A. 单帧成本拆解")
    print("-" * 96)

    arr = None
    ts = []
    for _ in range(5):
        t0 = time.perf_counter()
        arr = grab_window(info)
        ts.append((time.perf_counter() - t0) * 1000)
    grab_ms = sum(ts) / len(ts)
    print("  取屏 PrintWindow      %6.1f ms" % grab_ms)
    if arr is None:
        print("  取屏失败")
        return 3

    gate = ChangeGate()
    ts = []
    for _ in range(30):
        t0 = time.perf_counter()
        gate.changed(arr)
        ts.append((time.perf_counter() - t0) * 1000)
    gate_ms = sum(ts) / len(ts)
    print("  指纹门控              %6.2f ms" % gate_ms)

    # OCR 单帧（走真实 analyze_window，含自适应 det_limit）
    ocr_ms = []
    hits = 0
    for i in range(frames + 2):
        t0 = time.perf_counter()
        items = app.analyze_window(info, ocr, matcher, arr)
        dt = (time.perf_counter() - t0) * 1000
        if i >= 2:                     # 丢掉预热
            ocr_ms.append(dt)
            hits += len(items)
    ocr_avg = sum(ocr_ms) / len(ocr_ms)
    print("  OCR 全流程            %6.1f ms   (min %.0f / max %.0f, det_limit=%d)"
          % (ocr_avg, min(ocr_ms), max(ocr_ms), ocr.det_limit))
    print("    其中 det+rec 模型   %6.1f ms  ← 占 %.0f%%"
          % (ocr_avg, 100.0))
    print("  单帧命中股名          %.1f 个/帧" % (hits / max(1, len(ocr_ms))))
    print("  ------------------------------------------------")
    print("  门控判「有变化」时    %6.1f ms    (= 取屏 + 门控 + OCR)" % (grab_ms + gate_ms + ocr_avg))
    print("  门控判「无变化」时    %6.1f ms    (= 取屏 + 门控，不跑 OCR)" % (grab_ms + gate_ms))

    # ============================================================ B 进程 CPU
    print("\n" + "-" * 96)
    print("B. 进程 CPU 占用（忙时）")
    print("-" * 96)

    cp0, wt0 = time.process_time(), time.perf_counter()
    for _ in range(frames):
        app.analyze_window(info, ocr, matcher, arr)
    cp1, wt1 = time.process_time(), time.perf_counter()

    cpu_busy = (cp1 - cp0) / (wt1 - wt0)
    print("  纯 OCR 连打 %.0f 帧" % frames)
    print("    墙钟 %.2f s   CPU 时间 %.2f s" % (wt1 - wt0, cp1 - cp0))
    print("    → 占用 %.2f 个核   = 本机 %d 核的 %.0f%%"
          % (cpu_busy, NCORE, 100.0 * cpu_busy / NCORE))
    print("    → 单帧 CPU 成本 %.0f ms·核" % ((cp1 - cp0) / frames * 1000))

    # 同一时刻的整机占用
    b0, t0_ = sys_times()
    time.sleep(2.0)
    b1, t1_ = sys_times()
    print("    该窗口期整机 CPU %.1f%%   ← 含系统其他进程"
          % (100.0 * (b1 - b0) / max(1, t1_ - t0_)))

    # ============================================================ C 空转
    print("\n" + "-" * 96)
    print("C. 空转占用（画面不动，只取屏 + 门控）")
    print("-" * 96)
    cp0, wt0 = time.process_time(), time.perf_counter()
    n = 0
    t_end = time.time() + 6.0
    while time.time() < t_end:
        a = grab_window(info)
        if a is not None:
            gate.changed(a)
        n += 1
    cp1, wt1 = time.process_time(), time.perf_counter()
    cpu_idle = (cp1 - cp0) / (wt1 - wt0)
    print("  %.1f s 内轮询 %d 次（%.1f 次/秒）" % (wt1 - wt0, n, n / (wt1 - wt0)))
    print("    → 占用 %.3f 个核  = 本机 %d 核的 %.1f%%"
          % (cpu_idle, NCORE, 100.0 * cpu_idle / NCORE))

    # ============================================================ D 占空比
    print("\n" + "-" * 96)
    print("D. 真实占空比（画面真正变化的帧占比）—— 决定实际平均 CPU")
    print("-" * 96)
    print("  采样 %.0f 秒，模拟程序真实轮询节奏……" % secs)
    g = ChangeGate()
    g.changed(grab_window(info))
    changed = total = 0
    t_end = time.time() + secs
    poll = cfg.get("poll_interval", 0.2) or 0.2
    while time.time() < t_end:
        a = grab_window(info)
        if a is None:
            break
        total += 1
        if g.changed(a):
            changed += 1
        time.sleep(poll)
    duty = changed / max(1, total)
    print("  采样帧 %d，其中变化 %d 帧   → 占空比 %.1f%%" % (total, changed, duty * 100))

    # ============================================================ 汇总
    busy_lat = grab_ms + gate_ms + ocr_avg
    avg_cpu = duty * cpu_busy + (1 - duty) * cpu_idle
    print("\n" + "=" * 96)
    print("汇总")
    print("=" * 96)
    print("  %-30s %12s %14s" % ("场景", "占用(核)", "整机占比"))
    print("  " + "-" * 58)
    print("  %-30s %12.2f %13.1f%%" % ("持续刷屏（每帧都 OCR）", cpu_busy, 100.0 * cpu_busy / NCORE))
    print("  %-30s %12.3f %13.1f%%" % ("静止挂机（只有取屏+门控）", cpu_idle, 100.0 * cpu_idle / NCORE))
    print("  %-30s %12.2f %13.1f%%" % ("实测占空比下的平均", avg_cpu, 100.0 * avg_cpu / NCORE))
    print("  " + "-" * 58)
    print("  响应延迟：画面变化后 %.0f ms 内完成识别" % busy_lat)
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
