# Chapter 2: Core Concepts

Understanding these core concepts will help you get the most out of the Library. This chapter provides the foundational knowledge needed for every subsequent chapter.

## Documents and the Processing Pipeline

### What Happens When You Upload a Document

When you upload a document to the Library, it passes through a multi-stage processing pipeline:

```
Upload → Text Extraction → Chunking → Embedding → Entity Extraction
  → Semantic Entity Resolution → Entity-Chunk Linking → Neo4j Storage
  → Background Image Analysis (async) → Collection Assignment
```

Each stage transforms the raw document into searchable, interconnected knowledge.

### Text Extraction (Docling)

The Library uses **Docling**, a powerful document conversion engine, to extract text from a wide variety of formats:

| Format | Extensions | Notes |
|--------|-----------|-------|
| PDF | `.pdf` | Full text extraction with table structure recognition (TableFormer) |
| Word | `.docx` | Preserves structure and formatting |
| PowerPoint | `.pptx` | Extracts slide text and embedded images |
| Excel | `.xlsx` | Converts tabular data to text |
| HTML | `.html` | Strips markup, preserves content |
| Markdown | `.md`, `.markdown` | Native support |
| Plain Text | `.txt` | Direct ingestion |
| reStructuredText | `.rst` | Technical documentation format |
| LaTeX | `.tex` | Academic paper format |
| XML | `.xml` | Structured data |
| Images | `.png`, `.jpg`, `.jpeg`, `.tiff`, `.bmp` | Via vision model analysis |
| Audio | `.wav`, `.mp3` | Transcription support |

Docling also extracts embedded images from PDFs, Word documents, and presentations for separate analysis via the vision model pipeline.

### Chunks

After text extraction, documents are split into manageable pieces called **chunks**. Chunks are the fundamental unit of storage and search in the Library.

**Sentence-based chunking** (default):
- 5 sentences per chunk (configurable via `SENTENCES_PER_CHUNK`)
- 1 sentence overlap between consecutive chunks for context continuity
- Best for: Natural language documents, articles, reports

**Word-based chunking** (alternative):
- 500 words per chunk (configurable via `CHUNK_SIZE`)
- 50 words overlap (configurable via `CHUNK_OVERLAP`)
- Best for: Technical documentation, code-heavy content

**URL Protection**: Before chunking, URLs in the text are replaced with placeholders to prevent them from being split across chunk boundaries. They are restored after chunking.

**Image Placeholder Cleanup**: HTML image comments left by Docling are cleaned up, and excessive newlines are collapsed.

### Embeddings

Each chunk is converted into a numerical vector called an **embedding** — a high-dimensional representation of the chunk's semantic meaning. Two chunks that discuss similar topics will have similar embeddings, even if they use different words.

The Library supports two embedding backends:

| Backend | Model | Dimensions | Best For |
|---------|-------|-----------|----------|
| **OpenAI** (default) | `text-embedding-3-small` | 1536 | Production use, high quality |
| **sentence-transformers** (fallback) | `all-MiniLM-L6-v2` | 384 | Local development, no API key needed |

Embeddings are stored alongside chunks in Neo4j and indexed for fast cosine similarity search.

### Processing Statuses

Each document tracks its processing state:

| Status | Meaning |
|--------|---------|
| `pending` | Uploaded, waiting to be processed |
| `processing` | Text extraction and chunking in progress |
| `extracting` | Entity extraction in progress |
| `completed` | All processing finished successfully |
| `failed` | Processing encountered an error |

Documents also track image analysis progress separately via `image_progress_current` and `image_progress_total` fields, since image analysis runs asynchronously after text processing completes.

## Entities

Entities are the named "things" in your documents — the people, organizations, technologies, concepts, and other notable elements that the Library extracts and catalogs.

### Entity Extraction

During document ingestion, an LLM reads each chunk and identifies entities within it. The extraction process:

1. **Batches chunks** by token budget to fit within the LLM's context window
2. **Sends each batch** with a structured prompt asking for entities as compact pipe-delimited lines (`ENT|Name|Type|Description`; XML is still parsed as a legacy fallback)
3. **Parses the response** to extract entity names, types, and descriptions
4. **Normalizes entity types** to the 10 allowed categories using fuzzy matching
5. **Resolves duplicates** via embedding-based vector similarity (when `ENABLE_SEMANTIC_ENTITY_RESOLUTION=true`) to catch semantic matches, with Levenshtein fuzzy matching (85% threshold) as fallback
6. **Links entities to chunks** that mention them via fuzzy substring matching
7. **Extracts per-chunk relationships** — chunks with 2+ linked entities get an LLM call to extract relationships using the chunk text as direct evidence. Entity names are mapped to their canonical (dedup-resolved) names before storage, and self-referential relationships (source == target) are automatically filtered out. Stored with `extraction_method='per_chunk'`.

