# RAG Query Pipeline (Agent Architecture)

Two-stage researcher/writer pipeline for answering questions. See [`.claude/domain/skills.md`](skills.md) for skill-augmented capabilities.

## Researcher Agent

Uses OpenAI function-calling to iteratively gather information via tools:

### Tools
- `knowledge_search` — hybrid RRF: vector 0.5 + fulltext 0.3 + graph 0.2, with cross-encoder reranking. The async path (`graph_search_async`) wraps the sync embed + Neo4j Bolt calls in `asyncio.to_thread` so `asyncio.gather` of N parallel searches actually runs concurrently in the threadpool instead of serializing on the event loop. **Batched preprocessing** (`ENABLE_BATCHED_QUERY_EXTRACTION`, default on): `_execute_knowledge_search` does ONE query-entity-extraction LLM call + ONE embedding call for all of a search's (≤3) queries upfront, then passes each query its `precomputed_entities`/`precomputed_embedding` into `graph_search_async` (which skips its per-query extract/embed). Query-side entity extraction runs on the extraction tier (`GRAPH_EXTRACTION_MODEL` + minimized reasoning via `extract_entities_from_queries_async`), not the primary model. Falls back to the per-query path on failure or when the flag is off.
- `community_search` — search community summaries
- `entity_lookup` — find specific entities
- `reasoning` — available in quality mode always, or speed mode when skills are active
- `http_request` — built-in tool, auth injected server-side from skill configs (no headers param). See [`.claude/domain/skills.md`](skills.md#http-request-tool)
- `git_repo` — read/act on the connected git repo (read_file / propose_change / comment). Added only when a git connection exists (`has_git`), gated like `http_request`; writes always open a PR on a new branch and are rejected on read-only connections. See [`.claude/domain/git-integration.md`](git-integration.md#write-tool-git_repo-researcher-agent)
- `done` — signal completion

### Speed Mode (Chat)
- 5 iterations default (elevated from 2 when skills are active, gets `reasoning` tool)
- Tools: `knowledge_search` + `done` (+ `http_request`/`reasoning` when skills active)

### Quality Mode (Deep Research)
- Up to 10 iterations
- All tools with reasoning transparency

### Iteration Caps
- `RESEARCHER_MAX_ITERATIONS_SPEED` (default: 3)
- `RESEARCHER_MAX_ITERATIONS_QUALITY` (default: 8)

## Streaming feedback (status + heartbeat)

To remove the "is it stuck?" silent window before the first token, the SSE stream emits additive `{"status": {"stage", "message"}}` events at pipeline stages — `analyzing`/`searching`/`generating` on the agent path (`researcher_agent.py`), `searching`/`reranking`/`generating` on the legacy chat path (`main.py`) — gated by `stream_reasoning_steps`. Every `StreamingResponse` is wrapped with `with_sse_heartbeat()` (`main.py`), which injects `: ping` comment lines during ≥8 s silent windows (keep-alive; ignored by clients). Both are additive/backward-compatible. The frontend `ChatMessage` `ThinkingIndicator` consumes `status` (with a heuristic fallback). Partner integration: [`docs/cortex-chat-integration.md`](../../docs/cortex-chat-integration.md).

## Reranking (cross-encoder) — lifecycle & offload

`knowledge_search` reranks hybrid-RRF candidates through `QueryProcessor.rerank_results` → `self.reranker.predict(pairs)` (the single rerank choke point; `pairs = [(query, content), …]` → scores). The local CrossEncoder pulls torch + sentence-transformers (~780 MB beyond the ~650 MB haystack floor), so the lifecycle is tuned for per-instance footprint:

- **Lazy by default** — `RERANKER_PRELOAD=false`; the model loads on first reranked query, not at startup.
- **Cold-start hiding** — `prewarm_reranker()` (fired from `enforce_query_quota` at request entry) kicks off the ~7 s load in the background so it overlaps the query-analysis LLM + embedding + search that precede reranking.
- **Idle unload** — `RERANKER_IDLE_TTL_SECONDS` (default 1800; 0 = never) + `_reranker_idle_reaper` unload the model when idle, reclaiming ~1 GB; it reloads on next use.
- **Offload** — when `RERANKER_SERVICE_URL` is set, `rerank_results` POSTs to the shared `cortex-helper` service and no local model loads (`_rerank_remote`, with graceful fallback to original order on failure). See [`environment.md`](../environment.md#shared-model-services-cortex-helper).

## Writer

Synthesizes all gathered context from the researcher into a streamed answer.

- `WRITER_MAX_TOKENS_SPEED` (default: 1200)
- `WRITER_MAX_TOKENS_QUALITY` (default: 4000)
- Streaming responses via `/api/ask/stream` and `/api/ask/stream/thinking` endpoints
- **Failed actions** — when a skill `http_request` call fails (4xx/5xx/timeout), the failure is collected on `ResearchResult.failed_actions` and rendered into the writer user prompt as a `=== Failed Actions (MUST report to the user) ===` section (`get_writer_user_prompt`). The writer must tell the user the action did not succeed and why, instead of deflecting or implying success. See [`skills.md`](skills.md#failure-surfacing).
- **Citation invariant (`[src_N]` ↔ `sources`)** — the writer is told to cite `[src_N]`, but when retrieval returns nothing it tends to emit learned/hallucinated markers that point at nothing (surfacing as literal `[src_1]` text). Two guards in `run_research_pipeline`: (1) when `source_events` is empty, an explicit "do NOT use [src_N]" note is appended to the writer user prompt; (2) `_CitationStripper` filters the writer token stream and drops any `[src_N]` whose `N` is `< 1` or `> len(source_events)` (incl. the no-sources case), together with one optional leading space. It is **stream-safe** — buffers only across a potential partial marker (`[`, `[s`, `[src_1`…) and a trailing whitespace run, so a marker split across token boundaries is still caught and non-citation brackets (`[note]`, `array[0]`) survive untouched. The cleaned text (not raw tokens) feeds memory compaction, keeping the source ledger consistent with what the user saw. The researcher path emits exactly one `sources` frame (line ~1423), so there is no populated-then-empty clobber on this branch. Tested in `tests/test_researcher_helpers.py`.

## Conversation Memory / Context Curator

`backend/app/services/context_curator.py` replaces raw history truncation with a bounded, client-carried memory model. The backend stays stateless: the client sends an opaque `conversation_memory` blob on `RAGRequest`, and the pipeline returns an updated one via a `memory_update` SSE event (passed straight through `run_research_pipeline` → `agent_rag_stream` → SSE). **Fully backward-compatible** — no blob (`None`) ⇒ legacy `conversation_history[-max_conversation_history:]`, no `memory_update` emitted.

- `build_context(history, memory, settings)` is called once in `run_research_pipeline` and the curated list feeds **both** the researcher loop (`researcher_agent.py:~406`, slice removed) and the writer (`:~1335`) — replacing the seven legacy truncation sites on the agentic path.
- `compact_memory(...)` runs **after** the answer streams (zero added latency) using the fast model (`get_llm_config(fast_mode=True)`, override via `CONVERSATION_MEMORY_COMPACTION_MODEL`); degrades gracefully (prior blob kept, no message lost).
- **Phase 1 (shipped):** `transcript` bucket — last N msgs verbatim + a rolling `summary` of older ones. `summarized_count` indexes the client's canonical full history (client must send full history when using memory). Settings: `ENABLE_CONVERSATION_MEMORY`, `CONVERSATION_MEMORY_WINDOW` (default 6), `CONVERSATION_MEMORY_COMPACTION_MODEL`.
- **Phase 2 (shipped):** `source_ledger` bucket — citation continuity. Every emitted source carries a conversation-stable `sid` (`source_sid()`: hash of `chunk_id`, else filename+content prefix) on the `sources` SSE event (additive; existing frontends ignore it). `compact_memory(..., sources=)` accumulates `{sid, filename, gist, score}` into `source_ledger`, deduped by `sid`, capped at `CONVERSATION_MEMORY_MAX_LEDGER` (default 50). Per-turn `[src_N]` writer numbering is unchanged — `sid` lives only on the wire/ledger, not in the writer prompt.
- **Phase 3 (shipped):** semantic buckets — `facts[]`, `open_questions[]`, `intent`. `compact_memory` does **one** structured fast-LLM call (`_update_buckets`) that folds aged-out messages into `summary` **and** (re)derives facts/open_questions/intent from the latest exchange. Reasoning is forced **OFF** on this utility call (`_utility_kwargs` → `build_reasoning_kwargs(..., ReasoningMode.OFF)`) so a GPT-5/o-series model doesn't spend the completion budget reasoning. `build_context` injects all buckets as one `[Conversation memory]` message (`render_memory_block`).
- **Phase 4 (shipped):** fast-path + KG rehydration. `is_memory_answerable()` (cheap classifier, reasoning OFF, gated by `ENABLE_MEMORY_FAST_PATH`) decides if a follow-up ("summarize that", "why?", "in German") is answerable from memory; if so `run_research_pipeline` **skips the researcher loop** (`_empty_aiter()`) and seeds `research_result.graph_context`/`.communities` from the blob's `kg_context` via `rehydrate_graph_context()` — no retrieval, no Neo4j. `compact_memory(..., kg_context=)` stores a capped entities/communities snapshot each turn. The ledger digest in the curated context lets the writer reuse prior sources.
- Blob shape (v3): `{version, transcript:{summary, summarized_count}, facts:[], open_questions:[], intent:str, source_ledger:[{sid,filename,gist,score}], kg_context:{entities,communities}}`. See [`environment.md`](../environment.md).

## Legacy Fallback

Fixed pipeline available as fallback via `ENABLE_AGENT_RESEARCH=false`. Also `ENABLE_AGENT_CHAT=false` to disable chat agent.

**Event-loop invariant (don't regress):** every LLM call on a request path must be non-blocking. `document_processor.py` `rag_query` (the non-agentic `/api/ask` path) and `_agentic_rag_query` use the **synchronous** `OpenAI` client, so their `client.chat.completions.create(...)` calls are wrapped in `await asyncio.to_thread(...)` — a bare sync call pins the asyncio event loop for the whole ~15-20s generation, starving every other in-flight request's async work (Neo4j acquisition, etc.) and cascading into timeouts/`500`s under concurrency (watchdog logs `Event loop was blocked for …s` + a thread dump). The agentic researcher/writer path already uses `AsyncOpenAI` + `await`; embeddings, Neo4j, and query entity-extraction are already threaded/async. Use `AsyncOpenAI` or `asyncio.to_thread` for any new generation call — never a bare sync `.create()` in an `async def`.

## Frontend

See [`.claude/frontend-patterns.md`](../frontend-patterns.md#chatresearch-message-rendering) for chat/research message rendering patterns.

## Loop Efficiency (round-trip elimination — v2)

All default-on, individually env-gated (see [`environment.md`](../environment.md#agent-configuration)):

- **Speed early-write** (`RESEARCHER_SPEED_EARLY_WRITE`): after a speed-mode iteration whose `knowledge_search` produced sources — and no side-effecting tool (`http_request`/`git_repo`/skill tool) ran, whose researcher-only output the writer would lose — the loop breaks straight to the writer instead of asking the model "are you done?". That `done` call was a full non-streaming LLM round-trip whose `summary` the speed writer prompt never reads. Log line: `Speed early-write: N sources after iteration i`.
- **Parallel read-only tool calls** (`RESEARCHER_PARALLEL_TOOL_CALLS`): when one assistant message carries ≥2 read-only calls, they're precomputed with `asyncio.gather` (keyed by `tool_call.id`), then the per-call branches consume the results in original order — message ordering and per-`tool_call_id` replies unchanged. Side-effecting tools stay sequential. The quality prompt now explicitly invites multi-call responses.
- **Tool-emitted entity hints** (`RESEARCHER_TOOL_ENTITY_HINTS`): `knowledge_search` has an optional `entities` param; when present, `_execute_knowledge_search` skips the batched query-entity-extraction LLM call and feeds the hints to the graph leg as `precomputed_entities` (embedding still batched). Falls back to extraction when absent.
- **Search dedup** (`RESEARCHER_SEARCH_DEDUP`): per-run cache keyed on normalized sorted queries; an exact repeat returns the cached tool text with a "try a DIFFERENT angle" nudge — no retrieval, no re-accumulated sources.
- **`done` before memory compaction** (`EMIT_DONE_BEFORE_MEMORY`): the pipeline emits `{done, pending_memory: true}` right after the last answer token, *then* runs `compact_memory` (an LLM call, 1–4s) and emits `memory_update` before closing. Clients must read the stream to its end (the in-repo frontend does; it finalizes on `done` but keeps consuming). `false` restores memory_update→done for clients that stop at `done`.
- **Async client reuse** (`llm_config.make_async_openai_client`): clients are cached per (api_key, base_url, langfuse, kwargs) so the httpx pool (TCP+TLS) is reused across the researcher/writer/classifier/compaction calls and across turns, instead of 2–3 fresh handshakes per turn.
- **Memory fast-path is action-aware**: the `is_memory_answerable` classifier prompt explicitly rules out action requests (create/update/send/delete, API calls) so a skill action can never be silently swallowed by the answer-from-memory shortcut.

## v-Next Efficiency & Hardening

- **Stable researcher prompt** (`RESEARCHER_STABLE_PROMPT`, default true): the system prompt is built once per request from `get_researcher_prompt_static` (iteration-free, parameterized on `has_skills` — skill-less deployments get a prompt with no skills/http_request references at all) + skill blocks; the `Iteration i of N` counter rides as a trailing system note rebuilt per call. The message list stays append-only → provider prefix caches (OpenAI auto, vLLM `--enable-prefix-caching`, OpenRouter) hit from iteration 2 on. `false` restores the legacy per-iteration rebuild.
- **Anthropic cache_control** (`ENABLE_PROMPT_CACHE_CONTROL`): `reasoning_config.apply_cache_control` marks the first system message as an ephemeral cache breakpoint — only on OpenRouter + `anthropic/*` models; applied in `_prepare_call` (all three model tiers) and the researcher loop.
- **Skill state TTL cache**: `SkillService` caches `get_skill_catalog()` + `load_skill_for_activation()` for 60s (invalidated on every skill CRUD/config mutation) — removes the per-request Neo4j query + SKILL.md reads + secret decryption, and makes `<active_skills>` byte-identical across requests.
- **Budgets**: `RESEARCHER_WALL_CLOCK_SECONDS` (0=off) breaks the loop to the writer on expiry; `RERANK_TOP_K` (15) bounds rerank input; inbound `conversation_memory` blobs are clamped (`clamp_memory_blob`: ledger cap, ~64KB ceiling, never a 4xx); `is_memory_answerable` pre-gates the classifier LLM call when the blob has no summary/facts/intent.
- **Per-key rate limiting** (`RATE_LIMIT_QPM`, 0=off): token bucket on ask/upload endpoints, 429 + `Retry-After`.
