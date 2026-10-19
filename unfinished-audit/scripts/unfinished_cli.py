#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清账司 CLI（S136 · M-R81「收尾唯一入口」）

九技能干完活都跑同一条：**tvbox close**

为什么必须统一到一条：以前每个技能「收尾」各写各的——conductor 写 issues_ledger、
watchdog 写 monitors、inspect 写 findings_history、evolve 写经验池。
四条收尾通道各记各的，谁也没法回答「全局还有什么没做完」。
统一入口后，**收尾这件事本身变成可审计的单一动作**。
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import collect                    # noqa: E402
import dispatch as dispatch_mod    # noqa: E402  ← 别名：本模块导出 dispatch 函数给入口，
                                  #    直接叫 dispatch 会把模块遮蔽成函数（S136 实测踩坑）

USAGE = """用法: tvbox close [子命令]

  （无参数）        统一收尾：六源采集 → 分诊派单 → 落账 → 汇报「本次完成 / 仍未完成」
  status [--all]    看欠账单（默认只看未结；--all 含已结）×  按 owner 分组、按期限排序
  done <sid> <证据>  显式收口（证据必填，不接受空口）
  wontfix <sid> <理由>  不做并说明（须给理由）
  defer <sid> <YYYY-MM-DD> <理由>  显式延期
  mine <技能名>     只看某个监察官名下的欠账（派单反查）
  verify            欠账单自检（无主项 / 逾期 / 损坏）
  report            生成收尾汇报（Markdown，可直接贴给用户）
  promise "<承诺>"  登记会话承诺（AI 答应了但还没做的）——未收口则验收不过
  promise list      看未收口承诺
  promise done <Cxxx> <证据>  收口一条承诺
  link              四向联动验证（技能↔技能 / ↔资料库 / ↔云端 / ↔规则）
  learn biz "摘要" …  转接 `tvbox log`      写 ③ 会话台账（业务经验）★M-R67 收口必落池
  learn plat "场景" … 转接 `tvbox lib-log`  写 ④ 经验库（平台经验）

九技能共用：任何技能完成实质工作后，收尾一律跑本命令。
**收尾不等于只清欠账**：M-R67 要求经验必落双池，用 `close learn biz/plat`（= `tvbox log`/`lib-log`）。
"""


def _fmt(it, today=None):
    due = it.get('due', '')
    mark = '🔴' if (today and due and due < today) else '·'
    return (f"  {mark} {it.get('sid'):14s} [{it.get('owner', '?'):18s}] "
            f"{it.get('title', '')[:60]}" + (f"  due {due}" if due else ''))


