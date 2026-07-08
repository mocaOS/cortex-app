# Observability (Langfuse + GlitchTip)

LLM observability for the backend: every LLM / embedding / vision call is traced
and costed in [Langfuse](https://langfuse.com/docs), and agentic Q&A flows are
grouped into one trace per request. **Fully env-driven** — when the credentials
are absent the same image runs identically with zero tracing overhead. Designed
for multi-tenant deployments where each tenant `cortex-app` points at one
Langfuse project (per-project cost + isolation; the org rolls everything up).

Crash/error reporting is a separate, equally env-driven concern — GlitchTip via
the Sentry protocol, covering backend *and* frontend with readable stack traces.
See [Error tracking (GlitchTip)](#error-tracking-glitchtip) at the bottom.

## Activation

Tracing is active iff `Settings.langfuse_tracing_active` — all of
`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` set **and**
`LANGFUSE_TRACING_ENABLED` (default true). See [`environment.md`](../environment.md#observability-langfuse)
for the vars. The modern Langfuse Python SDK reads `LANGFUSE_BASE_URL` natively
(not `LANGFUSE_HOST`), but we construct the client explicitly from settings so
`.env`-loaded values work even when they never reach `os.environ`.

**Environment segmentation.** `init_langfuse()` passes `environment=` to the
`Langfuse(...)` constructor, resolved as `LANGFUSE_TRACING_ENVIRONMENT or
ENVIRONMENT`. In multi-tenant deployments the control plane injects
`LANGFUSE_TRACING_ENVIRONMENT=<tenant-slug>` so every tenant's traces share one
Langfuse project but are filterable by environment; single-tenant/self-host
deployments leave it unset and fall back to the deployment `ENVIRONMENT`
(`production`/`development`). Passing it explicitly is required — the SDK only
auto-reads `LANGFUSE_TRACING_ENVIRONMENT` from `os.environ`, which the explicit
construction above otherwise bypasses. The value must be lowercase alphanumeric
with hyphens/underscores and not start with `langfuse` (the SDK warns + ignores
invalid values).

SDK: `langfuse>=3.0.0,<4.0.0` (`requirements-base.txt`) — ships the OpenAI
drop-in + `@observe`; no extra OTel collector needed. Pinned to the v3 line to
match the self-hosted server major (`langfuse:3`).

## Components (`services/observability.py`)

| Function | Role |
|---|---|
| `init_langfuse()` | Builds the global `Langfuse(...)` singleton from settings. Called once in the FastAPI lifespan **startup** (`main.py`), before any traced call, so the openai drop-in + `@observe`/spans pick it up. No-op + log when inactive. |
| `shutdown_langfuse()` | `flush()` + `shutdown()` on lifespan **teardown** (the lifespan has `stop_grace_period: 35s` to drain). |
| `get_langfuse()` | The singleton, or `None` when inactive. |
| `observed_trace(name, *, user_id, session_id, tags, metadata)` | Context manager opening a root span so nested generations group into one trace; stamps trace attributes via `update_current_trace`. No-op (yields None) when inactive. |
| `traced_sse(agen, name, ...)` | Wraps an async SSE generator in `observed_trace` without re-indenting the generator body. Returns `agen` unchanged when inactive. |
| `record_generation(*, name, model, usage, input, output, metadata)` | Records a one-shot generation for calls the openai drop-in can't see (Haystack embedders, raw-httpx vision). Maps OpenAI-style `usage` → Langfuse `usage_details`. |
| `provider_from_base_url(url)` | `venice` / `openrouter` / `openai` / host — for provider tags. |

## How calls get traced

**1. The OpenAI client factory (the core).** Every backend OpenAI client is built
through `llm_config.make_openai_client` / `make_async_openai_client`. When tracing
is active they return `langfuse.openai`'s drop-in (same API, base_url-agnostic →
Venice + OpenRouter work unchanged), else the plain client. This is the single
on/off decision point — ~19 call sites across `graph_extractor`,
`researcher_agent`, `context_curator`, `document_processor`, `skill_service`,
`main.py`. All chat completions funnel through `reasoning_config.safe_chat_completion(create_fn=client.chat.completions.create, ...)`,
so the wrapped client's method is auto-traced with **no wrapper changes**.

> **Global instrumentation:** `init_langfuse()` imports `langfuse.openai` at
> startup, which patches the openai SDK **process-wide**. So *any* openai-SDK
> call is auto-traced — including libraries that build their own client
> (Haystack's embedders, see below). The factory's per-call import is then just
> belt-and-suspenders; the eager startup import guarantees the patch is active
> before the first embedding/LLM call regardless of order.

**2. Streaming usage.** OpenAI-compatible streams omit token usage unless
`stream_options.include_usage` is set. `llm_config.stream_usage_kwargs()` adds it
**only when traced** (gated, so untraced behavior and finicky gateways are
untouched). Applied to the streamed `create` calls in the researcher writer,
`/api/ask/stream*`, and the legacy synthesis path.

**3. Agentic grouping.** The four ask SSE generators in `main.py`
(`generate_agentic` / `generate_fast` / `generate` (standard) / `generate`
(thinking)) are wrapped with `traced_sse(...)` at the `StreamingResponse` site,
tagged `endpoint:* / mode:*` and `user_id=auth.key_id`. Every nested generation
(researcher iterations, writer) attaches to that one trace because it runs in the
same task while the span is the current context.

**4. Haystack embeddings — auto-traced (no manual record).** The embedders
(`document_processor.py` `embed_query` / `embed_queries` / ingestion
`self.embedder.run`) use the openai SDK internally, so the global patch from
step 1 traces them automatically as `OpenAI-embedding` generations with model +
usage + cost. **Do not** add a manual `record_generation` for these — it
double-counts (one manual + one auto for the same call). This was tried and
removed; see git history.

**5. Vision — manual `record_generation` (a genuine non-SDK path).**
`vision_analyzer.py` makes a raw `httpx` POST to `/chat/completions`, bypassing
the openai SDK, so the global patch can't see it. The 200-path records model +
`usage` + prompt/output via `record_generation` (`name="vision.analyze"`).

**6. Prompt Guard — manual `record_generation` (not an LLM at all).** The
query-time prompt-guard gate (`prompt_guard_client.guard_user_question`) POSTs to
cortex-helper's `/classify` (a deberta-v3 classifier, not the openai SDK), so it
records `name="prompt_guard.classify"`, `model=prompt_guard_model`,
`usage=None`, `metadata={"stage": "prompt_guard"}` (the `stage` key is in the
`_KEEP_METADATA_KEYS` allow-list). It nests under the ask trace opened by
`traced_sse`/`observed_trace`. It also meters one `query` unit via
`usage_meter.record_completion` (the classify call bypasses the factory's quota
metering, same as vision). These two (vision + prompt-guard) are the manual
`record_generation` paths.

**6. OpenRouter usage accounting — gateway-reported USD cost.** For OpenRouter
clients, `llm_config._instrument_completions` deep-merges
`extra_body={"usage": {"include": true}}` into every `create` call (gated on
tracing active + `openrouter.ai` base_url; merged per-call so a caller's own
`extra_body` reasoning params are preserved). OpenRouter then returns the actual
cost of the invocation in `usage.cost`, which the langfuse.openai drop-in reads
natively (`_parse_cost`) and records as the generation's `cost_details.total`.
This is the **authoritative** cost for OpenRouter and the **only correct source
for `:nitro`** — nitro routes each request to the fastest provider, so the
effective per-token price varies call-to-call and a static price catalog would
misreport it. Streaming still needs `stream_options.include_usage` (already
added by `stream_usage_kwargs()` when traced) so the final chunk carries usage +
cost. Zero behavior change untraced / non-OpenRouter (`extra_body` untouched).

## Content masking (`LANGFUSE_LOG_EXTENDED`)

By **default** (`LANGFUSE_LOG_EXTENDED=false`) Cortex redacts **all** user- and
model-authored text **client-side, before export** — raw content never hits the
network. This is both a privacy measure (we don't store what users prompt) and a
storage one (prompt/completion text dominates ClickHouse cost). Set
`LANGFUSE_LOG_EXTENDED=true` to log full content for local debugging.

**Wiring.** `init_langfuse()` passes `mask=_mask_content` to the `Langfuse(...)`
constructor when masking is on (`None` when `LANGFUSE_LOG_EXTENDED=true`). The
SDK's **legacy `mask` hook** (v3 line — `mask_otel_spans` does **not** exist in
v3 and isn't used) runs once per field (`input` / `output` / `metadata`). Because
the `langfuse.openai` drop-in routes `generation.update(input=…, output=…)`
through this hook, **one `mask=` arg covers every call site** — all chat
completions, embeddings, the vision `record_generation`, and `observed_trace`
metadata. No per-call-site edits.

**The hook is not told which field it's masking**, so classification is purely
**structural** (object shape + keys + message `role`). It receives the real
Python object (dict/list/str), not stringified JSON. `_mask_content` is **total**
— on any internal error or ambiguity it returns `"[REDACTED]"` (fail closed) and
never raises. (The SDK also fails closed if a mask hook raises, replacing the
whole field; we keep structure instead.)

**Policy (deny-by-default).**

- **KEEP (structural):** message `role`/`name`/`tool_call_id`/`finish_reason`/`type`;
  `model` + params (temperature, etc.); tool **calls** — function `name` + argument
  **keys** (not values); tool/function **definitions** — function `name` + parameter
  property **keys** (descriptions redacted); metadata keys `stage`/`endpoint`/`mode`/`provider`;
  all numeric/bool values (tokens, cost, latency).
- **REDACT → `"[REDACTED]"`:** every message `content` (system/user/assistant/tool);
  tool-call argument **values**; tool/function description strings; embedding inputs
  (`str` / `list[str]`); vision prompt + output; graph-extraction document/chunk text
  and XML/JSON output; any unclassifiable string leaf.

Implemented in `observability.py` (`_mask_content` + `_mask*` helpers); covered by
pure-function tests in `tests/test_langfuse.py` (no network), including a planted
`SECRET_*` leak check across messages, tool args, tool defs, and metadata.

**Surfaced in the admin UI.** `GET /api/admin/config` returns
`langfuse_tracing_active` + `langfuse_log_extended`, rendered in a **Privacy**
section on the `/admin` System Config panel (`frontend/src/app/admin/page.tsx`):
"Prompt & Content Redaction" (Enabled = `!langfuse_log_extended`) and "LLM Tracing
(Langfuse)". This lets an operator (or a customer auditing a hosted instance)
verify at a glance that prompt/completion content is redacted before export. The
section is always visible — it ignores the `DISPLAY_FULL_SYSTEM_CONFIG` advanced
gate.

## What you get

- **Cost by model / endpoint / provider** — generations carry the model + token
  usage; traces carry `endpoint:*`, `mode:*` tags. Accurate USD requires model
  price definitions in the Langfuse project (Venice/OpenRouter aren't in
  Langfuse's built-in catalog) — see Cost catalog below.
- **Agentic debugging** — open a trace → researcher iterations, retrieval, and
  each generation (prompt/completion/tokens/cost), grouped per request.
- **Per-tenant isolation** — one project per tenant; org dashboards aggregate.

## Cost catalog (USD pricing)

Langfuse prices a generation two ways, in priority order: (1) an explicit
`cost_details` on the generation — this is what **OpenRouter usage accounting**
supplies (see "How calls get traced" §6), so OpenRouter cost is exact and needs
no catalog entry; (2) otherwise, a **regex match of the recorded model name** to
a seeded price definition. Venice/direct-OpenRouter models aren't in Langfuse's
built-in catalog, so without seeding (and without usage accounting) cost shows
`$0` (token usage is still tracked).

- **`backend/scripts/langfuse-models.json`** — versioned price catalog, single
  source of truth. Prices are **USD per 1M tokens** (as on the provider's pricing
  page); embeddings use `output_per_1m: 0`. Edit here when prices change. An
  entry may set `match_pattern` to override the default exact-name regex (used
  by the OpenRouter gemma entry to tolerate the `:nitro` variant suffix). The
  OpenRouter gemma entry is a **fallback only** — usage accounting is the primary
  cost source; the static price is a floor-price safety net if `usage.cost` is
  ever absent, and is approximate for `:nitro` by nature.
- **`backend/scripts/seed_langfuse_models.py`** — idempotent seeder. POSTs each
  entry to a project's `POST /api/public/models` (Basic auth with the project
  key), converting per-1M → Langfuse's per-token `inputPrice`/`outputPrice`.
  Skips models already priced identically; reusable against **any** project
  (point the keys at a tenant project to backfill it — meta-cortex uses this).

```bash
# Seed the project named in .env (dry-run first):
cd backend && python scripts/seed_langfuse_models.py --env-file ../.env --dry-run
python scripts/seed_langfuse_models.py --env-file ../.env
# Seed an arbitrary tenant project:
python scripts/seed_langfuse_models.py --base-url https://lf... --public-key pk-lf-... --secret-key sk-lf-...
```

Pricing applies to generations recorded **after** seeding. When you add/change a
deployment's model (`OPENAI_MODEL`, `GRAPH_EXTRACTION_MODEL`, `VISION_MODEL`,
`EMBEDDING_MODEL`), add it to the catalog and re-run the seeder.

## Not covered / future

- **Neo4j retrieval tools** (`knowledge_search`, `entity_lookup`,
  `community_search`) are not yet emitted as explicit spans — they execute inside
  the grouped trace but aren't broken out. Add `start_as_current_span` around each
  tool to surface them.
- **Multi-turn `session_id`** — `RAGRequest` has no conversation id, so traces
  carry `user_id` but not `session_id`. Add a client-supplied conversation id to
  enable session grouping.
- **Per-tenant auto-provisioning** (create project + keys, inject `LANGFUSE_*`,
  seed the price catalog) lives in the **meta-cortex** control-plane repo, not here.

## Verifying

Boot with the `LANGFUSE_*` vars set; the startup log prints `Langfuse tracing
ACTIVE → <url>`. Run a `/api/ask/stream` query and an ingest; traces appear in the
project with generations carrying tokens. The untraced no-op path (no keys → plain
client, all helpers inert) is covered by design and should stay that way.

## Error tracking (GlitchTip)

Separate concern from Langfuse (LLM traces/cost): GlitchTip captures **crashes
and error logs** app-wide. It speaks the Sentry protocol, so both sides use
stock Sentry SDKs; env vars are `SENTRY_*` (see
[`environment.md`](../environment.md#error-tracking-glitchtip)). Backend and
frontend report to **separate GlitchTip projects** (distinct DSNs); compose
files map operator-facing `SENTRY_DSN_BACKEND`/`SENTRY_DSN_FRONTEND` vars onto
the standard names each container reads.

**Backend** — `services/error_tracking.py` owns the lifecycle, mirroring
`observability.py`: `init_sentry(service=...)` is a no-op without `SENTRY_DSN`,
reads Settings (`.env`-aware) with a raw-env fallback, and runs in `main.py`
**before** `app = FastAPI(...)` so the SDK's Starlette/FastAPI integrations
hook app construction — they capture unhandled exceptions *before* the
sanitizing 5xx handlers run, so tracking and client-facing sanitization
coexist. The logging integration (SDK default) turns any `logger.error/
exception` — background pipeline, flush loops, the docling worker (which calls
`init_sentry(service="docling-worker")` in its `main()`) — into events.
A `before_send` hook stamps the `request_id` tag from the logging contextvar
(inherited by request-spawned tasks), so issues correlate 1:1 with log lines
and `X-Request-ID` response headers. Source context comes free: the Python SDK
reads the lines around each frame from the container's own files. Privacy
mirrors the Langfuse mask: request bodies `never` by default, PII off.

**Frontend** — `@sentry/nextjs`: `src/instrumentation-client.ts` (browser),
`sentry.server.config.ts` + `src/instrumentation.ts` (`register()` +
`onRequestError = captureRequestError` for Server Components/proxy),
`src/app/global-error.tsx` (root-layout crashes). `next.config.mjs` wraps the
config in `withSentryConfig`, which — only when `SENTRY_AUTH_TOKEN` is set at
build time — generates + uploads source maps as **debug-ID artifact bundles**
(GlitchTip ≥ 4.2) and then deletes the `.map` files so the runtime image never
serves them. Verified working against a Turbopack `next build` (Next 16).
Upload config (`SENTRY_URL/ORG/PROJECT/AUTH_TOKEN`) enters `Dockerfile.prod`
as build args and stays out of the runner stage.

**Verifying**: set `SENTRY_DSN`, boot, and check the startup log for `Error
tracking active (service=backend, ...)`; a forced exception appears in the
GlitchTip project within seconds with context lines around the crash site. For
the frontend, `npm run build` with the four upload vars set must log
`Successfully uploaded source maps to Sentry`.
