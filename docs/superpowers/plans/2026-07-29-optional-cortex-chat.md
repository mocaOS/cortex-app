# Optional Cortex Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let someone installing Cortex choose whether Cortex Chat is installed alongside it, defaulting to off, without existing installs losing the chat they already run.

**Architecture:** The released stack puts the `chat` service behind a Compose profile, so `COMPOSE_PROFILES=chat` in `.env` is the entire on/off switch. Domain mode ships two Caddyfile templates because a Caddyfile with an unset site address fails to adapt at all. The installer asks one question (default No), records the answer in `cortex.json`, and uses it to decide what to write, what to wait for, and which Caddyfile to copy.

**Tech Stack:** Docker Compose v2 profiles, Caddy 2, TypeScript ESM (NodeNext, `tsc` to `dist/`), `node:test` + `assert/strict`, `@clack/prompts`, `semver`.

**Spec:** `docs/superpowers/specs/2026-07-29-optional-cortex-chat-design.md`

## Global Constraints

- Two repos. `cortex-app` = `/Volumes/WD_BLACK/PROJECTS/CORTEX/cortex`, `cortex-installer` = `/Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer`. Never mix their commits.
- **Compose interpolates `${VAR:?}` BEFORE it filters profiles.** A required var referenced by the profiled `chat` service still aborts the whole project when the profile is off. `CORTEX_CHAT_IMAGE` and `CHAT_APP_ENCRYPTION_KEY` must therefore always be written to `.env`. Do not "clean these up".
- **`CHAT_PORT` is `${CHAT_PORT:-3001}`** in the ports overlay — it has a default, so it is retained purely to make enabling chat later a one-line edit, not because Compose needs it.
- Default is **off**: `initialValue: false`, and `CORTEX_ENABLE_CHAT` must be exactly `"true"` to enable.
- Version targets: `cortex-app` 1.0.1 → **1.1.0**; `cortex-installer` 1.1.0 → **1.2.0**; `minInstaller` 1.0.2 → **1.2.0**; `CHAT_OPTIONAL_SINCE = "1.1.0"`.
- Never write a shell script under `cortex-app/scripts/` — `.gitignore` has `/scripts/*` with only `!/scripts/*.mjs` negated, so a `.sh` there is silently untracked.
- Installer tests: `npm test` (which typechecks first). cortex-app script tests: `node --test scripts/*.test.mjs`.
- Do not touch `status`, `doctor`, `formatStatusTable` or `uninstall`. They are driven by `docker compose ps` output and volume prefixes, so an absent chat service already produces the right result.

---

### Task 1: Put the chat service behind a Compose profile

**Files:**
- Modify: `cortex-app/selfhost/docker-compose.yml` (the `chat:` service)
- Modify: `cortex-app/selfhost/docker-compose.caddy.yml` (the `caddy:` service)

**Interfaces:**
- Consumes: nothing.
- Produces: the profile name `chat`, and the guarantee that `COMPOSE_PROFILES=chat` is the only switch. Task 6 writes that line; Task 3 asserts this contract.

- [ ] **Step 1: Verify the current behaviour, so the change is measurable**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex/selfhost
rm -rf /tmp/t1 && mkdir -p /tmp/t1
cp docker-compose.yml docker-compose.ports.yml docker-compose.caddy.yml /tmp/t1/
cat > /tmp/t1/.env <<'EOF'
COMPOSE_PROJECT_NAME=t1
CORTEX_BACKEND_IMAGE=x:0
CORTEX_FRONTEND_IMAGE=x:0
CORTEX_CHAT_IMAGE=x:0
NEO4J_PASSWORD=p
OPENAI_API_KEY=p
ADMIN_PASSWORD=p
ADMIN_API_KEY=p
SESSION_SECRET=p
CHAT_APP_ENCRYPTION_KEY=p
APP_DOMAIN=a.example.com
CHAT_DOMAIN=c.example.com
ACME_EMAIL=o@example.com
EOF
cd /tmp/t1 && COMPOSE_FILE=docker-compose.yml:docker-compose.ports.yml docker compose config --services | sort | tr '\n' ' '
```

Expected: `backend backup chat frontend neo4j` — chat is unconditionally present today.

- [ ] **Step 2: Add the profile to the chat service**

In `selfhost/docker-compose.yml`, inside the `chat:` service, immediately after the `image:` line, add:

```yaml
    # Cortex Chat is optional and OFF by default: it is a separate front end,
    # and nothing in the backend or frontend references it. `COMPOSE_PROFILES=chat`
    # in .env is the entire switch.
    #
    # NOTE: this does NOT stop Compose interpolating the variables below.
    # Interpolation runs before profile filtering, so CORTEX_CHAT_IMAGE and
    # CHAT_APP_ENCRYPTION_KEY must still be set in .env even with chat off —
    # `${CORTEX_CHAT_IMAGE:?}` aborts the whole project otherwise. The installer
    # always writes both for exactly this reason.
    profiles: ["chat"]
```

- [ ] **Step 3: Verify chat is now excluded, and that the ports overlay follows it**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex/selfhost
cp docker-compose.yml /tmp/t1/
cd /tmp/t1
echo "off: $(COMPOSE_FILE=docker-compose.yml:docker-compose.ports.yml docker compose config --services 2>&1 | sort | tr '\n' ' ')"
echo "on:  $(COMPOSE_FILE=docker-compose.yml:docker-compose.ports.yml COMPOSE_PROFILES=chat docker compose config --services 2>&1 | sort | tr '\n' ' ')"
```

Expected:
```
off: backend backup frontend neo4j
on:  backend backup chat frontend neo4j
```

`docker-compose.ports.yml` still declares a `chat:` block with a published port and needs no change — it merges into the profiled service and is excluded with it.

- [ ] **Step 4: Confirm the caddy overlay is now broken, so the next fix is justified**

```bash
cd /tmp/t1
COMPOSE_FILE=docker-compose.yml:docker-compose.caddy.yml docker compose config --services 2>&1 | head -2
```

Expected: `service "caddy" depends on undefined service "chat": invalid compose project`

- [ ] **Step 5: Fix the caddy overlay**

In `selfhost/docker-compose.caddy.yml`, in the `caddy:` service, replace this line:

```yaml
        - CHAT_DOMAIN=${CHAT_DOMAIN:?CHAT_DOMAIN is required in domain mode}
```

with:

```yaml
        # Not `:?` — chat is optional, and with it off there is no chat domain to
        # require. The app-only Caddyfile never references this, so it is simply
        # unused then. Losing the Compose-level error is deliberate: Compose
        # cannot express "required only when a profile is active", and the
        # installer always writes it when chat is on.
        - CHAT_DOMAIN=${CHAT_DOMAIN:-}
```

and replace the `depends_on` list:

```yaml
      depends_on:
        - frontend
        - chat
```

with the long form, which is the only way to depend on a service that may be
excluded by a profile:

```yaml
      depends_on:
        frontend:
          condition: service_started
        # required: false — chat may be excluded by its profile. The short list
        # form makes Compose reject the whole project with "depends on undefined
        # service chat" when that happens.
        chat:
          condition: service_started
          required: false
```

- [ ] **Step 6: Verify both modes now resolve with the profile on and off**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex/selfhost
cp docker-compose.caddy.yml /tmp/t1/
cd /tmp/t1
for f in docker-compose.yml:docker-compose.ports.yml docker-compose.yml:docker-compose.caddy.yml; do
  for prof in "" chat; do
    printf '%-52s %-5s %s\n' "$f" "${prof:-off}" \
      "$(COMPOSE_FILE=$f COMPOSE_PROFILES=$prof docker compose config --services 2>&1 | sort | tr '\n' ' ')"
  done
done
rm -rf /tmp/t1
```

Expected — four lines, no errors:
```
...ports.yml   off   backend backup frontend neo4j
...ports.yml   chat  backend backup chat frontend neo4j
...caddy.yml   off   backend backup caddy frontend neo4j
...caddy.yml   chat  backend backup caddy chat frontend neo4j
```

- [ ] **Step 7: Commit**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex
git add selfhost/docker-compose.yml selfhost/docker-compose.caddy.yml
git commit -m "feat(selfhost): put the chat service behind a Compose profile

Cortex Chat becomes opt-in: COMPOSE_PROFILES=chat in .env is the whole
switch. docker-compose.ports.yml needs no change, because its chat entry
merges into the profiled service and is excluded with it.

Two consequences handled here:

- caddy's depends_on had to move to the long form with required: false.
  The short list form makes Compose reject the entire project with
  'depends on undefined service chat' the moment the profile is off.
- caddy's own CHAT_DOMAIN guard relaxes from \${VAR:?} to \${VAR:-}. With
  chat off there is no chat domain to require, and Compose cannot express
  'required only while a profile is active'."
```

---

### Task 2: Split the Caddyfile into app-only and with-chat templates

**Files:**
- Modify: `cortex-app/selfhost/Caddyfile.template`
- Create: `cortex-app/selfhost/Caddyfile.chat.template`

**Interfaces:**
- Consumes: nothing.
- Produces: the two filenames. Task 3 validates them; Task 8 (`artifacts.ts`) copies one of them based on the chat choice; Task 4 documents them.

- [ ] **Step 1: Prove the app-only case is broken today**

An unset `{$CHAT_DOMAIN}` does not degrade — Caddy refuses to adapt the config at all, so it would never start.

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex
docker run --rm -v "$PWD/selfhost/Caddyfile.template:/etc/caddy/Caddyfile:ro" \
  -e APP_DOMAIN=cortex.example.com -e ACME_EMAIL=ops@example.com \
  caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile 2>&1 | tail -2
