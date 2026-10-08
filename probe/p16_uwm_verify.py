# -*- coding: utf-8 -*-
"""P0-16 UWM_STOCK 严谨复验

P0-15 测出 600519 让通达信画面变了 88.81%，但 OCR 没读到股票名。
排查发现是验证脚本自身的缺陷：通达信窗口宽 2564px，而 OCR 用了
det_limit_side_len=640（= 缩到 1/4），小字全糊，自然读不出。

本探针修正验证方法：
  1. 先做「空转」基线：不发消息、只等待，测出画面自身的噪声水平
  2. 再注入 UWM_STOCK，与基线对比，排除渲染抖动干扰
  3. OCR 用足够的分辨率（det_limit=2000），确保能读出 12px 级小字
  4. 保存全部截图供人工复核
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
from src.ocr import StockOCR

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]

HWND_BROADCAST = 0xFFFF
MARKET_SH, MARKET_SZ = 7, 6
OUT = os.path.join(ROOT, "out")


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
    return float((np.abs(x - y) > 25).mean()) if x.shape == y.shape else 1.0


def main():
    hit = linkage.find_process_window("TdxW.exe")
    if not hit:
        print("通达信未运行")
        return
    hwnd, area, rect, title = hit
    msg = user32.RegisterWindowMessageW("Stock")

    print("=" * 104)
    print("P0-16 UWM_STOCK 严谨复验")
    print("=" * 104)
    print("  通达信 0x%08X  %s" % (hwnd, title[:60]))
    print("  UWM_STOCK = 0x%04X" % msg)

    # 高分辨率 OCR（避免把 2564px 的窗口缩成 640px 导致小字消失）
    print("\n  初始化高分辨率 OCR（det_limit=2000）...")
    ocr = StockOCR(det_limit=2000, rec_batch=16, verbose=True)

    print("\n[1] 空转基线：不改动任何东西，仅等待 2 秒")
    a = grab(hwnd, rect)
    time.sleep(2.0)
    b = grab(hwnd, rect)
    noise = diff(a, b)
    a.save(os.path.join(OUT, "tdx_p16_base_a.png"))
    b.save(os.path.join(OUT, "tdx_p16_base_b.png"))
    print("     自身噪声差异 = %.3f%%   ← 低于此值的都算「无变化」" % (noise * 100))

    print("\n[2] OCR 基线画面（用于确认能读出小字）")
    t0 = time.perf_counter()
    boxes_b = ocr.recognize(np.asarray(b))
    tb = [x.text for x in boxes_b]
    print("     文本块 %d，耗时 %.0f ms" % (len(tb), (time.perf_counter() - t0) * 1000))
    print("     前 20 条: %s" % " | ".join(tb[:20]))

    # ---------------- 逐个注入 ----------------
    tests = [
        ("600519", MARKET_SH, "贵州茅台", "沪市"),
        ("000001", MARKET_SZ, "平安银行", "深市"),
    ]

    print("\n[3] 注入验证")
    print("  %-9s %-6s %-10s %-10s %-9s %-9s %s"
          % ("代码", "市场", "消息参数", "差异", "含名称", "含代码", "判定"))
    print("  " + "-" * 92)

    for code, market, name, mlabel in tests:
        before = grab(hwnd, rect)
        param = market * 1000000 + int(code)
        user32.PostMessageW(HWND_BROADCAST, msg, param, 0)
        user32.PostMessageW(hwnd, msg, param, 0)
        time.sleep(2.2)

        after = grab(hwnd, rect)
        after.save(os.path.join(OUT, "tdx_p16_%s.png" % code))
        d = diff(before, after)

        boxes = ocr.recognize(np.asarray(after))
        texts = [x.text for x in boxes]
        joined = " ".join(texts)
        got_name = name in joined
        got_partial = name[:2] in joined
        got_code = code in joined

        verdict = ("✓ 成功" if got_name
                   else ("✓ 部分命中" if got_partial or got_code
                         else ("? 画面变化但未读到名称" if d > max(noise * 3, 0.01) else "✗ 无变化")))
        print("  %-9s %-6s %-10d %-10s %-9s %-9s %s"
              % (code, mlabel, param, "%.2f%%" % (d * 100),
                 got_name or got_partial, got_code, verdict))
        rel = [t for t in texts if name[:2] in t or name[-2:] in t or code in t]
        if rel:
            print("           命中文本: %s" % " | ".join(rel[:8]))
        else:
            sample = [t for t in texts if len(t) >= 4][:12]
            print("           画面文本样例: %s" % " | ".join(sample))

    print("\n  截图已存至 %s" % OUT)


if __name__ == "__main__":
    main()
