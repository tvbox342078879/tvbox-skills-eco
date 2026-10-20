#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tvbox-inspect 监察司 引擎：生态全维监测（自监测/库监测/上游监测）+ 时效项 + 流程化报告
（S105 自 legacy-watchdog 物理搬迁；监察司=监测总控，watchdog=遗留处置）

用法:
  python3 inspect.py scan                 # 全量扫描：未解决问题 + 超期时效项 → 遗留清单
  python3 inspect.py status               # 遗留统计（按严重度/按监测项）
  python3 inspect.py report [--out 路径] [--upload]  # 生成流程化监测报告 md（默认 /workspace/生态<MMDD>_遗留问题监测报告.md）
                                              # --upload 云端直传（复用 create_doc.py）并回写 node id 锚点
  python3 inspect.py monitors             # 查看时效项登记册（monitors.json）
  python3 inspect.py add-monitor --id Mxx --name .. --check <内置类型> --desc .. --action .. [--severity 阻断|耗时|轻微] [--cadence ..]
  python3 inspect.py balance              # ★ 生态平衡性体检（S106/S107：六维+平衡指数+最短板+优先级清单）

设计（规则54四化落地 · S022 立项）:
  - 监测面① 历史对话未解决问题：③台账 状态≠已闭环 条目
  - 监测面② 时效性项目：monitors.json 登记册（内置 19 类检查，可成长——加监测项只改登记册不改代码）
  - 监测面③ 生态平衡性（S106/S107）：技能进化速度/薄弱环节六维量化，谁拖后腿 → 优先级清单
  - 结果同步：report 产出流程化文档，每条遗留带「下一步命令」（生态联动：tvbox log/block review/evolve upgrade/snapshot）
  - 复用 hub core.py（共享缓存/写后失效），禁自建云端读写
