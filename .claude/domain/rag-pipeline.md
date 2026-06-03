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

## Writer

Synthesizes all gathered context from the researcher into a streamed answer.

- `WRITER_MAX_TOKENS_SPEED` (default: 1200)
- `WRITER_MAX_TOKENS_QUALITY` (default: 4000)
- Streaming responses via `/api/ask/stream` and `/api/ask/stream/thinking` endpoints

## Legacy Fallback

Fixed pipeline available as fallback via `ENABLE_AGENT_RESEARCH=false`. Also `ENABLE_AGENT_CHAT=false` to disable chat agent.

## Frontend

See [`.claude/frontend-patterns.md`](../frontend-patterns.md#chatresearch-message-rendering) for chat/research message rendering patterns.
