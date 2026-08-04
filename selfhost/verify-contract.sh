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

fail=0
check() {
  if [ "$2" = "$3" ]; then
    printf '  ok    %s\n' "$1"
  else
    printf '  FAIL  %s\n          expected: %s\n          actual:   %s\n' "$1" "$2" "$3"
    # Only the compose checks pass a 4th arg (see services()/compose_stderr()
    # below) — surfaced here, and only here, so a real failure is still
    # debuggable without it ever being able to affect the comparison itself.
    [ -n "${4:-}" ] && printf '          stderr:   %s\n' "$4"
    fail=1
  fi
}

# Captures Compose's stdout and stderr separately: stdout (the actual service
# list) is the only thing any check compares against; stderr — e.g. the
# warning `docker compose config` logs for an undefaulted ${VAR} such as
# OPENAI_API_BASE — goes to $work/services.err and is surfaced only in a FAIL
# diagnostic via compose_stderr(). This is deliberate: an earlier version of
# this script merged the two streams (2>&1) before comparing, so any future
# addition of a new bare ${VAR} to docker-compose.yml (or a Compose/Docker
# version that adds a new stderr notice) would reproduce that exact
# false-FAIL. Separating the streams removes the coupling entirely — the
# services list is compared as Compose actually resolved it, full stop.
services() {
  ( cd "$work" && COMPOSE_FILE="$1" COMPOSE_PROFILES="${2:-}" \
      docker compose config --services 2>"$work/services.err" | sort | tr '\n' ' ' | sed 's/ *$//' )
}
compose_stderr() { cat "$work/services.err" 2>/dev/null; }

PORTS=docker-compose.yml:docker-compose.ports.yml
CADDY=docker-compose.yml:docker-compose.caddy.yml

echo "Compose: chat excluded by default, included with the profile"
# Note CHAT_DOMAIN is absent from .env above: domain mode must resolve without
# it, which is what relaxing caddy's guard to ${CHAT_DOMAIN:-} buys.
#
# `|| true` matches the same guard caddyv() is called with below: a real
# regression (e.g. a broken depends_on) makes `docker compose config` itself
# exit non-zero, and under `set -e` an unguarded assignment would abort the
# whole script right there — losing the FAIL line, the diagnostic, and every
# later check. `|| true` keeps that failure inside check()'s reporting.
actual="$(services "$PORTS" || true)";      check "localhost, chat off" "backend backup frontend neo4j"             "$actual" "$(compose_stderr)"
actual="$(services "$PORTS" chat || true)"; check "localhost, chat on"  "backend backup chat frontend neo4j"        "$actual" "$(compose_stderr)"
actual="$(services "$CADDY" || true)";      check "domain, chat off"    "backend backup caddy frontend neo4j"       "$actual" "$(compose_stderr)"
actual="$(services "$CADDY" chat || true)"; check "domain, chat on"     "backend backup caddy chat frontend neo4j"  "$actual" "$(compose_stderr)"

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
echo "Caddy: the chat template must FAIL to adapt when CHAT_DOMAIN is unset"
# This composes the two checks above instead of leaving them separate: "chat
# excluded/included by the profile" proves Compose can run without
# CHAT_DOMAIN, and "both templates adapt" above only ever validated the chat
# template WITH a domain given. Neither, alone or together, asserts the
# specific combination Fix 1 (final review) exists to prevent: chat resolved
# on in domain mode with .env holding no CHAT_DOMAIN. caddyTemplateFor
# (artifacts.ts) picks Caddyfile.chat.template from the chat flag alone, so if
# Caddy ever tolerated an empty {$CHAT_DOMAIN} here, the installer-side guard
# (assertChatDomainConfigured, commands/update.ts) would be protecting against
# a state Caddy no longer minds — this pins the coupling so a Caddy upgrade
# that changes this behaviour is caught here, not in an operator's outage.
# No `with-chat` argument here (unlike chat_out above), so caddyv omits
# -e CHAT_DOMAIN=... — reproducing the broken .env exactly.
broken_out="$(caddyv Caddyfile.chat.template || true)"
check "Caddyfile.chat.template does NOT report Valid configuration with CHAT_DOMAIN unset" \
  "yes" "$(grep -q 'Valid configuration' <<<"$broken_out" && echo no || echo yes)"
check "Caddyfile.chat.template's failure is the CHAT_DOMAIN-empty error, not something else" \
  "yes" "$(grep -q 'server block without any key is global configuration' <<<"$broken_out" && echo yes || echo no)"

echo
if [ "$fail" -ne 0 ]; then echo "selfhost contract: FAILED"; exit 1; fi
echo "selfhost contract: OK"
