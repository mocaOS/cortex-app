#!/bin/bash
# Disposable whole-stack restore rehearsal (qa/restore).
#
# Builds a two-store disposable Neo4j 5.26-community environment, seeds a
# deterministic fixture (graph + five file roots incl. the companion chat
# fixture), snapshots it with the ACTUAL ops/backup/backup.sh, then exercises
# the ACTUAL ops/backup/restore.sh against a clean target with an independent
# frozen oracle:
#
#   1. healthy full restore       -> oracle must PASS (graph by domain IDs,
#                                    per-file SHA-256, SQLite rows, schema
#                                    definitions incl. deliberately retained
#                                    vector index, post-T writes absent)
#   2. valid-checksum incomplete  -> oracle must REJECT (missing avatar blob)
#   3. refusal controls           -> restore.sh refuses with its exact
#                                    diagnostics, before the wipe; exact
#                                    target graph contents unchanged
#
# Every stage writes a JSON receipt BEFORE cleanup (fail-closed). On failure
# the run stops with all disposable resources left in place for diagnosis.
#
# Usage:
#   qa/restore/rehearsal.sh [--run-id ID] [--chat-fixture PATH]
#                           [--no-chat] [--no-cleanup]
# --no-chat runs a REDUCED SMOKE (no chat fixture) whose result is NOT
# whole-stack restore evidence; the full gate requires the chat helper.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ORACLE="$REPO/qa/restore/oracle.py"
CHAT_SCRIPT=""
CHAT_CONSUMER="$REPO/../cortex-chat/scripts/restore-consumer.mjs"
BACKEND_CONSUMER="$REPO/qa/restore/backend-consumer.py"
CHAT_APP_NODE="${CORTEX_CHAT_APP_NODE:-}"
NO_DNS="${CORTEX_RESTORE_PODMAN_NO_DNS:-0}"
NO_CHAT=0
CONSUMERS=0
CLEANUP=1
usage_fail() { echo "usage error: $*" >&2; exit 2; }
while [ $# -gt 0 ]; do
    case "$1" in
        --run-id)
            [ $# -ge 2 ] || usage_fail "--run-id requires a value"
            RUN_ID="$2"; shift 2 ;;
        --chat-fixture)
            [ $# -ge 2 ] || usage_fail "--chat-fixture requires a value"
            CHAT_SCRIPT="$2"; shift 2 ;;
        --chat-consumer)
            [ $# -ge 2 ] || usage_fail "--chat-consumer requires a value"
            CHAT_CONSUMER="$2"; shift 2 ;;
        --chat-app-node)
            [ $# -ge 2 ] || usage_fail "--chat-app-node requires a value"
            CHAT_APP_NODE="$2"; shift 2 ;;
        --podman-no-dns) NO_DNS=1; shift ;;
        --consumers) CONSUMERS=1; shift ;;
        --no-chat) NO_CHAT=1; shift ;;
        --no-cleanup) CLEANUP=0; shift ;;
        *) usage_fail "unknown arg: $1" ;;
    esac
done

if [ "${RUN_ID:-}" = "" ]; then
    RUN_ID="$(date -u +%Y%m%d-%H%M%S)-$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi
if ! printf '%s' "$RUN_ID" | grep -Eq '^[a-z0-9][a-z0-9-]{0,47}$'; then
    usage_fail "run-id must match ^[a-z0-9][a-z0-9-]{0,47}\$: got '$RUN_ID'"
fi

NEO4J_IMAGE="${NEO4J_IMAGE:-docker.io/library/neo4j:5.26-community}"
ALPINE_IMAGE="${ALPINE_IMAGE:-docker.io/library/alpine:3.20}"
RRP="crr-${RUN_ID}"
WSBASE="$HOME/.local/share/cortex-restore-rehearsal"
WS="$WSBASE/${RUN_ID}"
OUTBASE="$REPO/qa/restore/output"
OUT="$OUTBASE/${RUN_ID}"
EVID="$OUT/evidence"
RECEIPTS="$OUT/receipts"
LOGS="$OUT/logs"
CHAT_DB_REL="cortex-chat.db"
CHAT_MODE="smoke"
if [ "$NO_CHAT" = 0 ]; then
    CHAT_MODE="full"
    if [ -z "$CHAT_SCRIPT" ]; then
        CHAT_SCRIPT="$REPO/../cortex-chat/scripts/restore-fixture.mjs"
    fi
fi
NEO_PASS="crrpass-${RUN_ID}"
T_SHORT=60; T_CMD=600; T_BUILD=900
if [ "$NO_DNS" = 1 ]; then TRANSPORT_MODE="direct-ip"; else TRANSPORT_MODE="dns"; fi
STAGE_N=0
STAGE_START=""
STAGE_NAME=""

if [ "$CHAT_MODE" = full ] && [ ! -f "$CHAT_SCRIPT" ]; then
    echo "FATAL: chat fixture script not found at: $CHAT_SCRIPT" >&2
    echo "       (full gate requires it; pass --no-chat for the reduced smoke)" >&2
    exit 1
fi

# Transport endpoint refresh: the container NAME is the stable identity; the
# internal IP is ephemeral (rootless podman may reassign it across stop/start,
# and a stale target IP could even point at the consumer-copy store). Any
# claimed destructive operation must refresh the actual internal mapping for
# the named run-owned container and re-validate network identity + subnet with
# the existing oracle transport helper. DNS mode keeps the name-based address
# and records the container id as identity. No new gate semantics — the
# existing allowed-transport constraint, enforced at the claimed operation.
refresh_address() {
    local role="$1" cname ip cid
    if [ "$role" = source ]; then cname="$RRP-neo4j-src"; else cname="$RRP-neo4j-tgt"; fi
    cid="$(timeout "$T_SHORT" docker inspect "$cname" --format '{{.Id}}' \
        2>> "$LOGS/transport-refresh.log" || true)"
    if [ -z "$cid" ]; then
        echo "REFRESH-FAIL $role: container $cname not inspectable @ $(NOW)" \
            >> "$LOGS/transport-refresh.log"
        return 1
    fi
if [ "$NO_DNS" = 1 ]; then
        ip="$(timeout "$T_SHORT" docker inspect "$cname" \
            --format '{{json .NetworkSettings.Networks}}' \
            2>> "$LOGS/transport-refresh.log" \
            | python3 "$ORACLE" transport-endpoint \
                --expected-network-id "$NET_ID" --subnet "$NET_SUBNET")" \
            || { echo "REFRESH-FAIL $role: binding validation failed for $cname @ $(NOW)" \
                 >> "$LOGS/transport-refresh.log"; return 1; }
        REFRESHED_ADDRESS="bolt://$ip:7687"
        REFRESHED_IP="$ip"
    else
        REFRESHED_ADDRESS="neo4j://$cname:7687"
        REFRESHED_IP="<name-based>"
    fi
    REFRESHED_CID="$cid"
    echo "refresh $role: container_id=$cid ip=$REFRESHED_IP address=$REFRESHED_ADDRESS @ $(NOW)" \
        >> "$LOGS/transport-refresh.log"
    return 0
}

# ---------------------------------------------------------------- preflight
stage_begin() {
    STAGE_N=$((STAGE_N + 1))
    STAGE_NAME="$1"
    STAGE_START="$(NOW)"
    echo "=== [$STAGE_N] $STAGE_NAME @ $(NOW)"
}

NOW() { date -u +%Y-%m-%dT%H:%M:%SZ; }

receipt() {
    local status="$1" exit_code="$2" log="$3"; shift 3
    local fargs=() logargs=() kv
    for kv in "$@"; do fargs+=(--field "$kv"); done
    [ -n "$log" ] && logargs=(--log "$log")
    if ! python3 "$ORACLE" receipt \
        --out "$RECEIPTS/$(printf '%02d' "$STAGE_N")-${STAGE_NAME}.json" \
        --stage "$STAGE_NAME" --status "$status" --started "$STAGE_START" \
        --exit "$exit_code" "${logargs[@]}" "${fargs[@]}" >/dev/null; then
        echo "FATAL: receipt write failed for stage $STAGE_NAME (fail closed)" >&2
        exit 1
    fi
}

stage_ok() {
    local log="$1"; shift
    receipt ok 0 "$log" "$@"
    echo "=== [$STAGE_N] $STAGE_NAME OK"
}

die() {
    local log="$1" msg="$2"
    receipt fail 1 "$log" "error=$msg" || true
    echo "=== [$STAGE_N] $STAGE_NAME FAILED: $msg"
    echo "receipt: $RECEIPTS/$(printf '%02d' "$STAGE_N")-${STAGE_NAME}.json"
    echo "log: $log"
    echo "disposable resources left in place (prefix $RRP) for diagnosis."
    exit 1
}

collision_check() {
    local found=0 list=""
    local names
    names="$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E "^${RRP}-" || true)"
    [ -n "$names" ] && { list+="$names"$'\n'; found=1; }
    names="$(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -E "^${RRP}-" || true)"
    [ -n "$names" ] && { list+="$names"$'\n'; found=1; }
    names="$(docker network ls --format '{{.Name}}' 2>/dev/null | grep -E "^${RRP}-" || true)"
    [ -n "$names" ] && { list+="$names"$'\n'; found=1; }
    names="$(docker images --format '{{.Repository}}' 2>/dev/null | grep -E "(^|/)${RRP}-" || true)"
    [ -n "$names" ] && { list+="$names"$'\n'; found=1; }
    if [ "$found" = 1 ]; then
        echo "FATAL: resources with prefix $RRP already exist — refusing to reuse:" >&2
        echo "$list" >&2
        exit 1
    fi
}

cleanup() {
    local rc=0
    timeout "$T_CMD" docker rm -f "$RRP-neo4j-src" "$RRP-neo4j-tgt" "$RRP-neo4j-cons" \
        >> "$LOGS/cleanup.log" 2>&1 || rc=1
    local v
    for v in src-neo4j src-backups src-capture src-uploads src-custom_inputs \
             src-chat src-skills src-apps tgt-neo4j tgt-capture tgt-uploads \
             tgt-custom_inputs tgt-chat tgt-skills tgt-apps cons-graph cons-capture; do
        timeout "$T_CMD" docker volume rm "${RRP}-${v}" >> "$LOGS/cleanup.log" 2>&1 || rc=1
    done
    timeout "$T_CMD" docker network rm "${RRP}-net" >> "$LOGS/cleanup.log" 2>&1 || rc=1
    timeout "$T_CMD" docker rmi "$RRP-backup-sidecar" "localhost/$RRP-backup-sidecar" \
        >> "$LOGS/cleanup.log" 2>&1 || true
    local leftovers
    leftovers="$((docker ps -a --format '{{.Names}}'; docker volume ls --format '{{.Name}}'; docker network ls --format '{{.Name}}'; docker images --format '{{.Repository}}') 2>/dev/null | grep -E "(^|/)${RRP}-" || true)"
    [ -n "$leftovers" ] && { echo "leftover resources: $leftovers" >> "$LOGS/cleanup.log"; rc=1; }
    rm -rf "$WS" 2>> "$LOGS/cleanup.log" || rc=1
    return $rc
}

# ---- argument validation passed; now refuse collisions and claim scratch
collision_check
mkdir -p "$WSBASE" "$OUTBASE"
for p in "$WSBASE" "$OUTBASE"; do
    [ -L "$p" ] && { echo "FATAL: $p is a symlink — refusing" >&2; exit 1; }
