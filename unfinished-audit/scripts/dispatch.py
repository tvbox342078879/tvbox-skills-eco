#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清账司·分诊派单与总账（S136）

三件事：
  ① 分诊：给每条欠账定 owner（监察官）+ next（可执行命令）+ due（期限）
  ② 落账：写 unfinished_ledger.json（唯一欠账总账），**写后回读**
  ③ 对账：与上期账本比对 → 新增 / 存续 / 消失；消失未收口 = 告警（M-R82）

为什么分诊要落到账本里而不是只打印：**打印出来没人看，写进账本才会被 verify 查**。
这就是 M-R79「未完成不得无主」的强制力来源。
"""

import datetime
import json
import os
import re

import collect

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(os.path.dirname(HERE), 'unfinished_ledger.json')
SKILL_ROOT = collect.SKILL_ROOT

# 期限策略（天）：按性质给出「合理多久必须处置」
DUE_DAYS = {
    'code':       7,
    'route':      7,
    'rule':      14,
    'datasource': 7,
    'library':    7,
    'monitor':   14,
    'cloud':      7,
    'unknown':   14,
}

# 分诊表：性质 → (监察官, 下一步命令模板)
# **每条都必须落到一个真实存在的技能**——兜底不许留空（M-R79）。
ROUTE_TABLE = {
    'code':       ('legacy-watchdog',  'tvbox watchdog scan'),
    'route':      ('ecosys-conductor', 'tvbox conductor onboard <技能名>'),
    'rule':       ('skill-evolution',  'tvbox evolve upgrade <技能名>'),
    'datasource': ('probe-hub',        'tvbox probe monitor status'),
    'library':    ('tvbox-source-hub', 'tvbox status'),
    'monitor':    ('tvbox-inspect',    'tvbox inspect signal-check'),
    'cloud':      ('ecosys-conductor', 'tvbox status'),
    'unknown':    ('ecosys-conductor', 'tvbox conductor issues'),
}

# 细粒度覆盖：某些欠账光看性质会派错，按关键词更精确
FINE_ROUTE = [
    (('监测项', '检查器有效', 'proven'), 'tvbox-inspect', 'tvbox inspect signal-check'),
    (('云端', '节点', '删不掉', '收件箱'), 'tvbox-janitor', 'tvbox janitor scan'),
    (('slim_master', '可外置候选', '总册', '索引制'), 'ecosys-conductor', 'tvbox conductor slim <技能名> --diagnose'),
    (('触发词', '_SKILL_INTENTS', '路由'), 'ecosys-conductor', 'tvbox conductor onboard <技能名>'),
]


def _today():
    return datetime.date.today()


def _assign(item, today=None):
    """给单条欠账定 owner/next/due。返回 item（原地补字段）。"""
    today = today or _today()
    kind = item.get('kind') or 'unknown'
    owner, nxt = ROUTE_TABLE.get(kind, ROUTE_TABLE['unknown'])
    blob = f"{item.get('title', '')} {item.get('evidence', '')}"
    for words, o, n in FINE_ROUTE:
        if any(w in blob for w in words):
            owner, nxt = o, n
            break
    item['owner'] = owner
    item['next'] = nxt
    item['due'] = (today + datetime.timedelta(days=DUE_DAYS.get(kind, 14))).isoformat()
    # 无主自检：M-R79 三重必填
    miss = [k for k in ('owner', 'next', 'due') if not item.get(k)]
    if miss:
        raise RuntimeError(f'分诊失败，{item.get("sid")} 缺 {miss}——禁止落账无主项')
    return item


def _load_ledger():
    """三态（M-R60）：不存在=首期空账；损坏=显式报错，**不折叠成空账**
    （折叠成空账会让「账本坏了」看起来像「全清了」——本技能最不能犯的错）。"""
    try:
        with open(LEDGER, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {'version': 1, 'updated': '', 'items': [], 'closed': []}
    except Exception as e:
        raise RuntimeError(f'unfinished_ledger.json 损坏（{type(e).__name__}: {e}）'
                           f'——无法判定欠账状态，勿当作「已清零」')


def _save_ledger(db):
    """写账本 + **写后回读**（M-R71 自证全同步）：不回读就宣称写成功 = 假绿。"""
    db['updated'] = _today().isoformat()
    tmp = LEDGER + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    os.replace(tmp, LEDGER)
    back = json.load(open(LEDGER, encoding='utf-8'))
    if len(back.get('items', [])) != len(db.get('items', [])):
        raise RuntimeError(f'落账回读失败：预期 {len(db.get("items", []))} 条，'
                           f'实际 {len(back.get("items", []))} 条')
    return back


def reconcile_echo(db):
    """回声回填（S136）：读各技能 backlog 里的派单副本，把**对方已标完成**的项反映到总账。

    为什么需要：采集端已把副本跳过（否则账本自我增殖），但副本里带着真实进展——
    认领方做完后通常在**自己**的 backlog 上加 `[已清零 2026-xx-xx：证据]`。
    若完全不看副本，那条欠账就只能等总账侧显式 `close`，容易两边不同步：
    对方说做完了，总账还挂着，下轮又派一遍单（**重复骚扰**）。

    口径（保守）：
      · 副本标了完成标记 → 总账原项**不自动判绿**，只置 `echo_done` 标记 + 证据，
        仍要求 `close <sid> <证据>` 显式收口（M-R82：只有显式收口能变绿）。
        理由：别人说做完了 ≠ 我验证过做完了。
      · 副本被删了 → 不做任何推断（可能是手滑清表，也可能是重建）。
    返回 {sid: evidence} 供汇报展示「认领方称已完成，待你确认收口」。
    """
    import re as _re
    hint = {}
    for it in db.get('items', []):
        if it.get('status') != 'open':
            continue
        sid = it.get('sid')
        owner = it.get('owner') or ''
        fp = os.path.join(SKILL_ROOT, owner, 'scripts', 'flow.json')
        if not (sid and os.path.exists(fp)):
            continue
        try:
            d = json.load(open(fp, encoding='utf-8'))
        except Exception:
            continue          # 读失败不推断（≠"没做完"），交 verify_ledger 判
        for b in d.get('backlog', []):
            s = str(b)
            if sid not in s:
                continue
            m = _re.search(r'[\[（(]\s*(?:已清零|已兑现|已完成|已闭环)[^\]）)]*[\]）)]', s)
            if m:
                it['echo_done'] = True
                it['echo_evidence'] = s[m.start():m.start() + 160]
                hint[sid] = it['echo_evidence']
    _save_ledger(db)
    return hint


def sync(items, today=None):
    """把采集结果并入总账。返回 dict：
    {added:[], kept:[], vanished:[], closed_pending:[]}
      added    本期新出现
      kept     上期在册、本期仍在（更新 last_seen）
      vanished 上期在册、本期采集不到，**且未被显式收口** → 告警（M-R82）
    """
    today = (today or _today()).isoformat()
    db = _load_ledger()
    old = {it['signature']: it for it in db.get('items', [])}
    # ★ S163 修（真实缺陷，实测复现）：**已结区必须参与去重**。
    #   原实现只看 `db['items']`，于是「已经 close done 的条目」只要**源还在**
    #   （flow.backlog 里那条 [已清零] 副本仍在、CHANGELOG 那段仍在），下次 sync
    #   就把它当成**新增**重新写回 items 并把 status 置回 open。
    #   实测症状：`close done b5521ff2acde` 打印「已移入已结区」，下一次 `close status`
    #   它又出现在「未结」里；同一 sid 同时存在于 items(open) 和 closed(done)。
    #   这正是本文件 close_item 里 S136 注释描述过的「状态回滚 open」，但那次只补了
    #   已结区**去重**，没补**采集侧的去重依据**——堵了半边。
    #   口径：已结即已结。除非显式重开（另设 reopen），采集不得把 done/wontfix 拉回 open。
    closed_sigs = {c.get('signature') for c in (db.get('closed') or []) if c.get('signature')}
    seen = set()
    added, kept = [], []
    for it in items:
        _assign(it, _today() if today is None else datetime.date.fromisoformat(today))
        sig = it['signature']
        seen.add(sig)
        if sig in closed_sigs:
            # 已结项回魂：源还在，但它已经收口了 → 跳过，不重开
            continue
        if sig in old:
            o = old[sig]
            o['last_seen'] = today
            o['due'] = it['due']           # 期限随性质重算（属性可能被修正）
            o['owner'] = it['owner']       # 分诊规则改了也要跟
            o['next'] = it['next']
            o['title'] = it['title']
            kept.append(o)
        else:
            it.update({'status': 'open', 'first_seen': today, 'last_seen': today})
            added.append(it)
    vanished = [o for sig, o in old.items() if sig not in seen and o.get('status') == 'open']
    # S136 实测踩到的坑：原实现是 `db['items'] = kept + added`——**整体覆盖**。
    # 后果：任何一次只喂部分数据的调用（探针注入、单源补采、手工 sync）都会把
    # 「本次没采到的」条目**从账本里删掉**，而且不报错。清账司自己犯了它要防的错：
    # 账不是靠收口清的，是靠"这次没采到"清的。
    # 正确做法：**保留所有未结项**，只在采到时刷新；vanished 单独告警，绝不自动删除。
    keep_ids = {o['signature'] for o in kept} | {o['signature'] for o in added}
    carry = [o for o in old.values() if o['signature'] not in keep_ids and o.get('status') == 'open']
    db['items'] = sorted(kept + added + carry,
                         key=lambda x: (x.get('due', ''), x.get('owner', '')))
    _save_ledger(db)

    # 派单**真送达**（S136）：只写在自己账本里叫「记下来了」，不叫「派出去了」。
    # 这里把每条新增欠账推进 owner 的 flow.json backlog——认领方下次跑自己的命令就看得见。
    # 失败不阻断（旁路），但会计入 dispatch_fail 被打印出来，不静默。
    dispatched, fail = [], []
    for it in added:
        try:
            import link_eco
            if link_eco.notify_owner(it.get('owner') or 'ecosys-conductor', it):
                dispatched.append(it.get('sid'))
            else:
                fail.append(it.get('sid'))
        except Exception as e:
            fail.append(f"{it.get('sid')}({type(e).__name__})")
    return {'added': added, 'kept': kept, 'vanished': vanished,
            'dispatched': dispatched, 'dispatch_fail': fail}

def close_item(sid_or_sig, evidence, status='done', note=''):
    """显式收口：状态置 done/wontfix/defer，**必须带证据**。
    空证据直接拒绝——「口头说做完了」不算（这是本技能防自欺的最后一道门）。"""
    if not evidence or not evidence.strip():
        raise RuntimeError('收口必须带证据（做到了什么/在哪看到/哪个命令验证的），不接受空口')
    db = _load_ledger()
    hit = None
    for it in db.get('items', []):
        if it.get('sid') == sid_or_sig or it.get('signature') == sid_or_sig:
            hit = it
            break
    if hit is None:
        raise RuntimeError(f'账本里找不到 {sid_or_sig}（可用 close status 看全部 sid）')
    hit['status'] = status
    hit['closed_at'] = _today().isoformat()
    hit['close_evidence'] = evidence.strip()
    if note:
        hit['note'] = note
    if status in ('done', 'wontfix'):
        db['items'] = [x for x in db['items'] if x is not hit]
        # **已结区幂等**（S136 实测踩坑）：这里原来是无条件 append。当同一条欠账
        # 先被 close、后来又被采集按「新增」写回 items（状态回滚 open）、再被 close
        # 一次，closed 里就留下**两条同 sid 记录**——账本看着 8 条已结，实际只有 5 件
        # 事，而且重复项的 evidence 还是旧的。清账司自己也必须守「账要能清」（M-R82）：
        # 同 sid 只保留最后一次收口，旧的那条被覆盖而不是并排堆着。
        closed = db.setdefault('closed', [])
        closed[:] = [x for x in closed if x.get('signature') != hit.get('signature')]
        closed.append(hit)
    _save_ledger(db)
    # ★ S156：收口后**回调 owner 的 backlog 副本**（原缺失）。
    #   症状（实测）：M34 欠账已 done、已进已结区，但它当年派单时写进
    #   tvbox-inspect/flow.json backlog 的那条副本**原样留着**，于是 M03
    #   「backlog 未清零」照报——一条已经做完的事，在监控里永远是遗留。
    #   两条记录各说各话，人只能二选一信一个：账本说做完了，backlog 说没做。
    #   口径：收口即把副本标成 `[已清零 <日期> <sid>：证据摘要]`，
    #   **不删除**——删除会让"这件事曾经派过单"这段历史消失（M-R79 反对无声消失）。
    if status in ('done', 'wontfix'):
        _echo_close_to_backlog(hit)
    return hit


def _echo_close_to_backlog(item):
    """把已收口的欠账回写成 owner backlog 里的完成标记（S156 / S158 八补·九补）。

    三态：flow.json 不存在/读失败 → **不猜**，只告警（不能因为回写失败就说"没做成"，
    也不能静默当作已回写——否则下次 M03 又报同一条，而没人知道是回写没成功）。

    两条写入路径、两套回读证据（S158 九补定型）：
      · sid 命中 → 按 sid 写、按 sid 回读（独立证据＝sid 本身）
      · 标题兜底 → 按标题写、按「已结条目数增量」回读（不能拿标题再匹配一次验自己＝自证）
    """
    sid = item.get('sid')
    owner = item.get('owner') or ''
    # ★ S158 九补（根因）：原句是 `if not (sid and owner): return`。
    #   它把「无 sid」和「无 owner」当同一种情况，**直接 return，一声不吭**。
    #   于是野生 backlog（从没被采集过、因而没有 sid）收口时：不写、不报、不提示，
    #   人以为收口了，M03 下一轮照旧报它没清。
    #   我上一版 fallback 写了却测不出效果，真正原因就是——**兜底代码在 return 后面，永远走不到**。
    #   现在：无 sid 允许继续（走标题兜底）；无 owner 才真没法定位副本，必须喊出来。
    if not owner:
        print('  ⚠️ 回写 backlog 跳过：本条欠账无 owner，无法定位副本在谁的 flow.json——请人工核对')
        return
    fp = os.path.join(SKILL_ROOT, owner, 'scripts', 'flow.json')
    if not os.path.exists(fp):
        print(f'  ⚠️ 回写 backlog 跳过：{fp} 不存在（欠账已收口，副本仍留在原处）')
        return
    try:
        d = json.load(open(fp, encoding='utf-8'))
    except Exception as e:
        print(f'  ⚠️ 回写 backlog 跳过：flow.json 读取失败（{type(e).__name__}）——'
              f'欠账已收口，但 M03 可能仍报这条')
        return

    bl = d.get('backlog') or []
    ev = (item.get('close_evidence') or '')[:60].replace('\n', ' ')
    stamp = item.get('closed_at') or _today().isoformat()
    tag = '已清零' if item.get('status') == 'done' else '已归档(不修)'
    _DONE_K = ('已清零', '已归档', '已兑现', '已闭环', '已完成')   # 前后口径必须同一组
    min_len_title = 12      # 归一化后短于 12 字的标题不足以判定"同一件事"，宁可不标
    changed = 0
    _done_before = sum(1 for x in bl if any(k in str(x) for k in _DONE_K))
    _ta = _norm_text(item.get('title') or '')

    # ★ S158 八补：无 sid 条目的回写盲区（实测：probe-hub/flow.json backlog[12]）。
    #   原先只按 sid 匹配 → **野生 backlog（从没被采集过、无 sid）收口后原样挂着**，
    #   M03 永远报它未清，人只能手工标。
    # ★ S158 九补：原来 sid 命中后 `continue`，于是同一条正文的**另一份副本**
    #   （带 sid 的派单条目 + 无 sid 的野生条目各存一份）只标得掉带 sid 的那份，
    #   野生那份留着照旧报。现在两条路都走：sid 命中即标、不拦后面，其余条目再比标题。
    #   阈值 55% 不放松——标错了比标不掉更糟，宁可漏（保持原样并提示人工）也不猜。
    for i, b in enumerate(bl):
        s = str(b)
        if any(k in s for k in _DONE_K):
            continue
        _tb = _norm_text(s)
        _ba, _bb = _bigrams(_ta), _bigrams(_tb)
        _ov = (2 * len(_ba & _bb) / (len(_ba) + len(_bb))) if (_ba and _bb) else 0.0
        if sid and sid in s:
            # ★ S158 九补（承上，事故教训）：**sid 命中不等于标对了**。
            #   实测用例：账本里标题是「jar 抽取链路缺失」，被标到了 owner backlog 里
            #   「别的一句完全无关的欠账」上，只因为那一条带着这个 sid。
            #   sid 是**账本与副本的关联键**，不是"内容对不对"的证明——关联键会写错、会被复用。
            #   故：sid 命中**且**标题不像 → 不标，喊出来让人看。
            #   阈值 0.30 明显低于兜底阈值 0.55：sid 已经是强证据，这里只拦"完全不像"。
            if len(_ta) >= min_len_title and _ov < 0.30:
                print(f'  ⚠️ 跳过一条：{owner} backlog 里带 sid={sid} 的条目与账本标题几乎无关'
                      f'（重叠 {_ov:.0%}）——sid 可能误关联，**未标注**，请人工核对')
                continue
            bl[i] = f'[{tag} {stamp}]（sid={sid}）{ev} ‖ 原条目：{s}'
            changed += 1
            continue        # 本条已处置，换下一条
        if len(_ta) < min_len_title:
            continue
        if _ov >= 0.55:
            bl[i] = (f'[{tag} {stamp}]（无 sid·标题匹配{_ov:.0%}）{ev} ‖ 原条目：{s}')
            changed += 1

    if not changed:
        _why = f'sid={sid} 在 backlog 里找不到' if sid else '本条无 sid'
        print(f'  ⚠️ 回写 backlog 跳过：{owner} 的 backlog 里找不到本条副本'
              f'（{_why}，标题重叠也 <{min_len_title} 字/55%）——请人工核对是否本就没派过单')
        return

    d['backlog'] = bl
    try:
        # ★ S158 九补（真实 bug，非测试噪声）：原地 `json.dump(d, open(fp,'w'))` 后立刻回读，
        #   读到的极可能是**半截文件**——`open(fp,'w')` 先把文件截断，json.dump 往里写，
        #   但那个 file 对象没有绑定变量、靠 GC 关；CPython 里通常马上回收，但**回读发生在回收之前**
        #   就成了读自己写到一半的内容。实测：回读稳定报 JSONDecodeError（Extra data）。
        #   修法：with 块保证关闭+flush，再用 os.replace 原子替换（与 _save_ledger 同一姿势）。
        _tmp = fp + '.tmp'
        with open(_tmp, 'w', encoding='utf-8') as _f:
            json.dump(d, _f, ensure_ascii=False, indent=1)
        os.replace(_tmp, fp)
    except Exception as e:
        print(f'  ⚠️ 回写 backlog 失败（{type(e).__name__}）——请在 {fp} 手工标记')
        return

    # 写后回读（T-I：写了≠写成了）
    # ★ S157：这里原来 `except: ok = False` 被判为折叠候选。判断后**不是折叠**：
    #   ok=False 的语义是「**回读未确认**」，接着立刻打印 ⚠️ 要求人工核对——
    #   它没有被当成"回写成功"或"回写失败"任何一方的结论，只是把不确定性说出来。
    #   原变量名 `ok` 骗人（读起来像"回写成功与否"），故改三态变量名 + 显式分支。
    # ★ S158 九补：回读判据必须与**本次实际走的写入路径**一致。
    #   · 本次写过 sid 条目 → 按 sid 回读；按 sid 读不到就是不确定，**不许拿增量兜底充绿**。
    #   · 本次**只**靠标题写入 → 这些条目本就没有 sid，按 sid 判必然"未确认"（那是误报），
    #     改按「已结条目数增量」；用标题再匹配一次验自己＝自证，不行。
    back_state, back_detail = 'unknown', ''
    try:
        back = json.load(open(fp, encoding='utf-8'))
    except Exception as e:
        back_detail = f'回读解析失败（{type(e).__name__}）'
    else:
        _bbl = back.get('backlog', [])
        _sid_written = bool(sid) and any(sid in str(x) and any(k in str(x) for k in _DONE_K)
                                         for x in _bbl)
        if _sid_written:
            back_state = 'confirmed'
        elif sid and any(sid in str(x) for x in _bbl):
            # sid 写在里面但没带完成标记 → 写坏了
            back_detail = f'回读见 {sid} 但无完成标记（写入被截断？）'
        else:
            _done_n = sum(1 for x in _bbl if any(k in str(x) for k in _DONE_K))
            if _done_n >= _done_before + changed:
                back_state = 'confirmed'
            else:
                back_detail = (f'回读已结条目 {_done_n} 条，'
                               f'未达预期的 {_done_before + changed} 条（可能被并发覆盖）')
    if back_state == 'confirmed':
        print(f'  ✅ 已回写 {owner} backlog 副本 {changed} 条（回读确认）')
    else:
        print(f'  ⚠️ 已回写 {owner} backlog 副本 {changed} 条，但**回读未确认**：{back_detail}')
        print(f'     请在 {fp} 手工核对——勿当作已回写成功')


def _norm_text(s):
    """去掉标签/日期/sid 等噪声，留下可比的正文（S158 六补）。

    必须剥离的东西（实测：不剥就永远比不中）：
      `[遗留 S110]`、`[清账司派单 2026-10-25]`、`（…）`、`sid=xxxx`、标点、空白。
    """
    s = str(s)
    s = re.sub(r'^\[[^\]]*\]', '', s)          # 行首方括号标签（可能叠加，下面循环）
    while True:
        t = re.sub(r'^\[[^\]]*\]', '', s)
        if t == s:
            break
        s = t
    s = re.sub(r'sid\s*[=:]\s*[0-9a-f]+', '', s)
    s = re.sub(r'（[^）]*）|\([^)]*\)', '', s)
    s = re.sub(r'[，。；：、,.;:\s—\-｜|"\'“”‘’]', '', s)
    return s.strip()


def _bigrams(s, n=2):
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else {s}


def _near_duplicate_open(items, thresh=0.42, min_len=12):
    """开放区里「换了个说法重复登记」的可疑对（S158 六补）。

    两个判据，**命中任一即报**：

    ① 结构判据（可靠）：**同 due + 同 next + 不同 owner/skill**
       —— 同一个截止日、同一条下一步命令，却派给了两个技能，几乎必然是同一件事。
       实测案例正是这一类：becf9081fdbc(owner=legacy-watchdog) / 5e79eeb5ba4f(owner=probe-hub)，
       due 同为 2026-10-25。

    ② 文本判据（兜底）：中文 bigram **overlap 系数** 2|A∩B|/(|A|+|B|) ≥ thresh。
       为什么不用 Jaccard：实测这两条 Jaccard=0.26、overlap=0.42，
       因为两条详略不同（一条 87 字、一条 51 字），并集会惩罚长度差——
       **指标选错会把真重复判成不重复，而且是静默的**。
       overlap 对长度差不敏感，适合"一个详述、一个简述"的实际登记习惯。

    只看 status in (open, doing, defer)，且只在**不同技能**之间比
    （同技能内的相似条目多半是细分的两件事，误报率高）。
    """
    open_items = [i for i in items if i.get('status') in ('open', 'doing', 'defer')]
    norm = [(i, _norm_text(i.get('title') or '')) for i in open_items]
    norm = [(i, t) for i, t in norm if len(t) >= min_len]
    warns, seen_pair = [], set()

    def _emit(ia, ib, why):
        key = tuple(sorted([ia.get('sid'), ib.get('sid')]))
        if key in seen_pair:
            return
        seen_pair.add(key)
        warns.append(
            f'疑似重复 {ia.get("sid")}（{ia.get("owner") or "无主"}）≈ '
            f'{ib.get("sid")}（{ib.get("owner") or "无主"}）'
            f' · {why} —— 确认后留一条、另一条 close（附"并入 xxx"）'
        )

    for a in range(len(norm)):
        for b in range(a + 1, len(norm)):
            ia, ta = norm[a]
            ib, tb = norm[b]
            # ★ 比 owner 不比 skill：`skill` 是"哪方写的这条"（写单方），
            #   实测同一个技能派出的 4 条单 skill 全一样，用 skill 过滤等于没过滤；
            #   真正决定"谁该去干"的是 owner。
            _oa = (ia.get('owner') or '').strip()
            _ob = (ib.get('owner') or '').strip()
            if _oa and _ob and _oa == _ob:
                continue          # 同一个 owner 的两条 = 同一人手上两件事，不算互踢
            # ① 结构判据 A：同 due + 同 next（最硬，几乎必然同一件事）
            if (ia.get('due') and ia.get('due') == ib.get('due')
                    and ia.get('next') and ia.get('next') == ib.get('next')):
                _emit(ia, ib, f'同 due={ia.get("due")} 同 next')
                continue
            # ② 结构判据 B：同 due + 不同 owner + 文本有实质重合
            #    为什么这条必要：实测 becf9081fdbc / 5e79eeb5ba4f——
            #    due 同为 2026-10-25，但 next 不同（`watchdog scan` vs `probe monitor status`），
            #    于是判据 A 不成立；而纯文本 overlap 只有 0.42，卡在阈值边上。
            #    **同一个截止日 + 两个不同 owner + 讲的是同一件事**，这三条合起来才是真信号：
            #    它说明同一根因被拆成了两笔，各自排期，谁也收不了尾。
            _same_due = bool(ia.get('due')) and ia.get('due') == ib.get('due')
            ba, bb = _bigrams(ta), _bigrams(tb)
            if ba and bb:
                ov = 2 * len(ba & bb) / (len(ba) + len(bb))
                if _same_due and ov >= 0.35 and _oa and _ob and _oa != _ob:
                    _emit(ia, ib, f'同 due={ia.get("due")} 但 owner 各异（{_oa}/{_ob}），文本重合{ov:.0%}')
                    continue
                # ③ 文本判据（无结构信号时的兜底）
                if ov >= thresh:
                    _emit(ia, ib, f'文本重合{ov:.0%}：{ta[:24]}…')
    return warns


def verify_ledger():
    """欠账单自检：返回 (problems, warnings)。
    problems → verify 判据⑧ 判红；warnings → 只提示不阻断。"""
    problems, warnings = [], []
    try:
        db = _load_ledger()
    except RuntimeError as e:
        return [str(e)], []
    today = _today()
    for it in db.get('items', []):
        if it.get('status') not in ('open', 'doing', 'defer'):
            continue
        miss = [k for k in ('owner', 'next', 'due') if not it.get(k)]
        if miss:
            problems.append(f"无主项 {it.get('sid')}「{it.get('title', '')[:30]}」缺 {'/'.join(miss)}")
        if it.get('status') == 'defer' and not it.get('note'):
            problems.append(f"延期项 {it.get('sid')} 无理由（M-R79 延期须给理由）")
        due = it.get('due') or ''
        try:
            d = datetime.date.fromisoformat(due)
        except Exception:
            continue
        if it.get('status') != 'defer' and d < today:
            warnings.append(f"超期 {it.get('sid')}「{it.get('title', '')[:30]}」due {due} "
                            f"owner {it.get('owner')}")
    # 已结区重复 sid（S136 实测：状态回滚后再 close 会留下同 sid 两条）。
    # 判红而不是告警——重复意味着「已结 8 条」里有 3 条是影子，账目数字本身就不可信了，
    # 而账目数字是清账司唯一的对外交付物。
    _seen = {}
    for c in db.get('closed', []):
        sig = c.get('signature') or c.get('sid')
        _seen[sig] = _seen.get(sig, 0) + 1
    dup = {k: v for k, v in _seen.items() if v > 1}
    for k, v in dup.items():
        problems.append(f'已结区重复 {k} ×{v}（同一欠账被收口多次，证据可能已不是最后一次）')

    # 会话承诺（S136）：**超期未收口 = 硬问题判红**，不只是告警。
    # 为什么比普通欠账更严：承诺只活在上下文里，一旦没落账就彻底消失，
    # 没人会再想起来。若这里也只给警告，等于「答应了不做」零成本。
    try:
        import promises as _pm
        for pid, msg in _pm.stale():
            problems.append(f'承诺未兑现 {pid}：{msg}')
    except Exception as e:
        problems.append(f'承诺账本无法判定（{type(e).__name__}: {e}）——勿当作无承诺')

    # ★ S158 六补：语义重复检测（开放区）。
    #   实测事故：jar 字段缺口被登记成两条——
    #     becf9081fdbc | source=promise | skill=ecosys-conductor | owner=legacy-watchdog
    #     5e79eeb5ba4f | source=backlog | skill=probe-hub        | owner=probe-hub
    #   同一根因、同一 due(2026-10-25)，却派给两个技能 → **两边都以为对方在管**，
    #   比"没人管"更糟。而 signature=sha1(source|skill|title[:40])，
    #   这两条三要素全不同 → 指纹必然不同，现有去重对"换个说法再登记"完全无感。
    #   **只报警告、不自动合并**：合并要先判"哪条是正本"，是语义决策，算法不该替人做。
    warnings.extend(_near_duplicate_open(db.get('items', [])))
    return problems, warnings


def summary_text():
    db = _load_ledger()
    its = db.get('items', [])
    by_owner = {}
    for it in its:
        by_owner.setdefault(it.get('owner', '?'), []).append(it)
    return its, by_owner


if __name__ == '__main__':
    items, errs, stats = collect.collect_all()
    r = sync(items)
    print(f"新增 {len(r['added'])}｜存续 {len(r['kept'])}｜消失未收口 {len(r['vanished'])}")
    for v in r['vanished']:
        print(f"  ⚠️ 消失未收口：{v.get('sid')} {v.get('title', '')[:60]}")
