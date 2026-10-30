# drpy3 实景范本对照（官方 demo-sources 拆解）

> 来源：`hjdhnx/dsplayer-plugin` → `assets/demo-sources.zip`（1183579B，与 market.json 记录一致）
> 提取日期：S196　｜　配套文档：`drpy3_源写作指南.md`（规范）、`源本地代理指南.md`（六类源契约）
> 本文定位：**指南讲"能怎么写"，本文讲"作者实际怎么写"**。四份范本构成从最简到生产的完整梯度。

---

## 0. 四份范本的梯度定位

| 范本 | 行数 | 覆盖钩子 | 学什么 |
|---|---|---|---|
| `百忙无果d3.js` | 108 | search、detail（仅 2 个） | **声明式优先**：能声明就别写代码 |
| `奇奇影视d3.js` | 371 | 全声明 + filter 巨型表 | 分类/筛选表的组织法 |
| `央视频d3.js` | 629 | 含 worker | 特殊解析（worker 剥离） |
| `PPnix-DR3.js` | 830 | 全 8 钩子 + proxy | **生产级完整形态**：代理/字幕/密钥/分片 |
| `_lib.cctv.worker.new.js` | 4289 | — | 共享库抽离（`_lib.` 前缀） |

**关键判断**：`_lib.cctv.worker.new.js` 在 `dr3/` 和 `js/` 各出现一次（同名同内容），说明 `_lib.` 前缀是跨源共享库约定，多个源 `import` 它。这是官方给的多源复用范式——**不要每个源都复制一份解析代码**。

---

## 1. 百忙无果：drpy2 214 行 → dr3 108 行

这是全库最值得学的一份，因为它示范了**减法**。

### 1.1 零包装

```js
export default {
    meta: { title, host, searchable, filterable, quickSearch, multi },
    rule: { ... },
    async search(ctx, wd, quick, pg) { ... },
    async detail(ctx, id) { ... },
}
```

注意：**没有 `defineSource`，没有 `import`，`default export` 直接就是源对象**。方法直接挂在源对象上，与 `rule` 平级。

对比我们之前理解的 `export default { meta, rule, 钩子 }`——完全一致，但这份证实了**钩子可以只有 2 个**，其余全走 `rule` 字段。

### 1.2 能声明的全声明

```js
rule: {
  searchUrl: 'https://mobileso.bz.%6d%67%74%76.com/msite/search/v2?q=**&pn=fypage&pc=10',
  detailUrl: 'https://pcweb.api.mgtv.com/episode/list?page=1&size=50&video_id=fyid',
  url: '/rider/list/pcweb/v3?platform=pcweb&channelId=fyclass&pn=fypage&pc=80...',
  filter_url: 'year={{fl.year or "all"}}&sort={{fl.sort or "all"}}',
  headers: { 'User-Agent': 'PC_UA' },
  timeout: 5000,
  class_name: '电视剧&电影&综艺&动漫&纪录片&教育&少儿',
  class_url: '2&3&1&50&51&115&10',
  filter: 'H4sIA...（base64 压缩的筛选项表）',
  一级: 'json:data.hitDocs;title;img;updateInfo||rightCorner.text;playPartId',
}
```

**四个可直接抄的点**：

1. **`%6d%67%74%76` 而非 `mgtv`** —— 域名做 URL 编码，规避壳的域名黑名单/关键字扫描。这是 `host` 写法的实战技巧。
2. **`class_name`/`class_url` 用 `&` 分隔的静态串** —— 分类不需要 `home` 钩子，直接声明即可。
3. **`filter` 是 base64（H4sI = gzip）** —— 筛选项表压缩存放，源码可读性换体积。生成方式：JSON → gzip → base64。
4. **`一级` 的 DSL 语法**：`json:路径;字段1;字段2||路径2;字段3` —— `json:` 前缀声明 JSON 响应，`;` 分字段，`||` 分段落。这是纯声明式列表解析的语法核心。

### 1.3 只有两处必须写代码

**搜索**——因为响应结构嵌套 + 需要过滤：

