# 通用组件库（跨主题）

> 本文件的组件**与主题无关**，用占位符引用主题变量。使用前先根据 `theme-index.md` 把占位符替换成当前主题的实际值。
> 主题专属组件见各 `theme-<id>.md`。

---

## 变量占位符

| 占位符 | 含义 | indigo-archive | sand-magazine | electric-blueprint |
|---|---|---|---|---|
| `{{PAPER}}` | 全局纸底色 | `#F4F6FB` | `#FDF8F1` | `#F5F8FC` |
| `{{INK}}` | 标题 / 深字 | `#18203F` | `#3B2E24` | `#0E1726` |
| `{{TEXT}}` | 正文色 | `#464C66` | `#4A3F33` | `#3D4A5C` |
| `{{SUB}}` | 次要文字 | `#6B7290` | `#7A6A57` | `#6E7C90` |
| `{{FADE}}` | 弱化文字 | `#9BA2B8` | `#A8968A` | `#A3AFC0` |
| `{{BORDER}}` | 边框 / 分隔线 | `#C8D0E4` | `#E2D5C3` | `#D5E1F0` |
| `{{PRIMARY}}` | 主题主色 | `#2B3A67` | `#A85C27` | `#0B63CE` |
| `{{ACCENT}}` | 点睛色 | `#B8860B` | `#2F6B5A` | `#00A9C4` |
| `{{TINT}}` | 标签浅底 | `#E6EAF5` | `#F3E7D6` | `#E4EEFB` |
| `{{CODE_BG}}` | 代码块深底 | `#1E2540` | `#2E2820` | `#0E1726` |
| `{{CODE_DOT}}` | 代码块顶部点色 | `#4A5578` | `#5A5040` | `#2A3B52` |
| `{{CODE_TEXT}}` | 代码正文色 | `#D7DEEF` | `#E8DFD0` | `#CBD9EC` |
| `{{CODE_KEY}}` | 代码强调色 | `#E3B23C` | `#C98A4B` | `#4DD0E1` |
| `{{MONO}}` | 等宽字体栈 | 见下 | 见下 | 见下 |

**等宽字体栈 `{{MONO}}`**（三主题统一）：
```
ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace
```

**无衬线字体栈 `{{SANS}}`**（三主题统一）：
```
-apple-system,system-ui,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif
```

---

## G01 代码块 code-block

**用途**：命令行、代码片段、目录结构、配置示例。
**铁律**：
- ❌ **禁止 `white-space:pre`** —— 微信会合并空白，缩进全丢。
- ✅ **每一行代码一个 `<p style="margin:0">`**，缩进用 `&nbsp;` 手工补齐（1 缩进 = 2 个 `&nbsp;`）。
- 空行也要写 `<p style="margin:0;line-height:1.7;"><span leaf="">&nbsp;</span></p>`。

```html
<section style="margin-top:20px;border-radius:4px;overflow:hidden;background:{{CODE_BG}};">
  <section style="display:flex;align-items:center;gap:5px;padding:9px 14px;background:{{CODE_BG}};border-bottom:1px solid rgba(255,255,255,0.08);">
    <span style="width:8px;height:8px;border-radius:50%;background:{{CODE_DOT}};display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    <span style="width:8px;height:8px;border-radius:50%;background:{{CODE_DOT}};display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    <span style="width:8px;height:8px;border-radius:50%;background:{{CODE_DOT}};display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    <span style="flex:1;min-width:0;"></span>
    <span style="font-size:10px;color:{{CODE_KEY}};letter-spacing:0.5px;font-family:{{MONO}};"><span leaf="">{{语言/文件名}}</span></span>
  </section>
  <section style="padding:14px 16px;overflow-x:auto;">
    <p style="margin:0;font-size:12.5px;line-height:1.75;color:{{CODE_TEXT}};font-family:{{MONO}};word-break:break-word;"><span leaf="">{{代码第1行}}</span></p>
    <p style="margin:0;font-size:12.5px;line-height:1.75;color:{{CODE_TEXT}};font-family:{{MONO}};word-break:break-word;"><span leaf="">&nbsp;&nbsp;{{代码第2行，2空格缩进}}</span></p>
    <p style="margin:0;font-size:12.5px;line-height:1.75;color:{{CODE_KEY}};font-family:{{MONO}};word-break:break-word;"><span leaf="">&nbsp;&nbsp;{{高亮行，换 CODE_KEY 色}}</span></p>
    <p style="margin:0;line-height:1.75;"><span leaf="">&nbsp;</span></p>
    <p style="margin:0;font-size:12.5px;line-height:1.75;color:{{CODE_TEXT}};font-family:{{MONO}};word-break:break-word;"><span leaf="">{{代码第N行}}</span></p>
  </section>
</section>
```

