# Cortex self-host installer + 1.0.0 release pipeline — design

**Date:** 2026-07-27
**Status:** approved, ready for implementation planning
**Spans:** `mocaOS/cortex-app`, `mocaOS/cortex-chat`, new `mocaOS/cortex-installer`

## Goal

Someone with Docker installed types one command and ends up with a working,
self-hosted Cortex — the app and Cortex Chat, wired together — configured through
an interactive wizard, pinned to a tested release, and updatable from the CLI.

```
npx @mocaos/cortex
```

Two supporting goals fall out of that: both repos need real versioned releases
(they have none today), and those releases need to produce container images the
installer can pull.

## Scope corrections made during design

Three things in the original request could not work as stated:

1. **`npx install cortex-agent` is not valid npx syntax.** `npx install X` executes
   a package literally named `install`. The correct forms are `npx <pkg>`,
   `npx create-<pkg>`, or `npm create <pkg>`.
2. **`cortex-agent` is taken on npm** — an active package by "Kucell", latest
   `1.5.0`, an AI-agent governance tool. So are `create-cortex`, `cortex-cli`, and
   `create-cortex-app`.
3. **The `@mocaos` scope is free** (registry 404, zero packages under a scope-wide
   search). npm normalizes scopes to lowercase, so `@mocaOS/cortex` publishes and
   resolves as `@mocaos/cortex`.

**Decision: the command is `npx @mocaos/cortex`.** The npm org `mocaos` must be
created before first publish (free for public packages).

## Current state

| | cortex-app | cortex-chat |
|---|---|---|
| Repo | `mocaOS/cortex-app` | `mocaOS/cortex-chat` |
| Root `package.json` version | `0.0.1` (frontend says `1.0.0` — inconsistent) | `1.0.0` |
| Git tags | none | none |
| GitHub releases | none | none |
| CI | `ci.yml` — backend lint+pytest, slim-image smoke, frontend typecheck+lint | none |
| Published images | none (GHCR anonymous pull → 403; Docker Hub → 404) | none |

Everything deploys by **building from source** today.
`dokploy/docker-compose.dokploy.yml` builds backend and frontend from local
context and pulls chat as a remote BuildKit context pinned to `#main` — an
unversioned moving target, which is the concrete problem this work fixes.

meta-cortex is the control plane: it provisions Dokploy tenant stacks from
cortex-app's Dokploy compose via the Dokploy API. **This design does not change
that path.** Self-host is a parallel, additional deployment target.

---

## 1 · Versioning and releases

### Source of truth

The **root `package.json` `version`** of each repo. A release workflow step fails
the run if the pushed tag does not equal that version, so the file is normative
rather than decorative.

- `cortex-app` root `0.0.1` → **`1.0.0`**. A release script syncs
  `frontend/package.json` from root so the two can never drift again.
- `cortex-chat` root stays **`1.0.0`**.
- Both repos tag `v1.0.0` from current `main`.

### Component versions vs. stack version

The two repos version independently by semver. cortex-app additionally publishes
a **`stack.json`** release asset that pins the exact component set making up one
tested stack. This is the only file the installer reads.

```json
{
  "stack": "1.0.0",
  "components": {
    "backend":  "1.0.0",
    "frontend": "1.0.0",
    "chat":     "1.0.0",
    "neo4j":    "5.26-community",
    "caddy":    "2-alpine"
  },
  "minInstaller": "1.0.0",
  "notes": "https://github.com/mocaOS/cortex-app/releases/tag/v1.0.0"
}
```

A chat-only fix ships stack `1.0.1` pointing at backend `1.0.0` — users see one
number, and a 2 GB backend image is not republished for a chat typo.

`minInstaller` lets a future stack require a newer installer; the CLI checks it
and tells the user to run `npx @mocaos/cortex@latest` rather than failing
obscurely.

### Workflows

**`cortex-app/.github/workflows/release.yml`** — on `v*` tags:

