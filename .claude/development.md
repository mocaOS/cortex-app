# Development & Deployment

## Docker (primary development method)

```bash
# Dev environment (all services with hot reload)
docker compose up --build

# Production
docker compose -f docker-compose.prod.yml up --build
```

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

### Standalone Docker

`docker-compose.prod.yml` with Nginx reverse proxy (`nginx/nginx.conf`).

### Slim image & backups

- **Slim backend image** (helper-backed stacks): `docker build -f backend/Dockerfile.prod --build-arg INSTALL_LOCAL_ML=false` → no torch/docling (~1.2GB vs full). Requires OpenAI embeddings + `RERANKER_SERVICE_URL`/`DOCLING_SERVICE_URL` (recommended `HELPER_STRICT_REMOTE=true`). CI smoke-builds it on every PR. **The Dokploy deploy (`dokploy/docker-compose.dokploy.yml`) defaults the `backend` build to slim** (`INSTALL_LOCAL_ML=${INSTALL_LOCAL_ML:-false}`) since every cloud tenant offloads to `cortex-helper`; override with `INSTALL_LOCAL_ML=true` in the Dokploy env for a stack that runs models locally.
- **Backups** (opt-in overlay): `docker compose -f docker-compose.prod.yml -f docker-compose.backup.yml up -d` — nightly APOC logical export (Community + Enterprise) + uploads/custom_inputs tar, `BACKUP_RETENTION_DAYS` rotation. Manual run: `docker compose exec backup /backup.sh`. Restore runbook in `ops/backup/backup.sh`.
- **Memory caps**: every service (incl. Neo4j and nginx) carries a compose `mem_limit`; tune `CORTEX_NEO4J_MEM_LIMIT`/`CORTEX_NEO4J_HEAP_MAX`/`FRONTEND_MEM_LIMIT` per host (the neo4j caps are deliberately not `NEO4J_`-prefixed — a raw `NEO4J_*` var reaching the neo4j container is parsed as a config setting and rejected by strict_validation under PaaS env injection). `stop_grace_period` + uvicorn `--timeout-graceful-shutdown` drain in-flight requests on restarts; nginx has a dedicated unbuffered `location /api/ask/stream` (1h read timeout) for SSE.
