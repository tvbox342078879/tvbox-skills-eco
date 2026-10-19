# 主题组件库 —— 靛蓝档案 indigo-archive

> **气质**：档案馆 / 学术卷宗。靛蓝主调 + 金色点睛，标题用衬线体（Georgia / 宋体），编号、卷宗条、引用块构成"内部资料"的克制严肃感。
> **适合**：深度分析、行业报告、观点长文、案例复盘、系统性说明文档。
> **不适用**：轻松生活随笔、强促销的内容。

---

## 设计变量速查表

```
靛蓝主色（强调/深底/CTA）:   #2B3A67
深字 / 标题色:              #18203F
正文色:                     #464C66
次要文字:                   #6B7290
弱化文字:                   #9BA2B8
边框 / 分隔线:              #C8D0E4
纸感浅底（全局背景）:        #F4F6FB
标签浅底:                   #E6EAF5
强调金（下划线/重点/规则线）:  #B8860B
档案红（警示/否定）:         #A03A3A

正文字号:                   15px
正文行高:                   1.9
全局行高:                   1.75
最大宽度:                   677px
容器内边距:                 10px
区块间距:                   margin-top:22~26px
圆角:                       4px（方正，不用大圆角）
阴影:                       无。靠边框 + 色块 + 左竖条分层

标题字体栈:  Georgia,'Songti SC','Noto Serif CJK SC','Source Han Serif SC',serif
正文字体栈:  -apple-system,system-ui,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif
```

**正文关键词下划线**（权威值，见 theme-index）：`border-bottom:2px solid #B8860B;font-weight:600;color:#18203F;`

---

## 组件 01 全局容器 global-container

```html
<section style="max-width:677px;margin:0 auto;padding:10px;box-sizing:border-box;background:#F4F6FB;color:#464C66;font-family:-apple-system,system-ui,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;line-height:1.75;">

  <!-- 所有组件放这里。首块为头图卡（不设 margin-top），其余自带 margin-top -->

</section>
```

---

## 组件 02 头图卡 hero-card

**用途**：开篇档案封面。全文唯一，放在最前，不设 `margin-top`。
**可替换**：`{{档案编号}}` `{{日期}}` `{{主标题}}` `{{副标题}}` `{{底部摘要}}`

```html
<section style="background:#F4F6FB;border:1px solid #C8D0E4;border-radius:4px;overflow:hidden;">
  <section style="padding:26px 22px 20px;">
    <section style="display:flex;align-items:center;gap:10px;margin-bottom:20px;">
      <span style="width:6px;height:6px;background:#2B3A67;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
      <span style="font-size:10px;font-weight:700;letter-spacing:3px;color:#6B7290;"><span leaf="">{{档案编号}}</span></span>
      <span style="flex:1;min-width:0;height:1px;background:#C8D0E4;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
      <span style="font-size:10px;color:#9BA2B8;font-variant-numeric:tabular-nums;"><span leaf="">{{日期}}</span></span>
    </section>
    <p style="margin:0 0 12px;font-size:25px;font-weight:800;color:#18203F;line-height:1.22;font-family:Georgia,'Songti SC',serif;">
      <span leaf="">{{主标题}}</span>
    </p>
    <p style="margin:0 0 15px;font-size:14px;color:#6B7290;line-height:1.7;">
      <span leaf="">{{副标题}}</span>
    </p>
    <span style="width:40px;height:3px;background:#B8860B;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
  </section>
  <section style="background:#2B3A67;padding:12px 22px;">
    <p style="margin:0;font-size:12px;color:rgba(255,255,255,0.92);line-height:1.65;"><span leaf="">{{底部摘要}}</span></p>
  </section>
</section>
```

---

## 组件 03 章节标题 section-title

**用途**：大章节分隔，编号 01/02/03…，衬线数字 + PART 标签。
**可替换**：`{{编号}}` `{{标题}}` `{{英文副标题}}`

