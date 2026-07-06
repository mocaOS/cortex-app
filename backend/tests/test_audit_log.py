"""Audit log (ENABLE_AUDIT_LOG): JSONL events, privacy, fail-open, off-by-default."""

import json

from app.config import get_settings
from app.services.audit_log import AuditLogger, audit


def _read_events(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TestAuditLogger:
    def test_disabled_by_default_writes_nothing(self, tmp_path, monkeypatch):
        settings = get_settings()
        path = tmp_path / "audit.log"
        monkeypatch.setattr(settings, "audit_log_path", str(path))
        audit("api.request", actor="key_1", method="POST", path="/api/upload")
        assert not path.exists()

    def test_enabled_writes_jsonl_event(self, tmp_path, monkeypatch):
        settings = get_settings()
        path = tmp_path / "audit.log"
        monkeypatch.setattr(settings, "enable_audit_log", True)
        monkeypatch.setattr(settings, "audit_log_path", str(path))
        logger = AuditLogger()
        logger.record(
            "api.request", actor="key_1", outcome="ok",
            method="DELETE", path="/api/documents/doc-1", status=200,
        )
        logger.close()
        events = _read_events(path)
        assert len(events) == 1
        evt = events[0]
        assert evt["event"] == "api.request"
        assert evt["actor"] == "key_1"
        assert evt["outcome"] == "ok"
        assert evt["meta"]["path"] == "/api/documents/doc-1"
        assert "ts" in evt and "request_id" in evt

    def test_write_failure_never_raises(self, monkeypatch):
        settings = get_settings()
        monkeypatch.setattr(settings, "enable_audit_log", True)
        monkeypatch.setattr(settings, "audit_log_path", "/proc/definitely/not/writable/audit.log")
        logger = AuditLogger()
        logger.record("api.request", actor="key_1")  # must not raise
        logger.close()


class TestMiddlewareIntegration:
    def test_mutating_request_is_audited(self, client, tmp_path, monkeypatch):
        settings = get_settings()
        path = tmp_path / "audit.log"
        monkeypatch.setattr(settings, "enable_audit_log", True)
        monkeypatch.setattr(settings, "audit_log_path", str(path))
        from app.services import audit_log as mod
        monkeypatch.setattr(mod, "_audit_logger", None)  # fresh singleton per test

        resp = client.post("/api/search", json={"query": "hello"})
        assert resp.status_code in (200, 500)
        mod.get_audit_logger().close()
        events = _read_events(path)
        assert any(
            e["event"] == "api.request" and e["meta"]["path"] == "/api/search"
            for e in events
        )
        # Privacy: the query text itself must never be recorded.
        assert "hello" not in path.read_text(encoding="utf-8")
