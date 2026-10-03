#!/usr/bin/env python3
"""Real backend consumer boot + read verification against a restored fixture.

Launches the ACTUAL FastAPI app (`app.main:app`, real uvicorn lifespan, real
Neo4j bolt) as a subprocess and verifies restored-fixture reads through the
real HTTP boundary. Oracle contract (frozen BEFORE any runtime trial; wrong
bodies must fail the named checks — see `selftest`):

  - /health exact shape; auth boundary: missing/bad key -> exactly 401 (never
    200) on a protected route.
  - Fixture v2 (oracle.py gen-fixture) scope journeys with the shared
    synthetic keys through the REAL auth chain:
      * scoped key fx-backend-key-0001 (restricted, fx-collection-0001):
        /api/documents lists EXACTLY {fx-source-0001} (doc-crr-1/doc-crr-2 of
        col-crr-1/col-crr-2 denied), /api/collections lists EXACTLY
        {fx-collection-0001}, out-of-scope document/collection reads -> 403,
        scoped /api/stats document/chunk counts == 1.
      * all-scope key fx-backend-key-0002 and the env admin key: /api/documents
        lists EXACTLY the three fixture documents; admin /api/stats counts ==
        fixture (docs 3, chunks 3, entities 2, relationships 1).
      * document detail fields equal the fixture fixed values; /content
        full_content and chunk content equal the independent restored blob
        file's decoded text; /file bytes equal that blob file byte-for-byte
        (hash of the file on disk, not of a previous download).
  - Labeled DIRECT supplements (HTTP would need a banned model call):
      * Neo4j fulltext queryNodes must return all three fixture chunks.
      * AppStorageService (real service) on a byte-identical COPY of the
        restored storage.sqlite: list_keys() returns the {"keys": [...]}
        mapping (paginated to exhaustion), keys/values/updated_at equal the
        fixture rows exactly, and an independent immutable SQLite read of the
        copy agrees; the restored source file's hash must not change.
  - Boot/probe deltas: pre/post captures of nodes, relationships, constraints
    AND indexes are diffed and ENFORCED against an explicit allowlist derived
    from real startup code (neo4j_service.initialize_schema: IF-NOT-EXISTS
    schema ensure + default Collection MERGE; auth chain key-usage
    bookkeeping: APIKey last_used/total_requests/error fields +
    APIKeyUsageLog/HAS_USAGE). Any other node/rel/schema change, any removal
    (fixture data loss), and any definition drift is a run FAILURE, not a
    recorded note.

Integrated chat lifecycle: `--chat-consumer <absolute script>` invokes the
chat writer's node script WHILE this runner's own uvicorn is alive:
  node <script> --state-dir <files-root>/chat --backend-url <own base URL>
       --backend-admin-key <synthetic admin key> --document-file <restored
       citation blob from the fixture uploads root>
Exactly ONE JSON object must be printed on stdout (nothing else); nonzero exit
or contract violations fail the run. Hard (non-advisory) checks enforced by
THIS runner: the raw JSON's upstream.mode must be exactly "isolated-backend",
upstream.url must equal receipt.startup.baseUrl (this runner's own launched
base URL), the EXACTLY-named check ids "proxy-isolated-backend-collections-
200-scoped" (proxy collection) and "proxy-isolated-backend-citation-content-
exact" (citation content; the chat proxy only allows /api/documents/{id}/
content — the Source Modal fetches content and joins chunks — so the legacy
"proxy-isolated-backend-citation-file-exact" id is NOT accepted and its
absence is rejected) must exist with ok=true and WITHOUT reportOnly,
every non-reportOnly check must have ok=true, networkTripwire.clean must be
true with empty blockedAttempts, and cleanup.stateDirUnchanged must be true
(restored chat state byte-stable). The result is recorded as RAW parsed JSON
in receipt "chat"; the execution envelope (command, interpreters, exit,
stderr) is recorded separately in receipt "chat_execution". The backend is
stopped only AFTER the chat phase and the final capture.

Safety: server env is an allowlist (no ambient secrets, no .env reads); all
provider endpoints point at an in-process loopback tripwire — ANY connection
recorded there fails the run; a socket.connect guard inside the server
process blocks non-loopback outbound (no sys.modules replacement, boot not
hidden). Identity guard: the run requires the uvicorn banner for its own port
in its own log, checks its child process is alive before every probe, and
treats any post-death response as a stale-server failure. Scratch space is
claimed atomically, marked as owned, and only ever removed via that marker
(run-ids are sanitized; no traversal, no collisions, no user-path rm).
Receipts are written on EVERY path (preflight block, startup failure, probe
failure, cleanup) — nothing is claimed green on a missing artifact. Exit
codes: 0 = all probes passed; 1 = verification failure; 2 = blocked
(missing dependency / unusable environment), never a skip.
"""

import argparse
import hashlib
import ipaddress
import json
import os
import random
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

RUNNER_VERSION = 2
PLACEHOLDER_PROVIDER_KEY = "fx-provider-placeholder-no-calls"

# ---------------------------------------------------------------------------
# Frozen fixture-v2 oracle constants (source: qa/restore/oracle.py gen-fixture;
# fixture_version storage-v2-consumers-ready). Byte/text expectations are read
# from the independent restored files at runtime; only these fixed metadata
# values are encoded here.
# ---------------------------------------------------------------------------

FX_DOCUMENTS = {
    "fx-source-0001": {
        "filename": "fixture-source.md",
        "file_type": "text/markdown",
        "file_path": "uploads/fx-source-0001_fixture-source.md",
        "processing_status": "completed",
        "chunk_count": 1,
        "collection_id": "fx-collection-0001",
    },
}
FX_SHARED_COLLECTION_ID = "fx-collection-0001"
FX_READ_KEY_ID = "fx-backend-key-0001"
FX_READ_KEY_PLAINTEXT = "fx-synthetic-backend-read-key-0001"
FX_MANAGE_KEY_ID = "fx-backend-key-0002"
FX_MANAGE_KEY_PLAINTEXT = "fx-synthetic-backend-content-key-0002"
FX_ALL_DOCUMENTS = ("fx-source-0001", "doc-crr-1", "doc-crr-2")
FX_ALL_COLLECTIONS = ("fx-collection-0001", "col-crr-1", "col-crr-2")
FX_SCOPED_VISIBLE_COLLECTIONS = ("fx-collection-0001",)
FX_SCOPE_DENIED_DOCUMENTS = ("doc-crr-1", "doc-crr-2")
FX_FULLTEXT_CHUNK_IDS = (
    "doc-crr-1-chunk-0", "doc-crr-2-chunk-0", "fx-source-0001-chunk-0",
)
FX_ADMIN_STATS = {"document_count": 3, "chunk_count": 3,
                  "entity_count": 2, "relationship_count": 1}
FX_SCOPED_STATS = {"document_count": 1, "chunk_count": 1}
FX_APP_ID = "rehearsal-app"
FX_APP_STORAGE_ROWS = {
    "transcripts/rehearsal-1": {"role": "user", "text": "hello"},
    "sync/cursor": {"cursor": 42},
    "rehearsal/state": {"notes": "it's \"quoted\" \\ esc"},
}
FX_APP_STORAGE_TS = "2026-10-01T00:00:00Z"

# Startup-legit schema objects (neo4j_service.initialize_schema, all
# IF NOT EXISTS). The restored store may already carry them; additions must
# stay inside this set, removals/definition drift are failures.
# Fresh-bootstrap acceptance deltas (declared, source-backed):
# (1) Neo4j 5 creates a backing RANGE index per uniqueness constraint with
#     the SAME name; allowed only when type/entityType/labelsOrTypes/
#     properties pair EXACTLY with the accepted constraint of that name
#     (no name-catchall). Source: neo4j_service.initialize_schema CREATE
#     CONSTRAINT ... IS UNIQUE.
# (2) Boot backfill Document.entity_count (backfill_degraded_document_signals):
#     for completed docs with entity_count NULL, count(DISTINCT e) over
#     (d)-[:HAS_CHUNK]->(:Chunk)-[:MENTIONS]->(e:Entity). The runner computes
#     the expected count INDEPENDENTLY from the PRE-BOOT captured topology.
# (3) Stats-seeded SystemMeta timestamps (previously accepted; retained).

