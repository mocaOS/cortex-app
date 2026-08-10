"""Server-side sessions — CRUD, the ask integration contract, trim math.

Sessions are opt-in (ENABLE_SESSIONS); the client-carried memory contract
stays the stateless default. These tests cover the CRUD surface (ownership,
quota, import seeding), the one-source-of-truth rules on ask (session_conflict,
fast-search rejection, foreign-session 404), turn persistence on both the
non-streaming and streaming paths (including the post-done memory_update
capture), and the summarized_count-preserving history trim.
"""

import json
from unittest.mock import AsyncMock

import pytest

from app.services.session_service import (
    build_turn_state,
    parse_session_state,
    serialize_state,
    trim_history,
)


def _row(history=None, memory=None, **overrides):
    base = {
        "id": "ses_abc", "name": "t", "turn_count": len(history or []),
        "history": json.dumps(history or []),
        "memory": json.dumps(memory or {}),
        "created_at": "2026-08-10", "updated_at": "2026-08-10",
    }
    base.update(overrides)
    return base


@pytest.fixture
def sess_env(client, mock_neo4j, mock_processors, _isolate_env):
    _isolate_env.enable_sessions = True
    _isolate_env.session_max_per_key = 500
    _isolate_env.session_max_turns = 200

    class Env:
        pass

    env = Env()
    env.client = client
    env.neo4j = mock_neo4j
    env.query = mock_processors.query
    return env


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestTrimMath:
    def test_no_trim_under_cap(self):
        h = [{"role": "user", "content": "a"}] * 4
        m = {"transcript": {"summarized_count": 2}}
        h2, m2 = trim_history(h, m, 10)
        assert h2 is h and m2 is m

    def test_trim_adjusts_summarized_count(self):
        h = [{"role": "user", "content": str(i)} for i in range(10)]
        m = {"transcript": {"summarized_count": 6, "summary": "s"}, "facts": ["f"]}
        h2, m2 = trim_history(h, m, 6)
        assert len(h2) == 6
        assert h2[0]["content"] == "4"  # oldest 4 dropped
        assert m2["transcript"]["summarized_count"] == 2  # 6 - 4
        assert m2["transcript"]["summary"] == "s"  # rest of blob intact
        assert m2["facts"] == ["f"]

    def test_summarized_count_floors_at_zero(self):
        h = [{"role": "user", "content": str(i)} for i in range(10)]
        m = {"transcript": {"summarized_count": 1}}
        _, m2 = trim_history(h, m, 4)
        assert m2["transcript"]["summarized_count"] == 0

    def test_build_turn_state_appends_and_counts(self):
        hist_json, mem_json, count = build_turn_state(
            [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}],
            {"version": 3},
            "q2", "a2", {"version": 3, "facts": ["new"]}, 200,
        )
        history = json.loads(hist_json)
        assert count == 4 and history[-1] == {"role": "assistant", "content": "a2"}
        assert json.loads(mem_json)["facts"] == ["new"]

    def test_build_turn_state_keeps_prior_memory_without_update(self):
        _, mem_json, _ = build_turn_state([], {"version": 3, "facts": ["old"]},
                                          "q", "a", None, 200)
        assert json.loads(mem_json)["facts"] == ["old"]

    def test_parse_tolerates_corruption(self):
        h, m = parse_session_state({"history": "{not json", "memory": "42"})
        assert h == [] and m == {}

    def test_serialize_drops_oldest_on_byte_overflow(self, monkeypatch):
        import app.services.session_service as ss
        monkeypatch.setattr(ss, "MAX_HISTORY_BYTES", 300)
        h = [{"role": "user", "content": "x" * 60} for _ in range(10)]
        hist_json, _ = serialize_state(h, {})
        assert len(hist_json) <= 300
        assert len(json.loads(hist_json)) < 10


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestSessionCrud:
    def test_403_when_disabled(self, client, _isolate_env):
        _isolate_env.enable_sessions = False
        assert client.post("/api/sessions", json={}).status_code == 403
        assert client.get("/api/sessions").status_code == 403

    def test_create_and_seed(self, sess_env):
        sess_env.neo4j.count_api_sessions.return_value = 0
        sess_env.neo4j.create_api_session.return_value = {
            "id": "ses_1", "name": "migrated", "turn_count": 2,
            "created_at": "2026-08-10", "updated_at": "2026-08-10",
        }
        r = sess_env.client.post("/api/sessions", json={
            "name": "migrated",
            "history": [{"role": "user", "content": "q"},
                        {"role": "assistant", "content": "a"}],
            "memory": {"version": 3, "facts": ["seeded"]},
        })
        assert r.status_code == 200
        assert r.json()["id"] == "ses_1"
        args = sess_env.neo4j.create_api_session.call_args.args
        assert json.loads(args[2])[0]["content"] == "q"       # history seeded
        assert json.loads(args[3])["facts"] == ["seeded"]     # memory seeded
        assert args[4] == 2                                    # turn_count

    def test_quota_enforced(self, sess_env, _isolate_env):
        _isolate_env.session_max_per_key = 3
        sess_env.neo4j.count_api_sessions.return_value = 3
        r = sess_env.client.post("/api/sessions", json={})
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "session_quota_exceeded"

    def test_get_returns_parsed_state(self, sess_env):
        sess_env.neo4j.get_api_session.return_value = _row(
            history=[{"role": "user", "content": "q"}],
            memory={"version": 3},
        )
        body = sess_env.client.get("/api/sessions/ses_abc").json()
        assert body["history"][0]["content"] == "q"
        assert body["memory"] == {"version": 3}

    def test_get_foreign_session_404(self, sess_env):
        sess_env.neo4j.get_api_session.return_value = None
        assert sess_env.client.get("/api/sessions/ses_x").status_code == 404

    def test_delete_404_when_missing(self, sess_env):
        sess_env.neo4j.delete_api_session.return_value = False
        assert sess_env.client.delete("/api/sessions/nope").status_code == 404


