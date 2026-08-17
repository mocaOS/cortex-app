# Chapter 10: Ask AI — Chat and Deep Research

The Library provides two AI-powered question-answering modes, both accessible through the web interface and API. Both use a researcher/writer agent architecture that iteratively gathers information before synthesizing an answer.

> **Retrieving knowledge from the Cortex? Start with streaming Deep Research.** Whenever the task is "ask the Cortex", "retrieve data from the Cortex", or "find something in the Cortex", the first call should be `POST /api/ask/stream` (SSE) with `use_agentic: true`. It runs the full agentic pipeline and heartbeats keep long runs alive. The non-streaming `POST /api/ask` serves quick single-shot chat answers only — it is bounded by a ~28s server deadline (`ASK_DEADLINE_SECONDS`, 504 on expiry) and rejects `use_agentic: true` with `400 agentic_requires_streaming`.

## Two Modes

| Feature | Chat (Speed) | Deep Research (Quality) |
|---------|-------------|----------------------|
| **Purpose** | Quick answers to straightforward questions | Thorough, multi-angle research for complex questions |
| **Max iterations** | 3 | 8 |
| **Tools available** | `knowledge_search`, `done` | `knowledge_search`, `community_search`, `entity_lookup`, `reasoning`, `done` |
| **Writer output** | Up to 1,200 tokens | Up to 4,000 tokens |
| **LLM calls** | 2-3 per query | 6-15+ per query |
| **Latency** | 3-8 seconds | 15-60 seconds |
| **Best for** | Factual lookups, follow-up questions | Comparisons, analysis, multi-topic questions |

### Why Chat Is Snappy

On the chat path, hidden model "thinking" is suppressed by default
(`DEFAULT_REASONING_MODE=off`). Reasoning-capable models otherwise stream
chain-of-thought in a side channel the user never sees — adding seconds before
the first answer token and, across the agent loop, sometimes exhausting the
budget into an empty answer. With it off, the first token arrives in well under
a second on a capable model, and the chat writer is tuned to lead with the
answer and stay concise. To restore provider-default thinking on chat (e.g. on
OpenAI models that disable parallel tool calls at low reasoning), set
`DEFAULT_REASONING_MODE=auto`. See Chapter 4 and Chapter 22.

Deep Research runs without hidden thinking too (`RESEARCH_REASONING_MODE=off`),
and loses nothing by it: that loop already reflects **out loud** — the steps you
see in the stream are a real `reasoning` tool call the loop forces after any
round that searched without pausing to think, and that reflection is what steers
the next round. Hidden chain-of-thought, by contrast, is thrown away between
rounds while still being charged against the same output budget as the answer.
Set `RESEARCH_REASONING_MODE=auto` if a particular model gets worse at calling
tools without it.

## How the Pipeline Works

### Phase 1: Researcher Agent

An LLM-driven agent iteratively gathers information using function-calling tools:

**Speed Mode (Chat):**
1. Agent receives the question + conversation history
2. Issues 1 `knowledge_search` call with up to 3 parallel queries (passing the entity names it spotted, so no separate extraction call is needed)
3. Hands off to the writer as soon as the search produced sources (*early write* — the final "research complete" confirmation call is skipped; see `RESEARCHER_SPEED_EARLY_WRITE`). When a skill or git action ran, the agent keeps its full loop instead.

**Quality Mode (Deep Research):**
1. Agent uses `reasoning` tool to plan a research strategy
2. Issues broad `knowledge_search` + `community_search` for overview
3. Follows up with targeted searches based on initial findings
4. Uses `entity_lookup` to explore key entities mentioned in results
5. Cross-references and fills gaps
6. Calls `done` with a comprehensive summary

