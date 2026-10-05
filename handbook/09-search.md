# Chapter 9: Search and Discovery

This chapter explains the Library's hybrid search system — how it works, how to use it, and how to tune it for your needs.

## How Hybrid Search Works

The Library's search endpoint combines three retrieval methods and fuses their results:

```
User Query
    │
    ├──▶ Vector Search (cosine similarity on embeddings)
    │         Weight: 0.5
    │
    ├──▶ Keyword Search (Neo4j full-text index, Lucene)
    │         Weight: 0.3
    │
    └──▶ Metadata Search (filename, topic hints, custom-input content)
              Weight: 0.2
              │
              ▼
      Reciprocal Rank Fusion (RRF)
              │
              ▼
      Final Ranked Results
```

The search endpoint stops at the fusion — no cross-encoder step or entity-relationship traversal leg. Ask AI/context retrieval uses a different vector/keyword/graph fusion, with graph in place of metadata, followed by optional re-ranking; see below and [chapter 10](10-ask-ai.md).

### Vector Search (Semantic)

Your query is converted to an embedding and compared against all chunk embeddings using cosine similarity via Neo4j's vector index.

**Strengths:** Finds conceptually similar content even when different words are used. "How do I authenticate?" finds content about "login procedures" and "credential management."

**Limitations:** Can miss exact terms, especially rare names or codes.

### Keyword Search (Full-Text)

Full-text search using Neo4j's Lucene-based index on chunk content. The query is reduced to its word tokens before it reaches the index, so punctuation that Lucene treats as syntax (`/`, `:`, parentheses, a leading `-`, bare `AND`/`OR`/`NOT`) can never break the keyword leg — it used to fail silently on such input.

**Strengths:** Finds exact term matches. "ERC-721" finds all mentions of that specific standard.

**Limitations:** Misses paraphrased or conceptually related content.

### Metadata Search

The third leg matches document metadata by case-insensitive containment and returns the matching documents' chunks: the document's filename (relevance 3.0), a custom input's topic hint (2.5), or the raw content of custom inputs (2.0). Results are ordered by that relevance score, then by chunk position.

**Strengths:** Finds documents by what they are called or how they were labeled, even when the chunk text doesn't contain the query terms — and surfaces custom inputs whose raw content mentions the query.

**Limitations:** Pure substring matching on a handful of metadata fields; it doesn't understand paraphrases and doesn't reach the knowledge graph.

> Graph traversal — identifying entities in your query, resolving them to stored entities (including aliases), and following relationships to connected, mention-ranked chunks — is part of **Ask AI's** retrieval pipeline, not the search endpoint. It's described in [chapter 10](10-ask-ai.md).

### Reciprocal Rank Fusion (RRF)

RRF combines results from all three methods into a unified ranking:

```
RRF_score(chunk) = Σ (weight_i / (60 + rank_i))
```

Ranks are 1-based (the first result has rank 1). A chunk receives a contribution from each leg in which it appears, weighted by that leg and its rank. The three legs run one after another for each query.

### Cross-Encoder Re-Ranking (Ask AI retrieval)

