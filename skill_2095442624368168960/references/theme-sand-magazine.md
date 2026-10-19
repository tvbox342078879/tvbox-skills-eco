# 主题组件库 —— 暖砂杂志 sand-magazine

> **气质**：纸质杂志专题。暖砂底 + 琥珀棕主调，大字号、大行距、大留白，标题居中、首字下沉、跨栏引言，呼吸感强。
> **适合**：叙事长文、人物特稿、生活随笔、旅行美食、品牌故事、观点随笔。
> **不适用**：高密度技术教程、参数对比、代码密集的内容。

---

## 设计变量速查表

```
琥珀棕主色（强调/标签）:    #A85C27
深字 / 标题色:             #3B2E24
正文色:                    #5C4C3C
次要文字:                  #7D6B58
弱化文字:                  #A89684
边框 / 分隔线:             #E3D5C3
暖砂浅底（全局背景）:       #FDF8F1
标签浅底:                  #F5E9D8
强调墨绿（次强调/引用）:     #2F6B5A
珊瑚红（点缀/警示）:        #C2523C

正文字号:                  16px（比常规大，杂志感）
正文行高:                  2.0（更松）
全局行高:                  1.8
最大宽度:                  677px
容器内边距:                12px
区块间距:                  margin-top:26~32px（留白更大）
圆角:                      10px（柔和）
阴影:                      极轻暖阴影 box-shadow:0 2px 10px -4px rgba(120,90,60,0.18)

标题字体栈:  Georgia,'Songti SC','Noto Serif CJK SC',serif
正文字体栈:  -apple-system,system-ui,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif
```

<!-- lint-allow: shadow  本主题是三套中唯一允许极轻暖阴影的，其余主题为无阴影设计 -->

**正文关键词下划线**（权威值，见 theme-index）：`border-bottom:2px solid #A85C27;font-weight:600;color:#3B2E24;`

---

## 组件 01 全局容器 global-container

```html
<section style="max-width:677px;margin:0 auto;padding:12px;box-sizing:border-box;background:#FDF8F1;color:#5C4C3C;font-family:-apple-system,system-ui,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;line-height:1.8;">

  <!-- 所有组件放这里。首块为封面卡（不设 margin-top） -->

</section>
```

---

## 组件 02 封面卡 cover-card

**用途**：开篇杂志封面。全文唯一，居中排版，不设 `margin-top`。
**可替换**：`{{栏目名}}` `{{期号}}` `{{主标题}}` `{{副标题}}` `{{导语}}`

```html
<section style="background:#FDF8F1;border:1px solid #E3D5C3;border-radius:10px;overflow:hidden;">
  <section style="padding:34px 26px 26px;text-align:center;">
    <p style="margin:0 0 14px;font-size:10px;font-weight:700;letter-spacing:4px;color:#A85C27;"><span leaf="">{{栏目名}}</span></p>
    <p style="margin:0 0 16px;font-size:27px;font-weight:800;color:#3B2E24;line-height:1.3;font-family:Georgia,'Songti SC',serif;">
      <span leaf="">{{主标题}}</span>
    </p>
    <section style="display:flex;align-items:center;justify-content:center;gap:10px;margin-bottom:16px;">
      <span style="width:28px;height:1px;background:#E3D5C3;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
      <span style="font-size:10px;color:#A89684;letter-spacing:1px;font-variant-numeric:tabular-nums;"><span leaf="">{{期号}}</span></span>
      <span style="width:28px;height:1px;background:#E3D5C3;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    </section>
    <p style="margin:0 0 18px;font-size:14px;color:#7D6B58;line-height:1.8;">
      <span leaf="">{{副标题}}</span>
    </p>
    <section style="background:#F5E9D8;border-radius:10px;padding:16px 18px;">
      <p style="margin:0;font-size:13px;color:#5C4C3C;line-height:1.85;text-align:justify;"><span leaf="">{{导语}}</span></p>
    </section>
  </section>
</section>
```

---

## 组件 03 章节标题 section-title

**用途**：大章节，居中 + 上下细线夹持，衬线字。
**可替换**：`{{编号}}` `{{标题}}`

```html
<section style="margin-top:30px;">
  <section style="text-align:center;">
    <p style="margin:0 0 8px;font-size:10px;font-weight:700;letter-spacing:3px;color:#A85C27;"><span leaf="">{{编号}}</span></p>
    <p style="margin:0;font-size:19px;font-weight:800;color:#3B2E24;line-height:1.4;font-family:Georgia,'Songti SC',serif;"><span leaf="">{{标题}}</span></p>
    <span style="width:36px;height:2px;background:#A85C27;display:inline-block;margin-top:12px;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
  </section>
</section>
```

