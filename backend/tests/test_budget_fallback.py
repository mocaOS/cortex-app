"""Unit tests for the token / context budget fallback chain in Settings.

Chain (when raw field == 0):
    OPENAI_MAX_OUTPUT_TOKENS
        ↓
    EXTRACTION_MAX_OUTPUT_TOKENS
        ↓
    RELATIONSHIP_MAX_OUTPUT_TOKENS  (per-chunk + candidate scan)
        ↓
    VISION_MAX_OUTPUT_TOKENS

    RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS  ← standalone (Phase 2)

    OPENAI_MAX_CONTEXT
        ↓
    EXTRACTION_MAX_CONTEXT
        ↓
    RELATIONSHIP_MAX_CONTEXT

Tests import only `app.config` to stay free of the docling-heavy
`app.services` package; this keeps the suite runnable in the bare
Python env without the full backend toolchain installed.
"""

from __future__ import annotations

import pytest

from app.config import Settings, SettingsConfigDict


# Env vars the fallback chain reads. We clear all of these at the start of
# every test so the user's real .env / shell env can't leak in and shift
# the values out from under us.
_BUDGET_ENV_VARS = (
    "OPENAI_MAX_OUTPUT_TOKENS",
    "OPENAI_MAX_CONTEXT",
    "EXTRACTION_MAX_OUTPUT_TOKENS",
    "GRAPH_EXTRACTION_MAX_CONTEXT",  # canonical
    "EXTRACTION_MAX_CONTEXT",          # deprecated alias — still cleared in tests
    "RELATIONSHIP_MAX_OUTPUT_TOKENS",
    "RELATIONSHIP_MAX_CONTEXT",
    "RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS",
    "VISION_MAX_OUTPUT_TOKENS",
)


@pytest.fixture(autouse=True)
def _clear_budget_env(monkeypatch):
    """Strip budget env vars before each test so defaults apply."""
    for var in _BUDGET_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _fresh_settings(**field_overrides) -> Settings:
    """Build a Settings instance that ignores the on-disk .env file."""
    class TestSettings(Settings):
        model_config = SettingsConfigDict(
            env_file=None,
            case_sensitive=False,
            extra="ignore",
        )

    return TestSettings(**field_overrides)


# ---------------------------------------------------------------------------
# Default (all-zero raw fields) — everything resolves to primary
# ---------------------------------------------------------------------------

def test_defaults_resolve_to_primary():
    s = _fresh_settings()
    assert s.openai_max_output_tokens == 8000
    assert s.openai_max_context == 32768
    assert s.extraction_max_output_tokens == 8000
    assert s.relationship_max_output_tokens == 8000
    assert s.vision_max_output_tokens == 8000
    assert s.extraction_max_context == 32768
    assert s.relationship_max_context == 32768
    # Phase 2 batch is standalone — keeps its own 16000 default
    assert s.relationship_batch_max_output_tokens == 16000


# ---------------------------------------------------------------------------
# Primary bump — all sub-tiers inherit
# ---------------------------------------------------------------------------

def test_primary_bump_cascades_through_chain():
    s = _fresh_settings(openai_max_output_tokens=4000, openai_max_context=128000)
    assert s.extraction_max_output_tokens == 4000
    assert s.relationship_max_output_tokens == 4000
    assert s.vision_max_output_tokens == 4000
    assert s.extraction_max_context == 128000
    assert s.relationship_max_context == 128000
    # Standalone batch field stays at its own default
    assert s.relationship_batch_max_output_tokens == 16000


# ---------------------------------------------------------------------------
# Mid-chain override — downstream tiers inherit the override, not primary
# ---------------------------------------------------------------------------

def test_extraction_override_cascades_down():
    s = _fresh_settings(extraction_max_output_tokens_raw=12000)
    assert s.openai_max_output_tokens == 8000  # primary untouched
    assert s.extraction_max_output_tokens == 12000  # explicit
    assert s.relationship_max_output_tokens == 12000  # inherits extraction
    assert s.vision_max_output_tokens == 12000  # inherits relationship → extraction


def test_relationship_override_cascades_to_vision():
    s = _fresh_settings(
        extraction_max_output_tokens_raw=12000,
        relationship_max_output_tokens_raw=3500,
    )
    assert s.extraction_max_output_tokens == 12000
    assert s.relationship_max_output_tokens == 3500
    assert s.vision_max_output_tokens == 3500  # inherits relationship, not extraction


