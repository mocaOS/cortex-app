# RAG Query Pipeline (Agent Architecture)

Two-stage researcher/writer pipeline for answering questions. See [`.claude/domain/skills.md`](skills.md) for skill-augmented capabilities.

## Researcher Agent

Uses OpenAI function-calling to iteratively gather information via tools:

### Tools
- `knowledge_search` — hybrid RRF: vector 0.5 + fulltext 0.3 + graph 0.2, with cross-encoder reranking. The async path (`graph_search_async`) wraps the sync embed + Neo4j Bolt calls in `asyncio.to_thread` so `asyncio.gather` of N parallel searches actually runs concurrently in the threadpool instead of serializing on the event loop.
- `community_search` — search community summaries
- `entity_lookup` — find specific entities
- `reasoning` — available in quality mode always, or speed mode when skills are active
- `http_request` — built-in tool, auth injected server-side from skill configs (no headers param). See [`.claude/domain/skills.md`](skills.md#http-request-tool)
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