Read-only searches the agent issues in a single turn run **concurrently**, and an identical repeated search is answered from a per-question cache with a nudge to try a different angle — both keep long research runs from wasting wall-clock (see the loop-efficiency flags in [Chapter 4](04-configuration.md#agent-research-pipeline)).

The reflect-between-rounds rhythm (step 3) is **enforced by the loop**, not left to the model's goodwill: a search round the model didn't reason about triggers a forced reflection step whose analysis steers the next round (`RESEARCHER_FORCE_REFLECTION`), and research ends early once consecutive rounds mostly re-surface already-seen sources (`RESEARCHER_NOVELTY_*`) or the time budget runs out (`RESEARCHER_WALL_CLOCK_SECONDS`, default 60s) — the writer then synthesizes from what was gathered, so an answer always arrives even when the LLM provider is slow. Models that reflect on their own never trigger the forced step.

### Research Tools

**`knowledge_search`** — Primary information gathering tool
- Input: 1-3 search queries (keywords and entity names, not full sentences)
- Executes all queries in parallel via hybrid RRF search
- Applies cross-encoder re-ranking against the original question
- Returns top 15 deduplicated results with scores, entities, and relationships

**`community_search`** — Thematic context discovery (Quality mode only)
- Input: Topic keyword query
- Searches community summary full-text index
- Returns up to 3 matching communities with names, member counts, and summaries

**`entity_lookup`** — Detailed entity exploration (Quality mode only)
- Input: Up to 5 entity names (supports partial matching)
- Returns entity descriptions, types, and connection counts
- Useful for exploring entities discovered during knowledge_search

**`reasoning`** — Transparent thinking (Quality mode only)
- Input: Natural language reasoning text
- Streamed to the frontend as thinking events
- Required before every other tool call in Quality mode
- Helps the agent plan its next move

**`done`** — Signal completion
- Input: Brief summary of findings + key themes
- Triggers the transition to the Writer phase

### Phase 2: Writer

After the Researcher finishes, the Writer synthesizes all gathered context:

1. All accumulated sources are deduplicated (by chunk_id, keeping highest score)
2. Sources are capped (15 for Quality, 8 for Speed) and formatted with reference IDs
3. Graph context (entities, relationships, communities) is formatted
4. The Writer receives: sources + graph context + researcher summary + question + conversation history
5. The Writer streams its response token by token

**Speed mode writing style:** Natural expert voice, concise, with inline citations `[src_1]`

**Quality mode writing style:** Comprehensive research briefing with `##` headings, subheadings, bullet points, every factual statement cited, multi-angle coverage

## Using Chat (Web Interface)

Navigate to **Explore > Chat**:

1. Type your question in the input field — the composer is multi-line: press **Enter** to send, **Shift+Enter** for a new line
2. The answer streams in real-time; a **Stop** button lets you cancel generation while it's still streaming
3. Source citations appear as clickable references
4. Follow-up questions automatically include conversation history
5. Use the collection selector to scope queries

If the connection is interrupted while an answer is streaming (for example the server is redeployed mid-answer), the partial answer is finalized cleanly with a clear message rather than leaving an endless typing indicator. Backend errors are surfaced to you directly instead of a generic message. The same composer and behavior apply to Deep Research.

## Using Deep Research (Web Interface)

Navigate to **Explore > Deep Research**:

1. Type your question
2. Watch the **Research Process** block show:
   - Sub-Questions being researched
   - Thinking Steps (reasoning events)
   - Retrieval progress
3. The final answer appears below with full Markdown formatting
4. Sources are listed with clickable citation links

## API Usage

### Streaming (Recommended)

```bash
# Chat mode (streaming)
curl -X POST http://localhost:8000/api/ask/stream \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is the main topic of the documents?",
    "use_graph": true,
    "use_reranking": true
  }'

# Deep Research mode (streaming)
curl -X POST http://localhost:8000/api/ask/stream \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Compare the different approaches and their trade-offs",
    "use_agentic": true
  }'

# Fast mode (vector-only, no hybrid/reranking)
curl -X POST http://localhost:8000/api/ask/stream \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is the main topic?",
    "use_fast_search": true
  }'
```

### Extended Thinking (Streaming with Reasoning)

```bash
curl -X POST http://localhost:8000/api/ask/stream/thinking \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What are the relationships between the main concepts?",
    "use_agentic": true
  }'
```

### SSE Event Reference

All streaming endpoints use Server-Sent Events. Each event is a JSON object:

| Event | Content | When |
|-------|---------|------|
| `content` | `{"content": "answer text chunk"}` | Writer is generating the answer |
| `sources` | `{"sources": [{chunk_id, content, score, metadata}]}` | Sources retrieved |
| `graph_context` | `{"graph_context": {entities, relationships, communities}}` | Graph context used |
| `thinking` | `{"thinking": "reasoning text"}` | Agent reasoning (thinking endpoint) |
| `sub_questions` | `{"sub_questions": ["q1", "q2"]}` | Decomposed questions |
| `retrieval` | `{"retrieval": "Found N sources"}` | Search progress |
| `retrieval_stats` | `{"retrieval_stats": {total, unique, searches, communities}}` | Final search stats |
| `communities_used` | `{"communities_used": [1, 3]}` | Community IDs used |
| `memory_update` | `{"memory_update": {...}}` | Updated conversation-memory blob (only when `conversation_memory` was sent). Arrives **after** `done` by default — keep reading until the stream closes |
| `done` | `{"done": true}` | Answer complete. Carries `pending_memory: true` when a `memory_update` still follows |
| `error` | `{"error": "message"}` | Error occurred |

### Non-Streaming

```bash
curl -X POST http://localhost:8000/api/ask \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Explain the main concepts from the documents",
    "use_graph": true,
    "use_reranking": true,
    "use_agentic": false
  }'
```

### Conversation History

Include previous messages for context-aware follow-ups:

```bash
curl -X POST http://localhost:8000/api/ask/stream \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Can you elaborate on that last point?",
    "conversation_history": [
      {"role": "user", "content": "What is machine learning?"},
      {"role": "assistant", "content": "Machine learning is a subset of AI..."}
    ]
  }'
```

The Library retains up to `MAX_CONVERSATION_HISTORY` messages (default: 6).

### Collection-Scoped Questions

```bash
curl -X POST http://localhost:8000/api/ask/stream \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Summarize the key findings",
    "collection_id": "research-papers",
    "use_graph": true
  }'
```

## Request Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `question` | string | — | The question to ask (required) |
| `top_k` | int | 5 | Number of results per search (5-20) |
| `use_graph` | bool | true | Include graph traversal in search |
| `max_hops` | int | 2 | Max graph traversal hops (1-3) |
| `use_reranking` | bool | true | Apply cross-encoder re-ranking |
| `use_agentic` | bool | false | Enable Deep Research mode |
| `use_fast_search` | bool | false | Vector-only fast mode |
| `collection_id` | string | — | Scope to a specific collection |
| `conversation_history` | array | [] | Previous conversation messages |

## Source Citations

Answers include source citations linking back to specific document chunks. In the web interface:

1. Citations appear as `[src_1]`, `[src_2]` etc. in the answer text
2. Clicking a source opens a **Source Modal** showing:
   - The full document text
   - The specific cited chunk **highlighted** with full opacity and a 3px accent-colored left border
   - Surrounding text dimmed to 60% opacity
   - Auto-scroll to the highlighted section

## Configuration Summary

```env
# Agent pipeline (vs. legacy)
ENABLE_AGENT_RESEARCH=true       # Agent for Deep Research
ENABLE_AGENT_CHAT=true            # Agent for Chat (enables skills in chat mode)

# Iteration limits
RESEARCHER_MAX_ITERATIONS_SPEED=3    # Chat: 3 iterations
RESEARCHER_MAX_ITERATIONS_QUALITY=5  # Research: up to 5 iterations

# Writer output limits
WRITER_MAX_TOKENS_SPEED=1200     # Chat answers
WRITER_MAX_TOKENS_QUALITY=8000   # Research answers

# Optional dedicated writer model (researcher keeps OPENAI_MODEL for tool calling)
# WRITER_MODEL=                  # Defaults to OPENAI_MODEL
# WRITER_API_BASE=               # Defaults to OPENAI_API_BASE
# WRITER_API_KEY=                # Defaults to OPENAI_API_KEY

# Loop efficiency (all default true)
RESEARCHER_SPEED_EARLY_WRITE=true     # Chat: skip the final confirmation LLM call
RESEARCHER_PARALLEL_TOOL_CALLS=true   # Concurrent read-only searches per agent turn
RESEARCHER_TOOL_ENTITY_HINTS=true     # Agent-supplied entities skip the extraction call
RESEARCHER_SEARCH_DEDUP=true          # Repeat searches served from cache
RESEARCHER_GIT_TOOL=auto              # git_repo tool only for read/write repos (auto | always | off)
EMIT_DONE_BEFORE_MEMORY=true          # SSE done before memory compaction

# Deep research: reflection & convergence (all default on)
RESEARCHER_FORCE_REFLECTION=true      # Force a reasoning step after unreflected search rounds
RESEARCHER_NOVELTY_MIN_NEW_RATIO=0.35 # Round below this share of new sources counts as stale
RESEARCHER_NOVELTY_STALE_ROUNDS=2     # Consecutive stale rounds before the answer is written
RESEARCHER_WALL_CLOCK_SECONDS=60     # Research time budget (0 = unlimited)

# Search configuration
ENABLE_HYBRID_SEARCH=true
ENABLE_RERANKING=true
VECTOR_WEIGHT=0.5
KEYWORD_WEIGHT=0.3
GRAPH_WEIGHT=0.2

# Reasoning control + visibility
DEFAULT_REASONING_MODE=off       # Chat: suppress hidden thinking → snappy, no empty answers
RESEARCH_REASONING_MODE=off      # Deep research: same, the loop reflects out loud instead
                                 # (the final writer always runs with reasoning off in both
                                 #  modes, so the whole token budget goes to the visible answer)
STREAM_REASONING_STEPS=true      # Show researcher steps in stream
SHOW_RETRIEVAL_STATS=true        # Show retrieval stats

# Security
PROMPT_SECURITY=true             # Injection protection in prompts
```

### If an answer looks cut off

Answers are capped by the writer limits above. When a response reaches its cap, Cortex ends it with a visible note saying it was cut short — so a truncated answer is never presented as a complete one — and logs a warning naming the limit to raise. If you see that note regularly on Deep Research, either ask narrower questions or raise `WRITER_MAX_TOKENS_QUALITY`. An answer that stops mid-sentence *without* that note is a different problem (usually a network or proxy timeout), not the token limit.