### The 10 Entity Types

The Library enforces a strict set of 10 entity types. Non-standard types from the LLM are fuzzy-matched to the nearest allowed type (using rapidfuzz with a 75% threshold), with a fallback to "Concept".

| Type | Description | Examples |
|------|-------------|----------|
| **Person** | Individuals mentioned in documents | "Satoshi Nakamoto", "Vitalik Buterin", "Ada Lovelace" |
| **Organization** | Companies, groups, institutions, DAOs | "Ethereum Foundation", "OpenAI", "United Nations" |
| **Concept** | Abstract ideas, theories, methodologies | "Decentralization", "Machine Learning", "Proof of Stake" |
| **Technology** | Tools, protocols, programming languages, frameworks | "Solidity", "IPFS", "GraphQL", "React" |
| **Location** | Physical or virtual places | "Silicon Valley", "Ethereum Mainnet", "Layer 2" |
| **Event** | Occurrences with temporal context | "The Merge", "NFT NYC 2025", "Black Thursday" |
| **Product** | Specific offerings, artifacts, releases | "ChatGPT", "CryptoPunk #7523", "iPhone 15" |
| **Document** | Referenced documents, standards, specifications | "ERC-721", "Bitcoin Whitepaper", "RFC 2616" |
| **System** | Software systems, platforms, infrastructure | "Uniswap", "AWS Lambda", "Kubernetes" |
| **Process** | Workflows, procedures, algorithms | "KYC Verification", "Consensus Algorithm", "CI/CD Pipeline" |

### Semantic Entity Resolution

During storage, the Library automatically merges entities that are likely the same thing but spelled differently. For example:

- "OpenAI" and "Open AI" → merged into "OpenAI"
- "Vitalik" and "Vitalik Buterin" → kept separate (below threshold)

When `ENABLE_SEMANTIC_ENTITY_RESOLUTION=true` (default), this uses embedding-based vector similarity via Neo4j's vector index to catch semantic matches (e.g., "Museum of Crypto Art" and "MOCA"). Levenshtein string similarity with an 85% threshold (configurable via `ENTITY_SIMILARITY_THRESHOLD`) is used as a fallback. When entities are merged, the canonical entity gains aliases tracking all the variant names.

### Entity Storage in Neo4j

Each entity is stored as a Neo4j node with properties:

```
(:Entity {
  name: "OpenAI",              # Unique entity name
  type: "Organization",         # One of 10 allowed types
  description: "AI research...",# Context-aware description
  aliases: ["Open AI"],         # Alternative names
  source_documents: ["doc1"],   # Documents where found
  extraction_count: 5,          # Times extracted across chunks
  community_id: 3,              # Assigned community (if any)
  created_at: datetime          # First extraction timestamp
})
```

## Relationships

Relationships are typed, weighted connections between entities. They represent how entities relate to each other in the real world as described in your documents.

### Two-Phase Extraction

Relationship discovery is a separate step from entity extraction:

- **Phase A (Per-Document)**: Entity extraction happens during document ingestion — each document's chunks are analyzed individually. After entity extraction and chunk linking, **per-chunk relationship extraction** runs: chunks with 2+ linked entities get an LLM call to extract relationships using the chunk text as direct evidence. Entity names are mapped to their canonical (dedup-resolved) names before storage, and self-referential relationships are automatically filtered out. Stored with `extraction_method='per_chunk'`.
- **Phase B (Per-Collection)**: Cross-document relationship analysis happens as a separate job — entities across the entire collection are analyzed together to discover cross-document connections not visible within individual chunks.

This two-phase approach means entities and evidence-grounded relations are discovered incrementally (as each document is processed), while cross-document relations are discovered holistically (across all documents at once).

### The 14 Standard Relationship Types

