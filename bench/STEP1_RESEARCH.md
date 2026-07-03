# Step 1 performance research — best practices & evaluation (2026-07-03)

Research pass over the state of the art in efficient LLM-based KG construction
(GraphRAG-family frameworks, small-model extractors, LLM serving practices),
evaluated against Cortex's Step 1 (Phase A: entity extraction + per-chunk
relationship extraction). Sources were primary-verified (repo code, arXiv,
official blogs); vendor claims adversarially checked. Companion to
`bench/BASELINE.md` — every recommendation below that changes extraction
behavior goes through the A/B gate there.

## Measured Step 1 cost anatomy (local 1,267-doc library, ~prod-scale)

| Sub-stage | Volume | Notes |
|---|---|---|
| Per-chunk relationship extraction | **~17,050 LLM calls** (chunks with 2+ entities; avg 4.8 entities/chunk) | **~85% of Step 1 call volume** — the dominant cost |
| Entity extraction | ~1.3–3k calls | 256k-token budget packs whole docs into 1–2 calls each |
| Entity dedup + storage | 22k entities | per-item Neo4j round trips; Levenshtein full scan on embedding miss |
| Docling conversion | — | solved (warm helper service) |
| Effective LLM concurrency | **≤ 6 in flight** | `CONCURRENT_RELATIONS=3` × `BATCH_PROCESSING_CONCURRENCY=2` |

Wall-clock is dominated by 17k small relationship calls at concurrency ≤6.

## Ranked levers (impact × effort × risk)

### 1. Raise LLM concurrency (no code, biggest wall-clock lever)
Benchmarked: 27B-class models on vLLM saturate at **32–64 concurrent
requests** (~20× aggregate throughput vs c=1; inter-token latency stays flat
~65 ms/token — LLMKube Qwen3.6-27B bakeoff, DatabaseMart Gemma-3-27B: knee at
50–100 concurrent). vLLM's default `max_num_seqs` is 256; our ≤6 leaves the
server mostly idle. Action: raise `CONCURRENT_RELATIONS` /
`BATCH_PROCESSING_CONCURRENCY` (and `PARALLEL_RELATIONSHIP_BATCHES`) per
provider — self-hosted vLLM: target 32+ total in flight; Venice: ceiling is
100 req/min standard tier, use adaptive backoff (tenacity retry already
exists). Expected: **3–8× Step 1 wall-clock** depending on provider headroom.

### 2. Bench-validate and default-on the existing flags (built, never gated)
`bench/BASELINE.md` procedure exists but the baseline was never captured.
- `ENABLE_BATCHED_CHUNK_RELATIONSHIPS` (+`RELATIONSHIP_CHUNKS_PER_CALL=4`):
  17k → ~4.3k calls. Batch-prompting literature says accuracy holds for <8–16
  items/call on large models (small models degrade faster) — 4–6 chunks/call
  on 27B is inside the safe zone; consider raising to 6 after the A/B.
- `ENABLE_BATCHED_KG_WRITES` + `ENTITY_DEDUP_PREFILTER`: cuts the 22k-entity
  storage loop from per-item round trips + full Levenshtein scans to batched
  UNWINDs + top-50 fulltext candidates. Matters at 20k+ entities.
Expected combined with #1: relationship pass from hours to ~30–45 min.

### 3. Verify prompt-cache hits (near-free)
vLLM V1 automatic prefix caching is **default-on with <1% overhead**; measured
TTFT cuts of 60–80% with 2k+-token shared prefixes. Our prompts are already
static-first (system prompt + relation types before chunk text) — verify
byte-stability and cache-hit metrics per provider; OpenAI: shard
`prompt_cache_key` (~15 RPM/key limit); OpenRouter: pin the provider or
caching silently dies; Venice: supports `cache_control`.

### 4. Output-token economy (medium effort, needs quality A/B)
Decode time scales linearly with output tokens; our XML repeats full entity
names and long tag names per relationship (~60–80 output tokens each).
Precedent: Microsoft GraphRAG emits delimiter tuples precisely to cut
structural overhead. Options in ascending aggressiveness: shorter tags
(`<r>` vs `<relationship>`), reference entities by index instead of repeating
names, cap relationship-description length. Keep entity descriptions rich
(they feed embeddings, dedup, UI, RAG). Plausible 30–50% decode cut on the
relationship pass. Gate on extraction-quality metrics (entity/rel counts,
QA scores per BASELINE.md).

### 5. Smaller relationship-extraction model (config-only experiment)
Strongest evidence that extraction quality is above the knee: HippoRAG 2's
ablation — GPT-4o-mini vs Llama-3.3-70B for extraction: downstream QA F1
58.1 vs 59.8 (~97% retained). nano-graphrag ships a cheap-model/best-model
split as standard practice. We already have the three-tier config: point
`RELATIONSHIP_EXTRACTION_MODEL` at an 8–9B instruct and run
`bench/run_bench.py`. If quality holds: 2–3× per-call latency + big $ cut.
Later option: Distill-SynthKG showed a fine-tuned Llama-3-8B *beating* its
70B teacher at ~3% of GPT-4o cost — a path if we ever want an in-house
extraction model.

