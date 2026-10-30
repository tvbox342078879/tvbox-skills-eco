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


# 行首标签剥离：可能叠加多层（`[M03] [清账司派单 2026-10-27] [扩展点] …`）
_LEAD_TAG = re.compile(r'^\s*(?:\[[^\]]*\]|（[^）]*）|\([^)]*\))\s*')

# 派单/复述类前缀：这些东西是**谁在什么时候报的**，不是**这件事是什么**。
# 必须从身份里剥掉，否则同一件事每被转述一次就换一个身份。
_NOISE_TAG = re.compile(r'\[清账司派单[^\]]*\]|\[遗留[^\]]*\]|清账司派单\s*\d{4}-\d{2}-\d{2}|'
                        r'sid\s*[=:]\s*[0-9a-f]{6,}')


def _clean_title(t):
    """把标题剥成"这件事本身"，供**算身份**用（不用于展示）。

    ★ S184（A5 第一圈实测撞上，这是账本自增殖的第二处根因）：
      同一件事「MCP automation_update 就绪前脚本通道兜底」在账上是 **3 条**：
        · 一条由 `issues_ledger` 采来，title = `[扩展点] 长期方向：MCP …`
        · 一条由 `backlog` 采来（派单副本被写进对方 flow.json 后又被采回），
          title = `[M03] [清账司派单 2026-10-27] [扩展点] 长期方向：MCP …`
        · 一条由 `inspect_findings` 采来，title 又是另一种前缀组合
      根因不是回声抑制没写（`collect_backlog` 里早就挡了 `[清账司派单]` 前缀），
      而是**这个副本从别的路径绕回来了**，而身份算法 `_sig` 取 `title[:40]`——
      前 40 个字里塞满 `[M03] [清账司派单 2026-10-27] [扩展点]` 这类噪声，
      真正的内容一个字都没进哈希。于是"三个不同的哈希"其实指同一件事，
      而"两个不同的哈希"也可能因为前缀不同而被当成两件事。
      → 前 40 字截断 + 噪声前缀 = 身份既会**误合**也会**漏合**，两头都错。

    口径：算身份前先剥行首标签与噪声词，并且**不按字数截断**（哈希不在乎长短）。
    展示用的 title 仍保留原样——人要看到"谁派的、什么时候"，只是那不参与身份。
    """
    s = str(t or '')
    s = _NOISE_TAG.sub(' ', s)
    for _ in range(6):          # 多层叠加，逐层剥
        n = _LEAD_TAG.sub('', s)
        if n == s:
            break
        s = n
    # ★ 派单副本的**尾注**：`｜ sid=xxxx ｜ 下一步 …`。
    #   sid 上面已剥成空格，但 `｜ ｜ 下一步 …` 还留着两个分隔符，
    #   于是同一件事在 backlog 源里多出" ｜ ｜ 下一步 x"这一段，哈希仍然不同。
    #   剥尾注的理由同前缀：`下一步是什么` 是**处置建议**，不是**这件事是什么**。
    #   处置建议会随轮次变化（今天说下一步 A，明天改成 B），让它参与身份，
    #   等于每改一次建议就变成一件新事——账本会被"意见更新"撑爆。
    s = re.sub(r'\s*[｜|]\s*下一步\s*.*$', '', s)
    s = re.sub(r'\s*[｜|]\s*$', '', s)
    s = re.sub(r'[｜|]{2,}', '|', s)
    s = re.sub(r'\s+', ' ', s).strip(' ｜|')
    return s


