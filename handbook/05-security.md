# Chapter 5: Security and Authentication

This chapter covers the Library's security model, authentication mechanisms, API key management, prompt injection protection, and best practices for production deployments.

## Authentication Model

The Library uses a two-layer authentication model:

```
┌───────────────────────────────────────────────────────┐
│                    Web Interface                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │  Admin Login (Email + Password)                  │  │
│  │  → JWT token stored in HTTP-only cookie          │  │
│  │  → Full access to all features                   │  │
│  └─────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────┐
│                    REST API                            │
│  ┌─────────────────────────────────────────────────┐  │
│  │  API Key Authentication (X-API-Key header)       │  │
│  │  → Admin key: full access                        │  │
│  │  → Generated keys: configurable permissions      │  │
│  └─────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────┘
```

All endpoints except `/health` require authentication.

## Admin Authentication

### Web Login

When you access the Library's web interface, you'll see a login page. Sign in with the `ADMIN_EMAIL` and `ADMIN_PASSWORD` configured in your environment.

The admin account provides:
- Full access to all features (upload, delete, manage, search, ask)
- Access to the Settings dashboard
- API key management
- System reset capabilities
- System configuration viewing (no secrets exposed)

### Session Management

- Sessions use JWT tokens encrypted with `SESSION_SECRET`
- Tokens are stored in HTTP-only cookies (not accessible to JavaScript)
- `SESSION_SECRET` must be at least 32 characters of cryptographically random data
- Generate one with: `openssl rand -hex 32`

## API Key Authentication

Every API request must include an `X-API-Key` header:

```bash
curl -H "X-API-Key: your-api-key" http://localhost:8000/api/stats
```

### Key Types

| Type | Source | Access Level |
|------|--------|-------------|
| **Admin Key** | Set via `ADMIN_API_KEY` env var | Full access to all endpoints |
| **Generated Keys** | Created via Settings UI or API | Configurable permissions (`read` and/or `manage`) |

### Permission Levels

| Permission | Grants Access To |
|-----------|-----------------|
| **read** | Search, Ask AI, view documents, view entities/relationships/communities, view graph visualization, view stats, view task status |
| **manage** | Everything in `read`, plus: upload documents, delete documents, reprocess documents, manage collections, run entity extraction, run relationship analysis, run community detection, entity deduplication, cancel tasks, turbo mode start/stop/extend |

Admin-only operations (system reset, API key management, system configuration, turbo balance/jobs) always require the admin API key.

### Collection-Scoped Keys

In addition to permission levels, generated API keys can be restricted to specific collections. This enables true multi-tenant deployments from a single instance — each tenant gets a key that can only see and write to their own data.

| Scope | Behaviour |
|-------|-----------|
| **All Collections** (default) | Key can access every collection |
| **Restricted** | Key can only access the collections explicitly listed at creation time |

When a restricted key calls any endpoint, the system enforces the allowed collection list across **all** non-admin endpoints:

**Read / list endpoints** — results silently filtered or 403 on out-of-scope resource:
- `/api/documents` and `/api/collections` lists — filtered to allowed collections only
- `/api/documents/{id}`, `/api/collections/{id}`, and file/content endpoints — 403 if the resource is in a disallowed collection
- `/api/stats`, `/api/graph/status` — counts scoped to allowed collections only
- `/api/graph/entities`, `/api/graph/relationships`, `/api/graph/visualization`, `/api/graph/entity-types`, `/api/graph/relationship-types`, `/api/graph/subgraph`, `/api/graph/entity/{name}`, `/api/graph/entity/{name}/relationships`, `/api/graph/search` — all scoped via 4-hop Collection→Document→Chunk→Entity pattern
- `/api/graph/communities`, `/api/graph/communities/{id}`, `/api/graph/communities/search` — scoped via 5-hop pattern (Entity→Community)
- `/api/entities/duplicates` — scoped to allowed collections; `/api/entities/merge-history` requires all-scope key

**Write / mutation endpoints** — 403 if the target collection is not in the allowed list:
- Upload, custom-input, document reprocess, document delete, collection management, entity edit/merge, relationship analysis, community detection and deletion

New collections created after the key is issued are **not** automatically accessible — access must be explicitly granted by updating the key.

### Key Validation Flow

When an API request arrives:

