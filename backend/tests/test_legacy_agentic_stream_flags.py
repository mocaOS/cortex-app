"""Legacy flag-off agentic STREAMING completion-flags gate (pre-repair freeze).

Real-auth HTTP streaming entry (`POST /api/ask/stream`,
`POST /api/ask/stream/thinking`, depth "deep" / legacy `use_agentic`) with
`ENABLE_AGENT_RESEARCH=false` → real `QueryProcessor.agentic_rag_stream` over
the imported recording transport, with a canned async OpenAI client whose
synthesis stream terminates in content-free finish_reason chunks (the shape
real providers deliver: a final `delta` with no `content` carrying
`finish_reason`, optionally followed by empty-choices/usage-only chunks).

Additive acceptance delta (successor of the nonstream v3 gate,
output/legacy-agentic-nonstream-flags-20261004): the SYNTHESIS provider
stream's finish_reason is intended to reach the public `done` frame as
`truncated` — `length` → truncated true; `stop`, null, and a missing
`finish_reason` attribute must never set the flag (absent or false both
accepted; no new SSE field is required). The decompose call's reason must
never become the answer's truncation. Existing scope/progress/community
controls stay intact and answer tokens must be preserved exactly (no
appended notices in this slice).

Pre-repair freeze obligation: on the unchanged source the four synthesis-
`length` rows fail on the public truncated value only, while stop/null/
missing/decompose/no-reason/empty-choices rows pass healthy. Baseline here
is the retained-defect receipt; the runtime repair is a separate owned slice.

Gate v2 (post independent conditional review of the v1 baseline): the
no-flag assertions are tightened to `done.get("truncated", False) is False`
in both healthy branches — the public flag must be absent or exactly false,
not merely not-true (invalid types like a truthy string are rejected). The
absent-or-false promise itself is unchanged from v1.

Evidence boundary: recording-driver assembly and canned provider transport
only. The store never executes; this proves neither returned-data isolation,
live Cypher, production parity, nor model quality. Fixture and helpers are
imported from `tests.test_legacy_agentic_scope` (the `legacy_agentic_env`
pytest fixture is reused, not cloned; no global harness mutation). Raw
provider chunk observations and actual public SSE frames are retained in the
JUnit `user_properties` of every row BEFORE value assertions, including
healthy rows; the existing-helper row is public-frames-only because the
unchanged fixture factory exposes no chunk hook.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.test_legacy_agentic_scope import (  # noqa: F401
    CANNED_ANSWER_TOKENS,
    COMMUNITY_ID,
    ENDPOINTS,
    QUESTION,
    SUB_QUESTIONS,
    SCOPE_CASES,
    _LEG_RRF_MARKERS,
    _assert_attribution,
    _assert_graph_leg_scope,
    _frames_of_type,
    _run_stream,
    _single_frame,
    _subquery_observations,
    legacy_agentic_env,  # imported pytest fixture — reused, not cloned
)

_ABSENT = "__absent__"

_ENDPOINT_PATHS = {"stream": ENDPOINTS[0], "thinking": ENDPOINTS[1]}


# ---------------------------------------------------------------------------
# Scenario canned transport: synthesis stream with content-free reason chunks
# ---------------------------------------------------------------------------


def _token_chunk(token):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=token))]
    )


def _reason_chunk(reason=None, with_attr=True):
    delta = SimpleNamespace(content=None)
    choice = SimpleNamespace(delta=delta)
    if with_attr:
        choice.finish_reason = reason
    return SimpleNamespace(choices=[choice])


_EMPTY_CHUNK = SimpleNamespace(choices=[])
_USAGE_CHUNK = SimpleNamespace(
    choices=[],
    usage=SimpleNamespace(prompt_tokens=3, completion_tokens=5, total_tokens=8),
)


def _synthesis_chunks(scenario):
    """Token chunks plus the scenario's terminal, content-free chunks."""
    chunks = []
    if scenario == "empty-choices":
        chunks.append(_EMPTY_CHUNK)
    chunks.extend(_token_chunk(token) for token in CANNED_ANSWER_TOKENS)
    if scenario == "length":
        chunks.append(_reason_chunk("length"))
    elif scenario == "stop":
        chunks.append(_reason_chunk("stop"))
    elif scenario == "null":
        chunks.append(_reason_chunk(None))
    elif scenario == "missing-attr":
        chunks.append(_reason_chunk(with_attr=False))
    elif scenario == "empty-choices":
        chunks.append(_EMPTY_CHUNK)
        chunks.append(_reason_chunk("stop"))
        chunks.append(_USAGE_CHUNK)
    return chunks


