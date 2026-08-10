# Design: consumer identity (`appuser:` principals)

Status: **design for review — no code.** This is the last strategic-tier item and
the only one flagged as needing a design pass before implementation: it modifies
the auth hot path everything else depends on.

## Problem

Cortex has exactly one consumer-facing identity primitive: the API key, with two
permissions and optional collection scoping. Everything finer-grained is emulated:

- Multi-agent setups mint one key per agent and roll their own routing
  (`cortex.sh sources.json`; the handbook literally suggests
  `collection_id = f"agent-{agent_id}"`).
- cortex-chat serves many humans through one per-group key — the backend cannot
  distinguish them; per-user analytics, quotas, or personalization are impossible.
- Apps have a **reserved but unimplemented** `appuser:` principal
  (`app_service.py:610`, roadmap hook in `.claude/domain/apps.md`): app tokens can
  name an end-user, but nothing resolves that name into rights or state.
- Server-side sessions (shipped 2026-08-10) are keyed per API key — a group key's
  sessions are one undifferentiated pool.

## Proposal (summary)

Introduce a lightweight `Consumer` node attached to an API key:

```
(k:APIKey)-[:HAS_CONSUMER]->(c:Consumer {id, external_id, display_name,
                                          default_collection_id?, created_at,
                                          last_seen_at})
```

- **Identified per request, never authenticated separately.** The key remains the
  credential; a request may carry `X-Cortex-Consumer: <external_id>` (or
  `consumer` in app-token claims). The consumer is *attribution and defaults*,
  not a second auth factor — a caller that holds the key can claim any consumer
  on it, which is exactly the trust model integrators (cortex-chat, apps,
  multi-user harnesses) already operate under: they authenticate their own users
  and are trusted to label traffic truthfully.
- **Auto-provisioning:** first sight of a new `external_id` on a key creates the
  Consumer (capped per key, `CONSUMER_MAX_PER_KEY`, default 10k). No CRUD
  ceremony for the common path; admin endpoints exist for listing/deleting.
- **What a consumer gives you (v1):**
  1. **Session partitioning** — `ApiSession` gains `consumer_id`; listing/access
     scoped to (key, consumer). Fixes the group-key session pool immediately.
  2. **Usage attribution** — `APIUsage` rows gain `consumer_id`; per-user
     analytics in the admin UI ("which teammates actually use this").
  3. **Default write collection** — optional per-consumer
     `default_collection_id` applied when an upload names none. This is the
     dangerous one (see risks) and ships **disabled** unless explicitly set.
- **Explicit non-goals (v1):** per-consumer permissions (the key's permissions
  are the ceiling — consumers never escalate), per-consumer rate limits (later),
  cross-key consumer identity (a consumer exists under exactly one key).

## Why this shape

- **Additive on the hot path.** `validate_api_key` is untouched; consumer
  resolution happens after auth succeeds, is cache-friendly (same 30s TTL
  keyed by (key_id, external_id)), and a request without the header behaves
  byte-identically to today. No existing client changes behavior.
- **It matches how the ecosystem already works.** cortex-chat, apps with
  `appuser:` tokens, and Hermes multi-agent setups all have a user/agent name
  in hand at request time; a header is the integration cost they can pay
  trivially. The alternative (per-user API keys) explodes key management and
  breaks the collection-scoping model.
- **`appuser:` converges instead of forking.** App tokens already carry a
  principal slot; app-proxied requests resolve it to the same Consumer node —
  one identity concept across REST, apps, and MCP (remote MCP forwards the
  header through its ASGI self-calls unchanged).

## Risks & mitigations (the reason for this doc)

1. **Auth-path regression risk.** Mitigation: resolution strictly post-auth,
   fail-open (resolution error → request proceeds unattributed, logged), its own
   cache with the same invalidation discipline as the key cache, and the
   no-header path short-circuits before any lookup.
2. **The default-collection footgun.** We have live precedent (cortex-chat
   uploads silently landing in `default` for months): changing where writes land
   is the change that surprises people. Mitigations: `default_collection_id` is
   never auto-set, must be within the key's scope at write time (validated per
   request, not at set time), and an explicit `collection_id` on the request
   always wins.
3. **Cardinality.** A public-facing app could mint unbounded consumers.
   Mitigations: per-key cap with a clean 400 past it, consumers are TTL-swept
   when idle AND stateless (no sessions, no usage rows in retention window),
   and `last_seen_at` rides the existing usage-flush batching — no extra write
   per request.
4. **Privacy surface.** `external_id`/`display_name` are operator-chosen labels
   (pseudonymous IDs recommended, documented); consumers are instance-operational
   (excluded from library export), deleted with their key, and admin-deletable
   individually (the retention story mirrors sessions).
5. **Analytics migration.** `APIUsage` gains a nullable field — no backfill;
   pre-identity rows just have no consumer. Admin UI treats null as "(key-level)".

## Touch points (implementation map, for the eventual PR)

| Area | Change |
|---|---|
| `models.py` | Consumer shapes; nothing on RAGRequest (header, not body) |
| `auth_service.py` | `resolve_consumer(auth, request)` post-validation helper + cache |
| `neo4j_service.py` | Consumer CRUD + (key, external_id) unique constraint + idle sweep |
| `main.py` | header plumbing on ask/upload/sessions; admin list/delete endpoints |
| sessions | `consumer_id` on ApiSession create/list/get filters |
| `api_usage_service.py` | nullable consumer_id on usage rows + per-consumer aggregation |
| `app_service.py` | `appuser:` claim → Consumer resolution (closes the roadmap hook) |
| SDK | `consumer` option on the client constructor (one header) |
| Docs | admin-features + apps + rag-pipeline domain docs; environment.md; skills |

## Open questions for Rene

1. Header name: `X-Cortex-Consumer` vs folding into the key itself — header
   recommended (key rotation stays orthogonal to identity).
2. Should sessions *require* a consumer when the key has any consumers? (Strict
   partitioning vs. permissive mixed pool — recommend permissive v1.)
3. Per-consumer monthly quota: worth it in v1 or later? (Recommend later —
   metering is instance-level today; per-consumer metering touches the
   usage-meter flush path.)
4. Does cortex-chat adopt in v1? It's the biggest beneficiary (per-employee
   attribution + session partitioning) and the best validation target — but that
   sequencing repeats the sessions question: ship backend first, migrate
   cortex-chat once soaked.
