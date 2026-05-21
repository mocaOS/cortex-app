"""Unit tests for app.services.reasoning_config.

Covers:
- ReasoningMode parsing.
- parse_model_family regex dispatch (incl. forward-compat: gpt-5.8, gpt-6).
- classify_backend by base_url.
- build_reasoning_kwargs per-backend dispatch matrix.
- parse_overrides syntax.
- merge_kwargs deep-merge.
- safe_chat_completion (async) and safe_chat_completion_sync runtime fallback.
- Override precedence over heuristics.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.reasoning_config import (
    ModelFamily,
    ReasoningMode,
    _reset_caches_for_tests,
    build_reasoning_kwargs,
    classify_backend,
    is_unsupported_reasoning_error,
    merge_kwargs,
    parse_model_family,
    parse_overrides,
    safe_chat_completion,
    safe_chat_completion_sync,
)


@pytest.fixture(autouse=True)
def _reset_module_state():
    _reset_caches_for_tests()
    yield
    _reset_caches_for_tests()


# ---------------------------------------------------------------------------
# ReasoningMode.parse
# ---------------------------------------------------------------------------

def test_reasoning_mode_parse_known():
    assert ReasoningMode.parse("off") == ReasoningMode.OFF
    assert ReasoningMode.parse("MINIMAL") == ReasoningMode.MINIMAL
    assert ReasoningMode.parse("  auto  ") == ReasoningMode.AUTO
    assert ReasoningMode.parse("HIGH") == ReasoningMode.HIGH


def test_reasoning_mode_parse_unknown_falls_back_to_auto():
    assert ReasoningMode.parse("ultra") == ReasoningMode.AUTO
    assert ReasoningMode.parse("") == ReasoningMode.AUTO
    assert ReasoningMode.parse(None) == ReasoningMode.AUTO  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_model_family
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model,expected",
    [
        ("gpt-5", ModelFamily.OPENAI_GPT5_V0),
        ("gpt-5-mini", ModelFamily.OPENAI_GPT5_V0),
        ("gpt-5-nano", ModelFamily.OPENAI_GPT5_V0),
        ("gpt-5.1", ModelFamily.OPENAI_GPT5_V1PLUS),
        ("gpt-5.2", ModelFamily.OPENAI_GPT5_V1PLUS),
        ("gpt-5.8", ModelFamily.OPENAI_GPT5_V1PLUS),  # forward-compat
        ("gpt-5.27", ModelFamily.OPENAI_GPT5_V1PLUS),
        ("gpt-5-pro", ModelFamily.OPENAI_GPT5_PRO),
        ("gpt-5-codex", ModelFamily.OPENAI_GPT5_CODEX),
        ("gpt-5.1-codex", ModelFamily.OPENAI_GPT5_CODEX),
        ("gpt-6", ModelFamily.OPENAI_UNKNOWN_MAJOR),
        ("gpt-7-mini", ModelFamily.OPENAI_UNKNOWN_MAJOR),
        ("o1", ModelFamily.OPENAI_O_SERIES),
        ("o3-mini", ModelFamily.OPENAI_O_SERIES),
        ("o4-mini", ModelFamily.OPENAI_O_SERIES),
        ("claude-opus-4-7", ModelFamily.ANTHROPIC_OPUS_47_PLUS),
        ("claude-opus-4-8", ModelFamily.ANTHROPIC_OPUS_47_PLUS),
        ("claude-opus-5-0", ModelFamily.ANTHROPIC_OPUS_47_PLUS),
        ("claude-opus-4-6", ModelFamily.ANTHROPIC_OTHER),
        ("claude-sonnet-4-6", ModelFamily.ANTHROPIC_OTHER),
        ("claude-haiku-4-5", ModelFamily.ANTHROPIC_OTHER),
        ("qwen3-32b", ModelFamily.QWEN_THINKING),
        ("qwen-3-235b", ModelFamily.QWEN_THINKING),
        ("Qwen3-VL-Holo2", ModelFamily.QWEN_THINKING),  # qwen3 wins over holo
        ("holo2-7b", ModelFamily.HOLO),
        ("deepseek-v3", ModelFamily.DEEPSEEK_V3),
        ("deepseek_v3-base", ModelFamily.DEEPSEEK_V3),
        ("granite-3.2", ModelFamily.GRANITE),
        ("mistral-small-24b", ModelFamily.UNKNOWN),
        ("openai/minimax-m21", ModelFamily.UNKNOWN),
        ("", ModelFamily.UNKNOWN),
    ],
)
def test_parse_model_family(model, expected):
    assert parse_model_family(model) == expected


# ---------------------------------------------------------------------------
# classify_backend
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("https://api.openai.com/v1", "openai"),
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("https://api.venice.ai/api/v1", "venice"),
        ("https://api.anthropic.com/v1", "anthropic_native"),
        ("http://localhost:8000/v1", "vllm_or_compatible"),
        ("https://compute3.example.com/v1", "vllm_or_compatible"),
        ("", "vllm_or_compatible"),
        (None, "vllm_or_compatible"),
    ],
)
def test_classify_backend(base_url, expected):
    assert classify_backend(base_url, "any-model") == expected


# ---------------------------------------------------------------------------
# build_reasoning_kwargs — per-backend dispatch
# ---------------------------------------------------------------------------

def test_auto_mode_returns_empty_for_all_backends():
    for base_url in [
        "https://api.openai.com/v1",
        "https://openrouter.ai/api/v1",
        "https://api.venice.ai/api/v1",
        "https://api.anthropic.com/v1",
        "https://compute3.example.com/v1",
    ]:
        assert build_reasoning_kwargs(base_url, "any-model", ReasoningMode.AUTO) == {}


def test_openai_gpt5_v0_off_returns_minimal():
    kwargs = build_reasoning_kwargs(
        "https://api.openai.com/v1", "gpt-5-mini", ReasoningMode.OFF
    )
    assert kwargs == {"reasoning_effort": "minimal"}


def test_openai_gpt5_v1plus_off_returns_none():
    kwargs = build_reasoning_kwargs(
        "https://api.openai.com/v1", "gpt-5.1", ReasoningMode.OFF
    )
    assert kwargs == {"reasoning_effort": "none"}


def test_openai_gpt5_8_forward_compat():
    """gpt-5.8 (future release) should route like 5.1+ → 'none'."""
    kwargs = build_reasoning_kwargs(
        "https://api.openai.com/v1", "gpt-5.8", ReasoningMode.OFF
    )
    assert kwargs == {"reasoning_effort": "none"}


def test_openai_gpt6_optimistic_none():
    """Unknown major (gpt-6+) gets optimistic 'none'; runtime fallback will retry."""
    kwargs = build_reasoning_kwargs(
        "https://api.openai.com/v1", "gpt-6", ReasoningMode.OFF
    )
    assert kwargs == {"reasoning_effort": "none"}


def test_openai_o_series_off_returns_low():
    kwargs = build_reasoning_kwargs(
        "https://api.openai.com/v1", "o3-mini", ReasoningMode.OFF
    )
    assert kwargs == {"reasoning_effort": "low"}


def test_openai_gpt5_pro_off_is_empty():
    """gpt-5-pro is hard-pinned to high; helper returns {} and caller warns."""
    kwargs = build_reasoning_kwargs(
        "https://api.openai.com/v1", "gpt-5-pro", ReasoningMode.OFF
    )
    assert kwargs == {}


def test_openai_gpt5_codex_off_returns_low():
    """gpt-5-codex doesn't accept 'minimal'; downgrade to 'low'."""
    kwargs = build_reasoning_kwargs(
        "https://api.openai.com/v1", "gpt-5-codex", ReasoningMode.OFF
    )
    assert kwargs == {"reasoning_effort": "low"}


