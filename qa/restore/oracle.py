#!/usr/bin/env python3
"""Oracle for the disposable whole-stack restore rehearsal.

Captures and compares Cortex persistent state without internal Neo4j
identity: graph records keyed by domain identifiers, per-file SHA-256
manifests for the five file roots, and per-app SQLite rows. Also seeds the
deterministic fixture (graph cypher + file tree + app SQLite) and runs a
hermetic selftest of the comparison logic.

Subcommands:
    gen-fixture      write fixture.cypher + fixture file tree (deterministic)
    capture-graph    query a running neo4j container into a JSON capture
    capture-files    walk a directory tree into a JSON manifest
    capture-sqlite   dump all rows of a SQLite file into a JSON capture
    compare          full oracle verdict (pass or structured rejection)
    selftest         hermetic checks of the comparison logic
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

DOM = "crr"
FIXED_TS = "2026-10-01T00:00:00Z"
FIXTURE_VERSION = "storage-v2-consumers-ready"

READ_KEY_ID = "fx-backend-key-0001"
MANAGE_KEY_ID = "fx-backend-key-0002"
READ_KEY_PLAINTEXT = "fx-synthetic-backend-read-key-0001"
MANAGE_KEY_PLAINTEXT = "fx-synthetic-backend-content-key-0002"
SHARED_COLLECTION_ID = "fx-collection-0001"
CITATION_SOURCE_ID = "fx-source-0001"
CITATION_SOURCE_TITLE = "Fixture Source"
CITATION_SOURCE_FILENAME = "fixture-source.md"
CITATION_SOURCE_CHUNK_ID = "fx-source-0001-chunk-0"

CAPTURE_NODES_QUERY = (
    "MATCH (n) RETURN labels(n) AS labels, "
    "coalesce(n.id, n.task_id, n.name, n.key, n.date) AS key, "
    "properties(n) AS props ORDER BY labels(n)[0], key"
)
CAPTURE_RELS_QUERY = (
    "MATCH (a)-[r]->(b) RETURN labels(a)[0] AS la, "
    "coalesce(a.id, a.task_id, a.name, a.key, a.date) AS ka, "
    "type(r) AS rt, properties(r) AS rp, labels(b)[0] AS lb, "
    "coalesce(b.id, b.task_id, b.name, b.key, b.date) AS kb"
)

SCHEMA_CONSTRAINTS_QUERY = (
    "SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties "
    "RETURN name, type, entityType, labelsOrTypes, properties ORDER BY name"
)
SCHEMA_INDEXES_QUERY = (
    "SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, "
    "indexProvider, options WHERE NOT type IN ['LOOKUP'] "
    "RETURN name, type, entityType, labelsOrTypes, properties, indexProvider, "
    "coalesce(options['indexConfig']['vector.dimensions'], -1) AS vector_dimensions, "
    "coalesce(options['indexConfig']['vector.similarity_function'], '') AS vector_similarity, "
    "coalesce(options['indexConfig']['fulltext.analyzer'], '') AS fulltext_analyzer, "
    "coalesce(options['indexConfig']['fulltext.eventually_consistent'], false) "
    "AS fulltext_eventually_consistent ORDER BY name"
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_manifest(root: Path) -> dict:
    files = {}
    dirs = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        rel = Path(dirpath).relative_to(root)
        dirs.append(rel.as_posix() if str(rel) != "." else ".")
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            files[p.relative_to(root).as_posix()] = {
                "sha256": sha256_file(p),
                "bytes": p.stat().st_size,
            }
    return {"files": files, "dirs": sorted(dirs)}


def capture_files(root: Path, out: Path) -> None:
    out.write_text(json.dumps(walk_manifest(root), indent=1, sort_keys=True))


def capture_sqlite(path: Path, out: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"SQLite fixture missing: {path}")
    # immutable=1 deliberately avoids writes while reading a quiesced snapshot;
    # it must never silently ignore committed data still living in a WAL.
    sidecars = [str(path) + suffix for suffix in ("-wal", "-shm")]
    if any(Path(p).exists() for p in sidecars):
        raise RuntimeError(f"SQLite snapshot is not quiesced (WAL/SHM present): {path}")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        rows = {}
        for t in tables:
            cols = [c[1] for c in conn.execute(f'PRAGMA table_info("{t}")')]
            data = [
                list(r)
                for r in conn.execute(f'SELECT * FROM "{t}" ORDER BY ' + ",".join(
                    f'"{c}"' for c in cols
                ))
            ]
            rows[t] = {"columns": cols, "rows": data}
    finally:
        conn.close()
    out.write_text(json.dumps(rows, indent=1, sort_keys=True))


def run_cypher(container: str, password: str, query: str, database_args=None) -> str:
    cmd = [
        "docker", "exec", "-i", container, "cypher-shell",
        "-u", "neo4j", "-p", password, "--format", "plain",
    ]
    if database_args:
        cmd += database_args
    res = subprocess.run(cmd, input=query, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        raise RuntimeError(f"cypher-shell failed ({res.returncode}): {res.stderr.strip()}")
    return res.stdout


def docker_stdout(container: str, cmd: list) -> str:
    res = subprocess.run(
        ["docker", "exec", container] + cmd,
        capture_output=True, text=True, timeout=300,
    )
    if res.returncode != 0:
        raise RuntimeError(f"docker exec {container} failed: {res.stderr.strip()}")
    return res.stdout


def capture_graph(container: str, password: str, out: Path) -> None:
    stmts = (
        f'CALL apoc.export.json.query("{CAPTURE_NODES_QUERY}", '
        f'"oracle-capture-nodes.json") YIELD file RETURN file;\n'
        f'CALL apoc.export.json.query("{CAPTURE_RELS_QUERY}", '
        f'"oracle-capture-rels.json") YIELD file RETURN file;\n'
    )
    run_cypher(container, password, stmts)

    def read_ndjson(name: str) -> list:
        raw = docker_stdout(
            container, ["cat", f"/var/lib/neo4j/import/{name}"]
        )
        return [json.loads(ln) for ln in raw.splitlines() if ln.strip()]

    nodes = read_ndjson("oracle-capture-nodes.json")
    rels = read_ndjson("oracle-capture-rels.json")
    for n in nodes:
        if n["key"] is None or not n["labels"]:
            raise RuntimeError(
                f"capture found unlabeled/keyless node: {json.dumps(n)} "
                "(fixture must key every node by a domain identifier)"
            )

    def read_schema_plain(query: str) -> list:
        raw = run_cypher(container, password, query)
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        header = [h.strip() for h in lines[0].split(", ")]
        rows = []
        for ln in lines[1:]:
            norm = re.sub(r"\bTRUE\b", "true", ln)
            norm = re.sub(r"\bFALSE\b", "false", norm)
            vals = json.loads("[" + norm + "]")
            rows.append(dict(zip(header, vals)))
        return rows

    constraints = read_schema_plain(SCHEMA_CONSTRAINTS_QUERY)
    indexes = read_schema_plain(SCHEMA_INDEXES_QUERY)
    out.write_text(
        json.dumps(
            {"nodes": nodes, "rels": rels,
             "constraints": constraints, "indexes": indexes},
            indent=1, sort_keys=True,
        )
    )


def node_key(node: dict):
    return (tuple(sorted(node["labels"])), node["key"])


def rel_key(rel: dict):
    return (rel["la"], rel["ka"], rel["rt"],
            json.dumps(rel["rp"], sort_keys=True), rel["lb"], rel["kb"])


def node_props(node: dict) -> dict:
    return node["props"]


def index_nodes(capture: dict, side: str):
    out = {}
    dups = []
    for n in capture["nodes"]:
        k = node_key(n)
        if k in out:
            dups.append({"capture": side, "id": list(k)})
        out[k] = node_props(n)
    return out, dups


def diff_graph(expected: dict, actual: dict) -> list:
    diffs = []
    e_nodes, e_dups = index_nodes(expected, "expected")
    a_nodes, a_dups = index_nodes(actual, "actual")
    diffs += [{"kind": "duplicate_node", **d} for d in e_dups + a_dups]
    for k in sorted(e_nodes.keys() - a_nodes.keys(), key=str):
        diffs.append({"kind": "missing_node", "id": list(k)})
    for k in sorted(a_nodes.keys() - e_nodes.keys(), key=str):
        diffs.append({"kind": "extra_node", "id": list(k)})
    for k in sorted(e_nodes.keys() & a_nodes.keys(), key=str):
        if e_nodes[k] != a_nodes[k]:
            changed = sorted(
                [p for p in e_nodes[k] if e_nodes[k].get(p) != a_nodes[k].get(p)]
                + [p for p in a_nodes[k] if p not in e_nodes[k]]
            )
            diffs.append({"kind": "changed_node", "id": list(k), "properties": changed})
    e_rels = Counter(rel_key(r) for r in expected["rels"])
    a_rels = Counter(rel_key(r) for r in actual["rels"])
    for k in sorted((e_rels - a_rels).elements(), key=str):
        diffs.append({"kind": "missing_rel", "id": list(k)})
    for k in sorted((a_rels - e_rels).elements(), key=str):
        diffs.append({"kind": "extra_rel", "id": list(k)})
    if expected.get("constraints") != actual.get("constraints"):
        diffs.append({
            "kind": "schema_constraints",
            "expected": expected.get("constraints"),
            "actual": actual.get("constraints"),
        })
    if expected.get("indexes") != actual.get("indexes"):
        diffs.append({
            "kind": "schema_indexes",
            "expected": expected.get("indexes"),
            "actual": actual.get("indexes"),
        })
    return diffs


def diff_files(expected: dict, actual: dict, root: str) -> list:
    diffs = []
    e, a = expected["files"], actual["files"]
    for p in sorted(e.keys() - a.keys()):
        diffs.append({"kind": "missing_file", "root": root, "path": p,
                      "expected_sha256": e[p]["sha256"]})
    for p in sorted(a.keys() - e.keys()):
        diffs.append({"kind": "extra_file", "root": root, "path": p,
                      "sha256": a[p]["sha256"]})
    for p in sorted(e.keys() & a.keys()):
        if e[p]["sha256"] != a[p]["sha256"] or e[p]["bytes"] != a[p]["bytes"]:
            diffs.append({"kind": "changed_file", "root": root, "path": p,
                          "expected_sha256": e[p]["sha256"], "sha256": a[p]["sha256"]})
    e_dirs, a_dirs = set(expected["dirs"]), set(actual["dirs"])
    for d in sorted(e_dirs - a_dirs):
        diffs.append({"kind": "missing_dir", "root": root, "path": d})
    for d in sorted(a_dirs - e_dirs):
        diffs.append({"kind": "extra_dir", "root": root, "path": d})
    return diffs


def diff_sqlite(expected: dict, actual: dict) -> list:
    diffs = []
    for t in sorted(expected.keys() | actual.keys()):
        if t not in actual:
            diffs.append({"kind": "missing_table", "table": t})
            continue
        if t not in expected:
            diffs.append({"kind": "extra_table", "table": t})
            continue
        if expected[t] != actual[t]:
            e_rows, a_rows = expected[t]["rows"], actual[t]["rows"]
            diffs.append({
                "kind": "changed_rows", "table": t,
                "expected_rows": len(e_rows), "actual_rows": len(a_rows),
            })
    return diffs


def assert_absent(graph: dict, graph_keys: list, files: dict, file_paths: dict) -> list:
    violations = []
    present = {node_key(n) for n in graph["nodes"]}
    for k in graph_keys:
        if not isinstance(k, dict) or "labels" not in k or "key" not in k:
            raise RuntimeError(
                f"malformed absent-graph entry (want {{labels, key}}): {k!r}"
            )
        if (tuple(sorted(k["labels"])), k["key"]) in present:
            violations.append({
                "kind": "graph_key_present",
                "id": [k["labels"], k["key"]],
            })
    for root, rels in file_paths.items():
        manifest = files.get(root, {"files": {}})["files"]
        for p in rels:
            if p in manifest:
                violations.append({"kind": "file_present", "root": root, "path": p})
    return violations


class TransportError(RuntimeError):
    pass


def validate_transport_binding(expected_network_id: str, actual_network_id: str,
                               ip: str, subnet: str) -> list:
    """Direct-IP transport adapter gate: the container's selected network must
    be the run-owned network and its IP must sit inside that network's private
    subnet. Returns failure reasons (empty = bound)."""
    import ipaddress
    reasons = []
    if not expected_network_id:
        reasons.append("expected network id missing")
    if actual_network_id != expected_network_id:
        reasons.append(
            f"container network {actual_network_id!r} is not the run-owned "
            f"network {expected_network_id!r}")
    if not ip:
        reasons.append("container IP missing on the run-owned network")
    if not subnet:
        reasons.append("network subnet missing")
    if ip and subnet:
        try:
            net = ipaddress.ip_network(subnet, strict=False)
            if ipaddress.ip_address(ip) not in net:
                reasons.append(
                    f"IP {ip} is outside the run-owned network subnet {subnet}")
        except ValueError as e:
            reasons.append(f"invalid ip/subnet ({ip!r} in {subnet!r}): {e}")
    return reasons


def select_transport_endpoint(networks_json: str, expected_network_id: str,
                              subnet: str) -> str:
    """Pick the container IP on the run-owned network from a docker/podman
    inspect of .NetworkSettings.Networks; fail closed otherwise."""
    try:
        nets = json.loads(networks_json)
    except ValueError as e:
        raise TransportError(f"malformed networks inspect JSON: {e}")
    if not isinstance(nets, dict) or not nets:
        raise TransportError("container has no network attachments")
    for _name, n in nets.items():
        if not isinstance(n, dict):
            continue
        nid, ip = n.get("NetworkID"), n.get("IPAddress")
        if not nid or not ip:
            continue
        if not validate_transport_binding(expected_network_id, nid, ip, subnet):
            return ip
    raise TransportError(
        "no attachment to the run-owned network with an in-subnet IP "
        f"(expected network {expected_network_id!r}, subnet {subnet!r}, "
        f"attachments: {json.dumps(nets)[:300]})")


def transport_json_payload(flag_value, what: str) -> str:
    """Call protocol shared by the transport helpers: the raw JSON is accepted
    via the explicit flag (override) OR piped on stdin (how the harness calls
    it). Empty/malformed stdin fails — no fallback, no guessing."""
    if flag_value:
        return flag_value
    data = sys.stdin.read()
    if not data.strip():
        raise TransportError(
            f"empty {what} payload on stdin (explicit --flag override exists; "
            "no fallback)")
    return data


def cmd_transport_endpoint(args) -> int:
    ip = select_transport_endpoint(
        transport_json_payload(args.networks_json, "networks inspect"),
        args.expected_network_id, args.subnet)
    print(ip)
    return 0


def decode_network_binding(network_json: str, expected_name: str):
    """Decode the run-owned network binding (id, subnet, shape) from an
    ACTUAL `docker network inspect` payload — no guessed fallbacks.

    Supported shapes (both observed runtimes):
      Docker:  {"Name": ..., "Id": ..., "IPAM": {"Config": [{"Subnet": ...}]}}
      Podman:  {"name": ..., "id": ..., "subnets": [{"subnet": ...}]}
    Fails closed on: malformed/missing fields, name mismatch (orphan),
    duplicate entries, ambiguous subnets, invalid CIDR, non-private subnet,
    unknown driver shape."""
    import ipaddress
    try:
        payload = json.loads(network_json)
    except ValueError as e:
        raise TransportError(f"malformed network inspect JSON: {e}")
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list) or len(payload) != 1:
        count = len(payload) if isinstance(payload, list) else "non-list"
        raise TransportError(
            f"network inspect must return exactly one entry (got {count})")
    entry = payload[0]
    if not isinstance(entry, dict):
        raise TransportError("network inspect entry is not an object")
    name = entry.get("Name") or entry.get("name")
    nid = entry.get("Id") or entry.get("id")
    if not name or not nid:
        raise TransportError(
            "network entry missing Name/Id "
            f"(keys: {sorted(entry.keys())[:12]})")
    if name != expected_name:
        raise TransportError(
            f"network inspect returned {name!r}, expected "
            f"{expected_name!r} (orphan/mismatch)")
    candidates = set()
    shapes = set()
    ipam = entry.get("IPAM")
    if isinstance(ipam, dict) and isinstance(ipam.get("Config"), list):
        for c in ipam["Config"]:
            if isinstance(c, dict) and c.get("Subnet"):
                shapes.add("docker-ipam")
                candidates.add(str(c["Subnet"]))
    subnets = entry.get("subnets")
    if isinstance(subnets, list):
        for s in subnets:
            if isinstance(s, dict) and s.get("subnet"):
                shapes.add("podman-subnets")
                candidates.add(str(s["subnet"]))
    if len(candidates) > 1:
        raise TransportError(
            f"ambiguous subnets in inspect payload: {sorted(candidates)}")
    if not candidates:
        raise TransportError(
            "unknown driver shape — neither IPAM.Config nor subnets carried "
            f"a subnet (keys: {sorted(entry.keys())[:12]})")
    subnet = next(iter(candidates))
    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except ValueError as e:
        raise TransportError(f"invalid CIDR subnet {subnet!r}: {e}")
    if not net.is_private:
        raise TransportError(
            f"network subnet {subnet!r} is not a private range")
    if len(shapes) > 1:
        shape = "docker-ipam+podman-subnets-agree"
    else:
        shape = next(iter(shapes))
    return nid, subnet, shape


def cmd_network_binding(args) -> int:
    nid, subnet, shape = decode_network_binding(
        transport_json_payload(args.network_json, "network inspect"),
        args.expected_name)
    print(nid)
    print(subnet)
    print(shape)
    return 0


CHAT_CITATION_MESSAGE_ID = "fx-msg-0002"


def validate_chat_source_coherence(chat_db_path: str, contract: dict) -> list:
    """Cross-repo coherence preflight against the ACTUAL public typed
    Source boundary (chat src/types/index.ts): the SEEDED chat pack's
    citation message (fx-msg-0002, metadata.sources[0]) must carry
    document_id == declared id, sid == declared id (chosen fixture),
    chunk_id == declared chunk, nested entry.metadata.filename == declared
    filename (flat entry.filename is a legacy shape and rejected; the outer
    message metadata filename is NOT read — no authority), content
    non-empty and contained in the declared snapshot, and a typed finite
    numeric score (no cap). Expectations come from the declared main
    fixture contract only — no producer-derived oracle mirroring. Fails on
    input disagreement before any expensive build."""
    import math
    import sqlite3
    cs = (contract.get("fixture_pack") or {}).get("citation_source") or {}
    missing = [k for k in ("id", "chunk_id", "filename", "snapshot_text")
               if not cs.get(k)]
    if missing:
        return [f"contract citation_source incomplete: missing {missing}"]
    if not os.path.isfile(chat_db_path):
        return [f"chat db not found: {chat_db_path}"]
    conn = sqlite3.connect(f"file:{chat_db_path}?mode=ro&immutable=1", uri=True)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "chat_messages" not in tables:
            return [f"chat db has no chat_messages table "
                    f"(tables: {sorted(tables)[:8]})"]
        row = conn.execute(
            "SELECT metadata FROM chat_messages WHERE id = ?",
            (CHAT_CITATION_MESSAGE_ID,)).fetchone()
        if row is None:
            return [f"citation message {CHAT_CITATION_MESSAGE_ID} missing"]
        try:
            meta = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        except (TypeError, ValueError):
            return ["citation message metadata is not JSON"]
        sources = meta.get("sources") if isinstance(meta, dict) else None
        if not isinstance(sources, list) or not sources:
            return ["citation message metadata.sources missing or empty"]
        entry = sources[0]
        if not isinstance(entry, dict):
            return ["metadata.sources[0] is not an object"]
        reasons = []
        if entry.get("document_id") != cs["id"]:
            reasons.append(
                f"document_id {entry.get('document_id')!r} != declared "
                f"citation source id {cs['id']!r}")
        if entry.get("sid") != cs["id"]:
            reasons.append(
                f"sid {entry.get('sid')!r} != declared (chosen fixture) "
                f"citation source id {cs['id']!r}")
        if entry.get("chunk_id") != cs["chunk_id"]:
            reasons.append(
                f"chunk_id {entry.get('chunk_id')!r} != declared "
                f"{cs['chunk_id']!r}")
        meta_field = entry.get("metadata")
        if not isinstance(meta_field, dict):
            reasons.append(
                "source entry metadata missing or not an object (public "
                "Source shape requires nested metadata.filename; a flat "
                "entry.filename is a legacy shape and rejected)")
        elif meta_field.get("filename") != cs["filename"]:
            reasons.append(
                f"metadata.filename {meta_field.get('filename')!r} != "
                f"declared {cs['filename']!r}")
        content = entry.get("content")
        if not isinstance(content, str) or not content.strip():
            reasons.append("source content missing or empty")
        elif content not in cs["snapshot_text"]:
            reasons.append(
                "source content not contained in the declared fixture "
                "snapshot (input mismatch)")
        score = entry.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) \
                or not math.isfinite(score):
            reasons.append(
                f"source score must be a typed finite number (got "
                f"{score!r}); no untyped/provider-derived values")
        return reasons
    finally:
        conn.close()


def cmd_chat_coherence(args) -> int:
    with open(args.contract) as f:
        contract = json.load(f)
    reasons = validate_chat_source_coherence(args.chat_db, contract)
    if reasons:
        for r in reasons:
            print(f"INCOHERENCE: {r}")
        return 1
    print("cross-repo coherence: seeded chat citation agrees with the "
          "declared main fixture source")
    return 0


class GateArtifactMissing(RuntimeError):
    pass


ISOLATION_ROOTS = ("uploads", "custom_inputs", "chat", "skills", "apps")


def isolation_after_manifests(evidence_dir: str,
                              after_prefix: str =
                              "original-files-after-consumers") -> dict:
    """Locator for the post-consumer original-file manifests. The producer
    writes them at {evidence_dir}/{after_prefix}-{root}.json; ALL five must
    exist — missing evidence means isolation cannot be measured."""
    paths = {r: os.path.join(evidence_dir, f"{after_prefix}-{r}.json")
             for r in ISOLATION_ROOTS}
    missing = [p for p in paths.values() if not os.path.isfile(p)]
    if missing:
        raise GateArtifactMissing(
            f"missing {len(missing)} of {len(ISOLATION_ROOTS)} post-consumer "
            f"original-file manifests: {missing}")
    return paths


def compare_isolation_manifests(evidence_dir: str,
                                gate_prefix: str = "gate-files",
                                after_prefix: str =
                                "original-files-after-consumers") -> list:
    """Isolation proof: every restored file root must be byte/dir-identical
    between the frozen gate manifest and the post-consumer original manifest
    (all roots, bytes and dirs required). Real diffs are isolation breaches;
    missing evidence raises GateArtifactMissing (never treated as a pass)."""
    paths = isolation_after_manifests(evidence_dir, after_prefix)
    diffs = []
    for root, path in paths.items():
        gate = load(os.path.join(evidence_dir, f"{gate_prefix}-{root}.json"))
        after = load(path)
        diffs += diff_files(gate, after, root)
    return diffs


def cmd_isolation_compare(args) -> int:
    try:
        diffs = compare_isolation_manifests(args.evidence_dir,
                                            args.gate_prefix,
                                            args.after_prefix)
    except GateArtifactMissing as e:
        print(f"GATE-ARTIFACT-MISSING: {e}")
        return 2
    except FileNotFoundError as e:
        print(f"GATE-ARTIFACT-MISSING: required manifest not found — "
              f"isolation cannot be measured (artifact problem, not a "
              f"mutation): {e}")
        return 2
    except ValueError as e:
        print(f"GATE-ARTIFACT-INVALID: malformed manifest JSON — isolation "
              f"cannot be measured (artifact problem, not a mutation): {e}")
        return 2
    if diffs:
        for d in diffs:
            print(f"ISOLATION-DIFF: {json.dumps(d, sort_keys=True)}")
        print("ISOLATION-BREACH: original restored file roots changed "
              "during the consumer phase")
        return 1
    print("isolation proof: all five original restored file roots unchanged "
          "after consumer boot")
    return 0


def cmd_summary_emitter(args) -> int:
    summary = {
        "run_id": args.run_id,
        "mode": args.mode,
        "result": args.result_text,
        "images": {"neo4j": args.neo4j_image,
                   "neo4j_image_id": args.neo4j_image_id,
                   "neo4j_digest": args.neo4j_digest,
                   "apoc_version": args.apoc_version,
                   "neo4j_version": args.neo4j_version,
                   "sidecar_image_id": args.sidecar_image_id},
        "versions": {"neo4j_image": "5.26-community",
                     "scripts": "ops/backup/backup.sh + restore.sh executed "
                                "as on disk; exact identities in "
                                "evidence/provenance.json (restore.sh "
                                "runbook header was updated 2026-10-01 by "
                                "the docs owner; no qa/restore claim of "
                                "byte-identity to bd6c1e0)"},
        "fixture": {
            "graph": "synthetic complete domain model (API keys, ApiSession "
                     "opaque memory, TaskRecord, LLMUsageDay, SystemMeta "
                     "model identity, Chunk/Entity vector-ish embeddings, "
                     "GitConnection, X402Config, communities, collections)",
            "files": "uploads, custom_inputs, chat (companion fixture), "
                     "skills (encrypted-secret config.json), apps "
                     "(storage.sqlite kv rows)",
            "chat_fixture": args.chat_fixture,
            "chat_postt": args.chat_postt,
            "chat_verify_frozen": args.chat_verify_frozen,
            "chat_verify_restored": args.chat_verify_restored,
            "schema": "source+target: unique(Document.id) + fulltext("
                      "chunk_content) + vector(chunk_embedding, 8d cosine); "
                      "vector deliberately retained by restore.sh; "
                      "definitions captured and compared exactly",
            "fulltext_healthy_source": args.fulltext_source,
            "fulltext_restored_target": args.fulltext_target,
            "post_t_writes": "Document doc-crr-999 + blob + LLMUsageDay bump "
                             "+ chat canaries (asserted absent at target)"},
        "controls": {
            "healthy": {"expect": "pass",
                        "verdict": "evidence/verdict-healthy.json"},
            "incomplete": {"expect": "reject",
                           "verdict": "evidence/verdict-incomplete.json",
                           "missing_blob": args.missing_blob},
            "refusals": {
                "no_complete": "refused before wipe with exact .complete "
                               "diagnostic, exact graph unchanged",
                "bad_checksum": "refused before wipe with exact checksum "
                                "diagnostic, exact graph unchanged"},
        },
        "consumers": {
            "opt_in_flag": "--consumers",
            "requested": args.consumers_requested in ("1", "true", "True"),
            "target_bolt": (f"127.0.0.1:{args.target_bolt_port}"
                            if args.consumers_requested in ("1", "true",
                                                            "True")
                            else "not published (storage-only run)"),
            "results": ("evidence/consumer-results.json"
                        if args.consumers_requested in ("1", "true", "True")
                        else None),
            "scope": "consumer phase runs against the restored TARGET graph "
                     "+ file-root COPIES only (never the source); runs "
                     "BEFORE the destructive incomplete probe; original "
                     "restored volumes stay frozen",
            "allowed_state_transitions": "storage oracle verdicts are "
                                         "captured BEFORE the consumer phase; "
                                         "backend/chat startup deltas "
                                         "(idempotent schema ensure at 8 dims, "
                                         "terminal TaskRecord reconcile no-op, "
                                         "chat migration watermark no-op, "
                                         "consumer-session writes on the chat "
                                         "COPY) are declared, not treated as "
                                         "pristine regressions",
        },
        "transport": {
            "mode": args.transport_mode,
            "opt_in": "--podman-no-dns / CORTEX_RESTORE_PODMAN_NO_DNS=1 "
                      "(fixture-only environmental adapter)",
            "chosen_endpoints": {"source": args.source_address,
                                 "target": args.target_address},
            "claim_limit": "in direct-ip mode no DNS-based service discovery "
                           "is exercised or evidenced; addresses are "
                           "inspected from the run-owned network (podman "
                           "5.4.2 private root without aardvark DNS); "
                           "default remains name-based DNS for normal "
                           "engines",
            "evidence": "evidence/transport.json (direct-ip mode)",
        },
        "replay_command": f"qa/restore/rehearsal.sh --run-id {args.run_id}-replay"
                          + ("" if args.mode == "full"
                             else "  (full gate also needs --chat-fixture or "
                                  "the default companion discovery)"),
        "operator_restore_runbook": "ops/backup/restore.sh header (docker "
                                    "compose exec -e RESTORE_WIPE=yes backup "
                                    "/restore.sh <ts>)",
        "evidence_dir": f"{args.out}/evidence",
        "receipts_dir": f"{args.out}/receipts",
    }
    target = args.out_file or os.path.join(args.out, "summary.json")
    os.makedirs(os.path.dirname(os.path.abspath(target)) or ".",
                exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w") as f:
        json.dump(summary, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
    print(f"summary emitted: {target}")
    return 0


def load(path: str) -> dict:
    return json.loads(Path(path).read_text())


class ProvenanceError(RuntimeError):
    pass


PROV_CODE_ROOTS_REQUIRED = [
    ("cortex-app", "backend/app"),
    ("cortex-app", "backend/requirements-base.txt"),
    ("cortex-chat", "src"),
    ("cortex-chat", "package.json"),
    ("cortex-chat", "package-lock.json"),
    ("cortex-chat", "bun.lock"),
]
PROV_CODE_ROOTS_OPTIONAL = [
    ("cortex-app", "backend/requirements.txt"),
    ("cortex-chat", "next.config.ts"),
    ("cortex-chat", "tsconfig.json"),
    ("cortex-chat", "drizzle.config.ts"),
]
PROV_MANIFEST_REQUIRED_MEMBERS = [
    "backend/app/main.py",
    "src/app/",
    "package-lock.json:",
    "bun.lock:",
    "backend/requirements-base.txt:",
]


def sha256_bytes_path(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def git_identity(repo: str) -> dict:
    head_res = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=60,
    )
    if head_res.returncode != 0 or not head_res.stdout.strip():
        raise ProvenanceError(
            f"git rev-parse HEAD failed for required repo {repo!r} "
            f"(rc={head_res.returncode}): {head_res.stderr.strip()[:300]}")
    status_res = subprocess.run(
        ["git", "-C", repo, "status", "--short"],
        capture_output=True, text=True, timeout=60,
    )
    if status_res.returncode != 0:
        raise ProvenanceError(
            f"git status failed for required repo {repo!r} "
            f"(rc={status_res.returncode}): {status_res.stderr.strip()[:300]}")
    return {"head": head_res.stdout.strip(),
            "dirty_entries": len(status_res.stdout.splitlines())}


def build_code_manifest(app_repo: str, chat_repo: str):
    entries = []
    for repo, label in ((app_repo, "cortex-app"), (chat_repo, "cortex-chat")):
        if not os.path.isdir(repo):
            raise ProvenanceError(
                f"required repo root missing/not a directory: {label}: {repo}")
        for _, rel in [
            r for r in PROV_CODE_ROOTS_REQUIRED + PROV_CODE_ROOTS_OPTIONAL
            if r[0] == label
        ]:
            base = os.path.join(repo, rel)
            if not os.path.exists(base):
                if (label, rel) in PROV_CODE_ROOTS_REQUIRED:
                    raise ProvenanceError(
                        f"required source root missing: {label}:{rel} ({base})")
                continue
            if os.path.isfile(base):
                entries.append(f"{rel}:{sha256_bytes_path(base)}")
                continue
            collected = 0
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames.sort()
                for fn in sorted(filenames):
                    p = os.path.join(dirpath, fn)
                    if os.path.isfile(p) and not os.path.islink(p):
                        entries.append(
                            f"{rel}/{os.path.relpath(p, base)}:"
                            f"{sha256_bytes_path(p)}")
                        collected += 1
            if collected == 0:
                raise ProvenanceError(
                    f"required source root walked to zero files "
                    f"(cannot silently pass): {label}:{rel} ({base})")
    entries.sort()
    for member in PROV_MANIFEST_REQUIRED_MEMBERS:
        if not any(e.startswith(member) or member.rstrip(":") in e
                   for e in entries):
            raise ProvenanceError(
                f"code manifest missing required member: {member}")
    digest = hashlib.sha256("\n".join(entries).encode()).hexdigest()
    return digest, len(entries), entries


def build_provenance(out_path: str, files: list, app_repo: str, chat_repo: str,
                     harness_meta: dict = None) -> dict:
    input_files = {}
    for p in files:
        if not p or not os.path.isfile(p) or os.path.islink(p):
            raise ProvenanceError(
                f"provenance input file missing/not a regular file: {p!r}")
        input_files[p] = sha256_bytes_path(p)
    prov = {
        "input_files": input_files,
        "revisions": {
            "cortex-app": git_identity(app_repo),
            "cortex-chat": git_identity(chat_repo),
        },
    }
    if harness_meta:
        prov["harness_meta"] = dict(harness_meta)
    digest, count, _ = build_code_manifest(app_repo, chat_repo)
    prov["code_manifest"] = {
        "digest": digest,
        "files": count,
        "required_members": list(PROV_MANIFEST_REQUIRED_MEMBERS),
        "note": "coarse candidate identity of the actually-consumed production source + locks; dirtiness recorded, never hidden; required members enforced fail-closed",
    }
    with open(out_path, "w") as f:
        json.dump(prov, f, indent=1, sort_keys=True)
    return prov


def verify_provenance(prov_path: str, files: list, app_repo: str, chat_repo: str) -> dict:
    with open(prov_path) as f:
        prov = json.load(f)
    recorded = prov.get("input_files")
    if not isinstance(recorded, dict) or not recorded:
        raise ProvenanceError("provenance.input_files missing or empty")
    provided = set(files)
    for p in files:
        if not os.path.isfile(p):
            raise ProvenanceError(f"input file vanished during run: {p!r}")
        h = sha256_bytes_path(p)
        if recorded.get(p) != h:
            raise ProvenanceError(f"input changed during run: {p}")
    for p in recorded:
        if p not in provided:
            raise ProvenanceError(
                f"recorded input not re-verified (metadata mismatch): {p}")
    for repo_label, repo in (("cortex-app", app_repo), ("cortex-chat", chat_repo)):
        current = git_identity(repo)
        if prov.get("revisions", {}).get(repo_label) != current:
            raise ProvenanceError(
                f"repo revision changed during run: {repo_label}: "
                f"{prov.get('revisions', {}).get(repo_label)} != {current}")
    digest, count, _ = build_code_manifest(app_repo, chat_repo)
    recorded_manifest = prov.get("code_manifest", {})
    if recorded_manifest.get("digest") != digest:
        raise ProvenanceError(
            "consumed production source changed during run: code manifest "
            f"digest {recorded_manifest.get('digest')} != {digest}")
    if recorded_manifest.get("files") != count:
        raise ProvenanceError(
            f"code manifest file count changed: "
            f"{recorded_manifest.get('files')} != {count}")
    return {"input_files": len(recorded), "code_manifest_files": count,
            "digest": digest}


def parse_provenance_cli(argv: list):
    """Dynamic, variadic-robust parse of the provenance invocation.
    argv excludes the program name:
      [ --out PATH --file P ... --file P --app-repo R --chat-repo C ]
    Every --file is kept; nothing is truncated by fixed indices."""
    out = None
    files = []
    app_repo = None
    chat_repo = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--out":
            out, i = argv[i + 1], i + 2
        elif a == "--file":
            files.append(argv[i + 1])
            i += 2
        elif a == "--app-repo":
            app_repo, i = argv[i + 1], i + 2
        elif a == "--chat-repo":
            chat_repo, i = argv[i + 1], i + 2
        else:
            raise ProvenanceError(f"unknown provenance argument: {a!r}")
    if not out or not files or not app_repo or not chat_repo:
        raise ProvenanceError(
            "provenance invocation incomplete: --out, >=1 --file, "
            "--app-repo and --chat-repo are required")
    if app_repo == chat_repo:
        raise ProvenanceError(
            "provenance requires DISTINCT repo roots "
            f"(got {app_repo!r} twice)")
    return out, files, app_repo, chat_repo


CHAT_UPSTREAM_MODE = "isolated-backend"
CHAT_UPSTREAM_URL_FIELD = "url"
BACKEND_URL_SECTION = "startup"
BACKEND_URL_FIELD = "baseUrl"
CHAT_CHECK_STATUS_PASS_VALUES = {"ok", "pass", "passed", "true", "200"}
CHAT_CHECK_STATUS_FAIL_VALUES = {"fail", "failed", "error", "blocked", "false"}
CHAT_EXECUTION_URL_FIELDS = ("backendUrl", "backend_url", "url", "baseUrl")
REQUIRED_CHAT_CHECK_IDS = [
    "proxy-isolated-backend-collections-200-scoped",
    "proxy-isolated-backend-citation-content-exact",
]
LEGACY_CHAT_CHECK_IDS = [
    "proxy-isolated-backend-citation-file-exact",
]
GATE_VERSION = 2


def _check_passed(entry) -> bool:
    if isinstance(entry, bool):
        return entry
    if not isinstance(entry, dict):
        return False
    if entry.get("ok") is False or entry.get("passed") is False \
            or entry.get("status_ok") is False:
        return False
    status = entry.get("status")
    if isinstance(status, str) and status.strip().lower() in \
            CHAT_CHECK_STATUS_FAIL_VALUES:
        return False
    if entry.get("ok") is True or entry.get("passed") is True \
            or entry.get("status_ok") is True:
        return True
    if isinstance(status, int):
        return 200 <= status < 300
    if isinstance(status, str):
        s = status.strip().lower()
        if s in CHAT_CHECK_STATUS_PASS_VALUES:
            return True
    return False


def validate_consumer_receipt(receipt, *, required_chat_checks=None) -> list:
    """Strict consumer-gate validation against the ACTUAL delivery shapes
    (schema/interface alignment recorded in consumer-coordination.json;
    semantics not weakened): backend receipt with failures[] empty,
    boot_deltas present, boot_deltas_accepted exactly true, startup.baseUrl
    identity; receipt.chat = RAW chat receipt (ok true, failures[] empty,
    upstream{mode,url}, checks[{id, ok, reportOnly?, status?}],
    networkTripwire{clean,blockedAttempts}, cleanup{stateDirUnchanged});
    receipt.chat_execution wrapper process metadata present and
    identity-consistent. Missing/false values always fail. Returns a list
    of failure reasons (empty = accepted)."""
    if required_chat_checks is None:
        required_chat_checks = REQUIRED_CHAT_CHECK_IDS
    reasons = []
    if not isinstance(receipt, dict):
        return ["receipt is not a JSON object (missing/false = fail)"]
    failures = receipt.get("failures")
    if failures:
        reasons.append(f"receipt.failures non-empty: {json.dumps(failures)[:500]}")
    boot_deltas = receipt.get("boot_deltas")
    if boot_deltas is None:
        reasons.append("receipt.boot_deltas missing (pre/post boot capture required)")
    if receipt.get("boot_deltas_accepted") is not True:
        reasons.append(
            "receipt.boot_deltas_accepted is not exactly true "
            "(declared acceptance required, not implied)")
    startup = receipt.get(BACKEND_URL_SECTION)
    if not isinstance(startup, dict) or not startup.get(BACKEND_URL_FIELD):
        reasons.append(
            f"receipt.{BACKEND_URL_SECTION}.{BACKEND_URL_FIELD} missing "
            "(backend startup identity required)")
        backend_url = None
    else:
        backend_url = str(startup[BACKEND_URL_FIELD]).strip().rstrip("/")
    chat = receipt.get("chat")
    if not isinstance(chat, dict) or not chat:
        reasons.append("receipt.chat (raw chat receipt) missing or not an object")
        return reasons
    if chat.get("ok") is not True:
        reasons.append(
            f"raw chat receipt ok={chat.get('ok')!r} (must be exactly true)")
    if chat.get("failures"):
        reasons.append(
            f"chat.failures non-empty: {json.dumps(chat['failures'])[:500]}")
    upstream = chat.get("upstream")
    if not isinstance(upstream, dict):
        reasons.append("chat.upstream section missing")
    else:
        if upstream.get("mode") != CHAT_UPSTREAM_MODE:
            reasons.append(
                f"chat.upstream.mode={upstream.get('mode')!r} "
                f"!= {CHAT_UPSTREAM_MODE!r}")
        url = upstream.get(CHAT_UPSTREAM_URL_FIELD)
        if not isinstance(url, str) or not url.strip():
            reasons.append(
                f"chat.upstream.{CHAT_UPSTREAM_URL_FIELD} missing or not a URL")
        elif not backend_url:
            reasons.append(
                "chat upstream URL cannot be compared: backend startup "
                "identity missing")
        elif url.strip().rstrip("/") != backend_url:
            reasons.append(
                f"chat.upstream.{CHAT_UPSTREAM_URL_FIELD}={url!r} does not match "
                f"backend startup identity {backend_url!r}")
    checks = chat.get("checks")
    if not isinstance(checks, list) or not checks:
        reasons.append("chat.checks missing or not a non-empty list")
        checks = []
    by_id = {}
    for e in checks:
        if isinstance(e, dict) and e.get("id"):
            by_id.setdefault(e["id"], []).append(e)
    for e in checks:
        if not isinstance(e, dict):
            reasons.append(f"malformed check entry: {json.dumps(e)[:200]}")
            continue
        if e.get("id") in LEGACY_CHAT_CHECK_IDS:
            reasons.append(
                f"legacy check id rejected (gate v{GATE_VERSION} correction: "
                f"the chat proxy /file route is intentionally forbidden; the "
                f"supported citation entry is GET /documents/{{id}}/content): "
                f"{e.get('id')}")
            continue
        if e.get("reportOnly"):
            continue
        if not _check_passed(e):
            reasons.append(f"non-reportOnly check not passing: {json.dumps(e)[:300]}")
    for cid in required_chat_checks:
        entries = by_id.get(cid)
        if not entries:
            reasons.append(f"required chat check id missing: {cid}")
            continue
        for e in entries:
            if e.get("reportOnly"):
                reasons.append(
                    f"required check id {cid} is reportOnly "
                    "(required ids must be real observations)")
            elif not _check_passed(e):
                reasons.append(
                    f"required chat check not passing: {cid}: "
                    f"{json.dumps(e)[:300]}")
    tripwire = chat.get("networkTripwire")
    if not isinstance(tripwire, dict):
        reasons.append("chat.networkTripwire missing (network isolation gate)")
    else:
        if tripwire.get("clean") is not True:
            reasons.append(
                f"chat.networkTripwire.clean={tripwire.get('clean')!r} "
                "(must be exactly true)")
        attempts = tripwire.get("blockedAttempts")
        if not isinstance(attempts, list):
            reasons.append(
                f"chat.networkTripwire.blockedAttempts={attempts!r} "
                "must be an ARRAY (schema alignment to the delivered chat "
                "runner; integer 0 or other types are schema errors)")
        elif len(attempts) != 0:
            reasons.append(
                "chat.networkTripwire.blockedAttempts non-empty "
                f"({len(attempts)} egress attempt(s)): "
                f"{json.dumps(attempts[:1])[:300]}")
    cleanup = chat.get("cleanup")
    if not isinstance(cleanup, dict) \
            or cleanup.get("stateDirUnchanged") is not True:
        reasons.append(
            "chat.cleanup.stateDirUnchanged is not exactly true "
            "(recorded decision: consumer leaves the state copy unchanged)")
    chat_exec = receipt.get("chat_execution")
    if not isinstance(chat_exec, dict) or not chat_exec:
        reasons.append(
            "receipt.chat_execution (wrapper process metadata) missing or empty")
    else:
        for f in CHAT_EXECUTION_URL_FIELDS:
            v = chat_exec.get(f)
            if isinstance(v, str) and v.strip() and backend_url \
                    and v.strip().rstrip("/") != backend_url:
                reasons.append(
                    f"chat_execution.{f}={v!r} does not match backend "
                    f"startup identity {backend_url!r}")
    return reasons


class CompareError(ValueError):
    pass


REQUIRED_ROOTS = ("uploads", "custom_inputs", "chat", "skills", "apps")


def validate_compare_inputs(args) -> None:
    lens = (len(args.expected_roots), len(args.expected_files),
            len(args.actual_roots), len(args.actual_files))
    if len(set(lens)) != 1:
        raise CompareError(f"root/file list length mismatch: {lens}")
    if len(set(args.expected_roots)) != len(args.expected_roots):
        raise CompareError(f"duplicate expected roots: {args.expected_roots}")
    if len(set(args.actual_roots)) != len(args.actual_roots):
        raise CompareError(f"duplicate actual roots: {args.actual_roots}")
    if set(args.expected_roots) != set(args.actual_roots):
        raise CompareError(
            f"root sets differ: expected={sorted(args.expected_roots)} "
            f"actual={sorted(args.actual_roots)}"
        )
    missing = [r for r in REQUIRED_ROOTS if r not in args.expected_roots]
    if missing:
        raise CompareError(f"required fixture roots missing: {missing}")
    n = len(args.sqlite_names)
    if n:
        if not (n == len(args.expected_sqlite) == len(args.actual_sqlite)):
            raise CompareError(
                f"sqlite lists mismatch: names={n} "
                f"expected={len(args.expected_sqlite)} actual={len(args.actual_sqlite)}"
            )
        if len(set(args.sqlite_names)) != n:
            raise CompareError(f"duplicate sqlite store names: {args.sqlite_names}")


def cmd_compare(args) -> int:
    try:
        validate_compare_inputs(args)
    except CompareError as e:
        print(f"COMPARE-INPUT-ERROR: {e}")
        return 2
    expected_g, actual_g = load(args.expected_graph), load(args.actual_graph)
    verdict = {
        "oracle_version": 1,
        "expected_gate_sha256": sha256_file(Path(args.expected_graph)),
        "actual_capture_sha256": sha256_file(Path(args.actual_graph)),
    }
    verdict["graph_diffs"] = diff_graph(expected_g, actual_g)
    exp_f = {r: load(p) for r, p in zip(args.expected_roots, args.expected_files)}
    act_f = {r: load(p) for r, p in zip(args.actual_roots, args.actual_files)}
    verdict["file_diffs"] = []
    for root in exp_f:
        verdict["file_diffs"] += diff_files(exp_f[root], act_f[root], root)
    verdict["sqlite_diffs"] = []
    for name, (e, a) in zip(
        args.sqlite_names,
        zip(args.expected_sqlite, args.actual_sqlite),
    ):
        verdict["sqlite_diffs"] += [
            dict(d, store=name) for d in diff_sqlite(load(e), load(a))
        ]
    verdict["absence_violations"] = []
    if args.absent_graph or args.absent_files:
        verdict["absence_violations"] = assert_absent(
            actual_g, load(args.absent_graph) if args.absent_graph else [],
            act_f, load(args.absent_files) if args.absent_files else {},
        )
    verdict["pass"] = not (
        verdict["graph_diffs"] or verdict["file_diffs"]
        or verdict["sqlite_diffs"] or verdict["absence_violations"]
    )
    Path(args.verdict).write_text(json.dumps(verdict, indent=1, sort_keys=True))
    for d in verdict["graph_diffs"] + verdict["file_diffs"] + verdict["sqlite_diffs"] \
            + verdict["absence_violations"]:
        print(f"DIFF: {json.dumps(d, sort_keys=True)}")
    print(f"ORACLE {'PASS' if verdict['pass'] else 'REJECT'}")
    return 0 if verdict["pass"] == (args.expect == "pass") else 1


def graph_capture_fixture() -> dict:
    return {
        "nodes": [
            {"labels": ["Document"], "key": "doc-1",
             "props": {"id": "doc-1", "file_size": 10}},
            {"labels": ["Chunk"], "key": "doc-1-chunk-0",
             "props": {"id": "doc-1-chunk-0", "has_embedding": True}},
        ],
        "rels": [
            {"la": "Document", "ka": "doc-1", "rt": "HAS_CHUNK", "rp": {},
             "lb": "Chunk", "kb": "doc-1-chunk-0"},
        ],
        "constraints": [{"name": "c_doc", "type": "UNIQUENESS",
                         "entityType": "NODE", "labelsOrTypes": ["Document"],
                         "properties": ["id"]}],
        "indexes": [{"name": "chunk_content", "type": "FULLTEXT",
                     "entityType": "NODE", "labelsOrTypes": ["Chunk"],
                     "properties": ["content"], "indexProvider": "fulltext-1.0",
                     "vector_dimensions": -1, "vector_similarity": "",
                     "fulltext_analyzer": "standard-no-stop-words",
                     "fulltext_eventually_consistent": False},
                    {"name": "chunk_embedding", "type": "VECTOR",
                     "entityType": "NODE", "labelsOrTypes": ["Chunk"],
                     "properties": ["embedding"], "indexProvider": "vector-2.0",
                     "vector_dimensions": 8, "vector_similarity": "COSINE",
                     "fulltext_analyzer": "",
                     "fulltext_eventually_consistent": False}],
    }


def rel(r) -> dict:
    return {"la": r[0], "ka": r[1], "rt": r[2], "rp": {},
            "lb": r[3], "kb": r[4]}


def manifest_fixture() -> dict:
    return {"files": {"a.txt": {"sha256": "x1", "bytes": 4},
                      "b.txt": {"sha256": "x2", "bytes": 5}},
            "dirs": [".", "sub"]}


def sqlite_fixture() -> dict:
    return {"kv": {"columns": ["key", "value", "size", "updated_at"],
                   "rows": [["k1", "v1", 2, "t1"], ["k2", "v2", 2, "t2"]]}}


def expect_detection(name: str, diffs: list) -> None:
    if not diffs:
        raise AssertionError(f"selftest: mutation '{name}' was not detected")


def expect_clean(name: str, diffs: list) -> None:
    if diffs:
        raise AssertionError(f"selftest: identical state flagged: {name}: {diffs}")


def mutate(base: dict, fn) -> dict:
    import copy
    c = copy.deepcopy(base)
    fn(c)
    return c


def cmd_selftest(_args) -> int:
    import tempfile

    # The real SQLite operation matters: an active WAL must not be "verified"
    # using immutable=1, which would read only the older main database file.
    with tempfile.TemporaryDirectory(prefix="cortex-restore-oracle-") as tmp:
        db_path = Path(tmp) / "quiesce.sqlite"
        out_path = Path(tmp) / "capture.json"
        with sqlite3.connect(db_path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE fixture (id TEXT PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO fixture VALUES ('known-id', 'committed-in-wal')")
            db.commit()
            assert Path(str(db_path) + "-wal").exists()
            try:
                capture_sqlite(db_path, out_path)
            except RuntimeError as exc:
                assert "not quiesced" in str(exc)
            else:
                raise AssertionError("active SQLite WAL was silently ignored")
        db.close()
        capture_sqlite(db_path, out_path)
        assert load(str(out_path))["fixture"]["rows"] == [["known-id", "committed-in-wal"]]

    g = graph_capture_fixture()
    expect_clean("graph identical", diff_graph(g, mutate(g, lambda d: None)))
    expect_detection("missing node", diff_graph(
        g, mutate(g, lambda d: d["nodes"].pop(0))))
    expect_detection("extra node", diff_graph(
        g, mutate(g, lambda d: d["nodes"].append(
            {"labels": ["Document"], "key": "doc-999",
             "props": {"id": "doc-999"}}))))
    expect_detection("changed property", diff_graph(
        g, mutate(g, lambda d: d["nodes"][0].update(
            props={"id": "doc-1", "file_size": 11}))))
    expect_detection("duplicate node id", diff_graph(
        g, mutate(g, lambda d: d["nodes"].append(dict(d["nodes"][0])))))
    expect_detection("missing rel", diff_graph(
        g, mutate(g, lambda d: d["rels"].pop(0))))
    expect_detection("changed rel props", diff_graph(
        g, mutate(g, lambda d: d["rels"][0].update(rp={"x": 1}))))
    expect_detection("duplicate rel multiplicity loss", diff_graph(
        g, mutate(g, lambda d: d["rels"].append(dict(d["rels"][0])))))
    expect_detection("constraint drift", diff_graph(
        g, mutate(g, lambda d: d["constraints"].append(
            {"name": "c_extra", "type": "UNIQUENESS", "entityType": "NODE",
             "labelsOrTypes": ["X"], "properties": ["id"]}))))
    expect_detection("constraint definition change", diff_graph(
        g, mutate(g, lambda d: d["constraints"][0].update(properties=["id2"]))))
    expect_detection("index definition change", diff_graph(
        g, mutate(g, lambda d: d["indexes"][0].update(indexProvider="fulltext-2.0"))))
    expect_detection("vector index dims change", diff_graph(
        g, mutate(g, lambda d: d["indexes"][1].update(vector_dimensions=16))))

    m = manifest_fixture()
    expect_clean("files identical", diff_files(m, mutate(m, lambda d: None), "r"))
    expect_detection("missing file", diff_files(
        m, mutate(m, lambda d: d["files"].pop("a.txt")), "r"))
    expect_detection("changed file", diff_files(
        m, mutate(m, lambda d: d["files"]["b.txt"].update(sha256="zz")), "r"))
    expect_detection("extra file", diff_files(
        m, mutate(m, lambda d: d["files"].update(
            {"c.txt": {"sha256": "x3", "bytes": 1}})), "r"))
    expect_detection("missing dir", diff_files(
        m, mutate(m, lambda d: d["dirs"].remove("sub")), "r"))

    s = sqlite_fixture()
    expect_clean("sqlite identical", diff_sqlite(s, mutate(s, lambda d: None)))
    expect_detection("row change", diff_sqlite(
        s, mutate(s, lambda d: d["kv"]["rows"][0].__setitem__(1, "vx"))))
    expect_detection("table loss", diff_sqlite(
        s, mutate(s, lambda d: d.pop("kv"))))

    absent_key = {"labels": ["Document"], "key": "doc-1"}
    violations = assert_absent(g, [absent_key], {}, {})
    expect_detection("graph-only absence (object form)", violations)
    clean = assert_absent(
        g, [{"labels": ["Document"], "key": "doc-999"}], {}, {})
    expect_clean("graph-only absence clean", clean)
    try:
        assert_absent(g, [["Document", "doc-1"]], {}, {})
        raise AssertionError("malformed absent entry (list form) not rejected")
    except RuntimeError:
        pass

    files = {"r": manifest_fixture()}
    violations = assert_absent(g, [], files, {"r": ["a.txt"]})
    expect_detection("file absence", violations)
    clean = assert_absent(g, [], files, {"r": ["zz.txt"]})
    expect_clean("file absence clean", clean)

    class A:
        expected_roots = ["uploads", "custom_inputs", "chat", "skills", "apps"]
        actual_roots = ["uploads", "custom_inputs", "chat", "skills", "apps"]
        expected_files = ["1"] * 5
        actual_files = ["1"] * 5
        sqlite_names = []
        expected_sqlite = []
        actual_sqlite = []

    validate_compare_inputs(A())
    for mutate_fn, why in [
        (lambda a: a.__setattr__("actual_roots", a.actual_roots[:4]),
         "truncated actual roots"),
        (lambda a: a.__setattr__("actual_roots",
                                 ["uploads", "custom_inputs", "chat", "skills", "other"]),
         "root set mismatch"),
        (lambda a: a.__setattr__("expected_roots", ["uploads"] * 5),
         "duplicate roots"),
        (lambda a: a.__setattr__("expected_roots",
                                 ["uploads", "custom_inputs", "chat", "skills"]),
         "required root missing"),
        (lambda a: a.__setattr__("sqlite_names", ["x"]) or None,
         "sqlite length mismatch"),
    ]:
        try:
            import copy
            b = copy.deepcopy(A())
            mutate_fn(b)
            if why != "sqlite length mismatch":
                validate_compare_inputs(b)
                raise AssertionError(f"validation missed: {why}")
            else:
                b.expected_sqlite = []
                b.actual_sqlite = []
                try:
                    validate_compare_inputs(b)
                    raise AssertionError(f"validation missed: {why}")
                except CompareError:
                    pass
        except CompareError:
            pass

    def consumer_receipt_fixture() -> dict:
        raw_chat = {
            "ok": True,
            "upstream": {"mode": "isolated-backend",
                         "url": "http://127.0.0.1:45678"},
            "checks": [
                {"id": REQUIRED_CHAT_CHECK_IDS[0], "ok": True},
                {"id": REQUIRED_CHAT_CHECK_IDS[1], "ok": True},
                {"id": "chat-session-quiesced-read", "ok": True},
                {"id": "advisory-shape-probe", "ok": False, "reportOnly": True},
            ],
            "networkTripwire": {"clean": True, "blockedAttempts": []},
            "cleanup": {"stateDirUnchanged": True},
            "failures": [],
        }
        return {
            "failures": [],
            "boot_deltas": {"nodes": []},
            "boot_deltas_accepted": True,
            "startup": {"baseUrl": "http://127.0.0.1:45678"},
            "chat": raw_chat,
            "chat_execution": {
                "backendUrl": "http://127.0.0.1:45678",
                "pid": 4242,
            },
        }

    r = consumer_receipt_fixture()
    expect_clean("consumer receipt valid (raw chat shape)",
                 validate_consumer_receipt(r))
    REQUIRED = list(REQUIRED_CHAT_CHECK_IDS)

    def receipt_mutate(fn):
        return mutate(r, fn)

    for why, bad in [
        ("receipt not an object", None),
        ("receipt false", False),
        ("chat missing", receipt_mutate(lambda d: d.pop("chat"))),
        ("chat not object", receipt_mutate(lambda d: d.update(chat="x"))),
        ("chat empty", receipt_mutate(lambda d: d.update(chat={}))),
        ("raw ok false", receipt_mutate(
            lambda d: d["chat"].update(ok=False))),
        ("raw ok missing", receipt_mutate(lambda d: d["chat"].pop("ok"))),
        ("chat failures non-empty", receipt_mutate(
            lambda d: d["chat"].update(failures=["boom"]))),
        ("wrong upstream mode", receipt_mutate(lambda d: d["chat"]["upstream"].update(
            mode="live-backend"))),
        ("upstream missing", receipt_mutate(
            lambda d: d["chat"].pop("upstream"))),
        ("url missing", receipt_mutate(
            lambda d: d["chat"]["upstream"].pop("url"))),
        ("url identity mismatch", receipt_mutate(
            lambda d: d["chat"]["upstream"].update(
                url="http://127.0.0.1:99999"))),
        ("backend identity missing", receipt_mutate(
            lambda d: d["startup"].pop("baseUrl"))),
        ("required check missing", receipt_mutate(
            lambda d: d["chat"]["checks"].pop(0))),
        ("required check failing", receipt_mutate(
            lambda d: d["chat"]["checks"][0].update(ok=False))),
        ("required check blocked status", receipt_mutate(
            lambda d: d["chat"]["checks"][0].update(status="blocked"))),
        ("required id reportOnly", receipt_mutate(
            lambda d: d["chat"]["checks"][0].update(reportOnly=True))),
        ("non-reportOnly check failing", receipt_mutate(
            lambda d: d["chat"]["checks"][2].update(ok=False))),
        ("checks missing", receipt_mutate(lambda d: d["chat"].pop("checks"))),
        ("checks empty", receipt_mutate(
            lambda d: d["chat"].update(checks=[]))),
        ("tripwire missing", receipt_mutate(
            lambda d: d["chat"].pop("networkTripwire"))),
        ("tripwire not clean", receipt_mutate(
            lambda d: d["chat"]["networkTripwire"].update(clean=False))),
        ("tripwire blockedAttempts integer 0 (wrong type)", receipt_mutate(
            lambda d: d["chat"]["networkTripwire"].update(blockedAttempts=0))),
        ("tripwire blockedAttempts string type", receipt_mutate(
            lambda d: d["chat"]["networkTripwire"].update(
                blockedAttempts="none"))),
        ("tripwire blockedAttempts missing", receipt_mutate(
            lambda d: d["chat"]["networkTripwire"].pop("blockedAttempts"))),
        ("tripwire blockedAttempts non-empty array", receipt_mutate(
            lambda d: d["chat"]["networkTripwire"].update(
                blockedAttempts=[{"target": "https://evil.example",
                                  "at": "2026-10-01T00:00:00Z"}]))),
        ("cleanup missing", receipt_mutate(lambda d: d["chat"].pop("cleanup"))),
        ("state dir changed", receipt_mutate(
            lambda d: d["chat"]["cleanup"].update(stateDirUnchanged=False))),
        ("chat_execution missing", receipt_mutate(
            lambda d: d.pop("chat_execution"))),
        ("chat_execution empty", receipt_mutate(
            lambda d: d.update(chat_execution={}))),
        ("chat_execution identity mismatch", receipt_mutate(
            lambda d: d["chat_execution"].update(
                backendUrl="http://127.0.0.1:99999"))),
        ("legacy file id present", receipt_mutate(lambda d: d["chat"]["checks"].append(
            {"id": LEGACY_CHAT_CHECK_IDS[0], "ok": True}))),
        ("legacy file id reportOnly", receipt_mutate(
            lambda d: d["chat"]["checks"].append(
                {"id": LEGACY_CHAT_CHECK_IDS[0], "ok": True,
                 "reportOnly": True}))),
        ("receipt failures non-empty", receipt_mutate(
            lambda d: d.update(failures=[{"probe": "x"}]))),
        ("boot_deltas missing", receipt_mutate(lambda d: d.pop("boot_deltas"))),
        ("boot deltas not accepted", receipt_mutate(
            lambda d: d.update(boot_deltas_accepted=False))),
        ("boot deltas acceptance missing", receipt_mutate(
            lambda d: d.pop("boot_deltas_accepted"))),
    ]:
        reasons = validate_consumer_receipt(bad, required_chat_checks=REQUIRED)
        expect_detection(f"malformed consumer receipt: {why}", reasons)
    expect_clean("failing reportOnly check stays advisory", validate_consumer_receipt(
        mutate(r, lambda d: d["chat"]["checks"][3].update(ok=False))))
    reasons = validate_consumer_receipt(receipt_mutate(
        lambda d: d["chat"]["checks"].__setitem__(
            1, {"id": LEGACY_CHAT_CHECK_IDS[0], "ok": True})))
    assert any("legacy check id rejected" in x for x in reasons), reasons
    assert any(f"required chat check id missing: {REQUIRED_CHAT_CHECK_IDS[1]}"
               in x for x in reasons), reasons

    def _raises(fn) -> bool:
        try:
            fn()
            return False
        except ProvenanceError:
            return True

    def _tree_without(td: str, repo: str, rel: str) -> str:
        import shutil, tempfile
        copy = tempfile.mkdtemp(dir=td)
        shutil.copytree(repo, os.path.join(copy, "r"))
        target = os.path.join(copy, "r", rel)
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        return os.path.join(copy, "r")

    def prov_parser_dynamic_inputs():
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            f1 = os.path.join(td, "f1.txt")
            f8 = os.path.join(td, "f8.txt")
            for p, content in ((f1, "one"), (f8, "eight")):
                with open(p, "w") as fh:
                    fh.write(content)
            out = os.path.join(td, "prov.json")
            repo_a = os.path.join(td, "repo-a")
            repo_c = os.path.join(td, "repo-c")
            os.makedirs(repo_a)
            os.makedirs(repo_c)

            for count in (5, 8):
                files = ([f1] * (count - 1)) + [f8]
                argv = ["--out", out]
                for f in files:
                    argv += ["--file", f]
                argv += ["--app-repo", repo_a, "--chat-repo", repo_c]
                o, fs, a, c = parse_provenance_cli(argv)
                assert o == out and a == repo_a and c == repo_c
                assert fs == files, \
                    f"variadic truncation at {count} files: {fs}"
            try:
                parse_provenance_cli(["--out", out, "--file", f1,
                                      "--app-repo", repo_a,
                                      "--chat-repo", repo_a])
                raise AssertionError("distinct-repo-root guard missed")
            except ProvenanceError:
                pass
            try:
                parse_provenance_cli(["--out", out, "--app-repo", repo_a,
                                      "--chat-repo", repo_c])
                raise AssertionError("zero-files guard missed")
            except ProvenanceError:
                pass

            def git_guard():
                git_identity(os.path.join(td, "not-a-repo"))
            try:
                git_guard()
                raise AssertionError("git fault guard missed (non-repo)")
            except ProvenanceError:
                pass

            app = os.path.join(td, "fake-app-repo")
            chat = os.path.join(td, "fake-chat-repo")
            os.makedirs(os.path.join(app, "backend", "app"))
            os.makedirs(os.path.join(chat, "src", "app"))
            with open(os.path.join(app, "backend", "app", "main.py"), "w") as f:
                f.write("main")
            with open(os.path.join(app, "backend", "requirements-base.txt"),
                      "w") as f:
                f.write("dep==1\n")
            with open(os.path.join(chat, "src", "app", "page.tsx"), "w") as f:
                f.write("x")
            with open(os.path.join(chat, "package.json"), "w") as f:
                f.write("{}")
            with open(os.path.join(chat, "package-lock.json"), "w") as f:
                f.write("{}")
            with open(os.path.join(chat, "bun.lock"), "w") as f:
                f.write("{}")
            digest, count, entries = build_code_manifest(app, chat)
            for member in PROV_MANIFEST_REQUIRED_MEMBERS:
                assert any(member.rstrip(":") in e for e in entries), member
            expect_detection("missing required source root", [None] if _raises(
                lambda: build_code_manifest(
                    os.path.join(td, "nope"), chat)) else [])
            expect_detection("empty walked root", [None] if _raises(
                lambda: build_code_manifest(
                    _tree_without(td, app, "backend/app/main.py"), chat))
                else [])
            broken = _tree_without(td, chat, "bun.lock")
            expect_detection("missing required lock member", [None] if _raises(
                lambda: build_code_manifest(app, broken)) else [])
            try:
                build_provenance(out, [os.path.join(td, "missing-file")],
                                 app, chat)
                raise AssertionError("missing input file guard missed")
            except ProvenanceError:
                pass

    prov_parser_dynamic_inputs()

    def transport_binding():
        NET = "netid-abc123"
        SUB = "10.88.0.0/16"
        expect_clean("transport binding valid", validate_transport_binding(
            NET, NET, "10.88.3.17", SUB))
        expect_detection("transport wrong network", validate_transport_binding(
            NET, "other-net", "10.88.3.17", SUB))
        expect_detection("transport ip outside subnet", validate_transport_binding(
            NET, NET, "192.168.5.5", SUB))
        expect_detection("transport ip missing", validate_transport_binding(
            NET, NET, "", SUB))
        expect_detection("transport subnet missing", validate_transport_binding(
            NET, NET, "10.88.3.17", ""))
        expect_detection("transport malformed ip/subnet", validate_transport_binding(
            NET, NET, "999.1.2.3", "not-a-subnet"))
        ip = select_transport_endpoint(
            json.dumps({"crr-net": {"NetworkID": NET, "IPAddress": "10.88.3.17"}}),
            NET, SUB)
        assert ip == "10.88.3.17", ip
        for why, payload in [
            ("no attachments", "{}"),
            ("wrong net only", json.dumps(
                {"other": {"NetworkID": "x", "IPAddress": "10.88.3.17"}})),
            ("ip empty on owned net", json.dumps(
                {"crr-net": {"NetworkID": NET, "IPAddress": ""}})),
            ("ip outside subnet on owned net", json.dumps(
                {"crr-net": {"NetworkID": NET, "IPAddress": "172.99.1.1"}})),
        ]:
            try:
                select_transport_endpoint(payload, NET, SUB)
                raise AssertionError(f"transport selection guard missed: {why}")
            except TransportError:
                pass

    transport_binding()

    def network_binding_decoder():
        docker_shape = json.dumps([{
            "Name": "crr-d-net", "Id": "netid-docker-1", "Driver": "bridge",
            "IPAM": {"Config": [
                {"Subnet": "10.89.1.0/24", "Gateway": "10.89.1.1"}]}}])
        podman_shape = json.dumps([{
            "name": "crr-p-net", "id": "netid-podman-1", "driver": "bridge",
            "subnets": [{"subnet": "10.89.2.0/24", "gateway": "10.89.2.1"}]}])
        both_agree = json.dumps([{
            "Name": "crr-b-net", "Id": "netid-both-1",
            "IPAM": {"Config": [{"Subnet": "10.89.3.0/24"}]},
            "subnets": [{"subnet": "10.89.3.0/24"}]}])
        assert decode_network_binding(docker_shape, "crr-d-net") == (
            "netid-docker-1", "10.89.1.0/24", "docker-ipam")
        assert decode_network_binding(podman_shape, "crr-p-net") == (
            "netid-podman-1", "10.89.2.0/24", "podman-subnets")
        assert decode_network_binding(both_agree, "crr-b-net") == (
            "netid-both-1", "10.89.3.0/24", "docker-ipam+podman-subnets-agree")
        for why, payload, name in [
            ("orphan name mismatch", docker_shape, "other-net"),
            ("duplicate entries", json.dumps(
                [{"Name": "crr-d-net", "Id": "i1"},
                 {"Name": "crr-d-net", "Id": "i2"}]), "crr-d-net"),
            ("missing Id", json.dumps(
                [{"Name": "crr-d-net", "IPAM": {"Config": [
                    {"Subnet": "10.89.1.0/24"}]}}]), "crr-d-net"),
            ("unknown driver shape", json.dumps(
                [{"Name": "crr-x-net", "Id": "i3", "driver": "bridge"}]),
             "crr-x-net"),
            ("ambiguous subnets", json.dumps(
                [{"Name": "crr-x-net", "Id": "i4",
                  "IPAM": {"Config": [{"Subnet": "10.89.4.0/24"},
                                      {"Subnet": "10.89.5.0/24"}]}}]),
             "crr-x-net"),
            ("invalid CIDR", json.dumps(
                [{"Name": "crr-x-net", "Id": "i5", "subnets": [
                    {"subnet": "not-a-subnet"}]}]), "crr-x-net"),
            ("non-private subnet", json.dumps(
                [{"Name": "crr-x-net", "Id": "i6", "subnets": [
                    {"subnet": "8.8.8.0/24"}]}]), "crr-x-net"),
            ("malformed json", "{not-json", "crr-x-net"),
            ("empty payload", "[]", "crr-x-net"),
        ]:
            try:
                decode_network_binding(payload, name)
                raise AssertionError(f"network binding guard missed: {why}")
            except TransportError:
                pass

    network_binding_decoder()

    def chat_coherence_checks():
        import sqlite3
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "cortex-chat.db")
            contract = {"fixture_pack": {"citation_source": {
                "id": CITATION_SOURCE_ID,
                "chunk_id": CITATION_SOURCE_CHUNK_ID,
                "filename": CITATION_SOURCE_FILENAME,
                "title": CITATION_SOURCE_TITLE,
                "snapshot_text": "Alpha bravo\nCharlie delta"}}}

            def make_db(entries):
                if os.path.exists(db):
                    os.remove(db)
                conn = sqlite3.connect(db)
                conn.execute(
                    "CREATE TABLE chat_messages "
                    "(id TEXT PRIMARY KEY, metadata TEXT)")
                for mid, meta in entries:
                    conn.execute(
                        "INSERT INTO chat_messages (id, metadata) "
                        "VALUES (?, ?)", (mid, meta))
                conn.commit()
                conn.close()

            good_entry = {
                "sid": CITATION_SOURCE_ID,
                "document_id": CITATION_SOURCE_ID,
                "chunk_id": CITATION_SOURCE_CHUNK_ID,
                "content": "Alpha bravo",
                "score": 0.42,
                "metadata": {"filename": CITATION_SOURCE_FILENAME,
                             "chunk_index": 0},
            }
            good = json.dumps({"sources": [good_entry]})
            make_db([(CHAT_CITATION_MESSAGE_ID, good), ("fx-msg-0001", "{}")])
            expect_clean("chat source coherence healthy",
                         validate_chat_source_coherence(db, contract))
            make_db([(CHAT_CITATION_MESSAGE_ID, good),
                     (CHAT_CITATION_MESSAGE_ID + "x", good)])
            expect_clean("coherence ignores other messages",
                         validate_chat_source_coherence(db, contract))
            wrong_outer = json.dumps({"filename": "invented-outer.txt",
                                      "sources": [good_entry]})
            make_db([(CHAT_CITATION_MESSAGE_ID, wrong_outer)])
            expect_clean("outer message filename has no authority",
                         validate_chat_source_coherence(db, contract))

            def bad(mutate_entry):
                entry = json.loads(json.dumps(good_entry))
                mutate_entry(entry)
                make_db([(CHAT_CITATION_MESSAGE_ID,
                          json.dumps({"sources": [entry]}))])
                return validate_chat_source_coherence(db, contract)

            for why, mut in [
                ("bad document_id", lambda e: e.update(
                    document_id="fx-other-doc")),
                ("bad sid", lambda e: e.update(sid="fx-other-doc")),
                ("bad chunk_id", lambda e: e.update(
                    chunk_id="fx-source-0001-chunk-9")),
                ("metadata missing", lambda e: e.pop("metadata")),
                ("metadata mistyped", lambda e: e.update(metadata="x")),
                ("wrong nested filename", lambda e: e["metadata"].update(
                    filename="invented.txt")),
                ("flat filename with missing nested (legacy shape)",
                 lambda e: e.update(filename=CITATION_SOURCE_FILENAME)
                 or e.pop("metadata")),
                ("uncontained content", lambda e: e.update(
                    content="Alpha bravo Charlie delta echo")),
                ("missing content", lambda e: e.pop("content")),
                ("empty content", lambda e: e.update(content="")),
                ("missing score", lambda e: e.pop("score")),
                ("string score", lambda e: e.update(score="high")),
                ("bool score", lambda e: e.update(score=True)),
                ("infinite score", lambda e: e.update(score=float("inf"))),
                ("nan score", lambda e: e.update(score=float("nan"))),
            ]:
                expect_detection(f"chat coherence: {why}", bad(mut))
            make_db([("fx-msg-0001", "{}")])
            expect_detection("chat coherence: citation message missing",
                             validate_chat_source_coherence(db, contract))
            make_db([(CHAT_CITATION_MESSAGE_ID, "not-json")])
            expect_detection("chat coherence: malformed metadata",
                             validate_chat_source_coherence(db, contract))
            make_db([(CHAT_CITATION_MESSAGE_ID, json.dumps({"sources": []}))])
            expect_detection("chat coherence: empty sources",
                             validate_chat_source_coherence(db, contract))
            entry = json.loads(json.dumps(good_entry))
            entry["score"] = 1e9
            make_db([(CHAT_CITATION_MESSAGE_ID,
                      json.dumps({"sources": [entry]}))])
            expect_clean("no arbitrary score cap (finite large ok)",
                         validate_chat_source_coherence(db, contract))

    chat_coherence_checks()

    def isolation_locator():
        import shutil
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            for root in ISOLATION_ROOTS:
                for prefix in ("gate-files", "original-files-after-consumers"):
                    write_bytes(Path(td) / f"{prefix}-{root}.json",
                                json.dumps(manifest_fixture()).encode())
            paths = isolation_after_manifests(td)
            assert set(paths) == set(ISOLATION_ROOTS)
            assert paths["uploads"].endswith(
                "original-files-after-consumers-uploads.json"), paths
            expect_clean("isolation compare clean",
                         compare_isolation_manifests(td))
            missing_dir = shutil.copytree(td, os.path.join(td, "missing"))
            os.remove(os.path.join(
                missing_dir, "original-files-after-consumers-apps.json"))
            try:
                compare_isolation_manifests(missing_dir)
                raise AssertionError("missing-manifest guard missed")
            except GateArtifactMissing:
                pass
            broken = shutil.copytree(td, os.path.join(td, "b"))
            paths_b = isolation_after_manifests(broken)
            m = json.loads(Path(paths_b["uploads"]).read_text())
            m["files"]["a.txt"]["sha256"] = "changed"
            Path(paths_b["uploads"]).write_text(json.dumps(m))
            diffs = compare_isolation_manifests(broken)
            expect_detection("changed-hash isolation breach", diffs)
            assert any(d["kind"] == "changed_file" and d["root"] == "uploads"
                       for d in diffs), diffs

    isolation_locator()

    def summary_emitter_cli():
        """Subprocess CLI test with the CURRENT full named argset (run-J-like
        facts: consumers, direct-ip transport, source/target endpoints)."""
        import tempfile
        script = str(Path(__file__).resolve())
        base = ["summary-emitter",
                "--run-id", "restored-consumers-20261001-j",
                "--backup-ts", "20261002-000000",
                "--network-prefix", "crr-restored-consumers-20261001-j",
                "--neo4j-image", "docker.io/library/neo4j:5.26-community",
                "--neo4j-image-id",
                "2d8d9803fbe0cb13143971c97618d30a77dc597b96a2dea20a776a64e5fc9c8c",
                "--neo4j-digest",
                "sha256:5eb12ad77fa46ab73e23df9ea1f43f5c0f2a79523435577648e046be042b9b93",
                "--apoc-version", "5.26.31", "--neo4j-version", "5.26.31",
                "--sidecar-image-id", "cbc5747d77d0",
                "--mode", "full", "--chat-fixture", "seeded",
                "--chat-postt", "mutated+canary-verify-rejected",
                "--missing-blob", "data/chat/avatars/fx-user-0001.png",
                "--chat-verify-frozen", "ok", "--chat-verify-restored", "ok",
                "--fulltext-source", "3", "--fulltext-target", "3",
                "--result-text", "PASS: healthy restore accepted, "
                "valid-checksum-incomplete restore rejected, refusals held",
                "--consumers-requested", "1",
                "--target-bolt-port", "27114",
                "--transport-mode", "direct-ip",
                "--source-address", "bolt://10.89.0.2:7687",
                "--target-address", "bolt://10.89.0.3:7687"]

        def run(out_dir, out_file=None, drop=None):
            argv = [sys.executable, script]
            for a in base:
                argv.append(a)
            if out_file:
                argv += ["--out-file", out_file]
            argv += ["--out", out_dir]
            if drop:
                i = argv.index(drop)
                del argv[i:i + 2]
            return subprocess.run(argv, capture_output=True, text=True,
                                  timeout=120)

        with tempfile.TemporaryDirectory() as td:
            out_dir = os.path.join(td, "run")
            res = run(out_dir)
            assert res.returncode == 0, res.stderr.strip()[:400]
            summary = json.load(open(os.path.join(out_dir, "summary.json")))
            for key in ("run_id", "mode", "result", "images", "versions",
                        "fixture", "controls", "consumers", "transport",
                        "replay_command", "operator_restore_runbook",
                        "evidence_dir", "receipts_dir"):
                assert key in summary, f"summary key missing: {key}"
            assert summary["run_id"] == "restored-consumers-20261001-j"
            assert summary["transport"]["mode"] == "direct-ip"
            assert summary["transport"]["chosen_endpoints"] == {
                "source": "bolt://10.89.0.2:7687",
                "target": "bolt://10.89.0.3:7687"}
            assert summary["consumers"]["requested"] is True
            assert summary["consumers"]["target_bolt"] == "127.0.0.1:27114"
            assert summary["images"]["neo4j_image_id"].startswith("2d8d9803")
            assert summary["controls"]["incomplete"]["missing_blob"] == \
                "data/chat/avatars/fx-user-0001.png"
            assert len(base) == 47, \
                f"argset drift: {len(base)} items (want subcommand + 23 " \
                "named pairs; run() adds --out as the 24th pair)"
            expect_clean("full argset identity retained", [])
            separate = os.path.join(td, "selfcheck", "summary-emitter-selfcheck.json")
            res2 = run(out_dir, out_file=separate)
            assert res2.returncode == 0, res2.stderr.strip()[:400]
            again = json.load(open(separate))
            assert again == summary, "separate out-file diverged"
            assert os.path.exists(os.path.join(out_dir, "summary.json")), \
                "out-file mode must not disturb the default path"
            for drop in ("--target-address", "--consumers-requested",
                         "--backup-ts"):
                res3 = run(os.path.join(td, "neg"), drop=drop)
                assert res3.returncode != 0, \
                    f"missing required arg silently accepted: {drop}"
                assert not os.path.exists(
                    os.path.join(td, "neg", "summary.json")), \
                    "no partial/summary write on missing arg"

    summary_emitter_cli()

    def cli_transport_wiring():
        """Invoke the EXACT argv the harness uses, with the raw JSON piped on
        stdin (and once via explicit flag), through a real subprocess."""
        script = str(Path(__file__).resolve())
        docker_net = json.dumps([{
            "Name": "crr-cli-net", "Id": "netid-cli-d", "Driver": "bridge",
            "IPAM": {"Config": [{"Subnet": "10.89.10.0/24"}]}}])
        podman_net = json.dumps([{
            "name": "crr-cli-net", "id": "netid-cli-p", "driver": "bridge",
            "subnets": [{"subnet": "10.89.11.0/24"}]}])
        attachments = json.dumps(
            {"crr-cli-net": {"NetworkID": "netid-cli-d",
                             "IPAddress": "10.89.10.7"}})

        def run(args, stdin_data, expect_ok):
            res = subprocess.run(
                [sys.executable, script] + args,
                input=stdin_data, capture_output=True, text=True, timeout=120)
            if expect_ok:
                assert res.returncode == 0, \
                    f"CLI failed ({res.returncode}): {res.stderr.strip()[:300]}"
            else:
                assert res.returncode != 0, \
                    f"CLI accepted invalid input: {res.stdout.strip()[:200]}"
            return res.stdout.strip()

        out = run(["network-binding", "--expected-name", "crr-cli-net"],
                  docker_net, True).splitlines()
        assert out[0] == "netid-cli-d" and out[1] == "10.89.10.0/24" \
            and out[2] == "docker-ipam", out
        out = run(["network-binding", "--expected-name", "crr-cli-net"],
                  podman_net, True).splitlines()
        assert out[0] == "netid-cli-p" and out[1] == "10.89.11.0/24" \
            and out[2] == "podman-subnets", out
        out = run(["network-binding", "--network-json", docker_net,
                   "--expected-name", "crr-cli-net"], "", True).splitlines()
        assert out[0] == "netid-cli-d", out
        ip = run(["transport-endpoint", "--expected-network-id",
                  "netid-cli-d", "--subnet", "10.89.10.0/24"],
                 attachments, True)
        assert ip == "10.89.10.7", ip
        for why, args, stdin_data in [
            ("network-binding orphan name", ["network-binding",
             "--expected-name", "other-net"], docker_net),
            ("network-binding malformed stdin", ["network-binding",
             "--expected-name", "crr-cli-net"], "{not-json"),
            ("network-binding empty stdin", ["network-binding",
             "--expected-name", "crr-cli-net"], ""),
            ("network-binding unknown shape", ["network-binding",
             "--expected-name", "crr-cli-net"], json.dumps(
                 [{"Name": "crr-cli-net", "Id": "x"}])),
            ("transport-endpoint wrong id", ["transport-endpoint",
             "--expected-network-id", "other", "--subnet", "10.89.10.0/24"],
             attachments),
            ("transport-endpoint ip outside subnet", ["transport-endpoint",
             "--expected-network-id", "netid-cli-d", "--subnet",
             "10.50.0.0/16"], attachments),
            ("transport-endpoint malformed stdin", ["transport-endpoint",
             "--expected-network-id", "netid-cli-d", "--subnet",
             "10.89.10.0/24"], "{nope"),
            ("transport-endpoint empty stdin", ["transport-endpoint",
             "--expected-network-id", "netid-cli-d", "--subnet",
             "10.89.10.0/24"], ""),
        ]:
            run(args, stdin_data, False)
        print("CLI transport wiring: PASS (subprocess, same argv as harness)")

    cli_transport_wiring()

    print("SELFTEST PASS (oracle comparison + validation logic)")
    return 0


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def cmd_gen_fixture(args) -> int:
    files_dir = Path(args.files_dir)
    cypher_out = Path(args.cypher_out)
    files_dir.mkdir(parents=True, exist_ok=True)

    alpha_text = (
        "# Rehearsal Alpha\n\nCortex disposable restore rehearsal document. "
        "It's a \"test\" with escaping: back\\slash and 'quotes'.\n"
        "The quick brown fox mentions Rehearsal Alpha and Rehearsal Beta.\n"
    ).encode()
    beta_pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF\n"
    )
    custom_input = json.dumps(
        {"name": "rehearsal-input", "prompt": "Summarize {{document}}", "version": 1},
        indent=2,
    ).encode()
    skill_md = (
        "---\nname: rehearsal-skill\ndescription: Disposable restore fixture skill\n"
        "---\n\n# Rehearsal Skill\n\nSynthetic body.\n"
    ).encode()
    skill_sh = b"#!/bin/sh\necho rehearsal-skill-crr\n"
    skill_cfg = json.dumps({
        "version": 1,
        "fields": {
            "apiKey": {
                "type": "secret",
                "value": "enc:v1:crr-synthetic-encrypted-reference",
            }
        },
    }, indent=2).encode()
    app_manifest = json.dumps(
        {"id": "rehearsal-app", "name": "Rehearsal App", "version": "1.0.0",
         "capabilities": ["storage"]}, indent=2,
    ).encode()
    source_text = (
        "Fixture Source content for the disposable restore rehearsal.\n"
        "It's a \"test\" with escaping: back\\slash and 'quotes'.\n"
        "The fixture graph cites this document as "
        f"{CITATION_SOURCE_ID} ({CITATION_SOURCE_TITLE}).\n"
        "Mentions Rehearsal Alpha and Rehearsal Beta for graph linkage.\n"
    )

    write_bytes(files_dir / "uploads" / "doc-crr-1_rehearsal-alpha.md", alpha_text)
    write_bytes(files_dir / "uploads" / "doc-crr-2_rehearsal-beta.pdf", beta_pdf)
    write_bytes(
        files_dir / "uploads" / f"{CITATION_SOURCE_ID}_fixture-source.md",
        source_text.encode(),
    )
    write_bytes(files_dir / "custom_inputs" / "rehearsal-input.json", custom_input)
    write_bytes(files_dir / "skills" / "rehearsal-skill" / "SKILL.md", skill_md)
    write_bytes(files_dir / "skills" / "rehearsal-skill" / "scripts" / "run.sh", skill_sh)
    write_bytes(files_dir / "skills" / "rehearsal-skill" / "config.json", skill_cfg)
    write_bytes(files_dir / "apps" / "rehearsal-app" / "app.json", app_manifest)

    app_db = files_dir / "apps" / "rehearsal-app" / "storage.sqlite"
    app_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(app_db)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS kv ("
            " key TEXT PRIMARY KEY, value TEXT NOT NULL,"
            " size INTEGER NOT NULL, updated_at TEXT NOT NULL)"
        )
        rows = [
            ("transcripts/rehearsal-1", json.dumps({"role": "user", "text": "hello"}), FIXED_TS),
            ("sync/cursor", json.dumps({"cursor": 42}), FIXED_TS),
            ("rehearsal/state", json.dumps({"notes": "it's \"quoted\" \\ esc"}), FIXED_TS),
        ]
        for k, v, ts in rows:
            conn.execute(
                "INSERT OR REPLACE INTO kv (key, value, size, updated_at) "
                "VALUES (?, ?, ?, ?)", (k, v, len(v.encode()), ts),
            )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    def ch32(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:32]

    emb = [0.015625, -0.25, 0.5, 0.75, -0.875, 0.125, 0.375, -0.0625]
    alpha_content = alpha_text.decode()
    source_content = source_text

    def sha256_hex(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    read_prefix = READ_KEY_PLAINTEXT[:12]
    manage_prefix = MANAGE_KEY_PLAINTEXT[:12]
    read_hash = sha256_hex(READ_KEY_PLAINTEXT)
    manage_hash = sha256_hex(MANAGE_KEY_PLAINTEXT)
    src_chunk_id = f"{CITATION_SOURCE_ID}-chunk-0"
    cypher = f"""
