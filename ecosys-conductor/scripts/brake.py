#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""brake.py —— 治理刹车（S138·M-R94/M-R95）

## 为什么要有这个文件

S124 的全景复盘已经写明：「这不是设计缺陷，是缺少刹车。规则里全是"必须做 X"，
没有一条说"什么时候该停"」，并提出 B1/B2/B3 三条草案，末句是「请明确指示」。

**结果是刹车没装，治理又跑了 9 轮（S130→S138）：规则 82→100 条、信号源 27→31 个，
而这 9 轮没有产出任何一件可复用的业务承载。治理占比从 89% 涨到 90%，返工率从 25% 涨到 28%。**

所以这个模块不是"再加一条规则"，而是给治理行为本身装上**可执行的停止条件**。
判据必须由机器算、由命令拦，否则又是一句口号。

## 三条刹车

- **B1 治理预算**：治理:业务 应为 1:2。当前占比 >50% 即报警；**连续 ≥3 个纯治理会话即熔断**
  （熔断 = 新治理动作要求先做一件业务事，不是禁止思考）。
- **B2 立法成本门**：新增规则必须同时写出 ①它防的具体损失 ②维护它的成本
  ③守护它的信号源/探针。**无探针可守的规则不准立**——否则规则会只增不减。
- **B3 清账停工**：问题总账 open=0 时，禁止主动开启新治理轮次，除非业务面暴露硬缺陷。

## 用法

    python3 brake.py check          # 综合刹车状态（waste/verify 调用）
    python3 brake.py budget         # B1：治理:业务 占比 + 连续治理轮次
    python3 brake.py cost <id>      # B2：校验某条规则是否满足立法成本门
    python3 brake.py idle           # B3：open=0 时是否该停工

