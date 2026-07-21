"""Apps subsystem tests — install lifecycle, tokens/grants, allowlist, serving.

Covers app_service.py plus the main.py endpoints (admin CRUD, static serving
with CSP, proxy auth/allowlist rejection paths, share-link shell). The proxy's
upstream forwarding itself is exercised live via the verify skill, not here.
"""

import io
import json
import time
import zipfile
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------

def make_manifest(**overrides):
    manifest = {
        "id": "test-app",
        "name": "Test App",
        "version": "1.0.0",
        "type": "static",
        "description": "A test app",
        "publisher": {"name": "tester"},
        "icon": "icon.svg",
        "entry": "index.html",
        "cortex": {
            "minVersion": "2.0.0",
            "keyScope": "read",
            "endpoints": ["search", "ask"],
            "collections": "all",
        },
        "externalHosts": [],
        "sharing": {"links": True},
    }
    manifest.update(overrides)
    return manifest


def make_zip(manifest=None, extra_files=None):
    manifest = manifest if manifest is not None else make_manifest()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app.json", json.dumps(manifest))
        zf.writestr("icon.svg", "<svg/>")
        zf.writestr("dist/index.html", "<!doctype html><title>t</title>")
        zf.writestr("dist/assets/app.js", "console.log(1)")
        for name, content in (extra_files or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


class FakeAPIKeyService:
    """Stands in for APIKeyService — no Neo4j."""

    minted = []
    revoked = []

    def create_api_key(self, name, permissions, created_by="admin",
                       collection_scope=None, allowed_collections=None, **_):
        key_id = f"key_{len(self.minted)}"
        FakeAPIKeyService.minted.append(
            {"name": name, "permissions": permissions,
             "collections": allowed_collections}
        )
        return SimpleNamespace(id=key_id, key=f"cortex_ro_fake_{key_id}",
                               key_prefix="cortex_ro_fa")

    def revoke_api_key(self, key_id):
        FakeAPIKeyService.revoked.append(key_id)
        return SimpleNamespace(id=key_id)


@pytest.fixture
def apps_env(tmp_path, monkeypatch):
    """Enable apps against a temp dir with a fresh service singleton."""
    from app.config import get_settings
    import app.services.app_service as app_service_module

    settings = get_settings()
    saved = {
        "enable_apps": settings.enable_apps,
        "apps_dir": settings.apps_dir,
        "session_secret": settings.session_secret,
        "app_token_ttl_seconds": settings.app_token_ttl_seconds,
        "app_max_package_mb": settings.app_max_package_mb,
    }
    settings.enable_apps = True
    settings.apps_dir = str(tmp_path / "apps")
    settings.session_secret = "unit-test-secret-material-32chars!"
    settings.app_token_ttl_seconds = 900
    settings.app_max_package_mb = 5

    monkeypatch.setattr(
        "app.services.api_key_service.APIKeyService", FakeAPIKeyService
    )
    FakeAPIKeyService.minted = []
    FakeAPIKeyService.revoked = []
    app_service_module._app_service = None

    yield app_service_module.get_app_service()

    app_service_module._app_service = None
    for key, value in saved.items():
        setattr(settings, key, value)


# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------

def test_validate_manifest_accepts_valid(apps_env):
    assert apps_env.validate_manifest(make_manifest()) == []


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"id": "Bad_Id"}, "kebab-case"),
        ({"version": "1.0"}, "semver"),
        ({"type": "service"}, "cannot be hosted"),
        ({"type": "platform"}, "at least one capability"),
        ({"type": "platform", "capabilities": {"branding": {}}}, "not supported"),
        ({"type": "platform", "capabilities": {"tasks": {"cron": "* *"}}}, "takes no configuration"),
        ({"type": "platform", "capabilities": {"http": {}}}, '"hosts"'),
        ({"capabilities": {"http": {"hosts": ["x"]}}}, 'only valid for type: "platform"'),
        ({"cortex": None}, '"cortex" block'),
        ({"description": ""}, "description"),
        ({"icon": "nested/icon.svg"}, "icon"),
    ],
)
def test_validate_manifest_rejects(apps_env, overrides, fragment):
    issues = apps_env.validate_manifest(make_manifest(**overrides))
    assert any(fragment in issue for issue in issues), issues