**目录结构示例**（缩进用 `&nbsp;`，树线用半角字符）：
```html
<section style="margin-top:20px;border-radius:4px;overflow:hidden;background:{{CODE_BG}};">
  <section style="padding:9px 14px;border-bottom:1px solid rgba(255,255,255,0.08);">
    <span style="font-size:10px;color:{{CODE_KEY}};letter-spacing:0.5px;font-family:{{MONO}};"><span leaf="">// {{标题}}</span></span>
  </section>
  <section style="padding:14px 16px;">
    <p style="margin:0;font-size:12.5px;line-height:1.75;color:{{CODE_TEXT}};font-family:{{MONO}};"><span leaf="">my-skill/</span></p>
    <p style="margin:0;font-size:12.5px;line-height:1.75;color:{{CODE_TEXT}};font-family:{{MONO}};"><span leaf="">&nbsp;&nbsp;├── SKILL.md</span></p>
    <p style="margin:0;font-size:12.5px;line-height:1.75;color:{{CODE_TEXT}};font-family:{{MONO}};"><span leaf="">&nbsp;&nbsp;├── references/</span></p>
    <p style="margin:0;font-size:12.5px;line-height:1.75;color:{{CODE_TEXT}};font-family:{{MONO}};"><span leaf="">&nbsp;&nbsp;│&nbsp;&nbsp;&nbsp;└── theme-index.md</span></p>
    <p style="margin:0;font-size:12.5px;line-height:1.75;color:{{CODE_TEXT}};font-family:{{MONO}};"><span leaf="">&nbsp;&nbsp;└── scripts/</span></p>
  </section>
</section>
```

---

## G02 单行命令 inline-command

**用途**：只需要一行的 shell 命令，比完整代码块轻量。

```html
<section style="margin-top:16px;background:{{CODE_BG}};border-radius:4px;padding:11px 14px;">
  <p style="margin:0;font-size:12.5px;line-height:1.7;color:{{CODE_TEXT}};font-family:{{MONO}};word-break:break-all;"><span leaf="">$ {{命令}}</span></p>
</section>
```

---

## G03 目录导航 toc

**用途**：长文（>2500 字）开篇导航。等宽编号 + 虚线引导。

```html
<section style="margin-top:22px;background:{{TINT}};border-radius:4px;padding:16px 18px;">
  <p style="margin:0 0 10px;font-size:10px;font-weight:700;letter-spacing:2px;color:{{PRIMARY}};font-family:{{MONO}};"><span leaf="">CONTENTS</span></p>
  <section style="display:flex;align-items:baseline;gap:8px;margin-top:7px;">
    <span style="flex-shrink:0;font-size:11px;font-weight:700;color:{{ACCENT}};font-family:{{MONO}};"><span leaf="">01</span></span>
    <p style="margin:0;flex:1;min-width:0;font-size:13px;color:{{TEXT}};line-height:1.7;"><span leaf="">{{章节名}}</span></p>
  </section>
  <section style="display:flex;align-items:baseline;gap:8px;margin-top:7px;">
    <span style="flex-shrink:0;font-size:11px;font-weight:700;color:{{ACCENT}};font-family:{{MONO}};"><span leaf="">02</span></span>
    <p style="margin:0;flex:1;min-width:0;font-size:13px;color:{{TEXT}};line-height:1.7;"><span leaf="">{{章节名}}</span></p>
  </section>
</section>
```

---

## G04 通用对照表（N 列）

**用途**：3 列及以上的对比。**禁止 `<table>`**，用 flex 行。
**规则**：偶数列用 `border-left` 分隔；数据行斑马纹；屏宽不足时靠 `flex-wrap:wrap` 让行内单元换行。

