#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""guard.py —— 拖进程四禁（M-R115）的落地器。

为什么有这个文件：用户 2026-10-18 提"看一下卡进度和进度缓慢这个问题"。
诊断结论：卡顿**不在代码写错里**——本生态 11 技能零无超时调用、零 while True。
真正的拖进程是四件事，每件都设一道代码卡：

  禁一 无界探测 → expect_sec 工期估算 + 超预估 N 倍 raise StalledTask
  禁二 静默空转 → heartbeat 心跳（stderr）+ fetch_paged 游标不前进即中止
  禁三 无验开工 → require_matrix 全矩阵单调性断言
  禁四 悬空收尾 → Triage 三态收尾（已完成/无法判定/未开始）

与 M-R114 的关系：M-R114 管"怎么并"（并发边界与档位），M-R115 管"怎么不拖"
（工期/心跳/验证/收尾）。两者正交，可同时生效。

用法：
    from guard import expect_sec, heartbeat, fetch_paged, require_matrix, Triage

    est = expect_sec(620, per_item=1.2, workers=6)          # 124.0s
    with heartbeat(total=620, label='测活') as hb:
        for row in rows:
            ...
            hb.tick()                                        # 每 ≥2s 或每 5% 打一行到 stderr

    rows, ok = fetch_paged(fetch_page, cursor_key='id')      # 游标不前进自动中止

    require_matrix('质量评分', values=['1080p','720p','480p','360p'],
                   orders=['316KB','225KB','174KB','未测'], score_fn=score)

    tri = Triage('线路源库测活')
    tri.done('成品源库 111 条', 90)
    tri.undecidable('线路源库 620 条', '沙箱无 spider 环境，csp 类结构性测不了')
    tri.pending('jar 溯源', '依赖采集补包级字段，未开始')
    print(tri.report())