```js
async search(ctx, wd, quick, pg) {
    const { request } = ctx.lib.net;          // 解构惯用法
    const res = await request(
        ctx.rule.searchUrl.replaceAll('**', wd).replaceAll('fypage', pg),
        { headers: { 'User-Agent': 'MOBILE_UA', Referer: 'https://www.mgtv.com' } },
    );
    for (const data of (JSON.parse(res.content).data.contents || [])) {
        if (data.type !== 'media') continue;      // 混着非媒体项
        const item = data.data[0];
        if (item.source !== 'imgo') continue;     // 混着别家源
        list.push({ vod_id: ..., vod_name: item.title.replace(/<B>|<\/B>/g, ''), ... });
    }
    return { list };
}
```

**三个实战点**：
- `ctx.lib.net` 解构出 `request` 后，写法与 drpy2 完全一致——**迁移老源时这是省力开关**。
- `searchUrl` 里的 `**` 和 `fypage` 占位符，代码里要手动 `replaceAll` 替换。**声明式字段在钩子里不会自动注入**，这是坑。
- 搜索结果要自己脏数据过滤（type/source 双重筛），否则列表里混进一堆非影视条目。

**详情**——因为 mgtv 改版把 HTML 结构打碎了：

```js
// 2026-10-16 修复——此前「选集接口 + 详情页 HTML 抓取」两段式，
// mgtv 改版后选择器 .vt-txt/p:eq(n)/.desc 全失效：vod_name 恒空
// → 兜底把 type_name 当标题 = p:eq(0) 前 6 字
// → 每个详情页标题都显示「还没有收到任」（页面模板文案）
// 改为 episode/list 取选集 + video/info 取元信息，不再依赖 HTML 结构
```

**这是全书最有价值的反面案例**，对应 `SKILL.md` §3.13 静默缺陷的经典形态：

| 环节 | 表现 | 为什么难发现 |
|---|---|---|
| 选择器失效 | `p:eq(0)` 取不到 → 空串 | 不报错 |
| 兜底逻辑 | 空 → 退化取"第一个 p 标签" | 不报错 |
| 结果 | 页面模板文案「还没有收到任」 | 字段**有值**，非空 |
| 用户看到 | 每个详情页标题都叫「还没有收到任」 | 只有人工点进去才看得到 |

**教训**：`voc_name` 非空 ≠ 正确。兜底逻辑取到的"第一个可见文本"往往是页面 chrome（导航、模板文案、版权声明）。**兜底应该返回空，而不是猜一个。**

**修复后的正确姿势**——全 JSON，零 HTML 依赖：

```js
async detail(ctx, id) {
    const { req, all } = ctx.lib.net;
    const ep = JSON.parse((await req(epUrl, box)).content);
    // ...
    const rest = await all(                            // ← 分页并发
        Array.from({ length: total_page - 1 }, (_, i) =>
            req(epUrl.replace('page=1', 'page=' + (i + 2)), box)),
    );
}
```

`all()` 并发取剩余分页（drpy2 原是 for 串行）。**任何"N 页选集"都应该用 `all()`，串行必超时。**

---

## 2. PPnix：生产级全钩子形态

830 行，八个钩子全齐，是「真实站点完整适配」的参照。

### 2.1 钩子签名实测

```js
async init(ctx, ext)                        // 初始化，拿代理基址
async home(ctx, filter)                     // 首页（含筛选）
async homeVod(ctx)                          // 首页推荐位
async category(ctx, tid, pg, filter, extend) // 分类列表
async detail(ctx, id)                       // 详情
async play(ctx, flag, id, flags)            // 播放
async search(ctx, wd, quick)                // 搜索
async proxy(ctx, params)                    // 本地代理
```

注意 **`search(ctx, wd, quick)` 只有 3 参**，百忙无果是 4 参 `(ctx, wd, quick, pg)`。**分页参数按源而定，不是固定签名**——壳按需传，多余参数为 undefined。

### 2.2 `init` 与代理基址的兜底链

```js
async init(ctx, ext) {
    // 拿代理基址存到 this.js2Base
}
// 别处使用时必须兜底：
const js2Base = this.js2Base || ((await ctx.getProxyUrl()) + '&url=');
```

**为什么必须兜底**：`init` 不保证先跑（缓存命中、直接深链进详情、壳版本差异）。不兜底 → 字幕/播放地址拼成 `undefined...` → 播放黑屏。

这是**壳生命周期不确定**导致的必修防御，写源时凡是依赖 `this.xxx` 跨钩子传值，一律加 `|| 兜底`。

### 2.3 AES-128 密钥的坑（最有技术含量的一段）

