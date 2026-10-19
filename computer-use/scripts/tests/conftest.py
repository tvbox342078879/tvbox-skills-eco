"""
conftest.py - pytest fixtures for computer-use skill tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# 确保 scripts/ 在 sys.path 中
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture()
def tool():
    """
    返回一个 ComputerTool 实例。
    ComputerToolBase.__init__ 需要 DISPLAY 等桌面环境变量，
    这里直接 mock 掉 __init__，使测试不依赖沙箱环境。
    """
    from computer_tool import ComputerTool

    with patch.object(ComputerTool, "__init__", lambda self: None):
        instance = ComputerTool.__new__(ComputerTool)
    # mock 掉 __init__ 后实例缺少基类属性，补齐测试所需最小集
    instance.display = ":1"
    instance.width = 1280
    instance.height = 800
    instance.output_dir = "/tmp/computer-use-outputs"
    return instance


@pytest.fixture()
def registry():
    """返回当前注册表快照（dict[str, ActionSpec]）"""
    from modules.registry import get_registry

    return get_registry()
