# Coolify Deployment

This directory contains the Docker Compose configuration for deploying MOCA on [Coolify](https://coolify.io/).

## Magic Variables

The compose file uses Coolify's magic environment variables:

| Variable | Description |
|----------|-------------|
| `SERVICE_FQDN_FRONTEND_3000` | Auto-generates FQDN for frontend, proxies to port 3000 |
| `SERVICE_FQDN_API_8000` | Auto-generates FQDN for backend API, proxies to port 8000 |
| `SERVICE_URL_API_8000` | Full URL for the backend API (used by frontend) |
| `SERVICE_PASSWORD_NEO4J` | Auto-generates a secure password for Neo4j |

## Required Environment Variables

Set these in Coolify's environment configuration:

### LLM Configuration

| Variable | Description | Required |
|----------|-------------|----------|
| `OPENAI_API_KEY` | OpenAI API key | Yes |
| `OPENAI_API_BASE` | Custom OpenAI API base URL | No |
| `OPENAI_MODEL` | Model to use (e.g., `gpt-4`) | No |
| `EMBEDDING_MODEL` | Embedding model name | No |
| `EMBEDDING_DIMENSION` | Embedding vector dimension | No |
| `USE_OPENAI_EMBEDDINGS` | Whether to use OpenAI embeddings | No |

### Admin Authentication

| Variable | Description | Required |
|----------|-------------|----------|
| `ADMIN_EMAIL` | Admin login email | Yes |
| `ADMIN_PASSWORD` | Admin login password | Yes |
| `ADMIN_API_KEY` | Admin API key for full backend access | Yes |
| `SESSION_SECRET` | JWT session secret (min 32 chars) | Yes |
| `TRACK_ADMIN_API_KEY_USAGE` | Track usage analytics for admin API key (default: `false`) | No |

> 💡 **Generating secure values:**
> - `ADMIN_API_KEY`: Use `openssl rand -hex 32` prefixed with `moca_admin_`
> - `SESSION_SECRET`: Use `openssl rand -hex 32`

> ⚠️ **Do NOT set `NEO4J_USER`** - Neo4j interprets all `NEO4J_*` env vars as config settings, causing startup failures. The username is hardcoded to `neo4j` in the compose file.

## Deployment Steps

1. Create a new **Docker Compose** resource in Coolify
2. Point to this repository and set the compose file path to `coolify/docker-compose.coolify.yml`
3. Configure the required environment variables
4. Deploy!

## What Gets Exposed

- **Frontend**: Main domain (e.g., `moca.yourdomain.com`)
- **Backend API**: API subdomain (e.g., `api-moca.yourdomain.com`)
- **Neo4j**: Not exposed externally (internal only)

## Notes

- Neo4j browser is not exposed for security; use SSH tunnel if needed
- The `SERVICE_PASSWORD_NEO4J` is auto-generated and shared between neo4j and backend services
- Volumes are persisted by Coolify automatically
