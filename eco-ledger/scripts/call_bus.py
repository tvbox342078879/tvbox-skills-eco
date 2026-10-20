#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""call_bus.py —— 账房·跨界统一调用总线（S165）

用户原话（本模块唯一依据）：
   「做跨对话，跨任务的，跨场景，跨生态的调用机制闭环，合并进账房，
     做统一调用入口，不然如同刚才一样，调用麻烦，做好联动和分工接入」

## 为什么做在账房，而不是再另开一个入口

生态里已经有一个 `tvbox` 入口（15 动词 → 13 模块），它解决的是「**一个会话内**敲哪条命令」。
用户说的"调用麻烦"不是这个层面的：刚才处置定时任务遗留，真正的痛点是——
   · 定时任务在**另一个会话**里跑出来的问题，这边只能靠"人记得"接过来；
   · 一次要跨 4 个技能（source-hub 看数 → probe 测活 → inspect 判 → close 收口），
     每换一个技能就要重记一遍它自己的子命令和参数；
   · 跨过去以后**没人记账**，于是"调过去了"和"调成功了"没有区别。

所以本模块补的不是"又一个命令表"，而是四件事，且只做这四件：
   ① **统一调用入口** `ledger call <动词> …`：动词 → 技能 → 命令，一张**唯一路由表**
   ② **跨界**：四种调用（对话/任务/场景/生态）各有**出入参 + 记账科目**，
      每次调用都落一笔分录，凭证号按界生成（对话=会话号，任务=job_id，场景=链路名）
   ③ **闭环**：调用结束必须给出四态判定之一（✅成功 / ❌失败 / ⚠️部分 / ❓无法判定），
      失败自动进六源欠账（M-R82：**不许因为没有收口就默认成功**）
   ④ **联动与分工**：接力 `relay`（上游产出自动喂下游）、`who`（这活归谁）、
      `map`（谁能调谁 / 有没有环 / 有没有幽灵命令）

## 与 `tvbox` 入口的分工（避免两个入口打架，M-R38/M-R105）

   · `tvbox`      = **态射**（给人敲的原子动作）。本模块**不复制**它的命令定义，
                    一律经它派发（`_invoke` 内唯一一处 subprocess）。
   · `ledger`     = **函数**（把一串态射组合成有出入参、有记账、有闭环的一次调用）。
   · 判定/计数   = 复用指标户（`指标·调用总次数` 等），与 `reconcile` **同一口径**，
                    本模块不另写一套判据。

## 为什么必须走子进程而不是 import

`conductor.py` 有 8300+ 行且顶层持有大量模块级状态；同进程反复 import 其它技能
会共享缓存与 globals，**上一次调用的残留会算到这一次头上**——这正是 `tvbox`
入口在 S138 花整轮修掉的坑（`_EXIT_CODE`/`_RC` 全局残留）。故此处一律新进程，
只吞 stdout/stderr 与退出码，调用之间零共享。

## 三态纪律（M-R60）

manifest 读不到、调用超时、退出码语义不明 → 判 `❓无法判定`，**不折叠成成功也不折叠成失败**。
   · 折叠成成功 → 欠账被悄悄抹掉；
   · 折叠成失败 → 下游 relay 会被一个其实没跑坏的步骤掐断。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = '/root/.codebuddy/skills'
TVBOX = '/usr/local/bin/tvbox'
MANIFEST = os.path.join(SKILLS, 'tvbox-source-hub', 'manifest.json')

sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(SKILLS, 'tvbox-source-hub', 'scripts'))
import core_ledger as L          # noqa: E402  账房核心（唯一记账通道）
import ledger as LED             # noqa: E402  簿记实现（entry_balances / 指标户口径）