| Type | Description | Example |
|------|-------------|---------|
| `RELATED_TO` | General association | "Bitcoin" → "Blockchain" |
| `CREATED_BY` | Creation/authorship | "Ethereum" → "Vitalik Buterin" |
| `WORKS_FOR` | Employment/affiliation | "Sam Altman" → "OpenAI" |
| `PART_OF` | Component/membership | "Solidity" → "Ethereum Ecosystem" |
| `USES` | Utilization/dependency | "Uniswap" → "Ethereum" |
| `LOCATED_IN` | Physical/virtual location | "OpenAI" → "San Francisco" |
| `DEPENDS_ON` | Technical dependency | "DeFi" → "Smart Contracts" |
| `DERIVED_FROM` | Origin/basis | "ERC-721" → "ERC-20" |
| `INTERACTS_WITH` | Interaction/communication | "Chainlink" → "Ethereum" |
| `COMPETES_WITH` | Competition | "Solana" → "Ethereum" |
| `COLLABORATES_WITH` | Partnership/cooperation | "Polygon" → "Ethereum Foundation" |
| `INFLUENCES` | Impact/effect | "Bitcoin Whitepaper" → "Cryptocurrency" |
| `PRECEDES` | Temporal ordering | "Bitcoin" → "Ethereum" |
| `IMPLEMENTS` | Implementation | "Uniswap" → "Automated Market Maker" |

Non-standard types from the LLM are fuzzy-matched to these 14 types (80% threshold via rapidfuzz), with a fallback to `RELATED_TO`. The `MENTIONS` type was intentionally removed as it was being used as a lazy catch-all for co-occurrence without meaningful semantic content.

### Relationship Properties

Each relationship includes:

- **Type**: One of the 14 standard types
- **Description**: A natural language explanation of the connection
- **Weight**: A 0-10 scale indicating the strength of the relationship (default: 5.0)
- **Confidence**: A 0.0-1.0 score indicating how confident the LLM is in the relationship (relationships with confidence < 0.5 are filtered before storage; self-referential relationships where source == target are also filtered out)
- **Source document**: The document(s) that evidence the relationship
- **Extraction method**: How the relationship was discovered (`per_chunk` for Phase A chunk-level extraction, or Phase B cross-document analysis)

### Relationship Batching

For large knowledge bases, relationship analysis processes entities in batches:

- **120 entities per batch** (hard cap)
- **5% degree-aware overlap** between batches to catch cross-batch relationships (hub entities excluded from overlap after 2+ appearances)
- **Sequential or parallel** execution (configurable via `PARALLEL_RELATIONSHIP_BATCHES`)
- **Source text context**: For each batch, the system fetches co-mention chunks (chunks where batch entities appear together) and provides them to the LLM alongside the entity list
- **Existing relationship filtering**: Up to 400 existing relationships per batch are provided to avoid rediscovery

## Communities

Communities are clusters of entities that frequently appear together or are tightly connected in the knowledge graph. They represent the natural topic groupings in your knowledge base.

### How Communities Are Detected

1. **Graph Projection** — The entity graph is projected with undirected, weight-aware relationships. Additionally, **co-mention edges** are added: entities that appear in the same chunk receive an implicit connection with weight 2.0.
2. **Algorithm Selection** — The system tries three algorithms in order:
   - **Leiden** (preferred) — Hierarchical community detection with guaranteed connected communities. Uses Neo4j Graph Data Science (GDS) library.
   - **Louvain** (fallback) — Classic modularity-based detection. Also via GDS.
   - **BFS** (last resort) — Simple connected-component detection using breadth-first search. Used when GDS is unavailable.
3. **Size Filtering** — Communities below the minimum size (default: 3 entities) are discarded
4. **Summarization** — The extraction model generates a descriptive name and summary for each community

### Community Storage

```
(:Community {
  id: 3,
  name: "Ethereum Ecosystem",
  summary: "This community contains entities related to...",
  entity_count: 12,
  collection_id: "default"
})

(:Entity)-[:HAS_MEMBER]->(:Community)
```

### How Communities Enhance Search

During Deep Research mode, the researcher agent can use a `community_search` tool to find community summaries relevant to the question. This provides high-level thematic context that individual chunk searches might miss.

For example, asking "What are the main blockchain platforms?" would benefit from community summaries describing the "Ethereum Ecosystem" and "Bitcoin & Layer 1 Networks" communities.

## Collections

Collections are organizational containers for documents. Each collection maintains its own scoped view of the knowledge graph.

### Collection Architecture

```
(:Collection {id: "research-papers", name: "Research Papers", description: "..."})
(:Collection)-[:CONTAINS]->(:Document)
```

When you scope a search or question to a collection, only documents, chunks, and entities belonging to that collection are considered. This enables:

- **Multi-tenancy** — Different teams access different knowledge
- **Project isolation** — Separate knowledge bases per project
- **Focused queries** — Better accuracy when you know which collection is relevant
- **Agent personas** — Different collections for different agent personalities

### The Default Collection

