#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 TVBox 聚合包解出的 api/live.json 抽取直播源，生成带溯源字段的候选样本。

点③（live.json 溯源结构范本）+ 点④（90 条直播 sourceUrl 批量 livecheck）联动脚本。

live.json 每条结构（已实勘）：
    name / file / date / size / source / ua / url(镜像) / sourceUrl(原始上游直链)

抽取映射（探源溯源范本）：
    url       ← sourceUrl   # 测活目标 = 原始上游直链（探源核心：上游活不活）
    mirror    ← url         # 镜像站地址（moliys.icu 聚合镜像）
    sourceUrl ← sourceUrl   # 原始上游直链（与 url 同源，备查）
    ua        ← ua          # 请求 UA
    size      ← size        # 包大小
    source    ← source      # 来源分组（作者线名）
    date      ← date        # 上游更新日期

输出：
    harvest_candidates/livecheck_batch_666.json
    结构 [{name, url, kind, type, sourceUrl, mirror, ua, size, source, date, 分组类目, 生态类别}]
    —— kind='m3u8' 供 livecheck 真测活；溯源四字段随行透传。
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HARVEST_DIR = os.path.join(SCRIPT_DIR, 'harvest_candidates')
DEFAULT_IN = '/tmp/pak_out/api/live.json'
DEFAULT_OUT = os.path.join(HARVEST_DIR, 'livecheck_batch_666.json')


def _kind_of(url):
    u = (url or '').lower()
    if '.m3u8' in u or '.m3u' in u:
        return 'm3u8'
    if u.endswith('.php') or u.endswith('.txt') or u.endswith('.json'):
        # 聚合分发站常用脚本/文本端点吐 playlist，按直播拉流测
        return 'm3u8'
    return 'm3u8'


def extract(inp, outp):
    if not os.path.exists(inp):
        print(f'❌ 输入不存在：{inp}')
        return 1
    d = json.load(open(inp, encoding='utf-8'))
    items = d.get('items') if isinstance(d, dict) else d
    if not isinstance(items, list):
        print(f'❌ 无法解析 items（顶层类型 {type(d).__name__}）')
        return 1
    out = []
    for it in items:
        src_url = (it.get('sourceUrl') or '').strip()
        mirror = (it.get('url') or '').strip()
        # 探源取原始上游直链；若缺 sourceUrl 退化用镜像
        test_url = src_url or mirror
        if not test_url:
            continue
        rec = {
            'name': it.get('name', ''),
            'api接口': test_url,             # 候选主键（harvest 链路必填，= 上游直链）
            'url': test_url,                 # 测活目标（livecheck 链路）
            'kind': _kind_of(test_url),
            'type': 'm3u8',
            'sourceUrl': src_url,            # 原始上游直链
            'mirror': mirror,                # 镜像站地址
            'ua': (it.get('ua') or '').strip(),
            'size': (it.get('size') or '').strip(),
            'source': (it.get('source') or '').strip(),   # 来源分组
            'date': (it.get('date') or '').strip(),       # 上游更新日期
            'file': (it.get('file') or '').strip(),
            '分组类目': '直播',
            '生态类别': 'tvbox接口',          # 直播属 TVBox 范围，进册
        }
        out.append(rec)
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    json.dump(out, open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'✅ 抽取 {len(out)} 条直播源（带溯源字段）→ {outp}')
    # 溯源字段覆盖率自检
    for col in ('sourceUrl', 'mirror', 'ua', 'size'):
        n = sum(1 for r in out if r.get(col))
        print(f'   {col} 非空：{n}/{len(out)}')
    return 0


if __name__ == '__main__':
    inp = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IN
    outp = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT
    sys.exit(extract(inp, outp))
