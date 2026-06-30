"""Provider-agnostic reasoning control for LLM calls.

Knowledge-graph extraction and relationship analysis benefit from disabling
reasoning on capable models (GPT-5/5.1, Claude 4.x, Qwen3, DeepSeek-R1,
MiniMax M3). This module resolves the right per-backend request body
to force reasoning OFF (or any other level), based on the configured
`base_url` and `model` string.

Three layers of robustness:
  1. Version-aware classification via regex (handles new minor releases).
  2. Per-model override env var (escape hatch for novel models).
  3. Runtime fallback on BadRequestError (retries without reasoning params).
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class ReasoningMode(str, Enum):
    """User-facing reasoning level."""

    OFF = "off"           # minimum the backend allows; for structured extraction
    MINIMAL = "minimal"   # tiny think budget where supported
    AUTO = "auto"         # inject nothing; let provider default apply
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @classmethod
    def parse(cls, value: str) -> "ReasoningMode":
        """Parse a string into ReasoningMode. Defaults to AUTO on unknown.

        Accepts canonical enum values plus common aliases:
          - "none", "disabled", "disable", "false", "no" → OFF
          - "default" → AUTO
        """
        if not value:
            return cls.AUTO
        v = value.strip().lower()
        aliases = {
            "none": cls.OFF,
            "disabled": cls.OFF,
            "disable": cls.OFF,
            "false": cls.OFF,
            "no": cls.OFF,
            "default": cls.AUTO,
        }
        if v in aliases:
            return aliases[v]
        for mode in cls:
            if mode.value == v:
                return mode
        logger.warning(
            "Unknown reasoning mode %r; falling back to AUTO. "
            "Valid: off|none|minimal|auto|low|medium|high",
            value,
        )
        return cls.AUTO


class ModelFamily(str, Enum):
    """Tag produced by parse_model_family()."""

    OPENAI_GPT5_V0 = "openai_gpt5_v0"            # gpt-5, gpt-5-mini, gpt-5-nano
    OPENAI_GPT5_V1PLUS = "openai_gpt5_v1plus"    # gpt-5.1, gpt-5.2, gpt-5.X (X>=1)
    OPENAI_GPT5_PRO = "openai_gpt5_pro"          # gpt-5-pro (forced high)
    OPENAI_GPT5_CODEX = "openai_gpt5_codex"      # gpt-5-codex (no minimal)
    OPENAI_O_SERIES = "openai_o_series"          # o1, o3, o4-mini
    OPENAI_UNKNOWN_MAJOR = "openai_unknown_major"  # gpt-6+ — optimistic guess
    ANTHROPIC_OPUS_47_PLUS = "anthropic_opus_47_plus"
    ANTHROPIC_OTHER = "anthropic_other"
    QWEN_THINKING = "qwen_thinking"              # qwen3*, qwen-3*
    HOLO = "holo"
    DEEPSEEK_V3 = "deepseek_v3"                  # already off by default
    GRANITE = "granite"                          # already off by default
    UNKNOWN = "unknown"


# ----- backend classification -----------------------------------------------

def classify_backend(base_url: str, model: str) -> str:
    """Return one of: openai | openrouter | venice | anthropic_native | vllm_or_compatible."""
    bu = (base_url or "").lower()
    if "openrouter.ai" in bu:
        return "openrouter"
    if "venice.ai" in bu:
        return "venice"
    if "api.anthropic.com" in bu:
        return "anthropic_native"
    if "api.openai.com" in bu:
        return "openai"
    return "vllm_or_compatible"


# ----- model family parsing -------------------------------------------------

_RE_GPT5_PRO = re.compile(r"\bgpt-5(?:[.\-]\d+)?-pro\b")
_RE_GPT5_CODEX = re.compile(r"\bgpt-5(?:[.\-]\d+)?-codex")
_RE_GPT5_MINOR = re.compile(r"\bgpt-5\.(\d+)\b")
_RE_GPT5_V0 = re.compile(r"\bgpt-5(?:-mini|-nano)?(?![.\-\w])")
_RE_GPT_UNKNOWN_MAJOR = re.compile(r"\bgpt-(\d+)(?:[.\-]|\b)")
_RE_O_SERIES = re.compile(r"\bo[134](?:-mini)?\b")
_RE_CLAUDE_OPUS = re.compile(r"\bclaude-opus-(\d+)-(\d+)")
_RE_CLAUDE = re.compile(r"\bclaude")
_RE_QWEN = re.compile(r"\bqwen(?:-?3)?")
_RE_HOLO = re.compile(r"\bholo")
_RE_DEEPSEEK_V3 = re.compile(r"\bdeepseek[-_]?v3")
_RE_GRANITE = re.compile(r"\bgranite")


_OPENAI_REASONING_FAMILIES = frozenset({
    ModelFamily.OPENAI_GPT5_V0,
    ModelFamily.OPENAI_GPT5_V1PLUS,
    ModelFamily.OPENAI_GPT5_PRO,
    ModelFamily.OPENAI_GPT5_CODEX,
    ModelFamily.OPENAI_O_SERIES,
    ModelFamily.OPENAI_UNKNOWN_MAJOR,
})


def adapt_token_params(model: str, kwargs: dict) -> dict:
    """Adapt chat-completion params for OpenAI GPT-5 / o-series reasoning models.

    Those models reject the classic Chat Completions params: `max_tokens` must
    be sent as `max_completion_tokens`, and `temperature` only accepts the
    default (1) — any other value 400s. For every other family (Venice/qwen,
    vLLM, older OpenAI, Anthropic) the params are returned unchanged.

    Returns a new dict; the input is not mutated.
    """
    if parse_model_family(model) not in _OPENAI_REASONING_FAMILIES:
        return kwargs
    out = dict(kwargs)
    if "max_tokens" in out:
        out["max_completion_tokens"] = out.pop("max_tokens")
    if out.get("temperature") not in (None, 1, 1.0):
        out.pop("temperature", None)
    return out


def parse_model_family(model: str) -> ModelFamily:
    """Identify the model family from its name string.

    Uses regex so new minor releases route correctly without code changes
    (e.g. gpt-5.8 → OPENAI_GPT5_V1PLUS).
    """
    m = (model or "").lower()

    # OpenAI — specific subfamilies first, then version-based
    if _RE_GPT5_PRO.search(m):
        return ModelFamily.OPENAI_GPT5_PRO
    if _RE_GPT5_CODEX.search(m):
        return ModelFamily.OPENAI_GPT5_CODEX
    minor = _RE_GPT5_MINOR.search(m)
    if minor and int(minor.group(1)) >= 1:
        return ModelFamily.OPENAI_GPT5_V1PLUS
    if _RE_GPT5_V0.search(m):
        return ModelFamily.OPENAI_GPT5_V0
    unknown_major = _RE_GPT_UNKNOWN_MAJOR.search(m)
    if unknown_major and int(unknown_major.group(1)) >= 6:
        return ModelFamily.OPENAI_UNKNOWN_MAJOR
    if _RE_O_SERIES.search(m):
        return ModelFamily.OPENAI_O_SERIES

    # Anthropic
    opus = _RE_CLAUDE_OPUS.search(m)
    if opus:
        major, minor_v = int(opus.group(1)), int(opus.group(2))
        if (major, minor_v) >= (4, 7):
            return ModelFamily.ANTHROPIC_OPUS_47_PLUS
        return ModelFamily.ANTHROPIC_OTHER
    if _RE_CLAUDE.search(m):
        return ModelFamily.ANTHROPIC_OTHER

    # Open-weight / vLLM families
    if _RE_QWEN.search(m):
        return ModelFamily.QWEN_THINKING
    if _RE_HOLO.search(m):
        return ModelFamily.HOLO
    if _RE_DEEPSEEK_V3.search(m):
        return ModelFamily.DEEPSEEK_V3
    if _RE_GRANITE.search(m):
        return ModelFamily.GRANITE

    return ModelFamily.UNKNOWN


# ----- overrides parsing ----------------------------------------------------

def parse_overrides(env_value: str) -> dict[str, ReasoningMode]:
    """Parse REASONING_MODEL_OVERRIDES into a dict.

    Format: "model1:mode1,model2:mode2"
    Example: "gpt-5.8:none,custom-llm:minimal"
    """
    result: dict[str, ReasoningMode] = {}
    if not env_value:
        return result
    for entry in env_value.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            logger.warning("Skipping malformed override entry %r (expected 'model:mode')", entry)
            continue
        model, _, mode_str = entry.partition(":")
        model = model.strip()
        mode_str = mode_str.strip()
        if not model or not mode_str:
            logger.warning("Skipping override entry with empty model or mode: %r", entry)
            continue
        result[model.lower()] = ReasoningMode.parse(mode_str)
    return result


# ----- kwargs builder -------------------------------------------------------

def merge_kwargs(base: dict, extra: dict) -> dict:
    """Merge two kwarg dicts, deep-merging nested 'extra_body' dicts."""
    if not extra:
        return base
    if not base:
        return dict(extra)
    out = dict(base)
    for k, v in extra.items():
        if k == "extra_body" and isinstance(out.get("extra_body"), dict) and isinstance(v, dict):
            merged_eb = dict(out["extra_body"])
            for ek, ev in v.items():
                if isinstance(ev, dict) and isinstance(merged_eb.get(ek), dict):
                    merged_eb[ek] = {**merged_eb[ek], **ev}
                else:
                    merged_eb[ek] = ev
            out["extra_body"] = merged_eb
        else:
            out[k] = v
    return out


def _openai_kwargs_for(family: ModelFamily, mode: ReasoningMode) -> dict:
    """OpenAI direct (api.openai.com) request kwargs for the given mode."""
    if mode == ReasoningMode.AUTO:
        return {}

    if family == ModelFamily.OPENAI_GPT5_PRO:
        return {}  # cannot lower; warn elsewhere

    if mode == ReasoningMode.OFF:
        if family == ModelFamily.OPENAI_GPT5_V1PLUS:
            return {"reasoning_effort": "none"}
        if family == ModelFamily.OPENAI_GPT5_V0:
            return {"reasoning_effort": "minimal"}
        if family == ModelFamily.OPENAI_GPT5_CODEX:
            return {"reasoning_effort": "low"}  # codex doesn't accept minimal
        if family == ModelFamily.OPENAI_O_SERIES:
            return {"reasoning_effort": "low"}  # lowest available
        if family == ModelFamily.OPENAI_UNKNOWN_MAJOR:
            return {"reasoning_effort": "none"}  # optimistic; fallback retries
        return {}

    if mode == ReasoningMode.MINIMAL:
        if family == ModelFamily.OPENAI_GPT5_V0:
            return {"reasoning_effort": "minimal"}
        if family == ModelFamily.OPENAI_GPT5_V1PLUS:
            return {"reasoning_effort": "none"}  # closest available
        return {"reasoning_effort": "low"}

    return {"reasoning_effort": mode.value}


def _openrouter_kwargs_for(family: ModelFamily, mode: ReasoningMode) -> dict:
    if mode == ReasoningMode.AUTO:
        return {}
    if mode == ReasoningMode.OFF:
        effort = "minimal" if family == ModelFamily.OPENAI_GPT5_V0 else "none"
    elif mode == ReasoningMode.MINIMAL:
        effort = "minimal"
    else:
        effort = mode.value
    return {"extra_body": {"reasoning": {"effort": effort}}}


def _venice_kwargs_for(mode: ReasoningMode) -> dict:
    if mode in (ReasoningMode.OFF, ReasoningMode.MINIMAL):
        return {"extra_body": {"venice_parameters": {"disable_thinking": True}}}
    return {}


def _anthropic_native_kwargs_for(family: ModelFamily, mode: ReasoningMode) -> dict:
    if mode == ReasoningMode.AUTO:
        return {}
    if family == ModelFamily.ANTHROPIC_OPUS_47_PLUS:
        return {}  # manual thinking returns 400; adaptive thinking auto-applies
    if mode == ReasoningMode.OFF:
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {}


def _vllm_compatible_kwargs_for(family: ModelFamily, mode: ReasoningMode) -> dict:
    if mode == ReasoningMode.AUTO:
        return {}
    if mode in (ReasoningMode.OFF, ReasoningMode.MINIMAL):
        if family == ModelFamily.QWEN_THINKING:
            return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
        if family == ModelFamily.HOLO:
            return {"extra_body": {"chat_template_kwargs": {"thinking": False}}}
        if family in (ModelFamily.DEEPSEEK_V3, ModelFamily.GRANITE):
            return {}  # already off by default
        # Unknown OpenAI-compatible endpoint: send both keys defensively;
        # vLLM silently ignores unknown chat_template_kwargs.
        return {
            "extra_body": {
                "chat_template_kwargs": {"enable_thinking": False, "thinking": False}
            }
        }
    return {}


def build_reasoning_kwargs(
    base_url: str,
    model: str,
    mode: ReasoningMode,
    overrides: Optional[dict[str, ReasoningMode]] = None,
) -> dict:
    """Return kwargs to splat into client.chat.completions.create(...).

    The returned dict may contain 'reasoning_effort' and/or 'extra_body'.
    Empty dict means 'inject nothing' (provider default applies).

    Args:
        base_url: The OpenAI-compatible endpoint URL.
        model: The model identifier (e.g. "gpt-5-mini", "qwen3-32b").
        mode: Desired reasoning mode.
        overrides: Optional per-model overrides. If the model matches a key
            here (case-insensitive), the override mode wins.
    """
    # Per-model override beats everything
    if overrides:
        effective = overrides.get((model or "").lower(), mode)
    else:
        effective = mode

    if effective == ReasoningMode.AUTO:
        return {}

    backend = classify_backend(base_url, model)
    family = parse_model_family(model)

    if backend == "openai":
        return _openai_kwargs_for(family, effective)
    if backend == "openrouter":
        return _openrouter_kwargs_for(family, effective)
    if backend == "venice":
        return _venice_kwargs_for(effective)
    if backend == "anthropic_native":
        return _anthropic_native_kwargs_for(family, effective)
    return _vllm_compatible_kwargs_for(family, effective)


# ----- warnings -------------------------------------------------------------

_warned_unlowerable: set[tuple[str, str]] = set()


def warn_if_unlowerable(base_url: str, model: str, mode: ReasoningMode) -> None:
    """Log a one-time WARN if the model can't honor a sub-default reasoning mode."""
    if mode == ReasoningMode.AUTO:
        return
    family = parse_model_family(model)
    key = (base_url or "", model or "")
    if key in _warned_unlowerable:
        return
    if family == ModelFamily.OPENAI_GPT5_PRO:
        logger.warning(
            "Model %r is hard-pinned to reasoning_effort=high by OpenAI; "
            "EXTRACTION/RELATIONSHIP_REASONING_MODE=%s will be ignored.",
            model, mode.value,
        )
        _warned_unlowerable.add(key)
    elif family == ModelFamily.ANTHROPIC_OPUS_47_PLUS:
        logger.warning(
            "Model %r uses adaptive thinking; manual disable is not supported. "
            "Reasoning may still occur regardless of mode=%s.",
            model, mode.value,
        )
        _warned_unlowerable.add(key)


