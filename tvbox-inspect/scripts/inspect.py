#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tvbox-inspect（监察司）CLI 入口 —— 生态监测总控

定位：**监测总控**（发现层）。三类监测统一收口：
  · A 生态自监测  —— 20 信号源活体探针 / 6 判据终验 / 生态评估（S105 自 ecosys-conductor 迁入）
  · B 库/数据监测 —— M01-M18 检查器（S105 自 legacy-watchdog 物理搬迁）
  · C 上游监测    —— 仓库跑路/弃更编排（探活实现留在 probe-hub，本技能只编排）

与 legacy-watchdog（遗迹哨）的分工：
  监察司 = 发现（扫描出遗留清单/体检结论）→ watchdog = 处置（分级/派活/跟踪/报告上云）。
  「发现→处置」是水流关系，非重叠：检查器逻辑只有一份（在本技能）。

用法（tvbox inspect <cmd>）:
  scan / status / monitors / add-monitor           # 全量扫描 / 统计 / 登记册 / 加监测项
  signal-check / verify / assess                   # 生态自监测：探针 / 6判据终验 / 评估
  health / rootcause / gain / tune / hygiene       # 监测项自身健康 / 根因 / 增益 / 调优 / 卫生分
  coverage / dispatch-work / facts / noise         # 覆盖地图 / 派活 / 报告核对 / 噪声
  docsync / attribution / regress / latency        # 文档一致 / 归因 / 回归 / 延迟
  expand / replay / drill / track / add-decision   # 扩面雷达 / 回放 / 下钻 / 留痕 / 决策
"""
import os
import sys

_SELF = os.path.dirname(os.path.abspath(__file__))
if _SELF not in sys.path:
    sys.path.insert(0, _SELF)

import inspect_engine as E  # noqa: E402


def dispatch(argv=None):
    """监察司统一调度。与 blk/evolve/link/janitor 同契约（返回 int，供 tvbox 调度器）。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ['scan']
    cmd = argv[0]

    # ── A 类：生态自监测（S105 自 ecosys-conductor 迁入） ──
    if cmd in ('signal-check', 'signal', 'probe-sig'):
        return _delegate_conductor('signal-check', argv[1:])
    if cmd == 'verify':
        return _delegate_conductor('verify', argv[1:])
    if cmd == 'assess':
        return _delegate_conductor('assess', argv[1:])

    # ── B/C 类：库/数据/上游监测（引擎自带 dispatch）──
    return E.dispatch(argv)


def _delegate_conductor(sub, rest):
    """A 类自监测命令委托给 conductor 的对应实现（S105 过渡期）。

    **为何委托而非复制**：conductor 的 _sig_* 20 路信号源与 verify/assess 深度耦合
    conductor 的 SKILL_ROOT/评分器/生态常量；S105 阶段先做「统一入口收口」
    （用户从 tvbox inspect 一个入口进），底层实现暂由 conductor 提供；
    后续 S106 可将实现物理迁入本技能（届时仅改本函数为本地调用）。
    """
    try:
        CON = '/root/.codebuddy/skills/ecosys-conductor/scripts'
        if CON not in sys.path:
            sys.path.insert(0, CON)
        import conductor  # type: ignore
        return conductor.dispatch([sub] + list(rest))
    except Exception as e:
        print(f'  ⚠️ 自监测命令 {sub} 委托失败（{type(e).__name__}: {e}）'
              f'——请直接用 tvbox conductor {sub}')
        return 1


if __name__ == '__main__':
    raise SystemExit(dispatch())
