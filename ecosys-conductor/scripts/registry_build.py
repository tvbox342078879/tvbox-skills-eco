"""S177 步6 · 五方对照表 registry 构建器

目的：把「技能 ↔ 规则 ↔ 信号源 ↔ 监测项 ↔ 命令」这五张各自维护的表，
      合成**一张可查的对照表**，让每个问题都能回答三件事：
        ① 这个技能被什么守着？（规则/信号源/监测项）
        ② 这条规则落在哪？（owner 技能 / landing 锚点 / 是否有 probe）
        ③ 这个检查有没有入口、入口在哪？

为什么需要它：S177 第 3/4 步反复撞到同一类问题——
  「某项显示 ✅，但它从来没真查过」（M03 的 s 未定义、signal-check 恒 rc=0）。
  这类问题的根因都是**对照关系没人维护**：规则写了、技能在、但中间那根线断了，
  而断了不报错。本表把线显式化，断了就能一眼看出。

输出：/root/.codebuddy/skills/ecosys-conductor/plans/S177_registry.json
"""
import json, os, sys

SKILLS_ROOT = '/root/.codebuddy/skills'
COND_SCRIPTS = os.path.join(SKILLS_ROOT, 'ecosys-conductor', 'scripts')
sys.path.insert(0, COND_SCRIPTS)
import conductor as C

OUT = '/root/.codebuddy/skills/ecosys-conductor/plans/S177_registry.json'


def load_tvbox_route():
    """从 /usr/local/bin/tvbox 的 ROUTE 表解析 verb → 技能名。

    做法：把入口脚本当 Python 执行到「定义完 ROUTE 常量」为止不现实（它会跑 main），
    改用**静态解析**：
      ① 先抓所有 `XXX = '/root/.codebuddy/skills/<skill>/scripts/...'` 形式的常量 → {常量名: 技能名}
      ② 再抓 ROUTE = {...} 块，把 `'verb': CONST` 里的 CONST 换成技能名。
    这样入口一旦加新 verb / 改路径，本表自动跟上，不需要改这里。
    （硬编码一张 verb→技能 的表也能跑，但那是第二个真源，迟早和入口表打架。）
    """
    import re
    p = '/usr/local/bin/tvbox'
    try:
        txt = open(p, encoding='utf-8').read()
    except Exception as e:
        print(f'  ⚠️ 读不到 tvbox 入口（{type(e).__name__}: {e}）——命令归属将为空，**不是没有命令**')
        return {}
    const_skill = {}
    for m in re.finditer(r"^([A-Z][A-Z0-9_]*)\s*=\s*'([^']*)'", txt, re.M):
        const, path = m.group(1), m.group(2)
        mm = re.search(r'skills/([^/]+)/', path)
        if mm:
            const_skill[const] = mm.group(1)
    # ★ 补：有的常量是 `os.path.join(HUB, 'link.py')` 这种**拼接式**，上面的字面量正则抓不到。
    #   实测 LINK 就是这样定义的，导致 tvbox-source-hub 的命令数一直是 0——
    #   我一开始以为"这个技能没有命令"，其实它承载了 brief/status/sync/audit/log 等 9 个命令。
    #   又是同一个病：**取不到 ≠ 没有**。
    #   这里处理 `XXX = os.path.join(<已知常量>, '<file>.py')`：把已知常量的技能名继承下来。
    for m in re.finditer(r"^([A-Z][A-Z0-9_]*)\s*=\s*os\.path\.join\(\s*([A-Z][A-Z0-9_]*)\s*,",
                         txt, re.M):
        const, base = m.group(1), m.group(2)
        if const not in const_skill and base in const_skill:
            const_skill[const] = const_skill[base]
    # ROUTE 块
    m = re.search(r'^ROUTE\s*=\s*\{(.*?)^\}', txt, re.M | re.S)
    if not m:
        print('  ⚠️ tvbox 入口里没找到 ROUTE 块——命令归属将为空（结构变了？）')
        return {}
    body = m.group(1)
    route = {}
    for vm in re.finditer(r"'([^']+)'\s*:\s*([A-Z][A-Z0-9_]*)", body):
        verb, const = vm.group(1), vm.group(2)
        sk = const_skill.get(const)
        if sk:
            route[verb] = sk
    return route