done
for p in "$WS" "$OUT"; do
    [ -e "$p" ] && { echo "FATAL: $p already exists — refusing to reuse" >&2; exit 1; }
done
mkdir "$WS" "$OUT" || { echo "FATAL: scratch claim failed" >&2; exit 1; }
[ -L "$WS" ] || [ -L "$OUT" ] && { echo "FATAL: scratch path is a symlink" >&2; exit 1; }
mkdir -p "$EVID" "$RECEIPTS" "$LOGS"
echo "run-id: $RUN_ID  mode: $CHAT_MODE"
echo "workspace: $WS"
echo "evidence:  $OUT"

stage_begin preflight
{
    echo "podman: $(podman --version 2>&1)"
    echo "compose: $(docker compose version 2>&1)"
    echo "node: $(node --version 2>&1)"
    echo "python: $(python3 --version 2>&1)"
    echo "chat_mode: $CHAT_MODE"
    echo "chat_script: ${CHAT_SCRIPT:-<none>}"
    echo "consumers: $CONSUMERS"
    echo "backend_consumer: $BACKEND_CONSUMER"
    echo "chat_consumer: $CHAT_CONSUMER"
    echo "chat_app_node: ${CHAT_APP_NODE:-<unset>}"
    echo "docker_cli: $(command -v docker 2>/dev/null || echo missing)"
    df -h / | tail -1
    free -h | sed -n '2p'
} > "$LOGS/preflight.txt" 2>&1
if [ "$CONSUMERS" = 1 ]; then
    [ -f "$BACKEND_CONSUMER" ] \
        || die "$LOGS/preflight.txt" \
        "BLOCKED: --consumers requires the backend consumer runner at $BACKEND_CONSUMER (owned by backendagent; a run without it is not green)"
    if [ -n "$CHAT_APP_NODE" ]; then
        if [ ! -x "$CHAT_APP_NODE" ]; then
            die "$LOGS/preflight.txt" \
                "BLOCKED: --chat-app-node binary not executable: $CHAT_APP_NODE"
        fi
        CHAT_APP_NODE="$(readlink -f "$CHAT_APP_NODE")"
    fi
fi
if ! timeout "$T_CMD" docker info > "$LOGS/engine-info.txt" 2>&1; then
    die "$LOGS/engine-info.txt" \
        "ENGINE-BLOCKED: docker info failed — container engine/root not ready (actual stdout+stderr in logs/engine-info.txt; no credentials used); not an image-classification failure; no resources touched"
fi
grep -iE 'graphRoot|storage root|registry|operating system|podman|version' \
    "$LOGS/engine-info.txt" >> "$LOGS/preflight.txt" 2>/dev/null || true
docker inspect "$NEO4J_IMAGE" --format '{{.Id}}' > "$WS/neo4j-image-id.txt" 2>/dev/null \
    || die "$LOGS/preflight.txt" \
    "IMAGE-BLOCKED: neo4j image $NEO4J_IMAGE not present (engine verified ready via docker info; pull required)"
NEO4J_IMAGE_ID="$(cat "$WS/neo4j-image-id.txt")"
NEO4J_DIGEST="$(docker images --digests --format '{{.Digest}}' "$NEO4J_IMAGE" | head -1)"
{
    echo "image_id=$NEO4J_IMAGE_ID"
    echo "image_digest=$NEO4J_DIGEST"
} >> "$LOGS/preflight.txt"
stage_ok "$LOGS/preflight.txt" \
    "neo4j_image=$NEO4J_IMAGE" "image_id=$NEO4J_IMAGE_ID" "image_digest=$NEO4J_DIGEST" \
    "run_id=$RUN_ID" "mode=$CHAT_MODE" "workspace=$WS" \
    "docker_cli=$(command -v docker 2>/dev/null || echo missing)" \
    "engine_check=docker info OK (logs/engine-info.txt)"

# ------------------------------------------------------------- provenance
stage_begin provenance
PROV_FILES=(
    "$REPO/ops/backup/backup.sh"
    "$REPO/ops/backup/restore.sh"
    "$REPO/ops/backup/Dockerfile"
    "$ORACLE"
    "$REPO/qa/restore/rehearsal.sh"
    "$BACKEND_CONSUMER"
)
[ -f "$CHAT_CONSUMER" ] && PROV_FILES+=("$CHAT_CONSUMER")
[ "$CHAT_MODE" = full ] && PROV_FILES+=("$CHAT_SCRIPT")
[ -n "$CHAT_APP_NODE" ] && PROV_FILES+=("$CHAT_APP_NODE")
PROV_ARGS=()
for f in "${PROV_FILES[@]}"; do PROV_ARGS+=(--file "$f"); done
if python3 "$ORACLE" provenance --out "$EVID/provenance.json" \
    "${PROV_ARGS[@]}" \
    --meta "transport_mode=$TRANSPORT_MODE" --meta "podman_no_dns=$NO_DNS" \
    --app-repo "$REPO" --chat-repo "$REPO/../cortex-chat" \
    > "$LOGS/provenance.log" 2>&1
then
    :
else
    die "$LOGS/provenance.log" \
        "provenance recording failed (fail closed: git identity, input files and required code-manifest members are mandatory — invalid identity is never silently accepted)"
fi
tail -2 "$LOGS/provenance.log" >> "$LOGS/provenance.log" 2>/dev/null || true
cp "$REPO/ops/backup/backup.sh" "$REPO/ops/backup/restore.sh" "$REPO/ops/backup/Dockerfile" "$EVID/" 2>/dev/null
stage_ok "$LOGS/provenance.log" "provenance=$EVID/provenance.json" \
    "chat_script=${CHAT_SCRIPT:-<none>}"

# ------------------------------------------------------------- selftest
stage_begin oracle-selftest
python3 "$ORACLE" selftest > "$LOGS/selftest.log" 2>&1 \
    || die "$LOGS/selftest.log" "oracle comparison selftest failed"
stage_ok "$LOGS/selftest.log"

# ------------------------------------------------------------- up
stage_begin up
TGT_PORT_ARGS=""
TGT_BOLT_PORT=""
if [ "$CONSUMERS" = 1 ]; then
    if TGT_BOLT_PORT="$(python3 - <<'EOF'
import os, socket
for _ in range(50):
    p = 20000 + (int.from_bytes(os.urandom(2), "big") % 20000)
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
        s.close()
        print(p)
        break
    except OSError:
        continue
EOF
)"; [ -n "$TGT_BOLT_PORT" ]; then
        TGT_PORT_ARGS="-p 127.0.0.1:$TGT_BOLT_PORT:7687"
        echo "target bolt published at 127.0.0.1:$TGT_BOLT_PORT (for --consumers)" \
            >> "$LOGS/up.log"
    else
        die "$LOGS/up.log" "could not find a free loopback port for target bolt"
    fi
fi
if [ "$NO_DNS" = 1 ]; then
    timeout "$T_CMD" docker network create --disable-dns "$RRP-net" \
        >> "$LOGS/up.log" 2>&1 \
        || die "$LOGS/up.log" \
        "TRANSPORT-BLOCKED: network create with --disable-dns failed — this engine does not support the podman no-DNS direct-IP mode (runtime documented: podman 5.4.2 private root without aardvark DNS); refusing to fall back to a DNS network"
else
    timeout "$T_CMD" docker network create "$RRP-net" >> "$LOGS/up.log" 2>&1 \
        || die "$LOGS/up.log" "network create failed"
fi
for v in src-neo4j src-backups src-capture src-uploads src-custom_inputs src-chat \
         src-skills src-apps tgt-neo4j tgt-capture tgt-uploads tgt-custom_inputs \
         tgt-chat tgt-skills tgt-apps; do
    timeout "$T_CMD" docker volume create "$RRP-$v" >> "$LOGS/up.log" 2>&1 \
        || die "$LOGS/up.log" "volume create failed: $RRP-$v"
done
NEO_ENV=(
    -e "NEO4J_AUTH=neo4j/$NEO_PASS"
    -e 'NEO4J_PLUGINS=["apoc"]'
    -e NEO4J_apoc_export_file_enabled=true
    -e NEO4J_server_memory_heap_initial__size=256m
    -e NEO4J_server_memory_heap_max__size=512m
    -e NEO4J_server_memory_pagecache_size=128m
)
timeout "$T_CMD" docker run -d --name "$RRP-neo4j-src" --network "$RRP-net" \
    -v "$RRP-src-neo4j:/data" \
    -v "$RRP-src-backups:/var/lib/neo4j/import" \
    "${NEO_ENV[@]}" \
    "$NEO4J_IMAGE" >> "$LOGS/up.log" 2>&1 \
    || die "$LOGS/up.log" "neo4j-src start failed"
timeout "$T_CMD" docker run -d --name "$RRP-neo4j-tgt" --network "$RRP-net" \
    -v "$RRP-tgt-neo4j:/data" \
    -v "$RRP-tgt-capture:/var/lib/neo4j/import" \
    "${NEO_ENV[@]}" \
    $TGT_PORT_ARGS \
    "$NEO4J_IMAGE" >> "$LOGS/up.log" 2>&1 \
    || die "$LOGS/up.log" "neo4j-tgt start failed"
for c in src tgt; do
    ready=0
    for _ in $(seq 1 60); do
        if timeout "$T_SHORT" docker exec "$RRP-neo4j-$c" cypher-shell \
            -u neo4j -p "$NEO_PASS" --format plain "RETURN 1" \
            >> "$LOGS/up.log" 2>&1; then
            ready=1; break
        fi
        sleep 5
    done
    [ "$ready" = 1 ] || die "$LOGS/up.log" "neo4j-$c not ready in 300s"
done
APOC_VERSION="$(timeout "$T_SHORT" docker exec "$RRP-neo4j-src" cypher-shell \
    -u neo4j -p "$NEO_PASS" --format plain "RETURN apoc.version()" \
    | tail -1 | tr -d '"' | tr -d ' ')"
NEO_VERSION="$(timeout "$T_SHORT" docker exec "$RRP-neo4j-src" cypher-shell \
    -u neo4j -p "$NEO_PASS" --format plain \
    "CALL dbms.components() YIELD versions RETURN versions[0]" \
    | tail -1 | tr -d '"' | tr -d ' ')"
[ -n "$APOC_VERSION" ] || die "$LOGS/up.log" "APOC not available on neo4j-src"
SRC_CID="$(timeout "$T_SHORT" docker inspect "$RRP-neo4j-src" --format '{{.Id}}' 2>> "$LOGS/up.log" || true)"
TGT_CID="$(timeout "$T_SHORT" docker inspect "$RRP-neo4j-tgt" --format '{{.Id}}' 2>> "$LOGS/up.log" || true)"
[ -n "$SRC_CID" ] && [ -n "$TGT_CID" ] \
    || die "$LOGS/up.log" "store container identity inspect failed"
