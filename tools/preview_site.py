# -*- coding: utf-8 -*-
"""本地预览首页/内页并截图，检查资源 404 / JS 报错。"""
import http.server
import json
import os
import socketserver
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "website")
OUT = os.path.join(ROOT, "out")
os.makedirs(OUT, exist_ok=True)
PORT = 8899


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=SITE, **kw)

    def log_message(self, *a):
        pass


def serve():
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    serve()
    from playwright.sync_api import sync_playwright
    errors, failed = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for page_name, full in (("index", "index.html"),
                                ("introduce", "introduce.html"),
                                ("guide", "guide.html")):
            pg = b.new_page(viewport={"width": 1440, "height": 960})
            pg.on("pageerror", lambda e: errors.append("%s: %s" % (page_name, e)))
            pg.on("response", lambda r: failed.append(
                "%s: HTTP %s %s" % (page_name, r.status, r.url))
                if r.status >= 400 else None)
            pg.on("console", lambda m: errors.append("%s console.%s: %s"
                                                     % (page_name, m.type, m.text))
                  if m.type == "error" else None)
            pg.on("requestfailed", lambda r: failed.append(
                "%s: %s %s" % (page_name, r.url, r.failure)))
            pg.goto("http://127.0.0.1:%d/%s" % (PORT, full),
                    wait_until="networkidle")
            pg.wait_for_timeout(1200)
            pg.screenshot(path=os.path.join(ROOT, "out", "new_%s_top.png" % page_name))
            if page_name == "index":
                pg.evaluate("window.scrollTo(0, 900)")
                pg.wait_for_timeout(700)
                pg.screenshot(path=os.path.join(ROOT, "out", "new_idx_mid.png"))
                pg.evaluate("window.scrollTo(0, 1900)")
                pg.wait_for_timeout(700)
                pg.screenshot(path=os.path.join(ROOT, "out", "new_idx_oss.png"))
                pg.evaluate("window.scrollTo(0, 99999)")
                pg.wait_for_timeout(700)
                pg.screenshot(path=os.path.join(ROOT, "out", "new_idx_bot.png"))
            pg.close()
            # 移动端
            pg2 = b.new_page(viewport={"width": 390, "height": 844})
            pg2.on("response", lambda r: failed.append(
                "%s(m): HTTP %s %s" % (page_name, r.status, r.url))
                if r.status >= 400 else None)
            pg2.goto("http://127.0.0.1:%d/%s" % (PORT, full),
                     wait_until="networkidle")
            pg2.wait_for_timeout(800)
            pg2.screenshot(path=os.path.join(ROOT, "out", "new_%s_m.png" % page_name))
            pg2.close()
        b.close()
    print(json.dumps({"errors": errors, "failed": failed},
                     ensure_ascii=False, indent=1))
    print("OK" if not errors and not failed else "HAS ISSUES")


if __name__ == "__main__":
    main()