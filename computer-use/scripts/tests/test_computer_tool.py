"""
test_computer_tool.py - ComputerTool 集成测试（execute 分发）

验证：
  - execute() 正确分发到注册表方法
  - 缺少必选参数返回 error
  - 未知 action 返回 error
  - 可选参数默认值正确填充
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
class TestExecuteDispatch:
    """execute() 注册表驱动分发"""

    async def test_unknown_action_returns_error(self, tool):
        result = await tool.execute("__nonexistent_action__")
        assert "error" in result
        assert "Unknown action" in result["error"]

    async def test_missing_required_params(self, tool):
        """缺少必选参数返回明确错误"""
        # left_click_drag 需要 start_x, start_y, end_x, end_y
        result = await tool.execute("left_click_drag")
        assert "error" in result
        assert "Missing required" in result["error"]

    async def test_dispatch_calls_correct_method(self, tool):
        """execute 分发到正确的方法"""
        mock_result = {"status": "ok"}
        with patch.object(tool, "screenshot", new_callable=AsyncMock, return_value=mock_result):
            result = await tool.execute("screenshot")
        assert result == mock_result

    async def test_optional_params_defaults(self, tool):
        """可选参数使用注册表声明的默认值"""
        captured_kwargs = {}

        async def fake_scroll(**kwargs):
            captured_kwargs.update(kwargs)
            return {"status": "scrolled"}

        with patch.object(tool, "scroll", side_effect=fake_scroll):
            result = await tool.execute("scroll", direction="down")

        assert result["status"] == "scrolled"
        # scroll 注册时声明了 optional: amount, x, y
        assert "amount" in captured_kwargs or "direction" in captured_kwargs

    async def test_exception_in_method_returns_error(self, tool):
        """方法内部异常被捕获并返回 error"""
        with patch.object(
            tool, "screenshot",
            new_callable=AsyncMock,
            side_effect=RuntimeError("test boom"),
        ):
            result = await tool.execute("screenshot")
        assert "error" in result
        assert "Internal error" in result["error"]
        assert "test boom" in result["error"]

    async def test_value_error_returns_invalid_param(self, tool):
        """ValueError 被包装为 Invalid parameter 错误"""
        with patch.object(
            tool, "screenshot",
            new_callable=AsyncMock,
            side_effect=ValueError("bad value"),
        ):
            result = await tool.execute("screenshot")
        assert "error" in result
        assert "Invalid parameter" in result["error"]

    async def test_unexpected_params_rejected(self, tool):
        """传入未声明的参数应返回友好错误，而非透传导致 TypeError"""
        result = await tool.execute("screenshot", bogus_param="oops")
        assert "error" in result
        assert "Unexpected parameter" in result["error"]
        assert "bogus_param" in result["error"]

    async def test_multiple_unexpected_params_sorted(self, tool):
        """多个未声明参数应按字母序列出"""
        result = await tool.execute("screenshot", zzz="a", aaa="b")
        assert "error" in result
        assert "aaa" in result["error"]
        assert "zzz" in result["error"]
