#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox 技能生态统一入口（技能↔脚本联动中枢 + 并行加速）

分发到：联动引擎 link.py / 黑名单 blk.py / 进化引擎 evolve.py

用法:
  tvbox <verb> [args]                单命令（向后兼容：自动分发到对应脚本）
  tvbox run <v1> <v2> ...            同进程串联多命令，共享缓存（高效，最快单线程）
  tvbox p "<v1>" "<v2>" ...          并行跑多命令（多线程并发，各自带标题输出）
  tvbox manifest                     打印「意图 → 命令」联动清单
  tvbox help                         帮助

设计要点（回应两个提效方向）:
  1) 技能↔脚本联动：manifest.json 把"用户意图"显式绑定到"脚本命令"，
     调度器据此把多个命令放进**同一进程**并共享 core 缓存/并行池，脚本因此跑得更快。
  2) 并行更快：run 用进程内缓存消除重复云端读；p 把互相独立的命令多线程并发执行。

示例:
  tvbox run brief status            # 开工速览+体检，一条进程完成
  tvbox p "block stats" "evolve scan" "eco"   # 三件独立事并行跑
"""
import sys, os, io, time, json, subprocess, importlib.util

HUB = '/root/.codebuddy/skills/tvbox-source-hub/scripts'
BLK = '/root/.codebuddy/skills/tvbox-source-blacklist/scripts/blk.py'
EVO = '/root/.codebuddy/skills/skill-evolution/scripts/evolve.py'
DOG = '/root/.codebuddy/skills/legacy-watchdog/scripts/watchdog.py'
CON = '/root/.codebuddy/skills/ecosys-conductor/scripts/conductor.py'
IPL = '/root/.codebuddy/skills/tvbox-interface/scripts/pipeline.py'
PRB = '/root/.codebuddy/skills/probe-hub/scripts/probe.py'
UNF = '/root/.codebuddy/skills/unfinished-audit/scripts/unfinished_cli.py'   # S136 清账司
# S148 补（已闭环）：监察司 / 清道夫此前未接入入口——manifest 登记了 `tvbox inspect *` 与 `tvbox janitor *`
# 共 12 条命令，SKILL.md 也写明了用法，但 ROUTE 里没有这两个词，实跑一律「未知子命令: inspect」。
# **已于 ROUTE 行100 接好**：`'inspect': INS, 'janitor': JAN`（S148 修复落点，现可路由）。
# 历史残留（保留作审计）：M-R83 的探针 `tvbox inspect scan` 一度是幽灵命令（规则册声称有探针而探针不存在，
# 比没有更坏）；入口接好后该命令已可路由，规则册"有探针"登记现状见 M-R83 自身。
INS = '/root/.codebuddy/skills/tvbox-inspect/scripts/inspect.py'
JAN = '/root/.codebuddy/skills/tvbox-janitor/scripts/janitor.py'
# ★ S156（已闭环）：账房司（eco-ledger）此前未接进入口——SKILL.md 写了用法，`tvbox ledger` 却报未知子命令。
# 与 S148 的 inspect/janitor 同型：**技能建好但没人叫得动**。**已于 ROUTE 行101 接好**：`'ledger': LED`。
LED = '/root/.codebuddy/skills/eco-ledger/scripts/ledger.py'
# ★ S172（已闭环）：学习工坊（learn-hub）。建好后必须接入口，否则 manifest 里登记的命令全变幽灵。
# 已在 ROUTE 行110 接好：`'study': LRN`（动词取 `study` 不取 `learn`，避免与 link.py 冲突）。
# 动词取 `study` 不取 `learn`：`learn` 已被 link.py 占用（查业务历史经验），
# 同名会让两套语义挤在一个词上，那比幽灵命令更难查。
LRN = '/root/.codebuddy/skills/learn-hub/scripts/learn.py'
# S176c：`tvbox now` ——「现在在干什么」。
# 起因不是技术缺口，是**用户看不清我在干什么**：一晚上问了十几次「到哪了」「继续」。
# 放后台只解决了「不锁死会话」，没解决「他在后台跑的时候用户依然不知道进度」。
# 动词刻意短：它是我在执行中途该主动敲的东西，名字长了又变成一个没人跑的入口。
ACT = '/root/.codebuddy/skills/ecosys-conductor/scripts/activity.py'
LINK = os.path.join(HUB, 'link.py')
MANIFEST = os.path.join(os.path.dirname(HUB), 'manifest.json')   # 真身在 skills 目录（与 conductor 同源）
CONTRACTS = os.path.join(HUB, 'contracts.json')

# 让三个脚本能 import core（共享效率核心：同一模块实例 → 缓存/并行池复用）
sys.path.insert(0, HUB)


_MOD_CACHE = {}
_MOD_ERR = {}      # path -> '类型: 消息'（加载失败原因，S176b；查「为什么降级」只能靠它）

def get_module(path):
    """惰性加载模块（首次调用才 import），失败返回 None（降级，不让单模块缺失拖垮整个入口）。
    S069：原为顶层 eager load——任一技能脚本缺失（如 interface/scripts/pipeline.py 被移走）
    会让 `tvbox` 启动即 FileNotFoundError 崩溃，连其它健康技能的命令都用不了。

    ★ S176b 修真缺陷：原 `except Exception: m = None` 把**失败原因整个丢掉**——
    调用方只看到「加载失败」，查不出为什么失败（实测时就卡在这里：
    signal-check 报 2 条降级，却没有任何信息能定位是哪个模块、什么异常）。
    这是 M-R60「读失败不得折叠」的违反：失败信息不是噪声，是唯一的排查线索。
    现在把 `类型: 消息` 记进 _MOD_ERR，由 safe_dispatch 一并打印。
    """
    if path in _MOD_CACHE:
        return _MOD_CACHE[path]
    try:
        name = 'tvb_' + os.path.basename(path).replace('.', '_')
        spec = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _MOD_ERR.pop(path, None)
    except Exception as e:
        m = None
        _MOD_ERR[path] = f'{type(e).__name__}: {str(e)[:200]}'
    _MOD_CACHE[path] = m
    return m

# 动词 → 模块路径（惰性：调 get_module 时才真正加载）
ROUTE = {
    'brief': LINK, 'status': LINK, 'learn': LINK, 'log': LINK,
    'sync': LINK, 'audit': LINK, 'snapshot': LINK, 'ingest': LINK,
    'eco': LINK, 'lib-learn': LINK, 'lib-log': LINK, 'sizes': LINK,
    'block': BLK, 'evolve': EVO, 'watchdog': DOG, 'conductor': CON,
    'pipe': IPL, 'probe': PRB,
    # S136 M-R81「收尾唯一入口」：九技能共用同一条收尾命令
    'close': UNF, 'finish': UNF,
    # S148：补齐监察司/清道夫（此前 manifest 与 SKILL.md 都写了，入口却没有）
    'inspect': INS, 'janitor': JAN,
    'ledger': LED,      # S156 账房司：总账/账户/对账/平账/试算/终验
    # S149：体积写后即测（M-R77 拦截点前移）。
    # **别只把它登记成 `tvbox conductor size`**——用户与我会本能敲 `tvbox size`，
    # 入口没有这个词就报「未知子命令」，等于又造一条幽灵命令（M-R105 正是防这个）。
    'size': CON,
    # S153：会话闸门。**刻意做成短命令**——它是「写库前必先想一下」的动作，
    # 名字一长就会被跳过，那它就退化成又一个没人跑的入口（brief 的三个提示就是前车之鉴）。
    'gate': CON,
    # S172：学习工坊。命令就是 `tvbox study <intake|sort|gate|forge|store|watch|self-check>`
    'study': LRN,
    # S176c：`tvbox now` 无子命令、无参数，纯只读快照。
    'now': ACT,
}

# link 系动词本身就是 link.py 的子命令，需要补回；block/evolve/watchdog 系动词仅是选择器
LINK_VERBS = {'brief', 'status', 'learn', 'log', 'sync', 'audit', 'snapshot',
              'ingest', 'eco', 'lib-learn', 'lib-log', 'sizes'}
# S149：`size` 也必须把动词本身传进 conductor（它是 conductor 的子命令，不是选择器）。
# 漏了这条的后果：`tvbox size` → conductor 收到空 argv → 打印用法；`tvbox size probe-hub`
# → 收到 ['probe-hub'] → 报「未知命令: probe-hub」。**路由通了但参数错位**，
# 比"未知子命令"更难看出来（命令确实被执行了，只是执行错了）。
VERB_KEEP = LINK_VERBS | {'size', 'gate'}


def mod_argv(verb, rest):
    """计算传给模块 dispatch 的 argv。"""
    return [verb] + list(rest) if verb in VERB_KEEP else list(rest)

HELP = """📡 TVBox 技能生态统一入口（技能↔脚本联动中枢）

  单命令      : tvbox <brief|status|learn|log|sync|audit|snapshot|ingest|eco|lib-learn|lib-log>
  黑名单      : tvbox block <mark|check|guard|list|stats|review>
  进化        : tvbox evolve <recurring|create|scan|upgrade|review|note|mine|lend|review-lend>   # mine=抽招式 lend=查可借鉴 review-lend=对照对账（规则57）
  遗留监测    : tvbox watchdog <scan|status|report|monitors|add-monitor>   # 历史遗留+时效项
  需求管家    : tvbox conductor ask "<需求>"        # ★单一入口：随便怎么问，术语翻译+历史呼应+路由+拆解
  历史清单    : tvbox conductor history ["<关键词>"] # ★查"我以前做过什么、当时交付啥"
  生态指挥    : tvbox conductor <plan|sprint|speed|whoami|ask|lanes|assess|direction>  # 提速+协调+进化
  探源司      : tvbox probe <monitor|search|harvest|livecheck|token|limits|self-check>  # 上游监测/找源/采线路/真实测活（harvest promote 入成品源库；harvest prune 剔失效产备用版；livecheck 点播拉片/直播拉流）
  监察司      : tvbox inspect <scan|status|monitors|add-monitor|signal-check|balance>  # 生态监测总控：自监测+库监测+上游
  清道夫      : tvbox janitor <scan|list|inbox|add>      # 待删收件箱（人工清理投递）
  账房        : tvbox ledger <status|balance|reconcile|settle|finalcheck|post|writeoff|selftest>  # 复式记账·账实账账对账·平账·终验卡口
  ★统一调用   : tvbox ledger call <动词> [参数…]   # ★跨界调一次（对话/任务/场景/生态四界，自动入账）
                tvbox ledger verbs                 # 14 个调用动词（不知道敲什么先敲这个）
                tvbox ledger relay <链路名>         # 跨场景接力：逐步跑 + 逐步入账
                tvbox ledger map|audit-call|metrics # 谁能调谁 / 三方一致性体检 / 调用业态
  体积        : tvbox size [技能名|all]              # SKILL.md 体积写后即测（改完立刻跑；超标 exit=1）
  闸门        : tvbox gate [status|open <意图>|close] # 写库闸门：未跑 conductor ask 开工即拒绝写入（S153）
  串联(高效)  : tvbox run <v1> <v2> ...            # 同进程共享缓存
  并行(最快)  : tvbox p "<v1>" "<v2>" ...          # 多命令并发，各带标题
  任务链(一键): tvbox chain <名称>                  # 跨技能链：命令步骤自动跑，flow步骤提示按flow.json执行
  收尾(必用)  : tvbox close [--dry|status|mine|done|wontfix|defer|verify|report|promise|link]
                                                  # ★S136 九技能共用收尾入口：六源采集→对账→派单→落账→四向验证
  现在在干什么: tvbox now                           # 后台任务/最近动作/门禁实况 一屏（看不清进度时敲它）
  联动清单    : tvbox manifest                      # 意图→命令 + 任务链
  联动契约    : tvbox contracts                     # 上下游数据格式契约（规则54）
  发现页      : tvbox discover                      # 五域接入状态一屏（资料库/技能/连接器/专家/项目）
  技能域      : tvbox skills [list|自建名]           # S165：66 个技能清单与健康指针
  连接器域    : tvbox connectors                    # S165：MCP 连接器实况（mcporter）
  定时任务域  : tvbox jobs [id]                     # S165：任务列表+健康，停用项提示清理
  资料库域    : tvbox library [spaces|nodes <id>|table <id>]  # S165：空间/节点/表结构只读探查

  示例        : tvbox chain 开工速览
              : tvbox chain 新接口全流程
              : tvbox run brief status
              : tvbox p "block stats" "evolve scan" "eco"
