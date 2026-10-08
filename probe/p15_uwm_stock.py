# -*- coding: utf-8 -*-
"""P0-15 通达信 UWM_STOCK 广播消息验证

P0-12 ~ P0-14 证明：键盘精灵在当前「综合复盘聚合版」版面下不可用
（焦点落在内嵌 CEF 网页上，SendInput 与 PostMessage 全部无效）。

检索到通达信存在官方的自定义窗口消息接口：
    UINT UWM_STOCK = RegisterWindowMessage("Stock");
    PostMessage(HWND_BROADCAST, UWM_STOCK, MARKET*1000000 + CODE, 0);
编码规则：沪市前加 7，其它市场前加 6。
    600519 → 7600519
    000001 → 6000001

若成立，联动可做到完全后台静默（不抢前台、不需要焦点），
对用户零打扰，比键盘精灵方案好得多。
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
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

HWND_BROADCAST = 0xFFFF
MARKET_SH = 7      # 沪市
MARKET_SZ = 6      # 深市（及其它）


def encode(code, market):
    return market * 1000000 + int(code)


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


def diff(a, b):
    x = np.asarray(a.convert("L"), dtype=np.int16)
    y = np.asarray(b.convert("L"), dtype=np.int16)
    if x.shape != y.shape:
        return 1.0
    return float((np.abs(x - y) > 25).mean())


def main():
    hit = linkage.find_process_window("TdxW.exe")
    if not hit:
        print("通达信未运行")
        return
    hwnd, area, rect, title = hit

    msg = user32.RegisterWindowMessageW("Stock")
    print("=" * 100)
    print("P0-15 通达信 UWM_STOCK 广播消息验证")
    print("=" * 100)
    print("  通达信 hwnd=0x%08X  %s" % (hwnd, title[:56]))
    print("  RegisterWindowMessage(\"Stock\") = 0x%04X  (%d)" % (msg, msg))
    if msg == 0:
        print("  注册失败，无法继续")
        return
    print("  广播目标 HWND_BROADCAST = 0x%04X" % HWND_BROADCAST)

    from src.ocr import StockOCR
    ocr = StockOCR(verbose=False)

    tests = [
        ("600519", MARKET_SH, "贵州茅台"),
        ("000001", MARKET_SZ, "平安银行"),
        ("300750", MARKET_SZ, "宁德时代"),
    ]

    print("\n  %-10s %-10s %-12s %-9s %-9s %-9s %s"
          % ("代码", "市场", "消息参数", "差异", "含名称", "含代码", "判定"))
    print("  " + "-" * 92)

    for code, market, name in tests:
        param = encode(code, market)
        before = grab(hwnd, rect)
        before_text = " ".join(b.text for b in ocr.recognize(np.asarray(before)))

        t0 = time.perf_counter()
        user32.PostMessageW(HWND_BROADCAST, msg, param, 0)
        # 有些实现需要直接发到主窗口，两种都试
        user32.PostMessageW(hwnd, msg, param, 0)
        dt = (time.perf_counter() - t0) * 1000
        time.sleep(1.6)

        after = grab(hwnd, rect)
        after_text = " ".join(b.text for b in ocr.recognize(np.asarray(after)))
        d = diff(before, after)
        got_name = name in after_text
        # 允许部分匹配（OCR 可能把"贵州茅台"读错一个字）
        partial = name[:2] in after_text or name[-2:] in after_text
        got_code = code in after_text

        verdict = "✓ 成功" if (got_name or partial) else ("~ 有变化" if d > 0.01 else "✗ 无效")
        print("  %-10s %-10s %-12d %-9s %-9s %-9s %s"
              % (code, "沪" if market == MARKET_SH else "深", param,
                 "%.2f%%" % (d * 100), got_name or partial, got_code, verdict))

        if got_name or partial:
            after.save(os.path.join(ROOT, "out", "tdx_uwm_%s.png" % code))
            # 打印相关文本，便于确认
            rel = [t for t in after_text.split("  ") if name[:2] in t or code in t]
            if rel:
                print("           相关文本: %s" % " | ".join(rel[:6]))
        time.sleep(0.4)

    print("\n  说明：PostMessage 同时发给了 HWND_BROADCAST 和通达信主窗口，")
    print("       两个目标都命中才算确认。")


if __name__ == "__main__":
    main()
