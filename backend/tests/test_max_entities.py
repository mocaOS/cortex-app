"""Tests for MAX_ENTITIES enforcement across all entity-creating entry points.

MAX_ENTITIES caps total entity count globally, with sentinel `0` meaning
unlimited. The cap is enforced at:
- POST /api/upload
- POST /api/custom-input
- library_transfer_service.import_library (admin bulk import)
"""

from __future__ import annotations

import io
import json
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# /api/upload
# ---------------------------------------------------------------------------

def test_upload_unlimited_when_max_entities_zero(client, mock_neo4j):
    mock_neo4j.set_entity_count(5_000)

    response = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["document_id"] == "fake-doc-id-123"


def test_upload_allowed_just_below_cap(client, mock_neo4j, override_max_entities):
    override_max_entities(10)
    mock_neo4j.set_entity_count(9)

    response = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
    )

    assert response.status_code == 200, response.text
    assert response.json()["document_id"] == "fake-doc-id-123"


def test_upload_rejected_at_cap(client, mock_neo4j, override_max_entities):
    override_max_entities(10)
    mock_neo4j.set_entity_count(10)

    response = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "Entity limit reached" in detail
    assert "10" in detail
    mock_neo4j.find_document_by_filename_and_size.assert_not_called()


def test_upload_rejected_over_cap_defensive(client, mock_neo4j, override_max_entities):
    override_max_entities(10)
    mock_neo4j.set_entity_count(11)

    response = client.post(
        "/api/upload",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
    )

    assert response.status_code == 403
    assert "Entity limit reached" in response.json()["detail"]
    mock_neo4j.find_document_by_filename_and_size.assert_not_called()


# ---------------------------------------------------------------------------
# /api/custom-input
# ---------------------------------------------------------------------------

def _custom_input_payload() -> dict:
    return {
        "input_type": "text",
        "content": "Some custom knowledge content longer than ten characters.",
        "title": "Test entry",
        "start_processing": False,
    }


def test_custom_input_unlimited_when_max_entities_zero(
    client, mock_neo4j, mock_processors, monkeypatch,
):
    mock_neo4j.set_entity_count(5_000)
    monkeypatch.setattr(
        "app.main.generate_filename_with_llm",
        AsyncMock(return_value="test_topic"),
    )

    response = client.post("/api/custom-input", json=_custom_input_payload())

    assert response.status_code == 200, response.text
    assert response.json()["document_id"] == "fake-doc-id-123"


def test_custom_input_allowed_just_below_cap(
    client, mock_neo4j, mock_processors, override_max_entities, monkeypatch,
):
    override_max_entities(10)
    mock_neo4j.set_entity_count(9)
    monkeypatch.setattr(
        "app.main.generate_filename_with_llm",
        AsyncMock(return_value="test_topic"),
    )

    response = client.post("/api/custom-input", json=_custom_input_payload())

    assert response.status_code == 200, response.text
    assert response.json()["document_id"] == "fake-doc-id-123"


def test_custom_input_rejected_at_cap(
    client, mock_neo4j, override_max_entities, monkeypatch,
):
    override_max_entities(10)
    mock_neo4j.set_entity_count(10)
    fake_filename = AsyncMock(return_value="test_topic")
    monkeypatch.setattr("app.main.generate_filename_with_llm", fake_filename)

    response = client.post("/api/custom-input", json=_custom_input_payload())

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "Entity limit reached" in detail
    assert "10" in detail
    fake_filename.assert_not_called()


def test_custom_input_rejected_over_cap_defensive(
    client, mock_neo4j, override_max_entities, monkeypatch,
):
    override_max_entities(10)
    mock_neo4j.set_entity_count(11)
    fake_filename = AsyncMock(return_value="test_topic")
    monkeypatch.setattr("app.main.generate_filename_with_llm", fake_filename)

    response = client.post("/api/custom-input", json=_custom_input_payload())

    assert response.status_code == 403
    assert "Entity limit reached" in response.json()["detail"]
    fake_filename.assert_not_called()


# ---------------------------------------------------------------------------
# library_transfer_service.import_library — direct unit test
# ---------------------------------------------------------------------------

def _build_export_zip_with_entities(num_entities: int) -> bytes:
    """Build a minimal-valid library export ZIP with N dummy entities.

    documents.ndjson is left empty so MAX_FILES does not fire upstream and
    obscure the MAX_ENTITIES check we're testing.
    """
    from app.services.library_transfer_service import EXPORT_VERSION
    from app.config import get_settings

    settings = get_settings()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "version": EXPORT_VERSION,
            "embedding_model": settings.embedding_model,
            "embedding_dimension": settings.embedding_dimension,
        }))
        zf.writestr("documents.ndjson", "")
        zf.writestr("collections.ndjson", "")
        zf.writestr("chunks.ndjson", "")
        entity_lines = [
            json.dumps({
                "id": f"ent-{i}",
                "name": f"Entity {i}",
                "type": "Concept",
            })
            for i in range(num_entities)
        ]
        zf.writestr("entities.ndjson", "\n".join(entity_lines))
    return buf.getvalue()


def test_library_import_rejected_when_over_entity_cap(
    mock_neo4j, override_max_entities, tmp_path,
):
    """Library import must reject if it would exceed MAX_ENTITIES."""
    from app.services.library_transfer_service import LibraryTransferService

    override_max_entities(10)
    # Clean mode: target instance must be empty. Default mock_neo4j has
    # document_count=0 and entity_count=0, which satisfies clean mode.

    zip_bytes = _build_export_zip_with_entities(num_entities=11)
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(zip_bytes)

    service = LibraryTransferService(neo4j_service=mock_neo4j)
    update_progress = MagicMock()
    complete_task = MagicMock()
    fail_task = MagicMock()

    service.import_library(
        task_id="task-1",
        zip_path=str(zip_path),
        mode="clean",
        update_progress=update_progress,
        complete_task_fn=complete_task,
        fail_task_fn=fail_task,
    )

    fail_task.assert_called_once()
    args = fail_task.call_args.args
    assert args[0] == "task-1"
    assert "Entity limit reached" in args[1]
    assert "Upgrade your plan" in args[1]
    assert "10" in args[1]
    complete_task.assert_not_called()
    mock_neo4j.import_documents_batch.assert_not_called()
    mock_neo4j.import_collections_batch.assert_not_called()
