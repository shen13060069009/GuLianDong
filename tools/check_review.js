/* 验收页自检 v2（2026-10-08 免费开源版）：图片全部解码 / 无 JS 报错 / 无 404 /
   灯箱可开可翻可关 / 关键文案区块齐全 */
const path = require("path");
const { chromium } = require("playwright");

(async () => {
  const page_path = "file:///" + path.resolve(__dirname, "..", "review", "index.html").replace(/\\/g, "/");
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });

  const errors = [], bad = [];
  page.on("pageerror", e => errors.push("JS: " + e.message));
  page.on("console", m => { if (m.type() === "error") errors.push("console: " + m.text()); });
  page.on("response", r => { if (r.status() >= 400) bad.push(r.status() + " " + r.url()); });
  page.on("requestfailed", r => bad.push("FAILED " + r.url()));

  await page.goto(page_path, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    document.querySelectorAll('img[loading="lazy"]').forEach(i => i.loading = "eager");
  });
  const total = await page.evaluate(() => document.body.scrollHeight);
  for (let y = 0; y < total; y += 600) {
    await page.evaluate(v => window.scrollTo(0, v), y);
    await page.waitForTimeout(140);
  }

  const imgs = await page.evaluate(() => Array.from(document.images)
    .filter(i => i.getAttribute("src"))
    .map(i => ({
      src: i.src.split("/").slice(-2).join("/"), ok: i.complete && i.naturalWidth > 0 })));
  const broken = imgs.filter(i => !i.ok);

  // 必备区块
  const sec = await page.evaluate(() => ({
    decision: !!document.querySelector("#decision table"),
    color: document.querySelectorAll("#color .sw div").length,
    siteShots: document.querySelectorAll("#site .shot").length,
    clientShots: document.querySelectorAll("#client .shot").length,
    flow: document.querySelectorAll("#flow .st").length,
    kv: document.querySelectorAll("#verify .kv .m").length,
    todo: document.querySelectorAll("#todo .todo li").length,
  }));

  // 灯箱：打开 → 右翻 → 左翻回原图 → Esc 关
  await page.evaluate(() => document.querySelectorAll("#site .shot img")[0].parentElement.click());
  await page.waitForTimeout(400);
  const lbOn = await page.evaluate(() => document.getElementById("lb").classList.contains("on"));
  const s1 = await page.evaluate(() => document.getElementById("lbi").src);
  await page.click("#lbr"); await page.waitForTimeout(320);
  const s2 = await page.evaluate(() => document.getElementById("lbi").src);
  const lbl = await page.evaluate(() => document.getElementById("lbt").textContent);
  await page.click("#lbl"); await page.waitForTimeout(320);
  const s3 = await page.evaluate(() => document.getElementById("lbi").src);
  await page.keyboard.press("Escape"); await page.waitForTimeout(320);
  const lbOff = await page.evaluate(() => !document.getElementById("lb").classList.contains("on"));

  // 旧付费入口不得作为「可点入口 / 卡片标题 / 说明正文」残留。
// 排除：决策表 .del（刻意对照的「改动前」）、.why（验证表里描述 grep 结果的说明）。
  const stale = await page.evaluate(() => {
    const nodes = document.querySelectorAll('a.btn, .badges b, .card h4, .hd h1, .hd p, .stitle h2');
    const hits = [];
    nodes.forEach(n => {
      const t = (n.innerText || '').replace(/\s+/g, ' ');
      ["¥98", "立即购买", "微信扫码付款", "订单查码"].forEach(k => {
        if (t.includes(k)) hits.push(k + " @ " + t.slice(0, 60));
      });
    });
    // 决策表「改动后」列（第 3 列）不得出现付费词
    const after = Array.from(document.querySelectorAll('#decision tbody tr td:nth-child(3)'))
      .map(td => td.innerText.replace(/\s+/g, ' '));
    ["¥98", "立即购买", "买断", "订单查码"].forEach(k => {
      after.forEach(t => { if (t.includes(k)) hits.push('改动后列含 ' + k + ': ' + t); });
    });
    return hits;
  });

  console.log(JSON.stringify({
    images: { total: imgs.length, broken },
    sections: sec, stalePaidCopy: stale,
    lightbox: { open: lbOn, nextChanged: s1 !== s2, label: lbl,
                 prevRestored: s1 === s3, closedByEsc: lbOff },
    jsErrors: errors, http4xx: bad,
  }, null, 2));

  const pass = !broken.length && !errors.length && !bad.length && !stale.length &&
    sec.decision && sec.color >= 7 && sec.siteShots >= 6 && sec.clientShots >= 2 &&
    sec.flow >= 5 && sec.kv >= 4 && sec.todo >= 3 &&
    lbOn && s1 !== s2 && s1 === s3 && lbOff;
  console.log(pass ? "REVIEW-PAGE PASS" : "REVIEW-PAGE FAIL");
  await browser.close();
  process.exit(pass ? 0 : 1);
})();