```html
<section style="margin-top:26px;">
  <section style="display:flex;align-items:flex-start;gap:12px;">
    <section style="flex-shrink:0;text-align:center;padding-top:2px;">
      <p style="margin:0;font-size:22px;font-weight:800;color:#18203F;line-height:1;font-family:Georgia,serif;"><span leaf="">{{编号}}</span></p>
      <p style="margin:3px 0 0;font-size:7px;letter-spacing:2px;color:#9BA2B8;font-weight:700;"><span leaf="">PART</span></p>
    </section>
    <span style="width:1px;align-self:stretch;background:#C8D0E4;flex-shrink:0;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    <section style="min-width:0;">
      <p style="margin:0 0 2px;font-size:17px;font-weight:800;color:#18203F;font-family:Georgia,'Songti SC',serif;"><span leaf="">{{标题}}</span></p>
      <p style="margin:0;font-size:10px;letter-spacing:1.5px;color:#6B7290;font-weight:600;"><span leaf="">{{英文副标题}}</span></p>
    </section>
  </section>
</section>
```

---

## 组件 04 卷宗子标题 archive-subheading

**用途**：章节内的二级小标题，方形编号徽章 + 衬线标题。
**可替换**：`{{序号}}` `{{子标题}}`

```html
<section style="margin-top:24px;">
  <section style="display:flex;align-items:center;gap:10px;">
    <span style="display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;flex-shrink:0;background:#2B3A67;color:#FFFFFF;font-size:12px;font-weight:800;border-radius:2px;font-family:Georgia,serif;"><span leaf="">{{序号}}</span></span>
    <span style="font-size:15px;font-weight:700;color:#18203F;min-width:0;font-family:Georgia,'Songti SC',serif;"><span leaf="">{{子标题}}</span></span>
  </section>
</section>
```

---

## 组件 05 正文段落 body-paragraph

**可替换**：`{{正文内容}}`

```html
<section style="margin-top:22px;">
  <p style="margin:0;font-size:15px;line-height:1.9;text-align:justify;color:#464C66;">
    <span leaf="">{{正文内容}}</span>
  </p>
</section>
```

### 文字强调（段落内使用，一段不叠加超过 2 种）

**a. 强调加粗**
```html
<strong style="color:#18203F;"><span leaf="">{{文字}}</span></strong>
```

**b. 浅底高亮**
```html
<span style="background:#E6EAF5;padding:1px 5px;border-radius:3px;font-weight:600;color:#18203F;"><span leaf="">{{文字}}</span></span>
```

**c. 金色下划线**（正文关键词默认标记，权威值）
```html
<span style="border-bottom:2px solid #B8860B;font-weight:600;color:#18203F;"><span leaf="">{{文字}}</span></span>
```

**d. 行内代码**
```html
<span style="background:#E6EAF5;color:#2B3A67;padding:2px 6px;border-radius:3px;font-family:ui-monospace,Menlo,Monaco,Consolas,monospace;font-size:13px;border:1px solid #C8D0E4;"><span leaf="">{{代码}}</span></span>
```

**e. 删除线旧词**
```html
<span style="color:#9BA2B8;text-decoration:line-through;"><span leaf="">{{旧文字}}</span></span>
```

---

## 组件 06 卷宗编者按 editors-note

**用途**：章节前提醒 / 开篇引言。靛蓝标题条 + 浅底正文。
**可替换**：`{{标签}}` `{{小字}}` `{{正文}}`

```html
<section style="margin-top:24px;">
  <section style="border:1px solid #C8D0E4;border-radius:4px;overflow:hidden;">
    <section style="background:#2B3A67;padding:9px 16px;display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;">
      <p style="margin:0;font-size:10px;font-weight:700;letter-spacing:2px;color:#FFFFFF;"><span leaf="">{{标签}}</span></p>
      <span style="font-size:10px;color:rgba(255,255,255,0.6);"><span leaf="">{{小字}}</span></span>
    </section>
    <section style="padding:16px 18px;background:#E6EAF5;">
      <p style="margin:0;font-size:14px;line-height:1.9;color:#464C66;text-align:justify;"><span leaf="">{{正文}}</span></p>
    </section>
  </section>
</section>
```

---

## 组件 07 重点观点卡 key-point-card

**用途**：段落级重点，金色左竖条 + 浅底。
**可替换**：`{{重点观点}}` `{{补充说明}}`

```html
<section style="margin-top:24px;">
  <section style="background:#F4F6FB;border:1px solid #C8D0E4;border-left:4px solid #B8860B;border-radius:0 4px 4px 0;padding:16px 18px;">
    <p style="margin:0;font-size:14px;line-height:1.85;color:#464C66;text-align:justify;">
      <strong style="color:#18203F;"><span leaf="">{{重点观点}}</span></strong><span leaf="">&nbsp;{{补充说明}}</span>
    </p>
  </section>
</section>
```

