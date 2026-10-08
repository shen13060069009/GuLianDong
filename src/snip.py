# -*- coding: utf-8 -*-
"""框选联动层（豆包划词插件式交互）

用户按住触发键（默认 Alt）+ 左键在屏幕任意位置框选一块区域，
松手后自动对该区域做 OCR → 匹配股票 → 在框选处弹出联动浮条。
不依赖聊天窗口监控：微信截图里的字、PDF、网页、任何能显示的东西都能框。

═══ 交互设计 ═══

    1. 触发：Alt + 左键拖拽（WH_MOUSE_LL 全局钩子检测）。
       框选期间鼠标事件被钩子「吃掉」，底下的窗口不会收到拖拽，
       也不会误选文字 / 误点按钮 —— 跟截图工具一个手感。
    2. 框选过程：全屏透明蒙层 + 蓝色选框实时跟随（显示像素尺寸）。
    3. 松手：钩子把选区坐标发给主程序 → 取屏 → OCR → 匹配 → 弹浮条。
    4. 浮条（SelectionPopup）：每只股票一行，右侧一排目标按钮
       （通达信 / 东财 / 同花顺 / 复制），点一下直接联动，两步并一步。
    5. 消失：点击浮条外部任意位置、或 20 秒无操作自动隐藏。

═══ 为什么用钩子而不是让 Qt 窗口收鼠标 ═══

    蒙层若设成可点击窗口，会抢走前台焦点、把聊天窗口顶下去（title bar
    变灰），松手后用户还要点回来。保持蒙层穿透 + 全局钩子自己消费事件，
    全程不抢焦点，和 highlight overlay 是同一套哲学。

═══ 实测坑 ═══

    * WH_MOUSE_LL 收到的是物理像素坐标（多屏时可能是负数），
      BitBlt 取屏也用物理像素，两边天然一致；只有 Qt move() 要注意 DPI，
      本项目沿用 highlight overlay 的约定（目标机 100% 缩放）。
    * 钩子回调里绝不能做耗时操作（OCR 一放进去整机鼠标就卡死），
      回调只更新几何 + 发信号，识别丢给工作线程。
    * SetWindowsHookExW 的 HOOKPROC 必须持有 Python 引用，否则被 GC
      回收后钩子回调进野指针直接崩进程（overlay.py 同款坑）。
"""
import ctypes
import ctypes.wintypes as wt

