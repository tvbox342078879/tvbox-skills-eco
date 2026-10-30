#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""danmu_pick.py —— danmu-api 实例选型打分（S196，backlog #29 落码）。

判据优先级（S196 实测验证）：
  1. /api/config 能读 → version 越新越好
  2. SOURCE_ORDER 长度（官方默认只 4 源；≤5 基本没配，能搜到的番很少）
  3. CUSTOM_MERGE_RULES 非空（有人在持续维护别名库）
  4. 响应速度（最后才看）

用法：
  python3 danmu_pick.py <url1> <url2> ...      # 显式列表
  python3 danmu_pick.py --demo                 # 用 S196 测过的 7 个实例演示
"""
import argparse
import json
import re
import subprocess
import sys
import time
import concurrent.futures as cf

# 共享核心（M-R38 单一事实源 / M-R213 共享核心）：统一 HTTP 取数，含 SSRF/超时/三态
import sys as _sys
_sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
from core import http_get  # noqa: E402

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'

DEMO = [
    'https://logvar.hhht.cc.cd',
    'https://danmu.0000996.xyz',
    'https://dm.199333.xyz/dm',
    'https://dmfl.us.ci',
    'https://dandan.wenjian.de/wenjian',
    'https://logvardm.278612356.xyz/steven',
    'https://danmu.qianting168.com/456765847636987622146901',
]


def _curl(url, timeout=15):
    """取内容 → (text, sec)。底层统一走 core.http_get（C1：收敛取数入口）。"""
    t0 = time.time()
    st, payload, _ = http_get(url, timeout=timeout, ua=UA, as_text=True)
    if st == 'ok' and payload:
        return payload, time.time() - t0
    return '', time.time() - t0


def _version_key(v):
    m = re.match(r'(\d+)\.(\d+)\.(\d+)', str(v or ''))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def probe(url):
    row = {'url': url, 'ver': '-', 'ver_num': (0, 0, 0), 'src': 0, 'merge': False, 'ms': 9999, 'score': 0, 'note': ''}
    root = re.match(r'^(https?://[^/]+)', url).group(1)
    body, sec = _curl(root + '/api/config')
    row['ms'] = int(sec * 1000)
    if not body.lstrip().startswith('{'):
        row['note'] = 'config 不可读'
        return row
    try:
        j = json.loads(body)
    except Exception:
        row['note'] = 'config 非 JSON'
        return row
    envs = j.get('envs') or {}
    row['ver'] = str(j.get('version', '-'))
    row['ver_num'] = _version_key(row['ver'])
    order = envs.get('SOURCE_ORDER') or []
    if isinstance(order, str):
        order = [x for x in order.split(',') if x]
    row['src'] = len(order)
    row['merge'] = bool(str(envs.get('CUSTOM_MERGE_RULES') or '').strip())
    return row


def score(r):
    if r['ver_num'] == (0, 0, 0):
        return 0
    s = 40                                       # config 可读即 40 基础分
    s += min(r['ver_num'][0] * 6 + r['ver_num'][1] * 2 + r['ver_num'][2], 24)  # 版本最高 24
    s += min(r['src'], 17)                       # 源数最高 17（每源 1 分）
    s += 9 if r['merge'] else 0                  # 维护别名库 +9
    s += 7 if r['ms'] < 1500 else (4 if r['ms'] < 4000 else 0)  # 速度最高 7
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('urls', nargs='*')
    ap.add_argument('--demo', action='store_true')
    a = ap.parse_args()
    urls = DEMO if a.demo else a.urls
    if not urls:
        print(__doc__)
        sys.exit(2)
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(probe, urls))
    for r in rows:
        r['score'] = score(r)
    rows.sort(key=lambda r: (-r['score'], r['ms']))
    print(f"{'分':>4}  {'版本':8} {'源数':>3} {'维护':3} {'ms':>6}  地址")
    for r in rows:
        print(f"{r['score']:4d}  {r['ver']:8} {r['src']:3d} {'是' if r['merge'] else '—':3} {r['ms']:6d}  {r['url']}"
              + (f"   ({r['note']})" if r['note'] else ''))
    print('\n判据：config 可读 40 + 版本≤24 + 源数每源 1 分(≤17) + 别名库 9 + 速度≤7')


if __name__ == '__main__':
    main()
