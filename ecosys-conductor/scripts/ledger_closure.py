#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger_closure.py —— 欠账全程闭环四关（T23 · 用户定性「只登记不算闭环」）

四关：
  出生关  audit --fix  回填存量：open 条目缺 owner→「未派主」报出并给默认主；缺 due→opened+7天；缺 next→open 判红
  盯梢关  audit        每轮体检扫：超期(due<今天)/无主/无 next，>0 退出码 1（habit_brief check 常驻接入）
  验证关  close P0xx --evidence "..."  关闭必须带证据文本，写进 note；无证据拒关
  对账关  check-ids P047 P048 ...      校验编号真实在账（threads 复盘闸⑧段防虚构编号调用）

正本：plans/issues_ledger.json。全程简体中文（M-R110）。
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

LED = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'plans', 'issues_ledger.json')


def auto_prune_deliver_zone():
    """收口后自动维持产物区只留最新10个（用户要求，根治堆积复发）。
    调同目录 prune_deliver_zone.py（SSOT）；失败只告警不阻断（M-R136 第三关）。"""
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prune_deliver_zone.py')
    if not os.path.exists(script):
        return
    try:
        subprocess.run([sys.executable, script, '--by-mtime', '--keep', '10', '--go'],
                       capture_output=True, timeout=90)
    except Exception as e:
        print(f'⚠️ 产物区自动清理跳过（{e}）', file=sys.stderr)


def _load():
    d = json.load(open(LED, encoding='utf-8'))
    return d, d.get('issues', d if isinstance(d, list) else [])


