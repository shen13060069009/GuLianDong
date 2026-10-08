# -*- coding: utf-8 -*-
"""p36 诊断：为什么 process_running("hexin.exe") 判「未运行」

枚举 hexin.exe 的全部顶层窗口，逐个打印：
  HWND / IsWindowVisible / IsIconic / 尺寸 / 反查 exe 名 / 标题
用来定位是「窗口被隐藏」「尺寸不够」还是「exe 名反查失败」。
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.linkage import _proc_exe, _text  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]

TARGETS = ["hexin.exe", "hexinhelper.exe", "mainfree.exe", "TdxW.exe"]


def collect():
    """先按 PID 归纳，避免依赖 exe 名反查（这才是要验的对象）"""
    buckets = collections.defaultdict(list)

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = os.path.basename(_proc_exe(pid.value)).lower()
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        buckets[exe].append({
            "hwnd": hwnd,
            "pid": pid.value,
            "visible": bool(user32.IsWindowVisible(hwnd)),
            "iconic": bool(user32.IsIconic(hwnd)),
            "size": (r.right - r.left, r.bottom - r.top),
            "title": _text(hwnd),
        })
        return True

    user32.EnumWindows(cb, 0)
    return buckets


def main():
    b = collect()
    print("=" * 96)
    print("顶层窗口诊断（exe 名 ← 由 PID 反查得到，反查失败会落在 '(空)' 桶里）")
    print("=" * 96)
    for exe in sorted(b, key=lambda k: -len(b[k])):
        rows = b[exe]
        big = [r for r in rows if r["visible"] and r["size"][0] >= 400 and r["size"][1] >= 300]
        flag = "★ 可被 find_process_window 命中 %d 个" % len(big) if big else "✗ 无合格窗口"
        print("\n[%s]  顶层窗口 %d 个   %s" % (exe or "(空)", len(rows), flag))
        for r in sorted(rows, key=lambda x: -x["size"][0] * x["size"][1])[:6]:
            print("   hwnd=0x%08X pid=%-6d vis=%-5s icon=%-5s %5dx%-5d  %s"
                  % (r["hwnd"], r["pid"], r["visible"], r["iconic"],
                     r["size"][0], r["size"][1], r["title"][:48]))

    print("\n" + "=" * 96)
    print("结论")
    print("=" * 96)
    for name in TARGETS:
        rows = b.get(name.lower(), [])   # 桶的 key 是 lower 过的，别用原名查（会永远查不到）
        if not rows:
            print("  %-18s 无任何顶层窗口（进程可能只在托盘/后台）" % name)
            continue
        ok = [r for r in rows if r["visible"] and r["size"][0] >= 400 and r["size"][1] >= 300]
        if ok:
            print("  %-18s ✓ 可联动（%d 个合格窗口，最大 %dx%d）"
                  % (name, len(ok), max(r["size"][0] for r in ok),
                     max(r["size"][1] for r in ok)))
        else:
            vis = [r for r in rows if r["visible"]]
            print("  %-18s ✗ 判为未运行：可见 %d 个 / 合格 0 个" % (name, len(vis)))
            for r in vis[:3]:
                print("        可见但尺寸不足或异常: %dx%d  「%s」"
                      % (r["size"][0], r["size"][1], r["title"][:40]))


if __name__ == "__main__":
    main()