if [ "$NO_DNS" = 1 ]; then
    NET_INSPECT_JSON="$(timeout "$T_SHORT" docker network inspect "$RRP-net" \
        2>> "$LOGS/up.log")" \
        || die "$LOGS/up.log" "TRANSPORT-BLOCKED: run-owned network inspect failed"
    NET_BINDING="$(printf '%s' "$NET_INSPECT_JSON" \
        | timeout "$T_SHORT" python3 "$ORACLE" network-binding \
            --expected-name "$RRP-net" 2>> "$LOGS/up.log")" \
        || die "$LOGS/up.log" \
        "TRANSPORT-BLOCKED: run-owned network binding decode failed (orphan/malformed/ambiguous/unknown-driver-shape inspect payload — raw payload retained in up.log)"
    NET_ID="$(printf '%s\n' "$NET_BINDING" | sed -n 1p)"
    NET_SUBNET="$(printf '%s\n' "$NET_BINDING" | sed -n 2p)"
    NET_SHAPE="$(printf '%s\n' "$NET_BINDING" | sed -n 3p)"
    echo "network_binding: id=$NET_ID subnet=$NET_SUBNET shape=$NET_SHAPE" \
        >> "$LOGS/up.log"
    SRC_IP="$(timeout "$T_SHORT" docker inspect "$RRP-neo4j-src" \
        --format '{{json .NetworkSettings.Networks}}' 2>> "$LOGS/up.log" \
        | python3 "$ORACLE" transport-endpoint \
            --expected-network-id "$NET_ID" --subnet "$NET_SUBNET")" \
        || die "$LOGS/up.log" \
        "TRANSPORT-BLOCKED: source container IP not resolvable on the run-owned network (no-DNS direct-IP mode)"
    TGT_IP="$(timeout "$T_SHORT" docker inspect "$RRP-neo4j-tgt" \
        --format '{{json .NetworkSettings.Networks}}' 2>> "$LOGS/up.log" \
        | python3 "$ORACLE" transport-endpoint \
            --expected-network-id "$NET_ID" --subnet "$NET_SUBNET")" \
        || die "$LOGS/up.log" \
        "TRANSPORT-BLOCKED: target container IP not resolvable on the run-owned network (no-DNS direct-IP mode)"
    SOURCE_ADDRESS="bolt://$SRC_IP:7687"
    TARGET_ADDRESS="bolt://$TGT_IP:7687"
    : # TRANSPORT_MODE set at arg parse
    python3 - "$EVID/transport.json" "$RRP-net" "$NET_ID" "$NET_SUBNET" \
        "$NET_SHAPE" \
        "$SRC_IP" "$TGT_IP" "$SRC_CID" "$TGT_CID" <<'EOF'
import json, sys
json.dump({
    "mode": "direct-ip",
    "network": sys.argv[2], "network_id": sys.argv[3], "subnet": sys.argv[4],
    "network_shape": sys.argv[5],
    "source_ip": sys.argv[6], "target_ip": sys.argv[7],
    "source_container_id": sys.argv[8], "target_container_id": sys.argv[9],
    "source_address": f"bolt://{sys.argv[6]}:7687",
    "target_address": f"bolt://{sys.argv[7]}:7687",
    "identity_note": "container id is the stable identity; the internal IP is ephemeral and refreshed before every claimed operation",
    "note": "podman no-DNS direct-IP adapter; name-based DNS discovery unused and untested in this mode",
}, open(sys.argv[1], "w"), indent=1)
print("transport: direct-ip", sys.argv[6], sys.argv[7])
EOF
    { echo "transport_mode=direct-ip"; echo "source_address=$SOURCE_ADDRESS"
      echo "target_address=$TARGET_ADDRESS"; } >> "$LOGS/up.log" \
        || die "$LOGS/up.log" "transport record write failed"
else
    SOURCE_ADDRESS="neo4j://$RRP-neo4j-src:7687"
    TARGET_ADDRESS="neo4j://$RRP-neo4j-tgt:7687"
    python3 - "$EVID/transport.json" "$RRP-net" "$SRC_CID" "$TGT_CID" \
        "$SOURCE_ADDRESS" "$TARGET_ADDRESS" <<'EOF'
import json, sys
json.dump({
    "mode": "dns",
    "network": sys.argv[2],
    "source_container_id": sys.argv[3], "target_container_id": sys.argv[4],
    "source_address": sys.argv[5], "target_address": sys.argv[6],
    "identity_note": "container id is the stable identity; name-based DNS addresses (container names) resolved by the engine",
}, open(sys.argv[1], "w"), indent=1)
EOF
    { echo "transport_mode=dns"; echo "source_address=$SOURCE_ADDRESS"
      echo "target_address=$TARGET_ADDRESS"; } >> "$LOGS/up.log"
fi
if [ "$CONSUMERS" = 1 ]; then
    DETECTED_PORT="$(timeout "$T_SHORT" docker port "$RRP-neo4j-tgt" 7687 2>> "$LOGS/up.log" \
        | grep -oE '127\.0\.0\.1:[0-9]+' | head -1 | cut -d: -f2)"
    [ -n "$DETECTED_PORT" ] \
        || die "$LOGS/up.log" "target bolt port not detectable via docker port"
    [ "$DETECTED_PORT" = "$TGT_BOLT_PORT" ] \
        || die "$LOGS/up.log" "detected bolt port $DETECTED_PORT != reserved $TGT_BOLT_PORT"
    TGT_BOLT_PORT="$DETECTED_PORT"
fi
stage_ok "$LOGS/up.log" \
    "apoc_version=$APOC_VERSION" "neo4j_version=$NEO_VERSION" \
    "network=$RRP-net" "containers=$RRP-neo4j-src,$RRP-neo4j-tgt" \
    "transport_mode=$TRANSPORT_MODE" \
    "source_address=$SOURCE_ADDRESS" "target_address=$TARGET_ADDRESS" \
    "tgt_bolt_port=${TGT_BOLT_PORT:-<not-published>}"

# ------------------------------------------------------------- seed
stage_begin seed
python3 "$ORACLE" gen-fixture --cypher-out "$WS/fixture.cypher" \
    --files-dir "$WS/fixture-files" > "$LOGS/seed.log" 2>&1 \
    || die "$LOGS/seed.log" "fixture generation failed"
timeout "$T_CMD" docker exec -i "$RRP-neo4j-src" cypher-shell -u neo4j -p "$NEO_PASS" \
    --format plain < "$WS/fixture.cypher" >> "$LOGS/seed.log" 2>&1 \
    || die "$LOGS/seed.log" "graph fixture seeding failed"
timeout "$T_CMD" docker run --rm --name "$RRP-fs-seed" \
    -v "$RRP-src-uploads:/data/uploads" \
    -v "$RRP-src-custom_inputs:/data/custom_inputs" \
    -v "$RRP-src-skills:/data/skills" \
    -v "$RRP-src-apps:/data/apps" \
    -v "$WS/fixture-files:/seed:ro" \
    "$ALPINE_IMAGE" sh -c \
    'cp -a /seed/uploads/. /data/uploads/ && cp -a /seed/custom_inputs/. /data/custom_inputs/ && cp -a /seed/skills/. /data/skills/ && cp -a /seed/apps/. /data/apps/' \
    >> "$LOGS/seed.log" 2>&1 \
    || die "$LOGS/seed.log" "file fixture seeding failed"
CHAT_STATUS="smoke-no-chat"
CHAT_FIXTURE_DIR="$WS/chat-fixture"
if [ "$CHAT_MODE" = full ]; then
    if timeout 120 node "$CHAT_SCRIPT" seed "$CHAT_FIXTURE_DIR" \
        >> "$LOGS/seed-chat.log" 2>&1; then
        [ -f "$CHAT_FIXTURE_DIR/$CHAT_DB_REL" ] \
            || die "$LOGS/seed-chat.log" \
            "companion seed layout unexpected: $CHAT_DB_REL missing at chat volume root"
        CHAT_STATUS="seeded"
        timeout "$T_CMD" docker run --rm --name "$RRP-fs-seed-chat" \
            -v "$RRP-src-chat:/data/chat" \
            -v "$CHAT_FIXTURE_DIR:/seed:ro" \
            "$ALPINE_IMAGE" sh -c 'cp -a /seed/. /data/chat/' \
            >> "$LOGS/seed-chat.log" 2>&1 \
            || die "$LOGS/seed-chat.log" "chat fixture copy into volume failed"
    else
        die "$LOGS/seed-chat.log" "companion chat fixture seed failed"
    fi
fi
stage_ok "$LOGS/seed.log" "chat_fixture=$CHAT_STATUS" \
    "fixture_cypher=$WS/fixture.cypher" "fixture_files=$WS/fixture-files"

# ------------------------------------------------- cross-repo coherence preflight
if [ "$CHAT_MODE" = full ]; then
stage_begin cross-repo-coherence
: > "$LOGS/coherence.log"
[ -f "$CHAT_FIXTURE_DIR/$CHAT_DB_REL" ] \
    || die "$LOGS/coherence.log" \
    "CROSS-REPO-INCOHERENCE preflight blocked: seeded chat pack lacks $CHAT_DB_REL"
if python3 "$ORACLE" chat-coherence \
    --chat-db "$CHAT_FIXTURE_DIR/$CHAT_DB_REL" \
    --contract "$WS/fixture-contract.json" \
    >> "$LOGS/coherence.log" 2>&1; then
    :
else
    die "$LOGS/coherence.log" \
    "CROSS-REPO-INCOHERENCE: seeded chat pack and declared main fixture source disagree (per-field reasons in coherence.log: declared id/chunk_id/nested metadata.filename/snapshot containment/typed finite score) — caught before backup/consumer build; classify from the receipts before any repair (e.g. run g was a GATE-SHAPE defect — the coherence oracle assumed a flat source shape the actual public Source type does not have — not a chat-pack problem); NOT integration evidence; do not alter data to fit the gate"
fi
stage_ok "$LOGS/coherence.log" \
    "chat_db=$CHAT_FIXTURE_DIR/$CHAT_DB_REL (immutable read)" \
    "contract=$WS/fixture-contract.json" \
    "result=seeded chat citation matches declared main fixture source"
fi

# ------------------------------------------------------------- schema
stage_begin schema
cat > "$WS/schema.cypher" <<'EOF'
CREATE CONSTRAINT rehearsal_doc_id IF NOT EXISTS
FOR (d:Document) REQUIRE d.id IS UNIQUE;
CREATE FULLTEXT INDEX chunk_content IF NOT EXISTS
FOR (n:Chunk) ON EACH [n.content];
CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
FOR (c:Chunk) ON (c.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 8, `vector.similarity_function`: 'cosine'}};
EOF
timeout "$T_CMD" docker exec -i "$RRP-neo4j-src" cypher-shell -u neo4j -p "$NEO_PASS" \
    --format plain < "$WS/schema.cypher" > "$LOGS/schema.log" 2>&1 \
    || die "$LOGS/schema.log" "source schema creation failed"
wait_index() {
    local c="$1" name="$2" st
    for _ in $(seq 1 30); do
        st="$(timeout "$T_SHORT" docker exec "$RRP-neo4j-$c" cypher-shell \
            -u neo4j -p "$NEO_PASS" --format plain \
            "SHOW INDEXES YIELD name, state WHERE name = '$name' RETURN state" \
            | tail -1 | tr -d '"')"
        [ "$st" = "ONLINE" ] && return 0
        sleep 2
    done
    return 1
}
wait_index src chunk_content || die "$LOGS/schema.log" "source fulltext index never reached ON"
wait_index src chunk_embedding || die "$LOGS/schema.log" "source vector index never reached ON"
FT_SRC_COUNT="$(timeout "$T_SHORT" docker exec "$RRP-neo4j-src" cypher-shell \
    -u neo4j -p "$NEO_PASS" --format plain \
    "CALL db.index.fulltext.queryNodes('chunk_content', 'rehearsal') YIELD node RETURN count(node)" \
    | tail -1 | tr -d ' ')"
