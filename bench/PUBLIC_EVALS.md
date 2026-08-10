# Public Evals — design & runbook (groundwork, not yet published)

Goal: turn the bench harness's measurements into a **publishable retrieval-quality
eval** — a credibility asset ("here is how well Cortex answers, measured, reproducibly")
— without exposing the harness internals or operator configuration before we choose to.

Status: **design only.** Nothing in `README.md` / `documentation/` / `handbook/` /
cortex.eco references evals yet (same doctrine as `bench/` itself, see `.claude/bench.md`).
Actually running a publishable batch costs real LLM inference and is operator-triggered.

---

## What we would publish (and what stays private)

| Published | Stays private |
|---|---|
| Methodology: pipeline stages measured, question buckets, judge dimensions + prompts | Harness source (`bench/`), until we decide to open it |
| The **public corpus** (fixed document set, redistributable licenses) | `bench/files/` operator corpora |
| The **question bank** used (verbatim, per bucket) | Operator model registry (`models.yaml` with keys/base URLs) |
| Per-configuration results: Q+A judge scores, ERR, snappiness metrics, phase timings | Raw docker logs, `.env` snapshots, cost/account details |
| Cortex version + pinned model identities per run + judge model identity | Combo notes, internal heuristics wording |

## What the harness already measures that is publication-grade

1. **Q+A retrieval quality** (`qa_evaluator.py`): faithfulness / completeness /
   groundedness / conciseness (1–5) per (model-stack × mode × question), speed and
   deep-research arms. This is the headline table.
2. **Chat snappiness** (`run_qa_bench.py` / `qa_snappiness.py`): TTFT, total latency,
   tokens/sec, timeout/overthinking flags on a fixed graph. Secondary table.
3. **Ingestion robustness**: ERR (relationships per entity), verdicts, phase timings.
   Publish as supporting material, not the headline (it measures model stacks more
   than it measures Cortex).

## Gaps to close before numbers are publishable

1. **Public corpus.** `bench/files/` is operator-supplied and unredistributable.
   Curate ~15–25 documents with explicit redistribution rights (own docs — the Cortex
   handbook chapters are ideal dogfood — plus permissively-licensed technical material).
   Ship as `bench/public-corpus/` with a `MANIFEST.md` (source + license per file).
2. **Fixed, versioned question bank.** Today the bank is generated per batch from the
   operator's primary model. For public evals: generate once against the public corpus,
   **hand-review**, freeze as `bench/public-corpus/questions.v1.json`, and publish it.
   Regenerating per run would make results incomparable across time.
3. **Disinterested judge.** The judge currently runs on the operator's primary model —
   fine internally, circular for publication (a stack judging itself). Pin a strong
   third-party judge model, disclose its identity and the full judge prompt, and keep it
   fixed across a results generation. Add a `--judge-model` override to
   `qa_evaluator.judge_answers` config plumbing (small change, harness-internal).
4. **Statistical hygiene.** One run per configuration is an anecdote. Publishable rows
   need n ≥ 3 runs with mean ± spread on every judged dimension; flag any dimension
   where the spread swamps the differences.
5. **Reproducibility pinning.** Each published row must carry: Cortex version (git tag),
   the three tier models (exact IDs), reasoning-mode settings, embedding model
   (never rotated — by design), and the eval bundle version (corpus + bank).
6. **Sanitized export.** A `bench/export_public_results.py` that reads run JSONs and
   emits only the published fields (no base URLs, no keys, no operator notes, no
   docker-log fragments). The publication artifact is that JSON + a generated page.

## Publication surface (when ready)

- A static page (generated, like `build_dashboard.py` → data JS + HTML) published under
  docs.cortex.eco/evals or cortex.eco/evals — NOT the live bench dashboard, which stays
  private and operator-facing.
- The page shows: methodology summary, the corpus + bank download links, the results
  tables, and the reproduction recipe. Every number links to its pinned configuration.

## Runbook (operator steps, when we pull the trigger)

1. Curate `bench/public-corpus/` + `MANIFEST.md`; hand-review a generated question bank
   into `questions.v1.json` (~1 h).
2. Pick the configuration matrix worth publishing (start small: 3–5 model stacks that
   cover the recommended defaults + popular alternatives).
3. Per configuration: 3 runs of `run_bench.py` against the public corpus with the frozen
   bank, judge pinned to the disinterested model. (Budget: roughly a normal bench batch
   × runs × configs — real money; decide the matrix before starting.)
4. `export_public_results.py` → sanitized JSON → generate the page → review every free-text
   field manually before it goes anywhere public.
5. Publish page + corpus + bank; announce in the changelog. From then on, re-run per
   Cortex minor release or when a headline model changes.

## Non-goals

- Not a leaderboard of LLM vendors — it is "how well does Cortex retrieve/answer with
  stack X", framed around Cortex configurations.
- No external benchmark suites in v1 (LongMem/LoCoMo-style corpora bring licensing and
  comparability questions); our own corpus + disclosed methodology first.
- Does not open-source the harness — that is a separate later decision.