# ----- runtime fallback wrapper --------------------------------------------

# (base_url, model) tuples whose reasoning params were rejected at least once;
# subsequent calls skip reasoning params entirely.
_unsupported_reasoning_models: set[tuple[str, str]] = set()


_REASONING_ERR_TOKENS = (
    "reasoning_effort",
    "reasoning",
    "thinking",
    "chat_template_kwargs",
    "venice_parameters",
)


def is_unsupported_reasoning_error(exc: Exception) -> bool:
    """Heuristic: did this error come from a rejected reasoning parameter?"""
    msg = str(exc).lower()
    if not msg:
        return False
    # Only match 4xx-style validation errors, not auth/network/etc.
    if not any(tok in msg for tok in ("invalid", "unsupported", "unrecognized", "not allowed", "400", "bad request", "unexpected")):
        return False
    return any(tok in msg for tok in _REASONING_ERR_TOKENS)


def flatten_reasoning_body(reasoning_kwargs: dict) -> dict:
    """Flatten OpenAI-SDK-style reasoning kwargs into a raw JSON body dict.

    ``build_reasoning_kwargs`` returns kwargs shaped for the OpenAI Python SDK:
    top-level fields (e.g. ``reasoning_effort``) plus an ``extra_body`` dict
    whose contents the SDK splices into the HTTP body. Callers that POST raw
    JSON (vision_analyzer uses httpx) need both layers merged into one dict
    they can splat onto the payload. Pass the result through ``dict.update`` or
    ``payload | flatten_reasoning_body(...)``.

    Example::

        kw = build_reasoning_kwargs(base_url, model, ReasoningMode.OFF)
        # → {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
        body = flatten_reasoning_body(kw)
        # → {"chat_template_kwargs": {"enable_thinking": False}}
    """
    if not reasoning_kwargs:
        return {}
    out: dict[str, Any] = {}
    for k, v in reasoning_kwargs.items():
        if k == "extra_body" and isinstance(v, dict):
            for ek, ev in v.items():
                out[ek] = ev
        else:
            out[k] = v
    return out