case "$FT_SRC_COUNT" in ''|*[!0-9]*) die "$LOGS/schema.log" "fulltext count unparseable: $FT_SRC_COUNT" ;; esac
[ "$FT_SRC_COUNT" -gt 0 ] || die "$LOGS/schema.log" "source fulltext query returned no rows"
printf '%s' "$FT_SRC_COUNT" > "$WS/fulltext-src-count"
stage_ok "$LOGS/schema.log" \
    "source_schema=constraint+fulltext+vector(8d,cosine)" \
    "fulltext_healthy_count=$FT_SRC_COUNT"

# ------------------------------------------------------------- freeze
stage_begin freeze
python3 "$ORACLE" capture-graph --container "$RRP-neo4j-src" \
    --password "$NEO_PASS" --out "$EVID/gate-graph.json" \
    > "$LOGS/freeze.log" 2>&1 \
    || die "$LOGS/freeze.log" "graph freeze capture failed"
mkdir -p "$WS/frozen-files"
for root in uploads custom_inputs chat skills apps; do
    timeout "$T_CMD" docker run --rm --name "$RRP-fs-copyout-$root" \
        -v "$RRP-src-$root:/vol:ro" \
        -v "$WS/frozen-files:/out" \
        "$ALPINE_IMAGE" sh -c "mkdir -p /out/$root && cp -r /vol/. /out/$root/" \
        >> "$LOGS/freeze.log" 2>&1 \
        || die "$LOGS/freeze.log" "volume copy-out failed: $root"
    python3 "$ORACLE" capture-files --dir "$WS/frozen-files/$root" \
        --out "$EVID/gate-files-$root.json" >> "$LOGS/freeze.log" 2>&1 \
        || die "$LOGS/freeze.log" "file manifest failed: $root"
done
python3 "$ORACLE" capture-sqlite \
    --path "$WS/frozen-files/apps/rehearsal-app/storage.sqlite" \
    --out "$EVID/gate-sqlite-appstorage.json" >> "$LOGS/freeze.log" 2>&1 \
    || die "$LOGS/freeze.log" "app sqlite capture failed"
CHAT_VERIFY_STATUS="smoke-no-chat"
if [ "$CHAT_MODE" = full ]; then
    [ -f "$WS/frozen-files/chat/$CHAT_DB_REL" ] \
        || die "$LOGS/freeze.log" "frozen chat volume lacks $CHAT_DB_REL"
    CHAT_DB_SHA_FROZEN="$(sha256sum "$WS/frozen-files/chat/$CHAT_DB_REL" | cut -d' ' -f1)"
    if ! timeout 120 node "$CHAT_SCRIPT" verify "$WS/frozen-files/chat" \
        > "$LOGS/chat-verify-frozen.log" 2>&1; then
        die "$LOGS/chat-verify-frozen.log" "companion verify rejected the frozen chat fixture"
    fi
    CHAT_DB_SHA_AFTER="$(sha256sum "$WS/frozen-files/chat/$CHAT_DB_REL" | cut -d' ' -f1)"
    [ "$CHAT_DB_SHA_FROZEN" = "$CHAT_DB_SHA_AFTER" ] \
        || die "$LOGS/chat-verify-frozen.log" "verify mutated the chat DB (sha changed)"
    python3 "$ORACLE" capture-sqlite \
        --path "$WS/frozen-files/chat/$CHAT_DB_REL" \
        --out "$EVID/gate-sqlite-chat.json" >> "$LOGS/freeze.log" 2>&1 \
        || die "$LOGS/freeze.log" "chat sqlite capture failed"
    [ ! -f "$WS/frozen-files/chat/$CHAT_DB_REL-wal" ] \
        || die "$LOGS/freeze.log" "sqlite capture polluted the frozen chat set (-wal appeared)"
    CHAT_VERIFY_STATUS="ok"
fi
GATE_FILES=(
    "$EVID/gate-graph.json" "$EVID/gate-files-uploads.json"
    "$EVID/gate-files-custom_inputs.json" "$EVID/gate-files-chat.json"
    "$EVID/gate-files-skills.json" "$EVID/gate-files-apps.json"
    "$EVID/gate-sqlite-appstorage.json"
)
[ "$CHAT_MODE" = full ] && GATE_FILES+=("$EVID/gate-sqlite-chat.json")
python3 "$ORACLE" hashes --out "$EVID/gate-hashes.json" \
    --paths "${GATE_FILES[@]}" >> "$LOGS/freeze.log" 2>&1 \
    || die "$LOGS/freeze.log" "gate hashing failed"
stage_ok "$LOGS/freeze.log" \
    "gate_graph=$EVID/gate-graph.json" "gate_hashes=$EVID/gate-hashes.json" \
    "chat_verify=$CHAT_VERIFY_STATUS" "chat_db_sha_frozen=${CHAT_DB_SHA_FROZEN:-<none>}"

# ------------------------------------------------------------- build-sidecar
stage_begin build-sidecar
timeout "$T_BUILD" docker build \
    --build-arg "NEO4J_IMAGE=$NEO4J_IMAGE" \
    -t "$RRP-backup-sidecar" "$REPO/ops/backup" \
    > "$LOGS/build-sidecar.log" 2>&1 \
    || die "$LOGS/build-sidecar.log" "sidecar image build failed"
SIDECAR_ID="$(docker images --format '{{.ID}}' "$RRP-backup-sidecar" | head -1)"
stage_ok "$LOGS/build-sidecar.log" "sidecar_image=$RRP-backup-sidecar" \
    "image_id=$SIDECAR_ID" "build_arg_neo4j_image=$NEO4J_IMAGE"

# ------------------------------------------------------------- snapshot
stage_begin snapshot
if ! refresh_address source; then
    die "$LOGS/snapshot.log" \
        "TRANSPORT-BLOCKED: source endpoint refresh failed before backup (actual internal mapping required at the claimed operation)"
fi
SOURCE_ADDRESS="$REFRESHED_ADDRESS"
echo "snapshot uses refreshed source endpoint (container_id=$REFRESHED_CID): $SOURCE_ADDRESS" \
    >> "$LOGS/snapshot.log"
timeout "$T_CMD" docker run --rm --name "$RRP-backup-run" --network "$RRP-net" \
    -v "$RRP-src-backups:/backups" \
    -v "$RRP-src-uploads:/data/uploads:ro" \
    -v "$RRP-src-custom_inputs:/data/custom_inputs:ro" \
    -v "$RRP-src-chat:/data/chat:ro" \
    -v "$RRP-src-skills:/data/skills:ro" \
    -v "$RRP-src-apps:/data/apps:ro" \
    -e "NEO4J_ADDRESS=$SOURCE_ADDRESS" \
    -e "NEO4J_PASSWORD=$NEO_PASS" \
    --entrypoint /backup.sh \
    "$RRP-backup-sidecar" > "$LOGS/snapshot.log" 2>&1 \
    || die "$LOGS/snapshot.log" "actual backup.sh failed"
BK_TS="$(grep -oE '/backups/[0-9]{8}-[0-9]{6}' "$LOGS/snapshot.log" | tail -1 | cut -d/ -f3)"
[ -n "$BK_TS" ] || die "$LOGS/snapshot.log" "could not determine backup timestamp"
mkdir -p "$WS/frozen-archive/$BK_TS"
timeout "$T_CMD" docker run --rm --name "$RRP-fs-copyout-bk" \
    -v "$RRP-src-backups:/backups:ro" \
    -v "$WS:/out" \
    "$ALPINE_IMAGE" sh -c "cp -r /backups/$BK_TS/. /out/frozen-archive/$BK_TS/" \
    >> "$LOGS/snapshot.log" 2>&1 \
    || die "$LOGS/snapshot.log" "archive copy-out failed"
( cd "$WS/frozen-archive/$BK_TS" && sha256sum -c --quiet SHA256SUMS ) \
    >> "$LOGS/snapshot.log" 2>&1 \
    || die "$LOGS/snapshot.log" "frozen archive fails its own SHA256SUMS"
[ -f "$WS/frozen-archive/$BK_TS/.complete" ] \
    || die "$LOGS/snapshot.log" "backup dir lacks .complete marker"
grep -q '^:commit' <(gunzip -c "$WS/frozen-archive/$BK_TS/graph.cypher.gz") \
    || die "$LOGS/snapshot.log" "graph.cypher.gz lacks :commit blocks"