def _sig(source, skill, title):
    """条目的身份。

    ★ S184（A5 第一圈实测）：`source` **不参与身份**。

      第一版写的是 `f'{source}|{skill}|{title}'`，理由看着很正当——
      「源不同 = 证据不同，合并应交给分诊之后」。实测这个理由站不住：

        同一件事「MCP automation_update 就绪前脚本通道兜底」在账上变成 **3 条**，
        分别来自 issues_ledger（问题总账）、backlog（被派单写进对方 flow.json 后又采回）、
        inspect_findings（监察司 M03 红字）。三个源，三条账，处置一次只清一条，
        另两条下一轮原样重报——**账本永远清不干净**。

      而"源不同"根本不是三件不同的证据：它是**同一件未结事项被三条通道各看见一次**。
      把"谁看见的"写进身份，等于规定"每多一条观察通道，账目就翻一倍"。
      这与用户要的「生态自己转一圈并留账」正好相反：转得越多，账越假。

      口径：**身份 = 这件事是什么**（技能 + 剥噪后的正文）。谁看见的、什么时候看见的，
      记在 `source` 字段里供追溯，但**不参与身份**。真正该分开的是"不同的事"，
      不是"同一件事的不同观察"。
    """
    raw = f'{skill}|{_clean_title(title)}'
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
    """未结 = status ∈ {open, next}。done/wontfix 视为已结。

    ★ S180：问题总账是**人写的**，最了解这件事归谁。所以它有两条通道可以
      直接指定分诊，不必让清账司再猜：
        `kind`  —— 性质（cloud/rule/route/code/...），决定 owner/next/due
        `next`  —— 下一步**做什么**（自由文本），覆盖掉 ROUTE_TABLE 的命令模板
      这解决了两类真实误派：
        ① P030 只因标题里有「探针遗留物」被猜成 code→legacy-watchdog；
        ② 即便更正为 cloud，FINE_ROUTE 又按「云端/节点/删不掉」把它甩给
           tvbox-janitor scan —— 可 P030 的下一步是**用户在客户端核对**，
           不是扫一遍再说。命令模板解决不了"这一步只能人做"的情况。
    """
    fp = os.path.join(SKILL_ROOT, 'ecosys-conductor', 'plans', 'issues_ledger.json')
    d = _read_json(fp, 'issues_ledger')
    if d is None:
        return []
    items = []
    for it in d.get('issues', []):
        if it.get('status') not in ('open', 'next'):
            continue
        # ★ S180（真实缺陷，实测复现）：**问题总账自带 from，就不该再靠标题猜性质**。
        #   原实现 `_guess_kind(title)` 只看标题关键词，于是：
        #     P030「…③ 探针遗留物 __uptest_s179.md…」 命中 KIND_HINTS 的
        #     `code:('探针',)` → kind=code → ROUTE_TABLE → **legacy-watchdog**。
        #   可 P030 自己写着 `from=S179`、`next=用户在客户端核对孤儿夹附件后清账`——
        #   它是**云端/账本口径**的事，跟 legacy-watchdog（代码遗留巡检）毫无关系。
        #   更糟的是 _assign 只让 FINE_ROUTE 覆盖 owner/next，**kind 不动**，
        #   于是 due 也按 code=7 天算，比 unknown=14 天更紧——错上加错。
        #   口径：这一类条目**优先用它自己显式声明的性质**
        #   （issues add --kind，落在 `kind` 字段；或 `from` 是已知语义词如「云端」），
        #   都没有才退回标题猜。猜出来的值一律留在 evidence 里，便于事后复核
        #   分诊是不是被关键词带跑。
        _raw_title = it.get('title', '')
        _from = (it.get('from') or '').strip()
        _declared = (it.get('kind') or '').strip()
        _explicit = (_KIND_BY_FROM.get(_declared)
                     or _KIND_BY_FROM.get(_from))
        _guessed = _guess_kind(_raw_title)
        _kind = _explicit or _guessed
        _how = (f'kind={_kind}（显式声明 {_declared or _from}，不猜标题）'
                if _explicit else f'kind={_kind}（标题猜·未声明性质，猜原值={_guessed}）')
        items.append(_item('issues_ledger', 'ecosys-conductor',
                           f"{_raw_title}（{it.get('id')}·{it.get('status')}）",
                           _kind,
                           f"{fp} #{it.get('id')} 来源={_from or '未记'} {_how}"))
        # ★ S181：把「这个 kind 是**人显式声明**的还是机器猜的」传下去。
        #   分诊层需要区分：显式声明 → 关键词不许再改判；机器猜 → 关键词可以纠正。
        #   不传这个标志，dispatch 只能看到 kind 有值，会把「猜的」也当成「声明的」，
        #   于是 FINE_ROUTE 整张表失效（比原来的误判更糟）。
        if _explicit:
            items[-1]['kind_declared'] = True
        # 显式 next：账本自己写了下一步就照办，**不让 ROUTE_TABLE 再改一遍**
        if (it.get('next') or '').strip():
            items[-1]['next_fixed'] = it['next'].strip()
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
            _it = _item('backlog', s, t, _guess_kind(t), f'{fp} backlog[{i}]')
            # ★★ S182：backlog 条目的归属是**它所在的技能**，不是关键词说了算。
            #   实测：learn-hub 的 `[本期不处理] 需接资料库（library）云端 API…`
            #   因为标题里有「云端」，被 FINE_ROUTE 甩给 **tvbox-janitor scan**，
            #   而这条讲的是"learn-hub 的 store 索引该上云"——janitor 去扫一遍能做什么？
            #   派错人的后果不是"效率低"，是**这条永远没人做**：收件人看它不关己，
            #   真正的负责人（learn-hub）在自己 backlog 里看不到派单。
            #   口径：`skill` 字段本来就是所属技能，直接用它当 owner，关键词不许改判。
            #   （issues_ledger 是全局账，才需要关键词分诊；backlog 是**某技能自己的**待办。）
            _it['owner_fixed'] = s
            items.append(_it)
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
# ★ S186 改造（原状见 unfinished_ledger e2437854bccb）：
#   旧实现只扫本地快照 `core_db_rows.json`，而那个文件里**只有 count 数字和 ts**——
#   形如 {"pNwZ...": {"count": 620, "ts": "2026-10-18T12:45:29"}}。
#   它压根没有记录正文，所以正则永远匹配不到 → 该源**恒为 0**，且看不出是
#   「真没有」还是「结构上不可能有」。实测印证：文件 317 字节、快照已 23.9 小时前，
#   而直接读云端时技能生态登记库能扫出 2 行真"待跟进"——这 2 条一直被静默漏采。
#   这正是本文件抬头 M-R80 要防的形态：源看着在跑，其实查不出任何东西。
#
#   改法：真读云端记录正文（走 library 官方 http_request，与生产同通道），
#   拿不到就报 CollectError（计入"无法判定"），**不再**因为读不到而返回空。
CLOUD_DBS = [
    ('T4kODtANe7rSrtZTu4sIND', '技能生态登记库'),
    ('pNwZ9uBOTz6FzRuvu8nryI', '源数据库'),
    ('8JBrhSwsJ3pnPYtoGLo9XV', '成品源库'),
    ('hGwE41OpJDOR8CW0sLUwRx', '第三库'),
]


