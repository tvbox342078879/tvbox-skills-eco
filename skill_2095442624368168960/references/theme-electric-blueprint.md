# 主题组件库 —— 电光蓝图 electric-blueprint

> **气质**：工程蓝图 / 产品规格书。冷白纸底 + 电光蓝主色 + 青色信号线，元数据用等宽体，方括号编号、角标切角、斑马纹参数表，构成"可交付的技术文档"精度感。
> **适合**：技术教程、产品测评、架构拆解、工具上手指南、参数对比、踩坑记录。
> **不适用**：情绪向散文、生活随笔、品牌软文。

---

## 设计变量速查表

```
电光蓝（主色/强调/CTA）:   #0B63CE
电光青（信号/次强调）:     #00A9C4
深墨（标题/深底/控制台）:  #0E1726
正文色:                    #3D4A5C
次要文字:                  #6E7C90
弱化文字:                  #A3AFC0
边框 / 网格线:             #D5E1F0
纸感冷白（全局背景）:       #F5F8FC
标签浅底:                  #E4EEFB
青色浅底（提示块）:         #E6F7FA
信号黄（高亮标记底）:       #FFE08A
警示红（否定/警告）:        #E14B3B

正文字号:                   15px
正文行高:                   1.9
全局行高:                   1.75
最大宽度:                   677px
容器内边距:                 10px
区块间距:                   margin-top:22~26px
圆角:                       3px（硬朗，接近直角）
阴影:                       无。靠 1px 网格线 + 切角 + 左竖条分层

标题字体栈:  -apple-system,system-ui,'PingFang SC','Microsoft YaHei',sans-serif
正文字体栈:  -apple-system,system-ui,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif
等宽字体栈（编号/标签/代码）: ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace
```

**正文关键词下划线**（权威值，见 theme-index）：`border-bottom:2px solid #0B63CE;font-weight:600;color:#0B63CE;`

---

## 组件 01 全局容器 global-container

```html
<section style="max-width:677px;margin:0 auto;padding:10px;box-sizing:border-box;background:#F5F8FC;color:#3D4A5C;font-family:-apple-system,system-ui,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;line-height:1.75;">

  <!-- 所有组件放这里。首块为头图卡（不设 margin-top），其余自带 margin-top -->

</section>
```

---

## 组件 02 头图卡 hero-card

**用途**：开篇规格封面。全文唯一，放在最前，不设 `margin-top`。
**可替换**：`{{分类标签}}` `{{版本/日期}}` `{{主标题}}` `{{副标题}}` `{{底部摘要}}`

```html
<section style="background:#F5F8FC;border:1px solid #D5E1F0;border-radius:3px;overflow:hidden;">
  <section style="padding:24px 22px 20px;">
    <section style="display:flex;align-items:center;gap:8px;margin-bottom:16px;flex-wrap:wrap;">
      <span style="display:inline-block;background:#0B63CE;color:#FFFFFF;padding:3px 9px;border-radius:2px;font-size:10px;font-weight:700;letter-spacing:1px;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">{{分类标签}}</span></span>
      <span style="display:inline-block;background:#E4EEFB;color:#0B63CE;padding:3px 9px;border-radius:2px;font-size:10px;font-weight:700;letter-spacing:1px;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">{{版本/日期}}</span></span>
    </section>
    <p style="margin:0 0 12px;font-size:25px;font-weight:800;color:#0E1726;line-height:1.24;letter-spacing:-0.3px;">
      <span leaf="">{{主标题}}</span>
    </p>
    <p style="margin:0 0 16px;font-size:14px;color:#6E7C90;line-height:1.7;">
      <span leaf="">{{副标题}}</span>
    </p>
    <section style="height:2px;background:#0B63CE;overflow:hidden;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></section>
  </section>
  <section style="background:#0E1726;padding:14px 22px;">
    <section style="display:flex;align-items:flex-start;gap:8px;">
      <span style="flex-shrink:0;background:#00A9C4;width:3px;height:16px;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
      <p style="margin:0;font-size:12px;color:rgba(255,255,255,0.9);line-height:1.7;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">{{底部摘要}}</span></p>
    </section>
  </section>
</section>
```

---

## 组件 03 章节标题 section-title

**用途**：大章节分隔。方括号等宽编号 `[ 01 ]` + 左侧电光蓝竖条。
**可替换**：`{{编号}}` `{{标题}}` `{{英文副标题}}`