STARTUP_CONSTRAINT_NAMES = {
    "document_id", "chunk_id", "collection_id", "entity_name",
    "community_id", "api_key_id", "skill_id", "git_connection_id",
}
STARTUP_INDEX_NAMES = {
    "entity_type", "entity_community", "chunk_content",
    "entity_name_fulltext", "community_summary_fulltext",
    "chunk_embedding", "entity_embedding",
    "git_document_path", "phaseb_checkpoint_key",
}
# Key-usage bookkeeping (auth_service throttled last_used write +
# APIUsageMiddleware record_api_key_usage_simple). TRACK_ADMIN_API_KEY_USAGE
# is false in the server env, so only real generated keys bookkeep.
# Stats-seeded bookkeeping: neo4j_service.get_stats -> _get_or_seed_
# analysis/detection_timestamp() creates these SystemMeta nodes when absent.
# Triggered by the REQUIRED /api/stats fixture-counts probe; allowed only for
# these code-derived keys.
STATS_SEEDED_META_KEYS = {
    "last_relationship_analysis_at", "last_community_detection_at",
}
APIKEY_ALLOWED_CHANGED_FIELDS = {
    "total_requests", "last_used_at", "error_count",
    "last_error_at", "last_error_message",
}
USAGE_LOG_STATIC_CHANGED_FIELDS = {"request_count", "error_count"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Blocked(Exception):
    """Unusable environment / missing dependency -> exit 2."""


class RunFailure(Exception):
    """Verification failure -> exit 1 (fail-closed, receipt preserved)."""


# ---------------------------------------------------------------------------
# Dependency preflight (fail-closed; nothing is installed or skipped)
# ---------------------------------------------------------------------------

def preflight() -> dict:
    missing = []
    versions = {}
    for mod in ("fastapi", "uvicorn", "neo4j", "httpx"):
        try:
            m = __import__(mod)
            versions[mod] = getattr(m, "__version__", "unknown")
        except Exception as e:  # pragma: no cover - environment diagnostic
            missing.append(f"{mod}: {e}")
    if missing:
        raise Blocked(
            "runner dependencies missing in this Python environment ("
            + "; ".join(missing)
            + "). The real consumer boot cannot be claimed without them; "
            "install nothing automatically — exit 2.")
    return {"python": sys.version.split()[0], **versions}


# ---------------------------------------------------------------------------
# Keys fixture
# ---------------------------------------------------------------------------

def load_keys(path: Path) -> list:
    """Expected schema: [{"id": ..., "plaintext": ..., "role": "read"|"scoped",
    "collection": "<collection id>" (scoped only)}]."""
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        raise Blocked(f"keys fixture unreadable: {e}")
    if not isinstance(data, list) or not data:
        raise Blocked("keys fixture must be a non-empty JSON list")
    for entry in data:
        if not isinstance(entry, dict) or not entry.get("id") \
                or not entry.get("plaintext") \
                or entry.get("role") not in ("read", "scoped"):
            raise Blocked(
                "keys fixture entries need {id, plaintext, role=read|scoped"
                ", collection (scoped)}: " + json.dumps(entry))
        if entry["role"] == "scoped" and not entry.get("collection"):
            raise Blocked(f"scoped key {entry['id']} lacks collection")
    return data


def key_validation_shape(plaintext: str) -> dict:
    """The exact APIKey fields the real auth chain derives from a plaintext.

    Mirrors auth_service.hash_api_key / generate_api_key (prefix = first 12
    chars; lookup is exact-match on key_prefix, then SHA-256 hash compare).
    Used to state the fixture-seed contract and to self-check the supplied
    fixture keys are structurally authentic.
    """
    return {
        "key_prefix": plaintext[:12],
        "key_hash": sha256_bytes(plaintext.encode()),
    }


# ---------------------------------------------------------------------------
# Environment construction (allowlist; never inherits ambient secrets)
# ---------------------------------------------------------------------------

BASE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TERM")


def build_server_env(args, scratch: Path, provider_sink: str,
                     backend_dir: str) -> dict:
    env = {k: os.environ[k] for k in BASE_ENV_KEYS if os.environ.get(k)}
    env["HOME"] = str(scratch)
    env["TMPDIR"] = str(scratch / "tmp")
    workdir = scratch / "workdir"
    env.update({
        # Graph target (explicitly provided by the main harness)
        "NEO4J_URI": args.bolt_url,
        "NEO4J_USER": args.neo4j_user,
        "NEO4J_PASSWORD": args.neo4j_password,
        "ADMIN_API_KEY": args.admin_key,
        # Embedding parity with production shape (8-dim synthetic vectors in
        # the fixture) but a loopback sink + placeholder key: any accidental
        # provider call lands on the in-process tripwire and fails the run.
        "USE_OPENAI_EMBEDDINGS": "true",
        "EMBEDDING_DIMENSION": "8",
        "EMBEDDING_SEND_DIMENSIONS": "true",
        "EMBEDDING_API_KEY": PLACEHOLDER_PROVIDER_KEY,
        "EMBEDDING_API_BASE": provider_sink,
        "OPENAI_API_KEY": PLACEHOLDER_PROVIDER_KEY,
        "OPENAI_API_BASE": provider_sink,
        "GRAPH_EXTRACTION_API_KEY": PLACEHOLDER_PROVIDER_KEY,
        # Model/telemetry/effects hard-off
        "ENABLE_RERANKING": "false",
        "RERANKER_PRELOAD": "false",
        "AUTO_RESUME_PENDING_ON_STARTUP": "false",
        "AUTO_RESUME_IMAGE_ANALYSIS": "false",
        "ENABLE_GIT_INTEGRATION": "false",
        "ENABLE_WEBHOOKS": "false",
        "ENABLE_WEB_CRAWL": "false",
        "ENABLE_SKILLS": "false",
        "ENABLE_APPS": "false",
        "ENABLE_SESSIONS": "false",
        "ENABLE_REMOTE_MCP": "false",
        "ENABLE_CONSOLIDATION_SCHEDULER": "false",
        "ENABLE_AUDIT_LOG": "false",
        "TRACK_ADMIN_API_KEY_USAGE": "false",
        "MAX_QUERIES_PER_MONTH": "0",
        "EXPOSE_API_DOCS": "false",
        "SENTRY_DSN": "",
        "LANGFUSE_PUBLIC_KEY": "",
        "LANGFUSE_SECRET_KEY": "",
        "LANGFUSE_BASE_URL": "",
        "LOG_FORMAT": "plain",
        # Restored file roots (relative to the isolated server cwd)
        "UPLOAD_DIR": "./uploads",
        "CUSTOM_INPUTS_DIR": "./custom_inputs",
        "SKILLS_DIR": "./skills",
        "APPS_DIR": "./apps",
        "GIT_WORK_DIR": str(scratch / "git_repos"),
        "HF_HOME": str(scratch / "hf"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        # Real app import path (RESOLVED absolute backend directory string —
        # args.backend_dir defaults to None and must never reach the env)
        "PYTHONPATH": backend_dir,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        # Consumer identity marker (recorded; app ignores it)
        "CORTEX_BACKEND_CONSUMER_IDENTITY": args.run_id,
    })
    # subprocess.Popen requires str values; a None (unresolved required
    # value) must fail LOUDLY, never be discarded or silently coerced.
    for key, value in env.items():
        if value is None:
            raise Blocked(f"required server env value {key} resolved to "
                          f"None (unresolved input; refusing to spawn)")
        if not isinstance(value, str):
            raise Blocked(f"server env value {key} must be a string, got "
                          f"{type(value).__name__}")
    return env


def write_network_guard(path: Path) -> None:
    """Fail-fast guard inside the SERVER process: any outbound connection to a
    non-loopback address raises. Proves the receipt's no-external-request
    claim; the app source itself is untouched (no sys.modules replacement)."""
    path.write_text('''
import ipaddress, socket

_orig_connect = socket.socket.connect
_ALLOWED_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else None
    if isinstance(host, str) and host not in _ALLOWED_HOSTS:
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_loopback:
                raise RuntimeError(
                    f"network guard: non-loopback outbound connection to "
                    f"{host!r} blocked (consumer must make no external "
                    f"requests)")
        except ValueError:
            raise RuntimeError(
                f"network guard: outbound connection to non-IP host "
                f"{host!r} blocked")
    elif isinstance(address, str):
        raise RuntimeError("network guard: unix socket connect blocked")
    return _orig_connect(self, address)


socket.socket.connect = _guarded_connect

import runpy, sys
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
''')


def write_uvicorn_entry(path: Path, port: int) -> None:
    path.write_text(f'''
import logging
logging.basicConfig(level=logging.INFO)
import uvicorn
uvicorn.run(
    "app.main:app",
    host="127.0.0.1",
    port={port},
    lifespan="on",
    workers=1,
    log_level="info",
)
''')


# ---------------------------------------------------------------------------
# Provider tripwire: loopback sink recording ANY provider call attempt
# ---------------------------------------------------------------------------

class ProviderTripwire:
    """Listens on a random loopback port. Every provider base URL in the
    server env points here; any accepted connection is recorded and fails
    the run (a real provider must never be contacted, and a silent
    connection-refused must never hide an attempted call)."""

    def __init__(self):
        self.hits = []
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(16)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def sink_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def _serve(self):
        self._sock.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, addr = self._sock.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                try:
                    conn.recv(4096)
                except OSError:
                    pass
                try:
                    conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n"
                                 b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                except OSError:
                    pass
            self.hits.append({"peer": f"{addr[0]}:{addr[1]}",
                              "ts": time.time()})

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2)
        try:
            self._sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Read-only direct Neo4j captures (nodes + relationships + schema)
# ---------------------------------------------------------------------------

CAPTURE_QUERY = (
    "MATCH (n) RETURN labels(n) AS labels, "
    "coalesce(n.id, n.task_id, n.name, n.key, n.key_id, n.date) AS key, "
    "properties(n) AS props ORDER BY labels(n)[0], key"
)
CAPTURE_RELS_QUERY = (
    "MATCH (a)-[r]->(b) RETURN labels(a) AS la, "
    "coalesce(a.id, a.task_id, a.name, a.key, a.key_id, a.date) AS ka, "
    "type(r) AS rt, properties(r) AS rp, labels(b) AS lb, "
    "coalesce(b.id, b.task_id, b.name, b.key, b.key_id, b.date) AS kb"
)
SCHEMA_CONSTRAINTS_QUERY = (
    "SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties "
    "RETURN name, type, entityType, labelsOrTypes, properties ORDER BY name"
)
SCHEMA_INDEXES_QUERY = (
    "SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, "
    "indexProvider WHERE NOT type IN ['LOOKUP'] "
    "RETURN name, type, entityType, labelsOrTypes, properties, indexProvider "
    "ORDER BY name"
)


def _jsonable(value):
    """Neo4j driver temporals (neo4j.time.DateTime/Date/Time/Duration) are
    NOT JSON-serializable by default and would TypeError the receipt write.
    Convert explicitly to their standard ISO representation — meaning is
    preserved, not stripped."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    iso = getattr(value, "iso_format", None)
    if callable(iso):
        try:
            out = iso()
            if isinstance(out, str):
                return out
        except Exception:
            pass
    import datetime as _dt
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    return str(value)


def graph_capture(uri: str, user: str, password: str) -> dict:
    import neo4j as neo4j_mod

    driver = neo4j_mod.GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            nodes = []
            for r in session.run(CAPTURE_QUERY):
                key = r["key"]
                props = r["props"]
                # Usage-log nodes have no domain id; coalesce yields their
                # key_id. Disambiguate per date so same-key logs on
                # different dates never collapse into one capture identity.
                if key is not None and "key_id" in props \
                        and "date" in props and key == props["key_id"]:
                    key = f"{key}@{_jsonable(props['date'])}"
                nodes.append({"labels": list(r["labels"]), "key": key,
                              "props": {k: _jsonable(v)
                                        for k, v in props.items()}})
            rels = [
                {"la": list(r["la"]), "ka": r["ka"], "rt": r["rt"],
                 "rp": {k: _jsonable(v) for k, v in r["rp"].items()},
                 "lb": list(r["lb"]), "kb": r["kb"]}
                for r in session.run(CAPTURE_RELS_QUERY)
            ]
            constraints = [
                {k: _jsonable(r[k]) for k in
                 ("name", "type", "entityType", "labelsOrTypes", "properties")}
                for r in session.run(SCHEMA_CONSTRAINTS_QUERY)
            ]
            indexes = [
                {k: _jsonable(r[k]) for k in
                 ("name", "type", "entityType", "labelsOrTypes", "properties",
                  "indexProvider")}
                for r in session.run(SCHEMA_INDEXES_QUERY)
            ]
        return {"nodes": nodes, "rels": rels,
                "constraints": constraints, "indexes": indexes}
    finally:
        driver.close()


def safe_capture(args, label: str) -> dict:
    try:
        return graph_capture(args.bolt_url, args.neo4j_user,
                             args.neo4j_password)
    except Exception as e:
        raise RunFailure(
            f"{label} graph capture against the restored target failed "
            f"(bolt {args.bolt_url}): {type(e).__name__}: {e}")


def _node_key(n):
    return (tuple(sorted(n["labels"])), n["key"])


def _schema_rows(rows):
    return {r["name"]: json.dumps(r, sort_keys=True, default=str)
            for r in rows}


def expected_entity_counts(pre: dict) -> dict:
    """INDEPENDENTLY compute the entity_count the boot backfill must derive
    for each completed fixture Document whose entity_count is absent pre-boot
    (mirrors the WRITER SEMANTICS of backfill_degraded_document_signals —
    count(DISTINCT e) over (d)-[:HAS_CHUNK]->(:Chunk)-[:MENTIONS]->(e) — but
    derived from the runner's own PRE-BOOT capture, not from app code)."""
    chunks_of_doc = {}
    mentioned_by_chunk = {}
    for r in pre["rels"]:
        la, ka, rt, lb, kb = r["la"], r["ka"], r["rt"], r["lb"], r["kb"]
        if rt == "HAS_CHUNK" and la == ["Document"] and lb == ["Chunk"]:
            chunks_of_doc.setdefault(ka, set()).add(kb)
        elif rt == "MENTIONS" and la == ["Chunk"] and lb == ["Entity"]:
            mentioned_by_chunk.setdefault(ka, set()).add(kb)
    expected = {}
    for n in pre["nodes"]:
        if n["labels"] == ["Document"] \
                and n["props"].get("processing_status") == "completed" \
                and "entity_count" not in n["props"]:
            entities = set()
            for chunk in chunks_of_doc.get(n["key"], ()):
                entities |= mentioned_by_chunk.get(chunk, set())
            expected[n["key"]] = len(entities)
    return expected


def diff_capture(before: dict, after: dict) -> dict:
    b = {_node_key(n): n["props"] for n in before["nodes"]}
    a = {_node_key(n): n["props"] for n in after["nodes"]}
    changed = []
    for k in sorted(b.keys() & a.keys(), key=str):
        if b[k] != a[k]:
            fields = sorted(
                [p for p in b[k] if b[k].get(p) != a[k].get(p)]
                + [p for p in a[k] if p not in b[k]])
            changed.append({"labels": list(k[0]), "key": k[1],
                            "properties": fields,
                            "values": {
                                f: {"before": b[k].get(f),
                                    "after": a[k].get(f)}
                                for f in fields}})

    def rel_key(r):
        return (tuple(sorted(r["la"])), r["ka"], r["rt"],
                json.dumps(r["rp"], sort_keys=True),
                tuple(sorted(r["lb"])), r["kb"])

    rb = {rel_key(r) for r in before["rels"]}
    ra = {rel_key(r) for r in after["rels"]}

    cb = _schema_rows(before["constraints"])
    ca = _schema_rows(after["constraints"])
    ib = _schema_rows(before["indexes"])
    ia = _schema_rows(after["indexes"])

    def schema_diff(x, y):
        return sorted(set(x) - set(y))

    changed_schema = sorted(
        n for n in set(cb) & set(ca) if cb[n] != ca[n])
    changed_schema += sorted(
        n for n in set(ib) & set(ia) if ib[n] != ia[n])

    return {
        "after_constraints": {
            r.get("name"): {"entityType": r.get("entityType"),
                            "labelsOrTypes": r.get("labelsOrTypes") or [],
                            "properties": r.get("properties") or []}
            for r in after["constraints"]},
        "after_index_definitions": {
            r.get("name"): {"type": r.get("type"),
                            "entityType": r.get("entityType"),
                            "labelsOrTypes": r.get("labelsOrTypes") or [],
                            "properties": r.get("properties") or []}
            for r in after["indexes"]},
        "added_nodes": sorted(
            [list(k[0]), k[1]] for k in a.keys() - b.keys()),
        "removed_nodes": sorted(
            [list(k[0]), k[1]] for k in b.keys() - a.keys()),
        "changed_nodes": changed,
        "added_rels": sorted(
            [list(k[0]), k[1], k[2], list(k[4]), k[5]]
            for k in ra - rb),
        "removed_rels": sorted(
            [list(k[0]), k[1], k[2], list(k[4]), k[5]]
            for k in rb - ra),
        "schema_added": {
            "constraints": schema_diff(ca, cb),
            "indexes": schema_diff(ia, ib),
        },
        "schema_removed": {
            "constraints": schema_diff(cb, ca),
            "indexes": schema_diff(ib, ia),
        },
        "schema_changed": changed_schema,
    }


def _usage_key_id(key):
    """APIKeyUsageLog capture identity is '<key_id>@<date>' (see
    graph_capture); return the owning key id."""
    return key.split("@", 1)[0] if isinstance(key, str) else key


def _paired_backing_indexes(diff: dict) -> tuple:
    """Partition added indexes into (accepted constraint-backing RANGE
    indexes, other added indexes). Acceptance requires EXACT pairing with
    the post-boot uniqueness constraint of the same name: type RANGE,
    entityType NODE, identical labelsOrTypes AND properties — never a name
    catchall."""
    accepted, others = [], []
    for name in diff["schema_added"]["indexes"]:
        row = diff["after_index_definitions"].get(name, {})
        cons = diff["after_constraints"].get(name)
        if (name in STARTUP_CONSTRAINT_NAMES
                and cons is not None
                and row.get("type") == "RANGE"
                and row.get("entityType") == "NODE"
                and list(row.get("labelsOrTypes") or [])
                == list(cons.get("labelsOrTypes") or [])
                and list(row.get("properties") or [])
                == list(cons.get("properties") or [])):
            accepted.append(name)
        else:
            others.append(name)
    return accepted, others


def classify_delta(diff: dict, key_ids, max_usage_nodes=None,
                   expected_entity_counts=None) -> dict:
    """Enforce the explicit boot/probe delta allowlist. Returns violations
    (empty = delta accepted). Derived from real startup code only:
    initialize_schema IF-NOT-EXISTS ensures + constraint-backing RANGE
    indexes (exact pairing) + default-Collection MERGE, generated-key usage
    bookkeeping, stats-seeded SystemMeta timestamps, and the
    Document.entity_count bootstrap backfill verified against the
    independently computed expected counts. Everything else fails."""
    key_ids = set(key_ids)
    expected_entity_counts = expected_entity_counts or {}
    if max_usage_nodes is None:
        max_usage_nodes = max(2 * len(key_ids), 2)
    v = []

    for name in diff["schema_added"]["constraints"]:
        if name not in STARTUP_CONSTRAINT_NAMES:
            v.append(f"unexpected constraint created: {name}")
    backing, other_indexes = _paired_backing_indexes(diff)
    for name in other_indexes:
        if name not in STARTUP_INDEX_NAMES:
            v.append(f"unexpected index created: {name}")
    for name in diff["schema_removed"]["constraints"]:
        v.append(f"constraint removed (fixture schema loss): {name}")
    for name in diff["schema_removed"]["indexes"]:
        v.append(f"index removed (fixture schema loss): {name}")
    for name in diff["schema_changed"]:
        v.append(f"schema definition changed: {name}")

    added_collections = 0
    added_usage_logs = 0
    seeded_meta = 0
    backfilled = []
    for labels, key in diff["added_nodes"]:
        if labels == ["Collection"] and key == "default":
            added_collections += 1
            continue
        if labels == ["APIKeyUsageLog"] and _usage_key_id(key) in key_ids:
            added_usage_logs += 1
            continue
        if labels == ["SystemMeta"] and key in STATS_SEEDED_META_KEYS:
            seeded_meta += 1
            continue
        v.append(f"unexpected node added: labels={labels} key={key!r}")
    if seeded_meta > len(STATS_SEEDED_META_KEYS):
        v.append(f"too many stats-seeded SystemMeta nodes: {seeded_meta}")
    if added_collections > 1:
        v.append(f"too many default Collections created: {added_collections}")
    if added_usage_logs > max_usage_nodes:
        v.append(f"too many APIKeyUsageLog nodes created: {added_usage_logs} "
                 f"> {max_usage_nodes}")

    for labels, key in diff["removed_nodes"]:
        v.append(f"node removed (fixture data loss): labels={labels} "
                 f"key={key!r}")

    for c in diff["changed_nodes"]:
        labels, key, fields = c["labels"], c["key"], c["properties"]
        if labels == ["Document"] and key in expected_entity_counts:
            fields = c["properties"]
            values = c.get("values", {})
            if fields == ["entity_count"] \
                    and values.get("entity_count", {}).get("before") is None \
                    and values.get("entity_count", {}).get("after") \
                    == expected_entity_counts[key]:
                backfilled.append(key)
                continue
            v.append(
                f"Document {key} changed outside the entity_count "
                f"bootstrap backfill: fields={fields} "
                f"values={json.dumps(values, default=str)[:200]} "
                f"(expected entity_count {expected_entity_counts[key]} "
                f"from absent-before only; arbitrary field changes and "
                f"wrong derived counts cannot pass)")
            continue
        if labels == ["APIKey"] and key in key_ids:
            bad = [f for f in fields
                   if f not in APIKEY_ALLOWED_CHANGED_FIELDS]
            if bad:
                v.append(f"APIKey {key} changed unexpected fields {bad}")
            continue
        if labels == ["APIKeyUsageLog"] and _usage_key_id(key) in key_ids:
            bad = [f for f in fields
                   if f not in USAGE_LOG_STATIC_CHANGED_FIELDS
                   and not f.startswith("ep_")]
            if bad:
                v.append(f"APIKeyUsageLog {key} changed unexpected fields "
                         f"{bad}")
            continue
        v.append(f"node changed outside allowlist: labels={labels} "
                 f"key={key!r} fields={fields}")

    added_rels = 0
    for la, ka, rt, lb, kb in diff["added_rels"]:
        if rt == "HAS_USAGE" and la == ["APIKey"] and ka in key_ids \
                and lb == ["APIKeyUsageLog"] and _usage_key_id(kb) in key_ids:
            added_rels += 1
            continue
        v.append(f"unexpected relationship added: ({la}:{ka})-[:{rt}]->"
                 f"({lb}:{kb})")
    if added_rels > max_usage_nodes:
        v.append(f"too many HAS_USAGE rels added: {added_rels} > "
                 f"{max_usage_nodes}")
    for la, ka, rt, lb, kb in diff["removed_rels"]:
        v.append(f"relationship removed (fixture data loss): ({la}:{ka})"
                 f"-[:{rt}]->({lb}:{kb})")

    return {"violations": v, "accepted": {
        "entity_count_backfilled": {k: expected_entity_counts[k]
                                    for k in sorted(backfilled)},
        "constraint_backing_indexes": sorted(backing),
        "default_collection_created": added_collections,
        "usage_log_nodes_created": added_usage_logs,
        "stats_seeded_meta": sorted(
            k for k in diff["added_nodes"] if k[0] == ["SystemMeta"]
            and k[1] in STATS_SEEDED_META_KEYS),
        "usage_rels_created": added_rels,
        "schema_constraints_ensured": sorted(
            n for n in diff["schema_added"]["constraints"]
            if n in STARTUP_CONSTRAINT_NAMES),
        "schema_indexes_ensured": sorted(
            n for n in diff["schema_added"]["indexes"]
            if n in STARTUP_INDEX_NAMES),
    }}


# ---------------------------------------------------------------------------
# Direct supplements (fulltext + AppStorage) — asserted, not advisory
# ---------------------------------------------------------------------------

def fulltext_probe(uri: str, user: str, password: str, index: str,
                   term: str, expected_chunk_ids) -> dict:
    """DIRECT (not HTTP): the HTTP search boundary requires a query-embedding
    model call, which this consumer must not make. The restored fulltext
    index is exercised directly and must return the fixture chunks."""
    import neo4j as neo4j_mod

    driver = neo4j_mod.GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            rows = session.run(
                "CALL db.index.fulltext.queryNodes($index, $term) "
                "YIELD node, score "
                "RETURN node.id AS id, score ORDER BY score DESC LIMIT 10",
                index=index, term=term,
            )
            hits = [{"id": r["id"], "score": r["score"]} for r in rows]
    finally:
        driver.close()
    hit_ids = {h["id"] for h in hits}
    missing = sorted(set(expected_chunk_ids) - hit_ids)
    violations = []
    if not hits:
        violations.append(f"fulltext index {index!r} returned no hits for "
                          f"term {term!r}")
    if missing:
        violations.append(f"fulltext index {index!r} missing fixture chunks "
                          f"{missing} for term {term!r}")
    return {"index": index, "term": term, "hits": hits,
            "expected_chunk_ids": sorted(expected_chunk_ids),
            "violations": violations}


def _immutable_rows(db_path: Path):
    conn = sqlite3.connect(
        f"{db_path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        return {
            r[0]: {"value": r[1], "size": r[2], "updated_at": r[3]}
            for r in conn.execute(
                "SELECT key, value, size, updated_at FROM kv ORDER BY key")
        }
    finally:
        conn.close()


def appstorage_probe(files_root: Path, app_id: str, scratch: Path) -> dict:
    """DIRECT (not HTTP): AppStorageService (the real service) read against a
    byte-identical working COPY of the restored storage.sqlite (the service
    opens its database read-write, so the restored artifact itself must never
    be opened). The copy is read back independently (immutable SQLite) and
    compared to the fixture rows; the restored source file's hash must not
    change."""
    src = files_root / "apps" / app_id / "storage.sqlite"
    if not src.is_file():
        raise Blocked(f"app storage fixture missing: {src}")
    copy_root = scratch / "appstorage-copy-root"
    app_copy_dir = copy_root / app_id
    app_copy_dir.mkdir(parents=True, exist_ok=True)
    copy = app_copy_dir / "storage.sqlite"
    shutil.copyfile(src, copy)
    src_sha_before = sha256_file(src)
    copy_sha_before = sha256_file(copy)
    repo_root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(repo_root / "backend"))
    try:
        from app.services.app_storage_service import AppStorageService

        service = AppStorageService(copy_root)
        # list_keys returns {"keys": [{key, size, updated_at}], "next": ...}
        # — a mapping with a keys field, NOT a bare iterable of names.
        entries = []
        after = ""
        for _ in range(10):  # paginate to exhaustion (hard cap 10 pages)
            page = service.list_keys(app_id, after=after, limit=100)
            entries.extend(page.get("keys") or [])
            nxt = page.get("next")
            if not nxt:
                break
            after = nxt
        values = {e["key"]: service.get(app_id, e["key"]) for e in entries}
    finally:
        sys.path.pop(0)
    keys = sorted(e["key"] for e in entries)
    ts_by_key = {e["key"]: e["updated_at"] for e in entries}
    independent = _immutable_rows(copy)
    violations = []
    if set(keys) != set(FX_APP_STORAGE_ROWS):
        violations.append(
            f"appstorage keys {sorted(keys)} != fixture keys "
            f"{sorted(FX_APP_STORAGE_ROWS)}")
    for k, expected in FX_APP_STORAGE_ROWS.items():
        if k in values and values[k] != expected:
            violations.append(
                f"appstorage value for {k!r} != fixture value: "
                f"{values[k]!r} != {expected!r}")
        if k in ts_by_key and ts_by_key[k] != FX_APP_STORAGE_TS:
            violations.append(
                f"appstorage updated_at for {k!r} != fixture "
                f"({ts_by_key[k]!r} != {FX_APP_STORAGE_TS!r})")
    if set(independent) != set(FX_APP_STORAGE_ROWS):
        violations.append(
            f"independent immutable read keys {sorted(independent)} != "
            f"fixture keys {sorted(FX_APP_STORAGE_ROWS)}")
    for k, row in independent.items():
        try:
            val = json.loads(row["value"])
        except Exception:
            val = None
        if FX_APP_STORAGE_ROWS.get(k) != val:
            violations.append(
                f"independent immutable read for {k!r} != fixture value: "
                f"{val!r}")
    src_sha_after = sha256_file(src)
    if src_sha_after != src_sha_before:
        violations.append(
            "restored storage.sqlite hash changed during the probe "
            "(the restored artifact must never be touched)")
    return {
        "app_id": app_id,
        "keys": keys,
        "values": values,
        "updated_at": ts_by_key,
        "independent_immutable_read": {
            k: {"value_sha256": sha256_bytes(r["value"].encode()),
                "size": r["size"], "updated_at": r["updated_at"]}
            for k, r in independent.items()},
        "source_sha256_before": src_sha_before,
        "source_sha256_after": src_sha_after,
        "copy_sha256_before": copy_sha_before,
        "copy_sha256_after": sha256_file(copy),
        "mode": "direct (AppStorageService on a byte-identical copy; "
                "copy hash informational — WAL sidecars may appear)",
        "violations": violations,
    }


# ---------------------------------------------------------------------------
# HTTP probe engine — checks MUST raise on failure; a check that returns a
# falsy value or any exception also fails (a wrong body can never pass an
# empty/unselected check). Proven by `selftest`.
# ---------------------------------------------------------------------------

class ProbeResult(dict):
    pass


class _FakeResponse:
    """Minimal httpx.Response stand-in for selftest/unit use (stdlib only)."""

    def __init__(self, status_code=200, content=b"", json_body=None,
                 content_type="application/json"):
        self.status_code = status_code
        self.content = content if json_body is None else json.dumps(
            json_body).encode()
        self.headers = {"content-type": content_type}

    def json(self):
        return json.loads(self.content)


class _FakeClient:
    def __init__(self, routes):
        self.routes = routes  # (method, path) -> _FakeResponse
        self.requests = []

    def request(self, method, path, headers=None, **kwargs):
        self.requests.append((method, path, headers))
        return self.routes[(method, path)]


def probe(client, name: str, method: str, path: str, headers=None,
          expect_status: int = 200, checks=None, **kwargs) -> ProbeResult:
    resp = client.request(method, path, headers=headers, **kwargs)
    body = resp.content
    result = ProbeResult({
        "name": name, "method": method, "path": path,
        "status": resp.status_code, "expect_status": expect_status,
        "status_ok": resp.status_code == expect_status,
        "body_sha256": sha256_bytes(body),
        "bytes": len(body),
        "checks_failed": [],
    })
    parsed = None
    ctype = resp.headers.get("content-type", "") if hasattr(
        resp, "headers") else ""
    if "json" in ctype:
        try:
            parsed = resp.json()
        except Exception:
            parsed = None
    result["body_parsed"] = isinstance(parsed, (dict, list))
    for check_name, check in (checks or {}).items():
        failure = None
        try:
            ret = check(parsed, resp)
            if ret is False:
                failure = "check returned False without raising"
            elif ret is not True and ret is not None:
                failure = (f"check returned a non-True value (treated as "
                           f"failure): {ret!r}")
        except AssertionError as e:
            failure = str(e) or "assertion failed"
        except Exception as e:  # pragma: no cover - defensive
            failure = f"check raised {type(e).__name__}: {e}"
        if failure:
            result["checks_failed"].append(f"{check_name}: {failure}")
    return result


def probe_failed(p: ProbeResult) -> bool:
    return (not p["status_ok"]) or bool(p.get("checks_failed"))


# ---- frozen check factories (fixture v2) ----------------------------------

def docs_list_check(present=(), absent=(), exact=None):
    def _check(b, r):
        assert isinstance(b, dict), f"body not a dict: {type(b).__name__}"
        docs = b.get("documents")
        assert isinstance(docs, list) and docs, \
            f"documents missing/empty (empty selection cannot pass): " \
            f"{str(b)[:200]}"
        ids = [d.get("id") for d in docs if isinstance(d, dict)]
        assert all(isinstance(i, str) and i for i in ids), \
            f"malformed document ids: {ids!r}"
        assert b.get("total") == len(docs), \
            f"total {b.get('total')!r} != listed {len(docs)}"
        s = set(ids)
        if exact is not None:
            assert s == set(exact), \
                f"document ids {sorted(s)} != expected exactly {sorted(exact)}"
        for i in present:
            assert i in s, f"expected document {i} not listed"
        for i in absent:
            assert i not in s, \
                f"scope leak: document {i} listed but not granted to this key"
    return _check


def collections_check(present=(), absent=(), exact=None):
    def _check(b, r):
        assert isinstance(b, dict), f"body not a dict: {type(b).__name__}"
        cols = b.get("collections")
        assert isinstance(cols, list) and cols, \
            f"collections missing/empty (empty selection cannot pass): " \
            f"{str(b)[:200]}"
        ids = [c.get("id") for c in cols if isinstance(c, dict)]
        assert all(isinstance(i, str) and i for i in ids), \
            f"malformed collection ids: {ids!r}"
        assert b.get("total") == len(cols), \
            f"total {b.get('total')!r} != listed {len(cols)}"
        s = set(ids)
        if exact is not None:
            assert s == set(exact), \
                f"collection ids {sorted(s)} != expected exactly " \
                f"{sorted(exact)}"
        for i in present:
            assert i in s, f"expected collection {i} not listed"
        for i in absent:
            assert i not in s, \
                f"scope leak: collection {i} listed but not granted to this key"
    return _check


def health_check(b, r):
    assert isinstance(b, dict), f"body not a dict: {type(b).__name__}"
    assert b.get("status") == "healthy", f"status={b.get('status')!r}"
    assert b.get("schema_initialized") is True, \
        f"schema_initialized={b.get('schema_initialized')!r}"
    assert b.get("neo4j_connected") is True, \
        f"neo4j_connected={b.get('neo4j_connected')!r}"


def stats_check(expected_counts):
    def _check(b, r):
        assert isinstance(b, dict), f"body not a dict: {type(b).__name__}"
        for field, expected in expected_counts.items():
            assert b.get(field) == expected, \
                f"stats {field}={b.get(field)!r} != fixture {expected}"
    return _check


def fx_detail_check(doc_id, blob_bytes):
    fx = FX_DOCUMENTS[doc_id]

    def _check(b, r):
        assert isinstance(b, dict), f"body not a dict: {type(b).__name__}"
        assert b.get("id") == doc_id, f"id={b.get('id')!r} != {doc_id}"
        for field in ("filename", "file_type", "file_path",
                      "processing_status", "chunk_count", "collection_id"):
            expected = fx[field]
            assert b.get(field) == expected, \
                f"detail {field}={b.get(field)!r} != fixture {expected!r}"
        assert b.get("file_size") == len(blob_bytes), \
            f"detail file_size={b.get('file_size')!r} != blob bytes " \
            f"{len(blob_bytes)}"
    return _check


def fx_content_check(doc_id, blob_text):
    def _check(b, r):
        assert isinstance(b, dict), f"body not a dict: {type(b).__name__}"
        full = b.get("full_content")
        assert isinstance(full, str) and full, \
            f"full_content empty/non-string: {type(full).__name__}"
        assert full == blob_text, \
            f"full_content != restored fixture source text " \
            f"(len {len(full)} vs {len(blob_text)})"
        chunks = b.get("chunks")
        assert isinstance(chunks, list) and chunks, \
            f"chunks missing/empty (empty selection cannot pass)"
        contents = [c.get("content") for c in chunks if isinstance(c, dict)]
        assert all(c == blob_text for c in contents), \
            f"chunk content(s) != restored fixture source text: " \
            f"{[str(c)[:80] for c in contents]}"
    return _check


def fx_file_check(blob_bytes):
    def _check(b, r):
        data = getattr(r, "content", b"")
        assert data == blob_bytes, \
            f"downloaded file bytes != restored fixture blob " \
            f"(sha {sha256_bytes(data)[:16]}… vs " \
            f"{sha256_bytes(blob_bytes)[:16]}…, {len(data)} vs " \
            f"{len(blob_bytes)} bytes)"
        ctype = ""
        try:
            ctype = r.headers.get("content-type", "")
        except Exception:
            pass
        assert "text/markdown" in ctype, \
            f"content-type {ctype!r} does not match fixture text/markdown"
    return _check


CHAT_REQUIRED_RULE = (
    "exactly one JSON object on stdout; exit 0; parsed.ok == true; "
    "parsed.failures == []; parsed.upstream.mode == 'isolated-backend'; "
    "parsed.upstream.url == receipt.startup.baseUrl (this runner's own base "
    "URL); checks contain EXACTLY-named ids 'proxy-isolated-backend-"
    "collections-200-scoped' (proxy collection) and 'proxy-isolated-backend-"
    "citation-content-exact' (citation content; the chat proxy only allows "
    "/api/documents/{id}/content), each with ok=true and without "
    "reportOnly; every non-reportOnly check ok=true; parsed.networkTripwire."
    "clean == true and parsed.networkTripwire.blockedAttempts == []; parsed."
    "cleanup.stateDirUnchanged == true")

CHAT_REQUIRED_CHECK_IDS = (
    "proxy-isolated-backend-collections-200-scoped",
    "proxy-isolated-backend-citation-content-exact",
)


def validate_chat_receipt(parsed, base_url: str) -> list:
    """Hard (non-advisory) contract over the RAW parsed chat JSON. Returns
    failure strings (empty = pass)."""
    v = []
    if not isinstance(parsed, dict):
        return ["chat receipt is not a JSON object"]
    if parsed.get("ok") is not True:
        v.append(f"chat ok={parsed.get('ok')!r} != true")
    if parsed.get("failures") != []:
        v.append(f"chat failures must be []: "
                 f"{json.dumps(parsed.get('failures'))[:500]}")
    upstream = parsed.get("upstream")
    if not isinstance(upstream, dict):
        v.append("chat receipt has no upstream section")
    else:
        if upstream.get("mode") != "isolated-backend":
            v.append(f"upstream.mode={upstream.get('mode')!r} != "
                     f"'isolated-backend'")
        if upstream.get("url") != base_url:
            v.append(f"upstream.url={upstream.get('url')!r} != this runner's "
                     f"own base URL {base_url!r}")
    tripwire = parsed.get("networkTripwire")
    if not isinstance(tripwire, dict):
        v.append("chat receipt has no networkTripwire section")
    else:
        if tripwire.get("clean") is not True:
            v.append(f"networkTripwire.clean={tripwire.get('clean')!r} "
                     f"!= true")
        if tripwire.get("blockedAttempts") != []:
            v.append(f"networkTripwire.blockedAttempts must be []: "
                     f"{json.dumps(tripwire.get('blockedAttempts'))[:500]}")
    cleanup = parsed.get("cleanup")
    if not isinstance(cleanup, dict):
        v.append("chat receipt has no cleanup section")
    elif cleanup.get("stateDirUnchanged") is not True:
        v.append(f"cleanup.stateDirUnchanged="
                 f"{cleanup.get('stateDirUnchanged')!r} != true (the "
                 f"restored chat state copy must survive byte-identical)")
    checks = parsed.get("checks")
    if not isinstance(checks, list) or not checks:
        v.append("chat receipt has no checks (empty selection cannot pass)")
        return v
    by_id = {}
    for c in checks:
        if not isinstance(c, dict):
            v.append(f"malformed chat check entry: {str(c)[:120]}")
            continue
        cid = str(c.get("id", ""))
        by_id[cid] = c
        if c.get("ok") is not True and not c.get("reportOnly"):
            v.append(f"chat check failed: {cid}")
    for required in CHAT_REQUIRED_CHECK_IDS:
        c = by_id.get(required)
        if c is None:
            v.append(f"required hard chat check missing: {required}")
            continue
        if c.get("ok") is not True:
            v.append(f"required hard chat check failed: {required}")
        elif c.get("reportOnly"):
            v.append(f"required hard chat check is reportOnly (advisory): "
                     f"{required} — not acceptable")
    return v


def parse_single_json(text: str):
    """Parse exactly ONE JSON value (extra non-whitespace data is rejected)."""
    stripped = text.strip()
    if not stripped:
        return None, "no JSON on stdout (empty)"
    try:
        obj, idx = json.JSONDecoder().raw_decode(stripped)
    except Exception as e:
        return None, f"stdout is not a single JSON value: {e}"
    rest = stripped[idx:].strip()
    if rest:
        return None, f"stdout contains extra data after the JSON value: " \
                     f"{rest[:120]!r}"
    return obj, None


# ---------------------------------------------------------------------------
# Server lifecycle + identity/stale guards
# ---------------------------------------------------------------------------

def wait_for_health(base_url: str, timeout_s: float, client) -> dict:
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        try:
            resp = client.get(f"{base_url}/health")
            if resp.status_code == 200:
                body = resp.json()
                if body.get("schema_initialized"):
                    return {"ok": True, "body": body}
                last = f"degraded body: {body}"
            else:
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            last = f"connection error: {e}"
        time.sleep(1.0)
    return {"ok": False, "last": last}


def stop_server(proc) -> None:
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=15)


