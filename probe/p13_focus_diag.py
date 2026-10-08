# -*- coding: utf-8 -*-
"""P0-13 通达信焦点诊断

P0-12 显示：抢前台「成功」但跳转没生效。怀疑按键落在了错误的子窗口上
（通达信主框架 / MDI 子窗口 / 内嵌 CEF 网页视图，三者行为完全不同）。

本探针：
  1. 激活后，用 GetGUIThreadInfo 查该进程线程真正的 hwndFocus 是谁
  2. 只输入代码不回车，全屏截图看「键盘精灵」搜索框有没有弹出来
  3. 再回车，再截一张，对比
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import linkage

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wt.HWND
user32.GetFocus.restype = wt.HWND


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("flags", wt.DWORD),
        ("hwndActive", wt.HWND), ("hwndFocus", wt.HWND),
        ("hwndCapture", wt.HWND), ("hwndMenuOwner", wt.HWND),
        ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND),
        ("rcCaret", wt.RECT),
    ]


user32.GetGUIThreadInfo.argtypes = [wt.DWORD, ctypes.POINTER(GUITHREADINFO)]


def win_info(hwnd, depth=0):
    if not hwnd:
        return "  (空)"
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    t = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, t, 256)
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    pid = wt.DWORD()
    tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return ("hwnd=0x%08X cls=%-28s rect=%dx%d@%d,%d tid=%d pid=%d title=%s"
            % (hwnd, cls.value[:27], r.right - r.left, r.bottom - r.top,
               r.left, r.top, tid, pid.value, t.value[:30]))


def ocr_screen(ocr, label, out_dir):
    import mss
    import numpy as np
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[0])
        img = np.asarray(shot)[:, :, :3][:, :, ::-1]
    path = os.path.join(out_dir, "screen_%s.png" % label)
    from PIL import Image
    Image.fromarray(img, "RGB").save(path)
    boxes = ocr.recognize(img)
    texts = [b.text for b in boxes]
    print("    [%s] 文本块 %d，含'600519'=%s，含'茅台'=%s，含'键盘'=%s"
          % (label, len(texts), "600519" in " ".join(texts),
             "茅台" in " ".join(texts), "键盘" in " ".join(texts)))
    # 找疑似键盘精灵弹窗
    interesting = [b for b in boxes if "600519" in b.text or "茅台" in b.text
                   or "代码" in b.text or "拼音" in b.text]
    for b in interesting[:6]:
        print("         %s  @(%d,%d)-(%d,%d)" % (b.text[:40], b.x0, b.y0, b.x1, b.y1))
    return path


def main():
    print("=" * 100)
    print("P0-13 通达信焦点诊断")
    print("=" * 100)

    hit = linkage.find_process_window("TdxW.exe")
    if not hit:
        print("  通达信未运行")
        return
    hwnd, area, rect, title = hit
    rest = user32.GetForegroundWindow()
    out_dir = os.path.join(ROOT, "out")
    os.makedirs(out_dir, exist_ok=True)

    from src.ocr import StockOCR
    ocr = StockOCR(verbose=True)

    pid = wt.DWORD()
    tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    print("\n  主窗口: %s" % win_info(hwnd))

    # ---- 激活 ----
    print("\n[1] 抢前台")
    ok = linkage.force_foreground(hwnd)
    time.sleep(0.4)
    fg = user32.GetForegroundWindow()
    print("     force_foreground=%s   当前前台=0x%08X %s"
          % (ok, fg or 0, "✓ 一致" if fg == hwnd else "✗ 不一致"))

    # ---- 查焦点 ----
    print("\n[2] 线程内焦点（GetGUIThreadInfo）")
    gti = GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
        print("     hwndActive : %s" % win_info(gti.hwndActive))
        print("     hwndFocus  : %s" % win_info(gti.hwndFocus))
        print("     hwndCaret  : %s" % win_info(gti.hwndCaret))
        focus = gti.hwndFocus
    else:
        print("     GetGUIThreadInfo 失败 err=%d" % ctypes.get_last_error())
        focus = None

    # ---- 往上溯父链，看焦点落在哪个层级 ----
    if focus:
        print("\n[3] 焦点窗口的父链")
        cur, depth = focus, 0
        while cur and depth < 8:
            print("     %s%s" % ("  " * depth, win_info(cur)))
            cur = user32.GetParent(cur)
            depth += 1

    # ---- 尝试给行情视图设焦点 ----
    print("\n[4] 尝试把焦点设到子视图")
    import win32gui
    children = []
    win32gui.EnumChildWindows(hwnd, lambda h, _: children.append(h) or True, None)
    print("     子窗口共 %d 个" % len(children))

    # ---- 只输代码不回车，看键盘精灵是否弹出 ----
    print("\n[5] 注入 '600519'（不回车），观察是否弹出键盘精灵")
    linkage.send_vk(linkage.VK_ESCAPE)
    time.sleep(0.2)
    linkage.send_text("600519")
    time.sleep(0.6)
    p1 = ocr_screen(ocr, "typed", out_dir)

    print("\n[6] 注入回车")
    linkage.send_vk(linkage.VK_RETURN)
    time.sleep(1.8)
    p2 = ocr_screen(ocr, "after_enter", out_dir)

    print("\n  截图: %s" % p1)
    print("        %s" % p2)

    print("\n[7] 还原前台")
    if rest:
        linkage.force_foreground(rest)


if __name__ == "__main__":
    main()
