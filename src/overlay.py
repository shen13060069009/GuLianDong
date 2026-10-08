# -*- coding: utf-8 -*-
"""高亮渲染层 + 命中交互层

用一块「分层透明窗口」盖在目标聊天窗口上，把识别到的股票名逐词画框。

═══ 关键设计 ═══

1. 窗口样式
   FramelessWindowHint + WA_TranslucentBackground 让 Qt 出透明窗；
   再用 win32 补上 WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
   | WS_EX_TOOLWINDOW，达到：不抢焦点、不进任务栏、鼠标穿透。

2. 命中测试为什么用全局钩子而不是让窗口收事件
   因为窗口设了 WS_EX_TRANSPARENT（整层穿透），它永远收不到鼠标事件。
   如果去掉穿透，又会挡住微信/飞书本身的点击（用户点聊天记录就点不动了）。
   所以正确做法是：**整层保持穿透，另用 WH_MOUSE_LL 低级鼠标钩子
   自己判定点击坐标是否落进高亮矩形**。钩子只在需要时才拦截。

3. 坐标
   高亮矩形以「目标窗口客户区左上角」为原点，而 Overlay 正好对齐客户区，
   因此两者坐标系天然一致，不需要额外换算。窗口移动时同步 move() 即可。

═══ 实测坑 ═══
  * DPI：本机 100% 缩放（DPI=96），若目标机有缩放，必须保证本进程是
    Per-Monitor DPI Aware V2，否则 Qt 的 move() 会按逻辑像素算，整层错位。
  * 副屏在主屏上方（y 为负）时，务必按虚拟桌面绝对坐标 move()，
    不能用「相对主屏」的假设。
"""
import ctypes
import ctypes.wintypes as wt
import time

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_RBUTTONDOWN = 0x0204

# 点击手势：默认只吃 Ctrl+单击。
#   plain  —— 单击就拦截（会挡住聊天窗口里的普通点击，慎用）
#   ctrl   —— 只有按住 Ctrl 单击才拦截并弹菜单，其它点击原样透传 ★默认
#   off    —— 完全不拦，只做高亮（纯展示模式）
CLICK_MODES = ("plain", "ctrl", "off")
VK_CONTROL = 0x11

user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetAncestor.restype = wt.HWND
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]


# 高亮色板：遵循 A 股约定（红涨绿跌），无行情时用中性色
PALETTE = {
    "up":      {"fill": QColor(226, 75, 74, 38),   "line": QColor(226, 75, 74),   "text": QColor(163, 45, 45)},
    "down":    {"fill": QColor(29, 158, 117, 38),  "line": QColor(29, 158, 117),  "text": QColor(15, 110, 86)},
    "flat":    {"fill": QColor(37, 99, 235, 32),   "line": QColor(37, 99, 235),   "text": QColor(24, 95, 165)},
    "concept": {"fill": QColor(133, 79, 11, 32),   "line": QColor(186, 117, 23),  "text": QColor(99, 56, 6)},
    "hover":   {"fill": QColor(37, 99, 235, 60),   "line": QColor(37, 99, 235),   "text": QColor(12, 68, 124)},
}

# 暗色主题档位。实测微信 4.x 客户区帧灰度均值 52.7。
#
# 定档依据（probe/p24 + p25，用「文字可读性」而不是「高亮可见性」做指标）：
#   微信底色 RGB(47,47,48)，文字 RGB(117,117,121)，原始文字对比度 2.91。
#   在高亮区里文字会被填充色再混一次，alpha 越大字越糊：
#       暖黄 alpha 0.12 → 高亮vs底 1.35 / 文字vs填充 2.52   ✓
#       暖黄 alpha 0.16 → 高亮vs底 1.49 / 文字vs填充 2.40   ✓ 取这档
#       暖黄 alpha 0.20 → 高亮vs底 1.66 / 文字vs填充 2.27   ✗ 开始盖字
#   亮蓝在暗底上「高亮vs底」只有 1.16~1.22，等于白画 —— 暗底必须用暖色。
# 踩坑：第一版按「高亮填充 vs 背景」选配色，指标全绿但实际把字盖住了，
#       直到把对照图放大三倍看才发现。指标要选对，否则数据会骗人。
_D = 44   # 暗底填充 alpha（约 0.17）

