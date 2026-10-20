#!/usr/bin/env python3
"""规则探针独立入口（S176）。

背景：注册表里有 6 条规则的 probe.command 写成 `conductor verify` / `tvbox conductor verify`。
那是一条总闸门命令——跑一次要几分钟，且**分不清是哪条规则没过**（M-R95 自己说的：
「无探针即无法验证落地，等同死规则」）。用总闸门当单条规则的探针，等于没有探针。

本脚本把它们各自的**真实承载函数**单独调出来跑，每个都做双向验证：
  注入已知异常 → 必须捕获（hit）
  清理/正例   → 必须静默（clean）
只 hit 不 clean 是「恒报红」，只 clean 不 hit 是「哑巴」，两者都不算通过（M-R47③ 双侧验证）。

用法：python3 rule_probe.py M-R79
      python3 rule_probe.py --list
退出码：0=该规则探针通过；1=未通过；2=规则无探针实现
"""
import os
import sys
import json
import subprocess

# 自定位：本文件即 scripts/ 下，无��当前 cwd（注册表里的命令只写 `rule_probe.py`，
# 换任何工作目录启动都必须能跑。S176 教训：命令登记时能跑、换个 cwd 就跑不通，
# 与「从登记起就不可能跑通」是同一类缺陷，只是暴露得晚）。
COND = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(os.path.dirname(COND))
UNFIN = os.path.join(SKILL_ROOT, 'unfinished-audit', 'scripts')


def _p(fn, name):
    """带名字的单条探针结果。"""
    return {'ok': fn(), 'name': name}


# ============ M-R79 未完成不得无主 ============
def m_r79():
    """三重必填：owner/next/due 缺任一，_assign 必须 raise。"""
    sys.path.insert(0, UNFIN)
    import importlib
    import dispatch as D
    importlib.reload(D)
    hit = False
    # 注入：把 ROUTE_TABLE 的某个 kind 改成空 owner，_assign 应拒绝落账
    orig = dict(D.ROUTE_TABLE)
    try:
        D.ROUTE_TABLE['__probe__'] = ('', '')      # owner 与 next 皆空
        D.FINE_ROUTE = []                          # 关掉精细路由，确保走空值
        try:
            D._assign({'sid': 'zzprobe_r79', 'kind': '__probe__', 'title': '探针无主项'})
            hit = False                            # 没 raise = 守卫失效
        except RuntimeError as e:
            hit = '缺' in str(e) or '无主' in str(e)
    finally:
        D.ROUTE_TABLE.clear()
        D.ROUTE_TABLE.update(orig)
    # 清理/正例：正常 kind 必须产出三个字段且不 raise
    # ★ S176b：原写法 `except Exception: clean = False` 是 M-R60 明令禁止的折叠——
    #   异常被压成一个布尔值，调用方只看到"不干净"，不知道是**字段没产出**还是**代码抛错**。
    #   信号源16（基座折叠扫描）正是抓这个形态的，它抓到了本文件，判据是对的。
    #   改法：把异常类型/消息并入 clean 的判定依据，让它**说得出为什么**。
    clean, _clean_why = False, ''
    try:
        it = D._assign({'sid': 'zzprobe_r79b', 'kind': 'unknown', 'title': '正常项'})
        _miss = [k for k in ('owner', 'next', 'due') if not it.get(k)]
        clean = not _miss
        _clean_why = f'缺字段 {_miss}' if _miss else 'ok'
    except Exception as e:
        clean, _clean_why = False, f'{type(e).__name__}: {str(e)[:80]}'
    # 真实账本不得存在无主项（verify 判据⑧ 的实体口径）
    try:
        problems, _w = D.verify_ledger()
        noowner = [p for p in problems if p.startswith('无主项')]
        if noowner:
            clean = False
            _clean_why += f'｜实账有无主项 {len(noowner)} 条'
    except Exception as e:
        print(f'  ⚠️ verify_ledger 读取失败：{type(e).__name__}: {e}（M-R60 三态：勿当干净）')
        clean = False
        _clean_why += f'｜账本读取失败 {type(e).__name__}'
    print(f'  注入无主项→被拒={hit} ；正例三字段齐+实账无无主项={clean}（{_clean_why}）')
    return hit and clean


