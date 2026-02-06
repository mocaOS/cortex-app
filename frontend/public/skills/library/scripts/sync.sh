#!/bin/bash
# OpenClaw Library Sync Script (Bulk Upload Version)
set -e

STATE_DIR="$HOME/.openclaw/skills/library/state"
CREDENTIALS="$STATE_DIR/credentials.json"
TRACKING="$STATE_DIR/uploaded_files.json"
API_BASE="https://library.moca.qwellco.de"

# Memory directories to scan
MEMORY_DIRS=(
  "$HOME/.openclaw/memory"
  "$HOME/.openclaw/conversations"
)

# Load credentials
if [ ! -f "$CREDENTIALS" ]; then
  echo "❌ No credentials found. Please provide an API key."
  exit 1
fi

API_KEY=$(jq -r '.api_key' "$CREDENTIALS")
COLLECTION_ID=$(jq -r '.collection_id' "$CREDENTIALS")

if [ "$API_KEY" = "null" ] || [ -z "$API_KEY" ]; then
  echo "❌ No API key configured. Please provide an API key."
  exit 1
fi

# Initialize tracking if needed
if [ ! -f "$TRACKING" ]; then
  echo '{"files": {}, "last_sync": null}' > "$TRACKING"
fi

# Ensure collection exists
if [ "$COLLECTION_ID" = "null" ] || [ -z "$COLLECTION_ID" ]; then
  echo "🔍 Looking for OpenClaw collection..."
  COLLECTIONS=$(curl -s "$API_BASE/api/collections" -H "X-API-Key: $API_KEY")
  COLLECTION_ID=$(echo "$COLLECTIONS" | jq -r '.collections[] | select(.name == "OpenClaw") | .id')

  if [ -z "$COLLECTION_ID" ]; then
    echo "📚 Creating OpenClaw collection..."
    RESULT=$(curl -s -X POST "$API_BASE/api/collections" \
      -H "X-API-Key: $API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"name": "OpenClaw", "description": "Memory files synced from OpenClaw agent"}')
    COLLECTION_ID=$(echo "$RESULT" | jq -r '.id')
  fi
  
  # Save to credentials
  jq --arg cid "$COLLECTION_ID" '.collection_id = $cid' "$CREDENTIALS" > "$CREDENTIALS.tmp"
  mv "$CREDENTIALS.tmp" "$CREDENTIALS"
  echo "✅ Using collection: $COLLECTION_ID"
fi

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
  if [ ! -d "$DIR" ]; then continue fi
  
  for FILE in "$DIR"/*.{md,txt,json} 2>/dev/null; do
    if [ ! -f "$FILE" ]; then continue fi

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

    echo "  📄 $(basename "$FILE")"

    # Upload file WITHOUT processing (start_processing=false)
    RESULT=$(curl -s -X POST "$API_BASE/api/upload" \
      -H "X-API-Key: $API_KEY" \
      -F "file=@$FILE" \
      -F "collection_id=$COLLECTION_ID" \
      -F "start_processing=false")
      
    DOCUMENT_ID=$(echo "$RESULT" | jq -r '.document_id // .doc_id')
    
    if [ "$DOCUMENT_ID" = "null" ] || [ -z "$DOCUMENT_ID" ]; then
      echo "  ❌ Upload failed: $(echo "$RESULT" | jq -r '.detail // .message // "Unknown error"')"
      continue
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
    echo "  ⚠️ Could not start batch processing: $(echo "$PROCESS_RESULT" | jq -r '.detail // .message // "Unknown error"')"
    
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
    echo "  📊 Processing $PENDING_COUNT documents (Task: $TASK_ID)"
    
    # ============================================================================
    # PHASE 3: Wait for batch processing to complete
    # ============================================================================
    echo ""
    echo "⏳ Phase 3: Waiting for processing to complete..."
    
    MAX_ATTEMPTS=120 # 10 minutes at 5s intervals
    ATTEMPT=0
    
    while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
      TASK_STATUS=$(curl -s "$API_BASE/api/tasks/$TASK_ID" -H "X-API-Key: $API_KEY")
      STATUS=$(echo "$TASK_STATUS" | jq -r '.status' | tr '[:upper:]' '[:lower:]')
      PROGRESS=$(echo "$TASK_STATUS" | jq -r '.progress_percent // 0')
      MESSAGE=$(echo "$TASK_STATUS" | jq -r '.message // ""')
      
      # Only print progress updates every few iterations to reduce noise
      if [ $((ATTEMPT % 3)) -eq 0 ]; then
        echo "  Progress: ${PROGRESS}% - $MESSAGE"
      fi
      
      if [ "$STATUS" = "completed" ]; then
        echo ""
        echo "  ✅ All documents processed successfully!"
        FINAL_STATUS="completed"
        break
      elif [ "$STATUS" = "failed" ]; then
        echo ""
        ERROR=$(echo "$TASK_STATUS" | jq -r '.error // "Unknown error"')
        echo "  ❌ Processing failed: $ERROR"
        FINAL_STATUS="failed"
        break
      fi
      
      ATTEMPT=$((ATTEMPT + 1))
      sleep 5
    done
    
    if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
      echo "  ⏳ Processing still in progress (will continue in background)"
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
echo "  📤 Uploaded: $UPLOAD_COUNT files"
echo "  ⏭️ Skipped: $SKIP_COUNT files (already synced)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
