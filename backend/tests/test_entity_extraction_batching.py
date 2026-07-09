"""Tests for document-level entity extraction batching:
auto-summary mode (summary LLM call only for multi-batch documents),
the output-truncation split-retry guard (finish_reason == "length"),
and the run-health telemetry (run_stats counters, batch-start progress,
one-shot ratio diagnosis, repetition-loop detector)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_extractor import GraphExtractor


ENTITY_XML = (
    '<entity name="Alice"><type>Person</type>'
    "<description>Alice works at Acme.</description></entity>"
)


@pytest.fixture
def extractor():
    return GraphExtractor()


def _resp(content: str, finish_reason: str = "stop") -> MagicMock:
    r = MagicMock()
    choice = MagicMock()
    choice.finish_reason = finish_reason
    r.choices = [choice]
    r._content = content
    return r


def _wire_llm(extractor, monkeypatch, responses):
    """Fake extraction client + canned per-call responses."""
    monkeypatch.setattr(
        GraphExtractor, "async_extraction_client",
        property(lambda self: MagicMock()),
    )
    monkeypatch.setattr(
        GraphExtractor, "extraction_model_name",
        property(lambda self: "fake-model"),
    )
    extractor._async_safe_completion = AsyncMock(side_effect=responses)
    monkeypatch.setattr(
        extractor, "_extract_response_content", lambda response: response._content
    )
    extractor.generate_document_summary_async = AsyncMock(
        return_value="CANNED SUMMARY"
    )


# ~2,250 fallback-counted tokens per chunk (len // 4), so with a small
# max_tokens budget each chunk overflows a batch and forces a split.
BIG_CHUNK_A = "alpha " * 1500
BIG_CHUNK_B = "beta " * 1800
SMALL_CHUNKS = ["Alice works at Acme.", "Acme builds rockets."]


class TestAutoSummary:
    async def test_single_batch_skips_summary_call(self, extractor, monkeypatch):
        _wire_llm(extractor, monkeypatch, [_resp(ENTITY_XML)])
        entities = await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS, max_tokens=32768
        )
        assert [e.name for e in entities] == ["Alice"]
        assert extractor.generate_document_summary_async.await_count == 0
        assert extractor._async_safe_completion.await_count == 1
        prompt = extractor._async_safe_completion.await_args.kwargs["messages"][1]["content"]
        assert "No summary available." in prompt

    async def test_multi_batch_generates_summary_once(self, extractor, monkeypatch):
        _wire_llm(extractor, monkeypatch, [_resp(ENTITY_XML), _resp("")])
        await extractor.extract_entities_from_document_async(
            chunks=[BIG_CHUNK_A, BIG_CHUNK_B], max_tokens=1000
        )
        assert extractor.generate_document_summary_async.await_count == 1
        assert extractor._async_safe_completion.await_count == 2
        for call in extractor._async_safe_completion.await_args_list:
            assert "CANNED SUMMARY" in call.kwargs["messages"][1]["content"]

    async def test_explicit_summary_is_used_verbatim(self, extractor, monkeypatch):
        _wire_llm(extractor, monkeypatch, [_resp(ENTITY_XML)])
        await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS,
            document_summary="EXPLICIT SUMMARY",
            max_tokens=32768,
        )
        assert extractor.generate_document_summary_async.await_count == 0
        prompt = extractor._async_safe_completion.await_args.kwargs["messages"][1]["content"]
        assert "EXPLICIT SUMMARY" in prompt


class TestTruncationSplitRetry:
    async def test_truncated_batch_splits_and_retries_halves(
        self, extractor, monkeypatch
    ):
        second_entity = ENTITY_XML.replace("Alice", "Bob")
        _wire_llm(
            extractor,
            monkeypatch,
            [
                _resp(ENTITY_XML, finish_reason="length"),  # discarded
                _resp(ENTITY_XML),
                _resp(second_entity),
            ],
        )
        entities = await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS, max_tokens=32768
        )
        assert extractor._async_safe_completion.await_count == 3
        assert sorted(e.name for e in entities) == ["Alice", "Bob"]
        # halves cover the original batch exactly, in order
        half_prompts = [
            c.kwargs["messages"][1]["content"]
            for c in extractor._async_safe_completion.await_args_list[1:]
        ]
        assert SMALL_CHUNKS[0] in half_prompts[0]
        assert SMALL_CHUNKS[1] not in half_prompts[0]
        assert SMALL_CHUNKS[1] in half_prompts[1]

    async def test_single_chunk_truncation_keeps_parsed_entities(
        self, extractor, monkeypatch
    ):
        _wire_llm(extractor, monkeypatch, [_resp(ENTITY_XML, finish_reason="length")])
        entities = await extractor.extract_entities_from_document_async(
            chunks=[SMALL_CHUNKS[0]], max_tokens=32768
        )
        assert extractor._async_safe_completion.await_count == 1
        assert [e.name for e in entities] == ["Alice"]


class TestExtractionTelemetry:
    async def test_run_stats_counts_calls_and_splits(self, extractor, monkeypatch):
        second_entity = ENTITY_XML.replace("Alice", "Bob")
        _wire_llm(
            extractor,
            monkeypatch,
            [
                _resp(ENTITY_XML, finish_reason="length"),  # split
                _resp(ENTITY_XML),
                _resp(second_entity),
            ],
        )
        run_stats: dict = {}
        await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS, max_tokens=32768, run_stats=run_stats
        )
        assert run_stats["planned_batches"] == 1
        assert run_stats["llm_calls"] == 3
        assert run_stats["truncation_splits"] == 1
        assert run_stats["timeout_splits"] == 0
        assert run_stats["single_chunk_truncations"] == 0
        assert run_stats["errors"] == 0

    async def test_progress_fires_at_batch_start_with_detail(
        self, extractor, monkeypatch
    ):
        _wire_llm(extractor, monkeypatch, [_resp(ENTITY_XML)])
        calls: list = []

        async def cb(done: int, total: int, detail: str = "") -> None:
            calls.append((done, total, detail))

        await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS, max_tokens=32768, progress_callback=cb
        )
        # batch start (detail set, nothing done yet) then settle (no detail)
        assert calls[0] == (0, 2, "batch 1/1")
        assert calls[-1] == (2, 2, "")

    async def test_ratio_diagnosis_fires_once(self, extractor, monkeypatch, caplog):
        # 4 chunks, 1 planned batch. Overflow cascade: full batch truncates,
        # first half truncates, its single chunks pass, second half truncates,
        # then one single-chunk truncation — 4 overflows total, but the
        # budget-ratio diagnosis must be logged exactly once.
        chunks = ["Alice works at Acme."] * 4
        _wire_llm(
            extractor,
            monkeypatch,
            [
                _resp(ENTITY_XML, finish_reason="length"),  # [4] -> split
                _resp(ENTITY_XML, finish_reason="length"),  # [2] -> split
                _resp(ENTITY_XML),                          # [1]
                _resp(ENTITY_XML),                          # [1]
                _resp(ENTITY_XML, finish_reason="length"),  # [2] -> split
                _resp(ENTITY_XML, finish_reason="length"),  # [1] single-chunk
                _resp(ENTITY_XML),                          # [1]
            ],
        )
        run_stats: dict = {}
        with caplog.at_level(logging.WARNING, logger="app.services.graph_extractor"):
            await extractor.extract_entities_from_document_async(
                chunks=chunks, max_tokens=32768, run_stats=run_stats
            )
        diagnoses = [
            r for r in caplog.records if "output budget looks too small" in r.message
        ]
        assert len(diagnoses) == 1
        assert run_stats["truncation_splits"] == 3
        assert run_stats["single_chunk_truncations"] == 1

    async def test_repetition_loop_detector_flags_duplicate_flood(
        self, extractor, monkeypatch, caplog
    ):
        flood = ENTITY_XML * 35  # 35 identical entities from a 2-chunk batch
        _wire_llm(extractor, monkeypatch, [_resp(flood)])
        run_stats: dict = {}
        with caplog.at_level(logging.WARNING, logger="app.services.graph_extractor"):
            entities = await extractor.extract_entities_from_document_async(
                chunks=SMALL_CHUNKS, max_tokens=32768, run_stats=run_stats
            )
        assert run_stats["suspect_batches"] == 1
        assert any("repetition loop suspected" in r.message for r in caplog.records)
        # dedup still collapses the flood to one entity
        assert [e.name for e in entities] == ["Alice"]

    async def test_dense_unique_batch_is_not_flagged_suspect(
        self, extractor, monkeypatch, caplog
    ):
        # 45 UNIQUE entities from one chunk: entity-dense (>40/chunk) but 0%
        # duplicates — a list/index passage, not model degeneration. Live
        # 2026-07-09: this pattern produced 11 false "repetition loop" hits.
        dense = "".join(ENTITY_XML.replace("Alice", f"Name{i}") for i in range(45))
        _wire_llm(extractor, monkeypatch, [_resp(dense)])
        run_stats: dict = {}
        with caplog.at_level(logging.INFO, logger="app.services.graph_extractor"):
            entities = await extractor.extract_entities_from_document_async(
                chunks=[SMALL_CHUNKS[0]], max_tokens=32768, run_stats=run_stats
            )
        assert run_stats["suspect_batches"] == 0
        assert run_stats["dense_batches"] == 1
        assert not any(
            "repetition loop suspected" in r.message for r in caplog.records
        )
        assert any("entity-dense output" in r.message for r in caplog.records)
        assert len(entities) == 45


class TestTimeoutHandling:
    async def test_timeout_split_keeps_batch_numerator_within_denominator(
        self, extractor, monkeypatch
    ):
        # A split burns one call and queues two half-calls, so the expected
        # total grows by 2 — with +1 the display overran ("batch 53/43").
        _wire_llm(
            extractor,
            monkeypatch,
            [
                Exception("Request timed out."),  # [2 chunks] -> split
                _resp(ENTITY_XML),
                _resp(ENTITY_XML.replace("Alice", "Bob")),
            ],
        )
        details: list = []

        async def cb(done: int, total: int, detail: str = "") -> None:
            if detail:
                details.append(detail)

        run_stats: dict = {}
        await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS,
            max_tokens=32768,
            progress_callback=cb,
            run_stats=run_stats,
        )
        assert details == ["batch 1/1", "batch 2/3", "batch 3/3"]
        assert run_stats["timeout_splits"] == 1
        assert run_stats["llm_calls"] == 3

    async def test_timeout_diagnosis_fires_once(self, extractor, monkeypatch, caplog):
        # Timeout cascade: [4] times out, then each [2] half times out before
        # the singles pass — 3 timeout splits at >=25% of calls must produce
        # exactly one endpoint-too-slow diagnosis.
        chunks = ["Alice works at Acme."] * 4
        _wire_llm(
            extractor,
            monkeypatch,
            [
                Exception("Request timed out."),  # [4] -> split
                Exception("Request timed out."),  # [2] -> split
                _resp(ENTITY_XML),                # [1]
                _resp(ENTITY_XML),                # [1]
                Exception("Request timed out."),  # [2] -> split
                _resp(ENTITY_XML),                # [1]
                _resp(ENTITY_XML),                # [1]
            ],
        )
        run_stats: dict = {}
        with caplog.at_level(logging.WARNING, logger="app.services.graph_extractor"):
            await extractor.extract_entities_from_document_async(
                chunks=chunks, max_tokens=32768, run_stats=run_stats
            )
        diagnoses = [
            r for r in caplog.records if "keep timing out" in r.message
        ]
        assert len(diagnoses) == 1
        assert "GRAPH_EXTRACTION_MAX_CONTEXT" in diagnoses[0].message
        assert run_stats["timeout_splits"] == 3

    async def test_timeout_learns_budget_cap_for_next_document(
        self, extractor, monkeypatch, caplog
    ):
        # Doc 1: one full-size batch times out and splits. Doc 2 on the same
        # extractor must start with the learned smaller budget (more, smaller
        # planned batches) instead of re-running the split cascade.
        _wire_llm(
            extractor,
            monkeypatch,
            [
                Exception("Request timed out."),  # [A, B] -> split
                _resp(ENTITY_XML),
                _resp(ENTITY_XML.replace("Alice", "Bob")),
            ],
        )
        run1: dict = {}
        await extractor.extract_entities_from_document_async(
            chunks=[BIG_CHUNK_A, BIG_CHUNK_B], max_tokens=32768, run_stats=run1
        )
        assert run1["planned_batches"] == 1
        assert extractor._learned_entity_budget  # cap recorded

        extractor._async_safe_completion = AsyncMock(
            side_effect=[_resp(ENTITY_XML), _resp(ENTITY_XML)]
        )
        run2: dict = {}
        with caplog.at_level(logging.INFO, logger="app.services.graph_extractor"):
            await extractor.extract_entities_from_document_async(
                chunks=[BIG_CHUNK_A, BIG_CHUNK_B], max_tokens=32768, run_stats=run2
            )
        assert run2["planned_batches"] > run1["planned_batches"]
        assert run2["timeout_splits"] == 0
        assert any(
            "batch input budget capped" in r.message for r in caplog.records
        )

    async def test_rate_limited_batch_is_requeued_whole_with_backoff(
        self, extractor, monkeypatch
    ):
        # 429s must not split the batch (its size is fine) and must not be
        # dropped — requeue the whole batch after a backoff sleep.
        _wire_llm(
            extractor,
            monkeypatch,
            [
                Exception("Error code: 429 - rate limit exceeded"),
                _resp(ENTITY_XML),
            ],
        )
        sleeps: list = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr(
            "app.services.graph_extractor.asyncio.sleep", fake_sleep
        )
        run_stats: dict = {}
        entities = await extractor.extract_entities_from_document_async(
            chunks=SMALL_CHUNKS, max_tokens=32768, run_stats=run_stats
        )
        assert run_stats["rate_limit_retries"] == 1
        assert run_stats["timeout_splits"] == 0
        assert run_stats["errors"] == 0
        assert run_stats["llm_calls"] == 2
        assert sleeps == [5.0]
        assert [e.name for e in entities] == ["Alice"]
        # the retried call carries the SAME full batch, not a half
        prompts = [
            c.kwargs["messages"][1]["content"]
            for c in extractor._async_safe_completion.await_args_list
        ]
        assert prompts[0] == prompts[1]
