# Incremental Reprocess & Graph Conservation

Authoritative home for how a **changed** source document is re-ingested without
re-paying the whole document and without destroying graph state other documents
or users contributed. Shipped 2026-10-13. Product promise: users "just sync
their files" — no repo-structure rules (e.g. splitting changelogs) required.

## Problem (measured 2026-10-13)

Before: any byte change → whole-document fingerprint mismatch → delete all
chunks → re-embed + re-extract 100%. Positional sentence windows meant even a
chunk diff could not help the common case: prepending a release section to a
changelog shifted every boundary. Measured on three real 1.2k–2.4k-line
CHANGELOGs (share of characters re-extracted):

| Edit | Before | Legacy splitter + diff | Structural chunker + diff |
|---|---|---|---|
| Section prepended | 100% | 100% | 0.7–2.1% |
| 2 lines appended | 100% | 0.8–14% | 0.3–3.8% |
| 1 line edited mid-file | 100% | 4–64% | 1.7–3.1% |

The legacy sentence splitter also builds huge chunks from bullet markdown
(median 440–1750 words, max ~6k) because bullets are not sentences.

## Components

1. **Claim-set relationship provenance** — entity→entity edges carry
   `source_document_ids` and `source_chunk_ids` lists (additive; legacy
   `source_document_id` stays = most recent claimant). Every store path appends
   (legacy-initialised from the single field). Document removal releases the
   doc's claims; an edge is deleted only when **no** document claim remains —
   unless it carries `step2_verified = true` (a Step-2 cross-collection writer's
   own claim): then it is kept with its document claims cleared, like an edge
   only Step 2 ever wrote. Per-chunk relationship claims are attributed per
   chunk from the batched results map (each edge gets exactly the chunks that
   stated it).
   `remove_chunk_claims(doc_id, chunk_ids)` releases chunk-level claims (doc
   claim dropped when its last chunk claim goes). Fixes the old last-writer-wins
   loss (reprocessing doc B deleted edges doc A also asserted). Step-2
   `rebuild` (`delete_batch_relationships`) retracts the Step-2 claim (removes
   `step2_verified`) and deletes only edges no document claims; doc-claimed
   edges stay (reverted to `per_chunk` when they hold chunk claims).
   Detail: [`relationships.md`](relationships.md).
2. **Entity retirement** — an orphaned entity is **retired** (label swap
   `:Entity` → `:RetiredEntity`, `retired_at`, `retired_from_document`; all
   edges kept) when curated: `curated_at` set (by `update_entity` /
   `merge_entities`), non-empty `aliases`, or any non-`per_chunk` edge.
   Plain auto-extracted orphans are still hard-deleted. All indexes/queries are
   `:Entity`-scoped, so retired nodes vanish from retrieval. Re-extracting the
   name (case-insensitive, or an alias) **revives** the node before any MERGE.
   Revival looks up unlinked `RetiredEntityKey {key, name}` nodes (key =
   lower(name)/lower(alias); indexes `retired_entity_name`, `retired_entity_key`,
   `retired_entity_key_name`; startup backfills keys for retired nodes lacking
   them; orphaned keys are dropped on purge/reset). Unlinked so no traversal or
   degree count sees them.
   Detail: [`entities.md`](entities.md); admin endpoints in
   [`admin-features.md`](admin-features.md).
3. **Structural chunker** (`services/structural_chunker.py`, opt-in
   `CHUNKING_STRATEGY=structural`) — blank-line blocks; fenced code atomic;
   a heading forces a boundary once the buffer has ≥ `STRUCTURAL_CHUNK_MIN_WORDS`
   (smaller sections pack together); otherwise a boundary falls after a block
   whose sha1 % `STRUCTURAL_CHUNK_BOUNDARY_DIVISOR` == 0 or at
   `STRUCTURAL_CHUNK_MAX_WORDS`; a chunk never ends on a heading; no overlap
   (stability over context). Median 95–135 words on the measured changelogs.
   The legacy fingerprint (`_reprocess_config_hash`) is byte-identical for
   `legacy`; switching strategy changes fingerprints (one full reprocess per
   doc on its next change). **Default stays `legacy` until a retrieval-quality
   bench** compares the two.
