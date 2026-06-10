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
- `RESEARCHER_MAX_ITERATIONS_SPEED` (default: 5)
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

## Conversation Memory / Context Curator

`backend/app/services/context_curator.py` replaces raw history truncation with a bounded, client-carried memory model. The backend stays stateless: the client sends an opaque `conversation_memory` blob on `RAGRequest`, and the pipeline returns an updated one via a `memory_update` SSE event (passed straight through `run_research_pipeline` → `agent_rag_stream` → SSE). **Fully backward-compatible** — no blob (`None`) ⇒ legacy `conversation_history[-max_conversation_history:]`, no `memory_update` emitted.

- `build_context(history, memory, settings)` is called once in `run_research_pipeline` and the curated list feeds **both** the researcher loop (`researcher_agent.py:~406`, slice removed) and the writer (`:~1335`) — replacing the seven legacy truncation sites on the agentic path.
- `compact_memory(...)` runs **after** the answer streams (zero added latency) using the fast model (`get_llm_config(fast_mode=True)`, override via `CONVERSATION_MEMORY_COMPACTION_MODEL`); degrades gracefully (prior blob kept, no message lost).
- **Phase 1 (shipped):** `transcript` bucket — last N msgs verbatim + a rolling `summary` of older ones. `summarized_count` indexes the client's canonical full history (client must send full history when using memory). Settings: `ENABLE_CONVERSATION_MEMORY`, `CONVERSATION_MEMORY_WINDOW` (default 6), `CONVERSATION_MEMORY_MAX_TOKENS`, `CONVERSATION_MEMORY_COMPACTION_MODEL`.
- **Phase 2 (shipped):** `source_ledger` bucket — citation continuity. Every emitted source carries a conversation-stable `sid` (`source_sid()`: hash of `chunk_id`, else filename+content prefix) on the `sources` SSE event (additive; existing frontends ignore it). `compact_memory(..., sources=)` accumulates `{sid, filename, gist, score}` into `source_ledger`, deduped by `sid`, capped at `CONVERSATION_MEMORY_MAX_LEDGER` (default 50). Per-turn `[src_N]` writer numbering is unchanged — `sid` lives only on the wire/ledger, not in the writer prompt.
- **Phase 3 (shipped):** semantic buckets — `facts[]`, `open_questions[]`, `intent`. `compact_memory` does **one** structured fast-LLM call (`_update_buckets`) that folds aged-out messages into `summary` **and** (re)derives facts/open_questions/intent from the latest exchange. Reasoning is forced **OFF** on this utility call (`_utility_kwargs` → `build_reasoning_kwargs(..., ReasoningMode.OFF)`) so a GPT-5/o-series model doesn't spend the completion budget reasoning. `build_context` injects all buckets as one `[Conversation memory]` message (`render_memory_block`).
- **Phase 4 (shipped):** fast-path + KG rehydration. `is_memory_answerable()` (cheap classifier, reasoning OFF, gated by `ENABLE_MEMORY_FAST_PATH`) decides if a follow-up ("summarize that", "why?", "in German") is answerable from memory; if so `run_research_pipeline` **skips the researcher loop** (`_empty_aiter()`) and seeds `research_result.graph_context`/`.communities` from the blob's `kg_context` via `rehydrate_graph_context()` — no retrieval, no Neo4j. `compact_memory(..., kg_context=)` stores a capped entities/communities snapshot each turn. The ledger digest in the curated context lets the writer reuse prior sources.
- Blob shape (v3): `{version, transcript:{summary, summarized_count}, facts:[], open_questions:[], intent:str, source_ledger:[{sid,filename,gist,score}], kg_context:{entities,communities}}`. See [`environment.md`](../environment.md).

## Legacy Fallback

Fixed pipeline available as fallback via `ENABLE_AGENT_RESEARCH=false`. Also `ENABLE_AGENT_CHAT=false` to disable chat agent.

## Frontend

See [`.claude/frontend-patterns.md`](../frontend-patterns.md#chatresearch-message-rendering) for chat/research message rendering patterns.