def _chunk_receipt(chunk):
    """Serialize one raw provider chunk independently of the public frames."""
    choices = list(getattr(chunk, "choices", None) or [])
    receipt = {"n_choices": len(choices)}
    usage = getattr(chunk, "usage", None)
    if usage is not None:
        receipt["usage"] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
    if choices:
        choice = choices[0]
        delta = getattr(choice, "delta", None)
        receipt["delta_content"] = (
            getattr(delta, "content", _ABSENT) if delta is not None else _ABSENT
        )
        receipt["finish_reason"] = getattr(choice, "finish_reason", _ABSENT)
    return receipt


def _canned_reason_stream_factory(llm_calls, chunk_log, decompose_reason, scenario):
    decompose = SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(
            content=json.dumps({"sub_questions": list(SUB_QUESTIONS)})),
        finish_reason=decompose_reason,
    )])
    chunks = _synthesis_chunks(scenario)

    class _ReasonStreamClient:
        def __init__(self):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self._create))

        async def _create(self, **kwargs):
            if not kwargs.get("stream"):
                llm_calls.append({
                    "stream": False,
                    "role": "decompose",
                    "raw_finish_reason": decompose.choices[0].finish_reason,
                })
                return decompose
            llm_calls.append({"stream": True, "role": "synthesis"})

            async def _tokens():
                for chunk in chunks:
                    chunk_log.append(_chunk_receipt(chunk))
                    yield chunk
            return _tokens()

    return _ReasonStreamClient


# ---------------------------------------------------------------------------
# Bounded discriminating matrix (not a Cartesian grid).
#
# Expected-baseline-failing rows (product value only): the four synthesis-
# length combinations of BOTH endpoints × BOTH legacy entries. Healthy rows
# each discriminate one contract clause: stop on the remaining endpoint/entry
# combinations, null reason, missing finish_reason attribute, decompose-reason
# non-leakage, empty-choices/usage-only crash tolerance, and restricted-key
# scope/progress preservation under reason chunks. A separate row composes
# with the fixture's unchanged canned helper, which emits no finish_reason
# chunk at all.
# ---------------------------------------------------------------------------


_CASES = [
    # Expected product-value failures on the unchanged (pre-repair) source.
    pytest.param("stream", "depth", "length", "stop", "unrestricted", True,
                 id="length-synthesis-depth-deep-stream"),
    pytest.param("stream", "flag", "length", "stop", "unrestricted", True,
                 id="length-synthesis-legacy-flag-stream"),
    pytest.param("thinking", "depth", "length", "stop", "unrestricted", True,
                 id="length-synthesis-depth-deep-thinking"),
    pytest.param("thinking", "flag", "length", "stop", "unrestricted", True,
                 id="length-synthesis-legacy-flag-thinking"),
    # Healthy reason and compatibility controls.
    pytest.param("stream", "depth", "stop", "stop", "unrestricted", False,
                 id="stop-synthesis-depth-deep-stream"),
    pytest.param("thinking", "flag", "stop", "stop", "unrestricted", False,
                 id="stop-synthesis-legacy-flag-thinking"),
    pytest.param("thinking", "depth", "null", "stop", "unrestricted", False,
                 id="null-reason-depth-deep-thinking"),
    pytest.param("stream", "flag", "missing-attr", "stop", "unrestricted", False,
                 id="missing-reason-attr-legacy-flag-stream"),
    pytest.param("stream", "depth", "stop", "length", "unrestricted", False,
                 id="decompose-length-no-leak-depth-deep-stream"),
    pytest.param("stream", "flag", "empty-choices", "stop", "unrestricted", False,
                 id="empty-choices-usage-only-legacy-flag-stream"),
    pytest.param("thinking", "depth", "stop", "stop", "multi", False,
                 id="restricted-multi-scope-progress-stop-depth-deep-thinking"),
]


