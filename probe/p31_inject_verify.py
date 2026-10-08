# -*- coding: utf-8 -*-
"""p31 东财键盘注入：工具链自检 + 多序列对照

p29/p30 留下的疑点：
  * 权限不是问题（两边都是管理员，UIPI 排除）
  * 但四种按键序列全都等于基线噪声
  * 记事本验证失败（新版记事本没有经典 Edit 子类，测不出来）

这一轮要一次解决两件事：
  【工具链】用 Qt 自建窗口闭环验证 SendInput 到底能不能把字符送进另一个线程
           的输入控件（QLineEdit 能读回文本，是唯一可靠的读数方式）
  【东财】把「鼠标点击」也纳入手段：抢前台 → 点主图区 → 试多组按键序列
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402
from src.capture import grab_window, window_info  # noqa: E402
from src.linkage import force_foreground, _send_inputs, send_vk  # noqa: E402

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

VK_CONTROL, VK_F, VK_K, VK_ESCAPE, VK_RETURN = 0x11, 0x46, 0x4B, 0x1B, 0x0D
VK_F3 = 0x72
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD, INPUT_MOUSE = 1, 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000

EM_EXES = ("mainfree.exe", "maintrade.exe", "stockway.exe")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "out", "em")


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _U(ctypes.Union):
    _fields_ = [("ki", ctypes.c_byte * 32), ("mi", MOUSEINPUT)]


class INPUT_M(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT_M), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]


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


def click_at(x, y):
    """真实鼠标点击（SetCursorPos + SendInput 左键）"""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.12)
    arr = (INPUT_M * 2)()
    for i, fl in enumerate((MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)):
        arr[i].type = INPUT_MOUSE
        arr[i].u.mi.dx = 0
        arr[i].u.mi.dy = 0
        arr[i].u.mi.mouseData = 0
        arr[i].u.mi.dwFlags = fl
        arr[i].u.mi.time = 0
        arr[i].u.mi.dwExtraInfo = None
    user32.SendInput(2, arr, ctypes.sizeof(INPUT_M))
    time.sleep(0.2)


def type_text(text, delay=0.06):
    from src.linkage import _send_inputs as si
    total = 0
    for ch in text:
        total += si([(0, ord(ch), KEYEVENTF_UNICODE),
                     (0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)])
        time.sleep(delay)
    return total


def hotkey(vk, mod=VK_CONTROL):
    _send_inputs([(mod, 0, 0), (vk, 0, 0),
                  (vk, 0, KEYEVENTF_KEYUP), (mod, 0, KEYEVENTF_KEYUP)])
    time.sleep(0.2)


def zone_diff(a, b, zones):
    H, W = a.shape[:2]
    out = []
    for name, x0, y0, x1, y1 in zones:
        sa = a[int(H * y0):int(H * y1), int(W * x0):int(W * x1)].astype(np.int16)
        sb = b[int(H * y0):int(H * y1), int(W * x0):int(W * x1)].astype(np.int16)
        d = np.abs(sa - sb).sum(axis=2)
        out.append((name, float((d > 32).sum()) / d.size * 100))
    return dict(out)


ZONES = [
    ("顶部工具栏", 0.00, 0.00, 1.00, 0.09),
    ("主图区", 0.00, 0.20, 0.75, 0.70),
    ("右侧盘口", 0.75, 0.09, 1.00, 0.70),
    ("底部资讯", 0.00, 0.88, 1.00, 1.00),
    ("全局", 0.00, 0.00, 1.00, 1.00),
]


# ---------------------------------------------------------------- 一、SendInput 工具链自检
def selftest_sendinput():
    print("=" * 84)
    print("一、SendInput 工具链自检（Qt 窗口跨线程闭环）")
    print("=" * 84)
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QLineEdit, QWidget, QVBoxLayout

    app = QApplication.instance() or QApplication(sys.argv)
    w = QWidget()
    w.setWindowTitle("StockLens SendInput Probe")
    w.setGeometry(300, 300, 420, 110)
    lay = QVBoxLayout(w)
    ed = QLineEdit()
    ed.setObjectName("probe")
    lay.addWidget(ed)
    w.show()
    w.raise_()
    w.activateWindow()
    ed.setFocus()

    result = {"text": None, "diag": {}}

    def pump(ms):
        """在等待期间持续 pump Qt 事件循环（time.sleep 会把键盘消息压死）"""
        t0 = time.time()
        while (time.time() - t0) * 1000 < ms:
            app.processEvents()
            time.sleep(0.008)

    def fire():
        hwnd = int(w.winId())
        force_foreground(hwnd)
        pump(500)
        # 真实点击一下输入框，确保系统级焦点真的落在它上面
        click_at(w.x() + ed.x() + 60, w.y() + ed.y() + ed.height() // 2)
        pump(400)
        ed.setFocus()
        pump(300)

        before = ed.text()
        # 分批发键，每发一批就 pump，模拟真实打字节奏
        total = 0
        for ch in "AB7":
            total += _send_inputs([(0, ord(ch), KEYEVENTF_UNICODE),
                                   (0, ord(ch),
                                    KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)])
            pump(180)
        pump(500)

        result["diag"] = {
            "fg": user32.GetForegroundWindow(),
            "hwnd": hwnd,
            "fg_is_mine": user32.GetForegroundWindow() == hwnd,
            "sent": total,
            "before": before,
            "focus_widget": type(app.focusWidget()).__name__ if app.focusWidget() else None,
            "active": bool(app.activeWindow()),
        }
        result["text"] = ed.text()
        app.quit()

    QTimer.singleShot(900, fire)
    app.exec()
    got = result["text"]
    d = result.get("diag") or {}
    print("  诊断: 前台=0x%08X 我的窗口=0x%08X 前台是我的=%s  "
          "焦点控件=%s Qt activeWindow=%s  发出事件=%s  发键前内容=%r"
          % (d.get("fg", 0), d.get("hwnd", 0), d.get("fg_is_mine"),
             d.get("focus_widget"), d.get("active"), d.get("sent"),
             d.get("before")))
    ok = got is not None and "AB7" in got
    print("  输入框回读 = %r" % got)
    print("  → SendInput %s" % ("工作正常 ✓" if ok else "无效 ✗（根因在工具链，不在东财）"))
    return ok


# ---------------------------------------------------------------- 二、东财多序列对照
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


def main():
    os.makedirs(OUT, exist_ok=True)
    tool_ok = selftest_sendinput()
    if "--selftest-only" in sys.argv:
        return 0 if tool_ok else 2

    hwnd = find_em()
    if not hwnd:
        print("\n东财未运行")
        return 1
    info = window_info(hwnd)
    cx = info.client[0] + info.width // 2
    cy = info.client[1] + info.height // 2

    print("\n" + "=" * 84)
    print("二、东财多序列对照   窗口 0x%08X  %dx%d  中心点 (%d,%d)"
          % (hwnd, info.width, info.height, cx, cy))
    print("=" * 84)

    def shot(tag):
        a = grab_window(info)
        if a is not None:
            Image.fromarray(a).save(os.path.join(OUT, "%s.png" % tag))
        return a

    base = shot("v3_00_base")
    time.sleep(2.5)
    idle = shot("v3_01_idle")
    print("  基线噪声: " + "  ".join("%s=%.2f%%" % (k, v)
                                    for k, v in zone_diff(base, idle, ZONES).items()))

    def run(tag, label, actions):
        force_foreground(hwnd)
        time.sleep(0.5)
        actions()
        time.sleep(2.6)
        a = shot(tag)
        z = zone_diff(base, a, ZONES)
        print("  %-30s 主图=%6.2f%%  顶栏=%5.2f%%  全局=%5.2f%%  %s"
              % (label, z["主图区"], z["顶部工具栏"], z["全局"],
                 "★★" if z["主图区"] > 15 else ("?" if z["主图区"] > 3 else "—")))
        return z

    res = []
    # 1 纯 ESC —— 东财 ESC = 取消/返回上一级
    res.append(("ESC", run("v3_02_esc", "ESC", lambda: send_vk(VK_ESCAPE))))

    # 2 点击主图区（让焦点落到内容区）后再输入
    def a_click_type():
        click_at(cx, cy)
        time.sleep(0.3)
        type_text("600519")
        time.sleep(0.25)
        send_vk(VK_RETURN)
    res.append(("点击+输入+回车",
                run("v3_03_clicktype", "点主图 → 600519 → 回车", a_click_type)))

    # 3 点击主图区 + Ctrl+F
    def a_click_ctrlf():
        click_at(cx, cy)
        time.sleep(0.3)
        hotkey(VK_F)
        time.sleep(0.4)
        type_text("600036")
        time.sleep(0.25)
        send_vk(VK_RETURN)
    res.append(("点击+CtrlF+输入",
                run("v3_04_ctrlf", "点主图 → Ctrl+F → 600036 → 回车", a_click_ctrlf)))

    # 4 只点顶部搜索框位置（右上角）再输入
    def a_searchbox():
        sx = info.client[0] + int(info.width * 0.72)
        sy = info.client[1] + int(info.height * 0.035)
        click_at(sx, sy)
        time.sleep(0.5)
        type_text("000858")
        time.sleep(0.3)
        send_vk(VK_RETURN)
    res.append(("点搜索框+输入",
                run("v3_05_searchbox", "点顶部搜索框 → 000858 → 回车", a_searchbox)))

    print("\n" + "=" * 84)
    print("结果")
    print("=" * 84)
    if not tool_ok:
        print("  ⚠ SendInput 工具链自检就没过，东财那几行全部不可信")
    for nm, z in res:
        print("  %-20s 主图区 %6.2f%%   %s"
              % (nm, z["主图区"],
                 "成功" if z["主图区"] > 15 else "无变化"))
    print("\n出图: out/em/v3_*.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
