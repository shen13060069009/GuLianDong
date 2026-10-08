# -*- coding: utf-8 -*-
"""p27 启动东财 / 同花顺并解剖窗口结构

目的：
  1. 拿到两家客户端的真实进程名（决定 linkage.TARGETS 的 proc 字段）
  2. 拿到顶层窗口的类名 / 标题（决定跳转该往哪个 hwnd 发消息）
  3. 枚举子窗口，判断是「自绘 + 消息循环」还是「CEF 网页型版面」
     —— 后者是通达信踩过的坑（键盘注入全部无效）

用法：
  python probe/p27_launch_probe.py            # 只探测已运行的
  python probe/p27_launch_probe.py --launch   # 先启动再探测
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

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM),
                              wt.LPARAM]
user32.EnumChildWindows.argtypes = [wt.HWND,
                                    ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM),
                                    wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

TARGETS = [
    ("东方财富", r"C:\eastmoney\dfcf\mainfree.exe", r"C:\eastmoney\dfcf"),
    ("同花顺", r"D:\同花顺软件\同花顺\hexin.exe", r"D:\同花顺软件\同花顺"),
]


def exe_of(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def text_of(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def class_of(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def rect_of(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def children(hwnd, depth=2):
    out = []

    def walk(h, d):
        if d > depth:
            return True

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(ch, _):
            x, y, w, hh = rect_of(ch)
            out.append((d, ch, class_of(ch), text_of(ch)[:40], w, hh))
            if d < depth:
                walk(ch, d + 1)
            return True

        user32.EnumChildWindows(h, cb, 0)
        return True

    walk(hwnd, 1)
    return out


def scan(keywords):
    """枚举所有可见顶层窗口，返回属于这些进程的"""
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        path = exe_of(pid.value).lower()
        if not path:
            return True
        base = os.path.basename(path)
        if any(k.lower() in base for k in keywords):
            x, y, w, h = rect_of(hwnd)
            hits.append({
                "hwnd": hwnd, "pid": pid.value, "exe": base,
                "path": path, "cls": class_of(hwnd),
                "title": text_of(hwnd), "rect": (x, y, w, h),
                "visible": bool(user32.IsWindowVisible(hwnd)),
            })
        return True

    user32.EnumWindows(cb, 0)
    return hits


def main():
    launch = "--launch" in sys.argv

    if launch:
        print("=" * 88)
        print("启动客户端")
        print("=" * 88)
        for label, exe, cwd in TARGETS:
            if not os.path.exists(exe):
                print("  [%s] 找不到 %s" % (label, exe))
                continue
            if scan([os.path.basename(exe)]):
                print("  [%s] 已在运行，跳过启动" % label)
                continue
            try:
                subprocess.Popen([exe], cwd=cwd,
                                 creationflags=0x00000008)  # DETACHED_PROCESS
                print("  [%s] 已启动 %s" % (label, exe))
            except Exception as e:
                print("  [%s] 启动失败: %s" % (label, e))
        print("\n  等待 18 秒让窗口起来 ...")
        for i in range(18, 0, -1):
            time.sleep(1)
            if i % 6 == 0:
                print("    %d ..." % i)

    KEYS = ["mainfree", "maintrade", "stockway", "emweb", "etweb",
            "hexin", "hxgc", "xiadan", "weituonew", "hxaime"]
    hits = scan(KEYS)

    print("=" * 88)
    print("相关顶层窗口 %d 个" % len(hits))
    print("=" * 88)
    for h in sorted(hits, key=lambda x: -x["rect"][2] * x["rect"][3]):
        x, y, w, hh = h["rect"]
        print("  hwnd=0x%08X pid=%-6d %-16s [%-28s]" % (
            h["hwnd"], h["pid"], h["exe"], h["cls"][:28]))
        print("       %5dx%-5d @(%6d,%6d)  visible=%s  %r"
              % (w, hh, x, y, h["visible"], h["title"][:56]))

    # 挑面积最大的窗口解剖子窗口
    for h in sorted(hits, key=lambda x: -x["rect"][2] * x["rect"][3])[:4]:
        if h["rect"][2] < 400:
            continue
        print()
        print("-" * 88)
        print("解剖 %s hwnd=0x%08X  %s" % (h["exe"], h["hwnd"], h["title"][:50]))
        print("-" * 88)
        kids = children(h["hwnd"], depth=2)
        print("  子窗口 %d 个" % len(kids))
        cef = 0
        for d, ch, cls, t, w, hh in kids[:40]:
            mark = ""
            low = cls.lower()
            if "chrome" in low or "cef" in low or "widget" in low:
                mark = "  ← CEF/Chromium"
                cef += 1
            print("  %s0x%08X %-34s %5dx%-5d %r%s"
                  % ("  " * d, ch, cls[:34], w, hh, t[:26], mark))
        print("  → 含 CEF/Chromium 子窗口 %d 个" % cef)
        if cef:
            print("  ⚠ 网页型版面风险：键盘注入可能被内嵌浏览器吃掉（通达信同坑）")


if __name__ == "__main__":
    main()
