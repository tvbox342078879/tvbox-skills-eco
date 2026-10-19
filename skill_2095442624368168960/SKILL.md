---
name: gzh-typeset
display_name: 公众号排版工坊
display_name_en: WeChat Typeset Studio
description: 把 Markdown/Word/纯文本/网页内容排版成微信公众号可直接粘贴的 HTML，自带靛蓝档案、暖砂杂志、电光蓝图三套原创主题，多屏自适应、一键复制到公众号草稿箱且样式零丢失。触发词：公众号排版、公众号文章排版、微信排版、排版成公众号、gzh 排版、公众号 HTML、一键复制到公众号、公众号主题、公众号草稿。
description_zh: 将任意文本排版为微信公众号可直接粘贴的 HTML，内置三套原创主题，支持多屏自适应预览与一键复制，样式零丢失。
description_en: Typeset any content into paste-ready WeChat Official Account HTML with three original themes, responsive preview and one-click copy that preserves all inline styles.
category: writing
version: 1.0.0
author: baiying-studio
allowed-tools: Read,Write,Edit,Bash,Glob,Grep,WebFetch
---

# 公众号排版工坊 gzh-typeset

把内容变成**能直接粘贴进公众号编辑器、样式分毫不差**的 HTML。

核心能力：三套原创主题、微信红线机检、多屏自适应预览、一键复制（保留富文本结构）。

---

## 何时使用

用户出现以下任一意图时使用本技能：

- 要把一篇文章 / 一篇文档「排版成公众号」「做成公众号 HTML」「能直接粘贴到公众号」
- 提到「微信排版」「公众号主题」「公众号草稿」「一键复制」
- 已经写好内容，需要输出可发布的 HTML 成品

**不适用**：只是要写文章正文而不要排版、要发到知乎/掘金等非公众号平台。

---

## 工作流程

严格按 1→7 顺序执行，**不要跳步**。

### 第 1 步 · 归一化输入

读 @references/input-normalize.md，把用户给的内容（Markdown / Word 文本 / 纯文本 / URL / 口述要点）整理成该文件的「归一化目标结构」：meta / body（带 role 的区块列表）/ tail。

**区块列表必须在动笔写 HTML 之前定稿**。临时决定组件会导致同一层级出现两种视觉重量。

### 第 2 步 · 选定主题

读 @references/theme-index.md，按其中的「主题选择决策」选定**唯一**主题：

| 内容类型 | 主题 |
|---|---|
| 技术教程、产品测评、架构拆解、参数横评、踩坑记录 | `electric-blueprint` 电光蓝图 |
| 深度分析、行业报告、观点长文、案例复盘 | `indigo-archive` 靛蓝档案 |
| 人物故事、行业观察、生活方式、叙事长文 | `sand-magazine` 暖砂杂志 |

用户已指定主题时直接用指定的。无法判断时用 `electric-blueprint` 兜底。

### 第 3 步 · 加载组件库

**只读**选中主题的文件（不要同时读多套，会混色）：

- `electric-blueprint` → @references/theme-electric-blueprint.md
- `indigo-archive` → @references/theme-indigo-archive.md
- `sand-magazine` → @references/theme-sand-magazine.md

再读 @references/common-components.md 取通用件（代码块 G01、表格 G04、图片 G05、标签 G07、指标 G08、步骤 G09、正反对比 G10、来源 G06/G11、版权 G12），并**用 theme-index 的权威值表替换其中的 `{{占位符}}`**。

### 第 4 步 · 组装 HTML

按主题文件末尾的「完整文章模板骨架」组装。同时遵守 @references/platform-rules.md 的全部红线。

四条最容易翻车的，反复确认：