4. **Chunk-delta reprocess** (`ENABLE_CHUNK_DELTA_REPROCESS`, default on;
   requires `ENABLE_INGEST_RESUME`). Eligible (`_delta_eligible`): last run
   completed under claim tracking (`Document.claims_version`,
   `completed_file_sha256`, `completed_config_hash`), config unchanged, bytes
   changed, all chunks embedded, **no image chunks**, no unfinished delta
   (`Document.delta_in_progress` = file hash while a delta mutates the graph),
   not a forced (`engine=docling`) reprocess. `claims_version=1` and the
   `completed_*` fields are granted only when the run started clean (full
   cleanup / no prior chunks / already tracked) or no pre-claim (document-level
   only) edge of the doc remains — such an edge can't be released by a chunk
   delta. If the **new** version contains images, the run falls back to the full
   path before background image analysis starts storing image chunks. The
   fingerprint uses the file hash taken at run start. `plan_chunk_delta` multiset-matches new chunks
   to stored ones by `content_hash`; below `CHUNK_DELTA_MIN_REUSE_RATIO`
   (0.3) → full path. Kept chunks keep id/embedding/MENTIONS/`rels_extracted`
   (re-indexed); removed chunks release claims, record orphan candidates, are
   deleted; new chunks get content ids `{doc}_chunk_h{sha256[:16]}[_{n}]`
   (never index-based) and are the only ones embedded and entity-extracted
   (existing `skip_chunk_indices`). New entities are linked across all chunks;
   a kept chunk gaining a MENTIONS is re-opened for per-chunk relationships.
   `finalize_chunk_delta` resolves orphan candidates (retire/delete) only when
   no chunk mentions them and no `source_documents` claim remains.
   `extraction_stats.delta = {kept, removed, added}`.
   Pipeline mechanics: [`document-pipeline.md`](document-pipeline.md).
   Cancellation (abort, in-place replace of a running doc) reaches
   batch-processed docs too. With `ENABLE_INGEST_RESUME=false` the mismatch
   path still cleans up claim-aware (claim release + retirement).
5. **Identity-preserving sources** — callers no longer pre-wipe:
   git sync sets pending + provenance only ([`git-integration.md`](git-integration.md));
   `POST /api/upload` with `source_key` / `replace_document_id` replaces a doc
   in place (same id; identical bytes on a completed doc → no-op `unchanged`;
   **409** if the target is git-synced — git sync owns those docs);
   web import keys docs `url:<normalized>` / `site:<host>:<hash>`
   ([`web-crawl.md`](web-crawl.md)); the sync apps use the upload contract
   ([`apps.md`](apps.md)).

## Persisted state (slow layer)

New/changed persisted state — see [`../upgrade-recovery.md`](../upgrade-recovery.md#incremental-reprocess-state):
relationship `source_document_ids`/`source_chunk_ids`/`step2_verified`; label
`RetiredEntity` + `retired_at`/`retired_from_document`; `RetiredEntityKey`
lookup nodes + 3 indexes; entity `curated_at`; Document `source_key` (+ index
`document_source_key`), `updated_at`, `claims_version`, `completed_file_sha256`,
`completed_config_hash`, `delta_in_progress`, `delta_orphan_candidates`;
content-addressed chunk ids. Library export/import carries the claim lists and
a separate `retired_entities.ndjson` section (older importers skip it, so they
drop retired entities and their edges).

## Known limitations

- Image-bearing documents (old or new version) take the full path (image chunk ids are index-based).
- Legacy documents (no `claims_version`) get **one** full reprocess on their first change.
- Entity descriptions keep text contributed by removed chunks until regenerated;
  entities from removed chunks are counted in that run's `entity_count`.
- Delta extraction lacks surrounding-chunk context for new chunks.
- A crash between linking and clearing re-open flags can skip those chunks' relationship re-run;
  restart-mid-delta is reasoned/unit-tested, not live-crash-tested.
- The upload key lock is per process (no cross-replica serialization).
- Apps' `cortex.minVersion` is not enforced by the backend; apps detect an old
  backend by a missing `replaced` field and fall back to delete-after-upload.
- Step-2 cross-collection writes onto claimed edges still relabel `extraction_method`.

## Tests

`test_relationship_claims.py`, `test_entity_retirement.py`,
`test_structural_chunker.py`, `test_chunk_delta_reprocess.py`,
`test_upload_source_key.py`; live opt-in gates in [`../qa.md`](../qa.md#incremental-reprocess-live-gates).
