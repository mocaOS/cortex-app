# Pricing Limits — Tracking & Enforcement Status

> Source of truth for alignment between [cortex.moca.qwellco.de/pricing](https://cortex.moca.qwellco.de/pricing) and what the backend can actually track / enforce via environment variables. Cortex is sold as a single-tenant deployment — each customer runs their own instance, so **all limits are global to the instance**, not per-API-key.

---

## 1. Purpose

The public pricing page promises hard limits on Files, Entities, Queries, and Collections, plus feature toggles (premium apps, usage analytics, advanced security). Each pricing tier corresponds to a **specific set of env-var values applied to that customer's instance**. There is no per-key / per-user quota inside a shared multi-tenant system — the instance itself *is* the tier.

This document inventories each pricing-page promise and maps it to:
- what the backend currently **tracks** (metrics exist in code),
- what it currently **enforces** (requests are rejected when exceeded),
- the **env var(s)** that control it (or need to be added),
- the **gap** between today's behavior and the pricing-page contract.

**Design principle — env-var-only, global per-instance control.** Every pricing limit is a single global env var. Operators change caps by editing env config (e.g., in Coolify) and redeploying. No per-key attribution, no tier concept stored in the database, no admin UI, no database migration. See §8 for the end-to-end operator workflow.

---

## 2. Pricing Tiers Summary

Each tier is a different env-var configuration applied to a fresh instance. When a customer signs up, the operator deploys a Cortex instance with env vars matching the tier's numbers from §5.

| Tier | Price | Files | Entities | Queries/mo | Collections | Premium Apps | Advanced Security |
|---|---|---|---|---|---|---|---|
| **Free** | $0 | 20 | 500 | 1,000 | 1 | No | No |
| **Individual** | $29/mo | 500 | 10,000 | 10,000 | 10 | No | No |
| **Enthusiast** ⭐ (Most Popular) | $99/mo | 3,000 | 100,000 | 100,000 | 100 | Select | No |
| **Business** | $299/mo | 25,000 | 1,000,000 | Unlimited | Unlimited | All | Yes |
| **Enterprise** | Custom | Unlimited | Unlimited | Unlimited | Unlimited | All | Yes |
| **MOCA Flatrate** (DeCC0 / $MOCA staking) | Free | = Enthusiast values | | | | | |

Universal features across all tiers: **GraphRAG entity extraction**, **Ask AI with citations**, **API access**. Sentinel `0` in env vars means "unlimited".

---

## 3. Limit Enforcement Matrix

Status legend: ✅ Ready · 🟡 Partial · 🔴 Missing · ⚪ Non-technical (out of scope)

| # | Pricing Promise | Tracked? | Enforced? | Env Var | Status |
|---|---|---|---|---|---|
| 1 | Files | Yes (global) | Yes (global, at upload + custom input) | `MAX_FILES` | ✅ Ready |
| 2 | Entities | Yes (global count) | Yes (global, at upload + custom input) | `MAX_ENTITIES` | ✅ Ready |
| 3 | Queries / month | Yes (per-key daily log; trivially summed) | Yes (global, sum of `ep_ask + ep_search` across all keys) | `MAX_QUERIES_PER_MONTH` | ✅ Ready |
| 4 | Collections | Yes (global) | Yes (global, at creation) | `MAX_COLLECTIONS` | ✅ Ready |
| 5 | Premium apps | Via skill management | Partial | `ENABLED_SKILL_IDS` (new) | 🟡 Needs whitelist |
| 6 | GraphRAG entity extraction | N/A (feature) | Yes | `ENABLE_GRAPH_EXTRACTION` | ✅ Ready |
| 7 | Ask AI with citations | N/A (feature) | Yes | `ENABLE_AGENT_CHAT`, `ENABLE_AGENT_RESEARCH` | ✅ Ready |
| 8 | API rate limit | Yes (request counts) | No | `MAX_RPM` (new) | 🟡 No rate-limit middleware |
| 9 | Usage analytics (self-service) | Yes (admin only) | Partial | `ENABLE_SELF_SERVICE_ANALYTICS` (new) | 🟡 No user-facing endpoint |
| 10 | Support (Community/Email/Priority/Dedicated) | — | — | — | ⚪ Non-technical |
| 11 | Advanced security (Business+) | Partial | Partial | `ENABLE_IP_ALLOWLIST`, `AUDIT_LOG_RETENTION_DAYS`, `PROMPT_SECURITY_LEVEL`, … (all new) | 🟡 Feature set under-defined |
| 12 | Bespoke Enterprise work | — | — | — | ⚪ Non-technical |
| 13 | MOCA Flatrate eligibility | No | No | *(out of scope — on-chain verification)* | 🔴 Separate design |

---

## 4. Detailed Per-Limit Analysis

### 4.1 Files

- **Pricing values**: 20 / 500 / 3,000 / 25,000 / Unlimited
- **Tracked**: Global document count via `neo4j_service.get_stats()["document_count"]`.
- **Enforced**: Yes, globally, at [backend/app/main.py:585-597](backend/app/main.py#L585-L597) (file upload) and [backend/app/main.py:935-946](backend/app/main.py#L935-L946) (custom input creation) when `max_files > 0`.
- **Env var**: `MAX_FILES` at [backend/app/config.py:97-99](backend/app/config.py#L97-L99) (default `0` = unlimited).
- **Tests**: [backend/tests/test_max_files.py](backend/tests/test_max_files.py).
- **Status**: ✅ Ready. No code changes needed.
- **Operator action**: set `MAX_FILES` to the tier value on each instance.

### 4.2 Entities

- **Pricing values**: 500 / 10,000 / 100,000 / 1,000,000 / Unlimited
- **Tracked**: Global entity count via `get_stats()["entity_count"]`.
- **Enforced**: Yes, globally, at [backend/app/main.py:585-597](backend/app/main.py#L585-L597) (file upload) and [backend/app/main.py:935-946](backend/app/main.py#L935-L946) (custom input creation) when `max_entities > 0`. The check rejects new ingestion once the existing global entity count is at or above the cap.
- **Env var**: `MAX_ENTITIES` at [backend/app/config.py:103-105](backend/app/config.py#L103-L105) (default `0` = unlimited).
- **Tests**: [backend/tests/test_max_entities.py](backend/tests/test_max_entities.py).
- **Status**: ✅ Ready.
- **Design note**: enforcement happens at upload time (gate ingestion of new docs once the cap is reached), not mid-extraction. This means a single in-flight document can push the post-extraction count somewhat above the cap — accepted as a simplification. Surfacing the remaining-entities count to admins so they can prune before the cap is hit is still TODO.

### 4.3 Queries

- **Pricing values**: 1,000 / 10,000 / 100,000 / Unlimited / Unlimited
- **Tracked**: Per-key, daily. `APIUsageMiddleware` increments `APIKeyUsageLog.ep_ask` / `ep_search` on every call to `/api/ask*` and `/api/search*`.
- **Enforced**: Yes. `enforce_query_quota` FastAPI dependency on each chat handler (`/api/search`, `/api/ask`, `/api/ask/stream`, `/api/ask/stream/thinking`). When `MAX_QUERIES_PER_MONTH > 0`, the dependency calls `neo4j_service.get_query_count_this_month()` (sums `ep_ask + ep_search` across **all** `APIKeyUsageLog` rows for the current UTC month) and rejects with 429 + `Retry-After` (seconds until next UTC month) once the count is `>= MAX_QUERIES_PER_MONTH`.
- **Env var**: `MAX_QUERIES_PER_MONTH` (default `0` = unlimited).
- **Implementation**: dependency in [backend/app/main.py](backend/app/main.py); helper in [backend/app/services/neo4j_service.py](backend/app/services/neo4j_service.py) (`get_query_count_this_month`); tests in [backend/tests/test_max_queries_per_month.py](backend/tests/test_max_queries_per_month.py).
- **Future optimisation**: PRICING.md previously suggested caching the monthly count for a few seconds. Skipped for the initial impl — the aggregation is a single-label scan with a date filter. Add a `cachetools.TTLCache` if profiling shows it matters.

### 4.4 Collections

- **Pricing values**: 1 / 10 / 100 / Unlimited / Unlimited
- **Tracked**: Yes, globally, via `get_stats()["collection_count"]`.
- **Enforced**: Yes, globally, at [backend/app/main.py:2709-2715](backend/app/main.py#L2709-L2715) inside `POST /api/collections` when `max_collections > 0`.
- **Env var**: `MAX_COLLECTIONS` at [backend/app/config.py:100-102](backend/app/config.py#L100-L102) (default `0` = unlimited).
- **Tests**: [backend/tests/test_max_collections.py](backend/tests/test_max_collections.py).
- **Status**: ✅ Ready. The auto-created `default` collection counts toward the cap.
- **Operator action**: set `MAX_COLLECTIONS` to the tier value on each instance.

### 4.5 Premium Apps (Skills)

- **Pricing values**: Free apps only (Free/Individual) · Free + select premium (Enthusiast) · All premium (Business+) · Custom (Enterprise)
- **Tracked**: Skills stored as `Skill` nodes, managed via admin. See [.claude/domain/skills.md](.claude/domain/skills.md).
- **Enforced**: Global toggle (`ENABLE_SKILLS`); no per-skill gating.
- **Gap**: No way to restrict which skills are available beyond the global on/off.
- **Fix path**:
  1. Add `ENABLED_SKILL_IDS` env var — comma-separated whitelist of skill IDs active on this instance. Empty string = all installed skills active (backward-compatible).
  2. `skill_service` filters available skills against the whitelist at startup and on every request.
  3. Operator configures per-tier:
     - Free/Individual: only free skill IDs.
     - Enthusiast: free + chosen premium IDs.
     - Business+: leave empty (= all).
- **Alternative**: operator manually installs only the allowed skills per instance and skips the env var. The env-var whitelist is cleaner because the same image can ship all skills and the tier config filters at runtime.
- **Product decision needed**: which specific skill IDs are "free" vs "premium"? The pricing page doesn't enumerate them.

### 4.6 GraphRAG Entity Extraction

- **Available to**: All tiers.
- **Status**: ✅ Ready. `ENABLE_GRAPH_EXTRACTION` at [backend/app/config.py:125-127](backend/app/config.py#L125-L127). Global toggle, sufficient since every tier has it.

### 4.7 Ask AI with Citations

- **Available to**: All tiers.
- **Status**: ✅ Ready. `ENABLE_AGENT_CHAT` and `ENABLE_AGENT_RESEARCH` at [backend/app/config.py:222-230](backend/app/config.py#L222-L230). Citations are intrinsic to the RAG pipeline output.

### 4.8 API Access (Rate Limiting)

- **Available to**: All tiers.
- **Status**: API-key system is ready ([backend/app/services/api_key_service.py](backend/app/services/api_key_service.py)). **No rate limiting** currently.
- **Gap**: No middleware enforces requests-per-minute. Free tier especially needs throttling to prevent burning through the monthly quota in seconds.
- **Fix path**:
  1. Add `MAX_RPM` env var (default `0` = unlimited).
  2. Add a rate-limit middleware applying a sliding window **across the entire instance** (all keys share the RPM budget). In-process token bucket is fine as a starting point; migrate to Redis if multi-replica.
  3. Return 429 with `Retry-After` when exceeded.
- **Suggested tier values**: Free `30`, Individual `120`, Enthusiast `300`, Business `1200`, Enterprise `0`.

### 4.9 Usage Analytics (Self-Service)

- **Available to**: Enthusiast+ on the pricing page.
- **Status**: Admin view exists ([frontend/src/components/admin/UsageChart.tsx](frontend/src/components/admin/UsageChart.tsx)); per-key stats via `neo4j_service.get_api_key_stats()` and `get_api_key_usage_history()`.
- **Gap**: No self-service endpoint for non-admin users. No toggle.
- **Fix path**:
  1. Add `ENABLE_SELF_SERVICE_ANALYTICS` env var (boolean, default `false`). Operator sets `true` on Enthusiast+ instances.
  2. Add `GET /api/me/usage` endpoint returning the caller's own stats (queries this month, remaining quota, endpoint breakdown). Guarded by the env var.
  3. Surface in the frontend.

### 4.10 Support Tiers

- ⚪ **Non-technical.** Handled outside the product (help desk, SLA, email routing). No env var.

### 4.11 Advanced Security

- **Available to**: Business+.
- **Status**: Under-defined on the pricing page — covers multiple plausible deliverables. Each maps to its own env var so they can ship independently.
- **Currently exists**: boolean `PROMPT_SECURITY` at [backend/app/config.py:320-322](backend/app/config.py#L320-L322).
- **Proposed env vars** (each a discrete, independently-shippable feature):
  - `ENABLE_IP_ALLOWLIST` (boolean) + `IP_ALLOWLIST` (comma-separated CIDR list). When enabled, reject requests from IPs outside the list.
  - `AUDIT_LOG_RETENTION_DAYS` (int, default `0` = disabled). When > 0, log admin/destructive actions to a new `AuditLog` node type with the given retention.
  - `PROMPT_SECURITY_LEVEL` (`off` | `basic` | `strict`, default `basic`). Eventually supersedes the boolean `PROMPT_SECURITY`; ship as an additive new var first, deprecate old one after migration.
  - `API_KEY_ROTATION_RECOMMEND_DAYS` (int, default `0` = no hint). When > 0, surface a rotation reminder to admins in the UI. Not auto-enforced.
- **Fix path**: ship each one independently once product decides priorities.

### 4.12 Bespoke Enterprise / Dedicated Infra

- ⚪ **Non-technical.** Custom engagements. No env var.

### 4.13 MOCA Flatrate (DeCC0 / $MOCA staking)

- **Status**: 🔴 Out of scope for env-var-only work — requires on-chain verification of NFT ownership or $MOCA stake. For now, the operator manually provisions an Enthusiast-tier instance for a verified community member, same env vars as a paying Enthusiast.
- **Future design** (separate doc, not covered by PRICING.md):
  1. Wallet-linking: user signs a message with their wallet; backend verifies signature, stores linked address on the instance's customer record.
  2. Periodic job checks on-chain state (DeCC0 NFT / $MOCA stake) and flags instances for provisioning/revocation.
  3. New env vars for the verification layer: `MOCA_RPC_URL`, `DECC0_CONTRACT_ADDRESS`, `MOCA_TOKEN_ADDRESS`, `MOCA_MIN_STAKE_AMOUNT`.

---

## 5. Env Var Surface

One simple global env var per limit. No tier prefixes, no per-key attribution. To deploy tier X, set these to tier X's numbers.

```bash
# =============================================================================
# Global instance limits — sentinel 0 means "unlimited"
# =============================================================================
MAX_FILES=0                       # implemented (upload + custom-input gate)
MAX_ENTITIES=0                    # implemented (upload-time gate)
MAX_QUERIES_PER_MONTH=0           # implemented (FastAPI dependency, summed across all keys)
MAX_COLLECTIONS=0                 # implemented (POST /api/collections gate)
MAX_RPM=0                         # new — rate-limit middleware (instance-wide)

# =============================================================================
# Feature toggles
# =============================================================================
# Comma-separated whitelist of skill IDs active on this instance.
# Empty = all installed skills active. Use to gate premium apps per tier.
ENABLED_SKILL_IDS=
# Expose the self-service usage dashboard (`GET /api/me/usage`).
ENABLE_SELF_SERVICE_ANALYTICS=false

# =============================================================================
# Advanced security (each independent; ship as product decides priorities)
# =============================================================================
ENABLE_IP_ALLOWLIST=false
IP_ALLOWLIST=                     # comma-separated CIDRs when ENABLE_IP_ALLOWLIST=true
AUDIT_LOG_RETENTION_DAYS=0        # 0 = audit logging disabled
PROMPT_SECURITY_LEVEL=basic       # off | basic | strict (supersedes PROMPT_SECURITY)
API_KEY_ROTATION_RECOMMEND_DAYS=0 # 0 = no rotation hint surfaced

# =============================================================================
# MOCA Flatrate eligibility (separate design; listed for completeness)
# =============================================================================
# MOCA_RPC_URL=
# DECC0_CONTRACT_ADDRESS=
# MOCA_TOKEN_ADDRESS=
# MOCA_MIN_STAKE_AMOUNT=
```

### Recommended values per tier

Paste these into the instance env config at deployment time.

| Env Var | Free | Individual | Enthusiast | Business | Enterprise |
|---|---|---|---|---|---|
| `MAX_FILES` | `20` | `500` | `3000` | `25000` | `0` |
| `MAX_ENTITIES` | `500` | `10000` | `100000` | `1000000` | `0` |
| `MAX_QUERIES_PER_MONTH` | `1000` | `10000` | `100000` | `0` | `0` |
| `MAX_COLLECTIONS` | `1` | `10` | `100` | `0` | `0` |
| `MAX_RPM` | `30` | `120` | `300` | `1200` | `0` |
| `ENABLED_SKILL_IDS` | *(free only)* | *(free only)* | *(free + select premium)* | *(empty = all)* | *(empty = all)* |
| `ENABLE_SELF_SERVICE_ANALYTICS` | `false` | `false` | `true` | `true` | `true` |
| `ENABLE_IP_ALLOWLIST` | `false` | `false` | `false` | `true` | `true` |
| `AUDIT_LOG_RETENTION_DAYS` | `0` | `0` | `0` | `365` | `0` (= unlimited) |
| `PROMPT_SECURITY_LEVEL` | `basic` | `basic` | `basic` | `strict` | `strict` |
| `API_KEY_ROTATION_RECOMMEND_DAYS` | `0` | `0` | `0` | `90` | `90` |

MOCA Flatrate instances get the Enthusiast column.

**Existing env vars that stay**: `MAX_FILE_SIZE_MB`, `ENABLE_GRAPH_EXTRACTION`, `ENABLE_AGENT_CHAT`, `ENABLE_AGENT_RESEARCH`, `ENABLE_SKILLS`, `MAX_SKILL_TOOLS`. `PROMPT_SECURITY` (boolean) stays for backward compatibility and is superseded by `PROMPT_SECURITY_LEVEL` once both are wired — then removed.

---

## 6. Implementation Roadmap

Phased so each phase is independently shippable and backward-compatible.

### Phase 1 — Declare env vars, wire the already-declared-but-unused ones ✅ (partially shipped)
- ✅ `MAX_ENTITIES` and `MAX_QUERIES_PER_MONTH` added to [backend/app/config.py](backend/app/config.py) and [.env.example](.env.example).
- ✅ `MAX_COLLECTIONS` enforcement wired into `POST /api/collections`.
- 🟡 Remaining: add the still-new vars (`MAX_RPM`, `ENABLED_SKILL_IDS`, `ENABLE_SELF_SERVICE_ANALYTICS`, `ENABLE_IP_ALLOWLIST`, `IP_ALLOWLIST`, `AUDIT_LOG_RETENTION_DAYS`, `PROMPT_SECURITY_LEVEL`, `API_KEY_ROTATION_RECOMMEND_DAYS`) to both `config.py` and `.env.example`.

### Phase 2 — Queries & RPM middleware (queries shipped, RPM pending)
- ✅ `enforce_query_quota` FastAPI dependency wired onto `/api/search`, `/api/ask`, `/api/ask/stream`, `/api/ask/stream/thinking`; sums monthly instance-wide query count (reuses `APIKeyUsageLog` aggregation) and rejects with 429 + `Retry-After` when `MAX_QUERIES_PER_MONTH` is exceeded.
- 🟡 Remaining: add a global rate-limit middleware (in-process token bucket; Redis later if needed) that enforces `MAX_RPM` across the instance.

### Phase 3 — Entity cap ✅ (upload-gate variant shipped)
- ✅ Upload-time check counts current global entities via `get_stats()` and rejects new ingestion (file uploads + custom inputs) when the cap is reached. See §4.2 for the design tradeoff vs. mid-extraction enforcement.
- 🟡 Remaining: surface "entities remaining" to admins.

### Phase 4 — Feature gating (skills + self-service analytics)
- Wire `ENABLED_SKILL_IDS` as a whitelist in `skill_service`.
- Add `GET /api/me/usage` self-service endpoint, guarded by `ENABLE_SELF_SERVICE_ANALYTICS`.

### Phase 5 — Advanced security (pick priorities per product decision)
- `ENABLE_IP_ALLOWLIST` middleware (straightforward FastAPI `Depends`).
- `AuditLog` node type + retention job driven by `AUDIT_LOG_RETENTION_DAYS`.
- `PROMPT_SECURITY_LEVEL` three-level implementation; migrate `PROMPT_SECURITY` → `PROMPT_SECURITY_LEVEL` with a deprecation period.
- `API_KEY_ROTATION_RECOMMEND_DAYS` UI reminder.

### Phase 6 — MOCA Flatrate (separate doc)
- Wallet-link flow, on-chain verification job, automated provisioning hook.

---

## 7. Known Non-Technical Items

Not part of the env-var surface:

- **Community / Email / Priority / Dedicated support** — handled by the support team.
- **Bespoke app development** (Enterprise) — one-off engineering engagements.
- **"Most Popular" badge** on Enthusiast — marketing copy, not a feature.

---

## 8. Operator Workflow

Every limit is a global env var on the instance. Edit env config, redeploy, done. No admin UI, no database migration.

### Scenario A — A customer upgrades from Individual to Enthusiast

The customer's instance is redeployed with the Enthusiast column from §5:

```
MAX_FILES=3000
MAX_ENTITIES=100000
MAX_QUERIES_PER_MONTH=100000
MAX_COLLECTIONS=100
MAX_RPM=300
ENABLED_SKILL_IDS=<free-skill-ids>,<select-premium-ids>
ENABLE_SELF_SERVICE_ANALYTICS=true
```

Redeploy. All limits immediately reflect the new tier.

### Scenario B — Change a single cap on an existing instance

Edit `MAX_FILES=30` in env config, redeploy. The Free-tier instance now accepts 30 files instead of 20.

### Scenario C — Provision a MOCA Flatrate customer

Same as provisioning an Enthusiast: apply the Enthusiast column to the customer's instance. When on-chain verification ships (§4.13), provisioning becomes automated by a scheduled job, but the env-var surface the job writes to is identical.

### Scenario D — Roll out a new premium skill to Enthusiast+ customers

Update `ENABLED_SKILL_IDS` on every Enthusiast+ instance to include the new skill ID and redeploy those instances.

### Scenario E — Harden security on a Business instance

Set `PROMPT_SECURITY_LEVEL=strict`, `ENABLE_IP_ALLOWLIST=true`, `IP_ALLOWLIST=<customer CIDRs>`, `AUDIT_LOG_RETENTION_DAYS=365`. Redeploy.

### What is *not* controlled by env var

Only application *content* — documents, collections, entities, chat history, skill installations. These are what the customer creates inside their instance; they are counted against the env-var caps but are not themselves configuration.

Every **limit**, every **feature toggle**, every **tier-defining number** lives in env vars.

---

## 9. Verification Checklist

On every change to tiers or backend limits:

1. Open [cortex.moca.qwellco.de/pricing](https://cortex.moca.qwellco.de/pricing) side-by-side with §2 — every number matches.
2. Every env var in §5 is either (a) already present in [backend/app/config.py](backend/app/config.py), or (b) tagged here as "new".
3. File references resolve to the lines claimed (re-check after refactors).
4. When a phase from §6 ships, flip the corresponding 🟡/🔴 rows in §3 to ✅ and update [.env.example](.env.example) + [.claude/environment.md](.claude/environment.md).

Related docs to update alongside PRICING.md:
- [.env.example](.env.example) — keep in sync with §5.
- [.claude/environment.md](.claude/environment.md) — add new vars under a "Pricing Limits" section.
- [.claude/domain/admin-features.md](.claude/domain/admin-features.md) — document audit logs + IP allowlist when those ship.
- [.claude/domain/skills.md](.claude/domain/skills.md) — document the `ENABLED_SKILL_IDS` whitelist once wired.