# ══════════════════════════════════════════════════════════════════════════
# ① 唯一路由表：界 → 动词 → 技能/命令/产出/记账科目
#
# 这张表**不复制** `tvbox` 的 ROUTE（那是态射表，在本文件里只有 `_invoke` 一处引用它）。
# 本表管的是"调用"这一层的事：跨哪个界、谁能当上游、失败记到哪个科目。
# ══════════════════════════════════════════════════════════════════════════
BOUNDARIES = {
    'dialogue': {
        'name': '对话界', 'icon': '💬',
        'what': '上一个/下一个会话里发生的事（跨对话）——定时任务产出、别的会话欠下的账',
        'in': '会话号 / 定时任务名 / 关键词',
        'out': '事项清单（sid + 来源 + 证据）',
        'voucher': 'DLG-{YYYYMMDD}-{seq}',
        'acct': '跨界·对话',
    },
    'task': {
        'name': '任务界', 'icon': '📋',
        'what': '任务与任务之间（跨任务）——一路的产出是另一路的输入',
        'in': '事项号 sid / 承诺号 Cxxx',
        'out': '收口结论（done / wontfix / defer）+ 证据',
        'voucher': 'TSK-{YYYYMMDD}-{seq}',
        'acct': '跨界·任务',
    },
    'scene': {
        'name': '场景界', 'icon': '🎬',
        'what': '场景与场景之间（跨场景）——找源 → 测活 → 判活 → 入库 → 快照，接力式',
        'in': '候选文件 / 上游步骤产出',
        'out': '下游可直接吃的文件或清单',
        'voucher': 'SCN-{YYYYMMDD}-{seq}',
        'acct': '跨界·场景',
    },
    'eco': {
        'name': '生态界', 'icon': '🌐',
        'what': '技能与技能之间（跨生态）——谁归口、谁能调谁、调了记没记',
        'in': '技能名 / 意图',
        'out': '归口技能 + 可执行命令',
        'voucher': 'ECO-{YYYYMMDD}-{seq}',
        'acct': '跨界·生态',
    },
}

# 动词表：每个条目都能被 `ledger call <动词>` 直接叫到。
#   skill  归口技能（谁负责这件事，不是谁被执行）
#   cmd    `tvbox` 侧的真实命令（本模块只做**动词级别名**，不做语法改写）
#   io     出参里会被下游 relay 引用的字段（仅用于文档与提示，不做解析魔法）
VERBS = {
    # ── 跨对话：把别的会话/任务的产出捞回来 ──
    'debt': {
        'skill': 'unfinished-audit', 'cmd': 'close status',
        'desc': '欠账总览（六源全采）：别的会话/定时任务挂过来的都在这里',
        'io': '→ sid 清单', 'args': '[--source 六源之一]',
    },
    'from-job': {
        'skill': 'unfinished-audit', 'cmd': 'close',
        'desc': '只捞定时任务挂出来的（跨对话最常见的一种）',
        'io': '→ job 名下欠账', 'args': '[定时任务名]',
    },
    'mine': {
        'skill': 'unfinished-audit', 'cmd': 'close mine',
        'desc': '某技能名下欠了哪些（分工反查）',
        'io': '→ 该技能欠账', 'args': '<技能名>',
    },
    'done': {
        'skill': 'unfinished-audit', 'cmd': 'close done',
        'desc': '显式收口一笔（**必须带证据**，M-R82）',
        'io': '→ 收口结论', 'args': '<sid> <证据>',
    },
    # ── 跨任务 ──
    'claim': {
        'skill': 'unfinished-audit', 'cmd': 'close mine',
        'desc': '接活：先看这名下欠了什么再动手（P1 改代码前必查账）',
        'io': '→ 前置条件', 'args': '<技能名> [--before 文件|关键词]',
    },
    # ── 跨场景：一条链走完 ──
    'scan': {
        'skill': 'probe-hub', 'cmd': 'probe search cloud',
        'desc': '场景①找源：看云端候选册（跨会话持久）',
        'io': '→ 候选', 'args': '[关键词]',
    },
    'harvest': {
        'skill': 'probe-hub', 'cmd': 'probe harvest cloud',
        'desc': '场景①找源：看采集候选册',
        'io': '→ 候选', 'args': '',
    },
    'liveness': {
        'skill': 'probe-hub', 'cmd': 'probe livecheck',
        'desc': '场景②测活：真拉一次片/流（不是 ping）',
        'io': '→ 存活清单', 'args': '[api]',
    },
    'ingest': {
        'skill': 'tvbox-source-hub', 'cmd': 'ingest',
        'desc': '场景③入库：黑名单拦截 → 源库（幂等）',
        'io': '→ 入库条数', 'args': '--candidates 文件 [--dry]',
    },
    'snap': {
        'skill': 'tvbox-source-hub', 'cmd': 'snapshot',
        'desc': '场景④快照：唯一权威读数刷新',
        'io': '→ 快照 id', 'args': '',
    },
    # ── 跨生态：归口 / 地图 / 体检 ──
    'who': {
        'skill': 'ecosys-conductor', 'cmd': 'conductor whoami',
        'desc': '这活归谁：意图 → 技能（分工入口）',
        'io': '→ 归口技能', 'args': '<意图>',
    },
    'ask': {
        'skill': 'ecosys-conductor', 'cmd': 'conductor ask',
        'desc': '需求管家：术语翻译 + 历史呼应 + 路由 + 拆解（**开工第一句话**）',
        'io': '→ 执行清单', 'args': '<需求>',
    },
    'map': {
        'skill': 'eco-ledger', 'cmd': None,
        'desc': '调用地图：谁能调谁 / 有没有环 / 有没有幽灵命令（本模块自省）',
        'io': '→ 调用图', 'args': '--json',
    },
    'audit-call': {
        'skill': 'eco-ledger', 'cmd': None,
        'desc': '调用体检：登记了却没接线、接了线却没人记账、账上记了但命令不存在',
        'io': '→ 缺口清单', 'args': '',
    },
}

