# -*- coding: utf-8 -*-
"""P0-12 联动真机验证

对正在运行的通达信执行一次真实的键盘精灵跳转，并用「跳转前/后截图 + OCR」
证明代码确实生效（而不是只看 SendInput 返回值）。

验证链路：
    找到窗口 → 抢前台 → ESC 清状态 → 注入 600519 → 回车 → 等行情加载
    → 截图 → OCR → 确认画面里出现目标股票
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image

from src import linkage
from src.ocr import StockOCR

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wt.HWND

CODE = "600519"
EXPECT_NAME = "贵州茅台"


def grab_window(hwnd, rect):
    """PrintWindow(PW_RENDERFULLCONTENT) —— P0-3 已证明这是唯一能抓 GPU 窗口的方式"""
    import numpy as np
    import win32gui
    import win32ui
    l, t, r, b = rect
    w, h = r - l, b - t
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    arr = np.frombuffer(bits, dtype=np.uint8).reshape((info["bmHeight"], info["bmWidth"], 4))
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    return Image.fromarray(arr[:, :, :3][:, :, ::-1], "RGB")


def main():
    print("=" * 96)
    print("P0-12 联动真机验证 —— 通达信键盘精灵")
    print("=" * 96)

    hit = linkage.find_process_window("TdxW.exe")
    if not hit:
        print("  通达信未运行，跳过")
        return
    hwnd, area, rect, title = hit
    rest = user32.GetForegroundWindow()
    print("  目标: hwnd=0x%08X  %dx%d  %s"
          % (hwnd, rect[2] - rect[0], rect[3] - rect[1], title[:50]))
    print("  测试前的前台窗口: 0x%08X" % (rest or 0))

    ocr = StockOCR(verbose=True)
    out_dir = os.path.join(ROOT, "out")
    os.makedirs(out_dir, exist_ok=True)

    # ---------- 跳转前 ----------
    print("\n[1] 抓取跳转前画面")
    t0 = time.perf_counter()
    before = grab_window(hwnd, rect)
    print("     抓帧 %.0f ms  %dx%d" % ((time.perf_counter() - t0) * 1000,
                                        before.width, before.height))
    before.save(os.path.join(out_dir, "tdx_before.png")) if False else None

    # ---------- 执行跳转 ----------
    print("\n[2] 执行跳转 → %s" % CODE)
    t0 = time.perf_counter()
    ok, detail = linkage.jump_to(CODE, target="tdx", settle=2.0)
    dt = (time.perf_counter() - t0) * 1000
    print("     %s   (%.0f ms)  %s" % ("成功" if ok else "失败", dt, detail))
    if not ok:
        print("     终止：无法激活目标窗口")
        return

    # ---------- 跳转后 ----------
    print("\n[3] 抓取跳转后画面")
    after = grab_window(hwnd, rect)
    after.save(os.path.join(out_dir, "tdx_after.png"))

    # 与 before 对比，确认画面确实变了
    import numpy as np
    a0 = np.asarray(before.convert("L"), dtype=np.int16)
    a1 = np.asarray(after.convert("L"), dtype=np.int16)
    if a0.shape == a1.shape:
        diff = float((np.abs(a0 - a1) > 25).mean())
        print("     画面差异像素占比: %.2f%%  %s"
              % (diff * 100, "→ 画面已变化" if diff > 0.002 else "→ 画面几乎没变，跳转可能未生效"))

    # ---------- OCR 验证 ----------
    print("\n[4] OCR 验证：画面里是否出现目标股票")
    for label, img in (("跳转前", before), ("跳转后", after)):
        t0 = time.perf_counter()
        boxes = ocr.recognize(np.asarray(img))
        texts = [b.text for b in boxes]
        joined = " ".join(texts)
        hit_code = CODE in joined
        hit_name = EXPECT_NAME in joined
        # 贵州茅台常被读成"贵州茅台"或含"茅台"
        hit_partial = "茅台" in joined
        print("     %s  OCR %.0f ms  文本块 %d" % (label, (time.perf_counter() - t0) * 1000, len(texts)))
        print("          含代码 %s: %s" % (CODE, "是" if hit_code else "否"))
        print("          含名称 %s: %s (含'茅台': %s)"
              % (EXPECT_NAME, "是" if hit_name else "否", "是" if hit_partial else "否"))
        interesting = [t for t in texts if ("茅台" in t or CODE in t or "白酒" in t)]
        if interesting:
            print("          相关文本: %s" % " | ".join(interesting[:8]))

    # ---------- 还原焦点 ----------
    print("\n[5] 还原前台窗口")
    if rest:
        done = linkage.force_foreground(rest)
        print("     %s" % ("已还原" if done else "还原失败（可手动点回）"))


if __name__ == "__main__":
    main()
