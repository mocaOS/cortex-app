"""Tests for the LLM-completion usage meter (unit-denominated quota).

Covers the in-memory accumulator + Neo4j flush contract in
`app.services.usage_meter` and the client-factory counting wrap in
`app.services.llm_config` (every factory-built client's
chat.completions.create increments the meter; embeddings never do;
failed creates never do).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import usage_meter


@pytest.fixture()
def fake_neo4j_getter():
    fake = MagicMock()
    fake.get_llm_completion_count_this_month.return_value = {
        "total": 100, "query": 60, "processing": 40,
    }
    usage_meter.configure(lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Accumulator + flush contract
# ---------------------------------------------------------------------------

def test_record_completion_accumulates_by_kind():
    usage_meter.record_completion(kind="query")
    usage_meter.record_completion(kind="query")
    usage_meter.record_completion(kind="processing")
    assert usage_meter.pending_count() == 3


def test_record_completion_uses_contextvar_kind():
    usage_meter.set_usage_kind(usage_meter.KIND_PROCESSING)
    usage_meter.record_completion()
    counts = usage_meter.get_completions_this_month()
    assert counts["processing"] == 1
    assert counts["total"] == 1


def test_flush_writes_snapshot_and_clears_pending(fake_neo4j_getter):
    usage_meter.record_completion(n=3, kind="query")
    usage_meter.record_completion(n=2, kind="processing")

    usage_meter.flush_now()

    assert usage_meter.pending_count() == 0
    fake_neo4j_getter.increment_llm_completions.assert_called_once()
    date_str, by_kind = fake_neo4j_getter.increment_llm_completions.call_args[0]
    assert by_kind == {"query": 3, "processing": 2}
    assert len(date_str) == 10  # YYYY-MM-DD


def test_flush_failure_restores_pending(fake_neo4j_getter):
    fake_neo4j_getter.increment_llm_completions.side_effect = RuntimeError("neo4j down")
    usage_meter.record_completion(n=5, kind="query")

    usage_meter.flush_now()

    # Counts must not be lost when the write fails.
    assert usage_meter.pending_count() == 5


def test_get_completions_merges_stored_and_pending(fake_neo4j_getter):
    usage_meter.record_completion(n=2, kind="query")

    counts = usage_meter.get_completions_this_month()

    assert counts["total"] == 102
    assert counts["query"] == 62
    assert counts["processing"] == 40


def test_get_completions_without_getter_is_pending_only():
    usage_meter.record_completion(n=4, kind="query")
    counts = usage_meter.get_completions_this_month()
    assert counts == {"total": 4, "query": 4, "processing": 0}


def test_record_completion_never_raises():
    # Even with no getter and odd inputs, recording must be safe.
    usage_meter.record_completion(n=1, kind=None)
    assert usage_meter.pending_count() == 1


# ---------------------------------------------------------------------------
# Client-factory counting wrap
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _plain_openai_path(monkeypatch):
    """Force the non-Langfuse factory branch.

    The langfuse.openai drop-in binds openai.OpenAI/AsyncOpenAI once at first
    import, so per-test monkeypatches of `openai.*` would go stale behind it.
    The plain branch resolves `from openai import ...` at every call.
    """
    from app.services import llm_config

    monkeypatch.setattr(llm_config, "_use_langfuse", lambda: False)


def _fake_async_client_cls(create_mock):
    client = MagicMock()
    client.chat.completions.create = create_mock
    return MagicMock(return_value=client), client


def test_async_factory_counts_successful_create(monkeypatch):
    from app.services import llm_config

    create = AsyncMock(return_value=MagicMock())
    factory_cls, _ = _fake_async_client_cls(create)
    monkeypatch.setattr("openai.AsyncOpenAI", factory_cls)

    client = llm_config.make_async_openai_client(
        api_key="test-count-1", base_url="https://example.invalid/v1"
    )
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        client.chat.completions.create(model="m", messages=[])
    )

    assert usage_meter.pending_count() == 1
    create.assert_awaited_once()


def test_async_factory_does_not_count_failed_create(monkeypatch):
    from app.services import llm_config

    create = AsyncMock(side_effect=RuntimeError("boom"))
    factory_cls, _ = _fake_async_client_cls(create)
    monkeypatch.setattr("openai.AsyncOpenAI", factory_cls)

    client = llm_config.make_async_openai_client(
        api_key="test-count-2", base_url="https://example.invalid/v1"
    )
    loop = asyncio.get_event_loop_policy().new_event_loop()
    with pytest.raises(RuntimeError):
        loop.run_until_complete(client.chat.completions.create(model="m", messages=[]))

    assert usage_meter.pending_count() == 0


def test_sync_factory_counts_successful_create(monkeypatch):
    from app.services import llm_config

    create = MagicMock(return_value=MagicMock())
    client_instance = MagicMock()
    client_instance.chat.completions.create = create
    monkeypatch.setattr("openai.OpenAI", MagicMock(return_value=client_instance))

    client = llm_config.make_openai_client(
        api_key="test-count-3", base_url="https://example.invalid/v1"
    )
    client.chat.completions.create(model="m", messages=[])

    assert usage_meter.pending_count() == 1


def test_factory_does_not_touch_embeddings(monkeypatch):
    from app.services import llm_config

    client_instance = MagicMock()
    embed_create = MagicMock(return_value=MagicMock())
    client_instance.embeddings.create = embed_create
    monkeypatch.setattr("openai.OpenAI", MagicMock(return_value=client_instance))

    client = llm_config.make_openai_client(
        api_key="test-count-4", base_url="https://example.invalid/v1"
    )
    client.embeddings.create(model="e", input=["hello"])

    assert usage_meter.pending_count() == 0
    assert client.embeddings.create is embed_create


# ---------------------------------------------------------------------------
# Fleet orchestration surface: /api/instance/status carries counts + meter
# ---------------------------------------------------------------------------

def test_instance_status_exposes_usage_and_counts(
    client, mock_neo4j, override_max_queries_per_month,
):
    """meta-cortex reads library size + the unit meter from instance/status."""
    override_max_queries_per_month(1000)
    stats = dict(mock_neo4j.get_stats.return_value)
    stats.update({"document_count": 12, "entity_count": 345, "collection_count": 3})
    mock_neo4j.get_stats.return_value = stats
    mock_neo4j.verify_connectivity.return_value = True
    mock_neo4j._get_meta.return_value = None
    mock_neo4j.get_llm_completion_count_this_month.return_value = {
        "total": 250, "query": 100, "processing": 150,
    }

    response = client.get("/api/instance/status")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document_count"] == 12
    assert body["entity_count"] == 345
    assert body["collection_count"] == 3
    assert body["monthly_usage_used"] == 250
    assert body["monthly_usage_limit"] == 1000
    assert body["monthly_usage_query"] == 100
    assert body["monthly_usage_processing"] == 150


def test_factory_survives_duck_typed_client(monkeypatch):
    """A client object without .chat must not break construction (stubs)."""
    from app.services import llm_config

    monkeypatch.setattr("openai.OpenAI", MagicMock(return_value=object()))
    client = llm_config.make_openai_client(
        api_key="test-count-5", base_url="https://example.invalid/v1"
    )
    assert client is not None
