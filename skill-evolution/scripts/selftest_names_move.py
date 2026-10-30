# -*- coding: utf-8 -*-
"""P058 判据修正的双向自测（M-R134）。

每条用例都要**能测出反例**：正向用例必须判 doc（判据还认得出真欠账），
反向用例必须不判 doc（判据不再被无关句子骗）。

用例全部从本轮真实文本抄录（M-R135：不许臆想写法）。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, '/root/.codebuddy/skills/skill-evolution/scripts')
import evolve

ok = []


def judge(md_text, tid, skill='__probe__'):
    """在临时技能目录里放一份 SKILL.md，喂给 _move_landed。"""
    tmp = tempfile.mkdtemp()
    d = os.path.join(tmp, skill)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, 'SKILL.md'), 'w', encoding='utf-8').write(md_text)
    old = evolve.SKILLS
    evolve.SKILLS = tmp
    try:
        return evolve._move_landed(skill, tid)
    finally:
        evolve.SKILLS = old
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 反向：这些**不该**判 doc ----------
# R1 真实案例：janitor CHANGELOG 首行标题，含"探针"但与 T-E 无关
st, det = judge('## v1.2.7 — 版本对齐补记（T18 探针发现 CHANGELOG 漏记）\n', 'T-E')
ok.append(('R1 无关标题含特征词→不判doc', st != 'doc', st, det))

# R2 真实案例：learn-hub materials 里那句"量化画像"，含"量化"但与 T-F 无关
st, det = judge('1. 审计总账实况（缺 due/next/owner 计数）——拿到「不闭环」的量化画像。\n', 'T-F')
ok.append(('R2 复盘正文含特征词→不判doc', st != 'doc', st, det))

# R3 只提招式特征词、**无结论词**、也指不出是哪一招 → 不算证据
st, det = judge('- 那天用探针扫了一遍，发现三个脚本返回值不对。\n', 'T-E')
ok.append(('R3 只叙旧事无结论→不判doc', st != 'doc', st, det))

# R4 真实案例：结论词 + 特征词同现 = 真的在给这一招下结论 → 应判 na（不是 doc）
#   这一条是上一条 R3 撞出来的：`已跑探针验收（attach_guard.py…rc=0）` 确实是落地证据，
#   判 doc 才是错。旧版 R3 拿它当"不该判 doc"的用例，等于让判据**放弃真证据**。
st, det = judge('- 已跑探针验收（`attach_guard.py`，126 个脚本，rc=0）：自测可捕获 3 类真实误判形态。\n', 'T-E')
ok.append(('R4 结论+特征词同现→na', st == 'na', st, det))

# ---------- 正向：这些**必须**判 doc（判据不能因修而失灵） ----------
# P1 明确点名 T-E，只在讲做法不给结论
st, det = judge('- T-E 活体探针：新增检测器时要注入已知异常并建立假阳性基线。\n', 'T-E')
ok.append(('P1 点名招式+只讲做法→doc', st == 'doc', st, det))

# P2 用招式全名叙述做法
st, det = judge('- 活体探针的做法是：先造一批已知异常的样本，再跑一遍看捕获率。\n', 'T-E')
ok.append(('P2 招式全名+只讲做法→doc', st == 'doc', st, det))

# P3 点名招式但结论是"不适用"
st, det = judge('- T-E 活体探针 — **本技能当前不适用**：清道夫没有自检探针结构。\n', 'T-E')
ok.append(('P3 点名招式+明确结论→na', st == 'na', st, det))

# P4 空文档 → none（不是 doc）
st, det = judge('# 什么都不写\n', 'T-E')
ok.append(('P4 空文档→none', st == 'none', st, det))

allok = True
for name, passed, st, det in ok:
    print(('  ✅ ' if passed else '  ❌ ') + f'{name}  （实得 {st}）')
    if not passed:
        print(f'       detail: {det[:100]}')
        allok = False
print()
print('P058 判据自测:', f'{sum(1 for x in ok if x[1])}/{len(ok)}', '通过' if allok else '失败')
raise SystemExit(0 if allok else 1)
