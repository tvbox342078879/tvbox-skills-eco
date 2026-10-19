---
name: wechat-article-publisher
display_name: 公众号图文发布助手
display_name_en: WeChat Article Publisher
description: >-
  微信公众号图文「HTML 导入 -> 排版 -> 发布」全流程技能。当用户要把 HTML / Markdown /
  Word 文档 / AI 生成稿发布到微信公众号，但粘贴后样式丢失（渐变背景变透明、class
  样式不生效、阴影/圆角/flex 失效）时使用。自动把任意内容转成公众号兼容的内联样式
  HTML，套用排版主题，生成移动端预览，并做发布前检查（标题评估 + 违规敏感词扫描）。
  触发词：公众号排版、公众号发布、微信图文、HTML 转公众号、粘贴样式丢了、markdown
  发公众号、公众号编辑器、图文排版、135 编辑器替代。亦覆盖 CSDN 技术文发布
  （纯文本 Markdown 转换 + mermaid 炸弹体检 + 1280×720 科技风封面图生成）。
description_zh: 微信公众号图文发布全流程技能：把 HTML / Markdown / Word / AI 生成稿一键转成公众号兼容的内联样式 HTML，多主题美化、图片 base64 内嵌不丢图、移动端一键复制预览，附六维发布前质检（标题评分 + 爆款度 + 敏感词扫描），另附 CSDN 纯文本 Markdown 通道与封面图生成。
description_en: End-to-end WeChat Official Account publishing skill that converts HTML / Markdown / Word / AI drafts into WeChat-compatible inline-style HTML with theme beautification, base64-embedded images, one-click mobile preview and six-dimension pre-publish checks, plus a plain-text Markdown channel and cover-image generator for CSDN.
version: 2.0.0
category: writing
author: 七仔的AI工具箱
agent_created: true
---

# 公众号图文发布器（wechat-article-publisher）

把"好看但公众号不认"的内容，转成"公众号认且依旧好看"的内容。核心认知：
**公众号编辑器只接受内联 style + 实色背景 + 静态布局**，会丢弃 `<style>` 类样式、
`linear-gradient` 背景、`box-shadow`、`transform`、`display:flex` 等。本技能用脚本
一次性完成转换、美化、检查。

> ## [!] 排在第一位的坑（务必先读，90% "没样式" 都栽在这）
> 公众号新版编辑器底层是 **ProseMirror**，**白名单里根本没有 `<div>`**。
> 一旦内容用 `<div style="background:...;border-radius:...;box-shadow:...">` 包着卡片 / 封面 /
> 金句框，**整个 div 连同它的背景、圆角、边框、阴影会被整段吞掉**，只剩光秃秃的文字 ->
> 表面看就是"整篇没 CSS"。这比"渐变被吃"更隐蔽，是粘贴后样式全失的头号元凶。
> **解法：所有块级容器一律用 `<section>` 而不是 `<div>`**（秀米 / 135 导出的代码全是 section）。
> 本技能转换器已自动把所有 `<div>` rename 成 `<section>`，无需手动处理；
> 但**你手写或让 AI 写 HTML 时也请直接用 `<section>`**，别留 `<div>` 等转换器擦屁股。

> ## 关键认知：你的自定义 class 也会被内联（旧版最大坑已修）
> 旧版转换器**只内联主题 CSS**，把你文章 `<head>` 里自己写的 `<style>` 整段丢弃——
> 结果 `.fact-box` / `.golden` / `.limit-box` 这些自定义 class 全部没样式，粘进公众号「样式全丢」。
> **新版已修复**：转换器会抽取文章自带的 `<style>`，与主题合并后**确定性内联每一个 class**
> （含后代选择器 `.fact-box b`、复合 `.a.b`、`:hover`/`@media` 自动跳过）。
> 所以你写 HTML 时**放心用 class 排版**，不用再手写一堆 `style="..."`；转换器会自动拍平。
> 回归测试见 `scripts/test_inline.py`（改 skill 前跑一遍，防退化）。