def cmd_close(rest):
    """统一收尾：采集 → 分诊 → 落账 → 汇报两段。"""
    import datetime
    dry = '--dry' in rest
    print('🧾 清账司·统一收尾（六源全量采集）')
    print()
    items, errs, stats = collect.collect_all()
    print('  ── 六源采集 ──')
    for src, label, n, err in stats:
        if err:
            print(f"  ❌ {src:14s} {label:22s} —— 无法判定：{err}")
        else:
            print(f"  ✅ {src:14s} {label:22s} {n:3d} 条")
    if errs:
        print(f'\n  ⚠️ {len(errs)} 源无法判定——**不得**当作「无欠账」（M-R80）')
    print(f'\n  采集合计 {len(items)} 条')
    if dry:
        today = datetime.date.today().isoformat()
        for it in items:
            dispatch_mod._assign(it)
            print(_fmt(it, today))
        print('\n  [--dry] 未落账')
        return 0 if not errs else 1

    r = dispatch_mod.sync(items)
    today = datetime.date.today().isoformat()
    print(f"\n  ── 落账 ── 新增 {len(r['added'])}｜存续 {len(r['kept'])}"
          f"｜消失未收口 {len(r['vanished'])}")
    if r.get('dispatched'):
        print(f"  📮 已派单送达 {len(r['dispatched'])} 条（写入各 owner 的 flow.backlog，"
              f"它们下次跑自己的命令可见）")
    if r.get('dispatch_fail'):
        print(f"  ⚠️ 派单未送达 {len(r['dispatch_fail'])}：{'、'.join(r['dispatch_fail'][:5])}")
    if r['vanished']:
        print('  ⚠️ 上期在册、本期采集不到（**不等于做完了**，M-R82）：')
        for v in r['vanished']:
            print(f"     {v.get('sid')} {v.get('title', '')[:56]}")
        print('     → 要么 `close done <sid> <证据>` 显式收口，要么说明为何消失')

    its, by_owner = dispatch_mod.summary_text()
    print('\n  ── 本次完成（上期在册、本期已收口）──')
    db = dispatch_mod._load_ledger()
    recent = [c for c in db.get('closed', []) if c.get('closed_at') == today]
    if recent:
        for c in recent:
            print(f"  ✅ {c.get('title', '')[:60]}")
            print(f"      证据：{c.get('close_evidence', '')[:90]}")
    else:
        print('  （本次会话还没有显式收口项——真实收口后跑 close 会自动列在这里）')

    print('\n  ── 仍未完成 ──')
    if not its:
        print('  🎉 账本无未结项')
    for ow in sorted(by_owner):
        lst = sorted(by_owner[ow], key=lambda x: x.get('due', ''))
        print(f'  ▸ {ow}（{len(lst)} 项）')
        for it in lst:
            print(_fmt(it, today))

    probs, warns = dispatch_mod.verify_ledger()
    if warns:
        # ★ S158 六补：warnings 里现在有两类（超期未处置 / 疑似重复），
        #   原先统一挂在「⚠️ 超期未处置」标题下 —— 重复项会被当成超期项读，
        #   改标题并按类分组，否则告警挂在错误的分类里等于没告警。
        _dup = [w for w in warns if w.startswith('疑似重复')]
        _late = [w for w in warns if not w.startswith('疑似重复')]
        if _late:
            print('\n  ⚠️ 超期未处置：')
            for w in _late:
                print('     ' + w)
        if _dup:
            print('\n  ⚠️ 疑似重复登记（同一件事派给了两个技能，两边都等对方）：')
            for w in _dup:
                print('     ' + w)
    if probs:
        print('\n  ❌ 硬问题（会导致 verify 判据⑧ 不过）：')
        for p in probs:
            print('     ' + p)
        return 1
    print('\n  -- 四向联动 --')
    try:
        import link_four
        d1 = link_four.dir1_skill_to_skill()
        d2 = link_four.dir2_skill_to_library()
        d3 = link_four.dir3_skill_to_cloud()
        d4 = link_four.dir4_skill_to_rules()
        n_ok = sum([d1['ok'], d2['ok'], d3['ok'], d4['ok']])
        print(f"  技能↔技能 {'✅' if d1['ok'] else '❌'}｜技能↔资料库 {'✅' if d2['ok'] else '❌'}"
              f"｜技能↔云端 {'✅' if d3['ok'] else '❌'}｜技能↔规则 {'✅' if d4['ok'] else '❌'}"
              f"　四向 {n_ok}/4")
        for dd in d2.get('dead', []):
            print(f'     ⚠️ {dd} 读失败（云端节点可能已不存在）')
    except Exception as e:
        print(f'  ⚠️ 四向验证无法完成（{type(e).__name__}: {e}）')
    print('\n  ✅ 账本健康：无无主项、无未说明消失项')
    print('  💡 下一步：按上面的 owner 派单处置 → 处置完 `tvbox close done <sid> <证据>`')
    return 0