PALETTE_DARK = {
    "up":      {"fill": QColor(226, 75, 74, _D),   "line": QColor(255, 138, 132), "text": QColor(255, 200, 198)},
    "down":    {"fill": QColor(29, 158, 117, _D),  "line": QColor(88, 220, 178),  "text": QColor(190, 246, 228)},
    "flat":    {"fill": QColor(255, 214, 64, _D),  "line": QColor(255, 214, 64),  "text": QColor(255, 240, 190)},
    "concept": {"fill": QColor(198, 120, 255, _D), "line": QColor(206, 150, 255), "text": QColor(240, 220, 255)},
    "hover":   {"fill": QColor(255, 214, 64, 92),  "line": QColor(255, 246, 200), "text": QColor(255, 250, 230)},
}


def palette_for(state, dark=False):
    """按背景明暗取配色。dark=True 走暗色档。"""
    table = PALETTE_DARK if dark else PALETTE
    pal = table.get(state)
    if pal is None:
        pal = table["flat"]
    return pal


class Overlay(QWidget):
    """盖在目标窗口客户区上的透明高亮层"""

    clicked = Signal(object)   # 参数为该处的高亮项

    def __init__(self, clickable=True, radius=3, only_when_foreground=True,
                 adaptive_palette=True, click_modifier="ctrl"):
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
            | Qt.Tool | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.radius = radius
        self.only_when_foreground = only_when_foreground
        # 关掉就一律用浅底配色（调试用；微信 4.x 暗色下会几乎看不见）
        self.adaptive_palette = adaptive_palette
        self.items = []            # [{"rect": (x,y,w,h), "match": ..., "state": "up"/..., "label": str}]
        self._hover = -1
        self._hook = None
        self._hook_proc = None
        self._target = None        # 绑定的 WindowInfo
        self._clickable = clickable
        self.click_modifier = click_modifier if click_modifier in CLICK_MODES else "ctrl"

        self._track_timer = QTimer(self)
        self._track_timer.timeout.connect(self._follow_target)
        self._track_timer.setInterval(250)

    # ------------------------------------------------------------ 绑定目标
    def attach_to(self, info):
        """把自己对齐到目标窗口的客户区"""
        self._target = info
        self.setGeometry(info.client[0], info.client[1], info.width, info.height)
        self.apply_win32_style()
        self.show()
        self.raise_()
        if not self._track_timer.isActive():
            self._track_timer.start()
        if self._clickable and self._hook is None:
            self.install_mouse_hook()

    def apply_win32_style(self):
        hwnd = int(self.winId())
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            ex | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
        )
        user32.SetWindowPos(hwnd, HWND_TOPMOST,
                            self.x(), self.y(), self.width(), self.height(),
                            SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _follow_target(self):
        if self._target is None:
            return
        if not self._target.alive():
            self.hide()
            self._track_timer.stop()
            return
        from src.capture import window_info
        info = window_info(self._target.hwnd)
        self._target = info
        if (self.x(), self.y()) != (info.client[0], info.client[1]) or \
           (self.width(), self.height()) != (info.width, info.height):
            self.setGeometry(info.client[0], info.client[1], info.width, info.height)

        # ---- 遮挡保护 ----
        # 本层是 TOPMOST。如果聊天窗口此刻被别的程序盖住（实测：飞书被通达信
        # 整个盖掉），高亮框会孤零零浮在那个程序上面，看起来像画错了地方。
        # 所以只在「聊天窗口就是前台顶层窗口」时才显示，否则整层隐藏。
        if self.only_when_foreground:
            on_top = self._is_foreground()
            if on_top and not self.isVisible():
                self.show()
                self.raise_()
            elif not on_top and self.isVisible():
                self.hide()

    def _is_foreground(self):
        """目标窗口（或其子窗口）是不是当前前台顶层窗口"""
        if self._target is None:
            return False
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        GA_ROOT = 2
        root = user32.GetAncestor(fg, GA_ROOT) or fg
        return root == self._target.hwnd

    # ------------------------------------------------------------ 高亮数据
    def set_highlights(self, items):
        """items: [{"rect": (x,y,w,h), "state": str, "label": str, "match": obj}]"""
        self.items = list(items)
        self._hover = -1
        self.update()

    def clear(self):
        self.items = []
        self._hover = -1
        self.update()

    def hit_test(self, sx, sy):
        """屏幕坐标 → 命中的高亮项下标"""
        if self._target is None:
            return -1
        lx = sx - self._target.client[0]
        ly = sy - self._target.client[1]
        for i, it in enumerate(self.items):
            x, y, w, h = it["rect"]
            if x - 2 <= lx <= x + w + 2 and y - 2 <= ly <= y + h + 2:
                return i
        return -1

    def _should_catch(self):
        """这一击要不要「吃掉」（拦截，不传给底下的聊天窗口）。

        为什么默认要 Ctrl：整层是穿透的，如果我们把普通单击也吃掉，
        用户在聊天窗口里想点链接/选文字时会莫名其妙没反应。加 Ctrl 修饰后
        语义就清楚了 —— 普通点击照常给微信，Ctrl+点击才是「我要看这只票」。
        """
        if not self._clickable:
            return False
        if self.click_modifier == "off":
            return False
        if self.click_modifier == "ctrl":
            return bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
        return True     # plain

    # ------------------------------------------------------------ 全局鼠标钩子
    def install_mouse_hook(self):
        if self._hook:
            return
        overlay = self
        down_at = [None]

        @HOOKPROC
        def proc(nCode, wParam, lParam):
            if nCode == 0:
                try:
                    info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    x, y = info.pt.x, info.pt.y
                    if wParam == WM_MOUSEMOVE:
                        idx = overlay.hit_test(x, y)
                        if idx != overlay._hover:
                            overlay._hover = idx
                            overlay.update()
                    elif wParam == WM_LBUTTONDOWN:
                        down_at[0] = (x, y)
                    elif wParam == WM_LBUTTONUP:
                        if down_at[0]:
                            dx = abs(x - down_at[0][0])
                            dy = abs(y - down_at[0][1])
                            if dx < 5 and dy < 5:
                                idx = overlay.hit_test(x, y)
                                if idx >= 0 and overlay._should_catch():
                                    overlay.clicked.emit(overlay.items[idx])
                                    down_at[0] = None
                                    return 1   # 吃掉这次点击
                        down_at[0] = None
                except Exception:
                    pass
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._hook_proc = proc   # 必须持有引用，否则会被 GC 回收导致崩溃
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, proc, None, 0)

    def remove_mouse_hook(self):
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._hook_proc = None

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, ev):
        if not self.items:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        font = QFont()
        font.setPixelSize(12)
        p.setFont(font)
        for i, it in enumerate(self.items):
            state = "hover" if i == self._hover else it.get("state", "flat")
            pal = palette_for(state, bool(it.get("dark")) and self.adaptive_palette)
            x, y, w, h = it["rect"]
            r = QRect(int(x), int(y), int(w), int(h))
            p.setPen(Qt.NoPen)
            p.setBrush(pal["fill"])
            p.drawRoundedRect(r, self.radius, self.radius)
            pen = QPen(pal["line"])
            pen.setWidthF(1.4 if not it.get("dark") else 1.6)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, self.radius, self.radius)
            # 下划线，让「这是一个可点的词」更明确
            pen.setWidthF(1.6 if not it.get("dark") else 2.0)
            p.setPen(pen)
            p.drawLine(r.left() + 1, r.bottom(), r.right() - 1, r.bottom())

        # hover 提示：不提示的话没人会知道要按住 Ctrl
        if self._hover >= 0 and self._clickable and self.click_modifier == "ctrl":
            it = self.items[self._hover]
            x, y, w, h = it["rect"]
            txt = ("Ctrl+点击  %s %s" % (it.get("label") or "",
                                        it.get("code") or "")).strip()
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(txt) + 16
            th = fm.height() + 9
            lx = min(max(int(x), 0), max(self.width() - tw, 0))
            ly = int(y) - th - 4
            if ly < 0:
                ly = int(y) + int(h) + 4
            box = QRect(int(lx), int(ly), int(tw), int(th))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(24, 28, 34, 234))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QColor(255, 255, 255))
            p.drawText(box.adjusted(8, 0, -8, 0),
                       Qt.AlignVCenter | Qt.AlignLeft, txt)
        p.end()

    def closeEvent(self, ev):
        self.remove_mouse_hook()
        super().closeEvent(ev)


