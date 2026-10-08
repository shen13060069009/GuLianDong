# -*- coding: utf-8 -*-
"""p45 —— 授权体系端到端探针。

自包含：临时目录起一个独立端口的服务端（独立 SQLite），跑完整商业闭环：
    激活码生成 → 错码拒绝 → 正码激活 → 本地离线验签 → 重复激活幂等
    → 他机抢绑拒绝 → 封禁心跳下发 → 解封解绑复活 → 伪造 token 拒绝

用法：
    python probe/p45_license_e2e.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PY = sys.executable
PORT = 8790
SERVER_URL = "http://127.0.0.1:%d" % PORT
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  %s %s %s" % ("✓" if cond else "✗", name, detail))


def post(path, body=None, tok=None):
    h = {"Content-Type": "application/json"}
    if tok:
        h["X-Admin-Token"] = tok
    req = urllib.request.Request(SERVER_URL + path,
                                 data=json.dumps(body or {}).encode(),
                                 headers=h, method="POST")
    return json.loads(op.open(req, timeout=8).read())


def main():
    print("=" * 84)
    print("p45 授权体系端到端（独立端口 %d + 临时库，不碰真实数据）" % PORT)
    print("=" * 84)

    tmp = tempfile.mkdtemp(prefix="p45_lic_")
    # 服务端用随机密钥对（探针内把客户端公钥同步换成它，见下）；
    # 端口/库/密钥经环境变量注入 —— app.py 的 config.json 是部署语义，探针不碰
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from ed25519 import genkeypair
    seed, pub = genkeypair()
    cfg = {"ed25519_seed": seed.hex(), "ed25519_pub": pub.hex(),
           "admin_token": "tok_p45", "port": PORT}
    json.dump(cfg, open(os.path.join(tmp, "config.json"), "w"))

    env = dict(os.environ, PYTHONPATH=ROOT,
               LICENSE_PORT=str(PORT), LICENSE_DB=os.path.join(tmp, "license.db"),
               LICENSE_SEED=seed.hex(), LICENSE_TOKEN="tok_p45")
    proc = subprocess.Popen([PY, os.path.join(ROOT, "license_server", "app.py")],
                            cwd=tmp, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    # 客户端指向测试服务端；测试服务端是随机密钥对，
    # 必须把客户端内置公钥也换成测试公钥才能闭环（生产公钥一致性单测在最后）
    os.environ["GULIANDONG_LICENSE_SERVER"] = SERVER_URL
    import importlib
    from src import licensing
    importlib.reload(licensing)
    licensing.PUBKEY = pub
    # ⚠ 存储隔离：授权文件指向临时目录，注册表读写打桩——
    # 否则探针会把测试签发的授权写进真实 %APPDATA%，污染开发机状态（实测踩过）
    licensing._APPDATA = tmp
    licensing._LIC_FILE = os.path.join(tmp, "license.dat")
    licensing._reg_get = lambda name: None
    licensing._reg_set = lambda name, value: None

    real_mid = licensing.machine_id()
    try:
        for _ in range(40):
            try:
                op.open(SERVER_URL + "/api/ping", timeout=2)
                break
            except Exception:
                time.sleep(0.25)
                if proc.poll() is not None:
                    out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
                    raise RuntimeError("测试服务端退出:\n%s" % out[-1500:])
        else:
            proc.terminate()
            out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
            raise RuntimeError("测试服务端 10s 未就绪:\n%s" % out[-1500:])
        print("  服务端就绪 (db=%s)" % tmp)

        codes = post("/api/admin/create", {"count": 2, "note": "p45"},
                     "tok_p45")["codes"]
        check("激活码生成", len(codes) == 2 and codes[0].startswith("SL-"), codes[0])

        r = licensing.activate("SL-XXXX-YYYY-ZZZZ")
        check("错码拒绝", r == (False, "激活码不存在"), str(r[1]))

        r = licensing.activate(codes[0])
        check("正码激活", r[0], r[1])
        st = licensing.check(force=True)
        check("状态转已激活", st["state"] == "activated" and st["code"] == codes[0])

        check("重复激活幂等", licensing.activate(codes[0])[0])
        check("本地心跳续验", licensing.heartbeat()[0])

        r = post("/api/activate", {"code": codes[0], "machine_id": "FAKE machine"})
        check("他机抢绑拒绝", not r["ok"] and "绑定其他机器" in r["msg"], r["msg"])

        post("/api/admin/ban", {"code": codes[0], "ban": True}, "tok_p45")
        ok, msg = licensing.heartbeat()
        check("封禁心跳下发", not ok and "封禁" in msg, msg)
        check("封禁后本地授权清除", licensing._load_license() is None)

        post("/api/admin/ban", {"code": codes[0], "ban": False}, "tok_p45")
        post("/api/admin/unbind", {"code": codes[0]}, "tok_p45")
        check("解封+解绑后复活", licensing.activate(codes[0])[0])

        # 伪造 token（错误的私钥签名）必须被本地验签拒掉
        from ed25519 import genkeypair as _g2
        s2, _ = _g2()
        from ed25519 import sign as _sign
        raw = json.dumps({"v": 1, "product": "guliandong", "code": "FAKE",
                          "mid": real_mid, "iat": int(time.time()),
                          "exp": None}, sort_keys=True,
                         separators=(",", ":")).encode()
        from base64 import urlsafe_b64encode as b64
        bad = b64(raw).decode().rstrip("=") + "." + b64(_sign(raw, s2)).decode().rstrip("=")
        check("伪造签名 token 被拒", licensing._verify_token(bad) is None)

        # 客户端内置公钥必须与生产服务端 config.json 一致
        prod = json.load(open(os.path.join(ROOT, "license_server",
                                           "config.json"), encoding="utf-8"))
        check("生产公钥一致性",
              open(os.path.join(ROOT, "src", "licensing.py"), encoding="utf-8")
              .read().find(prod["ed25519_pub"]) > 0,
              "licensing.py 内置 = 服务端 config")
    finally:
        proc.terminate()
        try:
            os.remove(os.path.join(ROOT, "out", "lic_backup.json"))
        except OSError:
            pass

    print("-" * 84)
    print("结果：%d/%d PASS" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("失败项:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
