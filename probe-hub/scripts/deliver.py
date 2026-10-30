#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deliver.py —— 大集成消费优选投递（探源司新增板块·一期）

读成品源库 DB_PROD（已真实测活通过 + 已 quality.py 质量打分），
按分组类目过滤、质量分降序，挑 top N 最优 cms 接口地址，
产出可导入 TVBox 的订阅清单 + 冒烟测试入口。

边界（诚实）：
  · 本命令只做「读库优选 + 产出导入清单」，离线可靠，不依赖沙箱联网搜片；
  · 产出的是「最优 cms 接口地址」，用户导入 TVBox 即拥有可搜可看的大集成源；
  · 「实时按片名搜 + 自动切源」是二期（需壳端集成或沙箱联网搜），本期不做。
"""
import sys
import json

sys.path.insert(0, '/root/.codebuddy/skills/probe-hub/scripts')
sys.path.insert(0, '/root/.codebuddy/skills/tvbox-source-hub/scripts')
sys.path.insert(0, '/root/.codebuddy/skills/library')
from core import rows_of  # noqa

DB_PROD = '8JBrhSwsJ3pnPYtoGLo9XV'

TYPE_ALIAS = {
    '影视': '影视', '电影': '影视', '追剧': '影视', '剧': '影视',
    '音乐': '音乐', '听歌': '音乐',
    '直播': '直播', '电视': '直播',
    '小说': '小说', '读书': '小说',
}


def _to_int(v):
    try:
        return int(float(v))
    except Exception:
        return 0


def _cat_match(rec_cat, cat):
    """类目匹配：影视放宽（空类目默认影视，cms 采集源绝大多数为影视）；其余严格匹配。"""
    rec_cat = str(rec_cat or '')
    if cat == '影视':
        return rec_cat in ('', '影视')
    return rec_cat == cat


def _health_ok(rec):
    """只递健康源：真实测活通过 + 状态正常。"""
    if str(rec.get('真实测活', '')) != '通过':
        return False
    if str(rec.get('状态', '')) != '正常':
        return False
    return True


def run(a):
    top = getattr(a, 'top', 3) or 3
    ttype = getattr(a, 'type', '影视') or '影视'
    cat = TYPE_ALIAS.get(ttype, ttype)
    as_json = getattr(a, 'json', False)

    rows = rows_of(DB_PROD)
    if not rows:
        print('⚠️ 成品源库读取为空，无法投递（疑似云端通道暂不可达）')
        return

    cands = []
    for r in rows:
        if not _health_ok(r):
            continue
        if not _cat_match(r.get('分组类目'), cat):
            continue
        score = _to_int(r.get('质量分'))
        cands.append((score, r))

    if not cands:
        print(f'⚠️ 类目「{cat}」下暂无健康源（成品库健康源共 {sum(1 for r in rows if _health_ok(r))} 条，但无匹配类目）')
        return

    cands.sort(key=lambda x: -x[0])
    cands = cands[:top]

    if as_json:
        items = [{
            '名称': r.get('名称'),
            '质量分': sc,
            '最高画质': r.get('最高画质') or '未知',
            '广告判定': r.get('广告判定') or '未知',
            'api接口': r.get('api接口'),
            '分组类目': r.get('分组类目') or '',
        } for sc, r in cands]
        print(json.dumps({'type': cat, 'count': len(items), 'items': items},
                          ensure_ascii=False, indent=2))
        return

    print(f'🎯 大集成优选投递｜类目={cat}｜健康源中挑前 {len(cands)} 优（共筛 {sum(1 for r in rows if _health_ok(r) and _cat_match(r.get("分组类目"), cat))} 条候选）')
    print('=' * 64)
    for i, (sc, r) in enumerate(cands, 1):
        name = r.get('名称', '?')
        q = r.get('最高画质') or '未知'
        ad = r.get('广告判定') or '未知'
        api = r.get('api接口', '')
        print(f'{i}. {name} ｜质量分 {sc} ｜画质 {q} ｜广告 {ad}')
        print(f'   🔗 接口：{api}')
    print('-' * 64)
    if cands:
        print(f'👉 冒烟测试：先把第 1 条「{cands[0][1].get("名称")}」接口导入 TVBox，')
        print('   搜一个热门片，能搜到 + 能播 = 你的盒子合适，其余同理。')
        print('📋 使用：把上面接口地址填进 TVBox「配置 / 接口地址」，即可搜片看片。')
    print('=' * 64)
    print('说明：本命令只做「读库优选 + 产出导入清单」（离线可靠）。')
    print('实时按片名搜 + 自动切源为二期（需壳端集成 / 沙箱联网搜）。')


if __name__ == '__main__':
    class _A:
        pass
    a = _A()
    a.top = 3
    a.type = '影视'
    a.json = False
    run(a)
