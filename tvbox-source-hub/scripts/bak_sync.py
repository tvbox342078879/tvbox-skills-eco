#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
备用库同步（S155·补 P010 施工单）。

**为什么补这个**：`tvbox sizes` 长期显示「备用 0」。三态读确认**是真·空表**（不是读
失败折叠），而 link.py 正文早就写着「⚠️ 同步未接线（停更风险，P010 待 bak-sync）」——
备用库**建成即死：只有读展示、全生态 0 写入路径**（S075 定性）。

后果不是「少个备份」这么轻：源总库 801 条在跑，**无任何云端同构副本**。
S059 能从 537 行备用库回灌源库，恰恰说明备份的价值；现在这个位置是空的。

**字段差异必须适配**（S059 踩过：他处 select 类目「解析」被整条拒绝）：
  源库 状态      : select 正常/有广/待查/停用/**不适用**
  备用库 状态    : select 正常/有广/待查/停用        ← 无「不适用」
  源库 内容分级  : select
  备用库 内容分级: text                              ← 类型不同
  源库 可用体系  : multi_select                       ← 两库都有，但值来自 genres

**格式差异也必须注意**（本次实测二次踩）：
  batch_add    的 record = 字段字典本体，**不带 properties 包装**
  batch_update 的 record = {'record_id':..., 'properties':{...}}
  两者同构是错觉，套错就报「records[0] 校验后为空，无有效字段」——
  而这条错误信息**指错了方向**（听起来像字段类型不对，实际是层级错了）。

用法:
  python3 scripts/bak_sync.py --dry      # 只算不写
  python3 scripts/bak_sync.py --apply    # 真写入
"""
import json
import subprocess
import sys
import os
from collections import Counter

LIB = '/root/.codebuddy/skills/library/database'
# ★ S159：本脚本整体停用。
#   S144「阶段3 清空旧三库」把源总库 wW5xpbS… 与备用库 G9B1GzfR… 各 801 条清空并改名
#   「🗑【已废弃】」。此后本脚本仍在运行，效果是：从**空库**读源、把结果写进**废弃库**——
#   一份数据都同步不了，却仍挂着「备份」的名义，让人以为有备份（最坏的假安全感）。
#   三库互斥分区（M-R103）下也没有「同构备份库」这个角色；备份走 snapshot。
DB_SRC = 'wW5xpbS663nA6XK8a7DNsp'   # 旧源总库（已废弃，仅留作报错文案回指）
DB_BAK = 'G9B1GzfRJDoSausMmPXF8T'   # 旧备用库（已废弃，**禁止再写入**）

# 备用库「状态」无「不适用」——不能写非法枚举（会被 select 校验整条拒绝）
STATUS_MAP = {'不适用': '停用'}
# 备用库可用体系/内容分级等取值域差异，统一走字符串承载
TEXT_FIELDS_SRC = {'内容分级'}          # 源 select → 备用 text
TEXT_FIELDS = {'名称', 'host', 'api接口', '内容分级', '内容分类'}
SELECT_FIELDS = {'分组类目', '状态', '体系'}
DATE_FIELDS = {'入库时间'}
MULTI_FIELDS = {'可用体系'}


def q(db, page=100):
    cmd = ['python3', f'{LIB}/query_database_record.py', '--database-id', db,
           '--page-size', str(page)]
    cur, rows = None, []
    # ★ M-R115 禁二 / M-R100：原实现只看 has_more，若服务端一直回 has_more=true
    #   而 next_cursor 不变，就会无限循环且零报错（reclass2 栽过 4 分钟的同一个坑）。
    #   两道闸：游标不前进即退出 + 轮次硬上限。
    for _ in range(200):
        c = cmd + (['--start-cursor', cur] if cur else [])
        d = json.loads(subprocess.run(c, capture_output=True, text=True).stdout)
        rows += d.get('results', [])
        if not d.get('has_more'):
            break
        nxt = d.get('next_cursor')
        if not nxt or nxt == cur:
            break                      # 游标未前进 → 立即停，不空转
        cur = nxt
    return rows


def to_bak_record(r):
    """源库一行 → 备用库 record（**不带 properties 包装**，batch_add 的约定）。"""
    rec = {}
    for f in TEXT_FIELDS:
        v = str(r.get(f) or '')
        if v:
            rec[f] = {'text': v}
    for f in SELECT_FIELDS:
        v = str(r.get(f) or '')
        if f == '状态':
            v = STATUS_MAP.get(v, v)
        if v:
            rec[f] = {'select': v}
    for f in DATE_FIELDS:
        v = str(r.get(f) or '')
        if v:
            rec[f] = {'date': v}
    for f in MULTI_FIELDS:
        v = r.get(f)
        # 源库读出的是 "['A·CatVod标准系']" 这类字符串化列表，须还原成真列表
        if v:
            if isinstance(v, str):
                try:
                    lst = [x.strip().strip("'\"") for x in v.strip('[]').split(',') if x.strip()]
                except Exception:
                    lst = [v]
            else:
                lst = list(v)
            lst = [x for x in lst if x]
            if lst:
                rec[f] = {'multi_select': lst}
    return rec


def main(argv):
    dry = '--dry' in argv
    apply = '--apply' in argv
    if not (dry or apply):
        print('用法: bak_sync.py --dry | --apply')
        return 1
    # S159：硬停。即使显式 --apply 也拒绝，避免有人照着旧文档跑一次就把数据灌进废弃库。
    print('⏹ bak_sync 已停用（S159）：')
    print(f'   源库 {DB_SRC} 与备用库 {DB_BAK} 均已于 S144 清空并改名「🗑【已废弃】」。')
    print('   当前源库为：线路源库 / JSON库（见 core.SRC_DBS）。')
    print('   备份请用 snapshot（四极现状快照）。本脚本保留仅为可追溯，不再具备任何写入能力。')
    return 0

    src = q(DB_SRC)
    bak = q(DB_BAK)
    print(f'源库 {len(src)} 条 ｜ 备用库现有 {len(bak)} 条')

    exist = {str(r.get('api接口') or '') for r in bak}
    todo = [r for r in src if str(r.get('api接口') or '') and
            str(r.get('api接口')) not in exist]
    print(f'待同步 {len(todo)} 条（已存在跳过 {len(src) - len(todo)} 条）')

    # 预检：映射到备用库后的取值分布，非法枚举会当场暴露
    print('\n映射后取值分布:')
    for f in ('分组类目', '状态', '体系'):
        c = Counter(str(r.get(f, '')) for r in todo)
        print(f'  {f}: {dict(c.most_common(8))}')

    empty = [r for r in todo if not to_bak_record(r)]
    if empty:
        print(f'  ⚠️ {len(empty)} 条映射后无任何字段（跳过，不会写空记录）')

    if dry:
        print('\n[--dry] 未写入。样例 record:')
        for r in todo[:2]:
            print('  ' + json.dumps(to_bak_record(r), ensure_ascii=False)[:150])
        return 0

    # 真写入：分批 80，每批后计数
    ok = fail = 0
    for i in range(0, len(todo), 80):
        batch = [to_bak_record(r) for r in todo[i:i + 80]]
        batch = [b for b in batch if b]
        if not batch:
            continue
        payload = {'database_id': DB_BAK, 'records': batch}
        p = subprocess.run(['python3', f'{LIB}/batch_add_database_records.py', '--stdin'],
                           input=json.dumps(payload, ensure_ascii=False),
                           capture_output=True, text=True)
        try:
            res = json.loads(p.stdout or '{}').get('results', [])
            ok += sum(1 for x in res if x.get('success'))
            fail += sum(1 for x in res if not x.get('success'))
        except Exception:
            fail += len(batch)
            print(f'  批次 {i // 80 + 1} 解析失败: {p.stdout[:120]}')
    print(f'\n写入 成功 {ok} ｜ 失败 {fail}')

    # 写完必回读（T-I：success 不等于落库）
    after = q(DB_BAK)
    print(f'回读：备用库现有 {len(after)} 条')
    src_api = {str(r.get('api接口') or '') for r in src}
    bak_api = {str(r.get('api接口') or '') for r in after}
    miss = src_api - bak_api
    print(f'覆盖率 {len(src_api & bak_api)}/{len(src_api)} ｜ 缺口 {len(miss)}')
    if miss:
        print('  缺口样例:', list(miss)[:3])
    return 0 if not miss else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