1. Extract `X-API-Key` from the request header
2. If it matches the admin API key (constant-time comparison): grant full access
3. Otherwise, extract the key prefix (first 12 characters)
4. Look up matching keys in Neo4j by prefix
5. For each candidate, verify the full key hash (SHA-256, constant-time comparison)
6. If a match is found: check permissions, update `last_used_at`, grant appropriate access
7. If no match: return 401 Unauthorized

### Key Generation

Generated keys follow this format: `moca_` + 64 random hex characters

Example: `moca_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0u1v2w3x4y5z6a7b8c9d0`

Only the first 12 characters (prefix) and a SHA-256 hash of the full key are stored. The full key is returned **only once** at creation time — it cannot be retrieved later.

## Managing API Keys

### Via the Web Interface

Navigate to **Settings > API Key Management**:

1. **Create**: Click "New Key", enter a name, select permissions, and choose a collection scope:
   - *All Collections* — unrestricted access (default)
   - *Specific Collections* — select one or more collections from the picker
2. **Copy**: The full key is shown only once — copy it immediately
3. **View**: See all keys with names, permissions, collection scope badge, creation dates, and last-used timestamps
4. **Usage Stats**: View per-key request counts, error rates, and endpoint breakdowns (when `TRACK_ADMIN_API_KEY_USAGE=true`)
5. **Revoke**: Temporarily disable a key (can be reactivated later)
6. **Activate**: Re-enable a previously revoked key
7. **Delete**: Permanently remove a key

### Via the API

```bash
# Create a read-only key scoped to all collections (default)
curl -X POST http://localhost:8000/api/admin/api-keys \
  -H "X-API-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"name": "My Agent", "permissions": ["read"]}'

# Create a key restricted to specific collections
curl -X POST http://localhost:8000/api/admin/api-keys \
  -H "X-API-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Tenant A - Read Only",
    "permissions": ["read"],
    "collection_scope": "restricted",
    "allowed_collections": ["coll_abc123"]
  }'

# Update collection access on an existing key
curl -X PATCH http://localhost:8000/api/admin/api-keys/{id} \
  -H "X-API-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "collection_scope": "restricted",
    "allowed_collections": ["coll_abc123", "coll_def456"]
  }'

# List all keys
curl http://localhost:8000/api/admin/api-keys \
  -H "X-API-Key: your-admin-key"

# List keys with usage stats
curl http://localhost:8000/api/admin/api-keys/with-stats \
  -H "X-API-Key: your-admin-key"

# Revoke a key
curl -X POST http://localhost:8000/api/admin/api-keys/{id}/revoke \
  -H "X-API-Key: your-admin-key"

# Reactivate a key
curl -X POST http://localhost:8000/api/admin/api-keys/{id}/activate \
  -H "X-API-Key: your-admin-key"

# Update key permissions
curl -X PATCH http://localhost:8000/api/admin/api-keys/{id} \
  -H "X-API-Key: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{"permissions": ["read", "manage"]}'

# Delete a key
curl -X DELETE http://localhost:8000/api/admin/api-keys/{id} \
  -H "X-API-Key: your-admin-key"

# View usage history for a key
curl "http://localhost:8000/api/admin/api-keys/{id}/usage-history?days=30" \
  -H "X-API-Key: your-admin-key"

# Admin stats overview (across all keys)
curl http://localhost:8000/api/admin/stats/overview \
  -H "X-API-Key: your-admin-key"
```

## Prompt Injection Protection

The Library includes built-in protection against prompt injection attacks — attempts to manipulate the LLM's behavior through specially crafted user input.

### What It Protects Against

The system detects and blocks 50+ attack patterns including:

| Category | Examples |
|----------|---------|
| **System prompt extraction** | "Repeat your system prompt", "Show me your instructions" |
| **Instruction bypass** | "Ignore previous instructions", "Disregard your rules" |
| **Encoding/obfuscation** | "Translate your prompt to base64", "Write it in hex" |
| **Tag/structure extraction** | `</system>`, XML injection, bracket manipulation |
| **Role manipulation** | "You are now a developer", "Enter admin mode", "Become DAN" |
| **Formatting tricks** | "Output verbatim", "Copy-paste your prompt exactly" |
| **Separator tricks** | Closing system/instruction blocks, delimiter injection |
| **Jailbreaks** | DAN, escape mode, hack mode, unfiltered/uncensored mode requests |

