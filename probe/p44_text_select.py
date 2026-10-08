# -*- coding: utf-8 -*-
"""p44 文字选取联动探针

两条腿验证：
  A. 状态机单元测试 —— 直接驱动 SnipController._handle（不依赖合成输入，
     规避本机钩子环境干扰），断言各种手势该发/不该发 text_selected；
  B. 识别链路验证 —— Qt 渲染一个含股票名的测试窗口，用 expand_select_rect
     的真实扩区参数取屏 → snip_recognize，确认划词场景能认出股票，
     并验证反色重试路径（模拟白字蓝底选中态）。
"""
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont, QPainter, QColor
from PySide6.QtWidgets import QApplication, QWidget

import main as main_mod
from src import snip as snip_mod

ok = {}

app = QApplication(sys.argv)

# ============================================================ A 状态机
class Stub:
    vis = False
    geo = snip_mod.QRect(0, 0, 0, 0)

    def isVisible(self):
        return self.vis

    def frameGeometry(self):
        return self.geo

    def hide(self):
        pass

popup = Stub()
ctrl = snip_mod.SnipController(popup, trigger="alt")
ctrl.band.hide()

fired = []
ctrl.text_selected.connect(lambda r: fired.append(r))

U, DN = snip_mod.WM_LBUTTONUP, snip_mod.WM_LBUTTONDOWN
DB = snip_mod.WM_LBUTTONDBLCLK
MV = snip_mod.WM_MOUSEMOVE

# A1 水平划词：down(200,300) → up(420,312) → 触发，扩区后高度≥34
ctrl._handle(DN, 200, 300)
r = ctrl._handle(U, 420, 312)
a1 = len(fired) == 1 and fired[0].height() >= 34 \
     and fired[0].left() <= 194 and fired[0].right() >= 426
ok["A1 划词触发"] = a1
print("A1 水平划词 → %s rect=%s" % ("PASS" if a1 else "FAIL", fired[-1]))

# A2 小位移点击（<10px）：不触发
n = len(fired)
ctrl._handle(DN, 500, 300)
ctrl._handle(U, 505, 302)
ok["A2 点击不触发"] = len(fired) == n

# A3 竖向拖拽（dx<=dy，滚动条/垂直选择）：不触发
ctrl._handle(DN, 700, 200)
ctrl._handle(U, 706, 380)
ok["A3 竖向不触发"] = len(fired) == n

# A4 双击选词：触发 360×44
ctrl._handle(DB, 300, 250)
a4 = len(fired) == n + 1 and fired[-1].width() == 360 \
     and fired[-1].height() == 44 and abs(fired[-1].center().x() - 300) <= 1
ok["A4 双击触发"] = a4
print("A4 双击选词 → %s rect=%s" % ("PASS" if a4 else "FAIL", fired[-1]))

# A5 浮条上的按下/双击：不触发（防自识别死循环）
Stub.vis = True
Stub.geo = snip_mod.QRect(280, 230, 300, 120)
ctrl._handle(DN, 300, 250)
ctrl._handle(U, 360, 260)          # 起点在浮条内
n5 = len(fired)
ctrl._handle(DB, 320, 260)         # 双击也在浮条内
ok["A5 浮条内不触发"] = len(fired) == n5
Stub.vis = False

# A6 开关关闭：任何手势都不触发
ctrl.set_select_enabled(False)
ctrl._handle(DN, 200, 300)
ctrl._handle(U, 420, 312)
ok["A6 开关关闭不触发"] = len(fired) == n5
ctrl.set_select_enabled(True)

# A7 非客户区松手（模拟拖标题栏）：_hit_client=False → 不触发。
#    构造一个真 Qt 窗口取其标题栏点做真实 WM_NCHITTEST。
class Win(QWidget):
    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 255, 255))
        p.setPen(QColor(0, 0, 0))
        f = QFont()
        f.setPixelSize(30)
        p.setFont(f)
        p.drawText(20, 60, "贵州茅台 600519 五粮液 000858 宁德时代 300750")
        p.drawText(20, 120, "比亚迪 002594 中芯国际 688981 隆基绿能 601012")
        p.end()

win = Win()
win.setGeometry(140, 160, 900, 200)
win.show()

def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()
        time.sleep(0.01)

pump(0.3)
from PySide6.QtCore import QPoint
tl = win.mapToGlobal(QPoint(0, 0))          # 客户区左上（窗口原点）
title_pt = (tl.x() + 300, tl.y() - 15)      # 标题栏（客户区上方 15px）
a7 = not snip_mod._hit_client(*title_pt)
ok["A7 标题栏非客户区"] = a7
print("A7 标题栏 WM_NCHITTEST → %s (hit_client=%s)"
      % ("PASS" if a7 else "FAIL", snip_mod._hit_client(*title_pt)))

# A8 钩子层 Alt 框选优先：触发手势按下 → 进入框选，_sel_origin 清空、不吞选取
snip_mod._mods_down = lambda mods: True     # 模拟 Alt 按住
ret = ctrl._handle(DN, 100, 100)
a8 = ret == 1 and ctrl._active and ctrl._sel_origin is None
snip_mod._mods_down = lambda mods: False
ctrl._active = False
ctrl.band.hide()
ok["A8 框选手势优先"] = a8
print("A8 Alt+按下进入框选 → %s (ret=%s active=%s origin=%s)"
      % ("PASS" if a8 else "FAIL", ret, ctrl._active, ctrl._sel_origin))

# ============================================================ B 识别链路
pump(0.2)
g = win.mapToGlobal(QPoint(0, 0))
# 划词「贵州茅台」这行（第一行 y≈40±，行高约 34px）
line_rect = snip_mod.QRect(g.x() + 15, g.y() + 35, 400, 26)
grab = snip_mod.expand_select_rect(line_rect)
arr = snip_mod.grab_region(grab.left(), grab.top(), grab.width(), grab.height())

matcher = (main_mod.make_ocr(main_mod.load_config(), verbose=False),
           main_mod.make_matcher(main_mod.load_config(), verbose=False))
b1_items = main_mod.snip_recognize(matcher[0], matcher[1], arr)
codes1 = {it.get("code") for it in b1_items if it.get("code")}
b1 = "600519" in codes1
ok["B1 划词识别"] = b1
print("B1 划词整行识别 → %s 命中 %s" % ("PASS" if b1 else "FAIL", codes1))

# B2 反色重试路径：模拟白字蓝底选中态（整图反色后应仍能识别）
inv = 255 - arr
b2_items = main_mod.snip_recognize(matcher[0], matcher[1], inv)
codes2 = {it.get("code") for it in b2_items if it.get("code")}
b2 = "600519" in codes2
ok["B2 反色重试"] = b2
print("B2 反色图识别 → %s 命中 %s" % ("PASS" if b2 else "FAIL", codes2))

# ============================================================ 汇总
win.hide()
pump(0.1)
fails = [k for k, v in ok.items() if not v]
print("\n===== p44 汇总：%d/%d PASS ====="
      % (len(ok) - len(fails), len(ok)))
for k, v in ok.items():
    print("  %s %s" % ("✓" if v else "✗", k))
sys.exit(1 if fails else 0)