def is_reasoning_unsupported(base_url: str, model: str) -> bool:
    """Has this (base_url, model) been marked as rejecting reasoning params?"""
    key = ((base_url or "").lower(), (model or "").lower())
    return key in _unsupported_reasoning_models


def mark_reasoning_unsupported(base_url: str, model: str) -> None:
    """Cache that this (base_url, model) rejects reasoning params.

    Subsequent ``build_reasoning_kwargs`` calls still return the params (the
    cache lives in the safe_chat_completion wrappers), but callers that drive
    their own retry loop (e.g. vision_analyzer, which uses raw httpx) can use
    this to skip reasoning kwargs upfront after a one-time 400.
    """
    key = ((base_url or "").lower(), (model or "").lower())
    _unsupported_reasoning_models.add(key)


def _strip_reasoning_kwargs(kwargs: dict) -> dict:
    """Remove reasoning-related fields from request kwargs."""
    out = {k: v for k, v in kwargs.items() if k not in ("reasoning_effort", "thinking")}
    if isinstance(out.get("extra_body"), dict):
        eb = {
            k: v for k, v in out["extra_body"].items()
            if k not in ("reasoning", "thinking", "chat_template_kwargs", "venice_parameters")
        }
        if eb:
            out["extra_body"] = eb
        else:
            out.pop("extra_body", None)
    return out