## 何时使用
- 用户有 HTML / Markdown 文章，要发公众号，但吐槽"复制粘贴样式没了"。
- 用户要"排版美化""套模板""检查标题/违规词"。
- 用户提供的是 Word 文档、网页链接、或 AI 生成稿，需要走完整发布流程。
- 用户要发**腾讯新闻 / 小红书 / 知乎 / 头条等其他平台**，抱怨"只有一键复制、效果差""只能手机发没网页"——用**图片版**（见下）。

> ## 其他平台（腾讯新闻等）：图片版才是万能解
> 公众号还能靠"内联样式粘贴"救一救；但**腾讯新闻、小红书这类平台的编辑器更封闭**，
> 常常只有"一键复制"、且会过滤几乎所有 CSS，卡片/背景/圆角全崩。
> **真正 100% 保真的办法：把排版好的 HTML 渲染成图片，当图片上传。** 图片是所有平台的
> 最大公约数，编辑器再简陋也认。用 `scripts/html_to_image.py` 生成：
> - `<名>_长图.png` —— 整篇一张，存手机相册直接上传；
> - `<名>_分段_NN.png` —— 按高度切多张，适合手机逐张上传/九宫格。
>
> **认知纠偏**：腾讯新闻**有电脑网页版**——腾讯新闻创作服务平台 `https://shizi.qq.com`，
> PC 端支持工具栏 + **文档导入整篇文章**，比手机"一键复制"强得多（纯文字/简单排版走它，
> 精美卡片走图片版）。别再以为"腾讯新闻只能手机发"。
>
> **调用命令**（先确保装了 playwright：`pip install playwright`）：
> ```bash
> # 默认方式：让 playwright 自带 Chromium 渲染
> python scripts/html_to_image.py 文章_wechat.html --outdir . --width 750 --split 1200
>
> # 复用本机已装 Chrome，跳过 Chromium 下载（推荐，省一次大体积下载）
> python scripts/html_to_image.py 文章_wechat.html --outdir . --width 750 --split 1200 \
>   --chrome "C:/Program Files/Google/Chrome/Application/chrome.exe"
> ```
> 参数：`--width 750`（手机 2 倍图）、`--split 1200`（按高度切多张，0=只出长图）、`--scale 2.0`（清晰度）、`--chrome` 指定系统浏览器路径。

## 完整四环节流程

### 环节 1 · 内容获取（Content Acquisition）
把原始内容归一为干净的 HTML 片段，下游脚本才好处理。
- **HTML 文件**：直接用 `wechat_convert.py` 处理（见环节 2）。
- **Markdown 文件**：`wechat_convert.py` 已内置 `.md` 识别，直接把 `.md` 当输入即可
  （用 `markdown` 库转 HTML，含表格/代码块/换行）。
- **Word 文档（.docx）**：先调用 `docx` 技能把 docx 导出为 HTML，再进入环节 2。
- **网页链接**：用 WebFetch 抓取正文，或 `Bash` 里 `curl` 取 HTML 后交给转换器。
- **AI 生成稿**：直接让模型产出 HTML 片段（要求内联样式、实色背景、不用渐变/flex），
  存成 `.html` 后进环节 2。

### 环节 2 · 格式转换（Convert to WeChat-safe HTML）
运行转换器，把任意 HTML/MD 变成公众号能认的版本：
```bash
PY="python"   # 换成你环境里的 Python 3 解释器；需已装 beautifulsoup4 / lxml / markdown
SKILL="<本 skill 目录>"   # 即本 SKILL.md 所在目录
$PY "$SKILL/scripts/wechat_convert.py" 输入.html --theme fresh --title "标题" --outdir 输出目录
# 也支持 .md：wechat_convert.py 文章.md --theme news
```
转换器自动完成：
1. **内联 CSS**：用**自研确定性内联器**（`scripts/wechat_convert.py` 里的 `inline_rules`）把
   「主题 + 你文章自带 `<style>`」的全部 class 拍平为内联样式。**关键点：你手写 / AI 生成的
   HTML 里自定义 class（如 `.golden` `.fact-box` `.limit-box`）也会自动内联，无需再手动写
   `style="..."`**——这正是旧版「粘贴后样式全丢」的根因修复（旧版只内联主题、丢弃了用户 `<style>`）。
