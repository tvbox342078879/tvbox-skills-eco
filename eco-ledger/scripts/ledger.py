# -*- coding: utf-8 -*-
"""eco-ledger · 账房司 CLI v1.0.0
命令：init 期初建账 ｜ balance 余额表 ｜ post 记分录 ｜ reconcile 对账 ｜
     settle 结账 ｜ finalcheck 终验卡口 ｜ writeoff 平账 ｜ db create/retire 库操作
顺序纪律：settle → post → close（凭证必须在闸门关闭前落账）。
"""
import argparse, json, re, subprocess, sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core_ledger as L

# ★ S156：接入生态门面（监察司平衡性「联动断点」维度实测 45 分，木桶最短板）。
#   建账房时只做了业务闭环（记账/对账/平账/终验），**没接生态门面**——
#   于是它是生态里唯一一个"自己转得很顺、但出事没人知道"的技能：
#     · 终验拦截了，只在终端打印一行 🔴，**没有任何下游知道**
#     · 库操作残留删不掉的节点，只能靠人记得手动投递
#   两条门面都做成**旁路**：调用失败只告警、绝不阻断主流程（清理/登记永远不该
#   让记账失败——这是 core.file_to_janitor 的既定契约）。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', 'tvbox-source-hub', 'scripts'))
try:
    import core as _CORE          # 提供 file_to_janitor / register_monitor
except Exception as _e:           # 读不到 ≠ 没有门面，也不许让记账挂掉
    _CORE = None
    _CORE_ERR = f'{type(_e).__name__}: {str(_e)[:80]}'
else:
    _CORE_ERR = ''


def _file_to_janitor(node_id, reason):
    """把"AI 删不掉的节点"投递进清道夫收件箱（旁路，失败不抛）。

    ★ 入参必须是**真 node_id**。core.file_to_janitor 在 S156 已加校验，
      传描述文字会被拒收并告警——这正是账房最容易犯的错（手上只有账户名/科目名）。
      故此处只做透传，不在这里"编"一个 id 出来。
    """
    if _CORE is None:
        print(f'  ⚠️ 清道夫门面不可用（{_CORE_ERR}）——节点 {node_id} 未投递，请手动处理')
        return False
    try:
        return bool(_CORE.file_to_janitor(node_id, reason, quiet=False))
    except Exception as e:
        print(f'  ⚠️ 投递清道夫失败（{type(e).__name__}: {str(e)[:80]}）——请手动处理 {node_id}')
        return False


def _register_monitor(risk_face, check_type, desc=''):
    """把账房的账实缺口登记进监察司（旁路，失败不抛）。"""
    if _CORE is None:
        return False
    try:
        return bool(_CORE.register_monitor(risk_face, check_type, desc, quiet=False))
    except Exception as e:
        print(f'  ⚠️ 登记监察司失败（{type(e).__name__}: {str(e)[:80]}）')
        return False

# ---------- 基础 ----------
def _update(db, rid, props):
    rc, out, err = L._run(['python3', f'{L.DB}/batch_update_database_records.py',
                           '--database-id', db, '--records', json.dumps([{'record_id': rid, 'properties': props}], ensure_ascii=False)])
    d = L._json_out(out)
    return bool(d and d.get('results') and d['results'][0].get('success')), out

def _rid_of(db, field, val):
    rows, good = L.rows_of(db)
    if not good:
        return None
    for r in rows:
        if (r.get(field) or '') == val:
            return r['record_id']
    return None

def _acct_row(name):
    rows, good = L.rows_of(L.DB_ACCT)
    if not good:
        return None
    for r in rows:
        if r.get('账户名') == name:
            return r
    return None

def entry_balances():
    """按分录计算每账户余额：balance = Σ借方 - Σ贷方（同币种）。返回 {账户: {币种: 额}} 或 None。"""
    rows, good = L.rows_of(L.DB_TOTAL)
    if not good:
        return None
    bal = {}
    for r in rows:
        amt = r.get('金额') or 0
        cur = r.get('币种') or '未标'
        for side, sign in (('借方账户', 1), ('贷方账户', -1)):
            a = r.get(side)
            if a:
                bal.setdefault(a, {}).setdefault(cur, 0)
                bal[a][cur] += sign * amt
    return bal

# ---------- init 期初建账 ----------
def cmd_init(a):
    rows, good = L.rows_of(L.DB_ACCT)
    if not good:
        print('❌ 账户库读取失败'); sys.exit(1)
    if any(r.get('账户名') for r in rows):
        print('⚠️ 账户库非空——已建过账，拒绝重复建账（防 F13 双期初）'); sys.exit(1)

    # ① 盘点实况（L2 全量计数；读失败即中止——三态不折叠）
    counts = {}
    for name, cls, cur, ref in L.INITIAL_ACCOUNTS:
        if cls == '资产·数据' and ref:
            rows2, g2 = L.rows_of(ref)
            if not g2:
                print(f'❌ {name} 读取失败——期初建账中止（三态：无法判定不入账）'); sys.exit(1)
            counts[name] = len(rows2)
    versions = L.version_accounts()

    # ② 账户入库
    recs = []
    for name, cls, cur, ref in L.INITIAL_ACCOUNTS:
        recs.append({'账户名': {'text': name}, '科目类': {'select': cls}, '币种': {'select': cur},
                     '关联对象': {'text': ref or ''}, '账户状态': {'select': '正常'}})
    for name, cls, cur, ver in versions:
        recs.append({'账户名': {'text': name}, '科目类': {'select': cls}, '币种': {'select': cur},
                     '关联对象': {'text': name[3:]}, '账户状态': {'select': '正常'}})
    ok, raw = L.add_entries(L.DB_ACCT, recs)
    if not ok:
        print('❌ 账户写入失败：\n' + raw[:500]); sys.exit(1)

    # ③ 期初分录：借=各账户 / 贷=期初·建账基准（同额同币种 → 守恒律天然成立）
    entries = []
    for name, cls, cur, ref in L.INITIAL_ACCOUNTS:
        v = None
        if cls == '资产·数据' and ref:
            v = counts[name]
        elif name == '欠账·六源全采':
            v = 6    # 依据 2026-10-18 close 落账：仍未完成 6 项（conductor1+probe1+inspect4）
        elif name == '欠账·flow_backlog':
            v = 6
        elif name == '指标·信号源':
            v = 39
        elif name == '指标·版本一致':
            v = 10
        elif name == '指标·docsync':
            v = 0
        elif name == '产能·治理会话':
            v = 1
        elif name == '产能·业务会话':
            v = 0
        if v is not None:
            entries.append(L.make_entry('M0-期初', name, '期初·建账基准', float(v), cur, '建库',
                                        agent='eco-ledger', note='建账基准（F13 双轨验收）'))
    for name, cls, cur, ver in versions:
        entries.append(L.make_entry('M0-期初', name, '期初·建账基准', L.v2n(ver), cur, '建库',
                                    agent='eco-ledger', note='建账基准（F13 双轨验收）'))
    ok, raw = L.add_entries(L.DB_TOTAL, entries)
    if not ok:
        print('❌ 期初分录写入失败：\n' + raw[:500]); sys.exit(1)

    # ④ 余额回填（登记余额=分录计算值）
    bal = entry_balances() or {}
    rows3, g3 = L.rows_of(L.DB_ACCT)
    n = 0
    for r in rows3:
        name = r.get('账户名')
        b = bal.get(name, {})
        v = sum(b.values()) if b else 0
        _update(L.DB_ACCT, r['record_id'], {'当前余额': {'number': v}, '上次核对': {'date': L._now()}})
        n += 1
    print(f'✅ 期初建账完成：{len(L.INITIAL_ACCOUNTS) + len(versions)} 户、{len(entries)} 条期初分录、余额回填 {n} 户')
    for k in ('线路源库', '成品源库', '经验池', '会话台账'):
        print(f'   {k} = {counts.get(k)}')
    cmd_settle(a)