def test_openrouter_off_returns_effort_none():
    kwargs = build_reasoning_kwargs(
        "https://openrouter.ai/api/v1",
        "anthropic/claude-sonnet-4-5",
        ReasoningMode.OFF,
    )
    assert kwargs == {"extra_body": {"reasoning": {"effort": "none"}}}


def test_openrouter_off_for_gpt5_v0_uses_minimal():
    """Original GPT-5 via OpenRouter still needs 'minimal' not 'none'."""
    kwargs = build_reasoning_kwargs(
        "https://openrouter.ai/api/v1", "openai/gpt-5", ReasoningMode.OFF
    )
    assert kwargs == {"extra_body": {"reasoning": {"effort": "minimal"}}}


def test_venice_off_uses_venice_parameters():
    kwargs = build_reasoning_kwargs(
        "https://api.venice.ai/api/v1", "qwen3-235b", ReasoningMode.OFF
    )
    assert kwargs == {
        "extra_body": {"venice_parameters": {"disable_thinking": True}}
    }


def test_anthropic_off_returns_thinking_disabled():
    kwargs = build_reasoning_kwargs(
        "https://api.anthropic.com/v1", "claude-sonnet-4-5", ReasoningMode.OFF
    )
    assert kwargs == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_anthropic_opus_47_off_omits_thinking():
    """Opus 4.7+ returns 400 on manual thinking; helper omits the param."""
    kwargs = build_reasoning_kwargs(
        "https://api.anthropic.com/v1", "claude-opus-4-7", ReasoningMode.OFF
    )
    assert kwargs == {}


