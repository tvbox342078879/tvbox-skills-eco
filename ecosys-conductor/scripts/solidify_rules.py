#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
solidify_rules.py — 分卷固化器（S109 P2 · v2）

把 rules_registry.json 中每条规则的归属落进对应技能 SKILL.md 的
「铁律坐标（规则分卷 · S109）」章节。
  - 已有铁律表格的技能：在表尾追加统一 3 列行
  - 无该章节的技能：文末新建该章节
幂等：编号已存在则跳过。只插入、不改写既有内容。
"""
import json, os, re, sys

SKILLS = "/root/.codebuddy/skills"
REG = os.path.join(SKILLS, "ecosys-conductor/scripts/rules_registry.json")
FULL = "/tmp/s109_rules_full.json"
SEC_TITLE = "## 铁律坐标（规则分卷 · S109）"
HAS_TABLE = {"ecosys-conductor", "tvbox-inspect", "tvbox-source-hub"}


def brief(sol, pit, n=100):
    t = sol if len(sol) > len(pit) else pit
    t = t.replace('`', '').replace('*', '')
    t = re.sub(r'（[^）]{0,10}$', '', t)
    t = t.strip().rstrip('，。;；')
    return t[:n].rstrip('，。;；') if len(t) > n else t


def main(dry=True):
    reg = json.load(open(REG, encoding="utf-8"))
    full = json.load(open(FULL, encoding="utf-8"))
    byskill = {}
    for r in reg["rules"]:
        po = r["primary_owner"]
        if not po:
            continue
        p = os.path.join(SKILLS, po, "SKILL.md")
        txt = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
        if re.search(r'\b' + re.escape(r["id"]) + r'\b', txt):
            continue
        byskill.setdefault(po, []).append(r)

    for sk, rules in sorted(byskill.items()):
        p = os.path.join(SKILLS, sk, "SKILL.md")
        if not os.path.exists(p):
            print(f"{sk}: 跳过(无SKILL.md)"); continue
        txt = open(p, encoding="utf-8").read()
        ch = rules[0]["chapter"].replace("§", "").strip()
        rows = []
        for r in rules:
            f = full.get(r["id"], {})
            rows.append(f"| {r['id']} | {brief(f.get('sol',''), f.get('pit',''))} | 总册{r['chapter']}·S109固化 |")

        if sk in HAS_TABLE and re.search(r'^\|\s*(M-R|U-R|P-R)\s*\|', txt, re.M):
            lines = txt.split("\n")
            last = max(i for i, l in enumerate(lines)
                       if re.match(r'^\|\s*(M-R|U-R|P-R)\s*\|', l))
            lines[last + 1:last + 1] = rows
            new_txt = "\n".join(lines)
        else:
            block = ("\n\n" + SEC_TITLE + "\n\n"
                     "> S109 规则↔技能联动工程固化：本技能是以下规则的正本（技能为正本，总册留索引）。\n\n"
                     "| 规则 | 落地判据 | 来源 |\n|---|---|---|\n" + "\n".join(rows) + "\n")
            new_txt = txt.rstrip("\n") + block

        print(f"{sk}: 固化 {len(rows)} 条")
        if not dry:
            open(p, "w", encoding="utf-8").write(new_txt)
    print("---", "DRY RUN" if dry else "已写入")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
