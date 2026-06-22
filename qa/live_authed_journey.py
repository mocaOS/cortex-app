#!/usr/bin/env python3
"""Authenticated end-to-end journey against a REAL Neo4j (self-provisioned).

Runs the live app (FastAPI TestClient, no mocks, no auth bypass) against an
ephemeral Neo4j with a known admin key — so we exercise the genuine
app -> Neo4j authenticated path without ever touching production credentials.

Env (set before importing the app):
  NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD -> the ephemeral test DB
  ADMIN_API_KEY                            -> a key we control
  OPENAI_API_KEY / OPENAI_API_BASE         -> dummy so the embedder constructs
                                              (never called in this journey)

Exits 0 if every journey step passes, 1 otherwise.
"""
import os
import sys

os.environ.update({
    "NEO4J_URI": "bolt://localhost:7688",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "testpassword123",
    "ADMIN_API_KEY": "qa-e2e-admin-key-controlled",
    "ENVIRONMENT": "development",
    "ENABLE_COLLECTIONS": "true",
    "ENABLE_GRAPH_EXTRACTION": "false",
    "USE_OPENAI_EMBEDDINGS": "true",
    "OPENAI_API_KEY": "dummy-not-called",
    "OPENAI_API_BASE": "http://127.0.0.1:9",
    "RATE_LIMIT_QPM": "0",
    "MAX_QUERIES_PER_MONTH": "0",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

KEY = os.environ["ADMIN_API_KEY"]
H = {"X-API-Key": KEY}
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


with TestClient(app) as c:
    print("== Auth boundary ==")
    check("no key -> 401", c.get("/api/stats").status_code == 401)
    check("bad key -> 401", c.get("/api/stats", headers={"X-API-Key": "wrong"}).status_code == 401)

    print("== Stats (authed, real Neo4j) ==")
    r = c.get("/api/stats", headers=H)
    check("GET /api/stats -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code == 200:
        check("stats has document_count", "document_count" in r.json())

    print("== Collections CRUD round-trip ==")
    r = c.get("/api/collections", headers=H)
    check("GET /api/collections -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")

    r = c.post("/api/collections", headers=H, json={"name": "QA E2E Coll", "description": "temp"})
    check("POST create collection -> 2xx", r.status_code in (200, 201), f"got {r.status_code}: {r.text[:200]}")
    cid = r.json().get("id") if r.status_code in (200, 201) else None

    if cid:
        r = c.get(f"/api/collections/{cid}", headers=H)
        check("GET created collection -> 200", r.status_code == 200 and r.json().get("name") == "QA E2E Coll")

        r = c.put(f"/api/collections/{cid}", headers=H, json={"name": "QA E2E Renamed"})
        check("PUT rename -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")

        r = c.get("/api/collections", headers=H)
        names = [x.get("name") for x in r.json().get("collections", [])]
        check("rename reflected in list", "QA E2E Renamed" in names, str(names))

        r = c.delete(f"/api/collections/{cid}", headers=H)
        check("DELETE collection -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")

        r = c.get(f"/api/collections/{cid}", headers=H)
        check("GET deleted -> 404", r.status_code == 404, f"got {r.status_code}")

    print("== Read journeys (authed, real Neo4j) ==")
    r = c.get("/api/documents", headers=H)
    check("GET /api/documents -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    r = c.get("/api/graph/entities", headers=H)
    check("GET /api/graph/entities -> 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    # default collection cannot be deleted
    r = c.delete("/api/collections/default", headers=H)
    check("DELETE default collection -> 400", r.status_code == 400, f"got {r.status_code}")

passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print(f"\n=== {passed}/{total} authenticated journey steps passed ===")
sys.exit(0 if passed == total else 1)