def _receipt_property(request, payload):
    request.node.user_properties.append(
        ("legacy_stream_flags_receipt", json.dumps(payload, sort_keys=True))
    )


def _assert_public_stream_shape(frames, terminal_receipt):
    content = "".join(
        f["content"] for f in _frames_of_type(frames, "content")
    )
    assert content == "".join(CANNED_ANSWER_TOKENS), (
        "public answer tokens must be preserved exactly (no appended "
        f"notices in this slice); got {content!r}"
    )
    done = _single_frame(frames, "done")
    assert done.get("done") is True
    assert frames and frames[-1].get("type") == "done", (
        f"done must be the final public frame; tail={frames[-3:]}"
    )
    assert done.get("communities_used") == [COMMUNITY_ID], (
        f"done frame community IDs drifted: {done.get('communities_used')!r}"
    )
    content_indices = [
        i for i, f in enumerate(frames) if f.get("type") == "content"
    ]
    assert content_indices and content_indices[0] < len(frames) - 1, (
        "content frames must precede the done frame"
    )
    return done


@pytest.mark.parametrize(
    "endpoint,entry,scenario,decompose_reason,scope_case,expect_truncated",
    _CASES,
)
def test_legacy_stream_synthesis_finish_reason_public_truncation(
    endpoint, entry, scenario, decompose_reason, scope_case, expect_truncated,
    legacy_agentic_env, monkeypatch, request,
):
    from app.services import document_processor as dp

    env = legacy_agentic_env
    chunk_log = []
    reason_calls = []
    monkeypatch.setattr(
        dp, "make_async_openai_client",
        lambda **kwargs: _canned_reason_stream_factory(
            reason_calls, chunk_log, decompose_reason, scenario)(),
        raising=True,
    )
    frames = _run_stream(env, _ENDPOINT_PATHS[endpoint], scope_case, entry=entry)

    # Raw provider + actual public observations are retained BEFORE any value
    # assertion so even a failing row preserves its counterexample.
    _receipt_property(request, {
        "case": request.node.callspec.id,
        "endpoint": endpoint,
        "entry": entry,
        "scenario": scenario,
        "decompose_reason": decompose_reason,
        "scope_case": scope_case,
        "raw_provider_chunks": chunk_log,
        "public_frames": frames,
        "llm_calls": reason_calls,
        "receipt_class": "raw-provider-chunks+public-frames",
    })

    # Raw provider observations, independent of the public projection:
    # exactly one canned decompose (non-stream) then one synthesis stream,
    # and the synthesis terminal chunk carries the scenario reason with no
    # delta content.
    assert [c["stream"] for c in reason_calls] == [False, True], (
        f"expected exactly one decompose (non-stream) then one synthesis "
        f"stream call, got {reason_calls}"
    )
    assert [c["role"] for c in reason_calls] == ["decompose", "synthesis"]
    assert reason_calls[0]["raw_finish_reason"] == decompose_reason, (
        f"raw decompose finish_reason drifted: {reason_calls}"
    )
    with_choices = [c for c in chunk_log if c["n_choices"]]
    assert with_choices, f"provider stream emitted no chunk with choices: {chunk_log}"
    terminal = with_choices[-1]
    assert terminal["delta_content"] in (None, _ABSENT), (
        f"terminal provider chunk must be content-free: {terminal!r}"
    )
    expected_terminal = {
        "length": "length",
        "stop": "stop",
        "null": None,
        "missing-attr": _ABSENT,
        "empty-choices": "stop",
    }[scenario]
    assert terminal["finish_reason"] == expected_terminal, (
        f"terminal provider finish_reason {terminal['finish_reason']!r} != "
        f"{expected_terminal!r}; raw chunks: {chunk_log}"
    )
    tokens = "".join(
        c["delta_content"] for c in chunk_log
        if isinstance(c.get("delta_content"), str)
    )
    assert tokens == "".join(CANNED_ANSWER_TOKENS), (
        f"raw provider token stream drifted: {tokens!r}"
    )

    # Healthy progress/community/scope controls (also pass on the pre-repair
    # source; the failing rows must fail on the truncated value only).
    thinking = _frames_of_type(frames, "thinking")
    assert thinking and thinking[0]["thinking"] == "Analyzing question complexity...", (
        f"first thinking frame is not the legacy pipeline's: {thinking[:1]}"
    )
    assert _single_frame(frames, "sub_questions")["sub_questions"] == SUB_QUESTIONS
    sources = _single_frame(frames, "sources")["sources"]
    assert sources, "legacy agentic stream must deliver populated sources"
    stats = _single_frame(frames, "retrieval_stats")["retrieval_stats"]
    assert stats["total_sources"] > 0 and stats["communities_used"] >= 1
    assert _frames_of_type(frames, "retrieval"), "retrieval progress frames missing"
    assert _frames_of_type(frames, "graph_context"), "graph_context frame missing"
    if scope_case != "unrestricted":
        _, groups, _ = _subquery_observations(env)
        issues = []
        for i, subq in enumerate(SUB_QUESTIONS):
            vec = groups[i].get(_LEG_RRF_MARKERS["vector"])
            if vec is None:
                issues.append(
                    f"sub-question {subq!r}: no vector store call recorded "
                    f"(group markers: {list(groups[i])})"
                )
                continue
            query, params = vec
            _assert_attribution("vector", params, subq, issues)
            _assert_graph_leg_scope(scope_case, "vector", query, params, subq, issues)
        assert not issues, "restricted-scope observations failed:\n" + "\n".join(issues)

    done = _assert_public_stream_shape(frames, terminal)

    # The additive public value under freeze (added/changed obligation):
    # synthesis length → done.truncated true; stop/null/missing → never true
    # (absent or false accepted; no new SSE finish_reason field is required).
    if expect_truncated:
        assert done.get("truncated") is True, (
            "synthesis provider finish_reason 'length' must surface as the "
            f"public done.truncated=true (pre-repair freeze); done={done!r} "
            f"terminal={terminal!r}"
        )
    else:
        assert done.get("truncated", False) is False, (
            f"stop/null/missing provider reasons must leave the public "
            f"truncation flag absent or false (gate v2); done={done!r}"
        )


