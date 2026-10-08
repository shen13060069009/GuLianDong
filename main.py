# -*- coding: utf-8 -*-
"""股联动 GuLianDong —— 桌面股票识别联动工具

功能：
    划词 / 框选识别 微信 / 飞书 / 钉钉 / 企业微信 聊天窗口里的股票名称，
    点击即可在 通达信 / 东方财富 / 同花顺 中定位到该股。官网 gldong.com。

架构（对应 P0 验证过的分层）：
    窗口追踪(capture) → 变化门控 → OCR(ocr) → 匹配(matcher) → 高亮(overlay)
                                                            ↓ 点击
                                                        联动(linkage)

运行：
    python main.py            正常启动（带托盘图标）
    python main.py --once     只跑一轮，打印结果后退出（自检用）
    python main.py --selftest 逐层自检
"""
import argparse
import ctypes
import json
import os
import queue
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import linkage
from src.capture import ChangeGate, WindowWatcher, find_windows, grab_window, window_info
from src.paths import app_dir, config_path

# 打包后 ROOT 必须是 exe 所在目录，而不是 _MEIPASS（临时解压目录）——
# 否则 config.json / data/ 会被写到临时目录里，用户改配置文件永远不生效。
ROOT = app_dir()
CONFIG_PATH = config_path()

DEFAULT_CONFIG = {
    "sources": ["Weixin.exe", "WeChat.exe", "DingTalk.exe", "Feishu.exe", "Lark.exe", "WXWork.exe"],
    # 自动识别监视层开关：false = 不轮询聊天窗口、不画高亮（用户 2026-10-07 定：
    # 只要框选 + 划词）。后台代码保留，改回 true 即可恢复。
    "auto_recognize": False,
    "targets": ["tdx", "em", "ths"],   # 点击后弹出的操作项（tdx=通达信 em=东财客户端
                                       # ths=同花顺）；客户端没开的会自动从菜单里省掉。
                                       # 东财网页(emweb) 已按用户要求移除
    "poll_interval": 0.25,            # 窗口轮询间隔（秒）
    "change_thresh": 0.002,           # 帧差阈值（实测空转噪声 0.001%）
    "ocr": {
        "gpu": "auto",
        "det_limit": 900,             # 初始 det 输入长边，运行时按窗口自适应
        "det_coef": 0.85,             # 自适应系数：客户区长边 × coef
        "det_range": [640, 2400],     # 自适应结果的下上限（2400 是实测出来的：
                                      # 上限 2000 会让 2576px 密集列表漏 4 只股票）
        "rec_batch": 24,
        "use_cls": False,
        "cpu_threads": 0,             # CPU 推理线程数；0 = 按核数自动
                                      # （小机器 2 / 中等 3 / 大机器 4）。
                                      # 不限线程的话单次识别会把整机 CPU 拉满，
                                      # 表现为「每来一条消息机器卡一下」。
                                      # GPU(DirectML) 路径下此项无效。
    },
    "match": {
        # 弱词（机器人/老百姓/向日葵…）策略：
        #   context —— 需佐证才报（同段有强命中，或出现行情语义词），默认
        #   drop    —— 一律不报（最干净，会漏）
        #   keep    —— 一律报（全量召回，误报最多）
        "weak_mode": "context",
    },
    "overlay": {
        # 聊天窗口不在前台时隐藏高亮层，避免高亮框浮在盖住它的别的程序上面
        "only_when_foreground": True,
        # 按高亮处的背景明暗自动切换配色：微信 4.x 是暗色主题，
        # 固定用浅底配色（32/255 透明度）在深底上等于看不见
        "adaptive_palette": True,
        # 点击手势：
        #   ctrl  —— 只有按住 Ctrl 单击才拦截并弹菜单 ★默认
        #   plain —— 单击就拦（会挡掉聊天窗口里的普通点击）
        #   off   —— 完全不拦，纯展示
        "click_modifier": "ctrl",
    },
    "linkage": {
        "prefer_background": True,    # 优先用后台静默方式（通达信 UWM_STOCK）
    },
    "snip": {
        # 框选联动（豆包划词插件式交互）：
        # 按住触发键框选屏幕任意区域 → 松手自动 OCR → 在框选处弹出联动浮条。
        # 不限于聊天窗口，任何能显示股票名的地方都能框。
        "enabled": True,
        # 触发手势（托盘菜单「框选手势」可运行时热切换，选择会写回这里）：
        #   ctrl_alt = Ctrl+Alt+左键拖拽     ctrl_shift = Ctrl+Shift+左键拖拽（默认）
        #   shift_alt = Shift+Alt+左键拖拽
        "trigger": "ctrl_shift",
        # 文字选取联动（豆包 AI 划词式）：普通划词 / 双击选词松手后自动抓取
        # 选区识别，命中股票才在选取处弹浮条；未命中保持安静不弹窗。
        # 托盘「文字选取联动」可开关，选择写回这里。
        "select": True,
        # 浮条无操作自动隐藏秒数
        "auto_hide": 20,
    },
}


# StockOCR.__init__ 认识的关键字（config.json 里另外两个是自适应参数，不能展开传进去）
_OCR_KWARGS = ("gpu", "det_limit", "rec_batch", "use_cls", "cpu_threads")


def make_ocr(cfg, verbose=True):
    """按 config 构造 OCR 引擎，并把自适应参数挂到实例上"""
    from src.ocr import StockOCR
    o = cfg.get("ocr", {})
    ocr = StockOCR(**{k: o[k] for k in _OCR_KWARGS if k in o}, verbose=verbose)
    return configure_ocr(ocr, cfg)


def make_matcher(cfg, verbose=True):
    from src.matcher import build_default
    return build_default(verbose=verbose,
                         weak_mode=cfg.get("match", {}).get("weak_mode", "context"))


def configure_ocr(ocr, cfg):
    """把 config.json 里的自适应参数挂到 OCR 实例上"""
    o = cfg.get("ocr", {})
    ocr.det_coef = float(o.get("det_coef", 0.85))
    rng = o.get("det_range") or [640, 2000]
    ocr.det_range = (int(rng[0]), int(rng[1]))
    return ocr


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception as e:
            print("[配置] 读取失败，使用默认值: %s" % e)
    else:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
    return cfg


