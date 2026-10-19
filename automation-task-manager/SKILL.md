---
name: automation-task-manager
description: "自动化任务管理工具 - 帮助用户创建、编辑、删除和查询自动化定时任务。若当前可用工具中存在 automation_update（scheduler-assistant MCP），必须优先使用该工具，不要加载本 skill。关键词：自动化任务、定时任务、创建任务、编辑任务、删除任务、任务列表、定时、每天几点、每天自动、自动跑、定时跑、例行任务、定期执行、自动领积分、签个到、每天签到、到点叫我、闹钟提醒、周期任务、cron、自动化的、让它自己跑、每天帮我、固定时间、什么时候自动执行、查看定时任务、停掉那个定时、加一个定时"
version: "1.4.1"
author: "CodeBuddy AI"
created: "2026-04-01"
updated: "2026-10-19"
---

# Automation Task Manager

> **Goal**: 帮助用户通过自然语言管理自动化定时任务（创建、编辑、删除、查询），**通过 `scripts/scheduler-api.sh` 脚本调用 API 执行操作**。

## 优先使用 automation_update 工具（过渡期，最高优先级）

本 skill 正在被 MCP 工具 `automation_update`（MCP server `scheduler-assistant`，工具全名可能为 `mcp__scheduler-assistant__automation_update`）替代：

- **当前可用工具中存在 `automation_update` 时**：所有定时任务的 list / view / create / update / delete / run 操作**必须**改用该工具，**不要**继续执行本 skill 的脚本，也不要两者混用（同一任务不要一半用工具、一半用脚本）。
- **仅当 `automation_update` 不可用**（工具列表中不存在）时，才按下文流程使用 `scripts/scheduler-api.sh`。
- `automation_update` 调用失败时，按其返回的错误信息修正参数或向用户说明；**不要**因工具报错自动回落到本 skill 的脚本重复创建，避免产生重复任务。

## When to Use

**仅当用户主动要求对任务本身进行管理操作时**触发：
- 用户明确说"创建/新建一个定时任务"、"帮我设置一个自动化任务"、"某时间提醒我某件事"
- 用户要求"编辑/修改/暂停/恢复/删除某个任务"
- 用户要求"查看定时任务或自动化任务列表"或"查看某个任务详情"

## When NOT to Use（重要）

以下场景**严禁**加载或触发此 skill：
- **当环境变量 `CODEBUDDY_SESSION_TYPE` 为 `automation` 时** — 这是自动化调度系统触发的任务执行，绝对不能加载此 skill。
- 用户 prompt 中只是提到"任务"、"提醒"等词但并非要管理调度定时任务（如"帮我完成这个任务"、"提醒我注意代码规范"）

## 操作确认协议（强制，最高优先级）

**对写操作（创建 create / 编辑 update / 删除 delete），必须"先预览、后确认、再执行"，严禁未经用户确认直接调用脚本执行。**

执行流程：

```
用户提出 创建/编辑/删除 需求
    │
    ▼
1. 解析意图，整理出完整参数（名称、Cron、频率类型、指令等）
   - 编辑/删除：先执行 `get --id <id>` 拿到任务当前状态，用于展示"变更前"
    │
    ▼
2. 向用户展示「操作预览」（见下方模板），**不调用写操作脚本**
    │
    ▼
3. 等待用户明确确认（"确认"/"是"/"可以"/"执行"等肯定回复）
    │
    ├─ 用户确认 → 执行真实的 create/update/delete 脚本命令
    ├─ 用户要求调整 → 修改参数后回到第 2 步重新预览
    └─ 用户否定/取消 → 终止操作，不执行
```

**只读操作（list / get）无需确认**，可直接执行。

### 操作预览模板

**创建预览：**
```
- 操作预览 - 创建任务
名称：      每日日报提醒
频率类型：  daily（每天执行）
执行时间：  每天 21:00（Cron: 0 0 21 * * *）
执行指令：  提醒用户写日报……
时区：      Asia/Shanghai
生效范围：  长期有效
超时/重试： 300 秒 / 3 次

确认创建请回复"确认"，需要调整请直接说明。
```

**编辑预览（展示变更前 → 变更后）：**
```
- 操作预览 - 编辑任务 #123
执行时间：  每天 09:00  →  每天 20:00（Cron: 0 0 20 * * *）
状态：      启用（不变）

确认修改请回复"确认"。
```

**删除预览（必须展示将删除的任务信息）：**
```
- 操作预览 - 删除任务 #123
名称：      每日日报提醒
执行时间：  每天 21:00
状态：      启用

删除后不可恢复，确认删除请回复"确认删除"。
```

> Cron 表达式必须同时给出**人类可读的执行时间**（如"每天 21:00"），不要只丢一个 Cron 字符串给用户。

