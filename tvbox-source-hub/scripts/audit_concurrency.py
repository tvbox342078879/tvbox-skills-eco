#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_concurrency.py —— M-R114 判据5 的执法工具（生态审计）。

为什么需要它（M-R107 的教训）：
  纪律写在文档里 = 没人看；写在 SKILL.md = 会被绕过。
  唯一有效的方式是**扫出来**：谁没走统一入口，谁在就地拍并发数，一条条列清楚。

检查项：
  ① 直接 import ThreadPoolExecutor / ProcessPoolExecutor 却没用 concurrency 模块
     → 违规：绕过统一入口
  ② max_workers=<硬编码数字>
     → 违规：判据3 禁止就地拍数（必须来自档位表）
  ③ ThreadPoolExecutor(max_workers=None) 或未指定
     → 违规：默认值等于核数×5，等于没管
  ④ 白名单：concurrency.py 自身、外部技能（非本生态）、只读扫描类

退出码：0=全过 1=有违规 2=扫描失败
"""
import argparse
import json
import os
import re
import sys

SKILLS_ROOT = '/root/.codebuddy/skills'

# 白名单：这些文件允许直接用 ThreadPoolExecutor
WHITELIST_FILES = {
    'concurrency.py',                 # 统一入口本身
    'audit_concurrency.py',           # 本审计脚本
}
# 白名单：非本生态（第三方技能），不归 M-R114 管
WHITELIST_PREFIX = (
    'skill_2',        # 外部安装的技能目录
)

PAT_IMPORT = re.compile(r'^\s*(from\s+concurrent\.futures\s+import|import\s+concurrent\.futures)')
PAT_MP = re.compile(r'^\s*(import\s+multiprocessing|from\s+multiprocessing\s+import)')
PAT_POOL = re.compile(r'(ThreadPoolExecutor|ProcessPoolExecutor)\s*\(')
PAT_HARD = re.compile(r'max_workers\s*=\s*(\d+)')
# 合规形态：并发数来自档位表（resolve_workers/TIERS）或显式变量，不是就地拍的常量
PAT_TIERED = re.compile(r'max_workers\s*=\s*(_?resolve_workers|_?TIERS|_w\b|_workers\b|w\b)')


def iter_py(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in
                       ('__pycache__', '.git', 'node_modules', '.venv', 'venv')]
        rel = os.path.relpath(dirpath, root)
        if rel != '.' and rel.split(os.sep)[0].startswith(WHITELIST_PREFIX):
            continue
        for fn in filenames:
            if fn.endswith('.py'):
                yield os.path.join(dirpath, fn)


def scan_file(path):
    """→ list of {level, code, line, text, why}"""
    name = os.path.basename(path)
    if name in WHITELIST_FILES:
        return []
    try:
        with open(path, encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        return [{'level': 'ERROR', 'code': 'READ', 'line': 0,
                 'text': '', 'why': f'读失败：{e}'}]

    src = ''.join(lines)
    # ★ P017/S163 修：**取消"整文件放行"**。
    #   旧写法 `uses_unified = 'from concurrency import' in src` 是**文件级**判据——
    #   只要文件里出现过一次 import，判据① 就对该文件**整体失效**。实测后果：
    #   probe.py 引了 concurrency（为了 resolve_workers），于是里面 5 处裸 ThreadPoolExecutor
    #   一处也没被报出来，S111 就是这样潜伏了整整一个迭代。
    #
    #   新判据：**逐处查**。裸池（构造函数里没走档位表）一律报，无论文件别处有没有 import。
    #   "引了统一入口"只证明作者知道规矩，不证明这一处用了规矩。
    findings = []

    # 判据① 用 AST：**只报"真实构造了池"的地方**，不报 import 语句。
    #   理由：import 本身不是违规，`from concurrent.futures import ThreadPoolExecutor`
    #   常常只为在 `_mk_pool()` 里取档位表上限（那是对的）。按 import 行报会全员误伤。
    #   但取消文件级放行后，"构造点"必须逐处核——这才是 S111 真正漏掉的。
    ctor_lines = _pool_ctor_lines(src)

    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if s.startswith('#'):
            continue
        if i in ctor_lines:
            findings.append({
                'level': 'VIOLATION', 'code': 'M-R114-①',
                'line': i, 'text': s[:90],
                'why': '构造线程/进程池（判据1/5：必须走 concurrency.run_bounded）'})
        if PAT_POOL.search(ln):
            m = PAT_HARD.search(ln)
            if m:
                findings.append({
                    'level': 'VIOLATION', 'code': 'M-R114-③',
                    'line': i, 'text': s[:90],
                    'why': f'就地拍并发数 max_workers={m.group(1)}，必须走档位表（判据3）'})
            elif 'max_workers' not in ln and ')' not in ln.split('(', 1)[-1]:
                findings.append({
                    'level': 'WARN', 'code': 'M-R114-③',
                    'line': i, 'text': s[:90],
                    'why': '未显式指定 max_workers（可能跨行），需人工确认是否来自档位表'})
    return findings


def _pool_ctor_lines(src):
    """AST 找出**真正构造** ThreadPoolExecutor/ProcessPoolExecutor 的行号集合。

    只看 import 不看调用 → 会漏（S111 就是这么漏的）；
    按 import 行报 → 会全员误伤（import 常是给 _mk_pool 取档位上限用）。
    唯一准确的口径是：谁在代码里 `XxxPoolExecutor(...)` 了。
    """
    import ast as _ast
    _NAMES = ('ThreadPoolExecutor', 'ProcessPoolExecutor')
    out = set()
    try:
        tree = _ast.parse(src)
    except SyntaxError:
        # 语法都不对，AST 判不了 → 退回正则（宁可多报，不可漏报）
        return {i for i, ln in enumerate(src.splitlines(), 1)
                if PAT_POOL.search(ln) and not ln.strip().startswith('#')}
    for n in _ast.walk(tree):
        if not isinstance(n, _ast.Call):
            continue
        f = n.func
        nm = getattr(f, 'id', None) or getattr(f, 'attr', None)
        if nm in _NAMES:
            out.add(getattr(n, 'lineno', 0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--strict', action='store_true', help='WARN 也算失败')
    a = ap.parse_args()

    files = list(iter_py(SKILLS_ROOT))
    results = {}
    total_v = total_w = 0
    for p in files:
        fs = scan_file(p)
        if fs:
            rel = os.path.relpath(p, SKILLS_ROOT)
            results[rel] = fs
            total_v += sum(1 for f in fs if f['level'] == 'VIOLATION')
            total_w += sum(1 for f in fs if f['level'] in ('WARN', 'ERROR'))

    if a.json:
        print(json.dumps({'scanned': len(files), 'violations': total_v,
                          'warnings': total_w, 'files': results},
                         ensure_ascii=False, indent=1))
    else:
        print(f'扫描 {len(files)} 个 .py 文件（M-R114 并发审计）\n')
        if not results:
            print('✅ 全部走统一入口，无违规')
        for rel, fs in sorted(results.items(), key=lambda x: -len(x[1])):
            bad = [f for f in fs if f['level'] == 'VIOLATION']
            print(f'{"❌" if bad else "⚠️"} {rel}')
            for f in fs:
                mark = '  ❌' if f['level'] == 'VIOLATION' else '  ⚠️'
                print(f'{mark} L{f["line"]:<4} [{f["code"]}] {f["why"]}')
                print(f'        {f["text"]}')
        print(f'\n汇总：违规 {total_v} 处 / 待确认 {total_w} 处 / 涉及 {len(results)} 个文件')

    if total_v:
        return 1
    if a.strict and total_w:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
