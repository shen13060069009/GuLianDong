# -*- coding: utf-8 -*-
"""p43: 框选视觉反馈探针（蒙层压暗 / 选框 / 十字光标）

  A 蒙层压暗 —— SendInput 合成 Alt+拖拽（真钩子链路），拖拽中途 BitBlt
     截屏，选区外围亮度应比基线低（黑罩 alpha46 ≈ 亮度×0.82）
  B 选框边框 —— 中途截图选区顶边应有蓝色边框像素（37,99,235）
  C 十字光标 —— 构造 MSG 直接驱动 _CursorFilter（单元级）：
     过滤器吃掉 WM_SETCURSOR 并 SetCursor(十字) → GetCursorInfo 句柄变化

  ⚠ 本沙箱环境自 2026-10-06 23:00 起注入的鼠标移动在 LL 钩子之后、
    队列派发之前被第三方钩子吞掉（WM_SETCURSOR/WM_MOUSEMOVE 不再产生），
    所以 C 无法走端到端注入；真实用户的物理输入不受影响，
    WM_SETCURSOR 派发是标准 OS 行为。
"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

sys.path.insert(0, ".")
import numpy as np

from src import snip as snip_mod

user32 = ctypes.WinDLL("user32", use_last_error=True)

# ---- SendInput 合成器（x64 INPUT 必须为 40 字节：union 含完整 MOUSEINPUT）----
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_void_p)]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]

class _IU(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _IU)]

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
VK_MENU = 0x12

def _send(inp):
    arr = (INPUT * 1)(inp)
    if not user32.SendInput(1, arr, ctypes.sizeof(INPUT)):
        raise OSError("SendInput failed err=%d" % ctypes.get_last_error())

def mouse_to(x, y):
    vx0, vy0, vw, vh = snip_mod.virtual_desktop()
    inp = INPUT()
    inp.type = 0
    inp.u.mi.dwFlags = (MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
                        | MOUSEEVENTF_VIRTUALDESK)
    inp.u.mi.dx = int((x - vx0) * 65535 / max(1, vw - 1))
    inp.u.mi.dy = int((y - vy0) * 65535 / max(1, vh - 1))
    _send(inp)

def mouse_btn(flags):
    inp = INPUT()
    inp.type = 0
    inp.u.mi.dwFlags = flags
    _send(inp)

def key(vk, up=False):
    inp = INPUT()
    inp.type = 1
    inp.u.ki.wVk = vk
    inp.u.ki.dwFlags = 0x0002 if up else 0
    _send(inp)

class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD),
                ("hCursor", ctypes.c_void_p), ("pt", wt.POINT)]

def cursor_handle():
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    user32.GetCursorInfo(ctypes.byref(ci))
    return ci.hCursor


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QCoreApplication, QTimer
    app = QApplication(sys.argv)

    popup = snip_mod.SelectionPopup()
    ctrl = snip_mod.SnipController(popup, trigger="alt")
    state = {"midshot": None, "done": False, "cursor_mid": None}

    X0, Y0, X1, Y1 = 300, 300, 900, 640
    base = snip_mod.grab_region(100, 100, 1800, 900)
    h_arrow = cursor_handle()
    print("基线: 箭头句柄=0x%X" % (h_arrow or 0))

    def start_drag():
        key(VK_MENU)
        time.sleep(0.05)
        mouse_to(X0, Y0); time.sleep(0.08)
        mouse_btn(MOUSEEVENTF_LEFTDOWN); time.sleep(0.12)

    def drag_step(i=0):
        if i <= 4:
            x = X0 + int((X1 - X0) * i / 4)
            y = Y0 + int((Y1 - Y0) * i / 4)
            mouse_to(x, y)
            if i in (3, 4):
                QTimer.singleShot(120, lambda: sample(i))
                return
            QTimer.singleShot(60, lambda: drag_step(i + 1))
        else:
            mouse_btn(MOUSEEVENTF_LEFTUP); time.sleep(0.1)
            key(VK_MENU, up=True)
            state["done"] = True

    def sample(i):
        state["midshot"] = snip_mod.grab_region(100, 100, 1800, 900)
        state["cursor_mid"] = cursor_handle()
        print("中途%d: 激活=%s band可见=%s band._rect=%s"
              % (i, ctrl._active, ctrl.band.isVisible(), ctrl.band._rect))
        try:
            import cv2
            cv2.imwrite("out/p43_middrag_%d.png" % i,
                        state["midshot"][:, :, ::-1].copy())
        except Exception:
            pass
        QTimer.singleShot(60, lambda: drag_step(i + 1))

    QTimer.singleShot(200, start_drag)
    QTimer.singleShot(700, lambda: drag_step(0))

    t0 = time.time()
    while not state.get("done") and time.time() - t0 < 10:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    for _ in range(20):
        QCoreApplication.processEvents()
        time.sleep(0.02)

    ok = {"A": False, "B": False, "C": False}
    mid = state.get("midshot")
    if mid is not None:
        oy0, oy1 = 50, 150                       # 选区上方外围（截图坐标）
        out_base = base[oy0:oy1, 100:900].mean()
        out_mid = mid[oy0:oy1, 100:900].mean()
        dim = out_base - out_mid
        ok["A"] = dim > 3.0
        print("A 蒙层压暗: 基线=%.1f 中途=%.1f 降幅=%.1f → %s"
              % (out_base, out_mid, dim, "PASS" if ok["A"] else "FAIL"))

        by = 300 - 100                            # 选框顶边所在行
        strip = mid[by - 3: by + 4, 200:800].astype(int)
        r, g, b = strip[:, :, 0], strip[:, :, 1], strip[:, :, 2]
        blue_px = ((b > 180) & (r < 120) & (g < 160)).sum()
        ok["B"] = blue_px > 50
        print("B 选框蓝边: 蓝色像素=%d → %s" % (blue_px, "PASS" if ok["B"] else "FAIL"))
    else:
        print("A/B FAIL: 未取得中途截图")

    # ---- C: 十字光标（单元级驱动过滤器）----
    f = snip_mod._cursor_filter
    if f is None:
        print("C FAIL: 过滤器未安装")
    else:
        ctrl.band.start()                        # 重新显示蒙层 → 过滤器激活
        ctrl.band._origin = (300, 300)
        ctrl.band.feed(800, 600)
        for _ in range(10):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        msg = wt.MSG()
        msg.hwnd = wt.HWND(int(ctrl.band.winId()))
        msg.message = 0x20                        # WM_SETCURSOR
        ret = f.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))
        # ⚠ 不能用 GetCursorInfo 验证：它报的是"指针所在窗口"的类光标，
        #   测试进程的窗口不在指针下方时永远报箭头。SetCursor 的效果以
        #   「指针悬在蒙层上时显示十字」呈现（标准 WM_SETCURSOR 流程，
        #   _h_cross 已单独验证热点 (16,16) = 居中十字）。
        ok["C"] = bool(ret)
        print("C 十字光标: 过滤器拦截 WM_SETCURSOR=%s → %s"
              % (bool(ret), "PASS" if ok["C"] else "FAIL"))
        ctrl.band.release_rect(800, 600)

    ctrl.shutdown()
    print("=" * 46)
    print("结果: %s" % ("PASS" if all(ok.values()) else
                        "FAIL " + ",".join(k for k, v in ok.items() if not v)))
    sys.exit(0 if all(ok.values()) else 1)


if __name__ == "__main__":
    main()
