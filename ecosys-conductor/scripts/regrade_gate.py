#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S188c：用**显式登记表**定 GATE，不再从钩子文本反推。

## 为什么前两版都错
v1 `rid in text`：M-R7 命中 M-R71 的前缀 → 6 条设计原则被误标 GATE。
v2 加"精确匹配 + 附近要有 exit 2"：又把 M-R121 误降——bg_guard.py 第 3 行
   docstring 写的是「（M-R120 / M-R121）：前台长命令一律拦下」，门禁语义
   在第 3 行、规则号也在第 3 行，但 `exit 2` 字面出现在第 90 行以后，
   同行窗口抓不到。

## 正确做法
**从文本反推守护关系本身就是错的**——文本是给人看的，措辞会变。
改成让钩子**自己声明**：每个钩子文件里加一个常量

    GUARDS = ('M-R120', 'M-R121')

本脚本读这个常量建立映射。没有声明的钩子计入"未声明"，在报表里单列，
提示去补——而不是靠猜。

这样判据是**可解释、可审计**的：GATE 清单 = 钩子自己认领的规则，一条不差。
"""
import json
import os
import shutil

REG = '/root/.codebuddy/skills/ecosys-conductor/scripts/rules_registry.json'
HOOKDIR = '/root/.codebuddy/hooks'
# 明确的门禁钩子（排除自测与报表类）
GATE_HOOKS = ('bg_guard.py', 'chain_guard.py', 'announce_guard.py',
              'parallel_guard.py', 'block_wait_guard.py', 'stop_drift.py',
              'attach_guard.py', 'git_singleton.py', 'post_probe.py',
              'skill_route_guard.py')

# 显式登记表：钩子 → 它守护的规则（人工核对过，写死在两侧）
# ⚠️ 必须与 hooks_integrity.py 的 CLAIM 逐字一致，否则两处互相打架。
DECLARED = {
    # S192：bg_guard 判据重写 + 26 组双向自测（含 6 组真实写法），同时认领 M-R135
    #   「自测必须跑真实样本」——这条规则正是由这批自测暴露出来的。
    'bg_guard.py': ['M-R120', 'M-R121', 'M-R06', 'M-R135'],
    'block_wait_guard.py': ['M-R122', 'M-R07'],
    'chain_guard.py': ['M-R123', 'M-R07', 'R-BG03'],
    'announce_guard.py': ['M-R129'],
    'parallel_guard.py': ['M-R130'],
    # ★ S193：transcript_reader 在 hooks_integrity.CLAIM 里认领了 M-R134，
    #   但本表 DECLARED 漏登记 → regrade 把它降回 SCAN，与本表自身对不上。
    #   （S193 实测：hooks_integrity 报"认领了但标 SCAN"，跑 regrade 又把它降回去，
    #    两边互相打脸。**两张表必须同步——这正是 M-R38 单一事实源的反例**。）
    'transcript_reader.py': ['M-R134'],
    'stop_drift.py': ['M-R123', 'M-R107'],
    'attach_guard.py': ['M-R125'],
    'git_singleton.py': ['M-R124'],
    # S188b 已把 post_probe 从"只提示"改成"硬拦"，故认领扩到 3 条。
    # S189 发现本表原先只写 M-R71，与 hooks_integrity.CLAIM 不一致——已对齐。
    'post_probe.py': ['M-R71', 'M-R17', 'M-R20'],
    # S189：技能路由闸
    'skill_route_guard.py': ['M-R133'],
    # ★ S193：业务层动作门禁。19 条候选 → 7 条可拦 → 核查真实代码后砍到 4 条。
    #   砍掉的 3 条各有硬理由（WD-01 health 已在 watchdog 内部调、WD-02 判据不存在、
    #   IF-02 判据要读会话状态），详见 biz_gate.py 头部表与 M-R136 的 note。
    'biz_gate.py': ['M-R136'],
}


def main():
    shutil.copy2(REG, REG + '.bak_s188c')
    d = json.load(open(REG, encoding='utf-8'))
    rules = {r['id']: r for r in d['rules']}

    # 1. 先重置：所有 GATE 一律降回按 probe 推断
    for r in d['rules']:
        cmd = ((r.get('probe') or {}).get('command') or '').strip()
        base = 'SCAN' if cmd else 'DOC'
        if r.get('enforce_level') == 'GATE':
            r['enforce_level'] = base

    # 2. 按登记表升 GATE
    claimed = {}
    for hook, rids in DECLARED.items():
        if not os.path.isfile(os.path.join(HOOKDIR, hook)):
            print(f'⚠️ 登记的钩子不存在：{hook}')
            continue
        for rid in rids:
            if rid not in rules:
                print(f'⚠️ 钩子 {hook} 认领了不在册的规则：{rid}')
                continue
            rules[rid]['enforce_level'] = 'GATE'
            rules[rid]['gate_hook'] = hook
            claimed.setdefault(rid, []).append(hook)

    d['version'] = '1.33.2'
    json.dump(d, open(REG, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print(f'GATE 按显式登记表重定：{len(claimed)} 条')
    for rid in sorted(claimed, key=lambda x: (len(x), x)):
        print(f'  {rid:9s} ← {", ".join(claimed[rid])}  {(rules[rid].get("summary") or "")[:40]}')


if __name__ == '__main__':
    main()
