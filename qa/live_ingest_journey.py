#!/usr/bin/env python3
"""End-to-end document INGESTION -> EXTRACTION journey against the live stack.

Scoped + self-cleaning: creates a throwaway collection, uploads ONE tiny doc
with deliberately unique entity names, waits for the real pipeline (Docling/raw
-> chunk -> embed -> entity extraction) to finish, verifies chunks + searchable
content, then deletes the doc (orphaned-entity cleanup) and the collection in a
finally block. Does NOT run community detection (would re-cluster the live graph).

Env: CORTEX_E2E_API_KEY (required), CORTEX_E2E_BASE (default http://localhost:8000)
Exit 0 if all journey steps pass, 1 otherwise.
"""
import os
import sys
import time

import httpx

BASE = os.environ.get("CORTEX_E2E_BASE", "http://localhost:8000")
KEY = os.environ.get("CORTEX_E2E_API_KEY", "")
if not KEY:
    print("SKIP: CORTEX_E2E_API_KEY not set")
    sys.exit(0)

H = {"X-API-Key": KEY}
c = httpx.Client(base_url=BASE, headers=H, timeout=30.0)
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# Unique tokens so extracted entities are unambiguous and cleanup is verifiable.
UNIQ = "Ztqxcorp"
DOC = (
    f"{UNIQ} Industries is a fictional company founded by Wfbnldr Vexel in the "
    f"city of Qplmzar. {UNIQ} Industries builds the Hgttrn Protocol, a system "
    f"used by Qplmzar for testing. Wfbnldr Vexel leads {UNIQ} Industries."
).encode("utf-8")

cid = None
doc_id = None
try:
    # 1. throwaway collection
    r = c.post("/api/collections", json={"name": "__qa_e2e_ingest__", "description": "QA ingest; auto-deleted"})
    check("create temp collection", r.status_code in (200, 201), f"{r.status_code}: {r.text[:160]}")
    cid = r.json().get("id") if r.status_code in (200, 201) else None

    # 2. upload + process
    if cid:
        r = c.post(
            "/api/upload",
            params={"collection_id": cid, "start_processing": "true", "source": "qa_e2e"},
            files={"file": ("qa_e2e_doc.txt", DOC, "text/plain")},
        )
        check("upload tiny doc (start_processing)", r.status_code in (200, 201), f"{r.status_code}: {r.text[:200]}")
        doc_id = r.json().get("document_id") if r.status_code in (200, 201) else None

    # 3. poll until processed
    final = None
    if doc_id:
        deadline = time.time() + 180
        last = None
        while time.time() < deadline:
            r = c.get(f"/api/documents/{doc_id}")
            if r.status_code == 200:
                last = r.json()
                st = (last.get("processing_status") or last.get("status") or "").lower()
                if st in ("completed", "failed", "error"):
                    final = last
                    break
            time.sleep(3)
        check("doc reached terminal status", final is not None,
              f"last status={ (last or {}).get('processing_status') }")
        if final:
            st = (final.get("processing_status") or final.get("status") or "").lower()
            check("doc processed (not failed)", st == "completed",
                  f"status={st} err={final.get('error')}")
            cc = final.get("chunk_count") or final.get("chunks") or 0
            check("doc produced chunks", (isinstance(cc, int) and cc > 0) or
                  bool(final.get("chunk_count")), f"chunk_count={cc}")

    # 4. content retrievable + searchable
    if doc_id:
        r = c.get(f"/api/documents/{doc_id}/content")
        check("GET doc content -> 200", r.status_code == 200 and UNIQ.lower() in r.text.lower(),
              f"{r.status_code}")
        r = c.post("/api/search", json={"query": f"{UNIQ} Industries Hgttrn Protocol", "top_k": 5}, timeout=30.0)
        hit = r.status_code == 200 and any(
            UNIQ.lower() in (str(x.get("content", "")) + str(x.get("filename", ""))).lower()
            for x in r.json().get("results", [])
        )
        check("uploaded doc is searchable", hit, f"search status={r.status_code}")

    # 5. extraction produced our unique entity (best-effort; extraction may be off)
    if doc_id:
        r = c.get("/api/graph/search", params={"query": UNIQ})
        extracted = r.status_code == 200 and len(r.json().get("results", [])) > 0
        print(f"  [INFO] entity extraction for '{UNIQ}': "
              f"{'entities found' if extracted else 'none (extraction may be disabled)'}")

finally:
    # 6. cleanup — always
    if doc_id:
        rd = c.delete(f"/api/documents/{doc_id}")
        check("cleanup: delete doc", rd.status_code == 200, f"{rd.status_code}")
        check("doc gone -> 404", c.get(f"/api/documents/{doc_id}").status_code == 404)
    if cid:
        rc = c.delete(f"/api/collections/{cid}")
        check("cleanup: delete temp collection", rc.status_code == 200, f"{rc.status_code}")
    # verify our unique entity no longer resolves (orphan cleanup)
    rg = c.get("/api/graph/search", params={"query": UNIQ})
    if rg.status_code == 200:
        remaining = len(rg.json().get("results", []))
        check("unique entities cleaned up (orphan removal)", remaining == 0, f"remaining={remaining}")

passed = sum(1 for _, ok in results if ok)
total = len(results)
print(f"\n=== {passed}/{total} ingestion-journey steps passed ===")
sys.exit(0 if passed == total else 1)
