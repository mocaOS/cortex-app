"""Tests for document-level entity extraction batching:
auto-summary mode (summary LLM call only for multi-batch documents) and
the output-truncation split-retry guard (finish_reason == "length")."""

from __future__ import annotations

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
