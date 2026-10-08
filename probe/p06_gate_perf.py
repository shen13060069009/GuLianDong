# -*- coding: utf-8 -*-
"""P0-6 采集性能架构验证

核心思路：不要把 OCR 挂在定时器上，而是先用「廉价的变化检测」做门控，
只有画面真的变了才触发 OCR。

本探针量化三个环节各自的成本：
  1. PrintWindow(PW_RENDERFULLCONTENT) 抓一帧要多久
  2. 降采样 + 帧差比对要多久
  3. 模拟 3 秒轮询：静止态触发了几次 OCR，实际 CPU 开销多少

这个数据决定「窗口追踪层 + 文字采集层」的轮询架构。
"""
import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

user32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import numpy as np
import win32gui
import win32ui
from PIL import Image

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

PW_RENDERFULLCONTENT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def get_exe(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def find_big_window(base_name):
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = get_exe(pid.value)
        if os.path.basename(exe) != base_name:
            return True
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        if r - l > 200 and b - t > 200:
            hits.append((hwnd, (l, t, r, b), (r - l) * (b - t)))
        return True

    user32.EnumWindows(cb, 0)
    return max(hits, key=lambda x: x[2]) if hits else None


def grab(hwnd, rect):
    l, t, r, b = rect
    w, h = r - l, b - t
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    arr = np.frombuffer(bits, dtype=np.uint8).reshape((info["bmHeight"], info["bmWidth"], 4))
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    return arr[:, :, :3][:, :, ::-1].copy()  # BGRX -> RGB


def fingerprint(arr, scale=8):
    """把画面降采样成极小的指纹，用于快速比对是否变化"""
    h, w = arr.shape[:2]
    small = arr[::scale, ::scale]
    return hashlib.md5(np.ascontiguousarray(small).tobytes()).hexdigest()


def main():
    for target, label in (("Feishu.exe", "飞书 (Electron)"), ("TdxW.exe", "通达信 (Win32 自绘)")):
        hit = find_big_window(target)
        if not hit:
            print("[%s] 未找到可见大窗口，跳过" % label)
            continue
        hwnd, rect, area = hit
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        print("=" * 96)
        print("[%s]  hwnd=0x%08X  %dx%d (%.2f M px)" % (label, hwnd, w, h, w * h / 1e6))
        print("=" * 96)

        # --- 1. 纯抓帧成本 ---
        times = []
        for _ in range(15):
            t0 = time.perf_counter()
            arr = grab(hwnd, rect)
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        grab_ms = times[len(times) // 2]
        print("  [1] PrintWindow(PW_RENDERFULLCONTENT) 抓帧")
        print("      中位 %.0f ms   最快 %.0f ms   最慢 %.0f ms"
              % (grab_ms, times[0], times[-1]))

        # --- 2. 降采样指纹成本 ---
        fp_times = []
        for _ in range(30):
            t0 = time.perf_counter()
            fp = fingerprint(arr, scale=8)
            fp_times.append((time.perf_counter() - t0) * 1000)
        fp_times.sort()
        print("  [2] 降采样指纹 (1/8) 计算")
        print("      中位 %.1f ms   指纹=%s" % (fp_times[len(fp_times) // 2], fp[:12]))

        # --- 3. 帧差门控仿真：3 秒轮询，画面静止 ---
        print("  [3] 帧差门控仿真（3 秒，画面静止，轮询间隔 200ms）")
        seen = set()
        ocr_calls = 0
        t_end = time.perf_counter() + 3.0
        polls = 0
        t_start = time.perf_counter()
        while time.perf_counter() < t_end:
            t0 = time.perf_counter()
            a = grab(hwnd, rect)
            f = fingerprint(a, scale=8)
            polls += 1
            if f not in seen:
                seen.add(f)
                ocr_calls += 1  # 这里代表"会触发一次 OCR"
            dt = (time.perf_counter() - t0) * 1000
            sleep = 200 - dt
            if sleep > 0:
                time.sleep(sleep / 1000)

        wall = (time.perf_counter() - t_start) * 1000
        print("      轮询次数 = %d   触发 OCR 次数 = %d" % (polls, ocr_calls))
        print("      实际墙钟耗时 = %.0f ms" % wall)

        # 静止态开销：只有抓帧 + 指纹，不含 OCR
        per_poll = wall / polls
        idle_cpu_1core = per_poll / 200 * 100
        print("      单次轮询 %.0f ms  →  静止态单核占用约 %.1f%%  (20 核整机约 %.1f%%)"
              % (per_poll, idle_cpu_1core, idle_cpu_1core / 20))

        # 对比：不用门控，每 200ms 硬跑一次 OCR
        ocr_ms = 900
        naive = ocr_ms / 200 * 100
        print("      对比：无门控硬跑 OCR 单核占用约 %.0f%%（20 核整机 %.0f%%）"
              % (naive, naive / 20))
        print("      门控带来的优化倍数 ≈ %.0fx" % (naive / max(idle_cpu_1core, 0.01)))
        print()


if __name__ == "__main__":
    main()
