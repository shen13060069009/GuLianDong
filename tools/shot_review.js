// 验收页视觉抽查：整页长图 + 各区块定点截图
const path = require("path");
const { chromium } = require("playwright");

(async () => {
  const page_path = "file:///" + path.resolve(__dirname, "..", "review", "index.html").replace(/\\/g, "/");
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto(page_path, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => document.querySelectorAll('img[loading="lazy"]').forEach(i => i.loading = "eager"));
  await page.waitForTimeout(1600);
  await page.screenshot({ path: "review/_shot_full.png", fullPage: true });
  for (const id of ["decision", "color", "site", "client", "verify", "todo"]) {
    const el = await page.$("#" + id);
    if (el) await el.screenshot({ path: "review/_shot_" + id + ".png" });
  }
  console.log("shots written");
  await browser.close();
})();