CREATE (fxc:Collection {{id: '{SHARED_COLLECTION_ID}', name: 'Fixture Collection',
  description: 'chat-linked shared fixture collection',
  created_at: datetime('{FIXED_TS}')}});
CREATE (c1:Collection {{id: 'col-crr-1', name: 'Rehearsal Collection',
  description: 'disposable restore fixture', created_at: datetime('{FIXED_TS}')}});
CREATE (c2:Collection {{id: 'col-crr-2', name: 'Rehearsal Collection Two',
  description: 'scope-negative fixture collection',
  created_at: datetime('{FIXED_TS}')}});
CREATE (d1:Document {{id: 'doc-crr-1', filename: 'rehearsal-alpha.md',
  file_type: 'text/markdown', file_size: {len(alpha_text)},
  file_path: 'uploads/doc-crr-1_rehearsal-alpha.md',
  upload_date: datetime('{FIXED_TS}'), chunk_count: 1,
  processing_status: 'completed', error_message: '',
  progress_current: 1, progress_total: 1, progress_message: '',
  source: 'upload', git_connection_id: '', git_path: '', git_blob_sha: '',
  git_commit_sha: '', git_sync_status: ''}});
CREATE (d2:Document {{id: 'doc-crr-2', filename: 'rehearsal-beta.pdf',
  file_type: 'application/pdf', file_size: {len(beta_pdf)},
  file_path: 'uploads/doc-crr-2_rehearsal-beta.pdf',
  upload_date: datetime('2026-10-01T00:00:01Z'), chunk_count: 1,
  processing_status: 'completed', error_message: '',
  progress_current: 1, progress_total: 1, progress_message: '',
  source: 'upload', git_connection_id: '', git_path: '', git_blob_sha: '',
  git_commit_sha: '', git_sync_status: ''}});
