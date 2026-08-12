# Development & Deployment

## Docker (primary development method)

```bash
# Dev environment (all services with hot reload)
docker compose up --build

# Production
docker compose -f docker-compose.prod.yml up --build
```

**Frontend memory limits are mode-dependent.** Dev mode runs the Turbopack
compiler in-process plus `@sentry/nextjs` instrumentation and peaks well past
1g while compiling routes — the dev compose defaults `FRONTEND_MEM_LIMIT` to
**3g**. An undersized limit shows up as an OOM **crash-loop** (RestartCount
climbing, `docker events` showing `start→oom→die`; in the browser the page
"refreshes every few seconds" as HMR reconnects). The prod/Dokploy composes
keep the **1g** default because `next start` serves a prebuilt bundle at a
fraction of dev's footprint; the Coolify compose carries no mem ceilings at
all (removed 2026-07 — host-level isolation instead).

## Local Development (without Docker)

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev        # Dev server on :3000
npm run build      # Production build
npm run lint       # ESLint
```

## CI

`.github/workflows/ci.yml` gates PRs (and pushes to `main`):
- **Backend**: `pip install -r requirements.txt`, `ruff check --select E9,F63,F7,F82 .` (error-only smoke check — full ruff/mypy is a follow-up), `pytest`.
- **Frontend**: `npm ci`, `npx tsc --noEmit`, `npm run lint`.

The pytest suite is fully isolated (LLM + Neo4j mocked, env sandboxed via `conftest.py`), so it runs with no external services.

## Shared model service (cortex-helper)

Optional companion repo (`cortex-helper`) hosting the cross-encoder reranker + docling converter once per physical host, for tenant-stack density. Run `docker compose up -d` there (listens on **:3030**), then set `RERANKER_SERVICE_URL` / `DOCLING_SERVICE_URL` / `HELPER_SERVICE_TOKEN` in each cortex-app `.env`. To wire across compose projects, put both on a shared external docker network. Unset = built-in local path. See [`environment.md`](environment.md#shared-model-services-cortex-helper).

## Neo4j

Requires Neo4j 5.15+ with APOC plugin (this repo ships 5.26 in all compose files — 4096-dim vector indexes supported, native fit for Qwen3-Embedding-8B). In Docker this is preconfigured. For local dev, set `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` env vars. See [`.claude/environment.md`](environment.md) for all env vars.

## HuggingFace Model Cache

The cross-encoder reranker and sentence-transformer embedder are downloaded from HF on first use. To avoid re-downloading on every container restart, a named `hf_cache` volume is mounted at `/app/.cache/huggingface` in all compose files (`docker-compose.yml`, `docker-compose.prod.yml`, `coolify/`, `dokploy/`). The Dockerfiles set `ENV HF_HOME=/app/.cache/huggingface` and pre-download both models at build time as a fallback when no volume is mounted. The reranker is also pre-warmed during the FastAPI lifespan startup (via `asyncio.to_thread`) so the first Q+A doesn't pay the load cost on the request path.

## Deployment

### Coolify

Use `coolify/docker-compose.coolify.yml`. Important: services with `SERVICE_FQDN_*` must have `traefik.docker.network=coolify` label and join the external `coolify` network to avoid 504 timeouts.

### Dokploy

Use `dokploy/docker-compose.dokploy.yml`. Configure domains in Dokploy UI (Domains tab) or uncomment Traefik labels in the compose file. See `dokploy/README.md` for full setup.

### Multi-tenant hosts: never dial bare service names across the shared proxy network

On a host running **multiple** cortex stacks (Dokploy `dokploy-network`, Coolify `coolify`), every stack auto-aliases its services (`backend`, `chat`, `frontend`) on the shared network, and Docker DNS aggregates same-name records across all networks the calling container joins. Server-side calls to `http://backend:8000` therefore intermittently reached **another tenant's backend**, which rejects the caller's API keys → random 401s in chat/frontend (diagnosed live on the moca/creazy pair 2026-07-07: moca's chat requests landed as `4xx` in creazy's `/metrics` while moca's backend showed zero). Fix in both composes: the backend declares a `cortex-backend-internal` alias on the **stack-private default network only**, and all internal consumers (`frontend` `API_URL`, `chat` `CORTEX_API_URL`) dial that alias. Keep it this way for any new internal consumer; `neo4j` is safe as a bare name because it joins only the default network.

