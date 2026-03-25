# Detailed Sync Workflow

Complete guide for syncing agent memory files to Cortex Library, including QMD support, fallback strategies, and troubleshooting.

---

## Upload Tracking

Files are tracked in `~/.openclaw/skills/library/state/uploaded_files.json`:

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
2. Check if hash exists in tracking file
3. If hash matches -> skip (already uploaded)
4. If hash differs or file is new -> upload and update tracking

**CRITICAL:** Save tracking immediately after each upload, not batched at the end.

---

## QMD Memory Detection

The skill detects if QMD (Quick Memory Daemon) is enabled and includes session files.

```bash
MEMORY_BACKEND=$(cat ~/.openclaw/openclaw.json 2>/dev/null | jq -r '.memory.backend // "sqlite"')

if [ "$MEMORY_BACKEND" = "qmd" ]; then
  AGENT_ID=$(cat ~/.openclaw/openclaw.json 2>/dev/null | jq -r '.agents.defaults.id // "main"')
  QMD_SESSIONS_DIR="$HOME/.openclaw/agents/$AGENT_ID/qmd/sessions"
  if [ -d "$QMD_SESSIONS_DIR" ]; then
    MEMORY_DIRS+=("$QMD_SESSIONS_DIR")
  fi
fi
```

QMD can accumulate many session files (70+). For large batches:
- Upload all files first without processing
- Trigger single batch processing job
- Poll for ~5 minutes, then let it run server-side

---

## Complete Sync Script (Bash with Python Fallback)

