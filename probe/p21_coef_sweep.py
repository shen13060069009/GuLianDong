# -*- coding: utf-8 -*-
"""P0-21 跨帧 det 系数扫描（v2：每档独立引擎 + 原文取证）

v1 的耗时列不可信：7 个 det 档共用一台 StockOCR，缓存里同时挂 4~7 套 onnx
会话，DML 显存互相挤压，出现 399ms 与 3622ms 并存。v2 改成每档重建引擎、
只留一套会话，并在漏检时把该帧的 OCR 原文打出来，判断是「真漏」还是「误读」。

用法：
    python probe/p21_coef_sweep.py
"""
import glob
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main as app  # noqa: E402

CAP_DIR = os.path.join(ROOT, "out", "captures")
COEFS = (0.45, 0.55, 0.65, 0.75, 0.85, 1.00)
REF_COEF = 1.80


def hits_of(ocr, matcher, arr):
    boxes = ocr.recognize(arr)
    items = app.match_boxes(boxes, matcher)
    return {it["label"] for it in items}, boxes


def main():
    cfg = app.load_config()
    matcher = app.make_matcher(cfg, verbose=False)
    ocfg = cfg.get("ocr", {})
    # 用产品配置里的 det_range 做 clamp —— 否则测出来的不是线上真实行为。
    # 允许命令行覆盖上限，用来验证「2000 够不够 / 要不要抬到 2400」。
    rng = ocfg.get("det_range") or [640, 2000]
    lo_det = int(sys.argv[1]) if len(sys.argv) > 1 else int(rng[0])
    hi_det = int(sys.argv[2]) if len(sys.argv) > 2 else int(rng[1])
    print("det_range clamp = [%d, %d]" % (lo_det, hi_det))

    files = sorted(f for f in glob.glob(os.path.join(CAP_DIR, "*.png"))
                   if "_highlighted" not in f)
    frames = [(os.path.basename(f)[:-4],
               np.asarray(Image.open(f).convert("RGB"))) for f in files]

    # ---- 伪金标准（单独一台引擎，测完即弃）----
    ref = app.make_ocr(cfg, verbose=False)
    ref.det_range = (200, 4000)
    gold = {}
    ref_text = {}
    for name, arr in frames:
        ls = max(arr.shape[1], arr.shape[0])
        ref.reconfigure(det_limit=max(320, min(3000, int(ls * REF_COEF))))
        g, boxes = hits_of(ref, matcher, arr)
        gold[name] = g
        ref_text[name] = [b.text for b in boxes]
    del ref

    print("=" * 108)
    print("P0-21 跨帧 det 系数扫描 v2（%d 张样本，伪金标准 coef=%.2f＝几乎不缩放）"
          % (len(frames), REF_COEF))
    print("=" * 108)
    for name, arr in frames:
        print("  %-20s %5dx%-5d  %s"
              % (name, arr.shape[1], arr.shape[0],
                 "、".join(sorted(gold[name])) or "—"))

    print("\n  %-7s %-13s %-9s %-9s %-11s %s"
          % ("coef", "实际det(各帧)", "召回率", "漏检", "多余命中", "稳态ms 中位/最差"))
    print("  " + "-" * 100)
    summary = {}
    for coef in COEFS:
        ocr = app.make_ocr(cfg, verbose=False)     # 每档独立引擎
        ocr.det_range = (lo_det, hi_det)
        rec = tot = extra = 0
        miss = {}
        extra_detail = {}
        times = []
        dets = []
        for name, arr in frames:
            ls = max(arr.shape[1], arr.shape[0])
            det = max(lo_det, min(hi_det, int(ls * coef)))
            dets.append(det)
            ocr.reconfigure(det_limit=det)
            ocr.recognize(arr)                     # 预热（含可能的模型加载）
            ts = []
            found = set()
            for _ in range(3):
                t0 = time.perf_counter()
                found, _ = hits_of(ocr, matcher, arr)
                ts.append((time.perf_counter() - t0) * 1000)
            times.append(min(ts))
            rec += len(found & gold[name])
            tot += len(gold[name])
            e = found - gold[name]
            extra += len(e)
            if e:
                extra_detail[name] = sorted(e)
            lost = gold[name] - found
            if lost:
                miss[name] = sorted(lost)
        times.sort()
        med = times[len(times) // 2]
        rate = 100.0 * rec / tot if tot else 0
        summary[coef] = (rate, rec, tot, extra, med, times[-1], miss, extra_detail)
        print("  %-7.2f %-13s %-9s %-9s %-11d %.0f / %.0f"
              % (coef, "%d~%d" % (min(dets), max(dets)),
                 "%.0f%% (%d/%d)" % (rate, rec, tot),
                 sum(len(v) for v in miss.values()), extra, med, times[-1]))

    print("\n  漏检取证（连同同帧 OCR 原文，判断是「真漏」还是「误读」）：")
    for coef in COEFS:
        rate, rec, tot, extra, med, worst, miss, extra_detail = summary[coef]
        if not miss:
            print("    coef=%.2f  无漏检" % coef)
            continue
        print("    coef=%.2f  漏 %s" % (coef, ", ".join(
            "%s→%s" % (k, "、".join(v)) for k, v in sorted(miss.items()))))
        if coef in (0.55, 0.85):
            for fname, lost in sorted(miss.items()):
                joined = "".join(ref_text[fname])
                for w in lost:
                    print("        [%s] 金标准有「%s」，该帧原文是否出现：%s"
                          % (fname, w, "是（OCR 变体）" if w in joined else "否（可能误读）"))

    print("\n  多余命中取证（相对金标准多报的）：")
    for coef in COEFS:
        ed = summary[coef][7]
        print("    coef=%.2f  %s" % (coef, ", ".join(
            "%s→%s" % (k, "、".join(v)) for k, v in sorted(ed.items())) or "无"))

    best_safe = max((c for c in COEFS if summary[c][0] >= 96.0), default=None)
    print("\n  召回 ≥96%% 的最低档：%s"
          % ("coef=%.2f（中位 %.0fms）" % (best_safe, summary[best_safe][4])
             if best_safe else "无，需要 coef=1.00 以上"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