1. Guard: tag == root `package.json` version, else fail.
2. Build + push `cortex-backend` and `cortex-frontend` (see §2).
3. Generate `stack.json` from root `package.json` + a checked-in
   `selfhost/stack.template.json` holding the non-versioned pins (neo4j, caddy)
   and the current cortex-chat version.
4. Create the GitHub Release with generated notes, attaching `stack.json`.

**`cortex-chat/.github/workflows/release.yml`** — on `v*` tags: same guard, build
+ push `cortex-chat`, create the release.

**`cortex-chat/.github/workflows/ci.yml`** — new, on PR and push to main:
`npm ci` + `tsc --noEmit` (what the existing `lint` script already runs).

**`cortex-installer/.github/workflows/`** — `ci.yml` (typecheck, unit tests) and
`release.yml` (publish to npm with `NPM_TOKEN` + provenance).

### Release ordering

cortex-chat releases first when its version moves, because cortex-app's
`stack.json` pins a chat version that must already exist as an image. The
`stack.template.json` chat pin makes that dependency explicit and reviewable in a
PR rather than implicit in a workflow.

---

## 2 · Container images

Published to **GHCR**: `ghcr.io/mocaos/cortex-{backend,frontend,chat}`, tagged
`1.0.0`, `1.0`, `1`, `latest`.

### Multi-arch on native runners

`linux/amd64` on `ubuntu-latest`, `linux/arm64` on `ubuntu-24.04-arm` (free on
public repos since Aug 2025), merged with `docker buildx imagetools create`.
QEMU-emulated torch builds take hours; native builds take ~20 min. Layer caching
via `type=gha`.

### Backend image: CPU-only torch

The backend currently installs `torch` from the default PyPI index, which pulls
the CUDA wheels (`nvidia-*`, ~2.5 GB) into an image that never installs the CUDA
runtime and never requests GPU devices. A new build arg:

```dockerfile
ARG TORCH_VARIANT=cuda   # cuda (existing default) | cpu
```

When `cpu`, `requirements-ml.txt` installs against
`--index-url https://download.pytorch.org/whl/cpu`. The published self-host image
uses `cpu`: **~7 GB → ~2 GB**.

This is additive — the existing default is unchanged, so Dokploy/Coolify builds
and anyone building locally are unaffected. The `AcceleratorDevice.AUTO` paths in
`backend/app/services/document_processor.py:1659`, `docling_worker.py:122`, and
`prompt_guard_local.py:55` already resolve to CPU in every current Docker deploy,
so there is no behavioral change in practice.

The self-host image is the **full** ML image (`INSTALL_LOCAL_ML=true`). The slim
variant cannot convert documents without a remote cortex-helper, so it is not
viable standalone.

### Frontend image portability

Two build inputs are baked into a Next.js image and therefore must be correct at
publish time for all users:

- **`API_URL`** — consumed by `next.config.mjs` `rewrites()`, which Next
  serializes into `required-server-files.json` at build time. Published with the
  Dockerfile default `http://backend:8000`. **The generated self-host compose must
  therefore name the backend service `backend`.**
- **`NEXT_PUBLIC_API_URL`** — deliberately left **unset**. `frontend/src/lib/api.ts:60`
  falls back to `""`, so the browser calls same-origin `/api/*` and the Next server
  proxies to the backend. One image works for localhost and for every domain.

Consequence: `frontend/src/components/layout/Header.tsx:60` reads
`NEXT_PUBLIC_LOGO_URL`, a build-time inlined value, so **logo branding silently
cannot work on a prebuilt image**. Fixed by reading it server-side and passing it
as a prop — the same pattern `layout.tsx:46-50` already uses for `ACCENT_COLOR`.
Accent color already works at runtime and needs no change.

### GHCR visibility

Packages created by the first workflow push default to **private**, and the
installer pulls anonymously. Each of the three packages must be switched to public
once, manually, in the org's package settings. This is a one-time manual step and
a hard prerequisite for the installer working at all.

---

## 3 · The generated stack

Rendered into the install directory from a template checked into
`cortex-app/selfhost/`. Services:

| Service | Image | Notes |
|---|---|---|
| `neo4j` | `neo4j:5.26-community` | APOC, `mem_limit` from wizard, bolt/http bound to `127.0.0.1` only |
| `backend` | `ghcr.io/mocaos/cortex-backend:<pin>` | Named `backend` — required by the baked frontend rewrite |
| `frontend` | `ghcr.io/mocaos/cortex-frontend:<pin>` | |
| `chat` | `ghcr.io/mocaos/cortex-chat:<pin>` | `CORTEX_API_URL=http://backend:8000` |
| `backup` | built from `ops/backup` | Lifted from `dokploy/docker-compose.dokploy.yml:421-468` |
| `caddy` | `caddy:2-alpine` | **Public-domain mode only** — automatic Let's Encrypt |

Volumes: `neo4j_data`, `neo4j_logs`, `uploads_data`, `custom_inputs_data`,
`skills_data`, **`apps_data`**, `hf_cache`, `chat_data`, `backups`.

`apps_data:/app/.agents/apps` is present in `docker-compose.prod.yml:58` but
**missing from the Dokploy compose**, so installed apps currently do not survive a
redeploy there. The self-host compose includes it. Fixing the Dokploy stack is a
one-line change but belongs to a separate PR — noted here as an observation, not
taken on in this scope.

### The backup sidecar

Included by default. It performs a verified server-side APOC logical export plus a
tar of the file volumes, with retention rotation and a healthcheck that goes
unhealthy when the newest verified backup is stale. Self-hosters have no ops team;
without this, one `docker compose down -v` is unrecoverable loss. The machinery
already exists and is proven in the Dokploy stack — this design gives it a front
door (`cortex backup` / `cortex restore`) plus one coverage fix.

**Coverage fix.** `ops/backup/backup.sh:107-112` names its source directories
explicitly (`uploads`, `custom_inputs`, `chat`), so user-installed **skills and
apps are silently not backed up**. Two guarded entries are added for
`/data/skills` and `/data/apps`, mounted `:ro` in the self-host backup service.
The existing `[ -d … ]` guard pattern makes this backward compatible: Dokploy
stacks that do not mount those paths are unaffected.

Because `ops/backup` lives in cortex-app and is built rather than pulled, the
install directory needs it. The installer fetches the `ops/backup` directory from
the pinned cortex-app tag (tarball via the GitHub API) into `./cortex/ops/backup/`.
Building it is a few seconds — it is a small Alpine + cypher-shell image.

### Networking and ports

**Localhost mode** — every published port binds to `127.0.0.1` so a laptop or VPS
does not accidentally expose the stack to its LAN or the internet:

```
127.0.0.1:3000 → frontend      127.0.0.1:8000 → backend
127.0.0.1:3001 → chat          127.0.0.1:7474/7687 → neo4j
```

Ports are wizard-configurable; conflicts are detected before anything is written.

**Public-domain mode** — only Caddy binds `0.0.0.0:80` and `:443`. Nothing else
publishes a port.

```
cortex.example.com  { reverse_proxy frontend:3000 }
chat.example.com    { reverse_proxy chat:3000 }
```

The API needs no separate domain: the frontend proxies `/api/*`, `/apps/*` and
`/a/*` to the backend, so it is reachable at `https://cortex.example.com/api/…`.
Advanced mode can add a dedicated API domain. Default DNS setup is two A records.

### Environment file

Written to `./cortex/.env`, `chmod 600`. Key values:

| Var | Value | Why |
|---|---|---|
| `ENVIRONMENT` | `production` | Hides `/docs`, enables the secret-hardening startup check |
| `SESSION_COOKIE_SECURE` | `false` in localhost mode, unset otherwise | Browsers drop `Secure` cookies over plain HTTP; without this, admin login fails silently on localhost |
| `CORS_ALLOWED_ORIGINS` | the configured origins in domain mode, `*` on localhost | Hardening where a real origin exists |
| `SENTRY_DSN` / `SENTRY_DSN_BACKEND` / `SENTRY_DSN_FRONTEND` | **empty** unless explicitly opted in | See below |

