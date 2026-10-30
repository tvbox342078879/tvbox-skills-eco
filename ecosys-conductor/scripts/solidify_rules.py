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
# ★ S204 修：原路径在 /tmp —— 沙箱重启/换机即丢，工具会从「能跑」变成「永远跑不了」，
#   而且报的错是 FileNotFoundError，看不出是「临时文件没了」而不是「规则没了」。
#   改为技能目录持久保存；仍兼容读取旧的 /tmp 路径（只读兜底）。
FULL = os.path.join(SKILLS, "ecosys-conductor/scripts/rules_full.json")
FULL_LEGACY = "/tmp/s109_rules_full.json"
SEC_TITLE = "## 铁律坐标（规则分卷 · S109）"
HAS_TABLE = {"ecosys-conductor", "tvbox-inspect", "tvbox-source-hub"}


def brief(sol, pit, n=100):
    t = sol if len(sol) > len(pit) else pit
    t = t.replace('`', '').replace('*', '')
    t = re.sub(r'（[^）]{0,10}$', '', t)
    t = t.strip().rstrip('，。;；')
    return t[:n].rstrip('，。;；') if len(t) > n else t


def chapter_of(r):
    """取规则章节号，缺失时给明确回退值。

    ★ S204 修：原实现直接 `rules[0]["chapter"]`，而注册表里 M-R06/M-R07 两条
    没有 chapter 字段 → KeyError 直接崩，**整个固化器一条都跑不出来**。
    一条脏数据阻断全部流程，属「局部故障放大成全局故障」。
    回退值显式写「章节未登记」，让人一眼看出该补数据，而不是假称某个章节。
    """
    ch = r.get("chapter")
    if not ch:
        return "章节未登记"
    return str(ch).replace("§", "").strip() or "章节未登记"


def load_full():
    """读规则全文（sol/pit 文案来源）。三级兜底，缺失给明确提示而非崩栈。"""
    for p in (FULL, FULL_LEGACY):
        if os.path.exists(p):
            try:
                return json.load(open(p, encoding="utf-8"))
            except Exception as e:
                print(f"⚠️ {p} 解析失败（{type(e).__name__}: {e}）")
    print(f"⚠️ 规则全文缺失（已找 {FULL} 与 {FULL_LEGACY}）——"
          f"本次仍可继续，但「落地判据」列会是空的（不是规则没有，是文案没加载到）")
    return {}


def main(dry=True):
    reg = json.load(open(REG, encoding="utf-8"))
    full = load_full()
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
        rows = []
        for r in rules:
            f = full.get(r["id"], {})
            rows.append(f"| {r['id']} | {brief(f.get('sol',''), f.get('pit',''))} | "
                        f"总册{chapter_of(r)}·S109固化 |")

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
    # ★ T36 弃用门（M-R136 不造假：工具本身不许再误导人）
    #   2026-10-22 22:36 实证：本脚本不识别 --help，把该参数当普通参数忽略 →
    #   按「实跑」执行，直接把 ecosys-conductor/SKILL.md 重写成「基线+66 条表」，
    #   直到 git diff 才发现。事故根因是**默认动作是写盘**，任何误传参数都变成写入。
    #
    #   同时本脚本的路线已被判错（M-R92「技能写判据主干、总册做索引」）：
    #   它做的是把注册表**抄成表**追加进 SKILL.md，而 SKILL.md 是模型整体载入的
    #   上下文（M-R77 体积硬上限 35KB 就是为此），塞索引会挤掉真正要用的判据。
    #   正确做法是查询：`rules_cmd.py --owner <技能> --todo`（随时可算、不会过期）。
    #
    #   故改为：默认**拒绝写盘**，必须显式加 --i-know-legacy 才走旧行为；
    #   任何未知参数（含 --help）一律打印用法后退出码 2，绝不静默写文件。
    _args = [a for a in sys.argv[1:] if a != '--dry']
    _unknown = [a for a in _args if a != '--i-know-legacy']
    if _unknown:
        print("⛔ solidify_rules.py 已弃用（T36 · 2026-10-22）")
        print(f"   不认识的参数：{' '.join(_unknown)}")
        print("   为防误写，本脚本不再猜测参数含义——一个都不写。")
        print("\n   现在该用什么：")
        print("     python3 rules_cmd.py --owner <技能名> --todo   # 查该技能还缺哪几条")
        print("     python3 rules_cmd.py <规则编号>               # 查单条详情与落地状态")
        print("     python3 rules_cmd.py check                    # 三方一致性")
        print("\n   原因（M-R92）：技能 SKILL.md 写判据主干，索引交给总册；"
              "\n   抄进去的表会过期，查询不会。")
        sys.exit(2)
    if '--i-know-legacy' not in sys.argv and '--dry' not in sys.argv:
        print("⛔ solidify_rules.py 已弃用，默认不写盘。")
        print("   要确需走旧路线（把规则表抄进 SKILL.md），加 --i-know-legacy 显式确认。")
        print("   日常请改用：rules_cmd.py --owner <技能名> --todo")
        sys.exit(2)
    main(dry="--dry" in sys.argv)