```html
<section style="margin-top:26px;">
  <section style="border-left:3px solid #0B63CE;padding-left:12px;">
    <section style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;">
      <span style="font-size:12px;font-weight:700;color:#00A9C4;letter-spacing:1px;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">[ {{编号}} ]</span></span>
      <span style="font-size:10px;font-weight:700;color:#A3AFC0;letter-spacing:2px;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">PART</span></span>
    </section>
    <p style="margin:4px 0 3px;font-size:19px;font-weight:800;color:#0E1726;line-height:1.35;letter-spacing:-0.2px;">
      <span leaf="">{{标题}}</span>
    </p>
    <p style="margin:0;font-size:10px;color:#A3AFC0;letter-spacing:1.5px;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">{{英文副标题}}</span></p>
  </section>
  <section style="margin-top:10px;height:1px;background:#D5E1F0;overflow:hidden;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></section>
</section>
```

---

## 组件 04 规格子标题 spec-subheading

**用途**：章内小节标题。青色实心小方块 + 加粗短句。
**可替换**：`{{子标题}}`

```html
<section style="margin-top:20px;display:flex;align-items:center;gap:8px;">
  <span style="flex-shrink:0;width:7px;height:7px;background:#00A9C4;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
  <p style="margin:0;font-size:16px;font-weight:700;color:#0E1726;line-height:1.5;">
    <span leaf="">{{子标题}}</span>
  </p>
</section>
```

---

## 组件 05 正文段落 body-paragraph

**用途**：默认段落。**每段主动标 1~3 处关键词**（强调 c），这是本主题的呼吸感来源。

```html
<p style="margin:16px 0 0;font-size:15px;color:#3D4A5C;line-height:1.9;letter-spacing:0.2px;">
  <span leaf="">段落正文……</span>
</p>
```

### 强调 a 加粗
```html
<span style="font-weight:700;color:#0E1726;"><span leaf="">加粗文字</span></span>
```

### 强调 b 高亮标记（信号黄底）
```html
<span style="background:#FFE08A;color:#0E1726;padding:1px 3px;border-radius:2px;font-weight:600;"><span leaf="">高亮文字</span></span>
```

### 强调 c 关键词下划线（本主题主强调）
```html
<span style="border-bottom:2px solid #0B63CE;font-weight:600;color:#0B63CE;"><span leaf="">关键词</span></span>
```

### 强调 d 行内代码
```html
<span style="background:#E4EEFB;color:#0B63CE;padding:1px 5px;border-radius:2px;font-size:13px;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">code</span></span>
```

### 强调 e 删除 / 否定
```html
<span style="color:#A3AFC0;text-decoration:line-through;"><span leaf="">过时说法</span></span>
```

---

## 组件 06 规格摘要框 lead-brief

**用途**：开篇导语 / TL;DR。浅蓝底 + 左侧竖条 + 等宽标签。
**可替换**：`{{标签}}` `{{摘要正文}}`

```html
<section style="margin-top:22px;background:#E4EEFB;border-left:3px solid #0B63CE;border-radius:0 3px 3px 0;padding:16px 18px;">
  <p style="margin:0 0 8px;font-size:10px;font-weight:700;letter-spacing:2px;color:#0B63CE;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">{{标签}}</span></p>
  <p style="margin:0;font-size:14px;color:#3D4A5C;line-height:1.85;">
    <span leaf="">{{摘要正文}}</span>
  </p>
</section>
```

---

## 组件 07 重点观点卡 key-point-card

**用途**：核心结论 / 关键判断。白底 + 网格边框 + 右上角切角块。
**可替换**：`{{卡标题}}` `{{卡正文}}`

```html
<section style="margin-top:22px;background:#FFFFFF;border:1px solid #D5E1F0;border-radius:3px;padding:18px 18px 16px;">
  <section style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
    <span style="flex-shrink:0;width:4px;height:14px;background:#0B63CE;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    <p style="margin:0;font-size:13px;font-weight:800;color:#0E1726;letter-spacing:0.5px;"><span leaf="">{{卡标题}}</span></p>
    <span style="flex:1;min-width:0;height:1px;background:#D5E1F0;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    <span style="flex-shrink:0;width:8px;height:8px;background:#00A9C4;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
  </section>
  <p style="margin:0;font-size:14px;color:#3D4A5C;line-height:1.85;">
    <span leaf="">{{卡正文}}</span>
  </p>
</section>
```