def test_validate_manifest_rejects_bad_endpoints(apps_env):
    manifest = make_manifest()
    manifest["cortex"]["endpoints"] = ["/search"]  # leading slash forbidden
    assert any("endpoints" in i for i in apps_env.validate_manifest(manifest))


# ---------------------------------------------------------------------------
# Install lifecycle
# ---------------------------------------------------------------------------

def test_install_list_get_delete(apps_env):
    from app.models import APIKeyPermission

    info = apps_env.install_from_zip(make_zip())
    assert info.id == "test-app"
    assert info.enabled and info.key_prefix == "cortex_ro_fa"
    assert FakeAPIKeyService.minted[0]["name"] == "app:test-app"
    assert FakeAPIKeyService.minted[0]["permissions"] == [APIKeyPermission.READ]

    assert [a.id for a in apps_env.list_apps()] == ["test-app"]
    assert apps_env.get_app("test-app").version == "1.0.0"

    assert apps_env.delete_app("test-app") is True
    assert FakeAPIKeyService.revoked == ["key_0"]
    assert apps_env.get_app("test-app") is None


def test_upgrade_preserves_key_and_grants(apps_env):
    apps_env.install_from_zip(make_zip())
    apps_env.create_grant("test-app", "team", "viewer", None)

    manifest = make_manifest(version="1.1.0")
    info = apps_env.install_from_zip(make_zip(manifest))
    assert info.version == "1.1.0"
    assert len(FakeAPIKeyService.minted) == 1  # no second key
    assert info.grants_count == 1


def test_read_write_scope_mints_manage_key(apps_env):
    from app.models import APIKeyPermission

    manifest = make_manifest()
    manifest["cortex"]["keyScope"] = "read_write"
    apps_env.install_from_zip(make_zip(manifest))
    assert APIKeyPermission.MANAGE in FakeAPIKeyService.minted[0]["permissions"]


def test_user_selected_collections_restrict_key(apps_env):
    manifest = make_manifest()
    manifest["cortex"]["collections"] = "user-selected"
    apps_env.install_from_zip(make_zip(manifest), collections=["col-1"])
    assert FakeAPIKeyService.minted[0]["collections"] == ["col-1"]


def test_install_rejects_oversized_and_traversal_and_missing(apps_env):
    from app.services.app_service import AppValidationError

    cap = 5 * 1024 * 1024
    with pytest.raises(AppValidationError, match="cap"):
        apps_env.install_from_zip(b"x" * (cap + 1))

    with pytest.raises(AppValidationError, match="unsafe path"):
        apps_env.install_from_zip(make_zip(extra_files={"../evil.sh": "x"}))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("dist/index.html", "x")
    with pytest.raises(AppValidationError, match="app.json"):
        apps_env.install_from_zip(buf.getvalue())


# ---------------------------------------------------------------------------
# Tokens & grants
# ---------------------------------------------------------------------------

def test_token_roundtrip_and_rejections(apps_env):
    from app.config import get_settings

    apps_env.install_from_zip(make_zip())
    token, expires_at = apps_env.issue_token("test-app", "owner", "admin")

    payload = apps_env.validate_token(token, "test-app")
    assert payload["principal"] == "owner" and payload["role"] == "admin"

    assert apps_env.validate_token(token, "other-app") is None
    assert apps_env.validate_token(token[:-2] + "xx", "test-app") is None
    assert apps_env.validate_token("garbage", "test-app") is None

    get_settings().app_token_ttl_seconds = -10  # issue already-expired
    expired, _ = apps_env.issue_token("test-app", "owner", "admin")
    assert apps_env.validate_token(expired, "test-app") is None


