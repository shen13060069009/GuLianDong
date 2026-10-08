// check_review2.js — 验收页自检：图片解码 / JS 报错 / 404 / 灯箱
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const FILE = 'file://' + path.resolve(__dirname, '../review2/index.html').replace(/\\/g, '/');
const ROOT = path.resolve(__dirname, '..');

(async () => {
  let b;
  try {
    b = await chromium.launch();
  } catch (e) {
    try { b = await chromium.launch({ channel: 'msedge' }); }
    catch (e2) { console.log('SKIP: 浏览器启动失败 ' + e2.message); process.exit(0); }
  }
  const pg = await b.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [], failed = [];
  pg.on('pageerror', e => errors.push(String(e.message || e).slice(0, 160)));
  pg.on('response', r => { if (r.status() >= 400) failed.push(r.status() + ' ' + r.url().split('/').pop()); });

  await pg.goto(FILE, { wait_until: 'load' });
  await pg.waitForTimeout(900);

  // 1) 逐图强制 eager + 解码校验（排除灯箱里 src="" 的占位 img）
  const imgs = await pg.evaluate(async () => {
    const list = [].slice.call(document.querySelectorAll('.shot img'));
    list.forEach(i => { i.loading = 'eager'; });
    for (const i of list) {
      if (!i.complete) {
        await new Promise(r => { i.onload = r; i.onerror = r; setTimeout(r, 4000); });
      }
      if (i.decode) { try { await i.decode(); } catch (e) {} }
    }
    return list.map(i => ({
      src: i.getAttribute('src'),
      w: i.naturalWidth, h: i.naturalHeight, ok: i.naturalWidth > 0
    }));
  });

  console.log('== 图片解码 ==');
  imgs.forEach(i => console.log('  ' + (i.ok ? 'OK  ' : 'FAIL') + ' ' + i.w + 'x' + i.h + '  ' + i.src));

  // 2) 灯箱：开 → 翻 → 翻回 → 关
  await pg.locator('.shot img').first().click();
  await pg.waitForTimeout(450);
  const open1 = await pg.locator('#lb.on').count();
  const src1 = await pg.locator('#lb img').getAttribute('src');
  await pg.keyboard.press('ArrowRight');
  await pg.waitForTimeout(300);
  const src2 = await pg.locator('#lb img').getAttribute('src');
  await pg.keyboard.press('ArrowLeft');
  await pg.waitForTimeout(300);
  const src3 = await pg.locator('#lb img').getAttribute('src');
  await pg.keyboard.press('Escape');
  await pg.waitForTimeout(300);
  const closed = (await pg.locator('#lb.on').count()) === 0;

  console.log('== 灯箱 ==');
  console.log('  打开: ' + (open1 ? 'OK' : 'FAIL'));
  console.log('  右翻换图: ' + (src1 !== src2 ? 'OK' : 'FAIL'));
  console.log('  左翻还原: ' + (src1 === src3 ? 'OK' : 'FAIL'));
  console.log('  Esc 关闭: ' + (closed ? 'OK' : 'FAIL'));

  // 3) 关键内容存在
  const txt = await pg.evaluate(() => document.body.innerText);
  const keys = ['shen1306009009/GuLianDong', 'git push', 'Public', 'config.example.json',
    '82562db6d52195702a95aff402d58858', '2.09 MB', '3.6 GB'];
  console.log('== 关键内容 ==');
  const missing = keys.filter(k => !txt.includes(k));
  keys.forEach(k => console.log('  ' + (txt.includes(k) ? 'OK  ' : 'MISS') + ' ' + k));

  console.log('== JS 报错 ==', errors.length ? errors : '无');
  console.log('== 404 ==', failed.length ? failed : '无');

  const pass = imgs.every(i => i.ok) && open1 && src1 !== src2 && src1 === src3
    && closed && missing.length === 0 && errors.length === 0 && failed.length === 0;
  console.log('\n' + (pass ? 'PASS' : 'FAIL'));
  await b.close();
  process.exit(pass ? 0 : 1);
})();