---

## 组件 08 引用块 quote-block

**用途**：引用他人原话 / 文档原文。等宽前缀 `>` + 竖线。
**可替换**：`{{引用内容}}` `{{出处}}`

```html
<section style="margin-top:22px;background:#F5F8FC;border:1px solid #D5E1F0;border-left:3px solid #00A9C4;border-radius:0 3px 3px 0;padding:14px 18px;">
  <p style="margin:0 0 8px;font-size:14px;color:#3D4A5C;line-height:1.85;font-style:normal;">
    <span leaf="">{{引用内容}}</span>
  </p>
  <p style="margin:0;font-size:11px;color:#A3AFC0;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">// {{出处}}</span></p>
</section>
```

---

## 组件 09 清单列表 bullet-list

**用途**：并列要点。方括号等宽序号 `[01]` `[02]`。
**可替换**：每行 `{{要点}}`

```html
<section style="margin-top:18px;">
  <section style="display:flex;align-items:flex-start;gap:9px;margin-top:9px;">
    <span style="flex-shrink:0;font-size:11px;font-weight:700;color:#00A9C4;padding-top:4px;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">[01]</span></span>
    <p style="margin:0;font-size:14px;color:#3D4A5C;line-height:1.85;"><span leaf="">{{要点}}</span></p>
  </section>
  <section style="display:flex;align-items:flex-start;gap:9px;margin-top:9px;">
    <span style="flex-shrink:0;font-size:11px;font-weight:700;color:#00A9C4;padding-top:4px;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">[02]</span></span>
    <p style="margin:0;font-size:14px;color:#3D4A5C;line-height:1.85;"><span leaf="">{{要点}}</span></p>
  </section>
</section>
```

---

## 组件 10 条目卡 item-list-card

**用途**：带标题 + 说明的条目（步骤、模块、特性）。等宽序号 + 网格卡。
**可替换**：`{{序号}}` `{{条目标题}}` `{{条目说明}}`

```html
<section style="margin-top:14px;background:#FFFFFF;border:1px solid #D5E1F0;border-radius:3px;padding:14px 16px;">
  <section style="display:flex;align-items:flex-start;gap:10px;">
    <span style="flex-shrink:0;background:#0B63CE;color:#FFFFFF;min-width:22px;height:22px;line-height:22px;text-align:center;border-radius:2px;font-size:11px;font-weight:700;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">{{序号}}</span></span>
    <section style="flex:1;min-width:0;">
      <p style="margin:0 0 4px;font-size:15px;font-weight:700;color:#0E1726;line-height:1.5;"><span leaf="">{{条目标题}}</span></p>
      <p style="margin:0;font-size:13px;color:#6E7C90;line-height:1.8;"><span leaf="">{{条目说明}}</span></p>
    </section>
  </section>
</section>
```

---

## 组件 11 参数对照表 compare-table

**用途**：参数对比 / 方案横评。斑马纹规格表，表头深墨底。
**禁止**：`<table>` `<thead>` `display:grid`。用 `<section>` + flex 行 + `flex:1` 单元格。

```html
<section style="margin-top:22px;border:1px solid #D5E1F0;border-radius:3px;overflow:hidden;">
  <section style="display:flex;background:#0E1726;">
    <section style="flex:1;min-width:0;padding:10px 12px;"><p style="margin:0;font-size:12px;font-weight:700;color:#FFFFFF;letter-spacing:0.5px;"><span leaf="">{{列1标题}}</span></p></section>
    <section style="flex:1;min-width:0;padding:10px 12px;border-left:1px solid rgba(255,255,255,0.15);"><p style="margin:0;font-size:12px;font-weight:700;color:#00A9C4;letter-spacing:0.5px;"><span leaf="">{{列2标题}}</span></p></section>
  </section>
  <section style="display:flex;background:#FFFFFF;border-top:1px solid #D5E1F0;">
    <section style="flex:1;min-width:0;padding:10px 12px;"><p style="margin:0;font-size:13px;color:#3D4A5C;line-height:1.7;"><span leaf="">{{行1列1}}</span></p></section>
    <section style="flex:1;min-width:0;padding:10px 12px;border-left:1px solid #D5E1F0;"><p style="margin:0;font-size:13px;color:#3D4A5C;line-height:1.7;"><span leaf="">{{行1列2}}</span></p></section>
  </section>
  <section style="display:flex;background:#F5F8FC;border-top:1px solid #D5E1F0;">
    <section style="flex:1;min-width:0;padding:10px 12px;"><p style="margin:0;font-size:13px;color:#3D4A5C;line-height:1.7;"><span leaf="">{{行2列1}}</span></p></section>
    <section style="flex:1;min-width:0;padding:10px 12px;border-left:1px solid #D5E1F0;"><p style="margin:0;font-size:13px;color:#3D4A5C;line-height:1.7;"><span leaf="">{{行2列2}}</span></p></section>
  </section>
</section>
```

