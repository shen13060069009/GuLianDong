# -*- coding: utf-8 -*-
"""软件内引流位 / 自愿赞助位：文案与链接全部来自官网服务端，后台随时可改。

数据流：
    服务端 GET/POST /promo   →  {enabled,title,desc,url,button}   站内引流
    服务端 GET/POST /donate  →  {enabled,title,desc,url,button}   自愿赞助
    客户端启动后台线程拉取 → 内存缓存（1 小时 TTL）→ 本地文件缓存容灾
    （断网/服务端挂了也展示最近一次配置；从未拉到过则整个引流位不出现）

使用：
    promo.refresh_async()      # 启动时后台预取引流
    p = promo.get()            # None = 不展示
    promo.donate()            # 自愿赞助入口，None = 不展示
    promo.open_url(p["url"])   # 点引流条 → 系统默认浏览器

注意：免费开源版没有任何付费墙，以上两条链路挂了都只是「少一个入口」，
软件功能与可用性完全不受影响。
"""
import json
import os
import threading
import time
import webbrowser

from . import licensing

_TTL = 3600                      # 配置缓存 1 小时，后台改完最迟 1 小时生效
_lock = threading.Lock()
_state = {"data": None, "ts": 0.0}


def _cache_file():
    return os.path.join(licensing._APPDATA, "promo.json")


def _load_cache():
    try:
        with open(_cache_file(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("url") else None
    except Exception:
        return None


def _save_cache(d):
    try:
        os.makedirs(os.path.dirname(_cache_file()), exist_ok=True)
        with open(_cache_file(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


def get(force=False):
    """当前引流配置（dict）或 None（未启用/未拉到）。绝不抛异常、不阻塞。"""
    with _lock:
        if not force and _state["data"] is not None \
                and time.time() - _state["ts"] < _TTL:
            d = _state["data"]
            return d if d.get("enabled") and d.get("url") else None
    d = None
    try:
        r = licensing._post("/promo", {}, timeout=4)
        if isinstance(r, dict) and r.get("url"):
            d = {"enabled": bool(r.get("enabled")),
                 "title": str(r.get("title", ""))[:40],
                 "desc": str(r.get("desc", ""))[:80],
                 "url": str(r.get("url", "")),
                 "button": str(r.get("button", "了解更多"))[:20]}
    except Exception:
        pass
    with _lock:
        if d is not None:
            _state["data"] = d
            _state["ts"] = time.time()
            _save_cache(d)
        elif _state["data"] is None:
            c = _load_cache()                     # 网络失败 → 本地缓存兜底
            if c:
                _state["data"] = c
                _state["ts"] = time.time() - _TTL + 300   # 5 分钟后重试网络
        d = _state["data"]
    return d if d and d.get("enabled") and d.get("url") else None


def open_url(url):
    """系统默认浏览器打开引流链接。"""
    try:
        os.startfile(url)               # Windows
    except Exception:
        try:
            webbrowser.open(url)
        except Exception:
            pass


def donate():
    """拉取「自愿赞助」入口配置，返回 dict 或 None。

    赞助纯自愿、绝不作为付费墙：拉不到（没配 / 断网 / 服务端挂了）就返回
    None，「关于」窗口里那一栏直接不显示，不影响任何功能。
    """
    def norm(r):
        if isinstance(r, dict) and r.get("url"):
            return {"enabled": bool(r.get("enabled", True)),
                    "title": str(r.get("title", ""))[:40],
                    "desc": str(r.get("desc", ""))[:80],
                    "url": str(r.get("url", "")),
                    "button": str(r.get("button", "支持一下"))[:20]}
        return None

    try:
        d = norm(licensing._post("/donate", {}, timeout=4))
    except Exception:
        d = None
    if d and d.get("enabled", True):
        return d
    return None


def refresh_async():
    """启动时后台预取一次，避免卡 UI。"""
    threading.Thread(target=get, kwargs={"force": True},
                     daemon=True).start()