```

Expected: `Error: adapting config using caddyfile: server block without any key is global configuration, and if used, it must be first`

- [ ] **Step 2: Create the with-chat template**

Create `selfhost/Caddyfile.chat.template` with exactly this content (tabs, not spaces — Caddy's own formatter uses tabs and `caddy validate` warns otherwise):

```
# Cortex — Caddy reverse proxy, with Cortex Chat.
#
# Used when chat is installed. The app-only equivalent is Caddyfile.template;
# `npx @mocaos/cortex` copies whichever matches your install to ./Caddyfile.
#
# Caddy substitutes {$VAR} from its container environment at load time, so this
# file is static and needs no rendering.
#
# The backend needs no domain of its own: the frontend proxies /api/*, /apps/*
# and /a/* to it, so the API is reachable at https://{$APP_DOMAIN}/api/...
{
	email {$ACME_EMAIL}
}

{$APP_DOMAIN} {
	reverse_proxy frontend:3000
}

{$CHAT_DOMAIN} {
	reverse_proxy chat:3000
}
```

- [ ] **Step 3: Reduce Caddyfile.template to app-only**

Replace `selfhost/Caddyfile.template` entirely with:

```
# Cortex — Caddy reverse proxy.
#
# This is the app-only config, used when Cortex Chat is not installed (the
# default). With chat, Caddyfile.chat.template is used instead — a site block
# for an unset {$CHAT_DOMAIN} does not just warn, it makes Caddy refuse to
# adapt the whole config, so the two cases need separate files.
#
# Caddy substitutes {$VAR} from its container environment at load time, so this
# file is static and needs no rendering.
#
# The backend needs no domain of its own: the frontend proxies /api/*, /apps/*
# and /a/* to it, so the API is reachable at https://{$APP_DOMAIN}/api/...
{
	email {$ACME_EMAIL}
}

{$APP_DOMAIN} {
	reverse_proxy frontend:3000
}
```

- [ ] **Step 4: Validate both, including that neither is unformatted**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex
echo "── app-only, CHAT_DOMAIN deliberately unset ──"
docker run --rm -v "$PWD/selfhost/Caddyfile.template:/etc/caddy/Caddyfile:ro" \
  -e APP_DOMAIN=cortex.example.com -e ACME_EMAIL=ops@example.com \
  caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile 2>&1 | grep -iE "valid|error|not formatted"
echo "── with chat ──"
docker run --rm -v "$PWD/selfhost/Caddyfile.chat.template:/etc/caddy/Caddyfile:ro" \
  -e APP_DOMAIN=cortex.example.com -e CHAT_DOMAIN=chat.example.com -e ACME_EMAIL=ops@example.com \
  caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile 2>&1 | grep -iE "valid|error|not formatted"
```

Expected: `Valid configuration` for both, and **no** "not formatted" line for either. If you see the formatting warning, run `docker run --rm -v "$PWD/selfhost:/w" -w /w caddy:2-alpine caddy fmt --overwrite <file>` and re-validate.

- [ ] **Step 5: Commit**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex
git add selfhost/Caddyfile.template selfhost/Caddyfile.chat.template
git commit -m "feat(selfhost): split the Caddyfile into app-only and with-chat templates