**斑马纹规则**：数据行奇偶交替 `#FFFFFF` / `#F5F8FC`，表头固定 `#0E1726`，每 3~6 行数据配 1 张表。

---

## 组件 12 时间线 timeline

**用途**：版本迭代 / 事件历程。等宽时间码 + 节点圆点 + 竖线。
**可替换**：每组 `{{时间}}` `{{事件}}` `{{说明}}`

```html
<section style="margin-top:22px;">
  <section style="display:flex;align-items:flex-start;gap:12px;">
    <section style="flex-shrink:0;width:52px;text-align:right;padding-top:1px;">
      <p style="margin:0;font-size:11px;font-weight:700;color:#0B63CE;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">{{时间}}</span></p>
    </section>
    <section style="flex-shrink:0;position:relative;width:9px;padding-top:4px;">
      <span style="display:block;width:9px;height:9px;border-radius:50%;background:#0B63CE;font-size:0;line-height:0;overflow:hidden;"><span leaf="">&nbsp;</span></span>
    </section>
    <section style="flex:1;min-width:0;padding-bottom:14px;">
      <p style="margin:0 0 2px;font-size:14px;font-weight:700;color:#0E1726;line-height:1.6;"><span leaf="">{{事件}}</span></p>
      <p style="margin:0;font-size:13px;color:#6E7C90;line-height:1.8;"><span leaf="">{{说明}}</span></p>
    </section>
  </section>
  <section style="display:flex;align-items:flex-start;gap:12px;">
    <section style="flex-shrink:0;width:52px;text-align:right;padding-top:1px;">
      <p style="margin:0;font-size:11px;font-weight:700;color:#A3AFC0;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">{{时间}}</span></p>
    </section>
    <section style="flex-shrink:0;position:relative;width:9px;padding-top:4px;">
      <span style="display:block;width:9px;height:9px;border-radius:50%;background:#FFFFFF;border:2px solid #D5E1F0;box-sizing:border-box;font-size:0;line-height:0;overflow:hidden;"><span leaf="">&nbsp;</span></span>
    </section>
    <section style="flex:1;min-width:0;padding-bottom:14px;">
      <p style="margin:0 0 2px;font-size:14px;font-weight:700;color:#0E1726;line-height:1.6;"><span leaf="">{{事件}}</span></p>
      <p style="margin:0;font-size:13px;color:#6E7C90;line-height:1.8;"><span leaf="">{{说明}}</span></p>
    </section>
  </section>
</section>
```

**规则**：仅第一个节点为实心 `#0B63CE`（代表时间起点或最新节点），后续为空心边框节点。

---

## 组件 13 提示块 callout

**用途**：注意 / 坑点 / 结论提示。两种变体。
**可替换**：`{{提示标题}}` `{{提示正文}}`

### 变体 A 信息提示（青色）
```html
<section style="margin-top:20px;background:#E6F7FA;border:1px solid #B8E4EE;border-radius:3px;padding:14px 16px;">
  <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1px;color:#00A9C4;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">// {{提示标题}}</span></p>
  <p style="margin:0;font-size:14px;color:#3D4A5C;line-height:1.85;"><span leaf="">{{提示正文}}</span></p>
</section>
```

### 变体 B 警示（红）
```html
<section style="margin-top:20px;background:#FDECEA;border:1px solid #F5C6C0;border-left:3px solid #E14B3B;border-radius:0 3px 3px 0;padding:14px 16px;">
  <p style="margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:1px;color:#E14B3B;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">! {{提示标题}}</span></p>
  <p style="margin:0;font-size:14px;color:#3D4A5C;line-height:1.85;"><span leaf="">{{提示正文}}</span></p>
</section>
```

---

