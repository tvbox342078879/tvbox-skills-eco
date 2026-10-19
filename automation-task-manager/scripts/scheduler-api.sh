#!/bin/bash
#
# Automation Task Manager - HTTP API Client
# 用于调用 scheduler API 进行任务管理
#
# 使用方法: ./scheduler-api.sh <action> [options]
# Actions: create | list | get | update | delete
#

set -e

# load_trusted_env
# 幂等地从可信源拉取 env 并 source 到当前 shell。分两步：
#   1. sandbox-proxy 侧车（http://127.0.0.1:49983/envs）：系统级可信 env。
#   2. per-session bot env 文件：session 专属上下文（bot channel、session type）。
# 步 2 在步 1 之后执行，bot env 优先级更高（session 专属覆盖系统默认）。
#
# 行为契约：
#   - 步 1 侧车成功 + 值非空 → export 覆盖当前 shell 同名变量
#   - 步 1 侧车成功 + 值为空 → 跳过（避免空字符串覆盖现有有效 env）
#   - 步 1 侧车失败 / 返回非 JSON / 无 jq → 静默 fallback（不阻断脚本）
#   - 步 2 文件存在且 CODEBUDDY_SESSION_ID 非空 → source（幂等：_ONLY_ONCE）
#
# 已知限制：value 含换行符的场景下 @sh 转义会跨行，无法用 eval 重建。
# 沙箱侧车 env 不会出现换行符值，故不做特殊处理。
load_trusted_env() {
    [ -n "$_CB_TRUSTED_ENV_LOADED" ] && return 0

    # Step 1: sandbox-proxy 侧车 —— 系统级可信 env
    local trusted_env_url="http://127.0.0.1:49983/envs"
    local response

    # --max-time 3: 避免侧车不可用时 hang 住整个脚本
    # -f: 4xx/5xx 视作失败（避免误把错误页当 JSON 解析）
    if response=$(curl -s --max-time 3 -f "$trusted_env_url" 2>/dev/null) && [ -n "$response" ] && command -v jq &> /dev/null; then
        if echo "$response" | jq -e 'type == "object"' >/dev/null 2>&1; then
            # 跳过空值；jq @sh 自动做 shell 转义（value 中的单引号/特殊字符）
            # process substitution 而非 pipe：避免子 shell 导致 export 失效
            while IFS= read -r entry; do
                # entry 格式: KEY=shell-escaped-VALUE（@sh 对空字符串输出 ''）
                case "$entry" in
                    *='') continue ;;
                esac
                # shellcheck disable=SC2086
                eval "export $entry" 2>/dev/null || true
            done < <(echo "$response" | jq -r 'to_entries[] | select(.value != "") | "\(.key)=\(.value | @sh)"' 2>/dev/null)
        fi
    fi

    # Step 2: per-session bot env（CODEBUDDY_SESSION_ID 可能已被步 1 注入）
    if [ -n "$CODEBUDDY_SESSION_ID" ] && [ -f "/tmp/codebuddy_bot_env/${CODEBUDDY_SESSION_ID}.env" ]; then
        # shellcheck disable=SC1090
        source "/tmp/codebuddy_bot_env/${CODEBUDDY_SESSION_ID}.env"
    fi

    _CB_TRUSTED_ENV_LOADED=1
}

# 拉取并 source 侧车 + bot session 的可信 env；侧车不可用时静默 fallback
load_trusted_env

# 防护：自动化调度任务执行期间禁止调用此脚本（避免递归管理任务）
if [ "$CODEBUDDY_SESSION_TYPE" = "automation" ]; then
    echo "ERROR: 当前处于自动化任务执行环境（CODEBUDDY_SESSION_TYPE=automation），禁止管理定时任务" >&2
    exit 1
fi

# 默认配置
DEFAULT_TIMEZONE="Asia/Shanghai"
DEFAULT_TIMEOUT_SEC=600   # S165：服务端下限就是 600，原来写 300 会被静默改成 600
DEFAULT_RETRY_COUNT=3
DEFAULT_PAGE_SIZE=20

