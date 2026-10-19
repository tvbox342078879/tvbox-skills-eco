---
name: wechat-article-archive
display_name: "公众号整站归档"
display_name_en: "WeChat Article Archive"
description_zh: "把公众号历史文章抓成全量语料，交付可点读的 HTML 阅读器、TXT 文章包与带书签的 Word 全集。"
description_en: "Archive a full WeChat official-account article history into a clickable HTML reader, a TXT package and a bookmarked Word collection."
category: "writing"
version: 1.0.0
author: "WorkBuddy"
description: "把微信公众号（或任意「只有标题清单、没有正文」的公众号）的整站历史文章抓成全量语料，并交付三种归档件：可点读的单文件 HTML 阅读器、按账号分文件夹的 TXT 文章包（可打包 zip）、以及带书签+可点击目录的 Word 全集文档。当用户提出「把某公众号/某作者的文章全部整理出来」「做成能直接读的 HTML / TXT 包 / 带书签的 Word」「公众号文章合集」等需求时启用。适用于九边、六镇、卢克文工作室这类大号归档场景。"
agent_created: true
---

# 公众号文章全量归档

把「一个账号的全部历史文章」做成三套可离线使用的交付物。核心难点有三个：**没有官方全量接口**、
**正文要自己逐篇抓**、**Word 书签 python-docx 不支持**。本技能记录踩过的坑和可直接复用的做法。

## 何时用

- 用户要「把 XX 公众号的文章全部整理成清单 / 合集 / 电子书」
- 用户要「能点进去直接读的 HTML」「按作者分文件夹的 TXT 包」「带书签的 Word」
- 用户已有一份「日期+标题」的清单，想要补正文和归档

## 总体路线

```
① 找清单源（标题+日期，最好带链接）
      ↓ 标题交叉匹配补链接
② 逐篇抓正文（微信公开页可用，无登录态）
      ↓
③ 组装统一语料 corpus.json（一个条目一篇，含 status/chars/text）
      ↓
④ 三路产出：HTML 阅读器 / TXT 包+zip / Word（书签+目录）
      ↓
⑤ 必做：用 jsdom 跑一遍 HTML，断言渲染正确
```

## ① 清单源

微信公众号**没有**对外全量文章接口。可用的公开索引：

| 源 | 取法 | 说明 |
|---|---|---|
| GitHub 开源索引库 | `api.github.com/repos/<owner>/<repo>/contents/<dir>?ref=<branch>` 列目录 → `raw.githubusercontent.com` 下载 md | 例：`sushengbuhuo/blog` 的 `docs/wechat/` 有几百个「XX公众号历史文章列表.md」，内容是 `[日期_标题](微信链接)` + `文章简介` + `文章作者`，直接正则解析 |
| 文章阅读导航页 | 搜索「XX公众号 文章阅读导航 目录」 | 常只有「序号 标题 作者 日期」，**没有链接** |
| 搜狐号镜像 | 见 ④-备用 | 能补链接，不能补正文 |

解析 md 的正则：先用 `[...s.matchAll(/^\[(.+?)\]\((https?:\/\/[^)]+)\)/gm)]` 抓条目，
再用 `文章简介:` / `文章作者:` 行补字段；文件名里的日期是**最可靠的排序键**。

**标题交叉匹配**（补链接的关键，能救回 60%+）：

```js
const norm = s => String(s||'')
  .replace(/[《》“”‘’"'\u201c\u201d·…—\-\s!！?？,，.。:：;；、()（）\[\]【】<>"'`~@#$%^&*+=|\\/]/g,'')
  .replace(/^视频版|^原创|^文字版|^转载/g,'').trim().toLowerCase();
```
去标点+全半角括号+前缀，再比对。加一层「日期窗口 ±3 天 + 字符二元组 Jaccard ≥0.55」的模糊兜底。

## ② 抓微信正文（本技能最值钱的部分）

```js
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
         + 'Chrome/120.0.0.0 Safari/537.36 MicroMessenger/8.0.40.2040(0x28002837) WeChat/8.0.40';
// https://mp.weixin.qq.com/s/XXXX  和  /s?__biz=..&mid=..&idx=..&sn=..&chksm=..  两种链接都能直接出全文（页面 ~3MB）
```

**提取正文必须用「切片」而不是「正则配对」**——微信的 `js_content` 容器 class 属性里带换行、
属性顺序也不固定，`/<div class="rich_media_content[^"]*"[^>]*id="js_content"[^>]*>([\s\S]*?)<\/div>/`
会**漏掉大批文章**（表现为「抓到了但正文为空」）：

