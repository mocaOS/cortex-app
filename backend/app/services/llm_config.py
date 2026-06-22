"""LLM configuration utilities.

This module provides helpers to resolve the appropriate LLM configuration
for the main chat path and the dedicated extraction/relationship paths.
"""

import logging
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