# ============================================================ 识别流水线
def _local_luma(arr, rect):
    """采样高亮框所在区域的背景亮度（0-255）。

    为什么要这一步：微信 4.x 默认暗色主题（实测帧灰度均值 52.7），
    高亮层用的是 alpha=32 的半透明填充 + 深色描边，在深底上等于看不见。
    必须按背景明暗切换配色，不能写死一套。
    """
    if arr is None:
        return 255
    x, y, w, h = rect
    sub = arr[max(y, 0):y + h, max(x, 0):x + w]
    if sub.size == 0:
        return 255
    return float(sub.mean())


def match_boxes(boxes, matcher, arr=None, dark_thresh=110):
    """OCR 文本框 → 高亮项。字内偏移按字数线性折算（不改动 OCR 结果）。

    arr 传入时会顺带测每处高亮的背景亮度，标记 dark，供覆盖层选配色。
    """
    items = []
    for b in boxes:
        n = max(len(b.text), 1)
        for m in matcher.match(b.text):
            x0 = b.x0 + int(b.w * m.start / n)
            x1 = b.x0 + int(b.w * m.end / n)
            cand = m.primary
            rect = (max(x0 - 1, 0), max(b.y0 - 3, 0), x1 - x0 + 3, b.h + 6)
            items.append({
                "rect": rect,
                "state": "concept" if m.layer == "L3" else "flat",
                "label": cand.name if cand else m.surface,
                "code": cand.code if cand else "",
                "layer": m.layer,
                "kind": m.kind,
                "weak": m.weak,
                "ambiguous": m.ambiguous,
                "candidates": [(c.code, c.name) for c in m.candidates],
                "match": m,
                "dark": _local_luma(arr, rect) < dark_thresh,
            })
    return items


def recognize_and_match(ocr, matcher, arr):
    """纯函数版：不做任何自适应调参，用当前 OCR 参数识别并匹配。

    探针用它做「固定 det 档位」的对照实验；analyze_window 自适应后也走这里。
    """
    return match_boxes(ocr.recognize(arr), matcher, arr)


def analyze_window(info, ocr, matcher, arr=None):
    """对一个窗口做一轮：取屏 → （自适应 det）→ OCR → 匹配 → 高亮项"""
    if arr is None:
        arr = grab_window(info)
    if arr is None or arr.size == 0:
        return []

    # det_limit 必须随窗口尺寸自适应：窗口越窄还按大值跑纯属浪费，窗口越宽
    # 还按 640 缩下去小字会被压没（实测 2564px 宽的通达信窗口在 det640 下几乎全废）。
    #
    # 但也不能取太激进：早期按「窗口宽 × 1.2」算，1325px 的飞书被顶到 1590px，
    # 端到端稳态度 1187ms；降到 0.85 后 953ms。系数取多少见 probe/p21 的跨帧扫描。
    coef = getattr(ocr, "det_coef", 0.85)
    lo, hi = getattr(ocr, "det_range", (640, 2000))
    long_side = max(info.width, info.height)
    want = max(lo, min(hi, int(long_side * coef)))
    if abs(want - ocr.det_limit) > 120:
        ocr.reconfigure(det_limit=want)

    return recognize_and_match(ocr, matcher, arr)


def snip_recognize(ocr, matcher, arr):
    """框选区域的识别：小图 → 放大到合适 det 档 → OCR → 匹配。

    为什么用独立 OCR 实例（见 run_gui 里的 SnipWorker）：窗口轮询会把
    det_limit 调到 2000+，框选的小区域若共用一个引擎，每次都要重建引擎
    （reconfigure 开销数百 ms），窗口识别和框选来回打架。
    """
    import numpy as np
    if arr is None or arr.size == 0:
        return []
    long_side = max(arr.shape[0], arr.shape[1])
    # 小区域放大一点跑 det，聊天小字（12~16px）才能被检出来；
    # 上限压 2000，防止用户框了整屏导致识别巨慢
    want = int(np.clip(long_side * 1.4, 640, 2000))
    if abs(want - ocr.det_limit) > 60:
        ocr.reconfigure(det_limit=want)
    return match_boxes(ocr.recognize(arr), matcher, arr)


# ============================================================ 台账打印
def print_items(info, items):
    if not items:
        return
    print("\n[%s] %dx%d @(%d,%d) 命中 %d 处"
          % (info.app, info.width, info.height, info.client[0], info.client[1], len(items)))
    for it in items:
        extra = " (+%d候选)" % (len(it["candidates"]) - 1) if it["ambiguous"] else ""
        print("   %-12s %-8s %-10s %s%s"
              % (it["label"], it["code"], it["layer"], it["rect"], extra))


# ============================================================ 自检
def selftest(cfg):
    print("=" * 92)
    print("股联动 GuLianDong 逐层自检")
    print("=" * 92)

    print("\n[1] 窗口追踪层")
    wins = find_windows(cfg["sources"])
    if not wins:
        print("    未发现聊天客户端窗口")
    for w in wins:
        print("    %s" % w)

    print("\n[2] OCR 引擎")
    ocr = make_ocr(cfg, verbose=True)

    print("\n[3] 匹配引擎")
    t0 = time.perf_counter()
    matcher = make_matcher(cfg, verbose=True)
    print("    构建耗时 %.0f ms" % ((time.perf_counter() - t0) * 1000))
    probe = "中芯国际 600519 gzmt 光模块 中国平安"
    print("    冒烟测试 %r → %s" % (probe, matcher.match(probe)))
    for s in ("我刚问了机器人，它说不知道", "机器人板块爆发，埃斯顿涨停"):
        print("    弱词样例 %r → %s" % (s, matcher.match(s)))

    print("\n[4] 联动层")
    for key, label, state in linkage.available_targets():
        print("    %-8s %-16s %s" % (key, label, state))
    print("    编码验证 600519 → %d，000001 → %d"
          % (linkage.tdx_encode("600519"), linkage.tdx_encode("000001")))

    print("\n[5] 实窗识别")
    for w in wins[:3]:
        arr = grab_window(w)
        if arr is None:
            print("    %s 取屏失败" % w.app)
            continue
        t0 = time.perf_counter()
        items = analyze_window(w, ocr, matcher, arr)
        dt = (time.perf_counter() - t0) * 1000
        print("    %s  %.0f ms  命中 %d" % (w.app, dt, len(items)))
        print_items(w, items)


