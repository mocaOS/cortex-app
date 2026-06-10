"""Tests for chunk-batched relationship extraction
(enable_batched_chunk_relationships): the grouped-XML parser and the
batch call's degradation ladder (grouped → flat → per-chunk fallback)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_extractor import GraphExtractor


@pytest.fixture
def extractor():
    return GraphExtractor()


# ---------------------------------------------------------------------------
# _extract_xml_grouped_relationships
# ---------------------------------------------------------------------------

class TestGroupedRelationshipParser:
    def test_indexed_blocks(self, extractor):
        content = """
        <chunk index="1">
            <relationship><source>A</source><target>B</target><type>USES</type></relationship>
        </chunk>
        <chunk index="2">
            <relationship><source>C</source><target>D</target><type>PART_OF</type></relationship>
            <relationship><source>D</source><target>C</target><type>CONTAINS</type></relationship>
        </chunk>
        """
        groups = extractor._extract_xml_grouped_relationships(content, 2)
        assert len(groups[0]) == 1
        assert groups[0][0]["source"] == "A"
        assert len(groups[1]) == 2

    def test_empty_chunk_block_yields_empty_list(self, extractor):
        content = '<chunk index="1"></chunk><chunk index="2"><relationship><source>X</source><target>Y</target><type>USES</type></relationship></chunk>'
        groups = extractor._extract_xml_grouped_relationships(content, 2)
        assert groups[0] == []
        assert len(groups[1]) == 1

    def test_missing_index_positional_fallback(self, extractor):
        content = (
            "<chunk><relationship><source>A</source><target>B</target><type>USES</type></relationship></chunk>"
            "<chunk><relationship><source>C</source><target>D</target><type>USES</type></relationship></chunk>"
        )
        groups = extractor._extract_xml_grouped_relationships(content, 2)
        assert groups[0][0]["source"] == "A"
        assert groups[1][0]["source"] == "C"

    def test_no_chunk_blocks_returns_none(self, extractor):
        content = "<relationship><source>A</source><target>B</target><type>USES</type></relationship>"
        assert extractor._extract_xml_grouped_relationships(content, 2) is None

    def test_inner_parsing_delegates_normalization(self, extractor):
        # type normalization comes from _extract_xml_relationships
        content = '<chunk index="1"><relationship><source>A</source><target>B</target><type>part of</type></relationship></chunk>'
        groups = extractor._extract_xml_grouped_relationships(content, 1)
        assert groups[0][0]["relationship_type"] == "PART_OF"


# ---------------------------------------------------------------------------
# extract_chunk_relationships_batch_async
# ---------------------------------------------------------------------------

def _wire_batch_llm(extractor, monkeypatch, content):
    """Give the extractor a fake relationship client + canned LLM content."""
    monkeypatch.setattr(
        GraphExtractor, "async_relationship_client",
        property(lambda self: MagicMock()),
    )
    monkeypatch.setattr(
        GraphExtractor, "relationship_model_name",
        property(lambda self: "fake-model"),
    )
    extractor._async_safe_completion = AsyncMock(return_value="raw-response")
    monkeypatch.setattr(
        extractor, "_extract_response_content", lambda response: content
    )


_ITEMS = [
    {
        "key": "chunk-1",
        "chunk_text": "Alice works for Acme.",
        "entities": [
            {"name": "Alice", "type": "Person", "description": ""},
            {"name": "Acme", "type": "Organization", "description": ""},
        ],
    },
    {
        "key": "chunk-2",
        "chunk_text": "Cortex uses Neo4j.",
        "entities": [
            {"name": "Cortex", "type": "System", "description": ""},
            {"name": "Neo4j", "type": "Technology", "description": ""},
        ],
    },
]


class TestBatchExtraction:
    async def test_grouped_response_maps_to_chunks(self, extractor, monkeypatch):
        content = """
        <chunk index="1">
            <relationship><source>Alice</source><target>Acme</target><type>WORKS_FOR</type></relationship>
        </chunk>
        <chunk index="2">
            <relationship><source>Cortex</source><target>Neo4j</target><type>USES</type></relationship>
        </chunk>
        """
        _wire_batch_llm(extractor, monkeypatch, content)
        out = await extractor.extract_chunk_relationships_batch_async(_ITEMS)
        assert [r.source for r in out["chunk-1"]] == ["Alice"]
        assert [r.source for r in out["chunk-2"]] == ["Cortex"]
        # exactly one LLM call for both chunks
        assert extractor._async_safe_completion.await_count == 1
        # system prompt is the byte-identical single-chunk prompt
        from app.services.graph_extractor import RELATIONSHIP_ANALYSIS_SYSTEM_PROMPT
        messages = extractor._async_safe_completion.await_args.kwargs["messages"]
        assert messages[0]["content"] == RELATIONSHIP_ANALYSIS_SYSTEM_PROMPT

    async def test_cross_source_relationships_rejected(self, extractor, monkeypatch):
        # Alice (source 1) → Neo4j (source 2) must not survive validation
        content = """
        <chunk index="1">
            <relationship><source>Alice</source><target>Neo4j</target><type>USES</type></relationship>
            <relationship><source>Alice</source><target>Acme</target><type>WORKS_FOR</type></relationship>
        </chunk>
        <chunk index="2"></chunk>
        """
        _wire_batch_llm(extractor, monkeypatch, content)
        out = await extractor.extract_chunk_relationships_batch_async(_ITEMS)
        assert [r.target for r in out["chunk-1"]] == ["Acme"]

    async def test_flat_response_attributed_by_entity_sets(self, extractor, monkeypatch):
        content = """
        <relationship><source>Cortex</source><target>Neo4j</target><type>USES</type></relationship>
        <relationship><source>Alice</source><target>Acme</target><type>WORKS_FOR</type></relationship>
        """
        _wire_batch_llm(extractor, monkeypatch, content)
        out = await extractor.extract_chunk_relationships_batch_async(_ITEMS)
        assert [r.source for r in out["chunk-1"]] == ["Alice"]
        assert [r.source for r in out["chunk-2"]] == ["Cortex"]

    async def test_zero_parse_redispatches_per_chunk(self, extractor, monkeypatch):
        _wire_batch_llm(extractor, monkeypatch, "no relationships here at all")
        single_calls = []

        async def _single(chunk_text, entities, max_output_tokens=2000):
            single_calls.append(chunk_text)
            return []

        monkeypatch.setattr(
            extractor, "extract_chunk_relationships_async", _single
        )
        out = await extractor.extract_chunk_relationships_batch_async(_ITEMS)
        assert len(single_calls) == 2  # both chunks re-dispatched individually
        assert out == {"chunk-1": [], "chunk-2": []}

    async def test_llm_error_falls_back_per_chunk(self, extractor, monkeypatch):
        monkeypatch.setattr(
            GraphExtractor, "async_relationship_client",
            property(lambda self: MagicMock()),
        )
        monkeypatch.setattr(
            GraphExtractor, "relationship_model_name",
            property(lambda self: "fake-model"),
        )
        extractor._async_safe_completion = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        single_calls = []

        async def _single(chunk_text, entities, max_output_tokens=2000):
            single_calls.append(chunk_text)
            return []

        monkeypatch.setattr(
            extractor, "extract_chunk_relationships_async", _single
        )
        out = await extractor.extract_chunk_relationships_batch_async(_ITEMS)
        assert len(single_calls) == 2
        assert set(out) == {"chunk-1", "chunk-2"}

    async def test_single_item_uses_single_chunk_path(self, extractor, monkeypatch):
        single_calls = []

        async def _single(chunk_text, entities, max_output_tokens=2000):
            single_calls.append(chunk_text)
            return []

        monkeypatch.setattr(
            extractor, "extract_chunk_relationships_async", _single
        )
        out = await extractor.extract_chunk_relationships_batch_async(_ITEMS[:1])
        assert single_calls == ["Alice works for Acme."]
        assert out == {"chunk-1": []}

    async def test_ineligible_items_filtered(self, extractor):
        out = await extractor.extract_chunk_relationships_batch_async([
            {"key": "c", "chunk_text": "text", "entities": [{"name": "only-one"}]},
            {"key": "d", "chunk_text": "  ", "entities": [{"name": "a"}, {"name": "b"}]},
        ])
        assert out == {}
