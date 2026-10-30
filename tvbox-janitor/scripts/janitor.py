#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
janitor.py —— 清道夫·待删收件箱（S104）

背景（用户 2026-10-16）：
  "你删除不了的，需要我删除的统一放进一个文件内，方便我去删除，
   这个应该可以在生态里做一个新技能了，专门管整理这块。"
  ——"全量，只要你不能删除的，都放进来，不管是啥"
  ——"一个文件夹，把需要我删的统一放进去，我只管去这个文件删"

**能力边界**：云端库（WorkBuddy space）**无删除节点 API**（只有 create/rename/move/
publish/unpublish）。凡 AI 删不掉的节点 → 本技能**移动**到一个统一收件箱文件夹，
并生成《待删清单》，用户只需打开这一个文件夹按单删除。

命令：
  janitor inbox                       建/确认"🗑 待删收件箱"文件夹（幂等）
  janitor add --node ID [--reason R]  把节点移入收件箱 + 登记清单（AI 能做的最后一步）
  janitor scan                        扫全空间找"废弃/待删/🗑"标记节点（补漏登记）
  janitor list                        打印待删清单（Markdown + CSV 落盘）
  janitor render                      把清单重写进收件箱内的说明文档
  janitor move --node ID --to 父节点ID              节点移动到指定文件夹（改名/归位通用）
  janitor rename --node ID --title "新标题"         节点改名

铁律：
  · 只移动、不删除（AI 无删除权）；移动前**校验节点确实存在**。
  · 清单**永不静默丢项**：节点不存在/移动失败 → 显式告警并保留在清单（标"待人工确认"）。
  · 幂等：同一节点重复 add 不重复登记（按 node_id 去重）。
