# 公众号编辑器 CSS 支持对照表

微信公众号图文编辑器的正文是一个 `contenteditable` 富文本区，粘贴时会经过一层**白名单清洗**：
只保留白名单内的标签与**内联 style** 中的部分属性，其余一律丢弃。
本 skill 的 `wechat_convert.py` 已实现自动兼容转换，下表供人工排查时参考。

> ⚠️ **最致命的一条（90%「没样式」都栽在这）**：
> 公众号新版编辑器底层是 **ProseMirror**，**根本不认 `<div>`**！
> 一旦内容里有 `<div style="background:...;border-radius:...;box-shadow:...">`，
> 整个 div 连同它的背景/圆角/边框/阴影会被**整段吞掉**，只剩光秃秃的文字。
> **解法：所有块级容器必须用 `<section>` 而不是 `<div>`**（秀米/135 导出的代码全是 section）。
> 本 skill 的转换器已自动把 `<div>` 全部 rename 成 `<section>`，无需手动处理。

## 一、会被保留的内联属性（放心用）
| 属性 | 备注 |
|------|------|
| `color` | 支持 16 进制 / rgb / 颜色名 |
| `font-size` / `font-weight` / `font-style` / `font-family` | 字号、加粗、斜体、字体 |
| `text-align` / `text-indent` / `text-decoration` | 对齐、首行缩进、下划线 |
| `line-height` / `letter-spacing` / `word-spacing` | 行距、字距 |
| `margin*` / `padding*` | 外/内边距 |
| `border*` / `border-radius` / `border-collapse` | 边框、圆角、表格合并 |
| `background-color` | **仅实色**；渐变/图片背景会被丢 |
| `width` / `height` / `max-width` / `min-width` | 宽度（建议用 % 或 max-width:100%） |
| `vertical-align` / `white-space` / `word-break` | 对齐、换行控制 |
| `display`（block/inline-block/none 等） | flex/grid 会被丢 |
| `float` / `clear` | 图片绕排可用 |
| `opacity`（部分场景） | 灰度透明 |

## 二、会被丢弃 / 不支持（必须避开或用替代写法）
| 属性 / 写法 | 后果 | 替代方案 |
|------|------|------|
| `background:linear-gradient(...)` | **整段背景变透明**，肉眼可见崩坏 | 退化为 `background-color:#xxxxxx` 实色 |
| `background:url(...)` / `background-image` | 背景图丢失 | 用实色，或把图作为 `<img>` 内嵌 |
| `box-shadow` / `text-shadow` | 阴影消失 | 公众号卡片效果靠边框+底色实现 |
| `transform` / `transition` / `animation` | 完全无效 | 静态呈现，去掉 |
| `position` / `top/left/...` / `z-index` | 失效 | 用 margin/normal flow 布局 |
| `display:flex` / `grid` | 被重置为默认 | 改用 block / 表格 / float 实现分栏 |
| `filter` / `backdrop-filter` | 失效 | 去掉 |
| `<style>` 标签 / `class` 选择器 | **类样式不生效**（除非先内联） | 用本 skill 转换器提前 `premailer` 内联 |
| 外部 CSS / `<link>` | 不加载 | 全部改为内联 |

## 三、标签白名单（建议只用这些）
`p` `br` `span` `strong`/`b` `em`/`i` `u` `img` `a` `h1`-`h6`
`blockquote` `ul`/`ol`/`li` `table`/`thead`/`tbody`/`tr`/`td`/`th`
`hr` `section` `font`

> 结论：公众号排版的本质就是 **「内联样式 + 实色背景 + 静态布局」**。
> 凡是用 `<style>`+class 或渐变/阴影/flex 的漂亮页面，都必须先经转换再粘贴。
> 🔑 **头号坑一句话记住**：块级容器用 `<section>` 别用 `<div>`——你手写 HTML 时也请直接用 `<section>`，
> 别留 `<div>` 等转换器擦屁股（自动 rename 只是兜底，不是借口）。

## 四、正确的粘贴姿势（关键！）
1. 在**浏览器**里打开 `_wechat.html`（不要从代码编辑器/记事本复制！）。
2. `Ctrl/Cmd + A` 全选 → `Ctrl/Cmd + C` 复制。
3. 切到公众号编辑器，`Ctrl/Cmd + V` 粘贴。
4. 若仍想直接灌 HTML，可装「壹伴 / 新媒体管家 / 公众号助手」等浏览器插件，
   用它们的「导入 HTML」功能粘代码，效果等价且更稳。
