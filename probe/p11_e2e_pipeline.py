# -*- coding: utf-8 -*-
"""P0-11 端到端链路验证

渲染一张模拟聊天窗口截图（内容已知），跑完整链路：

    合成图 → OCR → 匹配 → 高亮框坐标

然后与 ground truth 对照，算准确率 / 召回率 / 误报数。
这是「识别 → 匹配」这条主链路的唯一可信验证方式。

同时验证几个必须成立的负样本：
  * 日期 20240519 不能命中
  * 长数字串中夹带的代码不能命中（代码边界校验）
  * 通用词不能满屏误命中
"""
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw, ImageFont

from src.matcher import build_default
from src.ocr import StockOCR

OUT_DIR = os.path.join(ROOT, "out")
os.makedirs(OUT_DIR, exist_ok=True)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]

# (发送者, 消息正文)  —— 模拟真实聊天口吻
MESSAGES = [
    ("老徐", "中芯国际今天放量了，我准备加点仓位"),
    ("阿哲", "宁德时代还在回调，贵州茅台倒是缩量到位了"),
    ("我", "600519 这个位置我盯了很久"),
    ("小李", "我买了 gzmt 和 zxgj，先放着看"),
    ("老徐", "光模块今天全线爆发，你们关注了吗"),
    ("阿哲", "中国平安和平安银行我选前者"),
    ("我", "别追高，等回踩"),
    ("小李", "寒武纪这个走势有点吓人"),
    ("老徐", "我看了下 20240519 那天的分时图"),
    ("阿哲", "资金流水号 1236005194 这个我查过了"),
]

# ground truth：应当被命中的字面
EXPECT_HIT = [
    "中芯国际", "宁德时代", "贵州茅台", "600519", "gzmt", "zxgj",
    "光模块", "中国平安", "平安银行", "寒武纪",
]
# ground truth：绝对不该被命中的
EXPECT_MISS = ["20240519", "1236005194", "今天", "回调", "仓位", "位置"]


def find_font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_chat(path):
    W, H = 760, 820
    img = Image.new("RGB", (W, H), (247, 248, 250))
    d = ImageDraw.Draw(img)

    f_title = find_font(19)
    f_msg = find_font(20)
    f_small = find_font(15)

    # 顶部标题栏
    d.rectangle([0, 0, W, 52], fill=(255, 255, 255))
    d.line([0, 52, W, 52], fill=(225, 228, 232))
    d.text((20, 15), "投研交流群", font=f_title, fill=(32, 36, 40))
    d.text((W - 130, 18), "11:58", font=f_small, fill=(150, 156, 164))

    y = 78
    for sender, msg in MESSAGES:
        d.text((24, y), sender, font=f_small, fill=(140, 146, 154))
        y += 23
        # 换行：每行约 28 个字符
        line = ""
        for ch in msg:
            line += ch
            if len(line) >= 28:
                d.text((24, y), line, font=f_msg, fill=(28, 32, 36))
                y += 30
                line = ""
        if line:
            d.text((24, y), line, font=f_msg, fill=(28, 32, 36))
            y += 30
        y += 14
        if y > H - 40:
            break

    img.save(path)
    return path


def main():
    img_path = os.path.join(OUT_DIR, "synthetic_chat.png")
    render_chat(img_path)
    print("=" * 100)
    print("P0-11 端到端链路验证")
    print("=" * 100)
    print("  合成图: %s" % img_path)

    t0 = time.perf_counter()
    matcher = build_default(verbose=True)
    t_build = (time.perf_counter() - t0) * 1000
    print("  词典构建耗时: %.0f ms" % t_build)

    ocr = StockOCR(verbose=True)
    ocr.recognize(img_path)

    t0 = time.perf_counter()
    boxes = ocr.recognize(img_path)
    t_ocr = (time.perf_counter() - t0) * 1000
    print("  OCR 耗时: %.0f ms   文本块 %d" % (t_ocr, len(boxes)))

    t0 = time.perf_counter()
    all_matches = []
    for b in boxes:
        for m in matcher.match(b.text):
            # 把文本内偏移换算成图像坐标（按字符宽度等比近似）
            n = max(len(b.text), 1)
            sx = b.x0 + int(b.w * m.start / n)
            ex = b.x0 + int(b.w * m.end / n)
            all_matches.append((b, m, (sx, b.y0, ex, b.y1)))
    t_match = (time.perf_counter() - t0) * 1000
    print("  匹配耗时: %.2f ms（%d 个文本块）" % (t_match, len(boxes)))

    # ---------------- 结果 ----------------
    print()
    print("=" * 100)
    print("匹配结果")
    print("=" * 100)
    print("  %-34s %-8s %-10s %-16s %s" % ("识别文本", "命中", "层级", "标注", "高亮框"))
    print("  " + "-" * 94)
    for b, m, rect in all_matches:
        c = m.primary
        label = "%s %s" % (c.code, c.name) if c else "?"
        if m.ambiguous:
            label += " (+%d)" % (len(m.candidates) - 1)
        print("  %-34s %-8s %-10s %-16s %s"
              % (b.text[:33], m.surface, m.layer, label[:15], rect))

    # ---------------- 评价 ----------------
    found = set()
    for b, m, _ in all_matches:
        found.add(m.surface)

    hit_ok = [k for k in EXPECT_HIT if k in found]
    hit_miss = [k for k in EXPECT_HIT if k not in found]
    false_pos = [k for k in EXPECT_MISS if k in found]

    # ground truth 是否出现在 OCR 结果里（区分"匹配失败"与"OCR 没读出来"）
    ocr_all = " ".join(b.text for b in boxes)
    ocr_ok = [k for k in EXPECT_HIT if k in ocr_all]
    ocr_lost = [k for k in EXPECT_HIT if k not in ocr_all]

    print()
    print("=" * 100)
    print("评价")
    print("=" * 100)
    print("  应命中 %d 项，实际命中 %d 项" % (len(EXPECT_HIT), len(hit_ok)))
    print("    命中  : %s" % "、".join(hit_ok))
    if hit_miss:
        print("    漏掉  : %s" % "、".join(hit_miss))
    print("  不应命中 %d 项，误报 %d 项%s"
          % (len(EXPECT_MISS), len(false_pos),
             "  → " + "、".join(false_pos) if false_pos else "  ✓ 无误报"))
    print()
    print("  归因（区分链路环节）：")
    print("    OCR 已读出 : %d/%d  %s" % (len(ocr_ok), len(EXPECT_HIT), "、".join(ocr_ok)))
    if ocr_lost:
        print("    OCR 漏读   : %d  %s  ← 问题在 OCR 不在匹配"
              % (len(ocr_lost), "、".join(ocr_lost)))
    rec = len(hit_ok) / max(len(EXPECT_HIT), 1) * 100
    print()
    print("  端到端召回率: %.0f%%    误报率: %.0f%%"
          % (rec, len(false_pos) / max(len(EXPECT_MISS), 1) * 100))

    # 导出可视化
    try:
        vis = Image.open(img_path).convert("RGB")
        dv = ImageDraw.Draw(vis)
        for b, m, rect in all_matches:
            dv.rectangle(rect, outline=(226, 75, 74), width=2)
            dv.text((rect[0], max(rect[1] - 15, 0)), m.surface,
                    font=find_font(13), fill=(226, 75, 74))
        vp = os.path.join(OUT_DIR, "synthetic_highlighted.png")
        vis.save(vp)
        print("\n  高亮可视化: %s" % vp)
    except Exception as e:
        print("  可视化导出失败: %s" % e)


if __name__ == "__main__":
    main()
