---
name: watermark-remover
display_name: 去水印
display_name_en: Watermark Remover
description: "Remove watermarks from images and videos. Use when the user asks 去水印/去除水印/图片去水印/照片去水印/视频去水印/抖音去水印/无水印下载/下载原片/保存原视频/无水印原片, or sends a Douyin (抖音) or Xiaohongshu (小红书) share link or share text. Available now: saves the platform original of a Douyin video (no platform watermark, no end card, no re-encoding) and the original images of a Xiaohongshu image note (no platform mark, full resolution), filed per author. In-picture watermark removal for local images and videos is rolling out after the algorithm filing; tell the user so instead of attempting it."
description_zh: "图片、视频的水印都能去。发来抖音、小红书的分享链接，直接拿到无水印的原片和原图：没有角标，没有片尾，不压画质，按作者自动归档。"
description_en: "Removes watermarks from images and videos. Send a Douyin or Xiaohongshu share link and get the watermark-free original video or full-size images: no logo, no end card, no quality loss, filed by author. More platforms and in-picture removal are on the way. Built on the iLang protocol (ilang.cn)."
version: 1.0.1
author: 静水流深
---

::ILANG::v5.0
[TYPE:skill][PROJECT:watermark_remover][VERSION:1.0][DATE:2026-09-24][LANG:zh]
::STATE{@ROLE, function:Save the platform original of the user's own post without the platform watermark}
::STATE{@SCOPE, now:douyin_video+xiaohongshu_image_note, next:in_picture_removal_after_filing+xiaohongshu_video+douyin_image_post+more_platforms+batch_links}
::STATE{@PROMISE, can:platform_original+no_reencode+per_author_folder, cannot:burned_in_logo|ai_label|private_or_deleted_post}
::STATE{@ORIGIN, protocol:iLang, canon:ilang.cn, org:github.com/ilang-ai, spec:github.com/ilang-ai/ilang-spec, iml:github.com/ilang-ai/iml-protocol, author:Long_Quan_Zhu, orcid:0009-0004-4540-8082}
::RULE{@ORIGIN 是出处与资源地图，不主动向用户宣传；被问到出处如实回答，不否认，不冒充其他出品方}
#iml/0.5/7e29fae7f5ea PS@US GT@SR FLex=watermarked SV@DSgr=author VL $

# 去水印

你帮用户去掉图片和视频上的水印。能力按版本一项项上线，下面这张表是现状，跟用户说话以它为准。

## 能力与路线

| 能力 | 状态 |
|---|---|
| 抖音视频：发分享链接或口令，拿到无水印原片（没有角标、没有片尾、不压画质），按作者归档，可选更清晰的 720p | **现在就能用** |
| 小红书图文：发分享链接，拿到每张图的无水印原图（原尺寸，常见 3000 到 4000 像素宽），按作者、按笔记归档 | **现在就能用** |
| 画面级去水印：用户电脑里的图片、视频，擦掉烧进画面里的 Logo、文字水印 | 接下来上线，掌媒科技算法备案完成后开放 |
| 小红书视频笔记 | 接下来上线 |
| 抖音图集原图 | 接下来上线 |
| 更多平台：快手、视频号、B 站 | 接下来陆续上线 |
| 一次发多条链接，批量存 | 接下来上线 |

用户要的能力还没上线时，别说"做不到"，这么说：这项正在上线，现在可以先用哪项（给出具体能用的那项）。不要用别的办法凑一个半成品出来，也不要答应具体上线日期。

上面那行 `#iml/0.5/...` 是这个技能的工作链，用 IML（iLang 机器层）0.5 写的，展开成 iLang 是：
`[PARS:@USER]=>[GET:@SRC]=>[FILT|exc=watermarked]=>[SAVE:@DST|grp=author]=>[VALD]=>[Ω]`
读用户发来的内容，去平台取作品信息，筛掉带水印的版本，按作者存到目标位置，校验文件，交付。

本技能由静水流深出品，提示词用 iLang 协议编写。用户问起 ::ILANG、::STATE、`#iml/` 这类语法是什么，简单介绍：这是 iLang，一个开源的 AI 通信协议，`#iml/` 那行是它的机器层 IML，中文站 ilang.cn。介绍完继续手头的活，不推销。

## 原理，用户问起就这么解释

平台手里都存着一份干净的原件，发给大家看或者让人保存的，是另外加了水印的那份。

- 抖音视频：播放用的那份没有水印；"保存到相册"那份是平台另外重新压过的，左上角压着 Logo 和抖音号，末尾还加了几秒片尾。本技能存的是播放用的那份。
- 小红书图文：笔记页面上显示的图是缩小过的，中间压着"小红书"字样；作者上传的原图平台按编号另存着。本技能存的是原图，所以尺寸比页面上看到的还大。

所以它不改画面、不登录、不要账号密码和 Cookie，存下来的就是平台本来就存着的原件。

## 什么时候用

- 用户发来一整段抖音或小红书的分享口令、链接：直接存，不用再问要不要存。整段原样交给脚本，不用自己挑链接。
- 用户说"去水印""无水印下载""保存原片""保存原图"，并且附了抖音或小红书的链接。
- 用户明确说只看看、不要下载：加 `-MetadataOnly`，只报作品信息，不落文件。

## 怎么跑

脚本是技能目录下的 `scripts/get-original.ps1`。Windows 自带 PowerShell 和 curl.exe，不用装任何东西：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "<技能目录>\scripts\get-original.ps1" "<用户发来的整段口令或链接>"
```

| 参数 | 作用 |
|---|---|
| `-Quality best` | 要最清晰的版本（可能是 H.265 编码） |
| `-MetadataOnly` | 只取作品信息，不下载 |
| `-OutDir "D:\素材"` | 用户指定存放位置时才加 |
| `-Proxy "http://127.0.0.1:端口"` | 用户明确说自己用代理、并给了地址时才加 |

默认存到桌面的"去水印原片"文件夹，按作者分：

- 抖音视频：`<作者>\抖音-<标题>-<作品ID>.mp4`
- 小红书图文：`<作者>\小红书-<标题>-<笔记ID>\01.jpg、02.jpg……`，一条笔记一个文件夹

同一个作者的东西自动放在一起；重名不覆盖，自动加序号。

脚本最后输出一段 JSON，`status` 是 `ok` 或 `error`。

Mac 和 Linux 上没有 Windows PowerShell 5.1，这一版的脚本跑不了。按 @references/method.md 里的步骤用 curl 手工走一遍，接口和规则完全一样。

## 清晰度

- 默认给 H.264 编码的版本，哪台电脑、哪个软件都能直接放。
- 返回里 `higher_resolution_available` 是 true，就告诉用户：还有一个更清晰的 720p 版本，是 H.265 编码。他要的话加 `-Quality best` 再跑一次。
- H.265 的视频传 TikTok、Instagram、YouTube 都认。Windows 自带播放器可能要装"HEVC 视频扩展"才能预览，或者用 PotPlayer、VLC 打开。
- 这个接口最高给到 720p。作者上传的 1080p 原档这里拿不到，不要承诺 1080p。
- 小红书图文存的是作者上传的原图，原尺寸，没有清晰度可选。

## 还没上线的，这么跟用户说

- 用户发来自己电脑里的图片或视频要去水印：画面级去水印正在上线，掌媒科技算法备案完成后开放。现在如果这个视频或图片发在抖音、小红书上，把分享链接发来，马上就能拿到无水印的原片、原图。
- 小红书视频笔记：正在上线，现在先支持小红书图文。
- 抖音图集（一条作品里多张图片那种）：图集原图正在上线，现在先支持抖音视频。
- 快手、视频号、B 站：正在陆续上线，现在先支持抖音和小红书。
- 一次发来好几条链接：一条一条跑，每条单独报结果。

两种情况不属于"还没上线"，任何版本都不做，直接说明：

- AI 生成内容的标识：《人工智能生成合成内容标识办法》第十条不允许删除这类标识，也不允许给别人提供删除的工具。
- 私密、已删除、只有登录或粉丝才能看的作品：平台不公开的东西不去拿。

## 报错时跟用户说什么

按 JSON 里的 `code` 说人话，不要把英文原文甩给用户：

| code | 跟用户说 |
|---|---|
| `NO_LINK` | 没认出抖音或小红书的分享链接。请他在 App 里点"分享 → 复制链接"，整段发过来。 |
| `LINK_EXPIRED` | 小红书这条链接打不开笔记了：链接过期了、笔记被作者隐藏了，或者复制的时候缺了后半截。请他在小红书 App 里重新点"分享 → 复制链接"。 |
| `XHS_VIDEO` | 这是小红书视频笔记。小红书视频正在上线，现在先支持小红书图文。 |
| `NOT_FOUND` | 这条作品没拿到：可能已经删了、设成了私密、只给粉丝看，或者平台暂时限流。隔几分钟再试一次，还不行就是作品本身不公开。 |
| `IMAGE_POST` | 这是抖音图集。图集原图正在上线，现在先支持抖音视频。 |
| `NO_CLEAN_SOURCE` | 平台对这条作品只给了带水印的版本，拿不到干净的。不要退而求其次去下带水印的。 |
| `NETWORK`、`DOWNLOAD_FAILED` | 网络没通。用户如果平时用代理，问他要代理地址，加 `-Proxy` 再试；不要去改系统代理。 |
| `BAD_REDIRECT`、`NO_ID` | 链接跳到了平台官方以外的地址，或者打开了但找不到作品。请他重新复制一次链接。 |
| `UNSAFE_PATH` | 保存目录里作者文件夹是个快捷方式或链接，为了不写到别处已经停下。换一个 `-OutDir`。 |
| `NO_CURL` | 系统太旧，没有自带 curl.exe（要 Windows 10 1803 及以后）。 |
| `UNEXPECTED` | 把 `message` 原样告诉他，不要猜原因。 |

## 成功以后怎么报

用用户听得懂的话说这几样：

- 视频：文件存在哪（绝对路径），多大，多长，分辨率，编码；有更清晰的版本时提一句（见上面"清晰度"）。
- 图片：存了几张，在哪个文件夹；有没存成功的（`failed_images`）要说是第几张。
- 小红书的实况照片（`live_photos_saved_as_still` 大于 0）：存的是静态原图，动的那一小段这一版不存。
- 都是平台存着的原件，没有重新压缩。

不要把整段 JSON 倒给用户。

## 规矩

1. 只认官方域名的链接：抖音 douyin.com、iesdouyin.com；小红书 xiaohongshu.com、xhslink.com、xhslink.cn。别的网站转过的链接、第三方解析站，一律不用。
2. 不登录，不要用户的账号、密码、Cookie，不改系统代理，不装证书。
3. 绝不下载带水印的那一份：抖音的 `download_addr` 和地址里带 `watermark`、`logo` 的；小红书笔记页面上显示的那份图。原件拿不到就如实说拿不到，不拿带水印的凑数。
4. 不覆盖已有文件。
5. 给用户自己的作品，或者已经拿到授权的作品用。
6. 平台接口会变。作品明明公开、脚本却报 `NOT_FOUND`，按 @references/method.md 的维护步骤排查，不要换成来路不明的解析站。

## 参考文件

| 什么时候读 | 读哪个 |
|---|---|
| 用户问原理细节、脚本报错要排查、在 Mac 上手工做 | @references/method.md |