def cmd_status(rest):
    import datetime
    show_all = '--all' in rest
    db = dispatch_mod._load_ledger()
    its = db.get('items', [])
    closed = db.get('closed', [])
    today = datetime.date.today().isoformat()
    print(f"📋 未完成事项总账：{len(its)} 条未结｜{len(closed)} 条已结"
          f"（更新 {db.get('updated', '?')}）")
    if not its:
        print('   🎉 空账')
    by_owner = {}
    for it in its:
        if it.get('status') in ('done', 'wontfix') and not show_all:
            continue
        by_owner.setdefault(it.get('owner', '?'), []).append(it)
    for ow in sorted(by_owner):
        lst = sorted(by_owner[ow], key=lambda x: x.get('due', ''))
        print(f'\n  ▸ {ow}（{len(lst)} 项）—— 反查：tvbox close mine {ow}')
        for it in lst:
            print(_fmt(it, today))
            print(f"      下一步：{it.get('next', '?')}")
    if show_all and closed:
        print(f'\n  ── 已结 {len(closed)} 条 ──')
        for c in closed[-20:]:
            print(f"  ✅ {c.get('sid', '')} {c.get('title', '')[:50]}"
                  f"（{c.get('closed_at', '')}）")
    return 0


def cmd_done(rest, status='done'):
    if len(rest) < 2:
        print(f'用法: tvbox close {status} <sid> <证据/理由>')
        print('     证据必须说清「做到了什么」——不接受"做完了"这种空口')
        return 1
    sid = rest[0]
    ev = ' '.join(rest[1:])
    try:
        hit = dispatch_mod.close_item(sid, ev, status=status)
    except RuntimeError as e:
        print(f'❌ {e}')
        return 1
    print(f"✅ {hit.get('sid')} → {status}")
    print(f"   {hit.get('title', '')[:80]}")
    print(f"   证据：{ev[:120]}")
    print('   （已移入已结区，下次 close 会列在「本次完成」）')
    return 0


def cmd_defer(rest):
    import datetime
    if len(rest) < 3:
        print('用法: tvbox close defer <sid> <YYYY-MM-DD> <理由>')
        return 1
    sid, newdue, why = rest[0], rest[1], ' '.join(rest[2:])
    try:
        datetime.date.fromisoformat(newdue)
    except Exception:
        print(f'❌ 期限格式不对：{newdue}（要 YYYY-MM-DD）')
        return 1
    db = dispatch_mod._load_ledger()
    hit = None
    for it in db.get('items', []):
        if it.get('sid') == sid or it.get('signature') == sid:
            hit = it
            break
    if hit is None:
        print(f'❌ 账本里找不到 {sid}'); return 1
    hit['status'] = 'defer'
    hit['due'] = newdue
    hit['note'] = why
    dispatch_mod._save_ledger(db)
    print(f'🟡 {sid} 已延期至 {newdue}：{why}')
    return 0


