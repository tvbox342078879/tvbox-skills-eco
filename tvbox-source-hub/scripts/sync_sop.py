#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_sop.py — 做JSON标准流程SOP 成长同步器
权威源：scripts/sop_makejson.json（机器可读，唯一真相）
投影文件：/workspace/标准1015_做JSON标准流程SOP.md（人读，由本脚本render生成，禁手改）

用法：
  python3 sync_sop.py route --symptom "报错关键词"   # 症状路由：命中直达解法
  python3 sync_sop.py check                          # 覆盖检查+待升级建议
  python3 sync_sop.py add-case --file case.json      # 登记新案例（自动去重/分级/渲染）
  python3 sync_sop.py add-case --symptom X --scene Y --solution Z --source L0xx
  python3 sync_sop.py render                         # 重新渲染md投影
  python3 sync_sop.py promote SYxx                   # 把pending症状提为正式symptom

案例JSON格式（--file）：
{"symptom":"..","scene":"..","root_cause":"..","solution":"..","source":"S/L编号",
 "step":"S2","case_title":"..","case_problem":"..","case_process":"..","case_final":".."}
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
SOP_JSON = HERE / 'sop_makejson.json'
MD_TARGET = Path('/workspace/标准1015_做JSON标准流程SOP.md')

STEP_NAMES = {}


def load():
    return json.loads(SOP_JSON.read_text(encoding='utf-8'))


def save(d):
    SOP_JSON.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')


def norm(s):
    return re.sub(r'\s+', '', str(s or '')).lower()


def _lcs(a, b):
    """最长公共子串 → (长度, 是否含非ASCII字符)。含中文的子串更特异，用于平局决胜。"""
    if not a or not b:
        return (0, False)
    prev = [0] * (len(b) + 1)
    prev_c = [False] * (len(b) + 1)
    best = (0, False)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        cur_c = [False] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                cur_c[j] = prev_c[j - 1] or ord(a[i - 1]) > 127
                L, C = cur[j], cur_c[j]
                if L > best[0] or (L == best[0] and C and not best[1]):
                    best = (L, C)
        prev, prev_c = cur, cur_c
    return best


def similarity(a, b):
    """相似度（0~1）：包含关系 0.9；强子串 0.75+特异性加分；否则 2-3 gram 覆盖率。
    强子串门槛分型（S021 校准实证）：含中文的子串≥5字符（『校验后为空』），纯ASCII≥7
    （『success』『rename-node』）——『tvbox』这类 5 字符领域词到处共现，一律不强命中；
    加分 min(0.12, LCS*0.01) 让平局按特异性决胜（SY11『success字段』8字符 > SY02『success』7）。"""
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 0.9
    lcs_len, lcs_cjk = _lcs(a, b)
    if lcs_len >= (5 if lcs_cjk else 7):
        return min(0.87, 0.75 + lcs_len * 0.01)
    grams_b = {b[i:i + 2] for i in range(len(b) - 1)} | {b[i:i + 3] for i in range(len(b) - 2)}
    if not grams_b:
        return 0.0
    hit = sum(1 for g in grams_b if g in a)
    return hit / len(grams_b)


def find_symptom(d, text, threshold=0.45, margin=0.15):
    """返回 (best, score, rivals)。rivals=与 best 分差<margin 的其他症状（疑似多解提示）。
    symptom / root_cause / alias（近义口语表述）分开比对取 max，避免长文本稀释。"""
    scored = []
    for sy in d['router']['symptoms']:
        candidates = [sy['symptom'], sy.get('root_cause', '')] + sy.get('alias', [])
        s = max(similarity(text, c) for c in candidates if c)
        scored.append((s, sy))
    scored.sort(key=lambda x: -x[0])
    if not scored or scored[0][0] < threshold:
        return (None, scored[0][0] if scored else 0.0, [])
    top_score, top = scored[0]
    rivals = [sy for s, sy in scored[1:] if s >= top_score - margin]
    return (top, top_score, rivals)