python3 "$ORACLE" hashes --out "$EVID/candidate-archive-hashes.json" \
    --paths "$WS/frozen-archive/$BK_TS"/* >> "$LOGS/snapshot.log" 2>&1 \
    || die "$LOGS/snapshot.log" "archive hashing failed"
stage_ok "$LOGS/snapshot.log" \
    "backup_ts=$BK_TS" "script=ops/backup/backup.sh (unchanged)" \
    "frozen_archive=$WS/frozen-archive/$BK_TS" \
    "meta_json=$(cat "$WS/frozen-archive/$BK_TS/meta.json")" \
    "transport_mode=$TRANSPORT_MODE" "source_address=$SOURCE_ADDRESS" \
    "source_container_id=$(docker inspect "$RRP-neo4j-src" --format '{{.Id}}' 2>/dev/null || echo unavailable)"

# ------------------------------------------------------------- post-t writes
stage_begin post-t
cat > "$WS/postt.cypher" <<'EOF'
CREATE (d:Document {id: 'doc-crr-999', filename: 'rehearsal-post.md',
  file_type: 'text/markdown', file_size: 42,
  file_path: 'uploads/doc-crr-999_rehearsal-post.md',
  upload_date: datetime('2026-10-01T12:00:00Z'), chunk_count: 0,
  processing_status: 'completed', error_message: '',
  progress_current: 0, progress_total: 0, progress_message: '',
  source: 'upload', git_connection_id: '', git_path: '', git_blob_sha: '',
  git_commit_sha: '', git_sync_status: ''});
MATCH (u:LLMUsageDay {date: '2026-10-01'})
SET u.completions = u.completions + 3, u.completions_query = u.completions_query + 2;
EOF
timeout "$T_CMD" docker exec -i "$RRP-neo4j-src" cypher-shell -u neo4j -p "$NEO_PASS" \
    --format plain < "$WS/postt.cypher" > "$LOGS/postt.log" 2>&1 \
    || die "$LOGS/postt.log" "post-T graph writes failed"
printf 'post-snapshot blob: must NOT appear after restore.\n' \
    > "$WS/postt-files-doc-crr-999.md"
timeout "$T_CMD" docker run --rm --name "$RRP-fs-postt" \
    -v "$RRP-src-uploads:/data/uploads" \
    -v "$WS:/seed:ro" \
    "$ALPINE_IMAGE" sh -c 'cp -a /seed/postt-files-doc-crr-999.md /data/uploads/doc-crr-999_rehearsal-post.md' \
    >> "$LOGS/postt.log" 2>&1 \
    || die "$LOGS/postt.log" "post-T blob write failed"
CHAT_POSTT_STATUS="smoke-no-chat"
CANARY_AVATAR="fx-canary-0001.png"
if [ "$CHAT_MODE" = full ]; then
    cp -a "$CHAT_FIXTURE_DIR" "$WS/chat-postt"
    if ! timeout 120 node "$CHAT_SCRIPT" mutate "$WS/chat-postt" \
        >> "$LOGS/postt-chat.log" 2>&1; then
        die "$LOGS/postt-chat.log" "companion chat mutate failed (cannot proceed green)"
    fi
    CHAT_DB_SHA_PREMUT="$(sha256sum "$WS/chat-fixture/$CHAT_DB_REL" | cut -d' ' -f1)"
    CHAT_DB_SHA_POSTMUT="$(sha256sum "$WS/chat-postt/$CHAT_DB_REL" | cut -d' ' -f1)"
    [ "$CHAT_DB_SHA_PREMUT" != "$CHAT_DB_SHA_POSTMUT" ] \
        || die "$LOGS/postt-chat.log" "mutate made no DB change — canary not written"
    if timeout 120 node "$CHAT_SCRIPT" verify "$WS/chat-postt" \
        >> "$LOGS/postt-chat-verify.log" 2>&1; then
        die "$LOGS/postt-chat-verify.log" \
        "companion verify ACCEPTED the mutated (canary) fixture — verify is not a real gate"
    fi
    grep -q '"ok":false' "$LOGS/postt-chat-verify.log" \
        || die "$LOGS/postt-chat-verify.log" "mutated verify output lacked ok:false"
    timeout "$T_CMD" docker run --rm --name "$RRP-fs-postt-chat" \
        -v "$RRP-src-chat:/data/chat" \
        -v "$WS/chat-postt:/seed:ro" \
        "$ALPINE_IMAGE" sh -c 'cp -a /seed/. /data/chat/' \
        >> "$LOGS/postt-chat.log" 2>&1 \
        || die "$LOGS/postt-chat.log" "post-T chat copy into source volume failed"
    CHAT_POSTT_STATUS="mutated+canary-verify-rejected"
fi
cat > "$WS/absent-graph.json" <<'EOF'
[{"labels": ["Document"], "key": "doc-crr-999"}]
EOF
if [ "$CHAT_MODE" = full ]; then
    cat > "$WS/absent-files.json" <<EOF
{"uploads": ["doc-crr-999_rehearsal-post.md"], "chat": ["$CANARY_AVATAR"]}
EOF
else
    cat > "$WS/absent-files.json" <<'EOF'
{"uploads": ["doc-crr-999_rehearsal-post.md"]}
EOF
fi
stage_ok "$LOGS/postt.log" \
    "postt_graph_keys=Document:doc-crr-999 + LLMUsageDay bump" \
    "postt_file=uploads/doc-crr-999_rehearsal-post.md" \
    "chat_postt=$CHAT_POSTT_STATUS"

# ------------------------------------------------------------- target-schema
stage_begin target-schema
timeout "$T_CMD" docker exec -i "$RRP-neo4j-tgt" cypher-shell -u neo4j -p "$NEO_PASS" \
    --format plain < "$WS/schema.cypher" > "$LOGS/target-schema.log" 2>&1 \
    || die "$LOGS/target-schema.log" "target schema creation failed"
wait_index tgt chunk_content || die "$LOGS/target-schema.log" "target fulltext index never reached ON"
wait_index tgt chunk_embedding || die "$LOGS/target-schema.log" "target vector index never reached ON"
stage_ok "$LOGS/target-schema.log" \
    "target_schema=same constraint+fulltext; vector pre-created for deliberate-retention path"

# ------------------------------------------------------------- healthy restore
stage_begin restore-healthy
if ! refresh_address target; then
    die "$LOGS/restore-healthy.log" \
        "TRANSPORT-BLOCKED: target endpoint refresh failed before the destructive healthy restore (actual internal mapping required; stale IP could address the consumer-copy store)"
fi
TARGET_ADDRESS="$REFRESHED_ADDRESS"
echo "healthy restore targets refreshed endpoint (container_id=$REFRESHED_CID): $TARGET_ADDRESS" \
    >> "$LOGS/restore-healthy.log"
mkdir -p "$WS/staging/healthy"
cp -a "$WS/frozen-archive/$BK_TS" "$WS/staging/healthy/$BK_TS"
( cd "$WS/staging/healthy/$BK_TS" && sha256sum -c --quiet SHA256SUMS ) \
    >> "$LOGS/restore-healthy.log" 2>&1 \
    || die "$LOGS/restore-healthy.log" "staging copy diverged from frozen archive"
timeout "$T_CMD" docker run --rm --name "$RRP-restore-run" --network "$RRP-net" \
    -v "$WS/staging/healthy:/backups:ro" \
    -v "$RRP-tgt-uploads:/data/uploads:ro" \
    -v "$RRP-tgt-custom_inputs:/data/custom_inputs:ro" \
    -v "$RRP-tgt-chat:/data/chat:ro" \
    -v "$RRP-tgt-skills:/data/skills:ro" \
    -v "$RRP-tgt-apps:/data/apps:ro" \
    -e RESTORE_WIPE=yes \
    -e "NEO4J_ADDRESS=$TARGET_ADDRESS" \
    -e "NEO4J_PASSWORD=$NEO_PASS" \
    --entrypoint /restore.sh \
    "$RRP-backup-sidecar" "$BK_TS" > "$LOGS/restore-healthy.log" 2>&1 \
    || die "$LOGS/restore-healthy.log" "actual restore.sh failed (graph replay)"
timeout "$T_CMD" docker run --rm --name "$RRP-untar-run" \
    -v "$RRP-tgt-uploads:/data/uploads" \
    -v "$RRP-tgt-custom_inputs:/data/custom_inputs" \
    -v "$RRP-tgt-chat:/data/chat" \
    -v "$RRP-tgt-skills:/data/skills" \
    -v "$RRP-tgt-apps:/data/apps" \
    -v "$WS/staging/healthy:/backups:ro" \
    "$ALPINE_IMAGE" tar -xzf "/backups/$BK_TS/files.tar.gz" -C / \
    >> "$LOGS/restore-healthy.log" 2>&1 \
    || die "$LOGS/restore-healthy.log" "runbook step 4 (file volumes untar) failed"
stage_ok "$LOGS/restore-healthy.log" \
    "restore_command=docker run --entrypoint /restore.sh -e RESTORE_WIPE=yes ... $RRP-backup-sidecar $BK_TS" \
    "ts=$BK_TS" "script=ops/backup/restore.sh (unchanged)" \
    "transport_mode=$TRANSPORT_MODE" "target_address=$TARGET_ADDRESS" \
    "target_container_id=$REFRESHED_CID"

# ------------------------------------------------------------- oracle healthy
stage_begin oracle-healthy
python3 "$ORACLE" capture-graph --container "$RRP-neo4j-tgt" \
    --password "$NEO_PASS" --out "$EVID/actual-graph-healthy.json" \
    > "$LOGS/oracle-healthy.log" 2>&1 \
    || die "$LOGS/oracle-healthy.log" "target graph capture failed"
mkdir -p "$WS/restored-files"
for root in uploads custom_inputs chat skills apps; do
    timeout "$T_CMD" docker run --rm --name "$RRP-fs-restored-$root" \
        -v "$RRP-tgt-$root:/vol:ro" \
        -v "$WS/restored-files:/out" \
        "$ALPINE_IMAGE" sh -c "mkdir -p /out/$root && cp -r /vol/. /out/$root/" \
        >> "$LOGS/oracle-healthy.log" 2>&1 \
        || die "$LOGS/oracle-healthy.log" "target copy-out failed: $root"
    python3 "$ORACLE" capture-files --dir "$WS/restored-files/$root" \
        --out "$EVID/actual-files-$root.json" >> "$LOGS/oracle-healthy.log" 2>&1 \
        || die "$LOGS/oracle-healthy.log" "target manifest failed: $root"
done
python3 "$ORACLE" capture-sqlite \
    --path "$WS/restored-files/apps/rehearsal-app/storage.sqlite" \
    --out "$EVID/actual-sqlite-appstorage.json" >> "$LOGS/oracle-healthy.log" 2>&1 \
    || die "$LOGS/oracle-healthy.log" "target app sqlite capture failed"
FT_TGT_COUNT="$(timeout "$T_SHORT" docker exec "$RRP-neo4j-tgt" cypher-shell \
    -u neo4j -p "$NEO_PASS" --format plain \
    "CALL db.index.fulltext.queryNodes('chunk_content', 'rehearsal') YIELD node RETURN count(node)" \
    | tail -1 | tr -d ' ')"
[ "$FT_TGT_COUNT" = "$(cat "$WS/fulltext-src-count")" ] \
    || die "$LOGS/oracle-healthy.log" \
    "restored fulltext index not usable/equal: source=$(cat "$WS/fulltext-src-count") target=$FT_TGT_COUNT"
CHAT_RESTORED_VERIFY="smoke-no-chat"
SQLITE_NAMES=(appstorage)
SQLITE_EXP=("$EVID/gate-sqlite-appstorage.json")
SQLITE_ACT=("$EVID/actual-sqlite-appstorage.json")
if [ "$CHAT_MODE" = full ]; then
    [ -f "$WS/restored-files/chat/$CHAT_DB_REL" ] \
        || die "$LOGS/oracle-healthy.log" "restored chat volume lacks $CHAT_DB_REL"
    CHAT_DB_SHA_PREVERIFY="$(sha256sum "$WS/restored-files/chat/$CHAT_DB_REL" | cut -d' ' -f1)"
    if ! timeout 120 node "$CHAT_SCRIPT" verify "$WS/restored-files/chat" \
        > "$LOGS/chat-verify-restored.log" 2>&1; then
        die "$LOGS/chat-verify-restored.log" "companion verify rejected the restored chat volume"
    fi
    CHAT_DB_SHA_POSTVERIFY="$(sha256sum "$WS/restored-files/chat/$CHAT_DB_REL" | cut -d' ' -f1)"
    [ "$CHAT_DB_SHA_PREVERIFY" = "$CHAT_DB_SHA_POSTVERIFY" ] \
        || die "$LOGS/chat-verify-restored.log" "verify mutated the restored chat DB (sha changed)"
    python3 "$ORACLE" capture-sqlite --path "$WS/restored-files/chat/$CHAT_DB_REL" \
        --out "$EVID/actual-sqlite-chat.json" >> "$LOGS/oracle-healthy.log" 2>&1 \
        || die "$LOGS/oracle-healthy.log" "target chat sqlite capture failed"
    SQLITE_NAMES+=(chat)
    SQLITE_EXP+=("$EVID/gate-sqlite-chat.json")
    SQLITE_ACT+=("$EVID/actual-sqlite-chat.json")
    CHAT_RESTORED_VERIFY="ok"
fi
python3 "$ORACLE" compare \
    --expected-graph "$EVID/gate-graph.json" \
    --actual-graph "$EVID/actual-graph-healthy.json" \
    --expected-roots uploads custom_inputs chat skills apps \
    --expected-files "$EVID/gate-files-uploads.json" "$EVID/gate-files-custom_inputs.json" \
        "$EVID/gate-files-chat.json" "$EVID/gate-files-skills.json" "$EVID/gate-files-apps.json" \
    --actual-roots uploads custom_inputs chat skills apps \
    --actual-files "$EVID/actual-files-uploads.json" "$EVID/actual-files-custom_inputs.json" \
        "$EVID/actual-files-chat.json" "$EVID/actual-files-skills.json" "$EVID/actual-files-apps.json" \
    --sqlite-names "${SQLITE_NAMES[@]}" \
    --expected-sqlite "${SQLITE_EXP[@]}" \
    --actual-sqlite "${SQLITE_ACT[@]}" \
    --absent-graph "$WS/absent-graph.json" \
    --absent-files "$WS/absent-files.json" \
    --verdict "$EVID/verdict-healthy.json" --expect pass \
    >> "$LOGS/oracle-healthy.log" 2>&1 \
    || die "$LOGS/oracle-healthy.log" "ORACLE REJECTED the healthy full restore (see verdict)"
stage_ok "$LOGS/oracle-healthy.log" \
    "verdict=$EVID/verdict-healthy.json" "chat_verify=$CHAT_RESTORED_VERIFY" \
    "fulltext_restored_count=$FT_TGT_COUNT" "result=PASS"

# ------------------------------------------------------------- consumer journeys
if [ "$CONSUMERS" = 1 ]; then
stage_begin consumer-journeys
: > "$LOGS/consumer.log"
[ -f "$BACKEND_CONSUMER" ] \
    || die "$LOGS/consumer.log" \
    "BLOCKED: backend consumer runner missing at $BACKEND_CONSUMER (backendagent owns it; run is not green)"
mkdir -p "$WS/consumer-files" "$WS/consumer-results"
for root in uploads custom_inputs chat skills apps; do
    timeout "$T_CMD" docker run --rm --name "$RRP-fs-cons-$root" \
        -v "$RRP-tgt-$root:/vol:ro" \
        -v "$WS/consumer-files:/out" \
        "$ALPINE_IMAGE" sh -c "mkdir -p /out/$root && cp -r /vol/. /out/$root/" \
        >> "$LOGS/consumer.log" 2>&1 \
        || die "$LOGS/consumer.log" "consumer copy of restored root failed: $root"
    python3 "$ORACLE" capture-files --dir "$WS/consumer-files/$root" \
        --out "$EVID/consumer-copy-files-$root-before.json" \
        >> "$LOGS/consumer.log" 2>&1 \
        || die "$LOGS/consumer.log" "consumer-copy file hash failed: $root"
done
CONSUMER_PY="${CORTEX_CONSUMER_PYTHON:-}"
if [ -z "$CONSUMER_PY" ] && [ -x /tmp/opencode/qa-venv/bin/python ]; then
    CONSUMER_PY="/tmp/opencode/qa-venv/bin/python"
fi
[ -n "$CONSUMER_PY" ] || CONSUMER_PY="python3"
if ! "$CONSUMER_PY" - "$WS/consumer-results/interpreter.json" <<'EOF' >> "$LOGS/consumer.log" 2>&1
import json, platform, sys
info = {"python": sys.executable, "version": platform.python_version()}
deps = {}
for m in ("fastapi", "uvicorn", "neo4j", "httpx"):
    try:
        mod = __import__(m)
        deps[m] = getattr(mod, "__version__", "unknown")
    except Exception:
        deps[m] = None
info["deps"] = deps
info["deps_complete"] = all(deps.values())
json.dump(info, open(sys.argv[1], "w"), indent=1)
print("consumer interpreter:", json.dumps(info))
EOF
then
    die "$LOGS/consumer.log" "consumer interpreter identity record failed"
fi
if [ "$(python3 -c "import json;print(json.load(open('$WS/consumer-results/interpreter.json'))['deps_complete'])")" != "True" ]; then
    echo "NOTE: consumer interpreter lacks backend deps (environment-block, not a product fail; the runner's own preflight must also fail closed)" \
        >> "$LOGS/consumer.log"
fi
if [ -n "$CHAT_APP_NODE" ]; then
    if ! "$CHAT_APP_NODE" --version > "$WS/consumer-results/chat-app-node-version.txt" 2>&1; then
        die "$LOGS/consumer.log" "chat app node binary --version failed: $CHAT_APP_NODE"
    fi
    echo "chat app node: $CHAT_APP_NODE ($(cat "$WS/consumer-results/chat-app-node-version.txt"))" \
        >> "$LOGS/consumer.log"
else
    echo "chat app node: <unset>" \
        >> "$LOGS/consumer.log"
fi
timeout "$T_CMD" docker volume create "$RRP-cons-graph" >> "$LOGS/consumer.log" 2>&1 \
    || die "$LOGS/consumer.log" "consumer graph volume create failed"
timeout "$T_CMD" docker volume create "$RRP-cons-capture" >> "$LOGS/consumer.log" 2>&1 \
    || die "$LOGS/consumer.log" "consumer capture volume create failed"
timeout "$T_CMD" docker stop "$RRP-neo4j-tgt" >> "$LOGS/consumer.log" 2>&1 \
    || die "$LOGS/consumer.log" "could not stop target for offline graph copy"
timeout "$T_CMD" docker run --rm --name "$RRP-fs-consgraph" \
    -v "$RRP-tgt-neo4j:/from:ro" \
    -v "$RRP-cons-graph:/to" \
    "$ALPINE_IMAGE" sh -c 'cp -a /from/. /to/' >> "$LOGS/consumer.log" 2>&1 \
    || { timeout "$T_CMD" docker start "$RRP-neo4j-tgt" >> "$LOGS/consumer.log" 2>&1 || true; \
         die "$LOGS/consumer.log" "consumer graph copy failed (target restart attempted)"; }
timeout "$T_CMD" docker start "$RRP-neo4j-tgt" >> "$LOGS/consumer.log" 2>&1 \
    || die "$LOGS/consumer.log" "target restart after graph copy failed"
if ! refresh_address target; then
    die "$LOGS/consumer.log" \
        "TRANSPORT-BLOCKED: target endpoint refresh failed immediately after restart (internal IP may have changed across stop/start; stale address could address the consumer-copy store)"
fi
TARGET_ADDRESS="$REFRESHED_ADDRESS"
echo "target restarted: refreshed endpoint (container_id=$REFRESHED_CID): $TARGET_ADDRESS" \
    >> "$LOGS/consumer.log"
for _ in $(seq 1 60); do
    if timeout "$T_SHORT" docker exec "$RRP-neo4j-tgt" cypher-shell \
        -u neo4j -p "$NEO_PASS" --format plain "RETURN 1" \
        >> "$LOGS/consumer.log" 2>&1; then break; fi
    sleep 5
done || true
if ! python3 "$ORACLE" capture-graph --container "$RRP-neo4j-tgt" \
    --password "$NEO_PASS" --out "$EVID/original-graph-before-consumers.json" \
    >> "$LOGS/consumer.log" 2>&1; then
    die "$LOGS/consumer.log" "pre-consumer original graph capture failed"
fi
if CONSUMER_BOLT_PORT="$(python3 - <<'EOF'
import os, socket
for _ in range(50):
    p = 20000 + (int.from_bytes(os.urandom(2), "big") % 20000)
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
        s.close()
        print(p)
        break
    except OSError:
        continue
EOF
)"; [ -n "$CONSUMER_BOLT_PORT" ]; then
    :
else
    die "$LOGS/consumer.log" "could not find a free loopback port for the consumer graph copy"
fi
timeout "$T_CMD" docker run -d --name "$RRP-neo4j-cons" --network "$RRP-net" \
    -v "$RRP-cons-graph:/data" \
    -v "$RRP-cons-capture:/var/lib/neo4j/import" \
    "${NEO_ENV[@]}" \
    -p "127.0.0.1:$CONSUMER_BOLT_PORT:7687" \
    "$NEO4J_IMAGE" >> "$LOGS/consumer.log" 2>&1 \
    || die "$LOGS/consumer.log" "consumer graph container start failed"
cons_ready=0
for _ in $(seq 1 60); do
    if timeout "$T_SHORT" docker exec "$RRP-neo4j-cons" cypher-shell \
        -u neo4j -p "$NEO_PASS" --format plain "RETURN 1" \
        >> "$LOGS/consumer.log" 2>&1; then cons_ready=1; break; fi
    sleep 5
done
[ "$cons_ready" = 1 ] || die "$LOGS/consumer.log" "consumer graph container not ready in 300s"
if ! python3 "$ORACLE" capture-graph --container "$RRP-neo4j-cons" \
    --password "$NEO_PASS" --out "$EVID/consumer-copy-graph-before.json" \
    >> "$LOGS/consumer.log" 2>&1; then
    die "$LOGS/consumer.log" "pre-boot consumer-copy graph hash failed"
fi
echo "consumer phase runs ONLY against the graph COPY (bolt 127.0.0.1:$CONSUMER_BOLT_PORT) + file-root copies at $WS/consumer-files; original restored volumes + target graph are frozen and verified unchanged after boot" >> "$LOGS/consumer.log"
ADMIN_KEY="fx-synthetic-admin-key-${RUN_ID}"
if python3 - "$WS/fixture-contract.json" "$WS/consumer-results/keys.json" <<'EOF' >> "$LOGS/consumer.log" 2>&1
import json, sys
c = json.load(open(sys.argv[1]))
km = c["key_material_synthetic"]
ids = c["shared_ids"]
keys = [
    {"id": ids["read_key_id"], "plaintext": km["read_key_plaintext"],
     "role": "scoped", "collection": ids["collection_id"]},
    {"id": ids["manage_key_id"], "plaintext": km["manage_key_plaintext"],
     "role": "read"},
]
json.dump(keys, open(sys.argv[2], "w"), indent=1)
print("keys fixture written (synthetic material from fixture-contract.json)")
EOF
then
    :
else
    die "$LOGS/consumer.log" "keys fixture generation failed"
fi
if ! timeout 900 "$CONSUMER_PY" "$BACKEND_CONSUMER" \
    --bolt-url "bolt://127.0.0.1:$CONSUMER_BOLT_PORT" \
    --neo4j-user neo4j \
    --neo4j-password "$NEO_PASS" \
    --files-root "$WS/consumer-files" \
    --admin-key "$ADMIN_KEY" \
    --keys-json "$WS/consumer-results/keys.json" \
    --chat-consumer "$CHAT_CONSUMER" \
    ${CHAT_APP_NODE:+--chat-app-node "$CHAT_APP_NODE"} \
    --expect-document-id fx-source-0001 \
    --expect-collection-id fx-collection-0001 \
    --fulltext-term rehearsal \
    --app-storage-id rehearsal-app \
    --run-id "$RUN_ID" \
    --out "$WS/consumer-results" >> "$LOGS/consumer.log" 2>&1; then
    die "$LOGS/consumer.log" \
        "consumer runner failed/blocked (exit nonzero — see consumer.log for the runner's own precise reason; healthy restored evidence preserved; no green claim)"
fi
CONSUMER_RECEIPT="$WS/consumer-results/backend-consumer-receipt.json"
[ -f "$CONSUMER_RECEIPT" ] \
    || die "$LOGS/consumer.log" \
    "consumer runner wrote no backend-consumer-receipt.json (coordination contract: qa/restore/consumer-coordination.json)"
cp "$CONSUMER_RECEIPT" "$EVID/consumer-results.json"
if python3 - "$EVID/consumer-results.json" \
    "$REPO/qa/restore/consumer-coordination.json" "$REPO/qa/restore" \
    <<'EOF' >> "$LOGS/consumer.log" 2>&1
import json, sys
sys.path.insert(0, sys.argv[3])
from oracle import validate_consumer_receipt
r = json.load(open(sys.argv[1]))
coord = json.load(open(sys.argv[2]))
required = coord["consumer_gate"]["required_chat_check_ids"]
reasons = validate_consumer_receipt(r, required_chat_checks=required)
assert not reasons, "consumer gate rejected: " + "; ".join(reasons)
print("consumer gate: accepted (strict: exit0, zero errors, chat upstream "
      "isolated-backend + identity match, required checks pass, no failed/"
      "blocked network events, boot deltas declared accepted)")
EOF
then
    :
else
    die "$LOGS/consumer.log" \
        "consumer gate rejected (fail closed; receipt preserved as evidence; gate is not weakened to fit a candidate)"
fi
python3 "$ORACLE" capture-graph --container "$RRP-neo4j-tgt" \
    --password "$NEO_PASS" --out "$EVID/original-graph-after-consumers.json" \
    >> "$LOGS/consumer.log" 2>&1 \
    || die "$LOGS/consumer.log" "post-consumer original graph capture failed"
if python3 - "$EVID/original-graph-before-consumers.json" \
    "$EVID/original-graph-after-consumers.json" "$REPO/qa/restore" \
    <<'EOF' >> "$LOGS/consumer.log" 2>&1
import sys
sys.path.insert(0, sys.argv[3])
from oracle import diff_graph, load
d = diff_graph(load(sys.argv[1]), load(sys.argv[2]))
assert not d, f"ORIGINAL target graph changed during consumer phase: {d}"
print("original target graph unchanged after consumer boot (isolation proof)")
EOF
then
    :
else
    die "$LOGS/consumer.log" "isolation breach: original restored graph changed during consumer phase"
fi
mkdir -p "$WS/original-files-after-consumers"
for root in uploads custom_inputs chat skills apps; do
    timeout "$T_CMD" docker run --rm --name "$RRP-fs-origafter-$root" \
        -v "$RRP-tgt-$root:/vol:ro" \
        -v "$WS/original-files-after-consumers:/out" \
        "$ALPINE_IMAGE" sh -c "mkdir -p /out/$root && cp -r /vol/. /out/$root/" \
        >> "$LOGS/consumer.log" 2>&1 \
        || die "$LOGS/consumer.log" "post-consumer original file copy-out failed: $root"
    python3 "$ORACLE" capture-files --dir "$WS/original-files-after-consumers/$root" \
        --out "$EVID/original-files-after-consumers-$root.json" \
        >> "$LOGS/consumer.log" 2>&1 \
        || die "$LOGS/consumer.log" "post-consumer original file manifest failed: $root"
done
ISOLATION_OUT="$(python3 "$ORACLE" isolation-compare --evidence-dir "$EVID" 2>&1)"
ISOLATION_RC="$?"
printf '%s\n' "$ISOLATION_OUT" >> "$LOGS/consumer.log"
if [ "$ISOLATION_RC" = 0 ]; then
    :
elif [ "$ISOLATION_RC" = 2 ]; then
    die "$LOGS/consumer.log" "GATE-ARTIFACT-MISSING: post-consumer original file manifest(s) not produced — isolation cannot be measured (producer failure, not a measured mutation): $ISOLATION_OUT"
else
    die "$LOGS/consumer.log" "isolation breach: original restored file roots changed during consumer phase: $ISOLATION_OUT"
fi
stage_ok "$LOGS/consumer.log" \
    "consumer_results=$EVID/consumer-results.json" \
    "consumer_bolt_port=$CONSUMER_BOLT_PORT (graph copy)" \
    "tgt_bolt_port=$TGT_BOLT_PORT (original, frozen)" \
    "consumer_interpreter=$CONSUMER_PY" \
    "chat_app_node=${CHAT_APP_NODE:-<unset>}" \
    "restored_files_copy=$WS/consumer-files" \
    "runner=qa/restore/backend-consumer.py" \
    "isolation=original graph+files verified unchanged post-boot" \
    "chat_consumer=$CHAT_CONSUMER" \
    "target_container_id_after_restart=$TGT_CID (refreshed at operation: see transport-refresh.log)"
fi

# ------------------------------------------------------------- incomplete control
stage_begin restore-incomplete
if ! refresh_address target; then
    die "$LOGS/restore-incomplete.log" \
        "TRANSPORT-BLOCKED: target endpoint refresh failed before the destructive incomplete restore"
fi
TARGET_ADDRESS="$REFRESHED_ADDRESS"
echo "incomplete restore targets refreshed endpoint (container_id=$REFRESHED_CID): $TARGET_ADDRESS" \
    >> "$LOGS/restore-incomplete.log"
MISSING_BLOB=""
if [ "$CHAT_MODE" = full ] && [ -n "$(find "$WS/frozen-files/chat" -type f \( -name '*.png' -o -name '*.jpg' -o -name '*.webp' -o -name '*.gif' \) 2>/dev/null | head -1)" ]; then
    MISSING_BLOB="data/$(find "$WS/frozen-files/chat" -type f \( -name '*.png' -o -name '*.jpg' -o -name '*.webp' -o -name '*.gif' \) | head -1 | sed "s|$WS/frozen-files/||")"
fi
[ -n "$MISSING_BLOB" ] || MISSING_BLOB="data/uploads/doc-crr-1_rehearsal-alpha.md"
mkdir -p "$WS/staging/incomplete-tree" "$WS/staging/incomplete"
cp -a "$WS/frozen-archive/$BK_TS" "$WS/staging/incomplete/$BK_TS"
tar -xzf "$WS/staging/incomplete/$BK_TS/files.tar.gz" \
    -C "$WS/staging/incomplete-tree" >> "$LOGS/restore-incomplete.log" 2>&1 \
    || die "$LOGS/restore-incomplete.log" "incomplete control: archive extraction failed"
rm -f "$WS/staging/incomplete-tree/$MISSING_BLOB"
[ ! -f "$WS/staging/incomplete-tree/$MISSING_BLOB" ] \
    || die "$LOGS/restore-incomplete.log" "blob removal failed: $MISSING_BLOB"
tar -czf "$WS/staging/incomplete/$BK_TS/files.tar.gz.new" \
    -C "$WS/staging/incomplete-tree" data >> "$LOGS/restore-incomplete.log" 2>&1 \
    || die "$LOGS/restore-incomplete.log" "incomplete control: re-tar failed"
mv "$WS/staging/incomplete/$BK_TS/files.tar.gz.new" \
   "$WS/staging/incomplete/$BK_TS/files.tar.gz"
( cd "$WS/staging/incomplete/$BK_TS" && rm -f SHA256SUMS && sha256sum -- * > SHA256SUMS )
( cd "$WS/staging/incomplete/$BK_TS" && sha256sum -c --quiet SHA256SUMS ) \
    >> "$LOGS/restore-incomplete.log" 2>&1 \
    || die "$LOGS/restore-incomplete.log" "incomplete staging checksums should pass"
[ -f "$WS/staging/incomplete/$BK_TS/.complete" ] \
    || die "$LOGS/restore-incomplete.log" "incomplete staging lost .complete"
for root in uploads custom_inputs chat skills apps; do
    timeout "$T_CMD" docker run --rm --name "$RRP-fs-clear-$root" \
        -v "$RRP-tgt-$root:/clear" \
        "$ALPINE_IMAGE" sh -c 'rm -rf /clear/* /clear/.[!.]* /clear/..?* 2>/dev/null; true'
done >> "$LOGS/restore-incomplete.log" 2>&1
timeout "$T_CMD" docker run --rm --name "$RRP-restore-incomplete" --network "$RRP-net" \
    -v "$WS/staging/incomplete:/backups:ro" \
    -v "$RRP-tgt-uploads:/data/uploads:ro" \
    -v "$RRP-tgt-custom_inputs:/data/custom_inputs:ro" \
    -v "$RRP-tgt-chat:/data/chat:ro" \
    -v "$RRP-tgt-skills:/data/skills:ro" \
    -v "$RRP-tgt-apps:/data/apps:ro" \
    -e RESTORE_WIPE=yes \
    -e "NEO4J_ADDRESS=$TARGET_ADDRESS" \
    -e "NEO4J_PASSWORD=$NEO_PASS" \
    --entrypoint /restore.sh \
    "$RRP-backup-sidecar" "$BK_TS" >> "$LOGS/restore-incomplete.log" 2>&1 \
    || die "$LOGS/restore-incomplete.log" "incomplete restore.sh should have succeeded (valid checksums)"
timeout "$T_CMD" docker run --rm --name "$RRP-untar-incomplete" \
    -v "$RRP-tgt-uploads:/data/uploads" \
    -v "$RRP-tgt-custom_inputs:/data/custom_inputs" \
    -v "$RRP-tgt-chat:/data/chat" \
    -v "$RRP-tgt-skills:/data/skills" \
    -v "$RRP-tgt-apps:/data/apps" \
    -v "$WS/staging/incomplete:/backups:ro" \
    "$ALPINE_IMAGE" tar -xzf "/backups/$BK_TS/files.tar.gz" -C / \
    >> "$LOGS/restore-incomplete.log" 2>&1 \
    || die "$LOGS/restore-incomplete.log" "incomplete untar failed"
stage_ok "$LOGS/restore-incomplete.log" \
    "restore_exit=0 (as intended: transport integrity passes)" \
    "missing_blob=$MISSING_BLOB" "result=restore.sh succeeded, oracle must reject" \
    "transport_mode=$TRANSPORT_MODE" "target_address=$TARGET_ADDRESS" \
    "target_container_id=$REFRESHED_CID"

# ------------------------------------------------------------- oracle incomplete
stage_begin oracle-incomplete
python3 "$ORACLE" capture-graph --container "$RRP-neo4j-tgt" \
    --password "$NEO_PASS" --out "$EVID/actual-graph-incomplete.json" \
    > "$LOGS/oracle-incomplete.log" 2>&1 \
    || die "$LOGS/oracle-incomplete.log" "target graph capture failed"
rm -rf "$WS/restored-files-incomplete"
mkdir -p "$WS/restored-files-incomplete"
for root in uploads custom_inputs chat skills apps; do
    timeout "$T_CMD" docker run --rm --name "$RRP-fs-restored2-$root" \
        -v "$RRP-tgt-$root:/vol:ro" \
        -v "$WS/restored-files-incomplete:/out" \
        "$ALPINE_IMAGE" sh -c "mkdir -p /out/$root && cp -r /vol/. /out/$root/" \
        >> "$LOGS/oracle-incomplete.log" 2>&1 \
        || die "$LOGS/oracle-incomplete.log" "target copy-out failed: $root"
    python3 "$ORACLE" capture-files --dir "$WS/restored-files-incomplete/$root" \
        --out "$EVID/actual-files-incomplete-$root.json" \
        >> "$LOGS/oracle-incomplete.log" 2>&1 \
        || die "$LOGS/oracle-incomplete.log" "target manifest failed: $root"
done
python3 "$ORACLE" compare \
    --expected-graph "$EVID/gate-graph.json" \
    --actual-graph "$EVID/actual-graph-incomplete.json" \
    --expected-roots uploads custom_inputs chat skills apps \
    --expected-files "$EVID/gate-files-uploads.json" "$EVID/gate-files-custom_inputs.json" \
        "$EVID/gate-files-chat.json" "$EVID/gate-files-skills.json" "$EVID/gate-files-apps.json" \
    --actual-roots uploads custom_inputs chat skills apps \
    --actual-files "$EVID/actual-files-incomplete-uploads.json" \
        "$EVID/actual-files-incomplete-custom_inputs.json" \
        "$EVID/actual-files-incomplete-chat.json" \
        "$EVID/actual-files-incomplete-skills.json" \
        "$EVID/actual-files-incomplete-apps.json" \
    --verdict "$EVID/verdict-incomplete.json" --expect reject \
    >> "$LOGS/oracle-incomplete.log" 2>&1 \
    || die "$LOGS/oracle-incomplete.log" "ORACLE ACCEPTED the incomplete restore (gate breach)"
if python3 - "$EVID/verdict-incomplete.json" "$MISSING_BLOB" <<'EOF' >> "$LOGS/oracle-incomplete.log" 2>&1
import json, sys
v = json.load(open(sys.argv[1]))
blob = sys.argv[2]
rel = blob[len("data/"):]
root, _, path = rel.partition("/")
hits = [d for d in v["file_diffs"]
        if d.get("kind") == "missing_file" and d.get("root") == root
        and d.get("path") == path]
assert hits, f"verdict does not name the missing blob {blob}: {v['file_diffs']}"
print("missing blob correctly named in rejection")
EOF
then
    :
else
    die "$LOGS/oracle-incomplete.log" "rejection did not name the missing blob"
fi
stage_ok "$LOGS/oracle-incomplete.log" \
    "verdict=$EVID/verdict-incomplete.json" "result=REJECT" \
    "missing_blob=$MISSING_BLOB"

# ------------------------------------------------------------- refusal controls
stage_begin refusal-controls
if ! refresh_address target; then
    die "$LOGS/refusal.log" \
        "TRANSPORT-BLOCKED: target endpoint refresh failed before the refusal operations"
fi
TARGET_ADDRESS="$REFRESHED_ADDRESS"
echo "refusal probes target refreshed endpoint (container_id=$REFRESHED_CID): $TARGET_ADDRESS" \
    >> "$LOGS/refusal.log"
NODE_COUNT() {
    timeout "$T_SHORT" docker exec "$RRP-neo4j-tgt" cypher-shell -u neo4j -p "$NEO_PASS" \
        --format plain "MATCH (n) RETURN count(n)" | tail -1 | tr -d ' '
}
mkdir -p "$WS/staging/nocomplete" "$WS/staging/badsum"
cp -a "$WS/frozen-archive/$BK_TS" "$WS/staging/nocomplete/$BK_TS"
rm -f "$WS/staging/nocomplete/$BK_TS/.complete"
cp -a "$WS/frozen-archive/$BK_TS" "$WS/staging/badsum/$BK_TS"
printf 'X' | dd of="$WS/staging/badsum/$BK_TS/graph.cypher.gz" bs=1 seek=50 \
    conv=notrunc status=none
: > "$LOGS/refusal.log"
N0="$(NODE_COUNT)"
python3 "$ORACLE" capture-graph --container "$RRP-neo4j-tgt" \
    --password "$NEO_PASS" --out "$WS/refusal-graph-before.json" \
    >> "$LOGS/refusal.log" 2>&1 \
    || die "$LOGS/refusal.log" "pre-refusal graph capture failed"
set +e
timeout "$T_CMD" docker run --rm --name "$RRP-restore-nocomplete" --network "$RRP-net" \
    -v "$WS/staging/nocomplete:/backups:ro" \
    -e RESTORE_WIPE=yes \
    -e "NEO4J_ADDRESS=$TARGET_ADDRESS" \
    -e "NEO4J_PASSWORD=$NEO_PASS" \
    --entrypoint /restore.sh \
    "$RRP-backup-sidecar" "$BK_TS" >> "$LOGS/refusal-nocomplete.log" 2>&1
RC1=$?
N1="$(NODE_COUNT)"
timeout "$T_CMD" docker run --rm --name "$RRP-restore-badsum" --network "$RRP-net" \
    -v "$WS/staging/badsum:/backups:ro" \
    -e RESTORE_WIPE=yes \
    -e "NEO4J_ADDRESS=$TARGET_ADDRESS" \
    -e "NEO4J_PASSWORD=$NEO_PASS" \
    --entrypoint /restore.sh \
    "$RRP-backup-sidecar" "$BK_TS" >> "$LOGS/refusal-badsum.log" 2>&1
RC2=$?
N2="$(NODE_COUNT)"
set -e
[ "$RC1" -ne 0 ] || die "$LOGS/refusal.log" "no-.complete copy was ACCEPTED by restore.sh"
[ "$RC2" -ne 0 ] || die "$LOGS/refusal.log" "checksum-corrupt copy was ACCEPTED by restore.sh"
grep -qF "no .complete marker" "$LOGS/refusal-nocomplete.log" \
    || die "$LOGS/refusal.log" "no-.complete refusal lacked the .complete diagnostic (exit was nonzero for another reason)"
grep -qF "checksum mismatch" "$LOGS/refusal-badsum.log" \
    || die "$LOGS/refusal.log" "checksum refusal lacked the checksum diagnostic (exit was nonzero for another reason)"
python3 "$ORACLE" capture-graph --container "$RRP-neo4j-tgt" \
    --password "$NEO_PASS" --out "$WS/refusal-graph-after.json" \
    >> "$LOGS/refusal.log" 2>&1 \
    || die "$LOGS/refusal.log" "post-refusal graph capture failed"
if python3 - "$WS/refusal-graph-before.json" "$WS/refusal-graph-after.json" \
    "$REPO/qa/restore" <<'EOF' >> "$LOGS/refusal.log" 2>&1
import sys
sys.path.insert(0, sys.argv[3])
from oracle import diff_graph, load
d = diff_graph(load(sys.argv[1]), load(sys.argv[2]))
assert not d, f"target graph changed across refusals: {d}"
print("target graph contents identical across refusals")
EOF
then
    :
else
    die "$LOGS/refusal.log" "exact graph compare across refusals failed"
fi
[ "$N1" = "$N0" ] || echo "advisory: nodecount changed across refusal ($N0 -> $N1)" >> "$LOGS/refusal.log"
[ "$N2" = "$N0" ] || echo "advisory: nodecount changed across refusal ($N0 -> $N2)" >> "$LOGS/refusal.log"
stage_ok "$LOGS/refusal.log" \
    "nocomplete_exit=$RC1" "badsum_exit=$RC2" \
    "diagnostics=.complete-marker + checksum-mismatch asserted" \
    "node_count_before=$N0(advisory)" \
    "node_count_after_nocomplete=$N1(advisory)" \
    "node_count_after_badsum=$N2(advisory)" \
    "transport_mode=$TRANSPORT_MODE" "target_address=$TARGET_ADDRESS" \
    "target_container_id=$REFRESHED_CID" \
    "result=refused-before-wipe, exact graph contents unchanged"

# ------------------------------------------------------------- finalize
stage_begin finalize
( cd "$WS/frozen-archive/$BK_TS" && sha256sum -c --quiet SHA256SUMS ) \
    >> "$LOGS/finalize.log" 2>&1 \
    || die "$LOGS/finalize.log" "frozen archive changed during the run"
VERIFY_ARGS=()
for f in "${PROV_FILES[@]}"; do VERIFY_ARGS+=(--file "$f"); done
if python3 "$ORACLE" provenance-verify --provenance "$EVID/provenance.json" \
    --out "$OUT" "${VERIFY_ARGS[@]}" \
    --app-repo "$REPO" --chat-repo "$REPO/../cortex-chat" \
    >> "$LOGS/finalize.log" 2>&1
then
    :
else
    die "$LOGS/finalize.log" \
        "input/git/code-manifest identity verification failed (fail closed — no result may be claimed on invalid provenance)"
fi
mkdir -p "$EVID/archive-frozen"
cp -a "$WS/frozen-archive/$BK_TS" "$EVID/archive-frozen/$BK_TS"
if grep -rlF "$NEO_PASS" "$LOGS" 2>/dev/null | while read -r f; do
        sed -i "s/$NEO_PASS/<synthetic-neo4j-password>/g" "$f"
    done; then
    :
fi
python3 "$ORACLE" hashes --out "$EVID/final-hashes.json" --paths \
    "$EVID/gate-graph.json" "$EVID/actual-graph-healthy.json" \
    "$EVID/actual-graph-incomplete.json" "$EVID/verdict-healthy.json" \
    "$EVID/verdict-incomplete.json" "$EVID/provenance.json" \
    >> "$LOGS/finalize.log" 2>&1 \
    || die "$LOGS/finalize.log" "final hashing failed"
RESULT_TEXT="PASS: healthy restore accepted, valid-checksum-incomplete restore rejected, refusals held"
if [ "$CHAT_MODE" = smoke ]; then
    RESULT_TEXT="REDUCED SMOKE (no chat fixture — NOT whole-stack restore evidence): healthy restore accepted, valid-checksum-incomplete restore rejected, refusals held"
fi
if python3 "$ORACLE" summary-emitter \
    --out "$OUT" \
    --run-id "$RUN_ID" --backup-ts "$BK_TS" --network-prefix "$RRP" \
    --neo4j-image "$NEO4J_IMAGE" --neo4j-image-id "$NEO4J_IMAGE_ID" \
    --neo4j-digest "$NEO4J_DIGEST" --apoc-version "$APOC_VERSION" \
    --neo4j-version "$NEO_VERSION" --sidecar-image-id "$SIDECAR_ID" \
    --mode "$CHAT_MODE" --chat-fixture "$CHAT_STATUS" \
    --chat-postt "$CHAT_POSTT_STATUS" --missing-blob "$MISSING_BLOB" \
    --chat-verify-frozen "$CHAT_VERIFY_STATUS" \
    --chat-verify-restored "$CHAT_RESTORED_VERIFY" \
    --fulltext-source "$FT_SRC_COUNT" --fulltext-target "$FT_TGT_COUNT" \
    --result-text "$RESULT_TEXT" --consumers-requested "$CONSUMERS" \
    --target-bolt-port "${TGT_BOLT_PORT:-<not-published>}" \
    --transport-mode "$TRANSPORT_MODE" --source-address "$SOURCE_ADDRESS" \
    --target-address "$TARGET_ADDRESS" \
    >> "$LOGS/finalize.log" 2>&1
then
    :
else
    die "$LOGS/finalize.log" "summary writing failed (named-argument emitter; no positional slice, no silent truncation)"
fi
stage_ok "$LOGS/finalize.log" "summary=$OUT/summary.json"

if [ "$CLEANUP" = 1 ]; then
    stage_begin cleanup
    if cleanup; then
        stage_ok "$LOGS/cleanup.log" "cleaned=containers,volumes,network,sidecar-image,workspace"
    else
        die "$LOGS/cleanup.log" "cleanup incomplete — leftovers recorded in log (fail closed)"
    fi
fi

echo ""
echo "REHEARSAL COMPLETE: $OUT/summary.json"