def test_vision_override_is_terminal():
    s = _fresh_settings(
        relationship_max_output_tokens_raw=3500,
        vision_max_output_tokens_raw=6000,
    )
    assert s.relationship_max_output_tokens == 3500
    assert s.vision_max_output_tokens == 6000  # explicit beats inheritance


# ---------------------------------------------------------------------------
# Full override — each tier honors its own value
# ---------------------------------------------------------------------------

def test_full_override_each_tier_independent():
    s = _fresh_settings(
        openai_max_output_tokens=1000,
        extraction_max_output_tokens_raw=2500,
        relationship_max_output_tokens_raw=3500,
        vision_max_output_tokens_raw=4500,
    )
    assert s.openai_max_output_tokens == 1000
    assert s.extraction_max_output_tokens == 2500
    assert s.relationship_max_output_tokens == 3500
    assert s.vision_max_output_tokens == 4500


# ---------------------------------------------------------------------------
# Phase 2 batch is NOT affected by chain changes — critical invariant
# ---------------------------------------------------------------------------

def test_phase2_batch_isolated_from_chain():
    """No chain value should bleed into the standalone Phase 2 budget."""
    s = _fresh_settings(
        openai_max_output_tokens=500,
        extraction_max_output_tokens_raw=1000,
        relationship_max_output_tokens_raw=2000,
    )
    # All three above are tiny — but Phase 2 still gets its own 16000
    assert s.relationship_batch_max_output_tokens == 16000


def test_phase2_batch_explicit_override():
    s = _fresh_settings(relationship_batch_max_output_tokens=24000)
    assert s.relationship_batch_max_output_tokens == 24000
    # Chain still operates independently
    assert s.relationship_max_output_tokens == 8000


# ---------------------------------------------------------------------------
# Context chain — identical structure
# ---------------------------------------------------------------------------

def test_context_chain_default():
    s = _fresh_settings()
    assert s.extraction_max_context == 32768
    assert s.relationship_max_context == 32768


def test_context_chain_extraction_override():
    s = _fresh_settings(graph_extraction_max_context_raw=65536)
    assert s.openai_max_context == 32768
    assert s.extraction_max_context == 65536
    assert s.relationship_max_context == 65536  # inherits extraction


def test_context_chain_relationship_override():
    s = _fresh_settings(
        graph_extraction_max_context_raw=65536,
        relationship_max_context_raw=131072,
    )
    assert s.extraction_max_context == 65536
    assert s.relationship_max_context == 131072  # explicit beats inheritance


# ---------------------------------------------------------------------------
# Legacy env-var aliases — old EXTRACTION_MAX_CONTEXT etc. still load
# ---------------------------------------------------------------------------

def test_graph_extraction_max_context_canonical_env(monkeypatch):
    """The new canonical name GRAPH_EXTRACTION_MAX_CONTEXT loads as expected."""
    monkeypatch.setenv("GRAPH_EXTRACTION_MAX_CONTEXT", "98765")
    s = _fresh_settings()
    assert s.extraction_max_context == 98765


def test_legacy_extraction_max_context_env_alias(monkeypatch):
    """Deprecated EXTRACTION_MAX_CONTEXT env var still maps to the same field
    for back-compat. A startup WARN nudges users to migrate (verified
    separately in test_legacy_extraction_max_context_emits_deprecation_warn)."""
    monkeypatch.setenv("EXTRACTION_MAX_CONTEXT", "98765")
    s = _fresh_settings()
    assert s.extraction_max_context == 98765


def test_canonical_name_wins_over_legacy_when_both_set(monkeypatch):
    """If a user has both names set (mid-migration), the canonical wins."""
    monkeypatch.setenv("GRAPH_EXTRACTION_MAX_CONTEXT", "111111")
    monkeypatch.setenv("EXTRACTION_MAX_CONTEXT", "222222")
    s = _fresh_settings()
    assert s.extraction_max_context == 111111


