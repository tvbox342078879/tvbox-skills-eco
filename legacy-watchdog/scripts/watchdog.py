#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
legacy-watchdog（遗迹哨）—— 遗留问题**处置**层（S105 拆分后的新定位）

定位变更（S105）：
  **监测引擎已物理搬迁**到 `tvbox-inspect`（监察司）。本技能现专注「处置闭环」：
  分级 → 派活 → 跟踪 → 报告上云。检查器逻辑只有一份（在监察司），
  本技能通过 `inspect_engine` 调用，结构上不可能与监察司重叠。

分工：
  监察司（tvbox-inspect）= 发现（扫描出遗留清单 / 体检结论 / 探针）
  遗迹哨（legacy-watchdog）= 处置（分级 / 派活 / 账龄 / 趋势 / 报告上云）

用法:
  python3 watchdog.py report [--out 路径] [--upload]   # 流程化监测报告（处置产出）
  python3 watchdog.py diff / trend / aging             # 环比 / 趋势 / 账龄
  python3 watchdog.py dispatch-work                    # 卫生分短板自动派活
  python3 watchdog.py trend-upload                     # 趋势留档上云
  （监测类命令 scan/status/health/coverage/... 已迁至 tvbox inspect，旧命令仍可用——转发）
"""
import os, sys, json, time, argparse, datetime, subprocess
from collections import Counter

# ── 监测引擎（监察司）— 单一事实源，禁在此重复实现检查器 ──
INSPECT = '/root/.codebuddy/skills/tvbox-inspect/scripts'
if INSPECT not in sys.path:
    sys.path.insert(0, INSPECT)
import inspect_engine as E  # noqa: E402
from inspect_engine import *      # noqa: E402,F401,F403  复用全部检查器/常量/内部工具
# `import *` 不含下划线私有名——处置命令依赖的内部工具需显式导入
from inspect_engine import (      # noqa: E402,F401
    _gain_rows, _coverage_rows, _hygiene_rows, _noise_rows, _regress_rows,
    _replay_rows, _drill_rows, _latency_rows, _expand_rows, _track_rows,
    _attr_rows, _snap_periods, _trend_md, _upload_report,
    diff_findings, load_prev_snapshot, load_monitors,
    _upstream_targets, check_upstream_gone,   # M18 上游跑路检查器（信号源19 哨兵依赖）
    cmd_scan, cmd_status, cmd_monitors, cmd_add_monitor,
    cmd_health, cmd_rootcause, cmd_gain, cmd_tune, cmd_hygiene, cmd_coverage,
    cmd_facts, cmd_noise, cmd_docsync, cmd_attribution, cmd_regress,
    cmd_latency, cmd_expand, cmd_replay, cmd_drill, cmd_track, cmd_add_decision,
    SEV_ORDER,
)

HUB = '/root/.codebuddy/skills/tvbox-source-hub/scripts'
sys.path.insert(0, HUB)

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ★ S163 修（M-R38 单一事实源）：本文件原来指向 legacy-watchdog 自己的 findings_history.json。
#   S105 迁移后快照写入在监察司（inspect_engine.save_snapshot），本技能这份成了**死副本**——
#   实测停在 2026-10-16（监察司真源已到 10-18），trend/aging 据此显示「0 项持平」，
#   而实况 5 项遗留。这正是 M-R40「副本静默过期」：不报错、不告警，安静得跟正常没两样。
#   修法＝读监察司同一份文件，本技能不再持有副本。
SNAP_HIST = '/root/.codebuddy/skills/tvbox-inspect/findings_history.json'

# 监测类命令（已迁监察司）→ 转发
_DELEGATE = {
    'scan','status','monitors','add-monitor','health','rootcause','gain','tune',
    'hygiene','coverage','facts','noise','docsync','attribution','regress',
    'latency','expand','replay','drill','track','add-decision',
}


def cmd_report(a):
    mon = load_monitors()
    exe_fail, empty_src = selfcheck_checkers(mon)   # 健康预检（写入报告，防伪达标）
    findings = cmd_scan(argparse.Namespace())
    now = datetime.datetime.now()
    stamp = now.strftime('%m%d')
    out = a.out or f'/workspace/生态{stamp}_遗留问题监测报告.md'
    # 版本 diff（本次 vs 上期遗留指纹快照）
    added, cleared, persist, state = diff_findings(findings)
    prev_n = len(load_prev_snapshot()[1])
    _state_note = {'none': '（首期，无基准）', 'ok': f'（上期 {prev_n} 项）',
                   'corrupt': '（⚠️ 上期快照损坏，基准缺失）'}[state]
    lines = [
        f"# 生态{stamp}_遗留问题监测报告",
        "",
        f"> 生成时间：{now.strftime('%Y-%m-%d %H:%M')} ｜ 监测项 {len(mon['monitors'])} 个 ｜ "
        f"遗留合计 {len(findings)} 项",
        f"> 环比上期：新增 {len(added)} ｜ 已清零 {len(cleared)} ｜ 持续 {len(persist)}"
        + _state_note,
        "",
        "## 〇、遗留增减趋势（本期 vs 上期）",
        "",
    ]
    if state == 'corrupt':
        lines.append(f"⚠️ **上期快照 findings_history.json 解析失败**——本期无可靠基准，"
                     f"下列「新增 {len(added)}」**不等于真实新增**（是基准缺失的假象）。"
                     f"请修复/恢复该文件后重跑 report，勿据本趋势调整优先级。")
    elif not prev_n:
        lines.append(f"首期报告，基准已建立（{len(findings)} 项）。下期起自动显示增减。")
    else:
        lines.append(f"| 类别 | 数量 | 说明 |")
        lines.append(f"|---|---|---|")
        lines.append(f"| 🆕 本期新增 | {len(added)} | 上期没有、本期出现的遗留 |")
        lines.append(f"| ✅ 已清零 | {len(cleared)} | 上期有、本期已解决 |")
        lines.append(f"| ♻️ 持续 | {len(persist)} | 两期都在（未推动） |")
        if cleared:
            lines.append("")
            lines.append("**已清零明细**：")
            for v in cleared:
                lines.append(f"- [{v.get('monitor','')}] {str(v.get('item',''))[:70]}")
        if added:
            lines.append("")
            lines.append("**新增明细**：")
            for f in added:
                lines.append(f"- [{f.get('monitor','')}] {str(f.get('item',''))[:70]}")
        if persist:
            lines.append("")
            lines.append(f"⚠️ **持续项 {len(persist)} 条未推动**——建议本期优先处理（清单见下）")
    lines += [
        "",
        "## 一、遗留清单（按严重度排序）",
        "",
    ]
    if not findings:
        lines.append("✅ 本次扫描无遗留，所有监测项达标。")
    for sev in ['阻断', '耗时', '轻微']:
        grp = [f for f in findings if f['sev'] == sev]
        if not grp:
            continue
        lines.append(f"### {sev}（{len(grp)} 项）")
        lines.append("")
        for f in grp:
            lines.append(f"- **[{f['monitor']}] {f['name']}**：{f['item']}")
            lines.append(f"  - 详情：{f['detail']}")
            lines.append(f"  - 下一步：{f['action']}")
        lines.append("")
    lines += [
        "## 二、监测项覆盖（登记册快照）",
        "",
        f"🩺 检查器健康：{len(mon['monitors'])} 个检查器中 "
        + (f"**{len(exe_fail)+len(empty_src)} 项告警**（下方「0 遗留」可能失真）：" if (exe_fail or empty_src) else "全部执行正常、无数据源空。"),
        "",
    ]
    for r in (exe_fail + empty_src):
        lines.append(f"- ⚠️ {r}")
    if exe_fail or empty_src:
        lines.append("")
    lines += [
        "| ID | 监测项 | 检查类型 | 阈值 | 周期 |",
        "|---|---|---|---|---|",
    ]
    for m in mon['monitors']:
        lines.append(f"| {m['id']} | {m['name']} | {m['check']} | {m.get('threshold','')} | {m.get('cadence','')} |")
    lines += [
        "",
        "## 二之二、根因聚类（系统性 vs 偶发）",
        "",
    ]
    if not findings:
        lines.append("无遗留，无需聚类分析。")
    else:
        name_of = {x['id']: x['name'] for x in mon['monitors']}
        by_m = {}
        for f in findings:
            by_m.setdefault(f['monitor'], []).append(f)
        hist_cnt = Counter()
        if os.path.exists(SNAP_HIST):
            try:
                for h in json.load(open(SNAP_HIST, encoding='utf-8')).get('history', []):
                    for mid in {v.get('monitor', '') for v in (h.get('keys') or {}).values()}:
                        hist_cnt[mid] += 1
            except Exception:
                pass
        tot = len(findings)
        top = max(by_m.items(), key=lambda x: len(x[1]))
        sys_flag = len(top[1]) >= tot * 0.6 and tot >= 3
        lines.append(f"> 判读：{'**系统性**——' + str(int(len(top[1])/tot*100)) + '% 集中在 ' + top[0] + '，建议改流程/口径而非逐条清' if sys_flag else '**偶发为主**——最集中的 ' + top[0] + ' 仅占 ' + str(int(len(top[1])/tot*100)) + '%，逐条清即可'}")
        lines += ["", "| 监测项 | 本项数 | 占比 | 历史出现期数 | 性质 |", "|---|---|---|---|---|"]
        for mid, arr in sorted(by_m.items(), key=lambda x: -len(x[1])):
            hc = hist_cnt.get(mid, 0)
            nature = ('🔥 反复（顽固）' if hc >= 2 else
                      ('⚠️ 系统性' if len(arr) >= tot * 0.5 and tot >= 3 else '偶发'))
            lines.append(f"| {mid} {name_of.get(mid,'')} | {len(arr)} | "
                         f"{int(len(arr)/tot*100)}% | {hc} | {nature} |")
        stubborn = [mm for mm, c in hist_cnt.items() if c >= 2 and mm in by_m]
        if stubborn:
            lines += ["", f"⚠️ **反复出现（历史 ≥2 期）**：{', '.join(stubborn)}——优先处理（之前清理未断根）"]
    lines += [
        "",
        "## 二之三、检查器增益（哪个监测项真抓到了遗留）",
        "",
    ]
    gain_rows = _gain_rows()
    if not gain_rows:
        lines.append("历史快照不足（<2 期），暂无法统计增益。")
    else:
        hit_any = [r for r in gain_rows if r[0] > 0]
        closed_any = [r for r in gain_rows if r[4] > 0]
        zero = [r[1] for r in gain_rows if r[0] == 0]
        lines.append(f"> 自记录以来（{_snap_periods()} 期）共 **{len(hit_any)}/{len(gain_rows)}** 个检查器抓到过遗留，"
                     f"其中 **{len(closed_any)}** 个推动了闭环；**{len(zero)}** 个零增益需复核（阈值过松 or 对象长期健康）。")
        lines += ["", "| 监测项 | 累计命中 | 出现期数 | 闭环数 | 价值判读 |", "|---|---|---|---|---|"]
        for hits, mid, nm, seen, cl in gain_rows[:10]:
            v = ('⚪ 零增益' if hits == 0 else
                 (f'🟢 高价值（抓{hits}清{cl}）' if cl > 0 else
                  ('🟡 高频未闭环' if hits >= 3 else '🔵 有效')))
            lines.append(f"| {mid} {nm} | {hits} | {seen} | {cl} | {v} |")
        if zero:
            lines += ["", f"⚪ 零增益检查器（{len(zero)} 个，建议复核）：{', '.join(zero)}"]
    # 二之四：调优建议（基于 since_period 的真实参与期数）
    lines += ["", "## 二之四、阈值调优建议（怎么调）", ""]
    periods = _snap_periods()
    if periods < 2:
        lines.append("历史不足（<2 期），暂无法给调优建议。")
    else:
        mon_all = load_monitors()
        by_id = {x['id']: x for x in mon_all['monitors']}
        sug_buckets = {}
        for hits, mid, nm, seen, cl in gain_rows:
            since = by_id.get(mid, {}).get('since_period', 1)
            active = max(periods - since + 1, 0)
            if active < 2:
                s = '➖样本不足'
            elif seen == 0 and active >= 3:
                s = '🔻关注/降频'
            elif seen == active and cl == 0 and hits > 0:
                s = '🔺收紧'
            elif hits > 0 and cl == 0:
                s = '⬆️提优先级'
            elif cl > 0:
                s = '✅维持'
            else:
                s = '➖观察'
            sug_buckets.setdefault(s, []).append(mid)
        lines += ["| 建议 | 检查器 |", "|---|---|"]
        for s, ids in sug_buckets.items():
            lines.append(f"| {s} | {', '.join(ids)} |")
        lines.append("")
        lines.append("> ⚠️ 命中率按 since_period 之后的**参与期数**计算——新上线检查器零命中属正常，勿误剔。")
    # 二之五：生态卫生分
    lines += ["", "## 二之五、生态卫生分（跨技能横向对比）", ""]
    try:
        hrows = _hygiene_rows()
        lines += ["| 技能 | 未清 backlog | 自检项 | 三件套 | 卫生分 |", "|---|---|---|---|---|"]
        for score, name, s, bad in sorted([r for r in hrows if r[0] >= 0], key=lambda x: -x[0]):
            tag = '🟢' if score >= 85 else ('🟡' if score >= 70 else '🔴')
            lines.append(f"| {name} | {s['uncleared']} | {s['self_check']} | {4-s['contract']}/4 | {tag}{score:.0f} |")
    except Exception as e:
        lines.append(f"（卫生分生成失败：{e}）")
    # 二之六：监测覆盖率地图
    lines += ["", "## 二之六、监测覆盖率地图（哪些风险面还没检查器）", ""]
    try:
        crows = _coverage_rows()
        blanks = [r[0] for r in crows if r[4]]
        lines.append(f"> 17 项检查器归入 6 大风险面；**{len(blanks)}** 个面未达期望覆盖。")
        lines += ["", "| 风险面 | 覆盖 | 期望 | 检查器 | 状态 |", "|---|---|---|---|---|"]
        for face, desc, ids, expect, blank in crows:
            lines.append(f"| {face} | {len(ids)} | {expect} | {', '.join(ids) or '（无）'} | {'⬜ 空白' if blank else '🟩 已覆盖'} |")
        if blanks:
            lines += ["", f"⬜ 空白风险面：{', '.join(blanks)}——建议补检查器"]
    except Exception as e:
        lines.append(f"（覆盖率生成失败：{e}）")
    # 二之七：报告正文自检（上期报告数字 vs 本期实况）
    lines += ["", "## 二之七、报告正文自检（本报告数字 vs 实况交叉核对）", ""]
    lines.append(f"> 本报告由脚本生成，下列数字**均于生成本刻从数据源实时计算**，无手工硬编码：")
    lines.append(f"> 监测项 {len(mon['monitors'])} 个 ｜ 遗留合计 {len(findings)} 项 ｜ "
                 f"检查器异常/源空告警 {len(exe_fail)+len(empty_src)} 项。")
    lines.append("")
    lines.append("> 历史报告若被手工编辑或数据源后续变动，其数字会与实况漂移；")
    lines.append("> 用 `tvbox watchdog facts` 可核对**上一份报告**与当前实况是否一致（防陈旧/笔误漂移）。")
    # 二之八：文档一致性（SKILL 声明 vs 代码）
    lines += ["", "## 二之八、文档一致性（SKILL 声明 vs 代码实况）", ""]
    try:
        import re as _re
        skf = os.path.join(SKILL_DIR, 'SKILL.md')
        skt = open(skf, encoding='utf-8').read() if os.path.exists(skf) else ''
        code = open(os.path.join(SKILL_DIR, 'scripts', 'watchdog.py'), encoding='utf-8').read()
        live_cmds = len(_re.findall(r"sub\.add_parser\('([a-z-]+)'\)", code))
        dc = 0
        m1 = _re.search(r'登记册\s*(\d+)\s*类检查', skt)
        if m1 and int(m1.group(1)) != len(mon['monitors']):
            dc += 1
        m2 = _re.search(r'检查器明细（M01-M(\d+)）', skt)
        if m2 and int(m2.group(1)) != len(mon['monitors']):
            dc += 1
        if dc:
            lines.append(f"⚠️ **{dc} 项文档漂移**——SKILL.md 声明与代码不一致（用 `tvbox watchdog docsync` 定位）")
        else:
            lines.append(f"✅ SKILL.md 与代码实况一致（检查器 {len(mon['monitors'])} · 命令 {live_cmds}）")
    except Exception as e:
        lines.append(f"（文档一致性生成失败：{e}）")
    # 二之九：检查器噪声（假阳性）度量
    lines += ["", "## 二之九、检查器噪声（假阳性）度量", ""]
    try:
        nrows = [r[:6] for r in _noise_rows()]
        noisy = [r for r in nrows if r[4] > 0 and r[5] >= 0.5]
        if noisy:
            lines += ["| 检查器 | 命中 | 豁免撤销 | 噪声比 |", "|---|---|---|---|"]
            for mid, nm, hits, w, total, ratio in noisy:
                lines.append(f"| {mid} {nm} | {hits} | {w} | {ratio:.0%} |")
            lines.append("")
            lines.append(f"> 🔴 高噪声 {len(noisy)} 个——建议复核豁免原因，必要时收紧口径（参考 M13 FUNC_HINT 防护词表）。")
        else:
            lines.append("✅ 无高噪声检查器（噪声比均 <50%）")
        lines.append("> 噪声比 = 豁免撤销 / (命中+豁免)；含正当豁免，故只提示不判错。")
    except Exception as e:
        lines.append(f"（噪声度量生成失败：{e}）")
    # 二之十：豁免原因归因（noise 的下钻）
    lines += ["", "## 二之十、豁免原因归因（为什么报这么多）", ""]
    try:
        ag = _attr_rows()
        tot_w = sum(len(v) for v in ag.values())
        if tot_w:
            lines += ["| 成因型 | 条数 | 占比 | 说明 |", "|---|---|---|---|"]
            _d = {'入库型': '有源增量，状态停留中间态', '文档型': '增量在底包库/交付物',
                  'SOP/流程型': '流程实质完成，状态未改写', '工具型': '工具/脚本交付', '其他': '未归类'}
            for g, arr in sorted(ag.items(), key=lambda x: -len(x[1])):
                lines.append(f"| {g} | {len(arr)} | {len(arr)/tot_w:.0%} | {_d.get(g,'')} |")
            big = [g for g, arr in ag.items() if len(arr) >= 3 and g != '其他']
            lines.append("")
            if big:
                lines.append(f"> 💡 **{', '.join(big)}** 出现 ≥3 次——已从「事后补白名单」升级为 **pre_exempt 证据驱动预豁免**"
                             f"（M01 现按「新增源数>0 或 成果产出含交付物特征」直接豁免）。")
            else:
                lines.append("> 白名单规模可控，暂无需升级预豁免规则。")
        else:
            lines.append("无豁免记录。")
    except Exception as e:
        lines.append(f"（归因生成失败：{e}）")
    # 二之十一：预豁免健全性回归
    lines += ["", "## 二之十一、预豁免健全性回归（白名单瘦身 + 误放检测）", ""]
    try:
        reg = _regress_rows()
        nw = len(load_monitors().get('ledger_waiver', {}).get('entries', []) or [])
        lines.append(f"> 回放全部台账条目（{reg['total']} 条）验证 pre_exempt 不误放真遗留；当前白名单 {nw} 条。")
        lines.append("")
        lines.append(f"- ♻️ 白名单冗余（预豁免可兜）：**{len(reg['redundant'])}** 条")
        lines.append(f"- 🔒 须保留（预豁免兜不住）：**{len(reg['keep'])}** 条")
        lines.append(f"- 🔴 假阴性（放行但无证据）：**{len(reg['danger'])}** 条 {'✅' if not reg['danger'] else '⚠️ 危险！'}")
        if reg['redundant'] and not reg['keep']:
            lines.append("")
            lines.append("> 💡 白名单已完全冗余——保留 3 条规则边界样本作回归锚点即可（白名单瘦身=口径缺陷已根治的证明）。")
    except Exception as e:
        lines.append(f"（回归生成失败：{e}）")
    lines += ["", "## 二之十二、监测延迟度量（发现及时性·真实时间差）", ""]
    try:
        lat = _latency_rows()
        mg = lat.get('median_gap')
        lines.append(f"> 基于 {lat.get('n_periods', 0)} 期历史；M-R18：期数差会被采样间隙污染"
                     f"（间隙中位数 {mg:.1f}h），故用**真实时间差**度量。")
        lines.append("")
        smp = lat.get('samples') or []
        if smp:
            lines.append(f"- ⏱️ 监测延迟（发现时刻 − 会话落库时刻）：**{len(smp)}** 条可追溯，"
                         f"平均 **{lat['avg']:.1f}h** ｜ 最长 {lat['max']:.1f}h")
        else:
            lines.append("- ⏱️ 监测延迟：暂无可追溯样本（需遗留含 S0xx 且台账有落库时间）")
        cv = lat.get('conv') or []
        if cv:
            cs = sorted(c[0] for c in cv)
            lines.append(f"- ✅ 闭环耗时：**{len(cv)}** 条已闭环，平均 **{sum(cs)/len(cs):.1f}h**（最长 {cs[-1]:.1f}h）")
        lines.append("")
        lines.append("> 💡 延迟越小说明「遗留一产生就被逮住」；某风险面延迟普遍偏高 → 检查器反应慢，建议提频或前置告警。")
    except Exception as e:
        lines.append(f"（延迟度量生成失败：{e}）")
    lines += ["", "## 二之十三、风险面动态扩展雷达（新业务提示）", ""]
    try:
        ex = _expand_rows()
        lines.append(f"> 扫描 {ex['sessions_n']} 条台账；M-R19：扩展建议必须证据驱动，"
                     f"同一新实体 <2 次只是噪声不提示。")
        lines.append("")
        orph = ex.get('orphans') or []
        lines.append(f"- 🧩 孤儿检查器：**{len(orph)}** 个"
                     + (f"（{', '.join(orph)}）" if orph else " ✅"))
        ne = ex.get('new_entities') or []
        if ne:
            lines.append(f"- 🆕 新实体：{', '.join(f'{k}（{v}次）' for k, v in ne[:5])}"
                         " → 评估是否需要新风险面/检查器")
        else:
            lines.append("- 🆕 新实体：无 ✅")
        us = ex.get('unmentioned_skills') or []
        lines.append(f"- 👀 未提及技能：**{len(us)}** 个"
                     + (f"（{', '.join(us[:5])}{'…' if len(us) > 5 else ''}）" if us else " ✅"))
        lines.append("")
        lines.append("> 💡 扩面是**人工决策**——雷达只提供候选，不自动改 RISK_FACES。")
    except Exception as e:
        lines.append(f"（扩展雷达生成失败：{e}）")
    lines += ["", "## 二之十四、检查器时效回归（口径变更回放历史）", ""]
    try:
        rp = _replay_rows()
        lines.append(f"> 回放 {rp['n_keys']} 条历史遗留指纹；M-R20：口径变更后必跑 replay，"
                     f"「今日将不报」的条目必须逐条人工确认，不允许静默消失。")
        lines.append("")
        dead = rp.get('dead') or []
        lines.append(f"- 🔴 失效引用（历史指纹指向已删检查器）：**{len(dead)}** 个"
                     + (f"（{', '.join(dead)}）" if dead else " ✅"))
        dr = rp.get('drift') or []
        lines.append(f"- ⚠️ 严重度漂移：**{len(dr)}** 条"
                     + (f"（{', '.join(f'{m}:{o}→{n}' for m, i, o, n in dr[:3])}）" if dr else " ✅"))
        et = rp.get('exempt_today') or []
        if et:
            lines.append(f"- ♻️ 今日将不报（按当前豁免口径）：**{len(et)}** 条"
                         f"（{', '.join(w for m, i, w in et[:3])}…）→ 逐条人工确认")
        else:
            lines.append("- ♻️ 今日将不报：**0** 条 ✅（历史遗留今日重现仍会全部上报）")
    except Exception as e:
        lines.append(f"（时效回归生成失败：{e}）")
    lines += ["", "## 二之十五、延迟归因下钻（哪类风险面慢·为什么）", ""]
    try:
        dr = _drill_rows()
        if dr.get('n_samples'):
            g = dr.get('global_avg') or 0
            lines.append(f"> {dr['n_samples']} 条延迟样本 · 全局均值 {g:.1f}h；"
                         "M-R21：样本 <3 的面只出「样本不足」不出结论。")
            lines.append("")
            lines.append("| 风险面 | 样本 | 平均 | 最长 | 归因 |")
            lines.append("|---|---|---|---|---|")
            for face, n, avg, mx, verdict, why in dr['faces']:
                lines.append(f"| {face} | {n} | {avg:.1f}h | {mx:.1f}h | **{verdict}** —— {why} |")
        else:
            lines.append("- ⚪ 暂无可追溯延迟样本（等 latency 产出样本后自动出下钻）。")
        lines.append("")
        lines.append("> 💡 「反应慢」面的检查器建议提高扫描频率或前置告警；归因随样本积累自动升级。")
    except Exception as e:
        lines.append(f"（延迟下钻生成失败：{e}）")
    lines += ["", "## 二之十六、扩面决策留痕（候选件件有去向）", ""]
    try:
        tr = _track_rows()
        total = len(tr['candidates']) + len(tr['stale'])
        lines.append(f"> M-R22：雷达候选必须件件有去向，不允许静默忽略。")
        lines.append("")
        pd = tr.get('pending') or []
        lines.append(f"- 🟡 待决策：**{len(pd)}** 个"
                     + (f"（{', '.join(k for k, v in pd[:5])}）" if pd else " ✅"))
        st = tr.get('stale') or []
        lines.append(f"- 📦 过期决策（候选已消失，留痕作历史）：**{len(st)}** 条")
        lines.append(f"- 📊 决策率：**{tr['decided_n']}/{max(total, 1)}**"
                     + ('（100% ✅）' if not pd and total else ''))
    except Exception as e:
        lines.append(f"（决策留痕生成失败：{e}）")
    lines += [
        "",
        "## 三、复盘四问",
        "",
        "1. **成果**：本次扫描发现并分级 N 项遗留，每条带下一步命令（生态联动闭环）",
        "2. **不足**：（扫描后填写——哪些监测项没覆盖到）",
        "3. **改进**：（填写——新监测项登记进 monitors.json / 检查阈值校准）",
        "4. **进化**：遗留清零记录回写各技能 flow.json backlog；复发问题喂 evolve recurring",
        "",
        "## 四、生态联动索引",
        "",
        "- 台账未闭环 → `tvbox log` 补闭环记录",
        "- 失效库待复测 → `tvbox block review`",
        "- 规则整理超期 → `tvbox evolve review` + 六步整理",
        "- 快照未刷 → `tvbox snapshot`",
        "- SOP 校准漂移 → 补 alias 重跑校准集（hub §8.15/8.16）",
        "- 契约缺口 → `tvbox evolve upgrade <技能名>`",
        "",
    ]
    with open(out, 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(lines))
    print(f'📄 监测报告已生成: {out}')
    print(f'   📈 环比上期：新增 {len(added)} / 已清零 {len(cleared)} / 持续 {len(persist)}'
          + _state_note)
    saved, _ = save_snapshot(findings)  # 落盘本期指纹，供下期 diff
    if not saved:
        print('   ⚠️ 快照未落盘：findings_history.json 解析失败或写入失败——')
        print('      已**保留原文件不覆盖**（防销毁上期基准）；请人工修复后重跑 report。')
    if getattr(a, 'upload', False):
        title = f"生态{stamp}_遗留问题监测报告"
        ok, node = _upload_report(out, title)
        if ok:
            print(f'☁️  已云端直传: {title}')
            print(f'   node_id: {node}')
            # 回写锚点，便于下次覆盖同节点（演进：报告云端 node id 自动回写）
            anchor = os.path.join(SKILL_DIR, 'last_report_node.json')
            try:
                hist = []
                if os.path.exists(anchor):
                    hist = json.load(open(anchor, encoding='utf-8')).get('history', [])
                hist.append({'ts': now.strftime('%Y-%m-%d %H:%M'), 'title': title,
                             'node_id': node, 'local': out})
                payload = {'latest': node, 'title': title, 'history': hist[-20:]}
                json.dump(payload, open(anchor, 'w', encoding='utf-8'),
                          ensure_ascii=False, indent=2)
                # ★ S182（招式 T-I「写后回读验证」）：写了 ≠ 写成了。
                #   原来 dump 完直接 print「锚点已回写」——磁盘满/只读/权限不足时
                #   dump 抛异常会被下面 except 抓到，但**写了一半截断**这种情形不抛异常，
                #   于是下一轮读锚点拿到坏 JSON，走 except 当"没有锚点"，
                #   表现为「每次都新建节点」而毫无报错。必须回读比对。
                back = json.load(open(anchor, encoding='utf-8'))
                if back.get('latest') != node:
                    print(f'   ⚠️ 锚点回读不一致（写 {node} / 读 {back.get("latest")}）'
                          f'——已保留原文件，请人工核对 {anchor}')
                else:
                    print(f'   锚点已回写（回读校验通过）: {anchor}')
            except Exception as e:
                print(f'   ⚠️ 锚点回写失败: {e}')
        else:
            print(f'☁️  云端直传失败: {node}')
            print(f'   （本地报告已落盘，可稍后 `tvbox watchdog report --upload` 重试）')
        # 趋势留档同步上云（跨设备可读）——与报告同批，避免遗漏
        try:
            md, n = _trend_md()
            if md:
                tp = os.path.join('/tmp', 'watchdog_trend.md')
                open(tp, 'w', encoding='utf-8').write(md)
                tok, tnode = _upload_report(tp, '生态遗留趋势留档（跨设备）')
                if tok:
                    json.dump({'node_id': tnode, 'periods': n,
                               'ts': now.strftime('%Y-%m-%d %H:%M')},
                              open(os.path.join(SKILL_DIR, 'trend_node.json'), 'w',
                                   encoding='utf-8'), ensure_ascii=False, indent=2)
                    print(f'☁️  趋势留档已上云: {tnode}（{n} 期）')
                else:
                    print(f'   ⚠️ 趋势留档上云失败: {tnode}')
        except Exception as e:
            print(f'   ⚠️ 趋势留档上云异常: {e}')
    else:
        print(f'   （如需同步云端：tvbox watchdog report --upload）')




def cmd_trend(a):
    """历史遗留趋势：读 findings_history.json，输出分期数量表 + 环比。"""
    if not os.path.exists(SNAP_HIST):
        print('📈 暂无历史快照（首次跑 report 后建立基准）')
        return
    try:
        d = json.load(open(SNAP_HIST, encoding='utf-8'))
    except Exception as e:
        print(f'📈 快照损坏无法读取趋势：findings_history.json 解析失败（{type(e).__name__}）')
        print('   请人工修复该文件（勿直接删除——删除即永久丢失历史基准）。')
        return
    hist = d.get('history', [])
    if not hist:
        print('📈 历史快照为空')
        return
    print(f'📈 遗留趋势（{len(hist)} 期，最近 30 期滚动）\n')
    print(f'{"期次":4} {"时间":17} {"合计":>4} {"阻断":>4} {"耗时":>4} {"轻微":>4} {"环比":>8}')
    print('-' * 56)
    prev_keys = None
    for i, h in enumerate(hist, 1):
        bs = h.get('by_sev', {})
        keys = set((h.get('keys') or {}).keys())
        if prev_keys is None:
            delta = '基准'
        else:
            dn = len(keys) - len(prev_keys)
            delta = f'{dn:+d}' if dn else '0'
        print(f'{i:<4} {h.get("ts",""):17} {h.get("n",0):>4} '
              f'{bs.get("阻断",0):>4} {bs.get("耗时",0):>4} {bs.get("轻微",0):>4} {delta:>8}')
        prev_keys = keys
    # 趋势判读
    if len(hist) >= 2:
        first, last = hist[0].get('n', 0), hist[-1].get('n', 0)
        arrow = '📉 下降' if last < first else ('📈 上升' if last > first else '➡️ 持平')
        print(f'\n趋势：{first} → {last} 项（{arrow}）')




def cmd_aging(a):
    """遗留闭环耗时统计（S031 新功能）：从历史指纹快照反推每条遗留的「存活期数」。
    · 持续存活：该指纹在连续多期出现 → 存活期数 = 首次出现的期次 到 最新期次
    · 已闭环：上期有、本期无 → 记录其「从出现到清零」经历的期数（闭环耗时）
    输出：① 存活期数排行榜（最顽固遗留）② 闭环耗时分布 ③ 当前未闭环项的平均账龄
    价值：把「哪些遗留最顽固」从感觉变成量化指标，指导优先处理顺序。"""
    if not os.path.exists(SNAP_HIST):
        print('📉 暂无历史快照（至少跑 2 期 report/scan 后才能统计）')
        return
    try:
        d = json.load(open(SNAP_HIST, encoding='utf-8'))
    except Exception as e:
        print(f'📉 快照损坏无法统计账龄：findings_history.json 解析失败（{type(e).__name__}）')
        print('   请人工修复该文件（勿直接删除——删除即永久丢失历史基准）。')
        return
    hist = d.get('history', [])
    if len(hist) < 2:
        print(f'📉 历史仅有 {len(hist)} 期，不足以统计闭环耗时（需 ≥2 期）')
        return
    # 反推每条指纹的出现期序列
    seen = {}   # key -> [期次下标...]
    meta = {}   # key -> {monitor,item}
    for i, h in enumerate(hist):
        for k, v in (h.get('keys') or {}).items():
            seen.setdefault(k, []).append(i)
            meta.setdefault(k, v)
    last_i = len(hist) - 1
    active, closed = [], []
    for k, idxs in seen.items():
        life = idxs[-1] - idxs[0] + 1          # 首次到最后一次跨越的期数
        if idxs[-1] == last_i:
            active.append((life, idxs[0], meta[k]))   # 最新期仍在 → 未闭环
        else:
            closed.append((life, idxs[0], idxs[-1], meta[k]))  # 中途消失 → 已闭环
    print(f'📉 遗留闭环耗时统计（基于 {len(hist)} 期历史）\n')
    # ① 当前未闭环：按存活期数降序
    if active:
        print(f'【未闭环 {len(active)} 条·按账龄降序】最顽固优先处理')
        for life, start, m in sorted(active, key=lambda x: (-x[0], x[1])):
            tag = '🔥' if life >= 3 else ('⚠️' if life == 2 else '·')
            print(f'  {tag} 存活 {life} 期（自第 {start+1} 期）[{m.get("monitor","")}] '
                  f'{str(m.get("item",""))[:56]}')
    else:
        print('【未闭环】无——当前所有监测项达标 ✅')
    # ② 已闭环：耗时分布
    if closed:
        print(f'\n【已闭环 {len(closed)} 条·按耗时降序】')
        for life, s0, s1, m in sorted(closed, key=lambda x: -x[0]):
            print(f'  ✅ 历时 {life} 期闭环（第 {s0+1}→{s1+1} 期）[{m.get("monitor","")}] '
                  f'{str(m.get("item",""))[:56]}')
        avg = sum(x[0] for x in closed) / len(closed)
        print(f'  平均闭环耗时 {avg:.1f} 期')
    else:
        print('\n【已闭环】暂无（历史中还没有"出现→清零"的完整样本）')
    # ③ 判读
    if active:
        avg_age = sum(x[0] for x in active) / len(active)
        print(f'\n判读：未闭环平均账龄 {avg_age:.1f} 期；'
              f'最久 {max(x[0] for x in active)} 期——建议优先清账龄 ≥3 期的"顽固遗留"')




def cmd_diff(a):
    """仅算 diff，不生成报告（快速看增减）。"""
    findings = cmd_scan(argparse.Namespace())
    added, cleared, persist, state = diff_findings(findings)
    prev_n = len(load_prev_snapshot()[1])
    base = {'none': '首期，无基准', 'ok': f'上期 {prev_n} 项',
            'corrupt': '⚠️ 上期快照损坏（基准不可信）'}[state]
    print(f'\n📊 遗留 diff（本期 {len(findings)} 项 vs {base}）')
    if state == 'corrupt':
        print('   ⚠️ findings_history.json 解析失败——下列「新增」**不代表真新增**，')
        print('      而是基准缺失导致的假象；修复快照后重跑，勿据此调整优先级。')
    print(f'   🆕 新增 {len(added)} ｜ ✅ 已清零 {len(cleared)} ｜ ♻️ 持续 {len(persist)}')
    for label, arr in (('新增', added), ('已清零', cleared)):
        if arr:
            print(f'\n  【{label}】')
            for x in arr:
                it = x.get('item', '') if isinstance(x, dict) else x
                print(f'    - [{x.get("monitor","")}] {str(it)[:70]}')
    if persist:
        print(f'\n  【持续未推动 {len(persist)} 条】')
        for x in persist[:10]:
            print(f'    - [{x.get("monitor","")}] {str(x.get("item",""))[:70]}')




def cmd_dispatch_work(a):
    """卫生分短板自动派活（S034 新功能）：hygiene 只「诊断」，dispatch_work 把短板转成可执行项。
    对每个技能：
      · backlog 未清 > 0 → 生成「清理 backlog」待办
      · 三件套缺项 > 0 → 生成「补齐三件套」待办
      · 自检项 < 5 → 生成「扩充 self_check」待办
    输出「技能 → 待办清单」，可直接抄进对应技能 flow.json backlog（人工确认后）。
    封闭 hygiene 的循环：诊断 → 派活。"""
    ranked = _hygiene_rows()
    print('📋 卫生分短板派活（把诊断转成待办）\n')
    plan = {}
    for score, name, s, bad in sorted(ranked, key=lambda x: -x[0]):
        if score < 0:
            plan[name] = ['flow.json 缺失——先建三件套']
            continue
        tasks = []
        if s['uncleared'] > 0:
            tasks.append(f'清理 backlog 未清 {s["uncleared"]} 项（逐条闭环或标注 [已清零]）')
        if s['contract'] > 0:
            tasks.append(f'补齐三件套缺 {s["contract"]} 项（version/CHANGELOG/触发词/flow.json）')
        if s['self_check'] < 5:
            tasks.append(f'扩充 self_check（当前 {s["self_check"]} 项，建议 ≥5）')
        if tasks:
            plan[name] = tasks
    if not plan:
        print('✅ 全生态无短板，无需派活')
        return plan
    for name, tasks in plan.items():
        print(f'  📌 {name}（{len(tasks)} 项）')
        for t in tasks:
            print(f'      - {t}')
    print(f'\n> 共 {len(plan)} 个技能有短板，{sum(len(v) for v in plan.values())} 条待办')
    print('> 落地方式：逐条抄进对应技能 flow.json 的 backlog（人工确认，不自动改）')
    return plan




def cmd_trend_upload(a):
    """把趋势留档上云（跨设备可读）。复用 create_doc.py，node id 存 trend_node.json。"""
    md, n = _trend_md()
    if md is None:
        print('📈 暂无趋势数据（先跑几次 report/scan）')
        return
    tmp = os.path.join('/tmp', 'watchdog_trend.md')
    open(tmp, 'w', encoding='utf-8').write(md)
    ok, node = _upload_report(tmp, '生态遗留趋势留档（跨设备）')
    if ok:
        anchor = os.path.join(SKILL_DIR, 'trend_node.json')
        try:
            json.dump({'node_id': node, 'periods': n,
                       'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')},
                      open(anchor, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        except Exception:
            pass
        print(f'☁️  趋势留档已上云（{n} 期）')
        print(f'   node_id: {node}')
        print(f'   锚点: {anchor}')
    else:
        print(f'☁️  趋势上云失败: {node}')




def dispatch(argv):
    p = argparse.ArgumentParser(prog='watchdog')
    sub = p.add_subparsers(dest='cmd')
    # ── S133 P005 收敛：4 个正式入口（旧 27 命令名保留为别名，全部映射到入口内子动作）──
    rp = sub.add_parser('report')          # ① 报告：生成 + 可选上云
    rp.add_argument('--out', default='')
    rp.add_argument('--upload', action='store_true')
    tr = sub.add_parser('trend')           # ② 趋势：趋势表 / 账龄 / 上云
    tr.add_argument('action', nargs='?', default='show', choices=['show', 'aging', 'upload'])
    ad = sub.add_parser('admin')           # ③ 运维：环比 / 派活 / 登记
    ad.add_argument('action', nargs='?', default='diff',
                    choices=['diff', 'dispatch-work', 'add-decision'])
    ad.add_argument('--entity', default='')
    ad.add_argument('--verdict', default='暂不')
    ad.add_argument('--note', default='')
    sc = sub.add_parser('scan')            # ④ 巡检：转发监察司（唯一监测真源）
    sc.add_argument('rest', nargs=argparse.REMAINDER)
    # 旧命令名别名（向后兼容，等价于对应入口；已迁监察司的直接转发）
    for _alias, _entry in (
        ('status','scan'), ('monitors','scan'), ('health','scan'), ('rootcause','scan'),
        ('gain','scan'), ('tune','scan'), ('hygiene','scan'), ('coverage','scan'),
        ('facts','scan'), ('noise','scan'), ('docsync','scan'), ('attribution','scan'),
        ('regress','scan'), ('latency','scan'), ('expand','scan'), ('replay','scan'),
        ('drill','scan'), ('track','scan'), ('add-monitor','scan'),
        ('aging','trend'), ('diff','admin'), ('dispatch-work','admin'),
        ('add-decision','admin'), ('trend-upload','trend'),
    ):
        sub.add_parser(_alias)   # 旧名；参数以原始 argv 透传，不经本层解析
    a, _unknown = p.parse_known_args(argv)   # S133：旧名带参数时残余透传
    a.rest = list(_unknown)                  # 未识别的选项/值 → 原样交给下游
    # ── S133 P005：4 入口分派（旧命令名 = 别名，语义等价；监测类一律转发监察司唯一真源）──
    cmd = a.cmd
    # 别名 → 入口 + 子动作
    _ALIAS = {
        'status': ('scan', None), 'monitors': ('scan', None), 'health': ('scan', None),
        'rootcause': ('scan', None), 'gain': ('scan', None), 'tune': ('scan', None),
        'hygiene': ('scan', None), 'coverage': ('scan', None), 'facts': ('scan', None),
        'noise': ('scan', None), 'docsync': ('scan', None), 'attribution': ('scan', None),
        'regress': ('scan', None), 'latency': ('scan', None), 'expand': ('scan', None),
        'replay': ('scan', None), 'drill': ('scan', None), 'track': ('scan', None),
        'add-monitor': ('scan', None),
        'aging': ('trend', 'aging'), 'trend-upload': ('trend', 'upload'),
        'diff': ('admin', 'diff'), 'dispatch-work': ('admin', 'dispatch-work'),
        'add-decision': ('admin', 'add-decision'),
    }
    if cmd in _ALIAS:
        entry, act = _ALIAS[cmd]
        if act is not None:
            a.action = act
        # 监测类旧名：转发监察司同名命令（保留原参数尾巴）
        if entry == 'scan' and cmd not in ('scan',):
            print(f'  ℹ️ `watchdog {cmd}` 已归并入 scan 入口——转发 `tvbox inspect {cmd}`')
            return E.dispatch([cmd] + (list(argv[1:]) if len(argv) > 1 else list(getattr(a, 'rest', []))))
        cmd = entry

    if cmd in (None, 'scan'):
        rest = getattr(a, 'rest', []) or []
        print('  ℹ️ 巡检已迁监察司（唯一监测真源）——转发 `tvbox inspect scan`')
        return E.dispatch(['scan'] + list(rest))

    if cmd == 'report':
        return cmd_report(a)

    if cmd == 'trend':
        act = getattr(a, 'action', 'show')
        if act == 'aging':
            return cmd_aging(a)
        if act == 'upload':
            return cmd_trend_upload(a)
        return cmd_trend(a)

    if cmd == 'admin':
        act = getattr(a, 'action', 'diff')
        if act == 'dispatch-work':
            return cmd_dispatch_work(a)
        if act == 'add-decision':
            if not getattr(a, 'entity', ''):
                print('❌ admin add-decision 需要 --entity'); return 1
            return cmd_add_decision(a)
        return cmd_diff(a)

    print(f'❌ 未知入口：{cmd}（可用：scan / report / trend / admin）')
    return 1


if __name__ == '__main__':
    dispatch(sys.argv[1:])
