# -*- coding: utf-8 -*-
"""p30 为什么键盘进不去东财？——焦点 / 权限 / UIPI 诊断

p29 现象：force_foreground 报成功，但 Ctrl+F / Ctrl+K / 直接输入 / 双回车
四种序列在主图区都只产生 0.1% 差异（= 基线噪声）→ 键盘事件没到东财。

要区分三种可能：
  ① 焦点不在东财主窗口（前台窗口 ≠ 键盘焦点窗口）
  ② 东财以管理员权限运行，而本脚本普通权限 → UIPI 静默丢弃 SendInput
  ③ 东财主窗口不处理键盘，键盘精灵挂在别的窗口/线程上
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM),
                              wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetFocus.restype = wt.HWND
user32.GetActiveWindow.restype = wt.HWND
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.GetCurrentProcess.restype = wt.HANDLE

EM_EXES = ("mainfree.exe", "maintrade.exe", "stockway.exe")


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD),
                ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND),
                ("hwndCapture", wt.HWND), ("hwndMenuOwner", wt.HWND),
                ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND),
                ("rcCaret", wt.RECT)]


def _exe(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        b = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        return b.value if kernel32.QueryFullProcessImageNameW(h, 0, b,
                                                              ctypes.byref(n)) else ""
    finally:
        kernel32.CloseHandle(h)


def cls(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def txt(h):
    b = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(h, b, 512)
    return b.value


def pid_of(h):
    p = wt.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def is_elevated(pid):
    """目标进程是否以提升权限运行（HIGH/System 完整性级别）"""
    PROCESS_QUERY_INFORMATION = 0x0400
    TOKEN_QUERY = 0x0008
    TokenElevation = 20
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return None
    try:
        tok = wt.HANDLE()
        if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
            return None
        try:
            val = wt.DWORD()
            ret = wt.DWORD()
            ok = advapi32.GetTokenInformation(
                tok, TokenElevation, ctypes.byref(val),
                ctypes.sizeof(val), ctypes.byref(ret))
            return bool(val.value) if ok else None
        finally:
            kernel32.CloseHandle(tok)
    finally:
        kernel32.CloseHandle(h)


advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD,
                                      ctypes.POINTER(wt.HANDLE)]
advapi32.GetTokenInformation.argtypes = [
    wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]


def check_elevation(pid, name):
    v = is_elevated(pid)
    if v is None:
        print("  %-16s 权限级别: 无法读取（可能更高完整性）" % name)
    else:
        print("  %-16s 权限级别: %s" % (name, "管理员(提升)" if v else "普通"))
    return v


def main():
    print("=" * 84)
    print("一、UIPI 权限对比（谁比谁高）")
    print("=" * 84)
    me = kernel32.GetCurrentProcess()
    TOKEN_QUERY = 0x0008
    TokenElevation = 20
    tok = wt.HANDLE()
    advapi32.OpenProcessToken(me, TOKEN_QUERY, ctypes.byref(tok))
    val, ret = wt.DWORD(), wt.DWORD()
    advapi32.GetTokenInformation(tok, TokenElevation, ctypes.byref(val),
                                 ctypes.sizeof(val), ctypes.byref(ret))
    print("  %-16s 权限级别: %s" % ("本脚本(python)",
                                   "管理员(提升)" if val.value else "普通"))
    kernel32.CloseHandle(tok)

    # 找东财
    em_hwnds = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        p = pid_of(hwnd)
        if os.path.basename(_exe(p)).lower() in EM_EXES:
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            em_hwnds.append((hwnd, p, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(cb, 0)
    if not em_hwnds:
        print("\n东财未运行")
        return 1
    em_hwnds.sort(key=lambda x: -x[2] * x[3])
    main_hwnd, em_pid = em_hwnds[0][0], em_hwnds[0][1]
    check_elevation(em_pid, "东方财富")

    print("\n" + "=" * 84)
    print("二、当前焦点链路")
    print("=" * 84)
    fg = user32.GetForegroundWindow()
    print("  GetForegroundWindow = 0x%08X  [%s] %r  pid=%d"
          % (fg, cls(fg), txt(fg)[:40], pid_of(fg)))
    tid = user32.GetWindowThreadProcessId(fg, None)
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
        print("  前台线程 %d 的焦点窗口 = 0x%08X  [%s] %r"
              % (tid, gti.hwndFocus or 0, cls(gti.hwndFocus or 0),
                 txt(gti.hwndFocus or 0)[:40]))
        print("  hwndActive=0x%08X  hwndCaret=0x%08X"
              % (gti.hwndActive or 0, gti.hwndCaret or 0))
    print("  东财主窗口       = 0x%08X  [%s] %r"
          % (main_hwnd, cls(main_hwnd), txt(main_hwnd)[:40]))
    print("  东财主窗口线程   = %d" % user32.GetWindowThreadProcessId(main_hwnd, None))

    print("\n" + "=" * 84)
    print("三、东财所有顶层窗口（判断键盘精灵挂在哪）")
    print("=" * 84)
    for h, p, w, hh in em_hwnds[:12]:
        print("  0x%08X %5dx%-5d tid=%-6d [%-30s] %r"
              % (h, w, hh, user32.GetWindowThreadProcessId(h, None),
                 cls(h)[:30], txt(h)[:34]))

    print("\n" + "=" * 84)
    print("四、SendInput 是否真的发出去了（发到记事本验证）")
    print("=" * 84)
    try:
        p = subprocess.Popen(["notepad.exe"])
        time.sleep(2.0)
        np_hwnd = None

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb2(hwnd, _):
            nonlocal np_hwnd
            if user32.IsWindowVisible(hwnd) and "记事本" in txt(hwnd) or \
               (user32.IsWindowVisible(hwnd) and os.path.basename(
                   _exe(pid_of(hwnd))).lower() == "notepad.exe"):
                r = wt.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                if r.right - r.left > 200:
                    np_hwnd = hwnd
            return True

        user32.EnumWindows(cb2, 0)
        if np_hwnd:
            print("  记事本窗口 0x%08X，抢前台后发 'AB' ..." % np_hwnd)
            from src.linkage import force_foreground, send_text
            force_foreground(np_hwnd)
            time.sleep(0.6)
            send_text("AB")
            time.sleep(0.8)
            buf = ctypes.create_unicode_buffer(64)
            n = user32.SendMessageW(np_hwnd, 0x000D, 64, ctypes.byref(buf)) \
                if False else None
            # 用 GetWindowText 取不到编辑框内容，改用 WM_GETTEXT 到焦点子窗口
            ed = user32.FindWindowExW(np_hwnd, None, "Edit", None)
            if ed:
                user32.SendMessageW(ed, 0x000D, 64, buf)   # WM_GETTEXT
                print("  记事本内容 = %r  → SendInput %s"
                      % (buf.value, "工作正常 ✓" if buf.value else "也失效 ✗"))
            else:
                print("  找不到记事本编辑框，跳过内容校验")
            p.terminate()
        else:
            print("  记事本窗口未找到")
            p.terminate()
    except Exception as e:
        print("  记事本测试异常:", e)

    print("\n" + "=" * 84)
    print("结论提示")
    print("=" * 84)
    print("  * 若东财=管理员 且 本脚本=普通 → SendInput 会被 UIPI 静默丢弃，")
    print("    必须让本程序也以管理员运行（或改用 PostMessage 到目标窗口）")
    print("  * 若前台窗口 != 焦点窗口 → 需要 AttachThreadInput 后再 SetFocus")
    return 0


if __name__ == "__main__":
    user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
    user32.FindWindowExW.restype = wt.HWND
    user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    sys.exit(main())