def test_grant_exchange_and_revocation_kills_tokens(apps_env):
    apps_env.install_from_zip(make_zip())
    grant = apps_env.create_grant("test-app", "community", "viewer", None)

    result = apps_env.exchange_grant("test-app", grant["grant_token"])
    assert result is not None
    token, _, role = result
    assert role == "viewer"
    assert apps_env.validate_token(token, "test-app")["principal"] == f"link:{grant['id']}"

    apps_env.revoke_grant("test-app", grant["id"])
    assert apps_env.exchange_grant("test-app", grant["grant_token"]) is None
    # already-issued tokens from that grant die too
    assert apps_env.validate_token(token, "test-app") is None


def test_grant_token_cannot_be_replayed_as_app_token(apps_env):
    """A grant token re-prefixed cat_ must NOT validate as an app token —
    otherwise it would skip revocation entirely (token type confusion)."""
    apps_env.install_from_zip(make_zip())
    grant = apps_env.create_grant("test-app", "x", "viewer", None)
    from app.services.app_service import APP_TOKEN_PREFIX, GRANT_TOKEN_PREFIX

    body_sig = grant["grant_token"][len(GRANT_TOKEN_PREFIX):]
    forged = f"{APP_TOKEN_PREFIX}{body_sig}"
    assert apps_env.validate_token(forged, "test-app") is None


def test_grant_requires_sharing_manifest(apps_env):
    from app.services.app_service import AppValidationError

    manifest = make_manifest(sharing={"links": False})
    apps_env.install_from_zip(make_zip(manifest))
    with pytest.raises(AppValidationError, match="share links"):
        apps_env.create_grant("test-app", "x", "viewer", None)


def test_expired_grant_rejected(apps_env):
    apps_env.install_from_zip(make_zip())
    grant = apps_env.create_grant("test-app", "x", "viewer", expires_hours=1)
    # forge time forward by rewriting exp in the stored grant? simpler: build
    # an expired grant token via the service's signer
    payload = {"v": 1, "app": "test-app", "grant": grant["id"], "exp": int(time.time()) - 5}
    expired_token = apps_env._sign(payload, "cag_")
    assert apps_env.exchange_grant("test-app", expired_token) is None


# ---------------------------------------------------------------------------
# Allowlist & static resolution
# ---------------------------------------------------------------------------

def test_endpoint_allowlist_prefix_semantics(apps_env):
    apps_env.install_from_zip(make_zip())
    assert apps_env.endpoint_allowed("test-app", "search")
    assert apps_env.endpoint_allowed("test-app", "ask/stream")
    assert apps_env.endpoint_allowed("test-app", "ask/stream/thinking")
    assert apps_env.endpoint_allowed("test-app", "search?query=x")
    assert not apps_env.endpoint_allowed("test-app", "searchx")
    assert not apps_env.endpoint_allowed("test-app", "graph/entities")
    assert not apps_env.endpoint_allowed("test-app", "admin/apps")


def test_endpoint_allowlist_rejects_traversal(apps_env):
    """Encoded ../ must not escape the allowlist to reach undeclared endpoints
    (the upstream would normalize documents/..%2fsearch → search)."""
    manifest = make_manifest()
    manifest["cortex"]["endpoints"] = ["documents"]  # search NOT declared
    apps_env.install_from_zip(make_zip(manifest))
    assert apps_env.endpoint_allowed("test-app", "documents")
    assert not apps_env.endpoint_allowed("test-app", "documents/..%2fsearch")
    assert not apps_env.endpoint_allowed("test-app", "documents/../search")
    assert not apps_env.endpoint_allowed("test-app", "documents/..%252fsearch")
    assert not apps_env.endpoint_allowed("test-app", "documents/..%2f..%2fadmin%2fstats")
    assert not apps_env.endpoint_allowed("test-app", "documents//search")


def test_static_resolution_and_traversal(apps_env, tmp_path):
    apps_env.install_from_zip(make_zip())
    assert apps_env.resolve_static("test-app", "").name == "index.html"
    assert apps_env.resolve_static("test-app", "assets/app.js").name == "app.js"
    # SPA route falls back to entry; real missing asset does not
    assert apps_env.resolve_static("test-app", "some/route").name == "index.html"
    assert apps_env.resolve_static("test-app", "missing.js") is None
    assert apps_env.resolve_static("test-app", "../install.json") is None

    apps_env.set_enabled("test-app", False)
    assert apps_env.resolve_static("test-app", "") is None


