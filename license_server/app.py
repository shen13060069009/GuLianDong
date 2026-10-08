# -*- coding: utf-8 -*-
"""股联动 GuLianDong 授权服务端（零依赖：纯 Python 标准库 + SQLite）。

接口：
    POST /api/activate   {code, machine_id, machine_info?}  → 激活并返回签名授权
    POST /api/verify     {code, machine_id}                 → 心跳/续验（7 天一次）
    GET  /admin                                            → 管理页（token 登录）
    POST /api/admin/create   {count?, note?}               → 生成激活码
    POST /api/admin/list     {}                            → 全部激活码
    POST /api/admin/unbind   {code}                        → 解绑机器（换机）
    POST /api/admin/ban      {code, ban:true|false}        → 封禁/解封

设计决策：
  * 纯标准库 http.server —— 宝塔机器不装任何 pip 包，python3 直接跑；
  * Ed25519 签名授权（src/ed25519.py 同一份实现），客户端只内置公钥，
    伪造授权 = 破私钥；
  * 1 码绑 1 机，重复激活同一机幂等；换机走管理页解绑；
  * 心跳只用于远程封禁生效，客户端断网宽限 14 天（买断制，宁可宽勿严）。

启动：python3 app.py   （建议 systemd 常驻，见 deploy.md）
"""
import json
import os
import sqlite3
import sys
import threading
import time
from base64 import urlsafe_b64encode, urlsafe_b64decode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ed25519 import sign as ed_sign  # noqa: E402
try:
    import wxpay as _wxpay           # noqa: E402  微信 Native 扫码（需 cryptography）
    if not _wxpay.is_enabled():
        _wxpay = None
except Exception:
    _wxpay = None

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "license.db")
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
SEED = bytes.fromhex(os.environ.get("LICENSE_SEED", CFG["ed25519_seed"]))
ADMIN_TOKEN = os.environ.get("LICENSE_TOKEN", CFG["admin_token"])
# 探针/多实例用：环境变量可覆盖端口与库文件（不设置 = 生产配置，行为不变）
PORT = int(os.environ.get("LICENSE_PORT", CFG.get("port", 8787)))
if os.environ.get("LICENSE_DB"):
    DB_PATH = os.environ["LICENSE_DB"]

_code_lock = threading.Lock()          # sqlite 跨线程写保护
_db_lock = threading.Lock()


# ---------------------------------------------------------------- 授权签发
def _b64(raw):
    return urlsafe_b64encode(raw).decode().rstrip("=")


def _issue_license(code, machine_id):
    """签发授权 token: b64(payload).b64(sig)。买断制：无过期时间。"""
    payload = {"v": 1, "product": "guliandong", "edition": "pro",
               "code": code, "mid": machine_id,
               "iat": int(time.time()), "exp": None}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = ed_sign(raw, SEED)
    return _b64(raw) + "." + _b64(sig)


# ---------------------------------------------------------------- 数据库
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # ⚠ 不设这个 SELECT * 是裸元组
    conn.execute("""CREATE TABLE IF NOT EXISTS codes(
        code TEXT PRIMARY KEY,
        note TEXT DEFAULT '',
        status TEXT DEFAULT 'unused',        -- unused | active | banned
        machine_id TEXT DEFAULT NULL,
        machine_info TEXT DEFAULT '',
        activated_at TEXT,
        last_seen TEXT,
        created_at TEXT)""")
    # kv：引流/支付等运营配置（后台可随时改，客户端/官网实时读）
    conn.execute("""CREATE TABLE IF NOT EXISTS kv(
        key TEXT PRIMARY KEY, val TEXT)""")
    # orders：自动发货订单（支付回调写入 → 买家凭订单号查码）
    conn.execute("""CREATE TABLE IF NOT EXISTS orders(
        order_id TEXT PRIMARY KEY,
        code TEXT,
        amount REAL DEFAULT 0,
        paid_at TEXT,
        created_at TEXT)""")
    # wxorders：微信 Native 扫码订单（官网购买 → 微信回调自动发货）
    conn.execute("""CREATE TABLE IF NOT EXISTS wxorders(
        out_trade_no TEXT PRIMARY KEY,
        amount_fen INTEGER DEFAULT 0,
        description TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',   -- pending | paid
        code TEXT,
        transaction_id TEXT DEFAULT '',
        created_at TEXT,
        paid_at TEXT)""")
    return conn