# ---------- 路由 ----------
def cmd_route(args):
    d = load()
    sy, score, rivals = find_symptom(d, args.symptom)
    if sy:
        step = next((s for s in d['steps'] if s['id'] == sy.get('step')), None)
        print(f"✅ 命中症状 {sy['id']}（相似度 {score:.2f}）")
        print(f"   症状：{sy['symptom']}")
        print(f"   根因：{sy['root_cause']}")
        if rivals:
            print(f"   ⚠️ 同时疑似 {len(rivals)} 个症状（分差<0.15，解法可能部分适用）：")
            for r in rivals[:3]:
                print(f"      - {r['id']} {r['symptom'][:40]}")
        print(f"   ★已验证解法（出处 {sy['source']}）：")
        print(f"   {sy['verified_solution']}")
        if step:
            print(f"   归属步骤：{step['id']} {step['name']}")
            if step.get('fallbacks'):
                print(f"   ⚠️ 解法不通时按序走备选：")
                for i, f in enumerate(step['fallbacks'], 1):
                    print(f"      {i}. {f}")
        return 0
    # 未命中 → 引导走标准主流程
    print(f"❌ 未命中已知症状（最高相似度 {score:.2f} < 0.45）")
    print("→ 按标准主流程排查，每步先看 pitfalls，不通走 fallbacks：")
    for s in d['steps']:
        print(f"   {s['id']} {s['name']}")
    print("→ 解决后请执行：python3 sync_sop.py add-case --symptom \"...\" --solution \"...\" --source S0xx")
    return 1


# ---------- 覆盖检查 ----------
def cmd_check(args):
    d = load()
    syms = d['router']['symptoms']
    print(f"当前 SOP：{len(d['steps'])} 步 / {len(syms)} 症状 / {len(d['cases'])} 案例")
    pend = d['growth'].get('pending_queue', [])
    if pend:
        print(f"\n⏳ 待分级队列（{len(pend)} 条）：")
        for p in pend:
            print(f"   [{p.get('queued_at','')}] {p['symptom'][:60]}（建议方向：{p.get('suggest','')}）")
    # 来源引用完整性
    ids = {s['id'] for s in syms}
    broken = [sy['id'] for sy in syms if sy.get('step') and sy['step'] not in {s['id'] for s in d['steps']}]
    if broken:
        print(f"\n⚠️ 症状指向了不存在的步骤：{broken}")
    # 升级建议：同一 source 出现多次 or pending 超3
    from collections import Counter
    src_c = Counter()
    for sy in syms:
        for m in re.findall(r'[LS]\d{3}', sy.get('source', '')):
            src_c[m] += 1
    hot = {k: v for k, v in src_c.items() if v >= 3}
    if hot:
        print(f"\n🔥 高频出处（解法被多症状复用，可考虑提炼为脚本/规则）：{hot}")
    if len(pend) >= 3:
        print("\n💡 pending_queue ≥3：建议跑一次 evolve recurring 聚簇，或 promote 高频项")
    if not pend and not broken:
        print("\n✅ 无待办；成长队列干净")
    return 0


# ---------- 登记新案例 ----------
def cmd_add_case(args):
    d = load()
    if args.file:
        case = json.loads(Path(args.file).read_text(encoding='utf-8'))
    else:
        if not (args.symptom and args.solution):
            print('错误：--file 或 (--symptom + --solution) 必填')
            return 2
        case = {
            'symptom': args.symptom, 'scene': args.scene or '',
            'root_cause': args.root_cause or '待复盘补充',
            'solution': args.solution, 'source': args.source or '待登记',
            'step': args.step or '', 'case_title': args.case_title or '',
            'case_problem': args.case_problem or args.symptom,
            'case_process': args.case_process or '见solution',
            'case_final': args.case_final or args.solution,
        }
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    # 1) 去重：症状是否已被覆盖
    sy, score, _rivals = find_symptom(d, case['symptom'] + ' ' + case.get('root_cause', ''))
    if sy:
        # 已覆盖 → 增强对应步骤 fallbacks（若解法提供了新路径）
        sol = norm(case['solution'])
        existing = norm(sy['verified_solution'])
        if sol and sol not in existing:
            sy['verified_solution'] += f" ｜ 补充（{case.get('source','?')}）：{case['solution']}"
            sy['source'] = sy.get('source', '') + f"+{case.get('source','?')}"
            print(f"🔁 症状 {sy['id']} 已覆盖 → 解法已增强（+{case.get('source','?')}）")
        else:
            print(f"♻️ 症状 {sy['id']} 已覆盖且无新信息 → 不新增")
        # 案例仍进档案（历史留痕）
        _append_case(d, case, now)
    else:
        # 全新 → 进 pending_queue 待分级
        d['growth']['pending_queue'].append({
            'symptom': case['symptom'], 'scene': case.get('scene', ''),
            'root_cause': case.get('root_cause', ''), 'solution': case['solution'],
            'source': case.get('source', '?'), 'step': case.get('step', ''),
            'queued_at': now,
            'suggest': '出现≥2次且解法稳定→promote为正式symptom并写入步骤fallbacks',
        })
        _append_case(d, case, now)
        print(f"🆕 全新症状 → 进 pending_queue 待分级（solve 后 promote）")
    d['growth']['stats']['cases'] = len(d['cases'])
    d['growth']['stats']['last_sync'] = now
    save(d)
    cmd_render(args)
    print(f"📄 md 投影已刷新：{MD_TARGET}")
    return 0


