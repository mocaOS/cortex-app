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

## Scan Memory Directories for New Files

Check these directories for memory files to sync:

1. `~/.openclaw/memory/` - Primary memory storage
2. `~/.openclaw/conversations/` - Conversation logs
3. Any custom paths your human has configured

**Supported file types:** `.md`, `.txt`, `.json`

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
# IMPORTANT: collection_id and start_processing are QUERY PARAMETERS, not form fields
RESULT=$(curl -s -X POST "$API_BASE/api/upload?collection_id=$COLLECTION_ID&start_processing=false" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@$FILE_PATH")

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

## Complete Sync Script

Here's the full sync logic using efficient bulk upload:

```bash
#!/bin/bash
# OpenClaw Library Sync Script (Bulk Upload Version)
#
# IMPORTANT: All memory files are uploaded ONLY to the "OpenClaw" collection.
# This script will automatically find or create the OpenClaw collection.
# Files are NEVER uploaded to any other collection.
#
# REQUIRED: Both api_key and base_url must be configured in credentials.json

set -e

STATE_DIR="$HOME/.openclaw/skills/library/state"
CREDENTIALS="$STATE_DIR/credentials.json"
TRACKING="$STATE_DIR/uploaded_files.json"
COLLECTION_NAME="OpenClaw"  # Target collection - DO NOT CHANGE

# Memory directories to scan
MEMORY_DIRS=(
  "$HOME/.openclaw/memory"
  "$HOME/.openclaw/conversations"
)

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📚 OpenClaw Library Sync"
echo "   Target collection: $COLLECTION_NAME"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Load credentials
if [ ! -f "$CREDENTIALS" ]; then
  echo "❌ No credentials found. Please provide both api_key and base_url."
  exit 1
fi

API_KEY=$(jq -r '.api_key' "$CREDENTIALS")
API_BASE=$(jq -r '.base_url' "$CREDENTIALS")
COLLECTION_ID=$(jq -r '.collection_id' "$CREDENTIALS")

# Validate BOTH credentials are present
if [ "$API_KEY" = "null" ] || [ -z "$API_KEY" ]; then
  echo "❌ No API key configured. Please provide an API key."
  exit 1
fi

if [ "$API_BASE" = "null" ] || [ -z "$API_BASE" ]; then
  echo "❌ No base URL configured. Please provide a base URL."
  exit 1
fi

echo "   Base URL: $API_BASE"
echo ""

# Initialize tracking if needed
if [ ! -f "$TRACKING" ]; then
  echo '{"files": {}, "last_sync": null}' > "$TRACKING"
fi

# ============================================================================
# PREREQUISITE: Find OpenClaw collection by NAME (MANDATORY - ALWAYS DO THIS)
# All files MUST go to the OpenClaw collection - no exceptions
# We ALWAYS look up by name to ensure we have the correct collection ID
# ============================================================================
echo "🔍 Finding OpenClaw collection by name..."

# ALWAYS query the API to find the OpenClaw collection by name
# Don't trust cached collection_id - it might be wrong or stale
COLLECTIONS=$(curl -s "$API_BASE/api/collections" -H "X-API-Key: $API_KEY")

# Check if we got a valid response
if [ -z "$COLLECTIONS" ] || [ "$(echo "$COLLECTIONS" | jq -r '.collections // empty')" = "" ]; then
  echo "   ❌ FATAL: Could not fetch collections from API"
  echo "      Response: $COLLECTIONS"
  exit 1
fi

# Find the OpenClaw collection by its exact name
COLLECTION_ID=$(echo "$COLLECTIONS" | jq -r ".collections[] | select(.name == \"$COLLECTION_NAME\") | .id" | head -n1)

if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
  echo "   📚 OpenClaw collection not found. Creating it now..."
  CREATE_RESULT=$(curl -s -X POST "$API_BASE/api/collections" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$COLLECTION_NAME\", \"description\": \"Memory files synced from OpenClaw agent\"}")
  
  COLLECTION_ID=$(echo "$CREATE_RESULT" | jq -r '.id')
  
  if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
    echo "   ❌ FATAL: Failed to create OpenClaw collection"
    echo "      Error: $(echo "$CREATE_RESULT" | jq -r '.detail // .message // "Unknown error"')"
    echo "      Full response: $CREATE_RESULT"
    exit 1
  fi
  echo "   ✅ Created OpenClaw collection: $COLLECTION_ID"
else
  echo "   ✅ Found OpenClaw collection: $COLLECTION_ID"
fi

# Save the verified collection ID to credentials
jq --arg cid "$COLLECTION_ID" '.collection_id = $cid' "$CREDENTIALS" > "$CREDENTIALS.tmp"
mv "$CREDENTIALS.tmp" "$CREDENTIALS"
echo "   💾 Collection ID saved: $COLLECTION_ID"

# Final validation - NEVER proceed without valid collection
if [ "$COLLECTION_ID" = "null" ] || [ -z "$COLLECTION_ID" ]; then
  echo ""
  echo "❌ FATAL: Cannot proceed without a valid OpenClaw collection ID"
  exit 1
fi

echo ""

# Arrays to track uploads
declare -a UPLOADED_FILES
declare -a UPLOADED_DOC_IDS
declare -a UPLOADED_HASHES
UPLOAD_COUNT=0
SKIP_COUNT=0

# ============================================================================
# PHASE 1: Upload all new/modified files to OpenClaw collection (without processing)
# ============================================================================
echo "📤 Phase 1: Uploading new files to OpenClaw collection..."
echo "   Collection ID: $COLLECTION_ID"
echo ""

for DIR in "${MEMORY_DIRS[@]}"; do
  if [ ! -d "$DIR" ]; then
    continue
  fi
  
  for FILE in "$DIR"/*.{md,txt,json} 2>/dev/null; do
    if [ ! -f "$FILE" ]; then
      continue
    fi
    
    # Calculate hash (use shasum on macOS, sha256sum on Linux)
    if command -v sha256sum &> /dev/null; then
      CURRENT_HASH=$(sha256sum "$FILE" | cut -d' ' -f1)
    else
      CURRENT_HASH=$(shasum -a 256 "$FILE" | cut -d' ' -f1)
    fi
    
    STORED_HASH=$(jq -r --arg path "$FILE" '.files[$path].hash // ""' "$TRACKING")
    
    if [ "$CURRENT_HASH" = "$STORED_HASH" ]; then
      SKIP_COUNT=$((SKIP_COUNT + 1))
      continue
    fi
    
    echo "   📄 $(basename "$FILE")"
    
    # Upload file to OpenClaw collection WITHOUT processing
    # IMPORTANT: collection_id and start_processing must be QUERY PARAMETERS, not form fields
    RESULT=$(curl -s -X POST "$API_BASE/api/upload?collection_id=$COLLECTION_ID&start_processing=false" \
      -H "X-API-Key: $API_KEY" \
      -F "file=@$FILE")
    
    DOCUMENT_ID=$(echo "$RESULT" | jq -r '.document_id // .doc_id')
    RESULT_COLLECTION=$(echo "$RESULT" | jq -r '.collection_id // "unknown"')
    
    if [ "$DOCUMENT_ID" = "null" ] || [ -z "$DOCUMENT_ID" ]; then
      echo "      ❌ Upload failed: $(echo "$RESULT" | jq -r '.detail // .message // "Unknown error"')"
      continue
    fi
    
    # Verify the document was uploaded to the correct collection
    if [ "$RESULT_COLLECTION" != "unknown" ] && [ "$RESULT_COLLECTION" != "$COLLECTION_ID" ]; then
      echo "      ⚠️ WARNING: File uploaded to wrong collection: $RESULT_COLLECTION (expected: $COLLECTION_ID)"
    else
      echo "      ✅ -> $DOCUMENT_ID (collection: $COLLECTION_ID)"
    fi
    
    # Track for later processing
    UPLOADED_FILES+=("$FILE")
    UPLOADED_DOC_IDS+=("$DOCUMENT_ID")
    UPLOADED_HASHES+=("$CURRENT_HASH")
    UPLOAD_COUNT=$((UPLOAD_COUNT + 1))
  done
done

echo ""
echo "📦 Uploaded $UPLOAD_COUNT files, $SKIP_COUNT already synced"

# ============================================================================
# PHASE 2: Trigger batch processing for all pending documents
# ============================================================================
if [ $UPLOAD_COUNT -gt 0 ]; then
  echo ""
  echo "🔄 Phase 2: Triggering batch processing..."
  
  PROCESS_RESULT=$(curl -s -X POST "$API_BASE/api/documents/process-pending" \
    -H "X-API-Key: $API_KEY")
  
  TASK_ID=$(echo "$PROCESS_RESULT" | jq -r '.task_id')
  PENDING_COUNT=$(echo "$PROCESS_RESULT" | jq -r '.pending_count // 0')
  
  if [ "$TASK_ID" = "null" ] || [ -z "$TASK_ID" ]; then
    echo "   ⚠️ Could not start batch processing: $(echo "$PROCESS_RESULT" | jq -r '.detail // .message // "Unknown error"')"
    # Fall back: update tracking with pending status
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    for i in "${!UPLOADED_FILES[@]}"; do
      jq --arg path "${UPLOADED_FILES[$i]}" \
         --arg hash "${UPLOADED_HASHES[$i]}" \
         --arg docid "${UPLOADED_DOC_IDS[$i]}" \
         --arg time "$TIMESTAMP" \
         '.files[$path] = {"hash": $hash, "document_id": $docid, "uploaded_at": $time, "status": "pending"} | .last_sync = $time' \
         "$TRACKING" > "$TRACKING.tmp"
      mv "$TRACKING.tmp" "$TRACKING"
    done
  else
    echo "   📊 Processing $PENDING_COUNT documents (Task: $TASK_ID)"
    
    # ============================================================================
    # PHASE 3: Wait for batch processing to complete
    # ============================================================================
    echo ""
    echo "⏳ Phase 3: Waiting for processing to complete..."
    
    MAX_ATTEMPTS=120  # 10 minutes at 5s intervals
    ATTEMPT=0
    
    while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
      TASK_STATUS=$(curl -s "$API_BASE/api/tasks/$TASK_ID" -H "X-API-Key: $API_KEY")
      
      STATUS=$(echo "$TASK_STATUS" | jq -r '.status' | tr '[:upper:]' '[:lower:]')
      PROGRESS=$(echo "$TASK_STATUS" | jq -r '.progress_percent // 0')
      MESSAGE=$(echo "$TASK_STATUS" | jq -r '.message // ""')
      
      # Only print progress updates every few iterations to reduce noise
      if [ $((ATTEMPT % 3)) -eq 0 ]; then
        echo "   Progress: ${PROGRESS}% - $MESSAGE"
      fi
      
      if [ "$STATUS" = "completed" ]; then
        echo ""
        echo "   ✅ All documents processed successfully!"
        FINAL_STATUS="completed"
        break
      elif [ "$STATUS" = "failed" ]; then
        echo ""
        ERROR=$(echo "$TASK_STATUS" | jq -r '.error // "Unknown error"')
        echo "   ❌ Processing failed: $ERROR"
        FINAL_STATUS="failed"
        break
      fi
      
      ATTEMPT=$((ATTEMPT + 1))
      sleep 5
    done
    
    if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
      echo "   ⏳ Processing still in progress (will continue in background)"
      FINAL_STATUS="processing"
    fi
    
    # ============================================================================
    # PHASE 4: Update tracking for all uploaded files
    # ============================================================================
    echo ""
    echo "📝 Updating tracking records..."
    
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    for i in "${!UPLOADED_FILES[@]}"; do
      # Check individual document status if batch completed
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

**If files were uploaded:**
```
Library sync complete - Uploaded 3 new memory files to knowledge graph:
- conversation_2026-02-05.md
- project_notes.md  
- research_summary.txt
Total in library: 47 documents, 892 entities
```

**If there were errors:**
```
Library sync completed with issues:
- ✅ Uploaded: 2 files
- ❌ Failed: 1 file (research.md - file too large)
Will retry failed files on next sync.
```

**If credentials are missing:**
```
⚠️ Library sync skipped - Missing credentials.
Please provide BOTH a MOCA Library base URL AND API key to enable memory sync.
```

---

## When to Tell Your Human

**Do tell them:**
- First-time setup needs an API key
- API key is invalid or expired
- Many files failed to upload
- Something unusual happened

**Don't bother them:**
- Routine successful syncs
- A few files skipped (already synced)
- Normal operation

---

## Sync Frequency

**Recommended:** Every 4-6 hours during active use

**Track your last sync:**
```bash
LAST_SYNC=$(jq -r '.last_sync' ~/.openclaw/skills/library/state/uploaded_files.json)
echo "Last library sync: $LAST_SYNC"
```

**Check if sync is needed:**
```bash
LAST_SYNC=$(jq -r '.last_sync // "1970-01-01T00:00:00Z"' ~/.openclaw/skills/library/state/uploaded_files.json)
LAST_SYNC_EPOCH=$(date -d "$LAST_SYNC" +%s 2>/dev/null || date -j -f "%Y-%m-%dT%H:%M:%SZ" "$LAST_SYNC" +%s)
NOW_EPOCH=$(date +%s)
HOURS_SINCE=$(( (NOW_EPOCH - LAST_SYNC_EPOCH) / 3600 ))

if [ $HOURS_SINCE -ge 4 ]; then
  echo "Time to sync! Last sync was $HOURS_SINCE hours ago."
fi
```

---

## Quick Commands

**Check sync status:**
```bash
cat ~/.openclaw/skills/library/state/uploaded_files.json | jq '.last_sync, (.files | length)'
```

**Force re-upload a file:**
```bash
# Remove file from tracking to force re-upload
jq 'del(.files["/path/to/file.md"])' ~/.openclaw/skills/library/state/uploaded_files.json > tmp.json
mv tmp.json ~/.openclaw/skills/library/state/uploaded_files.json
```

**Reset all tracking (will re-upload everything):**
```bash
echo '{"files": {}, "last_sync": null}' > ~/.openclaw/skills/library/state/uploaded_files.json
```

---

## Troubleshooting

### "jq: command not found"
Install jq or use alternative JSON parsing.

### "sha256sum: command not found" (macOS)
Use `shasum -a 256` instead of `sha256sum`.

### Connection refused
The API might be temporarily unavailable. Try again in a few minutes.

### 401 Unauthorized
Your API key is invalid. Ask your human for a new one.
