#!/usr/bin/env node
/* 股联动 GuLianDong 图标生成器 —— WinRAR 立体书堆风格
 *
 * 视觉语言：三层立体“书堆” + 米色书口页纹 + 饱和渐变封面 + 红色标签带 + 接地阴影。
 * 内容：前册封面 = 红色标签带（内含联动链环）+ 上升 K 线三根；中/后册露出各自标签带。
 *
 * 用法：
 *   NODE_PATH=C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules \
 *   node tools/make_icon_wr.cjs
 *
 * 产物（build/icon_wr/）：icon_wr.svg（母版）+ 各尺寸 PNG
 */
const fs = require('fs');
const nodePath = require('path');
const { Resvg } = require('@resvg/resvg-js');

const ROOT = nodePath.join(__dirname, '..');
const OUT = nodePath.join(ROOT, 'build', 'icon_wr');
fs.mkdirSync(OUT, { recursive: true });

// ─────────────────────────────────────────────── 设计参数（256 设计空间）
const V = 256;                  // viewBox
const W = 110, H = 72, R = 8;   // 封面：宽 / 高 / 圆角（书本比例 ≈1.5:1）
const EX = 16, EY = -12;        // 立体挤出向量（右上 = 书口厚度，压薄一点）
const BAND_Y = 4, BAND_H = 12;  // 封面标签带
const ROT = -6;                 // 整叠俯视角

// 前 → 后 三层（每层向右上错位，重叠要紧，避免读成“楼梯”）
const LEVELS = [
  { x: 40, y: 148 },
  { x: 56, y: 115 },
  { x: 72, y: 82 },
];
// 封面渐变：高光 → 主色 → 暗部
const COVER = [
  ['#A6CBFF', '#2E6BE6', '#0C2E7C'],  // 0 前册：品牌蓝（最亮，主角）
  ['#AE9BF8', '#5A45C8', '#261A5E'],  // 1 中册：紫罗兰
  ['#6D80C8', '#26356E', '#111838'],  // 2 后册：深靛
];
// 标签带：三册同色系，前册最亮（WinRAR 的“成套”感）
const BAND = [
  ['#FF6A50', '#DC3018', '#93150A'],
  ['#F4563C', '#C42712', '#80120A'],
  ['#DE4830', '#A81E0C', '#66100A'],
];
// 涨红 / 跌绿
const UP = '#FF4433', UP_D = '#B81A0E';
const DOWN = '#22B77C', DOWN_D = '#0B7A50';
// 页纸：冷白纸色（避免读成纸箱）
const PAGE_TOP = ['#FFFFFF', '#EDF0F5', '#D4DAE4'];
const PAGE_LINE = '#98A2B2';

// ─────────────────────────────────────────────── 小工具
const n = (v) => Math.round(v * 100) / 100;
const pts = (arr) => arr.map((p) => `${n(p[0])},${n(p[1])}`).join(' ');
const poly = (arr, fill, extra = '') => `<polygon points="${pts(arr)}" fill="${fill}" ${extra}/>`;
const rect = (x, y, w, h, fill, extra = '') =>
  `<rect x="${n(x)}" y="${n(y)}" width="${n(w)}" height="${n(h)}" fill="${fill}" ${extra}/>`;
const line = (a, b, c, w, extra = '') =>
  `<line x1="${n(a[0])}" y1="${n(a[1])}" x2="${n(b[0])}" y2="${n(b[1])}" stroke="${c}" stroke-width="${n(w)}" ${extra}/>`;
const lin = (id, stops, x1 = 0, y1 = 0, x2 = 0, y2 = 1) =>
  `<linearGradient id="${id}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}">` +
  stops.map(([o, c]) => `<stop offset="${o}" stop-color="${c}"/>`).join('') + '</linearGradient>';

