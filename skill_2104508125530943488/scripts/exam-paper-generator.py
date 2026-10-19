#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""试卷出题与组卷 · 结构化方案生成器（纯标准库、离线、只读）。

用法：
    python3 exam-paper-generator.py "用户的简要需求"
    python3 exam-paper-generator.py --json "用户的简要需求"

只输出结构化方案与口径，不替代专业结论；数值类按标注口径由用户代入。
"""

import argparse
import json
import sys

DATA = json.loads(r'''{"name": "exam-paper-generator", "disp_zh": "\u8bd5\u5377\u51fa\u9898\u4e0e\u7ec4\u5377", "disp_en": "Exam Paper Generator", "track": "\u6559\u5e08\u6559\u80b2", "abilities": ["\u8bc4\u8bed\u8bdd\u672f\uff1a\u6279\u91cf\u51fa\u8bc4\u8bed\u6a21\u677f\u4e0e\u5bb6\u957f\u4f1a\u6c9f\u901a\u811a\u672c", "\u9519\u9898\u672c\uff1a\u628a\u9519\u9898\u5f52\u7c7b\u5e76\u751f\u6210\u540c\u7c7b\u53d8\u5f0f\u7ec3\u4e60", "\u51fa\u9898\u7ec4\u5377\uff1a\u6309\u77e5\u8bc6\u70b9\u4e0e\u96be\u5ea6\u51fa\u5377\uff0c\u7ed9\u53cc\u5411\u7ec6\u76ee\u8868", "\u4f5c\u4e1a\u8bbe\u8ba1\uff1a\u6309\u8bfe\u65f6\u76ee\u6807\u51fa\u5206\u5c42\u4f5c\u4e1a\u4e0e\u6279\u6539\u8981\u70b9"], "rules": ["\u8bc4\u8bed\u8bb2\u5177\u4f53\uff1a\u907f\u514d\u5957\u8bdd\uff0c\u7ed9\u53ef\u89c2\u5bdf\u884c\u4e3a\u63cf\u8ff0", "\u9519\u9898\u8bb2\u5f52\u7c7b\uff1a\u6309\u77e5\u8bc6\u70b9\u5f52\u56e0\uff0c\u53d8\u5f0f\u4e0d\u8d85\u7eb2", "\u7ec4\u5377\u8bb2\u8986\u76d6\uff1a\u77e5\u8bc6\u70b9\u4e0e\u96be\u5ea6\u53cc\u5411\u5e73\u8861", "\u4f5c\u4e1a\u8bb2\u5206\u5c42\uff1a\u57fa\u7840\u63d0\u5347\u62d3\u5c55\u4e09\u6863\u53ef\u9009"], "boundary": "\u751f\u6210\u5185\u5bb9\u9700\u6559\u5e08\u7ed3\u5408\u5b66\u60c5\u5ba1\u6838\u4f7f\u7528\uff0c\u4e0d\u66ff\u4ee3\u6559\u5b66\u5224\u65ad\u4e0e\u8bc4\u5206\u3002", "samples": ["\u6309\u77e5\u8bc6\u70b9\u4e0e\u96be\u5ea6\u51fa\u5377\uff0c\u7ed9\u53cc\u5411\u7ec6\u76ee\u8868", "\u573a\u666f\uff1a\u8981\u5199\u5b66\u751f\u8bc4\u8bed\u3001\u8981\u51fa\u9519\u9898\u672c\u3001\u8981\u51fa\u9898\u7ec4\u5377\u3001\u8981\u8bbe\u8ba1\u4f5c\u4e1a\uff0c\u8bf7\u51fa\u4e00\u4efd\u8bd5\u5377\u51fa\u9898\u4e0e\u7ec4\u5377\u7684\u4ea4\u4ed8\u7269"], "calc_note": ""}''')


def build_plan(topic):
    d = DATA
    return {
        "topic": (topic or "").strip(),
        "skill": d["name"],
        "display_name": d["disp_zh"],
        "track": d["track"],
        "abilities": list(d["abilities"]),
        "rules": list(d["rules"]),
        "boundary": d["boundary"],
        "samples": list(d["samples"]),
        "calc_note": d.get("calc_note", ""),
    }


def render(plan):
    lines = []
    lines.append("需求：" + (plan["topic"] or "（未提供描述）"))
    lines.append("技能：" + plan["display_name"] + "（" + plan["skill"] + "）")
    lines.append("赛道：" + plan["track"])
    lines.append("")
    lines.append("执行要点：")
    for i, a in enumerate(plan["abilities"], 1):
        lines.append("  {}. {}".format(i, a))
    lines.append("铁律：")
    for i, r in enumerate(plan["rules"], 1):
        lines.append("  {}. {}".format(i, r))
    if plan["calc_note"]:
        lines.append("")
        lines.append("口径与公式：")
        for line in plan["calc_note"].split("\n"):
            lines.append("  " + line)
    lines.append("")
    lines.append("边界：" + plan["boundary"])
    lines.append("")
    lines.append("下一步：结合上述要点与你的具体参数填装交付物；数值类按标注口径代入，最终以官方或专业结论为准。")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=DATA["disp_zh"] + " 结构化方案生成器（离线）")
    ap.add_argument("topic", nargs="*", default=[], help="用户需求描述")
    ap.add_argument("--topic", dest="topic_opt", default=None, help="显式需求")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ns = ap.parse_args(argv)
    topic = ns.topic_opt or " ".join(ns.topic)
    if not topic.strip():
        ap.error("请提供需求描述，例如：python3 exam-paper-generator.py \"你的需求\"")
    plan = build_plan(topic)
    if ns.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(render(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main())