CREATE (ds:Document {{id: '{CITATION_SOURCE_ID}', filename: '{CITATION_SOURCE_FILENAME}',
  file_type: 'text/markdown', file_size: {len(source_text.encode())},
  file_path: 'uploads/{CITATION_SOURCE_ID}_fixture-source.md',
  upload_date: datetime('{FIXED_TS}'), chunk_count: 1,
  processing_status: 'completed', error_message: '',
  progress_current: 1, progress_total: 1, progress_message: '',
  source: 'upload', git_connection_id: '', git_path: '', git_blob_sha: '',
  git_commit_sha: '', git_sync_status: ''}});
CREATE (ch1:Chunk {{id: 'doc-crr-1-chunk-0',
  content: {json.dumps(alpha_content)}, embedding: {emb},
  has_embedding: true, chunk_index: 0,
  metadata: "{{\\'page\\': 1, \\'language\\': \\'en\\'}}", content_hash: '{ch32(alpha_content)}'}});
CREATE (ch2:Chunk {{id: 'doc-crr-2-chunk-0',
  content: 'Rehearsal Beta synthetic pdf chunk with 100% coverage.',
  embedding: {emb}, has_embedding: true, chunk_index: 0,
  metadata: "{{\\'page\\': 1}}", content_hash: '{ch32("Rehearsal Beta synthetic pdf chunk with 100% coverage.")}'}});
