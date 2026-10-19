#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""calibrate_guard.py —— 用实测数据标定 M-R115 的三个决策点（S111）。

第一版模型算错了：只比「多跑 3 条」的开销，得出阈值 620（荒谬）。
错在哪：校准的价值**不是省时间，是提前发现工期判断错了**。
所以本版用「期望收益」模型：

    收益 = P(估算错) × 止损省下的时间
    成本 = 3 条样本耗时

真实场景里 P(估算错) 不低——S111 实测沙箱测速 P50 只有 225KB/s，
而我开工时按「正常家宽 2MB/s」估，错了约 9 倍。那次若先跑 3 条样本，
20 秒就能发现，而不是跑到 4 分钟零产出。
"""
import io
import math
import sys

sys.path.insert(0, '.')
from guard import CALIB_SAMPLE, STALL_FACTOR, expect_sec, heartbeat  # noqa: E402


def calib_cost(per_item):
    """校准固定成本：3 条样本串行（不能并发，要测单条真实延迟）。"""
    return CALIB_SAMPLE * per_item


def stoploss_saving(n, assumed_per, actual_per, workers):
    """不校准时白跑的时间：全量跑到 STALL_FACTOR 才停，白烧的那些。"""
    est_assumed = expect_sec(n, assumed_per, workers)
    limit = est_assumed * STALL_FACTOR
    # 校准时：3 条样本就发现，立即停 → 白跑 3 条
    naive_waste = limit
    calib_waste = calib_cost(actual_per)
    return max(naive_waste - calib_waste, 0)


def main():
    print('=== M-R115 决策点标定（S111 第二版：期望收益模型）===\n')

    # ---------- 决策点1：校准阈值 ----------
    # 用真实案例标定：S111 沙箱实测 P50=225KB/s，开工按 2MB/s 估（错 9 倍）
    workers = 6
    assumed = 1.0      # 开工假设 1s/条
    actual = 9.0       # 实测 9s/条（错 9 倍，S111 真实比例）

    print('[决策点1] 校准阈值：按「期望收益 > 成本」找分界点')
    print(f'  （模型：假设 {assumed}s/条，实测 {actual}s/条，错 {actual/assumed:.0f} 倍）')
    print(f'{"条目数":>8} {"校准成本":>10} {"不校准白烧":>12} {"净收益":>10}  {"结论":>6}')
    threshold = None
    for n in (3, 5, 10, 15, 20, 30, 50, 100, 620):
        c = calib_cost(actual)
        saving = stoploss_saving(n, assumed, actual, workers)
        net = saving - c
        verdict = '校准' if net > 0 else '免'
        if net > 0 and threshold is None:
            threshold = n
        print(f'{n:>8} {c:>9.1f}s {saving:>11.1f}s {net:>9.1f}s  {verdict:>6}')

    print(f'\n  → 期望收益模型给出 CALIB_MIN_N = {threshold}')
    print('  但 n 很小时「白烧时间」也小（本来就跑得快），净值虽正但绝对值 <3s，')
    print('  此时校准反而是过度设计。取 max(收益分界, 绝对值下限) →')

    # 绝对值下限：白烧时间绝对值 ≥ 30s 才值得专门校准
    abs_floor = None
    for n in range(3, 2000):
        if stoploss_saving(n, assumed, actual, workers) >= 30:
            abs_floor = n
            break
    print(f'  白烧 ≥30s 的条目数下限 = {abs_floor}')
    final = max(threshold or 0, abs_floor or 0)
    print(f'  ⇒ 建议 CALIB_MIN_N = {final}')

    # 稳健性：即使估算只错 2 倍，这个阈值是否仍成立
    print('\n  稳健性检验（估算错 2 倍 / 5 倍 / 9 倍）：')
    for ratio in (2, 5, 9):
        a = assumed * ratio
        floor = None
        for n in range(3, 5000):
            if stoploss_saving(n, assumed, a, workers) >= 30:
                floor = n
                break
        print(f'    错 {ratio} 倍 → 需校准起点 n={floor}')

    # ---------- 决策点2：心跳密度 ----------
    print('\n[决策点2] 心跳 every：按「静默时长」而非「条数」定')
    print('  问题：every 按条数设，但条的耗时差异极大（0.2s ~ 9s），条数不等于时长')
    print(f'{"every":>7} {"620条@0.2s":>12} {"620条@1s":>11} {"620条@9s":>11}  {"评价":>8}')
    total = 620
    for every in (10, 25, 50, 100, 200):
        gaps = [every * per for per in (0.2, 1.0, 9.0)]
        worst = max(gaps)
        verdict = '过密' if worst < 5 else ('合适' if worst <= 60 else '偏疏')
        print(f'{every:>7} {gaps[0]:>11.1f}s {gaps[1]:>10.1f}s {gaps[2]:>10.1f}s  {verdict:>8}')
    print('\n  → every 不该写死条数。改为「按时间触发」：距上次心跳 ≥2s 才打，')
    print('    且至少每 5% 进度打一次。这样 0.2s/条 和 9s/条 都自然合适。')

    # ---------- 决策点3：STALL_FACTOR ----------
    print('\n[决策点3] STALL_FACTOR 敏感度')
    print(f'{"真/预":>7} {"2.0":>7} {"3.0":>7} {"5.0":>7}')
    for k in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0):
        r = ['停工' if k > f else '继续' for f in (2.0, 3.0, 5.0)]
        print(f'{k:>7.1f} {r[0]:>7} {r[1]:>7} {r[2]:>7}')
    print('  2.0 太敏感（首条慢启动、限流退避都会触发误停）；5.0 太钝（真卡住也不救）')
    print('  → 保留 3.0')


if __name__ == '__main__':
    main()