2. **sanitize 兼容化**：渐变背景->实色；`box-shadow`/`transform`/`flex` 等丢弃；
   只保留公众号白名单属性（详见 `references/wechat_css_support.md`）。
3. **图片本地化**：本地图自动转 base64 内嵌（外链图保留并加 `max-width:100%`）。
4. **套主题 + 包裹容器**，产出**两份文件（必须同时生成，缺一不可）**：
   - `<名>_preview.html` —— **带「复制全文」按钮的 375px 移动端预览页**。
     页面上方一键复制（自动把带内联样式的富文本送进剪贴板），下方即为公众号内真实效果，所见即所得。
   - `<名>_wechat.html` —— **纯内容版**，浏览器打开后 `Ctrl+A` 全选 -> `Ctrl+C` 复制，粘进公众号编辑器同样有效。
   > 两个文件用途互补：preview 主打"一键复制 + 实时预览"，wechat 是兜底"手动全选复制"。
   > 两者都保留兼容化后的内联样式，**严禁只生成其中一个**。

可用主题：`fresh`（紫蓝，默认）/ `minimal`（极简黑白）/ `news`（红头资讯）/
`warm`（暖橙）/ `none`（不套主题，仅做兼容转换）。

> ## 多推文命名约定（避免不同推文互相覆盖，重要）
> 转换器默认用「输入文件名」做输出前缀：输入 `article.html` -> 产出 `article_wechat.html` /
> `article_preview.html`。**若每次源文件都叫 `article.html`，四件套会互相覆盖、只留最后一篇。**
> **规范（强烈建议）**：每篇推文用「技能 / 主题标识」作统一前缀——源文件、转换器、诊断器三处一致：
> ① 源文件命名：`epub-to-txt.html` / `voice-reader.html` / `wechat-article-publisher.html`；
> ② 转换与诊断都传 `--name <同一前缀>`；省略 `--report` 时报告自动落到 `<名>_report.html`。
> 四件套 `<名>.html / <名>_preview.html / <名>_wechat.html / <名>_report.html` 互不覆盖，目录一眼可辨。
> ```bash
> $PY "$SKILL/scripts/wechat_convert.py" epub-to-txt.html --theme fresh --title "标题" --name epub-to-txt --outdir .
> $PY "$SKILL/scripts/check_publish.py" --title "标题" --html epub-to-txt_wechat.html --name epub-to-txt
> ```

### 环节 3 · 排版美化（Beautify）
美化靠"套主题"在环节 2 一步完成。若用户对默认样式不满意：
- 换主题：`--theme news` 等重跑。
- 微调：主题 CSS 在 `assets/*.css.txt`，改里面 `h2`/`blockquote`/`table` 等
  选择器即可（改完重跑转换器）。组件类 `.callout`（信息框）、`.note`（提示框）可直接
  在 HTML 里用 `<section class="callout">…</section>`（注意：**用 section，别用 div**，见上方"排在第一位的坑"）。
- 想加封面图/分隔/文末引导，直接在 HTML 里用 `<img>`/`<hr>`/`<section>` 写内联样式，
  转换器会保留并兼容化。

### 配图嵌入工作流（多模态生成 + 压缩 + 内嵌）
诊断报告的「审美排版」项常提示"纯文字完读率偏低，每屏加 1 张图"。本流程把配图做对、且**公众号粘贴不丢图**（已实战验证）：

1. **生成配图**：用多模态 ImageGen 出图（封面 / 原理示意 / 情绪场景 / 展望 各 1 张足够，共 3–4 张）。
   提示词带主题色让多张图视觉统一，与 `fresh` 紫蓝主题（主色 `#667eea` / `#8b5cf6`）一致：
   如 `indigo, violet, purple gradient, clean modern illustration`。要求 `no text, no watermark`。
   > 实测：ImageGen 出图约 1.3MB/张，必须用下一步压缩，否则内嵌后公众号丢图。