// ─────────────────────────────────────────────── 一颗“书”
// tier: 0 = 全细节；1 = 去接地阴影；2 = 去链环/页纹；3 = 只剩体块（≤24px）
function slab(i, tier) {
  const L = LEVELS[i], c = COVER[i], bd = BAND[i];
  const x = L.x, y = L.y;
  const A = [x, y], B = [x + EX, y + EY], C = [x + W + EX, y + EY], D = [x + W, y];
  const D2 = [x + W, y + H], C2 = [x + W + EX, y + EY + H];
  const g = [];

  // ① 书口：右侧面（页纸侧边）
  g.push(poly([D, C, C2, D2], `url(#pgr${i})`));
  // ② 书口：顶面（页纸上边）
  g.push(poly([A, B, C, D], `url(#pgt${i})`));
  if (tier <= 1) {
    // 页纹：沿挤出方向
    g.push(line([x + 0.5 * EX, y + 0.5 * EY], [x + 0.5 * EX + W, y + 0.5 * EY], PAGE_LINE, 1.3, 'opacity="0.45"'));
    for (const v of [0.34, 0.66]) {
      g.push(line([x + W, y + v * H], [x + W + EX, y + EY + v * H], PAGE_LINE, 1.2, 'opacity="0.4"'));
    }
  }

  // ③ 封面
  g.push(rect(x, y, W, H, `url(#cov${i})`, `rx="${R}"`));
  g.push(rect(x, y, W, H, `url(#gls${i})`, `rx="${R}"`));      // 玻璃高光 + 底部压暗
  // 封面顶边：一道暗缝 + 一道白高光，做出“封面压在书页上”的厚度
  g.push(`<path d="M${n(x + R * 0.5)} ${n(y + 0.7)} H${n(x + W - R * 0.5)}" stroke="#0A1436" stroke-opacity="0.34" stroke-width="1.5" stroke-linecap="round"/>`);
  g.push(`<path d="M${n(x + R * 0.8)} ${n(y + 2.6)} H${n(x + W - R * 0.8)}" stroke="#FFFFFF" stroke-opacity="0.4" stroke-width="1.5" stroke-linecap="round"/>`);

  // ④ 标签带
  g.push(rect(x, y + BAND_Y, W, BAND_H, `url(#band${i})`, `clip-path="url(#clip${i})"`));
  g.push(`<path d="M${n(x + R * 0.6)} ${n(y + BAND_Y + 1)} H${n(x + W - R * 0.6)}" stroke="#FFFFFF" stroke-opacity="0.3" stroke-width="1.3" stroke-linecap="round"/>`);
  g.push(`<path d="M${n(x + R * 0.6)} ${n(y + BAND_Y + BAND_H - 0.7)} H${n(x + W - R * 0.6)}" stroke="#5E0C03" stroke-opacity="0.45" stroke-width="1.3" stroke-linecap="round"/>`);

  // ⑤ 标签带里的联动链环（对角排布，避免读成两只眼睛）
  if (tier <= 1) {
    const cx = x + W / 2, cy = y + BAND_Y + BAND_H / 2 + 0.2, r = 3.2, o = 2.4, sw = 1.8;
    g.push(
      `<g transform="rotate(-18 ${n(cx)} ${n(cy)})" fill="none" stroke="#FFFFFF" stroke-width="${sw}" stroke-opacity="0.92">` +
      `<circle cx="${n(cx - o)}" cy="${n(cy - o * 0.72)}" r="${r}"/>` +
      `<circle cx="${n(cx + o)}" cy="${n(cy + o * 0.72)}" r="${r}"/>` +
      `</g>`
    );
  }

  // ⑥ 前册封面：上升 K 线（左低右高）
  if (i === 0 && tier <= 2) {
    const bw = 12;
    const cs = [
      { cx: x + 35, up: false, body: [y + 48, y + 64], wick: [y + 44, y + 68] },
      { cx: x + 55, up: true, body: [y + 39, y + 55], wick: [y + 35, y + 59] },
      { cx: x + 75, up: true, body: [y + 28, y + 44], wick: [y + 24, y + 48] },
    ];
    for (const k of cs) {
      const col = k.up ? UP : DOWN, dk = k.up ? UP_D : DOWN_D;
      g.push(line([k.cx, k.wick[0]], [k.cx, k.body[0]], col, tier <= 1 ? 2.2 : 3));
      g.push(line([k.cx, k.body[1]], [k.cx, k.wick[1]], col, tier <= 1 ? 2.2 : 3));
      g.push(rect(k.cx - bw / 2, k.body[0], bw, k.body[1] - k.body[0], col, 'rx="1.6"'));
      if (tier <= 1) {
        // 实体左侧内高光 + 底部暗边，做出圆柱感
        g.push(`<path d="M${n(k.cx - bw / 2 + 1.5)} ${n(k.body[0] + 2)} V${n(k.body[1] - 2)}" stroke="#FFFFFF" stroke-opacity="0.4" stroke-width="1.5" stroke-linecap="round"/>`);
        g.push(rect(k.cx - bw / 2, k.body[1] - 2, bw, 2, dk, 'rx="1" opacity="0.7"'));
      }
    }
  }

  return g.join('');
}