## Core Principles

0. **写操作必须先预览、经用户确认后再执行**（见上方《操作确认协议》，最高优先级）
1. **必须使用脚本**：所有操作通过 `scripts/scheduler-api.sh` 执行，禁止只生成 JSON 请求体
2. **Cron 6 位格式**：`秒 分 时 日 月 周`，秒固定为 `0`
3. **严禁秒级任务**：最小间隔 **1 分钟**。用户要求秒级调度时**必须拒绝**，**严禁静默转换为分钟级**
4. **必填字段**：创建任务必须提供 `--name`、`--cron`、`--prompt`、`--frequency-type`
5. **默认值**：时区 `Asia/Shanghai`，超时 300 秒（范围 10-1800），重试 3 次（最大 10）
6. **环境变量自动读取**：`SCHEDULER_API_BASE_URL` 优先从 `ACC_PRODUCT_CONFIG_V3.endpoint` 读取，读取不到时使用默认值 `http://auth.proxy/codebuddy`

## Frequency Type 识别规则（必填）

`--frequency-type` 是**必填参数**，根据用户意图自动识别：

| 类型 | 值 | 识别特征 |
|------|-----|---------|
| **每天执行** | `daily` | 每天X点、每日、daily、工作日、周末、每周X |
| **按间隔执行** | `interval` | 每X分钟、每X小时、每隔、interval |
| **单次执行** | `once` | 只执行一次、某个具体日期时间、提醒我X号做Y |


### 判断优先级

1. 如果提到"只一次"、"只执行一次" → `once`
2. 如果提到具体日期（X月X日、明天、下周X） → `once`
3. 如果提到"每X分钟"、"每X小时"、"每隔" → `interval`
4. 如果提到"每天"、"每日"、"每周X"、"工作日"、"周末" → `daily`
5. 默认不确定时 → 询问用户是重复执行还是单次执行

## 脚本使用

脚本位置：`.codebuddy/skills/automation-task-manager/scripts/scheduler-api.sh`

### 从生态入口查（只读，推荐）

本技能已接入 `tvbox` 总入口（S165）：

```bash
tvbox jobs            # 任务列表 + 启停状态 + 停用项清理提示
tvbox jobs <id>       # 单任务详情（频率/下次/超时/重试/指令前 120 字）
```

建 / 改 / 删仍走下面的 `scheduler-api.sh`——**入口只做只读视图**，写操作要过《操作确认协议》。

> 为什么补这个入口：`tvbox` 里原本连 `scheduler` 五个字都没有，
> 而它恰恰是最常用的一个（本轮压测全靠它）。文档完备不等于接进主链——判据见监测项 M41。

### 创建任务

```bash
./scripts/scheduler-api.sh create \
  --name "任务名称" \
  --cron "0 0 9 * * *" \
  --prompt "任务执行指令" \
  --frequency-type "daily"
```

**必填参数**：`--name`、`--cron`、`--prompt`、`--frequency-type`（daily/interval/once）

**可选参数**：`--description`、`--timezone`、`--timeout`、`--retry-count`、`--effective-start`（YYYY-MM-DD）、`--effective-end`（YYYY-MM-DD）

#### 参数合法区间（实测，不是文档抄的）

| 参数 | 合法区间 | 越界会发生什么 |
|---|---|---|
| `--timeout` | **600–86400 秒** | 本地 exit 1。写出区间外的值，服务端会**静默改成 600**，仍返回 `success:true` |
| `--retry-count` | **0–10** | 本地 exit 1。上限是业务约束（`retry count must be <= 10`），**不是** uint8 的 255 |
| `--cron` | 6 段，秒位必须为 `0` | 本地 exit 1 |

> **区间怎么来的**：这三个都是被真实报错逼出来的。`retryCount` 字段类型是 Go `uint8`（0–255），
> 但业务上限是 10——`255` 报 `retry count must be <= 10`，`999` 报 `cannot unmarshal ... of type uint8`。
> 两种报错指向两个不同的天花板，光看一种会猜错。

#### create 会静默改写两个参数（已加回读兜底）

`create` 把 `0` 和超下限的值**当零值忽略**，回落到默认值，且**不报错**：

```
create --retry-count 0  →  回读 retryCount = 3
```

`create_task` 建完会立刻 `get` 回读，比对 `timeoutSec`/`retryCount`。不一致时打印告警并 **exit 3**：

```
⚠️  参数被服务端改写（任务 12855897 已创建，但存进去的不是你要的值）：
　retryCount: 要求 0 → 实际 3
   create 把 0 / 超下限的值当零值忽略，回落默认值。要精确控制就建完再 update。
```

