# Pricing Limits — Tracking & Enforcement Status

> Source of truth for alignment between [cortex.moca.qwellco.de/pricing](https://cortex.moca.qwellco.de/pricing) and what the backend can actually track / enforce via environment variables. Use this as the planning document for wiring up per-tier limits.

---

## 1. Purpose

The public pricing page promises per-tier hard limits on Files, Entities, Queries, and Collections, plus feature toggles (premium apps, usage analytics, advanced security). This document inventories each promise and maps it to:

- what the backend currently **tracks** (metrics exist in code),
- what it currently **enforces** (requests are rejected when exceeded),
- the **env var(s)** that already control it (or would need to be added),
- the **gap** between today's behavior and the pricing-page contract.

No code changes here — this is the specification for later implementation work.

**Design principle — env-var-only control.** The target end state is that every pricing limit and every tier assignment is controlled by an environment variable. Once the roadmap in §6 is complete, operators change caps or move a customer between tiers by editing env config (e.g., in Coolify) and redeploying — no admin UI, no database migration, no code change. The only per-record data that remains in the database is *attribution* (which key owns which document/collection) — not configuration. See §8 for the end-to-end operator workflow.

---

## 2. Pricing Tiers Summary

| Tier | Price | Files | Entities | Queries | Collections | Apps | Support / Extras |
|---|---|---|---|---|---|---|---|
| **Free** | $0 | 20 | 500 | 1,000 | 1 | Free apps | Community support |
| **Individual** | $19/mo | 500 | 10,000 | 10,000 | 10 | Free apps | Email support |
| **Enthusiast** ⭐ (Most Popular) | $79/mo | 3,000 | 100,000 | 100,000 | 100 | Free + select premium | Priority support, Usage analytics |
| **Business** | $249/mo | 25,000 | 1,000,000 | Unlimited | Unlimited | All premium | Advanced security, Dedicated support |
| **Enterprise** | Custom | Custom | Custom | Custom | Custom | Custom | Bespoke app dev, dedicated infra |
| **MOCA Flatrate** | Free (DeCC0 / $MOCA staking) | Same as Enthusiast | | | | | |

Universal features across all tiers: **GraphRAG entity extraction**, **Ask AI with citations**, **API access**.

---

## 3. Limit Enforcement Matrix

Status legend: ✅ Ready · 🟡 Partial · 🔴 Missing · ⚪ Non-technical (out of scope)

| # | Pricing Promise | Tracked? | Enforced? | Existing Env Var | Status |
|---|---|---|---|---|---|
| 1 | Files / tier | Yes (global) | Yes (global, at upload) | `MAX_FILES` | 🟡 Partial — global only, not per-key |
| 2 | Entities / tier | Yes (global count) | No | *(none)* | 🔴 Missing |
| 3 | Queries / tier / month | Yes (per-key, daily rollup) | No | *(none)* | 🟡 Tracked, not enforced |
| 4 | Collections / tier | Yes (global) | No (config exists, unused) | `MAX_COLLECTIONS` | 🟡 Partial — code gap |
| 5 | Free apps vs Premium apps | Partial (skill registry) | No | `ENABLE_SKILLS`, `MAX_SKILL_TOOLS` | 🟡 No tier tagging |
| 6 | GraphRAG entity extraction | N/A (boolean feature) | Yes (global toggle) | `ENABLE_GRAPH_EXTRACTION` | ✅ Ready |
| 7 | Ask AI with citations | N/A (boolean feature) | Yes (global toggle) | `ENABLE_AGENT_CHAT`, `ENABLE_AGENT_RESEARCH` | ✅ Ready |
| 8 | API access (rate limits) | Yes (request counts) | No (no middleware) | *(none)* | 🟡 No rate limiting |
| 9 | Usage analytics (Enthusiast+) | Yes (admin view) | No (no tier gating) | *(none)* | 🟡 No self-service endpoint |
| 10 | Support (Community/Email/Priority/Dedicated) | — | — | — | ⚪ Non-technical |
| 11 | Advanced security (Business+) | Partial | Partial | `PROMPT_SECURITY` | 🟡 Under-defined |
| 12 | Bespoke app dev (Enterprise) | — | — | — | ⚪ Non-technical |
| 13 | **Prerequisite: tier concept on API keys** | No | No | proposed: `API_KEY_TIERS`, `DEFAULT_TIER` | 🟡 Env-var driven at auth time — no DB migration |
| 14 | MOCA Flatrate eligibility (DeCC0 / $MOCA) | No | No | *(none)* | 🔴 Needs on-chain verification |

---

## 4. Detailed Per-Limit Analysis

### 4.1 Files

- **Pricing values**: 20 / 500 / 3,000 / 25,000 / Unlimited
- **Tracked**: Global document count via `neo4j_service.get_stats()["document_count"]`.
- **Enforced**: Yes, globally, at [backend/app/main.py:548-556](backend/app/main.py#L548-L556) when `max_files > 0`.
- **Existing env var**: `max_files` at [backend/app/config.py:97-99](backend/app/config.py#L97-L99) (default `0` = unlimited).
- **Gap**: Global ceiling shared across all API keys — two Free-tier keys would together hit a 20-file cap, not 20 each. No attribution of documents to the key that uploaded them.
- **Fix path**:
  1. Add `owner_key_id` property to `Document` nodes, set during upload.
  2. In the upload endpoint, swap the global `get_stats` check for a per-key count `MATCH (d:Document {owner_key_id: $key_id}) RETURN count(d)`.
  3. New env vars `TIER_<TIER>_MAX_FILES` (one per tier).

### 4.2 Entities

- **Pricing values**: 500 / 10,000 / 100,000 / 1,000,000 / Unlimited
- **Tracked**: Global entity count via `get_stats()["entity_count"]`.
- **Enforced**: No.
- **Existing env var**: None.
- **Gap**: Entities are produced by the extraction pipeline from documents, not by direct user action. They are shared globally in Neo4j — the same entity node can be referenced by documents from multiple keys.
- **Fix path**: Attribute entities via their source documents: an entity "belongs to" key `K` if any document with `owner_key_id = K` has a chunk that mentions it.
  1. Add a pre-extraction quota check in the extraction pipeline that counts `COUNT(DISTINCT e) WHERE EXISTS ((e)<-[:MENTIONS]-(:Chunk)<-[:HAS_CHUNK]-(:Document {owner_key_id: $key_id}))`.
  2. If adding a new document would push the count over the tier cap, skip extraction or defer it until the key is upgraded.
  3. New env vars `TIER_<TIER>_MAX_ENTITIES`.
- **Note**: Entity count may grow asynchronously after upload (during background extraction) — enforcement cannot be purely upload-time; it must also apply at extraction time. See [.claude/domain/relationships.md](.claude/domain/relationships.md) and [.claude/domain/entities.md](.claude/domain/entities.md).

### 4.3 Queries

- **Pricing values**: 1,000 / 10,000 / 100,000 / Unlimited / Unlimited
- **Tracked**: Yes, per-key with daily granularity. Every request to `/api/ask*` or `/api/search*` increments `APIKeyUsageLog.ep_ask` or `ep_ask` category via the existing `APIUsageMiddleware` at [backend/app/main.py:260-298](backend/app/main.py#L260-L298).
- **Enforced**: No — middleware records usage but does not reject requests when a cap is reached.
- **Existing env var**: None.
- **Gap**: No env var for the monthly limit, no quota check before request processing.
- **Fix path**:
  1. In `APIUsageMiddleware` (or a new dedicated middleware placed *before* it), for requests to `/api/ask*` and `/api/search*`, sum `ep_ask + ep_search` across `APIKeyUsageLog` rows for the current UTC month for the caller's `key_id`.
  2. If the sum exceeds the tier cap, short-circuit with a 429 response (`Retry-After` = seconds until month rollover).
  3. New env vars `TIER_<TIER>_MAX_QUERIES_PER_MONTH`. Use sentinel `0` or `-1` for "unlimited" (Business / Enterprise).
- **Aggregation query already exists**: `neo4j_service.get_api_key_stats()` and `get_api_key_usage_history()` at [backend/app/services/neo4j_service.py](backend/app/services/neo4j_service.py) (lines ~5002–5109 per exploration) — reuse them.

### 4.4 Collections

- **Pricing values**: 1 / 10 / 100 / Unlimited / Unlimited
- **Tracked**: Yes, globally.
- **Enforced**: **No** — `max_collections` config exists at [backend/app/config.py:100-102](backend/app/config.py#L100-L102) but is **not referenced** in the `POST /api/collections` endpoint. This is a pre-existing bug: the knob is declared but ignored.
- **Existing env var**: `max_collections` (default `0` = unlimited).
- **Gap**:
  - Enforcement code missing entirely.
  - Even if added, limit is global, not per-key.
- **Fix path**:
  1. Add `owner_key_id` property to `Collection` nodes.
  2. Add per-key count check in the collection-creation endpoint before creating.
  3. New env vars `TIER_<TIER>_MAX_COLLECTIONS`.
- **Note**: The Free tier allows exactly 1 collection, which likely means the auto-created `default` collection — per-key attribution must count the default collection toward the tier's cap.

### 4.5 Free Apps vs Premium Apps

- **Pricing values**: Free (Free/Individual) · Free + select premium (Enthusiast) · All premium (Business) · Custom (Enterprise)
- **Tracked**: Skills are stored as `Skill` nodes in Neo4j with enable/disable and config schema. See [.claude/domain/skills.md](.claude/domain/skills.md).
- **Enforced**: Global enable/disable exists via `ENABLE_SKILLS`, `MAX_SKILL_TOOLS`, `ENABLE_SKILL_SCRIPTS`. No tier-aware filtering.
- **Existing env vars**: `ENABLE_SKILLS`, `MAX_SKILL_TOOLS`, `MAX_SKILL_INSTRUCTIONS_TOKENS`, `SKILLS_DIR`, `ENABLE_SKILL_SCRIPTS`, `SKILL_SCRIPT_TIMEOUT`, `SKILL_HTTP_TIMEOUT`.
- **Gap**: No `tier_required` (or `premium: bool`) metadata on skills. No filter in the skill-loading path that hides premium skills from lower-tier keys.
- **Fix path** (env-var only, no DB changes):
  1. Define tier mapping purely via env vars: `PREMIUM_SKILL_IDS` (comma-separated skill IDs available from Enthusiast+) and `BUSINESS_ONLY_SKILL_IDS` (Business+ only). Free / Individual keys see only skills in neither list.
  2. For large skill catalogs, support `SKILL_TIER_REGISTRY_PATH` pointing at a YAML file with the same mapping (operator edits one file instead of a long env var).
  3. `skill_service` loads the lists at startup and filters skills by comparing them against `auth.tier` on every request.
  4. `Skill` Neo4j nodes stay unchanged — tiering is purely configuration.
- **Product decision needed**: which specific skills are "premium"? The pricing page does not enumerate them — but whichever they are, they go in the env var, not the database.

### 4.6 GraphRAG Entity Extraction

- **Available to**: All tiers including Free.
- **Status**: ✅ Ready. Already controlled by `ENABLE_GRAPH_EXTRACTION` at [backend/app/config.py:125-127](backend/app/config.py#L125-L127). Global toggle — sufficient because every tier has it enabled.
- **Future consideration**: If a future tier disables extraction, per-key toggle required.

### 4.7 Ask AI with Citations

- **Available to**: All tiers.
- **Status**: ✅ Ready. Controlled by `ENABLE_AGENT_CHAT` and `ENABLE_AGENT_RESEARCH` at [backend/app/config.py:222-230](backend/app/config.py#L222-L230). Citations are intrinsic to the RAG pipeline output and do not require a separate flag.

### 4.8 API Access (Rate Limiting)

- **Available to**: All tiers.
- **Status**: Core API-key system is ready ([backend/app/services/api_key_service.py](backend/app/services/api_key_service.py)) with READ/MANAGE permissions and collection scoping. **No rate limiting** is currently implemented.
- **Gap**: No middleware enforces requests-per-minute / requests-per-second. The Free tier especially needs RPM throttling to prevent abuse within the monthly query cap.
- **Fix path**:
  1. Add a rate-limit middleware keyed by `key_id` (e.g., using a sliding window in Redis, or an in-process token bucket if Redis is not introduced yet).
  2. New env vars `TIER_<TIER>_RPM` (requests per minute) and optionally `TIER_<TIER>_BURST`.
- **Suggested defaults**: `TIER_FREE_RPM=30`, `TIER_INDIVIDUAL_RPM=120`, `TIER_ENTHUSIAST_RPM=300`, `TIER_BUSINESS_RPM=1200`.

### 4.9 Usage Analytics (Enthusiast+)

- **Available to**: Enthusiast, Business, Enterprise.
- **Status**: Backend data is ready — per-key stats and history already computed by `neo4j_service.get_api_key_stats()` and `get_api_key_usage_history()`. Admin view exists at [frontend/src/components/admin/UsageChart.tsx](frontend/src/components/admin/UsageChart.tsx).
- **Gap**: No **self-service** user-facing endpoint (an Enthusiast customer cannot see their own stats). No tier check gating visibility.
- **Fix path**:
  1. Add `GET /api/me/usage` endpoint returning the authenticated key's own stats (request count by period, endpoint category breakdown, remaining quota).
  2. Gate by `auth.tier in {enthusiast, business, enterprise}`.
  3. Surface in the frontend for logged-in users.
- **No new env var required** — the feature flag is implicit in the tier.

### 4.10 Support Tiers (Community / Email / Priority / Dedicated)

- ⚪ **Non-technical.** Handled outside the product (help desk, SLA, email routing). Not an env-var concern.

### 4.11 Advanced Security (Business+)

- **Available to**: Business, Enterprise.
- **Status**: Under-defined. The pricing page says "Advanced security" without listing concrete deliverables.
- **Currently exists**: `PROMPT_SECURITY` env var at [backend/app/config.py:320-322](backend/app/config.py#L320-L322), global, default on.
- **Plausible interpretations** (need product decision):
  - **IP allowlisting** per API key — new env var `TIER_BUSINESS_ENABLE_IP_ALLOWLIST` + a `allowed_ips` property on `APIKey` nodes.
  - **Audit logs** — per-key action log with longer retention than free tiers (requires a new `AuditLog` node type).
  - **SSO / SAML** — significant auth refactor.
  - **Key rotation policies** — expiry/rotation metadata on `APIKey`.
  - **Enhanced prompt-injection defenses** — tier-aware `PROMPT_SECURITY` level.
- **Fix path**: Each deliverable maps to a dedicated env var — see §5 for a concrete starter set (`API_KEY_IP_ALLOWLISTS`, `TIER_*_AUDIT_LOG_RETENTION_DAYS`, `TIER_*_PROMPT_SECURITY_LEVEL`, `TIER_BUSINESS_KEY_ROTATION_RECOMMEND_DAYS`). Product still needs to decide *which* features ship first, but the env-var surface is pre-declared so when the decision lands, wiring is mechanical.

### 4.12 Bespoke App Development / Dedicated Infra (Enterprise)

- ⚪ **Non-technical.** Enterprise deals are custom by definition. No env var needed.

### 4.13 Prerequisite: Tier Concept on API Keys

- **Status**: 🟡 Resolvable via env-var lookup — **no DB migration required**.
- **Design**: Tier is **not persisted** in Neo4j. Instead, it is computed at auth time from two env vars:
  - `API_KEY_TIERS` — comma-separated map of API-key `name` → tier. Example: `customer-alice:enthusiast,customer-bob:business,test-key:free`.
  - `DEFAULT_TIER` — tier assigned to any key not listed in `API_KEY_TIERS` (default: `free`).
  - The admin key (authenticated by `ADMIN_API_KEY`) is always treated as `enterprise` regardless of the mapping.
- **Fix path**:
  1. Add `tier: Tier` field to the `AuthResult` dataclass at [backend/app/services/auth_service.py](backend/app/services/auth_service.py), populated in `validate_api_key()`.
  2. Ship a helper `resolve_tier(api_key_name: str) -> Tier` that parses `API_KEY_TIERS` once at startup and looks up the name; falls back to `DEFAULT_TIER`.
  3. For deployments with many customers, support `API_KEY_TIERS_FILE` pointing at a YAML file with the same mapping (avoids a pathologically long env var).
  4. No admin UI — tier assignment is operator-controlled via env vars (see §8 for the workflow).
- **Zero migration**: No Neo4j schema changes, no data backfill, no Pydantic-model changes to existing persisted shapes. A redeploy with a new `API_KEY_TIERS` value takes effect on the next request.

### 4.14 MOCA Flatrate (DeCC0 / $MOCA staking)

- **Status**: 🔴 Missing. Eligibility cannot be verified from inside the current codebase — it requires reading on-chain state (NFT ownership or staked balance).
- **Fix path** (out of scope for env-var-only work):
  1. Wallet-linking flow: user signs a message with their wallet → backend verifies the signature and stores the linked address on the API key.
  2. Periodic job checks on-chain state (DeCC0 NFT ownership, $MOCA staking balance) and adjusts the key's `tier` accordingly.
  3. Env vars for the verification layer: `MOCA_RPC_URL`, `DECC0_CONTRACT_ADDRESS`, `MOCA_TOKEN_ADDRESS`, `MOCA_MIN_STAKE_AMOUNT`, `MOCA_ELIGIBILITY_TIER` (= `enthusiast`).
- **Note**: This is a feature, not just a limit — it warrants its own design doc separate from PRICING.md.

---

## 5. Proposed Env Var Surface

A consolidated list of env vars to add to [.env.example](.env.example) and declare in [backend/app/config.py](backend/app/config.py). **Sentinel value `0` or `-1` means "unlimited"** to match the existing `MAX_FILES=0` convention.

```bash
# =============================================================================
# Tier-based pricing limits (one set per tier)
# =============================================================================
# Values mirror cortex.moca.qwellco.de/pricing. Change here to change caps.
# Use 0 for "unlimited" (Business queries/collections, Enterprise everywhere).

# --- Free tier ---
TIER_FREE_MAX_FILES=20
TIER_FREE_MAX_ENTITIES=500
TIER_FREE_MAX_QUERIES_PER_MONTH=1000
TIER_FREE_MAX_COLLECTIONS=1
TIER_FREE_RPM=30

# --- Individual tier ---
TIER_INDIVIDUAL_MAX_FILES=500
TIER_INDIVIDUAL_MAX_ENTITIES=10000
TIER_INDIVIDUAL_MAX_QUERIES_PER_MONTH=10000
TIER_INDIVIDUAL_MAX_COLLECTIONS=10
TIER_INDIVIDUAL_RPM=120

# --- Enthusiast tier (MOCA Flatrate uses these) ---
TIER_ENTHUSIAST_MAX_FILES=3000
TIER_ENTHUSIAST_MAX_ENTITIES=100000
TIER_ENTHUSIAST_MAX_QUERIES_PER_MONTH=100000
TIER_ENTHUSIAST_MAX_COLLECTIONS=100
TIER_ENTHUSIAST_RPM=300

# --- Business tier ---
TIER_BUSINESS_MAX_FILES=25000
TIER_BUSINESS_MAX_ENTITIES=1000000
TIER_BUSINESS_MAX_QUERIES_PER_MONTH=0       # unlimited
TIER_BUSINESS_MAX_COLLECTIONS=0              # unlimited
TIER_BUSINESS_RPM=1200

# --- Enterprise tier (no caps by default; override per deployment) ---
TIER_ENTERPRISE_MAX_FILES=0
TIER_ENTERPRISE_MAX_ENTITIES=0
TIER_ENTERPRISE_MAX_QUERIES_PER_MONTH=0
TIER_ENTERPRISE_MAX_COLLECTIONS=0
TIER_ENTERPRISE_RPM=0

# =============================================================================
# API key → tier assignment (env-var driven, no DB migration)
# =============================================================================
# Comma-separated mapping of API-key `name` (set at creation) to tier.
# Valid tiers: free | individual | enthusiast | business | enterprise
# Example: API_KEY_TIERS=customer-alice:enthusiast,customer-bob:business,internal-test:free
API_KEY_TIERS=
# Default tier for any key NOT listed in API_KEY_TIERS.
DEFAULT_TIER=free
# Optional: path to a YAML file with the same mapping (use for large deployments).
# YAML schema: a top-level dict { "<key-name>": "<tier>", ... }
# API_KEY_TIERS_FILE=/app/config/key_tiers.yaml

# =============================================================================
# Premium app gating
# =============================================================================
# Comma-separated skill IDs that require an Enthusiast tier or above.
PREMIUM_SKILL_IDS=
# Comma-separated skill IDs that require a Business tier.
BUSINESS_ONLY_SKILL_IDS=
# Alternative: point at a YAML file mapping skill_id -> tier_required
# SKILL_TIER_REGISTRY_PATH=/app/config/skill_tiers.yaml

# =============================================================================
# Advanced security (Business+) — each env var toggles one concrete feature.
# Ship incrementally as product decides which features go in "Advanced security".
# =============================================================================
# IP allowlisting per key: comma-separated `key_name:CIDR` pairs.
# Example: API_KEY_IP_ALLOWLISTS=customer-bob:10.0.0.0/24,customer-carol:203.0.113.7/32
API_KEY_IP_ALLOWLISTS=
# Audit-log retention days per tier (0 = audit logging disabled for that tier).
# Enterprise uses 0 as sentinel for "unlimited retention".
TIER_FREE_AUDIT_LOG_RETENTION_DAYS=0
TIER_INDIVIDUAL_AUDIT_LOG_RETENTION_DAYS=0
TIER_ENTHUSIAST_AUDIT_LOG_RETENTION_DAYS=30
TIER_BUSINESS_AUDIT_LOG_RETENTION_DAYS=365
TIER_ENTERPRISE_AUDIT_LOG_RETENTION_DAYS=0
# Recommended key-rotation cadence (days). Surfaced in UI; not auto-enforced yet.
TIER_BUSINESS_KEY_ROTATION_RECOMMEND_DAYS=90
# Tier-aware prompt-injection defense level: off | basic | strict
TIER_FREE_PROMPT_SECURITY_LEVEL=basic
TIER_INDIVIDUAL_PROMPT_SECURITY_LEVEL=basic
TIER_ENTHUSIAST_PROMPT_SECURITY_LEVEL=basic
TIER_BUSINESS_PROMPT_SECURITY_LEVEL=strict
TIER_ENTERPRISE_PROMPT_SECURITY_LEVEL=strict

# =============================================================================
# MOCA Flatrate eligibility (separate design; listed for completeness)
# =============================================================================
# MOCA_RPC_URL=
# DECC0_CONTRACT_ADDRESS=
# MOCA_TOKEN_ADDRESS=
# MOCA_MIN_STAKE_AMOUNT=
# MOCA_ELIGIBILITY_TIER=enthusiast
```

Existing env vars that **stay** and cover existing functionality: `MAX_FILES`, `MAX_COLLECTIONS` (kept as a global safety ceiling applied on top of tier limits), `MAX_FILE_SIZE_MB`, `ENABLE_GRAPH_EXTRACTION`, `ENABLE_AGENT_CHAT`, `ENABLE_AGENT_RESEARCH`, `ENABLE_SKILLS`, `MAX_SKILL_TOOLS`, `PROMPT_SECURITY`.

---

## 6. Implementation Roadmap

Phased so each phase is independently shippable and the system stays in a consistent state between phases.

### Phase 1 — Foundations (blocks everything else)
- Wire `API_KEY_TIERS` + `DEFAULT_TIER` (+ optional `API_KEY_TIERS_FILE`) env vars; add computed `tier` field to `AuthResult` at auth time (**no DB migration for tier itself**).
- Add `owner_key_id` property to `Document` and `Collection` nodes and populate it on creation — this is the *only* schema change in this phase, and it's data attribution, not configuration.
- Backfill: existing documents/collections get `owner_key_id` from the admin key (or any recoverable association).
- **No admin UI built** — tier assignment is purely operator-controlled via env vars (see §8).

### Phase 2 — Files & Collections enforcement (simple, high-value)
- Swap the global `max_files` check in the upload endpoint for a tier-aware per-key check.
- Implement the missing `max_collections` check in collection creation, also tier-aware.
- Wire the `TIER_*_MAX_FILES` / `TIER_*_MAX_COLLECTIONS` env vars.

### Phase 3 — Query quota (requires new middleware)
- Add a quota-check middleware in front of `APIUsageMiddleware` for `/api/ask*` and `/api/search*`.
- Use existing `get_api_key_usage_history()` aggregation to sum monthly queries.
- Return 429 with `Retry-After` when exceeded.
- Wire the `TIER_*_MAX_QUERIES_PER_MONTH` env vars.

### Phase 4 — Entity attribution
- Add the entity-count-per-key Cypher query.
- Enforce at extraction time (not at upload), rejecting the extraction task if it would exceed the cap.
- Wire the `TIER_*_MAX_ENTITIES` env vars.

### Phase 5 — Feature gating and polish
- Add per-tier RPM rate limiting middleware.
- Filter skills in `skill_service` by reading `PREMIUM_SKILL_IDS` / `BUSINESS_ONLY_SKILL_IDS` (or `SKILL_TIER_REGISTRY_PATH` YAML) against `auth.tier`. **No DB changes** — skill tiering is config, not data.
- Add `GET /api/me/usage` self-service analytics endpoint, gated by tier ≥ enthusiast.

### Phase 6 — Blocked on product decisions
- "Advanced security" (Business+): decide concrete deliverables — IP allowlist, audit logs, SSO, key rotation, or enhanced prompt-injection defense.
- MOCA Flatrate: design wallet-link + on-chain verification flow (separate doc).

---

## 7. Known Non-Technical Items

These pricing-page items are **not** part of the env-var surface:

- **Community / Email / Priority / Dedicated support** — handled by the support team, not the application.
- **Bespoke app development** (Enterprise) — one-off engineering engagements.
- **"Most Popular" badge** on Enthusiast — marketing copy, not a feature.

---

## 8. Operator Workflow

Every pricing limit is controlled by env vars. Operators adjust limits by editing env config and redeploying — no database migrations, no admin UI, no code changes.

### Scenario A — A customer upgrades from Individual to Enthusiast

1. Look up the customer's API-key `name` (the value set when the key was created).
2. In the deployment's env config (e.g., Coolify env vars UI), edit `API_KEY_TIERS`:
   ```
   API_KEY_TIERS=customer-alice:enthusiast,customer-bob:business
   ```
3. Redeploy the backend.
4. On the next request from that key, `validate_api_key` reads the new mapping; every tier-aware check (files, entities, queries, collections, RPM, premium skills, audit-log retention, prompt-security level) immediately uses the new tier's caps.

### Scenario B — Change the Free-tier file cap from 20 to 30

1. Edit `TIER_FREE_MAX_FILES=30` in env config.
2. Redeploy.
3. All Free-tier keys now count up to 30 files before rejection.

### Scenario C — Add a MOCA Flatrate customer (manual, pre-automation)

1. Issue an API key through the normal flow; record the `name` (e.g., `moca-0xabc...`).
2. Add to `API_KEY_TIERS`:
   ```
   API_KEY_TIERS=...,moca-0xabc:enthusiast
   ```
3. Redeploy.
4. When on-chain verification ships (§4.14), this becomes a scheduled job writing to the same env var surface — the operator workflow stays the same.

### Scenario D — Roll out a new premium skill (e.g., `skill-x`) to Enthusiast+

1. Edit `PREMIUM_SKILL_IDS=skill-x,skill-y,skill-z` in env config.
2. Redeploy.
3. `skill_service` filters the skill out for Free / Individual keys; Enthusiast+ keys see it.

### Scenario E — Tighten prompt-security for Business tier

1. Edit `TIER_BUSINESS_PROMPT_SECURITY_LEVEL=strict` in env config.
2. Redeploy.
3. Business-tier requests run through the strict defense path.

### What is *not* controlled by env var

Only per-record data that is inherent to what the system stores lives in the database:

- `owner_key_id` on `Document` / `Collection` nodes — set at creation to attribute the record to the API key that created it. This is *data*, not config; it is what the tier-aware counts are counted against.

Every **limit**, every **tier assignment**, every **feature gate** is env-var driven.

---

## 9. Verification Checklist

To keep this doc accurate, on every change to tiers or backend limits:

1. Open [cortex.moca.qwellco.de/pricing](https://cortex.moca.qwellco.de/pricing) side-by-side with §2 — every number matches.
2. Every env var listed in §5 is either (a) present in [backend/app/config.py](backend/app/config.py), or (b) tagged here as "to be added".
3. Every file path reference resolves to the line claimed (re-check after refactors).
4. When a phase from §6 ships, move the corresponding rows in §3 from 🟡/🔴 to ✅ and update [.env.example](.env.example) + [.claude/environment.md](.claude/environment.md) to match.

Related docs to update alongside this one:
- [.claude/environment.md](.claude/environment.md) — add tier env vars under a new "Pricing Tier Limits" section.
- [.claude/domain/admin-features.md](.claude/domain/admin-features.md) — document the `tier` field on API keys once added.
- [.claude/domain/skills.md](.claude/domain/skills.md) — document premium skill gating once added.
- [.env.example](.env.example) — keep env-var block in sync with §5.