# ============ M-R91 索引校验须覆盖所有引用文件 ============
def m_r91():
    """_check_index_links 的注入模式：坏链接必须被抓，正常文本必须静默。"""
    sys.path.insert(0, COND)
    import conductor as C
    bad_txt = '正文外置 [描述](docs/DOES-NOT-EXIST-S176.md)\n'
    ok_txt = '正文外置 [描述](docs/rules_longform.md)\n'
    ok_bad, bad_list = C._check_index_links(_txt_override=bad_txt, _skills_override=['__s176__'])
    hit = (not ok_bad) and any('DOES-NOT-EXIST-S176' in b for b in bad_list)
    ok_ok, ok_list = C._check_index_links(_txt_override=ok_txt, _skills_override=['__s176__'])
    # 正例：真实存在的文件不应被判坏（__s176__ 目录不存在，任何链接都会判坏，
    # 故正例改用"不含外置引用"的空文本，验其静默）
    ok_empty, empty_list = C._check_index_links(_txt_override='无外置引用\n',
                                                _skills_override=['__s176__'])
    clean = ok_empty and not empty_list
    print(f'  注入坏链接→被抓={hit} ({bad_list[:1]}) ；无引用文本→静默={clean}')
    # 真跑一遍全生态索引，确认当前无断链（这是规则要守的实际面）
    real_ok, real_bad = C._check_index_links()
    print(f'  实跑全生态索引：{"完整" if real_ok else "断链 " + str(real_bad[:3])}')
    return hit and clean and real_ok


# ============ M-R92 总册只放判据主干 ============
def _overlong_rule_lines(txt, limit=300):
    """总册中「含规则号且超 limit 字节」的行 → [(规则号, 字节数)]。"""
    import re
    over = []
    for line in txt.splitlines():
        s = line.strip()
        if not s.startswith('|'):
            continue
        m = re.search(r'(M-R\d+|L0\d\d)', s)
        if not m:
            continue
        b = len(s.encode('utf-8'))
        if b > limit:
            over.append((m.group(1), b))
    return over


def m_r92():
    """总册每行 ≤300 字节，且**压缩后正本仍完整**（M-R92 的双侧：瘦身 vs 保真）。

    之前的写法 `return hit or clean` 恒为真——hit 与 clean 互斥，两者至少一真，
    等于一条永远通过的探针。这是把「探针」写成了「签名档」。现改为注入验证：
    合成一条超长行，判定器必须点名它；再跑真实总册，须已无超长行。
    """
    mp = '/workspace/生态1015_TVBox生态全局规则.md'
    if not os.path.exists(mp):
        print('  ⚠️ 总册不存在，无法判定（M-R60：不得折叠成通过）')
        return False
    # ① 注入：合成一条 400+ 字节的规则行，判定器必须抓到
    fake = '| **M-R999** | ' + ('很长的推演过程' * 40) + ' |'
    caught = [r for r, _b in _overlong_rule_lines(fake) if r == 'M-R999']
    # ② 真实总册
    txt = open(mp, encoding='utf-8').read()
    over = _overlong_rule_lines(txt)
    # ③ 保真：超长行压缩后，其 primary_owner 的 SKILL.md 仍须在（尺子改短了不能宣称达标）
    import json
    reg = json.load(open(os.path.join(COND, 'rules_registry.json'), encoding='utf-8'))
    owners = {r['id']: r.get('primary_owner') for r in reg['rules']}
    missing = []
    for rid, _b in over:
        ow = owners.get(rid)
        if ow and not os.path.exists(os.path.join(SKILL_ROOT, ow, 'SKILL.md')):
            missing.append(f'{rid}→{ow}')
    print(f'  注入超长行→被抓={bool(caught)} ；真实超长行={len(over)} 条 {[f"{r}({b}B)" for r, b in over[:5]]}')
    if missing:
        print(f'  ⚠️ 这些规则的正本 SKILL.md 不在：{missing}')
    return bool(caught) and not over and not missing


