#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清账司·生态联动门面（S136）

为什么必须有这个文件：新技能建好后如果**谁都不接**，它在六维平衡性里
「联动断点」维度会直接得 45 分（未接任何生态门面）→ 跌成生态最短板 → 被监察司判遗留。
这不是形式主义——它问的是「你这个技能，别人用得上吗？出了事故谁来处理？」

本文件把清账司真正接进三条既有联动通道：

  L1 技能 → 监察司（register_monitor）
      清账司发现「某类欠账反复出现>>不收敛」时，登记为新风险面，
      让监察司**长期盯**，而不是每次靠人记得跑 close。

  L2 技能 → 清道夫（file_to_janitor）
      欠账总账、汇报文件如果成了云端/本地冗余节点，扔进同一收件箱，
      用户在**一个地方**按单删（不新增第二处垃圾场）。

  L3 技能 → 生态登记（DB_ECO / eco_skills.json）
      已由 conductor onboard 完成（M-R78 四面）。

另外 L4：**反向联动**——清账司是**其它技能收尾的落点**（tvbox close 九技能共用）。
这条是「别人用得上吗」的最硬回答：九个技能都在往这里交账。

失败一律不阻断主流程（门面调用是旁路），但要能被看见（quiet=False 时可打印）。
"""

import os
import sys

# 生态技能根目录。**必须可覆写**（S136 单测踩坑）：
# 原实现把 '/root/.codebuddy/skills' 写死在 notify_owner 里，导致受控单测
# 虽然把账本换成了临时文件，派单仍然真实写进**生产** flow.json——
# 单测跑完在生产环境留下 `[清账司派单…] 甲欠账 | sid=2cf6146fb76c` 的垃圾项，
# 下一轮 watchdog 直接报遗留（判据④ 变红）。
# 这是「侧信道逸出」：被测代码有一条不受沙箱控制的真实路径。
# 修法：根目录提到模块级常量，单测可临时改写；生产值不变。
SKILLS_ROOT = os.environ.get('UFAUDIT_SKILLS_ROOT') or '/root/.codebuddy/skills'

HUB = os.path.join(SKILLS_ROOT, 'tvbox-source-hub', 'scripts')
if HUB not in sys.path:
    sys.path.insert(0, HUB)


def _core():
    """惰性拿 core（不给 import 失败就崩的机会）。"""
    try:
        import core
        return core
    except Exception as e:
        print(f'  ⚠️ core 门面不可用（{type(e).__name__}: {e}）——联动降级')
        return None


def register_risk_face(risk_face, desc, quiet=False):
    """L1：把「反复不收敛的欠账类型」登记进监察司监测册。"""
    c = _core()
    if c is None:
        return False
    ok = c.register_monitor(risk_face, 'naming_compliance', desc, quiet=quiet)
    if ok and not quiet:
        print(f'  ✅ 已登记风险面「{risk_face}」→ 监察司接手长期盯')
    return ok


def file_node_to_janitor(node_id, reason, quiet=False):
    """L2：把清账司产生的冗余节点（旧汇报/旧账本备份）投进清道夫收件箱。"""
    c = _core()
    if c is None:
        return False
    return c.file_to_janitor(node_id, reason, quiet=quiet)


def notify_owner(owner, item):
    """L3：把单条派单**推给**对应监察官（不只写在自己账本里等人来看）。

    这是「别产出后又啥都不做」的关键一环：
    账本落盘 + 门面通知 双写，认领方下次跑自己的命令时能看见。
    """
    try:
        base = SKILLS_ROOT          # 模块级常量：单测可覆写，避免侧信道写生产（S136）
        # 各监察官的「收件箱」：优先写各自 flow.json 的 backlog（它们本来就读这个）
        fp = os.path.join(base, owner, 'scripts', 'flow.json')
        if not os.path.exists(fp):
            return False
        import json
        d = json.load(open(fp, encoding='utf-8'))
        tag = f"[清账司派单 {item.get('due', '')}] {item.get('title', '')[:100]}"
        bl = d.setdefault('backlog', [])
        if any(item.get('sid', '') in str(x) for x in bl):
            return True           # 幂等：同一笔不重复派
        bl.append(f'{tag} ｜ sid={item.get("sid")} ｜ 下一步 {item.get("next")}')
        # ★ S158 九补：同款——写完不关就回读，读到半截文件（实测 JSONDecodeError）。
        _tmp = fp + '.tmp'
        with open(_tmp, 'w', encoding='utf-8') as _f:
            json.dump(d, _f, ensure_ascii=False, indent=1)
        os.replace(_tmp, fp)
        # 写后回读（M-R71）
        back = json.load(open(fp, encoding='utf-8'))
        return any(item.get('sid', '') in str(x) for x in back.get('backlog', []))
    except Exception as e:
        print(f'  ⚠️ 派单通知失败（{owner}）：{type(e).__name__}: {e}')
        return False


if __name__ == '__main__':
    print('清账司联动门面自检：')
    print('  core 可用：', _core() is not None)
    for m in ('register_risk_face', 'file_node_to_janitor', 'notify_owner'):
        print(f'  {m}: ', callable(globals()[m]))
