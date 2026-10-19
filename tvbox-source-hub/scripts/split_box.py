#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
box_bh 配置接口深度拆分：读取 parse_tvbox_config.py 提取的 clean JSON，
把 sites + lives 拆成 ingest 候选，写入 /tmp/to_add_box.json，
随后由 link.py ingest（L7 管线）统一走「黑名单拦截 → 放行入库 / 命中归档失效库」。

分类依据沿用 split_jsm.py 的 RULES / group_of（规则 49：名称关键词优先 → 兜底按 api 形态）。
"""
import json, re
from collections import Counter

CLEAN = '/tmp/box_bh.clean.json'
OUT = '/tmp/to_add_box.json'

# 与 split_jsm.py 保持同一套分组规则（规则 49 单一权威）
RULES = [
    ('直播',       r'直播|网络直播'),
    ('体育',       r'体育|看球|球通|瓜子体育|咖啡体育|919|球类|球星'),
    ('动漫',       r'动漫|漫国|方舟|曼波|咕咕|巴士|路漫漫|去看吧|Anime|NT动漫|新番'),
    ('音乐听书',   r'音乐|DJ|听书|广播|戏曲|相声|小品|FM|蜻蜓|有声|歌曲|演唱会'),
    ('网盘',       r'网盘|阿里|夸克|UC|百度|盘搜|天翼|趣盘|123|夸父|盘它'),
    ('搜索',       r'搜索|古哥|兄弟|百酷|米搜|鬼鬼|混合'),
    ('短剧',       r'短剧'),
    ('4K',         r'4K|至臻|多多|欧哥|二小|虎斑|玩偶|快映|木偶|蜡笔|指南|人人|即刻'),
    ('APP源',      r'APP|99|剧圈|双星|橘汁|华谊|苹果|薯条|久久|闪影|飞飞|云播|首发|金牌|老鹰|蓝鹰|猎豹|王子|秒看|茉莉|灵虎|韩圈|无极|听心|橙子|追番|顾我'),
    ('磁力',       r'磁力|荐片|修罗|七味|New6v|美剧迷|迅雷吧|电影港|6V'),
    ('解析接口',   r'解析'),
    ('官源JS',     r'官源'),
    ('影视',       r'影视|电影|影院|飞宇|奇乐|热播|三秋|独播|厂长|瓜子|云播|骚火|农民|爱看|小镇|面包|永乐|1905|哆啦|新闪雷|茶杯狐|TVB|ITalk|王二小|刑侦|蓝光|采集'),
    ('预告',       r'预告|新片'),
    ('系统内置',   r'配置|本地|应用商店|推送|手机'),
]

def group_of(name, api):
    n = name or ''
    for g, pat in RULES:
        if re.search(pat, n):
            return g
    if api.startswith('http'):
        return '外链采集'
    if api.startswith('csp_'):
        return '集成解析(csp)'
    if 'drpy' in api:
        return '官源JS(drpy)'
    if api.startswith('./py'):
        return 'PY脚本源'
    return '其他'

d = json.load(open(CLEAN, encoding='utf-8'))
sites = d.get('sites', [])
lives = d.get('lives', [])

cands = []
seen = set()
for s in sites:
    name = s.get('name') or s.get('key') or '未命名'
    api = s.get('api') or ''
    if not api:
        continue
    key = (name, api)
    if key in seen:
        continue
    seen.add(key)
    cands.append({'名称': name, 'api接口': api, '分组类目': group_of(name, api)})

for l in lives:
    name = '直播·' + (l.get('name') or '?')
    url = l.get('url') or ''
    if not url:
        continue
    key = (name, url)
    if key in seen:
        continue
    seen.add(key)
    cands.append({'名称': name, 'api接口': url, '分组类目': '直播'})

json.dump(cands, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

print(f'候选总数: {len(cands)}')
print('分组类目分布:')
for g, c in Counter(x['分组类目'] for x in cands).most_common():
    print(f'  {c:3}  {g}')
real = sum(1 for x in cands if x['api接口'].startswith('http'))
print(f'\n其中 真实外链(api以http开头): {real}  |  本地解析(csp/drpy/py/相对): {len(cands) - real}')
print(f'已写 {OUT}')