# ============ M-R94 治理刹车（B1/B3）============
def m_r94():
    """brake.budget/idle/check/cost_gate 四函数在册 + 阈值常量 + **熔断可真的触发**。

    check() 返回 (state, msgs)，state ∈ green/yellow/red/unknown。
    只验"函数存在+有返回值"是不够的——刹车装上了但踩不下去，等于是装饰，
    正是本条规则自己批的「诊断不进门禁，就永远只是文档」。
    """
    sys.path.insert(0, COND)
    import brake as B
    has = all(hasattr(B, n) for n in ('budget', 'idle', 'check', 'cost_gate'))
    consts = (getattr(B, 'RATIO_WARN', None) == 0.50
              and getattr(B, 'CONSEC_GOV_MAX', None) == 3)
    state, msgs = B.check()
    shaped = state in ('green', 'yellow', 'red', 'unknown') and isinstance(msgs, list) and msgs
    print(f'  check() → state={state} 段数={len(msgs) if isinstance(msgs, list) else "?"}')
    for s, m in (msgs if isinstance(msgs, list) else []):
        print(f'    [{s}] {m}')
    # 注入：把连续纯治理上限压到 0，check 必须由非 red 变 red（证明刹车真能踩下去）
    orig = B.CONSEC_GOV_MAX
    hit = False
    try:
        B.CONSEC_GOV_MAX = 0
        s2, m2 = B.check()
        hit = (s2 == 'red') and any('熔断' in x or 'B1' in x for _s, x in m2)
    finally:
        B.CONSEC_GOV_MAX = orig
    s3, _m3 = B.check()   # 恢复后应与注入前一致（不做脏状态）
    clean = (s3 == state)
    print(f'  四函数齐={has} 阈值在册={consts} 结构合法={bool(shaped)} 注入0阈→red={hit} 恢复后还原={clean}')
    return has and consts and shaped and hit and clean


# ============ M-R95 立法成本门（B2）============
def m_r95():
    """B2 成本门：①门本身能拦住缺三件套的假规则 ②**当前规则库真的干净**。

    第 ② 条是重点：本规则的原话是「验不过入黄名单」——如果探针只验"能识别假规则"
    而不管真库结果，那么门装上了、黄名单天天挂着 4 条、没人看见，等于没装。
    这正是它自己的 note 里写的「刚立的门禁不能立刻当真理用，须先自己过一遍」。
    """
    sys.path.insert(0, COND)
    import brake as B
    import importlib
    importlib.reload(B)
    # ① 注入：构造一条缺三要素的假规则，cost_gate 必须点名它
    fake = {'id': 'M-R999', 'summary': '探针用假规则', 'chapter': 'x',
            'primary_owner': 'ecosys-conductor'}
    flagged = False
    try:
        res = B.cost_gate(fake)
        flagged = 'M-R999' in json.dumps(res, ensure_ascii=False)
    except TypeError as e:
        print(f'  ⚠️ cost_gate 不接受单条规则参数（{e}）→ 无法做单条注入')
    # ② 真实全库
    ok, problems = B.cost_gate()
    if ok is None:
        print(f'  ⚠️ registry 读不到：{problems}（M-R60 三态，勿当干净）')
        return False
    print(f'  注入假规则→被点名={flagged} ；真实全库 ok={ok} 违规 {len(problems)} 项')
    for p in problems[:6]:
        print(f'    - {p}')
    if len(problems) > 6:
        print(f'    … 另 {len(problems) - 6} 项')
    return flagged and bool(ok) and not problems


# ============ M-R117 会话须交代落点/证据 ============
def m_r117():
    """signal-check --source 42：注入空/半/全三态，捕获+清理双向验证。"""
    r = subprocess.run([sys.executable, 'conductor.py', 'signal-check', '--source', '42'],
                       cwd=COND, capture_output=True, text=True, timeout=180)
    out = r.stdout + r.stderr
    hit = '捕获=是' in out
    clean = '清理后清零=是' in out
    line = [ln.strip() for ln in out.splitlines() if '信号源42' in ln]
    print(f'  {line[0] if line else out.strip()[:160]}')
    return hit and clean


PROBES = {
    'M-R79': m_r79,
    'M-R91': m_r91,
    'M-R92': m_r92,
    'M-R94': m_r94,
    'M-R95': m_r95,
    'M-R117': m_r117,
}


