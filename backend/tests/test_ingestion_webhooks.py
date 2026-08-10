"""Ingestion queue status endpoint + outbound webhooks.

Covers GET /api/ingestion/status aggregation and the webhook service:
signing, event envelope, gating, delivery retries, and the admin CRUD
surface (secret shown once, never listed).
"""

import json
from unittest.mock import MagicMock

import httpx
import pytest

from app.services import webhook_service


_DOCS = [
    {"id": "d1", "filename": "a.pdf", "collection_id": "c1",
     "processing_status": "completed"},
    {"id": "d2", "filename": "b.pdf", "collection_id": "c1",
     "processing_status": "pending"},
    {"id": "d3", "filename": "c.pdf", "collection_id": "c1",
     "processing_status": "processing", "processing_queued": True,
     "progress_current": 0, "progress_total": 0, "progress_message": "Queued"},
    {"id": "d4", "filename": "d.pdf", "collection_id": "c1",
     "processing_status": "processing", "progress_current": 3,
     "progress_total": 10, "progress_message": "Extracting"},
    {"id": "d5", "filename": "e.pdf", "collection_id": "c1",
     "processing_status": "failed"},
]


# ---------------------------------------------------------------------------
# GET /api/ingestion/status
# ---------------------------------------------------------------------------

class TestIngestionStatus:
    def test_counts_active_and_backlog(self, client, mock_neo4j, monkeypatch):
        mock_neo4j.get_all_documents.return_value = list(_DOCS)
        monkeypatch.setattr(
            "app.services.document_processor.get_active_processing_ids",
            lambda: ["d4"],
        )
        r = client.get("/api/ingestion/status")
        assert r.status_code == 200
        body = r.json()
        assert body["counts"] == {
            "completed": 1, "pending": 1, "queued": 1,
            "processing": 1, "failed": 1,
        }
        assert body["backlog"] == 3          # pending + queued + processing
        assert body["idle"] is False
        assert body["total_documents"] == 5
        active = {a["id"]: a for a in body["active"]}
        assert set(active) == {"d3", "d4"}
        assert active["d3"]["queued"] is True and active["d3"]["live"] is False
        assert active["d4"]["live"] is True
        assert active["d4"]["progress_current"] == 3

    def test_idle_when_everything_done(self, client, mock_neo4j, monkeypatch):
        mock_neo4j.get_all_documents.return_value = [
            {"id": "d1", "processing_status": "completed", "collection_id": "c1"},
        ]
        monkeypatch.setattr(
            "app.services.document_processor.get_active_processing_ids",
            lambda: [],
        )
        body = client.get("/api/ingestion/status").json()
        assert body["idle"] is True and body["backlog"] == 0
        assert body["active"] == []


# ---------------------------------------------------------------------------
# webhook_service primitives
# ---------------------------------------------------------------------------

class TestWebhookPrimitives:
    def test_signature_is_deterministic_and_hmac_sha256(self):
        sig = webhook_service.sign_payload("whsec_abc", 1700000000, '{"a":1}')
        assert sig.startswith("t=1700000000,v1=")
        import hashlib
        import hmac as hmac_mod
        expected = hmac_mod.new(
            b"whsec_abc", b'1700000000.{"a":1}', hashlib.sha256
        ).hexdigest()
        assert sig == f"t=1700000000,v1={expected}"

    def test_event_body_envelope(self):
        body = json.loads(webhook_service.build_event_body(
            "document.processed", {"document_id": "d1"}
        ))
        assert body["event"] == "document.processed"
        assert body["data"] == {"document_id": "d1"}
        assert body["id"].startswith("evt_")
        assert "created_at" in body

    def test_generate_secret_prefix(self):
        s = webhook_service.generate_secret()
        assert s.startswith("whsec_") and len(s) > 20

    def test_emit_is_noop_when_disabled(self, _isolate_env, monkeypatch):
        _isolate_env.enable_webhooks = False
        called = []
        monkeypatch.setattr(
            webhook_service, "_get_endpoints",
            lambda: called.append(1) or [],
        )
        webhook_service.emit_event("document.processed", {"document_id": "x"})
        assert called == []  # never even looked up endpoints


# ---------------------------------------------------------------------------
# Delivery (retries, 4xx short-circuit)
# ---------------------------------------------------------------------------

def _endpoint(events=None):
    return {"id": "wh1", "url": "https://example.test/hook",
            "events": events or [], "active": True, "secret": "enc"}


