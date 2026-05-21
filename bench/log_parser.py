"""Parse docker logs from a Cortex run into signal counts + phase timestamps.

Pure functions, no I/O — pass in the log text, get back a dict.

The patterns here mirror the ones the manual runs used. When graph_extractor.py
adds new log lines, extend the dispatcher in `parse_logs()`.
"""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from typing import Optional


_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3})")
_CANDIDATE_SCAN_RE = re.compile(r"Candidate scan: (\d+) candidate pairs from (\d+) entities")
_ENTITY_COMPLETE_RE = re.compile(r"Entity extraction complete: (\d+) raw -> (\d+) deduplicated")
_REL_DISCOVERED_RE = re.compile(r"Relationship analysis: discovered (\d+) relationships")
_GLEANING_RE = re.compile(r"Gleaning pass: \+(\d+) additional pairs")


def _parse_ts(line: str) -> Optional[datetime]:
    m = _TS_RE.match(line)
    if not m:
        return None
    return datetime.strptime(f"{m.group(1)}.{m.group(2)}000", "%Y-%m-%d %H:%M:%S.%f")


def parse_logs(log_text: str) -> dict:
    """Tally signal counts + phase timestamps from a stream of docker log lines."""
    counts: dict[str, int] = {
        "empty_content_length": 0,
        "empty_content_stop": 0,
        "candidate_scan_empty": 0,
        "candidate_scans_ok": 0,
        "candidate_pairs_total": 0,
        "zero_pair_scans": 0,
        "entity_batches_ok": 0,
        "raw_entities_extracted": 0,
        "relationship_batches": 0,
        "relationships_from_phase2": 0,
        "communities_named": 0,
        "community_parse_fallback": 0,
        "doc_summaries_ok": 0,
        "gleaning_passes": 0,
        "gleaning_pairs_total": 0,
        "per_chunk_retries": 0,
        "extraction_timeouts": 0,
    }

    phase_a_first: Optional[datetime] = None
    phase_a_last: Optional[datetime] = None
    phase_b_first: Optional[datetime] = None
    phase_b_last: Optional[datetime] = None
    step_3_first: Optional[datetime] = None
    step_3_last: Optional[datetime] = None

    for line in log_text.splitlines():
        ts = _parse_ts(line)

        if "LLM returned empty/None content" in line:
            if "finish_reason=length" in line:
                counts["empty_content_length"] += 1
            elif "finish_reason=stop" in line:
                counts["empty_content_stop"] += 1

        if "Candidate scan: LLM returned empty content" in line:
            counts["candidate_scan_empty"] += 1

        if "Error in entity extraction batch" in line and "timed out" in line:
            counts["extraction_timeouts"] += 1

        m = _CANDIDATE_SCAN_RE.search(line)
        if m:
            pairs = int(m.group(1))
            counts["candidate_scans_ok"] += 1
            counts["candidate_pairs_total"] += pairs
            if pairs == 0:
                counts["zero_pair_scans"] += 1
            if ts:
                phase_b_first = ts if phase_b_first is None else min(phase_b_first, ts)
                phase_b_last = ts if phase_b_last is None else max(phase_b_last, ts)

        m = _ENTITY_COMPLETE_RE.search(line)
        if m:
            counts["entity_batches_ok"] += 1
            counts["raw_entities_extracted"] += int(m.group(1))
            if ts:
                phase_a_first = ts if phase_a_first is None else min(phase_a_first, ts)
                phase_a_last = ts if phase_a_last is None else max(phase_a_last, ts)

        m = _REL_DISCOVERED_RE.search(line)
        if m:
            counts["relationship_batches"] += 1
            counts["relationships_from_phase2"] += int(m.group(1))
            if ts:
                phase_b_last = ts if phase_b_last is None else max(phase_b_last, ts)

        if "Generated community summary" in line:
            counts["communities_named"] += 1
            if ts:
                step_3_first = ts if step_3_first is None else min(step_3_first, ts)
                step_3_last = ts if step_3_last is None else max(step_3_last, ts)

        if "Could not parse community summary" in line:
            counts["community_parse_fallback"] += 1

        if "Generated document summary:" in line:
            counts["doc_summaries_ok"] += 1
            if ts:
                phase_a_first = ts if phase_a_first is None else min(phase_a_first, ts)

        m = _GLEANING_RE.search(line)
        if m:
            counts["gleaning_passes"] += 1
            counts["gleaning_pairs_total"] += int(m.group(1))

        if "Per-chunk extraction retry" in line:
            counts["per_chunk_retries"] += 1

    def _sec(a: Optional[datetime], b: Optional[datetime]) -> Optional[int]:
        if a is None or b is None:
            return None
        return int((b - a).total_seconds())

    timings = {
        "phase_a_first": phase_a_first.isoformat() if phase_a_first else None,
        "phase_a_last": phase_a_last.isoformat() if phase_a_last else None,
        "phase_b_first": phase_b_first.isoformat() if phase_b_first else None,
        "phase_b_last": phase_b_last.isoformat() if phase_b_last else None,
        "step_3_first": step_3_first.isoformat() if step_3_first else None,
        "step_3_last": step_3_last.isoformat() if step_3_last else None,
        "phase_a_sec": _sec(phase_a_first, phase_a_last),
        "phase_b_sec": _sec(phase_b_first, phase_b_last),
        "step_3_sec": _sec(step_3_first, step_3_last),
        "duration_total_sec": _sec(phase_a_first, step_3_last or phase_b_last or phase_a_last),
    }

    return {**counts, **timings}