# ============ M-R118 探针须可独立执行、且真被执行 ============
def m_r118():
    """规则 probe 不许拿总闸门当单条探针，且登记的命令必须真被执行。

    注入验证：扫描全库，统计「把 verify/assess 当探针」与「登记了却从不被执行」的条数。
    本条守的是**探针制度的可执行性**——没有执行器的探针字段等于文档。
    """
    import json as _j
    reg = _j.load(open(os.path.join(COND, 'rules_registry.json'), encoding='utf-8'))
    heavy_as_probe = []
    for r in reg['rules']:
        c = str(((r.get('probe') or {}).get('command')) or '')
        if not c:
            continue
        # 总闸门当单条探针：整条命令就是 verify/assess（允许 tail/管道）
        base = c.split('|')[0].strip()
        if base.split()[-1:] in (['verify'], ['assess']) or base.endswith(' verify'):
            heavy_as_probe.append(r['id'])
    # 执行器存在性（结构检查，**不递归调用**——S176 首版写成
    # `rules-probe --only M-R118` 让探针去跑自己，直接死循环超时）
    src = open(os.path.join(COND, 'conductor.py'), encoding='utf-8').read()
    has_runner = ('def cmd_rules_probe' in src and "'rules-probe'" in src
                  and 'subprocess.run(cmd' in src)
    # 执行器的核心能力自检：能把一条探针真跑起来并拿到 rc（用最轻的 M-R119 做样本）
    p = subprocess.run([sys.executable, 'conductor.py', 'rules-probe', '--only', 'M-R119'],
                       cwd=COND, capture_output=True, text=True, timeout=120)
    ran = ('实跑' in (p.stdout or '')) and ('rc=0' in (p.stdout or ''))
    print(f'  总闸门当探针={len(heavy_as_probe)} 条 {heavy_as_probe[:5]}')
    print(f'  执行器 rules-probe 在册={has_runner} ；能真跑一条拿 rc={ran}')
    return has_runner and ran and not heavy_as_probe


# ============ M-R119 探针命令须自定位 ============
def m_r119():
    """路径/前缀/程序名三类缺陷：相对路径、缺 tvbox 前缀、凭空捏的命令名。

    这三类的共同点：**登记时看着对，执行时跑不通**，且都不会在评审时暴露。
    """
    import json as _j
    import re as _re
    reg = _j.load(open(os.path.join(COND, 'rules_registry.json'), encoding='utf-8'))
    bad = []
    for r in reg['rules']:
        c = str(((r.get('probe') or {}).get('command')) or '')
        if not c:
            continue
        if _re.match(r'^(conductor|inspect)\s', c):
            bad.append((r['id'], '缺 tvbox 前缀', c))
        elif _re.match(r'^python3\s+(skill-|tvbox-|eco-|learn-|probe-)', c):
            bad.append((r['id'], '相对路径', c))
        elif c.startswith('probe '):
            bad.append((r['id'], '裸 probe', c))
    # 注入：三条已知坏样例必须全部被识别
    samples = ['conductor size', 'python3 skill-x/scripts/y.py', 'probe self-check']
    caught = 0
    for s in samples:
        if (_re.match(r'^(conductor|inspect)\s', s)
                or _re.match(r'^python3\s+(skill-|tvbox-|eco-|learn-|probe-)', s)
                or s.startswith('probe ')):
            caught += 1
    print(f'  现存缺陷={len(bad)} 条 {[(x[0], x[1]) for x in bad[:5]]}')
    print(f'  注入 3 类坏样例→识别 {caught}/3')
    return caught == 3 and not bad


PROBES['M-R118'] = m_r118
PROBES['M-R119'] = m_r119


# ============ M-R122 可对话门禁②：收后台结果必须非阻塞 ============
def m_r122():
    """双向验证 block_wait_guard 钩子：block=true 须被拦（rc=2），
    block=false 与其它工具须放行（rc=0）。只测钩子本体（直接喂数据），
    不真的调 TaskOutput——那会需要存在一个真后台任务，探针没法自备。"""
    HOOK = '/root/.codebuddy/hooks/block_wait_guard.py'
    if not os.path.exists(HOOK):
        print(f'  钩子缺失：{HOOK}')
        return False
    def _rc(payload):
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload),
                           capture_output=True, text=True, timeout=15)
        return r.returncode
    hit = _rc({'tool_name': 'TaskOutput', 'tool_input': {'task_id': 'x', 'block': True}}) == 2
    clean = (_rc({'tool_name': 'TaskOutput', 'tool_input': {'task_id': 'x', 'block': False}}) == 0
             and _rc({'tool_name': 'Bash', 'tool_input': {'command': 'ls'}}) == 0)
    print(f'  阻塞等待须拦={hit} ；非阻塞/其它工具须放行={clean}')
    return hit and clean