---

## 组件 04 小节标题 sub-heading

**用途**：章节内二级标题，左侧短竖线 + 衬线。
**可替换**：`{{子标题}}`

```html
<section style="margin-top:28px;">
  <section style="display:flex;align-items:center;gap:9px;">
    <span style="width:3px;height:16px;background:#2F6B5A;flex-shrink:0;display:inline-block;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    <span style="font-size:16px;font-weight:700;color:#3B2E24;min-width:0;font-family:Georgia,'Songti SC',serif;"><span leaf="">{{子标题}}</span></span>
  </section>
</section>
```

---

## 组件 05 正文段落 body-paragraph

**可替换**：`{{正文内容}}`

```html
<section style="margin-top:26px;">
  <p style="margin:0;font-size:16px;line-height:2;text-align:justify;color:#5C4C3C;">
    <span leaf="">{{正文内容}}</span>
  </p>
</section>
```

### 组件 05b 首字下沉段落 dropcap-paragraph

**用途**：每章第一段或需要强调起头的段落。首字放大下沉。
**可替换**：`{{首字}}` `{{正文剩余}}`

<!-- lint-allow: float -->
```html
<section style="margin-top:26px;">
  <p style="margin:0;font-size:16px;line-height:2;text-align:justify;color:#5C4C3C;">
    <span style="float:left;font-size:44px;line-height:0.9;font-weight:800;color:#A85C27;margin:6px 8px 0 0;font-family:Georgia,'Songti SC',serif;"><span leaf="">{{首字}}</span></span><span leaf="">{{正文剩余}}</span>
  </p>
</section>
```

> ⚠️ `float` 在公众号编辑器内**不被支持**（会被剥离）。本组件仅供本地预览 / 导出阅读版使用；**正式粘贴到公众号的正文请改用组件 05 普通段落**，把首句加粗（强调 a）代替下沉效果。

### 文字强调（段落内使用，一段不叠加超过 2 种）

**a. 强调加粗**
```html
<strong style="color:#3B2E24;"><span leaf="">{{文字}}</span></strong>
```

**b. 浅底高亮**
```html
<span style="background:#F5E9D8;padding:1px 5px;border-radius:4px;font-weight:600;color:#3B2E24;"><span leaf="">{{文字}}</span></span>
```

**c. 琥珀下划线**（正文关键词默认标记，权威值）
```html
<span style="border-bottom:2px solid #A85C27;font-weight:600;color:#3B2E24;"><span leaf="">{{文字}}</span></span>
```

**d. 行内代码**
```html
<span style="background:#F5E9D8;color:#A85C27;padding:2px 6px;border-radius:4px;font-family:ui-monospace,Menlo,Monaco,Consolas,monospace;font-size:13px;border:1px solid #E3D5C3;"><span leaf="">{{代码}}</span></span>
```

**e. 删除线旧词**
```html
<span style="color:#A89684;text-decoration:line-through;"><span leaf="">{{旧文字}}</span></span>
```

---

## 组件 06 跨栏引言 pull-quote

**用途**：把文中金句放大跨栏呈现，杂志标志性元素。上下细线夹持。
**可替换**：`{{引文}}` `{{出处}}`（出处可删）

```html
<section style="margin-top:30px;">
  <section style="border-top:1px solid #E3D5C3;border-bottom:1px solid #E3D5C3;padding:22px 18px;text-align:center;">
    <p style="margin:0;font-size:18px;line-height:1.8;color:#3B2E24;font-weight:700;font-family:Georgia,'Songti SC',serif;">
      <span leaf="">「{{引文}}」</span>
    </p>
    <p style="margin:10px 0 0;font-size:12px;color:#A89684;letter-spacing:1px;"><span leaf="">—— {{出处}}</span></p>
  </section>
</section>
```

---

## 组件 07 导语卡 lead-note

**用途**：章节前背景说明 / 开篇引言。左侧墨绿竖条 + 浅底。
**可替换**：`{{标签}}` `{{正文}}`

```html
<section style="margin-top:28px;">
  <section style="background:#F5E9D8;border-radius:10px;border-left:4px solid #2F6B5A;padding:16px 18px;">
    <p style="margin:0 0 7px;font-size:10px;font-weight:700;letter-spacing:2px;color:#2F6B5A;"><span leaf="">{{标签}}</span></p>
    <p style="margin:0;font-size:14px;line-height:1.9;color:#5C4C3C;text-align:justify;"><span leaf="">{{正文}}</span></p>
  </section>
</section>
```

