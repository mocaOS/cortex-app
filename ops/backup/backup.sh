#!/bin/bash
# Cortex backup job — runs inside the backup sidecar (see docker-compose.backup.yml).
#
# Tier 1 (default, works on Neo4j Community AND Enterprise):
#   online logical export via APOC (apoc.export.cypher.all) written server-side
#   into the shared backups volume (the neo4j service mounts it at its import
#   directory) + tar of the uploads/custom_inputs/chat volumes. No downtime,
#   no quoting round-trip: APOC writes the file itself, byte-exact.
#   REQUIRES on the neo4j service (see the deploy composes):
#     - NEO4J_apoc_export_file_enabled=true
#     - the backups volume mounted at /var/lib/neo4j/import
#
# Tier 2 (Enterprise only, NEO4J_ENTERPRISE_BACKUP=true):
#   online physical backup via `neo4j-admin database backup` — faster restore,
#   transactionally consistent.
#
# A backup only counts once it is verified: export row counts are checked
# against the live DB, checksums are written, and the run is stamped with
# $dest/.complete + $BACKUP_ROOT/LAST_SUCCESS (the compose healthcheck watches
# LAST_SUCCESS freshness). Retention runs ONLY after a verified success and
# never deletes the newest complete backup, so a string of failures can't
# rotate away the last good copy.
#
# Restore: /restore.sh <timestamp> (see restore.sh header for the full runbook).

set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
NEO4J_ADDRESS="${NEO4J_ADDRESS:-neo4j://neo4j:7687}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:?NEO4J_PASSWORD is required}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
ENTERPRISE="${NEO4J_ENTERPRISE_BACKUP:-false}"

cy() {
    cypher-shell -a "$NEO4J_ADDRESS" -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" \
        --format plain "$1"
}

fail() { echo "[backup] FAILED: $*" >&2; exit 1; }

ts=$(date -u +%Y%m%d-%H%M%S)
dest="$BACKUP_ROOT/$ts"

# The neo4j service writes the export into this volume as the neo4j user
# (uid 7474); volumes created by older sidecar versions are root-owned.
chown 7474:7474 "$BACKUP_ROOT" 2>/dev/null || true
mkdir -p "$dest"

echo "[backup] starting backup to $dest"

cy "RETURN 1" >/dev/null || fail "cannot reach neo4j at $NEO4J_ADDRESS"

on_exit() {
    status=$?
    if [ "$status" -ne 0 ]; then
        # No .complete marker => the stale-partial sweep of the next successful
        # run removes $dest; the healthcheck goes unhealthy via LAST_SUCCESS age.
        echo "[backup] FAILED (exit $status) — partial dir $dest left unmarked" >&2
    fi
}
trap on_exit EXIT

if [ "$ENTERPRISE" = "true" ]; then
    # Tier 2: physical online backup (Enterprise only)
    neo4j-admin database backup neo4j \
        --from="${NEO4J_BACKUP_ADDRESS:-neo4j:6362}" \
        --to-path="$dest"
    echo "[backup] enterprise physical backup complete"
else
    # Tier 1: APOC writes the export server-side into its import directory,
    # which the deploy composes bind to the same volume as $BACKUP_ROOT.
    # The export runs in a single transaction: a 55k-node graph with 24k
    # embedded chunks exports in ~30s; if a very large graph approaches the
    # server-side CORTEX_NEO4J_TX_TIMEOUT (default 300s), raise that setting.
    export_name="graph-$ts.cypher"
    if ! stats=$(cy "CALL apoc.export.cypher.all('$export_name', {format: 'cypher-shell', useOptimizations: {type: 'UNWIND_BATCH', unwindBatchSize: 100}, ifNotExists: true}) YIELD nodes, relationships, properties RETURN nodes, relationships, properties"); then
        fail "graph export failed — ensure the neo4j service sets NEO4J_apoc_export_file_enabled=true and mounts the backups volume at /var/lib/neo4j/import (see the deploy compose); on very large graphs raise CORTEX_NEO4J_TX_TIMEOUT"
    fi

    # Verify before calling it a backup.
    exported="$BACKUP_ROOT/$export_name"
    [ -s "$exported" ] || fail "export file missing or empty: $exported"
    exp_nodes=$(echo "$stats" | tail -n 1 | cut -d, -f1 | tr -d ' ')
    db_nodes=$(cy "MATCH (n) RETURN count(n)" | tail -n 1 | tr -d ' ')
    case "$exp_nodes" in *[!0-9]*|"") fail "could not parse export stats: $stats" ;; esac
    if [ "$db_nodes" -gt 0 ]; then
        # Tolerate ingestion drift between the export and the count query.
        min_ok=$(( db_nodes * 9 / 10 ))
        [ "$exp_nodes" -ge "$min_ok" ] || \
            fail "export covered $exp_nodes of $db_nodes nodes — refusing to trust it"
    fi
    grep -q '^:commit' "$exported" || fail "export lacks :commit blocks — not a cypher-shell replay file"
    raw_bytes=$(wc -c < "$exported")
    mv "$exported" "$dest/graph.cypher"
    # Embedding-heavy exports are large (~1.7GB for the 55k-node reference
    # graph) and compress 3-4x; restore.sh replays the .gz via gunzip -c.
    gzip -f "$dest/graph.cypher"
    echo "[backup] graph export complete ($exp_nodes nodes, $raw_bytes bytes raw, $(wc -c < "$dest/graph.cypher.gz") bytes compressed)"
    printf '{"timestamp": "%s", "nodes": %s, "relationships": %s, "db_nodes_at_export": %s, "raw_bytes": %s}\n' \
        "$ts" "$exp_nodes" "$(echo "$stats" | tail -n 1 | cut -d, -f2 | tr -d ' ')" "$db_nodes" "$raw_bytes" \
        > "$dest/meta.json"
fi

# File volumes (mounted read-only into this container). /data/chat carries
# the cortex-chat SQLite DB + assets on tenant stacks that run the chat service.
if [ -d /data/uploads ] || [ -d /data/custom_inputs ] || [ -d /data/chat ]; then
    rc=0
    tar -czf "$dest/files.tar.gz" \
        $( [ -d /data/uploads ] && echo /data/uploads ) \
        $( [ -d /data/custom_inputs ] && echo /data/custom_inputs ) \
        $( [ -d /data/chat ] && echo /data/chat ) \
        || rc=$?
    # GNU tar exit 1 = "file changed while reading" (live volume) — acceptable;
    # >=2 is a real failure and must not be swallowed.
    [ "$rc" -le 1 ] || fail "file-volume archive failed (tar exit $rc)"
    echo "[backup] file volumes archived"
fi

# Integrity manifest + success markers (the healthcheck watches LAST_SUCCESS).
(cd "$dest" && sha256sum -- * > SHA256SUMS)
touch "$dest/.complete"
ln -sfn "$ts" "$BACKUP_ROOT/latest"
date -u +%s > "$BACKUP_ROOT/LAST_SUCCESS"

# Retention — only reached after a verified success, and the backup just made
# ($ts) is always excluded, so at least one complete backup always survives.
# Partial dirs (no .complete) from failed runs are swept after a day.
find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d -mtime +0 ! -name "$ts" \
    -exec sh -c '[ ! -f "$1/.complete" ] && rm -rf "$1"' _ {} \; 2>/dev/null || true
find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d -mtime "+$RETENTION_DAYS" ! -name "$ts" \
    -exec rm -rf {} + 2>/dev/null || true

echo "[backup] done: $dest"