# 指标户（与 reconcile 同口径 —— 名字写死在这里，判据仍在 ledger.entry_balances 一处）
METRICS = {
    'total': '指标·调用总次数',
    'ok': '指标·调用成功',
    'fail': '指标·调用失败',
    'unknown': '指标·调用无法判定',
}

ICON = {'ok': '✅', 'fail': '❌', 'partial': '⚠️', 'unknown': '❓'}


# ══════════════════════════════════════════════════════════════════════════
# ② 执行：唯一一处跨进程调用
# ══════════════════════════════════════════════════════════════════════════
def _tvbox_argv(cmd, extra):
    """把 `tvbox <cmd> <extra...>` 变成 argv。只**拼接**，不改写语法。"""
    return cmd.split() + list(extra or [])


def _invoke(argv, timeout=180):
    """跑一条 `tvbox` 命令，返回 dict(ok, out, err, rc, elapsed, timeout)。

    ok 三态：
      True  —— 退出码 0
      False —— 退出码非 0（明确失败）
      None  —— 超时/起不来（**无法判定**，M-R60 不折叠）
    """
    t0 = time.time()
    try:
        r = subprocess.run([TVBOX] + list(argv), capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return {'ok': None, 'out': '', 'err': f'超时（>{timeout}s）',
                'rc': None, 'elapsed': time.time() - t0}
    except Exception as e:
        return {'ok': None, 'out': '', 'err': f'{type(e).__name__}: {str(e)[:120]}',
                'rc': None, 'elapsed': time.time() - t0}
    out = (r.stdout or '').strip()
    err = (r.stderr or '').strip()
    return {'ok': r.returncode == 0, 'out': out, 'err': err,
            'rc': r.returncode, 'elapsed': time.time() - t0}


# ══════════════════════════════════════════════════════════════════════════
# ③ 记账：一笔调用 = 一笔分录（凭证号按界生成，幂等靠分录指纹）
# ══════════════════════════════════════════════════════════════════════════
def _seq_today(acct):
    """同一界今天已记几笔（用于凭证序号）——从分录余额反推，不另建计数器。"""
    bal = LED.entry_balances() or {}
    v = sum((bal.get(acct) or {}).values())
    return int(v)


def _voucher(bnd, seq):
    return BOUNDARIES[bnd]['voucher'].format(
        YYYYMMDD=time.strftime('%Y%m%d'), seq=f'{seq:03d}')


def _book(bnd, verb, verdict, out, err, elapsed, argv, note=''):
    """落一笔调用分录。**记账失败不掩盖调用结果**（旁路，但必须说出来）。"""
    acct = BOUNDARIES[bnd]['acct']
    seq = _seq_today(acct) + 1
    v = _voucher(bnd, seq)
    vrb = VERBS.get(verb, {})
    tail = (out or '').strip().splitlines()
    summ = tail[0][:70] if tail else (err or '')[:70]
    e = L.make_entry(
        v, acct, '期初·建账基准', 1, '事项数', '转账',
        agent=vrb.get('skill', 'eco-ledger'), finalcheck=False,
        note=f'{ICON.get(verdict, "")}{verdict}｜{verb} → tvbox {" ".join(argv)}｜'
             f'{elapsed:.1f}s｜{summ}' + (f'｜{note}' if note else ''))
    ok, msg = L.post(e)
    return v, ok, msg


def _bump(metric, n=1):
    """指标户 +n（与 reconcile 同口径：余额 = Σ借 − Σ贷）。"""
    acct = METRICS[metric]
    e = L.make_entry(f'M-{time.strftime("%m%d%H%M%S")}-{metric}', acct,
                     '期初·建账基准', n, '事项数', '指标波动',
                     agent='eco-ledger', note=f'call {metric} +{n}')
    return L.post(e)[0]


# ══════════════════════════════════════════════════════════════════════════
# ④ 命令实现
# ══════════════════════════════════════════════════════════════════════════
def cmd_verbs(a):
    print('📞 账房·统一调用入口（`tvbox ledger call <动词>`）\n')
    by = {}
    for k, v in VERBS.items():
        by.setdefault(v['skill'], []).append(k)
    for b, meta in BOUNDARIES.items():
        print(f"{meta['icon']} {meta['name']}（{b}）　{meta['what']}")
        print(f"   出参: {meta['out']}　凭证: {meta['voucher']}　科目: {meta['acct']}")
        print()
    print('─' * 66)
    print(f'共 {len(VERBS)} 个动词，按归口技能分组：\n')
    for sk in sorted(by):
        print(f'  【{sk}】')
        for k in by[sk]:
            v = VERBS[k]
            print(f'    {k:<11} {v["desc"]}')
            run = f'tvbox {v["cmd"]} {v["args"]}'.rstrip() if v['cmd'] else '（本模块自省，不调外部命令）'
            print(f'    {"":<11} 跑: {run}')
        print()
    print('💡 跨界一次走完: tvbox ledger relay <场景名>')
    print('   P1 开工先查账: tvbox ledger call claim <技能名> --before <要改的文件>')


def cmd_call(a):
    verb = a.verb
    v = VERBS.get(verb)
    if not v:
        near = [k for k in VERBS if verb in k or k in verb]
        print(f'❌ 未知调用动词: {verb}')
        if near:
            print(f'   你是不是想: {" / ".join(near)}')
        print(f'   全部动词: {" / ".join(VERBS)}')
        print('   看详情: tvbox ledger verbs')
        return 2

    bnd = a.boundary or _guess_boundary(verb)
    if bnd not in BOUNDARIES:
        print(f'❌ 未知界: {bnd}（可选 {" / ".join(BOUNDARIES)}）')
        return 2

    # map / audit-call 是自省命令，不走子进程。
    # ★ `a` 是 `ledger call` 的 Namespace（只有 verb/args/boundary/...），
    #   而 cmd_map/cmd_audit_call 要读 `a.json`。直接透传会 AttributeError ——
    #   现象是"先报命令异常终止，再打出正确结果"（异常发生在返回码计算之后），
    #   比纯崩溃更迷惑：看起来像是成功了。故这里补一个**独立 Namespace**再调。
    if v['cmd'] is None:
        sub = _self_ns(a)
        if verb == 'map':
            return cmd_map(sub)
        return cmd_audit_call(sub)

    # ── 必填参数前置校验（S165）──
    # 底层的通病：缺参数时打印"用法: …"然后 exit 0（`conductor whoami` 就是，已单独修）。
    # 调用层不能指望每个技能都改好——这里是统一入口，就该在**发出去之前**拦住。
    # 判据不靠猜：动词表里 args 写成 `<必填>` 形式的就是必填项。
    # 只认方括号外的 `<必填>`：`[--source 六源之一]` 这种方括号里的全是可选项，
    # 首版把 `[<定时任务名>]` 也当成必填，`debt` 因此被自己的校验拦住（实测踩到）。
    _spec = re.sub(r'\[[^\]]*\]', '', v['args'] or '')
    need = re.findall(r'<([^>]+)>', _spec)
    if need and not a.args:
        print(f'❌ {verb} 缺参数：需要 {" ".join("<" + n + ">" for n in need)}')
        print(f'   正确用法: tvbox ledger call {verb} ' + ' '.join('<' + n + '>' for n in need))
        print(f'   该动词: {v["desc"]}　→ 底层 tvbox {v["cmd"]}')
        return 2

    # ── 技能名存在性校验（S165）──
    # 实测：`ledger call mine nonexist-skill` → 底层输出「名下欠账：0 条」→ 判 ✅ok。
    # **"没有欠账"和"根本没有这个技能"在结果上完全一样**，而这两件事处置完全不同：
    # 前者可以收工，后者说明我把技能名记错了、这笔账一直没人看。
    # 故凡是第一个参数是技能名的动词，先核一遍目录。
    _skill_first = {'mine', 'claim'}
    if verb in _skill_first and a.args:
        cand = str(a.args[0])
        if cand and not os.path.isdir(os.path.join(SKILLS, cand)):
            print(f'❌ 没有这个技能：{cand}')
            # ★ 建议只给**真的像**的：首版用 `cand.split('-')[0] in d`
            #   于是 `nonexist-skill` 命中了 `skill-evolution`（只因为共享 skill 这个词）。
            #   给错建议比不给更坏——用户会真的去点那个技能，然后又是一次"0 条欠账"的假绿。
            #   改为看首段是否相等（probe-hub vs prob-hub 命中，nonexist-skill 不命中），
            #   或整串包含（tvbox-source vs tvbox-source-hub）。
            near = []
            for d in os.listdir(SKILLS):
                if not os.path.isdir(os.path.join(SKILLS, d)):
                    continue
                if d.split('-')[0] == cand.split('-')[0] or cand in d or d in cand:
                    near.append(d)
                elif _edit1(cand, d):
                    # 只认「一步之差」（少/多/换一个字符）：prob-hub ↔ probe-hub。
                    # 再松就会把 tvbox-inspect-x 也推荐成 tvbox-inspect，
                    # 那种"看起来很像"的建议会把用户带偏到另一个技能上。
                    near.append(d)
            if near:
                print(f'   你是不是想: {" / ".join(sorted(near)[:6])}')
            print(f'   （不存在的技能"名下 0 条欠账"是假绿：不是没欠，是没人认这笔账）')
            return 2

    argv = _tvbox_argv(v['cmd'], a.args)
    meta = BOUNDARIES[bnd]
    if not a.quiet:
        print(f"{meta['icon']} {meta['name']}调用 → 【{v['skill']}】{v['desc']}")
        print(f'   $ tvbox {" ".join(argv)}')
        print('─' * 66)

    r = _invoke(argv, timeout=a.timeout)
    if not a.quiet:
        if r['out']:
            print(r['out'])
        if r['err']:
            print(r['err'], file=sys.stderr)

    verdict = 'ok' if r['ok'] is True else ('fail' if r['ok'] is False else 'unknown')
    if not a.no_book:
        vno, booked, msg = _book(bnd, verb, verdict, r['out'], r['err'],
                                 r['elapsed'], argv, note=a.note)
        _bump('total')
        _bump({'ok': 'ok', 'fail': 'fail', 'unknown': 'unknown'}[verdict])
        if not a.quiet:
            print('─' * 66)
            print(f'📒 入账 {vno} → {meta["acct"]}　'
                  f'{"✅" if booked else "❌ 记账失败：" + msg}')
    if a.quiet:
        # ★ S165：quiet 的语义定为「**只留关键行**」，不是"什么都不打"。
        #   两轮实测把这条逼出来的：
        #     ① 第一版 quiet 把全部输出关掉 → 零字节，这不是安静是哑巴；
        #     ② 第二版只打判定行 → 定时任务拿到了"✅ok"，却看不到捞到了什么，
        #        等于又要人工去重跑一遍——**quiet 就失去了意义**。
        #   现在：判定行 + 原文里的关键行（标题、未完成项、汇总、结论类）。
        #   为什么用"关键行"而不是"前 N 行"：脚本输出长短不一，
        #   截前几行常常只截到标题，真正要看的结论在最后。
        keep = _key_lines(r['out'])
        print(f"{ICON[verdict]} {meta['name']}·{verb} → {verdict}"
              f"（{r['elapsed']:.1f}s，退出码 {r['rc']}）"
              + (f'｜{(r["err"] or "")[:60]}' if r['err'] else ''))
        for ln in keep:
            print(f'   {ln}')
    else:
        print(f'{ICON[verdict]} 判定: {verdict}　（{r["elapsed"]:.1f}s，退出码 {r["rc"]}）')
    return 0 if verdict == 'ok' else 1


# 关键行判据（S165）：命中即保留。宁少勿滥——quiet 的读者是定时任务与串联脚本，
# 它们要的是"这次动了什么、有没有事"，不是完整报告。
_KEY_PAT = None


def _key_lines(text, limit=12):
    """从命令输出里挑出「值得在 quiet 模式下保留」的行。"""
    global _KEY_PAT
    if _KEY_PAT is None:
        import re as _re
        _KEY_PAT = _re.compile(
            r'(═══|未完成事项总账|未结|已结|名下欠账|仍然?未|共\s*\d+|合计|\d+\s*条|'
            r'✅\s*全绿|🟢|🔴|❌|⚠️|入账|删除|新增|跳过|无法判定|缺少|缺少候选)')
    out = []
    for ln in (text or '').splitlines():
        s = ln.rstrip()
        if not s.strip():
            continue
        if _KEY_PAT.search(s):
            out.append(s.strip()[:110])
        if len(out) >= limit:
            out.append('…')
            break
    if not out:
        # 一条都没命中：至少给首行，别让 quiet 变成空白
        head = (text or '').strip().splitlines()
        if head:
            out = [head[0][:110]]
    return out


def _edit1(a, b):
    """a 与 b 是否只差一个字符（增/删/换）。纯标准库，不做模糊匹配库那套。"""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    # 长度差 1：短的插一个字符能否变成长的
    s, l = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(l)):
        if l[:i] + l[i + 1:] == s:
            return True
    return False


