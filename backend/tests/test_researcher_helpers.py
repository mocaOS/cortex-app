"""Unit tests for researcher_agent pure helpers.

The agent loop's building blocks — graph-context merge/dedup, source dedup,
skill HTTP-response truncation, and skill variable substitution — are pure and
offline-testable. (Prompt/tool assembly is covered by test_researcher_prompt;
the live tool-calling loop needs a mocked LLM and is exercised at the contract
level by test_api_endpoints_smoke.)
"""

from __future__ import annotations

import pytest

from app.services.researcher_agent import (
    _deduplicate_sources,
    _merge_graph_context,
    _substitute_variables,
    _truncate_response,
)


# --- _merge_graph_context ----------------------------------------------------

def test_merge_graph_context_dedupes_entities_rels_chunks():
    acc = {"entities": [{"name": "A"}], "relationships": [], "chunks": []}
    new = {
        "entities": [{"name": "A"}, {"name": "B"}],  # A is dup
        "relationships": [{"source": "A", "type": "REL", "target": "B"}],
        "chunks": [{"chunk_id": "c1"}, {"chunk_id": "c1"}, {"no_id": 1}],
    }
    _merge_graph_context(acc, new)
    assert [e["name"] for e in acc["entities"]] == ["A", "B"]
    assert len(acc["relationships"]) == 1
    assert [c["chunk_id"] for c in acc["chunks"]] == ["c1"]  # dup + missing-id dropped


def test_merge_graph_context_empty_is_noop():
    acc = {"entities": [{"name": "A"}], "relationships": [], "chunks": []}
    _merge_graph_context(acc, {})
    assert acc["entities"] == [{"name": "A"}]


# --- _deduplicate_sources ----------------------------------------------------

def test_deduplicate_sources_keeps_highest_score_per_chunk():
    out = _deduplicate_sources([
        {"chunk_id": "c1", "score": 0.2},
        {"chunk_id": "c1", "rerank_score": 0.9},
        {"chunk_id": "c2", "score": 0.5},
    ])
    by_id = {s["chunk_id"]: s for s in out}
    assert by_id["c1"].get("rerank_score") == 0.9
    # sorted by score desc
    assert [s["chunk_id"] for s in out] == ["c1", "c2"]


def test_deduplicate_sources_keeps_idless_first():
    out = _deduplicate_sources([
        {"chunk_id": "c1", "score": 0.9},
        {"content": "skill api result"},  # no chunk_id -> always kept, first
    ])
    assert "chunk_id" not in out[0]
    assert out[1]["chunk_id"] == "c1"


# --- _truncate_response ------------------------------------------------------

def test_truncate_response_passthrough_under_budget():
    assert _truncate_response("short", max_chars=100) == "short"


def test_truncate_response_non_json_plain_truncates():
    text = "x" * 200
    assert _truncate_response(text, max_chars=50) == "x" * 50


def test_truncate_response_slims_json_array_to_fit():
    items = [{"id": i, "desc": "y" * 500} for i in range(5)]
    import json
    text = json.dumps({"data": items})
    out = _truncate_response(text, max_chars=600)
    assert len(out) <= 600
    parsed = json.loads(out)
    # all 5 items retained (slimmed), not dropped
    assert len(parsed["data"]) == 5
    assert parsed["data"][0]["desc"].endswith("...")


# --- _substitute_variables ---------------------------------------------------

def test_substitute_placeholder_from_config():
    assert _substitute_variables("Bearer ${API_TOKEN}", {"API_TOKEN": "secret"}) == "Bearer secret"


def test_substitute_unknown_placeholder_left_intact():
    assert _substitute_variables("x ${NOPE}", {"API_TOKEN": "s"}) == "x ${NOPE}"


def test_substitute_bare_uppercase_key():
    # LLM wrote the var name literally without ${}
    assert _substitute_variables("group=ZAMMAD_GROUP", {"ZAMMAD_GROUP": "Users"}) == "group=Users"


def test_substitute_skill_env_placeholder(monkeypatch):
    monkeypatch.setenv("SKILL_FOO", "envval")
    assert _substitute_variables("k=${SKILL_FOO}", {}) == "k=envval"