def test_compute3_qwen3_off_disables_thinking():
    kwargs = build_reasoning_kwargs(
        "https://compute3.example.com/v1", "qwen3-32b", ReasoningMode.OFF
    )
    assert kwargs == {
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}}
    }


def test_compute3_deepseek_off_is_empty():
    """DeepSeek-V3.1 default is OFF; no param needed."""
    kwargs = build_reasoning_kwargs(
        "https://compute3.example.com/v1", "deepseek-v3", ReasoningMode.OFF
    )
    assert kwargs == {}


def test_compute3_unknown_sends_both_keys_defensively():
    """Unknown OpenAI-compatible model: send both chat_template_kwargs defensively."""
    kwargs = build_reasoning_kwargs(
        "https://my-llm.example.com/v1", "unknown-future-model", ReasoningMode.OFF
    )
    assert kwargs == {
        "extra_body": {
            "chat_template_kwargs": {"enable_thinking": False, "thinking": False}
        }
    }


# ---------------------------------------------------------------------------
# Override precedence
# ---------------------------------------------------------------------------

def test_override_beats_heuristic():
    """A per-model override wins over auto-classification.

    Pick a model + mode combo where the override produces a different value
    than the heuristic OFF would. gpt-5 (v0 family):
      - heuristic OFF  → "minimal"
      - override HIGH  → "high"
    """
    overrides = {"gpt-5-mini": ReasoningMode.HIGH}
    kwargs = build_reasoning_kwargs(
        "https://api.openai.com/v1",
        "gpt-5-mini",
        ReasoningMode.OFF,
        overrides=overrides,
    )
    assert kwargs == {"reasoning_effort": "high"}


def test_override_can_disable_for_known_off_model():
    """Override to AUTO forces empty kwargs even if mode would normally inject."""
    overrides = {"openai/minimax-m21": ReasoningMode.AUTO}
    kwargs = build_reasoning_kwargs(
        "https://openrouter.ai/api/v1",
        "openai/minimax-m21",
        ReasoningMode.OFF,
        overrides=overrides,
    )
    assert kwargs == {}


def test_override_is_case_insensitive_on_model_lookup():
    overrides = {"my-model": ReasoningMode.OFF}
    kwargs = build_reasoning_kwargs(
        "https://api.openai.com/v1",
        "MY-MODEL",  # uppercase
        ReasoningMode.AUTO,
        overrides=overrides,
    )
    # Override OFF on unknown-ish OpenAI model → falls through to UNKNOWN bucket
    # which yields {} for OpenAI. The point is just that the override fires.
    # We assert the override fired by checking the mode resolution didn't stay AUTO.
    # (Easier: pick a backend where OFF has an observable effect.)
    overrides2 = {"my-model": ReasoningMode.OFF}
    kwargs2 = build_reasoning_kwargs(
        "https://api.venice.ai/api/v1",
        "MY-MODEL",
        ReasoningMode.AUTO,
        overrides=overrides2,
    )
    assert kwargs2 == {
        "extra_body": {"venice_parameters": {"disable_thinking": True}}
    }


# ---------------------------------------------------------------------------
# parse_overrides
# ---------------------------------------------------------------------------

def test_parse_overrides_basic():
    """`none` is an alias for OFF, per ReasoningMode.parse()."""
    result = parse_overrides("gpt-5.8:none,custom-llm:minimal")
    assert result == {
        "gpt-5.8": ReasoningMode.OFF,
        "custom-llm": ReasoningMode.MINIMAL,
    }


def test_parse_overrides_canonical_modes():
    result = parse_overrides("a:off,b:minimal,c:auto,d:low,e:medium,f:high")
    assert result == {
        "a": ReasoningMode.OFF,
        "b": ReasoningMode.MINIMAL,
        "c": ReasoningMode.AUTO,
        "d": ReasoningMode.LOW,
        "e": ReasoningMode.MEDIUM,
        "f": ReasoningMode.HIGH,
    }


