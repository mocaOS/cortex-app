"""HTTP-layer journey/contract tests via the FastAPI TestClient.

These exercise the request -> validation -> response cycle for representative
endpoints: input-validation bounds (422), explicit bad-input (400), not-found
(404), and the public health route. Business logic is mocked (Neo4j/processors),
so these assert routing + contract, the closest executable proxy to end-to-end
API journeys without a live stack. Auth is bypassed by the `client` fixture.
"""

from __future__ import annotations

import pytest


def test_health_is_public_and_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("healthy", "degraded")
    assert "neo4j_connected" in body


# --- input validation (422), evaluated before business logic -----------------

@pytest.mark.parametrize(
    "payload",
    [
        {"query": "hi", "top_k": 0},     # below ge=1
        {"query": "hi", "top_k": 999},   # above le=50
        {"top_k": 5},                     # missing required query
    ],
)
def test_search_validation_422(client, payload):
    assert client.post("/api/search", json=payload).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "hi", "top_k": 99},   # above le=20
        {"question": "hi", "max_hops": 9}, # above le=3
        {"top_k": 5},                       # missing required question
    ],
)
def test_ask_validation_422(client, payload):
    assert client.post("/api/ask", json=payload).status_code == 422


@pytest.mark.parametrize("limit", [0, 99999])
def test_graph_entities_limit_bounds_422(client, limit):
    assert client.get(f"/api/graph/entities?limit={limit}").status_code == 422


def test_graph_search_requires_query_422(client):
    assert client.get("/api/graph/search").status_code == 422       # missing query
    assert client.get("/api/graph/search?query=").status_code == 422  # min_length 1


@pytest.mark.parametrize("hours", [0, 999])
def test_tasks_cleanup_bounds_422(client, hours):
    assert client.post(f"/api/tasks/cleanup?max_age_hours={hours}").status_code == 422


# --- explicit bad input (400) ------------------------------------------------

def test_download_zip_empty_list_400(client):
    assert client.post("/api/documents/download-zip", json={"document_ids": []}).status_code == 400


# --- not found (404) ---------------------------------------------------------

def test_unknown_task_404(client):
    assert client.get("/api/tasks/does-not-exist").status_code == 404
