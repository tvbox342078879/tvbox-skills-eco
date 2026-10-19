#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主题自检器 —— gzh-typeset

用途：扩展或修改主题后，校验 references/ 下各文件之间的一致性，
      以及文档里所有 HTML 代码块本身是否满足微信红线。

检查项：
  1. theme-index.md 声明的主题 与 theme-*.md 文件一一对应
  2. 每个主题文件恰好 18 个组件、且含「骨架 / 配方 / Markdown 映射」三节
  3. 关键词下划线的权威值在 index 与主题文件之间一致
  4. index 权威值表里声明的色值，主题文件里确实存在（防止改了一处忘了另一处）
  5. 跨主题之间主色 / 点睛色不得重复
  6. 所有 ```html 代码块满足微信红线
     （在主题文件任意位置写 <!-- lint-allow: 规则名 --> 可豁免该文件下的指定规则，
       用于 float 首字下沉等「仅本地预览可用」的已声明例外）
  7. common-components.md 用到的 {{占位符}} 都在变量表里声明过

用法：
    python scripts/lint_themes.py [skill根目录]

退出码：0 通过 / 1 存在 ERROR
"""

import argparse
import os
import re
import sys
from collections import defaultdict

# ------------------------------------------------- 代码块红线规则

FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.S)

CODE_RULES = [
    (re.compile(r"class\s*="), "class", "ERROR",
     "class 属性在公众号内被剥离，全部禁止"),
    (re.compile(r"<div[\s>]"), "div", "ERROR",
     "用 <section> 代替 <div>"),
    (re.compile(r"<table[\s>]"), "table", "ERROR",
     "用 flex 仿表格代替 <table>"),
    (re.compile(r"display\s*:\s*grid"), "grid", "ERROR",
     "display:grid 在微信内不稳定，改用 display:flex"),
    (re.compile(r"position\s*:\s*(fixed|absolute|sticky)"), "position", "ERROR",
     "定位属性在公众号内失效"),
    (re.compile(r"@media"), "media", "ERROR",
     "@media 在内联样式中不生效"),
    (re.compile(r"var\s*\(\s*--"), "cssvar", "ERROR",
     "CSS 变量不支持，写死色值"),
    (re.compile(r"white-space\s*:\s*pre"), "pre", "WARN",
     "white-space:pre 不可靠，代码缩进改用逐行 <p> + &nbsp;"),
    (re.compile(r"float\s*:"), "float", "WARN",
     "float 会被剥离导致串行，确认这是本地预览专用组件"),
    (re.compile(r"box-shadow"), "shadow", "WARN",
     "阴影在部分机型渲染不一致，仅 sand-magazine 允许极轻暖阴影"),
]

IMG_RE = re.compile(r"<img\b[^>]*>")
# 只取 ASCII 标识符部分，后面可以跟中文说明（遇到中文或 --> 即停止）
ALLOW_RE = re.compile(r"<!--\s*lint-allow\s*:\s*([A-Za-z_,\s]+)")
EMPTY_LEAF_RE = re.compile(r'<span leaf="">\s*</span>')

LEVEL_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2}


# ------------------------------------------------- 工具

def read(path):
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def backticked(line):
    m = re.findall(r"`([^`]+)`", line)
    return m[-1].strip() if m else None


def hexes(text):
    return set(h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}\b", text))


# ------------------------------------------------- 主检查

def main():
    ap = argparse.ArgumentParser(description="主题自检")
    ap.add_argument("root", nargs="?", default=None, help="skill 根目录（默认脚本上级目录）")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    root = args.root or os.path.dirname(here)
    ref_dir = os.path.join(root, "references")
    if not os.path.isdir(ref_dir):
        print("找不到 references/ 目录：%s" % ref_dir)
        return 2

    index_txt = read(os.path.join(ref_dir, "theme-index.md"))
    if index_txt is None:
        print("缺少 references/theme-index.md")
        return 2

    issues = []  # (level, where, msg)

    # common-components.md 提前读入：CODE_* 色值与占位符都在这里比对
    cc_txt = read(os.path.join(ref_dir, "common-components.md"))

    # --- 1. 主题清单对应 ---
    index_ids = re.findall(r"^\|\s*`([a-z0-9-]+)`\s*\|", index_txt, re.M)
    theme_files = {}
    for fn in sorted(os.listdir(ref_dir)):
        m = re.match(r"^theme-([a-z0-9-]+)\.md$", fn)
        if m and m.group(1) != "index":
            theme_files[m.group(1)] = os.path.join(ref_dir, fn)

    for tid in index_ids:
        if tid not in theme_files:
            issues.append(("ERROR", "theme-index.md",
                           "索引里声明了主题 `%s`，但缺少 references/theme-%s.md" % (tid, tid)))
    for tid in theme_files:
        if tid not in index_ids:
            issues.append(("ERROR", "theme-%s.md" % tid,
                           "主题文件存在，但 theme-index.md 的速览表里没有登记"))

    # --- 解析 index 的权威值表 ---
    sections = re.split(r"^###\s+", index_txt, flags=re.M)
    index_vals = {}
    for sec in sections:
        head = sec.split("\n", 1)[0].strip()
        tid = head.split()[0] if head else ""
        if tid not in theme_files:
            continue
        d = {}
        for line in sec.split("\n"):
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            key, val = cells[0], cells[1]
            hv = re.search(r"#[0-9A-Fa-f]{6}\b", val)
            if hv:
                d[key] = hv.group(0).upper()
            if "关键词下划线" in key:
                d["__underline__"] = backticked(line) or ""
            if "组件文件" in key:
                d["__file__"] = val
        index_vals[tid] = d

    # --- 5. 跨主题主色唯一性 ---
    seen = defaultdict(list)
    for tid, d in index_vals.items():
        for key, val in d.items():
            if key.startswith("__"):
                continue
            if "PRIMARY" in key or "ACCENT" in key or "主色" in key or "点睛" in key:
                seen[val].append("%s:%s" % (tid, key))
    for val, owners in seen.items():
        if len(owners) > 1:
            issues.append(("ERROR", "theme-index.md",
                           "色值 %s 被多个主题复用（%s）—— 主题之间必须视觉可区分"
                           % (val, "、".join(owners))))

    # --- 2~4 & 6. 逐个主题文件 ---
    for tid, path in theme_files.items():
        txt = read(path)
        if txt is None:
            continue
        fname = os.path.basename(path)
        allowed = set()
        for m in ALLOW_RE.finditer(txt):
            allowed.update(x.strip() for x in m.group(1).split(",") if x.strip())

        # 组件数量
        n = len(re.findall(r"^## 组件\s", txt, re.M))
        if n != 18:
            issues.append(("ERROR", fname, "组件数量 %d 个，规范要求 18 个" % n))

        # 必备小节
        for sec in ("完整文章模板骨架", "文章类型 → 组件组合配方"):
            if sec not in txt:
                issues.append(("ERROR", fname, "缺少小节「%s」" % sec))
        if not re.search(r"^## Markdown →", txt, re.M):
            issues.append(("WARN", fname, "缺少「Markdown → 主题」映射表"))

        # 下划线权威值一致
        idx_u = index_vals.get(tid, {}).get("__underline__")
        m = re.search(r"\*\*正文关键词下划线\*\*[^\n]*", txt)
        file_u = backticked(m.group(0)) if m else None
        if idx_u is None:
            issues.append(("WARN", "theme-index.md", "主题 `%s` 的权威值表里缺少「关键词下划线」行" % tid))
        elif file_u is None:
            issues.append(("WARN", fname, "主题文件里找不到「正文关键词下划线」声明"))
        elif idx_u != file_u:
            issues.append(("ERROR", fname,
                           "下划线权威值与 theme-index 不一致：\n      文件：%s\n      索引：%s"
                           % (file_u, idx_u)))

        # 色值确实存在于主题文件
        # CODE_* 属通用件范畴，色值定义在 common-components.md，改去那里比对
        file_hex = hexes(txt)
        cc_hex = hexes(cc_txt or "")
        for key, val in index_vals.get(tid, {}).items():
            if key.startswith("__"):
                continue
            is_code = "CODE_" in key          # 代码区色值定义在 common-components.md
            pool = cc_hex if is_code else file_hex
            if val not in pool:
                issues.append(("WARN", fname,
                               "theme-index 声明的「%s = %s」在%s里找不到，可能已改动未同步"
                               % (key, val,
                                  "common-components.md" if is_code else "主题文件")))

        # 代码块红线
        for fm in FENCE_RE.finditer(txt):
            lang = fm.group(1).lower()
            if lang not in ("html", ""):
                continue
            block = fm.group(2)
            start_line = txt[:fm.start()].count("\n") + 1
            for rx, name, lvl, msg in CODE_RULES:
                if name in allowed:
                    continue
                for mm in rx.finditer(block):
                    ln = start_line + block[:mm.start()].count("\n")
                    issues.append((lvl, "%s:%d" % (fname, ln), "[%s] %s" % (name, msg)))
                    break
            for im in IMG_RE.finditer(block):
                tag = im.group(0)
                ln = start_line + block[:im.start()].count("\n")
                if "max-width:100%" not in tag.replace(" ", ""):
                    issues.append(("ERROR", "%s:%d" % (fname, ln),
                                   "<img> 缺少 max-width:100%"))
                if re.search(r"(?<!max-)width\s*:\s*100%", tag):
                    issues.append(("ERROR", "%s:%d" % (fname, ln),
                                   "<img> 用 width:100% 会拉伸糊掉"))
            for em in EMPTY_LEAF_RE.finditer(block):
                ln = start_line + block[:em.start()].count("\n")
                issues.append(("WARN", "%s:%d" % (fname, ln),
                               "空 <span leaf=\"\"></span> 会被编辑器折叠，应填 &nbsp;"))

    # --- 7. common-components 占位符 ---
    if cc_txt:
        # 变量表在文件开头，取到第一个通用组件 G01 之前的部分
        table_part = cc_txt.split("## G01")[0]
        declared = set(re.findall(r"`\{\{([A-Z_]+)\}\}`", table_part))
        used = set(re.findall(r"\{\{([A-Z_]+)\}\}", cc_txt))
        for tok in sorted(used - declared):
            if tok in ("TITLE", "GZH_CONTENT"):
                continue
            issues.append(("WARN", "common-components.md",
                           "占位符 {{%s}} 被使用但未在变量表里声明" % tok))

    # ------------------------------------------------- 输出
    issues.sort(key=lambda x: (LEVEL_ORDER.get(x[0], 9), x[1]))
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for lvl, where, msg in issues:
        counts[lvl] = counts.get(lvl, 0) + 1
        print("[%-5s] %-28s %s" % (lvl, where, msg))

    print("")
    print("—— 结论 ——")
    print("  主题文件：%d 套（%s）" % (len(theme_files), "、".join(sorted(theme_files))))
    print("  ERROR %d / WARN %d" % (counts["ERROR"], counts["WARN"]))

    if counts["ERROR"]:
        print("\n❌ 主题自检未通过，修复后再提交。")
        return 1
    if counts["WARN"]:
        print("\n⚠️  无 ERROR，但有 WARNING —— 确认是有意为之还是漏改。")
    else:
        print("\n✅ 主题自检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