def test_existing_canned_helper_without_finish_reason_still_completes(
    legacy_agentic_env, request,
):
    """Compose with the fixture's unchanged canned streaming helper.

    The existing helper yields only token chunks and never a finish_reason
    chunk; the additive flag semantics must leave that stream healthy (done
    present, exact tokens, no truncation flag). Raw-chunk retention is
    public-frames-only: the unchanged fixture factory exposes no chunk hook.
    """
    endpoint, entry = ENDPOINTS[1], "depth"
    env = legacy_agentic_env
    frames = _run_stream(env, endpoint, "unrestricted", entry=entry)

    _receipt_property(request, {
        "case": "existing-canned-helper",
        "endpoint": "thinking",
        "endpoint_path": endpoint,
        "entry": entry,
        "scenario": "existing-helper-no-reason-chunks",
        "raw_provider_capture": (
            "public-frames-only; the fixture's unchanged _canned_llm_factory "
            "emits token chunks without finish_reason chunks and exposes no "
            "chunk hook"
        ),
        "public_frames": frames,
        "llm_calls": env.llm_calls,
        "receipt_class": "public-frames-only",
    })

    assert [c["stream"] for c in env.llm_calls] == [False, True], (
        f"expected the fixture's own decompose+stream calls, got {env.llm_calls}"
    )
    thinking = _frames_of_type(frames, "thinking")
    assert thinking and thinking[0]["thinking"] == "Analyzing question complexity..."
    assert _single_frame(frames, "sub_questions")["sub_questions"] == SUB_QUESTIONS
    assert _single_frame(frames, "sources")["sources"]
    done = _assert_public_stream_shape(frames, None)
    assert done.get("truncated", False) is False, (
        "a stream with no provider reason chunk must leave truncated absent "
        f"or false (gate v2); done={done!r}"
    )