def _self_ns(a):
    """把 `ledger call` 的实参转成自省命令需要的 Namespace（补 json/quiet 等缺省）。"""
    ns = type('NS', (), {})()
    ns.json = bool(getattr(a, 'json', False))
    ns.quiet = bool(getattr(a, 'quiet', False))
    return ns


def _guess_boundary(verb):
    """动词 → 默认界（只为少打字；`--boundary` 永远可覆盖）。"""
    if verb in ('debt', 'from-job', 'mine'):
        return 'dialogue'
    if verb in ('done', 'claim'):
        return 'task'
    if verb in ('scan', 'harvest', 'liveness', 'ingest', 'snap'):
        return 'scene'
    return 'eco'


# ---------- 接力：跨场景一条链 ----------
RELAYS = {
    '新源入库': {
        'scene': '找源 → 测活 → 补信息 → 入库 → 快照，一条链走完',
        'steps': [('scan', ['cloud']), ('harvest', ['cloud']),
                  ('ingest', ['--candidates', '<file>']), ('snap', [])],
        'accept': '入库条数 > 0 且快照刷新成功',
    },
    '遗留清理': {
        'scene': '跨对话捞欠账 → 分工确认 → 逐项收口',
        'steps': [('from-job', []), ('mine', ['<技能名>'])],
        'accept': 'close status 该来源归零',
    },
    '生态体检': {
        'scene': '归口确认 → 调用体检 → 账房终验',
        'steps': [('who', ['<意图>']), ('audit-call', [])],
        'accept': 'audit-call 无缺口 且 ledger finalcheck 5/5',
    },
}


