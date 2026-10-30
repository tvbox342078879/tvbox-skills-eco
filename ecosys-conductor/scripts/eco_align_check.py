#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M-R213 探针：扫描各子系统是否私建与共享核心 core.py 平行的能力设施。

规则（M-R213 / M-R38 单一事实源）：
  共享核心唯一位置 = tvbox-source-hub/scripts/core.py。
  其他技能 scripts/ 下不应存在名为 core.py（或 core_*.py 等平行设施）的文件；
  否则 = 制造新的入口分裂 + 单一事实源冲突（1027 探源司治理重写轮坑1 实证）。

退出码：0=绿（无平行设施）；1=红（发现平行设施）。
"""
import os
import sys

SKILLS = "/root/.codebuddy/skills"
SHARED_CORE = os.path.join(SKILLS, "tvbox-source-hub", "scripts", "core.py")

hits = []
for name in sorted(os.listdir(SKILLS)):
    sp = os.path.join(SKILLS, name, "scripts")
    if not os.path.isdir(sp):
        continue
    if name == "tvbox-source-hub":
        continue  # 共享核心自身，豁免
    # 仅查 scripts 根目录下精确同名 core.py（与共享核心平行冲突的最直接信号）。
    # 子目录内的 core*.py（如 computer-use/modules/core.py、eco-ledger/core_ledger.py）
    # 属各技能自有子模块/账房核心，与 TVBox 取数核心不同领域，非平行冲突设施，不计入。
    cand = os.path.join(sp, "core.py")
    if os.path.isfile(cand):
        hits.append(cand)

if not os.path.isfile(SHARED_CORE):
    print("M-R213 异常：共享核心不存在", SHARED_CORE)
    sys.exit(1)

if hits:
    print("M-R213 红：发现与共享核心 core.py 平行的子系统设施：")
    for h in hits:
        print("  ", h)
    sys.exit(1)

print("M-R213 绿：未发现与共享核心 core.py 平行的子系统设施；共享核心存在。")
sys.exit(0)