def _cloud_rows(dbid, page_size=200):
    """真读云端一个库的记录正文。返回 list；失败抛 CollectError（不折叠成空）。"""
    try:
        sys.path.insert(0, os.path.join(SKILL_ROOT, 'library'))
        from _common import http_request, HttpError
    except Exception as e:
        raise CollectError('cloud', f'library http_request 不可用（{type(e).__name__}: {e}）')
    try:
        r = http_request('POST', '/space/api/agent/v1/query-database',
                         body={'databaseId': dbid, 'pageSize': page_size}, timeout=25)
    except HttpError as e:
        raise CollectError('cloud', f'{dbid} 读取失败 code={e.error_code} {e.backend_message[:60]}')
    except Exception as e:
        raise CollectError('cloud', f'{dbid} 读取异常（{type(e).__name__}: {e}）')
    data = r.get('data') if isinstance(r, dict) else None
    rows = (data or {}).get('results') if isinstance(data, dict) else None
    if rows is None and isinstance(r, dict):
        rows = r.get('results')
    if rows is None:
        raise CollectError('cloud', f'{dbid} 返回里没有 results 字段——格式变了，不当空处理')
    return rows


def collect_cloud(root='/root/.codebuddy/skills/ecosys-conductor/scripts'):
    """真读云端各库记录正文，扫「待跟进/待补/未闭环/待确认/暂无结论」。

    旧版只扫本地 count 快照 → 恒为 0（详见上方 S186 注释）。
    三态：读成功且零命中 = 真空（返回 []）；任一库读失败 = CollectError。
    """
    PAT = re.compile(r'(待跟进|待补|未闭环|待确认|暂无结论)')
    items, errs = [], []
    for dbid, name in CLOUD_DBS:
        try:
            rows = _cloud_rows(dbid)
        except CollectError as e:
            errs.append(f'{name}({dbid}): {e}')
            continue
        for idx, row in enumerate(rows):
            blob = json.dumps(row, ensure_ascii=False)
            if not PAT.search(blob):
                continue
            # 取最能代表该行的一句话，而不是整行 JSON
            rid = str(row.get('record_id') or row.get('id') or idx)
            for k, v in row.items():
                if isinstance(v, str) and PAT.search(v) and 4 < len(v) < 300:
                    items.append(_item('cloud', 'ecosys-conductor', v, 'cloud',
                                       f'{name}({dbid}) record={rid} 字段={k}'))
                    break
    if errs and not items:
        # 全失败才算无法判定；部分成功部分失败则保留已采到的，另由上层打印告警
        raise CollectError('cloud', '；'.join(errs))
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