2. **压缩（铁律，否则丢图）**：压到 **<100KB**（宽 ≤1000px、JPEG 质量 80）：
   ```bash
   PY="python"   # 换成你环境里的 Python 3 解释器
   $PY -c "from PIL import Image; im=Image.open('cover.png').convert('RGB'); \
   w,h=im.size; im=im.resize((1000,int(h*1000/w)),Image.LANCZOS) if w>1000 else im; \
   im.save('cover.jpg','JPEG',quality=80,optimize=True)"
   ```
3. **写入 HTML**（用 section/class，别用 div；见上方"排在第一位的坑"）：
   ```html
   <style>
   .fig{margin:20px 0;text-align:center;}
   .fig img{border-radius:12px;max-width:100%;display:block;margin:0 auto;box-shadow:0 4px 14px rgba(80,70,160,.18);}
   .fig .cap{font-size:12px;color:#999;margin-top:8px;line-height:1.6;}
   </style>
   ...
   <figure class="fig">
     <img src="verify/promo_imgs/cover.jpg" alt="封面图描述">
     <p class="cap">一句话图注，点出这张图在说什么。</p>
   </figure>
   ```
   > `src` 用**相对路径**（相对于输入 HTML 所在目录），转换器按此 base_dir 找图。
   > 转换器会**自动把文章自带 `<style>` 里的 `.fig`/`.cap` 拍平内联**（见下方"关键认知"），图样式不会丢。
4. **转换器自动 base64 内嵌**：`wechat_convert.py` 默认 `--img-mode embed`，
   把本地图转 `data:image/jpeg;base64,...` 内嵌进 `_wechat.html`——复制时图片跟着富文本走，
   粘进公众号编辑器**不丢图**（日志会打印"本地图片已转 base64 内嵌：xxx.jpg"）。
5. **验证**：
   - `grep -o "data:image/jpeg;base64" 文章_wechat.html | wc -l` 应等于你插的图数；
   - 复跑 `check_publish.py`，「审美排版」应到 100/100。
6. **踩坑提示**：① 图不压缩（>100KB）内嵌 -> 公众号丢图；② `src` 用绝对路径但转换器 base_dir 是 HTML 目录 -> 用相对路径；
   ③ 外链图（http）不内嵌，只加 `max-width:100%`，依赖图床稳定，公众号可能仍丢，优先用本地图。

### 环节 4 · 发布前诊断 + 改进建议（Pre-publish Diagnosis）
```bash
$PY "$SKILL/scripts/check_publish.py" --title "文章标题" --html <名>_wechat.html --name <名>
```
不止"查违规"，更给"怎么写更好"的可操作建议，覆盖**六个维度**：
1. **标题**：评分(0-100) + 短板分析 + 2-3 个爆款向改写示例（如"别再踩坑！…的真相"）。
2. **内容爆款度**：综合分 + 七项细分（开头 hook / 金句密度 / 段落节奏 / 互动引导 / CTA / 小标题结构 / 干货密度），每项给改进点。
3. **创意差异化**：套路化程度 + 痛点命中，提示如何"反常识/具体场景"切入。
4. **审美排版**：callout 视觉锚点 / 配图 / 分隔线密度，建议如何加视觉节奏。
5. **传播力·转发收藏驱动力**：五大驱动力——社交货币 / 情绪共鸣 / 认知增量 / 身份认同 / 可复用资产，逐一给提升建议（一篇好文值得转藏，远不止"解决痛点"）。
6. **违规敏感词扫描**：四类（绝对化 / 诱导分享关注 / 夸大承诺 / 医疗金融红线），词库 `assets/sensitive_words.json`。

加 `--name <名>`（或显式 `--report`）会生成可视化诊断报告（默认 `<名>_report.html`，分数条 + 改进清单），
在浏览器打开即可边看边改；`--name` 与转换器保持一致可保证报告名与推文前缀对齐、互不覆盖。标题技巧见 `references/title_tips.md`。

## 粘贴到公众号的正确姿势（务必告诉用户）
1. 用**浏览器**打开 `_wechat.html`（不要从代码编辑器/记事本复制，那样只带纯文本）。
2. `Ctrl/Cmd+A` 全选 -> `Ctrl/Cmd+C`。
3. 切到公众号编辑器 `Ctrl/Cmd+V` 粘贴。
4. 嫌麻烦可装「壹伴 / 新媒体管家 / 公众号助手」浏览器插件，用「导入 HTML」直接灌代码。

