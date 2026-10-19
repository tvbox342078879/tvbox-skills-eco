#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""activity.py —— 「我现在在干什么」的可视入口（S177）

## 为什么需要这个文件

用户今晚问了七八次「到哪了」「继续」「咋回事」。这本身说明一件事：
**我放后台跑命令之后，他依然不知道我在做什么。**

M-R120/121/122 解决的是「会话不被锁死」——他能插话了；
但没有解决「他看得见进度」——他还是只能靠问。

本文件补的就是第二个：一条命令把当前状态摊开给他看。

## 数据从哪来（不猜，都读真实文件）

1. 后台任务：解析 bg-tasks 目录下**最近改动**的 .log，报 mtime、大小、尾部输出
2. 会话日志：session_journal.jsonl 最近几条动作
3. 规则门禁：三道钩子是否在册（M-R120/121/122 的执行层）

## 用法

    tvbox now            # 一屏看全
    tvbox now --tail 20  # 后台任务尾部多给几行

## S176c 追加：这个文件自己暴露了本生态的一个通病

接路由时第一次跑 `tvbox now` 就报 `module has no attribute 'dispatch'`，
然后——**它自己没出现在自己的输出里**。查 journal 才发现真相：
tvbox 先把模块输出整个捕获进内存（run_capture），最后才一次性吐到屏幕，
所以 recs 拿到的是本次命令**落笔那一刻**的日志，`now` 那一行根本还没写。

这个现象就是本生态最典型的假绿：**「列表里没有我」被读成了「一切正常」**。
（跟 `sizes` 每格必报、缺格即失败是同一个教训。）

处置：把「本次命令自己」从列表里剔掉，并明写说明——不冒充「没有异常」。

## S176c 追加：selftest —— 门禁宣称必须可证伪