**Error tracking is off by default.** The Dokploy compose hard-defaults
`SENTRY_DSN` to `glitchtip.cortex.eco`. Shipping that in a public installer would
route strangers' stack traces — potentially containing their document text,
filenames and email addresses — to the maintainers' GlitchTip without consent. The
installer writes empty DSNs and offers one explicit opt-in prompt defaulting to No.

---

## 4 · The installer package

New repo **`mocaOS/cortex-installer`** → npm **`@mocaos/cortex`**. It versions and
releases independently of the stack, so an installer bugfix does not require an
app release, and one installer can support several stack versions.

Node ≥ 18, ESM, TypeScript, published with a single `bin`. Dependencies kept
minimal: `@clack/prompts` (the modern default for interactive Node CLIs — ESM,
TypeScript-native, styled with no theming layer), `picocolors`, `semver`, and
`yaml`. No `chalk`, no `inquirer`, no `ora` — clack ships its own spinner and
uses Node's built-in `styleText`.

### Modules

Each is independently testable with one clear job:

| Module | Responsibility |
|---|---|
| `preflight` | Docker / Compose / daemon / disk / RAM / arch / port checks |
| `stack` | Fetch and parse `stack.json`; semver + `minInstaller` comparison |
| `wizard` | Prompt flow only — returns a plain `InstallConfig` object |
| `secrets` | Generate and validate credentials |
| `validate` | Live LLM provider probes |
| `render` | `InstallConfig` → compose YAML, `.env`, `Caddyfile` (pure functions) |
| `docker` | Compose driver: pull, up, health-wait, logs, exec |
| `state` | Read/write `cortex.json` |
| `commands/*` | One file per CLI verb, composing the above |

`wizard` returns data and `render` is pure, so the whole config→artifact path is
snapshot-testable without Docker or a TTY.

### Install directory

```
./cortex/
  docker-compose.yml     generated, pinned image tags — regenerated on update
  .env                   chmod 600
  Caddyfile              public-domain mode only
  cortex.json            install state (no secrets)
  ops/backup/            fetched from the pinned cortex-app tag
  README.md              generated: URLs, commands, where the secrets live
```

`cortex.json` records installer version, stack version, resolved component
versions, mode, domains, ports, compose project name, install timestamp, provider
id, and the previous component set for rollback. **No secrets** — those exist only
in `.env`.

**User customizations go in `docker-compose.override.yml`**, which Compose merges
automatically and the installer never reads or writes. `docker-compose.yml` is
regenerated wholesale on update; the generated file says so in a header comment,
and `update` warns if the file has been modified since generation (hash recorded
in `cortex.json`).

---

## 5 · The wizard

### Visual design

```
   ▄▄· ⎯⎯⎯ Cortex
  ▐█ ▌▪ self-host installer 1.0.0

◇  Docker 27.3.1 · Compose v2.29.7 · daemon up          ✔
◇  Disk 84 GB free · RAM 16 GB                          ✔
│
◇  Stack manifest ─────────────────────────────────╮
│    Cortex 1.0.0                                  │
│    backend 1.0.0 · frontend 1.0.0 · chat 1.0.0   │
├──────────────────────────────────────────────────╯
│
◆  Install location
│  ./cortex
│
◆  How will you reach Cortex?
│  ● Localhost — http://localhost:3000
│  ○ Public domain — automatic HTTPS
│
◆  Setup depth
│  ● Quick — one provider, sensible defaults
│  ○ Advanced — per-task models
│
◆  LLM provider
│  ● OpenAI   ○ OpenRouter   ○ Venice   ○ Groq
│  ○ Ollama   ○ Other OpenAI-compatible
│
◆  API key
│  ••••••••••••••••••••••••••••••••
│
◇  68 models from api.openai.com                        ✔
◆  Chat model        › gpt-5.2
◆  Embedding model   › text-embedding-3-small
│
◇  Chat completion   412 ms                             ✔
◇  Embeddings        1536 dimensions detected           ✔
│
◆  Admin email
│  you@example.com
│
◆  Secrets — generate automatically?
│  ● Generate all 5   ○ Let me set them
│
◇  Generated ──────────────────────────────────────╮
│    ADMIN_PASSWORD         Kf7q-2mXv-9Lbn-4Twe    │
│    ADMIN_API_KEY          cortex_admin_a4f9…     │
│    NEO4J_PASSWORD         ••••  SESSION_SECRET ••│
│    CHAT_APP_ENCRYPTION_KEY ••••                  │
│    → saved to ./cortex/.env (chmod 600)          │
├──────────────────────────────────────────────────╯
│
◆  Send anonymous crash reports to the maintainers?
│  ○ Yes   ● No
│
◇  Pulling images  4/4 · 2.6 GB                         ✔
◇  Starting stack                                       ✔
◇  Waiting for health  neo4j ✔  backend ✔  frontend ✔  chat ✔
│
└  Cortex is running.

     Cortex   http://localhost:3000
     Chat     http://localhost:3001
     Login    you@example.com / Kf7q-2mXv-9Lbn-4Twe

     npx @mocaos/cortex status · logs · update
```