class ActionPopup(QWidget):
    """点击高亮词后弹出的操作菜单（不抢焦点）"""

    chosen = Signal(str, object)

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                           | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet("""
            QWidget { background:#ffffff; border:1px solid #d8dce1; border-radius:8px; }
            QLabel#title { font-size:13px; font-weight:600; color:#1f2328; padding:9px 12px; }
            QLabel#code  { font-size:12px; color:#6b7280; }
            QWidget#item:hover { background:#f2f4f7; }
            QLabel#itemtext { font-size:13px; color:#1f2328; padding:9px 12px; }
            QWidget#promorow { background:#fff8ec; border:none; border-top:1px solid #f0e4c8;
                               border-radius:0 0 8px 8px; }
            QWidget#promorow:hover { background:#fdf1d7; }
            QLabel#promotext { font-size:12px; color:#8a5b16; border:none; }
            QLabel#promobtn  { font-size:12px; font-weight:600; color:#ffffff; background:#f08300;
                               border:none; border-radius:4px; padding:3px 10px; }
        """)
        self._actions = []
        self._item = None

    def build(self, item, actions):
        """actions: [(key, label)]"""
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout
        self._item = item

        old = self.layout()
        if old is not None:
            while old.count():
                child = old.takeAt(0)
                w = child.widget()
                if w:
                    w.deleteLater()
            QWidget().setLayout(old)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 9, 12, 9)
        t = QLabel(item.get("label", ""))
        t.setObjectName("title")
        c = QLabel(item.get("code", ""))
        c.setObjectName("code")
        hl.addWidget(t)
        hl.addStretch(1)
        hl.addWidget(c)
        root.addWidget(head)

        self._actions = actions
        for key, label in actions:
            row = QWidget()
            row.setObjectName("item")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            lb = QLabel(label)
            lb.setObjectName("itemtext")
            rl.addWidget(lb)
            row.mousePressEvent = (lambda e, k=key: self.chosen.emit(k, self._item))
            root.addWidget(row)

        # ---- 引流位（服务端后台可配，客户端 1h 缓存）----
        try:
            from . import promo as _promo
            p = _promo.get()
        except Exception:
            p = None
        if p:
            prow = QWidget()
            prow.setObjectName("promorow")
            pl = QHBoxLayout(prow)
            pl.setContentsMargins(12, 7, 12, 7)
            txt = "🔥 %s · %s" % (p.get("title", ""), p.get("desc", ""))
            plb = QLabel(txt.strip(" ·"))
            plb.setObjectName("promotext")
            plb.setCursor(Qt.PointingHandCursor)
            btn = QLabel(p.get("button", "了解更多"))
            btn.setObjectName("promobtn")
            btn.setCursor(Qt.PointingHandCursor)
            pl.addWidget(plb, 1)
            pl.addWidget(btn)
            prow.mousePressEvent = (lambda e: self.chosen.emit("__promo", None))
            root.addWidget(prow)
        self.adjustSize()
        return self

    def popup_at(self, screen_x, screen_y):
        self.move(int(screen_x), int(screen_y))
        self.show()
        self.raise_()
