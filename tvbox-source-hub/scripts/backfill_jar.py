#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backfill_jar.py —— 补全 jar / spider 包级字段（S111）。

## 为什么有这个脚本

源库 schema 里有 `jar依赖` / `jar文件名` / `jar地址` / `py依赖` / `平台形态` 五个字段，
但实测填充率：

    jar依赖     0/620      ← 从未写过
    jar文件名  241/620      ← 早期写过，重构后链路断了
    jar地址     0/620      ← 从未写过
    py依赖      0/620      ← 从未写过
    平台形态    0/620      ← 从未写过

**字段存在 ≠ 字段有值**。jar 溯源做不了，根子在这里。

## 为什么没有 `spider` 字段（重要结论）

原计划"采集补 spider 字段"，核实后发现**粒度不对**：

    spider 是「配置文件级」概念（一个 json 一个 spider 地址）
    线路源库是「线路级」粒度（620 行 = 620 条线路）

一条线路无法回答"它的 spider 是什么"——同一个包里 50 条线路共用一个 spider。
硬塞进去会得到 620 份重复值，且首行写谁全看运气。故**不建 spider 字段**，
改为在线路行上记 `jar文件名`（= 该线路所属包），spider 归属留给底包库。

## 判定规则（只用可自证的）

| 类名后缀 | jar | 依据 |
|---|---|---|
| `*Guard` | mojiGuard.jar | 社区公认包名，183/183 命中 |
| `*Amns` | amns.jar | 社区公认包名，58/58 命中 |
| 其他 267 种 | **留空 + 标"待验"** | `PanUC`/`App99` 各家混用，猜错比空着更糟 |

S110 已用 `link.jar_file()` 表达过同一结论："猜错比空着更糟（空着=待验，猜错=误导）"。

用法：
    python3 backfill_jar.py --dry          # 预演，只打印统计
    python3 backfill_jar.py                # 真实写入
    python3 backfill_jar.py --db 线路源库
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402
from concurrency import TIER_API, run_bounded  # noqa: E402
from guard import Triage, expect_sec, heartbeat  # noqa: E402

LIB = core.LIB
QUERY = os.path.join(LIB, 'database', 'query_database_record.py')
UPDATE = os.path.join(LIB, 'database', 'batch_update_database_records.py')

# 可自证的 jar 归属（只收社区公认包名）
JAR_RULES = [
    (re.compile(r'Guard$'), 'mojiGuard.jar'),
    (re.compile(r'Amns$'), 'amns.jar'),
]


def jar_of(api):
    """csp 类名 → jar 文件名。判不出返回 ('', '待验')。"""
    api = (api or '').strip()
    if not api.startswith('csp_'):
        return '', '非csp'
    cls = api[4:]
    for pat, jar in JAR_RULES:
        if pat.search(cls):
            return jar, '类名后缀'
    return '', '待验'


def py_dep_of(api):
    """py 线路 → 依赖形态。

    ★ 判断顺序不能反（S111 踩过）：先判"本地"会把
      `http://host/py/x.py` 这种远程地址误判成本地——
      因为远程 URL 也常以 `.py` 结尾。先认 scheme，再认相对路径。
    """
    low = (api or '').strip().lower()
    if low.startswith('csp_'):
        return ''
    if low.startswith(('http://', 'https://')):
        if low.endswith('.py') or '.py?' in low:
            return '远程py'
        if low.endswith(('.js', '.jsm')) or 'drpy' in low:
            return '远程js'
        return ''
    if low.startswith(('./py/', '../py/', 'py/')):
        return '本地py'
    if low.endswith('.py'):
        return '本地py'
    return ''


# 平台形态的合法取值（必须与源库 select options 完全一致，否则平台拒写）
# 实测 options：CMS采集 / m3u8直链 / js源码 / py源码 / php源码 / JSON配置 / XML配置 / 其他
PLATFORM_FORMS = ('CMS采集', 'm3u8直链', 'js源码', 'py源码', 'php源码',
                  'JSON配置', 'XML配置', '其他')


def platform_form_of(api):
    """平台形态：映射到源库 select 的 8 个合法选项之一，判不出返回 '其他'。

    ★ 取值必须落在 PLATFORM_FORMS 内。S111 第一版自造了 `csp类`/`远程py`/`http直链`
      三个值，全部不在 options 里——直接写会被平台判 invalid fields。
      这正是 M-R115④「三态收尾」要防的：宁可落 '其他'，不要造不存在的档位。
    """
    low = (api or '').strip().lower()
    if not low:
        return ''
    if low.startswith('csp_'):
        return 'JSON配置'          # csp 类线路由 json 配置驱动
    if low.startswith(('http://', 'https://')):
        if low.endswith('.m3u8') or '.m3u8?' in low:
            return 'm3u8直链'
        if low.endswith('.py') or '.py?' in low:
            return 'py源码'
        if low.endswith(('.js', '.jsm')):
            return 'js源码'
        if 'api.php' in low or '/api.php' in low:
            return 'CMS采集'
        if '/provide/vod' in low:
            return 'CMS采集'
        return '其他'
    if low.startswith(('./py/', '../py/', 'py/')) or low.endswith('.py'):
        return 'py源码'
    return '其他'