Progress display follows the established CLI patterns: a **spinner** for steps
under ~5 s, an **X of Y counter** for image pulls (where progress is measurable
and a stall must be visible), and clack's **`taskLog`** for streaming Compose
output that is cleared on success. Completion messages are past tense
("Pulled", not "Pulling").

`NO_COLOR` and `--no-color` are respected, and truecolor degrades to 16-color.
Non-TTY detection switches to the non-interactive path automatically.

### Preflight

Runs before any prompt, so a doomed install fails in seconds:

- Node ≥ 18.
- `docker version` — daemon reachable, not just the binary present.
- `docker compose version` — the **v2 plugin**, ≥ 2.20.
- Host arch is `amd64` or `arm64`; anything else fails with a clear message.
- Free disk on `docker info → DockerRootDir` ≥ 20 GB (hard fail below 10).
- `docker info → MemTotal` ≥ 6 GB (warn below 8 — Neo4j alone is capped at 4 GB).
- Localhost mode: TCP-bind test on each chosen port; offer alternatives on conflict.
- Domain mode: resolve each domain and warn if it does not point at this host.

### Docker is the only prerequisite the user installs

Node is implied by typing `npx`. Everything else — compose file, secrets, images,
Neo4j, Caddy, TLS certificates — is handled.

The one wrinkle: on Linux, `apt install docker.io` yields Docker *without* the
Compose v2 plugin. The installer **detects and instructs**; it does not run
`curl | sh` or `apt install` itself. Those need sudo, are distro-specific, and an
installer that silently mutates system packages is a bad neighbour. macOS and
Windows need Docker Desktop, which is a GUI install regardless.

### LLM configuration — two tiers

**Quick.** Provider preset → base URL pre-filled → key → `GET /v1/models` → pick
chat and embedding model from the real list → live probes.

Providers: OpenAI, OpenRouter, Venice, Groq, Ollama (local, no key), and "other
OpenAI-compatible" (free-text base URL). When `/v1/models` is unavailable or 404s,
fall back to free-text model entry — never a hard block.

**Probes, run before anything is written:**

1. `POST /v1/chat/completions`, `max_tokens: 1` — proves key, base URL, model name.
2. `POST /v1/embeddings` with no `dimensions` param — records the natural
   dimension → `EMBEDDING_DIMENSION`.
3. `POST /v1/embeddings` again *with* `dimensions` set to the observed value —
   success → `EMBEDDING_SEND_DIMENSIONS=true`, failure → `false`. Empirical rather
   than a hardcoded model table that goes stale.

20 s timeout each. On failure, show the real HTTP status and a body snippet, then
offer retry / change settings / continue anyway.

Getting `EMBEDDING_DIMENSION` right at install time matters disproportionately:
Neo4j bakes it into the vector index, and changing it later forces a full re-embed
of the corpus.