```js
/* 端点返回 32 位 ASCII 十六进制串；实测 AES-128 密钥 = 该串前 16 个 ASCII 字符
   （原始字节），IV=0。直连 32 字符会被播放器误当密钥→解密失败/花屏。
   这里取前 16 字符，base64(flag=1) 回传。 */
if (/^[0-9a-fA-F]{32}$/.test(txt)) {
    kb = strToU8(txt.substring(0, 16));
}
return [200, 'application/octet-stream', bytesToBase64(kb), {...}, 1];
```

**三个关键知识点**：

1. **密钥是"32 个十六进制字符"的 ASCII 串，不是 16 字节二进制**。播放器要做 AES-128 需要 16 字节，所以取前 16 个字符的**原始 ASCII 字节**。
2. **`ctx.req` 会 UTF-8 化二进制**——32 字节的二进制 KEY 过 `ctx.req` 会变 24 字节（UTF-8 替换字符），AES 必挂。所以 KEY 必须**直连跳过代理**，或者在 proxy 里 `buffer: 1`。
3. **返回值第 5 个参数 `flag`**：`1` = 内容是 base64 需要壳解码，`0` = 纯文本直接输出。二进制必须 `1`。

```js
// proxy 返回签名：[status, contentType, body, headers, flag]
return [200, 'application/octet-stream', bytesToBase64(kb),
        { 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'public, max-age=86400' }, 1];
```

### 2.4 代理返回体的 ASCII 铁律

```js
if (hasNonAscii(out)) {
    return [500, 'text/plain', 'non-ascii in output', {}, 0];
}
return [200, 'application/vnd.apple.mpegurl', out, {...}, 0];
```

**flag=0 的返回体必须是纯 ASCII**。中文（subtitle、片名内嵌）会触雷 → 必须 `bytesToBase64(strToU8(vtt))` + `flag=1`。

这条铁律解释了为什么 srt→vtt 转换要绕这么大圈：

```js
/* srt → vtt，base64（flag=1）回传以绕过代理返回体全 ASCII 铁律，从而支持中文 */
```

### 2.5 m3u8 重写的三层处理

```js
/* 3a) master 预跟随 */   while (含 #EXT-X-STREAM-INF && hops < 3) 取 variant
/* 3b) KEY URI 直连跳过代理 */
/* 3c) 每行地址绝对化 + 走代理 */
```

**`hops < 3` 是防死循环保险**——master 套 master 的站存在，不设上限会跑死。

m3u8 重写核心循环：

```js
m3u8.split('\n').map(line => {
    if (line.charAt(0) === '#') {
        return line.replace(/URI="([^"]+)"/g, (_, u) => {
            const abs = absUri(u, absBase);
            if (/\/info\/m3u8\/key(\?|$)/i.test(abs))
                return 'URI="' + js2Base + encodeURIComponent(abs) + '&_type=key"';
            return 'URI="' + abs + '"';
        });
    }
    return js2Base + encodeURIComponent(absUri(line.trim(), absBase)) + '&_type=m3u8';
});
```

**注释行只改 `URI="..."` 里的地址，正文行整行转代理。** 绝对化用 `absUri(u, absBase)`，`absBase` 随 master 跟随更新。

### 2.6 字幕的演进史（v4.4.4 的教训）

```js
/* 原 3c 字幕轨注入段（master 包裹 + EXT-X-MEDIA SUBTITLES）已删除——
   裸 VTT 非法 media playlist + DsPlayer Exo/IJK 不渲染流内字幕轨，
   字幕下发改走 play 的 subs 协议 */
```

早期做法是把字幕作为 `#EXT-X-MEDIA:TYPE=SUBTITLES` 塞进 m3u8。**失败原因两条**：
1. 裸 VTT 不是合法的 media playlist（缺 `#EXTM3U`/`#EXTINF`），严格解析器直接拒。
2. DsPlayer 的 Exo/IJK 内核都不渲染流内字幕轨。

改走 `play` 返回 `subs` 数组（T4 同构）：

```js
subs = langs.map(lg => ({
    name: langNames[lg] || lg,
    url:  js2Base + encodeURIComponent(srtUrl) + '&_type=srt',
    lang: lg
}));
```

**数组顺序即优先级**，cn/zh 稳定置前：

```js
const isCn = lg => lg === 'cn' || lg === 'zh' || lg === 'zh-cn';
const langs = langsAll.filter(isCn).concat(langsAll.filter(lg => !isCn(lg)));
```

