# -*- coding: utf-8 -*-
"""wechat-article-publisher 硬卡口（safe_edit.py）。

把「改完必须跑回归」从软约定升级为物理强制：

  python safe_edit.py                 # 跑回归测试（等价于 tests/run_regression.py）
  python safe_edit.py --install-hook # 一键装 git pre-commit，以后 commit 自动跑回归

设计目的：
  - 手动：改完直接 python safe_edit.py 验证，绿了才算完。
  - 自动：--install-hook 在 .git/hooks/pre-commit 写入调用本脚本的逻辑，
    git commit 时若回归不过则 exit 1，提交被阻止——agent 想绕过也绕不过。
  - 分发恢复：发出去的 zip 不含 .git，拿到的人运行
    python safe_edit.py --install-hook 即可在本机恢复硬卡口。

注意：pre-commit hook 用「纯 Windows 绝对路径 + MSYS2_ARG_CONV_EXCL 禁用路径转换」
生成，规避 Git Bash 路径自动转换把 /c/ 风格路径误处理的坑。
"""
import io, os, subprocess, sys

SKILL = os.path.dirname(os.path.abspath(__file__))
REGRESSION = os.path.join(SKILL, "tests", "run_regression.py")
HOOK_DIR = os.path.join(SKILL, ".git", "hooks")


def _hook_content():
    """生成 pre-commit 内容（纯 Windows 绝对路径，禁 MSYS 转换）。"""
    # os.path.join 在 Windows 上产生反斜杠路径，直接注入 bash 单引号变量，
    # 既避开 Python 侧反斜杠转义，又让 bash 原样接收（MSYS 也不转换 X:\ 形式）。
    python_bin = os.path.join(SKILL, "..", "..", "binaries",
                              "python", "envs", "default", "Scripts", "python.exe")
    safe_edit = os.path.join(SKILL, "safe_edit.py")
    lines = [
        "#!/bin/sh",
        "# wechat-article-publisher 硬卡口：commit 前强制跑回归，不过则阻止提交。",
        'export MSYS2_ARG_CONV_EXCL="*"',               # 禁用 MSYS 参数路径自动转换
        "PYTHON='" + python_bin + "'",                  # bash 单引号包 Windows 绝对路径
        'if [ ! -f "$PYTHON" ]; then',
        '  PYTHON="$(command -v python3 2>/dev/null || command -v python 2>/dev/null)"',
        "fi",
        'if [ -z "$PYTHON" ]; then',
        '  echo "未找到 python，无法跑回归，提交中止。请先安装 python 或运行 python safe_edit.py 排查。"',
        "  exit 1",
        "fi",
        "\"$PYTHON\" '" + safe_edit + "'",           # 用 $PYTHON 解释器跑 safe_edit.py
        "exit $?",
        "",
    ]
    return "\n".join(lines)


def run_regression():
    print(">>> 运行回归测试（tests/run_regression.py）...")
    r = subprocess.run([sys.executable, REGRESSION],
                       cwd=os.path.join(SKILL, "tests"))
    return r.returncode


def install_hook():
    if not os.path.isdir(os.path.join(SKILL, ".git")):
        print(">>> git init ...")
        subprocess.run(["git", "init"], cwd=SKILL, check=True)
    os.makedirs(HOOK_DIR, exist_ok=True)
    hook_path = os.path.join(HOOK_DIR, "pre-commit")
    io.open(hook_path, "w", encoding="utf-8", newline="\n").write(_hook_content())
    try:
        os.chmod(hook_path, 0o755)
    except Exception:
        pass
    print(">>> 已安装 pre-commit hook:", hook_path)
    print(">>> 以后任何 git commit 都会自动跑回归，不过则阻止提交。")
    print(">>> 立即验证：python safe_edit.py")


def main():
    if "--install-hook" in sys.argv:
        install_hook()
        return
    rc = run_regression()
    sys.exit(rc)


if __name__ == "__main__":
    main()
