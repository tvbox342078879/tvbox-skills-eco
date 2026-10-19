#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_final.py —— M-R115 定稿前终验（S111）。

用真实网络验证最终实现：calibrate（中位数+跳首条）→ expect_sec → 实际跑。
目标是偏差 ≤30%（优秀线），且心跳行数 ≤8。
"""
import statistics
import sys
import time

sys.path.insert(0, '.')
from guard import calibrate, expect_sec, heartbeat  # noqa: E402
from concurrency import TIER_WEB, run_bounded  # noqa: E402

URLS = [
    'https://www.baidu.com', 'https://www.qq.com', 'https://www.163.com',
    'https://www.taobao.com', 'https://www.jd.com', 'https://www.bilibili.com',
    'https://www.sina.com.cn', 'https://www.sohu.com', 'https://www.zhihu.com',
    'https://www.aliyun.com', 'https://www.tencent.com', 'https://www.360.cn',
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


def main():
    print('=== M-R115 定稿终验（真实网络）===\n')
    print(f'{"规模":>5} {"校准μ":>8} {"预估":>8} {"实际":>8} {"偏差":>8} {"心跳行":>7}  判定')
    devs = []
    for n in (12, 18, 36, 54):
        urls = (URLS * 5)[:n]
        per, used = calibrate(urls, probe)
        if per is None:
            print(f'{n:>5}  校准失败，跳过')
            continue
        est = expect_sec(n, per, workers=6)
        import io
        buf = io.StringIO()
        t0 = time.time()
        with heartbeat(total=n, label='f', stream=buf) as hb:
            def w(u):
                r = probe(u)
                hb.tick()
                return r
            rep = run_bounded(urls, w, tier=TIER_WEB, workers=6, label='f')
        act = time.time() - t0
        lines = len(buf.getvalue().strip().split('\n'))
        dev = (est - act) / act if act else 0
        devs.append(abs(dev))
        flag = '✓' if abs(dev) <= 0.30 else ('○ 可接受' if abs(dev) <= 0.50 else '⚠')
        print(f'{n:>5} {per:>7.3f}s {est:>7.2f}s {act:>7.2f}s {dev:>7.0%} {lines:>7}  {flag}')

    if devs:
        print(f'\n平均绝对偏差 {statistics.mean(devs):.0%}，最大 {max(devs):.0%}')
        print('判据：≤30% 优秀 / ≤50% 可用 / STALL_FACTOR=3 需 200% 才误停 → 安全余量充足')


if __name__ == '__main__':
    main()