def ensure_alive(proc) -> None:
    """Stale-fail guard: the launched process must be alive; a response from
    a dead process (or an unexplained server) must fail the run."""
    if proc is not None and proc.poll() is not None:
        raise RunFailure(
            f"stale-fail guard: server process pid={proc.pid} exited "
            f"(code={proc.returncode}) while probes were still running")


# ---------------------------------------------------------------------------
# Guard validations (run-id traversal, bolt loopback, fixture markers)
# ---------------------------------------------------------------------------

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_run_id(run_id: str) -> None:
    if not run_id or not RUN_ID_RE.fullmatch(run_id) or ".." in run_id:
        raise Blocked(
            f"run-id {run_id!r} must match [A-Za-z0-9][A-Za-z0-9._-]{{0,63}} "
            f"(no traversal, no collisions, never a user path)")


def validate_bolt_url(url: str) -> None:
    parsed = urlparse(url or "")
    if parsed.scheme not in ("bolt", "bolt+s", "bolt+ssc",
                             "neo4j", "neo4j+s", "neo4j+ssc"):
        raise Blocked(f"bolt url scheme invalid: {url!r}")
    host = parsed.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
        loopback = ip.is_loopback
    except ValueError:
        loopback = host in ("localhost",)
    if not loopback:
        raise Blocked(
            f"bolt url host {host!r} is not loopback (the consumer may only "
            f"talk to the harness-published restored target on loopback)")


