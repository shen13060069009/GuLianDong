# -*- coding: utf-8 -*-
"""精确抓「关于 · 股联动 GuLianDong」窗口（按窗口句柄截图，不靠猜坐标）。"""
import os
import subprocess
import time

ROOT = r'C:\Users\Administrator\WorkBuddy\2026-10-06-13-12-12\stocklens'
EXE = os.path.join(ROOT, 'dist', 'GuLianDong', 'GuLianDong.exe')
OUT = os.path.join(ROOT, 'out', 'about_dialog_crop.png')


def find(title_sub):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hits = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if title_sub in buf.value:
            hits.append((hwnd, buf.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return hits


env = dict(os.environ, GLD_TEST_LIC='1')
p = subprocess.Popen([EXE, '--gui-test', '12'], env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(6)
hits = find('关于 · 股联动')
print('found', hits)
if hits:
    import win32gui
    from PIL import Image
    hwnd = hits[0][0]
    try:
        # 先置前再抓，保证不是被遮挡的残帧
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.8)
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w, h = r - l, b - t
    import win32ui
    from ctypes import windll
    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfcDC, w, h)
    saveDC.SelectObject(bmp)
    windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 2)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    im = Image.frombuffer('RGB', (info['bmWidth'], info['bmHeight']),
                          bits, 'raw', 'BGRX', 0, 1)
    win32gui.DeleteObject(bmp.GetHandle())
    saveDC.DeleteDC()
    mfcDC.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwndDC)
    im.save(OUT)
    print('saved', OUT, im.size)
else:
    print('未找到关于窗口')
try:
    p.wait(timeout=25)
except Exception:
    p.kill()