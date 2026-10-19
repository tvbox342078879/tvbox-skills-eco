#!/bin/bash

# 通用 OAuth Token 获取脚本
# 用法: source ./get_token.sh <platform>
# 平台: github | cnb | figma | gongfeng

set -e

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

# API 配置（走 agent-gateway 域名路由）
GATEWAY_SUFFIX="agent-gateway.auth-proxy.local"
MAX_RETRIES=3
RETRY_DELAY=2

# 支持的平台
SUPPORTED_PLATFORMS=("github" "cnb" "figma" "gongfeng")

# 参数检查
PLATFORM="$1"
if [ -z "$PLATFORM" ]; then
    echo -e "${RED}用法: source $0 <platform>${NC}"
    echo "支持: ${SUPPORTED_PLATFORMS[*]}"
    return 1 2>/dev/null || exit 1
fi

# 验证平台
VALID=false
for p in "${SUPPORTED_PLATFORMS[@]}"; do
    [ "$PLATFORM" = "$p" ] && VALID=true && break
done

if [ "$VALID" = false ]; then
    echo -e "${RED}不支持的平台: $PLATFORM${NC}"
    echo "支持: ${SUPPORTED_PLATFORMS[*]}"
    return 1 2>/dev/null || exit 1
fi

# 确定环境变量名和 connector name 候选列表（按优先级排列）
case "$PLATFORM" in
    github)   ENV_VAR="GITHUB_TOKEN";   CONNECTOR_NAMES=("github") ;;
    cnb)      ENV_VAR="CNB_TOKEN";      CONNECTOR_NAMES=("cnb") ;;
    figma)    ENV_VAR="FIGMA_TOKEN";    CONNECTOR_NAMES=("figma") ;;
    gongfeng) ENV_VAR="GONGFENG_TOKEN"; CONNECTOR_NAMES=("enterprise_gongfeng" "gongfeng") ;;
esac

# 单次请求（不重试，供 fetch_token 内部调用）
try_fetch() {
    local connector="$1"
    local url="http://${connector}.${GATEWAY_SUFFIX}/_internal/accesstoken"
    local resp=$(curl -s -w "\n%{http_code}" -X GET "$url" \
        -H "Content-Type: application/json" \
        --connect-timeout 10 --max-time 30 2>&1)
    local code=$(echo "$resp" | tail -n1)
    local body=$(echo "$resp" | sed '$d')
    echo "${code}|${body}"
}

# API 请求：按候选列表依次尝试，每个 connector 支持重试
fetch_token() {
    for connector in "${CONNECTOR_NAMES[@]}"; do
        local retry=0
        while [ $retry -lt $MAX_RETRIES ]; do
            local result=$(try_fetch "$connector")
            local code="${result%%|*}"
            local body="${result#*|}"

            if [ "$code" = "200" ]; then
                echo "$body"
                return 0
            elif [ "$code" = "401" ] || [ "$code" = "403" ]; then
                # 该 connector 授权无效，尝试下一个
                break
            elif [ "$code" -ge "500" ] || [ "$code" = "429" ]; then
                retry=$((retry + 1))
                [ $retry -lt $MAX_RETRIES ] && sleep $RETRY_DELAY
            else
                # 其他错误（如 404 token 不存在），尝试下一个
                break
            fi
        done
    done

    echo -e "${RED}授权失败 - 请前往 CodeBuddy 设置页面重新授权工蜂连接器${NC}" >&2
    return 1
}

# 获取 Token
RESPONSE=$(fetch_token) || { return 1 2>/dev/null || exit 1; }

# 解析 Token
ACCESS_TOKEN=$(echo "$RESPONSE" | grep -o '"access_token"[[:space:]]*:[[:space:]]*"[^"]*' | sed 's/.*"access_token"[[:space:]]*:[[:space:]]*"//')

if [ -z "$ACCESS_TOKEN" ]; then
    echo -e "${RED}Token 解析失败${NC}" >&2
    return 1 2>/dev/null || exit 1
fi

# 设置环境变量
export "${ENV_VAR}=${ACCESS_TOKEN}"

# 更新 shell 配置
SHELL_RC="$HOME/.zshrc"
[ -f "$HOME/.bashrc" ] && [ ! -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.bashrc"

touch "$SHELL_RC"
if grep -q "^export ${ENV_VAR}=" "$SHELL_RC"; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s|^export ${ENV_VAR}=.*|export ${ENV_VAR}=\"${ACCESS_TOKEN}\"|" "$SHELL_RC"
    else
        sed -i "s|^export ${ENV_VAR}=.*|export ${ENV_VAR}=\"${ACCESS_TOKEN}\"|" "$SHELL_RC"
    fi
else
    echo "export ${ENV_VAR}=\"${ACCESS_TOKEN}\"" >> "$SHELL_RC"
fi

# 简洁输出
TOKEN_MASKED="${ACCESS_TOKEN:0:8}...${ACCESS_TOKEN: -4}"
echo -e "${GREEN}✓ ${ENV_VAR}=${TOKEN_MASKED}${NC}"