```js
const END_MARKERS = [
  '<div class="rich_media_area_extra','<div id="js_tags','<div class="rich_media_tool',
  '<div class="rich_media_extra','<div id="js_pc_qr_code','rich_media_area_extra',
  '<div class="wx_profile_card','<script type="text/javascript" nonce','id="js_article_bottom_bar',
];
function extractText(html){
  const m = html.match(/<div[^>]*\bid="js_content"[^>]*>/);   // 注意：不要求 class 在前
  if(!m) return null;
  const rest = html.slice(m.index + m[0].length);
  let end = rest.length;
  for(const k of END_MARKERS){ const i = rest.indexOf(k); if(i>=0 && i<end) end = i; }
  let h = rest.slice(0, Math.min(end, 400000));
  h = h.replace(/<script[\s\S]*?<\/script>/gi,'').replace(/<style[\s\S]*?<\/style>/gi,'');
  h = h.replace(/<img[^>]*?data-src="([^"]*)"[^>]*>/gi,'\n[图片] $1\n');
  h = h.replace(/<\/(p|section|div|li|h1|h2|h3|h4|blockquote|tr)>/gi,'\n').replace(/<br\s*\/?>/gi,'\n');
  h = h.replace(/<[^>]+>/g,'');
  return decode(h).replace(/[ \t\u00a0]+/g,' ').replace(/\n{3,}/g,'\n\n')
          .split('\n').map(l=>l.trim()).join('\n').trim();
}
// 其他字段：og:title / var ct = "1700000000"(发稿时间戳，+8h 转本地日期) / var nickname / var author
```

**状态分类**（决定后续交付物里的提示文案）：

| 页面特征 | status | 真实含义 |
|---|---|---|
| 有正文 | `ok` | — |
| 含「该内容已被发布者删除/无法查看」 | `deleted` | **多为推广软文到期下架**，不是作者删了正文 |
| 有 `id="js_content"` 但取不到文本 | `unavailable` | 空壳页（~31KB），需登录或已失效 |
| 无 `js_content` 且页面 ~31KB | `unavailable` | 同上 |
| 有 `环境异常`/`请在微信客户端打开` | `need-login` | 触发了风控，降并发重试 |

**铁律**：同一链接反复请求返回**完全一致**的结果 → 说明是真的取不到，不要浪费重试；但可用不同
UA 各试一次排除偶发。并发 5-6、间隔 120-300ms，实测约 **7 篇/秒**，2000 篇 ≈ 4.5 分钟。
断点续跑：以「篇目文件名已存在且 >200 字节」为跳过条件。

## ③ 组装 corpus.json

一条 = `{account, date, title, author, url, status, chars, text, images, mirror?}`。
按 `(account, date, title)` 去重。**统计口径要在日志里打出来**（总条目/有正文/总字数），
后面写交付说明和向用户汇报都靠它。

## ④ 三路产出

### A. 单文件 HTML 阅读器（左目录 + 右正文）

**关键坑：file:// 下 `fetch()` 本地 JSON 会被 CORS 拦死。**
→ 数据必须写成 `.js` 文件用 `<script src>` 注入：

```js
// data/九边.js
window.WB_CORPUS=window.WB_CORPUS||{};window.WB_CORPUS["九边"]=[ /* [{d,t,a,u,s,n,c}] */ ];
```
HTML 里 `<script src="data/九边.js"></script>`，然后用 `window.WB_CORPUS` 渲染。

必备交互（用户预期）：账号标签页 + 年份吸顶分组 + 标题/正文全文搜索（命中高亮）+
「只看可直接阅读」开关 +「隐藏推广文」开关（用广告关键词正则识别）+ 上/下一篇 + 键盘 ↑↓。
数据里用**短键名**（d/t/a/u/s/n/c）能把体积压掉三成；2000 篇正文约 20MB JS，可接受。

### B. TXT 文章包

`资料/文章包/<账号>/<日期> 标题.txt`，一篇一个文件；文件名要过 `sanitize`：
`/[\\/:*?"<>|\r\n\t]/g → '_'`，截断 60 字，重名加 `(2)`。
每个账号文件夹放一份 `_目录.txt`（序号+日期+标题+[正文未获取]），根目录放 `_总说明.txt`。
正文用 `\r\n` 换行（Windows 记事本友好）。打包用 Python `zipfile`（比 Compress-Archive 更稳，
中文路径不会乱码），zip 内保留顶层目录名。

### C. Word 全集（带书签 + 可点击目录）

**python-docx 没有书签 API**，必须写裸 XML：

