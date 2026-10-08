# -*- coding: utf-8 -*-
"""股票主数据层

拉取全 A 股（沪主板 / 深主板 / 创业板 / 科创板 / 北交所）的「代码 + 简称」，
本地缓存成 JSON，并生成拼音首字母索引。

数据源策略（实测 2026-10）：
  * 首选 新浪 Market_Center.getHQNodeData —— 不封 IP，node=hs_a 覆盖沪深北，
    单页 100 条，共约 55 页；顺带返回实时价与涨跌幅（可复用于高亮配色）。
  * 兜底 东财 push2/clist —— 数据一样全，但风控严格（实测连续请求后
    RemoteDisconnected），只在前者失败时启用，且必须带退避重试。

设计要点：
  * 主数据只做事实，不做别名。别名走 matcher 的 L2 层，便于用户自行维护。
  * 名称需清洗：新浪会带 XD/XR/DR 除权标记前缀，东财会混全角空格。
"""
import json
import os
import random
import time
import urllib.parse
import urllib.request

try:
    from .paths import data_dir
except ImportError:                     # 直接以脚本方式跑 src/stockdb.py 时
    from paths import data_dir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = data_dir()
CACHE = os.path.join(DATA_DIR, "stocks.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

SINA_URL = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData")
EM_CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
EM_FS_ALL = ",".join([
    "m:0+t:6", "m:0+t:80", "m:1+t:2", "m:1+t:23", "m:0+t:81+s:2048",
])


def market_of(code):
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def board_of(code):
    if code.startswith("688"):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("4", "8")):
        return "北交所"
    if code.startswith("60"):
        return "沪主板"
    if code.startswith(("000", "001", "002", "003")):
        return "深主板"
    return "其他"


def clean_name(raw):
    """清洗简称：去空白、剥离除权标记"""
    s = str(raw or "")
    for ch in ("\u3000", " ", "\t", "\xa0", "\r", "\n"):
        s = s.replace(ch, "")
    s = s.strip()
    if not s:
        return ""
    # XD/XR/DR 是除权除息标记，会周期性出现，必须剥掉才能稳定匹配
    for mark in ("XD", "XR", "DR"):
        if s.startswith(mark) and len(s) > len(mark) + 1:
            return s[len(mark):]
    return s


def is_valid(name):
    if not name or len(name) < 2:
        return False
    if "\u9000" in name:        # 退市
        return False
    if name.startswith("PT"):
        return False
    if "未上市" in name:
        return False
    return True


def _http_json(url, params=None, headers=None, timeout=20, retries=4, encoding="utf-8"):
    full = url if params is None else "%s?%s" % (url, urllib.parse.urlencode(params))
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(full)
            req.add_header("User-Agent", UA)
            req.add_header("Accept", "*/*")
            for k, v in (headers or {}).items():
                req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode(encoding, "ignore"))
        except Exception as e:
            last = e
            time.sleep(1.5 * (i + 1) + random.uniform(0, 1.0))
    raise RuntimeError("请求连续 %d 次失败: %s" % (retries, last))


def fetch_sina(verbose=True):
    """新浪全 A 股。node=hs_a 覆盖沪深北，单页上限 100。"""
    rows = []
    page = 1
    while True:
        params = {
            "page": str(page), "num": "100", "sort": "symbol", "asc": "1",
            "node": "hs_a", "symbol": "", "_s_r_a": "page",
        }
        data = _http_json(SINA_URL, params,
                          headers={"Referer": "https://finance.sina.com.cn"},
                          encoding="utf-8")
        if not isinstance(data, list) or not data:
            break
        kept = 0
        for it in data:
            code = str(it.get("code") or "").strip()
            name = clean_name(it.get("name"))
            if len(code) != 6 or not code.isdigit() or not is_valid(name):
                continue
            rows.append({"code": code, "name": name,
                         "market": market_of(code), "board": board_of(code)})
            kept += 1
        if verbose:
            print("  新浪 第 %2d 页 返回 %3d，有效 %3d，累计 %d"
                  % (page, len(data), kept, len(rows)))
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.25 + random.uniform(0.05, 0.15))
    return rows


