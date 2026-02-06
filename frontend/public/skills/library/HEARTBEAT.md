# OpenClaw Library Heartbeat 📚

*Run this periodically (every 4+ hours) to keep your memories synced to the knowledge graph.*

## First: Check Your Credentials

```bash
# Check if credentials exist
cat ~/.openclaw/skills/library/state/credentials.json 2>/dev/null
```

**If file doesn't exist or is missing `api_key` OR `base_url`:**

Stop here and tell your human:

> "I need BOTH a MOCA Library base URL AND an API key to sync my memories. Please provide:
> 1. The base URL of your MOCA Library (e.g., https://library.example.com)
> 2. An API key from YOUR_BASE_URL/admin/api-keys"

Once they provide BOTH values, save them and continue:

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

**CRITICAL:** Both `api_key` AND `base_url` are REQUIRED. Do not proceed without both values.

---

## Validate API Connection

```bash
API_KEY=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.api_key')
API_BASE=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.base_url')

# Validate both credentials are present
if [ -z "$API_KEY" ] || [ "$API_KEY" = "null" ] || [ -z "$API_BASE" ] || [ "$API_BASE" = "null" ]; then
  echo "❌ Missing credentials. Both api_key and base_url are required."
  exit 1
fi

curl -s "$API_BASE/health" \
  -H "X-API-Key: $API_KEY"
```

**Expected:** `{"status": "healthy", ...}`

**If status is not "healthy" or you get an error:** Either the base URL is incorrect, the API might be down, or your key is invalid. Tell your human and try again later.

---

## Ensure OpenClaw Collection Exists (MANDATORY)

**CRITICAL:** All memory files MUST be uploaded to the `OpenClaw` collection. This step ensures the collection exists BEFORE any uploads.

**IMPORTANT:** ALWAYS look up the collection by NAME, not by cached ID. This ensures we always upload to the correct collection.

**Never skip this step. Never upload to a different collection.**

```bash
API_KEY=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.api_key')
API_BASE=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.base_url')
COLLECTION_NAME="OpenClaw"

# Validate both credentials are present
if [ -z "$API_KEY" ] || [ "$API_KEY" = "null" ] || [ -z "$API_BASE" ] || [ "$API_BASE" = "null" ]; then
  echo "❌ Missing credentials. Both api_key and base_url are required."
  exit 1
fi

# ALWAYS look up the collection by name - don't trust cached values
echo "🔍 Finding OpenClaw collection by name..."

COLLECTIONS=$(curl -s "$API_BASE/api/collections" -H "X-API-Key: $API_KEY")

# Check if API call succeeded
if [ -z "$COLLECTIONS" ] || [ "$(echo "$COLLECTIONS" | jq -r '.collections // empty')" = "" ]; then
  echo "❌ FATAL: Could not fetch collections from API"
  exit 1
fi

# Find the OpenClaw collection by its exact name
COLLECTION_ID=$(echo "$COLLECTIONS" | jq -r ".collections[] | select(.name == \"$COLLECTION_NAME\") | .id" | head -n1)

# If OpenClaw collection doesn't exist, create it
if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
  echo "📚 OpenClaw collection not found. Creating it now..."
  CREATE_RESULT=$(curl -s -X POST "$API_BASE/api/collections" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$COLLECTION_NAME\", \"description\": \"Memory files synced from OpenClaw agent\"}")

  COLLECTION_ID=$(echo "$CREATE_RESULT" | jq -r '.id')

  if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
    echo "❌ FATAL: Failed to create OpenClaw collection"
    echo "   Error: $(echo "$CREATE_RESULT" | jq -r '.detail // .message // "Unknown error"')"
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

# Verify we have a valid collection ID
if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
  echo "❌ FATAL: No valid OpenClaw collection ID. Aborting upload."
  exit 1
fi

echo "📁 All uploads will go to OpenClaw collection: $COLLECTION_ID"
```

---

## ⚠️ CRITICAL: Upload API Parameter Format

**For ALL file uploads, parameters MUST be passed as URL query parameters, NOT as form fields:**

| Parameter | Correct Usage | Wrong Usage |
|-----------|---------------|-------------|
| `collection_id` | `?collection_id=xxx` in URL | ~~`-F "collection_id=xxx"`~~ |
| `start_processing` | `?start_processing=true` in URL | ~~`-F "start_processing=true"`~~ |
| `file` | `-F "file=@/path/to/file"` | (this is correct) |

**✅ CORRECT:** `curl -X POST "$URL?collection_id=$ID&start_processing=false" -F "file=@$FILE"`

**❌ WRONG:** `curl -X POST "$URL" -F "collection_id=$ID" -F "start_processing=false" -F "file=@$FILE"`

---

## Scan Memory Directories for New Files

Check these directories for memory files to sync:

1. `~/.openclaw/memory/` - Primary memory storage
2. `~/.openclaw/conversations/` - Conversation logs
3. **QMD sessions** - When QMD is enabled (`~/.openclaw/agents/main/qmd/sessions/`)
4. Any custom paths your human has configured

**Supported file types:** `.md`, `.txt`, `.json`

### Detect QMD and Add Session Directory

If QMD is enabled as the memory backend, automatically include the QMD sessions directory:

```bash
# Check if QMD is the active memory backend
MEMORY_BACKEND=$(cat ~/.openclaw/openclaw.json 2>/dev/null | jq -r '.memory.backend // "sqlite"')

if [ "$MEMORY_BACKEND" = "qmd" ]; then
  echo "   🔍 QMD backend detected - including session files"

  # Get agent ID (default to "main")
  AGENT_ID=$(cat ~/.openclaw/openclaw.json 2>/dev/null | jq -r '.agents.defaults.id // "main"')

  # QMD sessions directory
  QMD_SESSIONS_DIR="$HOME/.openclaw/agents/$AGENT_ID/qmd/sessions"

  if [ -d "$QMD_SESSIONS_DIR" ]; then
    echo "   📁 Found QMD sessions at: $QMD_SESSIONS_DIR"
  fi
fi
```

### Load Upload Tracking State

```bash
# Create tracking file if it doesn't exist
if [ ! -f ~/.openclaw/skills/library/state/uploaded_files.json ]; then
  echo '{"files": {}, "last_sync": null}' > ~/.openclaw/skills/library/state/uploaded_files.json
fi

UPLOADED=$(cat ~/.openclaw/skills/library/state/uploaded_files.json)
```

### Find New or Modified Files

For each file in memory directories:

1. **Calculate SHA-256 hash** of file content
2. **Check if hash exists** in `uploaded_files.json`
3. **If hash matches** → Skip (already uploaded)
4. **If hash differs or new** → Add to upload queue

```bash
# Example: Check a single file
FILE_PATH="$HOME/.openclaw/memory/example.md"
CURRENT_HASH=$(sha256sum "$FILE_PATH" | cut -d' ' -f1)
STORED_HASH=$(echo "$UPLOADED" | jq -r --arg path "$FILE_PATH" '.files[$path].hash // ""')

if [ "$CURRENT_HASH" != "$STORED_HASH" ]; then
  echo "File needs upload: $FILE_PATH"
fi
```

---

## Upload New Memory Files to OpenClaw Collection

**All memory files are uploaded ONLY to the OpenClaw collection.**

For efficiency, we use a **two-step bulk upload process**:
1. Upload all files to the OpenClaw collection WITHOUT processing (fast)
2. Trigger batch processing for all pending documents at once

### Step 1: Upload Files to OpenClaw Collection (Without Processing)

**IMPORTANT:** Before uploading, ALWAYS look up the OpenClaw collection by name to get its ID.

**⚠️ CRITICAL - URL QUERY PARAMETERS ONLY:**
- `collection_id` and `start_processing` MUST be in the URL as query parameters (after the `?`)
- NEVER use `-F collection_id=...` or `-F start_processing=...` - this will NOT work
- The ONLY `-F` flag should be for the file itself: `-F "file=@..."`

```bash
API_KEY=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.api_key')
API_BASE=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.base_url')
COLLECTION_NAME="OpenClaw"

# Validate both credentials are present
if [ -z "$API_KEY" ] || [ "$API_KEY" = "null" ] || [ -z "$API_BASE" ] || [ "$API_BASE" = "null" ]; then
  echo "❌ Missing credentials. Both api_key and base_url are required."
  exit 1
fi

# ALWAYS look up the collection by name before uploading
COLLECTIONS=$(curl -s "$API_BASE/api/collections" -H "X-API-Key: $API_KEY")
COLLECTION_ID=$(echo "$COLLECTIONS" | jq -r ".collections[] | select(.name == \"$COLLECTION_NAME\") | .id" | head -n1)

if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
  echo "❌ OpenClaw collection not found. Cannot upload."
  exit 1
fi

echo "📁 Uploading to OpenClaw collection: $COLLECTION_ID"

# Upload the file to OpenClaw collection WITHOUT starting processing
#
# ⚠️ CRITICAL - URL QUERY PARAMETERS ONLY:
# - collection_id and start_processing MUST be in the URL after the ?
# - NEVER use -F for collection_id or start_processing
# - The ONLY -F flag should be for the file: -F "file=@..."
#
RESULT=$(curl -s -X POST "$API_BASE/api/upload?collection_id=$COLLECTION_ID&start_processing=false" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@$FILE_PATH")

# ❌ WRONG - NEVER DO THIS:
# curl -X POST "$API_BASE/api/upload" -F "collection_id=$COLLECTION_ID" -F "file=@$FILE_PATH"

DOCUMENT_ID=$(echo "$RESULT" | jq -r '.document_id')
STATUS=$(echo "$RESULT" | jq -r '.status')

echo "Uploaded $FILE_PATH -> $DOCUMENT_ID (collection: $COLLECTION_ID, status: $STATUS)"
```

### Step 2: Trigger Batch Processing

After uploading ALL files, trigger processing for all pending documents:

```bash
API_KEY=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.api_key')
API_BASE=$(cat ~/.openclaw/skills/library/state/credentials.json | jq -r '.base_url')

# Validate both credentials are present
if [ -z "$API_KEY" ] || [ "$API_KEY" = "null" ] || [ -z "$API_BASE" ] || [ "$API_BASE" = "null" ]; then
  echo "❌ Missing credentials. Both api_key and base_url are required."
  exit 1
fi

# Start processing all pending documents
PROCESS_RESULT=$(curl -s -X POST "$API_BASE/api/documents/process-pending" \
  -H "X-API-Key: $API_KEY")

TASK_ID=$(echo "$PROCESS_RESULT" | jq -r '.task_id')
PENDING_COUNT=$(echo "$PROCESS_RESULT" | jq -r '.pending_count')

echo "🔄 Processing $PENDING_COUNT documents. Task ID: $TASK_ID"
```

### Step 3: Wait for Batch Processing to Complete

Poll the task status until all documents are processed:

```bash
MAX_ATTEMPTS=60  # 5 minutes at 5s intervals
ATTEMPT=0

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
  TASK_STATUS=$(curl -s "$API_BASE/api/tasks/$TASK_ID" \
    -H "X-API-Key: $API_KEY")

  STATUS=$(echo "$TASK_STATUS" | jq -r '.status')
  PROGRESS=$(echo "$TASK_STATUS" | jq -r '.progress_percent // 0')
  MESSAGE=$(echo "$TASK_STATUS" | jq -r '.message // ""')

  echo "   Progress: ${PROGRESS}% - $MESSAGE"

  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "COMPLETED" ]; then
    echo "✅ All documents processed successfully!"
    break
  elif [ "$STATUS" = "failed" ] || [ "$STATUS" = "FAILED" ]; then
    ERROR=$(echo "$TASK_STATUS" | jq -r '.error // "Unknown error"')
    echo "❌ Processing failed: $ERROR"
    break
  fi

  ATTEMPT=$((ATTEMPT + 1))
  sleep 5
done

if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
  echo "⏳ Processing still in progress (will check next sync)"
fi
```

---

## Update Upload Tracking

After successful upload, update `uploaded_files.json`:

```bash
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
FILE_HASH=$(sha256sum "$FILE_PATH" | cut -d' ' -f1)

# Read current tracking file
TRACKING=$(cat ~/.openclaw/skills/library/state/uploaded_files.json)

# Update with new file entry
TRACKING=$(echo "$TRACKING" | jq \
  --arg path "$FILE_PATH" \
  --arg hash "$FILE_HASH" \
  --arg docid "$DOCUMENT_ID" \
  --arg time "$TIMESTAMP" \
  --arg status "$STATUS" \
  '.files[$path] = {
    "hash": $hash,
    "document_id": $docid,
    "uploaded_at": $time,
    "status": $status
  } | .last_sync = $time')

# Save updated tracking
echo "$TRACKING" > ~/.openclaw/skills/library/state/uploaded_files.json
```

---

## Implementation Notes & Best Practices

### Update Tracking Immediately (Not Batched)

**CRITICAL:** Save tracking data immediately after each successful upload, not batched at the end. This ensures:
- If sync is interrupted, already-uploaded files are tracked
- Re-running skips already-synced files
- No duplicate uploads occur

```bash
# ❌ WRONG: Batching updates at the end
# All uploads happen first, then one big tracking update
# If interrupted, tracking is lost!

# ✅ CORRECT: Update tracking immediately after each upload
for file in "$DIR"/*.md; do
  # ... upload file ...

  # Update tracking RIGHT NOW
  jq --arg path "$file" \
     --arg hash "$CURRENT_HASH" \
     --arg docid "$DOCUMENT_ID" \
     --arg time "$TIMESTAMP" \
     '.files[$path] = {"hash": $hash, "document_id": $docid, "uploaded_at": $time} | .last_sync = $time' \
     "$TRACKING_FILE" > "$TRACKING_FILE.tmp"
  mv "$TRACKING_FILE.tmp" "$TRACKING_FILE"
done
```

### Fallback for Missing jq

If `jq` is not installed, use Python:

```bash
# JSON helper using Python
json_get() {
  python3 -c "import json,sys; d=json.load(sys.stdin); print(d$1 if d.get('$2') else '')" 2>/dev/null
}

# Find collection by name without jq
collection_id=$(echo "$COLLECTIONS" | python3 -c "
import json,sys
d=json.load(sys.stdin)
ids=[c['id'] for c in d.get('collections',[]) if c.get('name')=='OpenClaw']
print(ids[0] if ids else '')")
```

### Handling Large QMD Session Backlogs

QMD can accumulate many session files (70+). For large batches:
- Upload all files first without processing (fast)
- Trigger single batch processing job
- Poll for ~5 minutes, then let it run server-side
- Don't block indefinitely

---

## Complete Sync Script (Production Version)

Here's the full sync logic with Python fallback and immediate tracking updates:

```bash
#!/bin/bash
# OpenClaw Library Sync Script - Production Version
#
# Features:
# - Works with or without jq (falls back to Python 3)
# - Updates tracking immediately after each upload (survives interruptions)
# - Handles QMD session files automatically
#
# IMPORTANT: All memory files are uploaded ONLY to the "OpenClaw" collection.
# REQUIRED: Both api_key and base_url must be configured in credentials.json

set -e

STATE_DIR="$HOME/.openclaw/skills/library/state"
CREDENTIALS="$STATE_DIR/credentials.json"
TRACKING="$STATE_DIR/uploaded_files.json"
COLLECTION_NAME="OpenClaw"

# ============================================================================
# JSON HELPERS (works with or without jq)
# ============================================================================

# Check if jq is available
HAS_JQ=$(command -v jq &> /dev/null && echo "yes" || echo "no")

json_get() {
  # Usage: json_get KEY < FILE
  # KEY format: "['key1']" or "['key1']['key2']"
  if [ "$HAS_JQ" = "yes" ]; then
    jq -r "$1 // empty"
  else
    python3 -c "import json,sys; d=json.load(sys.stdin); keys=$1; result=d; [result:=result.get(k) for k in keys if result and k in result]; print(result if result is not None else '')"
  fi
}

json_select_collection() {
  # Usage: echo COLLECTIONS | json_select_collection NAME
  local NAME="$1"
  if [ "$HAS_JQ" = "yes" ]; then
    jq -r ".collections[] | select(.name == \"$NAME\") | .id" | head -n1
  else
    python3 -c "import json,sys; d=json.load(sys.stdin); ids=[c['id'] for c in d.get('collections',[]) if c.get('name')=='$NAME']; print(ids[0] if ids else '')"
  fi
}

json_set() {
  # Usage: cat FILE | json_set KEY VALUE > FILE.tmp
  local KEY="$1"
  local VALUE="$2"
  if [ "$HAS_JQ" = "yes" ]; then
    jq --arg v "$VALUE" "$KEY = \$v"
  else
    python3 -c "import json,sys; d=json.load(sys.stdin); d$KEY='$VALUE'; json.dump(d,sys.stdout,indent=2)"
  fi
}

# ============================================================================
# SETUP
# ============================================================================

# Memory directories to scan
MEMORY_DIRS=(
  "$HOME/clawd/memory"
  "$HOME/.openclaw/memory"
  "$HOME/.openclaw/conversations"
)

# Detect QMD and add sessions directory if enabled
MEMORY_BACKEND=$(cat ~/.openclaw/openclaw.json 2>/dev/null | json_get "['memory']['backend']")
[ -z "$MEMORY_BACKEND" ] && MEMORY_BACKEND="sqlite"

if [ "$MEMORY_BACKEND" = "qmd" ]; then
  AGENT_ID="main"
  QMD_SESSIONS_DIR="$HOME/.openclaw/agents/$AGENT_ID/qmd/sessions"
  if [ -d "$QMD_SESSIONS_DIR" ]; then
    MEMORY_DIRS+=("$QMD_SESSIONS_DIR")
  fi
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📚 OpenClaw Library Sync"
echo "   Backend: $MEMORY_BACKEND | Directories: ${#MEMORY_DIRS[@]}"
echo "   JSON: $([ "$HAS_JQ" = "yes" ] && echo 'jq' || echo 'python3')"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Load credentials
if [ ! -f "$CREDENTIALS" ]; then
  echo "❌ No credentials found. Please provide both api_key and base_url."
  exit 1
fi

API_KEY=$(json_get "['api_key']" < "$CREDENTIALS")
API_BASE=$(json_get "['base_url']" < "$CREDENTIALS")

if [ -z "$API_KEY" ] || [ -z "$API_BASE" ]; then
  echo "❌ Missing credentials. Both api_key and base_url are required."
  exit 1
fi

echo "   Base URL: $API_BASE"
echo ""

# Initialize tracking if needed
if [ ! -f "$TRACKING" ]; then
  echo '{"files": {}, "last_sync": null}' > "$TRACKING"
fi

# ============================================================================
# FIND COLLECTION
# ============================================================================
echo "🔍 Finding OpenClaw collection by name..."

COLLECTIONS=$(curl -s "$API_BASE/api/collections" -H "X-API-Key: $API_KEY")

if [ -z "$COLLECTIONS" ]; then
  echo "   ❌ FATAL: Could not fetch collections from API"
  exit 1
fi

COLLECTION_ID=$(echo "$COLLECTIONS" | json_select_collection "$COLLECTION_NAME")

if [ -z "$COLLECTION_ID" ]; then
  echo "   📚 Creating OpenClaw collection..."
  CREATE_RESULT=$(curl -s -X POST "$API_BASE/api/collections" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$COLLECTION_NAME\", \"description\": \"Memory files synced from OpenClaw agent\"}")

  COLLECTION_ID=$(echo "$CREATE_RESULT" | json_get "['id']")

  if [ -z "$COLLECTION_ID" ]; then
    echo "   ❌ FATAL: Failed to create collection"
    exit 1
  fi
  echo "   ✅ Created: $COLLECTION_ID"
else
  echo "   ✅ Found: $COLLECTION_ID"
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

echo ""

# ============================================================================
# UPLOAD FILES (with immediate tracking)
# ============================================================================
echo "📤 Uploading files to OpenClaw collection..."

declare -a UPLOADED_DOC_IDS
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
UPLOAD_COUNT=0
SKIP_COUNT=0

# Helper to get stored hash
get_stored_hash() {
  local FILE_PATH="$1"
  if [ "$HAS_JQ" = "yes" ]; then
    jq -r --arg p "$FILE_PATH" '.files[$p].hash // ""' "$TRACKING"
  else
    python3 -c "import json; d=json.load(open('$TRACKING')); print(d.get('files',{}).get('$FILE_PATH',{}).get('hash',''))"
  fi
}

# Update tracking immediately after each upload
update_tracking() {
  local FILE_PATH="$1"
  local FILE_HASH="$2"
  local DOC_ID="$3"
  local STATUS="$4"

  python3 - "$TRACKING" "$FILE_PATH" "$FILE_HASH" "$DOC_ID" "$TIMESTAMP" "$STATUS" << 'PYEOF'
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

for DIR in "${MEMORY_DIRS[@]}"; do
  if [ ! -d "$DIR" ]; then
    continue
  fi

  echo "   📁 $DIR"

  # Use explicit patterns instead of brace expansion
  for PATTERN in "$DIR"/*.md "$DIR"/*.txt "$DIR"/*.json; do
    for FILE in $PATTERN; do
      [ ! -f "$FILE" ] && continue

      # Calculate hash
      if command -v sha256sum &> /dev/null; then
        CURRENT_HASH=$(sha256sum "$FILE" | cut -d' ' -f1)
      else
        CURRENT_HASH=$(shasum -a 256 "$FILE" | cut -d' ' -f1)
      fi

      STORED_HASH=$(get_stored_hash "$FILE")

      if [ "$CURRENT_HASH" = "$STORED_HASH" ] && [ -n "$STORED_HASH" ]; then
        SKIP_COUNT=$((SKIP_COUNT + 1))
        continue
      fi

      BASENAME=$(basename "$FILE")
      echo "      📄 $BASENAME"

      # Upload to OpenClaw collection
      RESULT=$(curl -s -X POST "$API_BASE/api/upload?collection_id=$COLLECTION_ID&start_processing=false" \
        -H "X-API-Key: $API_KEY" \
        -F "file=@$FILE")

      DOCUMENT_ID=$(echo "$RESULT" | json_get "['document_id']")
      [ -z "$DOCUMENT_ID" ] && DOCUMENT_ID=$(echo "$RESULT" | json_get "['doc_id']")

      if [ -z "$DOCUMENT_ID" ]; then
        echo "      ❌ Upload failed"
        continue
      fi

      echo "      ✅ $DOCUMENT_ID"

      # ⭐ CRITICAL: Update tracking IMMEDIATELY
      # If script is interrupted, we don't re-upload this file
      update_tracking "$FILE" "$CURRENT_HASH" "$DOCUMENT_ID" "uploaded"

      UPLOADED_DOC_IDS+=("$DOCUMENT_ID")
      UPLOAD_COUNT=$((UPLOAD_COUNT + 1))
    done
  done
done

echo ""
echo "📦 Uploaded: $UPLOAD_COUNT | Skipped: $SKIP_COUNT"

# ============================================================================
# TRIGGER BATCH PROCESSING
# ============================================================================
if [ $UPLOAD_COUNT -gt 0 ]; then
  echo ""
  echo "🔄 Triggering batch processing..."
  
  PROCESS_RESULT=$(curl -s -X POST "$API_BASE/api/documents/process-pending" \
    -H "X-API-Key: $API_KEY")
  
  TASK_ID=$(echo "$PROCESS_RESULT" | json_get "['task_id']")
  PENDING_COUNT=$(echo "$PROCESS_RESULT" | json_get "['pending_count']")
  [ -z "$PENDING_COUNT" ] && PENDING_COUNT=0
  
  if [ -z "$TASK_ID" ]; then
    echo "   ⚠️ Could not start batch processing"
  else
    echo "   📊 Task: $TASK_ID | Pending: $PENDING_COUNT"
    
    # Wait for processing (max ~5 minutes)
    echo ""
    echo "⏳ Waiting for processing..."
    
    MAX_ATTEMPTS=60
    ATTEMPT=0
    FINAL_STATUS="processing"
    
    while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
      TASK_STATUS=$(curl -s "$API_BASE/api/tasks/$TASK_ID" -H "X-API-Key: $API_KEY")
      
      STATUS=$(echo "$TASK_STATUS" | json_get "['status']" | tr '[:upper:]' '[:lower:]')
      PROGRESS=$(echo "$TASK_STATUS" | json_get "['progress_percent']")
      [ -z "$PROGRESS" ] && PROGRESS=0
      
      if [ $((ATTEMPT % 6)) -eq 0 ]; then
        echo "   Progress: ${PROGRESS}%"
      fi
      
      if [ "$STATUS" = "completed" ]; then
        echo ""
        echo "   ✅ All documents processed successfully!"
        FINAL_STATUS="completed"
        break
      elif [ "$STATUS" = "failed" ]; then
        echo ""
        echo "   ❌ Processing failed"
        FINAL_STATUS="failed"
        break
      fi
      
      ATTEMPT=$((ATTEMPT + 1))
      sleep 5
    done
    
    if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
      echo "   ⏳ Processing continues in background"
    fi
    
    # Update final status
    for DOC_ID in "${UPLOADED_DOC_IDS[@]}"; do
      python3 - "$TRACKING" "$DOC_ID" "$FINAL_STATUS" << 'PYEOF'
import json, sys
path, doc_id, status = sys.argv[1:4]
with open(path) as f:
    d = json.load(f)
for fp, info in d.get('files', {}).items():
    if info.get('document_id') == doc_id:
        info['status'] = status
with open(path, 'w') as f:
    json.dump(d, f, indent=2)
PYEOF
    done
  fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 Sync complete!"
echo "   📤 Uploaded: $UPLOAD_COUNT files"
echo "   ⏭️  Skipped:  $SKIP_COUNT files"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
```

---

## Summary Response Format

After sync, report to your human:

**If nothing to sync:**
```
HEARTBEAT_OK - Library sync complete. No new memory files to upload. 📚
```

**If files were uploaded:**
```
Library sync complete - Uploaded 5 new memory files:
- conversation_2026-02-05.md
- project_notes.md
- qmd/session_2026-02-06.md (QMD session)
Total: 47 documents in OpenClaw collection
```

**If there were errors:**
```
Library sync completed with issues:
- ✅ Uploaded: 2 files
- ❌ Failed: 1 file (too large)
Will retry on next sync.
```

---

## Quick Commands

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

**Reset all tracking:**
```bash
echo '{"files": {}, "last_sync": null}' > ~/.openclaw/skills/library/state/uploaded_files.json
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
Your API key is invalid. Ask your human for a new one from the Library admin panel.

---

## When to Tell Your Human

**Do tell them:**
- First-time setup needs API credentials
- API key is invalid or expired
- Many files failed to upload (>10%)
- Something unusual happened

**Don't bother them:**
- Routine successful syncs
- A few files skipped (already synced)
- Normal operation
      if [ "$FINAL_STATUS" = "completed" ]; then
        DOC_STATUS=$(curl -s "$API_BASE/api/documents/${UPLOADED_DOC_IDS[$i]}" -H "X-API-Key: $API_KEY" | jq -r '.processing_status // .status' | tr '[:upper:]' '[:lower:]')
      else
        DOC_STATUS="$FINAL_STATUS"
      fi

      jq --arg path "${UPLOADED_FILES[$i]}" \
         --arg hash "${UPLOADED_HASHES[$i]}" \
         --arg docid "${UPLOADED_DOC_IDS[$i]}" \
         --arg time "$TIMESTAMP" \
         --arg status "$DOC_STATUS" \
         '.files[$path] = {"hash": $hash, "document_id": $docid, "uploaded_at": $time, "status": $status} | .last_sync = $time' \
         "$TRACKING" > "$TRACKING.tmp"
      mv "$TRACKING.tmp" "$TRACKING"
    done
  fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 Sync complete!"
echo "   📤 Uploaded: $UPLOAD_COUNT files"
echo "   ⏭️  Skipped:  $SKIP_COUNT files (already synced)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
```

---

## Summary Response Format

After sync, report to your human:

**If nothing to sync:**
```
HEARTBEAT_OK - Library sync complete. No new memory files to upload. 📚
```

