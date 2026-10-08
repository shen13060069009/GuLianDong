# -*- coding: utf-8 -*-
"""p34 联动端到端复验：东财 mainfree.exe / 同花顺 hexin.exe

要点：本探针**调用 src.linkage 的真实函数**（jump_to / jump_keyboard），
不是另写一套注入逻辑 —— 否则验的是探针，不是产品代码。

判据（双通道，任一命中即算成功）：
  * 窗口标题变化：同花顺跳转后标题会带上股票名
  * OCR 读标题区：东财窗口标题恒为「东方财富终端」，必须读画面
    （东财把代码+名称画在客户区 y≈104px，裁 y=88..160 才读得到）
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capture import grab_window, window_info            # noqa: E402
from src.linkage import (TARGETS, find_process_window,      # noqa: E402
                         jump_to, process_running)
from src.ocr import StockOCR                                # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "out", "em")

# 每个软件的「代码+名称」判据区 (y0, y1, x1) —— 位置各不相同，实测得来：
#   东财  股票名画在客户区 y≈104px（在「分时/5日/日线」行与公告滚动条之下）
#   同花顺 名称+分时画在左上角 y≈66..97 的一条 31px 高带里（x 到 ~390 结束）
# 注意两处都 < 128px 高，正好吃 ocr._ensure_min_height 的保护。
JUDGE_CROP = {
    "em": (88, 160, 900),
    "ths": (50, 118, 600),
    "tdx": (0, 120, 900),
}


def title_of(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def read_strip(ocr, info, key):
    y0, y1, x1 = JUDGE_CROP.get(key, (0, 160, 900))
    arr = grab_window(info)
    if arr is None or arr.size == 0:
        return ""
    y1 = min(y1, arr.shape[0])
    x1 = min(x1, arr.shape[1])
    return " | ".join(b.text for b in ocr.recognize(arr[y0:y1, 0:x1]))


def brief(s, expect):
    """把命中的片段截出来展示，避免被前面无关的 UI 文本淹没"""
    if expect and expect in s:
        i = s.index(expect)
        return "…" + s[max(0, i - 6):i + len(expect) + 10] + "…"
    return (s[:26] if s else "(空)")


def main():
    os.makedirs(OUT, exist_ok=True)
    ocr = StockOCR(gpu="auto", det_limit=960, rec_batch=8, use_cls=False)

    cases = [
        ("tdx", "600519", "贵州茅台"),
        ("em", "600519", "贵州茅台"),
        ("em", "000858", "五粮液"),
        ("ths", "600036", "招商银行"),
        ("ths", "601318", "中国平安"),
    ]

    print("=" * 100)
    print("联动端到端复验（真实调用 src.linkage）")
    print("=" * 100)
    for key, label, state in [(k, TARGETS[k]["label"], "运行中" if TARGETS[k]["proc"] and process_running(TARGETS[k]["proc"]) else "未运行")
                              for k in ("tdx", "em", "ths")]:
        print("  %-6s %-10s %s" % (key, label, state))

    print("\n%-6s %-8s %-10s %-6s %-22s %-24s %s"
          % ("目标", "代码", "预期", "发键", "标题变化", "OCR 标题区", "判定"))
    print("-" * 100)

    ok = 0
    for key, code, expect in cases:
        cfg = TARGETS[key]
        hit = find_process_window(cfg["proc"])
        if not hit:
            print("%-6s %-8s %-10s 目标未运行" % (key, code, expect))
            continue
        hwnd = hit[0]
        t_before = title_of(hwnd)
        info = window_info(hwnd)
        o_before = read_strip(ocr, info, key)
        time.sleep(0.3)

        res_ok, detail = jump_to(code, target=key, verbose=False)
        time.sleep(0.4)

        t_after = title_of(hwnd)
        info2 = window_info(hwnd)
        o_after = read_strip(ocr, info2, key)

        by_title = (expect in t_after) and (expect not in t_before)
        by_ocr = expect in o_after
        hit_ok = bool(by_title or by_ocr)
        ok += hit_ok

        print("%-6s %-8s %-10s %-6s %-22s %-24s %s"
              % (key, code, expect, "✓" if res_ok else "✗",
                 ("→ " + t_after[:18]) if by_title else "(无变化)",
                 brief(o_after, expect),
                 "★ 命中" if hit_ok else "— 未命中"))
        if not hit_ok:
            print("        detail: %s" % detail)
            print("        跳转前 OCR: %r" % o_before[:60])
            print("        跳转后 OCR: %r" % o_after[:60])

    print("\n命中 %d/%d" % (ok, len(cases)))
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