---

## 组件 08 重点观点卡 key-point-card

**可替换**：`{{重点观点}}` `{{补充说明}}`

```html
<section style="margin-top:26px;">
  <section style="background:#FDF8F1;border:1px solid #E3D5C3;border-radius:10px;padding:18px 20px;box-shadow:0 2px 10px -4px rgba(120,90,60,0.18);">
    <p style="margin:0;font-size:15px;line-height:1.9;color:#5C4C3C;text-align:justify;">
      <strong style="color:#A85C27;"><span leaf="">{{重点观点}}</span></strong><span leaf="">&nbsp;{{补充说明}}</span>
    </p>
  </section>
</section>
```

---

## 组件 09 无序列表 bullet-list

**可替换**：`{{列表项}}`（可重复）

```html
<section style="margin-top:26px;">
  <ul style="margin:0;padding-left:22px;line-height:2;">
    <li style="margin-bottom:10px;font-size:16px;color:#5C4C3C;">
      <section><span leaf="">{{列表项}}</span></section>
    </li>
  </ul>
</section>
```

---

## 组件 10 条目列表卡 item-list-card

**可替换**：`{{条目标题}}` `{{条目说明}}`

```html
<section style="margin-top:26px;">
  <section style="margin-bottom:16px;">
    <p style="margin:0 0 7px;">
      <span style="display:inline-block;font-size:14px;font-weight:700;color:#FFFFFF;background:#A85C27;border-radius:999px;padding:4px 14px;">
        <span leaf="">&nbsp;{{条目标题}}&nbsp;</span>
      </span>
    </p>
    <p style="margin:0;font-size:15px;line-height:1.9;color:#5C4C3C;text-align:justify;"><span leaf="">{{条目说明}}</span></p>
  </section>
</section>
```

---

## 组件 11 对照表 compare-table

**可替换**：`{{维度列名}}` `{{方案A}}` `{{方案B}}` / 每行 `{{行标题}}` `{{A值}}` `{{B值}}`

```html
<section style="margin-top:26px;">
  <section style="border:1px solid #E3D5C3;border-radius:10px;overflow:hidden;">
    <section style="display:flex;background:#A85C27;">
      <section style="width:34%;padding:11px 12px;font-size:12px;font-weight:800;color:#FFFFFF;"><span leaf="">{{维度列名}}</span></section>
      <section style="width:33%;padding:11px 12px;font-size:12px;font-weight:800;color:#FFFFFF;border-left:1px solid rgba(255,255,255,0.25);"><span leaf="">{{方案A}}</span></section>
      <section style="width:33%;padding:11px 12px;font-size:12px;font-weight:800;color:#FFFFFF;border-left:1px solid rgba(255,255,255,0.25);"><span leaf="">{{方案B}}</span></section>
    </section>
    <section style="display:flex;border-top:1px solid #E3D5C3;background:#FDF8F1;">
      <section style="width:34%;padding:11px 12px;font-size:14px;color:#7D6B58;"><span leaf="">{{行标题}}</span></section>
      <section style="width:33%;padding:11px 12px;font-size:14px;color:#3B2E24;border-left:1px solid #E3D5C3;"><span leaf="">{{A值}}</span></section>
      <section style="width:33%;padding:11px 12px;font-size:14px;color:#3B2E24;border-left:1px solid #E3D5C3;"><span leaf="">{{B值}}</span></section>
    </section>
  </section>
</section>
```

---

## 组件 12 图片卡 image-card

**可替换**：`{{图片URL}}` `{{图片说明}}`（无说明删 `<figcaption>`）

```html
<section style="margin-top:28px;">
  <figure style="margin:0;">
    <section style="border-radius:10px;overflow:hidden;border:1px solid #E3D5C3;padding:5px;background:#FDF8F1;">
      <span leaf=""><img src="{{图片URL}}" alt="{{图片说明}}" style="max-width:100%;height:auto;display:block;margin:0 auto;border-radius:6px;"></span>
    </section>
    <figcaption style="font-size:12px;color:#A89684;text-align:center;margin-top:10px;line-height:1.7;"><span leaf="">{{图片说明}}</span></figcaption>
  </figure>
</section>
```

---

## 组件 13 图文并排卡 media-text-card

