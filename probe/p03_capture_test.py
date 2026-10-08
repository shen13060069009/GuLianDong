# -*- coding: utf-8 -*-
"""P0-3 取屏能力验证（决定项目生死的探针）

对不同类型的窗口分别尝试三种取屏方式：
  A. BitBlt      —— 从屏幕 DC 直接抓，最快，但被遮挡会抓到遮挡物
  B. PrintWindow(0)              —— 传统方式，GPU 渲染窗口常返回黑屏
  C. PrintWindow(PW_RENDERFULLCONTENT=2) —— Win8.1+ 支持 DirectComposition，理论上能抓 GPU 窗口

判定指标：
  black_ratio  全黑像素占比（>0.95 判定为黑屏失败）
  mean_luma    平均亮度（>3 说明有内容）
  uniq_colors  采样唯一色数（>200 说明是真实画面而非纯色块）

这是「文字采集层」能不能走通的唯一证据。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

user32 = ctypes.WinDLL("user32", use_last_error=True)

# 让本进程感知每显示器 DPI，否则 GetWindowRect 返回的是逻辑像素，和截图像素对不上
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

import numpy as np
import win32gui
import win32ui
from PIL import Image

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "captures")
os.makedirs(OUT, exist_ok=True)

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

PW_RENDERFULLCONTENT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 取样目标：覆盖「Electron / Chromium」「Win32 自绘」「Qt」三类渲染技术
WANT = [
    ("Feishu.exe", "飞书 (Electron)"),
    ("实时消息中心.exe", "实时消息中心 (Electron)"),
    ("msedge.exe", "Edge (Chromium)"),
    ("TdxW.exe", "通达信 (Win32 自绘)"),
    ("QQ.exe", "QQ (Electron)"),
]


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


def find_windows():
    """返回 {exe_basename: [(hwnd, area, rect, title), ...]} 只含可见且够大的窗口"""
    res = {}

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = get_exe(pid.value)
        base = os.path.basename(exe) if exe else ""
        if not base:
            return True
        try:
            l, t, r, b = win32gui.GetWindowRect(hwnd)
        except Exception:
            return True
        w, h = r - l, b - t
        if w < 200 or h < 200:
            return True
        res.setdefault(base, []).append((hwnd, w * h, (l, t, r, b), win32gui.GetWindowText(hwnd)))
        return True

    user32.EnumWindows(cb, 0)
    return res


def cap_bitblt(hwnd, rect):
    import mss
    l, t, r, b = rect
    w, h = r - l, b - t
    with mss.mss() as sct:
        raw = sct.grab({"left": l, "top": t, "width": w, "height": h})
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def cap_printwindow(hwnd, rect, flag):
    l, t, r, b = rect
    w, h = r - l, b - t
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), flag)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits, "raw", "BGRX", 0, 1)
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    return ok, img


def score(img):
    a = np.asarray(img.convert("RGB"), dtype=np.uint8)
    gray = a.mean(axis=2)
    black = float((gray < 12).mean())
    mean = float(gray.mean())
    step_y = max(1, a.shape[0] // 150)
    step_x = max(1, a.shape[1] // 150)
    samp = a[::step_y, ::step_x].reshape(-1, 3)
    uniq = int(len(np.unique(samp, axis=0)))
    return black, mean, uniq


def verdict(black, mean, uniq):
    if black > 0.95 and mean < 5:
        return "黑屏 ✗"
    if uniq < 30:
        return "纯色块 ✗"
    if uniq < 200:
        return "可疑 ?"
    return "可用 ✓"


def main():
    wins = find_windows()
    print("=" * 112)
    print("P0-3 取屏能力验证")
    print("=" * 112)

    rows = []
    for exe, label in WANT:
        cands = wins.get(exe)
        if not cands:
            print("\n[%s] 无可见大窗口，跳过" % label)
            continue
        hwnd, area, rect, title = max(cands, key=lambda x: x[1])
        l, t, r, b = rect
        print("\n[%s]" % label)
        print("  hwnd=0x%08X  rect=%dx%d@%d,%d  title=%s" % (hwnd, r - l, b - t, l, t, title[:50]))

        for tag, fn in (
            ("A.BitBlt", lambda: cap_bitblt(hwnd, rect)),
            ("B.PrintWindow(0)", lambda: cap_printwindow(hwnd, rect, 0)[1]),
            ("C.PrintWindow(2)", lambda: cap_printwindow(hwnd, rect, PW_RENDERFULLCONTENT)[1]),
        ):
            try:
                img = fn()
                if img is None or img.width < 10:
                    print("    %-16s 抓取失败" % tag)
                    continue
                black, mean, uniq = score(img)
                v = verdict(black, mean, uniq)
                fnout = os.path.join(OUT, "%s_%s.png" % (exe.replace(".exe", ""), tag.split(".")[0]))
                img.save(fnout)
                print("    %-16s black=%-6.3f luma=%-6.1f uniq=%-5d  %-10s -> %s"
                      % (tag, black, mean, uniq, v, os.path.basename(fnout)))
                rows.append((label, tag, black, mean, uniq, v))
            except Exception as e:
                print("    %-16s 异常: %s" % (tag, e))

    print()
    print("=" * 112)
    print("汇总（取屏方式的可用性矩阵）")
    print("=" * 112)
    print("  %-24s %-18s %-9s %-8s %-7s %s" % ("窗口", "方式", "黑屏率", "亮度", "色数", "判定"))
    for label, tag, black, mean, uniq, v in rows:
        print("  %-24s %-18s %-9.3f %-8.1f %-7d %s" % (label, tag, black, mean, uniq, v))

    print()
    print("截图已保存到: %s" % OUT)


if __name__ == "__main__":
    main()
