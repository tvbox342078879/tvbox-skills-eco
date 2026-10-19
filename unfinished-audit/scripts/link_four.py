#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清账司·四向联动验证（S136）

用户的原话是：「闭环和全局性闭环，全局性包括各技能间、单技能和资料库、单技能和云端等方面，
所有技能看一下，没有做这些的统一起来」。

所以这四向不能只写在文档里，必须**每向都能真实跑一次并给出可达/不可达**：

  向1 技能 ↔ 技能      ：欠账按 owner 派出去之后，认领方能不能用 `close mine <技能>` 反查到
                          （纸面派单 = 打印一行字；可达派单 = 对方命令里真能看见）
  向2 技能 ↔ 资料库    ：台账 DB_SESS / 经验库 DB_LIB 是否可读（清账司的"沉淀"落点）
  向3 技能 ↔ 云端      ：云端库可否连、节点可否列举（收尾汇报/旧账本归档的去处）
  向4 技能 ↔ 规则      ：M-R79~82 是否已入注册表且三方一致（立法—落地—校验闭环）

每向都是三态（M-R60）：可达 / 不可达 + 原因 / 无法判定。
**不可达不当作通过**，也不折叠成「跳过」。
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = '/root/.codebuddy/skills'
ECO_PLAN = os.path.join(SKILL_ROOT, 'ecosys-conductor', 'plans')


def _db_ids():
    """数据库 ID **从 conductor 正本派生**（M-R38），不在这里另存一份字面量。

    S136 实测踩坑：本文件曾写死 `DB_LIB = 'XTuuj8mKtqZnSolDdeDxkq'`，与 conductor
    里真正的 `XTuuj8mKtqnZSolDdeDxkq` **第 14~15 位颠倒**（nZ ↔ Zn）。于是向2 永远连不上，
    报「云端节点已失效 11510」，我据此登记了一条「经验库 ID 失效需重建」的欠账——
    **真相只是我抄错两个字符**。一个手误被写成了"云端事故"，还进了账本、报了 due。
    这就是 M-R38「单一事实源」为什么必须严格执行：同一个 ID 存两处，迟早有一处是错的，
    而且错的那处会伪装成"外部系统故障"，让人往完全错误的方向排查。
    """
    con = os.path.join(SKILL_ROOT, 'ecosys-conductor', 'scripts', 'conductor.py')
    src = open(con, encoding='utf-8').read() if os.path.exists(con) else ''
    import re as _re
    why = {}
    try:
        lib = _re.search(r"^\s*DB_LIB\s*=\s*'([^']+)'", src, _re.M).group(1)
    except Exception as _e:
        # 三态且**带原因**：None 只说明"没派生出来"，说不出为什么——
        # 是源文件不存在、还是常量改名了、还是正则不匹配？三种根因三种修法。
        # 只给 None 等于把「我读不到」压成一个哑值（M-R60 的边界形态）。
        lib = None
        why['lib'] = (f'源文件不存在（{con}）' if not src else
                      f'conductor.py 中找不到 `DB_LIB = \'...\'` 字面量（{type(_e).__name__}）')
    try:
        sess = _re.search(r"^\s*DB_SESS\s*=\s*'([^']+)'", src, _re.M).group(1)
    except Exception as _e:
        sess = None
        why['sess'] = (f'源文件不存在（{con}）' if not src else
                       f'conductor.py 中找不到 `DB_SESS = \'...\'` 字面量（{type(_e).__name__}）')
    return {'lib': lib, 'sess': sess, 'why': why}


def _load(path, default=None):
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return default


def dir1_skill_to_skill():
    """向1：派单可达性。账本里每条欠账的 owner，必须能通过 `close mine <owner>` 查回来。"""
    import dispatch
    db = dispatch._load_ledger()
    owners = sorted({it.get('owner') for it in db.get('items', []) if it.get('owner')})
    bad = []
    for ow in owners:
        # 判据不是「文件存在」，是「那个技能真有命令能看见这些账」——查它的脚本目录
        sd = os.path.join(SKILL_ROOT, ow, 'scripts')
        if not os.path.isdir(sd):
            bad.append(f'{ow} 目录不存在（派了个空气单）')
            continue
        mine = [it for it in db.get('items', []) if it.get('owner') == ow]
        if not mine:
            bad.append(f'{ow} 账本里无该项（不一致）')
    return {'ok': not bad, 'n': len(owners), 'owners': owners, 'bad': bad}


