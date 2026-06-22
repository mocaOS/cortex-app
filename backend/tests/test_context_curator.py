"""Unit tests for context_curator pure helpers (conversation memory).

Covers source_sid (stable citation identity), render_memory_block (bucket
rendering + caps + malformed-input tolerance), and build_context (legacy
truncation vs memory-block injection, summarized_count clamping). clamp_memory_blob
is already covered by test_phase1_trims.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import ConversationMessage
from app.services.context_curator import (
    build_context,
    render_memory_block,
    source_sid,
)


# --- source_sid --------------------------------------------------------------

def test_source_sid_is_stable_and_prefixed():
    s = {"chunk_id": "chunk-42", "content": "x"}
    sid = source_sid(s)
    assert sid.startswith("s_") and len(sid) == 14  # "s_" + 12 hex
    # same chunk_id -> same sid regardless of other fields
    assert source_sid({"chunk_id": "chunk-42", "content": "different"}) == sid


def test_source_sid_falls_back_to_filename_and_content():
    a = {"filename": "doc.md", "content": "hello world"}
    b = {"filename": "doc.md", "content": "hello world"}
    c = {"filename": "doc.md", "content": "totally different"}
    assert source_sid(a) == source_sid(b)
    assert source_sid(a) != source_sid(c)


# --- render_memory_block -----------------------------------------------------

def test_render_memory_block_empty_for_non_dict_or_empty():
    assert render_memory_block(None) == ""
    assert render_memory_block("not a dict") == ""
    assert render_memory_block({}) == ""


def test_render_memory_block_includes_buckets():
    mem = {
        "intent": "wants concise answers",
        "facts": ["f1", "f2"],
        "open_questions": ["q1"],
        "source_ledger": [{"sid": "s_a", "filename": "d.md", "gist": "g"}],
        "transcript": {"summary": "earlier chat"},
    }
    block = render_memory_block(mem)
    assert "wants concise answers" in block
    assert "- f1" in block and "- f2" in block
    assert "- q1" in block
    assert "[s_a] d.md: g" in block
    assert "earlier chat" in block


def test_render_memory_block_caps_facts_at_12():
    mem = {"facts": [f"fact{i}" for i in range(50)]}
    block = render_memory_block(mem)
    assert block.count("- fact") == 12


# --- build_context -----------------------------------------------------------

def _settings(mem_enabled=True, max_hist=5):
    return SimpleNamespace(
        enable_conversation_memory=mem_enabled, max_conversation_history=max_hist
    )


def _hist(n):
    return [ConversationMessage(role="user", content=f"m{i}") for i in range(n)]


def test_build_context_legacy_truncation_without_memory():
    out = build_context(_hist(10), None, _settings(max_hist=3))
    assert len(out) == 3 and out[-1].content == "m9"


def test_build_context_zero_max_history_keeps_all():
    out = build_context(_hist(4), None, _settings(max_hist=0))
    assert len(out) == 4


def test_build_context_injects_memory_block_and_verbatim_tail():
    mem = {"facts": ["f1"], "transcript": {"summarized_count": 2}}
    out = build_context(_hist(5), mem, _settings())
    assert out[0].role == "user" and out[0].content.startswith("[Conversation memory]")
    # 5 history msgs, 2 summarized -> 3 kept verbatim, after the memory block
    assert [m.content for m in out[1:]] == ["m2", "m3", "m4"]


def test_build_context_clamps_oversized_summarized_count():
    mem = {"facts": ["f1"], "transcript": {"summarized_count": 999}}
    out = build_context(_hist(3), mem, _settings())
    # summarized_count clamped to len(history); only the memory block remains
    assert len(out) == 1 and out[0].content.startswith("[Conversation memory]")


def test_build_context_disabled_memory_uses_legacy():
    mem = {"facts": ["f1"]}
    out = build_context(_hist(4), mem, _settings(mem_enabled=False, max_hist=2))
    assert len(out) == 2 and all(not m.content.startswith("[Conversation") for m in out)