def cmd_relay(a):
    name = a.name
    if not name or name not in RELAYS:
        print('🔗 可用的跨场景接力链：\n')
        for k, v in RELAYS.items():
            print(f'  {k:<10} {v["scene"]}')
            for verb, args in v['steps']:
                print(f'      → {verb} {" ".join(args)}'.rstrip())
            print(f'      验收: {v["accept"]}\n')
        if name:
            return 2
        return 0

    ch = RELAYS[name]
    print(f'🔗 接力「{name}」—— {ch["scene"]}')
    print('=' * 66)
    ctx = {}
    results = []
    for verb, args in ch['steps']:
        step_args = [str(x).format(**ctx) if isinstance(x, str) else x for x in args]
        placeholders = [x for x in step_args if x.startswith('<') and x.endswith('>')]
        if placeholders:
            # 占位符步骤**显式跳过**而不是硬跑：`tvbox chain` 在 S023 栽过这个——
            # 找不到文件/空参数，静默失败比报错更坏。
            print(f'⏭️  {verb}: 含占位符 {" ".join(placeholders)}——'
                  f'换成实际值后手动跑 `tvbox ledger call {verb} ...`')
            results.append((verb, 'skip'))
            continue
        ns = type('NS', (), {'verb': verb, 'args': step_args, 'boundary': None,
                             'timeout': a.timeout, 'note': f'relay:{name}',
                             'quiet': False, 'no_book': a.no_book})()
        rc = cmd_call(ns)
        results.append((verb, 'ok' if rc == 0 else 'fail'))
        if rc != 0 and not a.keep_going:
            print(f'\n⛔ 步骤 {verb} 未通过，接力中止（用 --keep-going 强行跑完剩余步骤）')
            break
        print()

    print('=' * 66)
    okn = sum(1 for _, s in results if s == 'ok')
    bad = [(v, s) for v, s in results if s != 'ok']
    print(f'📊 接力「{name}」：{okn} 步通过 / 共 {len(ch["steps"])} 步'
          + (f'　未通过: {" ".join(v for v, _ in bad)}' if bad else ''))
    print(f'✅ 验收判据: {ch["accept"]}')
    return 0 if not bad else 1


