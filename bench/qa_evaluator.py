"""Q+A retrieval evaluation phase for the bench harness.

Adds a post-Step-3 evaluation layer to each combo: ask a fixed set of questions
against the ingested corpus and score the answers. The point is to measure
**retrieval quality** alongside ingestion quality — same corpus, same questions,
every combo scored on the same axes.

Three pieces, all driven from `run_bench.py`:

1. `generate_question_bank()` — ONE LLM call against the operator's primary
   model, BEFORE the per-combo loop, that reads `bench/files/*.md` and emits
   ~15 questions in three buckets (factoid / synthesis / thematic). Cached
   under `bench/logs/qa-bank-<batch_id>.json`. Re-runs of the same batch_id
   reuse the cached bank (idempotent).

2. `run_question_set()` — per-combo, per-mode (speed | quality). Posts each
   question to `/api/ask` and collects answers, sources, latency. Never
   raises — per-question errors are recorded and the run continues.

3. `judge_answers()` — ONE LLM call at end of batch that scores every
   (combo × mode × question) on faithfulness / completeness / groundedness /
   conciseness (1–5 each) and writes a 2-sentence per-(combo, mode) summary.
   `apply_qa_scores_to_runs()` then merges aggregates back into each run JSON.

The judge uses the same primary-model config as `llm_review.py` (read from
the pre-batch `.env` backup via `EnvSwapper.primary_model_config()`), NOT
whatever value the last combo wrote to the live `.env`.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Literal, Optional

# Local imports — keep relative to bench/ so the harness can run from anywhere
sys.path.insert(0, str(Path(__file__).parent))
from _llm_io import chat_completion, parse_json_response  # noqa: E402
from cortex_client import CortexClient, CortexError  # noqa: E402


# ---------------------------------------------------------------------------
# Question generation
# ---------------------------------------------------------------------------

_QUESTION_GEN_SYSTEM = """You are designing an evaluation suite for a knowledge-graph
RAG system that has ingested the markdown documents shown below.

Generate exactly the requested number of questions distributed across three
buckets:

- **factoid** — answerable from a single document. Concrete, specific
  (names, dates, definitions, attributions). The reader can verify by
  reading one document.
- **synthesis** — requires combining information from two or more documents
  (multi-hop, cross-doc reasoning, comparison between entities/works).