def test_parse_overrides_empty():
    assert parse_overrides("") == {}
    assert parse_overrides("   ") == {}


def test_parse_overrides_skips_malformed():
    result = parse_overrides("good:off,bad-no-colon,also:bad:two-colons")
    # "good:off" parsed; "bad-no-colon" skipped; "also:bad:two-colons" splits
    # on first colon → model="also", mode="bad:two-colons" → parse falls back to AUTO
    assert "good" in result and result["good"] == ReasoningMode.OFF
    assert "bad-no-colon" not in result
    assert "also" in result and result["also"] == ReasoningMode.AUTO


def test_parse_overrides_lowercases_model_name():
    result = parse_overrides("GPT-5.8:off")
    assert "gpt-5.8" in result
    assert "GPT-5.8" not in result


# ---------------------------------------------------------------------------
# merge_kwargs
# ---------------------------------------------------------------------------

def test_merge_kwargs_simple():
    base = {"model": "x", "temperature": 0.1}
    extra = {"reasoning_effort": "none"}
    assert merge_kwargs(base, extra) == {
        "model": "x", "temperature": 0.1, "reasoning_effort": "none"
    }


def test_merge_kwargs_deep_merges_extra_body():
    base = {"extra_body": {"existing_field": "keep"}}
    extra = {"extra_body": {"reasoning": {"effort": "none"}}}
    merged = merge_kwargs(base, extra)
    assert merged["extra_body"] == {
        "existing_field": "keep",
        "reasoning": {"effort": "none"},
    }


def test_merge_kwargs_extra_body_nested_dict_merge():
    base = {"extra_body": {"reasoning": {"existing": "value"}}}
    extra = {"extra_body": {"reasoning": {"effort": "none"}}}
    merged = merge_kwargs(base, extra)
    assert merged["extra_body"]["reasoning"] == {
        "existing": "value",
        "effort": "none",
    }


def test_merge_kwargs_empty_inputs():
    assert merge_kwargs({}, {"a": 1}) == {"a": 1}
    assert merge_kwargs({"a": 1}, {}) == {"a": 1}
    assert merge_kwargs({}, {}) == {}


# ---------------------------------------------------------------------------
# is_unsupported_reasoning_error
# ---------------------------------------------------------------------------

def test_is_unsupported_reasoning_error_matches_known_patterns():
    assert is_unsupported_reasoning_error(
        Exception("400 Bad Request: Invalid reasoning_effort value")
    )
    assert is_unsupported_reasoning_error(
        Exception("Unsupported parameter: chat_template_kwargs")
    )
    assert is_unsupported_reasoning_error(
        Exception("Unrecognized field 'thinking' in request body")
    )


def test_is_unsupported_reasoning_error_rejects_unrelated_errors():
    assert not is_unsupported_reasoning_error(Exception("Connection timeout"))
    assert not is_unsupported_reasoning_error(Exception("401 Unauthorized"))
    assert not is_unsupported_reasoning_error(Exception(""))