"""
import os, sys, json, argparse, subprocess, time, re

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = '/root/.codebuddy/skills/library'
SPACE = '8w5cnqCqQJ0wNoF2Hb6efH'
# 收件箱挂「我的资料」根目录（S196 用户 2026-10-21 21:27 指示：以后待删放根目录，一眼可见）
PARENT = SPACE
# ★ 全局扫描超根（S158 修：这里曾经写错了）
#   原文注释断言「list-nodes 无法枚举空间顶层，故以探源司·总库为起点」——**那是错的**。
#   实测（S158）：`--parent-node-id ""`（**空字符串，不是省略也不是 'root'**）即可枚举空间顶层，
#   返回 total=76（60 doc / 8 folder / 7 database / 1 web）。
#   而 'gZ4xP6kXXeCpTlsH2CN9tJ'（📦 探源司·总库）只是这 76 个顶层节点中的**一个子树**，
#   以它为根 → 扫不到空间根层级那 60 多个散落的 doc，于是长期报「✅ 未发现垃圾」。
#   用户的感受是「资料库乱了、说了几遍没人动」——**不是没人想动，是扫描器根本没往那儿看**。
#   现改为遍历 ROOTS（空间根 + 探源司子树，后者保留以兼容旧调用），
#   空间根用空串表示（见 _kids 对空 pid 的处理）。
ROOT = 'gZ4xP6kXXeCpTlsH2CN9tJ'   # 保留：探源司·总库子树（历史调用/restore 反查用）
SPACE_ROOT = ''                    # ★ 空间真根：--parent-node-id "" （实测唯一可用写法）
ROOTS = [SPACE_ROOT]               # 扫描起点集合（空间根已包含探源司子树，故不再单列）
# ★ 垃圾判定关键词（标题命中即判垃圾，自动投递）。扩展覆盖各类废弃标记；
#   刻意**不含**「库/总库/源库」等正常词，避免误伤活跃库。
KW = ('废弃', '已废弃', '失效', '作废', '请删', '待删', '🗑', '空壳', '勿用',
      '测试', 'test', '重复', '弃用', '临时', 'temp', 'backup',
      'draft', '旧版', '垃圾', '（旧', '_old', '弃')
# ★ 注：「归档」**不**作为废弃标记——正常业务表（如 S103「产出json·归档表」）
#   标题常含"归档"，会误判。废弃判定只认显式废弃标记词（见上 KW），不泛化到"归档"。
INBOX_TITLE = '🗑 待删收件箱（清道夫·按单删即可）'
STATE = os.path.join(HERE, 'janitor_state.json')
OUT_DIR = '/workspace'

# ★ S158：空文件夹 ≠ 垃圾。这两类空夹**永远不投**：
#   · 名字像备份/库/归档 —— 可能是"内容在别处"或"还没写完"（备份夹实测就是空壳）
#   · 名字像运维/入口 —— 是结构性容器，不承载内容
#   事故链见 cmd_add 护栏注释：一次 scan 差点把 77 个附件的备份夹当空夹投掉。
EMPTY_KEEP = ('备份', 'backup', '归档', '库', '运维', '入口', '总库', '收件箱', '手册')

# ★ S179（M-R125）：上面这层保护**方向是错的**——它挂在"名字"上。
#
#   实测（见 ecosys-conductor/docs/M-R125_evidence.md）：
#     · node-info 字段全集 16 个，**没有任何附件字段**；
#     · api-manifest 41 个接口，**没有 attachment-list**；
#     · get-doc-review 正文对"有 81 个附件"与"真空"返回完全同形；
#     · 同一批 14 个备份夹，list-nodes 全返回 0 子节点，而 backup_state 记着 81 件已上传。
#
#   所以「子节点数 == 0」**不是弱证据，是不成立的证据**：接口返回的 0 是真值，
#   只是它回答的不是"这里有没有东西"。既然如此，靠 EMPTY_KEEP 关键词兜底就等于
#   **用命名习惯替代判据**——夹叫 `20261020_eco` 而不是 `20261020_生态备份` 就失守。
#
#   改为：**空夹一律不按"空"投递**，只登记为待人工核对的候选（nocontent），
#   并显式写清"AI 无法判定"。宁可少投（用户照 list 删即可），不可误投（附件的夹删了就没）。
#   EMPTY_KEEP 保留但降级为"排序权重"，不再承担安全职责。
EMPTY_NEVER_AUTOPOST = True

# ★ S179：结构性容器词——标题含这些词时，即便同时命中废弃标记也不自动投递（须人工裁决）。
#   理由同 EMPTY_KEEP 降级：一个"库/归档/总库"被整批搬走，损失远大于少投一条。
#   ★ 单字词必须带否定前瞻，否则子串误命中：
#     实测「🗄️ 待清·DmaxPro908（已入库待删）」——"入库"里含"库"，被判成结构性容器，
#     而它明明是"已入库**待删**"的待清项，本该投递。故 `库` 后面紧跟"存/房/内"时不认。
STRUCT_KEEP = ('归档', '总库', '备份', '运维', '入口', '手册', '收件箱')
_STRUCT_RE = re.compile(r'(?<!入)库(?!存|房|内)')


def _struct_hits(t):
    """标题命中的结构性容器词（`库` 用前瞻排除"入库/库存"等动词用法）。"""
    out = [k for k in STRUCT_KEEP if k in t]
    if _STRUCT_RE.search(t):
        out.append('库')
    return out

# ★ S158：同父同名去重的**默认阈值**。>该值才把多余副本投进收件箱（保留最早一份）。
#   为什么默认不是 0：实测根层级「生态遗留趋势留档（跨设备）」×23、
#   「生态1015_遗留问题监测报告」×19 —— 这些不是"两份不同内容的留档"，
#   而是同一个脚本每轮新建一份的产物。**不设默认值 = 问题永远只被报出来、永远不被处理**
#   （用户原话："我强调几遍依然没动作"）。2 是保守值：允许正常保留 2 份（现行 + 上一版）。
#   要"只告警不动手"显式传 `--dedup 0`。
DEFAULT_DEDUP = 2

# ★ S158 新增：大生态/小生态分层普查（census）
#   痛点：清道夫只会「按关键词找垃圾」，但用户真正问的是「**这块归谁管**」——
#   关键词判定回答不了归属，于是每次都要人临场翻。census 补上「归属」这一维：
#   输出每个节点属于哪个技能的责任田（L1），不属任何责任田的即为「缝」（L0 无主区）。
#   权威定义见 ecosys-conductor/references/eco_layers.md（单一事实源，禁在别处另立一套）。
#   责任田登记：节点 id → (技能, 说明)。**只登记技能能自己写东西的容器**，
#   不登记"只是路过读一下"的库——登记了就等于声称对它负责。
TERRITORY = {
    # ── 空间根层级锚点 ──
    'Xjqn2Wf5EKPSGIXqQ2K6Dc': ('eco-ledger', '📒 账房·总账'),
    '6cQgwWQqqKoeFAiTcb08Cx': ('eco-ledger', '📒 账房·账户'),
    'gZ4xP6kXXeCpTlsH2CN9tJ': ('tvbox-source-hub', '📦 探源司·总库'),
    'elOV0tlJ4gcTF3atBay2nS': ('ecosys-conductor', '📁 入口·常用入口索引'),
    'qBQcYoVTqg7v3NAYBa4eSF': ('skill-evolution', '🥋 通用招式库'),
    'MrJdROzysspCyWo0L3F32z': ('ecosys-conductor', '用户术语库'),
    'kqNrwHd53K9qr1DFBUPglZ': ('ecosys-conductor', '用户需求事件库'),
    # ── 探源司子树内的关键库 ──
    'GCLJWbFGS1BmYbj6JCX9it': ('tvbox-interface', '📁 底包·底包总库'),
    'pNwZ9uBOTz6FzRuvu8nryI': ('tvbox-source-hub', '探源司·线路源库'),
    '8JBrhSwsJ3pnPYtoGLo9XV': ('tvbox-source-hub', '探源司·成品源库'),
    'aCSvley568A0aW3sggzJwA': ('probe-hub', '上游仓库监测册'),
    'khHl458xi8qK15lVaYfbvU': ('probe-hub', '探源司·搜索候选册'),
    'E5I6T2UXIS7CB5r0AkimRl': ('probe-hub', '探源司·采集候选册'),
    'hGwE41OpJDOR8CW0sLUwRx': ('tvbox-source-blacklist', '探源司·失信host库'),
    # ── 生态四库（资料库侧）──
    'T4kODtANe7rSrtZTu4sIND': ('ecosys-conductor', '🧩 TVBox技能生态登记库'),
    'XTuuj8mKtqnZSolDdeDxkq': ('unfinished-audit', '📚 资料库操作经验库'),
    # ── 收件箱 ──
    'tBlgSL4SPEFMrsyTBTg03c': ('tvbox-janitor', '🗑 待删收件箱'),
}
# 空间根与收件箱不在 census 里当"缝"统计：前者是全体，后者是待删暂存
_NOT_GAP = {'', 'tBlgSL4SPEFMrsyTBTg03c'}

# ══ 归属真源合并（S158 修·本轮最严重的一次自打脸）═══════════════════════════
# **事故**：S158 我写了 `ecosys-conductor/scripts/zone_registry.json`，14 个区逐个指派
# owner，报告里写"覆盖全部 315 个缝节点，11 已指派"。但 `census` **只读本文件的
# TERRITORY**，从不读 zone_registry —— 于是缝占比一动不动，还是 71%（317/441）。
# 我指派的 8 个已指派区 node_id **一个都不在 TERRITORY 里**，那张表是死表。
#
# **这正是我本轮批判过的缺陷原样重演**：两处各存一份"归属"，互不相认，
# 而且两边都不报错——一边说"我指派了"，另一边说"还是没人管"，同时成立。
#
# **修法（M-R85 单一事实源）**：
#   · `zone_registry.json` 是**跨技能归属的主源**（含 owner/why/plan，属治理决策）；
#   · `TERRITORY` 只保留**技能自有锚点**（那些不在 registry 里的库/容器）；
#   · census 启动时**合并两者**，registry 优先（治理决策 > 代码默认）。
#   读不到 registry 时必须**显式告警**，不许静默只用 TERRITORY 假装覆盖完整。
ZONE_REGISTRY = '/root/.codebuddy/skills/ecosys-conductor/scripts/zone_registry.json'


def load_territory():
    """合并归属真源：TERRITORY（技能锚点） + zone_registry（治理指派）。

    返回 (merged, problems, prefix_rules)。problems 非空表示**覆盖不完整**，调用方必须报出来。

    ★ S158 一次读、两处用：原先这段读了 registry **两遍**，第二遍 `except: pass`。
      于是 registry 损坏时：第一遍报 problems，第二遍把前缀规则静默吞成空——
      **4 个根层级散落区的归属无声消失，而 problems 里看不到任何相关提示**。
      这正是本轮批判的 `except: pass` 原样重演，已改为单次读取。
    """
    merged = dict(TERRITORY)          # 先放技能锚点
    problems = []
    pref_rules = []
    try:
        reg = json.load(open(ZONE_REGISTRY, encoding='utf-8'))
        zones = reg.get('zones') or []
        for z in zones:
            nid = (z.get('node_id') or '').strip()
            owner = z.get('owner')
            pref = z.get('match_prefix') or []
            # 两种挂法：容器型 node_id / 根层级散落组 match_prefix。
            # 只有**两者都没有**才算登记不全（那说明这个区在表里只是个说法）。
            if not nid and not pref:
                problems.append(f'区「{z.get("top")}」既无 node_id 也无 match_prefix，无法参与归属判定')
                continue
            if not owner:
                continue                  # 待裁定区：仍算缝，但要单独报出来
            if nid:
                # registry 覆盖同名锚点：治理指派优先于技能自报（后写覆盖先写）
                merged[nid] = (owner, z.get('top') or '')
            if pref:
                pref_rules.append((tuple(pref), owner, z.get('top') or ''))
    except FileNotFoundError:
        problems.append(f'zone_registry 不存在：{ZONE_REGISTRY}——**归属只含技能锚点，'
                        f'治理指派的那批区会全部计为缝**（不是"没有缝"）')
    except Exception as e:
        problems.append(f'zone_registry 读取失败（{type(e).__name__}: {e}）——'
                        f'归属不完整，缝占比会偏高（无法判定，不是"没问题"）')
    return merged, problems, pref_rules

sys.path.insert(0, LIB)


def _space(api, **kw):
    args = [sys.executable, f'{LIB}/space_api.py', api]
    for k, v in kw.items():
        args += [f'--{k.replace("_", "-")}', str(v)]
    r = subprocess.run(args, capture_output=True, text=True)
    s = (r.stdout or '').strip()
    for ln in s.splitlines():
        ln = ln.strip()
        if ln.startswith('{'):
            try:
                return json.loads(ln)
            except Exception:
                continue
    return None


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding='utf-8'))
        except Exception as e:
            print(f'  ⚠️ 收件箱状态损坏（{type(e).__name__}）——禁止当"空收件箱"（M-R60）')
            raise SystemExit(2)
    return {'inbox': '', 'items': []}


def save_state(d):
    """★ S156 招式 T-I 落地：写后回读验证（写了 ≠ 写成了）。

    **为什么清道夫尤其需要**：`STATE` 是「待删收件箱」的唯一账本——哪些节点已投递、
    哪些已删、哪些还等着人工处理，全在这一个 json 里。原先它是**裸写**：
    `json.dump(d, open(...'w'))` 之后没有任何证据说明写成功了。
    写坏的后果不是"这一条没记住"，而是**整队丢了**：下一次 `scan` 读到残缺 json →
    `load_state` 抛 SystemExit(2) → 收件箱直接不可用；而如果截断后恰好仍能解析
    （比如只少了末尾几条），更糟——**没人知道少哪几条**。

    三态（M-R60）：写入失败 / 回读解析失败 / 回读条目数不符 —— 分别报原因并**抛错退出**，
    绝不静默继续（继续就会拿"假状态"去决定删不删节点）。
    """
    try:
        with open(STATE, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        raise SystemExit(f'❌ 收件箱状态写入失败（{type(e).__name__}: {str(e)[:100]}）——'
                         f'拒绝继续（后续动作会基于错误状态决定删不删节点）')
    try:
        back = json.load(open(STATE, encoding='utf-8'))
    except Exception as e:
        raise SystemExit(f'❌ 收件箱状态写后回读**解析失败**（{type(e).__name__}: {str(e)[:100]}）'
                         f'——文件已写坏，请检查 {STATE}')
    n_w, n_r = len((d or {}).get('items') or []), len((back or {}).get('items') or [])
    if n_w != n_r:
        raise SystemExit(f'❌ 收件箱状态回读条数不符：写入 {n_w} 条，回读到 {n_r} 条'
                         f'——内容被截断/覆盖，拒绝继续')
    if (d or {}).get('inbox', None) != (back or {}).get('inbox', None):
        raise SystemExit('❌ 收件箱状态回读：inbox 字段与写入值不符——状态已串')


def _kids(pid):
    """列某节点的子节点。**pid 为空串 = 空间顶层**（S158 实测：唯一可用写法）。

    实测过的三种写法：
      · parent_node_id='root' → `11510 block not found`（不行）
      · parent_node_id=<spaceId> → `11510 block not found`（不行）
      · parent_node_id=''      → 正常返回，total=76（**可行**）
    另：limit=200 单页可能被服务端截断，故带 offset 翻页到底。
    """
    out = []
    off = 0
    for _ in range(20):                      # 上限 20 页（4000 节点），防死循环
        d = _space('space.workspace.list-nodes', space_id=SPACE,
                   parent_node_id=pid, limit=200, offset=off)
        if not isinstance(d, dict) or d.get('error'):
            break
        data = d.get('data') or {}
        batch = data.get('nodes') or []
        out += batch
        if not data.get('hasMore') or not batch:
            break
        off += len(batch)
    return out


def _find_parent(nid, skip_inbox=True):
    """从空间超根 ROOT 递归反查 nid 的父节点 id（默认跳过收件箱子树）。

    为什么不用 node-info 的 parentNodeId？那字段实测恒为 None（不可信）。
    故用全树遍历反查——供 cmd_add 在移动前记录原父节点、误投后 restore 一键回退。
    """
    st = load_state()
    inbox = st.get('inbox')
    seen = set()

    def walk(pid):
        if pid in seen:
            return None
        seen.add(pid)
        if skip_inbox and inbox and pid == inbox:
            return None
        for n in _kids(pid):
            cid = n.get('id') or n.get('nodeId')
            if cid == nid:
                return pid
            if n.get('kind') == 'folder':
                r = walk(cid)
                if r:
                    return r
        return None

    return walk(ROOT)


def _find_inbox():
    """在 PARENT 下找已存在的收件箱文件夹（按标题前缀匹配）。
    ★ S196：PARENT=空间根时必须用 _kids('')（空串=空间顶层唯一可行写法，见 _kids docstring；
      _kids(SPACE) 报 11510——当晚实测因此没认出已搬根目录的老箱，重复建了一个）。"""
    kids = _kids('') if PARENT == SPACE else _kids(PARENT)
    for n in kids:
        if n.get('kind') == 'folder' and '待删收件箱' in (n.get('title') or ''):
            return n.get('id'), n.get('title')
    return '', ''


def cmd_inbox(a):
    st = load_state()
    iid, ititle = _find_inbox()
    if iid:
        st['inbox'] = iid
        save_state(st)
        print(f'✅ 收件箱已存在：{ititle}（{iid}）')
        return 0
    r = subprocess.run([sys.executable, f'{LIB}/manage/create_folder.py',
                        '--title', INBOX_TITLE, '--space-id', SPACE, '--parent-id', PARENT],
                       capture_output=True, text=True)
    out = (r.stdout or '').strip()
    # 返回形如：KS_FOLDER_CREATE\t<id>\t<title>
    iid = ''
    for ln in out.splitlines():
        if 'KS_FOLDER_CREATE' in ln:
            parts = ln.split('\t')
            if len(parts) >= 2:
                iid = parts[1].strip()
    if not iid:
        print(f'❌ 建收件箱失败：{out[:200]}{r.stderr[:200]}')
        return 1
    st['inbox'] = iid
    save_state(st)
    print(f'✅ 收件箱已创建：{INBOX_TITLE}（{iid}）')
    return 0


def cmd_add(a):
    st = load_state()
    iid = st.get('inbox') or _find_inbox()[0]
    if not iid:
        print('❌ 收件箱未建，先跑 janitor inbox'); return 1
    nid = a.node
    # 校验节点存在 + 取标题
    info = _space('space.workspace.node-info', node_id=nid)
    node = ((info or {}).get('data') or {}).get('node') or {}
    title = node.get('title') or ''
    if not title:
        print(f'  ⚠️ 节点不存在或不可读（{nid}）——登记但标"待人工确认"')
        title = '（节点不可读）'
    # ★ S158 新增护栏：**不许把有内容的节点投进收件箱**。
    #   事故（S158 实测发现，G10）：`conductor backup` 把每天的备份夹建在了收件箱里；
    #   `janitor scan` 的空文件夹规则（folder 无子节点→垃圾）在第一轮把「1017_S125_三层联动首备」
    #   （77 个附件）判成垃圾 —— 因为它当时确实还没传完，是空夹。
    #   **扫描器看的是"此刻有没有子节点"，而一个正在写入的容器此刻一定是空的。**
    #   收件箱是"待删"的地方，进去的东西默认会被用户整批删掉；
    #   没有这道护栏，一次 scan 就能把备份连根拔了。
    #   判据取"有没有子节点 + 名字像不像备份"，宁可漏投也不误杀。
    if not getattr(a, 'force', False):
        _kids_now = _kids(nid)
        _name_hit = any(k in title for k in ('备份', 'backup', '归档', '快照', '库'))
        if _kids_now:
            print(f'  🛑 拒收：{title}（{nid}）**非空**（{len(_kids_now)} 个子节点）——'
                  f'收件箱是待删暂存，非空容器进去就有被整批删掉的风险')
            print('     → 真要投递请先手动清空，或加 --force 明确承担后果')
            return 4
        if _name_hit:
            print(f'  🛑 拒收：{title}（{nid}）空夹但名字像备份/库——**空不等于该删**'
                  f'（可能正在写入或内容在别处）')
            print('     → 确认真的废弃再加 --force')
            return 4
    # 幂等
    if any(x['id'] == nid for x in st['items']):
        print(f'ℹ️ 已在清单：{title}'); return 0
    # ★ 移动前记录原父节点（node-info.parentNodeId 不可信，用全树反查），
    #   供误投后 `janitor restore` 一键退回原位（v1.1.7）。
    origin = _find_parent(nid) or ''
    # 移动入收件箱（AI 能做的最后一步）
    mv = _space('space.workspace.move-node', node_id=nid, target_parent_id=iid)
    # ★ S200 补护栏（P067·L244）：收件箱夹为本次新建时，首投可能撞 12607
    #   permission denied（新建夹权限/索引传播延迟，非真权限问题——实测同参数
    #   几分钟后重试 7/7 全过）。对 12607 带间隔重试一次，再失败才如实登记。
    if mv and mv.get('error') and '12607' in str(mv.get('error')):
        time.sleep(5)
        mv = _space('space.workspace.move-node', node_id=nid, target_parent_id=iid)
    ok = bool(mv and not mv.get('error'))
    st['items'].append({
        'id': nid, 'title': title, 'kind': node.get('kind', ''),
        'reason': a.reason or '（未注明）',
        'moved': ok, 'added': time.strftime('%Y-%m-%d %H:%M'),
        'origin_parent': origin,
    })
    save_state(st)
    print(f'  {"✅ 已移入收件箱" if ok else "⚠️ 移动失败（清单仍登记）"}：{title}（{nid}）')
    if not ok and mv:
        print(f'     原因：{str(mv.get("error"))[:120]}')
    # **S156 修：返回码必须反映「移动」有没有真成，不能一律 0。**
    # 事故：旧版无论成败都 return 0 → `auto_file`(→True) → `core.file_to_janitor`(→True)
    # → 调用方以为投递成功。而清单里躺着的是 `moved=False` 的记录：
    # **清单显示 40 项，收件箱实际只有 18 个**，差的那 22 项从来没离开原地。
    # 登记保留是合理的（"该删"这个事实为真），但**返回值必须说真话**，
    # 否则调用方永远收不到失败信号，假账会一直长下去。
    return 0 if ok else 3


def cmd_scan(a):
    """★ 全局扫描：从空间超根递归，识别**所有垃圾**（废弃标记/空文件夹/重复节点）
    并**发现即自动投递**收件箱（除非 --dry-run 仅预览）。覆盖范围=整个空间。

    判定规则（多维，覆盖"所有的垃圾"，不止空文件夹）：
      1) 标题命中 KW 关键词 → 废弃类垃圾；
      2) 空文件夹（folder 无子节点）→ 冗余容器垃圾；
      3) 同父同名重复节点 → **默认仅告警不投**（防误删，留人工裁决）；
         加 `--dedup N` 可把「同名且 >N 份」的**多出来的副本**投递（保留最早一份）。
         这条是 S158 加的：根层级实测有 26 份同名「生态遗留趋势留档（跨设备）」，
         旧行为一直只告警不投 → 越积越多 → 用户看到的是一片乱。

    ★ S158：扫描起点改为 `ROOTS`（含空间真根）。旧版从探源司·总库子树起扫，
    **空间根层级那 60+ 个散落 doc 从来没被看见**，于是一直报「✅ 未发现垃圾」。
    排除：收件箱自身及其子树（已在箱/是箱，不扫不投）。
    """
    from collections import Counter
    st = load_state()
    inbox = st.get('inbox') or _find_inbox()[0]
    dry = bool(getattr(a, 'dry_run', False))
    dedup_n = int(getattr(a, 'dedup', 0) or 0)
    seen, hits, dup_groups = set(), [], {}
    nocontent = []      # ★ S179（M-R125）：0 子节点的夹——**不投递**，只登记待人工核对

    def walk(pid, depth=0):
        if pid in seen or depth > 12:
            return
        seen.add(pid)
        if inbox and pid == inbox:
            return  # 不进入收件箱子树
        kids = _kids(pid)
        cnt = Counter((k.get('title') or '').strip() for k in kids if k.get('title'))
        for n in kids:
            nid = n.get('id') or n.get('nodeId')
            kind = n.get('kind')
            t = (n.get('title') or '').strip()
            if not nid or (inbox and nid == inbox):
                continue
            # 重复检测（同父同名，>1）：组登记，仅告警不投
            if t and cnt.get(t, 0) > 1:
                dup_groups.setdefault((pid, t), []).append(nid)
            reason = None
            kw = next((k for k in KW if k in t), None)
            # ★ S179：标题同时含「废弃标记」与「结构性容器词」时，**不自动投递**。
            #   实测「📁 归档·废弃归档库」两边都命中（KW=废弃/弃，STRUCT=归档/库）。
            #   这类名字无法从标题判断意图（是把整个归档库废了？还是这个库里装着废弃件？），
            #   自动搬走一个"库"的代价远大于少投一条。改为登记候选、交人裁决。
            _struct = _struct_hits(t)
            if kw and _struct:
                nocontent.append({'id': nid, 'title': t, 'looks_like_keep': True,
                                  'verdict': f'标题双义（废弃标记「{kw}」+ 容器词「{_struct[0]}」）——须人工裁决'})
                if kind == 'folder':
                    walk(nid, depth + 1)
                continue
            if kw:
                reason = f'标题含废弃标记「{kw}」'
            elif kind == 'folder' and not _kids(nid):
                # ★ S179（M-R125）：空夹**不再自动投递**。
                #   原实现在这里 `reason = '空文件夹（无子节点）'` → 直接进 hits → 被 scan 搬走。
                #   判据错：附件不占目录树槽位，0 子节点与"有 81 个附件"并存（实测）。
                #   现改为只登记为候选、显式标注"AI 无法判定"，由 list 交给用户核对。
                _keepish = bool(_struct_hits(t)) or any(k in t for k in EMPTY_KEEP)
                _cand = {'id': nid, 'title': t, 'looks_like_keep': _keepish,
                         'verdict': 'AI 无法判定（附件不可见）'}
                if EMPTY_NEVER_AUTOPOST:
                    nocontent.append(_cand)
                else:
                    reason = '空文件夹（无子节点·仅此一项判据，可能误判）'
                    hits.append((nid, kind, t, reason))
            if reason:
                hits.append((nid, kind, t, reason))
            if kind == 'folder':
                walk(nid, depth + 1)

    for _r in ROOTS:
        walk(_r)
    # ★ S158（G2 根因）：**统一去重**。旧版只在单次 walk 的"同一父节点"内比同名，
    #   而"生态遗留趋势留档（跨设备）"这类堆积恰恰是**每轮都往同一个父节点新建一份**——
    #   同父同名确实能抓到（实测 ×23），但旧行为默认 `--dedup 0` = 只告警不投，
    #   于是检测到了却什么也不做，越积越多。这不是"检测不到"，是**检测到了不处理**。
    #   新口径：不传 --dedup 时自动取 DEFAULT_DEDUP（>该值的多余副本才投），
    #   传 0 才表示"只告警不动手"。
    if not dedup_n:
        dedup_n = DEFAULT_DEDUP
    print(f'═══ 全局扫描 {len(seen)} 个容器（起点={ROOTS}｜去重阈值 >{dedup_n} 份投递）═══')
    # ★ S179（M-R125）：先交代"有多少夹我看着是空的，但我不能据此说它是垃圾"。
    #   这一段的用词必须守住三态：**不出现"空夹/可删"**，只说"从接口看不到内容"。
    if nocontent:
        _keepn = sum(1 for c in nocontent if c['looks_like_keep'])
        print(f'\n  ℹ️ {len(nocontent)} 个文件夹从接口看不到子节点——**不等于空**，本工具不投递它们：')
        for c in nocontent[:10]:
            hint = '（名字像结构性容器）' if c['looks_like_keep'] else ''
            print(f'     {c["id"]}  {c["title"]}{hint}  → {c["verdict"]}')
        if len(nocontent) > 10:
            # ★ S179：原来只写"另有 N 个，详见清单"——但清单(`list`)里并没有这一段，
            #   用户根本找不到。宁可多打几行，也别让"详见"指向不存在的地方。
            #   ★ 且必须与上面 10 条**同格式**（含 hint）——首版漏了 hint，
            #   于是同名的 4 个备份夹看起来"没被识别为结构性容器"，白白多查一轮。
            print(f'     …其余 {len(nocontent) - 10} 个：')
            for c in nocontent[10:]:
                hint = '（名字像结构性容器）' if c['looks_like_keep'] else ''
                print(f'     {c["id"]}  {c["title"]}{hint}  → {c["verdict"]}')
        print(f'     为什么不能判空：附件不占目录树槽位，`list-nodes` 恒返回 0（M-R125）。'
              f'要核实请到客户端看附件列表。')
    if not hits and not dup_groups:
        print('  ✅ 未发现新的垃圾节点' + ('（但上面那些夹需人工核对）' if nocontent else ''))
        return 0
    auto = 0
    for nid, kind, t, reason in hits:
        tag = '（预览·未移动）' if dry else ''
        print(f'  [{kind:8s}] {nid}  {t}')
        print(f'        └ 理由：{reason} {tag}')
        if not dry:
            rc = cmd_add(argparse.Namespace(node=nid, reason=reason, force=False))
            auto += 1 if rc == 0 else 0
    print(f'\n  命中待投递：{len(hits)} 项'
          + ('（仅预览，未移动）' if dry else f'（已自动投递 {auto} 项，其余为已在清单跳过）'))
    if dup_groups:
        # S158：把超过 dedup_n 份的同名节点的**多余副本**投递（保留最早一份）。
        # 「最早」按 title 相同组内 nodes 返回顺序（服务端按 created 排序）取第一个。
        # 注意 cmd_add 的护栏会二次把关：非空容器与"像备份"的名字一律拒收（返回 4），
        # 所以即使误入去重名单也不会被真的搬走——**两道闸，不靠单点判断**。
        _dup_dropped, _dup_blocked = 0, 0
        print(f'\n  ⚠️ 发现 {len(dup_groups)} 组重复节点（同目录同名）：')
        for (pid, t), ids in dup_groups.items():
            if len(ids) > dedup_n:
                keep, drop = ids[0], ids[1:]
                # ★ S179：先按**同一判据**预筛，别把注定被护栏拒收的项也说成"投递"。
                #   实测「20261020_生态备份」×13 属同父同名，原逻辑直接打印"投递 12 份副本"，
                #   而 cmd_add 的护栏会把这 12 个全拒（名字像备份）——于是屏幕上写着投递 12，
                #   清单里一条没有。这不是护栏没生效，是**输出在替事实撒谎**（M-R117）。
                _pre = [d for d in drop if not any(k in t for k in STRUCT_KEEP)]
                _hold = len(drop) - len(_pre)
                print(f'    · 目录 {pid} 下「{t}」×{len(ids)} → 保留 {keep}，'
                      f'可投递 {len(_pre)} 份副本'
                      + (f'，{_hold} 份因名字属结构性容器**不动**' if _hold else '')
                      + ('（预览，未移动）' if dry else ''))
                if not dry:
                    for d in drop:
                        if d not in _pre:
                            _dup_blocked += 1
                            continue
                        rc = cmd_add(argparse.Namespace(
                            node=d, force=False,
                            reason=f'同目录同名重复（{t}）第 {ids.index(d)+1}/{len(ids)} 份'))
                        if rc == 0:
                            _dup_dropped += 1
                        else:
                            _dup_blocked += 1      # 二道闸拦下，也要计数（不藏）
            else:
                print(f'    · 目录 {pid} 下「{t}」×{len(ids)}：保留（≤{dedup_n} 份视为正常留档）')
        print(f'  重复副本：实际投递 {_dup_dropped} 份'
              + (f'，被护栏/结构性容器拦下 {_dup_blocked} 份（**未移动**）' if _dup_blocked else '')
              + ('（预览）' if dry else ''))
    return 0


def _prune_stale(items, inbox, check=True):
    """★ S158 修（清单陈旧项）：剔除「清单里还挂着，但早已不在收件箱」的项。

    **事故**：`T7Bd0ukQ4dnnTAYMy2ktLq` / `xEiZLGulVg80N1jKfamb7B` 两个备份夹
    在 G10 修复时被 `restore` 迁回了「🗄 生态备份区」，parentId 已确认是备份区；
    但清单项没被移除，于是 `list` 仍然把它们列成"待删"。
    用户照单去收件箱找，**找不到**；更糟的是它造成"备份区的东西被列入待删"的错觉。

    **根因**：state 是"我以为它在该删箱里"，服务端才是"它实际在哪"。
    清单若只信 state，就永远不知道自己过期了。这正是 G9「死坐标」的同一个病。

    判据：逐项查服务端 parentId；**不是收件箱 → 从清单移除并报告**。
    查不到（网络/节点已删）→ **保留**并标"无法确认"，不擅自删项（误删清单比留脏更危险）。
    """
    if not check or not items:
        return items, []
    keep, stale, unknown = [], [], 0
    for x in items:
        ok, pid, why = _parent_of(x['id'])
        if not ok:
            x['_verify'] = f'无法确认位置（{why}）'
            keep.append(x); unknown += 1
        elif pid == inbox:
            keep.append(x)
        else:
            stale.append((x, pid))
    return keep, stale


def _parent_of(node_id):
    """查服务端真实父节点。返回 (ok, parentId, why)。**失败不折成"不在"。**"""
    import subprocess as _sp
    try:
        r = _sp.run([sys.executable, '/root/.codebuddy/skills/library/space_api.py',
                     'space.workspace.node-info', '--node-id', node_id],
                    capture_output=True, text=True, timeout=60)
    except Exception as e:
        return False, None, type(e).__name__
    for ln in (r.stdout or '').splitlines():
        ln = ln.strip()
        if ln.startswith('{'):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            node = (d.get('data') or {}).get('node') or {}
            if node:
                return True, node.get('parentId'), ''
            return False, None, str(d.get('error') or 'node 为空')[:40]
    return False, None, '未返回 JSON'


def _probe_empty_databases(items, control_id='Xjqn2Wf5EKPSGIXqQ2K6Dc'):
    """对清单里的 database 项逐个直连核实：真的空，还是只是读不到。

    返回 {'lines': [头部说明行], 'by_id': {id: 每项结论短语}}

    ★ 判据来自 `database/get_database_content.py`：它返回的 `content` 是 CSV 文本，
      首行永远是表头。**非空行数 == 1 表示 0 条记录**，>1 表示有数据。
    ★ 对照组（`control_id`=账房总账，已知有数据）必须先读出 >1 行，
      否则说明"这条通路读不到数据"，此时所有「空」都不可采信 → 一律标「无法判定」。
      没有对照的"全部为空"是假绿：读失败和真空长得一模一样。
    ★ 任何一项读失败 → 标「无法判定」，**不折叠成"空"**（M-R60）。
    """
    dbs = [x for x in items if (x.get('kind') == 'database')]
    out = {'lines': [], 'by_id': {}}
    if not dbs:
        return out
    try:
        # ★ 显式路径，不靠调用者预先污染 sys.path。
        #   实测教训：经 `tvbox janitor list` 跑时成功，直接 `import janitor` 调则
        #   ModuleNotFoundError——因为 core 在 source-hub/scripts，而 tvbox 入口
        #   恰好先 import 过它。**依赖调用者的副作用 = 换个入口就变行为**。
        _hub_scripts = '/root/.codebuddy/skills/tvbox-source-hub/scripts'
        if _hub_scripts not in sys.path:
            sys.path.insert(0, _hub_scripts)
        import core as _core
    except Exception as e:
        out['lines'] = [f'> ⚠️ 数据库核实跳过：无法加载 library core（{type(e).__name__}）——'
                        f'**下列数据库是否为空未经验证**，删除前请自行确认。']
        return out

    def _rows(did):
        ok, so, se = _core._run_lib('database/get_database_content.py', '--database-id', did)
        if not ok:
            return None, (se or 'run_lib 失败')[:80]
        try:
            c = json.loads(so).get('content') or ''
        except Exception as e:
            return None, f'返回解析失败 {type(e).__name__}'
        return len([x for x in c.split('\n') if x.strip()]), ''

    ctl, ctl_err = _rows(control_id)
    trusted = bool(ctl and ctl > 1)
    if trusted:
        out['lines'] = [f'> **数据库类 {len(dbs)} 项已逐个直连核实**（对照组 `账房·总账` 读出 '
                        f'{ctl - 1} 条记录，证明该通路能读到数据，不是"读不到一律算空"）。'
                        f'删库**不可逆**，故单列依据。']
    else:
        out['lines'] = [f'> ⚠️ **数据库核实不可信**：对照组只读出 {ctl} 行（应为 >1），'
                        f'{ctl_err or "通路可能读不到数据"}——'
                        f'下方各库的"空"**不能作为删除依据**，请手工确认。']

    empty_n = unk_n = data_n = 0
    for x in dbs:
        n, err = _rows(x['id'])
        if n is None:
            out['by_id'][x['id']] = f'（⚠️ 无法判定：{err}）'
            unk_n += 1
        elif n <= 1:
            if trusted:
                out['by_id'][x['id']] = '（✅ 已核实：仅表头，0 条记录）'
                empty_n += 1
            else:
                out['by_id'][x['id']] = '（⚠️ 读到 0 条，但对照组也读不到，**无法判定**）'
                unk_n += 1
        else:
            out['by_id'][x['id']] = f'（🔴 **有 {n - 1} 条记录**，不是空的，删前务必确认）'
            data_n += 1

    if trusted:
        out['lines'].append(f'> 核实结果：确认空 **{empty_n}** ｜ 仍有数据 **{data_n}** ｜ '
                            f'无法判定 **{unk_n}**')
    else:
        out['lines'].append(f'> 核实结果（不可信）：读到空 {empty_n} ｜ 有数据 {data_n} ｜ '
                            f'无法判定 {unk_n}')
    if data_n:
        out['lines'].append('> 🔴 **有数据库不为空**，它们不该按"废弃"直接删——见下表红标。')
    return out


def cmd_list(a):
    st = load_state()
    items = st.get('items') or []
    inbox = st.get('inbox')
    # ★ S158：先剔除陈旧项（清单挂着、人早已不在收件箱），再出单
    items, stale = _prune_stale(items, inbox, check=not getattr(a, 'no_verify', False))
    if stale:
        print(f'🧹 剔除 {len(stale)} 项陈旧清单项（已不在收件箱，清单过期）：')
        for x, pid in stale:
            print(f'   · {x["title"][:36]}（现父节点 {pid}）')
        st['items'] = [x for x in (st.get('items') or [])
                       if x['id'] not in {s[0]['id'] for s in stale}]
        st['pruned'] = st.get('pruned', []) + [
            {'id': x['id'], 'title': x['title'], 'reason': '移出收件箱后清单未同步',
             'actual_parent': pid, 'at': time.strftime('%Y-%m-%d %H:%M')}
            for x, pid in stale]
        save_state(st)
    lines = ['# 🗑 待删清单（清道夫 · 需人工删除）', '',
             f'> 生成时间：{time.strftime("%Y-%m-%d %H:%M")} ｜ 共 **{len(items)}** 项',
             '> 云端无删除 API —— 请**打开收件箱文件夹**，按下表逐项删除即可。', '']
    # ★ S158 三补：数据库类必须当场核实「是否真的空」。
    #   删库不可逆，而清单原先只给「标题含废弃标记」——名字里有"废弃"不等于里面没数据。
    #   更要命的是：上一轮我把核实结论**手写补进 md**，下一轮 `list` 重跑就整段冲掉了，
    #   等于没修。核实必须长在生成流程里，不能贴在产物上。
    db_probe = _probe_empty_databases(items)
    if db_probe['lines']:
        lines += db_probe['lines'] + ['']
    lines += ['| # | 标题 | 类型 | 节点ID | 原因 | 已移入收件箱 |',
              '|---|---|---|---|---|---|']
    for i, x in enumerate(items, 1):
        mv = '✅' if x.get('moved') else '⚠️ 未移动'
        reason = x.get('reason', '')
        _pb = db_probe['by_id'].get(x['id'])
        if _pb:
            reason = f'{reason} {_pb}'
        lines.append(f"| {i} | {x['title']} | {x.get('kind','')} | `{x['id']}` | "
                     f"{reason} | {mv} |")
    md = '\n'.join(lines) + '\n'
    # 规则51 命名合规：类别标签 + MMDD 日期段 + 描述（避免 watchdog M08 判遗留）
    _mmdd = time.strftime('%m%d')
    base = f'S104_{_mmdd}_待删清单'
    p_md = os.path.join(OUT_DIR, base + '.md')
    open(p_md, 'w', encoding='utf-8').write(md)
    # CSV
    import csv
    p_csv = os.path.join(OUT_DIR, base + '.csv')
    with open(p_csv, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['序号', '标题', '类型', '节点ID', '原因', '已移入收件箱'])
        for i, x in enumerate(items, 1):
            w.writerow([i, x['title'], x.get('kind', ''), x['id'],
                        x.get('reason', ''), '是' if x.get('moved') else '否'])
    print(f'📄 待删清单已落盘：{p_md}')
    print(f'📄 CSV：{p_csv}')
    # **S156 修：摘要不许只报总数。**
    # 「共 40 项」这句话害人不浅——它把「已真的移进收件箱」和「登记了但没移动」
    # 混成一个数字，于是 40 项里只有 18 个真在收件箱这件事，谁也看不出来。
    # **清单条数 ≠ 收件箱条数**，两者必须分开报，否则待删清单会变成自我安慰的假账。
    _moved = sum(1 for x in items if x.get('moved'))
    _not = len(items) - _moved
    print(f'   共 {len(items)} 项 ｜ 已移入收件箱 {_moved} ｜ ⚠️ 未移动 {_not}')
    if _not:
        print(f'   （未移动项 = 登记了但没挪进收件箱，见清单末列「⚠️ 未移动」）')
    return 0


def cmd_render(a):
    """把清单重写进收件箱内的说明文档（需知道文档 node-id）。"""
    print('（render：清单文档由 list 生成 Markdown，可用 create-doc 手动更新收件箱说明）')
    return 0


def cmd_census(a):
    """★ S158 大生态/小生态分层普查：回答「这块归谁管」，而不只是「这块是不是垃圾」。

    **为什么需要它**：`scan` 用的是关键词判定（废弃/测试/重复…），它回答的是
    「这个节点像不像垃圾」；但用户真正反复问的是「**图中这个归谁管**」——
    归属这件事，关键词永远答不上来。于是每次都要人临场翻 SKILL.md、翻坐标表，
    翻完就散，下一轮再翻一遍。本命令把「归属」变成一次扫描即可回答的问题。

    判定法（与 `references/eco_layers.md` 的三层一缝严格对齐）：
      · 从上往下走，一旦进入 `TERRITORY` 登记的容器，**整棵子树**计为该技能的责任田（L1）；
      · 走完整棵树仍没进任何责任田的节点 → 「缝」（L0 无主区）；
      · 缝按 **顶层分区** 聚合，因为「治理单位」就是分区，不是单个节点——
        一个 232 节点的裸文件夹，逐个列出来没人看得懂，必须先给出"哪个区没人管"。

    输出：控制台分区表 + JSON 明细（--json）。
    """
    from collections import Counter
    st = load_state()
    inbox = st.get('inbox') or _find_inbox()[0]
    rows, seen = [], set()
    # ★ S158 修：归属从**合并真源**取，不再只用 TERRITORY（否则 zone_registry 是死表）
    _territory, _tprob, _pref_rules = load_territory()


    def _by_prefix(title):
        """根层级散落组：按标题前缀判定归属（容器型挂法挂不住这类节点）。"""
        for _p, _o, _n in _pref_rules:
            if title.startswith(_p):
                return _o, _n
        return None
    for _pb in _tprob:
        print(f'   ⚠️ 归属真源不完整：{_pb}')

    def walk(pid, path, owner, note, depth=0):
        if pid in seen or depth > 12:
            return
        seen.add(pid)
        if inbox and pid == inbox and depth > 0:
            return  # 收件箱子树不普查（它本身就是待删暂存）
        kids = _kids(pid)
        if not kids and depth > 0:
            return
        cnt = Counter((k.get('title') or '').strip() for k in kids if k.get('title'))
        for n in kids:
            nid = n.get('id') or n.get('nodeId')
            if not nid or nid == inbox:
                continue
            t = (n.get('title') or '').strip()
            k = n.get('kind') or ''
            # 进入登记容器 → 整棵子树归该技能
            cur_owner, cur_note = owner, note
            if nid in _territory:
                cur_owner, cur_note = _territory[nid]
            elif cur_owner is None:
                # ★ S158：根层级散落组（同名文档堆）不会进容器，只能靠前缀认领
                _hit = _by_prefix(t)
                if _hit:
                    cur_owner, cur_note = _hit
            rows.append({
                'id': nid, 'title': t, 'kind': k,
                'path': '/'.join(path + [t]),
                'depth': depth, 'owner': cur_owner, 'owner_note': cur_note,
                # ★ S158：原先写 `nid in TERRITORY`，registry 指派的区会一律 False
                #   → JSON 明细对外断言"这里不是归属入口"，与 owner 字段自相矛盾。
                'entered_here': cur_owner is not None and (nid in _territory),
                'dup_in_parent': cnt.get(t, 0),
                'updatedAt': n.get('updatedAt'),
            })
            if k == 'folder':
                walk(nid, path + [t], cur_owner, cur_note, depth + 1)

    walk('', [], None, None)

    if not rows:
        print('❌ 普查结果为空——**无法判定**（不是"空间是空的"，M-R60）：'
              'list-nodes 全部失败，请先查 library 授权/网络')
        return 2

    gaps = [r for r in rows if not r['owner']]
    owned = [r for r in rows if r['owner']]
    # 缝按顶层分区聚合
    zone_cnt = Counter()
    for r in gaps:
        zone_cnt[r['path'].split('/')[0]] += 1

    print(f'═══ 大生态分层普查（L0 全空间）═══')
    print(f'节点合计 {len(rows)} ｜ L1 责任田内 {len(owned)} ｜ '
          f'缝（无主）{len(gaps)}（{100*len(gaps)//max(1,len(rows))}%）')
    print(f'按类型：{dict(Counter(r["kind"] for r in rows))}')
    print()
    print('── 缝（L0 无主区）按顶层分区 ──')
    print(f'{"节点数":>6}  顶层分区')
    for z, c in zone_cnt.most_common():
        print(f'{c:6d}  {z[:56]}')
    print()
    print('── 责任田覆盖 ──')
    oc = Counter(r['owner'] for r in owned)
    for s, c in oc.most_common():
        print(f'{c:6d}  {s}')
    print()
    # 缝里最扎眼的两类：重复堆积 + 根层级散落
    root_gap = [r for r in gaps if r['depth'] == 0]
    if root_gap:
        print(f'── 根层级无主节点 {len(root_gap)} 个（这就是"资料库看起来很乱"的直接来源）──')
        for r in sorted(root_gap, key=lambda x: (x['kind'], x['title'])):
            d = f' ×{r["dup_in_parent"]}' if r['dup_in_parent'] > 1 else ''
            print(f'  [{r["kind"]:8s}] {r["title"][:50]}{d}')
        print()
    dup = [r for r in rows if r['dup_in_parent'] > 1]
    if dup:
        print(f'── 同父同名重复 {len(dup)} 个节点 / '
              f'{len(set((r["path"].rsplit("/",1)[0], r["title"]) for r in dup))} 组 ≥2 份 ──')
        for (pz, t), c in Counter((r['path'].rsplit('/', 1)[0], r['title']) for r in dup).most_common(12):
            print(f'  ×{c:3d}  {t[:46]:48s} @ {pz.split("/")[0][:28]}')
        print()
    if getattr(a, 'json', False):
        p = os.path.join(OUT_DIR, 'S158_大生态分层普查.json')
        json.dump({'at': time.strftime('%Y-%m-%d %H:%M:%S'),
                   'total': len(rows), 'owned': len(owned), 'gaps': len(gaps),
                   'rows': rows}, open(p, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        print(f'📄 明细已落盘：{p}')
    return 0


def _node_parent(nid):
    """反查节点的真实父节点。node-info 的 parentNodeId 实测恒为 None（不可信），
    故走全树遍历 _find_parent。返回 '' 表示查不到（调用方必须显式报，不许当"根"）。"""
    try:
        return _find_parent(nid, skip_inbox=False) or ''
    except Exception:
        return ''


def cmd_move(a):
    """★ 节点移动（v1.2.0）：把任意节点挪到指定父节点下。

    为什么归清道夫：本技能已是全生态**唯一**常驻持有 space.move-node 的地方
    （add/restore 两条路径都靠它）。此前「节点移动改名」提案长期挂着，
    真实原因是**没有统一入口**——各处零散调用 move-node，没人做「移前校验存在 +
    移后回读确认」。本命令把这两条做成硬门。

    三态（M-R60）：
      移前：节点不存在 / 目标父不存在 → 直接拒，不动；
      移中：move-node 返回 error / status=pending（跨空间异步）→ 如实报，不假装成功；
      移后：**回读**（再查一次 node-info 的 parentId + 遍历确认目标下确有该节点），
            回读不符 → 退出码 3，明说"服务端说成功但位置不符，疑似索引延迟，请复核"。
    """
    nid = a.node
    target = a.to
    info = _space('space.workspace.node-info', node_id=nid)
    node = ((info or {}).get('data') or {}).get('node') or {}
    title = node.get('title') or ''
    if not title:
        print(f'❌ 节点不存在或不可读（{nid}）——不移动（M-R60 如实报）')
        return 1
    tinfo = _space('space.workspace.node-info', node_id=target) if target else None
    tnode = ((tinfo or {}).get('data') or {}).get('node') or {}
    if not tnode.get('id') and not tnode.get('title'):
        print(f'❌ 目标父节点不存在（{target}）——不移动')
        return 1
    if tnode.get('kind') and tnode.get('kind') != 'folder':
        print(f'❌ 目标不是文件夹（{target} kind={tnode.get("kind")}）——不移动')
        return 1
    src = _node_parent(nid)
    mv = _space('space.workspace.move-node', node_id=nid, target_parent_id=target)
    if not mv or mv.get('error'):
        print(f'❌ 移动失败：{str((mv or {}).get("error"))[:160] or "无响应"}')
        return 1
    data = mv.get('data') or {}
    st = data.get('status') or ''
    if st == 'pending':
        tid = data.get('taskId') or ''
        print(f'⏳ 跨空间移动已受理，异步进行中（taskId {tid}）——'
              f'**尚未落地**，请用 move-node-progress 跟踪后再确认')
        return 3
    # 移后回读：目标下确有该节点
    landed = any((n.get('id') or n.get('nodeId')) == nid for n in _kids(target))
    if not landed:
        print(f'⚠️ 服务端回报成功，但目标文件夹 {target} 下**未枚举到** {nid}'
              f'——疑似索引延迟或移动未落地，请复核（不当作成功）')
        return 3
    print(f'✅ 已移动：{title}（{nid}）\n   从 {src or "（未知，全树未反查到）"} → {target}')
    return 0


def cmd_rename(a):
    """★ 节点改名（v1.2.0）：移后回读标题，改不动不许说改好了。"""
    nid = a.node
    new = (a.title or '').strip()
    if not new:
        print('❌ 新标题不能为空'); return 1
    info = _space('space.workspace.node-info', node_id=nid)
    node = ((info or {}).get('data') or {}).get('node') or {}
    old = node.get('title') or ''
    if not old:
        print(f'❌ 节点不存在或不可读（{nid}）——不改名')
        return 1
    r = _space('space.workspace.rename-node', node_id=nid, title=new)
    if not r or r.get('error'):
        print(f'❌ 改名失败：{str((r or {}).get("error"))[:160] or "无响应"}')
        return 1
    back = _space('space.workspace.node-info', node_id=nid)
    now = (((back or {}).get('data') or {}).get('node') or {}).get('title') or ''
    if now != new:
        print(f'⚠️ 服务端未报错，但回读标题仍是「{now}」（期望「{new}」）'
              f'——不当作成功，请复核')
        return 3
    print(f'✅ 已改名：{old} → {now}（{nid}）')
    return 0


def cmd_restore(a):
    """★ 误投回退（v1.1.7）：把节点从收件箱移回原位，并从清单移除。

    原位 = add 时记录的 origin_parent（全树反查所得，不依赖 node-info）。
    旧版清单项（v1.1.6 前投递）无 origin_parent 记录 → 需 --target 手动指定回退目标。
    """
    st = load_state()
    nid = a.node
    item = next((x for x in st['items'] if x['id'] == nid), None)
    if not item:
        print(f'  ⚠️ 节点不在待删清单：{nid}'); return 1
    target = (getattr(a, 'target', None) or '').strip() or item.get('origin_parent') or ''
    if not target:
        print(f'  ⚠️ 无原位记录（该项为旧版投递，未存 origin_parent）。')
        print(f'     请指定回退目标：janitor restore --node {nid} --target <父节点ID>')
        return 1
    mv = _space('space.workspace.move-node', node_id=nid, target_parent_id=target)
    ok = bool(mv and not mv.get('error'))
    if ok:
        st['items'] = [x for x in st['items'] if x['id'] != nid]
        save_state(st)
        print(f'  ✅ 已移回原位 {target}：{item.get("title")}（{nid}）')
    else:
        print(f'  ❌ 移回失败：{str(mv.get("error"))[:160] if mv else "无响应"}')
    # 返回值必须说真话（S156 教训）
    return 0 if ok else 3


def auto_file(node_id, reason, quiet=False):
    """★ 生态联动便捷入口（供其他技能 import 调用）：
    把单个节点移入收件箱并登记清单。**永不抛异常**——清理是"尽力而为"的旁路，
    绝不因投递失败而阻断上游主流程（但失败会显式打印告警，不静默）。

    用法（其他技能里）：
        sys.path.insert(0, '/root/.codebuddy/skills/tvbox-janitor/scripts')
        import janitor; janitor.auto_file(nid, '建库失败残留')
    """
    try:
        iid = load_state().get('inbox') or _find_inbox()[0]
        if not iid:
            # 收件箱未建 → 尝试建
            if not _try_inbox() and not quiet:
                print(f'  ⚠️ 清道夫收件箱不可用，节点 {node_id} 未投递（请人工删）')
            iid = load_state().get('inbox') or _find_inbox()[0]
        if not iid:
            return False
        return _try_add(node_id, reason, quiet)
    except Exception as e:
        if not quiet:
            print(f'  ⚠️ 清道夫投递异常（{type(e).__name__}）：节点 {node_id} 未入收件箱，请人工删')
        return False


def _try_inbox():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest='cmd'); sub.add_parser('inbox')
    return cmd_inbox(ap.parse_args(['inbox'])) == 0


def _try_add(node_id, reason, quiet):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest='cmd')
    ad = sub.add_parser('add'); ad.add_argument('--node', required=True)
    ad.add_argument('--reason', default=''); ad.add_argument('--force', action='store_true')
    a = ap.parse_args(['add', '--node', node_id, '--reason', reason or '（未注明）'])
    return cmd_add(a) == 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='janitor.py')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('inbox')
    ad = sub.add_parser('add')
    ad.add_argument('--node', required=True)
    ad.add_argument('--reason', default='')
    ad.add_argument('--force', action='store_true',
                    help='跳过"非空/像备份"保护（S158 护栏）。**明确承担被整批删除的风险**')
    sc = sub.add_parser('scan')
    sc.add_argument('--dry-run', action='store_true', dest='dry_run',
                    help='仅预览候选垃圾，不实际移动到收件箱')
    sc.add_argument('--dedup', type=int, default=0, metavar='N',
                    help='把「同父同名且份数>N」的多余副本投递收件箱（保留最早一份）。'
                         '默认 0=只告警不投。S158 加：根层级有 26 份同名留档文档，'
                         '旧行为永远只告警，越积越乱')
    _lp = sub.add_parser('list')
    _lp.add_argument('--no-verify', dest='no_verify', action='store_true',
                     help='跳过服务端位置校验（默认逐项核 parentId，剔除已迁出的陈旧项）')
    sub.add_parser('render')
    cs = sub.add_parser('census')
    cs.add_argument('--json', action='store_true',
                    help='额外落盘 JSON 明细（/workspace/S158_大生态分层普查.json）')
    rs = sub.add_parser('restore')
    rs.add_argument('--node', required=True)
    rs.add_argument('--target', default='', help='无原位记录时，手动指定回退到的父节点ID')
    mo = sub.add_parser('move')
    mo.add_argument('--node', required=True)
    mo.add_argument('--to', required=True, help='目标父文件夹节点 ID')
    rn = sub.add_parser('rename')
    rn.add_argument('--node', required=True)
    rn.add_argument('--title', required=True)
    a = ap.parse_args(argv)
    return {'inbox': cmd_inbox, 'add': cmd_add, 'scan': cmd_scan,
            'list': cmd_list, 'render': cmd_render, 'census': cmd_census,
            'restore': cmd_restore, 'move': cmd_move, 'rename': cmd_rename}[a.cmd](a)


def dispatch(argv=None):
    """供调度器 tvbox 在同进程内直接调用（与 blk/evolve/link 同契约）。"""
    return main(list(argv or []))


if __name__ == '__main__':
    raise SystemExit(main())