- **thematic** — broad concept or theme that spans many documents
  (overall arguments, recurring patterns, the corpus's perspective on X).

Rules:
- Questions must be answerable from the corpus alone — do not require outside
  knowledge.
- Each question stands alone (no "as mentioned above"); a researcher reading
  only the question must understand what's being asked.
- Mix difficulty within each bucket. Factoid questions should not all be
  trivial; synthesis questions should genuinely need multiple sources.
- Use the document filenames as anchors; you may reference them in
  `source_hint` to indicate where the answer lives.

Output strict JSON of this shape (no prose outside, no markdown fences):

{
  "questions": [
    {
      "id": "q01",
      "question": "What ...?",
      "type": "factoid",
      "source_hint": "filename.md"
    },
    ...
  ]
}

`type` ∈ {"factoid","synthesis","thematic"}.
`source_hint` is a single filename, the literal string "multiple", or a
short topic label. Keep ids sequential: q01, q02, …
"""


def _read_corpus_summaries(files_dir: Path, *, max_files: int = 30,
                            excerpt_bytes: int = 3000) -> list[dict]:
    """Return [{filename, excerpt}, ...] for every .md under files_dir.

    Excerpts are the first `excerpt_bytes` of each file. We send filenames +
    excerpts to the question-gen model so it has context to compose questions
    that are actually grounded in the corpus.
    """
    md_files = sorted(files_dir.glob("*.md"))[:max_files]
    out = []
    for fp in md_files:
        try:
            raw = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.append({
            "filename": fp.name,
            "excerpt": raw[:excerpt_bytes].strip(),
        })
    return out


async def generate_question_bank(
    files_dir: Path,
    model_cfg: dict,
    *,
    count: int = 15,
    cache_path: Path,
    timeout_s: float = 600.0,
) -> list[dict]:
    """Generate (or load cached) question bank. ONE LLM call.

    Reads every .md under `files_dir`, sends filenames + 3kB excerpts to the
    primary model, and asks for `count` questions split across factoid /
    synthesis / thematic buckets. Caches the resulting JSON at `cache_path`.

    If `cache_path` already exists, it is loaded and returned without an
    LLM call — re-running a batch with the same batch_id reuses the bank.

    Raises RuntimeError on malformed response.
    """
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"[qa] cached bank at {cache_path} unreadable ({exc}); regenerating.",
                file=sys.stderr,
            )

    corpus = _read_corpus_summaries(files_dir)
    if not corpus:
        raise RuntimeError(
            f"No .md files found under {files_dir} — cannot generate questions."
        )

    bucket_target = max(count // 3, 1)
    user_msg = (
        f"Generate exactly {count} questions for this corpus. Distribute as: "
        f"~{bucket_target} factoid, ~{bucket_target} synthesis, "
        f"~{count - 2 * bucket_target} thematic. Corpus follows.\n\n"
        + json.dumps(corpus, indent=2)
    )

    text = await chat_completion(
        [
            {"role": "system", "content": _QUESTION_GEN_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        api_key=model_cfg["api_key"],
        base_url=model_cfg["base_url"],
        model=model_cfg["model"],
        max_tokens=4000,
        temperature=0.4,
        timeout_s=timeout_s,
    )

    parsed = parse_json_response(text)
    questions = parsed.get("questions")
    if not isinstance(questions, list) or not questions:
        raise RuntimeError(
            f"Question bank JSON missing/empty 'questions' array. Got keys: "
            f"{list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}"
        )

    # Light normalisation: enforce required fields + sequential ids
    normalised = []
    for i, q in enumerate(questions, start=1):
        if not isinstance(q, dict) or not q.get("question"):
            continue
        normalised.append({
            "id": q.get("id") or f"q{i:02d}",
            "question": str(q["question"]).strip(),
            "type": q.get("type", "factoid"),
            "source_hint": q.get("source_hint", ""),
        })
    if not normalised:
        raise RuntimeError("Question bank had no usable entries after normalisation.")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(normalised, indent=2), encoding="utf-8")
    print(
        f"[qa] generated {len(normalised)} questions → "
        f"{cache_path.name}",
        file=sys.stderr,
    )
    return normalised


# ---------------------------------------------------------------------------
# Per-combo: ask the corpus
# ---------------------------------------------------------------------------

SPEED_TIMEOUT_S = 90.0
QUALITY_TIMEOUT_S = 300.0


async def run_question_set(
    cx: CortexClient,
    questions: list[dict],
    *,
    mode: Literal["speed", "quality"],
) -> list[dict]:
    """Ask every question via /api/ask in the given mode. Returns answer dicts.

    Never raises — per-question failures are captured in the `error` field of
    the answer record and the loop continues. Ingestion is already paid for;
    a single Q+A failure shouldn't poison the whole run.
    """
    timeout = SPEED_TIMEOUT_S if mode == "speed" else QUALITY_TIMEOUT_S
    use_agentic = (mode == "quality")
    results: list[dict] = []
    for q in questions:
        record: dict = {
            "question_id": q["id"],
            "question": q["question"],
            "type": q.get("type", ""),
            "mode": mode,
            "answer": "",
            "sources_count": 0,
            "source_filenames": [],
            "latency_ms": 0,
            "retrieval_stats": None,
            "error": None,
        }
        start = time.monotonic()
        try:
            resp = await cx.ask(
                q["question"],
                use_agentic=use_agentic,
                top_k=5,
                use_graph=True,
                use_reranking=True,
                timeout_s=timeout,
            )
            record["answer"] = resp.get("answer", "") or ""
            sources = resp.get("sources") or []
            record["sources_count"] = len(sources)
            record["source_filenames"] = [
                (s.get("metadata") or {}).get("filename", "")
                for s in sources[:3]
            ]
            record["retrieval_stats"] = resp.get("retrieval_stats")
        except (CortexError, asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
            record["error"] = f"{type(exc).__name__}: {exc}"[:300]
        record["latency_ms"] = int((time.monotonic() - start) * 1000)
        results.append(record)
        print(
            f"[qa:{mode}] {q['id']} {'ERR' if record['error'] else 'OK'} "
            f"({record['latency_ms']}ms, {record['sources_count']} src)",
            file=sys.stderr,
        )
    return results


def summarise_answers(answers: list[dict]) -> dict:
    """Reduce a list of answer records to the per-mode aggregate fields.

    Returns {answered, errors, avg_latency_ms} — the score fields are filled
    by the post-batch judge pass, not here.
    """
    if not answers:
        return {"answered": 0, "errors": 0, "avg_latency_ms": 0}
    errors = sum(1 for a in answers if a.get("error"))
    answered = len(answers) - errors
    latencies = [a.get("latency_ms", 0) for a in answers if not a.get("error")]
    avg_lat = int(sum(latencies) / len(latencies)) if latencies else 0
    return {
        "answered": answered,
        "errors": errors,
        "avg_latency_ms": avg_lat,
    }


def write_qa_run(
    run_id: str,
    *,
    questions: list[dict],
    speed_answers: list[dict],
    quality_answers: list[dict],
    qa_runs_dir: Path,
) -> Path:
    """Persist per-combo raw Q+A data to bench/logs/qa-runs/<run_id>.json."""
    qa_runs_dir.mkdir(parents=True, exist_ok=True)
    out_path = qa_runs_dir / f"{run_id}.json"
    payload = {
        "run_id": run_id,
        "questions": questions,
        "speed": speed_answers,
        "quality": quality_answers,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# End-of-batch judge
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM = """You are scoring retrieval-augmented answers produced by a
knowledge-graph RAG system. For each (question, answer) pair, score the answer
on FOUR dimensions, integer 1–5:

- **faithfulness** — claims in the answer are supported by the corpus the
  system has access to (no hallucinations, no invented entities/dates).
- **completeness** — covers the substantive parts of what the question asks;
  doesn't skip the second half of multi-part questions.
- **groundedness** — answer cites or implies specific sources; the reader can
  trace claims back. Bare assertions without anchors score lower.
- **conciseness** — appropriate length for the question. Verbose padding
  lowers the score; so does under-answering.

If an answer is empty or errored (`error` field set), score 1 on every
dimension and set `notes` to a one-line reason.

For each `<run_id>.<mode>` combination, also write a `summary` (2 sentences)
capturing how that combo handled the question set in that mode — strengths,
weaknesses, recurring failure modes.

Output strict JSON of this shape (no prose outside):

{
  "by_run": {
    "<run_id>": {
      "speed": {
        "summary": "...",
        "scores": [
          {
            "question_id": "q01",
            "faithfulness": 4,
            "completeness": 5,
            "groundedness": 3,
            "conciseness": 4,
            "notes": ""
          }, ...
        ]
      },
      "quality": { ... same shape ... }
    }, ...
  }
}

Every run_id present in the input must appear in `by_run`. If a mode has no
answers (empty list), include the mode with `scores: []` and a `summary`
saying so. No code fences. Keep `<think>...</think>` separate from the JSON.
"""

_JUDGE_DIMENSIONS = ("faithfulness", "completeness", "groundedness", "conciseness")


def _compact_judge_input(
    qa_by_run: dict[str, dict],
    questions: list[dict],
) -> dict:
    """Build the user-message payload for the judge call.

    Strip everything the judge doesn't need (retrieval_stats internals,
    source_filenames beyond the first one) so token usage stays manageable.
    """
    compact_runs = {}
    for run_id, modes in qa_by_run.items():
        compact_runs[run_id] = {
            "speed": [
                {
                    "question_id": a["question_id"],
                    "answer": a.get("answer", "")[:4000],
                    "sources_count": a.get("sources_count", 0),
                    "error": a.get("error"),
                }
                for a in modes.get("speed", [])
            ],
            "quality": [
                {
                    "question_id": a["question_id"],
                    "answer": a.get("answer", "")[:4000],
                    "sources_count": a.get("sources_count", 0),
                    "error": a.get("error"),
                }
                for a in modes.get("quality", [])
            ],
        }
    return {
        "questions": [
            {"id": q["id"], "question": q["question"], "type": q.get("type", "")}
            for q in questions
        ],
        "answers_by_run": compact_runs,
    }


async def judge_answers(
    qa_by_run: dict[str, dict],
    questions: list[dict],
    model_cfg: dict,
    *,
    timeout_s: float = 900.0,
) -> dict:
    """One LLM call that scores every answer in `qa_by_run`.

    `qa_by_run[run_id]` must have keys `"speed"` and `"quality"` (each a list
    of answer dicts as produced by `run_question_set`).

    Returns the parsed judge payload `{"by_run": {run_id: {speed: {...},
    quality: {...}}}}`. Raises RuntimeError if the response is malformed or
    missing the `by_run` key.
    """
    if not qa_by_run:
        return {"by_run": {}}

    user_payload = _compact_judge_input(qa_by_run, questions)
    user_msg = (
        "Score the following answer set. Return ONLY the JSON described in "
        "the system prompt — no prose outside it.\n\n"
        "```json\n"
        + json.dumps(user_payload, indent=2)
        + "\n```"
    )

    text = await chat_completion(
        [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        api_key=model_cfg["api_key"],
        base_url=model_cfg["base_url"],
        model=model_cfg["model"],
        max_tokens=8000,
        temperature=0.2,
        timeout_s=timeout_s,
    )

    parsed = parse_json_response(text)
    if "by_run" not in parsed:
        raise RuntimeError(
            f"Judge response missing 'by_run' key. Got: {list(parsed.keys())}"
        )
    return parsed


def _aggregate_scores(scores: list[dict]) -> dict[str, float]:
    """Return {dimension: mean} across a list of per-question score dicts."""
    out: dict[str, float] = {}
    if not scores:
        return {d: 0.0 for d in _JUDGE_DIMENSIONS}
    for dim in _JUDGE_DIMENSIONS:
        vals = []
        for s in scores:
            v = s.get(dim)
            if isinstance(v, (int, float)) and 1 <= v <= 5:
                vals.append(float(v))
        out[dim] = round(sum(vals) / len(vals), 2) if vals else 0.0
    return out


def apply_qa_scores_to_runs(
    runs: list[dict],
    qa_by_run: dict[str, dict],
    judge_output: dict,
    *,
    questions_count: int,
) -> list[dict]:
    """Merge Q+A aggregates into each run dict. In-place; returns the list.

    Fields added to every run (even those with no Q+A data — populated with
    zeros so the .ods columns are always present):

      qa_questions_count
      qa_speed_answered, qa_speed_errors, qa_speed_avg_latency_ms
      qa_speed_faithfulness, qa_speed_completeness,
      qa_speed_groundedness, qa_speed_conciseness, qa_speed_summary
      qa_quality_* (same shape)
    """
    by_run = (judge_output or {}).get("by_run", {}) or {}
    for run in runs:
        run_id = run.get("run_id", "")
        modes = qa_by_run.get(run_id, {}) or {}
        judge_for_run = by_run.get(run_id, {}) or {}

        run["qa_questions_count"] = questions_count

        for mode in ("speed", "quality"):
            answers = modes.get(mode, []) or []
            agg = summarise_answers(answers)
            run[f"qa_{mode}_answered"] = agg["answered"]
            run[f"qa_{mode}_errors"] = agg["errors"]
            run[f"qa_{mode}_avg_latency_ms"] = agg["avg_latency_ms"]

            mode_judge = judge_for_run.get(mode, {}) or {}
            scores = mode_judge.get("scores", []) or []
            dim_means = _aggregate_scores(scores)
            for dim in _JUDGE_DIMENSIONS:
                run[f"qa_{mode}_{dim}"] = dim_means.get(dim, 0.0)
            run[f"qa_{mode}_summary"] = mode_judge.get("summary", "") or ""
    return runs


def write_qa_findings_md(
    runs: list[dict],
    path: Path,
) -> None:
    """Cross-run Q+A summary markdown — highest-scoring combo per dimension.

    Mirrors the existing `findings_<batch_id>.md` pattern but focused on
    retrieval quality. Best-effort: silently skips if no qa_* fields present.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not runs or not any("qa_questions_count" in r for r in runs):
        path.write_text("# Q+A findings\n\nNo Q+A data captured.\n", encoding="utf-8")
        return

    def _best(field: str) -> tuple[str, float]:
        best_run = max(runs, key=lambda r: r.get(field, 0.0) or 0.0)
        return best_run.get("combo_id", "?"), float(best_run.get(field, 0.0) or 0.0)

    lines: list[str] = ["# Q+A findings", ""]
    lines.append(
        f"Question bank size: {runs[0].get('qa_questions_count', 0)}.  "
        f"Runs evaluated: {len(runs)}."
    )
    lines.append("")
    lines.append("## Top combo per dimension (speed mode)")
    for dim in _JUDGE_DIMENSIONS:
        combo, score = _best(f"qa_speed_{dim}")
        lines.append(f"- **{dim}**: `{combo}` ({score:.2f}/5)")
    lines.append("")
    lines.append("## Top combo per dimension (quality / deep research mode)")
    for dim in _JUDGE_DIMENSIONS:
        combo, score = _best(f"qa_quality_{dim}")
        lines.append(f"- **{dim}**: `{combo}` ({score:.2f}/5)")
    lines.append("")
    lines.append("## Per-run summaries")
    for run in runs:
        rid = run.get("run_id", "?")
        lines.append(f"### `{rid}`")
        s_lat = run.get("qa_speed_avg_latency_ms", 0)
        q_lat = run.get("qa_quality_avg_latency_ms", 0)
        s_err = run.get("qa_speed_errors", 0)
        q_err = run.get("qa_quality_errors", 0)
        lines.append(
            f"- Speed: {s_lat}ms avg latency, {s_err} errors. "
            + (run.get("qa_speed_summary") or "")
        )
        lines.append(
            f"- Quality: {q_lat}ms avg latency, {q_err} errors. "
            + (run.get("qa_quality_summary") or "")
        )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _read_env_primary_config(env_path: Path) -> dict:
    """Minimal .env reader for the self-test entrypoint."""
    cfg = {"model": "", "base_url": "", "api_key": ""}
    if not env_path.exists():
        return cfg
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip().strip('"').strip("'")
        if k == "OPENAI_MODEL":
            cfg["model"] = v
        elif k == "OPENAI_API_BASE":
            cfg["base_url"] = v
        elif k == "OPENAI_API_KEY":
            cfg["api_key"] = v
    return cfg


async def _self_test() -> int:
    """Generate a question bank against bench/files/ and print it.

    Useful for verifying the LLM call works without running the full bench.
    Reads OPENAI_* from the project root `.env`.
    """
    bench_dir = Path(__file__).parent
    repo_root = bench_dir.parent
    files_dir = bench_dir / "files"
    cfg = _read_env_primary_config(repo_root / ".env")
    if not (cfg["model"] and cfg["base_url"] and cfg["api_key"]):
        print(
            "[self-test] OPENAI_MODEL / OPENAI_API_BASE / OPENAI_API_KEY "
            "must all be set in .env.",
            file=sys.stderr,
        )
        return 2
    cache = bench_dir / "logs" / "qa-bank-selftest.json"
    if cache.exists():
        cache.unlink()
    bank = await generate_question_bank(files_dir, cfg, count=6, cache_path=cache)
    print(json.dumps(bank, indent=2))
    return 0


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--self-test", action="store_true",
                    help="Generate a small question bank against bench/files/ and print it.")
    args = p.parse_args()
    if args.self_test:
        return asyncio.run(_self_test())
    p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
