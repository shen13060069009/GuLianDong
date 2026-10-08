# -*- coding: utf-8 -*-
"""股票匹配引擎

三层词典 + Aho-Corasick 多模式匹配。

    L1 主数据   代码 / 简称 / 拼音首字母          → 唯一确定，直接跳转
    L2 别名     用户自维护（中芯→中芯国际）        → 唯一确定，直接跳转
    L3 概念黑话 drmos / 光模块 / 固态电池          → 一对多，必须弹候选列表

为什么必须分三层：聊天里的黑话远不止股票简称。"drmos" 不是一个股票名，
它指向一串概念股；硬映射成单只股票一定出错。所以 L3 的点击行为与 L1/L2 不同。

拼写容错：OCR 对界面字体有系统性误识（实测把「飞书」稳定读成「作文」），
因此简称匹配允许一定的编辑距离容错，避免因 1 个字读错而漏识别。
"""
import json
import os
from dataclasses import dataclass, field

import ahocorasick

try:
    from .paths import data_dir
except ImportError:                     # 直接以脚本方式跑 src/matcher.py 时
    from paths import data_dir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = data_dir()

# 最小长度限制，防止短词造成满屏误匹配
MIN_NAME_LEN = 2
MIN_ABBR_LEN = 3

# 高频通用词黑名单：这些词经常出现在聊天里，且会与股票简称冲突
DEFAULT_BLACKLIST = {
    "中国", "国际", "东方", "南方", "北方", "西部", "东部", "科技", "股份",
    "集团", "控股", "发展", "实业", "投资", "资源", "能源", "电力", "环保",
    "医药", "医疗", "生物", "健康", "传媒", "文化", "教育", "旅游", "酒店",
    "地产", "置业", "建设", "工程", "交通", "汽车", "机械", "电子", "通信",
    "网络", "软件", "数据", "智能", "新材", "化工", "钢铁", "有色", "黄金",
    "银行", "证券", "保险", "信托", "基金", "期货", "今天", "明天", "昨天",
    "机会", "风险", "仓位", "加仓", "减仓", "清仓", "利好", "利空", "涨停",
    "跌停", "开盘", "收盘", "上涨", "下跌", "大盘", "个股", "板块", "龙头",
}

# 英文界面词 / 技术缩写黑名单（针对拼音首字母碰撞）
#
# 踩坑记录（P0-21 取证）：飞书窗口右上角的「搜素 (Ctl+ K)」提示文字里，
# "Ctl" 命中了楚天龙(003040) 的拼音首字母 ctl，成为一处稳定误报。
# 这类错误的共同点是：全是 3 字母、纯 ASCII 的界面/技术 token，
# 而用户手打拼音首字母几乎总是全小写（gzmt/ZXGJ），不会写成 Title Case。
# 所以用「大小写形态 + 英文词黑名单」两条一起挡。
ENGLISH_TOKEN_BLACKLIST = {
    "ctl", "alt", "tab", "esc", "del", "ins", "img", "doc", "pdf", "url",
    "api", "app", "web", "max", "min", "sum", "avg", "end", "top", "new",
    "old", "set", "get", "log", "sql", "cpu", "gpu", "ram", "ssd", "usb",
    "exe", "bin", "tmp", "var", "obj", "src", "lib", "run", "add", "cut",
    "all", "any", "key", "int", "str", "map", "ref", "row", "col", "num",
    "txt", "csv", "xml", "html", "css", "jsp", "php", "mac", "win", "ios",
    "pre", "post", "img", "vid", "aud", "net", "lan", "wan", "dns", "vpn",
    "ftp", "ssh", "ssl", "tcp", "udp", "ipv", "aaa", "bbb", "ccc",
}


# 行情语义词：弱词（老百姓/机器人/向日葵…）旁边出现这些词时，
# 说明整段文本是在聊股票，弱词大概率也是股票指代，可以保留。
# 只用 2 字以上的词，避免"涨"这种单字把天气/物价新闻也带进来。
MARKET_CONTEXT_WORDS = (
    # 涨跌
    "涨停", "跌停", "涨幅", "跌幅", "上涨", "下跌", "大涨", "大跌",
    "暴涨", "暴跌", "拉升", "跳水", "翻红", "翻绿", "高开", "低开",
    "一字板", "封板", "炸板", "连板", "天地板",
    # 操作
    "加仓", "减仓", "建仓", "清仓", "补仓", "持仓", "仓位", "重仓",
    "满仓", "空仓", "抄底", "逃顶", "买入", "卖出", "止盈", "止损",
    "套牢", "解套", "割肉", "打板", "低吸", "接力",
    # 盘面
    "股价", "收盘", "开盘", "盘中", "换手率", "放量", "缩量",
    "成交额", "成交量", "北向", "主力", "游资", "龙虎榜", "量能",
    # 题材
    "板块", "龙头", "题材", "概念股", "异动", "利好", "利空",
    "回调", "反弹", "反转", "轮动", "补涨", "炸板",
    # 基本面
    "市值", "估值", "市盈率", "研报", "目标价", "评级", "增持",
    "减持", "回购", "业绩", "财报", "定增",
    # 技术面
    "均线", "支撑位", "压力位", "缺口", "分时", "K线",
)


