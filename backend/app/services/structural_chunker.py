"""Structure-anchored, content-defined chunking (chunker v2, CHUNKING_STRATEGY=structural).

Why: the legacy DocumentSplitter cuts fixed sentence windows counted from the
top of the document, so prepending a section (the usual changelog edit) shifts
every boundary and no chunk survives an edit. Chunk-level delta reprocessing
can only reuse work when unchanged text yields byte-identical chunks.

Rules (deterministic — sha1, never Python ``hash()``):
- Blocks are blank-line-separated paragraphs; a fenced code block (``` / ~~~)
  is one atomic block even when it contains blank lines.
- A markdown heading line (``#``..``######``) forces a chunk boundary before it
  once the chunk already holds ``min_words``; smaller sections are packed
  together. A chunk never ends on a heading.
- Inside a section a boundary falls after a block once the chunk holds at least
  ``min_words`` words and ``sha1(block) % divisor == 0`` (content-defined, so
  boundaries re-synchronise right after an edit), or once it reaches
  ``max_words``.
- A single block longer than ``max_words`` is split at sentence boundaries with
  the same content-defined rule, then at word level as a last resort.

Tradeoff: no overlap between chunks. Overlap would copy neighbour text into each
chunk, so an edit would also invalidate the adjacent chunks; stability wins here.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List

_HEADING = re.compile(r"^#{1,6}\s")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _h(text: str) -> int:
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest(), 16)


def _words(text: str) -> int:
    return len(text.split())


def _blocks(text: str) -> List[str]:
    """Blank-line paragraphs; fenced code blocks kept whole; headings split off."""
    blocks: List[str] = []
    buf: List[str] = []
    fence: str | None = None

    def flush():
        if buf:
            block = "\n".join(buf).strip("\n")
            if block.strip():
                blocks.append(block)
            buf.clear()

    for line in text.split("\n"):
        m = _FENCE.match(line)
        if fence is not None:
            buf.append(line)
            if m and m.group(1) == fence:
                fence = None
                flush()
            continue
        if m:
            flush()
            fence = m.group(1)
            buf.append(line)
            continue
        if not line.strip():
            flush()
            continue
        if _HEADING.match(line):
            # A heading always starts its own block so it can anchor a boundary.
            flush()
        buf.append(line)
    flush()
    return blocks


def _pack(pieces: List[str], sep: str, min_words: int, max_words: int,
          divisor: int) -> List[str]:
    out: List[str] = []
    buf: List[str] = []
    words = 0
    for p in pieces:
        buf.append(p)
        words += _words(p)
        if words >= max_words or (words >= min_words and _h(p) % divisor == 0):
            out.append(sep.join(buf))
            buf, words = [], 0
    if buf:
        out.append(sep.join(buf))
    return out


def _split_oversize(block: str, min_words: int, max_words: int, divisor: int) -> List[str]:
    sentences = [s for s in _SENTENCE_END.split(block) if s.strip()]
    if len(sentences) > 1:
        parts = _pack(sentences, " ", min_words, max_words, divisor)
    else:
        parts = [block]
    out: List[str] = []
    for part in parts:
        if _words(part) <= max_words:
            out.append(part)
            continue
        # Last resort: word-level, still content-defined.
        out.extend(_pack(part.split(), " ", min_words, max_words, divisor))
    return out


def structural_split(text: str, min_words: int = 80, max_words: int = 400,
                     divisor: int = 4) -> List[str]:
    """Split ``text`` into structure-anchored, content-defined chunks."""
    min_words = max(1, int(min_words))
    max_words = max(min_words, int(max_words))
    divisor = max(1, int(divisor))

    chunks: List[str] = []
    buf: List[str] = []
    words = 0

    def flush():
        nonlocal buf, words
        if buf:
            chunks.append("\n\n".join(buf))
        buf, words = [], 0

    def take_trailing_headings() -> List[str]:
        nonlocal words
        carry: List[str] = []
        while buf and _HEADING.match(buf[-1]):
            carry.insert(0, buf.pop())
        words -= sum(_words(c) for c in carry)
        return carry

    for block in _blocks(text):
        is_heading = bool(_HEADING.match(block))
        # A heading starts a new chunk only once the buffer already holds
        # min_words; smaller sections are packed together so heading-dense
        # files don't fragment into tiny chunks.
        if is_heading and buf and words >= min_words:
            flush()
        n = _words(block)
        if n > max_words:
            carry = take_trailing_headings()
            flush()
            pieces = _split_oversize(block, min_words, max_words, divisor)
            if carry:
                pieces[0] = "\n\n".join(carry + [pieces[0]])
            chunks.extend(pieces)
            continue
        if buf and words + n > max_words:
            # Trailing headings move to the next chunk: never end on a heading.
            carry = take_trailing_headings()
            flush()
            buf, words = carry, sum(_words(c) for c in carry)
        buf.append(block)
        words += n
        if is_heading:
            continue  # never end a chunk on a heading
        if words >= max_words or (words >= min_words and _h(block) % divisor == 0):
            flush()
    flush()
    return chunks


def split_documents(documents: List[Any], min_words: int, max_words: int,
                    divisor: int) -> List[Any]:
    """Haystack-compatible replacement for ``DocumentSplitter.run(...)['documents']``.

    Output meta mirrors the legacy splitter's contract: the source meta plus
    ``source_id``, ``page_number``, ``split_id``, ``split_idx_start`` and
    ``_split_overlap`` (always empty — no overlap).
    """
    from haystack import Document as HaystackDocument

    out: List[Any] = []
    for doc in documents:
        content = doc.content or ""
        search_from = 0
        for split_id, chunk in enumerate(structural_split(content, min_words, max_words, divisor)):
            # Locate the chunk start for page/offset meta (best effort: the first
            # block is a verbatim substring of the source text).
            first = chunk.split("\n", 1)[0]
            pos = content.find(first, search_from)
            if pos < 0:
                pos = search_from
            else:
                search_from = pos
            meta: Dict[str, Any] = dict(doc.meta or {})
            meta.update({
                "source_id": doc.id,
                "page_number": content.count("\f", 0, pos) + 1,
                "split_id": split_id,
                "split_idx_start": pos,
                "_split_overlap": [],
            })
            out.append(HaystackDocument(content=chunk, meta=meta))
    return out


class StructuralSplitter:
    """Drop-in for the ``self.splitter`` slot: exposes ``run(documents=...)``."""

    def __init__(self, min_words: int = 80, max_words: int = 400, divisor: int = 4):
        self.min_words = min_words
        self.max_words = max_words
        self.divisor = divisor

    def run(self, documents: List[Any]) -> Dict[str, List[Any]]:
        return {"documents": split_documents(
            documents, self.min_words, self.max_words, self.divisor)}