def test_csp_resolves_external_hosts_from_config(apps_env):
    manifest = make_manifest(
        externalHosts=["${PAPERLESS_BASE_URL}"],
        config=[{"name": "PAPERLESS_BASE_URL", "type": "text", "required": True}],
    )
    apps_env.install_from_zip(make_zip(manifest))
    apps_env.save_config("test-app", {"PAPERLESS_BASE_URL": "https://p.example.com"})
    csp = apps_env.csp_header("test-app")
    assert "connect-src 'self' https://p.example.com" in csp


def test_secret_config_encrypted_and_masked(apps_env):
    manifest = make_manifest(
        config=[{"name": "TOKEN", "type": "secret", "required": True}]
    )
    apps_env.install_from_zip(make_zip(manifest))
    apps_env.save_config("test-app", {"TOKEN": "hunter2"})

    config = apps_env.get_config("test-app")
    assert config["values"]["TOKEN"] != "hunter2"  # masked
    # mask round-trip preserves the stored secret
    apps_env.save_config("test-app", {"TOKEN": config["values"]["TOKEN"]})
    assert apps_env._decrypted_config("test-app", manifest)["TOKEN"] == "hunter2"


# ---------------------------------------------------------------------------
# Platform capabilities (http + config read)
# ---------------------------------------------------------------------------

def platform_manifest(**overrides):
    manifest = make_manifest(
        id="pf-app",
        type="platform",
        capabilities={"http": {"hosts": ["${SERVICE_BASE_URL}", "api.example.com"]}},
        config=[
            {"name": "SERVICE_BASE_URL", "type": "text", "required": True},
            {"name": "SERVICE_TOKEN", "type": "secret",
             "auth_header": "Authorization: Token SERVICE_TOKEN"},
        ],
    )
    manifest.update(overrides)
    return manifest


def test_platform_manifest_accepted_and_hosts_resolved(apps_env):
    assert apps_env.validate_manifest(platform_manifest()) == []
    apps_env.install_from_zip(make_zip(platform_manifest()))
    # unresolved ${VAR} contributes nothing until configured
    assert apps_env.allowed_http_hosts("pf-app") == {"api.example.com"}
    apps_env.save_config("pf-app", {"SERVICE_BASE_URL": "https://svc.example.com:8443/base"})
    assert apps_env.allowed_http_hosts("pf-app") == {"api.example.com", "svc.example.com"}


def test_platform_auth_headers_from_secret_config(apps_env):
    apps_env.install_from_zip(make_zip(platform_manifest()))
    assert apps_env.platform_auth_headers("pf-app") == {}  # nothing configured yet
    apps_env.save_config("pf-app", {"SERVICE_TOKEN": "hunter2"})
    assert apps_env.platform_auth_headers("pf-app") == {"Authorization": "Token hunter2"}


def test_public_config_excludes_secrets(apps_env):
    apps_env.install_from_zip(make_zip(platform_manifest()))
    apps_env.save_config("pf-app", {"SERVICE_BASE_URL": "https://svc.example.com",
                                    "SERVICE_TOKEN": "hunter2"})
    assert apps_env.public_config("pf-app") == {"SERVICE_BASE_URL": "https://svc.example.com"}


