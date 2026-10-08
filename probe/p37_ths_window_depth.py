# -*- coding: utf-8 -*-
"""p37 深挖：同花顺主窗口为什么只有 160x28？

三路取证：
  A. 进程级检测（CreateToolhelp32Snapshot）—— 与窗口无关，判定「在不在跑」
  B. 窗口级细节 —— GetWindowPlacement / style / exstyle / owner / parent
  C. 真截图 —— 把该 hwnd 抓下来看内容到底是什么
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capture import grab_window, window_info  # noqa: E402
from src.linkage import _proc_exe, _text         # noqa: E402
from PIL import Image                            # noqa: E402

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]

TH32CS_SNAPPROCESS = 0x00000002


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG),
                ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260)]


def proc_snapshot():
    """不依赖窗口，直接问系统「这个 exe 在不在跑」"""
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return {}
    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
    out = {}
    ok = k32.Process32First(snap, ctypes.byref(pe))
    while ok:
        name = pe.szExeFile.decode("gbk", "ignore").lower()
        out.setdefault(name, []).append(pe.th32ProcessID)
        ok = k32.Process32Next(snap, ctypes.byref(pe))
    k32.CloseHandle(snap)
    return out


def windows_of(exe_names):
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = os.path.basename(_proc_exe(pid.value)).lower()
        if exe in exe_names:
            hits.append((hwnd, pid.value, exe))
        return True

    user32.EnumWindows(cb, 0)
    return hits


def main():
    procs = proc_snapshot()
    print("=" * 96)
    print("A. 进程级检测（与窗口无关）")
    print("=" * 96)
    for name in ("hexin.exe", "hexinhelper.exe", "mainfree.exe", "tdxw.exe"):
        pids = procs.get(name)
        print("  %-18s %s" % (name, ("运行中 PID=%s" % pids) if pids else "未运行"))

    print()
    print("=" * 96)
    print("B/C. 同花顺顶层窗口细节 + 截图")
    print("=" * 96)
    hits = windows_of({"hexin.exe"})
    if not hits:
        print("  EnumWindows 没找到任何 hexin.exe 的顶层窗口")
        return

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "out", "ths_diag")
    os.makedirs(out, exist_ok=True)

    for hwnd, pid, exe in hits:
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        cr = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(cr))
        wp = ctypes.create_string_buffer(64)
        user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
        # WINDOWPLACEMENT: UINT length; UINT flags; UINT showCmd; POINT ptMin; POINT ptMax; RECT rcNormal
        vals = ctypes.cast(wp, ctypes.POINTER(wt.UINT))
        show_cmd = vals[2]
        normal = ctypes.cast(ctypes.byref(wp, 28), ctypes.POINTER(wt.RECT)).contents

        style = user32.GetWindowLongW(hwnd, -16)
        exstyle = user32.GetWindowLongW(hwnd, -20)

        print("\n  hwnd=0x%08X pid=%d" % (hwnd, pid))
        print("    标题        : %s" % _text(hwnd)[:60])
        print("    GetWindowRect: (%d,%d)-(%d,%d)  %dx%d"
              % (r.left, r.top, r.right, r.bottom, r.right - r.left, r.bottom - r.top))
        print("    客户区       : %dx%d" % (cr.right, cr.bottom))
        print("    showCmd      : %d  (1=normal 2=minimized 3=maximized)"
              % show_cmd)
        print("    还原位置     : (%d,%d)-(%d,%d)  %dx%d"
              % (normal.left, normal.top, normal.right, normal.bottom,
                 normal.right - normal.left, normal.bottom - normal.top))
        print("    IsIconic=%s  IsZoomed=%s  IsVisible=%s"
              % (bool(user32.IsIconic(hwnd)), bool(user32.IsZoomed(hwnd)),
                 bool(user32.IsWindowVisible(hwnd))))
        print("    style=0x%08X  WS_VISIBLE=%s  exstyle=0x%08X  WS_EX_TOOLWINDOW=%s"
              % (style, bool(style & 0x10000000), exstyle, bool(exstyle & 0x00000080)))

        info = window_info(hwnd)
        arr = grab_window(info)
        if arr is not None and arr.size:
            fn = os.path.join(out, "hwnd_%08X.png" % hwnd)
            Image.fromarray(arr).save(fn)
            print("    截图         : %s  (%dx%d)" % (fn, arr.shape[1], arr.shape[0]))
        else:
            print("    截图         : 失败")


if __name__ == "__main__":
    main()
