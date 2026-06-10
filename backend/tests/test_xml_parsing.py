"""Golden/characterization tests for the GraphExtractor XML parsers.

These lock the current parsing behavior of `_extract_xml_relationships`,
`_extract_xml_entities`, and `_extract_xml_grouped_entity_names` before any
parser is extended (e.g. chunk-batched relationship extraction). Any change
in this behavior must be deliberate and show up as a test update here.
"""

from __future__ import annotations

import pytest

from app.services.graph_extractor import GraphExtractor


@pytest.fixture
def extractor():
    # Clients are lazy — constructing the extractor makes no LLM calls.
    return GraphExtractor()


# ---------------------------------------------------------------------------
# _extract_xml_relationships
# ---------------------------------------------------------------------------

class TestExtractXmlRelationships:
    def test_canonical_block(self, extractor):
        content = """
        <relationship>
            <source>Neo4j</source>
            <target>Cortex</target>
            <type>PART_OF</type>
            <description>Neo4j is the graph store of Cortex</description>
            <weight>8</weight>
        </relationship>
        """
        rels = extractor._extract_xml_relationships(content)
        assert rels == [{
            "source": "Neo4j",
            "target": "Cortex",
            "relationship_type": "PART_OF",
            "description": "Neo4j is the graph store of Cortex",
            "weight": 8.0,
            "confidence": 1.0,
        }]

    def test_flexible_element_ordering(self, extractor):
        content = """
        <relationship>
            <weight>3</weight>
            <type>USES</type>
            <target>Postgres</target>
            <description>desc</description>
            <source>App</source>
        </relationship>
        """
        rels = extractor._extract_xml_relationships(content)
        assert len(rels) == 1
        assert rels[0]["source"] == "App"
        assert rels[0]["target"] == "Postgres"
        assert rels[0]["relationship_type"] == "USES"
        assert rels[0]["weight"] == 3.0

    def test_missing_required_fields_skipped(self, extractor):
        content = """
        <relationship><source>A</source><type>USES</type></relationship>
        <relationship><source>A</source><target>B</target><type>USES</type></relationship>
        """
        rels = extractor._extract_xml_relationships(content)
        assert len(rels) == 1
        assert rels[0]["target"] == "B"

    def test_type_normalized_to_uppercase_with_underscores(self, extractor):
        content = (
            "<relationship><source>A</source><target>B</target>"
            "<type>part of</type></relationship>"
        )
        rels = extractor._extract_xml_relationships(content)
        assert rels[0]["relationship_type"] == "PART_OF"

    def test_unknown_type_fuzzy_matches_close_canonical(self, extractor):
        # "DEPEND_ON" is close enough (>= 80) to DEPENDS_ON
        content = (
            "<relationship><source>A</source><target>B</target>"
            "<type>DEPEND_ON</type></relationship>"
        )
        rels = extractor._extract_xml_relationships(content)
        assert rels[0]["relationship_type"] == "DEPENDS_ON"

    def test_unknown_type_far_from_canonical_falls_back_to_related_to(self, extractor):
        content = (
            "<relationship><source>A</source><target>B</target>"
            "<type>ZZZZQQQQ</type></relationship>"
        )
        rels = extractor._extract_xml_relationships(content)
        assert rels[0]["relationship_type"] == "RELATED_TO"

    def test_weight_clamped_to_0_10(self, extractor):
        content = (
            "<relationship><source>A</source><target>B</target><type>USES</type>"
            "<weight>99</weight></relationship>"
            "<relationship><source>C</source><target>D</target><type>USES</type>"
            "<weight>-5</weight></relationship>"
        )
        rels = extractor._extract_xml_relationships(content)
        assert rels[0]["weight"] == 10.0
        assert rels[1]["weight"] == 0.0

    def test_invalid_weight_defaults_to_5(self, extractor):
        content = (
            "<relationship><source>A</source><target>B</target><type>USES</type>"
            "<weight>heavy</weight></relationship>"
        )
        rels = extractor._extract_xml_relationships(content)
        assert rels[0]["weight"] == 5.0

    def test_confidence_clamped_to_0_1_and_defaults_to_1(self, extractor):
        content = (
            "<relationship><source>A</source><target>B</target><type>USES</type>"
            "<confidence>1.7</confidence></relationship>"
            "<relationship><source>C</source><target>D</target><type>USES</type>"
            "</relationship>"
        )
        rels = extractor._extract_xml_relationships(content)
        assert rels[0]["confidence"] == 1.0
        assert rels[1]["confidence"] == 1.0

    def test_duplicate_source_target_type_deduped(self, extractor):
        content = (
            "<relationship><source>A</source><target>B</target><type>USES</type>"
            "</relationship>"
            "<relationship><source>a</source><target>b</target><type>USES</type>"
            "</relationship>"
        )
        rels = extractor._extract_xml_relationships(content)
        assert len(rels) == 1

    def test_empty_content_returns_empty(self, extractor):
        assert extractor._extract_xml_relationships("") == []
        assert extractor._extract_xml_relationships("no xml here") == []

    def test_plaintext_arrow_fallback(self, extractor):
        content = """Here are the relationships:
1. Alice --[WORKS_FOR]--> Acme Corp - Alice is employed there
- **Bob --[USES]--> Cortex** - daily driver
"""
        rels = extractor._extract_xml_relationships(content)
        assert len(rels) == 2
        assert rels[0] == {
            "source": "Alice",
            "target": "Acme Corp",
            "relationship_type": "WORKS_FOR",
            "description": "Alice is employed there",
            "weight": 5.0,
        }
        assert rels[1]["source"] == "Bob"
        assert rels[1]["target"] == "Cortex"
        assert rels[1]["relationship_type"] == "USES"

    def test_arrow_fallback_not_used_when_xml_present(self, extractor):
        content = (
            "<relationship><source>A</source><target>B</target><type>USES</type>"
            "</relationship>\n"
            "C --[USES]--> D - should be ignored"
        )
        rels = extractor._extract_xml_relationships(content)
        assert len(rels) == 1
        assert rels[0]["source"] == "A"