from PySide6.QtCore import (QAbstractNativeEventFilter, QObject, QPoint, QRect,
                            Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QLabel, QWidget

user32 = ctypes.WinDLL("user32", use_last_error=True)

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203   # 双击（第二次按下时送达）——划词选单词
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_XBUTTONDOWN = 0x020B   # 鼠标侧键（XButton1/2，靠 mouseData 高位字区分）
WM_XBUTTONUP = 0x020C

VK_MENU = 0x12      # Alt
VK_CONTROL = 0x11
VK_SHIFT = 0x10

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetSystemMetrics.restype = ctypes.c_int

# ---- 文字选取判定 ----
WM_NCHITTEST = 0x0084
HTCLIENT = 1
SMTO_ABORTIFHUNG = 0x0002
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.WindowFromPoint.restype = wt.HWND
user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
                                       wt.UINT, wt.UINT,
                                       ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = ctypes.c_size_t


def _hit_client(x, y):
    """该点是否在目标窗口的客户区（WM_NCHITTEST == HTCLIENT）。

    用途：区分「划词选文字」和「拖标题栏移动窗口 / 拖滚动条」——
    后两者的按下点落在非客户区，不应触发文字选取联动。
    用 SendMessageTimeout 而非 SendMessage：跨进程查询若目标忙，
    最多等 30ms 就放弃（LL 钩子回调绝不能久停，否则 Windows 会摘掉钩子）。
    查询失败宁可信其是客户区（宁可多识别一次，不可漏）。"""
    hwnd = user32.WindowFromPoint(wt.POINT(x, y))
    if not hwnd:
        return False
    res = ctypes.c_size_t(0)
    lparam = (x & 0xFFFF) | ((y & 0xFFFF) << 16)   # 负坐标（多屏）按 16 位补码打包
    ok = user32.SendMessageTimeoutW(hwnd, WM_NCHITTEST, 0, lparam,
                                    SMTO_ABORTIFHUNG, 30, ctypes.byref(res))
    if not ok:
        return True
    return res.value == HTCLIENT


def expand_select_rect(rect, dbl=False):
    """把用户选取的原始矩形扩成适合 OCR 取屏的矩形。

    拖拽划词：上下各放 8px（用户常只划到字的半高，直接裁会切掉字），     011
    高度不足 34px 补到 34（完整一行）；
    双击选词：以点击点为中心取 360×44 —— 覆盖那个词所在的整行，
    识别出的词由 matcher 决定，不需要精确贴着选中的词。
    最后夹到虚拟桌面内（多屏负坐标安全）。"""
    vx, vy, vw, vh = virtual_desktop()
    if dbl:
        c = rect.center()
        rect = QRect(c.x() - 180, c.y() - 22, 360, 44)
    else:
        rect = rect.adjusted(-6, -8, 6, 8)
        if rect.height() < 34:
            cy = rect.center().y()
            rect = QRect(rect.left(), cy - 17, rect.width(), 34)
    x = max(vx + 2, min(rect.left(), vx + vw - rect.width() - 2))
    y = max(vy + 2, min(rect.top(), vy + vh - rect.height() - 2))
    return QRect(x, y, rect.width(), rect.height())

# 触发方式注册表：start/end 事件 + 起手时要按住的模式键。
# 只提供三个双组合键（单 Alt / 中键 / 侧键已按产品要求裁掉，误触率高）；
# 每项的 name 用于托盘菜单与日志展示；set_trigger 运行时热切换无需重装钩子。
DEFAULT_TRIGGER = "ctrl_shift"
TRIGGERS = {
    "ctrl_alt":   {"start": WM_LBUTTONDOWN, "end": WM_LBUTTONUP,
                   "mods": (VK_CONTROL, VK_MENU), "name": "Ctrl+Alt+左键框选"},
    "ctrl_shift": {"start": WM_LBUTTONDOWN, "end": WM_LBUTTONUP,
                   "mods": (VK_CONTROL, VK_SHIFT), "name": "Ctrl+Shift+左键框选"},
    "shift_alt":  {"start": WM_LBUTTONDOWN, "end": WM_LBUTTONUP,
                   "mods": (VK_SHIFT, VK_MENU), "name": "Shift+Alt+左键框选"},
}


def _mods_down(mods):
    return all(user32.GetAsyncKeyState(m) & 0x8000 for m in mods)


def virtual_desktop():
    """整个虚拟桌面的 (x, y, w, h)，多屏 / 负坐标安全"""
    SM_X, SM_Y, SM_W, SM_H = 76, 77, 78, 79
    x = user32.GetSystemMetrics(SM_X)
    y = user32.GetSystemMetrics(SM_Y)
    w = user32.GetSystemMetrics(SM_W)
    h = user32.GetSystemMetrics(SM_H)
    return x, y, w, h


def grab_region(x, y, w, h):
    """BitBlt 抓屏幕上的一块矩形 → BGR→RGB ndarray。
    必须走 GetDC(NULL)：QScreen.grabWindow 拿不到分层窗口的合成结果。"""
    import win32gui
    import win32ui
    import numpy as np

    hdc = win32gui.GetDC(0)
    src = win32ui.CreateDCFromHandle(hdc)
    dc = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    dc.SelectObject(bmp)
    dc.BitBlt((0, 0), (w, h), src, (x, y), 0x00CC0020)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    arr = np.frombuffer(bits, dtype=np.uint8).reshape(
        (info["bmHeight"], info["bmWidth"], 4))[:, :, :3][:, :, ::-1].copy()
    win32gui.DeleteObject(bmp.GetHandle())
    dc.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(0, hdc)
    return arr


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]