## 组件 14 分割线 divider

**用途**：章节之间的节奏留白。虚线网格感。

```html
<section style="margin-top:26px;height:1px;border-top:1px dashed #D5E1F0;overflow:hidden;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></section>
```

**变体**：需要更强分隔时用居中三点
```html
<section style="margin-top:26px;text-align:center;">
  <span style="display:inline-block;width:4px;height:4px;border-radius:50%;background:#D5E1F0;margin:0 4px;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
  <span style="display:inline-block;width:4px;height:4px;border-radius:50%;background:#0B63CE;margin:0 4px;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
  <span style="display:inline-block;width:4px;height:4px;border-radius:50%;background:#D5E1F0;margin:0 4px;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
</section>
```

---

## 组件 15 图片卡 image-card

**用途**：插图 / 截图。**图片必须 `max-width:100%;height:auto`**，禁止 `width:100%`。
**可替换**：`{{图片路径}}` `{{图注}}`

```html
<section style="margin-top:22px;">
  <section style="border:1px solid #D5E1F0;border-radius:3px;overflow:hidden;background:#FFFFFF;">
    <img src="{{图片路径}}" style="max-width:100%;height:auto;display:block;border:0;" />
  </section>
  <p style="margin:8px 0 0;font-size:11px;color:#A3AFC0;line-height:1.7;text-align:center;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">// {{图注}}</span></p>
</section>
```

---

## 组件 16 结尾总结块 ending-block

**用途**：全文最强锚点，收束结论。深墨控制台底 + 青色竖条。**全文最多 1 处**，只放文末。
**可替换**：`{{总结标题}}` `{{总结正文}}`

```html
<section style="margin-top:26px;background:#0E1726;border-radius:3px;padding:22px 20px;">
  <section style="display:flex;align-items:center;gap:8px;margin-bottom:12px;">
    <span style="flex-shrink:0;width:3px;height:14px;background:#00A9C4;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    <p style="margin:0;font-size:10px;font-weight:700;letter-spacing:2px;color:#00A9C4;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">{{总结标题}}</span></p>
  </section>
  <p style="margin:0;font-size:15px;color:rgba(255,255,255,0.92);line-height:1.9;font-weight:600;">
    <span leaf="">{{总结正文}}</span>
  </p>
</section>
```

---

## 组件 17 作者签名条 author-signature

**用途**：文末署名。等宽字 + 网格分隔线。
**可替换**：`{{作者名}}` `{{身份说明}}`

```html
<section style="margin-top:24px;border-top:1px solid #D5E1F0;padding-top:16px;">
  <section style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
    <span style="flex-shrink:0;background:#0B63CE;color:#FFFFFF;padding:3px 9px;border-radius:2px;font-size:10px;font-weight:700;letter-spacing:1px;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">AUTHOR</span></span>
    <p style="margin:0;font-size:14px;font-weight:700;color:#0E1726;"><span leaf="">{{作者名}}</span></p>
  </section>
  <p style="margin:8px 0 0;font-size:12px;color:#6E7C90;line-height:1.8;"><span leaf="">{{身份说明}}</span></p>
</section>
```

---

## 组件 18 结尾行动区 ending-actions

**用途**：点赞 / 在看 / 转发三连。等宽标签 + 实心按钮。
**可替换**：`{{互动引导}}`

```html
<section style="margin-top:26px;background:#FFFFFF;border:1px solid #D5E1F0;border-radius:3px;padding:22px 16px;text-align:center;">
  <p style="margin:0 0 6px;font-size:10px;font-weight:700;letter-spacing:2px;color:#A3AFC0;font-family:ui-monospace,'SFMono-Regular',Menlo,Consolas,'Courier New',monospace;"><span leaf="">// EOF</span></p>
  <p style="margin:0 0 14px;font-size:13px;font-weight:700;color:#0E1726;line-height:1.6;"><span leaf="">{{互动引导}}</span></p>
  <section style="display:flex;justify-content:center;gap:10px;flex-wrap:wrap;">
    <span style="display:inline-block;background:#0B63CE;color:#FFFFFF;padding:8px 18px;border-radius:2px;font-size:12px;font-weight:700;"><span leaf="">点赞</span></span>
    <span style="display:inline-block;background:#0E1726;color:#FFFFFF;padding:8px 18px;border-radius:2px;font-size:12px;font-weight:700;"><span leaf="">在看</span></span>
    <span style="display:inline-block;background:#00A9C4;color:#FFFFFF;padding:8px 18px;border-radius:2px;font-size:12px;font-weight:700;"><span leaf="">转发</span></span>
  </section>
</section>
```

