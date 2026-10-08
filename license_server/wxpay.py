# -*- coding: utf-8 -*-
"""微信支付 APIv3 Native 扫码支付 —— 从实时消息中心 pay.js 移植到 Python。

与实时消息中心共用同一微信商户号（pay-config.json + 商户私钥证书）。
回调经 gldong.com（东京反代重庆）：POST /pay/notify，验签后自动发码。

依赖：cryptography（RSA-SHA256 签名/验签 + AES-256-GCM 回调解密）。
配置：wxpay/pay-config.json（mch_id/app_id/api_v3_key/merchant_serial_no/
      wechatpay_public_key）+ wxpay/apiclient_key.pem（商户 API 私钥）。
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WXPAY_DIR = os.path.join(HERE, "wxpay")
CONFIG_FILE = os.path.join(WXPAY_DIR, "pay-config.json")
KEY_FILE = os.path.join(WXPAY_DIR, "apiclient_key.pem")
API_BASE = "https://api.mch.weixin.qq.com"
# 微信服务器回调地址（POST 不受未备案域名 GET 拦截影响，经东京反代到重庆）
NOTIFY_URL = "https://gldong.com/sl-api/pay/notify"


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _read_key():
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def is_enabled():
    c = load_config()
    return bool(c.get("mch_id") and c.get("app_id") and c.get("api_v3_key")
                and c.get("merchant_serial_no") and _read_key())


def config():
    c = load_config()
    return {
        "mchid": c.get("mch_id", ""),
        "appid": c.get("app_id", ""),
        "api_v3_key": c.get("api_v3_key", ""),
        "serial_no": c.get("merchant_serial_no", ""),
        "public_key": c.get("wechatpay_public_key", ""),
    }


# ------------------------------------------------------------- 出站请求签名
def _sign(message, key_pem):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    sig = key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def _authorization(method, url_path, body_str, conf, key_pem):
    timestamp = str(int(time.time()))
    nonce = os.urandom(16).hex()
    message = "%s\n%s\n%s\n%s\n%s\n" % (method, url_path, timestamp,
                                        nonce, body_str)
    signature = _sign(message, key_pem)
    return ('WECHATPAY2-SHA256-RSA2048 mchid="%s",nonce_str="%s",'
            'signature="%s",timestamp="%s",serial_no="%s"'
            % (conf["mchid"], nonce, signature, timestamp, conf["serial_no"]))


def wx_request(method, url_path, body=None):
    conf = config()
    key_pem = _read_key()
    body_str = json.dumps(body) if body is not None else ""
    req = urllib.request.Request(
        API_BASE + url_path,
        data=body_str.encode() if body is not None else None,
        method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("Accept-Language", "en_US")   # 微信 APIv3 必需（zh_CN 实测 406）
    req.add_header("Authorization",
                   _authorization(method, url_path, body_str, conf, key_pem))
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError("微信API %s: %s" % (e.code, detail))


def create_native_order(out_trade_no, total_fen, description):
    """Native 下单 → 返回 code_url（weixin://...，前端渲染成二维码）。"""
    conf = config()
    r = wx_request("POST", "/v3/pay/transactions/native", {
        "appid": conf["appid"], "mchid": conf["mchid"],
        "description": description, "out_trade_no": out_trade_no,
        "notify_url": NOTIFY_URL,
        "amount": {"total": int(total_fen), "currency": "CNY"},
    })
    return r["code_url"]


# ------------------------------------------------------------- 回调验签+解密
def decrypt_resource(r):
    """AES-256-GCM 解密回调 resource（密文 base64，末尾 16 字节为 tag）。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = config()["api_v3_key"].encode("utf-8")
    nonce = r["nonce"].encode("utf-8")
    aad = (r.get("associated_data") or "").encode("utf-8") or None
    data = base64.b64decode(r["ciphertext"])          # 密文 || tag
    return AESGCM(key).decrypt(nonce, data, aad).decode("utf-8")


def verify_notify_signature(headers, raw_body):
    """验微信回调签名：签名串 = timestamp\\nnonce\\nbody\\n，RSA-SHA256。
    用微信支付公钥模式（2024 新商户无平台证书）。headers 大小写不敏感。"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    def h(name):
        for k, v in headers.items():
            if k.lower() == name.lower():
                return v or ""
        return ""

    signature = h("Wechatpay-Signature")
    timestamp = h("Wechatpay-Timestamp")
    nonce = h("Wechatpay-Nonce")
    if not (signature and timestamp and nonce):
        return False
    pub_pem = config().get("public_key", "")
    if not pub_pem:
        return False
    try:
        message = "%s\n%s\n%s\n" % (timestamp, nonce, raw_body)
        pub = serialization.load_pem_public_key(pub_pem.encode())
        pub.verify(base64.b64decode(signature), message.encode(),
                   padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False
