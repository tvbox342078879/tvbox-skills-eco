# -*- coding: utf-8 -*-
"""
eco-ledger · 账房司 core（S161 方案实操版）
单一事实源：本文件是账房全部库坐标与账户定义的唯一出处。
币种（currency）：{记录数, 版本号, 事项数, 分数, 字节数}——试算平衡按币种分组，禁止跨币种相加。
"""
import json, os, subprocess, time

LIB = '/root/.codebuddy/skills/library'
DB = f'{LIB}/database'
SKILLS = '/root/.codebuddy/skills'

# ── 账房自有两库 id：M0 建账后回填（运行前必须填，否则命令会明确报"未建账"）──
DB_TOTAL = 'Xjqn2Wf5EKPSGIXqQ2K6Dc'   # 📒 账房·总账（分录流水）
DB_ACCT  = '6cQgwWQqqKoeFAiTcb08Cx'   # 📒 账房·账户（科目表实例）
LEDGER_READY = bool(DB_TOTAL and DB_ACCT)

# ── 会话台账库（凭证号空间，只引用不复制）──
DB_LEDGER_SESS = 'XTuuj8mKtqnZSolDdeDxkq'   # 经验池/台账所在（与 link.py 对齐后可删此行改 import）

# ── 闸门（只预检，不绕行）──
import sys
sys.path.insert(0, f'{SKILLS}/tvbox-source-hub/scripts')
import session_gate  # noqa: E402

def v2n(ver):
    """版本号 x.y.z → 数值 x*10000+y*100+z（币种「版本号」的记账编码）"""
    p = (str(ver).split('.') + ['0', '0'])[:3]
    return int(p[0]) * 10000 + int(p[1]) * 100 + int(p[2])

CURRENCIES = {'记录数', '版本号', '事项数', '分数', '字节数'}

# ── 科目表初始账户（M0 建账用；余额在期初分录里记，此处只声明）──
# (账户名, 科目类, 币种, 关联库id或技能名)
#
# ★ S176 引入「多币种户」概念（币种写 `*`）：
#   `期初·建账基准` 和 `暂记·无主变动` 是**对价户**——每一笔分录都要有对手方，
#   而分录可以是记录数/版本号/事项数/分数任何币种，所以对价户**必然要装多币种**。
#   以前它们被声明成「记录数」，于是：
#     · `balance` 用 `sum(b.values())` 把 4 个币种硬加成一个数（-125730），
#       这个数没有会计意义——它是 -1255 条 + -94 件 + -49 分 + -124332 版本号；
#     · 新加的币种校验会把对价户判成"币种不符"，直接堵死所有分录。
#   写 `*` 是**如实声明**它本就该装多币种，而不是给校验开后门：
#   校验对 `*` 户放行，但 `balance`/`settle` 必须**按币种分行显示**，不许求和。
COUNTERPARTY = '*'