### Standalone Docker

`docker-compose.prod.yml` with Nginx reverse proxy (`nginx/nginx.conf`). The backend CMD runs uvicorn with `--proxy-headers --forwarded-allow-ips ${UVICORN_FORWARDED_ALLOW_IPS:-*}`, so `request.client.host` carries the real client IP from nginx's `X-Forwarded-For` (per-IP rate-limit bucket, request logs). The backend port is never published in the shipped stacks, so the `*` default is safe; pin `UVICORN_FORWARDED_ALLOW_IPS` to the proxy subnet(s) when self-publishing the backend (also flips uvicorn to the spoof-resistant right-to-left trust walk — see [`environment.md`](environment.md)).

### Container user & volume ownership (prod)

`backend/Dockerfile.prod` (used by **all three** prod deploys — Coolify, Dokploy, Standalone) runs the app as non-root `appuser` (UID 1000), but PaaS named volumes mount **root-owned**, shadowing the build-time `chown` and causing `[Errno 13] Permission denied` on the first write to a mount (uploads, **downloaded skills** `/app/.agents/skills`, custom_inputs, HF cache). Fix: the image starts as **root** and `backend/docker-entrypoint.sh` chowns those mounts to `1000:1000`, then `exec gosu 1000:1000` drops to the app user (self-heals on every start; the `stat`-guard skips the recursive chown once ownership is already correct, so restarts stay fast). The standalone `backend/Dockerfile` (dev) runs as root and needs none of this. Immediate unblock on a live box without redeploy: `docker exec -u 0 <backend> chown -R 1000:1000 /app/uploads /app/custom_inputs /app/.agents/skills`.

### Slim image & backups

- **Slim backend image** (helper-backed stacks): `docker build -f backend/Dockerfile.prod --build-arg INSTALL_LOCAL_ML=false` → no torch/docling (~1.2GB vs full). Requires OpenAI embeddings + `RERANKER_SERVICE_URL`/`DOCLING_SERVICE_URL` (recommended `HELPER_STRICT_REMOTE=true`). CI smoke-builds it on every PR. **The Dokploy deploy (`dokploy/docker-compose.dokploy.yml`) defaults the `backend` build to slim** (`INSTALL_LOCAL_ML=${INSTALL_LOCAL_ML:-false}`) since every cloud tenant offloads to `cortex-helper`; override with `INSTALL_LOCAL_ML=true` in the Dokploy env for a stack that runs models locally.
- **Backups** (opt-in overlay): `docker compose -f docker-compose.prod.yml -f docker-compose.backup.yml up -d` — nightly **server-side** APOC logical export (`graph.cypher.gz`; the overlay/deploy composes set `NEO4J_apoc_export_file_enabled=true` on neo4j and mount the backups volume at its import dir — without both, the export fails loudly) + uploads/custom_inputs tar. Verified before it counts (row-count check vs live DB, SHA256SUMS, `.complete`/`LAST_SUCCESS` markers); retention (`BACKUP_RETENTION_DAYS`) rotates only after a verified success and never deletes the newest complete backup; the sidecar healthcheck goes unhealthy when `LAST_SUCCESS` is older than 2× `BACKUP_INTERVAL_SECONDS`. Manual run: `docker compose exec backup /backup.sh`. Restore: `docker compose exec -e RESTORE_WIPE=yes backup /restore.sh <ts>` (runbook in `ops/backup/restore.sh`; round-trip validated 2026-07-07 incl. adversarial escaping).
- **Memory caps**: every service (incl. Neo4j and nginx) carries a compose `mem_limit`; tune `CORTEX_NEO4J_MEM_LIMIT`/`CORTEX_NEO4J_HEAP_MAX`/`FRONTEND_MEM_LIMIT` per host (the neo4j caps are deliberately not `NEO4J_`-prefixed — a raw `NEO4J_*` var reaching the neo4j container is parsed as a config setting and rejected by strict_validation under PaaS env injection). `stop_grace_period` + uvicorn `--timeout-graceful-shutdown` drain in-flight requests on restarts; nginx has a dedicated unbuffered `location /api/ask/stream` (1h read timeout) for SSE.

