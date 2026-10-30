#!/usr/bin/env python3
"""S196 任务线看板（M-R140）：解决「会话里走了好几条线，用户问完成没不知道问哪条」。

用法：
  python3 threads.py open "<用户原话摘要>"        # 用户开新需求线 → 发线号 T1/T2…
  python3 threads.py fork <父线号> "<子任务摘要>"  # 走着走着分叉出子线（记父线，可溯源）
  python3 threads.py done <线号> "<证据>" [--report <复盘md路径>]
                                   # 收口；--report 走 M-R142 复盘闸（七段校验+自动进库）
  python3 threads.py list                          # 看板：在途/完成一屏，答「完成没」前必看

存储：scripts/.session_threads.json（会话级，轻量；正文摘要含用户原话前 40 字）。
判据（M-R140）：①用户开新需求线必须 open 并当场报线号；②分叉必须 fork 挂父线；
③被问「完成没/到哪了」必须先 list 对线作答，答完顺带报其他在途线；④done 必须带证据。
"""
import json
import os
import re
import sys
import time

STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.session_threads.json')


def _load():
    if os.path.exists(STORE):
        try:
            return json.load(open(STORE))
        except json.JSONDecodeError:
            print('⚠️ 看板损坏，重建（M-R60：显式告警，不静默当空）')
    return {'next': 1, 'threads': {}}


def _save(d):
    json.dump(d, open(STORE, 'w'), ensure_ascii=False, indent=1)


def _now():
    return time.strftime('%m-%d %H:%M')


# ── 跨会话断根（S198·2026-10-22）：done 收口自动登记云端事件库 ──────────
# 现场教训：事件库停在 10-16、交接摘要停在 10-18，10-20~22 的 13 条线全没进云端，
# 新沙箱问「t6」全失忆（threads 只写本地 flow.json）。done 是收口必经点，在此挂登记，
# 让「做过什么」必然进云端 DB_EVENT（M-R61 机制本体一直在，缺的是喂它的人）。
_EVENT_DB = 'kqNrwHd53K9qr1DFBUPglZ'
_BATCH_ADD = '/root/.codebuddy/skills/library/database/batch_add_database_records.py'


def _event_register(tid, ev):
    """收口事件登记进云端事件库；失败只警告不阻断收口（M-R60：如实报，不装成功）。"""
    try:
        import subprocess
        import datetime
        rec = [{
            '事件ID': {'text': 'T%s@%s' % (tid.lstrip('T'), time.strftime('%m%d%H%M'))},
            '日期': {'text': datetime.date.today().isoformat()},
            '主题': {'text': '任务线收口'},
            '需求原话': {'text': '线 %s 收口' % tid},
            '交付状态': {'select': '已交付'},
            '产出物': {'text': ev[:120]},
            '关联规则': {'text': 'M-R140/M-R145'},
        }]
        r = subprocess.run(
            ['python3', _BATCH_ADD, '--database-id', _EVENT_DB,
             '--records', json.dumps(rec, ensure_ascii=False)],
            capture_output=True, text=True, timeout=55)
        out = (r.stdout or '') + (r.stderr or '')
        if '"success": true' in out:
            print('   ☁️ 已登记云端事件库（跨会话可查，断根 S198）')
        else:
            print(f'⚠️ 事件库登记未确认（M-R60 如实报）：{out.strip()[:150]}')
    except Exception as e:
        print(f'⚠️ 事件库登记异常：{e}（收口本身不受影响）')


def cmd_open(a):
    text = ' '.join(a)
    if not text:
        print('用法: threads.py open "<用户原话摘要>"')
        return 1
    d = _load()
    tid = f"T{d['next']}"
    d['next'] += 1
    d['threads'][tid] = {'origin': text[:40], 'parent': None, 'status': '在途',
                         'opened': _now(), 'done_evidence': None}
    _save(d)
    print(f'🧵 已开任务线 {tid}：「{text[:40]}」——后续分叉/收口都挂这条线')
    return 0


def cmd_fork(a):
    if len(a) < 2:
        print('用法: threads.py fork <父线号> "<子任务摘要>"')
        return 1
    pid, text = a[0].upper(), ' '.join(a[1:])
    d = _load()
    if pid not in d['threads']:
        print(f'❌ 父线 {pid} 不在册（现有: {", ".join(d["threads"]) or "无"}）')
        return 1
    tid = f"T{d['next']}"
    d['next'] += 1
    d['threads'][tid] = {'origin': text[:40], 'parent': pid, 'status': '在途',
                         'opened': _now(), 'done_evidence': None}
    _save(d)
    print(f'🧵 {pid} 分叉出子线 {tid}：「{text[:40]}」')
    return 0