# ---------------------------------------------------------------------------
# safe_chat_completion (async) — runtime fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_safe_chat_completion_happy_path():
    """When the call succeeds, reasoning kwargs are passed through."""
    fake_response = MagicMock()
    create_fn = AsyncMock(return_value=fake_response)

    result = await safe_chat_completion(
        create_fn,
        base_url="https://api.openai.com/v1",
        model="gpt-5.1",
        reasoning_mode=ReasoningMode.OFF,
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
    )

    assert result is fake_response
    create_fn.assert_awaited_once()
    call_kwargs = create_fn.await_args.kwargs
    assert call_kwargs["reasoning_effort"] == "none"
    assert call_kwargs["model"] == "gpt-5.1"
    assert call_kwargs["messages"] == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_safe_chat_completion_retries_without_reasoning_on_400():
    """When a reasoning-related 400 fires, retry without the params."""
    fake_response = MagicMock()
    create_fn = AsyncMock(
        side_effect=[
            Exception("400 Invalid reasoning_effort"),
            fake_response,
        ]
    )

    result = await safe_chat_completion(
        create_fn,
        base_url="https://api.openai.com/v1",
        model="gpt-6",  # unknown major — optimistic 'none'
        reasoning_mode=ReasoningMode.OFF,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert result is fake_response
    assert create_fn.await_count == 2
    second_call_kwargs = create_fn.await_args_list[1].kwargs
    assert "reasoning_effort" not in second_call_kwargs
    assert second_call_kwargs["model"] == "gpt-6"


@pytest.mark.asyncio
async def test_safe_chat_completion_caches_unsupported_model():
    """After one 400, the second call for that model skips reasoning upfront."""
    fake_response = MagicMock()
    create_fn = AsyncMock(
        side_effect=[
            Exception("400 Invalid reasoning_effort"),
            fake_response,
            fake_response,
        ]
    )

    # First call: tries with reasoning, fails, retries, succeeds (2 awaits)
    await safe_chat_completion(
        create_fn,
        base_url="https://api.openai.com/v1",
        model="gpt-6",
        reasoning_mode=ReasoningMode.OFF,
        messages=[{"role": "user", "content": "hi"}],
    )
    assert create_fn.await_count == 2

    # Second call: skips reasoning params upfront (1 await, no retry)
    await safe_chat_completion(
        create_fn,
        base_url="https://api.openai.com/v1",
        model="gpt-6",
        reasoning_mode=ReasoningMode.OFF,
        messages=[{"role": "user", "content": "hi"}],
    )
    assert create_fn.await_count == 3
    last_call_kwargs = create_fn.await_args_list[2].kwargs
    assert "reasoning_effort" not in last_call_kwargs


@pytest.mark.asyncio
async def test_safe_chat_completion_does_not_swallow_unrelated_errors():
    """Non-reasoning errors propagate unchanged."""
    create_fn = AsyncMock(side_effect=Exception("Connection refused"))

    with pytest.raises(Exception, match="Connection refused"):
        await safe_chat_completion(
            create_fn,
            base_url="https://api.openai.com/v1",
            model="gpt-5.1",
            reasoning_mode=ReasoningMode.OFF,
            messages=[{"role": "user", "content": "hi"}],
        )
    assert create_fn.await_count == 1  # no retry


@pytest.mark.asyncio
async def test_safe_chat_completion_skips_fallback_when_no_reasoning_injected():
    """If mode is AUTO (no reasoning kwargs), any 400 propagates unchanged."""
    create_fn = AsyncMock(side_effect=Exception("400 invalid thinking field"))

    with pytest.raises(Exception, match="400"):
        await safe_chat_completion(
            create_fn,
            base_url="https://api.openai.com/v1",
            model="gpt-5.1",
            reasoning_mode=ReasoningMode.AUTO,
            messages=[{"role": "user", "content": "hi"}],
        )
    assert create_fn.await_count == 1


# ---------------------------------------------------------------------------
# safe_chat_completion_sync — same fallback semantics
# ---------------------------------------------------------------------------

def test_safe_chat_completion_sync_happy_path():
    fake_response = MagicMock()
    create_fn = MagicMock(return_value=fake_response)

    result = safe_chat_completion_sync(
        create_fn,
        base_url="https://compute3.example.com/v1",
        model="qwen3-32b",
        reasoning_mode=ReasoningMode.OFF,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert result is fake_response
    create_fn.assert_called_once()
    call_kwargs = create_fn.call_args.kwargs
    assert call_kwargs["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": False}
    }


def test_safe_chat_completion_sync_fallback_on_400():
    fake_response = MagicMock()
    create_fn = MagicMock(
        side_effect=[
            Exception("400 Unsupported parameter: chat_template_kwargs"),
            fake_response,
        ]
    )

    result = safe_chat_completion_sync(
        create_fn,
        base_url="https://some-llm.example.com/v1",
        model="unknown",
        reasoning_mode=ReasoningMode.OFF,
        messages=[{"role": "user", "content": "hi"}],
    )

    assert result is fake_response
    assert create_fn.call_count == 2
    second_call_kwargs = create_fn.call_args_list[1].kwargs
    assert "extra_body" not in second_call_kwargs


# ---------------------------------------------------------------------------
# Settings smoke — make sure default config doesn't crash
# ---------------------------------------------------------------------------

def test_settings_parsed_reasoning_overrides_empty_by_default():
    from app.config import get_settings

    settings = get_settings()
    # The autouse fixture sets reasoning_model_overrides="" via .env override.
    assert settings.parsed_reasoning_overrides == {}


def test_extraction_mode_default_is_off():
    from app.config import get_settings

    assert get_settings().extraction_reasoning_mode == "off"


def test_relationship_mode_default_is_off():
    from app.config import get_settings

    assert get_settings().relationship_reasoning_mode == "off"