**Advanced** adds, on top of Quick: `GRAPH_EXTRACTION_MODEL`, `VISION_MODEL`,
separate `EMBEDDING_API_BASE` / `EMBEDDING_API_KEY`, `OPENAI_MAX_CONTEXT`,
`ENABLE_RERANKING`, `CORTEX_NEO4J_MEM_LIMIT`, `BATCH_PROCESSING_CONCURRENCY`, chat
SMTP settings, and a dedicated API domain in public mode. Every one of these
already has a sane inheriting default, so skipping the tier costs nothing.

### Secrets

Default is **generate everything**, with an option to enter custom values.
All generated with `crypto.randomBytes`:

| Secret | Form |
|---|---|
| `ADMIN_PASSWORD` | 4 groups of 4 from an unambiguous alphabet (no `0/O/1/l/I`) — transcribable, ~82 bits |
| `ADMIN_API_KEY` | `cortex_admin_` + 32 bytes hex |
| `NEO4J_PASSWORD` | 32 bytes base64url |
| `SESSION_SECRET` | 48 bytes hex (96 chars — well over the 32-char minimum) |
| `CHAT_APP_ENCRYPTION_KEY` | `randomBytes(32).toString('base64')` — exactly the form cortex-chat expects |

Custom values are validated against the **same weak-value set the backend
enforces** at `backend/app/config.py:1232-1241`, so a bad secret is rejected at the
prompt rather than by a container that refuses to boot 90 seconds later.
`SESSION_SECRET` length is checked against the same ≥ 32 rule.

Only `ADMIN_PASSWORD` is printed in the final summary — it is the one the user must
copy. Nothing is written to a log file or transmitted anywhere.

### Shared credentials

cortex-chat reuses the Cortex admin identity, as the Dokploy stack already does:
`SUPERADMIN_EMAIL` = `ADMIN_EMAIL`, `SUPERADMIN_PASSWORD` = `ADMIN_PASSWORD`,
`BACKEND_ADMIN_API_KEY` = `ADMIN_API_KEY`. One login works in both apps, which is
the "work in combination" requirement.

### Non-interactive mode

`--yes` plus environment variables or a `--config <file>` skips every prompt. This
is a first-class feature, not a test affordance: it is how people script installs
and how CI exercises the end-to-end path. Missing required values fail with a
precise list rather than hanging on a prompt.

---

## 6 · CLI commands

| Command | Behavior |
|---|---|
| `install` (default) | The wizard above. Refuses to overwrite an existing install; suggests `update` or `config`. |
| `update` | Fetch latest `stack.json` → check `minInstaller` → diff table → offer backup → regenerate compose → `pull` → `up -d` → health-wait → update `cortex.json`. Offers rollback to `cortex.json.previous` if health fails. |
| `config` | Re-run the wizard against an existing install, pre-filled from `.env` + `cortex.json`. Shows a diff, recreates only affected services. |
| `status` | Service table: state, health, image tag, uptime, ports/URLs. |
| `logs [service]` | `docker compose logs -f`, correct project and directory. |
| `start` / `stop` / `restart` | Lifecycle wrappers. |
| `backup` | `docker compose exec backup /backup.sh`, streams progress, reports the resulting timestamp. |
| `restore <timestamp>` | Lists available backups, requires typed confirmation, runs `restore.sh`. |
| `doctor` | Diagnostics: Docker/Compose versions, container health, disk headroom, LLM key still authenticates, Neo4j accepts the stored password, backup freshness. Prints one pasteable block. |
| `uninstall` | Stops and removes containers. Volume deletion is a separate, explicitly typed confirmation. |

All commands locate the install by walking up from the cwd for `cortex.json`, or
accept `--dir`.

There is deliberately **no in-app update banner and no auto-update.** Updates are
CLI-initiated only.

---

## 7 · Changes to existing repos

**cortex-app**

- Root `package.json` → `1.0.0`; release script syncs `frontend/package.json`.
- `backend/Dockerfile.prod`: add `TORCH_VARIANT` build arg (§2).
- `frontend/src/components/layout/Header.tsx:60`: make the logo URL runtime-readable.
- `ops/backup/backup.sh`: two guarded entries for skills and apps (§3).
- New `selfhost/`: compose template, `stack.template.json`, README.
- New `.github/workflows/release.yml`.
- Docs: new handbook chapter on self-hosting; `.claude/` updates per the routing
  table in `CLAUDE.md` (`development.md` for the new deployment path,
  `environment.md` if any var semantics change).

