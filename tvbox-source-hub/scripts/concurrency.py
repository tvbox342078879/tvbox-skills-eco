#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""concurrency.py —— 生态唯一并发入口（M-R114）。

为什么要有这个文件（S110 用户原话）：
  "现在是不是同时进行着几个事情，不是有并发机制，没看到触发"

盘点结果：13 个脚本各自 `ThreadPoolExecutor(max_workers=3/6/8/10/12/20/30/40)`，
没有一个数有依据，横跨 13 倍。更严重的是**任务之间乱并行**——
体系重刷、质量评测、livecheck 同时写同一批记录，`batch_update` 提交整条
properties，互相覆盖，谁也说不清最终值是谁写的。

★ M-R114 五条判据，本文件是判据 1/2/3/5 的落地；判据 4 靠调用方留实测数据：

  判据1 并发边界：只准「条目级」，不准「任务级」
        → run_bounded() 的 items 必须是互不相干的条目
  判据2 写冲突：同一 record_id 只能有一个写者
        → declare_write_set() 登记，冲突即 raise（不是警告）
  判据3 并发数来自档位表：禁止就地拍数
        → TIERS 定档，超上限 raise（不静默截断）
  判据4 收益实测：不许估算（M-R26）
        → 返回 Report 带 elapsed 与串行等效耗时，供调用方记录
  判据5 必经点设卡：本模块是唯一入口，绕开即违规（M-R107）
        → audit_concurrency.py 扫描就地 ThreadPoolExecutor 报违规

用法：
    from concurrency import run_bounded, TIER_WEB

    rep = run_bounded(urls, evaluate_one, tier=TIER_WEB, workers=6)
    for r in rep.results: ...
    print(rep.summary())     # 含实测耗时，可直接写进文档

    # 写库任务：声明写集合，重入冲突会被拦住
    run_bounded(rows, write_one, tier=TIER_API, write_set={r['id'] for r in rows})