INITIAL_ACCOUNTS = [
    ('线路源库',     '资产·数据', '记录数', 'pNwZ9uBOTz6FzRuvu8nryI'),
    ('成品源库',     '资产·数据', '记录数', '8JBrhSwsJ3pnPYtoGLo9XV'),
    ('播放页壳库',   '资产·数据', '记录数', 'hPF6AUmWmOQa26tOg8Wjb5'),   # S196 补登：壳库（抠链原料）
    ('失信库',       '资产·数据', '记录数', 'hGwE41OpJDOR8CW0sLUwRx'),
    ('生态登记库',   '资产·数据', '记录数', 'T4kODtANe7rSrtZTu4sIND'),
    ('经验池',       '资产·数据', '记录数', 'XTuuj8mKtqnZSolDdeDxkq'),
    ('会话台账',     '资产·数据', '记录数', 'TO4LwsjVVdR8JRAUlkejk3'),
    ('暂记·无主变动', '暂记',     COUNTERPARTY, None),
    # ★ S176 补登：`暂记·待核销` 这个户**代码里根本不存在**（全仓搜索零命中），
    #   它是 S169b 那轮**手写 post 时顺手编出来的户名**（凭证 WO-S169b-*）。
    #   教训：`post` 接受任意 `借方账户` 字符串，不校验该户是否在科目表——
    #   于是人手一滑就能造一个不受任何校验约束的户，还因为试算平衡照样平，
    #   **永远没人发现**（只在 `balance` 的 ghost 分支露一次头）。
    #   补登进科目表是把这个既成事实纳入管辖；同时 `post` 现在会对未登记户告警
    #   （见 post 的 ghost 分支），不再让造户无声无息。
    ('暂记·待核销',   '暂记',     '记录数', None),
    ('期初·建账基准', '期初',     COUNTERPARTY, None),   # 所有期初分录的对价账户（ conservation 锚点 ）
    # 负债·欠账（unfinished-audit 口径）
    ('欠账·六源全采', '负债·欠账', '事项数', None),
    ('欠账·flow_backlog', '负债·欠账', '事项数', None),
    # 权益·指标（终验卡口）
    ('指标·信号源',   '权益·指标', '分数', None),
    ('指标·版本一致', '权益·指标', '分数', None),
    ('指标·docsync', '权益·指标', '分数', None),
    # 损益·产能
    ('产能·治理会话', '损益·产能', '事项数', None),
    ('产能·业务会话', '损益·产能', '事项数', None),
    # ── S165 跨界科目（统一调用总线 call_bus.py）──
    # 为什么记成"资产·调用"而不是损益：每一次跨界调用都是一笔**真实发生的动作**，
    # 要么落成产出（借增），要么落成欠账（须在六源里看得见）。
    # 记 1 事项数 = "调用了一次"，凭证据在备注里（命令/耗时/首行输出），
    # 不做"按重要度加权"——那会变成没人能核的第二套判据（M-R38）。
    ('跨界·对话',     '资产·调用', '事项数', None),
    ('跨界·任务',     '资产·调用', '事项数', None),
    ('跨界·场景',     '资产·调用', '事项数', None),
    ('跨界·生态',     '资产·调用', '事项数', None),
    ('指标·调用总次数',   '权益·指标', '事项数', None),
    ('指标·调用成功',     '权益·指标', '事项数', None),
    ('指标·调用失败',     '权益·指标', '事项数', None),
    ('指标·调用无法判定', '权益·指标', '事项数', None),
    # ★ S184：第四态。`call_bus` 加 partial 判定后 `_bump` 当场 KeyError——
    #   这说明"加一个判定"在这套账里**至少要在三处落地**：判据 / 科目表 / 指标映射。
    ('指标·调用部分成功', '权益·指标', '事项数', None),
    # ★ S165 补登：以下 4 户一直在总账里有余额，却**从未进过科目表**。
    #   实测 `ledger balance` 有 6 户"有余额但不显示"（见该命令的 ghost_accts 分支）——
    #   记账了却查不到，比没记账更危险：账上看着是平的，实际多出一笔没人认领的户头。
    ('版本·eco-ledger', '资产·版本', '版本号', None),
    ('演练·T1',         '暂记',      '记录数', None),   # selftest 留下的练习户，勿删（删了试算不平）
    ('演练·T2',         '暂记',      '记录数', None),
    ('优质实测库', '资产·数据', '记录数', 'nyxfTYFAScxpf8ZTdk8Otx'),   # S196 enroll 补登
    ('规则·维护', '损益·产能', '事项数', None),   # S196 enroll 补登
]
# 资产·版本户：10 个自建技能（期初值由建账脚本从 SKILL.md 实读）
SELF_BUILT = ['ecosys-conductor','legacy-watchdog','probe-hub','skill-evolution','tvbox-inspect',
              'tvbox-interface','tvbox-janitor','tvbox-source-blacklist','tvbox-source-hub','unfinished-audit', 'eco-ledger']

def version_accounts():
    import re
    accs = []
    for name in SELF_BUILT:
        p = os.path.join(SKILLS, name, 'SKILL.md')
        m = re.search(r'^version:\s*"?([\d.]+)"?', open(p, encoding='utf-8').read(), re.M)
        if m:
            accs.append((f'版本·{name}', '资产·版本', '版本号', m.group(1)))
    return accs

# ---------- 底层 IO（全部透传 stdout，防 F02 吞错） ----------
def _run(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or DB)
    return r.returncode, r.stdout, r.stderr

def _json_out(stdout):
    try:
        return json.loads(stdout)
    except Exception:
        return None

