"""Authenticated live end-to-end journeys against a running deployment.

Runs real authenticated requests (reads + a self-cleaning temp collection +
real hybrid search) against the deployed backend. The API key is read from the
environment (CORTEX_E2E_API_KEY) and NEVER hard-coded, so the secret stays out
of the repo; the whole module SKIPS when the key or backend is absent.

  CORTEX_E2E_BASE      backend base URL (default http://localhost:8000)
  CORTEX_E2E_API_KEY   admin/manage API key (required; module skips if unset)

Non-streaming /api/ask is intentionally NOT asserted to return an answer: it is
bounded by ASK_DEADLINE_SECONDS and is expected to 504 under a slow LLM (the
streaming endpoint is the real chat journey). Only non-destructive writes are
performed (a uniquely-named temp collection that is deleted in-test).
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE = os.environ.get("CORTEX_E2E_BASE", "http://localhost:8000")
KEY = os.environ.get("CORTEX_E2E_API_KEY", "")


def _reachable() -> bool:
    try:
        httpx.get(BASE + "/health", timeout=2.0)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not KEY or not _reachable(),
    reason="set CORTEX_E2E_API_KEY and run a live backend to exercise authed E2E",
)


@pytest.fixture
def client():
    return httpx.Client(base_url=BASE, headers={"X-API-Key": KEY}, timeout=30.0)


@pytest.mark.parametrize(
    "path",
    [
        "/api/stats",
        "/api/collections",
        "/api/documents",
        "/api/graph/entities?limit=5",
        "/api/graph/relationships?limit=5",
        "/api/graph/communities?limit=5",
        "/api/admin/config",
        "/api/admin/api-keys",
    ],
)
def test_authed_reads_ok(client, path):
    assert client.get(path).status_code == 200


def test_stats_shape(client):
    body = client.get("/api/stats").json()
    for k in ("document_count", "entity_count", "relationship_count", "collection_count"):
        assert k in body and isinstance(body[k], int)


def test_collections_crud_round_trip(client):
    created = client.post(
        "/api/collections",
        json={"name": "__qa_e2e_temp__", "description": "QA temp; auto-deleted"},
    )
    assert created.status_code in (200, 201)
    cid = created.json()["id"]
    try:
        assert client.get(f"/api/collections/{cid}").status_code == 200
        assert client.put(f"/api/collections/{cid}", json={"name": "__qa_e2e_temp2__"}).status_code == 200
    finally:
        assert client.delete(f"/api/collections/{cid}").status_code == 200
    assert client.get(f"/api/collections/{cid}").status_code == 404


def test_cannot_delete_default_collection(client):
    assert client.delete("/api/collections/default").status_code == 400


def test_hybrid_search_returns_results(client):
    r = client.post("/api/search", json={"query": "knowledge graph", "top_k": 3}, timeout=30.0)
    assert r.status_code == 200
    assert "results" in r.json()


def test_ask_stream_fast_path_streams_content(client):
    """The real chat journey: fast-search SSE must stream content + a done frame."""
    import json

    content_len, got_done, err = 0, False, None
    with client.stream(
        "POST",
        "/api/ask/stream",
        json={"question": "What is one topic covered here?", "top_k": 3,
              "use_graph": False, "use_fast_search": True, "use_agentic": False},
        timeout=180.0,
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            line = line.strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            try:
                d = json.loads(line[5:].strip())
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            content_len += len(str(d.get("content", "")))
            got_done = got_done or bool(d.get("done"))
            err = err or d.get("error")
            if got_done:
                break
    assert err is None
    assert content_len > 0 and got_done


def test_document_ingestion_extraction_journey(client):
    """Full ingestion pipeline E2E: upload -> chunk/embed/extract -> searchable
    -> self-cleaning delete. Scoped to a throwaway collection + a uniquely-named
    tiny doc; runs against the live LLM/embeddings. Cleans up in finally so the
    live graph is left untouched (orphaned entities removed on doc delete)."""
    import time

    uniq = "Ztqxcorp"
    body = (
        f"{uniq} Industries is a fictional company founded by Wfbnldr Vexel in "
        f"Qplmzar. {uniq} Industries builds the Hgttrn Protocol."
    ).encode("utf-8")

    coll = client.post("/api/collections", json={"name": "__qa_e2e_ingest__", "description": "QA"})
    assert coll.status_code in (200, 201)
    cid = coll.json()["id"]
    doc_id = None
    try:
        up = client.post(
            "/api/upload",
            params={"collection_id": cid, "start_processing": "true", "source": "qa_e2e"},
            files={"file": ("qa_e2e_doc.txt", body, "text/plain")},
        )
        assert up.status_code in (200, 201), up.text[:200]
        doc_id = up.json()["document_id"]

        deadline = time.time() + 180
        status = None
        while time.time() < deadline:
            d = client.get(f"/api/documents/{doc_id}")
            if d.status_code == 200:
                status = (d.json().get("processing_status") or "").lower()
                if status in ("completed", "failed"):
                    break
            time.sleep(3)
        assert status == "completed", f"ingestion did not complete: {status}"

        content = client.get(f"/api/documents/{doc_id}/content")
        assert content.status_code == 200 and uniq.lower() in content.text.lower()
    finally:
        if doc_id:
            assert client.delete(f"/api/documents/{doc_id}").status_code == 200
            assert client.get(f"/api/documents/{doc_id}").status_code == 404
        assert client.delete(f"/api/collections/{cid}").status_code == 200