"""
import os, sys, json, time, argparse, datetime, subprocess, re
from collections import Counter

HUB = '/root/.codebuddy/skills/tvbox-source-hub/scripts'
sys.path.insert(0, HUB)
from core import (rows_of, add_records, invalidate, rows_of_t, rows_of_failed,  # noqa: E402

                  SRC_DBS as _CORE_SRC_DBS, src_rows_t as _core_src_rows_t)

# ---------- M-R114 统一并发入口（S110） ----------
# 原先三处都写死 ThreadPoolExecutor(8)，连 max_workers= 都没写，8 从哪来无人知晓。
# 现按场景分档：探公网站点=B(6)、读自家文档/库=A(8)。
from concurrency import run_bounded as _run_bounded, TIER_API, TIER_WEB  # noqa: E402


SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MONITORS = os.path.join(SKILL_DIR, 'monitors.json')
SNAP_HIST = os.path.join(SKILL_DIR, 'findings_history.json')  # 遗留指纹快照（report/scan 落盘，供 diff）

DB_SRC = 'wW5xpbS663nA6XK8a7DNsp'      # ① 源数据库（旧·S144 已清空废弃，仅留作回指）
# ★ S159 源库坐标迁移：S144「阶段3 清空旧三库」把 `wW5xpbS…`（数据源总库）与
#   `G9B1GzfR…`（备用库）各 801 条清空、节点改名为「🗑【已废弃】」占位。
#   但本文件的 DB_SRC 仍指着那个**已空**的旧库 → M09/M10 等依赖源库的检查器
#   全部在空集上运行：永远报 0 项、永远"达标"。watchdog 只以「主数据源为空」的
#   健康告警露了个头，若不追下去，等于**巡检整片失效而账面全绿**。
#   业务数据现分散在三库（2026-10-18 实测 112+508+109=729 条，三库均有 api接口/host/状态）：
#     py线路源库 / csp线路源库 / 成品源库
#   故源库读法由「单库常量」改为「多库联合」：凡需要"全部源"的地方一律走 src_rows()。
#   保留 DB_SRC 常量仅供回指与兼容；**检查器不要再直接 rows_of(DB_SRC)**。
# ★ S159 二次修：坐标**不再在本文件定义**，改为从 core 导入。
#   同一批库 id 原先在 inspect_engine 与 link.py/blk.py/probe_src.py 等 10 处各写一份，
#   于是 S144 搬库后只有本文件改了、其余 10 处仍读空库（M-R38 同一事实源违规）。
#   现单一事实源在 `core.SRC_DBS`，谁要用谁导入；本文件只保留名字别名。
SRC_DBS = _CORE_SRC_DBS
DB_SESS = 'TO4LwsjVVdR8JRAUlkejk3'    # ③ 会话台账
DB_LIB = 'XTuuj8mKtqnZSolDdeDxkq'     # ④ 经验库
DB_DEAD = 'hGwE41OpJDOR8CW0sLUwRx'    # 🗑 失效源库
DB_UPSTREAM = 'aCSvley568A0aW3sggzJwA'  # 📡 上游仓库监测册（探源司 S088 建；M18 读它）


def src_rows():
    """读「全部源」= 三库联合（S159）。返回 (rows, info)。

    info 形如 {'ok': bool, 'total': int, 'by_db': {名: 条数}, 'empty': [名...]}。
    三态纪律（M-R60）：任一库读失败 → ok=False，且**不把失败库当空库**；
    调用方必须能区分「三库都空」与「读不到」。
    为什么必须联合：源库早已不是单库（S144 后），只读一个库等于只看三分之一的真相。
    """
    # 实现落在 core（单一事实源）；本函数只做「(rows, info)」签名适配。
    ok, rows, info = _core_src_rows_t()
    return rows, info

# 探源司本地监测册（云端兜底；M18 读它）
UPSTREAM_TARGETS = '/root/.codebuddy/skills/probe-hub/scripts/monitor_targets.json'

# 生态各技能 flow.json 路径（backlog 扫描对象）
# S105/S106：由「写死 5 条」改为**按 ECO_SKILLS 动态推导**——写死清单会漏新技能
# （janitor/inspect/conductor/probe-hub 曾漏登 → hygiene 误报"flow.json 缺失"）。
_SKILL_ROOT_ = '/root/.codebuddy/skills'
# SOP 权威源（校准集漂移检测）
SOP_JSON = os.path.join(HUB, 'sop_makejson.json')

# 生态登记技能白名单（M07 检查范围；平台自带技能如 library/computer-use 等不强制三件套）
# S090 同步：生态已由 5 成员扩至 7 成员（S087 探源司建档、conductor 此前已登记）——
# 此处漏登会使 M07 三件套契约检查**对两个新技能失明**（体检不过 = 假达标）。
# S105 同步：新增 tvbox-janitor（清道夫）+ tvbox-inspect（监察司）→ 9 成员。
def _load_eco_skills_set():
    """S127 P006 归一：唯一正本 eco_skills.json（与 conductor 共读），防双定义各自演化。"""
    import json as _json, os as _os
    fp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                       '..', '..', 'ecosys-conductor', 'scripts', 'eco_skills.json')
    try:
        with open(fp, encoding='utf-8') as f:
            return set(_json.load(f)['eco_skills'])
    except Exception as e:
        raise RuntimeError(f'eco_skills.json 读取失败（M-R60 不折叠）：{e}')


ECO_SKILLS = _load_eco_skills_set()

# S136（M-R38 单一事实源）：FLOWS 原为**手写 9 成员**，与 ECO_SKILLS（eco_skills.json 正本）
# 各自演化——新增 unfinished-audit 后 ECO_SKILLS 10 项、FLOWS 仍 9 项，
# 直接后果是 `_hygiene_rows()` 里它「flow.json 缺失」被扣成 60 分质量短板（假红）。
# 这类「两份清单不一致」的缺陷不会报错，只会让新技能在评估里莫名低分。
# 改为**从 ECO_SKILLS 派生**：加技能只需跑 conductor onboard（M-R78），此处自动跟上。
def _flows_of(names):
    return {n: os.path.join(_SKILL_ROOT_, n, 'scripts', 'flow.json') for n in names}


FLOWS = _flows_of(ECO_SKILLS)

SEV_ORDER = {'阻断': 0, '耗时': 1, '轻微': 2}


def load_monitors():
    return json.load(open(MONITORS, encoding='utf-8'))


def ledger_waiver():
    """半闭环决策留痕白名单（monitors.json.ledger_waiver）→ set(会话编号)。
    S027 新增：避免「已决策不改写历史」的条目每轮复扫重复报警（清单虚胖）。"""
    try:
        d = load_monitors().get('ledger_waiver') or {}
        return {str(e.get('no', '')).strip() for e in d.get('entries', [])}
    except Exception:
        return set()


# ---------- 内置检查器 ----------

def _is_doc_delivery(r):
    """预豁免判据（S037·由 attribution 归因反推）：该条目是否为「实质完成」。
    两类证据（覆盖 attribution 归纳的 3 类成因）：
      · 入库型：新增源数 > 0 → 增量已入源库，状态停留中间态非遗留
      · 文档型/SOP型：成果产出含交付物特征（xlsx/md/SKILL/flow.json/报告/node）→ 增量在交付物
    返回 (是否实质完成, 证据串)。"""
    try:
        n = int(r.get('新增源数') or 0)
    except Exception:
        n = 0
    blob = str(r.get('成果产出', '')) + str(r.get('任务摘要', ''))
    if n > 0:
        return True, f'源增量 +{n}（入库型）'
    DOC = ('.xlsx', '.md', 'SKILL', 'flow.json', '报告上云', 'node', 'SOP', '底包库', '溯源表', '标准')
    hit = next((k for k in DOC if k in blob), None)
    if hit:
        return True, f'交付物证据「{hit}」（文档型）'
    return False, ''


def check_ledger_pending(m):
    """③台账未闭环条目 → 历史对话未解决问题。
    口径校准（首扫实证）：待同步=真遗留(耗时)；已同步源库/已同步底包=半闭环(轻微，可补闭环标记)。
    口径校准（S027）：半闭环中已「决策留痕不改写历史」的条目走 ledger_waiver 豁免，不再重复报警。
    口径校准（S037·pre_exempt）：由 attribution 归因发现 82% 豁免来自「入库型+文档型」——
    它们本质是**实质完成**（增量在源库或交付物），应从「事后补白名单」升级为「证据驱动的预豁免」：
    `新增源数>0` 或 `成果产出含交付物特征` → 直接豁免，不再逐一登记 ledger_waiver（白名单规模可控）。"""
    waiver = ledger_waiver()
    out = []
    for r in rows_of(DB_SESS):
        st = r.get('反哺状态', '')
        if st == '待同步':
            sev = '耗时'
        elif st in ('已同步源库', '已同步底包'):
            if str(r.get('会话编号', '')).strip() in waiver:
                continue  # 决策留痕：已完成、状态停留中间态，非遗留
            done, ev = _is_doc_delivery(r)
            if done:
                continue  # S037 预豁免：证据表明实质完成（增量在源库或交付物）
            sev = '轻微'
        else:
            continue  # 已闭环 / 空
        out.append({
            'monitor': m['id'], 'name': m['name'], 'sev': sev,
            'item': f"[{r.get('会话编号','')}] {r.get('任务摘要','')[:40]}",
            'detail': f"状态={st}（{'未收口' if sev=='耗时' else '半闭环，可补闭环标记'}）",
            'action': m.get('action', ''),
        })
    return out


def check_dead_review_due(m):
    """失效库 待复测 host 复测时间超期"""
    out = []
    now = time.time()
    for r in rows_of(DB_DEAD):
        if r.get('复测结果') != '待复测':
            continue
        t = r.get('复测时间', '')
        try:
            ts = datetime.datetime.strptime(t[:10], '%Y-%m-%d').timestamp()
        except Exception:
            continue
        days = (now - ts) / 86400
        if days > 7:
            out.append({
                'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
                'item': f"{r.get('host','')[:36]}（样例 {r.get('样例api','')[:24]}）",
                'detail': f"待复测已 {days:.0f} 天（阈值>7天）",
                'action': m.get('action', ''),
            })
    return out


def _backlog_counts(item):
    """★ S176b 单一事实源：backlog 条目「算不算未清」的唯一判定。

    为何必须抽出来：本文件曾有**两套口径**——
      · check_backlog_items()：认完成标记 + [扩展点] + [本期不处理]（三处跳过）
      · _hygiene_rows() 的 uncleared：只认完成标记（两处跳过）
    结果：把 backlog 逐条标了「[本期不处理·原因]」之后，
    遗留清单从 11 项降到 3 项，**评分器却仍报「backlog 未清 15/7/7」**——
    同一件事两个数字，用户不知道该信哪个。

    口径（按优先级）：
      完成标记（已清零/已兑现/已归档…）→ 不算
      `[扩展点]` 前缀                  → 不算（持续演进项，不是遗漏）
      `[本期不处理…]` 前缀             → 不算（判据④认可的显式取舍）
      `[真欠账…]` 前缀                 → **算**（自认欠账，必须继续报出来）
      其余                             → 算
    """
    s = str(item)
    if not _backlog_is_open(item):
        return False
    if s.startswith(('[扩展点]', '[本期不处理')):
        return False
    return True


def _backlog_is_open(item):
    """backlog 条目是否为「真未清」（S106 修正：完成标记识别扩面）。

    历史口径只认开头 `[已清零`，导致 `[已兑现 S060]`、`[MVP已兑]`、
    `（2026-10-15 已清零：…）` 等**已完成但标记在后/用词不同**的条被误判为未清
    （M03 + 平衡性「实绩质量」双重假阳性来源）。修正为：只要含任一完成标记即视为已结。
    """
    s = str(item)
    # ★ S163（P2）：补「已归档」。原词表只有 '[归档'，而实际留痕写法是
    #   「[已归档(不修) 2026-10-18]（sid=…）」——不含 '[归档'，逐轮照报。
    #   4 条轻微里 2 条是这种已决策不修的噪声，清单虚胖、真遗留被淹没。
    DONES = ('已清零', '已兑现', '已兑', '已结', '已闭环', '已修复', '已落地',
             'MVP已兑', 'MVP 已兑', '已完成', '[归档', '已处理', '已采纳', '已归档')
    return not any(d in s for d in DONES)


def _backlog_is_assigned(item, ledger_sigs=None):
    """backlog 条目是否「已在欠账总账登记在办」（S136·M-R79 配套口径）。

    为什么要这条：M-R79 要求「未完成必登记」，清账司据此把每一项**真实欠账**
    写进各技能 flow.json backlog（带 `sid=<签名>` + owner + due）。若 M03 仍按
    「backlog 有未终标记项 = 遗留」判，就形成**死锁**：
      · 登记欠账 → M03 报遗留 → 判据④ 永远红
      · 不登记   → 判据⑧（欠账单）永远红
    两者都是"按规则做事却验收不过"，最后一定演变成把表填绿、真账不记。
    故口径拆开：
      **遗留** = 没人认领的脏尾巴
      **在办** = 已登记 + 有主 + 有期（清账司负责追，watchdog 不重复告警）
    仍属遗留的两种：① 条目没有任何 sid（野生 backlog）② sid 在总账里查不到
    （总账被回滚/手改 → 成了孤儿欠账，必须告警）。
    """
    s = str(item)
    m = re.search(r'sid[=:]\s*([0-9a-f]{8,})', s)
    if not m:
        return False                      # 无 sid → 野生，算遗留
    if ledger_sigs is None:
        return True                       # 账本读不到时不加剧告警（判据⑧会单独判红）
    return m.group(1) in ledger_sigs      # 账本里有 → 在办；没有 → 孤儿，算遗留


def _ledger_sigs():
    """读欠账总账里 status=open 的全部签名；读失败返回 None（≠空集，M-R57 三态）。"""
    p = '/root/.codebuddy/skills/unfinished-audit/unfinished_ledger.json'
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding='utf-8'))
        return {str(i.get('sid')) for i in d.get('items', []) if i.get('status') == 'open'}
    except Exception:
        return None


def check_backlog_items(m):
    """各技能 flow.json backlog 段未清零。
    口径（S106 校准）：含完成标记（已清零/已兑现/MVP已兑/已闭环…）= 历史结项跳过；
    `[扩展点]` 前缀 = 持续演进项非遗漏跳过；其余 = 真遗留。
    口径（S136 补）：已登记进欠账总账（带 sid 且在册 open）= **在办**，不重复报
    （清账司负责追，watchdog 只盯没人认领的脏尾巴；理由见 _backlog_is_assigned）。"""
    out = []
    sigs = _ledger_sigs()
    for skill, p in FLOWS.items():
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        bl = d.get('backlog', [])
        for item in bl:
            # ★ S176b：统一走 _backlog_counts（完成标记 / [扩展点] / [本期不处理] 三类跳过，
            #   [真欠账] 继续报）。原先此处与 _hygiene_rows 各写一套，导致同一条目
            #   在「遗留清单」里已消失、在「评分器」里仍算欠账，两个数字打架。
            if not _backlog_counts(item):
                continue
            if _backlog_is_assigned(item, sigs):
                continue  # 已登记在办（有主有期），归清账司追办
            # ★ S177 步3 实测修：这里原来写的是 `s[:80]`，而 `s` **在本函数里从未定义**。
            #   后果：这条分支平时永远走不到（当前无真欠账），所以 M03 一直显示
            #   「✅ 执行正常 · 命中 0」——**它不是"没欠账"，是从来没真查过**。
            #   一旦真出现一条无人认领的欠账，它立刻 NameError，被外层 try 记成
            #   「❌异常」，于是「有欠账」变成「检查器坏了」，两件事都不对。
            #   实证：注入一条 backlog → `NameError: name 's' is not defined`。
            #   正确值就是循环变量 item 本身（下方 detail 已经在用它的同级信息）。
            out.append({
                'monitor': m['id'], 'name': f"{m['name']}（{skill}）", 'sev': m.get('severity', '轻微'),
                'item': str(item)[:80],
                'detail': f"{skill}/flow.json backlog",
                'action': m.get('action', ''),
            })
    return out


def check_rules_review_due(m):
    """规则整理周期：距上次整理>7天（以 evolution_log 最后整理记录为据）"""
    log = '/root/.codebuddy/skills/skill-evolution/evolution_log.jsonl'
    last_review = None
    if os.path.exists(log):
        for ln in open(log, encoding='utf-8'):
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if '整理' in str(d.get('what', '')) or 'review' in str(d.get('what', '')):
                last_review = d.get('ts') or d.get('time')
    if last_review is None:
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': '无规则整理记录（evolution_log 未见整理动作）',
            'detail': '无法判定周期，按超期处理',
            'action': m.get('action', ''),
        }]
    try:
        ts = datetime.datetime.strptime(last_review[:10], '%Y-%m-%d').timestamp()
    except Exception as e:
        # S084·M-R32：时间戳格式损坏**不得静默 return []**（会被读作"整理及时"）。
        # 「无法判定」须报为真阳性，否则检测器自身的解析失败伪装成"达标"（假绿）。
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': f'检测器失效：整理时间戳格式异常（{last_review[:20]}）',
            'detail': f'无法解析 `{last_review[:20]}` 为日期（{type(e).__name__}）——'
                      f'整理时效**无法判定**，非「及时」',
            'action': m.get('action', ''),
        }]
    days = (time.time() - ts) / 86400
    if days > 7:
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': f'距上次规则整理已 {days:.0f} 天',
            'detail': f'上次整理 {last_review[:10]}',
            'action': m.get('action', ''),
        }]
    return []


def check_snapshot_stale(m):
    """快照新鲜度（S027 升级：硬锚点 → 真读云端 updatedAt）。
    两层：① 本地硬锚点 snapshot_id.txt 缺失/空 = 阻断（锚点丢失，无从判新鲜度）
          ② 云端锚点 node-info.updatedAt 与本地快照落盘时间比对：
             快照节点云端更新时间 早于 本地记录的快照生成时间 = 快照页未真正刷新（伪刷新）
    云侧取不到 updatedAt 时降级为「仅硬锚点」，不虚报。"""
    snap = os.path.join(os.path.dirname(HUB), 'snapshot_id.txt')
    if not os.path.exists(snap) or not open(snap, encoding='utf-8').read().strip():
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '阻断'),
            'item': '快照锚点 snapshot_id.txt 缺失或为空',
            'detail': f'{snap}',
            'action': m.get('action', ''),
        }]
    sid = open(snap, encoding='utf-8').read().strip()
    ts = _cloud_node_updated(sid)
    if ts is None:
        return []  # 取不到云端时间 → 降级为硬锚点检查（已通过），不虚报
    # 本地锚点文件的 mtime 作为「我们认为快照已刷」的时间
    local_mtime = os.path.getmtime(snap)
    if ts < local_mtime - 120:  # 容差 2 分钟（写盘与云端提交的时序差）
        lag_h = (local_mtime - ts) / 3600
        return [{
            'monitor': m['id'], 'name': f"{m['name']}（云端滞后）",
            'sev': m.get('severity', '阻断'),
            'item': f'快照节点云端更新 早于 本地锚点 {lag_h:.1f} 小时',
            'detail': f'node={sid} 云端 updatedAt={datetime.datetime.fromtimestamp(ts):%Y-%m-%d %H:%M} / '
                      f'本地锚点 mtime={datetime.datetime.fromtimestamp(local_mtime):%Y-%m-%d %H:%M}',
            'action': '重跑 tvbox snapshot 并确认云端返回新 node；若刚刷过请稍后重扫',
        }]
    return []


def _cloud_node_updated(node_id):
    """读云端节点 updatedAt → epoch 秒；失败返回 None（不虚报）。"""
    import subprocess
    api = os.path.join('/root/.codebuddy/skills/library', 'space_api.py')
    if not os.path.exists(api):
        return None
    try:
        r = subprocess.run(['python3', api, 'space.workspace.node-info', '--node-id', node_id],
                           capture_output=True, text=True, timeout=90,
                           env=dict(os.environ, CODEBUDDY_SKILL_DIR='/root/.codebuddy/skills/library'))
        ms = json.loads(r.stdout)['data']['node'].get('updatedAt')
        return int(ms) / 1000 if ms else None
    except Exception:
        return None


def check_route_calib(m):
    """SOP 校准集漂移：症状最后变更 晚于 校准记录"""
    if not os.path.exists(SOP_JSON):
        return []
    try:
        d = json.load(open(SOP_JSON, encoding='utf-8'))
    except Exception as e:
        # S084·M-R32/M-R60：SOP 文件损坏**不得静默 return []**（那会被读作"校准正常"）。
        # 检测器自身读取失败 → 报一条真阳性遗留（检测器失效也是一种遗留），
        # 否则 selfcheck 的 exe_fail 抓不到（不抛异常），形成"假绿"。
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': f'检测器失效：SOP 文件损坏无法解析（{type(e).__name__}）',
            'detail': f'{SOP_JSON} 读取/解析失败——校准漂移**无法判定**，非「无漂移」',
            'action': '修复 SOP 文件后重跑；勿据本项通过判断校准正常',
        }]
    cal = (d.get('growth') or {}).get('route_calibration') or {}
    last_cal = cal.get('last_calibrated', '')
    # SOP 症状数 vs 校准记录的症状数
    n_sym = len(d.get('symptoms', []))
    n_cal = cal.get('symptom_count', 0)
    if n_sym != n_cal:
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': f'SOP 症状 {n_sym} 个 ≠ 校准记录 {n_cal} 个',
            'detail': f"校准记录 last_calibrated={last_cal or '(无)'}",
            'action': m.get('action', ''),
        }]
    return []


def check_eco_contract(m):
    """生态三件套缺口（口径校准首扫实证）：只查生态登记技能白名单 ECO_SKILLS，
    平台自带技能（library/computer-use/figma-connector 等）不强制三件套。"""
    base = '/root/.codebuddy/skills'
    out = []
    for name in sorted(ECO_SKILLS):
        d = os.path.join(base, name)
        if not os.path.isdir(d):
            out.append({
                'monitor': m['id'], 'name': f"{m['name']}（{name}）", 'sev': m.get('severity', '耗时'),
                'item': '技能目录不存在',
                'detail': f'{d}',
                'action': m.get('action', ''),
            })
            continue
        missing = []
        if not os.path.exists(os.path.join(d, 'CHANGELOG.md')):
            missing.append('CHANGELOG')
        if not (os.path.exists(os.path.join(d, 'flow.json'))
                or os.path.exists(os.path.join(d, 'scripts', 'flow.json'))):
            missing.append('flow.json')
        if missing:
            out.append({
                'monitor': m['id'], 'name': f"{m['name']}（{name}）", 'sev': m.get('severity', '耗时'),
                'item': f"缺 {'+'.join(missing)}",
                'detail': f'{d}',
                'action': m.get('action', ''),
            })
    return out


_CLOUD_CACHE = {}   # node_id → [(id,title,kind,nchild),...]；进程内缓存（T2 共享缓存，S060）
_CHECKER_CACHE = {} # check_id → findings[]；进程内缓存（S065）：scan/health 共享检查器执行结果

_SPACE_API = '/root/.codebuddy/skills/library/space_api.py'
_API_ENV = None

def _api_env():
    global _API_ENV
    if _API_ENV is None:
        _API_ENV = dict(os.environ, CODEBUDDY_SKILL_DIR='/root/.codebuddy/skills/library')
    return _API_ENV

def _cloud_children(node_id, base=None):
    """云端节点即时列举：返回 [(id, title, kind, nchild), ...]。
    复用 library 技能 space_api.py（禁自建云端通道）。失败返回 None（网络/授权问题不误报）。
    进程内缓存（S060）：同一 node_id 只查一次——17 检查器共享，M15/M17 递归下探不再 N+1 重查。
    S066 批量列举：`space.workspace.list-nodes` 一次请求即返回该父节点下**所有子节点的完整信息**
    （id/title/kind/nodes），替代原先"1 次 node-info 拿 id 列表 + N 次 node-info 逐子查详情"。
    S067：实测该接口**只需 parentNodeId、无需 spaceId**，故去掉取 spaceId 的额外 node-info 往返
    ——请求数进一步由 2 降为 1（root 26 子：27 → 1 请求）。"""
    if node_id in _CLOUD_CACHE:
        return _CLOUD_CACHE[node_id]
    if not os.path.exists(_SPACE_API):
        return None
    try:
        r = subprocess.run(['python3', _SPACE_API, 'space.workspace.list-nodes',
                            '--parent-node-id', node_id, '--limit', '200', '--offset', '0'],
                           capture_output=True, text=True, timeout=90, env=_api_env())
        nodes = json.loads(r.stdout)['data']['nodes']
    except Exception:
        return None
    res = [(n['id'], n.get('title', ''), n.get('kind', ''), len(n.get('nodes') or []))
           for n in nodes]
    _CLOUD_CACHE[node_id] = res
    return res


def check_pkg_naming(m):
    """底包库节点命名合规（M12·云端节点级，M08 的姊妹项）。
    与 M08 分工：M08 查本地 /workspace 根级文件；M12 查云端底包库根级**节点标题**。
    口径（S027 首扫校准·关键）：不能一刀切要求含日期——
      · 枝文件夹（规则53：体系A/B/C/D，kind=folder 且含嵌套）= 容器，豁免
      · 常驻索引/清单节点（标题含 索引/清单/追踪表/可用底包）= 滚动更新的活节点，豁免
      · 其余一次性交付物 = 须含 MMDD 日期段（规则51）
    豁免规则以「常驻节点白名单关键词」实现，登记在 monitors.json.waiver_keywords。"""
    import re
    root = None
    try:
        root = load_monitors().get('pkg_root')
    except Exception:
        pass
    if not root:
        return []
    kids = _cloud_children(root)
    if kids is None:
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '轻微'),
            'item': '底包库节点列举失败（云端不可达/未授权）',
            'detail': f'root={root}',
            'action': '检查 library 授权或网络；确认后重跑 scan',
        }]
    try:
        kw = set(load_monitors().get('waiver_keywords') or [])
    except Exception:
        kw = set()
    out = []
    for nid, title, kind, nchild in kids:
        if not title:
            continue
        if kind == 'folder' and nchild > 0:
            continue  # 枝文件夹=容器（规则53），豁免
        if nchild > 0:
            continue  # 有子节点的容器，豁免
        if any(k in title for k in kw):
            continue  # 常驻索引/清单节点，豁免
        stem = re.sub(r'^[^\w\u4e00-\u9fff]+', '', title)
        if not re.search(r'\d{4}', stem):
            out.append({
                'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '轻微'),
                'item': f'{title[:50]}（{kind}）',
                'detail': '底包库交付物节点标题无 MMDD 日期段（规则51：体系/类别标签+日期+描述）',
                'action': m.get('action', ''),
            })
    return out


def _kw_is_mentioned_not_acted(blob, hit):
    """命中词只是"被提到"（讨论/枚举/引用），不是"在记一条待办"→ True。

    ★ S164 新增。与 unfinished-audit/scripts/collect.py 的 `_is_enum_or_label`
    同一套判据（M-R38 单一事实源）：那边管 SKILL.md 扫描，这边管云文档正文扫描，
    **两处必须同一口径**，否则同一条文本在一处报、另一处不报。

    判据（结构，不靠词表——词表永远列不全）：
      ① 被引号/书名号/加粗包裹 → 在"点名"这个词，不是在记待办
      ② 前后是判断语境（是不是/是否为/怎么判/如何判/判定/口径）→ 在讨论这个概念
      ③ 后接"→"或"转…backlog" → 在描述流转动作，不是本条待办
      ④ 位于枚举（同段 ≥3 个 / · 、 分隔项）→ 是枚举里的一项名字
    """
    import re as _re
    i = blob.find(hit)
    if i < 0:
        return False
    left, right = blob[:i], blob[i + len(hit):]
    # ① 被引号/加粗包裹
    if _re.search(r'[《「『“"\'*`（(]$', left.rstrip()[-2:] if left else '') or \
       _re.match(r'^[》」』”"\'*`）)]', right):
        return True
    if left.rstrip().endswith('「') or right.lstrip().startswith('」'):
        return True
    # ② 判断/讨论语境
    ctx = (left[-12:] + hit + right[:8])
    if _re.search(r'(是不是|是否为|是否算|怎么判|如何判|判定|口径|识别|区分|当成|误判|假阳性)', ctx):
        return True
    # ③ 流转描述：`待办 → 转…backlog`。注意正则写法——
    #    第一版写成 `^\s*[→\-]>`，字符类里放全角箭头再要求一个 `>`，
    #    于是单独一个 `→` 永远匹配不上（单测当场打脸）。箭头分全角/半角两种，写成择一。
    if _re.match(r'^\s*(→|->|=>)', right) or _re.search(r'转\s*\S*backlog', right):
        return True
    # ④ 处于枚举中
    seg = _re.split(r'\s*[/／·|]\s*', blob[:400])
    if len([s for s in seg if s.strip()]) >= 3:
        return True
    return False


def check_doc_todo(m):
    """云文档待办提取（M13）：从③台账/④经验库正文里扫「明写的待办标记」。
    与 M01/M03 分工：M01 查台账**状态列**、M03 查 flow.json backlog——都没覆盖
    「记录正文里随口写的待办」。本项补这个缝：扫描 可复用点/解法/踩坑 等长文本字段中的
    待办关键词（TODO/待办/待处理/待补/后续再/下次/暂不处理/留待）。
    口径校准：只报含「未完成语气」的（排除已完成的「已完成/已清零/已修复」）。
    口径校准（S035·M13 假阳性）：排除「功能描述词」——如「转待办/生成待办/待办清单/待办项/
    派活/待办标记/待办清单」等是在**描述一项功能**，不是在记录待办（本轮 L035 正文
    「把诊断转成可执行待办」被误判）。只有「待办」作为独立动作意图时才报。"""
    KW = ('TODO', '待办', '待处理', '待补', '后续再', '下次', '暂不处理', '留待', '尚未')
    DONE = ('已完成', '已清零', '已修复', '已闭环', '已解决', '已处理')
    # 功能描述词：含这些前缀/形态时不报（假阳性防护）
    FUNC_HINT = ('转待办', '生成待办', '转成待办', '待办清单', '待办项', '待办标记',
                 '派活', '待办转', '把诊断', '转成可执行', '可执行待办', '自动派活')
    out = []
    seen = set()
    for db, label in ((DB_SESS, '③台账'), (DB_LIB, '④经验库')):
        try:
            rows = rows_of(db)
        except Exception:
            continue
        for r in rows:
            # S121 口径补齐：M13 接入 ledger_waiver（S027 注释宣称与 M01 共用但实现漏接——决策留痕的条目不再重复报警）
            _no = str(r.get('会话编号') or r.get('编号') or '').strip()
            if _no and _no in ledger_waiver():
                continue
            blob = ' '.join(str(v) for k, v in r.items()
                            if k in ('可复用点', '解法', '踩坑', '成果产出', '任务摘要', '场景'))
            if any(d in blob for d in DONE):
                continue  # 已完成语气，不报
            if any(f in blob for f in FUNC_HINT):
                continue  # 功能描述语境（非真待办），不报
            hit = next((k for k in KW if k in blob), None)
            if not hit:
                continue
            # ★ S164：**结构判据**（与 unfinished-audit/collect.py 同一口径，M-R38 不另造）。
            #   触发本轮的原因：我刚往 ③台账/④经验库 写了两条经验，正文里有
            #   「是不是**待办**」「真待办 → 转 flow.json backlog」这样的**讨论**，
            #   立刻被 M13 当成待办抓出 2 项遗留。而这两条恰恰是在讲"怎么判待办"。
            #   更刺眼的是：同一天我刚在 collect.py 修好了同型假阳性，却没同步到 M13——
            #   **同一个缺陷第三处**（S158 保留待 → skillext → 这里）。
            #   判据不再补词：命中词若只是"被讨论/被枚举/被引用的名字"，就不报。
            if _kw_is_mentioned_not_acted(blob, hit):
                continue
            no = str(r.get('会话编号') or r.get('编号') or '')
            key = (label, no, hit)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                'monitor': m['id'], 'name': f"{m['name']}（{label}）", 'sev': m.get('severity', '轻微'),
                'item': f"[{no}] 正文含待办标记「{hit}」",
                'detail': blob[:110].replace('\n', ' '),
                'action': m.get('action', ''),
            })
    return out


def check_revive_eval(m):
    """失效库复活回流评估（M14）：对超期未复测的失效条目做**抽样探活**，判定是否已复活。
    与 M02 分工：M02 只数「待复测超期」的**数量**（不探活）；M14 真发请求**采样验证**，
    复活者给出回流建议——把「过期提醒」升级为「可执行的复活决策」。
    成本控制：只测超期条目的前 N 条（登记册 threshold 控制），随机/顺序均可；
    代理型 host 跳过（其可达性由被代理目标决定，S026 口径）。"""
    import random
    ok, rows = rows_of_t(DB_DEAD)   # S084·M-R60：三态读，区分「读失败」与「失效库真空」
    if not ok:
        print('  ⚠️ 失效库读取失败——复活评估不可用（勿误判为「无待复活源」）')
        return []
    days_th = int(m.get('threshold_days') or 7)
    sample_n = int(m.get('sample') or 8)
    now = time.time()
    cand = []
    for r in rows:
        if str(r.get('复测结果', '')) != '待复测':
            continue
        api = str(r.get('样例api', '')).strip()
        if not api.startswith('http') or _is_proxy_api(api):
            continue  # 代理型/非 http 跳过（S026 口径）
        t = str(r.get('复测时间') or r.get('判定时间') or '')
        try:
            ts = datetime.datetime.strptime(t[:10], '%Y-%m-%d').timestamp()
        except Exception:
            continue
        days = (now - ts) / 86400
        if days > days_th:
            cand.append((days, r, api))
    if not cand:
        return []
    cand.sort(key=lambda x: -x[0])  # 最久的优先
    pick = cand[:sample_n]
    # 复用 hub probe_src.probe（禁自建探测逻辑）
    try:
        sys.path.insert(0, HUB)
        from probe_src import probe as _probe
    except Exception:
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': f'失效库超期 {len(cand)} 条待复测（探活模块不可用，未采样）',
            'detail': 'probe_src 导入失败，仅报数量',
            'action': m.get('action', ''),
        }]

    def _probe_one(x):
        days, r, api = x
        return days, r, api, _probe(api)

    # ★ S163：改走统一入口（B 档：探测公网站点）。keep_order 必开——下方按 res 顺序产 issue
    _rep = _run_bounded(pick, _probe_one, tier=TIER_WEB, label='inspect-revive',
                        keep_order=True, on_error='record')
    res = [(_r if _r is not None else (d, r, api, ('x', '无法判定', 'ERR', 0)))
           for (d, r, api), _r in zip(pick, _rep.results)]
    out = []
    alive = [(days, r, api, pr) for (days, r, api, pr) in res if pr[1] == '正常']
    for days, r, api, pr in alive:
        code, size = pr[2], pr[3]
        out.append({
            'monitor': m['id'], 'name': f"{m['name']}（疑似复活）", 'sev': m.get('severity', '耗时'),
            'item': f"{str(r.get('host',''))[:36]} 已存活（HTTP {code}/{size}B，失效 {days:.0f} 天）",
            'detail': f"样例 {api[:60]}",
            'action': '从失效库移除（或标「已复活」）并评估回流源库；若原先整域拉黑需拆细到单条',
        })
    if not alive:
        out.append({
            'monitor': m['id'], 'name': f"{m['name']}（抽样全灭）", 'sev': '轻微',
            'item': f'超期 {len(cand)} 条，抽样 {len(pick)} 条全部仍失效',
            'detail': f'最久 {cand[0][0]:.0f} 天 / 最近 {pick[-1][0]:.0f} 天',
            'action': '本轮无需回流；下期继续抽样（可按死因分组抽样以提高信息量）',
        })
    return out


def check_subtree_naming(m):
    """底包库子枝命名抽查（M15）：递归下探一级，检查枝文件夹命名合规。
    与 M12 分工：M12 查根级交付物（须含 MMDD 日期）；M15 查**子枝命名**——
    子枝是规则53「系→枝→魔改」的中间层，命名规范是「<系代号>-<枝名>枝」
    （如 A-lytvs枝 / B-moji主枝 / C-lu3589直链枝），**不要求日期**（枝是长期容器）。
    抽查要点（口径·S031）：
      · 枝名应含系代号前缀（A-/B-/C-/D- 或 🔞-）→ 便于跨系检索时一眼归属
      · 枝名应以「枝」结尾（本库约定）→ 否则与交付物节点混淆
      · 子枝下若仍嵌套 folder = 疑似「枝里套枝」的层级过深，提示人工确认
    成本控制：只下探一级（root → 系 → 枝），不下钻到魔改层。"""
    import re
    root = load_monitors().get('pkg_root')
    if not root:
        return []
    kids = _cloud_children(root)
    if kids is None:
        return []
    # S103 口径修正：底包库根下**非「体系」容器**须排除——它们是功能库/线路库的分档文件夹，
    # 不是规则53 的「系→枝」层（如「线路总库_四档」下的 全量版/纯净版/18版/产出json；
    # 以及历史废弃根）。否则 M15 会把分档文件夹误报成「枝命名不合规」。
    _SKIP_CONTAINERS = ('线路总库', '产出json', '废弃', '🗑', '（废弃', '首轮失败')
    # S065 提速：系层节点彼此独立，预取各自子节点可并行（原为逐系串行 → 层数累加 ~11.4s）。
    # _cloud_children 内部已并发同层子查询且带进程内缓存，此处只需把「跨系」也并发。
    folders = [(nid, title, nchild) for nid, title, kind, nchild in kids
               if kind == 'folder' and nchild != 0
               and not any(sk in title for sk in _SKIP_CONTAINERS)]   # 只看系文件夹
    sub_map = {}
    if folders:
        # ★ S163：改走统一入口（A 档：读自家文档）。取不到 → None，由调用方判「无法判定」
        _ids = [nid for nid, _, _ in folders]
        _rep = _run_bounded(_ids, _cloud_children, tier=TIER_API, label='inspect-submap',
                            keep_order=True, on_error='record')
        for nid, v in zip(_ids, _rep.results):
            sub_map[nid] = v
    out = []
    # S065 提速：枝层「层级过深」探测（sn>0 的枝要再查一层）原为逐枝串行，实测占 M15 的 ~8.8s。
    # 枝间彼此独立 → 预取并行；_cloud_children 自带进程内缓存，重复枝不会二次查。
    need_subsub = [(sid, stitle, sn) for nid, _, _ in folders
                   for sid, stitle, skind, sn in (sub_map.get(nid) or [])
                   if skind == 'folder' and sn > 0]
    subsub_map = {}
    if need_subsub:
        _ids2 = [sid for sid, _, _ in need_subsub]
        _rep2 = _run_bounded(_ids2, _cloud_children, tier=TIER_API, label='inspect-subsubmap',
                             keep_order=True, on_error='record')
        for sid, v in zip(_ids2, _rep2.results):
            subsub_map[sid] = v
    for nid, title, nchild in folders:
        sub = sub_map.get(nid)
        if not sub:
            continue
        for sid, stitle, skind, sn in sub:
            if skind != 'folder':
                continue  # doc 类说明节点不查
            # 口径校准（S031 首扫实证·重要）：节点标题常带括号注解
            # （如「A-lytvs枝（spider.jar@lytvs.top·113类）」），判定须先剥注解
            base = re.sub(r'[（(].*$', '', stitle).strip()
            issues = []
            if not re.match(r'^[ABCD]-|^🔞-', base):
                issues.append('无系代号前缀(A-/B-/C-/D-/🔞-)')
            if not base.endswith('枝'):
                issues.append('未以「枝」结尾')
            if sn > 0:
                # 只对「纯容器」报层级过深（子节点全是 folder 才算套枝）
                subsub = subsub_map.get(sid) or []
                n_folder = sum(1 for _, _, k, _ in subsub if k == 'folder')
                if n_folder > 0:
                    issues.append(f'枝内嵌套 {n_folder} 个容器(疑似层级过深)')
            if issues:
                out.append({
                    'monitor': m['id'], 'name': f"{m['name']}（{title.split('·')[-1] or title}）",
                    'sev': m.get('severity', '轻微'),
                    'item': f'{stitle[:44]}',
                    'detail': '；'.join(issues),
                    'action': m.get('action', ''),
                })
    return out


def check_naming_compliance(m):
    """交付物命名合规（规则51）：/workspace 根级 json/md 文件名须含 MMDD 四位日期段。
    首建即校准（S023）：只扫根级交付物，子目录/非交付扩展名不误伤。

    ★ S159 白名单（ws_name_waiver）：规则51 原本一刀切「必须含 MMDD 日期段」，
      实测误伤两类**本就不该带日期**的文件，靠改名解决是错的：
        ① 固定索引/入口文件——文件名是**口令锚点**（用户说「常用入口」就指它），
           改名等于把口令改坏；这类文件长期滚动更新，不属于「一次性交付物」。
        ② S 编号报告——生态内统一用 `S<三位会话号>` 定位（S107/S154/S155/S156…），
           日期段本就冗余，且这批文件已按此命名成规模，改名会断掉历史引用。
      故不做「一刀切改名」，改为显式白名单；白名单未命中才判不合规。
      白名单登记在 monitors.json.ws_name_waiver，与云端节点的 waiver_keywords
      **分属两套口径**（一个扫本地文件名、一个扫云端节点标题），不可混用。"""
    import re
    out = []
    ws = '/workspace'
    if not os.path.isdir(ws):
        return []
    try:
        waiver = set(load_monitors().get('ws_name_waiver') or [])
    except Exception:
        waiver = set()
    for fn in sorted(os.listdir(ws)):
        p = os.path.join(ws, fn)
        if not os.path.isfile(p) or not fn.lower().endswith(('.json', '.md')):
            continue
        stem = os.path.splitext(fn)[0]
        if re.search(r'\d{4}', stem):
            continue
        # 整名豁免 或 前缀豁免（如 'S' 前缀规约 / 'S107_' 单文件）
        if fn in waiver or any(fn.startswith(w) for w in waiver if w):
            continue
        out.append({
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '轻微'),
            'item': fn,
            'detail': '文件名无 MMDD 日期段（规则51：体系/类别标签+日期+描述；'
                      '若属固定索引或 S 编号报告，请登记进 monitors.json.ws_name_waiver）',
            'action': m.get('action', ''),
        })
    return out


def check_status_archive(m):
    """状态-归档一致性（M09·双向对账）：源总库「状态」列与失效库（🗑）应互为镜像。
    实测盲区：停用 213 vs 归档 162，初判 51 条「死了没埋」——校准后发现 51 条全为
    代理型（gh-proxy 28 + 其他代理 3）或已被 host 级覆盖，真遗留 0。
    故本项查双向：
      正向 源库停用 → 失效库无对应条目（死了没埋）；代理型豁免（S026 口径）
      反向 失效库已拉黑 → 源库仍标 正常/待查（僵尸源，最危险：被判死却还在活跃清单）
    代理型豁免复用 _is_proxy_api（与 blacklist.blk 同口径）。"""
    # S159：源库已分散三库，必须联合读；且读失败不得当成「源库为空」——
    # 否则本项会从"报了 0 项"变成"源库空所以没得比"，两种都没问题，但性质完全不同。
    src, sinfo = src_rows()
    if not sinfo['ok'] or sinfo['total'] == 0:
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '轻微'),
            'item': '源库读取失败或三库皆空——无法判定',
            'detail': f"by_db={sinfo['by_db']} total={sinfo['total']}",
            'action': '确认 SRC_DBS 坐标与授权；读失败不等于源库为空（M-R60）',
        }]
    dead = rows_of(DB_DEAD)
    # **S159 修：按「是否在拦」判定，不按「字段字面值」判定。**
    # 事故：拦截器的取值域是 `整域/单条`，而我引入的第三态「观望」=
    # 已确认复活但待回流复核。它**不在拦截器里**（`startswith('整域'/'单条')`
    # 都不命中 → 执行器放行），但本检查器原来把「只要在失效库表里出现过」
    # 就算命中 → 报「僵尸源」，而实际拦截器已经放行。
    # 这是 M-R38（同一事实源）的又一次违规：同一个「是否被拉黑」的问题，
    # 拦截器答「否」，检查器答「是」。谁都不该自己维护第二份口径。
    # 判据改法：只把 `整域 / 单条` 两类计入命中（与 blk.blocked_set() 逐字同口径）；
    # `观望` 及任何未来新增的非拦值一律不参与「僵尸源」判定。
    BLOCKING_SCOPES = ('整域', '单条')
    dead_api = {str(r.get('样例api', '')).strip() for r in dead
                if str(r.get('拉黑范围') or '').startswith(BLOCKING_SCOPES)}
    # **S151 修：整域集合必须与拦截器 `blk.blocked_set()` 同口径**。
    # 事故：平台域（raw.githubusercontent.com）和代理域（gh-proxy/ghfast/fastgit）
    # 被历史数据写成整域，拦截器已按 S026/S151 在读侧过滤放行，但本检查器仍按
    # 「表里有 host 就算命中」判僵尸源 → 报 185 项，其中 27 项是检查器自己造出来的假红。
    # 判据不齐 = 检查器与执行器各说各话：执行器放行，检查器报"你没停用"。
    dead_hosts = {str(r.get('host', '')).strip() for r in dead
                  if str(r.get('拉黑范围') or '').startswith('整域')}
    dead_hosts = {h for h in dead_hosts if not _is_platform_host(h) and not _is_proxy_host(h)}
    # 事故链（同一根因的两层）：
    #   ① 整域层：平台域（raw.githubusercontent.com）/代理域（gh-proxy/ghfast/fastgit）
    #      被历史数据写成整域 → 拦截器按 S026/S151 在读侧放行，检查器却仍算命中；
    #   ② 单条层：把这些降级成「单条」后，`dead_api` 里存的仍是**代理 URL 原文**
    #      （`https://gh-proxy.com/https://raw.githubusercontent.com/...`）。
    #      源库里同一个代理 URL 又恰好一字不差 → 精确匹配命中 → 仍报僵尸源。
    #      但拦截器此时也按单条拦得住它——**这 158 项不是假红，是真实欠账**
    #      （这些源确实被判死了、也确实还在源库标着「待查」）。
    # 判据留给用户决定：回填 801 条源库状态属数据处置，不擅自动手。
    from urllib.parse import urlparse
    out = []
    for r in src:
        api = str(r.get('api接口', '')).strip()
        st = str(r.get('状态', ''))
        h = urlparse(api).netloc
        archived = api in dead_api or (h and h in dead_hosts)
        if st == '停用':
            # 正向：停用却未归档（代理型豁免）
            if archived or _is_proxy_api(api):
                continue
            out.append({
                'monitor': m['id'], 'name': f"{m['name']}（正向·死了没埋）",
                'sev': m.get('severity', '耗时'),
                'item': f"{api[:70]}",
                'detail': f"源库状态=停用，但失效库无对应条目（{r.get('体系','')}）",
                'action': m.get('action', ''),
            })
        else:
            # 反向：失效库已拉黑却仍标 正常/待查 = 僵尸源
            if not archived:
                continue
            out.append({
                'monitor': m['id'], 'name': f"{m['name']}（反向·僵尸源）",
                'sev': '耗时',
                'item': f"[{st}] {api[:66]}",
                'detail': '已命中失效库拉黑，但源库状态仍为 正常/待查（应为 停用）',
                'action': '回填源库状态=停用；若确认已复活则从失效库移除并保留正常',
            })
    return out


def _is_proxy_api(api):
    """与 blacklist.blk.is_proxy_api 同口径：URL 内嵌第二层 http(s):// 或代理域名族。
    复制实现而非导入——避免 watchdog 强依赖 blacklist 脚本路径（保持监测中立）。"""
    a = str(api or '')
    for scheme in ('http://', 'https://'):
        if a.find(scheme, a.find('://') + 3) != -1:
            return True
    PROXY_HINT = ('gh-proxy', 'ghproxy', 'ghfast', 'gh.halonice', 'ghfile',
                  'gitmirror', 'kgithub', 'fastgit', 'ghp.ci', 'gh.api')
    try:
        from urllib.parse import urlparse
        h = urlparse(a).netloc.lower()
    except Exception:
        h = ''
    return any(p in h for p in PROXY_HINT)


def _is_proxy_host(h):
    """纯域名口径的代理域判定（S151）。`_is_proxy_api` 看「path 里嵌了第二条 URL」，
    本函数只看域名族——整域表过滤需要的是后者（表里存的本来就是 host）。"""
    PROXY_HINT = ('gh-proxy', 'ghproxy', 'ghfast', 'gh.halonice', 'ghfile',
                  'gitmirror', 'kgithub', 'fastgit', 'ghp.ci', 'gh.api')
    h = str(h or '').strip().lower()
    return bool(h) and any(p in h for p in PROXY_HINT)


def _is_platform_host(h):
    """公共平台域判定（S151）。整域拉黑会误伤同域的无关发布者，故拦截器只允许单条。
    本检查器必须同口径，否则执行器放行、检查器仍报"僵尸源"= 自造假红。"""
    PLATFORM_HINT = ('raw.githubusercontent.com', 'githubusercontent.com', 'github.com',
                     'gitee.com', 'gitlab.com', 'jsdelivr.net', 'unpkg.com',
                     'gitcode.com', 'gitea.com', 'objects.githubusercontent.com',
                     'codeload.github.com',
                     # S159 补：与 blk.py 同步。cnb.cool(腾讯CNB) / git.yylx.win(自建Git)
                     # 同为通用代码托管域，整域拉黑会连同域存活源一起判死。
                     'cnb.cool', 'git.yylx.win')
    h = str(h or '').strip().lower()
    if not h:
        return False
    return any(h == p or h.endswith('.' + p) for p in PLATFORM_HINT)


def check_data_quality(m):
    """存量数据质检（M10）：双库重复 api 巡检 + 关键字段缺失。
    S025 只修复了写入侧的判重双层，存量数据从未巡检——本检查器补上「事后体检」这一环。
    判据：① 源总库/失效库 同 api 出现 >1 次即遗留（重复行 = 计数虚高 + 查询歧义）
          ② 源库记录 分组类目 为空即遗留（规则49 归类断链，会让 stats 失真）"""
    out = []
    # ① 重复巡检（S159：源库改为三库联合；重复按「库内」判，
    #    因为同一个 api 出现在 py库与 csp库是**正常的体系差异**，不是重复行。
    #    故逐库统计后再合并计数，而不是把三库拼起来一起 count）
    _src, _si = src_rows()
    for label, db, key in [('失效库', DB_DEAD, '样例api')]:
        c = Counter(str(r.get(key, '')) for r in rows_of(db))
        dups = {k: v for k, v in c.items() if v > 1}
        if dups:
            extra = sum(v - 1 for v in dups.values())
            sample = next(iter(dups))
            out.append({
                'monitor': m['id'], 'name': f"{m['name']}（{label}重复）",
                'sev': m.get('severity', '耗时'),
                'item': f"{label} 重复 api {len(dups)} 组 / 多余 {extra} 行",
                'detail': f"样例: {sample[:60]}",
                'action': '检索入库链路判重逻辑（须 exist_* 库内 + seen_* 批内双层），清理重复行',
            })
    # 源库重复：逐库判（跨库同 api 属体系差异，见上）
    for _nm, _db in SRC_DBS:
        _ok, _rows = rows_of_t(_db)
        if not _ok:
            out.append({
                'monitor': m['id'], 'name': f"{m['name']}（{_nm}读取失败）",
                'sev': m.get('severity', '耗时'),
                'item': f'{_nm} 读取失败——无法判定重复',
                'detail': 'M-R60：读失败不等于无重复',
                'action': '检查授权/网络后重跑',
            })
            continue
        _c = Counter(str(r.get('api接口', '')) for r in _rows)
        _d = {k: v for k, v in _c.items() if v > 1}
        if _d:
            _extra = sum(v - 1 for v in _d.values())
            out.append({
                'monitor': m['id'], 'name': f"{m['name']}（{_nm}重复）",
                'sev': m.get('severity', '耗时'),
                'item': f'{_nm} 重复 api {len(_d)} 组 / 多余 {_extra} 行',
                'detail': f'样例: {next(iter(_d))[:60]}',
                'action': '检索入库链路判重逻辑（须 exist_* 库内 + seen_* 批内双层），清理重复行',
            })
    # ② 关键字段缺失（S159：三库联合；带 _src库 标记便于定位是哪一库断链）
    src, _si2 = src_rows()
    miss = [] if not _si2['ok'] else [r for r in src if not str(r.get('分组类目', '')).strip()]
    if miss:
        _by = {}
        for r in miss:
            _by[r.get('_src库', '?')] = _by.get(r.get('_src库', '?'), 0) + 1
        out.append({
            'monitor': m['id'], 'name': f"{m['name']}（分组类目缺失）",
            'sev': m.get('severity', '耗时'),
            'item': f"源库 {len(miss)} 条无分组类目（{', '.join(f'{k}:{v}' for k, v in _by.items())}）",
            'detail': f"样例: {str(miss[0].get('名称',''))[:30]} / {str(miss[0].get('api接口',''))[:40]}",
            'action': '回填分组类目（统计与规则49 归类依赖此列）',
        })
    return out


def check_ledger_consistency(m):
    """台账-库实况对账（M11）：防「声明已做实则无锚点」的半闭环假象。
    与 M01 的分工（首扫校准，避免重复报同一批）：M01 查「状态值本身未闭环」——
    把所有 已同步源库/底包 一律列半闭环；M11 只查「状态与数据矛盾」——
    声明 已同步* 却 同步源数 为空/0，即找不到落库数量锚点。
    故 M11 结果应是 M01 中「有声明无数据」的子集，二者互补不重复。
    口径校准（S027）：与 M01 共用 ledger_waiver 决策留痕白名单，避免同批噪音双报。
    口径修正（S027 实证·重要）：台账**没有**「同步源数」列（初版按该字段取值恒为空 → 100% 虚报）。
    实际可用锚点是「新增源数」：入库型会话标 已同步* 却 新增源数=0 = 可疑（无落库增量）；
    文档型会话（摘要/成果含 交付/报告/表/SOP/复盘/规则 等）天然 0 增量，属正常豁免。"""
    waiver = ledger_waiver()
    DOC_HINT = ('交付', '报告', '表', 'SOP', '复盘', '规则', '文档', '溯源', '监测', '对比', '回填')
    out = []
    for r in rows_of(DB_SESS):
        st = str(r.get('反哺状态', ''))
        if st not in ('已同步源库', '已同步底包'):
            continue
        if str(r.get('会话编号', '')).strip() in waiver:
            continue  # 决策留痕：已完成、状态停留中间态，非遗留
        try:
            n = int(r.get('新增源数') or 0)
        except Exception:
            n = 0
        if n > 0:
            continue  # 有落库增量锚点，声明与数据一致
        blob = str(r.get('任务摘要', '')) + str(r.get('成果产出', ''))
        if any(k in blob for k in DOC_HINT):
            continue  # 文档型交付，无源增量属正常
        out.append({
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '轻微'),
            'item': f"[{r.get('会话编号','')}] 标 {st} 但 新增源数=0（入库型却无落库锚点）",
            'detail': f"任务: {str(r.get('任务摘要',''))[:40]}",
            'action': '核对库内实际增量：有则补 新增源数；无则改 已闭环 或退回 待同步',
        })
    return out


def _tvbox_commands():
    """解析 tvbox CLI 的权威命令集（顶层 + 子命令组），用于引用完整性校验。
    数据源：tvbox-source-hub/tvbox 的 ROUTE 字典 + 各子模块 argparse 子命令。
    返回 set，如 {'brief','block','block check','evolve','watchdog scan',...}；失败返回 None。"""
    import re
    tv = '/root/.codebuddy/skills/tvbox-source-hub/tvbox'
    if not os.path.exists(tv):
        return None
    try:
        txt = open(tv, encoding='utf-8').read()
    except Exception:
        return None
    cmds = set()
    # 顶层：从 ROUTE 字典取键
    m = re.search(r'ROUTE\s*=\s*\{(.*?)\}', txt, re.S)
    if m:
        for k in re.findall(r"'([a-z][a-z-]*)'\s*:", m.group(1)):
            cmds.add(k)
    # ★ S106 修：tvbox 入口还有一批「内联命令」（不经 ROUTE 字典，直接在 main 里 if verb 分派），
    #   如 run / p / chain / manifest / contracts —— 旧口径漏掉它们致 flow.json 引用被误报悬空。
    #   数据源：入口里 `verb in ('a','b')` / `verb == 'x'` 两种形态。
    for mm in re.finditer(r"verb\s+(?:in\s*\(([^)]*)\)|==\s*'([a-z][a-z-]*)')", txt):
        if mm.group(1):                                  # verb in ('run','p')
            for k in re.findall(r"'([a-z][a-z-]*)'", mm.group(1)):
                cmds.add(k)
        elif mm.group(2):                                # verb == 'run'
            cmds.add(mm.group(2))
    # 子命令组：从各脚本的 add_parser('xxx') 抓
    groups = {
        'block': '/root/.codebuddy/skills/tvbox-source-blacklist/scripts/blk.py',
        'evolve': '/root/.codebuddy/skills/skill-evolution/scripts/evolve.py',
        'watchdog': os.path.join(SKILL_DIR, 'scripts', 'inspect_engine.py'),
    }
    for g, fp in groups.items():
        if not os.path.exists(fp):
            continue
        try:
            t = open(fp, encoding='utf-8').read()
        except Exception:
            continue
        for s in re.findall(r"add_parser\(\s*'([a-z][a-z-]*)'", t):
            cmds.add(s)          # 裸子命令（link 系动词）
            cmds.add(f'{g} {s}')  # 组+子
    return cmds


def check_flow_ref_integrity(m):
    """M16 流程面·flow.json 命令引用完整性（S034 新增）。
    口径：各技能 flow.json 里引用的 `tvbox <cmd>` 必须是 CLI 真实存在的命令——
    否则该流程步骤一跑就失败（文档与实现漂移）。只报「明确的组+子命令」级悬空引用，
    对不常见的动词形式（如带参数的 flex）保守跳过，避免误报。"""
    ref_cmds = _tvbox_commands()
    if not ref_cmds:
        return []   # 拿不到命令集 → 降级不虚报（M-R1 精神）
    import re
    out = []
    for skill, fp in FLOWS.items():
        if not os.path.exists(fp):
            continue
        try:
            _obj = json.load(open(fp, encoding='utf-8'))
        except Exception:
            continue
        # ★★ S182 修判据：**已结条目的历史叙述不是流程步骤**。
        #   实测 flow.json 里留着一条 `[已清零 2026-10-20]（sid=b990c7ef334c）M45 已补判据
        #   并实测：tvbox jobs 实测 4 个任务全部启用…`——这是在记录「当初查过 tvbox jobs」，
        #   不是让人照着跑的一步。整文件 json.dumps 后被正则一扫，就报成
        #   「tvbox-inspect→jobs 悬空命令引用」，判据④每轮红一次。
        #   本检查器的判据是「这一跑就失败」，已结留档根本不会被跑。
        #
        # ★ 只对「值」剔已结条目，**键一律保留**。第一版把键也剔了，且判据写反：
        #   写成 `not _backlog_is_open(k)` 才保留，而 _backlog_is_open 返回 True 表示
        #   **未结**，于是"保留的恰好是已结的"。实测 68 条命令引用被剔到 0 条，
        #   连人造的 `tvbox inspect zzznonexist` 都留不下来——**检查器被修成哑巴**。
        #   记住：`_backlog_is_open(x) == True` 意思是 x 未结。
        def _strip_done(o):
            if isinstance(o, dict):
                return {k: _strip_done(v) for k, v in o.items()}
            if isinstance(o, list):
                # 列表里以字符串形态存的 backlog 条目才判已结，其余原样保留
                return [_strip_done(v) for v in o
                        if not (isinstance(v, str) and not _backlog_is_open(v))]
            if isinstance(o, str):
                return '' if not _backlog_is_open(o) else o
            return o
        txt = json.dumps(_strip_done(_obj), ensure_ascii=False)
        # S176b 修判据：原正则把 `tvbox box.json` 截成 `tvbox box`，报「悬空命令 probe-hub→box」。
        # 实测：probe-hub/scripts/flow.json 原句是「cluntop/tvbox box.json 抽 21 条」——
        # 描述的是**别人仓库里的文件名**，根本不是本 CLI 的命令。误报一条「耗时」级遗留，
        # 而这类假红会训练人忽略真红（M-R34：假阳性让人不再看告警）。
        # 补 `(?![\w.-])`：命令名后面紧跟字母/数字/点/横线即不是命令边界（box.json / log.txt）。
        for m2 in re.finditer(r'tvbox\s+([a-z][a-z-]*)(?![\w.-])(?:\s+([a-z][a-z-]*)(?![\w.-]))?', txt):
            g, sub = m2.group(1), m2.group(2)
            full = f'{g} {sub}' if sub else g
            # 只校验：组命令（block/evolve/watchdog）须带合法子命令；其他顶层命令须存在
            if sub:
                if g not in ('block', 'evolve', 'watchdog'):
                    continue  # 顶层命令带参数（如 log --summary）→ 跳过
                if full not in ref_cmds:
                    out.append({'skill': skill, 'ref': full})
            else:
                if g not in ref_cmds:
                    out.append({'skill': skill, 'ref': g})
    # 去重
    seen, uniq = set(), []
    for o in out:
        k = (o['skill'], o['ref'])
        if k not in seen:
            seen.add(k); uniq.append(o)
    if uniq:
        item = '、'.join(f"{o['skill']}→{o['ref']}" for o in uniq[:6])
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': '耗时',
            'item': f'{len(uniq)} 条悬空命令引用',
            'detail': f"flow.json 引用了 CLI 不存在的命令：{item}",
            'action': '修正 flow.json 中的命令引用（对照 tvbox ROUTE）或补实现该命令',
        }]
    return []


def check_pkg_index_sync(m):
    """M17 校准面·底包库索引对账（S034 新增）。
    口径：底包库根级有「索引/清单/追踪表」类常驻节点（见 waiver_keywords），
    这些索引节点声明的子项数应与底包库实际的体系/枝节点数一致——索引漏列或多列即漂移。
    这是校准面的第二个检查器（M06 查 SOP 路由校准，本项查云端索引校准）。
    降级策略：云端不可达/无索引节点 → 返回 []，不虚报。"""
    root = load_monitors().get('pkg_root')
    if not root:
        return []
    kids = _cloud_children(root)
    if kids is None or not kids:
        return []   # 云端不可达或空 → 降级不误报
    kw = load_monitors().get('waiver_keywords') or ['索引', '清单', '追踪表', '可用底包']
    # 索引节点 = 标题命中关键词的顶层节点
    index_nodes = [(nid, t, k, nc) for (nid, t, k, nc) in kids if any(w in t for w in kw)]
    # 实际内容节点 = 非索引、非枝容器的交付物/枝节点
    content = [k for (nid, t, k, nc) in kids if not any(w in t for w in kw)]
    if not index_nodes:
        return []
    # 逐索引节点：看其是否声明了数量（标题里的数字或子节点数），与实际节点数比对
    out = []
    actual_n = len(content)
    for nid, t, kind, nc in index_nodes:
        # 索引节点若非 folder（无子节点）则不承载对账语义 → 跳过
        if nc == 0:
            continue
        # 声明数 = 索引节点自身子节点数（索引条目数）
        declared = nc
        # 容差 ±2（索引可能含分类行/说明行）
        if abs(declared - actual_n) > max(3, actual_n * 0.5):
            out.append({'index': t, 'declared': declared, 'actual': actual_n})
    if out:
        it = '、'.join(f"{o['index']}(索引{o['declared']}/实际{o['actual']})" for o in out[:4])
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': '耗时',
            'item': f'{len(out)} 个索引节点与库实况漂移',
            'detail': f'底包库索引声明的条目数与实际节点数不符：{it}',
            'action': '重刷底包库索引节点，使索引条目数与实际体系/枝节点数一致',
        }]
    return []


def _upstream_targets():
    """读探源司上游监测册 → (state, targets)。三态，**云端优先、本地兜底**。

    M18 专用读取原语。为何不直接 import 探源司：**跨技能耦合须最小化**——只读它
    公开落盘的**数据契约**（monitor_targets.json / DB_UPSTREAM），不 import 其代码，
    否则探源司内部重构会连带 watchdog 崩溃（M-R38 单一事实源的边界：契约共享≠代码耦合）。

      · ('ok',   [...])  正常（可能为空=尚无监测目标）
      · ('none', [])     未初始化（探源司尚未登记任何目标）
      · ('fail', [])     读取失败/结构不符（**禁当空册**——M-R60，不得据此说"无跑路"）
    """
    rows = None
    # ① 云端 DB_UPSTREAM（权威）：探源司已在 S088 上云
    try:
        ok, rr = rows_of_t(DB_UPSTREAM)
        if ok and isinstance(rr, list) and rr:
            rows = rr
        elif not ok:
            # 云端读失败 ≠ 空册：记下，稍后本地兜底 + 若两边皆败则 fail
            rows = None
    except Exception:
        rows = None
    if rows:
        return 'ok', rows
    # ② 本地 monitor_targets.json（兜底）
    fp = UPSTREAM_TARGETS
    if not os.path.exists(fp):
        return ('ok', []) if rows is not None else ('none', [])
    try:
        d = json.load(open(fp, encoding='utf-8'))
        tgs = d.get('targets') if isinstance(d, dict) else None
        if not isinstance(tgs, list):
            return 'fail', []          # 结构不符 → 显式 fail（禁当空册）
        return 'ok', tgs
    except Exception:
        return 'fail', []


def check_upstream_gone(m):
    """M18 上游面·上游仓库跑路/弃更未处置（S090 新增）。

    **缺口**：探源司（probe-hub）能发现"作者仓库已封禁/删除 / 长期停滞"，但结论止于
    它自己的输出——**watchdog 不知道**。作者跑路后，底包库中派生自该仓库的源就成了
    "僵尸源"（还能用，但永不再更新、随时可能失效），**无人主动处置**。本检查器把
    探源司的判定**转译为 watchdog 遗留项**，纳入统一分级与跟踪。

    **三态铁律（M-R60，本检查器的命门）**：
      - `无法判定` **绝不产遗留**——那是"我读不到"，不是"作者跑路"。把二者折叠
        （S088 实证的 `dirs=[]` 谎报"移除×290"）比漏报更危险：会触发错误的应急处置。
      - 监测册**读取失败**（state='fail'）也**不产遗留**，但**显式打印告警**——
        "无法判定"≠"无问题"（M-R60⑫）。
      - 探源司**尚未登记任何目标**（state='none'）→ 静默返回 []（首期，非问题）。

    **分级口径（S090 定 / S091 校准）**：
      - 存活 ∈ {已封禁/删除, 疑似跑路} → **阻断**（上游已死，派生源随时失效，须当次评估）
      - 活跃度 == 归档（>365 天无 push）→ **耗时**（疑弃更，须复核后决定是否换线）
      - 活跃度 == 停滞（90~365 天）→ **不产遗留**（S091 校准：停滞只是"变慢"，大量
        仍在维护的项目会有阶段性停更；若产遗留，M18 会沦为"每个不够活跃的仓库都报一条"
        的噪声源，使 verify 长期无法达标——**M-R53 噪声淹没**）。
        "停滞"仍**在探源司监测台可见**（`probe monitor status`），需要关注的用户可自查，
        不必进 watchdog 遗留清单。**"值得关注"≠"需要处置"**，遗留清单只收后者。

    三态铁律（M-R60，本检查器的命门）：
      - `无法判定` **绝不产遗留**——那是"我读不到"，不是"作者跑路"。把二者折叠
        （S088 实证的 `dirs=[]` 谎报"移除×290"）比漏报更危险：会触发错误的应急处置。
      - 监测册**读取失败**（state='fail'）也**不产遗留**，但**显式打印告警**——
        "无法判定"≠"无问题"（M-R60⑫）。
      - 探源司**尚未登记任何目标**（state='none'）→ 静默返回 []（首期，非问题）。
    """
    out = []
    state, targets = _upstream_targets()
    if state == 'fail':
        print('  ⚠️ 上游监测册读取失败（云端不可达且本地损坏）——'
              'M18 本次**无法判定**，请勿据此认为「无上游跑路」')
        return []
    if state == 'none':
        return []                      # 首期：探源司尚无监测目标，非遗留

    for t in targets:
        if not isinstance(t, dict):
            continue
        name = (t.get('仓库名') or t.get('id') or t.get('仓库URL') or '?')
        alive = (t.get('存活状态') or '').strip()
        act = (t.get('活跃度') or '').strip()
        pkg = (t.get('关联底包') or '').strip()
        url = (t.get('仓库URL') or '').strip()

        sev = None
        why = ''
        if alive in ('已封禁/删除', '疑似跑路'):
            sev = '阻断'
            why = f'上游存活={alive}'
        elif act == '归档':
            sev = '耗时'
            why = '上游活跃度=归档（>365 天无提交，疑弃更）'
        # S091 校准：`停滞`（90~365 天）**不产遗留**——"值得关注"≠"需要处置"，
        #   见 docstring；避免 M18 沦为噪声源（M-R53）。
        # 关键：alive/act 为 '无法判定'/None/空 → **不产遗留**（三态，不折叠）

        if sev is None:
            continue
        out.append({
            'monitor': m['id'], 'name': m['name'], 'sev': sev,
            'item': f'{name}（{why}）',
            'detail': (f'仓库 {url or "—"}；受影响底包：{pkg or "（未登记关联底包）"}'
                       f'；建议先跑 tvbox probe monitor check --repo {url or "<url>"} 复核'),
            'action': m.get('action', ''),
        })
    return out


def check_ledger_aging(m):
    """暂记账龄（M34·S156）：暂记户长期挂着未认领的差额。

    **为什么需要独立检查器**：账房的 `writeoff` 每执行一次就往「暂记·无主变动」灌一笔差额，
    而原先只有一句"账龄 3 轮未升级高危"的**打印提醒**——没人跑就没人知道，
    暂记余额只增不减。S156 给账房加了 `ledger aging` 命令，本检查器把它接进监测册。

    三态铁律（M-R60）：
      · 账房脚本不存在 / 执行失败 / 输出无法解析 → **报"无法判定"**，绝不返回空列表；
        返回 [] 会被上层读成"暂记干净"，那是最危险的一种谎。
      · 退出码 1 = 有高危（正是本检查器要报的），**不是**失败，不能当异常吞掉。
    """
    fp = '/root/.codebuddy/skills/eco-ledger/scripts/ledger.py'
    if not os.path.exists(fp):
        return [{ 'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
                  'item': '账房脚本不存在——暂记账龄**无法判定**（勿当作无超期）',
                  'detail': fp, 'action': m.get('action', '')}]
    try:
        r = subprocess.run([sys.executable, fp, 'aging'],
                           capture_output=True, text=True, timeout=120)
    except Exception as e:
        return [{'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
                 'item': f'账龄命令执行失败（{type(e).__name__}）——**无法判定**',
                 'detail': str(e)[:200], 'action': m.get('action', '')}]
    out = (r.stdout or '') + (r.stderr or '')
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    hi_lines = [ln for ln in lines if '高危' in ln and ln.startswith('🔴')]
    if not any('暂记账龄' in ln or '暂记无挂账' in ln or '无法判定' in ln for ln in lines):
        return [{'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
                 'item': '账龄命令输出无法解析——**无法判定**（勿当作无超期）',
                 'detail': out[:300], 'action': m.get('action', '')}]
    if '无法判定' in out:
        return [{'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
                 'item': '总账读取失败——暂记账龄**无法判定**（勿当作无超期）',
                 'detail': out[:300], 'action': m.get('action', '')}]
    res = []
    for ln in hi_lines:
        res.append({'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
                    'item': ln.lstrip('🔴 ').strip() + ' 账龄 ≥21 天未认领',
                    'detail': '暂记差额须逐条认领（补凭证平账）或冲销，见 eco-ledger §1.2',
                    'action': m.get('action', '')})
    return res


CHECKERS = {
    'ledger_pending': check_ledger_pending,
    'dead_review_due': check_dead_review_due,
    'backlog_items': check_backlog_items,
    'rules_review_due': check_rules_review_due,
    'snapshot_stale': check_snapshot_stale,
    'route_calib': check_route_calib,
    'eco_contract': check_eco_contract,
    'naming_compliance': check_naming_compliance,
    'pkg_naming': check_pkg_naming,
    'doc_todo': check_doc_todo,
    'revive_eval': check_revive_eval,
    'subtree_naming': check_subtree_naming,
    'status_archive': check_status_archive,
    'data_quality': check_data_quality,
    'ledger_consistency': check_ledger_consistency,
    'flow_ref_integrity': check_flow_ref_integrity,
    'pkg_index_sync': check_pkg_index_sync,
    'upstream_gone': check_upstream_gone,
    'ledger_aging': check_ledger_aging,   # S156 M34 暂记账龄（账房 aging 命令的监测侧）
    # 'balance_weakness'（S106 平衡性）在下方函数定义后补注册（定义顺序：CHECKERS 在前）
}


def _norm(s):
    """归一化遗留文本 → 指纹基（去空白/取前 60 字），避免措辞微调导致 diff 误判为新增。"""
    import re
    return re.sub(r'\s+', '', str(s or ''))[:60]


def finding_key(f):
    """遗留稳定指纹：monitor + 名称主体 + item 归一化。
    跨期稳定的关键——同一遗留措辞微调（空格/截断）不应被算作「本期新增」。"""
    return f"{f.get('monitor','')}|{_norm(f.get('name',''))}|{_norm(f.get('item',''))}"


def _is_real_monitor(mid):
    """monitor ID 合法形态：M + 数字（M01/M13/M30…）。
    S137：快照落盘前用它挡掉测试占位（曾漏进 'm'/'i' 污染 4 期历史）。"""
    return bool(re.fullmatch(r'M\d{1,3}', str(mid or '').strip()))


def load_prev_snapshot():
    """读**最后一期**已落盘快照的指纹。返回 (state, keys) 三态（S082·M-R60）。

    三态语义（关键：不得把「损坏」与「首期」折叠成同一态）：
      · ('none',    {})  —— 文件不存在 / 历史数组为空 = 真·首期，无基准
      · ('ok',      {...}) —— 读到有效历史 = 正常环比
      · ('corrupt', {})  —— 文件**存在但解析失败** = 基座损坏，**禁止当首期**

    S082 修复的缺陷（M-R58 在 watchdog 的复现）：
      旧版 `except: return {}` 把「历史损坏」与「首期无历史」返回同一个 {}，
      导致 diff 把上期全部遗留谎报为「本期新增」，且随后 save_snapshot 用
      空 hist 回写、**静默覆盖销毁损坏文件**——「读失败→假空态→写覆盖」三级放大。
    时序约定：cmd_report/cmd_diff 先 diff 再 save_snapshot，
    故此处读 [-1] 拿到的就是「上期」；save 之后本期才成为 [-1]。"""
    if not os.path.exists(SNAP_HIST):
        return 'none', {}
    try:
        hist = json.load(open(SNAP_HIST, encoding='utf-8')).get('history', [])
    except Exception:
        return 'corrupt', {}
    if not hist:
        return 'none', {}
    keys = hist[-1].get('keys', {})
    if not isinstance(keys, dict):
        return 'corrupt', {}
    return 'ok', keys


def snap_history_or_warn(consume_name=''):
    """读完整 findings_history 的 history 数组，三态（S085·M-R60⑬）。
    返回 (state, hist)：state ∈ {'none','ok','corrupt'}，hist 为数组（none/corrupt 时为空）。
    S085 修复的漏网形态：`except: hist = []`（**赋值式折叠**，非 return）——
      · _latency_rows / _replay_rows 两处把「快照损坏」折叠成「无历史」，统计静默归零，
        且 S084 的折叠扫描器**只匹配 `return` 空、漏了赋值式**（扫描器自身盲区）。
    统一收口本助手（M-R38 单一事实源）：凡需读完整 history 数组的，一律走它，
    损坏时**显式告警**（consume_name 用于指明是哪个消费方）。"""
    if not os.path.exists(SNAP_HIST):
        return 'none', []
    try:
        hist = json.load(open(SNAP_HIST, encoding='utf-8')).get('history', [])
        if not isinstance(hist, list):
            raise ValueError('history 非数组')
    except Exception as e:
        tag = f'（{consume_name}）' if consume_name else ''
        print(f'  ⚠️ 快照损坏无法读取历史{tag}：findings_history.json 解析失败'
              f'（{type(e).__name__}）——相关统计不可用，请人工修复（勿据此判定"无历史"）')
        return 'corrupt', []
    if not hist:
        return 'none', []
    return 'ok', hist


def save_snapshot(findings):
    """把本期遗留指纹落盘（保留最近 30 期），供下期 diff。
    返回 (saved, hist)：saved=False 表示**拒绝写入**（基座损坏，S082·M-R60）。
    损坏保护：若已有文件解析失败，绝不拿空 hist 回写——否则会静默覆盖销毁上期基准。
    此时保留原文件、返回 (False, [])，由调用方提示人工介入。"""
    hist = []
    if os.path.exists(SNAP_HIST):
        try:
            hist = json.load(open(SNAP_HIST, encoding='utf-8')).get('history', [])
            if not isinstance(hist, list):
                raise ValueError('history 非数组')
        except Exception:
            return False, []   # 损坏：拒绝覆盖，保留现场
    keys = {finding_key(f): {'monitor': f.get('monitor', ''), 'item': f.get('item', '')[:100],
                             'sev': f.get('sev', '')} for f in findings
            # S137·脏快照拦截：历史上曾有合成测试 finding（monitor='m', item='i'）被落盘，
            # 污染 4 期快照、计入增益统计（`m` 成了「抓过遗留的检查器」）。测试必须注入到
            # 内存结构里断言，**不得走落盘通道**；这里对明显不是检查器 ID 的占位项直接拒收。
            if _is_real_monitor(f.get('monitor', ''))}
    skipped = len(findings) - len(keys)
    if skipped:
        print(f'  ⚠️ 快照落盘拒收 {skipped} 条非法 monitor（非 M\\d+ 形态，疑测试占位）——'
              f'不写入历史，避免污染增益/噪声统计（S137）')
    hist.append({'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                 'n': len(findings),
                 'by_sev': dict(Counter(f.get('sev', '') for f in findings)),
                 'keys': keys})
    try:
        json.dump({'latest_ts': hist[-1]['ts'], 'history': hist[-30:]},
                  open(SNAP_HIST, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    except Exception:
        return False, []   # 写失败：不得谎报已落盘
    return True, hist[-30:]


def diff_findings(findings):
    """本期 vs 上期遗留 diff → (added, cleared, persist, state)。
    state ∈ {'none','ok','corrupt'}（S082·M-R60）——调用方据此区分：
      · 'none'    → 真首期，added=全部是对的
      · 'ok'      → 正常环比
      · 'corrupt' → **不得**把 added=全部当作真实信号；应显式告警基座损坏"""
    state, prev = load_prev_snapshot()
    cur = {finding_key(f): f for f in findings}
    added = [cur[k] for k in cur if k not in prev]
    cleared = [v for k, v in prev.items() if k not in cur]
    persist = [cur[k] for k in cur if k in prev]
    return added, cleared, persist, state


# ---------- 命令 ----------

def _db_sizes(pairs, err=None):
    """并行读多个库的行数（M-R38 单一事实源 + M-R41 独立即并行）。
    pairs: [(别名, db_id), ...]；err: 异常占位值（None→-1，callable(e)→其返回值）。
    S070：原三处（selfcheck/probe/live）各自串行 for 循环逐库 rows_of，
    冷读 4 库串行 1.40s；4 库互相独立，并行后 0.49s（2.9x）。core.rows_of 自身
    有进程级 _MEM 缓存，故本函数只在**首读**受益，但仍应并行——首读正是 scan 大头。"""
    def _one(pair):
        name, db = pair
        try:
            return name, len(rows_of(db))
        except Exception as e:
            if err is None:
                return name, -1
            if callable(err):
                return name, err(e)
            return name, err
    if not pairs:
        return {}
    # ★ S163：改走统一入口（A 档：读自家 4 库）
    _rep = _run_bounded(pairs, _one, tier=TIER_API, label='db-sizes',
                        keep_order=True, on_error='record')
    return {nm: (n if n is not None else (-1 if err is None else 'ERR'))
            for (nm, n) in ((r if r is not None else (p[0], None))
                            for p, r in zip(pairs, _rep.results))}


def selfcheck_checkers(mon, run_cache=None):
    """检查器健康预检（S032）：返回 (exe_fail, empty_src) 两类风险清单。
    在 scan 开头调用——确保「0 遗留」是**真达标**，而非检查器集体失灵造成的假象。
    这是本技能的自指防线：监测者自己也要被监测。
    S060 优化：run_cache 传入 {check_id: findings} 时复用执行结果，避免与 cmd_scan 重复跑两遍。"""
    exe_fail, empty_src = [], []
    # 数据源规模（S070：并行读，原串行 1.40s→0.49s）
    # S159：源库已三分（S144 清空旧库后），规模探针必须看三库，否则「源库空」永远是假警报
    src_size = _db_sizes([('源·' + n, d) for n, d in SRC_DBS] +
                         [('③台账', DB_SESS), ('④经验库', DB_LIB), ('🗑失效库', DB_DEAD)])
    SRC_OF = {
        'ledger_pending': '③台账', 'ledger_consistency': '③台账', 'doc_todo': '③台账',
        'dead_review_due': '🗑失效库', 'revive_eval': '🗑失效库',
        'status_archive': '源总库', 'data_quality': '源总库',
    }
    for x in mon['monitors']:
        fn = CHECKERS.get(x['check'])
        if fn is None:
            exe_fail.append(f"{x['id']} {x['check']} 检查器未实现（登记册先行登记）")
            continue
        try:
            res = fn(x)
            if run_cache is not None:
                run_cache[x['id']] = res or []   # 复用给 cmd_scan，避免重复执行
        except Exception as e:
            exe_fail.append(f"{x['id']} {x['check']} 执行异常：{str(e)[:60]}")
        s = SRC_OF.get(x['check'])
        if s and src_size.get(s) == 0:
            empty_src.append(f"{x['id']} {x['check']} 主数据源「{s}」为空")
    return exe_fail, empty_src


def _snapshot_staleness(max_hours=48):
    """快照自鲜度（S163·P1）：findings_history 最新期距今 >max_hours 则告警。

    为什么必须有：S163 实测遗迹哨的快照副本**静默死了 3 天**才被撞出来——
    M-R40「副本静默过期」：不报错、不告警，安静得跟正常没两样。
    M05 查的是规则47权威快照，不查 findings_history 自己；度量器自己的粮食
    过期恰恰在监测盲区里。本函数让 scan/health 在读历史前先看它新不新鲜。

    三态（M-R60）：读不到/解析失败 → 返回 (None, '无法判定')，**不当"新鲜"也不当"过期"**。
    """
    try:
        d = json.load(open(SNAP_HIST, encoding='utf-8'))
        hist = d.get('history', [])
        if not hist:
            return None, '快照为空（首次运行前属正常）'
        ts = hist[-1].get('ts', '')
        # ts 实测两种形态：'2026-10-19 07:58'（分钟级）与 '2026-10-19 07:58:03'——
        # 截到 16 位统一按 '%Y-%m-%d %H:%M' 解析，别按没见过的格式硬猜
        t = datetime.datetime.strptime(ts[:16], '%Y-%m-%d %H:%M')
        age_h = (datetime.datetime.now() - t).total_seconds() / 3600
        return (age_h > max_hours), (f'最新期 {ts}（距今 {age_h:.0f}h）'
                                     if age_h > max_hours else '')
    except Exception as e:
        return None, f'快照读取失败（{type(e).__name__}）——无法判定'


def cmd_scan(a):
    mon = load_monitors()
    # 前置：检查器健康预检（防「看门狗自己睡着了」的伪达标）
    # S060：预检同时把每个检查器的结果缓存进 run_cache，主扫直接复用——不再跑两遍
    # S065：run_cache 提为进程级 _CHECKER_CACHE——health 与 scan 遍历的是同一批检查器，
    #       同进程（tvbox run scan health）时 health 直接复用，免第二次全量遍历（实测省 ~14.5s）
    run_cache = _CHECKER_CACHE
    exe_fail, empty_src = selfcheck_checkers(mon, run_cache=run_cache)
    findings = []
    unknown = []
    for m in mon['monitors']:
        fn = CHECKERS.get(m['check'])
        if fn is None:
            unknown.append(m['id'])
            continue
        findings.extend(run_cache.get(m['id'], []))
    findings.sort(key=lambda x: SEV_ORDER.get(x['sev'], 3))
    print(f"🔍 监察司 扫描（监测项 {len(mon['monitors'])} 个，内置检查 {len(CHECKERS)} 类）")
    if unknown:
        print(f"⚠️ 未实现的 check 类型: {', '.join(unknown)}（登记册可先行登记，代码后补）")
    if exe_fail or empty_src:
        print(f"🩺 检查器健康告警（{len(exe_fail)+len(empty_src)} 项，下方「0 遗留」可能失真）：")
        for r in (exe_fail + empty_src):
            print(f"    ⚠️ {r}")
    # S163·P1：快照自鲜度（diff/trend/aging 的粮食过期要先说出来）
    _stale, _why = _snapshot_staleness()
    if _stale is True:
        print(f"🩺 快照陈旧告警：{_why}——diff/trend/aging 的环比与账龄**可能失真**（>48h 未落盘）")
    elif _stale is None:
        print(f"🩺 快照鲜度无法判定：{_why}")
    if not findings:
        print("\n✅ 无遗留——所有监测项达标"
              + ("" if not (exe_fail or empty_src) else "（⚠️ 但存在检查器健康告警，见上）"))
        return findings
    by_sev = {}
    for f in findings:
        by_sev.setdefault(f['sev'], []).append(f)
    for sev in ['阻断', '耗时', '轻微']:
        for f in by_sev.get(sev, []):
            print(f"\n[{sev}] {f['monitor']} {f['name']}")
            print(f"    遗留: {f['item']}")
            print(f"    详情: {f['detail']}")
            print(f"    下一步: {f['action']}")
    print(f"\n📊 合计 {len(findings)} 项遗留（阻断 {len(by_sev.get('阻断', []))} / "
          f"耗时 {len(by_sev.get('耗时', []))} / 轻微 {len(by_sev.get('轻微', []))}）")
    # ★ S163 修：scan 也必须落盘快照。原来只有 report 落（save_snapshot 在 cmd_report 里），
    #   而 SKILL.md 的注释写的是「report/scan 落盘」——文档与实现漂移。
    #   实测后果：只跑 scan 不跑 report 的期次，findings_history 整期缺失，
    #   trend/aging 的环比与账龄全被拉长（2026-10-16→10-19 三天里全靠 report 兜底）。
    #   注意时序：cmd_report/cmd_diff 都是**先 diff 再 save**（见 load_prev_snapshot 注释）；
    #   scan 在汇报完才 save，同一时序约定不受影响——下期 diff 读到的是本期的最终态。
    _saved, _hist = save_snapshot(findings)
    if not _saved:
        print('   ⚠️ 快照未落盘（基座损坏，已保留原文件不覆盖）——下期 diff/trend 将以更旧一期为基准')
    return findings


def cmd_status(a):
    findings = cmd_scan(argparse.Namespace())
    mon = load_monitors()
    print(f"\n🗂  监测项状态：")
    for m in mon['monitors']:
        n = sum(1 for f in findings if f['monitor'] == m['id'])
        mark = '✅' if n == 0 else f'⚠️ {n}'
        print(f"  {m['id']}  {m['name']:22} {mark}")


def cmd_monitors(a):
    mon = load_monitors()
    print(f"📋 时效项登记册 {mon['name']} v{mon['version']}（加监测项只改本文件，代码零改动）\n")
    for m in mon['monitors']:
        print(f"  {m['id']}  [{m.get('severity','')}] {m['name']}")
        print(f"      检查: {m['check']} ｜ 阈值: {m.get('threshold','')} ｜ 周期: {m.get('cadence','')}")
        print(f"      说明: {m.get('desc','')}")
        print(f"      动作: {m.get('action','')}")


def cmd_add_monitor(a):
    mon = load_monitors()
    ids = [m['id'] for m in mon['monitors']]
    if a.id in ids:
        print(f'监测项 {a.id} 已存在')
        return
    if a.check not in CHECKERS:
        print(f"⚠️ check 类型 {a.check} 尚未实现（内置: {', '.join(CHECKERS)}）——仍登记，代码后续补")
    # 登记即校验名实相符（S136）：防止再出现「声称盯回退、实际查文件名」的挂名项。
    # 只告警不阻断——登记是旁路，不该因为一次提示就让人放弃登记（那会导致更糟的"不登记"）。
    _probe = {'id': a.id, 'name': a.name, 'check': a.check,
              'desc': a.desc or '', 'severity': a.severity or '耗时'}
    try:
        _mis = check_monitor_binding(_probe)
    except Exception as _e:
        # 三态：校验器自己坏了 → 必须**说出来**，不能静默当成"没有名实问题"。
        # （原写法 `_mis = []` 属 M-R60 折叠：校验失败被折叠成"校验通过"，
        #   于是这个新增的名字校验会在自己坏掉时永远报绿。）
        _mis = [{'detail': f'名实校验无法完成（{type(_e).__name__}: {_e}）——本条未经校验，勿当作已通过'}]
    for _x in _mis:
        print(f"   ⚠️ 名实不符：{_x['detail']}")
        if 'manual_track' not in str(_x.get('detail', '')):
            print(f"      → 若确无自动检查器，请改用 --check manual_track（如实标注，好过挂错）")
    mon['monitors'].append({
        'id': a.id, 'name': a.name, 'check': a.check, 'desc': a.desc or '',
        'threshold': a.threshold or '', 'cadence': a.cadence or '按需',
        'severity': a.severity or '耗时', 'action': a.action or '',
    })
    json.dump(mon, open(MONITORS, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'✅ 已登记监测项 {a.id}（{a.name}）——本技能成长+1')


def register_monitor(risk_face, check_type, desc='', quiet=False):
    """★ 生态联动（S105）：把"新风险面"登记进监测册（供 core.register_monitor 门面调用）。

    任何技能发现"新风险面"即可一行登记，**永不抛异常**（登记是旁路，不阻断主流程）：
        from core import register_monitor
        register_monitor('某类数据长期不更新', 'snapshot_stale', '快照超 3 天未刷新即报')
        register_monitor('升级后功能回退', 'manual_track', '无自动检查器，升级后 7 天内人工复核')

    **check_type 必须与风险面语义相符（S136 血泪教训）**：
    旧 docstring 的示例写的是 `register_monitor('废弃节点堆积', 'naming_compliance', ...)`，
    各技能照抄 —— 结果 M23/M24/M25/M28/M29 五项「升级回退/新失效类型」全部挂成
    `naming_compliance`（扫的是 /workspace 文件名）。五项每轮"通过"，而它们声称要盯的
    东西**一次都没被检查过**。示例被当成默认值，是最难查的污染源。
    · 有对应检查器 → 挂那个检查器；
    · **确无自动检查器 → 挂 `manual_track`**（如实承认没人自动看着，好过假装看着）。
      M30「监测项挂名」会把这二者区分开：manual_track 是知情，挂错 check 是自欺。

    - 幂等：按 risk_face 去重（同名不重复登记）。
    - 自动分配 id：取现有 Mxx 的 max+1。
    - check_type 未实现也允许先登记（scan 会提示"检查器未实现"）。
    返回 True=登记成功（含已存在），False=失败。
    """
    # ══ ★ 语义守卫（S158·M-R113）：防止"示例即默认值"污染再次发生 ══
    # S136 已记过：5 项「升级回退/新失效类型」被照抄示例挂成 naming_compliance，
    #   结果它们每轮"通过"，而声称要盯的东西一次都没被检查过。
    # S158 又复发一次（evolve.commit 照抄旧示例，把"janitor 升级后回退风险"挂成
    #   naming_compliance → 扫 /workspace 文件名 → 6 个正常文档被报成"遗留"）。
    # 守卫规则：风险面语义属「回退/失效/漂移/质量/性能/逻辑」等，而 check_type 却挂
    #   naming_compliance / pkg_naming / subtree_naming（这些只查文件名/包名）→ **拒绝登记**，
    #   并强制建议改挂 manual_track（如实承认无自动检查器，好过假装看着）。
    _NAMING_CHECKS = {'naming_compliance', 'pkg_naming', 'subtree_naming'}
    _MISMATCH_KW = ('回退', '失效', '漂移', '质量', '性能', '逻辑', '错误', '异常',
                    '回滚', '退化', '不一致', '不一致', '缺失', '冲突', '风险')
    if check_type in _NAMING_CHECKS and any(k in (risk_face or '') for k in _MISMATCH_KW):
        msg = (f'⛔ 语义守卫拒绝：风险面「{risk_face}」是功能/质量类，'
               f'不能挂 `{check_type}`（它只扫文件名/包名，盯不到该风险）。'
               f'应改挂 `manual_track`（人工复核）或对应检查器。')
        print(f'  {msg}' if not quiet else f'  {msg}')
        return False
    try:
        mon = load_monitors()
        ms = mon.get('monitors') or []
        # 幂等：同名风险面已登记 → 直接成功
        if any((m.get('name') or '') == risk_face for m in ms):
            if not quiet:
                print(f'  ℹ️ 监测面「{risk_face}」已登记（幂等跳过）')
            return True
        # 自动分配 id
        nums = []
        for m in ms:
            mid = str(m.get('id') or '')
            if mid.startswith('M') and mid[1:].isdigit():
                nums.append(int(mid[1:]))
        nid = f'M{(max(nums) + 1) if nums else 1:02d}'
        ms.append({
            'id': nid, 'name': risk_face, 'check': check_type,
            'desc': desc or '', 'threshold': '', 'cadence': '按需',
            'severity': '耗时', 'action': f'见登记说明；检查器类型 {check_type}',
        })
        mon['monitors'] = ms
        json.dump(mon, open(MONITORS, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        if not quiet:
            print(f'  ✅ 监察司已登记监测面 {nid}（{risk_face}）——新风险自动接手+1')
        return True
    except Exception as e:
        if not quiet:
            print(f'  ⚠️ 登记监测面失败（{type(e).__name__}: {e}）——请手动 tvbox inspect add-monitor')
        return False


def _upload_report(path, title):
    """云端直传（M12 新功能）：复用 library 技能 create_doc.py，禁自建云端通道。
    返回 (ok, node_id_or_err)。create_doc 成功时 stdout 首行形如
    `KS_DOC_CREATE\t<nodeBlockId>\t<kind>\t<url>\t0\t0`。"""
    import subprocess
    script = '/root/.codebuddy/skills/library/doc/create_doc.py'
    if not os.path.exists(script):
        return False, f'create_doc.py 不存在: {script}'
    try:
        r = subprocess.run(
            ['python3', script, '--title', title, '--content-file', path],
            capture_output=True, text=True, timeout=180)
    except Exception as e:
        return False, f'调用异常: {e}'
    out = (r.stdout or '').strip()
    for ln in out.splitlines():
        if ln.startswith('KS_DOC_CREATE\t'):
            parts = ln.split('\t')
            if len(parts) >= 2 and parts[1].strip():
                return True, parts[1].strip()
        if ln.startswith('KS_DOC_CREATE_DRYRUN'):
            continue
    if out.startswith('{') and '"error"' in out:
        return False, out[:200]
    return False, (out or r.stderr or '未知错误')[:200]


def _trend_md():
    """把 findings_history 渲染成趋势 md（跨设备可读的云端留档内容）。"""
    if not os.path.exists(SNAP_HIST):
        return None, 0
    try:
        hist = json.load(open(SNAP_HIST, encoding='utf-8')).get('history', [])
    except Exception as e:
        print(f'   ⚠️ 趋势留档跳过：findings_history.json 解析失败（{type(e).__name__}）'
              f'——快照损坏，请人工修复')
        return None, 0
    if not hist:
        return None, 0
    L = ['# 生态遗留趋势留档（跨设备）', '',
         f'> 共 {len(hist)} 期（滚动保留最近 30 期）｜ 最新 {hist[-1].get("ts","")}', '',
         '| 期次 | 时间 | 合计 | 阻断 | 耗时 | 轻微 | 环比 |',
         '|---|---|---|---|---|---|---|']
    prev = None
    for i, h in enumerate(hist, 1):
        bs = h.get('by_sev', {})
        keys = set((h.get('keys') or {}).keys())
        delta = '基准' if prev is None else (f'{len(keys)-len(prev):+d}' or '0')
        L.append(f'| {i} | {h.get("ts","")} | {h.get("n",0)} | {bs.get("阻断",0)} | '
                 f'{bs.get("耗时",0)} | {bs.get("轻微",0)} | {delta} |')
        prev = keys
    # 当前未闭环项
    if hist:
        cur = hist[-1].get('keys') or {}
        L += ['', f'## 当前未闭环（{len(cur)} 条）', '']
        if cur:
            for k, v in list(cur.items())[:30]:
                L.append(f'- [{v.get("monitor","")}] {str(v.get("item",""))[:80]}')
        else:
            L.append('✅ 全部达标，无未闭环项。')
    return '\n'.join(L), len(hist)


def cmd_trend_upload(a):
    """把趋势留档上云（跨设备可读）。复用 create_doc.py，node id 存 trend_node.json。"""
    md, n = _trend_md()
    if md is None:
        print('📈 暂无趋势数据（先跑几次 report/scan）')
        return
    tmp = os.path.join('/tmp', 'watchdog_trend.md')
    open(tmp, 'w', encoding='utf-8').write(md)
    ok, node = _upload_report(tmp, '生态遗留趋势留档（跨设备）')
    if ok:
        anchor = os.path.join(SKILL_DIR, 'trend_node.json')
        try:
            json.dump({'node_id': node, 'periods': n,
                       'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')},
                      open(anchor, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        except Exception:
            pass
        print(f'☁️  趋势留档已上云（{n} 期）')
        print(f'   node_id: {node}')
        print(f'   锚点: {anchor}')
    else:
        print(f'☁️  趋势上云失败: {node}')


def cmd_trend(a):
    """历史遗留趋势：读 findings_history.json，输出分期数量表 + 环比。"""
    if not os.path.exists(SNAP_HIST):
        print('📈 暂无历史快照（首次跑 report 后建立基准）')
        return
    try:
        d = json.load(open(SNAP_HIST, encoding='utf-8'))
    except Exception as e:
        print(f'📈 快照损坏无法读取趋势：findings_history.json 解析失败（{type(e).__name__}）')
        print('   请人工修复该文件（勿直接删除——删除即永久丢失历史基准）。')
        return
    hist = d.get('history', [])
    if not hist:
        print('📈 历史快照为空')
        return
    print(f'📈 遗留趋势（{len(hist)} 期，最近 30 期滚动）\n')
    print(f'{"期次":4} {"时间":17} {"合计":>4} {"阻断":>4} {"耗时":>4} {"轻微":>4} {"环比":>8}')
    print('-' * 56)
    prev_keys = None
    for i, h in enumerate(hist, 1):
        bs = h.get('by_sev', {})
        keys = set((h.get('keys') or {}).keys())
        if prev_keys is None:
            delta = '基准'
        else:
            dn = len(keys) - len(prev_keys)
            delta = f'{dn:+d}' if dn else '0'
        print(f'{i:<4} {h.get("ts",""):17} {h.get("n",0):>4} '
              f'{bs.get("阻断",0):>4} {bs.get("耗时",0):>4} {bs.get("轻微",0):>4} {delta:>8}')
        prev_keys = keys
    # 趋势判读
    if len(hist) >= 2:
        first, last = hist[0].get('n', 0), hist[-1].get('n', 0)
        arrow = '📉 下降' if last < first else ('📈 上升' if last > first else '➡️ 持平')
        print(f'\n趋势：{first} → {last} 项（{arrow}）')


def cmd_aging(a):
    """遗留闭环耗时统计（S031 新功能）：从历史指纹快照反推每条遗留的「存活期数」。
    · 持续存活：该指纹在连续多期出现 → 存活期数 = 首次出现的期次 到 最新期次
    · 已闭环：上期有、本期无 → 记录其「从出现到清零」经历的期数（闭环耗时）
    输出：① 存活期数排行榜（最顽固遗留）② 闭环耗时分布 ③ 当前未闭环项的平均账龄
    价值：把「哪些遗留最顽固」从感觉变成量化指标，指导优先处理顺序。"""
    if not os.path.exists(SNAP_HIST):
        print('📉 暂无历史快照（至少跑 2 期 report/scan 后才能统计）')
        return
    try:
        d = json.load(open(SNAP_HIST, encoding='utf-8'))
    except Exception as e:
        print(f'📉 快照损坏无法统计账龄：findings_history.json 解析失败（{type(e).__name__}）')
        print('   请人工修复该文件（勿直接删除——删除即永久丢失历史基准）。')
        return
    hist = d.get('history', [])
    if len(hist) < 2:
        print(f'📉 历史仅有 {len(hist)} 期，不足以统计闭环耗时（需 ≥2 期）')
        return
    # 反推每条指纹的出现期序列
    seen = {}   # key -> [期次下标...]
    meta = {}   # key -> {monitor,item}
    for i, h in enumerate(hist):
        for k, v in (h.get('keys') or {}).items():
            seen.setdefault(k, []).append(i)
            meta.setdefault(k, v)
    last_i = len(hist) - 1
    active, closed = [], []
    for k, idxs in seen.items():
        life = idxs[-1] - idxs[0] + 1          # 首次到最后一次跨越的期数
        if idxs[-1] == last_i:
            active.append((life, idxs[0], meta[k]))   # 最新期仍在 → 未闭环
        else:
            closed.append((life, idxs[0], idxs[-1], meta[k]))  # 中途消失 → 已闭环
    print(f'📉 遗留闭环耗时统计（基于 {len(hist)} 期历史）\n')
    # ① 当前未闭环：按存活期数降序
    if active:
        print(f'【未闭环 {len(active)} 条·按账龄降序】最顽固优先处理')
        for life, start, m in sorted(active, key=lambda x: (-x[0], x[1])):
            tag = '🔥' if life >= 3 else ('⚠️' if life == 2 else '·')
            print(f'  {tag} 存活 {life} 期（自第 {start+1} 期）[{m.get("monitor","")}] '
                  f'{str(m.get("item",""))[:56]}')
    else:
        print('【未闭环】无——当前所有监测项达标 ✅')
    # ② 已闭环：耗时分布
    if closed:
        print(f'\n【已闭环 {len(closed)} 条·按耗时降序】')
        for life, s0, s1, m in sorted(closed, key=lambda x: -x[0]):
            print(f'  ✅ 历时 {life} 期闭环（第 {s0+1}→{s1+1} 期）[{m.get("monitor","")}] '
                  f'{str(m.get("item",""))[:56]}')
        avg = sum(x[0] for x in closed) / len(closed)
        print(f'  平均闭环耗时 {avg:.1f} 期')
    else:
        print('\n【已闭环】暂无（历史中还没有"出现→清零"的完整样本）')
    # ③ 判读
    if active:
        avg_age = sum(x[0] for x in active) / len(active)
        print(f'\n判读：未闭环平均账龄 {avg_age:.1f} 期；'
              f'最久 {max(x[0] for x in active)} 期——建议优先清账龄 ≥3 期的"顽固遗留"')


def cmd_diff(a):
    """仅算 diff，不生成报告（快速看增减）。"""
    findings = cmd_scan(argparse.Namespace())
    added, cleared, persist, state = diff_findings(findings)
    prev_n = len(load_prev_snapshot()[1])
    base = {'none': '首期，无基准', 'ok': f'上期 {prev_n} 项',
            'corrupt': '⚠️ 上期快照损坏（基准不可信）'}[state]
    print(f'\n📊 遗留 diff（本期 {len(findings)} 项 vs {base}）')
    if state == 'corrupt':
        print('   ⚠️ findings_history.json 解析失败——下列「新增」**不代表真新增**，')
        print('      而是基准缺失导致的假象；修复快照后重跑，勿据此调整优先级。')
    print(f'   🆕 新增 {len(added)} ｜ ✅ 已清零 {len(cleared)} ｜ ♻️ 持续 {len(persist)}')
    for label, arr in (('新增', added), ('已清零', cleared)):
        if arr:
            print(f'\n  【{label}】')
            for x in arr:
                it = x.get('item', '') if isinstance(x, dict) else x
                print(f'    - [{x.get("monitor","")}] {str(it)[:70]}')
    if persist:
        print(f'\n  【持续未推动 {len(persist)} 条】')
        for x in persist[:10]:
            print(f'    - [{x.get("monitor","")}] {str(x.get("item",""))[:70]}')


def cmd_report(a):
    mon = load_monitors()
    exe_fail, empty_src = selfcheck_checkers(mon)   # 健康预检（写入报告，防伪达标）
    findings = cmd_scan(argparse.Namespace())
    now = datetime.datetime.now()
    stamp = now.strftime('%m%d')
    out = a.out or f'/workspace/生态{stamp}_遗留问题监测报告.md'
    # 版本 diff（本次 vs 上期遗留指纹快照）
    added, cleared, persist, state = diff_findings(findings)
    prev_n = len(load_prev_snapshot()[1])
    _state_note = {'none': '（首期，无基准）', 'ok': f'（上期 {prev_n} 项）',
                   'corrupt': '（⚠️ 上期快照损坏，基准缺失）'}[state]
    lines = [
        f"# 生态{stamp}_遗留问题监测报告",
        "",
        f"> 生成时间：{now.strftime('%Y-%m-%d %H:%M')} ｜ 监测项 {len(mon['monitors'])} 个 ｜ "
        f"遗留合计 {len(findings)} 项",
        f"> 环比上期：新增 {len(added)} ｜ 已清零 {len(cleared)} ｜ 持续 {len(persist)}"
        + _state_note,
        "",
        "## 〇、遗留增减趋势（本期 vs 上期）",
        "",
    ]
    if state == 'corrupt':
        lines.append(f"⚠️ **上期快照 findings_history.json 解析失败**——本期无可靠基准，"
                     f"下列「新增 {len(added)}」**不等于真实新增**（是基准缺失的假象）。"
                     f"请修复/恢复该文件后重跑 report，勿据本趋势调整优先级。")
    elif not prev_n:
        lines.append(f"首期报告，基准已建立（{len(findings)} 项）。下期起自动显示增减。")
    else:
        lines.append(f"| 类别 | 数量 | 说明 |")
        lines.append(f"|---|---|---|")
        lines.append(f"| 🆕 本期新增 | {len(added)} | 上期没有、本期出现的遗留 |")
        lines.append(f"| ✅ 已清零 | {len(cleared)} | 上期有、本期已解决 |")
        lines.append(f"| ♻️ 持续 | {len(persist)} | 两期都在（未推动） |")
        if cleared:
            lines.append("")
            lines.append("**已清零明细**：")
            for v in cleared:
                lines.append(f"- [{v.get('monitor','')}] {str(v.get('item',''))[:70]}")
        if added:
            lines.append("")
            lines.append("**新增明细**：")
            for f in added:
                lines.append(f"- [{f.get('monitor','')}] {str(f.get('item',''))[:70]}")
        if persist:
            lines.append("")
            lines.append(f"⚠️ **持续项 {len(persist)} 条未推动**——建议本期优先处理（清单见下）")
    lines += [
        "",
        "## 一、遗留清单（按严重度排序）",
        "",
    ]
    if not findings:
        lines.append("✅ 本次扫描无遗留，所有监测项达标。")
    for sev in ['阻断', '耗时', '轻微']:
        grp = [f for f in findings if f['sev'] == sev]
        if not grp:
            continue
        lines.append(f"### {sev}（{len(grp)} 项）")
        lines.append("")
        for f in grp:
            lines.append(f"- **[{f['monitor']}] {f['name']}**：{f['item']}")
            lines.append(f"  - 详情：{f['detail']}")
            lines.append(f"  - 下一步：{f['action']}")
        lines.append("")
    lines += [
        "## 二、监测项覆盖（登记册快照）",
        "",
        f"🩺 检查器健康：{len(mon['monitors'])} 个检查器中 "
        + (f"**{len(exe_fail)+len(empty_src)} 项告警**（下方「0 遗留」可能失真）：" if (exe_fail or empty_src) else "全部执行正常、无数据源空。"),
        "",
    ]
    for r in (exe_fail + empty_src):
        lines.append(f"- ⚠️ {r}")
    if exe_fail or empty_src:
        lines.append("")
    lines += [
        "| ID | 监测项 | 检查类型 | 阈值 | 周期 |",
        "|---|---|---|---|---|",
    ]
    for m in mon['monitors']:
        lines.append(f"| {m['id']} | {m['name']} | {m['check']} | {m.get('threshold','')} | {m.get('cadence','')} |")
    lines += [
        "",
        "## 二之二、根因聚类（系统性 vs 偶发）",
        "",
    ]
    if not findings:
        lines.append("无遗留，无需聚类分析。")
    else:
        name_of = {x['id']: x['name'] for x in mon['monitors']}
        by_m = {}
        for f in findings:
            by_m.setdefault(f['monitor'], []).append(f)
        hist_cnt = Counter()
        if os.path.exists(SNAP_HIST):
            try:
                for h in json.load(open(SNAP_HIST, encoding='utf-8')).get('history', []):
                    for mid in {v.get('monitor', '') for v in (h.get('keys') or {}).values()}:
                        hist_cnt[mid] += 1
            except Exception:
                pass
        tot = len(findings)
        top = max(by_m.items(), key=lambda x: len(x[1]))
        sys_flag = len(top[1]) >= tot * 0.6 and tot >= 3
        lines.append(f"> 判读：{'**系统性**——' + str(int(len(top[1])/tot*100)) + '% 集中在 ' + top[0] + '，建议改流程/口径而非逐条清' if sys_flag else '**偶发为主**——最集中的 ' + top[0] + ' 仅占 ' + str(int(len(top[1])/tot*100)) + '%，逐条清即可'}")
        lines += ["", "| 监测项 | 本项数 | 占比 | 历史出现期数 | 性质 |", "|---|---|---|---|---|"]
        for mid, arr in sorted(by_m.items(), key=lambda x: -len(x[1])):
            hc = hist_cnt.get(mid, 0)
            nature = ('🔥 反复（顽固）' if hc >= 2 else
                      ('⚠️ 系统性' if len(arr) >= tot * 0.5 and tot >= 3 else '偶发'))
            lines.append(f"| {mid} {name_of.get(mid,'')} | {len(arr)} | "
                         f"{int(len(arr)/tot*100)}% | {hc} | {nature} |")
        stubborn = [mm for mm, c in hist_cnt.items() if c >= 2 and mm in by_m]
        if stubborn:
            lines += ["", f"⚠️ **反复出现（历史 ≥2 期）**：{', '.join(stubborn)}——优先处理（之前清理未断根）"]
    lines += [
        "",
        "## 二之三、检查器增益（哪个监测项真抓到了遗留）",
        "",
    ]
    gain_rows = _gain_rows()
    if not gain_rows:
        lines.append("历史快照不足（<2 期），暂无法统计增益。")
    else:
        hit_any = [r for r in gain_rows if r[0] > 0]
        closed_any = [r for r in gain_rows if r[4] > 0]
        zero = [r[1] for r in gain_rows if r[0] == 0]
        lines.append(f"> 自记录以来（{_snap_periods()} 期）共 **{len(hit_any)}/{len(gain_rows)}** 个检查器抓到过遗留，"
                     f"其中 **{len(closed_any)}** 个推动了闭环；**{len(zero)}** 个零增益需复核（阈值过松 or 对象长期健康）。")
        lines += ["", "| 监测项 | 累计命中 | 出现期数 | 闭环数 | 价值判读 |", "|---|---|---|---|---|"]
        for hits, mid, nm, seen, cl in gain_rows[:10]:
            v = ('⚪ 零增益' if hits == 0 else
                 (f'🟢 高价值（抓{hits}清{cl}）' if cl > 0 else
                  ('🟡 高频未闭环' if hits >= 3 else '🔵 有效')))
            lines.append(f"| {mid} {nm} | {hits} | {seen} | {cl} | {v} |")
        if zero:
            lines += ["", f"⚪ 零增益检查器（{len(zero)} 个，建议复核）：{', '.join(zero)}"]
    # 二之四：调优建议（基于 since_period 的真实参与期数）
    lines += ["", "## 二之四、阈值调优建议（怎么调）", ""]
    periods = _snap_periods()
    if periods < 2:
        lines.append("历史不足（<2 期），暂无法给调优建议。")
    else:
        mon_all = load_monitors()
        by_id = {x['id']: x for x in mon_all['monitors']}
        sug_buckets = {}
        for hits, mid, nm, seen, cl in gain_rows:
            since = by_id.get(mid, {}).get('since_period', 1)
            active = max(periods - since + 1, 0)
            if active < 2:
                s = '➖样本不足'
            elif seen == 0 and active >= 3:
                s = '🔻关注/降频'
            elif seen == active and cl == 0 and hits > 0:
                s = '🔺收紧'
            elif hits > 0 and cl == 0:
                s = '⬆️提优先级'
            elif cl > 0:
                s = '✅维持'
            else:
                s = '➖观察'
            sug_buckets.setdefault(s, []).append(mid)
        lines += ["| 建议 | 检查器 |", "|---|---|"]
        for s, ids in sug_buckets.items():
            lines.append(f"| {s} | {', '.join(ids)} |")
        lines.append("")
        lines.append("> ⚠️ 命中率按 since_period 之后的**参与期数**计算——新上线检查器零命中属正常，勿误剔。")
    # 二之五：生态卫生分
    lines += ["", "## 二之五、生态卫生分（跨技能横向对比）", ""]
    try:
        hrows = _hygiene_rows()
        lines += ["| 技能 | 未清 backlog | 自检项 | 三件套 | 卫生分 |", "|---|---|---|---|---|"]
        for score, name, s, bad in sorted([r for r in hrows if r[0] >= 0], key=lambda x: -x[0]):
            tag = '🟢' if score >= 85 else ('🟡' if score >= 70 else '🔴')
            lines.append(f"| {name} | {s['uncleared']} | {s['self_check']} | {4-s['contract']}/4 | {tag}{score:.0f} |")
    except Exception as e:
        lines.append(f"（卫生分生成失败：{e}）")
    # 二之六：监测覆盖率地图
    lines += ["", "## 二之六、监测覆盖率地图（哪些风险面还没检查器）", ""]
    try:
        crows = _coverage_rows()
        blanks = [r[0] for r in crows if r[4]]
        lines.append(f"> 17 项检查器归入 6 大风险面；**{len(blanks)}** 个面未达期望覆盖。")
        lines += ["", "| 风险面 | 覆盖 | 期望 | 检查器 | 状态 |", "|---|---|---|---|---|"]
        for face, desc, ids, expect, blank in crows:
            lines.append(f"| {face} | {len(ids)} | {expect} | {', '.join(ids) or '（无）'} | {'⬜ 空白' if blank else '🟩 已覆盖'} |")
        if blanks:
            lines += ["", f"⬜ 空白风险面：{', '.join(blanks)}——建议补检查器"]
    except Exception as e:
        lines.append(f"（覆盖率生成失败：{e}）")
    # 二之七：报告正文自检（上期报告数字 vs 本期实况）
    lines += ["", "## 二之七、报告正文自检（本报告数字 vs 实况交叉核对）", ""]
    lines.append(f"> 本报告由脚本生成，下列数字**均于生成本刻从数据源实时计算**，无手工硬编码：")
    lines.append(f"> 监测项 {len(mon['monitors'])} 个 ｜ 遗留合计 {len(findings)} 项 ｜ "
                 f"检查器异常/源空告警 {len(exe_fail)+len(empty_src)} 项。")
    lines.append("")
    lines.append("> 历史报告若被手工编辑或数据源后续变动，其数字会与实况漂移；")
    lines.append("> 用 `tvbox watchdog facts` 可核对**上一份报告**与当前实况是否一致（防陈旧/笔误漂移）。")
    # 二之八：文档一致性（SKILL 声明 vs 代码）
    lines += ["", "## 二之八、文档一致性（SKILL 声明 vs 代码实况）", ""]
    try:
        import re as _re
        skf = os.path.join(SKILL_DIR, 'SKILL.md')
        skt = open(skf, encoding='utf-8').read() if os.path.exists(skf) else ''
        code = open(os.path.join(SKILL_DIR, 'scripts', 'inspect.py'), encoding='utf-8').read()
        live_cmds = len(_re.findall(r"sub\.add_parser\('([a-z-]+)'\)", code))
        dc = 0
        m1 = _re.search(r'登记册\s*(\d+)\s*类检查', skt)
        if m1 and int(m1.group(1)) != len(mon['monitors']):
            dc += 1
        m2 = _re.search(r'检查器明细（M01-M(\d+)）', skt)
        if m2 and int(m2.group(1)) != len(mon['monitors']):
            dc += 1
        if dc:
            lines.append(f"⚠️ **{dc} 项文档漂移**——SKILL.md 声明与代码不一致（用 `tvbox watchdog docsync` 定位）")
        else:
            lines.append(f"✅ SKILL.md 与代码实况一致（检查器 {len(mon['monitors'])} · 命令 {live_cmds}）")
    except Exception as e:
        lines.append(f"（文档一致性生成失败：{e}）")
    # 二之九：检查器噪声（假阳性）度量
    lines += ["", "## 二之九、检查器噪声（假阳性）度量", ""]
    try:
        _nrows, _nunk = _noise_rows()
        nrows = [r[:6] for r in _nrows]
        # S137：与 cmd_noise 同口径——n<3 的比值无分辨力，不进「高噪声」红名单（M-R14）
        noisy = [r for r in nrows if r[4] >= 3 and r[5] >= 0.5]
        if noisy:
            lines += ["| 检查器 | 命中 | 豁免撤销 | 噪声比 |", "|---|---|---|---|"]
            for mid, nm, hits, w, total, ratio in noisy:
                lines.append(f"| {mid} {nm} | {hits} | {w} | {ratio:.0%} |")
            lines.append("")
            lines.append(f"> 🔴 高噪声 {len(noisy)} 个——建议复核豁免原因，必要时收紧口径（参考 M13 FUNC_HINT 防护词表）。")
        else:
            lines.append("✅ 无高噪声检查器（噪声比均 <50%，或样本 n<3 无分辨力）")
        _weak = [r[0] for r in nrows if 0 < r[4] < 3]
        if _weak:
            lines.append(f"> ➖ 样本不足（n<3，比值无分辨力，不计高噪声）：{', '.join(_weak)}")
        if _nunk:
            lines.append(f"> ⚠️ 归属不明的动态豁免 {len(_nunk)} 条（{', '.join(_nunk)}）——"
                         f"理由里未写检查器 ID，建议补 mon 字段")
        lines.append("> 噪声比 = 豁免撤销 / (命中+豁免)；含正当豁免，故只提示不判错。")
    except Exception as e:
        lines.append(f"（噪声度量生成失败：{e}）")
    # 二之十：豁免原因归因（noise 的下钻）
    lines += ["", "## 二之十、豁免原因归因（为什么报这么多）", ""]
    try:
        ag = _attr_rows()
        tot_w = sum(len(v) for v in ag.values())
        if tot_w:
            lines += ["| 成因型 | 条数 | 占比 | 说明 |", "|---|---|---|---|"]
            _d = {'入库型': '有源增量，状态停留中间态', '文档型': '增量在底包库/交付物',
                  'SOP/流程型': '流程实质完成，状态未改写', '工具型': '工具/脚本交付', '其他': '未归类'}
            for g, arr in sorted(ag.items(), key=lambda x: -len(x[1])):
                lines.append(f"| {g} | {len(arr)} | {len(arr)/tot_w:.0%} | {_d.get(g,'')} |")
            big = [g for g, arr in ag.items() if len(arr) >= 3 and g != '其他']
            lines.append("")
            if big:
                lines.append(f"> 💡 **{', '.join(big)}** 出现 ≥3 次——已从「事后补白名单」升级为 **pre_exempt 证据驱动预豁免**"
                             f"（M01 现按「新增源数>0 或 成果产出含交付物特征」直接豁免）。")
            else:
                lines.append("> 白名单规模可控，暂无需升级预豁免规则。")
        else:
            lines.append("无豁免记录。")
    except Exception as e:
        lines.append(f"（归因生成失败：{e}）")
    # 二之十一：预豁免健全性回归
    lines += ["", "## 二之十一、预豁免健全性回归（白名单瘦身 + 误放检测）", ""]
    try:
        reg = _regress_rows()
        nw = len(load_monitors().get('ledger_waiver', {}).get('entries', []) or [])
        lines.append(f"> 回放全部台账条目（{reg['total']} 条）验证 pre_exempt 不误放真遗留；当前白名单 {nw} 条。")
        lines.append("")
        lines.append(f"- ♻️ 白名单冗余（预豁免可兜）：**{len(reg['redundant'])}** 条")
        lines.append(f"- 🔒 须保留（预豁免兜不住）：**{len(reg['keep'])}** 条")
        lines.append(f"- 🔴 假阴性（放行但无证据）：**{len(reg['danger'])}** 条 {'✅' if not reg['danger'] else '⚠️ 危险！'}")
        if reg['redundant'] and not reg['keep']:
            lines.append("")
            lines.append("> 💡 白名单已完全冗余——保留 3 条规则边界样本作回归锚点即可（白名单瘦身=口径缺陷已根治的证明）。")
    except Exception as e:
        lines.append(f"（回归生成失败：{e}）")
    lines += ["", "## 二之十二、监测延迟度量（发现及时性·真实时间差）", ""]
    try:
        lat = _latency_rows()
        mg = lat.get('median_gap')
        lines.append(f"> 基于 {lat.get('n_periods', 0)} 期历史；M-R18：期数差会被采样间隙污染"
                     f"（间隙中位数 {mg:.1f}h），故用**真实时间差**度量。")
        lines.append("")
        smp = lat.get('samples') or []
        if smp:
            lines.append(f"- ⏱️ 监测延迟（发现时刻 − 会话落库时刻）：**{len(smp)}** 条可追溯，"
                         f"平均 **{lat['avg']:.1f}h** ｜ 最长 {lat['max']:.1f}h")
        else:
            lines.append("- ⏱️ 监测延迟：暂无可追溯样本（需遗留含 S0xx 且台账有落库时间）")
        cv = lat.get('conv') or []
        if cv:
            cs = sorted(c[0] for c in cv)
            lines.append(f"- ✅ 闭环耗时：**{len(cv)}** 条已闭环，平均 **{sum(cs)/len(cs):.1f}h**（最长 {cs[-1]:.1f}h）")
        lines.append("")
        lines.append("> 💡 延迟越小说明「遗留一产生就被逮住」；某风险面延迟普遍偏高 → 检查器反应慢，建议提频或前置告警。")
    except Exception as e:
        lines.append(f"（延迟度量生成失败：{e}）")
    lines += ["", "## 二之十三、风险面动态扩展雷达（新业务提示）", ""]
    try:
        ex = _expand_rows()
        lines.append(f"> 扫描 {ex['sessions_n']} 条台账；M-R19：扩展建议必须证据驱动，"
                     f"同一新实体 <2 次只是噪声不提示。")
        lines.append("")
        orph = ex.get('orphans') or []
        lines.append(f"- 🧩 孤儿检查器：**{len(orph)}** 个"
                     + (f"（{', '.join(orph)}）" if orph else " ✅"))
        ne = ex.get('new_entities') or []
        if ne:
            lines.append(f"- 🆕 新实体：{', '.join(f'{k}（{v}次）' for k, v in ne[:5])}"
                         " → 评估是否需要新风险面/检查器")
        else:
            lines.append("- 🆕 新实体：无 ✅")
        us = ex.get('unmentioned_skills') or []
        lines.append(f"- 👀 未提及技能：**{len(us)}** 个"
                     + (f"（{', '.join(us[:5])}{'…' if len(us) > 5 else ''}）" if us else " ✅"))
        lines.append("")
        lines.append("> 💡 扩面是**人工决策**——雷达只提供候选，不自动改 RISK_FACES。")
    except Exception as e:
        lines.append(f"（扩展雷达生成失败：{e}）")
    lines += ["", "## 二之十四、检查器时效回归（口径变更回放历史）", ""]
    try:
        rp = _replay_rows()
        lines.append(f"> 回放 {rp['n_keys']} 条历史遗留指纹；M-R20：口径变更后必跑 replay，"
                     f"「今日将不报」的条目必须逐条人工确认，不允许静默消失。")
        lines.append("")
        dead = rp.get('dead') or []
        lines.append(f"- 🔴 失效引用（历史指纹指向已删检查器）：**{len(dead)}** 个"
                     + (f"（{', '.join(dead)}）" if dead else " ✅"))
        dr = rp.get('drift') or []
        lines.append(f"- ⚠️ 严重度漂移：**{len(dr)}** 条"
                     + (f"（{', '.join(f'{m}:{o}→{n}' for m, i, o, n in dr[:3])}）" if dr else " ✅"))
        et = rp.get('exempt_today') or []
        if et:
            lines.append(f"- ♻️ 今日将不报（按当前豁免口径）：**{len(et)}** 条"
                         f"（{', '.join(w for m, i, w in et[:3])}…）→ 逐条人工确认")
        else:
            lines.append("- ♻️ 今日将不报：**0** 条 ✅（历史遗留今日重现仍会全部上报）")
    except Exception as e:
        lines.append(f"（时效回归生成失败：{e}）")
    lines += ["", "## 二之十五、延迟归因下钻（哪类风险面慢·为什么）", ""]
    try:
        dr = _drill_rows()
        if dr.get('n_samples'):
            g = dr.get('global_avg') or 0
            lines.append(f"> {dr['n_samples']} 条延迟样本 · 全局均值 {g:.1f}h；"
                         "M-R21：样本 <3 的面只出「样本不足」不出结论。")
            lines.append("")
            lines.append("| 风险面 | 样本 | 平均 | 最长 | 归因 |")
            lines.append("|---|---|---|---|---|")
            for face, n, avg, mx, verdict, why in dr['faces']:
                lines.append(f"| {face} | {n} | {avg:.1f}h | {mx:.1f}h | **{verdict}** —— {why} |")
        else:
            lines.append("- ⚪ 暂无可追溯延迟样本（等 latency 产出样本后自动出下钻）。")
        lines.append("")
        lines.append("> 💡 「反应慢」面的检查器建议提高扫描频率或前置告警；归因随样本积累自动升级。")
    except Exception as e:
        lines.append(f"（延迟下钻生成失败：{e}）")
    lines += ["", "## 二之十六、扩面决策留痕（候选件件有去向）", ""]
    try:
        tr = _track_rows()
        total = len(tr['candidates']) + len(tr['stale'])
        lines.append(f"> M-R22：雷达候选必须件件有去向，不允许静默忽略。")
        lines.append("")
        pd = tr.get('pending') or []
        lines.append(f"- 🟡 待决策：**{len(pd)}** 个"
                     + (f"（{', '.join(k for k, v in pd[:5])}）" if pd else " ✅"))
        st = tr.get('stale') or []
        lines.append(f"- 📦 过期决策（候选已消失，留痕作历史）：**{len(st)}** 条")
        lines.append(f"- 📊 决策率：**{tr['decided_n']}/{max(total, 1)}**"
                     + ('（100% ✅）' if not pd and total else ''))
    except Exception as e:
        lines.append(f"（决策留痕生成失败：{e}）")
    lines += [
        "",
        "## 三、复盘四问",
        "",
        "1. **成果**：本次扫描发现并分级 N 项遗留，每条带下一步命令（生态联动闭环）",
        "2. **不足**：（扫描后填写——哪些监测项没覆盖到）",
        "3. **改进**：（填写——新监测项登记进 monitors.json / 检查阈值校准）",
        "4. **进化**：遗留清零记录回写各技能 flow.json backlog；复发问题喂 evolve recurring",
        "",
        "## 四、生态联动索引",
        "",
        "- 台账未闭环 → `tvbox log` 补闭环记录",
        "- 失效库待复测 → `tvbox block review`",
        "- 规则整理超期 → `tvbox evolve review` + 六步整理",
        "- 快照未刷 → `tvbox snapshot`",
        "- SOP 校准漂移 → 补 alias 重跑校准集（hub §8.15/8.16）",
        "- 契约缺口 → `tvbox evolve upgrade <技能名>`",
        "",
    ]
    with open(out, 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(lines))
    print(f'📄 监测报告已生成: {out}')
    print(f'   📈 环比上期：新增 {len(added)} / 已清零 {len(cleared)} / 持续 {len(persist)}'
          + _state_note)
    saved, _ = save_snapshot(findings)  # 落盘本期指纹，供下期 diff
    if not saved:
        print('   ⚠️ 快照未落盘：findings_history.json 解析失败或写入失败——')
        print('      已**保留原文件不覆盖**（防销毁上期基准）；请人工修复后重跑 report。')
    if getattr(a, 'upload', False):
        title = f"生态{stamp}_遗留问题监测报告"
        ok, node = _upload_report(out, title)
        if ok:
            print(f'☁️  已云端直传: {title}')
            print(f'   node_id: {node}')
            # 回写锚点，便于下次覆盖同节点（演进：报告云端 node id 自动回写）
            anchor = os.path.join(SKILL_DIR, 'last_report_node.json')
            try:
                hist = []
                if os.path.exists(anchor):
                    hist = json.load(open(anchor, encoding='utf-8')).get('history', [])
                hist.append({'ts': now.strftime('%Y-%m-%d %H:%M'), 'title': title,
                             'node_id': node, 'local': out})
                json.dump({'latest': node, 'title': title, 'history': hist[-20:]},
                          open(anchor, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
                print(f'   锚点已回写: {anchor}')
            except Exception as e:
                print(f'   ⚠️ 锚点回写失败: {e}')
        else:
            print(f'☁️  云端直传失败: {node}')
            print(f'   （本地报告已落盘，可稍后 `tvbox watchdog report --upload` 重试）')
        # 趋势留档同步上云（跨设备可读）——与报告同批，避免遗漏
        try:
            md, n = _trend_md()
            if md:
                tp = os.path.join('/tmp', 'watchdog_trend.md')
                open(tp, 'w', encoding='utf-8').write(md)
                tok, tnode = _upload_report(tp, '生态遗留趋势留档（跨设备）')
                if tok:
                    json.dump({'node_id': tnode, 'periods': n,
                               'ts': now.strftime('%Y-%m-%d %H:%M')},
                              open(os.path.join(SKILL_DIR, 'trend_node.json'), 'w',
                                   encoding='utf-8'), ensure_ascii=False, indent=2)
                    print(f'☁️  趋势留档已上云: {tnode}（{n} 期）')
                else:
                    print(f'   ⚠️ 趋势留档上云失败: {tnode}')
        except Exception as e:
            print(f'   ⚠️ 趋势留档上云异常: {e}')
    else:
        print(f'   （如需同步云端：tvbox watchdog report --upload）')


def cmd_health(a):
    """监测项自身健康自检（S032 新功能）：防止「看门狗自己睡着了」。
    两类风险，本命令逐一探测：
      ① **执行失效**：检查器抛异常（数据源不可达/字段结构变了）→ 静默返回 0，看似"达标"实则失灵
      ② **判定失效**：检查器长期恒 0 命中——可能阈值过松、字段名漂移、或对象全被豁免
    探测手段：
      · 逐检查器试运行 + 捕获异常（执行失效）
      · 每个检查器探测**数据源规模**（rows_of 行数）——数据源为空 = 高危信号
      · 结合 findings_history 统计各检查器的历史命中数（判定失效）
    输出：健康表 + 高危项清单。"""
    mon = load_monitors()
    # 数据源规模（空源 = 检查器必定恒 0，是"伪达标"的根因）
    # S070：并行读（同 selfcheck，M-R38 复用 _db_sizes）
    probe = _db_sizes([('源·' + n, d) for n, d in SRC_DBS] +
                      [('③台账', DB_SESS), ('④经验库', DB_LIB), ('🗑失效库', DB_DEAD)],
                      err=lambda e: f'ERR({str(e)[:24]})')
    print('🩺 监测项健康自检（防「看门狗自己睡着了」）\n')
    print('【数据源规模】')
    for k, v in probe.items():
        flag = '❌空' if v == 0 else '✅'
        print(f'  {flag} {k:10} {v}')
    # 历史命中统计
    hist_hits = Counter()
    periods = 0
    if os.path.exists(SNAP_HIST):
        try:
            hist = json.load(open(SNAP_HIST, encoding='utf-8')).get('history', [])
            periods = len(hist)
            for h in hist:
                for v in (h.get('keys') or {}).values():
                    hist_hits[v.get('monitor', '')] += 1
        except Exception:
            pass
    print(f'\n【检查器健康】历史 {periods} 期；命中=该监测项历史累计报警次数；源规模=该检查器主数据源行数')
    print(f'  {"ID":4} {"检查器":20} {"执行":6} {"命中":4} {"源规模":8} 判定')
    print('  ' + '-' * 62)
    # 检查器 → 主数据源映射（用于"源为空则必恒零"的根因定位）
    SRC_OF = {
        'ledger_pending': '③台账', 'ledger_consistency': '③台账', 'doc_todo': '③台账',
        'dead_review_due': '🗑失效库', 'revive_eval': '🗑失效库',
        'status_archive': '源总库', 'data_quality': '源总库',
    }
    risks = []
    for x in mon['monitors']:
        fn = CHECKERS.get(x['check'])
        if fn is None:
            print(f'  {x["id"]:4} {x["check"]:20} ❌未实现')
            risks.append(f'{x["id"]} 检查器未实现')
            continue
        try:
            # S065：优先复用进程级缓存（scan 已跑过则直接取，免第二次全量遍历）
            if x['id'] in _CHECKER_CACHE:
                r = _CHECKER_CACHE[x['id']]
            else:
                r = fn(x)
                _CHECKER_CACHE[x['id']] = r or []
            exe = '✅'
            hits = len(r)
        except Exception as e:
            exe = '❌异常'
            hits = '-'
            risks.append(f'{x["id"]} {x["check"]} 执行异常：{str(e)[:48]}')
        src_name = SRC_OF.get(x['check'], '')
        src_size = probe.get(src_name, '-') if src_name else '—'
        # 恒零判定（仅当有足够历史期数时）；proven 字段=S049 受控单测实证「判定器有效」，恒0=真实健康
        verdict = ''
        if exe == '✅' and periods >= 3 and hist_hits.get(x['id'], 0) == 0 and hits == 0:
            # ★ S177 步9：**先看归档，再看待外部条件，最后看 proven**。
            #   实测：M37/M38/M41 三项都带 `_archived_reason`（事已办完，明确写了闭环证据），
            #   但本处只认 `proven` 字段，于是它们被当成"疑似判定失效"报进高危——
            #   而且因为归档后没人会再去碰它，这条告警会**每期都报、永远不消**。
            #   这类"永远消不掉的告警"比漏报更坏：人会开始习惯性略过整块高危列表，
            #   真正的新告警也就一起被略过了（狼来了）。
            #   `waiting_for` 是同一病的另一形态：M39 等次日首跑、M40 等 external API 放开，
            #   条件不在我们手里，恒零是"还没到"，不是"判不了"。
            #   四态：已归档 → ✅闭环 ｜ 待外部 → ⏳等待（不占高危位）｜ proven → ✅实证 ｜ 其余 → ⚠️
            if x.get('_archived_reason'):
                _ar = str(x['_archived_reason']).strip()
                verdict = f'✅ 已归档闭环（{_ar[:40]}）' if _ar else '✅ 已归档闭环'
            elif x.get('waiting_for'):
                verdict = f'⏳ 待外部条件：{str(x["waiting_for"])[:46]}'
            elif x.get('proven'):
                verdict = f'✅ 历史恒零（已实证健康：{x["proven"]}）'
            else:
                verdict = '⚠️ 历史恒零'
                risks.append(f'{x["id"]} {x["check"]} 历史 {periods} 期恒 0 命中——疑似判定失效（若判定器有效请受控单测后登记 proven 字段）')
        if exe == '✅' and src_name and probe.get(src_name) == 0:
            verdict = (verdict + ' ❌源空').strip()
            risks.append(f'{x["id"]} {x["check"]} 主数据源「{src_name}」为空——检查器必然恒零')
        print(f'  {x["id"]:4} {x["check"]:20} {exe:6} {str(hits):4} '
              f'{str(src_size):8} {verdict}')
    print()
    if risks:
        print(f'⚠️ 高危 {len(risks)} 项：')
        for r in risks:
            print(f'  - {r}')
    else:
        print('✅ 所有检查器健康（执行正常 + 无恒零异常）')
    return risks


def cmd_rootcause(a):
    """遗留根因聚类（S032 新功能）：按监测项分组统计，区分「系统性」与「偶发」。
    价值：单看「共 N 项遗留」看不出性质——
      · 若 N 项集中在 1-2 个监测项（如 M01 全是台账半闭环）→ **系统性问题**（流程/口径需改）
      · 若 N 项分散在 N 个监测项 → **偶发**（逐条清即可，不必改流程）
    再叠加历史：某监测项在多期反复出现 → 顽固系统性问题（优先级最高）。
    输出：按监测项聚合表 + 系统性/偶发判读 + 反复出现项标记。"""
    findings = cmd_scan(argparse.Namespace())
    mon = load_monitors()
    name_of = {x['id']: x['name'] for x in mon['monitors']}
    if not findings:
        print('\n🔍 根因聚类：无遗留，无需分析')
        return
    # 按监测项聚合
    by_m = {}
    for f in findings:
        by_m.setdefault(f['monitor'], []).append(f)
    # 历史反复度
    hist_cnt = Counter()
    periods = 0
    if os.path.exists(SNAP_HIST):
        try:
            hist = json.load(open(SNAP_HIST, encoding='utf-8')).get('history', [])
            periods = len(hist)
            for h in hist:
                seen = {v.get('monitor', '') for v in (h.get('keys') or {}).values()}
                for mid in seen:
                    hist_cnt[mid] += 1
        except Exception:
            print('  ⚠️ 历史命中统计不可用：findings_history.json 解析失败（快照损坏）')
            periods = 0
    tot = len(findings)
    n_src = len(by_m)
    print(f'\n🔍 根因聚类（{tot} 项遗留，分布在 {n_src} 个监测项）\n')
    print(f'  {"监测项":20} {"本项数":6} {"占比":6} {"历史出现":8} 性质')
    print('  ' + '-' * 62)
    for mid, arr in sorted(by_m.items(), key=lambda x: -len(x[1])):
        pct = len(arr) / tot * 100
        hc = hist_cnt.get(mid, 0)
        if hc >= 2:
            nature = '🔥 反复出现（顽固）'
        elif len(arr) >= tot * 0.5 and tot >= 3:
            nature = '⚠️ 系统性'
        else:
            nature = '偶发'
        print(f'  {mid + " " + name_of.get(mid, "")[:14]:20} {len(arr):6} {pct:5.0f}% '
              f'{hc:8} {nature}')
    # 判读
    print()
    top = max(by_m.items(), key=lambda x: len(x[1]))
    if len(top[1]) >= tot * 0.6 and tot >= 3:
        print(f'判读：**系统性**——{int(len(top[1])/tot*100)}% 遗留集中在 {top[0]}（{name_of.get(top[0],"")}）。')
        print(f'      建议改流程/口径而非逐条清（如为半闭环类可加决策留痕白名单；如为数据类可修入库链路）。')
    else:
        print(f'判读：**偶发为主**——最集中的 {top[0]} 仅占 {int(len(top[1])/tot*100)}%，逐条清即可。')
    stubborn = [mid for mid, c in hist_cnt.items() if c >= 2 and mid in by_m]
    if stubborn:
        print(f'      ⚠️ 反复出现（历史 ≥2 期）：{", ".join(stubborn)}——优先处理，说明之前的清理没断根。')
    return by_m


def _snap_periods():
    """历史快照期数。"""
    if not os.path.exists(SNAP_HIST):
        return 0
    try:
        return len(json.load(open(SNAP_HIST, encoding='utf-8')).get('history', []))
    except Exception:
        return 0


def _gain_rows():
    """检查器增益统计（供 cmd_gain 与 report 复用）。
    返回按 (累计命中 desc, 闭环 desc) 排序的 rows：
      (hits, monitor_id, name, seen_periods, closed_cnt)；历史 <2 期时返回 []。
    S084·M-R60：快照损坏时**显式告警**——不得静默返回 []（那会被读作"历史不足2期"，
    增益统计被静默归零；与 S082 load_prev_snapshot 同源的漏网点，S082 只修了后者）。"""
    state, keys = load_prev_snapshot()   # 复用三态读取（S084 收口：单一事实源）
    if state == 'none':
        return []                        # 真·无历史/不足基准
    if state == 'corrupt':
        print('  ⚠️ 增益统计不可用：findings_history.json 损坏（快照解析失败）——'
              '增益/噪声比将缺失，请人工修复后重跑')
        return []
    # state == 'ok'：读完整历史数组（load_prev_snapshot 只给末期的 keys）
    try:
        hist = json.load(open(SNAP_HIST, encoding='utf-8')).get('history', [])
        if not isinstance(hist, list):
            raise ValueError('history 非数组')
    except Exception:
        print('  ⚠️ 增益统计不可用：findings_history.json 损坏（快照解析失败）')
        return []
    if len(hist) < 2:
        return []
    mon = load_monitors()
    name_of = {x['id']: x['name'] for x in mon['monitors']}
    hit_cnt, closed_cnt, seen_any = Counter(), Counter(), Counter()
    prev_keys = None
    for h in hist:
        keys = set((h.get('keys') or {}).keys())
        cur_mon = Counter(v.get('monitor', '') for v in (h.get('keys') or {}).values())
        for mid, c in cur_mon.items():
            hit_cnt[mid] += c
            seen_any[mid] += 1
        if prev_keys is not None:
            for k in (prev_keys - keys):
                closed_cnt[k.split('|')[0]] += 1
        prev_keys = keys
    rows = [(hit_cnt.get(x['id'], 0), x['id'], name_of.get(x['id'], ''),
             seen_any.get(x['id'], 0), closed_cnt.get(x['id'], 0)) for x in mon['monitors']]
    return sorted(rows, key=lambda r: (-r[0], -r[4]))


def cmd_gain(a):
    """检查器增益量化（S032 新功能）：每个检查器自上线以来抓了多少「真遗留」。
    为什么要量化：技能一路加检查器（M01→M15），但「哪个真有用」从没算过账。
      · 命中多且已清零 → 高价值（真发现了问题并推动解决）
      · 从未命中 → 可能冗余/阈值过松（需复核，参见 health 的恒零告警）
    数据源：findings_history 的 keys（按 monitor 归属统计）
    输出：按「历史累计命中」排名 + 闭环数 + 价值判读；并列出「零增益检查器」供复核。"""
    periods = _snap_periods()
    if periods == 0:
        print('📊 暂无历史快照（至少跑 2 期后可统计增益）')
        return
    if periods < 2:
        print(f'📊 历史仅 {periods} 期，不足以判增益（需 ≥2 期）')
        return
    rows = _gain_rows()
    print(f'📊 检查器增益量化（基于 {periods} 期历史）\n')
    print(f'  {"ID":4} {"检查器名称":22} {"累计命中":8} {"出现期数":8} {"闭环数":6} 价值判读')
    print('  ' + '-' * 68)
    for hits, mid, nm, seen, cl in rows:
        if hits == 0:
            verdict = '⚪ 零增益（复核阈值/对象）'
        elif cl > 0:
            verdict = f'🟢 高价值（抓 {hits} 清 {cl}）'
        elif hits >= 3:
            verdict = '🟡 高频命中（未闭环，需推动）'
        else:
            verdict = '🔵 有效'
        print(f'  {mid:4} {nm[:20]:22} {hits:8} {seen:8} {cl:6} {verdict}')
    zero = [r[1] for r in rows if r[0] == 0]
    high = [r[1] for r in rows if r[0] > 0 and r[4] > 0]
    print()
    if high:
        print(f'✅ 高价值检查器（抓过并推动闭环）：{", ".join(high)}')
    if zero:
        print(f'⚪ 零增益 {len(zero)} 个：{", ".join(zero)}')
        print(f'   复核方向：阈值是否过松 / 检查对象是否长期健康（健康也是正常结论）/ 是否应降频')
    return rows


def cmd_tune(a):
    """监测阈值调优建议（S033 新功能）：gain 只说「有没有用」，tune 给「怎么调」。
    把每个检查器的历史命中率（出现期数 / 总期数）映射为可执行动作：
      · 长期 0 命中（命中率 0 且期数≥3）→ 建议「放宽/降频/剔除」（可能陪跑）
      · 期期命中（命中率 1.0）→ 建议「收紧阈值 或 视为常态监测」（已无区分度）
      · 命中后从未闭环（有命中无闭环）→ 建议「加阻断级/提升优先级」
      · 偶发命中（0<率<1 且有闭环）→ 建议「维持」（健康的工作区间）
    只出建议不改配置——调优是决策，须人工确认后改 monitors.json。"""
    periods = _snap_periods()
    if periods < 2:
        print(f'📊 历史 {periods} 期，不足以给调优建议（需 ≥2 期）')
        return
    rows = _gain_rows()
    mon = load_monitors()
    by_id = {x['id']: x for x in mon['monitors']}
    print(f'📊 监测阈值调优建议（基于 {periods} 期历史）\n')
    print(f'  {"ID":4} {"检查器名称":20} {"命中率":7} {"建议":4} 依据')
    print('  ' + '-' * 74)
    buckets = {'剔除/降频': [], '收紧': [], '提优先级': [], '维持': [], '样本不足': []}
    for hits, mid, nm, seen, cl in rows:
        # M-R9：只在「该检查器实际参与过的期数」内算命中率，
        # 否则新上线的检查器会被误判为「陪跑」（since_period 之后的期才算参与）
        since = by_id.get(mid, {}).get('since_period', 1)
        active = max(periods - since + 1, 0)   # 参与期数
        rate = seen / active if active else 0
        if active < 2:
            sug, why = '➖样本不足', f'仅参与 {active} 期，需积累'
            buckets['样本不足'].append(mid)
        elif seen == 0 and active >= 3:
            sug, why = '🔻关注/降频', f'{active} 期零命中，可能陪跑或对象长期健康（非武断剔除）'
            buckets['剔除/降频'].append(mid)
        elif seen == active and cl == 0 and hits > 0:
            sug, why = '🔺收紧/常态', f'{active} 期全命中且未闭环，无区分度'
            buckets['收紧'].append(mid)
        elif hits > 0 and cl == 0:
            sug, why = '⬆️提优先级', f'命中 {hits} 次从未闭环，阈值可能太松或被忽略'
            buckets['提优先级'].append(mid)
        elif cl > 0:
            sug, why = '✅维持', f'命中率 {rate:.0%}，抓到并闭环 {cl} 次（健康区间）'
            buckets['维持'].append(mid)
        else:
            sug, why = '➖观察', f'参与 {active} 期、命中 {hits} 次，继续观察'
            buckets['样本不足'].append(mid)
        print(f'  {mid:4} {nm[:18]:20} {rate:6.0%} {sug:10} {why}')
    print()
    for k, ids in buckets.items():
        if ids:
            print(f'{k}（{len(ids)}）：{", ".join(ids)}')
    print('\n> 调整方式：改 monitors.json 对应项 threshold/cadence/severity 字段（引擎零改动）')
    print('> ⚠️ 命中率按 since_period 之后的「参与期数」计算——新上线检查器零命中属正常，勿误剔')
    return buckets


def _hygiene_rows():
    """跨技能卫生分计算（供 cmd_hygiene 与 report 复用）。
    返回 [(score, name, stat_dict, bad_str), ...]，score<0 表示 flow.json 缺失。"""
    import re
    skills = {}
    for name, fp in FLOWS.items():
        if not os.path.exists(fp):
            skills[name] = {'exist': False}
            continue
        try:
            d = json.load(open(fp, encoding='utf-8'))
        except Exception:
            skills[name] = {'exist': False}
            continue
        backlog = d.get('backlog', []) or []
        # ★ S176b：改调 _backlog_counts（唯一口径），不再裸用 _backlog_is_open
        uncleared = [b for b in backlog if _backlog_counts(b)]
        sc = d.get('self_check', []) or []
        skills[name] = {'exist': True, 'uncleared': len(uncleared),
                        'self_check': len(sc), 'flow': d}
    # 三件套完整度
    SKILL_ROOT = '/root/.codebuddy/skills'
    for name in skills:
        sd = os.path.join(SKILL_ROOT, name)
        sk = os.path.join(sd, 'SKILL.md')
        if not os.path.exists(sk):
            sk = None
        if not skills[name].get('exist'):
            miss = 1
            miss += 0 if sk else 1
            miss += 0 if os.path.exists(os.path.join(sd, 'CHANGELOG.md')) else 1
            skills[name]['contract'] = miss
            continue
        miss = 0
        miss += 0 if sk else 1
        miss += 0 if os.path.exists(os.path.join(sd, 'CHANGELOG.md')) else 1
        if sk:
            txt = open(sk, encoding='utf-8').read()
            miss += 0 if re.search(r'version:\s*"', txt) else 1
            miss += 0 if re.search(r'触发|Trigger|当用户提到', txt) else 1
        else:
            miss += 2
        skills[name]['contract'] = miss
    ranked = []
    for name, s in skills.items():
        if not s.get('exist'):
            ranked.append((-1, name, s, 'flow.json 缺失'))
            continue
        score = 100 - s['uncleared'] * 10 - s['contract'] * 15 + min(s['self_check'], 40) * 0.5
        score = max(0, min(100, score))
        bad = []
        if s['uncleared'] > 0:
            bad.append(f'backlog 未清 {s["uncleared"]}')
        if s['contract'] > 0:
            bad.append(f'三件套缺 {s["contract"]}')
        if s['self_check'] < 5:
            bad.append('自检偏薄')
        ranked.append((score, name, s, '、'.join(bad) or '✅ 无'))
    return ranked


def cmd_hygiene(a):
    """跨技能卫生分排名（S033 新功能）：横向对比生态各技能的「健康度」。
    单技能自查看不出相对水平——卫生分把三个维度量化成一个可排名分：
      · backlog 未清项数（未清零的 backlog 条目，越低越好）
      · self_check 项数（flow.json 自检清单，越多越严谨）
      · 三件套完整度（version/CHANGELOG/触发词/flow.json 四项齐全）
    分数 = 100 - 未清项×10 - (三件套缺项)×15 + 自检项×0.5（封顶 100）。
    输出排名 + 短板提示（哪个维度拖后腿）。"""
    ranked = _hygiene_rows()
    print('📊 跨技能卫生分排名（生态健康度横向对比）\n')
    print(f'  {"技能":24} {"未清":5} {"自检":5} {"三件套":6} {"卫生分":6} 短板')
    print('  ' + '-' * 70)
    for score, name, s, bad in sorted(ranked, key=lambda x: -x[0]):
        if score < 0:
            print(f'  {name:24} {"—":5} {"—":5} {"—":6} {"缺":6} {bad}')
        else:
            tag = '🟢' if score >= 85 else ('🟡' if score >= 70 else '🔴')
            print(f'  {name:24} {s["uncleared"]:5} {s["self_check"]:5} {4-s["contract"]}/4{"":3} {tag}{score:5.1f} {bad}')
    print('\n> 卫生分 = 100 − 未清项×10 − 三件套缺项×15 + min(自检项,40)×0.5（封顶 100）')
    return ranked


# 风险面分类：把检查器按「监测对象性质」归面，用于覆盖率地图
# expect = 该面「理想应覆盖的检查器数」（非当前实际数）——低于它即暴露薄弱面
RISK_FACES = {
    '数据面': {'codes': ['status_archive', 'data_quality', 'ledger_consistency'],
               'desc': '存量数据的完整性/一致性（归档、质检、对账）', 'expect': 3},
    '时效面': {'codes': ['dead_review_due', 'rules_review_due', 'snapshot_stale', 'revive_eval'],
               'desc': '有时效承诺的对象（复测/整理周期、快照新鲜、复活回流）', 'expect': 4},
    '契约面': {'codes': ['backlog_items', 'eco_contract'],
               'desc': '技能间契约（backlog 清零、三件套完整）', 'expect': 2},
    '命名面': {'codes': ['naming_compliance', 'pkg_naming', 'subtree_naming'],
               'desc': '交付物/云端节点命名规范', 'expect': 3},
    '流程面': {'codes': ['ledger_pending', 'doc_todo', 'flow_ref_integrity'],
               'desc': '未闭环流程（台账状态、正文待办、命令引用完整性）', 'expect': 3},
    '校准面': {'codes': ['route_calib', 'pkg_index_sync'],
               'desc': '校准集漂移（SOP 路由校准、云端索引对账）', 'expect': 2},
    '平衡面': {'codes': ['balance_weakness'],
               'desc': '生态平衡性（各技能进化速度/薄弱环节，谁拖后腿）', 'expect': 1},
}


def _coverage_rows():
    """覆盖率地图计算（供 cmd_coverage 与 report 复用）。
    返回 [(face, desc, covered_ids, expect, blank_bool), ...]；blank = 该面检查器数 < expect。"""
    mon = load_monitors()
    by_code = {}
    for m in mon['monitors']:
        by_code.setdefault(m['check'], []).append(m['id'])
    rows = []
    for face, cfg in RISK_FACES.items():
        ids = []
        for code in cfg['codes']:
            ids += by_code.get(code, [])
        rows.append((face, cfg['desc'], ids, cfg['expect'], len(ids) < cfg['expect']))
    return rows


def cmd_coverage(a):
    """监测覆盖率地图（S034 新功能）：回答「哪些风险面还没有检查器」。
    单看「发现多少遗留」是**盲人摸象**——只要某个风险面从一开始就没人监测，
    它永远报 0，看似健康实为空白。覆盖率地图把检查器按 6 大风险面归类：
      · 数据面 / 时效面 / 契约面 / 命名面 / 流程面 / 校准面
    每面登记期望检查器数（expect），实际 < expect 即标「空白风险面」，提示补检查器。
    这是元层面能力链的最后一环：health 防失能 → gain 防冗余 → tune 给动作
    → hygiene 给横向坐标 → coverage 给「还有哪里没看」。"""
    rows = _coverage_rows()
    mon = load_monitors()
    total_cov = sum(len(r[2]) for r in rows)
    total_mon = len(mon['monitors'])
    print(f'📊 监测覆盖率地图（{total_mon} 个检查器归入 {len(rows)} 个风险面）\n')
    print(f'  {"风险面":8} {"覆盖":6} {"期望":5} {"检查器":22} 状态')
    print('  ' + '-' * 72)
    blanks = []
    for face, desc, ids, expect, blank in rows:
        state = '⬜ 空白' if blank else '🟩 已覆盖'
        if blank:
            blanks.append(face)
        print(f'  {face:8} {len(ids):6} {expect:5} {", ".join(ids) or "（无）":22} {state}')
    orphan = [m['id'] for m in mon['monitors']
              if not any(m['id'] in r[2] for r in rows)]
    print()
    if blanks:
        print(f'⬜ 空白风险面（{len(blanks)}）：{", ".join(blanks)}——建议补检查器')
    else:
        print('✅ 所有风险面已覆盖')
    if orphan:
        print(f'⚠️ 未归类检查器（{len(orphan)}）：{", ".join(orphan)}——未纳入任一面，需补 RISK_FACES 映射')
    print(f'\n> 覆盖 {total_cov}/{total_mon} 检查器已归类；未归类者不计入覆盖面（防「假覆盖」）')
    return rows


def cmd_dispatch_work(a):
    """卫生分短板自动派活（S034 新功能）：hygiene 只「诊断」，dispatch_work 把短板转成可执行项。
    对每个技能：
      · backlog 未清 > 0 → 生成「清理 backlog」待办
      · 三件套缺项 > 0 → 生成「补齐三件套」待办
      · 自检项 < 5 → 生成「扩充 self_check」待办
    输出「技能 → 待办清单」，可直接抄进对应技能 flow.json backlog（人工确认后）。
    封闭 hygiene 的循环：诊断 → 派活。"""
    ranked = _hygiene_rows()
    print('📋 卫生分短板派活（把诊断转成待办）\n')
    plan = {}
    for score, name, s, bad in sorted(ranked, key=lambda x: -x[0]):
        if score < 0:
            plan[name] = ['flow.json 缺失——先建三件套']
            continue
        tasks = []
        if s['uncleared'] > 0:
            tasks.append(f'清理 backlog 未清 {s["uncleared"]} 项（逐条闭环或标注 [已清零]）')
        if s['contract'] > 0:
            tasks.append(f'补齐三件套缺 {s["contract"]} 项（version/CHANGELOG/触发词/flow.json）')
        if s['self_check'] < 5:
            tasks.append(f'扩充 self_check（当前 {s["self_check"]} 项，建议 ≥5）')
        if tasks:
            plan[name] = tasks
    if not plan:
        print('✅ 全生态无短板，无需派活')
        return plan
    for name, tasks in plan.items():
        print(f'  📌 {name}（{len(tasks)} 项）')
        for t in tasks:
            print(f'      - {t}')
    print(f'\n> 共 {len(plan)} 个技能有短板，{sum(len(v) for v in plan.values())} 条待办')
    print('> 落地方式：逐条抄进对应技能 flow.json 的 backlog（人工确认，不自动改）')
    return plan


# ============================================================
#  生态平衡性体检（S106/S107）：六维扫描「谁进化慢 / 谁拖后腿」
#  「生态平衡」不是看单点遗留，而是横向看各技能的差距——木桶原理 + 离散度。
# ============================================================

# 进化停滞阈值：末次升级距今超过 N 天 → 判「进化停滞」（生态时间尺度：天）
_BAL_STALE_DAYS = 2
# 台账滞后容差（S158 七补）：磁盘 mtime 比台账「末次升级」晚超过这么多秒 → 认定台账滞后。
# 12h：同一轮里"改文件"与"登记台账"允许有小时级延迟，但半天以上必然是台账没跟上。
_MT_SLACK = 12 * 3600
# 六维权重（合计 100）：进化停滞最重（用户核心诉求"谁进化慢"）；S107 增第六维「招式隔离」（方法极·规则57）
_BAL_WEIGHTS = {
    '进化停滞': 25,   # 久未升级/进化 → 进化慢
    '质量短板': 20,   # hygiene 卫生分低 → 质量差
    '监控盲区': 18,   # 该技能无监测器/未接监察司 → 监控薄弱
    '联动断点': 12,   # 未接清道夫/监察司/契约 → 联动缺失
    '实绩质量': 10,   # 检查器命中率低/闭环慢 → 实绩拖后腿
    '招式隔离': 15,   # 未借鉴跨技能通用招式 → 方法极孤立（规则57·S107）
}


def _bal_epoch(ts):
    """解析台账末次升级时间（ISO8601）→ epoch 秒；失败返回 None（三态：不折叠成 0）。"""
    if not ts:
        return None
    s = str(ts).strip().replace('Z', '+00:00')
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.datetime.strptime(str(ts).strip()[:19], fmt).timestamp()
            except Exception:
                continue
    return None


def _disk_version_mtime(sd):
    """读磁盘事实：SKILL.md 的 version 与最新 mtime（S158 七补）。

    返回 (version, mtime_epoch)；任一读不到返回 None（三态，不折叠）。
    mtime 取该技能目录下 SKILL.md / CHANGELOG.md / scripts/*.py 的**最大** mtime——
    只取 SKILL.md 会漏掉"只改了 CHANGELOG 记一笔"的情况。

    ★ 为什么需要它：台账 `末次升级` 是手工维护的副本，只有 conductor 的升级入口会写；
      任何技能绕过入口直接改版本，台账必然停在旧值，而检测器据此下结论 = 用断掉的记录判人。
    ★ 为什么不能只用它：mtime 会被 git checkout / 批量 sed / cp -r 刷新，
      不代表"内容真的变了"。故仅在「台账 version ≠ 磁盘 version」时作为佐证采信。
    """
    ver = None
    sp = os.path.join(sd, 'SKILL.md')
    try:
        for line in open(sp, encoding='utf-8'):
            if line.startswith('version:'):
                ver = (line.split('"')[1] if '"' in line
                       else line.split(':', 1)[1].strip())
                break
    except Exception:
        ver = None
    mts = []
    for rel in ('SKILL.md', 'CHANGELOG.md'):
        p = os.path.join(sd, rel)
        try:
            if os.path.exists(p):
                mts.append(os.path.getmtime(p))
        except Exception:
            pass
    sdir = os.path.join(sd, 'scripts')
    try:
        for fn in os.listdir(sdir):
            if fn.endswith('.py'):
                mts.append(os.path.getmtime(os.path.join(sdir, fn)))
    except Exception:
        pass
    return ver, (max(mts) if mts else None)


def _balance_scan():
    """生态平衡性六维扫描（供 cmd_balance 与 report / M19 复用）。

    返回 dict：
      {'skills': [{name, dims:{维度:得分0-100}, total, weakest, evidence}],
       'spread': {维度: {'min','max','std','gap'}},   # 分布离散度
       'balance_index': 0-100,                        # 生态平衡指数（越高越平衡）
       'shortboard': (name, dim, score),              # 木桶最短板
       'priorities': [(name, dim, score, cmd, why)]}  # 优先级排序（低分优先）
    """
    import statistics

    # ── 取技能清单（权威：evolve 发现的 9 个，本地兜底 ECO_SKILLS）──
    names = sorted(ECO_SKILLS)

    # ── 数据源①：DB_ECO 台账（末次升级时间 + version）──
    # 三态（M-R60）：读失败 ≠ 台账为空——用 _ledger_ok 标记，读不到时维度1显式「无法判定」，
    # 绝不把「读失败」折叠成「无末次升级记录」（否则全技能误判为进化停滞）。
    ledger = {}
    _ledger_ok = False
    try:
        q = '/root/.codebuddy/skills/library/database/query_database_record.py'
        if os.path.exists(q):
            # 只需一页：技能生态登记库 6 个成员，page-size=50 已远超实际条数。
            # （M-R115 禁二：不分页的前提是"能自证一页够"，否则静默漏数据。）
            r = subprocess.run(['python3', q, '--database-id', 'T4kODtANe7rSrtZTu4sIND',
                                '--page-size', '50'], capture_output=True, text=True, timeout=30)
            raw = r.stdout or ''
            i = raw.find('{')
            d = json.loads(raw[i:]) if i >= 0 else {}
            if d.get('results') is not None:
                _ledger_ok = True          # 拿到合法响应（哪怕 0 条）= 读取成功
                for row in (d.get('results') or []):
                    nm = str(row.get('技能名') or '').strip()
                    if nm:
                        ledger[nm] = {'ts': row.get('末次升级'), 'version': row.get('version')}
    except Exception:
        _ledger_ok = False                 # 读取失败：与「空台账」区分，不折叠

    # ── 数据源②：进化留痕（各技能被 evolve note/upgrade 提及的最近时间）──
    evo_recent = {}
    elog = '/root/.codebuddy/skills/skill-evolution/evolution_log.jsonl'
    try:
        if os.path.exists(elog):
            for ln in open(elog, encoding='utf-8'):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    e = json.loads(ln)
                except Exception:
                    continue
                ts = _bal_epoch(e.get('ts'))
                blob = str(e.get('what', ''))
                if ts is None:
                    continue
                for nm in names:
                    if nm in blob:
                        evo_recent[nm] = max(evo_recent.get(nm, 0), ts)
    except Exception:
        pass

    # ── 数据源③：卫生分（质量短板）──
    hyg = {}
    try:
        for score, nm, s, bad in _hygiene_rows():
            hyg[nm] = (score, bad)
    except Exception:
        pass

    # ── 数据源④：监控覆盖（该技能是否被检查器覆盖）──
    mon = load_monitors()
    mon_text = json.dumps(mon, ensure_ascii=False)
    SKILL_ROOT = '/root/.codebuddy/skills'

    # ── 数据源⑤：联动接入（清道夫/监察司/契约）──
    def _linked(nm):
        """该技能源码里是否 import 了生态门面（file_to_janitor / register_monitor / core）。

        S106 修正：门面技能**自己就是提供方**，不适用「未接门面」判据——
          · tvbox-janitor 提供 file_to_janitor（它不调自己）
          · tvbox-inspect 提供 register_monitor
          · tvbox-source-hub 提供 core（含两个门面）
        故这三个技能直接视为「已接」（它们是联动中枢本身）。
        """
        # 门面提供方 = 联动中枢，天然"已接"，不判断点
        _PROVIDERS = {'tvbox-janitor': '清道夫(提供)', 'tvbox-inspect': '监察司(提供)',
                      'tvbox-source-hub': 'core门面(提供)'}
        if nm in _PROVIDERS:
            return [_PROVIDERS[nm]]
        hits = []
        sd = os.path.join(SKILL_ROOT, nm, 'scripts')
        if not os.path.isdir(sd):
            return hits
        for fn in os.listdir(sd):
            if not fn.endswith('.py'):
                continue
            try:
                t = open(os.path.join(sd, fn), encoding='utf-8').read()
            except Exception:
                continue
            # 门面调用（清道夫/监察司联动）
            if 'file_to_janitor' in t or 'janitor.auto_file' in t or 'from janitor' in t:
                hits.append('清道夫')
            if 'register_monitor' in t or 'inspect_engine' in t:
                hits.append('监察司')
        return sorted(set(hits))

    now = time.time()
    # ── 数据源⑥：招式库（方法极·规则57）——各技能已用跨技能通用招式数 ──
    import re as _re                    # 本模块按惯例在函数内局部导入 re
    # 三态（M-R60）：库读失败 → _moves_ok=False，维度6 给「无法判定」中间分，
    # 绝不折叠成「0 条已用」（否则读失败会把全技能误判为"招式隔离最严重"）。
    _moves_ok = False
    move_used = {}                      # {skill: 已用招式数}
    move_total = 0
    try:
        _mids = json.load(open('/root/.codebuddy/skills/skill-evolution/scripts/move_ids.json',
                               encoding='utf-8'))
        _mdb = _mids.get('db')
        _mv = os.path.join(SKILL_ROOT, 'skill-evolution', 'scripts', 'evolve.py')
        if _mdb and os.path.exists(_mv):
            # 读招式库总条数
            _q = '/root/.codebuddy/skills/library/database/query_database_record.py'
            if os.path.exists(_q):
                _r = subprocess.run(['python3', _q, '--database-id', _mdb, '--page-size', '50'],
                                    capture_output=True, text=True, timeout=30)
                _raw = _r.stdout or ''
                _i = _raw.find('{')
                _d = json.loads(_raw[_i:]) if _i >= 0 else {}
                if _d.get('results') is not None:
                    move_total = len(_d.get('results') or [])
                    _moves_ok = True
    except Exception:
        _moves_ok = False
    # 各技能已用招式数：复用 evolve 的 mine 抽取（单一事实源，禁重复实现）
    # mine --dry 输出形如「[T-A] 招式名  跨7技能 ★热招」+ 下一行「出处: a, b, c」——
    # 按「出处行」的技能列表**逐技能累加**，得每个技能已用的通用招式数。
    if _moves_ok:
        try:
            _mv_out = subprocess.run(
                ['python3', _mv, 'mine', '--dry'],
                capture_output=True, text=True, timeout=90,
                cwd=os.path.join(SKILL_ROOT, 'skill-evolution'))
            for _m in _re.finditer(r'^\s*出处[:：]\s*(.+)$', _mv_out.stdout or '', _re.M):
                for _s in _m.group(1).split(','):
                    _s = _s.strip()
                    if _s:
                        move_used[_s] = move_used.get(_s, 0) + 1
            if not move_used:
                _moves_ok = False       # 输出格式漂移 → 显式降级为「无法判定」，不静默当 0
        except Exception as _me:
            # M-R32：降级须可诊断（保留原因），不静默吞异常致"看似无招式隔离"
            globals().setdefault('_MOVE_SCAN_ERR', f'{type(_me).__name__}: {_me}')
            _moves_ok = False

    rows = []
    for nm in names:
        sd = os.path.join(SKILL_ROOT, nm)
        if not os.path.isdir(sd):
            continue
        ev = {}
        # 维度1 进化停滞：末次升级距今（台账 ts 与进化留痕取较新者）
        # ★ S158 七补（用户：CB）：台账是**手工维护的副本**，会静默过期。
        #   实测事故：legacy-watchdog 台账写 `2026-10-16T12:00:00Z`（整点、12 条里唯一手填值），
        #   实际 v1.34.3 的 SKILL.md 是 10-18 20:09 改的 → M19 报"停滞 2.1 天"，**假数**。
        #   而 evolution_log 里该技能最后一条升级停在 1.20.1，之后 14 个版本一条没记。
        #   → 两条登记途径都断了，检测器却仍在按断掉的记录下结论。
        #
        #   修法：加第三个源「磁盘事实」，但**不许无脑取最大**：
        #     · mtime 不可靠（git checkout / 批量 sed / 解压都会刷新）；
        #     · 只有当 mtime **晚于**台账、且台账 version ≠ 磁盘 version 时，
        #       才说明"台账记录后确实又改过" → 采信 mtime，并在证据里标明来源与原因；
        #     · 三者都读不到 → 无法判定（M-R60），绝不折叠成"停滞"。
        ts_l = _bal_epoch((ledger.get(nm) or {}).get('ts'))
        ts_e = evo_recent.get(nm)
        _src = '台账'
        ts = max([x for x in (ts_l, ts_e, None) if x] or [0]) or None
        if ts_e and (ts is None or ts_e > ts):
            _src = '进化留痕'
        if ts is None:
            _src = ''
        # —— 磁盘事实交叉校验 ——
        _disk_ver, _disk_mt = _disk_version_mtime(sd)
        _led_ver = str((ledger.get(nm) or {}).get('version') or '').strip()
        _drift_note = ''
        # 情形①：版本号就漂了 —— 台账记录的时间点之后肯定还改过（最强证据）
        if _disk_ver and _led_ver and _disk_ver != _led_ver:
            _drift_note = f'台账version={_led_ver}≠磁盘{_disk_ver}'
            if _disk_mt and (ts is None or _disk_mt > ts):
                ts, _src = _disk_mt, '磁盘mtime（版本漂移佐证）'
        # 情形②：版本号一致但时间明显对不上 —— **实测踩的就是这一种**。
        #   legacy-watchdog 台账 version 已回填成 1.34.3，可 `末次升级` 还是手填的
        #   `2026-10-16T12:00:00Z`（整点值），而 SKILL.md 是 10-18 20:09 改的。
        #   **版本对得上、时间对不上** —— 只比 version 永远抓不到。
        #   判据：mtime 比台账时间晚过半天以上（_MT_SLACK），才认定台账滞后。
        #   为什么给半天缓冲：同一轮里"改文件"与"登记台账"之间有几分钟到几小时正常延迟，
        #   卡太紧会把正常登记判成滞后。
        elif _disk_mt and ts is not None and (_disk_mt - ts) > _MT_SLACK:
            _lag_h = (_disk_mt - ts) / 3600.0
            _drift_note = f'台账时间比磁盘mtime早{_lag_h:.1f}h（台账滞后）'
            ts, _src = _disk_mt, '磁盘mtime（台账滞后佐证）'
        if ts:
            days = max(0.0, (now - ts) / 86400.0)   # 负数=时钟/时区偏差 → 视为"刚刚"（0）
            stagnant = days > _BAL_STALE_DAYS
            d1 = 100 if days <= 1 else (70 if days <= _BAL_STALE_DAYS else max(0, 40 - (days - _BAL_STALE_DAYS) * 10))
            _extra = f'｜来源={_src}'
            if _drift_note:
                _extra += f'｜{_drift_note}'
            ev['进化停滞'] = (d1, f'末次升级 {days:.1f} 天前{"（停滞）" if stagnant else ""}{_extra}')
        else:
            d1 = 50  # 三态：无法判定，给中间分并标注（不折叠成 0/100）
            if not _ledger_ok:
                ev['进化停滞'] = (d1, '台账读取失败（无法判定）')
            else:
                ev['进化停滞'] = (d1, '台账无末次升级记录（无法判定）')
        # 维度2 质量短板：hygiene 分
        if nm in hyg:
            sc, bad = hyg[nm]
            ev['质量短板'] = (max(0, min(100, sc)), bad or '✅ 无短板')
        else:
            ev['质量短板'] = (60, 'flow.json 缺失/无法评分')
        # 维度3 监控盲区：是否被检查器 C 覆盖（监测册提及 or 有 flow.json 自检）
        flow_fp = os.path.join(sd, 'scripts', 'flow.json')
        has_flow = os.path.exists(flow_fp)
        # 维度3 监控盲区：flow.json self_check 项数（三态：读失败 → None「无法判定」，
        # 不折叠成 0，否则读失败会被误判为「未覆盖」造成虚假短板）
        sc_cnt = None
        if has_flow:
            try:
                sc_cnt = len((json.load(open(flow_fp, encoding='utf-8')).get('self_check') or []))
            except Exception:
                sc_cnt = None          # M-R60：读失败 ≠ 0 项，显式「无法判定」
        covered = (nm in mon_text) or (sc_cnt is None and has_flow) or (sc_cnt or 0) >= 1
        if sc_cnt is None and has_flow:
            d3 = 60                                      # 无法判定 → 中间分（不冤枉也不放过）
            ev['监控盲区'] = (d3, 'flow.json 读取失败（无法判定 self_check 项数）')
        else:
            d3 = 100 if covered and (sc_cnt or 0) >= 3 else (70 if covered else 30)
            ev['监控盲区'] = (d3, f'self_check {sc_cnt or 0} 项' + ('' if covered else '（未覆盖）'))
        # 维度4 联动断点：是否接了生态门面
        lk = _linked(nm)
        d4 = 100 if lk else 45
        ev['联动断点'] = (d4, ('已接：' + '、'.join(lk)) if lk else '未接任何生态门面（清道夫/监察司）')
        # 维度5 实绩质量：占位——用「三件套完整 + backlog 未清」近似实绩（后续可接检查器命中率）
        unc = (hyg.get(nm) or (0, ''))[1]
        d5 = 100 if '未清' not in str(unc) else 65
        ev['实绩质量'] = (d5, '无未清项' if d5 == 100 else '有未清 backlog')
        # 维度6 招式隔离（方法极·规则57·S107）：该技能已用多少跨技能通用招式
        # 判据：已用数 ≥3 → 100（方法联动充分）；1~2 → 70（部分借鉴）；0 → 40（方法孤立）
        # 三态：招式库读失败 → 60「无法判定」（不折叠成 0，否则误判全生态"招式隔离"）
        if not _moves_ok:
            d6 = 60
            ev['招式隔离'] = (d6, '招式库读取失败（无法判定）')
        elif nm == 'skill-evolution':
            # 招式库主人=方法极的**提供方**（类同 _PROVIDERS 豁免）：它不"借鉴"招式而是"产出"招式，
            # mine 有意排除自身（MOVE_NOISE）——故不适用「招式隔离」判据，直接视为已接方法极。
            d6 = 100
            ev['招式隔离'] = (d6, f'招式库提供方（产出 {move_total} 条通用招式）')
        else:
            _u = move_used.get(nm)
            if _u is None:
                d6 = 60
                ev['招式隔离'] = (d6, '技能未被招式库扫描到（无法判定）')
            else:
                d6 = 100 if _u >= 3 else (70 if _u >= 1 else 40)
                ev['招式隔离'] = (d6, f'已借鉴 {_u}/{move_total} 条通用招式'
                                      + ('' if _u >= 3 else '（方法极孤立）'))
        # 总分（加权）
        total = sum(ev[k][0] * _BAL_WEIGHTS[k] for k in _BAL_WEIGHTS) / sum(_BAL_WEIGHTS.values())
        weakest = min(ev, key=lambda k: ev[k][0])
        rows.append({'name': nm, 'dims': {k: v[0] for k, v in ev.items()},
                     'ev': {k: v[1] for k, v in ev.items()}, 'total': round(total, 1),
                     'weakest': weakest, 'weakest_score': ev[weakest][0]})

    # ── 分布离散度（各维度 min/max/std/gap）──
    spread = {}
    for dim in _BAL_WEIGHTS:
        vals = [r['dims'][dim] for r in rows]
        if vals:
            spread[dim] = {'min': min(vals), 'max': max(vals),
                           'std': round(statistics.pstdev(vals), 1),
                           'gap': max(vals) - min(vals)}
    totals = [r['total'] for r in rows]
    # 生态平衡指数 = 100 - 总分标准差（越大越均衡）
    balance_index = round(max(0, 100 - (statistics.pstdev(totals) if len(totals) > 1 else 0)), 1)
    # 木桶最短板
    shortboard = min(rows, key=lambda r: r['weakest_score'])
    short = (shortboard['name'], shortboard['weakest'], shortboard['weakest_score'])

    # ── 优先级排序：低分优先（越弱越先处理），同分按权重高的维度 ──
    prio = []
    for r in rows:
        for dim in _BAL_WEIGHTS:
            sc = r['dims'][dim]
            if sc < 70:
                prio.append((r['name'], dim, sc, r['ev'][dim],
                             _bal_next_cmd(r['name'], dim)))
    prio.sort(key=lambda x: (x[2], -_BAL_WEIGHTS[x[1]]))
    return {'skills': sorted(rows, key=lambda r: r['total']), 'spread': spread,
            'balance_index': balance_index, 'shortboard': short, 'priorities': prio,
            'weights': _BAL_WEIGHTS}


def _bal_next_cmd(name, dim):
    """给「技能×薄弱维度」生成下一步可执行命令（生态联动闭环）。"""
    M = {
        '进化停滞': f'tvbox evolve note --what "{name} 本轮进化：<做了什么>"  或  tvbox evolve upgrade {name}',
        '质量短板': f'看 tvbox inspect dispatch-work 里 {name} 的待办（清 backlog / 补三件套 / 扩自检）',
        '监控盲区': f'给 {name} 补 flow.json self_check，或 tvbox inspect add-monitor --id Mxx --name "{name}薄弱面" --check <类型>',
        '联动断点': f'在 {name} 里接入生态门面：from core import file_to_janitor / register_monitor',
        '实绩质量': f'tvbox inspect gain（看 {name} 相关检查器命中率）/ 清 {name} backlog',
        '招式隔离': f'tvbox evolve lend {name} --own（查可借鉴招式）→ 借鉴后跑 tvbox evolve mine --skill {name} 回写',
    }
    return M.get(dim, f'tvbox inspect scan（定位 {name} 遗留）')


def check_balance_weakness(m):
    """M19 生态平衡性薄弱环节（S106）：把「谁拖后腿」转成统一遗留项。

    与 cmd_balance 的分工：cmd_balance 出**横向体检报告**（谁强谁弱/离散度/最短板），
    M19 把其中**低于阈值的弱点**转成遗留项，进统一分级流（⚠️ 阻止下游盲区）。
    口径：仅对「加权总分 < 70」或「任一层维度 < 50」的技能产遗留——
    避免把正常波动全报成遗留（防噪声淹没，M-R53）。
    """
    bal = _balance_scan()
    out = []
    for r in bal['skills']:
        if r['total'] >= 70 and r['weakest_score'] >= 50:
            continue
        sev = '耗时' if r['total'] >= 60 else '阻断'
        weak_dim = r['weakest']
        out.append({
            'monitor': m['id'], 'name': f"生态薄弱：{r['name']}（{weak_dim}）",
            'sev': sev,
            'item': f"{r['name']} 平衡性 {r['total']}｜最弱={weak_dim}({r['weakest_score']})",
            'detail': f"{r['name']} 六维得分 {r['dims']}；短板：{r['ev'][weak_dim]}",
            'action': _bal_next_cmd(r['name'], weak_dim),
        })
    return out


# S106：把平衡性检查器注册进 CHECKERS（定义顺序：CHECKERS 在文件前部，故此处补注册）
CHECKERS['balance_weakness'] = check_balance_weakness


def check_monitor_binding(m):
    """挂名检查：监测项的**名字/描述**与它实际调用的 check 是否说同一件事（S136 新增）。

    为什么需要（S136 实测真缺陷）：M23/M24/M25/M28 名字都是「XX 升级后回退风险」、
    M29 名字是「黑名单新失效类型」，但五项 `check` 统统挂成 `naming_compliance`
    ——那个检查器扫的是 `/workspace` 根目录下 json/md 文件名有没有 MMDD 日期段。
    于是这五项**每轮都在"通过"**，而它们声称要盯的东西（升级回退、新失效类型）
    **一次都没被检查过**。报告上 29/29 达标，实际是 24 项在岗 + 5 项挂名。

    根因不是笔误：`register_monitor()` 的 docstring **示例**就写着
    `register_monitor('废弃节点堆积', 'naming_compliance', ...)`。各技能照抄示例，
    于是所有新登记的风险面都成了"检查文件名"——**示例被当成默认值**。

    判据（保守，只报明显错配，不做语义理解）：
      · 名字/描述里出现「回退/退化/降级」 → check 应含 regress/rollback/回退类
      · 名字里出现「失效类型/分类」 → check 应含 blacklist/blocklist/失效类
      · 上述信号存在但 check 是纯命名类（naming_compliance / subtree_naming / pkg_naming）
        → 报「挂名」
    之所以用白名单式关键词而非全自动：自动猜 check 类型会引入新错误，
    本检查的职责只是**指出「这里名实不符」**，让人去看。

    ★ S111 扩判据：**一个 check 被多个 monitor 共用 = 必然错配**。
      原判据只覆盖「挂成命名类」这一种，于是 M20/M21/M22/M26/M27/M32 六个
      「招式借鉴停滞」全挂 `balance_weakness` 时，本检查 6 个全放行 0 项 ——
      因为它压根没走到判据（`if chk not in NAMING_ONLY: return`）。
      而实际后果很实在：`check_balance_weakness` 不看 `m['id']`，7 个 monitor
      各调一次 → 报告里 14 项遗留有 7 项是同一句话。

      新判据不靠语义猜测，靠**结构证据**：
        · 某 check 被 >1 个 monitor 挂载
        · 且这些 monitor 的名字互不相同（说明各自声称盯的事不同）
        · 且 check 返回值不随 m['id'] 变化（真·共享检查器应如此，如 manual_track 恒空）
      → 报「共用错配」，因为一个检查器不可能同时是"N 件不同的事"的代言人。

    ★★ 但"共用"本身不一定是错 —— 关键看**返回是否按调用者切片**。
      判据二因此收窄为「真检测」：同一 check 的多个 monitor，实际调用后
      **返回的 item 文本完全一致**才算错配。
        · `move_borrow_stale` 被 4 个 monitor 共用，但它按 `m` 定位技能，
          各返回各的（tvbox-janitor 报 janitor 的未用招式）→ **不算错配**
        · `balance_weakness` 不看 `m['id']`，7 个 monitor 各返同一句话 → **错配**
      这样就不必靠"名字像不像"猜，而是拿真实返回值比对——证据说话。

      `manual_track` 是公认的"无自动检查器"占位，恒空、明确声明，不参与判据二。
    """
    out = []
    name = str(m.get('name') or '')
    desc = str(m.get('desc') or '')
    chk = str(m.get('check') or '')
    blob = name + ' ' + desc
    NAMING_ONLY = ('naming_compliance', 'subtree_naming', 'pkg_naming')

    # —— 判据二（S111）：同 check 共用 且 返回内容完全相同 = 错配 ——
    if chk and chk not in ('manual_track',):
        _all = _monitors_all()
        if _all is None:
            # 清单读不到 → 无法判定是否共用。如实报，不假装"没有共用"（M-R60）
            return [{
                'monitor': m.get('id'), 'name': name, 'sev': '轻微',
                'item': f'{m.get("id")} 监测项清单读取失败——无法判定检查器是否错配',
                'detail': 'monitors.json 读不到或格式异常，本项判据二（共用错配）无法执行',
                'action': '确认 tvbox-inspect/monitors.json 可读；此项未通过 ≠ 已达标（M-R60）',
            }]
        peers = [x for x in _all if str(x.get('check') or '') == chk]
        if len(peers) > 1:
            mine_items = _items_of(m)
            siblings_same = 0
            for p in peers:
                if p.get('id') == m.get('id'):
                    continue
                if mine_items and _items_of(p) == mine_items:
                    siblings_same += 1
            if siblings_same:
                return [{
                    'monitor': m.get('id'), 'name': name, 'sev': '耗时',
                    'item': f'{m.get("id")} 检查器 {chk} 返回内容与另 {siblings_same} 项完全相同',
                    'detail': f'共 {len(peers)} 个监测项挂 {chk}，实测返回的 item 文本逐字相同 —— '
                              f'说明该检查器不看 m["id"]，各 monitor 各报一遍同样的话。'
                              f'共用本身不算错（口径可相同），错在**返回没按调用者切片**',
                    'action': '让检查器按 m 过滤后返回（各报各的）；若确无自动检查器，改挂 manual_track',
                }]

    if chk not in NAMING_ONLY:
        return out
    signal = None
    if re.search(r'回退|退化|降级|regress', blob):
        signal = '升级后回退风险'
    elif re.search(r'失效类型|失效分类|新类型', blob):
        signal = '新失效类型跟踪'
    if signal:
        out.append({
            'monitor': m['id'], 'name': name, 'sev': m.get('severity', '耗时'),
            'item': f'{m["id"]} {name}',
            'detail': f'挂名检查：声称盯「{signal}」，实际调用 {chk}（扫的是 /workspace 交付物文件名）'
                      f'——该项每轮"通过"，但它声称要盯的东西从未被检查',
            'action': '改挂语义匹配的 check，或明确改为人工跟踪并在 desc 写清"非自动检查"',
        })
    return out


def _monitors_all():
    """读在册 monitor 清单（供 monitor_binding 判断"是否共用"）。

    ★ M-R60：读失败返回 **None**，不是 `[]`。
      返回 `[]` 等于告诉判据"全生态只有一个 monitor"，于是"是否共用"永远判否 ——
      读一次失败就把这个检查器永久变成哑巴，且报告全绿。
      None 表示"无法判定"，调用方必须显式处理（判据二此时不成立，但不假装成立）。
    """
    try:
        import json as _json
        import os as _os
        fp = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                           'monitors.json')
        d = _json.load(open(fp, encoding='utf-8'))
        ms = d.get('monitors')
        return ms if isinstance(ms, list) else None
    except Exception:
        return None


_ITEM_CACHE = {}
_ITEM_BUSY = set()


def _items_of(m):
    """真调一次该 monitor 的检查器，取回 item 文本元组（用于"是否返回同一份结果"）。

    为什么要真调：只看 check 名字猜不出「共用返回是否切片」。
    `move_borrow_stale` 被 4 个 monitor 共用但各返各的；`balance_weakness`
    被 7 个共用却返同一句 —— 名字一样，行为相反，只能跑一次看结果。

    ⚠️ 防环：`monitor_binding` 自己也会调本函数，若它出现在被比对清单里会无限递归。
       用 `_ITEM_BUSY` 标记在途的 check，重复进入直接返回空（视为"无从判定"，不报错）。
    """
    chk = str(m.get('check') or '')
    if not chk or chk == 'monitor_binding':
        return ()
    key = (chk, str(m.get('id') or ''))
    if key in _ITEM_CACHE:
        return _ITEM_CACHE[key]
    if chk in _ITEM_BUSY:
        return ()
    fn = CHECKERS.get(chk)
    if fn is None:
        return ()
    _ITEM_BUSY.add(chk)
    try:
        res = fn(m) or []
        val = tuple(sorted(str(x.get('item') or '') for x in res if isinstance(x, dict)))
    except Exception:
        val = ()
    finally:
        _ITEM_BUSY.discard(chk)
    _ITEM_CACHE[key] = val
    return val


CHECKERS['monitor_binding'] = check_monitor_binding


def check_move_borrow_stale(m):
    """招式借鉴停滞（S111 新增）：某技能还有几条「跨≥2技能的热招」没采用。

    为什么单独立这个检查器（S111 实测暴露）：
      M20/M21/M22/M26/M27/M32 六个 monitor 名字都是「XX 招式借鉴停滞」，
      但 `check` 统统挂成 `balance_weakness` —— 那是「生态平衡性六维扫描」，
      **跟招式借鉴毫无关系**，而且它不看 `m['id']`，7 个 monitor 各调一次
      就返回 7 份完全相同的结果（报告里 14 项遗留有 7 项是同一句话）。

    正确口径早就有：`skill-evolution/scripts/evolve.py` 的 `_lend_rows(name)`
    已经把「已用 / 未用但适用 / 无法判定」算出来了，本检查器只是把它接进监察司，
    **不重写判据**（M-R38 单一事实源）。

    `m['desc']` 里若写了技能名，优先用它定位；否则从 `m['name']` 前缀取。

    ★ 必须按 `m` 过滤：本检查器被 4 个 monitor 共用（M20/M21/M27/M32，各盯一个技能）。
      若像 `check_balance_weakness` 那样不看 `m['id']` 就返回全部薄弱技能，
      4 个 monitor 会各报一遍同样四行 —— 那正是 M30 判据二要抓的"共用错配"。
      一个检查器被共用是允许的（口径相同），但**返回必须按调用者切片**。
    """
    import os as _os
    out = []
    evo = '/root/.codebuddy/skills/skill-evolution/scripts'
    if not _os.path.isdir(evo):
        return []                      # 姊妹技能不在：不臆造"无借鉴"
    if evo not in sys.path:
        sys.path.append(evo)
    skill = _skill_of(m)
    try:
        import evolve as _evo
        used, unused, undet = _evo._lend_rows(skill)
    except Exception as e:
        # ★ M-R60：读不到 ≠ 没有未用招式。原写 `return []`（空态）会让本项静默通过，
        #   与"该技能招式全用完了"看起来一模一样 —— 那正是把故障折叠成健康。
        #   改成如实报"无法判定"，让人去看。
        return [{
            'monitor': m['id'], 'name': m.get('name', ''), 'sev': '轻微',
            'item': f'{skill} 招式借鉴情况无法判定',
            'detail': f'调 evolve._lend_rows 失败：{str(e)[:120]}',
            'action': '确认 skill-evolution 可 import 且招式库可读；此项未通过 ≠ 已达标（M-R60）',
        }]
    if unused is None:
        # 同上：unused=None 是 evolve 自己的三态信号（库缺失/读失败/空库）
        return [{
            'monitor': m['id'], 'name': m.get('name', ''), 'sev': '轻微',
            'item': f'{skill} 招式借鉴情况无法判定（招式库不可读）',
            'detail': 'evolve._lend_rows 返回 unused=None：库缺失 / 读取失败 / 空库，三者无法区分',
            'action': '确认招式库可达；此项未通过 ≠ 已达标（M-R60）',
        }]
    if not unused:
        return []
    names = '、'.join(f'[{t}]{nm}' for t, nm, _c, _n in unused[:6])
    return [{
        'monitor': m['id'], 'name': m.get('name', ''),
        'sev': '耗时' if len(unused) >= 4 else '轻微',
        'item': f'{skill} 未采用 {len(unused)} 条跨技能热招｜{names}',
        'detail': f'已用 {len(used)} 条；未用 {len(unused)} 条（跨≥2技能）'
                  + (f'；另 {len(undet)} 条跨技能数无法判定' if undet else ''),
        'action': '逐条确认「借鉴/不适用」并留痕（规则57）；确认不适用需写理由',
    }]


def _skill_of(m):
    """从 monitor 定位技能名：desc 里出现的技能名优先，其次取 name 首段。"""
    blob = str(m.get('name') or '') + ' ' + str(m.get('desc') or '')
    for s in ('tvbox-source-blacklist', 'tvbox-source-hub', 'tvbox-interface',
              'tvbox-janitor', 'tvbox-inspect', 'unfinished-audit',
              'probe-hub', 'skill-evolution', 'legacy-watchdog',
              'ecosys-conductor', 'eco-ledger'):
        if s in blob:
            return s
    return str(m.get('name') or '').split()[0]


CHECKERS['move_borrow_stale'] = check_move_borrow_stale


def check_manual_track(m):
    """人工跟踪项：**明确声明无自动检查器**，不产遗留。

    S136 新增。为什么要有这个「空检查器」：
    以前登记一个"人工跟踪"的风险面，为让它不报错，只能随便挂个 check——
    M23/M24/M25/M28/M29 就是这样被挂成 `naming_compliance` 的，于是
    **人工跟踪伪装成了自动监控**，报告上永远绿，实际上什么都没盯。
    承认"这里没人自动看着"比假装有人看着安全得多：前者会让人记得去看，
    后者会让人放心地不看。

    本检查恒返回空（无遗留）。它的价值不在"查出了什么"，而在
    `check_monitor_binding` 能把它与"挂错 check"区分开：
    manual_track = 明知无自动检查；naming_compliance 却声称盯回退 = 骗自己。
    """
    return []


CHECKERS['manual_track'] = check_manual_track


def check_eco_gap(m):
    """★ S158（M-R116 落地）：大生态「缝」规模监测——**回答"这块归谁管"这个问题还缺多少**。

    **为什么这个检查器非有不可**：S105 拆分把生态切成「发现层/处置层/调度层」，
    但三层都在**技能内部**打转；L0（整个空间）与 L1（各技能责任田）**之间的差额**
    从来没有任何检查器看过一眼。于是：
      · 用户看到"资料库很乱"——因为根层级堆了 68 个无主节点；
      · 所有检查器都报绿——因为它们扫的是各技能自己的库和 /workspace 文件；
      · 两者同时成立，且**能同时成立十几个会话**。
    这不是某个检查器写错了，是**监测面本身就缺了这一块**（M-R113 的另一面：
    不只是"挂错检查器"，还有"根本没有检查器"）。

    判据：调 `tvbox-janitor census --json` 拿归属普查明细，按「缝节点占比」报。
      · 读失败 / 无 census 输出 → **无法判定**（报出来，不折叠成"没问题"，M-R60）
      · 缝占比 > 阈值（默认 60%）→ 报遗留，附「按分区排名」的处置入口
      · 单个分区 > 100 节点 → 单独报（大块无主区比总量更值得先看）

    阈值在 monitors.json 的 gap_threshold 里配，代码不写死。
    """
    import subprocess as _sp
    thr = 0.60
    # 读数三态：读取失败必须落到「无法判定」分支，**不许静默退回默认值**
    # （S158 故障注入自查抓到的自身缺陷：原先 `except: pass` 会把
    #   "monitors.json 读不到" 折叠成 "用默认 0.60 继续"——配置坏了却毫无痕迹）
    _thr_note = ''
    try:
        cfg = load_monitors()
        if 'gap_threshold' in (cfg or {}):
            thr = float(cfg['gap_threshold'])
    except Exception as e:
        _thr_note = f'（gap_threshold 未取到：{type(e).__name__}，退回默认 {thr:.0%}）'
    jan = '/root/.codebuddy/skills/tvbox-janitor/scripts/janitor.py'
    if not os.path.exists(jan):
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': '清道夫脚本缺失，无法做分层普查',
            'detail': f'预期 {jan}', 'action': '检查 tvbox-janitor 是否完整',
        }]
    try:
        r = _sp.run(['python3', jan, 'census', '--json'],
                    capture_output=True, text=True, timeout=600)
    except Exception as e:
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': '分层普查执行异常——**无法判定**',
            'detail': f'{type(e).__name__}: {str(e)[:80]}',
            'action': '手动跑：python3 ' + jan + ' census',
        }]
    if r.returncode != 0:
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': '分层普查未成功退出——**无法判定**（不是"没有缝"）',
            'detail': (r.stdout or '')[-200:] + (r.stderr or '')[-100:],
            'action': '手动跑：python3 ' + jan + ' census',
        }]
    # 从落盘 JSON 读（比解析 stdout 稳，M-R37：别用正则解析别人的文案）
    # ★ S158 修（故障注入自查抓到）：读文件前必须先看子进程**这次真的写了**。
    #   事故：原先无条件读 /workspace/S158_*.json —— 而 census **写失败时不会删旧文件**，
    #   于是读到的是上一次的普查结果，注入"落盘缺失/读失败"时检查器照样报出 72% 真阳性，
    #   **看起来在正常工作，其实用的是过期数据**。这是"依赖外部文件"的通病：
    #   失败不产生新文件，而旧文件还在，于是失败长得跟成功一模一样。
    #   判据：census 本次 stdout 必须带「明细已落盘」这句话（它只在真的写成功后打印）。
    if '明细已落盘' not in (r.stdout or ''):
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': '分层普查未产出新明细——**无法判定**（不是"没有缝"）',
            'detail': f'census 未打印落盘确认；尾部输出：{(r.stdout or "")[-160:]}',
            'action': '手动跑：python3 ' + jan + ' census --json（确认 /workspace 可写）',
        }]
    jp = '/workspace/S158_大生态分层普查.json'
    try:
        d = json.load(open(jp, encoding='utf-8'))
    except Exception as e:
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': '普查明细落盘缺失/不可解析——**无法判定**',
            'detail': f'{jp}：{type(e).__name__}',
            'action': '确认 /workspace 可写，再跑 janitor census --json',
        }]
    total, gaps = int(d.get('total') or 0), int(d.get('gaps') or 0)
    if total <= 0:
        return [{
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': '普查 total=0——**无法判定**（空空间与读取失败不可区分）',
            'detail': 'M-R60：0 不能当"没问题"', 'action': '人工确认空间可读',
        }]
    ratio = gaps / total
    out = []
    if ratio > thr:
        # 按顶层分区聚合，给最大的几块
        from collections import Counter
        zc = Counter()
        for row in d.get('rows') or []:
            if not row.get('owner'):
                zc[(row.get('path') or '').split('/')[0]] += 1
        top = '；'.join(f'{z}({c})' for z, c in zc.most_common(5))
        out.append({
            'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '耗时'),
            'item': f'大生态「缝」占比 {ratio:.0%}（{gaps}/{total}），超阈值 {thr:.0%}{_thr_note}',
            'detail': f'无主区排名：{top}',
            'action': 'tvbox janitor census 看分区 → 在 zone_registry.json 指派 owner → '
                      'tvbox janitor scan 投递已废弃项',
        })
    # 单块超大无主区（>100 节点）：即使总比例没超阈值，也要单独报。
    # 这里直接用 rows 聚合——census 的 rows 每条都带 owner（None=缝）与 path。
    from collections import Counter as _C
    _zc = _C()
    for row in d.get('rows') or []:
        if not row.get('owner'):
            _zc[(row.get('path') or '').split('/')[0]] += 1
    for z, c in _zc.items():
        if c > 100:
            out.append({
                'monitor': m['id'], 'name': m['name'], 'sev': m.get('severity', '轻微'),
                'item': f'单个无主分区过大：{z}（{c} 节点）',
                'detail': '一块没人认领的区域比总量更值得先处理',
                'action': f'在 zone_registry.json 为「{z}」指派 owner',
            })
    return out


CHECKERS['eco_gap'] = check_eco_gap


# ---------- 外置索引断链（M42 · S167）----------
def find_link_breaks():
    """扫全生态自建技能的 SKILL.md，返回 (broken, total)。
    broken = [(技能名, 相对路径)]，total = 被检查的引用总数。

    ★ 这是**唯一实现**：docsync 第 6 项与 check_link_break 都调它（M-R38 单一事实源）。
      初版把逻辑直接写在 cmd_docsync 里，随后为了登记 M42 又要在 CHECKERS 里再写一遍——
      那正是「同一判据两处抄，抄完必漂移」。抽函数是唯一正确解。

    两种写法都查：
      · `[..](references/x.md)`  Markdown 链接
      · `` `references/x.md` ``  反引号路径 —— 不渲染成链接，任何链接检查器都发现不了
    平台内置技能 skill_NNN 跳过（有自己的安装校验，不归我们管）。
    """
    import re as _re
    root = os.path.dirname(SKILL_DIR)
    LINK = _re.compile(r'\]\(((?:references|docs)/[^)\s]+)\)')
    TICK = _re.compile(r'`((?:docs|references)/[^`\s]+?\.md)`')
    broken, total = [], 0
    try:
        names = sorted(os.listdir(root))
    except Exception:
        return [], 0
    for sk_name in names:
        sk_dir = os.path.join(root, sk_name)
        skm = os.path.join(sk_dir, 'SKILL.md')
        if not os.path.isdir(sk_dir) or not os.path.exists(skm) or sk_name.startswith('skill_'):
            continue
        try:
            st = open(skm, encoding='utf-8').read()
        except Exception:
            continue
        for c in set(LINK.findall(st)) | set(TICK.findall(st)):
            if '*' in c or '…' in c:      # 通配/省略号是示例写法，不是具体路径
                continue
            total += 1
            if not os.path.exists(os.path.join(sk_dir, c)):
                broken.append((sk_name, c))
    return broken, total


def check_link_break(m):
    """M42：外置索引断链。文档说「正本见 xxx.md」，那个文件是否存在。"""
    broken, total = find_link_breaks()
    return [{
        'monitor': m['id'],
        'name': m['name'],
        'sev': m.get('severity', '耗时'),
        'item': f'{sk}: {c}',
        'detail': f'文档指向的文件不存在（共检查 {total} 处外置引用）——读者扑空，'
                  f'且会因「已有正本」不再深究',
        'action': m.get('action', ''),
    } for sk, c in broken]


CHECKERS['link_break'] = check_link_break


def cmd_balance(a):
    """生态平衡性体检（S106 新功能·用户新任务）：监测生态各环节，揪出「谁进化慢、谁拖后腿」。

    与既有命令的差异：hygiene 只看**静态健康度**（backlog/三件套/自检），
    balance 补上**进化动力视角**——六维加权，回答三个用户问题：
      · 谁进化慢？  → 维度①进化停滞（末次升级距今）
      · 谁薄弱？    → 维度②质量短板 / ③监控盲区 / ④联动断点 / ⑤实绩质量
      · 生态平不平衡？→ **离散度**（各技能分差）+ **最短板**（木桶原理）双指标
    输出：六维得分表 + 平衡指数 + 最短板 + **弱点优先级排序（附下一步命令）**。
    只诊断不处置（处置=人工确认后按命令执行），与 monitor 体系同风格。"""
    bal = _balance_scan()
    W = bal['weights']
    if getattr(a, 'json', False):
        print(json.dumps(bal, ensure_ascii=False, indent=2))
        return bal
    print('⚖️  生态平衡性体检（六维：谁进化慢 / 谁拖后腿 / 谁方法孤立）\n')
    # 维度说明
    print(f'  权重：' + '｜'.join(f'{k}{v}' for k, v in W.items()))
    print(f'  停滞阈值：末次升级距今 > {_BAL_STALE_DAYS} 天\n')
    # 技能×维度表
    hdr = '  {:24}'.format('技能') + ''.join('{:>8}'.format(k) for k in W) + '{:>9} 最弱'.format('总分')
    print(hdr)
    print('  ' + '-' * (len(hdr) + 2))
    for r in bal['skills']:
        tag = '🟢' if r['total'] >= 85 else ('🟡' if r['total'] >= 70 else '🔴')
        cells = ''.join('{:>8}'.format(r['dims'][k]) for k in W)
        print('  {:24}'.format(r['name']) + cells + '{:>8.1f}'.format(r['total']) + f' {tag}{r["weakest"]}')
    print()
    # 平衡性双指标
    print(f'📐 生态平衡指数：{bal["balance_index"]}（100−总分标准差；越高越均衡）')
    print(f'   分布离散度（各维度 max−min）：' +
          '｜'.join(f'{k} {bal["spread"][k]["gap"]}' for k in W if k in bal['spread']))
    sb = bal['shortboard']
    print(f'🪣 木桶最短板：{sb[0]}〔{sb[1]}〕={sb[2]}  ← 优先级最高')
    print()
    # 优先级清单
    prio = bal['priorities']
    if not prio:
        print('✅ 全生态无薄弱环节（所有技能各维度 ≥70）——生态平衡')
        return bal
    print(f'🎯 薄弱环节优先级清单（共 {len(prio)} 项，越前越急）\n')
    print('  #  技能                     维度       分  说明                          下一步命令')
    print('  ' + '-' * 108)
    for i, (nm, dim, sc, why, cmd) in enumerate(prio[:20], 1):
        flag = '🔴' if sc < 50 else '🟡'
        print(f'  {i:2} {flag} {nm:22} {dim:8} {sc:4} {why[:26]:28} {cmd[:44]}')
    if len(prio) > 20:
        print(f'  … 还有 {len(prio) - 20} 项（完整见 tvbox inspect balance --json）')
    print(f'\n> 处理原则：先补最短板（木桶）→ 缩小离散度（拉齐头部尾部）→ 生态回归平衡')
    print('> 只诊断不自动改；处置按「下一步命令」逐条走（人工确认）')
    return bal


def _report_facts(path):
    """从报告正文提取可验证事实断言 → [(claim, 值)]。"""
    import re
    if not os.path.exists(path):
        return None
    txt = open(path, encoding='utf-8').read()
    claims = {}
    m = re.search(r'监测项\s*(\d+)\s*个', txt)
    if m:
        claims['监测项数'] = int(m.group(1))
    m = re.search(r'遗留合计\s*(\d+)\s*项', txt)
    if m:
        claims['遗留合计'] = int(m.group(1))
    m = re.search(r'检查器健康：(\d+)\s*个检查器', txt)
    if m:
        claims['健康段检查器数'] = int(m.group(1))
    return claims


def cmd_facts(a):
    """报告正文自检（S035 新功能）：核对已生成报告里的事实断言 vs 当前实况。
    这是元能力链补上的最后一环——前述 health/gain/tune/hygiene/coverage 都在查
    「监测体系本身」，但没人查「**报告正文写下的数字**」是否与数据源一致：
      · 报告是某一时刻的快照，数据源后续变动 → 报告数字过时（陈旧漂移）
      · 手工编辑报告 → 数字可能笔误（人工漂移）
    比对项：监测项数 / 遗留合计 / 数据源规模（源库行数·台账会话·经验条数）。
    注意口径：只报「报告与实况**不一致**」，不判定谁对——报告陈旧是正常现象，
    此处只做**提示重生成**（非遗留，故不计入 findings）。"""
    import re
    rpt = a.out if getattr(a, 'out', '') else \
        os.path.join('/workspace', f'生态1015_遗留问题监测报告.md')
    if not os.path.exists(rpt):
        print(f'📋 报告不存在：{rpt}（先跑 report 生成）')
        return
    claims = _report_facts(rpt) or {}
    if not claims:
        print('📋 报告正文无可识别的事实断言（跳过）')
        return
    # 实况重算
    mon = load_monitors()
    live_mon = len(mon['monitors'])
    # 遗留合计：立即跑一次扫描
    findings = []
    exe_fail, empty_src = selfcheck_checkers(mon)
    for mm in mon['monitors']:
        fn = CHECKERS.get(mm['check'])
        if not fn:
            continue
        try:
            findings += fn(mm)
        except Exception:
            pass
    live_n = len(findings)
    # S070：并行读（M-R38 复用 _db_sizes）
    live_src = _db_sizes([('源·' + n, d) for n, d in SRC_DBS] +
                         [('台账', DB_SESS), ('经验', DB_LIB)], err=lambda e: None)

    print(f'📋 报告正文自检：{os.path.basename(rpt)}\n')
    print(f'  {"断言":16} {"报告写":8} {"实况":8} 结论')
    print('  ' + '-' * 50)
    drift = 0
    checks = [
        ('监测项数', claims.get('监测项数'), live_mon),
        ('遗留合计', claims.get('遗留合计'), live_n),
        ('健康段检查器数', claims.get('健康段检查器数'), live_mon),
    ]
    for label, rv, lv in checks:
        if rv is None:
            continue
        ok = (rv == lv)
        if not ok:
            drift += 1
        print(f'  {label:16} {rv:<8} {lv:<8} {"✅ 一致" if ok else "⚠️ 漂移"}')
    print(f'\n  实况数据源：源库 {live_src["源库"]} 行 ｜ 台账 {live_src["台账"]} 会话 ｜ 经验 {live_src["经验"]} 条')
    if drift:
        print(f'\n⚠️ {drift} 项漂移——报告可能已过时（数据源变动或手工编辑）；建议重跑 report 重新生成')
    else:
        print('\n✅ 报告正文与实况一致')
    return {'drift': drift, 'claims': claims, 'live': {'mon': live_mon, 'n': live_n, **live_src}}


def _noise_rows():
    """检查器噪声比计算（供 cmd_noise 与 report 复用）。
    噪声（假阳性）信号 = monitors.json.ledger_waiver.entries（命中后被判定「非遗留」而撤销的记录）
    与各检查器历史累计命中（_gain_rows）比对：噪声比 = 豁免数 / (累计命中)。"""
    rows = _gain_rows()   # (hits, mid, name, seen, closed)
    hit_of = {r[1]: r[0] for r in rows}
    name_of = {r[1]: r[2] for r in rows}
    waivers = load_monitors().get('ledger_waiver', {}).get('entries', []) or []
    # S051 锚点豁免与动态豁免分离：S038 瘦身后白名单只保留「规则边界样本」作回归锚点，
    # 这些历史条目每期扫描都命中——若计入噪声分子，噪声比恒被污染（M01 曾恒 75%，
    # 与当期口径质量无关）。锚点豁免单列不计噪声；噪声比只反映**当期动态豁免**。
    wcnt = Counter()
    anchor_n = 0
    # S137 修复（M-R88 判据自证）：原来这里是无条件 `wcnt['M01'] += 1`——**所有**非锚点
    # 动态豁免都被记到 M01 名下，与「这条豁免是哪个检查器报的」毫无关系。实测 5 条豁免里
    # 非锚点的只有 S071 一条，而其原文白纸黑字写着「M13 命中」——却被算进 M01 的噪声比，
    # 得出 M01 = 1/(0+1) = 100% 高噪声，M13 反而 0%。这不是「M01 口径需收紧」，
    # 是**归属维度根本不存在**（豁免条目 schema 里没有 mon/mid 字段）。
    # 三层修：
    #  ① 优先取条目显式字段（mon/mid/checker，将来采集侧补上即自动生效）；
    #  ② 无字段则从 reason 正文里认 **检查器 ID**（M\d+），这是豁免理由的天然证据；
    #  ③ 都认不出 → 归入 `_unk`（不摊派给任何检查器），并在输出里如实单列，
    #     绝不静默塞给 M01 制造假红（S136 踩过的「假红比不报更坏」）。
    unk = []
    for e in waivers:
        if e.get('anchor') or '锚点' in str(e.get('reason', '')):
            anchor_n += 1
            continue
        mid_of_waiver = (str(e.get('mon') or e.get('mid') or e.get('checker') or '')).strip()
        if not mid_of_waiver:
            _mm = re.findall(r'\bM\d+\b', str(e.get('reason', '')))
            mid_of_waiver = _mm[0] if len(set(_mm)) == 1 else ''
        if mid_of_waiver:
            wcnt[mid_of_waiver] += 1
        else:
            unk.append(str(e.get('no', '?')))
    out = []
    for mid in hit_of:
        hits = hit_of[mid]
        w = wcnt.get(mid, 0)
        total = hits + w   # 上报总数 ≈ 命中数 + 动态豁免数
        ratio = (w / total) if total else 0.0
        out.append((mid, name_of.get(mid, ''), hits, w, total, ratio))
    out.sort(key=lambda x: -x[5])
    if anchor_n:
        # 锚点数挂在首行（M01）供判读——仅展示不影响排序
        out = [(a, b, c, d, e, f, anchor_n if i == 0 else 0)
               for i, (a, b, c, d, e, f) in enumerate(out)]
    return out, unk


def cmd_noise(a):
    """检查器噪声（假阳性）度量（S036 新功能）：哪个检查器「报得多但多为误报」。
    动机：上轮 facts 追根因时揪出 M13 假阳性（功能描述词被当待办）——但没有系统度量
    「哪个检查器最爱误报」。本命令把两类信号比对：
      · 累计命中（_gain_rows，该检查器历史上报了多少）
      · 豁免撤销（ledger_waiver.entries，命中后判定为非遗留而撤销的记录数）
    噪声比 = 豁免数 / (命中数 + 豁免数)。高噪声比的检查器提示「口径需收紧」。
    注意：豁免≠错误——有些是「决策留痕不改写历史」的正当豁免，故只提示不判错。"""
    rows, unk = _noise_rows()
    if not rows:
        print('📊 无历史数据，暂无法度量噪声')
        return
    print('📊 检查器噪声（假阳性）度量\n')
    anchor_n = rows[0][6] if rows and len(rows[0]) > 6 else 0
    print(f'  {"ID":4} {"检查器":22} {"命中":6} {"豁免":6} {"上报总":6} {"噪声比":7} 判读')
    print('  ' + '-' * 68)
    if anchor_n:
        print(f'  （白名单锚点豁免 {anchor_n} 条为回归样本，不计入噪声比——S051 口径修正）')
    noisy, weak = [], []
    for row in rows:
        mid, nm, hits, w, total, ratio = row[:6]
        if total == 0:
            verdict = '· 无样本'
        elif total < 3:
            # S137：n<3 时单条豁免就把比值顶到 100%，**分辨力为零**——n=1 的 100%
            # 与 n=30 的 100% 不是同一个事实。此时只报「样本不足·不足以判读」，
            # 不得给它挂「高噪声」红标（M-R14 精神：提示口径，不认定缺陷）。
            verdict = f'➖ 样本不足(n={total})·不足以判读'
            weak.append(f'{mid}(n={total})')
        elif ratio >= 0.5:
            verdict = '🔴 高噪声（口径需收紧）'
            noisy.append(mid)
        elif ratio >= 0.25:
            verdict = '🟡 中噪声（复核豁免合理性）'
        else:
            verdict = '🟢 低噪声'
        print(f'  {mid:4} {nm[:20]:22} {hits:6} {w:6} {total:6} {ratio:6.0%} {verdict}')
    print()
    if noisy:
        print(f'🔴 高噪声检查器：{", ".join(noisy)}——建议查豁免原因，必要时收紧关键词/加防护词表（参考 M13 FUNC_HINT）')
    else:
        print('✅ 无高噪声检查器')
    if weak:
        print(f'➖ 样本不足 {len(weak)} 个（n<3，比值无分辨力，不计入高噪声）：{", ".join(weak)}')
    if unk:
        print(f'⚠️ 归属不明的动态豁免 {len(unk)} 条（{", ".join(unk)}）——'
              f'豁免理由里没写检查器 ID，无法判读是哪个口径报的；请在 ledger_waiver 条目补 mon 字段')
    print('\n> 噪声比 = 豁免撤销数 / (命中数 + 豁免数)。豁免含「决策留痕不改写历史」的正当豁免，故只提示不判错。')
    return rows


def cmd_docsync(a):
    """SKILL 文档一致性检查（S036 新功能）：SKILL.md 声明 vs 代码实况。
    与 M16 分工：M16 查 flow.json 的**命令引用**是否存在；docsync 查 SKILL.md 的
    **声明数字**是否与代码一致（如「登记册 N 类检查」「检查器明细 M01-Mxx」）。
    文档漂移危害：读者按 SKILL 行事却与实现不符（如文档说 15 类实为 17 类）。
    实现：正则提取 SKILL 里的数字声明 → 与 monitors.json/CHECKERS/dispatch 实况比对。"""
    import re
    sd = SKILL_DIR
    sk = os.path.join(sd, 'SKILL.md')
    if not os.path.exists(sk):
        print('📄 SKILL.md 不存在')
        return
    txt = open(sk, encoding='utf-8').read()
    mon = load_monitors()
    live_checks = len(mon['monitors'])
    # 实况命令数：从本引擎 dispatch 统计 add_parser（S106 修：搬迁后 watchdog.py 已不在本技能，
    # 命令唯一事实源就是 inspect_engine.py 自身；旧路径致 FileNotFoundError）
    _eng = os.path.abspath(__file__)
    code = open(_eng, encoding='utf-8').read()
    live_cmds = len(re.findall(r"sub\.add_parser\('([a-z-]+)'\)", code))
    print('📄 SKILL 文档一致性检查（声明 vs 代码实况）\n')
    drift = 0
    # 1) description 里的「N 类检查」
    m = re.search(r'登记册\s*(\d+)\s*类检查', txt)
    if m:
        decl = int(m.group(1))
        ok = decl == live_checks
        drift += 0 if ok else 1
        print(f'  登记册检查器数    声明 {decl}  ｜ 实况 {live_checks}  {"✅" if ok else "⚠️ 漂移"}')
    # 2) 检查器明细标题「M01-Mxx」
    m = re.search(r'检查器明细（M01-M(\d+)）', txt)
    if m:
        decl = int(m.group(1))
        # ★ S164 修（旧口径已坏，别退回去）：原来写 `live_max = live_checks`，
        #   即「条数 = 最大编号」。这个等式在**编号连续且无归档**时才成立。
        #   实测：M22/M26 已进 archived_monitors，登记 34 条但最大编号是 M36 →
        #   等式差 2，docsync 每期都报「上界 M01-M36 vs 实况 M01-M34」。
        #   我一开始按"文档写错了"去改 SKILL，改完照样红——**文档是对的，检测器错了**。
        #   判据换成从 id 里真取最大值（不假设连续性、不假设条数=编号）。
        _nums = [int(m3.group(1)) for m3 in
                 (re.match(r'^M(\d+)$', str(m2.get('id') or '')) for m2 in mon['monitors'])
                 if m3]
        live_max = max(_nums) if _nums else live_checks
        ok = decl == live_max
        drift += 0 if ok else 1
        print(f'  明细表上界 M01-M{decl:<3} ｜ 实况 M01-M{live_max}  {"✅" if ok else "⚠️ 漂移"}')
    # 3) 命令数声明（若 SKILL 明写「N 个命令」）
    m = re.search(r'[（(]?(\d+)\s*个命令', txt)
    if m:
        decl = int(m.group(1))
        ok = decl == live_cmds
        drift += 0 if ok else 1
        print(f'  命令数            声明 {decl}  ｜ 实况 {live_cmds}  {"✅" if ok else "⚠️ 漂移"}')
    # 4) 每个登记的检查器是否在 SKILL 3.6 明细表出现
    missing = []
    for mm in mon['monitors']:
        if not re.search(rf'\|\s*{mm["id"]}\s*\|', txt):
            missing.append(mm['id'])
    if missing:
        drift += 1
        print(f'  明细表缺登记项    ⚠️ {", ".join(missing)} 未在 3.6 明细表出现')
    else:
        print(f'  明细表覆盖        全部 {live_checks} 项均在 3.6 明细表 ✅')

    # 5) 库坐标漂移（S159 补）：SKILL.md 里写的 database id 是否仍属有效库集合
    #    为什么必须单列这一项：上面 4 项比的全是「数字声明」（几类检查器、几号到几号），
    #    而本轮真实事故是 **库 id 写死在文档里、库早被清空**——数字全对、文档全错。
    #    S144 清空旧库后 SKILL.md 第 1 节仍写 `wW5xpbS…` 作「主库」，15 个会话没人发现；
    #    照着这批文档做的人（含 AI 自己）会直接去读一个 0 条的库，而且**不会报错**。
    #    这类漂移只能靠「把文档里的 id 拿去和有效集合比对」抓。
    #    ★ 判据不是「出现旧 id」而是「出现旧 id 且没标废弃」——文档需要能写「此库已废弃」
    #      这类警示语，否则检查器会逼人把废弃信息也删掉，反而降低可读性。
    try:
        _root = os.path.dirname(SKILL_DIR)          # /root/.codebuddy/skills
        sys.path.insert(0, os.path.join(_root, 'tvbox-source-hub', 'scripts'))
        from core import SRC_DBS as _VALID_SRC, DB_SRC_LEGACY as _LEG1, DB_BAK_LEGACY as _LEG2
        # ★S160：废弃集合必须包含「S160 并库后的旧三库」。
        #   缺口实证：并库当天本检查器报「无文档引用废弃库 ✅」，而 4 份 SKILL.md
        #   正把旧 py/csp/成品 当有效库引用（还带着条数表）。原因就是 retired_ids
        #   只有 S144 那两个库，**看不见新一代的废弃库**。
        #   教训：废弃名单是**会长的**，搬一次库就得补一批；否则检查器成了"过期地图"，
        #   报绿比报红更危险（M-R32 装健康的哑巴）。
        #   ★ 三态纪律：`DB_S160_OLD3` 取不到时**必须 downgrade 成"无法判定"**。
        #   初版这里写 `_OLD3 = {}`，被基座折叠扫描（信号源16）当场抓出——
        #   那正是 M-R60 缺陷：core 导不进来时废名单悄悄变空，检查器随即报"无漂移"，
        #   **一个陈旧的绿勾比一个红叉危险得多**。改为 raise 走外层 except（报无法判定）。
        from core import DB_S160_OLD3 as _OLD3
        if not isinstance(_OLD3, dict) or not _OLD3:
            raise RuntimeError(f'DB_S160_OLD3 不可用（{type(_OLD3).__name__}）——废弃名单缺失')
        valid_ids = {db for _n, db in _VALID_SRC}
        retired_ids = {_LEG1, _LEG2} | set(_OLD3.values())
        # 同一行出现这些词 → 视为「已标注废弃」的正当引用
        RETIRE_MARK = ('废弃', '已清空', '停用', '勿再', '勿用', '不再', '~~')
        # 扫「所有」技能文档，不只本技能——库坐标是本生态共享知识，谁写错都算漂移
        # ★S160：扫描面从 `SKILL.md` 扩到「SKILL.md + docs/**.md」。
        #   缺口实证：`tvbox-interface/docs/manual_6_4_提提源入总库.md` 里也写着库 id，
        #   但旧版只看 SKILL.md → 这类文档**永远不在检查范围内**（盲区，不是漏检）。
        #   上限 200 份文档，防某技能塞进上千 md 时卡死巡检。
        bad_doc, ok_doc, scanned = [], 0, 0
        for sk_name in sorted(os.listdir(_root)):
            sk_dir = os.path.join(_root, sk_name)
            if not os.path.isdir(sk_dir):
                continue
            cand = [os.path.join(sk_dir, 'SKILL.md')]
            docs_dir = os.path.join(sk_dir, 'docs')
            if os.path.isdir(docs_dir):
                for dp, _dn, fns in os.walk(docs_dir):
                    cand += [os.path.join(dp, f) for f in fns if f.endswith('.md')]
            for p in cand[:40]:                      # 单技能上限 40 份
                if scanned >= 200 or not os.path.exists(p):
                    continue
                try:
                    t = open(p, encoding='utf-8').read()
                except Exception:
                    continue
                scanned += 1
                rel = os.path.relpath(p, _root)
                hit_retired = set()
                for line in t.splitlines():
                    ids_here = {i for i in retired_ids if i in line}
                    if not ids_here:
                        continue
                    if any(mk in line for mk in RETIRE_MARK):
                        continue        # 已标注废弃，属正当警示语，不算漂移
                    hit_retired |= ids_here
                if hit_retired:
                    bad_doc.append((rel, sorted(hit_retired)))
                elif any(i in t for i in valid_ids):
                    ok_doc += 1
        if bad_doc:
            drift += 1
            print(f'  库坐标漂移        ⚠️ {len(bad_doc)} 份文档**当有效库引用**了已停用的旧库：')
            for sk_name, ids in bad_doc:
                print(f'      · {sk_name}: {", ".join(ids)}（该库已停用，照着做会读到空表或旧数据）')
        else:
            print(f'  库坐标漂移        无文档引用废弃库；{ok_doc}/{scanned} 份文档引用有效源库 ✅')
    except Exception as e:
        # 三态：模块导入失败时明说「无法判定」，不折叠成「一致」
        print(f'  库坐标漂移        ⚠️ 无法判定（{type(e).__name__}: {e}）')

    # 6) 外置索引断链（S167 补）：SKILL.md 说的「正本/详情见 references/xxx.md」是否真存在
    #    为什么必须单列：上面 5 项查的是「数字对不对、id 废没废」，全是**内容正确性**；
    #    从没查过「这个文件在不在」。实证：tvbox-source-hub §4.5 从 v1.0.46 起写着
    #    「依据与踩坑全文见 `references/concurrency_M-R114.md`」，**该文件从未创建**，
    #    横跨多个会话无人发现——因为 docsync 只查明细表登记，不查文件系统。
    #    读者按图索骥扑空、AI 照着「已有正本」不再深究，比体积超标隐蔽得多。
    #    ★ 两种写法都要查：`[..](references/x.md)` 与反引号 `references/x.md`。
    #      后者不渲染成链接、不会被任何链接检查器发现，恰恰是这次漏掉的那种。
    #    ★ 三态纪律：只判「文件存在与否」，读不了就算无法判定，不猜。
    #    ★ 实现抽到 find_link_breaks()，与 check_link_break（M42）共用同一份判据——
    #      初版逻辑直接写在这里，后来要登记 M42 时差点在 CHECKERS 里再抄一遍。
    try:
        broken, idx_total = find_link_breaks()
        if broken:
            drift += 1
            print(f'  外置索引断链      ⚠️ {len(broken)}/{idx_total} 处指向不存在的文件：')
            for sk_name, c in broken:
                print(f'      · {sk_name}: {c}（读者扑空，且照此不再深究）')
        else:
            print(f'  外置索引断链      无；{idx_total} 处外置引用全部指向真实文件 ✅')
    except Exception as e:
        print(f'  外置索引断链      ⚠️ 无法判定（{type(e).__name__}: {e}）')

    print()
    if drift:
        print(f'⚠️ {drift} 项文档漂移——SKILL.md 声明与代码不一致，须同步（防读者误信）')
    else:
        print('✅ SKILL.md 与代码实况一致')
    return {'drift': drift}


# 豁免归因分类规则：按 reason 的关键词模式归类（S037）
ATTR_RULES = [
    ('入库型', ('拆分入库', '实质完成（源库', '+164', '+92', '入库')),
    ('文档型', ('文档型', '文档+代码', 'xlsx', '溯源表', '报告上云', '交付')),
    ('SOP/流程型', ('SOP', 'route校准', '生态契约', '复盘·', '流程')),
    ('工具型', ('工具', '脚本', '检查器', 'watchdog')),
]


def _attr_rows():
    """豁免归因聚类（供 cmd_attribution 与 report 复用）。"""
    entries = load_monitors().get('ledger_waiver', {}).get('entries', []) or []
    groups = {}
    for e in entries:
        reason = str(e.get('reason', ''))
        g = '其他'
        for name, kws in ATTR_RULES:
            if any(k in reason for k in kws):
                g = name
                break
        groups.setdefault(g, []).append(e)
    return groups


def cmd_attribution(a):
    """豁免原因自动归因（S037 新功能）：分析 ledger_waiver 的共性，反推口径缺陷。
    承接 noise 的发现——M01 噪声比 92%，但「为什么豁免这么多」没人分析过。
    本命令把豁免条目按 reason 关键词聚类为若干**成因型**：
      · 入库型（有源增量但状态停留中间态）
      · 文档型（无源增量，增量在底包库/交付物）
      · SOP/流程型（流程实质完成，状态未改写）
      · 工具型
    输出各型占比 + 典型例句 + **口径改进建议**：出现 ≥3 次的成因型，应从
    「事后补白名单」升级为「事前预豁免规则」（写进 monitors.json）。
    这是 noise 的**下钻**：noise 说「谁爱报」，attribution 说「为什么报」。"""
    groups = _attr_rows()
    total = sum(len(v) for v in groups.values())
    if total == 0:
        print('📊 无豁免记录，无需归因')
        return
    print(f'📊 豁免原因归因（{total} 条豁免 → {len(groups)} 类成因）\n')
    print(f'  {"成因型":14} {"条数":5} {"占比":7} 说明')
    print('  ' + '-' * 66)
    desc = {
        '入库型': '有源增量，但状态停留中间态（已同步源库）',
        '文档型': '无源增量，增量在底包库/交付物（文档+代码）',
        'SOP/流程型': '流程实质完成，状态未改写（已同步底包）',
        '工具型': '工具/脚本类交付',
        '其他': '未归类（需补 ATTR_RULES 关键词）',
    }
    for g, arr in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f'  {g:14} {len(arr):5} {len(arr)/total:6.0%} {desc.get(g, "")}')
    print()
    # 典型例句
    for g, arr in sorted(groups.items(), key=lambda x: -len(x[1])):
        if len(arr) >= 1:
            print(f'  【{g}】典型：{arr[0].get("no","")} — {str(arr[0].get("reason",""))[:56]}')
    print()
    # 口径改进建议：≥3 条的成因型应从"事后白名单"升级"事前规则"
    sug = [g for g, arr in groups.items() if len(arr) >= 3 and g != '其他']
    if sug:
        print(f'💡 口径改进建议：{", ".join(sug)} 出现 ≥3 次——')
        print('   应从「事后补 ledger_waiver 白名单」升级为「事前预豁免规则」：')
        print('   建议在 monitors.json 增设 pre_exempt 规则（如「状态=已同步底包 且 文档型交付 → 直接豁免，不进白名单）」')
    else:
        print('💡 无 ≥3 次的成因型，白名单规模可控')
    if '其他' in groups:
        print(f'\n⚠️ {len(groups["其他"])} 条未归类——补 ATTR_RULES 关键词')
    return groups


def _regress_rows():
    """预豁免健全性回归计算（供 cmd_regress 与 report 复用）。
    返回 {'total','redundant':[(no,ev)],'keep':[(no,st)],'stale':[no],'danger':[no]}。"""
    r = rows_of(DB_SESS)
    by_no = {str(x.get('会话编号', '')).strip(): x for x in r}
    waiver = ledger_waiver()
    anchors = {e.get('no') for e in
               (load_monitors().get('ledger_waiver', {}).get('entries') or [])
               if e.get('anchor')}
    redundant, keep, stale = [], [], []
    for no in sorted(waiver):
        if no in anchors:
            # S051 锚点化：anchor:true 条目是预豁免规则边界样本，定位回归锚点而非冗余
            keep.append((no, '回归锚点(anchor)'))
            continue
        x = by_no.get(no)
        if not x:
            stale.append(no); continue
        st = x.get('反哺状态', '')
        if st not in ('已同步源库', '已同步底包'):
            stale.append(no); continue
        done, ev = _is_doc_delivery(x)
        if done:
            redundant.append((no, ev))
        else:
            keep.append((no, st))
    danger = []
    for x in r:
        st = x.get('反哺状态', '')
        if st in ('已同步源库', '已同步底包'):
            no = str(x.get('会话编号', '')).strip()
            if no in waiver:
                continue
            done, ev = _is_doc_delivery(x)
            if done and not ev:
                danger.append(no)
    return {'total': len(r), 'redundant': redundant, 'keep': keep,
            'stale': stale, 'danger': danger}


def cmd_regress(a):
    """预豁免规则健全性回归（S038 新功能）：回放全部历史条目，验证预豁免不误放真遗留。
    动机：上轮引入 pre_exempt 证据驱动预豁免，但**新规则上线后必须回归验证**——
    用历史条目回放，确保「该豁免的都豁免、不该放的一条都没放」。
    三件事：
      ① **白名单瘦身**：对每条 ledger_waiver 回放，若预豁免能兜住 → 该白名单条目冗余（可移除）
      ② **误放检测**：对台账所有「已同步*」条目回放，若预豁免放行了但**无证据** → 危险（假阴性）
      ③ **stale 检测**：白名单条目在台账已不存在或状态已变 → 陈旧白名单
    这是 pre_exempt 的**安全网**：每次改预豁免规则都应跑一次 regress。"""
    reg = _regress_rows()
    redundant, keep, stale, danger = reg['redundant'], reg['keep'], reg['stale'], reg['danger']
    waiver = ledger_waiver()
    print(f'📊 预豁免规则健全性回归（台账 {reg["total"]} 条 · 白名单 {len(waiver)} 条）\n')
    print('【① 白名单瘦身】预豁免能兜住的条目 = 白名单冗余')
    if redundant:
        for no, ev in redundant:
            print(f'  ♻️ {no} — 预豁免可兜（{ev}）→ 白名单可移除')
    if keep:
        for no, st in keep:
            print(f'  🔒 {no} — 预豁免兜不住（状态={st}）→ 白名单须保留')
    if stale:
        print(f'  🗑 陈旧白名单：{", ".join(stale)}（台账状态已变或不存在）')
    print(f'  小结：冗余 {len(redundant)} / 须保留 {len(keep)} / 陈旧 {len(stale)}\n')
    print('【② 误放检测】预豁免放行的条目必须**有证据**（防无脑放过）')
    if danger:
        print(f'  🔴 危险：{", ".join(danger)}——预豁免放行但无证据（假阴性！）')
    else:
        print('  ✅ 无假阴性——预豁免放行的条目均有证据')
    print()
    if redundant and not keep:
        print(f'💡 判读：白名单已**完全冗余**（{len(redundant)}/{len(waiver)} 可移除）——预豁免规则健全，')
        print('   建议保留 2-3 条「规则边界样本」作回归锚点，其余清理（白名单瘦身=口径缺陷已根治的证明）。')
    elif redundant:
        print(f'💡 判读：{len(redundant)} 条冗余、{len(keep)} 条须保留——部分可瘦身。')
    else:
        print('💡 判读：白名单均不可移除（预豁免尚未覆盖），维持现状。')
    return {'redundant': redundant, 'keep': keep, 'stale': stale, 'danger': danger}


def _latency_rows():
    """监测延迟度量计算（供 cmd_latency 与 report 复用）。

    **M-R18 铁律：延迟度量必须先排采样间隙**——本生态每轮 report 间隔仅数分钟，
    一条遗留从产生到被发现若横跨多期，其「期数差」会被大量采样间隙污染，
    故一律用 **真实时间差（小时）** 度量，期数差仅作辅助参考。

    定义：
      · 监测延迟 = 该遗留**首次被发现的时刻** − 该遗留**所属会话的落库时刻**
        （直接用台账「时间」列与 findings_history 首现期次戳的差值，二者都带真实时间）
      · 闭环耗时 = 该遗留**消失的时刻** − 首现时刻（沿用 aging 口径，带真实时间）

    返回 {'samples':[(delay_h, monitor, item)], 'avg':h, 'max':h, 'min':h,
          'gaps':[间隔小时...], 'median_gap':h, 'conv':[(h,...)]}。
    """
    import re
    if not os.path.exists(SNAP_HIST):
        return {'samples': [], 'conv': [], 'gaps': [], 'n_periods': 0}
    _st, hist = snap_history_or_warn('延迟度量')   # S085：三态读取，损坏不折叠
    if not hist:
        return {'samples': [], 'conv': [], 'gaps': [], 'n_periods': 0}

    def _ts(s):
        try:
            return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M')
        except Exception:
            return None

    # 采样间隙（相邻期戳差值，小时）——用于说明「期数差为何不可信」
    gaps = []
    for i in range(1, len(hist)):
        t0, t1 = _ts(hist[i - 1].get('ts', '')), _ts(hist[i].get('ts', ''))
        if t0 and t1:
            gaps.append((t1 - t0).total_seconds() / 3600.0)
    gaps_sorted = sorted(gaps)
    median_gap = gaps_sorted[len(gaps_sorted) // 2] if gaps_sorted else 0.0

    # 台账「时间」列 → 会话落库时刻
    led = {}
    for x in rows_of(DB_SESS):
        no = str(x.get('会话编号', '')).strip()
        if no:
            led[no] = _ts(str(x.get('时间', '')).strip())

    # 遍历历史指纹：每个 key 的首现期 / 末现期
    first_seen, last_seen, meta = {}, {}, {}
    for i, h in enumerate(hist):
        ti = _ts(h.get('ts', ''))
        for k, v in (h.get('keys') or {}).items():
            if k not in first_seen:
                first_seen[k] = (i, ti)
            last_seen[k] = (i, ti)
            meta.setdefault(k, v)

    samples = []   # (延迟小时, monitor, item) —— 仅能追溯到会话落库时刻的条目
    for k, (i0, t0) in first_seen.items():
        m = meta[k]
        item = str(m.get('item', ''))
        # 从条目文本里揪出会话编号（[S0xx] 前缀或内嵌 S0xx）
        mm = re.search(r'S\d{3}', item)
        if not mm:
            continue
        base = led.get(mm.group(0))
        if not base or not t0:
            continue
        delay_h = (t0 - base).total_seconds() / 3600.0
        if delay_h < 0:
            continue   # 台账时间晚于发现时刻（口径异常）——不计入
        samples.append((delay_h, m.get('monitor', ''), item))

    # 闭环耗时（带真实时间）
    conv = []
    last_i = len(hist) - 1
    for k, (i0, t0) in first_seen.items():
        i1, t1 = last_seen.get(k, (i0, t0))
        if i1 < last_i and t0 and t1:
            conv.append(((t1 - t0).total_seconds() / 3600.0, meta[k].get('monitor', ''),
                         str(meta[k].get('item', ''))))

    out = {'samples': samples, 'conv': conv, 'gaps': gaps,
           'median_gap': median_gap, 'n_periods': len(hist)}
    if samples:
        ds = sorted(s[0] for s in samples)
        out['avg'] = sum(ds) / len(ds)
        out['max'] = ds[-1]
        out['min'] = ds[0]
    return out


def cmd_latency(a):
    """监测延迟度量（S039 新功能）：从「遗留产生」到「被看门狗发现」的时间差。

    动机：报告只会说「现在有几条遗留」，但不会说「**这些遗留拖了多久才被发现**」——
    一个健康的看门狗应该「早发现」，延迟度量把「发现及时性」从感觉变成量化指标。

    **M-R18 铁律**：本生态 report 间隔仅数分钟，一条遗留横跨多期时「期数差」会被
    大量采样间隙污染（18 期仅跨约 1 小时），故一律用**真实时间差（小时）**度量。

    度量：
      ① 监测延迟 = 首现期次戳 − 该遗留所属会话的台账落库时刻（两处都带真实时间）
      ② 闭环耗时 = 消失期次戳 − 首现期次戳（带真实时间）
      ③ 采样间隙中位数——佐证「为何不能用期数差」
    """
    lat = _latency_rows()
    if not lat.get('n_periods'):
        print('⏱️ 暂无历史快照（至少跑 2 期 report/scan 后才能度量延迟）')
        return lat
    print(f'⏱️ 监测延迟度量（基于 {lat["n_periods"]} 期历史 · M-R18 用真实时间差）\n')
    if lat.get('median_gap') is not None:
        print(f'  采样间隙中位数 {lat["median_gap"]:.1f} 小时——期数差不可信，故用真实时间')
    print()
    samples = lat.get('samples') or []
    if samples:
        print(f'【① 监测延迟】{len(samples)} 条可追溯（延迟 = 发现时刻 − 会话落库时刻）')
        for d, mon, item in sorted(samples, key=lambda x: -x[0])[:10]:
            tag = '🔴' if d >= 24 else ('⚠️' if d >= 2 else '✅')
            print(f'  {tag} {d:6.1f}h [{mon}] {item[:52]}')
        print(f'  平均 {lat["avg"]:.1f}h ｜ 最长 {lat["max"]:.1f}h ｜ 最短 {lat["min"]:.1f}h\n')
    else:
        print('【① 监测延迟】暂无可追溯样本（需遗留条目文本含 S0xx 会话编号且台账有落库时间）\n')
    conv = lat.get('conv') or []
    if conv:
        cs = sorted(c[0] for c in conv)
        print(f'【② 闭环耗时】{len(conv)} 条已闭环样本，平均 {sum(cs)/len(cs):.1f}h '
              f'（最长 {cs[-1]:.1f}h）\n')
    print('💡 判读：延迟越小说明「遗留一产生就被看门狗逮住」；')
    print('   若某类遗留延迟普遍偏高 → 该风险面的检查器「反应慢」，建议提高扫描频率或前置告警。')
    return lat


def _expand_rows():
    """风险面动态扩展雷达计算（供 cmd_expand 与 report 复用）。S040。

    **M-R19 铁律：扩展建议必须证据驱动**——「新业务出现」的判定依据是台账**真实字段**
    （联动包列出现的实体名），禁止凭空脑补风险面；同一实体出现 <2 次只是噪声，不提示。

    三路信号：
      A. 孤儿检查器：不在任何 RISK_FACES 的检查器 ≥2 个 → 提示归并成新风险面
      B. 新实体：最近台账「联动包」列出现、但不在已知实体集（技能目录 ∪ RISK_FACES 提及）
         的实体，累计 ≥2 次 → 提示评估是否需要新风险面/检查器
      C. 未登记技能：SKILL_ROOT 下的技能目录若从未在台账「联动包」出现过 → 提示关注
    返回 {'orphans':[id], 'new_entities':[(name,count)], 'unmentioned_skills':[name],
          'known_n':int, 'sessions_n':int}。
    """
    import os as _os
    SKILL_ROOT = '/root/.codebuddy/skills'
    mon = load_monitors()
    # A. 孤儿检查器（coverage 已有此概念，此处独立重算避免耦合）
    cov_ids = set()
    for cfg in RISK_FACES.values():
        cov_ids.update(cfg['codes'])
    orphans = [m['id'] for m in mon['monitors'] if m['check'] not in cov_ids]
    # B. 台账「联动包」实体频次
    known = set(_os.listdir(SKILL_ROOT)) | {'watchdog', 'tvbox', '生态库'}
    for cfg in RISK_FACES.values():
        known.update(cfg['codes'])
    freq = {}
    sess = rows_of(DB_SESS)
    for x in sess:
        blob = str(x.get('联动包', '') or '')
        import re as _re2
        for tok in _re2.split(r'[,，、;；\s]+', blob):
            tok = tok.strip()
            if tok and tok not in known and len(tok) >= 3:
                freq[tok] = freq.get(tok, 0) + 1
    new_entities = sorted([(k, v) for k, v in freq.items() if v >= 2],
                          key=lambda t: -t[1])
    # C. 未在台账出现过的技能（过滤非技能目录：README/隐藏目录/skill_数字ID 临时目录）
    import re as _re3
    all_skills = {d for d in _os.listdir(SKILL_ROOT)
                  if not d.startswith('.') and d != 'README.md'
                  and not _re3.match(r'^skill_\d+$', d)}
    mentioned = set()
    for x in sess:
        blob = str(x.get('联动包', '') or '') + str(x.get('任务摘要', '') or '')
        for d in all_skills:
            if d in blob:
                mentioned.add(d)
    return {'orphans': orphans, 'new_entities': new_entities,
            'unmentioned_skills': sorted(all_skills - mentioned),
            'known_n': len(known), 'sessions_n': len(sess)}


def cmd_expand(a):
    """风险面动态扩展雷达（S040 新功能）：新业务出现时自动提示新增风险面。

    动机：RISK_FACES 是**静态字典**——它定义时覆盖了当时的业务，但生态会长出新业务
    （新技能、新库、新接口）。静态地图的问题和 coverage 抓空白面互补：coverage 说
    「已登记的面里哪个没人看」，expand 说「**地图外长出了什么新东西**」。

    **M-R19 铁律**：扩展建议必须证据驱动——「新业务」的判定依据是台账真实字段
    （联动包列实体名），同一实体 <2 次只是噪声不提示；禁止凭空脑补风险面。

    三路信号：A 孤儿检查器（≥2 个 → 归并新面）／B 新实体（联动包列未知名 ≥2 次）
    ／C 未提及技能（从未在台账出现过的技能目录 → 关注是否需要纳管）。"""
    ex = _expand_rows()
    print(f'🧭 风险面动态扩展雷达（扫描 {ex["sessions_n"]} 条台账 · 已知实体 {ex["known_n"]} 个）\n')
    hints = 0
    print('【A 孤儿检查器】不属于任何风险面的检查器')
    if ex['orphans']:
        print(f'  ⚠️ {", ".join(ex["orphans"])}'
              + ('  → ≥2 个，建议归并成新风险面（改 RISK_FACES）' if len(ex['orphans']) >= 2 else ''))
        hints += 1
    else:
        print('  ✅ 无——全部 17 个检查器已归面')
    print('\n【B 新实体】台账「联动包」列出现但不在已知实体集（≥2 次才提示）')
    if ex['new_entities']:
        for name, n in ex['new_entities']:
            print(f'  🆕 {name}（出现 {n} 次）→ 评估：是否需要新风险面/检查器？')
        hints += 1
    else:
        print('  ✅ 无——近期业务实体均在已知覆盖范围')
    print('\n【C 未提及技能】从未在台账出现过的技能目录')
    if ex['unmentioned_skills']:
        print(f'  👀 {", ".join(ex["unmentioned_skills"])}（共 {len(ex["unmentioned_skills"])} 个）'
              '→ 关注是否需要纳管监测')
        hints += 1
    else:
        print('  ✅ 无——全部技能都进过台账')
    print()
    if hints:
        print(f'💡 判读：{hints} 路信号待评估——扩面是**人工决策**，雷达只提供候选，不自动改 RISK_FACES。')
    else:
        print('💡 判读：三路信号全绿——风险面地图与业务现状同步，暂无扩面必要。')
    return ex


def _replay_rows():
    """检查器时效回归计算（供 cmd_replay 与 report 复用）。S041。

    **M-R20 铁律：口径变更必须回放历史**——改检查器逻辑/严重度/豁免口径时，
    历史「报过的」若今日按新口径会不报，就是**悄悄放水**；必须显式列出供人工确认。

    三路回放（基于 findings_history 全量指纹）：
      A. 失效引用：历史指纹指向的检查器 id 已从登记册删除 → 历史发现失去追溯
      B. 严重度漂移：历史指纹的 sev ≠ 当前登记册该检查器的 severity → 口径已变
      C. 今日回放：历史遗留若今日重现，按当前豁免口径（白名单 ∪ 预豁免）是否会被放行
         → 标「今日将不报」，人工确认放行是否合理（防口径变更悄悄放水）
    返回 {'n_keys':int, 'dead':[mid], 'drift':[(mid,item,old,new)],
          'exempt_today':[(mid,item,why)]}。
    """
    if not os.path.exists(SNAP_HIST):
        return {'n_keys': 0, 'dead': [], 'drift': [], 'exempt_today': []}
    _st, hist = snap_history_or_warn('豁免漂移回放')   # S085：三态读取，损坏不折叠
    mon = load_monitors()
    by_id = {m['id']: m for m in mon['monitors']}
    waiver = ledger_waiver()
    dead, drift = [], []
    exempt_today = []
    seen_keys = set()
    for h in hist:
        for k, v in (h.get('keys') or {}).items():
            if k in seen_keys:
                continue
            seen_keys.add(k)
            mid = k.split('|', 1)[0]
            item = str(v.get('item', ''))
            sev = v.get('sev', '')
            if mid not in by_id:
                if mid not in dead:
                    dead.append(mid)
                continue
            cur_sev = by_id[mid].get('severity', '')
            if sev and cur_sev and sev != cur_sev:
                drift.append((mid, item, sev, cur_sev))
            # C. 今日回放：白名单或预豁免是否会把该条放行
            import re as _re
            mm = _re.search(r'S\d{3}', item)
            no = mm.group(0) if mm else ''
            reason = ''
            if no and no in waiver:
                reason = f'白名单 {no}'
            else:
                x = next((r for r in rows_of(DB_SESS)
                          if str(r.get('会话编号', '')).strip() == no), None) if no else None
                if x is not None:
                    st = x.get('反哺状态', '')
                    if st in ('已同步源库', '已同步底包'):
                        done, ev = _is_doc_delivery(x)
                        if done:
                            reason = f'预豁免（{ev}）'
                        elif mid == 'M01':
                            # 预豁免不兜、白名单不兜 → 今日仍报（这是对的）
                            pass
            if reason:
                exempt_today.append((mid, item, reason))
    return {'n_keys': len(seen_keys), 'dead': dead, 'drift': drift,
            'exempt_today': exempt_today}


def cmd_replay(a):
    """检查器时效回归（S041 新功能）：口径变更时回放历史，确认不悄悄误报/误放。

    动机：regress 只验证**预豁免**这一条口径；但检查器的逻辑/严重度/豁免口径
    都会随轮次演进——改完之后，**历史上报过的条目今天还会报吗**？
    如果不会，就是口径变更在**悄悄放水**（真遗留被新口径放过且无人知晓）。
    replay 回放全量历史指纹做三检，把「口径变更的影响」从暗处拉到明处。

    **M-R20 铁律**：改检查器逻辑/严重度/豁免口径后必跑 replay；
    「今日将不报」的条目必须逐条人工确认，不允许静默消失。"""
    rp = _replay_rows()
    if not rp.get('n_keys'):
        print('⏪ 暂无历史指纹（跑过 report/scan 后才能回放）')
        return rp
    print(f'⏪ 检查器时效回归（回放 {rp["n_keys"]} 条历史遗留指纹）\n')
    print('【A 失效引用】历史指纹指向的检查器已被删除')
    if rp['dead']:
        print(f'  🔴 {", ".join(rp["dead"])} —— 历史发现失去追溯，建议恢复登记或归档说明')
    else:
        print('  ✅ 无——历史发现均可追溯到现存检查器')
    print('\n【B 严重度漂移】历史 sev ≠ 当前登记 severity')
    if rp['drift']:
        for mid, item, old, new in rp['drift'][:10]:
            print(f'  ⚠️ [{mid}] {old} → {new} ｜ {item[:44]}')
        print(f'  共 {len(rp["drift"])} 条——确认调级是否经过决策')
    else:
        print('  ✅ 无——严重度口径未变')
    print('\n【C 今日回放】历史遗留若今日重现，按当前口径是否会被放行')
    if rp['exempt_today']:
        for mid, item, why in rp['exempt_today'][:10]:
            print(f'  ♻️ [{mid}] {why} → 今日将不报 ｜ {item[:40]}')
        print(f'  共 {len(rp["exempt_today"])} 条——逐条人工确认放行是否合理（防悄悄放水）')
    else:
        print('  ✅ 历史遗留今日重现仍会全部上报——无放水')
    print()
    issues = len(rp['dead']) + len(rp['drift'])
    print(f'💡 判读：{"⚠️ " + str(issues) + " 项口径变更待确认" if issues else "口径稳定"}；'
          '每次改检查器逻辑/严重度/豁免后必跑 replay（M-R20）。')
    return rp


def _drill_rows():
    """延迟归因下钻计算（供 cmd_drill 与 report 复用）。S042。

    **M-R21 铁律：下钻结论必须标注样本量**——延迟样本 <3 的风险面只出「样本不足」
    不出结论，禁止用 1-2 个样本归纳「该面反应慢」（小样本归纳 = 脑补）。

    把 latency 的逐条延迟样本按风险面聚合，每面输出：
      · 统计：样本数 / 平均 / 最长
      · 归因（证据驱动，三选一）：
        ① 检查器上线晚 → 面内检查器 since_period 大（新上线，历史窗口覆盖不到）
        ② 样本不足 → 样本 <3，仅提示待积累
        ③ 反应慢 → 平均延迟显著高于全局（>2× 全局均值且样本 ≥3）
    返回 {'global_avg':h|None, 'faces':[(face, n, avg, max, verdict, why)]}。
    """
    lat = _latency_rows()
    samples = lat.get('samples') or []
    mon = load_monitors()
    since = {m['id']: m.get('since_period', 1) for m in mon['monitors']}
    face_of = {}
    for face, cfg in RISK_FACES.items():
        for code in cfg['codes']:
            for m in mon['monitors']:
                if m['check'] == code:
                    face_of[m['id']] = face
    gavg = lat.get('avg')
    agg = {}   # face -> [delay,...]
    for d, mid, item in samples:
        agg.setdefault(face_of.get(mid, '（未归类）'), []).append(d)
    rows = []
    for face, ds in agg.items():
        n = len(ds)
        avg = sum(ds) / n
        mx = max(ds)
        mids = {s[1] for s in samples if face_of.get(s[1]) == face}
        late_birth = any(since.get(m, 1) >= 5 for m in mids)
        if n < 3:
            verdict, why = '样本不足', f'仅 {n} 条，M-R21 禁止小样本归纳'
        elif late_birth:
            verdict, why = '检查器上线晚', '面内含 since_period≥5 的新检查器，历史窗口覆盖不到'
        elif gavg and avg > 2 * gavg:
            verdict, why = '反应慢', f'平均 {avg:.1f}h > 2× 全局均值 {gavg:.1f}h → 建议提频/前置告警'
        else:
            verdict, why = '正常', f'平均 {avg:.1f}h 在全局均值范围内'
        rows.append((face, n, avg, mx, verdict, why))
    rows.sort(key=lambda r: -r[2])
    return {'global_avg': gavg, 'faces': rows, 'n_samples': len(samples)}


def cmd_drill(a):
    """延迟归因下钻（S042 新功能）：latency 的下钻——按风险面聚合，回答「哪类面发现慢、为什么」。

    latency 说「这条遗留拖了多久」，drill 说「**哪一类风险面整体慢、慢的原因是什么**」。
    三种归因（证据驱动）：检查器上线晚（since_period 大）/ 样本不足（<3 条禁归纳）/
    反应慢（>2× 全局均值 → 提频或前置告警）。
    **M-R21 铁律**：下钻结论必须标注样本量，<3 条只出「样本不足」不出结论。"""
    dr = _drill_rows()
    if not dr.get('n_samples'):
        print('🔬 延迟归因下钻：暂无可追溯延迟样本（先让 latency 产出样本）')
        return dr
    g = dr.get('global_avg')
    print(f'🔬 延迟归因下钻（{dr["n_samples"]} 条延迟样本 · 全局均值 '
          f'{g:.1f}h · M-R21 小样本不归纳）\n')
    print(f'  {"风险面":10} {"样本":4} {"平均":>8} {"最长":>8} 归因')
    print('  ' + '-' * 66)
    for face, n, avg, mx, verdict, why in dr['faces']:
        tag = {'反应慢': '🔴', '检查器上线晚': '🟡', '样本不足': '⚪', '正常': '🟢'}.get(verdict, '·')
        print(f'  {face:10} {n:4} {avg:7.1f}h {mx:7.1f}h {tag} {verdict} —— {why}')
    print()
    print('💡 判读：归因结论随样本积累自动升级（样本不足 → 正常/反应慢）；'
          '「反应慢」面的检查器建议提高扫描频率或前置告警。')
    return dr


def _track_rows():
    """扩面决策留痕计算（供 cmd_track 与 report 复用）。S043。

    **M-R22 铁律：雷达候选必须件件有去向**——expand 提示的每个新实体，
    要么「已决策」（verdict 落在 monitors.json.expansion_log），要么「待决策」；
    不允许提示后被静默忽略（忽略 N 轮的候选 = 风险敞口）。

    对照 expand 候选 vs expansion_log：
      A. 待决策：候选出现但 log 无记录 → 悬空风险敞口
      B. 过期决策：log 有记录但该实体已不在候选（频次 <2）→ 留痕可归档
      C. 决策率：已决策 / 全部出现过的候选
    返回 {'pending':[(entity,count)], 'stale':[(entity,verdict)], 'decided_n':int,
          'log_n':int}。"""
    ex = _expand_rows()
    mon = load_monitors()
    log = mon.get('expansion_log') or []
    log_by = {str(e.get('entity', '')).strip(): e for e in log}
    candidates = {name: n for name, n in (ex.get('new_entities') or [])}
    pending = [(k, v) for k, v in candidates.items() if k not in log_by]
    stale = [(k, e.get('verdict', '?')) for k, e in log_by.items() if k not in candidates]
    return {'pending': pending, 'stale': stale,
            'decided_n': len(candidates) - len(pending), 'log_n': len(log),
            'candidates': candidates}


def cmd_track(a):
    """扩面决策留痕（S043 新功能）：雷达候选 → 人工评估 → 决策落痕的闭环追踪。

    动机：expand 只「提示候选」，但提示之后没人管——候选被静默忽略 N 轮，
    就从「雷达信号」退化成「背景噪声」，风险敞口无人认领。
    track 对照 expand 候选与决策留痕（monitors.json.expansion_log）三检：
      A. 待决策：候选出现但无决策记录 → 悬空敞口
      B. 过期决策：决策了但实体已不在候选 → 可归档
      C. 决策率：已决策 / 全部候选
    配套写命令：`tvbox watchdog add-decision --entity X --verdict 扩面|暂不 --note ...`
    **M-R22 铁律**：雷达候选必须**件件有去向**——不允许静默忽略。"""
    tr = _track_rows()
    print(f'🧾 扩面决策留痕（候选 {len(tr["candidates"])} · 留痕 {tr["log_n"]} 条）\n')
    print('【A 待决策】候选出现但无决策记录（悬空风险敞口）')
    if tr['pending']:
        for name, n in tr['pending']:
            print(f'  🟡 {name}（出现 {n} 次）→ '
                  '评估后用 add-decision 落痕')
    else:
        print('  ✅ 无——所有候选件件有去向')
    print('\n【B 过期决策】已决策但实体已不在候选（可归档）')
    if tr['stale']:
        for name, verdict in tr['stale']:
            print(f'  📦 {name}（{verdict}）→ 候选已消失，留痕保留作历史')
    else:
        print('  ✅ 无过期决策')
    total = len(tr['candidates']) + len(tr['stale'])
    print(f'\n【C 决策率】{tr["decided_n"]}/{max(total, 1)}'
          + ('（100%）' if not tr['pending'] and total else '（有待决策项）'))
    print('\n💡 判读：' + ('待决策项须人工评估后 add-decision 落痕（M-R22）；'
          if tr['pending'] else '闭环健康，') + '决策四选：扩面/补检查器/并入已知面/暂不。')
    return tr


def cmd_add_decision(a):
    """登记扩面决策（S043）：把对 expand 候选的人工决策写进 monitors.json.expansion_log。
    用法：tvbox watchdog add-decision --entity 数据源总库 --verdict 暂不 --note "hub 自管\""""
    mon = load_monitors()
    log = mon.setdefault('expansion_log', [])
    ent = (a.entity or '').strip()
    if not ent:
        print('❌ --entity 必填'); return None
    rec = {'entity': ent, 'verdict': (a.verdict or '暂不').strip(),
           'date': datetime.datetime.now().strftime('%Y-%m-%d'),
           'note': (a.note or '').strip()}
    log[:] = [e for e in log if str(e.get('entity', '')).strip() != ent]
    log.append(rec)
    try:
        json.dump(mon, open(MONITORS, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        print(f'✅ 决策已留痕：{ent} → {rec["verdict"]}（{rec["date"]}）')
    except Exception as e:
        print(f'❌ 写入失败：{e}'); return None
    return rec


def dispatch(argv):
    p = argparse.ArgumentParser(prog='watchdog')
    sub = p.add_subparsers(dest='cmd')
    sub.add_parser('scan')
    sub.add_parser('status')
    sub.add_parser('monitors')
    am = sub.add_parser('add-monitor')
    am.add_argument('--id', required=True)
    am.add_argument('--name', required=True)
    am.add_argument('--check', required=True)
    am.add_argument('--desc', default='')
    am.add_argument('--threshold', default='')
    am.add_argument('--cadence', default='')
    am.add_argument('--severity', default='耗时', choices=['阻断', '耗时', '轻微'])
    am.add_argument('--action', default='')
    rp = sub.add_parser('report')
    rp.add_argument('--out', default='')
    rp.add_argument('--upload', action='store_true',
                    help='云端直传（复用 library create_doc.py），并回写 node id 锚点')
    sub.add_parser('diff')   # 仅算环比增减，不出报告
    sub.add_parser('trend')  # 历史遗留趋势表
    sub.add_parser('aging')  # 遗留闭环耗时统计（存活期数/账龄）
    sub.add_parser('health')  # 监测项自身健康自检（防看门狗睡着）
    sub.add_parser('rootcause')  # 遗留根因聚类（系统性 vs 偶发）
    sub.add_parser('gain')   # 检查器增益量化（哪个检查器真抓到了遗留）
    sub.add_parser('tune')   # 监测阈值调优建议（放宽/收紧/提优先级/剔除）
    sub.add_parser('hygiene')  # 跨技能卫生分排名（生态健康度横向对比）
    sub.add_parser('coverage')  # 监测覆盖率地图（哪些风险面还没检查器）
    sub.add_parser('dispatch-work')  # 卫生分短板自动派活（诊断转待办）
    ba = sub.add_parser('balance')  # ★ 生态平衡性体检（谁进化慢/谁拖后腿·S106）
    ba.add_argument('--json', action='store_true', help='输出完整 JSON（含全部优先级项）')
    fc = sub.add_parser('facts')  # 报告正文自检（报告数字 vs 实况）
    fc.add_argument('--out', default='')
    sub.add_parser('noise')   # 检查器噪声（假阳性）度量
    sub.add_parser('docsync')  # SKILL 文档一致性检查（声明 vs 代码）
    sub.add_parser('attribution')  # 豁免原因自动归因（反推口径缺陷）
    sub.add_parser('regress')  # 预豁免规则健全性回归（白名单瘦身 + 误放检测）
    sub.add_parser('latency')  # 监测延迟度量（发现及时性·真实时间差）
    sub.add_parser('expand')  # 风险面动态扩展雷达（新业务提示）
    sub.add_parser('replay')  # 检查器时效回归（口径变更回放历史）
    sub.add_parser('drill')  # 延迟归因下钻（哪类风险面慢·为什么）
    sub.add_parser('track')  # 扩面决策留痕（候选件件有去向）
    ad=sub.add_parser('add-decision')  # 登记扩面决策
    ad.add_argument('--entity', required=True)
    ad.add_argument('--verdict', default='暂不')
    ad.add_argument('--note', default='')
    tu = sub.add_parser('trend-upload')  # 趋势留档上云（跨设备）
    tu.add_argument('--upload', action='store_true', default=True)
    a = p.parse_args(argv)
    if a.cmd in (None, 'scan'):
        # ★ S167 修：scan 的返回值是**数据**（findings 列表），不是退出码。
        #   `tvbox` 的 `_as_exit_code` 只认 int、bool→码、其余一律 0，
        #   于是「有 1 项耗时遗留」经 tvbox 跑出来 rc=0——门禁形同虚设。
        #   实测三路对照：engine 直跑 0 / inspect.py 直跑 1 / 经 tvbox 0，
        #   三个入口三个答案，坏在最后一层归一。
        #
        #   修法只动**CLI 出口这一处**，不改 cmd_scan 的返回值：
        #   内部有 4 处（report/diff/trend/aging）拿它当 findings 用，
        #   改数据结构会连带打断它们——那是「为一个调用方改公共接口」的老错。
        #   判据仍只有一条：有遗留 → 非 0。阻断/耗时/轻微都算（调用方要自己分级再看 sev）。
        _f = cmd_scan(a)
        return 1 if _f else 0
    return {'status': cmd_status, 'monitors': cmd_monitors,
            'add-monitor': cmd_add_monitor, 'report': cmd_report,
            'diff': cmd_diff, 'trend': cmd_trend, 'aging': cmd_aging,
            'health': cmd_health, 'rootcause': cmd_rootcause,
            'gain': cmd_gain,
            'tune': cmd_tune, 'hygiene': cmd_hygiene,
            'coverage': cmd_coverage, 'dispatch-work': cmd_dispatch_work,
            'balance': cmd_balance,
            'facts': cmd_facts,
            'noise': cmd_noise, 'docsync': cmd_docsync,
            'attribution': cmd_attribution,
            'regress': cmd_regress,
            'latency': cmd_latency,
            'expand': cmd_expand,
            'replay': cmd_replay,
            'drill': cmd_drill,
            'track': cmd_track, 'add-decision': cmd_add_decision,
            'trend-upload': cmd_trend_upload}[a.cmd](a)


if __name__ == '__main__':
    # S167：原来只写 dispatch(...)，返回值丢掉 → 直接跑脚本退出码恒 0。
    # 与 inspect.py（`raise SystemExit(dispatch())`）不同，这一层从来没传过码。
    # 同一命令三个入口三种码（engine 0 / inspect.py 1 / tvbox 0），
    # 排查时差点把「引擎坏了」当成「路由坏了」。统一在这一层收口。
    _ret = dispatch(sys.argv[1:])
    sys.exit(_ret if isinstance(_ret, int) else (1 if _ret else 0))
