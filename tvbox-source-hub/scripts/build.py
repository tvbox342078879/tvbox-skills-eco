#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build.py —— 出包闭环：候选清单 → 规范化 → 去重 → 入库（S111 重写）。

## 为什么重写

原版只有 67 行，且**没有真正入库**——它把结果写到 `/tmp/records.json` 就结束了，
"出包闭环"实际断在最后一步。同时藏着四个问题：

| 问题 | 后果 |
|---|---|
| 只读硬编码 `/tmp/to_add.json` | 无法指定输入，只能跑唯一路径 |
| `入库时间` 写死 `2026-10-15T01:50:00Z` | 每条记录都声称同一秒入库，时间维度失真 |
| `old_system()` 返回 `guard系`/`lytvs系` | **与库里真实取值 `jar系`/`直链系` 不符**，写进去就是脏数据 |
| 自维护 `CATS` 分组表 | 违背 M-R38，`split_jsm.group_of()` 才是分组正本 |

## 现在的链路

    候选清单(JSON)
      → 规范化（字段/时间/名称）
      → 黑名单拦截（blk.guard，命中直接归档失效库）
      → 去重（api 精确 + 名称+host 近似）
      → 入库（core.add_records，A 档串行保 QPS）
      → 写后读回核对（不信 success=true）
      → Triage 三态收尾

用法：
    python3 build.py --candidates /tmp/to_add.json --dry     # 预演
    python3 build.py --candidates /tmp/to_add.json           # 真入库
    python3 build.py -c x.json --db 线路源库 --limit 50