```python
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def add_bookmark(paragraph, name):            # name 形如 B00001，Word 书签名要求字母开头、无空格、<=40 字符
    bid = str(next(counter))
    s = OxmlElement('w:bookmarkStart'); s.set(qn('w:id'), bid); s.set(qn('w:name'), name)
    e = OxmlElement('w:bookmarkEnd');   e.set(qn('w:id'), bid)
    paragraph._p.insert(0, s); paragraph._p.append(e)

def add_internal_link(paragraph, anchor, text):   # 目录项 -> 正文书签
    h = OxmlElement('w:hyperlink'); h.set(qn('w:anchor'), anchor)
    r = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    c = OxmlElement('w:color'); c.set(qn('w:val'), '0B5CAD'); rPr.append(c)
    u = OxmlElement('w:u');     u.set(qn('w:val'), 'single'); rPr.append(u)
    r.append(rPr)
    t = OxmlElement('w:t'); t.set(qn('xml:space'), 'preserve'); t.text = text
    r.append(t); h.append(r); paragraph._p.append(h)
```

结构：封面 → 使用说明 → **目录（按年份分组、每项内部超链接）** → 正文（每篇 `add_page_break()`
+ `Heading 1` 标题 + 书签 + 元信息行 + 正文段落，首行缩进 0.74cm）。
`Heading 1` 一箭双雕：既进 Word 导航窗格，又能被目录超链接命中。
中文字体要同时设 `font.name` 和 `rFonts` 的 `w:eastAsia`，否则中文回落成宋体。

性能实测：1116 篇/355 万字 → 4.6MB/151s；1592 篇/524 万字 → 6.6MB/406s。
**超过 1000 篇务必放后台跑**，输出重定向到文件（`tail` 会缓冲，中途看不到进度）。

> 交付前提醒用户：几千页的 docx Word 打开会慢（几十秒到几分钟），这是内容体量决定的。
> 同时提供 HTML 阅读器作为轻量替代。

## ⑤ 验证（别跳过）

本机常没有 Chromium / agent-browser。用 **jsdom** 做真实 DOM 验证，比假 DOM 靠谱：

```js
import { createRequire } from 'module';
const require = createRequire('C:/Users/<user>/.workbuddy/binaries/node/workspace/'); // ESM 里 NODE_PATH 无效
const { JSDOM } = require('jsdom');
```
步骤：把 HTML 里的内联 `<script>` 抽出来用 `node --check` 验语法 → jsdom `runScripts:'outside-only'`
→ 手动 `window.eval(数据脚本)` → `window.eval(内联脚本)` → 模拟 `click`/`input` 事件 → 断言
标题、段落数、计数栏、筛选结果、错误块文案。顺手把 `window.addEventListener('error')` 挂上收集运行时错误。

## 备用：补链接（搜狐号）

唯一验证过可批量枚举的镜像接口：

```
https://v2.sohu.com/author-page-api/author-articles/pc/{userId}?pNo=N&size=20
```
`size` 上限 20，`pNo` 从 1 递增到返回空数组为止。返回里有 `id / title / link / publicTime`。
拿 `<userId>` 的办法：`https://mp.sohu.com/profile?xpt=<base64>` 页面里正则抓候选 userId，再逐个
调上面的接口看标题是不是目标账号的。

**⚠️ 搜狐只能补链接、不能补正文**（正文接口需鉴权）：
`edge.mp.sohu.com/api/article_data` → 403 AccessDenied；
`m.sohu.com/api/article_data` → 502；PC/移动页都是 JS 渲染，HTML 里没有正文。
另外：腾讯新闻 `i.news.qq.com/.../SearchMedia` 已 404、今日头条 author feed 需 `_signature`，
都走不通，别浪费时间。头条的 `https://www.163.com/dy/article/...`（网易）页面是服务端渲染的，
但需要逐篇搜索发现 URL，几百篇规模不现实。

## 边界与合规

- **如实交代覆盖率**，别把「公开可得的最大集合」说成「官方全量」。按账号/按年份列清楚
  多少篇取到正文、多少篇是什么原因没取到。
- 被删的篇目里**大量是广告推广文**，向用户说明这点能显著降低「怎么少这么多」的困惑。
- 想要 **100% 全量（含被删文章）**只有一条路：用户本人微信扫码登录公众平台 +
  第三方开源导出工具。这需要用户在场，无法代为完成 —— 主动给出这个选项，别擅自尝试。
- 所有产出都要标明**版权归原作者，仅供个人学习/离线阅读，不得商业用途或二次传播**。
- 抓取节奏控制在 ~7 篇/秒以内，不绕验证码、不伪造登录态。

## 环境坑（Windows 本机）

- 本机 bash 的 PATH 可能被 shim 破坏（`ls`/`head`/`dirname` 全 not found）。**最稳的执行通道是
  直接调 Node 绝对路径**：`C:/Users/<user>/.workbuddy/binaries/node/versions/24.14.0/node.exe script.mjs`，
  用 `fetch` 抓、`fs` 写、`console.log` 出。
- Python 侧统一用 `C:/Users/<user>/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（已装 python-docx）。
- 长任务一律 `run_in_background`，别用 `sleep` 轮询。
