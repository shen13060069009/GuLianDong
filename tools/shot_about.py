# -*- coding: utf-8 -*-
"""截客户端「关于」窗口的真机图（GLD_TEST_LIC=1 自动弹出后抓屏）。"""
import os
import subprocess
import sys
import time

ROOT = r'C:\Users\Administrator\WorkBuddy\2026-10-06-13-12-12\stocklens'
EXE = os.path.join(ROOT, 'dist', 'GuLianDong', 'GuLianDong.exe')
OUT = os.path.join(ROOT, 'out', 'about_dialog.png')

env = dict(os.environ, GLD_TEST_LIC='1')
p = subprocess.Popen([EXE, '--gui-test', '10'], env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(5)
from PIL import ImageGrab
img = ImageGrab.grab(all_screens=True)
img.save(OUT)
print('saved', OUT, img.size)
try:
    p.wait(timeout=25)
except Exception:
    p.kill()