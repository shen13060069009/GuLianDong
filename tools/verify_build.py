# -*- coding: utf-8 -*-
"""打包产物验证 —— 不看「文件在不在」，看「跑起来对不对」

为什么必须单独写这个：PyInstaller 的失败模式极其隐蔽。
最典型的是「缺一个 hiddenimport」或「data 没打进去」，
exe 文件照样生成、双击照样闪一下就退出（windowed 模式下连报错都看不到），
光看 dist 目录完全分不出来。

所以这里真的启动 exe，跑两轮，再去读它自己写的日志：
  1) --selftest  逐层自检（窗口 / OCR / 匹配 / 联动 / 实窗识别）
  2) --gui-test  真机 GUI 跑一轮，出合成图后自动退出

跑法：
    python tools/verify_build.py                 # 默认验 dist/GuLianDong
    python tools/verify_build.py <目录>           # 验指定产物目录
    SL_APP_DIR=<目录> python tools/verify_build.py

为什么支持指定目录：绿色版正在本机运行时 dist/GuLianDong 会被锁住，
此时只能构建到备用目录（dist_build），验证也得跟着切过去。
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP_ARG = (sys.argv[1] if len(sys.argv) > 1 else "") or os.environ.get("SL_APP_DIR", "")
APP = os.path.abspath(_APP_ARG) if _APP_ARG else os.path.join(ROOT, "dist", "GuLianDong")
EXE = os.path.join(APP, "GuLianDong.exe")
LOG = os.path.join(APP, "logs", "guliandong.log")

PASS_KEYS = [
    "[1] 窗口追踪层", "[2] OCR 引擎", "[3] 匹配引擎", "[4] 联动层", "[5] 实窗识别",
]


def tail_log(path, since=0):
    """读日志从 since 字节之后的内容"""
    if not os.path.exists(path):
        return "", since
    with open(path, "rb") as f:
        f.seek(since)
        raw = f.read()
    return raw.decode("utf-8", "ignore"), since + len(raw)


def run(args, seconds, label):
    print("\n" + "=" * 78)
    print("▶ %s    %s" % (label, " ".join(args)))
    print("=" * 78)

    before = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    t0 = time.time()
    try:
        p = subprocess.Popen(args, cwd=APP,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except OSError as e:
        print("✗ 启动失败：%s" % e)
        return False, ""

    try:
        p.wait(timeout=seconds)
        code = p.returncode
    except subprocess.TimeoutExpired:
        p.kill()
        code = "超时被强杀"
    dt = time.time() - t0

    out, _ = tail_log(LOG, before)
    print("  进程退出：%s   耗时 %.1fs" % (code, dt))
    if out.strip():
        for line in out.strip().splitlines()[:40]:
            print("  | " + line)
    else:
        print("  ⚠ 日志为空 —— 输出重定向可能没生效")
    return code == 0, out


def main():
    print("=" * 78)
    print("股联动 GuLianDong 打包产物验证")
    print("=" * 78)
    print("  exe : %s" % EXE)
    print("  存在: %s   %.1f MB" % (
        os.path.exists(EXE),
        sum(os.path.getsize(os.path.join(r, f))
            for r, _, fs in os.walk(APP) for f in fs) / 1024 / 1024
        if os.path.isdir(APP) else 0))

    if not os.path.exists(EXE):
        print("\n✗ 没找到 exe，先跑构建")
        return 1

    results = []

    # ---- 1 逐层自检 ----
    ok1, log1 = run([EXE, "--selftest"], 240, "逐层自检 --selftest")
    for k in PASS_KEYS:
        hit = k in log1
        results.append(("自检出现 %s" % k, hit))
        if not hit:
            print("  ✗ 日志里没有 %s" % k)

    # ---- 2 真机 GUI ----
    ok2, log2 = run([EXE, "--gui-test", "12"], 200, "真机 GUI --gui-test 12")
    for k in ("[自检]", "[退出]", "已清理"):
        hit = k in log2
        results.append(("GUI 日志出现 %s" % k, hit))

    # ---- 3 首次运行自举 data/ ----
    data_ok = os.path.isdir(os.path.join(APP, "data")) and \
        os.path.exists(os.path.join(APP, "data", "stocks.json"))
    results.append(("data/ 已自举到 exe 同目录", data_ok))

    # ---- 4 config.json 落地 ----
    cfg_ok = os.path.exists(os.path.join(APP, "config.json"))
    results.append(("config.json 在 exe 同目录", cfg_ok))

    print("\n" + "=" * 78)
    bad = [n for n, o in results if not o]
    print("验证 %d/%d 通过" % (len(results) - len(bad), len(results)))
    if bad:
        print("未通过：" + "、".join(bad))
        return 1
    print("全部通过 —— exe 可以直接交付")
    return 0


if __name__ == "__main__":
    sys.exit(main())
