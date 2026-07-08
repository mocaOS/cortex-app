"""Guards that keep degenerate chunk content away from the embeddings API.

Regression tests for the 2026-07-08 Pharmacotheon ingestion failure: a book
index page (dense names/numbers, ~2.4 chars/token) slipped past the char-based
8192-token cap with ~9.5k real tokens; the upstream 400 came back from the
Venice gateway wrapped in an HTTP 200 envelope with data=null, the instrumented
OpenAI client raised TypeError("object of type 'NoneType' has no len()"), and
the whole document failed.
"""

import pytest
from haystack import Document as HaystackDocument

from app.services.document_processor import (
    _drop_empty_chunks,
    _enforce_embed_token_cap,
    _token_len,
    _truncate_for_embedding,
)

MAX_TOKENS = 1024

# Index-page-style content: dense punctuation/numbers tokenize far below the
# 2.8 chars/token the char heuristic assumes.
INDEX_LINE = "Bigwood, J.-19,104,128,174,182,192,227,229; hGH (Eprox-25) 417; "


def _doc(content: str) -> HaystackDocument:
    return HaystackDocument(content=content, meta={"source": "test"})


def test_token_len_available():
    # The backend image pins tiktoken; the token-accurate path must be active.
    assert _token_len("hello world") is not None


def test_drop_empty_chunks_removes_empty_and_whitespace():
    docs = [_doc("real content"), _doc(""), _doc("   \n\t "), _doc("more")]
    kept = _drop_empty_chunks(docs)
    assert [d.content for d in kept] == ["real content", "more"]


def test_drop_empty_chunks_noop_when_clean():
    docs = [_doc("a"), _doc("b")]
    assert _drop_empty_chunks(docs) == docs


def test_enforce_cap_catches_token_dense_content():
    # Long enough that real tokens exceed MAX_TOKENS even though chars stay
    # under the char heuristic budget (chars/token here is ~2.4 < 2.8).
    dense = INDEX_LINE * 60
    tokens = _token_len(dense)
    assert tokens is not None and tokens > MAX_TOKENS  # premise of the bug
    assert len(dense) < int(MAX_TOKENS * 2.8) * (tokens // MAX_TOKENS + 1)

    result = _enforce_embed_token_cap([_doc(dense)], MAX_TOKENS)
    assert len(result) > 1  # was split
    for piece in result:
        assert _token_len(piece.content) <= MAX_TOKENS
        assert piece.content.strip()
        assert piece.meta["source"] == "test"
    # zero content loss (whitespace at split boundaries aside)
    assert sum(len(p.content) for p in result) >= len(dense) - len(result) * 2


def test_enforce_cap_keeps_small_chunks_untouched():
    docs = [_doc("short prose chunk"), _doc(INDEX_LINE)]
    assert _enforce_embed_token_cap(docs, MAX_TOKENS) == docs


def test_truncate_for_embedding_token_accurate():
    dense = INDEX_LINE * 60
    doc = _doc(dense)
    _truncate_for_embedding([doc], MAX_TOKENS)
    assert _token_len(doc.content) <= MAX_TOKENS


def test_truncate_for_embedding_handles_none_content():
    doc = HaystackDocument(content="x")
    doc.content = None
    _truncate_for_embedding([doc], MAX_TOKENS)  # must not raise
    assert doc.content is None
