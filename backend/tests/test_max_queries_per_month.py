"""Tests for MAX_QUERIES_PER_MONTH enforcement (unit-denominated quota).

MAX_QUERIES_PER_MONTH is denominated in internal LLM completions: every
successful chat-completion call (Q&A loop iterations, extraction, vision, ...)
consumes one unit, counted by `usage_meter` and persisted on LLMUsageDay
nodes. Sentinel `0` means unlimited.

The cap MUST be enforced on:
- the chat-style query endpoints (POST /api/search, /api/ask, /api/ask/stream,
  /api/ask/stream/thinking) via `enforce_query_quota`, and
- the document/graph processing entry points (upload, custom-input, reprocess,
  process-pending, web-import, git sync, relationship/community steps) via
  `enforce_processing_quota` — processing consumes LLM completions too, so it
  draws from the same pool. In-flight work always finishes; only NEW work is
  blocked.

Read-only and admin endpoints must remain open even when the quota is blown.
"""

from __future__ import annotations

import pytest


CHAT_ENDPOINTS = [
    "/api/search",
    "/api/ask",
    "/api/ask/stream",
    "/api/ask/stream/thinking",
]


def _chat_payload() -> dict:
    """Body shape that satisfies both SearchRequest and RAGRequest validation."""
    return {"query": "what is in the knowledge base?", "question": "what is in the knowledge base?"}


# ---------------------------------------------------------------------------
# Sentinel 0 = unlimited (default behaviour when env var unset)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_chat_unlimited_when_max_queries_zero(client, mock_neo4j, path):
    """With cap=0 (default), even an absurd existing count must not 429."""
    mock_neo4j.set_query_count(50_000)

    response = client.post(path, json=_chat_payload())

    assert response.status_code != 429, response.text
    mock_neo4j.get_llm_completion_count_this_month.assert_not_called()


# ---------------------------------------------------------------------------
# Below the cap → request reaches the handler (no 429)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_chat_allowed_just_below_cap(
    client, mock_neo4j, override_max_queries_per_month, path,
):
    override_max_queries_per_month(10)
    mock_neo4j.set_query_count(9)

    response = client.post(path, json=_chat_payload())

    # We don't assert the exact success status here — the underlying query
    # processor is a MagicMock that may not satisfy the response_model. What
    # matters for *this* test is that the quota dependency did NOT reject.
    assert response.status_code != 429, response.text
    mock_neo4j.get_llm_completion_count_this_month.assert_called()


# ---------------------------------------------------------------------------
# At and over the cap → 429 with Retry-After header
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_chat_rejected_at_cap(
    client, mock_neo4j, mock_processors, override_max_queries_per_month, path,
):
    override_max_queries_per_month(10)
    mock_neo4j.set_query_count(10)

    response = client.post(path, json=_chat_payload())

    assert response.status_code == 429, response.text
    detail = response.json()["detail"]
    assert "Monthly usage limit reached" in detail
    assert "10" in detail

    retry_after = response.headers.get("Retry-After")
    assert retry_after is not None
    assert int(retry_after) > 0

    # Quota check must short-circuit before the handler touches the processor.
    mock_processors.query.hybrid_search.assert_not_called()


@pytest.mark.parametrize("path", CHAT_ENDPOINTS)
def test_chat_rejected_over_cap_defensive(
    client, mock_neo4j, mock_processors, override_max_queries_per_month, path,
):
    override_max_queries_per_month(10)
    mock_neo4j.set_query_count(11)

    response = client.post(path, json=_chat_payload())

    assert response.status_code == 429, response.text
    assert "Monthly usage limit reached" in response.json()["detail"]
    mock_processors.query.hybrid_search.assert_not_called()


# ---------------------------------------------------------------------------
# Streaming endpoints: 429 must be a clean JSON error, not a partial SSE stream
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/ask/stream", "/api/ask/stream/thinking"])
def test_streaming_quota_returns_plain_json_429(
    client, mock_neo4j, override_max_queries_per_month, path,
):
    """Quota dependency runs before StreamingResponse starts — clean 429."""
    override_max_queries_per_month(1)
    mock_neo4j.set_query_count(1)

    response = client.post(path, json=_chat_payload())

    assert response.status_code == 429
    # Should be JSON, not text/event-stream
    content_type = response.headers.get("content-type", "")
    assert "json" in content_type.lower(), f"unexpected content-type: {content_type}"


# ---------------------------------------------------------------------------
# Processing entry points: gated once the unit quota is spent
# ---------------------------------------------------------------------------

def test_upload_rejected_when_quota_exceeded(
    client, mock_neo4j, override_max_queries_per_month,
):
    """Uploads consume LLM completions during processing — new uploads are
    blocked once the monthly unit budget is spent (429 + Retry-After)."""
    override_max_queries_per_month(1)
    mock_neo4j.set_query_count(10_000)

    response = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
    )

    assert response.status_code == 429, response.text
    assert "Monthly usage limit reached" in response.json()["detail"]
    assert int(response.headers.get("Retry-After", "0")) > 0


def test_upload_allowed_below_quota(
    client, mock_neo4j, override_max_queries_per_month,
):
    override_max_queries_per_month(100)
    mock_neo4j.set_query_count(50)

    response = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
    )

    assert response.status_code != 429, response.text


@pytest.mark.parametrize("path", [
    "/api/documents/process-pending",
    "/api/graph/relationships/analyze",
    "/api/graph/communities/detect",
])
def test_processing_endpoints_rejected_when_quota_exceeded(
    client, mock_neo4j, override_max_queries_per_month, path,
):
    override_max_queries_per_month(1)
    mock_neo4j.set_query_count(1)

    response = client.post(path)

    assert response.status_code == 429, response.text
    assert "Monthly usage limit reached" in response.json()["detail"]


def test_custom_input_rejected_when_quota_exceeded(
    client, mock_neo4j, override_max_queries_per_month,
):
    override_max_queries_per_month(1)
    mock_neo4j.set_query_count(1)

    response = client.post(
        "/api/custom-input",
        json={"input_type": "text", "content": "some note", "title": "note"},
    )

    assert response.status_code == 429, response.text


# ---------------------------------------------------------------------------
# Boundary: read-only/admin endpoints must remain open when the quota is blown
# ---------------------------------------------------------------------------

def test_documents_listing_works_when_quota_exceeded(
    client, mock_neo4j, override_max_queries_per_month,
):
    """Read-only document management bypasses the quota."""
    override_max_queries_per_month(1)
    mock_neo4j.set_query_count(10_000)
    mock_neo4j.get_documents.return_value = []

    response = client.get("/api/documents")

    assert response.status_code != 429, response.text
    mock_neo4j.get_llm_completion_count_this_month.assert_not_called()


def test_admin_config_works_when_quota_exceeded(
    client, mock_neo4j, override_max_queries_per_month,
):
    """Admin endpoints bypass the quota."""
    override_max_queries_per_month(1)
    mock_neo4j.set_query_count(10_000)

    response = client.get("/api/admin/config")

    assert response.status_code != 429, response.text
    mock_neo4j.get_llm_completion_count_this_month.assert_not_called()