1. **每个文字节点都包 `<span leaf="">`** —— 这是粘贴不掉样的根本。
2. **空装饰块（色条/圆点/分隔线）也要放 `<span leaf="">&nbsp;</span>`**，否则被编辑器折叠消失。
3. **图片 `max-width:100%;height:auto`**，绝不用 `width:100%`；本地相对路径必定裂图，先上传图床。
4. **代码块逐行 `<p style="margin:0">`，缩进用 `&nbsp;` 补**，禁用 `white-space:pre`。

每段正文主动标 **1~3 处关键词**（主题文件的「强调 c」）。这是排版"有设计感"和"一大坨灰字"的分界线。

### 第 5 步 · 机检（必做）

```bash
python scripts/validate_gzh_html.py "<输出.html>"
```

- **ERROR 必须为 0** 才能交付。有 ERROR 就按行号修到 0。
- WARNING 逐条确认（常见：半角中文标点、图片是本地相对路径）。
- 不要凭感觉跳过校验 —— 这条规则是踩了几十次坑攒出来的。

### 第 6 步 · 生成预览页（必做）

```bash
python scripts/wrap_preview.py "<输出.html>" "<标题>"
```

产出同名 `_预览.html`。预览页带：

- 手机 375px / 平板 768px / 桌面 100% **三档实时切换**
- **一键复制**按钮（复制渲染后的富文本 DOM，不是源码）

### 第 7 步 · 交付

用 `present_files` 同时给出 `输出.html` 和 `输出_预览.html`，并说明：

> 打开预览页 → 切换设备档位确认多屏效果 → 点「一键复制」→ 到公众号编辑器 Ctrl+V。
> 不要复制 HTML 源码文本，那样会丢全部样式。

---

## 文件索引

| 文件 | 用途 | 什么时候读 |
|---|---|---|
| @references/theme-index.md | 主题选择的唯一真源 + 三套主题权威色值 | 第 2、3 步必读 |
| @references/theme-electric-blueprint.md | 电光蓝图 18 个组件 | 选中该主题时读 |
| @references/theme-indigo-archive.md | 靛蓝档案 18 个组件 | 选中该主题时读 |
| @references/theme-sand-magazine.md | 暖砂杂志 18 个组件 | 选中该主题时读 |
| @references/common-components.md | 跨主题通用件 G01~G12 | 第 3 步读 |
| @references/platform-rules.md | 微信红线、span leaf、自适应、复制原理 | 有疑问时查；第 4 步前过一遍 |
| @references/input-normalize.md | 输入归一化 | 第 1 步读 |
| `scripts/validate_gzh_html.py` | 红线机检 | 第 5 步 |
| `scripts/wrap_preview.py` | 生成预览页 | 第 6 步 |
| `scripts/lint_themes.py` | 主题色值一致性自检（扩展主题时用） | 扩展主题时 |
| `templates/preview-template.html` | 预览页外壳 | 由 wrap_preview.py 调用 |

---

## 硬性约束（违反即废稿）

- 禁用标签：`<style>` `<script>` `<div>` `<link>` `<table>` 系列 `<iframe>` `<svg>`
- 禁用属性：`class` `id` 自定义 `data-*`（`<mp-style-type data-value="3">` 除外）
- 禁用 CSS：`position:fixed/absolute/sticky`、`float`、`@media`、`@keyframes`、`@import`、`display:grid`、`var()`、`!important`、外部字体
- 必须：样式全内联、`<img>` 用 `max-width:100%;height:auto`、中文用全角标点
- 必须：文末容器外放 `<p style="display:none;"><mp-style-type data-value="3"></mp-style-type></p>`

完整清单见 @references/platform-rules.md。

---

## 扩展主题

新增主题后**必须**同步更新 @references/theme-index.md（速览表 + 权威值表），然后跑：

```bash
python scripts/lint_themes.py
```

新主题硬要求：主色与点睛色不得与现有主题重复；正文字号 15~16px；圆角 ≤8px；无阴影、无渐变。

---

## 一句话心法

**宁可少放一个组件，也不要放错一个组件。** 三套主题各自只有 18 个组件，把它们用对位置，比临时发明新样式可靠得多。