The search endpoint returns its RRF-fused results as-is — no re-ranking is applied to `/api/search`. The Ask AI endpoints optionally re-score their retrieval candidates with a cross-encoder model that evaluates each (query, chunk) pair directly. This provides more precise relevance scores than the initial retrieval methods. The reranker is always given more candidates than it keeps (about twice `RERANK_TOP_K`, pooled across a search's queries and deduplicated first), so it selects rather than merely reorders.

Default model: `cross-encoder/ms-marco-MiniLM-L-6-v2`

The re-ranking step runs in a dedicated 2-worker thread pool to avoid blocking the event loop.

## Using Search

### Basic Search

```bash
curl -X POST http://localhost:8000/api/search \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"query": "What is machine learning?", "top_k": 5}'
```

**Response:**

```json
{
  "query": "What is machine learning?",
  "results": [
    {
      "document_id": "doc_abc123",
      "chunk_id": "doc_abc123_chunk_3",
      "content": "Machine learning is a subset of artificial intelligence...",
      "score": 0.0164,
      "document_title": "AI Fundamentals.pdf",
      "metadata": {
        "filename": "AI Fundamentals.pdf",
        "chunk_index": 3
      }
    }
  ],
  "total_results": 1,
  "total": 1
}
```

### Collection-Scoped Search

```bash
curl -X POST http://localhost:8000/api/search \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "quarterly revenue",
    "top_k": 10,
    "collection_id": "financial-reports"
  }'
```

Scoping is applied to every leg. Because Neo4j's vector index cannot filter while it searches, a scoped vector search over-fetches candidates and filters afterwards. At the default factor10, ANN depth is `max(depth, min(10 × depth, 200))`, where the search endpoint's leg depth is `top_k × 2`; a small collection can otherwise receive only the few of its chunks that happened to rank in the global top results.

### Search Within Ask AI

The Ask AI endpoints run their own retrieval internally: the researcher agent issues `knowledge_search` tool calls that execute a hybrid RRF search — vector + keyword + **graph traversal** — and re-rank the fused candidates. That graph leg (entity resolution, relationship traversal, mention-ranked passages) is why asking about "the CEO" can find the right person even when "CEO" never appears in a chunk; the search endpoint's third leg is metadata matching instead, with no graph and no re-ranking.

## Tuning Search

### Adjusting Weights

These environment variables tune the **Ask AI/context** retrieval fusion (vector + keyword + graph); they should sum to approximately 1.0. The search endpoint's own fusion is fixed at 0.5 (vector) / 0.3 (keyword) / 0.2 (metadata) and is not affected by them:

```env
VECTOR_WEIGHT=0.5     # Semantic similarity
KEYWORD_WEIGHT=0.3    # Exact term matching
GRAPH_WEIGHT=0.2      # Entity relationship traversal
```

**Tuning for your use case:**

| Use Case | Vector | Keyword | Graph | Why |
|----------|--------|---------|-------|-----|
| General Q&A | 0.5 | 0.3 | 0.2 | Balanced (default) |
| Technical docs with specific terms | 0.3 | 0.5 | 0.2 | Keyword priority for exact terms |
| Conceptual research | 0.6 | 0.2 | 0.2 | Semantic priority |
| Knowledge graph-heavy | 0.3 | 0.2 | 0.5 | Graph priority for connected knowledge |
| Simple RAG (no graph) | 0.7 | 0.3 | 0.0 | Disable graph traversal |

### Enabling/Disabling Features

```env
ENABLE_HYBRID_SEARCH=true   # Gates the Ask AI/context hybrid path (vector + keyword + graph).
                            # false falls back to legacy vector + graph traversal there — no keyword
                            # leg, but collection scope reaches both chunk queries. Graph entity/
                            # relationship metadata remains global in both retrieval paths.
                            # /api/search is unaffected: it always runs vector + keyword + metadata.
ENABLE_RERANKING=true        # Set false to skip the cross-encoder step (Ask AI retrieval)
```

### Graph Traversal Depth (Ask AI retrieval)

```env
MAX_GRAPH_HOPS=2   # How many relationship hops to follow (1-3)
```

- `1` — Only directly connected entities
- `2` — Connected entities and their neighbors (default)
- `3` — Three levels of connections (broader but potentially noisier)

## Entity Search

The Library provides a dedicated entity search endpoint using the full-text index with wildcard prefix matching:

```bash
# Search entities by name (prefix matching: "pol" finds "Polygon")
curl "http://localhost:8000/api/graph/search?query=pol" \
  -H "X-API-Key: your-api-key"
```

Results are sorted by connection count (most connected entities first), which typically surfaces the most important matches.

**How it works internally:**
1. Special characters are sanitized from the query
2. A Lucene wildcard suffix `*` is appended (e.g., "pol" becomes "pol*")
3. The full-text index `entity_name_fulltext` is searched
4. Results include entity name, type, description, and connection count
5. If the full-text search returns no results, an exact match fallback is tried

## Performance Tips

1. **Use collection scoping** — When you know which collection contains relevant content, scope your search. This reduces the search space and improves both speed and accuracy.

2. **Adjust `top_k` thoughtfully** — Request more results (10-20) for comprehensive research, fewer (3-5) for quick lookups.

3. **Re-ranking is an Ask AI feature** — The cross-encoder step significantly improves Ask AI retrieval precision. `/api/search` always returns RRF-fused results without it.

4. **Build the knowledge graph** — Ask AI retrieval's graph-traversal leg can add entity/relationship context after extraction. The search endpoint uses the same Neo4j store but has no entity-relationship traversal leg.

5. **Use the right tool for your query**:
   - Conceptual questions → Ask AI (hybrid retrieval with a graph leg)
   - Looking for a specific term or name → the search endpoint's keyword leg, or entity search below
   - Exploring entity connections → Ask AI, or the graph/visualization endpoints
   - Maximum speed → fast search mode (`use_fast_search=true` in Ask AI)
