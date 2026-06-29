# Chapter 10: Ask AI — Chat and Deep Research

The Library provides two AI-powered question-answering modes, both accessible through the web interface and API. Both use a researcher/writer agent architecture that iteratively gathers information before synthesizing an answer.

## Two Modes

| Feature | Chat (Speed) | Deep Research (Quality) |
|---------|-------------|----------------------|
| **Purpose** | Quick answers to straightforward questions | Thorough, multi-angle research for complex questions |
| **Max iterations** | 2 | 10 |
| **Tools available** | `knowledge_search`, `done` | `knowledge_search`, `community_search`, `entity_lookup`, `reasoning`, `done` |
| **Writer output** | Up to 1,200 tokens | Up to 4,000 tokens |
| **LLM calls** | 4-5 per query | 6-15+ per query |
| **Latency** | 3-8 seconds | 15-60 seconds |
| **Best for** | Factual lookups, follow-up questions | Comparisons, analysis, multi-topic questions |

### Why Chat Is Snappy

On the chat path, hidden model "thinking" is suppressed by default
(`DEFAULT_REASONING_MODE=off`). Reasoning-capable models otherwise stream
chain-of-thought in a side channel the user never sees — adding seconds before
the first answer token and, across the agent loop, sometimes exhausting the
budget into an empty answer. With it off, the first token arrives in well under
a second on a capable model, and the chat writer is tuned to lead with the
answer and stay concise. Deep Research (quality) mode is unaffected — it keeps
reasoning for thorough, multi-hop work. To restore provider-default thinking on
chat (e.g. on OpenAI models that disable parallel tool calls at low reasoning),
set `DEFAULT_REASONING_MODE=auto`. See Chapter 4 and Chapter 22.

## How the Pipeline Works

### Phase 1: Researcher Agent

An LLM-driven agent iteratively gathers information using function-calling tools:

**Speed Mode (Chat):**
1. Agent receives the question + conversation history
2. Issues 1 `knowledge_search` call with up to 3 parallel queries
3. Calls `done` with a summary of findings

**Quality Mode (Deep Research):**
1. Agent uses `reasoning` tool to plan a research strategy
2. Issues broad `knowledge_search` + `community_search` for overview
3. Follows up with targeted searches based on initial findings
4. Uses `entity_lookup` to explore key entities mentioned in results
5. Cross-references and fills gaps
6. Calls `done` with a comprehensive summary

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
| `done` | `{"done": true}` | Stream complete |
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
RESEARCHER_MAX_ITERATIONS_QUALITY=8  # Research: up to 8 iterations

# Writer output limits
WRITER_MAX_TOKENS_SPEED=1200     # Chat answers
WRITER_MAX_TOKENS_QUALITY=4000   # Research answers

# Search configuration
ENABLE_HYBRID_SEARCH=true
ENABLE_RERANKING=true
VECTOR_WEIGHT=0.5
KEYWORD_WEIGHT=0.3
GRAPH_WEIGHT=0.2

# Reasoning control + visibility
DEFAULT_REASONING_MODE=off       # Chat: suppress hidden thinking → snappy, no empty answers (deep-research stays AUTO)
STREAM_REASONING_STEPS=true      # Show researcher steps in stream
SHOW_RETRIEVAL_STATS=true        # Show retrieval stats

# Security
PROMPT_SECURITY=true             # Injection protection in prompts
```