def apply_cache_control(messages: list, base_url: str, model: str) -> list:
    """Mark the first system message as an Anthropic prompt-cache breakpoint.

    Only applies on OpenRouter with an `anthropic/*` model — OpenRouter
    forwards `cache_control` to Anthropic, which then serves the (large,
    stable) system prompt at cache-read pricing on subsequent calls. Every
    other backend either caches automatically by prefix (OpenAI, vLLM with
    prefix caching) or ignores the field, so this is a no-op for them.

    Returns a new list; the input messages are not mutated.
    """
    if classify_backend(base_url, model) != "openrouter":
        return messages
    if not (model or "").lower().startswith("anthropic/"):
        return messages
    out = []
    converted = False
    for m in messages:
        if (
            not converted
            and isinstance(m, dict)
            and m.get("role") == "system"
            and isinstance(m.get("content"), str)
        ):
            out.append({
                **m,
                "content": [{
                    "type": "text",
                    "text": m["content"],
                    "cache_control": {"type": "ephemeral"},
                }],
            })
            converted = True
        else:
            out.append(m)
    return out


def _prepare_call(
    base_url: str,
    model: str,
    reasoning_mode: ReasoningMode,
    overrides: Optional[dict[str, ReasoningMode]],
    create_kwargs: dict,
) -> tuple[dict, dict, tuple[str, str], bool]:
    """Shared prep for sync + async wrappers.

    Returns (merged_kwargs, reasoning_kwargs, cache_key, already_unsupported).
    """
    key = ((base_url or "").lower(), (model or "").lower())
    already_unsupported = key in _unsupported_reasoning_models

    if not already_unsupported:
        reasoning_kwargs = build_reasoning_kwargs(
            base_url=base_url,
            model=model,
            mode=reasoning_mode,
            overrides=overrides,
        )
        warn_if_unlowerable(base_url, model, reasoning_mode)
    else:
        reasoning_kwargs = {}

    # Ensure model is forwarded to create_fn (caller doesn't repeat it).
    merged = merge_kwargs(create_kwargs, reasoning_kwargs)
    merged.setdefault("model", model)
    # Translate max_tokens→max_completion_tokens / drop temperature for GPT-5.
    merged = adapt_token_params(model, merged)
    # Opt-in provider prompt caching (currently OpenRouter→Anthropic only).
    if isinstance(merged.get("messages"), list):
        try:
            from app.config import get_settings

            if get_settings().enable_prompt_cache_control:
                merged["messages"] = apply_cache_control(
                    merged["messages"], base_url, model
                )
        except Exception:  # noqa: BLE001 — caching must never break a call
            pass
    return merged, reasoning_kwargs, key, already_unsupported


