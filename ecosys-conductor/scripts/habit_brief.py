#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""habit_brief.py —— 用户习惯摘要生成器（M-R112 习惯随身 / M-R110 中文铁律）

用途：
  生成「模型无关」的用户习惯摘要，供**任何模型、任何会话**开场加载。
  解决「换个模型就忘了用户习惯」（尤其语言要求反复回退）的顽疾。

命令：
  habit_brief.py                生成 / 刷新 references/user_habit_brief.md（幂等，合并动态段）
  habit_brief.py show           打印当前摘要（会话开场快速读取）
  habit_brief.py handoff --task "..." --state "..." --next "..." --open "..."
                                生成跨模型交接摘要 references/_handoff_latest.md（M-R112③）
  habit_brief.py check          体检：摘要是否存在、关键铁律是否覆盖、是否过期

设计约束：
  · 动态段（持久指令、规则版本、生成时间）由脚本从**源数据**读取，禁止手写漂移。
  · 静态段（交互约定/工作偏好/模型联动）保留在模板里，人工维护。
  · 输出语言：简体中文（M-R110，本脚本注释与输出亦遵守）。
"""
import os
import json
import time
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
CONDUCTOR = os.path.dirname(HERE)                     # .../ecosys-conductor
REFS = os.path.join(CONDUCTOR, 'references')
BRIEF = os.path.join(REFS, 'user_habit_brief.md')
HANDOFF = os.path.join(REFS, '_handoff_latest.md')
STANDING = os.path.join(HERE, 'user_standing_instructions.json')
RULES = os.path.join(HERE, 'rules_registry.json')

# ── 静态段：人工维护的偏好模板（与动态数据合并输出）────────────────
# ★ S183（2026-10-20 17:05）：第 0 节放在语言铁律之前。
#   理由：语言铁律管「用什么语言说」，第 0 节管「交付物长什么样」。
#   两者都是"不依赖具体任务"的全局约束，但交付物格式一旦写错，
#   用户是要拿去复用的（本轮原话：「我要用于其他技能的改进」），返工成本高于一次语言回退。
#   用户明确要求跨场景跨对话长期有效——所以它必须在**每份**开场摘要的最前面，
#   而不是藏在某个技能的 references 里等着被翻到。
STATIC_DELIVERY = """## 0. 交付报告八段闭环（M-R128 · 用户 2026-10-20 明确要求跨场景跨对话长期有效）

> 用户原话：「从原因+过程+过程坑+解决方案+后续带出的问题+后续问题解决过程+完工经验总结，
> 形成这样一个闭环，给我一份报告，**以后的交付报告都按这个来**」
> 「这个规则是**跨场景，跨对话**的，就是在我使用这个app期间都给我按照这个走」

**适用范围**：任何交付报告／复盘／验收报告／治理回溯／事故复盘，不限于某个技能或某类任务。

| 段 | 内容 | 判红条件 |
|---|---|---|
| ① 原因 | 为什么会出这个问题（机制层，不是"我不小心"） | 只写现象不写机制 |
| ② 过程 | 按时间线/因果链讲清怎么一步步走到今天 | 只有结论没有路径 |
| ③ 过程坑 | **真实踩过的坑，含我自己造成的** | 只写"一切顺利" |
| ④ 解决方案 | 实际改了什么（文件+行+命令+验证结果） | 只有方案没有落地凭据 |
| ⑤ 后续带出的问题 | 修完冒出来的新问题 | 确实没有也要明写「无」，**不许省略该节** |
| ⑥ 后续问题解决过程 | ⑤ 里每个问题怎么处置的 | 跳过或含糊带过 |
| ⑦ 完工经验总结 | 可搬走的判断句（不是感想、不是"我以后会注意"） | 写成抒情或表态 |
| ⑧ 过程暴露问题登记 | 过程中暴露的异常**当场入欠账总账**，本段列「暴露点→欠账编号」；确实没有写「无」 | ⑤有遗留而⑧无编号=收口闸打回 |

