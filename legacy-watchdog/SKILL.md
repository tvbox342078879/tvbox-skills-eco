---
version: "1.34.7"
name: legacy-watchdog
description: "TVBox 生态的历史遗留问题**处置**技能（S105 拆分后新定位：监测引擎已物理搬迁至 tvbox-inspect 监察司，本技能专注处置闭环）。职责：拿到监察司的监测结果（18 检查器扫描的遗留清单 + 20 信号源探针 + 6 判据终验），做分级/派活/账龄/趋势/报告上云——即「发现→处置」中的处置层。产出：分级遗留清单 + 流程化监测报告（支持 --upload 云端直传+node id 回写）+ 逐条「下一步命令」。通过 inspect_engine 复用检查器，单一事实源不重复实现。分工：监察司=盯(发现)/遗迹哨=修(处置)/总指挥=派(调度)。复盘闭环前默认跑一次。 触发词：还有哪些老问题 / 之前那个坑修了吗 / 有什么遗留 / 老问题还在吗 / 之前说的那个还没解决吗 / 有什么没解决的 / 有尾巴吗 / 善后做完了吗 / 还有什么没收尾 / 遗留 / 历史遗留 / 未解决 / 待同步 / 时效 / 到期 / 看门狗 / 遗留清单 / 复盘闭环 / 处置 / 派活 / 账龄 / 分级 / 处理遗留 / 遗留监测 / 老问题监测 / 尾巴 / 善后 / 善后收尾 / 遗留收尾。"
---

> **🗣 语言铁律（M-R110 · 全模型适用）**：本技能所有输出——正文、思考过程、进度播报、
> 工具调用说明、报错与澄清提问——**一律简体中文**。切换模型（快速/Hy3/深度思考等）、
> 新会话、恢复会话、压缩上下文后**均不得回退英文**。用户明确要求中文时尤须遵守。
> 完整习惯快照见 `ecosys-conductor/references/user_habit_brief.md`。

# legacy-watchdog（遗迹哨 · 历史遗留问题处置）

> **定位（S105 变更）**：生态的**处置层**——监察司（tvbox-inspect）负责"盯"（发现遗留），
> 遗迹哨负责"修"（分级 → 派活 → 跟踪 → 报告上云）。**监测引擎已物理迁至监察司**，
> 本技能通过 `inspect_engine` 调用检查器（单一事实源，结构上不可能与监察司重叠）。
> 产出：分级遗留清单 + 流程化监测报告 + 逐条「下一步命令」（生态联动闭环）。
> 与 `skill-evolution` 的分工：evolve 管**复发问题→新技能**；本技能管**未闭环+时效项→监测闭环**。两者互补，都是规则 48 的落地引擎。

## 1. 触发词
遗留 · 历史遗留 · 未解决 · 待同步 · 时效 · 到期 · 超期 · 监测 · 看门狗 · 遗留清单 · 闭环检查

## 2. 两大监测面

| 面 | 数据源 | 检查器 |
|---|---|---|
| ① 未解决问题 | ③会话台账（反哺状态≠已闭环）+ 各技能 flow.json backlog 段 + 正文待办 | `ledger_pending` / `backlog_items` / `ledger_consistency` / `doc_todo` |
| ② 时效性项目 | monitors.json 登记册（内置 15 类，可成长） | `dead_review_due` / `rules_review_due` / `snapshot_stale` / `route_calib` / `eco_contract` / `naming_compliance` / `revive_eval` |
| ③ 库健康度 | ①源总库 + 🗑失效库 互查；②底包库云端节点巡检 | `status_archive`（双向对账）/ `data_quality`（重复+字段缺失）/ `pkg_naming`（根级命名）/ `subtree_naming`（子枝命名） |

## 3. 引擎命令（4 入口 · S133 P005 收敛）

```bash
tvbox watchdog scan                        # ① 巡检：转发监察司（唯一监测真源）；本体 scan/status/monitors/health/
                                           #    rootcause/gain/tune/hygiene/coverage/facts/noise/docsync/
                                           #    attribution/regress/latency/expand/replay/drill/track/add-monitor 全走此入口
tvbox watchdog report [--out 路径] [--upload]  # ② 报告：监测报告 → /workspace/生态<MMDD>_遗留问题监测报告.md
                                           #    （含「〇、遗留增减趋势」段；--upload 云端直传+回写 node id 锚点）
tvbox watchdog trend [show|aging|upload]   # ③ 趋势：show=30 期趋势表 / aging=账龄+闭环耗时 / upload=上云（默认 show）
tvbox watchdog admin [diff|dispatch-work|add-decision]  # ④ 运维：diff=环比增减 / dispatch-work=短板派活 /
                                           #    add-decision --entity X --verdict 扩面|暂不 --note ..（默认 diff）
```

**27 个旧命令名全部保留为别名**（向后兼容，语义等价，`tvbox run "watchdog scan" "watchdog health"` 等既有调用零改动）：