# ---------------------------------------------------------------------------
# Ask integration
# ---------------------------------------------------------------------------

class TestAskIntegration:
    def test_session_conflict_400(self, sess_env):
        r = sess_env.client.post("/api/ask", json={
            "question": "q", "session_id": "ses_abc",
            "conversation_memory": {},
        })
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "session_conflict"

    def test_fast_search_rejected(self, sess_env):
        r = sess_env.client.post("/api/ask/stream", json={
            "question": "q", "session_id": "ses_abc", "use_fast_search": True,
        })
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "session_not_supported"

    def test_foreign_session_404(self, sess_env):
        sess_env.neo4j.get_api_session.return_value = None
        r = sess_env.client.post("/api/ask", json={
            "question": "q", "session_id": "ses_foreign",
        })
        assert r.status_code == 404

    def test_disabled_flag_403(self, sess_env, _isolate_env):
        _isolate_env.enable_sessions = False
        r = sess_env.client.post("/api/ask", json={
            "question": "q", "session_id": "ses_abc",
        })
        assert r.status_code == 403

    def test_non_streaming_injects_history_and_persists(self, sess_env):
        sess_env.neo4j.get_api_session.return_value = _row(
            history=[{"role": "user", "content": "earlier q"},
                     {"role": "assistant", "content": "earlier a"}],
        )
        sess_env.query.rag_query = AsyncMock(return_value={
            "question": "q", "answer": "fresh answer", "sources": [],
            "graph_context": None, "reranked": False, "reasoning_steps": None,
        })
        r = sess_env.client.post("/api/ask", json={
            "question": "follow-up", "session_id": "ses_abc",
        })
        assert r.status_code == 200
        # stored history rode into the pipeline
        _, kwargs = sess_env.query.rag_query.call_args
        assert [m.content for m in kwargs["conversation_history"]] == \
            ["earlier q", "earlier a"]
        # turn persisted: 2 prior + question + answer
        args = sess_env.neo4j.update_api_session_state.call_args.args
        assert args[0] == "ses_abc"
        history = json.loads(args[2])
        assert len(history) == 4
        assert history[-1] == {"role": "assistant", "content": "fresh answer"}

    def test_streaming_captures_post_done_memory_update(self, sess_env, _isolate_env):
        _isolate_env.enable_agent_chat = True
        _isolate_env.openai_api_key = "test-key"  # pass the stream config gate

        async def fake_stream(**kwargs):
            # session memory {} must have opted the pipeline in
            assert kwargs["conversation_memory"] == {}
            yield {"content": "tok1 "}
            yield {"content": "tok2"}
            yield {"done": True, "pending_memory": True}
            yield {"memory_update": {"version": 3, "facts": ["learned"]}}

        sess_env.query.agent_rag_stream = fake_stream
        sess_env.neo4j.get_api_session.return_value = _row()

        r = sess_env.client.post("/api/ask/stream", json={
            "question": "q", "session_id": "ses_abc",
        })
        assert r.status_code == 200
        assert "tok1" in r.text
        args = sess_env.neo4j.update_api_session_state.call_args.args
        history = json.loads(args[2])
        assert history[-1]["content"] == "tok1 tok2"
        assert json.loads(args[3])["facts"] == ["learned"]  # post-done frame kept

    def test_features_advertises_sessions(self, sess_env):
        body = sess_env.client.get("/api/features").json()
        assert body["enable_sessions"] is True
