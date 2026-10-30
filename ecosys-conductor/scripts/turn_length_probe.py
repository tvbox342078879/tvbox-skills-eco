#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""turn_length_probe.py —— 回合长度探针（P045 / M-R146 的可执行判据）

## 为什么要这个探针
M-R146 立了「单回合完成独立阶段即落回合回报」，但它的 `probe.command` 指向
`p044_concurrency_probe.py` 并声称「回合链长度可见」——**那个脚本根本没有回合统计**。
这是「登记了探针但实现不完整」的形态，与 T28 的 M-R106 虚挂同族。

M-R146 要判的是一个**可直接计数**的量：一个回合里连续发了多少条工具调用、
中间有没有落过回报。这种量天生就能机器算，不该停在「提醒 + 文本体检」。

## 判据
回合边界 = `type=='message' and role=='user'`（一条用户输入开启一个回合）。
回合长度 = 该用户消息之后、下一条用户消息之前，助手发出的 `function_call` 条数。

分档（对齐 M-R146 原文「连续约 8 次调用即该落回合」）：
  🔴 长回合：单回合工具调用 ≥ LONG（默认 12），且**中途无助手文本消息**
            （无落回报 = 用户确实插不进去）
  🟡 待观察：≥ LONG 但中途有文本（说明落了回报，只是分段多），不拉红
  ⚪ 正常  ：< LONG

为什么要区分「有无中途文本」：M-R146 管的是「用户能不能插话」，
而能不能插话取决于**有没有落回报**，不是单纯看调用条数。只看条数会重犯
P044 那次的错——**用容易量的代理指标替代真正要测的东西（M-R134）**。

退出码：存在红档 → 1；否则 0。

用法：
  python3 turn_length_probe.py                 # 默认只看「立规日之后」（M-R146 created）
  python3 turn_length_probe.py <file.jsonl>
  python3 turn_length_probe.py --full          # 打印全部回合明细
  python3 turn_length_probe.py --since 0       # 不限时间（含历史，T31 实测 13 条全在立规前）
  python3 turn_length_probe.py --since 2026-10-20   # 指定分界日

  ★ 为什么用「立规日」而不是「最近 N 天」：
    T31 实测 13 个红档回合**全部落在 10-15 ~ 10-21 00:15**，而 M-R146 是 10-22 才立的。
    滑动窗口（如近 3 天）有两个毛病：
      · 窗口会随时间推移把老账又卷进来（改代码不改历史，明天又红）
      · 窗口也可能把「立规后第 4 天新出的违规」漏掉
    **规则生效之前的账归历史，生效之后的新增才是要治的**——分界线是规则的立规日，
    不是一拍脑袋的 N 天。这正是 P044 踩过的坑：一个永远红的指标会训练人无视它。
"""
import glob
import json
import os
import sys
import time

DEFAULT_GLOB = '/root/.codebuddy/projects/workspace/*.jsonl'
LONG = 12
SHOW = 10
# M-R146 created = 2026-10-22（见 rules_registry.json）
DEFAULT_SINCE = '2026-10-22'


def scan(path, since_ms=0):
    """返回回合列表 [(起始行号, 工具调用数, 中途助手文本数, 起始时间戳)]"""
    turns = []
    cur_calls = 0
    cur_texts = 0
    cur_line = None
    cur_ts = None
    started = False
    with open(path, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            if '"type"' not in line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            t = rec.get('type')
            role = rec.get('role')
            if t == 'message' and role == 'user':
                if started:
                    turns.append((cur_line, cur_calls, cur_texts, cur_ts))
                cur_calls = 0
                cur_texts = 0
                cur_line = i
                cur_ts = rec.get('timestamp')
                started = True
            elif started and t == 'function_call':
                cur_calls += 1
            elif started and t == 'message' and role == 'assistant':
                cur_texts += 1
    if started:
        turns.append((cur_line, cur_calls, cur_texts, cur_ts))
    if since_ms:
        turns = [x for x in turns if not x[3] or int(x[3]) >= since_ms]
    return turns


def main(argv):
    full = '--full' in argv
    since_day = DEFAULT_SINCE
    paths = []
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == '--since' and i + 1 < len(argv):
            since_day = argv[i + 1]
            i += 2
            continue
        if a.startswith('-'):
            i += 1
            continue
        paths.append(a)
        i += 1
    since = 0
    if since_day not in ('0', ''):
        try:
            since = int(time.mktime(time.strptime(since_day, '%Y-%m-%d')) * 1000)
        except ValueError:
            print(f'⚠️ --since 日期格式应为 YYYY-MM-DD，收到 {since_day!r}，按不限处理')
            since = 0
    if not paths:
        paths = sorted(glob.glob(DEFAULT_GLOB), key=os.path.getmtime)[-1:]
    if not paths:
        print('⚠️ 未找到会话转录文件')
        return 0

    red_any = False
    for p in paths:
        turns = scan(p, since)
        if not turns:
            print(f'⚠️ 近 {since_day} 天内无回合记录，无法判定')
            return 0
        long_turns = [(ln, c, t) for ln, c, t, _ts in turns if c >= LONG]
        red = [(ln, c, t) for ln, c, t in long_turns if t == 0]
        yel = [(ln, c, t) for ln, c, t in long_turns if t > 0]
        avg = sum(c for _l, c, _t, _ts in turns) / len(turns)
        span = f'{since_day} 之后' if since else '全量'
        print(f'📄 {os.path.basename(p)}（{span}）：{len(turns)} 个回合，'
              f'平均 {avg:.1f} 条调用/回合，最长 {max(c for _l, c, _t, _ts in turns)} 条')
        print(f'   🔴 长回合且无落回报（≥{LONG} 条调用）: {len(red)}')
        print(f'   🟡 长回合但有落回报             : {len(yel)}')
        show = [(ln, c, t) for ln, c, t, _ts in turns] if full else red
        for ln, c, t in show[:SHOW]:
            g = '🔴' if (c >= LONG and t == 0) else ('🟡' if c >= LONG else '⚪')
            print(f'      {g} 行 {ln}: {c} 条调用 / {t} 次文本回报')
        if len(show) > SHOW:
            print(f'      …另有 {len(show)-SHOW} 条（--full 查看）')
        if red:
            red_any = True
            print(f'   ↑ 这 {len(red)} 个回合连续 ≥{LONG} 条调用且中途一次没落回报——'
                  f'用户物理上插不进去（M-R146）')

    if red_any:
        print('❌ 存在「长回合且零回报」：完成独立阶段即落回报，把窗口交还用户（M-R146）')
        return 1
    print('✅ 未见「长回合且零回报」——长但落了回报的回合属正常分段')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))

