"""Live end-to-end journey checks against a running deployment.

These run real HTTP requests against the deployed backend (and frontend) — the
genuine end-to-end journeys that mocked tests can only approximate. They are
SKIPPED automatically when no stack is reachable, so the offline suite is
unaffected; set CORTEX_E2E_BASE to point at a non-default backend.

Covered (no credentials required): public health, the auth boundary (protected
endpoints reject anonymous callers), the admin-only metrics gate, and the
frontend's unauthenticated redirect to /login. Authenticated journeys (real
Neo4j reads/writes, search, RAG) require an API key and are intentionally not
attempted here.
"""

from __future__ import annotations

import os

import httpx
import pytest

BACKEND = os.environ.get("CORTEX_E2E_BASE", "http://localhost:8000")
FRONTEND = os.environ.get("CORTEX_E2E_FRONTEND", "http://localhost:3000")


def _reachable(url: str) -> bool:
    try:
        httpx.get(url + "/health", timeout=2.0)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(BACKEND), reason="no live Cortex backend reachable for E2E"
)


def test_health_live_ok():
    r = httpx.get(f"{BACKEND}/health", timeout=5.0)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("healthy", "degraded")
    assert "neo4j_connected" in body


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/stats"),
        ("GET", "/api/documents"),
        ("GET", "/api/collections"),
        ("GET", "/api/graph/entities"),
        ("POST", "/api/search"),
    ],
)
def test_protected_endpoints_reject_anonymous(method, path):
    r = httpx.request(method, f"{BACKEND}{path}", json={"query": "x"}, timeout=5.0)
    assert r.status_code == 401


def test_metrics_requires_admin():
    assert httpx.get(f"{BACKEND}/metrics", timeout=5.0).status_code == 401


def test_frontend_redirects_anonymous_to_login():
    try:
        root = httpx.get(f"{FRONTEND}/", timeout=5.0, follow_redirects=False)
    except Exception:
        pytest.skip("frontend not reachable")
    assert root.status_code in (301, 302, 307, 308)
    login = httpx.get(f"{FRONTEND}/login", timeout=5.0)
    assert login.status_code == 200
