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

Requires Neo4j 5.15+ with APOC plugin. In Docker this is preconfigured. For local dev, set `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` env vars. See [`.claude/environment.md`](environment.md) for all env vars.

## Deployment

### Coolify

Use `coolify/docker-compose.coolify.yml`. Important: services with `SERVICE_FQDN_*` must have `traefik.docker.network=coolify` label and join the external `coolify` network to avoid 504 timeouts.

### Dokploy

Use `dokploy/docker-compose.dokploy.yml`. Configure domains in Dokploy UI (Domains tab) or uncomment Traefik labels in the compose file. See `dokploy/README.md` for full setup.

### Standalone Docker

`docker-compose.prod.yml` with Nginx reverse proxy (`nginx/nginx.conf`).
