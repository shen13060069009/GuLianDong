# -*- coding: utf-8 -*-
"""P0-14 联动注入策略对比

P0-13 证明：通达信的「综合复盘聚合版」把键盘焦点交给了内嵌 CEF 网页视图，
SendInput 的按键被网页吃掉，键盘精灵没反应。

本探针对比 4 种注入策略，找出真正生效的那条：

  A. SendInput（已证明失败）           — 需要正确焦点，会抢前台
  B. PostMessage WM_CHAR → 主框架      — 不抢焦点，不打断用户打字
  C. PostMessage WM_CHAR → MDIClient   — 同上
  D. PostMessage WM_KEYDOWN 序列       — 部分程序只认 KEYDOWN 不认 CHAR

评测标准：注入后抓窗口截图，与注入前对比差异像素占比，
并用 OCR 确认画面里是否出现目标股票。策略 B/C/D 若成立，
产品就能做到「点击高亮 → 后台静默跳转，不打断用户」。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
from PIL import Image

from src import linkage

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
VK_RETURN = 0x0D

CODE = "600519"
TARGET_NAME = "茅台"


def grab(hwnd, rect):
    import win32gui
    import win32ui
    l, t, r, b = rect
    w, h = r - l, b - t
    hdc = win32gui.GetWindowDC(hwnd)
    mfc = win32ui.CreateDCFromHandle(hdc)
    dc = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc, w, h)
    dc.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, dc.GetSafeHdc(), 2)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    arr = np.frombuffer(bits, dtype=np.uint8).reshape((info["bmHeight"], info["bmWidth"], 4))
    win32gui.DeleteObject(bmp.GetHandle())
    dc.DeleteDC()
    mfc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hdc)
    return Image.fromarray(arr[:, :, :3][:, :, ::-1], "RGB")


def diff_ratio(a, b):
    x = np.asarray(a.convert("L"), dtype=np.int16)
    y = np.asarray(b.convert("L"), dtype=np.int16)
    if x.shape != y.shape:
        return 1.0
    return float((np.abs(x - y) > 25).mean())


def post_digits_char(hwnd, code, enter=True):
    for ch in code:
        user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 0)
        time.sleep(0.02)
    if enter:
        user32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, 0)
        user32.PostMessageW(hwnd, WM_KEYUP, VK_RETURN, 0)


def post_digits_keydown(hwnd, code, enter=True):
    for ch in code:
        vk = ord(ch)  # 数字 0-9 的 VK 码与 ASCII 一致
        user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
        user32.PostMessageW(hwnd, WM_KEYUP, vk, 0)
        time.sleep(0.02)
    if enter:
        user32.PostMessageW(hwnd, WM_KEYDOWN, VK_RETURN, 0)
        user32.PostMessageW(hwnd, WM_KEYUP, VK_RETURN, 0)


def main():
    hit = linkage.find_process_window("TdxW.exe")
    if not hit:
        print("通达信未运行")
        return
    hwnd_main, area, rect, title = hit

    print("=" * 104)
    print("P0-14 联动注入策略对比")
    print("=" * 104)
    print("  通达信 hwnd=0x%08X  %s" % (hwnd_main, title[:60]))

    # 找出候选接收窗口
    import win32gui
    kids = []
    win32gui.EnumChildWindows(hwnd_main, lambda h, _: kids.append(h) or True, None)
    by_class = {}
    for h in kids:
        c = win32gui.GetClassName(h)
        by_class.setdefault(c, []).append(h)

    candidates = []
    for cls in ("MDIClient", "Afx:00007FF72F240000:b:00000000"):
        for h in by_class.get(cls, [])[:1]:
            candidates.append((cls, h))
    # 加上主框架
    candidates.insert(0, ("TdxW_MainFrame", hwnd_main))

    print("\n  候选接收窗口：")
    for name, h in candidates:
        r = win32gui.GetWindowRect(h)
        print("    %-30s hwnd=0x%08X  %dx%d" % (name, h, r[2] - r[0], r[3] - r[1]))

    from src.ocr import StockOCR
    ocr = StockOCR(verbose=False)

    def probe(label, fn, hwnd_target):
        before = grab(hwnd_main, rect)
        before_texts = " ".join(b.text for b in ocr.recognize(np.asarray(before)))
        try:
            fn(hwnd_target)
        except Exception as e:
            print("  %-34s 调用失败: %s" % (label, e))
            return
        time.sleep(2.0)
        after = grab(hwnd_main, rect)
        after_texts = " ".join(b.text for b in ocr.recognize(np.asarray(after)))
        d = diff_ratio(before, after)
        got_code = CODE in after_texts
        got_name = TARGET_NAME in after_texts
        verdict = "✓ 生效" if (got_code or got_name) else ("~ 有变化" if d > 0.005 else "✗ 无效")
        print("  %-34s 差异 %6.2f%%  含代码=%-5s 含%s=%-5s  %s"
              % (label, d * 100, got_code, TARGET_NAME, got_name, verdict))
        if got_code or got_name:
            after.save(os.path.join(ROOT, "out", "tdx_success_%s.png"
                                    % label.replace(" ", "_").replace("→", "-")))
        # 还原：按 ESC
        linkage.send_vk(linkage.VK_ESCAPE)
        time.sleep(0.3)

    print("\n  测试结果：")
    for name, h in candidates:
        probe("B. WM_CHAR → %s" % name,
              lambda t: post_digits_char(t, CODE), h)
        probe("D. WM_KEYDOWN → %s" % name,
              lambda t: post_digits_keydown(t, CODE), h)

    print("\n  说明：%s" % ROOT)
    print("  注：若全部无效，说明该版面的键盘精灵不接受消息级注入，")
    print("      需要改用「切换标准行情版面」或「网页行情兜底」方案。")


if __name__ == "__main__":
    main()