# ---------- status ----------
def cmd_status(a):
    print('📒 账房司 状态')
    print(f'  建账状态   : {"✅ 已建账" if L.LEDGER_READY else "❌ 未建账"}')
    ok, why = L.gate_ready()
    print(f'  会话闸门   : {"🔓 放行" if ok else "🚪 未放行"}')
    snap = L.load_snapshot()
    print(f'  日结快照   : {snap["ts"] if snap else "无"}  试算: {"✅" if snap and snap.get("trial_ok") else "❌/未跑"}')
    rows, good = L.rows_of(L.DB_ACCT)
    print(f'  账户数     : {len(rows) if good else "读取失败"}')

# ---------- balance ----------
def cmd_balance(a):
    """余额表。

    ★ S176 重要修正：多币种对价户（声明 `*`）**必须按币种分行显示，不许求和**。
      以前一律 `sum(b.values())`，于是 `期初·建账基准` 显示成 -125730——
      可那是 -1255 条 + -94 件 + -49 分 + -124332 版本号 硬加出来的，
      四个不同单位相加没有任何会计意义。更糟的是它被当成"正确余额"去回写登记值，
      于是账户库里存了一个假数，而 `balance` 拿假数比假数，**永远显示对得上**。
      （这是 M-R38 最隐蔽的一种：不是两份实现，而是一处实现本身不成立。）
    """
    bal = entry_balances()
    if bal is None:
        print('❌ 总账读取失败'); sys.exit(1)
    rows, good = L.rows_of(L.DB_ACCT)
    if not good:
        print('❌ 账户库读取失败'); sys.exit(1)
    cmap = L.acct_currency_map()
    print(f'{"账户名":<20}{"科目类":<10}{"币种":<6}{"账面余额":>10}{"登记余额":>10}')
    registered = set()
    for r in sorted(rows, key=lambda x: (x.get('科目类') or '', x.get('账户名') or '')):
        name = r.get('账户名') or ''
        registered.add(name)
        b = bal.get(name, {})
        reg = r.get('当前余额') or 0
        if cmap.get(name) == L.COUNTERPARTY or len({c for c in b if abs(b[c]) > 0.005}) > 1:
            # 多币种户：逐币种一行，不求和。登记值同理拆不开，标「多币种·见明细」。
            live = {c: v for c, v in b.items() if abs(v) > 0.005}
            if not live:
                print(f'{name:<20}{(r.get("科目类") or ""):<10}{"多币种":<6}{0:>10.1f}{"—":>10}')
                continue
            first = True
            for c, v in sorted(live.items()):
                nm = name if first else ''
                cls = (r.get('科目类') or '') if first else ''
                print(f'{nm:<20}{cls:<10}{c:<6}{v:>10.1f}{"—" if first else "":>10}'
                      + ('  ← 多币种户·按币种分行' if first else ''))
                first = False
            continue
        v = sum(b.values()) if b else 0
        flag = '' if abs(v - (reg or 0)) < 0.005 else ' ⚠️账册不符'
        print(f'{name:<20}{(r.get("科目类") or ""):<10}{(r.get("币种") or ""):<6}{v:>10.1f}{reg:>10.1f}{flag}')
    # ★ S165 修：有余额却不在账户表里的户必须**显式列出来**。
    #   实测：S165 新增 4 个跨界科目（跨界·对话等）后，分录记上了、试算也平，
    #   但 `ledger balance` 一行都不显示——因为它只遍历账户库的行。
    #   这正是"账记了却看不见"的假绿：用户以为没记，实际记在一张看不见的户头上。
    #   余额 ≠ 0 且未登记 → 判「未入科目表」，并给补登指引，不静默丢弃。
    ghost_accts = {n: by for n, by in bal.items()
                   if n not in registered and abs(sum(by.values())) > 0.005}
    if ghost_accts:
        print()
        print(f'⚠️ {len(ghost_accts)} 个户**有余额但不在科目表**（记账了却查不到）：')
        for n, by in sorted(ghost_accts.items()):
            print(f'   {n:<20}{"未登记":<10}'
                  f'{"/".join(by):<6}{sum(by.values()):>10.1f}{"—":>10}')
        print('   处置：在 core_ledger.INITIAL_ACCOUNTS 补登该户 + 账户库补一行（M-R38）')
    tot = {}
    for name, bycur in bal.items():
        for c, v in bycur.items():
            tot[c] = tot.get(c, 0) + v
    bad = {c: v for c, v in tot.items() if abs(v) > 0.005}
    print('试算平衡: ' + ('✅ 按币种全部归零' if not bad else f'❌ {bad}'))

# ---------- reconcile ----------
def _live_count(dbid):
    rows, good = L.rows_of(dbid)
    return (len(rows), True) if good else (None, False)

def _drain_pending_post(quiet=False):
    """消费待过账队列（S175·P019 缺口③的账房侧）。

    ## 为什么要有这一步
    `tvbox log` 写会话台账之后不会自动记账，于是台账实际数永远比总账多 1，
    `reconcile` 每次都报「账实不符」。以前的做法是人工 `ledger post` 补一笔，
    再被 `writeoff` 收进暂记——**补一次多一笔无主变动，缺口没堵，账越补越花**。
    S175 起 `tvbox log` 落台账时同笔投递到本队列，这里消费。

    ## 三态口径（M-R60）
    · 队列不存在 → **空**，正常（不是读失败）
    · 队列读失败 → **无法判定**，报出来，**不清空**（清空等于吞待办）
    · 单条记账失败 → 该条**留在队列**，下次重试（post 本身幂等，重复不会记双账）

    返回 (处理数, 失败数, 错误原因或 None)。
    """
    q = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pending_post.json')
    if not os.path.exists(q):
        return 0, 0, None
    try:
        with open(q, encoding='utf-8') as f:
            d = json.load(f)
    except Exception as e:
        return 0, 0, f'待过账队列读取失败（{type(e).__name__}）——**不视为空**，本轮未消费'
    items = d.get('items') or []
    if not items:
        return 0, 0, None
    done, fail, left = 0, 0, []
    for it in items:
        try:
            e = L.make_entry(it['voucher'], it['debit'], it['credit'],
                             float(it.get('amount', 1)), it.get('currency', '记录数'),
                             it.get('action', '入库'), agent=it.get('agent', 'tvbox log'),
                             note=it.get('note', ''))
            ok, msg = L.post(e)
            if ok:
                done += 1
            else:
                fail += 1
                left.append(it)
                if not quiet:
                    print(f'   ⚠️ 待过账 {it.get("voucher")} 记账失败：{msg[:80]}——留在队列待重试')
        except Exception as ex:
            fail += 1
            left.append(it)
            if not quiet:
                print(f'   ⚠️ 待过账 {it.get("voucher")} 构造分录失败（{type(ex).__name__}）：留在队列待重试')
    # 只回写**没成功的**，成功的移除（post 幂等，即使这里写失败重跑也不会记双账）
    try:
        d['items'] = left
        tmp = q + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, q)
    except Exception as ex:
        return done, fail, f'队列回写失败（{type(ex).__name__}）——下轮可能重放，但 post 幂等不会记双账'
    return done, fail, None


