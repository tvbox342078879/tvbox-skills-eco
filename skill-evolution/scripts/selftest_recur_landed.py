# -*- coding: utf-8 -*-
"""P061 自测：recur 类型提案的判据归一。

要证明三件事：
  A. `_move_landed` 遇到 recur 类型名（中文）时，**不再甩 unknown**，而是转给 `_recur_landed`
  B. `_recur_landed` 的四态判得准（无note→none / 有规则号→landed / 有真文件→landed / 指空→doc）
  C. 闸门 `_gate_moves` 对 recur 的放行语义与改动前一致（landed 与 doc 都放行，none 退回）
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, '/root/.codebuddy/skills/skill-evolution/scripts')
import evolve

ok = []


def mk_skill(files):
    """造一个临时技能目录，files = {相对路径: 内容}。返回 (root, skill名)。"""
    tmp = tempfile.mkdtemp()
    sk = '__probe__'
    d = os.path.join(tmp, sk)
    os.makedirs(d, exist_ok=True)
    for rel, body in files.items():
        fp = os.path.join(d, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        open(fp, 'w', encoding='utf-8').write(body)
    return tmp, sk


# ---------- A：_move_landed 不再对 recur 甩 unknown ----------
tmp, sk = mk_skill({'SKILL.md': '# x\n', 'scripts/core.py': 'pass\n'})
old = evolve.SKILLS
evolve.SKILLS = tmp
try:
    st, det = evolve._move_landed(sk, '记录读写', '已固化：core.py 三态收口')
    ok.append(('A1 recur 中文ref→不返回unknown', st != 'unknown', st, det))

    # A2 真的招式号但不存在的招式，仍应 unknown（不能因修而把"没判据"也说成有判据）
    st2, det2 = evolve._move_landed(sk, 'T-Z', None)
    ok.append(('A2 非法招式号→仍unknown', st2 == 'unknown', st2, det2))

    # ---------- B：_recur_landed 四态 ----------
    st3, _ = evolve._recur_landed(sk, '')
    ok.append(('B1 无note→none', st3 == 'none', st3, ''))

    st4, _ = evolve._recur_landed(sk, '已固化：M-R38 立了单一事实源')
    ok.append(('B2 有规则号→landed', st4 == 'landed', st4, ''))

    st5, _ = evolve._recur_landed(sk, '已固化：scripts/core.py 收口')
    ok.append(('B3 有真文件→landed', st5 == 'landed', st5, ''))

    st6, _ = evolve._recur_landed(sk, '已固化：not_exist_zzz.py 里做了')
    ok.append(('B4 指空文件→doc', st6 == 'doc', st6, ''))
finally:
    evolve.SKILLS = old
    shutil.rmtree(tmp, ignore_errors=True)

# ---------- C：闸门放行语义不变 ----------
cases = [
    ('C1 landed→放行', {'ref': '记录读写', 'resolved_note': '已固化：M-R38'}),
    ('C2 doc→退回(指不出落点=没固化)', {'ref': '记录读写', 'resolved_note': '已固化：zzz_nofile.py'}),
    ('C3 无note→退回', {'ref': '记录读写', 'resolved_note': ''}),
]
tmp, sk = mk_skill({'SKILL.md': '# x\n'})
old = evolve.SKILLS
evolve.SKILLS = tmp
try:
    for name, base in cases:
        p = dict(base, skill=sk, type='recur', status='pending')
        o, b = evolve._gate_moves([p])
        passed = bool(o) == (name == 'C1 landed→放行')
        ok.append((name, passed, 'ok' if o else 'bad', p.get('landed', '') or ''))
finally:
    evolve.SKILLS = old
    shutil.rmtree(tmp, ignore_errors=True)

allok = True
for name, passed, st, det in ok:
    print(('  ✅ ' if passed else '  ❌ ') + f'{name}  （{st}）')
    if not passed:
        allok = False
print()
print('P061 自测:', f'{sum(1 for x in ok if x[1])}/{len(ok)}', '通过' if allok else '失败')
raise SystemExit(0 if allok else 1)