def _append_case(d, case, now):
    cid = f"C{len(d['cases']) + 1:02d}"
    d['cases'].append({
        'id': cid, 'time': now[:16].replace('T', ' '),
        'title': case.get('case_title') or case['symptom'][:30],
        'problem': case.get('case_problem', ''),
        'process': case.get('case_process', ''),
        'final': case.get('case_final', ''),
        'trace': case.get('source', ''),
    })


def cmd_promote(args):
    d = load()
    q = d['growth']['pending_queue']
    hit = [p for p in q if args.symptom_id and norm(args.symptom_id) in norm(p['symptom'])]
    if not hit and args.all:
        hit = list(q)
    if not hit:
        print('未找到匹配的 pending 症状（按症状关键词匹配；可用 --all 全部提升）')
        return 1
    for p in hit:
        nid = f"SY{len(d['router']['symptoms']) + 1:02d}"
        d['router']['symptoms'].append({
            'id': nid, 'symptom': p['symptom'], 'root_cause': p.get('root_cause', ''),
            'verified_solution': p['solution'], 'source': p.get('source', '?'),
            'step': p.get('step', ''),
        })
        q.remove(p)
        # 同步写入对应步骤 fallbacks（S020 补：文档承诺过但漏实现，promote 只加索引不写备选路径=半拉子）
        step_id = p.get('step', '')
        st = next((s for s in d['steps'] if s['id'] == step_id), None) if step_id else None
        fb_note = ''
        if st:
            sol = p['solution']
            if all(norm(sol) not in norm(f) for f in st.get('fallbacks', [])):
                st.setdefault('fallbacks', []).append(f"[{nid}] {sol}")
                fb_note = f" ｜ 已写入 {step_id} 备选路径"
        print(f"⬆️ {p['symptom'][:40]}… → 正式症状 {nid}{fb_note}")
    d['growth']['stats']['symptoms'] = len(d['router']['symptoms'])
    save(d)
    cmd_render(args)
    return 0