# ---------------------------------------------------------------------------
# _extract_xml_entities
# ---------------------------------------------------------------------------

class TestExtractXmlEntities:
    def test_full_format(self, extractor):
        content = (
            '<entity name="Neo4j"><type>Technology</type>'
            "<description>graph db</description></entity>"
        )
        ents = extractor._extract_xml_entities(content)
        assert ents == [
            {"name": "Neo4j", "type": "Technology", "description": "graph db"}
        ]

    def test_simple_format_without_description(self, extractor):
        content = '<entity name="Alice"><type>Person</type></entity>'
        ents = extractor._extract_xml_entities(content)
        assert ents == [{"name": "Alice", "type": "Person", "description": ""}]

    def test_unknown_type_normalizes_to_concept(self, extractor):
        content = '<entity name="X"><type>Zzzqqq</type></entity>'
        ents = extractor._extract_xml_entities(content)
        assert ents[0]["type"] == "Concept"


# ---------------------------------------------------------------------------
# _extract_xml_grouped_entity_names (the pattern A4 will mirror)
# ---------------------------------------------------------------------------

class TestExtractXmlGroupedEntityNames:
    def test_index_attributed_blocks(self, extractor):
        content = """
        <queries>
            <query index="1"><entity>Alpha</entity><entity>Beta</entity></query>
            <query index="2"><entity>Gamma</entity></query>
        </queries>
        """
        groups = extractor._extract_xml_grouped_entity_names(content, 2)
        assert groups == [["Alpha", "Beta"], ["Gamma"]]

    def test_missing_index_falls_back_to_positional(self, extractor):
        content = (
            "<query><entity>A</entity></query>"
            "<query><entity>B</entity></query>"
        )
        groups = extractor._extract_xml_grouped_entity_names(content, 2)
        assert groups == [["A"], ["B"]]

    def test_out_of_range_index_uses_positional_slot(self, extractor):
        content = '<query index="9"><entity>A</entity></query>'
        groups = extractor._extract_xml_grouped_entity_names(content, 2)
        assert groups == [["A"], []]

    def test_no_blocks_returns_all_empty(self, extractor):
        groups = extractor._extract_xml_grouped_entity_names("plain text", 3)
        assert groups == [[], [], []]

    def test_within_group_dedup_preserves_order(self, extractor):
        content = (
            '<query index="1">'
            "<entity>A</entity><entity>B</entity><entity>A</entity>"
            "</query>"
        )
        groups = extractor._extract_xml_grouped_entity_names(content, 1)
        assert groups == [["A", "B"]]

    def test_more_blocks_than_expected_ignored(self, extractor):
        content = (
            "<query><entity>A</entity></query>"
            "<query><entity>B</entity></query>"
            "<query><entity>C</entity></query>"
        )
        groups = extractor._extract_xml_grouped_entity_names(content, 2)
        assert groups == [["A"], ["B"]]