def validate_files_root(files_root: Path, repo_root: Path) -> None:
    if not files_root.is_dir():
        raise Blocked(f"files root is not a directory: {files_root}")
    if files_root.parent == files_root:
        raise Blocked("files root must not be a filesystem root")
    if files_root == Path.home() or files_root == repo_root:
        raise Blocked(f"files root must not be {files_root} "
                      f"(home or repository root)")
    try:
        repo_root.relative_to(files_root)
    except ValueError:
        pass
    else:
        raise Blocked("files root must not contain the repository")
    for sub in ("uploads", "custom_inputs"):
        if not (files_root / sub).is_dir():
            raise Blocked(f"files root lacks {sub}/ under {files_root}")


# ---------------------------------------------------------------------------
# Chat consumer hook
# ---------------------------------------------------------------------------

def _binary_version(argv) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=30)
        return (out.stdout or out.stderr).strip()[:80] or "unknown"
    except Exception as e:
        return f"unresolvable: {e}"


def run_chat_consumer(script: Path, chat_state_dir: Path, base_url: str,
                      admin_key: str, document_file: Path, workdir: Path,
                      timeout_s: float, app_node: str = None,
                      chat_work_dir: Path = None, chat_tmpdir: Path = None,
                      min_free_mb: float = 2048.0) -> tuple:
    """Invoke the chat writer's script while the backend is alive. Returns
    (raw parsed chat JSON or None, execution envelope, contract failures).

    Run preconditions enforced HERE: the chat build/install runs in an OWNED,
    marked scratch subdirectory (--work-dir), its TMPDIR points at the owned
    root-filesystem tmp (never the ambient /tmp — an ENOSPC there must not
    fail the consumer), and the ACTUAL selected filesystem's free space is
    enforced BEFORE the locked install."""
    execution = {
        "command": ["node", str(script)],
        "state_dir": str(chat_state_dir),
        "backend_url": base_url,
        "argv_contract": "node <script> --state-dir <chat state dir> "
                         "--backend-url <own base url> --backend-admin-key "
                         "<synthetic admin key> --document-file <restored "
                         "citation blob>[ --app-node <path>] "
                         "[--work-dir <owned scratch chat-work>]",
        "node_interpreter": _binary_version(["node", "--version"]),
        "app_node": None,
        "work_dir": str(chat_work_dir) if chat_work_dir else None,
        "tmpdir": str(chat_tmpdir) if chat_tmpdir else None,
        "min_free_mb": min_free_mb,
        "hard_checks_rule": CHAT_REQUIRED_RULE,
    }
    argv = [
        "node", str(script),
        "--state-dir", str(chat_state_dir),
        "--backend-url", base_url,
        "--backend-admin-key", admin_key,
        "--document-file", str(document_file),
    ]
    if chat_work_dir is not None:
        # Owned scratch only: env-install artifacts live under the marked
        # runner scratch, never in ambient /tmp or any user path; on failure
        # the scratch (and its logs) is preserved with the receipt.
        chat_work_dir.mkdir(parents=True, exist_ok=True)
        argv += ["--work-dir", str(chat_work_dir)]
    if chat_tmpdir is not None:
        chat_tmpdir.mkdir(parents=True, exist_ok=True)
    # Capacity gate on the ACTUAL selected filesystem before the locked
    # install (the H run died with ENOSPC on a full ambient /tmp).
    if chat_work_dir is not None:
        free_mb = shutil.disk_usage(chat_work_dir).free // (1024 * 1024)
        execution["free_mb_before"] = free_mb
        if free_mb < min_free_mb:
            execution["exit"] = "capacity-blocked"
            return None, execution, [
                f"chat capacity gate: only {free_mb} MB free on the "
                f"chat work-dir filesystem ({chat_work_dir}); "
                f"{min_free_mb} MB required before the locked install"]
    if app_node:
        if not Path(app_node).is_absolute():
            raise Blocked(f"--chat-app-node must be an absolute path: "
                          f"{app_node}")
        # Passthrough ONLY: the top-level node (fixture verification,
        # better-sqlite3 Node-22 ABI) is never switched automatically; the
        # chat writer applies --app-node to isolated app processes.
        argv += ["--app-node", app_node]
        execution["app_node"] = {
            "path": app_node,
            "version": _binary_version([app_node, "--version"]),
        }
    execution["command"] = argv
    started = time.monotonic()
    child_env = dict(os.environ)
    if chat_tmpdir is not None:
        child_env["TMPDIR"] = str(chat_tmpdir)
    try:
        proc = subprocess.run(
            argv, cwd=str(workdir), env=child_env, capture_output=True,
            text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        execution["exit"] = "timeout"
        execution["duration_s"] = round(time.monotonic() - started, 3)
        execution["stderr_tail"] = f"chat consumer exceeded {timeout_s}s"
        return None, execution, [f"chat consumer timed out after {timeout_s}s"]
    except FileNotFoundError as e:
        raise Blocked(f"chat consumer could not be executed: {e}")
    execution["exit"] = proc.returncode
    execution["duration_s"] = round(time.monotonic() - started, 3)
    execution["stderr_tail"] = (proc.stderr or "")[-2000:]
    parsed, parse_error = parse_single_json(proc.stdout or "")
    if parse_error:
        return None, execution, [f"chat stdout contract violation: "
                                 f"{parse_error}"]
    failures = validate_chat_receipt(parsed, base_url)
    if proc.returncode != 0 and not failures:
        failures = [f"chat consumer exited {proc.returncode}"]
    return parsed, execution, failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Real backend consumer boot/read verification on a "
                    "restored Cortex fixture (see module docstring).")
    ap.add_argument("--bolt-url", required=True,
                    help="actual Neo4j bolt URL of the restored target "
                         "(exposed by the main restore harness; loopback "
                         "only)")
    ap.add_argument("--neo4j-user", default="neo4j")
    ap.add_argument("--neo4j-password", required=True,
                    help="restored target Neo4j password (explicit; never "
                         "read from .env)")
    ap.add_argument("--files-root", required=True,
                    help="restored/frozen files root containing uploads/, "
                         "custom_inputs/, skills/, apps/, chat/")
    ap.add_argument("--backend-dir", default=None,
                    help="cortex-app backend directory containing app/ "
                         "(default: resolved from this script's location)")
    ap.add_argument("--admin-key", required=True,
                    help="synthetic admin API key for this run (explicit)")
    ap.add_argument("--keys-json", required=True,
                    help="JSON list of fixture API keys: [{id, plaintext, "
                         "role: read|scoped, collection}]")
    ap.add_argument("--expect-document-id", required=True,
                    help="domain Document id that must be readable; the full "
                         "consumer requires a fixture-v2 document id "
                         "(fx-source-0001) — non-fixture ids are BLOCKED, "
                         "never silently degraded to generic checks")
    ap.add_argument("--expect-collection-id", default=None,
                    help="domain Collection id that must be listed")
    ap.add_argument("--fulltext-index", default="chunk_content")
    ap.add_argument("--fulltext-term", default=None,
                    help="term queried against the restored fulltext index "
                         "(direct read; must return all three fixture "
                         "chunks)")
    ap.add_argument("--app-storage-id", default=None,
                    help="app id whose storage.sqlite KV is read directly "
                         "(rehearsal-app activates the frozen fixture-row "
                         "checks; omitted = not exercised)")
    ap.add_argument("--chat-consumer", default=None,
                    help="ABSOLUTE path to the chat writer's consumer script "
                         "(node). While this runner's own uvicorn is alive "
                         "the script is invoked with --state-dir "
                         "<files-root>/chat --backend-url <own base url> "
                         "--backend-admin-key <admin key> --document-file "
                         "<restored citation blob>; its single-JSON receipt "
                         "is validated hard and the RAW parsed JSON is "
                         "recorded as receipt 'chat' (execution envelope in "
                         "'chat_execution')")
    ap.add_argument("--chat-app-node", default=None,
                    help="optional ABSOLUTE path to the chat app-node "
                         "interpreter, passed through verbatim as the chat "
                         "script's --app-node (the top-level node that runs "
                         "fixture verification is NEVER switched: its "
                         "better-sqlite3 is built against the Node 22 ABI; "
                         "Node 20 applicability is the chat writer's tested "
                         "concern, not presumed here)")
    ap.add_argument("--chat-timeout", type=float, default=900.0)
    ap.add_argument("--chat-min-free-mb", type=float, default=2048.0,
                    help="minimum free MB on the chat work-dir filesystem "
                         "required BEFORE the locked app install (capacity "
                         "gate on the actual selected fs; failure is hard, "
                         "recorded in chat_execution)")
    ap.add_argument("--chat-state-dir", default=None,
                    help="chat state dir override (default "
                         "<files-root>/chat)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out", default=None,
                    help="receipt output directory")
    ap.add_argument("--startup-timeout", type=float, default=180.0)
    ap.add_argument("--port", type=int, default=0,
                    help="HTTP port (default: random loopback port)")
    ap.add_argument("--keep-scratch", action="store_true",
                    help="keep the spawned scratch workspace for diagnosis")
    return ap.parse_args(argv)