## 参考与资源
- `references/wechat_css_support.md` —— 公众号 CSS 支持/不支持完整对照，人工排查用。
- `references/title_tips.md` —— 标题长度、钩子、避坑经验。
- `assets/*.css.txt` —— 4 套排版主题，可改。
- `assets/sensitive_words.json` —— 违规敏感词库，可按账号领域增删。
- `scripts/wechat_convert.py` —— 转换+美化+预览主脚本。
- `scripts/check_publish.py` —— 标题评估+敏感词扫描脚本。
- `scripts/csdn_convert.py` —— HTML/MD -> CSDN Markdown 转换器（**默认纯文本不嵌图、无文末截图占位**；`--keep-img` 可选保留图[导出文件] + 轻度中性化）。
- `scripts/cover_prompt.py` —— 由文章标题+关键词生成 CSDN 封面图 ImageGen 提示词（1280×720 科技风，no text）。
- `tests/run_regression.py` —— **回归测试**（见下「稳定性保障」；现已同时校验 `csdn_convert.py`）。

## 标题方法论（升级版 · 2026-07）

标题决定打开率。诊断时 `check_publish.py` 按「让对的人 3 秒觉得与我有关、点开有用」打分，覆盖 4 动作 + 5 公式 + 15-22 字 + 禁用标题党。要点：

- **4 动作（顺序即权重）**：先圈人（越具体越好：微信读书重度用户 / 书架 100 本+）-> 再戳痛点 -> 给明确收益（3 个方法 / 9 步炼成能力坐标）-> 留钩子（别把答案说完）。
- **5 公式**：人群+痛点+数字 / 结果+方法 / 提问式 / 避坑式 / 反差式。诊断会告诉你命中哪一式。
- **长度 15–22 字**：手机端太长会被「…」截断；核心词前置。
- **禁用词强惩罚（-25）**：震惊 / 必看 / 不看后悔 / 100%有效 / 最好 / 第一 / 永久……既像标题党又易限流。
- **阅读量低的 5 原因**：标题封面没吸引力 / 内容不垂直 / 完读率低 / 缺互动引导 / 踩隐形限流（详见 `references/title_tips.md`）。

## 小红书九宫格（9 图）标准流程

公众号还能靠内联样式粘贴；小红书 / 腾讯新闻编辑器更封闭，**图片版才是万能解**。本技能内置 9 张方图模板：

1. 生成 3 张配图（封面 / 痛点 / 机制，紫蓝主色，no text），压到 <100KB：用法见上方「配图嵌入工作流」。
2. 复制模板并按需改文案：`assets/xhs_grid.html`（含 9 张 1080×1080 方卡：封面->痛点×3->机制×2->真实案例->局限->CTA，3 张占位图可删）。
3. 渲染 9 张方图（**默认只出 9 图，不强制金句卡 / 长图**）：
   ```bash
   python scripts/html_to_image.py assets/xhs_grid.html --outdir 输出目录 \
     --width 1080 --split 1080 --scale 2 \
     --chrome "C:/Program Files/Google/Chrome/Application/chrome.exe"
   ```
   输出 `xxx_分段_01..09.png`，按顺序在小红书发九宫格。要长图时另加 `--split 0` 单独出一张。
4. 金句卡 / 长图是可选的——只要九宫格时，跳过 quote / 长图即可。

## CSDN / 技术社区 MD 发布（已外置）

CSDN / 掘金 / 知乎技术栏发布是**独立子流程**，正文较长且自成体系，已移到 `docs/csdn_publishing.md`（含：纯文本转换通道、不用图铁律、mermaid 炸弹规避、过审铁律、arXiv 引用格式与核验协议、封面图规范）。公众号主流程不走这条。

