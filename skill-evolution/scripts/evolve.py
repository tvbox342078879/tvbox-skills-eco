#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
技能自我进化引擎（规则 48 落地）：监控复发问题 → 自动立项生成可升级技能

子命令:
  recurring [--min N]    扫描 ③会话台账 + ④资料库经验库，按「问题签名」聚类，
                         列出复发次数 ≥ N 的候选（应技能化）。默认 N=3
  create <name>          脚手架式生成新技能（SKILL.md 模板 + scripts/ + CHANGELOG + 扩展点）
         --trigger "..." 触发词（必填，让引擎未来能自动命中）
         --desc    "..." 一句话描述（必填）
  review                 对比当前规则数与上次快照（本地），输出「新增/待整理」提示
  note --what "..."      把一次进化动作写进 evolution_log.jsonl（本地留痕）

③ 会话台账 TO4LwsjVVdR8JRAUlkejk3 ｜ ④ 资料库经验库 XTuuj8mKtqnZSolDdeDxkq
"""
import os,sys,json,csv,io,time,argparse,subprocess,re,shutil
from collections import Counter,defaultdict

# 共享效率核心（S123 收窄：仅同一进程内与 link/blk 共享 core 模块实例；跨技能 CLI 单命令调用时各自独立实例）
sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from core import (rows_of, add_records, add_records_parallel,
                  invalidate, file_to_janitor, register_monitor)  # noqa: E402

# ★ 生态联动（S106 补接门面）：
#   · 技能升级后旧版快照/被取代的文档节点 AI 删不掉 → core.file_to_janitor 投递清道夫
#   · 进化过程发现新退化模式（新弱点类别）→ core.register_monitor 登记监察司，纳入下轮扫描
def _eco_linked_probe():
    """自证已接门面：返回 (清道夫, 监察司) 可达性，供 inspect 联动覆盖判据与自检。"""
    return (callable(file_to_janitor), callable(register_monitor))

LIB='/root/.codebuddy/skills/library'
DB_SESS='TO4LwsjVVdR8JRAUlkejk3'   # ③ 业务经验
DB_LIB='XTuuj8mKtqnZSolDdeDxkq'    # ④ 平台经验
# M-R108（S154）：用户偏好持久指令档——recurring 不只聚类「问题」，也要聚类「用户偏好」
STANDING_JSON='/root/.codebuddy/skills/ecosys-conductor/scripts/user_standing_instructions.json'
DB_ECO='T4kODtANe7rSrtZTu4sIND'    # 🧩 TVBox技能生态登记库
SKILLS='/root/.codebuddy/skills'
SELF=os.path.dirname(os.path.abspath(__file__))
ROOT=os.path.dirname(SELF)
LOG=os.path.join(ROOT,'evolution_log.jsonl')
SNAP=os.path.join(ROOT,'rule_count_snapshot.json')

# ══════════ S107 经验对照·招式库（规则57）══════════
# 定位：生态"第四极·方法极"——跨技能传递可复用的「打法」。
# 载体：云端 DB（可被 inspect/conductor 三态读 + 按出处反查 + 与 recurring 聚类解耦）。
MOVE_IDS=os.path.join(SELF,'move_ids.json')

def _move_db():
    """读招式库 id（失败返回 ''）。"""
    try:
        return json.load(open(MOVE_IDS,encoding='utf-8')).get('db','')
    except Exception:
        return ''

# 种子招式表（先验知识，可增长）：识别跨≥2技能复用的通用招式。
# 每条 = {名, 判据关键词(正则), 适用条件}。新招式靠 discover_new_moves 挖候选 → 人工确认后补入。
SEED_MOVES={
 'T-A':{'name':'三态契约（读失败≠空态/损坏≠首期）',
        'kw':[r'三态',r'读失败',r'折叠',r'空库',r'首期',r'损坏',r'M-R60',r'M-R57'],
        'cond':'读外部数据基座/DB/文件时'},
 'T-B':{'name':'物理搬迁+薄壳import（替代复制）',
        'kw':[r'物理搬迁',r'薄壳',r'搬迁',r'import\s*复用',r'替代复制',r'禁重复实现'],
        'cond':'跨技能共享逻辑时'},
 'T-C':{'name':'白名单用「文件:内容指纹(sha1前10)」做键',
        'kw':[r'sha1',r'指纹',r'白名单.{0,6}键',r'行号.{0,4}漂移',r'稳定指纹'],
        'cond':'维护白名单/豁免表时'},
 'T-D':{'name':'移动替代删除（无删除API时降维）',
        'kw':[r'移动.{0,4}删除',r'无.{0,6}删除.{0,4}API',r'降维',r'move.{0,4}替代',r'只移不删'],
        'cond':'目标无删除能力时'},
 'T-E':{'name':'活体探针（注入已知异常+假阳性基线）',
        'kw':[r'活体探针',r'注入.{0,8}捕获',r'假阳性基线',r'探针',r'清零'],
        'cond':'新增/修改检测器时'},
 'T-F':{'name':'机械量化替代人工估算',
        'kw':[r'卫生分',r'量化',r'评分',r'指数',r'五维',r'aging',r'机械.{0,4}量化'],
        'cond':'评估/体检类指标时'},
 'T-G':{'name':'单一事实源 SSOT（常量收敛，使用点派生）',
        'kw':[r'单一事实源',r'SSOT',r'收敛',r'派生',r'事实源',r'散落'],
        'cond':'同一常量被多处引用时'},
 'T-H':{'name':'「装健康的哑巴」检测（检测器静默失效）',
        'kw':[r'装健康的哑巴',r'静默失效',r'假绿',r'检测器.{0,6}失效',r'哑巴'],
        'cond':'自检检测器有效性时'},
 'T-I':{'name':'写路径「写后回读验证」（写了≠写成了）',
        'kw':[r'回读验证',r'写后.{0,4}读',r'伪成功',r'写.{0,4}不等于.{0,4}写成',r'record_id',r'静默失败',r'成功标志',r'success\b',
              # ★ S182：原 8 条只认"回读/伪成功"这类词，漏掉了**最常见的真落地形态**——
              #   写函数自己返回 saved/ok 布尔、失败时拒绝覆盖并保留现场。
              #   tvbox-inspect 的 save_snapshot 与 legacy-watchdog 的 `saved, _ = save_snapshot(...)`
              #   就是这个形态，却因一词不匹配被判「纯空转」。招式判据不许只认自己的黑话。
              r'saved\s*,',r'不得谎报',r'拒绝覆盖',r'写失败',r'落盘失败',
              r'回读.{0,6}不一致',r'回读.{0,4}校验'],
        'cond':'任何写库/写文件/写云端后'},
}

# 防噪：这些技能自身讲"招式/进化"定义 → 自指噪声，不参与扫描
MOVE_NOISE={'skill-evolution'}
# 防噪：upgrade 自动追加的空段落（非真招式证据）
MOVE_EXCLUDE_RE=[r'由\s*skill-evolution\s*upgrade\s*自动提升']

# ---------- recurring：复发问题聚类 ----------
STOP=set('的 了 是 在 我 你 他 它 与 和 或 及 对 从 到 把 被 这 那 该 此 一 条 个 类 类目 字段 记录 接口 工具 操作 报错 失败 成功 写入 读取 更新 删除 新增 生成 返回 校验 后 前 中 上 下'.split())

def kw(text):
    text=(text or '').lower()
    toks=re.findall(r'[a-z][a-z0-9_]{1,}|[\u4e00-\u9fff]{2,4}', text)
    out=[]
    for t in toks:
        if t in STOP: continue
        if re.fullmatch(r'[a-z0-9_]+',t) and len(t)<3: continue
        out.append(t)
    return out

def cmd_recurring(a):
    mn=a.min
    sess_raw=rows_of(DB_SESS); lib_raw=rows_of(DB_LIB)
    # ★ S159 已解决归档：recurring 的目的是「找出还在反复发生的坑」。
    #   若已标记「已解决/已固化」的记录仍参与聚类，同一个坑会被永久反复报警，
    #   阈值越堆越高、真正的新问题反而被淹没——经验池从「资产」退化成「噪声源」。
    #   故先过滤再聚类，并把过滤数量**显式打印**（不能静默少算，否则看数的人
    #   不知道分母变了；M-R60 精神：口径变化必须可见）。
    #   字段缺失一律视为「未解决」——宁可多报也不漏报，不拿「没填」当「已解决」。
    DONE = ('已解决', '已固化')
    def _open(r, fld):
        return str(r.get(fld) or '').strip() not in DONE
    sess=[r for r in sess_raw if _open(r, '问题状态')]
    lib =[r for r in lib_raw  if _open(r, '状态')]
    _ds, _dl = len(sess_raw)-len(sess), len(lib_raw)-len(lib)
    print(f'扫描 ③台账 {len(sess_raw)} 条 + ④经验库 {len(lib_raw)} 条 ｜ 阈值 ≥{mn}')
    print(f'  已归档过滤：③ {_ds} 条 / ④ {_dl} 条（标「已解决/已固化」的不再参与聚类）')
    print(f'  实际参与：③ {len(sess)} 条 + ④ {len(lib)} 条\n')
    # 两类分组键
    by_type=Counter()
    for r in lib: by_type[('④',r.get('操作类型',''))]+=1
    for r in sess: by_type[('③',r.get('触发类型',''))]+=1
    # 关键词全局计数（跨库）
    kwc=Counter()
    for r in lib:
        sig=' '.join([r.get('场景',''),r.get('坑',''),r.get('可复用点','')])
        for k in set(kw(sig)): kwc[k]+=1
    for r in sess:
        sig=' '.join([r.get('任务摘要',''),r.get('踩坑',''),r.get('可复用点','')])
        for k in set(kw(sig)): kwc[k]+=1
    # 输出1：按类型聚类
    cand_type=[(k,v) for k,v in by_type.items() if v>=mn]
    print('【按「问题类型」聚类——可直接立项】')
    if cand_type:
        for (src,typ),v in sorted(cand_type,key=lambda x:-x[1]):
            print(f'  {v:2}×  [{src}] {typ}')
    else:
        print('  （暂无类型达到阈值）')
    # 输出2：按关键词聚类
    cand_kw=[(k,v) for k,v in kwc.items() if v>=mn]
    print('\n【按「关键词」聚类——相似问题归并候选】')
    if cand_kw:
        for k,v in sorted(cand_kw,key=lambda x:-x[1]):
            print(f'  {v:2}×  {k}')
    else:
        print('  （暂无关键词达到阈值）')
    # 输出3：已固化途径 · 直达（规则52：复发问题先查途径再动手，禁止重新试错）
    # **S155 修：这里原来是三重嵌套 + 零去重**，同一条记录会被它所属的每一个
    # 「类型组」和每一个「关键词组」各打印一遍——一条经验含 10 个关键词就被打印 10 次。
    # 实测：两库合计 228 条 → 输出 4089 条（均值 18 倍），命令基本没法看。
    # 修法：**按 (来源, 记录号) 去重，一条只出一次**，并把命中的组聚成标签带在后面
    # （信息不减：既能看到它属于哪些聚类，又不用把全文抄 18 遍）。
    print('\n【已固化途径 · 直达（规则52）】')
    if not (cand_type or cand_kw):
        print('  （无命中，无需查途径）')
    else:
        hits = {}          # (src,rid) -> {'tags':[...], 'rec':row}
        # 类型面：字段精确相等即归属该组
        for (src, typ), v in cand_type:
            fld = '操作类型' if src == '④' else '触发类型'
            idf = '编号' if src == '④' else '会话编号'
            rows = lib if src == '④' else sess
            for r in rows:
                if str(r.get(fld) or '') == typ:
                    e = hits.setdefault((src, str(r.get(idf) or '?')), {'tags': [], 'rec': r})
                    if typ not in e['tags']:
                        e['tags'].append(typ)
        # 关键词面：命中关键词的记录同样归入该组
        for k, v in cand_kw:
            for src, rows, key_fields, idf in (('④', lib, ('场景', '坑', '可复用点'), '编号'),
                                               ('③', sess, ('任务摘要', '踩坑', '可复用点'), '会话编号')):
                for r in rows:
                    sig = ' '.join(str(r.get(f) or '') for f in key_fields)
                    if k in sig:
                        e = hits.setdefault((src, str(r.get(idf) or '?')), {'tags': [], 'rec': r})
                        if k not in e['tags']:
                            e['tags'].append(k)
        shown = 0
        for (src, rid), e in sorted(hits.items(), key=lambda x: -len(x[1]['tags'])):
            r = e['rec']
            fix = str(r.get('解法') or '').strip()
            reuse = str(r.get('可复用点') or '').strip()
            if not fix and not reuse:
                continue
            scene = r.get('场景') if src == '④' else r.get('任务摘要')
            tags = ' · '.join(sorted(e['tags'])[:4]) + ('…' if len(e['tags']) > 4 else '')
            print(f'  > [{src}] {rid}《{str(scene)[:40]}》  ⟵ {tags}')
            if fix:   print(f'      解法: {fix[:100]}')
            if reuse: print(f'      可复用: {reuse[:100]}')
            shown += 1
        if not shown:
            print('  （命中的聚类暂无已固化途径——按规则52，本次解决后必须 lib-log 补录）')
        else:
            print(f'  （去重后 {shown} 条；每条后 ⟵ 标注其所属聚类组）')
    print('\n→ 规则52直达：命中项先按上方已固化途径执行；确无途径才 evolve.py create <name> --trigger "..." --desc "..."')
    print('→ 问题已根治的，用 `evolve.py solved <编号...>` 归档，避免下次又被聚类报警')
    # 输出4：用户偏好·持久指令（M-R108：recurring 不只聚类『问题』，也要聚类『用户偏好』）
    print('\n【用户偏好·持久指令（M-R108）】')
    cmd_standing()

def cmd_solved(a):
    """把经验/台账记录标记为「已解决/已固化」，使其退出 recurring 聚类（S159）。

    为什么需要这一步：recurring 是按「同类问题出现次数」报警的，但它没法知道
    **这个坑早就治好了**。没有归档动作，历史条目会一直把计数顶在阈值以上，
    真正的新问题反而被埋在旧账里。归档 = 让经验池只统计「还欠着的」。

    用法：
      evolve.py solved L139 L140            → ④经验库按「编号」标记
      evolve.py solved S159 --ledger         → ③台账按「会话编号」标记
      evolve.py solved L139 --state 已固化    → 指定状态（默认「已解决」）
      evolve.py solved --list                → 只列已归档的，不改动
    """
    ids = list(a.ids or [])
    state = getattr(a, 'state', '已解决') or '已解决'
    if state not in ('已解决', '已固化'):
        print(f'⛔ 状态只能是「已解决」或「已固化」，收到「{state}」')
        return 2
    ledger = bool(getattr(a, 'ledger', False))
    db   = DB_SESS if ledger else DB_LIB
    idf  = '会话编号' if ledger else '编号'
    sfld = '问题状态' if ledger else '状态'

    if getattr(a, 'list', False):
        rows = rows_of(db, _cache=False)
        done = [r for r in rows if str(r.get(sfld) or '').strip() in ('已解决', '已固化')]
        print(f'📦 已归档 {len(done)} / {len(rows)} 条（{"③台账" if ledger else "④经验库"}）')
        for r in done:
            scene = str(r.get('场景') or r.get('任务摘要') or '')[:46]
            print(f'  {r.get(idf)}  [{r.get(sfld)}]  {scene}')
        return 0

    if not ids:
        print('用法: evolve.py solved <编号...> [--ledger] [--state 已固化] | --list')
        return 2

    rows = rows_of(db, _cache=False)
    have = {str(r.get(idf) or ''): r for r in rows}
    payload, miss = [], []
    for i in ids:
        if str(i) not in have:
            miss.append(i); continue
        # rows_of 会剥掉 record_id（只留业务字段），必须按编号反查云端 record_id
        rid = get_id(db, idf, str(i))
        if not rid:
            miss.append(i); continue
        payload.append({'record_id': rid, 'properties': {sfld: {'select': state}}})
    if miss:
        print(f'⚠️ 未找到编号：{", ".join(miss)}（{"台账" if ledger else "经验库"}里查无此号，未改动）')
    if not payload:
        return 3
    # S158：改走 core.update_records —— 它同时看 returncode 与 results[i].success，
    #   并且带 properties 结构误用检测（值不是 {type: v} 时直接给出可照抄的提示）。
    #   下面的"写后回读"逻辑**原样保留**：update_records 的 ok 仍只是"接口说成了"，
    #   而本函数的历史结论是「不信 batch_update 的自报」（见下方 S159 长注释）。
    # ★ S158 修·M-R60：原先这里写的是 `except Exception: ok = 0`——
    #   那是把「调用通路坏了」折叠成「成功了 0 条」，下游会照常打印
    #   「api 自报成功 0 条」，等于替失败编了个看起来正常的数字。
    #   改三态：ok=None 表示**未取得自报数**，输出必须显式说"未取得"，不许打印 0。
    ok = None
    _ur_info = ''
    try:
        import core as _ce
        _ur_ok, ok, _ur_info = _ce.update_records(db, payload)
        if not _ur_ok:
            print(f'   ⚠️ 批量更新未全部确认：{_ur_info[:140]}')
    except Exception as e:
        ok = None
        _ur_info = f'{type(e).__name__}: {str(e)[:70]}'
        print(f'   ⚠️ 批量更新不可用（自报数未取得）：{_ur_info}')
    # 写后回读：不信 batch_update 的自报，按编号逐个确认（绕缓存，见 L140）。
    # ★ 坑（S159 现场踩到）：rows_of/查询接口对**空值字段是「整个键不返回」**，
    #   不是返回 ''。所以判断"未生效"必须用 `i not in have` 之外再结合
    #   「键存在且等值」——直接 `.get(sfld)` 得到 None 会与"字段不存在"混为一谈，
    #   导致「明明写成功了却报未生效」。这里改用原始查询（保留全部业务键）复核。
    # **S159 修·M-R60**：原写法 `except: backstate = {}` 会把「回读失败」
    # 折叠成「回读成功但一条都没读到」，后果是**全部 N 条都被判「未生效」**——
    # 报出一份详尽、具体、却完全虚假的失败清单（每条都带「回读状态仍为（字段为空）」）。
    # 这比不报更糟：人会照着这份清单去重写一遍已经成功的数据。
    # 三态改法：读失败 → backstate=None → 不逐条判、不给结论、改报「无法判定」。
    good = []
    backstate = None
    _err = ''
    try:
        q = f'{LIB}/database/query_database_record.py'
        raw = subprocess.run(['python3', q, '--database-id', db, '--page-size', '200'],
                             capture_output=True, text=True).stdout
        rs = json.loads(raw or '{}').get('results') or []
        backstate = {str(r.get(idf) or ''): r.get(sfld) for r in rs}
    except Exception as e:
        backstate = None
        _err = f'{type(e).__name__}: {e}'
    if backstate is None:
        print(f'📦 归档结果：**无法判定**（回读失败 {_err}）')
        _self_rep = f'{ok}' if ok is not None else '（自报数也未取得）'
        print(f'   api 自报成功 {_self_rep}/{len(ids)} 条，但回读校验没做成 → 不要重跑，先修回读通路')
        return 4
    good = [i for i in ids if str(backstate.get(str(i)) or '') == state]
    _self_rep = f'{ok}' if ok is not None else '（未取得）'
    print(f'📦 归档结果：{len(good)}/{len(ids)} 条 → 「{state}」（api 自报成功 {_self_rep} 条）')
    for i in ids:
        cur = backstate.get(str(i))
        if str(cur or '') != state:
            print(f'  🔴 {i} 回读状态仍为「{cur if cur is not None else "（字段为空）"}」——未生效')
    return 0 if len(good) == len(ids) else 3

def cmd_standing(a=None):
    """用户偏好固化清单（M-R108）：列出持久指令及固化状态；会话开工核对用。

    与 recurring 的「问题聚类」互补——用户反复要求的偏好（如『会话保持中文』）
    过去从未进入③/④，recurring 只聚类问题不聚类偏好，导致同一要求反复重提却从不固化。
    本命令直接读 user_standing_instructions.json，给出待固化/已固化清单与下一步。
    """
    import os as _os
    if not _os.path.exists(STANDING_JSON):
        print('  （无持久指令档 user_standing_instructions.json —— 用户偏好尚未登记）')
        return 0
    d = json.load(open(STANDING_JSON, encoding='utf-8'))
    insts = d.get('instructions', [])
    print(f'  📌 持久用户指令（归 M-R108）共 {len(insts)} 条：')
    for it in insts:
        solid = str(it.get('status', '')).startswith('solidified')
        flag = '✅' if solid else '⏳'
        print(f'  {flag} [{it.get("id")}] {it.get("instruction")}')
        print(f'       重复要求 {it.get("requested_count")} 次 ｜ 固化于 {it.get("solidified_via")}'
              f' ｜ 开工核对={it.get("check_at_session_start")} ｜ 云端补登={it.get("cloud_ledger_due", "-")}')
    if not insts:
        print('  （空 —— 用户偏好未登记；一旦用户重复≥3次提出，应即时写入本档）')
    return 0

# ---------- create：脚手架生成可升级技能 ----------
SKILL_TPL="""---
name: {name}
description: "{desc}（可升级技能·规则48自动生成骨架）。触发词：{trigger}。当用户提到 {trigger} 时触发。"
version: "1.0.0"
---