async def safe_chat_completion(
    create_fn: Callable[..., Awaitable[Any]],
    *,
    base_url: str,
    model: str,
    reasoning_mode: ReasoningMode,
    overrides: Optional[dict[str, ReasoningMode]] = None,
    **create_kwargs: Any,
) -> Any:
    """Call an async chat.completions.create with reasoning kwargs + fallback.

    On `BadRequestError`-style errors that look like rejected reasoning params,
    retries once without them and caches the (base_url, model) pair so future
    calls skip the params upfront.

    Args:
        create_fn: The bound async method, e.g. `client.chat.completions.create`.
        base_url: Endpoint URL for backend detection.
        model: Model identifier (forwarded to create_fn).
        reasoning_mode: Desired reasoning mode.
        overrides: Per-model overrides dict (from settings).
        **create_kwargs: Other kwargs forwarded to create_fn (messages, temperature, etc.).
    """
    merged, reasoning_kwargs, key, already_unsupported = _prepare_call(
        base_url, model, reasoning_mode, overrides, create_kwargs
    )

    try:
        return await create_fn(**merged)
    except Exception as exc:
        if already_unsupported or not reasoning_kwargs:
            raise
        if not is_unsupported_reasoning_error(exc):
            raise
        logger.warning(
            "Reasoning params rejected by %s (model=%s): %s. "
            "Retrying without them and caching for future calls.",
            base_url, model, exc,
        )
        _unsupported_reasoning_models.add(key)
        stripped = _strip_reasoning_kwargs(merged)
        return await create_fn(**stripped)


def safe_chat_completion_sync(
    create_fn: Callable[..., Any],
    *,
    base_url: str,
    model: str,
    reasoning_mode: ReasoningMode,
    overrides: Optional[dict[str, ReasoningMode]] = None,
    **create_kwargs: Any,
) -> Any:
    """Synchronous twin of safe_chat_completion."""
    merged, reasoning_kwargs, key, already_unsupported = _prepare_call(
        base_url, model, reasoning_mode, overrides, create_kwargs
    )

    try:
        return create_fn(**merged)
    except Exception as exc:
        if already_unsupported or not reasoning_kwargs:
            raise
        if not is_unsupported_reasoning_error(exc):
            raise
        logger.warning(
            "Reasoning params rejected by %s (model=%s): %s. "
            "Retrying without them and caching for future calls.",
            base_url, model, exc,
        )
        _unsupported_reasoning_models.add(key)
        stripped = _strip_reasoning_kwargs(merged)
        return create_fn(**stripped)


def _reset_caches_for_tests() -> None:
    """Test-only: clear module-level caches between tests."""
    _warned_unlowerable.clear()
    _unsupported_reasoning_models.clear()