---

## 组件 08 档案引用块 archive-quote

**用途**：引文 / 金句，衬线斜体 + 左竖线 + 出处。
**可替换**：`{{引文}}` `{{出处}}`

```html
<section style="margin-top:24px;">
  <section style="padding:4px 0 4px 16px;border-left:3px solid #C8D0E4;">
    <p style="margin:0;font-size:15px;line-height:1.9;color:#6B7290;font-family:Georgia,'Songti SC',serif;">
      <span leaf="">「{{引文}}」</span>
    </p>
    <p style="margin:8px 0 0;font-size:12px;color:#9BA2B8;"><span leaf="">—— {{出处}}</span></p>
  </section>
</section>
```

---

## 组件 09 无序列表 bullet-list

**可替换**：`{{列表项}}`（可重复）

```html
<section style="margin-top:22px;">
  <ul style="margin:0;padding-left:20px;line-height:1.85;list-style-position:outside;">
    <li style="margin-bottom:8px;font-size:15px;color:#464C66;">
      <section><span leaf="">{{列表项}}</span></section>
    </li>
  </ul>
</section>
```

---

## 组件 10 条目列表卡 item-list-card

**用途**：标题 + 说明两层结构的并列条目。
**可替换**：`{{条目标题}}` `{{条目说明}}`

```html
<section style="margin-top:24px;">
  <section style="margin-bottom:14px;">
    <p style="margin:0 0 6px;">
      <span style="display:inline-block;font-size:13px;font-weight:700;color:#18203F;background:#E6EAF5;border:1px solid #C8D0E4;border-radius:2px;padding:3px 10px;">
        <span leaf="">&nbsp;{{条目标题}}&nbsp;</span>
      </span>
    </p>
    <p style="margin:0;font-size:14px;line-height:1.8;color:#464C66;text-align:justify;"><span leaf="">{{条目说明}}</span></p>
  </section>
</section>
```

---

## 组件 11 对照表 compare-table

**用途**：维度对照。表头靛蓝实底，数据行浅底。行数按需重复。
**可替换**：`{{维度列名}}` `{{方案A}}` `{{方案B}}` / 每行 `{{行标题}}` `{{A值}}` `{{B值}}`

```html
<section style="margin-top:24px;">
  <section style="border:1px solid #C8D0E4;border-radius:4px;overflow:hidden;">
    <section style="display:flex;background:#2B3A67;">
      <section style="width:34%;padding:10px 12px;font-size:12px;font-weight:800;color:#FFFFFF;"><span leaf="">{{维度列名}}</span></section>
      <section style="width:33%;padding:10px 12px;font-size:12px;font-weight:800;color:#FFFFFF;border-left:1px solid rgba(255,255,255,0.18);"><span leaf="">{{方案A}}</span></section>
      <section style="width:33%;padding:10px 12px;font-size:12px;font-weight:800;color:#FFFFFF;border-left:1px solid rgba(255,255,255,0.18);"><span leaf="">{{方案B}}</span></section>
    </section>
    <section style="display:flex;border-top:1px solid #C8D0E4;background:#F4F6FB;">
      <section style="width:34%;padding:10px 12px;font-size:13px;color:#464C66;"><span leaf="">{{行标题}}</span></section>
      <section style="width:33%;padding:10px 12px;font-size:13px;color:#18203F;border-left:1px solid #C8D0E4;"><span leaf="">{{A值}}</span></section>
      <section style="width:33%;padding:10px 12px;font-size:13px;color:#18203F;border-left:1px solid #C8D0E4;"><span leaf="">{{B值}}</span></section>
    </section>
  </section>
</section>
```

> 最后一行不带下边框时，把该行 `section` 的 `border-top` 保留即可（表格靠行间 `border-top` 分隔）。中间行与末尾行样式相同。

---

## 组件 12 时间线 timeline

**用途**：阶段 / 历程 / 步骤演进。左竖线 + 圆点。
**可替换**：`{{阶段}}` `{{内容}}`（可重复）