| 归属入口 | 旧命令名 |
|---|---|
| ① scan（转发监察司） | scan, status, monitors, health, rootcause, gain, tune, hygiene, coverage, facts, noise, docsync, attribution, regress, latency, expand, replay, drill, track, add-monitor |
| ② report | report |
| ③ trend | trend, aging, trend-upload |
| ④ admin | diff, dispatch-work, add-decision |

> **为何收敛**：21 个命令本地零实现、纯转发监察司——同一能力两个命令面 = 双份接口负担（M-R38 接口级）。
> 收敛后**入口 4 个真源唯一**，旧名作为兼容壳保留；`--id/--entity` 等参数经 `parse_known_args` 原样透传给监察司。
> **27/27 全量回归通过**（S133 实测，含带参数的 add-monitor）。

- 严重度三级：**阻断**（快照未刷/未收口写操作，当次必处理）/ **耗时**（推进中）/ **轻微**（记录在案）
- **成长机制**：加监测项只改 `monitors.json`（登记册），引擎代码零改动；未实现的 check 类型允许先登记后补代码（scan 会提示）
- **云端直传**：`--upload` 复用 `library` 技能 create_doc.py（禁自建云端通道），node id 回写 `last_report_node.json`（保留最近 20 次）

## 3.1 检查器明细（M01-M17）
| ID | 名称 | 关键口径 |
|---|---|---|
| M01 | 台账未闭环 | 待同步=耗时；已同步*=半闭环（轻微） |
| M02 | 失效库待复测超期 | 复测时间 >7 天 |
| M03 | backlog 未清零 | 跳过 `[已清零]`/`[扩展点]` 前缀 |
| M04 | 规则整理超期 | >7 天 |
| M05 | 快照新鲜度 | **真读云端 `updatedAt`** vs 本地锚点 mtime（取不到则降级硬锚点，不虚报） |
| M06 | SOP 校准集漂移 | 症状数 ≠ 校准记录数 |
| M07 | 生态三件套缺口 | 只查 ECO_SKILLS 白名单（平台技能豁免） |
| M08 | 命名合规（本地） | /workspace 根级 json/md 须含 MMDD |
| M09 | 停用源未归档 | **双向对账**：正向死了没埋 + 反向僵尸源；代理型豁免 |
| M10 | 存量数据质检 | 双库重复 api + 分组类目缺失 |
| M11 | 台账-库实况对账 | 以 `新增源数` 为锚点（台账无 `同步源数` 列·S027 修正）；入库型 0 增量=可疑，文档型豁免 |
| M12 | 命名合规（云端） | 底包库节点标题须含 MMDD；**枝文件夹 + 常驻索引豁免**（`waiver_keywords`） |
| M13 | 正文待办提取 | 扫台账/经验库长文本字段的明写待办；已完成语气豁免 |
| M14 | 失效库复活回流评估 | 抽样**真探活**超期条目（M02 只数数量）；代理型跳过 |
| M15 | 子枝命名抽查 | 递归下探一级查子枝；规范 `<系代号>-<枝名>枝`（**不要求日期**）；判定前须剥括号注解 |
| M16 | 流程命令引用完整性 | flow.json 引用的 `tvbox cmd` 须在 CLI 真实存在；只校验组+子命令级（防漂移） |
| M17 | 底包库索引对账 | 索引节点声明的条目数 vs 实际体系/枝节点数；偏差 >max(3,实际×50%) 即漂移 |

## 3.2 报告环比（`findings_history.json`）
> 每次 `report`/`scan` 落盘本期遗留**稳定指纹**（`monitor+name+item` 归一化），滚动保留 30 期。
> 下期自动输出三类：🆕 **新增**（上期无、本期有）｜ ✅ **已清零**（上期有、本期无）｜ ♻️ **持续**（两期都有，未推动）。
> **归一化防误判**：`_norm()` 去空白+截断 → 「条目B」与「条目 B」判为同一条（持续），不算新增。
> 用途：`diff` 快速看增减；`trend` 看长期趋势；`aging` 看账龄（哪些遗留最顽固）；`trend-upload` 跨设备留档。

## 3.2 豁免机制（两类，均登记在 `monitors.json`，改表不改码）

**① `ledger_waiver` 决策留痕白名单**
> **为什么需要**：M01 把「已同步源库/已同步底包」一律列半闭环（轻微）。但历史上有若干会话**实质已完成、状态停留中间态**——按 S022 决策「不改写历史记录，决策留痕台账」保留原状。若不豁免，每轮复扫都会重复报 6+ 条同样条目，清单虚胖、真遗留被淹没。
>
> **机制**：`ledger_waiver.entries` 登记 `{no, reason}`；M01/M11 读取后跳过。登记须带**数量锚点**（如「源库 963→1127，+164」）或文档交付物，防止白名单变成逃避监测的后门。若后续补齐闭环状态，从此表移除。

**② `waiver_keywords` 常驻节点关键词**
> **为什么需要**：M12 查云端底包库命名。但库里有两类节点天然无日期——**枝文件夹**（体系A/B/C/D，规则53 容器）与**常驻索引节点**（索引/清单/追踪表/可用底包，滚动更新的活节点）。一刀切会 26 节点误报 8 条。
>
> **机制**：`waiver_keywords` 词表 + 代码侧按 `kind=folder 且有子节点` 自动豁免容器。词表可增长。