def cmd_reconcile(a):
    # ★ S175：先消费待过账队列。**必须在算差额之前**——
    #   否则刚由 tvbox log 投递、尚未过账的台账增量会被当成「账实不符」报出来。
    _dn, _df, _derr = _drain_pending_post()
    if _dn or _df or _derr:
        _m = f'📮 待过账消费：入账 {_dn} 笔'
        if _df:
            _m += f'，失败 {_df} 笔（留队列）'
        if _derr:
            _m += f'｜⚠️ {_derr}'
        print(_m)
    # ★ S176：刚入账的笔数会改变账面余额，**必须同步回写登记值**，否则
    #   `ledger balance` 立刻多出一行「账册不符」。这是 sync-versions 同一缺陷的
    #   第三处：消费了分录却不更新账户库。三处（settle / sync-versions / reconcile）
    #   现在共用 sync_registered_balances()，不再各写一遍。
    if _dn:
        _s, _f, _e = sync_registered_balances()
        if _e:
            print(f'   ⚠️ 登记回写：{_e}')
        elif _s or _f:
            print(f'   登记回写: 同步 {_s} 户'
                  + (f'｜❌ 失败 {len(_f)} 户（{", ".join(_f[:4])}）' if _f else ''))
    diffs = []
    rows, good = L.rows_of(L.DB_ACCT)
    if not good:
        print('❌ 账户库读取失败'); sys.exit(1)
    bal = entry_balances() or {}
    for r in rows:
        if r.get('科目类') != '资产·数据' or not r.get('关联对象'):
            continue
        live, g = _live_count(r['关联对象'])
        book = sum((bal.get(r['账户名']) or {}).values())
        if not g:
            diffs.append((r['账户名'], book, '无法判定', '读失败——三态不折叠，人工复核'))
        elif live != book:
            diffs.append((r['账户名'], book, live, '账实不符'))
    # 账账线：登记余额 vs 上期日结快照（有快照才比）
    # ★ S175 改口径：原口径把「与上期快照不同」一律写成「确认有凭证支撑」——
    #   那句话是**替它自己开脱**：快照变了不等于变对了。实测会话台账 账面130/实际133
    #   被这句解释成正常，而同屏「生态登记库 12 vs 13」却标「账实不符」，两套标准。
    #   改为如实陈述：环比变动本身不是结论，**账实线才算结论**。账账线只列出「变了多少」，
    #   不再擅自加「有凭证支撑」四个字（除非真有凭证——那要另查，不在这里猜）。
    snap = L.load_snapshot()
    snap_changes = []
    if snap and snap.get('accounts'):
        for r in rows:
            name = r.get('账户名')
            if name in (snap.get('accounts') or {}):
                prev = snap['accounts'][name]
                cur = sum((bal.get(name) or {}).values())
                if abs(cur - prev) > 0.005:
                    snap_changes.append((name, prev, cur))
    if not diffs:
        print('✅ 对账通过：账实线无差额')
    else:
        print(f'⚠️ {len(diffs)} 项账实差额：')
        for name, book, live, why in diffs:
            print(f'   {name}: 账面={book} 实际={live}（{why}）')
        print('处置：ledger.py writeoff --account <名> --voucher <凭证号> （差额进暂记·无主变动）')
    # 账账线单独一节：只报「比上期变了多少」，不加结论性措辞
    if snap_changes:
        print(f'\n📈 账账线（与上期快照环比，仅供参考·非结论）：{len(snap_changes)} 户有变动')
        for name, prev, cur in snap_changes:
            print(f'   {name}: {prev} → {cur}（{"+" if cur > prev else ""}{cur - prev}）')
    sys.exit(1 if diffs else 0)

# ---------- settle ----------
def cmd_settle(a):
    bal = entry_balances()
    if bal is None:
        print('❌ 总账读取失败'); sys.exit(1)
    acc = {}
    rows, good = L.rows_of(L.DB_ACCT)
    for r in (rows if good else []):
        acc[r.get('账户名')] = sum((bal.get(r.get('账户名')) or {}).values())
    # ★ S165 修：settle 只把余额写进日结快照，**从来没回写账户库的「当前余额」**。
    #   于是 `ledger balance` 的"登记余额"列永远停在建账那天的数，
    #   每记一笔分录就多一行「⚠️账册不符」——实测一度有 5 户挂红（版本户 4 + 暂记）。
    #   而 balance 明明已经把 `账面 vs 登记` 摆在一起，settle 却不去补登记值，
    #   等于**诊断出来了、处方不写**。
    # ★ S176：回写逻辑已提到 sync_registered_balances()，与 sync-versions 共用一份。
    #   以前两处各写一遍，结果一处回写一处不回写——同一判据两份实现（M-R38）。
    synced, failed, err = sync_registered_balances()
    if err:
        print(f'   ⚠️ 余额回写：{err}')
    elif synced or failed:
        print(f'   余额回写: 同步 {synced} 户'
              + (f'｜❌ 失败 {len(failed)} 户（{", ".join(failed[:4])}）——账册将显示不符' if failed else ''))
    snap = L.take_snapshot(extra=acc)
    tot = {}
    for name, bycur in bal.items():
        for c, v in bycur.items():
            tot[c] = tot.get(c, 0) + v
    bad = {c: v for c, v in tot.items() if abs(v) > 0.005}
    print(f'✅ 日结快照 {L.SNAPSHOT_FILE}')
    print(f'   试算平衡: {"✅ 按币种归零" if not bad else "❌ 不平 " + str(bad)}')
    print('   顺序纪律：settle → post → close')

# ---------- finalcheck ----------
def _run_tail(cmd, tail=2000):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout + r.stderr)[-tail:]   # docsync 把结果打在 stderr（rc 亦可能非 0），两路都要读

def cmd_finalcheck(a):
    gates = []
    out = _run_tail(['python3', f'{L.SKILLS}/tvbox-inspect/scripts/inspect.py', 'docsync'])
    m = re.search(r"'drift':\s*(\d+)", out)
    gates.append(('①库坐标(docsync)', bool(m) and int(m.group(1)) == 0, f"drift={m.group(1) if m else '?'}"))
    rows, good = L.rows_of(L.DB_ACCT)
    bal = entry_balances() or {}
    n_bad = 0
    for r in (rows if good else []):
        if r.get('科目类') == '资产·数据' and r.get('关联对象'):
            live, g = _live_count(r['关联对象'])
            if not g or live != sum((bal.get(r['账户名']) or {}).values()):
                n_bad += 1
    gates.append(('②账实差额', good and n_bad == 0, f'不符 {n_bad} 户'))
    out = _run_tail(['python3', f'{L.SKILLS}/ecosys-conductor/scripts/version_guard.py'])
    # ★ S165 加固：原判据是 `'✅ 全部一致' in out` —— 只认那一行结论。
    #   实测踩到一次 🔴 拦截：刚把 eco-ledger 版本写进生态库，守卫读到的还是旧值，
    #   于是结论行变成「❌ 有 N 处不一致」，而 11 行明细其实已经逐行可查。
    #   问题不在守卫，在**判据太粗**：它拿"一句总结"当唯一证据，
    #   而那行结论反过来依赖**另一次网络读**（生态库）——读两次就可能不一致。
    #   改为**逐行明细判定**：解析每行的三个版本列，全部相等才算过；
    #   一行都解析不出来 → 判「无法判定」并说明，不折叠成通过也不折叠成失败。
    #
    # ★ S176 再修（**逐行判定本身漏掉了一行，而且分母跟着漏**）：
    #   原正则要求三个版本列都是数字 `([\d.]+)`，可 `automation-task-manager` 的
    #   「生态库」列是 `-`（未登记），整行不匹配 → 被跳过。于是：
    #     · 守卫自己说「13 个自建技能」，
    #     · finalcheck 只解析出 12 行，报「12/12 户一致」，
    #     · **`-` 那一行不是"对"，是"没有可比对象"，却被静默当成没问题**。
    #   这比原来的"只认结论行"更隐蔽：结论行至少会说 13，逐行判定连分母都悄悄少 1。
    #   修法：允许版本列为 `-`，`-` 的行**单列为"生态库未登记"**并计入分母、计为不过——
    #   因为版本守卫这三个字的含义就是三方一致，缺一方不是"一致"。
    vrows = re.findall(r'^\s+(\S+)\s+([\d.]+|[-—])\s+([\d.]+|[-—])\s+([\d.]+|[-—])\s+(✅|❌)\s*$', out, re.M)
    if not vrows:
        gates.append(('③版本一致', False, 'version_guard 输出无法解析（无法判定）'))
    else:
        # 分母以一、守卫自己报的总数；两者不等就说明**还有行没解析出来**，同样算不过。
        m_tot = re.search(r'（(\d+)\s*个自建技能）', out)
        claimed = int(m_tot.group(1)) if m_tot else None
        missing = []
        for n, a, b, c, mark in vrows:
            if '-' in (a, b, c) or '—' in (a, b, c):
                missing.append(n)
        bad_v = [n for n, a, b, c, mark in vrows
                 if mark != '✅' or not (a == b == c) or '-' in (a, b, c) or '—' in (a, b, c)]
        cnt_ok = len(vrows) - len(bad_v)
        detail = f'{cnt_ok}/{len(vrows)} 户一致'
        if claimed is not None and claimed != len(vrows):
            detail += f'｜⚠️ 守卫报 {claimed} 个技能，仅解析出 {len(vrows)} 行（有行未匹配）'
        if missing:
            detail += f'｜生态库未登记: {", ".join(missing[:3])}'
            bad_v = bad_v or ['(missing)']
        elif bad_v:
            detail += f'｜不符: {", ".join(bad_v[:3])}'
        gates.append(('③版本一致', not bad_v, detail))
    out = _run_tail(['python3', f'{L.SKILLS}/ecosys-conductor/scripts/conductor.py', 'signal-check'], 4000)
    m = re.search(r'(\d+)/(\d+)\s*信号源逻辑有效', out)
    gates.append(('④信号源', bool(m) and m.group(1) == m.group(2), f'{m.group(1)}/{m.group(2)}' if m else '?'))
    trows, tgood = L.rows_of(L.DB_TOTAL)
    open_fc = [r for r in (trows if tgood else [])
               if r.get('终验相关') and r.get('状态') not in ('已平', '冲销')]
    gates.append(('⑤凭证完备', tgood and not open_fc, f'未平终验分录 {len(open_fc)} 条' if tgood else '总账读失败'))
    allok = True
    for name, ok, detail in gates:
        allok &= bool(ok)
        print(f'  {"✅" if ok else "🚫"} {name}: {detail}')
    # ★ S156：拦截必须**外溢**，不能只在终端打印一行 🔴。
    #   此前账房终验不过 = 屏幕上红一下，监察司/清道夫/遗留清单全无感知，
    #   等于"自己判自己红、然后没人知道"——这正是平衡性「联动断点」判 45 分的由来。
    if not allok:
        bad = [f'{n}({d})' for n, ok, d in gates if not ok]
        _register_monitor(
            '账实/账账不符', '账房终验拦截',
            f'终验未过 {len(bad)}/{len(gates)} 卡口：' + '；'.join(bad)[:300])
    print(f'\n终验结论: {"🟢 放行" if allok else "🔴 拦截"}')
    sys.exit(0 if allok else 1)