# 问题总账的 `from` 字段 → 性质。**有映射的就不要猜标题**（S180）：
# 标题是给人读的自由文本，会出现「探针」「备份」「规则」等词，但那些词描述的是
# **手段**，不是**归属**。P030 三项全是运维/云端核对，只因写了「探针遗留物」
# 就被判成 code→legacy-watchdog，是典型的「被关键词带跑」。
# 只登记**语义明确**的 from；其余（如 S179 这种纯会话号）留空，退回标题猜。
_KIND_BY_FROM = {
    '云端': 'cloud',   'cloud':  'cloud',
    '规则': 'rule',    'rule':   'rule',
    '路由': 'route',   'route':  'route',
    '源线': 'datasource', 'datasource': 'datasource',
    '监测': 'monitor', 'monitor': 'monitor',
    '总库': 'library', 'library': 'library',
}


def collect_promises():
    """会话承诺（S136 追加）：AI 在会话里答应接下来做、但还没收口的事。
    这是六源之外**最容易被忽略的一源**——它不写在任何文件里，只活在上下文，
    上下文一没就彻底蒸发。故必须靠显式登记（tvbox close promise）。"""
    import promises as _pm
    return _pm.as_items()


# ---------------- ⑦ 监察司遗留（红字 → 欠账） ----------------
def _inspect_findings_fp():
    """监察司遗留历史文件。按候选路径逐个探，**不做"找得到就用、找不到就当空"**。"""
    for p in (os.path.join(SKILL_ROOT, 'tvbox-inspect', 'findings_history.json'),):
        if os.path.exists(p):
            return p
    return ''


def _strip_metric(t):
    """从监测项文本里剥掉**观测值**，只留"在说哪件事"。

    ★ S184 第四圈实测（账本每轮仍涨 1 条）：M19 的正文是

        ecosys-conductor 平衡性 77.5｜最弱=质量短板(5.0)

      而每跑一次扫描，这个分数都会变（77.5 → 76.5 → …）。
      把带分数的原文当身份 ⇒ **每轮都是一件"新事"**，账本每轮涨一条，
      而涨的那条永远是同一件事。

    观测值与事件的区别：
      · 77.5 是**这一期量出来的数**（会变，变了不代表事情变了）；
      · 「ecosys-conductor 平衡性不足，短板是质量」是**这件事**（不变）。
    欠账记的是后者。分数留在 `evidence` 与 `from_period` 里供追溯，
    但**不参与身份**——否则"指标动了"会被记成"欠了新账"。

    剥法：数字（含小数/百分比）、括号里的量值、以及它们前面的
    `平衡性/得分/命中率/覆盖率…` 这类**指标名**（指标名+数值是一体的）。
    """
    s = str(t or '')
    # 括号里的纯数值：`(5.0)` `（85%）`。
    # ★ 必须限定"括号内**只有**数值"——第一版写成 `[\d.%/]+` 就够，
    #   但顺手加的 `strip(' ｜|()（）')` 把**正常括号**也剥了半边，
    #   实测把 `（legacy-watchdog）` 剥成 `legacy-watchdog`、两边不一致。
    #   教训：剥噪的字符集要**逐个说明为什么在里面**，不能图省事一把梭。
    s = re.sub(r'[（(]\s*[\d.%/]+\s*[）)]', '', s)
    # 指标名 + 数值：`平衡性 77.5` → `平衡性`（指标名留着，数值是观测）
    s = re.sub(r'(平衡性|得分|分数)\s*[\d.]+', r'\1', s)
    # 裸数值（含小数/百分号）。前面必须是空格或行首，避免吃掉 `drpy2` 这类名字里的数字。
    s = re.sub(r'(?<=[\s｜|(（])[\d]+(?:\.[\d]+)?\s*(?:%|分|条|项|个)?', '', s)
    s = re.sub(r'\s+', ' ', s).strip(' ｜|')
    return s