# 同音/形近错字归一表。硬性要求：替换必须【等长】，否则 start/end 偏移
# 与原文错位，框选/划词的高亮定位会整体画歪。
# 2026-10-07 用户实测：聊天里「股份」经常被打成「谷份/古份」，
# 全库 769 只简称带「股份」（沙河股份/河钢股份/紫光股份…），
# 不归一的话这 769 只在错字场景下全部漏报。
_TYPO_NORMALIZE = (
    ("谷份", "股份"),
    ("古份", "股份"),
)


@dataclass
class Candidate:
    code: str
    name: str

    def __repr__(self):
        return "%s %s" % (self.code, self.name)


@dataclass
class Match:
    """一处命中"""
    start: int
    end: int
    surface: str            # 原文中命中的字面
    key: str                # 词典里命中的键
    layer: str              # L1 / L2 / L3
    kind: str               # code / name / abbr / alias / concept
    candidates: list        # [Candidate, ...]，L3 通常多个
    weak: bool = False      # 是否为「弱词」（通用词型简称，单出现不可信）

    @property
    def ambiguous(self):
        return len(self.candidates) > 1

    @property
    def primary(self):
        return self.candidates[0] if self.candidates else None

    def __repr__(self):
        c = self.candidates[0] if self.candidates else None
        return "<%s %r@%d-%d %s%s%s>" % (
            self.kind, self.surface, self.start, self.end,
            ("%s %s" % (c.code, c.name)) if c else "?",
            " +%d" % (len(self.candidates) - 1) if self.ambiguous else "",
            " weak" if self.weak else "")


def _load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def load_aliases():
    """L2 别名表：{"中芯": {"code":"688981","name":"中芯国际"}, ...}"""
    return _load_json(os.path.join(DATA_DIR, "aliases.json"), {})


def load_concepts():
    """L3 概念表：{"drmos": ["688711","300046"], ...}"""
    return _load_json(os.path.join(DATA_DIR, "concepts.json"), {})


def load_weak_names():
    """弱词表：本身是合法股票简称，但日常聊天里更常表达普通含义。

    实测误报：飞书一屏内「机器人」(300024) 被命中 4 次，实际都指聊天机器人。
    这类词单独出现时不可信，除非同段文本里还有别的确定命中（上下文佐证）。
    """
    raw = _load_json(os.path.join(DATA_DIR, "weak_names.json"), {})
    if isinstance(raw, dict):
        raw = raw.get("weak_names") or []
    return set(raw or [])


