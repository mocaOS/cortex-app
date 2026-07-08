"""Compact ENT|/REL| line-format parsers with XML fallback.

The XML wire format spent 32% (entities) / 44% (relationships) of extraction
output tokens on scaffolding; extraction latency is decode-bound, so the
compact format cuts wall time and timeout-splits proportionally (see
bench/STEP1_RESEARCH.md, 2026-07-08 addendum). Content parity with the XML
parsers is the contract these tests pin down.
"""

import pytest

from app.services.graph_extractor import GraphExtractor


@pytest.fixture()
def extractor():
    # Parsing needs no clients; bypass __init__ side effects.
    return GraphExtractor.__new__(GraphExtractor)


ENTITY_COMPACT = (
    "Here are the entities:\n"
    "ENT|Psilocybe cubensis|Concept|A mushroom species | widely used in rituals.\n"
    "- ENT|Timothy Leary|Person|Psychologist and author.\n"
    "**ENT|Timothy Leary|Person|duplicate should be skipped**\n"
    "ent|lowercase prefix|Concept|still parsed\n"
    "ENT|OnlyNameAndType|Technology\n"
    "ENT||Concept|missing name skipped\n"
    "random prose line\n"
)

ENTITY_XML = (
    '<entity name="OpenAI"><type>Organization</type>'
    "<description>AI research company.</description></entity>"
)


class TestCompactEntities:
    def test_happy_path_with_noise_and_dedup(self, extractor):
        out = extractor._parse_compact_entities(ENTITY_COMPACT)
        assert [e["name"] for e in out] == [
            "Psilocybe cubensis",
            "Timothy Leary",
            "lowercase prefix",
            "OnlyNameAndType",
        ]
        # pipes inside the trailing description survive
        assert out[0]["description"] == "A mushroom species | widely used in rituals."
        assert out[3]["description"] == ""

    def test_pure_xml_yields_nothing(self, extractor):
        assert extractor._parse_compact_entities(ENTITY_XML) == []

    def test_unified_entry_falls_back_to_xml(self, extractor):
        out = extractor._parse_entities_output(ENTITY_XML)
        assert len(out) == 1 and out[0]["name"] == "OpenAI"

    def test_unified_entry_prefers_compact(self, extractor):
        mixed = "ENT|A|Concept|compact wins\n" + ENTITY_XML
        out = extractor._parse_entities_output(mixed)
        assert [e["name"] for e in out] == ["A"]


REL_COMPACT = (
    "REL|Timothy Leary|Harvard University|WORKS_FOR|8|0.9|Leary taught at Harvard.\n"
    "REL|A|B|USES\n"
    "REL|A|D|USES|notanumber|desc with | pipe\n"
    "REL|E|F|USES|7|weight only then description\n"
    "REL|C|C|USES|5\n"
    "REL|X|Y|works for|7\n"
    "REL|X|Y|WORKS_FOR|7|1.0|duplicate key skipped\n"
)

REL_XML = (
    "<relationship><source>OpenAI</source><target>GPT-4</target>"
    "<type>CREATED_BY</type><description>d</description>"
    "<weight>9</weight></relationship>"
)


class TestCompactRelationships:
    def test_full_and_minimal_records(self, extractor):
        rels = extractor._parse_compact_relationships(REL_COMPACT)
        by_pair = {(r["source"], r["target"]): r for r in rels}
        assert len(rels) == 5  # self-loop dropped, duplicate dropped

        full = by_pair[("Timothy Leary", "Harvard University")]
        assert full["weight"] == 8.0 and full["confidence"] == 0.9
        assert full["description"] == "Leary taught at Harvard."

        minimal = by_pair[("A", "B")]
        assert minimal["weight"] == 5.0 and minimal["confidence"] == 1.0

        # adaptive fields: non-numeric right after type = description start
        nonnum = by_pair[("A", "D")]
        assert nonnum["weight"] == 5.0
        assert nonnum["description"] == "notanumber|desc with | pipe"

        # weight consumed, then description
        wonly = by_pair[("E", "F")]
        assert wonly["weight"] == 7.0 and wonly["confidence"] == 1.0
        assert wonly["description"] == "weight only then description"

    def test_fuzzy_type_normalization(self, extractor):
        rels = extractor._parse_compact_relationships("REL|X|Y|works for|7\n")
        assert rels[0]["relationship_type"] == "WORKS_FOR"

    def test_unified_entry_falls_back_to_xml(self, extractor):
        out = extractor._parse_relationships_output(REL_XML)
        assert len(out) == 1 and out[0]["relationship_type"] == "CREATED_BY"

    def test_grouped_chunk_blocks_parse_compact_lines(self, extractor):
        content = (
            '<chunk index="1">\nREL|A|B|USES|7|0.9|via source 1\n</chunk>\n'
            '<chunk index="2"></chunk>\n'
        )
        parsed = extractor._extract_xml_grouped_relationships_with_coverage(content, 2)
        assert parsed is not None
        groups, covered = parsed
        assert len(groups[0]) == 1 and groups[0][0]["source"] == "A"
        assert groups[1] == []
        assert covered == {0, 1}


class TestNormalizeRelationType:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("USES", "USES"),
            ("works for", "WORKS_FOR"),
            ("depends-on", "RELATED_TO"),  # hyphen breaks exact; fuzzy decides
            ("", "RELATED_TO"),
            ("COMPLETELY_UNRELATED_XYZ", "RELATED_TO"),
        ],
    )
    def test_normalization(self, extractor, raw, expected):
        out = extractor._normalize_relation_type(raw)
        if raw == "depends-on":
            assert out in ("DEPENDS_ON", "RELATED_TO")  # fuzzy threshold call
        else:
            assert out == expected
