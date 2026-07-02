# Cortex chat backend — integration spec for cortex-chat

**For:** Cortex Chat frontend team
**From:** Cortex backend (`cortex-app`)
**Date:** 2026-06-08
**Status:** shipped on the backend, ready to consume — all changes additive & backward-compatible

---

## TL;DR

Two things landed on `/api/ask/stream`, both **opt-in and backward-compatible** (omit the new field / ignore the new events → today's behavior unchanged):

1. **Streaming feedback — your report's asks A/B/C are implemented.** The endpoint now emits structured `status` events at each pipeline stage (including the previously-silent Chat mode), sends SSE `: ping` heartbeats during silent windows, and honors `stream_reasoning_steps`. Your `ThinkingIndicator` can now show **accurate** stage labels instead of heuristics.
2. **Conversation Memory — a new client-carried, multi-bucket memory.** Send an opaque `conversation_memory` blob; get an updated one back via a `memory_update` event; replay it next turn. This kills the "forgets after ~3 turns" problem, gives cross-turn **citation continuity** (stable `sid`), and adds a **fast-path** that answers memory-only follow-ups ("summarize that", "in German") without retrieval — much faster and cheaper.

Nothing here breaks the current client: every change is an additive JSON field or an SSE comment line, both already tolerated by your parser.

---

## Part 1 — Streaming feedback (your report A/B/C)

### A. `status` events — now emitted in every mode

A new additive field on the SSE JSON. Shape:

```jsonc
{ "status": { "stage": "searching", "message": "Searching the knowledge base" } }
```

`stage` is a stable machine key; `message` is human/i18n-friendly text. Stages currently emitted: `analyzing`, `searching`, `reranking`, `generating` (Chat/legacy path emits `searching → reranking → generating`; the agent path emits `analyzing → searching → generating`).

**Recommended consumption:** drive your `ThinkingIndicator` label from `status.message` when present, falling back to your current field-presence heuristic. You already ignore unknown fields, so this is safe to wire whenever you're ready.

### B. SSE heartbeats

During any silent window (≥ 8 s with no event — e.g. the long LLM calls in the researcher loop, or Chat-mode search+rerank), the backend now emits an SSE comment line:

```
: ping
```

Comment lines are ignored by the SSE spec and by your parser, so no handling is required — they exist purely to keep proxies/load-balancers from idle-timing-out and to confirm liveness. Every `StreamingResponse` is wrapped with this (`with_sse_heartbeat` in `main.py`).

### C. `stream_reasoning_steps`

Now wired: when `false`, the `status` (and reasoning) emissions are suppressed. Default `true`.

### Still true / unchanged

- `done` (and `error` on failure) remain **guaranteed** — keep finalizing the UI on them.
- Please keep sending `Accept-Encoding: identity` — uncompressed SSE is still required for real-time streaming.

### ⚠️ Amendment (2026-07): `done` is no longer necessarily the LAST frame

With `EMIT_DONE_BEFORE_MEMORY=true` (backend default since the v2 loop-efficiency pass), on memory-carrying turns the order is now:

```
…content tokens… → {"done": true, "pending_memory": true, …} → {"memory_update": {…}} → stream end
```

`done` fires as soon as the last answer token lands (finalize your UI there — that's the point: the 1–4s post-answer compaction no longer blocks the spinner), but the `memory_update` blob follows **after** it. Client obligations:

1. Keep reading the stream past `done` until it actually ends — do not `break`/`return` on `done`.
2. If you persist the conversation on `done`, persist **again** when the late `memory_update` arrives, or the stored session keeps the previous turn's blob.
3. `pending_memory: true` on the done frame tells you a `memory_update` is still coming; absent ⇒ the stream will just close.

Turns without a `conversation_memory` blob, and backends with `EMIT_DONE_BEFORE_MEMORY=false`, keep the legacy order (`memory_update` → `done`) — handle both.

---

## Part 2 — Conversation Memory

### The contract (three rules)

1. **Send** an opaque `conversation_memory` object on the `POST /api/ask/stream` body. Start with `{}` (or omit on turn 1).
2. **Read** the `memory_update` SSE event emitted near the end of every turn — it carries the full updated blob.
3. **Replay** that blob as `conversation_memory` on the next turn, and keep sending the **full** `conversation_history` (the blob indexes into it).

Treat the blob as **opaque** — store and replay exactly what the server returns; never construct or mutate it. Its shape will grow across phases.

```jsonc
// memory_update event (store client-side, send back as conversation_memory next turn)
{
  "memory_update": {
    "version": 3,
    "transcript": { "summary": "…rolling summary of older turns…", "summarized_count": 8 },
    "facts": ["Project codename: BlueFalcon", "Deadline: Friday"],
    "open_questions": [],
    "intent": "The user wants concise answers in German.",
    "source_ledger": [{ "sid": "s_7fd8dcf10785", "filename": "doc.pdf", "gist": "…" }],
    "kg_context": { "entities": [], "communities": [] }
  }
}
```

### What you get

- **No amnesia.** Older turns fold into a rolling summary + durable `facts` / `open_questions` / `intent`, rebuilt into a small fixed context each turn (lower cost and latency than re-feeding ever-growing history).
- **Citation continuity.** Every source object in the `sources` event now carries a conversation-stable **`sid`** (string), accumulated in `source_ledger`. Store the per-turn `src_N → sid` map so a follow-up ("expand on that source") refers to the same document across turns. Per-turn `[src_N]` numbering and your citation rendering are **unchanged** — `sid` is purely additive.
- **Memory fast-path.** Follow-ups answerable from memory alone ("summarize that", "why?", "translate to German") skip retrieval entirely → fast, cheap. You'll observe only `analyzing`/`generating` status and a `thinking` line ("Answering from conversation memory"), no `searching`/`sources`. Toggle backend-side with `ENABLE_MEMORY_FAST_PATH`.

Compaction (summary folding + bucket extraction) runs **after** the answer streams, on a cheap fast model, so it adds **no** user-visible latency before or during the answer.

### Migration is zero-risk

Ship the round-trip whenever you want. Until you do — no `conversation_memory` field → the backend behaves exactly as today (raw history truncation, no `memory_update`). `sid` and `status` already appear on the wire and are safely ignored by your current parser.

---

## Event reference (additions to your appendix)

| Field on the JSON object | When | Frontend action |
|---|---|---|
| `status` `{stage, message}` | all modes (if `stream_reasoning_steps`) | drive the live stage label; fall back to heuristic |
| `memory_update` `{…blob}` | when `conversation_memory` was sent — **may arrive after `done`** | store it; replay as `conversation_memory` next turn; re-persist if you already saved on `done` |
| `pending_memory: true` (on the `done` frame) | when a `memory_update` will still follow | keep reading the stream; expect one more event |
| `sid` (on each `sources[]` item) | any turn with sources | persist `src_N → sid` for cross-turn citation identity |
| `: ping` (SSE comment line) | silent windows ≥ 8 s | ignore (keep-alive) |

`done` (always) and `error` (on failure) remain guaranteed, but `done` is no longer necessarily the final frame — see the amendment above.

---

## Acceptance / how to validate

1. **Streaming feedback:** Chat mode shows a moving stage within ~500 ms of send; a `: ping` arrives at least every ~10 s during long turns; `ThinkingIndicator` labels match the backend `status.message`.
2. **Memory round-trip:** establish a fact in turn 1, ask for it again at turn 6 carrying the blob → correct recall (today: forgotten). Confirm `memory_update` arrives each turn and `summarized_count` advances.
3. **Citation continuity:** cite a source in turn 2, reference it in turn 5 → same `sid` in both `sources` events.
4. **Fast-path:** a memory-only follow-up returns with no `searching` status / no `sources` and a noticeably lower time-to-first-token.
5. **No-regression:** a client that ignores the new field/events (current build) behaves identically to today.

All changes are additive fields or SSE comment lines — nothing here is breaking.