"""
import sys
import time

# ---------- 禁一：工期估算 ----------
# S111 标定（calibrate_guard.py 实测）：期望收益模型 + 白烧≥30s 下限 → 100 条
# 稳健性：估算错 2/5/9 倍时起点分别为 56/69/88，取 100 是三者上界，安全。
CALIB_MIN_N = 100         # 低于此数免校准（3 条样本相对开销不值）
CALIB_SAMPLE = 3          # 校准样本数（另加 1 条跳过的冷启动，共测 4 条）
STALL_FACTOR = 3.0        # 实测超预估 3 倍 → 判 stalled（M-R115 禁一）
CONTENTION = 0.45         # 并发争抢折损（S111 反推标定，见 expect_sec 注释）


class StalledTask(RuntimeError):
    """任务卡住了：超出预估工期 STALL_FACTOR 倍，必须停下报告而不是继续转。"""


def expect_sec(n, per_item, workers=1, overhead=0.0):
    """估算整批工期（秒）。禁一：开工前必须出这个数，不许"不知道跑多久就开跑"。

    n        : 条目数
    per_item : 单条**串行**耗时（秒）。无历史数据时用 3 条样本实测均值。
    workers  : 并发数（走 M-R114 档位表）
    overhead : 额外固定开销（如入库的 sleep 累积）

    ★ 并发不是线性加速，且**小批量时并发几乎不赚**（S111 实测暴露）：
      12 条 × 0.1s 串行 1.54s，6 并发只到 0.89s（1.7x），不是 6x。
      原因：每条请求有固定握手/DNS 开销，并发还会争抢，轮次少了摊不平。

      speedup = min(workers^0.85 × CONTENTION, n)
      CONTENTION 标定过程（verify_final.py 真实网络 4 种规模）：
        - 取 0.75 时预估系统性偏乐观 39%~51%（实测）
        - 反推：54 条实测提速 2.1x，6^0.85=4.6，折算 0.46
        - 取 0.45 后预估与实际落进同一量级（偏差收窄至 ±25% 内）
      再乘 min(..., n)：并发收益不能超过条目数本身（1 条任务不可能因并发变快）。
    """
    n = max(int(n), 0)
    if n == 0:
        return 0.0
    if workers <= 1:
        return n * float(per_item) + float(overhead)
    raw = float(workers) ** 0.85 * CONTENTION
    speedup = max(min(raw, float(n)), 1.0)
    return (n * float(per_item)) / speedup + float(overhead)


def need_calibration(n):
    """禁一阈值：≥CALIB_MIN_N 条必须先跑样本校准。"""
    return int(n) >= CALIB_MIN_N


def calibrate(items, fn, sample=CALIB_SAMPLE, skip_first=True):
    """跑样本得出单条耗时估计。**用中位数，不用均值**（S111 标定）。

    ★ 为什么中位数（实测数据）：
      24 次采样的真实分布 CV=142%（个别慢请求把均值拉高），
      而 3 条样本的中位数估计 CV 仅 8%，均值估计 5%~70% 剧烈跳动。
      中位数抗离群，才配得上"3 条就够"这个小样本量。

    ★ 为什么跳过首条：
      首条要付 DNS + 连接池冷启动，系统性偏慢。实测跳过首条后
      估计值从 0.111s 降到 0.090s（更接近真实中位 0.086s）。

    返回 (per_item, samples_used)；样本不足时回退 None 让调用方自行处理。
    """
    import statistics
    n = int(sample) + (1 if skip_first else 0)
    picked = list(items or [])[:n]
    if len(picked) <= (1 if skip_first else 0):
        return None, 0
    t0 = time.time()
    times = []
    for i, it in enumerate(picked):
        s = time.time()
        try:
            fn(it)
        except Exception:
            pass                     # 样本失败不中断校准，只是不计入中位数
        el = time.time() - s
        if i == 0 and skip_first:
            continue
        times.append(el)
    if not times:
        return None, 0
    return statistics.median(times), len(times)


def check_stall(elapsed, expect, done, total, label=''):
    """在跑的过程中校验是否已判 stalled。超 STALL_FACTOR 倍即 raise。"""
    if expect <= 0:
        return
    if elapsed > expect * STALL_FACTOR:
        raise StalledTask(
            f'[{label or "任务"}] 已跑 {elapsed:.0f}s，超预估 {expect:.0f}s 的 '
            f'{STALL_FACTOR:.0f} 倍（进度 {done}/{total}，剩余 {total - done} 条）。'
            f'M-R115 禁一：停止空转，先查是限流、参数错、还是结构性不可达。')


# ---------- 禁二：心跳 + 游标闸门 ----------
class heartbeat:
    """长任务心跳：**按时间触发**往 stderr 打进度，不污染 stdout 管道。

    ★ 为什么按时间不按条数（S111 标定发现）：
      按条数设在 9s/条 的场景下，every=10 就是静默 90 秒，every=1 也没意义。
      而 0.2s/条 时 every=10 又变成 2 秒刷一次、620 条刷 64 行。
      条数 ≠ 时长，所以触发条件改成「距上次 ≥min_gap 秒」——两种场景都自然合适。

    ★ 为什么默认开、且走 stderr：
      本生态脚本多把结构化结果写 stdout 供下游解析（如 quality_batch 的 JSON）。
      心跳若走 stdout 会直接污染 JSON；走 stderr 则人看得到、管道不受影响。
      默认开是因为"光标转圈但无输出"正是用户投诉的卡进度形态，默认关等于没做。

    ★ 双保险 + 行数预算（S111 实测修正两次）：
      - 时间闸：距上次 ≥ min_gap 秒才打
      - 进度闸：只在**指数节点**打（5%/10%/20%/40%/80%）——不是每 5% 都打。
        实测：12 条任务按等距 5% 会瞬间刷 20 行，纯噪音；指数节点只 5 行。
      - 行数预算：总行数硬上限 max_lines（默认 8），超了只认时间闸和收尾。
    """

    _NODES = (0.05, 0.10, 0.20, 0.40, 0.80)

    def __init__(self, total, min_gap=2.0, label='', enabled=True, stream=None,
                 max_lines=8):
        self.total = int(total)
        self.min_gap = float(min_gap)
        self.label = label or '任务'
        self.enabled = enabled
        self.stream = stream or sys.stderr
        self.max_lines = max(int(max_lines), 2)
        self.done = 0
        self.t0 = 0.0
        self._last = 0.0
        self._hit = set()
        self._lines = 0

    def __enter__(self):
        self.t0 = self._last = time.time()
        if self.enabled:
            print(f'[{self.label}] 开始，共 {self.total} 条'
                  f'（心跳每 {self.min_gap:g}s 或关键进度节点）', file=self.stream)
            self._lines += 1
        return self

    def tick(self, k=1):
        self.done += k
        if not self.enabled:
            return
        now = time.time()
        pct = self.done / self.total if self.total else 1.0
        is_last = self.done == self.total
        by_time = (now - self._last) >= self.min_gap
        # 进度闸：跨过某个指数节点且该节点没打过
        crossed = [nd for nd in self._NODES if pct >= nd and nd not in self._hit]
        by_node = bool(crossed)
        over_budget = self._lines >= self.max_lines
        if over_budget and not (by_time or is_last):
            return
        if not (by_time or by_node or is_last):
            return
        for nd in crossed:
            self._hit.add(nd)
        self._last = now
        self._lines += 1
        el = now - self.t0
        per = el / self.done if self.done else 0
        left = max(self.total - self.done, 0)
        print(f'[{self.label}] {self.done}/{self.total} ({pct * 100:.0f}%)'
              f' | 均 {per:.2f}s/条 | 已 {el:.0f}s | 剩余 {left} 条 ~{per * left:.0f}s',
              file=self.stream)

    def __exit__(self, *a):
        if self.enabled:
            el = time.time() - self.t0
            per = el / self.done if self.done else 0
            print(f'[{self.label}] 结束 {self.done} 条，总 {el:.1f}s，均 {per:.2f}s/条',
                  file=self.stream)
        return False


def fetch_paged(fetch_page, cursor_key='id', max_pages=1000, label='分页',
                empty_tail_check=True):
    """分页拉取 + 游标闸门（禁二的核心，M-R100 从经验升级为代码）。

    为什么必须设卡（S111 实证）：
      `--cursor` 写成 `--start-cursor` 的前几天，`parse_known_args` 静默忽略了它，
      每页都返回首页 → reclass2 跑了 4 分钟零产出，**没有任何报错**。
      M-R100 早就写了这条坑，我照样踩——文字规则拦不住，只有断言拦得住。

    fetch_page(cursor) -> (rows, next_cursor)
      首次 cursor=None。next_cursor 为 None/空表示结束。

    连续 2 页首条记录相同 → 判「游标不前进」并 raise，而不是死循环到天荒地老。

    ★ empty_tail_check（S111 补）：先正常读到末页，再确认一次「末页之后确实为空」。
      针对的是比死循环更阴的坑——服务端若不返回 has_more（默认 False），
      调用方会**只读到第一页就以为读完了**，而且返回"成功"。
      这里额外拉一页验证；若服务端其实是"假结束"，断言会立刻发现。
      代价：多 1 次请求。数据一致性 > 一次往返。
    """
    out, cursor, prev_first = [], None, object()
    ended_early = False
    for page in range(max_pages):
        rows, nxt = fetch_page(cursor)
        rows = list(rows or [])
        if not rows:
            return out, True
        first = rows[0]
        key = first.get(cursor_key) if isinstance(first, dict) else first
        if page > 0 and key == prev_first:
            raise StalledTask(
                f'[{label}] 第 {page + 1} 页首条与上页相同（{key!r}），游标未前进。'
                f'M-R115 禁二 / M-R100：大概率是分页参数名写错被静默忽略。')
        prev_first = key
        out.extend(rows)
        if nxt in (None, '', False) or nxt == cursor:
            ended_early = True
            break
        cursor = nxt
    else:
        raise StalledTask(f'[{label}] 超过 {max_pages} 页仍未结束，判异常。')

    # 结束性验证：若"提前结束"，再确认下一批真的为空。
    # 注意：尾验证用**同一个 cursor 值**再拉一次——若服务端把 None 当"从头"，
    # 会返回第一页，此时靠"是否与已有数据重复"判别，而不是直接报漏数据。
    if ended_early and empty_tail_check and out:
        try:
            tail, _ = fetch_page(cursor)
            tail = list(tail or [])
            seen = {str(t.get(cursor_key) if isinstance(t, dict) else t) for t in out}
            fresh = [t for t in tail
                     if str(t.get(cursor_key) if isinstance(t, dict) else t) not in seen]
            if fresh:
                raise StalledTask(
                    f'[{label}] 声称已读完（{len(out)} 条），但再拉一页仍有 {len(fresh)} 条新数据。'
                    f'M-R115 禁二：服务端 has_more 缺失被默认成 False，会静默漏数据。')
        except StalledTask:
            raise
        except Exception:
            pass          # 尾验证本身失败不算错，主数据已拿到
    return out, True


# ---------- 禁三：全矩阵验证 ----------
class MatrixViolation(AssertionError):
    """评分/排序规则在极端组合下不满足单调性——不许开工写实现。"""


def require_matrix(name, values, orders, score_fn):
    """禁三：主观逻辑写代码前先枚举全矩阵，证明序关系成立。

    为什么要这步（S111 实证）：质量评分改了三版才稳——
      v1 速度无区分度（29 条 1080p 全卡 65 分）
      v2 速度能反超画质（360p+402KB/s 拿了 100 分）
      v3 才做到「任何速度下 1080p > 720p > 480p > 360p」全矩阵单调
    前两版若在动手前跑一遍矩阵，10 秒钟就能发现，不必各写一版。

    values : 主轴取值，**顺序即优先级**（前者必须永远 > 后者）
    orders : 副轴取值（速度/时长/广告等），穷举
    score_fn(value, order) -> 分数
    """
    bad = []
    for order in orders:
        prev_v, prev_s = None, None
        for v in values:
            s = score_fn(v, order)
            if prev_s is not None and s >= prev_s:
                bad.append(f'{order}: {v}={s} 未严格低于 {prev_v}={prev_s}')
            prev_v, prev_s = v, s
    if bad:
        raise MatrixViolation(
            f'[{name}] 单调性不成立，禁止写实现（M-R115 禁三）：\n  ' + '\n  '.join(bad[:8]))
    return len(values) * len(orders)


# ---------- 禁四：三态收尾 ----------
class Triage:
    """任务收尾三态清单（禁四）。杜绝"跑完了但说不清做没做完"。

    为什么会悬空（S111 实证）：线路源库 620 条质量评测全部"无法判定"，
    这不是漏做而是结构性限制（沙箱无 spider 环境）。但开工前没说，
    收尾时才讲，用户看到的就是"没干完"。
    """

    DONE, UNDECIDABLE, PENDING = '已完成', '无法判定', '未开始'

    def __init__(self, title=''):
        self.title = title
        self.items = []

    def done(self, what, n=None, note=''):
        self.items.append((self.DONE, what, n, note))
        return self

    def undecidable(self, what, why, n=None):
        """无法判定必须带结构性原因——"做不了"不算原因。"""
        if not why or len(str(why)) < 6:
            raise ValueError('无法判定必须给出结构性原因（M-R115 禁四）')
        self.items.append((self.UNDECIDABLE, what, n, why))
        return self

    def pending(self, what, why='', n=None):
        self.items.append((self.PENDING, what, n, why))
        return self

    def report(self):
        lines = [f'▌{self.title or "收尾三态"}']
        for st, what, n, note in self.items:
            cnt = f' {n} 条' if n is not None else ''
            mark = {self.DONE: '✅', self.UNDECIDABLE: '⚠️', self.PENDING: '⏸'}[st]
            lines.append(f'  {mark} [{st}] {what}{cnt}'
                         + (f' — {note}' if note else ''))
        done = sum(1 for i in self.items if i[0] == self.DONE)
        lines.append(f'  合计 {done}/{len(self.items)} 已完成')
        return '\n'.join(lines)


if __name__ == '__main__':
    print('=== M-R115 自检 ===')

    print('\n[禁一] 工期估算 + 校准阈值:')
    for n in (10, 20, 100, 620):
        est = expect_sec(n, 1.2, workers=6)
        print(f'  {n:4d} 条 × 1.2s / 6 并发 → 预估 {est:7.1f}s'
              f'  需校准={need_calibration(n)}')

    print('\n[禁一] 超预估 3 倍必须 raise:')
    try:
        check_stall(elapsed=400, expect=124, done=80, total=620, label='测活')
        print('  ❌ 没拦住')
    except StalledTask as e:
        print(f'  ✓ 拦住：{str(e)[:78]}')

    print('\n[禁二] 心跳按时间触发（0.2s/条 与 9s/条 都合适）:')
    import io
    for per, desc in ((0.0, '极快 0s/条'), (0.02, '快 20ms/条')):
        buf = io.StringIO()
        with heartbeat(total=100, label=desc, stream=buf) as hb:
            for _ in range(100):
                if per:
                    time.sleep(per)
                hb.tick()
        print(f'  {desc}: stdout 干净，stderr {len(buf.getvalue().strip().split(chr(10)))} 行')

    print('\n[禁二] 心跳不污染 stdout（关键）:')
    import contextlib
    so = io.StringIO()
    with contextlib.redirect_stdout(so):
        with heartbeat(total=50, label='污染测试', stream=io.StringIO()) as hb:
            for _ in range(50):
                hb.tick()
        print('{"json": "result"}')          # 模拟下游要解析的产物
    clean = so.getvalue().strip()
    print(f'  stdout 只剩产物: {clean!r} → {"✓" if clean == chr(123)+chr(34)+"json"+chr(34)+": "+chr(34)+"result"+chr(34)+chr(125) else "❌ 被污染"}')

    print('\n[禁二] 游标不前进必须中止:')
    calls = {'n': 0}
    def bad_page(cursor):
        calls['n'] += 1
        return [{'id': 1}, {'id': 2}], 'same'   # 永远返回同一页
    try:
        fetch_paged(bad_page, label='坏游标')
        print('  ❌ 没拦住')
    except StalledTask as e:
        print(f'  ✓ 拦住：{str(e)[:78]}')

    print('\n[禁二] 假结束（has_more 缺失默认 False）必须发现:')
    real = [{'id': i} for i in range(5)]
    state = {'page': 0}
    def lying_page(cursor):
        # 第 1 页回 3 条并声称结束，实际第 2 页还有 2 条
        if state['page'] == 0:
            state['page'] = 1
            return real[:3], None          # ← 谎报结束
        return real[3:], None
    try:
        got, _ = fetch_paged(lying_page, label='假结束')
        print(f'  ❌ 没发现，只拿到 {len(got)} 条（应为 5）')
    except StalledTask as e:
        print(f'  ✓ 发现：{str(e)[:78]}')

    print('\n[禁二] 正常分页应完整读完且不误报:')
    def good_page(cursor):
        i = int(cursor or 0)
        rows = real[i:i + 2]
        nxt = str(i + 2) if i + 2 < len(real) else None
        return rows, nxt
    got, _ = fetch_paged(good_page, label='正常')
    print(f'  拿到 {len(got)} 条 → {"✓" if len(got) == 5 else "❌"}')

    print('\n[禁三] 评分矩阵单调性:')
    def good_score(v, o):
        base = {'1080p': 92, '720p': 76, '480p': 60, '360p': 44}[v]
        d = {'快': 8, '中': 2, '慢': -3}[o]
        return max(0, min(base + d, 100))
    try:
        k = require_matrix('质量评分', ['1080p', '720p', '480p', '360p'],
                           ['快', '中', '慢'], good_score)
        print(f'  ✓ {k} 种组合全矩阵单调')
    except MatrixViolation as e:
        print(f'  ❌ {e}')

    def bad_score(v, o):
        return 100 if o == '快' else {'1080p': 92, '720p': 76, '480p': 60, '360p': 44}[v]
    try:
        require_matrix('坏评分', ['1080p', '720p', '480p', '360p'], ['快'], bad_score)
        print('  ❌ 坏评分没拦住')
    except MatrixViolation:
        print('  ✓ 速度反超画质的坏评分被拦住')

    print('\n[禁四] 三态收尾:')
    t = Triage('源库质量评测')
    t.done('成品源库', 90).undecidable('线路源库', '沙箱无 spider 环境，csp 类结构性测不了', 620)
    print(t.report())
