# -*- coding: utf-8 -*-
"""p28 东方财富终端 代码跳转方案实测

东财窗口结构（p27 解剖）：
    0x00050F22  Afx:000F0000:8:00010003:0000   '东方财富终端'  2566x1036
      └ MDIClient
        └ AfxFrameOrView140u  'Luke View Manager'
          └ ContainerWnd_DC              ← DuiLib 自绘容器
    深度 2 内没有 CEF/Chromium 子窗口 → 键盘消息有机会进主消息循环

要试的方案（按侵入性从低到高）：
  A. PostMessage WM_CHAR 数字 + 回车 到主窗口      （后台静默，最优）
  B. PostMessage WM_KEYDOWN/WM_KEYUP 到主窗口
  C. SendInput 键盘精灵（抢前台 + ESC + 代码 + 回车）
  D. 东财特有的搜索快捷键（Ctrl+F / F3 / 直接输入）

判据：抓帧差。跳转成功画面变化必然远超空转噪声（实测空转 < 0.1%）。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402
from src.capture import grab_window  # noqa: E402
from src.linkage import (force_foreground, send_text, send_vk,  # noqa: E402
                         VK_RETURN, VK_ESCAPE)

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.restype = wt.BOOL

WM_CHAR = 0x0102
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SETFOCUS = 0x0007
VK_RETURN_L = 0x0D

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "out", "em")

EM_EXES = ("mainfree.exe", "maintrade.exe", "stockway.exe")


def find_em_window():
    """找东财面积最大的可见窗口（find_windows 只认聊天类识别源，不能用）"""
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        path = _exe(pid.value)
        if os.path.basename(path).lower() not in EM_EXES:
            return True
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w >= 600 and h >= 400:
            hits.append((hwnd, w * h, (r.left, r.top, w, h)))
        return True

    user32.EnumWindows(cb, 0)
    return max(hits, key=lambda x: x[1]) if hits else None


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def _exe(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)
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


def diff_ratio(a, b):
    """降采样后差异像素占比（稳定、抗抖动）"""
    if a is None or b is None:
        return -1.0
    if a.shape != b.shape:
        return 1.0
    s = 8
    ha, wa = a.shape[0] // s, a.shape[1] // s
    aa = a[:ha * s, :wa * s].reshape(ha, s, wa, s, 3).mean(axis=(1, 3))
    bb = b[:ha * s, :wa * s].reshape(ha, s, wa, s, 3).mean(axis=(1, 3))
    d = np.abs(aa - bb).sum(axis=2)
    return float((d > 24).sum()) / d.size


def main():
    os.makedirs(OUT, exist_ok=True)
    from src.capture import window_info
    hit = find_em_window()
    if not hit:
        print("东方财富未运行")
        return 1
    hwnd_rect = window_info(hit[0])

    hwnd = hwnd_rect.hwnd
    print("=" * 84)
    print("东财主窗口 0x%08X  %s  %dx%d" % (hwnd, hwnd_rect.title[:40],
                                            hwnd_rect.width, hwnd_rect.height))
    print("=" * 84)

    base = grab_window(hwnd_rect)
    if base is None:
        print("抓帧失败")
        return 1
    Image.fromarray(base).save(os.path.join(OUT, "00_initial.png"))
    print("  初始帧已存（%dx%d）" % (base.shape[1], base.shape[0]))

    # ---------- 空转基线 ----------
    time.sleep(2.5)
    idle = grab_window(hwnd_rect)
    print("\n[基线] 空转 2.5s 差异 = %.4f%%  ← 跳转必须显著大于这个值"
          % (diff_ratio(base, idle) * 100))

    def shot(tag):
        a = grab_window(hwnd_rect)
        Image.fromarray(a).save(os.path.join(OUT, "%s.png" % tag))
        return a

    results = []

    # ---------- 方案 A：PostMessage WM_CHAR 到主窗口 ----------
    print("\n[A] PostMessage WM_CHAR 到主窗口 (0x%08X)" % hwnd)
    for ch in "600519":
        user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 0)
        time.sleep(0.05)
    time.sleep(0.15)
    user32.PostMessageW(hwnd, WM_CHAR, VK_RETURN_L, 0)
    time.sleep(2.2)
    a = shot("A_wmchar")
    r = diff_ratio(base, a)
    results.append(("A PostMessage WM_CHAR（主窗口）", r))
    print("    差异 = %.3f%%  %s" % (r * 100, "可能成功" if r > 0.02 else "无效"))

    # ---------- 方案 B：PostMessage WM_KEYDOWN ----------
    print("\n[B] PostMessage WM_KEYDOWN 到主窗口")
    for ch in "600519":
        vk = ord(ch)
        user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
        user32.PostMessageW(hwnd, WM_KEYUP, vk, 0)
        time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN_L, 0)
    user32.PostMessageW(hwnd, WM_KEYUP, VK_RETURN_L, 0)
    time.sleep(2.2)
    a = shot("B_wmkeydown")
    r = diff_ratio(a, base)
    results.append(("B PostMessage WM_KEYDOWN（主窗口）", r))
    print("    差异 = %.3f%%  %s" % (r * 100, "可能成功" if r > 0.02 else "无效"))

    # ---------- 方案 C：键盘精灵（抢前台 + SendInput） ----------
    print("\n[C] SendInput 键盘精灵（抢前台）")
    if force_foreground(hwnd):
        time.sleep(0.4)
        send_vk(VK_ESCAPE)
        time.sleep(0.2)
        send_text("600036")          # 招商银行，换个票避免和上面混淆
        time.sleep(0.2)
        send_vk(VK_RETURN)
        time.sleep(2.5)
        a = shot("C_sendinput")
        r = diff_ratio(a, base)
        results.append(("C SendInput 键盘精灵（抢前台）", r))
        print("    差异 = %.3f%%  %s" % (r * 100, "可能成功" if r > 0.02 else "无效"))
    else:
        print("    抢前台失败")
        results.append(("C SendInput 键盘精灵", -1))

    # ---------- 方案 D：PostMessage 到子窗口（MDIClient / View） ----------
    print("\n[D] PostMessage WM_CHAR 到 MDIClient / View 子窗口")
    kids = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(ch, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(ch, buf, 256)
        if buf.value in ("MDIClient", "AfxFrameOrView140u", "ContainerWnd_DC"):
            kids.append(ch)
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    print("    候选子窗口: %s" % ["0x%08X" % k for k in kids])
    for k in kids[:3]:
        for ch in "000651":
            user32.PostMessageW(k, WM_CHAR, ord(ch), 0)
            time.sleep(0.05)
        user32.PostMessageW(k, WM_CHAR, VK_RETURN_L, 0)
        time.sleep(1.8)
    a = shot("D_child")
    r = diff_ratio(a, base)
    results.append(("D PostMessage WM_CHAR（子窗口）", r))
    print("    差异 = %.3f%%  %s" % (r * 100, "可能成功" if r > 0.02 else "无效"))

    print("\n" + "=" * 84)
    print("结果汇总（判据：> 0.02% 才算有反应）")
    print("=" * 84)
    for name, r in results:
        flag = "★ 有反应" if r > 0.02 else ("— 无效" if r >= 0 else "? 未执行")
        print("  %-42s %7.3f%%  %s" % (name, r * 100 if r >= 0 else 0, flag))
    print("\n出图目录: out/em/  （对比 00_initial 与各方案截图即可肉眼确认）")
    return 0


if __name__ == "__main__":
    user32.EnumChildWindows.argtypes = [
        wt.HWND, ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
    user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    sys.exit(main())
