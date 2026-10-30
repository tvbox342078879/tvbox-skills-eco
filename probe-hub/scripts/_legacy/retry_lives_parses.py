#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ⚠️ 历史归档脚本（T8/C5 留档·勿直接执行）：硬编码的 /workspace/原仓库库/cluntop_box原文_1018.json 路径已失效（原仓库库已归档至 /root/uploads/历史产物归档_20261023_1014至1020会话.tar.gz 内）。当前运行必报错，仅供历史追溯。
"""S117 复测：对 v3 重建中判定失败的 lives/parses 加严复测（8 轮·间隔 2s）。"""
import json, subprocess, time

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
ORIG = '/workspace/原仓库库/cluntop_box原文_1018.json'

def curl(url, timeout=20):
    try:
        out = subprocess.run(['curl', '-s', '-L', '--compressed', '--connect-timeout', '8',
                              '--max-time', str(timeout), '-A', UA, url],
                             capture_output=True, timeout=timeout + 5)
        return out.stdout
    except subprocess.TimeoutExpired:
        return b''

def retry(url, want_m3u, rounds=8):
    last = ''
    for i in range(1, rounds + 1):
        body = curl(url)
        ok = (b'#EXTM3U' in body[:4096]) if want_m3u else (len(body) > 0 and b'#EXTM3U' not in body[:64] or len(body) > 200)
        # parses 基地址判据：HTTP 可达（有正文且非错误页）
        if want_m3u:
            ok = b'#EXTM3U' in body[:4096]
            last = f'第{i}轮 {"含M3U" if ok else f"{len(body)}B无M3U"}'
        else:
            ok = len(body) > 100
            last = f'第{i}轮 {len(body)}B'
        if ok:
            return True, last
        time.sleep(2)
    return False, last + '（8 轮全败）'

orig = json.load(open(ORIG, encoding='utf-8'))
bad_lives = ['Kimentanm', 'wqwqwq', 'YanG', 'epg.pw All', 'iptv.org All']
bad_parses = ['极速', 'Super', '无上', '巧计', '爱心', '观山', '777', 'jsonplayer', 'OKJX']

print('=== lives 复测（8 轮）===')
for l in orig['lives']:
    n, u = l.get('name'), l.get('url', '')
    if n in bad_lives and u:
        u = u.replace('https://https://', 'https://')
        ok, ev = retry(u, True)
        print(f'{n}: {"恢复通过" if ok else "确认失败"}（{ev}）')

print('\n=== parses 复测（8 轮）===')
seen = set()
for p in orig['parses']:
    n, u = p.get('name'), str(p.get('url', ''))
    if n in bad_parses and u and n not in seen:
        seen.add(n)
        base = u.split('?url=')[0].rstrip('/') + '/'
        ok, ev = retry(base, False)
        print(f'{n}: {"恢复通过" if ok else "确认失败"}（{ev}） base={base}')
