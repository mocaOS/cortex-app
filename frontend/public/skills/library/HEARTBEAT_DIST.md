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
```

---

## Scan, Upload, and Process

1. Scan `~/.openclaw/memory/`, `~/.openclaw/conversations/`, QMD sessions (if enabled)
2. Calculate SHA-256 hash, compare with tracking file, skip unchanged
3. Upload new/modified files to collection WITHOUT processing
4. Trigger batch processing: `POST /api/documents/process-pending`
5. Poll task status until complete
6. Update tracking immediately after each upload

See [references/SYNC.md](references/SYNC.md) for the complete sync script.

---

## Summary Response Format

**Nothing to sync:**
```
HEARTBEAT_OK - Library sync complete. No new memory files to upload.
```

**Files uploaded:**
```
Library sync complete - Uploaded 5 new memory files.
```

**Errors:**
```
Library sync completed with issues. Will retry on next sync.
```