def load_rules():
    p = os.path.join(COND_SCRIPTS, 'rules_registry.json')
    return json.load(open(p, encoding='utf-8')).get('rules', [])


def load_eco_skills():
    p = os.path.join(COND_SCRIPTS, 'eco_skills.json')
    return json.load(open(p, encoding='utf-8')).get('eco_skills', [])


def load_manifest_cmds():
    p = os.path.join(SKILLS_ROOT, 'tvbox-source-hub', 'manifest.json')
    d = json.load(open(p, encoding='utf-8'))
    out = []
    for x in d.get('commands', []):
        if isinstance(x, dict) and x.get('cmd'):
            out.append({'cmd': x['cmd'], 'desc': x.get('desc', ''),
                        'intent': x.get('intent', '')})
    return out


def load_signal_checks():
    """信号源 → 承担该源的技能。取 SIGNAL_SOURCES 名字 + 各源 probe 中出现的技能名。"""
    return dict(C.SIGNAL_SOURCES)


def load_monitors():
    """监察司监测项（43 项）。

    ★ 踩到的坑（第一版就在这里静默折叠了）：
      我原来写 `for name in ('MONITORS','MONITOR_DEFS','_MONITORS'): v=getattr(IE,name); if isinstance(v,list): return v`
      —— 实测这三个名字里 `MONITORS` **是一个 51 字符的字符串（路径）**，
      不是列表。于是循环全落空，函数 `return []`，
      对照表上「监测项 0」看起来像「本来就没有监测项」。
      **这正是 M-R60 说的折叠：读不到 ≠ 没有。**
      真数据入口是 `_monitors_all()`。修法：优先调函数，并把失败原因打出来。
    """
    try:
        sys.path.insert(0, os.path.join(SKILLS_ROOT, 'tvbox-inspect', 'scripts'))
        import inspect_engine as IE
        # 真入口：函数
        for fname in ('_monitors_all', 'load_monitors'):
            fn = getattr(IE, fname, None)
            if callable(fn):
                got = fn()
                if isinstance(got, list) and got:
                    return got
                if isinstance(got, dict):
                    vals = list(got.values())
                    if vals:
                        return vals
        # 兜底：真的列表变量
        for name in ('MONITOR_DEFS', '_MONITORS'):
            v = getattr(IE, name, None)
            if isinstance(v, list) and v:
                return v
        print('  ⚠️ 监测项：所有入口都取到空/非列表——'
              '**本列留空，不代表没有监测项**（M-R60）')
        return []
    except Exception as e:
        print(f'  ⚠️ 监测项读取失败（{type(e).__name__}: {e}）'
              f'——本列留空，不是"没有监测项"（M-R60）')
        return []


def _landing_items(rule):
    """把 landing 规范成 [(skill, anchor, type)]。

    ★ 踩到的坑（记在这里，别再犯）：landing 有两种形态——
      ① list[dict]：{'skill':..,'anchor':..,'type':..}
      ② **裸 str**：'conductor expand-eco'
    直接 `for ld in rule['landing']` 遇到 ② 会**逐字符遍历**，
    得到 'c','o','n','d','u','c','t','o','r',' ',... 一串单字母，
    然后 AttributeError: 'str' object has no attribute 'get'。
    更坏的情形是：若循环体里只做 `ld in something` 判断，**它不会报错**，
    只会静默产出 453 个假的"锚点"（实测就是这个数）。
    ——字符串被当列表遍历，是 Python 里最安静的一类错。
    """
    raw = rule.get('landing')
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for ld in raw:
        if isinstance(ld, dict):
            out.append((ld.get('skill'), ld.get('anchor'), ld.get('type')))
        elif isinstance(ld, str):
            # 裸字符串：约定第一段是技能名（'conductor expand-eco' → conductor）
            seg = ld.split()[0] if ld.split() else ''
            out.append((seg, ld, 'freeform'))
    return out