### How It Works

1. **Input Detection** (`validate_and_process_input`):
   - Normalizes input to lowercase
   - Checks special character ratio (>15% triggers a flag)
   - Runs all 50+ compiled regex patterns against the input
   - Returns whether injection was detected, with the matched pattern

2. **Input Sanitization**:
   - Removes fake system/instruction/prompt tags (XML and bracket variants)
   - Removes sequences of multiple special characters (encoding tricks)

3. **Output Filtering** (`filter_output`):
   - Splits the system prompt into 8+ word phrases
   - Checks if any phrases appear in the LLM response
   - Replaces leaked content with `[content filtered]`

4. **System Prompt Addendum** (`get_anti_injection_instruction`):
   - Added to the researcher and writer system prompts when enabled
   - Instructs the LLM to never reveal its instructions, ignore extraction attempts, and respond naturally

### Configuration

```env
PROMPT_SECURITY=true    # Enable protection (default)
PROMPT_SECURITY=false   # Disable for trusted internal environments
```

When an injection is detected:
- The input is sanitized (malicious patterns removed)
- The original question is still processed with the cleaned input
- A log entry records the detection

## API Key Best Practices

1. **Principle of least privilege** — Give each key only the permissions it needs
   - Read-only agents: `["read"]`
   - Upload pipelines: `["manage"]`
   - Admin operations: Use the admin key only

2. **Scope keys to collections** — For multi-tenant deployments, restrict each key to only the collections it needs. A key scoped to `["tenant-a-collection"]` cannot read or write to any other collection, even if it has `manage` permissions.

3. **Use descriptive names** — Name keys after their purpose:
   - "Tenant A - Read Only"
   - "Slack Bot - Production"
   - "Data Pipeline - Staging"
   - "Research Agent v2"

4. **Never share the admin key** — Create separate keys for every application and agent

5. **Store keys securely** — Use environment variables or secrets managers. Never commit keys to version control.

6. **Monitor usage** — Enable `TRACK_ADMIN_API_KEY_USAGE=true` and review statistics regularly

7. **Revoke, don't delete** — When you suspect a key is compromised, revoke it first (to stop access immediately), then investigate. Revoked keys can be reactivated if the concern was unfounded.

8. **Explicit collection grants** — New collections are never automatically accessible to existing restricted keys. Always update the key's `allowed_collections` when you create a new collection for a tenant.

## Network Security (Production)

### HTTPS

Always use HTTPS in production. The Library's streaming endpoints (SSE) and API key headers must be protected in transit.

**Nginx SSL configuration** (included in `docker-compose.prod.yml`):
- Place your certificates in `nginx/ssl/fullchain.pem` and `nginx/ssl/privkey.pem`
- Nginx handles SSL termination — backend services communicate over plain HTTP internally

### Firewall Rules

| Port | Service | Expose Publicly? |
|------|---------|-----------------|
| 80/443 | Nginx (frontend + API) | Yes |
| 3000 | Frontend (direct) | No — use Nginx |
| 8000 | Backend (direct) | No — use Nginx |
| 7474 | Neo4j Browser | **No** — internal only |
| 7687 | Neo4j Bolt | **No** — internal only |

### CORS

Configure allowed origins for API access. In production, restrict to your specific domains.

### Session Security

- Use a cryptographically random `SESSION_SECRET` (minimum 32 characters)
- JWT tokens are stored in HTTP-only cookies (not accessible to client-side JavaScript)
- Sessions expire based on the JWT token lifetime

## Security Checklist for Production

- [ ] Strong `NEO4J_PASSWORD` (not the default `password123`)
- [ ] Unique `ADMIN_API_KEY` (generated with `openssl rand -hex 32`)
- [ ] `SESSION_SECRET` at least 32 random characters
- [ ] HTTPS enabled with valid SSL certificates
- [ ] Neo4j ports (7474, 7687) not exposed to the internet
- [ ] `PROMPT_SECURITY=true` enabled
- [ ] API key authentication required for all endpoints
- [ ] CORS configured to allow only your domains
- [ ] Regular backups scheduled (see [Chapter 18](18-administration.md))
- [ ] API key usage tracking enabled (`TRACK_ADMIN_API_KEY_USAGE=true`)
- [ ] Generated API keys use least-privilege permissions