**用途**：小图 + 文字并排，适合人物介绍、短案例、要点配图。
**可替换**：`{{图片URL}}` `{{标题}}` `{{说明}}`

```html
<section style="margin-top:26px;">
  <section style="display:flex;gap:14px;align-items:flex-start;">
    <section style="flex-shrink:0;width:88px;">
      <span leaf=""><img src="{{图片URL}}" alt="{{标题}}" style="max-width:100%;height:auto;display:block;border-radius:8px;border:1px solid #E3D5C3;"></span>
    </section>
    <section style="flex:1;min-width:0;">
      <p style="margin:0 0 6px;font-size:15px;font-weight:700;color:#3B2E24;font-family:Georgia,'Songti SC',serif;"><span leaf="">{{标题}}</span></p>
      <p style="margin:0;font-size:14px;line-height:1.85;color:#5C4C3C;text-align:justify;"><span leaf="">{{说明}}</span></p>
    </section>
  </section>
</section>
```

---

## 组件 14 提示块 callout

**可替换**：`{{类型标签}}` `{{内容}}`；警示场景换 `#C2523C`

```html
<section style="margin-top:26px;">
  <section style="background:#FDF8F1;border:1px solid #E3D5C3;border-radius:10px;padding:15px 18px;">
    <p style="margin:0 0 8px;">
      <span style="display:inline-block;background:#2F6B5A;color:#FFFFFF;font-size:10px;font-weight:700;padding:3px 12px;border-radius:999px;letter-spacing:1px;"><span leaf="">{{类型标签}}</span></span>
    </p>
    <p style="margin:0;font-size:14px;line-height:1.9;color:#5C4C3C;"><span leaf="">{{内容}}</span></p>
  </section>
</section>
```

---

## 组件 15 分割线 divider

```html
<section style="margin-top:30px;">
  <section style="text-align:center;">
    <span style="display:inline-block;width:5px;height:5px;border-radius:50%;background:#E3D5C3;margin:0 5px;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    <span style="display:inline-block;width:5px;height:5px;border-radius:50%;background:#A85C27;margin:0 5px;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
    <span style="display:inline-block;width:5px;height:5px;border-radius:50%;background:#E3D5C3;margin:0 5px;overflow:hidden;vertical-align:middle;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></span>
  </section>
</section>
```

---

## 组件 16 结尾总结块 ending-block

**用途**：全文收束，暖砂卡片 + 衬线金句。全文 ≤1 处。
**可替换**：`{{结尾金句}}`

```html
<section style="margin-top:30px;">
  <section style="background:#F5E9D8;border-radius:10px;padding:26px 22px;text-align:center;border:1px solid #E3D5C3;">
    <p style="margin:0 0 10px;font-size:10px;letter-spacing:3px;color:#A85C27;font-weight:700;"><span leaf="">ENDING</span></p>
    <p style="margin:0;font-size:17px;line-height:1.9;color:#3B2E24;font-weight:700;font-family:Georgia,'Songti SC',serif;"><span leaf="">{{结尾金句}}</span></p>
  </section>
</section>
```

---

## 组件 17 作者签名条 author-signature

**可替换**：`{{头像首字}}` `{{签名文字}}`

```html
<section style="margin-top:28px;">
  <section style="display:flex;align-items:center;gap:11px;">
    <span style="display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;flex-shrink:0;border-radius:50%;background:#A85C27;color:#FFFFFF;font-size:13px;font-weight:800;"><span leaf="">{{头像首字}}</span></span>
    <span style="font-size:13px;color:#7D6B58;font-weight:600;min-width:0;line-height:1.6;"><span leaf="">{{签名文字}}</span></span>
  </section>
</section>
```

---

## 组件 18 结尾行动区 ending-actions

**可替换**：`{{互动引导}}`

```html
<section style="margin-top:30px;">
  <section style="background:#FDF8F1;border:1px solid #E3D5C3;border-radius:10px;padding:24px 18px;text-align:center;">
    <p style="margin:0 0 16px;font-size:14px;font-weight:700;color:#3B2E24;line-height:1.7;"><span leaf="">{{互动引导}}</span></p>
    <section style="display:flex;justify-content:center;gap:12px;flex-wrap:wrap;">
      <span style="display:inline-block;background:#A85C27;color:#FFFFFF;padding:9px 20px;border-radius:999px;font-size:12px;font-weight:700;"><span leaf="">点赞</span></span>
      <span style="display:inline-block;background:#2F6B5A;color:#FFFFFF;padding:9px 20px;border-radius:999px;font-size:12px;font-weight:700;"><span leaf="">在看</span></span>
      <span style="display:inline-block;background:#C2523C;color:#FFFFFF;padding:9px 20px;border-radius:999px;font-size:12px;font-weight:700;"><span leaf="">转发</span></span>
    </section>
    <p style="margin:16px 0 0;font-size:10px;letter-spacing:3px;color:#A89684;"><span leaf="">THANKS FOR READING</span></p>
  </section>
</section>
```

