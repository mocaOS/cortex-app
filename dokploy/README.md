# Dokploy Deployment

This directory contains the Docker Compose configuration for deploying Cortex on [Dokploy](https://dokploy.com/), an open-source self-hosted PaaS that uses Traefik for reverse proxy and automatic SSL.

## Prerequisites

- A running Dokploy instance ([install guide](https://docs.dokploy.com/docs/core/get-started/introduction))
- A domain pointed to your Dokploy server

## Deployment Steps

1. In Dokploy UI, create a new **Compose** project
2. Connect your Git repository and set the compose file path to `dokploy/docker-compose.dokploy.yml`
3. Go to the **Environment** tab and add the required environment variables (see below)
4. Configure domains for the **backend** and **frontend** services (see [Domain Configuration](#domain-configuration))
5. Deploy!

## Required Environment Variables

Set these in Dokploy's **Environment** tab. Dokploy injects them into the shell environment for `${}` interpolation in the compose file. Unlike Coolify, Dokploy does **not** create a physical `.env` file, so all variables are passed explicitly via `environment` in the compose file.

### Core Services

| Variable | Description | Required |
|----------|-------------|----------|
| `NEO4J_PASSWORD` | Neo4j database password | Yes |
| `OPENAI_API_KEY` | OpenAI API key | Yes |
| `OPENAI_API_BASE` | Custom OpenAI-compatible API base URL | No |
| `OPENAI_MODEL` | Model to use (e.g., `gpt-4o-mini`) | No |

### Embedding Configuration

| Variable | Description | Required |
|----------|-------------|----------|
| `EMBEDDING_MODEL` | Embedding model name | No |
| `EMBEDDING_DIMENSION` | Embedding vector dimension | No |
| `USE_OPENAI_EMBEDDINGS` | Whether to use OpenAI embeddings | No |
| `EMBEDDING_API_BASE` | Separate API base for embeddings | No |
| `EMBEDDING_API_KEY` | Separate API key for embeddings | No |

### Vision Model (Optional)

Image extraction and analysis. If not set, Docling's built-in image description is used.

| Variable | Description | Required |
|----------|-------------|----------|
| `VISION_MODEL` | Vision model (e.g., `gpt-4o`, `claude-3-5-sonnet-20241022`) | No |
| `VISION_MODEL_API_BASE` | API endpoint for vision model (defaults to `OPENAI_API_BASE`) | No |
| `VISION_MODEL_API_KEY` | API key for vision model (defaults to `OPENAI_API_KEY`) | No |

### Graph Extraction (Optional)

Use a smaller/faster model for entity extraction during document processing.

| Variable | Description | Required |
|----------|-------------|----------|
| `GRAPH_EXTRACTION_MODEL` | Model for entity extraction (defaults to `OPENAI_MODEL`) | No |
| `GRAPH_EXTRACTION_API_BASE` | API endpoint for extraction model (defaults to `OPENAI_API_BASE`) | No |
| `GRAPH_EXTRACTION_API_KEY` | API key for extraction model (defaults to `OPENAI_API_KEY`) | No |

> **Tip:** Use the same small multimodal model for both `GRAPH_EXTRACTION_MODEL` and `VISION_MODEL` to get fast extraction + image analysis from the same endpoint.

### Relationship Analysis (Optional)

Cross-document relationship discovery settings.

| Variable | Description | Required |
|----------|-------------|----------|
| `RELATIONSHIP_EXTRACTION_MODEL` | Dedicated model for relationship extraction | No |
| `RELATIONSHIP_EXTRACTION_API_BASE` | API endpoint for relationship model | No |
| `RELATIONSHIP_EXTRACTION_API_KEY` | API key for relationship model | No |
| `RELATIONSHIP_MAX_CONTEXT` | Max context window tokens for relationship analysis (default: `65536`) | No |
| `RELATIONSHIP_MAX_OUTPUT_TOKENS` | Max output tokens for relationship responses (default: `16000`) | No |

### Admin Authentication

| Variable | Description | Required |
|----------|-------------|----------|
| `ADMIN_EMAIL` | Admin login email | Yes |
| `ADMIN_PASSWORD` | Admin login password | Yes |
| `ADMIN_API_KEY` | Admin API key for full backend access | Yes |
| `SESSION_SECRET` | JWT session secret (min 32 chars) | Yes |
| `TRACK_ADMIN_API_KEY_USAGE` | Track usage analytics for admin API key (default: `false`) | No |

> **Generating secure values:**
> - `ADMIN_API_KEY`: Use `openssl rand -hex 32` prefixed with `cortex_admin_`
> - `SESSION_SECRET`: Use `openssl rand -hex 32`
> - `NEO4J_PASSWORD`: Use `openssl rand -hex 16`

### Frontend / Branding

| Variable | Description | Required |
|----------|-------------|----------|
| `NEXT_PUBLIC_API_URL` | Public URL of the backend API (e.g., `https://api-cortex.yourdomain.com`) | Yes |
| `NEXT_PUBLIC_LOGO_URL` | Custom logo URL | No |
| `NEXT_PUBLIC_ACCENT_COLOR` | Custom accent color (any CSS color value) | No |

### Chat

The `chat` service runs [Cortex Chat](https://github.com/mocaOS/cortex-chat), built from its public repo as a remote build context. It reuses `ADMIN_API_KEY`, `ADMIN_EMAIL`, and `ADMIN_PASSWORD` (the chat superadmin signs in with the same credentials as Cortex), so only one extra variable is needed:

| Variable | Description | Required |
|----------|-------------|----------|
| `CHAT_APP_ENCRYPTION_KEY` | 32 random bytes, base64-encoded — encrypts minted backend API keys in the chat's SQLite store. Generate with `openssl rand -base64 32`. Must stay stable across redeploys. | Yes |

> **Do NOT set `NEO4J_USER`** - Neo4j interprets all `NEO4J_*` env vars as config settings, causing startup failures. The username is hardcoded to `neo4j` in the compose file.

## Domain Configuration

Dokploy uses Traefik for reverse proxy with automatic SSL via Let's Encrypt. There are two ways to configure domains:

### Method A: Dokploy UI (Recommended)

1. In your Compose project, click on the **backend** service
2. Go to the **Domains** tab
3. Add your backend domain (e.g., `api-cortex.yourdomain.com`) mapped to port `8000`
4. Repeat for the **frontend** service with your frontend domain (e.g., `cortex.yourdomain.com`) mapped to port `3000`
5. Repeat for the **chat** service with your chat domain (e.g., `chat-cortex.yourdomain.com`) mapped to port `3000`

Dokploy auto-configures Traefik labels and TLS certificates. Allow ~10 seconds after deployment for certificate generation.

### Method B: Manual Traefik Labels

For GitOps workflows where you want domain config in version control, uncomment the `labels` block in the compose file for each web-facing service and replace `yourdomain.com` with your actual domain.

When using manual labels, ensure router names (e.g., `cortex-backend`, `cortex-frontend`) are globally unique across all services on the Dokploy instance.

## What Gets Exposed

- **Frontend**: Main domain (e.g., `cortex.yourdomain.com`) on port 3000
- **Backend API**: API subdomain (e.g., `api-cortex.yourdomain.com`) on port 8000
- **Chat**: Chat subdomain (e.g., `chat-cortex.yourdomain.com`) on port 3000
- **Neo4j**: Not exposed externally (internal only, not on dokploy-network)

## Differences from Coolify Deployment

| Aspect | Coolify | Dokploy |
|--------|---------|---------|
| Magic variables | `SERVICE_FQDN_*`, `SERVICE_PASSWORD_*` | None -- explicit `environment` pass-through |
| Domain routing | Auto via `SERVICE_FQDN_*` env vars | Dokploy UI Domains tab or manual Traefik labels |
| Password generation | `SERVICE_PASSWORD_NEO4J` auto-generated | Set `NEO4J_PASSWORD` manually |
| Proxy network | `coolify` | `dokploy-network` |
| Container names | Allowed | Forbidden (breaks Dokploy logging/metrics) |
| Service exposure | Implicit via Coolify proxy | `expose` + Traefik routing |
| Backend URL | `BACKEND_URL` (Coolify magic) | `NEXT_PUBLIC_API_URL` (set directly) |

## Notes

- Neo4j browser is not exposed for security. Use SSH tunnel if needed:
  ```bash
  ssh -L 7474:localhost:7474 -L 7687:localhost:7687 user@your-server
  ```
- Named volumes persist across deployments. To reset data, manually delete the volumes.
- Dokploy wipes the repo directory on each deploy (git clone), but named volumes are unaffected.
- **Build context paths**: The compose file uses `../backend` and `../frontend` because Dokploy resolves paths relative to the compose file's directory (`dokploy/`), not the repo root.
- **No `.env` file**: Dokploy does not create a physical `.env` file. It injects env vars into the shell for `${}` interpolation. All vars are passed explicitly via `environment` in the compose file.
