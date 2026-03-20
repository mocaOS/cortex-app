# Chapter 9: Search and Discovery

This chapter explains the Library's hybrid search system — how it works, how to use it, and how to tune it for your needs.

## How Hybrid Search Works

The Library combines three retrieval methods and fuses their results for comprehensive search:

```
User Query
    │
    ├──▶ Vector Search (cosine similarity on embeddings)
    │         Weight: 0.5
    │
    ├──▶ Keyword Search (Neo4j full-text index, Lucene)
    │         Weight: 0.3
    │
    └──▶ Graph Traversal (entity relationships)
              Weight: 0.2
              │
              ▼
      Reciprocal Rank Fusion (RRF)
              │
              ▼
      Cross-Encoder Re-Ranking (optional)
              │
              ▼
      Final Ranked Results
```

### Vector Search (Semantic)

Your query is converted to an embedding and compared against all chunk embeddings using cosine similarity via Neo4j's vector index.

**Strengths:** Finds conceptually similar content even when different words are used. "How do I authenticate?" finds content about "login procedures" and "credential management."

**Limitations:** Can miss exact terms, especially rare names or codes.

### Keyword Search (Full-Text)

Full-text search using Neo4j's Lucene-based index on chunk content. Special characters are escaped for safe querying.

**Strengths:** Finds exact term matches. "ERC-721" finds all mentions of that specific standard.

**Limitations:** Misses paraphrased or conceptually related content.

### Graph Traversal

Entities mentioned in your query are identified, then their relationships in the knowledge graph are followed to find connected chunks.

**Strengths:** Discovers content that is contextually related through entity connections, even if it doesn't directly contain your search terms. Asking about "Vitalik Buterin" can surface content about "Ethereum" through the CREATED_BY relationship.

**Limitations:** Depends on entities being correctly extracted and relationships existing.

### Reciprocal Rank Fusion (RRF)

RRF combines results from all three methods into a unified ranking:

```
RRF_score(chunk) = Σ (weight_i / (60 + rank_i))
```

This formula ensures that chunks appearing in multiple result sets rank higher, while the weights control each method's influence.

### Cross-Encoder Re-Ranking

After RRF, the top results are optionally re-scored by a cross-encoder model that evaluates each (query, chunk) pair directly. This provides more precise relevance scores than the initial retrieval methods.

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
      "score": 0.89,
      "metadata": {
        "document_name": "AI Fundamentals.pdf",
        "chunk_index": 3
      }
    }
  ],
  "total_results": 5
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

### Search Within Ask AI

The Ask AI endpoints use the same hybrid search internally. When you ask a question, the researcher agent issues `knowledge_search` tool calls that execute hybrid RRF search with re-ranking behind the scenes.

## Tuning Search

### Adjusting Weights

The three search method weights should sum to approximately 1.0:

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
ENABLE_HYBRID_SEARCH=true   # Set false for vector-only search
ENABLE_RERANKING=true        # Set false to skip cross-encoder step
```

### Graph Traversal Depth

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

3. **Enable re-ranking** — The cross-encoder step significantly improves precision with minimal latency cost. Keep it enabled unless you need sub-100ms responses.

4. **Build the knowledge graph** — Search quality improves dramatically once entity extraction, relationship analysis, and community detection are complete. The graph weight in RRF provides context that pure text search cannot.

5. **Use the right search type for your query**:
   - Conceptual questions → hybrid search (default)
   - Looking for a specific term or name → keyword search
   - Exploring entity connections → graph traversal
   - Maximum speed → fast search mode (`use_fast_search=true` in Ask AI)
