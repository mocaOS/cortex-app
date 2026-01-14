# Coolify Deployment Guide

This guide explains how to deploy MOCA Knowledge Base on Coolify.

## Prerequisites

- A Coolify instance (self-hosted or cloud)
- A domain name pointed to your Coolify server
- Git repository with this project

## Deployment Steps

### 1. Create a New Project in Coolify

1. Log into your Coolify dashboard
2. Click "New Project"
3. Name it "MOCA Knowledge Base"

### 2. Add Docker Compose Application

1. In your project, click "New Resource"
2. Select "Docker Compose"
3. Choose "Git Repository" as the source
4. Enter your repository URL
5. Set the compose file path to: `coolify/docker-compose.coolify.yml`
6. **IMPORTANT**: Set "Base Directory" to `/` (the repo root)

### 3. Configure Environment Variables

In Coolify's environment settings, add the variables (see section below).

### 4. Configure Domain

1. Go to the frontend service settings
2. Add your domain (e.g., `kb.yourdomain.com`)
3. Enable SSL (Let's Encrypt)

### 5. Configure Persistent Storage

Coolify automatically handles Docker volumes, but ensure:
- `neo4j_data` volume is persistent
- `uploads_data` volume is persistent

### 6. Deploy

Click "Deploy" and wait for the build to complete.

## Environment Variables for Coolify

Copy and paste the following into Coolify's environment variables section:

```env
# ===========================================
# REQUIRED - Must be set for the app to work
# ===========================================
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-strong-password-here
OPENAI_API_KEY=sk-your-openai-key-here
NEXT_PUBLIC_API_URL=https://your-domain.com

# ===========================================
# OpenAI / LiteLLM Configuration
# ===========================================
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

# ===========================================
# Embedding Configuration
# ===========================================
EMBEDDING_MODEL=openai/text-embedding-3-small
EMBEDDING_DIMENSION=1536
USE_OPENAI_EMBEDDINGS=true

# ===========================================
# Upload Configuration
# ===========================================
MAX_FILE_SIZE_MB=50

# ===========================================
# Chunking Configuration
# ===========================================
CHUNK_SIZE=500
CHUNK_OVERLAP=50
CHUNK_BY=sentence
SENTENCES_PER_CHUNK=5

# ===========================================
# GraphRAG Configuration
# ===========================================
ENABLE_GRAPH_EXTRACTION=true
MAX_GRAPH_HOPS=2
CONCURRENT_EXTRACTIONS=20

# ===========================================
# Enhanced RAG Configuration
# ===========================================
ENABLE_RERANKING=true
RERANKING_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
ENABLE_HYBRID_SEARCH=true
VECTOR_WEIGHT=0.5
KEYWORD_WEIGHT=0.3
GRAPH_WEIGHT=0.2
MAX_CONVERSATION_HISTORY=6
ENABLE_AGENTIC_RAG=true
MAX_AGENTIC_STEPS=3

# ===========================================
# Community Detection & Graph Summarization
# ===========================================
ENABLE_COMMUNITY_DETECTION=true
MIN_COMMUNITY_SIZE=3
MAX_COMMUNITIES=50
ENABLE_GRAPH_SUMMARIZATION=true

# ===========================================
# Semantic Entity Resolution
# ===========================================
ENABLE_SEMANTIC_ENTITY_RESOLUTION=true
ENTITY_SIMILARITY_THRESHOLD=0.85

# ===========================================
# Collection-Level Graphs
# ===========================================
ENABLE_COLLECTIONS=true
DEFAULT_COLLECTION=default

# ===========================================
# Extended Thinking / Reasoning Visibility
# ===========================================
STREAM_REASONING_STEPS=true
SHOW_RETRIEVAL_STATS=true
```

## Post-Deployment

### Access Neo4j Browser (Optional)

If you need to access Neo4j directly:
1. Add port 7474 and 7687 to the neo4j service expose list
2. Access via `https://your-domain.com:7474`

### Health Check

Visit `https://your-domain.com/health` to verify the API is running.

## Scaling

For higher loads:

1. **Backend**: Increase the number of workers in `Dockerfile.prod`
2. **Neo4j**: Adjust memory settings in docker-compose
3. **Frontend**: Coolify can scale Next.js instances automatically

## Troubleshooting

### Container Logs
Access logs through Coolify's dashboard under each service.

### Neo4j Connection Issues
Ensure Neo4j has fully started before the backend (healthcheck handles this).

### Slow First Request
The first request may be slow as models are loaded. This is normal.

### Build Fails with "npm ci" Error
Make sure `package-lock.json` exists in the frontend directory. Run `npm install` locally and commit the lock file.

### Neo4j Fails with "Unrecognized setting URI" Error
If Neo4j fails to start with "Unrecognized setting. No declared setting with name: URI", this is because Coolify is passing `NEO4J_URI` (which is for backend connection) to the Neo4j container. The docker-compose file includes a setting to disable strict validation to handle this. If the issue persists, ensure `NEO4J_URI` is not set as a global environment variable in Coolify - it should only be used by the backend service.
