"""Shared pytest fixtures for the Cortex backend test suite.

Two fixtures are autouse:
- `_isolate_env`: pins settings to safe test defaults so no test reads the real .env
- `mock_llm`: hard-blocks every path that could make a real LLM API call

Tests that exercise endpoints additionally request `client`, which builds a
FastAPI TestClient with Neo4j/processors mocked and auth dependencies bypassed.
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


class LLMCallNotAllowedInTest(RuntimeError):
    """Raised when test code attempts a real LLM/network call.

    The autouse `mock_llm` fixture installs raisers at every known LLM
    entry point. If a test legitimately needs a fake response, opt in via
    `mock_llm.set_chat_response("...")`.
    """


# ---------------------------------------------------------------------------
# Autouse: isolate settings & filesystem
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Reset Settings to safe values before every test, restore after.

    Mutates the @lru_cache'd Settings instance in-place. Does NOT call
    cache_clear() because that would re-read the real .env file.
    """
    from app.config import get_settings

    settings = get_settings()

    upload_dir = tmp_path / "uploads"
    custom_inputs_dir = tmp_path / "custom_inputs"
    upload_dir.mkdir(parents=True, exist_ok=True)
    custom_inputs_dir.mkdir(parents=True, exist_ok=True)

    saved: dict[str, Any] = {
        "max_files": settings.max_files,
        "max_collections": settings.max_collections,
        "max_entities": settings.max_entities,
        "max_queries_per_month": settings.max_queries_per_month,
        "upload_dir": settings.upload_dir,
        "custom_inputs_dir": settings.custom_inputs_dir,
        "openai_api_key": settings.openai_api_key,
        "graph_extraction_api_key": settings.graph_extraction_api_key,
        "relationship_extraction_api_key": settings.relationship_extraction_api_key,
        "vision_model": getattr(settings, "vision_model", ""),
        "admin_api_key": settings.admin_api_key,
        "enable_skills": settings.enable_skills,
        "track_admin_api_key_usage": settings.track_admin_api_key_usage,
        "api_key_cache_ttl_seconds": settings.api_key_cache_ttl_seconds,
    }

    settings.max_files = 0
    settings.max_collections = 0
    settings.max_entities = 0
    settings.max_queries_per_month = 0
    settings.upload_dir = str(upload_dir)
    settings.custom_inputs_dir = str(custom_inputs_dir)
    settings.openai_api_key = ""
    # Blank the tier keys too: a developer's .env otherwise leaks into
    # get_extraction_llm_config()/get_relationship_llm_config(), and any
    # code path that builds a tier client directly (e.g. the one-shot
    # max_retries=0 clients) trips the LLM-construction guard.
    settings.graph_extraction_api_key = ""
    settings.relationship_extraction_api_key = ""
    if hasattr(settings, "vision_model"):
        settings.vision_model = ""
    settings.admin_api_key = "test-admin-key"
    settings.enable_skills = False
    settings.track_admin_api_key_usage = False
    settings.api_key_cache_ttl_seconds = 30

    # The auth validation cache is module-global; never leak entries between
    # tests (a cached AuthResult would mask each test's mock_neo4j setup).
    from app.services.auth_service import invalidate_api_key_cache
    invalidate_api_key_cache()

    yield settings

    invalidate_api_key_cache()
    for k, v in saved.items():
        setattr(settings, k, v)


