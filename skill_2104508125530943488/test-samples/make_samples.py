#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""造样本需求与输出，供 run_tests 与本地面向演示使用。"""
import os
import json
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import importlib.util
_SPEC = importlib.util.spec_from_file_location("skill_mod", os.path.join(os.path.dirname(HERE), "scripts", "exam-paper-generator.py"))
M = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(M)


def main():
    out = os.path.join(HERE, "sample_output.txt")
    with open(out, "w", encoding="utf-8") as f:
        for t in M.DATA["samples"]:
            f.write(M.render(M.build_plan(t)))
            f.write("\n\n" + "-" * 40 + "\n\n")
    jout = os.path.join(HERE, "sample_output.json")
    with open(jout, "w", encoding="utf-8") as f:
        json.dump([M.build_plan(t) for t in M.DATA["samples"]], f, ensure_ascii=False, indent=2)
    print("样本已生成：", out, jout)


if __name__ == "__main__":
    main()
