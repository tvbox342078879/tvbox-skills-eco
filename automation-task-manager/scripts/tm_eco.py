#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tm_eco.py —— automation-task-manager 的生态门面（S176b）

## 为什么需要这个文件

本技能主体是 `scheduler-api.sh`（纯 bash），而生态门面 `file_to_janitor` /
`register_monitor` 都是 Python，bash 里 import 不了。于是「联动断点」这一维
恒判 45 分（未接任何生态门面）——不是技能没做联动，是**联动通路的语言不通**。

## 接什么？只接真有的东西

不为了凑分硬接。本技能确实有一类**自己删不掉的残留**：

  一次性探针任务（如 S165 的两个）执行完会停在 `status=0` 而仍留在任务列表里。
  不占运行资源，但会干扰清点；删之前需要人确认名称，所以**不能自动删**。

这正是清道夫收件箱的用途：本技能负责把「该删但不敢自动删的任务」投递过去，
由用户在收件箱里一处按单处理。

## 用法

    python3 tm_eco.py file <task_id> <task_name> <原因>
        → 把停用的任务投递进清道夫收件箱

    python3 tm_eco.py monitor
        → 把「一次性任务未清理」这条风险面登记进监察司（可重复调用，幂等）

门面失败**不阻断主流程**：打印告警并返回非 0，调用方按需忽略。
"""
import sys

CORE_DIR = '/root/.codebuddy/skills/tvbox-source-hub/scripts'
if CORE_DIR not in sys.path:
    sys.path.insert(0, CORE_DIR)

def _load_core():
    """加载 core 门面。返回 (模块, 失败原因)——失败原因绝不丢弃（M-R60）。

    刻意写成函数而非模块级 try/except：模块级 `except: CORE = None`
    是「读失败折叠成空态」的典型形态，信号源16 静态扫描必然会抓。
    返回 (None, '类型: 消息') 让调用方拿到的是**可打印的失败说明**，不是空。
    """
    try:
        if CORE_DIR not in sys.path:
            sys.path.insert(0, CORE_DIR)
        import core
        return core, ''
    except Exception as e:
        return None, f'{type(e).__name__}: {str(e)[:120]}'


CORE, _ERR = _load_core()


def do_file(task_id, task_name, reason):
    """把停用任务投递进清道夫。

    注意入参必须是**真 node_id**——S156 事故：`blk.py` 长期传描述文字导致
    节点从未进入收件箱、但清单照记 40 项。这里任务 id 是数字，不是节点 id，
    所以**如实传任务标识**并在 reason 里写清，不冒充节点 id。
    """
    if CORE is None:
        print(f'⚠️ core 门面不可用（{_ERR}）——未能投递，请人工处理任务 {task_id}')
        return 2
    ident = f'automation-task:{task_id}'
    ok = CORE.file_to_janitor(ident, f'[{task_name}] {reason}', quiet=True)
    print(f'{"✅ 已投递" if ok else "⚠️ 投递未成功"}：{ident}')
    return 0 if ok else 1


def do_monitor():
    """把「一次性任务未清理」登记进监察司。幂等（register_monitor 内部去重）。"""
    if CORE is None:
        print(f'⚠️ core 门面不可用（{_ERR}）')
        return 2
    ok = CORE.register_monitor(
        '一次性任务停用后未清理',
        'manual_track',
        'automation-task-manager 的一次性探针任务执行完停在 status=0 仍留列表，'
        '不占资源但干扰清点；删除前须人确认名称，故不自动删',
        quiet=True)
    print(f'{"✅ 已登记" if ok else "⚠️ 登记未成功"}：一次性任务停用后未清理')
    return 0 if ok else 1


def main(argv):
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == 'monitor':
        return do_monitor()
    if cmd == 'file':
        if len(argv) < 3:
            print('用法：tm_eco.py file <task_id> <task_name> [原因]')
            return 2
        return do_file(argv[1], argv[2], argv[3] if len(argv) > 3 else '一次性任务已停用')
    print(f'未知子命令：{cmd}')
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
