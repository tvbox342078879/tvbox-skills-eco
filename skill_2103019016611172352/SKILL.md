---
name: workbuddy-daily-credits
slug: workbuddy-daily-credits
version: 1.6.1
license: Proprietary
displayName: "WorkBuddy自动领积分"
display_name: "WorkBuddy自动领积分"
display_name_en: "WorkBuddy Auto Credits Claimer"
summary: 每天自动领取 WorkBuddy「Buddy加油站」的每日通用积分，并顺带完成派猫猫旅行闭环（先领后派）。读取本机登录态在本地完成兼容解密后直连官方接口（只读、不落盘、不外发），UI Automation 点击兜底；当天已领则静默跳过。
description: |
  每天自动领取 WorkBuddy「Buddy加油站」每日通用积分（100 分/天），并顺带完成派猫猫旅行闭环（先领后派，5-10 分/天，服务端每日限派 1 次）。

  双路径：① 接口直签（首选）——读取本机登录态文件，按客户端既有信封格式在本地解开当前登录用户自己的访问凭据（只读、不修改、不落盘、不外发），直调官方签到接口，不开窗口不动界面，约 0.5 秒；② 界面兜底——登录态不可用或接口异常时，用 Windows UI Automation 打开面板点击领取（最多 3 轮重试）。

  定时执行交给 WorkBuddy 自带自动化按日触发，无需任何系统级计划任务；本技能不写注册表、不装服务、不驻留后台进程。特点：当天已领静默退出；单实例守卫；--slot 时段窗口；--quiet 静音。网络访问仅限官方域名（codebuddy.cn 签到 / workbuddy.cn 旅行）。

  适用：Windows 10+，客户端已登录。

  触发词：自动签到、Buddy加油站、领积分、派猫猫、daily credits。
description_zh: |
  每天自动领取 WorkBuddy「Buddy加油站」每日通用积分 100 分/天，并顺带完成派猫猫旅行闭环：领到达奖励（5-10 分/天）+ 派新一轮（每日限派 1 次）。

  双路径：① 接口直签——读取本机登录态文件并在本地完成兼容解密，取得当前登录用户自己的访问凭据（只读、不修改文件、凭据不落盘不外发），直调官方签到接口，不开窗口不动界面，约 0.5 秒；② 界面兜底——登录态不可用/接口异常时，用 Windows UI Automation 打开面板点击领取（最多 3 轮重试）。

  定时执行用 WorkBuddy 自带自动化按日触发即可；本技能不写注册表、不建计划任务、不装服务、不设开机自启、不驻留后台进程。特性：单实例守卫；--slot 时段窗口；--quiet 静音；--travel-only 只跑旅行、--no-travel 关闭。网络访问仅限官方域名 www.codebuddy.cn（签到）与 www.workbuddy.cn（旅行）。

  触发词：自动签到、Buddy加油站、领积分、派猫猫、daily credits。
description_en: |
  Automatically claims the daily WorkBuddy "Buddy Fuel Station" credits (100 per day) and runs the Buddy travel loop: claim the arrived reward (5-10 credits/day) and dispatch a new trip (1 per day).

  1) API path (preferred): reads the local login-state file and decrypts it locally to obtain the signed-in user's own access credential (read-only, file never modified, credential never stored or shared), then calls the official APIs. No windows touched; ~0.5 s per run. 2) UI fallback via Windows UI Automation (up to 3 retries) if the login state is unavailable or the API fails.

  Scheduling is handled by WorkBuddy's built-in automation; the skill writes no registry keys, creates no scheduled tasks, installs no services and leaves no background process. Features: idempotent daily skip; single-instance guard; --slot/--quiet/--travel-only/--no-travel. Network access is limited to official domains (www.codebuddy.cn, www.workbuddy.cn).
category: office-efficiency
tags: [workbuddy, 积分, 签到, 自动化, 效率]
allowed-tools: Read, Write, Bash
metadata:
  author: amtf
  copyright: "Copyright (c) 2026 amtf. All rights reserved."
---
# WorkBuddy自动领积分

每天自动点开客户端的「Buddy加油站」，把那份**每日通用积分**领走（当前 **100 分/天**）。

## 它做什么 / 不做什么

| 做 | 不做 |
|---|---|
| 接口直签官方接口 / 界面兜底点击领取 | ❌ 不修改登录态文件（只读）、不外发凭据 |
| 派猫猫旅行「先领后派」闭环 | ❌ 不伪造风控参数、不访问非官方域名 |
| 当天已领则静默跳过 | ❌ 不联网上传任何数据、不监听网络端口 |
| 配合 WorkBuddy 自动化按日执行 | ❌ 不写注册表、不建计划任务、不装服务、不设开机自启 |
| 结束后把前台焦点还原给你 | ❌ 凭据不落盘、不打印、不外发 |
| 日志与状态落盘，`--json` 可解析 | ❌ 不在锁屏/未登录时乱点（直接退出报状态） |