# 校验 timeout 合法区间（S165·实测所加）
# 服务端硬下限 600、上限 86400。原来 create_task 的默认值是 300（**低于这个下限**），
# **低于这个下限**，而 create 会把它原样送出去 → 服务端把 300 静默改成 600，
# 调用方却收到 {"success": true}。实测：建完再 get 回读，timeoutSec 是 600 不是 300。
# 这就是「参数写 300、实际跑 600」的静默篡改——比报错更坏，因为没人会去回读。
# 这里前置拦下并给出可执行提示，让错误在**建的这一刻**就暴露。
# 注意：这两个函数**返回**退出码而不是自己 exit。S165 实测发现
# `bash script.sh update --retry-count -1` 明明打印了 ERROR，`$?` 却是 0——
# 因为 exit 1 发生在**函数内部的子 shell** 里，只在函数体里生效，调用方拿到的是
# 函数返回后被忽略的状态。函数返回码 → 调用方 `|| exit 1`，才真正传到进程。
validate_timeout() {
    local t="$1"
    if ! [[ "$t" =~ ^[0-9]+$ ]]; then
        echo "ERROR: --timeout 必须是整数秒（收到: $t）" >&2
        return 1
    fi
    if [ "$t" -lt 600 ] || [ "$t" -gt 86400 ]; then
        echo "ERROR: --timeout 须在 600–86400 秒之间（收到: $t）。" >&2
        echo "       服务端下限 600：小于它的值会被**静默改成 600**，调用方仍收到 success。" >&2
        return 1
    fi
    return 0
}

# 校验 retryCount 合法区间（S165·第二轮实测补齐）
# 合法区间 **0–10**，不是 uint8 的 0–255。实测三次才摸到真天花板：
#   -1   本地拦下（非无符号整数）
#   0    合法，且 update 通道能真的设成 0（回读得到 0）
#   255  服务端 400：`retry count must be <= 10`（字段虽是 uint8，业务上限另有约束）
#   999  服务端 400：`cannot unmarshal number 999 into ... retryCount of type uint8`
# 也就是说：**uint8 的字面范围和业务上限差了一个数量级**，光看报错猜不出来。
# 另外 **create 通道不认 0**：实测 `create --retry-count 0` 回读是 3（默认值），
# 不报错不警告——静默改写。create 侧由 verify_created_params 回读兜住。
validate_retry() {
    local r="$1"
    if ! [[ "$r" =~ ^[0-9]+$ ]]; then
        echo "ERROR: --retry-count 必须是 0–10 的整数（收到: $r）" >&2
        return 1
    fi
    if [ "$r" -gt 10 ]; then
        echo "ERROR: --retry-count 最大 10（收到: $r）。" >&2
        echo "       服务端业务上限是 10（不是 uint8 的 255）：retry count must be <= 10。" >&2
        return 1
    fi
    return 0
}

# 打印帮助信息
print_help() {
    cat << 'EOF'
Usage: scheduler-api.sh <action> [options]

Actions:
  create    创建新任务 (必填: --name, --cron, --prompt, --frequency-type)
  list      获取任务列表 (可选: --page, --page-size, --status, --keyword)
  get       获取任务详情 (必填: --id)
  update    更新任务 (必填: --id, 可选: --name, --cron, --prompt, --status 等)
  delete    删除任务 (必填: --id)

Frequency Types:
  daily     每天执行 (每天X点、工作日、周末、每周X)
  interval  按间隔执行 (每X分钟、每X小时)
  once      单次执行 (指定日期时间只执行一次)

Examples:
  scheduler-api.sh create --name "日报提醒" --cron "0 0 21 * * *" --prompt "提醒写日报" --frequency-type daily
  scheduler-api.sh create --name "状态检查" --cron "0 0 * * * *" --prompt "检查服务状态" --frequency-type interval
  scheduler-api.sh list --status 1
  scheduler-api.sh update --id 123 --status 0
  scheduler-api.sh delete --id 123
EOF
}