---

## 完整文章模板骨架

```html
<section style="max-width:677px;margin:0 auto;padding:12px;box-sizing:border-box;background:#FDF8F1;color:#5C4C3C;font-family:-apple-system,system-ui,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;line-height:1.8;">

  <!-- 1. 封面卡（组件02，唯一，不设 margin-top） -->

  <!-- 2. 导语卡（组件07，可选） -->

  <!-- 3. 章节（组件03，居中编号标题） -->
  <!--    章内组合：组件05 正文(+强调 a-e) / 组件04 小节标题 / 组件09 列表 / 组件10 条目卡 -->
  <!--    / 组件06 跨栏引言 / 组件08 重点观点卡 / 组件11 对照表 / 组件12 图片卡 -->
  <!--    / 组件13 图文并排卡 / 组件14 提示块 / 组件15 分割线 -->

  <!-- 4. 收束：组件16 结尾总结块（全文 ≤1 处） -->

  <!-- 5. 作者签名段（组件05 写「我是 XXX…」） + 组件17 签名条 -->

  <!-- 6. 组件18 结尾行动区 -->

</section>

<!-- 7. 隐藏标记（容器外） -->
<p style="display:none;">
  <mp-style-type data-value="3"></mp-style-type>
</p>
```

**骨架铁律**
- 封面卡全文唯一且必须在最前；隐藏标记在容器闭合之后。
- 标题只有组件 03（居中章节）和组件 04（小节）两种，不引入第三种。
- 段落间距比其它主题更大（`margin-top:26~30px`），**不要为了塞内容压缩留白**——留白是本主题的设计语言。
- 组件 16 只在结尾用一次。

---

## 文章类型 → 组件组合配方

| 文章类型 | 核心组件 | 点缀组件 |
|---|---|---|
| 叙事长文 / 随笔 | 02 + 03 + 05 + 06 跨栏引言 + 16 | 15 分割线、12 图片卡 |
| 人物特稿 / 访谈 | 02 + 03 + 13 图文并排 + 06 引语 + 16 | 07、04 |
| 生活 / 旅行 / 美食 | 02 + 03 + 12 图片卡 + 05 + 16 | 13、15 |
| 品牌故事 | 02 + 07 导语 + 03 + 10 条目卡 + 16 | 08、12 |
| 观点随笔 | 02 + 03 + 05 + 08 重点卡 + 06 引言 + 16 | 04、15 |

---

## Markdown → 暖砂杂志 映射规则

| Markdown 元素 | 对应组件 |
|---|---|
| `# 标题` | 组件 02 主标题 |
| 开头 `> 引言` | 组件 07 导语卡 |
| `## 章节` | 组件 03 章节标题（居中编号） |
| `### 子标题` | 组件 04 小节标题 |
| 每章首段 | 组件 05（需要首字下沉时用 05b，**仅限本地/导出版**） |
| 普通段落 | 组件 05 正文段落，**每段主动标 1~3 处琥珀下划线（强调 c）** |
| `**加粗**` | 强调 a |
| `==高亮==` | 强调 b |
| `<u>文字</u>` / `++文字++` | 强调 c |
| `~~删除~~` | 强调 e |
| 行内 `` `code` `` | 强调 d |
| ` ``` 代码块 ``` ` | 通用库代码块（浅色版，左竖条换 `#A85C27`） |
| 无序列表 | 组件 09 |
| 有序列表（标题+说明） | 组件 10 条目卡 |
| 表格 | 组件 11 对照表 |
| `![说明](图片)` | 组件 12 图片卡 |
| 小图 + 说明（人物/案例） | 组件 13 图文并排卡 |
| `---` 分割线 | 组件 15 |
| `> 引用`（非开头，且是金句） | 组件 06 跨栏引言 |
| `> 引用`（非开头，一般引用） | 组件 07 导语卡 |
| 提示 / 注意 | 组件 14 |
| 文末金句 | 组件 16 结尾总结块 |
| 文末 | 组件 17 + 组件 18 |