class TestWebhookDelivery:
    @pytest.fixture(autouse=True)
    def _fast_retries(self, monkeypatch):
        monkeypatch.setattr(webhook_service, "_RETRY_DELAYS_S", (0.0, 0.0, 0.0))
        monkeypatch.setattr(webhook_service, "_decrypt_secret", lambda ep: "whsec_x")
        self.recorded = []
        monkeypatch.setattr(
            webhook_service, "_record_delivery",
            lambda ep, ok, status: self.recorded.append((ok, status)),
        )

    def test_retries_then_succeeds(self, monkeypatch):
        attempts = []

        def fake_post(ep, event_type, body, secret):
            attempts.append(1)
            if len(attempts) < 2:
                raise httpx.ConnectError("boom")
            return httpx.Response(200, request=httpx.Request("POST", ep["url"]))

        monkeypatch.setattr(webhook_service, "_post_once", fake_post)
        webhook_service._deliver_with_retries(_endpoint(), "task.completed", "{}")
        assert len(attempts) == 2
        assert self.recorded == [(True, 200)]

    def test_4xx_short_circuits(self, monkeypatch):
        attempts = []

        def fake_post(ep, event_type, body, secret):
            attempts.append(1)
            return httpx.Response(410, request=httpx.Request("POST", ep["url"]))

        monkeypatch.setattr(webhook_service, "_post_once", fake_post)
        webhook_service._deliver_with_retries(_endpoint(), "task.completed", "{}")
        assert len(attempts) == 1  # no retries on a permanent 4xx
        assert self.recorded == [(False, 410)]

    def test_all_attempts_fail(self, monkeypatch):
        def fake_post(ep, event_type, body, secret):
            raise httpx.ConnectTimeout("nope")

        monkeypatch.setattr(webhook_service, "_post_once", fake_post)
        webhook_service._deliver_with_retries(_endpoint(), "task.completed", "{}")
        assert self.recorded == [(False, None)]

    def test_emit_filters_by_event_subscription(self, _isolate_env, monkeypatch):
        _isolate_env.enable_webhooks = True
        submitted = []
        monkeypatch.setattr(
            webhook_service._delivery_executor, "submit",
            lambda fn, *a: submitted.append(a),
        )
        monkeypatch.setattr(webhook_service, "_get_endpoints", lambda: [
            _endpoint(events=["document.processed"]),
            _endpoint(events=["task.completed"]),
            _endpoint(events=[]),  # empty = everything
        ])
        webhook_service.emit_event("document.processed", {"document_id": "d1"})
        assert len(submitted) == 2


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

class TestWebhookAdminEndpoints:
    def test_403_when_disabled(self, client, _isolate_env):
        _isolate_env.enable_webhooks = False
        assert client.get("/api/admin/webhooks").status_code == 403
        assert client.post(
            "/api/admin/webhooks", json={"url": "https://x.test/h"}
        ).status_code == 403

    def test_create_returns_secret_once_and_list_never(
        self, client, mock_neo4j, _isolate_env
    ):
        _isolate_env.enable_webhooks = True
        mock_neo4j.create_webhook_endpoint.return_value = {
            "id": "wh1", "url": "https://x.test/h", "events": [],
            "description": "", "active": True, "created_at": "2026-08-10",
        }
        r = client.post("/api/admin/webhooks", json={"url": "https://x.test/h"})
        assert r.status_code == 200
        assert r.json()["secret"].startswith("whsec_")
        # stored encrypted, not plaintext
        stored = mock_neo4j.create_webhook_endpoint.call_args.args[-1]
        assert not stored.startswith("whsec_")

        mock_neo4j.list_webhook_endpoints.return_value = [{
            "id": "wh1", "url": "https://x.test/h", "events": [],
            "secret": "encrypted-blob", "active": True,
        }]
        listed = client.get("/api/admin/webhooks").json()
        assert "secret" not in listed["webhooks"][0]

    def test_create_rejects_unknown_event(self, client, mock_neo4j, _isolate_env):
        _isolate_env.enable_webhooks = True
        r = client.post(
            "/api/admin/webhooks",
            json={"url": "https://x.test/h", "events": ["document.exploded"]},
        )
        assert r.status_code == 400
        assert "Unknown event" in r.json()["detail"]

    def test_create_rejects_non_http_url(self, client, mock_neo4j, _isolate_env):
        _isolate_env.enable_webhooks = True
        r = client.post("/api/admin/webhooks", json={"url": "ftp://x.test/h"})
        assert r.status_code == 400

    def test_delete_404_for_unknown(self, client, mock_neo4j, _isolate_env):
        _isolate_env.enable_webhooks = True
        mock_neo4j.delete_webhook_endpoint.return_value = False
        assert client.delete("/api/admin/webhooks/nope").status_code == 404

    def test_test_endpoint_delivers(self, client, mock_neo4j, _isolate_env, monkeypatch):
        _isolate_env.enable_webhooks = True
        mock_neo4j.list_webhook_endpoints.return_value = [
            {"id": "wh1", "url": "https://x.test/h", "events": [],
             "active": True, "secret": "enc"},
        ]
        monkeypatch.setattr(
            "app.services.webhook_service.deliver_test",
            lambda ep: {"ok": True, "status_code": 204},
        )
        r = client.post("/api/admin/webhooks/wh1/test")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "status_code": 204}