CREATE (chs:Chunk {{id: '{CITATION_SOURCE_ID}-chunk-0',
  content: {json.dumps(source_content)}, embedding: {emb},
  has_embedding: true, chunk_index: 0,
  metadata: "{{\\'page\\': 1}}", content_hash: '{ch32(source_content)}'}});
CREATE (e1:Entity {{name: 'Rehearsal Alpha', type: 'CONCEPT',
  description: 'Synthetic entity: it\\'s a \\"test\\" \\\\ esc',
  mentions_count: 2, embedding: {emb},
  first_seen: datetime('{FIXED_TS}'), last_seen: datetime('{FIXED_TS}')}});
CREATE (e2:Entity {{name: 'Rehearsal Beta', type: 'CONCEPT',
  description: 'Synthetic entity beta', mentions_count: 1, embedding: {emb},
  first_seen: datetime('{FIXED_TS}'), last_seen: datetime('{FIXED_TS}')}});
CREATE (com1:Community {{id: 'com-crr-1', label: 'Rehearsal Community',
  summary: 'Synthetic community summary', level: 0, parent: '', stale: false}});
CREATE (k1:APIKey {{id: '{READ_KEY_ID}', name: 'fixture-read-key',
  key_prefix: '{read_prefix}', key_hash: '{read_hash}',
  permissions: ['read'], is_active: true,
  created_at: datetime('{FIXED_TS}'), created_by: 'admin',
  collection_scope: 'restricted', price_per_query: 0.0,
  research_multiplier: '1.0'}});