"""


def load_manifest():
    try:
        return json.load(open(MANIFEST, encoding='utf-8'))
    except Exception:
        return {}


def _skills_scan():
    """技能域实况。**判据与 version_guard.collect 逐字一致**（M-R38 单一事实源）：
      生态自建 = 有 SKILL.md 且有带版本号的 CHANGELOG.md（守卫只对这类三重对账）
      平台技能 = 其余有 SKILL.md 的目录（含 skill_NNN 内置与插件市场装，不归我们管）
    ★ 首版用「具名目录」当自建 → 20 对 12 对不上（sites 等被冒充成技能）；
      二版用「有 SKILL.md」→ 还是 20 对 12（skill_NNN 也有 SKILL.md）。
      两次都错在同一处：**自己发明判据，而不是抄守卫的**。"""
    import re as _re
    root = '/root/.codebuddy/skills'

    def _ver(p, pat):
        try:
            m = _re.search(pat, open(p, encoding='utf-8').read(4000), _re.M)
            return m.group(1) if m else None
        except Exception:
            return None

    self_built, platform = [], 0
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if not os.path.isdir(p):
            continue
        sp = os.path.join(p, 'SKILL.md')
        if not os.path.exists(sp):
            continue
        cp = os.path.join(p, 'CHANGELOG.md')
        v_cl = _ver(cp, r'(?m)^##\s+(?:\d{4}-\d{2}-\d{2}\s+)?v?([0-9]+\.[0-9]+\.[0-9]+)') if os.path.exists(cp) else None
        if v_cl:
            self_built.append((d, _ver(sp, r'(?m)^version:\s*["\']?([0-9]+\.[0-9]+\.[0-9]+)') or '?'))
        else:
            platform += 1
    return self_built, platform


def _version_guard_tail():
    """跑版本守卫取结论行（只取尾部，别把 12 行表打满屏）。"""
    import subprocess as _sp
    try:
        r = _sp.run([sys.executable,
                     '/root/.codebuddy/skills/ecosys-conductor/scripts/version_guard.py'],
                    capture_output=True, text=True, timeout=120)
        out = (r.stdout or '') + (r.stderr or '')
        for line in out.splitlines():
            if '全部一致' in line or '不一致' in line:
                return r.returncode, line.strip()
        return r.returncode, '（无结论行——输出格式变了？）'
    except Exception as e:
        return 2, f'无法判定（{type(e).__name__}: {str(e)[:60]}）'


def _skill_meta(d, peers):
    """读单个自建技能的元数据：体积、脚本数、依赖面。

    ★ 依赖必须扫**代码层**，不能只扫自己的 SKILL.md 正文（S165 深挖踩到）：
      首版只在自己的 SKILL.md 里找同伴名 → `eco-ledger` 被判「没人依赖」，
      可它明明被 `tvbox` 路由（LED）和 conductor 的归口表调用。
      **文档里没写 ≠ 没人用；代码里有引用才是真依赖。**
    扫三处真身：/usr/local/bin/tvbox（路由）、conductor.py（归口）、eco-ledger 的 call_bus（调用动词）。
    """
    p = '/root/.codebuddy/skills/' + d
    size = 0
    txt = ''
    try:
        txt = open(os.path.join(p, 'SKILL.md'), encoding='utf-8').read()
        size = len(txt.encode())
    except Exception:
        pass
    scripts = 0
    sd = os.path.join(p, 'scripts')
    if os.path.isdir(sd):
        scripts = len([f for f in os.listdir(sd) if f.endswith(('.py', '.sh'))])
    return {'size': size, 'scripts': scripts,
            'deps': sorted({x for x in peers if x != d and x in txt})}


_CALLER_FILES = (
    '/usr/local/bin/tvbox',
    '/root/.codebuddy/skills/ecosys-conductor/scripts/conductor.py',
    '/root/.codebuddy/skills/eco-ledger/scripts/call_bus.py',
    '/root/.codebuddy/skills/tvbox-source-hub/scripts/link.py',
)


def _skill_users(tgt, peers):
    """谁在**代码里**引用它。返回 (code_users, doc_users)，两者分开列出——
    只有文档提到、代码没引用 = 「写了但没接上」，那正是 L123 那族缺陷。"""
    code, doc = set(), set()
    for f in _CALLER_FILES:
        try:
            t = open(f, encoding='utf-8', errors='ignore').read()
        except Exception:
            continue
        if tgt in t:
            code.add(os.path.basename(f))
    for d in peers:
        if d == tgt:
            continue
        try:
            if tgt in open('/root/.codebuddy/skills/' + d + '/SKILL.md',
                           encoding='utf-8', errors='ignore').read():
                doc.add(d)
        except Exception:
            pass
    return sorted(code), sorted(doc)


def cmd_skills(rest):
    """S165 技能域入口：发现页「技能」的真实数据面。
    缺省=总览；list=全清单；why <名>=依赖与被依赖；<自建名>=单技能详情。"""
    named, platform = _skills_scan()
    peers = [x[0] for x in named]
    sub = rest[0] if rest else ''
    if sub == 'list':
        print(f'技能共 {len(named) + platform} 个：自建 {len(named)} + 平台安装 {platform}')
        print()
        for d, v in named:
            m = _skill_meta(d, peers)
            print(f'  {d:<28} v{v:<9} {m["size"]:>6}B  脚本 {m["scripts"]:>2}')
        return 0
    if sub == 'why':
        if len(rest) < 2:
            print('❌ why 缺参数：需要 <技能名>')
            print('   正确用法: tvbox skills why <技能名>　（看它依赖谁、又被谁依赖）')
            return 2
        tgt = rest[1]
        if tgt not in peers:
            print(f'❌ 没有这个自建技能：{tgt}')
            near = [d for d in peers if tgt in d or d in tgt][:6]
            if near:
                print(f'   你是不是想: {" / ".join(near)}')
            return 2
        mine = _skill_meta(tgt, peers)['deps']
        code, doc = _skill_users(tgt, peers)
        print(f'🔗 {tgt} 的依赖关系')
        print(f'   它依赖  : {" / ".join(mine) if mine else "（无，是独立底座）"}')
        print(f'   代码引用: {" / ".join(code) if code else "（无——没接进任何入口）"}')
        print(f'   文档提及: {" / ".join(doc) if doc else "（无）"}')
        if doc and not code:
            print('   ⚠️ 只有文档提到、代码没引用——「写了但没接上」是这族缺陷的典型形态')
        elif not code and not doc:
            print('   ⚠️ 完全没人引——它是孤岛，或者还没接进去')
        return 0
    if sub:
        hit = [x for x in named if x[0] == sub]
        if not hit:
            print(f'❌ 没有这个自建技能：{sub}')
            near = [d for d in peers if sub in d or d in sub][:6]
            if near:
                print(f'   你是不是想: {" / ".join(near)}')
            print(f'   平台技能不在本清单内（共 {platform} 个），看全部: tvbox skills list')
            return 2
        m = _skill_meta(sub, peers)
        print(f'  {sub}  v{hit[0][1]}')
        print(f'   体积: {m["size"]}B ｜ 脚本: {m["scripts"]} 个 ｜ 依赖: {" / ".join(m["deps"]) or "无"}')
        print(f'   体积详情: tvbox size {sub} ｜ 依赖面: tvbox skills why {sub}')
        return 0
    # 总览
    print(f'🧩 技能域（发现页·技能）')
    print(f'   数量: {len(named) + platform} 个技能 = 生态自建 {len(named)}（守卫盯着） + 平台 {platform}')
    rc, line = _version_guard_tail()
    mark = '✅' if rc == 0 else ('❌' if rc == 1 else '❓')
    print(f'   版本一致性: {mark} {line}')
    print(f'   体积健康  : tvbox size all（写后即测，超标 exit=1）')
    print(f'   全清单    : tvbox skills list ｜ 依赖面: tvbox skills why <名>')
    return 0 if rc == 0 else 1


def cmd_connectors(rest):
    """S165 连接器域入口：发现页「连接器」的真实数据面（MCP server 实况）。
    三态：有 / 没有 / 通路坏（M-R60，不折叠）。"""
    import subprocess as _sp
    try:
        r = _sp.run(['mcporter', 'list'], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        print('❓ mcporter 未安装——连接器域无法判定（不是"没有连接器"）')
        return 2
    except Exception as e:
        print(f'❓ mcporter 调用异常（{type(e).__name__}: {str(e)[:60]}）——无法判定')
        return 2
    out = (r.stdout or '').strip()
    if 'No MCP servers configured' in out:
        print('🔗 连接器域（发现页·连接器）')
        print('   MCP server: 0 个（mcporter 实况）')
        print('   接入方法  : mcporter add <配置>（或平台「连接器」页添加，沙箱内 mcporter list 可见）')
        return 0
    if r.returncode != 0:
        print(f'❌ mcporter 退出码 {r.returncode}：{(r.stderr or out)[:120]}')
        return 1
    print('🔗 连接器域（发现页·连接器）')
    print(out)
    return 0


def cmd_library(rest):
    """S165 资料库域深度入口（发现页「资料库」）。

    ★ 为什么另开 `tvbox library` 而不是往 link.py 加子命令：
      link.py 已经有 17 个子命令，再塞会把它变成一个什么都装的抽屉。
      这里只做「**只读结构探查**」——空间/节点/表/内容，写操作仍走 lib-log 那一条。

    现状差距（本轮深挖实测）：生态此前只接了 ④经验库**一张表**，
    而个人空间实际有 65 个节点、4 个可访问空间。等于资料库接了 1/65。
    """
    import subprocess as _sp
    LIB = '/root/.codebuddy/skills/library'

    def _api(name, *args):
        try:
            r = _sp.run([sys.executable, 'space_api.py', name, *args],
                        capture_output=True, text=True, cwd=LIB, timeout=60)
        except Exception as e:
            return None, f'通路异常（{type(e).__name__}: {str(e)[:60]}）'
        try:
            d = json.loads(r.stdout or '{}')
        except Exception:
            return None, f'返回非 JSON：{(r.stdout or r.stderr or "")[:120]}'
        if 'error' in d:
            return None, str(d['error'])[:160]
        return d.get('data') or {}, ''

    sub = rest[0] if rest else ''
    if sub == 'spaces':
        d, err = _api('space.workspace.list-user-spaces')
        if d is None:
            print(f'❌ 取空间列表失败：{err}')
            return 1
        ss = d.get('spaces') or []
        print(f'📚 我的资料库空间（{len(ss)} 个）')
        for s in ss:
            role = {'owner': '所有者', 'reader': '只读', 'writer': '可写'}.get(s.get('role'), s.get('role'))
            print(f'   {s.get("spaceId")}  {role:<5}  {s.get("title") or "（无标题）"}')
        print()
        print('   看空间内容: tvbox library nodes <spaceId>')
        return 0
    if sub == 'nodes':
        if len(rest) < 2:
            print('❌ nodes 缺参数：需要 <spaceId>')
            print('   正确用法: tvbox library nodes <spaceId>　（先 library spaces 拿 id）')
            return 2
        sid = rest[1]
        d, err = _api('space.workspace.list-nodes', '--space-id', sid, '--limit', '100')
        if d is None:
            print(f'❌ 列节点失败：{err}')
            return 1
        ns = d.get('nodes') or []
        print(f'📂 空间 {sid}：{d.get("total")} 个节点'
              f'{"（只显示前 %d）" % len(ns) if d.get("hasMore") else ""}')
        kinds = {}
        for n in ns:
            k = n.get('kind') or '?'
            kinds[k] = kinds.get(k, 0) + 1
        print('   类型分布: ' + ' ｜ '.join(f'{k} {v}' for k, v in sorted(kinds.items())))
        print()
        for n in ns[:30]:
            mark = {'database': '📊', 'doc': '📄', 'folder': '📁', 'page': '🌐'}.get(n.get('kind'), '·')
            print(f'   {mark} {n.get("id")}  {n.get("title")}')
        if len(ns) > 30:
            print(f'   …还有 {len(ns) - 30} 个（本视图只列前 30）')
        print()
        print('   看表结构: tvbox library table <nodeId>')
        return 0
    if sub == 'table':
        if len(rest) < 2:
            print('❌ table 缺参数：需要 <数据库节点 id>')
            print('   正确用法: tvbox library table <id>　（先 library nodes 拿 id）')
            return 2
        d, err = _api('space.database.get-database', '--database-id', rest[1])
        if d is None:
            print(f'❌ 取表结构失败：{err}')
            return 1
        props = d.get('properties') or []
        print(f'📊 {d.get("title")}（{len(props)} 个字段）')
        for p in props:
            cfg = ''
            opts = ((p.get('config') or {}).get('options') or [])
            if opts:
                cfg = ' 选项: ' + ' / '.join(o.get('text', '') for o in opts[:8])
            print(f'   {p.get("name"):<14} {p.get("type"):<10}{cfg}')
        print()
        print('   读记录: 走 ④经验库那一套（core.rows_of）；写记录: tvbox lib-log')
        return 0
    # 缺省：总览
    d, err = _api('space.workspace.list-user-spaces')
    if d is None:
        print(f'❌ 取空间失败：{err}')
        print('   （不是"没有空间"——通路坏了，别折叠成空）')
        return 1
    ss = d.get('spaces') or []
    # 动态数节点：拿每个空间的 total 求和，别把数字写死在文案里
    total_nodes, reachable = 0, 0
    for s in ss:
        dd, _ = _api('space.workspace.list-nodes',
                     '--space-id', s.get('spaceId', ''), '--limit', '1')
        if dd and isinstance(dd.get('total'), int):
            total_nodes += dd['total']
            reachable += 1
    print('📚 资料库域（发现页·资料库）')
    print(f'   可访问空间: {len(ss)} 个（团队 {len([s for s in ss if s.get("category") == "team"])} 个）'
          f'，节点合计 {total_nodes} 个（{reachable}/{len(ss)} 个空间可读）')
    # ★ 这里千万别写死"已接入 N 张表"（本轮踩到）：我写了「1 张表 / 99 节点」，
    #   一查发现 6 张表里 5 张都有代码引用。**写死的说明句会替事实撒谎**，
    #   所以接入面一律现算：拿节点 id 去 skills/*/scripts/*.py 里搜引用。
    tables = []
    if ss:
        dd, _ = _api('space.workspace.list-nodes',
                     '--space-id', ss[0].get('spaceId', ''), '--limit', '100')
        for n in (dd or {}).get('nodes') or []:
            if n.get('kind') == 'database':
                tables.append(n)
    if tables:
        wired = 0
        import glob as _glob
        srcs = []
        for f in _glob.glob('/root/.codebuddy/skills/*/scripts/*.py'):
            try:
                srcs.append(open(f, encoding='utf-8', errors='ignore').read())
            except Exception:
                pass
        blob = '\n'.join(srcs)
        wired = sum(1 for t in tables if t.get('id') in blob)
        print(f'   数据表    : {len(tables)} 张，其中 {wired} 张已被代码引用'
              f'（{len(tables) - wired} 张是断的）')
        for t in tables:
            ok = '✅' if t.get('id') in blob else '⚠️'
            print(f'      {ok} {t.get("title")}')
    print(f'   经验库    : lib-learn 查 ｜ lib-log 写（④平台经验库这条路是通的）')
    print()
    print('   空间列表: tvbox library spaces')
    print('   空间内容: tvbox library nodes <spaceId>')
    print('   表结构  : tvbox library table <nodeId>')
    return 0


def cmd_jobs(rest):
    """S165 定时任务域入口（发现页「任务」+「定时任务」两个 tab）。

    ★ 为什么必须有它：全量扫了一遍「谁是孤岛」，12 个自建技能里
      **只有 automation-task-manager 一条代码引用都没有**——`tvbox` 里连 scheduler
      五个字都没出现。可它恰恰是最常用的那个：今天两轮压测全靠它建任务。
      「工具做好却接不到入口上」这族缺陷（L123）的又一例，只不过这次躺了两周没人发现。

    子命令：缺省=list（列表+健康摘要）；<id>=单任务详情（含 prompt 全文指针）。
    退出码：0 成功 / 1 API 失败 / 2 用法错。不做「失败当空列表」的折叠。
    """
    import subprocess as _sp
    API = '/root/.codebuddy/skills/automation-task-manager/scripts/scheduler-api.sh'

    def _api(*a):
        """返回 (dict|None, err)。err 以 'NOTFOUND|' 前缀区分「对象不存在」与「通路坏」——
        两者都返回失败，但处置完全不同：前者是用法错（2），后者是环境错（1）。
        不区分就会让人对着一个不存在的 id 反复重试。"""
        try:
            r = _sp.run(['bash', API, *a], capture_output=True, text=True, timeout=60)
        except Exception as e:
            return None, f'调度 API 不可达（{type(e).__name__}: {str(e)[:60]}）'
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or '')
            if 'TASK_NOT_FOUND' in msg or 'HTTP 404' in msg:
                return None, 'NOTFOUND|' + msg[:160]
            return None, msg[:200]
        try:
            return json.loads(r.stdout), ''
        except Exception:
            return None, f'返回非 JSON：{(r.stdout or "")[:100]}'

    sub = rest[0] if rest else ''
    if sub:
        d, err = _api('get', '--id', sub)
        if not d:
            if err.startswith('NOTFOUND|'):
                print(f'❌ 没有这个任务：{sub}')
                print('   看现有任务: tvbox jobs')
                return 2
            print(f'❌ 查任务 {sub} 失败：{err}')
            return 1
        t = d.get('data') or {}
        if not t:
            print(f'❌ 没有这个任务：{sub}')
            return 2
        st = '启用' if t.get('status') == 1 else '停用'
        print(f'⏰ {t.get("name")}（{sub}）')
        print(f'   频率: {t.get("frequencyType")} ｜ {t.get("scheduleDesc")} ｜ {st}')
        print(f'   下次: {t.get("nextRunAt") or "-"} ｜ 上次: {t.get("lastRunAt") or "从未"}')
        print(f'   超时: {t.get("timeoutSec")}s ｜ 重试: {t.get("retryCount")} 次'
              f'（间隔 {t.get("retryIntervalSec")}s）')
        p = ((t.get('agentConfig') or {}).get('prompt') or '').replace('\n', ' ')
        print(f'   指令: {p[:120]}{"…" if len(p) > 120 else ""}')
        return 0

    d, err = _api('list', '--page-size', '50')
    if not d:
        print(f'❌ 取任务列表失败：{err}')
        print('   （不是"没有任务"——通路坏了，别折叠成空列表）')
        return 1
    ts = ((d.get('data') or {}).get('tasks')) or []
    on = [t for t in ts if t.get('status') == 1]
    off = [t for t in ts if t.get('status') != 1]
    print('⏰ 定时任务域')
    print(f'   共 {len(ts)} 个：启用 {len(on)} ｜ 停用 {len(off)}')
    if off:
        print(f'   ⚠️ 停用的 {len(off)} 个里，如果是一次性任务跑完的，建议清掉：')
        for t in off:
            print(f'      {t["id"]}  {t["name"]}（{t.get("frequencyType")}，上次 '
                  f'{t.get("lastRunAt") or "从未"}）')
    print()
    for t in sorted(on, key=lambda x: x.get('nextRunAt') or ''):
        print(f'   {t["id"]}  {t["name"][:34]:<36} 下次 {t.get("nextRunAt") or "-"}')
    print()
    print('   详情: tvbox jobs <id> ｜ 建/改/删: automation-task-manager 的 scheduler-api.sh')
    print('   退出码可作门禁：scheduler-api.sh 参数非法 exit 1、create 参数被改写 exit 3')
    return 0


def cmd_discover(rest):
    """S165 发现页镜像：七个域的接入状态一屏，全部真实探测、不靠记忆写死。

    ★ 为什么从「五域」变「七域」：深挖时扫了一遍代码引用，发现
      `automation-task-manager`（定时任务）**一条引用都没有**——它是孤岛，
      可它恰恰是最常用的那个（今天两轮压测全靠它）。发现页上也没有它的位置。
      「页面列表里没有」不等于「用不上」，所以这里按**实际能接入的东西**列，不是照抄 App 菜单。

    退出码 0（状态视图不是门禁）；各域自己的命令才各自带码。
    """
    import subprocess as _sp
    named, platform = _skills_scan()
    peers = [x[0] for x in named]
    print('🔎 发现页 × 生态接入状态')
    print()

    # ── 资料库：现算，不写死 ──
    lib_line = '⚠️ 通路不可达'
    try:
        r = _sp.run([sys.executable, 'space_api.py', 'space.workspace.list-user-spaces'],
                    capture_output=True, text=True, cwd='/root/.codebuddy/skills/library', timeout=60)
        d = json.loads(r.stdout or '{}').get('data') or {}
        ns = d.get('spaces') or []
        lib_line = f'✅ {len(ns)} 个空间可读 → tvbox library'
    except Exception:
        pass
    print(f'  📚 资料库  {lib_line}')

    # ── 技能：数量 + 孤岛 ──
    print(f'  🧩 技能    ✅ {len(named) + platform} 个（生态自建 {len(named)}）→ tvbox skills')
    rc, line = _version_guard_tail()
    mark = '✅' if rc == 0 else ('❌' if rc == 1 else '❓')
    print(f'             版本: {mark} {line}')
    orphans = [d for d in peers if not _skill_users(d, peers)[0]]
    if orphans:
        print(f'             ⚠️ 代码没人引用（孤岛）: {" / ".join(orphans)}')

    # ── 连接器 ──
    try:
        r = _sp.run(['mcporter', 'list'], capture_output=True, text=True, timeout=30)
        empty = 'No MCP servers configured' in (r.stdout or '')
        print(f'  🔗 连接器  ✅ {"0 个 MCP server（未配置）" if empty else "有配置"} → tvbox connectors')
    except Exception:
        print('  🔗 连接器  ❓ mcporter 不可达——无法判定')

    # ── 定时任务 ──
    try:
        r = _sp.run(['bash', '/root/.codebuddy/skills/automation-task-manager/scripts/scheduler-api.sh',
                     'list', '--page-size', '50'], capture_output=True, text=True, timeout=60)
        ts = (json.loads(r.stdout or '{}').get('data') or {}).get('tasks') or []
        print(f'  ⏰ 定时任务  ✅ {len(ts)} 个（启用 {len([t for t in ts if t.get("status") == 1])}）→ tvbox jobs')
    except Exception:
        print('  ⏰ 定时任务  ❓ 调度 API 不可达——无法判定')

    print('  👤 专家    ⚠️ 无数据 API（auth.proxy 白名单外，实测 403）')
    print('             设备页可开：「发现→专家」（沙箱侧暂无读取通路，见 M40）')
    print('  📁 项目    ⚠️ 无数据 API（同上）；openPage 亦无 project 路由')
    print('             项目 ID 经 env X_PROJECT_ID 注入（本沙箱未设，见 M40）')
    print()
    print('  判定依据见各域命令——本视图只报实况，不把「没接上」折叠成「已接上」。')
    return 0


def cmd_manifest():
    m = load_manifest()
    if not m:
        print("  (manifest.json 缺失)")
        return
    print("🗺️  技能↔脚本 联动清单（意图 → 命令）\n")
    for r in m.get('commands', []):
        print(f"  {r.get('intent',''):34} →  {r.get('cmd','')}")
    chains = m.get('chains', [])
    if chains:
        print("\n🔗 跨技能任务链（tvbox chain <名称> 一键执行）\n")
        for c in chains:
            print(f"  {c.get('name',''):12} {c.get('scene','')}")
            for s in c.get('steps', []):
                if s.get('t') == 'cmd':
                    print(f"      $ tvbox {s.get('v','')}")
                else:
                    print(f"      ▶ {s.get('v','')}")


def cmd_contracts():
    try:
        d = json.load(open(CONTRACTS, encoding='utf-8'))
    except Exception:
        print('(contracts.json 缺失)')
        return
    print(f"📜 联动契约 {d.get('name','')} v{d.get('version','')}（{d.get('source','')}）\n")
    for c in d.get('contracts', []):
        print(f"  【{c.get('id','')}】{c.get('title','')}")
        print(f"    产出: {c.get('producer','')}")
        print(f"    消费: {c.get('consumer','')}")
        if c.get('cmd'):
            print(f"    命令: {c['cmd']}")
        if c.get('verify'):
            print(f"    验证: {c['verify']}")
        if c.get('rule'):
            print(f"    铁律: {c['rule']}")
        print()


def cmd_chain(name):
    m = load_manifest()
    chains = {c.get('name'): c for c in m.get('chains', [])}
    if name not in chains:
        print(f'未知任务链: {name}')
        print('可用: ' + ' / '.join(chains) if chains else '(无)')
        return
    c = chains[name]
    print(f"🔗 任务链「{name}」—— {c.get('scene','')}\n")
    t0 = time.time()
    skipped = 0
    for s in c.get('steps', []):
        if s.get('t') == 'cmd':
            v = s.get('v', '')
            # 占位符检测（S023 实证）：<候选文件> 等占位步骤直接执行必失败（找不到文件/空参数），
            # 静默失败比报错更坏——显式跳过并给出替换指引
            placeholders = [w for w in v.split() if w.startswith('<') and w.endswith('>')]
            if placeholders:
                skipped += 1
                print(f'── ⏭️  tvbox {v}')
                print(f'      （含占位符 {" ".join(placeholders)}——替换为实际值后手动执行）')
                continue
            parts = v.split()
            verb, rest = parts[0], parts[1:]
            if verb in ('run', 'p'):
                # 链内嵌套 run/p：展开执行
                print(f'── $ tvbox {v}')
                (cmd_run if verb == 'run' else cmd_parallel)(rest)
                continue
            if verb not in ROUTE:
                print(f'── $ tvbox {v}  ⚠️ 未知子命令，跳过')
                continue
            print(f'── $ tvbox {v}')
            _dispatch(verb, rest)
        else:
            print(f'── ▶ {s.get("v","")}（流程步骤，按 {s.get("ref","对应 flow.json")} 执行）')
    print(f'\n⏱  链「{name}」命令步骤完成，用时 {time.time()-t0:.1f}s'
          + (f'（{skipped} 个占位步骤已跳过）' if skipped else ''))
    print(f'✅ 验收判据：{c.get("accept","")}')


def _as_exit_code(ret):
    """返回值 → 退出码。**唯一一处**做这个判断，别处禁止重复实现（M-R38/M-R85）。

    取值域必须先定义（S136 五续教训）：只有 `int` 才可能是退出码；
    `bool` 是 int 的子类但语义是数据，必须排除；列表/字典/字符串/None 全是**数据**。
    首版写成 `ret or 0`，把 watchdog gain/noise/expand 返回的结果列表误判成失败，
    一次冒出 16 条假异常——「失败被当成功」修完，紧接着又造出「成功被当失败」。

    S138 例外（这条把上面那句补完整）：**False/True 不是数据，是判定结论**。
    排除 bool 的原意是防「列表当码」，而 `cmd_verify` 这类门禁命令的正确语义恰恰是
    `return passed == n` —— 返回布尔。实测踩到：verify 判 8/9 不通过后 exit 仍是 0，
    门禁报红、门却开着，`--strict-exit` 也拦不住。
    故约定：True → 0；False → 1。这是唯一允许的 bool→码映射，因为 False 处在这个位置上
    只有一个意思——「本次判定未通过」，不可能与「数据为空」混淆。
    """
    if isinstance(ret, bool):
        return 0 if ret else 1
    if isinstance(ret, int):
        return ret
    return 0


class Verdict:
    """一次派发的**完整判定证据**，不可变，随返回值走，不落任何全局（S138）。

    为什么要把三个模块全局（_EXIT_CODE/_ERRS/_RC）整体废掉：
    它们是「共享可变状态」，正确性依赖两条**靠人记住**的纪律——
      ① 每次调用必须重置全部字段；② 读取必须按固定顺序（exit 优先于 rc）。
    两次都栽在这：
      · S137 漏了 `_RC` 重置 → 上次的失败被算到这次头上（审计 ④ 抓出 3 条 false ok:false）；
      · 判定散在两处（safe_dispatch 写 / _dispatch 读），新增一个字段就多一个漏点。
    **根因不是「忘了重置」，是「允许有状态可读」**。改成纯函数后：
    证据在局部变量里攒齐 → 一次判定 → 结果随返回值传出。
    没有全局 = 没有残留 = 没有读错顺序的可能，也不需要「记得」任何事。

    证据三类（M-R87：判定须唯一、有证据）：
      · exit_code  真实退出码（SystemExit 捕获 / 模块缺失 127）
      · rc         dispatch 的**返回值**归一成的码（技能用 return 非0 报错）
      · err        异常类型+摘要（非空即失败）
    """
    __slots__ = ('exit_code', 'rc', 'err', 'ok')

    def __init__(self, exit_code=0, rc=0, err=''):
        self.exit_code = int(exit_code or 0)
        self.rc = int(rc or 0)
        self.err = err or ''
        # **唯一判定点**：任一证据表明失败即失败。判定规则只写在这里一次。
        if self.err:
            self.ok = False
        elif self.exit_code != 0:
            self.ok = False
        elif self.rc != 0:
            self.ok = False
            self.err = f'dispatch 返回 {self.rc}（int 非0，命令自报失败）'
        else:
            self.ok = True

    def reason(self):
        """失败原因（成功时为空串）——供审计显示证据，避免「须复核」而无通道。"""
        return self.err

    def __repr__(self):
        return (f'Verdict(ok={self.ok}, exit={self.exit_code}, rc={self.rc}, '
                f'err={self.err!r})')


def safe_dispatch(module, argv, quiet=False):
    """执行一次 dispatch，**返回 Verdict**（纯：不写任何全局状态）。

    历史两次方向性错误都记在这里，作为「为什么必须这样写」的证据：
      · S136 四续：`except SystemExit: pass` 把退出码一起吞了 → `exit(1)`（发现风险）
        在 journal 里记成 ok=True，**发现风险反记成功**；
      · S136 五续：改用 `ret or 0` 判返回值 → 查询类命令返回的结果列表被当非0码，
        16 条正常执行记成异常，**成功反记失败**（错误换了个方向）。

    ★ S176b `quiet`：探针会**故意**传 `None` / 抛假异常来验证判定方向（见
    conductor 里「模块缺失必须显式失败(127)」那条用例）。那些是测试手段，
    不是生态故障——但原实现把它们的告警原样打进 stdout，用户看到的是
    「❌ 命令异常终止 / ⚠️ 模块加载失败」各两条，像出了两次事故。
    注入噪声必须留在探针内部：诊断信息照常记进 Verdict（可断言），
    只是不往用户屏幕上喷。
    """
    if module is None:
        _p = next((k for k, v in _MOD_CACHE.items() if v is None), '?')
        _why = _MOD_ERR.get(_p, '未记录（非加载失败：可能是探针故意传入 None）')
        if not quiet:
            print(f'  ⚠️ 模块加载失败（降级跳过，不影响其它命令）\n'
                  f'     模块: {_p}\n'
                  f'     原因: {_why}')
        return Verdict(exit_code=127, err=f'模块加载失败：{_p}｜{_why}')

    exit_code, err = 0, ''
    try:
        # 技能有两种报错风格：抛异常 / return 非0。两种都必须看得到。
        ret = module.dispatch(list(argv))
        rc = _as_exit_code(ret)
    except SystemExit as e:
        c = e.code
        exit_code = 0 if c is None else (c if isinstance(c, int) else 1)
        rc = 0
    except Exception as e:
        # 真崩了：带类型+摘要留痕，否则审计只能说「须复核」而永远复核不出真因
        exit_code, rc = 1, 1
        err = f'{type(e).__name__}: {str(e)[:200]}'
        # **S154 修：exception 必须「看得见」，不只是「记得下」。**
        # 实证：`tvbox block guard --candidates <字段写错的json>` → stdout 与 stderr
        # **双双 0 字节**。退出码是对的（1），err 也进了 journal，
        # 但屏幕上一个字都没有——用户只知道"命令没跑成"，不知道是字段名错、
        # 文件不存在还是别的。**失败不可见＝没法修**，只能重跑猜。
        # 这与 M-R93/Verdict 那条同源：**证据要给到能看见的地方**，
        # 落在 journal 里的摘要救不了正在敲命令的人。
        import traceback as _tb
        # ★ S176b：quiet 时只留 err（进 Verdict/journal），不往屏幕打。
        #   理由同上：探针会故意让 dispatch 抛 ValueError('依赖缺失') 来验证
        #   「抛异常 ⇒ ok=False」这条判定方向，那是测试手段，不是本命令真崩了。
        if not quiet:
            print(f'  ❌ 命令异常终止：{err}', file=sys.stderr)
            if os.environ.get('TVBOX_TRACEBACK') == '1':
                _tb.print_exc()
            else:
                print('     （设 TVBOX_TRACEBACK=1 看完整堆栈）', file=sys.stderr)
    return Verdict(exit_code=exit_code, rc=rc, err=err)


JOURNAL_FP = '/root/.codebuddy/skills/ecosys-conductor/session_journal.jsonl'

def _journal(verb, rest, verdict):
    """S113 会话监工（M-R67）：每个动作落 journal 流水，conductor recap 兜底复盘。

    S138：签名由 `ok` 改为 `verdict` —— 证据随对象走，不再从全局读 exit/err。
    以前这里读 `_EXIT_CODE[0]`，本质是「隔空取物」：拿到的可能是上一次的值。
    """
    try:
        import json as _j
        rec = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'verb': verb,
               'arg': (rest[0][:60] if rest else ''), 'ok': bool(verdict.ok),
               'exit': verdict.exit_code}
        # 异常/自报失败必须带原因留痕：否则审计只能说「须复核」，永远复核不出真因
        if not verdict.ok and verdict.reason():
            rec['err'] = verdict.reason()
        with open(JOURNAL_FP, 'a', encoding='utf-8') as f:
            f.write(_j.dumps(rec, ensure_ascii=False) + '\n')
    except Exception:
        pass


def _dispatch(verb, rest):
    """按动词取模块（惰性）并派发，**返回该次的 Verdict**；模块缺失时降级提示而非崩溃（S069）。

    判定全部由 Verdict 完成（唯一判定点），本函数只做「取模块 → 派发 → 记账 → 回传」。
    上一版把判定拆成「safe_dispatch 写全局 + 这里按 exit/rc 顺序读全局」，
    于是正确性依赖「记得重置」和「记得读的顺序」——两个人肉纪律，各栽一次。

    唯一例外：close 的 status 子命令**永远** ok=True——它是只读查询，不该改变自身判定。
    """
    try:
        v = safe_dispatch(get_module(ROUTE[verb]), mod_argv(verb, rest))
    except Exception as e:
        # safe_dispatch 内部已兜 Exception；能冒到这里的是 BaseException（KeyboardInterrupt 等）。
        # 仍然构造一个 Verdict 再记账，绝不让 journal 因为异常而漏记本次动作。
        v = Verdict(err=f'{type(e).__name__}: {str(e)[:120]}')
        _journal(verb, rest, v)
        raise
    _journal(verb, rest, v)
    return v


def run_capture(module, argv):
    """在独立捕获区执行一条命令，返回其 stdout 文本。

    注意：多线程并行时不能用「重定向全局 sys.stdout」的方式隔离——
    命令内部会释放 GIL 去跑 subprocess(云读)，导致各线程的 stdout 缓冲互相串扰。
    因此并行模式改为「每命令一个子进程」（见 cmd_parallel），本函数仅串行/调试用。
    """
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        module.dispatch(list(argv))
    except SystemExit:
        pass
    except Exception as e:
        buf.write(f'\n[ERROR] {e}')
    finally:
        sys.stdout = old
    return buf.getvalue()


def run_subprocess(spec):
    """并行模式：每条命令独立子进程，stdout 天然隔离，避免线程间缓冲串扰。"""
    parts = spec.split()
    p = subprocess.run([sys.executable, os.path.abspath(__file__), *parts],
                       capture_output=True, text=True)
    out = p.stdout
    if p.stderr and p.returncode != 0:
        out += '\n[stderr] ' + p.stderr.strip()[:300]
    return spec, out


def cmd_run(specs):
    if not specs:
        print(HELP)
        return
    t0 = time.time()
    for spec in specs:
        parts = spec.split()
        v, rest = parts[0], parts[1:]
        if v not in ROUTE:
            print(f'未知子命令: {v}')
            continue
        _dispatch(v, rest)
    print(f'\n⏱  run 串联完成 {len(specs)} 个命令，用时 {time.time()-t0:.1f}s'
          f'（同进程 + 共享缓存）', file=sys.stderr)


def cmd_parallel(specs):
    if not specs:
        print(HELP)
        return
    tasks = [s for s in specs if s.split()[0] in ROUTE]
    # S165：两个边界都会炸，且炸法完全不同——
    #   ① tasks 只有 1 个 → min(1,8)=1 其实没事；真正炸的是 tasks 为 **0** 个（全部命令名不认识）
    #      → max_workers=0 → `ValueError: max_workers must be greater than 0`
    #   ② 即便不炸，`res` 在 with 里赋值、tasks 为空时 for 循环一进就崩在构造器上，
    #      后面的 `for spec, out in res` 会 NameError。
    # 两种都表现为**一屏 Python traceback**，用户看不懂、也拿不到可用信息。
    # 这里显式分流：无有效命令就说清楚哪几个不认识，别把内部异常抛给用户。
    if not tasks:
        bad = [s for s in specs if s.split()[0] not in ROUTE]
        print(f'❌ 没有可执行命令：{"、".join(bad) or "（空）"} 都不在路由表里')
        print(f'   可用子命令见 `tvbox` 帮助，或用 `tvbox conductor ask "<需求>"` 查该敲什么')
        return 2
    # ★ S193 修（audit_concurrency.py 实测报违规 1 处）：
    #   原写法 `ThreadPoolExecutor(max_workers=min(len(tasks), 8))` 是**就地拍数**——
    #   8 这个数字没有依据，且绕开了统一档位表 concurrency.TIERS，
    #   正是 M-R114 判据3「并发数必须来自统一档位表，禁止就地拍数」禁止的形态。
    #   tasks 跑的是本地 tvbox 子命令（子进程、无对外网络），属 **D·本地/无网络档**，
    #   上限 = min(cpu_count, 8)，由档位表给出而不是写死在这里。
    #
    #   keep_order=True：原实现用 `ex.map` 天然保序，换成 run_bounded 默认
    #   as_completed 会乱序（concurrency.py 注释里 core.fanout 就栽过），
    #   调用方按 spec 顺序读输出，必须显式保序。
    from concurrency import run_bounded, TIER_LOCAL
    rep = run_bounded(tasks, run_subprocess, tier=TIER_LOCAL,
                      label='tvbox p', keep_order=True)
    for item in rep.results:
        if item is None:          # keep_order=True 时，失败位置为 None
            continue
        spec, out = item
        print(f'\n═══ {spec} ═══')
        print(out.rstrip())
    # 单条失败不再整批崩（原 ex.map 会抛），但要让用户看见是哪条、为什么
    for e in rep.errors:
        print(f'❌ 并行子任务失败：{e}', file=sys.stderr)
    print(f'\n⏱  p 并行完成 {rep.n} 个命令，用时 {rep.elapsed:.1f}s'
          f'（{rep.workers} 并发 · D·本地档 · 子进程隔离）', file=sys.stderr)
    return 0 if not rep.errors else 1


def main():
    args = sys.argv[1:]
    # --strict-exit（S136 四续）：把子命令真实退出码传给 shell。
    # 默认仍返回 0——历史上有大量调用方只读 stdout、不看码，改默认值属破坏性变更。
    # 需要判成败的场合（CI/脚本/自动化）显式加此开关：
    #   tvbox block check --strict-exit || echo "有风险"
    # 不加时行为完全不变（零回归风险），加了才"退出码可信"。
    strict = '--strict-exit' in args
    if strict:
        args = [a for a in args if a != '--strict-exit']
    if not args or args[0] in ('-h', '--help', 'help'):
        print(HELP)
        return
    verb = args[0]
    if verb == 'manifest':
        return cmd_manifest()
    if verb == 'discover':
        return cmd_discover(args[1:])
    if verb == 'skills':
        return cmd_skills(args[1:])
    if verb == 'connectors':
        return cmd_connectors(args[1:])
    if verb == 'jobs':
        return cmd_jobs(args[1:])
    if verb == 'library':
        return cmd_library(args[1:])
    if verb == 'contracts':
        return cmd_contracts()
    if verb == 'chain':
        if len(args) < 2:
            return cmd_manifest()
        return cmd_chain(args[1])
    if verb == 'run':
        return cmd_run(args[1:])
    if verb == 'p':
        return cmd_parallel(args[1:])
    if verb not in ROUTE:
        print(f'未知子命令: {verb}')
        print(HELP)
        # ★ S177 步7 实测修：原来写 `return 2 if strict else 0`，
        #   即**非 strict 模式下"命令不存在"返回 0**。
        #   实测：`tvbox conductor __zzz__` → 屏幕打「未知子命令」，rc 却是 0。
        #   这与 T1（signal-check 有 ❌ 却 rc=0）是同一个病：
        #   **把"没跑"说成"跑成了"**，而按 rc 判断的 CI/脚本全被带成假绿。
        #
        #   为什么这里**不该**受 strict 控制：
        #     · `--strict-exit` 管的是「命令跑了、结果好不好」——
        #       默认 0 是为了兼容只读 stdout 的旧调用方，那是合理的兼容。
        #     · 而「命令根本不存在」**与读不读码无关**：它对所有调用方都是错误，
        #       不存在"某类调用方认为它成功"的场景。把错误伪装成成功没有受益方。
        #   故：未知子命令一律 rc=2（用法错），不再随 strict 变化。
        return 2
    _verdict = _dispatch(verb, args[1:])
    # S138/S139：**只读 ok，不再自己重判**。
    # 上面写了两版都不对：v1 默认 `return None` → 判了不通过却 exit 0（门禁形同虚设）；
    # v2 改成读 `exit_code != 0` → 又分裂了：Verdict 已判 ok=False（rc=1），
    #   而 exit_code 仍是 0，于是「判定说失败、码说成功」，同一处第三次栽跟头。
    # 根因和 M-R93 一样：**判定点不止一个**。Verdict 是唯一判定点，外面就不许再判。
    if _verdict is not None and not _verdict.ok:
        # ★ S165 修：原来 `cmd_whoami`（缺参 return 2）被算成 ok=True——
        #   因为 `_as_exit_code(2)` 得到 2，但 Verdict 判 `rc != 0` 已置 ok=False… 实测却通。
        #   真因在下面这行原来的 `return _verdict.exit_code or 1`：
        #   exit_code 是 0（没抛 SystemExit），`0 or 1` → 1，看着对；
        #   而**成功路径根本不看 rc**——`link.dispatch` / `ledger.main` 吞掉返回值时，
        #   rc 恒 0 就永远 ok。这些已在各模块修好（S165 四处）。
        #   这里补一道**兜底**：判 ok=False 但 exit_code 无从得知时，回落到 rc。
        return _verdict.exit_code or _verdict.rc or 1
    # ★ S165：`--strict-exit` 下**成功路径也要把 rc 传出去**。
    #   原来成功就 `return`(None) → exit 0，而"rc 非 0 但被 before/after 抵消"的情况
    #   在这里本来就该是失败，不会走到这一支。故这一支只需保持 0。
    #   真正要补的是 **conductor verify 这类 return False 的**：它由 dispatch 归一成 rc=1，
    #   Verdict 判 ok=False，上面那支已经能传出去——实测证明确实通了。
    return 0 if not strict else (_verdict.rc if _verdict else 0)


if __name__ == '__main__':
    sys.exit(main() or 0)
