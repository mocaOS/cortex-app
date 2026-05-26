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
