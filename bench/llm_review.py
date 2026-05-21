"""End-of-batch review using the primary OpenAI-compatible model.

Calls the model configured via `OPENAI_MODEL` / `OPENAI_API_BASE` /
`OPENAI_API_KEY` in the live `.env` (read from the swapper's PRE-BATCH
backup so a combo-rewritten value doesn't leak through). In the default
Venice setup that's MiniMax-M27; on a self-hosted vLLM it might be
GPT-OSS-120B; anywhere with an OpenAI-compatible `/chat/completions`
endpoint works.

Pure `httpx` — no Anthropic SDK dependency. The bench harness already has
httpx for the Cortex client; we reuse it here.

Output contract (the model must return JSON matching this shape):

    {
      "runs": {
        "<run_id>": {
          "observations": "2-3 sentence paragraph",
          "vs_previous_run": "2-3 sentence comparison (empty for first run)"
        }, ...
      },
      "code_optimisation_findings": "<markdown string, 250-600 words>"
    }
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import httpx


_SYSTEM_PROMPT = """You are reviewing benchmark runs of Cortex, a knowledge-graph
ingestion pipeline. Each run feeds the same dataset through Phase A (entity
extraction), Phase B (cross-document relationship analysis), and Step 3
(community detection) under a different LLM-model combination.

Your job has three parts:

1. For each run, write a 2-3 sentence `observations` paragraph capturing what
   actually happened — entity yield, relationship yield, failure modes
   triggered (token-burn vs format-adherence vs timeouts vs JSON parse), and
   any notable signals. Be concrete; cite numbers from the run's stats.

2. For each run after the first, write a 2-3 sentence `vs_previous_run`
   paragraph comparing the current run to the one immediately before it in
   the batch (the one whose run_id sorts just before this one). Highlight
   deltas in entity count, relationship count, ERR, wall time, failure
   modes. For the first run, return an empty string for `vs_previous_run`.

3. Write a single top-level `code_optimisation_findings` markdown block
   (~250-600 words) that synthesises patterns across runs and suggests
   concrete code-level optimisations for the Cortex codebase. Examples:
   - Models that consistently trigger gleaning rescue → should the trigger
     threshold change?
   - Models that show per-chunk collapse → should the 2000-token budget
     scale by model class?
   - Models where reasoning suppression appears NOT to hold → does the
     reasoning_config.py dispatch need a model-specific override?
   - Transport-layer timeouts → does the 120s HTTP client timeout need
     bumping for some model classes?
   Reference file paths in the Cortex repo where relevant
   (backend/app/services/reasoning_config.py,
   backend/app/services/graph_extractor.py).

Output strictly as JSON:

{
  "runs": {
    "<run_id>": {
      "observations": "...",
      "vs_previous_run": "..."
    }, ...
  },
  "code_optimisation_findings": "<markdown string>"
}

No prose outside the JSON. No code fences around the JSON. If you must
"think" first, keep all thinking inside <think>...</think> tags so the
caller can strip them — the actual answer must still be the JSON object
described above.
"""

_KEEP_FIELDS = (
    "run_id", "primary_model", "extraction_model", "relationship_model",
    "extraction_reasoning_mode", "relationship_reasoning_mode",
    "duration_total_sec", "phase_a_sec", "phase_b_sec", "step_3_sec",
    "documents", "chunks", "entities", "relationships_total",
    "per_chunk_relationships", "cross_doc_relationships", "err", "communities",
    "raw_entities_extracted", "candidate_scans_ok", "candidate_scan_empty",
    "zero_pair_scans", "candidate_pairs_total", "gleaning_passes",
    "empty_content_length", "empty_content_stop", "extraction_timeouts",
    "community_parse_fallback", "verdict", "failure_patterns",
    "performance_notes", "recommendation",
)

_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>\s*", re.IGNORECASE)


def _compact(runs: list[dict]) -> list[dict]:
    """Drop fields the reviewer doesn't need to keep the prompt small."""
    return [{k: r.get(k) for k in _KEEP_FIELDS} for r in runs]


