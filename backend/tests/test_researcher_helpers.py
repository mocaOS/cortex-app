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
    _CitationStripper,
    _deduplicate_sources,
    _merge_graph_context,
    _substitute_variables,
    _truncate_response,
)


# --- _CitationStripper -------------------------------------------------------

def _stream(text: str, max_index: int, chunk: int) -> str:
    """Feed `text` through the stripper in `chunk`-sized pieces."""
    f = _CitationStripper(max_index)
    out = "".join(f.feed(text[i : i + chunk]) for i in range(0, len(text), chunk))
    return out + f.flush()


@pytest.mark.parametrize("chunk", [1, 3, 7, 1000])
def test_citation_stripper_drops_orphans_when_no_sources(chunk):
    # max_index == 0 (retrieval returned nothing): every marker + its leading
    # space is removed so it never renders as literal [src_N] text.
    out = _stream("Wählen Sie Neu [src_1]. Dann [src_1] erneut.", 0, chunk)
    assert out == "Wählen Sie Neu. Dann erneut."


@pytest.mark.parametrize("chunk", [1, 3, 7, 1000])
def test_citation_stripper_keeps_valid_strips_overindexed(chunk):
    out = _stream("A [src_1] and B [src_2] but C [src_3] gone", 2, chunk)
    assert out == "A [src_1] and B [src_2] but C gone"


@pytest.mark.parametrize("chunk", [1, 3, 7, 1000])
def test_citation_stripper_preserves_non_citation_brackets(chunk):
    out = _stream("See [note] and array[0] here", 0, chunk)
    assert out == "See [note] and array[0] here"


@pytest.mark.parametrize("chunk", [1, 3, 7, 1000])
def test_citation_stripper_does_not_eat_real_whitespace(chunk):
    out = _stream("para one\n\npara two", 5, chunk)
    assert out == "para one\n\npara two"


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
    out = _truncate_response(text, max_chars=50)
    # Body bounded at max_chars, followed by the explicit truncation trailer
    # (the model must know data was dropped and how to paginate for the rest).
    assert out.startswith("x" * 50)
    assert "x" * 51 not in out
    assert "truncated" in out and "?limit=" in out


def test_truncate_response_slims_json_array_to_fit():
    items = [{"id": i, "desc": "y" * 500} for i in range(5)]
    import json
    text = json.dumps({"data": items})
    out = _truncate_response(text, max_chars=600)
    # JSON body slimmed within budget; trailer appended after it.
    body, sep, trailer = out.rpartition("\n[NOTE: response truncated")
    assert sep, "truncation trailer missing"
    assert len(body) <= 600
    parsed = json.loads(body)
    # all 5 items retained (slimmed), not dropped
    assert len(parsed["data"]) == 5
    assert parsed["data"][0]["desc"].endswith("...")


def test_truncate_response_short_text_untouched():
    # No truncation → no trailer, byte-identical passthrough.
    assert _truncate_response("hello", max_chars=50) == "hello"


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


# ---------------------------------------------------------------------------
# _needs_grounding_guard (zero-search grounding fallback)
# ---------------------------------------------------------------------------

class _Settings:
    researcher_force_grounding = True


def _result(search_count=0, sources=None):
    from app.services.researcher_agent import ResearchResult

    r = ResearchResult()
    r.search_count = search_count
    r.sources = sources or []
    return r


def test_grounding_guard_fires_on_zero_searches():
    from app.services.researcher_agent import _needs_grounding_guard

    assert _needs_grounding_guard(False, _result(), _Settings()) is True


def test_grounding_guard_skips_fast_path_and_searched_runs():
    from app.services.researcher_agent import _needs_grounding_guard

    # Memory fast-path answers without retrieval by design
    assert _needs_grounding_guard(True, _result(), _Settings()) is False
    # Searched-but-empty already had its retrieval chance
    assert _needs_grounding_guard(False, _result(search_count=2), _Settings()) is False
    # Skill API responses land in sources — a skill-answered question is grounded
    assert _needs_grounding_guard(
        False, _result(sources=[{"content": "api data"}]), _Settings()
    ) is False


def test_grounding_guard_respects_flag():
    from app.services.researcher_agent import _needs_grounding_guard

    class _Off:
        researcher_force_grounding = False

    assert _needs_grounding_guard(False, _result(), _Off()) is False


# --- Reflection gate + novelty convergence -----------------------------------

class _ToolCall:
    def __init__(self, name):
        class _Fn:
            pass
        self.function = _Fn()
        self.function.name = name


def test_searches_without_reflection_detects_shotgun_volley():
    from app.services.researcher_agent import _searches_without_reflection

    assert _searches_without_reflection(
        [_ToolCall("knowledge_search"), _ToolCall("knowledge_search")]
    ) is True
    assert _searches_without_reflection([_ToolCall("entity_lookup")]) is True


def test_searches_without_reflection_passes_compliant_and_terminal():
    from app.services.researcher_agent import _searches_without_reflection

    # reasoning alongside searches = the gemma-style steered round
    assert _searches_without_reflection(
        [_ToolCall("reasoning"), _ToolCall("knowledge_search")]
    ) is False
    # done anywhere in the volley means the model is wrapping up
    assert _searches_without_reflection(
        [_ToolCall("knowledge_search"), _ToolCall("done")]
    ) is False
    # non-retrieval tools (skills, git) are not gated
    assert _searches_without_reflection([_ToolCall("http_request")]) is False
    assert _searches_without_reflection([]) is False
    assert _searches_without_reflection(None) is False


def _srcs(*cids):
    return [{"chunk_id": c} for c in cids]


