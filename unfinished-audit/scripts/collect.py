#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清账司·六源采集器（S136 · unfinished-audit）

设计要点（照 M-R80「六源只读不猜」）：
  · 每个 collect_* 只读文件、不写、不改上游任何数据
  · 任一源读失败 → 返回 CollectError，由上层**原样打印**并计入「无法判定」
    **绝不** except: pass 折叠成「无欠账」——那正是历史上假绿的形态
    （源打不开 → 清单空 → 命令仍然打印 ✅ 一切正常）

统一结构 Item：
  sid       稳定标识（source:skill:序号）
  source    六源之一（issues_ledger / backlog / changelog / monitors / cloud / skillext）
  skill     产生该欠账的技能（用于按 owner 分组统计）
  title     一句话欠账描述（≤120 字，超长截断并标 …）
  kind      性质分类（code / route / rule / datasource / library / monitor / cloud / unknown）
  owner     监察官（由 dispatch.py 填，采集阶段为 None）
  next      下一步可执行命令（由 dispatch.py 填）
  due       期限 YYYY-MM-DD（由 dispatch.py 填）
  evidence  证据（原文定位：文件路径、行号、条目 id 等）
  signature sha1(source|skill|title[:40]) —— 跨次对账指纹（M-R82）
"""

import hashlib
import json
import os
import sys
import re

SKILL_ROOT = '/root/.codebuddy/skills'
# ★ S172 修（M-R38 单一事实源）：原为**写死的 9 个技能名**。
#   实测后果：learn-hub 建好后（生态已 12 成员），六源采集的 ② backlog 源
#   **根本遍历不到它**——它的 7 条真欠账一条都没进总账，而 watchdog 却照报「backlog 未清」。
#   一个报、一个不采，两边口径分叉，读的人只会觉得"这账本不靠谱"。
#   正本在 conductor/eco_skills.json（S127 已归一），这里改为派生读取；
#   读不到时**降级到原名单并显式告警**（M-R60 三态：读失败≠没有技能）。
_ECO_FALLBACK = ('ecosys-conductor', 'legacy-watchdog', 'probe-hub', 'skill-evolution',
                 'tvbox-inspect', 'tvbox-interface', 'tvbox-janitor',
                 'tvbox-source-blacklist', 'tvbox-source-hub')


def _eco_skills():
    """从正本读生态成员；读失败返回 fallback 并告警（不静默）。"""
    fp = '/root/.codebuddy/skills/ecosys-conductor/scripts/eco_skills.json'
    try:
        with open(fp, encoding='utf-8') as f:
            d = json.load(f)
        names = d.get('eco_skills') if isinstance(d, dict) else None
        if isinstance(names, list) and names:
            return tuple(names)
    except Exception as e:
        pass
    print(f'  ⚠️ eco_skills.json 读取失败，降级用内置 {len(_ECO_FALLBACK)} 人名单'
          f'（新技能可能漏采，M-R60）', file=sys.stderr)
    return _ECO_FALLBACK


ECO = _eco_skills()

# 已完成语气 / 已归档标记：命中即视为**已结**，不进欠账
DONE_TAG = re.compile(r'^\s*[\[（(]\s*(已清零|已兑现|已闭环|已关闭|已完成|MVP已兑|已修|已治理|'
                      r'已落地|已消除|已登记|已补|已合并)')
DONE_NOTE = re.compile(r'^\s*[（(]\s*\d{4}-\d{2}-\d{2}\s*(已清零|已完成|已闭环)')

# 派单回声标记（S136）：清账司写进对方 backlog 的副本都带 `sid=<签名>`。
# 采集时必须识别并**跳过**——否则下一轮把副本当新欠账采回来，账本自我增殖。
# 注意：这里只认「派单副本」这一形态；不带 sid 的野生 backlog 照常采（那是真脏尾巴）。
ECHO_TAG = re.compile(r'\[清账司派单[^\]]*\]|sid[=:]\s*[0-9a-f]{8,}')

# 技能正文待办标记（与 legacy-watchdog M13 同口径，但**排除功能描述词**：
# 「转待办」「待办清单」是功能名不是真待办——S035 踩过这个假阳性）
TODO_MARK = re.compile(r'(TODO|FIXME|待补|待实现|待跟进|尚未实现|后续再|留待|下次再)')
# ★ S158 八补：断词守卫。`留待` 会命中「死壳剔除 / 下载失败=无法判定≠死保**留待**人工」——
#   "保留" + "待人工" 被错误切成 "保" + "留待" + "人工"，于是**一句判据说明被当成待办**。
#   实测：probe-hub/SKILL.md:356 的规则说明抓出一条 sid=79f624f7d943 的假待办。
#   这类误报最坏的地方不是多一条，而是**它会让人开始怀疑整张待办表**。
#   守卫：`留待` 前面若是「保」，则要求后面不是「人工/确认/处理」——
#   即 `保留待人工` 是状态描述，不是"留待某人"的动作。
# ★ S158 八补：断词守卫 —— 「保留待X」不是待办。
#
# 病因：`留待` 会命中「死壳剔除 / 下载失败=无法判定≠死保**留待**人工」——
#   "保留"+"待人工" 被切成 "保"+"留待"+"人工"，于是**一句判据说明被当成待办**。
#   实测两次：probe-hub/SKILL.md:356（`保留待人工`）、:353（`保留待测活`）。
#
# 我第一版写成 `保留待(人工|确认|处理|核|定)`，用户点第 1 件事时，
#   `保留待测活` 仍然挂在总账上 —— **只堵了眼前那一例，换个 X 就漏**。
#   现改为不带枚举的通用判据：`保留待` 后面接任何词，都是"先留着，等某状态"
#   的**状态描述**，不是"留待某人去做"的动作。
#   判据差异用一句话说清：待办要有**执行主体**，判据说明没有。
_MARK_FALSE = re.compile(r'保留待')
FUNC_HINT = re.compile(r'(转待办|待办清单|待办提取|待办标记|待办项|待办语气|待办提取跑过)')

# ★ S164：把 S158 自己写下的判据真正落地——**「待办要有执行主体，判据说明没有」**。
#   S158 当时只补了 `保留待` 一个正例（见上），换了场景照样漏。本轮实测又撞到一例：
#     ecosys-conductor/SKILL.md  「六源全采：… / 云端**待跟进**」
#   这里的「待跟进」是**六源之一的名字**（名词），不是"待某人跟进"（动作），
#   却被 TODO_MARK 当成待办抓出来（sid=22ec8b8577f2），在账上挂了两天。
#
#   为什么不再补词：补"云端待跟进"只堵这一行，下一条写成"库待跟进"又要补。
#   判据改成**结构判断**——下面任一成立，就不是动作句：
#     ① 枚举型：整行是「一长串用 / 或 · 或 、分隔的短语」，命中词只是其中一项
#        （"问题总账 / 会话承诺 / … / 云端待跟进" 6 项并列，典型枚举）
#     ② 命名型：命中词紧跟在书名号/引号/加粗标题后，或整段是「标签：值」里的"值"
#     ③ 无动作性：命中词所在分句里没有任何动词性收尾（去做/补齐/实现/处理/解决…）
#   三条都是**可机械判定**的，且不针对具体词。
_ENUM_SEP = re.compile(r'\s*[/／·、|]\s*')


def _is_enum_or_label(ln, m):
    """命中词只是"名字"而非"动作"→ True（S164 结构判据，不靠补词）。"""
    body = ln.strip().lstrip('-*#• ').strip()
    # ① 枚举：去掉命中片段后，仍有 ≥3 段被 / · 、 隔开的并列项
    segs = [s.strip() for s in _ENUM_SEP.split(body) if s.strip()]
    if len(segs) >= 3:
        return True
    # ② 命名型：命中词前后紧邻引号/省略号/加粗结束符，说明它在被"点名"而非"吩咐"
    left = ln[:m.start()]
    right = ln[m.end():]
    if re.search(r'[《「『“"\'*]$', left) or re.match(r'^[》」』”"\'*]', right):
        return True
    if left.rstrip().endswith('：') or left.rstrip().endswith(':'):
        return True
    # ③ 无动作性：命中词**后面没有任何内容**，说明它只是句尾名词（如"…/ 云端待跟进"）。
    #    第一版这里写的是"整行不含某份动词词表 → 跳过"，单测当场打脸：
    #    `- 待补：把 M31 的 proven 受控单测做完` 里的"做完"不在词表 → 被误判成名词句。
    #    **凡是靠动词词表判"有没有动作"，都会漏**——词表永远列不全，这套坑本生态踩过多次。
    #    改成结构判据：标记词之后还有实际内容 ⇒ 是在吩咐做事；到行尾了就只是个标签。
    if not right.strip(' 　.。,，;；:：)）]】'):
        return True
    return False


class CollectError(Exception):
    """采集失败（M-R80）：显式携带源名与原因，由上层原样打印。

    注意：**必须继承 Exception**。S136 首版只写了 __slots__ 没继承，
    结果是 `raise CollectError(...)` 直接抛 TypeError（"exceptions must derive from
    BaseException"），而且只在**真出错时**才炸——正常情况下跑一万次都不会发现。
    受控单测 T7 就是靠「造一个坏 json」把它抓出来的。
    """
    __slots__ = ('source', 'reason')

    def __init__(self, source, reason):
        self.source = source
        self.reason = reason
        super().__init__(f'{source}: {reason}')

    def __repr__(self):
        return f'CollectError({self.source}: {self.reason})'


def _sig(source, skill, title):
    raw = f'{source}|{skill}|{title[:40]}'
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]


def _item(source, skill, title, kind, evidence):
    title = re.sub(r'\s+', ' ', str(title)).strip()
    if len(title) > 120:
        title = title[:117] + '…'
    return {
        'sid': _sig(source, skill, title)[:12],
        'source': source,
        'skill': skill,
        'title': title,
        'kind': kind,
        'owner': None,
        'next': '',
        'due': '',
        'evidence': evidence,
        'signature': _sig(source, skill, title),
    }


def _read_json(path, source):
    """三态读：不存在=空（合法）；损坏=CollectError（M-R60）。"""
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception as e:
        raise CollectError(source, f'{os.path.basename(path)} 解析失败（{type(e).__name__}: {e}）')


# ---------------- ① 问题总账 issues_ledger.json ----------------
def collect_issues():
    """未结 = status ∈ {open, next}。done/wontfix 视为已结。"""
    fp = os.path.join(SKILL_ROOT, 'ecosys-conductor', 'plans', 'issues_ledger.json')
    d = _read_json(fp, 'issues_ledger')
    if d is None:
        return []
    items = []
    for it in d.get('issues', []):
        if it.get('status') not in ('open', 'next'):
            continue
        items.append(_item('issues_ledger', 'ecosys-conductor',
                           f"{it.get('title', '')}（{it.get('id')}·{it.get('status')}）",
                           _guess_kind(it.get('title', '')),
                           f"{fp} #{it.get('id')} 来源={it.get('from', '')}"))
    return items


# ---------------- ② 各技能 flow.json backlog ----------------
def collect_backlog():
    """采集各技能 flow.json backlog 未结项。

    **回声抑制（S136 实测踩坑，比任何采集噪声都危险）**：
    清账司派单时会把自己的条目**写进对方 backlog**（M-R81「派单真送达」）。下一轮
    采集又把这个副本采回来 —— 同一条欠账变成两条（原项 `bc2dd17c605d` + 副本
    `dd4ef2ea9de8`，title 一模一样）。放任下去会指数增长：
       第1轮 5 条 → 第2轮 10 条 → 第3轮 20 条 … 账本自己被自己撑爆。
    且更隐蔽的是：**总数看起来在"变多"，像发现了新欠账**，实际全是回声。

    处理：带 `sid=<签名>` 的条目是**派单副本**，不产新项；它的唯一价值是
    「对方是否已把它标为已清零」——那用于回填原项状态（见 dispatch.reconcile_echo）。
    """
    items = []
    for s in ECO:
        fp = os.path.join(SKILL_ROOT, s, 'scripts', 'flow.json')
        if not os.path.exists(fp):
            continue          # 无 flow.json 不算读失败（该技能本就无此载体）
        try:
            d = json.load(open(fp, encoding='utf-8'))
        except Exception as e:
            raise CollectError('backlog', f'{s}/scripts/flow.json 解析失败（{type(e).__name__}: {e}）')
        for i, b in enumerate(d.get('backlog', [])):
            t = str(b).strip()
            if not t:
                continue
            # 派单副本：不产新项（回声抑制）。其状态由 dispatch.reconcile_echo 单独读。
            if ECHO_TAG.search(t):
                continue
            # 已结判定：开头就带 [已清零] 标签，或开头是（YYYY-MM-DD 已清零：…）
            # 注意：必须**只看开头**——正文里提到「已清零」不代表这条已结
            # （实测 tvbox-source-hub 有一条开头没有标签、正文写「已清零」，
            #   如果整条搜关键词就会误判已结而漏掉它）
            if DONE_TAG.match(t) or DONE_NOTE.match(t):
                continue
            items.append(_item('backlog', s, t, _guess_kind(t), f'{fp} backlog[{i}]'))
    return items


# ---------------- ③ CHANGELOG 敞开段 ----------------
# 敞口段标题：**必须整行就是标题**，且标题本身就是在声明敞口。
# 反例（S136 实测踩过，14 条噪声全来自这里）：
#   「### 四、立法补全（原「仍敞开」四项，本轮已全部补齐）」
#   「### 六、仍敞开（本轮明确不做，留档不装睡）」
# 二者都含「仍敞开」，但前者是**已补齐**、后者才是真敞口。
# 差别在括号里的定性词：补齐/已补 = 假的；明确不做/留档 = 真的。
# 用「标题正文里出现『仍敞开』就切段」会一把抓进 12 条历史叙述（假阳性淹没）。
OPEN_HEAD = re.compile(r'(?m)^#{2,4}[ \t]*(?:[^\s、.．]{1,6}[、.．][ \t]*)?(仍敞开|留档不装睡|明确不做|尚未做|待办清单)(?![^\n]*已(?:全部)?补齐)[^\n]*$')


def collect_changelog():
    """只抓「仍敞开 / 留档不装睡」这类**显式声明的敞口段**里的条目。
    不做全量关键词扫描——CHANGELOG 正文大量「待补」是**历史记述**（描述当时怎么发现的），
    扫全文会淹没在噪声里（S136 实测：全文扫得 8 条，其中 6 条是历史叙述）。"""
    items = []
    for s in ECO:
        fp = os.path.join(SKILL_ROOT, s, 'CHANGELOG.md')
        if not os.path.exists(fp):
            continue
        try:
            txt = open(fp, encoding='utf-8').read()
        except Exception as e:
            raise CollectError('changelog', f'{s}/CHANGELOG.md 读取失败（{type(e).__name__}: {e}）')
        lines = txt.splitlines()
        i = 0
        while i < len(lines):
            m = OPEN_HEAD.match(lines[i])
            if not m:
                i += 1
                continue
            head_ln = i + 1
            i += 1
            while i < len(lines) and not lines[i].startswith('#'):
                ln = lines[i].strip()
                # 列表项才算条目；续行（缩进的非 - 开头）并入上一条
                if ln.startswith(('- ', '* ')):
                    body = ln[2:].strip()
                    j = i + 1
                    while j < len(lines) and lines[j].startswith((' ', '\t')) and \
                            not lines[j].strip().startswith(('- ', '* ')) and lines[j].strip():
                        body += ' ' + lines[j].strip()
                        j += 1
                    if body:
                        # 已结过滤（S136 三续·真 bug）：这里原来**没有** DONE_TAG 判定，
                        # 于是「仍在 CHANGELOG 敞口段里、但已在行首标了 [已清零]」的条目
                        # 每轮都被采回来，派单写进 backlog，下一轮再采——收口了也永远关不掉。
                        # 实测：5 条欠账全部 done 后跑 close，采集合计仍是 3 条，三条全部"复活"。
                        # 敞口段是**历史留档**（S022：不改写历史），不能被改写；但「已做完」
                        # 这件事必须能表达出来 —— 行首标记就是那个表达。
                        if DONE_TAG.match(body) or DONE_NOTE.match(body):
                            i += 1
                            continue
                        items.append(_item('changelog', s, body, _guess_kind(body),
                                           f'{fp}:{head_ln} 「{m.group(1)}」段'))
                i += 1
    return items


# ---------------- ④ 监测项未实证 monitors.json ----------------
def collect_monitors():
    """缺 proven 或 proven 为空 = 检查器有效性未被证明（『装健康的哑巴』M-R32）。"""
    fp = os.path.join(SKILL_ROOT, 'tvbox-inspect', 'monitors.json')
    d = _read_json(fp, 'monitors')
    if d is None:
        return []
    items = []
    for m in d.get('monitors', []):
        pv = m.get('proven')
        if pv:
            continue
        mid = m.get('id', '?')
        items.append(_item('monitors', 'tvbox-inspect',
                           f"{mid}「{m.get('name', '')}」未实证：检查器有效性无证明",
                           'monitor',
                           f'{fp} {mid} proven={"None" if pv is None else repr(pv)}'))
    return items


# ---------------- ⑤ 云端台账 / 经验库待跟进 ----------------
def collect_cloud(root='/root/.codebuddy/skills/ecosys-conductor/scripts'):
    """静态扫描本地留存的云端行缓存（core_db_rows.json）里「待跟进/待补」记录。
    真实云端读取失败由信号源17/23 报，本采集器只做本地缓存层扫描；
    缓存本身缺失 → 空（不是失败，另由信号源自检兜住）。"""
    fp = os.path.join(root, 'core_db_rows.json')
    d = _read_json(fp, 'cloud')
    if d is None:
        return []
    items = []
    stack = [('', d)]
    PAT = re.compile(r'(待跟进|待补|未闭环|待确认|暂无结论)')
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                stack.append((f'{path}.{k}' if path else str(k), v))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                stack.append((f'{path}[{i}]', v))
        elif isinstance(node, str) and PAT.search(node) and len(node) < 400:
            items.append(_item('cloud', 'ecosys-conductor', node, 'cloud', f'{fp} {path}'))
    return items


# ---------------- ⑥ 技能正文待办标记 ----------------
def collect_skillext():
    """扫 9 个技能 SKILL.md 正文里的真待办标记。
    排除：① 功能描述词（转待办/待办清单…）② 代码块内（示例代码天然含 TODO）
    ③ 已完成语气行（「已完成/已清零」）。"""
    items = []
    for s in ECO:
        fp = os.path.join(SKILL_ROOT, s, 'SKILL.md')
        if not os.path.exists(fp):
            continue
        try:
            lines = open(fp, encoding='utf-8').read().splitlines()
        except Exception as e:
            raise CollectError('skillext', f'{s}/SKILL.md 读取失败（{type(e).__name__}: {e}）')
        in_code = False
        for i, ln in enumerate(lines, 1):
            if ln.strip().startswith('```'):
                in_code = not in_code
                continue
            if in_code:
                continue
            if not TODO_MARK.search(ln):
                continue
            if _MARK_FALSE.search(ln):
                continue          # 断词误报：`保留待人工` 是状态描述，不是待办动作
            # ★ S164：结构判据——命中词只是"名字"（枚举项/被点名的标签）而非"吩咐去做"
            if _is_enum_or_label(ln, TODO_MARK.search(ln)):
                continue
            if FUNC_HINT.search(ln):
                continue
            if re.search(r'(已完成|已清零|已闭环|已修复|无待办)', ln):
                continue
            body = ln.strip().lstrip('-*# ').strip()
            if len(body) < 6:
                continue          # 表格分隔/单字提示忽略
            items.append(_item('skillext', s, body, _guess_kind(body), f'{fp}:{i}'))
    return items


# ---------------- 性质猜测（供 dispatch 分诊用） ----------------
KIND_HINTS = [
    ('route',      ('路由', '触发', '纳管', 'onboard', '体积', 'SKILL.md', '瘦身', '40KB', '缓冲线', '文档漂移')),
    ('code',       ('检查器', '脚本', 'bug', '异常', '伪', '失效', '折叠', '回归', '修复', '语法', '探针')),
    ('rule',       ('规则', '招式', '固化', '进化', '承载', '经验', '索引')),
    ('datasource', ('源库', '仓库', '上游', '采集', '线路', '测活', '直链', 'lives', 'py线')),
    ('library',    ('总库', '入库', '去重', '整包', '底包', '分类')),
    ('monitor',    ('监测项', 'proven', '信号源', '监测', '覆盖率')),
    ('cloud',      ('云端', '节点', '删不掉', '收件箱', 'DB_')),
]


def _guess_kind(text):
    t = str(text)
    for kind, words in KIND_HINTS:
        if any(w in t for w in words):
            return kind
    return 'unknown'


def collect_promises():
    """会话承诺（S136 追加）：AI 在会话里答应接下来做、但还没收口的事。
    这是六源之外**最容易被忽略的一源**——它不写在任何文件里，只活在上下文，
    上下文一没就彻底蒸发。故必须靠显式登记（tvbox close promise）。"""
    import promises as _pm
    return _pm.as_items()


ALL_SOURCES = [
    ('issues_ledger', collect_issues, '问题总账未结'),
    ('promise',       collect_promises, '会话承诺未收口'),
    ('backlog',       collect_backlog, '各技能 flow.backlog 未结'),
    ('changelog',     collect_changelog, 'CHANGELOG 显式敞开段'),
    ('monitors',      collect_monitors, '监测项未实证'),
    ('cloud',         collect_cloud, '云端台账/经验库待跟进'),
    ('skillext',      collect_skillext, '技能正文待办标记'),
]


def collect_all():
    """跑六源。返回 (items, errors, stats)。
    **单源失败不影响其它源**（各自独立 try），但失败必须出现在 errors 里被打印出来。"""
    items, errors, stats = [], [], []
    for src, fn, label in ALL_SOURCES:
        try:
            got = fn()
            stats.append((src, label, len(got), None))
            items.extend(got)
        except CollectError as e:
            stats.append((src, label, 0, e.reason))
            errors.append(e)
        except Exception as e:
            # 未预期异常同样显式报——绝不吞
            reason = f'采集器自身异常（{type(e).__name__}: {e}）'
            stats.append((src, label, 0, reason))
            errors.append(CollectError(src, reason))
    return items, errors, stats


if __name__ == '__main__':
    its, errs, sts = collect_all()
    print('六源采集：')
    for src, label, n, err in sts:
        print(f'  {"❌" if err else "✅"} {src:14s} {label:24s} {n:3d} 条'
              + (f'  ← {err}' if err else ''))
    print(f'\n合计 {len(its)} 条' + (f'，{len(errs)} 源无法判定' if errs else ''))
    for it in its[:40]:
        print(f"  [{it['source']:13s}|{it['skill']:24s}] {it['title'][:80]}")
