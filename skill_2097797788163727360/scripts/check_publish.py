#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_publish.py — 发布前诊断 + 改进建议。

不止"检查违规"，更给"怎么写得更好"的可操作建议。覆盖六个维度：
  1) 标题     ：评分 + 短板分析 + 改写示例
  2) 内容爆款度：开头 hook / 金句密度 / 段落节奏 / 互动引导 / CTA / 结构 / 干货
  3) 创意差异化：套路化程度 / 痛点命中
  4) 审美排版  ：视觉锚点(callout/图/分隔)密度
  5) 传播力    ：转发收藏驱动力——社交货币 / 情绪共鸣 / 认知增量 / 身份认同 / 可复用资产
  6) 违规敏感词：绝对化 / 诱导分享关注 / 夸大承诺 / 医疗金融红线

用法：
  python check_publish.py --title "标题" --html 文章_wechat.html [--report 报告.html]
依赖：beautifulsoup4（仅内容分析需要；缺失则降级只做标题 + 敏感词）
"""
import os
import re
import sys
import argparse
import html as html_lib

# Windows 控制台默认 gbk，强制 utf-8 以免打印 emoji 报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SENSITIVE_FILE = os.path.join(HERE, "..", "assets", "sensitive_words.json")

# 标题钩子词（情感/利益驱动，提升打开率）
POWER_WORDS = [
    "揭秘", "干货", "教程", "指南", "如何", "怎样", "为什么", "为何",
    "终于", "没想到", "原来", "惊", "千万别", "建议收藏", "一文看懂",
    "速看", "重磅", "首次", "独家", "盘点", "避坑", "真相", "核心",
    "方法", "技巧", "秘籍", "福利", "限时", "免费", "亲测", "实测",
]

# 单字风险词（需人工确认是否为绝对化，避免误报）
RISKY_SINGLE = ["最", "第一", "唯一", "顶级", "极", "绝", "首"]

# ----------------------------------------------------------------------
# 标题升级方法论（2026-07）：让「对的人」3 秒觉得与我有关、点开有用
#   5 公式：人群+痛点+数字 / 结果+方法 / 提问式 / 避坑式 / 反差式
#   4 动作：圈人(具体) → 戳痛点 → 给明确收益 → 留钩子
#   长度：15-22 字黄金区；核心词前置
#   禁用：震惊/必看/不看后悔/100%有效/最好/第一/永久（标题党+易限流）
# ----------------------------------------------------------------------
# 1) 人群圈定词（越具体越好）
AUDIENCE_WORDS = ['宝妈','打工人','社畜','程序员','创业者','自由职业','体制内','30岁','90后','95后','新手','公众号','小红书','微信读书','读书','书友','书虫','重度用户','书架','职场','学生党','中层']
# 2) 痛点词
PAIN_WORDS = ['没流量','阅读量','越读越虚','用不上','说不清','存不下钱','焦虑','不会','不懂','踩坑','误区','白读','迷路','涨不动','上不去','留不住','转化低','没效果','假努力','低质','瓶颈']
# 3) 明确收益词
BENEFIT_WORDS = ['3个','5个','7个','9步','7天','翻倍','省下','直接能用','立刻','一步','搞定','见效','涨粉','提升','炼成','根治','救了','治好','不再','告别','解锁','拿捏']
# 4) 钩子开场词（制造认知缺口，留一点不说完）
HOOK_OPENERS = ['为什么','为何','如何','怎样','一文','别再','千万别','原来','没想到','揭秘','其实','我放弃了','我用']
# 5) 标题党 / 极限禁用词（既像标题党，又易触发审核限流，强惩罚）
TITLE_BAN = ['震惊','必看','不看后悔','不看亏','100%有效','最好','第一','永久','史上最','全网最低','绝对','极致','万能','王牌','金牌','100%','第一品牌','全球首发','独家','首款','顶级','最','唯一']
# 6) 五大标题公式（诊断时判断命中哪种、并据此给改写建议）
TITLE_FORMULAS = {
    '人群+痛点+数字': lambda t: any(a in t for a in AUDIENCE_WORDS) and any(p in t for p in PAIN_WORDS) and re.search(r'\d', t) is not None,
    '结果+方法': lambda t: any(b in t for b in ('靠','用','通过','学会','掌握')) and any(b in t for b in BENEFIT_WORDS),
    '提问式': lambda t: ('？' in t or '?' in t),
    '避坑式': lambda t: any(w in t for w in ('千万别','别再','避坑','误区','少碰','别踩')),
    '反差式': lambda t: any(w in t for w in ('却','但','反而','放弃了','停了')),
}

# ----------------------------------------------------------------------
# 0) 排版语义鲁棒识别（修复"只认死标记"误报）
#    同时兼容：① class 语义名  ② 转换后被展平的 inline-style 视觉特征
#    这样无论喂源 HTML 还是 wechat_convert 产出的 _wechat.html 都判得准。
# ----------------------------------------------------------------------
HEADING_CLASS = re.compile(r"\b(h[1-4]|heading|title|header|section-title|sec-title|hd|subtitle|sub-head)\b", re.I)
CALLOUT_CLASS = re.compile(
    r"\b(callout|note|card|box|tip|alert|quote|adv|pain|step|limit|future|cta|"
    r"highlight|info|warn|frame|panel|feature|well|insight)\b", re.I)
QUOTE_CLASS = re.compile(r"\b(quote|golden|saying|pull|lead-quote)\b", re.I)


def _classes(tag):
    return " ".join(tag.get("class", []) or [])


def _style(tag):
    return tag.get("style", "") or ""


def is_heading(tag):
    """小标题：原生 h1-h4 / 含标题语义 class / 左侧粗边 + 短文本的段。"""
    if tag.name in ("h1", "h2", "h3", "h4"):
        return True
    if HEADING_CLASS.search(_classes(tag)):
        return True
    st = _style(tag)
    if "border-left" in st and len(tag.get_text(strip=True)) <= 40:
        return True
    return False


def is_callout(tag):
    """视觉锚点框：含卡片语义 class / 圆角 +（背景色或边框）的块。"""
    if CALLOUT_CLASS.search(_classes(tag)):
        return True
    st = _style(tag)
    if "border-radius" in st and ("background" in st or "border" in st):
        return True
    return False


def is_quote(tag):
    """金句：blockquote / 含 quote 类 / 虚线或双线边框 / 居中粗体段。"""
    if tag.name == "blockquote":
        return True
    if QUOTE_CLASS.search(_classes(tag)):
        return True
    st = _style(tag)
    if ("border" in st and ("dashed" in st or "double" in st)) or \
       ("text-align:center" in st and "font-weight" in st):
        return True
    return False


# ----------------------------------------------------------------------
# 1) 标题
# ----------------------------------------------------------------------
def eval_title(title):
    if not title:
        return 0, ["未提供标题"], "—"
    n = len(title)
    score = 50
    tips = []
    if 15 <= n <= 22:
        score += 15
        tips.append(f'长度 {n} 字，落在 15-22 黄金区，手机端完整显示 ✅')
    elif 8 <= n <= 14:
        score += 6
        tips.append(f'长度 {n} 字，偏短，可再补人群/收益信息')
    elif 23 <= n <= 30:
        score -= 6
        tips.append(f'长度 {n} 字，超 22 字，订阅列表会被「…」截断，建议压到 22 内')
    else:
        score -= 15
        tips.append(f'长度 {n} 字，严重失衡，建议 15-22 字')
    if n < 15:
        tips.append('不足 15 字，难同时塞下圈人+痛点+收益')
    if re.search(r'\d', title):
        score += 6
        tips.append('含数字，可信度与点击驱动双增 ✅')
    if '？' in title or '?' in title:
        score += 6
        tips.append('含疑问句式，易引发好奇 ✅')
    aud = [w for w in AUDIENCE_WORDS if w in title]
    if aud:
        score += 12
        tips.append('圈定具体人群「' + ''.join(aud) + '」，对的人 3 秒觉得与我有关 ✅')
    pain = [w for w in PAIN_WORDS if w in title]
    if pain:
        score += 8
        tips.append('戳中痛点「' + ''.join(pain) + '」，点开即中 ✅')
    ben = [w for w in BENEFIT_WORDS if w in title]
    if ben:
        score += 10
        tips.append('给明确收益「' + ''.join(ben) + '」，点开有用感强 ✅')
    hook = [w for w in HOOK_OPENERS if w in title]
    if hook:
        score += 6
        tips.append('含钩子词「' + ''.join(hook) + '」，制造认知缺口 ✅')
    if re.search(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]", title):
        score += 4
        tips.append("含 emoji，视觉更跳 ✅")
    ban = [w for w in TITLE_BAN if w in title]
    if ban:
        score -= 25
        tips.append('含标题党/极限词 ' + str(ban) + '：既像标题党又易限流，必须换（如很/大多数/亲测）')
    score = max(0, min(100, score))
    grade = "优" if score >= 85 else "良" if score >= 70 else "中" if score >= 55 else "待优化"
    return score, tips, grade


def title_gaps(title):
    """返回标题短板 + 按 5 公式生成的 2-3 个改写示例（示例仅参考，请按实际调）。"""
    gaps = []
    rewrites = []
    n = len(title)
    if n > 22:
        gaps.append('超 22 字，列表/分享卡片会被截断')
    elif n < 15:
        gaps.append('不足 15 字，难同时塞下圈人+痛点+收益')
    if not any(a in title for a in AUDIENCE_WORDS):
        gaps.append('没圈定具体人群（如微信读书重度用户/书架100本+），对的人难 3 秒对号入座')
        rewrites.append('微信读书重度用户，最容易踩的 3 个坑（人群+痛点+数字）')
    if not any(p in title for p in PAIN_WORDS) and not any(h in title for h in HOOK_OPENERS):
        gaps.append('缺痛点或钩子，好奇驱动不足')
        rewrites.append('为什么读了很多书却用不上？一文讲透（提问式）')
    if not any(b in title for b in BENEFIT_WORDS) and not re.search(r'\d', title):
        gaps.append('缺明确收益/数字，点开有用感弱')
        rewrites.append('靠这 9 步读书循环，把书架炼成能力坐标（结果+方法）')
    if any(w in title for w in ('最','第一','唯一','顶级','绝对','最好','100%','震惊','必看')):
        gaps.append('含绝对化/标题党词，易违规限流，建议换说法')
        rewrites.append('大多数人都踩过的读书误区（用大多数替代最）')
    seen = set()
    uniq = []
    for r in rewrites:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return gaps, uniq[:3]


# ----------------------------------------------------------------------
# 2) 内容爆款度
# ----------------------------------------------------------------------
def analyze_content(html_raw):
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return None
    soup = BeautifulSoup(html_raw, "lxml")
    plain = re.sub(r"\s+", "", soup.get_text())
    char_count = len(plain)
    if char_count == 0:
        return None
    paras = [re.sub(r"\s+", "", p.get_text()) for p in soup.find_all(["p", "section", "div"])]
    # 开头 hook
    head = plain[:140]
    hook_words = ["烦", "难", "总", "每次", "崩溃", "踩坑", "原来", "没想到", "其实",
                  "秘密", "真相", "为什么", "如何", "怎样", "别", "千万别", "终于", "后悔", "救"]
    hook_hit = [w for w in hook_words if w in head]
    has_q = "？" in head or "?" in head
    hook_score = min(100, 35 + len(hook_hit) * 15 + (15 if has_q else 0))
    # 金句密度（鲁棒：blockquote 或带 quote 类 / 虚线居中粗体段）
    quotes = sum(1 for t in soup.find_all(["blockquote", "section", "p", "div"]) if is_quote(t))
    bolds = len(soup.find_all(["strong", "b"]))
    sents = [s for s in re.split(r"[。！？!?]", plain) if s.strip()]
    short = [s for s in sents if len(s) <= 15]
    short_ratio = len(short) / max(1, len(sents))
    quote_score = min(100, quotes * 18 + bolds * 5 + int(short_ratio * 45))
    # 段落节奏
    lens = [len(p) for p in paras if p]
    avg = sum(lens) / max(1, len(lens))
    short_p = [l for l in lens if l < 40]
    rhythm_score = min(100, int(len(short_p) / max(1, len(lens)) * 60) +
                      (30 if avg < 80 else 12 if avg < 130 else 0))
    # 互动引导
    interact = len(re.findall(r"你|评论|留言|聊聊|吗？|吗\?|怎么看|说说看", plain))
    interact_score = min(100, interact * 18)
    # CTA
    cta = len(re.findall(r"关注|点赞|收藏|在看|转发|分享", plain))
    cta_score = min(100, cta * 25)
    # 结构（小标题，鲁棒：原生 h2/h3 或带标题语义 class / 左侧粗边短段）
    heads = sum(1 for t in soup.find_all(["h1", "h2", "h3", "h4", "section", "p", "div"]) if is_heading(t))
    struct_score = min(100, heads * 18)
    # 干货密度
    lists = len(soup.find_all("li"))
    steps = len(re.findall(r"第一|首先|其次|步骤|第[一二三四五六七八]", plain))
    nums = len(re.findall(r"\d+", plain))
    value_score = min(100, lists * 8 + steps * 15 + min(30, nums))
    content_score = round(hook_score * 0.25 + quote_score * 0.2 + rhythm_score * 0.15 +
                          interact_score * 0.1 + cta_score * 0.1 + struct_score * 0.1 + value_score * 0.1)
    tips = []
    if hook_score < 60:
        tips.append("开头 140 字缺'痛点/反常识/冲突'钩子，前 3 秒抓不住人")
    if quote_score < 50:
        tips.append("金句/加粗偏少，读者难记住'一句话亮点'，建议每 300 字埋 1 个金句或加粗")
    if rhythm_score < 50:
        tips.append("段落偏长，手机阅读易疲劳，建议多切 <40 字短段")
    if interact_score < 30:
        tips.append("缺互动句式（'你遇到过吗？'），评论/转发率低")
    if cta_score < 30:
        tips.append("文末无关注/在看引导，涨粉机会浪费")
    if struct_score < 40:
        tips.append("小标题(h2/h3)太少，长文缺乏'导航'，跳读率高")
    if value_score < 40:
        tips.append("干货密度低，缺清单/步骤/数据，收藏价值不足")
    return dict(char_count=char_count, hook=hook_score, quote=quote_score, rhythm=rhythm_score,
                interact=interact_score, cta=cta_score, struct=struct_score, value=value_score,
                score=content_score, tips=tips)


# ----------------------------------------------------------------------
# 3) 创意差异化
# ----------------------------------------------------------------------
def analyze_creativity(plain):
    cliche = ["一文搞定", "五个方法", "3个技巧", "必备", "超全", "史上最", "干货满满", "建议收藏", "全攻略"]
    cliche_hit = [w for w in cliche if w in plain]
    pain = len(re.findall(r"烦|难|痛|坑|崩溃|焦虑|纠结|不会|不懂|踩过", plain))
    novelty = max(0, min(100, 70 - len(cliche_hit) * 18 + min(30, pain * 4)))
    tips = []
    if cliche_hit:
        tips.append(f"出现套路词 {cliche_hit}，角度偏常规，建议找'反常识/具体场景'切入")
    if pain < 2:
        tips.append("痛点词偏少，读者'与我有关'的代入感弱")
    if not tips:
        tips.append("角度有一定差异化，可再强化一个'别人没说过的点'")
    return novelty, tips


# ----------------------------------------------------------------------
# 4) 审美排版
# ----------------------------------------------------------------------
def analyze_aesthetic(soup):
    callouts = sum(1 for t in soup.find_all(["section", "div", "p", "blockquote"]) if is_callout(t))
    imgs = len(soup.find_all("img"))
    hrs = len(soup.find_all("hr"))
    score = min(100, callouts * 25 + imgs * 10 + hrs * 10)
    tips = []
    if callouts == 0:
        tips.append("无 callout/笔记框等视觉锚点，全文一个色调易视觉疲劳，建议加 1-2 个高亮框")
    if imgs == 0:
        tips.append("无配图，纯文字长文完读率偏低，建议每屏加 1 张图/封面")
    if hrs == 0:
        tips.append("无分隔线，段落边界模糊，建议用 <hr> 切分大块")
    if not tips:
        tips.append("视觉节奏尚可，可再优化配色对比")
    return score, tips, dict(callouts=callouts, imgs=imgs, hrs=hrs)


# ----------------------------------------------------------------------
# 4.5) 传播力 / 转发收藏驱动力
#      社交货币 · 情绪共鸣 · 认知增量 · 身份认同 · 可复用资产
# ----------------------------------------------------------------------
def analyze_shareability(plain):
    """转发/收藏的五大驱动力（来自《疯传》等传播学框架的启发式落地）。"""
    # 社交货币：转发后显得自己内行/有趣/有品味
    social_words = ["原来", "没想到", "其实", "冷知识", "99%", "圈内", "行家", "内幕",
                    "秘而不宣", "独家", "首发", "内部", "极少人知道", "反常识", "颠覆认知",
                    "看懂的人", "内行"]
    social_hit = [w for w in social_words if w in plain]
    social_score = min(100, 30 + len(social_hit) * 18)
    # 情绪共鸣：强情绪是转发第一推力
    emotion_map = {
        "敬畏": ["震撼", "惊叹", "绝了", "服了", "惊艳", "NB", "牛"],
        "愤怒": ["气", "怒", "黑幕", "无语", "离谱", "坑爹", "割韭菜"],
        "感动": ["泪", "破防", "暖", "泪目", "心疼", "戳中"],
        "怀旧": ["小时候", "当年", "回忆", "曾经", "那会儿", "青春"],
        "惊喜": ["真香", "意外", "惊喜", "居然", "竟然", "万万没想到"],
        "焦虑": ["慌", "怕", "焦虑", "纠结", "崩溃", "内耗"],
    }
    emotion_hit = {}
    for c, ws in emotion_map.items():
        if any(w in plain for w in ws):
            emotion_hit[c] = [w for w in ws if w in plain]
    emotion_score = min(100, 20 + len(emotion_hit) * 22)
    # 认知增量：新知/新框架/顿悟
    cogn_words = ["框架", "模型", "公式", "底层逻辑", "本质", "第一性原理", "心法", "体系",
                  "恍然大悟", "顿悟", "开窍", "原来如此", "认知", "方法论", "维度"]
    cogn_hit = [w for w in cogn_words if w in plain]
    cogn_score = min(100, 25 + len(cogn_hit) * 16)
    # 身份认同：圈层归属"这说的不就是我"
    identity_words = ["我们", "这类人", "作为", "打工人", "宝妈", "程序员", "的人",
                      "同频", "圈子", "一句话证明", "同行", "社畜", "体制内", "自由职业", "创业者"]
    identity_hit = [w for w in identity_words if w in plain]
    identity_score = min(100, 25 + len(identity_hit) * 12)
    # 可复用资产：清单/模板/话术，驱动收藏
    asset_words = ["清单", "模板", "话术", "流程图", "SOP", "公式", "表格", "速查",
                   "一图看懂", "收藏", "码住", "照着做", "对照表", "步骤图", "脑图"]
    asset_hit = [w for w in asset_words if w in plain]
    asset_score = min(100, 25 + len(asset_hit) * 16)

    dims = {
        "社交货币": social_score,
        "情绪共鸣": emotion_score,
        "认知增量": cogn_score,
        "身份认同": identity_score,
        "可复用资产": asset_score,
    }
    tips = []
    if social_score < 55:
        tips.append("缺'社交货币'：读者转发后难显得自己内行/有趣，建议加 1 个反常识冷知识或圈内梗")
    if emotion_score < 55:
        tips.append("情绪浓度偏低：缺能'戳中'的强情绪（感动/愤怒/惊喜/怀旧），强情绪是转发第一推力")
    if cogn_score < 55:
        tips.append("认知增量不足：缺'原来如此'的新知/新框架，读者缺'收藏以备思考'的理由")
    if identity_score < 55:
        tips.append("身份认同弱：难让读者觉得'这说的不就是我/我们这类人'，圈层归属感低")
    if asset_score < 55:
        tips.append("缺'可复用资产'：没有清单/模板/对照表，读者无'先收藏以后用'的动力")
    score = round(sum(dims.values()) / 5)
    return dict(score=score, dims=dims, tips=tips or ["五维驱动力基本具备，转发/收藏潜力不错"])


# ----------------------------------------------------------------------
# 5) 敏感词
# ----------------------------------------------------------------------
def load_sensitive():
    try:
        import json
        with open(SENSITIVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def scan_text(text, sens):
    found = {}
    for cat, words in sens.items():
        hits = [w for w in words if w in text]
        if hits:
            found[cat] = hits
    return found


# ----------------------------------------------------------------------
# 报告 HTML（内联样式 + section，公众号兼容）
# ----------------------------------------------------------------------
def color_for(score):
    if score >= 70:
        return "#07c160"
    if score >= 55:
        return "#fa9d3b"
    return "#e64340"


def card_html(name, score, tips):
    c = color_for(score)
    tip_html = "".join(f'<section style="margin:4px 0;color:#555;font-size:13px;">· {t}</section>' for t in tips) or \
               '<section style="margin:4px 0;color:#07c160;font-size:13px;">· 表现良好，继续保持</section>'
    return f'''
<section style="border:1px solid #eee;border-radius:8px;padding:12px 14px;margin:10px 0;">
  <section style="display:flex;justify-content:space-between;align-items:center;">
    <span style="font-size:15px;font-weight:bold;color:#333;">{name}</span>
    <span style="font-size:18px;font-weight:bold;color:{c};">{score}</span>
  </section>
  <section style="background:#f2f2f2;border-radius:6px;height:8px;margin:8px 0;overflow:hidden;">
    <section style="background:{c};height:8px;width:{score}%;"></section>
  </section>
  {tip_html}
</section>'''


def build_report(title, title_out, content_out, nov_out, nov_tips, ae_out, ae_tips, found, share_out=None):
    cards = []
    if title_out:
        sc, _, gr = title_out
        gaps, rew = title_gaps(title)
        tps = [f"等级 {gr}；短板：{('；'.join(gaps)) or '无明显短板'}"]
        tps += [f"改写示例：{r}" for r in rew]
        cards.append(card_html(f"标题 · {title}", sc, tps))
    if content_out:
        cards.append(card_html("内容爆款度", content_out["score"], content_out["tips"]))
    if nov_out is not None:
        cards.append(card_html("创意差异化", nov_out, nov_tips))
    if ae_out is not None:
        cards.append(card_html("审美排版", ae_out, ae_tips))
    if share_out is not None:
        cards.append(card_html("传播力·转发收藏", share_out["score"], share_out["tips"]))
    sens_html = ""
    if found:
        cat_name = {"absolute": "绝对化/极限用语", "induce": "诱导分享/关注",
                    "exaggerate": "夸大承诺", "redline": "医疗/金融红线"}
        rows = "".join(
            f'<section style="margin:4px 0;color:#e64340;font-size:13px;">⚠️ {cat_name.get(c,c)}：{", ".join(w)}</section>'
            for c, w in found.items())
        sens_html = f'<section style="border:1px solid #f5c6c6;border-radius:8px;padding:12px 14px;margin:10px 0;background:#fff5f5;">{rows}</section>'
    return f'''<section style="font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;max-width:680px;margin:0 auto;padding:16px;color:#333;">
  <h1 style="font-size:22px;margin:0 0 4px;">📊 发布前诊断报告</h1>
  <p style="color:#888;font-size:13px;margin:0 0 12px;">{title or '未命名'}</p>
  {''.join(cards)}
  {sens_html}
  <p style="color:#aaa;font-size:12px;margin-top:16px;">本诊断为基于爆款方法论的启发式评估，仅供参考，最终以《微信公众平台运营规范》为准。</p>
</section>'''


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="", help="文章标题")
    ap.add_argument("--html", default="", help="可选：待发布 HTML（用于扫描正文 + 内容诊断）")
    ap.add_argument("--name", default="",
                    help="推文前缀（与转换器 --name 一致）；省略 --report 时"
                         "自动输出 <name>_report.html，避免不同推文报告互相覆盖")
    ap.add_argument("--report", default="", help="可选：输出诊断报告 HTML 路径")
    args = ap.parse_args()

    sens = load_sensitive()

    print("=" * 52)
    print("📝 发布前诊断 + 改进建议")
    print("=" * 52)

    # 标题
    title_out = None
    if args.title:
        title_out = eval_title(args.title)
        score, tips, grade = title_out
        print(f"\n【标题】{args.title}")
        print(f"  评分：{score}/100  等级：{grade}")
        for t in tips:
            print(f"   · {t}")
        hits = [name for name, fn in TITLE_FORMULAS.items() if fn(args.title)]
        if hits:
            print("   命中公式：" + " / ".join(hits))
        gaps, rew = title_gaps(args.title)
        if gaps:
            print("   短板：" + "；".join(gaps))
        if rew:
            print("   改写示例（仅供参考）：")
            for r in rew:
                print(f"     → {r}")
    else:
        print("\n【标题】未提供（用 --title 可评估 + 给改写示例）")

    # 正文分析
    content_out = None
    nov_out = None
    nov_tips = []
    ae_out = None
    ae_tips = []
    share_out = None
    plain_text = args.title
    soup = None
    if args.html and os.path.exists(args.html):
        try:
            from bs4 import BeautifulSoup
            raw = open(args.html, "r", encoding="utf-8", errors="ignore").read()
            soup = BeautifulSoup(raw, "lxml")
            plain_text += "\n" + soup.get_text()
            content_out = analyze_content(raw)
            nov_out, nov_tips = analyze_creativity(re.sub(r"\s+", "", soup.get_text()))
            ae_out, ae_tips, _ = analyze_aesthetic(soup)
            share_out = analyze_shareability(re.sub(r"\s+", "", soup.get_text()))
        except Exception as e:
            plain_text += "\n" + open(args.html, "r", encoding="utf-8", errors="ignore").read()
            print(f"\n【内容诊断】跳过（需 beautifulsoup4：{e}）")

    if content_out:
        print("\n【内容爆款度】")
        print(f"   综合分：{content_out['score']}/100  字数：{content_out['char_count']}")
        print(f"   细分：hook {content_out['hook']} | 金句 {content_out['quote']} | 节奏 {content_out['rhythm']} | "
              f"互动 {content_out['interact']} | CTA {content_out['cta']} | 结构 {content_out['struct']} | 干货 {content_out['value']}")
        for t in content_out["tips"]:
            print(f"   · {t}")
    if nov_out is not None:
        print(f"\n【创意差异化】{nov_out}/100")
        for t in nov_tips:
            print(f"   · {t}")
    if ae_out is not None:
        print(f"\n【审美排版】{ae_out}/100")
        for t in ae_tips:
            print(f"   · {t}")
    if share_out is not None:
        print(f"\n【传播力·转发收藏驱动力】{share_out['score']}/100")
        for name, sc in share_out["dims"].items():
            print(f"   · {name} {sc}/100")
        for t in share_out["tips"]:
            print(f"   ➤ {t}")

    # 敏感词
    text = html_lib.unescape(re.sub(r"\s+", "", plain_text))
    if sens:
        found = scan_text(text, sens)
        print("\n【敏感词扫描】")
        if not found:
            print("   ✅ 未发现已知高风险词")
        else:
            cat_name = {"absolute": "绝对化/极限用语", "induce": "诱导分享/关注",
                        "exaggerate": "夸大承诺", "redline": "医疗/金融红线"}
            for cat, words in found.items():
                print(f"   ⚠️ {cat_name.get(cat, cat)}：{', '.join(words)}")
            print("\n   建议：以上词在公众号中极易触发审核/限流，能改则改；确属必要请确认表述合规。")
    else:
        print("\n【敏感词扫描】未加载词库（sensitive_words.json 缺失）")

    # 报告：--report 优先；否则若给了 --name，自动落到 <name>_report.html
    report_path = args.report
    if not report_path and args.name:
        html_dir = os.path.dirname(os.path.abspath(args.html)) if args.html else "."
        report_path = os.path.join(html_dir, f"{args.name}_report.html")
    if report_path:
        try:
            rep = build_report(args.title, title_out, content_out, nov_out, nov_tips, ae_out, ae_tips,
                               scan_text(text, sens) if sens else {}, share_out)
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(rep)
            print(f"\n📄 诊断报告已生成：{report_path}")
        except Exception as e:
            print(f"\n⚠️ 报告生成失败：{e}")

    print("\n" + "=" * 52)
    print("说明：本诊断为辅助参考，最终以《微信公众平台运营规范》为准。")
    print("=" * 52)


if __name__ == "__main__":
    main()
