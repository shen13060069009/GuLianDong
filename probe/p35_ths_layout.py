# -*- coding: utf-8 -*-
"""p35 同花顺画面结构探测：找出股票名画在哪个区域

p34 发现：同花顺跳转其实生效了（画面报价从 41.26 变成 53.32），
但判据区 y=88..160 落在报价带里读不到股票名。
本探针全窗 OCR + 坐标，定位「股票名」的实际位置。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capture import grab_window, window_info      # noqa: E402
from src.linkage import find_process_window           # noqa: E402
from src.ocr import StockOCR                          # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "out", "em")


def main():
    from PIL import Image
    os.makedirs(OUT, exist_ok=True)

    hit = find_process_window(("hexin.exe",))
    if not hit:
        print("同花顺未运行")
        return 1
    hwnd, _, _r, title = hit
    info = window_info(hwnd)
    print("同花顺 0x%08X  %s  %dx%d" % (hwnd, title[:44], info.width, info.height))

    arr = grab_window(info)
    if arr is None:
        print("取屏失败")
        return 1
    Image.fromarray(arr).save(os.path.join(OUT, "p35_ths_full.png"))

    ocr = StockOCR(gpu="auto", det_limit=1024, rec_batch=8, use_cls=False)
    boxes = ocr.recognize(arr)
    print("\n全窗 %d 个文本框，按 y 排序前 30 条：" % len(boxes))
    for b in sorted(boxes, key=lambda x: (x.y0, x.x0))[:30]:
        mark = "  ← 疑似股票名/代码" if (b.y0 < 200 and (4 <= len(b.text) <= 8 or b.text.isdigit())) else ""
        print("   (%4d,%4d)-(%4d,%4d) c=%.2f  %-24s%s"
              % (b.x0, b.y0, b.x1, b.y1, b.conf, b.text[:24], mark))
    return 0


if __name__ == "__main__":
    sys.exit(main())
