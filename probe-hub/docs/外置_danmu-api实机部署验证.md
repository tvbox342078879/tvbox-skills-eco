# danmu-api 实机部署验证（S196）

> 前一轮只拆了包结构，没真跑。这轮在沙箱里**实际启动并跑通全链路**，拿到部署数据。
> 结论一句话：**能跑，而且很轻；但有两个必踩的坑，且强依赖外网。**

---

## 1. 跑通验证（不是静态阅读，是真启动）

```
node danmu_api/server.js
→ Server running on http://[::]:19321
→ Favorite scheduler started (Node/Docker only, Asia/Shanghai)
→ Proxy server running on http://[::]:5321
```

**全链路实测：**

| 接口 | 结果 | 耗时 |
|---|---|---|
| `/search/anime?keyword=斗罗` | 200，**7.1KB** | 2.34s（冷） |
| `/bangumi/3965700`（斗罗大陆2） | 200，40KB | 0.004s（命中缓存） |
| 番剧详情**176 集**全返回 | ✓ | — |
| `/comment/10306`（第一集弹幕） | 200，**2.0MB·20035 条** | 0.67s |

弹幕返回格式（dandanplay v2 协议）：

```json
{"cid": 1, "p": "0.00,1,16777215,[qq]", "m": "唐门弟子报道 x 11 🔥1.4k", "t": 0}
```

`p` 字段是标准弹幕参数（时间,模式,颜色,**来源平台标记**），`[qq]` 表示来自腾讯源。
**注意 `cid` 不是平台 ID，是弹幕序号**——按 cid 统计平台会得到 "1~N 各一条" 的假分布。

---

## 2. 部署画像（实测数字，不是估算）

| 指标 | 实测值 | 含义 |
|---|---|---|
| 包体积 | **25 MB** | 含 `node_modules`（27 个包） |
| 常驻内存 (RSS) | **145 MB** | 空载 |
| 冷启动 | **0.30 s** | 从进程起到端口可连 |
| 冷查询 | 1.84 s | 首次搜一部没缓存的片 |
| 热查询 | **0.01 s** | 同请求重复，本地缓存命中 |
| 6 并发 | 3.45 s 总耗时 | 5 个不同查询 + 1 个 config |
| 监听端口 | 19321（主）+ **5321（附带代理）** | 别忘了防火墙要开两个 |

**判断**：25MB / 145MB / 0.3s 冷启动 —— 这规格**一台 1C1G 的机器绰绰有余**，
甚至软路由、群晖这类 NAS 都能跑。部署成本几乎可以忽略。

---

## 3. 两个必踩的坑（真踩到了）

### 坑 A：Node 版本 < 20.19 会直接崩，且包内**没有** esbuild

我第一次用系统 Node（v18.19.1）启动，报错：

```
[esm-shim] Node.js compatible (>=20.19.0): false
[esm-shim] Needs shim: true
[esm-shim] missing dependency: run `npm install esbuild`
Error: Cannot find module 'esbuild'
```

**根因**：官方的 `danmu-plugin.zip` 里**没有打包 esbuild**。
Node ≥20.19 时 shim 不启用，无所谓；一旦低于这条线，shim 强制启用并 require esbuild → 崩。

**处置**：
- 用 Node ≥ 20.19（推荐 22 LTS），**零额外安装**，实测直接起
- 若必须用老 Node：得先 `npm install esbuild`，而它只带了 27 个包，等于要联网补依赖

> **这条之前不知道**。如果按"包里带 node_modules 就能离线跑"的直觉部署到老环境，会直接挂。

### 坑 B：`.env` 得自己从 `.env.example` 复制

包里 `config/` 只有 `.env.example`（27KB），**没有 `.env`**。
首次启动会尝试从 `config_example/` 自动复制——但这个目录**包里也没有**，于是只能手工：

```bash
cp config/.env.example config/.env
```

日志会明确说 `[server] .env.example not found in config or config_example, cannot auto-copy`。

---

## 4. 强依赖外网，无法离线

翻了 `sources/` 的实现，所有源都是**实打实的外部 API 调用**，没有本地数据：

