"""Rule-based qualitative analysis fields from raw run signals.

Maps a run dict (stats + log counts + timings) → the deterministic analysis
columns: verdict, failure_patterns, performance_notes, recommendation.

The free-form `observations` and `vs_previous_run` fields are NOT set here —
they're filled by the LLM review pass after all runs complete.
"""

from __future__ import annotations

from typing import Optional


def _safe_div(a: int, b: int) -> float:
    return a / b if b else 0.0


def compute_verdict(run: dict, *, errored: bool = False) -> str:
    """Map raw signals → verdict tag."""
    if errored:
        return "ERROR"
    if run.get("extraction_timeouts", 0) > 5:
        return "ERROR"

    err = float(run.get("err") or 0.0)
    empty_len = run.get("empty_content_length", 0)
    scan_empty = run.get("candidate_scan_empty", 0)

    if err >= 1.0 and empty_len == 0 and scan_empty == 0:
        return "GOOD"
    if err >= 0.4:
        return "MIXED"
    return "POOR"


def compute_failure_patterns(run: dict) -> str:
    """Compose a multi-line failure-pattern string from triggered signals."""
    patterns: list[str] = []
    total_calls = (
        run.get("doc_summaries_ok", 0)
        + run.get("entity_batches_ok", 0)
        + run.get("candidate_scans_ok", 0)
        + run.get("relationship_batches", 0)
        + run.get("communities_named", 0)
    ) or 1
    empty_len = run.get("empty_content_length", 0)
    if _safe_div(empty_len, total_calls) > 0.10:
        patterns.append(
            f"Token-burn pattern: {empty_len} calls exhausted max_tokens on "
            "suppressed-but-inlined reasoning (finish_reason=length, empty content)."
        )

    scans_ok = run.get("candidate_scans_ok", 0)
    zero_scans = run.get("zero_pair_scans", 0)
    if _safe_div(zero_scans, scans_ok) > 0.40:
        patterns.append(
            f"Format-adherence: {zero_scans} of {scans_ok} candidate scans returned "
            "0 parseable pairs (model responded but emitted unparseable output)."
        )

    per_chunk = run.get("per_chunk_relationships", 0)
    rels_total = run.get("relationships_total", 0)
    if 0 < per_chunk < 50 and rels_total > 0:
        patterns.append(
            f"Per-chunk collapse: only {per_chunk} per-chunk relationships stored "
            "(2000-token budget appears insufficient for this model)."
        )

    timeouts = run.get("extraction_timeouts", 0)
    if timeouts > 0:
        patterns.append(
            f"Transport timeouts: {timeouts} entity-extraction batch(es) exceeded "
            "the 120s HTTP client timeout."
        )

    parse_fail = run.get("community_parse_fallback", 0)
    if parse_fail > 1:
        patterns.append(
            f"JSON output instability: {parse_fail} community summaries fell back "
            "to regex/heuristic parsing."
        )

    gleaning = run.get("gleaning_passes", 0)
    if gleaning > 0:
        patterns.append(
            f"Gleaning rescue engaged: {gleaning} pass(es) recovered "
            f"{run.get('gleaning_pairs_total', 0)} additional pairs from low/zero-pair scans."
        )

    # Family-tagged hints — only fire when the matching failure also fired
    rel_family = run.get("relationship_family", "")
    if rel_family and (zero_scans > 0 or (0 < per_chunk < 50 and rels_total > 0)):
        if rel_family == "qwen":
            patterns.append(
                "Family hint (qwen): Qwen3-family models show known first-pass "
                "format-adherence variance under venice_parameters.disable_thinking. "
                "Gleaning safety net usually rescues, but the 2000-token per-chunk budget "
                "may be the dominant constraint — see reasoning_config.py for the dispatch."
            )
        elif rel_family == "minimax":
            patterns.append(
                "Family hint (minimax): MiniMax inlines <think> tokens into content despite "
                "venice_parameters.disable_thinking=true. AVOID for relationship-tier on Venice; "
                "see reasoning_config.py for the dispatch caveats."
            )
        elif rel_family == "openai_gpt_oss":
            patterns.append(
                "Family hint (openai_gpt_oss): gpt-oss does not support response_schema — "
                "XML must come via prompt only. Watch for output drift if pipeline ever "
                "switches to structured-output mode."
            )

    return "\n".join(patterns) if patterns else "No notable failure patterns."


def compute_performance_notes(run: dict) -> str:
    total = run.get("duration_total_sec") or 0
    pa = run.get("phase_a_sec") or 0
    pb = run.get("phase_b_sec") or 0
    p3 = run.get("step_3_sec") or 0
    cross_doc = run.get("cross_doc_relationships") or 0
    per_rel = pb / cross_doc if cross_doc else None

    lines = [
        f"Total wall time {total}s ({pa}s Phase A + {pb}s Phase B + {p3}s Step 3).",
    ]
    if per_rel is not None:
        lines.append(
            f"Phase B yielded {cross_doc} cross-doc relationships → "
            f"{per_rel:.1f}s per relationship."
        )
    if run.get("entity_batches_ok"):
        lines.append(
            f"Entity extraction: {run.get('entity_batches_ok')} batch(es), "
            f"{run.get('raw_entities_extracted', 0)} raw → "
            f"{run.get('entities', 0)} after dedup."
        )
    return " ".join(lines)


_RECOMMENDATION_TABLE = {
    ("GOOD", False): "PRODUCTION-READY for ingestion. Use as default stack.",
    ("GOOD", True): "PRODUCTION-READY with caveats — see failure_patterns.",
    ("MIXED", False): "ACCEPTABLE. Run further A/B before promoting.",
    ("MIXED", True): "ACCEPTABLE with significant caveats — see failure_patterns. Consider gleaning thresholds or budget tuning before adopting.",
    ("POOR", False): "AVOID for relationship tier. May be usable for entity-only extraction.",
    ("POOR", True): "AVOID entirely for ingestion at current configuration.",
    ("ERROR", False): "RUN ERRORED. Inspect issue_notes; pipeline did not complete.",
    ("ERROR", True): "RUN ERRORED. Inspect issue_notes; pipeline did not complete.",
    ("TIMED_OUT", False): "Wall-time cap exceeded; partial data only. Consider larger budget or different model class.",
    ("TIMED_OUT", True): "Wall-time cap exceeded; partial data only. Failure patterns observed before abort: see failure_patterns.",
}


def compute_recommendation(verdict: str, failure_patterns: str) -> str:
    has_failures = failure_patterns and failure_patterns != "No notable failure patterns."
    return _RECOMMENDATION_TABLE.get((verdict, has_failures), "No recommendation rule matched.")


def apply_heuristics(run: dict, *, errored: bool = False, timed_out: bool = False) -> dict:
    """Mutate-and-return: fill verdict / failure_patterns / performance_notes / recommendation.

    Leaves observations + vs_previous_run as placeholders for the LLM review pass.
    """
    if timed_out:
        verdict = "TIMED_OUT"
    else:
        verdict = compute_verdict(run, errored=errored)
    failure_patterns = compute_failure_patterns(run)
    performance_notes = compute_performance_notes(run)
    recommendation = compute_recommendation(verdict, failure_patterns)

    run.setdefault("observations", "<pending LLM review>")
    run.setdefault("vs_previous_run", "<pending LLM review>")
    run["verdict"] = verdict
    run["failure_patterns"] = failure_patterns
    run["performance_notes"] = performance_notes
    run["recommendation"] = recommendation
    return run
