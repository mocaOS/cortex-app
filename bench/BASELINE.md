# Bench baseline — regression anchor for the v-next efficiency flags

Every efficiency flag shipped in the v-next workstream (`entity_dedup_prefilter`,
`enable_batched_kg_writes`, `enable_batched_chunk_relationships`,
`enable_prompt_cache_control`, `enable_phaseb_checkpointing`,
`enable_reprocess_delta`) is **gated on an A/B bench run against a recorded
baseline** before it may be documented as recommended-on. This file pins how
that baseline is captured and what "non-regressing" means.

## Capturing the baseline

1. Place the benchmark dataset in `bench/files/` (any `.md` files; the standard
   set is the 15-doc corpus used in prior runs). Record its identity:

   ```bash
   cd bench/files && sha256sum *.md | sha256sum   # dataset fingerprint
   ```

2. Ensure the local stack is up (`docker compose ps` → backend + neo4j healthy)
   and `.env` has `ADMIN_API_KEY` + provider keys per `bench/README.md`.

3. Run the anchor combo with **all v-next flags at their defaults (off)**:

   ```bash
   python bench/run_bench.py --only <anchor-combo>
   ```

4. Archive into `bench/backups/baseline-<date>/`:
   - the results spreadsheet (`build_results_ods.py` output)
   - `bench/logs/` for the run (contains per-call LLM I/O from `_llm_io.py` —
     this is where call *counts* come from)
   - the dataset fingerprint and the combo name
   - `git rev-parse HEAD` of the cortex-app commit benchmarked

## A/B gate for each flag

Re-run the same combo on the same dataset with exactly one flag flipped on,
then compare against the baseline:

| Metric | Source | Gate |
|---|---|---|
| Entity count | results sheet / `/api/stats` | within ±2% |
| Relationship count | results sheet | within ±10% |
| Q&A scores (faithfulness, completeness, groundedness) | `qa_evaluator.py` | non-regressing |
| Heuristics verdict | `heuristics.py` | not worse (GOOD stays GOOD) |
| LLM call count | `log_parser.py` over `bench/logs` | shows the expected reduction (e.g. ÷~4 for chunk-batched rels) |
| Wall-clock per phase | `log_parser.py` | improvement or neutral |

A flag that fails its gate stays default-off and undocumented until fixed.

## Status

- **2026-06-10**: procedure pinned. `bench/files/` is empty in the repo
  (dataset is operator-provided and gitignored) — the first baseline capture
  happens on the dev machine with the standard 15-doc corpus before any
  Phase 2+ flag is recommended-on.
