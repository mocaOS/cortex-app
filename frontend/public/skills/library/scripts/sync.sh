#!/bin/bash
# Cortex Library Sync Script
# Syncs agent memory files to Cortex Library knowledge graph.
# Reads base_url from credentials (no hardcoded URLs).
# Uses URL query parameters for upload API (NOT form fields).
set -e

STATE_DIR="$HOME/.openclaw/skills/library/state"
CREDENTIALS="$STATE_DIR/credentials.json"
TRACKING="$STATE_DIR/uploaded_files.json"
COLLECTION_NAME="OpenClaw"

# Memory directories to scan
MEMORY_DIRS=(
  "$HOME/.openclaw/memory"
  "$HOME/.openclaw/conversations"
)

# Detect QMD and add sessions directory
MEMORY_BACKEND=$(cat ~/.openclaw/openclaw.json 2>/dev/null | jq -r '.memory.backend // "sqlite"' 2>/dev/null || echo "sqlite")
if [ "$MEMORY_BACKEND" = "qmd" ]; then
  AGENT_ID=$(cat ~/.openclaw/openclaw.json 2>/dev/null | jq -r '.agents.defaults.id // "main"' 2>/dev/null || echo "main")
  QMD_DIR="$HOME/.openclaw/agents/$AGENT_ID/qmd/sessions"
  [ -d "$QMD_DIR" ] && MEMORY_DIRS+=("$QMD_DIR")
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📚 Cortex Library Sync"
echo "   Target collection: $COLLECTION_NAME"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Load credentials
if [ ! -f "$CREDENTIALS" ]; then
  echo "❌ No credentials found. Please provide api_key and base_url."
  exit 1
fi

API_KEY=$(jq -r '.api_key' "$CREDENTIALS")
API_BASE=$(jq -r '.base_url' "$CREDENTIALS")

if [ "$API_KEY" = "null" ] || [ -z "$API_KEY" ]; then
  echo "❌ No API key configured."
  exit 1
fi

if [ "$API_BASE" = "null" ] || [ -z "$API_BASE" ]; then
  echo "❌ No base URL configured."
  exit 1
fi

echo "   Base URL: $API_BASE"
echo ""

# Initialize tracking if needed
if [ ! -f "$TRACKING" ]; then
  echo '{"files": {}, "last_sync": null}' > "$TRACKING"
fi

# ============================================================================
# Ensure collection exists
# ============================================================================
echo "🔍 Finding $COLLECTION_NAME collection..."

COLLECTIONS=$(curl -s "$API_BASE/api/collections" -H "X-API-Key: $API_KEY")
COLLECTION_ID=$(echo "$COLLECTIONS" | jq -r ".collections[] | select(.name == \"$COLLECTION_NAME\") | .id" | head -n1)

if [ -z "$COLLECTION_ID" ] || [ "$COLLECTION_ID" = "null" ]; then
  echo "   📚 Creating $COLLECTION_NAME collection..."
  RESULT=$(curl -s -X POST "$API_BASE/api/collections" \
    -H "X-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$COLLECTION_NAME\", \"description\": \"Memory files synced from agent\"}")
  COLLECTION_ID=$(echo "$RESULT" | jq -r '.id')

  if [ "$COLLECTION_ID" = "null" ] || [ -z "$COLLECTION_ID" ]; then
    echo "   ❌ Failed to create collection"
    exit 1
  fi
  echo "   ✅ Created: $COLLECTION_ID"
else
  echo "   ✅ Found: $COLLECTION_ID"
fi

# Save to credentials
jq --arg cid "$COLLECTION_ID" '.collection_id = $cid' "$CREDENTIALS" > "$CREDENTIALS.tmp"
mv "$CREDENTIALS.tmp" "$CREDENTIALS"

echo ""

# Arrays to track uploads
declare -a UPLOADED_FILES
declare -a UPLOADED_DOC_IDS
declare -a UPLOADED_HASHES
UPLOAD_COUNT=0
SKIP_COUNT=0

# ============================================================================
# PHASE 1: Upload all new/modified files (without processing)
# ============================================================================
echo "📤 Phase 1: Uploading new files..."

for DIR in "${MEMORY_DIRS[@]}"; do
  if [ ! -d "$DIR" ]; then continue; fi

  for FILE in "$DIR"/*.md "$DIR"/*.txt "$DIR"/*.json; do
    if [ ! -f "$FILE" ]; then continue; fi

    # Calculate hash
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

    # Upload file WITHOUT processing
    # CRITICAL: collection_id and start_processing are URL QUERY PARAMETERS
    RESULT=$(curl -s -X POST "$API_BASE/api/upload?collection_id=$COLLECTION_ID&start_processing=false" \
      -H "X-API-Key: $API_KEY" \
      -F "file=@$FILE")

    DOCUMENT_ID=$(echo "$RESULT" | jq -r '.document_id // .doc_id')

    if [ "$DOCUMENT_ID" = "null" ] || [ -z "$DOCUMENT_ID" ]; then
      echo "      ❌ Upload failed: $(echo "$RESULT" | jq -r '.detail // .message // "Unknown error"')"
      continue
    fi

    UPLOADED_FILES+=("$FILE")
    UPLOADED_DOC_IDS+=("$DOCUMENT_ID")
    UPLOADED_HASHES+=("$CURRENT_HASH")
    UPLOAD_COUNT=$((UPLOAD_COUNT + 1))
  done
done

echo ""
echo "📦 Uploaded $UPLOAD_COUNT files, $SKIP_COUNT already synced"

# ============================================================================
# PHASE 2: Trigger batch processing
# ============================================================================
if [ $UPLOAD_COUNT -gt 0 ]; then
  echo ""
  echo "🔄 Phase 2: Triggering batch processing..."

  PROCESS_RESULT=$(curl -s -X POST "$API_BASE/api/documents/process-pending" \
    -H "X-API-Key: $API_KEY")

  TASK_ID=$(echo "$PROCESS_RESULT" | jq -r '.task_id')
  PENDING_COUNT=$(echo "$PROCESS_RESULT" | jq -r '.pending_count // 0')

  if [ "$TASK_ID" = "null" ] || [ -z "$TASK_ID" ]; then
    echo "   ⚠️ Could not start batch processing"

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
    # PHASE 3: Wait for processing
    # ============================================================================
    echo ""
    echo "⏳ Phase 3: Waiting for processing..."

    MAX_ATTEMPTS=120
    ATTEMPT=0
    FINAL_STATUS="processing"

    while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
      TASK_STATUS=$(curl -s "$API_BASE/api/tasks/$TASK_ID" -H "X-API-Key: $API_KEY")
      STATUS=$(echo "$TASK_STATUS" | jq -r '.status' | tr '[:upper:]' '[:lower:]')
      PROGRESS=$(echo "$TASK_STATUS" | jq -r '.progress_percent // 0')

      if [ $((ATTEMPT % 3)) -eq 0 ]; then
        echo "   Progress: ${PROGRESS}%"
      fi

      if [ "$STATUS" = "completed" ]; then
        echo ""
        echo "   ✅ All documents processed!"
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

    # ============================================================================
    # PHASE 4: Update tracking
    # ============================================================================
    echo ""
    echo "📝 Updating tracking..."

    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    for i in "${!UPLOADED_FILES[@]}"; do
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
echo "   ⏭️  Skipped: $SKIP_COUNT files (already synced)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
