#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mermaid_lint.py — 检查 Markdown 里所有 ```mermaid 块是否含 CSDN 会炸的危险字符。

危险字符（CSDN v11.6.0 节点标签 [] / 边标签 |...| 内会 Parse error，页面显示💣）：
  ( ) " ' + : /
唯独 <br/> 是安全换行；圆柱图 [(...)] 的 () 也会触发，应改普通矩形 [...]。

用法：
  python scripts/mermaid_lint.py 文章.md [更多文件 ...]
  python scripts/mermaid_lint.py *.md
退出码 0 = 全部干净；1 = 有炸弹隐患（并打印 文件名:块号:行号:类型:文本:危险字符）。
"""
import re
import sys
import os

DANGEROUS = set('()"\'+:/')


def lint_file(path):
    problems = []
    txt = open(path, encoding="utf-8").read()
    blocks = re.findall(r"```mermaid\n(.*?)```", txt, re.S)
    for bi, blk in enumerate(blocks, 1):
        for ln, line in enumerate(blk.splitlines(), 1):
            # 节点标签 [ ... ]
            for m in re.finditer(r"\[([^\]]*)\]", line):
                raw = m.group(1)
                seg = raw.replace("<br/>", "")
                off = set(c for c in seg if c in DANGEROUS)
                if "<" in seg or ">" in seg:
                    off.add("<")  # 裸 < > 也会炸（非 <br/> 场景）
                if off:
                    problems.append((path, bi, ln, "NODE", raw, "".join(sorted(off))))
            # 决策节点 {}（菱形）标签同样危险
            for m in re.finditer(r"\{([^{}]*)\}", line):
                raw = m.group(1)
                seg = raw.replace("<br/>", "")
                off = set(c for c in seg if c in DANGEROUS)
                if "<" in seg or ">" in seg:
                    off.add("<")
                if off:
                    problems.append((path, bi, ln, "DECISION", raw, "".join(sorted(off))))
            # 边标签 | ... |
            for m in re.finditer(r"\|([^|]*)\|", line):
                raw = m.group(1)
                seg = raw.replace("<br/>", "")
                off = set(c for c in seg if c in DANGEROUS)
                if "<" in seg or ">" in seg:
                    off.add("<")
                if off:
                    problems.append((path, bi, ln, "EDGE", raw, "".join(sorted(off))))
    return problems


def main():
    if len(sys.argv) < 2:
        sys.exit("用法：python mermaid_lint.py 文章.md [...]")
    allp = []
    for f in sys.argv[1:]:
        if not os.path.exists(f):
            print(f"跳过（不存在）：{f}")
            continue
        allp += lint_file(f)
    if allp:
        print(f"❌ 发现 {len(allp)} 处 mermaid 炸弹隐患：")
        for p in allp:
            print(f"  {os.path.basename(p[0])}:块{p[1]}:行{p[2]}:{p[3]}:{p[4]!r}:危险字符={p[5]}")
        sys.exit(1)
    print("✅ 全部 mermaid 块无危险字符，CSDN 不会炸。")


if __name__ == "__main__":
    main()
