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

### Vision Model (Optional)

Image extraction and analysis. If not set, Docling's built-in image description is used.

| Variable | Description | Required |
|----------|-------------|----------|
| `VISION_MODEL` | Vision model for image analysis (e.g., `gpt-4o`, `claude-3-5-sonnet-20241022`) | No |
| `VISION_MODEL_API_BASE` | API endpoint for vision model (defaults to `OPENAI_API_BASE` if not set) | No |
| `VISION_MODEL_API_KEY` | API key for vision model (defaults to `OPENAI_API_KEY` if not set) | No |

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
| `RELATIONSHIP_ANALYSIS_BATCH_SIZE` | Max entities per relationship analysis call (default: `100`) | No |
| `AUTO_RELATIONSHIP_ANALYSIS_AFTER_BATCH` | Auto-analyze after batch processing (default: `false`) | No |
| `AUTO_COMMUNITY_DETECTION_AFTER_BATCH` | Auto-detect communities after analysis (default: `false`) | No |

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