# ---------- 调用地图：谁能调谁 / 有无环 / 有无幽灵 ----------
def _route_of_tvbox():
    """从 `/usr/local/bin/tvbox` 读出动词 → 模块路径的映射。

    **不复制**：直接解析入口文件里的 ROUTE 字面量。
    复制一份到账房等于又造一个"要同步的第二处"（M-R38），
    而且会让 `audit-call` 失去意义——它查的正是"两处是否一致"。
    """
    try:
        src = open(TVBOX, encoding='utf-8').read()
    except Exception as e:
        return {}, f'{type(e).__name__}: {e}'
    m = re.search(r'^ROUTE\s*=\s*\{(.*?)^\}', src, re.S | re.M)
    if not m:
        return {}, 'ROUTE 字面量未找到（入口结构变了？）'
    body = m.group(1)
    alias = dict(re.findall(r"^(\w+)\s*=\s*'([^']+)'", src, re.M))
    out = {}
    for verb, mod in re.findall(r"'([\w\-]+)'\s*:\s*(\w+)", body):
        out[verb] = alias.get(mod, mod)
    # ★ S179 修：S165 起的「域命令」不走 ROUTE 字典，而是 `if verb == 'jobs': return cmd_jobs(...)`
    #   特例分支直接分发。只解析 ROUTE 会把它们误报成「幽灵命令」（实测 `tvbox jobs` 明明能跑，
    #   audit-call 却报 2 条幽灵）。这里把 `if verb == 'x':` 分支同样算作入口动词——
    #   判据要覆盖入口的**两种**注册方式，否则检测器自己就是缺陷源。
    for v in re.findall(r"^\s*if\s+verb\s*==\s*['\"]([\w\-]+)['\"]", src, re.M):
        out.setdefault(v, 'inline')
    return out, ''


