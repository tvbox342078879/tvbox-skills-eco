#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox 联动效率核心（技能↔脚本联动 + 并行加速 的运行时落点）

被 link.py / blk.py / evolve.py 共享导入 —— **仅同一进程内**是同一个模块实例（跨技能 CLI 单命令调用时各自独立实例，无跨进程复用，S123 措辞收窄），
同进程内缓存与并行池共享。这是「技能知识层(SKILL.md)↔脚本执行层(.py)联动」在
运行时的具体实现：调度器 tvbox 把多个命令放进同一进程 + 共享缓存，脚本因此跑得更快。

提供：
  rows_of(db)            读整表（进程内缓存，写后失效）
  api(script,*args)      调 library 脚本（进程内缓存）
  fanout(*tasks)         并发跑多个零参任务，保序返回（并行读核心）
  add_records(db,recs)   批量写（串行，保 QPS；每次写后失效该库缓存）
  add_records_parallel   （可选）限流并行写
  invalidate(db)         写后让缓存失效，防读到旧值
"""
import os, sys, json, csv, io, time, subprocess, threading

sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from concurrency import run_bounded as _run_bounded  # noqa: E402  M-R114 统一入口

LIB = '/root/.codebuddy/skills/library'
# B3：原仓库目录（env 可覆）。替代探源司脚本里硬编码的 /workspace/原仓库库/（T7），
# 统一收口到共享核心，避免再写死旧路径。
ORIG_DIR = os.environ.get('TVBOX_ORIG_DIR', '/workspace/原仓库库')
# S153 会话闸门状态落点：写在技能目录（可被 verify/探针检查），不写 /tmp（重启即失忆）
GATE_FILE = '/root/.codebuddy/skills/tvbox-source-hub/.session_gate.json'
GATE_TTL_SEC = 6 * 3600      # 开工登记有效期 6 小时：防「一次开工、无限放行」
_MEM = {}                 # 进程内缓存: db_id -> rows | ('api',script,args) -> stdout
_MEM_LOCK = threading.Lock()
_FAILED = set()           # S083·M-R60：读取失败的库 id（供调用方区分「读失败」与「库真空」）

# ---------- 源库坐标：**单一事实源**（S159 建立，S160 换库） ----------
# 事故：S144「阶段3 清空旧三库」把 `wW5xpbS663nA6XK8a7DNsp`（源总库）与
# `G9B1GzfRJDoSausMmPXF8T`（备用库）各 801 条清空并改名「🗑【已废弃】」，
# 但**只有 tvbox-inspect 一个脚本跟着改了读法**，其余 10 个脚本文件仍把旧坐标
# 写死在各自文件里。后果不是报错，而是**静默读到空表**：
#   · `tvbox sizes` → 「源库 0 | 备用 0」，assess ③活跃度据此判 0 分；
#   · `blk mark`（整域拉黑）→ http=[] → full=[] → 打印「无新增」，**整域拉黑整体失效**；
#   · `blk archive`（复活出清）→ 源库状态表为空 → 回流全部判「源库无此条」；
#   · `probe_src` / `newlib` / `run.py` → 测活与入库同样在空集上跑。
# 这些都不抛异常、不打警告，只输出「0 条」——**读失败被折叠成了合法结论**（M-R60）。
# 判据改法：坐标只在这里定义一次，其他脚本一律 `from core import ...`（M-R38）。
#   以后搬库只改这一处；谁再写死库 id，就是重新制造同一起事故。
#   ★S160 正是靠这条才敢搬库：只改本文件两行，10 个引用文件全部自动跟到新库。
SRC_DBS = [
    ('线路源库', 'pNwZ9uBOTz6FzRuvu8nryI'),
    ('成品源库', '8JBrhSwsJ3pnPYtoGLo9XV'),
]
# ---------- S160 重建：三库 → 两库（同一套语义，换了落点与粒度） ----------
# 旧三库（py线路源库/csp线路源库/成品源库）是**按技术形态切库**：py 一库、csp 一库、
# 实测成品一库。代价是三份字段定义各自为政（同名 `可用体系` 三种类型/三种选项），
# 且「一条线路到底该进哪个库」要靠 api 前缀猜。S160 按探源司标准重做为两库：
#
#   · 线路源库 620 条 ＝ 原 py 112 + 原 csp 508，**用 `源类型` 字段区分**（py线路/csp线路）
#     一条线路只存一份，不再靠"库"来编码技术形态（M-R103 互斥分区的更细实现）。
#     专属字段：`源类型`(py线路/csp线路)、`py引擎`、`py依赖`、`csp类名`、`jar依赖`
#
#   · 成品源库 109 条 ＝ 原「成品源库」沿用旧名（内容与语义都不变：真实测活通过的成品，
#     拿来即可放进用户 json）。★ 曾一度改名为「JSON库」，后按用户意见**改回「成品源库」**：
#     旧叫法大家顺手，且"成品"比"JSON"更能表达"已验收可用"这层含义。
#     写入方不变：`probe harvest promote` 从真实测活通过的源提升，**不由线路入库写**。
#     专属字段：`可进包`、`档位`；无 `体系枝`/`底包指纹`（那是线路溯源用的，成品不需要）
#
#   两库字段类型与选项已**完全统一**（`可用体系` 均 multi_select + A/B/C/D/🔞），
#   S160 迁移时按 `link.classify()` 重算而非照搬存量，故 729/729 满覆盖。
#
# 旧三库在 S160 **已清空并改名「🗑【已废弃】」**（729 条先归档到
# `backup_S160/旧三库_清空前终版快照.json` 再删）。它们不在 SRC_DBS 中：
#   · 旧 py  `2jgIs29ilgqIIFVU61cIyf`
#   · 旧 csp `LIksN7jSFuo4uWFaUSz2iz`
#   · 旧成品 `IGz9Q1jashp1JTTastBKGM`（注意：这是**旧**成品库；新成品库是 8JBrhSws…）
# 判据：**SRC_DBS 里没有的库，就是不该再被业务代码读写的库**。谁绕过它写死旧 id，
# 就是重新制造 S144「静默读到空表」那起事故（M-R60 / M-R38）。
DB_S160_OLD3 = {
    'py线路源库':  '2jgIs29ilgqIIFVU61cIyf',
    'csp线路源库': 'LIksN7jSFuo4uWFaUSz2iz',
    '成品源库':    'IGz9Q1jashp1JTTastBKGM',
}
# ---------- 旧三库语义（S160 前史，保留作迁移依据与踩坑回指） ----------
# py / csp 两库**从未有过任何 SKILL.md 定义**，语义只存在于口头。S159 前查
# 「这条线路该进哪个库」只能靠猜，是重复踩坑的源头。以下是当时的实测结论，
# 是 S160 合并两库的**依据**（并库时字段/条数就是照这个搬的）。
#   · 三库是**互斥分区**，不是备份（M-R103「一条线路只存在于一个分组」）：
#     api 集合两两交集 = 0（py∩csp = py∩成品 = csp∩成品 = 0）。**切勿当副本互相同步**。
#   · py线路源库   112 条 ＝ http 直链 104 + `./py/*` 本地路径 8
#   · csp线路源库  508 条，**全部 `csp_` 前缀**（508/508，零例外）
#   · 成品源库     109 条，全部 http（109/109），含 `真实测活`/`测活证据`
#     写入：**不由线路入库写**。由 `probe harvest promote` 从实测通过的源提升而来。
#           （历史坑：`link.py` 曾把 csp_ 源误灌进本库，S159 已修）
#   · `可用体系` 三库已于 S159 统一为 multi_select + A/B/C/D/🔞，覆盖 729/729。
#     判据统一由 `link.classify()` 给出（「后缀即体系」），代码从未写错——
#     空值是更早批次存量 + 字段定义三库各自为政的结果。
# S160 起：前三者合并为「线路源库」+「JSON库」，语义见上方 S160 段。
# 落库判据唯一实现在 `link.dest_db()`；本注释是说明，判据以代码为准（M-R38）。
DB_SRC_LEGACY = 'wW5xpbS663nA6XK8a7DNsp'   # 旧源总库：S144 已清空废弃，仅留作回指
DB_BAK_LEGACY = 'G9B1GzfRJDoSausMmPXF8T'   # 旧备用库：同上


def src_rows_t(_cache=True):
    """读「全部源」= 源库联合（S160 起为两库），三态返回 (ok, rows, info)。

    与 inspect_engine.src_rows() 同语义（那边从本模块导入坐标，避免两份定义）。
    info = {'ok':bool, 'total':int, 'by_db':{名:条数}, 'empty':[名...], 'failed':[名...]}
    铁律（M-R60）：任一库读失败 → ok=False，**不把失败库当空库**。
    """
    info = {'ok': True, 'total': 0, 'by_db': {}, 'empty': [], 'failed': []}
    out = []
    for name, db in SRC_DBS:
        ok, rows = rows_of_t(db, _cache=_cache)
        info['by_db'][name] = len(rows) if ok else None
        if not ok:
            info['ok'] = False
            info['failed'].append(name)
            continue
        if not rows:
            info['empty'].append(name)
        for r in rows:
            r = dict(r)
            r.setdefault('_src库', name)
            out.append(r)
    info['total'] = len(out)
    return info['ok'], out, info


def src_rows(_cache=True):
    """读全部源，失败返回 []（兼容旧签名）。需要区分失败与空库请用 src_rows_t。"""
    return src_rows_t(_cache=_cache)[1]


def src_where(field, value):
    """按字段值在源库中找到记录（先按「有该字段」过滤，避免遍历三库全表）。"""
    val = str(value).strip()
    return [r for r in src_rows() if str(r.get(field, '')).strip() == val]


def invalidate(db=None):
    """写后让缓存失效，避免读到旧值。db=None 时清空全部。

    坑（S020 实测复发，同症状第二次）：rows_of 经 api() 读整表，api() 的缓存键是
    ('api',script,args) 与 db_id 键是两套。只 pop db 键 → 写后读回命中 api 层
    旧缓存 → 读回验证谎报「未查到」（S019 落库实测：记录实际已在库）。
    故这里必须把 args 里含该 db_id 的 api 键一并清掉。"""
    with _MEM_LOCK:
        if db is None:
            _MEM.clear()
            _FAILED.clear()
            return
        _MEM.pop(db, None)
        _FAILED.discard(db)
        for k in [k for k in _MEM
                  if isinstance(k, tuple) and len(k) == 3 and k[0] == 'api'
                  and any(db in str(x) for x in k[2])]:
            _MEM.pop(k, None)


def api(script, *args, cwd=LIB, _cache=True):
    key = ('api', script, args)
    if _cache:
        with _MEM_LOCK:
            if key in _MEM:
                return _MEM[key]
    out = subprocess.run([sys.executable, f'{LIB}/{script}', *args],
                         capture_output=True, text=True, cwd=cwd).stdout
    if _cache:
        with _MEM_LOCK:
            _MEM[key] = out
    return out


def rows_of_t(db, _cache=True):
    """S083·M-R60：三态读整表 → (ok, rows)。
      · (True,  rows) —— 读到有效数据（rows 可能为空表=真·空库，ok 仍为 True）
      · (False, [])   —— **读取失败**（脚本报错/非 JSON/结构失配），**不得当空库**
    失败结果**不写缓存**（防一次失败毒化整进程后续所有读——旧版把 [] 缓存住，
    M-R60 的又一层放大：缓存固化假态）。
    这是 core.rows_of 的严格版；rows_of 保留旧签名（兼容 21 处调用），
    但失败时也会登记 _FAILED 并跳过缓存。"""
    if _cache:
        with _MEM_LOCK:
            if db in _MEM:
                return True, _MEM[db]
    out = api('database/get_database_content.py', '--database-id', db)
    try:
        d = json.loads(out)
        if 'content' not in d:
            raise ValueError('返回无 content 字段（脚本报错/结构失配）')
        val = list(csv.DictReader(io.StringIO(d['content'])))
    except Exception:
        with _MEM_LOCK:
            _FAILED.add(db)          # 登记失败，供 rows_of_failed() 查询
        return False, []             # 失败：不缓存，调用方须显式判断
    with _MEM_LOCK:
        _FAILED.discard(db)
    if _cache:
        with _MEM_LOCK:
            _MEM[db] = val
    return True, val


def rows_of_failed(db=None):
    """S083·M-R60：查询是否有库读取失败。db 省略则返回全部失败库 id 集合。"""
    with _MEM_LOCK:
        return set(_FAILED) if db is None else (db in _FAILED)


def rows_of(db, _cache=True):
    """读整表（兼容旧签名：失败返回 []）。**失败不缓存**（S083·M-R60 修复）。
    需要区分「读失败」与「空库」的调用方，请改用 rows_of_t(db) 或 rows_of_failed(db)。"""
    ok, val = rows_of_t(db, _cache=_cache)
    return val


def query_all(db, page_size=100, max_pages=50, fields=None):
    """★S157：分页读全表（含 record_id），三态返回 (ok, rows, info)。

    **为什么需要它**（S157 体检结论）：
      · `rows_of_t` 走的是 `get_database_content.py`——**只返回列内容，没有 record_id**，
        且**不分页**，超一页就静默截断。要改记录的调用方因此只能自己去调
        `query_database_record.py`；
      · 实测全生态有 **15 个文件**各自手写这套调用，**其中只有 eco-ledger 一处分了页**，
        其余 14 处全是「单页 + 自己解析 JSON + 自己判失败」——
        同一个"读库"动作 15 份实现，这就是"S157 共享逻辑"最该收口的地方。

    与 `rows_of_t` 的分工：
      · `rows_of_t`  —— 只要数据、不要 record_id、表不超过一页 → 用这个（快，走 content 接口）
      · `query_all`  —— 需要 record_id（改/删记录）、或表可能超过一页 → 用这个

    三态铁律（M-R60）：
      · (True,  rows, '')            —— 读成功（rows 可能是空表＝真·空库）
      · (False, [],   '原因')        —— **读失败**，调用方**不得当空库**
    分页安全（借鉴 probe-hub S110 踩过的坑）：
      · 游标不前进即退出——参数写错时服务端不报错、每次返回首页，循环会**死转**；
      · max_pages 兜底，超限按"读失败"返回（宁可说读不到，不可返回半张表当全表）。

    fields: 可选，只取这些字段（透传 --fields），减少传输量。
    """
    script = os.path.join(LIB, 'database', 'query_database_record.py')
    if not os.path.exists(script):
        return False, [], f'query_database_record.py 缺失（{script}）——无法读表'
    rows, cur, pages = [], None, 0
    while True:
        cmd = [sys.executable, script, '--database-id', db,
               '--page-size', str(page_size)]
        if fields:
            cmd += ['--fields', json.dumps(fields, ensure_ascii=False)
                    if not isinstance(fields, str) else fields]
        if cur:
            cmd += ['--start-cursor', cur]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=LIB, timeout=60)
        except Exception as e:
            return False, [], f'读表子进程异常（{type(e).__name__}: {str(e)[:60]}）'
        if r.returncode != 0:
            return False, [], f'读表退出码 {r.returncode}：{(r.stderr or r.stdout or "")[:80]}'
        try:
            d = json.loads(r.stdout or '{}')
        except Exception:
            return False, [], f'读表返回非 JSON：{(r.stdout or "")[:60]}'
        if 'results' not in d:
            return False, [], f'读表返回无 results 字段（脚本报错/结构失配）：{str(d)[:80]}'
        rows.extend(d.get('results') or [])
        nc = d.get('next_cursor')
        # 游标不前进即退出：防"参数写错→服务端每次返回首页→死转"（probe-hub S110 实证）
        if not d.get('has_more') or not nc or nc == cur:
            return True, rows, ''
        cur = nc
        pages += 1
        if pages >= max_pages:
            return False, [], (f'分页超过 {max_pages} 页仍未结束（已读 {len(rows)} 条）——'
                               f'按读失败处理，勿当全表')


# 云端 node id 形态：16–32 位 base62（实测如 tBlgSL4SPEFMrsyTBTg03c = 22 位）
_NODE_ID_RE = __import__('re').compile(r'^[A-Za-z0-9_-]{16,32}$')


def file_to_janitor(node_id, reason='', quiet=True):
    """★ 生态联动门面（S104）：把"AI 删不掉的节点"投递进清道夫收件箱。

    任何技能 import core 后即可一行调用（清理是旁路，永不阻断主流程）：
        core.file_to_janitor(nid, '建库失败残留节点')

    - 失败不抛异常（返回 False），仅打印告警（quiet=True 时完全静默）。
    - 收件箱：tvbox-janitor「🗑 待删收件箱」，用户一处按单删。

    **S156 修：入参必须是真 node_id，否则一律拒收并告警。**
    事故：`blk.py` 长期调用 `file_to_janitor(f'源总库·整域拉黑孤儿 host={h}', ...)`
    ——传的是**描述文字**，不是节点 id。后果链（三重静默，无人察觉）：
      ① `node-info` 查不到 → janitor 把标题写成「（节点不可读）」；
      ② `move-node` 必然失败 → **节点从未进入收件箱**；
      ③ 但 janitor 仍把它**登记进清单** → 清单显示 40 项，收件箱实际 18 个。
    **看起来在跑、有调用、有记录，核心动作一次都没成功**——典型的名义联动。
    更根本的问题：「拉黑孤儿」是**数据库记录**，压根不是云端节点，
    它该走数据库删除/登记欠账，不该走清道夫（清道夫只管云端节点）。
    """
    nid = str(node_id or '').strip()
    if not _NODE_ID_RE.match(nid):
        # 不是 node id 就别记假账——记了只会让清单虚高、掩盖"没投递"这件事实。
        # quiet 也拦不住：**参数错了是调用方的 bug，必须先让人看见**。
        print(f'  ❌ 清道夫投递被拒：入参不是云端 node_id → {nid[:70]!r}')
        print('     （node_id 形如 tBlgSL4SPEFMrsyTBTg03c；数据库记录/描述文字请走数据库删除或登记欠账）')
        return False
    try:
        JAN = '/root/.codebuddy/skills/tvbox-janitor/scripts'
        if JAN not in sys.path:
            sys.path.insert(0, JAN)
        import janitor
        return bool(janitor.auto_file(node_id, reason, quiet=quiet))
    except Exception as e:
        if not quiet:
            print(f'  ⚠️ 清道夫联动失败（{type(e).__name__}）：节点 {node_id} 未投递，请人工删')
        return False


def register_monitor(risk_face, check_type, desc='', quiet=True):
    """★ 生态联动门面（S105）：把"新风险面"登记进监察司监测册。

    任何技能 import core 后即可一行调用（登记是旁路，永不阻断主流程）：
        core.register_monitor('废弃节点堆积', 'naming_compliance', '库根下废弃节点超阈值即报')

    - 失败不抛异常（返回 False），仅打印告警（quiet=True 时完全静默）。
    - 加监测项只改 monitors.json 登记册不改引擎代码；未实现的 check 类型允许先登记后补。
    - 门面：tvbox-inspect（监察司）「monitors.json 登记册」，新风险自动被接手。
    """
    try:
        INS = '/root/.codebuddy/skills/tvbox-inspect/scripts'
        if INS not in sys.path:
            sys.path.insert(0, INS)
        import inspect_engine
        return bool(inspect_engine.register_monitor(risk_face, check_type, desc, quiet=quiet))
    except Exception as e:
        if not quiet:
            print(f'  ⚠️ 监察司联动失败（{type(e).__name__}）：风险面「{risk_face}」未登记，请手动 tvbox inspect add-monitor')
        return False


def fanout(*tasks):
    """并发执行若干零参可调用，按入参顺序返回结果。空/单任务退化为直接调用。

    ★ M-R114：走统一入口，归 D 档（本地/无网络，上限=min(核数,8)）。
      注意 D 档上限在 4 核机器上是 4，旧实现写死 10 会超档——这是有意的收紧。
    """
    if not tasks:
        return []
    if len(tasks) == 1:
        return [tasks[0]()]
    rep = _run_bounded(list(tasks), lambda f: f(), tier='D', label='fanout',
                       on_error='raise', keep_order=True)
    return rep.results


def _read_json(p, default=None):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default if default is not None else None


def _write_json(p, obj):
    """原子写：先写临时文件再 os.replace 替换，避免写途中进程被杀导致半截 JSON（T3）。"""
    tmp = p + '.tmp_core'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def http_get(url, timeout=15, ua=None, max_bytes=None, as_text=True,
             headers=None, method='GET', data=None, ssl_verify=True,
             return_status=False, err_body=False):
    """统一 HTTP 取数入口（M-R38 单一事实源 / M-R213 共享核心）。

    带 SSRF 防护（拦截私有/环回/链路本地/保留/组播地址，含 169.254.169.254 元数据）、
    超时、三态返回。纯新增函数，零影响现有调用（core 原无 http 能力）。
    返回 (status, payload, reason)：
      ok      -> ('ok', bytes/str 或 (status, payload), None)
      blocked -> ('blocked', None, 'ssrf')   # 命中内网/元数据
      error   -> ('error', None, 'dns'|'scheme'|'timeout'|'conn'|'http_<code>'|'unknown')
    新增参数（向后兼容，不传=与原行为一致）：
      headers       追加/覆盖请求头（如 Referer），与默认 UA 合并
      method/data   支持非 GET 请求
      ssl_verify    False 时跳过证书校验（spider_verify 等内网自签场景）
      return_status True 时 payload 改为 (http_status, payload) 元组
      err_body      True 时 HTTP 错误（非 2xx）payload 改为 (code, 错误正文)，
                    供调用方保留服务端排障信息（默认不读错误体，行为不变）
    """
    import ipaddress
    import socket
    import ssl as _ssl
    import urllib.request
    import urllib.error
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return ('error', None, 'scheme')
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        try:
            infos = socket.getaddrinfo(host, port)
        except Exception:
            return ('error', None, 'dns')
        for info in infos:
            ip = info[4][0]
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if (addr.is_private or addr.is_loopback or addr.is_link_local
                    or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
                return ('blocked', None, 'ssrf')
        hdrs = {'User-Agent': ua or 'Mozilla/5.0 (TVBox-core)'}
        if headers:
            hdrs.update(headers)
        ctx = None
        if parsed.scheme == 'https' and not ssl_verify:
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            status = getattr(resp, 'status', 200)
            data = resp.read() if max_bytes is None else resp.read(max_bytes)
        if as_text:
            try:
                data = data.decode('utf-8', 'replace')
            except Exception:
                pass
        payload = (status, data) if return_status else data
        return ('ok', payload, None)
    except urllib.error.HTTPError as e:
        if err_body:
            try:
                _b = e.read()
                if as_text:
                    _b = _b.decode('utf-8', 'replace')
            except Exception:
                _b = None
            return ('error', (e.code, _b), 'http_%d' % e.code)
        return ('error', None, 'http_%d' % e.code)
    except urllib.error.URLError as e:
        if isinstance(getattr(e, 'reason', None), socket.timeout):
            return ('error', None, 'timeout')
        return ('error', None, 'conn')
    except Exception:
        return ('error', None, 'unknown')


def session_open(intent='', _file=None):
    """登记「本次会话已开工」。由 `tvbox conductor ask` 调用（唯一正门）。"""
    st = {'opened_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
          'pid': os.getpid(), 'intent': str(intent)[:200],
          'writes': 0, 'bypassed': 0}
    ok = _write_json(_file or GATE_FILE, st)
    return ok


def session_close(_file=None):
    """收工：把开工状态归档为「已收尾」，新一轮须重新 ask。"""
    p = _file or GATE_FILE
    st = _read_json(p)
    if st is None:
        return False
    st['closed_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    return _write_json(p, st)


def session_state(_file=None):
    """读会话闸门状态。三态（M-R60）：文件读不到要显式报「无法判定」，**绝不当通过**。"""
    p = _file or GATE_FILE
    st = _read_json(p)
    if st is None:
        return {'ok': None, 'state': 'unknown',
                'reason': f'闸门状态文件不可读（{p}）——**无法判定**，勿当已开工（M-R60）'}
    age_sec = None
    try:
        age_sec = time.time() - os.path.getmtime(p)
    except Exception:
        pass
    if age_sec is not None and age_sec > GATE_TTL_SEC:
        return {'ok': False, 'state': 'expired', 'age': int(age_sec),
                'reason': f'开工登记已过期（{int(age_sec)}s > TTL {GATE_TTL_SEC}s）——'
                          f'新任务须重新跑 `tvbox conductor ask`（防一次开工无限放行）'}
    return {'ok': True, 'state': 'open', 'intent': st.get('intent', ''),
            'opened_at': st.get('opened_at', ''), 'writes': st.get('writes', 0)}


def gate_check(_file=None, _force=False):
    """写库闸：返回 (放行?, 说明)。

    **为何设在这里（S153）**：所有写库最终都汇流到本模块的 add_records / update_records，
    在此设卡＝一处拦住全生态写操作。若只在某个命令里提示，和我绕开 brief 的教训
    是同一个：**提示靠自觉，闸门靠机制**。
    """
    if _force or os.environ.get('TVBOX_GATE_BYPASS') == '1':
        _bump_bypass(_file)
        return True, '（闸门已旁路 —— 本次写入被计数留痕，用于事后追责）'
    st = session_state(_file)
    if st['ok'] is None:
        return False, st['reason']
    if st['state'] == 'expired':
        return False, st['reason']
    _bump_write(_file)
    return True, f"会话已开工登记（{st.get('opened_at','')}）"


def _bump(key, _file=None):
    p = _file or GATE_FILE
    st = _read_json(p)
    if st is None:
        return
    st[key] = int(st.get(key, 0)) + 1
    _write_json(p, st)


def _bump_write(_file=None):
    _bump('writes', _file)


def _bump_bypass(_file=None):
    _bump('bypassed', _file)


def add_records(db, recs, sleep=0.4, _gate=True, _force=False):
    """批量写（串行，保 QPS）。写后失效该库缓存。返回 (ok,fail)。

    S153 会话闸门：默认 `_gate=True` —— 未跑 `tvbox conductor ask` 开工登记即写库时
    **直接拒绝**（不做提示了事）。这是我们刚付过的学费：开工三条快捷命令写在
    `tvbox brief` 输出里，照样被绕过去手写脚本；把提醒升级成机制才拦得住。
    逃逸舱：`_force=True` 或环境变量 TVBOX_GATE_BYPASS=1，但**会被计数留痕**。
    """
    if _gate:
        ok_pass, why = gate_check(_force=_force)
        if not ok_pass:
            print('🚫 会话闸门拦截：本次写库被拒绝')
            print(f'   原因：{why}')
            print('   放行方式：tvbox conductor ask "<你的需求>"   （登记开工 + 自动预载历史经验）')
            print('   确需直接写：传 _force=True 或设 TVBOX_GATE_BYPASS=1（会被计数留痕）')
            return 0, len(recs)
    ok = fail = 0
    for i in range(0, len(recs), 100):
        b = recs[i:i + 100]
        out = subprocess.run([sys.executable, f'{LIB}/database/batch_add_database_records.py',
                              '--database-id', db, '--records', json.dumps(b, ensure_ascii=False)],
                             capture_output=True, text=True, cwd=LIB).stdout
        try:
            d = json.loads(out)
            for x in d.get('results', []):
                ok += 1 if x.get('success') else 0
                fail += 0 if x.get('success') else 1
        except Exception:
            fail += len(b)
        time.sleep(sleep)
    invalidate(db)
    return ok, fail


def add_records_diag(db, recs, sleep=0.4, _gate=True, _force=False):
    """同 add_records，但返回 (ok, fail, info)。info 非空 = 失败原因（服务端原文）。

    S158：`add_records` 只回 (ok, fail)，调用方想知道"为什么失败"就只能**自己再拼一遍
    subprocess + json**——那正是协议被绕开的起点（link.py 的 cmd_liblog/cmd_session
    就是这么长出来的第二套解析）。这里把诊断信息补上，让调用方不必重造轮子。

    info 的三种取值，与 update_records 对齐（三态，不折叠）：
      · ''            —— 全部成功
      · '服务端拒绝' / '返回非 JSON：...' / '返回无 results 字段：...'
      · '闸门拦截：...'（会话闸门未登记开工）
    注意：**info 非空不等于"一条都没写进去"**——部分成功时 fail>0 且 info 记最后一条原因。
    """
    if _gate:
        ok_pass, why = gate_check(_force=_force)
        if not ok_pass:
            return 0, len(recs), f'闸门拦截：{why}'
    ok = fail = 0
    info = ''
    for i in range(0, len(recs), 100):
        b = recs[i:i + 100]
        out = subprocess.run([sys.executable, f'{LIB}/database/batch_add_database_records.py',
                              '--database-id', db, '--records', json.dumps(b, ensure_ascii=False)],
                             capture_output=True, text=True, cwd=LIB).stdout
        good, flags, why = _parse_results(out)
        if not good:
            fail += len(b)
            # S158：优先透出服务端 error 原文——`_parse_results` 只会说
            # "返回无 results 字段"，人看不出是服务端拒绝还是协议变了。
            # ★ 此处原本写 `except: _d = None` 然后 `_d.get('error')`——
            #   **那是折叠**：None(解析不出) 与 {}(解析出但无 error 键) 走了同一条路，
            #   人分不清"服务端没回话"与"服务端回了但没给原因"。现在显式区分三分支。
            try:
                _d = json.loads(out or '{}')
            except Exception:
                _d = None
            if _d is None:
                info = f'返回不是合法 JSON（无法取得服务端原因）：{(out or "")[:120]}'
            elif isinstance(_d, dict) and _d.get('error'):
                info = f'服务端拒绝：{_d["error"]}'
            else:
                info = why
        else:
            ok += sum(1 for s in flags if s)
            fail += sum(1 for s in flags if not s)
            if fail and not info:
                info = f'返回 results 有 {fail} 条 success!=true：{out[:160]}'
        time.sleep(sleep)
    invalidate(db)
    return ok, fail, info


def add_records_parallel(db, recs, workers=3, sleep=0.3):
    """限流并行写（可选）：workers 并发批，写后失效缓存。返回 (ok,fail)。"""
    ok = fail = 0
    lock = threading.Lock()

    def do_batch(b):
        nonlocal ok, fail
        out = subprocess.run([sys.executable, f'{LIB}/database/batch_add_database_records.py',
                              '--database-id', db, '--records', json.dumps(b, ensure_ascii=False)],
                             capture_output=True, text=True, cwd=LIB).stdout
        bo = bf = 0
        try:
            d = json.loads(out)
            for x in d.get('results', []):
                bo += 1 if x.get('success') else 0
                bf += 0 if x.get('success') else 1
        except Exception:
            bf += len(b)
        with lock:
            ok += bo
            fail += bf
        time.sleep(sleep)

    batches = [recs[i:i + 100] for i in range(0, len(recs), 100)] or [[]]
    # ★ M-R114：写库归 A 档（上限 8），并把本批 record_id 登记为写集合，
    #   与在跑任务重叠时直接 raise——本次事故正是两个任务同时写同一批行。
    ids = {str(r.get('record_id') or r.get('id') or '') for r in recs} - {''}
    _run_bounded(batches, do_batch, tier='A', workers=workers,
                 label=f'add_records_parallel·{db}', write_set=ids)
    invalidate(db)
    return ok, fail


# ══════════════════════════════════════════════════════════════════════════
# 资料库读写统一协议（S158·技能↔资料库 + 技能↔大生态）
#
# 病根（S158 体检实测）：全生态 25 处自己写 `subprocess.run([... library/...])`，
# **其中 15 处没有三态返回**——读失败时返回 None / [] / ''，调用方无从区分
# 「库读不到」和「库是空的」。M-R60 早就立了铁律，但它只落在 core.rows_of_t
# 一家身上，绕过 core 的人根本没继承这条约定。
#
# 本段做的不是"新增一个工具"，而是**把协议默认化**：
#   · 所有读写一律返回 (ok, payload, info) 三元组，失败必须显式 False；
#   · 写操作强制过会话闸门（与 add_records 同一条闸门，不另设后门）；
#   · 写后必回读（T-I「success 不等于落库」）——回读不过就报失败，不报"成功"。
#
# 为什么放在 core 而不是新开模块：core 已被 19 个脚本 import，是事实上唯一的
# 共享入口（S127 P006 归一）。新开模块等于又造一个"要记得去用"的东西——
# 那正是 L123「工具做好却接不到入口上」的复发路径。
# ══════════════════════════════════════════════════════════════════════════

def _run_lib(script, *args, timeout=120):
    """调 library 脚本，统一返回 (ok, stdout, err)。**这是唯一允许的调用姿势。**

    绕过它的直接后果见本段开头：15 处无三态实现各自发明了失败表示法。
    """
    path = os.path.join(LIB, script)
    if not os.path.exists(path):
        return False, '', f'library 脚本缺失：{script}（预期 {path}）'
    try:
        r = subprocess.run([sys.executable, path, *args],
                           capture_output=True, text=True, cwd=LIB, timeout=timeout)
    except Exception as e:
        return False, '', f'子进程异常（{type(e).__name__}: {str(e)[:70]}）'
    if r.returncode != 0:
        return False, r.stdout or '', (f'退出码 {r.returncode}：'
                                       f'{(r.stderr or r.stdout or "")[:100]}')
    return True, r.stdout or '', ''


def _parse_results(out):
    """解析 batch_*_database_records.py 的 {"results":[{success:bool}...]}。

    三态：能解析且结构对 → (True, [success 布尔列表])；否则 (False, [], 原因)。
    绝不在解析失败时返回"全成功"——那是最危险的折叠。
    """
    try:
        d = json.loads(out or '{}')
    except Exception:
        return False, [], f'返回非 JSON：{(out or "")[:80]}'
    if not isinstance(d, dict) or 'results' not in d:
        return False, [], f'返回无 results 字段：{(out or "")[:80]}'
    res = d['results']
    if not isinstance(res, list):
        return False, [], 'results 不是列表'
    return True, [bool(x.get('success')) for x in res if isinstance(x, dict)], ''


def update_records(db, records, _gate=True, _force=False):
    """批量更新记录。records = [{"record_id": ..., "properties": {...}}, ...]

    返回 (ok, n_ok, info)：
      · (True,  n,  '')      —— 全部成功，n == len(records)
      · (False, n,  '原因')  —— 有失败/解析不了/闸门拦了；**n 是实际确认成功的条数**

    为什么不是 add_records 那样的 (ok, fail)：更新有 3 种结局（全成/部分成/全败），
    两元组表达不了"部分成功"这种最常见的情况。调用方拿到 False 时仍能从 n 知道
    落地了几条，避免"报失败就整批重写"导致重复更新。

    ★ properties 必须是 {字段名: {"text"/"number"/...: 值}} 的结构，
      直接传 {字段名: 值} 会被框架判为空（S158 实测踩过：传 `{"version":"1.68.0"}`
      报 "properties 校验后为空"）。这里做一次误用检测并给出可直接照抄的提示。
    """
    if not records:
        return True, 0, ''
    # 误用检测：值不是 dict → 极可能漏了类型键
    for i, r in enumerate(records):
        props = r.get('properties')
        if isinstance(props, dict):
            bad = [k for k, v in props.items() if not isinstance(v, dict)]
            if bad:
                return False, 0, (f'records[{i}].properties 的字段 {bad[:3]} 值不是对象——'
                                  f'须写成 {{"字段名": {{"text": "值"}}}}，'
                                  f'否则框架判"校验后为空"。这是 S158 实测踩过的坑')
    if _gate:
        ok_pass, why = gate_check(_force=_force)
        if not ok_pass:
            print('🚫 会话闸门拦截：本次更新被拒绝')
            print(f'   原因：{why}')
            print('   放行方式：tvbox conductor ask "<你的需求>"')
            return False, 0, f'闸门未放行：{why}'
    ok, out, err = _run_lib('database/batch_update_database_records.py',
                            '--database-id', db,
                            '--records', json.dumps(records, ensure_ascii=False))
    if not ok:
        return False, 0, err
    parsed, flags, perr = _parse_results(out)
    if not parsed:
        return False, 0, perr
    n = sum(1 for f in flags if f)
    invalidate(db)                       # 写后失效缓存（不论成败，数据可能已部分变）
    if n != len(records):
        return False, n, f'仅 {n}/{len(records)} 条成功（明细见 library 返回）'
    return True, n, ''


def delete_records(db, record_ids, _gate=True, _force=False):
    """批量删除记录。返回 (ok, n_ok, info)，语义同 update_records。

    ★ 删除是不可逆的，故这里比 update 更严：**调用方必须显式说明删哪些**，
      不接受"条件删"（条件删容易因为一次误判条件把整表清空——S144 清空三库那次
      就是这么发生的）。要按条件删，先用 query_all 查出来，再逐条给 id。
    """
    ids = [str(x) for x in (record_ids or []) if str(x).strip()]
    if not ids:
        return True, 0, ''
    if _gate:
        ok_pass, why = gate_check(_force=_force)
        if not ok_pass:
            print('🚫 会话闸门拦截：本次删除被拒绝')
            print(f'   原因：{why}')
            return False, 0, f'闸门未放行：{why}'
    ok, out, err = _run_lib('database/batch_delete_database_records.py',
                            '--database-id', db,
                            '--record-ids', json.dumps(ids, ensure_ascii=False))
    if not ok:
        return False, 0, err
    parsed, flags, perr = _parse_results(out)
    if not parsed:
        return False, 0, perr
    n = sum(1 for f in flags if f)
    invalidate(db)
    if n != len(ids):
        return False, n, f'仅 {n}/{len(ids)} 条删除成功'
    return True, n, ''


def write_and_verify(db, records, key_field=None, _gate=True, _force=False):
    """**写后必回读**（T-I）：写入 → 回读该库 → 确认记录真在里面。返回 (ok, n, info)。

    为什么需要它：`batch_add` 返回 success=true **不等于落库**——S083 实测过
    写完立刻回读却查不到（写入异步落盘）。只在"success"这一层验收，
    就是拿响应体当事实，属于最典型的一类假绿。

    key_field：用哪个字段判定"这条已落库"。给了就按字段值比，没给就比总行数是否增加。
    """
    before_ok, before_rows, berr = query_all(db)
    if not before_ok:
        return False, 0, f'写前回读失败，无法建立基线：{berr}（**不可当作从 0 开始**）'
    base = len(before_rows)
    ok, fail = add_records(db, records, _gate=_gate, _force=_force)
    after_ok, after_rows, aerr = query_all(db)
    if not after_ok:
        return False, ok, f'写后回读失败：{aerr}（写了 {ok} 条但**未确认落库**，须人工核对）'
    grew = len(after_rows) - base
    if key_field:
        keys = {str(r.get(key_field) or '') for r in records} - {''}
        have = {str((r.get('fields') or r).get(key_field) or '') for r in after_rows}
        miss = keys - have
        if miss:
            return False, len(keys) - len(miss), (f'回读未见 {len(miss)} 条'
                                                  f'（样例 {list(miss)[:3]}）——写入未落地')
        return True, len(keys), ''
    if grew < ok:
        return False, grew, (f'写入报成功 {ok} 条，回读只增 {grew} 条——'
                             f'部分未落地（M-R60：不得当成功）')
    return True, grew, ''
