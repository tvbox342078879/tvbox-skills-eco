#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quality_batch.py —— 把 quality.py 接到两库上（S110 闭环第三段）。

链路位置：
  livecheck（能不能拉到播放地址）→ **quality_batch（拉到了好不好用）** → build（出包）

职责：读库 → 取播放地址 → quality.evaluate → 回填 6 个质量字段。

★ 为什么分两步而不塞进 livecheck：
  livecheck 每条只发 1~3 个请求，快，可全量跑；
  质量评测每条要拉主清单 + 子清单 + 分片×rounds（约 5~9 个请求），慢 10 倍。
  硬塞一起会让全量测活从 10 分钟涨到 2 小时，且质量失败会污染"测活结论"。
  两件事失败语义不同：测活失败=源死；质量失败=这条源画质/速度差，源还活着。

★ 播放地址从哪来（三档，逐档降级）：
  1) 库里已有（成品源库部分条 `测活证据` 里带 m3u8）→ 直接抽
  2) CMS 采集源 → 调 ?ac=videolist&ids= 拿首条 vod_play_url 的第一个分片
  3) 抽不到 → 标「无法判定·无播放地址」，**不算失败**（三态纪律）
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, '/root/.codebuddy/skills/probe-hub/scripts')
sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')

import quality  # noqa: E402
from core import SRC_DBS, http_get  # noqa: E402
sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from concurrency import run_bounded, TIER_WEB, TIERS  # noqa: E402  M-R114 统一入口

LIB = '/root/.codebuddy/skills/library'
UA = quality.UA

# 成品源库 / 线路源库 的库 id
DB = dict(SRC_DBS)

# 质量字段名（两库同名，写库时按名字传）
QF = {
    'q': '最高画质', 'ms': '首片耗时', 'kb': '下载速率',
    'ad': '广告判定', 'score': '质量分', 'ev': '质量证据',
}


def _curl_json(url, timeout=15):
    """CMS 取数 → JSON 或 None。底层统一走 core.http_get（C1：收敛取数入口）。"""
    st, payload, _ = http_get(url, timeout=timeout, ua=UA, as_text=True)
    if st == 'ok' and payload:
        try:
            return json.loads(payload)
        except Exception:
            return None
    return None


def _clean_play_url(raw):
    """把 CMS 的 play_url 片段洗成单条 URL。

    ★ 格式坑（S110 实测）：vod_play_url 里一条长这样
        https://hn.bfvvs.com/play/bkRwOnNa$$$全集完结$https://hn.bfvvs.com/play/bkRwOnNa/index.m3u8
      结构是 `剧集名$$$分组$真实地址`，`$` 是分隔符不是 URL 的一部分。
      直接拿正则匹配 http...m3u8 会把前面那截原样带进来，末尾粘上 `$$$全集完结$`，
      请求必然 404 —— 会被误判成"源挂了"。

    策略：按 `$` 切段后**逐个试**，优先取 `.m3u8` 结尾的；
    都不以 m3u8 结尾就取最后一段合法的 http（可能是 mp4 或 share 页）。
    """
    if not raw:
        return ''
    s = raw.strip().replace('\\/', '/')
    cands = [p.strip() for p in s.split('$') if p.strip().startswith('http')]
    if not cands:
        return ''
    for c in reversed(cands):
        if '.m3u8' in c.lower():
            return c
    return cands[-1]


def play_url_of(api, evidence=''):
    """取一条可评的播放地址 → (url, 来源说明)。拿不到返 ('', 原因)。"""
    api = (api or '').strip()

    # 档1：证据里已有 m3u8（上轮 livecheck 留下的）
    ev = (evidence or '').replace('\\/', '/')
    for m in re.finditer(r'https?://[^\s#$"\']+?\.m3u8', ev):
        u = _clean_play_url(m.group(0))
        if u:
            return u, '测活证据'

    # 档2：http 直链本身就是 m3u8
    if api.startswith('http') and '.m3u8' in api.lower():
        return _clean_play_url(api) or api, 'api即m3u8'

    # 档3：CMS 采集源 → 拿最新一条的播放地址
    if api.startswith('http') and ('provide/vod' in api or api.endswith('/')):
        base = api if api.endswith('/') else api + '/'
        j = _curl_json(base + '?ac=videolist')
        if not j or not isinstance(j.get('list'), list):
            j = _curl_json(base + '?ac=detail')
        lst = (j or {}).get('list') or []
        for it in lst[:3]:
            pu = it.get('vod_play_url') or ''
            for part in pu.split('#'):
                u = _clean_play_url(part)
                if u:
                    return u, f"CMS首条·{it.get('vod_name', '')[:8]}"
        return '', 'CMS 未取到播放地址'

    if api.startswith('csp_'):
        return '', 'csp 源需壳子内解析（沙箱无 spider 环境）'
    if api.startswith('./'):
        return '', '本地随身文件源，不适用'
    return '', '非可评形态'


def fetch_rows(db, page_size=200):
    """全量读库。★ 分页参数是 `--start-cursor`，不是 `--cursor`——
    写错不会报错，脚本会一直读首页，陷入"每页都读 200 条"的假循环（S110 踩过）。"""
    rows, cursor = [], None
    while True:
        cmd = [sys.executable, f'{LIB}/database/query_database_record.py',
               '--database-id', db, '--page-size', str(page_size)]
        if cursor:
            cmd += ['--start-cursor', cursor]
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=LIB, timeout=90)
        try:
            d = json.loads(out.stdout)
        except Exception:
            print(f'  ⚠ 读失败，中断（已读 {len(rows)} 条）')
            break
        rows += d.get('results', [])
        nxt = d.get('next_cursor')
        if not d.get('has_more') or not nxt or nxt == cursor:
            break
        cursor = nxt
        time.sleep(0.1)
    return rows