CREATE (k2:APIKey {{id: '{MANAGE_KEY_ID}', name: 'fixture-manage-key',
  key_prefix: '{manage_prefix}', key_hash: '{manage_hash}',
  permissions: ['manage'], is_active: true,
  created_at: datetime('{FIXED_TS}'), created_by: 'admin',
  collection_scope: 'all', price_per_query: 0.0,
  research_multiplier: '1.0'}});
CREATE (l1:APIKeyUsageLog {{key_id: '{READ_KEY_ID}', date: '2026-10-01',
  requests: 3, tokens_in: 100, tokens_out: 50}});
CREATE (s1:ApiSession {{id: 'ses-crr-000000000001', key_id: '{READ_KEY_ID}',
  name: 'rehearsal-session',
  history: '[{{"role": "user", "content": "restore rehearsal question"}}]',
  memory: '{{"curated": "synthetic opaque memory blob crr-v1"}}',
  turn_count: 2, created_at: datetime('{FIXED_TS}'),
  updated_at: datetime('2026-10-01T00:00:04Z')}});
CREATE (t1:TaskRecord {{task_id: 'task-crr-1', type: 'extract',
  status: 'completed', progress: 100, message: 'synthetic task',
  created_at: datetime('{FIXED_TS}'),
  updated_at: datetime('2026-10-01T00:00:05Z'), document_id: 'doc-crr-1'}});