# 默认 API 地址
DEFAULT_SCHEDULER_API_BASE_URL="http://auth.proxy/codebuddy"

# 检查环境变量，自动从 ACC_PRODUCT_CONFIG_V3 读取
check_env() {
    if [ -z "$SCHEDULER_API_BASE_URL" ]; then
        if [ -n "$ACC_PRODUCT_CONFIG_V3" ] && command -v jq &> /dev/null; then
            SCHEDULER_API_BASE_URL=$(echo "$ACC_PRODUCT_CONFIG_V3" | jq -r '.endpoint // empty')
        fi
    fi

    # 如果仍然为空，使用默认值
    if [ -z "$SCHEDULER_API_BASE_URL" ]; then
        SCHEDULER_API_BASE_URL="$DEFAULT_SCHEDULER_API_BASE_URL"
    fi

    export SCHEDULER_API_BASE_URL
}

# 格式化 JSON 输出
format_json() {
    if command -v jq &> /dev/null; then
        jq '.'
    else
        cat
    fi
}

# 校验 Cron 秒位必须为 0
validate_cron_seconds() {
    local cron_expr="$1"
    local cron_sec
    cron_sec=$(echo "$cron_expr" | awk '{print $1}')
    if [ "$cron_sec" != "0" ]; then
        echo "ERROR: 不允许秒级定时任务，Cron 秒位必须为 0（当前: $cron_sec）" >&2
        exit 1
    fi
}

# 助理调试态沙箱（构建 / 预览）：env 与正式沙箱一致，只能按 origin 排除，
# 否则调试会话的试玩指令会被建成真实定时任务。
is_debug_agent_session() {
    case "$CLIENT_INFO_USER_AGENT_EXTENSION" in
        personal-cloudagents-build|personal-cloudagents-preview|AgentDebug|quickstart)
            return 0 ;;
    esac
    return 1
}

# 发送 HTTP 请求并处理响应
do_request() {
    local method="$1"
    local url="$2"
    local data="$3"

    local curl_args=(-s -w "\n%{http_code}" -X "$method" -H "Content-Type: application/json")
    # User-Agent 标识沙箱 agent 来源（后端 detectSourceFromUserAgent 兜底探测用）
    curl_args+=(-H "User-Agent: WorkBuddy-Agent/1.0 (sandbox-skill; automation-task-manager)")
    # 必须传 X-Enterprise-Id Header
    if [ -n "$X_ENTERPRISE_ID" ]; then
        curl_args+=(-H "X-Enterprise-Id: $X_ENTERPRISE_ID")
    fi
    # 标识任务来源为 AI 对话创建（后端据此上报 source=chat 事件）
    curl_args+=(-H "X-Task-Source: chat")
    if [ -n "$data" ]; then
        curl_args+=(-d "$data")
    fi

    local response
    response=$(curl "${curl_args[@]}" "$url")

    local http_code
    http_code=$(echo "$response" | tail -n 1)
    local body
    body=$(echo "$response" | sed '$d')

    if [ "$http_code" -ge 200 ] && [ "$http_code" -lt 300 ]; then
        echo "$body" | format_json
    else
        echo "FAILED (HTTP $http_code)" >&2
        echo "$body" | format_json >&2
        exit 1
    fi
}