# {name}

> 本技能由 `skill-evolution` 按规则 48 自动脚手架生成（{date}）。
> **可升级契约**：顶部 version + CHANGELOG.md；预留下方「扩展点」；自身遵循规则 48 持续进化。

## 1. 用途
{desc}

## 2. 触发词
{trigger}

## 3. 用法
（在此补充具体命令 / 流程）

## 4. 扩展点
- [ ] 待补充 subcommand
- [ ] 待补充配置项
- [ ] 待补充新规则位

## 5. 联动
- 进化留痕：skill-evolution/evolution_log.jsonl
- 平台经验回写：④ 资料库经验库（建库建夹类）
"""

def cmd_create(a):
    name=a.name
    d=os.path.join(SKILLS,name)
    if os.path.exists(d):
        print(f'⚠️ 技能已存在：{d}'); return
    os.makedirs(os.path.join(d,'scripts'),exist_ok=True)
    now=time.strftime('%Y-%m-%d',time.gmtime())
    open(os.path.join(d,'SKILL.md'),'w',encoding='utf-8').write(
        SKILL_TPL.format(name=name,desc=a.desc,trigger=a.trigger,date=now))
    open(os.path.join(d,'CHANGELOG.md'),'w',encoding='utf-8').write(
        f'# {name} CHANGELOG\n\n## {now} v1.0.0\n- 由 skill-evolution 按规则 48 自动脚手架生成\n')
    open(os.path.join(d,'scripts','__init__.py'),'w',encoding='utf-8').write('')
    print(f'✅ 已生成可升级技能骨架：{d}')
    print(f'   SKILL.md / CHANGELOG.md / scripts/ 已就绪')
    print(f'   下一步：补充用法与扩展点，并在 ③台账记「规则维护」+ ④经验库补「建库建夹」')
    register_eco(name, a.trigger, a.desc, intro=a.desc)
    # ★ S156：register_eco 之后**自动**对齐账房版本户（原为手动跑 ledger sync-versions）。
    #   不 import ledger（避免技能间硬依赖）：按文件路径惰性加载，失败只告警。
    _ledger_sync_after_register(name)
    note(f'create {name}: {a.desc}')

# ---------- review：规则数对比 ----------
def cmd_review(a):
    # 从 RULES.md 当前全文（本地缓存优先）统计规则数
    cnt=None
    p='/tmp/rules_now.xml'
    if os.path.exists(p):
        s=open(p,encoding='utf-8').read()
        cnt=len(re.findall(r'规则 \d+[:：]', s))
    print('规则 48 进化引擎 · review')
    if cnt: print(f'  当前 RULES.md 规则数（本地快照）：{cnt}')
    else:   print('  无本地 RULES 快照，请先拉取')
    if os.path.exists(SNAP):
        prev=json.load(open(SNAP,encoding='utf-8'))
        print(f'  上次快照：{prev.get("count")} 条（{prev.get("ts")}）')
        if cnt and cnt!=prev.get('count'):
            print(f'  ⚠️ 较上次 +{cnt-prev["count"]} 条，建议触发周期性系统整理（去重/简化）')
    else:
        print('  首次运行，已建立快照基准')
        if cnt: json.dump({'count':cnt,'ts':time.strftime('%Y-%m-%d %H:%M',time.gmtime())},
                           open(SNAP,'w',encoding='utf-8'))

# ---------- note：进化留痕 ----------
def note(what):
    rec={'ts':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'what':what}
    with open(LOG,'a',encoding='utf-8') as f: f.write(json.dumps(rec,ensure_ascii=False)+'\n')
    print(f'📝 进化留痕：{what}')

def cmd_note(a): note(a.what)

def _count_trigger_terms(desc):
    """数 description 里**真能抽出**的触发词个数（S148）。

    与 conductor._skill_desc_terms **同口径**（M-R38 单一事实源的精神：两处判定不得各写一套）：
      句式① `触发词：a / b / c`（遇 。；换行截断）
      句式② `当用户(提到|说) X 时触发`
    合格词：2–14 字，且不含 `→·—…：:` 等标点。
    为什么必须真抽：旧判据只看字样，写「当用户提到…时触发」就能过，
    而那句话里的长句按标点切分后**全部超过 14 字被丢弃**——实际一个词都路由不到。
    """
    if not desc:
        return 0
    d = desc.replace('\n', ' ')
    # ★ S176b：与 conductor._skill_desc_terms 同步放宽前缀（M-R38 单一事实源，
    #   两处判定不得各写一套）。原只认 `触发词：`，漏掉 `关键词：` / `触发关键词：`——
    #   实测 automation-task-manager（6 词）、class-activity-planner（7 词）被判 0 触发词，
    #   而这 13 个词其实写得完全合格。改判据，不是改技能。
    m = re.search(r'(?:触发关键词|触发词|关键词)[：:]\s*([^。；\n]+)', d)
    if m:
        parts = re.split(r'\s*[/、，,]\s*', m.group(1))
    else:
        m = re.search(r'当用户(?:提到|说)\s*(.+?)\s*(?:时触发|时)', d)
        if not m:
            return 0
        parts = re.split(r'\s*[、，,/]\s*', m.group(1))
    n = 0
    for x in parts:
        x = x.strip(' 「」『』*★（()）')
        if 2 <= len(x) <= 14 and not re.search(r'[→·—…：:]', x):
            n += 1
    return n


# ---------- 生态注册：把技能写进 🧩 生态登记库 ----------
def parse_fm(path):
    s=open(path,encoding='utf-8').read()
    m=re.match(r'^---\s*\n(.*?)\n---', s, re.S)
    if not m: return {}
    fm={}
    for ln in m.group(1).split('\n'):
        if ':' in ln:
            k,v=ln.split(':',1); fm[k.strip()]=v.strip().strip('"')
    return fm

def get_id(db,name_field,name_val):
    """按某文本字段取 record_id（get_database_content 无 id，需 query）"""
    cur=None; guard=0
    while True:
        cmd=['python3',f'{LIB}/database/query_database_record.py','--database-id',db,
             '--page-size','100']
        if cur: cmd+=['--start-cursor',cur]
        d=json.loads(subprocess.run(cmd,capture_output=True,text=True).stdout)
        for r in d.get('results',[]):
            if (r.get(name_field) or '')==name_val: return r['record_id']
        if not d.get('has_more'): break
        cur=d.get('next_cursor'); guard+=1
        if guard>25: break
    return None

def _ledger_sync_after_register(name, version='1.0.0'):
    """新建/升级技能后，让账房把「版本·X」账户对齐（S156 backlog ③ 落地）。

    为何放这里而不是账房主动轮询：登记是**evolve 的事件**，账房不知道何时发生；
    事件驱动才不漏。加载失败只告警——不对齐版本户是记账问题，不该阻断建技能。
    """
    fp = '/root/.codebuddy/skills/eco-ledger/scripts/ledger.py'
    try:
        import importlib.util as _u
        spec = _u.spec_from_file_location('_eco_ledger_hook', fp)
        m = _u.module_from_spec(spec)
        spec.loader.exec_module(m)
        ok, msg = m.after_register(name, version)
        print(f'  {"✅" if ok else "⚠️"} 账房版本户：{msg}')
    except Exception as e:
        print(f'  ⚠️ 账房钩子不可用（{type(e).__name__}: {str(e)[:80]}）——请手动跑 tvbox ledger sync-versions')


def register_eco(name,trigger,desc,version='1.0.0',state='活跃',upgrade='是',intro=''):
    """新建/更新技能时自动登记进生态库（已存在→更新版本号，保持同步）"""
    now=time.strftime('%Y-%m-%dT%H:%M:00Z',time.gmtime())
    exist=rows_of(DB_ECO)
    rec={k:v for k,v in {
        'version':{'text':version},'末次升级':{'date':now},
        '状态':{'select':state},'可升级':{'select':upgrade},
        '触发词':{'text':trigger},'简介':{'text':intro or desc}}.items()}
    hit=[r for r in exist if r.get('技能名')==name]
    if hit:
        rid=get_id(DB_ECO,'技能名',name)
        if rid:
            # S158：改走 core.update_records（统一三态；不再"解析失败一律当失败不做声"）
            try:
                import core as _ce
                _uok, _un, _uinfo = _ce.update_records(
                    DB_ECO, [{'record_id': rid, 'properties': rec}])
            except Exception as e:
                _uok, _uinfo = False, f'{type(e).__name__}: {str(e)[:60]}'
            if _uok:
                print(f'✅ 生态库已同步 {name} → v{version}')
            else:
                print(f'   ↪ 生态库更新 {name} **未确认**：{_uinfo[:120]}')
                print(f'      台账可能已滞后（M-R40）——下次 sync-version 会重试，但本次不算完成')
        return 1      # S177：原裸 return→rc0。语义「本次不算完成」，须显式非零
    full={'技能名':{'text':name},'路径':{'text':os.path.join(SKILLS,name)},
          '输入':{'text':'见 SKILL.md'},'输出':{'text':'见 SKILL.md'},'依赖':{'text':'library'}}
    full.update(rec)
    ok,fail=add_records(DB_ECO,[full])
    if ok: print(f'✅ 已登记进生态库：{name} (v{version})')

# ---------- scan：升级就绪度体检 ----------
def cmd_scan(a):
    print('🔍 技能升级就绪度体检（规则 48+54 可升级契约·三件套）\n')
    # S136（M-R38）：技能全集**统一走 _all_skills()**。
    # 此处原是 _all_skills 的一份**重复实现**（同样的前缀白名单），
    # 两份各自演化 → 新增 unfinished-audit 后 _all_skills 已含它、cmd_scan 仍扫 9 个。
    # 同型缺陷在 FLOWS（inspect）那里也存在过：不是不会写，是**写了两遍**。
    dirs=_all_skills()
    print(f'扫描 {len(dirs)} 个技能（生态库登记 ∪ 本地自建）：\n')
    tot_ready=0
    no_script=[]   # S069：契约齐备但无机器可执行层的「停滞技能」
    for d in dirs:
        sk=os.path.join(SKILLS,d,'SKILL.md')
        if not os.path.exists(sk):
            print(f'  ⚠️ {d:28} 无 SKILL.md（未成形）'); continue
        fm=parse_fm(sk)
        ver=fm.get('version','(无)')
        has_cl=os.path.exists(os.path.join(SKILLS,d,'CHANGELOG.md'))
        trig=fm.get('description','')
        # ★ S148：旧判据 `('触发词' in trig) or ('触发' in 全文)` 是**冒绿**——
        #   ① 4 个技能写的是「当用户提到…时触发」，不含「触发词」三字，靠 or 分支蒙过；
        #   ② 更糟的是 `'触发' in ' '.join(fm.values())` 会把**任意字段**里出现"触发"
        #      都算数（连 "触发词没写" 这种说明都能过），等于没有判据。
        #   实测：这 4 个里 skill-evolution 只能抽出 2 个词，真去路由一句话都接不住。
        #   改为**真抽一次词**：≥3 个合格触发词才算 ✓（2–14 字、不含标点，与 conductor 同口径）。
        has_trig=(_count_trigger_terms(trig) >= 3)
        has_flow=(os.path.exists(os.path.join(SKILLS,d,'flow.json'))
                  or os.path.exists(os.path.join(SKILLS,d,'scripts','flow.json')))
        score=sum([bool(ver!='(无)'),has_cl,has_trig,has_flow])
        ready='✅就绪' if score==4 else ('⚠️部分' if score>=1 else '❌否')
        # S069 第五维：机器可执行层（M-R45「契约齐备≠能力达标」的自动检测）
        # —— scripts/ 下有非 __init__ 的 .py = 有可执行脚本；无则标「纯文档」。
        # 注意：不改变 ready 字面（下游 conductor._sig_health 以「✅就绪/❌未就绪」为解析锚点）
        sdir=os.path.join(SKILLS,d,'scripts')
        py=[f for f in (os.listdir(sdir) if os.path.isdir(sdir) else [])
            if f.endswith('.py') and f!='__init__.py']
        has_exec=bool(py)
        exec_mark=f'脚本{len(py)}个✓' if has_exec else '脚本✗(纯文档)'
        if score==4:
            tot_ready+=1
            if not has_exec:
                no_script.append(d)
        print(f"  {ready} {d:26} v{ver:7} 版本{'✓' if ver!='(无)' else '✗'}"
              f"  CHANGELOG{'✓' if has_cl else '✗'} 触发词{'✓' if has_trig else '✗'}"
              f"  flow.json{'✓' if has_flow else '✗'}  {exec_mark}")
    print(f'\n就绪 {tot_ready}/{len(dirs)}（规则54 三件套：version+CHANGELOG+flow.json+触发词）'
          f' ｜ 未就绪者用：evolve.py upgrade <name> 补齐契约')
    # S069：把「契约齐备但无脚本」的停滞技能显式标出（M-R45 机制化）
    if no_script:
        print(f'\n⚠️ 契约齐备但**无机器可执行层**（S069·M-R45）：{", ".join(no_script)}')
        print('   → 这类技能三件套齐全却停滞在「纯文档 SOP」，每次执行靠手工重做；')
        print('     修法：把文档里最机械的步骤脚本化（如 interface 补 pipeline.py），而非只改版本号。')
    else:
        print('\n✅ 所有就绪技能均具备机器可执行层（scripts/*.py）')

# ---------- upgrade：真正做版本提升 + CHANGELOG ----------
def bump(v):
    try:
        a,b,c=[int(x) for x in v.strip('"').split('.')]
        return f'{a}.{b}.{c+1}'
    except Exception:
        return '1.0.1'

# ══════════ S107 经验对照·招式库（规则57）══════════
# 三命令：mine（抽取通用招式）/ lend（查可借鉴）/ review-lend（对账漏项）
# 原则：只诊断不自动改——mine 只写招式库且新候选不自动入库；lend 只出建议；违反→遗留项。

def _eco_registry_names():
    """生态技能正本清单（S136·M-R38）：读 eco_skills.json，与 conductor/inspect 同源。

    原先 _all_skills 的本地兜底只认 `tvbox-` 前缀 + 两个硬编码名字，
    新增不带前缀的技能（如 unfinished-audit）会**扫不到**：
      · evolve scan 显示 9 个技能（实际 10 个）
      · conductor 信号源7 立刻报「技能清单漂移」（这是它抓对了）
      · 但更深一层的后果是招式库/进化台账/平衡性全部看不到新技能
    改为读正本：加技能只需 conductor onboard（M-R78），这里自动跟上。
    """
    fp = os.path.join(SKILLS, 'ecosys-conductor', 'scripts', 'eco_skills.json')
    try:
        return {n for n in json.load(open(fp, encoding='utf-8'))['eco_skills'] if n != 'library'}
    except Exception:
        return set()


def _all_skills():
    """技能全集（生态库 ∪ 本地正本清单 ∪ tvbox- 自建）——与 cmd_scan 同口径（M-R38）。"""
    names=set()
    try:
        for r in rows_of(DB_ECO):
            n=r.get('技能名','')
            if n and n!='library': names.add(n)
    except Exception: pass
    names |= _eco_registry_names()          # S136：正本清单（含无前缀技能）
    for d in os.listdir(SKILLS):
        if d.startswith('tvbox-') or d in ('skill-evolution','legacy-watchdog'):
            if os.path.exists(os.path.join(SKILLS,d,'SKILL.md')):
                names.add(d)
    # 只保留真实存在 SKILL.md 的（正本清单里可能有已移除技能，scan 会给「未就绪」）
    return sorted(n for n in names if os.path.exists(os.path.join(SKILLS,n,'SKILL.md')))


def _changelog(skill):
    p=os.path.join(SKILLS,skill,'CHANGELOG.md')
    try:
        return open(p,encoding='utf-8').read()
    except Exception:
        return ''


def _segments(text):
    """按 '## ' 开头切段（抗噪音第一道闸：招式判据只在段内匹配，不跨段串味）。"""
    if not text:
        return []
    parts=re.split(r'(?m)^##\s+', text)
    out=['## '+p for p in parts[1:]]     # parts[0] 是文件头，非版本段
    return out


def _seg_head(seg, n=46):
    return seg.split('\n',1)[0][:n].strip()


def _seg_excluded(seg):
    return any(re.search(p, seg) for p in MOVE_EXCLUDE_RE)


def _match_moves(seg):
    """一段可命中多招（真实 CHANGELOG 常在一段里同时讲多个招式）——
    返回命中招式号列表。去重靠"跨技能 set"，不靠"一段一招"（后者会漏招，S107 实测：
    T-B/D/H 因同段含 T-A 关键词被 break 吞掉）。"""
    hits=[]
    for tid, spec in SEED_MOVES.items():
        for k in spec['kw']:
            if re.search(k, seg, re.I):
                hits.append(tid)
                break
    return hits


def _mine_data(only_skill=''):
    """扫描各技能 CHANGELOG → {招式号: {'skills':set, 'head':[...]}}（仅能归类的种子招式）。"""
    found=defaultdict(lambda:{'skills':set(),'head':[]})
    skills=[only_skill] if only_skill else _all_skills()
    total_lines=0
    for sk in skills:
        if sk in MOVE_NOISE:      # 自指噪声：元技能自己讲招式定义
            continue
        txt=_changelog(sk)
        if not txt:
            continue
        total_lines+=txt.count('\n')+1
        for seg in _segments(txt):
            if _seg_excluded(seg):
                continue
            for tid in _match_moves(seg):
                found[tid]['skills'].add(sk)
                if len(found[tid]['head'])<8:
                    found[tid]['head'].append(f'{sk}: {_seg_head(seg,34)}')
    return found, total_lines, len(skills)


def discover_new_moves(min_skill=2, only_skill=''):
    """未归类挖掘：跨 ≥N 技能出现、但不在 SEED_MOVES 里的高频短语（候选新招式）。
    只提示不自动入库（人工确认后补进 SEED_MOVES）。
    S179 修：本函数原忽略 only_skill，导致 `--skill X` 时仍按全生态算，
    六个不同技能打印出**一模一样**的候选清单——监视 M20/M21/M27/M32/M46/M47
    因此无法按 skill 取证（dst Olay: 参数生效了一半 = 最容易被当真的假数据）。
    修正：only_skill 非空时，只保留**该技能也在其中**的短语。"""
    phrase=defaultdict(set)
    for sk in _all_skills():
        if sk in MOVE_NOISE:
            continue
        for seg in _segments(_changelog(sk)):
            if _seg_excluded(seg):
                continue
            # 抽段内粗体短语 + 标题中文短语（4-12 字）
            for m in re.findall(r'\*\*([^*\n]{4,14})\*\*', seg):
                p=re.sub(r'[（(].*$','',m).strip()
                if 4<=len(p)<=12 and re.search(r'[\u4e00-\u9fff]', p):
                    phrase[p].add(sk)
    # 已归类招式名里的词剔除（近似）
    known=' '.join(v['name'] for v in SEED_MOVES.values())
    cands=[(p,len(ss)) for p,ss in phrase.items()
           if len(ss)>=min_skill and p not in known
           and (not only_skill or only_skill in ss)]
    cands.sort(key=lambda x:-x[1])
    return cands[:12]


def _move_rows_with_id(db):
    """读招式库**含 record_id** 的行 → (ok, {招式编号: row})。
    ★为何不用 rows_of：core.rows_of 走 get_database_content.py 的 CSV 输出，
      **不含 record_id**——直接用它做 update 会因缺 id 静默失败（伪成功）。
      S088 已记录此坑（"用 rows_of 取 record_id → 永远判新增"），此处按其教训改用
      query_database_record.py（分页返回，含 record_id）。
    ★三态（M-R60）：读取失败 → (False, {})；**调用方不得把 ok=False 当"库为空"**
      （否则会判全部为新增 → 重复插入，正是 S088 的"一次 check 插出 4 条重复行"）。"""
    q=f'{LIB}/database/query_database_record.py'
    try:
        r=subprocess.run(['python3',q,'--database-id',db,'--page-size','100'],
                         capture_output=True,text=True,timeout=30)
        d=json.loads(r.stdout or '{}')
        rows=d.get('results')
        if not isinstance(rows,list):
            return False, {}          # 结构失配 = 读取失败（非空库）
        return True, {str(x.get('招式编号') or ''):x for x in rows if x.get('招式编号')}
    except Exception:
        return False, {}              # 读取失败（非空库）


def cmd_mine(a):
    mn=a.min
    found, lines, nsk = _mine_data(a.skill)
    univ={tid:d for tid,d in found.items() if len(d['skills'])>=mn}
    print(f'🔬 招式挖掘（扫 {nsk} 技能 CHANGELOG {lines} 行）｜ 跨 ≥{mn} 技能才算通用\n')
    if not univ:
        print('  （未发现跨技能通用招式——检查 SEED_MOVES 判据或技能覆盖）')
    for tid in sorted(univ):
        d=univ[tid]; n=len(d['skills'])
        hot='★热招' if n>=3 else '常用'
        print(f'  [{tid}] {SEED_MOVES[tid]["name"]:32} 跨{n}技能 {hot}')
        print(f'        出处: {", ".join(sorted(d["skills"]))}')
    # 候选新招式
    # S179：--skill 时须把过滤传下去，否则候选清单仍是全生态的（见函数注释）
    cands=discover_new_moves(mn, only_skill=a.skill or '')
    if cands:
        scope=f'（限定 {a.skill}）' if a.skill else ''
        print(f'\n  ⚠️ 候选新招式{scope}（跨≥{mn}技能但未归类，需人工确认后补入 SEED_MOVES）：')
        for p,n in cands[:8]:
            print(f'     · {p}（跨{n}技能）')

    if a.dry:
        print(f'\n🧪 --dry：仅审计，未落库（{len(univ)} 条可入库）')
        return 0      # S177：--dry 仅审计，是成功的正常结束（显式 0，不靠隐式 None 偶然成立）
    # ── 落库（类型包装 + 三态）──
    db=_move_db()
    if not db:
        print('  ⚠️ 招式库 id 缺失（move_ids.json）——请先建库'); return
    now=time.strftime('%Y-%m-%dT%H:%M:00Z',time.gmtime())
    _ex_ok, exist = _move_rows_with_id(db)   # 三态：ok=False 时**不得当空库**（否则全判新增→重复插入）
    if not _ex_ok and exist == {}:
        print('  ⛔ 招式库读取失败（无法判定既有记录）——**中止落库**防重复插入（M-R60/S088 教训）')
        return 3      # S177：原裸 return→rc0。读库失败=无法判定(M-R60)，不是成功也不是没有数据
    recs=[]
    for tid in sorted(univ):
        d=univ[tid]; n=len(d['skills'])
        rec={'招式编号':{'text':tid},'招式名':{'text':SEED_MOVES[tid]['name']},
             '判据':{'text':'|'.join(SEED_MOVES[tid]['kw'])},
             '出处技能':{'text':','.join(sorted(d['skills']))},
             '跨技能数':{'number':n},
             '热度':{'select':'热招' if n>=3 else '常用'},
             '适用条件':{'text':SEED_MOVES[tid]['cond']},'时间':{'date':now}}
        if tid in exist:
            rec['record_id']=exist[tid].get('record_id')
            recs.append(('upd',rec))
        else:
            recs.append(('add',rec))
    ok=fail=unk=0
    for kind,rec in recs:
        try:
            if kind=='add':
                a_,b_=add_records(db,[rec]); ok+=a_; fail+=b_
            else:
                rid=rec.pop('record_id')
                if not rid:
                    fail+=1; continue      # 无 id 不可更新（防伪成功）
                # S158：改走 core.update_records
                # ★ M-R60：原写 `except Exception: _ok = False` 把「通路坏了」折叠成
                #   「更新被拒绝」——两者都记进 fail 计数，日志报「失败 N 条」时
                #   人分不清是数据被拒还是脚本连不上库，会照着去重写已经成功的数据。
                #   改三态：None = 未判定，单独计数，输出显式提示。
                _ok = None
                try:
                    import core as _ce2
                    _ok, _n2, _ = _ce2.update_records(db, [{'record_id': rid, 'properties': rec}])
                except Exception:
                    _ok = None
                if _ok is None:
                    unk += 1
                elif _ok:
                    ok += 1
                else:
                    fail += 1
        except Exception:
            fail+=1
    # 写后失效缓存（规则：写必须 invalidate，否则下方读回命中脏缓存）+ 读回验证（防伪成功）
    try:
        invalidate(db)
    except Exception:
        pass
    _after={r.get('招式编号'):r for r in rows_of(db)}
    _vfy=sum(1 for tid in univ if _after.get(tid) and str(_after[tid].get('跨技能数'))==str(len(univ[tid]['skills'])))
    print(f'\n✅ 新增/更新入库 {ok} 条 / 失败 {fail} 条'
          + (f' / **未判定 {unk} 条（更新通路异常，勿当作失败）**' if unk else '')
          + f' ｜ 招式库共 {len(univ)} 条通用招式'
          f' ｜ 读回验证 {_vfy}/{len(univ)}' + ('' if _vfy==len(univ) else ' ⚠️ 部分未同步，请复核'))
    # 门面联动：长期不借鉴热招的技能 → 登记监察司
    try:
        hot=[tid for tid in univ if len(univ[tid]['skills'])>=3]
        for sk in _all_skills():
            if sk in MOVE_NOISE: continue
            used=set()
            for seg in _segments(_changelog(sk)):
                used.update(_match_moves(seg))
            miss=[t for t in hot if t not in used]
            if len(miss)>=3:      # 多条热招都没用 → 值得盯
                register_monitor(f'{sk} 招式借鉴停滞','balance_weakness',
                                 f'{sk} 未采用 {len(miss)} 条跨技能热招（{"/".join(miss[:3])}…）')
    except Exception:
        pass


def _lend_rows(name):
    """某技能：已用招式(set) / 未用且适用(list)。三态——招式库读失败返回 None。"""
    found,_l,_n=_mine_data(name)        # 该技能已用招式
    used=set(found)
    db=_move_db()
    if not db:
        return used, None, []           # 库缺失：无法判定"未用"
    try:
        lib={r.get('招式编号'):r for r in rows_of(db)}
    except Exception:
        return used, None, []           # 库读取失败：无法判定"未用"
    if not lib:
        return used, None, []           # 空库≠无适用：无法判定
    unused=[]; undet=[]          # undet：跨技能数无法判定者（M-R60：不得折叠成 0 被静默丢弃）
    for tid,r in lib.items():
        try:
            ns=int(r.get('跨技能数') or 0)
        except (TypeError,ValueError):
            # 字段脏/缺失 → 三态：既不算"未用"也不算"不适用"，单列待人工核对
            # （原 `ns=0` 会因 ns<2 被 continue 静默丢弃——"读不到"被折叠成"不通用"）
            undet.append((tid, r.get('招式名','')))
            continue
        if ns<2: continue
        if tid in used: continue
        unused.append((tid, r.get('招式名',''), r.get('适用条件',''), ns))
    # ★ 排序必须全键确定：只按 `-跨技能数` 排，同数者顺序取决于云端返回顺序，
    #   列表会在一模一样的输入下换行——对外这是"输出不稳定"，对内它把
    #   equiv_check 变成恒报红的假警报（实测踩到：T-I 与 T-C 都是 10 技能，
    #   入库后 T-I 从第 10 行跳到第 8 行）。补招式编号兜底，同数永远同序。
    unused.sort(key=lambda x: (-x[3], x[0]))
    return used, unused, undet


def cmd_lend(a):
    used, unused, undet = _lend_rows(a.name)
    db=_move_db()
    print(f'📚 技能可借鉴招式：{a.name}')
    try:
        n_rec=len(rows_of(db)) if db else 0
    except Exception:
        n_rec=0
    print(f'  招式库 {n_rec} 条 ｜ 该技能已用 {len(used)} 条 ｜ 未用 '
          f'{"(无法判定)" if unused is None else len(unused)} 条\n')
    if used:
        print('  ✅ 已用: ' + '、'.join(f'[{t}]' for t in sorted(used)))
    if unused is None:
        print('  ⚠️ 招式库读取失败（无法判定"未用/适用"）——勿据此判"无借鉴"（M-R60）')
    else:
        print('  ⬜ 未用但适用（升级时建议逐条确认）:')
        for tid,nm,cond,ns in unused:
            print(f'     [{tid}] {nm:30} 跨{ns}技能 ｜ 适用: {cond}')
    if undet:
        # M-R60：跨技能数无法判定的招式，既不算"未用"也不算"不适用"——单列待人工核对
        print(f'  ⚠️ 跨技能数无法判定 {len(undet)} 条（字段脏/缺失，勿默认"不通用"）: '
              + '、'.join(f'[{t}]{n}' for t,n in undet))
    if a.json:
        print(json.dumps({'skill':a.name,'used':sorted(used),
                          'applicable':[] if unused is None else [t for t,_,_,_ in unused]},
                         ensure_ascii=False))
    else:
        print('\n  ↪ 规则57：升级前须逐条确认「借鉴/不适用」并留痕，漏项=遗留')


def _lend_baseline():
    """规则57 基线（M-R34：规则不溯及既往）——记录"立规时已在的最新升级段"，
    只有**立规之后新发生的升级**缺对照留痕才算缺口；存量段豁免，避免首次运行刷出 N 条历史噪声。
    返回 {skill: 最新段标题头(44字)}；文件缺失返回 {} 但不报错（视为无基线→首轮全部豁免并落基线）。"""
    fp=os.path.join(os.path.dirname(os.path.abspath(__file__)),'lend_baseline.json')
    try:
        return json.load(open(fp,encoding='utf-8'))
    except Exception:
        return None                      # None=基线文件缺失（首次立规），非"无豁免"


def _write_lend_baseline(seg_map):
    fp=os.path.join(os.path.dirname(os.path.abspath(__file__)),'lend_baseline.json')
    try:
        prev=json.load(open(fp,encoding='utf-8'))
    except Exception:
        prev={}
    prev.update(seg_map)                 # 增量吸收新段（历史豁免不丢）
    try:
        json.dump(prev,open(fp,'w',encoding='utf-8'),ensure_ascii=False,indent=2)
    except Exception:
        pass


def cmd_review_lend(a):
    """对账：最近升级的技能里，哪些"升了版但 CHANGELOG 无对照留痕"（规则57 违规）。
    M-R34：以 `lend_baseline.json` 为界——**立规前的存量段豁免**（不溯及既往），
    只有立规后新发生的升级缺留痕才报；并把本轮段增量写入基线（下轮起变存量）。"""
    print('🧾 经验对照对账（规则57：升级须有对照留痕）\n')
    base=_lend_baseline()
    first=(base is None)                 # 首次立规：全部存量豁免并落基线
    if first: base={}
    gaps=[]; new=[]
    for sk in _all_skills():
        txt=_changelog(sk)
        if not txt:
            continue
        segs=_segments(txt)
        if not segs:
            continue
        last=segs[-1]                     # 最新版本段
        head=_seg_head(last,44)
        is_upg = bool(re.search(r'v?\d+\.\d+\.\d+', _seg_head(last,80)))
        if not is_upg:
            continue
        new.append((sk, head))
        if base.get(sk)==head:            # 已豁免的存量段 → 跳过
            continue
        trace = bool(re.search(r'借鉴|不适用|经验对照', last))
        if not trace and not first:       # 首次立规：存量段一律豁免（M-R34 不溯及既往）
            gaps.append((sk, head))
    # 落基线：把本轮所有段头写入（含缺口段——缺口本轮报出后即豁免，防每轮重复刷屏；
    # 真实缺口应在下一次升级时由"新段无留痕"再次暴露，或由技能方主动补记）
    _write_lend_baseline({sk:h for sk,h in new})
    if not gaps:
        tail = '（首轮立规：存量段全部豁免，已落基线）' if first else '——近期升级均有对照留痕'
        print(f'  ✅ 无缺口{tail}')
        return 0      # S177：无缺口=全绿，显式 0
    for sk,head in gaps:
        print(f'  ⚠️ {sk} 升了版但无经验对照留痕：{head}')
    print(f'\n  共 {len(gaps)} 处缺口（规则57：升级前须跑 lend，升级后须回写"借鉴 T-X/不适用"）')


def _lend_precheck(a):
    """升级前置检查：返回"未用但适用的招式数"。
    三态：招式库读失败 → 返回 None（无法判定，不阻断，仅告警）。"""
    try:
        used, unused, undet = _lend_rows(a.name)
    except Exception as e:
        print(f'  ⚠️ 规则57 经验对照无法判定（{type(e).__name__}）：跳过前置检查，不阻断升级')
        return None
    if unused is None:
        print('  ⚠️ 规则57 经验对照无法判定（招式库读取失败）——不阻断升级，建议稍后重跑 lend')
        return None
    if unused:
        print(f'📚 规则57 经验对照：{a.name} 有 {len(unused)} 条可借鉴但未用的通用招式——')
        for tid,nm,cond,ns in unused[:6]:
            print(f'     [{tid}] {nm}（跨{ns}技能，适用: {cond}）')
        print('  ↪ 本次升级将记录「经验对照已做」；若全部不适用，请在 CHANGELOG 声明。')
    else:
        print(f'✅ 规则57 经验对照：{a.name} 已用全部适用招式（无待借鉴）')
    return len(unused)


PROPOSALS=os.path.join(ROOT,'upgrade_proposals.json')

# ══════════ M-R111 经验池驱动的技能升级闭环 ══════════
# 闭环：absorb(扫经验) → propose(出提案) → 用户授权 → commit(执行+留痕)
# 另含 cross-learn：把某技能新沉淀的招式推给匹配的其他技能（同走 propose→授权）。

def _load_props():
    try:
        return json.load(open(PROPOSALS,encoding='utf-8'))
    except Exception:
        return {'version':'1.0.0','items':[]}

def _save_props(d):
    json.dump(d,open(PROPOSALS,'w',encoding='utf-8'),ensure_ascii=False,indent=2)


def cmd_propose(a):
    """★ M-R111 第①②步：从招式库+经验池吸收 → 产出技能升级提案（不落笔改技能）。

    提案来源三类：
      A. 招式借鉴：技能有「未用但适用」的通用招式（lend 的 unused）→ 建议吸收
      B. 经验复发：recurring 聚类命中 ≥N 的问题，其解法尚未固化到某技能 → 建议补规则/补工具
      C. 跨技能学习：cross-learn 队列里待推送的招式
    """
    props=_load_props()
    exist={(p['skill'],p.get('type'),p.get('ref')) for p in props['items']}
    new=[]
    targets=[a.skill] if getattr(a,'skill','') else _all_skills()
    print('🧠 经验池 → 技能升级提案（M-R111：吸收→提案，等你授权后才改技能）\n')
    # A. 招式借鉴
    for sk in targets:
        try:
            _used,unused,_undet=_lend_rows(sk)
        except Exception:
            continue
        if not unused:
            continue
        for tid,nm,cond,ns in unused:
            key=(sk,'move',tid)
            if key in exist: continue
            new.append({'skill':sk,'type':'move','ref':tid,
                        'what':f'吸收通用招式 [{tid}] {nm}',
                        'why':f'跨{ns}技能复用；适用条件：{cond}',
                        'source':'招式库(lend)','created':time.strftime('%Y-%m-%d %H:%M'),
                        'status':'pending'})
    # B. 经验复发（只取「按问题类型聚类」一节；关键词聚类噪声大，不作提案依据）
    TYPE2SKILL={
        '记录读写':'tvbox-source-hub','规则维护':'ecosys-conductor','表格编辑':'workbuddy-sheetagent',
        '字段管理':'tvbox-source-hub','纠错返工':'ecosys-conductor','新接口处理':'tvbox-interface',
        '建库建夹':'tvbox-source-hub','节点移动改名':'tvbox-janitor','整包入库':'tvbox-interface',
    }
    try:
        import io, contextlib
        buf=io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_recurring(argparse.Namespace(min=getattr(a,'min',3)))
        rep=buf.getvalue()
        in_type=False
        mn=getattr(a,'min',3)
        for ln in rep.splitlines():
            if '按「问题类型」聚类' in ln:
                in_type=True; continue
            if in_type and '按「关键词」聚类' in ln:
                in_type=False; continue
            if not in_type or not ln.startswith('  '):
                continue
            m=re.match(r'\s*(\d+)×\s*\[([③④])\]\s*(.+?)\s*$', ln)
            if not m:
                continue
            n,src,typ=int(m.group(1)),m.group(2),m.group(3).strip()
            if n < mn:
                continue
            sk=TYPE2SKILL.get(typ)
            if not sk:
                print(f'  ⚠️ 复发类型「{typ}」未映射技能，跳过（请补 TYPE2SKILL）')
                continue
            key=(sk,'recur',typ)
            if key in exist: continue
            new.append({'skill':sk,'type':'recur','ref':typ,
                        'what':f'复发问题「{typ}」{n} 次 → 需固化进技能',
                        'why':f'{src}台账/经验库该类型累计 {n} 次（阈值≥{mn}），解法应沉淀为规则或工具',
                        'source':'recurring 聚类','created':time.strftime('%Y-%m-%d %H:%M'),
                        'status':'pending'})
    except Exception as e:
        print(f'  ⚠️ 复发聚类读取失败（{type(e).__name__}）——A 类提案不受影响')
    # C. 跨技能学习队列
    for q in props.get('cross_queue',[]):
        key=(q['to'],'cross',q['move'])
        if key in exist: continue
        new.append({'skill':q['to'],'type':'cross','ref':q['move'],
                    'what':f'跨技能学习：借鉴 [{q["move"]}] {q.get("name","")}',
                    'why':q.get('why',''),'source':'cross-learn 队列',
                    'created':time.strftime('%Y-%m-%d %H:%M'),'status':'pending'})
    if not new:
        print('  ✅ 无新提案（招式已吸收、无新复发、无待推送）')
        return 0
    props['items'].extend(new)
    _save_props(props)
    # 按技能分组打印
    bysk={}
    for p in new: bysk.setdefault(p['skill'],[]).append(p)
    for sk,ps in bysk.items():
        print(f'  📦 {sk}（{len(ps)} 条）')
        for i,p in enumerate(ps,1):
            print(f'    {i}. [{p["type"]}] {p["what"]}')
            print(f'       依据：{p["why"]}')
    print(f'\n  共 {len(new)} 条待授权提案 → {PROPOSALS}')
    print('  ↪ 授权执行：evolve.py commit --skill <名> [--all]  ｜ 驳回：evolve.py commit --reject <序号>')
    note(f'propose: 产出 {len(new)} 条升级提案')
    return 0


def _move_landed(skill, tid):
    """回读校验：招式 `tid` 是否**真的落进了技能**。返回 (state, detail)。

    ★ S182（本条是 S181 发现「41 条 done 空转」后的正式落地）：
      原实现 `for p in ps: p['status']='done'` —— **升级一结束就无条件标 done**，
      从没看过技能正文有没有变。于是「写了一条提案」被记成「招式已成事实」。

      ★ 但纠正一处我自己的误判：S181 当时说「招式代号 T-A…T-I 出现 0 次 = 全空转」，
        **那个判据是错的**——技能正文写的是招式的**名字和做法**，不是代号。
        用代号去 grep，命中的只会是"讨论招式库"的元技能，当然 0 次。
        重新用 SEED_MOVES 的关键词实测：29 条 move 提案里 **18 条脚本有实现、
        11 条只有文档痕迹、其中 2 条连文档都没有**。所以不是"全空转"，
        而是"**有一部分真落地了，另一部分只停留在文档**"——这 11 条才是真问题。

    判据分四态（M-R60：不许把"没查成"折叠成"没做"）：
      'landed'       脚本里能找到招式关键词（做法进了代码，最可信）
      'na'           **文档里明确评估过并判定不适用**（"不适用…"/"无删除API，这是硬边界"）
                     —— 这是完成态，不是欠账。认真评估后说"不需要"，比照抄一句话更可信
      'doc'          脚本没有、文档里在**叙述做法**（只说不做）
      'none'         两处都没有（纯空转，最该重开）
      'unknown'      技能目录不存在 / 招式不在库 → **不是 none**，不许当"没做"
    """
    d = os.path.join(SKILLS, skill)
    if not os.path.isdir(d):
        return 'unknown', f'技能目录不存在：{skill}'
    spec_m = SEED_MOVES.get(tid)
    if not spec_m:
        # 非 SEED 招式（如 recur 类提案）没有关键词表 → 无法判定，不得当已落地
        return 'unknown', f'招式 {tid} 不在 SEED_MOVES，无判据'
    kws = spec_m['kw']
    py_txt, md_txt = [], []
    for root, _dirs, files in os.walk(d):
        for f in files:
            p = os.path.join(root, f)
            try:
                s = open(p, encoding='utf-8').read()
            except Exception:
                continue
            if f.endswith('.py'):
                py_txt.append(s)
            elif f.endswith('.md'):
                md_txt.append(s)
    _py, _md = '\n'.join(py_txt), '\n'.join(md_txt)
    # ★★ S182 终验抓到的自证闭环（必须堵）：
    #   commit 会把提案原文 `- 依据 M-R111 提案：吸收通用招式 [T-D] 移动替代删除（…）`
    #   写进 CHANGELOG。闸门回读 CHANGELOG 时，这行**恰好含招式全名**，于是判成 doc。
    #   等于：我写下"我要吸收T-D" → 闸门看到"T-D" → 认为已吸收。
    #   越用越像真的，且同一招下次永远判 doc。这是判据的自我污染，比误判更坏。
    #   修法：回读前先剥掉「提案依据」这类**提交痕迹**，只认真实叙述。
    _md_raw = _md
    _md = re.sub(r'(?m)^[-*]\s*依据\s*M-R111\s*提案：.*$', '', _md)
    _md = re.sub(r'(?m)^[-*]\s*由\s*skill-evolution\s*upgrade\s*自动提升.*$', '', _md)
    n_py = sum(1 for k in kws if re.search(k, _py, re.I))
    if n_py:
        return 'landed', f'脚本命中 {n_py} 个关键词'

    # ★★ S182 第五次修正：**行级**判定，而不是整篇布尔。
    #   上一版只看"文档里有没有出现关键词"，于是把两种完全相反的东西算成一类：
    #     ①「我在用这招：删除接口不可用时改为移动到回收夹」   ← 叙述做法 = doc
    #     ②「[T-D] 移动替代删除 — **不适用**：监察司不做删除」← 评估结论 = na
    #   实测 9 条 doc 里，7 条其实是 ②。把"认真评估后判定不适用"记成欠账，
    #   等于逼技能去实现一个它根本用不到的招式 —— 这种事做出来就是纯负担。
    #   判据必须落到「含招式的那一行，是在讲做法还是在讲结论」。
    _NA_RE = re.compile(
        r'不适用|无需|不需要|暂无需求|没有东西要搬|硬边界|不做删除|'
        r'无.{0,6}删除.{0,4}API|本技能不|已评估|不涉及|'
        # ★ 实测补：这两条形态原先漏了，害得"明明写了结论"的行被判成在讲做法
        #   `- [T-B] 薄壳 import：无跨技能共享逻辑需求（账房自包含）`     ← 无…需求
        #   `- [T-D] 移动替代删除：library 有删除 API，无降维必要`        ← 无…必要
        r'无.{0,8}需求|无.{0,6}必要|用不上|没有.{0,6}场景', re.I)
    # ★★ S182 第七次修正：**已借鉴 也是结论**，而且是比"不适用"更强的结论。
    #   实测 16 条 doc 里有一半的原文长这样：
    #     `- **借鉴 T-G 单一事实源 SSOT**：失效原因枚举收口为唯一常量，使用点派生。`
    #   这是"我用了这招、用在哪"，其中「借鉴」二字是规则57 的固定记法（= 已采用），
    #   不是"我打算借鉴"。上一版不认这个词，于是每一条经验对照留痕都被算成欠账——
    #   等于**要求技能再实现一遍它已经写好的东西**。
    #   注意顺序：先判 na（不适用），再判采纳（已借鉴），最后才判 doc。
    #   同一行既有「借鉴 T-X」又有「不适用」时以不适用为准（那是评估结论）。
    _ADOPT_RE = re.compile(
        r'借鉴\s*T-[A-I]|已借鉴|已采用|已吸收|参考\s*T-[A-I]|采纳\s*T-[A-I]', re.I)
    _lines = _md.splitlines()
    na_hits, doc_hits, adopt_hits = [], [], []
    for idx, ln in enumerate(_lines):
        hit_kw = [k for k in kws if re.search(k, ln, re.I)]
        if not hit_kw:
            continue
        # 招式名常出现在"清单"行（`T-C 指纹白名单` / `T-F 机械量化` / …）——
        # 那是登记表，不是叙述也不是结论，单看它不算证据。
        # 但清单行后面常跟一段总说明（"…— **本技能当前不适用**：清道夫没有白名单结构"），
        # 那是真结论。所以清单行不直接判，改为**向下看 3 行**找结论。
        is_list = len(re.findall(r'`?\bT-[A-I]\b', ln)) >= 2
        # 只看关键词所在的分句，避免整行里"旁边恰好有个不适用"被借来当结论
        rest = ln
        for k in hit_kw:
            rest = re.sub(k, ' ', rest, flags=re.I)
        if _NA_RE.search(rest):
            na_hits.append(ln.strip()[:120])
            continue
        if _ADOPT_RE.search(rest):
            adopt_hits.append(ln.strip()[:120])
            continue
        if is_list:
            # 向下 3 行找结论；找到则本条算"已评估"
            _tail = ' '.join(_lines[idx + 1: idx + 4])
            if _NA_RE.search(_tail):
                na_hits.append((ln.strip()[:80] + ' … ' + _tail.strip())[:120])
            elif _ADOPT_RE.search(_tail):
                adopt_hits.append((ln.strip()[:80] + ' … ' + _tail.strip())[:120])
            # 找不到结论也不记 doc —— 清单行本身不是"叙述做法"的证据
            continue
        doc_hits.append(ln.strip()[:120])

    done_n = len(na_hits) + len(adopt_hits)
    if doc_hits and not done_n:
        return 'doc', f'脚本 0 命中、文档 {len(doc_hits)} 处在叙述做法——只说不做'
    if done_n and not doc_hits:
        _r = []
        if adopt_hits:
            _r.append(f'已借鉴 {len(adopt_hits)} 处')
        if na_hits:
            _r.append(f'判定不适用 {len(na_hits)} 处')
        return 'na', f'脚本 0 命中、文档明确给出结论（{"、".join(_r)}）——完成态'
    if doc_hits and done_n:
        # ★★ S182 第八次修正：两种都有时，**不能一律判 doc**。
        #   实测有 9 条属于"远处有结论、近处有叙述"，但近处那条叙述就是结论句本身。
        #   例：`- [T-B] 薄壳 import：无跨技能共享逻辑需求（账房自包含）`
        #   —— 行里的关键词是招式的特征词（薄壳 import），它旁边写着"无需求"，
        #   但 _NA_RE 的字面表里没有"无…需求"这一形态，于是被当成"在讲做法"。
        #   一律判 doc 的代价是：每一条**认真写过结论**的留痕都被算成欠账，
        #   而 skill 没法靠"写清楚"把账销掉 —— 判据逼着人去写假实现。
        #   改为：**逐行比距离**。离结论更近的叙述算已被结论覆盖；
        #   只有结论远在别处、近处确实在讲怎么做，才算 doc。
        _na_idx = {i for i, ln in enumerate(_lines)
                   if _NA_RE.search(ln) or _ADOPT_RE.search(ln)}
        _near = [h for h in doc_hits
                 if any(abs(_lines.index(h) - j) <= 2 for j in _na_idx
                        if h in _lines)]
        _far = [h for h in doc_hits if h not in _near]
        if _far:
            return 'doc', (f'脚本 0 命中；{len(_far)} 处在讲做法且附近无结论'
                           f'（另有 {len(_near)} 处被结论覆盖）——以叙述为准，可能有未做部分')
        return 'na', (f'脚本 0 命中；文档 {len(doc_hits)} 处叙述均紧贴结论'
                      f'（已借鉴 {len(adopt_hits)}、不适用 {len(na_hits)}）——完成态')
    if re.search('|'.join(f'(?:{k})' for k in kws), _md_raw, re.I):
        # 剥完回执后 0 命中，剥之前有 → 证据只剩提交痕迹。但要分清是哪种痕迹：
        #   回执行（"依据 M-R111 提案…"）→ 自我证明，绝不算落地
        #   清单行（一次列出好几个招式号）→ 只是登记，也不算落地
        # 判词必须说清是哪一种，否则下次查账的人会被误导去翻"提案依据"。
        _only_receipt = bool(re.search(r'依据\s*M-R111\s*提案', _md_raw))
        _only_list = bool(re.search(r'`?\bT-[A-I]\b.*`?\bT-[A-I]\b', _md_raw))
        if _only_receipt and not _only_list:
            return 'none', '仅命中「提案依据」回执行（自我证明，不算落地）——纯空转'
        if _only_list and not _only_receipt:
            return 'none', '仅命中招式清单行（只是登记，不是落地）——纯空转'
        return 'none', '仅命中登记/回执痕迹（清单行与提案依据），无真实叙述——纯空转'
    return 'none', '脚本与文档均 0 命中——纯空转'


def _prop_key(p):
    """提案的稳定唯一键。★ S182：原先用 `id(p)` 当键 —— 内存地址既不跨进程稳定，
    也不在提案结构里（提案根本没有 id 字段）。propose 里判重用的一直是
    `(skill, type, ref)` 三元组，闸门必须用同一把钥匙，否则「放行过谁」和
    「该标谁 done」永远对不上。"""
    return (p.get('skill') or '', p.get('type') or '', p.get('ref') or '')


def _gate_moves(ps):
    """对一批 move 提案做回读闸门。返回 (ok_list, fail_list)。

    不阻断升级（版本号该升还是要升），但**不许把没落地的标成 done**：
      · landed → 可以 done
      · na     → 可以 done（文档里已明确评估"不适用"，这是完成态，不是欠账）
      · doc    → 标 done 但记 `landed='doc'`，CHANGELOG 里写明"仅文档"
      · none   → **不标 done，退回 pending 并记原因**（这才是"待授权"的真实状态）
      · unknown→ 不标 done，退回 pending（判据不可用 ≠ 已落地）
    """
    ok, bad = [], []
    for p in ps:
        tid = p.get('ref')
        if p.get('type') != 'move' or not tid:
            ok.append(p)          # 非招式提案（recur 等）不受本闸门管辖
            continue
        state, detail = _move_landed(p.get('skill'), tid)
        p['landed'] = state
        p['landed_detail'] = detail
        if state in ('landed', 'doc', 'na'):
            ok.append(p)
        else:
            bad.append(p)
    return ok, bad


def _find_py_anchor(skill, tid):
    """给一条"只写在文档里"的招式找落点：返回候选插入位置列表。

    只读不写。**宁可返回空，不许凑数。**

    第一版按函数名关键词（save/write/check/probe…）扫，实测两份产物都跑偏：
      · `_probe_empty_databases` 名字带 probe 被推荐给「活体探针」——它查的是空库，不同事
      · conductor 12 个候选全是 check-fn，与「物理搬迁」毫无关系
    关键词匹配函数名，等于用"名字像"替"事情像"。这比不给候选更糟：它会让人
    照着一个错误的位置改，而错误的位置往往也能通过表面测试。
    改成：候选必须**函数体里也有招式关键词**，才列进来。
    """
    spec_m = SEED_MOVES.get(tid) or {}
    d = os.path.join(SKILLS, skill)
    kws = spec_m.get('kw') or []
    cands = []
    for root, _dirs, files in os.walk(d):
        if '__pycache__' in root:
            continue
        for f in sorted(files):
            if not f.endswith('.py'):
                continue
            p = os.path.join(root, f)
            try:
                src = open(p, encoding='utf-8').read()
            except Exception:
                continue
            lines = src.splitlines()
            # 先按 def 切段，拿到每个函数的起止行
            defs = [(i, ln) for i, ln in enumerate(lines, 1)
                    if re.match(r'\s*def\s+\w+', ln)]
            for n, (start, sig) in enumerate(defs):
                end = defs[n + 1][0] - 1 if n + 1 < len(defs) else len(lines)
                body = '\n'.join(lines[start - 1:end])
                hit = [k for k in kws if re.search(k, body, re.I)]
                if hit:
                    cands.append((os.path.relpath(p, d), start, sig.strip()[:90],
                                  '函数体内命中 ' + '、'.join(hit[:3])))
    return cands[:12]


def cmd_patch(a):
    """★ S182 第①档：为"只写在文档里"的招式生成**可审阅补丁**。

    严格边界（本命令的存在前提）：
      · **只读技能文件，绝不写入**；仅输出到 `--out`（默认 upgrade_patches/）
      · 生成的是"往哪个函数插什么判断"的提案文本 + 统一 diff 形式的示意
      · 不适用（na）的招式不生成补丁 —— 那是完成态，不是欠账
    这是"让它写、让人审"的第一步。**不给它写技能目录的权限**，是本档的核心约束。
    """
    props = _load_props()
    only = getattr(a, 'skill', '') or ''
    items = [p for p in props['items'] if p.get('type') == 'move' and p.get('status') == 'done']
    if only:
        items = [p for p in items if p.get('skill') == only]
    targets = []
    seen = set()
    for p in items:
        st, det = _move_landed(p.get('skill'), p.get('ref'))
        if st == 'doc':
            targets.append((p, det))
            seen.add((p.get('skill'), p.get('ref')))

    # ★ 只扫提案清单会漏掉绝大多数欠账。实测：提案清单里 done 的 move 有 29 条，
    #   而全量跑五态得到的 doc 有 16 格，两边**只交集 2 条**。差在哪：
    #   提案是"某技能用了某招"（lend 口径，只看没用过的），
    #   而 doc 欠账是"文档说了、脚本没做"（任意招式都可能，不限于没用过的）。
    #   两个口径不同的集合，拿其中一个去推另一个，必然漏。
    #   所以补一条全扫通道：所有技能 × 所有招式，一格一格判，doc 就出补丁。
    scanned = 0
    if not getattr(a, 'no_scan', False):
        sk_list = [only] if only else _all_skills()
        for sk in sk_list:
            if not os.path.isdir(os.path.join(SKILLS, sk)):
                continue
            for tid in SEED_MOVES:
                if (sk, tid) in seen:
                    continue
                scanned += 1
                st, det = _move_landed(sk, tid)
                if st == 'doc':
                    targets.append(({'skill': sk, 'ref': tid, 'type': 'move',
                                     'status': 'scan', 'src': '全量五态扫描'},
                                    det))
                    seen.add((sk, tid))

    print('🧩 只为「脚本无实现、文档在讲做法」的招式生成补丁')
    print(f'   提案清单通道：{len(items)} 条 done move'
          f'（跳过全扫时只走这条）')
    print(f'   全量扫描通道：{scanned} 格（{len(sk_list) if scanned else 0} 技能 × {len(SEED_MOVES)} 招式）')
    print(f'   合计命中 {len(targets)} 条'
          f'（na=已评估不适用 / landed=已落地 / none=连文档都没有，均不生成）\n')
    if not targets:
        print('  ℹ️ 没有需要生成补丁的招式。'); return 0

    out = getattr(a, 'out', '') or os.path.join(ROOT, 'upgrade_patches')
    os.makedirs(out, exist_ok=True)
    made = []
    for p, det in targets:
        sk, tid = p.get('skill'), p.get('ref')
        spec_m = SEED_MOVES.get(tid)
        if not spec_m:
            continue
        anchors = _find_py_anchor(sk, tid)
        body = []
        body.append(f'# 补丁提案：{sk} / {tid} {spec_m["name"]}')
        body.append('')
        body.append('> 本文件由 `evolve patch` 生成，**没有改动任何技能文件**。')
        body.append('> 审阅后由人决定是否采用。采用方式：照下列位置手工插入，或作为提示交给执行体。')
        body.append('')
        body.append('## 为什么需要这一步')
        body.append('')
        body.append(f'- 招式适用条件：{spec_m["cond"]}')
        body.append(f'- 当前判定：`doc` —— {det}')
        body.append(f'- 判定依据：脚本 `.py` 中对本招式的关键词 0 命中，但文档在**叙述做法**')
        body.append(f'- 提案原文：{p.get("what", "")}')
        body.append('')
        body.append('## 招式要义')
        body.append('')
        body.append(f'**{spec_m["name"]}**')
        body.append('')
        body.append('判据关键词（落地后应能在脚本里命中）：'
                    + '、'.join('`%s`' % k for k in spec_m['kw']))
        body.append('')
        body.append('## 候选落点（只读扫描得到，需人工判断哪一处才对）')
        body.append('')
        if anchors:
            body.append('候选要求：**函数体内**也出现招式关键词。只名字像的不算。')
            body.append('')
            body.append('| 文件 | 行 | 函数 | 依据 |')
            body.append('|---|---|---|---|')
            for f, i, sig, why in anchors:
                body.append(f'| `{f}` | {i} | `{sig}` | {why} |')
            body.append('')
            body.append('⚠️ 以上仍是**候选**，不是答案。选错位置比不改更糟。')
        else:
            body.append('**未找到候选落点——函数体里一处招式关键词都没有。**')
            body.append('')
            body.append('这不等于"必须硬塞一个"。真实情况通常是三者之一：')
            body.append('')
            body.append('1. **本技能确实用不到这招** → 应判 `na`，在 CHANGELOG 写明理由；')
            body.append('2. **做法已在文件/目录结构里体现**（如"搬迁"已把长条外置成 references）'
                        '→ 脚本层面本就没有对应动作，应在 CHANGELOG 说明"已落地、形式为文件布局"，'
                        '而不是硬加一个假的搬迁函数；')
            body.append('3. **函数命名与招式用词完全不同** → 需人工指定落点。')
            body.append('')
            body.append('**本工具不替你决定是哪一种。** 但它明确反对：为了让判定变绿而硬塞代码。')
        body.append('')
        body.append('## 验收：改完怎么证明真的落地了')
        body.append('')
        body.append('如果决定**动手改**，改完必须能回读出 `landed`：')
        body.append('')
        body.append('```bash')
        body.append('python3 -c "import sys; sys.path.insert(0,\'/root/.codebuddy/skills/'
                    'skill-evolution/scripts\'); import evolve; '
                    f'print(evolve._move_landed(\'{sk}\', \'{tid}\'))"')
        body.append('```')
        body.append('')
        body.append('否则说明改的地方没生效——**不许改判据来迁就结果**。')
        body.append('')
        body.append('如果决定**不改**（判定为不适用 / 已由文件布局体现），'
                    '则该补丁的收口方式是：在技能 CHANGELOG 写明理由，'
                    '并让 `_move_landed` 能读到这段话（判为 `na`）。')
        body.append('')
        body.append('**两条路都算解决。唯一不算解决的，是把判定改成绿的而代码没动。**')
        fn = f'{sk}__{tid}.md'.replace('/', '_')
        fp = os.path.join(out, fn)
        open(fp, 'w', encoding='utf-8').write('\n'.join(body) + '\n')
        made.append(fp)
        print(f'  ✅ {sk} / {tid}  → {fp}')
        print(f'     候选落点 {len(anchors)} 处；判定：{det}')
    print(f'\n共生成 {len(made)} 份补丁提案（**未改动任何技能文件**）')
    # ★ 不再判 doc 的旧补丁必须撤掉：判据改了（"已借鉴"也算结论），
    #   上一轮生成的补丁文件还留在目录里，人一翻就会去实现一个已经不欠的东西。
    #   **补丁目录必须与当前判据一致**，否则它比不生成更误导。
    _live = {os.path.basename(x) for x in made}
    _stale = [f for f in sorted(os.listdir(out))
              if f.endswith('.md') and f not in _live]
    if _stale:
        print(f'\n🧹 以下 {len(_stale)} 份是旧判据留下的、现已不是 doc，已移入 _stale/：')
        _sd = os.path.join(out, '_stale')
        os.makedirs(_sd, exist_ok=True)
        for f in _stale:
            shutil.move(os.path.join(out, f), os.path.join(_sd, f))
            print(f'   - {f}')
        print('   没有删：补丁是"某次判断的留档"，删了就查不到当时依据什么生成它。')
    print(f'输出目录：{out}')
    return 0


def cmd_apply(a):
    """★ S182 第②档：受控写入——**给权限，但关进笼子**。

    三道锁（缺一不动手）：
      ① 白名单：只改 `<技能>/scripts/*.py`；SKILL.md / CHANGELOG / 数据文件一律拒绝
      ② 顺序：snapshot → 写入 → 语法检查 → 行为等价比对；任一不过**自动回滚**
      ③ 退路独立：`apply_guard.py restore <技能>` 随时可撤，不依赖本次是否成功

    默认 dry-run。只有 `--yes` 才真改文件。
    """
    import staged as S
    rc = S.apply_staged(a.skill, a.rel, a.old, a.new, getattr(a, 'yes', False))
    return rc


def cmd_commit(a):
    """★ M-R111 第③④步：用户授权后执行升级（升级版本+写 CHANGELOG+清提案）。

    这是唯一会改技能 SSOT 的入口——必须由用户显式调用（=授权），
    propose 本身绝不落笔。支持 --reject 驳回。
    """
    props=_load_props()
    items=props['items']
    pend=[p for p in items if p.get('status')=='pending']
    if not pend:
        print('  ℹ️ 无待授权提案'); return 0
    if getattr(a,'reject','') is not None and getattr(a,'reject','')!='':
        idx=[int(x) for x in str(a.reject).split(',') if x.strip().isdigit()]
        drop=[]
        for k in idx:
            if 1<=k<=len(pend):
                drop.append(pend[k-1])
        for p in drop: p['status']='rejected'
        _save_props(props)
        print(f'  ✅ 已驳回 {len(drop)} 条提案')
        for p in drop: print(f'     - [{p["type"]}] {p["what"]}')
        return 0
    sel=[p for p in pend if (getattr(a,'all',False) or p['skill']==getattr(a,'skill',''))]
    if not sel:
        print(f'  无匹配提案（skill={getattr(a,"skill","")}）；--all 可批量')
        return 0                      # ★ 无匹配：直接返回，绝不动 props（防误清其它技能提案）
    bysk={}
    for p in sel: bysk.setdefault(p['skill'],[]).append(p)
    done=0
    _passed_keys=set()         # ★ S182：闸门放行过的提案键，供下面标 done 时比对
    for sk,ps in bysk.items():
        d=os.path.join(SKILLS,sk)
        if not os.path.exists(os.path.join(d,'SKILL.md')):
            print(f'  ⚠️ 技能不存在，跳过：{sk}'); continue        # 复用 upgrade 的版本提升+CHANGELOG 逻辑
        cmd_upgrade(argparse.Namespace(name=sk,strict=False,force=True))
        # ★ 修正：upgrade 默认 append 到文件末尾，但生态 CHANGELOG 是**倒序**（新段在前），
        #   追加会让新段沉底被漏看。此处把刚 append 的段摘出来、插回顶部。
        cl=os.path.join(d,'CHANGELOG.md')
        try:
            txt=open(cl,encoding='utf-8').read()
            # 兼容两种段标题：`## 2026-10-18 v1.2.3`（upgrade 写的）与 `## v1.2.3 — 说明`（人工写的）
            m=list(re.finditer(r'(?m)^## (?:\d{4}-\d{2}-\d{2} )?v[\d.]+', txt))
            if m:
                seg_start=m[-1].start()
                tail=txt[seg_start:]
                if tail.strip() and '自动提升' in tail:
                    head=txt[:seg_start].rstrip()+'\n'
                    # 补写"升级依据"到该段内
                    lines=[f'- 依据 M-R111 提案：{p["what"]}（{p["why"]}）' for p in ps]
                    newseg=tail.rstrip()+'\n'+'\n'.join(lines)+'\n'
                    # 插到文件最顶部（倒序 CHANGELOG 的最新位置）
                    txt2=newseg+'\n---\n\n'+head
                    open(cl,'w',encoding='utf-8').write(txt2)
                    print(f'    ↪ CHANGELOG 新段已置顶（倒序规范）')
                else:
                    # 未找到刚追加的段（可能 CHANGELOG 缺失/格式特殊）→ 直接用 append 兜底
                    open(cl,'a',encoding='utf-8').write(
                        '\n'.join(['']+[f'- 依据 M-R111 提案：{p["what"]}（{p["why"]}）' for p in ps])+'\n')
                    print('    ↪ 未识别到升级段，依据已追加到末尾（请人工核对位置）')
            else:
                open(cl,'a',encoding='utf-8').write(
                    '\n'.join([f'- 依据 M-R111 提案：{p["what"]}（{p["why"]}）' for p in ps])+'\n')
        except Exception as e:
            print(f'  ⚠️ CHANGELOG 置顶失败（{type(e).__name__}），段已追加在末尾，请人工搬移')
        # ★ S182：标 done **之前**先回读，没落地的不许记成已成事实。
        _ok_ps, _bad_ps = _gate_moves(ps)
        for p in _ok_ps:
            p['status']='done'
            _passed_keys.add(_prop_key(p))
        for p in _bad_ps:
            # 退回 pending：它本来就还没吸收完，只是"升了版本"而已
            p['status']='pending'
            p['landed_blocked']=f'{p.get("landed")}：{p.get("landed_detail")}'
        if _bad_ps:
            print(f'    ⛔ {len(_bad_ps)} 条提案**未落地**，已退回 pending（不计入已吸收）：')
            for p in _bad_ps:
                print(f'       · [{p.get("ref")}] {p.get("landed")} — {p.get("landed_detail")}')
        _doc_ps=[p for p in _ok_ps if p.get('landed')=='doc']
        if _doc_ps:
            print(f'    ℹ️ {len(_doc_ps)} 条仅在文档出现（脚本无实现），标记 landed=doc')
        done+=len(_ok_ps)
        print(f'  ✅ {sk} 已升级，吸收 {len(_ok_ps)} 条提案'
              + (f'（{len(_bad_ps)} 条未落地已退回）' if _bad_ps else ''))
    # ★ 只把**本次处理过**的提案标 done 并清出待办；其余技能/未处理的 pending 原样保留。
    #   （旧实现 `status not in ('pending','rejected')` 会把所有没处理的提案一起删——假绿式清账，已修）
    #
    # ★★ S182：**这段曾是闸门的漏洞**。原实现「凡是选中的、状态还是 pending 的，一律标 done」
    #   —— 而闸门刚把没落地的退回成 pending，下一行就被这里重新标回 done。
    #   等于闸门白装：只要闸门写 `pending`，这段立刻把它的成果抹掉。
    #   修法：只对闸门**放行过**的键（`_passed_keys`，上面循环里已收集）标 done；
    #   被退回的保留 pending 且带着 blocked 原因，下轮 propose/commit 能看见，而非静默消失。
    #   ★★ 再修（S182 第二处）：原句用了 `sel_ids` —— 这个变量在本函数里**从未定义**，
    #   一旦有提案走到这行必然 NameError（py_compile 查不出来，只有真跑才炸）。
    #   选中的集合本来就是 `sel`，直接用它。
    _sel_keys={_prop_key(p) for p in sel}
    for p in items:
        if _prop_key(p) in _sel_keys and p.get('status') == 'pending' and _prop_key(p) in _passed_keys:
            p['status'] = 'done'
    props['items']=[p for p in items if p.get('status')!='rejected']
    _save_props(props)
    print(f'\n  共执行 {done} 条（授权=你调用本命令）；其余未选中提案保持不变')
    note(f'commit: 执行 {done} 条升级提案')
    return 0


def cmd_cross_learn(a):
    """★ 技能间互相学习：把某技能（默认最新沉淀）的招式推送给他技能，进待授权队列。"""
    props=_load_props()
    mvid=getattr(a,'move','')
    if not mvid:
        print('  ⚠️ 需指定 --move <招式编号，如 T-I>'); return 1
    spec=SEED_MOVES.get(mvid)
    if not spec:
        print(f'  ⚠️ 招式库无 {mvid}（现有：{", ".join(SEED_MOVES.keys())}）'); return 1
    q=props.setdefault('cross_queue',[])
    added=0
    for sk in _all_skills():
        if sk in MOVE_NOISE or not getattr(a,'force',False):
            # 默认只推给"尚未用过该招式"的技能
            pass
        try:
            used,unused,_= _lend_rows(sk)
        except Exception:
            continue
        if unused is None:  # 无法判定（M-R60）→ 不臆测
            continue
        if mvid in used:
            continue
        if any(x['to']==sk and x['move']==mvid for x in q):
            continue
        q.append({'to':sk,'move':mvid,'name':spec['name'],
                  'why':f'跨技能学习：{spec["cond"]} 场景可借鉴',
                  'created':time.strftime('%Y-%m-%d %H:%M')})
        added+=1
    _save_props(props)
    print(f'✅ 跨技能学习：招式 [{mvid}] {spec["name"]} → 推送给 {added} 个未用技能（进待授权队列）')
    print('   ↪ 生成提案：evolve.py propose')
    note(f'cross-learn: {mvid} -> {added} skills')
    return 0


def cmd_upgrade(a):
    d=os.path.join(SKILLS,a.name)
    sk=os.path.join(d,'SKILL.md'); cl=os.path.join(d,'CHANGELOG.md')
    if not os.path.exists(sk):
        # ★ S177：原来写在同行 `print(...); return` —— 裸 return 隐式 rc0，
        #   于是「技能不存在」这种明确错误被记成成功。且同行写法逃过了逐行扫，
        #   说明"扫一遍就以为没有"本身不可靠（扫的规则漏了形态）。
        print(f'⚠️ 技能不存在：{d}')
        return 2      # 用法错：用户给的名字不存在（与 conductor 未知命令同口径）
    # ★ 规则57 前置：升级前经验对照（软阻断；--strict 硬阻断；--force 绕过）
    n_app=_lend_precheck(a)
    if n_app is None:
        pass                                   # 三态：无法判定 → 不阻断（已告警）
    elif n_app>0 and getattr(a,'strict',False) and not getattr(a,'force',False):
        print(f'⛔ --strict：{a.name} 有 {n_app} 条可借鉴招式未确认，禁止升级。')
        print(f'   确认借鉴请加 --force；不适用请先在 CHANGELOG 声明「不适用 T-X（原因）」。')
        return 1     # S177：原裸 return→rc0。--strict 门禁拦下，属「跑了但不通过」
    fm=parse_fm(sk)
    old=fm.get('version','1.0.0'); new=bump(old)
    # 更新 frontmatter version
    s=open(sk,encoding='utf-8').read()
    s2=re.sub(r'(?m)^version:\s*["\']?[0-9.]+["\']?', f'version: "{new}"', s, count=1)
    if s2==s:  # 没有 frontmatter version，补一行
        s2=s.replace('---\n', f'---\nversion: "{new}"\n',1)
    open(sk,'w',encoding='utf-8').write(s2)
    # 追加 CHANGELOG
    today=time.strftime('%Y-%m-%d',time.gmtime())
    entry=f'\n## {today} v{new}\n- 由 skill-evolution upgrade 自动提升（{old} → {new}）\n'
    if os.path.exists(cl):
        open(cl,'a',encoding='utf-8').write(entry)
    else:
        open(cl,'w',encoding='utf-8').write(f'# {a.name} CHANGELOG{entry}')
    # 同步生态库 version
    try:
        for r in rows_of(DB_ECO):
            if r.get('技能名')==a.name:
                break
    except Exception: pass
    print(f'✅ {a.name}: {old} → {new}（SKILL.md + CHANGELOG 已更新）')
    note(f'upgrade {a.name}: {old} -> {new}')
    register_eco(a.name, fm.get('description',''), fm.get('description',''), version=new)
    # ★ 生态联动（S106）：
    #   ① 升级后旧版快照/被取代的文档节点 AI 删不掉 → 投递清道夫收件箱
    #   ② 进化舵发现新退化模式（质量短板类别）→ 登记监察司，纳入下轮 balance/scan
    try:
        # S156 修：原在这里向清道夫投递「{name} 旧版快照（…）」——那是**描述文字不是 node_id**，
        #   且技能升级改的是本地 SKILL.md/CHANGELOG，**并不产生云端节点**，清道夫无从处理。
        #   结果只是往待删清单里塞了一条永远移不动的假账（见 janitor「未移动」列）。
        #   正确做法：无云端节点可投就不投，别造账。
        # ★ 1018 修（M-R111 实测踩到的坑）：「升级后回退风险」**没有**自动检查器，
        #   必须挂 `manual_track`（人工复核），不能照抄 `naming_compliance`——
        #   后者扫的是 /workspace 文件名，会把正常文档误报成"遗留"（M33 就是这么制造的 6 条假遗留）。
        #   这正是 inspect_engine.register_monitor docstring 里 S136 记录的同一个污染源。
        register_monitor(f'{a.name} 升级后回退风险', 'manual_track', f'{a.name} 升级到 {new} 后若出现功能回退，升级后 7 天内人工复核')
    except Exception as e:
        print(f'  ⚠️ 生态门面联动跳过（{type(e).__name__}）：版本已升，不影响升级结果')

def build_parser():
    ap=argparse.ArgumentParser(prog='evolve.py')
    sub=ap.add_subparsers(dest='cmd',required=True)
    rc=sub.add_parser('recurring'); rc.add_argument('--min',type=int,default=3)
    # ★ S159 已解决归档：让经验池只统计「还欠着的」，已根治的退出聚类
    sv=sub.add_parser('solved'); sv.add_argument('ids',nargs='*')
    sv.add_argument('--ledger',action='store_true',help='操作③台账而非④经验库')
    sv.add_argument('--state',default='已解决',choices=['已解决','已固化'])
    sv.add_argument('--list',action='store_true',help='只列已归档的，不改动')
    ct=sub.add_parser('create'); ct.add_argument('name'); ct.add_argument('--trigger',required=True)
    ct.add_argument('--desc',required=True)
    sub.add_parser('review')
    nt=sub.add_parser('note'); nt.add_argument('--what',required=True)
    sub.add_parser('standing')   # M-R108：用户偏好持久指令清单（会话开工核对）
    sc=sub.add_parser('scan'); sc.add_argument('--path',default='')
    up=sub.add_parser('upgrade'); up.add_argument('name')
    up.add_argument('--strict',action='store_true')   # ★ 规则57：硬阻断（CI/终验用）
    up.add_argument('--force',action='store_true')    # 确认借鉴后绕过 strict
    # ★ S107 经验对照·招式库（规则57）
    mx=sub.add_parser('mine')
    mx.add_argument('--min',type=int,default=2)       # 跨≥N技能才算通用
    mx.add_argument('--dry',action='store_true')      # 只审计不落库
    mx.add_argument('--skill',default='')             # 只扫某技能（增量抽取）
    ld=sub.add_parser('lend'); ld.add_argument('name')
    ld.add_argument('--own',action='store_true')      # 只列未用（升级前必查）
    ld.add_argument('--json',action='store_true')
    rl=sub.add_parser('review-lend'); rl.add_argument('--since',default='')
    # ★ M-R111 经验池驱动升级闭环
    pr=sub.add_parser('propose')
    pr.add_argument('--skill',default='')            # 只对某技能出提案
    pr.add_argument('--min',type=int,default=3)      # 复发聚类阈值
    cm=sub.add_parser('commit')
    cm.add_argument('--skill',default='')            # 授权升级某技能
    cm.add_argument('--all',action='store_true')     # 批量授权
    cm.add_argument('--reject',default='')           # 驳回提案序号（逗号分隔）
    cl=sub.add_parser('cross-learn'); cl.add_argument('--move',required=True)  # 跨技能学习招式
    pt=sub.add_parser('patch')                        # S182 第①档：只生成补丁，不改技能
    pt.add_argument('--skill',default='')
    pt.add_argument('--out',default='')
    pt.add_argument('--no-scan',action='store_true',
                    help='只查提案清单（旧口径），不做全量五态扫描')
    ap_=sub.add_parser('apply')                       # S182 第②档：受控写入（默认 dry-run）
    ap_.add_argument('skill')
    ap_.add_argument('rel')
    ap_.add_argument('old')
    ap_.add_argument('new')
    ap_.add_argument('--yes',action='store_true')
    return ap

AP=build_parser()
DISPATCH={'recurring':cmd_recurring,'solved':cmd_solved,'create':cmd_create,'review':cmd_review,'note':cmd_note,
          'standing':cmd_standing,
          'scan':cmd_scan,'upgrade':cmd_upgrade,
          'mine':cmd_mine,'lend':cmd_lend,'review-lend':cmd_review_lend,
          'propose':cmd_propose,'commit':cmd_commit,'cross-learn':cmd_cross_learn,
          'patch':cmd_patch,'apply':cmd_apply}

def dispatch(argv=None):
    """供调度器 tvbox 在同进程内直接调用，共享 core 缓存与并行池。"""
    a=AP.parse_args(argv)
    # ★ S177：原来不接返回值 → 上面所有命令的 return 1/2/3 **全被丢进垃圾桶**，
    #   入口一律 exit=0。这是「判定算对了却没交到下游」的同族错误里最典型的一处：
    #   link.py 早修成了 `return DISPATCH[...](a)` 并写注释"一句 return 就够"，
    #   而这里同一个位置一直是裸调用——**同类缺陷补了一处漏了另一处**。
    #   入口 `_as_exit_code` 已能处理 bool/int，这里只负责把值送出去。
    return DISPATCH[a.cmd](a)

def main():
    # ★ S177：`dispatch()` 的返回值原来被丢弃 → 直接 `python3 xxx.py` 跑时恒 rc=0。
    #   同一处病在 dispatch 里修过，但**出口这一层没修，等于白修**——
    #   经 `tvbox` 入口调用时值能传出去，直接跑脚本时不能，两条路径行为不一致。
    #   M-R63 的变体：修了上游、漏了下游，检测时只看上游就会以为已经好了。
    return dispatch()

if __name__=='__main__': sys.exit(main() or 0)
