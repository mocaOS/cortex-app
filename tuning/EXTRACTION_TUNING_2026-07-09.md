# Extraction Tuning — Run Log & Insights (2026-07-09)

Goal: zero `output truncated` warnings + constant high-quality entity/relationship
flow, tuned via env, toward a production-safe config for the LAN self-host.

Stack: `local-instruct` → `gemma4-aeon-uncensored` @ litellm `192.168.68.57:4000`.
Extraction + vision both hit the same gemma GPU @ `192.168.68.78:8280` this run.

## Config under test
- `GRAPH_EXTRACTION_MAX_CONTEXT=16000`, `EXTRACTION_MAX_OUTPUT_TOKENS=12000`
- `VISION_MODEL=local-instruct` (on), `VISION_MAX_CONCURRENT=1`
- Clean-slate rebuild of 31 uploaded docs (`reprocess` / auto-resumed batch).

## Run summary (raw: `run_2026-07-09_vision1_16k.rawlog`, `run_2026-07-09_monitor.log`)
- **18 clean batch completions, 6 truncations** (~25% of calls) — 1 single-chunk
  truncation = tail entities permanently lost.
- Clean-batch decode: **median 55.9 tok/s** (49.9–69.9). ~half of the ~108 tok/s
  idle baseline → vision at concurrency 1 still shares the GPU.
- Clean-batch duration: **median 28.6s**. Truncated batches: **180–220s each,
  discarded then split** — the dominant time sink.

Truncated batches (chunks, input tokens, wasted decode):
```
31 chunks, in≈10318  -> 12k cap @180.7s
25 chunks, in≈10534  -> 12k cap @190.0s
30 chunks, in≈10054  -> 12k cap @220.5s
 5 chunks, in≈ 9179  -> 12k cap @202.3s
 2 chunks, in≈ 4338  -> 12k cap @181.5s   <-- SMOKING GUN
```

## Key insights

1. **Two independent levers — don't conflate them.**
   - *Contention* (slow decode): extraction + vision share one GPU. Fix =
     separate GPUs. vision=5 → 29 tok/s probe / ~5 tok/s in-batch; vision=1 →
     ~58 tok/s probe / ~56 tok/s in-batch. A dedicated GPU → ~108.
   - *Truncation* (output > 12k cap): independent of which GPU runs it. Fixed
     only by smaller batches (lower `GRAPH_EXTRACTION_MAX_CONTEXT`) or by
     handling density/degeneration.

2. **16k does NOT eliminate truncation.** Earlier "finish=stop" reads were
   nondeterministic luck. 6/24 batches truncated at 16k.

3. **Pure context tuning has a FLOOR — the 2-chunk/4338-token truncation.**
   A 2-chunk batch producing >12,000 output = ~2.8× input. That is not an
   oversized batch; it is entity-dense reference text and/or **model
   repetition-degeneration** (cf. the commit's "288 entities from 5 sentences"
   detector; this run also saw 3 chunks → 86 entities). Lowering context shrinks
   the 25–31-chunk truncations but will NOT stop 2-chunk overflows. Reaching
   *true* zero-truncation likely needs one of:
   - the learned-cap-on-truncation code fix (mirror the timeout-split learning;
     drafted + reverted 2026-07-09 to avoid side-effects), and/or
   - the degeneration guard tightened to abort before the 12k cap, and/or
   - a better-behaved extraction model.

## Decisions / next steps
- **Infra (in progress):** move non-vision extraction to a NEW dedicated GPU
  server (another gemma). Kills contention → clean batches ~15s, truncated ones
  ~110s instead of 180–220s. Vision stays on `.78`.
- **Env step-down (after GPU live):** `GRAPH_EXTRACTION_MAX_CONTEXT` 16k → 12k →
  10k until only the irreducible density/degeneration truncations remain, then
  decide on the code-side guard.

## Ops reference
- Admin auth: `X-API-Key: $ADMIN_API_KEY` (backend env).
- Wipe graph (keep docs+chunks+keys): `DELETE /api/graph/{communities,relationships,entities}`.
- Start generation: `POST /api/documents/reprocess?chain=relationship_analysis,community_detection` with all doc ids.
- Conversion is 1-at-a-time (`_conversion_semaphore=Semaphore(1)`); `VISION_MODEL` empty flips Docling to CPU OCR+picture-description (slow) — keep vision set for fast text-only conversion.

## Update: dedicated GPU + non-uncensored model (gemma4-aeon @ .126, 16k/12k)
Model swap did most of the work vs -uncensored:
- output/input ratio median **0.14×** (max 0.27×) vs -uncensored spiking >1–3×.
- Truncations: ~1/6 in first window vs 6/24 before. Clean batches ~27s @ ~50 tok/s (consistent, no contention).
- Remaining truncation: a 28-chunk/9226-input batch still hit 12k → 204.9s stall (worse on the slower .126 box).
- DECISION: let the full run finish at 16k to measure gemma4-aeon's true truncation rate on the dense docs before deciding 16k-vs-12k. Next step if it persists: 16k→12k (splits the 28-chunk batch, pulls output under cap).