"""
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------- 判据3：档位表（数字有依据，不是拍的） ----------
# 依据取自 2026-10-18 两轮实测 + 各站点限流经验，改动须同步更新本注释与 M-R114。
TIERS = {
    'A': {'name': 'A·对自家 API', 'max': 8,
          'note': '写库/读库打到同一后端，服务端会限流；M-R26 说云端提速比会高估，故不给 12+'},
    'B': {'name': 'B·对公网站点', 'max': 6,
          'note': '测活/质量评测打在别人服务器上，太高被判爬虫，反过来害自己'},
    'C': {'name': 'C·对 CDN 分片', 'max': 4,
          'note': '单站常限单连接速率，多线程争抢会把测速结果搅乱，测出假速度'},
    'D': {'name': 'D·本地/无网络', 'max': min((os.cpu_count() or 4), 8),
          'note': '解 dex / 纯计算，受 CPU 上限约束'},
}

TIER_API = 'A'
TIER_WEB = 'B'
TIER_CDN = 'C'
TIER_LOCAL = 'D'


class ConcurrencyError(RuntimeError):
    """违反 M-R114 的并发用法。一律 raise，不降级成 warning。"""


# ---------- 判据2：写集合登记（进程内单写者） ----------
_LOCK = threading.Lock()
_ACTIVE_WRITES = {}     # token -> (label, set(record_id))


def declare_write_set(record_ids, label=''):
    """登记「我即将写这些记录」。与在跑的任务有交集 → ConcurrencyError。

    返回 token，用完必须 release_write_set(token)（推荐 with 语法见 write_scope）。

    ★ 为什么必须 raise 而不是警告：
      本次事故就是"都知道会覆盖，但没人拦"，结果 reclass2 跑了 4 分钟一条没写进去，
      而质量评测同时在改同一批行。警告会被无视，只有拒绝才拦得住（M-R107 ④）。
    """
    ids = {str(x) for x in (record_ids or [])}
    if not ids:
        return None
    with _LOCK:
        for tok, (lbl, other) in _ACTIVE_WRITES.items():
            inter = ids & other
            if inter:
                sample = ', '.join(list(inter)[:3])
                raise ConcurrencyError(
                    f'写集合冲突（M-R114 判据2）：本任务[{label}] 与在跑任务[{lbl}] '
                    f'重叠 {len(inter)} 条记录（如 {sample}）。跨任务必须串行。')
        tok = uuid.uuid4().hex[:8]
        _ACTIVE_WRITES[tok] = (label or 'unnamed', ids)
        return tok


def release_write_set(token):
    if not token:
        return
    with _LOCK:
        _ACTIVE_WRITES.pop(token, None)


class write_scope:
    """with write_scope(ids, '质量评测'):  ... 自动登记/释放。"""
    def __init__(self, record_ids, label=''):
        self.ids, self.label, self.tok = record_ids, label, None

    def __enter__(self):
        self.tok = declare_write_set(self.ids, self.label)
        return self

    def __exit__(self, *a):
        release_write_set(self.tok)
        return False


# ---------- 判据3：档位校验 ----------
def resolve_workers(tier, workers=None):
    """按档位解析并发数。超上限直接 raise（M-R114 判据3：不静默截断）。"""
    if tier not in TIERS:
        raise ConcurrencyError(f'未知档位 {tier!r}，可选 {list(TIERS)}')
    cap = TIERS[tier]['max']
    if workers is None:
        return cap
    if not isinstance(workers, int) or workers < 1:
        raise ConcurrencyError(f'workers 必须是正整数，收到 {workers!r}')
    if workers > cap:
        raise ConcurrencyError(
            f'{TIERS[tier]["name"]} 档上限 {cap}，请求 {workers} 超限（M-R114 判据3）。'
            f'依据：{TIERS[tier]["note"]}')
    return workers


# ---------- 主入口 ----------
class Report:
    """并发结果 + 实测数据（判据4：收益用实测不用估算）。"""
    __slots__ = ('results', 'errors', 'workers', 'tier', 'elapsed', 'n', 'label',
                 'expect_sec', 'stalled')

    def __init__(self):
        self.results = []
        self.errors = []
        self.workers = 0
        self.tier = ''
        self.elapsed = 0.0
        self.n = 0
        self.label = ''
        self.expect_sec = 0.0        # M-R115 禁一：预估工期
        self.stalled = False         # 是否被判拖进程

    @property
    def ok_count(self):
        return len(self.results)

    @property
    def fail_count(self):
        return len(self.errors)

    def summary(self):
        t = TIERS.get(self.tier, {})
        per = (self.elapsed / self.n) if self.n else 0
        s = (f'[{self.label or "并发"}] {self.n} 条 × {self.workers} 并发'
             f'（{t.get("name", self.tier)} 档）→ 成功 {self.ok_count} / 失败 {self.fail_count}'
             f' | 实测耗时 {self.elapsed:.1f}s，均 {per:.2f}s/条')
        if self.expect_sec:
            dev = (self.elapsed - self.expect_sec) / self.expect_sec
            s += f' | 预估 {self.expect_sec:.1f}s，偏差 {dev:+.0%}'
        if self.stalled:
            s += ' | ⚠ 判 stalled'
        return s


def run_bounded(items, fn, tier=TIER_WEB, workers=None, label='',
                write_set=None, on_error='record', first_arg=True, keep_order=False,
                expect=None, heartbeat_every=None, on_stall='raise'):
    """按档位限流并发跑 items → Report。

    items      : 互不相干条目（判据1：条目级，不是任务级）
    fn         : fn(item) -> 任意；若 first_arg=False 则 fn(idx, item)
    write_set  : 本批要写的 record_id 集合。传入则自动登记（判据2），冲突 raise
    on_error   : 'record' 收集异常继续（默认，单条失败不拖垮全批）
                 'raise'  立即抛出
    keep_order : True 时 results 与 items 顺序一一对应（失败位置为 None）。
                 ★ 默认 False 用 as_completed 边跑边收，吞吐更好；
                   但**调用方若依赖顺序必须显式开**——`core.fanout` 就栽在这：
                   原实现用 ex.map 天然保序，换成 as_completed 后返回 [2,3,1]，
                   依赖顺序的调用方会静默拿错结果，且不报错。

    ★ M-R115 拖进程护栏（新增，见 guard.py）：
    expect       : 预估工期秒数，由 guard.expect_sec() 算。给了才能判 stalled。
    heartbeat_every : 每推进多少条调一次 hb.tick()。不传则不打心跳。
                      传 heartbeat 对象也行（内部用 hb.tick()）。
    on_stall     : 'raise' 超预估 STALL_FACTOR 倍即抛 StalledTask（默认）
                   'record' 记 rep.stalled=True 但继续跑完

    ★ 单条异常默认不中断：测活/评测场景下个别源超时是常态，
      一旦中断整批，前面跑的全白费（设计质量评测时踩过）。
    """
    items = list(items or [])
    w = resolve_workers(tier, workers)
    rep = Report()
    rep.tier, rep.workers, rep.n, rep.label = tier, w, len(items), label
    rep.expect_sec = float(expect or 0.0)

    if not items:
        return rep

    token = declare_write_set(write_set, label) if write_set else None
    t0 = time.time()
    hb = None
    if heartbeat_every is not None:
        from guard import heartbeat as _hb
        hb = _hb(total=len(items), label=label or '并发')
        hb.__enter__()
    try:
        if len(items) == 1 or w == 1:
            # 退化路径：单线程直接跑，省掉线程池开销
            for i, it in enumerate(items):
                try:
                    rep.results.append(fn(it) if first_arg else fn(i, it))
                except Exception as e:
                    if on_error == 'raise':
                        raise
                    rep.errors.append((i, repr(e)[:200]))
                if hb:
                    hb.tick()
                if expect and on_stall != 'never':
                    try:
                        _check(items, i, t0, expect, label)
                    except Exception:
                        if on_stall == 'raise':
                            raise
                        rep.stalled = True
                        on_stall = 'never'
        else:
            slots = [None] * len(items)
            done_n = 0
            with ThreadPoolExecutor(max_workers=w) as ex:
                futs = {ex.submit(fn, it) if first_arg else ex.submit(fn, i, it): i
                        for i, it in enumerate(items)}
                for f in as_completed(futs):
                    i = futs[f]
                    try:
                        v = f.result()
                        if keep_order:
                            slots[i] = v
                        else:
                            rep.results.append(v)
                    except Exception as e:
                        if on_error == 'raise':
                            raise
                        rep.errors.append((i, repr(e)[:200]))
                    done_n += 1
                    if hb:
                        hb.tick()
                    if expect and on_stall != 'never':
                        try:
                            _check(items, done_n, t0, expect, label)
                        except Exception:
                            if on_stall == 'raise':
                                raise
                            rep.stalled = True
                            on_stall = 'never'
            if keep_order:
                rep.results = slots
    finally:
        if hb:
            hb.__exit__(None, None, None)
        release_write_set(token)
        rep.elapsed = time.time() - t0
    return rep


def _check(items, done, t0, expect, label):
    """M-R115 禁一：超预估 STALL_FACTOR 倍即停。"""
    from guard import check_stall
    check_stall(time.time() - t0, expect, done, len(items), label)


def active_writes():
    """当前正在写的任务（排障用）。"""
    with _LOCK:
        return {tok: {'label': lbl, 'n': len(ids)} for tok, (lbl, ids) in _ACTIVE_WRITES.items()}


if __name__ == '__main__':
    # 自检：五条判据各跑一次
    print('=== M-R114 自检 ===')
    for t in TIERS.values():
        print(f"  {t['name']:14s} 上限 {t['max']:2d}  {t['note'][:44]}")

    print('\n[判据3] 超限必须 raise:')
    for tier, w in [('B', 20), ('A', 8), ('D', 99)]:
        try:
            got = resolve_workers(tier, w)
            print(f'  {tier} 请求 {w} → 通过 {got}')
        except ConcurrencyError as e:
            print(f'  {tier} 请求 {w} → 拒绝 ✓')

    print('\n[判据2] 写集合冲突:')
    t1 = declare_write_set(['a', 'b', 'c'], '任务甲')
    try:
        declare_write_set(['c', 'd'], '任务乙')
        print('  ❌ 冲突未拦住')
    except ConcurrencyError as e:
        print(f'  ✓ 拦住：{str(e)[:70]}')
    release_write_set(t1)
    t2 = declare_write_set(['c', 'd'], '任务乙')
    print(f'  ✓ 释放后可登记：{bool(t2)}')
    release_write_set(t2)

    print('\n[判据1/4] 条目级并发 + 实测耗时:')
    import random
    def slow(x):
        time.sleep(0.05)
        return x * 2
    ts = time.time()
    serial = [slow(i) for i in range(12)]
    serial_t = time.time() - ts
    rep = run_bounded(range(12), slow, tier=TIER_LOCAL, workers=6, label='自检')
    print(f'  串行 {serial_t:.2f}s / 并发 {rep.elapsed:.2f}s'
          f' → 提速 {serial_t / max(rep.elapsed, 1e-6):.1f}x')
    print(' ', rep.summary())
    print(f'  结果一致: {sorted(rep.results) == sorted(serial)}')

    print('\n[异常隔离] 单条失败不拖垮全批:')
    def flaky(x):
        if x == 3:
            raise ValueError('故意失败')
        return x
    rep2 = run_bounded(range(6), flaky, tier=TIER_LOCAL, workers=3, label='异常测试')
    print(f'  成功 {rep2.ok_count} / 失败 {rep2.fail_count} → {"✓" if rep2.ok_count == 5 else "❌"}')

    print('\n[保序] keep_order=True 时结果必须与 items 一一对应:')
    import random as _r
    def jitter(x):
        time.sleep(_r.random() * 0.06)   # 故意打乱完成顺序
        return x
    rep3 = run_bounded(range(20), jitter, tier=TIER_LOCAL, workers=8,
                       label='保序测试', keep_order=True)
    print(f'  结果 {rep3.results[:8]}… → {"✓ 保序" if rep3.results == list(range(20)) else "❌ 乱序：core.fanout 曾栽在这"}')
    rep4 = run_bounded(range(20), jitter, tier=TIER_LOCAL, workers=8, label='乱序测试')
    print(f'  不保序时结果长度 {len(rep4.results)}（内容相同、顺序不保证，属预期）')
