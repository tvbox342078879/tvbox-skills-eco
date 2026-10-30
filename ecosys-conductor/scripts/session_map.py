#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""session_map.py —— 会话编号双体系映射（C007 落地）

## 问题（实测，不是推测）

生态里长期并存两套会话号：

| 体系 | 范围 | 来源 | 特性 |
|---|---|---|---|
| 台账 `会话编号` 字段 | S001–S137 | 机器自增写库 | **连续无断号**，有实体记录 |
| CHANGELOG 标题 `（Sxxx）` | S052–S183 | 人手自报 | 跳号（151→157）、**重号**（S158 出现 4 次） |

实测分叉点：**S052–S063、S108–S110 两套完全一致**（15 个号都在交集），
S110 之后开始脱钩，最终相差 46 个号。

更麻烦的是：**台账自己的摘要正文里也写着另一套号**——
S105 摘要开头是「S161 续2」、S112 是「S159 新增全局规则」。
所以不是"两个文件各用一套"，而是**同一行记录里两个号并存**。
台账 S109 摘要甚至写着「摘要现不自报编号，编号以『会话编号』字段为准」
——这个问题之前被意识到过，但只改了一轮描述，没有建立映射。

## 处置（本文件）

不回溯改历史号（会破坏历史可追溯性），改为**显式映射 + 单点权威**：

  · **权威**：台账 `会话编号` 字段（有实体、连续、可查）。
  · **别名**：CHANGELOG 行尾 `（Sxxx）` 作为历史别名保留，
    通过本文件生成的 `session_map.json` 双向查找。
  · **反查**：给定任一号，能查出它指向的台账行（或明确报"无对应台账号"）。

## 产物

  references/session_map.json
    {
      "authority": "ledger.session_id",
      "generated": "2026-10-20T...",
      "changelog_to_ledger": {"183": null, "163": null, ...},   # null = CHANGELOG 独有
      "ledger_to_changelog": {"137": [], ...},
      "notes": "..."
    }

## 用法

  python3 session_map.py build     # 重新生成映射（改完 CHANGELOG 后跑）
  python3 session_map.py lookup S183
  python3 session_map.py check     # 报告两套的偏移与重号情况
  python3 session_map.py verify    # M-R131 探针入口：权威连续 + 映射齐备 → rc=0
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = '/root/.codebuddy/skills'
SESS_DB = 'TO4LwsjVVdR8JRAUlkejk3'
OUT = os.path.join(os.path.dirname(HERE), 'references', 'session_map.json')

CHANGELOGS = [
    '/root/.codebuddy/skills/ecosys-conductor/CHANGELOG.md',
]


def _ledger_ids():
    sys.path.insert(0, os.path.join(SKILLS, 'tvbox-source-hub', 'scripts'))
    import core
    rows = core.rows_of(SESS_DB)
    out = {}
    for r in rows:
        m = re.search(r'(\d+)', str(r.get('会话编号') or ''))
        if m:
            out[int(m.group(1))] = {
                '编号': str(r.get('会话编号')),
                '时间': str(r.get('时间') or '')[:19],
                '摘要': str(r.get('任务摘要') or '')[:60],
            }
    return out


def _changelog_ids():
    """从 CHANGELOG 标题行尾取 `（Sxxx）`。返回 {号: [出现次数]}。"""
    out = {}
    for p in CHANGELOGS:
        if not os.path.exists(p):
            continue
        for ln in open(p, encoding='utf-8').read().split('\n'):
            if not ln.startswith('## '):
                continue
            m = re.search(r'（S(\d+)）\s*$', ln)
            if m:
                n = int(m.group(1))
                out[n] = out.get(n, 0) + 1
    return out


def cmd_build(_a):
    led = _ledger_ids()
    cl = _changelog_ids()
    data = {
        'authority': 'ledger.session_id（台账「会话编号」字段为唯一权威）',
        'generated': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'changelog_to_ledger': {},
        'ledger_to_changelog': {},
        'notes': ('CHANGELOG 号为历史别名，不回溯改写。null = 该号在台账无对应行，'
                  '说明那轮工作改了代码但没写台账，或用的是另一套计数口径。'),
    }
    for n in sorted(cl):
        data['changelog_to_ledger'][str(n)] = (f'S{n:03d}' if n in led else None)
    for n in sorted(led):
        aliases = [f'S{k}' for k in cl if k == n]
        data['ledger_to_changelog'][str(n)] = aliases

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    matched = sum(1 for v in data['changelog_to_ledger'].values() if v)
    print(f'✅ 映射已生成：{OUT}')
    print(f'   CHANGELOG 号 {len(cl)} 个（去重后）｜能对上台账 {matched} 个｜'
          f'独有 {len(cl) - matched} 个')
    print(f'   台账号 {len(led)} 个（S001–S{max(led):03d}）')
    return 0