# ---------- 渲染 md 投影 ----------
def cmd_render(_args):
    d = load()
    L = []
    A = L.append
    A(f"# {d['meta']['name']}")
    A('')
    A(f"> 版本 v{d['meta']['version']} ｜ 更新于 {d['growth']['stats']['last_sync']} ｜ 权威源 `scripts/sop_makejson.json`（机器可读，禁手改）")
    A('>')
    A(f"> {d['meta']['purpose']}")
    A('')
    A('---')
    A('')
    A('## 0. 本文档宪法（三级路由 + 成长协议）')
    A('')
    A('**遇到问题时的三级路由（顺序不可颠倒）**：')
    A('')
    A('```')
    A('① 症状直达：第1节索引命中 → 直接执行「已验证解法」（出处可溯源）')
    A('       ↓ 不通')
    A('② 标准主流程：走第2节对应步骤，先看该步「坑与解法」，再按「备选路径」按序尝试')
    A('       ↓ 仍不通')
    A('③ 未探索路径：记录现象→按第3节历史案例的排查思路类比→解决后必须登记（成长协议）')
    A('```')
    A('')
    A('**成长协议（让本文档自动同步最新做json过程）**：')
    A('')
    A('| 时机 | 动作 | 命令 |')
    A('|---|---|---|')
    A('| 每次做JSON任务复盘闭环时 | 登记新案例（自动去重/分级） | `python3 scripts/sync_sop.py add-case --file case.json` |')
    A('| 症状复发且解法稳定后 | 提升为正式症状 | `python3 sync_sop.py promote SYxx` |')
    A('| 想吃透某症状的历史链路 | 查第3节溯源档案 | 本文档第3节 |')
    A('| 想知道还有什么欠账 | 覆盖检查+升级建议 | `python3 sync_sop.py check` |')
    A('')
    A('自动同步规则：①已覆盖症状→解法增强而非新增 ②全新症状→pending_queue待分级 ③写入后自动重渲染本md（本文件是JSON权威源的投影，**禁手改**）')
    A('')
    A('---')
    A('')
    A('## 1. 问题 → 解法速查索引（症状直达表）')
    A('')
    A(f"共 {len(d['router']['symptoms'])} 条。用法：命中行 → 直接执行「已验证解法」；出处列可跳转第3节溯源。")
    A('')
    A('| ID | 症状 | 根因 | ★已验证解法 | 出处 | 归属步骤 |')
    A('|---|---|---|---|---|---|')
    for sy in d['router']['symptoms']:
        A(f"| {sy['id']} | {sy['symptom']} | {sy['root_cause']} | {sy['verified_solution']} | {sy['source']} | {sy.get('step','')} |")
    A('')
    A('---')
    A('')
    A('## 2. 做JSON标准主流程（十步·每步四要素）')
    A('')
    A('> 流程不通时：步内「备选路径」按序尝试；全都通记录新案例进成长协议。')
    A('')
    for s in d['steps']:
        A(f"### {s['id']} {s['name']}")
        A('')
        A(f"*历史出处：{s['source']}*")
        A('')
        A('**标准操作**：')
        for op in s['operations']:
            A(f"1. {op}")
        A('')
        A(f"**必须产出**：{'；'.join(s['products'])}")
        A('')
        if s.get('pitfalls'):
            A('**坑与解法（先读再动手）**：')
            for p in s['pitfalls']:
                note = f"——{p['note']}" if p.get('note') else ''
                sym = p.get('sym', '')
                A(f"- [{sym}] {note}" if sym else f"- {p.get('note','')}")
            A('')
        if s.get('fallbacks'):
            A('**备选路径（按序尝试）**：')
            for i, f in enumerate(s['fallbacks'], 1):
                A(f"{i}. {f}")
            A('')
    A('---')
    A('')
    A('## 3. 历史溯源档案（问题 → 解决过程 → 最终方法）')
    A('')
    A(f"共 {len(d['cases'])} 个案例，与③台账（S001-S0xx）、④经验库（L001-L0xx）编号互通。")
    A('')
    A('| # | 时间 | 案例 | 遇到的问题 | 解决过程 | 最终方法 | 溯源编号 |')
    A('|---|---|---|---|---|---|---|')
    for c in d['cases']:
        A(f"| {c['id']} | {c['time']} | {c['title']} | {c['problem']} | {c['process']} | {c['final']} | {c['trace']} |")
    A('')
    A('---')
    A('')
    A('## 4. 成长登记区与欠账')
    A('')
    st = d['growth']['stats']
    A(f"当前规模：**{st['steps']} 步 / {st['symptoms']} 症状 / {st['cases']} 案例**（上次同步 {st['last_sync']}）")
    A('')
    pend = d['growth'].get('pending_queue', [])
    if pend:
        A(f"**待分级队列（{len(pend)} 条）**：")
        A('')
        A('| 登记时间 | 症状 | 解法 | 出处 | 建议 |')
        A('|---|---|---|---|---|')
        for p in pend:
            A(f"| {p.get('queued_at','')} | {p['symptom']} | {p['solution']} | {p.get('source','')} | {p.get('suggest','')} |")
        A('')
    else:
        A('**待分级队列**：空 ✅')
        A('')
    A('**升级触发条件**：')
    A('')
    for k, v in d['meta']['growth_protocol']['promote_rules'].items():
        A(f"- {k}：{v}")
    A('')
    A('**下次复盘时检查**：pending_queue 有无可 promote；在线托管地址验活；本表与③④库数字对账。')
    A('')
    MD_TARGET.parent.mkdir(parents=True, exist_ok=True)
    MD_TARGET.write_text('\n'.join(L), encoding='utf-8')


def main():
    ap = argparse.ArgumentParser(description='做JSON标准流程SOP 成长同步器')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('route'); p.add_argument('--symptom', required=True)
    sub.add_parser('check')
    p = sub.add_parser('add-case')
    p.add_argument('--file'); p.add_argument('--symptom'); p.add_argument('--scene')
    p.add_argument('--root-cause', dest='root_cause'); p.add_argument('--solution')
    p.add_argument('--source'); p.add_argument('--step')
    p.add_argument('--case-title', dest='case_title'); p.add_argument('--case-problem', dest='case_problem')
    p.add_argument('--case-process', dest='case_process'); p.add_argument('--case-final', dest='case_final')
    p = sub.add_parser('promote'); p.add_argument('symptom_id', nargs='?'); p.add_argument('--all', action='store_true')
    sub.add_parser('render')
    args = ap.parse_args()
    fn = {'route': cmd_route, 'check': cmd_check, 'add-case': cmd_add_case,
          'promote': cmd_promote, 'render': cmd_render}[args.cmd]
    return fn(args) or 0


if __name__ == '__main__':
    sys.exit(main())