---

## 完整文章模板骨架

```html
<section style="max-width:677px;margin:0 auto;padding:10px;box-sizing:border-box;background:#F5F8FC;color:#3D4A5C;font-family:-apple-system,system-ui,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;line-height:1.75;">

  <!-- 1. 头图卡（组件02，唯一，不设 margin-top） -->

  <!-- 2. 规格摘要框（组件06，可选；原文有独立导语/TL;DR 时使用） -->

  <!-- 3. 章节（组件03，编号 01/02/03…） -->
  <!--    章内组合：组件05 正文(+强调 a-e) / 组件04 子标题 / 组件09 清单 / 组件10 条目卡 -->
  <!--    / 组件07 重点观点卡 / 组件08 引用块 / 组件11 参数对照表 / 组件12 时间线 / 组件13 提示块 -->
  <!--    / 组件15 图片卡 / 通用库代码块 / 组件14 分割线 -->

  <!-- 4. 收束：组件16 结尾总结块（全文 ≤1 处） -->

  <!-- 5. 作者签名段（组件05 正文段落写「我是 XXX…」） + 组件17 签名条 -->

  <!-- 6. 组件18 结尾行动区 -->

</section>

<!-- 7. 隐藏标记（容器外，全文最后） -->
<p style="display:none;">
  <mp-style-type data-value="3"></mp-style-type>
</p>
```

**骨架铁律**
- 头图卡全文唯一且必须在最前；隐藏标记必须在容器闭合**之后**。
- 标题类只用组件 03（大章节）+ 组件 04（子标题）两种，保持节奏统一。
- 组件 16 结尾总结块是唯一的最强锚点，不放正文中段。
- 等宽字体只用于「编号 / 标签 / 时间码 / 图注 / 代码」，正文段落一律用无衬线，禁止通篇等宽。

---

## 文章类型 → 组件组合配方

| 文章类型 | 核心组件 | 点缀组件 |
|---|---|---|
| 技术教程 / 上手指南 | 02 + 06 + 03 + 04 + 通用代码块 + 10 条目卡 | 13、15 |
| 产品测评 / 参数横评 | 02 + 06 + 03 + 11 参数对照表 + 07 | 09、15 |
| 架构拆解 | 02 + 03 + 04 + 通用代码块 + 15 图片卡 | 08、10 |
| 踩坑记录 / 问题排查 | 02 + 06 + 03 + 13 变体B + 通用代码块 | 09、04 |
| 版本迭代 / 时间线复盘 | 02 + 06 + 12 时间线 + 16 | 05、14 |

---

## Markdown → 电光蓝图 映射规则

| Markdown 元素 | 对应组件 |
|---|---|
| `# 标题` | 组件 02 主标题（副标题走 `{{副标题}}`） |
| 开头 `> 引言` | 组件 06 规格摘要框 |
| `## 章节` | 组件 03 章节标题（编号 `[01]` `[02]`） |
| `### 子标题` | 组件 04 规格子标题 |
| 普通段落 | 组件 05 正文段落，**每段主动标 1~3 处电光蓝下划线（强调 c）** |
| `**加粗**` | 强调 a |
| `==高亮==` | 强调 b（信号黄底） |
| `<u>文字</u>` / `++文字++` | 强调 c |
| `~~删除~~` | 强调 e |
| 行内 `` `code` `` | 强调 d |
| ` ``` 代码块 ``` ` | 通用库深色代码块（底色换 `#0E1726`，强调色 `#00A9C4`） |
| 无序列表 | 组件 09 |
| 有序列表（需标题+说明） | 组件 10 条目卡 |
| 表格 | 组件 11 参数对照表（flex 仿表格，不用 table/grid） |
| `![说明](图片)` | 组件 15 图片卡 |
| `---` 分割线 | 组件 14 |
| `> 引用`（非开头） | 组件 08 引用块 |
| 阶段 / 历程 | 组件 12 时间线 |
| 提示 / 注意 | 组件 13（信息用变体A，警告用变体B） |
| 文末金句 | 组件 16 结尾总结块 |
| 文末 | 组件 17 + 组件 18 |