# ---------------------------------------------------------------------------
# Autouse: hard-block all real LLM calls
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_llm(monkeypatch):
    """Block every entry point a real LLM call could escape through.

    Default behaviour is to RAISE LLMCallNotAllowedInTest if anything tries
    to construct an OpenAI client or POST to an LLM-shaped endpoint. Tests
    that need a fake response opt in via `mock_llm.set_chat_response(text)`.

    Patches:
    - openai.OpenAI / openai.AsyncOpenAI (function-import resolution)
    - app.services.graph_extractor.OpenAI / AsyncOpenAI (top-level imports)
    - app.services.researcher_agent.AsyncOpenAI (top-level import)
    - haystack.components.embedders.OpenAIDocumentEmbedder / OpenAITextEmbedder
    - httpx.AsyncClient.post (vision_analyzer)
    """

    def _raiser(*args, **kwargs):
        raise LLMCallNotAllowedInTest(
            "Test attempted to construct a real LLM client. Configure "
            "`mock_llm.set_chat_response(...)` in the test if intentional."
        )

    async def _block_async_post(self, url, *args, **kwargs):
        raise LLMCallNotAllowedInTest(
            f"Test attempted httpx POST to {url}. Configure mock_llm if intentional."
        )

    monkeypatch.setattr("openai.OpenAI", _raiser)
    monkeypatch.setattr("openai.AsyncOpenAI", _raiser)
    monkeypatch.setattr(
        "app.services.graph_extractor.OpenAI", _raiser, raising=False,
    )
    monkeypatch.setattr(
        "app.services.graph_extractor.AsyncOpenAI", _raiser, raising=False,
    )
    monkeypatch.setattr(
        "app.services.researcher_agent.AsyncOpenAI", _raiser, raising=False,
    )
    monkeypatch.setattr(
        "haystack.components.embedders.OpenAIDocumentEmbedder",
        _raiser, raising=False,
    )
    monkeypatch.setattr(
        "haystack.components.embedders.OpenAITextEmbedder",
        _raiser, raising=False,
    )
    monkeypatch.setattr("httpx.AsyncClient.post", _block_async_post)

    class _LLMController:
        @staticmethod
        def set_chat_response(text: str) -> None:
            """Replace the raiser with a fake client returning `text`."""
            fake_completion = MagicMock()
            fake_completion.choices = [MagicMock(message=MagicMock(content=text))]

            sync_client = MagicMock()
            sync_client.chat.completions.create = MagicMock(return_value=fake_completion)
            async_client = MagicMock()
            async_client.chat.completions.create = AsyncMock(return_value=fake_completion)

            sync_factory = MagicMock(return_value=sync_client)
            async_factory = MagicMock(return_value=async_client)

            monkeypatch.setattr("openai.OpenAI", sync_factory)
            monkeypatch.setattr("openai.AsyncOpenAI", async_factory)
            monkeypatch.setattr(
                "app.services.graph_extractor.OpenAI", sync_factory, raising=False,
            )
            monkeypatch.setattr(
                "app.services.graph_extractor.AsyncOpenAI", async_factory,
                raising=False,
            )
            monkeypatch.setattr(
                "app.services.researcher_agent.AsyncOpenAI", async_factory,
                raising=False,
            )

    return _LLMController()


# ---------------------------------------------------------------------------
# Opt-in: mocked Neo4j service
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_usage_meter():
    """Isolate the module-global LLM usage meter between tests.

    Clears pending in-memory counts and deregisters the Neo4j getter so a
    test's flush can never land on a previous test's mock.
    """
    from app.services import usage_meter

    usage_meter._reset_for_tests()
    yield
    usage_meter._reset_for_tests()


@pytest.fixture
def mock_neo4j(monkeypatch):
    """Replace the Neo4j service singleton with a MagicMock.

    Tests can drive document_count via `mock_neo4j.set_document_count(n)`.
    """
    fake = MagicMock()
    fake.get_stats.return_value = {
        "document_count": 0,
        "chunk_count": 0,
        "total_size": 0,
        "entity_count": 0,
        "relationship_count": 0,
        "community_count": 0,
        "collection_count": 0,
    }
    fake.find_document_by_filename_and_size.return_value = None
    fake.initialize_schema.return_value = None
    fake.ensure_admin_key_exists.return_value = None
    fake.set_custom_input_metadata.return_value = None
    fake.import_documents_batch.return_value = 0
    fake.import_collections_batch.return_value = 0
    fake.create_collection.return_value = {
        "id": "fake-collection-id",
        "name": "Test",
        "description": None,
    }
    fake.close.return_value = None
    fake.get_llm_completion_count_this_month.return_value = {
        "total": 0, "query": 0, "processing": 0,
    }
    fake.increment_llm_completions.return_value = None
    # Task-record persistence (write-through shadow of the in-memory store)
    fake.get_task_record.return_value = None
    fake.upsert_task_records.return_value = 0
    fake.fail_interrupted_task_records.return_value = 0
    fake.prune_task_records.return_value = 0
    fake.delete_task_record.return_value = None

    def _set_document_count(n: int) -> None:
        current = dict(fake.get_stats.return_value)
        current["document_count"] = n
        fake.get_stats.return_value = current
    fake.set_document_count = _set_document_count

    def _set_entity_count(n: int) -> None:
        current = dict(fake.get_stats.return_value)
        current["entity_count"] = n
        fake.get_stats.return_value = current
    fake.set_entity_count = _set_entity_count

    def _set_query_count(n: int) -> None:
        # The quota is denominated in LLM completions (unit metering).
        fake.get_llm_completion_count_this_month.return_value = {
            "total": n, "query": n, "processing": 0,
        }
    fake.set_query_count = _set_query_count

    def _set_collection_count(n: int) -> None:
        current = dict(fake.get_stats.return_value)
        current["collection_count"] = n
        fake.get_stats.return_value = current
    fake.set_collection_count = _set_collection_count

    import app.services.neo4j_service as neo4j_module
    monkeypatch.setattr(neo4j_module, "_neo4j_service", fake, raising=False)
    monkeypatch.setattr(
        "app.services.neo4j_service.get_neo4j_service",
        lambda: fake, raising=True,
    )
    monkeypatch.setattr("app.main.get_neo4j_service", lambda: fake, raising=True)
    monkeypatch.setattr(
        "app.services.auth_service.get_neo4j_service",
        lambda: fake, raising=True,
    )
    monkeypatch.setattr(
        "app.services.document_processor.get_neo4j_service",
        lambda: fake, raising=False,
    )

    return fake


