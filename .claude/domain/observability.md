# Observability (Langfuse)

LLM observability for the backend: every LLM / embedding / vision call is traced
and costed in [Langfuse](https://langfuse.com/docs), and agentic Q&A flows are
grouped into one trace per request. **Fully env-driven** — when the credentials
are absent the same image runs identically with zero tracing overhead. Designed
for multi-tenant deployments where each tenant `cortex-app` points at one
Langfuse project (per-project cost + isolation; the org rolls everything up).

## Activation

Tracing is active iff `Settings.langfuse_tracing_active` — all of
`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` set **and**
`LANGFUSE_TRACING_ENABLED` (default true). See [`environment.md`](../environment.md#observability-langfuse)
for the vars. The modern Langfuse Python SDK reads `LANGFUSE_BASE_URL` natively
(not `LANGFUSE_HOST`), but we construct the client explicitly from settings so
`.env`-loaded values work even when they never reach `os.environ`.

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

**4. Non-SDK calls** (manual `record_generation`):
- **Haystack embeddings** — `document_processor.py` `embed_query` / `embed_queries`
  and the ingestion chunk embed (`self.embedder.run`). Token usage is read from the
  Haystack result `meta.usage`.
- **Vision** — `vision_analyzer.py` makes a raw `httpx` POST to `/chat/completions`;
  the 200-path records model + `usage` + prompt/output.

## What you get

- **Cost by model / endpoint / provider** — generations carry the model + token
  usage; traces carry `endpoint:*`, `mode:*` tags. Accurate USD requires model
  price definitions in the Langfuse project (Models config / Models API) since
  Venice/OpenRouter aren't in Langfuse's built-in catalog.
- **Agentic debugging** — open a trace → researcher iterations, retrieval, and
  each generation (prompt/completion/tokens/cost), grouped per request.
- **Per-tenant isolation** — one project per tenant; org dashboards aggregate.

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