def write_quality(db, updates, batch=50):
    """批量回写质量字段。S158：改走 core.update_records（统一三态）。

    保留分批 + 限速：一次 500 条会被平台限流，分批是**业务需要**，不是历史包袱。
    """
    ok = fail = 0
    for i in range(0, len(updates), batch):
        chunk = updates[i:i + batch]
        recs = [{'record_id': rid, 'properties': props} for rid, props in chunk]
        try:
            import core as _c
            _uok, n, _info = _c.update_records(db, recs, _gate=False)
            if n:
                ok += n
            if not _uok:
                fail += len(chunk) - n
        except Exception as e:
            print(f'   ⚠️ 回写批次失败（{type(e).__name__}: {str(e)[:60]}）')
            fail += len(chunk)
        time.sleep(0.25)
    return ok, fail


def props_of(r):
    """评测结果 → typed properties 字典。空值不写（避免把已有数据刷掉）。"""
    p = {}
    if r.get('quality') and r['quality'] != '未知':
        p[QF['q']] = {'text': r['quality']}
    if r.get('first_ms'):
        p[QF['ms']] = {'text': f"{r['first_ms']}ms"}
    if r.get('kbps'):
        p[QF['kb']] = {'text': f"{r['kbps']}KB/s"}
    if r.get('ad') and r['ad'] != '未知':
        p[QF['ad']] = {'text': r['ad']}
    # ★ S196：score=None 表示"画质不可测·不评分"，**不写库**（旧值若存在则保留，
    #   因为已有分数可能来自更早一次能测出画质的评测）。写空串会污染数值列。
    if r.get('score') is not None:
        p[QF['score']] = {'text': str(r['score'])}
    if r.get('evidence'):
        p[QF['ev']] = {'text': r['evidence'][:400]}
    return p


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', choices=['成品源库', '线路源库', 'both'], default='成品源库')
    ap.add_argument('--limit', type=int, default=0, help='只测前 N 条（0=全量）')
    ap.add_argument('--only-pass', action='store_true', help='只测真实测活=通过的')
    ap.add_argument('--rounds', type=int, default=2)
    ap.add_argument('--timeout', type=int, default=12)
    ap.add_argument('--workers', type=int, default=6, help='并发数（M-R114 B档上限 6）')
    ap.add_argument('--dry', action='store_true', help='只打印不写库')
    # C8（T6）：argv=None 时兼容旧直跑；probe.py 总调度显式传 argv
    a = ap.parse_args(argv)

    targets = [('成品源库', DB['成品源库']), ('线路源库', DB['线路源库'])] \
        if a.db == 'both' else [(a.db, DB[a.db])]

    for name, db in targets:
        rows = fetch_rows(db)
        print(f'\n== {name} 读到 {len(rows)} 条 ==')
        if a.only_pass:
            rows = [r for r in rows if r.get('真实测活') == '通过']
            print(f'   仅 真实测活=通过 → {len(rows)} 条')
        if a.limit:
            rows = rows[:a.limit]

        updates, stat = [], {'通过': 0, '失败': 0, '无法判定': 0}
        lock = threading.Lock()

        def work(r):
            api = r.get('api接口') or ''
            url, src = play_url_of(api, r.get('测活证据', ''))
            if not url:
                with lock:
                    stat['无法判定'] += 1
                    done[0] += 1
                    print(f'  [{done[0]}/{len(rows)}] —  {r.get("名称", "")[:16]:18s} {src}', flush=True)
                return (r['record_id'], {QF['ev']: {'text': f'质量未测：{src}'}})
            res = quality.evaluate(url, timeout=a.timeout, rounds=a.rounds)
            with lock:
                stat[res['verdict']] = stat.get(res['verdict'], 0) + 1
                done[0] += 1
                _sc = '  —' if res['score'] is None else f'{res["score"]:3d}'
                print(f'  [{done[0]}/{len(rows)}] {res["verdict"]:4s} {_sc}  '
                      f'{r.get("名称", "")[:16]:18s} {res["evidence"][:64]}', flush=True)
            return (r['record_id'], props_of(res))

        done = [0]
        # ★ M-R114：走统一入口。质量评测打的是公网 CDN，归 B 档（上限 6）；
        #   本批要写的 record_id 一并声明，若另一任务正在写同一批会被直接拦住。
        rep = run_bounded(rows, work, tier=TIER_WEB, workers=min(a.workers, TIERS[TIER_WEB]['max']),
                          label=f'质量评测·{name}',
                          write_set={r['record_id'] for r in rows} if not a.dry else None)
        updates = rep.results
        print(f'  {rep.summary()}')
        if rep.errors:
            print(f'  ⚠ 单条异常 {rep.fail_count} 条（不拖垮全批，已跳过）')

        print(f'  —— 通过 {stat["通过"]} / 失败 {stat["失败"]} / 无法判定 {stat["无法判定"]}')
        if a.dry:
            print('  (dry，未写库)')
            continue
        ok, fail = write_quality(db, updates)
        print(f'  ✅ 回填 {ok} 条 / ❌ {fail} 条')


if __name__ == '__main__':
    main()
