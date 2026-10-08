# -*- coding: utf-8 -*-
"""P0-18 真实自适应路径下的端到端稳态延迟

⚠ 教训：v1 版本在循环里改 coef 却忘了同步 ocr.det_coef，而 analyze_window
内部是按 ocr.det_coef 重算 det_limit 的，于是五档全被覆盖成同一档，数据全废。
v2 只做一件事：走**未经修改的真实自适应路径**，量稳态延迟。

用法：
    python probe/p18_e2e_steady.py [窗口索引] [轮数]
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import main as app  # noqa: E402
from src.capture import find_windows, grab_window  # noqa: E402


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    cfg = app.load_config()
    wins = find_windows(cfg["sources"])
    if not wins:
        print("没发现聊天窗口")
        return 1
    info = wins[min(idx, len(wins) - 1)]
    ocr = app.make_ocr(cfg, verbose=False)
    matcher = app.make_matcher(cfg, verbose=False)

    print("=" * 100)
    print("P0-18 真实自适应路径端到端延迟   %s" % info)
    o = cfg.get("ocr", {})
    print("det_coef=%.2f  det_range=%s  rec_batch=%s  use_cls=%s"
          % (o.get("det_coef", 0.85), o.get("det_range"),
             o.get("rec_batch"), o.get("use_cls")))
    print("=" * 100)

    # 取屏成本单测
    grab_ts = []
    arr = None
    for _ in range(5):
        t0 = time.perf_counter()
        arr = grab_window(info)
        grab_ts.append((time.perf_counter() - t0) * 1000)
    print("  取屏(PrintWindow) min %.0f  avg %.0f ms"
          % (min(grab_ts), sum(grab_ts) / len(grab_ts)))

    rows = []
    for i in range(rounds + 1):
        t0 = time.perf_counter()
        items = app.analyze_window(info, ocr, matcher, arr)
        dt = (time.perf_counter() - t0) * 1000
        tag = "首轮(含引擎切换)" if i == 0 else "稳态"
        rows.append(dt)
        print("  %-14s %6.0f ms   det_limit=%d  命中 %d  %s"
              % (tag, dt, ocr.det_limit, len(items),
                 "、".join(it["label"] for it in items) or "—"))
    st = rows[1:]
    print("\n  稳态：min %.0f  avg %.0f  max %.0f ms" % (min(st), sum(st) / len(st), max(st)))
    print("  含取屏的端到端理论延迟 ≈ %.0f ms（稳态 OCR + 取屏）"
          % (sum(st) / len(st) + min(grab_ts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
