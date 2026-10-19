# 运行环境与故障处理

## 首次安装

需要 Node.js 20+（推荐受支持的 LTS），以及 Edge、Chrome 或 Playwright Chromium。技能通过终端执行，不依赖 WorkBuddy 特定浏览器工具，也不要求登录自有服务。

在技能 `scripts` 目录执行：

```text
npm ci --no-audit --no-fund
node run.mjs --doctor
```

也可在任意目录使用 `npm --prefix "技能绝对路径/scripts" ci --no-audit --no-fund`。需要下载依赖，遵循当前宿主的网络与执行权限。ZIP 不包含 node_modules；锁文件固定 Playwright 版本。

浏览器自动尝试 Edge、Chrome、Playwright Chromium。没有可用浏览器时，在 `scripts` 目录执行：

```text
npx playwright install chromium
```

Linux 环境若缺浏览器系统库，交由当前环境的管理员或标准依赖安装流程处理。不要静默修改全局系统配置。技能目录只读时，可把整个技能复制到用户可写工作目录后安装并调用。

## 命令参数

| 参数 | 用途 |
| --- | --- |
| `--url` | 微信文章完整链接，必须是 mp.weixin.qq.com/s 路径 |
| `--html` | 用户指定的本地公众号 HTML，与 --url 二选一 |
| `--out` | 导出根目录，默认当前工作目录下 wechat-exports；其下自动建立唯一子目录 |
| `--source-url` | 本地 HTML 的原文链接，用于相对资源解析及来源标注 |
| `--browser` | auto / edge / chrome / chromium |
| `--headed` | 显示独立浏览器窗口，供本机用户完成页面验证，不复用私人浏览器资料 |
| `--timeout` | 页面加载及正文等待各自的最长秒数，默认 30，范围 1–300 |
| `--doctor` | 检查 Node 依赖和浏览器能否启动，不抓取文章 |
| `--help` | 查看帮助 |

PowerShell 示例：

```powershell
node 'D:/skills/wechat-article-styled-extractor/scripts/run.mjs' --url 'https://mp.weixin.qq.com/s?__biz=用户提供的值&mid=用户提供的值' --out 'D:/工作目录/wechat-exports'
```

上述链接只是参数引用方式示意，不能直接作为实际文章请求。微信链接含 `&`，必须整体引用。

## 文件交付

仅生成并交付 `preview.html`：独立预览，内含文章数据、“一键复制带格式内容”按钮、Alt+C 及实际提取提示；按钮脚本已内嵌，不依赖本地相邻 JS。最终回复只展示这一个文件链接。预览页不提供 HTML 或纯文本下载按钮，也不生成其他产物文件。

复制结果要在目标编辑器中验收；导出的图片仍为外链，所有 HTML 都不是离线图片归档。

## 本地 HTTP 预览（可选）

只有在本机执行环境，且用户需要打开本地预览时使用：

```text
node "技能目录/scripts/serve.mjs" --dir "返回的 preview.html 所在目录"
```

命令返回 `http://127.0.0.1:随机端口/`。使用宿主允许的打开浏览器方式展示此链接；服务只绑定回环地址，只提供已知产物文件，30 分钟后自动退出，也可按 Ctrl+C 停止。后台启动须使用宿主合适的隐藏窗口方式。云端的 127.0.0.1 链接不能直接当作用户本机链接交付，应交付文件。

## 已打开的微信页面

`assets/wechat-article-styled-extractor.user.js` 是可以安装的完整油猴脚本，与 Skill 使用同一提取引擎。安装后仅在 mp.weixin.qq.com 页面运行；正文出现后显示按钮，最长等待 5 秒后显示按钮以便反馈失败。所有操作只在点击或 Alt+C 后发生。没有油猴管理器时，优先使用默认命令行流程。

## 常见问题

- 看到验证页：可在用户本地用 `--headed --timeout 120` 留出手动验证时间，页面出现正文后自动提取并关闭独立窗口；仍失败则使用正常浏览器油猴入口。
- 外链图片失效：工具只能恢复页面提供的图片地址，用户需在目标编辑器中重新上传或修复图片。
- 粘贴只有文字：预览会明确提示降级，请换支持富文本的本机浏览器/编辑器重试。
- 保存的 HTML 缺失样式：需连同原页面的资源目录保存；本工具不能恢复已经丢失的 CSS。
- 不能启动浏览器：检查执行权限与运行库；不能用普通 HTTP 抓取冒充已验证的样式保留结果。