def cmd_mine(rest):
    """mine <技能名> [--before <要改的文件|功能关键词>]：派单反查 / **改动前查账闸门**。

    ★ P1「改代码前必查账」的落地判据（用户原话：P1/P2/P3 是"想出来的"，不是从账上查出来的）。
    为什么不能只写在 CHANGELOG / 规则里：那条约束是给**动代码的那一刻**用的，
    而那一刻手边有的只是"我想改这个文件"，没有"这个文件历史上欠着什么"。
    所以把它做成命令：**改之前先跑一次，账上有同名欠账就当场摊开**。

    判据（不是枚举，别写死文件名清单）：命中 = 欠账的 evidence/title/next 里
    **出现我要改的那个路径（或其主干名）**。路径来自命令行，不来自内置白名单——
    白名单是"想出来的"，命令行参数才是"当下这件事"。
    """
    if not rest:
        print('用法: tvbox close mine <技能名> [--before <文件路径|功能关键词>]')
        print('  --before：改代码前查账。账上若有同名欠账，当场列出，别改完才发现白改。')
        return 1
    who = rest[0]
    before = ''
    if '--before' in rest:
        i = rest.index('--before')
        before = ' '.join(rest[i + 1:]).strip()
        rest = rest[:i]
    db = dispatch_mod._load_ledger()
    its = [it for it in db.get('items', []) if it.get('owner') == who]
    print(f'📌 {who} 名下欠账：{len(its)} 条')
    for it in its:
        print(f"  · {it.get('sid')} {it.get('title', '')[:70]}")
        print(f"     来源 {it.get('source')}／{it.get('skill')}  期限 {it.get('due')}")
        print(f"     下一步 {it.get('next')}")
        print(f"     证据位置 {it.get('evidence')}")
    if not its:
        print('  （无——该监察官当前不欠活）')

    if not before:
        return 0
    # ── 改动前查账：把"我要改的东西"跟账上存量做碰撞 ──
    # **实测踩到的第一版缺陷（别回退成只看路径字面）**：
    # 第一版判据是「evidence 里出现我要改的路径」，跑真阳性就挂了——
    # 账上 `c62973b49707`（probe-hub 成品源 502）的 evidence 写的是
    # `issues_ledger.json #P020`，**通篇没有一个字提到 probe.py**。
    # 道理很简单：账记的是"哪条源坏了"，不是"该改哪个文件"。
    # 所以判据必须是分层的，弱的也要摊开、但不能冒充强的：
    #   强（同一文件）  → 路径/主干名出现
    #   中（同一技能）  → 该技能名出现 或 欠账 owner 就是它
    #   弱（话题相关）  → 标题关键词与改动词有交集
    # 三档分开打印，不合并成一个"疑似"。宁可多列让人自己判断，
    # 也不要为了"看起来准"把弱证据删掉——**漏一条等于白改一遍**。
    print()
    print(f'🔎 改动前查账：准备改「{before}」')
    # **实测踩到的第二版缺陷：拿去掉扩展名的主干做子串匹配 → 大面积误报。**
    # `probe.py` 剥成 `probe`，于是 evidence 里的 `probe-hub`、`tvbox probe monitor status`
    # 全被当成"强命中"，两条欠账明明与这个文件无关却挂在🔴里。
    # 这比漏报更坏：误报多了，人就不看这一屏了，闸门等于白装。
    # 收紧成两条**互不重叠**的判据：
    #   强 = 完整文件路径出现，**或** `xxx.py` 这种带扩展名的文件名出现（不吃裸词）
    #   中 = **要改的对象自己落在 who 这个技能里**，且该账与它同技能
    #
    # **实测踩到的第三版缺陷（这条最值得记）**：中档原先写成
    # 「`it.owner == who` → 中档」，可是 `mine <who>` 的查询域**本来就是 who 名下的账**，
    # 于是条件恒真 → 中档必然全量命中，闸门退化成"把名下欠账再列一遍"。
    # 实测症状：我传 janitor 的路径，却把 probe-hub 的两条账列为🟡同技能。
    # 教训：判据里**不能有两个同侧变量**——`who` 与 `it.owner` 同源，互比等于自己跟自己比。
    # 必须让"命令行给的要改对象"参与判断，它才是与账本**独立**的那一侧。
    stems = {p for p in re.split(r'[\s,，;；]+', before) if p}
    fnames = {os.path.basename(s) for s in stems}
    # "带扩展名的文件名"才算强证据；没有扩展名的裸词一律不参与强判
    name_hits = {n for n in fnames if '.' in n}
    # 本次要改的对象是否落在 `who` 这个技能里——中档成立的**前提**
    #
    # ★ 第四版缺陷（真用起来才发现的，比前三版更隐蔽）：
    #   上一版写 `before_in_who = any(who in s for s in stems)`，即"传进来的字符串里含技能名"。
    #   实测：我改成品源库时传的是 `pure_版.json` —— **文件名里当然不会带 `probe-hub`**，
    #   于是中档永不成立，`c62973b49707`（正是这条库的 502 欠账）被漏掉，闸门报"✅ 无涉及"。
    #   为什么上一轮五点单测没抓到：我的用例全都传了完整路径（`/…/probe-hub/scripts/probe.py`），
    #   路径里恰好含技能名 → 判据"看起来对"。**测试用例比真实用法更宽松，就等于没测。**
    # 修法（判据用"能不能落到磁盘上"而不是"字符串像不像"）：
    #   ① 传的是真路径 → 出现在 with_who 前缀下即成立；
    #   ② 传的是裸文件名 → 去 `who` 的目录里找有没有这个文件，**存在才算**（仍不靠猜）；
    #   ③ 纯关键词 → 退回原来的"含技能名"。
    # 三态：三种都不成立才算"不在本技能"（M-R60：判不出不能折叠成"没欠账"）。
    SK = '/root/.codebuddy/skills'
    who_dir = os.path.join(SK, who)
    before_in_who = False
    for s in stems:
        if s.startswith('/') or '/' in s:
            if f'{SK}/{who}/' in s or s.startswith(who_dir):
                before_in_who = True
                break
        elif '.' in s:            # 裸文件名：真去找
            for sub in ('scripts', 'scripts/lib', ''):
                if os.path.exists(os.path.join(who_dir, sub, s)):
                    before_in_who = True
                    break
            if before_in_who:
                break
        elif who in s:            # 纯关键词
            before_in_who = True
            break
    strong, medium = [], []
    for it in its:
        blob = ' '.join(str(it.get(k) or '') for k in ('title', 'evidence', 'next'))
        path_hit = any(s in blob for s in stems if '/' in s)
        name_hit = any(n in blob for n in name_hits)
        if path_hit or name_hit:
            strong.append(it)
        elif before_in_who and (it.get('skill') == who or it.get('owner') == who):
            medium.append(it)
    if strong:
        print(f'  🔴 强命中 {len(strong)} 条（账上直接提到这个文件/主干名）——**先看，别改完才发现白改**：')
        for it in strong:
            print(f"     · {it.get('sid')} [{it.get('source')}] {it.get('title', '')[:66]}")
            print(f"       证据 {str(it.get('evidence'))[:100]}")
    if medium:
        print(f'  🟡 同技能 {len(medium)} 条（账记的是"哪条坏了"，未必写到文件，改之前请自己确认）：')
        for it in medium:
            print(f"     · {it.get('sid')} [{it.get('source')}] {it.get('title', '')[:66]}")
            print(f"       证据 {str(it.get('evidence'))[:100]}")
    if strong or medium:
        print('  → 已收口 → `close done <sid> <证据>`；仍要做 → 接着它改，别另起一套')
    else:
        print('  ✅ 账上无涉及它的未结欠账（改吧；改完别忘了 `close done` / `close`）')
    return 0


