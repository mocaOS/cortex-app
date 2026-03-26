# Cortex Library Skill

An [AgentSkills](https://agentskills.io)-compatible skill that syncs agent memory files to a Cortex Library knowledge graph.

## What It Does

This skill enables AI agents to:

- **Upload memory files** to a dedicated collection for organized storage
- **Search knowledge** using hybrid search (vector + keyword + graph traversal with RRF fusion)
- **Ask AI questions** with agentic deep research (multi-step reasoning, up to 10 iterations)
- **Auto-sync** new memories during periodic heartbeat cycles
- **Manage collections** to organize documents into logical groups
- **Add custom inputs** (Q&A pairs, text, markdown) without uploading files

## AgentSkills Standard

This skill follows the [AgentSkills open standard](https://agentskills.io/specification):

```
library/
  SKILL.md            # Required: metadata + instructions
  HEARTBEAT.md        # Periodic sync workflow
  references/         # Detailed documentation (progressive disclosure)
    API.md            # Full API reference (60+ endpoints)
    SYNC.md           # Detailed sync workflow and troubleshooting
  scripts/            # Executable sync scripts
    sync.sh           # Bash sync script
    sync.py           # Python sync script
    sync_bulk.py      # Python bulk sync script
  state/              # Runtime state
    credentials.example.json
    uploaded_files.json
```

## Quick Start

### 1. Install the Skill

```bash
mkdir -p ~/.openclaw/skills/library
cp -r . ~/.openclaw/skills/library/
```

### 2. Configure Credentials

Get an API key from your Cortex Library instance (`YOUR_BASE_URL/admin` -> API Keys):

```bash
cp state/credentials.example.json state/credentials.json
# Edit state/credentials.json and add your API key AND base URL
```

### 3. Start Syncing

The skill will automatically:
- Find or create the collection (all files go here exclusively)
- Scan memory directories for `.md`, `.txt`, `.json` files
- Upload new/modified files with SHA-256 dedup tracking
- Trigger batch processing

## Cortex Library Features

| Feature | Description |
|---------|-------------|
| **Document Processing** | PDF, DOCX, TXT, MD, XLSX, PPTX, images (50MB max) |
| **Hybrid Search** | Vector (0.5) + Keyword (0.3) + Graph (0.2) with reranking |
| **AI Q&A** | Chat mode (fast) and Deep Research mode (agentic, 10 iterations) |
| **Knowledge Graph** | 10 entity types, 14 relationship types, confidence scoring, community detection |
| **Collections** | Multi-tenant document organization with scoped search |
| **Entity Dedup** | Fuzzy matching with merge/dismiss workflow |
| **Image Analysis** | Concurrent vision model processing, integrated into RAG |
| **Streaming** | SSE for real-time Q&A, reasoning, and token delivery |
| **GPU Acceleration** | Compute3 integration (H100/A100) |
| **60+ API Endpoints** | Full REST API with auth and permissions |

## Requirements

- `curl` - For API requests
- `jq` - For JSON parsing (falls back to `python3`)
- A valid Cortex Library API key with READ and MANAGE permissions
- Network access to a Cortex Library instance

## License

MIT