def test_platform_endpoints_gating(apps_env, client):
    # a static app has no platform capabilities
    client.post("/api/admin/apps/install",
                files={"file": ("s.zip", make_zip(), "application/zip")})
    client.post("/api/admin/apps/install",
                files={"file": ("p.zip", make_zip(platform_manifest()), "application/zip")})
    static_token = client.post("/api/apps/test-app/token").json()["token"]
    pf_token = client.post("/api/apps/pf-app/token").json()["token"]

    # config read works for the platform app, secrets excluded
    client.put("/api/admin/apps/pf-app/config",
               json={"values": {"SERVICE_BASE_URL": "http://127.0.0.1:9", "SERVICE_TOKEN": "s3cret"}})
    cfg = client.get("/apps/pf-app/api/platform/config",
                     headers={"Authorization": f"Bearer {pf_token}"})
    assert cfg.status_code == 200
    assert cfg.json()["values"] == {"SERVICE_BASE_URL": "http://127.0.0.1:9"}

    # http capability: static app → 403; bad envelope → 400
    assert client.post("/apps/test-app/api/platform/http",
                       headers={"Authorization": f"Bearer {static_token}"},
                       json={"method": "GET", "url": "https://api.example.com/"}).status_code == 403
    assert client.post("/apps/pf-app/api/platform/http",
                       headers={"Authorization": f"Bearer {pf_token}"},
                       json={"nope": True}).status_code == 400

    # undeclared host → 403; declared-but-loopback → 403 (SSRF guard)
    assert client.post("/apps/pf-app/api/platform/http",
                       headers={"Authorization": f"Bearer {pf_token}"},
                       json={"method": "GET", "url": "https://evil.example.org/x"}).status_code == 403
    blocked = client.post("/apps/pf-app/api/platform/http",
                          headers={"Authorization": f"Bearer {pf_token}"},
                          json={"method": "GET", "url": "http://127.0.0.1:9/api/"})
    assert blocked.status_code == 403
    assert "Blocked target" in blocked.json()["detail"]

    # no token at all → 401
    assert client.post("/apps/pf-app/api/platform/http",
                       json={"method": "GET", "url": "https://api.example.com/"}).status_code == 401


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

def test_endpoints_404_when_disabled(client):
    from app.config import get_settings

    get_settings().enable_apps = False
    assert client.get("/api/admin/apps").status_code == 404
    assert client.get("/apps/anything/index.html").status_code == 404
    assert client.get("/a/anything").status_code == 404


def test_owner_token_requires_manage_for_read_write_app(apps_env, client):
    """A read_write app's minted key carries MANAGE; a READ-only caller must
    not be able to mint an owner token and borrow that key for writes."""
    from app.models import APIKeyPermission
    from app.services.auth_service import AuthResult, require_read_permission
    from app.main import app as fastapi_app

    manifest = make_manifest(id="rw-app")
    manifest["cortex"]["keyScope"] = "read_write"
    client.post("/api/admin/apps/install",
                files={"file": ("rw.zip", make_zip(manifest), "application/zip")})
    client.post("/api/admin/apps/install",
                files={"file": ("ro.zip", make_zip(), "application/zip")})  # read scope

    # Override the read-permission dependency with a READ-only (non-admin) caller.
    read_only = AuthResult(is_authenticated=True, is_admin=False,
                           permissions=[APIKeyPermission.READ], key_id="ro-key")
    fastapi_app.dependency_overrides[require_read_permission] = lambda: read_only
    try:
        assert client.post("/api/apps/rw-app/token").status_code == 403
        assert client.post("/api/apps/test-app/token").status_code == 200  # read app OK
    finally:
        # restore the conftest admin override
        fastapi_app.dependency_overrides[require_read_permission] = lambda: AuthResult(
            is_authenticated=True, is_admin=True,
            permissions=[APIKeyPermission.READ, APIKeyPermission.MANAGE], key_id="test-admin",
        )