def build():
    skills = load_eco_skills()
    rules = load_rules()
    sigs = load_signal_checks()
    monitors = load_monitors()
    cmds = load_manifest_cmds()

    # ---- 按技能索引 ----
    by_skill = {}
    for s in skills:
        name = s if isinstance(s, str) else s.get('name')
        if not name:
            continue
        by_skill[name] = {
            'rules_owned': [],       # 我 primary_owner 的规则
            'rules_touched': [],     # 我参与 landing 的规则
            'signals': [],           # 与我名字相关/我承担的信号源（按名字推断）
            'monitors': [],
            'commands': [],
        }

    # 规则 → 技能
    for r in rules:
        po = r.get('primary_owner')
        if po in by_skill:
            by_skill[po]['rules_owned'].append(r['id'])
        for _sk, _anchor, _type in _landing_items(r):
            sk = _sk
            # 'conductor' 这类短名要在登记表里找对应全名
            if sk and sk not in by_skill:
                for name in by_skill:
                    if name.endswith(sk) or sk in name.split('-'):
                        sk = name
                        break
            if sk in by_skill and r['id'] not in by_skill[sk]['rules_touched']:
                by_skill[sk]['rules_touched'].append(r['id'])
        for sk in (r.get('owner') or []):
            if isinstance(sk, str) and sk in by_skill and r['id'] not in by_skill[sk]['rules_touched']:
                by_skill[sk]['rules_touched'].append(r['id'])

    # 监测项 → 技能
    # ★ 实测：监测项**没有 owner/skill 字段**（45 项全是 id/name/check/desc/...）。
    #   所以原来那个 `owner = m.get('owner') or m.get('skill')` 恒为 None，
    #   导致「监=0」—— 又是"看起来像没有，其实是取不到"。
    #   真正的关联线索是 `check` 字段（如 'ledger_pending'），它指向 inspect_engine 里的实现函数。
    #   本表按 check 名反查函数、再按函数所在文件归属技能：
    #   45 项全部由 tvbox-inspect 承载（除少数跨技能调用），故先记为 tvbox-inspect，
    #   并对能在其它技能 scripts 里找到同名函数的，额外记一份。
    monitor_owner_map = {}
    for m in monitors:
        if not isinstance(m, dict):
            continue
        mid = m.get('id')
        chk = m.get('check')
        owner_sk = 'tvbox-inspect'     # 默认承载者（监测引擎本体）
        monitor_owner_map[mid] = owner_sk
        if owner_sk in by_skill:
            by_skill[owner_sk]['monitors'].append(mid)
    # 附：跨技能实现（check 名在别的技能脚本里也出现）——只记录，不改主归属
    for m in monitors:
        if not isinstance(m, dict):
            continue
        chk = m.get('check')
        if not chk:
            continue
        for name in list(by_skill):
            if name == 'tvbox-inspect':
                continue
            sdir = os.path.join(SKILLS_ROOT, name, 'scripts')
            if not os.path.isdir(sdir):
                continue
            for fn in os.listdir(sdir):
                if not fn.endswith('.py'):
                    continue
                try:
                    txt = open(os.path.join(sdir, fn), encoding='utf-8').read()
                except Exception:
                    continue
                if chk in txt:
                    by_skill[name].setdefault('monitors_via', []).append(m.get('id'))
                    break

    # 命令 → 技能：**用 tvbox 入口的 ROUTE 表，不靠猜名字**
    # ★ 踩到的坑：第一版我按「入口名与技能名做包含匹配」归类，结果
    #   `tvbox-source-hub` 命令数 = 0，而 `eco-ledger` 莫名 15 条。
    #   原因：入口名是 `block/sync/audit/log/ledger` 这些**裸词**，
    #   `tvbox-source-hub` 这个名字里一个都找不到；而 `ledger` 恰好是 eco-ledger 的后半段。
    #   **靠名字猜归属，猜出来的表看着很规整，但每条都可能错。**
    #   真源在 /usr/local/bin/tvbox 的 ROUTE（verb → 脚本路径），从路径取技能名，一一对应。
    route = load_tvbox_route()
    for c in cmds:
        toks = c['cmd'].split()
        if len(toks) < 2:
            continue
        verb = toks[1]
        sk = route.get(verb)
        if sk and sk in by_skill:
            by_skill[sk]['commands'].append(c['cmd'])
        else:
            c.setdefault('_unrouted', True)

    # ---- 反向索引：孤儿检测 ----
    orphan_rules = [r['id'] for r in rules
                    if r.get('primary_owner') not in by_skill]
    rules_no_probe = [r['id'] for r in rules if not r.get('probe')]
    rules_no_landing = [r['id'] for r in rules if not _landing_items(r)]

    # ---- 孤儿技能：登记在册，但生态看不见它 ----
    # 判据（三零）：不守任何规则 + 不被任何监测项盯 + 没有 tvbox 入口命令
    # 注意：**"不守规则"和"没人守它"是两件事**，这里记的是后者——
    #   它自己不承载规则、没有监测项盯它、也没有入口。等于生态只能靠"人记得"来用它。
    orphan_skills = []
    for k, v in by_skill.items():
        if not v['rules_owned'] and not v['monitors'] and not v['commands']:
            orphan_skills.append({
                'skill': k,
                'touched_rules': len(v['rules_touched']),
                'has_skill_md': os.path.exists(os.path.join(SKILLS_ROOT, k, 'SKILL.md')),
                'has_flow': os.path.exists(os.path.join(SKILLS_ROOT, k, 'scripts', 'flow.json')),
                'note': '三零：不守规则 / 无监测项 / 无 tvbox 入口',
            })

    # ---- 新技能未立规：有命令入口，但 0 规则 ----
    # 这类不是"孤儿"（能被调用），但**没有任何规则约束它的行为**，
    # 出问题时既没有判据也没有账可查。
    skills_no_rules = [
        {'skill': k, 'commands': len(v['commands']), 'monitors': len(v['monitors'])}
        for k, v in by_skill.items()
        if not v['rules_owned'] and not v['rules_touched'] and (v['commands'] or v['monitors'])
    ]

    reg = {
        'version': 'S177/v1',
        'source_files': {
            'eco_skills': 'ecosys-conductor/scripts/eco_skills.json',
            'rules': 'ecosys-conductor/scripts/rules_registry.json',
            'signals': 'ecosys-conductor/scripts/conductor.py::SIGNAL_SOURCES',
            'monitors': 'tvbox-inspect/scripts/inspect_engine.py::MONITORS',
            'commands': 'tvbox-source-hub/manifest.json',
        },
        'counts': {
            'skills': len(skills), 'rules': len(rules), 'signals': len(sigs),
            'monitors': len(monitors), 'commands': len(cmds),
        },
        'by_skill': by_skill,
        'signals': sigs,
        'gaps': {
            'orphan_rules_owner_not_registered': orphan_rules,
            'rules_without_probe': rules_no_probe,
            'rules_without_landing': rules_no_landing,
            'orphan_skills': orphan_skills,
            'skills_without_any_rule': skills_no_rules,
        },
    }
    return reg


