# -*- coding: utf-8 -*-
"""p29 东财跳转方案 v2

p28 的两个错误：
  1. 判据失效 —— 东财底部有滚动资讯栏，全局帧差基线就有 1.0%，
     四个方案都在 1.1% 附近，完全分辨不出成败。
     → 改成分区差异：只看「主图区」（跳转后整块换），不看底部资讯区。
  2. 没按东财的正确招式发键 —— 搜到的用法是：
     * Ctrl+K  激活键盘精灵（部分版本）
     * Ctrl+F  打开全局搜索框
     * 直接输入代码 + Enter
     直接敲数字 + 回车是通达信的招式，东财未必认。

窗口事实（p27/深度 5 复探）：
    主窗口 Afx:000F0000:8:00010003:0000  2560x1032
      └ MDIClient → AfxFrameOrView140u / ContainerWnd_DC
    深度 5 内 0 个 Chrome_WidgetWin → 纯 MFC+DuiLib 自绘，无 CEF 兜底问题
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402
from src.capture import grab_window, window_info  # noqa: E402
from src.linkage import force_foreground, send_text, send_vk, _send_inputs  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM),
                              wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

VK_CONTROL, VK_F, VK_K, VK_ESCAPE, VK_RETURN = 0x11, 0x46, 0x4B, 0x1B, 0x0D
KEYEVENTF_KEYUP = 0x0002
EM_EXES = ("mainfree.exe", "maintrade.exe", "stockway.exe")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "out", "em")


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


def find_em():
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if os.path.basename(_exe(pid.value)).lower() not in EM_EXES:
            return True
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w >= 800 and h >= 500:
            hits.append((hwnd, w * h))
        return True

    user32.EnumWindows(cb, 0)
    return max(hits, key=lambda x: x[1])[0] if hits else None


def send_hotkey(vk, mod=VK_CONTROL):
    _send_inputs([(mod, 0, 0), (vk, 0, 0),
                  (vk, 0, KEYEVENTF_KEYUP), (mod, 0, KEYEVENTF_KEYUP)])
    time.sleep(0.15)


def zone_diff(a, b, zones):
    """分区差异：zones 里每项是 (名称, x0,y0,x1,y1) 归一化坐标"""
    out = []
    H, W = a.shape[:2]
    for name, x0, y0, x1, y1 in zones:
        ax0, ay0 = int(W * x0), int(H * y0)
        ax1, ay1 = int(W * x1), int(H * y1)
        sa = a[ay0:ay1, ax0:ax1].astype(np.int16)
        sb = b[ay0:ay1, ax0:ax1].astype(np.int16)
        if sa.size == 0:
            out.append((name, 0.0))
            continue
        d = np.abs(sa - sb).sum(axis=2)
        out.append((name, float((d > 32).sum()) / d.size * 100))
    return out


ZONES = [
    ("顶部工具栏", 0.00, 0.00, 1.00, 0.09),
    ("主图左上(股名)", 0.00, 0.09, 0.30, 0.22),
    ("主图区", 0.00, 0.22, 0.75, 0.70),
    ("右侧盘口", 0.75, 0.09, 1.00, 0.70),
    ("底部资讯", 0.00, 0.88, 1.00, 1.00),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    hwnd = find_em()
    if not hwnd:
        print("东方财富未运行")
        return 1
    info = window_info(hwnd)
    print("=" * 84)
    print("东财主窗口 0x%08X  %dx%d @(%d,%d)"
          % (hwnd, info.width, info.height, info.client[0], info.client[1]))
    print("=" * 84)

    def shot(tag):
        a = grab_window(info)
        if a is not None:
            Image.fromarray(a).save(os.path.join(OUT, "%s.png" % tag))
        return a

    base = shot("v2_00_initial")
    if base is None:
        print("抓帧失败")
        return 1
    print("  初始帧 %dx%d" % (base.shape[1], base.shape[0]))

    # ---------- 基线：连抓两帧，看各区噪声水平 ----------
    time.sleep(3.0)
    idle = shot("v2_01_idle")
    print("\n[基线] 空转 3s 各区差异（%）：")
    for n, v in zone_diff(base, idle, ZONES):
        print("    %-16s %6.3f%%" % (n, v))

    # ---------- 抢前台 ----------
    if not force_foreground(hwnd):
        print("\n⚠ 抢前台失败，后续 SendInput 序列可能无效")
    else:
        print("\n✓ 已抢到前台")
    time.sleep(0.5)

    def trial(tag, code, pre=None, label=""):
        """pre: 激活键盘精灵的按键序列函数"""
        send_vk(VK_ESCAPE)
        time.sleep(0.25)
        if pre:
            pre()
            time.sleep(0.35)
        send_text(code)
        time.sleep(0.25)
        send_vk(VK_RETURN)
        time.sleep(2.6)
        a = shot(tag)
        z = zone_diff(base, a, ZONES)
        hot = max(z, key=lambda x: x[1])
        print("    %-26s 主图区=%6.2f%%  最热区=%-12s(%5.2f%%)"
              % (label, dict(z)["主图区"], hot[0], hot[1]))
        return dict(z)

    print("\n[E] Ctrl+F → 输入 → Enter  （东财全局搜索）")
    zE = trial("v2_E_ctrlf", "600519", pre=lambda: send_hotkey(VK_F),
               label="Ctrl+F + 600519 + Enter")

    print("\n[F] Ctrl+K → 输入 → Enter  （东财键盘精灵）")
    zF = trial("v2_F_ctrlk", "600036", pre=lambda: send_hotkey(VK_K),
               label="Ctrl+K + 600036 + Enter")

    print("\n[G] 直接输入代码 + Enter  （通达信式）")
    zG = trial("v2_G_direct", "000858", pre=None,
               label="直接 000858 + Enter")

    print("\n[G2] 直接输入 + 连续两个 Enter")
    send_vk(VK_ESCAPE)
    time.sleep(0.25)
    send_text("601318")
    time.sleep(0.25)
    send_vk(VK_RETURN)
    time.sleep(0.6)
    send_vk(VK_RETURN)
    time.sleep(2.4)
    a = shot("v2_G2_double")
    zG2 = dict(zone_diff(base, a, ZONES))
    print("    直接 601318 + Enter×2      主图区=%6.2f%%" % zG2["主图区"])

    print("\n" + "=" * 84)
    print("汇总（判据：主图区差异应 > 15%%，因为整块换股）")
    print("=" * 84)
    print("  %-28s %-10s %-10s %s" % ("方案", "主图区", "顶部栏", "判定"))
    for nm, z in (("E Ctrl+F", zE), ("F Ctrl+K", zF),
                  ("G 直接输入", zG), ("G2 输入+双回车", zG2)):
        v = z.get("主图区", 0)
        print("  %-28s %8.2f%% %8.2f%%  %s"
              % (nm, v, z.get("顶部工具栏", 0),
                 "★★ 成功" if v > 15 else ("? 有反应" if v > 5 else "无变化")))
    print("\n出图: out/em/v2_*.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
