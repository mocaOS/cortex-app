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

### 3. Configure Environment Variables

In Coolify's environment settings, add:

```
NEO4J_USER=neo4j
NEO4J_PASSWORD=<strong-password>
OPENAI_API_KEY=sk-your-key-here
NEXT_PUBLIC_API_URL=https://your-domain.com
```

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
