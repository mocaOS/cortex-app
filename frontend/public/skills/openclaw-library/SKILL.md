---
name: openclaw-library
version: 1.0.0
description: Sync OpenClaw memory files to MOCA Library knowledge graph for enhanced retrieval and search.
homepage: https://library.moca.qwellco.de
metadata: {"openclaw":{"emoji":"📚","category":"knowledge","api_base":"https://library.moca.qwellco.de"}}
---

# OpenClaw Library Skill

Sync your OpenClaw memory files **exclusively to the `OpenClaw` collection** in the MOCA Library knowledge graph. Upload documents, search your knowledge base, and ask AI-powered questions about your accumulated memories.

All memory files are organized within a single dedicated collection named **OpenClaw**. If this collection doesn't exist, the skill automatically creates it.

## Skill Files

| File | Description |
|------|-------------|
| **SKILL.md** (this file) | Main skill documentation and API reference |
| **HEARTBEAT.md** | Periodic sync tasks and memory upload workflow |
| **skill.json** | Skill metadata and configuration |

**Install locally:**
```bash
mkdir -p ~/.openclaw/skills/library
curl -s https://library.moca.qwellco.de/skills/openclaw-library/SKILL.md > ~/.openclaw/skills/library/SKILL.md
curl -s https://library.moca.qwellco.de/skills/openclaw-library/HEARTBEAT.md > ~/.openclaw/skills/library/HEARTBEAT.md
curl -s https://library.moca.qwellco.de/skills/openclaw-library/skill.json > ~/.openclaw/skills/library/skill.json
mkdir -p ~/.openclaw/skills/library/state
```

**Base URL:** `https://library.moca.qwellco.de`

---

## First-Time Setup

### Step 1: Get Your API Key

You need a MOCA Library API key to use this skill. Ask your human to:

