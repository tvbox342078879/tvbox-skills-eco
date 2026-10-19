# 公众号排版平台规则（红线 + 自适应 + 一键复制）

> 本文件是**强制约束**。任何一次排版输出前必须遵守；脚本 `scripts/validate_gzh_html.py` 会逐条机检。

---

## 一、硬性红线（违反 = 粘贴到公众号后样式炸掉或被静默剥离）

### 1.1 禁用标签

| 禁用 | 原因 | 替代方案 |
|---|---|---|
| `<style>` | 编辑器整段剥离 | 全部样式内联到 `style=""` |
| `<script>` | 直接剥离，且可能触发安全拦截 | 无替代，公众号不需要交互脚本 |
| `<div>` | 被转成 `<section>` 或丢失 | 统一用 `<section>` |
| `<link>` | 剥离 | 不允许外链资源 |
| `<table>` `<thead>` `<tbody>` `<tr>` `<td>` `<th>` | 微信对表格支持残缺、宽度失控 | 用 `<section>` + `display:flex` 仿表格（见 common-components） |
| `<iframe>` `<video>` `<audio>` `<svg>` | 不支持 | 视频走公众号自带素材插入 |
| `<a href="外链">` | 正文超链接会被清空 | 用文字写清来源，或走「阅读原文」 |
| `<br>` 连续堆砌 | 编辑器会合并/吞掉 | 用 `margin`/`padding` 控制间距 |

### 1.2 禁用属性

- `class` / `id` —— 会被剥离或与其他文章冲突，**全部禁止**。
- `data-*` 自定义属性 —— 仅 `<mp-style-type data-value="3">` 这一个例外（见 §3.3）。
- `onclick` / `onload` 等事件属性 —— 禁止。

### 1.3 禁用 CSS

| 禁用声明 | 原因 | 替代方案 |
|---|---|---|
| `position:fixed` / `absolute` / `sticky` | 公众号内全失效 | 用 flex 顺序 + margin 布局 |
| `float:left` / `right` | 被剥离，图文会串行 | 用 flex 横排（首字下沉效果在公众号不可用，只能本地预览看） |
| `@media` 查询 | 内联 style 里不生效 | 靠百分比 + `max-width` + `flex-wrap` 自适应（见 §2） |
| `@keyframes` / `animation` | 不支持 | 静态呈现 |
| `@import` / `url()` 外链 | 剥离 | 图片必须先上传公众号素材库 |
| `display:grid` | 微信内核支持不稳定 | 用 `display:flex` |
| `var(--x)` CSS 变量 | 不支持 | 直接写死色值 |
| `!important` | 编辑器会当作异常清洗 | 靠结构和顺序保证优先级 |
| 外部字体 `font-family:自定义字体名` | 无字体文件，回退 | 只用系统字体栈 |

### 1.4 必须遵守的写法

- **所有颜色、间距、字号写死在内联 `style` 里**，不依赖继承。
- **`<img>` 必须 `max-width:100%;height:auto;display:block;border:0;`**，绝不允许 `width:100%`（会导致低分辨率屏图片被拉伸糊掉）。
- **图片先上传公众号素材库或图床**，本地相对路径在公众号里 100% 裂图。
- **中文正文使用全角标点**（`，。；：""''？！`）。半角 `,` `.` `"` 在中文语境里会被校验脚本判 WARNING。
- **段落之间不用空 `<p>` 撑间距**，统一用 `margin-top`。

---

## 二、结构铁律

### 2.1 `span leaf` 包裹规则（最重要）

微信编辑器在粘贴时会重排 DOM。**任何文字节点若不包在带 `leaf` 属性的 `<span>` 里，样式会丢失或被拆分。**

```html
<!-- 正确 -->
<p style="margin:0;font-size:15px;color:#3D4A5C;"><span leaf="">这是一段正文</span></p>

<!-- 错误：文字裸奔 -->
<p style="margin:0;font-size:15px;color:#3D4A5C;">这是一段正文</p>
```

规则细化：
1. **每一个含文字的 `<p>` / `<span>`，其直接子节点必须是 `<span leaf="">`。**
2. `<span leaf="">` 内部**不再嵌套**其他带样式的元素（强调除外，见下）。
3. **强调嵌套**：外层 `<span>` 带样式，内层 `<span leaf="">` 包文字。
   ```html
   <p style="margin:0;"><span leaf="">普通文字</span><span style="font-weight:700;color:#0E1726;"><span leaf="">加粗文字</span></span><span leaf="">继续普通</span></p>
   ```
4. **纯装饰空块**（色条、圆点、分隔线）也要放 `<span leaf="">&nbsp;</span>` 占位，否则空 `<section>` 会被编辑器折叠掉。
   ```html
   <section style="height:2px;background:#0B63CE;overflow:hidden;font-size:0;line-height:0;"><span leaf="">&nbsp;</span></section>
   ```
5. `<img>` 是空元素，**不需要** `leaf` 包裹。

### 2.2 全局容器

```html
<section style="max-width:677px;margin:0 auto;padding:10px;box-sizing:border-box;background:<纸底色>;color:<正文色>;font-family:<正文字体栈>;line-height:<全局行高>;">
  ...全部内容...
</section>
```

- `max-width:677px` 是微信正文区的安全宽度上限，超出会被压缩导致字号变小。
- `box-sizing:border-box` 必须写，否则 padding 会撑破宽度。
- 容器上写 `background`，让整篇在不同阅读背景下都保持底色一致。

### 2.3 隐藏标记（全文最后，容器外）

```html
<p style="display:none;">
  <mp-style-type data-value="3"></mp-style-type>
</p>
```

