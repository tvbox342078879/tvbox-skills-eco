#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B·T4 配置生成器最小版：从源库（线路源库+成品源库联合）按条件筛源 → 拼一份可用的
TVBox sites json。道长「文件即配置」启发：配置不该手拼，该从库一键产。

用法：
  python3 gen_config.py --type py线路 --out my.json        # 按源类型筛
  python3 gen_config.py --kw 星河 --limit 20                # 按名称/接口关键词筛
  python3 gen_config.py --list-fields                       # 先看库里实际有哪些字段再筛
三态纪律（M-R60）：任一库读失败 → 报失败退出，不把失败库当空库静默产配置。
"""
import sys, os, json, argparse, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

_OK_CODES = ('ok', '可用', '正常', '活', '通过')


def main():
    ap = argparse.ArgumentParser(prog='gen_config.py', description='源库→TVBox 配置生成器（B·T4）')
    ap.add_argument('--type', help='按源类型筛（py线路/csp线路 等，模糊匹配）')
    ap.add_argument('--kw', help='按名称/api 关键词筛')
    ap.add_argument('--limit', type=int, default=0, help='最多出几条（0=不限）')
    ap.add_argument('--out', default='gen_sites.json', help='输出 json 路径')
    ap.add_argument('--list-fields', action='store_true', help='只列出字段与示例，不产配置')
    a = ap.parse_args()

    ok, rows, info = core.src_rows_t()
    if not ok:
        print(f'❌ 源库读取失败（M-R60 不当空库）：{info}')
        return 1
    if not rows:
        print('⚠️ 源库为空（真·空库），无可产配置')
        return 1
    fields = sorted({k for r in rows for k in (r.keys() if isinstance(r, dict) else [])})

    def get(r, *names):
        for n in names:
            for k in r:
                if k == n or n in k:
                    v = r[k]
                    if v not in (None, ''):
                        return v
        return ''

    if a.list_fields:
        print(f'📚 源库联合 {info["total"]} 条 ｜ 字段：')
        for f in fields:
            print(f'   {f}  例: {str(get(rows[0], f))[:50]}')
        return 0

    sel = []
    for r in rows:
        styp = str(get(r, '源类型', '类型'))
        name = str(get(r, '名称', '站名', 'name'))
        api = str(get(r, 'api', '接口', '地址', 'url'))
        if a.type and a.type not in styp:
            continue
        if a.kw and (a.kw not in name and a.kw not in api):
            continue
        # 存活/状态软过滤：有状态字段且明确写死/失效的剔除（不确定的保留，交壳子/测活兜底）
        stat = str(get(r, '状态', '存活', '可用性'))
        if any(x in stat for x in ('死', '失效', '不可用')):
            continue
        if not api:
            continue
        _slug = re.sub(r'\W+', '', name)[:12] or 'src'
        grp = str(get(r, '分组类目', '分组')) or '推荐'
        typ = 3 if (api.startswith('./') or api.endswith('.js')
                    or api.endswith('.py')) else 0
        # 甲·源扫描器借鉴（2026-10-21）：1:1 对齐实测全壳可用范本的站点字段
        #   key/name/type/api/searchable/quickSearch/filterable/changeable/group/timeout
        #   （范本经验：type3 站 filterable=0；字段顺序照范本排，不自己猜格式）
        sel.append({'key': f"lib_{len(sel)+1}_{_slug}",
                    'name': name or f'源{len(sel)+1}',
                    'type': typ,
                    'api': api,
                    'searchable': 1,
                    'quickSearch': 1,
                    'filterable': 0 if typ == 3 else 1,
                    'changeable': 0 if typ == 3 else 1,
                    'group': grp,
                    'timeout': 15,
                    **({'play_parse': 1} if typ == 3 else {})})
        if a.limit and len(sel) >= a.limit:
            break
    if not sel:
        print(f'⚠️ 筛选无命中（type={a.type} kw={a.kw}），先跑 --list-fields 看字段')
        return 1
    # 甲：顶层六键对齐范本（spider→wallpaper→parses→flags→ads→sites）；
    #   spider 空串＝无引擎桥，py/js 引擎一律交壳子自带（源扫描器 v2.17 实证）
    cfg = {'spider': '', 'wallpaper': '', 'parses': [], 'flags': [],
           'ads': [], 'sites': sel}
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f'✅ 产出 {a.out}：{len(sel)} 条（源库联合 {info["total"]} 条中筛出）')
    print('   提醒：出的是「候选配置」，上机前照模板跑测活（pipeline.py tpl --file %s 判型）' % a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
