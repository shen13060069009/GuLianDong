# -*- coding: utf-8 -*-
"""p32 东财跳转：用 OCR 读股票名做判据，严谨复验

p31 的教训：像素比例判据在暗色行情界面上极不灵敏
    海泰新能(920595) vs 贵州茅台(600519) 两个完全不同的票，
    分时图区的差异像素只占 0.09% —— 因为那块区域 95% 是黑底。
    用它判定「跳没跳」会得出完全相反的结论。

改成读事实：OCR 窗口左上角的「代码 + 名称」。
    东财把这个信息画在客户区 y≈18px 处，裁 (0,0,560,64) 即可稳定读出。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capture import grab_window, window_info  # noqa: E402
from src.linkage import _send_inputs, force_foreground, send_vk  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

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

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_ESCAPE, VK_RETURN = 0x1B, 0x0D
EM_EXES = ("mainfree.exe", "maintrade.exe", "stockway.exe")


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


def type_text(text, delay=0.07):
    total = 0
    for ch in text:
        total += _send_inputs([(0, ord(ch), KEYEVENTF_UNICODE),
                               (0, ord(ch),
                                KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)])
        time.sleep(delay)
    return total


OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "out", "em")


def main():
    from PIL import Image
    from src.ocr import StockOCR
    os.makedirs(OUT, exist_ok=True)
    hwnd = find_em()
    if not hwnd:
        print("东方财富未运行")
        return 1
    info = window_info(hwnd)
    print("=" * 88)
    print("东财 0x%08X  %dx%d" % (hwnd, info.width, info.height))
    print("=" * 88)

    ocr = StockOCR(gpu="auto", det_limit=960, rec_batch=8, use_cls=False)
    dbg = [0]

    def symbol():
        """OCR 读「代码 + 名称」

        ⚠ 坑：东财把股票名画在客户区 y≈104px 处（在「分时图/5日/日线」那一行
        之下、公告滚动条之下），不是最顶上的菜单栏区域。
        一开始裁 (0,0,820,96) 只能读到菜单，把股票名整条切掉了，
        导致 3/3 全部误判为「跳转失败」。
        """
        arr = grab_window(info)
        if arr is None:
            return ""
        y0, y1 = 88, min(160, arr.shape[0])
        x0, x1 = 0, min(900, arr.shape[1])
        crop = arr[y0:y1, x0:x1]
        dbg[0] += 1
        Image.fromarray(crop).resize(((x1 - x0) * 2, (y1 - y0) * 2),
                                     Image.LANCZOS).save(
            os.path.join(OUT, "crop_title_%02d.png" % dbg[0]))
        boxes = ocr.recognize(crop)
        return " | ".join(b.text for b in boxes)

    def jump(code, use_escape=True, settle=2.6):
        if not force_foreground(hwnd):
            return None, "抢前台失败"
        time.sleep(0.5)
        if use_escape:
            send_vk(VK_ESCAPE)
            time.sleep(0.35)
        n = type_text(code)
        time.sleep(0.3)
        send_vk(VK_RETURN)
        time.sleep(settle)
        return n, symbol()

    print("\n当前画面标题区: %r" % symbol())

    tests = [
        ("600519", False, "贵州茅台"),
        ("000858", False, "五粮液"),
        ("601318", False, "中国平安"),
        ("600036", False, "招商银行"),
    ]
    print("\n%-10s %-9s %-10s %-30s %s" % ("目标", "抢前台", "发键事件", "OCR 读回标题区", "判定"))
    print("-" * 96)
    ok = 0
    for code, esc, expect in tests:
        n, got = jump(code, use_escape=esc)
        hit = expect in (got or "")
        ok += hit
        print("%-10s %-9s %-10s %-30s %s"
              % (code, "✓" if n else "✗", n, (got or "")[:28],
                 "★ 命中" if hit else "— 未命中"))

    print("\n命中 %d/%d" % (ok, len(tests)))

    # 再测一次「先按 ESC 回列表页」再输入
    print("\n[对照] 先按 ESC 回到列表页，再输入代码 + 回车")
    n, got = jump("600009", use_escape=True)
    print("  600009 发键=%s  OCR=%r  %s"
          % (n, (got or "")[:60],
             "★ 命中 上海机场" if "上海机场" in (got or "") else "未命中"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