## Self-host (prebuilt images)

`selfhost/` holds a third deployment path alongside `coolify/` and `dokploy/`:
static Compose files that run **prebuilt GHCR images** rather than building
from source.

- `docker-compose.yml` — base stack. **Never generate or rewrite this file.**
  Everything is `${VAR}` interpolation from `.env`.
- `docker-compose.ports.yml` / `docker-compose.caddy.yml` — mode overlays
  selected by `COMPOSE_FILE` in `.env`.
- `stack.template.json` — pins for components not built from this repo
  (chat, neo4j, caddy). `scripts/build-stack-json.mjs` turns it into the
  `stack.json` release asset.

Constraints that will silently break the stack if violated:

- The backend service must stay named `backend` — the published frontend
  image bakes `API_URL=http://backend:8000` into its rewrite manifest.
- `NEXT_PUBLIC_API_URL` must never be set for the published frontend image.
- Error-tracking DSNs default to empty here, unlike the Dokploy compose.

Releases are tag-triggered (`.github/workflows/release.yml`) and guarded by
`scripts/check-version-sync.mjs`. cortex-chat must be released before
cortex-app, since `stack.json` pins its version and verifies it is pullable.

### The installer

`npx @mocaos/cortex` (repo: `mocaOS/cortex-installer`) automates the manual
`selfhost/README.md` path. It reads `stack.json` from the latest release,
fetches that tag's `selfhost/` + `ops/` from the release tarball, and writes
only `.env`.

Three things bite when testing a just-published release:

- `npm` config `min-release-age` (a supply-chain cooldown, and a sane thing to
  have set) hides every version published inside its window, so `npx` answers
  `ENOVERSIONS — No versions available` for a release that is hours old. Use
  `npx --min-release-age=0 @mocaos/cortex` to test one; there is no
  per-package exemption in npm.
- `ops/backup/*` ships as a **locally built** image, so a change there only
  reaches an existing install because `up` passes `--build` — that behaviour
  needed installer ≥ 1.0.2. Today's actual `minInstaller` floor is `1.2.2`:
  raised further because the chat service moved behind a Compose profile, and
  an older installer would install this stack with chat silently missing.
  Raise `minInstaller` in `selfhost/stack.template.json` whenever a
  stack fix depends on newer installer behaviour like that.
- The `chat` service sits behind `profiles: ["chat"]`. Two non-obvious
  consequences: Compose interpolates `${VAR:?}` **before** filtering profiles,
  so `CORTEX_CHAT_IMAGE` and `CHAT_APP_ENCRYPTION_KEY` must stay set in `.env`
  even with chat off; and anything with `depends_on: chat` needs the long form
  with `required: false` or Compose rejects the whole project. Both are asserted
  by `selfhost/verify-contract.sh`, which CI runs.

The manual runbook has to keep parity with what `update` actually does, and
until v1.0.1 it did not: `selfhost/README.md` and the deployment guide told
you to bump the image tags and run `pull && up -d`, which re-pulls nothing for
the build-only `backup` service and never re-fetches `selfhost/` + `ops/` at
all. Both now clone the tag and pass `--build`. Any change to what `update`
does needs the same edit in both documents — a manual updater following a
stale runbook gets a silently half-applied release, with the pieces that live
in scripts left behind.

Consequences for anyone changing `selfhost/`:

- Adding a `${VAR:?}` to a compose file is a **breaking change for the
  installer** — it must also be added to the installer's `env.ts`
  (`REQUIRED_VARS`), or every new install fails the moment Compose
  interpolates the file (at `pull`, the first `docker compose` call
  `install.ts` makes), despite every earlier installer step reporting
  success. `env.ts`'s `REQUIRED_VARS` is a hand-maintained mirror of every
  `${VAR:?}` across the selfhost compose files (12, as of this writing);
  `env.test.ts` only checks that `renderEnv()` fills everything currently
  listed there — it does not diff against the live compose files, so keeping
  the two in sync is a manual step, not something CI enforces automatically.
- Renaming or removing a compose file breaks `fetchArtifacts`'s
  `ARTIFACT_FILES` check.
- The installer never edits compose files, so their `${VAR}` interpolation is
  the whole configuration contract.
