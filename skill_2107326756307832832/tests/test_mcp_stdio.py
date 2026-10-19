"""End-to-end MCP stdio test: real subprocess, real JSON-RPC frames.

handle_request() alone can pass while the server never emits a single byte,
so this drives the actual stdin/stdout loop.
"""

import json
import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ARTICLE_HTML = """
<html><head>
<meta content="端到端测试文章" property="og:title">
<meta content="https://mp.weixin.qq.com/s/e2e" property="og:url">
</head><body><div id="js_content"><p>正文内容在这里。</p></div></body></html>
"""


def _spawn(tmp_path):
    env = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        WECHAT_SCRAPER_DATA_DIR=str(tmp_path / "data"),
        WECHAT_SCRAPER_EXPORT_DIR=str(tmp_path / "exports"),
    )
    proc = subprocess.Popen(
        [sys.executable, "mcp_server.py"],
        cwd=PROJECT_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    return proc


def _send(proc, *requests):
    for req in requests:
        proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def _read(proc, count):
    """Read `count` response frames, failing loudly if stdout goes silent."""
    frames = []
    for _ in range(count):
        line = proc.stdout.readline()
        if not line:
            err = proc.stderr.read()[:500]
            pytest.fail(f"服务端提前关闭 stdout（{len(frames)} 帧后）；stderr: {err}")
        frames.append(json.loads(line))
    return frames


@pytest.fixture
def server(tmp_path):
    proc = _spawn(tmp_path)
    yield proc
    proc.terminate()
    proc.wait(timeout=10)


class TestMcpStdio:
    def test_handshake_then_tools_list(self, server):
        _send(server,
              {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
              {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        init, listed = _read(server, 2)
        assert init["result"]["serverInfo"]["name"] == "wechat-article-scraper"
        assert init["result"]["capabilities"]["tools"]
        assert len(listed["result"]["tools"]) >= 21

    def test_notification_produces_no_frame(self, server):
        """A response to a notification would desync every client's id pairing."""
        _send(server,
              {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
              {"jsonrpc": "2.0", "method": "notifications/initialized"},
              {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                  "name": "parse_wechat_html",
                  "arguments": {"html": ARTICLE_HTML,
                                "url": "https://mp.weixin.qq.com/s/e2e"}}})
        first, second = _read(server, 2)
        assert first["id"] == 1
        assert second["id"] == 2, "notification must not consume a response slot"
        payload = json.loads(second["result"]["content"][0]["text"])
        assert payload["article"]["title"] == "端到端测试文章"
        assert payload["article"]["link"] == "https://mp.weixin.qq.com/s/e2e"
        assert second["result"]["isError"] is False

    def test_stdout_is_protocol_only(self, server):
        """Logging must not interleave into stdout frames."""
        _send(server,
              {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
                  "name": "get_skill_stats", "arguments": {}}},
              {"jsonrpc": "2.0", "id": 8, "method": "ping"})
        stats, ping = _read(server, 2)
        assert stats["id"] == 7 and ping["id"] == 8
        assert json.loads(stats["result"]["content"][0]["text"])["tool_count"] >= 21

    def test_malformed_line_is_skipped_not_fatal(self, server):
        server.stdin.write("{ this is not json\n")
        _send(server, {"jsonrpc": "2.0", "id": 9, "method": "ping"})
        assert _read(server, 1)[0]["id"] == 9

    def test_non_object_request_is_rejected_politely(self, server):
        """A JSON string / array on the wire is valid JSON but not a request."""
        _send(server, "just a bare string", {"jsonrpc": "2.0", "id": 10, "method": "ping"})
        bad, ok = _read(server, 2)
        assert bad["error"]["code"] == -32600
        assert ok["id"] == 10

    def test_unknown_method_returns_error_frame(self, server):
        _send(server, {"jsonrpc": "2.0", "id": 3, "method": "tools/nope"})
        resp = _read(server, 1)[0]
        assert resp["error"]["code"] == -32601
        assert resp["id"] == 3

    def test_tool_error_is_reported_not_raised(self, server):
        _send(server, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "summarize_articles", "arguments": {"articles": []}}})
        resp = _read(server, 1)[0]
        assert resp["result"]["isError"] is True
        assert "error" in json.loads(resp["result"]["content"][0]["text"])

    def test_history_roundtrip_over_stdio(self, server, tmp_path):
        articles = [
            {"title": "第一篇", "link": "https://mp.weixin.qq.com/s/one", "account": "甲"},
            {"title": "第二篇", "link": "https://mp.weixin.qq.com/s/two?chksm=1", "account": "甲"},
        ]
        _send(server,
              {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
                  "name": "filter_new_articles", "arguments": {"articles": articles}}},
              {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
                  "name": "filter_new_articles", "arguments": {"articles": articles}}})
        first, second = _read(server, 2)
        a = json.loads(first["result"]["content"][0]["text"])
        b = json.loads(second["result"]["content"][0]["text"])
        assert a["new_count"] == 2
        assert b["new_count"] == 0 and b["known_count"] == 2
        assert (tmp_path / "data" / "scrape_history.json").exists()