def run(args, receipt) -> int:
    """Everything after argument parsing. Returns exit code; receipt is
    mutated in place; the caller's finally block persists it."""
    run_id = args.run_id or time.strftime("bcons-%Y%m%d-%H%M%S")
    args.run_id = run_id
    validate_run_id(run_id)
    validate_bolt_url(args.bolt_url)
    repo_root = Path(__file__).resolve().parent.parent.parent
    backend_dir = args.backend_dir or str(repo_root / "backend")
    if not (Path(backend_dir) / "app" / "main.py").is_file():
        raise Blocked(f"backend dir with app/main.py not found at "
                      f"{backend_dir}")
    files_root = Path(args.files_root).resolve()
    validate_files_root(files_root, repo_root)
    keys = load_keys(Path(args.keys_json))

    # Full-consumer fixture-v2 identity: exact shared key material is
    # REQUIRED (no generic degrading). Maps the harness's role layout:
    # fx-backend-key-0001 = scoped to fx-collection-0001,
    # fx-backend-key-0002 = all-scope read.
    by_id = {k.get("id"): k for k in keys}
    if len(keys) != 2 or set(by_id) != {FX_READ_KEY_ID, FX_MANAGE_KEY_ID}:
        raise Blocked(
            f"keys fixture must contain exactly the fixture-v2 keys "
            f"{FX_READ_KEY_ID} (scoped) and {FX_MANAGE_KEY_ID} (all-scope "
            f"read); got {[k.get('id') for k in keys]}")
    read_key_def = by_id[FX_READ_KEY_ID]
    manage_key_def = by_id[FX_MANAGE_KEY_ID]
    if read_key_def["role"] != "scoped" \
            or read_key_def.get("collection") != FX_SHARED_COLLECTION_ID \
            or read_key_def["plaintext"] != FX_READ_KEY_PLAINTEXT:
        raise Blocked(
            f"{FX_READ_KEY_ID} must be role=scoped, collection="
            f"{FX_SHARED_COLLECTION_ID}, plaintext={FX_READ_KEY_PLAINTEXT}")
    if manage_key_def["role"] != "read" \
            or manage_key_def["plaintext"] != FX_MANAGE_KEY_PLAINTEXT:
        raise Blocked(
            f"{FX_MANAGE_KEY_ID} must be role=read (all-scope), plaintext="
            f"{FX_MANAGE_KEY_PLAINTEXT}")
    receipt["fixture_keys"] = [
        {"id": k["id"], "role": k["role"],
         "collection": k.get("collection"),
         "plaintext": "<provided-by-main-harness>",
         **key_validation_shape(k["plaintext"])}
        for k in keys
    ]
    key_ids = [k["id"] for k in keys]

    # Frozen expectations: the full consumer REQUIRES the fixture-v2 ids
    # (no generic pass-as-full).
    doc_id = args.expect_document_id
    if doc_id not in FX_DOCUMENTS:
        raise Blocked(
            f"--expect-document-id {doc_id!r} is not a fixture-v2 document "
            f"({sorted(FX_DOCUMENTS)}); the full consumer does not run "
            f"generic degraded expectations")
    if args.expect_collection_id != FX_SHARED_COLLECTION_ID:
        raise Blocked(
            f"--expect-collection-id {args.expect_collection_id!r} must be "
            f"the fixture-v2 collection {FX_SHARED_COLLECTION_ID!r}")
    if args.app_storage_id and args.app_storage_id != FX_APP_ID:
        raise Blocked(
            f"--app-storage-id {args.app_storage_id!r} is not the fixture "
            f"app {FX_APP_ID!r}; no generic app-storage expectations exist")
    blob_path = files_root / FX_DOCUMENTS[doc_id]["file_path"]
    if not blob_path.is_file():
        raise Blocked(
            f"independent fixture blob missing for frozen checks: "
            f"{blob_path}")

    chat_script = None
    chat_state_dir = None
    if args.chat_consumer:
        chat_script = Path(args.chat_consumer)
        if not chat_script.is_absolute():
            raise Blocked(f"--chat-consumer must be an absolute path: "
                          f"{chat_script}")
        if not chat_script.is_file():
            raise Blocked(f"chat consumer script not found: {chat_script}")
        chat_state_dir = Path(args.chat_state_dir or (files_root / "chat"))
        if not chat_state_dir.is_dir():
            raise Blocked(f"chat state dir missing: {chat_state_dir}")

    # Scratch claim (atomic; marked; never a user path)
    base = Path(os.environ.get("CORTEX_BACKEND_CONSUMER_HOME",
                               Path.home() / ".local/share")) \
        / "cortex-backend-consumer"
    scratch = base / run_id
    if scratch.exists():
        raise Blocked(f"scratch path already exists, refusing: {scratch}")
    scratch.mkdir(parents=True)
    marker = scratch / ".cortex-backend-consumer-owned"
    marker.write_text(json.dumps({"run_id": run_id, "pid": os.getpid(),
                                  "created": time.time()}))
    (scratch / "tmp").mkdir()
    workdir = scratch / "workdir"
    workdir.mkdir()
    out_dir = Path(args.out) if args.out else repo_root / "qa/restore/output" \
        / f"backend-consumer-{run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt["scratch"] = {"path": str(scratch), "owned_marker": str(marker)}

    # Isolated server cwd: symlink the restored roots into place so the
    # app's relative paths and stored relative file_path values resolve.
    for sub in ("uploads", "custom_inputs", "skills", "apps"):
        src = files_root / sub
        if src.is_dir():
            (workdir / sub).symlink_to(src, target_is_directory=True)

    tripwire = ProviderTripwire()
    env = build_server_env(args, scratch, tripwire.sink_url, backend_dir)
    receipt["provider_tripwire"] = {
        "sink": tripwire.sink_url,
        "rule": "any connection accepted by the sink = attempted provider "
                "call = run failure",
    }
    guard = scratch / "netguard.py"
    write_network_guard(guard)
    port = args.port or free_loopback_port()
    entry = scratch / "uvicorn_entry.py"
    write_uvicorn_entry(entry, port)
    server_log = scratch / "server.log"
    base_url = f"http://127.0.0.1:{port}"
    receipt["server"] = {"base_url": base_url, "port": port,
                         "log": str(server_log)}

    result = 1
    proc = None
    server_spawned = False
    phase = "pre-boot-capture"
    try:
        # ---- pre-boot capture (read-only bolt) ----
        pre = safe_capture(args, "pre-boot")
        phase = "boot"
        receipt["pre_boot_graph"] = {
            "node_count": len(pre["nodes"]),
            "rel_count": len(pre["rels"]),
            "constraints": sorted(c["name"] for c in pre["constraints"]),
            "indexes": sorted(i["name"] for i in pre["indexes"]),
        }
        expected_ec = expected_entity_counts(pre)
        if expected_ec:
            receipt["acceptance_delta"] = {
                "entity_count_backfill": {
                    "expected": expected_ec,
                    "source": "neo4j_service."
                              "backfill_degraded_document_signals "
                              "(count(DISTINCT e) over HAS_CHUNK->Chunk-"
                              "MENTIONS->Entity for completed docs with "
                              "entity_count NULL)",
                    "reason": "one-time idempotent bootstrap backfill for "
                              "fixture documents predating the field; "
                              "expected computed INDEPENDENTLY from the "
                              "pre-boot captured topology",
                    "accepted_only_if": "field absent before, exact "
                                        "nonnegative expected count after, "
                                        "no other Document field touched",
                },
                "constraint_backing_range_indexes": {
                    "names": sorted(STARTUP_CONSTRAINT_NAMES),
                    "source": "Neo4j 5 backing index per CREATE CONSTRAINT "
                              "... IS UNIQUE (initialize_schema)",
                    "reason": "fresh-bootstrap schema creation; accepted "
                              "ONLY with exact type/entityType/labels/"
                              "properties pairing to the same-named "
                              "accepted constraint, never name-catchall",
                },
                "stats_seeded_meta": {
                    "keys": sorted(STATS_SEEDED_META_KEYS),
                    "retained": True,
                },
            }

        # ---- spawn the real app ----
        import httpx  # deferred: unit tests reach this path only with deps
        with open(server_log, "ab") as logf:
            proc = subprocess.Popen(
                [sys.executable, str(guard), str(entry)],
                cwd=str(workdir), env=env, stdout=logf, stderr=logf,
            )
        server_spawned = True
        receipt["server"]["pid"] = proc.pid
        with httpx.Client(timeout=30.0, base_url=base_url) as client:
            health = wait_for_health(base_url, args.startup_timeout, client)
            receipt["startup"] = {
                "module": "app.main:app",
                "backend_dir": backend_dir,
                "lifespan": "real (uvicorn lifespan=on)",
                "baseUrl": base_url,
                "health_ok": health["ok"],
                "health_body": health.get("body"),
                "last_error": health.get("last"),
            }
            if not health["ok"]:
                receipt["startup"]["server_log_tail"] = server_log.read_text(
                    errors="replace")[-4000:]
                raise RunFailure(
                    f"server did not reach healthy schema_initialized "
                    f"state: {health.get('last')}")
            banner = f"Uvicorn running on http://127.0.0.1:{port}"
            log_head = server_log.read_text(errors="replace")
            if banner not in log_head:
                raise RunFailure(
                    "server identity guard: uvicorn banner for this run's "
                    f"own port not found in this run's server log ({banner})")
            receipt["server"]["identity"] = {
                "pid": proc.pid, "port": port,
                "banner_verified": True,
            }
            phase = "probes"

            probes = []
            hdr = lambda k: {"X-API-Key": k}  # noqa: E731
            admin_hdr = hdr(args.admin_key)
            failures = []

            def run_probe(p):
                ensure_alive(proc)
                probes.append(p)
                receipt["probes"] = probes  # incremental: survive a crash
                if probe_failed(p):
                    if p["checks_failed"]:
                        failures.extend(
                            f"{p['name']} [HTTP {p['status']}]: {f}"
                            for f in p["checks_failed"])
                    else:
                        failures.append(
                            f"{p['name']}: HTTP {p['status']} != expected "
                            f"{p['expect_status']}")

            # /health exact shape
            run_probe(probe(client, "health", "GET", "/health",
                            checks={"healthy": health_check}))

            # Auth boundary: missing + bad key must be exactly 401 (no 200)
            run_probe(probe(client, "unauthenticated-401", "GET",
                            "/api/documents", expect_status=401))
            run_probe(probe(client, "bad-key-401", "GET", "/api/documents",
                            headers=hdr("fx-not-a-real-key-000"),
                            expect_status=401))

            # Env admin key: full fixture visibility (exact)
            run_probe(probe(client, "admin-documents", "GET",
                            "/api/documents", headers=admin_hdr,
                            checks={"fixture_documents": docs_list_check(
                                exact=FX_ALL_DOCUMENTS)}))
            run_probe(probe(client, "admin-stats", "GET", "/api/stats",
                            headers=admin_hdr,
                            checks={"fixture_counts":
                                    stats_check(FX_ADMIN_STATS)}))

            # Real generated keys through the real auth chain (fixture-v2
            # identity already enforced above)
            scoped_hdr = hdr(read_key_def["plaintext"])
            manage_hdr = hdr(manage_key_def["plaintext"])

            # Scope-POSITIVE and scope-NEGATIVE on the shared fixtures
            run_probe(probe(client, "scoped-documents-exact", "GET",
                            "/api/documents", headers=scoped_hdr,
                            checks={"exact_scope": docs_list_check(
                                present=(doc_id,),
                                absent=FX_SCOPE_DENIED_DOCUMENTS,
                                exact=(doc_id,))}))
            run_probe(probe(client, "scoped-collections-exact", "GET",
                            "/api/collections", headers=scoped_hdr,
                            checks={"exact_scope": collections_check(
                                present=(args.expect_collection_id,),
                                absent=("col-crr-1", "col-crr-2"),
                                exact=FX_SCOPED_VISIBLE_COLLECTIONS)}))
            run_probe(probe(client, "scoped-stats", "GET", "/api/stats",
                            headers=scoped_hdr,
                            checks={"fixture_counts":
                                    stats_check(FX_SCOPED_STATS)}))
            # Negative controls: out-of-scope reads must be 403
            run_probe(probe(client, "scoped-denied-document-403", "GET",
                            "/api/documents/doc-crr-2", headers=scoped_hdr,
                            expect_status=403))
            run_probe(probe(client, "scoped-denied-collection-403", "GET",
                            "/api/collections/col-crr-2", headers=scoped_hdr,
                            expect_status=403))

            # Permission-tier boundary (real auth contract: GET endpoints
            # require READ — auth_service.require_read_permission; the
            # fixture's fx-backend-key-0002 is manage-only, so it must NOT
            # read. Scope-all positive visibility is proven via the env
            # admin key above.)
            run_probe(probe(client, "manage-only-documents-403", "GET",
                            "/api/documents", headers=manage_hdr,
                            expect_status=403))
            run_probe(probe(client, "manage-only-collections-403", "GET",
                            "/api/collections", headers=manage_hdr,
                            expect_status=403))

            # Fixture document detail/content/bytes vs independent blob.
            # Source verification: the ACTUAL restored source bytes are
            # verified through THIS runner's own HTTP probes (detail/content/
            # file) against the independent fixture blob, expecting NONEMPTY
            # content; no LLM/model call exists anywhere on these paths.
            blob_bytes = blob_path.read_bytes()
            blob_text = blob_bytes.decode("utf-8")
            assert blob_bytes, "fixture blob unexpectedly empty"
            assert blob_text.strip(), "fixture blob text unexpectedly empty"
            receipt["fixture_blob"] = {
                "path": str(blob_path),
                "sha256": sha256_bytes(blob_bytes),
                "bytes": len(blob_bytes),
                "role": "independent expected restored blob file",
            }
            receipt["source_verification"] = {
                "method": "own HTTP probes (fx-document-detail, "
                          "fx-document-content, fx-document-file-bytes) "
                          "against the independent restored blob",
                "expected_nonempty": True,
                "llm_or_model_calls": 0,
                "artifact_touched": "files-root COPY only (never the frozen "
                                    "original restored volumes); app "
                                    "storage read on a byte-identical "
                                    "second copy",
            }
            run_probe(probe(client, "fx-document-detail", "GET",
                            f"/api/documents/{doc_id}",
                            headers=scoped_hdr,
                            checks={"fixture_fields":
                                    fx_detail_check(doc_id, blob_bytes)}))
            run_probe(probe(client, "fx-document-content", "GET",
                            f"/api/documents/{doc_id}/content",
                            headers=scoped_hdr,
                            checks={"fixture_content":
                                    fx_content_check(doc_id, blob_text)}))
            run_probe(probe(client, "fx-document-file-bytes", "GET",
                            f"/api/documents/{doc_id}/file",
                            headers=scoped_hdr,
                            checks={"fixture_bytes":
                                    fx_file_check(blob_bytes)}))

            # ---- chat consumer phase (server still alive) ----
            phase = "chat"
            if chat_script is not None:
                ensure_alive(proc)
                chat_raw, chat_exec, chat_failures = run_chat_consumer(
                    chat_script, chat_state_dir, base_url, args.admin_key,
                    blob_path, scratch / "tmp", args.chat_timeout,
                    app_node=args.chat_app_node,
                    chat_work_dir=scratch / "chat-work",
                    chat_tmpdir=scratch / "tmp",
                    min_free_mb=args.chat_min_free_mb)
                # receipt.chat = RAW parsed chat JSON (verbatim, for the main
                # validator); execution envelope kept separately.
                receipt["chat"] = chat_raw if chat_raw is not None else None
                receipt["chat_execution"] = chat_exec
                failures.extend(f"chat: {f}" for f in chat_failures)
                ensure_alive(proc)

            # ---- final capture (AFTER chat) + enforced boot/probe delta ----
            phase = "final-capture"
            post = safe_capture(args, "final")
            diff = diff_capture(pre, post)
            receipt["boot_deltas"] = diff
            delta = classify_delta(diff, key_ids,
                                   expected_entity_counts=expected_ec)
            # boot_deltas_accepted is true ONLY after the strict allowlist
            # passed (zero violations); it is never asserted on a recorded
            # note.
            receipt["boot_deltas_accepted"] = not delta["violations"]
            receipt["delta_enforcement"] = {
                "rule": "explicit allowlist derived from real startup code: "
                        "IF-NOT-EXISTS schema ensure (initialize_schema) + "
                        "default Collection MERGE + generated-key usage "
                        "bookkeeping (APIKey last_used/total_requests/"
                        "error fields, APIKeyUsageLog + HAS_USAGE); any "
                        "removal, definition drift or out-of-scope change "
                        "FAILS the run",
                "accepted": delta["accepted"],
                "violations": delta["violations"],
            }
            failures.extend(f"delta: {v}" for v in delta["violations"])

            # ---- labeled direct supplements (asserted) ----
            ft_term = args.fulltext_term
            if ft_term:
                ft = fulltext_probe(args.bolt_url, args.neo4j_user,
                                    args.neo4j_password, args.fulltext_index,
                                    ft_term, FX_FULLTEXT_CHUNK_IDS)
                receipt["fulltext_direct"] = ft
                failures.extend(f"fulltext: {v}" for v in ft["violations"])
            if args.app_storage_id == FX_APP_ID:
                st = appstorage_probe(files_root, args.app_storage_id,
                                      scratch)
                receipt["appstorage_direct"] = st
                failures.extend(f"appstorage: {v}" for v in st["violations"])
            receipt["direct_supplement_labels"] = {
                "fulltext": "direct Neo4j read (HTTP /api/search requires a "
                            "query-embedding provider call, which is banned "
                            "in this consumer); hits must include all three "
                            "fixture chunks",
                "appstorage": "direct AppStorageService read on a "
                              "byte-identical copy of the restored "
                              "storage.sqlite; keys/values/updated_at must "
                              "equal the fixture rows and an independent "
                              "immutable read must agree",
            }

            # ---- tripwire verdict: any provider call attempt fails ----
            if tripwire.hits:
                receipt["provider_tripwire"]["hits"] = tripwire.hits
                failures.append(
                    f"provider tripwire: {len(tripwire.hits)} unexpected "
                    f"provider call attempt(s) recorded at the loopback "
                    f"sink (no provider may be contacted)")

            receipt["probes"] = probes
            if failures:
                receipt["failures"] = failures
                result = 1
            else:
                receipt["failures"] = []
                result = 0
    except (Blocked, RunFailure) as e:
        # Diagnosed block/failure: the receipt must carry the nonempty
        # failure diagnostic BEFORE the finally-block flush.
        receipt["failures"] = receipt.get("failures", []) + [str(e)]
        receipt["phase"] = receipt.get("phase", phase)
        result = 1
        raise
    except BaseException as e:
        # Unexpected runner/app failure: the receipt MUST carry the actual
        # diagnostic and nonempty failures with the correct phase — this is
        # a real failure (exit 1), never an implied boot.
        phase = f"{phase} (unexpected {type(e).__name__})"
        receipt["failures"] = receipt.get("failures", []) + [
            f"unexpected runner exception in phase {phase!r}: "
            f"{type(e).__name__}: {e}"]
        receipt["phase"] = phase
        result = 1
    finally:
        tripwire.close()
        # Cleanup facts, never implied: server_spawned reflects whether this
        # run actually spawned uvicorn; server_stopped is only True after a
        # real stop of a really-spawned server.
        receipt["phase"] = receipt.get("phase", phase)
        receipt["cleanup"] = {"server_spawned": server_spawned,
                              "server_stopped": False,
                              "tripwire_hits": len(tripwire.hits),
                              "scratch": str(scratch),
                              "scratch_removed": False}
        receipt_path = out_dir / "backend-consumer-receipt.json"
        # Flush BEFORE cleanup so the actual diagnostic survives even a
        # cleanup failure.
        receipt_path.write_text(_dump_receipt(receipt))
        receipt["_receipt_persisted"] = True
        print(f"receipt: {receipt_path}")
        if proc is not None:
            try:
                stop_server(proc)
                receipt["cleanup"]["server_stopped"] = True
            except Exception as e:  # pragma: no cover - defensive
                receipt["cleanup"]["server_stopped"] = False
                receipt.setdefault("failures", []).append(
                    f"cleanup: server stop error: {e}")
                result = max(result, 1) if isinstance(result, int) else 1
        if result == 0 and not args.keep_scratch:
            # Remove ONLY the marked scratch this run created.
            if marker.is_file() and scratch.resolve().is_relative_to(
                    base.resolve()):
                shutil.rmtree(scratch, ignore_errors=True)
                receipt["cleanup"]["scratch_removed"] = True
        receipt_path.write_text(_dump_receipt(receipt))
    return result


