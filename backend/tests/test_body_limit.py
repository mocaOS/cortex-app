"""Body-size limit middleware: per-route limit resolution + HTTP enforcement."""

from app.body_limit import _MULTIPART_SLACK_BYTES, _resolve_limit_bytes
from app.config import get_settings

MB = 1024 * 1024


class TestResolveLimit:
    def test_default_routes_get_global_cap(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "max_request_body_mb", 32)
        assert _resolve_limit_bytes("/api/search", settings) == 32 * MB
        assert _resolve_limit_bytes("/api/ask", settings) == 32 * MB

    def test_upload_routes_get_file_cap_plus_slack(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "max_file_size_mb", 50)
        expected = 50 * MB + _MULTIPART_SLACK_BYTES
        assert _resolve_limit_bytes("/api/upload", settings) == expected
        assert _resolve_limit_bytes("/api/documents/abc-123/reprocess", settings) == expected

    def test_import_routes_get_import_cap(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "max_import_body_mb", 2048)
        assert _resolve_limit_bytes("/api/admin/import", settings) == 2048 * MB
        assert _resolve_limit_bytes("/api/admin/import/upload/xyz/chunk", settings) == 2048 * MB

    def test_zero_disables(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "max_request_body_mb", 0)
        assert _resolve_limit_bytes("/api/search", settings) == 0
        monkeypatch.setattr(settings, "max_request_body_mb", 32)
        monkeypatch.setattr(settings, "max_file_size_mb", 0)
        assert _resolve_limit_bytes("/api/upload", settings) == 0
        monkeypatch.setattr(settings, "max_import_body_mb", 0)
        assert _resolve_limit_bytes("/api/admin/import", settings) == 0


class TestHttpEnforcement:
    def test_oversized_content_length_rejected(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "max_request_body_mb", 1)
        resp = client.post(
            "/api/search",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(2 * MB),
            },
        )
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()

    def test_oversized_streamed_body_rejected(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "max_request_body_mb", 1)
        body = b'{"query": "' + b"x" * (2 * MB) + b'"}'
        resp = client.post(
            "/api/search",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413

    def test_normal_body_passes_through(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "max_request_body_mb", 1)
        resp = client.get("/health")
        assert resp.status_code == 200