PROBES['M-R122'] = m_r122


# ============ M-R126 自由文本入参不许被分词截断 ============
def m_r127():
    """双向验证分诊：账本显式 `kind`/`next` 必须压过关键词猜与命令模板。

    **不碰真账本**——`collect_issues` 直接读 issues_ledger.json，`_assign` 只改内存
    对象。故这里只测纯函数 `_assign`：构造两条假条目（一条带显式声明、一条不带），
    看它是否按声明办、以及对没声明的仍走猜。

    hit  ：显式 kind=cloud + 显式 next → owner/next 照办，命令模板不许覆盖
    clean：没写 kind/next 的条目 → 仍按标题猜（不许因为加了通道就全都落到兜底）
    """
    import importlib.util
    D = '/root/.codebuddy/skills/unfinished-audit/scripts/dispatch.py'
    S = '/root/.codebuddy/skills/unfinished-audit/scripts'
    if not os.path.exists(D):
        print(f'  dispatch.py 缺失：{D}')
        return False
    if S not in sys.path:
        sys.path.insert(0, S)          # dispatch 顶层 `import collect`
    spec = importlib.util.spec_from_file_location('_dsp_probe', D)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f'  无法载入 dispatch（{type(e).__name__}: {e}）')
        return False
    assign = getattr(mod, '_assign', None)
    if assign is None:
        print('  dispatch 未暴露 _assign——本探针无法验证（不等于通过）')
        return False

    # ① 显式声明：P030 原样（标题含「探针」会被猜成 code，但它是 cloud + 等人做）
    import datetime
    a = {'sid': 'probe0000001', 'title': '备份系统三处遗留：③ 探针遗留物待人工清',
         'kind': 'cloud', 'evidence': 'issues_ledger #P030',
         'next_fixed': '用户在客户端核对孤儿夹附件后清账'}
    assign(a)
    # due 按 DUE_DAYS[kind] 算——**从表里读，不写死天数**（写死就变成第二个真源）
    want_due = (datetime.date.today()
                + datetime.timedelta(days=mod.DUE_DAYS.get('cloud', 7))).isoformat()
    hit = (a['owner'] == 'ecosys-conductor'
           and a['next'] == '用户在客户端核对孤儿夹附件后清账'
           and a['due'] == want_due)

    # ② 无声明：仍走关键词猜（'脚本' → code → legacy-watchdog），不许一律兜底
    b = {'sid': 'probe0000002', 'title': 'checker 脚本 语法 失效',
         'kind': 'code', 'evidence': 'x'}
    assign(b)
    clean = (b['owner'] == 'legacy-watchdog' and bool(b['next']))

    # ③ S181 复发：显式 kind 必须压过 FINE_ROUTE 关键词（S180 只压过了 ROUTE_TABLE）
    #    实测病灶：P032 kind=monitor，标题里「云端优先」命中 FINE_ROUTE → 又派回 janitor
    c = {'sid': 'probe0000003', 'kind': 'monitor', 'kind_declared': True,
         'title': '探源司谎报（云端优先须走 save_targets）', 'evidence': 'x'}
    assign(c)
    hit_c = (c['owner'] == 'tvbox-inspect')

    # ④ 但机器猜的 kind **不许**因此把 FINE_ROUTE 整张表废掉（否则换个方向误判）
    d = {'sid': 'probe0000004', 'kind': 'unknown',
         'title': '云端第⑤源只扫本地缓存', 'evidence': 'x'}
    assign(d)
    hit_d = (d['owner'] == 'tvbox-janitor')

    print(f'  显式 kind/next 须照办={hit}（owner={a["owner"]} next={a["next"][:18]}…）')
    print(f'  无声明仍按性质猜={clean}（owner={b["owner"]} next={b["next"][:22]}…）')
    print(f'  显式 kind 压过 FINE_ROUTE={hit_c}（owner={c["owner"]}）')
    print(f'  猜测型不得废 FINE_ROUTE={hit_d}（owner={d["owner"]}）')
    if not hit:
        print(f'    ↑ 期望 owner=ecosys-conductor / next=用户在客户端核对… / due={want_due}')
    return hit and clean and hit_c and hit_d


