"""
test_registry.py - Action 注册表单元测试（ADR-001）

验证：
  - 注册表非空
  - 每个 ActionSpec 结构完整
  - 重复注册抛 ValueError
  - action 名与方法名的映射关系
  - 装饰器元数据与实际方法签名匹配
"""

from __future__ import annotations

import inspect

import pytest

from modules.registry import (
    ActionSpec,
    get_action_spec,
    get_all_action_names,
    get_registry,
    register_action,
)


class TestRegistryBasics:
    """注册表基础功能"""

    def test_registry_not_empty(self, registry):
        assert len(registry) > 0, "注册表不应为空"

    def test_all_action_names_returns_frozenset(self):
        names = get_all_action_names()
        assert isinstance(names, frozenset)

    def test_get_action_spec_existing(self):
        spec = get_action_spec("screenshot")
        assert spec is not None
        assert spec.name == "screenshot"

    def test_get_action_spec_nonexistent(self):
        assert get_action_spec("__nonexistent_action__") is None

    def test_action_spec_has_required_attrs(self, registry):
        for name, spec in registry.items():
            assert isinstance(spec, ActionSpec), f"{name} 不是 ActionSpec 实例"
            assert spec.name == name
            assert isinstance(spec.method_name, str) and spec.method_name
            assert isinstance(spec.required_params, tuple)
            assert isinstance(spec.optional_params, dict)
            assert spec.layer in ("L1", "L2", "L3")


class TestDuplicateRegistration:
    """重复注册保护"""

    def test_duplicate_action_raises(self):
        """重复注册相同 action 名应抛出 ValueError"""
        # screenshot 已注册，再注册一次
        with pytest.raises(ValueError, match="Duplicate action"):

            @register_action("screenshot")
            async def fake_screenshot(self):
                pass  # pragma: no cover


class TestMethodResolution:
    """方法签名与注册元数据匹配"""

    def test_method_exists_on_tool(self, tool, registry):
        """每个注册方法在 ComputerTool 实例上可达"""
        for name, spec in registry.items():
            assert hasattr(tool, spec.method_name), (
                f"Action '{name}' 的方法 '{spec.method_name}' 在 ComputerTool 上不存在"
            )

    def test_method_is_callable(self, tool, registry):
        for name, spec in registry.items():
            method = getattr(tool, spec.method_name)
            assert callable(method), f"{spec.method_name} 不可调用"

    def test_required_params_in_method_signature(self, tool, registry):
        """required_params 必须出现在方法签名中"""
        for name, spec in registry.items():
            method = getattr(tool, spec.method_name)
            sig = inspect.signature(method)
            param_names = set(sig.parameters.keys()) - {"self"}
            for rp in spec.required_params:
                assert rp in param_names, (
                    f"Action '{name}': required param '{rp}' 不在方法 "
                    f"'{spec.method_name}' 的签名 {param_names} 中"
                )

    def test_optional_params_in_method_signature(self, tool, registry):
        """optional_params 必须出现在方法签名中"""
        for name, spec in registry.items():
            method = getattr(tool, spec.method_name)
            sig = inspect.signature(method)
            param_names = set(sig.parameters.keys()) - {"self"}
            for op in spec.optional_params:
                assert op in param_names, (
                    f"Action '{name}': optional param '{op}' 不在方法 "
                    f"'{spec.method_name}' 的签名 {param_names} 中"
                )


class TestExpectedActionCount:
    """注册表完整性——action 数量守卫"""

    def test_minimum_action_count(self, registry):
        """至少 50 个 action（当前实际 52 个）"""
        assert len(registry) >= 50, (
            f"注册表只有 {len(registry)} 个 action，低于最低阈值 50"
        )
