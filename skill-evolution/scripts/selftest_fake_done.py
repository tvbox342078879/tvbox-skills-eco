# -*- coding: utf-8 -*-
"""T24 假完成闸门自测：防「一句话洗绿」
用例从本次真实提案形态抄录，不用臆想写法（M-R135）。"""
import sys, os, tempfile, shutil, importlib
sys.path.insert(0, '/root/.codebuddy/skills/skill-evolution/scripts')
import evolve
importlib.reload(evolve)

ok = []
# 1) recur 无 resolved_note → 必须判 none（不许放行）
p = {'skill': 'tvbox-janitor', 'type': 'recur', 'ref': '节点移动改名', 'status': 'pending'}
o, b = evolve._gate_moves([dict(p)])
ok.append(('recur 无说明→none', bool(b) and b[0].get('landed') == 'none'))

# 2) recur 说明里点了不存在的文件 → 必须判 doc
p2 = {'skill': 'tvbox-janitor', 'type': 'recur', 'ref': 'X',
      'resolved_note': '已固化：not_exist_abc.py 里实现了'}
o, b = evolve._gate_moves([dict(p2)])
ok.append(('recur 指空文件→doc', bool(b) and b[0].get('landed') == 'doc'))

# 3) move 只有一句「已借鉴 T-D」→ 必须判 doc/none，不许 na
p3 = {'skill': 'ecosys-conductor', 'type': 'move', 'ref': 'T-D'}
# 直接验证判据函数对纯声明的识别
MD = '## 招式对照\n- T-D 移动替代删除：已借鉴 T-D 做法\n'
# 造临时技能目录
tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, 'ecosys-conductor'), exist_ok=True)
open(os.path.join(tmp, 'ecosys-conductor', 'SKILL.md'), 'w', encoding='utf-8').write(MD)
old = evolve.SKILLS
evolve.SKILLS = tmp
try:
    st, det = evolve._move_landed('ecosys-conductor', 'T-D')
finally:
    evolve.SKILLS = old
    shutil.rmtree(tmp, ignore_errors=True)
ok.append(('纯「已借鉴」声明→doc', st == 'doc'))

allok = all(x[1] for x in ok)
for n, v in ok:
    print(('  ✅ ' if v else '  ❌ ') + n)
print('T24 假完成闸门自测:', '通过' if allok else '失败')
raise SystemExit(0 if allok else 1)