def fetch_all(db):
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


def plan_updates(rows):
    """算出每行要补的字段。只补空值，不覆盖已有（幂等 + 不篡改）。

    ★ S111 修：两个 jar 字段的分工（上一版把同一个文件名写进两处，241 行完全重复）：
        · `jar依赖`   —— 记**有无**：`是` / `否`。回答"这条线路出包要不要带 jar"。
        · `jar文件名` —— 记**具体文件名**：`mojiGuard.jar` / `amns.jar`。回答"带哪个"。
      为什么要分开：出包时先按 `jar依赖` 筛出要带 jar 的线路（一个 select 级别的判断），
      再按 `jar文件名` 去凑文件。两个字段都存文件名时，第一层筛选取不到值。
      认不出 jar 家族（`jar_of` 返回空）时 `jar依赖` 仍写 `是`（csp 一律依赖 jar，
      见 link.classify docstring），`jar文件名` 留空表示"依赖 jar 但未知是哪个"。
    """
    ups = []
    for r in rows:
        api = str(r.get('api接口') or '')
        rid = r.get('record_id')
        if not rid:
            continue
        props, changed = {}, []
        jar, why = jar_of(api)
        needs_jar = api.startswith('csp_')      # csp 一律依赖 jar（含标准 jar）

        # jar依赖：有 jar 依赖就写"是"，明确无依赖写"否"（不留空，空值无法区分"否"和"没算"）
        # ⚠️ 该字段 schema 类型是 text（不是 select），写成 {'select':...} 会被平台整条拒绝
        cur_dep = str(r.get('jar依赖') or '').strip()
        want_dep = '是' if needs_jar else '否'
        if cur_dep != want_dep:
            props['jar依赖'] = {'text': want_dep}
            changed.append('jar依赖')

        # jar文件名：只在认得出家族时写，认不出留空（空=待验，猜错比空着糟）
        if jar and not str(r.get('jar文件名') or '').strip():
            props['jar文件名'] = {'text': jar}
            changed.append('jar文件名')

        pd = py_dep_of(api)
        if pd and not str(r.get('py依赖') or '').strip():
            props['py依赖'] = {'text': pd}
            changed.append('py依赖')

        pf = platform_form_of(api)
        if pf and not str(r.get('平台形态') or '').strip():
            props['平台形态'] = {'select': pf}
            changed.append('平台形态')

        if changed:
            ups.append({'record_id': rid, 'properties': props, '_fields': changed,
                        '_api': api, '_why': why})
    return ups


_SCHEMA = {}


def schema_types(db):
    """读库 schema → {字段名: type}。**读不到返回 None**（不是 {}）。

    ★ M-R60：`{}` 有歧义——既可能是"这个库真没字段"，也可能是"我没读到"。
      而 `type_gate()` 拿 `{}` 会走"schema 读取失败，跳过校验"，等于放行。
      两者必须分开：返回 None = 无法判定（闸门必须中止），返回 {} = 库真没字段（异常，也中止）。
    """
    if db in _SCHEMA:
        return _SCHEMA[db]
    script = f'{LIB}/database/get_database_schema.py'
    try:
        out = subprocess.run([sys.executable, script, '--database-id', db],
                             capture_output=True, text=True, cwd=LIB).stdout
        d = json.loads(out)
    except Exception:
        return None
    props = d.get('properties')
    if not isinstance(props, list):
        return None
    s = {p['name']: p.get('type') for p in props}
    if s:
        _SCHEMA[db] = s
    return s


