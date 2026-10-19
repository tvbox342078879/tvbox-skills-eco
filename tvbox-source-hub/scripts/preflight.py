#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
preflight.py —— 云端库写入预检（高频问题复盘产物 · 规则52 固化）

针对全量复盘暴露的 TOP1 问题：云端知识库读写坑占 ④经验库 13/16，
4 个阻断级坑（L001/L002/L003/L006）全部集中在此，共同结构是
「服务端校验规则与本地直觉不一致 + 失败信息不直白」。

本脚本在 batch_add_database_records **之前**跑，把四类高频坑拦在本地：
  1. 未知字段名   —— 凭记忆写字段名，服务端静默丢弃或报"校验后为空"（L015 类）
  2. 裸值未包装   —— 值必须是 PropertyValue：{'text':..}/{'select':..}/{'date':..}（L001 阻断）
  3. 类型不匹配   —— select 值不在 options 里 / date 字段给了 text（L006 阻断）
  4. 空值写入     —— text 空串会导致"状态列空值"，违反零空值铁律

用法：
  python3 preflight.py --database-id <id> --records '<JSON数组>'
  python3 preflight.py --database-id <id> --records-file /tmp/to_add.json
  cat recs.json | python3 preflight.py --database-id <id> --stdin

输出：问题清单（含对应经验编号与修法）；全部通过打印 PREFLIGHT_OK。
exit code：0=通过（可有警告）｜1=有阻断级问题（不要写）｜2=用法/数据错误
"""
import argparse
import json
import os
import subprocess
import sys

LIB = '/root/.codebuddy/skills/library'
WRAP_KEYS = ('text', 'select', 'multi_select', 'date', 'checkbox', 'number')


def get_schema(db):
    """S158：改走 core._run_lib（统一三态）。失败仍返回 None——调用方已判账（见下方）。
    此处本就有三态意识（None → 终止预检、不盲写），收敛只是把"调用姿势"统一，
    语义不变：**None 恒表示"取不到"，绝不是"没有字段"**。
    """
    try:
        import core as _c
        ok, out, err = _c._run_lib('database/get_database_schema.py', '--database-id', db)
    except Exception as e:
        print(f'（core 不可用：{type(e).__name__}: {str(e)[:60]}）', file=sys.stderr)
        return None
    if not ok:
        print(f'（schema 读取失败：{err[:120]}）', file=sys.stderr)
        return None
    try:
        d = json.loads(out)
        props = d.get('properties') or []
        return {p['name']: p for p in props}
    except Exception as e:
        print(f'（schema 解析失败：{type(e).__name__}）', file=sys.stderr)
        return None


def check_record(rec, schema, idx, problems):
    if not isinstance(rec, dict):
        problems.append(('阻断', f'records[{idx}] 不是对象：{str(rec)[:60]}'))
        return
    for field, val in rec.items():
        # 1. 未知字段
        if field not in schema:
            problems.append(('阻断', f'records[{idx}] 未知字段「{field}」—— schema 里没有；'
                                    f'凭记忆写字段名=服务端静默丢弃/报"校验后为空"(L015)。'
                                    f'可用字段：{list(schema)}'))
            continue
        p = schema[field]
        ftype = p.get('type')
        # 2. 裸值未包装
        if not isinstance(val, dict) or not any(k in val for k in WRAP_KEYS):
            problems.append(('阻断', f'records[{idx}].{field} 是裸值 {str(val)[:40]}——'
                                    f'必须 PropertyValue 包装，如 {{"text":".."}}(L001 阻断级)'))
            continue
        wrap = next(k for k in WRAP_KEYS if k in val)
        v = val[wrap]
        # 3. 类型匹配
        if ftype == 'select':
            if wrap != 'select':
                problems.append(('阻断', f'records[{idx}].{field} 是 {ftype} 字段却用 {wrap} 包装'))
            else:
                opts = [o.get('text') for o in (p.get('config') or {}).get('options', [])]
                if opts and v not in opts:
                    problems.append(('阻断', f'records[{idx}].{field} 值「{v}」不在选项里——'
                                            f'可选项：{opts}（L006：少了类型层包装/选项外取值）'))
        elif ftype == 'multi_select':
            if wrap != 'multi_select':
                problems.append(('阻断', f'records[{idx}].{field} 是 {ftype} 字段却用 {wrap} 包装'))
            else:
                opts = [o.get('text') for o in (p.get('config') or {}).get('options', [])]
                bad = [x for x in (v or []) if opts and x not in opts]
                if bad:
                    problems.append(('阻断', f'records[{idx}].{field} 值 {bad} 不在选项里：{opts}'))
        elif ftype == 'date' and wrap != 'date':
            problems.append(('阻断', f'records[{idx}].{field} 是 date 字段却用 {wrap} 包装'))
        elif ftype == 'text' and wrap != 'text':
            problems.append(('警告', f'records[{idx}].{field} 是 text 字段却用 {wrap} 包装'))
        # 4. 空值
        if wrap == 'text' and not str(v or '').strip():
            problems.append(('警告', f'records[{idx}].{field} 是空文本——状态列零空值铁律，确认是否有意为之'))
        if wrap == 'select' and not v:
            problems.append(('警告', f'records[{idx}].{field} select 为空'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--database-id', dest='db', required=True)
    ap.add_argument('--records', default='')
    ap.add_argument('--records-file', dest='rf', default='')
    ap.add_argument('--stdin', action='store_true')
    a = ap.parse_args()

    raw = ''
    if a.stdin:
        raw = sys.stdin.read()
    elif a.rf:
        raw = open(a.rf, encoding='utf-8').read()
    elif a.records:
        raw = a.records
    else:
        print(json.dumps({'error': '必须提供 --records / --records-file / --stdin'}, ensure_ascii=False))
        sys.exit(2)
    try:
        recs = json.loads(raw)
        if isinstance(recs, dict):
            recs = [recs]
    except Exception as e:
        print(json.dumps({'error': f'records JSON 解析失败: {e}'}, ensure_ascii=False))
        sys.exit(2)

    schema = get_schema(a.db)
    if schema is None:
        print(json.dumps({'error': 'schema 获取失败，终止预检（不要盲写）'}, ensure_ascii=False))
        sys.exit(2)

    problems = []
    for i, r in enumerate(recs):
        check_record(r, schema, i, problems)

    blockers = [p for p in problems if p[0] == '阻断']
    warns = [p for p in problems if p[0] == '警告']
    for lv, msg in problems:
        print(f'[{"❌阻断" if lv=="阻断" else "⚠️警告"}] {msg}')
    if blockers:
        print(f'\nPREFLIGHT_FAIL 阻断 {len(blockers)} 项 / 警告 {len(warns)} 项 / 共 {len(recs)} 条记录')
        print('→ 修复后再写；这些坑都踩过（④库 L001/L006/L015），别重蹈')
        sys.exit(1)
    print(f'PREFLIGHT_OK 通过（{len(recs)} 条记录，警告 {len(warns)} 项）')


if __name__ == '__main__':
    main()