def _manifest():
    try:
        return json.load(open(MANIFEST, encoding='utf-8')), ''
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def cmd_map(a):
    route, rerr = _route_of_tvbox()
    man, merr = _manifest()
    print('🗺️  调用地图（谁能调谁）\n')
    if rerr:
        print(f'❓ 入口路由读不出（{rerr}）——无法判定，不当作"没有路由"')
        return 1
    print(f'① 入口 `tvbox` 路由：{len(route)} 个动词 → {len(set(route.values()))} 个模块')
    inv = {}
    for v, m in route.items():
        inv.setdefault(m, []).append(v)
    for m, vs in sorted(inv.items()):
        print(f'   {os.path.basename(m):<22} ← {" ".join(sorted(vs))}')

    if merr:
        print(f'\n❓ manifest 读不出（{merr}）——无法判定')
        return 1
    cmds = man.get('commands', [])
    print(f'\n② manifest 登记：{len(cmds)} 条命令、{len(man.get("chains", []))} 条链')
    ghosts, wrong = [], []
    for c in cmds:
        cmd = (c.get('cmd') or '').strip()
        if not cmd.startswith('tvbox '):
            continue
        head = cmd.split()[1]
        if head in ('run', 'p', 'chain', 'manifest', 'contracts', 'help'):
            continue
        if head not in route:
            ghosts.append(cmd)
        elif len(cmd.split()) > 2:
            # 二级动词是否存在，只能靠实跑 —— 这里只查"一级能进得去"
            pass
    if ghosts:
        print(f'   ⚠️ 幽灵命令 {len(ghosts)} 条（manifest 写了，入口没有这个动词）：')
        for g in ghosts[:12]:
            print(f'      {g}')
    else:
        print('   ✅ 无幽灵命令（manifest 每条一级动词都在路由里）')

    print(f'\n③ 账房统一入口：{len(VERBS)} 个调用动词，{len(BOUNDARIES)} 个界，'
          f'{len(RELAYS)} 条接力链')
    for b, meta in BOUNDARIES.items():
        n = sum(1 for k, _ in VERBS.items() if _guess_boundary(k) == b)
        print(f'   {meta["icon"]} {meta["name"]:<8} {n:>2} 个动词　科目: {meta["acct"]}')

    if a.json:
        print('\n--- JSON ---')
        print(json.dumps({'route': route, 'verbs': VERBS, 'ghosts': ghosts},
                         ensure_ascii=False, indent=1))
    return 0