# ---------------------------------------------------------------------------
# Opt-in: mocked document/query processors
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_processors(monkeypatch):
    """Replace document_processor and query_processor singletons with mocks.

    Prevents the FastAPI lifespan from instantiating real Haystack/OpenAI
    clients during TestClient startup.
    """
    fake_doc = MagicMock()
    fake_doc.store_file_only = AsyncMock(return_value="fake-doc-id-123")
    fake_doc.process_file = AsyncMock(return_value="fake-doc-id-123")

    fake_query = MagicMock()
    fake_graph = MagicMock()
    fake_vision = MagicMock()
    fake_vision.is_vision_model_available = False

    monkeypatch.setattr(
        "app.main.get_document_processor", lambda: fake_doc, raising=True,
    )
    monkeypatch.setattr(
        "app.main.get_query_processor", lambda: fake_query, raising=True,
    )
    monkeypatch.setattr(
        "app.services.document_processor.get_document_processor",
        lambda: fake_doc, raising=True,
    )
    monkeypatch.setattr(
        "app.services.document_processor.get_query_processor",
        lambda: fake_query, raising=True,
    )
    monkeypatch.setattr(
        "app.services.document_processor._document_processor",
        fake_doc, raising=False,
    )
    monkeypatch.setattr(
        "app.services.document_processor._query_processor",
        fake_query, raising=False,
    )

    return SimpleNamespace(doc=fake_doc, query=fake_query, graph=fake_graph, vision=fake_vision)


# ---------------------------------------------------------------------------
# FastAPI TestClient with auth bypass
# ---------------------------------------------------------------------------

@pytest.fixture
def client(mock_neo4j, mock_processors):
    """FastAPI TestClient with Neo4j/processors mocked and auth bypassed."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.models import APIKeyPermission
    from app.services.auth_service import (
        AuthResult,
        require_admin,
        require_manage_permission,
        require_read_permission,
    )

    fake_admin = AuthResult(
        is_authenticated=True,
        is_admin=True,
        permissions=[APIKeyPermission.READ, APIKeyPermission.MANAGE],
        key_id="test-admin",
    )
    app.dependency_overrides[require_manage_permission] = lambda: fake_admin
    app.dependency_overrides[require_admin] = lambda: fake_admin
    app.dependency_overrides[require_read_permission] = lambda: fake_admin

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helper: set settings.max_files for one test
# ---------------------------------------------------------------------------

@pytest.fixture
def override_max_files(_isolate_env):
    """Factory: `override_max_files(10)` sets settings.max_files for this test."""
    settings = _isolate_env

    def _set(n: int) -> None:
        settings.max_files = n

    return _set


@pytest.fixture
def override_max_collections(_isolate_env):
    """Factory: `override_max_collections(10)` sets settings.max_collections for this test."""
    settings = _isolate_env

    def _set(n: int) -> None:
        settings.max_collections = n

    return _set


@pytest.fixture
def override_max_entities(_isolate_env):
    """Factory: `override_max_entities(10)` sets settings.max_entities for this test."""
    settings = _isolate_env

    def _set(n: int) -> None:
        settings.max_entities = n

    return _set


@pytest.fixture
def override_max_queries_per_month(_isolate_env):
    """Factory: `override_max_queries_per_month(10)` sets the monthly quota for this test."""
    settings = _isolate_env

    def _set(n: int) -> None:
        settings.max_queries_per_month = n

    return _set
