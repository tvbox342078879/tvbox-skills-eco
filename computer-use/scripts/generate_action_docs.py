#!/usr/bin/env python3
"""
generate_action_docs.py - 从注册表自动生成 SKILL.md 的 Action 文档片段

Harness Engineering 原则 5（架构约束优先机械化）的实现：
注册表是 Action 文档的唯一事实源（Single Source of Truth）。

用法:
    # 生成 action 表格（Markdown 格式）
    python3 generate_action_docs.py

    # 验证模式：检查 SKILL.md 中的 action 表格是否与注册表一致
    python3 generate_action_docs.py --verify

    # 更新模式：直接将生成的 action 表格写入 SKILL.md
    python3 generate_action_docs.py --update
"""

from __future__ import annotations

import re
import sys
from collections import OrderedDict
from pathlib import Path

# 确保 modules 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent))

from modules.registry import get_registry, ActionSpec
from modules.core import VERSION

# SKILL.md 中 action 表格的边界标记
BEGIN_MARKER = "<!-- BEGIN_ACTION_TABLES (auto-generated, do not edit) -->"
END_MARKER = "<!-- END_ACTION_TABLES -->"

# 分类排序顺序（按文档中的逻辑展示顺序）
CATEGORY_ORDER = [
    "基础操作",
    "窗口管理",
    "剪贴板",
    "等待条件",
    "OCR 文字识别",
    "Web 导航辅助（Legacy）",
    "Layer 1: Playwright 浏览器操控",
    "Layer 2: AXTree 语义感知",
    "分辨率",
    "音频",
    "屏幕录制",
]


def _format_params(spec: ActionSpec) -> str:
    """格式化参数列，required 不带方括号，optional 带方括号"""
    parts = []
    for p in spec.required_params:
        parts.append(p)
    for p, default in spec.optional_params.items():
        parts.append(f"[{p}]")
    return ", ".join(parts) if parts else "无"


def generate_action_tables() -> str:
    """从注册表生成按 category 分组的 Markdown Action 表格"""
    registry = get_registry()

    # 按 category 分组，保持 CATEGORY_ORDER 排序
    grouped: OrderedDict[str, list[ActionSpec]] = OrderedDict()
    for cat in CATEGORY_ORDER:
        grouped[cat] = []

    for spec in sorted(registry.values(), key=lambda s: s.name):
        cat = spec.category or "未分类"
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(spec)

    lines = []
    for cat, specs in grouped.items():
        if not specs:
            continue

        # 分类标题
        lines.append(f"#### {cat}")
        lines.append("")

        # Layer 信息提示
        if "Layer 1" in cat:
            lines.append("> 通过 CDP 连接已运行的浏览器，零 Token 消耗。**浏览器场景下首选**。")
            lines.append("")
        elif "Layer 2" in cat:
            lines.append("> 通过无障碍 API 获取桌面应用的语义结构树，零 Token 消耗。")
            lines.append("")
        elif "Legacy" in cat:
            lines.append("> ⚠️ **推荐使用 Layer 1 的 `browser_url` 和 `browser_links` 替代**。以下 legacy action 在 Playwright 不可用时作为降级方案。")
            lines.append("")

        # 表格头
        lines.append("| Action | 参数 | 说明 |")
        lines.append("|--------|------|------|")

        for spec in specs:
            params = _format_params(spec)
            lines.append(f"| `{spec.name}` | {params} | {spec.description} |")

        lines.append("")

    return "\n".join(lines)


def verify_skill_md(skill_md_path: Path) -> bool:
    """验证 SKILL.md 中的 action 表格是否与注册表一致"""
    text = skill_md_path.read_text(encoding="utf-8")

    if BEGIN_MARKER not in text or END_MARKER not in text:
        print(f"❌ SKILL.md 中未找到自动生成标记: {BEGIN_MARKER}")
        return False

    # 提取当前嵌入的 action 表格
    begin_idx = text.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    end_idx = text.index(END_MARKER)
    current_content = text[begin_idx:end_idx].strip()

    # 生成最新的 action 表格
    expected_content = generate_action_tables().strip()

    if current_content == expected_content:
        print(f"✅ SKILL.md action 表格与注册表一致 ({len(get_registry())} actions)")
        return True
    else:
        print("❌ SKILL.md action 表格与注册表不一致!")
        print("   运行 `python3 generate_action_docs.py --update` 更新")

        # 显示差异摘要
        current_actions = set(re.findall(r'\| `([a-z][a-z0-9_]*)` \|', current_content))
        expected_actions = set(re.findall(r'\| `([a-z][a-z0-9_]*)` \|', expected_content))
        only_current = current_actions - expected_actions
        only_expected = expected_actions - current_actions
        if only_current:
            print(f"   SKILL.md 中多余: {sorted(only_current)}")
        if only_expected:
            print(f"   SKILL.md 中缺失: {sorted(only_expected)}")
        if not only_current and not only_expected:
            print("   (action 名称一致，但参数或描述有差异)")
        return False


def update_skill_md(skill_md_path: Path) -> bool:
    """将自动生成的 action 表格写入 SKILL.md"""
    text = skill_md_path.read_text(encoding="utf-8")

    if BEGIN_MARKER not in text or END_MARKER not in text:
        print(f"❌ SKILL.md 中未找到自动生成标记，请手动添加:")
        print(f"   {BEGIN_MARKER}")
        print(f"   ... (action tables will go here)")
        print(f"   {END_MARKER}")
        return False

    begin_idx = text.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    end_idx = text.index(END_MARKER)

    new_content = generate_action_tables()
    new_text = text[:begin_idx] + "\n\n" + new_content + "\n" + text[end_idx:]

    skill_md_path.write_text(new_text, encoding="utf-8")
    print(f"✅ SKILL.md 已更新 ({len(get_registry())} actions)")

    # 同步更新 frontmatter version
    new_text = skill_md_path.read_text(encoding="utf-8")
    version_pattern = re.compile(r'^(version:\s*["\']?)([^"\']+)(["\']?\s*)$', re.MULTILINE)
    m = version_pattern.search(new_text)
    if m and m.group(2).strip() != VERSION:
        new_text = version_pattern.sub(f'version: "{VERSION}"', new_text)
        skill_md_path.write_text(new_text, encoding="utf-8")
        print(f"✅ SKILL.md version 已同步为 {VERSION}")

    return True


def main():
    skill_md = Path(__file__).resolve().parent.parent / "SKILL.md"

    if "--verify" in sys.argv:
        ok = verify_skill_md(skill_md)
        sys.exit(0 if ok else 1)
    elif "--update" in sys.argv:
        ok = update_skill_md(skill_md)
        sys.exit(0 if ok else 1)
    else:
        # 默认：输出到 stdout
        print(generate_action_tables())


if __name__ == "__main__":
    main()