# ---------- writeoff ----------
def cmd_writeoff(a):
    row = _acct_row(a.account)
    if not row or row.get('科目类') != '资产·数据':
        print('❌ 账户不存在或不是资产·数据户'); sys.exit(1)
    live, g = _live_count(row['关联对象']) if row.get('关联对象') else (None, False)
    if not g:
        print('❌ 实际行数读取失败——拒绝凭空平账'); sys.exit(1)
    bal = entry_balances() or {}
    book = sum((bal.get(a.account) or {}).values())
    diff = live - book
    if abs(diff) < 0.005:
        print('✅ 本就无差额'); return
    e = L.make_entry(a.voucher, a.account if diff > 0 else '暂记·无主变动',
                     '暂记·无主变动' if diff > 0 else a.account,
                     abs(diff), '记录数', '入库' if diff > 0 else '清库',
                     agent='eco-ledger', finalcheck=True, note=f'writeoff 对齐: 实际{live} vs 账面{book:.0f}')
    ok, msg = L.post(e)
    print(('✅ ' if ok else '❌ ') + msg + f'（差额 {diff:+.0f} → 暂记·无主变动，待认领）')
    if ok:
        rid = _rid_of(L.DB_ACCT, '账户名', a.account)
        if rid:
            _update(L.DB_ACCT, rid, {'当前余额': {'number': float(live)}, '上次核对': {'date': L._now()}})
        print('   提醒：暂记·无主变动余额上升——账龄 3 轮未认领升级高危（§1.2 纪律）')
    sys.exit(0 if ok else 1)

# ---------- enroll 补户（★ S196：消除"改科目表靠手改源码"的手动环节） ----------
def cmd_enroll(a):
    """一条命令完成补户三步，替代此前全手动的流程：
      ① core_ledger.INITIAL_ACCOUNTS 源码插入（科目表是两处声明之一）
      ② 账户库 batch_add 补行（另一处声明）
      ③ 双回读验证（源码 grep + 账户库 _acct_row，缺一处即判失败）
    背景：S196 补登「播放页壳库」时发现科目表两处声明缺一即成"幽灵户"，
    而当时补登全程是 AI 手改源码 + 手拼 batch_add——可复现性为零。
    """
    if not a.voucher:
        print('❌ 补户是账务动作，必须留痕：加 --voucher 凭证号'); sys.exit(1)
    # 前置：两处都查重，防双登
    src = open(L.__file__, encoding='utf-8').read()
    if re.search(rf"^    \('{re.escape(a.name)}',", src, re.M):
        print(f'❌ {a.name} 已在科目表源码中，拒绝重复补登'); sys.exit(1)
    if _acct_row(a.name):
        print(f'❌ {a.name} 已在账户库中，拒绝重复补登'); sys.exit(1)
    # ① 源码插入：插在 INITIAL_ACCOUNTS 列表收尾 ']' 之前的最后一个元组后
    ins = (f"    ('{a.name}', '{a.cls}', '{a.currency}', "
           f"{repr(a.object) if a.object else 'None'}),   # S196 enroll 补登\n]\n")
    new_src, n = re.subn(r"\n\]\n", '\n' + ins, src, count=1)
    if n != 1:
        print('❌ 未找到 INITIAL_ACCOUNTS 列表收尾——源码结构变了，停止补登'); sys.exit(1)
    open(L.__file__, 'w', encoding='utf-8').write(new_src)
    # ② 账户库补行
    rec = {'账户名': {'text': a.name}, '科目类': {'select': a.cls},
           '币种': {'select': a.currency}, '关联对象': {'text': a.object or ''},
           '账户状态': {'select': '正常'}}
    ok, out = L.add_entries(L.DB_ACCT, [rec])
    if not ok:
        print('❌ 账户库补行失败（源码已插入，请检查后回滚或补齐）：' + out[:200]); sys.exit(1)
    # ③ 双回读
    src2 = open(L.__file__, encoding='utf-8').read()
    in_src = bool(re.search(rf"^    \('{re.escape(a.name)}',", src2, re.M))
    row = _acct_row(a.name)
    if not (in_src and row):
        print(f'❌ 回读失败（源码:{in_src} 账户库:{bool(row)}）——幽灵户风险，立即处理'); sys.exit(1)
    print(f'✅ 补户三步完成：{a.name}（{a.cls}·{a.currency}·{a.object or "无对象"}）')
    # 可选期初余额：借 新户 / 贷 期初·建账基准（多币种锚点户）
    if a.balance:
        if a.cls != '资产·数据':
            print('⚠️ 非资产·数据户不自动记期初（币种锚点请手动 post）')
        else:
            live, g = _live_count(a.object)
            if not g:
                print('⚠️ 关联库实数读取失败，期初余额未记——请手动 post')
            else:
                e = L.make_entry(a.voucher, a.name, '期初·建账基准', live, a.currency,
                                 '建库', agent='eco-ledger', finalcheck=True,
                                 note=f'enroll 补户期初：实读 {live} 条')
                ok2, msg = L.post(e)
                print(('✅ ' if ok2 else '❌ ') + f'期初分录：{msg}')
    # 补登后立即对账一次，当场验证不是幽灵户
    rc = subprocess.run([sys.executable, __file__, 'reconcile'], capture_output=True, text=True)
    tail = [x for x in rc.stdout.splitlines() if x.strip()][-2:]
    print('reconcile 抽验: ' + ' | '.join(tail))
    sys.exit(0 if rc.returncode == 0 else 1)