def _m144_flag(rtext):
    """M-R144 判据（独立函数供探针实弹调用）：报告宣称登记新规则但无落点 → True（黄牌）。"""
    return ('登记 M-R' in rtext
            and not (('habit_brief' in rtext or '摘要' in rtext)
                     and ('探针' in rtext or '体检' in rtext)))


def _check_7sec(text):
    """M-R142：七段校验（段名+顺序，照 M-R128 探针判法）。返回缺失段列表。"""
    segs = ['一、原因', '二、过程', '三、过程坑', '四、解决方案',
            '五、后续带出的问题', '六、后续问题解决过程', '七、完工经验',
            '八、过程暴露问题登记']
    pos = []
    for s in segs:
        i = text.find(s)
        pos.append(i)
    miss = [s for s, i in zip(segs, pos) if i < 0]
    if not miss and pos != sorted(pos):
        miss = ['顺序错乱（七段须按一→七排列）']
    return miss


def _check_5to8(text):
    """M-R128 八段联动（T22 用户定性：过程暴露不入账=永远手动修结果）。

    ⑤ 段有遗留（内容非「无」或含 open）而 ⑧ 段没有欠账编号（P0xx）→ 打回。
    机制：问题在过程中暴露却等复盘才登记，上下文一压缩就丢——
    必须「当场入账、报告只列编号」。
    """
    i5 = text.find('五、后续带出的问题')
    i6 = text.find('六、后续问题解决过程')
    i8 = text.find('八、过程暴露问题登记')
    if i5 < 0 or i8 < 0:
        return None          # 段缺失由 _check_7sec 报，不重复
    sec5 = text[i5:i6 if i6 > i5 else len(text)]
    sec8 = text[i8:]
    has_open = ('open' in sec5.lower()) or ('无' not in sec5)
    ids = re.findall(r'P0\d{2,3}', sec8)
    if has_open and not ids:
        return ('⑤ 段有遗留但 ⑧「过程暴露问题登记」未挂欠账编号（P0xx）'
                '——过程暴露必须当场入欠账总账，报告只列编号')
    # T23 全程闭环·对账关：⑧ 段编号必须真实在账（防虚构编号）
    if ids:
        import json as _json
        import os as _os
        led = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                            _os.pardir, 'plans', 'issues_ledger.json')
        try:
            _have = {x.get('id') for x in _json.load(open(led, encoding='utf-8')).get('issues', [])}
        except Exception:
            return '欠账总账无法读取，无法核对编号（M-R60 如实报）'
        _ghost = [x for x in ids if x not in _have]
        if _ghost:
            return f'⑧ 段编号不在账: {_ghost}（虚构编号=假闭环，先立账再引用）'
    return None


def _intake_report(path, tid):
    """M-R142：复盘报告自动进学习资料库（learn-hub intake 正本入口，M-R38 禁自造写入器）。"""
    import subprocess
    learn = '/root/.codebuddy/skills/learn-hub/scripts/learn.py'
    try:
        r = subprocess.run([sys.executable, learn, 'intake', 'file', path,
                            '--name', f'复盘_{tid}_{os.path.basename(path)}'],
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout or '') + (r.stderr or '')
        print(out.strip()[:500])
        # 回执判据：intake 的「落地：materials/<落点名>（新增 N…）」行即进库凭据
        for ln in out.splitlines():
            if '落地：' in ln and '新增' in ln:
                seg = ln.split('落地：', 1)[1].split('（', 1)[0].strip()
                if seg:
                    return seg
    except Exception as e:
        print(f'⚠️ intake 进库异常：{e}（M-R60 如实报，不装成功）')
    return None


