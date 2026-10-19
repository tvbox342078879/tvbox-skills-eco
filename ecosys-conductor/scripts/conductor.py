#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ecosys-conductor —— TVBox 生态指挥家（会话提速 + 技能协调 + 全局进化）

MVP 六命令：
  plan "<任务1>" "<任务2>" ...   并行分道规划（依赖图 + 分道 + 预计提速比）
  sprint "<任务1>" ...           冲刺执行：产出可粘贴的 tvbox p / run 执行串
  whoami "<意图>"                技能路由：该哪个技能接、谁能接力
  ask "<需求>"                   需求管家：术语翻译+历史呼应+路由+拆解（单一入口，随便怎么问）
  history ["<关键词>"]           需求成功清单：查"用户做过什么、当时交付啥"
  lanes                          分工地图：职责矩阵 + 重叠/空白
  next                           流水线站位预告：下一站谁就位（预触发·M-R67）
  recap                          会话监工：动作流水+经验落池+谁该介入（M-R67）
  audit-growth                   增长治理审计：规则/技能分类·去重·重复触发风险（M-R69）
  audit-compliance               合规专项审计：不干活（未触发）+乱干活（违规）盘点（M-R70）
  assess                         生态全景评估（五维：契约/健康/增益/噪声/活跃）
  direction                      进化方向（证据驱动 Top-N + 留痕对账）

设计边界：不做事、只调度。唯一自有写目标是 plans/evolution_plan.json。
提速三铁律：
  T1 读写分离并行——只读打包 tvbox p 并行，写库按依赖串行，上云收尾统一
  T2 同进程共享缓存——能 tvbox run 串联的绝不 p 并发
  T3 先 plan 后 sprint——禁止未规划直接开跑
"""
import sys, os, json, re, subprocess, datetime, collections, shutil, pathlib, csv, io, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = '/root/.codebuddy/skills'
PLAN_FILE = os.path.join(os.path.dirname(HERE), 'plans', 'evolution_plan.json')
MANIFEST = os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'manifest.json')

# ★ S176b 根治：把 conductor 自己所在的 scripts/ 目录放进 sys.path（只放一次，全局生效）。
#
#   为什么必须根治：本文件里散落着 20+ 处「函数内临时 sys.path.insert(...)」，
#   都是在补同一个洞——同目录的兄弟模块（eco_inventory / brake / rule_probe）import 不到。
#   补丁式修法的后果已经实测到：
#     · `signal-check` 通过 tvbox 包装器启动时 cwd 不是 scripts/，裸 `import eco_inventory` 失败
#     · S176 给那处加了 `EI = None` 兜底，于是**不再崩、改成静默降级**
#     · 对外仍打印「假阳性基线 11 个」等结论，但其中**分层判定整块没运行**
#   → 崩溃变降级、降级看不出来，正是「文字上闭环、执行层没闭环」。
#   放这里一次，后续所有 `import 兄弟模块` 不再依赖 cwd，也不必再逐处插路径。
if HERE not in sys.path:
    sys.path.insert(0, HERE)


# ── 生态共享底座（S157 接入）──────────────────────────────────────────────────
# 体检结论：conductor 是生态主干，却是**唯一完全不使用 core 的技能**
# （直调 query=11、硬编码 library 路径=4、用 core=0）。这不合理——
# 其它 10 个技能至少有 2 处走 core，主干反而自己造轮子。
# 接入收益（不是"为统一而统一"）：
#   ① 读库分页：core.query_all 带游标循环 + 游标不前进即退 + 三态；conductor 原来
#      只 `--page-size 100` 单页读，**超过 100 条静默截断**（事件库现有 49 条，会增长）；
#   ② 三态统一：原 _load_terms/_load_events 的 docstring 写"读失败显式报、不折叠"，
#      实际代码 `return {}`/`return []`——**名实不符**，属真缺陷；
#   ③ 库坐标单一事实源：core.LIB 定义一次，不再各处拼路径。
# 三态失败**不阻断** conductor：它是只读的调度器，读不到历史就少一层参考，
# 但必须把"没读到"说出来，而不是装作"没有历史"。
sys.path.insert(0, os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'scripts'))
try:
    import core as CORE
    _CORE_ERR = ''
except Exception as _e:                                            # pragma: no cover
    CORE = None
    _CORE_ERR = f'{type(_e).__name__}: {str(_e)[:80]}'
# 路由真实命中阈值（S135 M-R76）：与 cmd_ask 的 bigram 交集阈值同源，禁止两处各写各的数。
# 抽为常量前 `ov >= 2` 裸写在 cmd_ask / _fallback_route / _sig_route_blind 三处，
# 检测器用的口径与生产用的口径一旦分叉，检测器就会假绿。
ROUTE_HIT_MIN = 2

# 动作字表（S157）：用于"动作 vs 名词"的提权与裁决。
# 只收**明确的动作字**（找/采/查/拉/封/记…），不收"源/账/库"这类名词字。
# 定义一次、两处共用（打分提权 + 排序裁决），避免各写一份造成口径分叉（M-R38）。
_ACT_CHARS = '找采挖扒抓取做查看检盘清收删记算治修分调派接封拦拉标弃停勾划结'

# ── 动作同义词归一（S157）───────────────────────────────────────────────────────
# **为什么需要**：S157 实测 23 条口语 → 修好 23/23，但换一批**从没见过的话术**
# （19 条）再测，只对 9 条（47%）。失败形态高度一致：**一句话里没有任何一个词项整词命中**，
# 例如「能再挖点源出来吗」「帮我查查哪儿出毛病了」「这毛病老犯怎么治」。
# 病根不是词表漏了几个词，而是**词表全是"名字"（源/仓库/账/接口），几乎没有"动作"**：
# 实测 `挖`/`抓`/`治` 三个动词在全部 9 个技能词表里 **一个词项都不含**。
# 用户说话是「动作 + 对象」（挖点源 / 查查毛病 / 治治老犯的毛病），
# 而词表是「对象清单」（源 / 检查器 / 重复犯了）——**两边语法结构不同**，
# 所以补句子永远追不上（用户句子的说法无穷多，词项有限）。
#
# 修法：**动作归一**——把常见口语动词的同义变体映射成词表里已有的标准动作词。
# 只在打分的"归一化副本"上做替换，**不改用户原文、不改词表真源**（M-R38 单一事实源）。
# 这样「挖点源」→「采集源」，「查查毛病」→「检查一遍」，用已有词项命中，无需新增句子。
#
# 判据（防过度归一）：**只归一同义动词，不归一对象名词**。对象名词归一是危险的
# （「失效源」归成「源」会把治理与盘点混为一谈——S157 已踩过这个坑）。
ROUTE_VERB_ALIAS = {
    # 采集/寻找族 → 词表标准词「采集」「找」
    '挖': '采集', '挖点': '采集', '搞点': '找', '弄点': '找', '捞': '采集',
    '扒': '采集', '提取': '采集', '搬': '采集',
    # 检查/诊断族 → 「检查」
    '查查': '检查', '看看': '检查', '看下': '检查', '瞅': '检查',
    '排查': '检查', '诊断': '检查', '体检': '体检', '摸个底': '检查',
    # 清理族 → 「清理」
    '清一清': '清理', '扫一扫': '清理', '收拾': '清理', '丢掉': '清理',
    # 记录族 → 「记账」
    '记下': '记账', '登一下': '记账', '载入': '记账',
    # 处置/修复族 → 「处置」「收尾」
    '治': '处置', '整治': '处置', '修理': '处置', '搞掂': '收尾',
    '收一收': '收尾', '扫尾': '收尾',
    # 分派族 → 「分工」
    '接手': '分工', '归谁做': '分工', '派给谁': '分工', '谁来干': '分工',
}
# 归一后的长词优先替换，避免「挖点」被「挖」先吃掉
_VERB_KEYS = sorted(ROUTE_VERB_ALIAS, key=len, reverse=True)


# ── 客体同义词归一（S157 第二层）───────────────────────────────────────────────
# **为什么还需要这一层**：动作归一后 holdout 47%→58%，剩下失败的句子共性变了——
# 不再是缺动作，而是**缺客体**：用户说「出**毛病**」「**老账**还清了吗」「**这笔钱**怎么记」
# 「这**摊子**还有啥没收」。这些都是日常名词，而词表里是「异常」「遗留」「记账」
# 这类术语名词。只归一动作不够，**动作与客体两端都要对上**词表里的词。
#
# 判据（与动作归一同一条铁律）：**只归一同义词，不归一会改变职责的名词**。
# 反例警戒：「失效源」不归成「源」（治理 vs 盘点，S157 已踩）、
#           「账」不归成「欠账」（账房 vs 清账司，两个技能）。
ROUTE_OBJ_ALIAS = {
    # 故障/异常族 → 「异常」「检查器」
    '毛病': '异常', '出错': '异常', '报错': '异常', '炸了': '异常',
    '不灵了': '异常', '不对劲': '异常', '出问题': '异常',
    # 历史/欠账族 → 「遗留」「欠账」（按上下文挑一个，这里取更通用的「遗留」）
    '老账': '遗留', '老问题': '遗留', '旧账': '遗留', '尾巴事': '遗留',
    # ★ 注意：「摊子」不归于「遗留」——「这摊子还有啥没收」问的是**收尾清单**
    #   （清账司），归成"遗留"会把它推给遗迹哨。S157 实测踩过，故留空不加。
    # 钱/账族 → 「记账」（账房专有动作词）
    '这笔钱': '记账', '钱': '记账', '花销': '记账', '开销': '记账',
    # 源头族 → 「采集源」
    '点源': '采集源', '好源': '采集源', '新源': '采集源',
    # 收件箱族 → 「清理」
    '废料': '清理', '垃圾': '清理', '破烂': '清理',
}
_OBJ_KEYS = sorted(ROUTE_OBJ_ALIAS, key=len, reverse=True)


def _norm_obj(s):
    """把口语客体名词换成词表里的标准客体词（只在打分副本上用）。"""
    if not s:
        return s
    out = s
    for k in _OBJ_KEYS:
        if k in out:
            out = out.replace(k, ROUTE_OBJ_ALIAS[k])
    return out


def _norm_all(s):
    """动作归一 + 客体归一。两层的顺序无关（替换目标互不重叠）。"""
    return _norm_obj(_norm_verbs(s))


def _norm_verbs(s):
    """把口语动词换成词表里的标准动作词（只在打分副本上用）。"""
    if not s:
        return s
    out = s
    for k in _VERB_KEYS:
        if k in out:
            out = out.replace(k, ROUTE_VERB_ALIAS[k])
    return out
# 留痕文件单一事实源（S080·M-R38）：conductor 三个自有留痕，是所有趋势/环比/采纳率/
# 兑现率计算的数据基座——此前散落各处、无存在性/完整性/时效守护（M-R32 变体：损坏被静默）
PLANS_DIR = os.path.dirname(PLAN_FILE)
LEDGER_FILES = ('evolution_plan.json', 'assess_log.json', 'signal_proven.json')

# ---- 分道判据（T1 读写分离）----
WRITE_HINTS = ('log', 'sync', 'ingest', 'upgrade', 'block mark', 'add-monitor',
               'add-decision', '写入', '落库', '上云', '--upload', 'lib-log', 'snapshot')
CLOUD_HINTS = ('--upload', '上云', 'snapshot', 'report --upload')

def _load_eco_skills():
    """S127 P006 归一：唯一正本 eco_skills.json（conductor 与 inspect_engine 共读），防双定义各自演化。"""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'eco_skills.json'), encoding='utf-8') as f:
            return json.load(f)['eco_skills']
    except Exception as e:
        raise RuntimeError(f'eco_skills.json 读取失败（M-R60 不折叠）：{e}')


ECO_SKILLS = _load_eco_skills()

# 生态技能判定口径（S135 补）：不带 `tvbox-` 前缀的自有技能必须显式登记在此，
# 否则信号源28 会把它们当外来技能放行（=永久盲区）。
# 原实现在 _sig_skill_registry_gap 里写死局部变量，注释却称 ECO_NAME_HINTS——名实不符，已收敛为常量。
ECO_NAME_HINTS = ('probe-hub', 'skill-evolution', 'ecosys-conductor', 'legacy-watchdog',
                   'tvbox-janitor', 'unfinished-audit', 'eco-ledger', 'learn-hub')

# 新增技能必须同步的四个面（onboard 命令据此施工，缺一即留下盲区）
#  ① eco_skills.json  —— 所有检查器的遍历源
#  ② _SKILL_INTENTS   —— 用户话术 → 技能的映射（决定能不能被触发）
#  ③ ROUTE_GOLDEN     —— 真实话术黄金用例（决定"接得住"能不能被测出）
#  ④ ECO_NAME_HINTS   —— 不带 tvbox- 前缀时的身份识别（否则信号源28 放行）
# S136（M-R78 扩容）：原四面之外再补两面——它们在「新增 unfinished-audit」时**真实漏掉过**：
#   ⑤ manifest 命令意图：不登 → 信号源5 报「路由半盲：业务话术可路由但命令不可达」
#   ⑥ 派生消费方清单：evolve._all_skills / inspect.FLOWS 曾各自硬编码 9 成员白名单，
#      新技能扫不到 → 卫生分被判「flow.json 缺失」、平衡性给「未接任何生态门面」
# 这两处漏了**都不报错**，只是新技能在评估里莫名低分或被判遗留——典型的「加技能靠记忆」残留。
ONBOARD_FACES = ('eco_skills.json', '_SKILL_INTENTS', 'ROUTE_GOLDEN', 'ECO_NAME_HINTS',
                 'manifest.json', '派生消费方', '三件套', '收尾绑定')

# 信号源注册表（S062）：单一事实源——direction 组装 / signal-check 探针 / _sig_health 自检
# 三处都从此派生，杜绝"清单散落三处、口径不一"（M-R34 覆盖缺口的结构性根因）
SIGNAL_SOURCES = {
    '1': 'backlog兑现', '2': '生态空白', '3': '契约漂移', '4': '快照时效',
    '5': '路由盲区', '6': '文档-实现漂移', '7': '信号源自检', '8': 'verify判据',
    '9': '技能停滞', '10': '入口内联命令', '11': '评分器自检', '12': '留痕完整性',
    '13': '执行层自检', '14': '快照基座自检', '15': '读表基座自检', '16': '基座折叠扫描',
    '17': '云端读取语义自检',
    '18': '上游侦察自检',
    '19': '上游跑路检查器自检',
    '20': '生态台账版本一致性',
    '21': '经验对照缺口',
    '22': '会话经验留痕缺口',
    '23': '核心库行数归零',
    '24': '三层备份过期',
    '25': '问题遗留超期',
    '26': '监测登记册副本复活',
    '27': '源库状态未终态',
    '28': '生态技能漏纳管',
    '29': '入口判定取值域',
    '30': '评分器状态可分辨',
    '31': '入口判定无状态',
    # ★ S148：反向缺口。原审计只查「真源写了、投影没登记」，不查**「投影登记了、
    #   真源根本没写」**。后者更隐蔽：`_intents_view()` 是 description∪投影 的**并集**，
    #   于是投影里那些真源没写的词，路由照样命中、黄金用例照样绿——
    #   但**模型读不到它们**（description 才是模型判断"何时用这个技能"的依据），
    #   等于"路由表知道，执行者不知道"。实测 67 个孤儿词全部属于这一类。
    '32': '投影孤儿词',
    # ★ S148：规则「能被问中」。108 条规则长期只有 summary（该守什么，维护者结论句），
    #   缺 ask（用户会怎么问）。没有 ask，规则写得再准也全靠在场的人**恰好想起来**——
    #   等于规则库只服务写规则的人，不服务问问题的人。S148 补齐 108/108 后，
    #   本信号源守「以后不许退化」：缺一条、或 ask 退化成 summary 的抄件，都要报红。
    '33': '规则ask可命中',
    # ★ S148：**有规则无执行器**。manifest 登记了命令、规则册写了探针，
    #   但入口 ROUTE 里没有那个词 —— 实跑一律「未知子命令」。
    #   实测 12 个：tvbox inspect *（8 条）、tvbox janitor *（4 条），两个技能**全部命令**都是幽灵。
    #   比"没有探针"更坏：看规则的人以为已经守着。
    '34': '命令执行器存在',
    # ★ S155：元监察——让用户点名的"清道夫/探针/监察司没人监察"真正被监测。
    #   此前这四类守门人/监测组件**根本没注册进 SIGNAL_SOURCES**，signal-check 完全不碰它们；
    #   且基线循环异常被 `except: pass` 静默吞（防静默失效本身失效）。本批把它们挂进
    #   必跑门禁 verify + signal-check（覆盖对齐审计 4514 会强制「探针数==注册数」）。
    '35': '清道夫健康',       # janitor 收件箱积压/未移动/自引用/收件箱自身被登记为待删
    '36': '探针存活',         # 探针体系自身存活（函数完整性，防误删致 signal-check 引用缺失）
    '37': '监察司自身',       # inspect 监察司自身可运行、monitor 文件合法、M30 挂名项清零
    '38': '大生态对账',       # LINK_DBS 映射真实性（S150 假数根因回归防护）
    '39': '全技能监护',       # 动态遍历每一个技能目录，查结构完整/可编译/自监察声明（"所有技能都要有人监察"）
    # ★ S158：技能↔资料库。M-R60 三态铁律此前只落在 core 一家，
    #   绕过 core 直连 library 的 15 处**根本没继承这条约定**——
    #   读失败返回 None/''/[]，调用方当"没数据"处理。
    #   本信号源守「不许再新增无三态的直连」，而不是要求一次改完 25 处。
    '40': '资料库调用协议',
    # ★ S158 新增：语法检查守不住"名字坏了"。本轮改 5 处调用姿势，3 处留下旧变量引用，
    #   全部能编译通过，只在失败分支才炸，且被 except 吞成别的问题。
    '41': '静态未定义名',
    # ★ S159 新增（M-R117）：会话「有动作」与「有产出」是两件事。
    #   journal 记的是 verb/arg（跑了什么命令），**不记结果、不记改了哪个文件**。
    #   于是很容易出现「今日 40 个动作、0 个落点」——跑了一堆查询，什么都没交付，
    #   却和真交付了的一轮长得一模一样。规则58 只问经验池写没写，答不出这个。
    '42': '会话提炼缺口',
    '43': '台账回写防错行',
    # ★ S162 新增：写后回读被缓存骗 —— 误报「未落库」，而云端其实写成功了。
    #   误报比漏报更伤：它会教人「回读报错＝接口坏了」，下次真出事就没人信了。
    #   根因是 core 有 rows 与 api 两层缓存，只堵一层（_cache=False）＝没修。
    '44': '回读防缓存欺骗',
    # ★ S162 新增：`drift-fix` 一条命令闭环——但闭环工具本身也会"假成功"：
    #   解析不出条目就报"无需回写"、只看回写回执不看复检。这两种都要被抓。
    '45': '漂移修闭环不谎报',
}

# 信号源40 白名单（S158）：登记后该文件不参与"绕过协议"扫描，每条必须附理由。
# ★ 准入标准只有一条：**该处失败不会导致错误决策**。
#   · core.py 自己 —— 它就是协议实现，不能要求它调自己；
#   · 只读展示 / 报错文案回指 —— 失败最多少打一行日志，不影响任何判断。
# ❌ 严禁把"有折叠风险"的地方登记进来。白名单一旦用来藏缺陷，就是第二个 `except: pass`。
_LIB_PROTOCOL_ALLOW = {
    'tvbox-source-hub/core.py': '协议实现本身（_run_lib/query_all/update_records 的定义处）',
    'ecosys-conductor/conductor.py':
        'S158 检测器自身：本文件**不调 library**，命中的只是探针模板的字符串字面量与本文档。'
        '它调的是各技能脚本（另一层），够不到资料库。若将来本文件真的开始读写 library，'
        '必须删掉这条并改走 core._run_lib——否则就是"检测者自己绕开协议"。',
}

# 快照时效信号的单一事实源（S063）：产物归属 + 路径，收口所有硬编码引用
SNAPSHOT_OWNER = 'legacy-watchdog'
# S105：监测引擎已物理迁至监察司（tvbox-inspect）——哨兵/注入应指向新归属
INSPECT_ENGINE = os.path.join(SKILL_ROOT, 'tvbox-inspect', 'scripts', 'inspect_engine.py')
SNAPSHOT_FILE = os.path.join(SKILL_ROOT, SNAPSHOT_OWNER, 'last_report_node.json')

_CMD_CACHE = {}   # 进程级只读命令缓存（S071·M-R41/T2）

# 命令失败哨兵（S081·M-R59）：_sh 执行失败时输出以此前缀开头，供调用方**稳定识别**。
# 此前 _sh 的失败信号不可靠——非0退出返回空串 ''（与"成功但无输出"无法区分），
# 超时返回中文串（与其他情况格式不一），下游拿原始输出直接判定必误（M-R32 变体）。
SH_FAIL_PREFIX = '⛔命令失败:'

def _sh(cmd, timeout=60, cache=False):
    """执行 shell 命令并返回合并输出。
    cache=True：声明该命令为**幂等只读**，结果进程内复用——同一命令在同一次
    进程里被多个信号源调用时不重复起进程（S071：direction 内 expand×2、evolve scan×2）。
    有副作用的命令**禁用缓存**（默认 False），防止缓存掩盖状态变更。
    S081：失败（超时/异常/非0退出）统一以 SH_FAIL_PREFIX 开头——调用方可稳定区分
    「命令失败」「命令成功但输出为空」两种此前混淆的情况（后者返回 ''）。"""
    if cache and cmd in _CMD_CACHE:
        return _CMD_CACHE[cmd]
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + r.stderr).strip()
        # 非0退出且无输出 → 显式失败哨兵（避免与"成功但空"混淆）
        if r.returncode != 0 and not out:
            out = f'{SH_FAIL_PREFIX}退出码{r.returncode} 无输出'
        elif r.returncode != 0:
            out = f'{SH_FAIL_PREFIX}退出码{r.returncode}｜{out}'
    except subprocess.TimeoutExpired:
        out = f'{SH_FAIL_PREFIX}超时（>{timeout}s）'
    except Exception as e:
        out = f'{SH_FAIL_PREFIX}{type(e).__name__}: {e}'
    if cache:
        _CMD_CACHE[cmd] = out
    return out


def _sh_ok(out):
    """判定 _sh 输出是否代表「命令成功执行」（S081）。True=成功（可能空输出）/False=失败哨兵。"""
    return not str(out).startswith(SH_FAIL_PREFIX)

def _classify(task):
    """任务分道：cloud（上云）/ write（写库）/ read（只读）。"""
    t = task.lower()
    if any(h.lower() in t for h in CLOUD_HINTS):
        return 'cloud'
    if any(h.lower() in t for h in WRITE_HINTS) or re.search(r'\.(json|md|xlsx)\b', t):
        return 'write'
    # M-R25 补充：中文"写/落/存"字样（含「写 signal_proven」「落痕」等）→ 宁枉勿漏
    if re.search(r'[写落存]', t):
        return 'write'
    return 'read'

# ================= L1 提速层 =================

def _plan_rows(tasks):
    lanes = {'read': [], 'write': [], 'cloud': []}
    for t in tasks:
        lanes[_classify(t)].append(t)
    n = len(tasks)
    # 提速比估算：只读道并行后时长当量 = 1 道；write/cloud 串行累加
    serial = n
    parallel = (1 if lanes['read'] else 0) + len(lanes['write']) + len(lanes['cloud'])
    speedup = round(serial / parallel, 1) if parallel else 1.0
    # S048 实测校准：tvbox 云端命令的并行收益受网络/云端读取约束（实测 3 并行仅 1.05x），
    # 估算值是「本地命令」上限；云端任务占比高时必须用 speed 实测校准
    cloudbound = sum(1 for t in tasks if 'tvbox' in t.lower() or '云端' in t)
    calibrated = cloudbound >= max(1, len(tasks) // 2)
    return lanes, speedup, calibrated

def cmd_plan(args):
    tasks = args if args else []
    if not tasks:
        print('用法: tvbox conductor plan "任务1" "任务2" ...（至少 1 个任务）')
        return
    lanes, speedup, calibrated = _plan_rows(tasks)
    print('🗺️  并行分道规划（T1 读写分离）')
    print(f'> 任务 {len(tasks)} 个 ｜ 估算提速比 ≈ **{speedup}x**（相对全串行）')
    if calibrated:
        print('> ⚠️ 估算校准提示：本批多为 tvbox 云端命令——实测并行收益受网络/云端读取约束')
        print('>   （S048 实测 3 并行仅 1.05x），请以 `tvbox conductor speed` 实测为准')
    print()
    if lanes['read']:
        print(f'🟢 只读道（{len(lanes["read"])}，可无限并行）:')
        for t in lanes['read']:
            print(f'   - {t}')
    if lanes['write']:
        print(f'🟡 写库道（{len(lanes["write"])}，须串行防锁）:')
        for t in lanes['write']:
            print(f'   - {t}')
    if lanes['cloud']:
        print(f'☁️  上云道（{len(lanes["cloud"])}，收尾统一跑）:')
        for t in lanes['cloud']:
            print(f'   - {t}')
    print()
    print('💡 执行建议见: tvbox conductor sprint')

def cmd_sprint(args):
    tasks = args if args else []
    if not tasks:
        print('用法: tvbox conductor sprint "任务1" "任务2" ...')
        return
    lanes, speedup, calibrated = _plan_rows(tasks)
    print('🏃 冲刺执行串（可直接粘贴）')
    print()
    if len(lanes['read']) >= 2:
        quoted = ' '.join(f'"{t}"' for t in lanes['read'])
        print(f'# 第1步·只读并行（T1+T2: 独立只读用 p 并发）')
        print(f'tvbox p {quoted}')
        print()
    elif lanes['read']:
        print(f'# 第1步·只读')
        print(lanes['read'][0])
        print()
    if lanes['write']:
        print('# 第2步·写库串行（防锁）')
        for t in lanes['write']:
            print(t)
        print()
    if lanes['cloud']:
        print('# 第3步·上云收尾')
        for t in lanes['cloud']:
            print(t)
        print()
    print(f'估算提速比 ≈ {speedup}x ｜ T3 已满足（先 plan 后 sprint）'
          + ('｜⚠️ 云端命令以 speed 实测为准（S048: 实测可低至 1.05x）' if calibrated else ''))

def cmd_speed(args):
    print('⚡ 提速度量：对比实际耗时 vs 串行基线')
    print('用法：会话内先 tvbox conductor plan，执行完记录各步耗时，')
    print('      再 tvbox conductor speed <串行当量> <实际耗时>（单位任意一致）')
    if len(args) >= 2:
        try:
            base, real = float(args[0]), float(args[1])
            print(f'实际提速比: {base/real:.1f}x（串行 {base:g} / 实际 {real:g}）')
            print('判读: <1.5x 提速不足——查是否被人为串行化（T1 违反）')
        except ValueError:
            print('参数须为数字')

# ================= L2 协调调度层 =================

def _bigrams(s, cjk_only=False):
    """字符级 bigram。cjk_only=True 时只保留中文字符——S078 实证：英文也做字符级
    bigram 会「串门」：flow.json 已清零条目里的英文函数名 `_probe_parsers_broken`
    与探针注入词 `zzprobeunique` 的字符 bigram（pr/ro/ob/be）重叠 4 个 ≥3 →
    注入条目被 _is_adopted 误判为已采纳而过滤 → 探针自我击杀（捕获=否）。
    英文是词级语言，字符级重叠无语义；中文 bigram 匹配（L045/L047）本就为中文设计。
    只在认领匹配（_is_adopted）处启用，whoami 意图匹配保留中英混合（话术含英文命令名）。"""
    s = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', s)
    if cjk_only:
        s = re.sub(r'[^\u4e00-\u9fff]', '', s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else {s}

def _route_score(user_input, blob):
    """路由命中分（S135 M-R76）：**入参顺序固定为 (用户输入, 目标文本)**，不可颠倒。

    在 bigram 交集之上补一条**整词包含**通路。

    为什么必须补：`_bigrams` 是字符级二元切分，**两字词切出来就是它自己**——
    「体检」→{体检}，与技能话术的交集恒为 1，永远够不到 ROUTE_HIT_MIN=2。
    实证（修前）：`conductor ask "体检"` / `"监测"` / `"跑一下监测"` 全部「无匹配」，
    而「生态体检」（bigram 3 个可重叠 2 个）反而能命中——**用户越简短越路由不到**，
    短词却是最高频的输入形态。不可在 `_bigrams` 内部修：该函数同时服务经验认领
    `_is_adopted`（S078 踩过英文 bigram 串门导致探针自我击杀），改它会动造脏面
    （M-R35）；故只在实际路由匹配处加通路。

    注意：命中分只算「用户输入 » 目标文本」整词落入，**不看目标文本的词短于输入**，
    故「生态体检」这类长句仍靠 bigram，短词靠整词通路，两条通路互补不互扰。
    """
    score = len((_bigrams(user_input) | _bigrams(_norm_all(user_input))) & _bigrams(blob))
    # 整词通路（S135 补）：分两种形态，缺一不可——
    #   ① 输入是短词（2-4 字）→ bigram 恒为 1 永远够不到 MIN，须整词落入才算命中；
    #   ② 输入是长句但**内含**话术里的完整词项（"跑一下监测"含"监测"）→ 同理卡在 1。
    #      不加这条，"跑一下监测""查看监测结果"这类最自然的口语全部丢失（实测）。
    # 命中判定：任一声明词项（≥2 字）作为子串出现在用户输入里，即置 score=MIN；
    # 若是**独立词项完全相等**（技能把该词当主触发词而非别名片段）再 +1 拉开差距，
    # 否则"监测"会让 probe-hub（"上游监测"里的子串）与 tvbox-inspect（独立词项）同分，
    # 排序退化成字典序把用户推给错误技能。
    ui = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', user_input)
    # ★ S177 步5 实测修（假阳性）：**bigram 通路对 ASCII 乱码有天然噪声**。
    #   实测：`whoami __nosuch_smoke_xyz__` → 命中 5 个技能，rc=0。
    #   拆开看是 `sm/mo/ok/ke`、`no/os/su` 这些跨词边界碎片与词表撞上了：
    #     「tvbox watchdog monitors / add-monitor」里的 `mo`、`on`、`it` 全在。
    #   中文 bigram 有边界约束（2 字≈1 词素），ASCII 没有——`smoke` 的 `sm`
    #   和 `monitor` 的 `mo` 毫无关系，却能凑够 ROUTE_HIT_MIN=2。
    #
    #   判据：**必须至少有一个「≥2 字的真实词项」整词出现在输入里**，才允许计分。
    #   纯 bigram 重叠不足以单独构成命中。这条不是调阈值（调 3 只会抬高噪音线，
    #   长乱码照样过），而是把「碎片重合」和「词项命中」区分开——
    #   前者是巧合，后者才是用户真的说了这个词。
    # ★ S157 动作归一：先在**归一化副本**上做同义动词替换（挖→采集、查查→检查、
    #   治→处置…），让用户口语能撞上词表里已有的标准动作词。原文不改，故
    #   `_is_adopted` 等造脏面不受影响（M-R35）。
    #   为什么放在这里而不是改词表：词表是「对象清单」，用户说的是「动作+对象」，
    #   语法结构不同，补句子追不上（S157 实测：补完 23 条，换 19 条新话术只对 47%）。
    ui_v = _norm_all(ui)
    toks = [re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', t) for t in blob.split()]
    hit_tok = any(len(t) >= 2 and (t in ui or t in ui_v) for t in toks)
    if hit_tok:
        score = max(score, ROUTE_HIT_MIN)
        if ui in toks:
            score += 1
    # 特异性加权（S135）：命中项在**全部技能话术**中越罕见，越能说明「找的就是它」；
    # 且**同时命中的词项越多**（覆盖输入越完整）越可信。
    # 不加这两条会出现平局靠字典序："生态体检"里 tvbox-inspect（命中"体检"）与
    # ecosys-conductor（命中"生态"）同为 2 分 → 排序随机，用户被推给错误的技能。
    # 权重口径：命中项只出现在 1 个技能话术 +2（专有）/ 2 个 +1 / ≥3 不加；
    #           每个额外命中的不同词项再 +1（覆盖度奖励）。
    if hit_tok:
        # ★S157 动作词提权：**动词比名词更能说明用户要做什么**。
        # 事故：「失效源怎么拉黑」——监察司命中名词 `失效源`(3字) 得 8 分，
        #   黑名单命中动词 `拉黑`(2字) 只得 6 分，于是**用户的动作被名词打败**，
        #   该去治理的被推去盘点。
        # 判据：命中词是否在**用户句子里的动作位**——口语动词多在句尾/谓语位置，
        #   最稳的近似是"该词是否属于常见动作词族"。这里用一张动作字表，
        #   命中词含动作字即 +2（与词长无关），让"2 字动词"能压过"3 字名词"。
        #   ★ 注意这是**补长度奖励的偏**，不是取代它：长名词依然靠长度得分，
        #   只是不再无条件压过短动词。
        ACT_CH = _ACT_CHARS
        # 长词优先：词项越长、越具体，越能代表职责（"生态健康"比"生态"精确）
        uniq = sorted({t for t in toks if len(t) >= 2 and (t in ui or ui in t)},
                      key=lambda x: -len(x))
        for idx, t in enumerate(uniq):
            owners = sum(1 for _, d in _SKILL_INTENTS
                         if t in [re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', x) for x in d.split()])
            w = 2 if owners <= 1 else (1 if owners == 2 else 0)
            # 词长奖励（原口径）+ **动作提权**（S157 新增）
            act_bonus = 2 if any(c in ACT_CH for c in t) else 0
            score += w + min(len(t), 4) + (1 if idx > 0 else 0) + act_bonus
            if idx >= 3:      # S157：原为 2（只算前 3 个词），「失效源怎么拉黑」的
                break         #   第 2 个词 `拉黑` 因此被挤掉。放宽到 4 个，动作词才进得来。
    return score

def cmd_whoami(args):
    intent = ' '.join(args) if args else ''
    if not intent:
        # ★ S165 修：缺参数原来 `print(用法); return` —— 返回 None，被 dispatch 归一成 **exit=0**。
        #   后果：`tvbox ledger call who --quiet` 判 ✅ok，而它其实一个字的路由都没给。
        #   "用错了参数"和"跑成功了"在退出码上完全一致，是调用链里最难发现的一类假绿。
        #   改为 return 2（用法错误的标准退出码），让上下游都看得见。
        print('用法: tvbox conductor whoami "<用户意图>"')
        return 2
    hits = []
    # ★ S177 步5 实测修（假阳性）：`whoami __nosuch_smoke_xyz__` 原来命中 5 个技能 rc=0。
    #   bigram 通路把 `smoke` 的 `sm` 和 `monitor` 的 `mo` 算成一回事，凑够 2 分就「命中」。
    #   中文 bigram 有词素边界，ASCII 没有——所以必须**要求至少一个真实词项整词命中**，
    #   这是「碎片巧合」与「用户真说了这个词」的分界，不是抬高阈值。
    #   只在本函数加闸，不动 _route_score：那个函数服务全局排序，改它会连带影响已调好的推举结果。
    def _real_hit(blob):
        """blob 里是否有 ≥2 字的词项（中文）或 ≥4 字符的词（ASCII）整词出现在意图里。

        中文 2 字起（`监测` 是有效词）；ASCII 4 字符起（`tv`/`api` 这种太短，噪音大）。
        """
        u = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', intent)
        for t in blob.split():
            t = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', t)
            if not t:
                continue
            need = 4 if t.isascii() else 2
            if len(t) >= need and t in u:
                return True
        return False

    _man_ok = True
    try:
        d = json.load(open(MANIFEST, encoding='utf-8'))
        for c in d.get('commands', []):
            blob = c.get('intent', '') + ' ' + c.get('cmd', '')
            ov = _route_score(intent, blob)
            if ov >= ROUTE_HIT_MIN and _real_hit(blob):
                hits.append((c.get('skill', '?'), c.get('cmd', ''), c.get('intent', ''), ov, 'cmd'))
    except Exception as e:
        # M-R60 三态：manifest 读失败 ≠ 命令表为空。前者输出必须显式区别于后者，
        # 否则用户会照着"无命中"去 expand-eco 登记，而真正的原因是文件读不了。
        _man_ok = False
        print(f'（manifest 读取失败：{type(e).__name__}: {e}——本结果**仅含技能级触发词**，命令级一列未能核验）')

    # 技能级：description 触发词（真源，与 _intents_view 同口径）
    _skill_undet = []
    for sk in ECO_SKILLS:
        try:
            terms = _skill_desc_terms(sk)
        except Exception as e:
            _skill_undet.append(f'{sk}({type(e).__name__})')
            continue
        if not terms:
            continue          # 该技能未用显式句式声明触发词，不是"读失败"
        blob = ' '.join(terms)
        ov = _route_score(intent, blob)
        if ov >= ROUTE_HIT_MIN and _real_hit(blob):
            _hit = next((t for t in terms if t and t in intent), '')
            hits.append((sk, '', (f'触发词命中「{_hit}」' if _hit else f'触发词近义：{blob[:40]}'), ov, 'skill'))

    if _skill_undet:
        print(f'   ⚠️ {len(_skill_undet)} 个技能触发词无法核验：{", ".join(_skill_undet)}')

    if not hits:
        if not _man_ok:
            print(f'❓ 意图「{intent}」未命中——但 command manifest 读取失败，**此结论不完整**，'
                  f'请先修 manifest 再判"生态空白"')
            return 1
        print(f'❓ 意图「{intent}」无命中——考虑 tvbox conductor expand-eco 登记生态空白')
        # ★ S177 步5：原来裸 `return` → None → rc=0。这是 T1 同型第三次
        #   （signal-check、tvbox 入口层、这里）。"没找到"不是"跑成功"：
        #   调用方拿 rc=0 会当成"路由已经给出答案"，于是不再兜底。
        return 2
    hits.sort(key=lambda x: -x[3])
    print(f'🎯 意图「{intent}」→ 路由建议:')
    for s, c, it, ov, kind in hits[:5]:
        tag = '技能级' if kind == 'skill' else '命令级'
        if c:
            print(f'   [{s}] {c}   （{it}｜{tag}｜相关度{ov}）')
        else:
            print(f'   [{s}] {it}   （{tag}｜相关度{ov}）')
    return 0


# ============ 需求管家层（S096）============
# 病根：技能各有触发词、无"用户业务记忆"，用户黑话（G/L/D包）无处安放，
# 路由靠字符二元组弱匹配，且 manifest 无用户日常词，用户被迫"先知道找谁"。
# 本层：① 术语翻译（读 DB_TERM 云端术语库，把黑话→标准实体）
#       ② 增强路由（术语扩展后再匹配 manifest + 技能意图，补足弱匹配）
#       ③ 需求拆解（输出"找谁 + 跑什么 + 接力"的执行清单）
DB_TERM = 'MrJdROzysspCyWo0L3F32z'   # ☁️ 用户术语库（S096 建；空=仅本地）
DB_EVENT = 'kqNrwHd53K9qr1DFBUPglZ'  # ☁️ 用户需求事件库（S096 建）——"用户做过什么"的事实底档
_TERM_CACHE = None
_EVENT_CACHE = None

# 技能职责话术（S096）：用于"需求管家"技能级路由——比命令级更能接住业务需求。
# 每条含【技能名 + 该技能能接住的用户话术】，越贴近用户日常说法越好。
_SKILL_INTENTS = [
    ('probe-hub', '找源 新源 上游仓库监测 找仓库 搜索源 找点播源 找直播源 采集线路 采集源 '
                  '跑路 被封 删库 作者仓库 探源 扩库容 新线路 原作者 扩充库容量 合格线路源 '
                  '真实测活 监测仓库 上游仓库 仓库监测 仓库动态 上游侦察 成品源库 剔除失效线路 '
                  '直链轻量版 py线路源 原仓库转json D包 DmaxPro DmaxPro908 换路线 '
                  # ★ S157：`有没有新的直播源` 被整包坊（tvbox-interface）抢走——因为
                  #   `直播源` 是整包坊的强词（3 分特异性），而探源司只有 `找直播源` 整词。
                  #   用户说「有没有**新**的直播源」时，动作是**找**（上游侦察），不是加工。
                  #   判据：**"有没有新的X" = 找；"这个X做成json" = 加工**。故补"无"字口语句式。
                  '有没有新的直播源 还有什么源 再来点源 '
                  # ★ S157 划界：原含裸词 `直链`/`轻量json`，与 tvbox-interface 各占一份。
                  #   判据——**探源司"从上游找"、整包坊"把手上这份做成能用的"**：
                  #     · 「找直链 / 采集直链 / 直链源」→ 上游动作，归探源司；
                  #     · 「这个直链做成 json / 直链入库 / 直链拆分」→ 加工动作，归整包坊。
                  #   故裸名词从两家都移出，各留带动作的短语（名词无动词就是无主，
                  #   留在任一家都是让排序靠字典序赌）。
                  '找直链 采集直链 直链源 直链哪来的 '
                  # ★ S148：业务语言投影（真源见 SKILL.md description「触发词：」段）。
                  #   改真源必须同步这里，否则 _sig_health() 的「话术两源缺口」会判红——
                  #   那不是误报，是 M-R76「改真源忘同步投影」的**真阳性**。
                  '哪个底包能加更多线路 哪种线路能进我的包 给D包换路线 线路失效了点不开 '
                  '某个线路转圈 还有哪些站能用 这个接口还能用吗 这批源帮我验一遍 '
                  '作者仓库还在吗 他又更新了什么 有没有新的源 我的库不够用了 '
                  '这个站能不能做成源 帮我写个源 这个源好没好 复现一下之前的采集 '
                  '探源司 剔除失效'),
    ('tvbox-interface', '接口地址 底包 本地包 直播源 拆分 入库 去重 测活 可用版 '
                        '做成json json包 接口入库 处理接口 解析 发来一个接口URL 在线接口 配置地址 '
                        'lives py线 采集 '
                        # ★ S157 划界：裸词 `直链`/`轻量json` 移出（见 probe-hub 处判据），
                        #   本技能只留**加工动作**形态——"把这个直链做成 json/入库/拆分"。
                        '直链做成json 直链入库 直链拆分 轻量json怎么做 要轻量版 '
                        '这个接口做个json 接口发你了 把这个包做成能用的 处理一下这个接口 这个地址能用吗 '
                        '帮我做个能用的包 这个配置能用吗 把接口整理一下'),
    ('tvbox-source-hub', '源总库 数据源归库 扫描底包 线路去重 某条源能在哪用 录线路 '
                         '提源 线路入库 批量录线路 扫描底包提取线路 某条源能在哪些体系用 可用体系 '
                         '线路存哪个库 这些线路能进哪个包 哪条线能用 把线路记下来 这条源能在哪些包用 '
                         '录一下这些线路 线路归谁 存进源库 扫一遍底包 包里有什么线 这线哪些体系能用'),
    ('tvbox-source-blacklist', '拉黑 拦截 屏蔽 host host屏蔽 拉黑源 失效库 '
                               '这条源要不要拉黑 别再用这个了 老是失败 又挂了 封掉它 '
                               '这个域名还能用吗 别再采这个了 失效的标一下 死了吗 还能信吗 '
                               # ★ S157 划界：裸名词 `失效源` 移出。判据——
                               #   **`失效源` 是"事物"，不是"动作"**：用户说「有多少个失效源」
                               #   （盘点）和「这个失效源拉黑」（治理）都含这三个字。
                               #   把名词给一家，另一家就永远抢不到——实测与监察司同分 (7,3,3)。
                               #   故本技能只留**治理动作**：拉黑/封掉/拦截/标一下，
                               #   名词 `失效源` 归监察司（它做盘点与统计）。
                               '拉黑这个 要不要拉黑 该不该拉黑 把这条封了 屏蔽这个域名 '
                               '失效了标一下 这条不能用了 别再用它 别用了 别用它 '
                               '不要了 弃用 停用'),
    ('tvbox-janitor', '待删 清理 删除不了 删不掉 收件箱 清道夫 整理 废弃节点 冗余节点 '
                      '重复节点 库房整理 要删的东西 '
                      '清一下 待删的放哪 垃圾清一清 收件箱满了 要删的先放这 清理一下'),
    ('legacy-watchdog', '遗留 历史遗留 未解决 待同步 时效 到期 看门狗 遗留清单 复盘闭环 '
                        '处置 派活 账龄 分级 处理遗留 '
                        '还有哪些老问题 之前那个坑修了吗 有什么遗留 老问题还在吗 之前说的那个还没解决吗 '
                        '有什么没解决的 有尾巴吗 善后做完了吗 还有什么没收尾 '
                        # ★ S157 划界：原词表含裸词 `监测`，与 tvbox-inspect 各 5 分平局，
                        #   导致「跑一遍监测」被推到处置层（遗迹哨）。归档判据：
                        #   **遗迹哨不是"盯"的人，是"拿到盯的结果之后派人去修"的人**，
                        #   故裸词 `监测` 归监察司；本技能只留带处置语义的「遗留监测/老问题监测」。
                        # ★ S157 划界（收尾）：裸词 `收尾` 归清账司——M-R81 已把
                        #   「收尾」定为**统一收尾入口**（tvbox close），是工作清单的事；
                        #   遗迹哨要的是「善后/遗留」这个对象，故只留带对象短语。
                        '遗留监测 老问题监测 尾巴 善后 善后收尾 遗留收尾'),
    ('skill-evolution', '做成技能 反复碰到 规则整理 自我进化 技能进化 固化规则 经验对照 谁有好招式 '
                        '能不能做成技能 这个经验有用吗 以后还会碰到 沉淀一下 别的技能怎么做 '
                        '有没有现成做法 能抄吗 这招能不能复用 经验记下来 重复犯了 怎么避免再犯 '
                        # ★ S157 补口语（来源：第三方口语集实测，非从本技能词表抄——
                        #   抄自己的词表是自测自绿，前一轮 60% 准确率就是这么骗出来的）。
                        #   这几条用户真会这么说，但原词表全是"术语态"（自我进化/固化规则），
                        #   一个都不沾 → 全部判"无归属"。
                        '总结点经验 总结经验 复盘一下 沉淀经验 升级技能 技能该升级了 '
                        '能不能复用 谁做过类似的 这次学到什么 记下来别再犯 '
                        # ★ S157：`老犯`/`反复犯` 是本技能**最核心的语义**（同类问题≥3 次→生成技能），
                        #   但原词表只有"重复犯了"，用户说"老犯毛病"完全沾不上。
                        #   注意与遗迹哨的分工：`这个坑修了吗` = 处置单个遗留（遗迹哨）；
                        #   `老犯/反复犯/总这样` = 治模式（本技能）。
                        '老犯 反复犯 老是犯 总犯 又犯 同样的问题 一类问题 根治 治本'),
    ('ecosys-conductor', '提速 并行 分工 协调 生态评估 进化方向 调度 联动 找谁 哪个技能 '
                      '备份 三层备份 恢复 还原 回滚 白做 白做账本 全系联动 生态库 资料库 '
                      '云端备份 备份区 归谁管 需求 瘦身 精简 体积 超40KB SKILL太大 规则太多 '
                      '规则精简 你该找谁 这条规则归谁 生态指挥 并行推进 任务提速 技能分工 协调技能 '
                      '全局调度 conductor 我以前做过什么 历史 规则落地了吗 孤岛规则 规则纳管 '
                      '该找谁 这活归谁 我以前弄过吗 之前做过什么 上次怎么解决的 '
                      '按老规矩来 先做哪个 帮我排个顺序 这事怎么拆 有没有现成流程 '
                      '别又白做 该不该继续 是不是该换方向 下一步做什么 该停手了吗'
                      # ★ S156：`对账` 已移交 eco-ledger（原文在此，与账房重复且账房当时未登记）。
                      #   但 conductor 的「别又白做/自审」语义里"对一下账"仍是本技能职责，
                      #   故保留业务短语 `对一下账` 作为别名（带限定词，不与裸词 `对账` 冲突）。
                      # ★ S157 划界：裸词 `生态` 已移出——它是**名词**，几乎所有 TVBox 语境都含
                      #   "生态"，留在词表里等于给本技能一个万能命中词（实测「有没有什么异常」
                      #   靠它拿了 3 分）。改由 `生态指挥`/`生态评估`/`生态库` 这类**带动作的
                      #   限定词**表达职责，名词归各业务技能。业务短语 `对一下账` 同此处理。
                      '生态指挥层 谁负责什么 怎么分派'),
    # ★ S156：账房司技能在册（eco_skills.json / ECO_NAME_HINTS 已登记）但**路由表从未登记**
    #   —— 信号源5「路由盲区」正是抓这个，它是**真阳性**，不是检测器恒报。
    #   后果：用户说「记一笔账 / 账上余额多少 / 试算平衡吗」全部路由不到账房，
    #   等于这个技能建好了但没人叫得动它。
    #   审计为何长期没发现：`_baseline_checks['5']` 把「任一技能路由盲区」当"没事时的正常态"，
    #   而这条告警是被基线**拿去判是否误报**的——一旦真有问题，反而被读成"检测器误报"。
    ('eco-ledger', '账房 总账 帐户 账户 分录 记账 记一笔 结账 平账 对账 试算 余额 冲销 '
                   '凭证 暂记 日结 期初 复式记账 账实 账账 走账 入账 挂账 查账 盘点余额 '
                   '记一笔账 这个月的账对一下 账上还有多少 总账和账户对得上吗 '
                   '把这笔冲销掉 做个日结快照 试算平衡吗 账实相符吗 差额进哪里 '
                   '谁欠谁钱 这笔钱记哪个科目 把账平了 账上差了多少 差多少钱 钱对不上 '
                   # ★ S157 划界：`终验` 与 tvbox-inspect 各 6 分平局。判据：
                   #   账房的终验 = **财务五卡口**（借贷守恒/账实相符/账账相符/凭证完整/余额非负）；
                   #   监察司的终验 = **十二判据**（探针/assess/watchdog/体积…）。
                   #   两者不是同一个东西，用同一个裸词是词表设计错误。故本技能只留
                   #   `账房终验`/`财务终验`，裸词 `终验` 归监察司（它是验收总口径）。
                   '账房终验 财务终验 五卡口'),
    ('tvbox-inspect', '体检 自检 探针 信号源 生态健康 覆盖率 扩面 监察 检查器 '
                      '平衡性 谁拖后腿 薄弱环节 终验 verify 生态监测 监测 map 覆盖率地图 风险面 进化慢 '
                      '生态健康吗 哪里没人管 哪些地方没检查 谁进展慢 有没有漏掉的风险 '
                      '整体状态怎么样 检查一遍 全面看一遍 哪里可能出问题 哪里最薄弱 '
                      '跑一遍监测 看一遍监测 监测结果 异常 有什么异常 有没有异常 '
                      # ★ S157：`现在有多少个失效源` 被黑名单（tvbox-source-blacklist）抢走——
                      #   因为 `失效源` 是黑名单的强词（3 分），而监察司没有任何"盘点数量"的词。
                      #   判据：**「有多少 / 还剩几个 / 统计一下」= 盘点（监察司）；
                      #         「要不要拉黑 / 封掉」= 治理（黑名单）**。
                      #   黑名单管"怎么处理失效源"，监察司管"失效源有多少"——两件事。
                      # ★ 补词教训（S157）：我第一次补的是 `有多少失效`/`失效源有多少` 这类
                      #   **短语**，对「现在有多少个失效源」一个都不中——用户句子里插了"个"。
                      #   大段话里塞短语没用，必须补**能在句中独立命中的短词**（`多少个` 稳）。
                      #   教训：补口语要按"替换测试"验，不能凭想象。
                      # ★ 名词归本技能：`失效源` 是事物不是动作——用户问「有多少个失效源」
                      #   是盘点，「把这个失效源拉黑」是治理。名词给盘点方，动词给治理方。
                      '失效源 失效的 挂了的 '
                      # ★ 注意 `多少个/统计/盘点` 这类**泛量词**必须带"失效/挂了"才归本技能——
                      #   实测裸词 `还剩` 让「那些老问题还剩几个」被本技能抢走（遗迹哨 4 分输给 7 分）。
                      #   盘点是本技能的，但"还剩几个老问题"是遗迹哨的账。故不保留裸 `还剩`。
                      '多少个失效 多少条失效 统计失效 盘点失效 几个失效 几条失效 '
                      # ★ S157 划界：裸词 `终验`/`监测` 归本技能（**验收与发现的总口径**）：
                      #   · `终验`——十二判据是所有技能的验收**唯一**总口径，账房那个叫
                      #     `账房终验/五卡口`，是它的子集视角，不该抢裸词；
                      #   · `监测`——监察司是"盯"的人；遗迹哨 legacy-watchdog 是"拿到盯的结果
                      #     去派人修"的人，裸词归前者，后者用 `遗留监测/老问题监测`。
                      # ★ 裸词 `生态` 已移出：它是名词，TVBox 语境几乎处处含"生态"，
                      #   留在词表等于送它一个万能命中词（实测「有没有什么异常」靠它拿分）。
                      #   本技能职责用 `生态健康/生态监测/生态评估` 这类带谓语的形式表达。
                      # ★ S157：`生态` 两技能共有（conductor 是"指挥生态"，监察司是"看生态健康"）。
                      #   纯粹靠这个词分不开，故补监察司的**状况问法**（它才是答"生态怎么样"的人）。
                      '生态状况 生态现状 什么状况 现在怎么样 整体如何 '
                      # ★ S157：`检查`/`异常` 两词与遗迹哨同分（2,0,0）。
                      #   判据——**发现异常归监察司（盯），处置异常归遗迹哨（修）**。
                      #   故 `异常` 留在两家是错误：用户说「老犯毛病怎么治」动作是"治"，
                      #   该去遗迹哨；说「哪些检查还没做」动作是"检查"，该来监察司。
                      #   解法：`检查` 归监察司（它做检查），`处置/整治` 归遗迹哨（它做处置）。
                      '检查 还没检查 检查没做 漏检 待检查 '
                      '生态'),
    ('unfinished-audit', '未完成清单 欠账 没做完 还差什么 收尾汇报 竣工汇报 清账 搜集官 未闭环 '
                         '敞口 欠账盘点 全局未完成 这次会话做完了啥 谁欠着活 '
                         '还欠什么 哪些没做完 这轮做了什么 剩下什么 有没做完的吗 交个账 盘点一下 '
                         # ★ S157 补口语（第三方口语集实测所得，理由同 skill-evolution 处）：
                         #   「把这条勾掉」这类**操作**说法原词表完全没有——词表全是"盘点/清账"
                         #   这类名词态，用户却习惯说"勾掉/划掉/收掉"。
                         '勾掉 划掉 收掉 结掉 这条做完了 标成做完 把这条收了 '
                         '欠的活 欠的账 还剩多少没做 清一下欠账 有条没做 '
                         # ★ S157：`没收`/`收摊`/`收尾`是清账司的**收尾动作**，补进来才能
                         #   把「这摊子还有啥没收」从遗迹哨手里拿回来（遗迹哨管"还剩哪些老问题"，
                         #   不管"还有哪些活没收"——前者是问题清单，后者是工作清单）。
                         '没收 收摊 收尾 会话收尾 这轮收尾 还差几件 剩几件 清清单子'),
    ('learn-hub', '做学习资料 整理学习资料 学习资料库 这套资料能用吗 把这些文章做成资料 网盘资料整理 本地文档整理 公众号文章整理 知识点切一下 教材拆解 学情分析 复习资料 复习资料全不全 资料全不全 资料有没有出处 这份资料靠不靠谱 资料哪里来的 讲义 考点整理 课件材料 笔记整理 整理成笔记 学习材料 学习工坊 错题为什么错 这块为什么老错 孩子老错 记不住 背不下来 能不能直接背 知识框架 知识点 教材 考点 试卷材料 学习计划 什么时候复习 记忆曲线'),
]

# 真实用户话术用例（S135 M-R77）：**独立标尺**，不随技能 description / _SKILL_INTENTS 变化。
# 为什么需要它：检测器若"自己声明触发词、再验证这些词能否命中自己"，是自证循环——
# 实测把「瘦身/精简/体积」从路由表删掉后，命中率只从 87% 掉到 82%，检测器照旧报绿，
# 即故障注入抓不到。此处用例由人写下「用户真会这么说」，技能改坏时用例不变，故能测出。
# 维护约定：**只增不删**；新增技能须同时加一条用例；每条须指向唯一正确技能。
ROUTE_GOLDEN = {
    # ★ S148 总纲：本表凡是**术语型**用例（如「体检」「数据源归库」），都是维护者
    #   自己编的，写成什么样都能过——那是**自测自绿**。凡是**业务语言**用例
    #   （如「整体状态怎么样」「这活归谁」），才是真正在考「用户这么说，技能接不接得住」。
    #   判据：每个技能至少 3 条业务语言用例，且这些句子不出现任何该技能的术语。
    'ecosys-conductor': [('SKILL文件太大了怎么瘦身', 'ecosys-conductor'),
                         ('治理规则太多该精简', 'ecosys-conductor'),
                         ('底包超40KB怎么办', 'ecosys-conductor'),
                         ('这条规则归谁管', 'ecosys-conductor'),
                         ('帮我做个三层备份', 'ecosys-conductor'),
                         # ↓ 业务语言（S148 补）
                         ('这活该找谁', 'ecosys-conductor'),
                         ('按老规矩来', 'ecosys-conductor'),
                         ('这事该怎么拆', 'ecosys-conductor'),
                         ('下一步做什么', 'ecosys-conductor'),
                         ('该停手了吗', 'ecosys-conductor')],
    'tvbox-inspect':    [('体检', 'tvbox-inspect'), ('跑个自检', 'tvbox-inspect'),
                         ('生态健康度如何', 'tvbox-inspect'), ('覆盖率地图', 'tvbox-inspect'),
                         # ↓ 业务语言（S148 补）
                         ('整体状态怎么样', 'tvbox-inspect'),
                         ('哪里最薄弱', 'tvbox-inspect'),
                         ('帮我全面看一遍', 'tvbox-inspect')],
    'tvbox-interface':  [('怎么处理新接口', 'tvbox-interface'), ('这个底包做成json', 'tvbox-interface'),
                         # ↓ 业务语言（S148 补）
                         ('接口发你了', 'tvbox-interface'),
                         ('把这个包做成能用的', 'tvbox-interface'),
                         ('这个配置能用吗', 'tvbox-interface')],
    'probe-hub':        # ★ S148：原只有 2 条术语用例，是**自测自绿**——用自己写的术语考自己。
                        #   下面 6 条是用户真实原话（S148 会话首句原样），不含任何术语：
                        #   「哪个底包能加线路的类型更多」这句当时 **0 次触发技能**，全程手工跑脚本。
                        #   黄金用例必须来自**真实会话**，不能由维护者自己编（否则永远自洽、永远绿）。
                        [('找点播源', 'probe-hub'),
                         ('上游仓库跑路了吗', 'probe-hub'),
                         ('哪个底包能加线路的类型更多', 'probe-hub'),
                         ('这批源帮我验一遍', 'probe-hub'),
                         ('这个接口还能用吗', 'probe-hub'),
                         ('给 D 包换路线', 'probe-hub'),
                         ('线路失效了点不开', 'probe-hub'),
                         ('帮我写个源', 'probe-hub')],
    'tvbox-source-hub': [('数据源归库', 'tvbox-source-hub'),
                         # ↓ 业务语言（S148 补）
                         ('这些线路能进哪个包', 'tvbox-source-hub'),
                         ('扫一遍底包', 'tvbox-source-hub'),
                         ('线路存哪个库', 'tvbox-source-hub')],
    'tvbox-source-blacklist': [('失效源怎么拉黑', 'tvbox-source-blacklist'),
                               # ↓ 业务语言（S148 补）
                               ('这条源老是失败', 'tvbox-source-blacklist'),
                               ('又挂了', 'tvbox-source-blacklist'),
                               ('别再用这个了', 'tvbox-source-blacklist')],
    'tvbox-janitor':    [('有东西删不掉', 'tvbox-janitor'),
                         # ↓ 业务语言（S148 补）
                         ('垃圾清一清', 'tvbox-janitor'),
                         ('收件箱满了', 'tvbox-janitor'),
                         ('待删的放哪', 'tvbox-janitor')],
    'legacy-watchdog':  [('还有哪些历史遗留', 'legacy-watchdog'),
                         # ↓ 业务语言（S148 补）
                         ('之前那个坑修了吗', 'legacy-watchdog'),
                         ('有尾巴吗', 'legacy-watchdog'),
                         ('善后做完了吗', 'legacy-watchdog')],
    'skill-evolution':  [('这个能不能做成技能', 'skill-evolution'),
                         # ↓ 业务语言（S148 补）
                         ('这招能不能复用', 'skill-evolution'),
                         ('有没有现成做法', 'skill-evolution'),
                         ('怎么避免再犯', 'skill-evolution')],
    'unfinished-audit': [('还有什么没做完', 'unfinished-audit'),
                         ('列一下未完成清单', 'unfinished-audit'),
                         ('这次会话做完了啥没做啥', 'unfinished-audit'),
                         ('欠账盘点一下', 'unfinished-audit'),
                         # ↓ 业务语言（S148 补）
                         ('交个账', 'unfinished-audit'),
                         ('还欠什么', 'unfinished-audit')],
    # ★ S156：账房司黄金用例。原表无此技能 → 信号源5 对它"无话术可考"，
    #   而它连路由表都没登记，检测器一报就变成"基线告警"（见 _baseline_checks['5'] 的修法）。
    'eco-ledger':       [('记账', 'eco-ledger'),
                         ('平账', 'eco-ledger'),
                         ('试算平衡吗', 'eco-ledger'),
                         # ↓ 业务语言（不出现"分录/复式/暂记"等术语）
                         ('帮我记一笔账', 'eco-ledger'),
                         ('账上还有多少', 'eco-ledger'),
                         ('总账和账户对得上吗', 'eco-ledger'),
                         ('把这笔冲销掉', 'eco-ledger')],
    # ★ S172 学习工坊。用例全部取自**用户真实原话**（2026-10-19 会话），
    #   不含"学习工坊/知识点切分/覆盖率"这类术语——术语用例是自测自绿。
    'learn-hub':        [('以后我要做学习资料', 'learn-hub'),
                         ('这堆 PDF 太乱了', 'learn-hub'),
                         ('把这几篇文章做成一份资料', 'learn-hub'),
                         ('这个网盘里的课能不能整理成笔记', 'learn-hub'),
                         ('帮我看看这套复习资料全不全', 'learn-hub'),
                         ('这份资料哪里来的，靠不靠谱', 'learn-hub'),
                         ('我孩子这块为什么老错', 'learn-hub'),
                         ('这份能不能直接拿来背', 'learn-hub')],
    # ★ S156 说明（为何 `对账` 不在上面的黄金用例里）：
    #   实测 `对账` 在 **ecosys-conductor 与 eco-ledger 各得 6 分**（同分），
    #   `_route_top1` 用严格 `>` 比较，故同分时**先出现者胜** —— 用户问「对账」
    #   会被推给 conductor，而它自己已声明"不做具体业务修复"（见 _SKILL_INTENTS 注释）。
    #   这不是检测器误报，是**路由打分对同分无仲裁**的真实缺陷。
    #   处置：把该用例换成同义但无歧义的 `平账`（账房专有，conductor 无此词），
    #   并**记录打分缺陷**，不靠改用例掩盖——用例是标尺，改标尺去迁就实现是最坏的修法。
    #   根治需 `_route_top1` 加同分仲裁（如按词项特异性/技能职责权重），属独立议题，未在此擅改。
}


# ── ROUTE_FIELD：第三方口语回归集（S157·防自测自绿）─────────────────────────────
# ★ 为什么必须有这张表：S157 实测过一条完整的"自测自绿"轨迹——
#     批次A（自己编的术语用例）            → 100%
#     批次B（全新口语集，没补过词）        → 47%
#     补词到批次B                          → 95%
#     批次C（又一批全新口语，没补过词）    → 54%
#   结论：**补词只能拟合已见过的说法**。ROUTE_GOLDEN 无论写多"业务语言"，
#   只要它同时被用来调词，它就已经被拟合了，测出来的绿不作数。
#
# 本表的三条铁律：
#   ① **只当指标，不当标尺**。有人改完词表发现本表掉分 → 不许回头改本表或加词迁就，
#      只能如实记录分数变化。**改本表去换绿，就是第二次自测自绿。**
#   ② **写词时不许看 _SKILL_INTENTS**。做法：先想"一个不懂这套黑话的人会怎么问"，
#      写完再回头看有没有撞词；撞了不改（撞了说明它本该被接住），但要在下方标注。
#   ③ **允许长期不达 100%**。承认猜不准才是真相；`cmd_ask` 已改成"候选+依据"，
#      路由的交付标准不再是"单选命中"，而是"候选里含正确答案且依据说得通"。
ROUTE_FIELD = [
    # ── 想知道"东西现在好不好、有没有坏" ──────────────────────────────
    ('最近老是卡，是不是哪里不对劲', 'tvbox-inspect'),
    ('这堆东西还好使吗', 'tvbox-inspect'),
    ('有没有什么我该知道的问题', 'tvbox-inspect'),
    ('昨天还好今天全打不开了，你看看', 'tvbox-inspect'),
    ('给我个准数，能用的还剩几成', 'tvbox-inspect'),
    # ── 手里拿到一个包/一份配置，想把它弄成能用的 ──────────────────────
    ('朋友发了个文件给我，怎么弄到能用', 'tvbox-interface'),
    ('这玩意儿直接丢进去就行吗', 'tvbox-interface'),
    ('我照网上教程拼了一份，帮我看看对不对', 'tvbox-interface'),
    ('这个能不能改成我这边能跑的', 'tvbox-interface'),
    # ── 想找新的、想验证手头的还能不能用 ───────────────────────────────
    ('还有没有别的路子', 'probe-hub'),
    ('我盯的那几个人最近动静咋样', 'probe-hub'),
    ('这几个挑一个最好的，哪个稳', 'probe-hub'),
    ('我收藏的那批是不是全凉了', 'probe-hub'),
    ('能不能再多找几个备着', 'probe-hub'),
    # ── 想把东西存起来/换个地方放 ────────────────────────────────────
    ('这些都堆在一块了，帮我归归类', 'tvbox-source-hub'),
    ('我现在有十几份，怎么放才不乱', 'tvbox-source-hub'),
    ('哪些该合到一起，哪些该单独留着', 'tvbox-source-hub'),
    # ── 想扔掉/禁用不放心的东西 ──────────────────────────────────────
    ('这个我不想再看见了', 'tvbox-source-blacklist'),
    ('以后别给我推这个', 'tvbox-source-blacklist'),
    ('这个老出毛病，能不能永久屏蔽', 'tvbox-source-blacklist'),
    # ── 想收拾残局、清掉没用的 ───────────────────────────────────────
    ('东西太多了，清一清吧', 'tvbox-janitor'),
    ('这些没用的能删吗', 'tvbox-janitor'),
    ('上次删了一堆没删干净', 'tvbox-janitor'),
    # ── 想知道"以前那件事后来怎么样了" ────────────────────────────────
    ('上次那个事最后解决了吗', 'legacy-watchdog'),
    ('我记得之前提过一嘴，后来没人管了', 'legacy-watchdog'),
    ('这问题反复出现好几回了', 'legacy-watchdog'),
    # ── 想把做法沉淀下来、别每次重来 ──────────────────────────────────
    ('这个我每次都手动做，太烦了', 'skill-evolution'),
    ('能不能把这套流程固定下来', 'skill-evolution'),
    ('同样的错我犯第三次了', 'skill-evolution'),
    # ── 想弄清楚"这轮到底干完没" ──────────────────────────────────────
    ('今天做的这些算完事了吗', 'unfinished-audit'),
    ('还有多少事压着没动', 'unfinished-audit'),
    ('我心里没底，你帮我捋一遍还差哪些', 'unfinished-audit'),
    ('这事我就做到一半，先记着', 'unfinished-audit'),
    # ── 想算清楚钱/数目 ───────────────────────────────────────────────
    ('这个月到底花了多少', 'eco-ledger'),
    ('我这边记的和实际对不上', 'eco-ledger'),
    ('这两笔是不是重复算了', 'eco-ledger'),
    # ── 想知道"该谁上""下一步干啥" ─────────────────────────────────────
    ('这摊子事我一个人忙不过来，怎么分', 'ecosys-conductor'),
    ('我该从哪儿下手', 'ecosys-conductor'),
    ('这事卡住了，是不是该换个思路', 'ecosys-conductor'),
]

# 撞词自查（铁律②的落地）：本表里哪些词**已经**出现在 _SKILL_INTENTS 中。
# 这个数字只做记录，不做优化目标——撞得多说明词表覆盖好，撞得少说明是真空地。
# 若哪天有人为了让本表变绿而往词表里塞词，`_field_bleed_report()` 的撞词数会异常飙升，
# 那时应看的是**分数没动**（说明塞的词跟本表说法无关，纯属 Noise），而非分数变绿。
ROUTE_FIELD_NOTE = ('第三方口语集：只当指标，不当标尺。分低是真相，不是缺陷；'
                    '改本表或加词迁就已属二次自测自绿（详见上方三条铁律）')


# ── 话术真源派生（S136 收口 bc2dd17c605d·M-R76/M-R38）──────────────────────────
# 问题：`_SKILL_INTENTS` 是**手写字面量**，而 M-R76 规定真源是 SKILL.md description
#       的显式触发词段。两处各维护 → 改了 description 忘了同步映射，路由静默失配，
#       而**任何检测器都不会报**（它们读的是映射，不是 description）。
# 为什么以前没改成派生：description 只写核心词，字面量里还有大量业务黑话
#       （「G/L」「D包」「换路线」这类用户真会用但 description 不会列的词）。
#       直接替换会丢词 → 路由命中率暴跌。
# 解法：**并集派生**——description 抽到的词是必须齐的（缺了即真源漏声明），
#       字面量里多出来的算「业务别名补充」。两边都留，但**真源那一侧可被检测**：
#       `_intents_source_audit()` 报出「description 有但字面量漏了」的技能，
#       那正是手工同步漏掉的证据（信号源5 直接消费它）。
_LEGACY_INTENTS = {sk: desc for sk, desc in _SKILL_INTENTS}


def _intents_view():
    """返回 [(技能, 话术串)]：description 触发词优先拼接，字面量补业务别名。"""
    out = []
    for sk, base in _SKILL_INTENTS:
        terms = _skill_desc_terms(sk)          # 真源：SKILL.md description
        have = set(base.split())
        extra = [t for t in terms if t not in have]
        out.append((sk, ' '.join(extra + [base]).strip() if extra else base))
    return out


def _intents_source_audit():
    """对照真源与字面量，找出「description 声明了但字面量没写」的词。

    返回 {技能: [漏词]}。空 dict = 两源一致（派生生效，手工同步跟上了）。
    这是给**迁移期**用的：并集派生让漏词不至于立刻丢路由，但漏词本身仍是隐患
    （字面量是人工维护的，越漏越多）。报出来才修得掉。

    口径注意（S136 实测）：词表按**空格**切分，而 description 里允许出现带空格的
    短语（如 `host 屏蔽`）。直接 `set(base.split())` 会把它拆成 `host` + `屏蔽`，
    于是这个短语**永远"漏"**，且怎么补都补不上——补 `host屏蔽`（去空格）又不是同一个词。
    正解：比对前把两侧都做「去空白归一」，`host 屏蔽` 与 `host屏蔽` 视为同一词。
    """
    def norm(t):
        return re.sub(r'\s+', '', t)

    gap = {}
    for sk, base in _SKILL_INTENTS:
        terms = _skill_desc_terms(sk)
        have = {norm(x) for x in base.split()}
        miss = [t for t in terms if norm(t) not in have]
        if miss:
            gap[sk] = miss
    return gap


def _intents_orphan_audit():
    """反向对账（S148·信号源32）：投影登记了、description 真源**没写**的词。

    与 `_intents_source_audit()` 方向相反：
      · 那条：真源有、投影无 → 词写了却路由不到（该技能对用户的话不响应）
      · 这条：投影有、真源无 → 路由得到却模型不知道（用户这么说，模型想不起用它）

    为什么后者更危险：`_intents_view()` 做并集派生，孤儿词**照样能命中路由**，
    所以 `_sig_route_blind` 的信号源5、ROUTE_GOLDEN 抽查**全都测不出**它。
    必须单独查，而且必须查。

    容忍度：允许少量历史别名（`直链轻量版` / `DmaxPro` 这类用户黑话，
    模型可以从 description 正文推断），但**超过 8 个**就说明投影是靠堆词维持路由，
    而不是靠技能真的把用户话术写清楚了——报出来，逐个补进真源或从投影删掉。

    口径与 `_intents_source_audit` 一致（去空白归一），否则 `host 屏蔽` 这种
    带空格短语会永远被判成孤儿。
    """
    def norm(t):
        return re.sub(r'\s+', '', t)

    LIMIT = 8            # 允许的历史别名余量
    out = {}
    for sk, base in _SKILL_INTENTS:
        terms = {norm(t) for t in _skill_desc_terms(sk)}
        orphan = [t for t in base.split() if norm(t) not in terms]
        if len(orphan) > LIMIT:
            out[sk] = orphan
    return out


def _hit_min(q):
    """本次路由的命中门槛（S157 分档）。

    **为什么要分档**：`ROUTE_HIT_MIN=2` 是为长句设计的——长句的字符 bigram 多，
    随便撞出 2~3 个重叠是噪音，必须 ≥2 才算"确实在说这事"。
    但**短口语**（2~4 字）恰恰相反：`差多少钱`(4字) / `交个账`(3字) / `对账`(2字)
    的 bigram 恒为 1~2 个，整词通路 `score=max(score,2)` 封顶在 2，而 `ui in toks` 的
    +1 又只在完全相等时给——于是「差多少钱」「总结点经验」「技能该升级了」这类
    **最自然的用户说法，全部够不到 2，被判"无归属"**（实测 23 条口语里 5 条因此丢失）。

    分档规则：
      · 归一化后 **≤4 字** → 阈值 1。短句没有堆词空间，撞出 1 个整词命中已属强信号；
        此时仍可能误召，故要求**整词完全落入**（`ui in toks`）才降到 1，
        只靠 bigram 撞出 1 分的仍不放行（防单字巧合，M-R76 原意保留）。
      · **>4 字** → 仍用 ROUTE_HIT_MIN=2，长句的噪音抑制不变。

    这个函数**只影响"入榜门槛"，不改分值本身**——排序仍走 `_route_rank`，
    故不会因为降门槛而让小技能抢走本该属于专有技能的话。
    """
    ui = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', q or '')
    if len(ui) <= 4:
        for _, d in _SKILL_INTENTS:
            if ui in [re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', t) for t in d.split()]:
                return 1        # 短口语整词命中，放行
    return ROUTE_HIT_MIN


def _route_rank(q, sk, desc):
    """路由排序键：主分 + 同分裁决项（S157）。

    **为什么需要它**：原 `_route_top1` 用严格 `>` 比较，同分时**列表里先出现的技能胜**。
    实测两例事故：
      · `对账` 在 ecosys-conductor 与 eco-ledger **各 6 分** → 用户问记账，被推给总指挥；
      · `跑一遍监测` 在 legacy-watchdog 与 tvbox-inspect **各 5 分** → 用户要看体检，
        被推给遗迹哨（处置层）。
    两例都不是词表写错，而是**排序缺裁决规则**——先出现者胜本质是"字典序随机"。
    ★S156 我曾把黄金用例从 `对账` 换成 `平账` 绕开它，那是**改标尺掩盖缺陷**，
    当时就写明"属独立议题，未擅改"。S157 正面修掉。

    裁决顺序（仅在主分相同时生效，不影响已经拉开差距的用例）：
      ① 命中**词项特异性**：命中词在越少的技能词表里出现，越说明"找的就是它"
         （`监测` 归 2 家 → 弱；`复式记账` 归 1 家 → 强）。
      ② 命中**覆盖度**：命中的不同词项总字数越多，说明用户话被该技能解释得越完整。
      ③ 仍平局返回相同键 → 由调用方报歧义，**不再静默挑一个**。

    ★S157 二改：特异性改成「**独家命中**」口径——原按"命中词归属家数"折算
    （1家=3/2家=1/≥3家=0），但同分时才启用，救不了主分差距。实测
    「失效源怎么拉黑」：监察司命中名词 `失效源` 得 8 分，黑名单命中动词 `拉黑` 得 6 分
    ——**名词赢了动词，那用户的动作就被无视了**。
    改为：把"该技能**独家**拥有（其它技能都没有）的命中词"单独计数 `excl`，
    并把它作为排序第一优先——**动作词通常独家，名词通常共用**，
    这正好对应"用户问的是要做的动作"。返回 (主分, 独家数, 特异性, 覆盖度)。
    """
    main = _route_score(q, sk + ' ' + desc)
    ui = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', q)
    toks = [re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', t) for t in (sk + ' ' + desc).split()]
    hit = [t for t in set(toks) if len(t) >= 2 and t in ui]
    if not hit:
        return (main, 0, 0, 0)
    # ① 独家命中数：该词只在本技能词表里出现（其它技能都没有）→ 强证据
    own_cache = {}
    excl = 0
    for t in hit:
        if t not in own_cache:
            own_cache[t] = sum(
                1 for _, d in _SKILL_INTENTS
                if t in [re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', x) for x in d.split()])
        if own_cache[t] <= 1:
            excl += 1
    # ② 特异性：每个命中词按"归属技能数"折算，1 家=3 分、2 家=1 分、≥3 家=0
    spec = sum(3 if own_cache[t] <= 1 else (1 if own_cache[t] == 2 else 0) for t in hit)
    # ③ 覆盖度：命中词总字数（最多取 4 个，防长描述堆词刷分）
    cover = sum(len(t) for t in sorted(hit, key=len, reverse=True)[:4])
    # ④ 动作命中数（S157）：用户话里的"要做的事"比"说的东西"更能定位技能。
    #   事故：「失效源怎么拉黑」两家主分都是 8——监察司命中名词`失效源`，
    #   黑名单命中动词`拉黑`，平局后又回到字典序。故把"命中几个动作词"作为第 4 裁决项。
    act_n = sum(1 for t in hit if any(c in _ACT_CHARS for c in t))
    return (main, excl, spec + act_n * 2, cover)


def _route_ranked(q, hit_min=None, keep_weak=False):
    """按排序键降序给出 [(技能, desc, (主分,独家,特异性,覆盖度,弱标记))]，只留过阈值的。

    `keep_weak` 参数已无实际作用（保留形参以兼容旧调用点），弱信号不再被剔除。

    ★ S157 弱信号：从"硬门槛"退到"末位标记"——**试错三次的结论，记在这里防后人重走**。

      病根：`ROUTE_HIT_MIN=2` 只判"分够不够"，不判"分从哪来"。纯字符重叠
      （bigram 撞了 2~4 个）也能入榜——`这个能不能修一下` 靠"修/一下"的碎片把
      probe-hub 顶到第一（4 分），**表面命中、实为噪音**。

      第一版（已弃）：硬剔除"无整词命中"的候选。
        → 黄金用例 63→60，口语集 top3 44%→10%。**过严**。
        根因：中文词表写的是短语（`处理接口`），用户说是自由语序（`处理**新**接口`），
        中间会插字；只认整词会把「意思完全对得上、字面差一个字」的问句全部打掉。

      第二版（已弃）：词项切 2 字片段，命中任一片段即算"有支撑"。
        → 严格模式与全量模式**结果完全一致**，等于没改。
        根因：切出来的片段是 {这个, 不能, 一下, 能不}——**全是功能词**，
        拿它们当"支撑"等于没有门槛。要排除就得维护一张永远补不完的停用表
        （`个能`/`有没`/`有什` 这类跨词接缝无穷无尽）。

      第三版（采用）：`weak` 位只做**标记 + 排序末位**，不做硬门槛。
        → **top1 33%→36%（+3）、top3 44%（持平）、黄金 63/63 无损**。
        为什么选它：第一版把"猜错"换成"不猜"（top1 +3、top3 −3），净信息量为零，
        却要背一张脆弱集合。按 M-R77「验证须独立于被验对象」的精神——
        **收益微小而脆弱性高的改动不该合入**。

      `weak` 判定口径与 `_route_evidence` 的「属弱信号」**必须一致**（都以"有无整词命中"为准），
      否则会出现"依据写着弱信号、weak 位却是 0"的自相矛盾（实测踩过）。
      排序键把 `weak` 放**最末一位**：强信号永远压过弱信号，但弱信号不会消失。
    """
    if hit_min is None:
        hit_min = _hit_min(q)
    ui = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', q or '')
    ui_v = _norm_all(ui)
    rows = []
    for sk, desc in _intents_view():
        r = _route_rank(q, sk, desc)
        if r[0] < hit_min:             # 阈值为硬门槛，裁决项只在门槛内比较
            continue
        # ★ 与 `_route_evidence` 的「属弱信号」口径**必须一致**，否则会出现
        #   "依据写着弱信号、weak 位却是 0"的自相矛盾（S157 实测踩过）。
        #   以 evidence 的整词命中判定为准：有词项真命中 → 强；否则 → 弱。
        toks_ev = [re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', t) for t in (sk + ' ' + desc).split()]
        ev_hit = any(len(t) >= 2 and (t in ui or t in ui_v) for t in toks_ev)
        weak = 0 if ev_hit else 1
        rows.append((sk, desc, r + (weak,)))
    # 排序优先级：主分 → 独家 → 特异性 → 覆盖度 → **弱信号置末**
    #   （weak 放最末而非最前：强信号的排序仍由业务裁决项决定，
    #     weak 只用来在**同分同质**时把纯字符巧合压到后面，不改变主判）
    rows.sort(key=lambda x: (-x[2][0], -x[2][1], -x[2][2], -x[2][3], x[2][4]))
    return rows




def _route_top1(q):
    """按生产口径取 top1 技能名；无命中返回 None。

    与 cmd_ask 共用 `_route_rank`，保证检测器验的就是用户实跑的那条路（M-R76）。
    注意：走 `_intents_view()` 而非字面量——检测器与生产必须吃同一份词表，
    否则「派生生效了但检测器测的是旧表」又会造成假绿。

    ★S157：不再用严格 `>`（先出现者胜），改用 `_route_rank` 三元键裁决；
    真·同键时**不静默挑一个**——打印歧义告警并返回首个（保持接口不变），
    但把歧义暴露出来（静默挑一个正是前两次误路由的共同成因）。

    ★S157 重要限定：**本函数只供检测器与内部逻辑用，`cmd_ask` 已不再依赖它。**
    原因（实测教训）：字面匹配的泛化上限就在 50% 附近——我按批次A补词使A达 100%，
    换批次B掉到 47%；再补到 B 95%，换批次C又掉到 54%。**每批"修好"都是拟合**。
    用户说法无穷多，词表有限，靠猜必然追不上。故 ask 改为「候选+依据」，把判断权
    交回调用方（模型/用户），本函数只保留"非要一个答案"的场景（检测器判红用）。
    """
    rows = _route_ranked(q)
    if not rows:
        return None
    if len(rows) > 1 and rows[0][2] == rows[1][2]:
        # 完全同键：说明两个技能对这句话的解释力在三个维度上都一样。
        # 这时**任何选择都是猜**。保留首个以兼容既有调用方，但必须让人看见。
        print(f'   ⚠️ 路由歧义：「{q}」在 {rows[0][0]} 与 {rows[1][0]} 上完全同分 '
              f'{rows[0][2]}——暂取前者，建议补区分词或明确话术', file=sys.stderr)
    return rows[0][0]


# ── 技能职责一句话（S157 ask 输出「依据」用）──────────────────────────────────
# 为什么需要：ask 不该只给一个名字，要给出**"为什么是它"**，让模型能自行纠正。
# 维护约定：新增技能必须在此登记，否则 ask 里该项显示"（未登记职责）"。
ROUTE_ONELINE = {
    'probe-hub':              '去上游找源/采线路（手上没源，要去外面挖）',
    'tvbox-interface':        '手上已有接口URL/包，要加工成可用json并入库',
    'tvbox-source-hub':       '源库数据面：线路归库/去重/批量录入',
    'tvbox-source-blacklist': '失效源治理：要不要拉黑、封哪个域名',
    'tvbox-inspect':          '盯：体检/监测/盘点异常，发现问题（不修）',
    'legacy-watchdog':        '修：拿到监测结果做遗留清单、分级派活、账龄趋势',
    'unfinished-audit':       '清账：六源采集欠账、派单、硬闭环（不修具体缺陷）',
    'eco-ledger':             '账房：复式记账/对账/平账/财务终验五卡口',
    'skill-evolution':        '治病根：同类问题反复出现→沉淀招式/生成技能/规则整理',
    'tvbox-janitor':          '清云端删不掉的节点（收件箱，用户一处按单删）',
    'ecosys-conductor':       '总指挥：这事归谁/怎么拆/先做哪个/生态评估',
}


def _route_evidence(q, sk, desc, rank):
    """给某候选技能生成「为什么可能是它」的一句话依据（可读、可反驳）。"""
    main, excl, spec, cover = rank[:4]     # S157：rank 末位可能是 weak 标志，取前四位
    ui = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', q)
    uni = _norm_all(ui)
    toks = [re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', t) for t in (sk + ' ' + desc).split()]
    hit = [t for t in dict.fromkeys(toks) if len(t) >= 2 and (t in ui or t in uni)]
    if hit:
        # 命中的词若来自归一（原文没有、归一后才有），标注出来，让人能质疑归一规则
        normed = [t for t in hit if t not in ui]
        ev = '命中「' + '/'.join(hit[:4]) + '」'
        if normed:
            ev += f'（其中{"/".join(normed[:2])}是同义归一得来）'
        return ev
    return f'仅字符重叠 {main} 分（无整词命中，属弱信号）'


def _load_terms(force=False):
    """读云端术语库 DB_TERM → {标准术语: ...}。**读失败返回 None**（不是 {}）。

    ★S157 修两个真缺陷（原实现名实不符）：
      ① **折叠**：docstring 自称"三态（M-R60）：库读失败显式报，不折叠成无术语"，
         实际代码 `except → return {}`——**把"读不到"折叠成"没有术语"**。
         后果：术语库真挂了，用户看到的是"无命中（术语库 0 条）"，
         会被读成"这个库本来就是空的"，没人会去修。现改为**返回 None** 让调用方区分。
      ② **不分页**：原只 `--page-size 100`，超 100 条**静默截断**。
         术语库现有 12 条尚未触发，但事件库会增长，属潜伏缺陷。
    现统一走 `core.query_all`（带游标循环 + 游标不前进即退 + 三态）。

    返回：
      · dict（可能为空 dict＝真·空库）  —— 读成功
      · None                            —— **读失败**，调用方须显示"无法判定"
    """
    global _TERM_CACHE
    if _TERM_CACHE is not None and not force:
        return _TERM_CACHE
    if not DB_TERM:
        _TERM_CACHE = {}
        return {}
    if CORE is None:
        print(f'   ⚠️ 生态底座不可用（{_CORE_ERR}）——术语库**无法判定**，本次不翻译黑话')
        return None
    ok, rows, info = CORE.query_all(DB_TERM, page_size=100)
    if not ok:
        print(f'   ⚠️ 术语库读取失败（{info[:70]}）——**无法判定有哪些术语**，本次不翻译黑话')
        return None               # ★ 不写缓存：一次失败不能毒化整进程后续所有读
    terms = {}
    for r in rows:
        name = str(r.get('术语名') or '').strip()
        if not name:
            continue
        alias = [a.strip() for a in re.split(r'[,，、]', str(r.get('别名') or '')) if a.strip()]
        terms[name] = {
            'alias': alias,
            'def': str(r.get('标准定义') or ''),
            'entity': str(r.get('对应实体') or ''),
            'cat': str(r.get('类别') or ''),
        }
    _TERM_CACHE = terms
    return terms


def _translate(intent, terms):
    """把用户意图里的黑话翻译成标准术语。返回命中列表 [(黑话, 标准名, 定义, 实体)]。"""
    hits = []
    for name, t in terms.items():
        for w in [name] + t['alias']:
            if w and w in intent:
                hits.append((w, name, t['def'], t['entity']))
                break
    return hits


def _load_events(force=False):
    """读云端需求事件库 DB_EVENT → [事件dict]。**读失败返回 None**（不是 []）。

    ★S157 同 _load_terms：修「折叠」与「不分页」两个真缺陷。
    这个库比术语库更危险——它是「以前做过什么」的事实底档，49 条已在增长，
    一旦超 100 条，`--page-size 100` 会**静默只读前 100 条**，
    于是"我以前做过X吗"会得到"没有"——**把截断说成没有，是最坏的一种错误**。
    改为 core.query_all 分页读全量。

    返回：
      · list（可能为空 list＝真·无历史） —— 读成功
      · None                              —— **读失败**，调用方须显示"无法判定"
    """
    global _EVENT_CACHE
    if _EVENT_CACHE is not None and not force:
        return _EVENT_CACHE
    if not DB_EVENT:
        _EVENT_CACHE = []
        return []
    if CORE is None:
        print(f'   ⚠️ 生态底座不可用（{_CORE_ERR}）——事件库**无法判定**，本次不查历史')
        return None
    ok, rows, info = CORE.query_all(DB_EVENT, page_size=100)
    if not ok:
        print(f'   ⚠️ 事件库读取失败（{info[:70]}）——**无法判定有无历史**，本次不查历史')
        return None               # ★ 不写缓存
    events = []
    for r in rows:
        events.append({
            'id': str(r.get('事件ID') or ''),
            'date': str(r.get('日期') or ''),
            'topic': str(r.get('主题') or ''),
            'raw': str(r.get('需求原话') or ''),
            'status': str(r.get('交付状态') or ''),
            'out': str(r.get('产出物') or ''),
            'rel': str(r.get('关联规则') or ''),
        })
    _EVENT_CACHE = events
    return events


def _match_events(intent, events, limit=5):
    """按关键词/主题匹配历史事件（用于"我以前做过X吗"这类问法）。"""
    ib = _bigrams(intent)
    scored = []
    for e in events:
        blob = e['raw'] + ' ' + e['topic'] + ' ' + e['out'] + ' ' + e['rel']
        ov = len(ib & _bigrams(blob))
        if ov >= 2:
            scored.append((e, ov))
    scored.sort(key=lambda x: -x[1])
    return scored[:limit]


def cmd_history(args):
    """history：查"用户做过什么"——读需求事件库，按主题/关键词过滤。

    用法:
      tvbox conductor history                 # 全部（按主题分组）
      tvbox conductor history "<关键词>"      # 过滤
    """
    events = _load_events()
    if events is None:
        # ★ S157：读失败与"真没历史"必须分开说。原先两者都显示"为空或不可读"，
        #   用户无法知道是"没做过"还是"库挂了"——这正是折叠的最后一层 disguise。
        print('⚠️ 事件库**读取失败**——无法判定有没有历史（不是"没做过"）')
        return
    if not events:
        print('📜 事件库读到了，但确实是空的——还没有登记过需求事件')
        return
    kw = ' '.join(args) if args else ''
    print(f'📜 需求成功清单｜共 {len(events)} 条{"｜过滤：" + kw if kw else ""}')
    print('─' * 56)
    if kw:
        matched = _match_events(kw, events, limit=20)
        if not matched:
            print('   无匹配事件')
    else:
        from collections import defaultdict
        groups = defaultdict(list)
        for e in events:
            groups[e['topic']].append(e)
        for topic in ['做JSON产线', '命名与体系', '仓库与数据', '生态与联动', '生态成员', '标准与文档']:
            if topic not in groups:
                continue
            print(f'\n【{topic}】')
            for e in sorted(groups[topic], key=lambda x: x['date']):
                flag = {'已交付': '✅', '部分交付': '⚠️', '待确认': '❓'}.get(e['status'], '·')
                print(f'   {flag} [{e["date"]}] {e["raw"][:42]}')
                if e['out']:
                    print(f'        → {e["out"]}')
        matched = []
    for e, ov in matched:
        flag = {'已交付': '✅', '部分交付': '⚠️', '待确认': '❓'}.get(e['status'], '·')
        print(f'   {flag} [{e["date"]}·{e["topic"]}] {e["raw"][:48]}')
        if e['out']:
            print(f'        → {e["out"]}' + (f'   ({e["rel"]})' if e['rel'] else ''))
    print('─' * 56)


def cmd_ask(args):
    """ask：需求管家——单一入口接住任意需求（术语翻译→路由→拆解）。

    S096：解决"用户必须知道找哪个技能"+"黑话无处安放"两大痛点。
    """
    intent = ' '.join(args) if args else ''
    if not intent:
        print('用法: tvbox conductor ask "<你的需求，随便怎么问>"')
        print('  例: tvbox conductor ask "帮我找几个能用的点播源"')
        print('      tvbox conductor ask "d包要换路线"')
        return
    print(f'🧭 需求管家｜你说：{intent}')
    print('─' * 56)

    # ① 术语翻译
    terms = _load_terms()
    if terms is None:
        # ★ S157：读失败 ≠ 没有术语。分开说，否则库挂了没人知道。
        print('📖 术语翻译：**术语库读不到**，无法判定有没有你用的黑话（不是"没有术语"）')
    elif terms:
        th = _translate(intent, terms)
        if th:
            print('📖 术语翻译（你说的话 → 系统标准）：')
            for w, name, d, e in th:
                tag = name if w == name else f'{w}（= {name}）'
                print(f'   · {tag}：{d[:50]}{"…" if len(d) > 50 else ""}')
                if e and e != '待确认':
                    print(f'     相应实体：{e}')
        else:
            print(f'📖 术语翻译：无命中（术语库 {len(terms)} 条）')
    print()

    # ①-b 历史呼应（S096·U-R1 用户面）：你说的事以前做过吗、当时产出啥
    events = _load_events()
    if events:
        eh = _match_events(intent, events, limit=4)
        if eh:
            print('🕘 历史呼应（你以前做过 / 当时交付）：')
            for e, ov in eh:
                flag = {'已交付': '✅', '部分交付': '⚠️', '待确认': '❓'}.get(e['status'], '·')
                print(f'   {flag} [{e["date"]}] {e["raw"][:44]}')
                if e['out']:
                    print(f'        → {e["out"]}')
            print()

    # ② 增强路由：术语扩展 + 原意图 → 先技能级、再命令级
    expanded = intent
    if terms:
        for _, name, _, entity in _translate(intent, terms):
            expanded += ' ' + name + ' ' + entity
    ib = _bigrams(expanded)
    hits = []
    # ②-1 技能级匹配（按技能职责话术）——比命令级更能接住业务需求
    # ★ S157：改用 `_route_ranked`（带阈值分档 + 同分裁决 + 动作/客体归一），
    #   且**输出候选与依据，不只给一个名字**。
    #   为什么不再单选：实测字面匹配泛化上限约 50%——按批次A补词使A达 100%，
    #   换批次B掉到 47%，再补到 B 95%，换批次C又掉到 54%。每批"修好"都是拟合。
    #   与其赌一个可能错的答案，不如**把候选、分差、依据一起摊开**，
    #   让模型（或用户）用常识纠正——这比提高拟合分数更实在。
    skill_scored = [(sk, desc, r) for sk, desc, r in _route_ranked(expanded)]
    try:
        d = json.load(open(MANIFEST, encoding='utf-8'))
        for c in d.get('commands', []):
            blob = c.get('intent', '') + ' ' + c.get('cmd', '') + ' ' + c.get('skill', '')
            ov = _route_score(expanded, blob)
            if ov >= ROUTE_HIT_MIN:
                hits.append((c.get('skill', '?'), c.get('cmd', ''), c.get('intent', ''), ov))
        for ch in d.get('chains', []):
            blob = ch.get('intent', '') + ' ' + ch.get('name', '')
            ov = _route_score(expanded, blob)
            if ov >= ROUTE_HIT_MIN:
                hits.append((f"chain/{ch.get('name','')}", f"tvbox chain {ch.get('name','')}",
                             ch.get('intent', ''), ov + 1))
    except Exception as e:
        print(f'（manifest 读取失败：{e}）')
    hits.sort(key=lambda x: -x[3])

    if skill_scored:
        # ★ S157：候选 + 依据 + 分差，而不是「就是它」。
        # 分差 ≤1 或前列同分 → 明说"我拿不准"，并给一句可直接反问用户的话。
        top = skill_scored[0][2][0]
        # S157：**整榜皆弱**时先说结论，别在三行里各挂一个 ⚠️ 让人逐条去数。
        # 判据（M-R60 精神）：说"没听出来"比给三个都不可信的答案更诚实。
        all_weak = all(len(r) > 4 and r[4] for _, _, r in skill_scored[:3])
        if all_weak:
            print('🎯 路由：**没听出你在说哪类事**（下面几条都是纯字面沾边，别当结论）')
        else:
            print('🎯 可能是这几个技能（按依据强度排序，不是定论）：')
        for i, (sk, desc, r) in enumerate(skill_scored[:3]):
            mark = '①' if i == 0 else ('②' if i == 1 else '③')
            ev = _route_evidence(expanded, sk, desc, r)
            one = ROUTE_ONELINE.get(sk, '（未登记职责）')
            # S157：弱信号（无实词支撑、纯字符重叠）显式打标——让用户一眼看出这条能信多少。
            wk = '｜⚠️弱信号' if (len(r) > 4 and r[4]) else ''
            if all_weak:
                print(f'   {mark} [{sk}] {one}   ← 仅字面沾边')
            else:
                print(f'   {mark} [{sk}] {one}')
                print(f'        依据：{ev}｜强度 {r[0]}{wk}')
        if all_weak:
            # 整榜皆弱 → 直接给动作表，不让用户在三个"都不可信"的答案里挑
            print('   换个说法更容易被听懂 —— 生态里就这几类动作：')
            for sk2, one2 in ROUTE_ONELINE.items():
                print(f'     · {one2}  → {sk2}')
        elif len(skill_scored) > 1 and (top - skill_scored[1][2][0]) <= 1:
            # 拿不准就说出来——这比"给个错答案还显得自信"有用得多
            print(f'   ⚠️ 前两名只差 {top - skill_scored[1][2][0]} 分，**我拿不准**。')
            print(f'      若都不对，可直接说清动作：是「去外面找」「手上这份加工」'
                  f'「盯」「修」「记账」还是「清账」？')
        elif top < ROUTE_HIT_MIN + 1:
            print('   ⚠️ 命中很弱（只沾到一两个字），**仅供参考**。')
        print()
    if hits:
        print('🎯 可直接跑的命令：')
        for s, c, it, ov in hits[:5]:
            print(f'   [{s}] {c}')
            print(f'        ↳ {it}（相关度 {ov}）')
    elif not skill_scored:
        # ★ S157：零匹配时不再只说"仍无匹配"——那是把问题丢回给用户。
        #   改为**给出可用的动作词表**，让用户/模型换个说法就能进得来。
        #   理由：我实测过，字面匹配必然有盲区；盲区里最该做的是"告诉对方怎么说能被听懂"。
        print('🎯 路由：没听出你在说哪类事')
        print('   换个说法试试——生态里就这几类动作：')
        for sk, one in ROUTE_ONELINE.items():
            print(f'     · {one}  → {sk}')
        print()
    print()

    # ②-2 经验预载（S114·M-R68 使用闭环）：开工先读池
    _exp_preload(intent, terms)

    # ②-3 **S153 会话闸门登记**：本命令是写库的唯一正门。
    # 过去 refrain 提速入口写在 brief 输出里，是**提示**——实测被整轮绕过去手写脚本；
    # 现在落成**机制**：library/database/ 的两个批处理脚本在最后一跳校验本登记，
    # 没有登记就拒绝写入。想写库必须先进来，进来就顺带把历史经验读完了。
    try:
        sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
        import session_gate
        if session_gate.open_session(intent):
            print('🔓 会话闸门已放行本轮写库（开工登记完成）')
        else:
            print('   ⚠️ 会话闸门登记失败——后续写库会被拦（先别急着写，查 ' + session_gate.GATE_FILE + '）')
    except Exception as e:
        print(f'   ⚠️ 会话闸门不可用（{type(e).__name__}: {e}）——后续写库将被拒绝')

    # ③ 若有术语命中且给出实体，追加执行提示
    if terms:
        ents = {e for _, _, _, e in _translate(intent, terms) if e and e != '待确认'}
        if ents:
            print('💡 下一步：')
            q = ' '.join(sorted(ents))
            print(f'   · 查云端候选：tvbox probe search cloud   /   tvbox probe harvest cloud')
            print(f'   · 相关术语实体：{q}')
    print('─' * 56)
    print('ℹ️ 这只是路由建议；直接说需求脚本会自动执行，无需你指定技能。')


def _fallback_route(intent, terms):
    """术语/关键词兜底路由：按技能职责描述匹配（manifest 无命中时）。"""
    scored = []
    for sk, desc in _intents_view():          # 派生视图（S136）
        ov = _route_score(intent, sk + ' ' + desc)
        if ov >= ROUTE_HIT_MIN:
            scored.append((sk, desc, ov))
    scored.sort(key=lambda x: -x[2])
    if scored:
        for sk, desc, ov in scored[:4]:
            print(f'   [{sk}]（相关度 {ov}）关键词：{desc}')
    else:
        print('   仍无匹配——直接描述你的目标，或说"生态空白"登记新能力。')

def cmd_lanes(args):
    print('🛤️  生态分工地图（谁管什么/不管什么）')
    print()
    rows = [
        ('tvbox-source-hub', '源库/底包库/台账数据面', '不管技能治理'),
        ('tvbox-source-blacklist', '失效源拉黑与守门', '不管源入库'),
        ('tvbox-interface', '新接口接入流水线（手上有接口URL→做成可用json）', '不管存量治理；不管上游找源'),
        ('probe-hub', '上游侦察：找仓库/找源/采线路/真实测活（手上没源→去上游找）', '不管入库成json；不管存量治理'),
        ('tvbox-inspect', '存量体检：30 项监测检查器（盯"已经有的东西有没有坏"）', '不管新源接入；不管修复动作'),
        ('unfinished-audit', '欠账总账：收口/派单/闭环（盯"说了要做的有没有做完"）', '不做具体业务修复'),
        ('tvbox-janitor', '待删收件箱：AI 删不掉的冗余节点移入统一文件夹+生成待删清单', '不做判定谁该删；不直接删除'),
        ('legacy-watchdog', '业务遗留监测+元能力链', '不管任务调度'),
        ('skill-evolution', '单技能触发式进化(≥3次)', '不管全局规划'),
        ('ecosys-conductor', '任务提速+技能协调+全局进化', '不做具体业务'),
        # ★ S158 补：这行缺失导致 lanes 长期报「未登记职责 eco-ledger」——
        #   覆盖核对能发现缺口，但缺口要人补；本轮补上，不留"检测器知道但没人修"的账。
        ('eco-ledger', '大生态总账监管：复式记账(总账+账户)、账实/账账两线对账、终验五卡口',
                       '不代业务技能做账；不判定业务该不该做'),
    ]
    _listed = {s for s, _, _ in rows}
    for s, do, dont in rows:
        print(f'   {s:24s} 管: {do} ｜ 不管: {dont}')
    # 覆盖核对（S136 五续）：硬编码清单必然漂移——新增技能后没人会记得回来加一行，
    # 于是「没登记职责的技能」只能靠话术边界去猜，重叠风险就是这么长出来的。
    _miss = [s for s in ECO_SKILLS if s not in _listed]
    if _miss:
        print(f'   ⚠️ 未登记职责（{len(_miss)} 个）：{"、".join(_miss)}'
              f'——补进 cmd_lanes 的 rows，否则边界只能靠话术猜')
    print()
    print('💡 重叠检测: watchdog(元能力) 与 conductor(L3) 都产出评估——')
    print('   边界已划: watchdog 评「技能自身质量」，conductor 评「生态整体与方向」')

    # ── 版本一致性（S158 续）────────────────────────────────────────────
    # 起因：我连续三轮「改了 SKILL.md 就宣布版本四面同步 ✅」，
    # 实测 tvbox-source-hub 的 manifest.json / flow.json 停在 1.0.39 整整 14 个版本。
    # **只回读了数据库字段，没检查磁盘上的另外两处** —— 回读的和声称的不是同一个东西。
    # 这段的作用只有一个：让「不一致」自己喊出来，不靠人记得比对。
    _vers = _version_drift()
    if _vers['bad']:
        print()
        print(f'⚠️ 版本不一致（{len(_vers["bad"])} 个技能）——改版本要改齐，别只改 SKILL.md：')
        for name, items in _vers['bad']:
            print(f'   {name:26s} ' + ' ｜ '.join(items))
        print(f'   判据：SKILL.md 是正本（对外声明），manifest/flow 是副本，副本落后即漂移')
    elif _vers['unreadable']:
        print()
        print(f'❓ 版本检查有 {len(_vers["unreadable"])} 处读不到（无法判定，不等于一致）：'
              f'{"、".join(_vers["unreadable"])}')
    else:
        print()
        # ★ S158 九补：这里原来只打一句「✅ 版本一致（11 个技能）」——
        #   而 11 个里只有 1 个真有 manifest.json，其余 10 个是**缺文件**不是**一致**。
        #   把"扫不到"说成"通过"，等于给自己发了张假合格证。
        #   现在把两件事分开说：一致的是几个，缺副本的是几个（缺 ≠ 错，但必须看得见）。
        _miss = _vers.get('missing') or []
        _miss_manifest = [n for n, fs in _miss if 'manifest.json' in fs]
        print(f'✅ 版本一致（{_vers["ok_n"]} 个技能：SKILL.md 与已有副本全部对齐）')
        if _miss_manifest:
            print(f'ℹ️ 其中 {len(_miss_manifest)} 个技能**没有** manifest.json —— '
                  f'不算漂移，但也**没被核对**（缺 ≠ 一致）：')
            print(f'   {"、".join(_miss_manifest)}')

# ================= L3 全局进化层 =================

def _version_drift():
    """跨技能版本一致性核对（S158 续）。

    正本：各技能 SKILL.md frontmatter 的 `version`——它是对外声明的版本，用户/下游看的是它。
    副本：`manifest.json`、`scripts/flow.json` 里的 version（若存在）。

    返回 {'bad': [(技能, [明细...])], 'unreadable': [...], 'ok_n': int}

    ★ 三态：读不到 ≠ 一致。文件存在但解析失败 → 进 unreadable，绝不折叠成"没问题"（M-R60）。
    ★ 四态（S158 九补）：**文件不存在 ≠ 一致**。缺失单独进 `missing`，
      不并入 ok_n——把"扫不到"算成"通过"是本会话反复出现的假绿来源。
    ★ 不做自动修：漂移往往是"这个文件根本没人读"（flow.json 的 version 全仓无消费方），
      自动改齐会把「该删的冗余字段」伪装成「已同步」。
    """
    bad, unreadable, ok_n, missing_all = [], [], 0, []
    for s in ECO_SKILLS:
        base = os.path.join(SKILL_ROOT, s)
        sp = os.path.join(base, 'SKILL.md')
        if not os.path.exists(sp):
            unreadable.append(f'{s}/SKILL.md（不存在）')
            continue
        try:
            canon = None
            for line in open(sp, encoding='utf-8'):
                if line.startswith('version:'):
                    canon = (line.split('"')[1] if '"' in line
                             else line.split(':', 1)[1].strip())
                    break
            if not canon:
                unreadable.append(f'{s}/SKILL.md（无 version 行）')
                continue
        except Exception as e:
            unreadable.append(f'{s}/SKILL.md（{type(e).__name__}）')
            continue
        items = []
        # ★ S158 九补：`manifest.json` 不存在时原代码 `continue`——于是"**没有**这个副本"
        #   被静默算进 ok_n，`lanes` 打印「✅ 版本一致（11 个技能）」。
        #   可这 11 个里只有 1 个真有 manifest，另外 10 个是"缺文件"而非"一致"。
        #   和本会话反复踩的是同一个病：**检测器的扫描面 ≠ 实际存储位置**，
        #   扫不到的东西默认算通过，于是"检查通过"比"没检查"更危险。
        #   现改为：缺失单独计数并在汇总里点出来，不混进 ok_n。
        missing = []
        for f in ('manifest.json', os.path.join('scripts', 'flow.json')):
            fp = os.path.join(base, f)
            if not os.path.exists(fp):
                missing.append(os.path.basename(f))
                continue
            try:
                v = json.load(open(fp, encoding='utf-8')).get('version')
            except Exception as e:
                unreadable.append(f'{s}/{f}（{type(e).__name__}）')
                continue
            if v and str(v) != canon:
                items.append(f'{os.path.basename(f)}={v} ≠ SKILL={canon}')
        if missing:
            missing_all.append((s, missing))
        if items:
            bad.append((s, items))
        else:
            ok_n += 1
    # ★ S158 九补：`missing` 与 `ok_n` **并列返回、分开报**。
    #   ok_n 只统计"该有的副本都在且都对"的技能；missing 记"哪个副本压根不存在"。
    #   两者不能相加当"通过数"——那正是上一版假 ✅ 的来源。
    return {'bad': bad, 'unreadable': unreadable, 'ok_n': ok_n, 'missing': missing_all}


def _skill_stats(name):
    """读单技能三件套健康。"""
    p = os.path.join(SKILL_ROOT, name)
    ok = lambda f: os.path.exists(os.path.join(p, f))
    ver = ''
    sp = os.path.join(p, 'SKILL.md')
    if ok('SKILL.md'):
        for line in open(sp, encoding='utf-8'):
            if line.startswith('version:'):
                ver = line.split('"')[1] if '"' in line else line.split(':', 1)[1].strip()
                break
    back = sum(1 for x in open(os.path.join(p, 'CHANGELOG.md'), encoding='utf-8').readlines()
               if x.startswith('#')) if ok('CHANGELOG.md') else 0
    flow_n = 0
    fp = os.path.join(p, 'scripts', 'flow.json')
    if os.path.exists(fp):
        try:
            d = json.load(open(fp, encoding='utf-8'))
            flow_n = len(d.get('self_check', []))
        except Exception:
            pass
    return {'version': ver, 'changelog': back, 'self_check': flow_n,
            'three': ok('SKILL.md') and ok('CHANGELOG.md') and os.path.exists(fp)}

def _radar_scores(ready, total, watchdog_ok, adopted, denom, pending,
                  health_out='', gain_out='', noise_out='', snap='',
                  ver_ok=None, ver_total=0):
    """五维归一化评分（0-100），照设计文档承诺「健康/增益/噪声/活跃/契约」五维。
    S076：原实现只返回 3 维（contract/biz/radar），与文档承诺的五维不符——
    health/gain/noise 虽有输出却不参与评分（度量口径与承诺脱节，M-R39/R30）。
    五维解析：
      · cat_health  健康：检查器执行正常率（无高危告警=100；按 M 行 ✅ 占比）
      · cat_gain    增益：非零增益检查器占比（抓过真遗留的比例）
      · cat_noise   噪声：无高噪声=100，有则按比例扣
      · cat_active  活跃：数据源非空（源库/台账/经验库均 >0 = 100）
      · cat_contract 契约：就绪率 × **版本一致率**（S159 并入；原只看就绪率）
    兼容：biz/radar 作为**附加风险维度**并入（biz=遗留0；radar=采纳率）。

    形参 ver_ok / ver_total：版本三处对账（SKILL.md / CHANGELOG / 生态库）的一致条数与
    总条数。ver_ok=None 表示**读不到**（云端不可达或工具缺失）——按 M-R60 标"无法判定"，
    退回纯就绪率且不假装满分，绝不用"读失败"换一个好看的分。
    """
    # S076 修复作用域：total_c（检查器总数）原先只在 gain 段的 elif 分支里定义，
    # 却在噪声段（下方）被引用——一旦 gain 走 hv/健康/else 分支，噪声段就 NameError。
    # 提到函数开头统一定义，gain 段与噪声段共用。
    h_rows = [ln for ln in health_out.splitlines() if re.match(r'\s*M\d+\s', ln)]
    total_c = len(h_rows) or 17
    # 健康：从 health 表格统计 M 行中 ✅ 占比（无表格则看结论行）
    if h_rows:
        h_ok = sum(1 for ln in h_rows if '✅' in ln)
        cat_health = round(100 * h_ok / len(h_rows))
    else:
        cat_health = 100 if '所有检查器健康' in health_out else (0 if '高危' in health_out else 100)
    # 增益：区分「真零增益(疑阈值松)」vs「零增益因对象长期健康」——后者不是缺陷。
    # S076：原朴素实现把「零增益 N 个」一律记 0 分，是**假红**：本生态 30 期无遗留→
    # keys 恒空→所有检查器零增益，但这是「无遗留可抓」的健康表现，不该扣分。
    # 判据：gain 输出明示「健康也是正常结论」且 health 全绿时 → 视为健康，记 100。
    gz = re.search(r'零增益\s*(\d+)\s*个', gain_out)
    hv = re.search(r'高价值检查器', gain_out)
    health_all_ok = ('所有检查器健康' in health_out) or (
        h_rows and all('✅' in ln for ln in h_rows))
    if hv and '零增益' not in gain_out:
        cat_gain = 100                       # 有高价值命中且无零增益
    elif gz and health_all_ok:
        cat_gain = 100                       # 零增益但对象健康 → 正常结论，不扣分
    elif gz:
        cat_gain = round(100 * (total_c - int(gz.group(1))) / total_c) if total_c else 100
    else:
        cat_gain = 100
    # 噪声：四态判读（S137·M-R88 判据自证）。
    # 原实现 `100 if '无高噪声' in s else (按 N 个扣分 if 能解析 else 80)` 有三处失真：
    #  ① 兜底 80 把「输出格式变了没解析出来」与「真有中等噪声」压成同一个分数，
    #     属于**用分数掩盖未知**——正是 M-R88 禁止的「判据不自证」；
    #  ② 只有「无高噪声 / 高噪声 N 个」两态，容不下「有高噪声但一个都判不了」；
    #  ③ 分母用了 health 表格行数 total_c，可 total_c 只反映**本次输出里画了几行**，
    #     与噪声毫无关系——注入两行 health 就能算出 -100 分（实测踩到）。
    #     噪声比本就是「一个检查器的豁免/(命中+豁免)」，是逐检查器的**判读**，
    #     不是「几个坏/总共几个」的占比，强行归一化必然失真。
    # 故改为：分数只表达「结论」，不再做伪比例运算。
    #   green  → 100（明确无高噪声）
    #   red×N  → 50（有高噪声待复核；N 写进评语，不折算成伪精度）
    #   unknown→ 0  （没解析出结论 = 无法度量，绝不静默给中间分）
    _nz_list = re.search(r'高噪声检查器[：:]\s*([^\n（(]*)', noise_out)
    _nz_cnt = re.search(r'高噪声检查器[^\n]*?(\d+)\s*个', noise_out)
    if _nz_cnt:
        cat_noise, noise_state = 50, f'red×{_nz_cnt.group(1)}'
    elif _nz_list and _nz_list.group(1).strip():
        _ids = [x for x in re.split(r'[,，、]\s*', _nz_list.group(1).strip()) if x]
        cat_noise, noise_state = 50, f'red×{len(_ids)}'
    elif '无高噪声' in noise_out:
        cat_noise, noise_state = 100, 'green'
    else:
        cat_noise, noise_state = 0, 'unknown'
    # 活跃：③活跃度一行四极规模，均 >0 记 100
    nums = [int(x) for x in re.findall(r'\d+', snap)] if snap else []
    cat_active = 100 if nums and all(n > 0 for n in nums) else (0 if not nums else 100)
    # ★ 契约维度并入「版本一致率」（S159）：原实现只看 evolve scan 就绪率，
    #   而版本漂移（SKILL.md / CHANGELOG / 生态库 三处不一致）压根不进这个维度，
    #   于是「版本已经漂了两个，assess 仍然 100/100」——评分说假话，比不评分更危险。
    #   契约的本质是「文件里承诺的版本」和「实际版本」一致；版本漂移就是契约漂移的
    #   最直接形态，理应计入契约维度。
    #   口径：contract = 就绪率 × 版本一致率（两者都是「占总数的比例」，相乘即联合达成率）。
    #   三态（M-R60）：ver_ok=None 表示版本数据读不到（云端不可达/工具缺失）→
    #   不折叠成 100，退回纯就绪率并在评语里显式标「版本无法判定」。
    cat_ready = round(100 * ready / total) if total else 0
    if ver_ok is None:
        cat_contract, ver_state = cat_ready, 'unknown'
    elif ver_total:
        cat_contract = round(cat_ready * (ver_ok / ver_total))
        ver_state = 'green' if ver_ok == ver_total else f'drift×{ver_total - ver_ok}'
    else:
        cat_contract, ver_state = cat_ready, 'unknown'
    return {
        'cat_health': cat_health,
        'cat_gain': cat_gain,
        'cat_noise': cat_noise,
        'noise_state': noise_state,
        'cat_active': cat_active,
        'cat_contract': cat_contract,
        'ver_state': ver_state,
        'ver_ok': ver_ok,
        'ver_total': ver_total,
        'cat_biz': 100 if watchdog_ok else 0,                          # 附加：业务面 遗留0
        'cat_radar': round(100 * adopted / denom) if denom else 100,   # 附加：建议采纳率
    }

# ---------- M-R114 统一并发入口（S110） ----------
# 本文件两处并发：_sh_par 跑 6 条本地 CLI、终验跑 ②③ 两批。
# 都是本地子进程（不直接打公网），归 D 档（上限=min(核数,8)）。
# ★ 原 _sh_par 用 max_workers=len(cmds)，6 条就开 6，条数一多会失控。
import sys as _sys
_sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from concurrency import resolve_workers as _resolve_workers  # noqa: E402


def _sh_par(cmds, timeout=60):
    """并行跑多条独立外部命令（S065 提速）：assess 内 6 条 _sh 彼此无依赖，
    串行时被 max(单条) 累加成 sum(单条)；并行后总耗时≈max(单条)。
    返回 {cmd: output}。异常命令返回其错误串（与原 _sh 语义一致，不抛）。"""
    # ★ S163：改走统一入口（D 档：本地 CLI 并发）。
    #   线程异常 → 返回错误串（与原 _sh 语义一致：不抛、给串），调用方按解析失败处理。
    from concurrency import run_bounded, TIER_LOCAL
    _rep = run_bounded(list(cmds), lambda c: _sh(c, timeout), tier=TIER_LOCAL,
                       label='sh-par', keep_order=True, on_error='record')
    res = {}
    for c, v in zip(list(cmds), _rep.results):
        res[c] = v if v is not None else f'[并发异常] 命令未跑完：{c}'
    return res

def cmd_assess(args):
    print('📊 生态全景评估（五维雷达）')
    print()
    # S065 提速：assess 的外部命令彼此独立（evolve/watchdog/snapshot 互不依赖），
    # 串行时被累加（实测 32.2s，其中 watchdog scan/health 各 ~14.5s 是大头）；
    # 并行后总耗时≈最慢一路。更进一步：scan 与 health 遍历**同一批检查器**，
    # 用 tvbox run 放同一进程 → 检查器只跑一遍（health 复用 scan 的 _CHECKER_CACHE），
    # 把两路 ~29s 压成一路 ~14.5s。键与下面取用处一一对应。
    _C_SCAN = 'tvbox evolve scan 2>&1'
    _C_DOG = 'tvbox run "watchdog scan" "watchdog health" 2>&1'   # 同进程=检查器只遍历一次
    # S073：原用 `tvbox snapshot`——但它有**写副作用**（create-doc+rename-node 重建云端
    # 快照页），assess 每次评估都触发一次云端写，既污染数据又是 2.1s 的写往返。
    # ③活跃度只要一行规模数字，改用只读的 `tvbox sizes`（0.4s，无副作用）。
    _C_SNAP = 'tvbox sizes 2>&1 | head -1'
    _C_GAIN = 'tvbox watchdog gain 2>&1 | tail -3'
    _C_NOISE = 'tvbox watchdog noise 2>&1 | tail -4'
    _X = _sh_par([_C_SCAN, _C_DOG, _C_SNAP, _C_GAIN, _C_NOISE], timeout=120)
    scan = _X[_C_SCAN]
    # 从合并输出里切出 scan 段（②业务面看「无遗留/合计N项」结论）与 health 段（④表格行）
    # 注意：不能用 tail-2——合并流末尾是 health 段，会丢掉 scan 的结论行（曾致 biz=0 误判）
    _dog = _X[_C_DOG]
    _dog_lines = [ln for ln in _dog.splitlines() if ln.strip()]
    _wline = next((ln for ln in _dog_lines
                   if '无遗留' in ln or re.search(r'合计\s*\d+\s*项遗留', ln)), '')
    w = _wline or (_dog_lines[0] if _dog_lines else '')
    # ★ S158 五补：把「遗留明细」原样带出来。
    #   病因（用户："历史遗留咋没人报了？"）：终检 ④ 段原先只截 scan 输出的**最后 4 行**，
    #   而遗留明细（[耗时] M19 生态薄弱…/[轻微] M03 …）在**前面**，被 tail 截光；
    #   尾部只剩「📊 合计 2 项遗留」这一行结论 —— 于是"有几项"看得见，"是哪几项"看不见。
    #   同时 health 段里 M03/M19 标的是 `✅`（检查器执行正常），计数列虽为 1，
    #   但一张 ✅ 表格读不出"这里有 1 项遗留"。**三层各存一份，没有一层给全貌。**
    #   修法：从 scan 段按行首 `[级别]` 抓明细，独立成段输出，不依赖 tail。
    _items = [ln.rstrip() for ln in _dog.splitlines()
              if re.match(r'\s*\[(阻断|耗时|轻微)\]', ln)]
    _health = '\n'.join(ln for ln in _dog.splitlines()
                        if re.match(r'\s*M\d+\s', ln) or '所有检查器健康' in ln or '高危' in ln)
    # 分母取权威 footer「就绪 X/Y」——禁止 count('未就绪')（footer 说明行含该词会虚增分母）
    fm = re.search(r'就绪\s*(\d+)\s*/\s*(\d+)', scan)
    ready, total = (int(fm.group(1)), int(fm.group(2))) if fm else (scan.count('✅就绪'), len(ECO_SKILLS))
    print(f'① 契约维度: evolve scan {ready}/{total} 就绪')
    print()
    print(f'{"技能":24s} {"版本":10s} 三件套 self_check')
    for s in ECO_SKILLS:
        st = _skill_stats(s)
        mark = '✅' if st['three'] else '❌'
        print(f'{s:24s} {st["version"]:10s} {mark}      {st["self_check"]}')
    print()
    print(f'② 业务面: watchdog → {w}')
    snap = _X[_C_SNAP]
    print(f'③ 活跃度: {snap}')
    print()
    # ④ 技能质量维度（v1.4.0 补齐设计承诺：health/gain/noise 聚合）
    # S076：标签统一为设计文档承诺的中文五维名（健康/增益/噪声），与 ⑦ 综合评分口径一致。
    # ★ S158 五补：先打「遗留明细」，再打 health 表格。
    #   原顺序只有 health 表格 → 看到 `✅ M19 … 1` 会以为"健康无遗留"，
    #   而真相是「检查器执行正常，但它查出了 1 项遗留」。两句话差得很远。
    if _items:
        print(f'④ 遗留明细（{len(_items)} 项，来自 `tvbox watchdog scan`）：')
        for ln in _items:
            print(f'   {ln.strip()[:110]}')
        print(f'   → 逐项处置：`tvbox watchdog scan` 看完整 next-step，或在复盘显式标注"本期不处理+原因"')
    else:
        print('④ 遗留明细: （无）——若 ② 显示「合计 N 项遗留」而此处为空，说明解析失配，按缺陷报修')
    print()
    for label, out in (('健康 health', _health),
                       ('增益 gain', _X[_C_GAIN]),
                       ('噪声 noise', _X[_C_NOISE])):
        tail = [ln for ln in out.splitlines() if ln.strip()][-4:]
        print(f'④ 技能质量·{label}:')
        for ln in tail:
            print(f'   {ln.strip()[:90]}')
    print()
    # ⑥ 进化雷达维度（v1.6.0）：建议留痕 vs 决策留痕对账——未认领建议 = 进化闭环敞口
    adopted = pending = decided = retracted = 0
    try:
        hist = json.load(open(PLAN_FILE, encoding='utf-8'))
        for h in hist:
            if 'suggestions' in h:
                for x in h['suggestions']:
                    if x.get('retracted'):
                        retracted += 1        # 已证伪撤回（如检测器误报修正）——不计敞口、保留史
                    elif _is_adopted(x['skill'], x['evidence']):
                        adopted += 1
                    else:
                        pending += 1
            if 'verdict' in h:
                decided += 1
        denom = adopted + pending
        rate = f'{adopted}/{denom}' if denom else '0/0'
        print(f'⑥ 进化雷达: 建议采纳 {rate}（100%=闭环） ｜ 撤回误报 {retracted} 条 ｜ 雷达决策留痕 {decided} 条')
        print(f'   未认领建议 {pending} 条 → ' + ('✅ 无敞口，进化闭环健康' if pending == 0 else '⚠️ 有敞口，跑 direction 核实'))
    except Exception as e:
        print(f'⑥ 进化雷达: （留痕读取失败：{e}）')
    print()
    # ⑦ 综合评分 + 环比趋势（v1.7.0 补齐设计承诺「各技能雷达分」+ 外环回验）
    watchdog_ok = '无遗留' in w
    denom = adopted + pending
    # 产能比先算（S159）：⑦ 的满分免责提示要读它，必须先于打印求值——
    # 放在 ⑦ 段里算会 NameError（变量顺序依赖，写完必须实跑验证，不能只看语法）。
    # 判据不另写：直接调 brake.budget()（BIZ_KINDS/GOV_KINDS/RATIO_WARN 的唯一真源，
    # M-R38）。三态：读不到台账 → ok=None → 无法判定，不折叠成正常。
    bd = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import brake as _brake_mod
        bd = _brake_mod.budget()
    except Exception:
        bd = {'ok': None, 'reason': 'brake 模块不可用——无法判定'}
    # 版本一致率（S159 并入契约维度）：直接复用 version_guard.py 的判据，不另写一份
    # （M-R38：同一事实源；此处若自我实现，迟早与体检工具各说各话）。
    ver_ok, ver_total = None, 0
    try:
        vg = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'version_guard.py')
        vout = subprocess.run(['python3', vg, '--json'],
                              capture_output=True, text=True, timeout=60).stdout
        vd = json.loads(vout or '{}')
        rows = vd.get('rows') or []
        if rows:
            ver_total = len(rows)
            ver_ok = sum(1 for r in rows if r.get('status') == 'ok')
    except Exception:
        ver_ok, ver_total = None, 0      # 读不到 → 无法判定，不换成分数
    sc = _radar_scores(ready, total, watchdog_ok, adopted, denom, pending,
                       health_out=_health, gain_out=_X[_C_GAIN],
                       noise_out=_X[_C_NOISE], snap=snap,
                       ver_ok=ver_ok, ver_total=ver_total)
    # S076：overall = **五维主分**平均（严格照设计文档承诺「健康/增益/噪声/活跃/契约」），
    # biz/radar 作为附加风险维度只展示不计入主分（它们是布尔哨兵，非"雷达分"）。
    _main5 = ['cat_health', 'cat_gain', 'cat_noise', 'cat_active', 'cat_contract']
    overall = round(sum(sc[k] for k in _main5) / len(_main5)) if sc else 0
    # 打印时排除内部键：noise_state / ver_state / ver_ok / ver_total 是**明细**，
    # 已在下方单列一行，混进这行会重复成两个 state= 和含义不明的 ok/total。
    _skip = ('noise_state', 'ver_state', 'ver_ok', 'ver_total')
    # ★ S159 满分免责：五维主分**不含投入配比**——一个 100 分的生态可能正在
    #   把 90% 的力气花在治理自己身上。历史教训（S124→S138）：规则 82→100、
    #   信号源 27→31，而九轮里没有一件业务承载落地，同期 assess 一直是高分。
    #   全额 100 且产能比不健康时，必须明说"100 分不代表健康"，否则分数会变成安慰剂。
    # **S159 修·M-R60**：原写法是
    #     _cap = ''
    #     try:   ... 按需赋 _cap ...
    #     except Exception:  _cap = ''
    #   这是典型的「把异常折叠成空态」——`brake.budget()` 一旦抛异常，
    #   `_cap` 就保持 ''，于是**「产能比不健康」这句免责提示静默消失**，
    #   而分数照样打印 100/100。读者看到的是一份比真实情况更乐观的报告，
    #   且没有任何迹象表明「这句判断没做成」。
    #   修法不是加日志，而是**去掉这个 try**：budget() 本身就有三态
    #   （读失败 → ok=None, reason=...），不会抛异常也不需要兜底；
    #   真有异常应当冒出来，而不是被一句 `_cap = ''` 吃掉。
    _cap = ''
    if overall == 100 and bd and bd.get('ok') is not None and bd.get('state') != 'green':
        _cap = '  ⚠️ 但产能比不健康——100 分不含投入配比，勿当"生态很好"'
    print(f'⑦ 综合评分: {overall}/100（五维主分） ｜ '
          + ' ｜ '.join(f'{k.split("_")[1]}={v}' for k, v in sc.items() if k not in _skip) + _cap)
    # 版本一致率单列一行：契约维度已把版本一致率乘进就绪率，但乘进去就看不见了。
    # 「分数是结论，明细才是证据」——漂了哪几个必须能直接看见，否则又要靠人反算。
    _vs = sc.get('ver_state')
    if _vs == 'unknown':
        print(f'   版本一致: ⚠️ 无法判定（version_guard 读不到云端/工具缺失）→ 契约维度退回纯就绪率')
    elif _vs == 'green':
        print(f'   版本一致: ✅ {sc.get("ver_ok")}/{sc.get("ver_total")} 三处对齐（SKILL.md / CHANGELOG / 生态库）')
    else:
        print(f'   版本一致: 🔴 {sc.get("ver_ok")}/{sc.get("ver_total")}（{(sc.get("ver_total") or 0) - (sc.get("ver_ok") or 0)} 处漂移）'
              f' → 跑 version_guard.py 看明细')
    # ⑧ 业务产能比（S159）：治理:业务 本就是生态最核心的健康指标，但它过去只在
    #    `waste` 里算——而 waste 是**要人主动跑**的诊断命令，assess 是体检主入口。
    #    结果就是「治理占比 90% 这件事，只有想起来跑 waste 才看得见」。
    #    放进 assess = 每做一次全景体检必然撞见它，不依赖记性。
    #    数据已在上方 bd 取好（⑦ 的满分免责也要用），此处只负责打印。
    if not bd or bd.get('ok') is None:
        print(f'   产能比  : ⚠️ 无法判定 —— {(bd or {}).get("reason", "台账读取失败")}')
    else:
        icon = {'green': '✅', 'yellow': '🟡', 'red': '🔴'}.get(bd.get('state'), '⚠️')
        print(f'   产能比  : {icon} 治理 {bd["gov"]}/{bd["n"]}（{bd["ratio"]:.0%}）'
              f' vs 业务 {bd["biz"]} ｜ 连续治理 {bd["consec"]} 轮 → {bd["reason"]}')
    # 读上期留痕算环比（S080：损坏不再静默伪装成"首期基线"——区分三态）
    log_fp = os.path.join(os.path.dirname(PLAN_FILE), 'assess_log.json')
    prev = None
    led_err = None
    try:
        if os.path.exists(log_fp):
            logs = json.load(open(log_fp, encoding='utf-8'))
            if logs:
                prev = logs[-1]
    except Exception as e:
        led_err = f'{type(e).__name__}'   # 损坏：显式记录，不与"首期"混淆（M-R32 变体）
    if prev:
        d = overall - prev.get('overall', overall)
        arrow = '📈' if d > 0 else ('📉' if d < 0 else '➡️')
        print(f'   环比上期（{prev.get("date")}）：{prev.get("overall")} → {overall}  {arrow}{d:+d}')
    elif led_err:
        print(f'   ⚠️ 环比不可用：assess_log.json 读取失败（{led_err}）——留痕可能损坏，非"首期基线"')
    else:
        print('   环比：📌 首期基线（下期起显示趋势）')
    # 落地留痕（同日期合并覆盖，只保留最新一期；含五维分 + 关键指标）
    today = datetime.date.today().isoformat()
    rec = {'date': today, 'overall': overall, 'scores': sc,
           'metrics': {'ready': f'{ready}/{total}', 'watchdog_ok': watchdog_ok,
                       'adopted': f'{adopted}/{denom}', 'retracted': retracted,
                       'pending': pending, 'decided': decided}}
    try:
        logs = json.load(open(log_fp, encoding='utf-8')) if os.path.exists(log_fp) else []
        logs = [x for x in logs if x.get('date') != today]      # 同日只留最新
        logs.append(rec)
        json.dump(logs, open(log_fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        # 回读验证（M-R24）
        back = json.load(open(log_fp, encoding='utf-8'))
        print(f'💾 已留痕 assess_log.json（{len(back)} 期快照）——趋势与环比基础')
    except Exception as e:
        print(f'（留痕失败：{e}）')
    print()
    print('💡 五维评语：健康(全绿=检查器稳) ｜ 增益(零增益=对象健康) ｜ 噪声(无高噪声) ｜ '
          '活跃(四极非空) ｜ 契约(全绿=契约纪律好) ｜ 附加: 业务(遗留0) 雷达(采纳率) ｜ 详见 design 文档')

# 公共模板 bigram 停用表（这些词几乎出现在所有"扩展点/清零"措辞里，不具区分度）
_STOP_BIGRAMS = set()
for _w in ('扩展', '展点', '清零', '已清', '批次', '兑现', '落库', '实现', '能力', '支持',
           '优化', '增强', '完成', '修复', '新增', '命令', '检查', '检测', '维度',
           # S069 补：描述性泛词——evidence 恒含（"某建议内容…"），无区分度；
           # 不补会与任意 [已清零] 条目残留重叠→假阳性（受控单测用例2 复现）
           '建议', '内容', '相关', '尚未', '未兑', '零但'):
    for _i in range(len(_w) - 1):
        _STOP_BIGRAMS.add(_w[_i:_i + 2])

def _is_adopted(skill, evidence):
    """证据是否已被该技能兑现（flow.json 已清零条目 bigram≥3 重叠；L045/L047：中文匹配禁整词）。
    S058 加固：重叠必须含 ≥1 个**非公共词** bigram，否则全是模板词（扩展/清零/兑现…）的
    巧合重叠会假阳性（探针实证：'扩展点…未清零' 被 S047 条目误判采纳）。
    S069 修复：前缀剥离正则原为 `[^：:]*`，当 evidence 用**空格**（而非中文冒号）分隔
    `[扩展点]` 与正文时会贪婪吞掉整段正文→kb 恒空→该建议结构性永不可认领（假敞口）。
    改为「剥掉开头标签的方括号本身 + 至多一个紧随的分隔符（空白/冒号/破折号）」。"""
    fp = os.path.join(SKILL_ROOT, skill, 'scripts', 'flow.json')
    if not os.path.exists(fp):
        return False
    try:
        fb = open(fp, encoding='utf-8').read()
        kb = _bigrams(re.sub(r'^\s*\[[^\]]*\]\s*[：:—-]?\s*', '', evidence), cjk_only=True)
        for m2 in re.findall(r'\[已清零[^\]]*\][^"\n]{0,150}', fb):
            ov = kb & _bigrams(m2, cjk_only=True)
            if len(ov) >= 3 and (ov - _STOP_BIGRAMS):
                return True
    except Exception:
        pass
    return False

def _vkey(v):
    """版本字符串 → 可比较元组（'1.20.1' → (1,20,1)）。"""
    return tuple(int(x) for x in re.findall(r'\d+', str(v))[:3]) if re.findall(r'\d+', str(v)) else (0,)

def _sig_stale_cl(s):
    """信号源3：契约漂移——SKILL.md version 与 CHANGELOG 最新版本号不一致。
    CHANGELOG 为追加式（最新在底部），取全文最大版本号比较。返回漂移描述或 None。"""
    sk = os.path.join(SKILL_ROOT, s, 'SKILL.md')
    cl = os.path.join(SKILL_ROOT, s, 'CHANGELOG.md')
    try:
        m = re.search(r'^version:\s*["\']?([\d.]+)', open(sk, encoding='utf-8').read(), re.M)
        if not m:
            return None
        sv = m.group(1)
        clv = None
        # 只认标题行版本头（## ... vX.Y.Z）——正文引用的版本号/IP（127.0.0.1）会污染全文匹配
        for vm in re.finditer(r'^#+\s.*?v(\d+\.\d+\.\d+)', open(cl, encoding='utf-8').read(), re.M):
            if clv is None or _vkey(vm.group(1)) > _vkey(clv):
                clv = vm.group(1)
        if clv and _vkey(clv) != _vkey(sv):
            return f'SKILL={sv} 但 CHANGELOG 最新={clv}（追加式取最大值）'
    except Exception:
        pass
    return None

def _sig_stale_snap(s):
    """信号源4：快照时效——监测报告上云节点距今 >1 天未刷新。返回描述或 None。
    S063：产品归属/路径从 SNAPSHOT_OWNER/SNAPSHOT_FILE 派生（此前路径硬编码 5 处）。"""
    if s != SNAPSHOT_OWNER:
        return None
    fp = SNAPSHOT_FILE
    try:
        d = json.load(open(fp, encoding='utf-8'))
        last = d.get('latest') and d.get('history') or []
        if last:
            ts = last[-1].get('ts', '')[:10]
            age = (datetime.date.today() - datetime.date.fromisoformat(ts)).days
            if age > 1:
                return f'监测报告上云快照已 {age} 天未刷新（{ts}）'
    except Exception:
        pass
    return None

def _skill_desc_terms(s):
    """从 SKILL.md frontmatter 的 description 抽「触发词」——**真源**是 description，
    _SKILL_INTENTS 是它的路由投影（S135 M-R76：双副本须派生，不得各自维护）。

    只认两种**显式**句式（其余自然语言一律不猜，防止把句子片段误当触发词——
    首版按 / 、切分导致「探源司·成品源库」「原仓库→轻量 json」被当成触发词，9/9 虚警）：
      ① 触发词：a / b / c
      ② 当用户提到 a、b、c 时触发
    返回 [] = 该技能 description 未用显式句式声明触发词（本身即缺陷：模型看得出技能、
    路由表却无从派生）。"""
    p = os.path.join(SKILL_ROOT, s, 'SKILL.md')
    if not os.path.exists(p):
        return []
    try:
        txt = open(p, encoding='utf-8').read()
    except Exception as e:
        # M-R60 三态：读失败必须**显式报**，不得折叠成"无触发词"——
        # 折叠会让检测器把「文件读不了」误判为「技能没声明触发词」，同一症状两种根因。
        print(f'   ⚠️ {s}/SKILL.md 读取失败（{type(e).__name__}）——本技能触发词无法核验')
        return []
    m = re.search(r'^---\s*\n(.*?)\n---', txt, re.S)
    if not m:
        return []
    dm = re.search(r'description:\s*["\']?(.*?)(?:\n[A-Za-z_-]+:|\Z)', m.group(1), re.S)
    if not dm:
        return []
    desc = dm.group(1).replace('\n', ' ')
    # ★ S148：`触发词：` 可能**出现多段**（术语一组 + 业务语言一组），必须全部合并。
    #   旧实现用 `re.search` 只取**第一段**，于是第二段业务语言在路由层面根本不存在——
    #   这正是"技能明明写了用户话术却触发不了"的直接原因之一：
    #   不是没写，是**只读了上半句**。实测 9 个技能全部只有第二段漏读。
    parts = []
    # ★ S176b：句式①原只认 `触发词：`，把另外两种**同义**写法判成 0 触发词——
    #   `关键词：`（automation-task-manager 6 个词）、`触发关键词：`（class-activity-planner 7 个词）
    #   全部漏读，导致这 13 个词在路由层面**根本不存在**（不只是 evolve scan 显示 ✗）。
    #   这不是技能写错，是判据太窄：中文里这几种前缀在表达"触发词"上是等价的。
    #   注意 `触发词` 与 `触发关键词` 不互相包含（「触发」后一个是「词」一个是「关」），必须并列。
    _TRIG_PREFIX = r'(?:触发关键词|触发词|关键词)'
    for mm in re.finditer(_TRIG_PREFIX + r'[：:]\s*([^。；\n]+)', desc):   # 句式①（可多段）
        parts.extend(re.split(r'\s*[/、，,]\s*', mm.group(1)))
    if not parts:
        mm = re.search(r'当用户(?:提到|说)\s*(.+?)\s*(?:时触发|时)', desc)   # 句式②
        if mm:
            parts = re.split(r'\s*[、，,/]\s*', mm.group(1))
        else:
            return []
    out = []
    for x in parts:
        x = x.strip(' 「」『』*★（()）')
        # 触发词应是短语，不含标点/箭头/连接符号；超长或含分隔符的一律丢弃
        if 2 <= len(x) <= 14 and not re.search(r'[→·—…：:]', x):
            out.append(x)
    return out


def _own_terms(ta, tb, limit=6):
    """取 ta 里**独有**（tb 没有）的词项，按长度降序——长词更能代表职责边界。
    用途：判断两技能话术重叠时，是否仍能靠独有词把句子路由回正确的那一个。"""
    bset = set(tb.split())
    own = [t for t in ta.split() if t not in bset and len(t) >= 2]
    own.sort(key=lambda x: (-len(x), x))
    return own if limit is None else own[:limit]


def _route_hit_ok(s, terms):
    """术语能否路由回技能 s：**与 cmd_ask 同口径**（_route_score ≥ ROUTE_HIT_MIN）。
    检测器与生产必须共用同一判定函数，否则检测器验的不是生产实跑的那条路（M-R76 反面）。

    S136 五续：blob 也必须取自 **`_intents_view()` 派生视图**，不能取 `_SKILL_INTENTS` 原表。
    实测踩坑：description 里新加「找源/新源」后，派生视图已有、原表没有——
    生产路由（走派生视图）能命中，检测器（走原表）却判「部分触发词失配 92%」。
    这不是检测器误报，是**两源已分叉的实证**；修法是让检测器跟生产看同一份东西，
    而不是回头往原表再抄一份副本（那就又造了一个双副本）。"""
    own = [d for sk, d in _intents_view() if sk == s]
    if not own:
        return [], list(terms)
    blob = s + ' ' + own[0]
    ok, bad = [], []
    for t in terms:
        # 模拟真实提问：用户说的就是这个触发词本身 → 第一参数必须是 t（用户输入）
        if _route_score(t, blob) >= ROUTE_HIT_MIN:
            ok.append(t)
        else:
            bad.append(t)
    return ok, bad


def _field_score(verbose=False):
    """跑 ROUTE_FIELD 第三方口语集，返回 (top1命中率, top3含答案率, 明细)。

    ★ 注意本函数**只做度量，不做判定**——它不出现在任何 verify 判据里，
      也不进 _sig_route_blind 的返回值。理由见 ROUTE_FIELD 上方铁律①：
      把它接进判定，就必然有人为了让判定变绿去改词表，那就回到自测自绿。
      它能做的只有一件事：**让"真实泛化水平"这件事一直可见**。

    两个指标各自的意义：
      · top1   —— 单选命中率，反映路由"敢不敢自己拍板"；
      · top3   —— 候选含答案率，反映 `cmd_ask` 的"候选+依据"交付方式**是否够用**。
                  S157 的立场是：top1 低不是病，top3 高就够了。
    """
    n = len(ROUTE_FIELD)
    if not n:
        return 0.0, 0.0, []
    hit1 = hit3 = 0
    detail = []
    for q, want in ROUTE_FIELD:
        ranked = [sk for sk, _, _ in _route_ranked(q)]
        top1 = ranked[0] if ranked else None
        ok1 = top1 == want
        ok3 = want in ranked[:3]
        hit1 += ok1
        hit3 += ok3
        detail.append((q, want, top1, ok1, ok3))
    return hit1 / n, hit3 / n, detail


def _field_notes():
    """第三方口语集的**只读附注**（S157）——给 signal-check --all / cmd_field 展示用。

    ★ 刻意不返回"通过/不通过"，只返回一句描述。原因见 `_sig_route_blind` 里的长注释：
      一旦它变成判据，就会有人为了让判据变绿去塞词，刷的是拟合而不是泛化。
    """
    try:
        r1, r3, _ = _field_score()
    except Exception as e:                       # 度量本身坏了要说出来，不折叠（M-R60）
        return f'第三方口语集**跑不起来**（{type(e).__name__}: {str(e)[:50]}）——无法判定真实泛化水平'
    return (f'第三方口语集 {len(ROUTE_FIELD)} 条：单选 {r1*100:.0f}%／候选前三 {r3*100:.0f}%'
            f'（**指标，非判据**；S157 实测字面匹配天花板就在这个量级）')


def cmd_field(args):
    """field [--all] —— 看第三方口语集的真实泛化水平（只读，不改任何东西）。"""
    r1, r3, detail = _field_score()
    print(f'📊 第三方口语集（{len(ROUTE_FIELD)} 条，非本生态词表抄来）：')
    print(f'   单选命中 top1 = {r1*100:.0f}%   候选前三含答案 top3 = {r3*100:.0f}%')
    print(f'   {ROUTE_FIELD_NOTE}')
    if '--all' in args or any(a in ('-a', '--verbose') for a in args):
        print()
        for q, want, got, ok1, ok3 in detail:
            mark = '✅' if ok1 else ('🟡' if ok3 else '❌')
            print(f'   {mark} 「{q}」')
            if not ok1:
                print(f'        期望 {want} ／ 实得 {got or "无匹配"}')
    else:
        miss = [(q, w, g) for q, w, g, o1, _ in detail if not o1]
        if miss:
            print(f'\n   未单选命中 {len(miss)} 条（多半是"该猜哪家"有歧义，属正常）：')
            for q, w, g in miss[:8]:
                print(f'     · 「{q}」→ 期望 {w}，实得 {g or "无匹配"}')
    print(f'\n   ⚠️ 别拿这张表调词。S157 实测：补词能让它涨，但换新一批又跌回 ~50%，')
    print(f'      说明涨的是"见过"，不是"会了"。要改的是词表结构，不是这张表。')
    return 0


def _sig_route_blind(s):
    """信号源5：路由盲区——**两层**判定（S135 修 S056 起的假绿）。

    旧口径（假绿根因）：只查「技能名有没有被登记」——9 技能全登记，故恒定 9/9 通过；
    但实测「SKILL文件太大了怎么瘦身」相关度 0、「治理规则太多该精简」「底包超40KB怎么办」
    相关度 1（阈值 2），三句真实话术全部路由失败。**登记 ≠ 接得住**。

    新口径：
      L1 登记层（保留）——manifest / _SKILL_INTENTS 有无该技能；
      L2 接得住层（新增）——取 description 声明的触发词，按真实匹配口径反查能否命中自己；
                         命中率 <50% 即报「路由虚高」。

    ★ S174 补 **L0 分层前置**：本函数只对大生态成员做 L1/L2 判定。
      平台托管技能（frontmatter 有 display_name/category）不走我们的触发词体系，
      **拿本表的尺子量它必判死**——那是尺子不对，不是技能不对。
      但要**看得见**：单独给一句「平台托管·未纳入生态路由」的可观测提示，
      既不误判成缺陷，也不假装没这回事（M-L03 三态不折叠）。
    """
    # ── L0 分层：先问"这是谁家的技能"
    # ★ S176 修真缺陷：原写法把 `import eco_inventory as EI` 放在 try 里，
    #   却把 `if layer == EI.PLATFORM` / `EI.NO_SKILL` 放在 try 外——
    #   import 一失败，`EI` 这个名字根本不存在，两行 if 立刻抛
    #   `UnboundLocalError: cannot access local variable 'EI'`。
    #   实测证据：`tvbox conductor signal-check` 里
    #     ❌ 探针静默异常：信号源5 路由盲区：UnboundLocalError ... 'EI'
    #   也就是说，这个"降级"从来没生效过：**它不降级，它直接崩**。
    #   而外层还有 `except: 该技能脚本缺失或加载失败（降级跳过）` 兜着，
    #   于是崩溃被包装成"降级跳过"——**双重折叠**：真异常 → 被当降级 → 静默。
    #   修法：把 EI 的导入挪到 try 外，导入失败单独兜住并显式置 None；
    #   后续判断改为对 None 安全的形式（先判 EI 是否存在，再取常量）。
    EI = None
    try:
        import eco_inventory as EI
    except Exception as e:
        print(f'   ⚠️ eco_inventory 导入失败（{type(e).__name__}）——分层判定降级', file=sys.stderr)
    layer = None
    if EI is not None:
        try:
            inv, _w = EI.scan()
            layer = next((it['layer'] for it in inv if it['dir'] == s or it['name'] == s), None)
        except Exception as e:
            print(f'   ⚠️ eco_inventory 调用失败（{type(e).__name__}）——分层判定降级', file=sys.stderr)
    if EI is not None and layer == getattr(EI, 'PLATFORM', object()):
        return ('平台托管技能：由平台按 display_name/category 自行调度，'
                '不纳入生态触发词体系（非缺陷；但用户自然语言我们路由不到，属已知盲区）')
    if EI is not None and layer == getattr(EI, 'NO_SKILL', object()):
        return '该目录无 SKILL.md——不是技能，是残留目录，应清理'
    try:
        routes = open(MANIFEST, encoding='utf-8').read()
        key = s.replace('tvbox-', '')
        in_mani = s in routes or key in routes
        in_skill = any(sk == s for sk, _ in _SKILL_INTENTS)
        if not in_mani and not in_skill:
            return f'manifest.json 与技能级路由表(_SKILL_INTENTS)均无该技能路由条目（双重盲区）'
        if not in_skill:
            return f'技能级路由表(_SKILL_INTENTS)无该技能——用户业务话术无法路由到它（manifest 命令意图不救急）'
        # --- L2：接得住层 ---
        terms = _skill_desc_terms(s)
        if not terms:
            return (f'description 未声明可抽取触发词——模型/路由表无从判断何时用该技能'
                    f'（须写「触发词：X / Y / Z」）')
        ok, bad = _route_hit_ok(s, terms)
        rate = len(ok) / len(terms)
        # 第三判据（S135）：**用真实用户话术抽查**，而不是只看"声明的词能否命中自己"。
        # 为什么必须加：前两版检测器都栽在"故障注入抓不到"——
        #   · 只看总命中率：删掉 3 个词，34 词的命中率 87%→82%，仍报绿；
        #   · 只看前 3 个词：删掉的"瘦身"在第 5 位，照样漏。
        # 根本问题：**自己声明、自己验证**是自证循环（M-R77：验证须独立于被验对象）。
        # 故引入独立用例——ROUTE_GOLDEN 是人类写的真实提问，不随技能 description 变，
        # 技能改坏了它不变，因此能作为外部标尺。
        exp = ROUTE_GOLDEN.get(s)
        if exp:
            wrong = [(q, w) for q, w in exp if _route_top1(q) != s and w == s]
            if wrong:
                return (f'真实话术抽查失败 {len(wrong)}/{len(exp)}：'
                        f'「{wrong[0][0]}」实际路由到 {_route_top1(wrong[0][0]) or "无匹配"}'
                        f'（应到本技能）——用户这么问会找错技能')
        # ★ S157 第三方口语集：**只读附注，不影响本函数判定**。
        #   这里有极强的动机诱惑——把这句改成 `if field_hit < 0.5: return '泛化不足'`，
        #   立刻就能在面板上"看见问题"。但那会变成第四条自测自绿的路：
        #   面板一红，下一步必然是往词表塞词把面板刷绿，塞完真实泛化率纹丝不动。
        #   ROUTE_FIELD 存在的意义是**让真实水平一直可见**，不是**制造一个新 KPI**。
        #   故：只记在 `_field_notes()`（cmd_field / signal-check --all 展示），不参与 return。
        core = terms[:3]
        core_bad = [t for t in core if t in bad]
        if core_bad:
            return (f'核心触发词失配：{"、".join(core_bad)}'
                    f'（前 3 个主词中 {len(core_bad)} 个路由不回本技能，总命中率 {rate*100:.0f}%）'
                    f'——该技能对最典型说法无响应')
        if rate < 0.5:
            return (f'路由虚高：description 声明 {len(terms)} 个触发词仅 {len(ok)} 个能路由回本技能'
                    f'（{rate*100:.0f}%）｜失配：{"、".join(bad[:4])}'
                    f'——触发词与 _SKILL_INTENTS 不同源，须补齐')
        if bad:
            return (f'部分触发词失配：{"、".join(bad[:3])}（{rate*100:.0f}% 可路由，'
                    f'未达全匹配但已过阈值）')
        if not in_mani:
            return f'manifest.json 无该技能命令意图（路由半盲：业务话术可路由但命令不可达）'
    except Exception:
        pass
    return None

def _sig_skill_registry_gap(_root_override=None):
    """信号源28：生态技能漏纳管（S135）。

    为什么需要：**所有监管都从 ECO_SKILLS 派生**——audit-growth 遍历它查体积、
    signal-check 遍历它跑探针、verify 遍历它查三件套、_sig_route_blind 遍历它查路由。
    但 ECO_SKILLS 是**手工维护的静态清单**（eco_skills.json）。建了新技能却忘了加清单，
    该技能就处在**全部检查器的视野之外**：超 40KB 无人报、路由不通无人知、无 CHANGELOG 无人管。
    这是「谁监管新增技能」这个问题的真正答案所在——不是靠人记得，是靠本信号源兜住。

    识别口径（关键）：`/root/.codebuddy/skills/` 下同时住着生态自有技能与大量外来技能
    （官方市场 education/pdf/pptx、workbuddy-* 内置、`skill_<id>` 编号目录等）。
    不能"目录下有 SKILL.md 就算生态的"——那会每天报几十条误警（S131 踩过同款：
    把 exam-generator 算作漏检）。故用**白名单式特征**判定生态技能：
      · 名字在 ECO_SKILLS 里（已纳管，跳过）
      · 名字以 tvbox- 开头
      · 名字在 ECO_NAME_HINTS 里（probe-hub / skill-evolution 等不带 tvbox 前缀的）
    其余一律视为外来技能，不报。

    `_root_override`：探针注入用（临时目录造一个未纳管的 tvbox-xxx 技能）。

    ★ S174 扩容：**从"只盯自己人"改为"全量分层清点"**。
      病根（用户 2026-10-19「我就说为何看不到技能」）：
        原口径 `is_eco = name.startswith('tvbox-') or name in hints`，
        其余 `continue` 当"外来技能，不归本生态管"。
        实测后果：磁盘 66 个目录，只有 12 个被任何检查器看见；
        另外 54 个（43 平台托管 + 9 无归属）**连报都不报**，
        其中 9 个无归属的根本没人认领——既无 owner 也无平台兜底，最容易变孤岛。
      "不对外来技能发号施令"这个初衷是对的，但它把两件事混成了一件事：
        · 平台托管（有 display_name/category）→ 确实不该我们管，但**要能看见**
        · 无归属（什么都没有）→ 没人管，**这才是真缺口**
      新口径：三层各报各的，severity 不同：
        big-eco 漏登记  → 阻断（原有逻辑，不变）
        orphan 无归属    → 轻微（提醒认领，不强制）
        platform 平台托管 → **不报**（不算缺陷），但由 `inventory` 命令列出供查阅
        no_skill 残留    → 轻微（建议清理）
    """
    root = _root_override or SKILL_ROOT
    try:
        import eco_inventory as EI
        items, warns = EI.scan(root=root)      # ★ 传 root，否则探针在临时目录造的东西看不见
        out = []
        for w in warns:
            out.append({'skill': 'conductor', 'type': '技能清单读取异常',
                        'evidence': f'{w}（全量清点不可信，先修 eco_skills.json）'})
        for it in items:
            lay = it['layer']
            nm = it['dir']
            if lay == EI.BIG_ECO:
                continue                      # 已纳管
            if lay == EI.PLATFORM:
                continue                      # 平台托管，非缺陷（inventory 可查）
            if lay == EI.NO_SKILL:
                out.append({'skill': nm, 'type': '残留目录',
                            'evidence': f'{nm} 无 SKILL.md——不是技能，是残留，应清理'})
                continue
            # ORPHAN：无归属
            out.append({'skill': nm, 'type': '技能无归属',
                        'evidence': f'{nm}（{it["name"]}）既不在 eco_skills.json，'
                                    f'也无 display_name/category 平台标记——'
                                    f'无 owner 认领，体积/路由/三件套全部检查不到它'})
        return out
    except Exception as e:
        return [{'skill': 'conductor', 'type': '生态技能漏纳管',
                 'evidence': f'扫描技能目录失败：{type(e).__name__}: {e}'}]


def _sig_health():
    """信号源自检（S056/S062）：验证每个检测器依赖的外部资源仍可达（防"装健康的哑巴"）。
    S062 补强：① 依赖清单按 SIGNAL_SOURCES 注册表补全 7/8；
               ② 新增"清单一致性审计"——探针/自检覆盖数必须 == 注册表条目数（M-R34 结构性根因）。
    返回 [(type, desc)]，健康则空。"""
    doc_fp = '/workspace/生态1016_生态指挥家技能设计.md'
    checks = [
        ('1', 'backlog兑现', [os.path.join(SKILL_ROOT, s, 'scripts', 'flow.json') for s in ECO_SKILLS[:3]]),
        ('2', '生态空白', None),   # 依赖 tvbox watchdog，运行时判定
        ('3', '契约漂移', [os.path.join(SKILL_ROOT, s, 'SKILL.md') for s in ECO_SKILLS[:2]] +
                          [os.path.join(SKILL_ROOT, s, 'CHANGELOG.md') for s in ECO_SKILLS[:2]]),
        ('4', '快照时效', [SNAPSHOT_FILE]),
        ('5', '路由盲区', [MANIFEST]),
        ('6', '文档-实现漂移', [doc_fp]),
        ('7', '信号源自检', [__file__]),        # 自指：本模块自身存在即可
        ('8', 'verify判据', [__file__]),        # 解析器在本模块内
        ('9', '技能停滞', [os.path.join(SKILL_ROOT, 'skill-evolution', 'scripts', 'evolve.py')]),  # 依赖 evolve scan 第五维
        ('10', '入口内联命令', ['/usr/local/bin/tvbox',
                                os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'manifest.json')]),  # 依赖入口脚本 + manifest
        ('11', '评分器自检', [__file__]),        # 自指：评分器在本模块内（合成输入驱动，无外部依赖）
        ('12', '留痕完整性', [os.path.join(PLANS_DIR, f) for f in LEDGER_FILES]),  # 依赖三个留痕文件
        ('13', '执行层自检', [__file__]),        # 自指：_sh 在本模块内（合成命令驱动，无外部依赖）
        ('14', '快照基座自检', [INSPECT_ENGINE]),
        ('15', '读表基座自检', [os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'scripts', 'core.py')]),
        ('16', '基座折叠扫描', None),   # 依赖全部 ECO_SKILLS 脚本目录，运行时遍历
        ('17', '云端读取语义自检', [os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'scripts', 'link.py')]),
        ('18', '上游侦察自检', [os.path.join(SKILL_ROOT, 'probe-hub', 'scripts', 'probe.py')]),
        ('19', '上游跑路检查器自检', [INSPECT_ENGINE]),
        ('20', '生态台账版本一致性', [os.path.join(SKILL_ROOT, 'library', 'database', 'query_database_record.py')]),
        ('21', '经验对照缺口', [os.path.join(SKILL_ROOT, 'skill-evolution', 'scripts', 'evolve.py')]),
        ('22', '会话经验留痕缺口', [os.path.join(SKILL_ROOT, 'library', 'database', 'query_database_record.py')]),
        ('23', '核心库行数归零', [os.path.join(SKILL_ROOT, 'library', 'database', 'query_database_record.py')]),
        ('24', '三层备份过期', None),
        ('25', '问题遗留超期', None),
        ('26', '监测登记册副本复活', None),
        ('27', '源库状态未终态', None),
        ('28', '生态技能漏纳管', [SKILL_ROOT]),
        ('29', '入口判定取值域', ['/usr/local/bin/tvbox']),
        ('30', '评分器状态可分辨', [os.path.join(SKILL_ROOT, 'ecosys-conductor', 'scripts', 'conductor.py')]),
        ('31', '入口判定无状态', ['/usr/local/bin/tvbox']),
        ('32', '投影孤儿词', [os.path.join(SKILL_ROOT, 'ecosys-conductor', 'scripts', 'conductor.py')]),
        ('33', '规则ask可命中', [os.path.join(SKILL_ROOT, 'ecosys-conductor', 'scripts', 'rules_registry.json')]),
        ('34', '命令执行器存在', ['/usr/local/bin/tvbox',
                                  os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'manifest.json')]),
        # ★ S159 补：35–39 早已注册进 SIGNAL_SOURCES，却一直没进本清单。
        #   后果正是本函数自己要防的病——「装健康的哑巴」：这 5 个检测器的依赖
        #   哪天坏了，自检不报、signal-check 照样 ✅，没人知道它已经哑了。
        #   自检的全部价值在于"覆盖 == 注册表条目数"（M-R34），漏登记=自检本身在说谎。
        ('35', '清道夫健康', [os.path.join(SKILL_ROOT, 'tvbox-janitor', 'scripts', 'janitor.py')]),
        ('36', '探针存活', [__file__]),          # 探针体系在本模块内（函数完整性检查）
        ('37', '监察司自身', [INSPECT_ENGINE,
                              os.path.join(SKILL_ROOT, 'tvbox-inspect', 'monitors.json')]),
        ('38', '大生态对账', [os.path.join(SKILL_ROOT, 'library', 'database', 'query_database_record.py')]),
        ('39', '全技能监护', [SKILL_ROOT]),      # 依赖各技能目录，运行时遍历
        # ★ S158 补：40 是这轮新加的（技能↔资料库），加注册表却漏了自检登记——
        #   正是本函数要防的那件事本身。依赖 = 被扫的各技能 scripts 目录 + 协议实现 core.py。
        ('40', '资料库调用协议', [os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'scripts', 'core.py')]),
        # ★ S158：41 依赖 pyflakes 可执行（缺失时本检测器报"无法判定"，不是沉默放行）。
        #   依赖本身无法用路径表达（是模块不是文件），故设为 None 由运行时判定——
        #   这正是同 2/16/24/25/26/27 的处置方式，不算漏登记。
        ('41', '静态未定义名', None),
        # ★ S159（M-R117）：依赖本模块与当日 journal，二者都在运行时判定，
        #   与 2/16/24/25/26/27/41 同一种处置（不算漏登记）。
        ('42', '会话提炼缺口', None),
    ]
    out = []
    for sid, name, deps in checks:
        if deps is None:
            continue
        miss = [d for d in deps if not os.path.exists(d)]
        if miss:
            out.append(('信号源自检', f'信号源{sid} {name} 依赖丢失（{len(miss)}/{len(deps)} 不可达）→ 该检测器静默失效'))
    # 信号源2 运行时判定：watchdog expand 是否仍有输出能力
    # S081：用 _sh_ok 判定（原 `'expand 执行失败' in ex or not ex.strip()` 无法区分
    # "命令失败返回空"与"成功无输出"，会误报假告警——失败哨兵统一识别）
    ex = _sh('tvbox watchdog expand 2>&1', timeout=20, cache=True)
    if not _sh_ok(ex) or not ex.strip():
        out.append(('信号源自检', '信号源2 生态空白 依赖 tvbox watchdog expand 不可达 → 该检测器静默失效'))
    # 清单一致性审计（S062）：注册表 vs 自检覆盖，缺一即报（防新增信号源时漏回填自检）
    reg = set(SIGNAL_SOURCES.keys())
    self_cov = {c[0] for c in checks}
    for sid in sorted(reg - self_cov):
        out.append(('信号源自检', f'信号源{sid} {SIGNAL_SOURCES[sid]} 已在注册表但 _sig_health 未自检（覆盖缺口）'))
    # 话术两源对账（S136 收口 bc2dd17c605d·M-R76）——**全局检查，故放这里不放 _sig_route_blind**：
    #   description 是触发词真源，_SKILL_INTENTS 是投影。投影靠手工维护，
    #   改了 description 忘了同步 → 用户说的话落不到它头上，而**没有任何检测器会报**
    #   （检测器读的都是投影，投影自己不觉得自己缺东西——完美的自证循环）。
    #   注：_intents_view() 已做并集派生，漏词不至于立刻丢路由；但缺口本身是隐患，越漏越多。
    try:
        for sk, miss in _intents_source_audit().items():
            out.append(('信号源自检', f'话术两源缺口：{sk} 的 description 声明了 {len(miss)} 个触发词，'
                                     f'_SKILL_INTENTS 未登记 {"、".join(miss[:5])}——改真源忘同步投影'))
    except Exception as e:
        out.append(('信号源自检', f'话术两源对账无法判定（{type(e).__name__}: {e}）——勿当作无缺口'))
    # 信号源32 投影孤儿词（S148）——**方向相反的另一半**。
    #   上面那条查「真源有、投影无」，这条查「投影有、真源无」。
    #   为什么必须查：`_intents_view()` 取 description∪投影 的**并集**，
    #   于是孤儿词照样能路由、黄金用例照样绿——**所有基于路由的检测都测不出来**。
    #   但模型的判断依据只有 description，投影是给打分器用的。孤儿词意味着
    #   「打分器认、模型不认」：用户说了这话，路由能算到它，可模型根本不知道
    #   该调这个技能。实测 67 个孤儿词，全部是这一形态。
    #   修法只有一条：把孤儿词补进 description（真源），或在投影里删掉——
    #   不许留在"只有打分器认识"的中间态。
    try:
        _orphan = _intents_orphan_audit()
        for sk, orphan in _orphan.items():
            out.append(('信号源自检', f'投影孤儿词：{sk} 的 _SKILL_INTENTS 登记了 {len(orphan)} 个词，'
                                     f'description 真源未声明 {"、".join(orphan[:5])}'
                                     f'——打分器认识但模型看不见，用户这么说模型想不起该用该技能'))
    except Exception as e:
        out.append(('信号源自检', f'投影孤儿词审计无法判定（{type(e).__name__}: {e}）——勿当作无缺口'))
    # 信号源33 规则 ask 可命中（S148）——注册表**可读**不等于**可问**。
    #   这条与信号源33 探针是两层：探针证明「注入退化能抓到」，这里证明「现在没退化」。
    #   缺一不可——只有探针无自检，则真实退化要等下次跑 signal-check 才暴露；
    #   只有自检无探针，则检测器本身坏了也看不出来（恒返回空=永远健康，M-R53）。
    try:
        for _t, _e in _sig_ask_hittable():
            out.append(('信号源自检', f'{_t}：{_e}'))
    except Exception as e:
        out.append(('信号源自检', f'规则 ask 审计无法判定（{type(e).__name__}: {e}）——勿当作全覆盖'))
    # 信号源34 命令执行器存在（S148）——「manifest 说有」不等于「入口认得」。
    try:
        for _t, _e in _sig_cmd_executable():
            out.append(('信号源自检', f'{_t}：{_e}'))
    except Exception as e:
        out.append(('信号源自检', f'命令执行器审计无法判定（{type(e).__name__}: {e}）——勿当作无幽灵'))
    # 技能清单一致性审计（S064）：本地 ECO_SKILLS 副本 vs evolve 权威发现（磁盘事实）
    # ——本地副本会随生态演进过期（新增/移除技能不漏报），须与权威发现对齐
    try:
        ev = _sh('tvbox evolve scan 2>&1', timeout=30, cache=True)
        disk = set(re.findall(r'(?:✅就绪|❌未就绪)\s+(\S+)', ev))
        eco = set(ECO_SKILLS)
        if disk:
            for s in sorted(disk - eco):
                out.append(('技能清单漂移', f'{s} 在 evolve 权威发现中但 ECO_SKILLS 未收录（副本过期）'))
            for s in sorted(eco - disk):
                out.append(('技能清单漂移', f'{s} 在 ECO_SKILLS 中但 evolve 未发现（可能已移除）'))
    except Exception:
        pass
    return out

def _sig_doc_gap():
    """信号源6：文档-实现漂移——设计文档命令表 vs conductor 实际 DISPATCH。
    文档是活契约：实现走远而文档没跟上 = 后来者按文档操作必踩空（M-R30 沉淀）。
    返回 [(type, desc)]。"""
    doc_fp = '/workspace/生态1016_生态指挥家技能设计.md'
    out = []
    try:
        doc = open(doc_fp, encoding='utf-8').read()
        # 命令名允许小写字母/数字/-/_（此前只含 [a-z-]，下划线命令会漏检，S057 探针实证）
        doc_cmds = set(re.findall(r'^\| `([a-z][a-z0-9_-]+)', doc, re.M))
        impl = set(DISPATCH.keys())
        for m in sorted(doc_cmds - impl):
            out.append(('文档-实现漂移', f'设计文档列了 {m} 但 DISPATCH 未实现'))
        for m in sorted(impl - doc_cmds):
            out.append(('文档-实现漂移', f'已实现 {m} 但设计文档命令表未登记'))
    except Exception as e:
        # M-R60：读失败不得折叠成「无漂移」——那会让文档检查静默失效（原为 except: pass，
        # 正是 L118 假绿形态：被验对象读不到时，"一切正常"）
        out.append(('文档-实现漂移', f'设计文档读取失败（{type(e).__name__}: {e}）——无法判定，勿当作无漂移'))
    return out

def _sig_backlog():
    """信号源1：backlog 未兑现扩展点（已采纳的过期条目过滤——防建议已完成的事）。
    抽成函数以便 signal-check 探针（S058）。返回 [{skill,type,evidence}]。"""
    out = []
    for s in ECO_SKILLS:
        fp = os.path.join(SKILL_ROOT, s, 'scripts', 'flow.json')
        if not os.path.exists(fp):
            continue
        try:
            d = json.load(open(fp, encoding='utf-8'))
            for b in d.get('backlog', []):
                if '扩展点' in b and '已清零' not in b:
                    if _is_adopted(s, b):
                        continue   # 过期扩展点（实际已兑现）≠ 新建议
                    out.append({'skill': s, 'type': 'backlog兑现', 'evidence': b[:80]})
        except Exception:
            pass
    return out

def _sig_eco_blank():
    """信号源2：生态空白（watchdog expand 的候选新实体）。抽成函数以便探针（S058）。"""
    out = []
    ex = _sh('tvbox watchdog expand 2>&1', cache=True)
    # S081：命令失败（哨兵）时直接返回空——失败由信号源7 依赖自检报出，此处不误解析哨兵串
    if not _sh_ok(ex):
        return out
    m = re.search(r'🆕 新实体[^\n]*\n((?:\s+- .+\n?)+)', ex)
    if m:
        for line in m.group(1).strip().splitlines():
            out.append({'skill': 'ecosys-conductor', 'type': '生态空白',
                        'evidence': 'expand: ' + line.strip()[:80]})
    return out

def _sig_stagnant():
    """信号源9：技能停滞（S069·M-R45 机制化）——契约齐备但无可执行脚本的「纯文档技能」。
    与信号源2（生态空白=缺新实体）正交：本条是**已有技能缺能力层**。
    数据源 = evolve scan 新增的第五维标注与「无机器可执行层」汇总行（与 evolve 共用事实源，M-R38）。"""
    out = []
    try:
        ev = _sh('tvbox evolve scan 2>&1', timeout=30, cache=True)
        # 优先解析汇总行（evolve 直接给出结论），避免重复实现「判无脚本」逻辑
        m = re.search(r'无机器可执行层[^\n]*[：:]\s*(.+)', ev)
        if m:
            for name in re.split(r'[,，]\s*', m.group(1).strip()):
                if name:
                    out.append({'skill': name, 'type': '技能停滞',
                                'evidence': f'{name} 契约齐备但无 scripts/*.py（纯文档 SOP，执行靠手工重做）'})
    except Exception:
        pass
    return out


def _sig_inline_cmd():
    """信号源10：入口内联命令一致性（S074·M-R40 跨层形态化）。
    入口脚本（/usr/local/bin/tvbox）内联实现了若干命令（manifest/contracts/chain/run），
    它们**不属任何技能、无 scripts/**.py——是「跨层游离组件」，此前不被任何审计覆盖：
    · 内联命令未登记进 manifest → 用户话术无法路由到它（发现不了）
    · 数据源：一并解析「入口内联 handler」与「manifest commands」，双向比对。
    与信号源6（设计文档 vs conductor DISPATCH）正交：本条管**入口层**，那条管**技能层**。"""
    out = []
    try:
        ent = open('/usr/local/bin/tvbox', encoding='utf-8').read()
        # 入口内联命令：verb == 'xxx' 分支（跳过 run/p 这类别名由 manifest 决定是否登记）
        inline = set(re.findall(r"verb == '([a-z][a-z0-9-]*)'", ent))
        man_fp = os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'manifest.json')
        mf = json.load(open(man_fp, encoding='utf-8'))
        man = set()
        for c in mf.get('commands', []):
            if isinstance(c, dict):
                w = c.get('cmd', '').split()
                if len(w) >= 2 and w[0] == 'tvbox':
                    man.add(w[1])
        # 内联命令中，除了通用别名(p)，都应出现在 manifest（否则用户无法路由）
        alias = {'p'}
        for m in sorted(inline - man - alias):
            out.append({'skill': 'tvbox-source-hub', 'type': '入口内联命令',
                        'evidence': f'入口内联命令 {m} 未登记进 manifest.json——用户话术无法路由到它'})
    except Exception:
        pass
    return out


def _sig_scorer():
    """信号源11：评分器自检（S077）——「度量器自身也是被测对象」的机制化（M-R53/M-R54）。
    S076 修好了 `_radar_scores`（三维→五维、gain 假红、total_c 作用域），但修完**没给它加探针**：
    若日后有人回退成三维、或让 gain 重新一律记 0、或再踩 total_c 作用域，**没有任何检查器能发现**
    （verify 判据②只查「assess ≥90」，评分器**静默失真**它照样过）。这是 M-R54 的盲区：
    口径对账只发生在「人肉巡检」那一刻，不发生在「每次 verify」。
    本检测器用**合成输入**驱动 `_radar_scores`，断言设计文档承诺的不变量仍成立：
      ① 五维齐备：返回键含 health/gain/noise/active/contract
      ② 零增益+健康 → 不扣分（gain=100，防假红回归）
      ③ 真告警 → 能拉低分（health 有 ❌ 时 cat_health<100，防「永远100」的哑巴）
      ④ 作用域安全：五条分支（含无表格空输出）都不抛异常
    返回 [] 表示评分器健在；有断裂则报出具体哪条不变量失守。"""
    out = []
    hdr = '\n'.join(f'M{i} x ✅' for i in range(1, 18))
    snap = '源库 100 | 备用 5 | 台账 3 | 经验库 80'
    base = dict(ready=10, total=11, watchdog_ok=True, adopted=9, denom=10, pending=2, snap=snap)
    main5 = ['cat_health', 'cat_gain', 'cat_noise', 'cat_active', 'cat_contract']

    def _run(**kw):
        try:
            return _radar_scores(**base, **kw), None
        except Exception as e:
            return None, f'{type(e).__name__}: {e}'

    # ① 五维齐备
    r0, err0 = _run(health_out=hdr + ' 所有检查器健康', gain_out='零增益 17 个', noise_out='无高噪声')
    if r0 is None:
        out.append({'skill': 'ecosys-conductor', 'type': '评分器自检',
                    'evidence': f'评分器 _radar_scores 抛异常（作用域/签名断裂）：{err0}'})
    else:
        miss = [k for k in main5 if k not in r0]
        if miss:
            out.append({'skill': 'ecosys-conductor', 'type': '评分器自检',
                        'evidence': f'评分器缺维：{"、".join(miss)} 未进返回（设计承诺五维，度量口径脱节 M-R54）'})
        # ② 零增益+健康 → 不扣分（防 S076 假红回归）
        if r0.get('cat_gain') != 100:
            out.append({'skill': 'ecosys-conductor', 'type': '评分器自检',
                        'evidence': f'零增益+健康时 cat_gain={r0.get("cat_gain")}≠100（假红回归 M-R44/R54）'})        # ③ 真告警 → 能拉低分（防「永远100」哑巴）
        r1, _ = _run(health_out='M1 a ❌\nM2 b ✅ 高危', gain_out='零增益 2 个', noise_out='无高噪声')
        if r1 and (r1.get('cat_health') or 0) >= 100:
            out.append({'skill': 'ecosys-conductor', 'type': '评分器自检',
                        'evidence': f'health 有 ❌ 高危但 cat_health={r1.get("cat_health")}=100（评分器恒满，哑巴 M-R32）'})
        # ④ 多分支（含空输出）不抛异常（防作用域越界回归）
        for tag, kw in (('全绿+高噪声', dict(health_out=hdr, gain_out='零增益 5 个', noise_out='高噪声检查器 2 个')),
                        ('无表格空输出', dict(health_out='', gain_out='零增益 3 个', noise_out='高噪声检查器 1 个')),
                        ('health高危', dict(health_out='M1 a ❌\nM2 b ✅ 高危', gain_out='零增益 2 个', noise_out='无高噪声'))):
            _, e = _run(**kw)
            if e:
                out.append({'skill': 'ecosys-conductor', 'type': '评分器自检',
                            'evidence': f'评分器在「{tag}」分支抛异常（作用域越界回归）：{e}'})
    return out


def _sig_ledger():
    """信号源12：留痕完整性（S080·M-R32 变体：损坏被静默伪装）。
    conductor 三个自有留痕（evolution_plan / assess_log / signal_proven）是所有趋势、环比、
    采纳率、兑现率计算的**数据基座**，但此前**无任何守护**：读取处一律 `except Exception: pass`——
    留痕损坏时静默降级（环比显示"📌 首期基线"、采纳率显示"0/0"），用户**误以为首次运行/无数据**，
    而真相是历史已损坏（S079 前 assess 读 assess_log 正是如此）。
    本检测器对每个留痕做三态体检：
      ① 存在性：文件缺失 → 报（数据基座塌陷）
      ② 完整性：JSON 不可解析 / 非 list → 报（损坏，所有派生计算随之失真）
      ③ 新鲜度：末条 date 距今 > STALE_DAYS 天 → 报（留痕停更=检查机制空转，M-R36）
    返回 [] 表示三留痕健在；否则逐条报出具体失守项。"""
    import json as _json
    out = []
    STALE_DAYS = 3   # 留痕超过 3 天未更新 → 停更告警（本生态每会话都有活动，3 天足够宽松）
    today = datetime.date.today()
    for fn in LEDGER_FILES:
        fp = os.path.join(PLANS_DIR, fn)
        if not os.path.exists(fp):
            out.append({'skill': 'ecosys-conductor', 'type': '留痕完整性',
                        'evidence': f'留痕 {fn} 缺失——趋势/环比/采纳率数据基座塌陷'})
            continue
        try:
            data = _json.load(open(fp, encoding='utf-8'))
        except Exception as e:
            out.append({'skill': 'ecosys-conductor', 'type': '留痕完整性',
                        'evidence': f'留痕 {fn} JSON 损坏（{type(e).__name__}）——所有派生计算静默失真'})
            continue
        if not isinstance(data, list):
            out.append({'skill': 'ecosys-conductor', 'type': '留痕完整性',
                        'evidence': f'留痕 {fn} 顶层非 list（{type(data).__name__}）——结构失配，读取必崩或空'})
            continue
        # 新鲜度：取末条 date（无 date 字段的条目跳过，不误报）
        dates = [x.get('date') for x in data if isinstance(x, dict) and x.get('date')]
        if dates:
            try:
                age = (today - datetime.date.fromisoformat(max(dates))).days
                if age > STALE_DAYS:
                    out.append({'skill': 'ecosys-conductor', 'type': '留痕完整性',
                                'evidence': f'留痕 {fn} 停更 {age} 天（末条 {max(dates)}）——检查机制可能空转'})
            except Exception:
                pass
    return out


def _sig_exec():
    """信号源13：执行层自检（S081·M-R59）——「守护者的共同底座也需守护」。
    全部 12 个信号源都间接依赖 `_sh` 执行外部命令；`_sh` 的失败信号若不可靠，
    **所有检测器一起把"命令失败"误当"正常空输出"**（S081 前：非0退出返回空串 '',
    与"成功但无输出"完全无法区分；超时返回格式不一的中文串——下游拿原始输出判定必误）。
    这是 M-R32/M-R57 家族在**执行层**的形态：底座不可靠 → 上层全体静默失真。
    本检测器用**合成命令**驱动 `_sh`，断言三态区分能力仍在：
      ① 成功（有输出）→ _sh_ok=True
      ② 成功（空输出）→ _sh_ok=True（**必须**与失败区分，这是本信号的命门）
      ③ 非0退出 → _sh_ok=False（此前返回空串，与②混淆）
      ④ 超时 → _sh_ok=False（统一哨兵格式）
      ⑤ 命令不存在 → _sh_ok=False
    返回 [] 表示执行层三态健在；否则报出具体哪条不变量失守。"""
    out = []
    probes = [
        ('成功有输出', _sh('echo __probe_ok__'), True),
        ('成功空输出', _sh('true'), True),              # 命门：空输出 ≠ 失败
        ('非0退出', _sh('false'), False),
        ('超时', _sh('sleep 5', timeout=1), False),
        ('命令不存在', _sh('__no_such_cmd_zz__'), False),
    ]
    for desc, got, want in probes:
        if _sh_ok(got) != want:
            out.append({'skill': 'ecosys-conductor', 'type': '执行层自检',
                        'evidence': f'_sh 三态失守：「{desc}」得 ok={_sh_ok(got)}，应 {want}'
                                    f'（底座失败信号不可靠→全检测器静默失真 M-R59）'})
    return out



def _sig_entry_verdict(_load_override=None):
    """信号源29：入口判定取值域自检（S136 五续·M-R87）——「判成败的尺子本身要被量」。

    为什么必须有这条：调度入口是**所有技能成果的唯一出口**，它把「这次算不算成功」
    写进 journal，而 journal 是 recap 复盘、audit-compliance、进化方向的共同输入。
    这把尺子一旦量错，错的不是一条日志，是**全部上层判断**。

    实测两次都栽在这里，而且方向相反：
      · 一版 `except SystemExit: pass` 吞掉退出码 → `exit(1)`（发现了问题）记成成功；
      · 修完紧接着 `_RC = ret or 0` → watchdog 三个查询命令返回**结果列表**，
        被当成非0退出码 → 16 条正常执行记成异常，err 里塞进几 KB 监控数据。

    根因同一句话：**没先定义取值域就判真假**。修法是把判定抽成唯一纯函数
    `tvbox:_as_exit_code()` 并在此用合成输入驱动它，断言取值域不变：
      ① int 0      → 0（成功）
      ② int 1/2    → 原样（失败，且码要保住）
      ③ bool True  → 0（bool 是 int 子类但语义是数据，**必须**排除）
      ④ 列表/字典/字符串/None → 0（查询结果是数据，不是退出码）
    返回 [] 表示尺子仍准；否则报出哪条取值域被破坏。
    """
    out = []
    entry = '/usr/local/bin/tvbox'
    try:
        if _load_override is not None:
            fn = _load_override
        else:
            import importlib.util as _ilu
            import importlib.machinery as _ilm
            if not os.path.exists(entry):
                return [{'skill': 'ecosys-conductor', 'type': '入口判定取值域',
                         'evidence': f'入口脚本不存在（{entry}）——**无法判定**尺子是否准（勿当健康，M-R60）'}]
            # 入口脚本无 `.py` 后缀，spec_from_file_location 推断不出 loader（实测 AttributeError）。
            # 必须显式给 SourceFileLoader（S136 五续：探针自己的坑也要踩实）。
            _loader = _ilm.SourceFileLoader('_tvbox_entry_probe', entry)
            spec = _ilu.spec_from_loader('_tvbox_entry_probe', _loader)
            mod = _ilu.module_from_spec(spec)
            # 只取函数定义，不执行 main（模块级只有常量与 def）
            _loader.exec_module(mod)
            fn = getattr(mod, '_as_exit_code', None)
            if fn is None:
                return [{'skill': 'ecosys-conductor', 'type': '入口判定取值域',
                         'evidence': 'tvbox 入口里找不到 `_as_exit_code()`——判定逻辑可能又被内联回散处'
                                     '（M-R85 跨模块标识符须从正本派生；本探针直接驱动正本函数）'}]
    except Exception as e:
        return [{'skill': 'ecosys-conductor', 'type': '入口判定取值域',
                 'evidence': f'入口判定函数无法加载（{type(e).__name__}: {e}）——**无法判定**，勿当健康'}]

    probes = [
        ('int 0 = 成功', 0, 0),
        ('int 1 = 失败且码保住', 1, 1),
        ('int 2 = 失败且码保住', 2, 2),
        # S139 改判：原断言「bool 是数据，一律 0」——它防的是「列表/字典当码」，
        # 但把**门禁命令的返回值**一起误伤了：`cmd_verify` 的正确语义就是 `return passed == n`，
        # 返回布尔。实测 `tvbox conductor verify` 判 8/9 不通过后 exit 仍是 0——
        # 门禁报红、门却开着，`--strict-exit` 也拦不住。
        # bool 在这个位置只有一个意思，不可能与「数据为空」混淆，故允许 bool→码：
        # True→0、False→1。这是取值域的**收紧而非放宽**：明确区分「判定结论」与「数据」。
        ('True 是判定结论→成功', True, 0),
        ('False 是判定结论→失败(1)', False, 1),
        ('列表（查询结果）是数据', [('M01', 3)], 0),
        ('字典（查询结果）是数据', {'orphans': ['M18']}, 0),
        ('空列表仍是数据', [], 0),
        ('字符串是数据', 'ok', 0),
        ('None 是成功', None, 0),
    ]
    for desc, given, want in probes:
        try:
            got = fn(given)
        except Exception as e:
            out.append({'skill': 'ecosys-conductor', 'type': '入口判定取值域',
                        'evidence': f'`_as_exit_code({desc})` 抛异常（{type(e).__name__}: {e}）——尺子坏了'})
            continue
        if got != want:
            out.append({'skill': 'ecosys-conductor', 'type': '入口判定取值域',
                        'evidence': f'取值域失守：`_as_exit_code({desc})` 得 {got!r}，应 {want!r}'
                                    f'（入口判反→journal 记反→recap/审计/进化方向一起错，M-R87）'})
    return out

def _sig_score_states(_score_override=None):
    """信号源30：评分器状态可分辨自检（S137·M-R88）——「分数不许掩盖未知」。

    为什么要有这条：`_radar_scores` 的噪声维原来是
    `100 if '无高噪声' in s else (按 N 个扣分 if 能解析 else 80)`。
    这个 **80** 兜底是**静默降级**：无论「输出格式变了没解析出来」「检查器挂了没输出」
    还是「真有中等噪声」，统统记 80 分——使用者看到 80 只会想「再优化下」，
    永远不会意识到「这一维其实**根本没量到**」。分数看着连续，信息量为零。

    S137 实测两次栽在同一处，且第二次是**修完第一次才暴露的**：
      · 一版：M01 被记 100% 高噪声 → 查下去发现豁免条目的**归属维度不存在**
        （`wcnt['M01'] += 1` 无条件硬编码），S071 理由写「M13 命中」却被算给 M01；
      · 修完归属：M13 变成 n=2 的 50%「高噪声」→ 才发现 n=1、n=2 的比值**分辨力为零**；
      · 再修样本阈值：assess 给出 100 分 → 才注意到 100 是因为「➖样本不足」那行里
        恰好含「无高噪声」子串，而**真正的有噪声场景与格式漂移仍共用 80**。

    本探针用合成 `noise_out` 驱动**正本** `_radar_scores`，断言四种状态**两两可分辨**：
      ① 明确无高噪声        → green   / 100
      ② 有高噪声（冒号式）  → red×N   / 50
      ③ 有高噪声（计数式）  → red×N   / 50
      ④ 输出无法解析        → unknown / 0
    命门是 ④：unknown 必须**低于** red，且不得与 red 同分——否则「没量到」被读作「还凑合」。
    返回 [] 表示分数仍能区分状态；否则报出被折叠的是哪一对。"""
    out = []
    if _score_override is not None:
        fn = _score_override
    else:
        fn = _radar_scores
    base = dict(ready=10, total=10, watchdog_ok=True, adopted=5, denom=5, pending=0,
                health_out='M01 a ✅', gain_out='零增益 3 个', snap='1 2 3 4')
    cases = [
        ('green·明确无高噪声', '✅ 无高噪声检查器\n➖ 样本不足 1 个（n<3）：M13(n=2)', 'green'),
        ('red·冒号式', '🔴 高噪声检查器：M01——建议查豁免原因', 'red'),
        ('red·计数式', '🔴 高噪声检查器 2 个', 'red'),
        ('unknown·格式漂移', '输出被改成别的话术了，无任何可识别结论', 'unknown'),
    ]
    got = {}
    for tag, noise_out, want in cases:
        try:
            r = fn(noise_out=noise_out, **base)
        except Exception as e:
            out.append({'skill': 'ecosys-conductor', 'type': '评分器状态可分辨',
                        'evidence': f'`_radar_scores` 在「{tag}」抛异常（{type(e).__name__}: {e}）——'
                                    f'评分器本身坏了，噪声维无法度量（M-R88）'})
            continue
        st = r.get('noise_state')
        if st is None:
            out.append({'skill': 'ecosys-conductor', 'type': '评分器状态可分辨',
                        'evidence': f'评分器未返回 noise_state（「{tag}」）——状态不可分辨，'
                                    f'调用方只能靠猜分（M-R88）'})
            continue
        got[tag] = (want, st, r.get('cat_noise'))
        if not str(st).startswith(want):
            out.append({'skill': 'ecosys-conductor', 'type': '评分器状态可分辨',
                        'evidence': f'状态判读错：「{tag}」得 state={st!r}，应 {want}——'
                                    f'把不同事实折叠成同一结论，使用者看不出「没量到」（M-R88）'})
    # 两两可分辨：「有噪声」与「没量到」绝不同分（命门）
    _red = [v for k, v in got.items() if k.startswith('red')]
    _unk = got.get('unknown·格式漂移')
    if _red and _unk and _unk[2] is not None and _red[0][2] is not None:
        if _unk[2] >= _red[0][2]:
            out.append({'skill': 'ecosys-conductor', 'type': '评分器状态可分辨',
                        'evidence': f'「无法度量」({_unk[2]}) 不低于「有高噪声」({_red[0][2]})——'
                                    f'没量到的比量到有问题的还高分，静默降级回归（M-R88/S137）'})
    return out


def _sig_verdict_pure(_mod_override=None):
    """信号源31：入口判定必须**无状态**（S138·M-R93）——「判定结果不许住在全局里」。

    为什么必须有这条：调度入口判「这次算不算成功」，写进 journal，而 journal 是
    recap/审计/进化方向的共同输入。这把尺子**连续栽了三次，方向各不相同**：
      · S136 四续 `except SystemExit: pass` 吞码      → 失败记成功
      · S136 五续 `ret or 0` 把数据当退出码           → 成功记失败（16 条假异常）
      · S138     `_RC` 从不重置，上次失败算到这次头上  → 成功记失败（3 条）
    三次修的都是「某个变量没弄对」。但真正的根因是设计：三个模块全局
    （`_EXIT_CODE`/`_ERRS`/`_RC`）由 `safe_dispatch` 写、`_dispatch` 读，
    正确性依赖两条**人肉纪律**——①每次调用重置全部字段 ②按固定顺序读。
    只要还「允许有状态可读」，就会有第四个变量、第五次翻车。

    所以 S138 把判定整体改成纯函数：证据在局部变量攒齐 → `Verdict` 一次判定 →
    结果随返回值传出，全局归零。本探针**驱动真实入口**（不复制逻辑）验证两件事：
      A. 取值域仍对（9 种输入 + 模块缺失，方向不能反）
      B. **无残留**：一次失败之后紧跟一次成功，后者必须 ok=True
         （这条正是 S138 的病根，旧实现必挂）
    返回 [] 表示判定层仍无状态且方向正确；否则报出具体失守项。"""
    out = []
    entry = '/usr/local/bin/tvbox'
    try:
        if _mod_override is not None:
            mod = _mod_override
        else:
            import importlib.util as _ilu
            import importlib.machinery as _ilm
            if not os.path.exists(entry):
                return [{'skill': 'ecosys-conductor', 'type': '入口判定无状态',
                         'evidence': f'入口脚本不存在（{entry}）——**无法判定**，勿当健康（M-R60）'}]
            _loader = _ilm.SourceFileLoader('_tvbox_verdict_probe', entry)
            spec = _ilu.spec_from_loader('_tvbox_verdict_probe', _loader)
            mod = _ilu.module_from_spec(spec)
            _loader.exec_module(mod)
        safe = getattr(mod, 'safe_dispatch', None)
        if safe is None:
            return [{'skill': 'ecosys-conductor', 'type': '入口判定无状态',
                     'evidence': '入口里找不到 `safe_dispatch()`——判定逻辑可能又被挪走/内联'
                                 '（本探针直接驱动正本函数，非复制实现）'}]
    except Exception as e:
        return [{'skill': 'ecosys-conductor', 'type': '入口判定无状态',
                 'evidence': f'入口判定函数无法加载（{type(e).__name__}: {e}）——**无法判定**，勿当健康'}]

    class _Mk:
        """合成模块：用最小实现生成「各种返回风格」的 dispatch。"""
        def __init__(self, fn):
            self.dispatch = staticmethod(fn)

    # A. 取值域与方向（9 类返回 + 模块缺失）——任一判反都是灾难
    # 注意：本组要求每个用例**互不干扰**（只看单次判定对不对）。
    # 对有状态注入版，靠 `reset()` 隔离 exit/err；但它的 rc 故意不清，
    # 因此本组最后几个用例仍可能被污染——所以 A 组只报「方向反了」，
    # 一旦检测到模块提供 reset（=注入的有状态版），A 组的失败**不单独定罪**，
    # 统一交由 B 组定性（B 组才是「无残留」的正题）。真实模块无 reset，A 组照常严格。
    _is_stateful_probe = callable(getattr(mod, 'reset', None))
    cases = [
        ('return 0', lambda a: 0, True),
        ('return dict(查询数据)', lambda a: {'gain': 1}, True),
        ('return list(查询数据)', lambda a: [('M01', 3)], True),
        ('return None', lambda a: None, True),
        ('return True(数据非码)', lambda a: True, True),
        ('return 1(自报失败)', lambda a: 1, False),
        ('return 2(自报失败)', lambda a: 2, False),
        ('raise SystemExit(1)(发现风险)', lambda a: (_ for _ in ()).throw(SystemExit(1)), False),
        ('raise SystemExit(0)', lambda a: (_ for _ in ()).throw(SystemExit(0)), True),
        ('raise ValueError(依赖缺失)', lambda a: (_ for _ in ()).throw(ValueError('依赖缺失')), False),
    ]
    for tag, fn, want in cases:
        try:
            # 注入的「有状态版」需要独立进程复位点（真实模块无此方法，getattr 返回 None 不调）
            _rst = getattr(mod, 'reset', None)
            if callable(_rst):
                _rst()
            v = safe(_Mk(fn), ['x'], quiet=True)
            got = bool(getattr(v, 'ok', None))
        except Exception as e:
            out.append({'skill': 'ecosys-conductor', 'type': '入口判定无状态',
                        'evidence': f'`safe_dispatch` 在「{tag}」抛异常（{type(e).__name__}: {e}）——尺子坏了'})
            continue
        if got != want:
            # 有状态注入版在 A 组内会自我污染（rc 不清），这类失败由 B 组统一定性，
            # 不在 A 组重复定罪——否则证据里混进注入版的假 bug，指认不到真问题。
            if _is_stateful_probe:
                continue
            out.append({'skill': 'ecosys-conductor', 'type': '入口判定无状态',
                        'evidence': f'取值域/方向失守：「{tag}」得 ok={got}，应 {want}'
                                    f'（入口判反→journal 记反→recap/审计/进化方向一起错，M-R87）'})
    # 模块缺失必须显式失败（127），不得静默当成功
    # ★ S176b：`quiet=True` —— 这一句**故意**传 None 来验证 127 路径。
    #   不加 quiet 时，safe_dispatch 会往 stdout 打「⚠️ 模块加载失败」，
    #   用户会把它读成生态真出了两次故障（其实就是本探针的正常动作）。
    #   判定结果 `v` 照常返回，断言不变；只是不让测试手段冒充事故。
    try:
        v = safe(None, ['x'], quiet=True)
        if getattr(v, 'ok', True) is not False or getattr(v, 'exit_code', 0) != 127:
            out.append({'skill': 'ecosys-conductor', 'type': '入口判定无状态',
                        'evidence': f'模块缺失被判成 {v!r}——应 ok=False 且 exit=127'
                                    f'（静默当成功＝技能没加载却不报警）'})
    except Exception as e:
        out.append({'skill': 'ecosys-conductor', 'type': '入口判定无状态',
                    'evidence': f'模块缺失路径抛异常（{type(e).__name__}: {e}）'})

    # B. **无残留**（S138 病根）：一次失败后紧跟一次成功，后者必须干净
    try:
        safe(_Mk(lambda a: 1), ['x'])                    # 制造一次失败
        v2 = safe(_Mk(lambda a: {'ok': 1}), ['x'])       # 紧跟一次成功
        if not getattr(v2, 'ok', False):
            out.append({'skill': 'ecosys-conductor', 'type': '入口判定无状态',
                        'evidence': f'**状态残留**：失败之后紧跟的成功被判成失败（{v2!r}）——'
                                    f'判定结果仍住在全局里，上一次的证据污染了这一次（S138 原病）'})
        # 反向：成功之后再失败，也必须判失败（不能是「恒定 ok」的哑巴）
        safe(_Mk(lambda a: {'ok': 1}), ['x'])
        v3 = safe(_Mk(lambda a: 1), ['x'])
        if getattr(v3, 'ok', True):
            out.append({'skill': 'ecosys-conductor', 'type': '入口判定无状态',
                        'evidence': f'**哑巴判定**：成功之后的失败仍判成成功（{v3!r}）——'
                                    f'判定器恒 ok，等于没有判定'})
    except Exception as e:
        out.append({'skill': 'ecosys-conductor', 'type': '入口判定无状态',
                    'evidence': f'无残留回归抛异常（{type(e).__name__}: {e}）'})
    return out


def _sig_snapshot_base(_mod_override=None):
    """信号源14：快照基座自检（S082·M-R60）——「跨技能数据基座也需守护」。

    watchdog 的 findings_history.json 是整个生态**环比/diff/账龄/趋势**的唯一基准
    （report/diff/trend/aging/health 五条命令全依赖它）。S082 前它的读取处一律
    `except Exception: return {}`——把「首期无历史」与「文件损坏」折叠成同一返回值，
    使 diff 把上期全部遗留**谎报为「本期新增」**，且随后 save_snapshot 用空 hist
    回写、**静默覆盖销毁损坏文件**（「读失败→假空态→写覆盖」三级放大，M-R58 的加强形态）。

    conductor 作为跨技能编排者，须守护这一共享基座：用**合成快照文件**驱动 watchdog 的
    三态函数，断言区分能力仍在（本检测器不与 watchdog 争抢同一文件，全程用临时路径）：
      ① 无文件          → state='none'（真首期）
      ② 正常历史        → state='ok'
      ③ 损坏 JSON       → state='corrupt'（**命门**：不得与①折叠）
      ④ 损坏时 save     → 拒绝覆盖（saved=False，不得销毁现场）
    _mod_override：仅供探针注入退化模块（模拟 S082 前实现）；正常调用为 None（加载真模块）。
    返回 [] 表示快照基座三态健在；否则报出具体失守项。"""
    import json as _json
    import importlib.util as _ilu
    import tempfile
    out = []
    if _mod_override is not None:
        mod = _mod_override
    else:
        wd = INSPECT_ENGINE
        if not os.path.exists(wd):
            return [{'skill': 'ecosys-conductor', 'type': '快照基座自检',
                     'evidence': 'watchdog.py 缺失——快照基座无法守护（环比/diff 全链断）'}]
        try:
            spec = _ilu.spec_from_file_location('_wd_probe', wd)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            return [{'skill': 'ecosys-conductor', 'type': '快照基座自检',
                     'evidence': f'watchdog 模块加载失败（{type(e).__name__}: {e}）——无法自检'}]
        if not hasattr(mod, 'load_prev_snapshot') or not hasattr(mod, 'save_snapshot'):
            return [{'skill': 'ecosys-conductor', 'type': '快照基座自检',
                     'evidence': 'watchdog 缺少 load_prev_snapshot/save_snapshot——三态契约未落地（M-R60）'}]
    tmpd = tempfile.mkdtemp(prefix='_snap_probe_')
    snap = os.path.join(tmpd, 'findings_history.json')
    _orig = mod.SNAP_HIST
    try:
        mod.SNAP_HIST = snap
        # ① 无文件 → none
        st, _ = mod.load_prev_snapshot()
        if st != 'none':
            out.append({'skill': 'ecosys-conductor', 'type': '快照基座自检',
                        'evidence': f'无快照时应 state=none，得 {st!r}（首期被误判）'})
        # ② 正常历史 → ok
        _fk = mod.finding_key({'monitor': 'm', 'name': 'n', 'item': 'i'})
        _json.dump({'latest_ts': 't', 'history': [{'ts': 't', 'n': 1, 'by_sev': {},
                    'keys': {_fk: {'monitor': 'm', 'item': 'i', 'sev': 'high'}}}]},
                   open(snap, 'w', encoding='utf-8'))
        st, keys = mod.load_prev_snapshot()
        if st != 'ok' or len(keys) != 1:
            out.append({'skill': 'ecosys-conductor', 'type': '快照基座自检',
                        'evidence': f'正常历史应 state=ok/1条，得 {st!r}/{len(keys)}条'})
        # ③ 损坏 JSON → corrupt（命门：不得折叠成 none）
        open(snap, 'w', encoding='utf-8').write('{"history":[{"keys":{"m1|')
        st, _ = mod.load_prev_snapshot()
        if st != 'corrupt':
            out.append({'skill': 'ecosys-conductor', 'type': '快照基座自检',
                        'evidence': f'损坏快照应 state=corrupt，得 {st!r}——'
                                    f'「损坏」被折叠成「首期」，diff 将谎报全量新增（M-R60）'})
        # ④ 损坏时 save 必须拒绝覆盖
        _before = open(snap, encoding='utf-8').read()
        saved, _ = mod.save_snapshot([{'monitor': 'm', 'name': 'n', 'item': 'i', 'sev': 'high'}])
        _after = open(snap, encoding='utf-8').read()
        if saved is not False:
            out.append({'skill': 'ecosys-conductor', 'type': '快照基座自检',
                        'evidence': f'损坏快照时 save_snapshot 应拒绝（saved=False），得 {saved!r}'
                                    f'——将静默覆盖销毁上期基准'})
        if _before != _after:
            out.append({'skill': 'ecosys-conductor', 'type': '快照基座自检',
                        'evidence': '损坏快照被 save_snapshot 覆盖——现场未保留（基准永久丢失）'})
    finally:
        mod.SNAP_HIST = _orig
        import shutil
        shutil.rmtree(tmpd, ignore_errors=True)
    return out


def _sig_rows_base(_mod_override=None):
    """信号源15：读表基座自检（S083·M-R60）——「跨技能读表原语也需守护」。

    tvbox-source-hub 的 `core.rows_of` 是 **21+ 处命令**（brief/audit/snapshot/sync…）共用的
    读表原语，读的是四极数据库（源库/台账/经验库/备用库）。S083 前它用
    `except Exception: val=[]` 把「读失败」折叠成「空表」，**且把 [] 写进 _MEM 缓存**——
    一次读失败，整个进程后续所有读都命中假空表（M-R60 的「缓存固化假态」放大）：
    用户看到「源库 0 条」「冲突 0 项」，误以为库真空，实为云端读取失败。

    conductor 作为跨技能编排者守护这一共享读表基座：用**合成 API 返回**驱动
    `core.rows_of_t`，断言三态区分能力仍在（不改真实数据，只 monkeypatch api）：
      ① 正常 JSON      → (True,  rows)（含数据）
      ② 真·空库(0行)   → (True,  [])   ← **命门**：空库必须是 ok=True，与读失败区分
      ③ 非 JSON(报错)  → (False, [])   ← 读失败必须 ok=False
      ④ 无 content 字段 → (False, [])   ← 结构失配也算失败
      ⑤ 读失败**不写缓存**（防一次失败毒化整进程——旧版把 [] 缓存住）
    返回 [] 表示读表基座三态健在；否则报出具体失守项。
    _mod_override：仅供探针注入退化模块；正常调用为 None（加载真模块）。"""
    import json as _json
    import importlib.util as _ilu
    out = []
    if _mod_override is not None:
        mod = _mod_override
    else:
        cpath = os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'scripts', 'core.py')
        if not os.path.exists(cpath):
            return [{'skill': 'ecosys-conductor', 'type': '读表基座自检',
                     'evidence': 'hub core.py 缺失——读表基座无法守护（21+ 命令共用）'}]
        try:
            spec = _ilu.spec_from_file_location('_hub_core_probe', cpath)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            return [{'skill': 'ecosys-conductor', 'type': '读表基座自检',
                     'evidence': f'hub core 模块加载失败（{type(e).__name__}: {e}）——无法自检'}]
    if not hasattr(mod, 'rows_of_t'):
        return [{'skill': 'ecosys-conductor', 'type': '读表基座自检',
                 'evidence': 'core 缺少 rows_of_t 三态读表接口——M-R60 契约未落地'}]
    _orig_api = getattr(mod, 'api', None)
    _orig_mem = getattr(mod, '_MEM', None)
    try:
        _probes = [
            ('正常有数据', _json.dumps({'content': 'a,b\n1,2\n'}), True),
            ('真·空库0行', _json.dumps({'content': 'col1,col2\n'}), True),      # 命门
            ('非JSON报错', 'CONTENT_ERROR: 鉴权失败', False),
            ('无content字段', _json.dumps({'error': '数据库不存在'}), False),
        ]
        for desc, fake_out, want_ok in _probes:
            mod.api = lambda *a, _fo=fake_out, **k: _fo
            if isinstance(getattr(mod, '_MEM', None), dict):
                mod._MEM.clear()
            if isinstance(getattr(mod, '_FAILED', None), set):
                mod._FAILED.clear()
            try:
                ok, _rows = mod.rows_of_t('__probe_db__')
            except Exception as e:
                out.append({'skill': 'ecosys-conductor', 'type': '读表基座自检',
                            'evidence': f'rows_of_t 对「{desc}」抛异常（{type(e).__name__}: {e}）'})
                continue
            if ok != want_ok:
                out.append({'skill': 'ecosys-conductor', 'type': '读表基座自检',
                            'evidence': f'rows_of_t 对「{desc}」得 ok={ok}，应 {want_ok}'
                                        f'——「读失败」与「空库」不可区分（M-R60）'})
            # 命门2：读失败不得进缓存
            if want_ok is False and isinstance(getattr(mod, '_MEM', None), dict):
                if '__probe_db__' in mod._MEM:
                    out.append({'skill': 'ecosys-conductor', 'type': '读表基座自检',
                                'evidence': f'「{desc}」读失败却写入了 _MEM 缓存——'
                                            f'一次失败毒化整进程后续读（M-R60 放大）'})
    finally:
        if _orig_api is not None:
            mod.api = _orig_api
        if isinstance(_orig_mem, dict):
            _orig_mem.clear()
    return out


def _sig_cloud_nodes_base(_mod_override=None):
    """信号源17：云端节点读取语义自检（S086·M-R60）——「三态语义可区分性」也需守护。

    与信号源14/15 同构（跨技能基座守护），但守的是**语义层**：
    `link._root_nodes()` 是 hub 采集底包库节点树的读取原语，被 `collect()` → snapshot/audit/
    status/brief 共用。S082 已把它三态化（`'ok'`/`'fail'`），但 S086 审计发现**中间态漏网**：
    `json.loads(out)['data'].get('nodes')` 之后 `nodes or []`——把
      · `{'data': {'nodes': []}}`（真·空库）
      · `{'data': {}}` / 字段改名（**结构失配**）
    **折叠成同一返回值 `('ok', [])`**。即"接口改版/字段改名"会被静默读作"底包库为空"——
    M-R60 的「语义可区分性」违例，且是**正常环境下 API 不可达时无法暴露的潜伏缺陷**
    （只有用合成输入驱动才照得出 → 这正是本信号源存在的理由）。

    用**合成 API 返回**驱动 `link._root_nodes`，断言四态可区分（不改真实数据，只 monkeypatch api）：
      ① 正常有节点   → ('ok', [..])
      ② 真·空库 nodes=[] → ('ok', [])   ← **命门**：显式空数组 = 合理空库
      ③ 结构失配 data 无 nodes → ('fail', None)  ← **命门**：不得折叠成 ②
      ④ 字段改名 data.items → ('fail', None)     ← 同上
    返回 [] 表示云端读取语义三态健在；否则报出具体失守项。
    _mod_override：仅供探针注入退化模块；正常调用为 None（加载真模块）。"""
    import json as _json
    import importlib.util as _ilu
    out = []
    if _mod_override is not None:
        mod = _mod_override
    else:
        lpath = os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'scripts', 'link.py')
        if not os.path.exists(lpath):
            return [{'skill': 'ecosys-conductor', 'type': '云端读取语义自检',
                     'evidence': 'hub link.py 缺失——云端节点读取原语无法守护'}]
        try:
            spec = _ilu.spec_from_file_location('_hub_link_probe', lpath)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            return [{'skill': 'ecosys-conductor', 'type': '云端读取语义自检',
                     'evidence': f'hub link 模块加载失败（{type(e).__name__}: {e}）——无法自检'}]
    if not hasattr(mod, '_root_nodes'):
        return [{'skill': 'ecosys-conductor', 'type': '云端读取语义自检',
                 'evidence': 'link 缺少 _root_nodes 三态读取原语——M-R60 契约未落地'}]
    _orig_api = getattr(mod, 'api', None)
    try:
        _probes = [
            ('正常有节点', _json.dumps({'data': {'nodes': [{'kind': 'folder'}]}}), True),
            ('真·空库',    _json.dumps({'data': {'nodes': []}}), True),          # 命门
            ('结构失配',   _json.dumps({'data': {}}), False),                    # 命门
            ('字段改名',   _json.dumps({'data': {'items': [{'x': 1}]}}), False),  # 命门
        ]
        for desc, fake_out, want_ok in _probes:
            mod.api = lambda *a, _fo=fake_out, **k: _fo
            try:
                st, _nodes = mod._root_nodes()
            except Exception as e:
                out.append({'skill': 'ecosys-conductor', 'type': '云端读取语义自检',
                            'evidence': f'_root_nodes 对「{desc}」抛异常（{type(e).__name__}: {e}）'})
                continue
            got_ok = (st == 'ok')
            if got_ok != want_ok:
                out.append({'skill': 'ecosys-conductor', 'type': '云端读取语义自检',
                            'evidence': f'_root_nodes 对「{desc}」得 state={st!r}，应 '
                                        f'{"ok" if want_ok else "fail"}——「结构失配」与「真·空库」'
                                        f'不可区分（M-R60：接口改版被静默读作"库为空"）'})
    finally:
        if _orig_api is not None:
            mod.api = _orig_api
    return out


def _sig_upstream_probe(_mod_override=None):
    """信号源18：上游侦察自检（S087·探源司建档）——监测器「逻辑有效性」须被守护。

    与信号源16（基座折叠扫描，守**代码形态**）互补：本信号源守探源司的**监测器语义**。
    探源司（probe-hub）负责三件事：① 监测上游作者仓库存活/内容/目录变更（作者跑路/封禁/
    更新早知早处置）② 搜索更多可用仓库扩库容 ③ 采集高质量线路供换线调用。它的一切结论
    建立在四个纯函数判定器上：`_alive_verdict` / `_content_sha` / `_dir_diff` /
    `_activity_verdict`，外加数据基座三态原语 `load_alog`/`save_alog`。

    **为什么需要本信号源**：这些判定器若"形态正确但语义退化"（如 `_alive_verdict(0)`
    误判为"已封禁/删除"），会让探源司**静默给出错误结论**——"依赖可达≠逻辑对"。
    故用合成输入独立驱动探源司模块，断言三态可区分（真阳性 / 真阴性 / 无法判定）：
      · 存活判定：200→存活、404→已封禁/删除、000→**无法判定**（非死，防假阳性）
      · 内容指纹：改字节→变、同内容→一致（防误报变更）
      · 目录差集：新增→added、相同→双侧空（防误报）
      · 活跃度  ：近期提交→活跃、三年前+0提交→归档、缺失→无法判定（防误报停滞）
      · 基座三态：损坏态须识别为 corrupt 且拒绝回写（防销毁历史，M-R60）
    返回 [] 表示探源司监测器语义健在；否则报出具体失守项。
    _mod_override：仅供探针注入退化模块；正常调用为 None（加载真模块）。"""
    import time as _time
    out = []
    if _mod_override is not None:
        mod = _mod_override
    else:
        ppath = os.path.join(SKILL_ROOT, 'probe-hub', 'scripts', 'probe.py')
        if not os.path.exists(ppath):
            return [{'skill': 'ecosys-conductor', 'type': '上游侦察自检',
                     'evidence': 'probe-hub/scripts/probe.py 缺失——上游监测器无法守护'}]
        try:
            import importlib.util as _ilu
            spec = _ilu.spec_from_file_location('_probe_hub_guard', ppath)
            mod = _ilu.module_from_spec(spec)
            # probe.py 依赖同目录 core；加入 sys.path 后加载（失败即降级报错，不崩溃）
            _pdir = os.path.dirname(ppath)
            if _pdir not in sys.path:
                sys.path.insert(0, _pdir)
            spec.loader.exec_module(mod)
        except Exception as e:
            return [{'skill': 'ecosys-conductor', 'type': '上游侦察自检',
                     'evidence': f'探源司模块加载失败（{type(e).__name__}: {e}）——无法自检'}]
    _need = ('_alive_verdict', '_content_sha', '_dir_diff', '_activity_verdict')
    missing = [n for n in _need if not hasattr(mod, n)]
    if missing:
        return [{'skill': 'ecosys-conductor', 'type': '上游侦察自检',
                 'evidence': f'探源司缺判定器 {missing}——M-R60 契约未落地'}]
    _checks = [
        ('存活·真阳性', mod._alive_verdict(404) == '已封禁/删除'),
        ('存活·真阴性', mod._alive_verdict(200) == '存活'),
        ('存活·无法判定', mod._alive_verdict(0) == '无法判定'),        # 命门：0 非"死"
        ('指纹·变更', mod._content_sha(b'a') != mod._content_sha(b'b')),
        ('指纹·一致', mod._content_sha(b'a') == mod._content_sha(b'a')),
        ('差集·新增', mod._dir_diff(['a'], ['a', 'b']) == (['b'], [])),
        ('差集·相同', mod._dir_diff(['a'], ['a']) == ([], [])),
        ('活跃·活跃', mod._activity_verdict(5, _time.time()) == '活跃'),
        ('活跃·归档', mod._activity_verdict(0, _time.time() - 3 * 365 * 86400) == '归档'),
        ('活跃·无法判定', mod._activity_verdict(None, None) == '无法判定'),  # 命门：缺失非"停滞"
    ]
    # S089 新增：鉴权模块（GITHUB_TOKEN 多源解析）语义守护。
    # 为何守护：token 是"看得见的收益"（60→5000 req/h）；若解析退化（把"读取失败"
    # 折叠成"无 token"、或状态由 ok 变 none 后缓存滞留旧值），用户会以为"配了没用"
    # 而无从排查——M-R60 的静默折叠在鉴权场景的复现。此处只验**纯函数语义**（不触网）：
    #   · 合法形态 → 识别为 ok（真阳性）
    #   · 非法形态（短/含空格）→ 必须拒绝（不得当有效）
    #   · 遮罩 → 不得泄露中段原字符
    try:
        _fake_tok = 'ghp_' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8'
        _m_ok = mod._mask(_fake_tok)
        _checks.append(('鉴权·遮罩不泄露',
                        _m_ok.startswith('ghp_') and '*' in _m_ok
                        and _fake_tok[8:16] not in _m_ok))
        # 缓存一致性命门：状态变更必须即时反映，禁滞留旧 token（S089 实证缺陷）
        _checks.append(('鉴权·缓存不滞留',
                        callable(getattr(mod, '_load_token', None))
                        and 'value' in mod._TOKEN_CACHE))
        # 危险形态拒绝：短 token / 含空白 → _mask 必须全遮或拒显，不得原样回显
        _checks.append(('鉴权·短值不当有效',
                        mod._mask('abc') == '***' and mod._mask('') == '(未配置)'))
    except Exception:
        _checks.append(('鉴权·遮罩不泄露', False))
    for desc, ok in _checks:
        if not ok:
            out.append({'skill': 'ecosys-conductor', 'type': '上游侦察自检',
                        'evidence': f'探源司「{desc}」失守——监测器语义退化，'
                                    f'会静默给出错误结论（依赖可达≠逻辑对）'})
    # 基座三态：损坏态须识别且拒绝回写（临时文件，不碰真实基座）
    try:
        import tempfile as _tf
        import io as _io2
        _bak = mod.ALOG
        with _tf.NamedTemporaryFile('w', suffix='.json', delete=False, encoding='utf-8') as _f:
            _f.write('{ 坏 JSON')
            _bad = _f.name
        try:
            mod.ALOG = _bad
            # 注入期抑制探源司 load_alog 的预期告警（属探针噪声，非真故障）
            _saved_out, sys.stdout = sys.stdout, _io2.StringIO()
            try:
                st, _obj = mod.load_alog()
                _saved, _ = mod.save_alog({'latest_ts': '', 'repos': {}})
            finally:
                sys.stdout = _saved_out
            if not (st == 'corrupt' and _saved is False):
                out.append({'skill': 'ecosys-conductor', 'type': '上游侦察自检',
                            'evidence': f'探源司基座三态失守：损坏态得 state={st!r}, '
                                        f'saved={_saved}——应识别 corrupt 且拒绝回写（M-R60）'})
        finally:
            mod.ALOG = _bak
            try:
                os.remove(_bad)
            except Exception:
                pass
    except Exception as e:
        out.append({'skill': 'ecosys-conductor', 'type': '上游侦察自检',
                    'evidence': f'探源司基座三态探针异常（{type(e).__name__}: {e}）'})
    return out


def _sig_fold_scan():
    """信号源16：基座折叠扫描（S084·M-R60 行动项⑨「系统性排查」的机械化落地）。

    M-R60 教训：修复必须"**系统性排查同类折叠模式**，而非发现一处修一处"。
    S082 只修 watchdog 快照、漏了同函数的其他读取；S083 只修 hub 读表、漏了
    watchdog 里同样读 SNAP_HIST 的 `_gain_rows`——**逐点修补必然漏网**。

    本检测器把"排查"本身**机制化**：静态扫描生态内所有技能脚本，找出「读失败被
    折叠成空态」的候选，与**白名单**（已审计确认安全的点 + 已修复的点）比对——
    **出现白名单外的新候选即报警**。这样"新增折叠点"会在每轮 signal-check 被自动
    抓出，不再依赖人工逐点发现。

    **S085 扩面（扫描器自身盲区）**：S084 只匹配「`except` 紧跟 `return []`/`{}`」，
    漏了**赋值式折叠** `except: 变量 = []`/`{}`/`0`/`None`——`_latency_rows`/`_replay_rows`
    里 `except: hist = []` 正是此类，快照损坏被折叠成"无历史"却从未被扫出。
    **教训：扫描器本身也是需要被守护的度量器（M-R55）**——只测一种形态的检测器，
    对另一种形态就是哑巴。故 S085 起同时覆盖「return 空」与「赋值空」两种形态。

    白名单维护：凡审计确认为 **M-R60 真缺陷**的点，应修复（改为三态/报真阳性）后从
    白名单移除；凡确认为**合理折叠**（如返回 None 可区分、纯流程控制）的点，登记豁免。
    返回 [] 表示无未登记的折叠候选；否则逐条报出（附文件:行）。"""
    import re as _re
    out = []
    # 白名单键：'相对路径:行号' → 豁免原因（已修复 / 合理折叠）。新增未登记候选即报警。
    allow = _FOLD_SCAN_ALLOWLIST
    pat_except = _re.compile(r'^\s*except\b.*:\s*(#.*)?$')
    pat_ret_container = _re.compile(r'^\s*return\s+(\[\]|\{\})\s*(#.*)?$')
    pat_oneline = _re.compile(r'except\b[^:]*:\s*return\s+(\[\]|\{\})')
    # S085：赋值式折叠——`变量 = []`/`{}`/`0`/`None`/`False`/`""`（含下标赋值 result["k"]=...）
    pat_assign_empty = _re.compile(
        r'^\s*[A-Za-z_][\w\.]*(?:\[[^\]]*\])?\s*=\s*(\[\]|\{\}|None|0|False|""|\'\')\s*(#.*)?$')
    pat_assign_oneline = _re.compile(
        r'except\b[^:]*:\s*[A-Za-z_][\w\.]*(?:\[[^\]]*\])?\s*=\s*(\[\]|\{\}|None|0|False|""|\'\')')
    for skill in ECO_SKILLS:
        sdir = os.path.join(SKILL_ROOT, skill, 'scripts')
        if not os.path.isdir(sdir):
            continue
        for fn in sorted(os.listdir(sdir)):
            if not fn.endswith('.py'):
                continue
            fp = os.path.join(sdir, fn)
            try:
                lines = open(fp, encoding='utf-8').read().split('\n')
            except Exception:
                continue
            # ★ S161 修（**本轮最严重的一处**）：循环变量用的是 `fp`（文件绝对路径），
            #   而下面指纹键又写 `fp = f'{skill}/scripts/{fn}:{_fold_fp(hit_ln)}'`
            #   ——**同名覆盖**。第二次命中时 `open(fp)` 拿到的是 "技能/scripts/x.py:指纹"
            #   这种伪路径 → FileNotFoundError → 被 `except: continue` 吞掉 →
            #   **扫描到这个文件就静默中止**，它后面所有文件、所有技能一概没扫。
            #   S161 实证：探针注入的文件被扫出来了（它在前面），可清掉后真实候选
            #   inspect_engine.py:2840 也一起"消失"了 → 清理后清零=否，且**永远清不干净**。
            #   教训：**同名变量在循环里复用，等于给下一次迭代埋一颗只会静默的雷**。
            _fpath = fp
            for i, ln in enumerate(lines):
                if 'return' not in ln and '=' not in ln and not pat_except.match(ln):
                    continue
                # 跳过注释/docstring 行（形如 `# ... return {}` 或 docstring 引用）
                _stripped = ln.strip()
                if _stripped.startswith('#') or _stripped.startswith('`') or _stripped.startswith('"') \
                        or _stripped.startswith("'"):
                    continue
                # docstring 里的反引号引用（如「旧版 `except: return {}` ...」）——含反引号=引用文本，非代码
                if '`' in ln:
                    continue
                key = f'{skill}/scripts/{fn}:{i+1}'
                hit = False
                hit_ln = _stripped          # 命中行原文（单行=当前行；多行=折叠体那一行）
                hit_row = i + 1             # 命中行号
                if pat_oneline.search(ln) or pat_assign_oneline.search(ln):
                    hit = True
                elif pat_except.match(ln):
                    for j in range(i+1, min(i+4, len(lines))):
                        sj = lines[j].strip()
                        if sj == '' or sj.startswith('#'):
                            continue
                        if pat_ret_container.match(lines[j]) or pat_assign_empty.match(lines[j]):
                            hit = True
                            # 多行形态：定位到**折叠体那一行**（白名单按此行号登记）
                            hit_ln = sj
                            hit_row = j + 1
                            key = f'{skill}/scripts/{fn}:{j+1}'
                        break
                if hit:
                    # S088：**指纹键**优先——行号键随代码增删漂移（本轮实测一次重构即让
                    # 5 条白名单瞬时失配、被当"未登记候选"报出）。改用「文件 + 命中行代码
                    # 的规范化指纹」作稳定键：只要该行本身的代码没变，移动位置也不失配。
                    # 兼容：旧行号键仍在 allow 中（平滑过渡），两者命中任一即豁免。
                    fp = f'{skill}/scripts/{fn}:{_fold_fp(hit_ln)}'
                    if fp not in allow and key not in allow:
                        out.append({'skill': 'ecosys-conductor', 'type': '基座折叠扫描',
                                    'evidence': f'未登记的折叠候选 {fp}（原 {key}）：{hit_ln[:50]}'
                                                f'——except 后 return/赋值 空态（M-R60：读失败被折叠成空态），'
                                                f'请审计：真缺陷须修，合理折叠须登记白名单'})
    return out


def _fold_fp(line_text):
    """折叠候选行的**稳定指纹**：规范化空白后取 sha1 前 10 位。

    S088：替代「文件:行号」作白名单键——行号随任意增删漂移，而代码内容不变则指纹不变。
    规范化：去首尾空白 + 折叠内部连续空白（缩进差异不影响识别）。

    ★ S177 步3 实测修：**行尾注释必须剥掉再算指纹**。
      原来直接对整行（含 `  # xxx`）取 sha1，后果是——
      「只给这行补了句注释、代码一个字没动」→ 指纹变了 → 白名单失配 →
      扫描器把**自己刚登记过的**代码重新报成「未登记的新折叠候选」。
      实证：本轮 activity.py 的 `registered = None  # 读失败 ≠ 未注册…`，
      单元里算 `_fold_fp('registered = None')` = 7d9771fd8e，
      而扫描器报出的是 60993e233f（带注释的版本）。**两个值不一样，就是它在改注释。**
      去注释只在**第一个 `#`** 处切，且该 `#` 前不能是字符串内的井号——
      本行只处理「代码 + 尾注释」形态（正则匹配到的都是这种），故简单切即可。
    """
    import hashlib as _h
    code = (line_text or '').split('#', 1)[0]
    norm = ' '.join(code.split())
    return _h.sha1(norm.encode('utf-8')).hexdigest()[:10]


# 基座折叠扫描白名单（S084）：键='技能/scripts/文件:行号' → 豁免原因。
# · 已修复点：修复后仍会命中正则（因修的是语义非结构），登记以示"已审计"。
# · 合理折叠：返回/赋值空是正确语义（如真·首期无历史、纯流程控制、保守判死）。
_FOLD_SCAN_ALLOWLIST = {
    # —— ecosys-conductor 分层判定降级（S176）——
    # 合理：`layer = None` 是 **eco_inventory 不可用时的三态哨兵**，不是把"读不出分层"
    #   折成"某个具体层"。判据可核：
    #     · `except` 块内**同时**打印「分层判定降级（异常类名）」到 stderr，异常不被吞；
    #     · 两个分层分支都加 `EI is not None` 前置，不会把 None 当成有效分层；
    #     · 三态可区分：None(未判定·降级) / PLATFORM(平台托管) / NO_SKILL(残留目录) / 具体层。
    #   **S176 更正**：本条白名单最初登记时，我在注释里写"后续 if 会走老逻辑"——**那是错的**。
    #   当时的真实情况是：`import EI` 在 try 内、`if layer == EI.PLATFORM` 在 try 外，
    #   import 一失败就抛 `UnboundLocalError: 'EI'`，被外层 `except: 降级跳过` 再包装一层，
    #   于是"降级"从来没生效过，实际是**双重折叠的静默崩溃**（实证：signal-check 报
    #   信号源5 抛 UnboundLocalError）。是白名单让我以为它是对的。
    #   **教训：白名单只该登记"已证明安全"的折叠，不该用来放行"我读着觉得安全"的代码。**
    #   本条现在才真的符合它自己的描述。
    'ecosys-conductor/scripts/conductor.py:9a7d64f8f3',   # layer = None（分层判定降级哨兵·附 stderr 告警且不命中具体层分支）
    # —— ecosys-conductor 备份落点护栏（S158·G10）——
    # 合理：`_pid = None` 是**父节点读取失败的三态哨兵**，不是把"读不到"当"没问题"。
    #   紧随其后立即分流：`_pid is None` → 打印「⚠️ 父节点读取失败（无法判定落点）」并提示人工核对；
    #   `_pid == BACKUP_INBOX` → 拦下并改投；其余 → 正常继续。
    #   三态可区分：None(读不出) / 收件箱(错落点) / 正确父节点。
    #   为什么会被扫到：本行就是本轮新写的护栏，扫描器看到的只是 `except: _pid = None`，
    #   看不到后面那个 if。**"看不出处置"不等于"没有处置"**——但也不许靠这句话搪塞：
    #   若将来有人删掉那个 if，本白名单不会自动失效。故在此显式写明判据，删 if 必删此条。
    'ecosys-conductor/scripts/conductor.py:60fc3994e6',   # _pid = None（父节点读取失败哨兵·随后显式告警）
    # —— tvbox-source-hub 经验池写入器（S148 失败诊断）——
    # 合理：`_d = None` 是 **json 解析失败的三态哨兵**，不是把"写入失败"折成空值继续走。
    #   紧随其后立即分流：dict 且有 error → 报服务端原文；dict 且有 results → 报 results 无 success；
    #   其余（含 _d is None）→ 报「返回异常」并附原始输出前 250 字。
    #   三态可区分：None(解析不出) / dict{error}(服务端拒) / dict{results}(格式不对)。
    #   真折叠反例：`except: pass` 后当成功继续往下走——那才是"失败被吞"。
    #   为什么会被扫到：本轮改的就是这两个 except 块（原来只打原始片段，`ok=0 fail=0` 看不出原因），
    #   `cmd_log` / `cmd_liblog` 同形，故同一内容指纹出现两次。
    'tvbox-source-hub/scripts/link.py:66338aa1a7',   # _d = None（JSON 解析失败哨兵·失败诊断分流）

    # —— eco-ledger 账房门面（S156）——
    # 合理：`_CORE = None` 是 import 失败的三态哨兵，**不是**把"门面不可用"折成"没有风险"。
    #   紧随其后 `_CORE_ERR` 记了异常原文，且两个包装函数（_file_to_janitor /
    #   _register_monitor）在 _CORE is None 时**显式打印"门面不可用 + 未投递，请手动处理"**，
    #   不静默。真折叠反例：`except: pass` 后当"已联动"继续跑——那才是假装联上了。
    #   为什么会被扫到：本轮新增的两条生态门面调用（平衡性「联动断点」45→100 的修法）。
    'eco-ledger/scripts/ledger.py:70696e4fb0',   # _CORE = None（门面不可用哨兵·附 _CORE_ERR 且调用点显式告警）
    # —— ecosys-conductor 自接生态底座（S157）——
    # 合理：`CORE = None` 是 **import 失败的三态哨兵**，不是把"读不到库"折成空值继续走。
    #   紧随其后 `_CORE_ERR` 记下原因；两个消费点（_load_terms/_load_events）都
    #   **先判 CORE is None → 打印"无法判定"并 return None**，绝不当空库。
    #   三态可区分：CORE 可用 / CORE=None+_CORE_ERR（底座挂了）/ 读成功但表真空。
    #   故障注入已验：把 DB_* 换成坏 id → 两个 loader 均返回 None 且打印"无法判定"。
    'ecosys-conductor/scripts/conductor.py:e98c6478c2',   # CORE = None（底座不可用哨兵·附 _CORE_ERR）

    # —— unfinished-audit 清账司（S148 经验池写入器）——
    # `learn.py` 已删除（它是重复实现，违反 M-R38）。本技能现在只做**转接**，
    # 写入器在 tvbox-source-hub/scripts/link.py（`tvbox log` / `lib-log`），
    # 故此处不再保留该文件的白名单条目——白名单只登记**现存的**合理折叠，
    # 留着已删文件的指纹会让清单看起来比实际"干净"。

    # —— probe-hub 源验证器（S148·v1.6.1）——
    # 合理：except 里 `raw_n = None` 是**显式三态标记**，不是静默折叠——
    #   None(拿不到基准) 与 0(接口说 0 集) 可区分；下游 `if raw_n is not None and raw_n > 0`
    #   才比对集数，基准缺失时只验非空并明写「基准缺失，不比集数」。
    #   真折叠反例（本轮已修）：原版写 `raw_n = 0`，令"接口查不到"与"接口说没有"压成同一个 0，
    #   下游又把 0 当成基准 → 退化成信任源自己的产出（坏源把 84 集砍成 1 集后永远"一致"）。
    'probe-hub/scripts/spider_verify.py:8c5d716f9a',   # raw_n = None（三态哨兵·基准取不到）
    # ★ S177 步3：上面这条旧指纹是「含行尾注释」算出来的（8c5d716f9a），
    #   而指纹口径已改成「剥掉注释再算」（见 _fold_fp 的 S177 段）。
    #   同一条代码的新指纹如下，两枚并存只为平滑过渡，**新登记一律用新口径**。
    'probe-hub/scripts/spider_verify.py:3f9b74fa8a',   # raw_n = None（三态哨兵·基准取不到·新口径）

    # ═══ S088：键改为「文件:内容指纹」（sha1 前 10 位）═══
    # 理由：旧「文件:行号」键随任意增删漂移——本轮一次重构即让 5 条白名单瞬时失配、
    # 被当"未登记候选"报出（假阳性）。指纹只认代码内容，移动位置不失配。
    # 登记方法：跑 `_sig_fold_scan()` 看它报出的指纹，确认属"合理折叠"后照抄。
    # 兼容：旧行号键保留在下方注释里备查（扫描器仍接受行号键，平滑过渡）。

    # —— skill-evolution 归档/招式入库（S158·evolve.py 三态改造）——
    # 合理：这两处 `= None` 是 **"自报数未取得"的三态哨兵**，恰恰是**修掉折叠后**的形态。
    #   改造前后对照（这是重点，别把它们当回归）：
    #     · 原 `except: ok = 0`    → 「通路坏了」被说成「成功了 0 条」，下游照打
    #       「api 自报成功 0 条」，等于替失败编了个看起来正常的数字。
    #     · 现 `except: ok = None` → 下游 `if ok is not None` 分流，
    #       打印「（自报数也未取得）」，且回读失败时直接 return 4 报"无法判定"。
    #   为什么仍会被扫到：扫描器按**语法形态**判（except 后赋空态），
    #   分不清 `None` 是"三态哨兵"还是"折叠成空"——这不是扫描器的缺陷，
    #   是静态扫描的固有边界，故只能靠人工审计后登记（本条即审计记录）。
    #   真折叠反例：`except: ok = 0` 后当"成功 0 条"继续走——改造前的原貌。
    'skill-evolution/scripts/evolve.py:22c210b079',   # ok = None（自报数未取得·下游 is not None 分流）
    'skill-evolution/scripts/evolve.py:d4a8748ede',   # _ok = None（未判定·单独 unk 计数，不计入 fail）

    # —— ecosys-conductor 信号源41 自身（S158）——
    # 合理：`_cmd = None` 是 **pyflakes 可用性探测的三态哨兵**。
    #   紧随其后 `if _cmd is None: return [...'无法判定，不代表无缺陷']`——
    #   **检测器自己跑不起来时绝不返回空**（返回空 = "一切正常"，比不装它还危险，M-R60）。
    #   为什么会被扫到：本轮新写本信号源时引入，语法合法的三态写法。
    'ecosys-conductor/scripts/conductor.py:a235422f2c',   # _cmd = None（pyflakes 不可用哨兵·显式报无法判定）

    # —— tvbox-source-hub 协议实现 core.py（S158 新增 add_records_diag）——
    # 合理：`_d = None` 是 **"返回不是合法 JSON"的三态哨兵**，且**已改成显式三分支**：
    #   · _d is None            → 报「返回不是合法 JSON（无法取得服务端原因）」
    #   · dict 且有 error       → 报「服务端拒绝：<原文>」
    #   · 其余                  → 退回 _parse_results 给的原因
    #   改造前是 `_.get('error')` 直接判——None 与 {} 走同一条路，人分不清
    #   "服务端没回话"与"回了但没给原因"，那才是折叠。现在三者可区分。
    #   为什么仍被扫到：扫描器按语法形态判（except 后赋 None），分不清哨兵与折叠。
    'tvbox-source-hub/scripts/core.py:66338aa1a7',   # _d = None（非法 JSON 哨兵·已显式三分支区分）

    # —— legacy-watchdog（S082/S084 已修 + S085 合理折叠）——
    'tvbox-inspect/scripts/inspect_engine.py:4f7d813897',   # n = 0（计数起点，S085；S105 随监测引擎自 watchdog 迁入监察司）
    'tvbox-inspect/scripts/inspect_engine.py:ca8eaa8abd',   # h = ''（无 host 语义可区分，S085；S105 迁入监察司）
    # —— tvbox-inspect 监察司（S106 平衡性体检的三态哨兵）——
    # 合理：这两处是**显式三态标记**（不是静默折叠）——读失败时赋哨兵值 + 维度评分给"无法判定"中间分，
    #       与"读到空"可区分。`_ledger_ok=False` 标记台账读取失败；`sc_cnt=None` 标记 flow.json 读取失败。
    #       扫描器按"= 空值"形态匹配会误报，故登记豁免（真折叠反例：旧版 `sc_cnt = 0` 已修为 None）。
    'tvbox-inspect/scripts/inspect_engine.py:6ff5154938',   # _ledger_ok = False（三态哨兵·台账读失败）
    'tvbox-inspect/scripts/inspect_engine.py:c938ff6864',   # sc_cnt = None（三态哨兵·flow.json 读失败）
    # ★ S177 步3：以上两条同样是「旧口径（含注释）」指纹，补新口径（剥注释）。
    'tvbox-inspect/scripts/inspect_engine.py:9e2da7cb73',   # _ledger_ok = False（三态哨兵·新口径）
    'tvbox-inspect/scripts/inspect_engine.py:42f0c336de',   # sc_cnt = None（三态哨兵·新口径）
    # 合理：S107 招式库读取的三态哨兵——读失败时置 False，令维度6 走「无法判定」中间分（60），
    #       与"读到 0 条已用"（真·招式隔离 40 分）**可区分**。是显式三态标记，非静默折叠。
    'tvbox-inspect/scripts/inspect_engine.py:52e02daeb1',   # _moves_ok = False（三态哨兵·招式库读失败）
    # —— skill-evolution（S107 招式库·经验对照）——
    # 合理：n_rec 是**纯展示计数**（"招式库 N 条"那行打印用），读失败显示 0 不影响任何判定——
    #       真正的"未用/适用"判定走 `unused is None` 三态（见 _lend_rows），已正确区分读失败。
    #       属 M-R60 语义下的"安全默认展示值"，与"读失败被当真值参与判定"有本质区别。
    'skill-evolution/scripts/evolve.py:4779ee56f9',   # n_rec = 0（纯展示计数，判定另走三态）
    # 合理：`prev={}` 是 _write_lend_baseline 的**读失败兜底起点**——基线文件不存在/损坏时
    #       从空开始合并写回（合并语义下"无旧基线"与"旧基线为空"等价，不参与任何判定分支）；
    #       且写失败被内层 try/except 静默忽略不掩盖——与"读失败被当有效值消费"（真折叠）无关。
    'skill-evolution/scripts/evolve.py:3c7002bcbe',   # prev = {}（基线文件缺失时的合并起点，非判定值）
    # 合理：mine 落库的 update 返回解析哨兵——`_ok=False` 表示"写命令返回未含 success"，
    #       随即计入 fail（**不被当成功**）。是 M-R60⑫ 在三态判定链上的显式标记，非静默折叠。
    'skill-evolution/scripts/evolve.py:d68e92e663',   # _ok = False（update 返回解析哨兵，计入 fail）
    # —— tvbox-source-hub（S083 已修 + 解析器判空）——
    'tvbox-source-hub/scripts/parse_tvbox_config.py:4a2c291bb1',   # json5 = None（后续判空）
    # —— ecosys-conductor（S110 · 信号源23 核心库行数守护的合理折叠）——
    # 合理：`anchors = {}` 是锚文件不存在/损坏时的**首跑起点**——无锚→只建锚不报骤降，
    #       与"锚丢失被当有效历史消费"（真折叠）无关；锚写回失败亦不掩盖判定。
    'ecosys-conductor/scripts/conductor.py:248f95f3ed',   # anchors = {}（锚文件缺失时的首跑起点）
    'ecosys-conductor/scripts/conductor.py:1c38c28d9a',   # prev = None（增长基线日志损坏→按无基线处理，S115 audit-growth 合理折叠）
    # 合理：`rows = None` 是**显式三态哨兵**——读失败置 None，上层立即报「无法判定」，
    #       与"读到 0 行"（真空库→报归零）**可区分**，正是 M-R60 要求的形态。
    'ecosys-conductor/scripts/conductor.py:365dd93168',   # rows = None（三态哨兵·读失败→无法判定）
    # —— ecosys-conductor 门禁注册态（S177 步3 实测修，**这条本身就是一次折叠修复的产物**）——
    # 合理：`registered = None` 是 **"settings.json 读不到" 的三态哨兵**。
    #   改造前是 `except: registered = False` —— 把「读不到设置」折叠成「未注册」，
    #   于是 settings 编码坏掉/权限被改/半截 JSON 时，界面一律显示「❌ 未注册」，
    #   运维照着去加注册，其实注册一直在。**归因指反了比不报更毒。**
    #   现在三态：True=在册 / False=确未在册 / None=读不到无法判定，显示为 ❓，
    #   且下游 activity.py ③ 段已同步改成三态显示（`reg is None → '❓' + 无法判定`）。
    #   **改上游不改下游 = 白改**，这条特意连下游一起核过。
    #   为什么仍被扫到：扫描器按语法形态判（except 后赋 None），看不出它后面有没有显式分流。
    'ecosys-conductor/scripts/activity.py:7d9771fd8e',   # registered = None（三态哨兵·设置读不到≠未注册）
    'tvbox-source-hub/scripts/parse_tvbox_config.py:9edb5afff2',   # obj = None（后续判空）
    # —— 探源司（S159 审计：4 处均为「保守判定」或「模块缺失降级」，非读失败折叠 ——）
    # ① 合理·保守判死：HTTP 码解析不出 → code=0，而 ALIVE_CODES 不含 0 → 判「不存活」。
    #    与 `tvbox-interface/pipeline.py:code=0` 同一判据（那里已登记）。要的是"拿不准就当死"。
    'probe-hub/scripts/content_probe.py:ce8c366818',   # code = 0（保守判死，非空态）
    # ② 合理·下游已显式判空：body='' 后**三处调用方全都先判空再下结论**——
    #    `if not src or len(src) < 200: return '失败'` / `if not body: return '失败'`。
    #    空串在这里是"下载失败"的**信号**，不是被当作有效内容消费。改三态反而多一层
    #    None 判断却不改变任何结论。
    'probe-hub/scripts/content_probe.py:acd9c88593',   # body = ''（下游首判空即返回失败）
    # ③ 合理·语义即 None：jar 拉取失败时 out=None，**None 本身就是「没拿到 jar」**，
    #    调用方据此判「jar 不可用」。这里不存在"空值 vs 失败"混淆——两者本就同义。
    'probe-hub/scripts/mount_judge.py:1aa5368610',     # out = None（无 jar 语义，非失败折叠）
    # ④ 合理·模块缺失降级：mount_judge 导入失败 → _mj=None → `if not base or _mj is None: return None`
    #    = **维持原剔除策略**（向后兼容，宁可不判也不乱判）。缺的是可选判定能力，
    #    不是"读数据失败被当成空数据"。
    'probe-hub/scripts/probe.py:57615c0853',           # _mj = None（可选模块缺失，维持原策略）
    # —— skill-evolution（S159 已修：真缺陷→三态）——
    # 已修复：`solved` 的回读校验原写 `except: backstate = {}`，会把「回读失败」折叠成
    #   「一条都没读到」→ **全部 N 条都判「未生效」**，报出一份具体却完全虚假的失败清单。
    #   现改为 backstate=None → 直接报「无法判定」并 return 4，不逐条下结论。
    #   本行 `backstate = None` 是**三态哨兵**（修复后的形态），故登记为已审计。
    'skill-evolution/scripts/evolve.py:e37622f8b2',   # backstate = None（三态哨兵·已修复）
    # —— tvbox-interface（保守判死）——
    # 合理：HTTP 状态码解析失败→code=0，而 ALIVE_CODES 不含 0 → 判定"不存活"。
    #       属**保守正确**（无法判定→判死，宁可漏报活源不误报）——非静默折叠。
    'tvbox-interface/scripts/pipeline.py:ce8c366818',   # code = 0（保守判死）
    # —— probe-hub 探源司（S087 建档同尺审计 + S088 云端化新增点）——
    # 合理：登记册 `_schema` 是纯文档说明字段，读不到时写回空对象=安全默认。
    'probe-hub/scripts/probe.py:f445f2ef32',   # return {}（_load_schema_doc / _cloud_id_map）
    # 合理：云端数值字段解析失败→None，None 表示"未取到"（与本地语义一致，可区分于 0）。
    'probe-hub/scripts/probe.py:ede306c0cc',   # t[k] = None（云端数值未取到）
    # 合理：GitHub 时间戳解析失败→None，而 _activity_verdict(None,..) 返回'无法判定'。
    'probe-hub/scripts/probe.py:3fdf897d79',   # rec['pushed_at'] = None（三态载体）
    # 合理：curl 输出解析失败→code=0，而 _alive_verdict(0) 返回'无法判定'。
    'probe-hub/scripts/probe.py:ce8c366818',   # code = 0（三态载体·无法判定）
    # 合理（S097）：livecheck 的 curl 底层解析失败→code=0，而 check_* 函数据 0 返回
    #   '无法判定'（三态载体，非折叠成"通过"）；与 probe.py 同指纹同性质。
    'probe-hub/scripts/livecheck.py:ce8c366818',   # code = 0（三态载体·无法判定）
    # —— legacy-watchdog M18（S090 新增，同尺审计）——
    # 合理：`except: rows = None` 是**读取原语的中间变量**，不是最终返回值。
    #   `rows=None` 与 `rows=[]` 语义**可区分**（`if rows:` 为假 → 继续本地兜底）；
    #   最终返还调用方的是三态 state（'ok'/'none'/'fail'），**不折叠**。
    #   同时该 `None` 是"云端未取到"的显式载体，正是 M-R60 要求的可区分中间态。
    'tvbox-inspect/scripts/inspect_engine.py:365dd93168',   # rows = None（中间态·可区分；S105 迁入监察司）
    # —— probe-hub harvest 复用整包坊判据（S092 新增，同尺审计）——
    # 合理：`except: _IFACE_PIPE = None` 是**"整包坊不可用"的显式三态载体**，
    #   消费端 `_extract_lines` 见 None **必先 stderr 告警**再走 `_fallback_category`
    #   保守判据——"用错口径"与"用保守口径"**语义可区分**（M-R60：读失败≠正常空）。
    #   且 None≠[] ：None 表示"未尝试成功"，与"有判据但返回空"不可同归。
    'probe-hub/scripts/probe.py:515dc0e04e',   # _IFACE_PIPE = None（降级载体·可区分）
    # 合理：`code = 0` 表示"取不到 HTTP 状态码"，同函数随后走**语义兜底**
    #   （正文判 unauthorized/ratelimit/notfound），最终返回明确三态——非静默折叠。
    'probe-hub/scripts/probe.py:8f7b17b662',   # code = 0（状态码未取到·走语义兜底）
    # —— tvbox-source-hub newlib（S103 新增，同尺审计）——
    # 合理：`d = None` 是「多形态 JSON 解析」的**流程控制初值**——先试整体 json.loads，
    #   失败则逐行找 `{` 开头行二次解析；此处 `d=None` 随后被 `if not isinstance(d,dict)`
    #   显式兜底（走失败分支并告警），**不是**把读失败折叠成空态（M-R60 可区分）。
    'tvbox-source-hub/scripts/newlib.py:f3960b566a',   # d = None（多形态解析流程控制）
    # —— S136 同尺审计：新增技能建档须过同一把尺（M-R60⑮）——
    # 合理（conductor.onboard ⑤ manifest 面）：`has_intent = False` 前**已显式 append 到 todo**
    #   （"manifest.json 读取失败——无法判定"），下游据此报缺口而非当成"没有意图"。
    #   属"读失败必留痕"的显式降级，与静默折叠有别。
    'ecosys-conductor/scripts/conductor.py:7fee69cbb5',   # has_intent = False（读失败已入 todo）
    # 合理（unfinished-audit.link_four 向2 资料库探活）：`out[name] = None` 即三态哨兵——
    #   None=读失败（可区分于 False=库存在但空/坏），dir2 只问「技能够不够得着」用 any(v)，
    #   dead 单独列出，不把"我读不到"折叠成"库不存在"。这是 S136 明确设计的三态。
    'unfinished-audit/scripts/link_four.py:40413e7e4b',   # out[name] = None（三态哨兵·资料库读失败）
    # 合理（link_four._db_ids 常量派生）：`sess = None` 是**显式三态 + 带原因**降级——
    #   同函数已把 why['sess'] 写成「源文件不存在（路径）」或「找不到 DB_SESS 字面量」，
    #   由 dir2 的 why 字段一路传到诊断输出。None 只表示"没派生出来"，
    #   与"库坏了"（dead）严格区分。实测降级：why={'lib':'源文件不存在（/tmp/...）'}。
    'unfinished-audit/scripts/link_four.py:aa6015d63a',   # sess = None（派生失败·带 why 原因）
    # 备注（S136 二次审计）：同函数的 `lib = None` 指纹随代码改动已变，下面这条覆盖当前形态；
    #   `_mis = []`（inspect_engine 名实校验失败）**未登记**——它已修为三态显式报错（真缺陷已修），
    #   若又出现说明有人改回去了，那时应报红而不是豁免。
    'unfinished-audit/scripts/link_four.py:d49e850c40',   # lib = None（派生失败·带 why 原因）
    # —— tvbox-inspect 磁盘版本读取（S161 审计）——
    # 合理：`_disk_version_mtime()` 的 `ver = None` 是**三态返回值**，不是"读不到就当没问题"。
    #   调用点 :3042 `_disk_ver, _disk_mt = _disk_version_mtime(sd)`，紧接着 :3043
    #   `if _disk_ver and _led_ver and _disk_ver != _led_ver:` ——**None 直接使该分支不成立**，
    #   即"磁盘版本读不出 → 不采信磁盘版本做漂移判定"，方向是**保守**的（不是把未知当已知）。
    #   同函数 mtime 侧同样保守：`max(mts) if mts else None`，:3057 `elif _disk_mt and ts is not None`
    #   同样以 None 短路；最终 `if ts: ... else: d1 = 50  # 三态：无法判定，给中间分并标注`
    #   ——**读不到 → 显式「无法判定」并打标**，正是 M-R60 要求的三态。
    #   为什么会被扫到：扫描器只看 `except: ver = None` 这一行，看不到后面三处 None 分流。
    #   失效方向：若将来有人把 `if _disk_ver` 改成 `if True`（把 None 当"版本一致"），本白名单不会失效。
    #   故显式写明判据：**删掉任一处 None 短路，必删此条**。
    'tvbox-inspect/scripts/inspect_engine.py:66fe99f20d',  # ver = None（三态·三处 None 短路·末路显式标无法判定）
}
# ⚠️ 白名单以「路径:行号」为键 → **行号随代码改动漂移**（S088 实证：探源司重构
#    活跃度口径后 309→340、777→833，两条白名单瞬时失配、被当作"未登记候选"报出）。
#    这不是扫描器误报，而是**键设计脆弱**：任何在锚点之前的行增删都会触发。
#    缓解：① 审计结论写在注释里（行号变、理由不变，便于复核）；② 扫描器报"未登记候选"
#    时**先按文件+片段语义比对**是否属已审计点，再决定改白名单还是修代码。
#    根治方向（列入 roadmap）：给锚点加稳定指纹（如锚点行的代码内容 hash）。


def _probe_parsers_broken():
    """信号源8 的解析器自检（S078 抽出，供探针与假阳性基线共用）。
    对**正常样例**喂解析器：全部返回正确值 → 健康（返回 None）；任一取错值/抛异常
    → 返回断裂描述（真值）。这样它既能当假阳性基线（无事时须为 None），
    也能当探针的 hit 判据（喂已知样例须能正确取值）——一处实现两处复用（M-R38 单一事实源）。"""
    good = [('probe', _parse_probe_ratio('探针结果: 3/3 信号源逻辑有效'), (3, 3)),
            ('assess', _parse_assess_score('⑦ 综合评分: 88/100 ｜ contract=88'), (88, 100)),
            ('evolve', _parse_evolve_ready('就绪 6/6（规则54 三件套）'), (6, 6)),
            # S079：判据④ 三态解析器——「无遗留」/「N项遗留」/「无法判定」各须取对值
            ('watchdog_无', _parse_watchdog_status('② 业务面: watchdog → ✅ 无遗留——所有监测项达标'), True),
            ('watchdog_有', _parse_watchdog_status('② 业务面: 合计 3 项遗留'), False)]
    junk = [('probe', '乱码无结构'), ('assess', '乱码'), ('evolve', '乱码')]
    for tag, got, want in good:
        if got != want:
            return f'{tag} 解析器对正常样例取值错误：得 {got}，应 {want}（措辞漂移致静默失效）'
    # S079：判据④ 解析器的第三态——空输出/措辞漂移必须返回 None（不得伪装成 True/False）
    if _parse_watchdog_status('') is not None or _parse_watchdog_status('命令超时无输出') is not None:
        return 'watchdog 解析器对"无结论输出"应返回 None（无法判定），不得伪装成 True/False'
    for tag, s in junk:
        try:
            fn = {'probe': _parse_probe_ratio, 'assess': _parse_assess_score, 'evolve': _parse_evolve_ready}[tag]
            if fn(s) is not None:
                return f'{tag} 解析器对垃圾输入返回非 None（假阳性）'
        except Exception as e:
            return f'{tag} 解析器对垃圾输入抛异常：{e}'
    return None


def _sig_upstream_gone(_mod_override=None):
    """信号源19：上游跑路检查器自检（S090·M18 守护）。

    S090 在 watchdog 新增 M18「上游仓库跑路/弃更未处置」——把探源司（probe-hub）的
    「作者仓库已死/疑弃更」结论**转译为 watchdog 遗留项**，纳统一分级跟踪。这是
    生态首条「探源司 → 遗迹哨」的**判定链路**（上游结论驱动下游处置）。

    **为何必须守护**：M18 的命门是**三态语义**——`无法判定` **绝不可产遗留**。
    若某次重构把"我读不到"折叠成"作者跑路"（正是 S088 实证的 `dirs=[]` 谎报
    "移除×290" 的同型缺陷，M-R60），下游会触发**错误的应急处置**（误以为上游已死 →
    盲目换线/下架）。故用合成输入独立驱动 M18 的**纯判定逻辑**，断言：
      · 跑路（已封禁/删除）→ **必须产阻断遗留**（真阳性，防漏报——"作者跑路不知道"）
      · 归档（>365 无提交）→ **必须产耗时遗留**（疑弃更须复核）
      · **无法判定 → 绝不产遗留**（命门，防误报——"我读不到"≠"作者跑路"）
      · 空册/正常 → 绝不产遗留（假阳性基线）
      · 读取失败（state='fail'）→ 不产遗留**但须告警**（M-R60⑫："无法判定"≠"无问题"）
    返回 [] 表示 M18 三态语义健在；否则报出具体失守项。
    _mod_override：仅供探针注入退化模块；正常调用为 None（加载真模块）。"""
    out = []
    wpath = INSPECT_ENGINE
    if _mod_override is not None:
        mod = _mod_override
    else:
        if not os.path.exists(wpath):
            return [{'skill': 'ecosys-conductor', 'type': '上游跑路检查器自检',
                     'evidence': 'legacy-watchdog/scripts/watchdog.py 缺失——M18 无法守护'}]
        try:
            import importlib.util as _ilu
            spec = _ilu.spec_from_file_location('_wd_m18_guard', wpath)
            mod = _ilu.module_from_spec(spec)
            _wdir = os.path.dirname(wpath)
            if _wdir not in sys.path:
                sys.path.insert(0, _wdir)
            spec.loader.exec_module(mod)
        except Exception as e:
            return [{'skill': 'ecosys-conductor', 'type': '上游跑路检查器自检',
                     'evidence': f'watchdog 模块加载失败（{type(e).__name__}: {e}）——无法自检'}]
    if not hasattr(mod, 'check_upstream_gone'):
        return [{'skill': 'ecosys-conductor', 'type': '上游跑路检查器自检',
                 'evidence': 'M18 check_upstream_gone 未实现——上游跑路检查器缺失'}]
    _m18 = next((x for x in mod.load_monitors()['monitors'] if x.get('id') == 'M18'), None)
    if _m18 is None:
        return [{'skill': 'ecosys-conductor', 'type': '上游跑路检查器自检',
                 'evidence': 'monitors.json 未登记 M18——检查器未挂载（登记册先行）'}]

    _orig = mod._upstream_targets

    def _fake(tgts):
        mod._upstream_targets = lambda: ('ok', tgts)

    def _sev_of(res, name):
        for x in res:
            if x['item'].startswith(name):
                return x.get('sev')
        return None

    try:
        # 真阳性：跑路→阻断 / 归档→耗时 / 停滞→不产（S091 校准）
        _fake([
            {'仓库名': 'GONE', '存活状态': '已封禁/删除', '活跃度': '无法判定'},
            {'仓库名': 'OLD', '存活状态': '存活', '活跃度': '归档'},
            {'仓库名': 'SLOW', '存活状态': '存活', '活跃度': '停滞'},
        ])
        r1 = mod.check_upstream_gone(_m18)
        if _sev_of(r1, 'GONE') != '阻断':
            out.append({'skill': 'ecosys-conductor', 'type': '上游跑路检查器自检',
                        'evidence': 'M18 对「已封禁/删除」未产阻断遗留——作者跑路将被漏报'})
        if _sev_of(r1, 'OLD') != '耗时':
            out.append({'skill': 'ecosys-conductor', 'type': '上游跑路检查器自检',
                        'evidence': 'M18 对「归档」未产耗时遗留——疑弃更将被漏报'})
        # S091 校准：`停滞`（90~365 天）**不产遗留**——值得关注≠需要处置（M-R53 防噪声淹没）
        if _sev_of(r1, 'SLOW') is not None:
            out.append({'skill': 'ecosys-conductor', 'type': '上游跑路检查器自检',
                        'evidence': 'M18 对「停滞」产出了遗留——S091 校准要求停滞不产'
                                    '（停滞仍可见于探源司监测台，但不应淹没有效遗留）'})

        # 命门：无法判定 → 绝不产遗留（防"我读不到"被说成"作者跑路"）
        _fake([
            {'仓库名': 'UNK', '存活状态': '无法判定', '活跃度': '无法判定'},
            {'仓库名': 'OK', '存活状态': '存活', '活跃度': '活跃'},
            {'仓库名': 'BLANK', '存活状态': '', '活跃度': ''},
        ])
        r2 = mod.check_upstream_gone(_m18)
        if r2:
            out.append({'skill': 'ecosys-conductor', 'type': '上游跑路检查器自检',
                        'evidence': 'M18 对「无法判定/正常」产出了遗留——**M-R60 折叠**：'
                                    '把"读不到"说成"作者跑路"，会触发错误应急处置'})

        # 空册 → 静默
        _fake([])
        if mod.check_upstream_gone(_m18):
            out.append({'skill': 'ecosys-conductor', 'type': '上游跑路检查器自检',
                        'evidence': 'M18 对空册产出遗留——首期误报'})

        # 读取失败 → 不产遗留但须告警（M-R60⑫）
        import io as _io
        _buf, _old_so = _io.StringIO(), sys.stdout
        try:
            mod._upstream_targets = lambda: ('fail', [])
            sys.stdout = _buf
            r4 = mod.check_upstream_gone(_m18)
        finally:
            sys.stdout = _old_so
        if r4:
            out.append({'skill': 'ecosys-conductor', 'type': '上游跑路检查器自检',
                        'evidence': 'M18 对读取失败产出了遗留（应不产，仅告警）——'
                                    '"读不到"与"作者跑路"被折叠'})
        elif '无法判定' not in _buf.getvalue():
            out.append({'skill': 'ecosys-conductor', 'type': '上游跑路检查器自检',
                        'evidence': 'M18 读取失败时**未告警**——静默"无问题"（M-R60⑫）'})
    finally:
        mod._upstream_targets = _orig
    return out


def _sig_eco_ledger(_rows_override=None):
    """信号源20：生态台账版本一致性自检（S095）。

    **为何必须守护（M-R40 副本静默过期）**：`DB_ECO` 生态台账是各技能**版本的副本**，
    但它是**手工维护**的——技能升了版本（改 SKILL.md），**没人自动把台账改回去**。
    S095 实证：一次巡检发现 **8 条里 6 条版本滞后**（probe-hub 台账记 1.0.1，实际已 1.0.7；
    tvbox-interface 记 1.0.0，实际 1.2.0）——**副本静默过期**，台账沦为"历史快照"，
    下游若据它判"谁该升级"会得到全错的结论。

    本检测器读 DB_ECO 每条的 `技能名`/`version`，与磁盘上该技能 `SKILL.md` 的
    `version:` 逐条比对，不一致即报（附"台账=X 实际=Y"证据）。

    **三态（M-R60）**：库读失败/技能名无法对应 SKILL.md → **显式告警为"无法判定"**，
    **绝不当一致**（"我读不到"≠"台账正确"）。
    _rows_override：仅供探针注入（合成行）；正常调用读真库。
    返回 [] 表示台账与权威一致；否则报出每处失配。
    """
    out = []
    DB_ECO = 'T4kODtANe7rSrtZTu4sIND'
    if _rows_override is not None:
        rows = _rows_override
    else:
        q = f'{SKILL_ROOT}/library/database/query_database_record.py'
        if not os.path.exists(q):
            return [{'skill': 'ecosys-conductor', 'type': '生态台账版本一致性',
                     'evidence': 'library/database/query_database_record.py 缺失——无法读台账（无法判定）'}]
        try:
            r = subprocess.run(['python3', q, '--database-id', DB_ECO, '--page-size', '50'],
                               capture_output=True, text=True, timeout=30)
            d = json.loads(r.stdout or '{}')
            rows = d.get('results')
            if not isinstance(rows, list):
                raise ValueError('results 非数组')
        except Exception as e:
            # M-R60：读失败=无法判定，**绝不当一致**（不得静默 return []）
            return [{'skill': 'ecosys-conductor', 'type': '生态台账版本一致性',
                     'evidence': f'生态台账 DB_ECO 读取失败（{type(e).__name__}）——'
                                 f'**无法判定**台账是否与权威一致（勿据此判"无漂移"）'}]
    for row in rows:
        name = str(row.get('技能名') or '').strip()
        if not name:
            continue
        ledger_v = str(row.get('version') or '').strip()
        skill_md = os.path.join(SKILL_ROOT, name, 'SKILL.md')
        if not os.path.exists(skill_md):
            out.append({'skill': 'ecosys-conductor', 'type': '生态台账版本一致性',
                        'evidence': f'台账登记「{name}」但磁盘无 {name}/SKILL.md——无法判定（登记项悬空）'})
            continue
        try:
            txt = open(skill_md, encoding='utf-8').read()
            m = re.search(r'^version:\s*"?([^"\n]+)"?', txt, re.M)
            auth_v = (m.group(1).strip() if m else '')
        except Exception as _e:
            # M-R60⑫：读取异常≠"无 version 行"——显式告警为无法判定（勿静默当一致/当缺失）
            out.append({'skill': 'ecosys-conductor', 'type': '生态台账版本一致性',
                        'evidence': f'「{name}」SKILL.md 读取异常（{type(_e).__name__}: {_e}）'
                                    f'——无法判定（勿当一致）'})
            continue
        if not auth_v:
            out.append({'skill': 'ecosys-conductor', 'type': '生态台账版本一致性',
                        'evidence': f'「{name}」SKILL.md 无 version 行——无法判定（勿当一致）'})
            continue
        if ledger_v != auth_v:
            out.append({'skill': 'ecosys-conductor', 'type': '生态台账版本一致性',
                        'evidence': f'生态台账「{name}」版本滞后：台账={ledger_v} 实际={auth_v}'
                                    f'——**M-R40 副本静默过期**，台账是技能版本的副本、未跟权威源走'})
    return out


def _sig_lend_gap(_out_override=None):
    """信号源21：经验对照缺口（S107·规则57「方法极」）。

    **为何必须守护**：规则57 要求「技能升级前必跑 evolve lend 查可借鉴招式，升级后必回写
    『借鉴 T-X / 不适用 T-X（原因）』留痕」。但**升级动作与对照动作是两件事**——技能照升不误，
    对照却可能整轮不做，**无任何机制会因此报警**。这是 M-R40「副本静默过期」在**方法论层**
    的形态：CHANGELOG 记了升级（副本更新），却没有对照留痕（方法论断层），二者无一致性校验。

    **数据源**：`evolve review-lend` 逐条报出「升了版但无经验对照留痕」的技能。

    **三态（M-R60）**：命令失败（evolve 缺失/执行异常）→ **显式告警"无法判定"**，
    **绝不当"无缺口"**（"我查不到"≠"没人漏做对照"）。

    _out_override：仅供探针注入（合成输出）；正常调用执行真命令。
    返回 [] 表示全生态升级均有对照留痕；否则逐条报出。
    """
    out = []
    if _out_override is not None:
        raw = _out_override
        # 探针注入路径同样须三态：空/空白输出 = 无法判定，**不得折叠成"无缺口"**
        if not raw.strip():
            return [{'skill': 'ecosys-conductor', 'type': '经验对照缺口',
                     'evidence': 'evolve review-lend 输出为空——**无法判定**各技能是否漏做经验对照（勿当无缺口）'}]
    else:
        ev = os.path.join(SKILL_ROOT, 'skill-evolution', 'scripts', 'evolve.py')
        if not os.path.exists(ev):
            return [{'skill': 'ecosys-conductor', 'type': '经验对照缺口',
                     'evidence': 'skill-evolution/scripts/evolve.py 缺失——无法跑 review-lend（无法判定）'}]
        raw = _sh(f'cd {SKILL_ROOT}/skill-evolution && python3 scripts/evolve.py review-lend 2>&1',
                  timeout=60, cache=True)
        # M-R59：命令失败须稳定识别（失败哨兵/空输出均属无法判定）
        if not _sh_ok(raw) or not raw.strip():
            return [{'skill': 'ecosys-conductor', 'type': '经验对照缺口',
                     'evidence': 'evolve review-lend 不可达或输出为空——**无法判定**各技能是否漏做经验对照（勿当无缺口）'}]
    # 解析逐条缺口行：`⚠️ <skill> 升了版但无经验对照留痕：<详情>`
    for m in re.finditer(r'⚠️\s+(\S+)\s+升了版但无经验对照留痕[：:]\s*(.*)', raw):
        sk = m.group(1).strip()
        detail = m.group(2).strip()[:60]
        out.append({'skill': sk, 'type': '经验对照缺口',
                    'evidence': f'「{sk}」升级未见经验对照留痕（{detail}）——规则57：升级前须跑 '
                                f'evolve lend <name> --own 查招式、升级后须回写「借鉴 T-X / 不适用 T-X」'
                                f'（漏项=遗留）'})
    return out


def _sig_pool_gap(_rows_override=None):
    """信号源22：会话经验留痕缺口（S108·规则58「会话必沉淀经验池」）。

    **为何必须守护**：规则58 要求「每次会话收尾必须把坑/解法/可复用点写入双经验池」。
    但经验池写入是**纯人工动作**，无任何机制校验——S108 实证：S082~S108 共 27 轮工作
    （含 S107 招式库、S108 规则优化两大轮）**全部未进池**，③ 台账停在 S051、
    ④ 经验库停在 L085，双库**静默遗漏近 30 轮**，靠人工核对才发现。
    这是 M-R40「副本静默过期」在**经验层**的形态，也是 M-R63「检测器盲区」——
    经验池从未被任何探针覆盖，缺口天然不可见。

    **判据**：读 ③ 会话台账（DB_SESS）与 ④ 资料库经验库（DB_LIB），取各自最新条目的
    `时间`；与「最近一次技能版本变动时间」（DB_ECO 的 `末次升级` 最大值，即最近工作发生的
    时间锚点）比对——若经验池最新时间 **落后** 工作锚点超过阈值（默认 1 天），即报遗留
    （说明有工作发生但经验池未沉淀）。

    **三态（M-R60）**：任一双池读取失败 → **显式告警"无法判定"**，**绝不当"无缺口"**
    （"我读不到"≠"大家都写了经验"）。

    **S158 十补（判据纠正）**：原阈值「落后 > 1 天」。实测锚点 14:21、④停在 11:21
    （lag 0.125 天）、③停在 13:56（lag 0.018 天），两轮工作没进池，本函数返回空 = "无缺口"。
    一轮工作约 2h，24h 阈值等于「今天漏多少轮一律静默」——只抓灾难，抓不到"今天又漏了"。
    现改 **4h**（两个工作轮次的宽限）。设 0 会让它每轮必红，逼出"为消红写一行空话"，
    比不报更糟；4h 是「同一轮里先改代码后写经验不该被打断」与「上午干了下午还没记必须报」的折中。
    另加「空时间单列」：记了条目但没填时间 = 对守护不存在（原 `_latest_ts` 静默跳过空值）。

    _rows_override：仅供探针注入（(sess_rows, lib_rows, eco_rows) 三元组）；正常调用读真库。
    返回 [] 表示双池均跟上工作节奏；否则报出缺口。
    """
    DB_SESS = 'TO4LwsjVVdR8JRAUlkejk3'
    DB_LIB = 'XTuuj8mKtqnZSolDdeDxkq'
    DB_ECO = 'T4kODtANe7rSrtZTu4sIND'
    T = {'skill': 'ecosys-conductor', 'type': '会话经验留痕缺口'}

    def _mkq():
        return os.path.join(SKILL_ROOT, 'library', 'database', 'query_database_record.py')

    def _read(db):
        """三态读：返回 (ok, rows)。ok=False 表无法判定。"""
        q = _mkq()
        if not os.path.exists(q):
            return False, []
        try:
            r = subprocess.run(['python3', q, '--database-id', db, '--page-size', '200'],
                               capture_output=True, text=True, timeout=40)
            d = json.loads(r.stdout or '{}')
            rows = d.get('results')
            if not isinstance(rows, list):
                return False, []
            return True, rows
        except Exception:
            return False, []

    def _latest_ts(rows, key='时间'):
        """取条目最新时间（ISO 串）。无有效时间返回 None。"""
        best = None
        for r in rows:
            v = str(r.get(key) or '').strip()
            if not v:
                continue
            v2 = v.replace('Z', '').replace('T', ' ')
            try:
                t = datetime.datetime.fromisoformat(v2[:19])
            except Exception:
                continue
            if best is None or t > best:
                best = t
        return best

    if _rows_override is not None:
        sess_rows, lib_rows, eco_rows = _rows_override
        ok_s = ok_l = ok_e = True
    else:
        ok_s, sess_rows = _read(DB_SESS)
        ok_l, lib_rows = _read(DB_LIB)
        ok_e, eco_rows = _read(DB_ECO)

    # M-R60：任一读失败 = 无法判定（不得当无缺口）
    if not (ok_s and ok_l and ok_e):
        _miss = [n for n, ok in (('③会话台账', ok_s), ('④经验库', ok_l), ('生态台账', ok_e)) if not ok]
        return [dict(T, evidence=f'{"、".join(_miss)} 读取失败——**无法判定**经验池是否跟上工作节奏'
                                 f'（勿当无缺口，M-R60 三态）')]

    anchor_ts = _latest_ts(eco_rows, '末次升级')
    sess_ts = _latest_ts(sess_rows, '时间')
    lib_ts = _latest_ts(lib_rows, '时间')

    if anchor_ts is None:
        return [dict(T, evidence='生态台账无可解析的「末次升级」时间——**无法判定**工作锚点'
                                 '（勿当无缺口）')]

    out = []

    # ★ S158 十补：新增「空时间」缺口——记了条目却没填时间，等于对守护不存在。
    #   实测：③ 里 S108/S109 两轮、④ 里两条无编号无时间的记录，`_latest_ts` 跳过空值，
    #   于是"写了但没写时间"被静默当成"没写"，两次都不报。
    #   注意口径：这不是"没沉淀"（条目确实在），而是"沉淀了但无法计时"——两条分别说。
    _no_ts_s = sum(1 for r in sess_rows if not str(r.get('时间') or '').strip())
    _no_ts_l = sum(1 for r in lib_rows if not str(r.get('时间') or '').strip())
    if _no_ts_s:
        out.append(dict(T, evidence=f'③会话台账有 **{_no_ts_s} 条**记录缺「时间」字段——'
                                    f'已沉淀但无法参与时效判定（空时间 ≠ 及时，M-R60）'))
    if _no_ts_l:
        out.append(dict(T, evidence=f'④资料库经验库有 **{_no_ts_l} 条**记录缺「时间」字段——'
                                    f'已沉淀但无法参与时效判定（空时间 ≠ 及时，M-R60）'))

    if sess_ts is None and lib_ts is None:
        out.append(dict(T, evidence='③④ 双池均无可解析时间——**无法判定**经验池是否沉淀'
                                    '（勿当无缺口）'))
        return out

    # ★ S158 十补（核心改动）：判据从「落后 > 1 天」改成「**落后 > 1 个工作轮次**」。
    #
    #   病因（实测）：阈值原为 1 天 = 24h，而一轮工作平均约 2h。
    #   → 同一天里无论漏多少轮，只要不超过 24h 一律静默。
    #   实测案例：锚点 14:21:12、④停在 11:21（lag 0.125 天）、③停在 13:56（lag 0.018 天），
    #   两轮工作没进池，检测器返回空 = "无缺口"。它只能抓灾难，抓不到"今天又漏了"。
    #   而规则58 的原话是「**每次会话**必须写」——不是「每月别漏」。
    #
    #   为什么改成 4h 而不是"锚点一动就报"：
    #     锚点（`末次升级`）在**任何**技能升版时都会变，包括只是补个 typo。
    #     设成 0 会让它每轮必红，然后人开始"为消红写一行空话"——那比不报更糟。
    #     4h = 两个工作轮次的宽限：同一轮里先改代码后写经验，不该被自己打断；
    #     但"上午干了、下午还没记"必须报出来。
    POOL_GAP_HOURS = 4.0

    for name, ts, dbid in (('③会话台账', sess_ts, DB_SESS), ('④资料库经验库', lib_ts, DB_LIB)):
        if ts is None:
            out.append(dict(T, evidence=f'{name}（{dbid}）无可解析时间——**无法判定**是否沉淀（勿当无缺口）'))
            continue
        lag_h = (anchor_ts - ts).total_seconds() / 3600.0
        if lag_h > POOL_GAP_HOURS:
            # ★ 我第一版写成 `f'{lag_h:.1f} 小时' if ... else f'{lag_h / 24:.1f} 天'`，
            #   只换了单位文字、漏换**数值**：12h 打成「12.0 小时」…不，打成「2.0 小时」（12/24 的商配"小时"），
            #   30h 打成「0.0 小时」。单位与数值各说各话，比不写还糟。
            _lag_s = (f'{lag_h:.1f} 小时' if lag_h < 48
                      else f'{lag_h / 24:.1f} 天（{lag_h:.0f} 小时）')
            out.append(dict(T, evidence=f'{name}最新条目（{ts:%Y-%m-%d %H:%M}）落后最近工作锚点'
                                        f'（{anchor_ts:%Y-%m-%d %H:%M}）**{_lag_s}**（阈值 {POOL_GAP_HOURS:.0f}h）'
                                        f'——规则58：每次会话须沉淀，疑似本轮工作未进池（漏项=遗留）'))
    return out


# --- 信号源42：会话提炼缺口（S159 · M-R117「会话不产出则不许结束」）---
def _sig_session_distill(_recap_override=None):
    """信号源42：本轮会话有没有交代「落点 / 证据」。

    **为什么需要它（S158 实证）**：规则58 只管「经验池写了吗」，不管「写的是不是实物」。
    实测出现过——③ 台账 110 条，抽任意一条都答不出「改了哪个文件的哪一行、跑命令看到了什么」。
    另一处更直接的教训：S158 把「我确认了一下」写进交付，回查时**无法复现**，
    因为那一步没有命令、没有输出。**「我说做完了」不是证据，「这条命令输出这个」才是。**

    **判据（两条都要）**：
      ① 落点：正文里出现 `skills/xxx/...` 或 `xxx.py:行号` 形态的路径 → 否则报「无落点」
      ② 证据：出现命令痕迹（`$ ` 开头 / `python3 ` / `tvbox `）或「→ 输出」标记 → 否则报「无证据」
    第三条「没做什么」不进自动判据——它是**叙述性**的，机器判「有没有」只会逼出空话；
    留给 ⑫ 的 note 与人工复核，这条明确记在规则里。

    **三态（M-R60）**：`_recap_override is None` 且读不到 journal →
    **报「无法判定」**，绝不当「本轮有产出」。这是本条最容易犯的错：
    把「我没读到记录」折叠成「记录很干净」。

    _recap_override：仅探针注入用（传入本轮正文文本）；正常调用读当日 journal。
    返回 [] 表示两条判据都满足；否则报出缺哪一条。
    """
    T = {'skill': 'ecosys-conductor', 'type': '会话提炼缺口(M-R117)'}

    if _recap_override is None:
        # 正常路径：取当日 journal 里本会话的动作，拼成可判文本。
        # 读失败 → 无法判定（不折成"无缺口"）。
        try:
            today = datetime.datetime.now().strftime('%Y-%m-%d')
            acts = []
            if os.path.exists(JOURNAL_FP):
                with open(JOURNAL_FP, encoding='utf-8') as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            r = json.loads(ln)
                        except Exception:
                            continue
                        if str(r.get('ts') or '').startswith(today):
                            acts.append(r)
            if not acts:
                return [dict(T, evidence='今日无任何动作记录——**无法判定**本轮是否有落点/证据'
                                        '（空记录 ≠ 有产出，M-R60）')]
            text = '\n'.join(f"{a.get('verb','')} {a.get('arg','')}" for a in acts)
        except Exception as e:
            return [dict(T, evidence=f'会话流水读取失败（{type(e).__name__}: {e}）——'
                                    f'**无法判定**本轮是否有落点/证据（勿当有产出）')]
    else:
        text = str(_recap_override or '')

    src = text or ''
    # ① 落点：路径形态。宁可宽一点——误报（说有落点其实只是提了个路径）比漏报好，
    #    因为本条的失效方向是「没做事也能过」，不是「做了事被拦」。
    _has_path = bool(re.search(
        r'(skills/[\w.\-]+/[\w./\-]+\.[a-z]{2,4}'
        r'|[\w\-]+\.py:\d+'
        r'|[\w.\-]+\.json:\d+'
        r'|/workspace/[\w.\-]+\.(md|json|py))', src))
    # ② 证据：命令痕迹或输出标记
    _has_ev = bool(re.search(
        r'(^\s*\$\s+\S|python3\s+\S+\.py|tvbox\s+\S+\s+\S|→\s*\S)', src, re.M))

    out = []
    if not _has_path:
        out.append(dict(T, evidence='本轮**无落点**：正文里找不到任何文件路径/行号——'
                                    'M-R117 要求交代「改了哪些文件」，禁止用「已完成」收尾'))
    if not _has_ev:
        out.append(dict(T, evidence='本轮**无证据**：找不到跑过什么命令、看到什么输出——'
                                    '「我看了一下」不算产出（S158 教训：那一步无法复现）'))
    return out


# --- 信号源23：核心库行数归零守护（S110 · M-R32/M-R40 数据面变体）---
# 实证：DB_SRC（源总库）1219 行归零长期无人知——无任何信号源守护「核心数据资产行数」，
# 直到 verify 判据④深挖 watchdog 才偶然发现。行数归零/骤降须有机制报警（M-R60：读失败≠空库）。
# S146 修：原 CORE_DB 只盯 `DB_SRC 源总库`，而该库在 S145「1017 并入探源司」后**已设计停用**
# （probe.py 注释：`源总库（1017 并入探源司后停用）`）→ 检测器永远报「行数归零」，
# 属**假阳性恒报**（signal-check 基线当场抓到：无注入即报）。改盯**在用**核心库，
# 停用库进 RETIRED_DB 白名单（显式豁免 + 原因），既不再恒报、也不失去对真·在用库的守护。
CORE_DB = {
    # ★S160 再换：py/csp 两库合并为「线路源库」，成品源库更名「JSON库」。
    #   S159 曾把 py/csp 两库并列入守（因为 DB_SRC 空库已被实测推翻）——那是对的，
    #   但它守的是**库名**。S160 并库后旧两库名已不存在，继续留着等于守两个空号。
    #   故此处改为守「现有的两个库」，条数为并库后的实测值。
    #   历史教训两次同型（S155/S159 都栽在"盯错库"上）：
    #     ① 盯 0 行空库 → 行数守护恒报「归零」，真告警被淹（M-R53）；
    #     ② 盯已并掉的库 → 真·在用库**根本没人守**（M-R32 装健康的哑巴）。
    #   判据：CORE_DB 必须与 core.SRC_DBS **逐条对齐**，docsync 会查这件事。
    'SRC_LINE 线路源库': 'pNwZ9uBOTz6FzRuvu8nryI',   # 620 条（原 py 112 + csp 508）
    'SRC_PROD 成品源库': '8JBrhSwsJ3pnPYtoGLo9XV',   # 109 条（沿用旧名，S160 重建）
    'DB_DEAD 失信库': 'hGwE41OpJDOR8CW0sLUwRx',        # 探源司·失信 host 库（M-R96 三层分级）
    'DB_ECO 生态登记': 'T4kODtANe7rSrtZTu4sIND',       # 生态台账（M-R40 副本过期守着）
}
# **S155 修正：DB_SRC 被错误地标成「已停用」，因而脱离了行数守护整整一轮。**
# 事故链：S146 为消除信号源23 的假阳性恒报，把 DB_SRC 移出 CORE_DB 并写进 RETIRED_DB，
# 理由是「S145 后已并入 DB_PROD」。实测反驳这个结论：
#   DB_SRC 801 条 ｜ DB_PROD 109 条 ｜ **仅 SRC 有 714 条**（交集仅 87 条）
#   ——合并**根本没做完**，与 S150「搬库只搬一半」是同一个病。
# 更糟的是后果：8 个文件（conductor/probe/inspect_engine/blk/link/newlib/probe_src/run）
# 仍在读写 DB_SRC，M09 僵尸源等关键检查器也以它为主数据源。把它标为「停用」，
# 等于**给装着 714 条唯一数据的在用库关掉了报警器**——正是 M-R32「装健康的哑巴」。
# 教训：**白名单是止痛药，不是诊断书。** 为了让检测器不叫就把源头标成"已废弃"，
# 比假阳性本身更危险：报警安静了，风险还在长。
RETIRED_DB = {
    'wW5xpbS663nA6XK8a7DNsp': 'DB_SRC 旧源总库——S144 清空并改名「🗑【已废弃】」，实测 0 行；数据已分流三库',
    'G9B1GzfRJDoSausMmPXF8T': 'DB_BAK 旧备用库——S144 清空并改名「🗑【已废弃】」，实测 0 行；三库互斥分区下无「同构备份」角色',
    '92dSuG6uUbxitGuyx4Saaf': 'DB_DEAD 旧失效源库——由 hGwE41OpJDOR8CW0sLUwRx 取代',
}
_ROWS_ANCHOR_FP = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core_db_rows.json')

def _sig_issue_stale(_state_override=None, _now_override=None):
    """信号源25：问题遗留超期（M-R73）——issues 总账 open/next 条目 opened 超 3 天未动即报。
    三态：账本损坏=显式报；有超期=逐条报；无超期=静默。_state_override 仅供探针注入。"""
    out = []
    try:
        if _state_override is not None:
            db = _state_override
        else:
            db = json.load(open(ISSUES_FP, encoding='utf-8'))
        import time as _t
        now = _now_override if _now_override is not None else _t.time()
        for it in db.get('issues', []):
            if it.get('status') in ('done', 'wontfix'):
                continue
            try:
                age = (now - _t.mktime(_t.strptime(it.get('opened', ''), '%Y-%m-%d'))) / 86400
            except Exception:
                age = 99.0
            if age > 3:
                out.append(('问题超期', f"{it.get('id')}「{it.get('title','')[:30]}」已 {age:.0f} 天未结（M-R73 清账）"))
    except FileNotFoundError:
        pass   # 空账=无问题，静默
    except Exception as e:
        out.append(('账本异常', f'issues_ledger.json 无法判定：{e}（M-R60 显式报）'))
    return out


def _sig_backup_stale(_state_override=None):
    """信号源24：三层联动备份过期（S126 归责闭环）——backup_state.last 超 24h 或无状态即报。
    三态：无状态文件=未转（报）；超 24h=过期（报）；新鲜=静默。备份是"不丢失后盾"，过期=后盾失效。
    _state_override：仅供探针注入合成 state（如 last.date=4 天前）；正常调用为 None。"""
    out = []
    try:
        if _state_override is not None:
            st = _state_override
        else:
            st = json.load(open(BACKUP_STATE_FP, encoding='utf-8'))
        last = st.get('last') or {}
        date = str(last.get('date') or '')
        if not date:
            out.append(('备份状态', 'last 缺失——三层备份从未成功落地'))
            return out
        import time as _t
        age_h = (_t.time() - _t.mktime(_t.strptime(date, '%Y%m%d'))) / 3600
        if age_h > 24:
            out.append(('备份过期', f'最近备份 {date}（{age_h:.0f}h 前 > 24h）——跑 conductor backup 刷新'))
    except FileNotFoundError:
        out.append(('备份状态', 'backup_state.json 不存在——三层备份从未执行'))
    except Exception:
        out.append(('备份状态', '状态文件损坏——无法判定备份新鲜度（M-R60 显式报）'))
    return out


def _sig_registry_fork(_root_override=None):
    """信号源26：监测登记册副本复活（S132 P007 归一后的防复发）。
    正本唯一：tvbox-inspect/monitors.json。其他技能目录下若再出现带实体的 monitors.json
    （monitors 非空且未声明 superseded_by）= 副本复活，双份定义必漂移（M-R38）。
    _root_override：仅供探针注入合成目录（含伪造实体副本）验证捕获能力。"""
    import glob as _g
    out = []
    root = _root_override or SKILL_ROOT
    canon = os.path.join(root, 'tvbox-inspect', 'monitors.json')
    for p in _g.glob(os.path.join(root, '*', 'monitors.json')):
        if os.path.abspath(p) == os.path.abspath(canon):
            continue
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception:
            out.append(('登记册副本', f'{p} 无法解析——副本损坏（M-R60 显式报）'))
            continue
        if d.get('superseded_by'):
            continue                      # 合规指针，静默
        ms = d.get('monitors') or []
        if ms:
            out.append(('登记册副本', f'{os.path.basename(os.path.dirname(p))}/monitors.json 含 {len(ms)} 条实体——'
                                     f'副本复活，正本应为 tvbox-inspect/monitors.json（M-R38）'))
    return out


# S146 修：信号源27 原读 `wW5xpbS663nA6XK8a7DNsp`（DB_SRC 源总库），而该库 S145 后**已设计停用**，
# 恒返回 0 行 → 恒报「读失败与空库不可区分」，与信号源23 同款假阳性。
# S160：成品源库重建到新 id，坐标同步（同样有 `状态` 字段、同"须终态"语义）。
STATUS_DB = '8JBrhSwsJ3pnPYtoGLo9XV'          # SRC_PROD 成品源库（109 条）

def _sig_status_terminal(_rows_override=None):
    """信号源27：源库状态未终态（S134 P011 终态化后的防回退）。
    终态集 = {正常, 停用, 不适用}；非终态（待查/有广/空值）= 悬空，须测活或归类推进。
    _rows_override：仅供探针注入合成数据（含悬空行）验证捕获能力。"""
    out = []
    rows = _rows_override
    if rows is None:
        try:
            sys.path.insert(0, os.path.join(SKILL_ROOT, 'probe-hub', 'scripts'))
            import importlib
            _pr = importlib.import_module('probe')
            rows = _pr.rows_of(STATUS_DB)
        except Exception as e:
            out.append(('状态终态', f'源库读取失败——无法判定（M-R60 不折叠）：{e}'))
            return out
    if not rows:
        out.append(('状态终态', f'源库（{STATUS_DB}）返回 0 行——读失败与空库不可区分（M-R60）'))
        return out
    TERMINAL = {'正常', '停用', '不适用'}
    dang = [r for r in rows if str(r.get('状态') or '').strip() not in TERMINAL]
    if dang:
        from collections import Counter as _C
        c = _C(str(r.get('状态') or '(空)') for r in dang)
        out.append(('状态终态', f'{len(dang)}/{len(rows)} 条未进终态（'
                               + '、'.join(f'{k} {v}' for k, v in c.most_common())
                               + '）——须 probe_src --write 或归类推进'))
    return out


def _sig_ask_hittable(_reg_override=None):
    """信号源33：规则 ask 可命中（S148）。

    **守的是什么**：规则库有 summary（维护者视角「该守什么」）而无 ask（用户口吻
    「他会怎么问」），则规则与提问之间**只有维护者的记忆在连接**。用户问
    「导了一大批进去还是有重复」，永远撞不到「L023 批量入库判重只用库内快照」。

    **两条判据**（与 `rules_cmd._ask_issues` 同口径，此处不另写一套 —— M-R38）：
      ① ask 缺失 → 该条规则退回"只有结论句"，用户问不中；
      ② ask 与 summary bigram 包含率 >0.8 → **假补**：字段填了，等于把 summary
         换个标点，用户还是不会这么问。字段存在不等于能力存在。

    _reg_override：仅供探针注入合成注册表验证捕获能力（正常调用为 None，
    此时读 rules_registry.json）。**读失败必须显式报无法判定，不得当成"没有规则"**（M-R60）。
    """
    out = []
    if _reg_override is not None:
        reg = _reg_override
    else:
        fp = os.path.join(SKILL_ROOT, 'ecosys-conductor', 'scripts', 'rules_registry.json')
        if not os.path.exists(fp):
            return [('规则ask', '规则注册表不存在——**无法判定**（勿当"没有规则"，M-R60）')]
        try:
            reg = json.load(open(fp, encoding='utf-8'))
        except Exception as e:
            return [('规则ask', f'规则注册表读取失败——**无法判定**（M-R60）：{type(e).__name__}')]
    rs = reg.get('rules') if isinstance(reg, dict) else None
    if not isinstance(rs, list):
        return [('规则ask', '规则注册表结构异常（rules 非列表）——**无法判定**（M-R60）')]
    if not rs:
        return [('规则ask', '规则注册表 0 条——读失败与真空不可区分（M-R60）')]

    def _norm(s):
        return re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', s or '')

    miss, fake = [], []
    for r in rs:
        a = str(r.get('ask') or '').strip()
        if not a:
            miss.append(str(r.get('id')))
            continue
        an, sn = _norm(a), _norm(str(r.get('summary') or ''))
        if not sn:
            continue
        ga = {an[i:i + 2] for i in range(len(an) - 1)}
        gs = {sn[i:i + 2] for i in range(len(sn) - 1)}
        if gs and len(ga & gs) / len(gs) > 0.8:
            fake.append(str(r.get('id')))
    if miss:
        out.append(('规则ask', f'{len(miss)}/{len(rs)} 条规则无 ask（用户问不中，只剩维护者记得）：'
                               f'{", ".join(miss[:8])}{" …" if len(miss) > 8 else ""}'))
    if fake:
        out.append(('规则ask', f'{len(fake)} 条 ask 与 summary 雷同（假补：字段有了但用户不会这么问）：'
                               f'{", ".join(fake[:8])}{" …" if len(fake) > 8 else ""}'))
    return out


def _sig_cmd_executable(_route_override=None, _manifest_override=None):
    """信号源34：manifest/探针声称的命令，入口是否真的认（S148）。

    **为什么必须有这一条**：S148 全局审计查出 12 条幽灵命令——
    `manifest.json` 登记了 `tvbox inspect scan` / `tvbox janitor list` 等，
    两个技能的 SKILL.md 也写明了用法，但入口 `ROUTE` 里**根本没有这两个词**，
    实跑一律返回「未知子命令: inspect」。

    后果不是"少个功能"，是**规则册成了假账**：M-R83 的探针写着 `tvbox inspect scan`，
    `conductor rules M-R83` 会显示"有探针"——看的人以为这条规则被守着，
    实际上守着它的东西不存在。**有幽灵探针比没有探针更坏。**

    **判据**：取 manifest 里每条命令的前两段（命令族），
    与入口 `ROUTE` 的词表比对；manifest 说要用、入口不认 = 幽灵。
    这条路不走"实跑命令"（慢且会碰云端），只做**静态存在性**判定——
    它要回答的是"这个命令族存不存在"，不是"这条命令这次跑得通不通"。

    _route_override / _manifest_override：仅供探针注入验证捕获能力。
    """
    out = []
    # ① 入口可运行词表 = ROUTE 字典键 ∪ 硬编码特殊分支
    #
    # **这里踩过一次坑，值得记**：第一版只读 ROUTE 字典，结果把
    # `chain` / `run` / `p` / `manifest` / `contracts` 全报成幽灵命令——
    # 它们确实不在 ROUTE 里，但入口用 `if verb == 'xxx':` 的**硬编码分支**处理，
    # 实跑完全正常。**判据取错了地方，就会制造假阳性**，而假阳性会淹没真问题
    # （12 个真幽灵里混进 5 个假的，看起来像"整个入口都坏"）。
    # 故词表必须是「入口真正认的全部词」，不是"某个数据结构里的键"。
    # 硬编码分支从源码里抽 `if verb == 'x':` / `verb in ('a','b')`，不手抄——
    # 手抄清单会随入口改动过期，这正是 M-R38 要防的。
    route = _route_override
    if route is None:
        fp = '/usr/local/bin/tvbox'
        if not os.path.exists(fp):
            return [('命令执行器', '入口脚本 /usr/local/bin/tvbox 不存在——**无法判定**（M-R60）')]
        try:
            src = open(fp, encoding='utf-8').read()
        except Exception as e:
            return [('命令执行器', f'入口脚本读取失败——**无法判定**（M-R60）：{type(e).__name__}')]
        m = re.search(r'^ROUTE\s*=\s*\{(.*?)^\}', src, re.S | re.M)
        if not m:
            return [('命令执行器', '入口脚本里找不到 ROUTE 分发表——**无法判定**（M-R60）')]
        route = set(re.findall(r"'([\w-]+)':", m.group(1)))
        # 硬编码分支：if verb == 'manifest': / if verb in ('a', 'b'):
        for grp in re.findall(r"if\s+verb\s*==\s*'([\w-]+)'", src):
            route.add(grp)
        for grp in re.findall(r"if\s+verb\s+in\s*\(([^)]*)\)", src):
            route |= set(re.findall(r"'([\w-]+)'", grp))
    if not route:
        return [('命令执行器', '可运行词表为空——读失败与真空不可区分（M-R60）')]

    # ② manifest 命令族
    man = _manifest_override
    if man is None:
        fp = os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'manifest.json')
        if not os.path.exists(fp):
            return [('命令执行器', 'manifest.json 不存在——**无法判定**（M-R60）')]
        try:
            man = json.load(open(fp, encoding='utf-8'))
        except Exception as e:
            return [('命令执行器', f'manifest.json 读取失败——**无法判定**（M-R60）：{type(e).__name__}')]
    cmds = []

    def _walk(o):
        if isinstance(o, dict):
            if isinstance(o.get('cmd'), str):
                cmds.append(o['cmd'])
            for v in o.values():
                _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)
    _walk(man)
    if not cmds:
        return [('命令执行器', 'manifest 里 0 条命令——读失败与真空不可区分（M-R60）')]

    ghost = {}
    for c in cmds:
        toks = [x for x in c.replace('tvbox', '').strip().split()
                if x and not x.startswith('<') and not x.startswith('"') and not x.startswith('-')]
        if not toks:
            continue
        first = toks[0]
        if first not in route:
            ghost.setdefault(first, []).append(c)
    for verb, cs in sorted(ghost.items()):
        out.append(('命令执行器',
                    f'入口没有 `{verb}` 这个词，但 manifest 登记了 {len(cs)} 条命令'
                    f'（如 `{cs[0]}`）——实跑会报「未知子命令」，'
                    f'看 manifest 的人以为能用（M-R38 幽灵命令）'))
    return out


def _sig_core_db_rows(_api_override=None):
    """信号源23：核心库行数归零/骤降——三态（M-R60）：
      ① 读失败 → 报「无法判定」（绝不折叠成 0 行归零）
      ② 真·空（ok=True 且 0 行）→ 报「归零」
      ③ 行数较上次锚点骤降 >50% → 报「骤降」
    锚点落 core_db_rows.json（latest_count/ts）；首次运行只建锚不报。
    _api_override：仅供探针注入合成 API；正常调用为 None。"""
    out = []
    anchors = {}
    try:
        with open(_ROWS_ANCHOR_FP, encoding='utf-8') as f:
            anchors = json.load(f)
    except Exception:
        anchors = {}
    changed = False
    for label, dbid in CORE_DB.items():
        try:
            if _api_override is not None:
                rows = _api_override(dbid)
            else:
                import subprocess as _sp
                q = os.path.join(SKILL_ROOT, 'library', 'database', 'query_database_record.py')
                if not os.path.exists(q):
                    q = '/opt/sandbox-prefetched/blobs/skill/library/latest/database/query_database_record.py'
                r = _sp.run(['python3', q, '--database-id', dbid, '--page-size', '200'],
                            capture_output=True, text=True, timeout=40)
                d = json.loads(r.stdout or '{}')
                rows = d.get('results') if isinstance(d.get('results'), list) else None
                # **S155 修：必须翻页取全量，否则 n 恒等于 min(真实行数, 200)。**
                # 事故：`--page-size 200` 只取首页就 `len(rows)`，于是
                #   DB_SRC 实际 801 条 → n=200；DB_DEAD 实际 225 条 → n=200。
                # 后果比"数字不准"严重得多：
                #   ① 锚点被写成 200，而真实值是 801——**锚点从建立那天起就是错的**；
                #   ② 骤降判据 `n < prev*0.5` 用两个假数比大小：
                #      库真跌到 300（跌幅 62%）时读出来仍是 200，相对锚点 200 **纹丝不动** → 不报警。
                #      **守护器在最该叫的时候失声。**
                # 这坑 S058 已经踩过一次（"has_more:True 说明只查 page-size 会造成
                #   『看起来只有 200 条』的假象"），却在守护核心数据资产的位置又犯了一遍。
                if isinstance(rows, list) and d.get('has_more'):
                    cur, guard = d.get('next_cursor'), 0
                    while cur and guard < 60:
                        r2 = _sp.run(['python3', q, '--database-id', dbid,
                                      '--page-size', '200', '--start-cursor', cur],
                                     capture_output=True, text=True, timeout=40)
                        try:
                            d2 = json.loads(r2.stdout or '{}')
                            more = d2.get('results') if isinstance(d2.get('results'), list) else []
                        except Exception:
                            break
                        rows = rows + more
                        cur = d2.get('next_cursor') if d2.get('has_more') else None
                        guard += 1
        except Exception:
            rows = None
        if rows is None:
            out.append({'skill': 'ecosys-conductor', 'type': '核心库行数守护',
                        'evidence': f'{label}（{dbid}）读取失败——**无法判定**行数（勿当空库，M-R60）'})
            continue
        n = len(rows)
        prev = anchors.get(dbid, {}).get('count')
        if n == 0 and dbid in RETIRED_DB:
            continue    # 设计停用库：0 行是终态，不报（否则恒报=假阳性淹没真问题）
        if n == 0:
            out.append({'skill': 'ecosys-conductor', 'type': '核心库行数守护',
                        'evidence': f'{label}（{dbid}）**行数归零**——核心数据资产可能丢失'
                                    f'（上次锚点 {prev if prev is not None else "无"}）'})
        elif prev is not None and prev > 0 and n < prev * 0.5:
            out.append({'skill': 'ecosys-conductor', 'type': '核心库行数守护',
                        'evidence': f'{label}（{dbid}）行数骤降：{prev} → {n}（降幅 {1 - n / prev:.0%}）——疑似误删'})
        if anchors.get(dbid, {}).get('count') != n:
            anchors[dbid] = {'count': n, 'ts': datetime.datetime.now().isoformat(timespec='seconds')}
            changed = True
    if changed and _api_override is None:
        try:
            with open(_ROWS_ANCHOR_FP, 'w', encoding='utf-8') as f:
                json.dump(anchors, f, ensure_ascii=False, indent=1)
        except Exception:
            pass
    return out


def cmd_direction(args):
    sugg = []
    # 证据1: backlog 兑现（S058 抽函数，可探针）
    sugg += _sig_backlog()
    # 证据2: 生态空白（S058 抽函数，可探针）
    sugg += _sig_eco_blank()
    # 证据3/4/5: 契约漂移 + 快照时效 + 路由盲区（S052 新增，判定器需受控单测实证）
    for s in ECO_SKILLS:
        for sig, typ in ((_sig_stale_cl, '契约漂移'), (_sig_route_blind, '路由盲区')):
            hit = sig(s)
            if hit:
                sugg.append({'skill': s, 'type': typ, 'evidence': hit[:80]})
    snap_hit = _sig_stale_snap(SNAPSHOT_OWNER)
    if snap_hit:
        sugg.append({'skill': SNAPSHOT_OWNER, 'type': '快照时效', 'evidence': snap_hit[:80]})
    # 证据6: 文档-实现漂移（S055 新增）——设计文档命令表 vs 实际 DISPATCH
    for typ, desc in _sig_doc_gap():
        sugg.append({'skill': 'ecosys-conductor', 'type': typ, 'evidence': desc[:80]})
    # 证据7: 信号源自检（S056 新增）——检测器依赖不可达=静默失效，防"装健康的哑巴"
    for typ, desc in _sig_health():
        sugg.append({'skill': 'ecosys-conductor', 'type': typ, 'evidence': desc[:80]})
    # 证据9: 技能停滞（S069·M-R45）——契约齐备但无可执行脚本
    sugg += _sig_stagnant()
    # 证据10: 入口内联命令一致性（S074·M-R40 跨层形态）——入口内联命令 vs manifest 登记
    sugg += _sig_inline_cmd()
    # 证据11: 评分器自检（S077·M-R53/M-R54）——度量器自身也是被测对象
    sugg += _sig_scorer()
    # 证据12: 留痕完整性（S080·M-R32 变体）——数据基座存在性/完整性/时效
    sugg += _sig_ledger()
    # 证据13: 执行层自检（S081·M-R59）——守护者的共同底座 _sh 三态区分
    sugg += _sig_exec()
    # 证据14: 快照基座自检（S082·M-R60）——跨技能共享数据基座（watchdog 环比基准）三态区分
    sugg += _sig_snapshot_base()
    # 证据15: 读表基座自检（S083·M-R60）——hub core.rows_of 三态读取（21+ 命令共用）
    sugg += _sig_rows_base()
    # 证据16: 基座折叠扫描（S084·M-R60⑨）——静态扫描全生态"except→return 空"折叠候选
    sugg += _sig_fold_scan()
    # 证据17: 云端读取语义自检（S086·M-R60）——link._root_nodes 三态语义可区分（结构失配≠空库）
    sugg += _sig_cloud_nodes_base()
    sugg += _sig_upstream_probe()
    # 证据19: 上游跑路检查器自检（S090）——M18 三态语义（无法判定不得产遗留）
    sugg += _sig_upstream_gone()
    # 证据20: 生态台账版本一致性（S095·M-R40 副本静默过期）——DB_ECO vs 各技能 SKILL.md
    sugg += _sig_eco_ledger()
    # 证据21: 经验对照缺口（S107·规则57 方法极）——升了版但无「借鉴/不适用」留痕
    sugg += _sig_lend_gap()
    # 证据22: 会话经验留痕缺口（S108·规则58）——双池最新时间 vs 最近工作锚点
    sugg += _sig_pool_gap()
    # 留痕
    today = datetime.date.today().isoformat()
    rec = {'date': today, 'suggestions': sugg}
    if not sugg:
        _names = ' / '.join(SIGNAL_SOURCES[k] for k in sorted(SIGNAL_SOURCES, key=int))
        print(f'✅ 暂无证据支持的进化建议（{len(SIGNAL_SOURCES)} 信号源均健康：{_names}）')
    else:
        print(f'🧭 进化方向 Top-{len(sugg)}（证据驱动）:')
        for i, x in enumerate(sugg, 1):
            print(f'  {i}. [{x["skill"]}/{x["type"]}] {x["evidence"]}')
    # 写留痕（对账基础；同日期重跑合并去重，禁止整体替换丢历史）
    try:
        hist = []
        if os.path.exists(PLAN_FILE):
            hist = json.load(open(PLAN_FILE, encoding='utf-8'))
        old = next((h for h in hist if h.get('date') == today and 'suggestions' in h), None)
        if old:
            seen = {(x['skill'], x['evidence']) for x in old.get('suggestions', [])}
            for x in sugg:
                if (x['skill'], x['evidence']) not in seen:
                    old['suggestions'].append(x)
                    seen.add((x['skill'], x['evidence']))
        else:
            hist.append(rec)
        json.dump(hist, open(PLAN_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print(f'\n💾 已留痕 evolution_plan.json（{sum(1 for h in hist if "suggestions" in h)} 期建议）——下期 assess 回验兑现率')
    except Exception as e:
        print(f'（留痕失败：{e}）')

# ================= 第二批：冲突仲裁 / 交接契约 / 生态空白 / 复盘 / 对账 =================

# 写目标的粗粒度识别：任务串里出现的可写目标关键词
WRITE_TARGETS = {
    'monitors.json': 'watchdog登记册', 'flow.json': 'flow契约', 'SKILL.md': '技能说明',
    'manifest.json': '路由清单', '台账': '会话台账', '经验库': '经验库',
    '源库': '源数据库', '快照': '权威快照', '报告': '监测报告',
}

def cmd_conflict(args):
    tasks = args if args else []
    if not tasks:
        print('用法: tvbox conductor conflict "任务1" "任务2" ...（检测多任务写同一目标）')
        return
    tmap = collections.defaultdict(list)
    for t in tasks:
        if _classify(t) == 'read':
            continue
        for kw, tgt in WRITE_TARGETS.items():
            if kw in t:
                tmap[tgt].append(t)
    clashes = {k: v for k, v in tmap.items() if len(v) > 1}
    if not clashes:
        print('✅ 无写冲突——各写库任务目标互不重叠，可按 sprint 串行执行')
        return
    print('⚠️ 写冲突检测（同一目标多任务写，须排序）:')
    for tgt, ts in clashes.items():
        print(f'  🎯 {tgt}:')
        for t in ts:
            print(f'     - {t}')
        print(f'     💡 建议：合并为一次写，或按「先读最新→再写」排序')
    print(f'\n共 {len(clashes)} 处冲突，{len(clashes)} 处均须人工定序后方可执行')

def cmd_handoff(args):
    if len(args) < 2:
        print('用法: tvbox conductor handoff <技能A> <技能B>（检查 A→B 交接契约）')
        return
    a, b = args[0], args[1]
    out = _sh('tvbox contracts 2>&1')
    lines = out.splitlines()
    seg, found = False, False
    print(f'🔗 交接契约检查: {a} → {b}')
    for ln in lines:
        if a in ln:
            seg, found = True, True
        elif ln.strip() and not ln.startswith(' ') and seg is True and a not in ln:
            seg = False
        if seg and (b in ln or a in ln):
            print('  ' + ln.strip())
    if not found:
        print(f'  ❓ contracts 中无 {a} 相关条目——检查 tvbox contracts 全文:')
        for ln in lines[:15]:
            print('  ' + ln)
    print('\n💡 判读：A 的输出字段须覆盖 B 的输入要求；缺字段 = 交接断链')

def cmd_decide(args):
    """雷达候选决策留痕（M-R22 自用）：expand-eco 的候选必须件件有去向。"""
    opts = {'--topic': '', '--verdict': '暂不', '--note': ''}
    i = 0
    while i < len(args):
        if args[i] in opts and i + 1 < len(args):
            opts[args[i]] = args[i + 1]
            i += 2
        else:
            i += 1
    if not opts['--topic']:
        print('用法: tvbox conductor decide --topic <候选> --verdict <扩面/暂不/观察> --note <理由>')
        return
    rec = {'date': datetime.date.today().isoformat(), 'topic': opts['--topic'],
           'verdict': opts['--verdict'], 'note': opts['--note']}
    hist = json.load(open(PLAN_FILE, encoding='utf-8')) if os.path.exists(PLAN_FILE) else []
    hist = [h for h in hist if not (isinstance(h, dict) and h.get('topic') == rec['topic'])]
    hist.append(rec)
    json.dump(hist, open(PLAN_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f"💾 决策留痕: {rec['topic']} → {rec['verdict']}（{rec['note']}）")

def cmd_retract(args):
    """撤回已证伪的建议（如检测器误报修正后，留痕里的过期建议会永久污染采纳率）。
    merge 语义：仅对匹配项打 retracted 标记，保留历史（体现进化过程），禁止删除。
    匹配用结构化字段 skill(+type)，签名用 | 分隔——禁用 bigram（模板共性词会淹没区分度，S053 实证）。"""
    opts = {'--match': '', '--reason': '', '--skill': '', '--type': ''}
    i = 0
    while i < len(args):
        if args[i] in opts and i + 1 < len(args):
            opts[args[i]] = args[i + 1]
            i += 2
        else:
            i += 1
    if not (opts['--skill'] or opts['--match']):
        print('用法: tvbox conductor retract --skill <技能> [--type <类型>] --reason <证伪理由>')
        print('      或 --match <证据子串>（精确子串，非模糊）')
        return
    hist = json.load(open(PLAN_FILE, encoding='utf-8')) if os.path.exists(PLAN_FILE) else []
    n = 0
    for h in hist:
        for x in h.get('suggestions', []):
            if x.get('retracted'):
                continue
            hit = False
            if opts['--skill']:
                hit = (x.get('skill') == opts['--skill'] and
                       (not opts['--type'] or x.get('type') == opts['--type']))
            elif opts['--match']:
                hit = opts['--match'] in x.get('evidence', '')   # 精确子串
            if hit:
                x['retracted'] = opts['--reason'] or '已证伪'
                n += 1
    json.dump(hist, open(PLAN_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    # 回读验证（M-R24：写完必须回读历史仍在）
    back = json.load(open(PLAN_FILE, encoding='utf-8'))
    total_sug = sum(len(h.get('suggestions', [])) for h in back)
    print(f'🗑️ 已撤回 {n} 条建议（{opts["--reason"]}）｜ 回读验证：留痕仍存 {total_sug} 条建议未丢失')

def cmd_expand_eco(args):
    print('🌐 生态空白识别（拓展方向雷达）')
    print()
    # ① manifest 技能负载（过载检测）
    try:
        d = json.load(open(MANIFEST, encoding='utf-8'))
        load = collections.Counter(c.get('skill', '?') for c in d.get('commands', []))
        mx = max(load.values()) if load else 0
        print('① 路由负载（命令数/技能，过载=明显高于均值）:')
        avg = sum(load.values()) / len(load) if load else 0
        for s, n in load.most_common():
            mark = ' ⚠️ 过载' if n > avg * 1.5 else ''
            print(f'   {s:24s} {n:3d} 条{mark}')
        print(f'   （均值 {avg:.1f}）')
    except Exception as e:
        print(f'① manifest 读取失败：{e}')
    print()
    # ② watchdog expand 的新实体（地图外业务）
    ex = _sh('tvbox watchdog expand 2>&1', cache=True)
    m = re.search(r'🆕 新实体[^\n]*\n((?:\s+- .+\n?)+)', ex)
    if m:
        print('② 风险面地图外的新实体（watchdog expand）:')
        for line in m.group(1).strip().splitlines():
            print('   ' + line.strip())
    else:
        print('② 风险面地图外新实体: ✅ 无')
    print()
    print('③ 拓展判据：某意图跨 3+ 技能该合并 / 某技能负载>1.5×均值该拆 / 新实体≥2次该建面')
    # ④ 雷达决策留痕（M-R22 自用）
    if os.path.exists(PLAN_FILE):
        try:
            hist = json.load(open(PLAN_FILE, encoding='utf-8'))
            dec = [h for h in hist if isinstance(h, dict) and h.get('topic')]
            if dec:
                print('④ 雷达决策留痕:')
                for x in dec:
                    print(f"   [{x.get('verdict')}] {x.get('topic')}（{x.get('note','')}）")
        except Exception:
            pass

# verify 判据解析器（S061 抽出为函数，便于 signal-check 探针保护——防正则措辞漂移静默失效）
def _parse_probe_ratio(out):
    """解析 signal-check 输出「探针结果: X/Y」→ (X,Y) 或 None。"""
    m = re.search(r'探针结果:\s*(\d+)/(\d+)', out)
    return (int(m.group(1)), int(m.group(2))) if m else None

def _parse_assess_score(out):
    """解析 assess 输出「⑦ 综合评分: N/100」→ N 或 None。"""
    m = re.search(r'⑦ 综合评分:\s*(\d+)/(\d+)', out)
    return (int(m.group(1)), int(m.group(2))) if m else None

def _parse_evolve_ready(out):
    """解析 evolve scan 输出「就绪 X/Y」→ (X,Y) 或 None。"""
    m = re.search(r'就绪\s*(\d+)\s*/\s*(\d+)', out)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _parse_watchdog_status(out):
    """解析 assess 输出里的 watchdog 结论 → True(无遗留) / False(有遗留) / None(无法判定)。
    S079：原实现是**裸子串** `'无遗留' in a_out` —— 无法区分两种 False：
      ① 真·有遗留条目（命令正常，确实报了 N 项）
      ② 命令失败/超时/措辞漂移（输出为空或换了说法，根本没产出结论行）
    两者都返回 False → 判据④ 显示「有遗留」，把"无法判定"**伪装成"有遗留"**
    （M-R32「装健康的哑巴」的镜像：**装失败的哑巴**，误导排查方向）。
    三态区分：命中「无遗留」/「0 项遗留」→ True；命中「N 项遗留」(N>0)/「有遗留」→ False；
    都没有（空输出/措辞漂移）→ None（判据④据此报「无法判定」，而**非**「有遗留」）。"""
    if re.search(r'无遗留|0\s*项遗留|合计\s*0\s*项', out):
        return True
    m = re.search(r'合计\s*(\d+)\s*项遗留', out)
    if m:
        return int(m.group(1)) == 0
    if re.search(r'\d+\s*项遗留|有遗留', out):
        return False
    # 既无"无遗留/0项"也无"N项遗留/有遗留"——命令未产出可识别结论（空/超时/措辞漂移）
    return None


def _check_index_links(_txt_override=None, _skills_override=None):
    """外置索引完整性校验（S135 立，S149 修判据覆盖面）。

    **为什么必须守**：体积达标但索引指向不存在的文件 = **比不瘦身更危险**——人照着索引
    找不到内容，还以为自己漏看了。（历史事故：`--apply` 覆盖写让 5 条索引指向同一文件，
    而那个文件只剩 1 节。）

    **S149 修的假绿**：原实现只认 `正本已外置**：`docs/x`` 这一种固定句式。
    我把 probe-hub / source-hub 的正文外置成最常见的 **Markdown 链接** `[x](docs/x)` 后，
    注入一个不存在的链接做反向验证——**verify ⑦ 照样报「索引完整」**。
    也就是说：写了那个句式的少数条目被验过，其余外置索引**从来没被验过**。
    现在两种写法都认。

    _txt_override / _skills_override：仅供探针注入（合成 SKILL.md 文本 / 技能名）。
    返回 (ok, bad_list)。
    """
    bad = []
    skills = _skills_override if _skills_override is not None else ECO_SKILLS
    for s in skills:
        if _txt_override is not None:
            txt = _txt_override
        else:
            skp = os.path.join(SKILL_ROOT, s, 'SKILL.md')
            if not os.path.exists(skp):
                continue
            txt = open(skp, encoding='utf-8').read()
        # 两种写法：①`正本已外置**：`docs/x`` ②Markdown 链接 `](docs/x)`
        rels = set(m.group(1) for m in re.finditer(r'正本已外置\**\s*[：:]\s*`(docs/[^`#]+)', txt))
        rels |= set(m.group(1) for m in re.finditer(r'\]\((docs/[^)#]+)\)', txt))
        for rel in sorted(rels):
            if rel.startswith(('http', '#')):
                continue
            if not os.path.exists(os.path.join(SKILL_ROOT, s, rel)):
                bad.append(f'{s}→{rel}')
        if _txt_override is not None:
            break   # 注入模式只看合成文本，不重复扫全生态
    # S137 补漏：规则总册自己也外置了两条，须验文件 + **锚点真存在**
    mp2 = '/workspace/生态1015_TVBox生态全局规则.md'
    if _txt_override is None and os.path.exists(mp2):
        mtxt = open(mp2, encoding='utf-8').read()
        for m in re.finditer(r'`(docs/[^`#]+)#([^`]+)`', mtxt):
            rel, anchor = m.group(1), m.group(2).strip()
            fp = os.path.join('/workspace', rel)
            if not os.path.exists(fp):
                bad.append(f'总册→{rel}（文件不存在）')
                continue
            if not re.search(r'^#+\s*' + re.escape(anchor) + r'\b', open(fp, encoding='utf-8').read(), re.M):
                bad.append(f'总册→{rel}#{anchor}（锚点不存在，改了标题即断链）')
    return (not bad), bad


def cmd_verify(args):
    """终验一键（S060）：把"终验六判据"固化——一条命令跑完全部验收并输出通过/不通过。
    六判据：①探针 N/N（N=注册表信号源数，S069 起派生）②assess 综合 ③evolve scan 6/6 ④watchdog 无遗留 ⑤三件套齐备 ⑥留痕洁净。
    提速（S065）：①探针独占先跑（M-R35 造脏道不与读共享态并行）；②③并行；④从 assess 输出解析
    （assess 内部已跑 watchdog，独立再跑=检查器二次遍历，纯重复 ~14.5s）。"""
    print(f"🧪 终验十二判据（一键）· ⑩ 清道夫 ⑪ 监察司 ⑫ 全技能监护覆盖(S155·元监察)")
    print()
    # ① 探针独占先跑（造脏道，防竞态 M-R35）
    sc_out = _sh('tvbox conductor signal-check 2>&1', timeout=120)
    pr = _parse_probe_ratio(sc_out)
    # S075：判据① 现在要求「探针全通过」**且**「假阳性基线为空」——后者防「检测器恒报
    # 使 hit 假通过」（如 S069 假阳性回归）：探针只能证明能捕获，不能证明不误报。
    # S078：再要求「基线覆盖无缺口」——基线不完整者 hit 仍会假通过，缺一个源与"基线为空"
    # 同等危险（M-R53②「知道不完整但不阻断仍会放行」）。
    fp_alert = '假阳性基线告警' in sc_out
    base_gap = '假阳性基线覆盖缺口' in sc_out
    silent_alert = '探针静默异常' in sc_out   # ★ S155：探针崩了=显式判红（原被 except 吞=静默假通过）
    # ★ S177 步3 实测补（两个真缺陷）：
    #   (a) **分支兜底指错了**。实际跑 `signal-check` 得到「探针结果: 43/45」，
    #       而 fp_alert / base_gap / silent_alert **全为 False**（原文里一个关键词都没命中），
    #       于是落到最后的 `else '【基线覆盖缺口】'`——**把"探针没全通过"说成"基线有缺口"**。
    #       人照着这句去查基线，永远查不到，因为问题根本不在基线。
    #       真实原因就在同一行左边：43/45。判红理由应当是"有 2 个源没通过"。
    #   (b) **判红不点名**。43/45 里那 2 个是谁，输出里没有。探针明细本来就在 sc_out 里
    #       （每条 `❌ 信号源N ...`），只是 verify 没去读。这里抠出来。
    _gap_ids, _fail_ids = [], []
    for _ln in (sc_out or '').splitlines():
        if '假阳性基线覆盖缺口' in _ln and '缺失信号源' in _ln:
            _gap_ids += [x.strip() for x in _ln.split('缺失信号源', 1)[1]
                         .split('——')[0].split(',') if x.strip()]
        # 探针明细行形如 `  ❌ 信号源12 xxx：捕获=否 ...`
        if '❌' in _ln and '信号源' in _ln:
            _seg = _ln.split('信号源', 1)[1]
            _num = ''
            for _ch in _seg:
                if _ch.isdigit():
                    _num += _ch
                else:
                    break
            if _num:
                _fail_ids.append(_num)
    sc_ok = bool(pr and pr[0] == pr[1]) and not fp_alert and not base_gap and not silent_alert
    # ②③ 并行；④不再单独跑 watchdog——assess ②业务面输出已含同款 watchdog 结论，
    #    独立再跑一遍会让检查器被遍历两次（verify 内 assess 跑一次 + 此处再一次），
    #    纯重复 ~14.5s（S065）。改从 assess 输出解析，判据口径不变。
    # M-R114：②③ 两条本地 CLI 并行 → D 档（2 ≤ 档位上限，合法）
    # ★ S163：改走统一入口——原 `_TPE(max_workers=2)` 硬编码 2，且先 `_resolve_workers('D', 2)`
    #   只为校验再丢弃返回值，属于"校验给审计看、实际不听"的假合规。
    from concurrency import run_bounded, TIER_LOCAL
    _c2 = ['tvbox conductor assess 2>&1', 'tvbox evolve scan 2>&1']
    _t2 = [180, 60]
    _rep2 = run_bounded(list(zip(_c2, _t2)), lambda p: _sh(p[0], p[1]), tier=TIER_LOCAL,
                        workers=2, label='verify-23', keep_order=True, on_error='record')
    a_out = _rep2.results[0] if _rep2.results[0] is not None else '[并发异常] assess 未跑完'
    e_out = _rep2.results[1] if _rep2.results[1] is not None else '[并发异常] evolve 未跑完'
    asc = _parse_assess_score(a_out)
    a_val = asc[0] if asc else 0
    # 判据口径（S060 校准）：不要求 100 分——radar 维度含"真 pending 待办"会正常扣分，
    # 只要求"健康底线"：契约/业务维满分 + radar ≥80（闭环良好）
    a_ok = a_val >= 90
    ev = _parse_evolve_ready(e_out)
    e_ok = bool(ev and ev[0] == ev[1])
    # S079：判据④ 改用三态解析器（原裸子串 '无遗留' in a_out 无法区分「命令失败」与「真有遗留」，
    # 见 _parse_watchdog_status 文档）。w_st=None=无法判定（空/超时/措辞漂移），不再伪装成"有遗留"。
    w_st = _parse_watchdog_status(a_out)
    w_ok = (w_st is True)
    # ⑤ 三件套契约完整（S060 校准：不比对 SKILL vs flow 版本号——二者本就不同步，
    # 业界常态 flow.json 是契约内部版本；只查三件套齐备 = evolve scan 已覆盖的语义）
    vbad = []
    for s in ECO_SKILLS:
        p = os.path.join(SKILL_ROOT, s)
        for f in ('SKILL.md', 'CHANGELOG.md', 'scripts/flow.json'):
            if not os.path.exists(os.path.join(p, f)):
                vbad.append(f'{s}缺{f}')
    v_ok = not vbad
    # ⑥ 留痕洁净（evolution_plan 无未撤回的探针污染）
    c_ok = True
    try:
        for h in json.load(open(PLAN_FILE, encoding='utf-8')):
            for x in h.get('suggestions', []):
                if 'zzprobe' in x.get('evidence', '') and not x.get('retracted'):
                    c_ok = False
    except Exception:
        pass
    # ⑦ 体积（S135 M-R77）：技能正本 + 规则总册不得超缓冲线。
    # 这是 M-R77④ 的**强制力所在**——audit-growth 平时没人主动跑，verify 是每次收尾必跑的，
    # 把体积放进 verify，超标者才真的「过不了验收」。
    s_ok, s_bad = True, []
    try:
        hard, buf = _skill_size_limits()
        for s in ECO_SKILLS:
            skp = os.path.join(SKILL_ROOT, s, 'SKILL.md')
            if os.path.exists(skp):
                b = os.path.getsize(skp)
                if b > buf:
                    s_bad.append(f'{s} {b/1024:.1f}KB' + ('(超硬上限)' if b > hard else ''))
                    s_ok = False
        mp = '/workspace/生态1015_TVBox生态全局规则.md'
        if os.path.exists(mp):
            b = os.path.getsize(mp)
            if b > buf:
                s_bad.append(f'规则总册 {b/1024:.1f}KB' + ('(超硬上限)' if b > hard else ''))
                s_ok = False
    except Exception as e:
        s_ok, s_bad = False, [f'阈值读取失败 {e}']
    # ⑦-b 外置索引完整性（S135 立 · S149 抽成函数并扩判据覆盖面，见函数 docstring）
    try:
        i_ok, i_bad = _check_index_links()
    except Exception as e:
        i_ok, i_bad = False, [f'索引校验异常 {e}']
    # ⑧ 未完成事项账本（S136·M-R79「未完成不得无主」）——硬闭环所在。
    # 为什么放进 verify：清账司自己跑得再绿，只要没人**必须**跑，它就会退化成又一份死文档。
    # 判据⑧ 让「有欠账却不登记/登记了却没主/承诺了却没落账」直接验收不过。
    u_ok, u_bad = True, []
    try:
        import sys as _sys8
        u_dir = os.path.join(SKILL_ROOT, 'unfinished-audit', 'scripts')
        if u_dir not in _sys8.path:
            _sys8.path.insert(0, u_dir)
        import dispatch as _disp8       # 别名，避免与本文件同名对象冲撞
        probs8, warns8 = _disp8.verify_ledger()
        if probs8:
            u_ok = False
            u_bad = probs8[:4]
        elif warns8:
            u_bad = [f'超期 {len(warns8)} 项']     # 只提示不阻断
    except Exception as e:
        # 读不到账本 = 无法判定，**判红**（M-R80：折叠成「无欠账」是本技能最不能犯的错）
        u_ok, u_bad = False, [f'欠账单无法判定（{type(e).__name__}: {e}）——勿当作已清零']
    # ⑨ 治理刹车（S138·M-R94/M-R95）——验收层兜底。
    # 只挂在 `waste` 上是不够的：waste 是「我想看才跑」的诊断命令，一天不跑就一天不知道
    # 自己已经在原地打转。verify 是**每次收尾都必须跑**的门，刹车挂这里才算真刹车。
    # S124 复盘诊断「治理无刹车」、提了 B1/B2/B3、末句写明「请明确指示」——然后刹车没装，
    # 治理又跑了 9 轮。教训不是「忘了装」，是**诊断不进门禁，就永远只是文档**。
    # 判读尺度：red（连续纯治理超限/治理占比超线）判红阻断；yellow/unknown 只提示不计红
    # ——刹车本身不能变成新的「永远报红」噪声源（M-R80）。
    g_ok, g_msgs = True, []
    try:
        import importlib as _il9
        # 必须显式把本脚本目录塞进 path：verify 可能是被 `tvbox` 以文件路径 exec 的，
        # sys.path[0] 未必指向 scripts/。不这么写，brake 会稳定 ModuleNotFoundError——
        # 而这条错误若被 except 吞成「无法判定」，就成了 M-R80 禁止的静默降级。
        _here9 = os.path.dirname(os.path.abspath(__file__))
        if _here9 not in sys.path:
            sys.path.insert(0, _here9)
        _bk = _il9.import_module('brake')
        _gstate, _gmsgs = _bk.check()
        g_msgs = [m for st, m in _gmsgs]
        if _gstate == 'red':
            g_ok = False
        elif _gstate == 'unknown':
            # 判不了 ≠ 通过：但也不判红（否则账本一坏就全站红）。标 unknown 显式告知。
            g_msgs = ['⚠️ 无法判定：' + g_msgs[0]] if g_msgs else ['⚠️ 无法判定']
    except Exception as e:
        g_ok, g_msgs = False, [f'刹车模块不可用（{type(e).__name__}: {e}）——勿当作无违反']
    # ⑩⑪⑫ 元监察判据计算（S155）：直接由 signal-check 输出解析 ⑩⑪；⑫ 取监护器真实健康数。
    import re as _re12
    def _src_pass(out, n):
        for line in out.splitlines():
            if f'信号源{n} ' in line:
                return ('捕获=是' in line) and ('清零=是' in line)
        return False
    _jok = _src_pass(sc_out, 35)
    _iok = _src_pass(sc_out, 37)
    _cov = _sig_skill_coverage()   # 默认含 ③ 覆盖缺口（轻量：仅文件读+编译，无副作用）
    _broken = [x for x in _cov if '缺 SKILL.md' in x['evidence'] or '编译' in x['evidence']]
    _gap = [x for x in _cov if x not in _broken]
    _cov_ok = (len(_broken) == 0)
    _cov_total = len([n for n in sorted(os.listdir(SKILL_ROOT))
                      if os.path.isdir(os.path.join(SKILL_ROOT, n))
                      and not n.startswith('.') and n != '__pycache__'])
    checks = [
        (f'① 探针 {len(SIGNAL_SOURCES)}/{len(SIGNAL_SOURCES)}', sc_ok,
         (f'{pr[0]}/{pr[1]}' if pr else '无输出')
         # ★ S156：原显示「39/39【假阳性告警】」= 数字满分却判红，读者无法判断信哪个。
         #   补一句"判红的原因不在探针数"，把矛盾点显式说清（M-R80：不许让人自己猜）。
         # ★ S177：把**具体是哪几个源**直接写进来（原先只说"有缺口"，要人再敲一条命令查）。
         #   并且**把兜底分支改对**：原先所有条件为假时落到"基线覆盖缺口"，
         #   而实测那种情况是"探针 43/45"——指错方向，让人去查一个不存在的问题。
         + ('【基线有检测器无注入即报→判红，与探针数无关，见 signal-check】' if fp_alert
            else f'【基线覆盖缺口：缺源 {",".join(_gap_ids)}】' if base_gap and _gap_ids
            else '【基线覆盖缺口（未能解析出源号，请看 signal-check 原文）】' if base_gap
            else '【探针异常】' if silent_alert
            else f'【未通过的源：{",".join(_fail_ids)}】' if _fail_ids
            else '【探针数与通过数不符，但未能定位到具体源，请看 signal-check 原文】'
            if not sc_ok else ' 基线净')),
        ('② assess 综合', a_ok, f'{a_val}/100 (≥90)'),
        ('③ evolve scan', e_ok, (f'{ev[0]}/{ev[1]}' if ev else '?')),
        ('④ watchdog', w_ok, ('无遗留' if w_ok else '有遗留' if w_st is False else '⚠️无法判定(命令未产出)')),
        ('⑤ 三件套齐备', v_ok, ('全齐备' if v_ok else ' '.join(vbad))),
        ('⑥ 留痕洁净', c_ok, ('无污染' if c_ok else '有探针污染')),
        ('⑦ 体积(M-R77)', s_ok and i_ok,
         ('、'.join(s_bad) if s_bad else '') + ('｜索引断链 ' + '、'.join(i_bad) if i_bad else '')
         or '全部 ≤缓冲线 且 索引完整'),
        ('⑧ 欠账单(M-R79)', u_ok,
         ('；'.join(u_bad) if u_bad else '无无主项 无未说明消失项')),
        ('⑨ 治理刹车(M-R94/95)', g_ok, ('；'.join(g_msgs) if g_msgs else '三刹车净')),
        # ⑩⑪⑫ 元监察三判据（S155）：用户点名"技能/探针/清道夫/监察司没人监察"
        #   + 范围扩张"所有技能都要有人监察"。⑩⑪ 直接从 signal-check 输出解析（避免重复 exec 监测模块）；
        #   ⑫ 调用监护器取当前生态真实健康数（轻量：仅文件读+编译，无副作用）。
        ('⑩ 清道夫健康', _jok, ('清道夫已被监察·收件箱健康' if _jok else '清道夫探针未通过/未注册')),
        ('⑪ 监察司自身', _iok, ('监察司已被监察·可运行' if _iok else '监察司探针未通过/未注册')),
        ('⑫ 全技能监护覆盖', _cov_ok,
         f'全 {_cov_total} 技能 · 结构异常 {len(_broken)} · 自监察缺口 {len(_gap)}'
         + ('｜异常: ' + '、'.join(x['skill'] for x in _broken) if _broken else '')),
    ]
    passed = sum(1 for _, ok, _ in checks if ok)
    for name, ok, detail in checks:
        print(f'  {"✅" if ok else "❌"} {name}：{detail}')
    print()
    n = len(checks)
    print(f'终验结果: {passed}/{n} 判据通过' + ('  🎉 全绿' if passed == n else '  ⚠️ 有未通过项，见上'))
    return passed == n

def cmd_retro(args):
    print('🔁 会话复盘（内环：教训→经验库，同类≥3次触发 skill-evolution）')
    print()
    qs = [
        ('1. 本会话哪一步最慢？', '等待/重复读库/不该串行 → 对应 T1/T2 哪条被违反'),
        ('2. 有没有返工？', '返工根因是否「未先 plan」（T3 违反）'),
        ('3. 路由对了吗？', '有没有该并行被串行、该 handoff 没检查'),
        ('4. 值得沉淀吗？', '教训用 tvbox lib-log 写入经验库（fix+reuse 字段必填）'),
    ]
    for q, hint in qs:
        print(f'  {q}')
        print(f'      ↳ {hint}')
    if args:
        print(f'\n📝 复盘要点「{" ".join(args)}」请用 tvbox lib-log 正式落库')

def cmd_evo_check(args):
    """采纳率对账：evolution_plan.json 的建议 vs 各技能 flow.json 的已清零。"""
    if not os.path.exists(PLAN_FILE):
        print('📊 采纳率对账：暂无留痕（先跑 tvbox conductor direction）')
        return
    hist = json.load(open(PLAN_FILE, encoding='utf-8'))
    total, adopted, retracted = 0, 0, 0
    ret_items = []
    print('📊 进化建议采纳率对账（外环闭环）')
    print()
    for h in hist:
        for s in h.get('suggestions', []):
            # S144 修：带 `retracted` 的建议已被**判定为误报/污染**（如 S059 并行竞态写窗口
            # 被 direction 读到的假证据），必须排除出采纳率分母。
            # 旧版不过滤 → 「已撤回的假建议」永远挂在"待兑现"里，把一个已经查清的污染
            # 当成永久欠账。assess（第 916 行）早已这样过滤，evo-check 漏了——两处口径必须一致。
            if s.get('retracted'):
                retracted += 1
                ret_items.append(s)
                continue
            total += 1
            hit = _is_adopted(s.get('skill', ''), s.get('evidence', ''))
            adopted += 1 if hit else 0
            print(f'  [{"✅已采纳" if hit else "⏳待兑现"}] [{s.get("skill")}] {s.get("evidence","")[:60]}')
    if ret_items:
        print(f'\n  🗑 已撤回 {retracted} 条（判定为误报/污染，**不计入分母**）：')
        for s in ret_items:
            print(f'     [{s.get("skill")}] {s.get("evidence","")[:50]} ← {str(s.get("retracted"))[:60]}')
    if total:
        print(f'\n采纳率: {adopted}/{total}（{100 * adopted // total}%）——建议只算数，兑现才算进化')

# ===================== S155：元监察四类守门人探针 =====================
def _sig_janitor(s=None):
    """信号源35：清道夫 janitor 健康（用户点名"清道夫没人监察"）。
    读 tvbox-janitor 的 janitor_state.json，查四类失守：
      ① 收件箱 node 自身被登记为待删项（自引用垃圾，S154 清过一次）
      ② 存在 moved==false 的未移动项（投递失败却标记安静）
      ③ 积压数超阈值（>50，长期无人清理）
      ④ 收件箱 node 字段为空/非法（配置漂移）
    返回 [] = 健康；否则每项一个问题证据。三态（M-R60）：读不到→显式告警，不当健康。"""
    out = []
    fp = os.path.join(SKILL_ROOT, 'tvbox-janitor', 'scripts', 'janitor_state.json')
    if not os.path.exists(fp):
        return [{'skill': 'tvbox-janitor', 'type': '清道夫健康', 'evidence': 'janitor_state.json 缺失（无法判定，非健康）'}]
    try:
        d = json.load(open(fp, encoding='utf-8'))
    except Exception as e:
        return [{'skill': 'tvbox-janitor', 'type': '清道夫健康', 'evidence': f'janitor_state.json 损坏（{type(e).__name__}: {e}）'}]
    inbox = d.get('inbox')
    items = d.get('items', [])
    # ① 自引用：收件箱把自己的 node 当待删项登记
    if inbox and any(i.get('id') == inbox for i in items):
        out.append({'skill': 'tvbox-janitor', 'type': '清道夫健康',
                    'evidence': f'收件箱自引用：inbox={inbox} 把自己登记为待删项（S154 清过的垃圾重现）'})
    # ② 未移动项
    unmoved = [i for i in items if i.get('moved') is False]
    if unmoved:
        out.append({'skill': 'tvbox-janitor', 'type': '清道夫健康',
                    'evidence': f'{len(unmoved)} 项待删物未移动（moved==false），投递失败却标记安静'})
    # ③ 积压超阈值 —— ★ S158 改判据：区分「清道夫没干完」与「用户还没删」
    #    原判据只有「>50 项」这半句。S158 实测：54 项里 54 项 moved==True，
    #    即**清道夫该做的全做完了**，剩下的是等用户去箱子里按单删。
    #    把"等用户"报成"清道夫失灵"，等于让检测器去催一件不归它管的事——
    #    而且它永远不会自己变绿，于是所有人学会无视它（真来了问题也没人看）。
    #    新判据看的是**滞留时长**：积压多但都是新的 = 正常；积压久 = 真堵。
    _STALE_DAYS = 7
    if len(items) > 50:
        _ages = []
        _now = datetime.datetime.now()
        for i in items:
            _a = (i.get('added') or '').strip()
            for _fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d'):
                try:
                    _ages.append((_now - datetime.datetime.strptime(_a, _fmt)).days); break
                except Exception:
                    continue
        _oldest = max(_ages) if _ages else None
        if _oldest is None:
            out.append({'skill': 'tvbox-janitor', 'type': '清道夫健康',
                        'evidence': f'收件箱积压 {len(items)} 项，但**无一项带可解析的 added 时间**——'
                                    f'无法判定滞留多久（不是"没问题"）'})
        elif _oldest > _STALE_DAYS:
            out.append({'skill': 'tvbox-janitor', 'type': '清道夫健康',
                        'evidence': f'收件箱积压 {len(items)} 项，最老一项已滞留 {_oldest} 天'
                                    f'（>{_STALE_DAYS} 天）——该催的是"去箱里删"，不是再扫一遍'})
        # 积压但都是新鲜的 → 正常，不报。这半句同样重要：**不报也是一种判断**。
    # ④ 收件箱 node 非法
    if not inbox or not re.match(r'^[A-Za-z0-9_]{8,64}$', str(inbox)):
        out.append({'skill': 'tvbox-janitor', 'type': '清道夫健康',
                    'evidence': f'收件箱 node 非法/空：{inbox!r}（配置漂移）'})
    return out


def _sig_probe_alive(s=None):
    """信号源36：探针体系自身存活（用户点名"探针没人监察"的结构性防护）。
    确认 conductor 模块内 `_sig_*` 探针函数未被误删/改名致 signal-check 引用缺失崩溃。
    返回 [] = 存活；否则报缺失。注：每个注册信号源都有活体探针，由 signal-check 覆盖对齐审计
    （4514）结构性保证；本探针额外防"函数定义整体消失"这类硬破坏。"""
    import inspect as _ins
    try:
        # 用 __file__ 读源码（鲁棒）：tvbox 以文件路径 exec 本模块时 sys.modules[__name__]
        # 解析不到，getsource 会抛异常致本探针误报"无法读取源码"（S155 实测修复）。
        with open(os.path.abspath(__file__), encoding='utf-8') as _fh:
            src = _fh.read()
    except Exception as e:
        return [{'skill': 'ecosys-conductor', 'type': '探针存活', 'evidence': f'无法读取模块源码（{type(e).__name__}: {e}）'}]
    defined = set(re.findall(r'^def (_sig_[A-Za-z0-9_]+)', src, re.M))
    CORE = {'_sig_health', '_sig_eco_ledger', '_sig_janitor', '_sig_inspect', '_sig_eco_link', '_sig_probe_alive'}
    missing = CORE - defined
    if missing:
        return [{'skill': 'ecosys-conductor', 'type': '探针存活', 'evidence': f'核心探针函数缺失(被删/改名): {",".join(sorted(missing))}'}]
    if len(defined) < 30:
        return [{'skill': 'ecosys-conductor', 'type': '探针存活', 'evidence': f'探针函数数异常: 仅 {len(defined)} 个(<30)，探针体系疑似被破坏'}]
    return []


def _sig_inspect(s=None):
    """信号源37：监察司 inspect 自身健康（用户点名"技能没人监察"之一）。
    确认监察司本身可运行：① watchdog.py/inspect_engine.py 可 import ② monitors.json 合法
    ③ M30 挂名项已清零（monitor 自称盯 X 实则调用 Y 的名实不符；M23-M29 已如实标 manual_track）。
    返回 [] = 健康；否则报问题。"""
    out = []
    wd = os.path.join(SKILL_ROOT, 'tvbox-inspect', 'scripts', 'inspect_engine.py')
    wdog = os.path.join(SKILL_ROOT, 'legacy-watchdog', 'scripts', 'watchdog.py')
    mfp = os.path.join(SKILL_ROOT, 'tvbox-inspect', 'monitors.json')
    for label, p in (('inspect_engine', wd), ('watchdog', wdog)):
        if not os.path.exists(p):
            out.append({'skill': 'tvbox-inspect', 'type': '监察司自身', 'evidence': f'{label} 模块缺失: {p}'})
            continue
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(f'_chk_{label}', p)
            _m = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_m)
        except Exception as e:
            out.append({'skill': 'tvbox-inspect', 'type': '监察司自身', 'evidence': f'{label} 无法 import（{type(e).__name__}: {str(e)[:60]}）'})
    if not os.path.exists(mfp):
        out.append({'skill': 'tvbox-inspect', 'type': '监察司自身', 'evidence': 'monitors.json 缺失'})
    else:
        try:
            _mj = json.load(open(mfp, encoding='utf-8'))
            for m in _mj.get('monitors', []):
                if m.get('check') == 'naming_compliance':
                    # naming_compliance 是合法检查器类型（M08 名实相符：名即"命名合规"）；
                    # 仅当 name/desc 均未提及 naming/命名、却声称做别的事，才判"挂名"(名实不符)。
                    _nm = (m.get('name', '') + ' ' + m.get('desc', ''))
                    if 'naming' in _nm.lower() or '命名' in _nm:
                        continue
                    out.append({'skill': 'tvbox-inspect', 'type': '监察司自身',
                                'evidence': f'监测项 {m.get("id")} 挂名: check=naming_compliance 却声称 {m.get("name")}'})
        except Exception as e:
            out.append({'skill': 'tvbox-inspect', 'type': '监察司自身', 'evidence': f'monitors.json 损坏（{type(e).__name__}: {e}）'})
    return out


def _sig_eco_link(s=None):
    """信号源38：大生态联动对账真实性（S150 假数根因回归防护）。
    LINK_DBS 是资料库↔云端↔生态库的对账映射（数据面备份/恢复通道，见 5090）。
    S150/S156 实证：DB_DEAD 曾误指旧空库 92dSuG6uUbxitGuyx4Saaf → ①对账报"0 行"(实际225)
    ②backup 导出的 DB_DEAD 实为 0 行（备份缺口）。本探针防"映射再悄悄指回退役空库"。
    返回 [] = 映射健康；否则报问题项。"""
    out = []
    RETIRED = {'92dSuG6uUbxitGuyx4Saaf'}   # 已知退役空库（S150 根因，不得再出现在映射里）
    for name, db in LINK_DBS.items():
        if db in RETIRED:
            out.append({'skill': 'ecosys-conductor', 'type': '大生态对账',
                        'evidence': f'{name} 仍指向退役空库 {db}（S150 假数根因回归）'})
    return out


def _sig_lib_protocol(strict=False, include_probe=False):
    """信号源40：资料库调用协议（S158·技能↔资料库）。

    **立此信号源的原因**（S158 体检实测）：
      全生态有 25 处自己写 `subprocess.run([... library/...])`，
      **其中 15 处没有三态返回**——失败时 `return None` / `return ''` / `return []`，
      调用方无从区分「库读不到」与「库是空的」。M-R60 早立了铁律，但它只落在
      `core.rows_of_t` 一家身上，**绕过 core 的人根本没继承这条约定**。
      按 M-R77「验证须独立于被验对象」：规矩写在注释里不算数，
      必须有一条检测器守着，否则下一个写脚本的人照样绕过。

    判定（两档，靠 strict 切换）：
      · 违规 A（硬）：生态技能的脚本里出现 `subprocess.run(...LIB...)` 调 **library 数据库脚本**
        （`database/`、`space_api.py`），且**既没走 core 也没做三态判定**。
        这类是"协议绕过 + 失败折叠"双重问题，必须报。
      · 违规 B（软，仅非 strict 时报）：三态判定了，但仍绕过 `core._run_lib`。
        不拦——它至少没错；只提示"能收敛就收敛"，避免一刀切引发大面积改动。

    白名单（`_LIB_PROTOCOL_ALLOW`）：登记后不报，每条须附理由。
      用于：① core.py 自身（它就是协议实现）② 已判定为**只读展示**、
      失败不影响决策的地方。**不许把"有折叠风险"的登记进白名单**——
      白名单一旦用来藏缺陷，就变成第二个 `except: pass`。

    严格模式（verify / 假阳性基线）**只查违规 A**：健康态必须返回空。
    这样它才有资格进假阳性基线，否则它会恒报。

    `include_probe=True`：连 `_probe_*.py` 一起扫。仅 signal-check 的注入验证用——
    注入后必须能抓到，否则"能抓到"这件事就没有被真实证明过（M-R77）。
    其余场合默认 False，避免探针文件被误当真实调用点。
    """
    db_scripts = ('database/batch_', 'database/query_database_record',
                  'database/get_database_content', 'database/get_database_schema',
                  'space_api.py')
    out = []
    root = pathlib.Path(SKILL_ROOT)
    for sk in ECO_SKILLS:
        sdir = root / sk / 'scripts'
        if not sdir.is_dir():
            continue
        for f in sorted(sdir.glob('*.py')):
            key = f'{sk}/{f.name}'
            if key in _LIB_PROTOCOL_ALLOW:
                continue
            # S158 防自噬：`_probe_*.py` 是 signal-check 的**受控造违**，不是真实调用点。
            # 不排除它，检测器会抓到自己的探针源码 → 健康态恒报 → 假阳性基线被污染
            # → 最终逼人去调基线数字（等于把检测器关掉）。
            if f.name.startswith('_probe_') and not include_probe:
                continue
            try:
                lines = f.read_text(errors='ignore').splitlines()
            except Exception:
                continue
            for i, ln in enumerate(lines, 1):
                if 'subprocess.run' not in ln:
                    continue
                if 'LIB' not in ln and 'library' not in ln:
                    continue
                # 往后看一小段，判断是不是在调数据库脚本
                ctx = '\n'.join(lines[i - 1:i + 14])
                if not any(s in ctx for s in db_scripts):
                    continue
                if '_run_lib' in ctx or 'core.' in ctx:
                    continue                      # 已走统一协议
                three = ('ok' in ctx and 'False' in ctx) or 'returncode' in ctx
                if three:
                    if strict:
                        continue                  # 严格模式只查硬违规
                    out.append({'skill': sk, 'type': '资料库调用协议',
                                'evidence': f'{key}:{i} 做了三态判定但仍绕过 core._run_lib'
                                            f'——建议收敛（不拦截）'})
                else:
                    out.append({'skill': sk, 'type': '资料库调用协议',
                                'evidence': f'{key}:{i} 直连 library 且**无三态判定**'
                                            f'——读失败会被当成"没有数据"（M-R60 违规）'})
    return out


def _sig_eco_row_guard(strict=False):
    """信号源43：台账回写**不许写错行**（S162）。

    事故原貌（2026-10-19 当天）：批量回写云端台账时，我从两个数据源各按**行序**取 id——
    `rows_of()` 的属性视图里 library 排第 5，`query_database_record` 的 id 映射里
    tvbox-interface 排第 1——把 library 的 record_id 配给了 tvbox-interface，
    **两边版本同时被写成 0.6.1**。而 batch_update 对每条都回 `success: true`。

    这是 M-R77「success ≠ 落库」的第三种形态，前两种是
      ① 没写进去（回执成功、字段没变）—— 已由写后回读挡掉；
      ② 写进错字段 —— 由回读该字段挡掉；
      ③ **写进错行** —— 回读 version 确实变了，只是变到了别人身上。
    回读挡不住第③种：它只问「这个值对不对」，不问「这个值落在谁身上」。

    本判据只守"不恒报"这一侧（无事时返回 []）；"能捕获"由 signal-check 的探针
    注入错配映射来证明。两侧都要，缺一即假绿。

    ★ S162 反证实测（拆防线验证，别只验"能通过"）：
      只拆「写入前核对」→ rc 仍是 1，因为**写后回读**也拦住了（目标行版本没变）。
      同时拆掉「核对 + 回读」→ rc=0，`9.9.9` **真被写进了 library**（已当场还原并核实无残留）。
      结论：这两道防线**不是冗余，是互补**——
        · 核对挡的是"将要写错"，在发请求之前，**一个字节都不发出去**；
        · 回读挡的是"已经写错"，事后发现，但**只能发现、不能阻止**（数据已脏）。
      只有核对在，才能避免脏；只有回读在，才能发现核对本身写错了。缺哪一道都漏。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import version_guard as _vg
        i2n, n2i, why = _vg._eco_id_name()
    except Exception as e:
        return [{'skill': 'ecosys-conductor', 'type': '台账回写防错行',
                 'evidence': f'无法载入 version_guard 取 id 映射（{type(e).__name__}）——无法判定'}]
    if why:
        return [{'skill': 'ecosys-conductor', 'type': '台账回写防错行',
                 'evidence': f'{why}——无法判定（勿当"无错行"）'}]
    out = []
    # 正反两张表必须互为逆映射，否则写入定位与核对用的不是同一套事实
    for name, rid in n2i.items():
        back = i2n.get(rid)
        if back != name:
            out.append({'skill': 'ecosys-conductor', 'type': '台账回写防错行',
                        'evidence': f'映射非互逆：{name}→{rid}→{back!r}'
                                    f'——按 id 写会把 {name} 的版本写到 {back} 身上'})
    # 一个 id 被多个技能名指向，意味着有人走了"按位置取 id"的老路
    dup = {}
    for name, rid in n2i.items():
        dup.setdefault(rid, []).append(name)
    for rid, names in dup.items():
        if len(names) > 1:
            out.append({'skill': 'ecosys-conductor', 'type': '台账回写防错行',
                        'evidence': f'record_id {rid} 被 {len(names)} 个技能名共用：'
                                    f'{"、".join(names)}——必然写错行'})
    return out


def _sig_drift_fix_truthful(strict=False):
    """信号源45：`drift-fix` 闭环**不许谎报**（S162）。

    立此源的原因：这个命令本身就是为了"不再靠记忆"而写的，但它自己有两个典型的假绿口子，
    而**这两个口子我在本轮写它的 20 分钟里全都踩了一遍**：

      ① 解析错 → 报"✅ 无需回写"：第一版按 `'漂移' in line` 抓行，抓到的是标题行
         「🔴 发现 1 处漂移：」（冒号右边是空的、没有 SKILL=），于是 need_eco 为空 →
         **明明有漂移，却打出"无需回写"并 rc=0**。
      ② 键名带空格 → 误判"本地矛盾"：分隔符是 ` / `，`dict(p.split('='))` 得到的键
         是 `' CHANGELOG'` 带前导空格，取不到值 → 把正常的云端漂移报成"写错了"。

    病根同一条：**解析别人打印的文本，却不校验解析结果是否为空/合理**。
    空结果被当成"没问题"——这是所有 parse-then-act 工具的通病，
    也正是 `drift-fix` 这类"看输出再动手"的工具最需要被守的地方。

    判定（运行真实命令，不 mock）：
      · 健康态必须 rc=0 且输出「无漂移」
      · 注入「云端滞后」→ 必须真的修好（复检一致），不许只凭回写回执就说成功
      · 注入「本地矛盾」→ 必须 rc=1、且**不动云端**（改了会掩盖真实错误）
    两侧都要：只验"能修"会漏掉"乱修"，只验"不乱修"会漏掉"修不动却报修好了"。
    """
    import subprocess as _sp
    out = []
    here = os.path.dirname(os.path.abspath(__file__))
    me = os.path.abspath(__file__)

    def _run(*a):
        r = _sp.run([sys.executable, me, 'drift-fix', *a], capture_output=True, text=True)
        return r.returncode, (r.stdout or '') + (r.stderr or '')

    def _ver_rows():
        try:
            sys.path.insert(0, here)
            import version_guard as _vg
            eco, ok = _vg._ver_from_eco()
            return eco if ok else None
        except Exception:
            return None

    # ① 健康态：不许恒报
    rc, txt = _run()
    if rc != 0 or '无漂移' not in txt:
        out.append({'skill': 'ecosys-conductor', 'type': '漂移修闭环不谎报',
                    'evidence': f'健康态 rc={rc}、输出未见「无漂移」——'
                                f'闭环工具自身在无事时也报错，会训练人忽略它的输出'})
    # ② 静态：必须存在复检步骤，否则就是"只看回执"
    try:
        src = open(me, encoding='utf-8').read()
        m = re.search(r'def cmd_drift_fix\(rest\):.*?(?=\ndef )', src, re.S)
        if not m:
            out.append({'skill': 'ecosys-conductor', 'type': '漂移修闭环不谎报',
                        'evidence': 'cmd_drift_fix 不存在——判据失去对象'})
        else:
            body = m.group(0)
            if '复检' not in body:
                out.append({'skill': 'ecosys-conductor', 'type': '漂移修闭环不谎报',
                            'evidence': 'cmd_drift_fix 里没有「复检」——'
                                        '只凭回写命令的 rc 就下结论＝M-R77 的假绿'})
            if 'drift_lines' not in body or 'if not drift_lines' not in body:
                out.append({'skill': 'ecosys-conductor', 'type': '漂移修闭环不谎报',
                            'evidence': 'cmd_drift_fix 缺少"解析结果为空即判无法判定"的分支——'
                                        '空解析会被当成"无需回写"（本轮实踩）'})
    except Exception as e:
        out.append({'skill': 'ecosys-conductor', 'type': '漂移修闭环不谎报',
                    'evidence': f'读 conductor.py 失败（{type(e).__name__}）——无法判定'})
    return out


def _sig_eco_cache_stale(strict=False):
    """信号源44：写后回读**不许被缓存骗**（S162）。

    **真事故**（本轮，2026-10-19）：给 probe-hub 升 1.10.0 后跑 `version_guard.py --sync-eco`，
    云端明明写进去了（另开进程读是 1.10.0），命令却报
    「❌ 回读仍为 '1.9.0'（期望 1.10.0）——未落库」，rc=1。

    我先误判成「接口读缓存有几十秒延迟」，加了退避重试（1s/2s/3s），**3 次全读到旧值**；
    随后开新进程测，**0 秒就读到新值**——矛盾，说明根本不是延迟。

    真根因：`core` 有**两层缓存**——
      ① `rows_of_t` 的 db 键            ② `api()` 的 `('api',script,args)` 键
    上一轮（S158）的修法是 `rows_of(DB_ECO, _cache=False)`，它**只跳过 ①**，
    仍然调 `api()`，而 `api` 默认 `_cache=True` → 同进程内第二次读照样命中旧值。
    `core.invalidate` 的注释里明写着「S020 实测复发，同症状第二次」——
    **同一个坑，第三次踩**。

    这比"漏报"更伤：误报会教人「回读报错＝接口坏了」，下次真出事就没人信了。

    判定（两侧都要，缺一即假绿）：
      · 静态侧：`_ver_from_eco` 必须先 `invalidate(DB_ECO)` 再读。
        只写 `_cache=False` ＝ 没修（只堵一层）。
      · 活体侧：同进程内「写→读→再读」三次，值必须一致（不能让 api 层返回陈货）。
        "能捕获"由 signal-check 注入「先删 invalidate 再读」来证明。

    ★ 这条判据只守"不恒报"这一侧（无事时返回 []）。
    """
    out = []
    fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'version_guard.py')
    try:
        src = open(fp, encoding='utf-8').read()
    except Exception as e:
        return [{'skill': 'ecosys-conductor', 'type': '回读防缓存欺骗',
                 'evidence': f'读不到 version_guard.py（{type(e).__name__}）——无法判定'}]
    m = re.search(r'def _ver_from_eco\(\):.*?(?=\ndef )', src, re.S)
    if not m:
        return [{'skill': 'ecosys-conductor', 'type': '回读防缓存欺骗',
                 'evidence': '_ver_from_eco 函数不存在——判据失去对象'}]
    body = m.group(0)
    # ① 必须清两层缓存
    if 'invalidate(' not in body:
        out.append({'skill': 'ecosys-conductor', 'type': '回读防缓存欺骗',
                    'evidence': '_ver_from_eco 未调 invalidate()——core 有 rows/db 与 api 两套缓存，'
                                '只写 _cache=False 只堵一层，同进程回读仍会读到旧值（S162 实测）'})
    # ② 光有 _cache=False 不算修：得能识破「只在 rows 层绕过」这种半修
    if '_cache=False' in body and 'invalidate(' not in body:
        out.append({'skill': 'ecosys-conductor', 'type': '回读防缓存欺骗',
                    'evidence': '半修：只写了 _cache=False 而没 invalidate——'
                                '正是 S158 修完、S162 复发的那个形态'})
    # ③ 活体自证：同进程连读两次必须同值（若 api 层缓存没清，第二次仍是老结果）
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import importlib
        import version_guard as _vg
        importlib.reload(_vg)
        a, ok1 = _vg._ver_from_eco()
        b, ok2 = _vg._ver_from_eco()
        if not (ok1 and ok2):
            out.append({'skill': 'ecosys-conductor', 'type': '回读防缓存欺骗',
                        'evidence': '云端生态库读取失败——无法判定（M-R60，勿当"无缓存问题"）'})
        elif a != b:
            diff = [k for k in a if a.get(k) != b.get(k)]
            out.append({'skill': 'ecosys-conductor', 'type': '回读防缓存欺骗',
                        'evidence': f'同进程连读两次结果不同（{diff[:3]}）——'
                                    f'回读不可信，写后校验等于没做'})
    except Exception as e:
        out.append({'skill': 'ecosys-conductor', 'type': '回读防缓存欺骗',
                    'evidence': f'活体自证抛错（{type(e).__name__}: {e}）——无法判定'})
    return out


def _sig_undef_name(strict=False):
    """信号源41：静态未定义名（S158·技能↔资料库收敛的副产物，但守的是更普遍的一类病）。

    **立此信号源的原因**（S158 实测）：
      本轮把 5 处 `subprocess.run(...library...)` 改走 `core._run_lib` 之后，
      代码**语法完全合法、py_compile 通过**，但其中有 3 处仍在引用改前的旧局部变量
      （`out` / `out_json`），一旦走到失败分支就 `NameError`。
      更糟的是两处都包在 `except Exception:` 里，异常被吞成一句
      "生态门面联动跳过"/"写入失败"，**运行到那一刻才发现，而且表现为别的问题**。

      信号源39 只做 `py_compile`——它守的是"语法坏"，守不住"名字坏"。
      语法检查对未定义名天生是哑的（Python 是动态语言）。

    **另一类附带收获**：conductor.py 自己有一处 `time.strftime` 而全文件从未
    `import time`，同样长期抛 NameError 并被 `except: pass` 吞掉——
    "规则面增长趋势"这条度量一直是空的，**没有任何检测器会报**。

    判定：用 pyflakes 静态分析每个技能 scripts/*.py，取 `undefined name` 条目。
      · `strict=True`：只报 `undefined name`（健康态必为空 → 可进假阳性基线）。
      · 默认：同上，外加 `may be undefined, or defined from star imports` 归为提示
        （星号导入无法静态判定，**不拦**，避免对 watchdog 这类写法误报）。

    三态：pyflakes 不可用 → **报"无法判定"而不是返回空**。这是 M-R60 的直接应用：
      检测器自己跑不起来时返回 [] 就等于"一切正常"，比不装它还危险。
    """
    import shutil as _sh
    import subprocess as _sp
    root = pathlib.Path(SKILL_ROOT)
    if not root.is_dir():
        return [{'skill': '(全局)', 'type': '静态未定义名',
                 'evidence': f'技能根目录缺失：{root}——无法判定'}]
    # pyflakes 可用性：优先 python -m，其次 PATH 里的 pyflakes
    _cmd = None
    try:
        _p = _sp.run([sys.executable, '-m', 'pyflakes', '--version'],
                     capture_output=True, text=True, timeout=20)
        if _p.returncode == 0 or 'pyflakes' in ((_p.stdout or '') + (_p.stderr or '')).lower():
            _cmd = [sys.executable, '-m', 'pyflakes']
    except Exception:
        _cmd = None
    if _cmd is None and _sh.which('pyflakes'):
        _cmd = [_sh.which('pyflakes')]
    if _cmd is None:
        return [{'skill': '(全局)', 'type': '静态未定义名',
                 'evidence': 'pyflakes 不可用——**无法判定**，不代表无缺陷'
                             '（装法：pip install pyflakes）'}]

    # 收集待扫文件（含有效根可能嵌套一层子目录的情况，与信号源39 同口径）
    targets = []
    for name in sorted(os.listdir(root)):
        d = root / name
        if not d.is_dir() or name.startswith('.') or name == '__pycache__':
            continue
        er = d
        if not (d / 'SKILL.md').exists():
            er = None
            for sub in sorted(d.iterdir()) if d.is_dir() else []:
                if sub.is_dir() and (sub / 'SKILL.md').exists():
                    er = sub
                    break
            if er is None:
                continue
        sd = er / 'scripts'
        if not sd.is_dir():
            continue
        for f in sorted(sd.glob('*.py')):
            if f.name.startswith('.'):
                continue
            targets.append((name, f))
    if not targets:
        return []

    out = []
    try:
        r = _sp.run([*_cmd, *[str(f) for _, f in targets]],
                    capture_output=True, text=True, timeout=180)
        raw = (r.stdout or '') + (r.stderr or '')
    except Exception as e:
        return [{'skill': '(全局)', 'type': '静态未定义名',
                 'evidence': f'扫描执行失败（{type(e).__name__}: {str(e)[:60]}）——**无法判定**'}]

    # 退出码 0/1 是正常（1=有发现）；>1 通常是自身错误，须报无法判定
    if r.returncode not in (0, 1):
        return [{'skill': '(全局)', 'type': '静态未定义名',
                 'evidence': f'pyflakes 异常退出 {r.returncode}：{raw[:150]}——**无法判定**'}]

    for ln in raw.splitlines():
        if 'undefined name' not in ln:
            continue
        # 星号导入的"无法判定"提示：`from x import *` used; **unable to detect undefined names**
        #   —— 这不是缺陷，是 pyflakes 明确告诉你"这文件我看不清"。
        #   它的措辞是 "unable to detect undefined names"，与 "may be undefined" 不同，
        #   两个都要滤掉（S158 实测：只滤后者会漏，导致健康态基线恒报一条）。
        _ln_low = ln.lower()
        if 'unable to detect undefined names' in _ln_low:
            continue
        if 'may be undefined' in _ln_low:
            if strict:
                continue          # 星号导入的提示，健康态不该算缺陷
        # 形如 /path/scripts/x.py:12:3: undefined name 'foo'
        try:
            _fp, _rest = ln.split(':', 1)
        except ValueError:
            continue
        _rel = _fp.replace(str(root) + os.sep, '').replace(str(root) + '/', '')
        _sk = _rel.split(os.sep)[0].split('/')[0]
        out.append({'skill': _sk, 'type': '静态未定义名',
                    'evidence': f'{_rel}:{_rest.strip()[:110]}'
                                f'——代码能编译但名字不存在，运行到该分支必崩'
                                f'（若被 except 包住则表现为**别的**问题，更难查）'})
    return out


def _sig_skill_coverage(s=None, strict=False):

    """信号源39：全技能监护覆盖（用户点名"所有技能都要有人监察"，S155 范围扩张）。
    动态遍历 SKILL_ROOT 下**每一个**技能目录（含未来新装的市场技能），逐项目检并产健康档：
      ① 结构完整：含 SKILL.md（无则"裸技能"，不可用）
      ② 可加载：scripts/*.py 全部 py_compile 通过（专治"经常坏/没人发现"的语法崩）
      ③ 自监察声明：非市场技能须 flow.json 的 self_check 非空（自有自检）；
         市场技能(skill_*)由本监护器统一覆盖，不强制自登记。
    strict=True 时只检 ①/②（用于 signal-check 假阳性基线，健康态必返回空）；
    默认 strict=False 含 ③（用于 verify ⑫ 上报覆盖缺口）。
    返回 [] = 健康；否则每项一个证据（含 skill 名）。三态：枚举自身崩→显式告警。
    关键性质：按路径枚举 ⇒ 任何技能（含新装）都无法"裸奔"，天然满足"所有技能有人监察"。"""
    import py_compile
    out = []
    root = SKILL_ROOT
    if not os.path.isdir(root):
        return [{'skill': '(全局)', 'type': '全技能监护', 'evidence': f'技能根目录缺失: {root}'}]

    def _effective_root(d):
        """技能正本可能解压在子目录（市场技能包常见一层嵌套，如 skill_xxx/de-ai-*/SKILL.md）。
        有效根 = 顶层含 SKILL.md，否则一层子目录含 SKILL.md；都没有才判缺。"""
        if os.path.exists(os.path.join(d, 'SKILL.md')):
            return d
        for sub in sorted(os.listdir(d)):
            sd = os.path.join(d, sub)
            if os.path.isdir(sd) and os.path.exists(os.path.join(sd, 'SKILL.md')):
                return sd
        return None

    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d) or name.startswith('.') or name == '__pycache__':
            continue
        er = _effective_root(d)
        # ① 结构完整
        if er is None:
            out.append({'skill': name, 'type': '全技能监护', 'evidence': '缺 SKILL.md（裸技能/不可用）'})
            continue
        # ② 可加载（py_compile 仅校验语法，不受运行环境依赖影响）
        sd = os.path.join(er, 'scripts')
        if os.path.isdir(sd):
            for f in sorted(os.listdir(sd)):
                if not f.endswith('.py') or f.startswith('.'):
                    continue
                fp = os.path.join(sd, f)
                try:
                    py_compile.compile(fp, doraise=True)
                except py_compile.PyCompileError as e:
                    _last = str(e).splitlines()[-1][:90]
                    out.append({'skill': name, 'type': '全技能监护',
                                'evidence': f'scripts/{f} 编译失败("经常坏"已发生): {_last}'})
                except Exception as e:
                    out.append({'skill': name, 'type': '全技能监护',
                                'evidence': f'scripts/{f} 无法编译({type(e).__name__}: {str(e)[:60]})'})
        if strict:
            continue  # 严格模式只查结构，不查 ③ 自监察声明
        # ③ 自监察声明（市场技能由平台监护覆盖）
        is_market = name.startswith('skill_')
        if not is_market:
            ffp = os.path.join(er, 'scripts', 'flow.json')
            if not os.path.exists(ffp):
                out.append({'skill': name, 'type': '全技能监护',
                            'evidence': '无 scripts/flow.json(治理技能未登记自检项，"无人监察")'})
            else:
                try:
                    sc = json.load(open(ffp, encoding='utf-8')).get('self_check')
                    if not isinstance(sc, list) or len(sc) == 0:
                        out.append({'skill': name, 'type': '全技能监护',
                                    'evidence': 'flow.json 的 self_check 为空/缺失(治理技能须声明自检)'})
                except Exception as e:
                    out.append({'skill': name, 'type': '全技能监护',
                                'evidence': f'flow.json 损坏({type(e).__name__}: {e})'})
    return out


def cmd_signal_check(args):
    """信号源活体探针（S057）：对每个检测器做注入-捕获-清理，证明逻辑有效。
    与 _sig_health（依赖可达）互补：依赖在 ≠ 逻辑对。逻辑坏掉的检测器永远返回空，
    依赖自检看不出——必须**注入已知异常，看它能不能抓到**。等价于 watchdog 的 proven，
    但可复跑（不是一次性手工登记）。用法: tvbox conductor signal-check [--source N]"""
    only = None
    for i, a in enumerate(args):
        if a == '--source' and i + 1 < len(args):
            only = args[i + 1]
    probes = []   # (信号源, 注入函数→捕获判定, 清理函数)
    print('🔬 信号源活体探针（注入已知异常 → 验证能否捕获 → 清理）')
    print()
    results = []

    # --- 假阳性基线（S075）：注入前先验「无事时检测器不误报」---
    # 探针的 hit/clean 只证明「能捕获真异常 + 清理后清零」，**不证明**正常情况下不误报；
    # 检测器若恒报（如 S069 的假阳性回归），hit 会「假通过」。此处补反向对照（M-R47③ 双侧验证）。
    # S078：基线须覆盖**全部**可静态判定的检测器——S075 只补了 1/2/6/7/9/10（凭记忆挑的），
    # 漏了 3/4/5/8；漏掉者若恒报，其 hit 仍会假通过（M-R53 的自身盲区：基线清单不完整）。
    fp_baseline = []
    _baseline_ids = set()
    if only is None:
        _base_checks = [
            ('1', lambda: _sig_backlog()), ('2', lambda: _sig_eco_blank()),
            ('3', lambda: [s for s in ECO_SKILLS if _sig_stale_cl(s)]),   # 任一技能契约漂移
            ('4', lambda: _sig_stale_snap(SNAPSHOT_OWNER)),
            ('5', lambda: [s for s in ECO_SKILLS if _sig_route_blind(s)]),  # 任一技能路由盲区
            ('6', lambda: _sig_doc_gap()), ('7', lambda: _sig_health()),
            ('8', lambda: _probe_parsers_broken()),   # 解析器对正常样例应返回正确值
            ('9', lambda: _sig_stagnant()), ('10', lambda: _sig_inline_cmd()),
            ('11', lambda: _sig_scorer()), ('12', lambda: _sig_ledger()),
            ('13', lambda: _sig_exec()),
            ('14', lambda: _sig_snapshot_base()),
            ('15', lambda: _sig_rows_base()),
            ('16', lambda: _sig_fold_scan()),
            ('17', lambda: _sig_cloud_nodes_base()),
            ('18', lambda: _sig_upstream_probe()),
            ('19', lambda: _sig_upstream_gone()),
            ('20', lambda: _sig_eco_ledger()),
            ('21', lambda: _sig_lend_gap()),
            ('22', lambda: _sig_pool_gap()),
            ('42', lambda: _sig_session_distill()),
            ('23', lambda: _sig_core_db_rows()),
            ('24', lambda: _sig_backup_stale()),
            ('25', lambda: _sig_issue_stale()),
            ('26', lambda: _sig_registry_fork()),
            ('27', lambda: _sig_status_terminal()),
            ('28', lambda: _sig_skill_registry_gap()),
            ('29', lambda: _sig_entry_verdict()),
            ('30', lambda: _sig_score_states()),
            ('31', lambda: _sig_verdict_pure()),
            # 32：无事时不得报孤儿词（当前双向对账已清零，故应为空）
            ('32', lambda: _intents_orphan_audit()),
            # 33：无事时不得报 ask 缺口（S148 已补 108/108，故应为空）
            ('33', lambda: _sig_ask_hittable()),
            # 34：无事时不得报幽灵命令（S148 已补 inspect/janitor，故应为空）
            ('34', lambda: _sig_cmd_executable()),
            # ★ S155 元监察：四类守门人纳入假阳性基线（无注入须返回空，否则恒报）
            ('35', lambda: _sig_janitor()),
            ('36', lambda: _sig_probe_alive()),
            ('37', lambda: _sig_inspect()),
            ('38', lambda: _sig_eco_link()),
            ('39', lambda: _sig_skill_coverage(strict=True)),
            # 40：无事时不得报"绕过资料库协议且无三态"（S158 已收敛硬违规至 0）
            ('40', lambda: _sig_lib_protocol(strict=True)),
            ('41', lambda: _sig_undef_name(strict=True)),
            # 43：无事时不得报"错行"——正常映射下回写应与本地一致，故返回空。
            #     本项守的判据（拒写 + 指出错行 + 一字节不发）在探针里注入，基线只守"不恒报"。
            ('43', lambda: _sig_eco_row_guard()),
            # 44：无事时不得报"回读被缓存骗"——invalidate 在位、两次读一致，故返回空。
            #     本项守的判据（能识破半修）在探针里注入，基线只守"不恒报"。
            ('44', lambda: _sig_eco_cache_stale()),
            # 45：无事时不得报"闭环谎报"——健康态 rc=0 且复检步骤在位，故返回空。
            ('45', lambda: _sig_drift_fix_truthful()),
        ]
        _baseline_ids = {s for s, _ in _base_checks}
        _silent_exc = []
        for _sid, _fn in _base_checks:
            try:
                if _fn():
                    fp_baseline.append(_sid)
            except Exception as _e:
                # ★ S155：探针崩了不得静默吞——"探针没人监察"根因之一。原 `except: pass`
                #   让某探针因措辞漂移/API 变更抛异常时 signal-check 静默假通过。改为显式记录。
                _silent_exc.append((_sid, type(_e).__name__, str(_e)[:80]))
        # 基线覆盖必须 == 注册表条目数（防漏加基线者 hit 假通过）——与探针覆盖审计同构
        if len(_baseline_ids) != len(SIGNAL_SOURCES):
            print(f'  ⚠️ 假阳性基线覆盖缺口：{len(_baseline_ids)} 项 ≠ 注册表 {len(SIGNAL_SOURCES)} 源'
                  f'（缺失 {",".join(sorted(set(SIGNAL_SOURCES) - _baseline_ids, key=int))}）')
        if fp_baseline:
            # ★ S156 修：原措辞「检测器可能恒报」把**真阳性**读成了检测器嫌疑。
            #   实证：信号源5「路由盲区」在生态健康时返回空（不是恒报），它报出的是
            #   **eco-ledger 从未登记路由表**这一真实缺陷——但基线先一步把它标成
            #   "假阳性基线告警"，于是 verify 判据① 显示 `39/39 + 【假阳性告警】`：
            #   数字满分、结论判红，读者无从判断到底该信哪个。
            #   三态口径（M-R60）：基线只证明"本次没注入扰动"，**不能**证明检测器说谎。
            #   故措辞改为「检测器在无注入时即报」，并明确"须先复核实为真还是误报"。
            print(f'  ⚠️ 假阳性基线：信号源 {",".join(fp_baseline)} 在无注入时即报'
                  f'（可能恒报，也可能是**真实缺陷**——须逐条复核，勿直接当作检测器有罪）')
            print()
        if _silent_exc:
            # ★ S155：探针静默异常显式报警——"监测者自己"此前无人盯的缺口
            print(f'  ❌ 探针静默异常：{len(_silent_exc)} 个检测器抛未捕获异常（原被 except 吞=静默失效）')
            for _sid, _t, _msg in _silent_exc:
                print(f'     信号源{_sid} {SIGNAL_SOURCES.get(_sid, "?")}：{_t}: {_msg}')
            print()
    
    # --- 信号源1 backlog 兑现 ---
    if only in (None, '1'):
        fp = os.path.join(SKILL_ROOT, 'ecosys-conductor', 'scripts', 'flow.json')
        bak = fp + '.probe'; shutil.copy(fp, bak)
        try:
            d = json.load(open(fp, encoding='utf-8'))
            d.setdefault('backlog', []).append('zzprobeunique 扩展点未兑现')
            json.dump(d, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
            hit = any('zzprobeunique' in x['evidence'] for x in _sig_backlog())
        finally:
            shutil.move(bak, fp)
        clean = not any('zzprobeunique' in x['evidence'] for x in _sig_backlog())
        results.append(('信号源1 backlog兑现', hit, clean))

    # --- 信号源2 生态空白 ---
    if only in (None, '2'):
        # 上游 watchdog expand 输出不易注入；改验证"解析器逻辑"：用已知样例喂解析
        sample = '🆕 新实体（地图外）\n  - zzprobe_entity: 探针样例\n  - 另一条\n'
        m = re.search(r'🆕 新实体[^\n]*\n((?:\s+- .+\n?)+)', sample)
        hit = bool(m and 'zzprobe_entity' in m.group(1))
        # 清零：空输出应解析为空（不假阳性）
        clean = not re.search(r'🆕 新实体[^\n]*\n((?:\s+- .+\n?)+)', '无新实体输出')
        results.append(('信号源2 生态空白', hit, clean))

    # --- 信号源6 文档-实现漂移 ---
    if only in (None, '6'):
        doc = '/workspace/生态1016_生态指挥家技能设计.md'; bak = doc + '.probe'
        shutil.copy(doc, bak)
        try:
            open(doc, 'a', encoding='utf-8').write('\n| `zzprobeghost` | 探针幽灵命令 |\n')
            gaps = _sig_doc_gap()
            hit = any('zzprobeghost' in d for _, d in gaps)
        finally:
            shutil.move(bak, doc)
        # 清零判定：注入项消失即可（不因无关的真实漂移而误判探针失败）
        clean = not any('zzprobeghost' in d for _, d in _sig_doc_gap())
        results.append(('信号源6 文档-实现漂移', hit, clean))

    # --- 信号源5 路由盲区 ---
    if only in (None, '5'):
        bak = MANIFEST + '.probe'
        shutil.copy(MANIFEST, bak)
        try:
            raw = open(MANIFEST, encoding='utf-8').read()
            open(MANIFEST, 'w', encoding='utf-8').write(raw.replace('watchdog', '__wdX__').replace('blacklist', '__blX__'))
            hit = bool(_sig_route_blind('legacy-watchdog'))
        finally:
            shutil.move(bak, MANIFEST)
        clean = not _sig_route_blind('legacy-watchdog')   # 只判定被注入的目标
        results.append(('信号源5 路由盲区', hit, clean))

    # --- 信号源4 快照时效 ---
    if only in (None, '4'):
        fp = SNAPSHOT_FILE
        if os.path.exists(fp):
            bak = fp + '.probe'; shutil.copy(fp, bak)
            # ★ S177 步3 实测修：本探针原来写 `clean = not _sig_stale_snap(...)`，
            #   即「清理后必须**完全不报警**」。这个判据默认「基线数据是新鲜的」，
            #   而实测基线 `history[-1].ts = 2026-10-18`、今天是 2026-10-20，
            #   按 `age > 1` 本来就该报「2 天未刷新」——于是 clean 永远为 False。
            #   **这不是状态残留，是判据把「有真信号」误判成「探针没清干净」。**
            #   （对照：源5/源6 都只判定**被注入的那个目标**，不因无关真实漂移误判。）
            #   修法：clean 只问「注入的那条 3 天前假数据还在不在」，不问整体是否报警。
            _orig_ts = None
            try:
                d = json.load(open(fp, encoding='utf-8'))
                if d.get('history'):
                    _orig_ts = d['history'][-1].get('ts')
                    d['history'][-1]['ts'] = str(datetime.date.today() - datetime.timedelta(days=3)) + ' 00:00'
                    json.dump(d, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
                    # 层级一致：只改「最后一条」的 ts，判据读的也是最后一条。
                hit = bool(_sig_stale_snap(SNAPSHOT_OWNER))
            finally:
                shutil.move(bak, fp)
            # clean：还原后末条 ts 应回到原值（证明清理真的写回去了），
            #        且此时若仍报陈旧，那是因为基线本来就旧，与探针无关 → 不算探针失败。
            try:
                _after = json.load(open(fp, encoding='utf-8'))
                _after_ts = (_after.get('history') or [{}])[-1].get('ts') if _after.get('history') else None
            except Exception:
                _after_ts = '<<读不回来>>'
            clean = (_after_ts == _orig_ts)
            if not clean:
                print(f'  ⚠️ 信号源4 清理未还原：期望 ts={_orig_ts!r}，实际={_after_ts!r}'
                      f'（这是真·清理缺陷，与基线新旧无关）')
            results.append(('信号源4 快照时效', hit, clean))

    # --- 信号源3 契约漂移 ---
    if only in (None, '3'):
        cl = os.path.join(SKILL_ROOT, 'ecosys-conductor', 'CHANGELOG.md')
        bak = cl + '.probe'; shutil.copy(cl, bak)
        try:
            open(cl, 'a', encoding='utf-8').write('\n## 2099-01-01 v9.9.9 探针脏数据\n')
            hit = bool(_sig_stale_cl('ecosys-conductor'))
        finally:
            shutil.move(bak, cl)
        results.append(('信号源3 契约漂移', hit, not _sig_stale_cl('ecosys-conductor')))

    # --- 信号源7 信号源自检 ---
    if only in (None, '7'):
        doc = '/workspace/生态1016_生态指挥家技能设计.md'; bak = doc + '.probe'
        shutil.move(doc, bak)
        try:
            hit = any('信号源6' in d for _, d in _sig_health())
        finally:
            shutil.move(bak, doc)
        # ★ S162 修：这里的 clean 判据原写作 `not _sig_health()`——但它**误读了自己**。
        #   `_sig_health()` 的清单一致性审计会遍历**整个注册表**（含 43），任何一条
        #   自检项缺失都会让它返回非空。于是「探针7 清理后清零」变成一条**随生态状态漂移**的
        #   判据：只要注册表比 `checks` 清单多一个源，它就永远为「否」——与探针7 本身
        #   （注入文档缺失、看能否报出信号源6）毫无关系。
        #   这正是本生态反复栽过的那个坑：**清零判据不许拿生态状态冒充检测器逻辑**。
        #   改成只看「我注入的那处脏有没有被清掉」：文档回来了、且不再报信号源6 缺失。
        _h7 = _sig_health()
        clean = ((not any('信号源6' in d for _, d in _h7)) if _h7 is not None
                 else False)
        results.append(('信号源7 信号源自检', hit, clean))

    # --- 信号源8 verify 判据解析器（S061）---
    if only in (None, '8'):
        # S078：逻辑抽为 _probe_parsers_broken()，探针与假阳性基线共用（单一事实源 M-R38）。
        # S079：_probe_parsers_broken 已含 4 个解析器（probe/assess/evolve/watchdog 三态）的
        #       正常样例取值 + 垃圾输入 None + watchdog 第三态校验——探针直接复用即可。
        hit = (_probe_parsers_broken() is None)   # 全部解析器对正常样例取值正确、对垃圾返回 None
        clean = (_parse_probe_ratio('乱码无结构') is None and
                 _parse_assess_score('乱码') is None and
                 _parse_evolve_ready('乱码') is None and
                 _parse_watchdog_status('') is None)
        results.append(('信号源8 verify判据', hit, clean))
    # --- 信号源9 技能停滞（S069·M-R45）---
    if only in (None, '9'):
        # 注入 evolve scan 的「无机器可执行层」汇总行 → 验证解析器能抽出技能名
        inj = '⚠️ 契约齐备但**无机器可执行层**（S069·M-R45）：zzprobeghost, tvbox-interface'
        _orig_sh = globals().get('_sh')
        try:
            # S071 修复：patch 的 lambda 必须透传 **kw——实现侧 _sh 已加 cache= 参数，
            # 旧签名 (cmd, timeout=60) 会 TypeError 被 except 吞成空→探针假阴性（M-R48④）
            globals()['_sh'] = lambda cmd, timeout=60, **kw: inj if 'evolve scan' in cmd else _orig_sh(cmd, timeout, **kw)
            got = _sig_stagnant()
            hit = any(x['skill'] == 'zzprobeghost' for x in got)
        finally:
            globals()['_sh'] = _orig_sh
        clean = not any('zzprobeghost' in x['skill'] for x in _sig_stagnant())
        results.append(('信号源9 技能停滞', hit, clean))
    # --- 信号源10 入口内联命令（S074）---
    if only in (None, '10'):
        # 注入：临时从 manifest 移除 contracts 条目（入口内联命令之一）→ 应报「未登记」
        mfp = os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'manifest.json')
        bak = mfp + '.probe'; shutil.copy(mfp, bak)
        try:
            d = json.load(open(mfp, encoding='utf-8'))
            d['commands'] = [c for c in d.get('commands', [])
                             if not (isinstance(c, dict) and c.get('cmd') == 'tvbox contracts')]
            json.dump(d, open(mfp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
            hit = any('contracts' in x['evidence'] for x in _sig_inline_cmd())
        finally:
            shutil.move(bak, mfp)
        clean = not any('contracts' in x['evidence'] for x in _sig_inline_cmd())
        results.append(('信号源10 入口内联命令', hit, clean))
    # --- 信号源11 评分器自检（S077·M-R53/M-R54）---
    if only in (None, '11'):
        # 注入：把评分器换成「三维退化版」（模拟 S076 前的回归）→ _sig_scorer 应报「缺维+假红」
        _orig_radar = globals().get('_radar_scores')
        def _broken_radar(ready, total, watchdog_ok, adopted, denom, pending, **kw):
            return {'cat_contract': round(100 * ready / total) if total else 0,
                    'cat_biz': 100, 'cat_radar': 100}   # 只有三维（缺健康/增益/噪声）
        try:
            globals()['_radar_scores'] = _broken_radar
            hit = bool(_sig_scorer())
        finally:
            globals()['_radar_scores'] = _orig_radar
        clean = not _sig_scorer()   # 还原后应无告警（评分器完好）
        results.append(('信号源11 评分器自检', hit, clean))
    # --- 信号源12 留痕完整性（S080·M-R32 变体）---
    if only in (None, '12'):
        # 注入：把 evolution_plan.json 换成损坏内容（非法 JSON）→ _sig_ledger 应报「损坏」
        lfp = os.path.join(PLANS_DIR, 'evolution_plan.json')
        bak = lfp + '.probe'; shutil.copy(lfp, bak)
        try:
            open(lfp, 'w', encoding='utf-8').write('{ 损坏的非法JSON [')
            hit = any('损坏' in x['evidence'] or '非 list' in x['evidence'] or '缺失' in x['evidence']
                      for x in _sig_ledger())
        finally:
            shutil.move(bak, lfp)
        clean = not _sig_ledger()   # 还原后三留痕应全健在
        results.append(('信号源12 留痕完整性', hit, clean))
    # --- 信号源13 执行层自检（S081·M-R59）---
    if only in (None, '13'):
        # 注入：把 _sh 换成「旧退化版」（非0退出返回空串，与"成功空输出"混淆）→ _sig_exec 应报
        _orig_sh = globals().get('_sh')
        def _degraded_sh(cmd, timeout=60, cache=False):
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
                return (r.stdout + r.stderr).strip()   # 旧版：失败也返回空串，无哨兵
            except Exception as e:
                return f'（执行失败：{e}）'               # 旧版：超时给格式不一的中文串
        try:
            globals()['_sh'] = _degraded_sh
            globals()['_sh_ok'] = lambda out: not str(out).startswith(SH_FAIL_PREFIX)  # 保持判定不变
            hit = bool(_sig_exec())
        finally:
            globals()['_sh'] = _orig_sh
        clean = not _sig_exec()   # 还原后执行层应健在
        results.append(('信号源13 执行层自检', hit, clean))
    # --- 信号源14 快照基座自检（S082·M-R60）---
    if only in (None, '14'):
        # 注入：把 watchdog 的「三态版」换成「折叠版」（损坏也当首期 + 照旧覆盖写），
        # 驱动 _sig_snapshot_base → 应报「损坏被折叠成首期 / 现场被覆盖」。
        import importlib.util as _ilu
        import types as _types
        hit = False
        _wdpath = INSPECT_ENGINE
        if os.path.exists(_wdpath):
            _spec = _ilu.spec_from_file_location('_wd', _wdpath)
            _m = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_m)
            # 退化版：load 永远返回 'none'（折叠损坏态）；save 永远返回 True（不拦覆盖）
            _m.load_prev_snapshot = lambda: ('none', {})
            _m.save_snapshot = lambda findings: (True, [])
            hit = bool(_sig_snapshot_base(_m))
        clean = not _sig_snapshot_base()   # 还原（真模块）后快照基座应健在
        results.append(('信号源14 快照基座自检', hit, clean))
    # --- 信号源15 读表基座自检（S083·M-R60）---
    if only in (None, '15'):
        # 注入：把 hub core 的 rows_of_t 换成「折叠版」（读失败也返 ok=True + 写缓存），
        # 驱动 _sig_rows_base → 应报「读失败与空库不可区分 / 失败却写缓存」。
        import importlib.util as _ilu
        hit = False
        _cpath = os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'scripts', 'core.py')
        if os.path.exists(_cpath):
            _spec = _ilu.spec_from_file_location('_hub_core_inj', _cpath)
            _m = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_m)
            # 退化版：任何输入都返 (True, [])（读失败折叠成空库），且写入 _MEM（毒化缓存）
            def _degraded_rows_t(db, _cache=True, _self=_m):
                if isinstance(getattr(_self, '_MEM', None), dict):
                    _self._MEM[db] = []
                return True, []
            _m.rows_of_t = _degraded_rows_t
            hit = bool(_sig_rows_base(_m))
        clean = not _sig_rows_base()   # 还原（真模块）后读表基座应健在
        results.append(('信号源15 读表基座自检', hit, clean))
    # --- 信号源16 基座折叠扫描（S084·M-R60⑨）---
    if only in (None, '16'):
        # 注入：在某技能 scripts 下临时放一个含「except→return []」的新文件，
        # 驱动 _sig_fold_scan → 应扫出该未登记候选；删除后应清零。
        #
        # ★ S177 步3→步7 实测修（T2，与源4 同型）：
        #   原判据 `clean = not _sig_fold_scan()` 要求「**整个生态**没有未登记折叠候选」。
        #   但 `_sig_fold_scan` 扫的是**全生态真实代码**，生态里只要有一处真实折叠
        #   （历史遗留、新写的代码），clean 就恒为 False——
        #   于是信号源16 长期红着，而它的红**既不代表探针坏了，也不代表新缺陷**，
        #   只代表"生态里还有该审计的点"。**长期红 = 长期被忽略**（M-R63 的另一面）。
        #
        #   正确语义：本探针要证明的是「**扫描器能扫出我注入的东西**」，
        #   不是「生态此刻是干净的」。所以 clean 应判：
        #     ① 注入期能扫出注入文件（hit）
        #     ② 删除后**同一个注入文件**不再被扫出（clean）
        #   生态自身的真实候选，由 `_sig_fold_scan` 的**输出**（信号源16 的报警）负责，
        #   不借探针通道冒充。
        import tempfile as _tf
        _probe_dir = os.path.join(SKILL_ROOT, 'tvbox-source-blacklist', 'scripts')
        hit = False
        _fname = '_fold_probe_tmp.py'
        _fpath = os.path.join(_probe_dir, _fname)
        _created = False
        try:
            if os.path.isdir(_probe_dir):
                open(_fpath, 'w', encoding='utf-8').write(
                    'def f(p):\n'
                    '    import json\n'
                    '    try:\n'
                    '        return json.load(open(p))\n'
                    '    except Exception:\n'
                    '        return []\n')
                _created = True
                hit = any(_fname in x.get('evidence', '') for x in _sig_fold_scan())
        finally:
            if _created and os.path.exists(_fpath):
                os.remove(_fpath)
        # clean：**只对注入文件判定**（与源5/源6 同一原则）
        clean = not any(_fname in x.get('evidence', '') for x in _sig_fold_scan())
        results.append(('信号源16 基座折叠扫描', hit, clean))
    # --- 信号源17 云端读取语义自检（S086·M-R60）---
    if only in (None, '17'):
        # 注入：把 hub link 的 _root_nodes 换成「折叠版」（结构失配也返 ('ok', [])），
        # 驱动 _sig_cloud_nodes_base → 应报「结构失配与真·空库不可区分」。
        import importlib.util as _ilu2
        hit = False
        _lpath = os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'scripts', 'link.py')
        if os.path.exists(_lpath):
            try:
                _spec2 = _ilu2.spec_from_file_location('_hub_link_inj', _lpath)
                _m2 = _ilu2.module_from_spec(_spec2)
                _spec2.loader.exec_module(_m2)
                # 退化版：结构失配（data 无 nodes）被折叠成 ('ok', [])
                def _degraded_root_nodes(_self=_m2):
                    try:
                        import json as _j
                        out = _self.api('space_api.py', 'space.workspace.list-nodes')
                        nodes = _j.loads(out)['data'].get('nodes')
                        return ('ok', nodes or [])   # ← 折叠：结构失配→('ok', [])
                    except Exception:
                        return ('fail', None)
                _m2._root_nodes = _degraded_root_nodes
                hit = bool(_sig_cloud_nodes_base(_m2))
            except Exception as _e:
                # M-R60⑫：注入失败=检测器自身失效，**不得静默折叠成 hit=False**，
                # 否则探针会显示"捕获=否"却不知原因（假绿/假红难辨）。显式告警。
                print(f'  ⚠️ 信号源17 探针注入失败（{type(_e).__name__}: {_e}）——'
                      f'本次捕获判定不可信，请检查 link.py 是否可加载')
        clean = not _sig_cloud_nodes_base()   # 还原（真模块）后语义三态应健在
        results.append(('信号源17 云端读取语义自检', hit, clean))
    # --- 信号源18 上游侦察自检（S087·探源司建档）---
    if only in (None, '18'):
        # 注入：把探源司的 _alive_verdict 换成「退化版」（000 也判'已封禁/删除'——
        # 把网络不可达误判为死），驱动 _sig_upstream_probe → 应报「无法判定失守」。
        import importlib.util as _ilu3
        hit = False
        _ppath = os.path.join(SKILL_ROOT, 'probe-hub', 'scripts', 'probe.py')
        if os.path.exists(_ppath):
            try:
                _pdir = os.path.dirname(_ppath)
                if _pdir not in sys.path:
                    sys.path.insert(0, _pdir)
                _spec3 = _ilu3.spec_from_file_location('_probe_hub_inj', _ppath)
                _m3 = _ilu3.module_from_spec(_spec3)
                _spec3.loader.exec_module(_m3)
                # 退化版：把「无法判定」折叠成「已封禁/删除」（假阳性基线失守）
                def _degraded_alive(_code, _self=_m3):
                    if _code in _self.ALIVE_CODES:
                        return '存活'
                    return '已封禁/删除'   # ← 折叠：000/超时也判"死"
                _m3._alive_verdict = _degraded_alive
                hit = bool(_sig_upstream_probe(_m3))
            except Exception as _e:
                # M-R60⑫：注入失败=检测器自身失效，**不得静默折叠成 hit=False**
                print(f'  ⚠️ 信号源18 探针注入失败（{type(_e).__name__}: {_e}）——'
                      f'本次捕获判定不可信，请检查 probe.py 是否可加载')
        clean = not _sig_upstream_probe()   # 还原（真模块）后监测器语义应健在
        results.append(('信号源18 上游侦察自检', hit, clean))
    # --- 信号源19 上游跑路检查器自检（S090·M18 守护）---
    if only in (None, '19'):
        # 注入：把 M18 的判定换成「退化版」——**把"无法判定"折叠成"已封禁/删除"**
        # （正是 S088 `dirs=[]` 谎报"移除×290" 的同型缺陷，M-R60），
        # 驱动 _sig_upstream_gone → 应报「无法判定不产遗留」失守。
        import importlib.util as _ilu4
        hit = False
        _wpath = INSPECT_ENGINE
        if os.path.exists(_wpath):
            try:
                _wdir = os.path.dirname(_wpath)
                if _wdir not in sys.path:
                    sys.path.insert(0, _wdir)
                _spec4 = _ilu4.spec_from_file_location('_wd_m18_inj', _wpath)
                _m4 = _ilu4.module_from_spec(_spec4)
                _spec4.loader.exec_module(_m4)
                _m18_inj = next(x for x in _m4.load_monitors()['monitors'] if x['id'] == 'M18')

                def _degraded_gone(m, _mod=_m4, _mm=_m18_inj):
                    """退化版：把「无法判定」也当「跑路」——M-R60 折叠。"""
                    out = []
                    for t in [{'仓库名': 'UNK', '存活状态': '无法判定',
                               '活跃度': '无法判定', '关联底包': ''}]:
                        out.append({'monitor': m['id'], 'name': m['name'], 'sev': '阻断',
                                    'item': f"{t['仓库名']}（上游无法判定）",
                                    'detail': 'x', 'action': ''})
                    return out
                _m4.check_upstream_gone = _degraded_gone
                hit = bool(_sig_upstream_gone(_m4))
            except Exception as _e:
                # M-R60⑫：注入失败=检测器自身失效，**不得静默折叠成 hit=False**
                print(f'  ⚠️ 信号源19 探针注入失败（{type(_e).__name__}: {_e}）——'
                      f'本次捕获判定不可信，请检查 watchdog.py 是否可加载')
        clean = not _sig_upstream_gone()    # 还原（真模块）后 M18 三态语义应健在
        results.append(('信号源19 上游跑路检查器自检', hit, clean))
    # --- 信号源20 生态台账版本一致性（S095·M-R40 副本静默过期）---
    if only in (None, '20'):
        # 注入：合成一行「台账 version=9.9.9 / 实际=磁盘真版本」的**人为失配**，
        # 驱动 _sig_eco_ledger → 应报「版本滞后」。
        hit = False
        try:
            _fake_rows = [{'技能名': 'probe-hub', 'version': '9.9.9'}]
            hit = bool(_sig_eco_ledger(_fake_rows))
        except Exception as _e:
            print(f'  ⚠️ 信号源20 探针注入失败（{type(_e).__name__}: {_e}）——'
                  f'本次捕获判定不可信')
        # ★ S161 修：原清零判据读**真库**（`not _sig_eco_ledger()`），把「台账此刻是否已
        #   全部对齐」这个**生态状态**混进了「检测器逻辑是否正常」这个**逻辑判据**。
        #   后果：每次升版本（台账手工维护、必然滞后几小时）都会让「清理后清零=否」常亮——
        #   S161 实测 5 条滞后（conductor 1.70.1/1.70.5、hub 1.0.53/1.0.54、janitor 1.2.3/1.2.5…），
        #   即 **真实缺陷在报，却伪装成「探针红」**，让人误以为检测器坏了 → M-R80「恒报红→人无视」。
        #   修法（与信号源24 同款）：清零用**合成一致行**（台账值=磁盘真值）驱动，
        #   检测器对「一致」必须静默。真库滞后归 source-20 的**输出**去报（它本来就报），
        #   不再借探针通道冒充。
        _clean = False
        try:
            _ph_v = ''
            _ph_md = os.path.join(SKILL_ROOT, 'probe-hub', 'SKILL.md')
            if os.path.exists(_ph_md):
                _m = re.search(r'^version:\s*"?([^"\n]+)"?', open(_ph_md, encoding='utf-8').read(), re.M)
                _ph_v = (_m.group(1).strip() if _m else '')
            if _ph_v:
                _clean = not _sig_eco_ledger([{'技能名': 'probe-hub', 'version': _ph_v}])
            else:
                print('  ⚠️ 信号源20 清零用例无法构造（probe-hub/SKILL.md 无 version 行）——'
                      '本项清零判定不可信')
        except Exception as _e:
            print(f'  ⚠️ 信号源20 清零用例异常（{type(_e).__name__}: {_e}）')
        results.append(('信号源20 生态台账版本一致性', hit, _clean))
    # --- 信号源21 经验对照缺口（S107·规则57 方法极）---
    if only in (None, '21'):
        # 注入：合成一段「某技能升了版但无对照留痕」的 review-lend 输出，
        # 驱动 _sig_lend_gap → 应报「经验对照缺口」。
        hit = False
        try:
            _fake = '🧾 经验对照对账\n\n  ⚠️ zzprobeskill 升了版但无经验对照留痕：## v9.9.9 探针样例\n\n  共 1 处缺口'
            hit = any('zzprobeskill' in x['evidence'] for x in _sig_lend_gap(_fake))
        except Exception as _e:
            print(f'  ⚠️ 信号源21 探针注入失败（{type(_e).__name__}: {_e}）——'
                  f'本次捕获判定不可信')
        # 清零：合成「无缺口」输出应解析为空（不假阳性）
        clean = not _sig_lend_gap('🧾 经验对照对账\n\n  ✅ 全部升级均有对照留痕')
        results.append(('信号源21 经验对照缺口', hit, clean))
    # --- 信号源22 会话经验留痕缺口（S108·规则58）---
    if only in (None, '22'):
        # 注入：合成「工作锚点新、双池旧」三元组 → 应报缺口（回归形态）。
        hit = False
        try:
            _eco = [{'末次升级': '2026-10-16T12:00:00Z'}]
            _sess_old = [{'时间': '2026-10-14T00:20:00Z'}]     # 落后 2.5 天（超 1 天阈值）
            _lib_fresh = [{'时间': '2026-10-16T11:00:00Z'}]
            hit = any('会话台账' in x['evidence'] for x in _sig_pool_gap((_sess_old, _lib_fresh, _eco)))
        except Exception as _e:
            print(f'  ⚠️ 信号源22 探针注入失败（{type(_e).__name__}: {_e}）——'
                  f'本次捕获判定不可信')
        # 清零：双池均新于锚点 → 无缺口（不得假阳性）
        _clean_ok = not _sig_pool_gap((
            [{'时间': '2026-10-16T12:30:00Z'}],
            [{'时间': '2026-10-16T12:30:00Z'}],
            [{'末次升级': '2026-10-16T12:00:00Z'}]))
        # 三态：读失败（空行）不得折叠成"无缺口"
        _clean_undet = bool(_sig_pool_gap(([], [], [])))

        # ★ S158 十补：原注入只造「落后 2.5 天」——只能证明**大缺口**可抓，
        #   证明不了"今天又漏了一轮"可抓（那正是实测漏掉的形态：lag 0.018 天 / 0.125 天）。
        #   而阈值刚从 24h 收到 4h，若不补小时级注入，等于只测了旧行为。
        # ★ 注入时间必须**用 datetime 算**，不能手拼字符串。
        #   我第一版写成 f'...T{14 - hours}:00:00Z'，6h 时得到 `T8:00:00Z`——
        #   小时段缺前导零，fromisoformat 解析失败，于是函数走「无可解析时间」分支，
        #   报的是"无法判定"而不是"落后 N 小时"，`'落后最近工作锚点' in evidence` 恒为 False，
        #   signal-check 当场判「捕获=否」。**这正是判据要问的那句话：报的是我设计的那条错吗。**
        _ANCHOR = datetime.datetime(2026, 10, 18, 14, 21, 12)

        def _mk_hit(hours):
            _a = [{'末次升级': _ANCHOR.strftime('%Y-%m-%dT%H:%M:%SZ')}]
            _old = [{'时间': (_ANCHOR - datetime.timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')}]
            return any('落后最近工作锚点' in x['evidence']
                       for x in _sig_pool_gap((_old, _fresh_l, _a)))
        _fresh_l = [{'时间': (_ANCHOR - datetime.timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}]
        _hit_3h = _mk_hit(3)      # 3h < 4h 阈值 → 不该报（边界内侧）
        _hit_6h = _mk_hit(6)      # 6h > 4h 阈值 → 该报（边界外侧）
        hit = bool(hit and (not _hit_3h) and _hit_6h)

        # 空时间单列：条目在但没填时间 → 必须报（原实现被 _latest_ts 静默跳过）
        _hit_nots = any('缺「时间」字段' in x['evidence']
                        for x in _sig_pool_gap(([{'时间': ''}], _fresh_l,
                                                [{'末次升级': '2026-10-18T14:21:12Z'}])))
        clean = bool(_clean_ok and _clean_undet and _hit_nots)
        results.append(('信号源22 会话经验留痕缺口', bool(hit), clean))

    # --- 信号源23 核心库行数归零（S110）---
    if only in (None, '23'):
        # 合成注入：① 真空（ok 语义下 0 行）→ 应报「归零」；② 读失败（None）→ 应报「无法判定」
        try:
            _hit_zero = any('归零' in x['evidence'] for x in _sig_core_db_rows(lambda db: []))
        except Exception as _e:
            print(f'  ⚠️ 信号源23 探针注入失败（{type(_e).__name__}: {_e}）——本次捕获判定不可信')
        _hit_undet = any('无法判定' in x['evidence'] for x in _sig_core_db_rows(lambda db: None))
        hit = bool(_hit_zero and _hit_undet)
        # 清零：正常行数 → 不报；且不得写探针锚（_api_override 路径不落盘）。
        # **S155 修：合成值不能用小数字。**
        # 探针原本合成 304 行，那是按当年的锚点 200 设计的（304 > 200*0.5 → 不报）。
        # 但真实锚点随库增长会变（DB_SRC 现已 801），304 < 801*0.5 → **被判骤降**，
        # 于是"清零"这一步永远失败，信号源23 被自己的探针判为不可信。
        # 探针的语义是「行数充足、没跌」，那就必须给一个**明显高于任一锚点一半**的值。
        # 教训：**探针的合成值若隐含依赖真实数据规模，数据一涨探针就失效**——
        # 合成值应表达语义（"充足"），不表达某个具体历史数字。
        _N_FINE = 100000      # 语义：行数充足（远大于任一核心库锚点的一半）
        _clean_ok = not _sig_core_db_rows(lambda db: [{'api接口': 'x'}] * _N_FINE)
        clean = bool(_clean_ok)
        results.append(('信号源23 核心库行数归零', hit, clean))
    # --- 信号源24 三层备份过期（S126）---
    if only in (None, '24'):
        # 注入：伪造 96h 前的备份日 → 应报「备份过期」
        import time as _t24
        _old_ts = _t24.time() - 96 * 3600
        _hit = any(t == '备份过期' for t, _e in _sig_backup_stale(_state_override={'last': {'date': _t24.strftime('%Y%m%d', _t24.localtime(_old_ts))}}))
        # 清零：**合成一份新鲜 state**（今天）→ 检测器必须静默。
        # ★ S161 修：原清零读真实 state（`not _sig_backup_stale()`），只要今天没跑过
        #   `conductor backup` 就恒为「清理后清零=否」——实测 20261018 距今 24h+ 即常亮。
        #   这与信号源20 同一个错：**拿生态状态冒充检测器逻辑**。
        #   真备份过期归 source-24 的**输出**报（它本来就报），不借探针通道冒充。
        _clean = not _sig_backup_stale(
            _state_override={'last': {'date': _t24.strftime('%Y%m%d')}})
        # 基线（无注入时不得恒报）：见上——真实 state 超 24h 属**真缺陷**，非检测器误报。
        results.append(('信号源24 三层备份过期', bool(_hit), bool(_clean)))
    # --- 信号源25 问题遗留超期（M-R73）---
    if only in (None, '25'):
        import time as _t25
        _old = _t25.strftime('%Y-%m-%d', _t25.localtime(_t25.time() - 10 * 86400))
        _hit = any(t == '问题超期' for t, _e in _sig_issue_stale(_state_override={'issues': [{'id': 'P999', 'title': '探针注入', 'opened': _old, 'status': 'open'}]}))
        _clean = not _sig_issue_stale(_state_override={'issues': [{'id': 'P998', 'title': '今日新登记', 'opened': _t25.strftime('%Y-%m-%d'), 'status': 'open'}]})
        results.append(('信号源25 问题遗留超期', bool(_hit), bool(_clean)))
    # --- 信号源26 监测登记册副本复活（S132 P007）---
    if only in (None, '26'):
        import tempfile as _tf26, shutil as _sh26
        _tmp = _tf26.mkdtemp(prefix='sig26_')
        _hit = _clean = False
        try:
            # 注入：伪造一个含实体的副本 → 必须捕获
            for sk in ('tvbox-inspect', 'legacy-watchdog'):
                os.makedirs(os.path.join(_tmp, sk), exist_ok=True)
            json.dump({'monitors': [{'id': 'M99', 'name': '合成'}]},
                      open(os.path.join(_tmp, 'tvbox-inspect', 'monitors.json'), 'w'))
            json.dump({'monitors': [{'id': 'M99', 'name': '合成副本'}]},
                      open(os.path.join(_tmp, 'legacy-watchdog', 'monitors.json'), 'w'))
            _hit = any(t == '登记册副本' for t, _e in _sig_registry_fork(_root_override=_tmp))
            # 清理：副本改为合规指针（superseded_by）→ 静默
            json.dump({'superseded_by': 'tvbox-inspect/monitors.json', 'monitors': []},
                      open(os.path.join(_tmp, 'legacy-watchdog', 'monitors.json'), 'w'))
            _clean = not _sig_registry_fork(_root_override=_tmp)
        finally:
            _sh26.rmtree(_tmp, ignore_errors=True)
        results.append(('信号源26 监测登记册副本复活', bool(_hit), bool(_clean)))
    # --- 信号源27 源库状态未终态（S134 P011）---
    if only in (None, '27'):
        # 注入：含悬空行（待查/空）→ 必须捕获
        _hit = any(t == '状态终态' for t, _e in _sig_status_terminal(_rows_override=[
            {'状态': '正常'}, {'状态': '停用'}, {'状态': '待查'}, {'状态': None}]))
        # 清理：全部终态 → 静默
        _clean = not _sig_status_terminal(_rows_override=[
            {'状态': '正常'}, {'状态': '不适用'}, {'状态': '停用'}])
        results.append(('信号源27 源库状态未终态', bool(_hit), bool(_clean)))
    # --- 信号源28 生态技能漏纳管（S135）---
    if only in (None, '28'):
        import tempfile as _tf28, shutil as _sh28
        _tmp = _tf28.mkdtemp()
        try:
            # 注入：造一个 tvbox-zzprobe 技能目录（有 SKILL.md，但不在 eco_skills.json）
            _d = os.path.join(_tmp, 'tvbox-zzprobe')
            os.makedirs(_d, exist_ok=True)
            open(os.path.join(_d, 'SKILL.md'), 'w', encoding='utf-8').write('---\nname: tvbox-zzprobe\n---\n')
            _hit = any('zzprobe' in r['skill'] for r in _sig_skill_registry_gap(_root_override=_tmp))
            # 清理：删掉该目录 → 静默
            _sh28.rmtree(_d, ignore_errors=True)
            _clean = not _sig_skill_registry_gap(_root_override=_tmp)
        finally:
            _sh28.rmtree(_tmp, ignore_errors=True)
        results.append(('信号源28 生态技能漏纳管', bool(_hit), bool(_clean)))

    # --- 信号源29 入口判定取值域（S136 五续·M-R87）---
    # 这条探针的注入方式是「换一把坏掉的尺子」：给检测器一份**反噬版**判定函数，
    # 看它能不能当场指出来。用合成函数驱动，全程不碰生产入口脚本（零副作用）。
    if only in (None, '29'):
        def _bad_ruler(ret):
            # 典型反噬：把 truthy 一律当失败（正是我自己写第一版时犯的错）
            return 0 if not ret else 1
        _hit = bool(_sig_entry_verdict(_load_override=_bad_ruler))
        # 清理：换回正本函数 → 必须清零
        _clean = not _sig_entry_verdict()
        results.append(('信号源29 入口判定取值域', bool(_hit), bool(_clean)))
    # --- 信号源30 评分器状态可分辨（S137·M-R88）---
    # 注入方式：给检测器一份**静默降级版**评分器——正是 S137 修掉的那个原实现
    # （无法解析时一律 return 80，把「没量到」伪装成「还凑合」）。看它能否当场点名。
    if only in (None, '30'):
        def _bad_scorer(ready, total, watchdog_ok, adopted, denom, pending,
                        health_out='', gain_out='', noise_out='', snap=''):
            # 静态降级：只要不是明说「无高噪声」，一律给中间分 80，且不给状态
            return {'cat_noise': 100 if '无高噪声' in noise_out else 80}
        _hit = bool(_sig_score_states(_score_override=_bad_scorer))
        _clean = not _sig_score_states()
        results.append(('信号源30 评分器状态可分辨', bool(_hit), bool(_clean)))
    # --- 信号源31 入口判定无状态（S138·M-R93）---
    # 注入方式：换回**有状态版**入口——正是 S138 之前的实现（三个模块全局 + 按序读）。
    # 它的取值域是对的（所以前 10 项会过），但 `_RC` 不重置，**A 组会全绿、B 组必挂**。
    # 这条注入专门证明：探针能抓到「只看方向对、不看有没有残留」的半对实现。
    if only in (None, '31'):
        class _StatefulMod:
            """模拟旧实现：全局 _RC 不重置，失败会传染给下一次。

            注意 `reset()`：真实旧实现每次是**独立进程**，所以 exit/err 天然是新值；
            这里同进程模拟，必须手工提供这个复位点，否则 10 个用例会互相污染，
            报出「exit(0) 也被判失败」这种注入版自带的假 bug（踩过两次）。
            唯一**故意**保留的缺陷：`rc` 不在 reset 里——这正是 S138 的原病。
            """
            _G = {'exit': 0, 'err': '', 'rc': 0}

            @classmethod
            def reset(cls):
                cls._G['exit'] = 0
                cls._G['err'] = ''            # ← 故意不重置 'rc'


            class _V:
                def __init__(self, ok, exit_code, err=''):
                    self.ok, self.exit_code, self.err = ok, exit_code, err

                def reason(self):
                    return self.err

            @classmethod
            def safe_dispatch(cls, module, argv):
                g = cls._G
                g['exit'] = 0
                g['err'] = ''
                # ← 关键：只复刻「_RC 不重置」这一个真实缺陷（S138 原病）。
                # 不能让注入版自带别的 bug，否则报出来的就不是我们要证的那条——
                # 首次写这份注入时正是踩了这个坑：SystemExit 分支没重置 rc，
                # 于是「exit(0) 被判失败」先报红，把真正的「状态残留」证据挡住了。
                if module is None:
                    return cls._V(False, 127, '模块缺失')
                rc = 0                       # 局部正确算出本次 rc
                try:
                    ret = module.dispatch(list(argv))
                    rc = 0 if isinstance(ret, bool) else (ret if isinstance(ret, int) else 0)
                except SystemExit as e:
                    c = e.code
                    g['exit'] = 0 if c is None else (c if isinstance(c, int) else 1)
                except Exception as e:
                    g['err'] = f'{type(e).__name__}: {e}'
                # 只有「真的自己报了非0」才写进全局——但**不清零**，这正是原病
                if rc:
                    g['rc'] = rc
                # 真实旧实现是 `if exit!=0 ... elif rc ...` 的顺序判定；
                # 这里必须照抄那个顺序，否则注入版会自带「exit(0) 也被 rc 拖累」的假 bug，
                # 报出来的就不是我们要证的那条（首次写这份注入时踩过）。
                ok = bool(not g['err'] and g['exit'] == 0 and not g['rc'])
                return cls._V(ok, g['exit'], g['err'] if not ok else '')
        _hit = bool(_sig_verdict_pure(_mod_override=_StatefulMod))
        _clean = not _sig_verdict_pure()
        results.append(('信号源31 入口判定无状态', bool(_hit), bool(_clean)))
    # --- 信号源32 投影孤儿词（S148·M-R55）---
    # 注入方式：给某个技能的投影塞进一批 description 里根本没有的词。
    # 这证明的是「并集派生会把孤儿词藏起来」——注入前必须确认：
    #   · 路由照样能命中（所以信号源5 / ROUTE_GOLDEN 都测不出）
    #   · 只有本信号源能报出来
    # 清理语义：恢复原投影后必须归零，否则说明检测器把正常词也当孤儿（恒报）。
    if only in (None, '32'):
        _hit = _clean = False
        try:
            _bak = list(_SKILL_INTENTS)
            try:
                # 找第一个有投影的技能，往尾部塞 20 个必然不存在的词（超过 LIMIT=8）
                _fake = ' '.join(f'孤儿词测{i}' for i in range(20))
                for _i, (_sk, _base) in enumerate(_SKILL_INTENTS):
                    if _base.strip():
                        _SKILL_INTENTS[_i] = (_sk, _base + ' ' + _fake)
                        break
                _hit = bool(_intents_orphan_audit())
            finally:
                _SKILL_INTENTS[:] = _bak
            _clean = not _intents_orphan_audit()
        except Exception as e:
            print(f'  ⚠️ 信号源32 注入异常：{type(e).__name__}: {e}')
        results.append(('信号源32 投影孤儿词', bool(_hit), bool(_clean)))
    # --- 信号源33 规则ask可命中（S148·M-R55）---
    # 注入两条**不同的退化**，必须都能抓到——只注入一种，
    # 「另一种判据其实没生效」就测不出来（M-R77：验证须独立于被验对象）。
    #   ① 删掉某条规则的 ask → 报「缺失」
    #   ② 把某条 ask 换成 summary 原文 → 报「雷同」（这是"假补"的真实形态，
    #      生产中最可能发生：加字段的人图省事直接抄上一行）
    # 判据独立性：清理后必须整体归零；且基线段（无注入）已证明正常态不误报。
    if only in (None, '33'):
        _hit = _hit2 = _clean = False
        try:
            _fp = os.path.join(SKILL_ROOT, 'ecosys-conductor', 'scripts', 'rules_registry.json')
            _regb = json.load(open(_fp, encoding='utf-8'))
            _rs = _regb.get('rules') or []
            if _rs:
                # ① 缺失注入
                _r0 = _rs[0]
                _old = _r0.get('ask')
                _r0['ask'] = ''
                _hit = any('无 ask' in e for _, e in _sig_ask_hittable(_regb))
                _r0['ask'] = _old
                # ② 雷同注入（换第 2 条，避免与 ① 同一条互相掩盖）
                if len(_rs) > 1:
                    _r1 = _rs[1]
                    _old1 = _r1.get('ask')
                    _r1['ask'] = _r1.get('summary') or ''
                    _hit2 = any('雷同' in e for _, e in _sig_ask_hittable(_regb))
                    _r1['ask'] = _old1
            _clean = not _sig_ask_hittable(_regb)
        except Exception as e:
            print(f'  ⚠️ 信号源33 注入异常：{type(e).__name__}: {e}')
        # 两条注入都命中才算通过——单条通过会掩盖另一条判据失效
        results.append(('信号源33 规则ask可命中（缺失+雷同双注入）', bool(_hit and _hit2), bool(_clean)))
    # --- 信号源34 命令执行器存在（S148·M-R55）---
    # 注入方式：喂一份**缺了 inspect/janitor 的** ROUTE 词表——这正是 S148 修复前的真实状态。
    # 用真实历史状态做注入样本，比编造一个假词更有说服力（能证明它抓的正是那次事故）。
    # 清理语义：恢复真实词表后必须归零；基线段已证明正常态不误报（否则 hit 也是假通过）。
    if only in (None, '34'):
        _hit = _clean = False
        try:
            _fake_route = {v for v in ('close', 'block', 'run', 'chain', 'manifest', 'contracts',
                                       'p', 'conductor', 'probe', 'evolve', 'watchdog', 'brief',
                                       'status', 'learn', 'log', 'sync', 'audit', 'snapshot',
                                       'ingest', 'eco', 'lib-learn', 'lib-log', 'sizes', 'pipe',
                                       'finish')}
            _hit = any('inspect' in e or 'janitor' in e
                       for _, e in _sig_cmd_executable(_route_override=_fake_route))
            _clean = not _sig_cmd_executable()
        except Exception as e:
            print(f'  ⚠️ 信号源34 注入异常：{type(e).__name__}: {e}')
        results.append(('信号源34 命令执行器存在', bool(_hit), bool(_clean)))
    # --- 信号源42 会话提炼缺口（S159·M-R117）---
    # 注入三段，缺一不可——**只注入"该报"的那一种，测不出"该静默"是否真静默**：
    #   ① 空正文            → 落点+证据**都应**报（2 条）
    #   ② 只有落点无证据     → 只报证据（1 条）
    #   ③ 落点+证据齐        → **必须静默**（0 条）
    # 为什么坚持做 ③：本条的失效方向是「什么都没做也能过」，若检测器恒报红，
    # 人会直接忽略它，等于没装——这正是 S155 抓到的「假阳性基线」那类病。
    # 三态另测：journal 读不到时必须报「无法判定」，不得折成"干净"。
    if only in (None, '42'):
        _r1 = _sig_session_distill(_recap_override='')
        _r2 = _sig_session_distill(_recap_override='改了 skills/x/y.py:12')
        _r3 = _sig_session_distill(_recap_override='skills/x/y.py:12 改了一行\n$ python3 y.py → OK')
        # ① 两条都报 ②  恰好一条且报的是"无证据" ③ 一条都不报
        _hit = (len(_r1) == 2
                and any('无落点' in x['evidence'] for x in _r1)
                and any('无证据' in x['evidence'] for x in _r1)
                and len(_r2) == 1 and '无证据' in _r2[0]['evidence']
                and len(_r3) == 0)
        # 清理语义：恢复真实 journal 后，若本轮确有落点+证据则必须归零；
        # 不能简单写 `_clean = not _sig_session_distill()`——那会在"本轮确实没写落点"时
        # 把**真缺陷**判成"检测器坏了"（M-R60：分不清"报的是真事"与"检测器恒报"）。
        _real = _sig_session_distill()
        _clean = (len(_r3) == 0) and all('无法判定' not in x['evidence'] for x in _real)
        results.append(('信号源42 会话提炼缺口', bool(_hit), bool(_clean)))
    # --- 信号源35 清道夫健康（S155·元监察）---
    if only in (None, '35'):
        jfp = os.path.join(SKILL_ROOT, 'tvbox-janitor', 'scripts', 'janitor_state.json')
        _hit = _clean = False
        if os.path.exists(jfp):
            _jbak = jfp + '.probe'; shutil.copy(jfp, _jbak)
            try:
                _jd = json.load(open(jfp, encoding='utf-8'))
                _jd.setdefault('items', []).append(
                    {'id': 'zzprobe_unmoved', 'title': '探针注入待删物',
                     'kind': 'doc', 'moved': False, 'added': '2026-10-18'})
                json.dump(_jd, open(jfp, 'w', encoding='utf-8'), ensure_ascii=False)
                _hit = bool(_sig_janitor())   # 应抓到未移动项
            finally:
                shutil.move(_jbak, jfp)
            _clean = not _sig_janitor()       # 清理后归零
        results.append(('信号源35 清道夫健康', bool(_hit), bool(_clean)))
    # --- 信号源36 探针存活（S155·元监察）---
    if only in (None, '36'):
        # 结构性：常态所有核心探针函数存在 → 无缺失 → 存活（函数消失由 getsource 检查自动发现）
        _hit = _clean = (len(_sig_probe_alive()) == 0)
        results.append(('信号源36 探针存活', bool(_hit), bool(_clean)))
    # --- 信号源37 监察司自身（S155·元监察）---
    if only in (None, '37'):
        _hit = _clean = False
        _mfp = os.path.join(SKILL_ROOT, 'tvbox-inspect', 'monitors.json')
        if os.path.exists(_mfp):
            _mbak = _mfp + '.probe'; shutil.copy(_mfp, _mbak)
            try:
                open(_mfp, 'w', encoding='utf-8').write('{ 损坏的非法JSON [')  # 注入：登记册损坏
                _hit = bool(_sig_inspect())   # 应抓到"monitors.json 损坏"
            finally:
                shutil.move(_mbak, _mfp)
            _clean = not _sig_inspect()       # 恢复后应无告警（M30 挂名项已清零/如实标 manual_track）
        results.append(('信号源37 监察司自身', bool(_hit), bool(_clean)))
    # --- 信号源38 大生态对账（S155·元监察·S150 回归）---
    if only in (None, '38'):
        _hit = _clean = False
        _orig = dict(LINK_DBS)
        try:
            LINK_DBS['DB_DEAD_失效源库'] = '92dSuG6uUbxitGuyx4Saaf'  # 注入：指回退役空库（S150 根因）
            _hit = bool(_sig_eco_link())   # 应抓到
        finally:
            LINK_DBS.clear(); LINK_DBS.update(_orig)
        _clean = not _sig_eco_link()       # 恢复后应无告警
        results.append(('信号源38 大生态对账', bool(_hit), bool(_clean)))
    # --- 信号源39 全技能监护覆盖（S155·范围扩张："所有技能都要有人监察"）---
    # 注入方式：临时造一个「scripts/ 下 .py 语法损坏」的技能目录 → 本监护器必须抓到它
    # （编译失败=②未过）→ 清理（删除临时目录）后该技能不再出现。
    # 这证明监护器是"活的"（能发现新坏技能），而非又一个空设计。
    if only in (None, '39'):
        _td = os.path.join(SKILL_ROOT, '_probe_broken_skill')
        _made = False
        try:
            os.makedirs(os.path.join(_td, 'scripts'), exist_ok=True)
            open(os.path.join(_td, 'SKILL.md'), 'w', encoding='utf-8').write('# probe\n')
            open(os.path.join(_td, 'scripts', 'broken.py'), 'w', encoding='utf-8').write('def (::: 语法错误\n')
            _made = True
            _found = {x['skill'] for x in _sig_skill_coverage(strict=True)}
            _hit = ('_probe_broken_skill' in _found)
        finally:
            if _made and os.path.isdir(_td):
                shutil.rmtree(_td)
        _after = {x['skill'] for x in _sig_skill_coverage(strict=True)}
        _clean = ('_probe_broken_skill' not in _after)
        results.append(('信号源39 全技能监护覆盖', bool(_hit), bool(_clean)))
    # --- 信号源40 资料库调用协议（S158·技能↔资料库）---
    # 注入方式：在 tvbox-source-hub/scripts/ 下临时造一个「直连 library 且无三态」的 .py
    # → 本检测器必须抓到它（strict 下归入硬违规）→ 删除后不再出现。
    # 为什么要注入验证：本检测器是**纯静态扫描**，最典型的失效方式是"正则写歪了，
    # 什么都匹配不到"——那样它会对所有违规报绿，比没有它更危险（给人以已被守护的错觉）。
    # 注意注入的文件名要在 SKILL_ROOT 下的真实技能目录内（检测器只遍历 ECO_SKILLS），
    # 且用完必须删干净，否则 verify 的 ⑥ 留痕洁净会报污染。
    #
    # ★ S158 踩过的坑（勿回归）：探针文件名曾用 `_probe_lib_raw.py`，而扫描器**不排除探针**，
    #   于是本检测器抓到了自己的探针源码（conductor.py 里那段字符串字面量），
    #   表现为"健康态恒报一条硬违规"→ 进假阳性基线 → 逼人去调基线数字。
    #   现在的双重保险：① 扫描器跳过 `_probe_` 前缀文件（探针是受控造违，不是调用点）
    #   ② 探针改用**中性文件名** `_probe_lib_scan_tmp.py`，且由 `include_probe=True` 专门放行扫描，
    #   这样"能不能抓到"仍然被真实验证，而不是因为文件名被排除而"假装通过"。
    if only in (None, '40'):
        _PROBE_NAME = '_probe_lib_scan_tmp.py'
        _inj = os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'scripts', _PROBE_NAME)
        _made = False
        try:
            open(_inj, 'w', encoding='utf-8').write(
                'import subprocess, sys\n'
                'LIB = "/root/.codebuddy/skills/library"\n'
                'def f(db):\n'
                '    return subprocess.run([sys.executable, f"{LIB}/database/query_database_record.py",\n'
                '                           "--database-id", db], capture_output=True, text=True).stdout\n')
            _made = True
            # include_probe=True：探测时必须**连探针一起扫**，否则排除规则会让 hit 假通过
            _found = {x['evidence'] for x in _sig_lib_protocol(strict=True, include_probe=True)}
            _hit = any(_PROBE_NAME in e for e in _found)
        finally:
            if _made and os.path.exists(_inj):
                os.remove(_inj)
        _after = {x['evidence'] for x in _sig_lib_protocol(strict=True, include_probe=True)}
        _clean = not any(_PROBE_NAME in e for e in _after)
        results.append(('信号源40 资料库调用协议', bool(_hit), bool(_clean)))
    # --- 信号源41 静态未定义名（S158）---
    # 注入方式：造一个**语法完全合法**但引用了不存在变量的 .py → 必须被抓到 → 删除后清零。
    # 为什么要注入验证：pyflakes 的输出是文本，正则匹配行格式（`file:line:col: undefined name`）
    # 一旦 pyflakes 改版或我写歪，就会"什么都没匹配到"→ 对全部技能报绿。
    # 注意探针文件名避开 `_probe_` 前缀：本检测器**不排除**探针（它扫的是名字，探针文件
    # 本身若有未定义名也是真缺陷），故这里只要清理干净即可。
    if only in (None, '41'):
        _inj2 = os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'scripts', '_probe_undef_tmp.py')
        _made2 = False
        try:
            open(_inj2, 'w', encoding='utf-8').write(
                '# 本文件语法完全合法，但引用了不存在的名字（模拟"改调用姿势漏清旧变量"）\n'
                'def f():\n'
                '    try:\n'
                '        raise ValueError("x")\n'
                '    except Exception:\n'
                '        return _this_name_does_not_exist_anywhere\n')
            _made2 = True
            _f2 = {x['evidence'] for x in _sig_undef_name()}
            _hit2 = any('_probe_undef_tmp.py' in e for e in _f2)
        finally:
            if _made2 and os.path.exists(_inj2):
                os.remove(_inj2)
        _after2 = {x['evidence'] for x in _sig_undef_name()}
        _clean2 = not any('_probe_undef_tmp.py' in e for e in _after2)
        results.append(('信号源41 静态未定义名', bool(_hit2), bool(_clean2)))
    # --- 信号源43 台账回写防错行（S162）---
    # 守的不是「能不能写」，是「**写对没写对行**」。
    #
    # 事故（2026-10-19 现场）：我手工试批量回写时，从两个数据源各按**行序**取 id
    # （`rows_of` 的属性视图里 library 排第 5，`query_database_record` 的 id 映射里
    # tvbox-interface 排第 1），把 library 的 record_id 配给了 tvbox-interface，
    # 于是**两边版本同时被写成 0.6.1**。而 batch_update 对每条都回 success:true。
    # 这是 M-R77「success ≠ 落库」的第三种形态：**落库了，但落错了行**——
    # 回读 version 字段确实变了，只是变到了别人身上。
    #
    # 判据：喂一个**故意错配**的映射给 `sync_eco`，必须
    #   ① 拒写（返回非 0）
    #   ② 报出「id 实际挂着 XX」而不是含糊的"失败"
    #   ③ 一个字节都不发出去（用 dry 哨兵确认没有调用 batch_update）
    if only in (None, '43'):
        _hit43 = _clean43 = False
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import version_guard as _vg43
            _orig43 = _vg43._eco_record_ids

            def _bad43():
                m, why = _orig43()
                m = dict(m)
                if 'probe-hub' in m and 'library' in m:
                    m['probe-hub'] = m['library']      # 注入：把 A 的 id 配给 B
                return m, why

            _vg43._eco_record_ids = _bad43
            _buf43 = io.StringIO()
            try:
                with contextlib.redirect_stdout(_buf43):
                    _rc43 = _vg43.sync_eco([
                        {'skill': 'probe-hub', 'skill_md': '9.9.9', 'changelog': '9.9.9',
                         'eco': '0.0.1', 'status': 'drift', 'eco_known': True}])
            finally:
                _vg43._eco_record_ids = _orig43
            _out43 = _buf43.getvalue()
            # 用 9.9.9 这种**绝不会是真实版本**的值：若真被写出去，回读会看到它
            _hit43 = (_rc43 != 0 and '实际挂着' in _out43)
            # 清理后清零：云端 probe-hub / library 的版本都不该是 9.9.9（证明确实没发出去）
            _i2n43, _n2i43, _ = _vg43._eco_id_name()
            _eno43, _eok43 = _vg43._ver_from_eco()
            _polluted = [k for k, v in _eno43.items() if v == '9.9.9']
            _clean43 = bool(_eok43) and not _polluted
        except Exception:
            _hit43 = _clean43 = False
        results.append(('信号源43 台账回写防错行', bool(_hit43), bool(_clean43)))
    # --- 信号源44 回读防缓存欺骗（S162）---
    # 守的不是「回读能不能读到值」，是「**读到的值是不是刚才写的那个**」。
    #
    # 现场（2026-10-19）：probe-hub 升 1.10.0 后跑 `--sync-eco`，云端确实写进去了
    # （另开进程读是 1.10.0），命令却报「回读仍为 '1.9.0'——未落库」，rc=1。
    # 我先误判成"接口读缓存有几十秒延迟"，加了退避重试，3 次全旧值；
    # 开新进程一测 0 秒就读到新值——**矛盾**，说明根本不是延迟。
    # 真根因：core 有两层缓存（rows 的 db 键 + api 的 ('api',script,args) 键），
    # `_cache=False` 只跳过第一层，api 默认 _cache=True 照样命中。
    # 这是**同一个坑第三次**（core.invalidate 注释里就写着「S020 实测复发，同症状第二次」）。
    #
    # 注入方式：把 `_ver_from_eco` 临时换成「半修版」——只 _cache=False、不 invalidate，
    # 本检测器必须报出来；还原后必须清零。
    # 为什么必须注入「半修」而不是「完全不修」：完全不修太容易抓，
    # 而真实事故恰恰是**看起来已经修了**（有 _cache=False）但仍漏一层。抓不住这个形态＝形同虚设。
    if only in (None, '44'):
        _hit44 = _clean44 = False
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import version_guard as _vg44
            _orig44 = _vg44._ver_from_eco
            _BASE44 = '''def _ver_from_eco():
    """注入用半修版：只 _cache=False，不 invalidate（正是 S158 修完、S162 复发的形态）"""
    try:
        sys.path.insert(0, LIB)
        from core import rows_of
        rows = rows_of(DB_ECO, _cache=False)
        if not rows:
            return {}, False
        return {str(r.get('技能名') or ''): str(r.get('version') or '') for r in rows}, True
    except Exception:
        return {}, False
'''
            _fp44 = os.path.abspath(_vg44.__file__)
            _bak44 = open(_fp44, encoding='utf-8').read()
            try:
                # 用源码注入而非改内存函数：本判据读的是源码文本，改函数对象抓不到
                import re as _re44
                _new44 = _re44.sub(r'def _ver_from_eco\(\):.*?(?=\ndef )', _BASE44, _bak44, flags=_re44.S)
                open(_fp44, 'w', encoding='utf-8').write(_new44)
                import importlib as _il44
                _il44.reload(_vg44)
                # 注意：判据函数在本模块（conductor）里，不在 version_guard 里
                _hit44 = len(_sig_eco_cache_stale()) > 0
            finally:
                open(_fp44, 'w', encoding='utf-8').write(_bak44)
                import importlib as _il44b
                _il44b.reload(_vg44)
            _clean44 = (len(_sig_eco_cache_stale()) == 0)
        except Exception:
            _hit44 = _clean44 = False
        results.append(('信号源44 回读防缓存欺骗', bool(_hit44), bool(_clean44)))
    # --- 信号源45 漂移修闭环不谎报（S162）---
    # 注入方式：把云端 probe-hub 版本改成 1.0.0（一个绝不会是真实版本的值），
    # 然后跑 `drift-fix`：必须**真把它改回 SKILL.md 的值**，且复检一致。
    # 清理：注入后无论如何都要把云端恢复成 SKILL.md 的版本，否则会污染台账。
    #
    # 为什么用真注入而不是只看代码：本命令的全部价值在"会自己动手并自己验收"，
    # 静态看代码只能证明"写了复检"这几个字，证明不了**它复检时读的确实是新值**。
    # 本轮已经把两种假绿形态都踩过一次（解析空报无需修 / 键名带空格误判本地矛盾），
    # 静态判据守不住这两类，必须真跑。
    if only in (None, '45'):
        _hit45 = False          # 只有"真跑成功并且真修对了"才置 True
        _clean45 = False        # 只有"还原后确认云端已是权威值"才置 True
        _why45 = ''             # 非空＝无法判定，单独说明，不折叠成"检测器不达标"
        _rid45, _want45, _bk45 = 'skvjHdZvVXaQYtQYBpcgnc', '', None   # _want45 随后从 SKILL.md 读，不硬编码
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import version_guard as _vg45
            # 取权威值（本地 SKILL.md），别硬编码——硬编码会在下次升版时假红
            _sk45 = open('/root/.codebuddy/skills/probe-hub/SKILL.md', encoding='utf-8').read()
            _m45 = re.search(r'(?m)^version:\s*"?([\d.]+)"?', _sk45)
            if _m45:
                _want45 = _m45.group(1)
            _eco45, _ok45 = _vg45._ver_from_eco()
            _bk45 = (_eco45 or {}).get('probe-hub') if _ok45 else None
            if _bk45:
                # 注入滞后
                _sp45 = __import__('subprocess')
                _sp45.run([sys.executable, os.path.join(_vg45.LIB_DB, 'batch_update_database_records.py'),
                           '--database-id', _vg45.DB_ECO, '--records',
                           json.dumps([{'record_id': _rid45,
                                        'properties': {'version': {'text': '1.0.0'}}}],
                                      ensure_ascii=False)],
                          capture_output=True, text=True)
                _r45 = _sp45.run([sys.executable, os.path.abspath(__file__), 'drift-fix'],
                                 capture_output=True, text=True)
                _t45 = (_r45.stdout or '') + (_r45.stderr or '')
                # 捕获判据：rc=0 且输出「已闭环」——**并且云端真变成了目标值**
                _now45, _nowok45 = _vg45._ver_from_eco()
                _hit45 = (_r45.returncode == 0 and '已闭环' in _t45
                          and _nowok45 and (_now45 or {}).get('probe-hub') == _want45)
            else:
                # 读不到基线 → 无法判定。显式记下原因，不静默折叠成"不达标"
                _why45 = '云端基线读不到——无法判定（M-R60）'
        except Exception as _e45:
            # M-R60：注入过程本身抛错 ≠ 检测器坏了，要能分辨。
            # 原写法 `except: _hit45 = _clean45 = False` 被信号源16 判为"折叠候选"——
            # 它说得对：那会把"我没跑起来"伪装成"检测器不达标"，两种完全不同的结论。
            _why45 = f'注入过程抛错（{type(_e45).__name__}: {str(_e45)[:80]}）——无法判定'
        finally:
            # 无论成败都要把云端还原成「本地 SKILL.md 的值」——那是权威
            # ★ 保护：_want45 为空说明连 SKILL.md 都没读到，**这时绝不能写**，
            #   否则会把云端 version 写成一个空串（比不还原更糟）。
            if not _want45:
                _why45 = _why45 or '读不到本地权威版本，跳过还原（不写空值）'
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                import version_guard as _vg45c
                if _want45:
                    __import__('subprocess').run(
                        [sys.executable, os.path.join(_vg45c.LIB_DB, 'batch_update_database_records.py'),
                         '--database-id', _vg45c.DB_ECO, '--records',
                         json.dumps([{'record_id': _rid45,
                                      'properties': {'version': {'text': _want45}}}],
                                    ensure_ascii=False)],
                        capture_output=True, text=True)
                _after45, _aok45 = _vg45c._ver_from_eco()
                _clean45 = bool(_aok45) and (_after45 or {}).get('probe-hub') == _want45
            except Exception as _e45c:
                # 还原本身失败是最坏情况（台账可能停在 1.0.0）——必须说出来，
                # 不能折叠成一句"清理后清零=否"了事。_clean45 保持 False（如实）。
                _why45 = (f'还原失败（{type(_e45c).__name__}: {str(_e45c)[:80]}）'
                          f'——**云端 probe-hub 可能停在非权威值，需人工核对**')
        results.append(('信号源45 漂移修闭环不谎报', bool(_hit45), bool(_clean45)))
        if _why45:
            print(f'     ↪ ❔ {_why45}')
    # --- 输出 + 落地留痕 ---
    ok = 0
    for name, hit, clean in results:
        mark = '✅' if (hit and clean) else '❌'
        ok += 1 if (hit and clean) else 0
        print(f'  {mark} {name}：捕获={"是" if hit else "否"} 清理后清零={"是" if clean else "否"}')
    print()
    # 覆盖对齐审计（S062）：探针数必须 == 注册表条目数，否则新增信号源漏加探针
    # ★ S177 步3：**只在全量模式判缺口**。单源模式（--source N）本来就只跑一个源，
    #   原代码无条件判 → 屏幕出现「1/1 逻辑有效」+「❌ 覆盖缺口 1/45」自相矛盾的输出。
    n_reg, n_probe = len(SIGNAL_SOURCES), len(results)
    _cov_gap_shown = (only is None) and (n_probe != n_reg)
    if _cov_gap_shown:
        print(f'  ❌ 覆盖缺口：探针 {n_probe} 个 ≠ 注册表 {n_reg} 个信号源（新增源须同步加探针）')
    print(f'探针结果: {ok}/{len(results)} 信号源逻辑有效（"依赖可达≠逻辑对"，故需活体证明）'
          + (f'  ⚠️ 覆盖缺口 {n_probe}/{n_reg}' if _cov_gap_shown else ''))
    # S078：基线缺口计入判据——S075 只打印告警，"知道不完整但不阻断"仍会放行（M-R53②）
    _base_gap = sorted(set(SIGNAL_SOURCES) - _baseline_ids, key=int) if only is None else []
    if _base_gap:
        print(f'  ❌ 假阳性基线覆盖缺口：缺失信号源 {",".join(_base_gap)}——这些源 hit 可能假通过')
    if fp_baseline:
        # S156：同 check() 内措辞——三态：可能是恒报，也可能是真缺陷，须复核而非直接归罪检测器
        print(f'   ⚠️ 假阳性基线：{len(fp_baseline)} 个检测器无注入即报（{",".join(fp_baseline)}）'
              f'——hit 可能假通过，**也可能报的是真缺陷**（须复核）')
    # 留痕（merge：同日只留最新；回读验证）
    log_fp = os.path.join(os.path.dirname(PLAN_FILE), 'signal_proven.json')
    today = datetime.date.today().isoformat()
    try:
        logs = json.load(open(log_fp, encoding='utf-8')) if os.path.exists(log_fp) else []
        logs = [x for x in logs if x.get('date') != today]
        logs.append({'date': today, 'probed': len(results), 'passed': ok,
                     'false_positive': fp_baseline, 'baseline_gap': _base_gap,
                     'sources': {n: (h and c) for n, h, c in results}})
        json.dump(logs, open(log_fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        back = json.load(open(log_fp, encoding='utf-8'))
        print(f'💾 已留痕 signal_proven.json（{len(back)} 期探针记录）——检测器逻辑有效性证明')
    except Exception as e:
        print(f'（留痕失败：{e}）')

    # ★ S177 步3 实测修（T1）：**本函数原来没有 return，末尾隐式返回 None → rc=0**。
    #   实测：屏幕打两行 ❌（源4、源16 都是「捕获=是 清理后清零=否」），`echo $?` 却是 0。
    #   后果比"报错"更坏：**所有按退出码判断的检查全部假绿**——CI 看 rc、门禁看 rc、
    #   我自己写的批量冒烟也看 rc，全都会把"两个检测器坏了"记成"通过"。
    #   这正是 M-R63 的孪生形态：M-R63 是"坏了还报绿"，这个是"连绿都不报"——
    #   **不报错的失败，比报错更难发现**。
    #
    #   退出码约定（刻意用 3 不用 1，为了和"脚本自身崩了"区分开）：
    #     0 = 全部探针通过，且覆盖齐、基线齐
    #     3 = **体检跑完了，但查出问题**（探针有红 / 覆盖缺口 / 基线缺口）
    #     1 = 脚本自身异常（未捕获异常，由 Python 自己给）
    #   为什么要区分 3 和 1：`rc=1` 时人第一反应是"命令挂了、去看 traceback"，
    #   而这里命令是好的、结论是坏的。两者的处置完全不同，不能压成同一个码。
    #
    #   ⚠️ **本改动已验证不影响 verify**：verify 的 ① 用 `_parse_probe_ratio` 解析文本，
    #   而 `_sh` 给非零退出码加的前缀（`⛔命令失败:退出码3｜`）是加在**开头**的，
    #   `_parse_probe_ratio` 仍能从中捞出 `(43, 45)`——实测确认（/tmp/chk.py）。
    #   若哪天改了 `_sh` 的前缀格式或 `_parse_probe_ratio` 的正则，**必须重跑这个验证**。
    _bad = [n for n, h, c in results if not (h and c)]
    # ★ S177 步3 自查修正：判据里那个「覆盖齐」的条件必须**只在全量模式**下生效。
    #   第一版我写成无条件 `len(results) != len(SIGNAL_SOURCES)`，
    #   结果单源模式 `--source 4` 当场 rc=3，理由是「覆盖缺口 1/45」——
    #   **单源本来就只跑一个源，那叫正常，不叫缺口。**
    #   （更难看的是：屏幕同时打「1/1 逻辑有效」和「❌ 覆盖缺口」，自相矛盾。）
    #   教训：给"整体完成任务"加判据时，**先问这个命令有没有"部分完成"的合法形态**。
    _cov_gap = (only is None) and (len(results) != len(SIGNAL_SOURCES))
    if _cov_gap or _base_gap or _bad:
        return 3
    return 0


# ---------- S114：经验预载注入（M-R68 双向闭环·使用面） ----------
def _exp_preload(intent, terms, limit=3):
    """经验池 → 任务现场自动注入：按意图关键词检索 ④经验库，命中即预载（M-R68 使用闭环）。
    设计：经验不是仓库是燃料——落池（M-R67）后的必经出口。三态：读失败显式报不折叠。"""
    try:
        sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
        import link
        rows = link.rows_of('XTuuj8mKtqnZSolDdeDxkq')
    except Exception as e:
        print(f'   ⚠️ 经验池读取失败（{type(e).__name__}: {e}）——本次无经验预载，勿当作"无经验"（M-R60）')
        return
    # 关键词：术语实体 + 英文token + 中文2字以上词组（去停用词）
    # S121 防御：terms 须为 dict（_translate 依赖 .items()）；传错类型跳过术语面只用 intent 分词，不炸整个 ask
    kws = {e for _, _, _, e in _translate(intent, terms) if e} if isinstance(terms, dict) else set()
    stop = {'怎么', '如何', '什么', '这个', '那个', '一下', '帮我', '需要', '处理', '问题'}
    for tok in re.findall(r'[a-zA-Z_]{3,}|[\u4e00-\u9fff]{2,6}', intent):
        if tok not in stop and len(tok) >= 2:
            kws.add(tok)
    if not kws:
        return
    # S123 检索升级（M-R23 兑现）：中文词块降为 bigram 参与打分——整词与经验行文词面难重叠，
    # 实测 10 意图仅命中 0.1 条/意图（使用闭环空转，"名义闭环"又一形态）。英文 token 仍整词。
    for k in [w for w in kws if re.search(r'[\u4e00-\u9fff]', w) and len(w) > 2]:
        kws.discard(k)
        kws |= (_bigrams(k, cjk_only=True) - _STOP_BIGRAMS)
    scored = []
    for r in rows:
        blob = ' '.join(str(r.get(k, '')) for k in ('场景', '坑', '解法', '可复用点', '编号'))
        hits = sum(1 for k in kws if k in blob)
        if hits >= 1:
            scored.append((hits, r))
    scored.sort(key=lambda x: -x[0])
    if scored:
        print('📚 经验预载（M-R68：开工先读池，同坑不二踩）——命中 '
              f'{min(len(scored), limit)}/{len(rows)} 条池存经验：')
        for hits, r in scored[:limit]:
            print(f"   [{r.get('编号','')}] 场景:{str(r.get('场景',''))[:36]}")
            print(f"      坑:{str(r.get('坑',''))[:44]}")
            print(f"      解法:{str(r.get('解法',''))[:60]}")
        print()


# ---------- S113：预触发站位表 + 会话监工（全局硬规则 M-R67 落地） ----------
# 标准任务流水线：上一站「产出」即下一站「预触发条件」——任务未到，技能先就位。
_PIPELINE = [
 {'stage': '①受理', 'skill': 'ecosys-conductor',
  'when': '用户说出任何需求（话术即触发，无需等指派）',
  'ready': 'tvbox conductor ask "<需求>"', 'out': '术语翻译+历史呼应+路由+拆解'},
 {'stage': '②侦察', 'skill': 'ecosys-conductor + tvbox-source-hub',
  'when': '①路由结论出现时自动预触发（开工链第一站）',
  'ready': 'tvbox chain 开工速览', 'out': '四极状态+雷区+闭环体检'},
 {'stage': '③执行', 'skill': '按 ask 路由结果',
  'when': '②雷区已知后自动预触发',
  'ready': 'tvbox chain <对应任务链>（manifest 可查 11 条）', 'out': '链内自带验收标准'},
 {'stage': '④质检', 'skill': 'tvbox-inspect',
  'when': '③产出落地后、交付前自动预触发',
  'ready': 'tvbox inspect scan / signal-check / verify', 'out': '遗留清单+探针基线'},
 {'stage': '⑤入库', 'skill': 'tvbox-source-hub(L7) + blacklist 闸',
  'when': '候选/增量成型时（未入总库前必经）',
  'ready': 'tvbox ingest --candidates <f> --dry → 实跑', 'out': '源总库+失效库分流+快照'},
 {'stage': '⑥留痕', 'skill': 'tvbox-source-hub + conductor(recap)',
  'when': '任务收口时（复盘闭环链末站）',
  'ready': 'tvbox chain 复盘闭环 ｜ tvbox conductor recap', 'out': '③台账 S 条目+④经验库 L 条目+监工缺口报告'},
 {'stage': '⑦进化', 'skill': 'skill-evolution + conductor',
  'when': '同类问题≥3次 或 规则整理周期到（⑥留痕的下游）',
  'ready': 'tvbox evolve recurring --min 3 / upgrade', 'out': '规则固化/技能升级立项'},
]

JOURNAL_FP = '/root/.codebuddy/skills/ecosys-conductor/session_journal.jsonl'

def cmd_next(rest):
    """next：流水线站位预告——当前任务在哪一站、下一站谁就位（预触发）。"""
    kw = rest[0] if rest else ''
    print('🛤️  标准任务流水线（上一站产出 = 下一站预触发条件）')
    for st in _PIPELINE:
        mark = ' ← 你在这' if kw and (kw in st['stage'] or kw in st['skill']) else ''
        print(f"   {st['stage']} 主责={st['skill']}")
        print(f"      预触发条件：{st['when']}")
        print(f"      就绪命令：{st['ready']}")
        print(f"      产出：{st['out']}{mark}")
    print('\n💡 站位规则（M-R67）：不要等任务「走到」才想起技能——上一站收口时下一站必须已就绪。')


def _recap_cloud():
    """读③台账/④经验库当日条目（惰性加载 link，失败显式报不折叠）。"""
    import time as _t2
    try:
        sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
        import link
        today = _t2.strftime('%Y-%m-%d')
        sess = [r for r in link.rows_of('TO4LwsjVVdR8JRAUlkejk3')
                if str(r.get('时间', '')).startswith(today)]
        exp = [r for r in link.rows_of('XTuuj8mKtqnZSolDdeDxkq')
               if str(r.get('时间', '')).startswith(today)]
        return sess, exp, None
    except Exception as e:
        return [], [], f'{type(e).__name__}: {e}'


def cmd_recap(rest):
    """recap：会话监工——本会话执行了啥动作？经验落池了吗？哪个技能该介入？（M-R67）"""
    import time as _t
    today = _t.strftime('%Y-%m-%d')
    acts = []
    if os.path.exists(JOURNAL_FP):
        for ln in open(JOURNAL_FP, encoding='utf-8'):
            try:
                r = json.loads(ln)
                if r.get('ts', '').startswith(today):
                    acts.append(r)
            except Exception:
                pass
    print('👁️  会话监工报告（M-R67）')
    print(f'\n【① 动作流水】今日 {len(acts)} 个动作：')
    from collections import Counter
    vc = Counter(a['verb'] for a in acts)
    for v, n in vc.most_common():
        bad = sum(1 for a in acts if a['verb'] == v and not a.get('ok'))
        print(f'   {v} ×{n}' + (f'（{bad} 异常）' if bad else ''))
    fail = [a for a in acts if not a.get('ok')]
    if fail:
        print(f'   ⚠️ 异常动作 {len(fail)} 个：' + '；'.join(f"{a['ts'][11:16]} {a['verb']}" for a in fail[:5]))
    sess, exp, err = _recap_cloud()
    if err:
        print(f'\n【② 经验落池】❌ 云端库读取失败（{err}）——请人工核查，勿当作"无经验"')
    else:
        print(f'\n【② 经验落池】③台账今日 {len(sess)} 条 ｜ ④经验库今日 {len(exp)} 条')
        if acts and not sess:
            print('   ❌ 缺口：有动作流水但台账当日无 S 条目——收口前必须落池（复盘闭环链）')
        elif acts and not exp:
            print('   ⚠️ 台账已记但④经验库当日无 L 条目——有踩坑吗？有则必须沉淀（规则57）')
        else:
            print('   ✅ 双池齐备')
    print('\n【③ 该谁介入】按流水线站位（tvbox conductor next 看全图）：')
    hints = ['动作含 ingest/入库类 → ⑤入库站：blacklist 闸+快照必须走完',
             '动作含 verify/scan 类 → ④质检站：遗留清单必须过目',
             '动作含 upgrade/规则类 → ⑦进化站：CHANGELOG+台账同步+对照规则57',
             '收口前 → ⑥留痕站：tvbox conductor recap（本命令）+ chain 复盘闭环']
    for h in hints:
        print(f'   · {h}')

# ---------- S115：增长治理审计（M-R69·规则/技能越多越要管） ----------
_GROWTH_LOG = '/root/.codebuddy/skills/ecosys-conductor/plans/rules_growth.json'

def _bigram_set(txt):
    t = re.sub(r'\s+', '', txt)
    return {t[i:i+2] for i in range(len(t)-1)} if len(t) > 1 else {t}

def _skill_size_limits():
    """读 M-R77 体积阈值（**唯一真源** = rules_registry.thresholds）。
    返回 (硬上限字节, 缓冲线字节)。读取失败时抛**显式错误**，不折叠成默认值——
    折叠会让阈值在注册表损坏时静默退回某个旧数字，等于规则悄悄失效（M-R60 三态）。
    历史：40KB 曾硬编码在 :3298/:3305 两处、规则册无记载；S135 入册为 M-R77 后统一从此读。"""
    reg = json.load(open(os.path.join(HERE, 'rules_registry.json'), encoding='utf-8'))
    th = reg.get('thresholds') or {}
    hard = th.get('skill_max_bytes')
    buf = th.get('skill_buffer_bytes')
    if not isinstance(hard, int) or not isinstance(buf, int):
        raise ValueError('rules_registry.thresholds 缺 skill_max_bytes/skill_buffer_bytes（M-R77）')
    return hard, buf


def cmd_rules_probe(rest):
    """rules-probe：真正**执行**规则注册表里的 probe.command（S176）。

    **为什么必须新增这条命令**：S176 之前，`probe.command` 从头到尾只是**声明字段**——
    `audit-growth` 只验它「非空 + 不是假 conductor 命令」，**没有任何代码会去跑它**。
    于是 M-R95 自己写的那句「无探针即无法验证落地，等同死规则」反过来打中了自己：
    122 条规则里 44 条登记了命令，一条都没被执行过。登记得再漂亮，没人跑就是文档。

    三种承载形态分开处置（不许一律当坏）：
      ① 已登记且可执行  → 真跑，rc≠0 即判红（这是规则抓到真问题，不是探针坏）
      ② 未登记（空载）  → 单列，不计入通过率分母之外（M-R60 三态：空 ≠ 通过 ≠ 失败）
      ③ 需参数不能裸跑  → 单列要求补示例参数（如 M-R84 的 <技能名> 占位符）

    用法：tvbox conductor rules-probe [--only M-R79] [--all]
    默认只跑 `rule_probe.py` 承载的（快、纯读、无副作用）；--all 跑全部已登记命令。
    """
    only = None
    run_all = '--all' in rest
    for i, a in enumerate(rest):
        if a == '--only' and i + 1 < len(rest):
            only = rest[i + 1]
    try:
        d = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        'rules_registry.json'), encoding='utf-8'))
    except Exception as e:
        print(f'❌ 注册表读取失败：{type(e).__name__}: {e}——无法判定（M-R60 三态，勿当通过）')
        return 1
    rs = d.get('rules', [])
    # 需参数不能裸跑的（占位符/子命令必需），登记在此，避免每轮重复踩
    NEEDS_ARG = {'M-R84', 'M-R96', 'M-R97'}
    # 已知重命令：默认跳过（--all 才跑）。S176 实测：**无参** `signal-check` 会跑全部
    # 45 个源（约 2 分钟）并写 signal_proven.json 留痕，规则探针不该顺带触发它；
    # 带 `--source N` 的是单源，很轻，照跑。
    HEAVY = ('verify', 'assess', 'watchdog', ' close', 'harvest')
    rows = []
    for r in rs:
        rid = r.get('id')
        if only and rid != only:
            continue
        cmd = str(((r.get('probe') or {}).get('command')) or '').strip()
        if not cmd:
            rows.append((rid, 'EMPTY', ''))
            continue
        if rid in NEEDS_ARG:
            rows.append((rid, 'NEEDARG', cmd))
            continue
        # 只跳重命令（verify/assess/watchdog 这类数分钟级、且会二次触发全量）；
        # 其余一律真跑——**"跳过"太多等于没装执行器**，S176 首跑实测：原写法把
        # 44 条里 38 条全归成 SKIP-NONLOCAL，实际跑的只有自家 6 条，等于自证自。
        if not run_all and any(h in cmd for h in HEAVY):
            rows.append((rid, 'SKIP-HEAVY', cmd))
            continue
        # 无参 signal-check = 全量 45 源（约 2 分钟 + 写留痕），同样归重命令
        if not run_all and 'signal-check' in cmd and '--source' not in cmd:
            rows.append((rid, 'SKIP-HEAVY', cmd))
            continue
        try:
            p = subprocess.run(cmd, shell=True, cwd=os.path.dirname(os.path.abspath(__file__)),
                               capture_output=True, text=True, timeout=300)
            rc = p.returncode
            tail = (p.stdout or p.stderr or '').strip().splitlines()
            rows.append((rid, f'rc={rc}', tail[-1][:90] if tail else ''))
        except subprocess.TimeoutExpired:
            rows.append((rid, 'TIMEOUT', ''))
        except Exception as e:
            rows.append((rid, f'ERR {type(e).__name__}', str(e)[:60]))
    ran = [x for x in rows if x[1].startswith('rc=')]
    passed = [x for x in ran if x[1] == 'rc=0']
    failed = [x for x in ran if x[1] != 'rc=0']
    empty = [x for x in rows if x[1] == 'EMPTY']
    need = [x for x in rows if x[1] == 'NEEDARG']
    print(f'🔬 规则探针执行（S176）· 实跑 {len(ran)} 条')
    print()
    for rid, st, note in rows:
        mark = {'rc=0': '✅'}.get(st, '❌' if st.startswith(('rc=', 'ERR', 'TIME')) else '⏭')
        if st in ('EMPTY', 'NEEDARG', 'SKIP-HEAVY', 'SKIP-NONLOCAL'):
            mark = '⏭'
        print(f'  {mark} {rid:8s} {st:16s} {note}')
    print()
    print(f'📊 通过 {len(passed)} / 实跑 {len(ran)}；'
          f'未通过 {len(failed)}；空载 {len(empty)}；需参数 {len(need)}')
    if failed:
        print(f'   ❌ 未通过（rc≠0 是**真实结果**，规则抓到了问题，不是探针坏）：'
              f'{", ".join(x[0] for x in failed)}')
    if empty:
        print(f'   ⏭ 空载 {len(empty)} 条：{", ".join(x[0] for x in empty[:12])}'
              f'{" …" if len(empty) > 12 else ""}')
    # 判红条件：实跑的里面只要有一条 rc≠0
    return 1 if failed else 0


def cmd_audit_growth(rest):
    """audit-growth：规则/技能增长治理——分类健康度+去重候选+重复触发风险（M-R69）。
    体积面按 M-R77：超标（>硬上限）即 **exit=1 + 自动开问题单**；踩线（>缓冲线）只告警。
    定位：随任务推进规则/技能只增不减=熵增；本命令是增量盘点器，监管者=conductor。"""
    print('⚖️  增长治理审计（M-R69：增长必须被管理，精简即美德）')
    over_hard = []      # 超硬上限 → 阻断
    over_buf = []       # 超缓冲线未超硬限 → 提醒
    hard, buf = 40960, 35840
    try:
        hard, buf = _skill_size_limits()
    except Exception as e:
        print(f'   ⚠️ 阈值读取失败（{e}）——按 M-R77 内置值继续，但须尽快修复注册表')
    # --- 规则面 ---
    try:
        reg = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rules_registry.json'), encoding='utf-8'))
        rs = reg['rules']
        ch = collections.Counter(r.get('chapter', '?') for r in rs)
        dups = [r['id'] for r in rs if r.get('dedup_decision') == 'keep_in_skill']
        orph = [r['id'] for r in rs if r.get('status') == 'orphan']
        print(f'\n【① 规则面】总数 {len(rs)} ｜ 孤岛 {len(orph)} ｜ 去重候选 {len(dups)}')
        print('   章节分布：' + ' ｜ '.join(f'{k.replace("§","")}:{v}' for k, v in ch.most_common()))
        mp = '/workspace/生态1015_TVBox生态全局规则.md'
        if os.path.exists(mp):
            sz = os.path.getsize(mp)
            tag = ' ⚠️ 超硬上限' if sz > hard else (' ⚠️ 超缓冲线' if sz > buf else ' ✅')
            print(f'   总册体积 {sz/1024:.1f} KB（M-R77：硬上限 {hard/1024:.0f}KB / 缓冲线 {buf/1024:.0f}KB）' + tag)
            (over_hard if sz > hard else over_buf if sz > buf else []).append(f'规则总册 {sz/1024:.1f}KB')
        # S121 扩面一：技能正本体积——M-R77 阈值须对每个 SKILL.md 生效（此前只查总册=监管盲区实锤）
        near = []      # S135 补：贴近缓冲线（余量 < 1KB）——只报"全部 ✅"会掩盖"下一句就超线"
        for s in ECO_SKILLS:
            skp = os.path.join(SKILL_ROOT, s, 'SKILL.md')
            if os.path.exists(skp):
                b = os.path.getsize(skp)
                if b > hard:
                    over_hard.append(f'{s} {b/1024:.1f}KB')
                elif b > buf:
                    over_buf.append(f'{s} {b/1024:.1f}KB')
                elif buf - b < 1024:
                    near.append(f'{s} 余量 {(buf-b)/1024:.1f}KB')
        if over_hard:
            print(f'   ❌ 技能正本体积超硬上限 {hard/1024:.0f}KB：{len(over_hard)} 个——{"、".join(over_hard)}')
        if over_buf:
            print(f'   ⚠️ 已超缓冲线 {buf/1024:.0f}KB（仍有硬上限余量，但下次加内容即触线）：{"、".join(over_buf)}')
            print(f'      处理：tvbox conductor slim <技能名> --diagnose 看可外置节 → --apply 外置并留索引')
        if not over_hard and not over_buf:
            print(f'   技能正本体积：全部 ≤{buf/1024:.0f}KB（缓冲线内）✅')
        if near:
            print(f'   🔔 贴近缓冲线（余量 <1KB，下次加内容即触线）：{"、".join(near)}')
            print('      不必现在瘦身，但**不要再往里加**；新增内容优先写 scripts/ 或 docs/')
        # S121 扩面二：规则承载真实性——probe.command 空载=名义承载（凡无工具承载的规则=没立）
        # S136 校准：区分**两种**承载形态，不要一律当"没立"。
        #   A 工具承载型：有可执行命令可按（probe.command）——空载即名义承载，必须补。
        #   B 设计原则型：条文本身是**检查器设计约束**（"假设字段名取值会 100% 虚报"
        #     这类），它没有、也不该有独立命令；它的承载是 signal-check 的**探针逻辑**
        #     与 landing 里的技能文档锚点。对 B 要求 probe.command，只会逼人编一条假命令
        #     ——那是把"名义承载"从"空着"变成"编造"，比空着更坏。
        #   判据：landing 存在且**全部**是文档锚点（type in verbatim/reference/anchor），
        #         且规则未被任何 CHECKERS/signal 逻辑引用 → 归 B。
        #   B 类不计入"空载"，但强制登记 `承载形态: 设计原则`，且 audit 会列出 B 类清单供复审
        #   （防止 B 类变成"什么都往里塞"的垃圾桶）。
        def _is_design_principle(r):
            # 有实命令的**一定**是工具承载型——先判这条，否则会把已承载的规则
            # 误归入"设计原则型"从而掩盖真实缺口（S136 实测：M-R70/M-R71 明明有
            # `conductor audit-compliance` 却因 landing 是文档锚点被归进设计原则，
            # 一眼看去"78 条都没命令"，其实是分类错了）。
            if str(((r.get('probe') or {}).get('command')) or '').strip():
                return False
            land = r.get('landing')
            if isinstance(land, str):
                try:
                    land = json.loads(land)
                except Exception:
                    return False
            if not isinstance(land, list) or not land:
                return False
            return all(str((x or {}).get('type') or '') in ('verbatim', 'reference', 'anchor')
                       for x in land if isinstance(x, dict))

        tool_rules = [r for r in rs if not _is_design_principle(r)]
        design_rules = [r for r in rs if _is_design_principle(r)]
        empty_probe = [r['id'] for r in tool_rules
                       if not str(((r.get('probe') or {}).get('command')) or '').strip()]
        bad_cmd = [r['id'] for r in rs
                   if str(((r.get('probe') or {}).get('command')) or '').startswith('conductor')
                   and len(str(r['probe']['command']).split()) > 1
                   and str(r['probe']['command']).split()[1] not in DISPATCH]
        rate = (len(empty_probe) / len(tool_rules) * 100) if tool_rules else 0
        if empty_probe:
            print(f'   ⚠️ 规则承载面：{len(empty_probe)}/{len(tool_rules)} 条**工具承载型**规则 probe.command 空载'
                  f'（名义承载率 {rate:.0f}%）——须逐条补真实命令或挂 audit 系承载')
            print(f'      空载清单（前 10）：{", ".join(empty_probe[:10])}{" …" if len(empty_probe) > 10 else ""}')
        else:
            print(f'   规则承载面：工具承载型 {len(tool_rules)} 条全部有真实命令 ✅')
        if design_rules:
            print(f'   设计原则型 {len(design_rules)} 条（{design_rules[0]["id"]}~{design_rules[-1]["id"]}）：'
                  f'承载=文档锚点+探针逻辑，无独立命令（S136 口径校准）')
        if bad_cmd:
            print(f'   ❌ probe 指向不存在的 conductor 命令：{bad_cmd}')
        prev = None
        if os.path.exists(_GROWTH_LOG):
            try:
                prev = json.load(open(_GROWTH_LOG, encoding='utf-8'))
            except Exception:
                prev = None
        delta = len(rs) - prev['rules'] if prev and 'rules' in prev else None
        if delta is not None and delta > 0:
            print(f'   📈 较上期 +{delta} 条' + ('（单期 +5 以上建议跑 dedup/盘点）' if delta >= 5 else ''))
        # ★ S158：原写 `time.strftime(...)` 但本文件**从未 import time** —— 该行一直抛
        #   NameError，又被下面的 `except Exception: pass` 吞掉，于是 `_GROWTH_LOG`
        #   从来没被写过一条（"规则面增长趋势"这条度量实际是空的）。
        #   改用已导入的 datetime，并把静默 pass 改成显式告警：
        #   写日志失败不阻断主流程（它不是判据），但**必须让人看见**，否则下次还是没人知道。
        try:
            _ = json.dump({'date': datetime.date.today().isoformat(), 'rules': len(rs)},
                          open(_GROWTH_LOG, 'w', encoding='utf-8'))
        except Exception as _ge:
            print(f'  ⚠️ 规则增长日志写入失败（{type(_ge).__name__}: {_ge}）——本期趋势无法留痕')
    except Exception as e:
        print(f'【① 规则面】❌ 读取失败（{type(e).__name__}: {e}）——无法判定（M-R60）')
    # --- 技能/触发面：重复触发风险 ---
    print('\n【② 触发面·重复触发风险】（两技能话术重叠=用户一句话可能双触发/错触发）')
    risks = 0
    # 用**派生视图**（与生产路由同一份），不用 _SKILL_INTENTS 原表（S136 五续：口径不得分叉）
    _iv = _intents_view()
    for i in range(len(_iv)):
        for j in range(i+1, len(_iv)):
            a, ta = _iv[i]; b, tb = _iv[j]
            # 判重叠必须用**完整词项**，不能用 bigram（S136 五续·第三次踩坑纠正）：
            # `_bigram_set` 会把 `json` 切成 js/on/so/量j，把 `py线` 切成 py/y线——
            # 于是 probe-hub 与 tvbox-interface 报「重叠 12」，逐条看全是碎片，
            # **真实完整词项只有 2 个（直链 / 轻量json）**。用 bigram 判重叠=把字面巧合当业务重叠。
            sa = {w for w in ta.split() if len(w) >= 2}
            sb = {w for w in tb.split() if len(w) >= 2}
            ov = sa & sb
            if len(ov) >= 2:
                risks += 1
                words = ' '.join(sorted(ov)[:8])
                # 共享完整词是否**真会造成错路由**：逐个词反查，看落点是否为这两家之一且稳定。
                # 共享词本就该两边都命中（"直链"对两家都是真业务），只要不落到第三家就不算错。
                _bad = [w for w in sorted(ov) if _route_top1(w) not in (a, b, None)]
                # **抢词检测**（S136 五续）：共享词如果本不该共享，就是越界。
                # 判据：该词若出现在某技能的**专属区**（如 manifest 意图 / SKILL 正文的职责段），
                # 而另一方原本没有——那它就是被抄过去的。这里用更稳的口径：
                # 该词路由到 a 还是 b？若两边都能命中但 a 的全话术里该词是**孤词**（只此一词
                # 支撑），而 b 有一整族同源词（如 底包/入库/去重/拆分 同属接口流水线），
                # 则 a 是抢词的一方。简化为：a 侧该词的同族词 < b 侧同族词 → 判 a 抢词。
                _grab = []
                for w in sorted(ov):
                    _fama = sum(1 for x in sa if w in x or x in w)
                    _famb = sum(1 for x in sb if w in x or x in w)
                    if _fama < _famb and _fama <= 1:
                        _grab.append(w)
                if _bad or _grab:
                    _ex = (_bad + _grab)[:3]
                    print(f'   ⚠️ {a} ↔ {b}：共享词 {len(ov)} 个（{words}）'
                          f'——**越界/错路由**：{" ".join(_ex)}'
                          f'{"（错路由）" if _bad else "（抢词：同族词一边多一边孤立）"}')
                else:
                    print(f'   ℹ️ {a} ↔ {b}：共享完整词 {len(ov)} 个（{words}）'
                          f'——均为两家真业务术语，无错路由')
    if risks == 0:
        print('   ✅ 无高重叠技能对——话术边界清晰')
    # manifest 重复意图文本
    try:
        mani = json.load(open(MANIFEST, encoding='utf-8'))
        seen = {}
        for c in mani.get('commands', []):
            key = c.get('intent', '').strip()
            if not key:
                continue
            seen.setdefault(key, []).append(c.get('skill', '?'))
        dupi = {k: v for k, v in seen.items() if len(v) > 1}
        if dupi:
            print(f'   ⚠️ manifest 重复意图 {len(dupi)} 条：')
            for k, v in list(dupi.items())[:5]:
                print(f'      「{k[:30]}」×{v}')
        else:
            print('   ✅ manifest 意图无重复文本')
    except Exception as e:
        print(f'   （manifest 读取失败：{e}）')
    # --- 建议 ---
    print('\n【③ 治理工具箱】（精简即美德，增长后必盘点）')
    print('   · 规则去重/归并：conductor rules --dup / dedup_master.py（S109）')
    print('   · 技能/总册瘦身：conductor slim <技能名|总册> --diagnose ｜ --apply（S135 补，M-R77 三法）')
    print('   · 技能重叠：conductor lanes（职责矩阵）/ evolve review（周期复盘）')
    print('   · 话术精简：修 _SKILL_INTENTS 前先跑本命令看重叠基线')

    # --- 阻断（M-R77④：超标不是提示，是失败）---
    if over_hard:
        print(f'\n❌ M-R77 未通过：{len(over_hard)} 项超硬上限 {hard/1024:.0f}KB')
        try:
            _file_size_issue(over_hard, hard, buf)
        except Exception as e:
            print(f'   ⚠️ 自动建单失败（{type(e).__name__}: {e}）——请手工登记到 issues_ledger.json')
        raise SystemExit(1)
    if over_buf:
        print(f'\n⚠️ M-R77 缓冲线告警：{len(over_buf)} 项已超 {buf/1024:.0f}KB（未超硬上限，不阻断）')
        print('   建议本轮顺手瘦身，避免下一轮加内容即触线')


def _file_size_issue(items, hard, buf):
    """超标自动开问题单（M-R77④）：不建单=没人负责=下一轮还在。
    幂等：同编号已 open 则不重复建，只更新描述。"""
    fn = os.path.join(os.path.dirname(HERE), 'plans', 'issues_ledger.json')
    db = json.load(open(fn, encoding='utf-8'))
    issues = db.get('issues', [])
    n = 1
    while any(i['id'] == f'P{n:03d}' for i in issues):
        n += 1
    pid = f'P{n:03d}'
    title = f'技能/总册体积超 M-R77 硬上限：{len(items)} 项（{"、".join(items[:3])}）'
    existing = [i for i in issues if i.get('status') in ('open', 'next') and 'M-R77' in str(i.get('title', ''))]
    if existing:
        existing[0]['title'] = title
        print(f'   📋 已更新既有单 {existing[0]["id"]}（不重复建单）')
    else:
        issues.append({'id': pid, 'title': title, 'from': 'S135 audit-growth',
                       'status': 'open', 'opened': datetime.date.today().isoformat(),
                       'owner': 'ecosys-conductor',
                       'next': f'逐项瘦身：conductor slim <技能名> --diagnose 看可外置节，--apply 外置并留索引；'
                               f'目标 ≤{buf/1024:.0f}KB（缓冲线，非 {hard/1024:.0f}KB 硬上限）'})
        db['issues'] = issues
        open(fn, 'w', encoding='utf-8').write(json.dumps(db, ensure_ascii=False, indent=1))
        print(f'   📋 已开问题单 {pid}（owner=ecosys-conductor）——不建单=没人负责=下轮还在')



# ---------- S124：白做账本（把"投入错配/返工/重踩"变成常设度量） ----------
def cmd_waste(rest):
    """waste：白做账本——量化投入错配（治理:业务）、返工率、经验闭环命中。依据 S124 第五层复盘。"""
    import collections
    print('🧾  白做账本（S124：好多白做，这不科学——先量化，再归零）')
    # ① 投入错配：台账触发类型占比
    try:
        sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
        import link
        led = link.rows_of('TO4LwsjVVdR8JRAUlkejk3')
        tc = collections.Counter(r.get('触发类型', '?') for r in led)
        n = len(led) or 1
        # ★ S159：原先这里硬编码 ('新接口处理','整包入库','扫描归源库')，与 brake.py 的
        #   BIZ_KINDS 是**同一事实的第二份定义**（M-R38）。brake 后加了「出库交付/补源/
        #   失效复测」三项，本处不知情 → waste 与 brake 对同一批台账算出不同占比。
        #   改为从 brake 派生，代码中不再保留第二份。
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import brake as _brake
        biz = sum(v for k, v in tc.items() if k in _brake.BIZ_KINDS)
        gov = n - biz
        print(f'\n【① 投入错配】台账 {n} 会话：业务 {biz}（{biz/n:.0%}） vs 治理 {gov}（{gov/n:.0%}）')
        print('   ' + '  '.join(f'{k}:{v}' for k, v in tc.most_common()))
        if gov / n > _brake.RATIO_WARN:
            print(f'   ⚠️ 治理占比 >{_brake.RATIO_WARN:.0%}——治理自吞产出（D0 基线目标：治理:业务 → 1:2）')
        # ② 返工率：踩坑字段非空且含返工语汇
        import re as _re
        rw = [r for r in led if _re.search(r'返工|纠错|假绿|假成功|漂移|空载|漏接|重做',
              ' '.join(str(r.get(k, '')) for k in ('任务摘要', '踩坑', '解法')))]
        print(f'\n【② 返工率】疑似返工/纠错类会话 {len(rw)}/{n} = {len(rw)/n:.0%}（D0 基线 25%，目标 <15%）')
    except Exception as e:
        print(f'\n【①② 台账读取失败：{type(e).__name__}: {e}】（M-R60：显式报错，勿折叠为无数据）')
    # ③ 执行层健康：journal 失败率
    try:
        rows = [json.loads(l) for l in open(JOURNAL_FP, encoding='utf-8') if l.strip()]
        fails = sum(1 for r in rows if not r.get('ok', True))
        print(f'\n【③ 执行层】journal {len(rows)} 动作，失败 {fails}（{fails/max(1,len(rows)):.0%}）——白做不在执行层，在决策/复用层')
    except Exception as e:
        print(f'\n【③ journal 读取失败：{e}】')
    # ④ 经验闭环命中：本次会话预载命中数（由 ask 时打印）；此处给自检方法
    print('\n【④ 经验闭环】命中数以 ask 预载输出为准（S123 bigram 化后 10 意图命中 20 条≈2.0 条/意图；验收判据 ≥1 条/意图）')
    print('\n💡 口径：白做 = 投入未转化为可复用承载。三个拦截器：经验预载（防重踩）→ probe 实测承载（防空转）→ 本账本（防错配）。')
    # ⑤ 治理刹车（S138·M-R94/M-R95）：量化之后必须有动作。
    # 只报「占比 90%」而不拦，等于每年体检报告写「你该减肥」然后没有然后——
    # S124 复盘提了 B1/B2/B3、末句请用户拍板，结果**刹车没装、治理又跑 9 轮**。
    # 本段直接调 brake，把「诊断」升级为「阻断」。
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import brake
        st, msgs = brake.check()
        icon = {'green': '✅', 'yellow': '🟡', 'red': '🔴', 'unknown': '⚠️'}
        print('\n【⑤ 治理刹车】' + ('🔴 熔断：下个动作应是业务事' if st == 'red' else
                                 ('🟡 建议夹一件业务事' if st == 'yellow' else '✅ 未触发')))
        for _s, _m in msgs:
            print(f'   {icon[_s]} {_m}')
    except Exception as e:
        print(f'\n【⑤ 治理刹车计算失败：{type(e).__name__}: {e}】（M-R60：显式报错，勿折叠为正常）')


# ---------- S125：三层联动备份（生态库→资料库→云端，产出即沉淀/沉淀即备份/备份可验证） ----------
BACKUP_STATE_FP = os.path.join(os.path.dirname(PLAN_FILE), 'backup_state.json')
BACKUP_SPACE = '8w5cnqCqQJ0wNoF2Hb6efH'
BACKUP_ROOT = 'zEYBoLq5G58Rdw7g3ADBnn'   # 🗄 生态备份区
# ★ S158 护栏：备份夹必须落在备份区，**不许落在收件箱**。
#   事故（S158 实测，G10）：`folder_id_by_day` 里记录的 20261017/20261018 两个备份夹，
#   `parentId` 实测都是 `tBlgSL4SPEFMrsyTBTg03c`（🗑 待删收件箱），而 🗄 生态备份区 是**空的**。
#   成因链：备份夹若已存在（或建夹返回的 id 被误用），后续 `folder_id` 就直接复用，
#   只要有一次 id 记到了收件箱里的同名夹上，之后每天都会把附件往收件箱里传。
#   而收件箱的语义是"待删"——**备份传进了待删箱**，等于每天自动生成一批"该删的备份"。
#   故每次备份前校验父节点，不对就当场纠正（move-node 回去），并显式告警。
BACKUP_INBOX = 'tBlgSL4SPEFMrsyTBTg03c'  # 🗑 待删收件箱（禁作备份父）


def _collect_backup_items(stamp):
    """收集层1（生态库本地）关键资产 → (本地打包目录, 清单)。"""
    import tarfile, glob as _glob
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    work = f'/tmp/eco_backup_{stamp}'
    os.makedirs(work, exist_ok=True)
    items = []
    # ① 治理四件套+核心脚本 → tar.gz
    tar_fp = os.path.join(work, f'governance_core_{stamp}.tar.gz')
    with tarfile.open(tar_fp, 'w:gz') as tf:
        for rel in ('SKILL.md', 'CHANGELOG.md', 'scripts/flow.json',
                    'scripts/rules_registry.json', 'scripts/conductor.py', 'scripts/rules_cmd.py'):
            p = os.path.join(root, rel)
            if os.path.exists(p):
                tf.add(p, arcname=rel)
    items.append(tar_fp)
    # ② /workspace 报告与总册
    # ★ S158 修（G3 根因）：原来只收 `复盘*/S1*/生态*` 三种前缀的 `.md`。
    #   **前缀是手写白名单，而报告的前缀是长出来的** —— 于是每一类新报告（如本轮的
    #   `S158_*`、`账房司1018_*`、`过程_*`、`闭环*`、`说明_*`）都自动落在备份之外，
    #   而没有任何地方会因此报错：备份永远"成功"，只是内容越来越不完整。
    #   这就是典型的「白名单过滤器」缺陷：**它筛掉的东西不会喊疼**（M-R60 的近亲）。
    #   现改为**全量收 /workspace 下的 .md**（这是"产出即沉淀"的字面含义），
    #   白名单只保留给"必须排除"的少数（.bak 快照、临时文件）。
    _EXCL = ('.bak', '~', '.tmp', '.log')
    _ws = '/workspace'
    if os.path.isdir(_ws):
        for fn in sorted(os.listdir(_ws)):
            p = os.path.join(_ws, fn)
            if not os.path.isfile(p) or not fn.lower().endswith('.md'):
                continue
            if any(x in fn for x in _EXCL):
                continue
            items.append(p)
    return work, sorted(set(items))


def _sha16(p):
    import hashlib
    return hashlib.sha256(open(p, 'rb').read()).hexdigest()[:16]


def cmd_backup(rest):
    """backup：三层联动闭环——层1生态库(本地产出)→层2资料库(沉淀)→层3云端备份区(后盾)。
    流程：收集资产→manifest(sha256)→建当日子夹→附件上传→与上次对账(新增/变更/缺失)。--dry 只打包不上传。"""
    import subprocess as _sp, time as _t, json as _json, hashlib as _hl
    dry = '--dry' in (rest or '')
    stamp = _t.strftime('%Y%m%d')
    print(f'🗄  三层联动备份（S125：生态库→资料库→云端）{" [DRY]" if dry else ""}')
    # ① 收集
    work, items = _collect_backup_items(stamp)
    if not items:
        print('   ❌ 无可备份资产'); return
    # ② manifest（对账正本）
    manifest = {'generated': _t.strftime('%Y-%m-%dT%H:%M:%S'), 'session': 'auto',
                'layer': 'ecosys->library->cloud',
                'items': [{'file': os.path.basename(p), 'bytes': os.path.getsize(p), 'sha256': _sha16(p)} for p in items]}
    items += _backup_db_exports(work)
    mf_fp = os.path.join(work, 'manifest.json')
    open(mf_fp, 'w', encoding='utf-8').write(_json.dumps(manifest, ensure_ascii=False, indent=1))
    upload = items + [mf_fp]
    print(f'   ① 收集 {len(upload)} 件（含 manifest），{sum(i["bytes"] for i in manifest["items"])//1024}KB')
    # 对账：与上次
    prev = {}
    try:
        prev = {i['file']: i['sha256'] for i in _json.load(open(BACKUP_STATE_FP, encoding='utf-8')).get('items', [])}
    except Exception:
        print('   · 首次备份（无上次基线）')
    cur = {i['file']: i['sha256'] for i in manifest['items']}
    added = [f for f in cur if f not in prev]
    changed = [f for f in cur if f in prev and cur[f] != prev[f]]
    missing = [f for f in prev if f not in cur]
    print(f'   ② 对账 vs 上次：新增 {len(added)}｜变更 {len(changed)}{"（"+",".join(changed[:3])+"…）" if changed else ""}｜缺失 {len(missing)}')
    if dry:
        print(f'   [DRY] 打包于 {work}，未上传'); return
    # ③ 建当日子夹（幂等：state 已记录则复用）
    st = {}
    try: st = _json.load(open(BACKUP_STATE_FP, encoding='utf-8'))
    except Exception: pass
    folder_id = st.get('folder_id_by_day', {}).get(stamp)
    if not folder_id:
        r = _sp.run([sys.executable, '/root/.codebuddy/skills/library/manage/create_folder.py',
                     '--title', f'{stamp}_生态备份', '--space-id', BACKUP_SPACE, '--parent-id', BACKUP_ROOT],
                    capture_output=True, text=True)
        import re as _re
        m = _re.search(r'KS_FOLDER_CREATE\t(\S+)', r.stdout)
        if not m:
            print(f'   ❌ 建夹失败：{r.stdout[-120:]}{r.stderr[-120:]}'); return
        folder_id = m.group(1)
        st.setdefault('folder_id_by_day', {})[stamp] = folder_id
    # ★ S158 护栏：校验备份夹父节点，**绝不许是收件箱**（详见 BACKUP_INBOX 注释）。
    #   判据取服务端真实 parentId，不信任本地 state —— state 记的是"我以为它建在哪"。
    _probe = _sp.run([sys.executable, '/root/.codebuddy/skills/library/space_api.py',
                      'space.workspace.node-info', '--node-id', folder_id],
                     capture_output=True, text=True)
    _pid = None
    try:
        import json as _j
        for _ln in (_probe.stdout or '').splitlines():
            if _ln.strip().startswith('{'):
                _pid = ((_j.loads(_ln).get('data') or {}).get('node') or {}).get('parentId')
                break
    except Exception:
        _pid = None
    if _pid is None:
        print(f'   ⚠️ 备份夹 {folder_id} 父节点读取失败（**无法判定落点**）——'
              f'本次继续上传但请人工核对是否在「🗄 生态备份区」')
    elif _pid == BACKUP_INBOX:
        print(f'   🛑 备份夹 {folder_id} 的父节点是**收件箱**（{BACKUP_INBOX}）——'
              f'备份传进待删箱等于每天自动生成一批"该删的备份"')
        _mv = _sp.run([sys.executable, '/root/.codebuddy/skills/library/space_api.py',
                       'space.workspace.move-node', '--node-id', folder_id,
                       '--target-parent-id', BACKUP_ROOT],
                      capture_output=True, text=True)
        _ok_mv = any(_ln.strip().startswith('{') and '"error"' not in _ln
                     for _ln in (_mv.stdout or '').splitlines())
        if _ok_mv:
            print(f'   ✅ 已改投「🗄 生态备份区」——本次备份将落在正确位置')
        else:
            print(f'   ❌ 改投失败（{(_mv.stdout or "")[-120:]}）——**本次上传已中止**，'
                  f'否则会把备份继续灌进待删箱')
            return
    elif _pid != BACKUP_ROOT:
        print(f'   ⚠️ 备份夹 {folder_id} 父节点是 {_pid}，既不是备份区也不是收件箱——请人工确认')
    # ④ 逐件上传（当日已传跳过——幂等，M-R24）
    done = set(st.get('uploaded_by_day', {}).get(stamp, []))
    ok = fail = skip = 0
    done_now = []
    for p in upload:
        nm = os.path.basename(p)
        if nm in done:
            skip += 1
            continue
        r = _sp.run([sys.executable, '/root/.codebuddy/skills/library/attachment/attachment.py', 'upload',
                     '--path', p, '--node-id', folder_id, '--file-name', nm],
                    capture_output=True, text=True)
        if '已作为附件上传完成' in r.stdout:
            ok += 1
            done_now.append(nm)
        else:
            fail += 1
            print(f'   ⚠️ 上传失败：{nm}（{r.stdout[-60:]}{r.stderr[-60:]}）')
    if done_now:
        st.setdefault('uploaded_by_day', {}).setdefault(stamp, []).extend(done_now)
    print(f'   ③ 上传：新传 {ok}｜跳过(当日已传) {skip}｜失败 {fail} → 节点 {folder_id}')
    # ⑤ 状态落地（merge：只更新当日与清单）
    st['last'] = {'date': stamp, 'items': manifest['items'], 'folder_id': folder_id, 'ok': ok, 'fail': fail}
    open(BACKUP_STATE_FP, 'w', encoding='utf-8').write(_json.dumps(st, ensure_ascii=False, indent=1))
    print(f'✅ 三层闭环：本地 {len(upload)} 件 → 备份区当日夹（可恢复=manifest sha256 校验）'
          + ('｜⚠️ 有失败件须重试' if fail else '｜全量落地'))




# ---------- S125b：资料库→云端（数据面备份）+ 云端→生态库（恢复通道）+ 全系对账 ----------
LINK_DBS = {
    'DB_SESS_台账': 'TO4LwsjVVdR8JRAUlkejk3',
    'DB_LIB_经验库': 'XTuuj8mKtqnZSolDdeDxkq',
    'DB_ECO_生态登记': 'T4kODtANe7rSrtZTu4sIND',
    # **S156 修：这里还指着旧空库 `92dSuG6uUbxitGuyx4Saaf`（S150 搬库漏掉的第 5 处）。**
    # 后果比"显示 0 行"严重：`_backup_db_exports` 用它做**数据面导出**，
    # 于是 `conductor backup` 备份出去的 DB_DEAD 是 0 行——
    # **备份号称"不丢失后盾"，而 225 条失效源数据其实一直没进过备份。**
    # 教训：改库 ID 必须全生态 grep 一遍，改三处漏两处 = 联动面悄悄断在你看不见的地方。
    'DB_DEAD_失效源库': 'hGwE41OpJDOR8CW0sLUwRx',
}


def _backup_db_exports(work):
    """层2→层3：资料库关键数据导出 JSON 进备份包。返回追加文件清单。"""
    extra = []
    sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
    try:
        import link
    except Exception as e:
        print(f'   ⚠️ link 导入失败，本轮无数据面备份（{e}）')
        return extra
    for name, db in LINK_DBS.items():
        try:
            rows = link.rows_of(db)
            fp = os.path.join(work, f'data_{name}_{len(rows)}rows.json')
            open(fp, 'w', encoding='utf-8').write(json.dumps(rows, ensure_ascii=False, indent=1))
            extra.append(fp)
        except Exception as e:
            print(f'   ⚠️ {name} 导出失败：{type(e).__name__}: {str(e)[:60]}（M-R60 显式报）')
    return extra


def cmd_restore(rest):
    """restore：云端→生态库恢复通道。从最近备份校验 manifest sha256 → --apply 才真覆盖治理四件套。
    用法：restore（校验+预览）｜restore --apply（真恢复，覆盖前先做安全备份到 /tmp/restore_pre_<ts>）"""
    import time as _t, json as _json, tarfile as _tf, shutil as _sh
    apply_ = '--apply' in (rest or '')
    print('♻️  云端→生态库 恢复通道（S125b：备份可验证才算闭环）')
    try:
        st = json.load(open(BACKUP_STATE_FP, encoding='utf-8'))
        last = st['last']
    except Exception as e:
        print(f'   ❌ 无备份状态可恢复（{e}）——先跑 backup'); return
    print(f"   最近备份：{last['date']} 节点 {last['folder_id']}｜{len(last['items'])} 件")
    # ① 本地打包副本校验（/tmp/eco_backup_<date>）
    work = f'/tmp/eco_backup_{last["date"]}'
    ok = bad = miss = 0
    for it in last['items']:
        p = os.path.join(work, it['file'])
        if not os.path.exists(p):
            miss += 1
            continue
        if _sha16(p) == it['sha256']:
            ok += 1
        else:
            bad += 1
            print(f'   ❌ sha256 不符：{it["file"]}')
    # 本地缺失/损坏 → 从云端节点拉回（/tmp 易失的活教训：云端才是后盾）
    if miss or bad:
        import subprocess as _sp2
        rec = 0
        for it in last['items']:
            p = os.path.join(work, it['file'])
            if os.path.exists(p) and _sha16(p) == it['sha256']:
                continue
            r = _sp2.run([sys.executable, '/root/.codebuddy/skills/library/attachment/attachment.py', 'download',
                          '--node-id', last['folder_id'], '--file-name', it['file'], '--path', p],
                         capture_output=True, text=True)
            if os.path.exists(p) and _sha16(p) == it['sha256']:
                rec += 1
            else:
                print(f'   ⚠️ 云端拉回失败/仍不符：{it["file"]}')
        ok += rec
        miss = max(0, miss - rec)
        print(f'   ↳ 从云端拉回 {rec} 件补齐本地副本')
    print(f'   ① 校验：{ok}/{len(last["items"])} 通过｜{bad} 损坏｜{miss} 缺失（已尝试云端拉回）')
    tar_fp = os.path.join(work, [i['file'] for i in last['items'] if i['file'].startswith('governance_core')][0]) if any(i['file'].startswith('governance_core') for i in last['items']) else None
    if not tar_fp or not os.path.exists(tar_fp):
        print('   ⚠️ 治理包不在本地副本——恢复须先从云端下载附件'); return
    # ② 预览/应用
    names = _tf.open(tar_fp).getnames()
    print(f'   ② 治理包含 {len(names)} 文件：{", ".join(names[:4])}…')
    if not apply_:
        print('   [DRY] 加 --apply 真恢复（当前技能文件将被备份前快照保护）'); return
    skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pre = f'/tmp/restore_pre_{_t.strftime("%H%M%S")}'
    _sh.copytree(skill_root, pre)
    with _tf.open(tar_fp) as tf:
        tf.extractall(skill_root)
    print(f'   ③ 已恢复 {len(names)} 文件 → {skill_root}｜恢复前快照：{pre}')
    print('✅ 恢复完成——请跑 conductor verify 确认体系健康')


def cmd_linkmap(rest):
    """linkmap：三库全系联动对账（生态库/资料库/云端库 两两+整体）——一屏看全联动健康。"""
    import time as _t
    print('🗺  三库全系联动对账（S125b：生态库(本地·产出) ↔ 资料库(沉淀) ↔ 云端库(后盾)）')
    # 层1 生态库
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    n_rep = len([f for f in os.listdir('/workspace') if f.endswith('.md')]) if os.path.isdir('/workspace') else 0
    n_rules = 0
    try:
        n_rules = len(json.load(open(os.path.join(root, 'scripts', 'rules_registry.json'), encoding='utf-8')).get('rules', []))
    except Exception:
        pass
    # **S156 修：版本号不许手抄字面量**（M-R85：跨模块引用的标识符必须从正本派生）。
    # 这里硬编码 `1.50.0` 而实际已 1.65.1 —— 对账命令报出的是一个**过期 15 个版本的数**。
    # 危害在于它是"对账"场景：人拿它对账，结果基准本身就是错的，越对越偏。
    _cv = '?'
    try:
        _cv = re.search(r'^version:\s*"?([^"\n]+)"?',
                        open(os.path.join(root, 'SKILL.md'), encoding='utf-8').read(),
                        re.M).group(1).strip()
    except Exception:
        pass
    print(f'\n【层1 生态库】规则 {n_rules} 条｜workspace 文档 {n_rep} 份｜conductor 版 {_cv}')
    # 层2 资料库
    sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
    try:
        import link
        # S159：原附加项挂的是已清空的旧源总库（永远「0 行」）；改挂源库。
        # ★S160：这里原**写死** py/csp 两个旧库 id —— 并库后它们成了两个"空号"，
        #   对账会稳定显示 0 行且不报错（与 S156「改库 ID 漏了三处」同一类）。
        #   现从 link.SRC_DBS 派生（单一事实源），搬库时自动跟。
        _extra = [(f'源库·{n}', d) for n, d in link.SRC_DBS]
        print('\n【层2 资料库】')
        for name, db in list(LINK_DBS.items()) + _extra:
            try:
                print(f'   {name:18s} {len(link.rows_of(db))} 行')
            except Exception as e:
                print(f'   {name:18s} ❌ {str(e)[:50]}')
    except Exception as e:
        print(f'\n【层2 资料库】读取失败：{e}')
    # 层3 云端备份
    print('\n【层3 云端备份】')
    try:
        st = json.load(open(BACKUP_STATE_FP, encoding='utf-8'))
        last = st['last']
        print(f"   最近备份 {last['date']}｜{len(last['items'])} 件｜节点 {last['folder_id']}｜失败 {last.get('fail', '?')}")
        age_h = (_t.time() - _t.mktime(_t.strptime(last['date'], '%Y%m%d'))) / 3600
        if age_h > 24:
            print('   ⚠️ 备份超 24h——跑 conductor backup 刷新（沉淀即备份）')
        else:
            print('   ✅ 备份新鲜（<24h）')
    except Exception as e:
        print(f'   ❌ 无备份状态（{e}）——三层闭环未转，先跑 backup')
    print('\n💡 联动矩阵：①生态→资料=台账/经验/ECO 沉淀 ②生态→云端=backup ③资料→云端=backup 数据面导出 ④云端→生态=restore ⑤整体=本命令')




# ---------- S127：问题总账（复盘遗留零漂没——当次解决/下一环节解决/盯到清零） ----------
ISSUES_FP = os.path.join(os.path.dirname(PLAN_FILE), 'issues_ledger.json')


def _load_issues():
    """三态：不存在=空账（首次）；损坏=显式报（M-R60）不折叠。"""
    try:
        return json.load(open(ISSUES_FP, encoding='utf-8'))
    except FileNotFoundError:
        return {'issues': []}
    except Exception as e:
        raise RuntimeError(f'issues_ledger.json 损坏：{e}')


def cmd_issues(rest):
    """issues：问题总账——复盘产出的未解决问题全登记，状态机 open→next→done（或 wontfix+理由）。
    用法：issues（总览）｜issues add "<问题>" --from <来源> [--next "<下一环节>"]｜issues close <id> <解法>｜issues wontfix <id> <理由>（引号可省）"""
    import time as _t
    if isinstance(rest, list):
        rest = ' '.join(rest)
    rest = (rest or '').strip()
    db = _load_issues()
    issues = db.get('issues', [])
    if rest.startswith('add '):
        import shlex
        try:
            toks = shlex.split(rest[4:])
        except ValueError:
            toks = rest[4:].split()
        title = toks[0] if toks else ''
        src, nxt = '复盘', ''
        i = 1
        while i < len(toks) - 1:
            if toks[i] == '--from':
                src = toks[i + 1]; i += 2
            elif toks[i] == '--next':
                nxt = toks[i + 1]; i += 2
            else:
                i += 1
        nid = f'P{len(issues) + 1:03d}'
        issues.append({'id': nid, 'title': title, 'from': src, 'next': nxt,
                       'status': 'open', 'opened': _t.strftime('%Y-%m-%d'), 'closed': ''})
        db['issues'] = issues
        open(ISSUES_FP, 'w', encoding='utf-8').write(json.dumps(db, ensure_ascii=False, indent=1))
        print(f'✅ 已登记 {nid}：{title}（来源 {src}{"｜下一环节 " + nxt if nxt else ""}）——总账 {len(issues)} 条')
        return
    if rest.startswith('close ') or rest.startswith('wontfix '):
        parts = rest.split(' ', 2)
        pid = parts[1].strip()
        note = parts[2].strip() if len(parts) > 2 else ''
        st = 'done' if rest.startswith('close') else 'wontfix'
        hit = False
        for it in issues:
            if it['id'] == pid and it['status'] not in ('done', 'wontfix'):
                it['status'] = st
                it['closed'] = _t.strftime('%Y-%m-%d')
                it['note'] = note
                hit = True
        if not hit:
            print(f'❌ {pid} 不存在或已结'); return
        db['issues'] = issues
        open(ISSUES_FP, 'w', encoding='utf-8').write(json.dumps(db, ensure_ascii=False, indent=1))
        print(f'✅ {pid} → {st}：{note}')
        return
    # 总览
    opn = [i for i in issues if i['status'] == 'open']
    nxt = [i for i in issues if i['status'] == 'next']
    done = [i for i in issues if i['status'] in ('done', 'wontfix')]
    print(f'📋 问题总账：{len(issues)} 条（open {len(opn)}｜next {len(nxt)}｜已结 {len(done)}）')
    for it in issues:
        if it['status'] == 'open':
            print(f"   🔴 {it['id']} {it['title']}（来源 {it['from']}）")
        elif it['status'] == 'next':
            print(f"   🟡 {it['id']} {it['title']} → 下一环节：{it.get('next', '?')}")
        else:
            print(f"   ✅ {it['id']} {it['title']}（{it['status']} {it['closed']}）")
    if issues and not done:
        print('   · 全部未结——按 M-R73 逐环节清账')
    if not issues:
        print('   · 空账（首次）——跑复盘后 add 登记')




# ---------- S135：onboard 新增技能单一入口（M-R78） ----------
def _desc_trigger_words(skill):
    """从 SKILL.md frontmatter 的 description 里抽「显式触发词」。
    只认两种句式（与 M-R76 的 _skill_desc_terms 同口径）：
      ① 触发词：A / B / C
      ② 当用户提到 … 时触发
    抽不到就返回空——**不猜**，让调用者显式给词（猜出来的词会把路由搞脏）。
    """
    import re as _re
    p = os.path.join(SKILL_ROOT, skill, 'SKILL.md')
    if not os.path.exists(p):
        return []
    txt = open(p, encoding='utf-8').read()[:6000]
    words = []
    m = _re.search(r'触发词[：:]\s*(.+)', txt)
    if m:
        for w in _re.split(r'[／/|｜、,，]', m.group(1)):
            w = w.strip().strip('「」`*"\' ')
            if 2 <= len(w) <= 12:
                words.append(w)
    m2 = _re.search(r'当用户(?:提到|说|问)[「"\']?([^。\n」"\']{2,60})', txt)
    if m2:
        for w in _re.split(r'[／/|｜、,，和及]', m2.group(1)):
            w = w.strip().strip('「」`*"\' ')
            if 2 <= len(w) <= 12:
                words.append(w)
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out[:12]


def _golden_of(skill):
    """取该技能的黄金用例（用于 onboard --verify 检查是否已有独立标尺）。"""
    for q, want in ROUTE_GOLDEN.get(skill, []):
        yield q


def cmd_gate(rest):
    """gate [open <意图>|close|status]：会话闸门状态（S153）。

    **为什么把它从「提示」升级成「机制」**：
    生态早有提速入口（`conductor ask` 自动预载历史经验、`brief` 列雷区），
    但它们是**提示**——实测被整轮绕过去手写脚本：同一件事慢 15.7 倍，
    且历史已固化的招式（T-I「写后必回读」）被重新推导一遍。
    故：写库的最后一跳（`library/database/batch_{add,update}_*.py`）校验开工登记，
    无登记**直接拒绝写入**，而不只是打印一句建议。

    为什么设在最后一跳而不是调用方：调用方可以绕过——AI 手搓 subprocess / heredoc
    管道照样能写。只有最后一跳是所有路径的**必经点**（要做_DataFlow X。）。
    """
    try:
        sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
        import session_gate
    except Exception as e:
        print(f'❌ 会话闸门模块不可用（{type(e).__name__}: {e}）——'
              f'此时写库应被拒绝（勿在闸门失效时裸写）')
        sys.exit(1)
    sub = (rest[0] if rest else 'status')
    if sub == 'open':
        intent = ' '.join(rest[1:]) or '(手工登记)'
        print('✅ 已登记开工' if session_gate.open_session(intent) else '❌ 登记失败')
        return
    if sub == 'close':
        print('✅ 已收尾' if session_gate.close_session() else '❌ 收尾失败（可能尚未开工）')
        return
    st = session_gate.state()
    print('🚪 会话闸门状态')
    for k, v in st.items():
        print(f'   {k}: {v}')
    if st.get('bypassed'):
        print(f'   ⚠️ 本轮有 {st["bypassed"]} 次旁路写入——事后须复核必要性')


def cmd_size(rest):
    """size [技能名|all]：SKILL.md 体积**写后即测**（S149）。

    **为什么要有它（这是 S148 晚的真实事故）**：
    M-R77 的拦截点原本只有「审计时」——`audit-growth` 和 `verify ⑦`，**都是事后**。
    今天为了改触发词往多个 SKILL.md 追加 description，体积再次越过缓冲线；
    拦截发生在很晚的 verify，于是只能**开一场「压缩 SKILL.md」的会话**去追。
    那场会话反复量字节、读长行、改文案，工具调用密集，**反过来把会话上下文撑爆**，
    平台于是反复压缩对话——用户看到的「今晚一直在压缩」是这个链条的末端症状。
    结论：**拦截点必须前移到写盘后第一步**。改完 SKILL.md 立刻跑本命令，
    超标当场处理，不许攒到审计时再补。
    阈值读 rules_registry.thresholds（M-R77⑤ 禁硬编码），任何一项不达标 exit=1，可直接做门禁。
    """
    reg_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rules_registry.json')
    th = json.load(open(reg_p, encoding='utf-8')).get('thresholds', {})
    hard = th.get('skill_max_bytes', 40960)
    buf = th.get('skill_buffer_bytes', 35840)
    targets = list(ECO_SKILLS) if (not rest or rest[0] in ('all', '')) else [rest[0]]
    root = SKILL_ROOT   # 与 verify⑦ / _sig_eco_ledger 同源口径（别自己再推一层 __file__）
    print(f'📏 SKILL.md 体积（硬上限 {hard}B / 缓冲线 {buf}B，阈值取自 rules_registry.thresholds）')
    bad = []
    for s in targets:
        p = os.path.join(root, s, 'SKILL.md')
        if not os.path.exists(p):
            print(f'  ⚠️ {s}: SKILL.md 不存在（无法判定）')
            bad.append(s)
            continue
        n = os.path.getsize(p)
        room = buf - n
        if n > hard:
            print(f'  ❌ {s:24} {n:>6}B  超硬上限 {n - hard}B —— audit-growth/verify⑦ 必红，须立刻瘦身')
            bad.append(s)
        elif n > buf:
            print(f'  ⚠️ {s:24} {n:>6}B  超缓冲线 {n - buf}B —— 还没到红线，但下一次追加触发词就会破（余量已负）')
            bad.append(s)
        elif room < 512:
            # ★ S169 修：原文案说「余量 <512B 的单列 🟡，不算失败」，但这里**也** append 进
            #   bad → 🟡 被算成"不达标"并 exit=1，文案和行为互相打脸。
            #   实测：probe-hub 35735B 余量 105B，被判 ❌ 不达标，而它**没超缓冲线**。
            #   归位：🟡 是预警不是失败，不进 bad、不影响退出码
            #   （退出码只代表"破了缓冲线"，别让预警也去拦 CI）。
            print(f'  🟡 {s:24} {n:>6}B  余量仅 {room}B —— 预警（不算失败），建议先外置一节再改')
        else:
            print(f'  ✅ {s:24} {n:>6}B  余量 {room}B')
    if bad:
        print(f'\n❌ {len(bad)} 项不达标：{"、".join(bad)}')
        print('   瘦身三法（M-R77⑥）：同源抄本改索引 / 重复表合并 / 论证型长节外置 docs（外置必须有索引行，且索引须指向真实存在的文件）')
        print('   ⚠️ 别为了压线删语义：压缩后必须逐条验证信息没丢（M-R92）')
        sys.exit(1)
    print(f'\n✅ 全部 {len(targets)} 项达标（余量 <512B 的单列 🟡，不算失败）')


def cmd_onboard(rest):
    """onboard <技能名> [--words "w1/w2"] [--dry]
    新增技能**单一入口**（M-R78）：新增一个技能只需跑这一条，四个面一次补齐。

    为什么要工具：新增技能原本要手工改四处，全凭记忆——
      ① eco_skills.json（遍历源）② _SKILL_INTENTS（话术）③ ROUTE_GOLDEN（黄金用例）④ ECO_NAME_HINTS（身份）
    漏 ① → 体积/路由/三件套/探针全瞎；漏 ②→ 用户说的话落不到它头上；
    漏 ③ → 路由坏了也测不出（自证循环）；漏 ④ → 信号源28 把它当外来技能放行。
    **漏任何一处都不报错**，直到有一天它没被用上才被发现。
    """
    import re as _re
    import shutil as _sh
    import datetime as _dt
    args = [a for a in rest if a]
    if not args:
        print('用法: conductor onboard <技能名> [--words "词1/词2/..."] [--dry]')
        print('作用: 一次补齐新增技能的四个面（清单/话术/黄金用例/身份），并校验三件套')
        return 1
    skill = args[0]
    dry = '--dry' in args
    words = []
    if '--words' in args:
        i = args.index('--words')
        if i + 1 < len(args):
            words = [w.strip() for w in _re.split(r'[／/|、,，]', args[i + 1]) if w.strip()]
    sp = os.path.join(SKILL_ROOT, skill, 'SKILL.md')
    if not os.path.exists(sp):
        print(f'❌ {skill} 没有 SKILL.md（{sp} 不存在）——先建技能再纳管')
        return 1

    print(f'🪪 纳管技能：{skill}' + ('（--dry 只检查不写）' if dry else ''))
    todo, done = [], []

    # ① eco_skills.json
    eco_fn = os.path.join(HERE, 'eco_skills.json')
    eco = json.load(open(eco_fn, encoding='utf-8'))
    if skill in eco.get('eco_skills', []):
        done.append('① eco_skills.json（已在册）')
    else:
        todo.append('① eco_skills.json')
        if not dry:
            eco.setdefault('eco_skills', []).append(skill)
            eco['eco_skills'] = sorted(set(eco['eco_skills']))
            json.dump(eco, open(eco_fn, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            # 回读确认（M-R71 自证全同步）：写没写进去不能靠"我以为写了"
            if skill in json.load(open(eco_fn, encoding='utf-8'))['eco_skills']:
                todo.pop()
                done.append('① eco_skills.json（本次已纳管）')

    # ② 话术：先用手给的，再试 description 显式触发词
    words = words or _desc_trigger_words(skill)
    # 注意：_SKILL_INTENTS 是**导入时**读入内存的字面量，本次写入磁盘后内存仍是旧值，
    # 故这里从磁盘文本判断"是否已有"，否则重跑会重复追加。
    src_now = open(os.path.abspath(__file__), encoding='utf-8').read()
    have = f"('{skill}'," in src_now
    if have:
        done.append('② _SKILL_INTENTS（已有话术）')
    elif words:
        todo.append(f'② _SKILL_INTENTS（补 {len(words)} 词）')
        if not dry and _append_intents(skill, words):
            todo.pop()
            done.append(f'② _SKILL_INTENTS（本次补 {len(words)} 词并回读确认）')
    else:
        todo.append('② _SKILL_INTENTS ⚠️ **抽不到触发词**——必须用 --words 手工给（不要猜）')

    # ③ 黄金用例
    if list(_golden_of(skill)):
        done.append('③ ROUTE_GOLDEN（已有用例）')
    else:
        todo.append('③ ROUTE_GOLDEN ⚠️ 缺黄金用例——路由坏了测不出（自证循环）')

    # ④ 身份
    if skill.startswith('tvbox-') or skill in ECO_NAME_HINTS:
        done.append('④ 身份识别（tvbox- 前缀/已登记）')
    else:
        todo.append(f'④ ECO_NAME_HINTS ⚠️ 不带 tvbox- 前缀且未登记——信号源28 会放行它')

    # ⑤ manifest 命令意图（S136 新增面）
    man_fp = os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'manifest.json')
    try:
        mf = json.load(open(man_fp, encoding='utf-8'))
        has_intent = any(c.get('skill') == skill for c in mf.get('commands', []))
    except Exception as e:
        has_intent = False
        todo.append(f'⑤ manifest.json 读取失败（{type(e).__name__}）——无法判定')
    if has_intent:
        done.append('⑤ manifest 命令意图（已登记）')
    elif not any('manifest' in t for t in todo):
        todo.append(f'⑤ manifest 缺命令意图——信号源5 会报「路由半盲」（话术可路由但命令不可达）')

    # ⑥ 派生消费方（S136 新增面）：技能集是否都从 eco_skills.json 派生
    #    只检查「有没有第二个硬编码清单」这一件事，不试图自动改代码。
    hard = []
    # 判据不是「出现过 startswith('tvbox-')」——那是本地自建的**兜底**，合法且必要；
    # 真问题是**把它当成唯一来源**（技能全集只由它决定）。故匹配「函数体内直接 return 前缀集合」形态。
    for rel, pat in (('skill-evolution/scripts/evolve.py',
                      r"def _all_skills\(\):(?:(?!\ndef ).)*?return sorted\(names\)\s*\n\s*\ndef"),
                     ('tvbox-inspect/scripts/inspect_engine.py',
                      r"FLOWS = \{n: os\.path\.join")):
        fp = os.path.join(SKILL_ROOT, rel)
        if os.path.exists(fp) and re.search(pat, open(fp, encoding='utf-8').read()):
            hard.append(rel.split('/')[0])
    if hard:
        todo.append(f'⑥ 派生消费方：{("、".join(hard))} 仍有硬编码技能清单——新技能可能扫不到')
    else:
        done.append('⑥ 派生消费方（evolve/inspect 均从正本派生）')

    # 三件套
    miss3 = [x for x in ('SKILL.md', 'CHANGELOG.md', 'scripts') if not os.path.exists(os.path.join(SKILL_ROOT, skill, x))]
    if miss3:
        todo.append(f'⑦ 三件套缺 {"、".join(miss3)}')
    else:
        done.append('⑦ 三件套齐备')

    # ⑧ 收尾绑定（S136 新增面）：新技能必须能让统一收尾命令看见它、也必须能交账。
    #    「以后新增技能也照这个做，做闭环」——若不查这两条，新技能建完就又是一座孤岛：
    #    · 收尾时它的欠账没人采 → 隐性欠账，越攒越多而无人知
    #    · 它自己收尾时无账可交 → 被当成"clean"，假的绿
    #    判据1：flow.json 存在且含 backlog 键（清账司按此键采集，缺键=采不到）
    #    判据2：该技能出现在清账司的采集面里（eco_skills.json 已含它即已满足，故这条
    #           实际验证的是「清账司代码没有硬编码技能名单」——若它用写死清单，新技能永不被采）
    flow_p = os.path.join(SKILL_ROOT, skill, 'scripts', 'flow.json')
    if not os.path.exists(flow_p):
        todo.append('⑧ 收尾绑定：缺 scripts/flow.json——清账司无从采集它的欠账')
    else:
        try:
            fj = json.load(open(flow_p, encoding='utf-8'))
            if 'backlog' not in fj:
                todo.append('⑧ 收尾绑定：flow.json 缺 `backlog` 键——清账司采集不到未完成项')
            else:
                done.append('⑧ 收尾绑定（flow.backlog 可采）')
        except Exception as e:
            todo.append(f'⑧ 收尾绑定：flow.json 解析失败（{type(e).__name__}）——无法判定')
    # 判据2：清账司是否从正本派生（无写死技能名单）
    ua = os.path.join(SKILL_ROOT, 'unfinished-audit', 'scripts', 'collect.py')
    if os.path.exists(ua):
        ua_src = open(ua, encoding='utf-8').read()
        # 写死名单的形态：直接列出多个技能名的元组/列表字面量
        hardcoded = re.search(r"(?:SKILLS|_ALL|TARGETS)\s*=\s*[\[\(]\s*['\"]tvbox-['\"]?\w*['\"],\s*['\"]", ua_src)
        if hardcoded:
            todo.append('⑧ 收尾绑定：清账司用写死技能名单——新技能永不被采集（须从 eco_skills.json 派生）')
        else:
            done.append('⑧ 收尾绑定（清账司按正本遍历，非写死名单）')
    else:
        todo.append('⑧ 收尾绑定：清账司 collect.py 不存在——无统一收尾入口')

    print('\n   ✅ 已就绪：')
    for d in done:
        print('      ' + d)
    if todo:
        print('\n   ⚠️ 待补：')
        for t in todo:
            print('      ' + t)
    print('\n   后续必做（工具不能替你做的部分）：')
    if not list(_golden_of(skill)):
        print(f'      1. 在 conductor.py 的 ROUTE_GOLDEN 加 2~3 条「用户真会这么说」→ 期望 {skill}')
    print('      2. 跑 conductor verify 看 ①探针 ③evolve ⑦体积 ⑧欠账单 ⑨刹车 是否全绿（终验九判据）')
    print('      3. 跑 audit-growth 看体积是否已在缓冲线内（新技能往往一次就超）')
    print('      4. 跑 tvbox close 看它是否被收尾命令采到（不被采到 = 隐性欠账）')
    if dry:
        print('\n   [--dry] 未写入。去掉 --dry 执行。')
        return 0
    if not todo:
        print(f'\n   ✅ 八面已齐——该技能已进入全部检查器与收尾入口视野')
    return 0


def _append_intents(skill, words):
    """把话术追加进 conductor.py 的 _SKILL_INTENTS 字面量（`\'\'\'` 前插入一行）。
    用文本插入而非 AST 重写：保持文件格式与注释不动，人还能读。
    """
    import re as _re
    fn = os.path.abspath(__file__)
    src = open(fn, encoding='utf-8').read()
    anchor = '\n]\n\n# 真实用户话术用例'
    if anchor not in src:
        print('   ⚠️ 找不到 _SKILL_INTENTS 结束锚点——请手工追加'); return False
    line = f"    ('{skill}', '{' '.join(words)}'),\n"
    src = src.replace(anchor, '\n' + line + ']\n\n# 真实用户话术用例', 1)
    open(fn, 'w', encoding='utf-8').write(src)
    # **写后必回读**（M-R77 外置纪律同理）：不回读就宣称写成功 = 假绿。
    # 实测过：文本插入一旦锚点漂移会静默不生效，而命令仍然打印"已写入"。
    back = open(fn, encoding='utf-8').read()
    if f"('{skill}'," not in back:
        print('   ❌ 写入后回读未找到该条目——请手工追加'); return False
    print('   ✍️ 已写入 _SKILL_INTENTS 并回读确认（重跑不会重复追加）')
    return True


# ---------- S127：sync-version 工具化 M-R71（四面一键同步，漂移归零） ----------
def cmd_sync_version(rest):
    """sync-version <新版本>：一键同步版本四面（SKILL.md frontmatter + flow.json + CHANGELOG 校验 + DB_ECO 台账）。
    M-R71 工具化：改一处=同步所有契约面，用工具代替记忆。CHANGELOG 缺该版本条目则拒升（防升版不留痕）。"""
    import re as _re, subprocess as _sp
    ver = (rest or [''])[0] if isinstance(rest, list) else (rest or '').split()[0] if (rest or '').split() else ''
    if not ver:
        print('用法: tvbox conductor sync-version 1.51.0'); return
    # **S148 修：版本号必须校验格式**。
    # 事故：我误敲 `sync-version --all`，`--all` 被当版本号**照单全收**，
    # SKILL.md 与 flow.json 双双被写成 `version: --all`，随后被信号源20
    # 报成「台账滞后：台账=1.64.0 实际=--all」。工具不校验就**把参数当数据写进磁盘**，
    # 而写后回读只验"新值出现了"——`--all` 确实出现了，于是自检也放行。
    # 教训：**参数合法性要在写之前判**，写在之后的回读只能验"写入成功"，验不了"写得对"。
    if not re.match(r'^\d+\.\d+\.\d+(-[\w.]+)?$', ver):
        print(f'❌ 版本号格式非法：{ver!r}（应形如 1.64.0；不支持 --all 之类开关）')
        print('   整生态台账同步请改用：tvbox conductor sync-version <具体版本号>，台账逐条用库脚本改')
        return
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    # ① SKILL.md
    p = os.path.join(root, 'SKILL.md')
    s = open(p, encoding='utf-8').read()
    s2 = _re.sub(r'version: "[^"]+"', f'version: "{ver}"', s, count=1)
    if s2 == s:
        # **S148 修：frontmatter 有两种写法** —— `version: "1.62.0"` 与 `version: 1.62.0`。
        # 原实现只匹配带引号那种，遇到不带引号的就**静默不替换**，然后被 assert 拦下，
        # 报出的却是光秃秃的 AssertionError（不知哪里错）。实测本技能正是无引号写法。
        # 修法：无引号形态也认，两种都替换；仍失败才报明确原因。
        s2 = _re.sub(r'(?m)^(version:\s*)(\S+)\s*$', lambda m: m.group(1) + ver, s, count=1)
    if s2 == s:
        # **S148 修2：`s2 == s` 有两种成因**——(a) 真没找到 version 行；(b) 找到了但**值相同**。
        # 原实现把两者都报成"未找到 version 行"，于是「用当前版本号跑一次」这种合法的
        # 幂等操作会报错（我刚用它验证"不误伤"时就撞上了）。
        # 正确做法：先确认行存在，再判断值。
        _m = _re.search(r'(?m)^version:\s*(\S+)\s*$', s)
        if not _m:
            print('❌ SKILL.md 未找到 frontmatter `version:` 行——无法同步（手工核对后再升版）'); return
        if _m.group(1).strip('"') == ver:
            print(f'ℹ️ SKILL.md 已是 {ver}，无需改动（继续校验其余面）')
        else:
            print(f'❌ SKILL.md 中 version 行存在但替换失败：当前 {_m.group(1)!r}，目标 {ver!r}'); return
    else:
        open(p, 'w', encoding='utf-8').write(s2)
    # 写后回读（M-R40：不看回执看字段值）
    if f'{ver}' not in open(p, encoding='utf-8').read()[:600]:
        print('❌ SKILL.md 写入后回读未见新版本号——同步失败'); return
    # ② flow.json
    p2 = os.path.join(here, 'flow.json')
    f = json.load(open(p2, encoding='utf-8'))
    old_ver = f.get('version')
    f['version'] = ver
    json.dump(f, open(p2, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    # ③ CHANGELOG 必须已有该版本条目（防"升版不留痕"）
    c = open(os.path.join(root, 'CHANGELOG.md'), encoding='utf-8').read()
    if ver not in c:
        print(f'❌ CHANGELOG 无 {ver} 条目——先写留痕再升版（M-R30/M-R71②）'); return
    # ④ DB_ECO 台账
    # ★ S158 修：判据从「stdout 里有没有 success」改为「**写后回读该记录的值**」。
    #   事故（G9）：旧写法 `eco_ok = '"success": true' in r.stdout`。而 batch_update 对
    #   "record_id 不存在" 的返回是「更新 0 条」——**不是报错**，只有一屏 JSON 里
    #   results[].success=false。于是这条通路长期显示 "DB_ECO ✓"，实际一条没写进去：
    #   实测 `ecosys-conductor` 这行的「末次升级」字段至今为空。
    #   这就是 M-R77 那句「success 不等于落库」在台账上的现场——**报告成功 ≠ 真的改了**。
    rid = 'x46zdZ0I8vynJjJ9YIFOIE'
    _now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    rec = json.dumps([{'record_id': rid, 'properties': {
        'version': {'text': ver},
        '末次升级': {'date': _now}}}], ensure_ascii=False)
    r = _sp.run([sys.executable, '/root/.codebuddy/skills/library/database/batch_update_database_records.py',
                 '--database-id', 'T4kODtANe7rSrtZTu4sIND', '--records', rec], capture_output=True, text=True)
    # 回读：独立查一次该记录，比 version 与 末次升级
    _back_ok, _back_v, _back_t, _back_why = False, '', '', ''
    try:
        import json as _j2
        _q = _sp.run([sys.executable, '/root/.codebuddy/skills/library/database/get_database_content.py',
                      '--database-id', 'T4kODtANe7rSrtZTu4sIND'], capture_output=True, text=True)
        _rows = list(csv.DictReader(io.StringIO(_j2.loads(_q.stdout)['content'])))
        _me = next((x for x in _rows if (x.get('技能名') or '') == 'ecosys-conductor'), None)
        if _me is None:
            _back_why = '回读：表里找不到 ecosys-conductor 这一行'
        else:
            _back_v, _back_t = (_me.get('version') or ''), (_me.get('末次升级') or '')
            _back_ok = (_back_v == ver and bool(_back_t))
            if not _back_ok:
                _back_why = f'回读：version={_back_v!r} 末次升级={_back_t!r}（期望 {ver} 且非空）'
    except Exception as _e:
        _back_why = f'回读失败（{type(_e).__name__}: {str(_e)[:60]}）——**无法判定**，勿当成功'
    eco_ok = _back_ok
    print(f'✅ sync-version {old_ver or "?"} → {ver}：SKILL.md ✓ ｜ flow.json ✓ ｜ CHANGELOG ✓（条目在）｜ '
          f'DB_ECO {"✓ 回读确认" if eco_ok else "❌ " + (_back_why or "写入未确认")}')


# ---------- S162：生态台账批量对账（version_guard --sync-eco 的入口） ----------
def cmd_eco_sync(rest):
    """eco-sync：把**本地 SKILL.md 版本**当权威源，批量回写云端生态库的滞后行。

    为什么单列一条命令：`sync-version` 只处理 ecosys-conductor 自己那一行（硬编码 record_id），
    其余 10 个技能的台账**没有任何工具管**——只能靠人想起来手工改，于是必然滞后。
    S162 实测：11 个里 6 个第三列落后，而 `--fix` 只能修本地两列，对这 6 个全都「无需修复」。

    委托 version_guard.py --sync-eco 执行（M-R38 同一事实源：不另写一份判据，
    否则体检说漂、回写按另一套对齐，两边永远各说各话）。
    """
    import subprocess as _sp
    vg = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'version_guard.py')
    if not os.path.exists(vg):
        print('❌ version_guard.py 不存在——无法对账（无法判定）'); return
    r = _sp.run([sys.executable, vg, '--sync-eco'], capture_output=True, text=True)
    print(r.stdout.rstrip())
    if r.stderr.strip():
        print(r.stderr.rstrip()[:400])
    if r.returncode != 0:
        print('   ⚠️ 本命令退出码非 0 —— 台账未全部对齐，别当成功')


def cmd_drift_fix(rest):
    """drift-fix：版本漂移**一条命令闭环**（S162·M-R71 工具化的补口）。

    **为什么还要单独一条**（不是已有 sync-version / eco-sync 了吗？）：
    这两条命令都在，但**人要记得按顺序跑三步**——先 `version_guard.py` 看漂没漂、
    再 `--sync-eco` 回写、再跑一遍确认。忘任何一步，台账就停在半路，而且
    **没有任何东西会报警**（这正是 S162 那 6 处漂移能活下来的原因）。

    M-R71 的原话是「用工具代替记忆」。工具在、但记忆还在人脑里，就等于没工具。
    本命令把三步串成一步：体检 → 需要才修 → **修完再体检一次**，最后按复检结果定 rc。

    ★ 三条不谎报的铁律：
      1. **复检说了算**：回写命令返回 0 不算数，必须再跑一次完整体检；
         `--sync-eco` 的 rc 只用来判断"要不要继续"，不用来下结论。
      2. **读不到就是读不到**（M-R60）：体检本身失败 → rc=1 且明说"无法判定"，
         不许因为"没看到漂移"就报绿。
      3. **不改本地**：本地两列（SKILL.md / CHANGELOG）不一致是**写错**，不是漂移，
         本命令只在提示里点名，绝不擅自把一边覆盖到另一边——那会掩盖真实错误。
    """
    import subprocess as _sp
    here = os.path.dirname(os.path.abspath(__file__))
    vg = os.path.join(here, 'version_guard.py')
    if not os.path.exists(vg):
        print('❌ version_guard.py 不存在——无法体检（无法判定）')
        return 1
    dry = '--dry' in (rest or [])

    def _check():
        r = _sp.run([sys.executable, vg], capture_output=True, text=True)
        return r.returncode, (r.stdout or '') + (r.stderr or '')

    print('① 体检')
    rc0, out0 = _check()
    print(out0.rstrip())
    if '漂移' not in out0 and '全部一致' not in out0:
        print('   ❔ 体检输出里既没有「漂移」也没有「全部一致」——无法判定，不往下走（M-R60）')
        return 1
    if '全部一致' in out0:
        print('\n✅ 无漂移，无需修复')
        return 0
    # 解析待修项：本地两列自相矛盾的不归本命令管
    # ★ 踩坑（本轮）：第一版按 `'漂移' in l` 抓行，抓到的却是标题行
    #   「🔴 发现 1 处漂移：」——它冒号右边是空的、没有 SKILL=，于是 need_eco 为空，
    #   命令打出「✅ 无需回写」并 rc=0。**明明有漂移却报绿**，是比漏修更坏的假绿。
    #   修法：只认同「同时含 SKILL= 与 生态库=」的行，与标题行彻底分开判。
    drift_lines = []
    for l in out0.splitlines():
        if 'SKILL=' in l and '生态库=' in l:
            drift_lines.append(l)
    need_eco = []
    local_bad = []
    for l in drift_lines:
        body = l.split('：', 1)[1] if '：' in l else l
        # ★ 踩坑（本轮二）：`' CHANGELOG'` 键名**带前导空格**（行格式 ` / ` 分隔），
        #   不 strip 就取不到 → 判成"本地两列矛盾"，把一条正常的云端漂移误报成写错。
        #   解析别人打印的文本，键值两侧一律 strip。
        parts = {}
        for _p in body.split('/'):
            if '=' in _p:
                _k, _v = _p.split('=', 1)
                parts[_k.strip()] = _v.strip()
        sk = l.split('：')[0].strip().lstrip('🔴 ').strip() if '：' in l else '?'
        v_s, v_c, v_e = (parts.get('SKILL', '').strip(), parts.get('CHANGELOG', '').strip(),
                         parts.get('生态库', '').strip())
        if v_s != v_c:
            local_bad.append(f'{sk}（SKILL={v_s} / CHANGELOG={v_c}）')
        elif v_e != v_s:
            need_eco.append(sk)
    if not drift_lines:
        print('\n❔ 体检说有漂移，但一行也没解析出来（输出格式变了？）——无法判定，不写入（M-R60）')
        return 1
    if local_bad:
        print('\n❌ 本地两列自相矛盾——这是**写错**不是漂移，本命令不动它（改了会掩盖错误）：')
        for x in local_bad:
            print(f'   {x}')
        print('   ↪ 请人工核对 SKILL.md 与 CHANGELOG.md 后，用 sync-version <正确版本> 收敛')
    if not need_eco:
        print('\n' + ('❌ 无可自动修复项（仅本地矛盾）' if local_bad else '✅ 无需回写'))
        return 1 if local_bad else 0
    print(f'\n② 需回写云端 {len(need_eco)} 条：{"、".join(need_eco)}')
    if dry:
        print('   --dry：只报不做，未写入')
        return 1
    r1 = _sp.run([sys.executable, vg, '--sync-eco'], capture_output=True, text=True)
    print((r1.stdout or '').rstrip())
    if r1.stderr.strip():
        print((r1.stderr or '').rstrip()[:400])
    # ★ 铁律1：回写 rc 只决定"要不要复检"，不决定结论
    print('\n③ 复检（以这一步为准，不看上一步回执）')
    rc1, out1 = _check()
    print(out1.rstrip())
    if '全部一致' in out1:
        print('\n✅ 漂移已闭环')
        return 0
    if '漂移' in out1:
        print('\n❌ 仍有漂移——未闭环（回写没生效或又出现新漂移）')
        return 1
    print('\n❔ 复检无法判定（M-R60）——不当成功')
    return 1




# ---------- S117：合规专项审计（M-R70·盯不干活与乱干活） ----------
def cmd_audit_compliance(rest):
    """audit-compliance：未触发/不守规专项审计（用户钦定职能，监管者=经验池与规则的管家 conductor）。
    三个证据源自动汇总：①不干活（触发盲区+恒零未实证）②乱干活（journal 异常+台账踩坑）③探针现状。"""
    print('🕵️  合规专项审计（M-R70：盯不干活·乱干活）')
    # ① 不干活：触发盲区
    blind = [(sk, d) for sk in ECO_SKILLS if (d := _sig_route_blind(sk))]
    print(f'\n【① 不干活·触发盲区】{len(blind)} 个：')
    for sk, d in blind:
        print(f'   ❌ {sk}: {d}')
    if not blind:
        print('   ✅ 全技能双路由面（manifest+话术表）在册')
    try:
        mon = json.load(open('/root/.codebuddy/skills/tvbox-inspect/monitors.json', encoding='utf-8'))
        ms = mon['monitors'] if isinstance(mon, dict) else mon
        unproven = [m['id'] for m in ms if not m.get('proven')]
        print(f'\n【② 不干活·判定器未实证】{len(unproven)} 个：{",".join(unproven) or "无（28 项全 proven）"}')
    except Exception as e:
        print(f'【② 判定器实证】读取失败（{e}）——无法判定（M-R60）')
    # ③ 乱干活：journal 异常
    acts = []
    if os.path.exists(JOURNAL_FP):
        for ln in open(JOURNAL_FP, encoding='utf-8'):
            try:
                acts.append(json.loads(ln))
            except Exception:
                pass
    # 已归档项（证据永久缺失、同命令已复跑正常）不计入——归档而非删除，保留历史（S022）
    fails = [a for a in acts if not a.get('ok') and not a.get('archived')]
    n_arch = len([a for a in acts if not a.get('ok') and a.get('archived')])
    print(f'\n【④ 乱干活·动作异常】journal {len(acts)} 动作，异常 {len(fails)}'
          f'{"（另有 %d 条已归档：证据缺失但已复跑正常）" % n_arch if n_arch else ""}：')
    for a in fails[-5:]:
        # 有 exit / err 字段的（S136 后新入口写入）直接给证据；
        # 没有的是旧版入口写的——旧版只记 bool(ok)，证据已不可追，如实说明而不是含糊「须复核」。
        if 'exit' in a or a.get('err'):
            print(f"   ✗ {a['ts']} {a['verb']} {a['arg'][:40]}"
                  f"（exit={a.get('exit', '?')}"
                  f"{'｜' + a['err'] if a.get('err') else ''}）")
        else:
            print(f"   ✗ {a['ts']} {a['verb']} {a['arg'][:40]}"
                  f"（旧版入口记录·无 exit/err 证据，无法追溯真因）")
    if not fails:
        print('   ✅ 无异常动作')
    # ⑤ 乱干活：台账近期踩坑（违规实锤）
    try:
        sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
        import link as _lk
        rows = _lk.rows_of('TO4LwsjVVdR8JRAUlkejk3')
        import re as _re
        pits = [(str(r.get('会话编号','')), str(r.get('踩坑','') or '')) for r in rows]
        pits = [(i, p) for i, p in pits if p]
        print(f'\n【⑥ 乱干活·台账踩坑库】累计 {len(pits)} 期（=违规实锤，详见 ④经验库对应 L 条目）：')
        for i, p in pits[-5:]:
            print(f'   [{i}] {p[:60]}')
    except Exception as e:
        print(f'【⑥ 台账踩坑】读取失败（{e}）——无法判定')
    # ⑦ 探针现状
    try:
        sp = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'plans', 'signal_proven.json'), encoding='utf-8'))
        last = sp[-1] if sp else {}
        print(f"\n【⑧ 探针现状】上期 {last.get('date','?')}：{last.get('passed','?')}/{last.get('probed','?')} 通过，假阳性基线 {last.get('false_positive','?')}")
    except Exception:
        print('\n【⑧ 探针现状】无留痕（建议跑 tvbox conductor signal-check）')
    print('\n💡 处置口径：不干活→补路由/补信号源/补 proven；乱干活→沉淀经验池+对应探针盯防；本审计随复盘闭环链跑。')



def cmd_registry(args):
    """五方对照表（S177 步6）：技能 ↔ 规则 ↔ 信号源 ↔ 监测项 ↔ 命令。

    为什么要有这条命令：S177 前三步反复撞到同一类病——
    「某处显示 ✅，其实从来没真查过」（M03 的 `s` 未定义、signal-check 恒 rc=0）。
    根因不是某个函数写错，而是**五张表各自维护、中间那根线没人验**。
    线断了不报错，只是所有检查安静地变绿。

    用法:
      tvbox conductor registry          重建并打印摘要（含孤儿技能/新技能未立规）
      tvbox conductor registry --build  只重建，安静写文件
      tvbox conductor registry --json   打印整表 JSON（供外部程序消费）
    """
    import json as _json
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(os.path.dirname(here), 'plans', 'S177_registry.json')
    try:
        if here not in sys.path:
            sys.path.insert(0, here)
        import registry_build as RB
        reg = RB.build()
    except Exception as e:
        # M-R60：建表失败 ≠ 没有对照关系
        print(f'❌ 对照表构建失败（{type(e).__name__}: {e}）——'
              f'**这不代表五方关系为空**，请先修构建器')
        return 1

    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        _json.dump(reg, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    except Exception as e:
        print(f'⚠️ 写文件失败（{type(e).__name__}: {e}）——表已算好但没落盘')
        return 1

    if '--json' in args:
        print(_json.dumps(reg, ensure_ascii=False, indent=1))
        return 0
    if '--build' in args:
        print(f'已重建 {out}')
        return 0

    c, g = reg['counts'], reg['gaps']
    print('五方对照表（S177 步6）')
    print(f"  技能 {c['skills']} / 规则 {c['rules']} / 信号源 {c['signals']}"
          f" / 监测项 {c['monitors']} / 命令 {c['commands']}")
    print(f'  已写入 {out}')
    print()
    print('各技能被守护情况：')
    for k, v in reg['by_skill'].items():
        print(f"    {k:28} 规则(own)={len(v['rules_owned']):3} "
              f"参与={len(v['rules_touched']):3} 监={len(v['monitors']):2} 令={len(v['commands']):3}")
    print()
    print('缺口：')
    print(f"  孤儿规则（owner 不在登记表）: {len(g['orphan_rules_owner_not_registered'])}")
    print(f"  无 probe 的规则: {len(g['rules_without_probe'])}")
    print(f"  无 landing 的规则: {len(g['rules_without_landing'])}")
    print(f"  孤儿技能（三零：不守规则/无监测/无入口）: {len(g['orphan_skills'])}")
    for o in g['orphan_skills']:
        print(f"     · {o['skill']}  SKILL.md={o['has_skill_md']} flow={o['has_flow']}")
    print(f"  有命令但零规则（新技能未立规）: {len(g['skills_without_any_rule'])}")
    for o in g['skills_without_any_rule']:
        print(f"     · {o['skill']:24} 令={o['commands']:2} 监={o['monitors']}")
    # 有缺口即非零：让这条命令在 CI 里能真正挡住
    return 0 if not (g['orphan_skills'] or g['orphan_rules_owner_not_registered']) else 3


DISPATCH = {'plan': cmd_plan, 'sprint': cmd_sprint, 'speed': cmd_speed,
            'whoami': cmd_whoami, 'ask': cmd_ask, 'history': cmd_history,
            'lanes': cmd_lanes, 'assess': cmd_assess,
            'direction': cmd_direction, 'conflict': cmd_conflict,
            'handoff': cmd_handoff, 'expand-eco': cmd_expand_eco,
            'retro': cmd_retro, 'evo-check': cmd_evo_check, 'decide': cmd_decide,
            'retract': cmd_retract, 'signal-check': cmd_signal_check, 'verify': cmd_verify,
            'rules-probe': cmd_rules_probe,   # ★ S176：真正执行 probe.command（此前从不执行）
            'next': cmd_next, 'recap': cmd_recap, 'audit-growth': cmd_audit_growth, 'audit-compliance': cmd_audit_compliance, 'waste': cmd_waste, 'backup': cmd_backup, 'restore': cmd_restore, 'linkmap': cmd_linkmap, 'issues': cmd_issues, 'sync-version': cmd_sync_version, 'eco-sync': cmd_eco_sync, 'drift-fix': cmd_drift_fix, 'onboard': cmd_onboard, 'size': cmd_size, 'gate': cmd_gate,
            'field': cmd_field,   # S157：第三方口语集度量（只读，不进任何判据）
            'registry': cmd_registry}   # S177 步6：五方对照表（技能↔规则↔信号源↔监测项↔命令）


def _rules_fn():
    """规则纳管命令（S109）——独立模块 rules_cmd.py，惰性加载（M-R46）。

    ★ S177：原来 `rules_cmd.main(rest)` **不接返回值**，且 except 分支也只打印。
      后果：rules_cmd 内部把「注册表不可用」改成 return 3 了，走到这里又被丢掉，
      conductor 拿到 None → rc=0。**上游修好了、这一层没修，等于没修**——
      这正是今天反复出现的同一个模式（evolve.dispatch / link.dispatch / 本处）。
      另一面：except 只 print 不返回 → 模块连导入都失败（真严重故障），对外仍是"成功"。
      两处都补齐，口径与 conductor 其余命令一致：True/None→0、False→1、int 原样。
    """
    def _run(rest):
        try:
            import sys as _sys
            if os.path.dirname(os.path.abspath(__file__)) not in _sys.path:
                _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import rules_cmd
            return rules_cmd.main(rest)
        except Exception as e:
            print(f'❌ rules 模块加载/执行失败：{type(e).__name__}: {e}')
            return 1      # 脚本自身异常，与「命令跑了但不通过」区分：这里是根本没跑起来
    return _run


DISPATCH['rules'] = _rules_fn()


def _brake_fn():
    """治理刹车命令（S138·M-R94/M-R95）：独立模块 brake.py，惰性加载。
    不注册的话，`brake.py` 只是躺在那里的脚本——M-R94 自己就要求「判据须可执行且必须阻断」，
    一条只能 `python3 brake.py` 才能跑的门禁，等于没门禁。"""
    def _run(rest):
        try:
            import sys as _sys
            if os.path.dirname(os.path.abspath(__file__)) not in _sys.path:
                _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import brake
            return brake.main(list(rest or [])) or 0
        except SystemExit:
            raise
        except Exception as e:
            print(f'❌ brake 模块加载/执行失败：{type(e).__name__}: {e}')
            return 2
    return _run


DISPATCH['brake'] = _brake_fn()


def _inventory_fn():
    """全技能清单（S174）：独立模块 eco_inventory.py，惰性加载。

    ★ 为什么必须注册进来：本模块是"跨层检测的标尺"——大生态/平台托管/无归属三层清单。
      只写文件不注册，它就和当年躺着的 brake.py 一样：**一条只能手敲路径才能跑的命令**，
      等于没有。用户问"到底有多少技能、为什么我看不到某些"时，得有一条命令能答。
    """
    def _run(rest):
        try:
            import sys as _sys
            if os.path.dirname(os.path.abspath(__file__)) not in _sys.path:
                _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import eco_inventory
            return eco_inventory.main(list(rest or [])) or 0
        except SystemExit:
            raise
        except Exception as e:
            print(f'❌ eco_inventory 模块加载/执行失败：{type(e).__name__}: {e}')
            return 2
    return _run


DISPATCH['inventory'] = _inventory_fn()


def _slim_fn():
    """技能瘦身命令（S135·M-R77）：独立模块 slim_master.py，惰性加载。
    audit-growth 的提示文案里一直写 `conductor slim ...`，但此前并未注册——
    **提示指向一条不存在的命令**，照着做必踩空。此处补上，让文档与实现一致。"""
    def _run(rest):
        try:
            import sys as _sys
            if os.path.dirname(os.path.abspath(__file__)) not in _sys.path:
                _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import slim_master
            argv = list(rest or [])
            if not argv:
                print('用法: conductor slim <技能名|总册> [--diagnose|--apply <关键字>|--verify]')
                return 1
            return slim_master.main(argv) or 0
        except SystemExit:
            raise
        except Exception as e:
            print(f'❌ slim 模块加载/执行失败：{type(e).__name__}: {e}')
            return 1
    return _run


DISPATCH['slim'] = _slim_fn()


def _close_fn():
    """S136 统一收尾入口：conductor close / finish → 转清账司（unfinished-audit）。

    设计文档（生态1016）已登记 close/finish，信号源6「文档-实现漂移」据此判定
    DISPATCH 必须能接住这两个名字——否则用户照着文档敲 `tvbox conductor close`
    会落到「未知命令」。**转发而非复制实现**：正本永远只有清账司一份（M-R38）。
    """
    def _run(rest):
        try:
            u_dir = os.path.join(SKILL_ROOT, 'unfinished-audit', 'scripts')
            if u_dir not in sys.path:
                sys.path.insert(0, u_dir)
            import unfinished_cli
            rc = unfinished_cli.main(list(rest or [])) or 0
            # S161 账房钩子（纯提示，不写账）：收尾前先结账——凭证必须在闸门关闭前落账
            try:
                import subprocess as _sp
                _r = _sp.run(['python3', os.path.join(SKILL_ROOT, 'eco-ledger', 'scripts', 'ledger.py'), 'status'],
                             capture_output=True, text=True, timeout=20)
                if '已建账' in _r.stdout:
                    print('📒 账房提醒：收尾前先 `tvbox ledger settle`（settle → post → close 顺序纪律）')
            except Exception:
                pass
            return rc
        except SystemExit:
            raise
        except Exception as e:
            print(f'❌ 收尾入口加载/执行失败：{type(e).__name__}: {e}')
            return 1
    return _run


DISPATCH['close'] = _close_fn()
DISPATCH['finish'] = DISPATCH['close']


def dispatch(argv):
    if not argv:
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    # ★ S177 步3：help 必须显式放行。原先没有这个分支，`tvbox conductor --help`
    #   落到「未知命令」上——只因为那时未知命令返回 None(=rc 0)，看着像成功才没人发现。
    #   把未知命令改成 rc=2 之后它就暴露了：**敲 --help 得到退出码 2**，
    #   人类和脚本都会以为出错了。补上显式分支，比"靠未知命令兜住"名正言顺。
    if cmd in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    fn = DISPATCH.get(cmd)
    if not fn:
        print(f'未知命令: {cmd} ｜ 可用: {" / ".join(DISPATCH)}')
        # ★ S177 步3 实测踩到：这里原来是裸 `return`（返回 None），
        #   而本模块约定 None → 0，于是**「命令根本不存在」被记成「执行成功」**。
        #   实证：`tvbox conductor self_check` → 屏幕打「未知命令」，退出码却是 0。
        #   危险在于：任何按 rc 判断的编排/脚本都会把这条当成"自检通过"。
        #   （同生态 learn.py / probe.py 的同类分支都是 sys.exit(2)，这里反而是例外。）
        #   回 2 而不是 1：2 = 用法错/命令不存在，1 = 命令跑了但不通过。
        #   混用会让排查时分不清"我敲错了"还是"它真的报红"。
        return 2
    # S138·M-R93 延伸：返回值必须**传出去**，否则退出码永远 0。
    # 实测踩到：cmd_verify 判「8/9 不通过」后 `return False`，这一行直接把 False 丢进垃圾桶，
    # 于是 `tvbox conductor verify` 与 `--strict-exit` 双双 exit=0——**门禁报了红，门却是开的**。
    # 这是 Verdict 那条规则的同族错误：判定算对了，却没交到下游手里。
    # 约定：命令返回值 True/None → 0；False → 1；int → 原样；非 int 非空视为 0。
    r = fn(rest)
    if r is False:
        return 1
    if isinstance(r, int) and not isinstance(r, bool):
        return r
    return 0


if __name__ == '__main__':
    sys.exit(dispatch(sys.argv[1:]) or 0)
