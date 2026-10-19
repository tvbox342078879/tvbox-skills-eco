#!/usr/bin/env bash
# =====================================================================
# check.sh - computer-use 技能一键检查脚本
# Harness Engineering 原则 5：架构约束优先机械化
#
# 用法:
#   bash check.sh [command]
#
# 命令:
#   syntax   - Python 语法检查（AST）
#   lint     - flake8 代码风格检查
#   type     - mypy 类型检查
#   test     - pytest 运行所有测试
#   version  - VERSION 一致性检查
#   actions  - Action 注册表一致性检查
#   skill-md - SKILL.md 与注册表一致性检查（参数签名级）
#   all      - 执行 syntax + lint + test（默认）
#
# 返回码: 0=通过, 1=失败
# =====================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ok() { echo -e "${GREEN}✅ $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; }
info() { echo -e "${YELLOW}⏳ $1${NC}"; }

cmd_syntax() {
    info "Python AST 语法检查..."
    python3 -c "
import ast, sys, pathlib

errors = []
for f in sorted(pathlib.Path('.').rglob('*.py')):
    # 跳过 __pycache__
    if '__pycache__' in str(f):
        continue
    try:
        ast.parse(f.read_text(), filename=str(f))
    except SyntaxError as e:
        errors.append(f'{f}: {e}')

if errors:
    for e in errors:
        print(f'  ❌ {e}')
    sys.exit(1)
else:
    print(f'  All Python files pass AST syntax check')
"
    ok "语法检查通过"
}

cmd_lint() {
    info "flake8 代码风格检查..."
    python3 -m flake8 modules/ computer_tool.py
    ok "Lint 检查通过"
}

cmd_type() {
    info "mypy 类型检查..."
    python3 -m mypy modules/ computer_tool.py --ignore-missing-imports
    ok "类型检查通过"
}

cmd_test() {
    info "pytest 运行测试..."
    python3 -m pytest tests/ -v --tb=short
    ok "所有测试通过"
}

cmd_version() {
    info "VERSION 一致性检查..."
    python3 -m pytest tests/test_consistency.py -k "TestVersionConsistency" -v --tb=short
    ok "VERSION 一致性通过"
}

cmd_actions() {
    info "Action 注册表一致性检查..."
    python3 -m pytest tests/test_consistency.py -k "TestActionConsistency" -v --tb=short
    ok "Action 一致性通过"
}

cmd_skill_md() {
    info "SKILL.md 与注册表一致性检查（参数签名级）..."
    python3 generate_action_docs.py --verify
    python3 -m pytest tests/test_consistency.py -k "TestSkillMdStructure or TestActionMetadata" -v --tb=short
    ok "SKILL.md 一致性通过"
}

cmd_all() {
    echo "=========================================="
    echo " computer-use skill 全量检查"
    echo "=========================================="
    echo ""

    local failed=0

    cmd_syntax || { fail "语法检查失败"; failed=1; }
    echo ""
    cmd_lint || { fail "Lint 检查失败"; failed=1; }
    echo ""
    cmd_test || { fail "测试失败"; failed=1; }
    echo ""

    if [ "$failed" -eq 0 ]; then
        echo "=========================================="
        ok "全量检查通过 🎉"
        echo "=========================================="
    else
        echo "=========================================="
        fail "存在检查失败项"
        echo "=========================================="
        exit 1
    fi
}

case "${1:-all}" in
    syntax)   cmd_syntax ;;
    lint)     cmd_lint ;;
    type)     cmd_type ;;
    test)     cmd_test ;;
    version)  cmd_version ;;
    actions)  cmd_actions ;;
    skill-md) cmd_skill_md ;;
    all)      cmd_all ;;
    *)
        echo "用法: bash check.sh [syntax|lint|type|test|version|actions|skill-md|all]"
        exit 1
        ;;
esac
