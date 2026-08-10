"""Instance-hosted remote MCP (/mcp) — protocol handshake + tool dispatch.

Exercises the streamable-HTTP transport end to end through TestClient:
initialize / tools/list / ping, the 404-when-disabled gate, notification
acks, batch rejection, and tools/call dispatching through the in-process
ASGI client into the real (mocked-services) REST endpoints.
"""

import json
from unittest.mock import AsyncMock

import pytest

from app.services import remote_mcp


def rpc(method, params=None, msg_id=1):
    m = {"jsonrpc": "2.0", "method": method, "id": msg_id}
    if params is not None:
        m["params"] = params
    return m


@pytest.fixture
def mcp_env(client, mock_processors, mock_neo4j, _isolate_env):
    _isolate_env.enable_remote_mcp = True
    _isolate_env.enable_reranking = False  # keep internal /api/context deterministic

    class Env:
        pass

    env = Env()
    env.client = client
    env.query = mock_processors.query
    env.neo4j = mock_neo4j
    return env


class TestTransport:
    def test_404_when_disabled(self, client, _isolate_env):
        _isolate_env.enable_remote_mcp = False
        assert client.post("/mcp", json=rpc("initialize")).status_code == 404
        assert client.get("/mcp").status_code == 404

    def test_initialize_handshake(self, mcp_env):
        r = mcp_env.client.post("/mcp", json=rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        }))
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == 1
        result = body["result"]
        assert result["protocolVersion"] == "2025-03-26"  # echoed (supported)
        assert result["serverInfo"]["name"] == "cortex"
        assert "tools" in result["capabilities"]

    def test_initialize_unknown_version_gets_latest(self, mcp_env):
        r = mcp_env.client.post("/mcp", json=rpc("initialize", {
            "protocolVersion": "2099-01-01",
        }))
        assert r.json()["result"]["protocolVersion"] == remote_mcp.LATEST_PROTOCOL_VERSION

    def test_notification_gets_202(self, mcp_env):
        r = mcp_env.client.post("/mcp", json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        assert r.status_code == 202

    def test_ping(self, mcp_env):
        r = mcp_env.client.post("/mcp", json=rpc("ping", msg_id=7))
        assert r.json() == {"jsonrpc": "2.0", "id": 7, "result": {}}

    def test_tools_list(self, mcp_env):
        r = mcp_env.client.post("/mcp", json=rpc("tools/list"))
        tools = r.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        assert {"search_knowledge", "ask_question", "get_context",
                "list_documents", "upload_document", "get_stats"} <= names
        for t in tools:
            assert t["inputSchema"]["type"] == "object"

    def test_batch_rejected(self, mcp_env):
        r = mcp_env.client.post("/mcp", json=[rpc("ping")])
        assert r.status_code == 400
        assert r.json()["error"]["code"] == -32600

    def test_unknown_method(self, mcp_env):
        r = mcp_env.client.post("/mcp", json=rpc("resources/list"))
        assert r.json()["error"]["code"] == -32601

    def test_get_and_delete_405(self, mcp_env):
        assert mcp_env.client.get("/mcp").status_code == 405
        assert mcp_env.client.delete("/mcp").status_code == 405


class TestToolDispatch:
    def test_search_tool_hits_real_endpoint(self, mcp_env):
        mcp_env.query.hybrid_search.return_value = [
            {"document_id": "d1", "chunk_id": "c1", "content": "hello world",
             "score": 0.9, "filename": "notes.md", "chunk_index": 0},
        ]
        r = mcp_env.client.post("/mcp", json=rpc("tools/call", {
            "name": "search_knowledge",
            "arguments": {"query": "hello"},
        }))
        assert r.status_code == 200
        result = r.json()["result"]
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "notes.md" in text and "hello world" in text

    def test_get_context_tool(self, mcp_env):
        mcp_env.query.graph_search_async = AsyncMock(return_value={
            "results": [{"document_id": "d1", "chunk_id": "c1",
                         "content": "ctx content", "score": 0.9,
                         "filename": "f.md", "chunk_index": 0}],
            "graph_context": {"entities": [], "relationships": [], "chunks": []},
        })
        mcp_env.neo4j.search_communities_by_content.return_value = []
        r = mcp_env.client.post("/mcp", json=rpc("tools/call", {
            "name": "get_context",
            "arguments": {"query": "q", "max_tokens": 1000},
        }))
        result = r.json()["result"]
        assert result["isError"] is False
        assert "=== Knowledge Context ===" in result["content"][0]["text"]

    def test_ask_chat_tool(self, mcp_env):
        mcp_env.query.rag_query = AsyncMock(return_value={
            "question": "q", "answer": "The answer.", "sources": [],
            "graph_context": None, "reranked": False, "reasoning_steps": None,
        })
        r = mcp_env.client.post("/mcp", json=rpc("tools/call", {
            "name": "ask_question",
            "arguments": {"question": "q", "mode": "chat"},
        }))
        result = r.json()["result"]
        assert result["isError"] is False
        assert "The answer." in result["content"][0]["text"]

    def test_unknown_tool_is_isError_result(self, mcp_env):
        r = mcp_env.client.post("/mcp", json=rpc("tools/call", {
            "name": "explode", "arguments": {},
        }))
        result = r.json()["result"]
        assert result["isError"] is True
        assert "Unknown tool" in result["content"][0]["text"]

    def test_api_error_maps_to_isError(self, mcp_env):
        # get_document for a missing id → internal 404 → isError result
        mcp_env.neo4j.get_document.return_value = None
        r = mcp_env.client.post("/mcp", json=rpc("tools/call", {
            "name": "get_document", "arguments": {"document_id": "nope"},
        }))
        result = r.json()["result"]
        assert result["isError"] is True
        assert "404" in result["content"][0]["text"]

    def test_sse_response_mode(self, mcp_env):
        mcp_env.query.hybrid_search.return_value = []
        r = mcp_env.client.post(
            "/mcp",
            json=rpc("tools/call", {
                "name": "search_knowledge", "arguments": {"query": "x"},
            }),
            headers={"Accept": "text/event-stream"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        payloads = [
            json.loads(line[5:].strip())
            for line in r.text.splitlines()
            if line.startswith("data:")
        ]
        assert len(payloads) == 1
        msg = payloads[0]
        assert msg["jsonrpc"] == "2.0" and msg["id"] == 1
        assert "type" not in msg  # raw JSON-RPC — no Cortex SSE type stamp
        assert msg["result"]["isError"] is False