```html
<section style="margin-top:22px;border:1px solid {{BORDER}};border-radius:4px;overflow:hidden;">
  <section style="display:flex;background:{{PRIMARY}};">
    <section style="flex:1;min-width:0;padding:10px 12px;"><p style="margin:0;font-size:12px;font-weight:700;color:#FFFFFF;"><span leaf="">{{列1}}</span></p></section>
    <section style="flex:1;min-width:0;padding:10px 12px;border-left:1px solid rgba(255,255,255,0.18);"><p style="margin:0;font-size:12px;font-weight:700;color:#FFFFFF;"><span leaf="">{{列2}}</span></p></section>
    <section style="flex:1;min-width:0;padding:10px 12px;border-left:1px solid rgba(255,255,255,0.18);"><p style="margin:0;font-size:12px;font-weight:700;color:#FFFFFF;"><span leaf="">{{列3}}</span></p></section>
  </section>
  <section style="display:flex;background:#FFFFFF;border-top:1px solid {{BORDER}};">
    <section style="flex:1;min-width:0;padding:10px 12px;"><p style="margin:0;font-size:13px;color:{{TEXT}};line-height:1.7;word-break:break-word;"><span leaf="">{{数据}}</span></p></section>
    <section style="flex:1;min-width:0;padding:10px 12px;border-left:1px solid {{BORDER}};"><p style="margin:0;font-size:13px;color:{{TEXT}};line-height:1.7;word-break:break-word;"><span leaf="">{{数据}}</span></p></section>
    <section style="flex:1;min-width:0;padding:10px 12px;border-left:1px solid {{BORDER}};"><p style="margin:0;font-size:13px;color:{{TEXT}};line-height:1.7;word-break:break-word;"><span leaf="">{{数据}}</span></p></section>
  </section>
  <section style="display:flex;background:{{PAPER}};border-top:1px solid {{BORDER}};">
    <section style="flex:1;min-width:0;padding:10px 12px;"><p style="margin:0;font-size:13px;color:{{TEXT}};line-height:1.7;word-break:break-word;"><span leaf="">{{数据}}</span></p></section>
    <section style="flex:1;min-width:0;padding:10px 12px;border-left:1px solid {{BORDER}};"><p style="margin:0;font-size:13px;color:{{TEXT}};line-height:1.7;word-break:break-word;"><span leaf="">{{数据}}</span></p></section>
    <section style="flex:1;min-width:0;padding:10px 12px;border-left:1px solid {{BORDER}};"><p style="margin:0;font-size:13px;color:{{TEXT}};line-height:1.7;word-break:break-word;"><span leaf="">{{数据}}</span></p></section>
  </section>
</section>
```

**列宽控制**：需要某列更宽时，把该列 `flex:1` 改 `flex:1.4`，其余保持 `flex:1`。

---

## G05 图片（单图 / 双图并排）

**单图**
```html
<section style="margin-top:22px;">
  <img src="{{图片URL}}" style="max-width:100%;height:auto;display:block;border:0;border-radius:4px;" />
  <p style="margin:8px 0 0;font-size:12px;color:{{FADE}};line-height:1.7;text-align:center;"><span leaf="">{{图注}}</span></p>
</section>
```

**双图并排（窄屏自动堆叠）**
```html
<section style="margin-top:22px;display:flex;gap:10px;flex-wrap:wrap;">
  <section style="flex:1 1 240px;">
    <img src="{{图片1}}" style="max-width:100%;height:auto;display:block;border:0;border-radius:4px;" />
    <p style="margin:6px 0 0;font-size:11px;color:{{FADE}};line-height:1.7;text-align:center;"><span leaf="">{{图注1}}</span></p>
  </section>
  <section style="flex:1 1 240px;">
    <img src="{{图片2}}" style="max-width:100%;height:auto;display:block;border:0;border-radius:4px;" />
    <p style="margin:6px 0 0;font-size:11px;color:{{FADE}};line-height:1.7;text-align:center;"><span leaf="">{{图注2}}</span></p>
  </section>
</section>
```

---

## G06 脚注 / 来源标注

**用途**：引用数据、外部资料时标注出处，规避版权风险。