快速入口（转换同一篇源 HTML 为干净纯文本 MD）：
```bash
$PY "$SKILL/scripts/csdn_convert.py" 输入.html --title "标题" --name <前缀> --outdir .
# 也支持 .md：csdn_convert.py 文章.md --name <前缀>
# 如需保留图片（导出为独立文件，需手动上传），加 --keep-img
```
详细铁律、封面图渲染、引用核验回执等见 `docs/csdn_publishing.md`。

## 稳定性保障（重要 · 改这个 skill 前必读）
本 skill 早期「不稳定」的根因已根治，改代码时**严禁回退**：

1. **清洗 100% 在 Python 完成，浏览器里的 JS 不做任何转换。**
   `wechat_convert.py` 的 `build_preview()` 预览页「复制全文」按钮，只是把 `#copy-target`
   里**已由 Python 洗干净的 HTML** 写进剪贴板——**不**在 JS 里再跑 sanitize / 正则 / 转义。
   [X] 不要把渐变转换、div->section、剥宽度等逻辑写回 `<script>`：
   那段 JS 嵌在 Python f-string 里，`\'` 会被 f-string 吞掉反斜杠 -> JS 语法崩 ->
   `copyContent is not defined` -> 用户点「复制全文」**毫无反应**（这是早期最频繁的故障）。
   [OK] 正确做法：任何兼容化都在 `wechat_convert.py` 的 Python 管道做，
   预览 JS 保持「只复制、不转换」的最小形态（无正则、无单引号转义）。

2. **出厂质检（硬门禁）**：`validate_wechat(html)` 在写文件前断言
   `0 <div> / 0 渐变 / 0 固定 max-width / 0 UI 铬层(.bar/.tip/#phone/#copied/button) /
   0 <script> / 0 <style> / 0 <!DOCTYPE> / 0 <html><head><body>`。
   任一不过 -> 打印具体问题并 `sys.exit(1)`。**不要删这门禁去「掩盖」质检失败。**

3. **回归样例**：`tests/fixtures/*.source.html` 是历史出过问题的源文件
   （工具栏泄漏、内容太窄 375px、复制没反应）。每次改完跑：
   ```bash
   python tests/run_regression.py
   ```
   它做三层校验：① 转换退出码必须 0（质检门禁）；
   ② 提取生成预览里的 `<script>` 用 `node --check` 验语法（卡住「复制没反应」类故障）；
   ③ 模拟点复制，提取 `#copy-target` 内容断言无 `<div>`/渐变/工具栏文字/UI 铬层。
   全绿才算改完。依赖：转换用 `beautifulsoup4 lxml markdown`，语法检查用 `node`。

4. **硬卡口（git pre-commit + safe_edit.py）—— 让「改完跑回归」从软约定变物理强制。**
   上面 1/2/3 是「记得做」的约定；这条是「不做就提交不了」的强制：
   - 手动验证：`python safe_edit.py`（等价 `python tests/run_regression.py`，但更顺手）。
   - 一键装强制：`python safe_edit.py --install-hook`
     会在 skill 目录 `git init` 并写入 `.git/hooks/pre-commit`，
     以后**任何 `git commit` 都会自动跑回归，不过则 `exit 1` 阻止提交**。
     agent 想绕过也绕不过——它只要 commit 交付，hook 就拦。
   - **分发恢复**：发出去的 zip 不含 `.git`，拿到 skill 的人
     运行 `python safe_edit.py --install-hook` 即可在本机恢复硬卡口。
   - 这样三层保险：1 SKILL.md 约定（加载即读）2 MEMORY.md 常驻（boot 必读）
     3 **git pre-commit 物理强制（commit 不过回归就 block）**。

## 版权与署名

本 skill 为原创工具，可免费使用与二次分享；转载请注明出处「七仔的AI工具箱」。
内置的公众号 CSS 兼容性结论、敏感词库与发布质检方法均为作者实战经验总结，未包含任何第三方受版权保护的原文摘录。

## 作者与联系方式

本 skill 由「七仔的AI工具箱」维护。欢迎就技能使用、问题反馈与二次分享交流：

- 微信公众号：七仔的AI工具箱
- 抖音号：七仔的AI工具箱
- CSDN 博客：七仔的AI工具箱

本 skill 可免费使用与二次分享；转载请注明出处「七仔的AI工具箱」。