def get_kv(key, default=None):
    conn = db()
    row = conn.execute("SELECT val FROM kv WHERE key=?", (key,)).fetchone()
    conn.close()
    if row:
        try:
            return json.loads(row["val"])
        except Exception:
            return default
    return default


def set_kv(key, val):
    conn = db()
    conn.execute("INSERT INTO kv(key,val) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET val=excluded.val",
                 (key, json.dumps(val, ensure_ascii=False)))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- 运营配置
# 引流位（客户端联动菜单/托盘展示，官网也可读）——后台可随时改
DEFAULT_PROMO = {
    "enabled": True,
    "title": "实时消息中心",
    "desc": "AI 监控全网财经消息 · 比行情软件快一步",
    "url": "https://fupanxia.cn",
    "button": "免费看实时消息",
}


def get_promo():
    return {**DEFAULT_PROMO, **(get_kv("promo", {}) or {})}


# 官网购买/自动发货配置
DEFAULT_SITEPAY = {
    "price": 98,
    "pay_link": "",      # 购买按钮跳转的支付页（微信收款页/商城链接）
    "qr_img": "",        # 微信收款码图片 URL（无 pay_link 时官网弹码展示）
    "contact": "购买后联系卖家发货",
}


def get_sitepay():
    return {**DEFAULT_SITEPAY, **(get_kv("sitepay", {}) or {})}


# 自愿赞助（客户端「关于」窗口展示）。纯自愿，绝不作为付费墙——
# 客户端拉不到就直接不显示这一栏，功能不受任何影响。
DEFAULT_DONATE = {
    "enabled": False,
    "title": "支持开源项目",
    "desc": "自愿赞助，助力持续维护",
    "url": "",          # 爱发电 / GitHub Sponsors 等
    "button": "自愿赞助",
}


def get_donate():
    return {**DEFAULT_DONATE, **(get_kv("donate", {}) or {})}


def _webhook_token():
    """支付回调令牌：首次访问自动生成，后台查看/重置。"""
    tok = get_kv("pay_webhook_token")
    if not tok:
        import secrets
        tok = secrets.token_hex(16)
        set_kv("pay_webhook_token", tok)
    return tok


def gen_codes(count=1, note=""):
    import secrets
    out = []
    with _code_lock:
        conn = db()
        for _ in range(count):
            while True:
                chars = "".join(secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ")
                                for _ in range(12))
                # 12 位无易混淆字符（去 0/O/1/I），SL-XXXX-XXXX-XXXX
                code = "SL-" + "-".join(chars[i:i + 4] for i in (0, 4, 8))
                if not conn.execute("SELECT 1 FROM codes WHERE code=?",
                                    (code,)).fetchone():
                    break
            conn.execute("INSERT INTO codes(code, note, created_at) VALUES(?,?,?)",
                         (code, note, time.strftime("%Y-%m-%d %H:%M:%S")))
            out.append(code)
        conn.commit()
        conn.close()
    return out


