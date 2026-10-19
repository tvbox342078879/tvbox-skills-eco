#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_estimate.py —— 只用真实数据验证 expect_sec 的准确性（S111）。

前两次实测被「故意估小 10 倍」的扰动干扰了判断，看不清模型本身准不准。
本脚本只做一件事：用 3 条样本得到真实 per_item，再比较预估 vs 实际。
跑 5 轮，看偏差分布。
"""
import statistics
import sys
import time

sys.path.insert(0, '.')
from guard import expect_sec, heartbeat  # noqa: E402
from concurrency import TIER_WEB, run_bounded  # noqa: E402

URLS = [
    'https://www.baidu.com', 'https://www.qq.com', 'https://www.163.com',
    'https://www.taobao.com', 'https://www.jd.com', 'https://www.bilibili.com',
    'https://www.sina.com.cn', 'https://www.sohu.com', 'https://www.zhihu.com',
    'https://www.aliyun.com', 'https://www.tencent.com', 'https://www.360.cn',
    'https://www.ctrip.com', 'https://www.meituan.com', 'https://www.douban.com',
    'https://www.iqiyi.com', 'https://www.youku.com', 'https://www.mgtv.com',
]


def probe(url):
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read(2048)
        return True
    except Exception:
        return False


def one_round(n, workers=6):
    urls = (URLS * 3)[:n]
    # 3 条样本 → 真实 per_item（串行）
    t0 = time.time()
    for u in urls[:3]:
        probe(u)
    per = (time.time() - t0) / 3

    est = expect_sec(n, per, workers)
    t0 = time.time()
    rep = run_bounded(urls, probe, tier=TIER_WEB, workers=workers, label='verify')
    actual = time.time() - t0
    return per, est, actual, rep.ok_count


def main():
    print('=== expect_sec 准确性验证（真实网络，5 轮 × 3 种规模）===\n')
    print(f'{"规模":>5} {"样本均":>8} {"预估":>8} {"实际":>8} {"偏差":>9}  判定')
    worst = 0.0
    for n, workers in ((12, 6), (18, 6), (36, 6)):
        for r in range(2):
            per, est, act, ok = one_round(n, workers)
            dev = (est - act) / act if act else 0
            worst = max(worst, abs(dev))
            flag = '✓' if abs(dev) <= 0.5 else ('⚠ 偏保守' if dev > 0 else '⚠ 偏乐观')
            print(f'{n:>5} {per:>7.2f}s {est:>7.2f}s {act:>7.2f}s {dev:>8.0%}  {flag}')
    print(f'\n最大偏差 {worst:.0%}（判据：≤50% 可用，≤30% 优秀）')
    print('注：STALL_FACTOR=3.0 意味着偏差到 200% 才误停，50% 偏差完全在安全区。')


if __name__ == '__main__':
    main()
