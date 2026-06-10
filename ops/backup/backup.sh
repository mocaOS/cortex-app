#!/bin/sh
# Cortex backup job — runs inside the backup sidecar (see docker-compose.backup.yml).
#
# Tier 1 (default, works on Neo4j Community AND Enterprise):
#   online logical export via APOC (apoc.export.cypher.all) + tar of the
#   uploads/custom_inputs volumes. No downtime.
#
# Tier 2 (Enterprise only, NEO4J_ENTERPRISE_BACKUP=true):
#   online physical backup via `neo4j-admin database backup` — faster restore,
#   transactionally consistent.
#
# Retention: BACKUP_RETENTION_DAYS (default 7).
#
# Restore (Tier 1):
#   1. Stop the backend:        docker compose stop backend
#   2. Wipe the graph:          cypher-shell "MATCH (n) DETACH DELETE n"
#      (or start from an empty neo4j_data volume)
#   3. Replay the export:       cypher-shell -f /backups/<ts>/graph.cypher
#   4. Untar the file volumes:  tar -xzf /backups/<ts>/files.tar.gz -C /
#   5. Start the backend and verify /api/stats counts.

set -eu

BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
NEO4J_ADDRESS="${NEO4J_ADDRESS:-neo4j://neo4j:7687}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:?NEO4J_PASSWORD is required}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
ENTERPRISE="${NEO4J_ENTERPRISE_BACKUP:-false}"

ts=$(date -u +%Y%m%d-%H%M%S)
dest="$BACKUP_ROOT/$ts"
mkdir -p "$dest"

echo "[backup] starting backup to $dest"

if [ "$ENTERPRISE" = "true" ]; then
    # Tier 2: physical online backup (Enterprise only)
    neo4j-admin database backup neo4j \
        --from="${NEO4J_BACKUP_ADDRESS:-neo4j:6362}" \
        --to-path="$dest" \
        && echo "[backup] enterprise physical backup complete"
else
    # Tier 1: APOC logical export streamed back through cypher-shell.
    # apoc.export.cypher.all with stream:true avoids requiring write access
    # to the DB container's filesystem.
    cypher-shell -a "$NEO4J_ADDRESS" -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" \
        --format plain \
        "CALL apoc.export.cypher.all(null, {stream: true, format: 'cypher-shell', useOptimizations: {type: 'UNWIND_BATCH', unwindBatchSize: 100}}) YIELD cypherStatements RETURN cypherStatements" \
        | sed -e 's/^"//' -e 's/"$//' -e 's/\\n/\n/g' -e 's/\\"/"/g' \
        > "$dest/graph.cypher"
    echo "[backup] graph export complete ($(wc -c < "$dest/graph.cypher") bytes)"
fi

# File volumes (mounted read-only into this container)
if [ -d /data/uploads ] || [ -d /data/custom_inputs ]; then
    tar -czf "$dest/files.tar.gz" \
        $( [ -d /data/uploads ] && echo /data/uploads ) \
        $( [ -d /data/custom_inputs ] && echo /data/custom_inputs ) \
        2>/dev/null || true
    echo "[backup] file volumes archived"
fi

# Retention
find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d -mtime "+$RETENTION_DAYS" \
    -exec rm -rf {} + 2>/dev/null || true

echo "[backup] done: $dest"