def cmd_verify(rest):
    probs, warns = dispatch_mod.verify_ledger()
    db = dispatch_mod._load_ledger()
    n = len(db.get('items', []))
    print(f'🧪 欠账单自检：{n} 条未结')
    for p in probs:
        print(f'  ❌ {p}')
    _dup = [w for w in warns if w.startswith('疑似重复')]
    _late = [w for w in warns if not w.startswith('疑似重复')]
    for w in _late:
        print(f'  ⚠️ {w}')
    for w in _dup:
        print(f'  ⚠️ {w}')
    if not probs and not warns:
        print('  ✅ 全绿：无无主项、无逾期、无重复、账本可读')
    elif not probs:
        _parts = []
        if _late:
            _parts.append(f'{len(_late)} 项超期')
        if _dup:
            _parts.append(f'{len(_dup)} 项疑似重复')
        print(f'  ✅ 无硬问题（{"、".join(_parts)}仅告警）')
    return 0 if not probs else 1


def cmd_report(rest):
    """生成可直接贴的收尾汇报（Markdown）。"""
    import datetime
    today = datetime.date.today().isoformat()
    db = dispatch_mod._load_ledger()
    its = db.get('items', [])
    closed = [c for c in db.get('closed', []) if c.get('closed_at') == today]
    lines = [f'## 收尾汇报 · {today}', '']
    lines.append(f'### 本次完成（{len(closed)} 项）')
    if closed:
        for c in closed:
            lines.append(f"- ✅ {c.get('title', '')}  ")
            lines.append(f"  证据：{c.get('close_evidence', '')}")
    else:
        lines.append('- （本次未显式收口任何欠账）')
    lines.append('')
    lines.append(f'### 仍未完成（{len(its)} 项）')
    by_owner = {}
    for it in its:
        by_owner.setdefault(it.get('owner', '?'), []).append(it)
    if not its:
        lines.append('- 🎉 无')
    for ow in sorted(by_owner):
        lines.append(f'**{ow}（{len(by_owner[ow])} 项）**')
        for it in sorted(by_owner[ow], key=lambda x: x.get('due', '')):
            lines.append(f"- {it.get('title', '')}")
            lines.append(f"  下一步 `{it.get('next', '')}`｜期限 {it.get('due', '')}")
        lines.append('')
    txt = '\n'.join(lines)
    out = os.path.join('/workspace', f'收尾汇报_{today.replace("-", "")}.md')
    try:
        open(out, 'w', encoding='utf-8').write(txt)
        print(txt)
        print(f'\n（已写入 {out}）')
    except Exception as e:
        print(txt)
        print(f'\n（写文件失败：{e}）')
    return 0


