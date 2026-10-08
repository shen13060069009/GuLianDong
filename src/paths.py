# -*- coding: utf-8 -*-
"""统一路径解析 —— 同时兼容「源码运行」与「PyInstaller 打包后运行」。

打包后会出现两套目录，不区分清楚就会变成
「配置改了没反应」或「第一次运行崩在找不到 data/stocks.json」：

    sys._MEIPASS   打包进 exe 的只读资源（首次运行从这里自举）
    exe 所在目录    用户可见、可改、可备份（config.json / data/ 放这里）

策略：只读资源首次运行时从 _MEIPASS 复制到 exe 旁，之后一律以 exe 旁的为准
—— 用户改了的才算数。
"""
import os
import shutil
import sys


def is_frozen():
    """是否运行在 PyInstaller 打包产物里"""
    return bool(getattr(sys, "frozen", False))


def app_dir():
    """用户数据目录。

    打包后 = exe 所在目录（不是 _internal/，用户能直接看到）
    源码运行 = 项目根目录
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_dir():
    """打包进来的只读资源目录（源码运行时等同于 app_dir）"""
    if is_frozen():
        return getattr(sys, "_MEIPASS", app_dir())
    return app_dir()


def data_dir(autocopy=True):
    """data/ 目录。首次运行（或用户误删）时从打包资源自举一份出来。"""
    dst = os.path.join(app_dir(), "data")
    src = os.path.join(bundle_dir(), "data")
    if autocopy and is_frozen() and not os.path.isdir(dst) and os.path.isdir(src):
        try:
            shutil.copytree(src, dst)
        except Exception:
            return src          # 复制失败就退回只读资源，至少能跑起来
    return dst if os.path.isdir(dst) else src


def config_path():
    """config.json 路径 —— 永远在用户数据目录，保证可写"""
    return os.path.join(app_dir(), "config.json")
