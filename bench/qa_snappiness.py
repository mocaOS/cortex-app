"""Q+A *chat* benchmark — snappiness (latency / overthinking) + answer quality.

Distinct from `qa_evaluator.py`, which is bolted onto the ingestion-focused
combo loop in `run_bench.py`. This module powers `run_qa_bench.py`, which holds
the knowledge graph FIXED and swaps only the answer-generation model
(`OPENAI_MODEL`) to compare candidates as the chat model.

What it measures, per (model × question), over the **streaming** chat path
(`POST /api/ask/stream`, `use_agentic=false` — the snappy graph-chat path):

- **ttft_ms** — time from request to the FIRST answer token (`{"content": ...}`
  event). This is the snappiness signal a user feels, and the overthinking
  proxy: a model that reasons forever delays its first visible token.
- **gen_ttft_ms** — time from the `generating` status event to first content,
  when the backend emits stage events (`STREAM_REASONING_STEPS=true`). Isolates
  pure model time-to-first-token from the constant retrieval/rerank prelude.
- **total_ms** — request to `{"done": true}` (or stream end).
- **tokens_per_sec**, **answer_chars** — output rate and verbosity.
- **status** — ok | over_budget | timeout | incomplete | error. `over_budget`
  means it completed but slower than the snappy-chat budget; `timeout` means the
  transport read timeout (hard cap) tripped — the deepseek-v4-flash failure mode.

Quality reuses `qa_evaluator.judge_answers` (faithfulness / completeness /
groundedness / conciseness, 1–5). The report ranks models on a speed×quality
blend and flags overthinkers (timeout-prone or far slower than the fleet).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Optional

import httpx


class _StreamError(RuntimeError):
    """Internal: an SSE `{"error": ...}` event was received mid-stream."""

sys.path.insert(0, str(Path(__file__).parent))
from _llm_io import chat_completion, parse_json_response  # noqa: E402
from cortex_client import CortexClient, CortexError  # noqa: E402


# ---------------------------------------------------------------------------
# Graph-sourced question generation
# ---------------------------------------------------------------------------

_GRAPH_QUESTION_SYSTEM = """You are designing an evaluation suite for a
knowledge-graph RAG system. You are given a SAMPLE of the entities and community
summaries the system has already extracted from its document corpus (you do NOT
see the source documents — only the graph). Use this sample to infer what the
corpus is about and compose questions a real user of this knowledge base would
ask.

Generate exactly the requested number of questions across three buckets:

- **factoid** — a specific, concrete answer likely living in one place (a name,
  date, definition, attribute, or relationship between two named entities).
- **synthesis** — requires combining several entities/relationships (multi-hop,
  comparison, "how does X relate to Y").
- **thematic** — a broad question about a recurring theme or the corpus's
  overall perspective, spanning many entities/communities.

Rules:
- Questions must be answerable from THIS knowledge base (ground them in the
  entity names and community themes shown). Do not require outside knowledge.
- Each question stands alone and is specific enough to answer without seeing
  "the above". Prefer questions that name real entities from the sample.
- Mix difficulty. Keep questions concise (one sentence).

Output strict JSON, no prose, no code fences:

{
  "questions": [
    {"id": "q01", "question": "...", "type": "factoid"},
    ...
  ]
}
`type` ∈ {"factoid","synthesis","thematic"}. Ids sequential: q01, q02, …
"""


async def _sample_graph(cx: CortexClient, *, entity_limit: int = 40,
                        community_limit: int = 12) -> dict:
    """Pull a compact sample of entities + community summaries from the graph."""
    ents_resp = await cx.list_entities(limit=entity_limit, skip=0)
    comms_resp = await cx.list_communities(limit=community_limit, skip=0)
    entities = ents_resp.get("entities") or ents_resp.get("items") or []
    communities = comms_resp.get("communities") or comms_resp.get("items") or []
    return {
        "entities": [
            {
                "name": e.get("name", ""),
                "type": e.get("type", e.get("entity_type", "")),
                "description": (e.get("description", "") or "")[:200],
            }
            for e in entities if e.get("name")
        ],
        "communities": [
            {
                "name": c.get("name", c.get("title", "")),
                "summary": (c.get("summary", "") or "")[:400],
            }
            for c in communities if (c.get("summary") or c.get("name"))
        ],
    }


def _normalise_questions(parsed: dict) -> list[dict]:
    questions = parsed.get("questions") if isinstance(parsed, dict) else None
    if not isinstance(questions, list) or not questions:
        raise RuntimeError(
            f"Question JSON missing/empty 'questions'. Got: "
            f"{list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}"
        )
    out = []
    for i, q in enumerate(questions, start=1):
        if not isinstance(q, dict) or not q.get("question"):
            continue
        out.append({
            "id": q.get("id") or f"q{i:02d}",
            "question": str(q["question"]).strip(),
            "type": q.get("type", "factoid"),
        })
    if not out:
        raise RuntimeError("No usable questions after normalisation.")
    return out


async def generate_question_bank_from_graph(
    cx: CortexClient,
    model_cfg: dict,
    *,
    count: int,
    cache_path: Path,
    timeout_s: float = 600.0,
) -> list[dict]:
    """Generate (or load cached) question bank from the live graph. ONE LLM call.

    Samples entities + community summaries via the API, then asks the operator's
    primary model to compose `count` questions. Cached at `cache_path`; reused
    on re-run so every model answers the IDENTICAL bank.
    """
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[qa] cached bank unreadable ({exc}); regenerating.", file=sys.stderr)

    sample = await _sample_graph(cx)
    if not sample["entities"]:
        raise RuntimeError(
            "Graph has no entities to sample — ingest a corpus before benchmarking Q+A."
        )

    bucket = max(count // 3, 1)
    user_msg = (
        f"Generate exactly {count} questions: ~{bucket} factoid, ~{bucket} "
        f"synthesis, ~{count - 2 * bucket} thematic. Knowledge-graph sample "
        f"follows.\n\n" + json.dumps(sample, indent=2)
    )
    text = await chat_completion(
        [
            {"role": "system", "content": _GRAPH_QUESTION_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        api_key=model_cfg["api_key"],
        base_url=model_cfg["base_url"],
        model=model_cfg["model"],
        max_tokens=4000,
        temperature=0.4,
        timeout_s=timeout_s,
    )
    bank = _normalise_questions(parse_json_response(text))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(bank, indent=2), encoding="utf-8")
    print(f"[qa] generated {len(bank)} questions from graph → {cache_path.name}",
          file=sys.stderr)
    return bank


# ---------------------------------------------------------------------------
# Streaming metric capture
# ---------------------------------------------------------------------------

def _approx_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Provider-agnostic, good enough
    for cross-model output-rate comparison."""
    return max(len(text) // 4, 0)


async def stream_question(
    cx: CortexClient,
    q: dict,
    *,
    budget_s: float,
    hard_cap_s: float,
    top_k: int = 5,
) -> dict:
    """Stream one question over the chat path and capture timing/snappiness.

    Never raises — transport/timeout/parse failures are recorded in `status` +
    `error`. Lets the stream run to `hard_cap_s` so an overthinker's true total
    latency is captured (flagged `over_budget`), only marking `timeout` when the
    transport read timeout actually trips.
    """
    rec: dict = {
        "question_id": q["id"],
        "question": q["question"],
        "type": q.get("type", ""),
        "answer": "",
        "answer_chars": 0,
        "approx_tokens": 0,
        "ttft_ms": None,
        "gen_ttft_ms": None,
        "total_ms": 0,
        "tokens_per_sec": 0.0,
        "sources_count": 0,
        "status": "ok",
        "error": None,
    }
    start = time.monotonic()
    parts: list[str] = []
    state = {"first_content_t": None, "generating_t": None, "done": False}
    err: Optional[str] = None
    timed_out = False

    async def _consume() -> None:
        # SSE heartbeats keep the transport alive, so the httpx read timeout
        # can't bound a silently-reasoning model — the wall-clock wait_for
        # below is the real cap. The httpx timeout is just a backstop.
        async for ev in cx.ask_stream_events(
            q["question"], use_agentic=False, top_k=top_k,
            use_graph=True, use_reranking=True, timeout_s=hard_cap_s + 15,
        ):
            now = time.monotonic()
            if "error" in ev:
                raise _StreamError(str(ev["error"])[:300])
            status = ev.get("status")
            if isinstance(status, dict) and status.get("stage") == "generating" \
                    and state["generating_t"] is None:
                state["generating_t"] = now
            srcs = ev.get("sources")
            if isinstance(srcs, list):
                rec["sources_count"] = len(srcs)
            content = ev.get("content")
            if content:
                if state["first_content_t"] is None:
                    state["first_content_t"] = now
                parts.append(content)
            if ev.get("done"):
                state["done"] = True
                return

    try:
        await asyncio.wait_for(_consume(), timeout=hard_cap_s)
    except asyncio.TimeoutError:
        timed_out = True  # overthinking → blew the wall-clock cap (partial kept)
    except _StreamError as exc:
        err = str(exc)
    except (CortexError, httpx.HTTPError) as exc:
        err = f"{type(exc).__name__}: {exc}"[:300]
    except Exception as exc:  # noqa: BLE001 — bench must never crash on one Q
        err = f"{type(exc).__name__}: {exc}"[:300]

    end = time.monotonic()
    first_content_t = state["first_content_t"]
    generating_t = state["generating_t"]
    done = state["done"]
    total_ms = int((end - start) * 1000)
    answer = "".join(parts)
    rec["answer"] = answer
    rec["answer_chars"] = len(answer)
    rec["approx_tokens"] = _approx_tokens(answer)
    rec["total_ms"] = total_ms
    if first_content_t is not None:
        rec["ttft_ms"] = int((first_content_t - start) * 1000)
        gen_secs = max(end - first_content_t, 1e-3)
        rec["tokens_per_sec"] = round(rec["approx_tokens"] / gen_secs, 1)
        if generating_t is not None:
            rec["gen_ttft_ms"] = int((first_content_t - generating_t) * 1000)

    if err:
        rec["status"] = "error"
        rec["error"] = err
    elif timed_out:
        rec["status"] = "timeout"
    elif not done:
        rec["status"] = "incomplete"
    elif total_ms > budget_s * 1000:
        rec["status"] = "over_budget"
    else:
        rec["status"] = "ok"
    return rec


async def run_snappiness_set(
    cx: CortexClient,
    questions: list[dict],
    *,
    budget_s: float,
    hard_cap_s: float,
    top_k: int = 5,
) -> list[dict]:
    """Stream every question sequentially (chat is one-at-a-time per user)."""
    out: list[dict] = []
    for q in questions:
        rec = await stream_question(
            cx, q, budget_s=budget_s, hard_cap_s=hard_cap_s, top_k=top_k
        )
        ttft = rec["ttft_ms"]
        print(
            f"[qa] {q['id']:>4} {rec['status']:>11}  "
            f"ttft={ttft if ttft is not None else '-':>6}ms  "
            f"total={rec['total_ms']:>6}ms  {rec['answer_chars']:>5} chars",
            file=sys.stderr,
        )
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return float(s[lo] + (s[hi] - s[lo]) * (k - lo))


_QUALITY_DIMS = ("faithfulness", "completeness", "groundedness", "conciseness")


def aggregate_model(model_id: str, context: int, answers: list[dict]) -> dict:
    """Reduce per-question records to one model summary row (pre-quality)."""
    n = len(answers)
    statuses = [a["status"] for a in answers]
    errors = sum(1 for s in statuses if s == "error")
    timeouts = sum(1 for s in statuses if s == "timeout")
    over_budget = sum(1 for s in statuses if s == "over_budget")
    incomplete = sum(1 for s in statuses if s == "incomplete")
    # Latency stats over answers that produced a first token.
    ttfts = [a["ttft_ms"] for a in answers if a["ttft_ms"] is not None]
    totals = [a["total_ms"] for a in answers if a["status"] in ("ok", "over_budget")]
    tok_rates = [a["tokens_per_sec"] for a in answers if a["tokens_per_sec"] > 0]
    chars = [a["answer_chars"] for a in answers if a["answer_chars"] > 0]
    return {
        "model": model_id,
        "context": context,
        "n": n,
        "errors": errors,
        "timeouts": timeouts,
        "over_budget": over_budget,
        "incomplete": incomplete,
        "timeout_rate": round(timeouts / n, 3) if n else 0.0,
        "ttft_p50_ms": int(_percentile(ttfts, 0.50)),
        "ttft_p95_ms": int(_percentile(ttfts, 0.95)),
        "ttft_mean_ms": int(sum(ttfts) / len(ttfts)) if ttfts else 0,
        "total_p50_ms": int(_percentile(totals, 0.50)),
        "total_p95_ms": int(_percentile(totals, 0.95)),
        "total_mean_ms": int(sum(totals) / len(totals)) if totals else 0,
        "tok_per_sec_mean": round(sum(tok_rates) / len(tok_rates), 1) if tok_rates else 0.0,
        "answer_chars_mean": int(sum(chars) / len(chars)) if chars else 0,
        # quality filled later by apply_quality()
        "quality": {d: 0.0 for d in _QUALITY_DIMS},
        "quality_overall": 0.0,
    }


def apply_quality(rows: list[dict], judge_output: dict) -> None:
    """Merge judge scores (speed mode) into each model row, in place."""
    by_run = (judge_output or {}).get("by_run", {}) or {}
    for row in rows:
        speed = (by_run.get(row["model"], {}) or {}).get("speed", {}) or {}
        scores = speed.get("scores", []) or []
        means: dict[str, float] = {}
        for dim in _QUALITY_DIMS:
            vals = [float(s[dim]) for s in scores
                    if isinstance(s.get(dim), (int, float)) and 1 <= s[dim] <= 5]
            means[dim] = round(sum(vals) / len(vals), 2) if vals else 0.0
        row["quality"] = means
        non_zero = [v for v in means.values() if v > 0]
        row["quality_overall"] = round(sum(non_zero) / len(non_zero), 2) if non_zero else 0.0
        row["quality_summary"] = speed.get("summary", "") or ""


def score_and_flag(rows: list[dict], *, budget_s: float) -> None:
    """Add speed_index, combined_score, and overthinking flags. In place.

    speed_index   = 100 × (fleet-fastest total_mean / this total_mean) — 100 = fastest.
    combined_score = 0.5·speed_index + 0.5·(quality_overall/5·100) − timeout penalty.
    overthinking   = any timeouts, OR ttft_p95 > 2× fleet median, OR total_mean
                     over the snappy-chat budget.
    """
    valid_totals = [r["total_mean_ms"] for r in rows if r["total_mean_ms"] > 0]
    fastest = min(valid_totals) if valid_totals else 1
    median_ttft_p95 = _percentile([r["ttft_p95_ms"] for r in rows if r["ttft_p95_ms"] > 0], 0.5) or 0
    for r in rows:
        # No completed answers (e.g. all timed out) → not "fast", it's unusable.
        tm = r["total_mean_ms"]
        r["speed_index"] = round(100 * fastest / tm, 1) if tm > 0 else 0.0
        quality_pct = (r["quality_overall"] / 5.0) * 100 if r["quality_overall"] else 0.0
        timeout_penalty = r["timeout_rate"] * 40  # up to −40 for all-timeout
        r["combined_score"] = round(
            0.5 * r["speed_index"] + 0.5 * quality_pct - timeout_penalty, 1
        )
        reasons = []
        if r["timeouts"]:
            reasons.append(f"{r['timeouts']}/{r['n']} timed out")
        if median_ttft_p95 and r["ttft_p95_ms"] > 2 * median_ttft_p95:
            reasons.append(f"p95 TTFT {r['ttft_p95_ms']}ms ≫ fleet median")
        if r["total_mean_ms"] > budget_s * 1000:
            reasons.append(f"mean total {r['total_mean_ms']}ms over {int(budget_s)}s budget")
        r["overthinking"] = bool(reasons)
        r["overthinking_reason"] = "; ".join(reasons)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report_md(
    rows: list[dict],
    *,
    batch_id: str,
    baseline_model: str,
    budget_s: float,
    hard_cap_s: float,
    questions_count: int,
    reasoning_mode: str,
) -> str:
    ranked = sorted(rows, key=lambda r: r["combined_score"], reverse=True)
    L: list[str] = []
    L.append(f"# Q+A chat benchmark — {batch_id}")
    L.append("")
    L.append(
        f"Path: `POST /api/ask/stream` (use_agentic=false). "
        f"{questions_count} questions · context pinned to "
        f"{rows[0]['context'] if rows else '?'} · snappy budget {int(budget_s)}s · "
        f"hard cap {int(hard_cap_s)}s · reasoning mode `{reasoning_mode}`."
    )
    L.append(f"Baseline (current): `{baseline_model}`.")
    L.append("")
    L.append("## Leaderboard (combined speed×quality)")
    L.append("")
    L.append("| # | Model | Score | Speed idx | Quality /5 | TTFT p50/p95 | Total p50/p95 | tok/s | Timeouts | Overthinks |")
    L.append("|---|-------|------:|----------:|-----------:|--------------|---------------|------:|---------:|:----------:|")
    for i, r in enumerate(ranked, 1):
        mark = "🚩" if r["overthinking"] else "—"
        base = " *(baseline)*" if r["model"] == baseline_model else ""
        L.append(
            f"| {i} | `{r['model']}`{base} | **{r['combined_score']}** | "
            f"{r['speed_index']} | {r['quality_overall']} | "
            f"{r['ttft_p50_ms']}/{r['ttft_p95_ms']}ms | "
            f"{r['total_p50_ms']}/{r['total_p95_ms']}ms | {r['tok_per_sec_mean']} | "
            f"{r['timeouts']}/{r['n']} | {mark} |"
        )
    L.append("")
    flagged = [r for r in ranked if r["overthinking"]]
    if flagged:
        L.append("## ⚠️ Overthinking / not-snappy")
        for r in flagged:
            L.append(f"- `{r['model']}` — {r['overthinking_reason']}.")
        L.append("")
    L.append("## Quality detail (1–5)")
    L.append("")
    L.append("| Model | Faithful | Complete | Grounded | Concise |")
    L.append("|-------|---------:|---------:|---------:|--------:|")
    for r in ranked:
        qd = r["quality"]
        L.append(
            f"| `{r['model']}` | {qd['faithfulness']} | {qd['completeness']} | "
            f"{qd['groundedness']} | {qd['conciseness']} |"
        )
    L.append("")
    L.append("## Per-model notes")
    for r in ranked:
        L.append(f"### `{r['model']}`")
        L.append(
            f"- {r['n']} Qs · {r['errors']} errors · {r['timeouts']} timeouts · "
            f"{r['over_budget']} over-budget · mean answer {r['answer_chars_mean']} chars."
        )
        if r.get("quality_summary"):
            L.append(f"- Judge: {r['quality_summary']}")
        L.append("")
    return "\n".join(L)