# ============================================================ 框选蒙层
WM_SETCURSOR = 0x0020
IDC_CROSS = 32515
user32.LoadCursorW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadCursorW.restype = wt.HANDLE
_h_cross = user32.LoadCursorW(None, ctypes.c_void_p(IDC_CROSS))


class _CursorFilter(QAbstractNativeEventFilter):
    """框选光标 = 十字（截图工具惯例）。

    ⚠ 为什么是应用级原生事件过滤器而不是 QWidget.setCursor / nativeEvent：
      1. setCursor 依赖 Qt 的 enter 跟踪，而蒙层是"出现在光标底下"而非
         "光标进入蒙层"，enter 不触发，WM_SETCURSOR 落回类默认箭头；
      2. Qt 的 windowsProc 会先内部消化 WM_SETCURSOR/WM_MOUSEMOVE 等
         输入消息，QWidget.nativeEvent 未必收得到；
      3. SetSystemCursor 全局热替换也不行——已开的窗口在类注册时缓存了
         箭头句柄，对它们无效。
    过滤器在 Qt 派发前看到一切消息：蒙层可见期间（= 框选进行中，
    蒙层盖住整个桌面）对 WM_SETCURSOR 直接 SetCursor(十字) 并吃掉。
    """

    def __init__(self, band):
        super().__init__()
        self.band = band

    def nativeEventFilter(self, eventType, message):
        try:
            if eventType == b"windows_generic_MSG" and self.band.isVisible():
                msg = wt.MSG.from_address(int(message))
                if msg.message == WM_SETCURSOR:
                    user32.SetCursor(_h_cross)
                    return True
        except RuntimeError:
            pass    # 程序退出时 C++ 对象已析构，过滤器还挂在事件循环上
        return False


_cursor_filter = None


class RubberBand(QWidget):
    """全屏透明蒙层，画框选矩形。事件全靠钩子喂。"""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                               | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # 不能设 WA_TransparentForMouseEvents：蒙层必须可命中，
        # WM_SETCURSOR 才会发给本线程（过滤器才能拦到）。
        global _cursor_filter
        if _cursor_filter is None:
            _cursor_filter = _CursorFilter(self)
            from PySide6.QtCore import QCoreApplication
            QCoreApplication.instance().installNativeEventFilter(_cursor_filter)
        self._rect = None
        self._origin = None

    def start(self):
        x, y, w, h = virtual_desktop()
        self._rect = None
        self.setGeometry(x, y, w, h)
        self.show()
        self.raise_()

    def feed(self, x, y):
        """钩子喂来的当前鼠标位置（物理像素，屏幕坐标系）"""
        if self._origin is None:
            return
        self._rect = QRect(QPoint(*self._origin), QPoint(x + 1, y + 1)).normalized()
        self.update()

    def release_rect(self, x, y):
        if self._origin is None:
            return QRect()
        self._rect = QRect(QPoint(*self._origin), QPoint(x + 1, y + 1)).normalized()
        r = self._rect
        self._rect = None
        self._origin = None
        self.hide()
        return r

    def paintEvent(self, ev):
        p = QPainter(self)
        # 全屏压暗一层，让「正在框选」这个状态有明确的视觉反馈
        p.fillRect(self.rect(), QColor(0, 0, 0, 46))
        if self._rect is not None:
            # ⚠ _rect 是钩子喂来的【屏幕】坐标，而 painter 用【部件本地】坐标。
            #   蒙层盖住整个虚拟桌面时本地原点 ≠ 屏幕原点（多屏副屏在上/左时
            #   桌面原点是负数），不换算的话选框会画到别的显示器上——
            #   症状就是"压暗了但看不到框"。
            org = self.mapToGlobal(QPoint(0, 0))
            r = self._rect.translated(-org.x(), -org.y())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(37, 99, 235, 26))
            p.drawRect(r)
            pen = QPen(QColor(37, 99, 235), 2)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            # 角落显示尺寸，跟系统截图工具一致，用户能确认框选生效
            fm = QFont(p.font())
            fm.setPixelSize(12)
            p.setFont(fm)
            txt = "%d × %d" % (r.width(), r.height())
            tw = p.fontMetrics().horizontalAdvance(txt) + 12
            lx = r.left()
            ly = r.top() - 22
            if ly < 2:
                ly = r.bottom() + 4
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(24, 28, 34, 230))
            p.drawRoundedRect(QRect(lx, ly, tw, 18), 4, 4)
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRect(lx, ly, tw, 18), Qt.AlignCenter, txt)
        p.end()