### 6. Chunk size (cheap experiment, recall tradeoff)
We chunk at 500 words (~650 tokens); Microsoft GraphRAG moved its default
300 → 1200 tokens explicitly as a cost lever, knowing "600-token chunks
extract ~2× the entity references of 2400-token chunks". Raising CHUNK_SIZE
directly shrinks the per-chunk call count. Bench entity-count delta before
committing; interacts with RAG retrieval granularity.

### 7. GLiNER first-pass entities in cortex-helper (medium-term, strategic fit)
GLiNER2 (205M params, Apache 2.0) reaches CrossNER F1 0.590 vs GPT-4o's
0.599 — parity on *named* types (Person/Org/Location/Event/Technology) at
1/1000th the size; weaker on abstract Concepts. ONNX/Rust serving is
torch-free (gline-rs: ~6.7 seq/s CPU, ~249 seq/s on a 4080) and ships a
Ray-Serve HTTP mode — the exact shape of our shared-helper pattern
(reranker/docling precedent). Tiered ladder (Neo4j agent-memory pattern):
GLiNER for named entities → LLM only for Concepts + all relationship
writing. Relationship extraction stays LLM: best zero-shot RE encoders hit
only ~25% strict F1, emit no descriptions, and GLiREL is CC BY-NC (license
blocked); GLiNER-Relex (CC BY 4.0) is usable only as a candidate proposer.

### 8. Chunk-level extraction cache (nice-to-have)
LightRAG pattern: MD5 chunk-content hash + (model+prompt)-keyed LLM response
cache makes re-ingest/re-index near-free. We have doc-level
`ENABLE_REPROCESS_DELTA`; chunk-level cache would help partial re-imports
and prompt-unchanged rebuilds.

## What NOT to do (evidence against)

- **NLP-only extraction (LazyGraphRAG `--method fast`, E2GraphRAG):** 0.1% of
  indexing cost is real, but Microsoft's own docs say the resulting graph is
  "quite a bit noisier… less directly relevant for use outside" retrieval.
  Cortex's graph is a user-facing product surface (Explore, entity editing,
  communities) — quality is the product. Only viable someday as an instant
  "draft graph" preview tier.
- **Gleaning passes:** never add to Step 1. The only independent ablation
  found disabling gleaning *improved* accuracy; fast-graphrag ships gleaning=0;
  Microsoft never quantified its benefit. (Step 2 legacy scan still has one —
  moot now that targeted mode is default.)
- **Triplex/SciPhi:** abandoned since 2024, LLM-judge-only claims, NC license.
- **Combined-vs-split extraction:** no controlled evidence either way; our
  split (doc-level entities, chunk-level relationships) is already
  call-efficient on the entity side. Don't refactor to per-chunk combined
  calls — that would *increase* entity-call granularity; chunk-batched
  relationship calls (#2) achieve the same consolidation.
- **Batch APIs for the default path:** 1–24 h nondeterministic turnaround is
  wrong for a progress-bar UX. Possible future opt-in "economy re-index" mode
  (OpenAI Batch/Flex, Anthropic Batches, 50% off).

## Validation log

**2026-07-03 — lever #2 (chunk-batched relationship extraction): PASSED, enabled.**
Live A/B on the local stack (qwen3-6-27b via Venice, 24 real chunks, concurrency 3):

| Path | Calls | Wall | Rels | Chunks w/ 0 | Avg conf | With description |
|---|---|---|---|---|---|---|
| single (prod) | 24 | 20.0s | 45 | 7/24 | 0.93 | 0/45 |
| batched ×4 | 6 | 20.1s | 43 | 4/24 | 0.90 | 43/43 |
| batched ×6 | 4 | 23.5s | 51 | 2/24 | 0.94 | 51/51 |

Parity: 0 chunks lost at ×4 and ×6. Same wall-clock at fixed concurrency (decode-bound,
as predicted) — the gain is **÷4–6 request count**, i.e. 4–6× effective throughput under a
req/min rate limit. Two defects found and fixed during validation:
1. The batched prompt didn't pin the `<relationship>` element format → qwen emitted
   `<relation>` instead of `<type>` → every grouped parse returned 0 and silently
   re-dispatched per chunk (batched path never actually fired). Fixed: explicit
   one-line format example in the prompt + `<relation>` accepted as `<type>` alias
   in `_extract_xml_relationships`.
2. Same root cause on the *single* path: models omitted `<description>` — 99.5%
   (48,768/49,001) of per-chunk relationships in the prod-scale local graph had empty
   descriptions. With the format example: 100% description coverage (verified 11/11).

Repo default flipped to ON (owner decision, 2026-07-03) with
`RELATIONSHIP_CHUNKS_PER_CALL=4`. Remaining open: entity-count/QA-score A/B
on the 15-doc bench corpus as a post-hoc confirmation.

## Suggested sequence

1. Capture the bench baseline (BASELINE.md procedure — 15-doc corpus).
2. A/B: batched chunk relationships (4/call) → concurrency raise → both.
3. A/B: batched KG writes + dedup prefilter (Neo4j-side, model-independent).
4. Verify prefix-cache hit rates on the prod provider; fix any byte-instability.
5. A/B: 8–9B relationship model; then output-schema slimming.
6. Prototype GLiNER in cortex-helper behind a flag (entity recall assist).