def cmd_promise(rest):
    """登记会话承诺：tvbox close promise "<承诺>" [--due YYYY-MM-DD]"""
    import promises as pm
    if not rest or rest[0] in ('list', 'status'):
        its = pm.as_items()
        print(f'📝 未收口承诺：{len(its)} 条')
        for it in its:
            print(f"  · {it['title'][:76]}  due {it['due']}  owner {it['owner']}")
        if not its:
            print('  （无）')
        return 0
    if rest[0] == 'done':
        if len(rest) < 3:
            print('用法: tvbox close promise done <Cxxx> <证据>'); return 1
        try:
            h = pm.close(rest[1], ' '.join(rest[2:]))
        except RuntimeError as e:
            print(f'❌ {e}'); return 1
        print(f"✅ 承诺 {h['id']} 已收口：{h['text'][:60]}")
        return 0
    text = ' '.join(x for x in rest if not x.startswith('--'))
    due = None
    if '--due' in rest:
        i = rest.index('--due')
        if i + 1 < len(rest):
            due = rest[i + 1]
            text = ' '.join(x for x in rest if x not in ('--due', due))
    try:
        pid = pm.add(text, due)
    except RuntimeError as e:
        print(f'❌ {e}'); return 1
    print(f'📝 已登记承诺 {pid}：{text[:70]}')
    print('   它会并入欠账单，收尾时未收口 → verify 判据⑧ 报红')
    return 0


