#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""version_guard.py —— 版本一致性体检（S158·P0）

背景：2026-10-18 复盘发现 probe-hub 的 CHANGELOG 已到 v1.7.6，但 SKILL.md 与
生态库仍停在 1.7.2——**1.7.3/1.7.4/1.7.5 从没进过版本号**（文档记了、机器不认）。
source-hub 同样漏 1 个版本。根因：手工写 CHANGELOG 时不会同步版本号。

本工具做三处对账：
  ① SKILL.md frontmatter 的 `version`
  ② CHANGELOG.md 最新段的版本号
  ③ 生态库（云端 DB）登记的 version

三处不一致 → 报漂移。
**三态纪律（M-R60）**：云端读不到 → 第三列为"无法判定"，绝不当"一致"。

命令：
  version_guard.py            体检（人类可读）
  version_guard.py --json     机器可读
  version_guard.py --fix      自动对齐 SKILL.md 到 CHANGELOG 最新版本（不动云端）
  version_guard.py --sync-eco 把**本地权威版本**写回云端生态库（S162 补）
退出码：0=全部一致（或仅有"无法判定"）；1=存在真实漂移。

★ S162 补记——「只能看不能补」的半截工具：
  S158 建本工具时，`--fix` 只修 SKILL↔CHANGELOG 这一对**本地**面；工具最后打印的
  `↪ 修复：version_guard.py --fix（对齐本地）＋ 同步云端生态库`，后半句
  **根本没有对应命令**。实测 2026-10-19：11 个技能里 6 个的漂移全在**第三列（云端）**，
  本地两列早已对齐 → `--fix` 对这 6 个全都「无需修复」，报告永远是红的。
  这又正是本工具 docstring 开头写的 M-R40「副本静默过期」——**副本没跟权威源走**：
  技能升版本时只改本地，云端靠手工记，必然滞后。
  现在 `--sync-eco` 把「本地 SKILL.md 版本」当权威源写回云端，并把**写后回读**的
  结果逐技能打印（M-R77：success 不等于落库；不许只看回执）。