## 快速开始

```bash
# 只检测状态，不点击领取（首次运行会先自动装依赖）
python scripts/fuel-claim.py --dry-run

# 真正领取一次（当天已领会自动跳过）
python scripts/fuel-claim.py

# 输出一行 JSON，便于自动化/脚本解析
python scripts/fuel-claim.py --json

# 只在 09:10 前后 30 分钟内执行，其余时间静默跳过（适合放进自启/定时任务）
python scripts/fuel-claim.py --slot 09:10

# 自定义窗口：09:10 ± 15 分钟（可跨零点，如 23:50±30）
python scripts/fuel-claim.py --slot "09:10±15"

# 静音：不输出任何内容，只写日志文件（适合后台静默运行）
python scripts/fuel-claim.py --quiet
```

| 参数 | 作用 |
|---|---|
| `--dry-run` | 只检测状态，不点击领取 |
| `--force` | 忽略「当天已领」短路，强制走一遍完整流程 |
| `--json` | 只输出一行 JSON |
| `--keep-open` | 结束后不关闭面板（排障用） |
| `--slot HH:MM[±mm]` | 仅在指定时段（默认 ±30 分钟）内执行，其余时间跳过 |
| `--quiet` | 静音，stdout 不输出任何内容，只写日志文件 |
| `--api-only` | 只走接口直签路径，接口不可用立即退出（码 4），绝不弹界面（定时任务专用） |

**单实例守卫**：脚本保证同一时刻只有一个实例在动作，多个触发撞在一起时，后触发的那次会打印「已有实例在运行」并立即退出，绝不会出现两个实例同时点击客户端界面。`fuel-claim.py` 用 Windows 命名互斥体实现。

要求：**WorkBuddy 桌面客户端已登录**。接口直签路径只读本地登录态，客户端可以不开；界面兜底路径要求客户端正在运行，且锁屏状态下无法操作界面。

### 依赖

