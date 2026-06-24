"""Unit tests for library export/import NDJSON serialization primitives.

The endpoint-level cap rejection is covered by test_max_files/entities; this
exercises the streaming round-trip core that those tests mock past:
_serialize_value (Neo4j type coercion), _write_ndjson/_iter_ndjson (write->read
round-trip), _iter_ndjson_batches (batching), and _count_ndjson (parse-free count,
missing-entry safety).
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

import pytest
from neo4j.time import DateTime as Neo4jDateTime

from app.services.library_transfer_service import (
    _count_ndjson,
    _iter_ndjson,
    _iter_ndjson_batches,
    _serialize_value,
    _write_ndjson,
)


# --- serialization -----------------------------------------------------------

def test_serialize_value_coerces_datetimes():
    assert _serialize_value(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"
    n = Neo4jDateTime(2026, 1, 2, 3, 4, 5, 0)
    assert _serialize_value(n) == n.isoformat()


def test_serialize_value_recurses_into_collections():
    out = _serialize_value({"a": [datetime(2026, 1, 1)], "b": ("x", 1)})
    assert out == {"a": ["2026-01-01T00:00:00"], "b": ["x", 1]}


def test_serialize_value_passthrough_scalars():
    assert _serialize_value(42) == 42
    assert _serialize_value("s") == "s"
    assert _serialize_value(None) is None


# --- NDJSON round-trip -------------------------------------------------------

def _zip_with(records):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        n = _write_ndjson(zf, "data.ndjson", records)
    buf.seek(0)
    return zipfile.ZipFile(buf, "r"), n


def test_write_then_iter_round_trip_preserves_records():
    records = [{"id": i, "ts": datetime(2026, 1, 1)} for i in range(3)]
    zf, written = _zip_with(records)
    assert written == 3
    out = list(_iter_ndjson(zf, "data.ndjson"))
    assert out == [{"id": i, "ts": "2026-01-01T00:00:00"} for i in range(3)]


def test_iter_ndjson_missing_entry_yields_nothing():
    zf, _ = _zip_with([{"id": 1}])
    assert list(_iter_ndjson(zf, "nope.ndjson")) == []


def test_count_ndjson_counts_without_parsing():
    zf, _ = _zip_with([{"id": i} for i in range(5)])
    assert _count_ndjson(zf, "data.ndjson") == 5
    assert _count_ndjson(zf, "absent.ndjson") == 0


def test_iter_ndjson_batches_chunks_records():
    zf, _ = _zip_with([{"id": i} for i in range(7)])
    batches = list(_iter_ndjson_batches(zf, "data.ndjson", batch_size=3))
    assert [len(b) for b in batches] == [3, 3, 1]
    assert sum(len(b) for b in batches) == 7
