#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""试卷出题与组卷 离线回归测试。直接 import 技能脚本跑断言，不联网、不依赖第三方库。"""

import os
import sys
import json
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
SCRIPT_PATH = os.path.join(SKILL_DIR, "scripts", "exam-paper-generator.py")
import importlib.util
_spec = importlib.util.spec_from_file_location("skill_mod", SCRIPT_PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    mark = "通过" if ok else "失败"
    print("  [{}] {}{}".format(mark, name, "（{}）".format(detail) if (detail and not ok) else ""))
    return ok


print("== 1. 数据契约 ==")
check("abilities 非空且>=3条", isinstance(M.DATA["abilities"], list) and len(M.DATA["abilities"]) >= 3)
check("rules 非空且>=3条", isinstance(M.DATA["rules"], list) and len(M.DATA["rules"]) >= 3)
check("boundary 非空", bool(M.DATA["boundary"].strip()))
check("samples 非空且>=1条", isinstance(M.DATA["samples"], list) and len(M.DATA["samples"]) >= 1)
check("name 与脚本名一致", M.DATA["name"] == "exam-paper-generator")

print("== 2. 渲染输出 ==")
plan = M.build_plan(M.DATA["samples"][0])
check("build_plan 返回关键字段", all(k in plan for k in ("topic", "skill", "abilities", "rules", "boundary")))
out = M.render(plan)
check("render 含『技能：』与技能名", "技能：" in out and M.DATA["disp_zh"] in out)
check("render 含『铁律』", "铁律" in out)
check("render 含『边界』", "边界" in out)

print("== 3. 命令行与 JSON 契约 ==")
py = sys.executable
r1 = subprocess.run([py, os.path.join(SKILL_DIR, "scripts", "exam-paper-generator.py"), M.DATA["samples"][0]],
                    capture_output=True, text=True)
check("命令行走 stdout 非空", r1.returncode == 0 and bool(r1.stdout.strip()))
r2 = subprocess.run([py, os.path.join(SKILL_DIR, "scripts", "exam-paper-generator.py"), "--json", M.DATA["samples"][0]],
                    capture_output=True, text=True)
ok_json = False
detail = ""
try:
    obj = json.loads(r2.stdout)
    ok_json = isinstance(obj, dict) and obj.get("skill") == M.DATA["name"]
except Exception as e:
    detail = str(e)
check("JSON 输出可解析且含 skill 字段", ok_json, detail)

print("== 4. 边界与空输入 ==")
plan_empty = M.build_plan("")
check("空需求 build_plan 不崩溃且 topic 为空串", plan_empty["topic"] == "")
r3 = subprocess.run([py, os.path.join(SKILL_DIR, "scripts", "exam-paper-generator.py")], capture_output=True, text=True)
check("无参数触发 argparse error（退出码非0）", r3.returncode != 0)

total = len(results)
passed = sum(results)
print("结论：{} / {} 通过".format(passed, total))
sys.exit(0 if passed == total else 1)
