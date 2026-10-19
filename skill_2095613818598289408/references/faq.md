# 常见问题（安装 · 报错 · 边界）

问题集中写在这里，不要到引擎技能里翻。出件命令以 `zagens-office` CLI + `schema` 为准；页 JSON 契约见主技能 `pptx-kejian.md`。不要 `load_skill office-pptx` 做课件。

## 为什么还要另装一个 Office 工具？

本技能**有意不内嵌** Word/PPT 软件，也不在 zip 里带 exe。

- 课件规则（课型、大纲、不编学生数据）在本技能。
- 真正写出可打开的 `.pptx` 在本机引擎 **zagens-office**（同一引擎还服务公司介绍、表格等场景）。
- 文件只落在老师电脑上，不上传名册和课件内容。

用户只要大纲：**可以写大纲、改目标**，不出 PPT。用户要出 PPT：按主技能「首次使用」**agent 自动**装引擎，**勿**只发链接。不要改用手写脚本或在线套模板凑一份。

## 安装

安装见本技能根 `SKILL.md`「首次使用」（本文件不复述命令）。本技能只为**课件出件**而装。

1. 老师确认装好后，按**当前终端**重新探测（命令见主技能「首次使用」），不要把 `%USERPROFILE%` 写进 Git Bash。PATH 没有命令时再试 `~/.zagens-pro/bin/` 和 npm 全局 `vendor` 里的 exe。
2. stdout 里要有版本号（如 `zagens-office 0.14.2`），且**要求 ≥ 0.14.2**。`0.0.0-stage` 是占位包，按没有引擎处理。低于或占位则按根技能「首次使用」升级后再继续。没有版本号就更不能开始 `write`。
3. 装好后可再跑 `zagens-office schema capabilities`：有 `inspect_slide` / batch 含 `preflight` 时按主技能用新验收；命令失败或字段缺失则退回 `preflight` + `view svg`（npm 已发布 0.14.2 可能尚无这些字段）。

引擎免费，无需授权码。`read` / `schema` 一直可用。

## 信息不全时

学段和学科都缺，才用主技能里的**待补清单**一次问完。  
只缺课题：四年级数学默认《小数的意义》，其它用该学段常见课题并写明「按默认」。不要停轮空问。  
课型、课时、页数有默认，不必问。  
WorkBuddy 里跑 `.ps1` 必须用 **PowerShell 工具**，不要在 Bash 里套 `powershell.exe -File`。

## 引擎失败时

先看信封一行 JSON 的 `ok` / `code` / `suggestion`。

| `code` 或现象 | 处理 |
|---------------|------|
| 找不到命令 / 无版本号 | 按主技能「首次使用」征得同意后安装 |
| `validation` | 按 `suggestion` 改 JSON 或路径，再试一次 |
| `path_denied` | 路径须在 `--workspace` 内；Windows 原生路径；禁止 `..`；禁止 Git Bash 的 `/c/Users/...` |
| `image_skipped` | 即使 `ok:true` 也是这页没图。`path` 改成工作区内绝对路径，CLI 带 `--workspace`，勿写 `url` |
| `unknown_op` / `inspect_slide` 失败 | 引擎偏旧：改用 `preflight` + `view svg`；或换本仓 dogfood 二进制后再试 |
| 信封有 `suggestion` | **把 suggestion 告诉老师**，不要只说「生成失败」 |

改一处再跑。不要换输出文件名盲打第二次。

## 会不会上传学生信息？

不会。引擎不上传文档。本技能禁止把名册传到网盘或网页。大纲里不要写学生真名；没有学情数据就标 `[待补充]`，不编均分。

## 能不能成绩单、奖状、试卷？

不能走本技能。改口用 `zagens-office`，或另装对应场景技能。

## 只要大纲、不要 PPT？

可以。用户说只要大纲才停。用户开口就要课件、或默认提问已是「做一节……课件」时，新授课交完大纲同一轮出 PPT。

## `view svg` 的输出在哪？

线框在 **`result.metadata.svg`**（不是 `result.svg`）。v0.14.0 起通常有输出；仅当 `svg_len=0`（确实为空）才跳过，不当作出件失败。

## `inspect_slide` 和 `schema capabilities` 是什么？

新引擎提供的 Agent 闭环能力：`schema capabilities` 列出格式与 op；`inspect_slide` 一次返回该页文本 + LayoutIR + 版式问题。公开课逐页 upsert 后优先用它做档 A0。若本机 npm 引擎还没有这些命令，不要硬重试——退回档 A+B，或按 WorkBuddy 宿主说明换本仓构建的 dogfood exe。

## 公式不对 / 收到 `math_unsupported_command`？

见 `pptx-kejian.md` §公式。不支持的命令会保留字面文本并回该告警 → 改写法，或改用预渲染图（`path`）。引擎低于 0.14.2 时先升级，不要在旧引擎上拆分式凑合。**公式页必须打开文件目视**（preflight / view svg 查不出公式错）。

## 公开课能不能一次生成整份？

正式 / 公开课 / 说课 / 对外：必须骨架 write + 逐页 upsert。一次 `write` 塞满多页重内容，容易版式翻车，技能禁止。
