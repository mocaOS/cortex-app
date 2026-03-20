# Chapter 12: Communities

Communities are automatically detected clusters of entities that share common themes or frequently co-occur in your documents. They provide a high-level map of the topics in your knowledge base and enhance search quality.

## What Are Communities?

Think of communities as automatically discovered topic areas. In a technology knowledge base, you might see:

- **"Ethereum Ecosystem"** — Entities: Ethereum, Solidity, EVM, Smart Contracts, Gas, DeFi
- **"Machine Learning Frameworks"** — Entities: TensorFlow, PyTorch, Keras, CUDA, GPU
- **"Cloud Infrastructure"** — Entities: AWS, Docker, Kubernetes, Terraform, CI/CD

Communities are detected by analyzing the structure of your knowledge graph — which entities are frequently connected and which tend to appear together.

## How Community Detection Works

### Step 1: Graph Projection

The entity graph is projected as an undirected, weighted graph:

- **Relationship edges** — All Entity→Entity relationships are included bidirectionally (UNION). Edge weights from the `weight` property (0-10 scale) influence community membership.
- **Co-mention edges** — Entities that appear in the same chunk receive an implicit edge with weight 2.0. This connects entities that frequently co-occur in text, even without explicit relationships.

### Step 2: Algorithm Selection

Three algorithms are tried in order:

| Algorithm | Library | Characteristics |
|-----------|---------|----------------|
| **Leiden** (preferred) | Neo4j GDS | Hierarchical, guaranteed connected communities, weight-aware |
| **Louvain** (fallback) | Neo4j GDS | Classic modularity-based, weight-aware |
| **BFS** (last resort) | Pure Python | Connected components only, no weights |

### Step 3: Size Filtering

Communities smaller than `MIN_COMMUNITY_SIZE` (default: 3) are discarded. The system monitors for pathological distributions and logs warnings for:
- Mega-communities (single community containing most entities)
- All-minimum-size communities (no meaningful clusters found)

### Step 4: Summarization

An LLM generates a name and summary for each community:

- Up to 30 member entities and 40 internal relationships are provided as context
- The LLM returns `{"name": "...", "summary": "..."}`
- An assistant prefill technique forces JSON output
- Multiple parsing fallback strategies handle various LLM output formats

## Managing Communities

### Detect Communities

```bash
# Default settings
curl -X POST "http://localhost:8000/api/graph/communities/detect?min_size=3" \
  -H "X-API-Key: your-api-key"

# Collection-scoped
curl -X POST "http://localhost:8000/api/graph/communities/detect?collection_id=my-collection&min_size=3" \
  -H "X-API-Key: your-api-key"
```

Returns a `task_id` — detection runs as a background task.

### Generate Summaries

```bash
curl -X POST http://localhost:8000/api/graph/communities/summarize \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"force_regenerate": false}'
```

Set `force_regenerate: true` to regenerate summaries for communities that already have them.

### List Communities

```bash
# Paginated listing
curl "http://localhost:8000/api/graph/communities?skip=0&limit=25&search=machine" \
  -H "X-API-Key: your-api-key"
```

### Get Community Details

```bash
curl http://localhost:8000/api/graph/communities/{id} \
  -H "X-API-Key: your-api-key"
```

Returns: community name, summary, member entities, and up to 20 key relationships within the community.

### Search Communities

```bash
curl "http://localhost:8000/api/graph/communities/search?query=blockchain&limit=5" \
  -H "X-API-Key: your-api-key"
```

Uses the `community_summary_fulltext` index for fast search across community names and summaries.

### Delete Communities

```bash
# Delete a specific community (unlinks entities, preserves them)
curl -X DELETE http://localhost:8000/api/graph/communities/{id} \
  -H "X-API-Key: your-api-key"

# Delete ALL communities
curl -X DELETE http://localhost:8000/api/graph/communities \
  -H "X-API-Key: your-api-key"
```

## How Communities Enhance Search

### In Deep Research Mode

The researcher agent has a `community_search` tool that searches community summaries for thematic context. This provides:

- **Broader understanding** — Community summaries capture high-level themes that individual chunks miss
- **Cross-document context** — Communities span multiple documents, providing synthesis
- **Topic discovery** — The agent can discover relevant topic areas it didn't initially know about

### Example

Question: "What are the main blockchain platforms discussed?"

Without communities, the agent searches for chunks mentioning "blockchain platforms" — potentially missing some.

With communities, the agent:
1. Searches knowledge → finds chunks about specific platforms
2. Searches communities → discovers the "Ethereum Ecosystem" and "Layer 1 Platforms" communities
3. Uses community summaries to provide comprehensive coverage

## When to Re-Detect Communities

The Knowledge Graph page automatically detects staleness. Re-detect when:

| Trigger | Why |
|---------|-----|
| New relationships analyzed | Graph topology changed |
| Entities merged (deduplication) | Graph topology changed |
| Significant document additions | New entities may form new clusters |

The system tracks `last_community_detection_at` and `last_entity_merge_at` timestamps to determine staleness.

## Configuration

```env
ENABLE_COMMUNITY_DETECTION=true     # Enable/disable feature
MIN_COMMUNITY_SIZE=3                 # Minimum entities per community
MAX_COMMUNITIES=50                   # Maximum communities to track
ENABLE_GRAPH_SUMMARIZATION=true     # Generate LLM summaries
COMMUNITY_SUMMARY_MODEL=            # Defaults to OPENAI_MODEL
```

## Use Cases

| Use Case | How Communities Help |
|----------|-------------------|
| **Topic discovery** | See what major themes exist in your knowledge base |
| **Content audit** | Identify well-covered and under-covered topics |
| **Focused Q&A** | Research mode uses community context for comprehensive answers |
| **Knowledge mapping** | Visualize how your knowledge is structured |
| **Onboarding** | Help new team members understand what the knowledge base covers |
| **Gap analysis** | Find topics with few entities or weak connections |