def test_legacy_extraction_max_context_emits_deprecation_warn(monkeypatch, caplog):
    """Using only the legacy name fires the one-shot deprecation WARN."""
    from app.config import _reset_deprecation_warnings_for_tests
    _reset_deprecation_warnings_for_tests()

    monkeypatch.setenv("EXTRACTION_MAX_CONTEXT", "65536")
    monkeypatch.delenv("GRAPH_EXTRACTION_MAX_CONTEXT", raising=False)

    import logging
    with caplog.at_level(logging.WARNING, logger="app.config"):
        _fresh_settings()

    msg = " ".join(r.message for r in caplog.records)
    assert "EXTRACTION_MAX_CONTEXT" in msg
    assert "GRAPH_EXTRACTION_MAX_CONTEXT" in msg
    assert "deprecated" in msg.lower()


def test_canonical_name_alone_does_not_fire_deprecation_warn(monkeypatch, caplog):
    """If only the new name is set, no deprecation WARN should fire."""
    from app.config import _reset_deprecation_warnings_for_tests
    _reset_deprecation_warnings_for_tests()

    monkeypatch.setenv("GRAPH_EXTRACTION_MAX_CONTEXT", "65536")
    monkeypatch.delenv("EXTRACTION_MAX_CONTEXT", raising=False)

    import logging
    with caplog.at_level(logging.WARNING, logger="app.config"):
        _fresh_settings()

    deprecation_msgs = [
        r for r in caplog.records
        if "EXTRACTION_MAX_CONTEXT" in r.message and "deprecated" in r.message.lower()
    ]
    assert not deprecation_msgs


def test_legacy_relationship_max_context_env_alias(monkeypatch):
    monkeypatch.setenv("RELATIONSHIP_MAX_CONTEXT", "200000")
    s = _fresh_settings()
    assert s.relationship_max_context == 200000


def test_legacy_relationship_max_output_tokens_now_drives_chained(monkeypatch):
    """RELATIONSHIP_MAX_OUTPUT_TOKENS env var now maps to the chained per-chunk
    field. Users who had this set to 16000 for Phase 2 will see per-chunk
    inherit 16000 (overkill but harmless); they should migrate to
    RELATIONSHIP_BATCH_MAX_OUTPUT_TOKENS to restore the original semantics.
    """
    monkeypatch.setenv("RELATIONSHIP_MAX_OUTPUT_TOKENS", "16000")
    s = _fresh_settings()
    assert s.relationship_max_output_tokens == 16000
    # Phase 2 batch still uses its standalone default
    assert s.relationship_batch_max_output_tokens == 16000


def test_new_extraction_max_output_tokens_env_alias(monkeypatch):
    monkeypatch.setenv("EXTRACTION_MAX_OUTPUT_TOKENS", "3500")
    s = _fresh_settings()
    assert s.extraction_max_output_tokens == 3500


def test_new_vision_max_output_tokens_env_alias(monkeypatch):
    monkeypatch.setenv("VISION_MAX_OUTPUT_TOKENS", "8192")
    s = _fresh_settings()
    assert s.vision_max_output_tokens == 8192


# ---------------------------------------------------------------------------
# Recommended-setup smoke: user sets only OPENAI_MODEL + GRAPH_EXTRACTION_MODEL
# ---------------------------------------------------------------------------

def test_recommended_minimal_setup_inherits_everything(monkeypatch):
    """Per the plan's recommended config: user sets only the two model names
    and expects budgets to flow through the chain from primary defaults.
    """
    # Clear the user's real model env vars so we observe pure defaults
    for var in (
        "OPENAI_MODEL", "GRAPH_EXTRACTION_MODEL",
        "RELATIONSHIP_EXTRACTION_MODEL", "VISION_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "MiniMaxAI/MiniMax-M2.7")
    monkeypatch.setenv("GRAPH_EXTRACTION_MODEL", "qwen/qwen3-7-27b")
    s = _fresh_settings()

    # Models honor user's explicit values
    assert s.openai_model == "MiniMaxAI/MiniMax-M2.7"
    assert s.extraction_model == "qwen/qwen3-7-27b"
    # Relationship + vision inherit the extraction model (string fallback)
    assert s.rel_extraction_model == "qwen/qwen3-7-27b"

    # All budgets fall through to the primary defaults
    assert s.extraction_max_output_tokens == 8000
    assert s.relationship_max_output_tokens == 8000
    assert s.vision_max_output_tokens == 8000
    assert s.relationship_batch_max_output_tokens == 16000
