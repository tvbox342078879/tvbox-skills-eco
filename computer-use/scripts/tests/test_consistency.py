"""
test_consistency.py - 一致性守卫测试（Harness Engineering 原则 5 + 8）

机械化约束：确保 SKILL.md、代码注册表、VERSION 三者一致。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 确保 modules 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.core import VERSION
from modules.registry import get_all_action_names, get_registry

# 路径常量
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
SKILL_DIR = SCRIPTS_DIR.parent         # skills/computer-use/
SKILL_MD = SKILL_DIR / "SKILL.md"
INSTALL_SH = SCRIPTS_DIR / "install.sh"
CORE_PY = SCRIPTS_DIR / "modules" / "core.py"

# SKILL.md 自动生成标记
BEGIN_MARKER = "<!-- BEGIN_ACTION_TABLES (auto-generated, do not edit) -->"
END_MARKER = "<!-- END_ACTION_TABLES -->"


# =====================================================================
# VERSION 一致性
# =====================================================================

class TestVersionConsistency:
    """VERSION 必须在 core.py / SKILL.md / install.sh 三处一致"""

    def test_core_py_version_defined(self):
        """core.py 中 VERSION 必须是非空字符串"""
        assert isinstance(VERSION, str) and VERSION, "VERSION 未定义或为空"

    def test_skill_md_version_matches(self):
        """SKILL.md 的 frontmatter version 必须与 core.py 一致"""
        text = SKILL_MD.read_text(encoding="utf-8")
        m = re.search(r'^version:\s*["\']?([^"\']+)["\']?\s*$', text, re.MULTILINE)
        assert m is not None, "SKILL.md 中未找到 version 字段"
        assert m.group(1).strip() == VERSION, (
            f"SKILL.md version={m.group(1).strip()!r} != core.py VERSION={VERSION!r}"
        )

    def test_install_sh_reads_from_core(self):
        """install.sh 应从 core.py 动态读取版本，不硬编码"""
        text = INSTALL_SH.read_text(encoding="utf-8")
        # install.sh 中应包含 grep core.py 的动态提取逻辑
        assert "core.py" in text, (
            "install.sh 中未引用 core.py，可能在硬编码版本号"
        )
        # 不应有 version=X.Y.Z 形式的硬编码
        hardcoded = re.findall(r'^\s*version=\d+\.\d+\.\d+', text, re.MULTILINE)
        assert not hardcoded, (
            f"install.sh 中发现硬编码版本: {hardcoded}"
        )


# =====================================================================
# Action 一致性（名称级）
# =====================================================================

def _parse_skill_md_actions() -> set:
    """
    从 SKILL.md 的 Action 表格中解析所有 action 名称。
    表格格式: | `action_name` | 参数 | 说明 |
    只匹配第一列中反引号包裹的、含下划线或全小写字母的标识符。
    排除纯数字（表格序号列）。
    """
    text = SKILL_MD.read_text(encoding="utf-8")
    actions = set()
    for m in re.finditer(r'\|\s*`([a-z][a-z0-9_]*)`\s*\|', text):
        actions.add(m.group(1))
    return actions


class TestActionConsistency:
    """注册表 ⇔ SKILL.md action 列表双向一致"""

    def test_registered_actions_cover_skill_md(self):
        """注册表 action ⊇ SKILL.md 声明的 action（无遗漏）"""
        skill_md_actions = _parse_skill_md_actions()
        registered = get_all_action_names()
        missing = skill_md_actions - registered
        assert not missing, (
            f"SKILL.md 声明了但未注册的 action: {sorted(missing)}"
        )

    def test_no_orphan_actions(self):
        """注册表 action ⊆ SKILL.md 声明的 action（无孤儿）"""
        skill_md_actions = _parse_skill_md_actions()
        registered = get_all_action_names()
        orphans = registered - skill_md_actions
        assert not orphans, (
            f"注册表中有但 SKILL.md 未声明的孤儿 action: {sorted(orphans)}"
        )

    def test_skill_md_action_count_reasonable(self):
        """SKILL.md 中解析出的 action 数量应在合理范围内"""
        skill_md_actions = _parse_skill_md_actions()
        assert len(skill_md_actions) >= 40, (
            f"SKILL.md 只解析出 {len(skill_md_actions)} 个 action，可能解析逻辑有误"
        )


# =====================================================================
# SKILL.md 自动生成标记守卫
# =====================================================================

class TestSkillMdStructure:
    """SKILL.md 必须包含自动生成标记，且内容与注册表一致"""

    def test_has_begin_marker(self):
        """SKILL.md 必须包含 BEGIN_ACTION_TABLES 标记"""
        text = SKILL_MD.read_text(encoding="utf-8")
        assert BEGIN_MARKER in text, (
            f"SKILL.md 中未找到 BEGIN_ACTION_TABLES 标记"
        )

    def test_has_end_marker(self):
        """SKILL.md 必须包含 END_ACTION_TABLES 标记"""
        text = SKILL_MD.read_text(encoding="utf-8")
        assert END_MARKER in text, (
            f"SKILL.md 中未找到 END_ACTION_TABLES 标记"
        )

    def test_markers_in_correct_order(self):
        """BEGIN 标记必须在 END 标记之前"""
        text = SKILL_MD.read_text(encoding="utf-8")
        if BEGIN_MARKER in text and END_MARKER in text:
            assert text.index(BEGIN_MARKER) < text.index(END_MARKER), (
                "BEGIN_ACTION_TABLES 标记必须在 END_ACTION_TABLES 之前"
            )

    def test_action_tables_match_registry(self):
        """
        SKILL.md 中自动生成区块的内容必须与注册表生成的内容完全一致。
        这是参数签名级守卫：不仅检查 action 名称，还检查参数和描述。
        运行 `python3 generate_action_docs.py --update` 修复不一致。
        """
        from generate_action_docs import generate_action_tables

        text = SKILL_MD.read_text(encoding="utf-8")
        if BEGIN_MARKER not in text or END_MARKER not in text:
            return  # 由上方测试覆盖

        begin_idx = text.index(BEGIN_MARKER) + len(BEGIN_MARKER)
        end_idx = text.index(END_MARKER)
        current_content = text[begin_idx:end_idx].strip()
        expected_content = generate_action_tables().strip()

        assert current_content == expected_content, (
            "SKILL.md Action 表格与注册表不一致！"
            "运行 `python3 generate_action_docs.py --update` 更新"
        )


# =====================================================================
# Action 元数据完整性守卫
# =====================================================================

class TestActionMetadata:
    """每个 action 的 description 和 category 必须非空"""

    def test_all_actions_have_description(self):
        """注册表中所有 action 必须有非空 description"""
        registry = get_registry()
        missing = [name for name, spec in registry.items() if not spec.description]
        assert not missing, (
            f"以下 action 缺少 description: {sorted(missing)}"
        )

    def test_all_actions_have_category(self):
        """注册表中所有 action 必须有非空 category"""
        registry = get_registry()
        missing = [name for name, spec in registry.items() if not spec.category]
        assert not missing, (
            f"以下 action 缺少 category: {sorted(missing)}"
        )
