"""Shared pytest setup: keep every artifact outside the skill package."""

import os
import sys

# Never write .pyc into the package (blocks skill upload).
sys.dont_write_bytecode = True

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """Redirect cache / history / exports into the per-test tmp directory."""
    data_dir = tmp_path / "data"
    export_dir = tmp_path / "exports"
    data_dir.mkdir(parents=True, exist_ok=True)
    export_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("WECHAT_SCRAPER_DATA_DIR", str(data_dir))
    monkeypatch.setenv("WECHAT_SCRAPER_EXPORT_DIR", str(export_dir))
    yield
    mcp_server = sys.modules.get("mcp_server")
    if mcp_server is not None:
        mcp_server._searcher = None
        mcp_server._fetcher = None
        mcp_server._state = None
        mcp_server._config = None