def main(argv):
    rest = list(argv or [])
    if not rest:
        return cmd_close([])
    # ★ 本次实证的断层（同一类坑第二次踩，第一次是 S152 的 `--dry`）：
    #   入口 ROUTE 把 `close` 路由到本模块，但 mod_argv 只对 VERB_KEEP 里的动词
    #   （link 系/size/gate）保留动词名，**`close` 不在其中** → 模块收到的是
    #   `['done', sid, ev]`。而 HELP 与所有文档写的都是 `tvbox close done <sid> <证据>`——
    #   人照着文档敲，手上就是 `close …`；直接跑 `python3 unfinished_cli.py close done …`
    #   时 `close` 就会走到下面「未知子命令」分支**报错退出**。
    #   更糟的是**方向相反**的那一半：不带子命令敲 `… close` 会落到 `cmd_close`
    #   去跑六源全量采集——一次误拼看起来像"收尾成功"，实际什么都没收口。
    #   判据（不是枚举）：`close` 是模块自己的动词名，出现即剥掉，后面才是真子命令。
    if rest and rest[0] in ('close', 'finish'):
        rest = rest[1:]
        if not rest:
            return cmd_close([])
    # **S152 修：以 `-` 开头的 token 是「参数」，不是「子命令」**。
    # 入口 HELP 写着 `tvbox close [--dry|status|...]`，但 `--dry` 走到这里被当成
    # 子命令名去逐个 if 比对，全部落空 → 报「未知子命令：--dry」。
    # 而 `cmd_close(rest)` 自己第 56 行就支持 `--dry`（`dry = '--dry' in rest`）——
    # **能力存在、且入口文档也写了，唯独分发层没把参数放行进去**。
    # 这比"幽灵命令"更隐蔽：文档真、实现真，只有隔在中间的分发层是断的。
    # 判据（不是枚举）：**凡 `-` 开头皆参数**，一并返回 cmd_close 自行解析。
    if str(rest[0]).startswith('-') and rest[0] not in ('-h', '--help', 'help'):
        return cmd_close(rest)
    sub = rest[0]
    tail = rest[1:]
    if sub == 'status':
        return cmd_status(tail)
    if sub == 'done':
        return cmd_done(tail, 'done')
    if sub == 'wontfix':
        return cmd_done(tail, 'wontfix')
    if sub == 'defer':
        return cmd_defer(tail)
    if sub == 'mine':
        return cmd_mine(tail)
    if sub == 'verify':
        return cmd_verify(tail)
    if sub == 'report':
        return cmd_report(tail)
    if sub == 'promise':
        return cmd_promise(tail)
    if sub == 'link':
        import link_four
        return link_four.main()
    if sub == 'learn':
        # S148：**指向真正的写入器，不另起一套**。
        # `tvbox log` / `tvbox lib-log`（tvbox-source-hub/scripts/link.py）本来就在写经验池，
        # 这里只做转接，好处是"收尾时想起来该落池"的人能在 close 下找到入口，
        # 而不必去记另一个命令族。**禁止在这里复制写入逻辑**（M-R38 单一事实源）：
        # 一次 S148 的教训就是先写了重复实现，再回头删掉。
        # 注意参数拼接：`log` 的摘要走 --summary，`lib-log` 的场景走 --scene，
        # 两个命令的参数名不同，故只**去掉 biz/plat 这个前缀词**，其余原样透传。
        _args = (['log'] + tail[1:]) if tail and tail[0] == 'biz' else \
                (['lib-log'] + tail[1:]) if tail and tail[0] == 'plat' else None
        if _args is None:
            print('用法: tvbox close learn biz|plat …（转接 tvbox log / lib-log）')
            print('  查池状态: tvbox sizes（看两库条数）｜tvbox learn <关键字> 查业务经验')
            return 1
        import subprocess as _sp
        return _sp.run(['tvbox'] + _args).returncode
    if sub in ('-h', '--help', 'help'):
        print(USAGE); return 0
    # 未知子命令：防止 `tvbox close 随便什么` 静默当成收尾
    print(f'❌ 未知子命令：{sub}')
    print(USAGE)
    return 1


def dispatch_cmd(argv):
    """入口协议（与其它技能一致）：tvbox 入口用 safe_dispatch(module, argv) 调用。
    未注册 dispatch 会被入口当「非标准模块」——这就是 M-R81 里「谁都能调」的技术前提。"""
    argv = list(argv)
    if len(argv) == 1 and isinstance(argv[0], (list, tuple)):
        argv = list(argv[0])
    try:
        rc = main(argv)
    except SystemExit:
        raise
    except Exception as e:
        print(f'❌ 清账司执行异常：{type(e).__name__}: {e}')
        return 1
    return rc or 0


# 入口协议别名：tvbox 入口按 `module.dispatch(argv)` 调用。
# 内部模块用 dispatch_mod 别名（见文件头 import），避免「模块名 = 函数名」遮蔽。
dispatch = dispatch_cmd


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]) or 0)