```html
<section style="margin-top:16px;">
  <p style="margin:0;font-size:11px;color:{{FADE}};line-height:1.8;word-break:break-word;">
    <span leaf="">［1］{{来源名称}}，{{文章标题}}，{{发布日期}}。</span>
  </p>
</section>
```

**多条来源**
```html
<section style="margin-top:22px;border-top:1px solid {{BORDER}};padding-top:14px;">
  <p style="margin:0 0 8px;font-size:10px;font-weight:700;letter-spacing:2px;color:{{FADE}};font-family:{{MONO}};"><span leaf="">REFERENCES</span></p>
  <p style="margin:0 0 5px;font-size:11px;color:{{FADE}};line-height:1.8;word-break:break-word;"><span leaf="">［1］{{来源}}</span></p>
  <p style="margin:0 0 5px;font-size:11px;color:{{FADE}};line-height:1.8;word-break:break-word;"><span leaf="">［2］{{来源}}</span></p>
</section>
```

> **重要**：公众号正文**不能放可点击外链**。来源一律用文字写明，或统一放到「阅读原文」。

---

## G07 关键词标签组 tag-group

**用途**：文末话题标签、技术栈罗列。

```html
<section style="margin-top:20px;display:flex;flex-wrap:wrap;gap:8px;">
  <span style="display:inline-block;background:{{TINT}};color:{{PRIMARY}};padding:4px 11px;border-radius:3px;font-size:11px;font-weight:600;"><span leaf="">#{{标签}}</span></span>
  <span style="display:inline-block;background:{{TINT}};color:{{PRIMARY}};padding:4px 11px;border-radius:3px;font-size:11px;font-weight:600;"><span leaf="">#{{标签}}</span></span>
  <span style="display:inline-block;background:{{TINT}};color:{{PRIMARY}};padding:4px 11px;border-radius:3px;font-size:11px;font-weight:600;"><span leaf="">#{{标签}}</span></span>
</section>
```

---

## G08 数据指标块 metric-row

**用途**：并排展示 2~3 个关键数字，比表格更有冲击力。

```html
<section style="margin-top:22px;display:flex;gap:10px;flex-wrap:wrap;">
  <section style="flex:1 1 150px;background:{{TINT}};border-radius:4px;padding:16px 14px;text-align:center;">
    <p style="margin:0 0 4px;font-size:26px;font-weight:800;color:{{PRIMARY}};line-height:1.2;font-family:{{MONO}};"><span leaf="">{{数值}}</span></p>
    <p style="margin:0;font-size:11px;color:{{SUB}};line-height:1.6;"><span leaf="">{{指标名}}</span></p>
  </section>
  <section style="flex:1 1 150px;background:{{TINT}};border-radius:4px;padding:16px 14px;text-align:center;">
    <p style="margin:0 0 4px;font-size:26px;font-weight:800;color:{{ACCENT}};line-height:1.2;font-family:{{MONO}};"><span leaf="">{{数值}}</span></p>
    <p style="margin:0;font-size:11px;color:{{SUB}};line-height:1.6;"><span leaf="">{{指标名}}</span></p>
  </section>
</section>
```

---

## G09 步骤流程 step-flow

**用途**：操作流程、安装步骤。带竖向连接线的编号卡。**禁止用 `position:absolute` 画连线**，用「编号块 + 卡片」分段实现。