# 创建任务
create_task() {
    local name="" description="" cron_expr="" prompt=""
    local timezone="$DEFAULT_TIMEZONE" timeout_sec="$DEFAULT_TIMEOUT_SEC" retry_count="$DEFAULT_RETRY_COUNT"
    local frequency_type="" effective_start="" effective_end=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            --name) name="$2"; shift 2 ;;
            --description) description="$2"; shift 2 ;;
            --cron) cron_expr="$2"; shift 2 ;;
            --prompt) prompt="$2"; shift 2 ;;
            --timezone) timezone="$2"; shift 2 ;;
            --timeout) timeout_sec="$2"; shift 2 ;;
            --retry-count) retry_count="$2"; shift 2 ;;
            --frequency-type) frequency_type="$2"; shift 2 ;;
            --effective-start) effective_start="$2"; shift 2 ;;
            --effective-end) effective_end="$2"; shift 2 ;;
            *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    # 验证必填字段
    [ -z "$name" ] && { echo "ERROR: --name is required" >&2; exit 1; }
    [ -z "$cron_expr" ] && { echo "ERROR: --cron is required" >&2; exit 1; }
    [ -z "$prompt" ] && { echo "ERROR: --prompt is required" >&2; exit 1; }
    [ -z "$frequency_type" ] && { echo "ERROR: --frequency-type is required (daily/interval/once)" >&2; exit 1; }

    # 验证 frequency_type 值
    case "$frequency_type" in
        daily|interval|once) ;;
        *) echo "ERROR: --frequency-type must be one of: daily, interval, once" >&2; exit 1 ;;
    esac

    validate_cron_seconds "$cron_expr" || exit 1
    validate_timeout "$timeout_sec" || exit 1
    validate_retry "$retry_count" || exit 1

    # 构建 JSON（source=agent 标识来源为沙箱 agent skill 创建）
    local json_body
    json_body=$(jq -n \
        --arg name "$name" \
        --arg description "$description" \
        --arg cronExpr "$cron_expr" \
        --arg timezone "$timezone" \
        --arg prompt "$prompt" \
        --arg frequencyType "$frequency_type" \
        --argjson timeoutSec "$timeout_sec" \
        --argjson retryCount "$retry_count" \
        '{name: $name, description: $description, cronExpr: $cronExpr, timezone: $timezone, frequencyType: $frequencyType, agentConfig: {prompt: $prompt}, timeoutSec: $timeoutSec, retryCount: $retryCount, source: "agent"}')

    [ -n "$effective_start" ] && json_body=$(echo "$json_body" | jq --arg v "$effective_start" '. + {effectiveStart: $v}')
    [ -n "$effective_end" ] && json_body=$(echo "$json_body" | jq --arg v "$effective_end" '. + {effectiveEnd: $v}')

    # ownerType / agentType 注入策略（自上而下，命中即停）：
    #   1. 频道助理沙箱 → channel_assistant + ownerType=team + projectId=频道 ID，
    #      结果投递回该频道。频道由 Teams 项目升级而来，频道 ID 即 project_id
    #      （同一张 t_teams_projects 表），因此可直接复用 X_PROJECT_ID。
    #   2. 个人助理沙箱 → personal_agent + ownerType=personal，不配 channel_id，
    #      服务端回落为单聊投递给任务创建者本人（收件人不开放配置，防伪造）。
    #   3. 渠道预置助理沙箱（企微助理等）→ personal_agent_wecom + ownerType=personal，
    #      投递同分支 2。它 origin 是 exclusive / a2a、也没有 projectId，
    #      故既不命中分支 1/2 也不命中分支 4，需单独一条。
    #   4. Teams 项目会话 → ownerType=team + projectId（老行为）。
    #   5. bot channel env 齐全 → ownerType=enterprise + open_sandbox_agent（老行为）。
    #   6. 都没有 → 不注入，走 personal 默认行为。
    #
    # 分支 1/2 用 origin 白名单判定，不能用「CODEBUDDY_PERSONAL_EXPERT_ID 是否非空」：
    # 构建模式（personal-cloudagents-build）与 TestRun 预览
    # （personal-cloudagents-preview）沙箱同样带该 env，按非空判定会把调试态会话
    # 误建成正式定时任务。CLIENT_INFO_USER_AGENT_EXTENSION 的值就是 conversationOrigin，
    # 由 agentserver 装配 manifest 时写入，可精确区分这四种沙箱。
    # 分支 3 判据 WORKBUDDY_CHANNEL_KEY 也存在于调试态沙箱，故另加 is_debug_agent_session。
    #
    # CODEBUDDY_PERSONAL_EXPERT_ID 协议名保留 EXPERT_ID 历史命名，存的是助理 agent id，
    # 正好是 cloud_agent_id 与 im_channel options.agent_id 需要的值。
    if [ "$CLIENT_INFO_USER_AGENT_EXTENSION" = "channel-workbuddy" ] \
        && [ -n "$CODEBUDDY_PERSONAL_EXPERT_ID" ] && [ -n "$X_PROJECT_ID" ]; then
        # 频道助理任务：投递回当前频道
        json_body=$(echo "$json_body" | jq \
            --arg agentId "$CODEBUDDY_PERSONAL_EXPERT_ID" \
            --arg channelId "$X_PROJECT_ID" \
            '.agentConfig.cloud_agent_id = $agentId
             | . + {agentType: "channel_assistant", ownerType: "team", projectId: $channelId,
                    deliveryConfig: {enabled: true, channels: [{type: "im_channel", enabled: true,
                        options: {agent_id: $agentId, channel_id: $channelId}}]}}')
    elif [ "$CLIENT_INFO_USER_AGENT_EXTENSION" = "assistant" ] \
        && [ -n "$CODEBUDDY_PERSONAL_EXPERT_ID" ]; then
        # 个人助理任务：不带 channel_id，服务端单聊回投创建者本人
        json_body=$(echo "$json_body" | jq \
            --arg agentId "$CODEBUDDY_PERSONAL_EXPERT_ID" \
            '.agentConfig.cloud_agent_id = $agentId
             | . + {agentType: "personal_agent", ownerType: "personal",
                    deliveryConfig: {enabled: true, channels: [{type: "im_channel", enabled: true,
                        options: {agent_id: $agentId}}]}}')
    elif [ -n "$WORKBUDDY_CHANNEL_KEY" ] && [ -n "$CODEBUDDY_PERSONAL_EXPERT_ID" ] \
        && ! is_debug_agent_session; then
        # 渠道预置助理任务。判据 WORKBUDDY_CHANNEL_KEY 与服务端一致（agentRole 被刻意
        # 设为 specialist，区分不出来）。
        # 投递同分支 2：不带 channel_id → 服务端单聊回投创建者本人。
        json_body=$(echo "$json_body" | jq \
            --arg agentId "$CODEBUDDY_PERSONAL_EXPERT_ID" \
            '.agentConfig.cloud_agent_id = $agentId
             | . + {agentType: "personal_agent_wecom", ownerType: "personal",
                    deliveryConfig: {enabled: true, channels: [{type: "im_channel", enabled: true,
                        options: {agent_id: $agentId}}]}}')
    elif [ -n "$X_PROJECT_ID" ]; then
        # Teams 项目任务：ownerType=team + projectId（互斥，不附加 bot channel）
        json_body=$(echo "$json_body" | jq \
            --arg projectId "$X_PROJECT_ID" \
            '. + {ownerType: "team", projectId: $projectId}')
    elif [ -n "$CODEBUDDY_BOT_CHANNEL_TYPE" ] && [ -n "$CODEBUDDY_BOT_CHANNEL_ID" ] && [ -n "$CODEBUDDY_BOT_RECIPIENT_ID" ] && [ -n "$CODEBUDDY_BOT_CHANNEL_BINDING_ID" ]; then
        # Bot channel 触发：ownerType=enterprise（保持原有行为）
        json_body=$(echo "$json_body" | jq \
            --arg channelType "$CODEBUDDY_BOT_CHANNEL_TYPE" \
            --arg channelId "$CODEBUDDY_BOT_CHANNEL_ID" \
            --arg recipientId "$CODEBUDDY_BOT_RECIPIENT_ID" \
            --arg bindingId "$CODEBUDDY_BOT_CHANNEL_BINDING_ID" \
            '. + {ownerType: "enterprise", agentType: "open_sandbox_agent", deliveryConfig: {type: "bot_channel", enabled: true, channels: [{type: "bot_channel", enabled: true, options: {channel_type: $channelType, channel_id: $channelId, recipient_id: $recipientId, channel_binding_id: $bindingId}}]}}')
    fi

    do_request POST "$SCHEDULER_API_BASE_URL/v2/as/scheduler/tasks" "$json_body"
    local _rc=$?
    [ "$_rc" -ne 0 ] && return "$_rc"

    # 建完立刻回读，核对「我要求的值」和「服务端真存的值」是否一致（S165·第二轮）。
    # 起因：`create --retry-count 0` 返回 success，回读却是 3——服务端把 0 当零值
    # 忽略了（Go 的 omitempty 语义）。timeout 同理有下限截断。
    # 这类静默改写只有回读能发现，所以别省这一次 GET。
    verify_created_params "$name" "$timeout_sec" "$retry_count"
}

# 回读刚建的任务，比对 timeoutSec / retryCount；不一致就明说差在哪
verify_created_params() {
    local name="$1" want_timeout="$2" want_retry="$3"
    local list_json id got_timeout got_retry
    list_json=$(do_get_list_for_verify "$name") || return 0
    id=$(echo "$list_json" | jq -r --arg n "$name" '(.data.tasks // [])[] | select(.name==$n) | .id' 2>/dev/null | head -1)
    [ -z "$id" ] && return 0

    local detail
    detail=$(do_get_task "$id" 2>/dev/null) || return 0
    got_timeout=$(echo "$detail" | jq -r '.data.timeoutSec // empty' 2>/dev/null)
    got_retry=$(echo "$detail" | jq -r '.data.retryCount // empty' 2>/dev/null)

    local drift=""
    [ -n "$got_timeout" ] && [ "$got_timeout" != "$want_timeout" ] && \
        drift="${drift}　timeoutSec: 要求 $want_timeout → 实际 $got_timeout"$'\n'
    [ -n "$got_retry" ] && [ "$got_retry" != "$want_retry" ] && \
        drift="${drift}　retryCount: 要求 $want_retry → 实际 $got_retry"$'\n'

    if [ -n "$drift" ]; then
        echo "" >&2
        echo "⚠️  参数被服务端改写（任务 $id 已创建，但存进去的不是你要的值）：" >&2
        printf '%s' "$drift" >&2
        echo "   create 把 0 / 超下限的值当零值忽略，回落默认值。要精确控制就建完再 update。" >&2
        echo "   例：bash $0 update --id $id --retry-count $want_retry" >&2
        return 3
    fi
    return 0
}

# 获取任务列表
list_tasks() {
    local page=1 page_size="$DEFAULT_PAGE_SIZE" status="" keyword=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            --page) page="$2"; shift 2 ;;
            --page-size) page_size="$2"; shift 2 ;;
            --status) status="$2"; shift 2 ;;
            --keyword) keyword="$2"; shift 2 ;;
            *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    local query="page=$page&pageSize=$page_size"
    [ -n "$status" ] && query="$query&status=$status"
    [ -n "$keyword" ] && query="$query&keyword=$keyword"

    # 过滤策略与 create 的判定逐条对齐，保证"建了就能查到"：
    #   1. 频道助理沙箱 → channel_assistant + team + projectId + cloudAgentId
    #   2. 个人助理沙箱 → personal_agent + personal + cloudAgentId
    #   3. 渠道预置助理沙箱 → personal_agent_wecom + personal + cloudAgentId
    #   4. Teams 项目会话 → ownerType=team + projectId（老行为）
    #   5. bot channel env 齐全 → enterprise + open_sandbox_agent（老行为）
    #   6. 都没有 → 不附加，走后端默认（personal + sandbox_agent）
    #
    # 前三个分支必须显式带 agentType：后端 agentType 缺省时只返回 sandbox_agent，
    # 不传会导致刚建好的助理/频道任务在列表里查不到。
    # cloudAgentId 进一步收窄到当前助理，避免串出同一用户其它助理的任务。
    if [ "$CLIENT_INFO_USER_AGENT_EXTENSION" = "channel-workbuddy" ] \
        && [ -n "$CODEBUDDY_PERSONAL_EXPERT_ID" ] && [ -n "$X_PROJECT_ID" ]; then
        query="$query&agentType=channel_assistant&ownerType=team&projectId=$X_PROJECT_ID"
        query="$query&cloudAgentId=$CODEBUDDY_PERSONAL_EXPERT_ID"
    elif [ "$CLIENT_INFO_USER_AGENT_EXTENSION" = "assistant" ] \
        && [ -n "$CODEBUDDY_PERSONAL_EXPERT_ID" ]; then
        query="$query&agentType=personal_agent&ownerType=personal"
        query="$query&cloudAgentId=$CODEBUDDY_PERSONAL_EXPERT_ID"
    elif [ -n "$WORKBUDDY_CHANNEL_KEY" ] && [ -n "$CODEBUDDY_PERSONAL_EXPERT_ID" ] \
        && ! is_debug_agent_session; then
        query="$query&agentType=personal_agent_wecom&ownerType=personal"
        query="$query&cloudAgentId=$CODEBUDDY_PERSONAL_EXPERT_ID"
    elif [ -n "$X_PROJECT_ID" ]; then
        query="$query&ownerType=team&projectId=$X_PROJECT_ID"
    elif [ -n "$CODEBUDDY_BOT_CHANNEL_TYPE" ] && [ -n "$CODEBUDDY_BOT_CHANNEL_ID" ] && [ -n "$CODEBUDDY_BOT_RECIPIENT_ID" ] && [ -n "$CODEBUDDY_BOT_CHANNEL_BINDING_ID" ]; then
        query="$query&ownerType=enterprise&agentType=open_sandbox_agent"
    fi

    do_request GET "$SCHEDULER_API_BASE_URL/v2/as/scheduler/tasks?$query"
}