CREATE (u1:LLMUsageDay {{date: '2026-10-01', completions: 12,
  completions_query: 7, completions_processing: 5}});
CREATE (m1:SystemMeta {{key: 'last_community_detection',
  value: '{FIXED_TS}'}});
CREATE (m2:SystemMeta {{key: 'embedding_model',
  value: 'synthetic-embed-model-crr'}});
CREATE (m3:SystemMeta {{key: 'extraction_model',
  value: 'synthetic-extract-model-crr'}});
CREATE (m4:SystemMeta {{key: 'fixture_version', value: '{FIXTURE_VERSION}'}});
CREATE (g1:GitConnection {{id: 'gitconn-crr-1', provider: 'github',
  display_name: 'synthetic/rehearsal',
  url: 'https://example.invalid/synthetic/rehearsal',
  target_collection_id: 'col-crr-1',
  last_synced_at: datetime('{FIXED_TS}'), status: 'active'}});
CREATE (x1:X402Config {{id: 'x402', enabled: false,
  facilitator_url: 'https://facilitator.invalid',
  default_price_per_query: 0.01, default_research_multiplier: '1.0',
  updated_at: datetime('{FIXED_TS}')}});
MATCH (c1:Collection {{id: 'col-crr-1'}}), (d1:Document {{id: 'doc-crr-1'}})
MERGE (c1)-[:CONTAINS]->(d1);
MATCH (c2:Collection {{id: 'col-crr-2'}}), (d2:Document {{id: 'doc-crr-2'}})
MERGE (c2)-[:CONTAINS]->(d2);
MATCH (fxc:Collection {{id: '{SHARED_COLLECTION_ID}'}}),
      (ds:Document {{id: '{CITATION_SOURCE_ID}'}})