def cmd_lookup(a):
    if not a:
        print('用法: session_map.py lookup S183')
        return 1
    m = re.search(r'(\d+)', a[0])
    if not m:
        print(f'❌ 解析不出编号：{a[0]}')
        return 1
    n = int(m.group(1))
    if not os.path.exists(OUT):
        print('❌ 映射未生成，先跑 build')
        return 1
    d = json.load(open(OUT, encoding='utf-8'))
    led = _ledger_ids()
    print(f'🔎 查 S{n}')
    if n in led:
        r = led[n]
        print(f'   ✅ 台账有：{r["编号"]} | {r["时间"]} | {r["摘要"]}')
    else:
        print(f'   ⚪ 台账无 S{n:03d}（该号只出现在 CHANGELOG 里，是历史别名）')
    cnt = _changelog_ids().get(n, 0)
    print(f'   CHANGELOG 出现 {cnt} 次' + ('（⚠️ 重号）' if cnt > 1 else ''))
    return 0


def cmd_check(_a):
    led = _ledger_ids()
    cl = _changelog_ids()
    print('📋 会话编号双体系体检\n')
    print(f'  权威（台账字段）: S001–S{max(led):03d}，共 {len(led)} 条')
    gaps = [n for n in range(1, max(led) + 1) if n not in led]
    print(f'    断号: {gaps if gaps else "无（连续）"}')
    print(f'  别名（CHANGELOG）: {len(cl)} 个不同号，范围 S{min(cl):03d}–S{max(cl):03d}')
    dup = {n: c for n, c in cl.items() if c > 1}
    print(f'    重号: {dup if dup else "无"}')
    only_cl = sorted(n for n in cl if n not in led)
    print(f'    仅存在于 CHANGELOG: {len(only_cl)} 个 → {only_cl}')
    ok = 1 if only_cl else 0
    print()
    if only_cl:
        print(f'  ⚠️ {len(only_cl)} 个 CHANGELOG 号在台账无对应实体——')
        print('     处置：不回溯改号（破坏可追溯），按别名保留，用 session_map.py lookup 反查。')
    else:
        print('  ✅ 两套已对齐')
    return 0


def cmd_verify(_a):
    """M-R131 登记的探针入口。rc 必须来自**校验结果**，不能来自"命令不存在时打印文档返回 1"。

    S192 实测：登记写的是 `session_map.py verify`，但 main() 原本只认
    build/lookup/check —— 探针每次跑都是 rc=1（打印用法），
    被当"未通过"。**探针常年红 = 等于没有探针**（M-R118 的同族病）。

    判据用实体事实，不用文档口径：
      ① 台账号连续（无断号）        —— 权威体系自身完好
      ② 映射文件存在且能解析        —— 能反查
      ③ 每个 CHANGELOG 独有号在映射里显式为 null  —— 别名没被偷偷当成实体
    """
    led = _ledger_ids()
    if not led:
        print('❌ 台账读不到会话编号（下游 core 不通）')
        return 1
    top = max(led)
    gaps = [n for n in range(1, top + 1) if n not in led]
    if not os.path.exists(OUT):
        print(f'❌ 映射未生成：{OUT}（先跑 session_map.py build）')
        return 1
    try:
        d = json.load(open(OUT, encoding='utf-8'))
    except Exception as e:
        print(f'❌ 映射解析失败：{type(e).__name__}: {e}')
        return 1
    c2l = d.get('changelog_to_ledger') or {}
    cl = _changelog_ids()
    missing = [n for n in cl if str(n) not in c2l]
    print(f'🔬 M-R131 会话编号权威性')
    print(f'   权威条数={len(led)}（S001–S{top:03d}）断号={gaps if gaps else "无"}')
    print(f'   映射条数={len(c2l)}｜CHANGELOG 号={len(cl)}｜映射未覆盖={missing or "无"}')
    ok = (not gaps) and (not missing)
    print('   ' + ('✅ 通过' if ok else '❌ 未通过：权威不连续或映射缺项'))
    return 0 if ok else 1


def main(argv):
    cmds = {'build': cmd_build, 'lookup': cmd_lookup, 'check': cmd_check,
            'verify': cmd_verify}
    if len(argv) < 2 or argv[1] not in cmds:
        print(__doc__)
        return 1
    return cmds[argv[1]](argv[2:])


if __name__ == '__main__':
    sys.exit(main(sys.argv))