```html
<section style="margin-top:16px;">
  <section style="display:flex;align-items:flex-start;gap:11px;">
    <span style="flex-shrink:0;width:24px;height:24px;line-height:24px;text-align:center;border-radius:50%;background:{{PRIMARY}};color:#FFFFFF;font-size:12px;font-weight:700;font-family:{{MONO}};"><span leaf="">1</span></span>
    <section style="flex:1;min-width:0;background:{{PAPER}};border:1px solid {{BORDER}};border-radius:4px;padding:12px 14px;">
      <p style="margin:0 0 3px;font-size:14px;font-weight:700;color:{{INK}};line-height:1.6;"><span leaf="">{{步骤标题}}</span></p>
      <p style="margin:0;font-size:13px;color:{{SUB}};line-height:1.8;word-break:break-word;"><span leaf="">{{步骤说明}}</span></p>
    </section>
  </section>
  <section style="margin-top:10px;display:flex;align-items:flex-start;gap:11px;">
    <span style="flex-shrink:0;width:24px;height:24px;line-height:24px;text-align:center;border-radius:50%;background:{{PRIMARY}};color:#FFFFFF;font-size:12px;font-weight:700;font-family:{{MONO}};"><span leaf="">2</span></span>
    <section style="flex:1;min-width:0;background:{{PAPER}};border:1px solid {{BORDER}};border-radius:4px;padding:12px 14px;">
      <p style="margin:0 0 3px;font-size:14px;font-weight:700;color:{{INK}};line-height:1.6;"><span leaf="">{{步骤标题}}</span></p>
      <p style="margin:0;font-size:13px;color:{{SUB}};line-height:1.8;word-break:break-word;"><span leaf="">{{步骤说明}}</span></p>
    </section>
  </section>
</section>
```

---

## G10 正反对比 pros-cons

**用途**：优势 / 劣势、之前 / 之后。左右两栏，窄屏自动堆叠。

```html
<section style="margin-top:22px;display:flex;gap:10px;flex-wrap:wrap;">
  <section style="flex:1 1 240px;background:#FFFFFF;border:1px solid {{BORDER}};border-top:3px solid #2E7D5B;border-radius:0 0 4px 4px;padding:14px 16px;">
    <p style="margin:0 0 8px;font-size:13px;font-weight:800;color:#2E7D5B;"><span leaf="">✓ {{正面标题}}</span></p>
    <p style="margin:0 0 5px;font-size:13px;color:{{TEXT}};line-height:1.8;"><span leaf="">{{要点}}</span></p>
    <p style="margin:0;font-size:13px;color:{{TEXT}};line-height:1.8;"><span leaf="">{{要点}}</span></p>
  </section>
  <section style="flex:1 1 240px;background:#FFFFFF;border:1px solid {{BORDER}};border-top:3px solid #C25B4E;border-radius:0 0 4px 4px;padding:14px 16px;">
    <p style="margin:0 0 8px;font-size:13px;font-weight:800;color:#C25B4E;"><span leaf="">✕ {{负面标题}}</span></p>
    <p style="margin:0 0 5px;font-size:13px;color:{{TEXT}};line-height:1.8;"><span leaf="">{{要点}}</span></p>
    <p style="margin:0;font-size:13px;color:{{TEXT}};line-height:1.8;"><span leaf="">{{要点}}</span></p>
  </section>
</section>
```

---

## G11 引用来源卡片 source-card

**用途**：引用官方文档、他文观点时给出明确出处块。

```html
<section style="margin-top:20px;background:#FFFFFF;border:1px solid {{BORDER}};border-left:3px solid {{ACCENT}};border-radius:0 4px 4px 0;padding:14px 16px;">
  <p style="margin:0 0 7px;font-size:14px;color:{{TEXT}};line-height:1.85;"><span leaf="">{{引用原文}}</span></p>
  <p style="margin:0;font-size:11px;color:{{FADE}};line-height:1.7;font-family:{{MONO}};"><span leaf="">—— {{出处}}，{{时间}}</span></p>
</section>
```

---

## G12 文末版权条 copyright-bar

**用途**：原创声明 / 转载说明。放在所有内容之后、容器闭合之前。

```html
<section style="margin-top:24px;border-top:1px dashed {{BORDER}};padding-top:14px;">
  <p style="margin:0;font-size:11px;color:{{FADE}};line-height:1.8;"><span leaf="">本文为原创内容，转载请注明出处。</span></p>
</section>
```

---

## 使用顺序建议

```
头图卡（主题 02）
  ↓
[可选] 目录导航 G03（长文）
[可选] 摘要框（主题 06）
  ↓
章节循环：主题 03 → { 主题 05 / 主题 04 → 通用 G01 G04 G05 G08 G09 / 主题 07~15 }
  ↓
主题 16 结尾总结块
  ↓
作者签名段 + 主题 17
  ↓
主题 18 结尾行动区
  ↓
[可选] G06 来源 / G07 标签 / G12 版权条
  ↓
容器闭合 → 隐藏标记
```
