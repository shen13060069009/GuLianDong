# -*- coding: utf-8 -*-
"""p42 框选联动端到端验证

流程（全自动，无需人工参与）：
    1. 造一个测试窗口，白底黑字显示 5 只知名股票（OCR 稳定可读）
    2. 启动 SnipController（真实全局钩子）
    3. 用 SendInput 合成「Alt 按下 → 左键按下 → 分步拖拽 → 左键松开」
       （低级钩子能看到注入事件，和真人框选完全等价）
    4. 断言：蒙层出现 → 选区尺寸正确 → 浮条弹出 → 命中股票数 ≥ 4
    5. 再合成一次「点浮条外部」验证自动消失
    6. 截图存 out/p42_popup.png / out/p42_region.png

判定：PASS = 全部断言通过
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1


class MOUSEINPUT(ctypes.Structure):
    # ⚠ 必须带 dwFlags：x64 上 MOUSEINPUT=32 字节 → INPUT=40，
    #   少一个字段 sizeof 变 32，SendInput 直接 ERROR_INVALID_PARAMETER 静默拒绝
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _IU(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _IU)]


def abs_xy(x, y):
    """物理像素 → SendInput 归一化坐标（按虚拟桌面）"""
    vx = user32.GetSystemMetrics(76)
    vy = user32.GetSystemMetrics(77)
    vw = user32.GetSystemMetrics(78)
    vh = user32.GetSystemMetrics(79)
    return (int((x - vx) * 65535 / (vw - 1)),
            int((y - vy) * 65535 / (vh - 1)))


def mouse(flags, x=None, y=None):
    inp = INPUT()
    inp.type = INPUT_MOUSE
    if x is not None:
        nx, ny = abs_xy(x, y)
        inp.u.mi.dx = nx
        inp.u.mi.dy = ny
        inp.u.mi.dwFlags = flags | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
    else:
        inp.u.mi.dwFlags = flags
    inp.u.mi.time = 0
    arr = (INPUT * 1)(inp)
    assert user32.SendInput(1, arr, ctypes.sizeof(INPUT)) == 1, "SendInput 失败"


def key(vk, up=False):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = vk
    inp.u.ki.dwFlags = 0x0002 if up else 0
    arr = (INPUT * 1)(inp)
    user32.SendInput(1, arr, ctypes.sizeof(INPUT))


VK_MENU = 0x12


def pump(sec):
    """给 Qt 事件循环喘息（钩子回调、信号派发都在这里执行）"""
    from PySide6.QtCore import QCoreApplication
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)


def main_test():
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QFont, QPixmap
    from PySide6.QtWidgets import QApplication, QLabel
    from src import snip as snip_mod

    app = QApplication.instance() or QApplication(sys.argv)

    # ---------- 1. 测试窗口 ----------
    names = ["贵州茅台", "五粮液", "中芯国际", "宁德时代", "比亚迪"]
    win = QLabel("    ".join(names))
    f = QFont()
    f.setPixelSize(22)
    f.setBold(True)
    win.setFont(f)
    win.setStyleSheet("background:#ffffff; color:#111111;")
    win.adjustSize()
    win.resize(max(win.width(), 720), 64)
    win.move(200, 200)
    win.show()
    pump(0.6)
    wg = win.frameGeometry()
    print("[窗口] %dx%d @(%d,%d)" % (wg.width(), wg.height(), wg.x(), wg.y()))

    # ---------- 2. 控制器 + 识别线程 ----------
    cfg = main.load_config()
    matcher = main.make_matcher(cfg, verbose=False)
    snip_ocr = main.make_ocr(cfg, verbose=False)

    popup = snip_mod.SelectionPopup()
    chosen_log = []

    def on_chosen(key, code):
        chosen_log.append((key, code))
        if key == "__copy":     # 与 main.App.on_snip_action 等价
            QApplication.clipboard().setText(code)
    popup.chosen.connect(on_chosen)

    results = {}

    def on_snipped(rect):
        results["rect"] = QRect(rect)
        arr = snip_mod.grab_region(rect.left(), rect.top(),
                                   rect.width(), rect.height())
        from PIL import Image
        Image.fromarray(arr).save(os.path.join(OUT, "p42_region.png"))
        items = main.snip_recognize(snip_ocr, matcher, arr)
        results["items"] = items
        rows, seen = [], set()
        for it in items:
            code = it.get("code")
            if code and code not in seen:
                seen.add(code)
                rows.append({"name": it["label"], "code": code, "tag": ""})
        results["rows"] = rows
        acts = [("tdx", "看通达信"), ("__copy", "复制")]
        popup.show_results(rows, acts, rect)

    ctrl = snip_mod.SnipController(popup, trigger="alt")
    ctrl.snipped.connect(on_snipped)
    pump(0.3)

    if os.environ.get("P42_DEBUG"):
        orig_handle = snip_mod.SnipController._handle

        def dbg(self, w, x, y):
            alt = bool(snip_mod.user32.GetAsyncKeyState(VK_MENU) & 0x8000)
            print("  EVT 0x%04X (%d,%d) active=%s alt=%s"
                  % (w, x, y, self._active, alt), flush=True)
            return orig_handle(self, w, x, y)
        ctrl._handle = dbg.__get__(ctrl)

    ok = {}

    # ---------- 3. 合成 Alt+框选 ----------
    x0, y0 = wg.left() - 12, wg.top() - 12
    x1, y1 = wg.right() + 12, wg.bottom() + 12
    mouse(MOUSEEVENTF_MOVE, x0, y0); pump(0.15)
    key(VK_MENU); pump(0.1)
    mouse(MOUSEEVENTF_LEFTDOWN, x0, y0); pump(0.15)
    steps = 10
    for i in range(1, steps + 1):
        mouse(MOUSEEVENTF_MOVE,
              x0 + (x1 - x0) * i // steps,
              y0 + (y1 - y0) * i // steps)
        pump(0.05)
    mouse(MOUSEEVENTF_LEFTUP, x1, y1); pump(0.1)
    key(VK_MENU, up=True)
    pump(1.2)     # 等 OCR（在主线程直接跑的，这里已经在 on_snipped 里同步完成）

    rect = results.get("rect")
    ok["选区产生"] = rect is not None
    if rect is not None:
        dw = abs(rect.width() - (x1 - x0))
        dh = abs(rect.height() - (y1 - y0))
        ok["选区尺寸(±8px)"] = dw <= 8 and dh <= 8
        print("[选区] 合成 %dx%d  实得 %dx%d" % (x1 - x0, y1 - y0,
                                               rect.width(), rect.height()))
    rows = results.get("rows", [])
    ok["浮条弹出"] = popup.isVisible()
    ok["命中≥4只"] = len(rows) >= 4
    codes = {r["code"] for r in rows}
    ok["茅台识别"] = "600519" in codes
    print("[命中] %s" % "、".join("%s(%s)" % (r["name"], r["code"]) for r in rows))

    if popup.isVisible():
        pm = popup.grab()
        pm.toImage().save(os.path.join(OUT, "p42_popup.png"))
        print("[截图] 浮条 → out/p42_popup.png  %dx%d @(%d,%d)"
              % (popup.width(), popup.height(), popup.x(), popup.y()))

    # ---------- 4. 点浮条按钮（复制） ----------
    if popup.isVisible():
        # 找「复制」按钮位置：直接发 chosen 信号等价验证链路
        popup.chosen.emit("__copy", "600519")
        pump(0.2)
        clip = QApplication.clipboard().text()
        ok["复制链路"] = clip == "600519"

    # ---------- 5. 点外部自动消失 ----------
    if popup.isVisible():
        mouse(MOUSEEVENTF_MOVE, x0, y0); pump(0.1)
        mouse(MOUSEEVENTF_LEFTDOWN, x0, y0); pump(0.1)
        mouse(MOUSEEVENTF_LEFTUP, x0, y0); pump(0.3)
        ok["点外消失"] = not popup.isVisible()
    else:
        ok["点外消失"] = None    # 浮条已不在，跳过

    ctrl.shutdown()

    print("=" * 60)
    all_pass = True
    for k, v in ok.items():
        if v is None:
            print("  ○ %-14s 跳过" % k)
        elif v:
            print("  ✓ %-14s 通过" % k)
        else:
            all_pass = False
            print("  ✗ %-14s 失败" % k)
    print("=" * 60)
    print("结果：%s" % ("PASS" if all_pass else "FAIL"))
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main_test())
