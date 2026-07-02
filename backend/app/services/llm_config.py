"""LLM configuration utilities.

This module provides helpers to resolve the appropriate LLM configuration
for the main chat path and the dedicated extraction/relationship paths.
"""

import logging
import threading
from typing import Optional, Tuple
from dataclasses import dataclass

from app.config import get_settings
from app.services.reasoning_config import ReasoningMode

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    """LLM configuration for API calls."""
    api_key: str
    base_url: str
    model: str
    reasoning_mode: ReasoningMode = ReasoningMode.AUTO


def get_llm_config(fast_mode: bool = False) -> LLMConfig:
    """
    Get the LLM configuration from the default OpenAI-compatible settings.

    Args:
        fast_mode: If True, uses the fast mode model (OPENAI_MODEL_FAST_MODE)
                   for quicker/cheaper responses.

    Returns:
        LLMConfig with api_key, base_url, and model
    """
    settings = get_settings()
    default_mode = ReasoningMode.parse(settings.default_reasoning_mode)

    model = settings.fast_mode_model if fast_mode else settings.openai_model

    return LLMConfig(
        api_key=settings.openai_api_key,
        base_url=settings.openai_api_base,
        model=model,
        reasoning_mode=default_mode,
    )


def get_extraction_llm_config() -> LLMConfig:
    """
    Get LLM config for graph extraction operations.

    Uses the dedicated extraction config (properties fall back to the main
    OpenAI config when empty).
    """
    settings = get_settings()
    extraction_mode = ReasoningMode.parse(settings.extraction_reasoning_mode)

    return LLMConfig(
        api_key=settings.extraction_api_key,
        base_url=settings.extraction_api_base,
        model=settings.extraction_model,
        reasoning_mode=extraction_mode,
    )


def get_relationship_llm_config() -> LLMConfig:
    """
    Get LLM config for per-chunk relationship extraction.

    Falls back to extraction config, then main config.
    """
    settings = get_settings()
    relationship_mode = ReasoningMode.parse(settings.relationship_reasoning_mode)

    return LLMConfig(
        api_key=settings.rel_extraction_api_key,
        base_url=settings.rel_extraction_api_base,
        model=settings.rel_extraction_model,
        reasoning_mode=relationship_mode,
    )


def build_chat_params(
    model: str,
    *,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
) -> dict:
    """Build chat-completion kwargs adapted to the target model family.

    For OpenAI GPT-5 / o-series reasoning models, `max_tokens` is translated to
    `max_completion_tokens` and non-default `temperature` is dropped (the API
    400s otherwise). For all other models (Venice, vLLM, older OpenAI), the
    standard params are passed through unchanged. Shares one implementation
    with the extraction path via `reasoning_config.adapt_token_params`.
    """
    from app.services.reasoning_config import adapt_token_params

    params: dict = {}
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if temperature is not None:
        params["temperature"] = temperature
    return adapt_token_params(model, params)


def get_llm_config_tuple() -> Tuple[str, str, str]:
    """
    Get LLM configuration as a tuple for backward compatibility.

    Returns:
        Tuple of (api_key, base_url, model)
    """
    config = get_llm_config()
    return (config.api_key, config.base_url, config.model)


def _use_langfuse() -> bool:
    """Whether to return the Langfuse-wrapped OpenAI client."""
    return get_settings().langfuse_tracing_active


def stream_usage_kwargs() -> dict:
    """Kwargs to request token usage on *streamed* completions, when traced.

    OpenAI-compatible streams omit usage unless ``stream_options.include_usage``
    is set — without it Langfuse records a streamed generation with zero cost.
    Gated on tracing being active so untraced deployments (and any gateway that
    might reject the param) see no behavior change. Spread into a streaming
    ``create`` call: ``**stream_usage_kwargs()``.
    """
    return {"stream_options": {"include_usage": True}} if _use_langfuse() else {}


def make_openai_client(*, api_key: str, base_url: str, **kwargs):
    """Construct a sync OpenAI client, Langfuse-wrapped when tracing is active.

    Single decision point for the whole backend: every call site builds its
    client through here, so observability is on/off in one place. The Langfuse
    drop-in is API-compatible with the stock client and base_url-agnostic, so
    Venice/OpenRouter gateways work unchanged. Calls routed through
    ``safe_chat_completion_sync`` (which takes ``client.chat.completions.create``
    as ``create_fn``) are auto-traced transparently.
    """
    if _use_langfuse():
        from langfuse.openai import OpenAI  # drop-in: same API, auto-traces
    else:
        from openai import OpenAI
    return OpenAI(api_key=api_key, base_url=base_url, **kwargs)


_ASYNC_CLIENT_CACHE: dict = {}
_ASYNC_CLIENT_LOCK = threading.Lock()


def make_async_openai_client(*, api_key: str, base_url: str, **kwargs):
    """Async twin of :func:`make_openai_client`, with connection-pool reuse.

    A fresh ``AsyncOpenAI`` owns a fresh httpx pool, so every construction pays
    a TCP+TLS handshake on its first request (~50-300ms against remote
    gateways) — and the ask pipeline used to build 2-3 per turn (researcher/
    writer, memory classifier, compaction). Clients are safe for concurrent
    use on one event loop, so cache them per (api_key, base_url, langfuse,
    extra-kwargs) and reuse the warm pool across calls and turns. Settings
    changes produce a different key, so hot-reloads get a new client.
    """
    use_lf = _use_langfuse()
    cache_key = (api_key, base_url, use_lf, tuple(sorted(kwargs.items())) if kwargs else ())
    try:
        cached = _ASYNC_CLIENT_CACHE.get(cache_key)
        if cached is not None:
            return cached
    except TypeError:
        # Unhashable kwarg (e.g. an http_client object) — build uncached.
        cache_key = None
    if use_lf:
        from langfuse.openai import AsyncOpenAI
    else:
        from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, **kwargs)
    if cache_key is not None:
        with _ASYNC_CLIENT_LOCK:
            _ASYNC_CLIENT_CACHE[cache_key] = client
    return client
