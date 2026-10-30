#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""domain_cluster.py —— 成品源库按域名/品牌聚类，找同源多点失效组（S196）。

用法：python3 domain_cluster.py            # 读 DB_PROD（112 条）
判据：域名级（host 完全相同）+ 品牌级（标签去数字取后两段，
      ffzy5.tv→ffzy.tv、cj/api.ffzyapi.com→ffzyapi.com）。
用途：一个品牌下任一入口失效 → 整组标记「疑同源失效」，停止逐条重试。
"""
import re
import sys
from collections import defaultdict

sys.path.insert(0, '/root/.codebuddy/skills/probe-hub/scripts')
sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
sys.path.insert(0, '/root/.codebuddy/skills/library')

from core import SRC_DBS, rows_of  # noqa

db_id = '8JBrhSwsJ3pnPYtoGLo9XV'   # DB_PROD（core.SRC_DBS[0][1]）
rows = rows_of(db_id)
print('读库成功，条数:', len(rows))


def host_of(rec):
    api = ''
    for k in ('api', '接口', '接口地址', 'url', '地址'):
        v = rec.get(k)
        if isinstance(v, str) and v.startswith('http'):
            api = v
            break
    if not api:
        for k, v in rec.items():
            if isinstance(v, str) and v.startswith('http'):
                api = v
                break
    m = re.match(r'https?://([^/:]+)', api or '')
    return m.group(1).lower() if m else '?'


groups = defaultdict(list)
for r in rows:
    name = r.get('名称') or r.get('name') or '?'
    groups[host_of(r)].append(str(name)[:24])

multi = {h: names for h, names in groups.items() if h != '?' and len(names) > 1}
print('\n== 域名级同域组 ==')
for h in sorted(multi, key=lambda x: -len(multi[x])):
    print(f'  {len(multi[h]):2d} 条  {h:34s}  {"、".join(multi[h][:4])}{"…" if len(multi[h]) > 4 else ""}')

# 品牌级聚类：标签去数字 + 拼接（ffzy.tv / ffzy5.tv → ffzy.tv；cj/api.ffzyapi.com → ffzyapi.com）
def brand(host):
    if host == '?':
        return '?'
    labels = [re.sub(r'\d+', '', lb) for lb in host.split('.')]
    if len(labels) >= 2:
        return '.'.join(labels[-2:])
    return host

bg = defaultdict(list)
for h, names in groups.items():
    bg[brand(h)].extend(names)
bmulti = {b: ns for b, ns in bg.items() if b != '?' and len(ns) > 1}
print('\n== 品牌级同源组（真失效连带面）==')
for b in sorted(bmulti, key=lambda x: -len(bmulti[x])):
    print(f'  {len(bmulti[b]):2d} 条  {b:34s}  {"、".join(bmulti[b][:5])}{"…" if len(bmulti[b]) > 5 else ""}')

print('\n单域名组:', sum(1 for h in groups if h != '?' and len(groups[h]) == 1), '个')
print('无域名(非http):', len(groups.get('?', [])), '条')
