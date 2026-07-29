#!/usr/bin/env bash
# Verifies the selfhost stack's optional-chat contract against real Compose and
# Caddy. Every assertion corresponds to a behaviour that no file-level review
# can see and that silently produces a broken stack if it regresses. See
# docs/superpowers/specs/2026-07-29-optional-cortex-chat-design.md.
#
# Usage: selfhost/verify-contract.sh   (needs docker + compose v2)
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

cp "$here"/docker-compose.yml "$here"/docker-compose.ports.yml \
   "$here"/docker-compose.caddy.yml "$work/"

# Every ${VAR:?} the stack enforces, satisfied with placeholders. `docker compose
# config` only interpolates and validates — it connects to nothing.
cat > "$work/.env" <<'EOF'
COMPOSE_PROJECT_NAME=contract
CORTEX_BACKEND_IMAGE=ghcr.io/mocaos/cortex-backend:0.0.0
CORTEX_FRONTEND_IMAGE=ghcr.io/mocaos/cortex-frontend:0.0.0
CORTEX_CHAT_IMAGE=ghcr.io/mocaos/cortex-chat:0.0.0
NEO4J_PASSWORD=placeholder
OPENAI_API_KEY=placeholder
ADMIN_PASSWORD=placeholder
ADMIN_API_KEY=placeholder
SESSION_SECRET=placeholder
CHAT_APP_ENCRYPTION_KEY=placeholder
APP_DOMAIN=cortex.example.com
ACME_EMAIL=ops@example.com
EOF

# The backend service also references these without a `${VAR:?}` guard or a
# default, so they never block `docker compose config` — but left absent, it
# still logs a "variable is not set" warning on stderr for each one. `services()`
# below deliberately keeps stderr merged into the comparison (so a real Compose
# error is visible in `actual` on FAIL), which means these benign warnings would
# otherwise sort themselves right into the services string and desync every
# check. Blank placeholders silence the warnings without touching that merge.
cat >> "$work/.env" <<'EOF'
OPENAI_API_BASE=
OPENAI_MODEL=
EMBEDDING_MODEL=
EMBEDDING_DIMENSION=
EOF

fail=0
check() {
  if [ "$2" = "$3" ]; then
    printf '  ok    %s\n' "$1"
  else
    printf '  FAIL  %s\n          expected: %s\n          actual:   %s\n' "$1" "$2" "$3"
    fail=1
  fi
}

services() {
  ( cd "$work" && COMPOSE_FILE="$1" COMPOSE_PROFILES="${2:-}" \
      docker compose config --services 2>&1 | sort | tr '\n' ' ' | sed 's/ *$//' )
}

PORTS=docker-compose.yml:docker-compose.ports.yml
CADDY=docker-compose.yml:docker-compose.caddy.yml

echo "Compose: chat excluded by default, included with the profile"
# Note CHAT_DOMAIN is absent from .env above: domain mode must resolve without
# it, which is what relaxing caddy's guard to ${CHAT_DOMAIN:-} buys.
check "localhost, chat off" "backend backup frontend neo4j"             "$(services "$PORTS")"
check "localhost, chat on"  "backend backup chat frontend neo4j"        "$(services "$PORTS" chat)"
check "domain, chat off"    "backend backup caddy frontend neo4j"       "$(services "$CADDY")"
check "domain, chat on"     "backend backup caddy chat frontend neo4j"  "$(services "$CADDY" chat)"

echo
echo "Caddy: both templates adapt"
caddyv() {
  docker run --rm -v "$here/$1:/etc/caddy/Caddyfile:ro" \
    -e APP_DOMAIN=cortex.example.com -e ACME_EMAIL=ops@example.com \
    ${2:+-e CHAT_DOMAIN=chat.example.com} \
    caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile 2>&1
}
app_out="$(caddyv Caddyfile.template || true)"
chat_out="$(caddyv Caddyfile.chat.template with-chat || true)"
check "Caddyfile.template valid with CHAT_DOMAIN unset" \
  "yes" "$(grep -q 'Valid configuration' <<<"$app_out" && echo yes || echo no)"
check "Caddyfile.chat.template valid" \
  "yes" "$(grep -q 'Valid configuration' <<<"$chat_out" && echo yes || echo no)"
# A formatting warning is not fatal to Caddy but appears in the operator's logs
# on every start, so it is a defect here.
check "Caddyfile.template formatted" \
  "yes" "$(grep -q 'not formatted' <<<"$app_out" && echo no || echo yes)"
check "Caddyfile.chat.template formatted" \
  "yes" "$(grep -q 'not formatted' <<<"$chat_out" && echo no || echo yes)"

echo
if [ "$fail" -ne 0 ]; then echo "selfhost contract: FAILED"; exit 1; fi
echo "selfhost contract: OK"
