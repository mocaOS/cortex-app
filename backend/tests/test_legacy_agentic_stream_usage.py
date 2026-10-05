"""Supplemental usage-tail composition gate for the legacy streaming flags.

Post-candidate ADDITIVE coverage only; the v2 gate/oracle/runner and the
runtime are immutable after their executions. The v2 baseline length rows
remain the omission sensitivity; they do not independently demonstrate a
trailing-guard: a plausible implementation that erases the captured
`length` when a trailing null-reason or usage-only chunk arrives would pass
every plain length case. This module adds the discriminating composition:
the synthesis stream carries the exact canned tokens, a content-free
`length` reason chunk, then a trailing content-free null-reason chunk and
an empty-choices usage-only tail — the public `done.truncated` must STILL
be exactly true (the existing positive cut contract; no new runtime bug is
claimed here — this is a supplemental healthy composition case on the
accepted candidate runtime).

Two rows, one per real-auth streaming entry (`POST /api/ask/stream`,
`POST /api/ask/stream/thinking`, depth "deep", flag off), reusing the
imported unchanged `legacy_agentic_env` fixture, scope helpers, and the v2
gate's canned reason factory. The v2 source file is NOT modified: the
factory's chunks supplier (`_synthesis_chunks`) is monkeypatched test-locally
to the usage-tail sequence.

Raw provider chunk receipts (including the observed usage-tail numbers) and
the actual public SSE frames are appended to JUnit `user_properties` BEFORE
value assertions. Evidence boundary unchanged: canned/recording transport —
real-auth orchestration and public SSE projection only; no live provider,
store, or observability (Langfuse) proof.
"""

from __future__ import annotations

import json

import pytest

from tests.test_legacy_agentic_scope import (  # noqa: F401
    CANNED_ANSWER_TOKENS,
    COMMUNITY_ID,
    ENDPOINTS,
    SUB_QUESTIONS,
    _frames_of_type,
    _run_stream,
    _single_frame,
    legacy_agentic_env,  # imported pytest fixture — reused, not cloned
)
import tests.test_legacy_agentic_stream_flags as _v2_gate
from tests.test_legacy_agentic_stream_flags import (  # import-only; v2 source immutable
    _canned_reason_stream_factory,
    _reason_chunk,
    _token_chunk,
    _USAGE_CHUNK,
)


def _usage_tail_chunks(scenario):
    """Test-local replacement for the v2 factory's chunks supplier."""
    assert scenario == "usage-tail", scenario
    chunks = [_token_chunk(token) for token in CANNED_ANSWER_TOKENS]
    chunks.append(_reason_chunk("length"))
    chunks.append(_reason_chunk(None))
    chunks.append(_USAGE_CHUNK)
    return chunks


@pytest.mark.parametrize("endpoint_path", ENDPOINTS, ids=["stream", "thinking"])
def test_length_survives_trailing_null_reason_and_usage_tail(
    endpoint_path, legacy_agentic_env, monkeypatch, request,
):
    from app.services import document_processor as dp

    env = legacy_agentic_env
    chunk_log = []
    reason_calls = []
    monkeypatch.setattr(
        _v2_gate, "_synthesis_chunks", _usage_tail_chunks, raising=True)
    monkeypatch.setattr(
        dp, "make_async_openai_client",
        lambda **kwargs: _canned_reason_stream_factory(
            reason_calls, chunk_log, "stop", "usage-tail")(),
        raising=True,
    )
    frames = _run_stream(env, endpoint_path, "unrestricted", entry="depth")

    # Raw provider + actual public observations are retained BEFORE any
    # value assertion so a failing row preserves its counterexample.
    request.node.user_properties.append(
        ("legacy_stream_usage_tail_receipt", json.dumps({
            "case": request.node.callspec.id,
            "endpoint_path": endpoint_path,
            "scenario": "usage-tail-after-length",
            "decompose_reason": "stop",
            "raw_provider_chunks": chunk_log,
            "public_frames": frames,
            "llm_calls": reason_calls,
            "receipt_class": "raw-provider-chunks+public-frames",
        }, sort_keys=True)))

    # Raw provider observations: exact tokens, the length reason, then the
    # trailing null reason and the usage-only tail (raw usage numbers
    # observed and retained).
    assert [c["stream"] for c in reason_calls] == [False, True], reason_calls
    assert [c["role"] for c in reason_calls] == ["decompose", "synthesis"]
    tokens = "".join(
        c["delta_content"] for c in chunk_log
        if isinstance(c.get("delta_content"), str))
    assert tokens == "".join(CANNED_ANSWER_TOKENS), (tokens, chunk_log)
    with_choices = [c for c in chunk_log if c["n_choices"]]
    assert len(with_choices) == 4, chunk_log  # two tokens + length + null
    assert [c["finish_reason"] for c in with_choices[2:]] == ["length", None], (
        chunk_log)
    assert all(c["delta_content"] in (None, "__absent__")
               for c in with_choices[2:]), chunk_log
    usage_tail = chunk_log[-1]
    assert usage_tail["n_choices"] == 0, usage_tail
    assert usage_tail.get("usage", {}).get("total_tokens") == 8, usage_tail

    # Public contract (unchanged): exact tokens, done last with the
    # community IDs, and the existing positive cut flag survives the tail.
    content = "".join(f["content"] for f in _frames_of_type(frames, "content"))
    assert content == "".join(CANNED_ANSWER_TOKENS), (
        f"public answer tokens must be preserved exactly; got {content!r}")
    done = _single_frame(frames, "done")
    assert done.get("done") is True
    assert frames and frames[-1].get("type") == "done", frames[-3:]
    assert done.get("communities_used") == [COMMUNITY_ID], done
    assert done.get("truncated") is True, (
        "the captured length reason must survive the trailing null-reason "
        f"and usage-only tail; done={done!r} raw={chunk_log!r}")
