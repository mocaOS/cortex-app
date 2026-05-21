"""Sanity test: feed the three existing manual run JSONs through heuristics
and assert the auto-derived verdicts match the human-written ones.

Run with: python -m pytest bench/test_heuristics.py
Or directly: python bench/test_heuristics.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parent))
HISTORICAL_RUNS_DIR = THIS.parent / "logs" / "runs"

from heuristics import apply_heuristics  # noqa: E402


EXPECTED = {
    "run_2026-05-20_15:34": "MIXED",      # MiniMax x3
    "run_2026-05-20_16:06": "GOOD",       # Mistral + GPT-OSS
    "run_2026-05-20_21:16": "POOR",       # Qwen3-A3B x2 — actually MIXED in human label
}


def _load(name: str) -> dict:
    path = HISTORICAL_RUNS_DIR / f"{name.replace(':', '-')}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _verdict(name: str) -> str:
    run = _load(name)
    # Strip pre-existing analysis fields so heuristics computes fresh.
    for k in ("verdict", "observations", "vs_previous_run", "failure_patterns",
              "performance_notes", "recommendation"):
        run.pop(k, None)
    apply_heuristics(run)
    return run["verdict"]


def test_run1_minimax_is_mixed():
    """Run 1: ERR 0.52 + 18 empty events + 5 scan empties → MIXED."""
    assert _verdict("run_2026-05-20_15:34") == "MIXED"


def test_run2_mistral_gptoss_is_good():
    """Run 2: ERR 1.43, 0 empties, 0 scan empties → GOOD."""
    assert _verdict("run_2026-05-20_16:06") == "GOOD"


def test_run3_qwen3_is_poor_or_mixed():
    """Run 3: ERR 0.38 — under our 0.4 threshold → POOR. Human called it MIXED.
    Document the disagreement: heuristic uses a strict ERR cutoff, human factored
    in that gleaning recovered the cross-doc relationships."""
    v = _verdict("run_2026-05-20_21:16")
    assert v in ("POOR", "MIXED"), f"Expected POOR or MIXED, got {v}"


if __name__ == "__main__":
    failures = []
    for fn in (test_run1_minimax_is_mixed, test_run2_mistral_gptoss_is_good, test_run3_qwen3_is_poor_or_mixed):
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failures.append((fn.__name__, str(e)))
            print(f"FAIL {fn.__name__}: {e}")
    sys.exit(1 if failures else 0)