def collect_inspect_findings():
    """★ S184（A0 实测发现的自转断点）：把**监察司当期红字**变成可派单的欠账。

    为什么必须新增这一源：
      自转链条是「红字 → 派单 → 接单 → 处置 → 收口 → 对账」。
      A0 实测：`tvbox inspect scan` 报了「ecosys-conductor 平衡性 85.5｜最弱=质量短板」，
      而 `tvbox close status` 的 21 条未结里**一条都不是它**——
      逐条核对特征串（`平衡性 85.5` / `质量短板`）命中 0 条。
      也就是：红字只活在 `findings_history.json` 和报告文本里，
      清账司的七源里根本没有"监察司遗留"这一路。

      后果不是"少采一条"，而是**整个第三条腿断了**：
      红字不会派单 → 不会有人接 → 不会被收口 → 下一轮扫描原样再报一遍。
      M19 这一项已经连续多期出现在报告里，从来没进过任何人的待办。
      S178 ③期「账驱动：每笔红字有下文」缺的就是这一环，而且缺得很安静——
      因为 `close` 的输出看着一切正常（21 条未结、四向联动 4/4、账本健康）。

    口径决定（为什么按**最新一期**而不是按累积）：
      · findings_history 是**滚动快照**（保留最近 30 期），不是台账。
        按累积采会把"三期前已经自己消失的遗留"重新翻出来派单，等于制造噪声。
      · 按最新一期采，等于"**现在还在红的**都该有人管"；某一项消失（修好了 /
        监测项被删了），下一轮它自然不在最新一期里 → 采集不到 → 由 vanished 告警
        提示人工确认，而不是自动判绿（M-R82：不因"这次没采到"就清账）。
      · 同一项连续多期存在时，signature 稳定（source+skill+title 不变）→ 幂等，
        不会每轮新增一条（不会重演 S136 那个"账本自我增殖"）。

    owner 归谁：`skill` 字段取**遗留指向的技能**（不是 tvbox-inspect）——
      它是"谁该去修"，不是"谁报的"。监察司只负责发现，不负责修（它的 lanes 写得
      很清楚：管"盯"，不管"修"）。所以 owner 按该技能分诊；
      遗留指向的技能若在生态里不存在（如系统技能），归 ecosys-conductor 兜底。
    """
    fp = _inspect_findings_fp()
    if not fp:
        # 文件不存在 ≠ 没有遗留。但也不能因此判红——监察司可能是没跑过。
        # 三态里这一档归"空"，由 M05 快照新鲜度负责报"从没扫过"。
        return []
    d = _read_json(fp, 'inspect_findings')
    if d is None:
        return []
    hist = d.get('history') or []
    if not hist:
        return []
    latest = hist[-1]
    ts = latest.get('ts', '')
    keys = latest.get('keys') or {}
    items = []
    for _k, v in keys.items():
        if not isinstance(v, dict):
            continue
        mon = str(v.get('monitor') or '?')
        it_txt = re.sub(r'\s+', ' ', str(v.get('item') or '')).strip()
        if not it_txt:
            continue
        sev = str(v.get('sev') or '')
        # 遗留指向哪个技能：优先取 item 文本里出现的技能名（结构证据，不猜）。
        # ★ S184 第一版做错了，此处记录为什么改：原来遍历 `sorted(os.listdir(SKILL_ROOT),
        #   key=len, reverse=True)` 取**第一个命中**的目录名。实测这个"最长匹配优先"
        #   在真实数据上直接选错——`tvbox-inspect` 比 `ecosys-conductor` 短，
        #   排序后被后遍历，于是「ecosys-conductor 平衡性 85.5」被判给了 tvbox-inspect，
        #   欠账落在发现者名下而不是负责人名下（派单等于派给了报信的人）。
        #   更隐蔽的是：`skill_2104…` 这类长目录名一旦命中，还会盖住真正的技能名。
        #   正确做法：按 **item 里出现位置** 取"文本中最早出现的那个技能名"——
        #   监测项的名字习惯是「<技能> <现象>」，技能名总在句首。
        sk = 'ecosys-conductor'
        try:
            best = None
            for cand in os.listdir(SKILL_ROOT):
                if not os.path.isdir(os.path.join(SKILL_ROOT, cand)):
                    continue
                pos = it_txt.find(cand)
                if pos < 0:
                    continue
                # 位置优先；同位置取更长的（避免 tvbox-inspect 盖过 tvbox-inspect-x 这类前缀重合）
                if best is None or pos < best[0] or (pos == best[0] and len(cand) > len(best[1])):
                    best = (pos, cand)
            if best:
                sk = best[1]
        except Exception:
            pass
        its = _item('inspect_findings', sk,
                    f"[{mon}] {it_txt}", 'code',
                    f'{fp} 期次 {ts} monitor={mon} sev={sev}')
        # ★★★ S184（A5 第四圈实测，账本还在涨，这是身份的最后一处错）：
        #   **期次不能进身份，观测值也不能进身份。**
        #
        #   前几版写的是 `f"{ts}|{mon}|{it_txt}"`，理由很正当：不带期次的话，
        #   一旦 close 过，`sync` 的 `closed_sigs` 会把以后每期都挡掉（"还红着却永远不报"）。
        #   但带上期次之后走向了另一个极端——实测：
        #     · M19 每跑一次扫描，平衡分都在变（77.5 → 76.5 → …），
        #       而 `it_txt` 里写着这个分数 → 每期文本都不同；
        #     · 期次 `ts` 更是每期都不同。
        #   两个都进身份 ⇒ **每一期都是"一件新事"**，账本每轮涨一条，
        #   而涨的那条永远是同一件事（conductor 平衡分低）。
        #
        #   正确口径要同时满足两条相反的要求：
        #     ① 同一件事**跨期仍红** → 必须能被重新报出来（否则收口一次就永久静默）；
        #     ② 同一件事**跨期仍红** → 必须是**同一条账**（否则账本无限增殖）。
        #   这两条不冲突——它们说的是两件事：**身份**（是同一件事）与 **期次**
        #   （这一期还在不在红）。身份里放"是什么"，期次放"哪一期观测的"。
        #
        #   所以：
        #     · 身份 = 剥掉观测值后的项目文本（`_strip_metric`），**不含 ts**。
        #     · 那"收口一次就永久静默"怎么办？不靠身份挡，靠 **dispatch.sync 的
        #       `closed_sigs` 规则**挡：它只挡"本期之前就结过、且本期没有重新观测到"的，
        #       而"本期又红了一次"会带 `from_period` 更新——这条链路由
        #       `reopen_if_red_again` 负责（见 dispatch）。身份干净，续报由期次字段驱动。
        its['signature'] = _sig('inspect_findings', sk, f"{mon}|{_strip_metric(it_txt)}")
        its['sid'] = its['signature'][:12]
        # ★ S184：把"负责人指向"传给分诊层。
        #   不加这一步的后果（实测）：kind='code' → ROUTE_TABLE → legacy-watchdog，
        #   而红字点名的负责人是 ecosys-conductor，监察司只是报信的。
        #   派给报信人 = 这条永远没人修（收件人看它不关己，负责人又看不到派单）。
        its['owner_hint'] = sk
        # proven/阈值等细节留给监察司；这里只保证"红字一定有人管"
        its['from_monitor'] = mon
        its['from_period'] = ts
        items.append(its)
    return items


ALL_SOURCES = [
    ('issues_ledger', collect_issues, '问题总账未结'),
    ('promise',       collect_promises, '会话承诺未收口'),
    ('backlog',       collect_backlog, '各技能 flow.backlog 未结'),
    ('changelog',     collect_changelog, 'CHANGELOG 显式敞开段'),
    ('monitors',      collect_monitors, '监测项未实证'),
    ('cloud',         collect_cloud, '云端台账/经验库待跟进'),
    ('skillext',      collect_skillext, '技能正文待办标记'),
    # ★ S184：第八源。七源之外新增——红字必须落成欠账，否则"发现"与"处置"之间
    #   永远隔着一个人肉搬运动作（S178 ③期「账驱动」的核心缺口）。
    ('inspect_findings', collect_inspect_findings, '监察司当期红字'),
]


def collect_all():
    """跑全部采集源。返回 (items, errors, stats)。
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
