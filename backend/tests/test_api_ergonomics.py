"""API ergonomics contract tests.

Covers the additive integration-surface improvements driven by live
agent-harness testing:

- GET /api/documents server-side filtering / sorting / pagination
- Additive response-field aliases (`total`, `id`, `document_title`,
  `/api/ask` echoing the applied `collection_id`)
- The SSE `type` discriminator (flat keys preserved)
- Uniform `collection_id` placement (top-level on /api/search, form field
  on /api/upload)
- `response_format` (structured JSON answers) validation + routing
"""

import io
import json
from unittest.mock import AsyncMock

import pytest

from app.main import sse_frame
from app.models import SearchResponse, SearchResult, UploadResponse, ProcessingStatus
from app.services.document_processor import _parse_structured_answer


_DOCS = [
    {"id": "d1", "filename": "b.pdf", "collection_id": "c1",
     "processing_status": "completed", "file_size": 10, "chunk_count": 2,
     "upload_date": "2026-01-02", "entity_count": 3},
    {"id": "d2", "filename": "a.pdf", "collection_id": "c2",
     "processing_status": "pending", "file_size": 30, "chunk_count": 1,
     "upload_date": "2026-01-03", "entity_count": 1},
    {"id": "d3", "filename": "c.pdf", "collection_id": "c1",
     "processing_status": "completed", "file_size": 20, "chunk_count": 5,
     "upload_date": "2026-01-01", "entity_count": 2},
]


# ---------------------------------------------------------------------------
# GET /api/documents — query params
# ---------------------------------------------------------------------------

class TestDocumentsQueryParams:
    def test_no_params_is_legacy_full_list(self, client, mock_neo4j):
        mock_neo4j.get_all_documents.return_value = list(_DOCS)
        r = client.get("/api/documents")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3
        assert len(body["documents"]) == 3

    def test_collection_filter(self, client, mock_neo4j):
        mock_neo4j.get_all_documents.return_value = list(_DOCS)
        r = client.get("/api/documents", params={"collection_id": "c1"})
        body = r.json()
        assert body["total"] == 2
        assert {d["id"] for d in body["documents"]} == {"d1", "d3"}

    def test_status_filter(self, client, mock_neo4j):
        mock_neo4j.get_all_documents.return_value = list(_DOCS)
        r = client.get("/api/documents", params={"status": "pending"})
        body = r.json()
        assert body["total"] == 1
        assert body["documents"][0]["id"] == "d2"

    def test_sort_ascending_and_descending(self, client, mock_neo4j):
        mock_neo4j.get_all_documents.return_value = list(_DOCS)
        asc = client.get("/api/documents", params={"sort": "filename"}).json()
        assert [d["filename"] for d in asc["documents"]] == ["a.pdf", "b.pdf", "c.pdf"]
        desc = client.get("/api/documents", params={"sort": "-file_size"}).json()
        assert [d["file_size"] for d in desc["documents"]] == [30, 20, 10]

    def test_pagination_total_is_pre_pagination(self, client, mock_neo4j):
        mock_neo4j.get_all_documents.return_value = list(_DOCS)
        r = client.get(
            "/api/documents",
            params={"sort": "filename", "limit": 1, "offset": 1},
        ).json()
        assert r["total"] == 3          # filtered count, not page size
        assert len(r["documents"]) == 1
        assert r["documents"][0]["filename"] == "b.pdf"
        assert r["limit"] == 1 and r["offset"] == 1

    def test_unknown_sort_field_is_400(self, client, mock_neo4j):
        mock_neo4j.get_all_documents.return_value = list(_DOCS)
        r = client.get("/api/documents", params={"sort": "nope"})
        assert r.status_code == 400
        assert "Unsupported sort field" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Additive field aliases
# ---------------------------------------------------------------------------