def fetch_container_logs(
    container: str, since_iso: str, until_iso: Optional[str] = None
) -> str:
    """Run `docker logs <container> --since <ts> [--until <ts>]` and return stdout."""
    cmd = ["docker", "logs", container, "--since", since_iso]
    if until_iso:
        cmd += ["--until", until_iso]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    # docker logs writes to BOTH stdout (info) and stderr (warning/error).
    return (proc.stdout or "") + (proc.stderr or "")


# ---------------------------------------------------------------------------
# Recent-events tail — for the dashboard "Live now" feed
# ---------------------------------------------------------------------------

_FINISH_REASON_RE = re.compile(r"finish_reason=([a-z_]+)")


def _classify_event(line: str) -> Optional[tuple[str, str]]:
    """Return (kind, summary) for a bench-relevant docker log line, else None.

    Mirrors the patterns in `parse_logs()` but emits human-readable summaries
    instead of incrementing counters. Used by `tail_recent_events()` to build
    the dashboard's live "what just happened" feed.
    """
    m = _ENTITY_COMPLETE_RE.search(line)
    if m:
        return ("entity_batch", f"entity batch: {m.group(1)} raw → {m.group(2)} deduplicated")

    m = _REL_DISCOVERED_RE.search(line)
    if m:
        return ("rel_batch", f"phase-2 batch: discovered {m.group(1)} relationships")

    m = _CANDIDATE_SCAN_RE.search(line)
    if m:
        n_pairs = int(m.group(1))
        kind = "candidate_scan_zero" if n_pairs == 0 else "candidate_scan"
        return (kind, f"candidate scan: {m.group(1)} pairs from {m.group(2)} entities")

    m = _GLEANING_RE.search(line)
    if m:
        return ("gleaning", f"gleaning pass: +{m.group(1)} pairs")

    if "Generated document summary:" in line:
        sub = re.search(r"Generated document summary: (\d+) chars", line)
        return ("doc_summary", f"document summary: {sub.group(1) if sub else '?'} chars")

    if "Generated community summary" in line:
        return ("community", "community summary generated")

    if "Could not parse community summary" in line:
        return ("warning", "community summary JSON parse fallback engaged")

    if "Candidate scan: LLM returned empty content" in line:
        return ("warning", "candidate scan returned empty content")

    if "LLM returned empty/None content" in line:
        fr = _FINISH_REASON_RE.search(line)
        return ("warning", f"empty content (finish_reason={fr.group(1) if fr else '?'})")

    if "Error in entity extraction batch" in line:
        return ("error", "entity-extraction batch failed")

    if "Reasoning params rejected" in line:
        return ("warning", "model rejected reasoning params (runtime fallback engaged)")

    if "Per-chunk extraction retry" in line:
        return ("retry", "per-chunk extraction retry")

    return None


def tail_recent_events(
    container: str, since_iso: str, max_events: int = 30
) -> list[dict]:
    """Return the most recent N bench-relevant docker log events, newest first.

    Each item: ``{"ts": iso, "kind": str, "summary": str}``. Returns ``[]`` if
    the docker logs call fails (e.g. container not running) so the caller can
    proceed gracefully without aborting a batch.
    """
    try:
        log_text = fetch_container_logs(container, since_iso)
    except subprocess.CalledProcessError:
        return []
    events: list[dict] = []
    for line in log_text.splitlines():
        ts = _parse_ts(line)
        if ts is None:
            continue
        ev = _classify_event(line)
        if ev is None:
            continue
        events.append({"ts": ts.isoformat(), "kind": ev[0], "summary": ev[1]})
    events.reverse()  # newest first
    return events[:max_events]
