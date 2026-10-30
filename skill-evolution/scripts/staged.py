#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第②档主控：受控写入。**默认什么都不做**，必须三件事齐备才动手。

设计原则（这条命令存在的全部理由）：
  它不是"自动改代码的机器人"，是**把改动关进笼子的机关**。
  笼子三道锁：
    ① 白名单：只允许改技能自己的 `scripts/*.py`，绝不碰 SKILL.md / CHANGELOG / 数据文件
    ② 先后顺序：snapshot → 写入 → 语法检查 → 行为等价比对 → 不过就自动回滚
    ③ 任何人都能一键撤销：restore 不依赖本命令是否成功

用法：
  apply_guard.py 已有 snapshot/restore/diff
  staged.py apply <技能> <相对路径> <替换前文本> <替换后文本> [--yes]
      · 不带 --yes 只做预演（dry-run），打印将要改什么，**不动文件**
      · 带 --yes 才真改；改完自动跑等价比对，diff 就回滚

为什么替换用"文本对"而不用行号：行号会漂移，改完一处后面全错位。
"""

import argparse
import os
import py_compile
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import apply_guard as G
import equiv_check as E

# 路径不在这里存副本——每次用时读 G.SKILLS（M-R38）。
# 原来写 `SKILLS = G.SKILLS` 是**导入时求值**，之后 G.SKILLS 变了这里还是老值。
def _skills_root():
    return G.SKILLS


def _workspace():
    """派生数据落点。同样现读，不存快照（见 apply_guard.default_ws 的注释）。"""
    return G.default_ws()

# ① 白名单：只有这些形态允许被改
ALLOW_SUFFIX = ('.py',)
ALLOW_DIRS = ('scripts',)          # 相对技能根的历史
DENY_NAME = ('flow.json', 'monitor_targets.json', 'SKILL.md', 'CHANGELOG.md')


def _check_target(skill, rel):
    """返回 (ok, why)。白名单判定，宁可拒绝。"""
    if not rel.startswith(ALLOW_DIRS[0] + '/'):
        return False, f'不在白名单目录：只允许 {ALLOW_DIRS[0]}/ 下'
    if not rel.endswith(ALLOW_SUFFIX):
        return False, f'只允许改 {ALLOW_SUFFIX}'
    if os.path.basename(rel) in DENY_NAME:
        return False, f'禁止改：{os.path.basename(rel)}'
    if '..' in rel:
        return False, '路径含 ..'
    full = os.path.join(_skills_root(), skill, rel)
    if not os.path.exists(full):
        return False, f'文件不存在：{full}'
    return True, 'ok'


def apply_staged(skill, rel, old_txt, new_txt, yes=False):
    """受控替换。返回退出码：0=成功或已回滚且无残留 / 1=被拦 / 2=失败已回滚"""
    ok, why = _check_target(skill, rel)
    if not ok:
        print(f'⛔ 拒绝：{why}')
        return 1

    full = os.path.join(_skills_root(), skill, rel)
    src = open(full, encoding='utf-8').read()

    n = src.count(old_txt)
    if n == 0:
        print('⛔ 拒绝：替换前文本在文件里找不到（**没改任何东西**）')
        print('   下面是文件里最接近的几行，请核对：')
        # 给出提示而不是要求人自己去翻
        first = old_txt.strip().splitlines()[0][:60] if old_txt.strip() else ''
        for i, ln in enumerate(src.splitlines(), 1):
            if first and first[:20] in ln:
                print(f'   {i}: {ln[:110]}')
        return 1
    if n > 1:
        print(f'⛔ 拒绝：替换前文本出现 {n} 次，不唯一。请带上更长的上下文。')
        return 1

    out = src.replace(old_txt, new_txt, 1)

    print(f'📋 计划：{skill}/{rel}')
    print(f'   替换 1 处（前文本 {len(old_txt)} 字符 → 后文本 {len(new_txt)} 字符）')
    if not yes:
        print('\n【预演模式】未改动任何文件。确认无误后加 --yes 执行。')
        print('--- 将变成 ---')
        for ln in new_txt.splitlines()[:20]:
            print('  ' + ln)
        return 0

    # ② 顺序：先快照（退路），再写
    print('\n① 建快照（退路）…')
    sok, smsg = G.snapshot(skill)
    print('   ', smsg)
    if not sok:
        print('⛔ 快照失败，**不执行写入**')
        return 1

    print('② 写入…')
    open(full, 'w', encoding='utf-8').write(out)

    def _rollback(reason):
        print(f'\n⛔ {reason}')
        print('③ 自动回滚…')
        rok, rmsg = G.restore(skill)
        print('   ', rmsg)
        if not rok:
            # ★ 这是最坏结局：改坏了、退路也没了。必须喊到人看见，不能返回个码了事。
            print('\n' + '=' * 60)
            print('🚨 回滚失败！代码可能已处于损坏状态，需要人工立即介入。')
            print(f'   技能：{skill}')
            print(f'   快照：{G._snap_dir(skill)}')
            print(f'   可手动还原：python3 apply_guard.py restore {skill}')
            print('=' * 60)
            G._log({'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'op': 'ROLLBACK_FAILED',
                    'skill': skill, 'reason': reason})
            return 3
        return 2

    print('③ 语法检查…')
    try:
        py_compile.compile(full, doraise=True)
        print('    ✅ 语法通过')
    except Exception as e:
        return _rollback(f'语法检查未通过：{e}')

    print('④ 行为等价比对…')
    v, det = E.compare(skill)
    print(f'    [{v}] {det}')
    if v == 'same':
        print('\n✅ 通过：语法正常、行为与改前一致')
        print('   （注意：这只证明"没改坏"，不证明"招式真落地了"——后者看 _move_landed）')
        G._log({'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'op': 'apply_ok',
                'skill': skill, 'rel': rel})
        return 0
    if v == 'no_entry':
        print('\n⚠️ 该技能没有登记可跑入口，无法做行为等价比对。')
        print('   只有语法检查通过。是否保留请你判断；不满意可：')
        print(f'     python3 apply_guard.py restore {skill}')
        G._log({'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'op': 'apply_no_entry',
                'skill': skill, 'rel': rel})
        return 0
    return _rollback(f'行为变了（{v}）')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['apply'])
    ap.add_argument('skill')
    ap.add_argument('rel')
    ap.add_argument('old')
    ap.add_argument('new')
    ap.add_argument('--yes', action='store_true')
    a = ap.parse_args()
    sys.exit(apply_staged(a.skill, a.rel, a.old, a.new, a.yes))