"""
import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core                                            # noqa: E402
from link import classify                              # noqa: E402  # 体系判定唯一正本
from guard import Triage                               # noqa: E402

LIB = core.LIB
BLK = os.path.join(os.path.dirname(LIB), 'tvbox-source-blacklist', 'scripts', 'blk.py')
QUERY = os.path.join(LIB, 'database', 'query_database_record.py')

# ⚠️ 两套分类并存，别混用（S111 踩过一次）：
#
#   split_jsm.group_of()  → 「json 内部分组名」：外链采集/系统内置/集成解析(csp)/官源JS(drpy)/PY脚本源
#   CATS（本文件）        → 「源库 分组类目 select」：配置/本地/网盘/直链/动漫… 共 20 项
#
# 前者**不在**后者的 options 里。原计划"复用 group_of 满足 M-R38"，实际会被平台判
# invalid fields。核实库内 620 行取值，全部落在 CATS 内，无一例外——证明入库口径是 CATS。
CATS = ['配置', '本地', '网盘', 'DIY', '直链', '动漫', '音乐', '4k', '教育', 'B站',
        '磁力', '直播', '体育', '短剧', '听书', '搜索', '功夫', '影视', 'APP', '其他']


def cat_of(name, kind, api):
    """线路 → 源库「分组类目」。**输出必须落在 CATS 内**，否则平台拒写。

    判据：直播类型 → 直播；否则名称关键词优先；再按 api 形态兜底。
    """
    if kind == 'live':
        return '直播'
    n = name or ''
    pairs = [('网盘', '网盘'), (' Pan', '网盘'), ('阿里', '网盘'), ('夸克', '网盘'),
             ('百度', '网盘'), ('115', '网盘'),
             ('磁力', '磁力'), ('种子', '磁力'), ('荐片', '磁力'),
             ('动漫', '动漫'), ('番剧', '动漫'), ('AGE', '动漫'),
             ('音乐', '音乐'), ('听歌', '音乐'), ('MV', '音乐'),
             ('直播', '直播'), ('电视', '直播'), ('央视', '直播'),
             ('体育', '体育'), ('足球', '体育'), ('NBA', '体育'), ('看球', '体育'),
             ('短剧', '短剧'), ('听书', '听书'), ('有声', '听书'),
             ('搜索', '搜索'), ('4K', '4k'), ('4k', '4k'),
             ('B站', 'B站'), ('哔哩', 'B站'), ('bili', 'B站'),
             ('APP', 'APP'), ('应用', 'APP'),
             ('教育', '教育'), ('课堂', '教育'), ('学习', '教育'),
             ('影视', '影视'), ('电影', '影视'), ('剧', '影视'),
             ('功夫', '功夫'), ('Local', '本地'), ('本地', '本地'),
             ('Alist', 'DIY'), ('Emby', 'DIY'), ('DIY', 'DIY'), ('配置', '配置')]
    for k, v in pairs:
        if k in n:
            return v if v in CATS else '其他'
    if kind == 'parse':
        return '配置'
    if api.startswith(('http://', 'https://')):
        return '直链'
    return '其他'


def host_of(api):
    """线路 → host 字段。csp 类没有 host，写空串（不是写类名小写）。

    S111 修正：原逻辑对非 http 的 api 返回 `a.split('/')[0].lower()`，
    于是 `csp_PanConfigGuard` 变成 `csp_panconfigguard` —— 既不是域名也不是类名，
    拿去做黑名单/去重/统计全是噪声。csp 类的身份是**类名**，另有 `csp类名` 字段承载。
    """
    a = (api or '').strip()
    if a.startswith(('http://', 'https://')):
        try:
            return urlparse(a).netloc
        except Exception:
            return ''
    return ''


def now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


# ★ S111 教训：字段清单不能靠记忆写死。
#   上一版凭印象写了 `源类型`=py/java 之类，实际 schema 只有 `py线路`/`csp线路`；
#   还写了一个 schema 里根本不存在的 `入库时间`。平台对这种记录的处理是**整条拒绝**：
#     {"success": false, "error": "invalid fields (not found in schema...): [体系 入库时间 源类型]"}
#   注意报错里连 `体系` 都算非法 —— 因为该字段是 select，值不在 options 内同样判非法。
#   所以这里改成：①字段名必须命中 schema ②select 值必须命中 options，否则丢弃该字段。
_SCHEMA_CACHE = {}


def schema_of(db):
    """读库真实 schema → {字段名: {'type':..., 'options':[...]}}。读失败返回 None（不缓存）。"""
    if db in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[db]
    script = f'{core.LIB}/database/get_database_schema.py'
    try:
        out = subprocess.run([sys.executable, script, '--database-id', db],
                             capture_output=True, text=True, cwd=core.LIB).stdout
        d = json.loads(out)
    except Exception as e:
        print(f'  ⚠️ 读 schema 失败（{e}），本次不写库以免整条被拒')
        return None
    sc = {}
    for p in d.get('properties', []):
        sc[p['name']] = {'type': p.get('type'),
                         'options': [o['text'] for o in (p.get('config') or {}).get('options', [])]}
    if not sc:
        print('  ⚠️ schema 为空，拒绝写库')
        return None
    _SCHEMA_CACHE[db] = sc
    return sc


def _wrap(val, spec, field, dropped):
    """按 schema 把值包成 {type: value}；不合法则登记到 dropped 并返回 None。"""
    t = spec.get('type') or 'text'
    if t in ('select', 'multi_select'):
        opts = spec.get('options') or []
        if opts and val not in opts:
            dropped.append(f'{field}={val!r}(不在选项内)')
            return None
    return {t: val}


def normalize(x, batch_tag='', schema=None):
    """候选 → 库记录。

    ★ 每个字段都必须是 {类型键: 值} 的 dict（S111 踩坑）：
      `_db_types.validate_properties` 遇到任何非 dict 的值就 `return {}`，
      整条记录被判「校验后为空，无有效字段」——而 `core.add_records` 只统计
      results 里的 success，拿不到 results 就返回 (0,0)，看起来像"成功0失败0"，
      实则是**根本没写**。所以宁可用预检在这里卡死，也不要静默丢记录。
    """
    api = str(x.get('api接口') or '').strip()
    if not api:
        return None
    nm = str(x.get('名称') or '').strip() or '(未命名)'
    kind = x.get('kind') or ('live' if x.get('直播') else '')
    syssel, _ = classify(api)          # classify 返回 (体系, 可用体系list)

    wanted = {
        '名称': nm,
        'host': host_of(api),
        '分组类目': cat_of(nm, kind, api),
        'api接口': api,
        '源类型': core_src_type(api),
        '体系': syssel,
        '状态': '待查',
    }
    if batch_tag:
        wanted['进库批次'] = batch_tag

    rec = {}
    dropped = []
    for k, v in wanted.items():
        if schema is not None and k not in schema:
            dropped.append(f'{k}(schema 无此字段)')
            continue
        spec = (schema or {}).get(k, {'type': 'text'})
        w = _wrap(v, spec, k, dropped)
        if w is not None:
            rec[k] = w
    if dropped:
        print(f'    ⚠️ {nm}: 丢弃字段 {"; ".join(dropped)}')
    rec['_api'] = api        # 内部用：原始 api 字符串（写库前 strip_internal 剥掉）
    return rec


def api_of(rec):
    """取记录的 api 字符串（对内是 _api，对外是 {'text': ...} 包装）。"""
    if '_api' in rec:
        return rec['_api']
    v = rec.get('api接口')
    return v.get('text', '') if isinstance(v, dict) else str(v or '')


def strip_internal(rec):
    """去掉下划线开头的内部字段 + 空值，得到可直接入库的 payload。"""
    return {k: v for k, v in rec.items() if not k.startswith('_') and v is not None}


def core_src_type(api):
    """schema 的 `源类型` 只有两个合法值：`csp线路` / `py线路`（实测自 get_database_schema）。

    注意：jar 系线路的 api 也是 `http(s)://...?db=xxx`，在 schema 里同样归 `py线路`
    （`py线路` 指"走 py 引擎取数"，jar 是另一维度，由 `jar依赖` 字段承载）。
    """
    return 'csp线路' if api.startswith('csp_') else 'py线路'


def _text(rec, field):
    v = rec.get(field)
    return (v.get('text') or '') if isinstance(v, dict) else str(v or '')


def _write_errors(db, payload, max_show=5):
    """复跑一次批量写，只为拿 results[].error 原文（失败时才有必要，最多一批）。

    代价是这批会再被拒一次（不会被写入，因为本来就是非法记录），
    但换到的是**根因原文**，比让下一个人重新手工炮制一遍划算。
    """
    script = f'{core.LIB}/database/batch_add_database_records.py'
    try:
        out = subprocess.run([sys.executable, script, '--database-id', db,
                              '--records', json.dumps(payload[:100], ensure_ascii=False)],
                             capture_output=True, text=True, cwd=core.LIB).stdout
        d = json.loads(out)
    except Exception as e:
        return [f'（取 error 原文失败：{e}）']
    lines = []
    for x in d.get('results', []):
        if x.get('success'):
            continue
        i = x.get('index', -1)
        nm = _text(payload[i], '名称') if 0 <= i < len(payload) else '?'
        lines.append(f'[{i}] {nm}: {x.get("error") or "(无 error 字段)"}')
    if not lines:
        lines.append(f'无 results 明细；原始返回：{str(d)[:300]}')
    if len(lines) > max_show:
        lines = lines[:max_show] + [f'…另 {len(lines) - max_show} 条同类错误']
    return lines


def dedup(recs):
    """两层去重：api 精确（唯一键）+ 名称|host 近似（同名同站视为重复）。"""
    out, seen_api, seen_nh = [], set(), set()
    dup_api = dup_nh = 0
    for r in recs:
        a = api_of(r)
        if a in seen_api:
            dup_api += 1
            continue
        key = (_text(r, '名称').lower(), _text(r, 'host').lower())
        if key in seen_nh:
            dup_nh += 1
            continue
        seen_api.add(a)
        seen_nh.add(key)
        out.append(r)
    return out, dup_api, dup_nh


def blacklisted(recs, tmpdir='/tmp'):
    """批量查黑名单。返回 (拦截的 api 集合, 是否降级)。

    ★ 按 blk.py 真实签名调用：它是**文件进、文件出**（`guard --candidates F`
      先落 /tmp/guard_pass.json 与 /tmp/guard_block.json），没有 `--apis` 参数。
      S111 第一版按 `--apis` 写，跑起来直接 argparse 报错。

    复用 blk.guard 而不自己实现判据（M-R38）。调用失败时**如实返回降级标记**，
    不折叠成"全部放行"（M-R60 三态：读失败 ≠ 没有黑名单）。
    """
    if not os.path.exists(BLK):
        return set(), True
    cf = os.path.join(tmpdir, 'build_candidates.json')
    json.dump([{'api接口': api_of(r), '名称': _text(r, '名称')} for r in recs],
              open(cf, 'w', encoding='utf-8'), ensure_ascii=False)
    try:
        p = subprocess.run(['python3', BLK, 'guard', '--candidates', cf],
                           capture_output=True, text=True, timeout=300)
        bf = os.path.join(tmpdir, 'guard_block.json')
        if not os.path.exists(bf):
            return set(), True
        blocked = json.load(open(bf, encoding='utf-8'))
        return {str(x.get('api接口') or '') for x in blocked}, False
    except Exception:
        return set(), True


def fetch_existing(db):
    rows, cur = [], None
    for _ in range(50):
        c = ['python3', QUERY, '--database-id', db, '--page-size', '200']
        if cur:
            c += ['--start-cursor', cur]
        o = subprocess.run(c, capture_output=True, text=True).stdout
        i = o.find('{')
        if i < 0:
            break
        d = json.loads(o[i:])
        rows += d.get('results') or []
        nxt = d.get('next_cursor')
        if not d.get('has_more') or not nxt:
            break
        cur = nxt
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-c', '--candidates', required=True)
    ap.add_argument('--db', default='线路源库')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--batch', default='', help='进库批次标记（默认自动生成）')
    a = ap.parse_args()

    if not os.path.exists(a.candidates):
        print(f'❌ 候选文件不存在：{a.candidates}')
        return 2
    raw = json.load(open(a.candidates, encoding='utf-8'))
    if isinstance(raw, dict):
        raw = raw.get('records') or raw.get('kept') or raw.get('candidates') or []
    if a.limit:
        raw = raw[:a.limit]
    print(f'▌候选 {len(raw)} 条 ← {a.candidates}')

    db = dict(core.SRC_DBS).get(a.db)
    if not db:
        print(f'❌ 未知库 {a.db}，可选 {[n for n, _ in core.SRC_DBS]}')
        return 2

    batch = a.batch or time.strftime('S%m%d_%H%M', time.gmtime())
    sc = schema_of(db)
    if sc is None:
        print('❌ 无法读取库 schema，放弃本次入库（避免整条被拒）')
        return 2
    recs = [r for r in (normalize(x, batch, sc) for x in raw) if r]
    print(f'  规范化后 {len(recs)} 条（丢弃无 api {len(raw) - len(recs)} 条）')

    # 黑名单拦截（先于去重/入库，M-R115 顺序铁律）
    blocked, degraded = blacklisted(recs)
    if degraded:
        print('  ⚠️ 黑名单查询不可用 → 本次未拦截（无法判定，不等于无黑名单）')
    if blocked:
        recs = [r for r in recs if api_of(r) not in blocked]
        print(f'  黑名单拦截 {len(blocked)} 条')

    recs, d_api, d_nh = dedup(recs)
    print(f'  去重后 {len(recs)} 条（api 重复 {d_api} / 名称+host 重复 {d_nh}）')

    tri = Triage(f'出包 → {a.db}')
    tri.done('规范化', n=len(raw), note=f'有效 {len(recs)}')
    if blocked:
        tri.done('黑名单拦截', n=len(blocked), note='已归档失效库')
    if degraded:
        tri.undecidable('黑名单拦截', why='blk.py 不可用，本次未做拦截判定，不代表无黑名单')
    tri.done('去重', n=d_api + d_nh, note=f'api {d_api} / 名称host {d_nh}')

    if not recs:
        print('\n无待入库记录')
        print(tri.report())
        return 0

    print('  分组类目分布:', dict(Counter(r['分组类目']['select'] for r in recs)))
    print('  体系分布:', dict(Counter(r['体系']['select'] for r in recs)))

    if a.dry:
        print('\n（--dry 预演，未入库）')
        for r in recs[:3]:
            print('  ', json.dumps(strip_internal(r), ensure_ascii=False)[:150])
        print(tri.report())
        return 0

    # 入库前查已有，做最后一道 api 去重（库内快照为准，防并发写入重复）
    print('\n  读库内现有记录做最后一道 api 去重...')
    existing = {str(r.get('api接口') or '').strip() for r in fetch_existing(db)}
    before = len(recs)
    recs = [r for r in recs if api_of(r) not in existing]
    if before != len(recs):
        print(f'  库内已存在 {before - len(recs)} 条，跳过')
    if not recs:
        print('  全部已在库中')
        # 三态必须齐全：没有这一步也要显式标"已完成(0条)"，
        # 否则报告只剩两行，看起来像漏了一步（读者无法区分"跳过"和"忘做"）。
        tri.done('入库并读回确认', n=0, note='全部已在库中，本次无写入')
        print()
        print(tri.report())
        return 0

    print(f'\n  开始入库 {len(recs)} 条 → {a.db}（{db}）')
    payload = [strip_internal(r) for r in recs]
    # 入库前自查：每条记录必须每个字段都是 dict（否则平台整条拒绝，且 add_records 返回 0/0）
    bad = [i for i, r in enumerate(payload) if any(not isinstance(v, dict) for v in r.values())]
    if bad:
        print(f'  ❌ 记录格式异常（非 dict 字段）：{bad[:5]}，中止')
        return 2
    ok, fail = core.add_records(db, payload)
    print(f'  写入结果：成功 {ok} / 失败 {fail}')
    if fail:
        # 平台退货时 results 里带 error 原文（invalid fields / 选项不合法 等）。
        # 不复现一遍就看不到根因，所以这里打出来，别让下次还得手工重跑。
        for line in _write_errors(db, payload):
            print(f'    ✗ {line}')
    if ok == 0 and fail == 0 and payload:
        print('  ⚠️ add_records 返回 (0,0)：通常意味着平台整批拒绝且未回 results。')

    # 写后读回核对（平台铁律：不信 success=true）
    print('  读回核对...')
    after = {str(r.get('api接口') or '').strip() for r in fetch_existing(db)}
    hit = sum(1 for r in recs if api_of(r) in after)
    print(f'    库内命中 {hit}/{len(recs)}')

    if fail or hit < len(recs):
        tri.pending('部分记录未确认入库', why=f'写失败 {fail}，读回缺失 {len(recs) - hit}')
    else:
        tri.done('入库并读回确认', n=hit, note='api 全部可查')
    print()
    print(tri.report())

    if ok:
        print(f'\n  提示：本次对源库有写操作，收工需 `tvbox snapshot` 刷新权威快照')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
