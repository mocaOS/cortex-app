# Cortex Library Heartbeat

*Run this periodically (every 4+ hours) to keep your memories synced to the knowledge graph.*

## First: Check Your Credentials

```bash
cat ~/.openclaw/skills/library/state/credentials.json 2>/dev/null
```

**If file doesn't exist or is missing `api_key` OR `base_url`:**

Stop here and tell your human:

> "I need BOTH a Cortex Library base URL AND an API key to sync my memories. Please provide:
> 1. The base URL of your Cortex Library (e.g., https://library.example.com)
> 2. An API key from YOUR_BASE_URL/admin -> API Keys"

Once they provide BOTH values, save them:

```bash
mkdir -p ~/.openclaw/skills/library/state
cat > ~/.openclaw/skills/library/state/credentials.json << 'EOF'
{
  "api_key": "THE_API_KEY_THEY_PROVIDED",
  "base_url": "THE_BASE_URL_THEY_PROVIDED",
  "collection_id": null
}
EOF
```

---

## Validate API Connection

```bash
API_KEY=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.api_key')
API_BASE=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.base_url')

if [ -z "$API_KEY" ] || [ "$API_KEY" = "null" ] || [ -z "$API_BASE" ] || [ "$API_BASE" = "null" ]; then
  echo "Missing credentials. Both api_key and base_url are required."
  exit 1
fi

curl -s "$API_BASE/health" -H "X-API-Key: $API_KEY"
```

**Expected:** `{"status": "healthy", ...}`

---

## Ensure Collection Exists (MANDATORY)

All memory files MUST be uploaded to the configured collection. Always look up by name, not cached ID.

```bash
API_KEY=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.api_key')
API_BASE=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.base_url')
COLLECTION_NAME="OpenClaw"

if [ -z "$API_KEY" ] || [ "$API_KEY" = "null" ] || [ -z "$API_BASE" ] || [ "$API_BASE" = "null" ]; then
  echo "Missing credentials."
  exit 1
fi

COLLECTIONS=$(curl -s "$API_BASE/api/collections" -H "X-API-Key: $API_KEY")

if [ -z "$COLLECTIONS" ] || [ "$(echo "$COLLECTIONS" | jq -r '.collections // empty')" = "" ]; then
  echo "Could not fetch collections from API"
  exit 1
fi

COLLECTION_ID=$(echo "$COLLECTIONS" | jq -r ".collections[] | select(.name == \"$COLLECTION_NAME\") | .id" | head -n1)

if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
  CREATE_RESULT=$(curl -s -X POST "$API_BASE/api/collections" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$COLLECTION_NAME\", \"description\": \"Memory files synced from agent\"}")
  COLLECTION_ID=$(echo "$CREATE_RESULT" | jq -r '.id')
  if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
    echo "Failed to create collection"
    exit 1
  fi
fi

jq --arg cid "$COLLECTION_ID" '.collection_id = $cid' \
  ~/.openclaw/skills/library/state/credentials.json > ~/.openclaw/skills/library/state/credentials.json.tmp
mv ~/.openclaw/skills/library/state/credentials.json.tmp ~/.openclaw/skills/library/state/credentials.json
```

---

## Upload API Parameter Format

**CRITICAL:** `collection_id` and `start_processing` MUST be URL query parameters:

```bash
# CORRECT:
curl -X POST "$API_BASE/api/upload?collection_id=$COLLECTION_ID&start_processing=false" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@$FILE_PATH"

# WRONG (will not work):
# curl -X POST "$API_BASE/api/upload" -F "collection_id=$COLLECTION_ID" -F "file=@$FILE_PATH"
```

---

## Scan Memory Directories

Check these directories for memory files to sync:

1. `~/.openclaw/memory/` - Primary memory storage
2. `~/.openclaw/conversations/` - Conversation logs
3. **QMD sessions** - When QMD is enabled
4. Custom paths configured by your human

**Supported file types:** `.md`, `.txt`, `.json`

### Detect QMD

```bash
MEMORY_BACKEND=$(cat ~/.openclaw/openclaw.json 2>/dev/null | jq -r '.memory.backend // "sqlite"')
if [ "$MEMORY_BACKEND" = "qmd" ]; then
  AGENT_ID=$(cat ~/.openclaw/openclaw.json 2>/dev/null | jq -r '.agents.defaults.id // "main"')
  QMD_SESSIONS_DIR="$HOME/.openclaw/agents/$AGENT_ID/qmd/sessions"
  [ -d "$QMD_SESSIONS_DIR" ] && MEMORY_DIRS+=("$QMD_SESSIONS_DIR")
fi
```

### Load Upload Tracking

```bash
if [ ! -f ~/.openclaw/skills/library/state/uploaded_files.json ]; then
  echo '{"files": {}, "last_sync": null}' > ~/.openclaw/skills/library/state/uploaded_files.json
fi
UPLOADED=$(cat ~/.openclaw/skills/library/state/uploaded_files.json)
```

### Find New or Modified Files

For each file:
1. Calculate SHA-256 hash
2. Compare with stored hash in tracking file
3. Upload if new or changed

---

## Upload Files

### Step 1: Upload Without Processing

```bash
for FILE in "$DIR"/*.md "$DIR"/*.txt "$DIR"/*.json; do
  [ ! -f "$FILE" ] && continue

  CURRENT_HASH=$(sha256sum "$FILE" 2>/dev/null || shasum -a 256 "$FILE" | cut -d' ' -f1)
  STORED_HASH=$(jq -r --arg p "$FILE" '.files[$p].hash // ""' "$TRACKING_FILE")

  [ "$CURRENT_HASH" = "$STORED_HASH" ] && continue

  RESULT=$(curl -s -X POST "$API_BASE/api/upload?collection_id=$COLLECTION_ID&start_processing=false" \
    -H "X-API-Key: $API_KEY" \
    -F "file=@$FILE")

  DOCUMENT_ID=$(echo "$RESULT" | jq -r '.document_id // .doc_id')
done
```

### Step 2: Trigger Batch Processing

```bash
PROCESS_RESULT=$(curl -s -X POST "$API_BASE/api/documents/process-pending" \
  -H "X-API-Key: $API_KEY")
TASK_ID=$(echo "$PROCESS_RESULT" | jq -r '.task_id')
```

### Step 3: Wait for Processing

```bash
MAX_ATTEMPTS=60
ATTEMPT=0
while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
  TASK_STATUS=$(curl -s "$API_BASE/api/tasks/$TASK_ID" -H "X-API-Key: $API_KEY")
  STATUS=$(echo "$TASK_STATUS" | jq -r '.status' | tr '[:upper:]' '[:lower:]')
  [ "$STATUS" = "completed" ] && echo "All documents processed!" && break
  [ "$STATUS" = "failed" ] && echo "Processing failed" && break
  ATTEMPT=$((ATTEMPT + 1))
  sleep 5
done
```

---

## Update Tracking (Immediately After Each Upload)

```bash
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
jq --arg path "$FILE_PATH" \
   --arg hash "$FILE_HASH" \
   --arg docid "$DOCUMENT_ID" \
   --arg time "$TIMESTAMP" \
   --arg status "uploaded" \
   '.files[$path] = {"hash": $hash, "document_id": $docid, "uploaded_at": $time, "status": $status} | .last_sync = $time' \
   "$TRACKING_FILE" > "$TRACKING_FILE.tmp"
mv "$TRACKING_FILE.tmp" "$TRACKING_FILE"
```

---

## Summary Response Format

**Nothing to sync:**
```
HEARTBEAT_OK - Library sync complete. No new memory files to upload.
```

**Files uploaded:**
```
Library sync complete - Uploaded 5 new memory files:
- conversation_2026-02-05.md
- project_notes.md
Total: 47 documents in collection
```

**Errors:**
```
Library sync completed with issues:
- Uploaded: 2 files
- Failed: 1 file (too large)
Will retry on next sync.
```

---

## When to Tell Your Human

**Do tell them:** First-time setup needs credentials, API key invalid/expired, many files failed (>10%)

**Don't bother them:** Routine syncs, skipped files (already synced), normal operation

---

## Sync Frequency

**Recommended:** Every 4-6 hours during active use

```bash
LAST_SYNC=$(jq -r '.last_sync // "1970-01-01T00:00:00Z"' ~/.openclaw/skills/library/state/uploaded_files.json)
LAST_EPOCH=$(date -d "$LAST_SYNC" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%SZ" "$LAST_SYNC" +%s)
NOW_EPOCH=$(date +%s)
HOURS_SINCE=$(( (NOW_EPOCH - LAST_EPOCH) / 3600 ))

if [ $HOURS_SINCE -ge 4 ]; then
  echo "Time to sync! Last sync was $HOURS_SINCE hours ago."
fi
```