# 获取任务详情
get_task() {
    local task_id=""
    while [[ $# -gt 0 ]]; do
        case $1 in
            --id) task_id="$2"; shift 2 ;;
            *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    [ -z "$task_id" ] && { echo "ERROR: --id is required" >&2; exit 1; }

    do_request GET "$SCHEDULER_API_BASE_URL/v2/as/scheduler/tasks/$task_id"
}

# 更新任务
update_task() {
    local task_id="" name="" description="" cron_expr="" prompt=""
    local timezone="" timeout_sec="" retry_count="" status=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            --id) task_id="$2"; shift 2 ;;
            --name) name="$2"; shift 2 ;;
            --description) description="$2"; shift 2 ;;
            --cron) cron_expr="$2"; shift 2 ;;
            --prompt) prompt="$2"; shift 2 ;;
            --timezone) timezone="$2"; shift 2 ;;
            --timeout) timeout_sec="$2"; shift 2 ;;
            --retry-count) retry_count="$2"; shift 2 ;;
            --status) status="$2"; shift 2 ;;
            *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    [ -z "$task_id" ] && { echo "ERROR: --id is required" >&2; exit 1; }

    [ -n "$cron_expr" ] && { validate_cron_seconds "$cron_expr" || exit 1; }
    [ -n "$timeout_sec" ] && { validate_timeout "$timeout_sec" || exit 1; }
    [ -n "$retry_count" ] && { validate_retry "$retry_count" || exit 1; }

    # 构建 JSON（增量添加非空字段）
    local json_body="{}"
    [ -n "$name" ] && json_body=$(echo "$json_body" | jq --arg v "$name" '. + {name: $v}')
    [ -n "$description" ] && json_body=$(echo "$json_body" | jq --arg v "$description" '. + {description: $v}')
    [ -n "$cron_expr" ] && json_body=$(echo "$json_body" | jq --arg v "$cron_expr" '. + {cronExpr: $v}')
    [ -n "$prompt" ] && json_body=$(echo "$json_body" | jq --arg v "$prompt" '. + {agentConfig: {prompt: $v}}')
    [ -n "$timezone" ] && json_body=$(echo "$json_body" | jq --arg v "$timezone" '. + {timezone: $v}')
    [ -n "$timeout_sec" ] && json_body=$(echo "$json_body" | jq --argjson v "$timeout_sec" '. + {timeoutSec: $v}')
    [ -n "$retry_count" ] && json_body=$(echo "$json_body" | jq --argjson v "$retry_count" '. + {retryCount: $v}')
    [ -n "$status" ] && json_body=$(echo "$json_body" | jq --argjson v "$status" '. + {status: $v}')

    if [ "$(echo "$json_body" | jq 'length')" -eq 0 ]; then
        echo "ERROR: No update fields provided" >&2
        exit 1
    fi

    do_request PUT "$SCHEDULER_API_BASE_URL/v2/as/scheduler/tasks/$task_id" "$json_body"
}

