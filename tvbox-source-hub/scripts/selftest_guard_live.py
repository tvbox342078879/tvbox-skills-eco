#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest_guard_live.py —— M-R115 实测：真实网络任务下护栏是否真能提前发现问题。

不是 mock，是拿真 URL 跑。对比三组：
  A 无护栏：直接全量跑，不知进度、不知工期
  B 有护栏：expect_sec 预估 + heartbeat 心跳
  C 错误估算：故意把 per_item 估小 10 倍，看 check_stall 能否在合理时机叫停

目的：证明「禁一 + 禁二」不是纸面规则，而是真的把"卡了 4 分钟才发现"变成
     "20 秒就知道要出问题"。
"""
import sys
import time

sys.path.insert(0, '.')
from guard import (StalledTask, check_stall, expect_sec, heartbeat,
                   need_calibration)  # noqa: E402
from concurrency import TIER_WEB, run_bounded  # noqa: E402

# 用真实可达的 URL（公网、体积小、响应快）
URLS = [
    'https://www.baidu.com',
    'https://www.qq.com',
    'https://www.163.com',
    'https://www.taobao.com',
    'https://www.jd.com',
    'https://www.bilibili.com',
] * 2      # 12 条，够跑几轮


def probe(url):
    import urllib.request
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            n = len(r.read(4096))
        return {'url': url, 'ok': True, 'bytes': n, 'sec': time.time() - t0}
    except Exception as e:
        return {'url': url, 'ok': False, 'err': repr(e)[:60], 'sec': time.time() - t0}


def main():
    print('=== M-R115 真实网络实测 ===\n')

    # ---------- 先测 3 条样本，得到真实单条耗时 ----------
    print('[校准] 3 条样本（CALIB_SAMPLE）:')
    samples = [probe(u) for u in URLS[:3]]
    per_real = sum(s['sec'] for s in samples) / 3
    for s in samples:
        print(f"  {s['url'][:34]:34s} {'OK' if s['ok'] else 'FAIL':4s} {s['sec']:.2f}s")
    print(f'  → 实测均 {per_real:.2f}s/条\n')

    n = len(URLS)
    est_real = expect_sec(n, per_real, workers=6)
    print(f'[禁一] 按实测单条耗时预估：{n} 条 / 6 并发 → {est_real:.1f}s')
    print(f'  需校准阈值 {need_calibration(n)}（CALIB_MIN_N=100，{n} 条免校准）\n')

    # ---------- A 无护栏 ----------
    print('[A 无护栏] 直接全量跑:')
    t0 = time.time()
    a_rows = [probe(u) for u in URLS]
    a_t = time.time() - t0
    print(f'  串行全量 {a_t:.2f}s，成功 {sum(1 for r in a_rows if r["ok"])}/{n}\n')

    # ---------- B 有护栏 ----------
    print('[B 有护栏] expect_sec + heartbeat + 并发:')
    import io
    buf = io.StringIO()
    t0 = time.time()
    with heartbeat(total=n, label='探活', stream=buf) as hb:
        def wrapped(u):
            r = probe(u)
            hb.tick()
            return r
        rep = run_bounded(URLS, wrapped, tier=TIER_WEB, workers=6, label='探活')
    b_t = time.time() - t0
    print(f'  并发 {b_t:.2f}s，成功 {rep.ok_count}/{n}，提速 {a_t / max(b_t, 1e-6):.1f}x')
    hb_lines = buf.getvalue().strip().split('\n')
    print(f'  心跳输出 {len(hb_lines)} 行（stderr），示例：')
    for ln in hb_lines[:3]:
        print(f'    {ln}')
    print()

    # ---------- C 错误估算，看能否叫停 ----------
    print('[C 错误估算] 故意把 per_item 估小 10 倍:')
    wrong_per = per_real / 10
    est_wrong = expect_sec(n, wrong_per, workers=6)
    print(f'  错误预估 {est_wrong:.2f}s（真实约 {b_t:.2f}s，实际是预估的 {b_t / max(est_wrong, 1e-6):.1f} 倍）')
    try:
        check_stall(elapsed=b_t, expect=est_wrong, done=n, total=n, label='错误估算')
        print('  → 未触发停机')
        if b_t > est_wrong * 3:
            print('  ❌ 实际已超 3 倍却没拦住')
        else:
            print('  ✓ 未超 3 倍，继续是对的')
    except StalledTask as e:
        print(f'  ✓ 触发停机：{str(e)[:100]}')

    # ---------- 汇总 ----------
    print('\n[结论]')
    print(f'  无护栏串行 {a_t:.2f}s（无进度、无 ETA）')
    print(f'  有护栏并发 {b_t:.2f}s（有 ETA、有心跳、可中途止损）')
    print(f'  提速 {a_t / max(b_t, 1e-6):.1f}x，且从「跑完才知道」变成「跑前可预估」')


if __name__ == '__main__':
    main()
