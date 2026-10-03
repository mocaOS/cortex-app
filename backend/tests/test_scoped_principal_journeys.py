"""Authenticated-principal journeys at the real (no-bypass) auth boundary.

The shared `client` fixture overrides the three auth dependencies with a fake
admin whose key_id is always "test-admin", so tests built on it can never
verify principal scoping — every request is the same superuser. This module
builds a TestClient with the REAL auth dependencies and fabricates two
distinct API keys through the real validation path (prefix lookup + SHA-256
hash check against a scripted key store), then asserts the documented
principal contracts at the public HTTP surface:

- INV-SESS-001: a server-side session belongs to the key that created it;
  another key's GET/DELETE/ask on it is a 404 (session_service doctrine:
  "A session belongs to the API key that created it; other keys see 404").
- INV-SESS-002: session_id and client-carried state are mutually exclusive
  on BOTH ask transports (the streaming variant was previously uncovered).
- INV-SESS-003: the owner's opaque memory blob round-trips verbatim and is
  never exposed to another principal.
- INV-MCP-001: /mcp requires a valid key, and tools/call forwards the
  CALLER's key into the internal ASGI dispatch — a collection-restricted key
  sees only its allowed collections through MCP search.
- INV-MCP-002: the deep_research tool aggregates the ACTUAL internal
  /api/ask/stream SSE leg (agentic routing is streaming-only) and maps a
  stream error frame to an isError tool result, not a protocol crash.

Negative control (INV-SESS-001 sensitivity): swapping the scripted session
store for one that ignores key ownership must break the gate — the foreign
key reads the owner's session. This is an in-test store substitute fault,
isolated to this module's fixtures; no shared runtime is mutated.

Limitations (recorded in qa/QA_CONTRACT_RECORDS.md):
- The ownership filter itself lives in the Neo4j Cypher
  (neo4j_service.get_api_session); offline the scripted store mirrors its
  documented contract, so this gate proves the HTTP layer passes the real
  authenticated principal into the owner-scoped lookups and maps a miss to
  404 — not the Cypher predicate. Live coverage remains the E2E harness's job.
- LLM/Neo4j are mocked per conftest; no model quality is measured here.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Scripted principals — validated through the REAL auth dependency chain
# ---------------------------------------------------------------------------

def _make_key(secret_body: str, key_id: str, name: str, *, scope: str = "all",
              allowed: Optional[List[str]] = None) -> Dict[str, Any]:
    full = f"cortex_{secret_body}"
    return {
        "id": key_id,
        "name": name,
        "key_hash": hashlib.sha256(full.encode()).hexdigest(),
        "permissions": ["read"],
        "collection_scope": scope,
        "allowed_collections": allowed or [],
        "last_used_at": None,
    }


KEY_A = _make_key("a" * 64, "key-alpha", "Alpha", scope="restricted",
                  allowed=["col-alpha"])
KEY_B = _make_key("b" * 64, "key-beta", "Beta", scope="all")
A_HEADER = {"X-API-Key": f"cortex_{'a' * 64}"}
B_HEADER = {"X-API-Key": f"cortex_{'b' * 64}"}


@pytest.fixture
def noauth_client(mock_neo4j, mock_processors):
    """TestClient with real auth dependencies (no override)."""
    from app.main import app

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _install_key_registry(mock_neo4j):
    """Prefix lookup against the scripted key store (real hash verification)."""

    def get_by_prefix(prefix: str):
        # auth_service looks keys up by the first 12 chars of the minted key.
        return [k for k in (KEY_A, KEY_B) if _minted(k["id"])[:12] == prefix]

    def _minted(key_id: str) -> str:
        body = "a" * 64 if key_id == "key-alpha" else "b" * 64
        return f"cortex_{body}"

    mock_neo4j.get_api_key_by_prefix.side_effect = get_by_prefix


class _SessionStoreFake:
    """In-memory ApiSession store mirroring the documented store contract.

    Mirrors neo4j_service: every lookup/update/delete matches on (id, key_id)
    — ownership enforced at the store boundary, miss -> None/False. The
    negative control subclasses this with ownership matching disabled.
    """

    enforce_owner = True

    def __init__(self):
        self.rows: Dict[str, Dict[str, Any]] = {}
        self._next = 0

    def _matches(self, row: Dict[str, Any], key_id: str) -> bool:
        return (not self.enforce_owner) or row["key_id"] == key_id

    def count_api_sessions(self, key_id: str) -> int:
        return sum(1 for r in self.rows.values() if r["key_id"] == key_id)

    def create_api_session(self, key_id: str, name: str, history_json: str,
                           memory_json: str, turn_count: int) -> dict:
        self._next += 1
        sid = f"ses_{self._next:04d}"
        self.rows[sid] = {
            "id": sid, "key_id": key_id, "name": name,
            "history": history_json, "memory": memory_json,
            "turn_count": turn_count,
            "created_at": "2026-10-01T00:00:00",
            "updated_at": "2026-10-01T00:00:00",
        }
        return {k: self.rows[sid][k] for k in
                ("id", "name", "turn_count", "created_at", "updated_at")}

    def get_api_session(self, session_id: str, key_id: str) -> Optional[dict]:
        row = self.rows.get(session_id)
        if row and self._matches(row, key_id):
            return dict(row)
        return None

    def update_api_session_state(self, session_id: str, key_id: str,
                                 history_json: str, memory_json: str,
                                 turn_count: int) -> bool:
        row = self.rows.get(session_id)
        if row and self._matches(row, key_id):
            row.update(history=history_json, memory=memory_json,
                       turn_count=turn_count,
                       updated_at="2026-10-01T00:00:01")
            return True
        return False

    def delete_api_session(self, session_id: str, key_id: str) -> bool:
        row = self.rows.get(session_id)
        if row and self._matches(row, key_id):
            del self.rows[session_id]
            return True
        return False

    def list_api_sessions(self, key_id: str, limit: int = 50,
                          offset: int = 0) -> dict:
        own = [r for r in self.rows.values() if r["key_id"] == key_id]
        return {"sessions": [{"id": r["id"], "name": r["name"],
                              "turn_count": r["turn_count"],
                              "created_at": r["created_at"],
                              "updated_at": r["updated_at"]}
                             for r in own],
                "total": len(own)}


@pytest.fixture
def sessions_env(noauth_client, mock_neo4j, mock_processors, _isolate_env,
                 monkeypatch):
    """Real-auth env with sessions enabled and the scripted store installed."""
    _isolate_env.enable_sessions = True
    _isolate_env.session_max_per_key = 50
    _isolate_env.session_max_turns = 200
    _install_key_registry(mock_neo4j)
    store = _SessionStoreFake()
    _install_session_store(mock_neo4j, store)
    return {"client": noauth_client, "store": store, "neo4j": mock_neo4j,
            "query": mock_processors.query}


def _install_session_store(mock_neo4j, store: _SessionStoreFake):
    mock_neo4j.count_api_sessions.side_effect = store.count_api_sessions
    mock_neo4j.create_api_session.side_effect = store.create_api_session
    mock_neo4j.get_api_session.side_effect = store.get_api_session
    mock_neo4j.update_api_session_state.side_effect = \
        store.update_api_session_state
    mock_neo4j.delete_api_session.side_effect = store.delete_api_session
    mock_neo4j.list_api_sessions.side_effect = store.list_api_sessions


def _seed_a_session(env, *, memory: Optional[dict] = None) -> str:
    body = {"name": "alpha-thread"}
    if memory is not None:
        body["memory"] = memory
        body["history"] = [{"role": "user", "content": "seed question"},
                           {"role": "assistant", "content": "seed answer"}]
    r = env["client"].post("/api/sessions", json=body, headers=A_HEADER)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# INV-SESS-001/003: cross-key session isolation with REAL principals
# ---------------------------------------------------------------------------

class TestSessionOwnership:
    def test_owner_sees_own_session_with_opaque_memory_intact(self, sessions_env):
        mem = {"version": 3, "facts": ["alpha-secret-fact"],
               "transcript": {"summarized_count": 0}}
        sid = _seed_a_session(sessions_env, memory=mem)
        r = sessions_env["client"].get(f"/api/sessions/{sid}", headers=A_HEADER)
        assert r.status_code == 200
        body = r.json()
        # The opaque blob round-trips verbatim to its OWNER.
        assert body["memory"] == mem
        assert [m["content"] for m in body["history"]] == \
            ["seed question", "seed answer"]

    def test_foreign_key_cannot_read_update_or_delete(self, sessions_env):
        sid = _seed_a_session(
            sessions_env,
            memory={"version": 3, "facts": ["alpha-secret-fact"]},
        )
        c = sessions_env["client"]
        # Read: other keys see 404 — the session is invisible, not empty.
        assert c.get(f"/api/sessions/{sid}", headers=B_HEADER).status_code == 404
        # List: B's inventory does not contain A's session.
        listed = c.get("/api/sessions", headers=B_HEADER).json()
        assert listed["total"] == 0
        # Delete: 404 and the session survives.
        assert c.delete(f"/api/sessions/{sid}",
                        headers=B_HEADER).status_code == 404
        assert sid in sessions_env["store"].rows

    def test_foreign_key_cannot_ride_a_session_through_ask(
            self, sessions_env, _isolate_env):
        # The streaming endpoint checks its LLM-key config gate after session
        # resolution (documented ordering: session errors must not be masked),
        # so give the gate its key to observe the 404 on both transports.
        _isolate_env.openai_api_key = "test-key"
        sid = _seed_a_session(
            sessions_env, memory={"version": 3, "facts": ["alpha-secret-fact"]},
        )
        c = sessions_env["client"]
        r = c.post("/api/ask", json={"question": "q", "session_id": sid},
                   headers=B_HEADER)
        assert r.status_code == 404
        r = c.post("/api/ask/stream",
                   json={"question": "q", "session_id": sid},
                   headers=B_HEADER)
        assert r.status_code == 404
        # No turn was persisted into the owner's session by the foreign key.
        assert sessions_env["store"].rows[sid]["turn_count"] == 2

    def test_owner_stream_turn_persists_under_its_own_principal(
            self, sessions_env, _isolate_env):
        _isolate_env.enable_agent_chat = True
        _isolate_env.openai_api_key = "test-key"  # stream config gate
        sid = _seed_a_session(
            sessions_env, memory={"version": 3, "facts": ["alpha-secret-fact"]},
        )

        async def fake_stream(**kwargs):
            yield {"content": "tok1 "}
            yield {"content": "tok2"}
            yield {"done": True}

        sessions_env["query"].agent_rag_stream = fake_stream

        r = sessions_env["client"].post(
            "/api/ask/stream", json={"question": "follow-up",
                                     "session_id": sid},
            headers=A_HEADER,
        )
        assert r.status_code == 200
        assert "tok1" in r.text
        row = sessions_env["store"].rows[sid]
        assert row["turn_count"] == 4
        history = json.loads(row["history"])
        assert history[-1] == {"role": "assistant", "content": "tok1 tok2"}
        # The opaque memory blob was preserved (no memory_update frame ->
        # prior blob kept), and only the OWNER can observe it.
        assert json.loads(row["memory"])["facts"] == ["alpha-secret-fact"]
        assert sessions_env["client"].get(
            f"/api/sessions/{sid}", headers=B_HEADER).status_code == 404

    def test_stream_conflict_session_vs_client_memory(self, sessions_env):
        # The streaming variant of the one-source-of-truth rule was uncovered:
        # only the non-streaming 400 and the stream fast-search 400 existed.
        sid = _seed_a_session(sessions_env)
        r = sessions_env["client"].post(
            "/api/ask/stream",
            json={"question": "q", "session_id": sid,
                  "conversation_memory": {"version": 3}},
            headers=A_HEADER,
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "session_conflict"


# ---------------------------------------------------------------------------
# Negative control: gate sensitivity to a scoping-broken store (isolated)
# ---------------------------------------------------------------------------

class _OwnerBlindSessionStore(_SessionStoreFake):
    """Mutant store substitute: ownership matching disabled (id-only lookups).

    Stands in for the plausible violation "the store (or its caller) stops
    matching key_id" — the exact loss INV-SESS-001 exists to catch.
    """

    enforce_owner = False


def test_negative_control_owner_blind_store_breaks_isolation_gate(
        noauth_client, mock_neo4j, mock_processors, _isolate_env):
    _isolate_env.enable_sessions = True
    _isolate_env.session_max_per_key = 50
    _isolate_env.session_max_turns = 200
    _install_key_registry(mock_neo4j)
    store = _OwnerBlindSessionStore()
    _install_session_store(mock_neo4j, store)
    sid = _seed_a_session({"client": noauth_client, "store": store},
                          memory={"version": 3, "facts": ["alpha-secret-fact"]})

    # Expected failing assertion: the INV-SESS-001 read-isolation check.
    with pytest.raises(AssertionError):
        assert noauth_client.get(f"/api/sessions/{sid}",
                                 headers=B_HEADER).status_code == 404
    # Verify the cause is the intended behavioral leak (not a harness outage):
    # the foreign key read the owner's session INCLUDING its memory blob.
    r = noauth_client.get(f"/api/sessions/{sid}", headers=B_HEADER)
    assert r.status_code == 200
    assert "alpha-secret-fact" in r.text


# ---------------------------------------------------------------------------
# INV-MCP-001: /mcp runs tools under the CALLER's forwarded principal
# ---------------------------------------------------------------------------

@pytest.fixture
def mcp_real_env(noauth_client, mock_neo4j, mock_processors, _isolate_env):
    _isolate_env.enable_remote_mcp = True
    _isolate_env.enable_reranking = False
    _install_key_registry(mock_neo4j)
    return {"client": noauth_client, "query": mock_processors.query,
            "neo4j": mock_neo4j}


def _rpc(method, params=None, msg_id=1):
    m = {"jsonrpc": "2.0", "method": method, "id": msg_id}
    if params is not None:
        m["params"] = params
    return m


class TestMcpPrincipalForwarding:
    def test_mcp_rejects_invalid_key_401(self, mcp_real_env):
        r = mcp_real_env["client"].post(
            "/mcp", json=_rpc("tools/list"),
            headers={"X-API-Key": "cortex_" + "f" * 64})
        assert r.status_code == 401

    def test_restricted_key_search_is_confined_via_forwarded_key(
            self, mcp_real_env):
        calls = []

        def fake_hybrid_search(*args, **kwargs):
            calls.append(kwargs)
            if kwargs.get("collection_id") == "col-alpha":
                return [{"document_id": "da", "chunk_id": "ca",
                         "content": "alpha-only evidence", "score": 0.9,
                         "filename": "alpha.md", "chunk_index": 0}]
            return [{"document_id": "db", "chunk_id": "cb",
                     "content": "beta-only evidence", "score": 0.9,
                     "filename": "beta.md", "chunk_index": 0}]

        mcp_real_env["query"].hybrid_search.side_effect = fake_hybrid_search
        r = mcp_real_env["client"].post(
            "/mcp", json=_rpc("tools/call", {
                "name": "search_knowledge",
                "arguments": {"query": "evidence"},
            }),
            headers=A_HEADER,  # restricted to col-alpha
        )
        assert r.status_code == 200
        result = r.json()["result"]
        assert result["isError"] is False
        text = result["content"][0]["text"]
        # The internal leg re-authenticated the forwarded key and applied its
        # collection restriction: alpha content only, no beta leakage.
        assert "alpha-only evidence" in text
        assert "beta" not in text
        assert calls and calls[0].get("collection_id") == "col-alpha"

    def test_unrestricted_key_sees_other_collections(self, mcp_real_env):
        mcp_real_env["query"].hybrid_search.return_value = [
            {"document_id": "db", "chunk_id": "cb",
             "content": "beta-only evidence", "score": 0.9,
             "filename": "beta.md", "chunk_index": 0}]
        r = mcp_real_env["client"].post(
            "/mcp", json=_rpc("tools/call", {
                "name": "search_knowledge", "arguments": {"query": "evidence"},
            }),
            headers=B_HEADER,  # scope "all"
        )
        result = r.json()["result"]
        assert result["isError"] is False
        assert "beta-only evidence" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# INV-MCP-002: deep_research aggregates the actual internal ask-SSE leg
# ---------------------------------------------------------------------------

class TestMcpDeepResearchSse:
    def _enable(self, mcp_real_env, _isolate_env):
        _isolate_env.enable_agentic_rag = True
        _isolate_env.enable_agent_research = True
        _isolate_env.openai_api_key = "test-key"

    def test_deep_research_aggregates_internal_sse_stream(
            self, mcp_real_env, _isolate_env):
        self._enable(mcp_real_env, _isolate_env)
        seen = {}

        def fake_agent_stream(**kwargs):
            seen.update(kwargs)

            async def gen():
                yield {"content": "Step 1 done. "}
                yield {"sources": [{"document_id": "d1", "score": 0.9,
                                    "metadata": {"filename": "research.md"}}]}
                yield {"content": "Final: 42."}
                yield {"done": True}

            return gen()

        mcp_real_env["query"].agent_rag_stream = fake_agent_stream
        r = mcp_real_env["client"].post(
            "/mcp", json=_rpc("tools/call", {
                "name": "ask_question",
                "arguments": {"question": "deep question",
                              "mode": "deep_research"},
            }),
            headers=B_HEADER,
        )
        assert r.status_code == 200
        result = r.json()["result"]
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "Step 1 done." in text and "Final: 42." in text
        assert "**Sources:**" in text and "research.md" in text
        # The aggregation ran over the streaming endpoint's agentic branch:
        # quality-mode agent stream (reachable only via /api/ask/stream —
        # POST /api/ask refuses agentic with a 400).
        assert seen["mode"] == "quality"
        assert seen["question"] == "deep question"

    def test_deep_research_stream_error_frame_becomes_isError(
            self, mcp_real_env, _isolate_env):
        self._enable(mcp_real_env, _isolate_env)

        def fake_agent_stream(**kwargs):
            async def gen():
                yield {"content": "partial progress"}
                yield {"error": "research exploded"}

            return gen()

        mcp_real_env["query"].agent_rag_stream = fake_agent_stream
        r = mcp_real_env["client"].post(
            "/mcp", json=_rpc("tools/call", {
                "name": "ask_question",
                "arguments": {"question": "q", "mode": "deep_research"},
            }),
            headers=B_HEADER,
        )
        assert r.status_code == 200  # protocol stays healthy
        result = r.json()["result"]
        assert result["isError"] is True
        assert "research exploded" in result["content"][0]["text"]