def run_once(cfg, seconds=6.0):
    print("=" * 92)
    print("股联动 GuLianDong —— 单轮采样（%.0f 秒）" % seconds)
    print("=" * 92)
    ocr = make_ocr(cfg, verbose=True)
    matcher = make_matcher(cfg, verbose=True)

    watcher = WindowWatcher(proc_names=cfg["sources"],
                            poll_interval=cfg["poll_interval"])
    seen = {}
    t_end = time.time() + seconds
    total = 0
    while time.time() < t_end:
        changed = watcher.poll_once()
        for hwnd, (info, arr) in changed.items():
            items = analyze_window(info, ocr, matcher, arr)
            total += len(items)
            print_items(info, items)
        time.sleep(cfg["poll_interval"])
    print("\n采样结束：共触发 %d 次识别，累计命中 %d 处" % (len(seen), total))


# ============================================================ 桌面截屏
def grab_desktop():
    """抓整个虚拟桌面（含我们叠上去的分层窗口）。

    为什么不用 QScreen.grabWindow：Qt 在 Windows 上拿不到 WS_EX_LAYERED
    的合成结果（我们自己的高亮层就是分层窗口，会被漏掉）。必须走
    GetDC(NULL) + BitBlt —— 它拿的是 DWM 合成后的画面。
    """
    import ctypes as _c
    import win32gui
    import win32ui
    import numpy as np

    x = _c.windll.user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    y = _c.windll.user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    w = _c.windll.user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    h = _c.windll.user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN

    hdc = win32gui.GetDC(0)
    src = win32ui.CreateDCFromHandle(hdc)
    dc = src.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src, w, h)
    dc.SelectObject(bmp)
    dc.BitBlt((0, 0), (w, h), src, (x, y), 0x00CC0020)   # SRCCOPY
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    arr = np.frombuffer(bits, dtype=np.uint8).reshape(
        (info["bmHeight"], info["bmWidth"], 4))[:, :, :3][:, :, ::-1].copy()
    win32gui.DeleteObject(bmp.GetHandle())
    dc.DeleteDC()
    src.DeleteDC()
    win32gui.ReleaseDC(0, hdc)
    return arr, (x, y)


