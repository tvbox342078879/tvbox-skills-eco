#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_sample_size.py —— 标定 CALIB_SAMPLE 该取几条（S111）。

上一轮发现：3 条样本算出的 per_item 在 0.10~0.36s 间跳，导致预估偏差 -58%~+57%。
本脚本对同一批 URL 反复采样，比较不同样本量下的估计稳定性（用变异系数 CV）。

判据：CV < 15% 视为稳定。
"""
import statistics
import sys
import time

sys.path.insert(0, '.')
from guard import expect_sec  # noqa: E402

URLS = [
    'https://www.baidu.com', 'https://www.qq.com', 'https://www.163.com',
    'https://www.taobao.com', 'https://www.jd.com', 'https://www.bilibili.com',
    'https://www.sina.com.cn', 'https://www.sohu.com', 'https://www.zhihu.com',
    'https://www.aliyun.com', 'https://www.tencent.com', 'https://www.360.cn',
]


def probe(url):
    import core                                   # ECO 对齐：走共享核心 http_get
    st, _, _ = core.http_get(url, timeout=10, max_bytes=2048)
    return st == 'ok'


def timed(url):
    t0 = time.time()
    probe(url)
    return time.time() - t0


def main():
    print('=== CALIB_SAMPLE 标定（同批 URL，5 次重复采样）===\n')
    urls = URLS[:8]
    # 先全部测一遍拿真实分布
    all_t = [timed(u) for u in urls for _ in range(3)]
    true_mean = statistics.mean(all_t)
    print(f'真值（24 次采样）：均值 {true_mean:.3f}s，中位 {statistics.median(all_t):.3f}s，'
          f'标准差 {statistics.stdev(all_t):.3f}s，CV {statistics.stdev(all_t) / true_mean:.0%}\n')

    print(f'{"样本量":>6} {"均值估计":>22} {"中位估计":>22}')
    for k in (3, 5, 7):
        means, meds = [], []
        for rep in range(5):
            s = [timed(u) for u in urls[:k]]
            means.append(statistics.mean(s))
            meds.append(statistics.median(s))
        cv_m = statistics.stdev(means) / statistics.mean(means) if len(means) > 1 else 0
        cv_d = statistics.stdev(meds) / statistics.mean(meds) if len(meds) > 1 else 0
        print(f'{k:>6}  μ={statistics.mean(means):.3f}s CV={cv_m:>5.0%}  '
              f'μ={statistics.mean(meds):.3f}s CV={cv_d:>5.0%}')

    print('\n判据：CV<15% 稳定。若 3 条不够，则取首个 CV<15% 的样本量。')
    print('另注：样本应**跳过第一条**（DNS/连接池冷启动，系统性偏慢）。')


if __name__ == '__main__':
    main()