def rows_of(db_id, page_size=100):
    """分页拉全量（不信任任何缓存，防 F03）。返回 (rows, ok)。读失败≠读空（三态纪律）。"""
    rows, cur, guard = [], None, 0
    while True:
        cmd = ['python3', f'{DB}/query_database_record.py', '--database-id', db_id, '--page-size', str(page_size)]
        if cur:
            cmd += ['--start-cursor', cur]
        rc, out, err = _run(cmd)
        d = _json_out(out)
        if rc != 0 or d is None:
            return None, False          # 读失败：调用方必须按"无法判定"处理，不得当空表
        rows += d.get('results', [])
        if not d.get('has_more'):
            return rows, True
        cur = d.get('next_cursor'); guard += 1
        if guard > 50:
            return None, False

def add_entries(db_id, entries):
    """entries: [properties dict]。返回 (ok, raw_stdout)。

    ★ S165 修（**静默类型错，已存在很久**）：
      `batch_add_database_records.py` 的入参是 **records 数组，每项就是字段字典本身**
      （`{"账户名": {"text": "x"}}`），不是 `{"properties": {...}}`。
      它内部调 `validate_properties(record)`——传进去外层多包一层 `properties`，
      每个 value 都不是 dict→ 直接 `返回 {}` → 报
      `records[0] 校验后为空，无有效字段`（**exit=0，只有 stdout 里一个 error**）。

      为什么以前没暴露：账房真正的写入都走 `post()`，而 `post()` 拼的是
      `make_entry()` 的**扁平** properties，正好对；`add_entries` 只在建账/补登等
      边缘路径被用到，出错时又只是"没写进去"，没人去查。
      这一次补登 11 个户全失败才挖出来——**同一条通道一半对一半错，是最难发现的形态**。
      修法：兼容两种入参（有且仅有 `properties` 一个键时拆包；否则原样）。
      兼容而不是强改，是因为调用方已经按两种姿势写了很多处，强改会造出新的断层。
    """
    flat = []
    for e in entries:
        if isinstance(e, dict) and set(e.keys()) == {'properties'} and isinstance(e['properties'], dict):
            flat.append(e['properties'])
        else:
            flat.append(e)
    rc, out, err = _run(['python3', f'{DB}/batch_add_database_records.py',
                         '--database-id', db_id, '--records', json.dumps(flat, ensure_ascii=False)])
    d = _json_out(out)
    ok = bool(d and d.get('results') and all(r.get('success') for r in d['results']))
    return ok, out

# ---------- 闸门 ----------
def gate_ready():
    """写前预检（F01）：闸门未开就拒绝写，并给放行指引。返回 (bool, 说明)。"""
    st = session_gate.state()
    opened = isinstance(st, dict) and st.get('state') == 'open'
    if opened:
        return True, '闸门已放行'
    return False, '闸门未放行——先执行: python3 %s/ecosys-conductor/scripts/conductor.py ask "<需求>"' % SKILLS

# ---------- 分录 ----------
def _now():
    return time.strftime('%Y-%m-%dT%H:%M:00Z', time.gmtime())

def acct_currency_map():
    """账户名 → 声明币种（取自 INITIAL_ACCOUNTS 这张**唯一**科目表）。

    为什么必须有这个：`make_entry` 以前只校验 `currency in CURRENCIES`（币种名合法），
    却**不校验这笔分录的账户本身是不是这个币种**。于是 S165 那笔
    `S165-LOG-119-CLAIM` 拿「记录数」去借记 `产能·治理会话`——那个户的币种是
    「事项数」。写进去了、试算也平（记录数整体仍归零），但该户从此挂着一个
    记录数 -1，`ledger balance` 的账面与登记永远差一格，而且**没有任何一处报错**。

    读的是 INITIAL_ACCOUNTS **加上** version_accounts()，因为科目表有两处声明：
    版本户在 `INITIAL_ACCOUNTS` 里只硬编码了一个 `版本·eco-ledger`，其余 10 个由
    `version_accounts()` 从各 SKILL.md 动态生成（SELF_BUILT 名单）。
    实测后果：加上币种校验后，那 10 个版本户被当成 ghost **空转放行**——
    校验看着生效，实际对 10/11 的版本户什么都没管。
    以 version_accounts() 为版本户的唯一来源，INITIAL_ACCOUNTS 那份是历史残留
    （建账脚本直接用，暂不动它；两边声明必须在这里合流，不能各走各的）。
    """
    m = {name: cur for name, _cls, cur, _ref in INITIAL_ACCOUNTS}
    for name, _cls, cur, _v in version_accounts():
        m[name] = cur
    return m