def fetch_em(verbose=True):
    """东财兜底。风控严格，间隔放大到 1.2s。"""
    rows = []
    page = 1
    while True:
        params = {
            "pn": str(page), "pz": "100", "po": "0", "np": "1",
            "fltt": "2", "invt": "2", "fid": "f12",
            "fs": EM_FS_ALL, "fields": "f12,f14",
        }
        d = _http_json(EM_CLIST, params,
                       headers={"Referer": "https://quote.eastmoney.com/"})
        diff = (d.get("data") or {}).get("diff") or []
        if not diff:
            break
        for it in diff:
            code = str(it.get("f12") or "").strip()
            name = clean_name(it.get("f14"))
            if len(code) != 6 or not code.isdigit() or not is_valid(name):
                continue
            rows.append({"code": code, "name": name,
                         "market": market_of(code), "board": board_of(code)})
        if verbose:
            print("  东财 第 %2d 页 返回 %3d，累计 %d" % (page, len(diff), len(rows)))
        if len(diff) < 100:
            break
        page += 1
        time.sleep(1.2 + random.uniform(0.1, 0.5))
    return rows


def dedup(rows):
    uniq = {}
    for r in rows:
        uniq[r["code"]] = r
    return sorted(uniq.values(), key=lambda x: x["code"])


def add_pinyin(rows):
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        return False
    for r in rows:
        try:
            r["abbr"] = "".join(
                lazy_pinyin(r["name"], style=Style.FIRST_LETTER,
                            errors=lambda x: list(x))).lower()
        except Exception:
            r["abbr"] = ""
    return True


def fetch_all(verbose=True):
    """优先新浪；失败或数量明显不足（<3000）时退到东财"""
    try:
        rows = fetch_sina(verbose)
        if len(rows) >= 3000:
            return dedup(rows)
        if verbose:
            print("  新浪只拿到 %d 条，不足 3000，转东财兜底" % len(rows))
    except Exception as e:
        if verbose:
            print("  新浪失败（%s），转东财兜底" % e)
    return dedup(fetch_em(verbose))


def build(verbose=True):
    t0 = time.time()
    rows = fetch_all(verbose=verbose)
    has_py = add_pinyin(rows)
    if verbose:
        print("  拼音索引: %s" % ("已生成" if has_py else "跳过（未装 pypinyin）"))
        print("  共 %d 只，耗时 %.1fs" % (len(rows), time.time() - t0))
    payload = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(rows),
        "items": rows,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    if verbose:
        print("  已写入 %s (%.1f KB)" % (CACHE, os.path.getsize(CACHE) / 1024))
    return payload


def load(max_age_days=14, verbose=False):
    if os.path.exists(CACHE):
        with open(CACHE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        try:
            t = time.mktime(time.strptime(payload["updated_at"], "%Y-%m-%d %H:%M:%S"))
            if (time.time() - t) / 86400 <= max_age_days:
                return payload
        except Exception:
            pass
    return build(verbose=verbose)


if __name__ == "__main__":
    p = build()
    print("\n抽样校验：")
    by_code = {r["code"]: r for r in p["items"]}
    for code in ("600519", "000001", "300750", "688981", "920000"):
        h = by_code.get(code)
        print("  %s -> %s" % (code, ("%s [%s] abbr=%s" % (h["name"], h["board"], h.get("abbr", "")))
                              if h else "未找到"))
    boards = {}
    for r in p["items"]:
        boards[r["board"]] = boards.get(r["board"], 0) + 1
    print("\n板块分布：")
    for k, v in sorted(boards.items(), key=lambda x: -x[1]):
        print("  %-8s %5d" % (k, v))