// 接地阴影：上层书堆压在下层书口上的暗带
// 遮挡线 = 下层封面的书口上沿；跨度 = 该线处书口的 x 范围
const contact = (yLine, x0, x1) => rect(x0, yLine - 9, x1 - x0, 9.5, 'url(#cs)');
const contactOf = (i) => contact(LEVELS[i].y + EY, LEVELS[i].x + EX, LEVELS[i].x + W + EX);

// ─────────────────────────────────────────────── 构图
function content(tier) {
  const b = [];
  b.push(tier === 0 ? `<ellipse cx="121" cy="224" rx="92" ry="12" fill="url(#amb)"/>` : '');
  b.push(slab(2, tier));
  if (tier <= 1) b.push(contactOf(1));   // 中册压后册
  b.push(slab(1, tier));
  if (tier <= 1) b.push(contactOf(0));   // 前册压中册
  b.push(slab(0, tier));
  return b.filter(Boolean).join('\n');
}

function defs() {
  const d = [];
  LEVELS.forEach((L, i) => {
    d.push(lin(`cov${i}`, [[0, COVER[i][0]], [0.12, COVER[i][0]], [0.48, COVER[i][1]], [1, COVER[i][2]]]));
    d.push(lin(`band${i}`, [[0, BAND[i][0]], [0.36, BAND[i][1]], [1, BAND[i][2]]]));
    // 顶面：沿挤出方向（左下亮 → 右上暗）
    d.push(lin(`pgt${i}`, [[0, PAGE_TOP[0]], [0.55, PAGE_TOP[1]], [1, PAGE_TOP[2]]], 0, 1, 1, 0));
    // 右侧面：上亮下暗
    d.push(lin(`pgr${i}`, [[0, PAGE_TOP[1]], [0.42, PAGE_TOP[2]], [1, '#BEC5D1']]));
    d.push(
      `<linearGradient id="gls${i}" x1="0" y1="0" x2="0" y2="1">` +
      `<stop offset="0" stop-color="#FFFFFF" stop-opacity="0.34"/>` +
      `<stop offset="0.24" stop-color="#FFFFFF" stop-opacity="0.10"/>` +
      `<stop offset="0.52" stop-color="#FFFFFF" stop-opacity="0"/>` +
      `<stop offset="0.84" stop-color="#000000" stop-opacity="0.05"/>` +
      `<stop offset="1" stop-color="#000000" stop-opacity="0.20"/>` +
      `</linearGradient>`
    );
    d.push(`<clipPath id="clip${i}"><rect x="${L.x}" y="${L.y}" width="${W}" height="${H}" rx="${R}"/></clipPath>`);
  });
  d.push(lin('cs', [['0', 'rgba(8,14,36,0)'], ['1', 'rgba(8,14,36,0.5)']]));
  d.push(
    `<radialGradient id="amb" cx="0.5" cy="0.5" r="0.5">` +
    `<stop offset="0" stop-color="#0A1028" stop-opacity="0.26"/>` +
    `<stop offset="0.55" stop-color="#0A1028" stop-opacity="0.12"/>` +
    `<stop offset="1" stop-color="#0A1028" stop-opacity="0"/></radialGradient>`
  );
  return d.join('');
}

