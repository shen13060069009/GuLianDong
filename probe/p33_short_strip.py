# -*- coding: utf-8 -*-
"""p33 矮条 OCR 保护复验

复现 p32 踩到的坑：东财把「代码 + 名称」画在客户区 y≈104px 处的一条
72px 高的带子里。72px 直接送进 det 会返回 0 框（DBNet backbone 5 次
下采样，1/32 → 特征图只剩 2px 高）。

本探针做三件事：
  1. 对照组：绕过 recognize 直接把 72px 原图送 engine —— 应 0 框
  2. 修复组：走 recognize（内部自动 pad 到 160）—— 应读出内容
  3. 断言：回退后的 y 坐标落回 [0, 72) 原图坐标系
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.capture import grab_window, window_info          # noqa: E402
from src.linkage import find_process_window               # noqa: E402
from src.ocr import StockOCR                              # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "out", "em")


def main():
    from PIL import Image
    os.makedirs(OUT, exist_ok=True)

    hit = find_process_window(("mainfree.exe", "maintrade.exe", "stockway.exe"))
    if not hit:
        print("东方财富未运行")
        return 1
    hwnd, _, _rect, title = hit
    info = window_info(hwnd)
    print("=" * 84)
    print("东财 0x%08X  %s  %dx%d" % (hwnd, title[:44], info.width, info.height))
    print("=" * 84)

    arr = grab_window(info)
    if arr is None or arr.size == 0:
        print("取屏失败")
        return 1

    y0, y1 = 88, min(160, arr.shape[0])
    crop = arr[y0:y1, 0:min(900, arr.shape[1])]
    Image.fromarray(crop).save(os.path.join(OUT, "p33_crop_raw.png"))
    print("裁剪条高度 = %d px（< 128 触发保护阈值）" % crop.shape[0])

    ocr = StockOCR(gpu="auto", det_limit=960, rec_batch=8, use_cls=False)

    # ---- 对照组：绕过 recognize，直接送 engine（== 修复前的行为）----
    raw = ocr.engine(crop)
    raw_res = raw[0] if isinstance(raw, tuple) else raw
    n_raw = len(raw_res or [])
    print("\n[对照] 直接送 %dpx 原图 → %d 个框" % (crop.shape[0], n_raw))

    # ---- 修复组：走 recognize（内部自动 pad 到 MIN_DET_HEIGHT=160）----
    boxes = ocr.recognize(crop)
    print("[修复] 走 recognize（自动 pad 到 160）→ %d 个框" % len(boxes))
    for b in boxes[:8]:
        print("      (%4d,%4d)-(%4d,%4d) c=%.2f  %s"
              % (b.x0, b.y0, b.x1, b.y1, b.conf, b.text))

    in_range = all(0 <= b.y0 < crop.shape[0] for b in boxes) if boxes else False
    print("\n★ y 坐标是否回退到原图坐标系 [0,%d)：%s"
          % (crop.shape[0], "是" if in_range else "否"))

    ok = n_raw == 0 and len(boxes) > 0 and in_range
    print("★ 结论：%s" % ("保护生效（0 框 → 正常识别，坐标已回退）" if ok
                       else "未达预期，需要复查"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
