## 1.4.1 — 2026-10-19（S165 深挖：接进 tvbox 总入口，消除孤岛）

### 修：这个技能是孤岛

全量扫 12 个自建技能在代码层的引用，**只有它一条都没有**——`tvbox` 里连 `scheduler`
五个字都没出现。文档写得齐全、功能也真在用（当日两轮定时任务压测全靠它），
但没有任何入口指向它。**文档完备 ≠ 接进主链。**

已接：`tvbox jobs`（列表 + 启停状态 + 停用项清理提示）、`tvbox jobs <id>`（单任务详情）。

### 为什么入口只做只读

建/改/删要过《操作确认协议》，放进 `tvbox` 一个子命令里会被绕过。
入口负责**看得见**，写操作仍走 `scheduler-api.sh`。

# Changelog

## 1.4.0 — 2026-10-19（S165 第二轮：用真实定时任务压测暴露的一族缺陷）

### 修：整条链的退出码是死的（最严重）

`bash scheduler-api.sh update --id 1 --retry-count -1` 会打印
`ERROR: --retry-count 必须是整数`，但 `$?` 是 **0**。三层都在吞：

| 层 | 修前 | 修后 |
|---|---|---|
| `validate_*` 函数 | 函数体内 `exit 1`（出不了函数） | `return 1`，调用方 `|| exit 1` |
| `main` 的 case 分支 | 裸调用，返回值被 `case` 吃掉 | 末尾 `return $?` |

后果不只是"退出码不好看"——**任何把脚本当门禁的调用方都恒判成功**。
与账房 `ledger.dispatch` / `link.dispatch` / `tvbox` 入口是同一族缺陷，本轮一并收口。

### 修：retryCount 区间靠试出来（0–10，不是 255）

字段类型是 Go `uint8`，但业务上限 10。三种报错指向两个不同天花板：

- `-1` → 本地拦（非无符号）
- `255` → `retry count must be <= 10`
- `999` → `cannot unmarshal number 999 into ... retryCount of type uint8`

新增 `validate_retry` 硬上限 10，并写明"uint8 字面范围 ≠ 业务上限"。

### 新增：create 回读，抓静默改写

`create --retry-count 0` 返回 `success:true`，回读却是 `3`（零值被 omitempty 忽略，回落默认）。
这类改写只有回读能发现。`create_task` 建完立刻 `get` 回读比对 `timeoutSec`/`retryCount`，
不一致打印告警并 **exit 3**（任务已建，不是失败，别重试创建）。

### 保留

`DEFAULT_TIMEOUT_SEC` 保持 600（服务端下限），`validate_timeout` 区间 600–86400 不变。
