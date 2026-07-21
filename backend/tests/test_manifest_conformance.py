"""Cross-validator manifest conformance.

The corpus in cortex-registry (conformance/manifests.json) is the shared
floor of manifest rules enforced by three independent implementations: the
registry validator, the app template's validate.mjs, and THIS backend's
AppService.validate_manifest. Each repo's CI runs its own consumer, so a
rule change that reaches only one implementation fails the others.

Corpus resolution: $CONFORMANCE_CORPUS (path or URL) → the sibling
cortex-registry checkout → raw.githubusercontent.com. Skips (never fails)
when no source is reachable, so offline test runs stay green.
"""

import json
import os
from pathlib import Path

import pytest

from tests.test_apps import apps_env  # noqa: F401

CORPUS_URL = (
    "https://raw.githubusercontent.com/mocaOS/cortex-registry/main/conformance/manifests.json"
)


def _load_corpus() -> dict:
    override = os.environ.get("CONFORMANCE_CORPUS")
    if override and not override.startswith("http"):
        return json.loads(Path(override).read_text())
    sibling = Path(__file__).resolve().parents[3] / "cortex-registry" / "conformance" / "manifests.json"
    if not override and sibling.exists():
        return json.loads(sibling.read_text())
    import httpx

    try:
        response = httpx.get(override or CORPUS_URL, timeout=10.0, follow_redirects=True)
        response.raise_for_status()
        return response.json()
    except Exception as e:  # offline runs skip, CI (with network) enforces
        pytest.skip(f"conformance corpus unreachable: {e}")


def test_manifest_conformance(apps_env):  # noqa: F811
    corpus = _load_corpus()
    failures = []
    for case in corpus["cases"]:
        issues = apps_env.validate_manifest(case["manifest"])
        if case["valid"] and issues:
            failures.append(f"{case['name']}: expected VALID, got: {'; '.join(issues)}")
        elif not case["valid"]:
            if not issues:
                failures.append(f"{case['name']}: expected INVALID, no issues raised")
            elif case.get("mention") and case["mention"].lower() not in "\n".join(issues).lower():
                failures.append(
                    f"{case['name']}: issues do not mention {case['mention']!r}: {'; '.join(issues)}"
                )
    assert not failures, "\n".join(failures)
    assert len(corpus["cases"]) >= 10  # the corpus shrank? investigate, don't celebrate
