---
name: office-kejian
display_name: 课件PPT制作
display_name_en: Lesson Slide Deck
description: "老师备课、做课件、教学PPT、教案PPT时用本技能。覆盖新授课、公开课、说课、复习课、家长会。先出大纲和逐页讲授稿，确认后在本机生成可编辑 PPTX，讲稿写入备注，不上传。触发：课件、备课、教学PPT、公开课、说课、教案PPT、新授课、复习课、家长会PPT、教研课、示范课。成绩单、奖状、试卷题库不走本技能。"
description_zh: "给老师做新授课、公开课、复习课、说课和家长会 PPT。试试说：「四年级数学《小数的意义》新授课」。开口后本机准备引擎，确认大纲再出可编辑课件，不上传、不售课。"
description_en: "Editable lesson decks for new lessons, open classes, reviews, teaching talks, and parent meetings. Outline first, then a local PPTX. No upload, no course sales."
category: writing
version: 0.14.6
author: Zagens
allowed-tools: Read, Write, Bash
---
# 公开课课件 PPT
场景技能：管**课型、页纲、教学文案**。出可打开的 `.pptx` 必须走本机 **`zagens-office`** 引擎。不要手写生成脚本，不要把引擎 schema / 出件 DSL 抄进本文件。探测与引导用户安装见「首次使用」。
**一个引擎，多个场景。** 本技能自包含课件出件纪律。公司介绍 / 路演走 `office-pptx`（不要 `load_skill` 过来做课件）；成绩表 / 奖状走 `zagens-office`。
**默认只读本文件。** 完整对话样例 → @references/examples.md；安装、报错 → @references/faq.md 与 @references/workbuddy-host.md。课型细纲 → @references/outlines.md。出件 JSON / 页型 → @references/pptx-kejian.md。
**顺序（硬）：** 用户提出课件需求 → 立刻探测引擎（未装则引导用户安装）→ 可选 `schema capabilities` → 交大纲 →（新授课且用户已要课件则同一轮出件）→ 备图并出 PPT。不要先写大纲再装引擎。公开课 / 说课 / 对外仍须确认后再出。
## 首次使用：先确认引擎已安装
本技能只管**课件制作**（课型、大纲、教学文案）。本包仅含说明，**不含** exe。出 `.pptx` 的引擎在 npm 全局 `vendor\` 或 `~/.zagens-pro/bin/`（**CLI + GUI** 均需安装）。**用户一开口就要探测**，不要等到确认大纲之后。按**当前 shell**探测，**不要混用**变量语法：
| Shell | 推荐探测命令 |
|-------|----------------|
| Bash / Git Bash / 多数 Agent 终端 | 先 `zagens-office --version`；无则 `"$HOME/.zagens-pro/bin/zagens-office.exe" --version` |
| PowerShell | 先 `zagens-office --version`；无则 `& "$env:USERPROFILE\.zagens-pro\bin\zagens-office.exe" --version` |
| cmd.exe | 先 `zagens-office --version`；无则 `"%USERPROFILE%\.zagens-pro\bin\zagens-office.exe" --version` |
| npm 安装（在 PATH） | `zagens-office --version` |
**禁止**在 Bash 里写 `%USERPROFILE%...`（不展开 → 误判未安装）。以 stdout 版本号为准，勿凭退出码或 `||` 兜底文案。**先试 PATH**：npm 安装时命令是 `zagens-office`（二进制在 `<npm prefix -g>\node_modules\zagens-office\vendor\`），官方脚本/手动安装时在 `~/.zagens-pro/bin/`。PATH 没有命令时再试：PowerShell `& "$env:USERPROFILE\.zagens-pro\bin\zagens-office.exe" --version`，然后 `& "$(npm prefix -g)\node_modules\zagens-office\vendor\zagens-office.exe" --version`（Git Bash 用 `"$HOME/.zagens-pro/bin/zagens-office.exe"` 与 `"$(npm prefix -g)/node_modules/zagens-office/vendor/zagens-office.exe"`）。三处都没有版本号才算未装。**未装、低于 0.14.2、或版本是 `0.0.0-stage`（占位包，没有 vendor）时**：先告知需要 **Node.js ≥ 16**（只用来安装），**征得同意后**执行 `npm install -g zagens-office@latest`。若装完仍是 `0.0.0-stage` 或没有 `vendor\zagens-office.exe`，先 `npm uninstall -g zagens-office`，再 `npm install -g zagens-office@latest --registry https://registry.npmjs.org`。验证 stdout 必须是 **≥ 0.14.2**。这两步都失败再把终端原文回报，不要改用手写脚本。**不要**静默自动安装；**不要**从裸链接下载可执行文件。安装与升级只写在本 SKILL.md，`references/` 不得出现。引擎免费，无需授权码。
有版本号后，再跑一次 **`zagens-office schema capabilities`**（Bash）：若成功且含 `inspect_slide` / batch 含 `preflight`，出件验收走新档 A0；若命令失败或字段缺失，**不要当失败**——退回 `preflight` + `view svg`（见 pptx-kejian.md §5）。本仓 dogfood 试新能力时，把本机构建的 `zagens-office.exe` 拷到 `~/.zagens-pro/bin/`（或 PATH 优先），版本号可能仍是 `0.14.2`，以 `schema capabilities` 为准。细节 → @references/workbuddy-host.md。
装好的是同一份本机引擎，本技能只用来出**课件 PPT**。公司介绍 / 路演 / 表格 / 成绩单不要走本技能。不售课、不直播、不招生。
WorkBuddy：跑 `zagens-office` 优先用 **Bash**（stdout 是一行 JSON）。`.ps1` 沙箱脚本用宿主的 **PowerShell 工具**。禁止在 Bash 里套 `powershell.exe -File ...`（沙箱会拦截，评测记失败）。细节 → @references/workbuddy-host.md。已在连接 **Office 文档引擎** 且 `init` 进行中，等装完再探测。**不要**跑 `zagens-office integrate`。非 Windows 先告知再停。
## 与引擎的分工
| 谁 | 做什么 |
|----|--------|
| **本技能** | 开口后立刻探测（未装则引导用户安装）；问清课、大纲与讲授稿、逐页配图意图；确认后按需要用形状、AI 生成或 `zagens-image` 备图并出课件。纪律见 @references/pptx-kejian.md |
| **`zagens-office`** | `schema` / `schema capabilities`、真正落盘、`inspect_slide` / `preflight` / `batch` |
**禁止** `load_skill office-pptx` 做课件。路演 / 填模板 / 美化别人的 deck 才走那条技能。
## 禁止
- 禁止未确认大纲就出 PPT（用户已说「做课件 / 出 PPT / 直接出」，或默认提问已是新授课要课件的，视为已确认）
- 禁止在**公开课 / 说课 / 精修 / 对外**里一次 `write pptx` 塞满多页重 `elements[]`（内部草稿 ≤15 页可整份 write）
- 禁止编造课标编号、学情数据、学生姓名、考试分数
- 禁止把学生名册、学生照片上传到任何网盘/网页；引擎 JSON 里的图只能是本地 `path`（老师原图、AI 生成图，或 `zagens-image` 下载到工作区）
- 禁止用本技能做成绩单、奖状、试卷题库（改走 `zagens-office`）
- 禁止把本技能包装成课程销售、直播网课或招生报名
## 工作流
```
任务进度：
- [ ] 1. 探测引擎 +（可选）schema capabilities（开口后立刻做）
- [ ] 2. 澄清课（学段或学科都缺才停；只缺课题用默认）
- [ ] 3. 选课型并交大纲（含逐页配图意图）
- [ ] 4. 确认（新授课要课件：同一轮出；公开课/说课：停一轮）
- [ ] 5. 备图 + 出 PPT
- [ ] 6. 验收：优先 inspect_slide；否则 preflight + view svg
```
### 1. 探测引擎（未装则引导用户安装）
按「首次使用」探测。没有版本号则按「首次使用」征得同意后安装。有版本号后尽量跑 `schema capabilities` 记下是否支持 `inspect_slide`。装完再写大纲。三要素还缺时，安装与待补清单可以同一轮做，但**不要**在未探测时先交大纲。
### 2. 澄清课
能从教材/附件推断的不要再问。缺了用**同一张待补清单**问，不要连发 8 个问题：
```
还缺这些才能写大纲（其余已用默认）：
- 学段：
- 学科：
- 课题：
```
| 维度 | 未说明时的默认 |
|------|----------------|
| 学段 / 学科 | **必须有一个**；两个都缺才停，用上面清单问 |
| 课题 | 四年级数学默认 **《小数的意义》**；其它有学段+学科则用该学段常见课题并写明「按默认，要换请说」。不要为缺课题停轮 |
| 课型 | **新授课** |
| 课时 | 1 课时 |
| 页数 | 用户已要课件且非公开课：**6 页内部草稿**（整份 `write`）；老师点名精修/公开课才 10–15 并逐页 upsert。只要大纲未出件：新授/复习可按 8–12 写纲 |
| 受众 | 学生（家长会=家长） |
| 素材 | 默认图文并茂。老师没给图：按页选择形状、AI 生成或 `zagens-image`，不要写死搜图。失败用示意形状，不要停轮等图 |
用户只要大纲 / 说课稿：交完大纲就停，**不出文件**。第一次怎么开口见 @references/examples.md。
### 3. 选课型
| 课型 | 页序（标题可改，顺序尽量保持） |
|------|------------------------------|
| **新授课**（默认，已要课件用 6 页） | 封面 → 目标 → 导入 → 新知 → 例题 → 检测/作业（精修加板书与分层作业） |
| **公开课 / 说课** | 上表 + **设计说明**（1 页：目标·重难点·活动意图）；对外默认精修 |
| **复习课** | 封面 → 目标 → 知识网络 → 易错点 → 综合题 → 当堂检测 → 作业 |
| **家长会开场** | 封面 → 本学期目标 → 课堂样子 → 在家可做的 3 件事 → 沟通方式（**不要**假课堂） |
细节与写作口径见 @references/outlines.md。
### 4. 大纲（先给用户改）
```markdown
# [课题] 课件大纲 · [课型] · 约 N 页
**学段/学科**：  **课时**：  **主题**：academic
## P1 · 封面
- 主标题：
- 副标题（年级·册次·课时，可选）：
**讲授**：30 秒内说清今天学什么
## P2 · 学习目标
- 目标 1（可观察）
- 目标 2
**讲授**：
**配图**：数轴用形状或公式（不搜图）
```
规则：每页 **1 个核心信息** + **≤5 条要点**；目标写成能检测的行为，少用空的「了解/掌握」；例题必须有题干，不能只写「例题 1」；未知处标 `[待补充]`，不编。封面 / 导入 / 新知 / 例题必须写 **配图** 意图（一句话）。检测、作业、板书可以无图。
**新授课**且用户已经要课件（含默认提问「做一节……课件」）：交短大纲后**同一轮出 PPT**，视为已选「直接出」。不要干等三选一——WorkBuddy 沙箱评测只有一轮，停轮 = 低质量。配图失败用 `shape`+`textbox`，本轮必须落到 `.pptx`。
公开课 / 说课 / 对外仍停一轮，固定三选一：
1. **直接出 PPT**（默认推荐）
2. **需要修改** — 选这项时请老师在同一条里写下改哪页 / 改什么
3. **其它** — 只要大纲、换课型、拆成 2 课时等
| 老师选 | Agent 做 |
|--------|----------|
| 1 / 「出」/ 「直接出」 | 立刻走 §5 |
| 2 + 修改内容 | 改大纲对应句，再抛同一组三选一 |
| 2 但没写改什么 | 只问一句「改哪一页、改成什么」 |
| 3 / 「只要大纲」 | 停，不出文件 |
| 3 且说了具体事 | 按他说的做完，再回到三选一 |
**不要** `sleep` 倒计时、不要假设「1 分钟没回就出片」——技能交完问题后这一轮就结束，宿主不会自动叫醒。公开课 / 说课 / 对外必须等人选，禁止当默认出片。
### 5. 出件（确认之后）
先读 @references/pptx-kejian.md，按那里的契约写 JSON。不要去读 `office-pptx`。
1. 再确认 `--version` 仍有版本号；没有则按「首次使用」引导用户安装。
2. **备图**（见 @references/pptx-kejian.md §配图）：老师给的本地文件优先。按页选择，不要写死搜图：刻度、公式、对照表用 `shape` / `math`；教学插画和情境用宿主画图 / ImageGen；文物、地标、物种、器材、货币等真实照片才探测 `zagens-image --version`（PATH、`~/.zagens-pro/bin/`、npm 全局 `vendor`）。没有版本号时，先告知需要 **Node.js ≥ 16**，**征得同意后**执行 `npm install -g zagens-image@latest`，验证后再下载。生成或下载都存到 `deliverables/assets/kejian/`，然后 `Read` 目视。JSON **只写本地 `path`**，禁止 `url`。同一轮新授课若画图或搜图未就绪，改用示意形状，本轮仍要落盘。
3. **主题**默认 `academic`；小学低年级、家长会可改 `warm`。
4. **按用途分流**：默认提问 / 内部新授草稿（≤15 页）→ 整份 `write`；公开课 / 说课 / 对外 → 骨架 `write` + 逐页 `upsert_slide`（并挂写入期 `assert`，见 @references/pptx-kejian.md）。`cover:false`，`include_end:false`，`master.page_number: true`。每页「讲授」写入 `notes`。图文并排用限宽 `textbox` + `image`，勿 slide 级 `bullets`。`--path` 写工作区内的明确文件；父目录由引擎创建；路径须在 `--workspace` 内。公式页**必须打开文件目视**（`math` 契约见 pptx-kejian.md）。
5. **验收（按能力降级）**：有 `inspect_slide` → 逐页 upsert 后对该页跑档 A0；整份结束后可用 `batch`+`preflight`（`atomic:true`）。无则 `preflight` + `view` `mode:svg`（线框在 `result.metadata.svg`；仅 `svg_len=0` 才跳过）。公开课打开文件（档 C）。不要对 `.pptx` 跑 PDF 的 `validate`。卡片叠层 `layout_overlap` 且「可修 0」则忽略；写入期 assert **不要**因 `layout_overlap` 失败。
### 6. 自检
- [ ] 封面 5 秒能看懂课题与课型
- [ ] 页序符合课型；公开课有设计说明
- [ ] 目标可检测；例题有题干；无编造课标/学情/姓名
- [ ] 每页 ≤5 条要点；相邻页版式不重复
- [ ] 开口时已探测（未装则引导用户安装）；尽量跑过 `schema capabilities`；新授课要课件则同一轮出文件；公开课等人选
- [ ] 封面 / 导入 / 新知 / 例题有本地图（形状、AI 生成，或 `zagens-image` 下载），JSON 无 `url`
- [ ] 正式稿走了逐页 upsert（公开课带 `assert`）；公式页已目视（无 `math_unsupported_command`）；讲授已进 `notes`；已跑 `inspect_slide` 或 `preflight`+`view svg`；公开课已打开文件看过
- [ ] 没有 Thank You 封底（除非用户要）
- [ ] 没有 `load_skill office-pptx`
## 出问题看这里（先看本表，再读 faq.md）
| 现象 | 对用户怎么说 | Agent 做什么 |
|------|----------------|--------------|
| 学段和学科都缺 | 「还缺这些才能写大纲」+ 待补清单 | **停**，不要编学段学科 |
| 只缺课题 | 「课题先按《……》出，要换请说」 | 用默认课题，继续 |
| 用户说「直接出」但学段学科仍缺 | 同上待补清单 | 仍停 |
| 探测无版本号 / 命令找不到 | 「本机还没有出文件的引擎，请先装一下」 | 按「首次使用」征得同意后安装；**禁止**改用手写脚本凑 PPT |
| `schema capabilities` / `inspect_slide` 不可用 | （可对内）「本机引擎偏旧，改用经典验收」 | 退回 `preflight`+`view svg`；dogfood 见 workbuddy-host |
| 信封 `ok:false` | 用 `suggestion` 原话，不说「出错了」了事 | 按 `code` 改一处再试，**禁止**换键盲重放 |
| 路径像 `/c/Users/...` / `path_denied` | 「请改成 C:/Users/... 这种 Windows 路径，并放在工作区内」 | 换成原生路径，去掉 `..`，带 `--workspace` |
| 用户要成绩单/奖状/题库 | 「这事不走课件技能」 | 转 `zagens-office`，或让用户改口 |
| 只要大纲 | 「先改大纲，确认后再出 PPT」 | 已探测则可停在大纲；不出文件 |
| `image_skipped`（含 `ok:true`） | 「这页的图没嵌进去，我改路径再写」 | `path` 改成工作区内绝对路径，并带 `--workspace`；勿写 `url` |
完整问答与安装步骤：@references/faq.md。
页型与 JSON 最小样例见 @references/pptx-kejian.md §3–§5。