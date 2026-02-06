# OpenClaw Library Skill

An OpenClaw/Moltbot skill that syncs memory files **exclusively to the `OpenClaw` collection** in the MOCA Library knowledge graph.

## What It Does

This skill enables OpenClaw AI agents to:

- **Upload memory files** to the dedicated `OpenClaw` collection for organized storage
- **Search their knowledge** using hybrid vector + keyword + graph search
- **Ask AI questions** about their accumulated memories with RAG
- **Auto-sync** new memories during periodic heartbeat cycles

**Important:** All files are uploaded ONLY to the `OpenClaw` collection. The skill automatically finds or creates this collection before any uploads.

## Quick Start

### 1. Install the Skill

```bash
mkdir -p ~/.openclaw/skills/library
cp -r . ~/.openclaw/skills/library/
```

### 2. Configure API Key

Get an API key from https://library.moca.qwellco.de/admin/api-keys, then:

```bash
cp state/credentials.example.json state/credentials.json
# Edit state/credentials.json and add your API key
```

### 3. Start Syncing

The skill will automatically:
- **Find or create the "OpenClaw" collection** (all files go here exclusively)
- Scan memory directories for `.md`, `.txt`, `.json` files
- Upload new/modified files to the OpenClaw collection only
- Track uploads to avoid duplicates

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Full documentation and API reference |
| `HEARTBEAT.md` | Periodic sync workflow |
| `skill.json` | Skill metadata and configuration |
| `state/credentials.example.json` | Template for API credentials |
| `state/uploaded_files.json` | Upload tracking state |

## API Base URL

```
https://library.moca.qwellco.de
```

## Requirements

- `curl` - For API requests
- `jq` - For JSON parsing
- A valid MOCA Library API key with READ and MANAGE permissions

## Memory Directories

By default, the skill scans:
- `~/.openclaw/memory/`
- `~/.openclaw/conversations/`

Supported file types: `.md`, `.txt`, `.json`

## Usage Examples

### Search Knowledge

```bash
curl -X POST "https://library.moca.qwellco.de/api/search" \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "project notes"}'
```

### Ask AI

```bash
curl -X POST "https://library.moca.qwellco.de/api/ask" \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "What do I know about machine learning?"}'
```

## License

MIT