# ============================================================ GUI 主程序
def run_gui(cfg, auto_exit=None, shot=None):
    from PySide6.QtCore import (QObject, QThread, QTimer, Signal, Qt)
    from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import (QApplication, QDialog, QLabel,
                                   QMenu, QSystemTrayIcon, QVBoxLayout,
                                   QWidget)

    from src import licensing
    from src import promo
    from src.matcher import build_default
    from src.ocr import StockOCR
    from src.overlay import ActionPopup, Overlay
    from src import snip as snip_mod

    lic = licensing.check()

    class AboutDialog(QDialog):
        """关于 + 开源信息 + 自愿赞助窗口。

        免费开源版没有激活码 / 联网校验，这里只做三件事：
            1) 版本与开源协议信息（MIT + 仓库地址）
            2) 本机信息（机器码仅供用户自查，不再用于任何门控）
            3) 可选的自愿赞助入口（爱发电，赞助纯属自愿，不影响任何功能）
        """

        def __init__(self):
            super().__init__(None)
            self.setWindowTitle("关于 · 股联动 GuLianDong")
            self.setMinimumWidth(440)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(22, 20, 22, 18)
            lay.setSpacing(10)

            head = QLabel(
                "<b style='font-size:16px'>%s</b> v%s<br>"
                "<span style='color:#667085'>%s</span>"
                % (licensing.APP_NAME, licensing.APP_VERSION,
                   licensing.APP_TAGLINE))
            lay.addWidget(head)

            badge = QLabel(
                "<span style='background:#e8f6ee;color:#0a7a4a;"
                "border-radius:4px;padding:3px 9px;font-size:12px;'>"
                "✔ 免费开源 · 全部功能不限时、不限机器、无需激活码</span>")
            lay.addWidget(badge)

            info = QLabel(
                "本软件基于 <b>%s</b> 协议开源，源码可自由查看、修改、分发。<br>"
                "识别完全在本机运行，聊天内容与截图<b>不上传任何服务器</b>。<br>"
                "<span style='color:#98a2b3;font-size:11px'>"
                "机器码（仅用于问题排查，不作任何门控）：%s</span>"
                % (licensing.APP_LICENSE, licensing.machine_display()))
            info.setWordWrap(True)
            lay.addWidget(info)

            mid_row = QLabel(
                "本机机器码：<b style='font-family:Consolas'>%s</b>"
                % licensing.machine_display())
            mid_row.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(mid_row)

            from PySide6.QtWidgets import QPushButton, QHBoxLayout
            brow = QHBoxLayout()
            b_home = QPushButton("官网首页")
            b_home.clicked.connect(
                lambda: promo.open_url(licensing.APP_HOMEPAGE))
            b_repo = QPushButton("GitHub 源码")
            b_repo.clicked.connect(
                lambda: promo.open_url(licensing.APP_REPO))
            brow.addWidget(b_home)
            brow.addWidget(b_repo)
            lay.addLayout(brow)

            # ---- 自愿赞助（非强制，赞助/不赞助功能完全一致）----
            tip = QLabel(
                "<span style='color:#667085'>项目完全免费、不设任何付费墙。"
                "如果它帮到了你，可以考虑自愿赞助支持后续维护 —— "
                "赞助与否不影响任何功能。</span>")
            tip.setWordWrap(True)
            lay.addWidget(tip)
            self.donate_lbl = QLabel()
            self.donate_lbl.setWordWrap(True)
            lay.addWidget(self.donate_lbl)

            b_copy = QPushButton("复制本机机器码")
            self.status_lbl = QLabel("")
            self.status_lbl.setStyleSheet("color:#667085")
            b_copy.clicked.connect(
                lambda: (QApplication.clipboard().setText(
                    licensing.machine_display()),
                    self.status_lbl.setText("机器码已复制")))
            lay.addWidget(b_copy)
            lay.addWidget(self.status_lbl)

            # ---- 引流位（服务端后台可配；拉不到就不显示）----
            from PySide6.QtWidgets import QLabel as _PL
            p = promo.get()
            if p:
                self.promo_lbl = _PL(
                    "🔥 <a style='color:#f08300;text-decoration:none' href='#'>"
                    "<b>%s</b> · %s → %s</a>"
                    % (p.get("title", ""), p.get("desc", ""),
                       p.get("button", "了解更多")))
                self.promo_lbl.setStyleSheet(
                    "background:#fff8ec;border:1px solid #f0e4c8;"
                    "border-radius:6px;padding:7px 10px;font-size:12px;")
                self.promo_lbl.setCursor(Qt.PointingHandCursor)
                url = p["url"]
                self.promo_lbl.linkActivated.connect(
                    lambda _=None, u=url: promo.open_url(u))
                lay.addWidget(self.promo_lbl)

        def refresh_donate(self, cfgd):
            """服务端下发的赞助链接（拉不到就不显示，不硬编码任何收款码）。"""
            if not cfgd:
                self.donate_lbl.setText("")
                return
            self.donate_lbl.setText(
                "<a style='color:#f08300;text-decoration:none' href='#'>"
                "<b>%s</b> · %s → %s</a>"
                % (cfgd.get("title", ""), cfgd.get("desc", ""),
                   cfgd.get("button", "支持一下")))
            url = cfgd["url"]
            self.donate_lbl.linkActivated.connect(
                lambda _=None, u=url: promo.open_url(u))
            self.donate_lbl.setCursor(Qt.PointingHandCursor)

    class SnipWorker(QThread):
        """框选识别工作线程。

        为什么不复用主 Worker 的 OCR 引擎：两者会把同一个引擎的
        det_limit 拉向不同档位（窗口 2000+ / 框选 640~2000 来回跳），
        reconfigure 一次重建引擎数百 ms，来回打架。
        所以框选单独养一个引擎（模型只有十几 MB，内存代价可忽略），
        matcher 词典只读共享。
        """
        done = Signal(object, list, str)      # (选区QRect, 高亮项, 模式snip/select)

        def __init__(self, ocr, matcher):
            super().__init__()
            self.ocr = ocr
            self.matcher = matcher
            self.q = queue.Queue()
            self._running = True

        def submit(self, rect, arr, mode="snip"):
            self.q.put((rect, arr, mode))

        def run(self):
            while self._running:
                try:
                    rect, arr, mode = self.q.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    items = snip_recognize(self.ocr, self.matcher, arr)
                    if mode == "select" and not items and arr.size:
                        # 划词常选中高亮态文字（白字蓝底），OCR 可能认不出；
                        # 无命中时用反色图重试一次，只多花一次小图识别
                        items = snip_recognize(self.ocr, self.matcher, 255 - arr)
                    self.done.emit(rect, items, mode)
                except Exception as e:
                    print("[框选] 识别失败: %s" % e)

        def stop(self):
            self._running = False

    class Worker(QThread):
        """识别工作线程：轮询窗口 → 门控 → OCR → 匹配"""
        results = Signal(object)     # [(info, items), ...]
        status = Signal(str)

        def __init__(self, ocr, matcher):
            super().__init__()
            self.ocr = ocr
            self.matcher = matcher
            self._running = True
            self._gates = {}

        def run(self):
            while self._running:
                try:
                    wins = find_windows(cfg["sources"])
                    alive = set()
                    for info in wins:
                        alive.add(info.hwnd)
                        gate = self._gates.get(info.hwnd)
                        if gate is None:
                            gate = ChangeGate(thresh=cfg["change_thresh"])
                            self._gates[info.hwnd] = gate
                        arr = grab_window(info)
                        if arr is None or arr.size == 0:
                            continue
                        if not gate.changed(arr):
                            continue
                        items = analyze_window(info, self.ocr, self.matcher, arr)
                        self.results.emit([(info, items)])
                    for hwnd in list(self._gates):
                        if hwnd not in alive:
                            del self._gates[hwnd]
                except Exception as e:
                    self.status.emit("识别异常: %s" % e)
                time.sleep(cfg["poll_interval"])

        def stop(self):
            self._running = False

    class StartupToast(QWidget):
        """启动提醒：桌面右下角自绘 toast，显示 3.2s 后淡出关闭。

        为什么不用 QSystemTrayIcon.showMessage（气球通知）：
        Win10/11 的通知中心对「没有开始菜单快捷方式/未注册 AppID」的
        exe 会静默吞掉通知——用户照样不知道软件已经开了，等于没做。
        自绘顶层窗口 100% 可控。不抢焦点（Tool + ShowWithoutActivating），
        出现期间打字不受影响。
        """

        def __init__(self):
            super().__init__(None, Qt.Tool | Qt.FramelessWindowHint
                                   | Qt.WindowStaysOnTopHint)
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            from PySide6.QtWidgets import QLabel, QVBoxLayout
            # 手势提示跟随用户当前配置（老配置的手势键已裁掉的回退默认）
            import json as _json
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    _t = (_json.load(f).get("snip") or {}).get("trigger")
            except Exception:
                _t = None
            _tname = snip_mod.TRIGGERS.get(
                _t, snip_mod.TRIGGERS[snip_mod.DEFAULT_TRIGGER])["name"]
            line2 = ("<span style='color:#b9c2cf'>框选 = %s &nbsp;·&nbsp; "
                     "划词 = 选中文字松手</span>" % _tname)
            line3 = ("<span style='color:#7ee2a8'>免费开源 · 全功能无限时"
                     "无需激活码</span>")
            card = QLabel()
            card.setTextFormat(Qt.RichText)
            card.setText(
                "<b style='font-size:14px'>股联动 GuLianDong 已启动</b><br>"
                + line2 + "<br>" + line3)
            card.setStyleSheet("""
                QLabel { background:#1f2430; color:#ffffff;
                         border-radius:10px; padding:14px 20px; }""")
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(card)
            self.adjustSize()

        def popup(self):
            import ctypes as _c
            import ctypes.wintypes as _wt
            # 工作区 = 主屏去掉任务栏后的区域，toast 贴其右下角
            rect = _wt.RECT()
            if not _c.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                          _c.byref(rect), 0):
                rect.right = 1920
                rect.bottom = 1040
            x = rect.right - self.width() - 18
            y = rect.bottom - self.height() - 18
            self.move(x, y)
            self.show()
            self.raise_()
            QTimer.singleShot(3200, self._fade_out)

        def _fade_out(self):
            from PySide6.QtCore import QPropertyAnimation, QEasingCurve
            self._anim = QPropertyAnimation(self, b"windowOpacity", self)
            self._anim.setDuration(600)
            self._anim.setStartValue(1.0)
            self._anim.setEndValue(0.0)
            self._anim.setEasingCurve(QEasingCurve.OutCubic)
            self._anim.finished.connect(self.close)
            self._anim.start()

    class App(QObject):
        def __init__(self, app):
            super().__init__()
            self.app = app
            self.cfg = cfg
            self.overlays = {}
            self.popup = ActionPopup()
            self.popup.chosen.connect(self.on_action)
            self.enabled = True
            # 自动识别监视层（窗口轮询 + 高亮）总开关；关掉后只保留
            # 框选 / 划词两条链路（用户 2026-10-07 起：不要自动识别）
            self.auto = bool(cfg.get("auto_recognize", True))

            self.matcher = make_matcher(cfg, verbose=True)
            if self.auto:
                self.ocr = make_ocr(cfg, verbose=True)
                self.worker = Worker(self.ocr, self.matcher)
                self.worker.results.connect(self.on_results)
                self.worker.status.connect(lambda s: print(s))
                self.worker.start()
            else:
                # 不建主 OCR 引擎：模型 ~14MB 内存 + 启动 1~2s 都省下，
                # 划词/框选用 SnipWorker 自己的独立引擎
                self.ocr = None
                self.worker = None
                print("[自动识别] 已关闭（仅框选 + 划词模式）")

            # ---------------- 免费开源版：全功能无条件启用 ----------------
            # 没有任何门控：不做激活、不做联网校验、不看机器码。
            self._about_dialog = None
            self.snip = None
            self._enable_snip()

            self.tray = self._build_tray(app)
            # 引流位配置：启动后台预取（联动菜单/浮条/托盘共用）
            promo.refresh_async()
            # 周期任务：只刷引流 / 赞助文案（不再有授权复核）
            self._lic_timer = QTimer(self.app)
            self._lic_timer.setInterval(30 * 60 * 1000)
            self._lic_timer.timeout.connect(self._lic_tick)
            self._lic_timer.start()
            # 启动提醒：程序没有主窗口，不打个招呼用户根本不知道开没开
            if not auto_exit:
                self.toast = StartupToast()
                self.toast.popup()
            if self.auto:
                print("\n股联动已启动。监视: %s" % "、".join(cfg["sources"]))
            else:
                print("\n股联动已启动（框选 + 划词模式）。")
            _k = getattr(self, "snip_key", None)
            _k = _k if _k in snip_mod.TRIGGERS else snip_mod.DEFAULT_TRIGGER
            print("框选 = %s / 划词 = 选中文字松手 —— 任意界面全局可用。\n"
                  % snip_mod.TRIGGERS[_k]["name"])

            if auto_exit:
                # GLD_TEST_LIC=1 时自动打开「关于」窗口（回归测试）
                if os.environ.get("GLD_TEST_LIC") == "1":
                    QTimer.singleShot(1500, self.show_activation)
                QTimer.singleShot(int(auto_exit * 1000), self._auto_shot)

        # ---------------- 自检模式 ----------------
        def _auto_shot(self):
            """跑满指定秒数后出一份验收材料：

            1) 整屏 BitBlt 截图 —— 验证分层窗口真的合成上了屏（含遮挡保护）
            2) 每个覆盖层单独出「帧 + 高亮层」的合成图 —— 不依赖合成器，
               直接看高亮画得对不对，即使聊天窗口此刻被别的程序盖住。
            """
            if os.environ.get("GLD_TEST_LIC") == "1":
                d = self._about_dialog
                print("[TEST-LIC] about_dialog=%s visible=%s"
                      % (d is not None,
                         d.isVisible() if d is not None else None))
                t = getattr(self, "tray", None)
                if t is not None and t.contextMenu() is not None:
                    for a in t.contextMenu().actions():
                        if a.isSeparator():
                            print("[TEST-LIC] menu: ---")
                        else:
                            print("[TEST-LIC] menu: %-30s enabled=%s"
                                  % (a.text(), a.isEnabled()))
            try:
                self._auto_shot_inner(shot)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print("[自检] 出错：%s" % e)
            finally:
                self.quit()          # 无论如何都要退出，否则 GUI 会挂住

        def _auto_shot_inner(self, shot):
            if shot:
                from PIL import Image
                # 打包后 exe 旁没有 out/ 目录（源码运行时有，所以这个 bug
                # 以前一直没暴露）。保存前必须自己建。
                os.makedirs(os.path.dirname(shot), exist_ok=True)
                arr, origin = grab_desktop()
                Image.fromarray(arr).save(shot)
                print("[自检] 整屏截图 → %s  尺寸 %dx%d  虚拟桌面原点(%d,%d)"
                      % (shot, arr.shape[1], arr.shape[0], origin[0], origin[1]))

            for hwnd, ov in self.overlays.items():
                t = ov._target
                print("[自检] 覆盖层 0x%08X  目标=%s  对齐 %dx%d@(%d,%d)  "
                      "高亮 %d 处  可见=%s  前台=%s"
                      % (hwnd, t.app if t else "?", ov.width(), ov.height(),
                         ov.x(), ov.y(), len(ov.items), ov.isVisible(),
                         ov._is_foreground()))
                if not shot:
                    continue
                try:
                    composed = self._compose_preview(ov)
                except Exception as e:
                    print("[自检] 合成失败：%s" % e)
                    continue
                if composed is None:
                    continue
                base = shot.rsplit(".", 1)[0]
                out = "%s_%s.png" % (base, t.exe.replace(".exe", "") if t else "win")
                from PIL import Image
                Image.fromarray(composed).save(out)
                print("[自检] 帧+高亮 合成图 → %s  尺寸 %dx%d"
                      % (out, composed.shape[1], composed.shape[0]))

        @staticmethod
        def _compose_preview(ov):
            """把覆盖层离屏渲染，alpha 合成到聊天窗口的 PrintWindow 帧上"""
            import numpy as np
            from PIL import Image
            from PySide6.QtGui import QImage
            from src.capture import grab_window

            t = ov._target
            if t is None:
                return None
            base = grab_window(t)
            if base is None:
                return None
            pm = ov.grab()                       # 离屏渲染（不看屏幕合成）
            if pm.isNull():
                return None
            qimg = pm.toImage().convertToFormat(QImage.Format_RGBA8888)
            bpl = qimg.bytesPerLine()          # 每行字节数，可能是 width*4 的对齐值
            buf = np.frombuffer(qimg.constBits(), dtype=np.uint8)
            buf = buf[:bpl * qimg.height()].reshape(qimg.height(), bpl // 4, 4)
            over = buf[:, :qimg.width(), :]
            h = min(base.shape[0], over.shape[0])
            w = min(base.shape[1], over.shape[1])
            base = base[:h, :w].astype(np.float32)
            over = over[:h, :w].astype(np.float32)
            a = over[:, :, 3:4] / 255.0
            out = over[:, :, :3] * a + base * (1 - a)
            return out.astype(np.uint8)

        # ---------------- 托盘 ----------------
        def _icon(self):
            """软件图标（与 exe / 桌面快捷方式同一枚 build/icon.ico）。

            早期版本这里是手绘的占位图形（一个蓝色圆角矩形），用户看到的是
            「一个不像软件的图标」——托盘图标必须和程序本体视觉一致。
            ico 内含 16/24/32/48/64/128/256 七种尺寸，交给 QIcon 自己按
            系统 DPI/托盘尺寸挑，缩放清晰不糊；文件缺失时回退到手绘图标。
            """
            try:
                from src import paths
                ico = os.path.join(paths.bundle_dir(), "build", "icon.ico")
                icon = QIcon(ico)
                if not icon.isNull():
                    return icon
            except Exception as e:
                print("[图标] 读取 icon.ico 失败，回退绘制：%s" % e)
            pm = QPixmap(32, 32)
            pm.fill(QColor(0, 0, 0, 0))
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(QColor(37, 99, 235))
            p.setBrush(QColor(37, 99, 235, 40))
            p.drawRoundedRect(4, 8, 24, 16, 4, 4)
            p.setPen(QColor(37, 99, 235))
            p.drawLine(9, 20, 23, 20)
            p.end()
            return QIcon(pm)

        def _build_tray(self, app):
            tray = QSystemTrayIcon(self._icon(), app)
            tray.setToolTip("股联动 GuLianDong v%s · 免费开源版"
                         % licensing.APP_VERSION)
            menu = QMenu()
            if self.auto:
                self.act_toggle = QAction("暂停识别", menu)
                self.act_toggle.triggered.connect(self.toggle)
                menu.addAction(self.act_toggle)
                act_scan = QAction("立即重新扫描", menu)
                act_scan.triggered.connect(self.rescan)
                menu.addAction(act_scan)
            if self.snip is not None:
                self._add_snip_actions(menu)
                self._snip_menu_wired = True
            menu.addSeparator()
            self.act_lic = QAction("关于 股联动（免费开源）", menu)
            self.act_lic.triggered.connect(self.show_activation)
            menu.addAction(self.act_lic)
            # ---- 引流入口（设置/托盘菜单）：文案来自服务端，后台可随时改 ----
            self.act_promo = QAction("更多 · 实时消息中心", menu)
            _p0 = promo.get()
            if _p0:
                self.act_promo.setText("%s · %s" % (
                    _p0.get("button") or "了解更多", _p0.get("title", "")))
            else:
                self.act_promo.setVisible(False)   # 启动时没拉到先藏，tick 里再刷
            self.act_promo.triggered.connect(self._open_promo)
            menu.addAction(self.act_promo)
            menu.addSeparator()
            act_quit = QAction("退出", menu)
            act_quit.triggered.connect(self.quit)
            menu.addAction(act_quit)
            tray.setContextMenu(menu)
            tray.show()
            return tray

        def toggle(self):
            self.enabled = not self.enabled
            self.act_toggle.setText("暂停识别" if self.enabled else "恢复识别")
            if not self.enabled:
                for ov in self.overlays.values():
                    ov.clear()
            print("[状态] %s" % ("已恢复" if self.enabled else "已暂停"))

        def rescan(self):
            if self.worker is None:
                return
            for gate in getattr(self.worker, "_gates", {}).values():
                gate.reset()
            print("[状态] 已请求重新扫描")

        # ---------------- 结果 ----------------
        def on_results(self, batch):
            if not self.enabled:
                return
            for info, items in batch:
                ov = self.overlays.get(info.hwnd)
                if ov is None:
                    ocfg = cfg.get("overlay", {})
                    ov = Overlay(
                        clickable=True,
                        only_when_foreground=ocfg.get("only_when_foreground", True),
                        adaptive_palette=ocfg.get("adaptive_palette", True),
                        click_modifier=ocfg.get("click_modifier", "ctrl"))
                    ov.clicked.connect(self.on_click)
                    ov.attach_to(info)
                    self.overlays[info.hwnd] = ov
                ov.set_highlights(items)
                if items:
                    print("[命中] %s: %s" % (
                        info.app,
                        "、".join("%s(%s)" % (i["label"], i["code"]) for i in items[:8])))

        # ---------------- 点击 ----------------
        def on_click(self, item):
            if not self.enabled:
                return
            acts = []
            for key in cfg["targets"]:
                cfg_t = linkage.TARGETS.get(key)
                if not cfg_t:
                    continue
                if cfg_t["method"] == "url":
                    acts.append((key, "%s（网页）" % cfg_t["label"]))
                elif linkage.process_running(cfg_t["proc"]):
                    acts.append((key, "在%s中查看" % cfg_t["label"]))
                # 客户端没开就整条省掉 —— 留着点了也是失败，不如不给
            if item.get("ambiguous"):
                acts = [("__pick", "候选 %d 只，点此选择" % len(item["candidates"]))] + acts
            acts.append(("__copy", "复制代码 %s" % item.get("code", "")))

            import ctypes as _c
            pt = _c.wintypes.POINT()
            _c.windll.user32.GetCursorPos(_c.byref(pt))
            self.popup.build(item, acts).popup_at(pt.x + 6, pt.y + 6)

        def on_action(self, key, item):
            self.popup.hide()
            if key == "__promo":
                p = promo.get()
                if p:
                    promo.open_url(p["url"])
                    print("[引流] 打开 %s" % p["url"])
                return
            if key == "__copy":
                QApplication.clipboard().setText(item.get("code", ""))
                print("[复制] %s" % item.get("code"))
                return
            if key == "__pick":
                cands = item.get("candidates", [])
                if cands:
                    code = cands[0][0]
                    print("[候选] %s → 先跳第一只 %s" % (cands, code))
                    linkage.jump_to(code, target=cfg["targets"][0] if cfg["targets"] else "tdx")
                return
            code = item.get("code")
            if not code:
                return
            ok, detail = linkage.jump_to(code, target=key)
            print("[联动] %s → %s  %s" % (code, "成功" if ok else "失败", detail))

        # ---------------- 框选联动 ----------------
        def _enable_snip(self):
            """创建框选/划词整套链路。启动时无条件创建。"""
            if self.snip is not None:
                return
            if not cfg.get("snip", {}).get("enabled", True):
                return
            self.snip_popup = snip_mod.SelectionPopup()
            self.snip_popup.chosen.connect(self.on_snip_action)
            # 独立 OCR 引擎，理由见 SnipWorker docstring
            self.snip_ocr = make_ocr(cfg, verbose=False)
            self.snip_worker = SnipWorker(self.snip_ocr, self.matcher)
            self.snip_worker.done.connect(self.on_snip_results)
            self.snip_worker.start()
            self.snip = snip_mod.SnipController(
                self.snip_popup,
                trigger=cfg.get("snip", {}).get("trigger",
                                                snip_mod.DEFAULT_TRIGGER))
            self.snip_key = cfg.get("snip", {}).get("trigger",
                                                    snip_mod.DEFAULT_TRIGGER)
            if self.snip_key not in snip_mod.TRIGGERS:
                # 老版本配置里可能是 alt / middle / x1 / x2 —— 已裁掉，迁移默认
                self.snip_key = snip_mod.DEFAULT_TRIGGER
                self.snip.set_trigger(self.snip_key)
            self.snip.snipped.connect(self.on_snipped)
            self.snip.text_selected.connect(self.on_text_selected)
            self.snip.select_enabled = cfg.get("snip", {}).get("select", True)
            print("框选联动已开启（%s）：按住框选屏幕任意区域，"
                  "松手自动识别并弹出联动浮条"
                  % snip_mod.TRIGGERS[self.snip_key]["name"])

        def _disable_snip(self):
            """兼容旧调用点：免费开源版不存在「授权失效」，不再停功能。"""
            pass

        def show_activation(self):
            """托盘「关于」入口（沿用旧方法名以免牵动其它调用点）。"""
            if self._about_dialog is not None:
                self._about_dialog.show()
                self._about_dialog.raise_()
                self._about_dialog.activateWindow()
                return
            dlg = AboutDialog()
            self._about_dialog = dlg
            dlg.show()

        def _open_promo(self):
            p = promo.get()
            if p:
                promo.open_url(p["url"])
                print("[引流] 打开 %s" % p["url"])

        def _lic_tick(self):
            """周期任务（每 30 分钟）：只刷引流 / 自愿赞助文案。

            免费开源版没有任何授权复核、不联网校验、不因服务端状态
            锁功能 —— 服务端不可达时静默跳过，软件照常全功能。
            """
            if getattr(self, "act_promo", None):
                p = promo.get(force=True)
                if p:
                    self.act_promo.setVisible(True)
                    self.act_promo.setText("%s · %s" % (
                        p.get("button") or "了解更多", p.get("title", "")))
            if getattr(self, "_about_dialog", None):
                try:
                    self._about_dialog.refresh_donate(promo.donate())
                except Exception:
                    pass

        def _on_verify_done(self, _ok, _msg):
            """兼容旧调用点：免费开源版不做任何授权复核。"""
            pass

        def _apply_lic_state(self):
            """兼容旧调用点：免费开源版恒为可用，此处不再做任何锁定。"""
            pass

        def toggle_snip(self):
            on = self.act_snip.isChecked()
            self.snip.set_enabled(on)
            self._save_snip_cfg()
            print("[框选] %s" % ("已开启" if on else "已关闭"))

        def toggle_select(self):
            on = self.act_select.isChecked()
            self.snip.set_select_enabled(on)
            self._save_snip_cfg()
            print("[选取] %s" % ("已开启" if on else "已关闭"))

        # ---------------- 托盘菜单：框选区生命周期 ----------------
        def _add_snip_actions(self, menu):
            """托盘菜单加框选/划词区：开关两项 + 手势子菜单。"""
            menu.addSeparator()
            self.act_snip = QAction("框选联动", menu)
            self.act_snip.setCheckable(True)
            self.act_snip.setChecked(self.snip.enabled)
            self.act_snip.triggered.connect(self.toggle_snip)
            menu.addAction(self.act_snip)
            self.act_select = QAction("文字选取联动", menu)
            self.act_select.setCheckable(True)
            self.act_select.setChecked(self.snip.select_enabled)
            self.act_select.triggered.connect(self.toggle_select)
            menu.addAction(self.act_select)
            # 手势子菜单：单选组，选择即热切换并写回 config.json
            gmenu = menu.addMenu("框选手势")
            self._snip_group = QActionGroup(gmenu)
            self._snip_group.setExclusive(True)
            self._snip_actions = {}
            for key in snip_mod.TRIGGERS:      # 仅三个组合键，见 snip.TRIGGERS
                a = QAction(snip_mod.TRIGGERS[key]["name"], gmenu)
                a.setCheckable(True)
                self._snip_group.addAction(a)
                a.triggered.connect(
                    lambda checked=False, k=key: self.set_snip_trigger(k))
                gmenu.addAction(a)
                self._snip_actions[key] = a
            self._sync_snip_menu()
            self._snip_menu_refs = [(self.act_snip, "框选联动"),
                                    (self.act_select, "文字选取联动"),
                                    (gmenu, "框选手势")]

        def _refresh_tray_menu(self):
            """兼容旧调用点：免费开源版菜单恒为完整可用，无需重建。"""
            pass

        def _rebuild_tray(self):
            """兼容旧调用点：菜单结构已固定（无授权态差异），直接跳过。"""
            pass

        def _sync_snip_menu(self):
            """让手势子菜单的勾选态与当前 self.snip_key 一致"""
            for k, a in self._snip_actions.items():
                a.setChecked(k == self.snip_key)

        def set_snip_trigger(self, key):
            """托盘切换框选手势：热切换 + 勾选态 + 写回 config.json"""
            if key not in snip_mod.TRIGGERS:
                return
            self.snip_key = key
            self.snip.set_trigger(key)
            self._sync_snip_menu()
            self._save_snip_cfg()
            print("[框选] 手势已切换：%s（已保存，重启保持）"
                  % snip_mod.TRIGGERS[key]["name"])

        def _save_snip_cfg(self):
            """把框选开关 + 手势写回用户 config.json（保留其他字段）"""
            try:
                import json
                try:
                    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
                snip_cfg = data.get("snip") if isinstance(data.get("snip"), dict) else {}
                snip_cfg["enabled"] = self.snip.enabled
                snip_cfg["trigger"] = self.snip_key
                snip_cfg["select"] = bool(getattr(self.snip, "select_enabled", True))
                data["snip"] = snip_cfg
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print("[框选] 配置保存失败: %s" % e)

        def on_snipped(self, rect):
            """框选完成 → 抓屏（快，主线程做）→ 丢给识别线程"""
            if not self.enabled:
                return
            try:
                arr = snip_mod.grab_region(rect.left(), rect.top(),
                                           rect.width(), rect.height())
            except Exception as e:
                print("[框选] 取屏失败: %s" % e)
                return
            self.snip_worker.submit(rect, arr)

        def on_text_selected(self, rect):
            """文字选取联动：划词 / 双击选词松手 → 抓屏识别。

            与框选同一条识别链路；未命中时【不弹窗】——这是被动触发的
            手势，用户可能只是在正常选文字，保持安静是底线。
            """
            if not self.enabled or \
                    not getattr(self.snip, "select_enabled", False):
                return
            try:
                arr = snip_mod.grab_region(rect.left(), rect.top(),
                                           rect.width(), rect.height())
            except Exception as e:
                print("[选取] 取屏失败: %s" % e)
                return
            self.snip_worker.submit(rect, arr, mode="select")

        @staticmethod
        def _short_actions():
            """当前可用的联动目标（短标签版）+ 复制。
            与 on_click 同一套判活逻辑：客户端没开就不给这一项。"""
            acts = []
            for key in cfg["targets"]:
                t = linkage.TARGETS.get(key)
                if not t:
                    continue
                label = t["label"].replace("东方财富", "东财").replace("(网页)", "网页")
                if t["method"] == "url":
                    acts.append((key, label))
                elif linkage.process_running(t["proc"]):
                    acts.append((key, "看" + label))
            acts.append(("__copy", "复制"))
            return acts

        def on_snip_results(self, rect, items, mode="snip"):
            if not self.enabled:
                return
            rows, seen = [], set()
            for it in items:
                if it.get("ambiguous"):
                    # 概念词 / 多候选：每个候选单独成行，都能一键联动
                    for code, name in it["candidates"][:6]:
                        if code in seen:
                            continue
                        seen.add(code)
                        rows.append({"name": name, "code": code,
                                     "tag": it["label"]})
                else:
                    code = it.get("code")
                    if not code or code in seen:
                        continue
                    seen.add(code)
                    rows.append({"name": it["label"], "code": code, "tag": ""})
            if not rows and mode == "select":
                # 文字选取未命中 → 不弹「未识别到」卡片，保持安静
                print("[选取] 未识别到股票（不弹窗）")
                return
            tag = "[选取]" if mode == "select" else "[框选]"
            self.snip_popup.show_results(rows, self._short_actions(), rect)
            if rows:
                print("%s 命中 %d 只: %s"
                      % (tag, len(rows),
                         "、".join("%s(%s)" % (r["name"], r["code"])
                                   for r in rows[:8])))
            else:
                print("%s 该区域未识别到股票" % tag)

        def on_snip_action(self, key, code):
            # 锁定置顶时点按钮不收浮条 —— 用户要连续点开多只对比；
            # 未锁定保持原行为（点一下即收起）。
            if not getattr(self.snip_popup, "_pinned", False):
                self.snip_popup.hide()
            if key == "__promo":
                p = promo.get()
                if p:
                    promo.open_url(p["url"])
                    print("[引流] 打开 %s" % p["url"])
                return
            if key == "__copy":
                QApplication.clipboard().setText(code)
                print("[复制] %s%s" % (code, "（浮条已锁定，继续点下一只）"
                      if getattr(self.snip_popup, "_pinned", False) else ""))
                return
            ok, detail = linkage.jump_to(code, target=key)
            print("[联动] %s → %s  %s" % (code, "成功" if ok else "失败", detail))

        def quit(self):
            if self.worker is not None:
                self.worker.stop()
                # 工作线程可能正卡在一次 OCR（最长 ~1s），等待要给够；
                # 超时则强制结束，否则 QThread 析构时会 abort。
                if not self.worker.wait(4000):
                    self.worker.terminate()
                    self.worker.wait(1000)
            for ov in self.overlays.values():
                ov.remove_mouse_hook()
                ov.close()
            if self.snip is not None:
                self.snip_worker.stop()
                self.snip_worker.wait(2000)
                self.snip.shutdown()
            self.tray.hide()
            print("[退出] 已清理 %d 个覆盖层与鼠标钩子" % len(self.overlays))
            self.app.quit()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    # 任务栏 / Alt-Tab / 弹窗标题栏图标：与 exe 同一枚（托盘图标在 App._icon 里）
    try:
        from src import paths as _paths
        _ico = QIcon(os.path.join(_paths.bundle_dir(), "build", "icon.ico"))
        if not _ico.isNull():
            app.setWindowIcon(_ico)
    except Exception as e:
        print("[图标] 应用图标设置失败：%s" % e)
    ctrl = App(app)
    app._ctrl = ctrl
    return app.exec()


def _setup_frozen_logging():
    """打包成 windowed exe 后没有控制台，sys.stdout/stderr 是 None。

    而本项目到处都在 print（状态、命中、联动结果），不接管的话
    第一句 print 就 AttributeError 崩掉。所以：
      1. 优先把输出重定向到 exe 旁的 logs/guliandong.log（出问题能查）
      2. 重定向失败就退化成丢弃，绝不能让它成为新的崩溃源
    """
    if not getattr(sys, "frozen", False):
        return None

    class _Null:
        def write(self, s):
            pass

        def flush(self):
            pass

    try:
        logdir = os.path.join(ROOT, "logs")
        os.makedirs(logdir, exist_ok=True)
        f = open(os.path.join(logdir, "guliandong.log"), "a",
                 encoding="utf-8", buffering=1)
        f.write("\n" + "=" * 60 + "\n启动 %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        sys.stdout = f
        sys.stderr = f
        return f
    except Exception:
        sys.stdout = _Null()
        sys.stderr = _Null()
        return None


def main():
    _setup_frozen_logging()

    ap = argparse.ArgumentParser(description="股联动 GuLianDong")
    ap.add_argument("--once", action="store_true", help="单轮采样后退出")
    ap.add_argument("--selftest", action="store_true", help="逐层自检")
    ap.add_argument("--seconds", type=float, default=6.0, help="单轮采样时长")
    ap.add_argument("--gui-test", type=float, default=0, metavar="秒",
                    help="启动 GUI 跑 N 秒、截图到 out/gui_overlay_test.png 后自动退出")
    args = ap.parse_args()

    cfg = load_config()
    if args.selftest:
        selftest(cfg)
    elif args.once:
        run_once(cfg, seconds=args.seconds)
    elif args.gui_test:
        shot = os.path.join(ROOT, "out", "gui_overlay_test.png")
        sys.exit(run_gui(cfg, auto_exit=args.gui_test, shot=shot))
    else:
        sys.exit(run_gui(cfg))


if __name__ == "__main__":
    main()