"""
import os
import re
import sys
import json
import argparse


SKILLS = '/root/.codebuddy/skills'
DB_ECO = 'T4kODtANe7rSrtZTu4sIND'
# core 模块真实位置在 tvbox-source-hub/scripts（eсosys 共用底座），不是 library 根
LIB = '/root/.codebuddy/skills/tvbox-source-hub/scripts'


def _ver_from_skill(p):
    try:
        t = open(p, encoding='utf-8').read(3000)
    except Exception:
        return None
    m = re.search(r'(?m)^version:\s*["\']?([0-9]+\.[0-9]+\.[0-9]+)', t)
    return m.group(1) if m else None


def _ver_from_changelog(p):
    """取 CHANGELOG 最靠前的版本段（兼容 `## 2026-10-18 v1.7.6` 与 `## v1.7.6 — 说明`）。"""
    try:
        t = open(p, encoding='utf-8').read(4000)
    except Exception:
        return None
    m = re.search(r'(?m)^##\s+(?:\d{4}-\d{2}-\d{2}\s+)?v?([0-9]+\.[0-9]+\.[0-9]+)', t)
    return m.group(1) if m else None


def _ver_from_eco():
    """读云端生态库版本映射。返回 (dict, ok)；ok=False 表示无法判定（M-R60）。"""
    try:
        sys.path.insert(0, LIB)
        from core import rows_of, invalidate
        # ★ S158 坑：rows_of 默认 _cache=True，刚写完云端立刻校验会读到**旧缓存**，
        #   表现为「写入成功但回读还是旧版本」的假失败（本次实测踩到）。
        # ★ S162 补刀：**只加 _cache=False 不够**。core 有**两层**缓存：
        #     ① rows_of_t 的 db 键   ② api() 的 ('api',script,args) 键
        #   `_cache=False` 只跳过 ①，仍然走 api()，而 api 默认 _cache=True
        #   → 同进程内第二次读照样命中旧值。这正是 core.invalidate 注释里写的
        #   「S020 实测复发，同症状第二次」。所以必须先 invalidate(DB_ECO) 清两套。
        invalidate(DB_ECO)
        rows = rows_of(DB_ECO, _cache=False)
        if not rows:
            return {}, False
        return {str(r.get('技能名') or ''): str(r.get('version') or '') for r in rows}, True
    except Exception:
        return {}, False


def _cmp(a, b):
    try:
        return tuple(int(x) for x in a.split('.')) < tuple(int(x) for x in b.split('.'))
    except Exception:
        return False


# ---------- S162：云端生态库回写（--sync-eco） ----------
LIB_DB = '/root/.codebuddy/skills/library/database'


def _eco_id_name():
    """一次查询，同时给出 name→id 与 id→name 两张表。

    **为什么要两张**：name→id 用于写入定位，id→name 用于**写入前的反向核对**。
    只有正向表时，一个取错的 id 在正向表里「查得到」（它确实是个合法 id），
    没有任何判据会发现它属于别人——S162 就这么把 tvbox-interface 写成了 library 的版本。
    反向表让「id 挂的不是这个名」变成**可判定的错误**，而不是等回读时才发现数字不对。

    返回 (id2name, name2id, why)；why 非空＝无法判定。
    """
    import subprocess
    q = os.path.join(LIB_DB, 'query_database_record.py')
    if not os.path.exists(q):
        return {}, {}, f'{q} 不存在——无法取 record_id（无法判定）'
    try:
        # 只需一页：生态登记表仅 9 个技能条目（远小于 page-size 50），无需分页遍历
        r = subprocess.run([sys.executable, q, '--database-id', DB_ECO, '--page-size', '50'],
                           capture_output=True, text=True, timeout=40)
        rows = (json.loads(r.stdout or '{}') or {}).get('results')
        if not isinstance(rows, list):
            raise ValueError('results 非数组')
    except Exception as e:
        return {}, {}, f'取 record_id 失败（{type(e).__name__}: {str(e)[:60]}）——无法判定'
    i2n, n2i = {}, {}
    for r_ in rows:
        rid = str(r_.get('record_id') or '').strip()
        name = str(r_.get('技能名') or '').strip()
        if rid and name:
            i2n[rid] = name
            n2i[name] = rid
    if not n2i:
        return {}, {}, '查询结果里一条带 record_id 的记录都没有——无法回写（无法判定）'
    return i2n, n2i, ''


def _eco_record_ids():
    """技能名 → record_id 映射。写云端必须按 record_id，按名字写不进去（S158 G9 事故现场）。

    ★ 坑（S162 实测踩到）：`core.rows_of()` 返回的行里**没有 record_id**——
      它是「属性视图」，只给字段值；record_id 是行的身份，不在属性里。
      第一版回写直接用了 rows_of，于是 `--sync-eco` 报「没有 record_id 字段」，
      看着像云端坏了，其实是**取错了数据源**。改用 query_database_record.py（整行返回含 id）。

    返回 (map, why)；why 非空表示无法取到 id（M-R60：无法判定，不是「没有」）。
    """
    _i2n, n2i, why = _eco_id_name()
    return n2i, why


def sync_eco(rows):
    """把本地 SKILL.md 版本回写到云端生态库（权威源＝本地 SKILL.md）。

    为什么权威是本地：SKILL.md 与 CHANGELOG 都由「改技能」这一动作同步产生，
    而云端是人手工补记的**副本**。副本跟权威走，不反过来。

    铁律：写后必须回读该字段的值（M-R77）。batch_update 对 record_id 不存在时
    返回的是「更新 0 条」而**不是报错**，只看回执会把「一条没写进去」当成成功。
    """
    rec_ids, why = _eco_record_ids()
    if why:
        print(f'❌ --sync-eco 无法执行：{why}')
        print('   M-R60：读不到 ≠ 一致，本次不做任何写入。')
        return 1
    todo = [r for r in rows if r['skill_md'] and r.get('eco') != r['skill_md']]
    if not todo:
        print('✅ 云端无需回写（所有自建技能第三列已与本地一致）')
        return 0
    print(f'🔧 回写云端生态库（权威源＝本地 SKILL.md）{len(todo)} 条：')
    now = __import__('datetime').datetime.now(
        __import__('datetime').timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    recs, skipped = [], []
    for r in todo:
        rid = rec_ids.get(r['skill'])
        if not rid:
            skipped.append(f"{r['skill']}（云端无此技能名，无法定位 record_id）")
            continue
        # ★ S162 第二处自伤：这里原来只放 record_id + properties，**没带 skill**。
        #   下游核对段却写 `r['skill']` → 一旦真的有待写项，就直接 KeyError 崩掉。
        #   而它一直没崩，是因为前面几次跑全都「无需回写」——**空分支掩盖了崩溃点**。
        #   教训：全绿路径没跑过的代码，不算写完；写判据必须用能进分支的合成输入试。
        recs.append({'skill': r['skill'], 'record_id': rid,
                     'properties': {'version': {'text': r['skill_md']},
                                    '末次升级': {'date': now}}})
    # ★ S162 自伤防线（血换的）：写之前必须逐条核对「record_id 上挂的真是这个技能名」。
    #   事故现场：我手工试批量时按**两套数据源的行序**各取了一个 id
    #   （rows_of 里 library 排第 5、record_id 映射里 tvbox-interface 排第 1），
    #   结果把 library 的 record_id 配给了 tvbox-interface —— **两边版本同时被写成 0.6.1**，
    #   而 batch_update 对每条都返回 success:true。回执全绿，数据全错。
    #   这就是 M-R77「success 不等于落库」的第三种形态：**落库了，但落错了行**。
    #   防御＝写入前做一次「id → 技能名」反向核对，对不上就整批拒写。
    _id2name, _r2, _ = _eco_id_name()
    _mismatch = [f"{r['skill']}（id={r['record_id']} 实际挂着 {_id2name.get(r['record_id'])!r}）"
                 for r in recs if _id2name.get(r['record_id']) != r['skill']]
    if _mismatch:
        print('❌ 写入前核对失败，整批拒写（防止写错行）：')
        for x in _mismatch:
            print(f'   {x}')
        return 1
    if recs:
        import subprocess
        # 报文里剥掉 skill（云端不认这个字段），只送 record_id + properties
        payload = [{'record_id': r['record_id'], 'properties': r['properties']} for r in recs]
        res = subprocess.run(
            [sys.executable, os.path.join(LIB_DB, 'batch_update_database_records.py'),
             '--database-id', DB_ECO,
             '--records', json.dumps(payload, ensure_ascii=False)],
            capture_output=True, text=True)
        # ★ S163：区分「被拒」与「失败」。闸门拦截现在 rc=1（S163 修 library 侧假成功），
        #   这里把两类分开说：被拒=去重新 ask，不是接口坏了；其他失败=按失败报。
        _out = (res.stderr or '') + (res.stdout or '')
        if res.returncode != 0 and '会话闸门' in _out:
            print('   🚫 写库被会话闸门拒绝——不是接口坏了。重新登记：tvbox conductor ask "<需求>"')
            return 1
        if res.returncode != 0:
            print(f'   ❌ 批量写入失败（退出码 {res.returncode}）：{(res.stderr or res.stdout)[:200]}')
    # 写后回读：不信回执，重新整表读一遍逐条比
    # ★ S162 第四处（v1.10.0 真实场景踩到）：`get_database_content` 接口有**数秒级读缓存**。
    #   写完立刻回读，读到的还是旧值 → 判「未落库」，rc=1，可云端其实已经写进去了。
    #   这是**误报**，比漏报更伤：它会教人「回读报错=接口坏了」，下次真出事就不信了。
    #   处置：回读不通过时**退避重试**（3 次，间隔递增），仍不一致才认；三次读完
    #   若值在推进（旧值→新值），说明是延迟不是失败，按成功算但如实注明「重试后确认」。
    after = _read_back(todo, tries=3)
    rows2, eco_ok = collect()
    after.update({r['skill']: r['eco'] for r in rows2})
    bad_n = 0
    for r in todo:
        got = after.get(r['skill'], '<<不在核对范围>>')
        if got == r['skill_md']:
            print(f"   ✅ {r['skill']:<26}{r.get('eco') or '-'} → {got}")
        elif r['skill'] in [s.split('（')[0] for s in skipped]:
            print(f"   ⚠️ {r['skill']:<26}跳过：云端无此技能名")
        elif got == '<<不在核对范围>>':
            # ★ S162 第三处：`collect()` 只收「有 CHANGELOG」的项，插件市场技能（library 无
            #   CHANGELOG）压根不进回读表。原实现 `after.get(...)` 得到 None，
            #   被判成「回读仍为 None —— 未落库」，可**下一行结论又写「全部对齐」**：
            #   同一函数里两句自相矛盾，而 rc 仍是 0。
            #   三态：读不到 ≠ 写失败 ≠ 一致 —— 单独说清楚，不借道任何一边。
            print(f"   ❔ {r['skill']:<26}不在三处核对范围（无 CHANGELOG，插件市场技能）"
                  f"——无法用本工具回读确认，**不是**写入失败（M-R60）")
        else:
            print(f"   ❌ {r['skill']:<26}回读仍为 {got!r}（期望 {r['skill_md']}）——未落库")
            bad_n += 1
    if not eco_ok:
        print('   ⚠️ 回读时生态库读取失败——本次结论标「无法判定」，勿当回写成功（M-R60）')
        return 1
    if bad_n:
        print(f'   🔴 {bad_n} 条回读未确认写入——本次不算成功')
        return 1
    left = [r for r in rows2 if r['status'] == 'drift']
    if left:
        print(f'   🔴 仍有 {len(left)} 处漂移：' + '、'.join(r['skill'] for r in left))
        return 1
    print(f'   ✅ 全部对齐（{len(rows2)} 个自建技能三处一致）')
    return 0


def _read_back(todo, tries=3):
    """写完立刻回读——带退避，专治接口读缓存（S162 v1.10.0 实测）。

    判据不是「读到的值对不对」，而是**「值有没有在推进」**：
      · 第一次就读到新值 → 立刻返回，不浪费重试
      · 读到旧值 → 退避再读（1s / 2s / 3s，等缓存失效）
      · 三次都是旧值 → 返回旧值，调用方据此判「未落库」

    为什么不干脆「写死了等 5 秒再读」：那会把快的情况也拖慢，而且 5 秒不一定够。
    按「读到就停」推进，快的照快，慢的才等。
    """
    import time
    want = {r['skill']: r['skill_md'] for r in todo}
    got = {}
    for i in range(tries):
        if i:
            time.sleep(i)
        eco, ok = _ver_from_eco()
        if not ok:
            continue
        for k, v in want.items():
            if eco.get(k) == v:
                got[k] = v
        if all(k in got for k in want):
            break
    return got


def collect():
    eco, eco_ok = _ver_from_eco()
    out = []
    for s in sorted(os.listdir(SKILLS)):
        sp = os.path.join(SKILLS, s, 'SKILL.md')
        cp = os.path.join(SKILLS, s, 'CHANGELOG.md')
        if not os.path.exists(sp):
            continue
        v_skill = _ver_from_skill(sp)
        v_cl = _ver_from_changelog(cp) if os.path.exists(cp) else None
        # 只对"有 CHANGELOG 的自建技能"做三重对账（插件市场技能不归我们管）
        if not v_cl:
            continue
        v_eco = eco.get(s) if eco_ok else None
        status = 'ok'
        if v_skill and v_cl and v_skill != v_cl:
            status = 'drift'
        elif v_eco and v_skill and v_eco != v_skill:
            status = 'drift'
        # ★ S176 修：**"生态库没这一行"不是"一致"，是"未登记"**。
        #   原代码 `elif v_eco and ...` —— v_eco 为 None 时整条短路，status 停在 'ok'。
        #   于是 automation-task-manager（云端根本没登记）在表里显示成 `-` 却算 ✅，
        #   结论行还把总括成「13 个自建技能全部一致」。实测：那 13 里就有这个没登记的。
        #   这是 M-R60 的第三次同形复发：**读不到/没有 ≠ 对**。
        #   单列 unregistered 而不是并进 drift：两者处置不同（一个是补登记，一个是纠版本）。
        elif eco_ok and v_skill and not v_eco:
            status = 'unregistered'
        out.append({'skill': s, 'skill_md': v_skill, 'changelog': v_cl,
                    'eco': v_eco, 'status': status,
                    'eco_known': bool(eco_ok)})
    return out, eco_ok


def main(argv=None):
    ap = argparse.ArgumentParser(prog='version_guard.py')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--fix', action='store_true', help='把 SKILL.md 版本对齐到 CHANGELOG 最新（不动云端）')
    ap.add_argument('--sync-eco', dest='sync_eco', action='store_true',
                    help='把本地 SKILL.md 版本回写云端生态库（写后回读确认）')
    a = ap.parse_args(argv)

    rows, eco_ok = collect()
    drift = [r for r in rows if r['status'] == 'drift']
    unreg = [r for r in rows if r['status'] == 'unregistered']

    if a.sync_eco:
        return sync_eco(rows)

    if a.fix:
        fixed = []
        for r in drift:
            if r['changelog'] and r['changelog'] != r['skill_md']:
                p = os.path.join(SKILLS, r['skill'], 'SKILL.md')
                t = open(p, encoding='utf-8').read()
                t2 = re.sub(r'(?m)^(version:\s*)["\']?[0-9]+\.[0-9]+\.[0-9]+["\']?',
                            rf'\g<1>"{r["changelog"]}"', t, count=1)
                if t2 != t:
                    open(p, 'w', encoding='utf-8').write(t2)
                    fixed.append(f'{r["skill"]}: {r["skill_md"]} -> {r["changelog"]}')
        if fixed:
            print('🔧 已对齐 SKILL.md：')
            for f in fixed:
                print(f'   {f}')
        else:
            print('🔧 无需修复（无 SKILL.md 与 CHANGELOG 不一致项）')
        print('   ⚠️ 云端生态库需另行同步（本工具不改云端）')
        return 0

    if a.json:
        print(json.dumps({'eco_known': eco_ok, 'rows': rows}, ensure_ascii=False, indent=1))
        return 1 if (drift or unreg) else 0

    print('🔍 版本一致性体检（SKILL.md / CHANGELOG / 生态库）\n')
    print(f"  {'技能':<26}{'SKILL':<10}{'CHANGELOG':<12}{'生态库':<10}状态")
    for r in rows:
        st = {'ok': '✅', 'drift': '🔴 漂移', 'unregistered': '⚠️ 生态库未登记'}.get(r['status'], r['status'])
        eco = r['eco'] if r['eco'] is not None else ('无法判定' if not r['eco_known'] else '-')
        print(f"  {r['skill']:<26}{str(r['skill_md'] or '-'):<10}"
              f"{str(r['changelog'] or '-'):<12}{str(eco):<10}{st}")
    print()
    if not eco_ok:
        print('  ⚠️ 生态库读取失败——第三列标"无法判定"，不当"一致"（M-R60 三态）')
    if unreg:
        print(f'  ⚠️ {len(unreg)} 个技能**生态库未登记**（不是"一致"，是压根没这一行）：')
        for r in unreg:
            print(f'     {r["skill"]}：SKILL={r["skill_md"]} / CHANGELOG={r["changelog"]} / 生态库=未登记')
        print('  ↪ 处置：conductor.py onboard <技能名> 补纳管（工具不会自动新增行，只回写已有行）')
    if drift:
        print(f'  🔴 发现 {len(drift)} 处漂移：')
        for r in drift:
            print(f'     {r["skill"]}：SKILL={r["skill_md"]} / CHANGELOG={r["changelog"]} / 生态库={r["eco"]}')
        print('  ↪ 修复：version_guard.py --fix（对齐本地两列）')
        if any(r.get('eco') and r.get('eco') != r.get('skill_md') for r in drift):
            print('           version_guard.py --sync-eco（把本地版本回写云端，S162 补）')
        return 1
    if unreg:
        # 有未登记就**不许**说"全部一致"。原来这行无条件打印，
        # 于是屏幕上刚列完「1 个未登记」，紧跟着一句「✅ 全部一致」——自己打自己脸。
        print(f'  ⚠️ {len(rows) - len(unreg)}/{len(rows)} 个自建技能三处一致，{len(unreg)} 个生态库未登记')
        return 1
    print(f'  ✅ 全部一致（{len(rows)} 个自建技能）')
    # ★ P033②（S185 落地）：把「谁被排除、为什么」显式印出来。
    #   原状：collect() 只收「有 CHANGELOG」的技能，平台技能（library 等）压根不进表——
    #   而体检输出里**一个字都不提它们**，于是"没报错"被读成"没问题"。
    #   这是白名单过滤器的典型失效：被筛掉的东西不会喊疼。
    #   注意：不是要给平台技能做版本核对（它们的版本随官方发布，不归我们管），
    #   而是要让"不核对"这件事本身是可见的、有理由的。
    try:
        _excluded = []
        for _s in sorted(os.listdir(SKILLS)):
            _sp = os.path.join(SKILLS, _s, 'SKILL.md')
            if not os.path.isfile(_sp) or _s in {r['skill'] for r in rows}:
                continue
            _has_cl = os.path.isfile(os.path.join(SKILLS, _s, 'CHANGELOG.md'))
            _excluded.append((_s, '有 SKILL.md 但无 CHANGELOG' if not _has_cl else '未纳入 collect()'))
        if _excluded:
            # 平台安装目录形如 skill_2100202160126345216，几十个，逐个列会把本条淹掉。
            # 按类归并：具名的一次性列清（它们才是需要人判断的），匿名的只报个数。
            _plat = [s for s, _ in _excluded if s.startswith('skill_')]
            _named = [(s, w) for s, w in _excluded if not s.startswith('skill_')]
            print(f'  ℹ️ 另 {len(_excluded)} 个目录不在本表范围（无 CHANGELOG，版本随上游发布，不归本生态核对）：')
            for _s, _why in _named:
                print(f'     · {_s}（{_why}）')
            if _plat:
                print(f'     · 其余 {len(_plat)} 个为平台安装目录（skill_<id>），同上不核对')
    except Exception:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
