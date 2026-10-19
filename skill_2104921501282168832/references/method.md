<!-- iLang · ilang.cn · github.com/ilang-ai · Long Quan Zhu (ORCID 0009-0004-4540-8082) -->
# 原理、手工步骤与维护

用户问原理细节、脚本报错要排查、或者在 Mac 上没法跑脚本时读这份。

## 为什么存下来的没有水印

抖音对同一个视频存了几份，作品信息里分字段给出：

| 字段 | 是什么 | 用不用 |
|---|---|---|
| `video.play_addr_h264` | 播放用的 H.264 版本 | 用，默认首选 |
| `video.play_addr` | 播放用的版本，通常同上 | 用 |
| `video.bit_rate[].play_addr` | 各清晰度档位，多为 H.265 | 要清晰度时用 |
| `video.download_addr` | 「保存到相册」那份 | **绝不用** |

`download_addr` 是平台另外重新压出来的：地址里带 `watermark=1`，路径里有 `/mps/logo/`，画面左上角压着抖音 Logo 和抖音号，末尾多出几秒片尾。播放那几份没有这些。

所以本技能做的事是「从平台本来就给出的几份里挑没水印的那份」，不是去改画面。画面里本来就有的东西（作者自己加的 Logo、字幕、贴片）一个像素都不会变。

## 小红书图文为什么存下来没有水印

小红书笔记页面的数据里，每张图给了三样东西：

| 字段 | 是什么 | 用不用 |
|---|---|---|
| `imageList[].url`、`infoList` 里的 `H5_DTL` | 页面显示用的图，缩到 1080 宽，**中间压着「小红书」字样** | **绝不用** |
| `infoList` 里的 `H5_PRV` | 更小的预览图 | 不用 |
| `imageList[].fileId` | 作者上传的原图在平台上的编号 | 用 |

拿 `fileId` 去小红书自己的图床取，拿到的就是原图：`https://sns-img-bd.xhscdn.com/<fileId>`，取不到再试 `https://ci.xiaohongshu.com/<fileId>`、`https://sns-img-qc.xhscdn.com/<fileId>`。三个都是小红书自己的域名。都取不到就报错，**不拿页面上那张带水印的凑数**。

笔记数据在分享页 HTML 的 `window.__INITIAL_STATE__` 里。用手机浏览器的 User-Agent 打开分享链接，数据在 `noteData.data.noteData`；用电脑浏览器打开会被跳到登录页。数据是 JS 对象的写法，里面有 `undefined`，要先换成 `null` 才能按 JSON 读。

分享链接必须带 `xsec_token`（从 App 里「分享 → 复制链接」得到的链接自带）。不带或者过期了，小红书会把你跳到首页、登录页或者 `/404/sec_…` 安全页。这不是脚本坏了，是链接失效了，让用户重新复制。

## 实测记录（2026-09-24）

环境：Windows 11，Windows PowerShell 5.1，系统自带 curl.exe 8.x。

- **作品信息接口**是 `https://aweme.snssdk.com/aweme/v1/feed/?aweme_id=<作品ID>&aid=1128`，请求头 User-Agent 用 Aweme App 的串。它返回的其实是一小段推荐流，要的那条作品只是「种子」：公开且还在的作品 3 次都在返回里，已删除的永远不在。**所以必须按作品 ID 逐条核对，绝不能拿推荐流里的第一条凑数。**
- **有没有水印是看画面对比出来的**，不是只看字段名：同一条视频、同一秒，`download_addr` 那份左上角有「抖音」Logo 和抖音号、末尾多 3 秒片尾、分辨率被重压成 720×1280；`play_addr` 那份干净。
- **清晰度**：新作品的 `play_addr` 常是 540p 的 H.264；`bit_rate` 里是 H.265 的各档，最高 720p。这个接口拿不到作者上传的 1080p 原档。老作品（2021 年那批）的 H.264 有 720p。
- **图集不支持**：这个接口不返回图集；分享页 `iesdouyin.com/share/note/<ID>/` 改成了前端渲染，页面里没有数据；能返回图集的接口不带签名一律被过滤。
- **小红书图文**：一条 11 张图的公开笔记，页面上那张 1080×1434 的图中间有「小红书」字样；按 `fileId` 从 `sns-img-bd.xhscdn.com` 取回来的是 3072×4080 的原图，同一位置干干净净。11 张全部取到，另抽一张 4096×3072 的也核过画面。
- **小红书视频笔记**：没测。视频笔记页面数据里有 `video` 字段，但没有真链接可验，这一版脚本遇到视频笔记直接报 `XHS_VIDEO`，不去猜。
- **小红书短链**：`xhslink.cn` 在 Windows 自带的 curl 上会因为连不上证书吊销服务器而握手失败（`CRYPT_E_REVOCATION_OFFLINE`）。脚本只在这一种错误上去掉吊销查询重试一次，证书本身照样校验。2025 年的旧 `xhslink.com` 短链已经全部跳首页，失效了。