| 源 | 依赖 |
|---|---|
| `douban.js` | `douban-util.js` → 豆瓣接口（含"公开接口"兜底路径） |
| `kan360.js` | `https://api.so.360kan.com/episodeszongyi` 等 |
| `tencent/iqiyi/youku/mango/...` | 各自平台的私有接口 |
| `vod.js` | `VOD_SERVERS` 里的采集站 |

**结论**：这个服务是"聚合代理"，不是"离线库"。**没网 = 没弹幕**，离线部署无意义。

---

## 5. 自建 vs 野部署：现在可以给结论了

上一轮我只给了选型判据，没给结论。现在有数据了：

| 维度 | 自建 | 野部署（实测 11 个） |
|---|---|---|
| **成本** | 25MB/145MB，1C1G 够用，**几分钟搞定** | 0 |
| **可控性** | `SOURCE_ORDER` 随你配（官方默认只开 4 源） | 看部署者心情，多数只开 4 源 |
| **可用性** | 取决于你的机器 | 取决于别人，随时可能挂 |
| **速率** | 独占，无排队 | 共享，高峰期排队 |
| **隐私** | 你自己的 token | **你的搜索词会经过别人的服务器** |
| **门槛** | Node ≥20.19 + 复制一份 .env | 直接用 |
| **维护** | 版本更新要自己拉 | 别人管 |

**结论：能自建就自建。**

理由不是"自建更快"，而是**野部署的根本性缺陷是 `SOURCE_ORDER` 普遍只开 4 源**
（`douban,360,renren,hanjutv`）—— 你自己起一份，改成 17 源全开，能搜到的番立刻多一大截。
这事的收益远大于"省下 145MB 内存"。

**什么时候用野部署**：只是想临时看某部剧的弹幕、不想折腾机器、或客户端只是个轻量播放器。

**选野部署时按这个顺序挑**（上一轮推的判据，这轮验证过可行）：
1. `/api/config` 能读 → 直接看 `version`，**越新越好**（实测分布 1.9.3 ~ 1.21.6）
2. `SOURCE_ORDER` 长度，**≤5 个基本等于没配**
3. `CUSTOM_MERGE_RULES` 非空 → 说明有人在持续维护别名库
4. 最后才看响应速度

---

## 6. 部署最小可行步骤（实测可用）

```bash
# 1. Node 必须 >= 20.19（实测 22.13.1 可直接跑）
node -v

# 2. 解包后复制一份 .env（包里没有现成的）
cd danmu-api
cp config/.env.example config/.env

# 3. 改关键项（必改：TOKEN；建议改：SOURCE_ORDER 开全源）
#    TOKEN=87654321        <- 默认值，不改就是公开可读
#    SOURCE_ORDER=douban,360,tencent,youku,iqiyi,imgo,bilibili,renren,hanjutv,dandan,animeko,leshi,xigua,migu,sohu

# 4. 启动
DANMU_API_PORT=9321 TOKEN=你的token node danmu_api/server.js

# 5. 验证
curl "http://127.0.0.1:9321/你的token/api/v2/search/anime?keyword=斗罗"
curl "http://127.0.0.1:9321/你的token/api/v2/comment/<episodeId>?withRelated=true&format=json"
```

**注意端口**：主服务 9321（默认）+ 附带代理 **5321**，两个都要放行。
`9321` 同时是 DsPlayer 本地网关的默认端口，**同机部署会打架，记得改一个**。

---

## 7. 边界

- 只验证了 **Node 直启**这一种方式；Docker / Vercel / Cloudflare / Netlify / HF 五种官方方式**均未试**。
- 内存/冷启动数字来自沙箱实测，**换平台（尤其 Serverless）会完全不同**——Serverless 有冷启动惩罚，145MB 常驻在免费额度上未必划算。
- 只在沙箱网络下测过，**国内网络访问各平台的连通性未验证**。
- `NODE ≥20.19` 是实测下限，**没有回溯测试 20.0~20.18 区间**具体行为。
- 未测长时间运行下的内存泄漏（只跑了 54s + 数次短测）。