### 2.7 `play` 的返回结构

```js
return {
    parse: 0,                    // 0 = 直链，1 = 需要嗅探/解析
    urls:  ['2000Proxy', out],   // [flag名, 地址]，成对出现
    header: { 'User-Agent': UA, 'Referer': ROOT },
    subs:  subs
};
```

`urls` 是**成对数组**：`[线路名, 播放地址, 线路名2, 地址2, ...]`，不是对象列表。

### 2.8 选集 token 的编码约定

```js
/* 选集 token 形如  第N集$<m3u8url>[@langs]
   '$' 分隔 集名/地址，'#' 分隔集，'@' 之后为本源附加的字幕语言（cn,en,tw…） */
```

`vod_play_url = eps.map(it => it.t4 + '$' + url).join('#')`，`play` 里反向拆。

用 `lastIndexOf('@')` 而不是 `indexOf`——URL 里可能已含 `@`（用户信息、IPFS 路径）。**拆自定义分隔符永远用 `lastIndexOf`。**

同时 `ensureDecoded(id)` 包裹——id 可能被 URL 编码过（壳多段传递会编码），进 `play` 前先试解码。

---

## 3. 奇奇影视：筛选项表的组织法

371 行，绝大部分是数据表，代码很少。

```js
const CATEGORIES = [{ type_id: '2', type_name: '电视剧' }, ...9 个];
const CLASS_TAGS  = { '2': ['古装','都市',...16 个], '35': [...], ... };
const AREA_TAGS   = { '2': ['内地','香港',...], ... };
const YEAR_OPTIONS = [2026, 2025, ... 2004];   // 23 年，倒序
const SORT_OPTIONS = [{ n: '最新', v: '24' }, { n: '最热', v: '4' }, { n: '好评', v: '11' }];
```

**组织要点**：
- 分类表用**数组**（顺序即展示顺序），子筛选用 **key=type_id 的对象**（O(1) 定位）。
- 年份从当前年倒序，直接硬编码数组——比 `new Date().getFullYear()` 减循环更好读、更好改。
- 排序项的 `n`/`v` 短键——这是 filter DSL 里 `{{fl.sort}}` 的取用约定。

对照百忙无果的 base64 filter：**筛选项少就用 JS 对象直写，多了才压 base64。**

---

## 4. danmu-api 服务端包（顺带拆解）

同一个 `dsplayer-plugin` 还带 `danmu-plugin.zip`（6639589B），是 LogVar 弹幕 API 的**官方精简部署包**。这解答了之前"我们找的那堆野部署哪个好"的问题——**可以直接自己跑一份。**

### 4.1 包结构

```
danmu-api/
├── package.json          # type: module，start: node danmu_api/server.js
├── config/.env.example   # 27762 字节，配置项极多
├── danmu_api/
│   ├── server.js         # 21650B，HTTP 入口
│   ├── worker.js         # 32817B，核心工作进程
│   ├── esm-shim.cjs      # 13876B，ESM/CJS 桥
│   ├── apis/             # dandan-api / env-api / favorite-api / local-danmu-api / system-api / forward-trace-api
│   ├── sources/          # 25 个源：bilibili / iqiyi / youku / tencent / mango / migu / sohu / leshi / xigua / renren / hanjutv / maiduidui / aiyifan / hongguo / bahamut / dandan / douban / tmdb / animeko / kan360 / vod / local / custom / other / registry
│   └── utils/
└── node_modules/         # 依赖已打包，开箱即跑
```

依赖 7 个：`@dan-uni/dan-any`、`brotli`、`dotenv`、`fast-xml-parser`、`https-proxy-agent`、`node-fetch`、`opencc-js`。

### 4.2 API 路由（dandanplay v2 协议）

| 路由 | 用途 |
|---|---|
| `GET /api/v2/search/anime?keyword=` | 按番剧名搜索 |
| `POST /api/v2/match` | 按文件名匹配（播放器用这个） |
| `GET /api/v2/search/episodes` | 按集号搜索 |
| `GET /api/v2/bangumi/:animeId` | 番剧详情 |
| `GET /api/v2/comment/:commentId` | 取弹幕（核心） |
| `GET /api/v2/comment?url=` / `extcomment` | 按 URL 取 |
| `GET /api/v2/segmentcomment` | 分段取 |
| `GET /api/v2/favorite/list` | 收藏列表 |