MERGE (fxc)-[:CONTAINS]->(ds);
MATCH (d1:Document {{id: 'doc-crr-1'}}), (ch1:Chunk {{id: 'doc-crr-1-chunk-0'}})
MERGE (d1)-[:HAS_CHUNK]->(ch1);
MATCH (d2:Document {{id: 'doc-crr-2'}}), (ch2:Chunk {{id: 'doc-crr-2-chunk-0'}})
MERGE (d2)-[:HAS_CHUNK]->(ch2);
MATCH (ds:Document {{id: '{CITATION_SOURCE_ID}'}}),
      (chs:Chunk {{id: '{src_chunk_id}'}})
MERGE (ds)-[:HAS_CHUNK]->(chs);
MATCH (ch1:Chunk {{id: 'doc-crr-1-chunk-0'}}), (e1:Entity {{name: 'Rehearsal Alpha'}})
MERGE (ch1)-[:MENTIONS]->(e1);
MATCH (ch2:Chunk {{id: 'doc-crr-2-chunk-0'}}), (e2:Entity {{name: 'Rehearsal Beta'}})
MERGE (ch2)-[:MENTIONS]->(e2);
MATCH (chs:Chunk {{id: '{src_chunk_id}'}}),
      (e1:Entity {{name: 'Rehearsal Alpha'}})