1. Log into the Library at https://library.moca.qwellco.de
2. Go to Admin → API Keys
3. Create a new API key with **READ** and **MANAGE** permissions
4. Copy the API key (it's only shown once!)

### Step 2: Configure Credentials

When you first use this skill, check if credentials exist:

```bash
cat ~/.openclaw/skills/library/state/credentials.json
```

**If the file doesn't exist or has no valid `api_key`:**

Tell your human:
> "I need a MOCA Library API key to sync memories. Please provide an API key from https://library.moca.qwellco.de/admin/api-keys"

Once they provide the key, save it:

```bash
mkdir -p ~/.openclaw/skills/library/state
cat > ~/.openclaw/skills/library/state/credentials.json << 'EOF'
{
  "api_key": "YOUR_API_KEY_HERE",
  "collection_id": null
}
EOF
```

### Step 3: Validate the API Key

Test the connection:

```bash
curl -s "https://library.moca.qwellco.de/health" \
  -H "X-API-Key: YOUR_API_KEY"
```

Expected response:
```json
{"status": "healthy", "neo4j_connected": true, "version": "2.0.0"}
```

**If you get an error:** The API key is invalid. Ask your human for a valid key.

### Step 4: Find or Create OpenClaw Collection (REQUIRED)

**This step is mandatory.** All memory files MUST be uploaded to the `OpenClaw` collection.

**IMPORTANT:** ALWAYS look up the collection by NAME, not by cached ID. This ensures you always have the correct collection.

```bash
API_KEY=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.api_key')
API_BASE="https://library.moca.qwellco.de"
COLLECTION_NAME="OpenClaw"

# ALWAYS query the API to find the collection by name
echo "🔍 Finding OpenClaw collection..."
COLLECTIONS=$(curl -s "$API_BASE/api/collections" -H "X-API-Key: $API_KEY")

# Find collection by exact name match
COLLECTION_ID=$(echo "$COLLECTIONS" | jq -r ".collections[] | select(.name == \"$COLLECTION_NAME\") | .id" | head -n1)

# If not found, create it
if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
  echo "📚 Creating OpenClaw collection..."
  CREATE_RESULT=$(curl -s -X POST "$API_BASE/api/collections" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$COLLECTION_NAME\", \"description\": \"Memory files synced from OpenClaw agent\"}")
  
  COLLECTION_ID=$(echo "$CREATE_RESULT" | jq -r '.id')
  
  if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
    echo "❌ Failed to create collection"
    echo "   Error: $(echo "$CREATE_RESULT" | jq -r '.detail // .message // "Unknown"')"
    exit 1
  fi
  echo "✅ Created OpenClaw collection: $COLLECTION_ID"
else
  echo "✅ Found OpenClaw collection: $COLLECTION_ID"
fi

# Save the verified collection ID to credentials
jq --arg cid "$COLLECTION_ID" '.collection_id = $cid' \
  ~/.openclaw/skills/library/state/credentials.json > ~/.openclaw/skills/library/state/credentials.json.tmp
mv ~/.openclaw/skills/library/state/credentials.json.tmp ~/.openclaw/skills/library/state/credentials.json
echo "💾 Collection ID saved: $COLLECTION_ID"

# Verify we have a valid ID
if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
  echo "❌ FATAL: No valid collection ID. Cannot proceed."
  exit 1
fi
```

**CRITICAL:** Never proceed with uploads if `collection_id` is null or empty. Always ensure the OpenClaw collection exists first.

---

## The OpenClaw Collection

**IMPORTANT:** All memory files are uploaded EXCLUSIVELY to the `OpenClaw` collection in the MOCA Library. This keeps your memories organized and separate from other content.

**Before any upload operation:**
1. Check if `collection_id` exists in credentials
2. If not, search for a collection named "OpenClaw"
3. If "OpenClaw" doesn't exist, create it
4. Save the `collection_id` to credentials for future use

**Never upload to other collections.** The skill automatically ensures all files go to the OpenClaw collection.

---

## Memory Directories

This skill syncs files from your memory directories to the OpenClaw collection in the knowledge graph.

**Default memory locations:**
- `~/.openclaw/memory/` - Primary memory storage
- `~/.openclaw/conversations/` - Conversation logs
- Custom paths configured by your human

**Supported file types:**
- `.md` - Markdown files
- `.txt` - Plain text files
- `.json` - JSON data files

---

## Upload Tracking

To avoid uploading the same file twice, this skill tracks uploaded files in:

```
~/.openclaw/skills/library/state/uploaded_files.json
```

Format:
```json
{
  "files": {
    "/path/to/memory.md": {
      "hash": "sha256_hash_of_content",
      "document_id": "doc_xxx",
      "uploaded_at": "2026-02-05T10:30:00Z",
      "status": "completed"
    }
  },
  "last_sync": "2026-02-05T10:30:00Z"
}
```

**How it works:**
1. Calculate SHA-256 hash of file content
2. Check if hash exists in `uploaded_files.json`
3. If hash matches → file already uploaded, skip
4. If hash differs or file is new → upload and update tracking

---

## API Reference

All requests require the `X-API-Key` header with your API key.

### Health Check

```bash
curl "https://library.moca.qwellco.de/health"
```

### List Collections

```bash
curl "https://library.moca.qwellco.de/api/collections" \
  -H "X-API-Key: YOUR_API_KEY"
```

### Create Collection

```bash
curl -X POST "https://library.moca.qwellco.de/api/collections" \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "OpenClaw", "description": "Memory files from OpenClaw agent"}'
```

### Upload a File (Single)

For uploading a single file with immediate processing:

```bash
curl -X POST "https://library.moca.qwellco.de/api/upload" \
  -H "X-API-Key: YOUR_API_KEY" \
  -F "file=@/path/to/memory.md" \
  -F "collection_id=YOUR_COLLECTION_ID" \
  -F "start_processing=true"
```

Response:
```json
{
  "document_id": "doc_xxx",
  "filename": "memory.md",
  "status": "processing",
  "message": "Document uploaded and processing started"
}
```

### Upload Files (Bulk - Recommended for Multiple Files)

For uploading multiple files efficiently, upload without processing first, then trigger batch processing:

**Step 1: Upload files without processing**
```bash
curl -X POST "https://library.moca.qwellco.de/api/upload" \
  -H "X-API-Key: YOUR_API_KEY" \
  -F "file=@/path/to/memory.md" \
  -F "collection_id=YOUR_COLLECTION_ID" \
  -F "start_processing=false"
```

Response:
```json
{
  "document_id": "doc_xxx",
  "filename": "memory.md",
  "status": "pending",
  "message": "Document uploaded successfully"
}
```

**Step 2: Trigger batch processing**
```bash
curl -X POST "https://library.moca.qwellco.de/api/documents/process-pending" \
  -H "X-API-Key: YOUR_API_KEY"
```

Response:
```json
{
  "task_id": "task_xxx",
  "status": "running",
  "pending_count": 5,
  "concurrency": 10,
  "message": "Processing 5 pending documents"
}
```

### Get Pending Documents

Check which documents are waiting to be processed:

```bash
curl "https://library.moca.qwellco.de/api/documents/pending" \
  -H "X-API-Key: YOUR_API_KEY"
```

Response:
```json
{
  "pending_count": 3,
  "documents": [
    {"id": "doc_xxx", "filename": "memory1.md", "status": "pending"},
    {"id": "doc_yyy", "filename": "memory2.md", "status": "pending"}
  ]
}
```

### Check Document Status

```bash
curl "https://library.moca.qwellco.de/api/documents/DOCUMENT_ID" \
  -H "X-API-Key: YOUR_API_KEY"
```

Status values:
- `pending` - Waiting for processing
- `processing` - Currently being processed
- `extracting` - Extracting entities and relationships
- `completed` - Successfully processed
- `failed` - Processing failed

### Search Knowledge Base

```bash
curl -X POST "https://library.moca.qwellco.de/api/search" \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "your search query", "top_k": 10}'
```

### Ask AI (RAG Query)

```bash
curl -X POST "https://library.moca.qwellco.de/api/ask" \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What do I know about topic X?",
    "top_k": 5,
    "use_graph": true
  }'
```

Response:
```json
{
  "question": "What do I know about topic X?",
  "answer": "Based on your memories...",
  "sources": [...],
  "graph_context": {...}
}
```

### Get Statistics

```bash
curl "https://library.moca.qwellco.de/api/stats" \
  -H "X-API-Key: YOUR_API_KEY"
```

---

## Common Operations

### Upload All New Memory Files (Bulk Upload)

For efficiency, upload all files first without processing, then trigger batch processing.

**All files are uploaded to the OpenClaw collection ONLY.**

```bash
# Get credentials
API_KEY=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.api_key')
COLLECTION_ID=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.collection_id')

# ALWAYS look up the OpenClaw collection by name (don't trust cached ID)
echo "🔍 Finding OpenClaw collection by name..."
COLLECTIONS=$(curl -s "https://library.moca.qwellco.de/api/collections" -H "X-API-Key: $API_KEY")

# Find collection by exact name match
COLLECTION_ID=$(echo "$COLLECTIONS" | jq -r '.collections[] | select(.name == "OpenClaw") | .id' | head -n1)

# If not found, create it
if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
  echo "📚 Creating OpenClaw collection..."
  CREATE_RESULT=$(curl -s -X POST "https://library.moca.qwellco.de/api/collections" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"name": "OpenClaw", "description": "Memory files synced from OpenClaw agent"}')
  COLLECTION_ID=$(echo "$CREATE_RESULT" | jq -r '.id')
  
  if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
    echo "❌ Failed to create collection. Cannot proceed."
    exit 1
  fi
  echo "✅ Created OpenClaw collection: $COLLECTION_ID"
else
  echo "✅ Found OpenClaw collection: $COLLECTION_ID"
fi

# Save the verified collection ID to credentials
jq --arg cid "$COLLECTION_ID" '.collection_id = $cid' \
  ~/.openclaw/skills/library/state/credentials.json > ~/.openclaw/skills/library/state/credentials.json.tmp
mv ~/.openclaw/skills/library/state/credentials.json.tmp ~/.openclaw/skills/library/state/credentials.json

# Step 1: Upload all files to OpenClaw collection WITHOUT processing (faster)
echo "📤 Uploading to collection: $COLLECTION_ID"
UPLOADED_COUNT=0
for file in ~/.openclaw/memory/*.{md,txt,json} 2>/dev/null; do
  if [ -f "$file" ]; then
    echo "   📄 $(basename "$file") -> $COLLECTION_ID"
    RESULT=$(curl -s -X POST "https://library.moca.qwellco.de/api/upload" \
      -H "X-API-Key: $API_KEY" \
      -F "file=@$file" \
      -F "collection_id=$COLLECTION_ID" \
      -F "start_processing=false")
    
    # Verify the upload succeeded and went to the right collection
    DOC_COLLECTION=$(echo "$RESULT" | jq -r '.collection_id // "unknown"')
    if [ "$DOC_COLLECTION" != "$COLLECTION_ID" ] && [ "$DOC_COLLECTION" != "unknown" ]; then
      echo "      ⚠️ Warning: Document uploaded to $DOC_COLLECTION instead of $COLLECTION_ID"
    fi
    
    UPLOADED_COUNT=$((UPLOADED_COUNT + 1))
  fi
done

echo ""
echo "📦 Uploaded $UPLOADED_COUNT files. Starting batch processing..."

# Step 2: Trigger processing for all pending documents
PROCESS_RESULT=$(curl -s -X POST "https://library.moca.qwellco.de/api/documents/process-pending" \
  -H "X-API-Key: $API_KEY")

TASK_ID=$(echo "$PROCESS_RESULT" | jq -r '.task_id')
echo "🔄 Processing started. Task ID: $TASK_ID"

# Step 3: Wait for processing to complete (optional)
while true; do
  TASK_STATUS=$(curl -s "https://library.moca.qwellco.de/api/tasks/$TASK_ID" \
    -H "X-API-Key: $API_KEY")
  
  STATUS=$(echo "$TASK_STATUS" | jq -r '.status')
  PROGRESS=$(echo "$TASK_STATUS" | jq -r '.progress_percent // 0')
  
  echo "   Progress: ${PROGRESS}%"
  
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "COMPLETED" ]; then
    echo "✅ All documents processed!"
    break
  elif [ "$STATUS" = "failed" ] || [ "$STATUS" = "FAILED" ]; then
    echo "❌ Processing failed"
    break
  fi
  
  sleep 5
done
```

### Upload Single File (Immediate Processing)

For a single file with immediate processing (uploads to OpenClaw collection ONLY):

```bash
API_KEY=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.api_key')
API_BASE="https://library.moca.qwellco.de"
COLLECTION_NAME="OpenClaw"

# ALWAYS look up the OpenClaw collection by name before uploading
echo "🔍 Finding OpenClaw collection..."
COLLECTIONS=$(curl -s "$API_BASE/api/collections" -H "X-API-Key: $API_KEY")
COLLECTION_ID=$(echo "$COLLECTIONS" | jq -r ".collections[] | select(.name == \"$COLLECTION_NAME\") | .id" | head -n1)

if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
  echo "❌ Error: OpenClaw collection not found. Create it first."
  exit 1
fi

echo "✅ Uploading to OpenClaw collection: $COLLECTION_ID"

# Upload to OpenClaw collection
curl -X POST "$API_BASE/api/upload" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@/path/to/memory.md" \
  -F "collection_id=$COLLECTION_ID" \
  -F "start_processing=true"
```

### Search Your Memories

```bash
API_KEY=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.api_key')

curl -X POST "https://library.moca.qwellco.de/api/search" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "what I discussed about project X"}'
```

### Ask a Question About Your Knowledge

```bash
API_KEY=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.api_key')

curl -X POST "https://library.moca.qwellco.de/api/ask" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"question": "Summarize what I know about machine learning"}'
```

---

## Error Handling

### Missing API Key

If `credentials.json` doesn't exist or `api_key` is null:

```
⚠️ MOCA Library API key not configured!

I need an API key to sync memories to the Library. Please:
1. Go to https://library.moca.qwellco.de/admin/api-keys
2. Create an API key with READ and MANAGE permissions
3. Give me the key so I can save it

Without an API key, I cannot upload memories or search the knowledge base.
```

### Invalid API Key

If health check fails with 401:

```
⚠️ Invalid MOCA Library API key!

The API key you provided is not valid or has been revoked.
Please provide a new API key from https://library.moca.qwellco.de/admin/api-keys
```

### Collection Not Found

If the OpenClaw collection was deleted:

```
📚 OpenClaw collection not found. Creating a new one...
```

Then create the collection and update `credentials.json` with the new `collection_id`.

### Upload Failed

If an upload fails:

```
❌ Failed to upload {filename}: {error message}
Will retry on next sync.
```

Don't add failed files to `uploaded_files.json` so they'll be retried.

---

## When to Sync

**Automatic sync (via heartbeat):**
- Every 4+ hours during normal operation
- Check for new/modified files in memory directories

**Manual sync:**
- When your human says "sync memories to library"
- When you've written important new memories
- Before answering questions that might benefit from historical context

**Don't sync:**
- Every few minutes (too frequent, wastes resources)
- Empty or temporary files
- Files that are still being written

---

## Integration with Heartbeat

Add this to your HEARTBEAT.md:

```markdown
## Library Sync (every 4+ hours)
If 4+ hours since last library sync:
1. Fetch HEARTBEAT.md from library skill and follow it
2. Update lastLibrarySync timestamp in memory
```

See [HEARTBEAT.md](./HEARTBEAT.md) for detailed sync instructions.

---

## Rate Limits

The MOCA Library API has reasonable rate limits:
- Normal operations: 100+ requests/minute
- File uploads: No specific limit, but be respectful
- Large batch uploads: Use `start_processing=false` and batch process

---

## Your Human Can Ask Anytime

Your human might prompt you to:
- "Sync your memories to the library"
- "Search your knowledge for X"
- "What do you know about Y?"
- "Upload this conversation to your memory"
- "Check if your memories are up to date"

You don't have to wait for heartbeat - if they ask, do it!

---

## Troubleshooting

### Can't connect to API

```bash
# Check if the API is reachable
curl -v "https://library.moca.qwellco.de/health"
```

If connection fails, the service might be down. Try again later.

### File not appearing in library

1. Check upload status: `GET /api/documents/{document_id}`
2. If status is "failed", check `error_message` field
3. Try reprocessing: `POST /api/documents/{document_id}/reprocess`

### Out of sync with uploaded_files.json

If the tracking file gets corrupted or out of sync:

```bash
# Reset tracking (will re-upload all files on next sync)
echo '{"files": {}, "last_sync": null}' > ~/.openclaw/skills/library/state/uploaded_files.json
```

---

## Privacy & Security

- Your memories are stored in your own MOCA Library instance
- API keys should be kept secure and not shared
- The skill only uploads files from designated memory directories
- You control what gets synced

---

## Version History

- **1.0.0** - Initial release with memory sync, search, and RAG capabilities