唯一第三方依赖是 [`uiautomation`](https://pypi.org/project/uiautomation/)（LGPL-3.0 开源库，Windows UI Automation 的 Python 封装）。

- **首次运行时自动安装**：脚本检测到缺失会先打印 `[setup]` 提示，再执行 `python -m pip install --user uiautomation`（安装到你的用户目录，不动系统环境）。
- 也可以提前手动装好：`python -m pip install --user uiautomation`，装好后脚本不会再触发安装。

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 已领 / 领取成功 / 今日已领跳过 / 不在指定时段跳过 / 已有实例跳过 |
| 2 | 未能完成（面板打不开、点击后状态未确认等，看日志排查） |
| 3 | 环境异常（登录态不可用、客户端未运行、无障碍树未就绪、依赖装不上） |
| 4 | 接口不可用（--api-only 模式，等下轮重试） |

## 每日自动执行（推荐用 WorkBuddy 自动化）

本技能**不写注册表、不创建计划任务、不装服务、不驻留后台进程**。定时交给 WorkBuddy 客户端自带的「自动化」功能即可，一次配置长期有效：

**方式 A：WorkBuddy 自动化（推荐）**

在客户端「自动化」面板新建一条每天执行的任务：

- 名称：`WorkBuddy 每日积分签到`
- 频率：每天一次（如 09:00）
- 提示词：

  > 运行 workbuddy-daily-credits 技能的 `scripts/fuel-claim.py --json`，
  > 并把结果用一句话汇报（今日是否已领、连领天数、累计积分）。

该方式由客户端自身调度，无需任何系统级权限，也不会在系统里留下常驻进程。

**方式 B：手动 / 自行安排**

也可以在任何时候手动跑一次：

```bash
python scripts/fuel-claim.py            # 完整流程（先领后派）
python scripts/fuel-claim.py --json     # 只输出一行 JSON，便于接入你自己的脚本
python scripts/fuel-claim.py --quiet    # 静默，只写日志
```

无论哪种方式，同一自然日内的重复触发都会被「今日已领」短路，不会重复领取；多路触发器同时命中时，单实例守卫保证只有一个实例真正动作。

> 说明：如果你确实需要**脱离 WorkBuddy 客户端**的系统级定时（Windows 计划任务 / macOS launchd），
> 可自行创建，本技能不主动写入任何系统配置。接口直签路径不依赖客户端运行，因此这类方案也可用。

## 工作原理

**接口直签路径（首选，约 0.5 秒）**

1. **读登录态** —— 在本机 WorkBuddy 的登录态文件中找到 `auth.accessToken`（只读，不修改）
2. **本地解密** —— 若该字段是客户端 5.6.2+ 的 AES-256-GCM 信封，按客户端既有格式在本机解开，得到当前用户自己的访问凭据；解密密钥按「环境变量 → 密钥文件 → 本机密钥块 →（最后兜底）客户端进程内存」四级依次查找，正常环境走到第三级即可，**无需读取任何进程**
3. **调接口** —— 直连官方 `checkin-activity-status` 查询：已领则直接收工；未领则调 `daily-checkin` 领取
4. **旅行闭环** —— 收尾时执行「先领后派」（见上）
5. 任一环节不可用时**静默退回界面路径**，绝不因接口问题让当天签到失败

**界面兜底路径（唤醒无障碍树 + 点击）**

0. **前置闸门（不碰界面）** —— 先做 `--slot` 时段判定、再抢单实例互斥体、再看当天是否已领；任一不满足就直接退出，完全不触碰客户端界面
1. **唤醒无障碍树** —— 临时置位系统「屏幕阅读器」标志（`SPI_SETSCREENREADER`）。WorkBuddy 是 Electron 应用，内嵌 Chromium 的无障碍树是按需构建的：不置位时整个窗口只暴露 8 个空壳控件，置位后约 10 秒内展开到 1000+。用完立即恢复系统原值
2. **找头像、展开菜单** —— 头像是窗口左下角的 `MenuItemControl`（AutomationId `\:rs\:`）；点开后在菜单浮层内找「Buddy加油站」入口按钮
3. **点入口、等面板** —— 入口按钮用 UIA 的 Invoke 模式点击（实测该按钮几何中心恰好落在「Buddy加油站」与「去邀约」两个区块的分界线上，纯坐标点击无效）
4. **读签到按钮文案** —— 面板内 `fuel-expanded-claim` 按钮的文案即状态：含「已领」→ 收工；否则执行领取
5. **点掉「开心收下」确认框** → 关闭面板 → 还原前台焦点 → 写状态文件

## 已知限制与风险

- **接口直签路径**只依赖本机登录态，客户端不开也能用；**界面兜底路径**要求客户端正在运行且已登录，且窗口不能处于最小化（脚本会尝试自行恢复/前台化，但锁屏时无能为力）
- 加油站面板是客户端**远程加载**的页面，官方改版可能改控件 ID 或文案。脚本用稳定的 `fuel-*` AutomationId + 文案兜底，但无法保证永远兼容——遇到改版请反馈
- 自动化点击客户端界面存在被风控识别的理论风险。接口路径全天只做少量幂等查询与至多一次领取；界面兜底每天最多动作一次，当天领到即停，不做任何高频请求

## 排障

| 现象 | 处理 |
|---|---|
| 提示「无法从本机登录态取得可用凭据」 | 确认 WorkBuddy 客户端已登录；若客户端为 5.6.2+，可设置 `WORKBUDDY_ATREST_KEY`（44 字符规范 base64）或 `WORKBUDDY_ATREST_KEY_FILE`（密钥文件路径）后重试 |
| 退出码 3「未找到客户端窗口」 | 只有界面兜底路径需要客户端。启动并登录 WorkBuddy 后重试，或直接依赖接口路径 |
| 退出码 3「无障碍树未就绪」 | 多数是锁屏/远程桌面断开导致。回本地跑一次看是否恢复 |
| 退出码 2「打不开加油站面板」 | 手动点一次头像菜单确认客户端界面正常；官方改版时等技能更新 |
| 提示「点击了但未能确认结果」 | 用 `--keep-open` 跑一次，肉眼观察按钮实际文案变化 |
| 用 `--slot` 时总是跳过 | 检查当前系统时间是否落在该时段 ±30 分钟内；日志里会打印实际窗口与当前时间；想要更宽/更窄用 `--slot "HH:MM±mm"` |
| 日志出现「已有实例在运行」 | 说明上一轮还没跑完（或残留进程）。等它结束后重跑即可，属正常保护 |
| 依赖安装失败 | 手动执行 `python -m pip install --user uiautomation` 后重试（仅界面路径需要） |
| 日志出现「接口网络异常」「ConnectionRefused」 | 网络层瞬态故障。脚本内置短重试（3 次），通常几分钟内自愈；开机初期代理常比客户端晚启动，稍后再试即可 |
| 自动化没按点跑 | 到客户端「自动化」面板检查该任务是否启用；确认客户端当时在运行 |

日志与状态文件位置：`~/.workbuddy/wb-daily-credits/state/`（`fuel.log` 运行日志、`fuel-state.json` 当日状态；可随时删除，删后下次运行重建）。

---

**版权声明**：Copyright (c) 2026 amtf. All rights reserved.
本技能为专有作品，受著作权法保护。任何人可以安装并在自己设备上**个人使用**；
未经著作权人书面许可，不得复制、修改、再分发、商用，或基于本作品进行二次开发后对外发布。
授权发布渠道：
- SkillHub：https://skillhub.cn/skills/user_9cf3307c/workbuddy-daily-credits
- WorkBuddy 开放平台（open.workbuddy.cn）：搜索「WorkBuddy自动领积分」