def _strip_thinking_and_fences(text: str) -> str:
    """Remove `<think>...</think>` blocks AND a wrapping ```json fence
    that a reasoning-style model may emit despite instructions."""
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    if cleaned.startswith("```"):
        # Drop the opening fence line (```json or just ```)
        first_nl = cleaned.find("\n")
        if first_nl >= 0:
            cleaned = cleaned[first_nl + 1:]
        # Drop the trailing fence
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


async def review_batch(
    runs: list[dict],
    *,
    api_key: str,
    base_url: str,
    model: str,
    log_windows: Optional[dict[str, str]] = None,
    max_output_tokens: int = 8000,
    temperature: float = 0.3,
    timeout_s: float = 600.0,
) -> dict:
    """Call the primary OpenAI-compatible model to fill observations + findings.

    Returns: dict with `runs` (map of run_id → {observations, vs_previous_run})
    and `code_optimisation_findings` (markdown string).

    Raises:
        RuntimeError if config is incomplete or the response can't be parsed.
    """
    if not (api_key and base_url and model):
        raise RuntimeError(
            "Primary model config incomplete — need OPENAI_API_KEY, "
            "OPENAI_API_BASE, and OPENAI_MODEL all set in .env."
        )

    user_payload: dict = {"runs": _compact(runs)}
    if log_windows:
        user_payload["log_tails"] = log_windows

    user_msg = (
        "Here is the benchmark batch to review. Return ONLY the JSON "
        "described in the system prompt — no prose outside it.\n\n"
        "```json\n"
        + json.dumps(user_payload, indent=2)
        + "\n```"
    )

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": temperature,
        "max_tokens": max_output_tokens,
        # Hint at JSON output. Providers that don't recognise this just ignore it
        # — and the 400-retry path below drops it if the server rejects.
        "response_format": {"type": "json_object"},
    }

    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async def _post(payload: dict) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            return await client.post(url, json=payload, headers=headers)

    try:
        r = await _post(body)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LLM review request failed: {exc}") from exc

    # Some providers (or models) reject `response_format`. Retry without it.
    if r.status_code == 400 and "response_format" in body:
        body.pop("response_format", None)
        try:
            r = await _post(body)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"LLM review retry failed: {exc}") from exc

    if r.status_code != 200:
        raise RuntimeError(
            f"LLM review HTTP {r.status_code} from {url}: {r.text[:500]}"
        )

    try:
        payload = r.json()
        text = payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"LLM review response malformed: {r.text[:500]}"
        ) from exc

    cleaned = _strip_thinking_and_fences(text)
    if not cleaned:
        raise RuntimeError(
            "LLM review returned empty content after stripping <think> blocks. "
            f"Raw (first 500 chars): {text[:500]}"
        )

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"LLM review did not return valid JSON ({exc}). "
            f"Cleaned response (first 500 chars): {cleaned[:500]}"
        ) from exc

    if "runs" not in parsed or "code_optimisation_findings" not in parsed:
        raise RuntimeError(
            f"LLM review JSON missing required keys. Got: {list(parsed.keys())}"
        )

    return parsed


def apply_review_to_runs(runs: list[dict], review: dict) -> list[dict]:
    """Merge review.observations / vs_previous_run back into each run dict."""
    by_id = review.get("runs", {}) or {}
    for run in runs:
        entry = by_id.get(run.get("run_id"))
        if not entry:
            continue
        run["observations"] = entry.get("observations", run.get("observations", ""))
        run["vs_previous_run"] = entry.get(
            "vs_previous_run", run.get("vs_previous_run", "")
        )
    return runs


def write_findings_md(findings: str, path: Path) -> None:
    """Write the cross-run findings markdown to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(findings or "", encoding="utf-8")