def cmd_done(a):
    # M-R142：done <线号> "<证据>" [--report <复盘md路径>]——收口必过复盘闸
    rep = None
    if '--report' in a:
        i = a.index('--report')
        if i + 1 >= len(a):
            print('❌ --report 后面要给复盘 md 路径')
            return 1
        rep = a[i + 1]
        a = a[:i] + a[i + 2:]
    if len(a) < 2:
        print('用法: threads.py done <线号> "<证据>" [--report <复盘md路径>]（不接受空口）')
        return 1
    tid, ev = a[0].upper(), ' '.join(a[1:])
    d = _load()
    if tid not in d['threads']:
        print(f'❌ 线 {tid} 不在册')
        return 1
    if rep:
        if not os.path.isfile(rep):
            print(f'❌ M-R142 复盘闸：报告文件不存在 {rep}，不许收口')
            return 1
        rtext0 = open(rep, encoding='utf-8').read()
        miss = _check_7sec(rtext0)
        if miss:
            print(f'❌ M-R142 复盘闸：八段不齐（缺: {miss}）——按 M-R128 模板补齐再收口')
            return 1
        import re as _re
        _m58 = _check_5to8(rtext0)
        if _m58:
            print(f'❌ M-R142 复盘闸：{_m58}')
            return 1
        # M-R144 新规则闭环三关：报告宣称登记新规则，必须带落点（注入+体检/探针），
        # 否则黄牌——立规三关缺一=死规则（pref_deliver_zone 封存事故，2026-10-22）
        rtext = open(rep, encoding='utf-8').read()
        if _m144_flag(rtext):
            print('⚠️ M-R144 黄牌：报告称「登记 M-R」但未见 habit_brief 注入与体检/探针落点——'
                  '新规则三关（入册/注入/体检）不齐，补齐落点或在本报告如实写明欠账后再收口')
        # M-R143 产物区交付铁律：收口时产物区须有本会话交付型写入，否则黄牌
        try:
            import subprocess as _sp
            _dg = '/root/.codebuddy/hooks/deliver_guard.py'
            if os.path.exists(_dg):
                _rr = _sp.run([sys.executable, _dg, 'check'], capture_output=True, text=True, timeout=30)
                if _rr.returncode == 2:
                    print('⚠️ M-R143 黄牌：产物区无本会话交付型写入——成品须进产出区'
                          '（/workspace/产出区_1016）后再收口')
        except Exception as _e:
            print(f'⚠️ M-R143 自检异常（{_e}）——如实报')
        node = _intake_report(rep, tid)
        if not node:
            print('❌ M-R142 复盘闸：报告进库未拿到回执节点号，不许收口（M-R60 不装成功）')
            return 1
        ev = f'{ev} ｜复盘已进库: {os.path.basename(rep)}'
        d['threads'][tid].update(status='完成', done_evidence=ev[:60], closed=_now(),
                                 retro_report=os.path.abspath(rep), retro_node=node)
        _save(d)
        pending = [k for k, v in d['threads'].items() if v['status'] == '在途']
        print(f'✅ {tid} 收口（证据：{ev[:60]}）｜复盘七段校验过、已进库（节点 {node}）')
        print('   收尾提醒（M-R142）：经验走 tvbox close learn biz 正本入口进经验池')
        print(f'   在途线剩: {", ".join(pending) or "无"}')
        _event_register(tid, ev)
        return 0
    # 无 --report：允许收口但必须亮黄牌（历史兼容：纯查询类小收尾不强造报告）
    d['threads'][tid].update(status='完成', done_evidence=ev[:60], closed=_now())
    _save(d)
    pending = [k for k, v in d['threads'].items() if v['status'] == '在途']
    print(f'⚠️ {tid} 已收口但无复盘报告（M-R142 黄牌）：实质性任务须补 --report 七段复盘进库')
    print(f'✅ {tid} 收口（证据：{ev[:60]}）｜在途线剩: {", ".join(pending) or "无"}')
    _event_register(tid, ev)
    return 0


def cmd_list(a):
    d = _load()
    if not d['threads']:
        print('看板为空：本轮还没有登记任务线')
        return 0
    print('🧵 任务线看板（父→子可溯源）')
    for tid, v in d['threads'].items():
        mark = '✅' if v['status'] == '完成' else '🔄'
        parent = f" ←{v['parent']}" if v.get('parent') else ''
        ev = f"｜证据: {v['done_evidence']}" if v.get('done_evidence') else ''
        print(f"  {mark} {tid}{parent} {v['status']}「{v['origin']}」（{v['opened']}）{ev}")
    pend = [k for k, v in d['threads'].items() if v['status'] == '在途']
    print(f'在途 {len(pend)} 条: {", ".join(pend) or "无"}')
    return 0


CMDS = {'open': cmd_open, 'fork': cmd_fork, 'done': cmd_done, 'list': cmd_list}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__)
        sys.exit(1)
    sys.exit(CMDS[sys.argv[1]](sys.argv[2:]))