**六条硬性要求**：
1. ③ 必须含自己造成的坑——报告的价值就在这一段，美化等于销毁证据。
2. ⑤ 确实没有也要写「无」，不许删节——删了读者无法区分"没发生"和"没写"。
3. 未解决的事如实标 `open`，**不许写成已完成**。
4. ⑦ 写成**判断句**（"X 情况下必须 Y，因为 Z"），能直接搬去别的技能。
5. 文件名含 `MMDD` 日期段（规则51），格式 `类别_MMDD_描述.md`。
6. 过程中暴露的异常当场入欠账总账，⑧ 段列编号——⑤有遗留而⑧无编号=闸打回（T22 立段）。

**模板**：`ecosys-conductor/references/交付报告_七段模板.md`
**登记**：`rules_registry.json` → `M-R128`
"""

STATIC_BACKGROUND = """## 2. 后台执行门禁（M-R120 · 优先级仅次语言铁律 · 用户 2026-10-19 明确要求）
- **收后台结果必须非阻塞（M-R122）**：禁止 block=true 阻塞等待——「放后台」≠「可对话」，收结果也用非阻塞，完成靠系统通知。

**预估或实测超过 1 分钟的命令，一律后台跑，不许前台阻塞。**

> 用户原话：「超过一分钟，必须后台跑，不然我都找不到人，并且我无法插入对话」。
> S176 实况：我在前台连跑十几分钟的长命令，屏幕反复刷同一批输出，用户发的截图里只看到
> 「咋回事啊」「到哪了」「继续」——他既找不到进度，也插不进话。

| 情形 | 必须怎么做 |
|---|---|
| 预估 >60s（`verify` / `signal-check` / 全量扫描 / 批量入库） | 直接 `run_in_background`，别试前台 |
| 已经在跑且超时 | 立刻转后台，不要原地等 |
| 启动后台后 | **马上回报**三句话：已在后台跑什么 / 预计多久 / 我接着做什么 |
| 后台输出 | **必须重定向落盘**（`> /tmp/xxx.txt 2>&1`）——句柄会被清掉 |
| 等待方式 | 靠完成通知，**禁止 sleep 轮询** |
| 长任务期间 | 保持可对话：用户随时能插话、改需求、换方向 |

**为什么必须落盘**：S176 实测两次 `No task found with ID`，后台句柄被清，结果全丢只能重跑——白等一轮。

**判红口径**：单次前台命令耗时 >60s 即违规；已落盘的命令不计。
"""

STATIC_INTERACTION = """## 3. 交互约定

| 规则 | 内容 |
|---|---|
| **M-R109 短代号** | 用户说 `1`/`2`/`a`/`b`/`1.2`/`a.b` 时，**直接指代 agent 最近一次回复中列出的选项**，立即执行、不澄清。例外：代号歧义、或该选项是不可逆/破坏性操作（删库/覆盖/发布）→ 先确认。 |
| **"都做"** | = 把上一轮回复末尾提出的**所有**待办/建议全部执行。 |
| **"继续"** | = 接着上次中断处往下做，不重述、不确认。 |
| **"按顺序来"** | = 按上一轮列出的 1→2→3 顺序依次执行，不停顿问。 |
| **"从根源解决"** | = 不接受打补丁/权宜之计，要改机制、改 SSOT、堵住复发路径。 |
"""

STATIC_WORK = """## 4. 工作偏好

