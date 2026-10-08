# -*- coding: utf-8 -*-
"""股联动 · 免费开源版运行时服务层。

2026-10-08 起股联动完全免费开源（MIT 协议），本模块从「授权校验层」
改造为「运行时服务层」—— 仍然提供三件事，签名/接口保持不变以免牵动
promo 等调用方：

    * _APPDATA         用户数据目录（promo 缓存等沿用）
    * _post()          对官网 /sl-api/ 的 HTTP POST（引流通用 + 可选赞助
                       链接拉取；服务器挂了自动降级，不影响任何功能）
    * machine_display()机器码（仅用于「关于」窗口里的匿名统计展示与
                       用户自助排查，不再用于任何门控）

**不再存在激活码、试用期、联网校验、解绑封禁**。check() 永远返回可用，
主程序里所有「未激活 → 锁定功能」的分支都已删除。软件离线、永久、
无条件全功能。

开源仓库：见官网 https://www.gldong.com
"""
import hashlib
import json
import os
import platform
import threading
import urllib.request
import winreg

from src.paths import app_dir

# 服务端（仅用于拉取引流通知文案 / 可选赞助入口；挂了软件照常全功能）
DEFAULT_SERVERS = ["http://gldong.com:8080/sl-api",
                   "http://122.51.111.104:8080/sl-api"]

_APPDATA = os.path.join(os.environ.get("APPDATA", app_dir()), "GuLianDong")

_state_lock = threading.Lock()
_state = None            # check() 的缓存（免费模式下恒定不变）

APP_VERSION = "1.1.0"
APP_NAME = "股联动 GuLianDong"
APP_TAGLINE = "聊天里的股票，一键联动行情软件"
APP_HOMEPAGE = "https://www.gldong.com"
APP_REPO = "https://github.com/shen13060069009/GuLianDong"
APP_LICENSE = "MIT License"


# ---------------------------------------------------------------- 机器码
def machine_id():
    """稳定机器指纹（MachineGuid 优先）。开源免费版只用于匿名统计展示，
    不参与任何功能判断，因此没有「换机即失效」的问题。"""
    guid = ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Cryptography") as k:
            guid = winreg.QueryValueEx(k, "MachineGuid")[0]
    except OSError:
        pass
    if not guid:
        guid = "|".join([platform.machine(), platform.processor(),
                         os.environ.get("COMPUTORNAME", ""),
                         os.environ.get("PROCESSOR_IDENTIFIER", "")])
    return hashlib.sha256(guid.encode("utf-8", "ignore")).hexdigest()[:32]


def machine_display():
    """给用户看/排查用的机器码：XXXX-XXXX-XXXX-XXXX"""
    mid = machine_id().upper()
    return "-".join(mid[i:i + 4] for i in range(0, 32, 4))


def machine_info():
    return "%s / %s" % (platform.system(), platform.machine())


# ---------------------------------------------------------------- 服务端通信
def _servers():
    """生效的服务端地址列表。优先级：环境变量 > config.json > 默认。"""
    env = os.environ.get("GULIANDONG_LICENSE_SERVER")
    if env:
        return [env.rstrip("/")]
    try:
        cfg = json.load(open(os.path.join(app_dir(), "config.json"),
                             encoding="utf-8"))
        s = cfg.get("license", {}).get("server")
        if s:
            return [s.rstrip("/")]
    except Exception:
        pass
    return DEFAULT_SERVERS


def _post(path, data, timeout=6):
    """POST 到官网 /sl-api/（多地址容灾）。

    仅用于可选的引流 / 赞助文案拉取。任何异常都向上抛，由调用方 catch
    后静默降级 —— 免费开源版没有任何功能依赖这条链路。
    """
    if not path.startswith("/"):
        path = "/" + path
    last_err = None
    for server in _servers():
        req = urllib.request.Request(
            server + path,
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        # 绕过系统代理直连：用户机器上的抓包/加速器代理会拦掉这个请求
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("无可用服务器")


# ---------------------------------------------------------------- 对外 API
def check(force=False):
    """免费开源版：永远可用。保留 force 参数只为兼容旧调用点。"""
    global _state
    with _state_lock:
        if _state is not None and not force:
            return _state
        _state = {"state": "free", "days_left": None, "code": None,
                  "machine_id": machine_id(), "msg": "免费开源版，全部功能可用"}
        return _state


def heartbeat_needed():
    """兼容旧调用点：免费版无需任何校验。"""
    return False


def heartbeat():
    """兼容旧调用点：免费版不联网校验，直接成功。"""
    return True, "免费开源版，无需校验"


def heartbeat_async(on_done=None):
    """兼容旧调用点。"""
    if on_done:
        on_done(True, "免费开源版，无需校验")


if __name__ == "__main__":
    print(APP_NAME, APP_VERSION, "|", APP_LICENSE)
    print("模式: 免费开源，全功能可用（无激活码 / 无联网校验）")
    print("官网:", APP_HOMEPAGE)
    print("仓库:", APP_REPO)