# -*- coding: utf-8 -*-
"""线上真机验证：https://www.gldong.com
- 首页 / 内页 渲染 + 资源 404 + JS 报错
- 极光动画是否真的在动（两次截图取样点比对）
- 关键下载链接是否存在
- 后台可登录性
"""
import json
import os
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = r'C:\Users\Administrator\WorkBuddy\2026-10-06-13-12-12\stocklens'
OUT = os.path.join(ROOT, 'out')
B = 'https://www.gldong.com'


def main():
    errors, bad = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, path in (('index', '/'), ('introduce', '/introduce.html'),
                           ('guide', '/guide.html')):
            pg = b.new_page(viewport={'width': 1440, 'height': 960})
            pg.on('pageerror', lambda e, n=name: errors.append('%s: %s' % (n, e)))
            pg.on('console', lambda m, n=name: errors.append(
                '%s console.%s: %s' % (n, m.type, m.text)) if m.type == 'error' else None)
            pg.on('response', lambda r, n=name: bad.append(
                '%s: HTTP %s %s' % (n, r.status, r.url)) if r.status >= 400 else None)
            pg.goto(B + path, wait_until='networkidle', timeout=60000)
            pg.wait_for_timeout(1500)
            pg.screenshot(path=os.path.join(OUT, 'live_%s_top.png' % name))
            if name == 'index':
                # 极光是否在动：间隔 2.5s 取样 hero 区域两次比对
                pg.evaluate('window.scrollTo(0,0)')
                pg.wait_for_timeout(500)
                box = pg.locator('.hero').bounding_box()
                clip = {'x': 0, 'y': 60, 'width': 1440, 'height': 460}
                a = os.path.join(OUT, '_a.png')
                pg.screenshot(path=a, clip=clip)
                pg.wait_for_timeout(2600)
                bb = os.path.join(OUT, '_b.png')
                pg.screenshot(path=bb, clip=clip)
                try:
                    from PIL import Image, ImageChops
                    im1 = Image.open(a).convert('RGB')
                    im2 = Image.open(bb).convert('RGB')
                    diff = ImageChops.difference(im1, im2)
                    bbox = diff.getbbox()
                    changed = 0
                    if bbox:
                        px = diff.crop(bbox).getdata()
                        changed = sum(1 for v in px if max(v) > 8)
                    total = im1.size[0] * im1.size[1]
                    pct = round(changed * 100.0 / total, 2)
                    print('极光动画：变化像素 %d (%.2f%%) bbox=%s' % (changed, pct, bbox))
                except Exception as e:
                    print('极光比对失败', e)
                for off, tag in ((760, 'mid'), (1750, 'oss'), (99999, 'bot')):
                    pg.evaluate('window.scrollTo(0,%d)' % off)
                    pg.wait_for_timeout(800)
                    pg.screenshot(path=os.path.join(OUT, 'live_idx_%s.png' % tag))
            pg.close()

        # 下载链接 HEAD 检查
        pg = b.new_page()
        for u in ('/download/GuLianDong-Setup-1.1.0.exe',
                  '/download/GuLianDong-1.1.0.zip',
                  '/download/GuLianDong-Source-1.1.0.zip'):
            r = pg.request.head(B + u, timeout=60000)
            print('  %-46s %s  %.1f MB' % (
                u, r.status,
                int(r.headers.get('content-length', 0)) / 1048576))
        # 后台
        r = pg.request.get(B + '/sl-api/admin', timeout=30000)
        print('  后台 /sl-api/admin ->', r.status)
        r = pg.request.get(B + '/sl-api/donate', timeout=30000)
        print('  /sl-api/donate ->', r.status, r.text()[:120])
        pg.close()
        b.close()

    print(json.dumps({'errors': errors, 'bad': bad}, ensure_ascii=False, indent=1))
    print('ONLINE OK' if not errors and not bad else 'ONLINE HAS ISSUES')


if __name__ == '__main__':
    main()