```html
<section style="margin-top:24px;">
  <section style="display:flex;">
    <section style="display:flex;flex-direction:column;align-items:center;margin-right:14px;flex-shrink:0;">
      <span style="width:10px;height:10px;border-radius:50%;background:#2B3A67;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
      <span style="width:1px;background:#C8D0E4;flex:1;min-width:0;min-height:40px;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    </section>
    <section style="flex:1;min-width:0;padding-bottom:14px;">
      <p style="margin:0 0 4px;font-size:13px;font-weight:800;color:#18203F;"><span leaf="">{{阶段}}</span></p>
      <p style="margin:0;font-size:14px;line-height:1.8;color:#464C66;text-align:justify;"><span leaf="">{{内容}}</span></p>
    </section>
  </section>
</section>
```

> 作为最后一条时，把竖线 `span` 的 `flex:1;min-width:0;min-height:40px` 删掉，避免尾部多出一截空线。

---

## 组件 13 提示块 callout

**用途**：提示 / 注意 / 重点 / 旁注。金色胶囊标签 + 浅底。
**可替换**：`{{类型标签}}` `{{内容}}`；警示场景把标签底色换成 `#A03A3A`

```html
<section style="margin-top:24px;">
  <section style="background:#F4F6FB;border:1px solid #C8D0E4;border-radius:4px;padding:14px 16px;">
    <p style="margin:0 0 7px;">
      <span style="display:inline-block;background:#B8860B;color:#FFFFFF;font-size:10px;font-weight:700;padding:2px 9px;border-radius:2px;letter-spacing:1px;"><span leaf="">{{类型标签}}</span></span>
    </p>
    <p style="margin:0;font-size:14px;line-height:1.8;color:#464C66;"><span leaf="">{{内容}}</span></p>
  </section>
</section>
```

---

## 组件 14 分割线 divider

```html
<section style="margin-top:26px;">
  <section style="display:flex;align-items:center;justify-content:center;gap:8px;">
    <span style="width:56px;height:1px;background:#C8D0E4;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    <span style="width:5px;height:5px;border-radius:50%;background:#B8860B;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    <span style="width:56px;height:1px;background:#C8D0E4;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
  </section>
</section>
```

---

## 组件 15 图片卡 image-card

**可替换**：`{{图片URL}}` `{{图片说明}}`（无说明时删 `<figcaption>`）

```html
<section style="margin-top:24px;">
  <figure style="margin:0;">
    <section style="border:1px solid #C8D0E4;border-radius:4px;overflow:hidden;padding:4px;background:#F4F6FB;">
      <span leaf=""><img src="{{图片URL}}" alt="{{图片说明}}" style="max-width:100%;height:auto;display:block;margin:0 auto;"></span>
    </section>
    <figcaption style="font-size:12px;color:#6B7290;text-align:center;margin-top:8px;line-height:1.6;"><span leaf="">{{图片说明}}</span></figcaption>
  </figure>
</section>
```

---

## 组件 16 结尾总结块 ending-block

**用途**：全文最强收束锚点，靛蓝实底 + 衬线金句。全文 ≤1 处。
**可替换**：`{{结尾金句}}`

```html
<section style="margin-top:26px;">
  <section style="background:#2B3A67;border-radius:4px;padding:22px 20px;">
    <p style="margin:0 0 9px;font-size:10px;letter-spacing:3px;color:rgba(255,255,255,0.55);font-weight:700;"><span leaf="">CLOSING</span></p>
    <p style="margin:0;font-size:16px;line-height:1.85;color:#FFFFFF;font-weight:700;font-family:Georgia,'Songti SC',serif;"><span leaf="">{{结尾金句}}</span></p>
  </section>
</section>
```

---

## 组件 17 作者签名条 author-signature

**可替换**：`{{头像首字}}` `{{签名文字}}`

```html
<section style="margin-top:24px;">
  <section style="display:flex;align-items:center;gap:10px;">
    <span style="display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;flex-shrink:0;border-radius:50%;background:#2B3A67;color:#FFFFFF;font-size:12px;font-weight:800;"><span leaf="">{{头像首字}}</span></span>
    <span style="font-size:13px;color:#464C66;font-weight:600;min-width:0;"><span leaf="">{{签名文字}}</span></span>
  </section>
</section>
```

---

## 组件 18 结尾行动区 ending-actions

**用途**：点赞 / 在看 / 转发三连。本主题用**文字胶囊按钮**（非图标块），贴合档案的克制感。
**可替换**：`{{互动引导}}`

