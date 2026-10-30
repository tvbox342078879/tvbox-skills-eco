#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘点修正版：拦截池扩到 conductor scripts（threads.py 复盘闸等 GATE 在那里）；
单查用户明令固化 8 条（standing）对应的注入状态。"""
import json, os, glob, time, re

COND = '/root/.codebuddy/skills/ecosys-conductor/scripts'
BRIEF = '/root/.codebuddy/skills/ecosys-conductor/references/user_habit_brief.md'
brief = open(BRIEF, encoding='utf-8').read()

# 拦截文本池：hooks + conductor scripts + tvbox-interface/hooks 相关技能脚本
pool = ''
for pat in ('/root/.codebuddy/hooks/*.py', os.path.join(COND, '*.py'),
            '/root/.codebuddy/skills/*/scripts/session_gate.py'):
    for f in glob.glob(pat):
        try:
            pool += open(f, encoding='utf-8', errors='ignore').read()
        except Exception:
            pass

stand = json.load(open(os.path.join(COND, 'user_standing_instructions.json')))
lst = stand['instructions'] if isinstance(stand, dict) else stand
print('=== 用户明令固化指令 → 对应规则注入核验 ===')
for it in lst:
    solid = str(it.get('solidified_via', ''))
    ids = [w for w in solid.replace('（', ' ').replace('）', ' ').split() if w.startswith('M-R')]
    for rid in ids:
        print(f"  {it['id']:<28} → {rid:<8} 摘要注入={'✅' if rid in brief else '❌'}")

d = json.load(open(os.path.join(COND, 'rules_registry.json')))
rules = d['rules'] if isinstance(d, dict) else d

def _has_landing(r):
    """GATE 规则是否真有落点（守 M-R136，防误判真空）。
    命中任一即视为已落点：①gate_hook 字段指向 hooks 下真实 .py；
    ②implementation/landing/carrier_note 提及 hooks 下真实 .py。
    排除『落点在外部执行体（scheduler-api.sh 等）』与『已补 gate_hook 字段』两类真有落点却被旧逻辑误杀的情况。"""
    gh = r.get('gate_hook')
    if gh and os.path.exists(os.path.join('/root/.codebuddy/hooks', gh)):
        return True
    blob = ' '.join(str(r.get(k) or '') for k in ('implementation', 'landing', 'carrier_note'))
    for tok in blob.replace('(', ' ').replace(')', ' ').split():
        if tok.endswith('.py') and os.path.exists(os.path.join('/root/.codebuddy/hooks', tok)):
            return True
    return False

gate_no_hook = [(r['id'], (r.get('title') or '')[:36]) for r in rules
                if (r.get('enforce_level') or '').upper() == 'GATE'
                and not _has_landing(r) and r['id'] not in pool]
print(f"\n=== 修正后：GATE 档但全池（hooks+conductor scripts）零命中 === {len(gate_no_hook)}")
for rid, t in gate_no_hook:
    print(f"   {rid}  {t}")

# 第三块：治理闭环八段报告体检（P079·对齐 M-R128/M-R207/M-R133）
# 守 M-R136 三关：单点可观测（规则册 mtime + 产物区文件）/纯文本判真假/SCAN 级不阻塞误报低
ZONE = '/workspace/产出区_1016'
RR = os.path.join(COND, 'rules_registry.json')
print('\n=== 治理闭环体检：规则改动是否走八段报告（P079） ===')
now = time.time()
rr_recent = os.path.exists(RR) and (now - os.path.getmtime(RR)) < 7 * 86400
segs = ['原因', '过程坑', '后续带出的问题', '完工经验总结', '过程暴露问题登记']
cnt = 0
latest = None
if os.path.isdir(ZONE):
    for fn in os.listdir(ZONE):
        if not fn.endswith('.md'):
            continue
        if not re.search(r'_\d{4}_', fn):   # M-R128 命名含 MMDD 日期段（_1029_ 之类）
            continue
        fp = os.path.join(ZONE, fn)
        try:
            txt = open(fp, encoding='utf-8', errors='ignore').read()
        except Exception:
            txt = ''
        if sum(1 for s in segs if s in txt) < 4:   # 八段不全不算治理报告
            continue
        cnt += 1
        try:
            m = os.path.getmtime(fp)
        except Exception:
            continue
        if latest is None or m > latest[1]:
            latest = (fn, m)
if rr_recent and (latest is None or (now - latest[1]) > 7 * 86400):
    print(f'  ⚠️ 规则册近 7 天有改动，但产物区无近 7 天八段报告 → 治理动作疑似未走 M-R128 闭环')
    print(f'     产物区八段报告数={cnt}｜最近一份={latest[0] if latest else "无"}')
else:
    print(f'  ✅ 治理闭环体检通过（规则册近期改动={rr_recent}｜产物区八段报告数={cnt}｜最近={latest[0] if latest else "无"}）')

# 第四块：开工必读遵守体检（V3/V7·M-R215 闭环·守 M-R136 三关）
import sys as _sys, subprocess as _sp
print('\n=== 开工必读遵守体检（V3/V7·M-R215）===')
inj = ''
try:
    r = _sp.run([_sys.executable, '/root/.codebuddy/hooks/habit_inject.py'],
                input='{"prompt":"测试"}', capture_output=True, text=True, timeout=20)
    if r.stdout.strip():
        inj = json.loads(r.stdout).get('hookSpecificOutput', {}).get('additionalContext', '')
except Exception:
    inj = ''
persist_keys = ['pref_zh_chat', 'pref_bg_over_1min', 'pref_narrate_progress',
                'pref_delivery_report_7sec', 'pref_canonical_entry', 'pref_chinese_selfcheck',
                'pref_thread_board', 'pref_deliver_zone']
miss = [k for k in persist_keys if k not in inj]
if not inj:
    print('  ⚠️ 注入为空（钩子异常？）→ 无法核验持久指令落地')
elif miss:
    print(f'  ❌ 注入漏持久指令：{miss}（M-R211 假绿风险）')
else:
    print(f'  ✅ 注入含全部 8 条持久指令关键词（M-R211 防假绿通过，注入长={len(inj)}）')

recent_rep = latest[0] if latest else None
if recent_rep:
    try:
        t = open(os.path.join(ZONE, recent_rep), encoding='utf-8', errors='ignore').read()
        nzh = sum(1 for c in t if '一' <= c <= '鿿')
        ratio = nzh / max(1, len(t))
        has_mr = 'M-R' in t
        flag = '✅' if has_mr else '⚠️'
        print(f'  {flag} 全程遵守抽检：最近报告={recent_rep}｜中文占比={ratio:.0%}(代码块稀释仅参考)｜含M-R引用={has_mr}')
    except Exception as e:
        print(f'  ⚠️ 抽检读取失败：{e}')
else:
    print('  ⚠️ 无产物区报告可抽检')