if __name__ == '__main__':
    r = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(r, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    c = r['counts']
    print(f'五方对照表已生成：{OUT}')
    print(f"  技能 {c['skills']} / 规则 {c['rules']} / 信号源 {c['signals']}"
          f" / 监测项 {c['monitors']} / 命令 {c['commands']}")
    g = r['gaps']
    print(f"  孤儿规则（owner 不在登记表）: {len(g['orphan_rules_owner_not_registered'])}"
          f"  {g['orphan_rules_owner_not_registered'][:8]}")
    print(f"  无 probe 的规则: {len(g['rules_without_probe'])}  {g['rules_without_probe'][:8]}")
    print(f"  无 landing 的规则: {len(g['rules_without_landing'])}  {g['rules_without_landing'][:8]}")
    print(f"  孤儿技能（三零）: {len(g['orphan_skills'])}")
    for o in g['orphan_skills']:
        print(f"     · {o['skill']}  SKILL.md={o['has_skill_md']} flow={o['has_flow']}  {o['note']}")
    print(f"  有命令但零规则（新技能未立规）: {len(g['skills_without_any_rule'])}")
    for o in g['skills_without_any_rule']:
        print(f"     · {o['skill']:24} 令={o['commands']:2} 监={o['monitors']}")
    print()
    print('  各技能被守护情况（**全 13 个，不截断**）:')
    for k, v in r['by_skill'].items():
        print(f"    {k:28} 规则(own)={len(v['rules_owned']):3} "
              f"参与={len(v['rules_touched']):3} 监={len(v['monitors']):2} 令={len(v['commands']):3}")