def cmd_audit_call(a):
    """调用体检：三件事各查一遍。
       ① 登记了却没接线（manifest 有、入口无）——幽灵命令
       ② 接了线却没人记账（走 tvbox 跑了，但没有分录）——白干
       ③ 账上记了但命令不存在——账是假的
    """
    route, rerr = _route_of_tvbox()
    man, merr = _manifest()
    print('🩺 调用体检（联动与分工接入）\n')
    problems = []

    if rerr or merr:
        print(f'❓ 读取失败：{rerr or merr}——无法判定（不当作干净）')
        return 1

    # ① 幽灵命令
    ghosts = []
    for c in man.get('commands', []):
        cmd = (c.get('cmd') or '').strip()
        if cmd.startswith('tvbox '):
            head = cmd.split()[1]
            if head not in route and head not in ('run', 'p', 'chain', 'manifest',
                                                  'contracts', 'help'):
                ghosts.append(cmd)
    print(f'① 幽灵命令（登记了但入口没有）: '
          f'{"✅ 无" if not ghosts else f"❌ {len(ghosts)} 条"}')
    for g in ghosts[:10]:
        print(f'      {g}')
        problems.append(f'幽灵命令: {g}')

    # ② 接线了但没人记账（有没有调用分录）
    bal = LED.entry_balances()
    if bal is None:
        print('❓ 总账读取失败——无法判定 ②③（不当作"没有调用"）')
        problems.append('总账读不出，②③ 无法判定')
    else:
        n_call = 0
        for b in BOUNDARIES:
            n_call += sum((bal.get(BOUNDARIES[b]['acct']) or {}).values())
        tot = sum((bal.get(METRICS['total']) or {}).values())
        print(f'② 调用记账（跨界科目合计 vs 指标户）: 科目 {int(n_call)} 笔 / 指标 {int(tot)} 笔')
        if int(tot) == 0:
            print('   ⚠️ 还没有任何经统一入口的调用——机制已就位，尚未被用过')
        elif abs(n_call - int(tot)) > 0.5:
            problems.append(f'② 记账不一致：科目 {int(n_call)} vs 指标 {int(tot)}')

        # ③ 账上记了但命令不存在
        bad = []
        rows, good = L.rows_of(L.DB_TOTAL)
        if not good:
            print('❓ 总账明细读失败——③ 无法判定')
            problems.append('总账明细读不出，③ 无法判定')
        else:
            for r in rows:
                acct = r.get('借方账户') or ''
                if acct not in [BOUNDARIES[b]['acct'] for b in BOUNDARIES]:
                    continue
                note = r.get('备注') or ''
                # 备注里写的是 `verb → tvbox xxx`；取出首词核对仍在路由里
                m2 = re.search(r'→\s*tvbox\s+([\w\-]+)', note)
                if m2 and m2.group(1) not in route and m2.group(1) not in VERBS:
                    bad.append((r.get('凭证号'), m2.group(1)))
            print(f'③ 账上调用（命令是否还在）: {"✅ 全部可查" if not bad else f"❌ {len(bad)} 笔指向不存在的命令"}')
            for vno, v in bad[:8]:
                print(f'      {vno} → {v}')
                problems.append(f'③ {vno} 指向不存在的命令 {v}')

    print()
    if problems:
        print(f'🔴 有 {len(problems)} 处缺口：')
        for p in problems:
            print(f'   · {p}')
        return 1
    print('🟢 三查全过：路由—登记—记账 三方一致')
    return 0


def cmd_metrics(a):
    """调用业态：各界的调用次数 / 成功率（复用指标户，不另算）。"""
    bal = LED.entry_balances()
    if bal is None:
        print('❓ 总账读取失败——无法判定（不当作 0）')
        return 1

    def v(acct):
        return int(sum((bal.get(acct) or {}).values()))

    print('📊 调用业态\n')
    tot, ok, fail, unk = (v(METRICS[k]) for k in ('total', 'ok', 'fail', 'unknown'))
    rate = (ok / tot * 100) if tot else 0
    print(f'  总调用 {tot} 笔　✅{ok}　❌{fail}　❓{unk}　成功率 {rate:.0f}%')
    print()
    print(f'  {"界":<10}{"科目":<14}{"笔数":>6}')
    for b, meta in BOUNDARIES.items():
        print(f"  {meta['name']:<10}{meta['acct']:<14}{v(meta['acct']):>6}")
    absent = [BOUNDARIES[b]['name'] for b in BOUNDARIES if v(BOUNDARIES[b]['acct']) == 0]
    if absent:
        print(f'\n  ⚠️ 从未调过的界：{" / ".join(absent)}——'
              f'机制在，但这条边界还没被真正走过')
    return 0