- 必须放在**全局容器闭合之后**。
- `data-value="3"` 表示「自动匹配公众号排版样式」，能让正文段落自动继承公众号的行间距设置。
- 这是唯一被允许的 `data-*` 属性。

---

## 三、多屏自适应策略（不用 `@media`）

公众号文章会在 4.7" 到 6.7" 手机、iPad、PC 浏览器三种环境里被阅读。因为 `@media` 不可用，只能靠**弹性单位 + 收缩规则**。

### 3.1 四层自适应机制

| 机制 | 写法 | 解决什么 |
|---|---|---|
| ① 流体容器 | `max-width:677px` + `margin:0 auto`，**不写死 `width`** | 小屏自动收窄，大屏居中不拉伸 |
| ② 弹性横排 | 横排一律 `display:flex` + **必须带 `flex-wrap:wrap`** | 窄屏自动换行，不溢出 |
| ③ 比例单元 | 表格/并列卡用 `flex:1`，间距用固定 px，文字占比用 flex 自适应 | 屏宽变化时列宽等比缩放 |
| ④ 图片收缩 | `max-width:100%;height:auto` | 任何屏宽下图片不溢出、不拉伸 |

### 3.2 各元素的自适应写法

**图片**
```html
<img src="..." style="max-width:100%;height:auto;display:block;border:0;" />
```

**两列横排（窄屏自动堆叠）**
```html
<section style="display:flex;gap:10px;flex-wrap:wrap;">
  <section style="flex:1 1 240px;">...</section>
  <section style="flex:1 1 240px;">...</section>
</section>
```
`flex:1 1 240px` 的含义：可伸缩、可收缩、基准 240px。屏宽不足 500px 时自动变两行。

**文字 + 固定图标**
```html
<section style="display:flex;align-items:flex-start;gap:9px;">
  <span style="flex-shrink:0;">图标/序号</span>
  <section style="flex:1;min-width:0;">文字</section>
</section>
```
`flex-shrink:0` 保住图标不被压扁；`min-width:0` 防止长英文/URL 撑破 flex 容器。

**字号**：正文 15px 是手机端最舒适值，不要用 `em`/`rem`（继承链不稳定）。小屏上微信自身会做一次缩放，所以**不要**为了"适配小屏"把字号改小。

**长文本防溢出**
```html
<p style="word-break:break-word;overflow-wrap:break-word;">
```
遇到长 URL、长英文标识符时加这两条。

### 3.3 本地预览的多屏模拟

`assets/preview-template.html` 内置了 手机 375px / 平板 768px / 桌面 100% 三档切换，**仅在本地预览页生效**（那是普通网页，可以用 `@media`）。这不影响粘贴到公众号的结果 —— 公众号里靠 §3.1 的弹性机制自然适配。

---

## 四、一键复制保真原理

### 4.1 为什么普通复制会掉样式

微信编辑器接收的是**富文本 HTML 片段**。如果你从 Markdown 编辑器、Word 或浏览器 DevTools 里复制，带走的是纯文本或简化 HTML，样式全丢。

### 4.2 本 skill 的保真链路

```
内联样式 HTML（本 skill 产出）
   ↓  wrap_preview.py 套入 preview-template.html
本地预览页（浏览器渲染真实 DOM）
   ↓  点击「一键复制」→ 用 Range + execCommand('copy') 复制富文本
剪贴板里是完整的 HTML 片段（含内联样式 + span leaf）
   ↓  在公众号编辑器 Ctrl+V
样式 1:1 还原
```

关键点：
1. **必须复制"渲染后的 DOM"**，不是复制源码文本。`document.execCommand('copy')` 会把选中区域的 HTML 结构一并写入剪贴板。
2. **选中范围必须精确落在文章根节点上**，多选中工具栏、少选中隐藏标记都会出问题。
3. **样式全部内联**是保真的前提 —— 微信编辑器会剥离 `<style>`，只认内联。
4. 复制后如果公众号里出现「外框残留」，通常是复制时多选了外层容器，回到预览页重新点一次按钮即可。

### 4.3 粘贴后的自检清单

- [ ] 标题层级、编号是否完整
- [ ] 卡片底色、边框是否都在
- [ ] 关键词下划线/高亮是否保留
- [ ] 代码块是否等宽、深色底是否保留
- [ ] 图片是否全部显示（本地路径必裂图）
- [ ] 表格是否没串行
- [ ] 文末「点赞/在看/转发」区是否完整

---

## 五、输出前必跑的校验

```bash
python scripts/validate_gzh_html.py <输出.html>
```

- `ERROR` 必须 **0** 才能交付。
- `WARNING` 逐条确认（半角标点、图片路径为本地相对路径等）。
- 未通过时**不要**手动猜测原因，按脚本给出的行号定位修复。

---

## 六、常见翻车点速查

| 现象 | 原因 | 修复 |
|---|---|---|
| 粘贴后样式全无 | 用了 `<style>` 或复制的是源码文本 | 全部内联 + 用预览页复制按钮 |
| 空色块/分隔线消失 | 空 `<section>` 被折叠 | 补 `<span leaf="">&nbsp;</span>` 占位 |
| 表格串行 | 用了 `<table>` | 改 flex 仿表格 |
| 图片糊 | 用了 `width:100%` | 改 `max-width:100%;height:auto` |
| 图片裂 | 本地相对路径 | 先上传公众号素材库/图床 |
| 首字下沉串行 | 用了 `float` | 改用大字号内联 span（无环绕效果） |
| 桌面端被拉很宽 | 容器没设 `max-width` | 加 `max-width:677px;margin:0 auto` |
| 长英文撑破卡片 | flex 子项没加 `min-width:0` | 补 `min-width:0;word-break:break-word` |
| 行距突然变大 | 隐藏标记写在容器内 | 移到容器闭合之后 |
