# -*- coding: utf-8 -*-
"""P0-2 环境探针

1. 目标进程的【全部】窗口（含不可见 / 最小化 / DWM 隐藏），确认窗口是否真的存在
2. 显示器布局（数量 / 坐标 / 主屏）
3. 窗口所在显示器的 DPI 缩放

这一步决定「窗口追踪层」的坐标基准，多屏 + 负坐标 + DPI 缩放
是高亮层最容易翻车的地方。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
user32.MonitorFromWindow.restype = wt.HANDLE

kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]

shcore.GetDpiForMonitor.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.POINTER(wt.UINT), ctypes.POINTER(wt.UINT)]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_CLOAKED = 14
MONITOR_DEFAULTTONEAREST = 2
MDT_EFFECTIVE_DPI = 0

TARGETS = {
    "Weixin.exe": "微信 4.x",
    "WeChat.exe": "微信 3.x",
    "DingTalk.exe": "钉钉",
    "Feishu.exe": "飞书",
    "Lark.exe": "飞书",
    "WXWork.exe": "企业微信",
    "TdxW.exe": "通达信",
    "EMClient.exe": "东方财富",
    "Hevo.exe": "同花顺",
}

MONITORENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HANDLE, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


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


def wtext(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.GetWindowTextW(hwnd, buf, n + 2)
    return buf.value


def wclass(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetClassNameW(hwnd, buf, 512)
    return buf.value


def wrect(hwnd):
    r = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right, r.bottom)


def fmt(r):
    if not r:
        return "-"
    return "%dx%d@%d,%d" % (r[2] - r[0], r[3] - r[1], r[0], r[1])


def is_cloaked(hwnd):
    v = wt.DWORD(0)
    hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(v), ctypes.sizeof(v))
    return (hr == 0 and v.value != 0), v.value


def monitor_dpi(hwnd):
    mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not mon:
        return None
    dx, dy = wt.UINT(0), wt.UINT(0)
    hr = shcore.GetDpiForMonitor(mon, MDT_EFFECTIVE_DPI, ctypes.byref(dx), ctypes.byref(dy))
    if hr != 0:
        return None
    return (dx.value, dy.value)


def list_monitors():
    out = []

    @MONITORENUMPROC
    def cb(hmon, hdc, lprc, lparam):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            dx, dy = wt.UINT(0), wt.UINT(0)
            shcore.GetDpiForMonitor(hmon, MDT_EFFECTIVE_DPI, ctypes.byref(dx), ctypes.byref(dy))
            out.append(dict(
                handle=hmon,
                rect=(mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right, mi.rcMonitor.bottom),
                work=(mi.rcWork.left, mi.rcWork.top, mi.rcWork.right, mi.rcWork.bottom),
                primary=bool(mi.dwFlags & 1),
                dpi=(dx.value, dy.value),
            ))
        return True

    user32.EnumDisplayMonitors(None, None, cb, 0)
    return out


def main():
    print("=" * 100)
    print("显示器布局")
    print("=" * 100)
    mons = list_monitors()
    print("  显示器数量: %d" % len(mons))
    for i, m in enumerate(mons):
        r, w = m["rect"], m["work"]
        print("  #%d %s  %-22s 工作区=%-22s DPI=%d (缩放 %d%%)"
              % (i, "主屏" if m["primary"] else "副屏", fmt(r), fmt(w),
                 m["dpi"][0], round(m["dpi"][0] / 96 * 100)))

    print()
    print("=" * 100)
    print("目标进程的全部窗口（含隐藏 / 最小化 / DWM Cloaked）")
    print("=" * 100)
    found = {}

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = get_exe(pid.value)
        base = os.path.basename(exe) if exe else ""
        if base not in TARGETS:
            return True
        vis = bool(user32.IsWindowVisible(hwnd))
        ico = bool(user32.IsIconic(hwnd))
        cloak, cval = is_cloaked(hwnd)
        r = wrect(hwnd)
        rec = dict(hwnd=hwnd, pid=pid.value, base=base, title=wtext(hwnd), cls=wclass(hwnd),
                   rect=r, vis=vis, ico=ico, cloak=cloak, cval=cval, dpi=monitor_dpi(hwnd))
        found.setdefault(base, []).append(rec)
        return True

    user32.EnumWindows(cb, 0)

    if not found:
        print("  未发现任何目标进程的窗口")
    for base, items in sorted(found.items()):
        real = [x for x in items if x["rect"] and (x["rect"][2] - x["rect"][0]) > 100
                and (x["rect"][3] - x["rect"][1]) > 100]
        print("\n[%s] %s  窗口总数=%d  其中大窗口(>100x100)=%d"
              % (base, TARGETS[base], len(items), len(real)))
        for x in items:
            if not x["rect"] or (x["rect"][2] - x["rect"][0]) < 100 or (x["rect"][3] - x["rect"][1]) < 100:
                continue
            flags = []
            flags.append("可见" if x["vis"] else "不可见")
            if x["ico"]:
                flags.append("已最小化")
            if x["cloak"]:
                flags.append("DWM隐藏(v=%d)" % x["cval"])
            dpi = ("DPI=%d" % x["dpi"][0]) if x["dpi"] else "DPI=?"
            print("    hwnd=0x%08X  %-30s %-24s %-22s %s  pid=%d"
                  % (x["hwnd"], x["cls"][:29], fmt(x["rect"]), "/".join(flags), dpi, x["pid"]))
            print("        标题: %s" % (x["title"] or "(无标题)"))

    print()
    print("=" * 100)
    print("结论提示")
    print("=" * 100)
    for base in ("Weixin.exe", "DingTalk.exe", "Feishu.exe", "TdxW.exe"):
        if base in found:
            big = [x for x in found[base] if x["rect"] and (x["rect"][2] - x["rect"][0]) > 100
                   and (x["rect"][3] - x["rect"][1]) > 100]
            print("  %-14s 窗口存在=%s  可用大窗=%d" % (base, "是" if big else "否（仅隐藏）", len(big)))
        else:
            print("  %-14s 未运行" % base)


if __name__ == "__main__":
    main()