def test_novelty_tracker_stops_after_consecutive_stale_rounds():
    from app.services.researcher_agent import _NoveltyTracker

    t = _NoveltyTracker(min_new_ratio=0.2, stale_limit=2)
    t.begin_round()
    t.observe(_srcs("a", "b", "c"))
    assert t.end_round() is False  # 100% new

    t.begin_round()
    t.observe(_srcs("a", "b", "c"))  # 0% new — stale #1
    assert t.end_round() is False

    t.begin_round()
    t.observe(_srcs("b", "c"))  # stale #2 → stop
    assert t.end_round() is True


def test_novelty_tracker_fresh_round_resets_stale_count():
    from app.services.researcher_agent import _NoveltyTracker

    t = _NoveltyTracker(min_new_ratio=0.2, stale_limit=2)
    t.begin_round(); t.observe(_srcs("a", "b")); t.end_round()
    t.begin_round(); t.observe(_srcs("a", "b")); assert t.end_round() is False
    t.begin_round(); t.observe(_srcs("x", "y"))  # fresh ground
    assert t.end_round() is False
    assert t.stale_rounds == 0


def test_novelty_tracker_dedup_hit_and_empty_rounds():
    from app.services.researcher_agent import _NoveltyTracker

    t = _NoveltyTracker(min_new_ratio=0.2, stale_limit=2)
    t.begin_round(); t.observe_dedup_hit()  # exact repeat = fully stale
    assert t.end_round() is False
    t.begin_round(); t.observe([])  # searched, zero results = stale
    assert t.end_round() is True
    # rounds without any search never count toward staleness
    t2 = _NoveltyTracker(min_new_ratio=0.2, stale_limit=1)
    t2.begin_round()
    assert t2.end_round() is False


def test_novelty_tracker_disabled_by_zero_knobs():
    from app.services.researcher_agent import _NoveltyTracker

    for t in (
        _NoveltyTracker(min_new_ratio=0, stale_limit=2),
        _NoveltyTracker(min_new_ratio=0.2, stale_limit=0),
    ):
        for _ in range(5):
            t.begin_round(); t.observe(_srcs("a")); 
            assert t.end_round() is False


def test_novelty_tracker_ignores_sources_without_chunk_id():
    from app.services.researcher_agent import _NoveltyTracker

    t = _NoveltyTracker(min_new_ratio=0.2, stale_limit=2)
    t.begin_round()
    t.observe([{"content": "skill api response"}])  # no chunk_id
    assert t.round_total == 0
    assert "no new sources" in t.note_line()


def test_quality_iteration_directive_shapes():
    from app.services.researcher_agent import (
        _NoveltyTracker,
        _quality_iteration_directive,
    )

    t = _NoveltyTracker(min_new_ratio=0.2, stale_limit=2)
    # iteration 0: nothing to reflect on yet
    assert _quality_iteration_directive(0, t, True) == ""
    # iteration 1+, no search round yet: directive only, no novelty line
    d = _quality_iteration_directive(1, t, True)
    assert "reasoning call" in d and "%" not in d
    # after a round, the novelty percentage is reported
    t.begin_round(); t.observe(_srcs("a", "b", "c", "d")); t.end_round()
    t.begin_round(); t.observe(_srcs("a", "b", "c", "x")); t.end_round()
    d = _quality_iteration_directive(2, t, True)
    assert "25% new" in d
    # force_reflection off: novelty still reported, directive absent
    d = _quality_iteration_directive(2, t, False)
    assert "25% new" in d and "reasoning call" not in d


# --- writer reasoning mode ---------------------------------------------------
# On thinking models the reasoning trace is billed against the same max_tokens
# budget as the visible answer, so a reasoning writer truncates deep-research
# answers mid-word (measured on qwen3-6-35b-a3b: 2.7k-4.3k trace tokens against
# a 4000 cap, sometimes leaving zero visible prose). The writer is therefore
# always OFF, while the researcher loop must keep AUTO in quality mode — the
# forced-reflection micro-call depends on the model actually thinking.

def test_writer_reasoning_is_off_in_both_modes():
    from app.services.reasoning_config import ReasoningMode
    from app.services.researcher_agent import _writer_reasoning_mode

    class S:
        default_reasoning_mode = "auto"

    assert _writer_reasoning_mode("quality", S()) is ReasoningMode.OFF
    assert _writer_reasoning_mode("speed", S()) is ReasoningMode.OFF


def test_researcher_loop_keeps_reasoning_in_quality_mode():
    from app.services.reasoning_config import ReasoningMode
    from app.services.researcher_agent import _chat_reasoning_mode

    class S:
        default_reasoning_mode = "off"

    # quality ignores DEFAULT_REASONING_MODE and stays AUTO
    assert _chat_reasoning_mode("quality", S()) is ReasoningMode.AUTO
    # speed follows the configured default
    assert _chat_reasoning_mode("speed", S()) is ReasoningMode.OFF


def test_researcher_loop_forces_reasoning_off_for_openai_gpt_models():
    # gpt-5.6-luna 400s when function tools are sent with any reasoning_effort
    # other than 'none' — including the provider default that applies when we
    # inject nothing (the quality-mode AUTO path). The researcher loop always
    # sends tools, so OpenAI reasoning families are pinned OFF in both modes.
    from app.services.reasoning_config import ReasoningMode
    from app.services.researcher_agent import _chat_reasoning_mode

    class S:
        default_reasoning_mode = "auto"

    for model in ("gpt-5.6-luna", "gpt-5.1", "gpt-5-mini", "o3-mini"):
        assert _chat_reasoning_mode("quality", S(), model) is ReasoningMode.OFF
        assert _chat_reasoning_mode("speed", S(), model) is ReasoningMode.OFF
    # non-OpenAI models keep the existing behavior
    assert _chat_reasoning_mode("quality", S(), "qwen3-32b") is ReasoningMode.AUTO
