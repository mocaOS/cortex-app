"""HTTP-layer auth-enforcement tests (without the auth-bypass override).

The shared `client` fixture overrides require_admin/manage/read with a fake admin,
so it never exercises real rejection. This builds a TestClient with Neo4j and
processors mocked but auth dependencies INTACT, verifying the security journey:
protected endpoints reject missing/invalid keys (401) and accept the admin env key.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def noauth_client(mock_neo4j, mock_processors):
    """TestClient with real auth dependencies (no override)."""
    from app.main import app

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


PROTECTED_GETS = ["/api/documents", "/api/stats", "/api/collections", "/api/graph/entities"]


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_protected_endpoint_rejects_missing_key_401(noauth_client, path):
    assert noauth_client.get(path).status_code == 401


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_protected_endpoint_rejects_invalid_key_401(noauth_client, path):
    r = noauth_client.get(path, headers={"X-API-Key": "cortex_ro_" + "z" * 64})
    assert r.status_code == 401


def test_admin_env_key_is_accepted(noauth_client):
    # _isolate_env sets settings.admin_api_key = "test-admin-key"
    r = noauth_client.get("/api/stats", headers={"X-API-Key": "test-admin-key"})
    assert r.status_code == 200


def test_health_needs_no_key(noauth_client):
    assert noauth_client.get("/health").status_code == 200
