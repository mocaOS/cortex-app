#!/bin/bash
# Cortex restore — replays a Tier-1 backup produced by backup.sh.
#
# Runbook (from the host, in the stack directory):
#   1. ls the available backups:      docker compose exec backup ls /backups
#   2. Stop the backend:              docker compose stop backend
#   3. Restore the graph:             docker compose exec -e RESTORE_WIPE=yes backup /restore.sh <timestamp>
#   4. Restore the file volumes (the sidecar mounts them read-only, so this
#      runs in a throwaway container — adjust volume names to your stack):
#        docker run --rm \
#          -v <stack>_uploads_data:/data/uploads \
#          -v <stack>_custom_inputs_data:/data/custom_inputs \
#          -v <stack>_chat_data:/data/chat \
#          -v <stack>_skills_data:/data/skills \
#          -v <stack>_apps_data:/data/apps \
#          -v <stack>_backups:/backups:ro \
#          alpine tar -xzf /backups/<timestamp>/files.tar.gz -C /
#   5. Start the backend:             docker compose start backend
#      Startup recreates every constraint/index — including the vector indexes,
#      which the logical export does not carry. /health reports
#      schema_initialized=true once done.
#   6. Verify document/entity counts on GET /api/stats.
#
# RESTORE_WIPE=yes is required: step 3 DETACH DELETEs the entire graph — and
# drops its constraints and indexes, which the replay recreates — before
# replaying the export. Vector indexes are left alone (the export does not carry
# them; the backend rebuilds them at startup).

set -euo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/backups}"
NEO4J_ADDRESS="${NEO4J_ADDRESS:-neo4j://neo4j:7687}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:?NEO4J_PASSWORD is required}"

cy() {
    cypher-shell -a "$NEO4J_ADDRESS" -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" \
        --format plain "$1"
}

fail() { echo "[restore] FAILED: $*" >&2; exit 1; }

ts="${1:?usage: restore.sh <backup-timestamp>  (list them: ls $BACKUP_ROOT)}"
src="$BACKUP_ROOT/$ts"

[ -d "$src" ] || fail "no such backup: $src"
[ -f "$src/.complete" ] || fail "backup $ts is incomplete (no .complete marker) — pick another"
[ -f "$src/graph.cypher.gz" ] || fail "backup $ts has no graph.cypher.gz (enterprise physical backup? use neo4j-admin database restore)"
[ "${RESTORE_WIPE:-}" = "yes" ] || fail "this wipes the entire graph first — re-run with RESTORE_WIPE=yes"

echo "[restore] verifying checksums..."
(cd "$src" && sha256sum -c --quiet SHA256SUMS) \
    || fail "checksum mismatch — backup $ts is corrupt, pick another"

cy "RETURN 1" >/dev/null || fail "cannot reach neo4j at $NEO4J_ADDRESS"

# The wipe batches its deletes and the replay commits per :begin/:commit
# block, so each transaction stays comfortably inside the server-side
# CORTEX_NEO4J_TX_TIMEOUT (default 300s). Raise that setting if a very large
# graph's restore hits it.
echo "[restore] wiping graph..."
cy "MATCH (n) CALL { WITH n DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS" >/dev/null

# The wipe has to take the schema with it, not just the data.
#
# apoc.export.cypher.all({ifNotExists: true}) emits `IF NOT EXISTS` for
# constraints and plain indexes but NOT for fulltext indexes — it writes a bare
# `CREATE FULLTEXT INDEX chunk_content FOR (n:Chunk) ON EACH [n.content]`. The
# backend creates that schema at startup, so on any live instance the replay hit
# "An equivalent index already exists", rolled back its whole transaction, and
# left the graph empty: the wipe above had already run. Restore therefore only
# ever worked against a schema-less database, which is not the case a restore is
# for. Dropping the schema first makes the replay authoritative for both halves
# of what it carries.
#
# Two exclusions, both deliberate:
#   LOOKUP — the built-in token indexes; the export does not recreate them.
#   VECTOR — the export does not carry them either (backend startup rebuilds
#            them), and dropping them would force a full re-embed of every
#            chunk on a large graph for no reason.
# Dropping a constraint also drops its backing index, so constraints go first.
echo "[restore] dropping schema (the replay recreates what it carries)..."
names() { cy "$1" | tail -n +2 | tr -d '"' | sed '/^$/d'; }
for c in $(names "SHOW CONSTRAINTS YIELD name RETURN name"); do
    cy "DROP CONSTRAINT \`$c\` IF EXISTS" >/dev/null
done
for i in $(names "SHOW INDEXES YIELD name, type WHERE NOT type IN ['LOOKUP', 'VECTOR'] RETURN name"); do
    cy "DROP INDEX \`$i\` IF EXISTS" >/dev/null
done

echo "[restore] replaying $src/graph.cypher.gz..."
# A failure here is the one case where the operator is worse off than when they
# started — the wipe has run — so it must never be reported as a bare shell
# error. The backup itself is untouched and still checksum-verified, so the
# recovery is simply to re-run once the cause is fixed.
if ! gunzip -c "$src/graph.cypher.gz" | \
    cypher-shell -a "$NEO4J_ADDRESS" -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" \
        --fail-fast >/dev/null; then
    echo "[restore] ---------------------------------------------------------" >&2
    echo "[restore] REPLAY FAILED — the graph is EMPTY right now: the wipe" >&2
    echo "[restore] completed but the replay did not." >&2
    echo "[restore]" >&2
    echo "[restore] Your backup is intact and still verified:" >&2
    echo "[restore]   $src" >&2
    echo "[restore] Nothing about it changed. Fix the error above, then re-run" >&2
    echo "[restore] exactly the same command to try again." >&2
    echo "[restore] ---------------------------------------------------------" >&2
    exit 1
fi

nodes=$(cy "MATCH (n) RETURN count(n)" | tail -n 1 | tr -d ' ')
echo "[restore] graph restored: $nodes nodes"
if [ -f "$src/meta.json" ]; then
    echo "[restore] backup metadata: $(cat "$src/meta.json")"
fi

if [ -f "$src/files.tar.gz" ]; then
    if tar -xzf "$src/files.tar.gz" -C / 2>/dev/null; then
        echo "[restore] file volumes restored"
    else
        echo "[restore] NOTE: file volumes are mounted read-only here — restore them" \
             "from the host (step 4 of the runbook in this script's header)."
    fi
fi

echo "[restore] done. Start the backend (it recreates all indexes, including" \
     "vector indexes) and verify counts on /api/stats."
