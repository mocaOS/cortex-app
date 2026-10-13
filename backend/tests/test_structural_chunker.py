"""Structural (content-defined) chunker v2: rules, edit stability, fingerprint safety."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

from haystack import Document

from app.services import document_processor as dp
from app.services.structural_chunker import (
    StructuralSplitter,
    split_documents,
    structural_split,
)


def _para(seed: int, words: int = 40) -> str:
    return " ".join(f"w{seed}x{i}" for i in range(words)) + "."


def _changelog(sections: int = 40) -> str:
    parts = ["# project"]
    for v in range(sections, 0, -1):
        parts.append(f"## 1.{v}.0")
        parts.append("### Patch Changes")
        for b in range(4):
            parts.append(f"- Fix item {v}-{b}: " + _para(v * 10 + b, 25))
    return "\n\n".join(parts)


def _reextract_fraction(old: list[str], new: list[str]) -> float:
    s = set(old)
    total = sum(len(c) for c in new)
    return sum(len(c) for c in new if c not in s) / total


# --- rules --------------------------------------------------------------------

def test_heading_forces_boundary_once_min_reached():
    text = "intro " + _para(1, 30) + "\n\n## Section A\n\n" + _para(2, 5)
    chunks = structural_split(text, min_words=20, max_words=2000, divisor=10**9)
    assert len(chunks) == 2
    assert chunks[1].startswith("## Section A")


def test_small_sections_are_packed_below_min():
    text = "\n\n".join(f"## S{i}\n\n" + _para(i, 5) for i in range(6))
    chunks = structural_split(text, min_words=1000, max_words=2000)
    assert len(chunks) == 1


def test_max_words_forces_boundary():
    text = "\n\n".join(_para(i, 30) for i in range(20))
    chunks = structural_split(text, min_words=50, max_words=100, divisor=10**9)
    assert len(chunks) > 1
    assert all(len(c.split()) <= 100 for c in chunks)


def test_code_fence_is_atomic():
    fence = "```python\nx = 1\n\n\ny = 2\n```"
    text = _para(1, 5) + "\n\n" + fence + "\n\n" + _para(2, 5)
    chunks = structural_split(text, min_words=1, max_words=500, divisor=1)
    assert any(fence in c for c in chunks)


def test_oversize_block_split_sentences_then_words():
    sentences = " ".join(f"Sentence number {i} has some words here." for i in range(200))
    for c in structural_split(sentences, min_words=20, max_words=60):
        assert len(c.split()) <= 60
    one_word_run = " ".join(f"tok{i}" for i in range(500))
    chunks = structural_split(one_word_run, min_words=20, max_words=60)
    assert all(len(c.split()) <= 60 for c in chunks)
    assert " ".join(chunks).split() == one_word_run.split()  # no content loss


def test_deterministic_and_lossless():
    text = _changelog(10)
    a = structural_split(text)
    assert a == structural_split(text)
    assert " ".join(a).split() == text.split()


# --- edit stability -----------------------------------------------------------

def test_small_edits_keep_90_percent_of_chunks():
    text = _changelog(40)
    old = structural_split(text)
    lines = text.split("\n")

    prepend = text.replace("# project\n\n",
                           "# project\n\n## 1.41.0\n\n### Patch Changes\n\n- Fix a crash. "
                           "\n\n- Add a retry option.\n\n", 1)
    append = text + "\n\n- Initial release of the legacy adapter.\n\n- Document the build flags."
    mid = list(lines)
    j = len(mid) // 2
    while not mid[j].strip():
        j += 1
    mid[j] += " (corrected)"

    for new_text in (prepend, append, "\n".join(mid)):
        assert _reextract_fraction(old, structural_split(new_text)) <= 0.10


# --- haystack integration -----------------------------------------------------

def test_split_documents_meta_contract():
    doc = Document(content="## A\n\n" + _para(1, 5) + "\f\n\n## B\n\n" + _para(2, 5),
                   meta={"file_path": "x.md"})
    out = split_documents([doc], 3, 400, 10**9)
    assert [d.meta["split_id"] for d in out] == [0, 1]
    for d in out:
        assert d.meta["file_path"] == "x.md"
        assert d.meta["source_id"] == doc.id
        assert d.meta["_split_overlap"] == []
    assert out[0].meta["page_number"] == 1
    assert out[1].meta["page_number"] == 2
    assert StructuralSplitter(3, 400, 10**9).run(documents=[doc])["documents"][0].content == out[0].content


# --- fingerprint safety -------------------------------------------------------

def _settings(**over):
    base = dict(
        chunk_by="sentence", chunk_size=500, chunk_overlap=50, sentences_per_chunk=5,
        enable_graph_extraction=True, enable_semantic_entity_resolution=False,
        embedding_model="text-embedding-3-small", embedding_dimension=1536,
        embedding_max_input_tokens=8000, extraction_max_context=16000,
        enable_anydoc=True, chunking_strategy="legacy",
        structural_chunk_min_words=80, structural_chunk_max_words=400,
        structural_chunk_boundary_divisor=4,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _proc(settings):
    p = object.__new__(dp.DocumentProcessor)
    p.settings = settings
    p.graph_extractor = SimpleNamespace(extraction_model_name="m1", relationship_model_name="m2")
    return p


def test_legacy_config_hash_is_byte_identical_to_pre_v2():
    s = _settings()
    src = "|".join([
        "m1", "m2", s.chunk_by, str(s.chunk_size), str(s.chunk_overlap),
        str(s.sentences_per_chunk), str(s.enable_graph_extraction),
        str(s.enable_semantic_entity_resolution), s.embedding_model,
        str(s.embedding_dimension), str(s.embedding_max_input_tokens),
        str(s.extraction_max_context), "extraction-prompts:compact-v1",
        f"converter:anydoc-{dp.anydoc_version()}", "chunk-namespace:v2",
    ])
    assert _proc(s)._reprocess_config_hash() == hashlib.sha256(src.encode()).hexdigest()[:16]


def test_structural_strategy_and_params_change_hash():
    legacy = _proc(_settings())._reprocess_config_hash()
    structural = _proc(_settings(chunking_strategy="structural"))._reprocess_config_hash()
    tuned = _proc(_settings(chunking_strategy="structural",
                            structural_chunk_max_words=500))._reprocess_config_hash()
    assert len({legacy, structural, tuned}) == 3


def test_no_heading_only_chunks():
    text = "## 1.2.0\n\n### Fixes\n\n- " + _para(1, 10) + "\n\n## 1.1.0\n\n### Fixes\n\n" + _para(2, 600)
    chunks = structural_split(text, min_words=20, max_words=100)
    assert all(any(not line.startswith("#") for line in c.split("\n") if line.strip())
               for c in chunks)
    assert chunks[0].startswith("## 1.2.0\n\n### Fixes")