function wrap(inner, transform) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${V}" height="${V}" viewBox="0 0 ${V} ${V}">\n` +
    `<defs>${defs()}</defs>\n${transform ? `<g transform="${transform}">` : '<g>'}\n${inner}\n</g>\n</svg>`;
}

// ─────────────────────────────────────────────── 自动量测 + 居中适配
function renderPixels(svg, size) {
  const rv = new Resvg(svg, { fitTo: { mode: 'width', value: size } });
  const r = rv.render();
  return { px: r.pixels, w: r.width, h: r.height };
}
function renderPng(svg, size) {
  return new Resvg(svg, { fitTo: { mode: 'width', value: size } }).render().asPng();
}
// 量 α>阈值 的包围盒（阈值用于忽略柔和投影），返回设计空间坐标
function measure(tier, alphaMin = 40) {
  const size = V;
  const { px, w, h } = renderPixels(wrap(content(tier), ''), size);
  let x0 = w, y0 = h, x1 = -1, y1 = -1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (px[(y * w + x) * 4 + 3] > alphaMin) {
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
    }
  }
  return { x0, y0, x1, y1, bw: x1 - x0, bh: y1 - y0 };
}

// 旋转后的包围盒（解析计算，绕 (cx,cy)）
function rotBox(box, deg) {
  const th = (deg * Math.PI) / 180, c = Math.cos(th), s = Math.sin(th);
  const cx = (box.x0 + box.x1) / 2, cy = (box.y0 + box.y1) / 2;
  let w = 0, h = 0;
  for (const p of [[box.x0, box.y0], [box.x1, box.y0], [box.x1, box.y1], [box.x0, box.y1]]) {
    const dx = p[0] - cx, dy = p[1] - cy;
    const rx = dx * c - dy * s, ry = dx * s + dy * c;
    w = Math.max(w, Math.abs(rx)); h = Math.max(h, Math.abs(ry));
  }
  return { cx, cy, bw: w * 2, bh: h * 2 };
}

function fitFor(tier, marginRatio) {
  // tier 0 带环境投影，量测时把柔影算进去（阈值压低），避免被画布切边
  const box = measure(tier, tier === 0 ? 24 : 150);
  const rb = rotBox(box, ROT);
  const margin = V * marginRatio;
  const k = (V - 2 * margin) / Math.max(rb.bw, rb.bh);
  return `translate(${V / 2} ${V / 2}) scale(${n(k)}) rotate(${ROT} ${n(rb.cx)} ${n(rb.cy)}) translate(${n(-rb.cx)} ${n(-rb.cy)})`;
}

// ─────────────────────────────────────────────── 尺寸档位
function tierFor(size) {
  if (size >= 64) return 0;
  if (size >= 32) return 2;
  return 3;
}
const marginFor = (size) => (size >= 128 ? 0.075 : size >= 48 ? 0.05 : size >= 32 ? 0.038 : 0.016);

function main() {
  const svgFor = (size) => wrap(content(tierFor(size)), fitFor(tierFor(size), marginFor(size)));

  // 母版：全细节 + 大留白（可直接用于网页/设计稿，等比缩放）
  fs.writeFileSync(nodePath.join(OUT, 'icon_wr.svg'), svgFor(256), 'utf8');
  console.log('SVG  ->', nodePath.join(OUT, 'icon_wr.svg'));

  const b = measure(0, 150);
  console.log(`未变换实体包围盒: x ${b.x0}..${b.x1}  y ${b.y0}..${b.y1}  (${b.bw}×${b.bh})`);

  for (const size of [512, 256, 128, 64, 48, 32, 24, 16]) {
    fs.writeFileSync(nodePath.join(OUT, `icon_wr_${size}.png`), renderPng(svgFor(size), size));
    console.log(`PNG  -> icon_wr_${size}.png  (tier ${tierFor(size)})`);
  }
}

if (require.main === module) main();
module.exports = { wrap, content, fitFor, renderPixels, OUT };
