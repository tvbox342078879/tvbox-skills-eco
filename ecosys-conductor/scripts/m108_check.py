"""T29 · P054：M-R108 可执行判据（v2 · 修正过宽判定）

## 修正记录（真实坑，必须留痕）
v1 把「recurring 报 ⏳ 待固化」直接当缺口 → 对 `pref_deliver_zone` 报红。
但查档案：该条 `requested_count=1`、status=active。
**M-R108 的门槛是「重复 ≥3 次」，1 次不该要求固化。**
v1 等于把"还没到门槛"和"到了门槛没固化"混为一谈——判据过宽，
误拦代价是"每一条新建的偏好都会被报红"，那会让人很快忽略这项体检。

v2 收紧：**唯一的红判据 = requested_count ≥ THRESHOLD 且 status 未 solidified**。
recurring 的 ⏳ 只作为提示行（不计入退出码），因为它反映的是"尚未固化"，
不区分"次数不足"还是"次数够了没做"。

## 判据（M-R136 三关复核）
①触发点单点可观测：读持久指令档，逐条比 requested_count 与 status —— 纯数据，单点
②判据纯文本可判：`n >= 3 and not status.startswith('solidified')`
③误拦代价：极小 —— 只报真到门槛未固化的
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# ★ 与 habit_brief.py:32 同一路径定义（单一事实源）
STANDING = os.path.join(HERE, 'user_standing_instructions.json')
EVOLVE = '/root/.codebuddy/skills/skill-evolution/scripts/evolve.py'
THRESHOLD = 3


def _load(p, default=None):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default


def check_standing(stpath=None):
    """返回 (状态, 红明细, 提示明细, 条数)；状态 ∈ {'OK','缺口','无法判定'}"""
    d = _load(stpath or STANDING, {'instructions': []})
    insts = d.get('instructions', []) if isinstance(d, dict) else []
    if not insts:
        return '无法判定', ['持久指令档读不到或为空'], [], 0
    red = []
    tips = []
    for it in insts:
        iid = it.get('id')
        n = it.get('requested_count', 0) or 0
        st = str(it.get('status', ''))
        solidified = st.startswith('solidified')
        if n >= THRESHOLD and not solidified:
            red.append(f'{iid} 要求 {n} 次（≥{THRESHOLD}）但仍未固化（status={st}）')
        elif not solidified:
            tips.append(f'{iid} 要求 {n} 次，未到 {THRESHOLD} 次门槛，暂不要求固化')
    return ('缺口' if red else 'OK'), red, tips, len(insts)


def recurring_tips():
    """recurring 的 ⏳ 行只作提示，不计入红判据"""
    if not os.path.exists(EVOLVE):
        return ['evolve.py 不存在，跳过 recurring 对账']
    try:
        r = subprocess.run([sys.executable, EVOLVE, 'recurring', '--min', str(THRESHOLD)],
                           capture_output=True, text=True, timeout=180)
    except Exception as e:
        return [f'recurring 跑失败：{e}']
    out = r.stdout or ''
    if '【用户偏好·持久指令' not in out:
        return ['recurring 输出无「用户偏好」段，无法对账']
    seg = out.split('【用户偏好·持久指令', 1)[1]
    pend = re.findall(r'^\s*⏳\s*\[([a-z_0-9]+)\]', seg, re.M)
    return [f'recurring 报 `{p}` 未固化（若其次数已达 {THRESHOLD} 次应补 status）' for p in pend]


def run():
    st, red, tips, n = check_standing()
    tips = tips + recurring_tips()
    return st, red, tips, n


if __name__ == '__main__':
    st, red, tips, n = run()
    icon = {'OK': '✅', '缺口': '❌', '无法判定': '⚠️'}[st]
    print(f'{icon} M-R108 持久指令联通体检：{st}（档案 {n} 条）')
    for d in red:
        print(f'   🔴 {d}')
    for d in tips:
        print(f'   · {d}')
    sys.exit(0 if st == 'OK' else 1)
