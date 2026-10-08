# -*- coding: utf-8 -*-
"""P23 通用词型股票名全表扫描

思路：股票简称如果本身就是日常中文里的常用词，就会在聊天里被大量误报。
用 jieba 的词频词典（349,046 词条 + 词频 + 词性）做主判据，
再叠加几个可验证的形态特征，输出候选表供人工确认。

用法:
  python probe/p23_weak_scan.py             # 打印全部候选
  python probe/p23_weak_scan.py 300         # 只看前 300 条
  python probe/p23_weak_scan.py --json      # 输出 JSON
  python probe/p23_weak_scan.py --write     # 按规则重写 data/weak_names.json

为什么需要「保留名单」：
  词频高 ≠ 会误报。像 五粮液 / 中关村 / 陆家嘴 / 白云机场 这类词，
  jieba 词频也不低（163 / 153 / 38 / 34），但它们在聊天里出现时
  绝大多数就是在聊这只股票（品牌名、地名商圈就是公司本体）。
  真正会满屏误报的是「普通名词/动词短语」型：老百姓、机器人、指南针、
  向日葵、农产品、太阳能 …… 这类词有通用语义，和公司没有指向关系。
  所以规则 = 词频/词性筛出候选，再减掉品牌地名保留名单。
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import jieba
import jieba.posseg as pseg

DATA = os.path.join(ROOT, "data")

# ---------- 读 jieba 词典（词 -> (词频, 词性)）----------
def load_jieba_dict():
    path = os.path.join(os.path.dirname(jieba.__file__), "dict.txt")
    d = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                freq = int(parts[1])
            except ValueError:
                continue
            d[parts[0]] = (freq, parts[2] if len(parts) > 2 else "")
    return d


# 明确的"公司/机构"后缀词：带这些后缀的名字即使常用也是真公司
ORG_SUFFIX = (
    "股份", "集团", "控股", "科技", "电子", "电力", "能源", "医药", "生物",
    "重工", "钢铁", "银行", "证券", "保险", "地产", "置业", "汽车", "机械",
    "化工", "material", "传媒", "环保", "通信", "网络", "传媒",
)
# 地名类前缀（省级/主要城市）：地名 + 行业 是典型股票命名法，不算日常词
PLACES = set("""
北京 上海 广州 深圳 天津 重庆 河北 山西 辽宁 吉林 黑龙江 江苏 浙江 安徽 福建
江西 山东 河南 湖北 湖南 广东 广西 海南 四川 贵州 云南 陕西 甘肃 青海 台湾
内蒙古 宁夏 新疆 西藏 香港 澳门
南京 杭州 苏州 无锡 宁波 温州 合肥 福州 厦门 南昌 济南 青岛 郑州 长沙 武汉
成都 昆明 西安 兰州 太原 石家庄 大连 沈阳 长春 哈尔滨 乌鲁木齐 呼和浩特
""".split())

# ---------------------------------------------------------------- 保留名单
# 品牌 / 地标 / 机构型简称：词频高，但聊天里出现时基本就是在说这只股票
# （品牌名、地名商圈本身就是公司本体，不构成"通用语义"）。
# 每条都要能说出理由，不然以后没法维护。
KEEP_AS_NORMAL = {
    # 消费品牌
    "五粮液", "同仁堂", "全聚德", "雅戈尔", "蒙娜丽莎", "索菲亚", "双汇发展",
    "中青旅", "天士力", "鄂尔多斯", "白云山", "中联重科", "中兴通讯",
    # 地标 / 地名 / 商圈 / 景区
    "中关村", "陆家嘴", "王府井", "徐家汇", "外高桥", "张家界", "长白山",
    "白云机场", "连云港", "北大荒", "珍宝岛", "会稽山",
    # 公司名就是产品名的（无通用语义）
    "福斯特", "康普顿", "伊戈尔", "中钨高新", "首都在线", "拉普拉斯",
    "寒武纪", "红太阳",
}

# 已在现有表里、但明确要保留成正常词的（防止老表把它们带成弱词）
NOT_WEAK = set()

# 词性白名单：这些词性说明它是个"通用实词"，才可能是日常语义
WEAK_POS = {"n", "nr", "ns", "nt", "nz", "nrt", "v", "vn", "a", "l", "i", "j"}


def main():
    limit = None
    as_json = "--json" in sys.argv
    for a in sys.argv[1:]:
        if a.isdigit():
            limit = int(a)

    stocks = json.load(open(os.path.join(DATA, "stocks.json"), encoding="utf-8"))["items"]
    jd = load_jieba_dict()

    # 用独立分词器看名字能否被切成多个常用词
    seg = jieba.Tokenizer()

    rows = []
    for s in stocks:
        name = s["name"]
        if len(name) < 2:
            continue
        hit = jd.get(name)
        toks = [t for t in seg.lcut(name) if t.strip()]
        # 形态特征
        covered = sum(len(t) for t in toks) == len(name) and len(toks) > 1
        tok_freqs = [jd[t][0] for t in toks if t in jd]
        min_tok_freq = min(tok_freqs) if len(tok_freqs) == len(toks) and toks else 0
        has_place = any(name.startswith(p) for p in PLACES)
        has_org = any(name.endswith(x) for x in ORG_SUFFIX)

        score = 0
        reasons = []
        if hit:
            f, pos = hit
            score += 2
            reasons.append("词典直收(f=%d,%s)" % (f, pos) if pos else "词典直收(f=%d)" % f)
            if f >= 500:
                score += 3
                reasons.append("高频%d" % f)
            elif f >= 100:
                score += 2
                reasons.append("中频%d" % f)
            elif f >= 20:
                score += 1
        if covered and len(toks) >= 2 and min_tok_freq >= 100:
            score += 2
            reasons.append("可切成%d个常用词%s" % (len(toks), "/".join(toks[:4])))
        if has_place:
            score -= 3
            reasons.append("地名前缀")
        if has_org:
            score -= 2
            reasons.append("公司后缀")

        if score >= 3:
            rows.append({
                "code": s["code"], "name": name, "score": score,
                "jieba": hit[0] if hit else 0, "pos": hit[1] if hit else "",
                "segs": toks, "reasons": reasons,
                "keep": name in KEEP_AS_NORMAL or name in NOT_WEAK,
            })

    rows.sort(key=lambda r: (-r["score"], -r["jieba"], r["name"]))

    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    wk_path = os.path.join(DATA, "weak_names.json")
    cur_wk = set(json.load(open(wk_path, encoding="utf-8")).get("weak_names", []))
    auto = [r["name"] for r in rows if not r["keep"]]
    # 并集：自动扫描 ∪ 手工积累（去掉被改判为保留的），手工条目永不丢
    final = sorted(set(auto) | (cur_wk - KEEP_AS_NORMAL - NOT_WEAK))

    if "--write" in sys.argv:
        payload = {
            "_说明": ("通用词型股票名弱词表（自动扫描 + 手工积累）。这些简称本身是合法股票名，"
                      "但在日常聊天里更常表达普通含义（如'机器人'多指聊天机器人、"
                      "'老百姓'就是普通名词），会造成大量误报。"
                      "默认 weak_mode=context：单独出现不报，同段文本里有强命中或行情语义词时才报。"
                      "想让它恢复全量匹配，把它从列表删掉即可。"
                      "由 probe/p23_weak_scan.py 重新生成。"),
            "_统计": {"自动扫描候选": len(auto), "手工保留": len(cur_wk - KEEP_AS_NORMAL - NOT_WEAK),
                      "合计": len(final)},
            "_保留名单": sorted(KEEP_AS_NORMAL),
            "weak_names": final,
        }
        with open(wk_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print("已重写 %s  共 %d 条（自动 %d + 手工 %d）"
              % (wk_path, len(final), len(auto), len(cur_wk - KEEP_AS_NORMAL - NOT_WEAK)))

    print("股票总数 %d  扫描候选 %d（其中保留为正常词 %d）  现有表 %d 条\n"
          % (len(stocks), len(rows), len(rows) - len(auto), len(cur_wk)))
    print("%-4s %-9s %-8s %-6s %-6s %-5s %s"
          % ("#", "代码", "简称", "分", "jieba", "处置", "判据"))
    print("-" * 106)
    for i, r in enumerate(rows[:limit] if limit else rows, 1):
        print("%-4d %-9s %-8s %-6d %-6d %-5s %s"
              % (i, r["code"], r["name"], r["score"], r["jieba"],
                 "保留" if r["keep"] else "弱词", "；".join(r["reasons"])))

    print("\n弱词表最终 %d 条：" % len(final))
    print("  " + "、".join(final))
    json.dump(rows, open(os.path.join(ROOT, "out", "weak_candidates.json"),
                         "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n完整候选已存 → out/weak_candidates.json")


if __name__ == "__main__":
    main()
