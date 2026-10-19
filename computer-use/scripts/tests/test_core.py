"""
test_core.py - ComputerToolBase 基类与常量的单元测试
"""

from __future__ import annotations

import re

from modules.core import (
    CLICK_BUTTONS,
    COMMAND_TIMEOUT,
    MAX_SCROLL_AMOUNT,
    MAX_TEXT_LENGTH,
    OUTPUT_DIR,
    RECORDING_DIR,
    SCROLL_DIRECTIONS,
    VERSION,
    validate_keys,
    validate_modifier_key,
)


class TestConstants:
    """常量值合理性检查"""

    def test_version_format(self):
        """VERSION 格式为 X.Y.Z"""
        assert re.match(r"^\d+\.\d+\.\d+$", VERSION), (
            f"VERSION={VERSION!r} 不符合 semver 格式"
        )

    def test_output_dir_is_tmp(self):
        assert OUTPUT_DIR.startswith("/tmp"), "截图临时目录应在 /tmp 下"

    def test_recording_dir_not_tmp(self):
        """录制目录应在 /workspace（用户可见），不在 /tmp"""
        assert "/workspace" in RECORDING_DIR or "RECORDING_DIR" in repr(RECORDING_DIR)

    def test_command_timeout_positive(self):
        assert COMMAND_TIMEOUT > 0

    def test_max_text_length_positive(self):
        assert MAX_TEXT_LENGTH > 0

    def test_max_scroll_amount_positive(self):
        assert MAX_SCROLL_AMOUNT > 0

    def test_click_buttons_has_standard_keys(self):
        for key in ("left_click", "right_click", "middle_click"):
            assert key in CLICK_BUTTONS, f"CLICK_BUTTONS 缺少 '{key}'"

    def test_scroll_directions(self):
        for d in ("up", "down", "left", "right"):
            assert d in SCROLL_DIRECTIONS, f"SCROLL_DIRECTIONS 缺少 '{d}'"


class TestValidation:
    """校验函数"""

    def test_validate_keys_normal(self):
        """正常按键组合不应抛异常"""
        validate_keys("ctrl+s")
        validate_keys("Return")
        validate_keys("alt+Tab")

    def test_validate_keys_rejects_empty(self):
        """空字符串应被拒绝"""
        import pytest

        with pytest.raises((ValueError, Exception)):
            validate_keys("")

    def test_validate_modifier_key_valid(self):
        """合法修饰键不应抛异常"""
        for key in ("shift", "ctrl", "alt", "super"):
            validate_modifier_key(key)

    def test_validate_modifier_key_invalid(self):
        """非法修饰键应被拒绝"""
        import pytest

        with pytest.raises((ValueError, Exception)):
            validate_modifier_key("invalid_key_xxx")