class TestFieldAliases:
    def test_search_response_total_mirrors_total_results(self):
        resp = SearchResponse(query="q", results=[], total_results=7)
        assert resp.total == 7

    def test_upload_response_id_mirrors_document_id(self):
        resp = UploadResponse(
            document_id="abc", filename="f.txt",
            status=ProcessingStatus.PENDING, message="ok",
        )
        assert resp.id == "abc"

    def test_search_result_document_title_from_metadata_filename(self):
        r = SearchResult(
            document_id="d", chunk_id="c", content="x", score=0.5,
            metadata={"filename": "notes.md", "chunk_index": 0},
        )
        assert r.document_title == "notes.md"

    def test_search_result_document_title_absent_filename(self):
        r = SearchResult(
            document_id="d", chunk_id="c", content="x", score=0.5, metadata={},
        )
        assert r.document_title is None

    def test_search_endpoint_emits_aliases(self, client, mock_processors):
        mock_processors.query.hybrid_search.return_value = [
            {"document_id": "d1", "chunk_id": "ch1", "content": "hello",
             "score": 0.9, "filename": "doc.pdf", "chunk_index": 0},
        ]
        r = client.post("/api/search", json={"query": "hello"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == body["total_results"] == 1
        assert body["results"][0]["document_title"] == "doc.pdf"
        assert body["results"][0]["metadata"]["filename"] == "doc.pdf"

    def test_ask_echoes_applied_collection_id(self, client, mock_processors):
        mock_processors.query.rag_query = AsyncMock(return_value={
            "question": "q", "answer": "a", "sources": [], "graph_context": None,
            "reranked": False, "reasoning_steps": None,
        })
        r = client.post("/api/ask", json={"question": "q", "collection_id": "col-9"})
        assert r.status_code == 200
        assert r.json()["collection_id"] == "col-9"


# ---------------------------------------------------------------------------
# SSE `type` discriminator
# ---------------------------------------------------------------------------

class TestSSEFrameType:
    def _decode(self, frame: str) -> dict:
        assert frame.startswith("data: ") and frame.endswith("\n\n")
        return json.loads(frame[len("data: "):])

    def test_flat_keys_preserved_and_typed(self):
        out = self._decode(sse_frame({"content": "hi"}))
        assert out == {"type": "content", "content": "hi"}

    def test_priority_on_multi_key_frames(self):
        out = self._decode(sse_frame({"done": True, "pending_memory": True}))
        assert out["type"] == "done"
        out = self._decode(sse_frame({"done": True, "fast_mode": True}))
        assert out["type"] == "done"

    def test_existing_type_untouched(self):
        out = self._decode(sse_frame({"type": "custom", "content": "x"}))
        assert out["type"] == "custom"

    def test_unknown_shape_gets_event(self):
        out = self._decode(sse_frame({"mystery": 1}))
        assert out["type"] == "event"

    @pytest.mark.parametrize("key", [
        "error", "done", "memory_update", "sources", "graph_context",
        "retrieval_stats", "retrieval", "sub_questions", "status",
        "thinking", "reasoning", "content",
    ])
    def test_every_known_frame_kind(self, key):
        out = self._decode(sse_frame({key: "v"}))
        assert out["type"] == key


# ---------------------------------------------------------------------------
# Uniform collection_id placement
# ---------------------------------------------------------------------------

class TestUniformCollectionId:
    def test_search_top_level_collection_id(self, client, mock_processors):
        mock_processors.query.hybrid_search.return_value = []
        r = client.post("/api/search", json={"query": "q", "collection_id": "c1"})
        assert r.status_code == 200
        _, kwargs = mock_processors.query.hybrid_search.call_args
        assert kwargs["collection_id"] == "c1"

    def test_search_filters_placement_still_works(self, client, mock_processors):
        mock_processors.query.hybrid_search.return_value = []
        r = client.post(
            "/api/search",
            json={"query": "q", "filters": {"collection_id": "c2"}},
        )
        assert r.status_code == 200
        _, kwargs = mock_processors.query.hybrid_search.call_args
        assert kwargs["collection_id"] == "c2"

    def test_search_disagreeing_placements_400(self, client, mock_processors):
        r = client.post(
            "/api/search",
            json={"query": "q", "collection_id": "c1",
                  "filters": {"collection_id": "c2"}},
        )
        assert r.status_code == 400

    def test_upload_accepts_collection_id_form_field(self, client, mock_processors):
        r = client.post(
            "/api/upload",
            files={"file": ("note.txt", io.BytesIO(b"hello world"), "text/plain")},
            data={"collection_id": "form-col"},
        )
        assert r.status_code == 200, r.text
        args, kwargs = mock_processors.doc.store_file_only.call_args
        assert args[3] == "form-col"
        body = r.json()
        assert body["id"] == body["document_id"]

    def test_upload_query_param_wins_over_form(self, client, mock_processors):
        r = client.post(
            "/api/upload",
            params={"collection_id": "query-col"},
            files={"file": ("note2.txt", io.BytesIO(b"hello again"), "text/plain")},
            data={"collection_id": "form-col"},
        )
        assert r.status_code == 200, r.text
        args, _ = mock_processors.doc.store_file_only.call_args
        assert args[3] == "query-col"


# ---------------------------------------------------------------------------
# response_format (structured answers)
# ---------------------------------------------------------------------------

_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


class TestResponseFormat:
    def test_valid_schema_passes_through_and_returns_structured(
        self, client, mock_processors
    ):
        mock_processors.query.rag_query = AsyncMock(return_value={
            "question": "q", "answer": '{"answer": "42"}', "sources": [],
            "graph_context": None, "reranked": False, "reasoning_steps": None,
            "structured": {"answer": "42"},
        })
        r = client.post(
            "/api/ask", json={"question": "q", "response_format": _SCHEMA},
        )
        assert r.status_code == 200
        assert r.json()["structured"] == {"answer": "42"}
        _, kwargs = mock_processors.query.rag_query.call_args
        assert kwargs["response_format"] == _SCHEMA

    def test_non_object_root_is_400(self, client, mock_processors):
        r = client.post(
            "/api/ask",
            json={"question": "q", "response_format": {"type": "array"}},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invalid_response_format"

    def test_with_use_agentic_is_400(self, client, mock_processors):
        r = client.post(
            "/api/ask",
            json={"question": "q", "response_format": _SCHEMA,
                  "use_agentic": True},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "response_format_not_supported"

    @pytest.mark.parametrize("endpoint", [
        "/api/ask/stream", "/api/ask/stream/thinking",
    ])
    def test_streaming_endpoints_reject_response_format(
        self, client, mock_processors, endpoint
    ):
        r = client.post(
            endpoint, json={"question": "q", "response_format": _SCHEMA},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "response_format_requires_non_streaming"


class TestParseStructuredAnswer:
    def test_plain_json(self):
        assert _parse_structured_answer('{"a": 1}') == {"a": 1}

    def test_fenced_json(self):
        assert _parse_structured_answer('```json\n{"a": 1}\n```') == {"a": 1}

    def test_prose_wrapped_json(self):
        text = 'Here is the result:\n{"a": 1}\nHope that helps!'
        assert _parse_structured_answer(text) == {"a": 1}

    def test_non_object_json_is_none(self):
        assert _parse_structured_answer('[1, 2, 3]') is None

    def test_garbage_is_none(self):
        assert _parse_structured_answer('no json here') is None


# ---------------------------------------------------------------------------
# Unified ask depth dial
# ---------------------------------------------------------------------------

class TestDepthParam:
    def test_depth_deep_on_non_streaming_ask_400(self, client, mock_processors, _isolate_env):
        _isolate_env.enable_agent_research = True
        r = client.post("/api/ask", json={"question": "q", "depth": "deep"})
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "agentic_requires_streaming"

    def test_depth_conflict_is_400(self, client, mock_processors):
        r = client.post(
            "/api/ask",
            json={"question": "q", "depth": "fast", "use_agentic": True},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "depth_conflict"

    def test_depth_conflict_on_stream_is_400(self, client, mock_processors):
        r = client.post(
            "/api/ask/stream",
            json={"question": "q", "depth": "standard", "use_fast_search": True},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "depth_conflict"

    def test_invalid_depth_is_422(self, client, mock_processors):
        r = client.post("/api/ask", json={"question": "q", "depth": "turbo"})
        assert r.status_code == 422

    def test_depth_standard_normalizes_legacy_flags(self, client, mock_processors):
        mock_processors.query.rag_query = AsyncMock(return_value={
            "question": "q", "answer": "a", "sources": [], "graph_context": None,
            "reranked": False, "reasoning_steps": None,
        })
        r = client.post("/api/ask", json={"question": "q", "depth": "standard"})
        assert r.status_code == 200
        _, kwargs = mock_processors.query.rag_query.call_args
        assert kwargs["use_agentic"] is False

    def test_matching_redundant_flags_are_fine(self, client, mock_processors, _isolate_env):
        _isolate_env.enable_agent_research = True
        # depth=deep + use_agentic=true agree — passes normalization, then the
        # non-streaming agentic guard fires as it always has.
        r = client.post(
            "/api/ask",
            json={"question": "q", "depth": "deep", "use_agentic": True},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "agentic_requires_streaming"
