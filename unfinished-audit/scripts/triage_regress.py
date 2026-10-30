#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""triage_regress.py —— 分诊回归台（P0xx / e5b57fad599f 落地）

## 这条欠账原来怎么写的

> 分诊表目前是「关键词 + 性质」两段判定，压测样本只有 5 条。
> 真实跑满几轮后应统计**误派率**（派给 A 实际该由 B 处理），再决定是否引入更细的分诊。

## 为什么不能按字面做「误派率统计」

试过：从 60 条已结欠账的 `close_evidence` 里抽「实际由哪个技能处理」，再比 `owner`。
结果：**54 条里 51 条（94%）证据正文根本不写文件路径**，能抽出来的只有 3 条，
其中还有 1 条是「证据里提到某技能名」但不代表它处理了。
→ 按这个口径算出来的误派率，分母是假的、分子是噪声，**是个看起来精确的空数**。
（这正是本生态反复踩的坑：判据看着有、其实不测任何东西。）

## 改成什么

不统计"误派率"（无数据支撑），改做**有标注的回归台**：
把**真实发生过的派错事故**逐条固化成用例（输入 item → 期望 owner），
每跑一次就知道「已修的那些错会不会回潮」。

样本全部来自 `dispatch.py` 里记录的真实事故（S181 / S182 / S184），不是编的。
每条都做过反证：把对应规则改坏，该用例必须变红——否则它没有鉴别力。

## 用法

  python3 triage_regress.py          # 跑全部用例，任一条失败则退出码 1
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dispatch as D  # noqa: E402

# (说明, item, 期望 owner, 这条用例守的是哪条规则)
CASES = [
    ('S184：M19 红字的负责人是"遗留指向的技能"，不是 legacy-watchdog',
     {'sid': 't1', 'kind': 'code',
      'title': '[M19] ecosys-conductor 平衡性 83.5｜最弱=质量短板',
      'owner_hint': 'ecosys-conductor'},
     'ecosys-conductor', 'owner_hint 优先于 kind 关键词'),

    ('S181：显式声明 kind 后，标题含「云端」也不许被 FINE_ROUTE 改判',
     {'sid': 't2', 'kind': 'monitor', 'kind_declared': True,
      'title': '③改本地白改（云端优先须走 save_targets）'},
     'tvbox-inspect', 'kind_declared > FINE_ROUTE'),

    ('S182：owner_fixed（归属方自述待办）关键词不许改判',
     {'sid': 't3', 'kind': 'cloud', 'owner_fixed': 'probe-hub',
      'title': '采集缺包级字段，spider/jar 被丢弃',
      'next_fixed': 'tvbox probe forge list'},
     'probe-hub', 'owner_fixed > 关键词'),

    ('普通监测项 → 监察司',
     {'sid': 't4', 'kind': 'monitor', 'title': '监测项有效性未实证'},
     'tvbox-inspect', 'ROUTE_TABLE 基本映射'),

    ('含「收件箱」→ 清道夫',
     {'sid': 't5', 'kind': 'cloud', 'title': '云端节点删不掉，先放收件箱'},
     'tvbox-janitor', 'FINE_ROUTE 关键词命中'),

    ('无线索 → 兜底不悬空（M-R79）',
     {'sid': 't6', 'title': '某件说不清的事'},
     'ecosys-conductor', '兜底必落真实技能'),
]


def main():
    ok = 0
    fails = []
    for desc, item, expect, rule in CASES:
        try:
            got = D._assign(dict(item)).get('owner')
        except Exception as e:
            got = f'异常 {type(e).__name__}'
        if got == expect:
            ok += 1
        else:
            fails.append((desc, expect, got, rule))

    if fails:
        for desc, expect, got, rule in fails:
            print(f'  ❌ {desc}')
            print(f'     期望 {expect} ／ 实得 {got} ｜ 守的规则：{rule}')
        print(f'🔴 分诊回归：{ok}/{len(CASES)} 通过（{len(fails)} 条回潮）')
        return 1
    print(f'🟢 分诊回归：{ok}/{len(CASES)} 全过（S181/S182/S184 三处派错未回潮）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