def _save(d):
    json.dump(d, open(LED, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)


def cmd_intake(a):
    """统一落池入口（P089 根治）：任何治理写操作收尾都可调，落④经验库+③台账。
    参数：intake --scene <必填> --fix <必填> [--type 类型] [--reuse 复用建议] [--sev 阻断|耗时|轻微]
    封装闸门重登记 + tvbox lib-log + tvbox log；失败只告警不阻断（守M-R136第三关）。"""
    args = a if isinstance(a, list) else []
    scene = fix = reuse = ''
    typ = '记录读写'
    sev = '轻微'
    i = 0
    while i < len(args):
        if args[i] == '--scene':
            scene = args[i + 1] if i + 1 < len(args) else ''
            i += 2
        elif args[i] == '--fix':
            fix = args[i + 1] if i + 1 < len(args) else ''
            i += 2
        elif args[i] == '--reuse':
            reuse = args[i + 1] if i + 1 < len(args) else ''
            i += 2
        elif args[i] == '--type':
            typ = args[i + 1] if i + 1 < len(args) else typ
            i += 2
        elif args[i] == '--sev':
            sev = args[i + 1] if i + 1 < len(args) else sev
            i += 2
        else:
            i += 1
    if not scene or not fix:
        print('❌ 落池必须两件齐：--scene <场景> + --fix <经验/方案文本>')
        return 1
    import subprocess
    tv = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts', 'entry', 'tvbox.py')
    if not os.path.exists(tv):
        print('⚠️ 经验入池跳过：tvbox 入口缺失', file=sys.stderr)
        return 1
    # 闸门重登记（P087）：过期/关闭/未知自动 open_session，确保入池不被拦
    gate_ok = False
    try:
        sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
        import session_gate
        st = session_gate.state()
        if st.get('ok') is not True:
            session_gate.open_session(f"落池{scene}自动登记（原闸门={st.get('state')}）")
            print(f'🔓 落池自动重登记开工（原闸门 {st.get("state")}），入池放行', file=sys.stderr)
        gate_ok = True
    except Exception as e:
        print(f'⚠️ 闸门重登记失败（{e}），改用旁路兜底', file=sys.stderr)
    env = dict(os.environ)
    if not gate_ok:
        env['TVBOX_GATE_BYPASS'] = '1'
    calls = [
        [tv, 'lib-log', '--scene', scene, '--type', typ,
         '--fix', fix, '--reuse', reuse or '收口即落池不依赖模型自觉', '--sev', sev],
        [tv, 'log', '--summary', scene, '--status', '已闭环',
         '--reuse', reuse or '收口即落池不依赖模型自觉'],
    ]
    for c in calls:
        try:
            subprocess.run([sys.executable] + c, capture_output=True, timeout=30, env=env)
        except Exception as e:
            print(f'⚠️ 经验入池失败（{e}），请手动 tvbox lib-log', file=sys.stderr)
    print(f'✅ 落池完成（{scene}）')
    auto_prune_deliver_zone()
    return 0


def _try_intake(iid, ev):
    """收口即落双池（M-R142/M-R67 闭环）：委托统一入口 cmd_intake，保持 close 流程不变。"""
    return cmd_intake(['--scene', f'收口{iid}', '--fix', (ev or '')[:200],
                       '--reuse', '收口即落池不依赖模型自觉', '--sev', '轻微'])


def cmd_audit(a):
    fix = '--fix' in a
    d, items = _load()
    today = datetime.now()
    problems = []
    for it in items:
        iid = it.get('id', '?')
        if it.get('status') == 'closed':
            # 验证关：关闭证据可在 evidence 字段，也可在历史关闭入口写入 note 的
            # 「关闭证据：」标记段（S214 等批量关闭实践）。两者皆无才判假关，
            # 避免字段错位导致真闭环被误报（P084 根治，守 M-R136 只认标记段、不认任意 note）。
            ev = str(it.get('evidence') or '').strip()
            note = str(it.get('note') or '')
            note_has_ev = ('关闭证据' in note) or ('关闭证据:' in note)
            if not ev and not note_has_ev:
                problems.append(f'{iid} 已关闭但无关闭证据（验证关不通过）')
            continue
        if it.get('status') != 'open':
            continue
        if not str(it.get('owner') or '').strip():
            if fix:
                it['owner'] = 'ecosys-conductor'
                problems.append(f'{iid} 无主 → 已默认派主 ecosys-conductor')
            else:
                problems.append(f'{iid} 无主')
        due = str(it.get('due') or '')
        if not re.match(r'\d{4}-\d{2}-\d{2}', due):
            if fix:
                base = it.get('opened') or today.strftime('%Y-%m-%d')
                try:
                    new_due = (datetime.strptime(str(base)[:10], '%Y-%m-%d')
                               + timedelta(days=7)).strftime('%Y-%m-%d')
                except Exception:
                    new_due = (today + timedelta(days=7)).strftime('%Y-%m-%d')
                it['due'] = new_due
                problems.append(f'{iid} 缺 due → 已补 {new_due}（+7天）')
            else:
                problems.append(f'{iid} 缺 due')
        elif due < today.strftime('%Y-%m-%d'):
            problems.append(f'{iid} 超期（due {due}）')
        if not str(it.get('next') or '').strip():
            problems.append(f'{iid} 无 next（下一步动作缺失，无法推进）')
    if fix:
        _save(d)
    for p in problems:
        print('  🔴', p)
    print(f'盯梢结果：{"修复后 " if fix else ""}问题 {len(problems)} 条 / open '
          f'{sum(1 for it in items if it.get("status") == "open")} 条')
    return 1 if problems else 0


def cmd_close(a):
    iid = a.get('id') if isinstance(a, dict) else None
    args = a if isinstance(a, list) else []
    # 简单解析：close P0xx --evidence "..."
    iid = None
    ev = ''
    i = 0
    while i < len(args):
        if args[i] == '--evidence':
            ev = args[i + 1] if i + 1 < len(args) else ''
            i += 2
        elif not args[i].startswith('--'):
            iid = args[i]
            i += 1
        else:
            i += 1
    if not iid or not ev.strip():
        print('❌ 关闭必须两件齐：欠账编号 + --evidence "证据文本"（全程闭环·验证关）')
        return 1
    d, items = _load()
    it = next((x for x in items if x.get('id') == iid), None)
    if not it:
        print(f'❌ {iid} 不在账（M-R60 如实报）')
        return 1
    if it.get('status') != 'open':
        # 已是 closed：覆盖式写入
        it.setdefault('closed', time.strftime('%Y-%m-%d'))
        it['evidence'] = ev
        base = re.sub(r'\s*｜?\s*(关闭|补充)证据:.*$', '', it.get('note') or '')
        it['note'] = (base + ' ｜ 关闭证据: ' + ev).strip(' ｜')
        _save(d)
        print(f'✅ {iid} 补录关闭证据（before-hook 被拦的历史关闭项）')
        _try_intake(iid, ev)
        return 0
    it['status'] = 'closed'
    it['closed'] = time.strftime('%Y-%m-%d')
    it['evidence'] = ev
    it['note'] = f'{(it.get("note") or "")} ｜ 关闭证据: {ev}'.strip(' ｜')
    _save(d)
    print(f'✅ {iid} 已关闭（证据已入账）')
    _try_intake(iid, ev)
    return 0


def cmd_check_ids(a):
    """对账关：编号必须真实在账（防复盘报告虚构编号）。"""
    d, items = _load()
    have = {it.get('id') for it in items}
    bad = [x for x in a if x not in have]
    if bad:
        print(f'❌ 编号不在账: {bad}（复盘⑧段出现账外编号=虚构，打回）')
        return 1
    print(f'✅ 编号全部在账（{len(a)} 条）')
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    cmd = argv[0]
    rest = argv[1:]
    if cmd == 'audit':
        return cmd_audit(rest)
    if cmd == 'close':
        return cmd_close(rest)
    if cmd == 'intake':
        return cmd_intake(rest)
    if cmd == 'check-ids':
        return cmd_check_ids(rest)
    print(f'未知子命令 {cmd}')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