def _wx_deliver(out_trade_no, transaction_id="", amount_fen=0, note="微信自动发货"):
    """微信支付成功 → 幂等发激活码（同一单号永远返回同一个码）。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _db_lock:
        conn = db()
        row = conn.execute("SELECT code FROM wxorders WHERE out_trade_no=?",
                           (out_trade_no,)).fetchone()
        if row is None:
            # 未知单号（对账补救）：直接建单发货，不丢单
            code = gen_codes(1, note="%s:%s" % (note, out_trade_no))[0]
            conn.execute("""INSERT INTO wxorders(out_trade_no, amount_fen,
                            description, status, code, transaction_id,
                            created_at, paid_at)
                            VALUES(?,?,?,?,?,?,?,?)""",
                         (out_trade_no, int(amount_fen or 0), "", "paid",
                          code, transaction_id, now, now))
            conn.commit()
            conn.close()
            return code
        if row["code"]:
            conn.close()
            return row["code"]                    # 已发货，幂等返回
        code = gen_codes(1, note="%s:%s" % (note, out_trade_no))[0]
        conn.execute("""UPDATE wxorders SET status='paid', code=?,
                        transaction_id=?, paid_at=? WHERE out_trade_no=?""",
                     (code, transaction_id, now, out_trade_no))
        conn.commit()
        conn.close()
        return code


def activate(code, machine_id, machine_info=""):
    with _db_lock:
        conn = db()
        row = conn.execute("SELECT * FROM codes WHERE code=?",
                           (code,)).fetchone()
        if not row:
            conn.close()
            return None, "激活码不存在"
        if row["status"] == "banned":
            conn.close()
            return None, "激活码已被封禁，请联系卖家"
        if row["status"] == "active" and row["machine_id"] != machine_id:
            conn.close()
            return None, "激活码已绑定其他机器（换机请联系卖家解绑）"
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("""UPDATE codes SET status='active', machine_id=?,
                        machine_info=?, activated_at=COALESCE(activated_at,?),
                        last_seen=? WHERE code=?""",
                     (machine_id, machine_info[:200], now, now, code))
        conn.commit()
        conn.close()
    return _issue_license(code, machine_id), "ok"


def verify(code, machine_id):
    with _db_lock:
        conn = db()
        row = conn.execute("SELECT status, machine_id FROM codes WHERE code=?",
                           (code,)).fetchone()
        if not row:
            conn.close()
            return None, "激活码不存在"
        status, mid = row
        if status == "banned":
            conn.close()
            return None, "已封禁"
        if mid != machine_id:
            conn.close()
            return None, "机器不匹配"
        conn.execute("UPDATE codes SET last_seen=? WHERE code=?",
                     (time.strftime("%Y-%m-%d %H:%M:%S"), code))
        conn.commit()
        conn.close()
    return _issue_license(code, machine_id), "ok"


# ---------------------------------------------------------------- HTTP
ADMIN_HTML = open(os.path.join(HERE, "admin.html"), encoding="utf-8").read()


def _norm_path(p):
    """路由归一化：/api/ 前缀可有可无，且允许多层（nginx 反代可能叠加）。
    /activate、/api/activate、/api/api/activate 都归到 /activate。"""
    while p.startswith("/api/"):
        p = p[4:]
    return p or "/"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):        # 安静模式：只留错误
        if self.path.startswith("/api/admin") or "error" in fmt.lower():
            sys.stderr.write("[license] %s %s\n" % (self.address_string(), fmt % args))

    # ---- helpers
    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def _raw_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return self.rfile.read(n).decode("utf-8", "replace")
        except Exception:
            return ""

    def _admin_ok(self):
        return self.headers.get("X-Admin-Token", "") == ADMIN_TOKEN

    # ---- routes
    def do_GET(self):
        path_only, _, qs = self.path.partition("?")
        p = _norm_path(path_only)
        q = dict(pair.split("=", 1) for pair in qs.split("&")
                 if "=" in pair) if qs else {}
        if p == "/admin":
            body = ADMIN_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p == "/ping":
            self._json({"ok": True, "service": "guliandong-license"})
        elif p == "/promo":
            # 引流配置（公开）：客户端联动菜单/托盘、官网共用
            self._json(get_promo())
        elif p == "/donate":
            # 自愿赞助入口（公开）。客户端拉不到 / enabled=False 就整栏隐藏。
            self._json(get_donate())
        elif p == "/sitepay":
            # 官网购买配置（公开，绝不含 webhook token）
            sp = get_sitepay()
            self._json({"price": sp["price"], "pay_link": sp["pay_link"],
                        "qr_img": sp["qr_img"], "contact": sp["contact"],
                        "wxpay": bool(_wxpay)})
        elif p == "/pay/status":
            # 官网购买轮询：微信 Native 订单是否已支付（支付成功即返回激活码）
            oid = (q.get("order_id") or "").strip()
            if not oid:
                self._json({"ok": False, "msg": "缺少 order_id"})
                return
            conn = db()
            row = conn.execute("SELECT status, code FROM wxorders "
                               "WHERE out_trade_no=?", (oid,)).fetchone()
            conn.close()
            if row and row["status"] == "paid" and row["code"]:
                self._json({"ok": True, "paid": True, "code": row["code"]})
            else:
                self._json({"ok": True, "paid": False})
        elif p == "/pay/query":
            # 买家凭订单号查激活码（自动发货兜底通道；兼容微信订单与手工回调单）
            oid = (q.get("order_id") or "").strip()
            if not oid:
                self._json({"ok": False, "msg": "缺少 order_id"})
                return
            conn = db()
            row = conn.execute("SELECT code, paid_at FROM orders "
                               "WHERE order_id=?", (oid,)).fetchone()
            if not (row and row["code"]):
                row = conn.execute("SELECT code, paid_at FROM wxorders "
                                   "WHERE out_trade_no=?", (oid,)).fetchone()
            conn.close()
            if row and row["code"]:
                self._json({"ok": True, "paid": True, "code": row["code"],
                            "paid_at": row["paid_at"]})
            else:
                self._json({"ok": True, "paid": False,
                            "msg": "订单不存在或未支付"})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            self._route_post()
        except Exception as e:
            # 顶层兜底：任何未捕获异常都回 500 JSON，绝不能静默断连
            # （否则客户端只看到 RemoteDisconnected，无法定位）
            import traceback
            traceback.print_exc()
            try:
                self._json({"ok": False, "msg": "服务器内部错误: %s" % e}, 500)
            except Exception:
                pass

    def _route_post(self):
        p = _norm_path(self.path.split("?")[0])
        if p == "/activate":
            b = self._body()
            lic, msg = activate(str(b.get("code", "")).strip().upper(),
                                str(b.get("machine_id", "")),
                                str(b.get("machine_info", "")))
            if lic is None:
                self._json({"ok": False, "msg": msg})
            else:
                self._json({"ok": True, "msg": "激活成功",
                            "license": lic, "payload": _payload_of(lic)})
        elif p == "/verify":
            b = self._body()
            lic, msg = verify(str(b.get("code", "")).strip().upper(),
                              str(b.get("machine_id", "")))
            if lic is None:
                self._json({"ok": False, "msg": msg})
            else:
                self._json({"ok": True, "license": lic})
        elif p == "/promo":
            # POST /promo 也可读（客户端 _post 走这里）
            self._json(get_promo())
        elif p == "/donate":
            # 自愿赞助入口（客户端「关于」窗口）。enabled=False 或 url 为空
            # 时客户端不显示 —— 赞助永不影响功能。
            self._json(get_donate())
        elif p == "/wxpay/order":
            # 官网购买：微信 Native 下单 → 返回 code_url（前端渲染二维码）。
            # 带 X-Admin-Token 时可指定 amount_fen（后台测试下单 1 分钱用）。
            if not _wxpay:
                self._json({"ok": False, "msg": "微信支付未启用"})
                return
            b = self._body()
            sp = get_sitepay()
            total_fen = max(1, round(float(sp.get("price", 98)) * 100))
            if self._admin_ok() and b.get("amount_fen"):
                total_fen = int(b["amount_fen"])
            import secrets as _secrets
            oid = "GL" + str(int(time.time() * 1000)) + _secrets.choice(
                "ABCDEFGHJKLMNPQRSTUVWXYZ23456789") + _secrets.choice(
                "ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
            desc = "股联动 GuLianDong 激活码"
            try:
                code_url = _wxpay.create_native_order(oid, total_fen, desc)
            except Exception as e:
                self._json({"ok": False, "msg": str(e)})
                return
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            with _db_lock:
                conn = db()
                conn.execute("""INSERT INTO wxorders(out_trade_no, amount_fen,
                                description, status, created_at)
                                VALUES(?,?,?,?,?)""",
                             (oid, total_fen, desc, "pending", now))
                conn.commit()
                conn.close()
            self._json({"ok": True, "order_id": oid, "code_url": code_url,
                        "total_fen": total_fen,
                        "price": round(total_fen / 100.0, 2)})
        elif p == "/pay/notify":
            # 支付回调，双协议：
            # A) 微信官方 APIv3 回调（带 Wechatpay-Signature 头）：
            #    验签 → AES-GCM 解密 resource → out_trade_no/trade_state → 发码
            # B) 自建支付系统简单回调：X-Pay-Token 头或 body.token 鉴权，
            #    body: {order_id, amount?, status?} → 返回 {ok, code}
            if _wxpay and self.headers.get("Wechatpay-Signature"):
                raw = self._raw_body()
                if not _wxpay.verify_notify_signature(self.headers, raw):
                    self._json({"code": "FAIL", "message": "验签失败"}, 401)
                    return
                try:
                    env = json.loads(raw or b"{}")
                    plain = json.loads(_wxpay.decrypt_resource(env.get("resource", {})))
                except Exception as e:
                    self._json({"code": "FAIL", "message": "解密失败: %s" % e}, 400)
                    return
                oid = str(plain.get("out_trade_no", "")).strip()
                if plain.get("trade_state") != "SUCCESS" or not oid:
                    # 非支付成功状态：确认收到，不发货
                    self._json({"code": "SUCCESS", "message": "忽略"})
                    return
                _wx_deliver(oid, str(plain.get("transaction_id", "")),
                            int(plain.get("amount", {}).get("payer_total",
                                plain.get("amount", {}).get("total", 0) or 0)))
                self._json({"code": "SUCCESS", "message": "OK"})
                return
            b = self._body()
            tok = self.headers.get("X-Pay-Token", "") or str(b.get("token", ""))
            if tok != _webhook_token():
                self._json({"ok": False, "msg": "token 无效"}, 403)
                return
            if str(b.get("status", "paid")).lower() not in ("paid", "success",
                                                            "ok", "true", "1"):
                self._json({"ok": False, "msg": "status 非支付成功，忽略"})
                return
            oid = str(b.get("order_id", "")).strip()
            if not oid:
                self._json({"ok": False, "msg": "缺少 order_id"})
                return
            with _db_lock:
                conn = db()
                row = conn.execute("SELECT code FROM orders WHERE order_id=?",
                                   (oid,)).fetchone()
                if row and row["code"]:
                    conn.close()
                    self._json({"ok": True, "code": row["code"],
                                "msg": "订单已发货（幂等返回）"})
                    return
                code = gen_codes(1, note="自动发货:" + oid)[0]
                conn.execute("""INSERT INTO orders(order_id, code, amount,
                                paid_at, created_at) VALUES(?,?,?,?,?)""",
                             (oid, code, float(b.get("amount", 0) or 0),
                              time.strftime("%Y-%m-%d %H:%M:%S"),
                              time.strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit()
                conn.close()
            self._json({"ok": True, "code": code, "msg": "发货成功"})
        elif p.startswith("/admin/"):
            if not self._admin_ok():
                self._json({"ok": False, "msg": "token 无效"}, 403)
                return
            b = self._body()
            act = self.path.rsplit("/", 1)[-1]
            if act == "create":
                codes = gen_codes(int(b.get("count", 1)), str(b.get("note", "")))
                self._json({"ok": True, "codes": codes})
            elif act == "list":
                conn = db()
                rows = conn.execute(
                    "SELECT code,note,status,machine_id,machine_info,"
                    "activated_at,last_seen,created_at FROM codes "
                    "ORDER BY created_at DESC").fetchall()
                conn.close()
                self._json({"ok": True, "rows": [
                    dict(zip(("code", "note", "status", "machine_id",
                              "machine_info", "activated_at", "last_seen",
                              "created_at"), r)) for r in rows]})
            elif act == "unbind":
                conn = db()
                conn.execute("UPDATE codes SET status='unused', machine_id=NULL, "
                             "activated_at=NULL WHERE code=?", (b.get("code"),))
                conn.commit()
                conn.close()
                self._json({"ok": True, "msg": "已解绑"})
            elif act == "ban":
                st = "banned" if b.get("ban", True) else "active"
                conn = db()
                conn.execute("UPDATE codes SET status=? WHERE code=?",
                             (st, b.get("code")))
                conn.commit()
                conn.close()
                self._json({"ok": True, "msg": "已" + ("封禁" if st == "banned" else "解封")})
            elif act == "promo":
                cur = get_promo()
                for k in ("enabled", "title", "desc", "url", "button"):
                    if k in b:
                        cur[k] = b[k]
                cur["enabled"] = bool(cur.get("enabled"))
                set_kv("promo", cur)
                self._json({"ok": True, "msg": "引流配置已保存", "promo": cur})
            elif act == "sitepay_get":
                sp = get_sitepay()
                sp["webhook_token"] = _webhook_token()
                self._json({"ok": True, "sitepay": sp})
            elif act == "donate":
                cur = get_donate()
                for k in ("enabled", "title", "desc", "url", "button"):
                    if k in b:
                        cur[k] = b[k]
                cur["enabled"] = bool(cur.get("enabled"))
                set_kv("donate", cur)
                self._json({"ok": True, "msg": "赞助配置已保存", "donate": cur})
            elif act == "sitepay":
                cur = get_sitepay()
                for k in ("price", "pay_link", "qr_img", "contact"):
                    if k in b:
                        cur[k] = b[k]
                if "price" in b:
                    try:
                        cur["price"] = float(b["price"])
                    except (TypeError, ValueError):
                        pass
                set_kv("sitepay", cur)
                if b.get("reset_token"):
                    set_kv("pay_webhook_token", "")
                cur["webhook_token"] = _webhook_token()
                self._json({"ok": True, "msg": "支付配置已保存", "sitepay": cur})
            elif act == "wxpay":
                if _wxpay:
                    c = _wxpay.config()
                    self._json({"ok": True, "enabled": True,
                                "mchid": c["mchid"], "appid": c["appid"],
                                "notify_url": _wxpay.NOTIFY_URL})
                else:
                    self._json({"ok": True, "enabled": False,
                                "msg": "微信支付未启用（缺配置或 cryptography）"})
            elif act == "wxpay_testorder":
                # 1 分钱测试单：真实走微信下单，扫码支付后验证回调自动发货
                if not _wxpay:
                    self._json({"ok": False, "msg": "微信支付未启用"})
                    return
                import secrets as _secrets
                oid = "GLT" + str(int(time.time() * 1000)) + _secrets.choice(
                    "ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
                try:
                    code_url = _wxpay.create_native_order(
                        oid, 1, "股联动测试单(0.01元)")
                except Exception as e:
                    self._json({"ok": False, "msg": str(e)})
                    return
                now = time.strftime("%Y-%m-%d %H:%M:%S")
                with _db_lock:
                    conn = db()
                    conn.execute("""INSERT INTO wxorders(out_trade_no,
                                    amount_fen, description, status, created_at)
                                    VALUES(?,?,?,?,?)""",
                                 (oid, 1, "股联动测试单(0.01元)", "pending", now))
                    conn.commit()
                    conn.close()
                self._json({"ok": True, "order_id": oid, "code_url": code_url})
            else:
                self._json({"ok": False, "msg": "unknown action"}, 404)
        else:
            self._json({"error": "not found"}, 404)


def _payload_of(lic):
    try:
        raw = urlsafe_b64decode(lic.split(".")[0] + "==")
        return json.loads(raw)
    except Exception:
        return {}


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("[license-server] listening on :%d  db=%s" % (PORT, DB_PATH))
    srv.serve_forever()
