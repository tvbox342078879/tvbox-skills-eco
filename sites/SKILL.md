---
name: 发布为应用
description: 把当前沙箱里的项目发布为在线应用，生成可访问的分享链接（网站/小游戏/静态页/Node.js/Python/Go 单端口 HTTP 服务）。当用户想“发布为应用”、部署、发布、上线、生成分享链接、take offline / unpublish 时使用。
allowed-tools: Read, Write, Bash
---

# 发布应用 / Publish

把当前项目发布为一个在线应用，返回稳定的分享链接。

> `<skill-directory>` 指本 Skill 所在目录，不是用户项目目录。

## When to Use

- 用户要求 **发布 / 部署 / 上线 / 生成分享链接 / 预览到云端 / give me a URL**。
- 用户 @ 提到 **发布应用 / publish**。
- 用户要 **取消发布 / 下线 / take offline / unpublish** 已发布的应用。

## Ask Before Deploying —— 发布授权不跨轮次继承

发布是一次对外发版：它会覆盖分享链接背后现在的内容，而这个链接用户可能已经发给别人了。所以**每一次**
发布都要有**当轮**的授权。

只有用户**最近一条消息**满足下列之一，才算明确要求发布：

- 出现发布动词：发布 / 上线 / 部署 / 同步到线上 / 更新线上 / publish / deploy / go live；
- @ 提到 **发布应用 / publish**；
- 明确要一个可分享的在线链接。

其余一律算**没要求**，先用用户的语言问一句，再执行。**会话里早先同意过发布，不构成对下一次发布的授权。
不要静默发布。**

## 已发布的应用被再次修改

最常见的后续场景：应用已经上线，用户接着说「把标题改成 X」「按钮再大一点」「修一下这个 bug」。这类请求要的
是**本地修改**，不是发版；直接重新发布会把别人此刻正在看的页面替换掉，而用户根本没被问过。

改完之后：

1. 说明改了什么，让用户先在本地预览确认；
2. 用用户的语言问一句：「改动已经完成，需要我把它同步更新到线上分享链接吗？（线上现有内容会被覆盖）」；
3. **结束本轮，等用户回答。** 得到明确同意再跑 `publish.js`。

用户拒绝时，保持线上链接不动，并告诉他改动只留在本地。

## What Can Be Published

只支持能作为**单个 HTTP 端口**跑起来的项目：
- 静态站点 / PDF / 资源文件（用内置 HTTP server 提供）。
- 可运行的 HTTP 应用：Node.js（Express 等）、Python（Flask/FastAPI/Django）、Go http。

## Unsupported Projects & No Silent Downgrade

沙箱原生支持 **Node.js / Python / Go / 静态站点**。**不支持**：
- **Java / Maven / Gradle**（沙箱无 JVM/Maven/Gradle 构建）。
- 依赖沙箱不提供的**外部服务**（MySQL / Redis / Kafka / MongoDB，通常声明在 `docker-compose.yml` 或 Spring `application.*`）。

遇到不支持的项目：
- **直接告诉用户不支持**，说明原因和支持的类型。
- **绝不静默降级成静态占位页 / 项目概览页**。若静态页确实可能有用，先明确问用户 yes/no，得到同意再做。
- 依赖安装中途失败（如 Go 模块拉不下来）→ 报告失败和可能原因，**不要自作主张换策略**。建议具体修法（如 `go mod vendor`）或询问用户。
- 需要数据库/后端服务的项目 → 建议用带托管服务的平台（CloudBase / Supabase）。

## Requirements the Project Must Meet

- 服务必须监听 `PORT` 环境变量并绑定 `0.0.0.0`（Node 场景脚本会注入 `PORT`）。
- 纯静态 / PDF：无需自己写服务，脚本用 `python3 -m http.server` 提供。
- Vite 系（Vite/Vue/React/Svelte）：dev/preview server 必须允许反代 host —— 设 `server.host = "0.0.0.0"`，并把部署域名加入 `server.allowedHosts`（或 `allowedHosts: true`），否则 Vite 会 "Blocked request. This host is not allowed."。

## How to Publish

先确保项目文件都已写入磁盘（脚本按目录现状起服务）。然后：

```bash
node <skill-directory>/scripts/publish.js --dir <项目绝对路径>
```

可选参数：
- `--port N` — 单个公开端口（注入为 `PORT`），默认自动/3000。
- `--language auto|node|python|go|static` — 默认 `auto`。
- `--install-cmd "..."` — 覆盖自动探测的安装命令；传空串跳过安装。
- `--start-cmd "..."` — 覆盖启动命令；必须监听 `$PORT`。

脚本会自动探测运行时、安装依赖、启动服务、校验就绪并发布，最后输出 JSON。重复发布保持同一链接——也就意味着
链接背后的内容会被**覆盖**，且对所有已拿到链接的人立即生效，所以每次都要先过上面的授权检查。

启动后先做本地快速探测（约 5s）；若探测不通，脚本会读取自启动日志做分级诊断：
- **服务已起但端口不符** → 扫描真实监听端口（数据面识别 <50000 的端口，已排除 envd(49983)、keepalive(52025) 等基础设施端口），用真实端口重写自启动并重发。
- **命令类错误**（如命令里残留 `$PORT`）→ 有限自愈后重试。
- **运行时错误**（缺依赖、端口被占用等）→ 如实报告失败原因和日志摘要，**不自愈、不静默降级**。

输出示例：
```json
{ "shareLink": "https://axxxx.example.com", "verified": true }
```

启用了访问控制的环境下，`shareLink` 会带一个 `sharecode` 查询参数：

```json
{ "shareLink": "https://axxxx.example.com/?sharecode=ABC23XYZ89", "verified": true }
```

- 把 `shareLink` **整条原样**作为分享链接回给用户。
- **绝不能删掉或截断 `?sharecode=...`**：它是访问凭据，缺了它对方打开会看到
  「链接不完整」提示页。它属于链接本身，不是需要隐藏的内部细节。
- 不要暴露 releaseId、runtimeId、端口、appName 等内部细节。
- 若 `verified` 为 `false`，提示稍等几秒再打开链接。

## Unpublish (take offline)

```bash
node <skill-directory>/scripts/unpublish.js
```

直接使用本地记录取消发布。取消后分享链接失效。

- 这是**破坏性操作**，用户未明确要求时先确认。
- 输出 `status`：
  - `unpublished` — 已下线。
  - `not_found` — 本地没有可取消的发布记录（可能从未发布，或状态文件被清理）。
  - `already_unpublished` — 该发布已失效/已下线。

## Important Rules

- 只回 `shareLink`（分享链接），隐藏所有内部细节。链接里的 `?sharecode=...` 是链接的
  组成部分，必须完整保留 —— 它不是内部细节。
- 构建保守：不要瞎猜构建命令；monorepo / 非常规构建先问用户。
- 脚本报缺少运行环境相关错误时，如实告知用户，**不要编造**。