def test_admin_install_serve_and_proxy_gates(apps_env, client):
    # install via multipart
    resp = client.post(
        "/api/admin/apps/install",
        files={"file": ("test-app-1.0.0.zip", make_zip(), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "test-app"
    assert client.get("/api/admin/apps").json()[0]["id"] == "test-app"

    # static serving with CSP, cookie-less
    page = client.get("/apps/test-app/")
    assert page.status_code == 200
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert "doctype" in page.text

    # proxy: no token → 401; valid token but non-allowlisted path → 403
    assert client.post("/apps/test-app/api/cortex/search", json={}).status_code == 401
    token = client.post("/api/apps/test-app/token").json()["token"]
    denied = client.post(
        "/apps/test-app/api/cortex/admin/apps",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert denied.status_code == 403

    # invalid manifest rejected with the full issue list
    bad = make_zip(make_manifest(id="Bad_Id", type="service"))
    resp = client.post(
        "/api/admin/apps/install", files={"file": ("bad.zip", bad, "application/zip")}
    )
    assert resp.status_code == 400
    assert len(resp.json()["detail"]["issues"]) >= 2


def test_share_shell_and_grant_exchange(apps_env, client):
    client.post(
        "/api/admin/apps/install",
        files={"file": ("t.zip", make_zip(), "application/zip")},
    )
    grant = client.post(
        "/api/admin/apps/test-app/grants", json={"label": "team", "role": "viewer"}
    ).json()
    assert grant["share_path"].startswith("/a/test-app?g=cag_")

    shell = client.get("/a/test-app")
    assert shell.status_code == 200
    assert 'sandbox="allow-scripts' in shell.text
    assert "allow-same-origin" not in shell.text  # opaque origin is the point

    grant_token = grant["share_path"].split("g=")[1]
    exchange = client.post(
        "/api/apps/test-app/grant-exchange", json={"grant": grant_token}
    )
    assert exchange.status_code == 200
    assert exchange.json()["token"].startswith("cat_")

    assert client.post(
        "/api/apps/test-app/grant-exchange", json={"grant": "cag_forged.sig"}
    ).status_code == 403

    # shell 404s for apps that don't allow sharing
    manifest = make_manifest(id="private-app", sharing={"links": False})
    client.post(
        "/api/admin/apps/install", files={"file": ("p.zip", make_zip(manifest), "application/zip")}
    )
    assert client.get("/a/private-app").status_code == 404


def test_share_shell_escapes_manifest_xss(apps_env, client):
    # name and entry are third-party manifest values — must be HTML-escaped
    # in the share shell (whose CSP permits inline script).
    evil_entry = 'x"><script>alert(1)</script>.html'
    manifest = make_manifest(
        id="xss-app",
        name="</title><script>alert(document.domain)</script>",
        entry=evil_entry,
    )
    # entry must resolve to a real file in dist/, so ship it under that name
    client.post(
        "/api/admin/apps/install",
        files={"file": ("x.zip", make_zip(manifest, extra_files={f"dist/{evil_entry}": "x"}),
                        "application/zip")},
    )
    shell = client.get("/a/xss-app")
    assert shell.status_code == 200
    assert "<script>alert(document.domain)</script>" not in shell.text
    assert '"><script>alert(1)</script>' not in shell.text
    assert "&lt;script&gt;" in shell.text  # escaped form present


def test_grant_exchange_rate_limited(apps_env, client):
    import app.main as main_module

    client.post(
        "/api/admin/apps/install",
        files={"file": ("t.zip", make_zip(), "application/zip")},
    )
    main_module._grant_exchange_hits.clear()
    try:
        codes = [
            client.post(
                "/api/apps/test-app/grant-exchange", json={"grant": "cag_bogus.sig"}
            ).status_code
            for _ in range(main_module._GRANT_EXCHANGE_LIMIT + 5)
        ]
        assert codes[0] == 403  # invalid grant, but allowed through the limiter
        assert 429 in codes
        assert all(c == 429 for c in codes[main_module._GRANT_EXCHANGE_LIMIT:])
    finally:
        main_module._grant_exchange_hits.clear()  # don't poison other tests


def test_auth_header_base64_transform_and_cross_var_substitution(apps_env):
    """Basic-auth apps declare `Authorization: Basic base64(USER:PASSWORD)` and
    take plain credentials as config — users never hand-encode base64 (the
    manual-encode step produced corrupted values in the field)."""
    manifest = make_manifest(
        id="basic-auth-app",
        type="platform",
        capabilities={"http": {"hosts": ["nc.example"]}},
        config=[
            {"name": "NC_USER", "type": "text", "required": True},
            {
                "name": "NC_APP_PASSWORD",
                "type": "secret",
                "required": True,
                "auth_header": "Authorization: Basic base64(NC_USER:NC_APP_PASSWORD)",
            },
        ],
    )
    apps_env.install_from_zip(make_zip(manifest))
    apps_env.save_config(
        "basic-auth-app",
        {"NC_USER": "rene", "NC_APP_PASSWORD": "abcde-12345-fghij-67890-klmno"},
    )
    headers = apps_env.platform_auth_headers("basic-auth-app", target_host="nc.example")
    import base64 as b64

    expected = b64.b64encode(b"rene:abcde-12345-fghij-67890-klmno").decode()
    assert headers["Authorization"] == f"Basic {expected}"


def test_orphaned_config_keys_never_surface_after_upgrade(apps_env):
    """Upgrades preserve config.json — a var that was secret-typed in the OLD
    manifest must not reappear (unmasked or at all) once the new manifest
    stops declaring it. Found live: nextcloud-sync renamed its secret var and
    the stale secret came back as plaintext in both admin get_config and the
    app-facing public_config."""
    v1 = make_manifest(
        id="upgrade-app",
        type="platform",
        capabilities={"http": {"hosts": ["svc.example"]}},
        config=[
            {"name": "SVC_URL", "type": "text", "required": True},
            {"name": "OLD_SECRET", "type": "secret",
             "auth_header": "Authorization: Token OLD_SECRET"},
        ],
    )
    apps_env.install_from_zip(make_zip(v1))
    apps_env.save_config(
        "upgrade-app", {"SVC_URL": "https://svc.example", "OLD_SECRET": "hunter2"}
    )

    v2 = dict(v1)
    v2["version"] = "1.1.0"
    v2["config"] = [
        {"name": "SVC_URL", "type": "text", "required": True},
        {"name": "NEW_SECRET", "type": "secret",
         "auth_header": "Authorization: Token NEW_SECRET"},
    ]
    apps_env.install_from_zip(make_zip(v2))

    admin_values = apps_env.get_config("upgrade-app")["values"]
    assert "OLD_SECRET" not in admin_values
    assert admin_values["SVC_URL"] == "https://svc.example"

    public = apps_env.public_config("upgrade-app")
    assert "OLD_SECRET" not in public
    assert public == {"SVC_URL": "https://svc.example"}


def test_google_sa_token_transform(apps_env, monkeypatch):
    """auth_header google_sa_token(VAR, scopes): server-side RS256 JWT mint +
    exchange, cached until near-expiry, token_uri pinned to Google's endpoint
    (a hostile key JSON must not steer the platform elsewhere)."""
    import asyncio
    import base64 as b64
    import json as jsonlib

    import httpx
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    sa_json = jsonlib.dumps({
        "type": "service_account",
        "client_email": "sync@proj.iam.gserviceaccount.com",
        "private_key": pem,
        "token_uri": "https://oauth2.googleapis.com/token",
    })

    manifest = make_manifest(
        id="gdrive-app",
        type="platform",
        capabilities={"http": {"hosts": ["www.googleapis.com"]}},
        config=[
            {"name": "GDRIVE_SA_KEY", "type": "secret",
             "auth_header": "Authorization: Bearer google_sa_token(GDRIVE_SA_KEY, https://www.googleapis.com/auth/drive.readonly)"},
        ],
    )
    apps_env.install_from_zip(make_zip(manifest))
    apps_env.save_config("gdrive-app", {"GDRIVE_SA_KEY": sa_json})

    exchanges = []

    async def fake_post(self, url, data=None, **kwargs):
        exchanges.append((str(url), data))
        # decode the JWT claims to prove structure
        assertion = data["assertion"]
        payload = assertion.split(".")[1]
        claims = jsonlib.loads(b64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        assert claims["iss"] == "sync@proj.iam.gserviceaccount.com"
        assert claims["scope"] == "https://www.googleapis.com/auth/drive.readonly"
        assert claims["aud"] == "https://oauth2.googleapis.com/token"
        return httpx.Response(200, json={"access_token": "ya29.MINTED", "expires_in": 3600})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    header = apps_env.platform_auth_headers("gdrive-app", target_host="www.googleapis.com")["Authorization"]
    # the rendered header still carries the expression (var name protected
    # from substitution — the raw key JSON must never inline into a header)
    assert "google_sa_token(GDRIVE_SA_KEY" in header and pem not in header

    resolved = asyncio.run(apps_env.resolve_google_sa_tokens("gdrive-app", header))
    assert resolved == "Bearer ya29.MINTED"
    resolved2 = asyncio.run(apps_env.resolve_google_sa_tokens("gdrive-app", header))
    assert resolved2 == "Bearer ya29.MINTED"
    assert len(exchanges) == 1  # cached — one exchange for two resolutions

    # pinned token_uri: a key steering elsewhere is refused
    evil = jsonlib.loads(sa_json)
    evil["token_uri"] = "https://attacker.example/token"
    apps_env.save_config("gdrive-app", {"GDRIVE_SA_KEY": jsonlib.dumps(evil)})
    apps_env._sa_token_cache.clear()
    import pytest as _pytest
    with _pytest.raises(ValueError, match="token_uri"):
        asyncio.run(apps_env.resolve_google_sa_tokens("gdrive-app", header))


def test_ms_graph_token_transform(apps_env, monkeypatch):
    """auth_header ms_graph_token(TENANT, CLIENT, SECRET): app-only client-
    credentials mint against the pinned Microsoft endpoint, tenant segment
    sanitized, cached — the Sites.Selected SharePoint model."""
    import asyncio

    import httpx

    manifest = make_manifest(
        id="sp-app",
        type="platform",
        capabilities={"http": {"hosts": ["graph.microsoft.com"]}},
        config=[
            {"name": "ENTRA_TENANT_ID", "type": "text", "required": True},
            {"name": "ENTRA_CLIENT_ID", "type": "text", "required": True},
            {"name": "ENTRA_CLIENT_SECRET", "type": "secret",
             "auth_header": "Authorization: Bearer ms_graph_token(ENTRA_TENANT_ID, ENTRA_CLIENT_ID, ENTRA_CLIENT_SECRET)",
             "auth_host": "graph.microsoft.com"},
        ],
    )
    apps_env.install_from_zip(make_zip(manifest))
    apps_env.save_config("sp-app", {
        "ENTRA_TENANT_ID": "qwellcode.onmicrosoft.com",
        "ENTRA_CLIENT_ID": "client-guid",
        "ENTRA_CLIENT_SECRET": "s3cret~value",
    })

    exchanges = []

    async def fake_post(self, url, data=None, **kwargs):
        exchanges.append(str(url))
        assert str(url) == ("https://login.microsoftonline.com/"
                             "qwellcode.onmicrosoft.com/oauth2/v2.0/token")
        assert data["client_id"] == "client-guid"
        assert data["client_secret"] == "s3cret~value"
        assert data["grant_type"] == "client_credentials"
        assert data["scope"] == "https://graph.microsoft.com/.default"
        return httpx.Response(200, json={"access_token": "eyJ.MINTED", "expires_in": 3599},
                               headers={"content-type": "application/json"})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    header = apps_env.platform_auth_headers("sp-app", target_host="graph.microsoft.com")["Authorization"]
    assert "ms_graph_token(" in header and "s3cret~value" not in header

    resolved = asyncio.run(apps_env.resolve_ms_graph_tokens("sp-app", header))
    assert resolved == "Bearer eyJ.MINTED"
    asyncio.run(apps_env.resolve_ms_graph_tokens("sp-app", header))
    assert len(exchanges) == 1  # cached

    # auth_host scoping: nothing injected on the login host itself
    assert "Authorization" not in apps_env.platform_auth_headers(
        "sp-app", target_host="login.microsoftonline.com")

    # hostile tenant value must not steer the URL path
    import pytest as _pytest
    apps_env.save_config("sp-app", {"ENTRA_TENANT_ID": "evil.example/..%2f"})
    apps_env._sa_token_cache.clear()
    with _pytest.raises(ValueError, match="tenant"):
        asyncio.run(apps_env.resolve_ms_graph_tokens("sp-app", header))