# ============================================================ 联动浮条
class SelectionPopup(QWidget):
    """框选后弹出的操作浮条（豆包插件风格）

    每只股票一行：名称 + 代码在左，目标按钮在右，点一下直接联动。
    chosen(key, code) —— key 是目标 key（tdx/em/ths/...）或 __copy。
    """

    chosen = Signal(str, str)

    STYLE = """
        QWidget { background:#ffffff; }
        QFrame#card { background:#ffffff; border:1px solid #dfe3e8;
                      border-radius:10px; }
        QLabel#name { font-size:13px; font-weight:600; color:#1f2328; }
        QLabel#code { font-size:12px; color:#8a919c; }
        QLabel#tag  { font-size:11px; color:#b07a1e;
                      background:#fdf3e0; border-radius:3px; padding:1px 5px; }
        QLabel#count { font-size:11px; color:#8a919c; }
        QLabel#pin  { font-size:13px; color:#8a919c;
                      border-radius:4px; padding:1px 7px; }
        QLabel#pin:hover { background:#eef2f7; }
        QLabel#pin[pinned="true"] { color:#2563eb; background:#e8effd; }
        QLabel#btn  { font-size:12px; color:#1f2328;
                      border:1px solid #d8dce1; border-radius:5px;
                      padding:3px 9px; }
        QLabel#btn:hover { background:#2563eb; color:#ffffff;
                           border-color:#2563eb; }
        QWidget#row:hover { background:#f5f7fa; }
        QLabel#empty { font-size:12px; color:#8a919c; padding:10px 14px; }
        QWidget#promorow { background:#fff8ec; border:1px solid #f0e4c8; border-radius:6px; }
        QWidget#promorow:hover { background:#fdf1d7; }
        QLabel#promotext { font-size:12px; color:#8a5b16; background:transparent; border:none; }
        QLabel#promobtn  { font-size:12px; font-weight:600; color:#ffffff; background:#f08300;
                           border:none; border-radius:4px; padding:3px 10px; }
    """

    MAX_ROWS = 10

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                               | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet(self.STYLE)
        self._hide_timer = QTimer(self)
        self._hide_timer.setInterval(20000)
        self._hide_timer.timeout.connect(self.hide)
        self._rows = []
        # 锁定置顶：锁定后不自动隐藏、点外部不消失、点按钮不收起，
        # 用户可以连续点开多只股票对比（2026-10-07 用户需求）
        self._pinned = False
        self._pin_btn = None

    # ---- 锁定置顶 ----
    def _toggle_pin(self, _e):
        self.set_pinned(not self._pinned)

    def set_pinned(self, on):
        self._pinned = bool(on)
        if self._pin_btn is not None:
            self._pin_btn.setProperty("pinned", self._pinned)
            st = self._pin_btn.style()
            st.unpolish(self._pin_btn)
            st.polish(self._pin_btn)
            self._pin_btn.setToolTip(
                "已锁定：浮条常驻置顶，可连续点开多只对比。再点一次解锁"
                if self._pinned else
                "锁定置顶：点按钮后浮条不消失，可连续点开多只对比")
        if on:
            self._hide_timer.stop()
        elif self.isVisible():
            self._hide_timer.start()

    # ---- 构建 ----
    def show_results(self, rows, actions, near_rect):
        """        rows: [{"name","code","tag"}]  actions: [(key,label)]
        near_rect: 选区 QRect（屏幕坐标），浮条出现在其下方"""
        self._clear()
        self._pinned = False          # 新一次识别，锁定态复位
        self._pin_btn = None
        card = QWidget()
        card.setObjectName("card")
        from PySide6.QtWidgets import QVBoxLayout
        lay = QVBoxLayout(card)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(2)

        if not rows:
            empty = QLabel("未识别到股票，换个区域试试")
            empty.setObjectName("empty")
            lay.addWidget(empty)
        else:
            if len(rows) >= 2:
                # 多只命中：顶部一条工具栏 —— 左侧数量提示，右侧锁定按钮。
                # 锁定后浮条常驻置顶，点「看通达信/看东财」不消失，
                # 用户可以逐只点开对比，不用重新框选。
                from PySide6.QtWidgets import QHBoxLayout
                bar = QWidget()
                hl = QHBoxLayout(bar)
                hl.setContentsMargins(8, 2, 8, 0)
                hl.setSpacing(4)
                cnt = QLabel("识别到 %d 只" % len(rows))
                cnt.setObjectName("count")
                hl.addWidget(cnt)
                hl.addStretch(1)
                from PySide6.QtWidgets import QLabel as _L
                pin = _L("📌")
                pin.setObjectName("pin")
                pin.setCursor(Qt.PointingHandCursor)
                pin.setToolTip(
                    "锁定置顶：点按钮后浮条不消失，可连续点开多只对比")
                pin.mousePressEvent = self._toggle_pin
                self._pin_btn = pin
                hl.addWidget(pin)
                lay.addWidget(bar)
            for r in rows[:self.MAX_ROWS]:
                lay.addWidget(self._row(r, actions))
            if len(rows) > self.MAX_ROWS:
                more = QLabel("…还有 %d 只未列出" % (len(rows) - self.MAX_ROWS))
                more.setObjectName("empty")
                lay.addWidget(more)

        # ---- 引流位（服务端后台可配）----
        try:
            from . import promo as _promo
            p = _promo.get()
        except Exception:
            p = None
        if p:
            from PySide6.QtWidgets import QHBoxLayout as _QH
            prow = QWidget()
            prow.setObjectName("promorow")
            pl = _QH(prow)
            pl.setContentsMargins(10, 6, 6, 6)
            pl.setSpacing(6)
            txt = "🔥 %s · %s" % (p.get("title", ""), p.get("desc", ""))
            plb = QLabel(txt.strip(" ·"))
            plb.setObjectName("promotext")
            plb.setCursor(Qt.PointingHandCursor)
            btn = QLabel(p.get("button", "了解更多"))
            btn.setObjectName("promobtn")
            btn.setCursor(Qt.PointingHandCursor)
            pl.addWidget(plb, 1)
            pl.addWidget(btn)
            prow.mousePressEvent = (lambda e: self.chosen.emit("__promo", ""))
            lay.addSpacing(3)
            lay.addWidget(prow)

        outer = self.layout()
        if outer is None:
            from PySide6.QtWidgets import QVBoxLayout as _V
            outer = _V(self)
            outer.setContentsMargins(8, 8, 8, 12)   # 留边给阴影
        outer.addWidget(card)
        self.adjustSize()
        self._paint_shadow = True

        # ---- 位置：选区下方 8px，贴边翻转、不出虚拟桌面 ----
        vx, vy, vw, vh = virtual_desktop()
        w, h = self.width(), self.height()
        px = near_rect.left()
        py = near_rect.bottom() + 8
        if px + w > vx + vw - 8:
            px = vx + vw - 8 - w
        if py + h > vy + vh - 8:
            py = near_rect.top() - h - 8        # 下方放不下 → 放上方
        px = max(vx + 8, min(px, vx + vw - w - 8))
        py = max(vy + 8, min(py, vy + vh - h - 8))
        self.move(px, py)
        self.show()
        self.raise_()
        self._hide_timer.start()
        return self

    def _clear(self):
        lay = self.layout()
        if lay is not None:
            while lay.count():
                it = lay.takeAt(0)
                w = it.widget()
                if w:
                    w.deleteLater()

    def _row(self, r, actions):
        from PySide6.QtWidgets import QHBoxLayout, QFrame, QLabel
        row = QFrame()
        row.setObjectName("row")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(8, 4, 6, 4)
        rl.setSpacing(6)

        name = QLabel(r["name"])
        name.setObjectName("name")
        rl.addWidget(name)
        code = QLabel(r["code"])
        code.setObjectName("code")
        rl.addWidget(code)
        if r.get("tag"):
            tag = QLabel(r["tag"])
            tag.setObjectName("tag")
            rl.addWidget(tag)
        rl.addStretch(1)

        for key, label in actions:
            b = QLabel(label)
            b.setObjectName("btn")
            b.setCursor(Qt.PointingHandCursor)
            b.mousePressEvent = (lambda e, k=key, c=r["code"]:
                                 self.chosen.emit(k, c))
            rl.addWidget(b)
        return row

    def hide(self):
        self._hide_timer.stop()
        self._pinned = False        # 关掉后下一次弹出不继承锁定态
        super().hide()