## 小红书手工步骤

1. 用手机浏览器的 User-Agent 打开分享链接（跟着跳转），存下 HTML。
2. 在 HTML 里找 `window.__INITIAL_STATE__ = {…}</script>`，把里面的 `undefined` 换成 `null`，按 JSON 读。
3. 取 `noteData.data.noteData`，核对 `noteId` 是链接里那条。`type` 是 `normal` 是图文，`video` 是视频（这一版不做）。
4. 对 `imageList` 里每张图，用 `https://sns-img-bd.xhscdn.com/<fileId>` 下载，看文件头判断格式（JPEG 以 `FF D8 FF` 开头）。

## 抖音手工步骤（Mac、Linux，或脚本出错时排查）

以下用 curl。Windows 上把 `curl` 写成 `curl.exe`（PowerShell 里 `curl` 是另一个命令的别名）。

1. **从分享链接拿作品 ID。** 短链会跳转到 `https://www.iesdouyin.com/share/video/<作品ID>/…`：

   ```bash
   curl -sSL -o /dev/null -w '%{url_effective}\n' \
     -A 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1' \
     'https://v.douyin.com/xxxx/'
   ```

   跳到的地址里是 `/share/note/` 的，是图集，这一版到此为止。跳到抖音官方域名以外的，停下。

2. **取作品信息：**

   ```bash
   curl -sS --compressed -o feed.json \
     -A 'Aweme 350101 rv:350101 (iPhone; iOS 17.0; zh_CN) Cronet' \
     'https://aweme.snssdk.com/aweme/v1/feed/?aweme_id=<作品ID>&aid=1128'
   ```

3. **在 `aweme_list` 里找 `aweme_id` 等于作品 ID 的那一条。** 找不到就重试两次，还没有就是作品不公开或已删除。有 jq 可以这样取播放地址：

   ```bash
   jq -r --arg id '<作品ID>' '.aweme_list[] | select(.aweme_id==$id) | .video.play_addr_h264.url_list[0] // .video.play_addr.url_list[0]' feed.json
   ```

   没有 jq 也没关系，直接读这个 JSON，按上面「字段」那张表挑地址。**`download_addr` 不要碰，地址里带 `watermark` 或 `logo` 的都不要。**

4. **下载并校验：**

   ```bash
   curl -sSL -o 原片.mp4 -e 'https://www.douyin.com/' \
     -A 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1' \
     '<上一步拿到的地址>'
   head -c 12 原片.mp4 | tail -c 8 | od -c | head -1
   ```

   第 5 到第 8 个字节是 `ftyp` 才是 MP4。下载到的是网页或几百字节的小文件，说明地址过期了（这些地址几个小时就失效），回到第 2 步重新取。

## 脚本挑版本的规则

- 候选只来自 `play_addr_h264`、`play_addr`、`bit_rate[].play_addr` 这几处，地址必须是 https、不是裸 IP、不含水印标记。
- 默认（`compat`）：H.264 优先，其次分辨率高的，再其次码率高的。
- `best`：分辨率优先，同分辨率里 H.264 优先，再看码率。
- 第一个地址下载失败或者下下来不是 MP4，就换下一个，全部失败才报错。
- 不转码。存下来的字节就是平台给的字节。

## 维护：作品明明公开，脚本却拿不到

按这个顺序查，每一步确认了再往下：

1. 用一条确定公开的作品跑 `-MetadataOnly`，排除是这条作品本身的问题。
2. 看第 1 步的跳转结果里还有没有 `/share/video/<作品ID>`。平台改了跳转格式，就改脚本里取 ID 的规则。
3. 看作品信息接口的返回里 `aweme_list` 还在不在、要的作品在不在里面。整个接口不通了，先怀疑 User-Agent 里的 App 版本号过旧。
4. 看 `video` 下面那几个字段名变没变。
5. 改完用确定公开的作品重跑一次真实下载，再核一次画面没有水印。

不管哪一步卡住，都**不要**改成去下 `download_addr`，也**不要**换成第三方解析网站。拿不到就如实告诉用户拿不到。