def _dump_receipt(receipt: dict) -> str:
    """JSON receipt text; keys starting with '_' are control markers, never
    part of the artifact."""
    return json.dumps(
        {k: v for k, v in receipt.items() if not k.startswith("_")},
        indent=1, sort_keys=True)


def persist_receipt(receipt: dict, args) -> None:
    """Receipt preservation on every path (preflight block, startup failure,
    probe failure, cleanup). Best effort: an unwritable out location must not
    mask the real exit code."""
    try:
        run_id = getattr(args, "run_id", None) or "unknown"
        out_dir = Path(args.out) if getattr(args, "out", None) else \
            Path(__file__).resolve().parent.parent.parent \
            / "qa/restore/output" / f"backend-consumer-{run_id}"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "backend-consumer-receipt.json"
        if receipt.get("_receipt_persisted"):
            return  # run()'s finally already wrote the full receipt
        path.write_text(_dump_receipt(receipt))
        print(f"receipt: {path}")
    except Exception as e:  # pragma: no cover - defensive
        print(f"receipt persistence failed: {e}", file=sys.stderr)


def main(argv=None) -> int:
    if argv is None:
        argv = list(sys.argv[1:])
    if argv[:1] == ["selftest"]:
        return selftest()
    args = parse_args(argv)
    receipt = {
        "runner": "qa/restore/backend-consumer.py",
        "runner_version": RUNNER_VERSION,
        "claim": "real FastAPI app import + real uvicorn lifespan + real "
                 "Neo4j on the restored fixture; HTTP boundary reads; no "
                 "provider/model calls; production source untouched; "
                 "enforced boot-delta allowlist; integrated chat consumer "
                 "phase against the live backend",
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        receipt["dependency_versions"] = preflight()
        return run(args, receipt)
    except Blocked as e:
        receipt["blocked"] = str(e)
        persist_receipt(receipt, args)
        print(f"FATAL (blocked): {e}", file=sys.stderr)
        return 2
    except RunFailure as e:
        receipt["failures"] = receipt.get("failures", []) + [str(e)]
        persist_receipt(receipt, args)
        print(f"FATAL: {e}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Selftest: wrong-body probe failures + out-of-scope delta control
# (dependency-free; frozen BEFORE any runtime trial)
# ---------------------------------------------------------------------------

def selftest() -> int:
    ok = True

    def check(label, fn):
        nonlocal ok
        try:
            fn()
            print(f"  ok   {label}")
        except AssertionError as e:
            print(f"  FAIL {label}: {e}")
            ok = False

    # -- probe engine: wrong bodies must FAIL their checks --
    def wrong_body_fails(name, probe_fn):
        def fn():
            result = probe_fn()
            assert probe_failed(result), \
                f"{name}: wrong body did NOT fail the checks " \
                f"({result.get('checks_failed')})"
            assert result["checks_failed"], \
                f"{name}: no recorded check failures"
        return fn

    check("probe: wrong health body fails",
          wrong_body_fails("health-wrong", lambda: probe(
              _FakeClient({("GET", "/health"): _FakeResponse(
                  json_body={"status": "unhealthy",
                             "schema_initialized": False,
                             "neo4j_connected": True})}),
              "health", "GET", "/health", checks={"healthy": health_check})))
    check("probe: empty documents body fails (empty selection)",
          wrong_body_fails("docs-empty", lambda: probe(
              _FakeClient({("GET", "/d"): _FakeResponse(
                  json_body={"documents": [], "total": 0})}),
              "docs", "GET", "/d",
              checks={"c": docs_list_check(exact=FX_ALL_DOCUMENTS)})))
    check("probe: scope leak fails",
          wrong_body_fails("docs-leak", lambda: probe(
              _FakeClient({("GET", "/d"): _FakeResponse(json_body={
                  "documents": [{"id": "fx-source-0001"},
                                {"id": "doc-crr-2"}],
                  "total": 2})}),
              "docs", "GET", "/d",
              checks={"c": docs_list_check(
                  absent=FX_SCOPE_DENIED_DOCUMENTS)})))
    check("probe: wrong detail fields fail",
          wrong_body_fails("detail-wrong", lambda: probe(
              _FakeClient({("GET", "/x"): _FakeResponse(json_body={
                  "id": "fx-source-0001", "filename": "wrong.md",
                  "file_type": "text/markdown", "file_size": 1,
                  "file_path": "uploads/other", "processing_status": "done",
                  "chunk_count": 0, "collection_id": "nope"})}),
              "detail", "GET", "/x",
              checks={"c": fx_detail_check("fx-source-0001", b"x")})))
    check("probe: wrong file bytes fail",
          wrong_body_fails("file-wrong", lambda: probe(
              _FakeClient({("GET", "/f"): _FakeResponse(
                  content=b"tampered", json_body=None,
                  content_type="text/markdown")}),
              "file", "GET", "/f",
              checks={"c": fx_file_check(b"expected-bytes")})))
    check("probe: wrong fulltext-free full_content fails",
          wrong_body_fails("content-wrong", lambda: probe(
              _FakeClient({("GET", "/c"): _FakeResponse(json_body={
                  "full_content": "other text", "chunks": []})}),
              "content", "GET", "/c",
              checks={"c": fx_content_check("fx-source-0001", "fixture")})))
    check("probe: check returning False fails",
          wrong_body_fails("false-return", lambda: probe(
              _FakeClient({("GET", "/z"): _FakeResponse(json_body={})}),
              "z", "GET", "/z", checks={"c": lambda b, r: False})))
    def right_body_passes():
        r = probe(_FakeClient({("GET", "/h"): _FakeResponse(json_body={
            "status": "healthy", "schema_initialized": True,
            "neo4j_connected": True})}),
            "h", "GET", "/h", checks={"healthy": health_check})
        assert not probe_failed(r), \
            f"right body wrongly failed: {r['checks_failed']}"

    check("probe: right bodies pass", right_body_passes)

    # -- delta classifier: out-of-scope control must be flagged --
    def out_of_scope_delta_flagged():
        before = {"nodes": [{"labels": ["Document"], "key": "doc-crr-1",
                             "props": {"filename": "a.md"}}],
                  "rels": [], "constraints": [], "indexes": []}
        after = {"nodes": [{"labels": ["Document"], "key": "doc-crr-1",
                            "props": {"filename": "b.md"}},
                           {"labels": ["Document"], "key": "doc-x",
                            "props": {}}],
                 "rels": [{"la": ["Document"], "ka": "doc-crr-1",
                           "rt": "RELATED_TO", "rp": {},
                           "lb": ["Entity"], "kb": "E"}],
                 "constraints": [], "indexes": []}
        diff = diff_capture(before, after)
        out = classify_delta(diff, ["fx-backend-key-0001"])
        v = " ".join(out["violations"])
        assert out["violations"], "out-of-scope delta was NOT flagged"
        assert "doc-crr-1" in v and "doc-x" in v and "RELATED_TO" in v, \
            f"out-of-scope control insufficient: {v}"

    check("delta: out-of-scope node/rel changes flagged",
          out_of_scope_delta_flagged)

    def allowed_delta_accepted():
        before = {"nodes": [{"labels": ["APIKey"], "key": "fx-backend-key-0001",
                             "props": {"key_prefix": "fx-synthetic"}}],
                  "rels": [], "constraints": [], "indexes": []}
        after = {"nodes": [
            {"labels": ["APIKey"], "key": "fx-backend-key-0001",
             "props": {"key_prefix": "fx-synthetic", "total_requests": 5,
                       "last_used_at": "now"}},
            {"labels": ["APIKeyUsageLog"],
             "key": "fx-backend-key-0001@2026-10-01",
             "props": {"key_id": "fx-backend-key-0001", "date": "2026-10-01",
                       "request_count": 1, "ep_documents": 1}},
            {"labels": ["Collection"], "key": "default",
             "props": {"id": "default", "name": "Default"}}],
            "rels": [{"la": ["APIKey"], "ka": "fx-backend-key-0001",
                      "rt": "HAS_USAGE", "rp": {},
                      "lb": ["APIKeyUsageLog"],
                      "kb": "fx-backend-key-0001@2026-10-01"}],
            "constraints": [], "indexes": []}
        diff = diff_capture(before, after)
        out = classify_delta(diff, ["fx-backend-key-0001"])
        assert not out["violations"], \
            f"allowed delta wrongly flagged: {out['violations']}"

    check("delta: allowed startup/usage deltas accepted", allowed_delta_accepted)

    def seeded_meta_control():
        before = {"nodes": [], "rels": [], "constraints": [], "indexes": []}
        after = {"nodes": [
            {"labels": ["SystemMeta"], "key": "last_relationship_analysis_at",
             "props": {"key": "last_relationship_analysis_at",
                       "value": "2026-10-01T00:00:00Z"}},
            {"labels": ["SystemMeta"], "key": "unknown-meta",
             "props": {"key": "unknown-meta", "value": "x"}}],
            "rels": [], "constraints": [], "indexes": []}
        out = classify_delta(diff_capture(before, after), ["k1"])
        v = " ".join(out["violations"])
        assert "unknown-meta" in v, "foreign SystemMeta not flagged"
        assert "last_relationship_analysis_at" not in v, \
            "stats-seeded SystemMeta wrongly flagged"

    check("delta: stats-seeded SystemMeta allowlist exact", seeded_meta_control)

    def fresh_bootstrap_controls():
        base_cons = [
            {"name": "collection_id", "type": "UNIQUENESS",
             "entityType": "NODE", "labelsOrTypes": ["Collection"],
             "properties": ["id"]},
            {"name": "skill_id", "type": "UNIQUENESS",
             "entityType": "NODE", "labelsOrTypes": ["Skill"],
             "properties": ["skill_id"]},
        ]
        before = {"nodes": [
            {"labels": ["Document"], "key": "fx-source-0001",
             "props": {"processing_status": "completed",
                       "filename": "fixture-source.md"}}],
            "rels": [{"la": ["Document"], "ka": "fx-source-0001",
                      "rt": "HAS_CHUNK", "rp": {},
                      "lb": ["Chunk"], "kb": "fx-source-0001-chunk-0"},
                     {"la": ["Chunk"], "ka": "fx-source-0001-chunk-0",
                      "rt": "MENTIONS", "rp": {},
                      "lb": ["Entity"], "kb": "Rehearsal Alpha"}],
            "constraints": [], "indexes": []}
        after = json.loads(json.dumps(before))
        after["nodes"][0]["props"]["entity_count"] = 1
        after["constraints"] = json.loads(json.dumps(base_cons))
        after["indexes"] = [
            {"name": "collection_id", "type": "RANGE", "entityType": "NODE",
             "labelsOrTypes": ["Collection"], "properties": ["id"],
             "indexProvider": "native-btree"},
            {"name": "skill_id", "type": "RANGE", "entityType": "NODE",
             "labelsOrTypes": ["Skill"], "properties": ["skill_id"],
             "indexProvider": "native-btree"},
        ]
        exp = expected_entity_counts(before)
        assert exp == {"fx-source-0001": 1}, f"derived count wrong: {exp}"
        out = classify_delta(diff_capture(before, after), ["k1"],
                             expected_entity_counts=exp)
        assert not out["violations"], \
            f"healthy fresh bootstrap wrongly flagged: {out['violations']}"
        assert out["accepted"]["entity_count_backfilled"] == \
            {"fx-source-0001": 1}
        assert sorted(out["accepted"]["constraint_backing_indexes"]) == \
            ["collection_id", "skill_id"]

        # unpaired wrong RANGE index: right name, WRONG labels -> denied
        bad = json.loads(json.dumps(after))
        bad["indexes"].append(
            {"name": "collection_id", "type": "RANGE",
             "entityType": "NODE", "labelsOrTypes": ["Document"],
             "properties": ["id"], "indexProvider": "native-btree"})
        out = classify_delta(diff_capture(before, bad), ["k1"],
                             expected_entity_counts=exp)
        assert any("collection_id" in x for x in out["violations"]), \
            f"unpaired RANGE index accepted: {out['violations']}"

        # wrong derived count -> denied
        wrong = json.loads(json.dumps(after))
        wrong["nodes"][0]["props"]["entity_count"] = 5
        out = classify_delta(diff_capture(before, wrong), ["k1"],
                             expected_entity_counts=exp)
        assert any("entity_count" in x and "5" in x
                   for x in out["violations"]), \
            f"wrong derived count accepted: {out['violations']}"

        # arbitrary Document field -> denied
        arb = json.loads(json.dumps(after))
        arb["nodes"][0]["props"]["filename"] = "tampered.md"
        out = classify_delta(diff_capture(before, arb), ["k1"],
                             expected_entity_counts=exp)
        assert any("tampered" in x or "filename" in x
                   for x in out["violations"]), \
            f"arbitrary Document field accepted: {out['violations']}"

        # pre-existing (mutated) entity_count cannot pass as bootstrap
        mut = json.loads(json.dumps(before))
        mut["nodes"][0]["props"]["entity_count"] = 99
        out = classify_delta(diff_capture(mut, after), ["k1"],
                             expected_entity_counts={"fx-source-0001": 1})
        assert out["violations"], \
            "pre-existing entity_count mutation passed as bootstrap"

    check("delta: fresh bootstrap indexes + entity_count exact controls",
          fresh_bootstrap_controls)

    def fixture_loss_flagged():
        before = {"nodes": [{"labels": ["Collection"], "key": "col-crr-2",
                             "props": {"id": "col-crr-2"}}],
                  "rels": [{"la": ["Collection"], "ka": "col-crr-2",
                            "rt": "CONTAINS", "rp": {},
                            "lb": ["Document"], "kb": "doc-crr-2"}],
                  "constraints": [{"name": "collection_id", "type": "UNIQUENESS",
                                   "entityType": "NODE",
                                   "labelsOrTypes": ["Collection"],
                                   "properties": ["id"]}],
                  "indexes": []}
        after = {"nodes": [], "rels": [],
                 "constraints": [{"name": "collection_id", "type": "UNIQUENESS",
                                  "entityType": "NODE",
                                  "labelsOrTypes": ["Collection"],
                                  "properties": ["id"]}],
                 "indexes": []}
        diff = diff_capture(before, after)
        out = classify_delta(diff, ["fx-backend-key-0001"])
        v = " ".join(out["violations"])
        assert "col-crr-2" in v and "doc-crr-2" in v and "CONTAINS" in v, \
            f"fixture loss not fully flagged: {v}"

    check("delta: fixture node/rel loss flagged", fixture_loss_flagged)

    # -- chat receipt contract (real-shaped raw JSON, exact ids) --
    BASE = "http://127.0.0.1:9"

    def real_chat_json(**over):
        receipt = {
            "ok": True,
            "failures": [],
            "upstream": {"mode": "isolated-backend", "url": BASE},
            "networkTripwire": {"clean": True, "blockedAttempts": []},
            "cleanup": {"stateDirUnchanged": True},
            "checks": [
                {"id": "proxy-isolated-backend-collections-200-scoped",
                 "ok": True},
                {"id": "proxy-isolated-backend-citation-content-exact",
                 "ok": True},
                {"id": "login-wrong-password-401", "ok": True},
            ],
        }
        receipt.update(over)
        return receipt

    def chat_contract():
        assert validate_chat_receipt(real_chat_json(), BASE) == [], \
            "valid real-shaped chat receipt rejected"

        def rejects(mutate, label):
            bad = real_chat_json()
            mutate(bad)
            failures = validate_chat_receipt(bad, BASE)
            assert failures, f"wrong chat receipt NOT rejected: {label}"

        rejects(lambda r: r["upstream"].__setitem__(
            "url", "http://127.0.0.1:8"), "wrong url")
        rejects(lambda r: r["upstream"].__setitem__(
            "mode", "loopback-sink"), "wrong mode")
        rejects(lambda r: r["checks"][0].__setitem__("ok", False),
                "required collection check false")
        rejects(lambda r: r["checks"][0].__setitem__("reportOnly", True),
                "required collection check reportOnly")
        rejects(lambda r: r["checks"].pop(1), "missing citation check")
        # Legacy file-route id is NOT accepted: a receipt that still carries
        # 'proxy-isolated-backend-citation-file-exact' instead of the
        # content id must be rejected (the old oracle asserted a forbidden/
        # 404 file route).
        rejects(lambda r: r["checks"].__setitem__(
            1, {"id": "proxy-isolated-backend-citation-file-exact",
                "ok": True}), "legacy file-route citation id")
        rejects(lambda r: r["networkTripwire"].__setitem__("clean", False),
                "network tripwire not clean")
        rejects(lambda r: r["networkTripwire"].__setitem__(
            "blockedAttempts", [{"peer": "10.0.0.9"}]),
            "network tripwire recorded attempts")
        rejects(lambda r: r["cleanup"].__setitem__(
            "stateDirUnchanged", False), "source state changed")
        rejects(lambda r: r.__setitem__("ok", False), "ok false")
        rejects(lambda r: r.__setitem__("failures", ["x"]), "failures")
        rejects(lambda r: r.__setitem__("checks", []), "empty selection")

    check("chat: hard receipt contract enforced (exact ids, raw shape)",
          chat_contract)

    def single_json():
        obj, err = parse_single_json('{}\n')
        assert err is None and obj == {}, f"plain object rejected: {err}"
        _, err = parse_single_json('{} trailing')
        assert err, "trailing data accepted"
        _, err = parse_single_json('')
        assert err, "empty stdout accepted"
        _, err = parse_single_json('{"a":1}\nlog line')
        assert err, "mixed stdout accepted"

    check("chat: exactly-one-JSON stdout enforced", single_json)

    # -- guard validations --
    def guards():
        for bad in ("../evil", "a/b", "", ".hidden", "..", "a b"):
            try:
                validate_run_id(bad)
            except Blocked:
                continue
            raise AssertionError(f"run-id {bad!r} accepted")
        validate_run_id("restore-final-20261001")
        for bad in ("bolt://10.0.0.5:7687", "http://127.0.0.1:1",
                    "bolt://example.com:7687"):
            try:
                validate_bolt_url(bad)
            except Blocked:
                continue
            raise AssertionError(f"bolt url {bad!r} accepted")
        validate_bolt_url("bolt://127.0.0.1:7687")

    check("guards: run-id traversal + non-loopback bolt rejected", guards)

    print("SELFTEST " + ("PASS (wrong-body probes, out-of-scope delta "
                         "control, chat contract, guards)" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
