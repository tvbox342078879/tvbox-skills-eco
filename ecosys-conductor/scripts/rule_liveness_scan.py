"""规则活体扫描 v2（T16）：三级健康报表——让全部规则「工作起来」可查可证。

三级口径（按 enforce_level 本职）：
  强制面（GATE）：必须有 .py 执行落点（钩子/检查器/脚本闸）
  监测面（PROBE）：有探针监测即可，非动作时强制（Layer2 诚实降级，防幻觉成 GATE）
  提醒面（SCAN）：必须有 .py 执行落点 或 已注入习惯摘要 + ask 路由面
  落点面（DOC）：必须有文档/代码任一落点
退出码：有死规则=1。--report 输出全表+统计。

用法：
  python3 rule_liveness_scan.py            # 只报死规则（体检用）
  python3 rule_liveness_scan.py --report   # 全量三档健康报表
"""
import json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))
REG = os.path.join(BASE, 'rules_registry.json')
HB = os.path.join(BASE, '..', 'references', 'user_habit_brief.md')
SCAN_ROOTS = ('/root/.codebuddy/skills', '/root/.codebuddy/hooks')


def _pool():
    self_path = os.path.abspath(__file__)
    py, md = '', ''
    for base in SCAN_ROOTS:
        for root, _, fs in os.walk(base):
            if os.path.basename(root) == 'plans':
                continue
            for f in fs:
                if 'rules_registry' in f or 'node_modules' in root:
                    continue
                p = os.path.join(root, f)
                if os.path.abspath(p) == self_path:
                    continue
                try:
                    txt = open(p, encoding='utf-8', errors='ignore').read()
                except Exception:
                    continue
                if f.endswith('.py') or root.endswith('hooks'):
                    py += txt
                if f.endswith(('.md', '.json')):
                    md += txt
    return py, md


def main():
    report = '--report' in sys.argv
    d = json.load(open(REG))
    rules = d.get('rules', d if isinstance(d, list) else [])
    py_blob, md_blob = _pool()
    hb = open(HB, encoding='utf-8').read() if os.path.exists(HB) else ''
    dead, rows = [], []
    for r in rules:
        rid, lv = r.get('id', '?'), r.get('enforce_level', '')
        in_py, in_md, in_hb = py_blob.count(rid), md_blob.count(rid), rid in hb
        has_ask = bool(str(r.get('ask') or '').strip())
        if lv == 'GATE':
            alive, face = in_py > 0, '强制面'
        elif lv == 'PROBE':
            # ★ Layer2 诚实降级（M-R128 机制闭环·顽疾 0424fba26cdd 根因）：
            # PROBE 的 alive（有探针在跑）≠ 强制（动作时拦截）。
            # 原把它并入门禁「强制面」，制造"PROBE 像 GATE 一样被强制"的幻觉，
            # 正是顽疾根因（报告假闭环、欠账未真实写入 ledger）。如实拆成「监测面」。
            alive, face = in_py > 0, '监测面(非强制)'
        elif lv == 'SCAN':
            alive, face = (in_py > 0 or in_hb), '提醒面'
        else:
            # DOC = 文档锚点型（设计原则），本就不该有命令。
            # v4 N3：DOC **不计入"活"**（原判定 in_md>0 即算活 → 假绿、M52 恒零），
            # 单列「声明级·无执行层」；也不算"死规则"（它本就不要求执行层），
            # 但数量须显式报出，避免"看起来全活"。
            alive, face = False, '声明级'
        rows.append((rid, lv, face, alive, in_py, in_md, in_hb, has_ask))
        if not alive and face != '声明级':
            dead.append((rid, lv, r.get('status', '?')))
    if report:
        from collections import Counter
        print(f'📋 规则健康报表｜共 {len(rules)} 条')
        print(f'   分级：{dict(Counter(r[1] for r in rows))}')
        print(f'   提醒面（ask 字段）覆盖：{sum(1 for r in rows if r[7])}/{len(rules)}')
        print(f'   摘要注入：{sum(1 for r in rows if r[6])}/{len(rules)}（注入面按需，非全部须注入）')
        print()
        for face in ('强制面', '监测面(非强制)', '提醒面', '声明级'):
            sub = [r for r in rows if r[2] == face]
            if face == '声明级':
                print(f'【声明级·DOC】{len(sub)} 条 —— 文档锚点型，**无执行层，不计入"活"**（防假绿）')
                print(f'   ⚠️ 这些只对"读得到文档的模型"生效；若某条实际是行为约束，应升 GATE/SCAN')
                continue
            if face == '监测面(非强制)':
                print(f'【监测面·PROBE】{len(sub)} 条 —— 仅被探针监测，**非动作时强制**（Layer2 诚实降级，防幻觉成 GATE）')
                print(f'   ⚠️ 这些"有人主动跑探针才查"，不拦动作；要强制须升 GATE（如 M-R128 已升）')
                for rid, lv, f2, alive, p, m, h, a in sub:
                    if not alive:
                        print(f'   ❌ {rid}｜{lv}｜py:{p} 监测探针缺失')
                continue
            print(f'【{face}】{len(sub)} 条，全活 {sum(1 for r in sub if r[3])}，死 {sum(1 for r in sub if not r[3])}')
            for rid, lv, f2, alive, p, m, h, a in sub:
                if not alive:
                    print(f'   ❌ {rid}｜{lv}｜py:{p} md:{m} 摘要:{h}')
    if dead:
        print(f'❌ 死规则 {len(dead)} 条（立了无落点）：')
        for rid, lv, st in dead:
            print(f'   {rid}｜{lv}｜{st}')
        return 1
    doc_n = sum(1 for r in rows if r[1] == 'DOC')
    probe_n = sum(1 for r in rows if r[1] == 'PROBE')
    print(f'✅ 无死规则（强制面+监测面+提醒面均有落点）｜DOC 声明级 {doc_n} 条无执行层、'
          f'PROBE 监测面 {probe_n} 条非强制、均不计入"活"（防假绿）'
          + ('｜详情见上' if report else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