| 维度 | 用户偏好 |
|---|---|
| **执行风格** | 少问多做；能自己判断的直接做完再汇报。选项只给真正需要拍板的（2–4 个，带推荐）。 |
| **汇报风格** | 先给结论，再给证据（数据/文件路径/实测输出）。禁止只报"已完成"而无凭据。 |
| **真实性** | 状态不许美化：失败就说失败，没做就说没做，不确定就标不确定。清单条数 ≠ 实际条数时必须分开报。 |
| **破坏性操作** | 删/覆盖/发布前必须：出清单 → 备份 → 显式等用户确认（"删"）→ 才执行。 |
| **文档落点** | SSOT 在技能目录（SKILL.md / CHANGELOG / flow.json）；交付物在 `/workspace`；临时文件在 artifact 目录。 |
| **产物区交付（M-R143）** | 成品进产物区 `/workspace/产出区_1016`；含 json 须 json 置顶发（用户先拿 json 上机实测）。 |
| **新规则闭环三关（M-R144）** | 立新全局规则同轮走完三关：①入册②注入（habit_brief 含规则号）③探针；缺一=死规则（无落点黄牌）。 |
| **可并行默认并行不问（M-R145）** | 独立可并行任务默认同消息并发发出，不用问用户；仅抢资源/需排优先级才问。长命令按 M-R120 后台。 |
| **完成态凭落点（M-R148）** | 标「完成/done/已固化/已吸收」必须有可核实落点（代码命中/真实文件/规则号在册）；「已借鉴/已采用」只是声明，不算证据。 |
| **欠账全程闭环（M-R147）** | 立账必带 next+主+期限；每轮体检自动扫超期/无主；关闭必凭证据；报告引用编号必须真实在账——只登记不算闭环（T23 用户定性）。 |
| **回合收敛（M-R146）** | 单回合完成一个独立阶段即落回合回报（做了什么/在跑什么/下一步），不许攒十几条调用不落回合——用户消息只能插在回合之间，回合不落=用户切不进（T20 实证）。与 M-R120 互补：120 管命令时长，146 管回合总长。 |
| **后台长任务三带（M-R138）** | 后台跑长任务必须三带：单步有超时、逐条报进度、完成即报。与 M-R120（>1min 必后台）配套：后台≠失联。 |
| **治理三段闭环（M-R139）** | 治理动作三段：动前留回滚通道 → 验证通过 → 删掉通道才算闭环。删备份是最后一步，不是可选步骤。 |
| **收口必进云端（S198 断根）** | threads done 收口自动登记云端事件库——「做过什么」须跨会话可查（本地写完≠云端可查）。 |
| **行为门禁三件套（盘点补齐 2026-10-22）** | M-R129 长任务持续出声｜M-R137 正本入口优先｜M-R140 任务线看板。三条钩子均活体。 |
| **规则总册变更须授权（M-R214）** | 改规则总册须用户当轮显式授权：意图闸写短期令牌(用后即焚)，registry_guard 无令牌即 exit 2 硬拦；守 M-R136 不造假。 |
| **探源司监测分诊细化（M-R203）** | 探源司监测作者仓库须按三态（活/停滞/死）分诊，借鉴 learn-hub M-L03。owner=probe-hub，SCAN 提醒面。 |
| **定时任务先预览后确认（M-R204）** | 定时任务写操作须先预览、经用户确认后执行，严禁未确认直调。owner=automation-task-manager。 |
| **定时任务严禁秒级（M-R205）** | 定时任务严禁秒级、不许静默降级分钟级（cron 秒位固定0、最小1分钟）。owner=automation-task-manager，GATE。 |
| **闭环交付须附可运行验证（M-R207）** | 标榜「闭环/已治理」的交付须附可运行验证脚本+实测日志，只靠「我写了」自证不算闭环。owner=ecosys-conductor。 |
| **令牌单一事实源（M-R208）** | GitHub 令牌以 /root/.codebuddy/.gh_token 为唯一事实源：探源司缺失时自动回退全局、换机恢复自动落位、用户更新只需覆盖该文件一处；不得另立私源、不得靠符号链接。S209 立册。 |
| **重构引用现态化（M-R153）** | 重构/改名/移动/备份/新增后须核验零断链、禁引用 .bak/旧路径、注释随码同步；常态 code_stale_ref 核验、动作 biz_gate 拦、rule_liveness 盯落点（缺=死规则）。owner=ecosys-conductor，GATE。 |
| **技能脚本可编译性（M-R210）** | 技能 scripts/*.py 须可编译；动作 biz_gate check_compile 拦、常态 compile_all 核验、rule_liveness 盯落点（缺=死规则）。owner=ecosys-conductor，GATE。 |
| **审计客观性纪律（M-R211）** | 全量审计/体检先跑并采信内置扫描脚本客观输出，禁凭标签/status 主观代判；诚实降级按声明级单列，信号源「捕获=否」先分检测器坏还是真问题。owner=ecosys-conductor，PROBE。 |
| **进度沉淀 M-R216** | 进度写 `current_task.md`（进度/下一步），钩子每轮拼「当前进行中任务」；删/换会话不丢。 |
| **模型选择** | 日常执行/批量操作 → 快速模型（用户评价"很好用"）；复杂推理/深度审查 → 重型模型。切到重型模型时**不要变啰嗦**，保持快速模型的直接风格。 |
"""

STATIC_MODEL = """## 5. 模型联动约定（M-R112 · 原 M-R112 已退役，由 M-R120 开工门禁承接）

> 退役交代：原 M-R112「习惯随身」已并入开工门禁体系，本条保留为「模型联动约定」说明，避免检查器替退役规则背书。

1. **习惯随身**：本文件即"习惯随身"载体——换模型后先读它，而不是从零理解用户。
2. **风格守恒**：切到任何模型，**输出密度、直接程度、中文要求**都不许降级。重型模型尤其要避免"繁复化"（用户明确反馈 Hy3 切换后过于繁复）。
3. **接力摘要**：切换模型时，上一模型须留下：当前任务、进度到哪、下一步、未决问题（命令见下）。
4. **新模型接入**：任何新模型第一次服务本用户，**先读本文件再动手**。
5. **业务上下文随身（M-R206·已升级为源头钩子）**：换模型跑偏的根因已根治——全局钩子 `memory_inject_guard` 在**每条用户消息前**自动把业务背景注入上下文（源 `memory_core.md`，所有模型共享、动态更新），**不再依赖读本文件**。若仍不确定，跑 `python3 /root/.codebuddy/memory_restore.py` 全量恢复，**不要只拿压缩摘要硬猜**。
6. **交接摘要须对版**：`references/_handoff_latest.md` 须反映当前主线；若它写的是过期任务（如已被替换的监测盲区），以「第 5.5 节 + 当前指令」为准，并提醒用户更新 handoff。

```bash
# 切换模型前：留下交接摘要
python3 habit_brief.py handoff --task "当前任务" --state "做到哪了" \\
        --next "下一步" --open "未决问题"
# 切换模型后：先读习惯摘要 + 交接摘要
python3 habit_brief.py show && cat references/_handoff_latest.md
```
"""

STATIC_CONTEXT = """## 5.5 用户核心上下文快照（已迁至全局钩子·本节约简）

> ⚠️ 业务背景单一事实源已迁至 **`/root/.codebuddy/memory_core.md`**，
> 由全局钩子 `memory_inject_guard`（UserPromptSubmit）**每轮自动注入**所有模型上下文，
> 不再写死在本文件（避免过期双源）。完整业务画像以 memory_core.md 为准。

**总目标（一句话）**：用户在做「自己能增删线路的 TVBox/OK影视 json」（合平735 为底），
辅以探源司「直送」模式（找公开分享→验活→发链接）。csp=爬虫源线路，6V=磁力站，直送=发链接不碰网盘。

**全量兜底**：换模型/跑偏时，钩子自动跑 `python3 /root/.codebuddy/memory_restore.py`
从原始会话记录恢复 100% 真实输入（实测 2284 条）落 `/tmp/user_msgs_all.txt`。
"""

STATIC_COORD = """## 6. 生态坐标（常用，省得每次找）

| 对象 | ID / 路径 |
|---|---|
| 个人空间 | `8w5cnqCqQJ0wNoF2Hb6efH` |
| 探源司·总库（空间超根） | `gZ4xP6kXXeCpTlsH2CN9tJ` |
| 底包·底包总库 | `GCLJWbFGS1BmYbj6JCX9it` |
| 🗑 待删收件箱 | `tBlgSL4SPEFMrsyTBTg03c` |
| 规则总册 | `ecosys-conductor/scripts/rules_registry.json` |
| 持久指令档 | `ecosys-conductor/scripts/user_standing_instructions.json` |
| 招式库 / 进化日志 | `skill-evolution/scripts/evolve.py`、`evolution_log.jsonl` |
"""


def _load(p, default=None):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default


def build():
    """组装完整摘要文本：动态段由源数据生成，静态段取自模板。"""
    stand = _load(STANDING, {'instructions': []})
    insts = stand.get('instructions', [])
    reg = _load(RULES, {'rules': []})
    rules = reg.get('rules', [])
    r110 = next((r for r in rules if r.get('id') == 'M-R110'), None)
    n_rules = len(rules)

    # 动态：语言铁律段落（要求次数取自持久指令档，真实反映复发）
    zh = next((i for i in insts if i.get('id') == 'pref_zh_chat'), None)
    cnt = zh.get('requested_count', 0) if zh else 0
    # ★ P081：读取 lang_ledger 账本，注入「实时复发态势」——闭环「推」的开场可见位。
    #   与 lang_ledger._last7 同源口径（按日计数 daily 汇总近7日），账本缺字段安全回退。
    _led = _load('/root/.codebuddy/hooks/.lang_violations.json', {'total': 0, 'daily': {}})
    lang_total = _led.get('total', 0)
    _now = time.time()
    lang_last7 = 0
    for _k, _v in _led.get('daily', {}).items():
        try:
            _dt = time.mktime(time.strptime(_k, '%Y-%m-%d'))
        except Exception:
            continue
        if 0 <= (_now - _dt) <= 7 * 86400:
            lang_last7 += _v
    lang = f"""## 1. 语言铁律（M-R110 · 优先级最高 · 用户已要求 {cnt} 次）

**全程简体中文。无例外。**（覆盖：正文回复、深度思考/思考面板、工具调用说明、报错、提问——无一例外）

**触发重置的场景**（都要重新对齐）：切换模型、新开会话、打断后恢复、上下文压缩后。

> ⚠️ 历史事实：该要求已登记 {cnt} 次仍未根治，用户 2026-10-18 再次反馈。**看到本文件即视为已对齐，不得再要用户提醒。**

**实时复发态势**（lang_ledger 账本·每会话收尾记账）：累计 {lang_total} 次 ｜ 近7日 {lang_last7} 次
"""

    # 动态：持久指令清单（完整列出，供核对）
    lines = ['## 7. 持久用户指令清单（M-R108 · 源自 user_standing_instructions.json）', '']
    if insts:
        lines.append('| 编号 | 指令 | 要求次数 | 状态 |')
        lines.append('|---|---|---|---|')
        for it in insts:
            st = '✅ 已固化' if str(it.get('status', '')).startswith('solidified') else '⏳ 待固化'
            ins = (it.get('instruction') or '').replace('\n', ' ')
            if len(ins) > 90:
                ins = ins[:90] + '…'
            lines.append(f"| `{it.get('id')}` | {ins} | {it.get('requested_count', 0)} | {st} |")
    else:
        lines.append('（暂无登记）')
    lines.append('')
    persist = '\n'.join(lines)

    today = time.strftime('%Y-%m-%d %H:%M')
    head = f"""# 用户习惯摘要（MODEL-AGNOSTIC · 任何模型开场必读）

> **用途**：本文件是**模型无关**的用户习惯快照。任何模型（快速 / Hy3 / 深度思考 / 未来新模型）、
> 任何会话（新开 / 切换 / 恢复 / 压缩后）在开始工作前**先读本文件**，即可对齐用户习惯，
> 无需用户重新教一遍。
>
> **维护**：`ecosys-conductor`（生成与刷新）｜来源：规则册 M-R109/M-R110/M-R112（共 {n_rules} 条）
> + 持久指令档 `scripts/user_standing_instructions.json`
> ｜**刷新命令**：`python3 scripts/habit_brief.py`
>
> 版本：v1.0.0 ｜ 生成：{today} ｜ **本文件由脚本生成，勿手改动态段**

---
"""

    tail = f"""
---

**一句话**：中文、直接、少问、有据、守习惯——换什么模型都这样。

<!-- 生成器：habit_brief.py ｜ 规则数：{n_rules} ｜ 持久指令：{len(insts)} 条 ｜ 时间：{today} -->
"""
    return '\n'.join([head, STATIC_DELIVERY, lang, STATIC_BACKGROUND, STATIC_INTERACTION,
                      STATIC_WORK, STATIC_MODEL, STATIC_CONTEXT, STATIC_COORD, persist, tail])


def cmd_gen(a=None):
    os.makedirs(REFS, exist_ok=True)
    txt = build()
    old = open(BRIEF, encoding='utf-8').read() if os.path.exists(BRIEF) else ''
    open(BRIEF, 'w', encoding='utf-8').write(txt)
    changed = '已更新' if old != txt else '无变化'
    print(f'✅ 用户习惯摘要{changed}：{BRIEF}')
    print(f'   行数 {txt.count(chr(10))} ｜ 字符 {len(txt)}')
    return 0


def cmd_show(a=None):
    if not os.path.exists(BRIEF):
        print('⚠️ 摘要不存在，先跑：python3 habit_brief.py')
        return 1
    print(open(BRIEF, encoding='utf-8').read())
    return 0


def cmd_handoff(a):
    """生成跨模型交接摘要（M-R112③）：切换模型前后的状态传递。"""
    os.makedirs(REFS, exist_ok=True)
    now = time.strftime('%Y-%m-%d %H:%M')
    txt = f"""# 跨模型交接摘要（切换模型时先读我）

> 生成时间：{now} ｜ 用途：让接手的新模型**不从零开始**，避免"繁复地重来一遍"

## 1. 语言要求（最高优先）
全程**简体中文**（正文 + 思考 + 播报 + 报错）。切换模型后仍须中文，不得回退英文。—— 详见 `user_habit_brief.md`

## 2. 当前任务
{a.task or '（未填写）'}

## 3. 进度状态
{a.state or '（未填写）'}

## 4. 下一步
{a.next or '（未填写）'}

## 5. 未决问题 / 需用户拍板
{a.open or '（无）'}

## 6. 交接约定
- 接手后：先读本文件 + `user_habit_brief.md`，再动手；不要重述已完成的步骤。
- 风格：保持直接、简洁、给证据；**不要因换了模型而变啰嗦**。
- 若发现与上文冲突，以用户当前指令优先。
"""
    open(HANDOFF, 'w', encoding='utf-8').write(txt)
    print(f'✅ 交接摘要已生成：{HANDOFF}')
    return 0


def cmd_check(a=None):
    ok = True
    if not os.path.exists(BRIEF):
        print('❌ 摘要文件不存在'); return 1
    t = open(BRIEF, encoding='utf-8').read()
    checks = [
        # ★ S183：第 0 节必须在这里，否则「摘要里少了七段闭环」永远体检不出来——
        #   体检器只验它认识的项，正是本夜反复踩的「检查器自己不在检查清单里」。
        ('交付报告八段闭环 M-R128', 'M-R128' in t and '过程坑' in t and '后续带出的问题' in t
         and '完工经验总结' in t and '过程暴露问题登记' in t),
        ('语言铁律 M-R110', 'M-R110' in t and '简体中文' in t),
        ('后台执行门禁 M-R120', 'M-R120' in t and '超过 1 分钟' in t and 'run_in_background' in t),
        ('短代号 M-R109', 'M-R109' in t),
        # ★ T27：M-R112 已退役（retracted_by=M-R120），体检项改为核「退役交代在位」而非核它还在——
        #   原写 '模型联动 M-R112', 'M-R112' in t 会在摘要改成「原 M-R112 已退役」后仍报绿，
        #   那是**检查器在替一条退役规则背书**。现改为：摘要里必须出现「原 M-R112」+「退役」才算交代清楚。
        ('模型联动退役交代 M-R112', 'M-R112' in t and '退役' in t),
        # ★ S201 修正（刷新摘要后出现的假红）：
        #   原判据按**旧版**文案写（要求含「关键术语映射」表格），
        #   但 5.5 节早已升级为**节简版**——业务背景单一事实源迁至 memory_core.md
        #   （避免过期双源），术语表随之迁走，「关键术语」字样在新版里本就不存在。
        #   刷新摘要后旧判据失配 → M-R206 报红，属于**判据过期**而非功能缺失。
        #   按 M-R38：源是准的，判据跟随源改；**不许为了消红去回退源**。
        #   新判据验证三要素：快照节在 + 总目标在 + 指向记忆中枢（memory_core）与兜底恢复（memory_restore）。
        ('用户核心上下文快照 M-R206', '核心上下文快照' in t and '总目标' in t
         and 'memory_core' in t and 'memory_restore' in t),
        ('持久指令段', '持久用户指令清单' in t),
        ('深呼吸/思考面板覆盖', '深度思考' in t),
        ('产物区交付 M-R143', 'M-R143' in t and '产出区_1016' in t and 'json 置顶' in t),
        ('进度沉淀跨会话 M-R216', 'M-R216' in t and 'current_task' in t),
        ('新规则闭环三关 M-R144', 'M-R144' in t and '三关' in t and '黄牌' in t),
        ('可并行默认并行 M-R145', 'M-R145' in t and '不用问' in t and '排优先级' in t),
        ('回合收敛 M-R146', 'M-R146' in t and '回合之间' in t),
        ('欠账全程闭环 M-R147', 'M-R147' in t),
        ('完成态凭落点 M-R148', 'M-R148' in t and '声明' in t),
        # ★ 本夜新增（闭环「截断漏注入」顽疾 M-R211）：摘要正文若超过 habit_inject.MAX_CHARS(9000)，
        #   注入钩子会截断、后半段规则（M-R137/M-R207/M-R210 等）注入不到任何模型上下文 → 看不见就不守。
        #   此处体检兜底：超 9000 即报红，逼人去调 MAX_CHARS 或瘦身文件，闭合 M-R144 第③关「丢了会红」。
        ('摘要注入超限 M-R211', len(t) <= 9000),
        ('令牌单一事实源 M-R208', 'M-R208' in t and '单一事实源' in t and '.gh_token' in t),
        # ★ 本会话 M-R214：规则总册变更须用户授权。注入态（摘要含 M-R214 + 授权 + registry_guard）
        #   与执行层（registry_guard 硬拦 + grant 意图闸写令牌）双向一致，闭合 M-R144 第②③关。
        ('规则总册变更须授权 M-R214', 'M-R214' in t and '授权' in t and 'registry_guard' in t),
        ('收口必进云端 S198', '_event_register' in open(
            os.path.join(os.path.dirname(BRIEF), '..', 'scripts', 'threads.py'),
            encoding='utf-8').read()),
    ]
    # T14：死规则扫描常驻——立了无落点的规则当场报红
    import subprocess as _sp
    _lv = _sp.run(['python3', os.path.join(os.path.dirname(BRIEF), '..', 'scripts',
                 'rule_liveness_scan.py')], capture_output=True, text=True, timeout=60)
    print(f'  {"✅" if _lv.returncode == 0 else "❌"} 死规则扫描（rule_liveness_scan）'
          f'{"" if _lv.returncode == 0 else "：" + _lv.stdout.strip()[:120]}')
    ok = ok and _lv.returncode == 0
    print(f'🔍 习惯摘要体检（{BRIEF}）')
    # T23 全程闭环·盯梢关：欠账超期/无主/无 next 每轮必扫（用下方的 _sp2，先导入）
    import subprocess as _sp2
    _lc = os.path.join(HERE, 'ledger_closure.py')
    if os.path.exists(_lc):
        _rl = _sp2.run(['python3', _lc, 'audit'], capture_output=True, text=True, timeout=30)
        _bad = [l for l in _rl.stdout.splitlines() if '🔴' in l]
        print(f'  {"⚠️" if _rl.returncode != 0 else "✅"} 欠账盯梢（全程闭环·盯梢关）：'
              f'{_rl.stdout.strip().splitlines()[-1] if _rl.stdout.strip() else "无法判定"}'
              + (f'（前3条: {"; ".join(b.strip() for b in _bad[:3])}）' if _bad else ''))
    # P044：串行行为探针（警示项，不拉死体检闸——串行段含合法「读后改」）
    _p44 = os.path.join(HERE, 'p044_concurrency_probe.py')
    if os.path.exists(_p44):
        _r44 = _sp2.run(['python3', _p44], capture_output=True, text=True, timeout=120)
        first = (_r44.stdout.strip().splitlines() or [''])[0]
        print(f'  {"⚠️" if _r44.returncode != 0 else "✅"} 串行行为探针（P044）：{first}')
    # ★ T29 / P054：M-R108 可执行判据——「同一要求 ≥3 次须固化」过去只有文档、
    #   没有机器检查，两边（recurring 聚类 / 持久指令档）各自为政。
    #   m108_check.py 把两边比上：requested_count ≥3 且未 solidified → 红。
    #   ⚠️ 判据必须严：v1 曾把「recurring 报 ⏳」直接当缺口，而 ⏳ 含"次数还没到门槛"，
    #   于是每条新建偏好都被报红 —— 那种体检很快就会被无视。
    _m108 = os.path.join(HERE, 'm108_check.py')
    if os.path.exists(_m108):
        _r108 = _sp2.run(['python3', _m108], capture_output=True, text=True, timeout=240)
        _l108 = (_r108.stdout.strip().splitlines() or [''])
        _red = [l.strip() for l in _l108 if '🔴' in l]
        print(f'  {"❌" if _r108.returncode != 0 else "✅"} 持久指令通联 M-R108：'
              f'{_l108[0].lstrip("✅❌⚠️ ") if _l108 else "无法判定"}'
              + (f'（{"; ".join(_red[:2])}）' if _red else ''))
        ok = ok and _r108.returncode == 0
    # ★ T31 / P045：M-R146 回合收敛的可执行判据。
    #   此前 M-R146 的 probe.command 指向 p044_concurrency_probe.py 并声称「回合链长度可见」，
    #   但那个脚本根本不统计回合——是「登记了探针但实现不完整」，与 T28 的 M-R106 虚挂同族。
    #   turn_length_probe.py 补上真实现：按 user 消息切回合，算「≥12 条调用且中途零回报」。
    #   口径用「立规日之后」而非「最近 N 天」——立规前 13 个红档属历史，
    #   算进每轮体检会让这项永远红着（P044 的教训）。
    _tl = os.path.join(HERE, 'turn_length_probe.py')
    if os.path.exists(_tl):
        _rtl = _sp2.run(['python3', _tl], capture_output=True, text=True, timeout=300)
        _ltl = (_rtl.stdout.strip().splitlines() or [''])
        _cnt = next((l.strip() for l in _ltl if '长回合且无落回报' in l), '')
        print(f'  {"❌" if _rtl.returncode != 0 else "✅"} 回合收敛 M-R146：'
              f'{_cnt or "无法判定"}')
        ok = ok and _rtl.returncode == 0
    for name, passed in checks:
        print(f'  {"✅" if passed else "❌"} {name}')
        ok = ok and passed
    # 与源数据一致性：规则数 / 持久指令数是否与末尾注释一致
    reg = _load(RULES, {'rules': []})
    stand = _load(STANDING, {'instructions': []})
    n_rules, n_inst = len(reg.get('rules', [])), len(stand.get('instructions', []))
    print(f'  源数据：规则 {n_rules} 条 ｜ 持久指令 {n_inst} 条')
    stale = f'规则数：{n_rules}' not in t or f'持久指令：{n_inst} 条' not in t
    print(f'  {"⚠️ 摘要已过期（源数据变更后未刷新）" if stale else "✅ 与源数据一致"}')
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog='habit_brief.py',
                                 description='用户习惯摘要生成器（M-R110/M-R112 习惯随身）')
    sub = ap.add_subparsers(dest='cmd')
    sub.add_parser('gen')
    sub.add_parser('show')
    sub.add_parser('check')
    hd = sub.add_parser('handoff')
    hd.add_argument('--task', default='')
    hd.add_argument('--state', default='')
    hd.add_argument('--next', default='')
    hd.add_argument('--open', default='')
    a = ap.parse_args(argv)          # None → 读取真实命令行；[] → 空参（默认 gen）
    cmd = a.cmd or 'gen'
    fn = {'gen': cmd_gen, 'show': cmd_show, 'check': cmd_check, 'handoff': cmd_handoff}.get(cmd)
    if fn is None:
        ap.print_help(); return 1
    return fn(a)


if __name__ == '__main__':
    raise SystemExit(main())