def type_gate(db, ups):
    """★ 写库前类型闸门：每个字段的 {type: value} 键必须与 schema 一致。

    这条闸门的来历很实在：S111 修 `jar依赖` 时我顺手写成 {'select': '是'}，
    而该字段 schema 是 text —— 平台对这种记录的处理是**整条拒绝**，错误码还把它
    和"字段不存在"混在一起报（invalid fields ... failed to parse），极难反查。
    既然规则是"类型必须查 schema"，就不能靠自觉，要在这里卡死。
    """
    st = schema_types(db)
    if not st:
        # None = 没读到；{} = 读到了但库无字段。两者都不能当"检查通过"，
        # **一律中止**（宁可不写，也不写一条可能被整批拒掉的记录）。
        print('  ❌ schema 不可用（读取失败或库无字段），**中止写入**——不拿它当"校验通过"')
        return False
    bad = []
    for u in ups:
        for f, v in u['properties'].items():
            if f not in st:
                bad.append(f'{u["_api"][:40]} 字段 {f} 不在 schema')
                continue
            if not isinstance(v, dict):
                bad.append(f'{u["_api"][:40]} {f} 值不是 dict')
                continue
            key = next(iter(v), '')
            if key != st[f]:
                bad.append(f'{u["_api"][:40]} {f} 用了 {key!r}，schema 是 {st[f]!r}')
    if bad:
        print(f'  ❌ 类型闸门拦截 {len(bad)} 处（整条记录会被平台拒绝）：')
        for b in bad[:8]:
            print(f'     {b}')
        return False
    return True


def write_batch(db, batch):
    """写一批（每批 ≤100，走 batch_update）。"""
    payload = [{'record_id': u['record_id'], 'properties': u['properties']} for u in batch]
    p = subprocess.run(['python3', UPDATE, '--database-id', db,
                        '--records', json.dumps(payload, ensure_ascii=False)],
                       capture_output=True, text=True, timeout=120)
    ok = fail = 0
    try:
        i = (p.stdout or '').find('{')
        d = json.loads(p.stdout[i:]) if i >= 0 else {}
        for x in d.get('results', []):
            ok += 1 if x.get('success') else 0
            fail += 0 if x.get('success') else 1
    except Exception:
        fail = len(batch)
    return ok, fail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='线路源库')
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args()

    db = dict(core.SRC_DBS).get(a.db)
    if not db:
        print(f'❌ 未知库 {a.db}，可选 {[n for n, _ in core.SRC_DBS]}')
        return 1

    print(f'▌{a.db}（{db}）')
    rows = fetch_all(db)
    print(f'  读入 {len(rows)} 行')

    ups = plan_updates(rows)
    from collections import Counter
    field_cnt = Counter()
    for u in ups:
        for f in u['_fields']:
            field_cnt[f] += 1
    print(f'  待补 {len(ups)} 行')
    for f, n in field_cnt.most_common():
        print(f'    {f}: {n}')

    # 判定分布（含待验，用于三态收尾）
    jar_dist = Counter()
    for r in rows:
        j, why = jar_of(str(r.get('api接口') or ''))
        jar_dist[why] += 1
    print('  判定依据分布：', dict(jar_dist))

    tri = Triage(f'{a.db} jar/py 字段补全')
    tri.done('类名后缀可判（Guard/Amns）', n=jar_dist.get('类名后缀', 0),
             note='写 jar依赖=是 + jar文件名')
    tri.undecidable('其余 csp 类名', n=jar_dist.get('待验', 0),
                    why='PanUC/App99 等通用类名各家混用，不解 dex 无法判定归属；'
                        'jar依赖 仍记"是"（csp 一律依赖 jar），jar文件名 留空待验')
    tri.done('非 csp（py/直链）', n=jar_dist.get('非csp', 0),
             note='写 py依赖 + 平台形态')
    print()
    print(tri.report())

    if a.dry:
        print('\n（--dry 预演，未写入）')
        for u in ups[:3]:
            print(f"  样例 {u['_api'][:40]} → {u['_fields']}")
        type_gate(db, ups)      # 预演也过闸门，让问题在写之前暴露
        return 0

    if not type_gate(db, ups):
        print('  已中止，未写入任何记录')
        return 2

    if not ups:
        print('\n无需补全')
        return 0

    print(f'\n开始写入（{len(ups)} 行，每批 100）...')
    batches = [ups[i:i + 100] for i in range(0, len(ups), 100)]
    est = expect_sec(len(batches), 2.0, workers=a.workers)
    with heartbeat(total=len(batches), label='补全写入') as hb:
        def work(b):
            r = write_batch(db, b)
            hb.tick()
            return r
        rep = run_bounded(batches, work, tier=TIER_API, workers=a.workers,
                          label='jar补全', expect=est)
    ok = sum(r[0] for r in rep.results)
    fail = sum(r[1] for r in rep.results)
    print(f'\n  写入完成：成功 {ok} / 失败 {fail}    {rep.summary()}')

    # 写后读回核对（M-R115 禁四 + 平台铁律：不信 success=true）
    print('  读回核对...')
    after = fetch_all(db)
    got_jar = sum(1 for r in after if str(r.get('jar文件名') or '').strip())
    got_pf = sum(1 for r in after if str(r.get('平台形态') or '').strip())
    print(f'    jar文件名 有值 {got_jar}/{len(after)}（补前 241）')
    print(f'    平台形态 有值 {got_pf}/{len(after)}（补前 0）')
    return 0 if fail == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