# 删除任务
delete_task() {
    local task_id=""
    while [[ $# -gt 0 ]]; do
        case $1 in
            --id) task_id="$2"; shift 2 ;;
            *) echo "ERROR: Unknown option: $1" >&2; exit 1 ;;
        esac
    done

    [ -z "$task_id" ] && { echo "ERROR: --id is required" >&2; exit 1; }

    do_request DELETE "$SCHEDULER_API_BASE_URL/v2/as/scheduler/tasks/$task_id"
}

# 回读辅助：按名字查列表（供 verify_created_params 用，不打印任何东西）
do_get_list_for_verify() {
    local keyword="$1" body http_code
    body=$(curl -s -w $'\n%{http_code}' \
        -X GET "$SCHEDULER_API_BASE_URL/v2/as/scheduler/tasks?page=1&pageSize=50" \
        -H "Content-Type: application/json" \
        -H "User-Agent: WorkBuddy-Agent/1.0 (sandbox-skill; automation-task-manager)" \
        ${X_ENTERPRISE_ID:+-H "X-Enterprise-Id: $X_ENTERPRISE_ID"} 2>/dev/null) || return 1
    http_code=$(echo "$body" | tail -n 1)
    [ "$http_code" -ge 200 ] 2>/dev/null && [ "$http_code" -lt 300 ] 2>/dev/null || return 1
    echo "$body" | sed '$d'
}