```bash
#!/bin/bash
# Cortex Library Sync Script
# Works with or without jq (falls back to Python 3)
# Updates tracking immediately after each upload
# Handles QMD session files automatically
# All files uploaded to the configured collection ONLY

set -e

STATE_DIR="$HOME/.openclaw/skills/library/state"
CREDENTIALS="$STATE_DIR/credentials.json"
TRACKING="$STATE_DIR/uploaded_files.json"
COLLECTION_NAME="OpenClaw"

HAS_JQ=$(command -v jq &> /dev/null && echo "yes" || echo "no")

json_get() {
  if [ "$HAS_JQ" = "yes" ]; then
    jq -r "$1 // empty"
  else
    python3 -c "import json,sys; d=json.load(sys.stdin); print(eval('d' + '$1'.replace('.', \"['\").rstrip(']') + \"']\") if d else '')" 2>/dev/null
  fi
}

json_select_collection() {
  local NAME="$1"
  if [ "$HAS_JQ" = "yes" ]; then
    jq -r ".collections[] | select(.name == \"$NAME\") | .id" | head -n1
  else
    python3 -c "import json,sys; d=json.load(sys.stdin); ids=[c['id'] for c in d.get('collections',[]) if c.get('name')=='$NAME']; print(ids[0] if ids else '')"
  fi
}

# Memory directories
MEMORY_DIRS=(
  "$HOME/.openclaw/memory"
  "$HOME/.openclaw/conversations"
)

# Detect QMD
MEMORY_BACKEND=$(cat ~/.openclaw/openclaw.json 2>/dev/null | jq -r '.memory.backend // "sqlite"' 2>/dev/null || echo "sqlite")
if [ "$MEMORY_BACKEND" = "qmd" ]; then
  AGENT_ID="main"
  QMD_DIR="$HOME/.openclaw/agents/$AGENT_ID/qmd/sessions"
  [ -d "$QMD_DIR" ] && MEMORY_DIRS+=("$QMD_DIR")
fi

echo "Cortex Library Sync"
echo "  Backend: $MEMORY_BACKEND | Directories: ${#MEMORY_DIRS[@]}"

# Load credentials
if [ ! -f "$CREDENTIALS" ]; then
  echo "No credentials found. Please provide api_key and base_url."
  exit 1
fi

API_KEY=$(jq -r '.api_key' "$CREDENTIALS" 2>/dev/null || python3 -c "import json; print(json.load(open('$CREDENTIALS')).get('api_key',''))")
API_BASE=$(jq -r '.base_url' "$CREDENTIALS" 2>/dev/null || python3 -c "import json; print(json.load(open('$CREDENTIALS')).get('base_url',''))")

if [ -z "$API_KEY" ] || [ "$API_KEY" = "null" ] || [ -z "$API_BASE" ] || [ "$API_BASE" = "null" ]; then
  echo "Missing credentials. Both api_key and base_url are required."
  exit 1
fi

# Initialize tracking
[ ! -f "$TRACKING" ] && echo '{"files": {}, "last_sync": null}' > "$TRACKING"

# Find/create collection
COLLECTIONS=$(curl -s "$API_BASE/api/collections" -H "X-API-Key: $API_KEY")
COLLECTION_ID=$(echo "$COLLECTIONS" | json_select_collection "$COLLECTION_NAME")

if [ -z "$COLLECTION_ID" ]; then
  CREATE_RESULT=$(curl -s -X POST "$API_BASE/api/collections" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$COLLECTION_NAME\", \"description\": \"Memory files synced from agent\"}")
  COLLECTION_ID=$(echo "$CREATE_RESULT" | jq -r '.id' 2>/dev/null || python3 -c "import json,sys; print(json.load(sys.stdin).get('id',''))" <<< "$CREATE_RESULT")
  if [ -z "$COLLECTION_ID" ]; then
    echo "Failed to create collection"
    exit 1
  fi
fi

# Update credentials with collection ID
python3 << PYEOF
import json
with open("$CREDENTIALS") as f:
    d = json.load(f)
d['collection_id'] = '$COLLECTION_ID'
with open("$CREDENTIALS", 'w') as f:
    json.dump(d, f, indent=2)
PYEOF

# Upload files
UPLOAD_COUNT=0
SKIP_COUNT=0

update_tracking() {
  python3 - "$TRACKING" "$1" "$2" "$3" "$4" "$5" << 'PYEOF'
import json, sys
path, file_path, file_hash, doc_id, ts, status = sys.argv[1:7]
with open(path) as f:
    d = json.load(f)
if 'files' not in d:
    d['files'] = {}
d['files'][file_path] = {
    'hash': file_hash,
    'document_id': doc_id,
    'uploaded_at': ts,
    'status': status
}
d['last_sync'] = ts
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
PYEOF
}

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

for DIR in "${MEMORY_DIRS[@]}"; do
  [ ! -d "$DIR" ] && continue
  for PATTERN in "$DIR"/*.md "$DIR"/*.txt "$DIR"/*.json; do
    for FILE in $PATTERN; do
      [ ! -f "$FILE" ] && continue

      if command -v sha256sum &> /dev/null; then
        CURRENT_HASH=$(sha256sum "$FILE" | cut -d' ' -f1)
      else
        CURRENT_HASH=$(shasum -a 256 "$FILE" | cut -d' ' -f1)
      fi

      STORED_HASH=$(jq -r --arg p "$FILE" '.files[$p].hash // ""' "$TRACKING" 2>/dev/null || python3 -c "import json; print(json.load(open('$TRACKING')).get('files',{}).get('$FILE',{}).get('hash',''))")

      if [ "$CURRENT_HASH" = "$STORED_HASH" ] && [ -n "$STORED_HASH" ]; then
        SKIP_COUNT=$((SKIP_COUNT + 1))
        continue
      fi

      echo "  Uploading: $(basename "$FILE")"

      # Upload with URL query parameters (NOT form fields)
      RESULT=$(curl -s -X POST "$API_BASE/api/upload?collection_id=$COLLECTION_ID&start_processing=false" \
        -H "X-API-Key: $API_KEY" \
        -F "file=@$FILE")

      DOCUMENT_ID=$(echo "$RESULT" | jq -r '.document_id // .doc_id // empty' 2>/dev/null || python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('document_id',d.get('doc_id','')))" <<< "$RESULT")

      if [ -z "$DOCUMENT_ID" ]; then
        echo "    Upload failed"
        continue
      fi

      update_tracking "$FILE" "$CURRENT_HASH" "$DOCUMENT_ID" "$TIMESTAMP" "uploaded"
      UPLOAD_COUNT=$((UPLOAD_COUNT + 1))
    done
  done
done

echo "Uploaded: $UPLOAD_COUNT | Skipped: $SKIP_COUNT"

# Trigger batch processing
if [ $UPLOAD_COUNT -gt 0 ]; then
  PROCESS_RESULT=$(curl -s -X POST "$API_BASE/api/documents/process-pending" -H "X-API-Key: $API_KEY")
  TASK_ID=$(echo "$PROCESS_RESULT" | jq -r '.task_id // empty' 2>/dev/null || echo "")

  if [ -n "$TASK_ID" ]; then
    echo "Processing task: $TASK_ID"
    ATTEMPT=0
    while [ $ATTEMPT -lt 60 ]; do
      TASK_STATUS=$(curl -s "$API_BASE/api/tasks/$TASK_ID" -H "X-API-Key: $API_KEY")
      STATUS=$(echo "$TASK_STATUS" | jq -r '.status // ""' 2>/dev/null | tr '[:upper:]' '[:lower:]')
      [ "$STATUS" = "completed" ] && echo "All documents processed!" && break
      [ "$STATUS" = "failed" ] && echo "Processing failed" && break
      ATTEMPT=$((ATTEMPT + 1))
      sleep 5
    done
  fi
fi

echo "Sync complete!"
```

---

## Troubleshooting

### "python3: command not found"
Install Python 3 - it's required for the skill to work without jq.

### "sha256sum: command not found" (macOS)
Use `shasum -a 256` instead - the script handles this automatically.

### Connection refused
The API might be temporarily unavailable. Try again in a few minutes.

### 401 Unauthorized
Your API key is invalid. Ask your human for a new one from the admin panel.

### File not appearing in library
1. Check status: `GET /api/documents/{document_id}`
2. If "failed", check `error_message` field
3. Reprocess: `POST /api/documents/{document_id}/reprocess`

### Out of sync tracking
Reset tracking to re-upload everything:
```bash
echo '{"files": {}, "last_sync": null}' > ~/.openclaw/skills/library/state/uploaded_files.json
```

### Quick Commands

**Check sync status:**
```bash
cat ~/.openclaw/skills/library/state/uploaded_files.json | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"Last sync: {d.get('last_sync','never')}, Files: {len(d.get('files',{}))}\")"
```

**Force re-upload a file:**
```bash
python3 - ~/.openclaw/skills/library/state/uploaded_files.json "/path/to/file.md" << 'EOF'
import json, sys
path, file_path = sys.argv[1:3]
with open(path) as f:
    d = json.load(f)
d['files'].pop(file_path, None)
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
EOF
```