**cortex-chat**

- New `.github/workflows/ci.yml` and `release.yml`.
- No application changes — its Dockerfile is already fully runtime-configured,
  which makes its image portable as-is.

**cortex-installer** — new repo, everything in §4–6.

**Not changed:** `dokploy/`, `coolify/`, and meta-cortex's provisioning path. Those
continue to build from source. Migrating them onto the published images is a
reasonable follow-up but is explicitly out of scope here.

---

## 8 · Testing

**Unit** — the majority of the value, and all of it runs without Docker:

- `render` snapshot tests: every mode × option combination produces the expected
  compose, `.env`, and `Caddyfile`.
- `secrets`: charset, length, and — importantly — assert that generated values are
  never members of the backend's weak-value set.
- `stack`: manifest parsing, semver comparison, `minInstaller` enforcement,
  malformed-manifest handling.
- `preflight`: version parsing across real `docker version` output shapes.

**Integration** — a fake OpenAI-compatible server exercising `validate`:
happy path, 401, `/v1/models` 404 (fallback to free-text), fixed-dimension
embedding models, and timeouts.

**End-to-end** — in the installer repo's CI, run the non-interactive install
against real GHCR images and assert every service reaches healthy and the backend
`/health` returns 200, then `update`, `backup`, and `uninstall`. Gated to release
tags and a nightly schedule, since it pulls gigabytes.

**Manual, once, before announcing 1.0.0:** a real install on a clean cloud VPS in
public-domain mode, verifying Let's Encrypt issuance, chat↔backend wiring, a
document upload through the full pipeline, and a shared login across both apps.

---

## 9 · Implementation phasing

The work spans three repos, so it is sequenced into phases that each end at a
verifiable state rather than a half-migrated one.

**Phase 1 — releases and images.** Version bumps, the three release workflows,
cortex-chat CI, `TORCH_VARIANT`, the logo fix, and GHCR publishing.
*Done when:* `docker pull ghcr.io/mocaos/cortex-backend:1.0.0` works anonymously
for all three images on both architectures, and `stack.json` is attached to the
cortex-app v1.0.0 release. This phase has standalone value — the Dokploy and
Coolify paths could later consume these images — and the installer is dead
weight without it.

**Phase 2 — the stack template.** `selfhost/` compose template, `apps_data`, the
`backup.sh` coverage fix.
*Done when:* a hand-filled `.env` plus the template brings up a healthy stack on a
clean host, verified by an actual document upload and a shared login across both
apps.

**Phase 3 — the installer.** The `cortex-installer` repo, wizard, CLI, tests,
npm publish.
*Done when:* `npx @mocaos/cortex` completes on a clean VPS in both modes, and
`update` / `backup` / `doctor` / `uninstall` behave.

**Phase 4 — documentation.** Handbook self-hosting chapter, README install
section, `.claude/` updates per the routing table in `CLAUDE.md`.

Phase 2 depends on Phase 1 only for image names, so the template can be written
in parallel and pinned once the images exist.

## 10 · Manual prerequisites

Not automatable; must happen before or during rollout:

1. Create the npm org **`mocaos`** and an automation token → `NPM_TOKEN` secret in
   `cortex-installer`.
2. Create the **`cortex-installer`** repo.
3. After the first image push, flip all three GHCR packages to **public**
   visibility. The installer pulls anonymously and will fail hard until this is
   done.

---

## 11 · Explicit non-goals

- Deploying to a remote Dokploy server from the installer — meta-cortex owns that.
- Kubernetes, Helm, or any non-Compose orchestration.
- Auto-updates, Watchtower, or scheduled pulls.
- A bundled Mailpit; SMTP is an optional prompt and cortex-chat already hides the
  reset flow when it is unset.
- Migrating the Dokploy/Coolify composes onto published images.
- An in-app update notification.
