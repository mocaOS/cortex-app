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
    def test_bad_pat_returns_401_not_502(self, client, monkeypatch, git_enabled):
        _patch_provider(monkeypatch, GitProviderError(
            "github GET https://api.github.com/user → HTTP 401: Bad credentials",
            status_code=401,
        ))
        resp = client.post("/api/integrations/git/connections", json=_CREATE_BODY)
        assert resp.status_code == 401
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
    def test_browse_bad_pat_returns_401(self, client, monkeypatch, git_enabled):
        _patch_provider(monkeypatch, GitProviderError("bad creds", status_code=401))
        resp = client.get(
            "/api/integrations/git/browse",
            params={"vendor": "github", "pat": "ghp_badtoken12345"},
        )
        assert resp.status_code == 401

    def test_browse_bad_input_valueerror_returns_400(self, client, monkeypatch, git_enabled):
        def _raise(*a, **k):
            raise ValueError("unsupported vendor")
        monkeypatch.setattr("app.services.git_providers.get_provider", _raise)
        resp = client.get(
            "/api/integrations/git/browse",
            params={"vendor": "github", "pat": "ghp_badtoken12345"},
        )
        assert resp.status_code == 400
