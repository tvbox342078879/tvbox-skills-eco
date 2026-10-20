#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第②档安全底座：改动前快照 / 改动后回滚 / 沙箱试跑。

为什么必须独立成一个文件：第②档要给它"写代码的权限"。权限本身不危险，
**没有退路才危险**。所以顺序必须是——先把退路造好，再给权限。

三条能力（每一条都只做一件事）：
  1. snapshot(skill)   把技能目录完整复制到补丁工作区（含 .py/.md/子目录）
  2. restore(skill)    从快照完整还原；还原后回读校验 sha，不一致就报错、不许装成没事
  3. diff(skill)       列出"相对快照改了什么"（文件级 + 行数），供人审
  4. clean([skill])    只清过程残留（pre_restore_*），不动 snapshot / baseline

设计约束：
  · 快照与还原**只碰技能自己的目录**，绝不跨技能
  · 还原前先把当前状态另存为 .pre_restore（防止"回滚本身把新东西弄丢"）
  · 所有操作打日志到 snapshot/_ops.jsonl，谁改的、改了什么、什么时候
"""

import hashlib
import json
import os
import shutil
import time

SKILLS = '/root/.codebuddy/skills'
WS = os.path.join(SKILLS, 'skill-evolution', 'upgrade_patches', '_work')

# ★ M-R38 单一事实源：路径只在**这一处**存副本，别人一律调函数现读。
#   实测踩到：evolve.py 顶部也写了一份 SKILLS='/root/.codebuddy/skills'，
#   测试把本模块的 SKILLS 指到临时目录，evolve 仍去真实目录找文件 → 测试假通过。
#   ★ 而且这里必须保留模块级名字（WS），因为有测试要 monkeypatch 它；
#   所以别人要用 `default_ws()` 而不是 `G.WS` 快照式取值。


def _sha(p):
    h = hashlib.sha1()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _tree(root):
    """根目录下所有文件 → {相对路径: sha}。跳过 __pycache__ 与快照目录自身。"""
    out = {}
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', '_ops_backup')]
        for f in files:
            if f.endswith('.pyc'):
                continue
            p = os.path.join(r, f)
            out[os.path.relpath(p, root)] = _sha(p)
    return out


def _log(rec):
    os.makedirs(WS, exist_ok=True)
    with open(os.path.join(WS, '_ops.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def _snap_dir(skill):
    return os.path.join(WS, skill, 'snapshot')


def snapshot(skill):
    """建基线快照。返回 (ok, msg)。重复调用会覆盖旧快照——但先把旧的存成 .prev。"""
    src = os.path.join(SKILLS, skill)
    if not os.path.isdir(src):
        return False, f'技能目录不存在：{src}'
    dst = _snap_dir(skill)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isdir(dst):
        prev = dst + '.prev'
        if os.path.isdir(prev):
            shutil.rmtree(prev)
        shutil.move(dst, prev)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    files = _tree(dst)
    _log({'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'op': 'snapshot',
          'skill': skill, 'n_files': len(files)})
    return True, f'已建快照：{len(files)} 个文件 → {dst}'


def restore(skill):
    """从快照完整还原，并回读校验。返回 (ok, msg)。
    ★ 还原也要回读——"回滚成功了"同样不许自称。"""
    snap = _snap_dir(skill)
    if not os.path.isdir(snap):
        return False, f'无快照可还原：{snap}（先跑 snapshot）'
    live = os.path.join(SKILLS, skill)
    if not os.path.isdir(live):
        return False, f'技能目录不存在（无法还原）：{live}'

    # 先把现状备份，防"回滚把新东西弄丢"
    # ★★ 实测踩到：备份目录名粒度只到秒，同一秒内连续两次回滚 →
    #   copytree 撞上已存在的目录 → FileExistsError → **回滚自己崩了**。
    #   这是最坏的形态：代码已经改坏，退路却在关键时刻断掉。
    #   修法：名字加 pid + 序号，且目录已存在就往后顺延，绝不覆盖别人的备份。
    _base = os.path.join(WS, skill, 'pre_restore_%s_%d'
                         % (time.strftime('%Y%m%d_%H%M%S'), os.getpid()))
    bak, _n = _base, 0
    while os.path.exists(bak):
        _n += 1
        bak = f'{_base}_{_n}'
    os.makedirs(os.path.dirname(bak), exist_ok=True)
    _backup_ok = True
    # ★ S182：`_backup_err` 原先只在 except 里赋值，靠"三元表达式只求值一边"侥幸不炸。
    #   但这是**隐式依赖求值顺序**——任何人把第 145 行的三元改成 if/else 分支，
    #   备份成功那条路立刻 UnboundLocalError，而且发生在"还原已经做完、只差打日志"时，
    #   等于把一次成功的还原报成失败。先给默认值，不靠求值顺序活着。
    _backup_err = ''
    try:
        shutil.copytree(live, bak, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    except Exception as e:
        # 备份失败**不能**就此不还原——那等于"退路坏了干脆不退了"。
        # 仍然还原，只是少一层保险，并明确告警。
        _backup_ok = False
        _backup_err = f'{type(e).__name__}: {e}'

    want = _tree(snap)
    # 删掉快照里没有的文件（只删技能目录内的，且跳过缓存）
    for r, dirs, files in os.walk(live):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            if f.endswith('.pyc'):
                continue
            p = os.path.join(r, f)
            rel = os.path.relpath(p, live)
            if rel not in want:
                os.remove(p)
    # 拷回
    for rel in want:
        s = os.path.join(snap, rel)
        d = os.path.join(live, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)

    # ★ 回读校验
    got = _tree(live)
    bad = [k for k in want if got.get(k) != want[k]]
    extra = [k for k in got if k not in want]
    _log({'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'op': 'restore',
          'skill': skill, 'mismatch': len(bad), 'extra': len(extra),
          'backup': os.path.relpath(bak, WS)})
    if bad or extra:
        return False, (f'❌ 还原后回读不一致：{len(bad)} 个文件内容不符、{len(extra)} 个多余。'
                       f'现状已备份到 {bak}')
    _tail = '' if _backup_ok else f' ⚠️ 备份失败（{_backup_err}），本次无"改前状态"留档'
    return True, f'✅ 已还原 {len(want)} 个文件，回读校验一致{_tail}'


def default_ws():
    """默认工作区：所有派生数据（快照 / 行为基线 / 改前备份）都落在这里。

    ★ 为什么要有这个函数：三个脚本原本各写一份 WS 常量。测试里只改其中一个，
      其余两个仍指真实目录 → 测试替身只挡住一部分副作用 → **测试假通过**。
      这是 M-R38 单一事实源的活标本，所以改成只有这一处定义，别人调它。
    """
    return WS


def clean(skill=None):
    """清理过程残留：改前备份目录（pre_restore_*）。

    为什么不删 snapshot：快照是**退路**。清掉它，第②档下一次出错就没得回滚。
    为什么不删 baseline.json：那是行为基线，删了下次等价校验就无从对比。
    只删 pre_restore_* 与 *.prev —— 这些是"某一次操作前顺手存的"，事后无用。
    """
    targets = []
    if skill:
        base = os.path.join(WS, skill)
        if os.path.isdir(base):
            targets = [(base, n) for n in os.listdir(base)
                       if n.startswith('pre_restore_') or n == 'snapshot.prev']
    else:
        for sk in sorted(os.listdir(WS)) if os.path.isdir(WS) else []:
            base = os.path.join(WS, sk)
            if not os.path.isdir(base):
                continue
            targets += [(base, n) for n in os.listdir(base)
                        if n.startswith('pre_restore_') or n == 'snapshot.prev']
    n, freed = 0, 0
    for base, name in targets:
        p = os.path.join(base, name)
        freed += sum(os.path.getsize(os.path.join(r, f))
                     for r, _, fs in os.walk(p) for f in fs)
        shutil.rmtree(p, ignore_errors=True)
        n += 1
    _log({'ts': time.strftime('%Y-%m-%d %H:%M:%S'), 'op': 'clean',
          'skill': skill or '*', 'n_dirs': n})
    return True, f'已清理 {n} 个过程残留目录（{freed // 1024}K）；snapshot 与 baseline.json 保留'


def diff(skill):
    """相对快照改了什么。返回 (ok, {added, removed, changed})。"""
    snap = _snap_dir(skill)
    live = os.path.join(SKILLS, skill)
    if not os.path.isdir(snap):
        return False, {'error': f'无快照：{snap}'}
    a, b = _tree(snap), _tree(live)
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = sorted(k for k in a if k in b and a[k] != b[k])
    return True, {'added': added, 'removed': removed, 'changed': changed}


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('用法: apply_guard.py snapshot|restore|diff <技能名>')
        print('      apply_guard.py clean [技能名]   # 只清过程残留，不动 snapshot/baseline')
        sys.exit(2)
    op = sys.argv[1]
    sk = sys.argv[2] if len(sys.argv) > 2 else None
    if op == 'clean':
        ok, m = clean(sk); print(m); sys.exit(0 if ok else 1)
    if not sk:
        print('用法: apply_guard.py snapshot|restore|diff <技能名>')
        sys.exit(2)
    if op == 'snapshot':
        ok, m = snapshot(sk); print(m); sys.exit(0 if ok else 1)
    if op == 'restore':
        ok, m = restore(sk); print(m); sys.exit(0 if ok else 1)
    if op == 'diff':
        ok, d = diff(sk)
        if not ok:
            print(d['error']); sys.exit(1)
        print(f"新增 {len(d['added'])} / 删除 {len(d['removed'])} / 修改 {len(d['changed'])}")
        for k in d['added']:
            print('  + ' + k)
        for k in d['removed']:
            print('  - ' + k)
        for k in d['changed']:
            print('  ~ ' + k)
        sys.exit(0)
    print('未知操作'); sys.exit(2)