def m_r126():
    """双向验证 `issues add` 的标题解析：**直接 import conductor 的解析逻辑**跑。

    为什么不跑真 CLI：`issues add` 会真的落盘进 issues_ledger.json，
    探针跑一次就给总账加一条假账。故探针测**解析函数**，不测副作用。

    hit  ：标题含空格 + 带 --from/--next → 标题须**逐字完整**、选项须正确切出
    clean：标题里出现 `--` 但后面不是 from/next 关键字 → 不许被误当选项切走
    """
    import importlib.util
    C = '/root/.codebuddy/skills/ecosys-conductor/scripts/conductor.py'
    if not os.path.exists(C):
        print(f'  conductor.py 缺失：{C}')
        return False
    spec = importlib.util.spec_from_file_location('_cdr_probe', C)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)          # conductor 顶层无副作用（只有定义）
    except Exception as e:
        print(f'  无法载入 conductor（{type(e).__name__}: {e}）')
        return False
    parse = getattr(mod, '_parse_issue_add', None)
    if parse is None:
        print('  conductor 未暴露 _parse_issue_add——本探针无法验证（不等于通过）')
        return False

    # ① 截断回归：这就是 S179 出事的那条（原实现只留「备份系统三处遗留：①13」）
    long_title = ('备份系统三处遗留：① 13 个同名「20261020_生态备份」已改名区分；'
                  '② uploaded_by_day 只记文件名不记落到哪个夹；③ 探针遗留物待人工清')
    t1, s1, n1 = parse(f'{long_title} --from "S179 立规" --next "客户端核对后清账"')
    # ② 误切：`--` 后面不是 from/next，标题必须原样保留
    t2, s2, n2 = parse('标题里含 -- 符号 但不是选项')
    hit = (t1 == long_title and s1 == 'S179 立规' and n1 == '客户端核对后清账')
    clean = (t2 == '标题里含 -- 符号 但不是选项' and s2 == '复盘' and n2 == '')

    # ③ 选项值同样是自由文本，不许被 \S+ 截断（S180 实测：P031 next 只剩「先补」）
    bare_next = '先补 TYPE2SKILL 两映射（否则复发源一直在漏）；再定 commit 的落点'
    t3, s3, n3 = parse(f'某标题 --from S180 --next {bare_next}')
    hit3 = (t3 == '某标题' and s3 == 'S180' and n3 == bare_next)
    # ④ 两个裸值挨着：不许把下一个选项吞进上一个值
    t4, s4, n4 = parse('标题 --from 甲 乙 丙 --next 丁 戊')
    hit4 = (t4 == '标题' and s4 == '甲 乙 丙' and n4 == '丁 戊')

    print(f'  长标题须逐字完整={hit}（得 {len(t1)}/{len(long_title)} 字）')
    print(f'  非选项 -- 不得误切={clean}（得 {t2!r}）')
    print(f'  裸值 --next 不得截断={hit3}（得 {n3!r}）')
    print(f'  两个裸值不得互吞={hit4}（from={s4!r} next={n4!r}）')
    if not hit:
        print(f'    期望: {long_title[:60]}…')
        print(f'    实得: {t1[:60]}…')
    if not hit3:
        print(f'    期望 next: {bare_next!r}')
        print(f'    实得 next: {n3!r}')
    return hit and clean and hit3 and hit4


PROBES['M-R126'] = m_r126
PROBES['M-R127'] = m_r127