**命名分组说明**：注释里的 `// Extracted function for GET /api/v2/xxx` 说明这是从 worker 里抽出来的纯函数，路由分发在 `server.js`。

### 4.3 关键配置项（决定源质量）

```bash
TOKEN=87654321                    # 默认 token！野部署不改就是这个
DANMU_API_PORT=9321               # 默认 9321 —— 和 DsPlayer 本地网关同端口
SOURCE_ORDER=douban,360,renren,hanjutv
VOD_SERVERS=金蝉@https://zy.jinchancaiji.com,789@https://www.caiji.cyou,听风@https://gctf.tfdh.top
VOD_RETURN_MODE=fastest           # fastest=只返回最快站点，all=全返回
OTHER_SERVER=https://api.danmu.icu
DANMU_LIMIT=0                     # 0 = 不限条数
DANMU_PUSH_URL=http://127.0.0.1:9978/action?do=refresh&type=danmaku&path=
DANMU_OUTPUT_FORMAT=json
```

**三个直接可用的判据**：

1. **默认 token 是 `87654321`** —— 我们之前找到的那 10 条活源，凡是不用 token 就能访问的，基本就是没改默认值。反过来：**带非默认 token 的源说明部署者认真在维护**。
2. **`SOURCE_ORDER` 默认只有 4 源**（douban,360,renren,hanjutv）—— 想要 B站/腾讯/优酷/爱奇艺/芒果弹幕，必须手动加。**野部署大多没加，这就是"某些番搜不到弹幕"的根因**。
3. **`VOD_SERVERS` 默认是 3 个采集站** —— 这是用弹幕 API 顺带做影视搜索。我们之前测活只测 `/api/v2/search/anime`，**没测 VOD 部分**，活源里可能有能当采集站用的。

### 4.4 `EPISODE_TITLE_FILTER` 正则巨兽

`.env.example` 第 154 行是一条**约 6KB 的单行正则**，用于过滤"特别企划/vlog/reaction/纯享/加更/预告/花絮/直拍/特辑/彩蛋/专访/幕后/PV/片花/精华/看点/速看/回顾/影评/解说/吐槽/盘点"等非正片。

写法极其讲究：**大量负向先行断言 `(?!(...))` 防止误杀**——比如"花絮"要排除"死亡花絮/恐怖花絮/灵异花絮/怪谈花絮"（这些是正片标题），"Plus" 要排除 "Love Plus/Disney Plus/One Plus/C Note/S1 Plus"。

**这份正则可以直接抄进我们自己的清洗逻辑**，比手写关键词表强得多。它解决的是"搜索番剧时结果混入大量花絮/预告，导致匹配到错误集号"的问题。

---

## 5. 与指南的偏差核查

对照 `drpy3_源写作指南.md` 逐条验证，**发现两处指南未覆盖**：

| 项 | 指南记载 | 实景 | 处置 |
|---|---|---|---|
| 钩子数量 | 11 个 | 实测 8 个常用（init/home/homeVod/category/detail/play/search/proxy），其余 3 个（如 `action`/`live`）在 demos 中未出现 | 已在指南标注"8 个高频" |
| `search` 签名 | 未明确参数个数 | 3 参 / 4 参并存 | 本文 §2.1 已记 |

其余全部吻合：`export default` 直接是对象、`ctx.lib.net` 解构、`ctx.rule.xxx` 读声明字段、`ctx.getProxyUrl()`、proxy 五元组返回、`all()` 并发、`_lib.` 共享库。

**没有任何"只有 demos 会、指南没写"的暗语法**——这反过来验证了指南的完整性。

---

## 6. 可立即复用的七条

1. **`%6d%67%74%76` 域名编码** 绕过壳的域名扫描——高危站点必用。
2. **`class_name`/`class_url` 静态声明**，分类别写 `home` 钩子。
3. **`一级` DSL**：`json:路径;字段;字段||路径2;字段` —— 纯 JSON 列表零代码。
4. **`all()` 并发分页**，任何 N 页选集禁止串行。
5. **`this.xxx` 一律加 `|| 兜底`** —— `init` 不保证先跑。
6. **兜底别猜值**——取不到就返回空串，不要取"页面第一个 p 标签"（百忙无果的「还没有收到任」）。
7. **AES KEY 取前 16 字符的 ASCII 字节**，且必须绕开 `ctx.req` 的 UTF-8 化（`buffer:1` 或直连）。

