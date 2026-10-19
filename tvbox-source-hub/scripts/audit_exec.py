#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_exec.py —— M-R115 拖进程四禁的静态审计（S111）。

原 audit_concurrency.py 只管 M-R114（怎么并）。本脚本补齐 M-R115（怎么不拖）：
扫全生态 .py，检查四类可静态发现的拖进程风险。

  D1 无界网络调用   requests/urlopen 不带 timeout → 可能永久挂起
  D2 无界循环       while True 且循环体内无 break/return/上限判断
  D3 无界等待       time.sleep 参数是变量且看不到上限
  D4 分页无闸门     出现 cursor/page 循环但未使用 guard.fetch_paged

用法：
    python3 audit_exec.py                # 全生态
    python3 audit_exec.py --json         # 机器可读
    python3 audit_exec.py --skill probe-hub

退出码：0 无违规 / 1 有违规（CI 可用）
"""
import argparse
import json
import os
import re
import sys

ECO_SKILLS = [
    'ecosys-conductor', 'tvbox-source-hub', 'tvbox-inspect', 'tvbox-interface',
    'tvbox-source-blacklist', 'probe-hub', 'legacy-watchdog', 'skill-evolution',
    'tvbox-janitor', 'unfinished-audit', 'eco-ledger',
]

# 本文件与被审工具自身豁免
SELF_EXEMPT = {'audit_exec.py', 'audit_concurrency.py', 'guard.py'}

NET_RE = re.compile(r'\b(requests\.(get|post|put|head)|urlopen|httpx\.(get|post))\s*\(')
CURSOR_RE = re.compile(r'(cursor|page|offset|next_page)\s*=', re.I)
PAGED_RE = re.compile(r'fetch_paged\s*\(')
HEARTBEAT_RE = re.compile(r'(heartbeat|hb\.tick|run_bounded\([^)]*heartbeat)')
SELECT_RE = re.compile(r'batch_update|add_records|add_records_parallel')


def scan_file(path):
    """返回 [(code, line_no, detail)]。**读不到文件返回特殊标记，不返回 []**。

    ★ M-R60：原写 `except: return []` —— 读不了的文件在审计器眼里长得和
      "四禁全过"完全一样。一个**审计器**对读不到的文件发绿牌，比不审计更危险：
      报告上"四禁零违规"的结论会覆盖掉那些根本没被读到的文件。
      改为返回 D0 标记（无法审计），让调用方计入 findings 而不是静默跳过。
    """
    try:
        src = open(path, encoding='utf-8', errors='replace').read()
    except Exception as e:
        return [('D0', 1, f'文件无法读取，未审计：{str(e)[:80]}')]
    lines = src.split('\n')
    out = []

    # D1：网络调用无 timeout（同一调用可能跨行，看调用点后 3 行内有无 timeout）
    for i, ln in enumerate(lines):
        if NET_RE.search(ln) and not ln.lstrip().startswith('#'):
            window = '\n'.join(lines[i:i + 3])
            if 'timeout' not in window:
                out.append(('D1', i + 1, ln.strip()[:90]))

    # D2：while True 无出口
    for i, ln in enumerate(lines):
        if re.search(r'while\s+True\s*:', ln):
            # 找循环体（粗略：直到缩进回退）
            base = len(ln) - len(ln.lstrip())
            body = []
            for j in range(i + 1, min(i + 80, len(lines))):
                nxt = lines[j]
                if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= base:
                    break
                body.append(nxt)
            blob = '\n'.join(body)
            if not re.search(r'\b(break|return|raise|sys\.exit)\b', blob):
                out.append(('D2', i + 1, ln.strip()[:90]))

    # D3：sleep 变量参数——**只作提示**，不算违规（S111 核实：core.py 的 sleep 是
    #     形参带默认值 0.3/0.6，probe.py 的 _wait 遍历固定元组 (0,1,3)，均有界）。
    for i, ln in enumerate(lines):
        m = re.search(r'time\.sleep\(\s*([A-Za-z_][\w\.]*)\s*\)', ln)
        if m and not ln.lstrip().startswith('#'):
            var = m.group(1)
            # 同文件内若该变量在形参里有默认值，或来自固定元组/常量，视为有界
            # S176 补漏：原只认 `enumerate((`，漏了同样有界的 `for i in range(N)`。
            # 实测 version_guard.py 的 `time.sleep(i)` 外层就是 `for i in range(tries)`，
            # 天然有界（最多 tries-1 秒），却被判成 D3 无界等待——**判据比代码更粗**
            # 会制造假违规，假的多了真违规就没人看（同 M-R34：假阳性会让人忽略告警）。
            bounded = (re.search(rf'def\s+\w+\([^)]*\b{var}\s*=\s*[\d.]', src) or
                       re.search(rf'in\s+enumerate\(\s*\(', src) or
                       re.search(rf'\bfor\s+{var}\s+in\s+range\(', src) or
                       re.search(rf'\b{var}\s*=\s*[\d.]+', src))
            if not bounded:
                out.append(('D3', i + 1, ln.strip()[:90]))

    # D4：有分页迹象但既没用 fetch_paged，也没有「游标不前进即退出」断言
    #     识别四种等价写法（S111 逐个核实后的判据）：
    #       a) fetch_paged(...)                      统一入口
    #       b) nxt == cursor / cur == cursor         自我比较
    #       c) guard > N / 页数上限 break             轮次硬上限
    #       d) for _ in range(N) 包裹分页循环          有界遍历
    if CURSOR_RE.search(src) and re.search(r'while\s', src):
        has_guard = (PAGED_RE.search(src) or
                     re.search(r'(\w+)\s*==\s*(cur|cursor|last|prev|nxt)\b', src) or
                     re.search(r'(guard|pages|seen)\s*[><]=?\s*\d+', src) or
                     re.search(r'for\s+\w+\s+in\s+range\(\s*\d{2,}\s*\)', src) or
                     re.search(r'游标|不前进', src))
        if not has_guard:
            out.append(('D4', 1, '有分页循环但未见 fetch_paged / 游标前进断言 / 轮次上限'))

    # D5：调 query_database_record 却完全不分页（无 start-cursor / has_more）
    #     这不一定是 bug——"只取一页够用"（如查台账 6 条、探活判库能否读）合法。
    #     但必须能自证：page-size 要显著大于预期条数，或注释写明"只需一页"。
    if 'query_database_record' in src and 'has_more' not in src and 'start-cursor' not in src:
        m = re.search(r"--page-size['\"]?\s*,\s*['\"](\d+)", src)
        size = int(m.group(1)) if m else 0
        self_documented = bool(re.search(r'只需一页|一页够|仅取首|只需首|single.page', src))
        if not self_documented and size < 200:
            out.append(('D5', 1,
                        f'调分页接口但不分页，page-size={size} 且未注明"只需一页"'))

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--skill', default=None)
    ap.add_argument('--root', default=os.path.expanduser('~/.codebuddy/skills'))
    args = ap.parse_args()

    skills = [args.skill] if args.skill else ECO_SKILLS
    findings, n_files = [], 0
    for sk in skills:
        base = os.path.join(args.root, sk)
        if not os.path.isdir(base):
            continue
        for dirpath, _, files in os.walk(base):
            for f in sorted(files):
                if not f.endswith('.py') or f in SELF_EXEMPT:
                    continue
                p = os.path.join(dirpath, f)
                n_files += 1
                for code, ln, detail in scan_file(p):
                    findings.append({'skill': sk, 'file': os.path.relpath(p, args.root),
                                     'line': ln, 'code': code, 'detail': detail})

    by_code = {}
    for f in findings:
        by_code.setdefault(f['code'], []).append(f)

    if args.json:
        print(json.dumps({'files': n_files, 'total': len(findings),
                          'by_code': {k: len(v) for k, v in by_code.items()},
                          'findings': findings}, ensure_ascii=False, indent=2))
        return 1 if findings else 0

    LABEL = {'D1': '无界网络调用（缺 timeout）', 'D2': '无界循环（无 break/return）',
             'D3': '无界等待（sleep 变量）', 'D4': '分页无游标闸门',
             'D5': '调分页接口但未分页'}
    print(f'=== M-R115 拖进程审计（{n_files} 个 .py）===')
    if not findings:
        print('  ✅ 四禁零违规')
        return 0
    for code in ('D1', 'D2', 'D3', 'D4', 'D5'):
        items = by_code.get(code, [])
        print(f'\n{code} {LABEL[code]}：{len(items)} 处')
        for it in items[:12]:
            print(f"  {it['file']}:{it['line']}  {it['detail'][:78]}")
        if len(items) > 12:
            print(f'  … 另 {len(items) - 12} 处')
    print(f'\n合计 {len(findings)} 处（D3 多为有界退避，需人工确认上限）')
    return 1


if __name__ == '__main__':
    sys.exit(main())