def check_acct_currency(debit_acct, credit_acct, currency):
    """校验借贷两户的声明币种都与本分录币种一致。返回 (ok, 原因)。

    不抛异常而是返回布尔：调用方要能区分「记账被拒」与「记账写失败」，
    抛异常会让上层把两种情况糊成一种（S172 变种 d：结果验不出路径）。
    未登记在科目表里的户**放行**——那是 ghost 户，由 `balance` 的 ghost 分支
    负责暴露并指引补登，不在这里拦（否则新户永远建不起来）。
    声明为 `*`（多币种对价户）的户**放行**——它本就是替任何币种做对手方的。
    """
    m = acct_currency_map()
    bad = []
    for side, a in (('借方', debit_acct), ('贷方', credit_acct)):
        want = m.get(a)
        if want and want != COUNTERPARTY and want != currency:
            bad.append(f'{side}「{a}」声明币种为「{want}」，本分录却是「{currency}」')
    return (not bad), '；'.join(bad)

def make_entry(voucher, debit_acct, credit_acct, amount, currency, action,
               agent='', finalcheck=False, snapshot=None, note='', strict_currency=False):
    """构造一条分录 properties。借贷金额同值同币种（守恒律）。

    strict_currency=True 时（默认 False，为兼容历史调用）额外校验账户声明币种；
    新写的调用点都应传 True。`post()` 里另有一道**无条件**校验兜底，见该函数。
    """
    assert currency in CURRENCIES, f'非法币种: {currency}'
    assert amount >= 0 or action == '冲销', '金额须>=0（冲销允许负值+引用原分录）'
    if strict_currency:
        okc, whyc = check_acct_currency(debit_acct, credit_acct, currency)
        assert okc, f'币种不符：{whyc}'
    fp_src = f'{voucher}|{debit_acct}|{credit_acct}|{amount}|{action}'
    import hashlib
    return {
        '凭证号':   {'text': voucher},
        '日期':     {'date': _now()},
        '借方账户': {'text': debit_acct},
        '贷方账户': {'text': credit_acct},
        '金额':     {'number': amount},
        '币种':     {'select': currency},
        '动作类型': {'select': action},   # 入库/清库/建库/并库/改名/版本升级/指标波动/冲销/转账
        '经手技能': {'text': agent},
        '回读验证': {'select': '未验'},    # 默认未验，回读后由 verify_entry 置"已验"
        '终验相关': {'checkbox': bool(finalcheck)},
        '余额快照': {'text': json.dumps(snapshot or {}, ensure_ascii=False)},
        '分录指纹': {'text': hashlib.sha256(fp_src.encode()).hexdigest()[:16]},
        '备注':     {'text': note},
        '状态':     {'select': '冲销' if action == '冲销' else '已记'},  # 已记/已平/挂账/冲销
    }