class StockMatcher:
    """三层词典匹配器。

    weak_mode 控制「弱词」（机器人/大智慧/好想你…这类通用词型简称）的处理：
        drop     —— 直接不进词典，绝对不报（默认，最干净）
        context  —— 进入词典，但只有同段文本里存在 ≥1 个强命中时才保留
        keep     —— 不特殊处理（全量召回，误报最多）
    """

    def __init__(self, stocks, aliases=None, concepts=None,
                 blacklist=None, enable_abbr=True,
                 weak_names=None,         weak_mode="context"):
        self.by_code = {s["code"]: s for s in stocks}
        self.aliases = aliases if aliases is not None else load_aliases()
        self.concepts = concepts if concepts is not None else load_concepts()
        self.blacklist = set(DEFAULT_BLACKLIST) | set(blacklist or [])
        self.enable_abbr = enable_abbr
        if weak_names is None:
            weak_names = load_weak_names()
        self.weak_names = set(weak_names or [])
        self.weak_mode = weak_mode if weak_mode in ("drop", "context", "keep") else "drop"
        self._build()

    # ------------------------------------------------------------ 构建
    def _build(self):
        auto = ahocorasick.Automaton()
        # key -> payload 列表（同一 key 可能对应多只股票）
        bucket = {}

        def put(key, payload):
            if not key:
                return
            bucket.setdefault(key, []).append(payload)

        # --- L1 简称 ---
        for s in self.by_code.values():
            name = s["name"]
            if len(name) < MIN_NAME_LEN or name in self.blacklist:
                continue
            # 弱词：drop 模式直接不进词典；context 模式标记后交给 match() 二次裁定
            if name in self.weak_names:
                if self.weak_mode == "drop":
                    continue
                put(name, {"layer": "L1", "kind": "name", "weak": True,
                           "cand": Candidate(s["code"], name)})
                continue
            put(name, {"layer": "L1", "kind": "name",
                       "cand": Candidate(s["code"], name)})
        # --- L1 代码 ---
        for code, s in self.by_code.items():
            put(code, {"layer": "L1", "kind": "code",
                       "cand": Candidate(code, s["name"])})
        # --- L1 拼音首字母 ---
        if self.enable_abbr:
            for s in self.by_code.values():
                abbr = (s.get("abbr") or "").lower()
                if len(abbr) >= MIN_ABBR_LEN and abbr.isalpha():
                    put(abbr, {"layer": "L1", "kind": "abbr",
                               "cand": Candidate(s["code"], s["name"])})
        # --- L2 别名 ---
        for alias, target in self.aliases.items():
            if isinstance(target, dict):
                code = target.get("code", "")
                name = target.get("name") or self.by_code.get(code, {}).get("name", code)
                put(alias, {"layer": "L2", "kind": "alias",
                            "cand": Candidate(code, name)})
            elif isinstance(target, str):
                name = self.by_code.get(target, {}).get("name", target)
                put(alias, {"layer": "L2", "kind": "alias",
                            "cand": Candidate(target, name)})
        # --- L3 概念（一对多）---
        for term, codes in self.concepts.items():
            cands = []
            for c in codes:
                s = self.by_code.get(c)
                cands.append(Candidate(c, s["name"] if s else c))
            if cands:
                put(term, {"layer": "L3", "kind": "concept", "cands": cands})

        for key, payloads in bucket.items():
            auto.add_word(key.lower(), (key, payloads))
        auto.make_automaton()
        self._auto = auto
        self._keys = len(bucket)

    # ------------------------------------------------------------ 匹配
    def _boundary_ok(self, text, low, start, end, kind):
        """按命中类型做边界校验，避免匹配到更长串的内部

        踩坑记录：不能用 str.isalpha() 判断"是否处在英文单词里"——
        Python 里中文也算 alphabetic（'了'.isalpha() 为 True），
        而 OCR 经常吃掉空格，把 "我买了 gzmt" 变成 "我买了gzmt"，
        此时前一个字符是汉字，会导致拼音缩写被误判为无效而漏掉。
        必须限定为 ASCII 字母。

        拼音缩写还有第二类坑：与界面英文词碰撞。"Ctl"（飞书的 Ctrl+K 提示）
        命中楚天龙(003040) 的 abbr ctl；"Del"/"Tab"/"Img" 同理。
        手打拼音首字母只有全小写/全大写两种形态，Title Case 的基本是界面词，
        再加一份英文 token 黑名单兜底。
        """
        prev = low[start - 1] if start > 0 else ""
        nxt = low[end] if end < len(low) else ""
        if kind == "code":
            # 6 位代码前后不能再是数字
            if prev.isdigit() or nxt.isdigit():
                return False
            return True
        if kind == "abbr":
            # 拼音缩写前后不能再是 ASCII 字母（中文不算，否则会漏匹配）
            if (prev.isascii() and prev.isalpha()) or (nxt.isascii() and nxt.isalpha()):
                return False
            surface = text[start:end]
            if not (surface.islower() or surface.isupper()):
                return False                      # Ctl / Del / Img → 界面词
            if surface.lower() in ENGLISH_TOKEN_BLACKLIST:
                return False
            return True
        return True

    def match(self, text, extra_blacklist=None):
        """返回按位置排序、互不重叠的命中列表"""
        if not text:
            return []
        # 错字归一：等长替换，偏移不变，调用方仍按原文定位
        for src, dst in _TYPO_NORMALIZE:
            if src in text:
                text = text.replace(src, dst)
        low = text.lower()
        raw = []
        for end_idx, (key, payloads) in self._auto.iter(low):
            start = end_idx - len(key) + 1
            for p in payloads:
                kind = p["kind"]
                if not self._boundary_ok(text, low, start, end_idx + 1, kind):
                    continue
                if extra_blacklist and text[start:end_idx + 1] in extra_blacklist:
                    continue
                cands = p.get("cands") or [p["cand"]]
                # 同一 key 可能映射多只股票（拼音缩写碰撞很常见）
                if p.get("cand") and len(payloads) > 1:
                    cands = [q["cand"] for q in payloads if q.get("cand")]
                raw.append(Match(
                    start=start, end=end_idx + 1,
                    surface=text[start:end_idx + 1],
                    key=key, layer=p["layer"], kind=kind,
                    candidates=cands,
                    weak=bool(p.get("weak")),
                ))

        hits = self._resolve(raw)
        return self._apply_weak_policy(hits, text)

    def _apply_weak_policy(self, hits, text):
        """context 模式：弱词要有佐证才保留

        佐证有两类，任一成立即保留：
          A. 同段里还有强命中（6 位代码 / 非通用词简称 / 别名）
             —— 说明这段文本就是在聊股票
          B. 同段里出现行情语义词（涨停/加仓/放量/板块/龙虎榜…）
             —— 弱词此时大概率也是股票指代

        实测样例：
          「民生为大，把老百姓的…」（微信新闻预览）→ 无佐证 → 丢弃 ✓
          「机器人 300024 涨停」                    → 代码佐证 → 保留 ✓
          「机器人板块今天爆发」                     → 行情词佐证 → 保留 ✓
          「我刚问了机器人，它说不知道」              → 无佐证 → 丢弃 ✓
        """
        if self.weak_mode != "context" or not hits:
            return hits
        # 佐证 A：同段还有强命中；佐证 B：同段出现行情语义词
        if any(not m.weak for m in hits):
            return hits
        if self._has_market_word(text):
            return hits
        # 全是弱词且无任何佐证 → 整段丢弃（宁可漏报，不可满屏误报）
        return []

    @staticmethod
    def _has_market_word(text):
        return any(w in text for w in MARKET_CONTEXT_WORDS)

    @staticmethod
    def _resolve(matches):
        """重叠消解：长匹配优先，同长则层级优先，最后按出现顺序"""
        if not matches:
            return []
        layer_rank = {"L2": 0, "L1": 1, "L3": 2}
        kind_rank = {"alias": 0, "name": 1, "code": 1, "abbr": 2, "concept": 3}
        ordered = sorted(
            matches,
            key=lambda m: (-(m.end - m.start), layer_rank.get(m.layer, 9),
                           kind_rank.get(m.kind, 9), m.start),
        )
        taken = []
        occupied = []
        for m in ordered:
            if any(not (m.end <= a or m.start >= b) for a, b in occupied):
                continue
            # 合并同一区间的同名候选（如拼音缩写碰撞）
            taken.append(m)
            occupied.append((m.start, m.end))
        taken.sort(key=lambda m: m.start)
        return taken

    def match_texts(self, texts):
        """批量匹配，返回 [(原文, [Match, ...]), ...]"""
        return [(t, self.match(t)) for t in texts]


