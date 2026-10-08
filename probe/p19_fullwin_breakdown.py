# -*- coding: utf-8 -*-
"""P0-19 全窗 OCR 成分拆解 + 优化试验（走真实引擎：DirectML + cls 关闭）

P0-18 显示：det_limit 640~2000 对稳态总耗时几乎无影响（都在 940ms）。
本探针在真实窗口帧上做 det/cls/rec 三段拆解并逐项试参。

⚠ 踩坑：直接用 RapidOCR(**kw) 构造会走 CPU —— DirectML 只由
src.ocr._patch_ort_provider 注入，且必须在构造 RapidOCR 之前调用。
所以下面统一走 app.make_ocr()，再直接调 ocr.engine(arr) 拿带 elapse 的原始输出。
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main as app  # noqa: E402
from src.capture import find_windows, grab_window  # noqa: E402
from src.ocr import _available_eps, _patch_ort_provider  # noqa: E402


def hot(eng, arr, reps=4):
    eng(arr)
    best = None
    for _ in range(reps):
        t0 = time.perf_counter()
        out = eng(arr)
        dt = (time.perf_counter() - t0) * 1000
        el = out[1] if isinstance(out, tuple) and len(out) > 1 else None
        res = out[0] if isinstance(out, tuple) else out
        if best is None or dt < best[0]:
            best = (dt, el, res)
    return best


def fmt(e):
    if not e:
        return "n/a"
    try:
        v = [float(x) * 1000 for x in e]
    except Exception:
        return str(e)[:40]
    names = ["det", "cls", "rec"]
    return "  ".join("%s=%.0f" % (names[i] if i < 3 else "x%d" % i, x)
                     for i, x in enumerate(v))


def mk(det_limit=900, rec_batch=24, use_cls=False):
    from rapidocr_onnxruntime import RapidOCR
    kw = dict(rec_batch_num=rec_batch, det_limit_side_len=det_limit,
              det_limit_type="max", use_angle_cls=use_cls,
              det_model_path=None, rec_model_path=None)
    if use_cls:
        kw["cls_model_path"] = None
    return RapidOCR(**kw)


def main():
    cfg = app.load_config()
    wins = find_windows(cfg["sources"])
    if not wins:
        print("没发现聊天窗口")
        return 1
    info = wins[0]
    arr = grab_window(info)
    eps = _available_eps()
    print("=" * 100)
    print("P0-19 全窗 OCR 拆解   %s %dx%d" % (info.app, arr.shape[1], arr.shape[0]))
    print("onnxruntime EP: %s" % eps)
    print("=" * 100)

    _patch_ort_provider("DmlExecutionProvider" in eps)

    print("\n  %-22s %-9s %-7s %s" % ("配置", "总耗时", "文本块", "分段(ms)"))
    print("  " + "-" * 90)
    for label, det, batch, cls in [
        ("基线 det900 b24", 900, 24, False),
        ("det640   b24", 640, 24, False),
        ("det1126  b24", 1126, 24, False),
        ("det2000  b24", 2000, 24, False),
        ("det900 b8", 900, 8, False),
        ("det900 b16", 900, 16, False),
        ("det900 b32", 900, 32, False),
        ("det900 b48", 900, 48, False),
        ("det900 b24 +cls", 900, 24, True),
    ]:
        try:
            eng = mk(det, batch, cls)
        except Exception as e:
            print("  %-22s 构建失败 %s" % (label, str(e)[:60]))
            continue
        dt, el, res = hot(eng, arr)
        print("  %-22s %-9s %-7d %s" % (label, "%.0f ms" % dt, len(res or []), fmt(el)))

    # ---- 裁剪试验（GPU + 基线参数）----
    H, W = arr.shape[:2]
    print("\n  裁剪试验（把非文字区域砍掉，GPU b24）:")
    for label, (x0, y0, x1, y1) in [
        ("全窗", (0, 0, W, H)),
        ("去左栏 25%", (int(W * 0.25), 0, W, H)),
        ("去左栏 25% + 底 8%", (int(W * 0.25), 0, W, int(H * 0.92))),
        ("去左栏25%+顶6%+底8%", (int(W * 0.25), int(H * 0.06), W, int(H * 0.92))),
    ]:
        sub = np.ascontiguousarray(arr[y0:y1, x0:x1])
        eng = mk(900, 24, False)
        dt, el, res = hot(eng, sub)
        print("    %-22s %4dx%-4d %6.0f ms  文本块 %-4d %s"
              % (label, sub.shape[1], sub.shape[0], dt, len(res or []), fmt(el)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
