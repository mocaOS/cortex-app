"""Registry consumer tests — catalog fetch, sha256-verified install, gates.

The registry's trust anchor is the pinned checksum: these tests prove the
installer refuses artifacts whose bytes don't match the catalog (tampered
content, size lies) and installs byte-exact ones end to end.
"""

import hashlib
import json

import httpx
import pytest

from tests.test_apps import FakeAPIKeyService, apps_env, make_manifest, make_zip  # noqa: F401


@pytest.fixture
def registry_env(apps_env, monkeypatch):  # noqa: F811
    """A fake registry: one valid app zip served over faked httpx."""
    import app.services.app_registry_service as registry_module

    registry_module._registry_service = None

    manifest = make_manifest(id="registry-app")
    zip_bytes = make_zip(manifest)
    listing = {
        "slug": "registry-app",
        "app": {k: v for k, v in manifest.items() if k != "$schema"},
        "artifact": {
            "url": "https://github.example/releases/registry-app-1.0.0.zip",
            "sha256": hashlib.sha256(zip_bytes).hexdigest(),
            "size": len(zip_bytes),
        },
        "repo": "https://github.example/registry-app",
        "tags": ["test"],
        "listedAt": "2026-07-20",
        "status": "active",
    }
    index = {"version": 1, "apps": [listing, {"slug": "yanked-app", "status": "yanked",
                                                "app": {}, "artifact": {}}]}
    state = {"zip": zip_bytes, "index": index, "fetches": []}

    async def fake_get(self, url, **kwargs):
        state["fetches"].append(str(url))
        return httpx.Response(200, json=state["index"], request=httpx.Request("GET", str(url)))

    class FakeStream:
        def __init__(self, url):
            self.url = url
            self.status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def aiter_bytes(self):
            data = state["zip"]
            for i in range(0, len(data), 8192):
                yield data[i : i + 8192]

    def fake_stream(self, method, url, **kwargs):
        state["fetches"].append(str(url))
        return FakeStream(str(url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)

    yield state
    registry_module._registry_service = None


def test_registry_browse_and_verified_install(registry_env, client):
    # browse: catalog joined with install state
    resp = client.get("/api/admin/apps/registry")
    assert resp.status_code == 200, resp.text
    apps = resp.json()["apps"]
    assert len(apps) == 1  # yanked/malformed entries filtered out
    entry = apps[0]
    assert entry["slug"] == "registry-app"
    assert entry["installed_version"] is None
    assert entry["key_scope"] == "read"

    # install: artifact fetched, checksum verified, normal installer runs
    resp = client.post("/api/admin/apps/registry/install", json={"slug": "registry-app"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "registry-app"

    # browse again: shows installed, no update pending
    apps = client.get("/api/admin/apps/registry").json()["apps"]
    assert apps[0]["installed_version"] == "1.0.0"
    assert apps[0]["update_available"] is False


def test_registry_install_refuses_checksum_mismatch(registry_env, client):
    registry_env["zip"] = registry_env["zip"] + b"tampered"
    # size check trips first on the padded zip; make size "match" the tampered
    # bytes so the sha256 gate is what refuses
    registry_env["index"]["apps"][0]["artifact"]["size"] = len(registry_env["zip"])
    resp = client.post("/api/admin/apps/registry/install", json={"slug": "registry-app"})
    assert resp.status_code == 502
    assert "checksum mismatch" in resp.json()["detail"]
    assert client.get("/api/admin/apps").json() == []  # nothing was installed


def test_registry_install_refuses_size_lie(registry_env, client):
    registry_env["index"]["apps"][0]["artifact"]["size"] = 10  # listing lies small
    resp = client.post("/api/admin/apps/registry/install", json={"slug": "registry-app"})
    assert resp.status_code == 502
    assert "larger than its listed size" in resp.json()["detail"]


def test_registry_unknown_slug_404(registry_env, client):
    resp = client.post("/api/admin/apps/registry/install", json={"slug": "nope"})
    assert resp.status_code == 404


def test_registry_cache_and_refresh(registry_env, client):
    client.get("/api/admin/apps/registry")
    fetches_after_first = len(registry_env["fetches"])
    client.get("/api/admin/apps/registry")  # served from cache
    assert len(registry_env["fetches"]) == fetches_after_first
    client.get("/api/admin/apps/registry?refresh=true")
    assert len(registry_env["fetches"]) == fetches_after_first + 1


def test_registry_disabled_without_url(registry_env, client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "app_registry_url", "")
    assert client.get("/api/admin/apps/registry").status_code == 404


def test_registry_404_when_apps_disabled(registry_env, client):
    from app.config import get_settings

    get_settings().enable_apps = False
    assert client.get("/api/admin/apps/registry").status_code == 404
    assert client.post(
        "/api/admin/apps/registry/install", json={"slug": "registry-app"}
    ).status_code == 404
