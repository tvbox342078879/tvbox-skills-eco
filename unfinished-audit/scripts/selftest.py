#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清账司·受控单测（S136 · 招式 T-E 活体探针 + T-F 机械量化）

为什么必须有：清账司自己也是「检测器」——它输出的「无欠账」同样可能是**装健康的哑巴**。
如果采集器对注入的欠账视而不见、或对已完成项误报，它每个会话都会打印 ✅ 一切正常，
而真实的未完成静悄悄地没人管。这正是 M-R32 说的失败形态。

每个用例都是「造真数据 → 跑真采集器 → 断言检出/不检出 → 清理」：
  T1 真阳性·backlog 未结项能被抓到
  T2 真阳性·CHANGELOG 敞口段能被抓到
  T3 真阳性·缺 proven 的监测项能被抓到
  T4 假阳性·[已清零] 条目不得被抓
  T5 假阳性·「原仍敞开…已全部补齐」这种标题不得被抓（S136 实踩）
  T6 假阳性·「转待办/待办清单」功能描述词不得被抓
  T7 三态·源文件损坏必须报「无法判定」，不得静默返回空
  T8 硬闭环·无主项必须被 verify_ledger 判红
  T9 假阴性·清理后清单一律回到 0（防恒报）

用法: python3 selftest.py
"""

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAIL = []


# 沙箱闸门（S136 踩坑）：单测必须能挡住一切写生产的侧信道。
# 教训：T11/T12 只把账本换成临时文件，但 dispatch.sync → link_eco.notify_owner
# 仍写真实 flow.json，于是单测在生产留下 `[清账司派单…] 甲欠账` 垃圾项，
# 下一轮 watchdog 直接报遗留。**受控单测的定义是「跑完现场干净」，不是「断言过了就行」。**
# 做法：进入单测即把 link_eco.SKILLS_ROOT 指向临时目录，退出时还原。
_SANDBOX = {'root': None, 'orig': None}


def _sandbox_on():
    import link_eco
    _SANDBOX['orig'] = link_eco.SKILLS_ROOT
    tmp = tempfile.mkdtemp(prefix='ufaudit_sbx_')
    _SANDBOX['root'] = tmp
    link_eco.SKILLS_ROOT = tmp
    return tmp


def _sandbox_off():
    import link_eco
    if _SANDBOX['orig'] is not None:
        link_eco.SKILLS_ROOT = _SANDBOX['orig']
    if _SANDBOX['root']:
        shutil.rmtree(_SANDBOX['root'], ignore_errors=True)
    _SANDBOX['root'] = None


def check(name, cond, detail=''):
    print(f'  {"✅" if cond else "❌"} {name}' + (f'  {detail}' if detail else ''))
    if not cond:
        FAIL.append(name)


def _tmp_skillroot():
    """造一个只有最小结构的临时技能根（隔离真实数据，绝不改生产文件）。"""
    root = tempfile.mkdtemp(prefix='ua_probe_')
    for s in ('ecosys-conductor', 'tvbox-inspect'):
        os.makedirs(os.path.join(root, s, 'scripts'), exist_ok=True)
    os.makedirs(os.path.join(root, 'ecosys-conductor', 'plans'), exist_ok=True)
    return root


def t1_t3(root):
    """T1 backlog / T2 changelog / T3 monitors 真阳性。"""
    import collect
    collect.SKILL_ROOT = root
    collect.ECO = ('ecosys-conductor',)
    # backlog：一条未结 + 一条已结
    json.dump({'backlog': ['[已清零 S001] 老账已清', '新欠账：巡检脚本还没接进来']},
              open(os.path.join(root, 'ecosys-conductor', 'scripts', 'flow.json'), 'w'),
              ensure_ascii=False)
    # changelog：一个真敞口段 + 一个「已补齐」假敞口
    open(os.path.join(root, 'ecosys-conductor', 'CHANGELOG.md'), 'w', encoding='utf-8').write(
        '# CH\n\n### 六、仍敞开（本轮明确不做，留档不装睡）\n\n'
        '- 触发词双副本未做成派生\n'
        '- 规则 probe.command 空载\n\n'
        '## 2026-10-01 v1.0\n\n### 四、立法补全（原「仍敞开」四项，本轮已全部补齐）\n\n'
        '- 这条是历史叙述，不该被抓\n')
    # monitors：一项缺 proven
    json.dump({'monitors': [{'id': 'M99', 'name': '未实证检查器'}]},
              open(os.path.join(root, 'tvbox-inspect', 'monitors.json'), 'w'), ensure_ascii=False)

    bl = collect.collect_backlog()
    check('T1 backlog 未结被抓到（1 条）', len(bl) == 1 and '巡检脚本' in bl[0]['title'],
          f'实际 {len(bl)} 条')
    check('T4 [已清零] 条目不被抓（假阳性）',
          not any('老账已清' in x['title'] for x in bl))
    ch = collect.collect_changelog()
    check('T2 changelog 敞口段被抓到', len(ch) >= 1 and any('触发词双副本' in x['title'] for x in ch),
          f'实际 {len(ch)} 条')
    check('T5 「已全部补齐」标题不被抓（假阳性）',
          not any('历史叙述' in x['title'] for x in ch),
          f'实际抓到 {[x["title"][:20] for x in ch]}')
    mo = collect.collect_monitors()
    check('T3 缺 proven 监测项被抓到', len(mo) == 1 and 'M99' in mo[0]['title'])


def t6():
    """T6 功能描述词不得被抓。"""
    import collect
    root = tempfile.mkdtemp(prefix='ua_probe6_')
    os.makedirs(os.path.join(root, 'ecosys-conductor'))
    open(os.path.join(root, 'ecosys-conductor', 'SKILL.md'), 'w', encoding='utf-8').write(
        '# SKILL\n\n- M13 会做「转待办」判定，待办清单也可提取（这是功能描述）\n'
        '- 待补：真的欠账一条\n')
    collect.SKILL_ROOT = root
    collect.ECO = ('ecosys-conductor',)
    got = [x['title'] for x in collect.collect_skillext()]
    check('T6 功能描述词不被抓（假阳性）',
          not any('功能描述' in g for g in got) and any('真的欠账' in g for g in got),
          f'实际 {got}')
    shutil.rmtree(root, ignore_errors=True)


def t7():
    """T7 源损坏必须报 CollectError，不得静默空。"""
    import collect
    root = _tmp_skillroot()
    bad = os.path.join(root, 'ecosys-conductor', 'scripts', 'flow.json')
    open(bad, 'w').write('{ 这不是合法 json')
    collect.SKILL_ROOT = root
    collect.ECO = ('ecosys-conductor',)
    raised = False
    try:
        collect.collect_backlog()
    except collect.CollectError as e:
        raised = '解析失败' in str(e.reason)
    check('T7 源损坏报「无法判定」而非静默空（三态）', raised)
    shutil.rmtree(root, ignore_errors=True)


def t8():
    """T8 无主项必须被 verify_ledger 判红。"""
    import dispatch
    bak_fp = dispatch.LEDGER
    tmp = bak_fp + '.probe'
    shutil.copy(bak_fp, tmp)
    try:
        db = json.load(open(bak_fp, encoding='utf-8'))
        db['items'].append({'sid': 'zzprobe9', 'title': '无主项注入', 'status': 'open',
                            'owner': '', 'next': '', 'due': '', 'signature': 'zzprobe9'})
        json.dump(db, open(bak_fp, 'w', encoding='utf-8'), ensure_ascii=False)
        probs, _ = dispatch.verify_ledger()
        check('T8 无主项被判红（硬闭环）', any('zzprobe9' in p for p in probs),
              f'problems={probs}')
    finally:
        shutil.move(tmp, bak_fp)
    # T9 清理后必须回零
    import importlib
    importlib.reload(dispatch)
    probs, _ = dispatch.verify_ledger()
    check('T9 清理后归零（非恒报）', not any('zzprobe9' in p for p in probs))


def t10():
    """T10 dispatch 分诊必须三项必填（M-R79）。"""
    import dispatch
    it = {'sid': 'x', 'source': 'changelog', 'skill': 's', 'title': '某个路由问题', 'kind': 'route',
          'owner': None, 'next': '', 'due': '', 'evidence': ''}
    dispatch._assign(it)
    check('T10 分诊三项必填齐全', all(it.get(k) for k in ('owner', 'next', 'due')),
          f"{it.get('owner')}/{it.get('next')}/{it.get('due')}")


def t11():
    """T11 部分采集不得把账本抹空（M-R82 自测）——S136 实测踩过的真坑。

    当时 sync() 用 `db['items'] = kept + added` **整体覆盖**，
    任何只喂部分数据的调用都会把「本次没采到的」条目从账本里删掉，且不报错。
    清账司自己犯了它要防的错：账不是靠收口清的，是靠「这次没采到」清的。
    """
    import json
    import shutil
    import collect
    import dispatch
    bak = dispatch.LEDGER + '.probe11'
    shutil.copy(dispatch.LEDGER, bak)
    try:
        db = json.load(open(dispatch.LEDGER, encoding='utf-8'))
        db['items'] = [{'sid': 'zzA', 'signature': 'sigA', 'title': '甲欠账', 'status': 'open',
                        'owner': 'ecosys-conductor', 'next': 'x', 'due': '2099-01-01',
                        'source': 'changelog', 'skill': 's', 'kind': 'rule', 'evidence': ''},
                       {'sid': 'zzB', 'signature': 'sigB', 'title': '乙欠账', 'status': 'open',
                        'owner': 'ecosys-conductor', 'next': 'x', 'due': '2099-01-01',
                        'source': 'changelog', 'skill': 's', 'kind': 'rule', 'evidence': ''}]
        json.dump(db, open(dispatch.LEDGER, 'w', encoding='utf-8'), ensure_ascii=False)
        # 只喂「甲」这一条（模拟部分采集）
        partial = collect._item('changelog', 's', '甲欠账', 'rule', '')
        dispatch.sync([partial])
        after = dispatch._load_ledger()
        ids = {x.get('sid') for x in after['items']}
        check('T11 部分采集不抹掉未采到的欠账（M-R82）',
              'zzA' in ids and 'zzB' in ids, f'实际 {sorted(ids)}')
    finally:
        shutil.move(bak, dispatch.LEDGER)


def t12():
    """T12 派单回声不得让账本自我增殖（M-R81/M-R82 自测）——S136 实测踩过的真坑。

    清账司派单时会把自己的条目写进对方 flow.backlog（派单真送达）。若采集端不识别
    这个副本，下一轮就把它当新欠账采回来：
        第1轮 5 条 → 第2轮 10 条 → 第3轮 20 条 …
    更隐蔽的是总数看起来在「变多」，像发现了新欠账，实际全是自己的回声。
    实测症状：同一 title 出现两条（原项 bc2dd17c605d + 副本 dd4ef2ea9de8）。
    """
    import re
    import collect
    cases = [
        # (样本, 期望被当回声跳过吗, 说明)
        ('[清账司派单 2026-10-24] 「触发词双副本」未做成派生 ｜ sid=bc2dd17c605d ｜ 下一步 x', True, '标准副本'),
        ('  某欠账 ｜ sid=deadbeef99', True, '仅带 sid'),
        ('[待跟进] zz-wild 没人认领的脏尾巴', False, '野生 backlog 必须照采'),
        ('甲欠账', False, '无任何标记'),
    ]
    bad = []
    for text, want_echo, desc in cases:
        got_echo = bool(collect.ECHO_TAG.search(text))
        if got_echo != want_echo:
            bad.append(f'{desc}(期望回声={want_echo}, 实际={got_echo})')
    check('T12 派单回声识别（副本跳过 / 野生照采）', not bad, '；'.join(bad) or '4/4 形态判定正确')

    # 端到端：连跑两轮 sync，第二轮不得新增（模拟回声增殖）
    import json
    import shutil
    import dispatch
    bak = dispatch.LEDGER + '.probe12'
    shutil.copy(dispatch.LEDGER, bak)
    try:
        db = {'items': []}
        json.dump(db, open(dispatch.LEDGER, 'w', encoding='utf-8'), ensure_ascii=False)
        one = collect._item('changelog', 's', '甲欠账', 'rule', '')
        dispatch.sync([one])
        n1 = len(dispatch._load_ledger()['items'])
        dispatch.sync([one])          # 同一批再喂一次（真实情形：下一轮采集到同一条）
        n2 = len(dispatch._load_ledger()['items'])
        check('T12 重复采集不增殖（同 signature 幂等）', n2 == n1,
              f'第1轮 {n1} 条 → 第2轮 {n2} 条')
    finally:
        shutil.move(bak, dispatch.LEDGER)


def t14():
    """T14 CHANGELOG 敞口段里的「已结条目」不得被反复采集（S136 三续·真 bug）。

    实测症状：5 条欠账全部 `done` 后跑 `close`，采集合计仍是 3 条——三条全部"复活"。
    根因：`collect_changelog()` **没有** DONE_TAG 过滤（只有 collect_backlog 有）。
    于是「人在敞口段行首标了 [已清零]」这件事对采集器完全不可见，收口永远关不掉，
    且每轮还会往对方 backlog 派一次单（重复骚扰）。
    这是「账要能清」（M-R82）的致命形态：**做完了也关不掉**。
    """
    import collect
    # 单元级：四种形态的判定
    cases = [
        ('[已清零 S136] 触发词双副本——已做成派生', True, '行首已清零标签'),
        ('（2026-10-17 已清零：证据）某欠账', True, '日期式已清零'),
        ('「触发词双副本」未做成派生', False, '真敞口必须照采'),
        ('[扩展点] 持续演进项', False, '扩展点仍由 backlog 侧处理'),
    ]
    bad = []
    for text, want_done, desc in cases:
        got_done = bool(collect.DONE_TAG.match(text) or collect.DONE_NOTE.match(text))
        if got_done != want_done:
            bad.append(f'{desc}(期望已结={want_done} 实际={got_done})')
    check('T14 敞口段已结判定（4 种形态）', not bad, '；'.join(bad) or '4/4 正确')

    # 端到端：造一个含敞口段的临时 CHANGELOG，验证采集器真的跳过已结条目
    import tempfile
    tmp = tempfile.mkdtemp(prefix='ufaudit_t14_')
    try:
        sk = os.path.join(tmp, 'tvbox-t14')
        os.makedirs(os.path.join(sk, 'scripts'), exist_ok=True)
        open(os.path.join(sk, 'SKILL.md'), 'w', encoding='utf-8').write(
            '---\nname: tvbox-t14\ndescription: 测试\n---\n')
        open(os.path.join(sk, 'CHANGELOG.md'), 'w', encoding='utf-8').write(
            '# CHANGELOG\n\n### 九、仍敞开（留档不装睡）\n\n'
            '- [已清零 S136] 这条做完了\n'
            '- 这条真的没做\n')
        orig = collect.ECO, collect.SKILL_ROOT
        collect.ECO = ['tvbox-t14']
        collect.SKILL_ROOT = tmp
        try:
            got = [it['title'] for it in collect.collect_changelog()]
        finally:
            collect.ECO, collect.SKILL_ROOT = orig
        check('T14 敞口段已结条目不被采集（端到端）',
              len(got) == 1 and '真的没做' in got[0],
              f'实际采到 {len(got)} 条：{got}')
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def t15():
    """T15 已结区幂等：同一欠账被 close 两次不得在 closed 里留下两条（S136 四续·真 bug）。

    实测症状：账本显示「已结 8 条」，实际只有 5 件事——3 条是影子。
    成因链：①某轮 close 把条目移进 closed；②下一轮采集因别的原因把同 signature
    又写回 items（状态 open）；③再 close 一次，`closed.append(hit)` 无条件执行。
    危害不是"多一行"，而是**账目数字本身变得不可信**——而数字是清账司唯一交付物。
    """
    import tempfile
    import dispatch
    tmp = tempfile.mkdtemp(prefix='ufaudit_t15_')
    try:
        led = os.path.join(tmp, 'ledger.json')
        orig = dispatch.LEDGER
        dispatch.LEDGER = led
        try:
            sig = 'abc123def456'
            base = {'signature': sig, 'sid': sig, 'title': '同一件欠账',
                    'owner': 'ecosys-conductor', 'next': 'n', 'due': '2026-10-24',
                    'status': 'open'}
            dispatch._save_ledger({'version': 1, 'updated': '', 'items': [dict(base)], 'closed': []})
            dispatch.close_item(sig, '第一次证据')
            # 模拟状态回滚：采集把同 signature 当新增写回 items
            db = dispatch._load_ledger()
            db['items'] = [dict(base)]
            dispatch._save_ledger(db)
            dispatch.close_item(sig, '第二次证据（应为最终留档）')

            db = dispatch._load_ledger()
            n_closed = len([c for c in db.get('closed', [])
                            if (c.get('signature') or c.get('sid')) == sig])
            ev = [c.get('close_evidence', '') for c in db.get('closed', [])
                  if (c.get('signature') or c.get('sid')) == sig]
            ok = (n_closed == 1 and ev and '第二次' in ev[0])
            check('T15 已结区幂等（重复收口只留最新一条）', ok,
                  f'closed 中同 sid {n_closed} 条，证据={ev}')

            # 自检必须把重复判红（而不是只警告），否则以后没人发现
            db['closed'].append(dict(base, status='done', close_evidence='影子'))
            dispatch._save_ledger(db)
            probs, _w = dispatch.verify_ledger()
            check('T15 已结区重复被判红（自检生效）',
                  any('已结区重复' in p for p in probs),
                  f'problems={probs}')
        finally:
            dispatch.LEDGER = orig
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def t16():
    """T16 重复检测的两类假阳性（S182·实测一次报 18 条）。

    这两条判据错在"拿没有信息量的字段做判据"，而不是错在阈值：
      ① `同 due + 同 next` —— next 是 ROUTE_TABLE 的命令模板时，同 kind 全一样。
         实测「会话编号两套体系」≈「probe pan pull 链路」被判重复，两者毫无关系。
      ② backlog 的 owner 被关键词改判 —— learn-hub 自己的待办因标题含「云端」
         被派给 janitor，**派错的代价是这条永远没人做**。
    """
    import dispatch

    # ① 模板 next 不构成重复证据；具体 next 相同仍然算
    _ia = {'sid': 'a1', 'title': '会话编号两套体系需要统一', 'owner': 'ecosys-conductor',
           'due': '2026-11-03', 'next': 'tvbox conductor issues', 'status': 'open'}
    _ib = {'sid': 'b2', 'title': 'probe pan pull 链路真实可用', 'owner': 'learn-hub',
           'due': '2026-11-03', 'next': 'tvbox conductor issues', 'status': 'open'}
    hit = dispatch._near_duplicate_open([dict(_ia), dict(_ib)])
    check('T16a 模板 next 相同不算重复（假阳性）', not hit, f'误报 {hit}')

    _ic = dict(_ia, sid='c3', next='tvbox janitor scan --fix')
    _id = dict(_ib, sid='d4', owner='tvbox-janitor', next='tvbox janitor scan --fix')
    hit2 = dispatch._near_duplicate_open([_ic, _id])
    # ★ 失败文案写「未报出」有歧义——和断言的期望方向读起来相反，看图的人会以为
    #   "没报出来是对的"。失败信息必须直接说清"期望什么、实际什么"。
    check('T16b 具体 next 相同仍判重复（不误伤真信号）', bool(hit2),
          '应报出重复却没报（真信号被吞）')

    # ② backlog 的 owner 归所属技能，关键词不许改判
    it = {'sid': 'e5', 'title': '需接资料库（library）云端 API', 'kind': 'rule',
          'evidence': '/x/learn-hub/scripts/flow.json backlog[5]', 'owner_fixed': 'learn-hub'}
    dispatch._assign(it)
    check('T16c backlog owner 归所属技能（不被「云端」改判）',
          it.get('owner') == 'learn-hub', f"实际 owner={it.get('owner')}")


if __name__ == '__main__':
    print('🧪 清账司受控单测（造真数据 → 跑真采集器 → 断言 → 清理）')
    print()
    root = _tmp_skillroot()
    sbx = _sandbox_on()
    try:
        t1_t3(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    t6()
    t7()
    t8()
    t10()
    t11()
    t12()
    t14()
    t15()
    t16()
    _sandbox_off()
    # 现场洁净自证（M-R32 镜像）：单测跑完不得在生产 flow.json 留任何派单垃圾
    _leaked = []
    for s in ('ecosys-conductor', 'skill-evolution', 'tvbox-inspect', 'tvbox-janitor'):
        p = os.path.join('/root/.codebuddy/skills', s, 'scripts', 'flow.json')
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding='utf-8'))
            for b in d.get('backlog', []):
                if 'zzA' in str(b) or 'zzB' in str(b) or '甲欠账' in str(b):
                    _leaked.append(f'{s}:{str(b)[:50]}')
        except Exception:
            pass
    check('T13 单测跑完现场洁净（无写生产侧信道）', not _leaked,
          '；'.join(_leaked) or f'沙箱 {os.path.basename(sbx)} 已回收，生产 0 残留')
    print()
    print(f'结果：{"全部通过 🎉" if not FAIL else "失败 " + str(len(FAIL)) + " 项：" + "、".join(FAIL)}')
    sys.exit(1 if FAIL else 0)
