#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第②档第二道闸：行为等价基线（没有测试也能验"没改坏"）。

背景：13 个自建技能里只有 2 个有自测脚本。所以"测试先过"在多数技能上无测试可跑。
退一步的硬底线：**改完之后，技能的只读命令输出必须与改前逐字一致**。

做法：
  baseline(skill)  跑一组**只读**命令，把 stdout/stderr/rc 存成基线
  compare(skill)   再跑一遍，与基线逐项比对；任何一项变了就报红

只读命令白名单（绝不跑会写库/改状态的东西）：
  --help / -h / 无参（多数脚本无参会打 usage）
  以及技能自定义的只读子命令（在 READONLY 里登记）

判据三态（M-R60）：
  · 命令跑不起来（超时/不存在）→ 'unknown'，不许当"一致"
  · rc 或输出变了 → 'diff'
  · 全部一致 → 'same'
"""

import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import apply_guard as G

# ★ M-R38 单一事实源：路径只认 apply_guard 这一份。
#   实测踩到：三个脚本各硬编码 `SKILLS='/root/.codebuddy/skills'`，
#   测试时只改了其中一个，另外两个仍指向真实目录，于是"临时技能"根本没被测到，
#   反而去真实目录里找文件。**同一常量被多处维护，改一处漏两处，测试就假通过。**
#   ★ 且不能用 `SKILLS = G.SKILLS` —— 那是**导入时求值**，之后 G.SKILLS 变了这里还是旧值。
#   必须每次调用现读，否则测试把 G.SKILLS 指向临时目录时，本模块仍去真实目录找文件。
def _skills_root():
    return G.SKILLS


def _ws():
    # ★ 同样不许写 `WS = G.WS`：那是导入时求值，测试里把 G.WS 指到临时目录后本模块仍用旧值。
    #   默认工作区只在 apply_guard.default_ws() 一处定义（M-R38）。
    return G.default_ws()

# 只读命令白名单：{技能: [[参数列表], ...]}
READONLY = {
    'skill-evolution': [['--help'], ['lend', '__probe__'], ['mine', '--skill', '__probe__']],
    'unfinished-audit': [['--help']],
    'legacy-watchdog': [['--help'], ['trend']],
    'probe-hub': [['--help'], ['monitor', 'status']],
    'tvbox-janitor': [['--help']],
    'tvbox-inspect': [['--help']],
    'tvbox-source-hub': [['--help']],
    'tvbox-interface': [['--help']],
    'tvbox-source-blacklist': [['--help']],
    'eco-ledger': [['--help']],
    'ecosys-conductor': [['--help']],
}

ENV_KEY = 'PYTHONIOENCODING'

# ★ 归一化：把"天然会变但语义无关"的东西抹平，否则每次复测都报假 diff。
# 实测踩到：unfinished-audit 的 selftest 每次用随机临时目录，
# 输出里带 `/tmp/ua_probe_ecdosgbz/...` vs `/tmp/ua_probe_y_khw2ow/...`，
# 行为完全一致却被判 diff。假警报比没警报更坏——它教会人忽略报警。
NORMALIZE = [
    (r'/tmp/[A-Za-z0-9_]+', '/tmp/<TMP>'),                  # 临时目录
    (r'\b[A-Za-z_]*sbx_[A-Za-z0-9_]+', '<SBX>'),             # 沙箱名（有的不带 /tmp 前缀）
    (r'\b\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?\b', '<TS>'),  # 时间戳
    (r'\b\d{2}:\d{2}:\d{2}\b', '<HMS>'),
    (r'\b\d+(\.\d+)?\s*(ms|s)\b', '<DUR>'),                  # 耗时
    (r'\bsid=[0-9a-f]{8,}\b', 'sid=<HEX>'),
    (r'\b[0-9a-f]{12,40}\b', '<HEX>'),                       # 哈希/node_id
    (r'\b\d{1,3}(,\d{3})+\b', '<NUM>'),                      # 带千分位的大数（库行数）
]


def _norm(s):
    for pat, rep in NORMALIZE:
        s = re.sub(pat, rep, s)
    return s


# ★★ 显式入口白名单。**没有登记的技能一律不跑。**
#
# 血的教训（S182 实测踩到）：第一版按"最大的带 `__main__` 的脚本"猜入口，
# 于是 unfinished-audit 猜中了 `collect.py` —— 它一跑就**真去采集、真写账本**。
# 后果：建基线这个动作本身制造了生产污染
#   · unfinished_ledger.json 被追加 15 行
#   · tvbox-inspect/scripts/flow.json 被追加一条派单 M48
# 而且它的输出每次不同（"存续 19/20"），于是"基线 vs 复测"永远 diff，成为恒报红的假警报。
#
# 结论：**验行为等价的工具，自己不许有副作用。**
# 所以入口不靠猜，只认这里登记过的。没登记 = 不测（报 no_entry），
# 绝不拿"猜一个像入口的文件"去试——试错成本落在生产库上。
ENTRY = {
    'skill-evolution': 'evolve.py',
    # 无安全只读的 CLI 入口（collect 会写账本），改用自带沙箱的自测脚本：
    # selftest.py 进入即把 SKILLS_ROOT 指向临时目录、退出时清理，且末尾断言"生产 0 残留"。
    # 用它的输出当"行为基线"，等价于"这套断言还全过"。
    'unfinished-audit': 'selftest.py',
    'legacy-watchdog': 'watchdog.py',
    'probe-hub': 'probe.py',
    'tvbox-janitor': 'janitor.py',
    'tvbox-inspect': 'inspect_engine.py',
    'tvbox-source-hub': 'build.py',
    'tvbox-interface': 'pipeline.py',
    'tvbox-source-blacklist': 'blk.py',
    'eco-ledger': 'ledger.py',
    'ecosys-conductor': 'conductor.py',
}


def _main_script(skill):
    """只认 ENTRY 登记过的入口。**不猜。**"""
    name = ENTRY.get(skill)
    if not name:
        return None
    p = os.path.join(_skills_root(), skill, 'scripts', name)
    return p if os.path.exists(p) else None

def _run(skill, args, timeout=60):
    main = _main_script(skill)
    if not main:
        # ★ 与"跑起来但失败"分开记：前者是**没东西可测**，后者是**测了没过**。
        #   混成一个 unknown，会让"这个技能根本没有可跑入口"变成一句含糊的
        #   "无法判定"，查账的人搞不清该去修入口还是修逻辑。
        return {'rc': None, 'out': '', 'err': '无带 __main__ 的脚本',
                'state': 'no_entry'}
    env = dict(os.environ)
    env[ENV_KEY] = 'utf-8'
    try:
        r = subprocess.run([sys.executable, main] + args, capture_output=True,
                           text=True, timeout=timeout, env=env, cwd=os.path.dirname(main))
    except subprocess.TimeoutExpired:
        return {'rc': None, 'out': '', 'err': '超时', 'state': 'timeout'}
    except Exception as e:
        return {'rc': None, 'out': '', 'err': f'{type(e).__name__}: {e}', 'state': 'error'}
    # ★ 语法错误必须单独拎出来。实测踩到：脚本被改坏成 `def f(:` 后，
    #   入口还在、文件还在，但一跑就 SyntaxError。若把它归进"跑起来了但输出变了"，
    #   人只会看到"输出不一致"，得自己去翻 stderr 才知道是代码坏了。
    #   代码坏了和输出变了，处理动作完全不同：一个是回滚，一个是审文案。
    if 'SyntaxError' in r.stderr or 'IndentationError' in r.stderr:
        return {'rc': r.returncode, 'out': _norm(r.stdout[-4000:]), 'err': r.stderr[-2000:],
                'state': 'syntax_error'}
    return {'rc': r.returncode, 'out': _norm(r.stdout[-4000:]), 'err': _norm(r.stderr[-2000:]),
            'state': 'ok'}


def baseline(skill):
    cmds = READONLY.get(skill) or [['--help']]
    res = {}
    for args in cmds:
        res[' '.join(args)] = _run(skill, args)
    os.makedirs(_ws(), exist_ok=True)
    fp = os.path.join(_ws(), skill, 'baseline.json')
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    json.dump({'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'skill': skill, 'cmds': res},
              open(fp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    ok = sum(1 for v in res.values() if v['state'] == 'ok')
    return True, f'基线已存 {ok}/{len(res)} 条命令（{fp}）'


def compare(skill):
    """返回 (verdict, detail)。
    verdict ∈ 'same' | 'diff' | 'no_entry' | 'unknown'
      · 'same'     全部一致
      · 'diff'     rc/输出变了（**唯一需要回滚信号**）
      · 'no_entry' 技能没有可跑入口 → 本机制对它无效，必须换手段验
      · 'unknown'  超时/异常，判不了 → 不许当一致
    """
    fp = os.path.join(_ws(), skill, 'baseline.json')
    if not os.path.exists(fp):
        return 'unknown', f'无基线：{fp}（先跑 baseline）'
    base = json.load(open(fp, encoding='utf-8'))['cmds']
    if not base:
        return 'no_entry', '基线里没有任何命令'
    diffs, unk, noent, syn = [], [], [], []
    for k, old in base.items():
        cur = _run(skill, k.split(' '))
        st = cur['state']
        # 语法坏了优先级最高：这时候比"输出变没变"毫无意义，代码根本跑不起来
        if st == 'syntax_error' or old.get('state') == 'syntax_error':
            syn.append(k)
            continue
        if st == 'no_entry' or old.get('state') == 'no_entry':
            noent.append(k)
            continue
        if st in ('timeout', 'error') or old.get('state') in ('timeout', 'error'):
            unk.append(f"{k}({cur.get('err') or old.get('err')})")
            continue
        if old['rc'] != cur['rc']:
            diffs.append(f"{k}: rc {old['rc']} → {cur['rc']}")
        elif old['out'] != cur['out']:
            # 指出第一处不同，别只报"不一致"
            a, b = old['out'].splitlines(), cur['out'].splitlines()
            for i in range(max(len(a), len(b))):
                la = a[i] if i < len(a) else '<无>'
                lb = b[i] if i < len(b) else '<无>'
                if la != lb:
                    diffs.append(f'{k}: 第{i+1}行\n      改前 {la[:100]}\n      改后 {lb[:100]}')
                    break
    if syn:
        return 'syntax_error', f'脚本语法已坏（{syn}）——必须回滚，不是"输出变了"'
    if diffs:
        return 'diff', '；'.join(diffs)
    if unk:
        return 'unknown', f'以下命令无法判定（超时/异常）：{unk}'
    if noent:
        return 'no_entry', f'以下命令无入口可跑（技能没有带 __main__ 的脚本）：{noent}'
    return 'same', f'{len(base)} 条命令输出一致'


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('用法: equiv_check.py baseline|compare <技能名>')
        sys.exit(2)
    op, sk = sys.argv[1], sys.argv[2]
    if op == 'baseline':
        ok, m = baseline(sk); print(m); sys.exit(0 if ok else 1)
    if op == 'compare':
        v, d = compare(sk)
        print(f'[{v}] {d}')
        sys.exit(0 if v == 'same' else 1)
    print('未知操作'); sys.exit(2)
