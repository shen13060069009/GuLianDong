# -*- coding: utf-8 -*-
"""P0-1 窗口枚举探针

目的：在不依赖任何第三方库的前提下，把当前桌面上所有可见顶层窗口
（标题 / 类名 / 进程 / 矩形）列出来，并标出目标应用，同时把目标应用
的子窗口树打印出来。

这一步决定后面「窗口追踪层」能不能可靠地锁定聊天区域。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- 必须先声明 argtypes，否则 64 位下 HWND 会被截断成 int32 ---
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.EnumChildWindows.argtypes = [wt.HWND, ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindow.argtypes = [wt.HWND, wt.UINT]

kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GW_OWNER = 4

# 目标应用：识别源 + 联动目标
TARGET_PROC = {
    "Weixin.exe": ("识别源", "微信 4.x"),
    "WeChat.exe": ("识别源", "微信 3.x"),
    "WeChatAppEx.exe": ("识别源", "微信内嵌容器"),
    "DingTalk.exe": ("识别源", "钉钉"),
    "WXWork.exe": ("识别源", "企业微信"),
    "Lark.exe": ("识别源", "飞书"),
    "TdxW.exe": ("联动目标", "通达信"),
    "EMClient.exe": ("联动目标", "东方财富"),
    "Hevo.exe": ("联动目标", "同花顺"),
}


def get_pid(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


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


def is_toplevel(hwnd):
    """排除 owned window（弹窗/工具窗），只保留真正的顶层主窗。"""
    return user32.GetWindow(hwnd, GW_OWNER) == 0


def collect():
    rows = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = wtext(hwnd)
        cls = wclass(hwnd)
        pid = get_pid(hwnd)
        exe = get_exe(pid)
        base = os.path.basename(exe) if exe else ""
        rect = wrect(hwnd)
        rows.append(
            dict(
                hwnd=hwnd,
                title=title,
                cls=cls,
                pid=pid,
                exe=exe,
                base=base,
                rect=rect,
                toplevel=is_toplevel(hwnd),
            )
        )
        return True

    user32.EnumWindows(cb, 0)
    return rows


def fmt_rect(r):
    if not r:
        return "-"
    return "%dx%d@%d,%d" % (r[2] - r[0], r[3] - r[1], r[0], r[1])


def main():
    rows = collect()
    targets = [r for r in rows if r["base"] in TARGET_PROC]

    print("=" * 108)
    print("P0-1 目标应用顶层窗口")
    print("=" * 108)
    if not targets:
        print("  未发现目标应用窗口（可能未启动，或窗口被最小化到托盘）")
    for r in sorted(targets, key=lambda x: x["base"]):
        kind, label = TARGET_PROC[r["base"]]
        print("[%s] %-16s hwnd=0x%08X  pid=%-6d  %-16s class=%-28s rect=%s"
              % (kind, label, r["hwnd"], r["pid"], r["base"], r["cls"], fmt_rect(r["rect"])))
        print("        标题: %s" % (r["title"] or "(无标题)"))
        print("        路径: %s" % r["exe"])

    print()
    print("=" * 108)
    print("全部可见顶层窗口（前 40 条，用于确认枚举通道正常）")
    print("=" * 108)
    tl = [r for r in rows if r["toplevel"]]
    print("  顶层窗口总数 = %d（全部可见窗口 %d）" % (len(tl), len(rows)))
    for r in sorted(tl, key=lambda x: -(x["rect"][2] - x["rect"][0]) * (x["rect"][3] - x["rect"][1]) if x["rect"] else 0)[:40]:
        print("  %-24s %-30s %-22s %s"
              % (r["base"][:23], r["cls"][:29], fmt_rect(r["rect"]), r["title"][:40]))

    # --- 目标进程的子窗口树 ---
    for base in ("Weixin.exe", "DingTalk.exe", "TdxW.exe"):
        tops = [r for r in targets if r["base"] == base and r["toplevel"]]
        if not tops:
            continue
        print()
        print("=" * 108)
        print("子窗口树：%s（%s）" % (base, TARGET_PROC[base][1]))
        print("=" * 108)
        for top in tops:
            print("根 hwnd=0x%08X  class=%s  rect=%s" % (top["hwnd"], top["cls"], fmt_rect(top["rect"])))
            depth_cap = [0]
            count = [0]

            @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
            def child_cb(hwnd, _):
                count[0] += 1
                if count[0] > 60:
                    return False
                cls = wclass(hwnd)
                t = wtext(hwnd)
                r = wrect(hwnd)
                # 用 parent 递归算深度
                print("    hwnd=0x%08X  %-32s %-30s %s"
                      % (hwnd, cls[:31], fmt_rect(r), t[:34]))
                return True

            user32.EnumChildWindows(top["hwnd"], child_cb, 0)
            print("    （子窗口共 %d 个）" % count[0])

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "p01_windows.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write("%-24s | %-40s | %-30s | pid=%-6d | %s\n"
                    % (r["base"], (r["title"] or "")[:39], r["cls"][:29], r["pid"], fmt_rect(r["rect"])))
    print()
    print("完整清单已写入: %s" % out)


if __name__ == "__main__":
    main()
