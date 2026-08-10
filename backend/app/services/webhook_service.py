"""Outbound webhooks — push ingestion/task lifecycle events to subscribers.

Kills the polling loop every API consumer had to build ("upload, then GET the
document every 3s"): register an endpoint once, get an HMAC-signed POST when
the work finishes.

Design:
- Master switch `ENABLE_WEBHOOKS` (env, default off) — emit_event is a no-op
  without it, so the feature costs nothing when unused.
- Endpoints are admin-managed (`/api/admin/webhooks`), persisted as Neo4j
  `WebhookEndpoint` nodes; the signing secret is generated server-side, shown
  ONCE at creation, and stored encrypted (crypto_service, like git PATs).
- Delivery is fire-and-forget on a small dedicated thread pool — emit points
  live inside pipeline threads and the event loop; neither may block on a
  subscriber's HTTP server. Bounded retries with backoff, 5s timeout per
  attempt, per-endpoint delivery state recorded best-effort.
- Signature (Stripe-style, replay-resistant):
      X-Cortex-Signature: t=<unix>,v1=hex(hmac_sha256(secret, f"{t}.{body}"))
  plus `X-Cortex-Event` (type) and `X-Cortex-Delivery` (uuid) headers.

Events: document.processed, document.failed, task.completed, task.failed,
webhook.test. An endpoint with an empty `events` list receives everything.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

EVENT_TYPES = {
    "document.processed",
    "document.failed",
    "task.completed",
    "task.failed",
    "webhook.test",
}

_DELIVERY_TIMEOUT_S = 5.0
_RETRY_DELAYS_S = (0.0, 1.0, 5.0)  # 3 attempts total

_delivery_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="webhook")

# Small TTL cache over the endpoint list — emit points fire per document/task
# and must not pay a Neo4j query each time. Invalidated on every CRUD.
_cache_lock = threading.Lock()
_cached_endpoints: Optional[List[dict]] = None
_cache_expires: float = 0.0
_CACHE_TTL_S = 15.0


def webhooks_enabled() -> bool:
    from app.config import get_settings

    return bool(getattr(get_settings(), "enable_webhooks", False))


def invalidate_webhook_cache() -> None:
    global _cached_endpoints, _cache_expires
    with _cache_lock:
        _cached_endpoints = None
        _cache_expires = 0.0


def _get_endpoints() -> List[dict]:
    global _cached_endpoints, _cache_expires
    with _cache_lock:
        if _cached_endpoints is not None and time.monotonic() < _cache_expires:
            return _cached_endpoints
    try:
        from app.services.neo4j_service import get_neo4j_service

        endpoints = get_neo4j_service().list_webhook_endpoints()
    except Exception as e:  # noqa: BLE001 — webhooks must never break the pipeline
        logger.warning(f"Webhook endpoint lookup failed: {e}")
        return []
    with _cache_lock:
        _cached_endpoints = endpoints
        _cache_expires = time.monotonic() + _CACHE_TTL_S
    return endpoints


def generate_secret() -> str:
    return f"whsec_{secrets.token_hex(24)}"


def sign_payload(secret: str, timestamp: int, body: str) -> str:
    digest = hmac.new(
        secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def build_event_body(event_type: str, data: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "id": f"evt_{uuid.uuid4().hex}",
            "event": event_type,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data": data,
        }
    )


def emit_event(event_type: str, data: Dict[str, Any]) -> None:
    """Fire-and-forget: schedule delivery to every subscribed endpoint.

    Safe to call from the event loop or pipeline threads; never raises, never
    blocks on network. No-op while ENABLE_WEBHOOKS is off or nothing is
    subscribed to the event.
    """
    try:
        if not webhooks_enabled():
            return
        endpoints = [
            ep
            for ep in _get_endpoints()
            if ep.get("active", True)
            and (not ep.get("events") or event_type in ep.get("events", []))
        ]
        if not endpoints:
            return
        body = build_event_body(event_type, data)
        for ep in endpoints:
            _delivery_executor.submit(_deliver_with_retries, ep, event_type, body)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Webhook emit failed for {event_type}: {e}")


def _decrypt_secret(ep: dict) -> Optional[str]:
    try:
        from app.services.crypto_service import get_crypto_service

        return get_crypto_service().decrypt(ep.get("secret"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Webhook secret decrypt failed for {ep.get('id')}: {e}")
        return None


def _post_once(ep: dict, event_type: str, body: str, secret: str) -> httpx.Response:
    timestamp = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "cortex-webhooks/1.0",
        "X-Cortex-Event": event_type,
        "X-Cortex-Delivery": uuid.uuid4().hex,
        "X-Cortex-Signature": sign_payload(secret, timestamp, body),
    }
    with httpx.Client(timeout=_DELIVERY_TIMEOUT_S, follow_redirects=False) as client:
        return client.post(ep["url"], content=body, headers=headers)


def _deliver_with_retries(ep: dict, event_type: str, body: str) -> None:
    secret = _decrypt_secret(ep)
    if not secret:
        return
    last_status: Optional[int] = None
    for delay in _RETRY_DELAYS_S:
        if delay:
            time.sleep(delay)
        try:
            resp = _post_once(ep, event_type, body, secret)
            last_status = resp.status_code
            if 200 <= resp.status_code < 300:
                _record_delivery(ep, ok=True, status=resp.status_code)
                return
            # 4xx won't improve on retry — record and stop (except 408/429)
            if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429):
                break
        except httpx.HTTPError as e:
            logger.debug(f"Webhook delivery attempt to {ep.get('url')} failed: {e}")
            last_status = None
    logger.warning(
        f"Webhook delivery failed for endpoint {ep.get('id')} "
        f"({event_type}, last status: {last_status})"
    )
    _record_delivery(ep, ok=False, status=last_status)


def _record_delivery(ep: dict, ok: bool, status: Optional[int]) -> None:
    try:
        from app.services.neo4j_service import get_neo4j_service

        get_neo4j_service().record_webhook_delivery(ep["id"], ok=ok, status=status)
    except Exception:  # noqa: BLE001 — bookkeeping must never matter
        pass


def deliver_test(ep: dict) -> Dict[str, Any]:
    """Synchronous single-attempt test delivery (admin 'Send test event')."""
    secret = _decrypt_secret(ep)
    if not secret:
        return {"ok": False, "error": "secret_unavailable"}
    body = build_event_body(
        "webhook.test", {"message": "Cortex webhook test delivery"}
    )
    try:
        resp = _post_once(ep, "webhook.test", body, secret)
        ok = 200 <= resp.status_code < 300
        _record_delivery(ep, ok=ok, status=resp.status_code)
        return {"ok": ok, "status_code": resp.status_code}
    except httpx.HTTPError as e:
        _record_delivery(ep, ok=False, status=None)
        return {"ok": False, "error": str(e)[:200]}