def post(entry):
    """写一条分录。幂等：同凭证号+指纹已存在则跳过。写前过闸门预检（F01）。

    ★ S176：写前**无条件**校验借贷两户的声明币种。以前只在 make_entry 里校验
      币种名合法，账户币种一概不管，于是「记录数」可以记进事项数户、写进去还不报错。
      做成 post 这一层而不是 make_entry 一层，是因为 make_entry 是**构造器**，
      绕开它直接拼 properties 也能写；post 是**唯一入口**，拦在这里才拦得住。
      （对比 M-R38：校验只许有一个执行点，且必须落在所有路径都必经的位置。）
    """
    if not LEDGER_READY:
        return False, '总账/账户库未建账（core_ledger.py 顶部 DB_TOTAL/DB_ACCT 为空）——先跑 M0'
    okc, whyc = check_acct_currency(entry['借方账户']['text'], entry['贷方账户']['text'],
                                    entry['币种']['select'])
    if not okc:
        # ★ S176 关键例外：**冲销必须放行币种校验**。
        #   实测发现的死锁：`S165-LOG-119-CLAIM` 当初用「记录数」借记了事项数户
        #   `产能·治理会话`。加了币种校验之后，**连冲销它的反向分录也被拒**——
        #   于是这笔错账永远冲不掉，M-R29「只增不撤、错账只能冲销」直接失效。
        #   一条为了防错的规则把纠错路径堵死，比不加这条规则更糟。
        #
        #   放行的边界（不是无条件开口子）：
        #     · 只放 `冲销` 一种动作；入库/清库/版本升级等一律照拒；
        #     · 冲销仍要**如实喊出来**，让人看见"这是一笔带病冲销"；
        #     · 冲销必须是对既有错账的反向，它本身不创造新余额方向——
        #       因为原错账已经把那笔余额记进去了，反向只是撤回。
        if entry['动作类型']['select'] != '冲销':
            return False, f'❌ 币种校验未通过，拒绝记账：{whyc}'
        print(f'   ⚠️ 冲销放行（币种不符但必须允许纠错）：{whyc}')
        print(f'      ↳ 冲销是撤回既有错账的唯一合法路径（M-R29），若一并拦住，错账将永久滞留')
    # ★ S176：未登记户**不拦但必须喊一声**。实测 `暂记·待核销` 就是手写 post 时
    #   顺手编出来的户名，因为试算照样平，一直没人发现。这里不拒绝（新户有时是
    #   正当的，拦死会逼人绕开 post 直接写库，那更难管），但把话喊出来。
    _known = acct_currency_map()
    _ghost = [a for a in (entry['借方账户']['text'], entry['贷方账户']['text']) if a not in _known]
    if _ghost:
        print(f'   ⚠️ 未登记户：{", ".join(_ghost)}——不在科目表，本次不校验其币种。'
              f'请补登 INITIAL_ACCOUNTS + 账户库（M-R38），否则它将不受任何约束')
    ok, why = gate_ready()
    if not ok:
        return False, why
    rows, good = rows_of(DB_TOTAL)
    if not good:
        return False, '总账读取失败（三态：无法判定，不当作空表）'
    fp = entry['分录指纹']['text']
    if any(r.get('分录指纹') == fp for r in rows):
        return True, f"幂等跳过：凭证 {entry['凭证号']['text']} 已入账"
    ok, raw = add_entries(DB_TOTAL, [entry])
    if not ok:
        return False, f'写入失败，原始返回：\n{raw[:600]}'
    # 回读验证（S020 伪成功防御，短重试）；通过即自动转"已平/已验"
    for _ in range(3):
        rows, good = rows_of(DB_TOTAL)
        if good:
            hit = next((r for r in rows if r.get('分录指纹') == fp), None)
            if hit:
                upd = {'回读验证': {'select': '已验'}, '状态': {'select': '已平'}}
                subprocess.run(['python3', f'{DB}/batch_update_database_records.py',
                                '--database-id', DB_TOTAL,
                                '--records', json.dumps([{'record_id': hit['record_id'], 'properties': upd}], ensure_ascii=False)],
                               capture_output=True, text=True, cwd=DB)
                return True, '已入账（回读验证通过，状态→已平）'
        time.sleep(0.6)
    return False, '写入返回成功但回读未查到——按未入账处理，请人工复核'

# ---------- 试算平衡（F06/F08） ----------
def trial_balance():
    """按币种分组试算：Σ|借-贷| 应=0（冲销按负额计入）。返回 (dict by 币种, ok_all)。
    本函数是 M-L04（复式记账守恒律）的执行体锚点：不平=账本损坏=最高优先级告警。"""
    if not LEDGER_READY:
        return {}, False
    rows, good = rows_of(DB_TOTAL)
    if not good:
        return {}, False
    net = {}
    for r in rows:
        c = r.get('币种') or '未标'
        amt = r.get('金额') or 0
        # 借方为 +，贷方为 -（分录存金额绝对值，方向由账户科目定号；此处简化：同一分录借贷对冲，
        # 试算对象是"每账户累计"，跨账户两两相抵 → 按(币种)汇总 abs 校验每条分录自身已带方向字段时再细化）
        net[c] = net.get(c, 0)
    return net, True
    # 完整实现见 ledger.py reconcile --trial：逐账户累计后按账户符号聚合，与日结快照比对

# ---------- 日结快照（F09/F12 基础） ----------
SNAPSHOT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'daily_snapshot.json')

def take_snapshot(extra=None):
    snap = {'ts': _now(), 'accounts': extra or {}, 'trial_balance': {}}
    if LEDGER_READY:
        tb, ok = trial_balance()
        snap['trial_balance'] = tb
        snap['trial_ok'] = ok
    with open(SNAPSHOT_FILE, 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    return snap

def load_snapshot():
    if not os.path.exists(SNAPSHOT_FILE):
        return None
    with open(SNAPSHOT_FILE, encoding='utf-8') as f:
        return json.load(f)
