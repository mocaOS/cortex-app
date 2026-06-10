"""Tests for logging setup (plain/json + request IDs) and the /metrics gate."""

from __future__ import annotations

import json
import logging

from app.config import get_settings
from app.logging_setup import (
    JsonFormatter,
    RequestIdFilter,
    configure,
    get_request_id,
    new_request_id,
    set_request_id,
)


class TestLoggingSetup:
    def _make_record(self, msg="hello"):
        return logging.LogRecord(
            name="app.test", level=logging.INFO, pathname=__file__,
            lineno=1, msg=msg, args=(), exc_info=None,
        )

    def test_json_formatter_emits_request_id(self):
        set_request_id("req-abc")
        try:
            record = self._make_record()
            RequestIdFilter().filter(record)
            payload = json.loads(JsonFormatter().format(record))
            assert payload["msg"] == "hello"
            assert payload["level"] == "INFO"
            assert payload["logger"] == "app.test"
            assert payload["request_id"] == "req-abc"
        finally:
            set_request_id(None)

    def test_request_id_defaults_to_dash(self):
        set_request_id(None)
        record = self._make_record()
        RequestIdFilter().filter(record)
        assert record.request_id == "-"

    def test_plain_configure_keeps_legacy_format(self):
        configure("plain")
        root = logging.getLogger()
        fmt = root.handlers[0].formatter._fmt
        assert fmt == "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    def test_configure_idempotent(self):
        configure("json")
        configure("json")
        root = logging.getLogger()
        assert len(root.handlers) == 1
        configure("plain")  # restore

    def test_new_request_id_unique(self):
        assert new_request_id() != new_request_id()
        assert get_request_id() is None or isinstance(get_request_id(), str)


class TestRequestIdMiddleware:
    def test_response_echoes_request_id(self, client):
        r = client.get("/health", headers={"X-Request-ID": "trace-123"})
        assert r.headers.get("X-Request-ID") == "trace-123"

    def test_request_id_generated_when_absent(self, client):
        r = client.get("/health")
        assert len(r.headers.get("X-Request-ID", "")) == 16


class TestMetricsEndpoint:
    def test_metrics_disabled_returns_404(self, client):
        settings = get_settings()
        settings.metrics_enabled = False
        try:
            r = client.get("/metrics")
            assert r.status_code == 404
        finally:
            settings.metrics_enabled = True

    def test_metrics_enabled_serves_or_501(self, client):
        """With prometheus-client installed → 200 text payload; without → 501.
        Both are correct depending on the image."""
        from app import metrics

        r = client.get("/metrics")
        if metrics.AVAILABLE:
            assert r.status_code == 200
            assert "cortex_http_requests_total" in r.text
        else:
            assert r.status_code == 501