A site block whose address resolves to nothing does not warn — Caddy
refuses to adapt the config entirely ('server block without any key is
global configuration'), so it never starts. An app-only domain install
therefore cannot keep the {\$CHAT_DOMAIN} block at all.

Caddyfile.template is now app-only and Caddyfile.chat.template carries
both site blocks. An optional glob import would avoid the duplication but
logs 'No files matching import glob pattern' on every start, and chat-off
is the new default, so the quiet option won."
```

---

### Task 3: Lock the contract with a verification script wired into CI

**Files:**
- Create: `cortex-app/selfhost/verify-contract.sh`
- Modify: `cortex-app/.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Tasks 1 and 2 (`profiles: ["chat"]`, `required: false`, both templates).
- Produces: `selfhost/verify-contract.sh`, runnable locally with no arguments.

Why a shell script and not a unit test: every fact here is a *Compose and Caddy* behaviour, invisible to any file-level or TypeScript-level check. Note it must **not** live in `cortex-app/scripts/` — `.gitignore` ignores everything there except `*.mjs`, so a `.sh` would be silently untracked.

- [ ] **Step 1: Write the script**

Create `selfhost/verify-contract.sh`:

```bash
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
```

- [ ] **Step 2: Make it executable and run it**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex
chmod +x selfhost/verify-contract.sh
./selfhost/verify-contract.sh
```

Expected: eight `ok` lines and `selfhost contract: OK`, exit 0.

- [ ] **Step 3: Prove the script actually catches a regression**

Temporarily break the caddy overlay, confirm the script fails, then restore it.

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex
cp selfhost/docker-compose.caddy.yml /tmp/caddy.bak
python3 - <<'PY'
import re, pathlib
p = pathlib.Path("selfhost/docker-compose.caddy.yml")
s = p.read_text().replace("          required: false\n", "")
p.write_text(s)
PY
./selfhost/verify-contract.sh; echo "exit=$?"
cp /tmp/caddy.bak selfhost/docker-compose.caddy.yml && rm /tmp/caddy.bak
./selfhost/verify-contract.sh >/dev/null && echo "restored OK"
```

Expected: the middle run prints `FAIL domain, chat off` and `exit=1`; the final line prints `restored OK`.

- [ ] **Step 4: Wire it into CI**

In `.github/workflows/ci.yml`, add a job. Match the file's existing indentation and `runs-on` style:

```yaml
  selfhost-contract:
    name: Selfhost compose contract
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # Asserts the chat service is excluded by default and included with
      # COMPOSE_PROFILES=chat, in both modes, and that both Caddyfile templates
      # adapt. These are Compose/Caddy behaviours that no unit test can see.
      - run: ./selfhost/verify-contract.sh
```

- [ ] **Step 5: Verify the YAML parses and the job is registered**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex
python3 -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/ci.yml')); print('jobs:', list(d['jobs']))"
```

Expected: the job list includes `selfhost-contract`.

- [ ] **Step 6: Commit**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex
git add selfhost/verify-contract.sh .github/workflows/ci.yml
git commit -m "test(selfhost): assert the optional-chat contract in CI

Eight assertions against real Compose and Caddy: chat absent by default
and present with COMPOSE_PROFILES=chat in both modes, both Caddyfile
templates adapting, and neither carrying a formatting warning.

None of this is visible to a file-level review or a TypeScript test. The
depends_on/required:false detail in particular fails as 'depends on
undefined service chat' — a whole-project error — and only shows up when
Compose actually resolves the files.

Lives in selfhost/ rather than scripts/, where .gitignore's /scripts/*
rule would silently leave a .sh untracked."
```

---

### Task 4: Document the opt-in for the manual path

**Files:**
- Modify: `cortex-app/selfhost/.env.example`
- Modify: `cortex-app/selfhost/README.md`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: `.env.example` carrying the commented `COMPOSE_PROFILES=chat` line that Task 6's rendering mirrors.

- [ ] **Step 1: Add the opt-in block to .env.example**

In `selfhost/.env.example`, directly after the `COMPOSE_PROJECT_NAME=cortex` line, insert:

```dotenv

# --- Cortex Chat (optional, OFF by default) -----------------------------
# Cortex Chat is a separate chat front end. Nothing in Cortex itself needs
# it. To run it, uncomment the line below and `docker compose up -d`.
#
# In domain mode you must also set CHAT_DOMAIN and CHAT_BASE_URL, add the
# chat origin to CORS_ALLOWED_ORIGINS, and use Caddyfile.chat.template
# instead of Caddyfile.template.
# COMPOSE_PROFILES=chat
```

- [ ] **Step 2: Explain why the two chat variables stay set**

In `selfhost/.env.example`, change the `CORTEX_CHAT_IMAGE` line to carry a preceding comment:

```dotenv
# Read by Compose even when chat is not running: variable interpolation
# happens before profile filtering, so an unset value here aborts the whole
# project. Leave it set regardless.
CORTEX_CHAT_IMAGE=ghcr.io/mocaos/cortex-chat:1.0.0
```

and add the same note above `CHAT_APP_ENCRYPTION_KEY=` in the secrets block:

```dotenv
# Also required with chat off — see the note on CORTEX_CHAT_IMAGE. Generating
# it now additionally means enabling chat later is a one-line change.
CHAT_APP_ENCRYPTION_KEY=
```

- [ ] **Step 3: Update selfhost/README.md**

Change the opening line from `Runs Cortex and Cortex Chat from prebuilt images.` to:

```markdown
Runs Cortex from prebuilt images, with Cortex Chat as an opt-in extra.
Everything is configured through `.env`; the Compose files are static release
artifacts you never need to edit.
```

In the **Install** section, change `cp Caddyfile.template Caddyfile` to:

```bash
cp Caddyfile.template Caddyfile        # or Caddyfile.chat.template, with chat
```

Add a new section immediately before `## Modes`:

```markdown
## Cortex Chat (optional)

Chat is **off by default**. To run it, set this in `.env` and `docker compose up -d`:

```dotenv
COMPOSE_PROFILES=chat
```

In domain mode also set `CHAT_DOMAIN` and `CHAT_BASE_URL`, add the chat origin
to `CORS_ALLOWED_ORIGINS`, and use the other Caddy template:

```bash
cp Caddyfile.chat.template Caddyfile
```

To turn chat off again, remove the `COMPOSE_PROFILES` line and run
`docker compose up -d --remove-orphans` — without `--remove-orphans` the
already-running chat container is left behind. Its data stays in the
`chat_data` volume either way.

`CORTEX_CHAT_IMAGE` and `CHAT_APP_ENCRYPTION_KEY` stay set even with chat off:
Compose interpolates variables before it filters profiles, so an unset value
aborts the whole project.
```

In the **Modes** table for localhost, mark the chat row as conditional:

```markdown
| Chat | http://localhost:3001 (only with `COMPOSE_PROFILES=chat`) |
```

In the **Logging in** section, change `Cortex and Chat share one identity` to
`Cortex and Chat share one identity (when chat is installed)`.

- [ ] **Step 4: Verify the documented flow actually works**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex
grep -c "COMPOSE_PROFILES=chat" selfhost/.env.example selfhost/README.md
./selfhost/verify-contract.sh >/dev/null && echo "contract still OK"
```

Expected: at least 1 in each file, and `contract still OK`.

- [ ] **Step 5: Commit**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex
git add selfhost/.env.example selfhost/README.md
git commit -m "docs(selfhost): document chat as an opt-in

.env.example carries a commented COMPOSE_PROFILES=chat with the domain-mode
extras spelled out, and both CORTEX_CHAT_IMAGE and CHAT_APP_ENCRYPTION_KEY
now explain why they stay set with chat off.

The README gains a Cortex Chat section covering both directions, including
that turning chat back off needs --remove-orphans or the running container
is left behind."
```

---

### Task 5: Installer — the chat choice, in the wizard and in `--yes`

**Files:**
- Modify: `cortex-installer/src/stack.ts` (add `CHAT_OPTIONAL_SINCE`, `supportsOptionalChat`)
- Modify: `cortex-installer/src/env.ts` (`InstallConfig.chat`, `domains.chat` optional)
- Modify: `cortex-installer/src/state.ts` (`InstallState.chat`, `domains.chat` optional)
- Modify: `cortex-installer/src/wizard.ts` (prompt; gate `CHAT_DOMAIN` and SMTP; `CORTEX_ENABLE_CHAT`)
- Test: `cortex-installer/test/wizard.test.ts`, `cortex-installer/test/stack.test.ts`

**Interfaces:**
- Consumes: the profile contract from Task 1.
- Produces:
  - `CHAT_OPTIONAL_SINCE: string` and `supportsOptionalChat(stack: Stack): boolean` from `src/stack.ts`
  - `InstallConfig.chat: boolean` and `InstallState.chat: boolean`
  - `InstallConfig["domains"]` / `InstallState["domains"]` become `{ app: string; chat?: string; acmeEmail: string }`
  - Tasks 6–9 all read `cfg.chat` / `state.chat`.

- [ ] **Step 1: Write the failing tests**

In `test/stack.test.ts`, add `supportsOptionalChat` and `CHAT_OPTIONAL_SINCE` to the **existing** `../src/stack.js` import (do not add a second import statement — the file already imports `parseStack, assertInstallerSupported, imageRefs, fetchStack`), then append, reusing the file's existing `GOOD` fixture:

```ts
test("optional chat is gated on the stack release that introduced the profile", () => {
  // A new installer can still install an OLD stack (minInstaller only guards the
  // other direction). On a stack whose compose has no chat profile, omitting
  // COMPOSE_PROFILES would NOT disable chat — it would run anyway while the
  // installer reported it as off. So the question must not be asked there.
  const at = (v: string) => parseStack({ ...GOOD, stack: v });
  assert.equal(supportsOptionalChat(at("1.0.1")), false);
  assert.equal(supportsOptionalChat(at(CHAT_OPTIONAL_SINCE)), true);
  assert.equal(supportsOptionalChat(at("2.0.0")), true);
});
```

Append to `test/wizard.test.ts`:

```ts
test("chat is off unless CORTEX_ENABLE_CHAT is exactly true", () => {
  assert.equal(buildConfigNonInteractive(MINIMAL, stack, "/tmp/c").chat, false);
  for (const v of ["false", "1", "yes", "TRUE", ""]) {
    assert.equal(
      buildConfigNonInteractive({ ...MINIMAL, CORTEX_ENABLE_CHAT: v }, stack, "/tmp/c").chat,
      false,
      `CORTEX_ENABLE_CHAT=${JSON.stringify(v)} must not enable chat`
    );
  }
  assert.equal(
    buildConfigNonInteractive({ ...MINIMAL, CORTEX_ENABLE_CHAT: "true" }, stack, "/tmp/c").chat,
    true
  );
});

test("domain mode without chat needs no chat domain", () => {
  const cfg = buildConfigNonInteractive(
    { ...MINIMAL, CORTEX_MODE: "domain", CORTEX_APP_DOMAIN: "a.example.com", CORTEX_ACME_EMAIL: "o@example.com" },
    stack,
    "/tmp/c"
  );
  assert.equal(cfg.chat, false);
  assert.equal(cfg.domains?.chat, undefined);
});

test("domain mode with chat still requires a chat domain", () => {
  assert.throws(
    () =>
      buildConfigNonInteractive(
        {
          ...MINIMAL,
          CORTEX_MODE: "domain",
          CORTEX_ENABLE_CHAT: "true",
          CORTEX_APP_DOMAIN: "a.example.com",
          CORTEX_ACME_EMAIL: "o@example.com",
        },
        stack,
        "/tmp/c"
      ),
    /CORTEX_CHAT_DOMAIN/
  );
});
```

- [ ] **Step 2: Run them and watch them fail**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm test 2>&1 | tail -20
```

Expected: FAIL — `supportsOptionalChat` is not exported, and `cfg.chat` is `undefined`.

- [ ] **Step 3: Add the stack-support gate**

In `src/stack.ts`, after the `COMPONENTS` constant, add:

```ts
/**
 * First stack release whose compose puts the chat service behind a profile.
 *
 * `minInstaller` stops an OLD installer applying a NEW stack, but not the
 * reverse: this installer can still install an older pinned stack, whose
 * compose runs chat unconditionally. Omitting COMPOSE_PROFILES there would not
 * disable chat — chat would run while we reported it as off — so on such a
 * stack the choice is not offered at all.
 */
export const CHAT_OPTIONAL_SINCE = "1.1.0";

export function supportsOptionalChat(stack: Stack): boolean {
  return semver.valid(stack.stack) !== null && semver.gte(stack.stack, CHAT_OPTIONAL_SINCE);
}
```

- [ ] **Step 4: Widen the types**

In `src/env.ts`, in `InstallConfig`, change the `domains` line and add `chat`:

```ts
  /** Whether Cortex Chat is part of this install. Off by default. */
  chat: boolean;
  ports: { app: number; chat: number; api: number; neo4jHttp: number; neo4jBolt: number };
  /** `chat` is absent when chat is not installed — there is no chat domain then. */
  domains?: { app: string; chat?: string; acmeEmail: string };
```

Make the identical change to `InstallState` in `src/state.ts`, adding:

```ts
  /**
   * Whether chat is installed. Absent on installs that predate the option —
   * those were always running chat, which is what `update` back-fills.
   */
  chat?: boolean;
  domains?: { app: string; chat?: string; acmeEmail: string };
```

`InstallState.chat` is optional on purpose: the migration in Task 9 depends on being able to tell "chose no chat" from "predates the choice".

- [ ] **Step 5: Add the wizard prompt**

In `src/wizard.ts`, add `supportsOptionalChat` to the existing `./stack.js` import. Then, in `runWizard`, immediately after the `if (p.isCancel(mode)) bail("Cancelled.");` line that follows the mode select, insert:

```ts
  /**
   * Cortex Chat is a separate front end and entirely optional — nothing in the
   * backend or frontend references it. Default off: someone who wants a
   * knowledge base should not have to opt out of a second web app.
   *
   * Asked here, before the domain questions, because chat needs its own domain
   * and prompting for one we will never use is worse than not asking.
   */
  let chat = true;
  if (supportsOptionalChat(opts.stack)) {
    const want = await p.confirm({
      message: "Also install Cortex Chat? (a separate chat front end)",
      initialValue: false,
    });
    if (p.isCancel(want)) bail("Cancelled.");
    chat = Boolean(want);
  } else {
    p.log.info(
      `Cortex Chat is always installed on stack ${opts.stack.stack}; it became ` +
        `optional in ${CHAT_OPTIONAL_SINCE}.`
    );
  }
```

Add `CHAT_OPTIONAL_SINCE` to the same import.

- [ ] **Step 6: Gate the chat domain prompt**

In `runWizard`'s `if (mode === "domain")` block, replace the chat-domain prompt and the `domains = ...` assignment. The existing code reads:

```ts
    const chat = await p.text({
      message: "Domain for Cortex Chat",
      placeholder: "chat.example.com",
      validate: (v) => (v && v.includes(".") ? undefined : "Enter a fully-qualified domain"),
    });
    if (p.isCancel(chat)) bail("Cancelled.");
```

Replace it with (note the rename — `chat` is now the boolean):

```ts
    let chatDomain: string | undefined;
    if (chat) {
      const entered = await p.text({
        message: "Domain for Cortex Chat",
        placeholder: "chat.example.com",
        validate: (v) => (v && v.includes(".") ? undefined : "Enter a fully-qualified domain"),
      });
      if (p.isCancel(entered)) bail("Cancelled.");
      chatDomain = String(entered);
    }
```

and change the assignment to:

```ts
    domains = { app: String(app), chat: chatDomain, acmeEmail: String(acmeEmail) };
```

Then fix the DNS pre-check loop just below, which currently iterates both domains — with chat off there is only one:

```ts
    for (const host of [domains.app, domains.chat].filter((h): h is string => Boolean(h))) {
```

- [ ] **Step 7: Gate the SMTP prompt and return the flag**

In the `depth === "advanced"` block, wrap the SMTP question so it is only asked when chat is installed — SMTP configures chat's password-reset mail and nothing else:

```ts
    // SMTP configures chat's password-reset mail and nothing else, so it is
    // only worth asking about when chat is installed.
    if (chat) {
      const wantSmtp = await p.confirm({ message: "Configure SMTP for chat password reset?", initialValue: false });
      if (p.isCancel(wantSmtp)) bail("Cancelled.");
      if (wantSmtp) {
        const host = await p.text({ message: "SMTP host", validate: (v) => (v ? undefined : "Required") });
        if (p.isCancel(host)) bail("Cancelled.");
        const from = await p.text({ message: "From address", validate: (v) => (v?.includes("@") ? undefined : "Required") });
        if (p.isCancel(from)) bail("Cancelled.");
        smtp = { host: String(host), port: 587, secure: false, from: String(from) };
      }
    }
```

Add `chat` to the object `runWizard` returns, next to `mode`:

```ts
    mode,
    chat,
```

- [ ] **Step 8: Wire `--yes`**

In `buildConfigNonInteractive`, after the `mode` validation, add:

```ts
  // Opt-in, and only on an exact "true" — same shape as CORTEX_ERROR_REPORTING.
  // On a stack that predates the profile, chat runs regardless of what we write,
  // so report it as installed rather than claiming it is off.
  const chat = supportsOptionalChat(stack) ? env.CORTEX_ENABLE_CHAT === "true" : true;
```

Find the existing domain-mode branch that reads `CORTEX_CHAT_DOMAIN` via `need(...)` and make it conditional, so `--yes` without chat needs no chat domain:

```ts
    domains = {
      app: need("CORTEX_APP_DOMAIN"),
      chat: chat ? need("CORTEX_CHAT_DOMAIN") : undefined,
      acmeEmail: need("CORTEX_ACME_EMAIL"),
    };
```

Add `chat` to the returned object next to `mode`.

- [ ] **Step 9: Run the tests**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm test 2>&1 | grep -E "^# (tests|pass|fail)"
```

Expected: all pass, `# fail 0`. If `tsc` reports errors about `domains.chat` being possibly `undefined` in files other than the ones above, note them — Tasks 6 and 7 fix those call sites; you may add a narrow non-null assertion only where the surrounding code has already checked `cfg.chat`.

- [ ] **Step 10: Commit**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
git add src/stack.ts src/env.ts src/state.ts src/wizard.ts test/stack.test.ts test/wizard.test.ts
git commit -m "feat: ask whether to install Cortex Chat, defaulting to off

One confirm with initialValue: false, placed before the domain questions
so a chat domain is only requested when chat is actually wanted. It also
suppresses the SMTP question, which configures chat's password-reset mail
and nothing else.

--yes reads CORTEX_ENABLE_CHAT and requires an exact \"true\", matching
CORTEX_ERROR_REPORTING.

Gated on supportsOptionalChat(): minInstaller stops an old installer
applying a new stack, but not the reverse, and on a stack whose compose
predates the profile, omitting COMPOSE_PROFILES would leave chat running
while we reported it off. There the question is not asked at all.

InstallState.chat is deliberately optional — 'chose no chat' and
'predates the choice' must stay distinguishable for the update back-fill."
```

---

### Task 6: Installer — render `.env` for both states

**Files:**
- Modify: `cortex-installer/src/env.ts`
- Test: `cortex-installer/test/env.test.ts`

**Interfaces:**
- Consumes: `InstallConfig.chat` and optional `domains.chat` from Task 5.
- Produces: `.env` content whose only chat switch is the `COMPOSE_PROFILES` line; `REQUIRED_VARS` no longer contains `CHAT_DOMAIN`.

- [ ] **Step 1: Write the failing tests**

Append to `test/env.test.ts`. It already defines `base(over: Partial<InstallConfig>): InstallConfig` and imports `renderEnv` and `REQUIRED_VARS`, so no new imports are needed:

```ts
test("chat off omits COMPOSE_PROFILES but keeps the two vars Compose still reads", () => {
  const env = renderEnv(base({ chat: false }));
  assert.ok(!/^COMPOSE_PROFILES=/m.test(env), "COMPOSE_PROFILES must be absent with chat off");
  // Interpolation runs before profile filtering, so an unset value here aborts
  // the entire compose project even though chat never starts.
  assert.match(env, /^CORTEX_CHAT_IMAGE=ghcr\.io\/mocaos\/cortex-chat:/m);
  assert.match(env, /^CHAT_APP_ENCRYPTION_KEY=.+/m);
  // Retained so enabling chat later is a single line.
  assert.match(env, /^CHAT_PORT=\d+/m);
});

test("chat on writes the profile line", () => {
  assert.match(renderEnv(base({ chat: true })), /^COMPOSE_PROFILES=chat$/m);
});

test("chat off in domain mode writes no chat domain and no chat CORS origin", () => {
  const env = renderEnv(
    base({ chat: false, mode: "domain", domains: { app: "a.example.com", acmeEmail: "o@example.com" } })
  );
  assert.ok(!/^CHAT_DOMAIN=/m.test(env));
  assert.ok(!/^CHAT_BASE_URL=/m.test(env));
  assert.match(env, /^CORS_ALLOWED_ORIGINS=https:\/\/a\.example\.com$/m);
});

test("chat on in domain mode writes both origins", () => {
  const env = renderEnv(
    base({
      chat: true,
      mode: "domain",
      domains: { app: "a.example.com", chat: "c.example.com", acmeEmail: "o@example.com" },
    })
  );
  assert.match(env, /^CHAT_DOMAIN=c\.example\.com$/m);
  assert.match(env, /^CHAT_BASE_URL=https:\/\/c\.example\.com$/m);
  assert.match(env, /^CORS_ALLOWED_ORIGINS=https:\/\/a\.example\.com,https:\/\/c\.example\.com$/m);
});

test("CHAT_DOMAIN is no longer a required var", () => {
  // The compose relaxed it to ${CHAT_DOMAIN:-}; this list must mirror the
  // compose's ${VAR:?} guards or it lies about what a valid .env needs.
  assert.ok(!(REQUIRED_VARS as readonly string[]).includes("CHAT_DOMAIN"));
  assert.ok((REQUIRED_VARS as readonly string[]).includes("CORTEX_CHAT_IMAGE"));
  assert.ok((REQUIRED_VARS as readonly string[]).includes("CHAT_APP_ENCRYPTION_KEY"));
});
```

- [ ] **Step 2: Run them and watch them fail**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm test 2>&1 | tail -20
```

Expected: FAIL on the `COMPOSE_PROFILES` and `CHAT_DOMAIN` assertions.

- [ ] **Step 3: Write the profile line in the Mode section**

In `renderEnv`, in the `section("Mode")` block, after the `COMPOSE_PROJECT_NAME` line:

```ts
  if (cfg.chat) {
    L.push("# Cortex Chat is installed. Remove this line and re-run `up -d --remove-orphans` to drop it.");
    put("COMPOSE_PROFILES", "chat");
  } else {
    L.push(
      "",
      "# Cortex Chat is NOT installed. To add it, uncomment the line below and run",
      "# `npx @mocaos/cortex restart`. CHAT_PORT and CHAT_APP_ENCRYPTION_KEY below",
      "# are already set, so in this mode that is the only change needed.",
      "# COMPOSE_PROFILES=chat"
    );
  }
```

For domain mode the comment must not promise a one-line switch, because it is not one. Use this instead when `cfg.mode === "domain"` and chat is off:

```ts
      "",
      "# Cortex Chat is NOT installed. To add it: set COMPOSE_PROFILES=chat, add",
      "# CHAT_DOMAIN + CHAT_BASE_URL, add the chat origin to CORS_ALLOWED_ORIGINS,",
      "# `cp Caddyfile.chat.template Caddyfile`, then `npx @mocaos/cortex restart`.",
      "# COMPOSE_PROFILES=chat"
```

- [ ] **Step 4: Annotate the image pin**

Above the existing `put("CORTEX_CHAT_IMAGE", img.chat)` line, add:

```ts
  if (!cfg.chat) {
    L.push("# Read by Compose even with chat off: interpolation happens before profile");
    L.push("# filtering, so an unset value here aborts the whole project.");
  }
```

- [ ] **Step 5: Make the domain block conditional**

Replace the three chat lines in the `else` (domain) branch:

```ts
    put("APP_DOMAIN", d.app);
    put("ACME_EMAIL", d.acmeEmail);
    if (cfg.chat && d.chat) {
      put("CHAT_DOMAIN", d.chat);
      put("CHAT_BASE_URL", `https://${d.chat}`);
    }
    put(
      "CORS_ALLOWED_ORIGINS",
      cfg.chat && d.chat ? `https://${d.app},https://${d.chat}` : `https://${d.app}`
    );
```

Also change the comment above them from "Both domains must already have A records" to:

```ts
    L.push(
      cfg.chat
        ? "# Both domains must already have A records pointing at this host."
        : "# This domain must already have an A record pointing at this host."
    );
```

- [ ] **Step 6: Annotate the retained secret**

Above the existing `put("CHAT_APP_ENCRYPTION_KEY", ...)` line, add:

```ts
  if (!cfg.chat) {
    L.push("# Generated even with chat off — Compose reads it regardless (see");
    L.push("# CORTEX_CHAT_IMAGE), and it means enabling chat later needs no new secret.");
  }
```

- [ ] **Step 7: Drop CHAT_DOMAIN from REQUIRED_VARS**

In the `REQUIRED_VARS` array, delete the `"CHAT_DOMAIN",` entry and add above the array:

```ts
/**
 * Every ${VAR:?} the released compose enforces.
 *
 * CHAT_DOMAIN is deliberately absent: the caddy overlay relaxed it to
 * ${CHAT_DOMAIN:-} when chat became optional. CORTEX_CHAT_IMAGE and
 * CHAT_APP_ENCRYPTION_KEY stay, because Compose interpolates them whether or
 * not the chat profile is active.
 */
```

- [ ] **Step 8: Run the tests**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm test 2>&1 | grep -E "^# (tests|pass|fail)"
```

Expected: all pass.

- [ ] **Step 9: Check a rendered file by eye, both ways**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm run build >/dev/null
node -e '
const {renderEnv}=require("./dist/env.js");
const stack={stack:"1.1.0",components:{backend:"1.1.0",frontend:"1.1.0",chat:"1.0.0",neo4j:"5.26-community",caddy:"2-alpine"},minInstaller:"1.2.0"};
const base={mode:"localhost",dir:"/tmp/c",projectName:"cortex",stack,
 secrets:{adminPassword:"a",neo4jPassword:"b",adminApiKey:"c",sessionSecret:"d".repeat(32),chatEncryptionKey:"e"},
 adminEmail:"me@example.com",
 llm:{providerId:"openai",baseUrl:"https://api.openai.com/v1",apiKey:"k",chatModel:"m",embeddingModel:"e",embeddingDimension:1536,embeddingSendDimensions:true},
 ports:{app:3000,chat:3001,api:8000,neo4jHttp:7474,neo4jBolt:7687},errorReporting:false};
for (const chat of [false,true]) {
  const out=renderEnv({...base,chat});
  console.log("=== chat="+chat+" ===");
  console.log(out.split("\n").filter(l=>/CHAT|PROFILES/.test(l)).join("\n"));
}'
```

Expected: with `chat=false`, a commented `# COMPOSE_PROFILES=chat`, a set `CORTEX_CHAT_IMAGE`, `CHAT_PORT` and `CHAT_APP_ENCRYPTION_KEY`, and the explanatory comments. With `chat=true`, an uncommented `COMPOSE_PROFILES=chat`.

- [ ] **Step 10: Commit**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
git add src/env.ts test/env.test.ts
git commit -m "feat(env): render .env for chat on and off

COMPOSE_PROFILES=chat is written only when chat is installed; with chat off
the same line is emitted commented out, above a note on exactly what
turning it on requires — one line in localhost mode, four in domain mode,
and the comment says which.

CORTEX_CHAT_IMAGE, CHAT_APP_ENCRYPTION_KEY and CHAT_PORT stay written with
chat off. The first two are not optional: Compose interpolates before it
filters profiles, so an unset value aborts the project. Both carry a
comment saying so, because 'chat is off, delete the chat vars' is the
obvious and wrong cleanup.

Domain mode with chat off writes no CHAT_DOMAIN or CHAT_BASE_URL and drops
the chat origin from CORS_ALLOWED_ORIGINS. CHAT_DOMAIN leaves REQUIRED_VARS
to mirror the compose relaxing it to \${CHAT_DOMAIN:-}."
```

---

### Task 7: Installer — stop waiting for a service that will never start

**Files:**
- Modify: `cortex-installer/src/docker.ts` (`healthServices`)
- Modify: `cortex-installer/src/state.ts` (add `chatEnabledFor`)
- Modify: `cortex-installer/src/commands/install.ts` (call site, ports, URL box)
- Modify: `cortex-installer/src/commands/start.ts` (call site)
- Modify: `cortex-installer/src/commands/update.ts` (call site)
- Test: `cortex-installer/test/docker.test.ts`, `cortex-installer/test/state.test.ts`

**Interfaces:**
- Consumes: `cfg.chat` / `state.chat` from Task 5.
- Produces: `healthServices(mode: "localhost" | "domain", chat: boolean): string[]` from `src/docker.ts`, and `chatEnabledFor(state: { chat?: boolean }): boolean` from `src/state.ts`. Task 9 reuses both.

This is the failure that hurts most if missed: `waitHealthy` polls for 300 seconds for a container that Compose was never asked to create, then reports a false failure on a healthy stack.

- [ ] **Step 1: Write the failing test**

Append to `test/docker.test.ts`. It already imports `healthServices` from `../src/docker.js`, so add no import:

```ts
test("healthServices omits chat when chat is not installed", () => {
  // waitHealthy polls until every named service appears. Naming a service that
  // Compose was never asked to create means a 300s spin ending in a false
  // failure on a perfectly healthy stack.
  assert.deepEqual(healthServices("localhost", false), ["neo4j", "backend", "frontend"]);
  assert.deepEqual(healthServices("localhost", true), ["neo4j", "backend", "frontend", "chat"]);
  assert.deepEqual(healthServices("domain", false), ["neo4j", "backend", "frontend", "caddy"]);
  assert.deepEqual(healthServices("domain", true), ["neo4j", "backend", "frontend", "chat", "caddy"]);
});
```

Append to `test/state.test.ts` (add `chatEnabledFor` to its existing `../src/state.js` import):

```ts
test("an install predating the chat option counts as chat-enabled", () => {
  // This distinction is the whole migration. `undefined` means the install was
  // made before the choice existed, and every such install runs chat; `false`
  // means someone deliberately declined it. Collapsing them would either drop a
  // running service or resurrect a declined one.
  assert.equal(chatEnabledFor({}), true);
  assert.equal(chatEnabledFor({ chat: undefined }), true);
  assert.equal(chatEnabledFor({ chat: true }), true);
  assert.equal(chatEnabledFor({ chat: false }), false);
});
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm test 2>&1 | tail -15
```

Expected: FAIL — `healthServices` takes one argument and always includes `chat`.

- [ ] **Step 3: Change the signature**

In `src/docker.ts`, replace the body of `healthServices`:

```ts
export function healthServices(mode: "localhost" | "domain", chat: boolean): string[] {
  const services = ["neo4j", "backend", "frontend"];
  // Only name services Compose was actually asked to create: waitHealthy polls
  // until each one appears, so an uninstalled chat means a full-timeout spin
  // ending in a false failure.
  if (chat) services.push("chat");
  if (mode === "domain") services.push("caddy");
  return services;
}
```

- [ ] **Step 4: Add `chatEnabledFor` to state.ts**

`InstallState.chat` is optional, and what its absence means is a migration rule
rather than a default — so it belongs next to the type, named, once:

```ts
/**
 * Whether chat is part of an existing install.
 *
 * An absent `chat` field means the install predates the option, and every such
 * install was running chat — so absent reads as enabled. Only an explicit
 * `false` means someone declined it. Treating absent as disabled would silently
 * drop a running service on the next update.
 */
export function chatEnabledFor(state: { chat?: boolean }): boolean {
  return state.chat !== false;
}
```

- [ ] **Step 5: Update the three call sites**

`src/commands/install.ts`:

```ts
  const healthy = await waitHealthy(dir, healthServices(cfg.mode, cfg.chat), 300_000, (st) => {
```

`src/commands/start.ts`:

```ts
  // state.mode decides whether caddy is part of the stack, chatEnabledFor
  // whether chat is — see healthServices.
  const ok = await waitHealthy(dir, healthServices(state.mode, chatEnabledFor(state)));
```

`src/commands/update.ts` — same expression for now; Task 9 replaces it with the
value it back-fills:

```ts
  const ok = await waitHealthy(dir, healthServices(state.mode, chatEnabledFor(state)));
```

Import `chatEnabledFor` from `../state.js` in both files.

- [ ] **Step 6: Fix the ports preflight**

In `src/commands/install.ts`, replace the `portsToCheck` assignment:

```ts
      // Omit the chat port when chat is not installed: nothing will bind it, and
      // under --yes an occupied port is fatal, so checking it could abort an
      // install over a conflict that cannot occur.
      const localhostPorts = cfg.chat
        ? Object.values(cfg.ports)
        : [cfg.ports.app, cfg.ports.api, cfg.ports.neo4jHttp, cfg.ports.neo4jBolt];
      const portsToCheck = cfg.mode === "domain" ? [80, 443] : localhostPorts;
```

- [ ] **Step 7: Fix the closing URL box**

In `src/commands/install.ts`, replace the `urls` assignment:

```ts
    const urls =
      cfg.mode === "localhost"
        ? [
            `Cortex   http://localhost:${cfg.ports.app}`,
            ...(cfg.chat ? [`Chat     http://localhost:${cfg.ports.chat}`] : []),
          ]
        : [
            `Cortex   https://${cfg.domains!.app}`,
            ...(cfg.chat && cfg.domains!.chat ? [`Chat     https://${cfg.domains!.chat}`] : []),
          ];
```

- [ ] **Step 8: Persist the choice**

Find the `writeState(` call in `src/commands/install.ts` and add `chat: cfg.chat,` to the object it writes, next to `mode`.

- [ ] **Step 9: Run the tests**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm test 2>&1 | grep -E "^# (tests|pass|fail)"
```

Expected: all pass, and `tsc` clean — the typecheck is what proves all three call sites were updated.

- [ ] **Step 10: Commit**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
git add src/docker.ts src/state.ts src/commands/install.ts src/commands/start.ts src/commands/update.ts test/docker.test.ts test/state.test.ts
git commit -m "fix: do not wait on, or reserve a port for, an uninstalled chat

healthServices takes chat explicitly. Naming a service Compose was never
asked to create makes waitHealthy poll for the full 300s and then report a
false failure on a healthy stack — the worst available outcome for someone
whose first install just worked.

Also drops the chat port from the preflight (under --yes an occupied port
is fatal, so checking one nothing will bind could abort an install over an
impossible conflict) and drops the Chat line from the closing URL box.

start and update pass state.chat !== false, so an install predating the
option is treated as chat-enabled, which it was."
```

---

### Task 8: Installer — copy the matching Caddyfile template

**Files:**
- Modify: `cortex-installer/src/artifacts.ts`
- Modify: `cortex-installer/src/commands/install.ts` (the `fetchArtifacts` call)
- Test: `cortex-installer/test/artifacts.test.ts`

**Interfaces:**
- Consumes: both template filenames from Task 2; `cfg.chat` from Task 5.
- Produces: `fetchArtifacts(opts: { version: string; dir: string; chat: boolean }): Promise<void>`. Task 9's `update` call passes the back-filled value.

- [ ] **Step 1: Write the failing test**

Append to `test/artifacts.test.ts`. It already imports `ARTIFACT_FILES`, so add no import:

```ts
test("both Caddyfile templates are required artifacts", () => {
  // A release missing one must fail loudly during the fetch, not later as Caddy
  // crash-looping on a bind mount Docker turned into a directory.
  const files = ARTIFACT_FILES as readonly string[];
  assert.ok(files.includes("Caddyfile.template"));
  assert.ok(files.includes("Caddyfile.chat.template"));
});
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm test 2>&1 | tail -12
```

Expected: FAIL — `Caddyfile.chat.template` is not in the list.

- [ ] **Step 3: Add the template to ARTIFACT_FILES**

In `src/artifacts.ts`:

```ts
export const ARTIFACT_FILES = [
  "docker-compose.yml",
  "docker-compose.ports.yml",
  "docker-compose.caddy.yml",
  "Caddyfile.template",
  "Caddyfile.chat.template",
  ".env.example",
] as const;
```

- [ ] **Step 4: Copy the right one**

Change the signature to accept the flag:

```ts
export async function fetchArtifacts(opts: {
  version: string;
  dir: string;
  chat: boolean;
}): Promise<void> {
```

and replace the unconditional copy with:

```ts
    // The caddy overlay bind-mounts ./Caddyfile. If it is missing Docker
    // creates a root-owned DIRECTORY with that name and Caddy crash-loops on
    // "is a directory" with nothing pointing at the cause.
    //
    // Which template depends on the install: the app-only one omits the chat
    // site block, because a block whose {$CHAT_DOMAIN} is unset makes Caddy
    // refuse to adapt the entire config rather than merely warn.
    cpSync(
      join(opts.dir, opts.chat ? "Caddyfile.chat.template" : "Caddyfile.template"),
      join(opts.dir, "Caddyfile")
    );
```

- [ ] **Step 5: Update the install call site**

In `src/commands/install.ts`, find the `fetchArtifacts({ version: ..., dir })` call and add `chat: cfg.chat`.

- [ ] **Step 6: Run the tests**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm test 2>&1 | grep -E "^# (tests|pass|fail)"
```

Expected: all pass. `tsc` will flag the `update.ts` call site as missing `chat` — leave it; Task 9 owns that call. If you need a green typecheck to commit, pass `chat: state.chat !== false` there now and Task 9 will refine it.

- [ ] **Step 7: Commit**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
git add src/artifacts.ts src/commands/install.ts test/artifacts.test.ts
git commit -m "feat(artifacts): copy the Caddyfile template matching the install

fetchArtifacts takes chat and copies Caddyfile.chat.template or
Caddyfile.template accordingly. Both are now required artifacts, so a
release missing one fails during the fetch rather than as Caddy
crash-looping on a bind mount Docker turned into a directory."
```

---

### Task 9: Installer — never let an update silently drop a running chat

**Files:**
- Modify: `cortex-installer/src/update.ts` (add `ensureChatProfile`)
- Modify: `cortex-installer/src/commands/update.ts`
- Test: `cortex-installer/test/update.test.ts`

**Interfaces:**
- Consumes: `InstallState.chat` (optional) from Task 5; `chatEnabledFor` and `healthServices(mode, chat)` from Task 7; `fetchArtifacts({chat})` from Task 8.
- Produces: `ensureChatProfile(envText: string, enabled: boolean): string`.

Every install that exists today runs chat. Applying the new stack without care leaves `COMPOSE_PROFILES` absent, and chat vanishes on the next `update` — data intact in `chat_data`, service gone, nothing said. An absent `state.chat` means "predates the option", which means chat *was* running.

- [ ] **Step 1: Write the failing tests**

In `test/update.test.ts`, add `ensureChatProfile` and `chatEnabledFor` to the **existing** `../src/update.js` import (the file already imports `diffComponents, rewriteImagePins`), then append:

```ts
test("ensureChatProfile adds the profile line when it is absent", () => {
  const out = ensureChatProfile("COMPOSE_FILE=a.yml\nCOMPOSE_PROJECT_NAME=cortex\n", true);
  assert.match(out, /^COMPOSE_PROFILES=chat$/m);
});

test("ensureChatProfile uncomments the commented form rather than duplicating it", () => {
  const out = ensureChatProfile("# COMPOSE_PROFILES=chat\nCOMPOSE_FILE=a.yml\n", true);
  assert.match(out, /^COMPOSE_PROFILES=chat$/m);
  assert.equal(out.match(/COMPOSE_PROFILES=chat/g)?.length, 1);
});

test("ensureChatProfile is idempotent", () => {
  const once = ensureChatProfile("COMPOSE_PROFILES=chat\n", true);
  assert.equal(ensureChatProfile(once, true), once);
});

test("ensureChatProfile comments the line out when disabling", () => {
  const out = ensureChatProfile("COMPOSE_PROFILES=chat\nCOMPOSE_FILE=a.yml\n", false);
  assert.ok(!/^COMPOSE_PROFILES=chat$/m.test(out));
  assert.match(out, /^# COMPOSE_PROFILES=chat$/m);
});

test("disabling when no profile line exists is a byte-for-byte no-op", () => {
  // This function rewrites the file holding the only copy of NEO4J_PASSWORD, so
  // it must never touch a line it was not asked to change.
  const input = "NEO4J_PASSWORD=s3cret\nCOMPOSE_FILE=a.yml\nOPENAI_API_KEY=k\n";
  assert.equal(ensureChatProfile(input, false), input);
});

test("enabling changes exactly one line and preserves the rest", () => {
  const input = "NEO4J_PASSWORD=s3cret\n# COMPOSE_PROFILES=chat\nOPENAI_API_KEY=k\n";
  const out = ensureChatProfile(input, true);
  assert.equal(out.split("\n").length, input.split("\n").length, "line count must not change");
  assert.match(out, /^NEO4J_PASSWORD=s3cret$/m);
  assert.match(out, /^OPENAI_API_KEY=k$/m);
  assert.match(out, /^COMPOSE_PROFILES=chat$/m);
});```

- [ ] **Step 2: Run them and watch them fail**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm test 2>&1 | tail -15
```

Expected: FAIL — `ensureChatProfile` is not exported.

- [ ] **Step 3: Implement it**

Add to `src/update.ts`:

```ts
/**
 * Sets or clears `COMPOSE_PROFILES=chat` in .env text, leaving every other line
 * byte-identical.
 *
 * Enabling prefers uncommenting the commented form the installer writes, so the
 * surrounding explanatory comment stays where it is and the line does not appear
 * twice. Disabling comments the line rather than deleting it, so the operator can
 * see what was turned off.
 */
export function ensureChatProfile(envText: string, enabled: boolean): string {
  const lines = envText.split("\n");
  const active = lines.findIndex((l) => /^\s*COMPOSE_PROFILES\s*=/.test(l));
  const commented = lines.findIndex((l) => /^\s*#\s*COMPOSE_PROFILES\s*=\s*chat\s*$/.test(l));

  if (enabled) {
    if (active !== -1) {
      lines[active] = "COMPOSE_PROFILES=chat";
    } else if (commented !== -1) {
      lines[commented] = "COMPOSE_PROFILES=chat";
    } else {
      // Prepend rather than append: COMPOSE_* belongs with the mode block, and a
      // trailing line after the secrets reads like an afterthought.
      lines.unshift("COMPOSE_PROFILES=chat");
    }
    return lines.join("\n");
  }

  if (active !== -1) lines[active] = "# COMPOSE_PROFILES=chat";
  return lines.join("\n");
}
```

- [ ] **Step 4: Run the tests**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm test 2>&1 | grep -E "^# (tests|pass|fail)"
```

Expected: all pass.

- [ ] **Step 5: Apply the back-fill in the update flow**

In `src/commands/update.ts`, immediately before the `fetchArtifacts` call, add:

```ts
  /**
   * Every install that predates the chat option was running chat, so an absent
   * state.chat means enabled. Without this, applying a stack whose compose puts
   * chat behind a profile would silently drop a running service: the data would
   * survive in chat_data, the container would not, and nothing would say why.
   */
  const chat = chatEnabledFor(state);
  if (state.chat === undefined) {
    p.log.info("Cortex Chat stays installed. It is optional for new installs from this release on.");
  }
```

Change the `fetchArtifacts` call to pass it:

```ts
  await fetchArtifacts({ version: latest.stack, dir, chat });
```

Change the `.env` rewrite so the profile is written in the same atomic write as the image repin — two separate writes to this file would double the window in which a crash loses `NEO4J_PASSWORD`:

```ts
  const envPath = join(dir, ".env");
  writeEnvFile(
    envPath,
    ensureChatProfile(rewriteImagePins(readFileSync(envPath, "utf8"), latest.components), chat)
  );
```

Change the health wait to use the same value:

```ts
  const ok = await waitHealthy(dir, healthServices(state.mode, chat));
```

And record it, so the next update no longer has to infer it. In the `writeState` call add:

```ts
    chat,
```

Add `ensureChatProfile` to the existing `../update.js` import and `chatEnabledFor` to the `../state.js` one.

- [ ] **Step 6: Verify the back-fill end to end on a fake install directory**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm run build >/dev/null
node -e '
const {ensureChatProfile}=require("./dist/update.js");
// A pre-migration .env: no COMPOSE_PROFILES line at all.
const before="COMPOSE_FILE=docker-compose.yml:docker-compose.ports.yml\nNEO4J_PASSWORD=keepme\n";
const after=ensureChatProfile(before,true);
console.log(JSON.stringify(after));
console.log("password preserved:", after.includes("NEO4J_PASSWORD=keepme"));
console.log("profile enabled:", /^COMPOSE_PROFILES=chat$/m.test(after));
'
```

Expected: `password preserved: true` and `profile enabled: true`.

- [ ] **Step 7: Commit**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
git add src/update.ts src/commands/update.ts test/update.test.ts
git commit -m "fix(update): never silently drop a chat that was already running

Every install that predates the chat option runs chat. Applying a stack
whose compose puts chat behind a profile leaves COMPOSE_PROFILES absent, so
without this the service would vanish on update — data intact in chat_data,
container gone, nothing said.

An absent state.chat therefore means enabled, and update back-fills both
the .env line and the state field, telling the operator once that chat
stays installed and is now optional for new installs.

ensureChatProfile folds into the same atomic write as the image repin
rather than adding a second write: that file holds the only copy of
NEO4J_PASSWORD, and every extra truncating write widens the window where a
crash makes the graph permanently unauthenticatable."
```

---

### Task 10: Installer — document and release 1.2.0

**Files:**
- Modify: `cortex-installer/README.md`
- Modify: `cortex-installer/package.json`

**Interfaces:**
- Consumes: Tasks 5–9.
- Produces: `@mocaos/cortex@1.2.0` on npm — the version Task 11 sets as `minInstaller`.

- [ ] **Step 1: Document the flag in the README**

In the `--yes` section, after the block listing `CORTEX_MODE=domain` and the secret overrides, add:

```markdown
Cortex Chat is **not** installed by default. Add it with:

```bash
CORTEX_ENABLE_CHAT=true
```

In domain mode that also makes `CORTEX_CHAT_DOMAIN` required. The interactive
wizard asks the same question, defaulting to No.

To add or remove chat after installing, edit `.env` — set or comment out
`COMPOSE_PROFILES=chat` — and run `npx @mocaos/cortex restart`. In localhost
mode that is the only change needed; the chat port and encryption key are
already written. In domain mode you also need `CHAT_DOMAIN`, `CHAT_BASE_URL`,
the chat origin in `CORS_ALLOWED_ORIGINS`, and
`cp Caddyfile.chat.template Caddyfile`.
```

- [ ] **Step 2: Verify the whole suite and typecheck are clean**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm run typecheck && npm test 2>&1 | grep -E "^# (tests|pass|fail)"
```

Expected: typecheck silent, `# fail 0`.

- [ ] **Step 3: Bump and verify the version gate**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
npm version 1.2.0 --no-git-tag-version >/dev/null
node scripts/check-version-sync.mjs --tag v1.2.0
npm run build >/dev/null && node dist/cli.js --version
```

Expected: `Version 1.2.0 OK` and `1.2.0`.

- [ ] **Step 4: Commit, tag, push**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex-installer
git add README.md package.json package-lock.json
git commit -m "feat: Cortex Chat is now opt-in (1.2.0)

Documents CORTEX_ENABLE_CHAT for --yes and the .env switch for changing
your mind afterwards, in both modes."
git push origin main
git tag -a v1.2.0 -m "v1.2.0 — optional Cortex Chat"
git push origin v1.2.0
```

- [ ] **Step 5: Verify the release published**

```bash
gh run list --repo mocaOS/cortex-installer --limit 2 \
  --json workflowName,status,conclusion --jq '.[]|"\(.workflowName) \(.status)/\(.conclusion // "-")"'
curl -s https://registry.npmjs.org/@mocaos%2fcortex \
  | python3 -c 'import json,sys;print("latest:",json.load(sys.stdin)["dist-tags"]["latest"])'
```

Expected: Release `completed/success`, `latest: 1.2.0`. The registry can lag a minute; retry rather than assuming failure. Note that `npx @mocaos/cortex` will not resolve a release this fresh on a machine with `min-release-age` set — use `npx --min-release-age=0 @mocaos/cortex@1.2.0` to smoke-test it.

---

### Task 11: cortex-app — documentation sweep, `minInstaller`, release 1.1.0

**Files:**
- Modify: `cortex-app/selfhost/stack.template.json`
- Modify: `cortex-app/handbook/26-self-hosting.md`
- Modify: `cortex-app/documentation/pages/guides/deployment.mdx`
- Modify: `cortex-app/README.md`
- Modify: `cortex-app/documentation/pages/changelog.mdx`
- Modify: `cortex-app/.claude/development.md`
- Modify: `cortex-app/package.json`, `cortex-app/frontend/package.json`, `cortex-app/selfhost/.env.example`, `cortex-app/backend/app/main.py`

**Interfaces:**
- Consumes: `@mocaos/cortex@1.2.0` from Task 10.
- Produces: stack 1.1.0 with `minInstaller: 1.2.0`.

- [ ] **Step 1: Raise minInstaller**

In `selfhost/stack.template.json`, set `"minInstaller": "1.2.0"` and extend the `_comment` with:

```
1.2.0 is required because the chat service moved behind a Compose profile: an
older installer never writes COMPOSE_PROFILES, so it would install a stack with
chat silently missing and then wait for it to become healthy until it timed out.
```

- [ ] **Step 2: Correct the handbook**

In `handbook/26-self-hosting.md`:

- Change `Both produce the identical stack: Cortex, Cortex Chat, Neo4j, a nightly backup sidecar, and — in public-domain mode — Caddy for automatic HTTPS.` to `Both produce the identical stack: Cortex, Neo4j, a nightly backup sidecar, optionally Cortex Chat, and — in public-domain mode — Caddy for automatic HTTPS.`
- In the numbered wizard sequence, insert a new step after the mode step (currently 3): `**Asks whether to install Cortex Chat**, defaulting to no. Chat is a separate front end and nothing in Cortex needs it, so it is opt-in. Saying yes is what makes the wizard ask for a chat domain in domain mode, and for SMTP in Advanced. Renumber the steps that follow.`
- In the `--yes` section, add `CORTEX_ENABLE_CHAT=true` to the prose listing optional variables, noting it makes `CORTEX_CHAT_DOMAIN` required in domain mode.
- Add a short subsection after the install sequence:

```markdown
### Adding or removing Cortex Chat later

Set or comment out `COMPOSE_PROFILES=chat` in `.env` and run `npx @mocaos/cortex restart`. In localhost mode that's the whole change — the chat port and encryption key are written either way, precisely so this is one line. In domain mode you also need `CHAT_DOMAIN`, `CHAT_BASE_URL`, the chat origin added to `CORS_ALLOWED_ORIGINS`, and `cp Caddyfile.chat.template Caddyfile`.

Chat's data lives in the `chat_data` volume and survives being turned off, so this is reversible in both directions. When turning it off, `docker compose up -d --remove-orphans` (which `restart` does for you) is what actually removes the container.
```

- [ ] **Step 3: Correct the deployment guide**

In `documentation/pages/guides/deployment.mdx`:

- In the installer section's opening sentence, change `brings up the whole stack — Cortex, Cortex Chat, Neo4j, a nightly backup sidecar, and Caddy…` to `brings up the whole stack — Cortex, Neo4j, a nightly backup sidecar, and Caddy for automatic HTTPS if you deploy on a domain — from prebuilt, version-pinned images. Cortex Chat is offered as an opt-in extra, off by default.`
- In step 3 of the numbered list, add that it asks whether to install Cortex Chat, default no.
- In the manual path, change `The stack runs Cortex **and Cortex Chat**. Both share one identity…` to `The stack runs Cortex. Cortex Chat is optional — set \`COMPOSE_PROFILES=chat\` in \`.env\` to include it. With chat installed the two share one identity — \`ADMIN_EMAIL\` + \`ADMIN_PASSWORD\` — and Chat mints its own scoped backend keys using \`ADMIN_API_KEY\`.`
- In the domain-mode `.env` example, mark the chat lines as chat-only:

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.caddy.yml
APP_DOMAIN=cortex.example.com
ACME_EMAIL=you@example.com
# with chat only:
COMPOSE_PROFILES=chat
CHAT_DOMAIN=chat.example.com
CHAT_BASE_URL=https://chat.example.com
CORS_ALLOWED_ORIGINS=https://cortex.example.com,https://chat.example.com
```

- At line 77, change `cp Caddyfile.template Caddyfile` to `cp Caddyfile.template Caddyfile        # or Caddyfile.chat.template, with chat`.
- Replace the sentence at line 136 that reads `the release ships \`.env.example\` and \`Caddyfile.template\` and carries no override file. If you never hand-edited \`Caddyfile\`, re-copy it from the refreshed template so template changes land too.` with:

```markdown
the release ships `.env.example` and both Caddyfile templates and carries no override file. If you never hand-edited `Caddyfile`, re-copy it from the refreshed template so template changes land too — `Caddyfile.template` without chat, `Caddyfile.chat.template` with it.
```

- [ ] **Step 4: Correct the README**

In `README.md`, in the Quick Start `### Install (recommended)` paragraph, add a sentence: `Cortex Chat — a separate chat front end — is offered as an opt-in during setup and is off by default.` Change the `### Either way` note `The installer prints these when it finishes…` to mention that the Chat URL appears only if you chose to install it.

- [ ] **Step 5: Add a changelog section**

`documentation/pages/changelog.mdx` already has a `## July 29, 2026` heading — append a section inside it (do **not** add a second `##` for the same date):

```markdown
### Cortex Chat is now optional

- **Off by default.** The installer asks whether to include Cortex Chat and defaults to no. Nothing in Cortex references it, so a knowledge base no longer arrives with a second web app attached. `--yes` opts in with `CORTEX_ENABLE_CHAT=true`.
- **One line to change your mind.** The chat service sits behind a Compose profile, so `COMPOSE_PROFILES=chat` in `.env` plus a restart is the whole switch in localhost mode — the chat port and encryption key are written either way for exactly this reason. Domain mode additionally needs a chat domain and the `Caddyfile.chat.template` variant, because a Caddy site block with an unset address makes Caddy refuse to load at all.
- **Existing installs keep their chat.** An install that predates the option was running chat, so `update` treats it as enabled and writes the profile line for you. `minInstaller` rises to 1.2.0 so an older installer cannot apply this stack and silently drop the service.
- Requires installer **1.2.0**: `npx @mocaos/cortex`.
```

- [ ] **Step 6: Note the mechanism for maintainers**

In `.claude/development.md`, in the installer section's list of things that bite, add:

```markdown
- The `chat` service sits behind `profiles: ["chat"]`. Two non-obvious
  consequences: Compose interpolates `${VAR:?}` **before** filtering profiles,
  so `CORTEX_CHAT_IMAGE` and `CHAT_APP_ENCRYPTION_KEY` must stay set in `.env`
  even with chat off; and anything with `depends_on: chat` needs the long form
  with `required: false` or Compose rejects the whole project. Both are asserted
  by `selfhost/verify-contract.sh`, which CI runs.
```

- [ ] **Step 7: Bump all four version locations and verify**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex
npm version 1.1.0 --no-git-tag-version >/dev/null
node -e 'const fs=require("fs"),p="frontend/package.json",j=JSON.parse(fs.readFileSync(p,"utf8"));j.version="1.1.0";fs.writeFileSync(p,JSON.stringify(j,null,2)+"\n")'
perl -pi -e 's{(CORTEX_BACKEND_IMAGE=ghcr\.io/mocaos/cortex-backend:)[\d.]+}{${1}1.1.0};
             s{(CORTEX_FRONTEND_IMAGE=ghcr\.io/mocaos/cortex-frontend:)[\d.]+}{${1}1.1.0}' selfhost/.env.example
perl -pi -e 's{^CORTEX_VERSION = "[\d.]+"}{CORTEX_VERSION = "1.1.0"}' backend/app/main.py
node scripts/check-version-sync.mjs --tag v1.1.0
node --test scripts/*.test.mjs 2>&1 | grep -E "^# (tests|pass|fail)"
./selfhost/verify-contract.sh
```

Expected: `Versions in sync.`, all script tests passing, and `selfhost contract: OK`.

- [ ] **Step 8: Commit, tag, push**

```bash
cd /Volumes/WD_BLACK/PROJECTS/CORTEX/cortex
git add -A
git commit -m "feat(selfhost): Cortex Chat becomes optional (1.1.0)

Raises minInstaller to 1.2.0 — an older installer never writes
COMPOSE_PROFILES, so it would install this stack with chat silently absent
and then wait for it to become healthy until it timed out.

Documentation caught up everywhere it claimed the stack includes chat:
handbook 26, the deployment guide, both READMEs, and a changelog section
under the existing July 29 heading."
git push origin main
git tag -a v1.1.0 -m "v1.1.0 — optional Cortex Chat"
git push origin v1.1.0
```

- [ ] **Step 9: Verify the release and its manifest**

```bash
gh run watch "$(gh run list --repo mocaOS/cortex-app --workflow release.yml --limit 1 --json databaseId --jq '.[0].databaseId')" --repo mocaOS/cortex-app --exit-status
gh release download v1.1.0 --repo mocaOS/cortex-app --pattern stack.json --output -
```

Expected: the run succeeds and `stack.json` shows `"stack":"1.1.0"`, backend/frontend `1.1.0`, `chat` still `1.0.0`, and `"minInstaller":"1.2.0"`.

---

### Task 12: Live verification of all four combinations, plus the migration

**Files:** none — this task only runs things.

**Interfaces:**
- Consumes: the published installer 1.2.0 and stack 1.1.0.

Read this first: **never** run `docker volume rm` against a volume named `cortex_*`. Those are the maintainer's own development data. Test installs use their own project name, and only that prefix may be removed.

- [ ] **Step 1: Install with chat off (the new default) and confirm it is genuinely absent**

```bash
cd /tmp && rm -rf chatoff && mkdir chatoff && cd chatoff
CORTEX_ADMIN_EMAIL=qa@example.invalid \
CORTEX_OPENAI_API_KEY="$OPENAI_API_KEY" CORTEX_OPENAI_API_BASE="$OPENAI_API_BASE" \
CORTEX_OPENAI_MODEL=openai/gpt-4o-mini CORTEX_EMBEDDING_MODEL=text-embedding-3-small \
CORTEX_EMBEDDING_DIMENSION=1536 CORTEX_PROJECT_NAME=cortex-chatoff \
npx --min-release-age=0 @mocaos/cortex@1.2.0 --dir /tmp/chatoff/cortex --yes --no-color install
```

Then:

```bash
cd /tmp/chatoff/cortex
docker compose ps --format '{{.Service}}' | sort | tr '\n' ' '   # expect: backend backup frontend neo4j
grep -c '^COMPOSE_PROFILES' .env                                 # expect: 0
grep -c '^CORTEX_CHAT_IMAGE\|^CHAT_APP_ENCRYPTION_KEY' .env      # expect: 2
python3 -c "import json;print('state chat:',json.load(open('cortex.json'))['chat'])"  # expect: False
curl -s -o /dev/null -w 'health %{http_code}\n' http://localhost:8000/health
curl -s -o /dev/null -w 'chat port %{http_code}\n' http://localhost:3001 || echo "chat port refused (correct)"
```

The install must reach "Cortex is running" without a 300-second health stall, and print no Chat URL.

- [ ] **Step 2: Turn chat on the documented way and confirm it appears**

```bash
cd /tmp/chatoff/cortex
printf 'COMPOSE_PROFILES=chat\n' >> .env
npx --min-release-age=0 @mocaos/cortex@1.2.0 --dir /tmp/chatoff/cortex restart
docker compose ps --format '{{.Service}}' | sort | tr '\n' ' '   # expect chat present
curl -s -o /dev/null -w 'chat %{http_code}\n' http://localhost:3001   # expect 200 or 307
```

- [ ] **Step 3: Tear it down safely**

```bash
cd /tmp/chatoff/cortex
npx --min-release-age=0 @mocaos/cortex@1.2.0 --dir /tmp/chatoff/cortex uninstall < /dev/null
docker volume ls --format '{{.Name}}' | grep '^cortex-chatoff_' | xargs -r -n1 docker volume rm
docker volume ls --format '{{.Name}}' | grep -c '^cortex_'   # must still be 9 — never remove these
cd /tmp && rm -rf chatoff
```

- [ ] **Step 4: Install with chat on and confirm six services**

Repeat Step 1 with `CORTEX_ENABLE_CHAT=true` and `CORTEX_PROJECT_NAME=cortex-chaton` into `/tmp/chaton/cortex`, then:

```bash
cd /tmp/chaton/cortex
docker compose ps --format '{{.Service}}' | sort | tr '\n' ' '   # expect: backend backup chat frontend neo4j
grep '^COMPOSE_PROFILES' .env                                    # expect: COMPOSE_PROFILES=chat
curl -s -o /dev/null -w 'chat %{http_code}\n' http://localhost:3001
```

Tear down as in Step 3, substituting the `cortex-chaton_` prefix.

- [ ] **Step 5: Verify the migration — a pre-option install must keep its chat**

This is the highest-risk path in the whole change. Install the **previous** stack with the **previous** installer, then update.

```bash
cd /tmp && rm -rf mig && mkdir mig && cd mig
CORTEX_ADMIN_EMAIL=qa@example.invalid \
CORTEX_OPENAI_API_KEY="$OPENAI_API_KEY" CORTEX_OPENAI_API_BASE="$OPENAI_API_BASE" \
CORTEX_OPENAI_MODEL=openai/gpt-4o-mini CORTEX_EMBEDDING_MODEL=text-embedding-3-small \
CORTEX_EMBEDDING_DIMENSION=1536 CORTEX_PROJECT_NAME=cortex-mig \
npx --min-release-age=0 @mocaos/cortex@1.1.0 --dir /tmp/mig/cortex --yes --no-color install

cd /tmp/mig/cortex
docker compose ps --format '{{.Service}}' | grep -c '^chat$'                       # expect 1
python3 -c "import json;print('chat field:',json.load(open('cortex.json')).get('chat','ABSENT'))"  # expect ABSENT
```

Then update with the new installer:

```bash
npx --min-release-age=0 @mocaos/cortex@1.2.0 --dir /tmp/mig/cortex --yes update
docker compose ps --format '{{.Service}}' | grep -c '^chat$'   # MUST still be 1
grep '^COMPOSE_PROFILES' .env                                  # expect COMPOSE_PROFILES=chat
python3 -c "import json;d=json.load(open('cortex.json'));print('stack',d['stack'],'chat',d['chat'])"
curl -s -o /dev/null -w 'chat %{http_code}\n' http://localhost:3001
```

Expected: chat still running, `COMPOSE_PROFILES=chat` now present, state `stack 1.1.0 chat True`. If chat is gone, **stop** — the back-fill in Task 9 is broken and this is the data-visible failure the whole task exists to prevent.

- [ ] **Step 6: Tear down and confirm the maintainer's volumes are untouched**

```bash
cd /tmp/mig/cortex
npx --min-release-age=0 @mocaos/cortex@1.2.0 --dir /tmp/mig/cortex uninstall < /dev/null
docker volume ls --format '{{.Name}}' | grep '^cortex-mig_' | xargs -r -n1 docker volume rm
docker volume ls --format '{{.Name}}' | grep '^cortex_' | sort   # expect the same 9 as before
cd /tmp && rm -rf mig
```

- [ ] **Step 7: Report**

Summarise: services present in each of the four combinations, whether the chat-off install avoided the health stall, and the migration result. Note explicitly that domain mode was verified only by `verify-contract.sh` and `caddy validate` — real ACME issuance needs public DNS and is out of scope here.

---

## Notes for the implementer

**The one thing most likely to go wrong.** Removing chat's `${VAR:?}` guards, or stopping `.env` from setting `CORTEX_CHAT_IMAGE`, looks like the obvious tidy-up and breaks every chat-off install with `error while interpolating services.chat.image`. Compose interpolates before it filters profiles. `selfhost/verify-contract.sh` catches it; run it after any compose edit.

**Task order matters in two places.** Task 10 (installer 1.2.0) must land before Task 11, because Task 11 sets `minInstaller` to the version Task 10 publishes. And Task 12's migration check needs both releases live.

**Domain mode cannot be fully verified here.** `verify-contract.sh` and `caddy validate` cover config correctness; certificate issuance needs a public A record. Do not claim domain mode is tested end to end.