# ============================================================ 控制器
class SnipController(QObject):
    """全局钩子状态机：检测触发手势 → 蒙层跟随 → 发出选区

    snipped(QRect) —— 框选完成（屏幕物理像素）
    text_selected(QRect) —— 文字选取完成（普通划词松手 / 双击选词，
                            已扩成取屏矩形）
    顺带负责浮条的「点外面消失」判定（同一个钩子，省一次安装）。
    """

    snipped = Signal(QRect)
    text_selected = Signal(QRect)

    def __init__(self, popup, trigger=DEFAULT_TRIGGER):
        super().__init__()
        self.popup = popup
        self.trigger = TRIGGERS.get(trigger, TRIGGERS[DEFAULT_TRIGGER])
        self.enabled = True
        # 文字选取联动开关（独立于框选开关，托盘各自勾选）
        self.select_enabled = True
        self._sel_origin = None     # 划词起点（LMB down 时记录）
        self.band = RubberBand()
        self._active = False
        self._origin = None
        self._hook = None
        self._hook_proc = None
        self._install()

    # ---- 钩子 ----
    def _install(self):
        ctrl = self

        @HOOKPROC
        def proc(nCode, wParam, lParam):
            if nCode == 0:
                try:
                    info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    return ctrl._handle(wParam, info.pt.x, info.pt.y, info.mouseData)
                except Exception:
                    pass
            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._hook_proc = proc    # 必须持有引用，否则 GC 回收 → 崩溃
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, proc, None, 0)

    def _hit(self, t, which, wParam, data):
        """wParam 是否命中 t["start"/"end"]；X 侧键再校验 mouseData 高位字"""
        if wParam != t[which]:
            return False
        xd = t.get("xdata")
        if xd is None:
            return True
        return ((data >> 16) & 0xFFFF) == xd

    def _handle(self, wParam, x, y, data=0):
        t = self.trigger
        # ---- 框选中：吃掉拖拽相关事件，喂给蒙层 ----
        if self._active:
            if self._hit(t, "end", wParam, data):
                self._finish(x, y)
                return 1
            if wParam == WM_MOUSEMOVE:
                self.band.feed(x, y)
                # ⚠ move 必须放行（不能 return 1 吃掉）：吃掉后系统光标
                #   根本不动，松手坐标还是起点，选区尺寸=0 被当误触丢弃。
                #   放行的副作用只是底下窗口收到无按键的 mousemove，无害。
                return user32.CallNextHookEx(None, 0, wParam, 0)
            if self._hit(t, "start", wParam, data):
                self.band.feed(x, y)
                return 1
            return user32.CallNextHookEx(None, 0, wParam, 0)  # 其余放行

        # ---- 浮条可见时：点外部即消失（锁定置顶时除外）----
        if self.popup.isVisible() and \
                not getattr(self.popup, "_pinned", False) and \
                wParam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN):
            g = self.popup.frameGeometry()
            if not g.contains(x, y):
                self.popup.hide()

        # ---- 文字选取联动：只观察，绝不吞事件 ----
        #   用户在做【真实】的划词/选词，事件必须原样送达目标窗口，
        #   否则选取本身失效。我们只记录按下点、松手时另行取屏 OCR。
        if wParam == WM_LBUTTONDOWN:
            in_popup = self.popup.isVisible() and \
                self.popup.frameGeometry().contains(x, y)
            # 浮条上的按下不作为选取起点（防「识别浮条自己」死循环）
            self._sel_origin = None if in_popup else (x, y)
        elif wParam == WM_LBUTTONUP and self._sel_origin is not None:
            ox, oy = self._sel_origin
            self._sel_origin = None
            self._maybe_select(ox, oy, x, y, dbl=False)
        elif wParam == WM_LBUTTONDBLCLK and self.select_enabled:
            self._maybe_select(x, y, x, y, dbl=True)

        # ---- 起手：触发键按住 + 起手键按下 ----
        if self.enabled and self._hit(t, "start", wParam, data) \
                and _mods_down(t["mods"]):
            self._begin(x, y)
            return 1
        return user32.CallNextHookEx(None, 0, wParam, 0)

    def _maybe_select(self, ox, oy, x, y, dbl):
        """判定一次按下/松手是否构成有效文字选取，是则发 text_selected。

        过滤器（从宽到严）：
        1. 起点或终点在自家浮条上 → 跳过（点浮条按钮是 DOWN+UP，会走到这）；
        2. 拖拽：水平位移 ≥10px 且 dx>dy —— 文字选取是横向的，
           竖向拖拽=拖滚动条，直接排除；
        3. 松手点必须是客户区（WM_NCHITTEST）——排除拖标题栏移窗口、
           拖滚动条、拖菜单等非文字区。
        """
        if not self.select_enabled:
            return
        g = self.popup.frameGeometry()
        if self.popup.isVisible() and (g.contains(ox, oy) or g.contains(x, y)):
            return
        if not dbl:
            dx, dy = abs(x - ox), abs(y - oy)
            if dx < 10 or dx <= dy:
                return
        if not _hit_client(x, y):
            return
        if dbl:
            rect = expand_select_rect(QRect(x - 8, y - 8, 17, 17), dbl=True)
        else:
            rect = expand_select_rect(
                QRect(QPoint(min(ox, x), min(oy, y)),
                      QPoint(max(ox, x) + 1, max(oy, y) + 1)).normalized())
        self.text_selected.emit(rect)

    def _begin(self, x, y):
        self.popup.hide()
        self._origin = (x, y)
        self._active = True
        self._sel_origin = None     # 框选起手吞掉了 down，不作为划词起点
        self.band._origin = (x, y)
        self.band.start()

    def _finish(self, x, y):
        self._active = False
        rect = self.band.release_rect(x, y)
        # 太小视为误触（比如想 Alt+单击），不弹浮条
        if rect.width() >= 12 and rect.height() >= 12:
            self.snipped.emit(rect)

    def set_trigger(self, name):
        """运行时热切换触发手势（钩子不重装，_handle 实时读 self.trigger）。

        若切换时正在框选中，直接中止本次拖拽——旧手势的 end 键不会再被
        识别，蒙层会卡在屏幕上。
        """
        if self._active:
            self._active = False
            self.band.hide()
        self.trigger = TRIGGERS.get(name, TRIGGERS[DEFAULT_TRIGGER])

    def set_enabled(self, on):
        self.enabled = bool(on)
        if not on:
            self.popup.hide()
            if self._active:
                self._active = False
                self.band.hide()

    def set_select_enabled(self, on):
        """文字选取联动开关（独立于框选）"""
        self.select_enabled = bool(on)
        if not on:
            self._sel_origin = None

    def shutdown(self):
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._hook_proc = None
        self.band.hide()
        self.popup.hide()