# ============ M-R128 交付报告七段闭环 ============
# 为什么探针要做得这么细（S183 立规时的自我提醒）：
#   M-R128 守的是**交付物格式**。格式要求最容易的失效方式是"文件还在、段落被悄悄合并或改名"——
#   看起来有报告，实际上 ③过程坑 被并进 ②过程、⑤后续问题 被删掉。
#   只验"模板文件存在"等于没验：文件存在但它可以一个月前就写错了。
#   故本探针验三件，缺一即红：
#     ① 模板文件里的七段**名字与顺序**逐字对；
#     ② 模板里"代码块内的正文骨架"也完整（防只改目录、骨架漏段）；
#     ③ 会话开场必读摘要 user_habit_brief.md 里 M-R128 段真的在（防"规则登记了但开场读不到"）。
#   再做**注入验证**：临时造一份"合并了③、删掉⑤"的坏模板文本喂给同一个校验函数，
#   必须被抓住——只验正例的话，校验函数写成恒真也照样通过（M-R47③ 双侧验证）。
def m_r128():
    import re

    SEVEN = [('一', '原因'), ('二', '过程'), ('三', '过程坑'), ('四', '解决方案'),
             ('五', '后续带出的问题'), ('六', '后续问题解决过程'), ('七', '完工经验总结')]

    def audit(txt):
        """返回 (七段是否齐且顺序对, 实际解析到的段名列表, 缺失项)。"""
        got = re.findall(r'^## ([一二三四五六七])、\s*(.+?)\s*$', txt, re.M)
        # 去重：模板里"代码块骨架"与"正文示例"同名段会各出现一次，按首次出现判顺序
        seen, order = set(), []
        for num, name in got:
            if num not in seen:
                seen.add(num)
                order.append((num, name))
        miss = [(n, nm) for n, nm in SEVEN if n not in seen]
        # 顺序：七段在文本中的首次出现位置必须递增
        pos = [txt.find(f'## {n}、') for n, _ in SEVEN]
        ordered = all(p >= 0 for p in pos) and pos == sorted(pos)
        return (not miss) and ordered, order, miss

    T = os.path.join(os.path.dirname(COND), 'references', '交付报告_七段模板.md')
    B = os.path.join(os.path.dirname(COND), 'references', 'user_habit_brief.md')
    if not os.path.exists(T):
        print(f'  ❌ 模板缺失：{T}')
        return False
    tmpl = open(T, encoding='utf-8').read()
    ok1, order, miss = audit(tmpl)

    # ② 代码块骨架也须完整（防"目录改了、骨架还是旧的"）
    i = tmpl.find('````markdown')
    j = tmpl.find('````', i + 10) if i >= 0 else -1
    if i < 0 or j < 0:
        print('  ❌ 模板里找不到 ````markdown 骨架块')
        ok2 = False
    else:
        ok2, _, miss2 = audit(tmpl[i:j])
        if not ok2:
            print(f'     骨架缺段：{miss2}')

    # ③ 会话开场必读摘要须含 M-R128 段（否则"登记了但没人读到"）
    ok3 = os.path.exists(B) and 'M-R128' in open(B, encoding='utf-8').read()

    # ④ 注入：合并③ + 删⑤ 的坏模板，audit 必须判红
    bad = tmpl.replace('## 三、过程坑', '## 三、过程（含过程坑）')
    bad = re.sub(r'^## 五、后续带出的问题.*?^## 六、', '## 六、', bad, flags=re.S | re.M)
    caught, _, badmiss = audit(bad)
    ok4 = (not caught) and len(badmiss) >= 1

    print(f'  模板七段齐且有序={ok1}（{len(order)} 段：{"/".join(n for _, n in order)}）')
    if not ok1:
        print(f'     缺段：{miss}')
    print(f'  正文骨架同步完整={ok2}')
    print(f'  开场摘要含 M-R128={ok3}')
    print(f'  注入「合并③+删⑤」→ 被判红={ok4}（识别缺段 {[n for n, _ in badmiss]}）')
    return ok1 and ok2 and ok3 and ok4


PROBES['M-R128'] = m_r128


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(2)
    if sys.argv[1] == '--list':
        print('\n'.join(sorted(PROBES)))
        sys.exit(0)
    rid = sys.argv[1]
    fn = PROBES.get(rid)
    if not fn:
        print(f'无探针实现：{rid}')
        sys.exit(2)
    print(f'🔬 {rid}')
    try:
        ok = fn()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f'  ❌ 探针自身异常：{type(e).__name__}: {e}')
        sys.exit(1)
    print(f'  {"✅ 通过" if ok else "❌ 未通过"}')
    sys.exit(0 if ok else 1)