---

## 7. 诚实边界

- 本文基于 **静态阅读**，四份源**未在真实 DsPlayer 壳内跑通**（沙箱无 GUI）。
- `%6d%67%74%76` 的实际绕过效果未验证——只确认源码这么写。
- ~~`_lib.cctv.worker.new.js`（4289 行）只看了存在性和文件头，**未逐行读**~~（S196 已补读，见 §8）。
- ~~`央视频d3.js` / `七星猫短剧d3.js` / `喜福短剧d3.js` 三份未细读~~（S196 已补读，见 §8）。
- danmu-api 包**未实际启动**~~，路由表由源码注释与 `apis/` 文件名推断~~——S196 已实机跑通，
  另见 `/root/.codebuddy/skills/probe-hub/docs/外置_danmu-api实机部署验证.md`（技能内冗余副本，原 `/workspace/1021/` 临时目录随沙箱漂移、随时可能失效）。

---

## 8. 补读完成（S196 收尾）：剩下三份源 + _lib

### 8.1 七星猫短剧d3.js —— 多平台合流的样板

**一份源装三个平台**（七星/七猫/好看），各自全链路。可抄的点：

- **加密双引擎兼容**：`typeof CryptoJS !== 'undefined'` 走 CryptoJS，
  否则 `globalThis._nodeCrypto` 走 Node `createCipheriv('aes-128-ecb')`，再不行 `require('crypto')`。
  **写跨宿主源时，加密要三层兜底**——你不知道宿主注入了哪个。
- **自造字符映射表**做参数编码（`QM_CHAR_MAP`，'+'→'P'、'/'→'X'…）：某些平台的
  "加密"其实只是换表 base64，反向映射即可解。
- 硬编码 AES key 直接进源码（`B@ecf920Od8A4df7`）——这类短剧站密钥本来就是公开的。

### 8.2 喜福短剧d3.js —— 零依赖纯 JS 实现的样板

最干净的一份：**自包含 base64/UTF-8/HMAC-SHA1 纯 JS 实现，不依赖运行时 crypto**。
可抄的点：

- **服务端不支持筛选时，拉全量 + 本地过滤 + 本地分页**，配 10 分钟内存缓存
  （`CACHE_TTL=600`）——别硬造请求参数。
- **站方没有搜索端点**（list 接口的 search 参数无效）→ 本地全量匹配标题/别名/主演。
  说明：**先验证 API 行为再写源**，别信文档。
- 播放走阿里云 VOD 的 `playAuth`(base64 JSON 凭证) → HMAC-SHA1 签名 → 真 mp4 直链，
  清晰度按 OD>FHD>HD>SD 择优。这是**正版云点播的标准接法**。

### 8.3 央视频d3.js —— wasm 托管与性能铁律（4289 行 _lib 的用法全在这）

- `_lib.cctv.worker.new.js` 的接入方式：整个文件是一个 **wasm 工厂**，
  末行 `globalThis.CNTVModuleFactory = CNTVModule`；源里经
  `ctx.lib.wasm.load` 托管加载（垫片环境、就绪等待、按路径缓存）。
  **`_lib.` 共享库 ≠ 被多个源 import，也可能是 wasm 模块载体。**
- **性能铁律一（真机实测注释）**：TS 段 ~2.7MB → base64，纯 JS 循环每段 370-520ms，
  占代理链路 15%；宿主 C 层 `toBase64`（quickjs-android）同段 **2-10ms**。
  三级回退：`bytes.toBase64 → Buffer → 纯 JS`。**写重活先查宿主 C 能力。**
- **性能铁律二（花屏实锤）**：纯 JS base64 必须**分块收集 + join**（旧写法逐字符
  `out +=` 是 O(n²)，真机 30s+ 超时），且 **CHUNK 必须是 3 的倍数**（0x7FFE）——
  块边界落在非 3 字节组上会产生独立编码的 "xxx="，与下一块合并解码时错位，
  82 个边界 = +82 字节 → **TS 全错位花屏**（2026-09-28 实锤）。

### 8.4 三份的定位补全

| 范本 | 一句话 |
|---|---|
| 七星猫 | 多平台合流 + 加密三层兜底 |
| 喜福 | 零依赖纯 JS + 本地过滤分页 + 正版云点播接法 |
| 央视频 | wasm 托管 + 宿主 C 能力探测 + base64 性能/正确性双铁律 |