MERGE (chs)-[:MENTIONS]->(e1);
MATCH (e1:Entity {{name: 'Rehearsal Alpha'}}), (e2:Entity {{name: 'Rehearsal Beta'}})
MERGE (e1)-[:RELATED_TO {{type: 'references', weight: 0.5}}]->(e2);
MATCH (com1:Community {{id: 'com-crr-1'}}), (e1:Entity {{name: 'Rehearsal Alpha'}})
MERGE (com1)-[:HAS_MEMBER]->(e1);
MATCH (com1:Community {{id: 'com-crr-1'}}), (e2:Entity {{name: 'Rehearsal Beta'}})
MERGE (com1)-[:HAS_MEMBER]->(e2);
MATCH (k1:APIKey {{id: '{READ_KEY_ID}'}}),
      (fxc:Collection {{id: '{SHARED_COLLECTION_ID}'}})
MERGE (k1)-[:HAS_ACCESS_TO]->(fxc);
MATCH (k1:APIKey {{id: '{READ_KEY_ID}'}}),
      (l1:APIKeyUsageLog {{key_id: '{READ_KEY_ID}', date: '2026-10-01'}})
MERGE (k1)-[:HAS_USAGE]->(l1);
"""
    cypher_out.write_text(cypher)
    contract = {
        "fixture_version": FIXTURE_VERSION,
        "fixture_pack": {
            "version": 2,
            "citation_source": {
                "id": CITATION_SOURCE_ID,
                "title": CITATION_SOURCE_TITLE,
                "utf8_snapshot": "chunk content of "
                                 f"{CITATION_SOURCE_ID}-chunk-0 (the exact "
                                 "value stored in the fixture; the chat "
                                 "content-exact check compares against this "
                                 "UTF-8 snapshot, not original file bytes)",
                "file_path": f"uploads/{CITATION_SOURCE_ID}_fixture-source.md",
                "filename": CITATION_SOURCE_FILENAME,
                "chunk_id": CITATION_SOURCE_CHUNK_ID,
                "chunk_cardinality": 1,
                "snapshot_text": source_content,
                "score_rule": "typed finite numeric source-quality score; "
                              "no arbitrary upper cap unless a contract "
                              "states one; no provider-derived values",
                "raw_bytes_gate": "backend-side only (original raw file "
                                  "bytes via the backend HTTP gate); the "
                                  "chat proxy /file route is intentionally "
                                  "404 (PROXY_ALLOWLIST route.ts:16-22) and "
                                  "is NOT part of the chat journey",
            },
            "coherence": {
                "citation_message_id": CHAT_CITATION_MESSAGE_ID,
                "note": "cross-repo preflight: seeded chat pack metadata "
                        "sources[0] must match the declared main fixture "
                        "source at the ACTUAL public typed Source boundary "
                        "(src/types/index.ts): document_id == id, sid == "
                        "id, chunk_id == declared, nested "
                        "metadata.filename == declared filename; flat "
                        "entry.filename is a legacy shape and rejected; "
                        "outer message filename not read; content "
                        "contained in declared snapshot; typed finite "
                        "score (no cap)",
            },
            "supported_citation_entry": "GET /documents/{id}/content "
                                        "(chunk-sorted, joined with '\\n\\n')",
            "gate_version": GATE_VERSION,
            "required_chat_check_ids": list(REQUIRED_CHAT_CHECK_IDS),
        },
        "shared_ids": {
            "read_key_id": READ_KEY_ID,
            "manage_key_id": MANAGE_KEY_ID,
            "collection_id": SHARED_COLLECTION_ID,
            "citation_source_id": CITATION_SOURCE_ID,
            "citation_source_title": CITATION_SOURCE_TITLE,
        },
        "key_material_synthetic": {
            "read_key_plaintext": READ_KEY_PLAINTEXT,
            "manage_key_plaintext": MANAGE_KEY_PLAINTEXT,
            "key_prefix_length": 12,
            "key_prefix": read_prefix,
            "hash_format": "sha256 hexdigest of plaintext",
            "read_key_hash": read_hash,
            "manage_key_hash": manage_hash,
        },
        "graph_labels_actual": {
            "key": "APIKey",
            "lookup": "by key_prefix (first 12 chars) + is_active, then sha256 verify",
        },
        "scope": {
            "read_key": "restricted to fx-collection-0001 (HAS_ACCESS_TO)",
            "manage_key": "collection_scope all",
            "scope_negative_target": "doc-crr-2 (col-crr-2)",
        },
        "files": {
            "citation_source": f"uploads/{CITATION_SOURCE_ID}_fixture-source.md",
        },
    }
    (files_dir.parent / "fixture-contract.json").write_text(
        json.dumps(contract, indent=1, sort_keys=True)
    )
    print(f"fixture v2 written: {cypher_out} + {files_dir} "
          f"(fixture_version={FIXTURE_VERSION})")


def _flat_prov_args(args):
    flat = []
    for f in args.file:
        flat += ["--file", f]
    return flat


def cmd_provenance(args) -> int:
    argv = ["--out", args.out] + _flat_prov_args(args) + [
        "--app-repo", args.app_repo, "--chat-repo", args.chat_repo]
    out, files, app_repo, chat_repo = parse_provenance_cli(argv)
    meta = {}
    for kv in args.meta or []:
        k, _, v = kv.partition("=")
        meta[k] = v
    prov = build_provenance(out, files, app_repo, chat_repo,
                            harness_meta=meta or None)
    print(f"provenance: {out} ({len(prov['input_files'])} input files, "
          f"code manifest {prov['code_manifest']['files']} files, "
          f"digest {prov['code_manifest']['digest'][:12]})")
    return 0


def cmd_provenance_verify(args) -> int:
    argv = _flat_prov_args(args) + [
        "--app-repo", args.app_repo, "--chat-repo", args.chat_repo,
        "--out", args.out]
    _, files, app_repo, chat_repo = parse_provenance_cli(argv)
    res = verify_provenance(args.provenance, files, app_repo, chat_repo)
    print("provenance re-verified: "
          f"{res['input_files']} input files unchanged, "
          f"code manifest {res['code_manifest_files']} files unchanged "
          f"(digest {res['digest'][:12]})")
    return 0


def cmd_receipt(args) -> int:
    receipt = {
        "stage": args.stage,
        "status": args.status,
        "started_utc": args.started,
        "ended_utc": datetime_utcnow(),
        "exit": args.exit,
        "log": args.log,
        "fields": {},
    }
    for kv in args.field or []:
        k, _, v = kv.partition("=")
        receipt["fields"][k] = v
    for ref in args.artifact or []:
        receipt.setdefault("artifacts", []).append(ref)
    Path(args.out).write_text(json.dumps(receipt, indent=1, sort_keys=True))
    print(f"receipt: {args.out} [{args.status}]")
    return 0


def datetime_utcnow() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_hashes(args) -> int:
    out = {}
    for p in args.paths:
        pp = Path(p)
        out[str(pp)] = sha256_file(pp)
    Path(args.out).write_text(json.dumps(out, indent=1, sort_keys=True))
    print(f"hashes: {args.out} ({len(out)} files)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("provenance")
    p.add_argument("--out", required=True)
    p.add_argument("--file", action="append", required=True)
    p.add_argument("--app-repo", required=True)
    p.add_argument("--chat-repo", required=True)
    p.add_argument("--meta", action="append", default=[])
    p.set_defaults(fn=cmd_provenance)

    p = sub.add_parser("provenance-verify")
    p.add_argument("--provenance", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--file", action="append", required=True)
    p.add_argument("--app-repo", required=True)
    p.add_argument("--chat-repo", required=True)
    p.set_defaults(fn=cmd_provenance_verify)

    p = sub.add_parser("transport-endpoint")
    p.add_argument("--networks-json", default=None)
    p.add_argument("--expected-network-id", required=True)
    p.add_argument("--subnet", required=True)
    p.set_defaults(fn=cmd_transport_endpoint)

    p = sub.add_parser("network-binding")
    p.add_argument("--network-json", default=None)
    p.add_argument("--expected-name", required=True)
    p.set_defaults(fn=cmd_network_binding)

    p = sub.add_parser("chat-coherence")
    p.add_argument("--chat-db", required=True)
    p.add_argument("--contract", required=True)
    p.set_defaults(fn=cmd_chat_coherence)

    p = sub.add_parser("isolation-compare")
    p.add_argument("--evidence-dir", required=True)
    p.add_argument("--gate-prefix", default="gate-files")
    p.add_argument("--after-prefix", default="original-files-after-consumers")
    p.set_defaults(fn=cmd_isolation_compare)

    p = sub.add_parser("summary-emitter")
    p.add_argument("--out", required=True)
    p.add_argument("--out-file", default=None)
    p.add_argument("--run-id", required=True)
    p.add_argument("--backup-ts", required=True)
    p.add_argument("--network-prefix", required=True)
    p.add_argument("--neo4j-image", required=True)
    p.add_argument("--neo4j-image-id", required=True)
    p.add_argument("--neo4j-digest", required=True)
    p.add_argument("--apoc-version", required=True)
    p.add_argument("--neo4j-version", required=True)
    p.add_argument("--sidecar-image-id", required=True)
    p.add_argument("--mode", required=True)
    p.add_argument("--chat-fixture", required=True)
    p.add_argument("--chat-postt", required=True)
    p.add_argument("--missing-blob", required=True)
    p.add_argument("--chat-verify-frozen", required=True)
    p.add_argument("--chat-verify-restored", required=True)
    p.add_argument("--fulltext-source", required=True)
    p.add_argument("--fulltext-target", required=True)
    p.add_argument("--result-text", required=True)
    p.add_argument("--consumers-requested", required=True)
    p.add_argument("--target-bolt-port", required=True)
    p.add_argument("--transport-mode", required=True)
    p.add_argument("--source-address", required=True)
    p.add_argument("--target-address", required=True)
    p.set_defaults(fn=cmd_summary_emitter)

    p = sub.add_parser("receipt")
    p.add_argument("--out", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--status", choices=["ok", "fail", "skipped"], required=True)
    p.add_argument("--started", required=True)
    p.add_argument("--exit", default="0")
    p.add_argument("--log")
    p.add_argument("--field", action="append", default=[])
    p.add_argument("--artifact", action="append", default=[])
    p.set_defaults(fn=cmd_receipt)

    p = sub.add_parser("hashes")
    p.add_argument("--out", required=True)
    p.add_argument("--paths", nargs="+", required=True)
    p.set_defaults(fn=cmd_hashes)


    p = sub.add_parser("gen-fixture")
    p.add_argument("--cypher-out", required=True)
    p.add_argument("--files-dir", required=True)
    p.set_defaults(fn=cmd_gen_fixture)

    p = sub.add_parser("capture-graph")
    p.add_argument("--container", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=lambda a: capture_graph(a.container, a.password, Path(a.out)) or 0)

    p = sub.add_parser("capture-files")
    p.add_argument("--dir", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=lambda a: capture_files(Path(a.dir), Path(a.out)) or 0)

    p = sub.add_parser("capture-sqlite")
    p.add_argument("--path", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=lambda a: capture_sqlite(Path(a.path), Path(a.out)) or 0)

    p = sub.add_parser("compare")
    p.add_argument("--expected-graph", required=True)
    p.add_argument("--actual-graph", required=True)
    p.add_argument("--expected-roots", nargs="+", required=True)
    p.add_argument("--expected-files", nargs="+", required=True)
    p.add_argument("--actual-roots", nargs="+", required=True)
    p.add_argument("--actual-files", nargs="+", required=True)
    p.add_argument("--sqlite-names", nargs="*", default=[])
    p.add_argument("--expected-sqlite", nargs="*", default=[])
    p.add_argument("--actual-sqlite", nargs="*", default=[])
    p.add_argument("--absent-graph")
    p.add_argument("--absent-files")
    p.add_argument("--verdict", required=True)
    p.add_argument("--expect", choices=["pass", "reject"], default="pass")
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("selftest")
    p.set_defaults(fn=cmd_selftest)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