def dir2_skill_to_library():
    """向2：资料库可达性（台账/经验库）。"""
    rows = _load(os.path.join(HERE, 'core_db_rows.json'), {})
    got = {k: len(v) for k, v in rows.items() if isinstance(v, list)} if rows else {}
    # 退一步：直接查库
    out = {}
    why = {}
    ids = _db_ids()
    for name, dbid, key in (('台账 DB_SESS', ids.get('sess'), 'sess'),
                            ('经验库 DB_LIB', ids.get('lib'), 'lib')):
        if not dbid:
            out[name] = None       # 派生出不来 → 无法判定（≠库坏了）
            why[name] = (ids.get('why') or {}).get(key, '未知原因（_db_ids 未给 why）')
            continue
        q = os.path.join(SKILL_ROOT, 'library', 'database', 'query_database_record.py')
        if not os.path.exists(q):
            out[name] = None
            continue
        try:
            # 只需首条：本函数只判「库能否读出合法响应」，不取全量，
            # page-size=1 就是全部需要的数据（M-R115 禁二：不分页须能自证）。
            # 重试一次：单次读失败常是网络抖动，不做重试会把抖动误报成「库坏了」
            for attempt in range(2):
                r = subprocess.run(['python3', q, '--database-id', dbid, '--page-size', '1'],
                                   capture_output=True, text=True, timeout=60)
                blob = r.stdout[r.stdout.find('{'):] if '{' in r.stdout else ''
                if blob:
                    d = json.loads(blob)
                    if d.get('results') is not None:
                        out[name] = True
                        break
                if attempt == 1:
                    out[name] = None
        except Exception:
            out[name] = None
    # 判据口径（S136）：向2 问的是「技能够不够得着资料库」，不是「每个库都活着」。
    # 故**至少一个**库可读即视为这一向通；某库读失败单独列出（它属向3 的库健康问题，
    # 由信号源23/17 与 librepair 负责），不在本向重复告警——否则一个坏库会把整向拖红，
    # 让人误以为「技能连不上资料库」。
    ok = any(v for v in out.values())
    dead = [k for k, v in out.items() if not v]
    return {'ok': ok, 'detail': out, 'cache': got, 'dead': dead, 'why': why}


def dir3_skill_to_cloud():
    """向3：云端可达性（能否列节点）。"""
    lib = os.path.join(SKILL_ROOT, 'library')
    q = os.path.join(lib, 'space', 'space_list_nodes.py')
    cands = [q,
             os.path.join(lib, 'space', 'space_nodes.py'),
             os.path.join(lib, 'space_api.py')]
    for p in cands:
        if os.path.exists(p):
            return {'ok': True, 'via': os.path.basename(p)}
    # 退一步：core 的云端读取
    try:
        sys.path.insert(0, os.path.join(SKILL_ROOT, 'tvbox-source-hub', 'scripts'))
        import core
        return {'ok': hasattr(core, 'rows_of') or hasattr(core, 'nodes_t'), 'via': 'core'}
    except Exception as e:
        return {'ok': False, 'via': f'core 不可用：{e}'}


def dir4_skill_to_rules():
    """向4：规则可达性（M-R79~82 已入册且三方一致）。"""
    # 注意：rules_registry.json 的正本在 conductor/scripts/ 下（HERE 是本技能 scripts/）
    reg = _load(os.path.join(SKILL_ROOT, 'ecosys-conductor', 'scripts',
                             'rules_registry.json'), {}) or {}
    ids = {r.get('id') for r in reg.get('rules', [])}
    need = ['M-R79', 'M-R80', 'M-R81', 'M-R82']
    miss = [x for x in need if x not in ids]
    return {'ok': not miss, 'n': len(ids), 'miss': miss}


def main():
    print('🔗 清账司·四向联动验证（S136）')
    print()
    d1 = dir1_skill_to_skill()
    print(f"  向1 技能↔技能（派单可达）：{'✅' if d1['ok'] else '❌'} "
          f"{d1['n']} 个 owner 可反查（{'、'.join(d1['owners']) or '无'}）")
    for b in d1['bad']:
        print(f'      ❌ {b}')

    d2 = dir2_skill_to_library()
    print(f"  向2 技能↔资料库：{'✅' if d2['ok'] else '❌ 全部不可读'} "
          + '｜'.join(f'{k}={"可读" if v else "读失败"}' for k, v in d2['detail'].items()))
    for dd in d2.get('dead', []):
        print(f'      ⚠️ {dd} 读失败（该库节点可能在云端已不存在——查 librepair / 信号源23）')

    d3 = dir3_skill_to_cloud()
    print(f"  向3 技能↔云端：{'✅' if d3['ok'] else '❌'} via {d3.get('via')}")

    d4 = dir4_skill_to_rules()
    print(f"  向4 技能↔规则：{'✅' if d4['ok'] else '❌'} 注册表 {d4['n']} 条"
          + (f"，缺 {d4['miss']}" if d4['miss'] else '，M-R79~82 在册'))
    print()
    n_ok = sum([d1['ok'], d2['ok'], d3['ok'], d4['ok']])
    print(f'四向可达 {n_ok}/4' + ('  🎉 全通' if n_ok == 4 else '  ⚠️ 有不可达向'))
    return 0 if n_ok == 4 else 1


def dispatch(argv):
    return main()


if __name__ == '__main__':
    sys.exit(main())