def build_default(stocks_payload=None, verbose=False, weak_mode="context"):
    """从缓存的主数据构建默认匹配器"""
    if stocks_payload is None:
        try:
            from . import stockdb
        except ImportError:
            import stockdb  # type: ignore
        stocks_payload = stockdb.load()
    m = StockMatcher(stocks_payload["items"], weak_mode=weak_mode)
    if verbose:
        print("[Matcher] 词典键 %d 个（股票 %d 只，别名 %d，概念 %d，弱词 %d/%s）"
              % (m._keys, len(m.by_code), len(m.aliases), len(m.concepts),
                 len(m.weak_names), m.weak_mode))
    return m


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ROOT)
    samples = [
        "中芯国际今天放量了，我准备加点仓位",
        "宁德时代回调，贵州茅台缩量到位",
        "600519 和 sh600519 还有 000001",
        "我买了 gzmt 和 zxgj",
        "光模块今天爆发，固态电池也在动",
        "中芯和茅台都还行",
        "中国平安 vs 平安银行，你选哪个",
        "20240519 这个日期不是股票代码",
        # --- 弱词专项 ---
        "我刚问了机器人，它说不知道",
        "机器人板块爆发，埃斯顿涨停",
        "机器人 300024 这个位置不错",
        "好想你出了个大红包活动",
        "民生为大，把老百姓的..",
        "老百姓今天涨停了",
        "向日葵开得真好",
        "买点老百姓，加仓三成",
        # --- 错字容错专项（谷份/古份 → 股份）---
        "沙河谷份和河钢古份今天都涨停了",
        "紫光古份放量，我准备加点仓位",
        # --- 拼音缩写 × 界面英文词碰撞专项 ---
        "Q 搜素 (Ctl+ K)",
        "按 Del 删除，Tab 切换，Img 目录",
        "我买了 ctl 和 gzmt",
        "CTL 这个票怎么样",
    ]
    for mode in ("drop", "context", "keep"):
        m = build_default(verbose=True, weak_mode=mode)
        print("\n=== weak_mode = %s ===" % mode)
        for s in samples:
            hits = m.match(s)
            print("  %-30s → %s" % (s, hits if hits else "无"))