退出码：0=可继续；1=建议停（黄）；2=应停（红，熔断）。
"""

import json
import os
import re
import sys

SKILL_ROOT = '/root/.codebuddy/skills'
CONDUCTOR = os.path.join(SKILL_ROOT, 'ecosys-conductor')
REGISTRY = os.path.join(CONDUCTOR, 'scripts', 'rules_registry.json')
LEDGER_DB = 'TO4LwsjVVdR8JRAUlkejk3'

# —— B1 单一真源：什么算「业务」 ——
# 这个元组原先散落在 cmd_waste 里硬编码（M-R38 违规：同一事实两处定义）。
# 抽到这里作唯一真源，waste / budget / check 全部从这里派生。
BIZ_KINDS = ('新接口处理', '整包入库', '扫描归源库', '出库交付', '补源', '失效复测')
GOV_KINDS = ('规则维护', '纠错返工', '查询答疑', '生态治理')

# B1 阈值（唯一真源，代码禁硬编码第二份）
RATIO_WARN = 0.50        # 治理占比告警线
CONSEC_GOV_MAX = 3       # 连续纯治理会话上限，达到即熔断


def _ledger_rows():
    """读生态台账。失败返回 None（**不得折叠成空列表**，M-R60 三态）。"""
    try:
        sys.path.insert(0, os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'scripts'))
        import link
        return link.rows_of(LEDGER_DB)
    except Exception:
        return None


def budget():
    """B1：治理预算。返回 dict（含 ok / ratio / consec / reason）。

    `consec` 的口径：从台账**末尾**往前数，连续多少条属于治理类。
    为什么要连续计数而不是只看总占比：总占比是存量，**连续值才是增量趋势**——
    占比 90% 可能是历史沉淀，但「最近 3 条全是治理」说明当下正在失控。
    """
    rows = _ledger_rows()
    if rows is None:
        return {'ok': None, 'reason': '台账读取失败——**无法判定**，勿当健康（M-R60）'}
    n = len(rows) or 1
    tc = {}
    for r in rows:
        k = r.get('触发类型', '?')
        tc[k] = tc.get(k, 0) + 1
    biz = sum(v for k, v in tc.items() if k in BIZ_KINDS)
    gov = n - biz
    ratio = gov / n

    # ★ S158 修（原判据把「空值」当「治理」，连续计数虚高）：
    #   缺陷1：`r.get('触发类型','?')` —— 字段**存在但为空**时拿到的是 ''，不是 '?'，
    #          于是 `'' in BIZ_KINDS` 为 False → 空值被算成治理。实测 S108/S109 两条
    #          早期台账没填触发类型，其中 S109 明确是"采源+测活+入库"的**业务活**，
    #          却被计入连续治理。**"不知道"被当成了"是"**（M-R60 的同款错误，只是换了个字段）。
    #   缺陷2：只要有一条空值，连续计数就跨过它继续往上累计——脏数据把趋势算歪。
    #   修法：空值/未知值 → **跳过并计数**（不参与连续判断，但要报出来），
    #         让"台账缺字段"这件事自己被看见，而不是偷偷抬高熔断次数。
    consec = 0
    unknown_n = 0
    for r in reversed(rows):
        k = (r.get('触发类型') or '').strip()
        if not k or (k not in BIZ_KINDS and k not in GOV_KINDS):
            unknown_n += 1
            continue                      # 跳过：既不算业务，也不算治理
        if k in BIZ_KINDS:
            break
        consec += 1

    unk_note = (f'；另有 {unknown_n} 条台账无有效触发类型（不参与连续计数，'
                f'但说明台账填写有缺）' if unknown_n else '')

    if consec >= CONSEC_GOV_MAX:
        state, ok = 'red', False
        reason = (f'**熔断**：连续 {consec} 个纯治理会话（上限 {CONSEC_GOV_MAX}）——'
                  f'下个动作应是业务事，不是治理事{unk_note}')
    elif ratio > RATIO_WARN:
        state, ok = 'yellow', True
        reason = f'治理占比 {ratio:.0%} > {RATIO_WARN:.0%}——治理自吞产出，应夹业务'
    else:
        state, ok = 'green', True
        reason = f'治理占比 {ratio:.0%}，节奏正常'
    return {'ok': ok, 'state': state, 'n': n, 'biz': biz, 'gov': gov,
            'ratio': ratio, 'consec': consec, 'reason': reason, 'by_kind': tc}


def cost_gate(rule=None):
    """B2：立法成本门。校验规则是否具备「三件套」。

    为什么要有这道门：S130→S138 规则从 82 涨到 100，**没有一条被删过**。
    只增不减的规则库必然膨胀到无人能读，而读不动 = 等于没有。
    要求的不是形式文档，是三样**能被机器验证**的东西：
      ① 防的具体损失（summary/note 里要有具体后果，不是「为了规范」）
      ② 维护成本（谁在什么场景要改它）
      ③ 守护探针（probe.command 非空 + 对应信号源真实存在）
    返回 (ok, problems)。
    """
    try:
        d = json.load(open(REGISTRY, encoding='utf-8'))
    except Exception as e:
        return None, [f'registry 读取失败：{type(e).__name__}: {e}']
    rules = d.get('rules', [])
    # 只审 M-R 系（设计铁律）——L 系是历史「坑解法」沉淀，立的时候还没有这道门，
    # 用今天的标准去追诉旧账只会制造 200+ 条噪音，把真问题埋掉（M-R14 精神：只提示不追诉）。
    rules = [r for r in rules if str(r.get('id', '')).startswith('M-R')]
    # 只对「这道门生效之后新立的法」强制（M-R80 起），旧账不追诉。
    # 实测：M-R1~M-R66 共 64 条全部没有 probe.command——它们立的时候还没有探针制度。
    # 若一律追诉，会产出 64×3≈200 条噪音，把「新法有没有守」这个真问题彻底埋掉。
    # 这正是 M-R14「只提示不判错」在治理面的应用。
    GRANDFATHER_BELOW = 80
    def _num(rid):
        m = re.fullmatch(r'M-R(\d+)', str(rid))
        return int(m.group(1)) if m else None
    rules = [r for r in rules if (_num(r.get('id')) or 0) >= GRANDFATHER_BELOW]
    targets = [r for r in rules if rule is None or r.get('id') == rule]
    if not targets:
        return None, [f'未找到规则 {rule}']

    problems = []
    for r in targets:
        rid = r.get('id', '?')
        # ③ 探针可守（最硬的一条：无探针的规则只会永远「已落地」而从不生效）
        #    **S176 修判据**：原实现只认 `probe.command`，把 4 条**明确声明**为
        #    「设计原则型·文档锚点」的规则（M-R108/109/110/112——回复语言、短代号映射、
        #    模型交接，本质是**会话行为**，没有任何命令能验）一律判成「无探针」。
        #    这与 `cmd_audit_growth` 里的 `_is_design_principle()` 口径**直接打架**：
        #    同一个判据两处实现，一处认设计原则、一处不认（M-R38 单一事实源）。
        #    逼这类规则编一条假命令，正是 M-R95 自己警告的「把名义承载从空着变成编造，
        #    比空着更坏」。故此处承认两种合法承载，但**要求可追溯**：
        #      A 工具承载型：probe.command 非空
        #      B 设计原则型：carrier 明示「设计原则型」且 verify_via 给出了验证途径
        pr = (r.get('probe') or {})
        _cmd = (pr.get('command') or '').strip()
        _car = str(pr.get('carrier') or '')
        _via = str(pr.get('verify_via') or '').strip()
        if not _cmd:
            if '设计原则' in _car and _via:
                pass          # B 类：有承载声明 + 有验证途径，放过（由 audit-growth 单列复审）
            else:
                problems.append(f'{rid}: 无 probe.command 且未声明设计原则型承载'
                                f'——没有守护手段的规则不准立（B2-③）')
        # ① 具体损失：summary 要有因果，不能只有祈使句
        sm = r.get('summary') or ''
        if not re.search(r'→|导致|否则|后果|记成|误判|失真|白做|错|丢|蒸发|消失|吞|漏|塌|'
                         r'失效|膨胀|不可信|无人|看不见|绕过|没法回答|隐性|假绿|假成功|'
                         r'为零|无意义|没人|无法|不报|漏报|误报|假红|自欺|裸奔|名存实亡', sm):
            problems.append(f'{rid}: summary 未见「不这么做会怎样」——立法须写清它防的具体损失（B2-①）')
        # ② 维护成本：landing 要指到具体文件/函数，不能空泛
        # S139：landing 有两种合法结构——字符串（单落点）与列表（多落点，归并规则常见）。
        # 原实现只吃字符串，遇到列表时 `len()` 拿到元素个数、`re.search` 在列表上抛错，
        # 整个 B2 判据直接失效（错误被上层吞成「不满足」）。**判据本身要能吃两种形态**，
        # 否则「规则写对了但判据说读不懂」会变成新的误判源。
        ld_raw = r.get('landing') or ''
        if isinstance(ld_raw, list):
            ld = ' '.join(str(x) for x in ld_raw if x)
        else:
            ld = str(ld_raw)
        _ld_ok = (len(ld) >= 12) and re.search(
            r'[A-Za-z0-9_]+\.(py|md|json|jsonl)'      # 指名文件
            r'|[a-z_]{3,}\(\)'                        # 指名函数（带括号）
            r'|[a-z_]{4,}\s*(?:类|函数|方法|入口)'      # 指名函数（中文描述）
            r'|/usr/|/root/\.codebuddy'               # 指名绝对路径
            r'|cmd_[a-z_]+|_sig_[a-z_]+|[a-z]+_[a-z_]+'  # 指名内部符号（下划线标识符）
            r'|[①-⑳]|\d+·|-b\b|⑦'                     # 指名到「环节/条目」（如 verify ⑦-b）
            , ld)
        if not _ld_ok:
            problems.append(f'{rid}: landing 未指向具体落地位置——写不清改哪里的规则没人能维护（B2-②）')
    return (not problems), problems


def idle():
    """B3：清账停工。open=0 时应停止主动开新治理轮，除非业务面有硬缺陷。

    返回 dict：open 数量、是否建议停工。
    注意：**不禁止修 bug**——业务真出问题时该修就修。B3 拦的是「没问题也开一轮治理」。
    """
    ledger = os.path.join(SKILL_ROOT, 'unfinished-audit', 'unfinished_ledger.json')
    if not os.path.exists(ledger):
        return {'ok': None, 'reason': f'欠账本不存在（{ledger}）——无法判定'}
    try:
        d = json.load(open(ledger, encoding='utf-8'))
    except Exception as e:
        return {'ok': None, 'reason': f'欠账本损坏：{type(e).__name__}: {e}——**无法判定**（M-R60）'}
    items = [x for x in d.get('items', []) if str(x.get('status', '')).lower() not in ('done', 'wontfix')]
    n_open = len(items)
    if n_open == 0:
        return {'ok': True, 'open': 0, 'state': 'idle',
                'reason': '欠账 open=0——**应停工**。除非业务面暴露硬缺陷，否则不再主动开治理轮（B3）'}
    return {'ok': True, 'open': n_open, 'state': 'working',
            'reason': f'欠账 open={n_open}，继续清账合理'}


def check():
    """综合刹车状态。返回 (state, msgs)：state ∈ green/yellow/red/unknown。"""
    msgs = []
    b = budget()
    i = idle()
    ok2, prob2 = cost_gate()

    if b.get('ok') is None:
        msgs.append(('unknown', f'B1 {b.get("reason")}'))
    else:
        msgs.append((('green' if b['state'] == 'green' else
                      ('yellow' if b['state'] == 'yellow' else 'red')), f'B1 {b["reason"]}'))
    if i.get('ok') is None:
        msgs.append(('unknown', f'B3 {i.get("reason")}'))
    else:
        msgs.append((('green' if i['state'] == 'idle' else 'green'), f'B3 {i["reason"]}'))
    if ok2 is None:
        msgs.append(('unknown', f'B2 {prob2}'))
    elif ok2:
        msgs.append(('green', 'B2 全部规则满足立法成本门'))
    else:
        msgs.append(('yellow', f'B2 {len(prob2)} 条规则不满足成本门'))

    order = {'red': 3, 'yellow': 2, 'unknown': 2, 'green': 1}
    state = max((s for s, _ in msgs), key=lambda s: order[s])
    return state, msgs


def main(argv):
    cmd = argv[0] if argv else 'check'
    if cmd == 'budget':
        b = budget()
        if b.get('ok') is None:
            print(f'⚠️ {b["reason"]}')
            return 2
        print(f'【B1 治理预算】会话 {b["n"]}：业务 {b["biz"]} vs 治理 {b["gov"]}（{b["ratio"]:.0%}）')
        print('   ' + '  '.join(f'{k}:{v}' for k, v in
              sorted(b['by_kind'].items(), key=lambda x: -x[1])))
        print(f'   连续纯治理会话：{b["consec"]}（上限 {CONSEC_GOV_MAX}）')
        print(f'   {"🔴" if b["state"] == "red" else ("🟡" if b["state"] == "yellow" else "✅")} {b["reason"]}')
        return 0 if b['ok'] else 2
    if cmd == 'cost':
        rid = argv[1] if len(argv) > 1 else None
        okk, probs = cost_gate(rid)
        if okk is None:
            print(f'⚠️ {probs}')
            return 2
        if okk:
            print(f'✅ {"全部规则" if rid is None else rid} 满足立法成本门（B2）')
            return 0
        print(f'⚠️ 不满足立法成本门 {len(probs)} 项：')
        for p in probs[:20]:
            print(f'   - {p}')
        if len(probs) > 20:
            print(f'   ... 另有 {len(probs)-20} 项')
        return 1
    if cmd == 'idle':
        i = idle()
        if i.get('ok') is None:
            print(f'⚠️ {i["reason"]}')
            return 2
        print(f'【B3 清账停工】{i["reason"]}')
        return 0
    # check
    state, msgs = check()
    icon = {'green': '✅', 'yellow': '🟡', 'red': '🔴', 'unknown': '⚠️'}
    print('🛑 治理刹车（S138·M-R94/M-R95：规则里全是「必须做」，缺一条「什么时候该停」）\n')
    for s, m in msgs:
        print(f'  {icon[s]} {m}')
    print()
    if state == 'red':
        print('🔴 熔断——下个动作应是业务事（出库交付/补源/新包加工），不是治理事')
        return 2
    if state == 'yellow':
        print('🟡 建议夹一件业务事再继续治理')
        return 1
    print('✅ 刹车未触发')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