```html
<section style="margin-top:26px;">
  <section style="border:1px solid #C8D0E4;border-radius:4px;padding:22px 16px;text-align:center;background:#F4F6FB;">
    <p style="margin:0 0 14px;font-size:13px;font-weight:700;color:#18203F;line-height:1.6;"><span leaf="">{{互动引导}}</span></p>
    <section style="display:flex;justify-content:center;gap:14px;flex-wrap:wrap;">
      <span style="display:inline-block;background:#2B3A67;color:#FFFFFF;padding:8px 16px;border-radius:3px;font-size:12px;font-weight:700;"><span leaf="">点赞</span></span>
      <span style="display:inline-block;background:#2B3A67;color:#FFFFFF;padding:8px 16px;border-radius:3px;font-size:12px;font-weight:700;"><span leaf="">在看</span></span>
      <span style="display:inline-block;background:#B8860B;color:#FFFFFF;padding:8px 16px;border-radius:3px;font-size:12px;font-weight:700;"><span leaf="">转发</span></span>
    </section>
    <p style="margin:14px 0 0;font-size:10px;letter-spacing:2px;color:#9BA2B8;"><span leaf="">THANKS FOR READING</span></p>
  </section>
</section>
```

---

## 完整文章模板骨架

```html
<section style="max-width:677px;margin:0 auto;padding:10px;box-sizing:border-box;background:#F4F6FB;color:#464C66;font-family:-apple-system,system-ui,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;line-height:1.75;">

  <!-- 1. 头图卡（组件02，唯一，不设 margin-top） -->

  <!-- 2. 卷宗编者按（组件06，可选；原文有独立引言时使用） -->

  <!-- 3. 章节（组件03，编号 01/02/03…） -->
  <!--    章内组合：组件05 正文(+强调 a-e) / 组件04 卷宗子标题 / 组件09 列表 / 组件10 条目卡 -->
  <!--    / 组件07 重点观点卡 / 组件08 引用块 / 组件11 对照表 / 组件12 时间线 / 组件13 提示块 -->
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

---

## 文章类型 → 组件组合配方

| 文章类型 | 核心组件 | 点缀组件 |
|---|---|---|
| 深度分析 / 观点 | 02 + 03 + 05 + 07 + 16 | 06、08、14 |
| 行业报告 / 数据复盘 | 02 + 03 + 11 对照表 + 12 时间线 + 16 | 05、13 |
| 案例复盘 | 02 + 03 + 12 时间线 + 10 条目卡 + 16 | 08、15 |
| 系统性说明文档 | 02 + 03 + 04 子标题 + 05 + 10 条目卡 | 13、11、通用代码块 |
| 教程 / 操作指南 | 02 + 03 + 04 + 通用代码块 + 09 列表 + 16 | 13、15 |

---

## Markdown → 靛蓝档案 映射规则

| Markdown 元素 | 对应组件 |
|---|---|
| `# 标题` | 组件 02 主标题（副标题走 `{{副标题}}`） |
| 开头 `> 引言` | 组件 06 卷宗编者按 |
| `## 章节` | 组件 03 章节标题（编号 01/02/03…） |
| `### 子标题` | 组件 04 卷宗子标题 |
| 普通段落 | 组件 05 正文段落，**每段主动标 1~3 处金色下划线（强调 c）** |
| `**加粗**` | 强调 a |
| `==高亮==` | 强调 b |
| `<u>文字</u>` / `++文字++` | 强调 c |
| `~~删除~~` | 强调 e |
| 行内 `` `code` `` | 强调 d |
| ` ``` 代码块 ``` ` | 通用库深色代码块（顶部条换 `#2B3A67`） |
| 无序列表 | 组件 09 |
| 有序列表（需标题+说明） | 组件 10 条目卡 |
| 表格 | 组件 11 对照表（flex 仿表格，不用 table/grid） |
| `![说明](图片)` | 组件 15 图片卡 |
| `---` 分割线 | 组件 14 |
| `> 引用`（非开头） | 组件 08 档案引用块 |
| 阶段 / 历程 | 组件 12 时间线 |
| 提示 / 注意 | 组件 13 |
| 文末金句 | 组件 16 结尾总结块 |
| 文末 | 组件 17 + 组件 18 |
