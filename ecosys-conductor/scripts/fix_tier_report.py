#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_tier_report.py —— 规则执行层分级报表（S188）。

## 为什么需要这个报表

`tier: enforced` 原先被当成"这条规则会被执行"，实测不是：141 条里 37 条标着
enforced，`probe.command` 却是 `tvbox conductor signal-check` 这类全量自检——
只在有人主动跑检查时才有意义。用户感觉"规则写了没用"，根源就是这个虚标。

本报表按 `enforce_level` 四档统计，并列出 GATE（真正拦得住的）清单，
让"到底几条规则在守"变成可查的数字，而不是感觉。

    GATE   动作时拦截（PreToolUse 钩子，命中 exit 2）
    PROBE  可双向验证（rule_probe.py 有注入/正例用例）
    SCAN   静态体检（signal-check / verify 等）
    DOC    文档锚点（设计原则型）

用法：
    python3 fix_tier_report.py            # 分布 + GATE 清单
    python3 fix_tier_report.py --by-owner # 按技能分布
    python3 fix_tier_report.py --check    # 只看是否有未分级条目（rc=1 表示有）
"""
import json
import sys
from collections import Counter, defaultdict

REG = '/root/.codebuddy/skills/ecosys-conductor/scripts/rules_registry.json'
ORDER = ('GATE', 'PROBE', 'SCAN', 'DOC')


def load():
    d = json.load(open(REG, encoding='utf-8'))
    return d.get('rules', [])


def main(argv):
    rules = load()
    active = [r for r in rules if r.get('status') not in ('retracted', '已废止')]
    ungraded = [r for r in rules if not r.get('enforce_level')]

    if '--check' in argv:
        if ungraded:
            print(f'❌ 有 {len(ungraded)} 条未标 enforce_level：'
                  + ', '.join(r["id"] for r in ungraded[:10]))
            return 1
        print(f'✅ 全部 {len(rules)} 条均已标 enforce_level')
        return 0

    if '--by-owner' in argv:
        agg = defaultdict(Counter)
        for r in active:
            owner = r.get('primary_owner') or '(未归属)'
            agg[owner][r.get('enforce_level') or '未分级'] += 1
        print(f'{"owner":26s} {"GATE":>5s} {"PROBE":>6s} {"SCAN":>5s} {"DOC":>5s}')
        for owner in sorted(agg, key=lambda o: -agg[o].get('GATE', 0)):
            c = agg[owner]
            print(f'{owner:26s} {c.get("GATE",0):5d} {c.get("PROBE",0):6d} '
                  f'{c.get("SCAN",0):5d} {c.get("DOC",0):5d}')
        return 0

    cnt = Counter(r.get('enforce_level') or '未分级' for r in rules)
    print(f'规则执行层分级（共 {len(rules)} 条，生效 {len(active)} 条）')
    print('=' * 56)
    desc = {'GATE': '动作时拦截，命中即 exit 2（真拦得住）',
            'PROBE': '可双向验证（注入必捕获 + 正例必静默）',
            'SCAN': '静态体检（跑一次查一次）',
            'DOC': '文档锚点（设计原则型）'}
    for k in ORDER:
        n = cnt.get(k, 0)
        pct = n * 100.0 / len(rules) if rules else 0
        print(f'  {k:6s} {n:4d} 条  {pct:5.1f}%   {desc[k]}')
    if cnt.get('未分级'):
        print(f'  {"未分级":6s} {cnt["未分级"]:4d} 条  ← 需补')
    gate = sum(cnt.get(k, 0) for k in ('GATE', 'PROBE'))
    print('-' * 56)
    print(f'  可验证合计（GATE+PROBE）：{gate}/{len(rules)} '
          f'= {gate*100.0/len(rules):.1f}%')
    print()
    print('GATE 清单（做事时会真的被拦下来的规则）：')
    for r in rules:
        if r.get('enforce_level') == 'GATE':
            print(f'  {r["id"]:9s} {(r.get("summary") or "")[:52]}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