`_hook_state` 只能说「文件+注册」，说不了「真拦得住」。
道门禁的真验证只有一条：**注入坏值看它是否 exit 2，正例看它是否 exit 0**。
`tvbox now --selftest` 现场跑一遍，把「在册」升级成「实测有效」。
"""
import os
import sys
import time
import glob
import json

BG_GLOBS = [
    '/tmp/codebuddy-0/workspace/*/bg-tasks/*.stdout.log',
    '/tmp/codebuddy-*/workspace/*/bg-tasks/*.stdout.log',
]
JOURNAL = '/root/.codebuddy/skills/ecosys-conductor/session_journal.jsonl'
SETTINGS = '/root/.codebuddy/settings.json'
HOOKS = ['/root/.codebuddy/hooks/bg_guard.py',
         '/root/.codebuddy/hooks/block_wait_guard.py']


def _human_age(sec):
    if sec < 60:
        return f'{int(sec)} 秒前'
    if sec < 3600:
        return f'{int(sec/60)} 分钟前'
    return f'{sec/3600:.1f} 小时前'


def _bg_tasks(limit=6):
    """列出最近有动静的后台任务（按产物 mtime 倒序）。"""
    seen = {}
    for g in BG_GLOBS:
        for p in glob.glob(g):
            try:
                st = os.stat(p)
            except OSError:
                continue
            base = p[:-len('.stdout.log')]
            seen[base] = st
    out = []
    now = time.time()
    for base, st in sorted(seen.items(), key=lambda x: -x[1].st_mtime)[:limit]:
        # 完成判定：有 stderr 且 stdout 最后修改时间已过 3 秒，视为已结束（无进程信息可用时的近似）
        # 说明：这里**不假装知道进程是否在跑**——判定不了就标「状态未知」，不猜。
        age = now - st.st_mtime
        out.append({
            'id': os.path.basename(base),
            'path': base + '.stdout.log',
            'bytes': st.st_size,
            'mtime': st.st_mtime,
            'age': age,
        })
    return out


def _journal_tail(n=5):
    """最近 n 条动作，**剔除本次 `now` 自己**。

    剔除不是为了让画面好看，是因为 `now` 的那条记录要等本命令返回后才落笔，
    留在列表里只会是上一次的旧值；而更危险的是**它不在列表里被当成"没有异常"**。
    所以这里显式剔掉 + 在输出里说明，不让「没看到 = 没问题」成立。
    """
    if not os.path.exists(JOURNAL):
        return []
    try:
        lines = open(JOURNAL, encoding='utf-8').read().strip().split('\n')
    except Exception as e:
        return [{'ts': '', 'verb': f'(读取失败 {type(e).__name__})', 'arg': '', 'ok': None}]
    recs = []
    for ln in lines[-max(n * 3, 15):]:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('verb') == 'now':
            continue
        recs.append(r)
    return recs[-n:]


def _hook_state():
    """三道门禁是否在册（执行层实况，不是文档宣称）。

    ★ S177 步3 实测修（M-R60 三态铁律）：`registered` 原来是纯 bool，
      读 SETTINGS 抛异常时 `registered = False` 把**「读不到」折叠成「没注册」**。
      后果：settings.json 编码坏掉 / 权限被改 / 半截 JSON，界面一律显示「未注册」，
      运维照着去加注册，其实注册一直在——**归因指反了比不报更毒**。
      现在三态：True=在册 / False=确未在册 / None=读不到无法判定。
      判定链：`if exists and os.path.exists(SETTINGS)` → 文件不存在本就是 False（真事实，
      「设置文件都没有」确实是未注册）；**只有 try 里抛异常那个分支**才是 None。
    """
    rows = []
    for h in HOOKS:
        exists = os.path.exists(h)
        registered = False
        if exists and os.path.exists(SETTINGS):
            try:
                reg = json.load(open(SETTINGS, encoding='utf-8'))
                txt = json.dumps(reg, ensure_ascii=False)
                registered = os.path.basename(h) in txt
            except Exception:
                registered = None   # 读失败 ≠ 未注册（M-R60），不许折叠
        rows.append((os.path.basename(h), exists, registered))
    return rows


def _hook_selftest():
    """两道门禁的**行为级**验证（不是文件级）。

    返回 [(名称, 说明, ok)]。判定标准：
      - block_wait_guard：TaskOutput+block=true → rc=2；TaskOutput+block=false → rc=0；
                          别的工具 → rc=0（不许误伤）
      - bg_guard        ：只验「文件可执行且能读 stdin 不崩」——它的拦截规则依赖
                          命令文本，喂坏样本容易误判，宁可说「未做行为验证」也不假装通过。
    """
    import subprocess
    rows = []

    BW = '/root/.codebuddy/hooks/block_wait_guard.py'
    def _rc(payload):
        try:
            r = subprocess.run([sys.executable, BW], input=json.dumps(payload),
                               capture_output=True, text=True, timeout=10)
            return r.returncode
        except Exception as e:
            return f'异常:{type(e).__name__}'

    hit = _rc({'tool_name': 'TaskOutput', 'tool_input': {'block': True}}) == 2
    clean = (_rc({'tool_name': 'TaskOutput', 'tool_input': {'block': False}}) == 0
             and _rc({'tool_name': 'Bash', 'tool_input': {'command': 'ls'}}) == 0)
    rows.append(('block_wait_guard.py', '阻塞等待拦得住 / 非阻塞与其它工具不误伤',
                 hit and clean))

    BG = '/root/.codebuddy/hooks/bg_guard.py'
    bg_ok, bg_why = False, ''
    try:
        r = subprocess.run([sys.executable, BG],
                           input=json.dumps({'tool_name': 'Bash',
                                             'tool_input': {'command': 'echo hi'}}),
                           capture_output=True, text=True, timeout=10)
        bg_ok = r.returncode in (0, 2)
        bg_why = f'rc={r.returncode}'
    except Exception as e:
        bg_why = f'{type(e).__name__}: {str(e)[:60]}'
    rows.append(('bg_guard.py', f'可执行且不崩（{bg_why}；拦截规则未做行为验证）', bg_ok))
    return rows


def main(argv):
    tail_n = 12
    if '--tail' in argv:
        i = argv.index('--tail')
        if i + 1 < len(argv):
            try:
                tail_n = int(argv[i + 1])
            except ValueError:
                pass

    print('=' * 62)
    print('  现在在干什么')
    print('=' * 62)

    # ① 后台任务
    tasks = _bg_tasks()
    print('\n① 后台任务（按最近活动排序）')
    if not tasks:
        print('   （暂无落盘的后台任务）')
    for t in tasks:
        print(f"   {t['id']:10} {_human_age(t['age']):10} {t['bytes']:>7} B  {t['path'].split('/')[-1]}")
        # 尾部输出——这是用户唯一能直接看到"跑到哪了"的东西
        try:
            with open(t['path'], encoding='utf-8', errors='replace') as f:
                body = f.read().strip().split('\n')
            for ln in body[-4:]:
                if ln.strip():
                    print(f'       │ {ln[:88]}')
        except Exception as e:
            print(f'       │ (读取失败 {type(e).__name__})')

    # ② 会话最近动作
    print('\n② 最近动作（session_journal）')
    recs = _journal_tail(5)
    if not recs:
        print('   （无记录）')
    for r in recs:
        ok = {True: '✅', False: '❌', None: '?'}.get(r.get('ok'), '?')
        print(f"   {ok} {r.get('ts','')[:19]}  {r.get('verb',''):20} {str(r.get('arg',''))[:30]}")

    # ③ 门禁实况
    print('\n③ 可对话门禁（执行层是否在册）')
    # ★ S177 步3：显示层也必须三态。原来写 `'✅' if exists and reg else '❌'`，
    #   而 _hook_state() 现在会返回 reg=None（读设置失败）——None 是假值，
    #   于是「读不到」又被折叠回「❌ 否」。**改上游不改下游 = 白改。**
    for name, exists, reg in _hook_state():
        if not exists:
            mark = '❌'
        elif reg is None:
            mark = '❓'
        else:
            mark = '✅' if reg else '❌'
        print(f"   {mark} {name:24} 文件={'有' if exists else '缺'} "
              f"注册={'是' if reg is True else '否' if reg is False else '无法判定（设置读不到）'}")
    if '--selftest' in argv:
        print('\n   行为验证（--selftest：注入样本看真拦不拦）')
        for name, why, ok in _hook_selftest():
            print(f"   {'✅' if ok else '❌'} {name:24} {why}")
    else:
        print('   （「在册」≠「拦得住」；要行为级验证加 --selftest）')

    print('\n说明①：后台任务「是否仍在跑」本命令**判定不了**（拿不到进程信息），')
    print('        只看「最后一次有输出的时间」。超 5 分钟无输出 = 大概卡住或已结束。')
    print('说明②：②里的动作**不含本次 now**——它的 journal 要等本命令返回后才落笔。')
    print('        没看到 ≠ 没发生（M-R60 三态不折叠）。')
    return 0


def dispatch(argv, ctx=None):
    """统一入口契约：tvbox 用 dispatch(argv, ctx) 调模块。
    main() 是给人/命令行用的；dispatch 是给路由用的。缺了它 `tvbox now`
    会报 'has no attribute dispatch' —— 路由通了但模块不认，正是 M-R105 那类幽灵。"""
    return main(list(argv or []))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
