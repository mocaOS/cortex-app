"""HTTP status mapping for git-provider failures at the connection endpoints.

A provider *client* error — bad PAT (401), no access (403), missing repo (404) —
is the caller's problem and must surface as that 4xx, not as a 502 that reads as
a server fault (and gets double-reported to the error tracker as one). Only
genuine upstream 5xx / network failures stay 502.

Regression coverage for GlitchTip CORTEX-BACKEND-5V / -IT (bad PAT → HTTP 502).
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.services.git_providers.base import GitProviderError


class _FakeProvider:
    """Stand-in provider whose calls raise a chosen GitProviderError."""

    def __init__(self, err: GitProviderError):
        self._err = err

    async def verify(self):
        raise self._err

    async def list_repos(self, page: int = 1):
        raise self._err

    async def default_branch(self, owner, name):  # pragma: no cover - not reached
        return "main"


@pytest.fixture
def git_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "enable_git_integration", True)


def _patch_provider(monkeypatch, err: GitProviderError):
    monkeypatch.setattr(
        "app.services.git_providers.get_provider",
        lambda *a, **k: _FakeProvider(err),
    )


_CREATE_BODY = {
    "vendor": "github",
    "repo_owner": "octocat",
    "repo_name": "hello-world",
    "pat": "ghp_badtoken12345",
}


class TestCreateConnectionStatusMapping:
    def test_bad_pat_returns_403_not_502(self, client, monkeypatch, git_enabled):
        # Provider 401 → our 403: a Cortex 401 means "session expired" and the
        # admin UI logs the user out on it, so a bad PAT must not look like one.
        _patch_provider(monkeypatch, GitProviderError(
            "github GET https://api.github.com/user → HTTP 401: Bad credentials",
            status_code=401,
        ))
        resp = client.post("/api/integrations/git/connections", json=_CREATE_BODY)
        assert resp.status_code == 403
        assert "Bad credentials" in resp.json()["detail"]

    def test_forbidden_returns_403(self, client, monkeypatch, git_enabled):
        _patch_provider(monkeypatch, GitProviderError("no access", status_code=403))
        resp = client.post("/api/integrations/git/connections", json=_CREATE_BODY)
        assert resp.status_code == 403

    def test_upstream_5xx_stays_502(self, client, monkeypatch, git_enabled):
        _patch_provider(monkeypatch, GitProviderError("github 503", status_code=503))
        resp = client.post("/api/integrations/git/connections", json=_CREATE_BODY)
        assert resp.status_code == 502

    def test_network_failure_no_status_stays_502(self, client, monkeypatch, git_enabled):
        _patch_provider(monkeypatch, GitProviderError("github request failed: timeout"))
        resp = client.post("/api/integrations/git/connections", json=_CREATE_BODY)
        assert resp.status_code == 502


class TestBrowseStatusMapping:
    def test_browse_bad_pat_returns_403(self, client, monkeypatch, git_enabled):
        _patch_provider(monkeypatch, GitProviderError("bad creds", status_code=401))
        resp = client.get(
            "/api/integrations/git/browse",
            params={"vendor": "github", "pat": "ghp_badtoken12345"},
        )
        assert resp.status_code == 403

    def test_browse_bad_input_valueerror_returns_400(self, client, monkeypatch, git_enabled):
        def _raise(*a, **k):
            raise ValueError("unsupported vendor")
        monkeypatch.setattr("app.services.git_providers.get_provider", _raise)
        resp = client.get(
            "/api/integrations/git/browse",
            params={"vendor": "github", "pat": "ghp_badtoken12345"},
        )
        assert resp.status_code == 400


# -----------------------------------------------------------------------------
# Target collection: validated on create/update, synced documents follow a move
# -----------------------------------------------------------------------------

class _OkProvider:
    async def verify(self):
        from app.services.git_providers.base import VerifyResult
        return VerifyResult(valid=True, login="octocat")

    async def default_branch(self, owner, name):
        return "main"


def _patch_ok_provider(monkeypatch):
    monkeypatch.setattr(
        "app.services.git_providers.get_provider",
        lambda *a, **k: _OkProvider(),
    )


_EXISTING = {
    "id": "git_abc123def456",
    "vendor": "github",
    "repo_owner": "octocat",
    "repo_name": "hello-world",
    "pat": "ghp_sometoken1234",
    "pat_last4": "1234",
    "access_level": "read",
    "branch": "main",
    "default_branch": "main",
    "include_globs": [],
    "exclude_globs": [],
    "wiki_enabled": False,
    "collection_id": "col_old",
    "sync_interval_minutes": 0,
}


class TestTargetCollection:
    def test_create_rejects_unknown_collection(self, client, mock_neo4j, monkeypatch, git_enabled):
        _patch_ok_provider(monkeypatch)
        mock_neo4j.get_collection.return_value = None
        resp = client.post(
            "/api/integrations/git/connections",
            json={**_CREATE_BODY, "collection_id": "col_missing"},
        )
        assert resp.status_code == 400
        assert "col_missing" in resp.json()["detail"]
        mock_neo4j.create_git_connection.assert_not_called()

    def test_create_stores_valid_collection(self, client, mock_neo4j, monkeypatch, git_enabled):
        _patch_ok_provider(monkeypatch)
        mock_neo4j.get_collection.return_value = {"id": "col_docs", "name": "Docs"}
        mock_neo4j.create_git_connection.side_effect = lambda props: props
        resp = client.post(
            "/api/integrations/git/connections",
            json={**_CREATE_BODY, "collection_id": "col_docs"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["collection_id"] == "col_docs"
        stored = mock_neo4j.create_git_connection.call_args.args[0]
        assert stored["collection_id"] == "col_docs"

    def test_create_blank_collection_means_default(self, client, mock_neo4j, monkeypatch, git_enabled):
        _patch_ok_provider(monkeypatch)
        mock_neo4j.create_git_connection.side_effect = lambda props: props
        resp = client.post(
            "/api/integrations/git/connections",
            json={**_CREATE_BODY, "collection_id": "   "},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["collection_id"] is None
        mock_neo4j.get_collection.assert_not_called()

    def test_update_moves_synced_documents_to_new_collection(self, client, mock_neo4j, git_enabled):
        mock_neo4j.get_git_connection.return_value = dict(_EXISTING)
        mock_neo4j.get_collection.return_value = {"id": "col_new", "name": "New"}
        mock_neo4j.update_git_connection.side_effect = (
            lambda cid, props: {**_EXISTING, **props}
        )
        mock_neo4j.list_documents_for_git_connection.return_value = [
            {"id": "doc_1", "git_path": "README.md"},
            {"id": "doc_2", "git_path": "docs/a.md"},
        ]
        mock_neo4j.move_documents_to_collection.return_value = {"moved_count": 2}

        resp = client.patch(
            f"/api/integrations/git/connections/{_EXISTING['id']}",
            json={"collection_id": "col_new"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["collection_id"] == "col_new"
        mock_neo4j.move_documents_to_collection.assert_called_once_with(
            ["doc_1", "doc_2"], "col_new",
        )

    def test_update_same_collection_does_not_move(self, client, mock_neo4j, git_enabled):
        mock_neo4j.get_git_connection.return_value = dict(_EXISTING)
        mock_neo4j.get_collection.return_value = {"id": "col_old", "name": "Old"}
        mock_neo4j.update_git_connection.side_effect = (
            lambda cid, props: {**_EXISTING, **props}
        )
        resp = client.patch(
            f"/api/integrations/git/connections/{_EXISTING['id']}",
            json={"collection_id": "col_old", "wiki_enabled": True},
        )
        assert resp.status_code == 200, resp.text
        mock_neo4j.move_documents_to_collection.assert_not_called()

    def test_update_to_default_keeps_documents_in_place(self, client, mock_neo4j, git_enabled):
        mock_neo4j.get_git_connection.return_value = dict(_EXISTING)
        mock_neo4j.update_git_connection.side_effect = (
            lambda cid, props: {**_EXISTING, **props}
        )
        resp = client.patch(
            f"/api/integrations/git/connections/{_EXISTING['id']}",
            json={"collection_id": None},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["collection_id"] is None
        mock_neo4j.move_documents_to_collection.assert_not_called()

    def test_update_rejects_unknown_collection(self, client, mock_neo4j, git_enabled):
        mock_neo4j.get_git_connection.return_value = dict(_EXISTING)
        mock_neo4j.get_collection.return_value = None
        resp = client.patch(
            f"/api/integrations/git/connections/{_EXISTING['id']}",
            json={"collection_id": "col_missing"},
        )
        assert resp.status_code == 400
        mock_neo4j.update_git_connection.assert_not_called()