## 3.3 新增检查器的前置铁律（M-R1）
> **新增监测器的第一动作 = dump 目标库真实字段清单**（`print(rows_of(db)[0].keys())`）。
> 依据：M11 初版按假设的 `同步源数` 列取值，而台账**根本没有该列** → 取值恒空 → **100% 虚报**（且不报错）。
> 同理，新检查器上线前须做**受控单测**（真阳性 1 / 假阳性 0 / 假阴性 0 三点），见 M12/M13 的验证方式。

## 3.4 元层面能力（监测者也要被监测，S032）
> **health**：防「看门狗自己睡着了」——检查器执行异常/数据源为空/长期恒 0 命中，都会让「0 遗留」变成假象。
> scan 与 report **已内置健康预检**（不是可选步骤），有告警时明确提示结论可能失真。
>
> **rootcause**：区分「系统性」与「偶发」——遗留集中在 1-2 个监测项 = 改流程/口径；分散 = 逐条清；
> 某监测项多期反复 = 顽固（之前没断根，优先处理）。report 已自动输出聚类段。
>
> **元能力链（20 项）**：`health`（查失能）→ `gain`（查冗余）→ `tune`（给动作）→ `hygiene`（横向坐标）→
> `coverage`（还有哪里没看）→ `dispatch-work`（诊断转待办）；外围 `facts`（报告数字对实况）/ `docsync`（SKILL 声明 vs 代码）/
> `noise`（谁爱误报）→ `attribution`（为什么）→ `pre_exempt`（根治）→ `regress`（验证根治安全）/
> `latency`（拖多久才被发现）→ `drill`（哪类面慢）→ `expand`（地图外长出什么）→ `replay`（口径变更回放）/
> `track`（候选件件有去向）+ `rootcause`（根因聚类）。
> **每项的判定口径、铁律（M-R9/R18/R19/R20/R21/R22）与实测案例 → `docs/manual_元能力链.md`。**

## 4. 生态联动（L8：监测→闭环→进化）
```
watchdog scan ──┬─ 台账未闭环      → tvbox log 补闭环记录（③）
                ├─ 失效库待复测    → tvbox block review（🗑失效库）
                ├─ 规则整理超期    → tvbox evolve review + 六步整理（规则48）
                ├─ 快照未刷        → tvbox snapshot（规则47）
                ├─ SOP校准漂移     → 补 alias 重跑校准集（hub §8.15/8.16）
                ├─ 契约缺口        → tvbox evolve upgrade <技能名>（规则48/54）
                └─ 同类问题≥3次    → evolve recurring 自动立项（监测→进化闭环）
```
> 监测不许「只报不治」：报告里每条遗留都带下一步命令；处理后回写各技能 flow.json backlog（清零或标注原因）。

## 5. 升级契约（规则 48 + 54 三件套）
- 顶部 `version` + 本 `CHANGELOG.md` + `scripts/flow.json`（机器可读流程）
- 版本提升：`tvbox evolve upgrade legacy-watchdog`
- 扩展点：底包库子枝内部命名抽查 / 失效库复活回流自动评估 / 报告版本 diff（本次 vs 上期遗留增减）

## 6. 关联
- ③ 会话台账：`TO4LwsjVVdR8JRAUlkejk3`
- 🗑 失效源库：`92dSuG6uUbxitGuyx4Saaf`
- 上游全部：生态各技能 flow.json backlog + SOP 权威源 + 唯一权威快照（规则 47）
- 元规则：云 RULES `isuY4U01Me0kUggpMN5RfX`（规则 48/52/54）

## 铁律坐标（规则分卷 · S109）

> S109 规则↔技能联动工程固化：**本技能是以下规则的正本**（技能为正本，总册留索引）。
> 每行 = 一条规则的落地判据；违反即本技能自检失败。

| 规则 | 落地判据 | 来源 |
|---|---|---|
| M-R48 | 提速是迭代的、瓶颈会转移——提速后不许只看总量，要对每个检查器+每个前置段单独计时定位新热点；计时器本身也要核对调用签名 | 总册·S109固化 |
| M-R9 | 命中率须按 `since_period` 之后的**参与期数**算，防新上线检查器被误判「陪跑」（tune） | 总册·S109固化 |
| M-R18 | 时间/时长类度量一律用**真实时间差**，不用期数差——期数差被采样间隙污染（latency） | 总册·S109固化 |
| M-R19 | 扩展建议必须**证据驱动**（台账真实字段），禁脑补风险面；扩面是人工决策（expand） | 总册·S109固化 |
| M-R20 | 改检查器逻辑/严重度/豁免后**必跑 replay**；「今日将不报」不许静默消失（口径悄悄放水） | 总册·S109固化 |
| M-R21 | 下钻结论须**标样本量**，<3 条只出「样本不足」不出结论（drill） | 总册·S109固化 |
| M-R22 | 雷达候选**件件有去向**（扩面/补检查器/并入已知面/暂不），用 add-decision 落痕（track） | 总册·S109固化 |