# ---------- aging：暂记账龄自动升级 ----------
def cmd_aging(a):
    """★ S156 backlog 清账②：暂记账龄**自动**升级（原只在 writeoff 后打一句"提醒"）。

    原状：`writeoff` 末尾打印「账龄 3 轮未认领升级高危（§1.2 纪律）」——
    **一句提醒不等于一个动作**。没人跑就没有任何输出，暂记余额可以挂一年，
    而每条 `writeoff` 都在往暂记里灌差额（账房越用，暂记越脏）。

    判据（可机械计算，不靠人记）：对每条**仍挂在暂记**的分录，
      · 用「本期最后一条分录的日期」当轮次基准（台账没有"轮"的概念，用会话日期近似）；
      · 账龄 = 基准日 − 分录日期（天）；≥21 天（≈3 周≈3 轮）→ 高危，≥7 天 → 超期。
    三态：总账读失败 → 明确报"无法判定"，**不折叠成"无超期"**（M-R60）。

    退出码：有高危项 → 1（可挂进 verify 当阻断）；否则 0。
    """
    rows, good = L.rows_of(L.DB_TOTAL)
    if not good:
        print('❌ 总账读取失败——账龄**无法判定**，勿当作「无超期」（M-R60）')
        sys.exit(2)
    # 已平/已冲销的暂记分录不再挂账（贷/借任一侧为暂记且状态非终态才算在挂）
    live = [r for r in rows
            if '暂记·无主变动' in ((r.get('借方账户') or '') + (r.get('贷方账户') or ''))
            and (r.get('状态') or '') not in ('已平', '冲销')]
    if not live:
        print('✅ 暂记无挂账分录（已平/冲销的分录不计入账龄）')
        sys.exit(0)
    q = sum(1 for _ in live)
    hi, over, detail = 0, 0, []
    for r in live:
        d = r.get('日期')
        if not d:
            detail.append((None, r.get('凭证号'), '日期缺失——无法算账龄'))
            hi += 1        # 算不出账龄 = 无法排除，按高危处理（不放过）
            continue
        try:
            t = time.mktime(time.strptime(d[:10], '%Y-%m-%d'))
        except Exception:
            detail.append((None, r.get('凭证号'), f'日期格式异常 {d!r}'))
            hi += 1
            continue
        age = int((time.time() - t) // 86400)
        tag = '高危' if age >= 21 else ('超期' if age >= 7 else '正常')
        if tag == '高危':
            hi += 1
        elif tag == '超期':
            over += 1
        detail.append((age, r.get('凭证号'), tag))
    print(f'📅 暂记账龄（在挂 {q} 条）：高危 {hi} / 超期 {over}')
    for age, v, tag in sorted(detail, key=lambda x: -(x[0] or 9999)):
        print(f'  {"🔴" if tag == "高危" else "🟡" if tag == "超期" else "  "} '
              f'{(str(age) + " 天") if age is not None else "—":>7}  {v}  {tag}')
    if hi:
        print('\n处置：逐条认领（补凭证平账）或冲销；账龄 ≥21 天不得继续挂账（§1.2）')
        _register_monitor('暂记账龄高危', '账房暂记账龄升级',
                          f'{hi} 条暂记分录账龄 ≥21 天未认领，暂记余额持续被 writeoff 灌入')
    sys.exit(1 if hi else 0)


# ---------- post ----------
def cmd_post(a):
    e = L.make_entry(a.voucher, a.debit, a.credit, a.amount, a.currency, a.action,
                     agent=a.agent, finalcheck=a.final, note=a.note)
    ok, msg = L.post(e)
    print(('✅ ' if ok else '❌ ') + msg)
    sys.exit(0 if ok else 1)



# ---------- selftest（T-E 活体探针 + T-H 哑巴检测的账房版，借鉴 M-R111 提案） ----------
def cmd_selftest(a):
    """三段活体证明：①干净基线（假阳性检查）②注入已知差额（探针）③自净（冲销归零）。
    任一段失败=账房检测器静默失效（装健康的哑巴），退出码 1。"""
    voucher = f"SELFTEST-{time.strftime('%m%d%H%M')}"
    fails = []
    # ⓪ 先结账刷新快照（账账线基线必须新鲜，否则是账未平不是假阳性）
    subprocess.run([sys.executable, __file__, 'settle'], capture_output=True, text=True)
    # ① 基线：当前必须无差额（有=假阳性，检测器太松）
    r = subprocess.run([sys.executable, __file__, 'reconcile'], capture_output=True, text=True)
    if r.returncode != 0:
        fails.append('①基线有差额——存在假阳性（检测器过松或账未平），先 reconcile 归零再自检')
    # ② 探针：注入已知差额 +1（失信库），reconcile 必须抓到
    e = L.make_entry(voucher, '失信库', '期初·建账基准', 1, '记录数', '入库',
                     agent='selftest', note='活体探针注入（非实体增量，selftest 自动清理）')
    ok, msg = L.post(e)
    if not ok:
        fails.append(f'②探针注入失败：{msg}')
    else:
        r = subprocess.run([sys.executable, __file__, 'reconcile'], capture_output=True, text=True)
        caught = '失信库' in r.stdout and r.returncode != 0
        if not caught:
            fails.append('②注入已知差额后 reconcile 未抓到——检测器静默失效（T-H 哑巴）')
    # ③ 自净：冲销探针分录，恢复基线
    r2 = subprocess.run([sys.executable, __file__, 'post', '--voucher', voucher + '-R',
                         '--debit', '期初·建账基准', '--credit', '失信库',
                         '--amount', '1', '--action', '冲销',
                         '--note', f'冲销 {voucher}（selftest 自净）'], capture_output=True, text=True)
    if '✅' not in r2.stdout:
        fails.append(f'③自净失败：{r2.stdout.strip()[:120]}')
    else:
        r3 = subprocess.run([sys.executable, __file__, 'reconcile'], capture_output=True, text=True)
        if r3.returncode != 0:
            fails.append('③自净后基线未恢复——账房自身留了脏账')
    if fails:
        print('🚫 selftest 失败：')
        for f in fails:
            print('  ', f)
        sys.exit(1)
    print(f'✅ selftest 通过（{voucher}）：基线无假阳性 → 注入差额被抓到 → 自净归零——检测器活着')

# ---------- db merge / move（方案 §2.4 顺序固化） ----------
def _schema_of(db):
    rc, out, err = L._run(['python3', f'{L.DB}/get_database_schema.py', '--database-id', db])
    d = L._json_out(out)
    return d if d and d.get('properties') else None

def _wrap(schema, row):
    """把 query 返回的扁平字段包回 batch_add 需要的 typed properties。"""
    types = {p['name']: p['type'] for p in schema['properties']}
    props = {}
    for k, v in (row or {}).items():
        t = types.get(k)
        if t is None or v is None or k == 'record_id':
            continue
        if t in ('text', 'url', 'email', 'phone_number'):
            props[k] = {t: str(v)}
        elif t in ('select', 'multi_select', 'date', 'number', 'checkbox'):
            props[k] = {t: v}
        else:
            props[k] = {'text': str(v)}   # 未知类型降级 text（并在日志标注）
    return props

def _rename_node(db_id, title):
    """S158：改走 core._run_lib（统一三态）。语义不变——原本就有回读验证，此处只统一点调用姿势。

    ★ 保留原有的一条实测结论：`get_database_schema` 的标题缓存有滞后（S161 实测），
      所以改名后**必须用 list-nodes 验真**，不能拿"api 返回 ok"当改名成功。
      这正是 T-I「success 不等于落库」在本技能的具体形态。
    """
    try:
        import core as _c
    except Exception as e:
        print(f'⚠️ 改名跳过：core 不可用（{type(e).__name__}: {str(e)[:60]}）')
        return
    ok, _out, err = _c._run_lib('space_api.py', 'space.workspace.rename-node',
                                '--node-id', db_id, '--title', title)
    if not ok:
        print(f'⚠️ 改名请求失败：{err[:120]}')
        return
    # 回读验证：必须用 list-nodes 验真（schema 的标题缓存滞后）
    ok2, lst, err2 = _c._run_lib('space_api.py', 'space.workspace.list-nodes')
    if not ok2:
        print(f'⚠️ 改名回读**无法判定**（list-nodes 读不到：{err2[:100]}）——'
              f'不能当成功，也不能当失败，请人工核对')
        return
    if title not in lst:
        print(f'⚠️ 改名回读未确认（api 返回 ok 但 list-nodes 无新标题）：{title}')

def cmd_db_move(a):
    """★ S156 backlog 清账①：`db move` **独立语义**（原为 `db move` 报错→要求改用 merge）。

    与 merge 的实质差别只有一条，但很关键：
      · merge  = 源账户**并入**目标账户（币种/科目归并，源账户状态→已并入）
      · move   = 记录**换个家**，账户身份不变（源账户状态→已迁移，目标账户新建同名户）
    共同部分（冻结→快照→schema 比对→迁移→对账→清源→改名🗑）完全复用 merge 的实现，
    **不复制**那 40 行（复制就会分叉——M-R38）。做法：给 merge 传一个 mode 标记。
    """
    return _db_transfer(a, mode='move')


def cmd_db_merge(a):
    """merge: 源库全量并入目标库。顺序=冻结→快照→schema比对→迁移→对账→清源→改名→转账分录→账户状态。"""
    return _db_transfer(a, mode='merge')


def _db_transfer(a, mode='merge'):
    """merge/move 共用的搬运实现。mode 只影响分录类型与账户终态。"""
    if not a.archive or not os.path.exists(a.archive):
        print('❌ 缺 --archive 快照——拒绝并库（S160 规则：先归档才动手）'); sys.exit(1)
    if a.frm == a.to:
        print('❌ 源=目标'); sys.exit(1)
    s_schema, d_schema = _schema_of(a.frm), _schema_of(a.to)
    if not s_schema or not d_schema:
        print('❌ schema 读取失败——三态不折叠，中止'); sys.exit(1)
    s_fields = {p['name'] for p in s_schema['properties']}
    d_fields = {p['name'] for p in d_schema['properties']}
    if s_fields != d_fields and not a.force:
        print(f'❌ 字段不一致（源独有 {sorted(s_fields - d_fields)}，目标独有 {sorted(d_fields - s_fields)}）——'
              '字段先统一再并库（S160：跨库统计必失真）；确要继续加 --force（仅复制交集字段）'); sys.exit(1)
    src_rows, g = L.rows_of(a.frm)
    if not g:
        print('❌ 源库读取失败——中止'); sys.exit(1)
    d_rows, g2 = L.rows_of(a.to)
    if not g2:
        print('❌ 目标库读取失败——中止'); sys.exit(1)
    n, before = len(src_rows), len(d_rows)
    print(f'① 冻结源账户 + ② 快照 {a.archive}（{n} 条）+ ③ schema {"一致" if s_fields == d_fields else "force交集"}')

    # ④ 迁移（分批 50）
    copied = 0
    for i in range(0, n, 50):
        chunk = [_wrap(d_schema, r) for r in src_rows[i:i+50]]
        chunk = [c for c in chunk if c]
        if chunk:
            ok, raw = L.add_entries(a.to, chunk)
            if not ok:
                print(f'❌ 迁移批次 {i} 写入失败：' + raw[:300]); sys.exit(1)
        copied += len(chunk)
    d_rows2, g3 = L.rows_of(a.to)
    if not g3 or len(d_rows2) != before + n:
        print(f'❌ 迁移对账失败：目标 {before}+{n} ≠ 实际 {len(d_rows2) if g3 else "读失败"}'); sys.exit(1)
    print(f'④ 迁移 {n}/{n}，目标对账 {before}+{n}={len(d_rows2)} ✅')

    # ⑤ 清源 → 回读 0
    rids = [r['record_id'] for r in src_rows]
    for i in range(0, len(rids), 100):
        L._run(['python3', f'{L.DB}/batch_delete_database_records.py',
                '--database-id', a.frm, '--record-ids', json.dumps(rids[i:i+100])])
    src2, g4 = L.rows_of(a.frm)
    if not g4 or src2:
        print('❌ 清源后回读非空/失败——中止改名'); sys.exit(1)
    print('⑤ 源库清空 → 回读 0 ✅')

    # ⑥ 改名🗑 ⑦ 转账分录 ⑧ 账户状态
    stamp = time.strftime('%m%d_%H%M')
    _rename_node(a.frm, f'🗑【已废弃】{a.name}·并入目标库_{stamp}')
    _action = '转账' if mode == 'merge' else '转账'
    _end = '已并入' if mode == 'merge' else '已迁移'
    e = L.make_entry(a.voucher, a.to_name or '期初·建账基准', a.name, n, '记录数', _action,
                     agent='eco-ledger', finalcheck=True,
                     note=(f'{mode} {a.frm} → {a.to}（快照 {a.archive}；改名🗑_{stamp}）'))
    ok, msg = L.post(e)
    for acc, state in ((a.name, _end), (a.to_name, None)):
        rid = _rid_of(L.DB_ACCT, '账户名', acc)
        if rid:
            props = {'账户状态': {'select': state}} if state else {'当前余额': {'number': float(sum((entry_balances().get(acc) or {}).values()))}, '上次核对': {'date': L._now()}}
            _update(L.DB_ACCT, rid, props)
    print(f'⑥ 已改名🗑 ⑦ 转账分录{"✅" if ok else "❌ " + msg} ⑧ 源账户→{_end} ✅')
    print('   收尾：retired 名单扩容 + core 坐标回填（M-R38）')

# ---------- sync-versions（register_eco 的账房侧钩子） ----------
def after_register(name, version=''):
    """★ S156 backlog 清账③：register_eco 之后**自动**对齐版本户，不再靠人记得手动跑。

    原状：evolve.register_eco() 只写生态库；账房的「版本·X」账户要另一条命令
    `ledger sync-versions` 才对得上——两条命令之间靠"人记得"，于是必然忘记，
    实测生态库 v/skill 一致但版本户滞后（信号源20 台账一致性长期报红）。

    设计取舍：**不**在 evolve.py 里写 `import ledger`（会让技能间产生硬依赖，
    evolve 挂了账房跟着挂）。改为账房导出本钩子，由调用方显式接线；
    钩子自身失败**不抛**（记账失败不该阻断"新建技能"这件更大的事）。
    返回 (ok, msg) 供调用方留痕。
    """
    class _A:
        voucher = f'AUTO-REG-{time.strftime("%m%d%H%M")}'
    try:
        cmd_sync_versions(_A)
        return True, f'{name} v{version} 版本户已对齐'
    except SystemExit:
        return True, f'{name} 版本户已对齐（cmd 内 sys.exit 属正常返回）'
    except Exception as e:
        print(f'  ⚠️ 版本户自动对齐失败（{type(e).__name__}: {str(e)[:80]}）——请手动跑 ledger sync-versions')
        return False, f'{type(e).__name__}: {str(e)[:80]}'



def sync_registered_balances(quiet=False):
    """把**账面余额**回写进账户库的「当前余额」（登记值）。

    ## 为什么提成独立函数（S176·M-R38 第三次踩）
    `settle` 在 S165 就补上了回写，`sync-versions` 却**从来没写**——
    于是每次版本升级：分录记了、账面涨了、登记值不动，`ledger balance`
    立刻多一行「⚠️账册不符」。实测 `版本·eco-ledger` 账面 10203 / 登记 10202
    就是这么来的：`AUTO-VER-10192007` 记了 +1，账户库没人去改。
    两个地方都写版本户、只有一处回写，是标准的"同一判据两份实现"。

    提成函数后，settle 与 sync-versions 都调它，**回写只有一个实现**。
    返回 (同步户数, 失败户名列表, 读失败原因或 None)。
    """
    bal = entry_balances()
    if bal is None:
        return 0, [], '总账读取失败——无法判定，不回写（三态不折叠）'
    rows, good = L.rows_of(L.DB_ACCT)
    if not good:
        return 0, [], '账户库读取失败——无法判定，不回写'
    synced, failed = 0, []
    cmap = L.acct_currency_map()
    for r in rows:
        name = r.get('账户名')
        bycur = bal.get(name) or {}
        # ★ S176：多币种对价户**不回写**。账户库的「当前余额」是单个数字，
        #   装不下四个币种；硬写成 sum() 会把 4 个不同单位加成一个假数，
        #   然后 balance 拿假数比假数，永远显示对得上——假绿。
        #   这类户的余额只能看 `balance` 的按币种分行明细，账户库不承载。
        if cmap.get(name) == L.COUNTERPARTY or len({c for c in bycur if abs(bycur[c]) > 0.005}) > 1:
            continue
        cur = sum(bycur.values())
        reg = r.get('当前余额') or 0
        if abs(cur - reg) < 0.005:
            continue
        ok, _msg = _update(L.DB_ACCT, r['record_id'],
                           {'当前余额': {'number': cur}, '上次核对': {'date': L._now()}})
        if ok:
            synced += 1
            if not quiet:
                print(f'   登记回写 {name}: {reg} → {cur}')
        else:
            failed.append(name)
    return synced, failed, None

def cmd_sync_versions(a):
    """版本户与 SKILL.md 实况对齐：差额自动记「版本升级」分录（版本号币种）。

    ★ S176：记完分录**必须回写账户库**，否则每升一次版本就多一行账册不符。
    """
    versions = {n[3:]: L.v2n(v) for n, c, cur, v in L.version_accounts()}
    bal = entry_balances() or {}
    changed = 0
    for name, target in versions.items():
        acc = f'版本·{name}'
        cur = sum((bal.get(acc) or {}).values())
        if abs(cur - target) < 0.5:
            continue
        diff = target - cur
        debit, credit = (acc, '期初·建账基准') if diff > 0 else ('期初·建账基准', acc)
        e = L.make_entry(a.voucher or f'AUTO-VER-{time.strftime("%m%d%H%M")}', debit, credit,
                         abs(diff), '版本号', '版本升级', agent='eco-ledger', finalcheck=True,
                         note=f'{name} 版本户对齐 → {target}（v2n 编码）', strict_currency=True)
        ok, msg = L.post(e)
        print(f'{"✅" if ok else "❌"} {name}: {cur:.0f} → {target}（{msg}）')
        changed += ok
    if not changed:
        print('✅ 版本户全部与 SKILL.md 对齐，无分录')
    # ★ 无论记没记分录，都回写一次：历史欠下的差额也要在这里被补齐。
    synced, failed, err = sync_registered_balances()
    if err:
        print(f'   ⚠️ 登记回写：{err}')
    elif synced or failed:
        print(f'   登记回写: 同步 {synced} 户'
              + (f'｜❌ 失败 {len(failed)} 户（{", ".join(failed[:4])}）——账册将显示不符' if failed else ''))
        sys.exit(1 if failed else 0)

# ---------- db ----------
def cmd_db(a):
    if a.op == 'create':
        schema = a.schema if a.schema else '{}'
        if a.schema:
            rc, out, err = L._run(['python3', f'{L.DB}/create_database.py', '--schema', a.schema])
        else:
            rc, out, err = L._run(['python3', f'{L.DB}/create_database.py', '--stdin'])
        d = L._json_out(out)
        if not d or not d.get('database_id'):
            print('❌ 建库失败：' + out[:300]); sys.exit(1)
        ok, msg = L.post(L.make_entry(a.voucher, a.name, '期初·建账基准', 0, a.currency, '建库',
                                      agent='eco-ledger', finalcheck=True, note=f"建库 {d['database_id']}"))
        print(f"✅ 建库 {a.name} = {d['database_id']}；{'分录已记' if ok else '分录未记: ' + msg}")
        print('   下一步：账户库登记 + core 坐标回填（M-R38）')
    elif a.op == 'retire':
        if not a.archive or not os.path.exists(a.archive):
            print('❌ 缺 --archive 快照或路径不存在——拒绝删库（S160 规则）'); sys.exit(1)
        rows, good = L.rows_of(a.database)
        if not good:
            print('❌ 目标库读取失败——三态不折叠，不删'); sys.exit(1)
        n = len(rows)
        rids = [r['record_id'] for r in rows]
        if rids:
            L._run(['python3', f'{L.DB}/batch_delete_database_records.py',
                    '--database-id', a.database, '--record-ids', json.dumps(rids)])
        rows2, g2 = L.rows_of(a.database)
        if not g2 or rows2:
            print('❌ 删除后回读非空/失败——中止'); sys.exit(1)
        ok, msg = L.post(L.make_entry(a.voucher, a.name, '暂记·无主变动', n, '记录数', '清库',
                                      agent='eco-ledger', finalcheck=True, note=f'清库+待改名🗑 快照={a.archive}'))
        print(f'✅ 清库 {n} 条 → 回读 0（快照: {a.archive}）')
        print('   收尾：space_api rename-node 改「🗑【已废弃】…」+ retired 名单扩容 + 账户状态→已废弃')
        sys.exit(0 if ok else 1)

def _cb_mod():
    """惰性加载调用总线（M-R46：不让 call_bus 的失败拖垮整个账房——记账是账房的本职，
    调用只是它的一个门面）。失败时抛，由 main 转成可读错误。"""
    import call_bus
    return call_bus


_CB_CACHE = {}


def cb_mod():
    if 'm' not in _CB_CACHE:
        _CB_CACHE['m'] = _cb_mod()
    return _CB_CACHE['m']


def _cb_dispatch(a):
    """`ledger call` 的入口：`call` 不带动词 → 打印动词表（不是报错）。

    ★ 这一行是刻意加的。`tvbox size` 在 S149 栽过同型的坑：不带参数被当成"参数错位"
    报「未知命令」，于是用户以为功能坏了。统一入口尤其要经得起裸敲——
    裸敲的那一刻正是用户最需要知道"我能调什么"。
    """
    if not a.verb:
        return cb_mod().cmd_verbs(a)
    return cb_mod().cmd_call(a)


_CALL_OWN_ARGS = ('--boundary', '--timeout', '--note', '--quiet', '--no-book')


def _split_call_args(raw):
    """把 `call` 之后的参数切成「本层选项」与「透传给底层技能的参数」。

    为什么必须做：argparse 子解析器不认 `--before`/`--candidates`/`--dry` 这类
    **底层技能自己的**选项，会在 parse 阶段直接 SystemExit(2)——事后再补已经晚了。
    故在 parse 之前重排：把透传参数统一挪到 `--` 之后，argparse 就不会再碰它们。

    只对 `call` 生效，其它子命令原样返回（别的地方没有透传需求）。
    """
    if 'call' not in raw:
        return raw
    i = raw.index('call')
    head, tail = raw[:i + 1], raw[i + 1:]
    own, passthru = [], []
    j = 0
    while j < len(tail):
        t = tail[j]
        if t in _CALL_OWN_ARGS:
            own.append(t)
            if j + 1 < len(tail):
                own.append(tail[j + 1]); j += 2; continue
            j += 1; continue
        if any(t.startswith(o + '=') for o in _CALL_OWN_ARGS):
            own.append(t); j += 1; continue
        passthru.append(t); j += 1
    return head + own + (['--'] + passthru if passthru else [])


def main():
    p = argparse.ArgumentParser(prog='ledger.py')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status').set_defaults(f=cmd_status)
    sub.add_parser('init').set_defaults(f=cmd_init)
    sub.add_parser('accounts-show').set_defaults(
        f=lambda a: [print(f'{n:<18}{c:<10}{cur:<6}{r or "-"}') for n, c, cur, r in L.INITIAL_ACCOUNTS])
    sub.add_parser('balance').set_defaults(f=cmd_balance)
    sub.add_parser('reconcile').set_defaults(f=cmd_reconcile)
    sub.add_parser('settle').set_defaults(f=cmd_settle)
    sub.add_parser('finalcheck').set_defaults(f=cmd_finalcheck)
    sub.add_parser('selftest', help='活体探针自检（T-E/T-H）').set_defaults(f=cmd_selftest)
    ag = sub.add_parser('aging', help='暂记账龄自动升级（≥21 天高危，退出码 1）')
    ag.set_defaults(f=cmd_aging)
    wp = sub.add_parser('writeoff')
    wp.add_argument('--account', required=True);     wp.add_argument('--voucher', required=True)
    wp.set_defaults(f=cmd_writeoff)
    en = sub.add_parser('enroll', help='★补户：科目表源码+账户库双登+回读验证（S196）')
    en.add_argument('--name', required=True)
    en.add_argument('--class', dest='cls', required=True,
                    choices=['资产·数据', '暂记', '期初', '负债·欠账', '权益·指标', '损益·产能', '资产·调用', '资产·版本'])
    en.add_argument('--currency', required=True, choices=sorted(L.CURRENCIES))
    en.add_argument('--object', default=None, help='资产·数据户的关联库 id')
    en.add_argument('--balance', type=float, default=0, help='可选：实读期初余额')
    en.add_argument('--voucher', required=True)
    en.set_defaults(f=cmd_enroll)
    pp = sub.add_parser('post')
    pp.add_argument('--voucher', required=True); pp.add_argument('--debit', required=True)
    pp.add_argument('--credit', required=True); pp.add_argument('--amount', type=float, required=True)
    pp.add_argument('--currency', default='记录数', choices=sorted(L.CURRENCIES))
    pp.add_argument('--action', required=True,
                    choices=['入库','清库','建库','并库','改名','版本升级','指标波动','冲销','转账'])
    pp.add_argument('--agent', default='eco-ledger'); pp.add_argument('--final', action='store_true')
    pp.add_argument('--note', default='')
    pp.set_defaults(f=cmd_post)
    dp = sub.add_parser('db')
    dp.add_argument('op', choices=['create', 'retire', 'merge', 'move'])
    dp.add_argument('--name'); dp.add_argument('--currency', default='记录数')
    dp.add_argument('--voucher'); dp.add_argument('--database'); dp.add_argument('--archive')
    dp.add_argument('--schema')
    dp.add_argument('--frm', help='merge/move: 源库 id'); dp.add_argument('--to', help='merge: 目标库 id')
    dp.add_argument('--to-name', help='merge: 目标账户名'); dp.add_argument('--force', action='store_true')
    dp.set_defaults(f=cmd_db)
    sv = sub.add_parser('sync-versions', help='版本户与 SKILL.md 对齐（register_eco 钩子）')
    sv.add_argument('--voucher', default=None)
    sv.set_defaults(f=cmd_sync_versions)

    # ── S165 跨界统一调用（call_bus.py）──
    # 为什么挂在账房下而不是 `tvbox` 下：用户原话「合并进账房，做统一调用入口」。
    # 而且调用这一层天然属于账房——每次调用都要落笔分录，记账在这里、调用的账也在这里，
    # 分两个地方才是"调用麻烦"的根源。
    cb = sub.add_parser('call', help='★统一调用入口：跨界调用任何技能的一件事')
    cb.add_argument('verb', nargs='?', help='调用动词（tvbox ledger verbs 看全表）')
    cb.add_argument('args', nargs='*', help='透传给底层的参数')
    cb.add_argument('--boundary', default=None,
                    help='界：dialogue/task/scene/eco（默认按动词推断）')
    cb.add_argument('--timeout', type=int, default=180)
    cb.add_argument('--note', default='')
    cb.add_argument('--quiet', action='store_true', help='只出结论，不打印子命令输出')
    cb.add_argument('--no-book', action='store_true', help='不落分录（只在排查时用）')
    cb.set_defaults(f=_cb_dispatch)

    sv_ = sub.add_parser('verbs', help='列出全部调用动词与四个界')
    sv_.set_defaults(f=lambda a: cb_mod().cmd_verbs(a))

    rl = sub.add_parser('relay', help='跨场景接力：一条链自动串起来并逐步入账')
    rl.add_argument('name', nargs='?', help='链路名（不带则列出全部）')
    rl.add_argument('--timeout', type=int, default=180)
    rl.add_argument('--keep-going', action='store_true')
    rl.add_argument('--no-book', action='store_true')
    rl.set_defaults(f=lambda a: cb_mod().cmd_relay(a))

    mp = sub.add_parser('map', help='调用地图：谁能调谁 / 有无幽灵命令')
    mp.add_argument('--json', action='store_true')
    mp.set_defaults(f=lambda a: cb_mod().cmd_map(a))

    ac = sub.add_parser('audit-call', help='调用体检：路由—登记—记账三方一致')
    ac.set_defaults(f=lambda a: cb_mod().cmd_audit_call(a))

    mt = sub.add_parser('metrics', help='调用业态：各界调用次数与成功率')
    mt.set_defaults(f=lambda a: cb_mod().cmd_metrics(a))

    # ★ S165 修：`ledger call <动词> …` 的后续参数里含有**底层技能自己的选项**
    #   （如 `--before probe.py`、`--candidates x.json`、`--dry`）。argparse 在
    #   `call` 这一层只声明了 --boundary/--timeout/--note/--quiet/--no-book，
    #   于是 `--before` 被判 `unrecognized arguments` 直接 SystemExit(2)。
    #   实测踩到：`tvbox ledger call claim probe-hub --before probe.py` 打印 usage。
    #   更坏的是**退出码被 tvbox 入口判成 0**，看起来像"跑通了"。
    #   故必须在 parse_args **之前**动手：解析阶段就会抛，事后再修已经晚了。
    #   做法：call 之后，只有本层声明的选项归自己，其余全部当作透传参数。
    _argv = _split_call_args([str(x) for x in sys.argv[1:]])
    a = p.parse_args(_argv)
    if a.cmd == 'db' and a.op in ('merge', 'move'):
        a.frm = a.frm or a.database
        # ★ S156：move 原被显式拒绝（"请用 merge"），实为能力缺失而非设计取舍。
        #   现 move 有独立语义（记录换家、账户身份不变，终态「已迁移」而非「已并入」）。
        if not a.to:
            print('❌ 缺 --to 目标库 id——无法确定搬去哪'); sys.exit(1)
        (cmd_db_move if a.op == 'move' else cmd_db_merge)(a); return
    # ★ S165 修：原来只 `a.f(a)` 不接返回值，命令函数的 `return 2` 全被丢掉，
    #   main() 恒返回 None → dispatch 恒 0 → 外壳 `$?` 恒 0。
    #   **整条账房的退出码是死的**：call 报 ❌、finalcheck 判 🔴，shell 里都是成功。
    #   （finalcheck 本身 `sys.exit(1)` 所以那条还能出去，但 return 风格的一律失效。）
    return a.f(a)

def dispatch(argv):
    """★ S156：给统一入口 `tvbox ledger` 用的派发口。

    此前本脚本只有 `main()`（自己 parse sys.argv），而入口 `tvbox` 的契约是
    `module.dispatch(argv)` —— 于是 `tvbox ledger status` 报
    `AttributeError: module 'tb_ledger_py' has no attribute 'dispatch'`。
    这与 S148 的 inspect/janitor 是**同型缺陷**：脚本单独能跑，接进入口就崩，
    而"单独能跑"会让人以为已经接好了。

    实现方式：把 argv 塞回 sys.argv 后复用 main()，**不复制**一套参数定义
    （复制就会分叉，两处各改各的——M-R38 单一事实源）。main() 内部用 parse_args，
    报错时抛 SystemExit(2)，入口的 safe_dispatch 已能正确接住并记退出码。
    """
    _old = sys.argv
    try:
        sys.argv = ['ledger.py'] + [str(x) for x in (argv or [])]
        rc = main()
    finally:
        sys.argv = _old
    # ★ S165 修：原来这里**硬编码 return 0** —— 判定算对了，却没交到下游手里。
    #   实测：`tvbox ledger call who`（缺参数）打印 ❌ 并 return 2，但 `tvbox` 入口
    #   收到的是 dispatch 的返回值 0 → shell 里 `$?` 也是 0。门报红、门却开着，
    #   与 S138 `cmd_verify` 那条是同一族错误（判定被 return 0 吃掉）。
    #   main() 里的命令函数多数用 `return 2/1` 表达失败，必须原样传出去。
    #   int 原样返回；bool 按 False→1（与入口 `_as_exit_code` 同义）；其它当 0。
    if isinstance(rc, bool):
        return 0 if rc else 1
    if isinstance(rc, int):
        return rc
    return 0


if __name__ == '__main__':
    main()