# 回读辅助：按 id 查详情（静默）
do_get_task() {
    local task_id="$1" body http_code
    body=$(curl -s -w $'\n%{http_code}' \
        -X GET "$SCHEDULER_API_BASE_URL/v2/as/scheduler/tasks/$task_id" \
        -H "Content-Type: application/json" \
        -H "User-Agent: WorkBuddy-Agent/1.0 (sandbox-skill; automation-task-manager)" \
        ${X_ENTERPRISE_ID:+-H "X-Enterprise-Id: $X_ENTERPRISE_ID"} 2>/dev/null) || return 1
    http_code=$(echo "$body" | tail -n 1)
    [ "$http_code" -ge 200 ] 2>/dev/null && [ "$http_code" -lt 300 ] 2>/dev/null || return 1
    echo "$body" | sed '$d'
}

# 主函数
main() {
    if [ $# -eq 0 ]; then
        print_help
        exit 0
    fi

    local action="$1"
    shift

    case "$action" in
        create) check_env; create_task "$@" ;;
        list)   check_env; list_tasks "$@" ;;
        get)    check_env; get_task "$@" ;;
        update) check_env; update_task "$@" ;;
        delete) check_env; delete_task "$@" ;;
        help|--help|-h) print_help ;;
        *) echo "ERROR: Unknown action: $action" >&2; print_help; exit 1 ;;
    esac
    # 关键：把子命令的退出码传出去（S165·第二轮）。
    # 原来每个分支都是裸调用，函数返回值被 case 吞掉，main 直接掉到最后一行
    # → 整个脚本恒 exit 0。于是「ERROR: --retry-count 必须是整数」打印了、
    # 调用方 `$?` 却是 0——和账房那边一模一样的「判定对了但没交到下游」。
    return $?
}

main "$@"