Documents uploaded without specifying a collection go into the default collection (configurable via `DEFAULT_COLLECTION`, defaults to `"default"`). Queries without a `collection_id` search across all collections.

## GraphRAG and Hybrid Search

### What is GraphRAG?

GraphRAG (Graph-enhanced Retrieval-Augmented Generation) is the Library's approach to answering questions. It goes beyond simple vector similarity search by combining multiple retrieval methods and leveraging the knowledge graph structure.

### The Three Search Methods

| Method | Weight | How It Works | What It Finds |
|--------|--------|-------------|---------------|
| **Vector Search** | 0.5 | Converts query to embedding, finds similar chunks via cosine similarity | Semantically related content, even with different wording |
| **Keyword Search** | 0.3 | Full-text search in Neo4j using Lucene | Exact term matches, specific names, codes, acronyms |
| **Graph Traversal** | 0.2 | Identifies entities in the query, follows their relationships in the graph | Contextually connected content through entity relationships |

### Reciprocal Rank Fusion (RRF)

Results from all three methods are combined using RRF, a technique that produces a unified ranking more robust than any single method:

```
RRF_score(chunk) = Σ (weight_i / (k + rank_i))
```

Where `k = 60` (a constant), `rank_i` is the chunk's position in method `i`'s results, and `weight_i` is the method's configured weight.

### Cross-Encoder Re-Ranking

After RRF fusion, an optional cross-encoder model re-scores the top results against the original query. The cross-encoder evaluates each (query, chunk) pair directly, providing more precise relevance scores than the initial retrieval.

Default model: `cross-encoder/ms-marco-MiniLM-L-6-v2`

### The Researcher/Writer Pipeline

For Q&A, the Library uses a two-stage agentic pipeline:

1. **Researcher Agent** — An LLM-driven agent that iteratively gathers information using function-calling tools:
   - `knowledge_search` — Hybrid RRF search with re-ranking
   - `community_search` — Search community summaries for thematic context
   - `entity_lookup` — Look up specific entities and their connections
   - `reasoning` — Transparent reasoning about findings
   - `done` — Signal completion

2. **Writer** — A separate LLM call that synthesizes all gathered context into a coherent, cited answer

This architecture ensures thorough information gathering before answer generation, producing more comprehensive and accurate responses.

## Neo4j Graph Schema

The Library uses this graph structure:

```
(:Document {id, filename, file_type, file_size, upload_date, processing_status})
(:Chunk {id, content, embedding, chunk_index, metadata})
(:Entity {name, type, description, aliases, community_id, created_at})
(:Community {id, name, summary, entity_count})
(:Collection {id, name, description, created_at})
(:SystemMeta {key, value})  -- Staleness timestamps
(:MergeHistory {id, canonical_name, merged_names, timestamp})  -- Audit trail
(:APIKey {id, name, key_prefix, key_hash, permissions, is_active})

# Relationships
(:Document)-[:HAS_CHUNK]->(:Chunk)
(:Chunk)-[:MENTIONS]->(:Entity)
(:Entity)-[:RELATED_TO|USES|CREATED_BY|...]->(:Entity)  -- 14 types, weighted with confidence
(:Entity)-[:HAS_MEMBER]->(:Community)
(:Collection)-[:CONTAINS]->(:Document)
```

### Indexes

The Library creates several indexes for performance:

- **Vector index** on `Chunk.embedding` — Cosine similarity search
- **Full-text index** on `Chunk.content` — Keyword search
- **Full-text index** on `Entity.name` and `Entity.description` — Entity search
- **Full-text index** on `Community.summary` — Community search
- **Uniqueness constraints** on Document.id, Chunk.id, Entity.name, Community.id

## Staleness Tracking

The Library tracks timestamps in `SystemMeta` Neo4j nodes to detect when pipeline steps are out of date:

| Key | Tracks |
|-----|--------|
| `last_relationship_analysis_at` | When Phase B relationship analysis last completed |
| `last_community_detection_at` | When community detection last completed |
| `last_entity_merge_at` | When entity deduplication last performed |

The Knowledge Graph page uses these timestamps to determine:
- Step 1 stale → New documents uploaded since last entity extraction
- Step 2 stale → New entities since last relationship analysis
- Step 3 stale → Relationships changed OR entities merged since last community detection

Steps cascade — if Step 1 is stale, Steps 2 and 3 are blocked.

## What's Next

- **Ready to deploy?** Continue to [Chapter 3: Getting Started](03-getting-started.md)
- **Want to start using Cortex?** Jump to [Chapter 6: The Web Interface](06-web-interface.md)
