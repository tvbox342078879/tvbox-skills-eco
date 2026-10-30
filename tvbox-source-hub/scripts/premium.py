#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""premium.py —— 优质实测库（source-hub 正式能力，S199 建）

用户上机实测选出的优质线路专属库。四命令闭环：
  init   建库（幂等：已建则直接复用，不重复建）
  add    录一条优质线路（--name --api --system 手机壳子实测/服务端测活 --conclusion --note）
  list   全列
  find   关键词查
写库走 core.add_records（会话闸门+三态），建库走 newlib 正本（mk_folder/mk_db），不自造通道。
"""
import sys, os, json, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import newlib, core  # noqa: E402

IDS_KEY = '优质实测库'
PROPS = [
    {'name': '名称', 'config': {'text': {}}},
    {'name': 'api接口', 'config': {'text': {}}},
    {'name': '体系', 'config': {'select': {'options': [
        {'text': 'py线路'}, {'text': 'csp线路'}, {'text': '直链'},
        {'text': '猫源js'}, {'text': '直播'}]}}},
    {'name': '验证方式', 'config': {'select': {'options': [
        {'text': '手机壳子实测'}, {'text': '服务端测活'}, {'text': '两者齐全'}]}}},
    {'name': '验证结论', 'config': {'select': {'options': [
        {'text': '全链路通过'}, {'text': '列表详情通过'}, {'text': '播放待复验'}]}}},
    {'name': '验证日期', 'config': {'date': {}}},
    {'name': '备注', 'config': {'text': {}}},
]


def _ids():
    ids = newlib.load_ids()
    return ids.get(IDS_KEY) or {}


def _save_ids(v):
    ids = newlib.load_ids()
    ids[IDS_KEY] = v
    newlib.save_ids(ids)


def _db_id():
    """三态（M-R60）：取到/确定没有/建档缺失显式报。"""
    v = _ids()
    if v.get('db'):
        return v['db']
    return None


def cmd_init(a):
    if _db_id():
        print(f'✅ 优质实测库已存在（表 {_db_id()}），幂等复用不重建')
        return 0
    folder = newlib.mk_folder(IDS_KEY)
    if not folder:
        print('❌ 建文件夹失败（云端返回空）')
        return 1
    db = newlib.mk_db('优质实测线路', PROPS, folder)
    if not db:
        print('❌ 建表失败（云端返回空，M-R60 如实报）')
        return 1
    _save_ids({'folder': folder, 'db': db})
    print(f'✅ 优质实测库建成：文件夹 {folder} ｜ 表 {db}')
    return 0


def cmd_add(a):
    db = _db_id()
    if not db:
        print('❌ 优质实测库还没建（M-R60 显式报），先跑 premium.py init')
        return 1
    if not a.name or not a.api:
        print('用法: premium.py add --name <站名> --api <接口> [--system py线路] '
              '[--verify 手机壳子实测] [--conclusion 全链路通过] [--note 备注]')
        return 1
    # 格式（S199 实测）：batch_add 直接把 record 交给 validate_properties 校验，
    #   每个字段值必须恰含一个类型键（text/select/date）——平铺裸文本会被判「校验后为空」。
    rec = {
        '名称': {'text': a.name},
        'api接口': {'text': a.api},
        '体系': {'select': a.system or 'py线路'},
        '验证方式': {'select': a.verify or '手机壳子实测'},
        '验证结论': {'select': a.conclusion or '全链路通过'},
        '验证日期': {'date': datetime.date.today().isoformat()},
        '备注': {'text': a.note or ''},
    }
    ok, fail, why = core.add_records_diag(db, [rec])
    if ok:
        print(f'✅ 已录入优质实测库：{a.name}')
        print('   提醒：下次 gen_config.py 可加 --premium 直接从本库产配置')
        return 0
    print(f'❌ 写入失败 ok={ok} fail={fail} 原因: {why}')
    return 1


def _rows():
    ok, rows, info = core.src_rows_t()  # 不用它；这里单库读：
    return None


def _read_db():
    ok, rows = core.rows_of_t(_db_id())
    if not ok:
        print('❌ 优质实测库读取失败（M-R60 不当空库）')
        return None
    return rows


def cmd_list(a):
    db = _db_id()
    if not db:
        print('❌ 优质实测库还没建')
        return 1
    rows = _read_db()
    if rows is None:
        return 1
    print(f'📚 优质实测库 {len(rows)} 条')
    for r in rows:
        p = r.get('properties', r)
        def g(k):
            v = p.get(k)
            return v.get('select') if isinstance(v, dict) else v
        print(f"  {g('名称')} ｜ {g('体系')} ｜ {g('验证结论')} ｜ {g('验证日期') or '?'}")
    return 0


def cmd_find(a):
    db = _db_id()
    if not db:
        print('❌ 优质实测库还没建')
        return 1
    rows = _read_db() or []
    kw = ' '.join(a.kw)
    hits = [r for r in rows if kw in json.dumps(r, ensure_ascii=False)]
    print(f'🔍「{kw}」命中 {len(hits)} 条')
    for r in hits:
        p = r.get('properties', r)
        v = p.get('名称')
        print('  -', v.get('select') if isinstance(v, dict) else v)
    return 0


CMDS = {'init': cmd_init, 'add': cmd_add, 'list': cmd_list, 'find': cmd_find}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__)
        sys.exit(1)
    argv = sys.argv[2:]
    if sys.argv[1] == 'add':
        class A:
            name = api = system = verify = conclusion = note = ''
        i = 0
        while i < len(argv):
            k = argv[i].lstrip('-')
            if hasattr(A, k):
                setattr(A, k, argv[i + 1] if i + 1 < len(argv) else '')
                i += 2
            else:
                i += 1
        sys.exit(CMDS['add'](A))
    sys.exit(CMDS[sys.argv[1]](sys.argv[2:]))
