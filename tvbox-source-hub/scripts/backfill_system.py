#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回填 `体系` 字段：把不依赖外部业务 jar 的本地线路从 jar系 改为直链系（S111）。

判据正本是 `link.classify()`，本脚本只是把它对存量数据重算一遍再写回，
**不自己实现判据**（M-R38 单一事实源）。

起因：`classify()` 原逻辑把 `./py/*.py` 与 `./lib/*.js` 判 jar系，理由是"要有引擎 jar 才跑"。
这条推理把「引擎」当成了「外部业务 jar 依赖」——两回事：
  · jar系 = 出包必须带某个外部业务 jar（mojiGuard/amns/各家自定类），不带就起不来
  · 本地 py/js = 靠 py 引擎（drpy2/TVB-py）执行，与 jar 依赖无关
反向验证：`jar_file()` 认得出 jar 家族的 241 条，恰好等于库里有 jar 的 241 条，一条不差。

⚠️ csp_ 一律保持 jar系：测活证据写明「csp类线：依赖 jar，仅可随底包端测」。
   `csp_PanUC`/`csp_Config` 看着像标准类，用的仍是 CatVod 标准 jar —— 标准 jar 也是 jar。

用法：
    python3 backfill_system.py            # 预演（默认）
    python3 backfill_system.py --apply    # 真写
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys

sys.path.insert(0, __file__.rsplit('/', 1)[0])

import core                                      # noqa: E402
from guard import Triage                        # noqa: E402
from link import classify                       # noqa: E402

DB = 'pNwZ9uBOTz6FzRuvu8nryI'   # 线路源库


def dump_rows():
    """读整表（CSV 形态，含 record_id 需另查，这里只用于比对期望值）。"""
    out = subprocess.run([sys.executable, f'{core.LIB}/database/get_database_content.py',
                          '--database-id', DB, '--page-size', '900'],
                         capture_output=True, text=True, cwd=core.LIB).stdout
    d = json.loads(out)
    return list(csv.DictReader(io.StringIO(d.get('content', ''))))


def find_id(api):
    """按 api 精确查 record_id。查不到返回 None（不猜）。"""
    body = {'database_id': DB,
            'filter': {'property': {'property': 'api接口', 'text': {'equals': api}}},
            'page_size': 5}
    out = subprocess.run([sys.executable, f'{core.LIB}/database/query_database_record.py', '--stdin'],
                         input=json.dumps(body, ensure_ascii=False),
                         capture_output=True, text=True, cwd=core.LIB).stdout
    try:
        res = json.loads(out).get('results') or []
    except Exception:
        return None
    hit = [r for r in res if str(r.get('api接口') or '').strip() == api.strip()]
    return hit[0].get('record_id') if hit else None


def batch_update(pairs):
    """pairs = [(record_id, properties)]，一次 ≤100 条。返回 (ok, fail, errors)。"""
    recs = [{'record_id': rid, 'properties': props} for rid, props in pairs]
    out = subprocess.run([sys.executable, f'{core.LIB}/database/batch_update_database_records.py',
                          '--database-id', DB, '--records', json.dumps(recs, ensure_ascii=False)],
                         capture_output=True, text=True, cwd=core.LIB).stdout
    ok = fail = 0
    errs = []
    try:
        d = json.loads(out)
    except Exception:
        return 0, len(recs), [f'返回非 JSON：{out[:200]}']
    for x in d.get('results', []):
        if x.get('success'):
            ok += 1
        else:
            fail += 1
            errs.append(f"[{x.get('index')}] {x.get('error')}")
    if not d.get('results'):
        errs.append(f'无 results 明细：{str(d)[:300]}')
        fail = len(recs)
    return ok, fail, errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真写库（默认预演）')
    a = ap.parse_args()

    tri = Triage('回填 体系·本地线路改判直链系')
    rows = dump_rows()
    if not rows:
        print('❌ 读表为空，放弃（读失败 ≠ 空库）')
        return 2
    print(f'▌线路源库 {len(rows)} 行')

    # 只取本判据能覆盖的：本地相对路径
    todo = []
    for r in rows:
        api = str(r.get('api接口') or '').strip()
        if not api.startswith('./'):
            continue
        want, _ = classify(api)
        cur = str(r.get('体系') or '').strip()
        if want != cur:
            todo.append((api, cur, want, r))

    print(f'  需改判 {len(todo)} 条：')
    for api, cur, want, r in todo:
        print(f'    {api:28} {cur} → {want}   ({str(r.get("名称") or "")[:26]})')
    tri.done('比对判据', n=len(rows), note=f'需改 {len(todo)}')

    if not todo:
        tri.done('回填', n=0, note='无需改动')
        print(); print(tri.report()); return 0

    if not a.apply:
        print('\n（预演，未写库；加 --apply 执行）')
        tri.done('回填', n=0, note='预演')
        print(); print(tri.report()); return 0

    pairs = []
    missing = []
    for api, cur, want, _ in todo:
        rid = find_id(api)
        if not rid:
            missing.append(api)
            continue
        pairs.append((rid, {'体系': {'select': want}}))
    if missing:
        print(f'  ⚠️ {len(missing)} 条查不到 record_id，未处理：{missing[:5]}')

    ok, fail, errs = batch_update(pairs)
    print(f'  写入结果：成功 {ok} / 失败 {fail}')
    for e in errs[:5]:
        print(f'    ✗ {e}')

    # 写后读回核对（不信 success）
    after_rows = dump_rows()
    after = {str(r.get('api接口') or '').strip(): str(r.get('体系') or '').strip() for r in after_rows}
    hit = sum(1 for api, cur, want, _ in todo if after.get(api) == want)
    print(f'  读回核对：{hit}/{len(todo)} 已生效')

    if fail or hit < len(todo):
        tri.undecidable('回填未全部确认', f'写失败 {fail}，读回未生效 {len(todo) - hit}', n=len(todo))
    else:
        tri.done('回填并读回确认', n=hit)
    print(); print(tri.report())
    return 0 if fail == 0 and hit == len(todo) else 1


if __name__ == '__main__':
    sys.exit(main())