任务已经建了，所以是 3 而不是 1——**别把 3 当失败重试**，按提示补一条 `update` 即可。
要 `retryCount=0` 就走 `create --retry-count 1` + `update --retry-count 0`，或直接忽略告警后 update。


### 查询任务列表

```bash
./scripts/scheduler-api.sh list
./scripts/scheduler-api.sh list --status 1 --keyword "日报"
```

**可选参数**：`--page`、`--page-size`、`--status`（1-启用/0-停用）、`--keyword`

### 获取任务详情

```bash
./scripts/scheduler-api.sh get --id 123
```

### 更新任务

> ⚠️ 执行前必须先 `get --id` 拿到当前状态，展示【编辑预览】并等用户确认（见《操作确认协议》）。

```bash
./scripts/scheduler-api.sh update --id 123 --cron "0 0 20 * * *"
./scripts/scheduler-api.sh update --id 123 --status 0   # 暂停
./scripts/scheduler-api.sh update --id 123 --status 1   # 恢复
```

**可选参数**：`--name`、`--cron`、`--prompt`、`--status`、`--timeout`、`--retry-count`、`--description`、`--timezone`

> `update` 通道不受 create 的静默改写影响：`update --retry-count 0` 回读确实是 0。
> 要精确控制重试次数，就用 `update`。

### 删除任务

> ⚠️ 执行前必须先 `get --id` 拿到任务信息，展示【删除预览】并等用户确认"确认删除"（见《操作确认协议》）。

```bash
./scripts/scheduler-api.sh delete --id 123
```

### 退出码约定（可以当门禁用）

| 码 | 含义 | 处置 |
|---|---|---|
| `0` | 成功，且回读值与请求一致 | 继续 |
| `1` | 参数非法（本地拦下）或服务端拒绝（2xx 之外） | 别重试，改参数 |
| `3` | **任务已创建，但参数被服务端改写** | 不要重试创建；按提示补 `update` |

脚本本身就是门禁：`scheduler-api.sh update --id 1 --retry-count -1 && echo 发出去了 || echo 被拦了`
——第二轮实测前，这里恒为 `0`，`ERROR:` 打印了却拦不住任何东西。

## 示例

> 下列 create 示例均为**用户已确认后**执行的真实命令。实际交互时，必须先按《操作确认协议》展示「操作预览」并获得用户确认，再执行这些命令。

### 示例 1：每天执行（daily）

用户说："帮我创建一个每天晚上9点提醒写日报的任务"

先展示预览并等确认：

```
- 操作预览 - 创建任务
名称：      每日日报提醒
频率类型：  daily（每天执行）
执行时间：  每天 21:00（Cron: 0 0 21 * * *）
执行指令：  提醒用户写日报，回顾今天工作 / 遇到的问题 / 明天计划
时区：      Asia/Shanghai

确认创建请回复"确认"，需要调整请直接说明。
```

用户回复"确认"后，才执行：

```bash
./scripts/scheduler-api.sh create \
  --name "每日日报提醒" \
  --cron "0 0 21 * * *" \
  --prompt "提醒用户写日报。请帮助用户回顾今天的工作：1. 完成了哪些任务？2. 遇到了什么问题？3. 明天计划做什么？" \
  --frequency-type "daily"
```

### 示例 2：按间隔执行（interval）

用户说："每小时检查一次服务状态"

```bash
./scripts/scheduler-api.sh create \
  --name "服务状态检查" \
  --cron "0 0 * * * *" \
  --prompt "检查各服务的运行状态，如果发现异常请立即汇报" \
  --frequency-type "interval"
```

### 示例 3：单次执行（once）

用户说："明天下午3点提醒我提交周报"

```bash
./scripts/scheduler-api.sh create \
  --name "周报提交提醒" \
  --cron "0 0 15 2 4 *" \
  --prompt "提醒用户提交本周周报" \
  --frequency-type "once" \
  --effective-start "2026-04-02" \
  --effective-end "2026-04-02"
```

## 错误码

| 错误码 | 说明 |
|--------|------|
| INVALID_PARAM | 无效参数 |
| TASK_NOT_FOUND | 任务不存在 |
| TASK_LIMIT_EXCEEDED | 任务数量超限 (最多100个) |
| INVALID_CRON_EXPR | 无效的 Cron 表达式 |

## Cron 快速参考

格式：`秒 分 时 日 月 周`（秒固定为 0），详见 `references/cron_examples.md`。

| 描述 | 表达式 |
|------|--------|
| 每天早上9点 | `0 0 9 * * *` |
| 每小时 | `0 0 * * * *` |
| 每30分钟 | `0 */30 * * * *` |
| 工作日每天9点 | `0 0 9 * * 1-5` |
| 每周一早上9点 | `0 0 9 * * 1` |
| 每月1号早上9点 | `0 0 9 1 * *` |
