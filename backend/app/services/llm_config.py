"""LLM Configuration utility for Turbo Mode support.

This module provides utilities to get the appropriate LLM configuration
based on whether Turbo Mode is enabled (running Compute3 GPU instance).
"""

import logging
from typing import Optional, Tuple
from dataclasses import dataclass, field

from app.config import get_settings
from app.services.reasoning_config import ReasoningMode

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    """LLM configuration for API calls."""
    api_key: str
    base_url: str
    model: str
    is_turbo: bool = False
    reasoning_mode: ReasoningMode = ReasoningMode.AUTO


# Cache for active turbo mode state (updated by compute3_service)
_turbo_state = {
    "active": False,
    "base_url": None,
    "api_key": None,
}


def set_turbo_mode_state(
    active: bool, 
    base_url: Optional[str] = None, 
    api_key: Optional[str] = None
):
    """
    Update the turbo mode state.
    Called by compute3_service when turbo mode is started/stopped.
    
    Args:
        active: Whether turbo mode is active
        base_url: The vLLM server URL
        api_key: The authentication token for the vLLM server
    """
    global _turbo_state
    _turbo_state["active"] = active
    _turbo_state["base_url"] = base_url
    _turbo_state["api_key"] = api_key
    
    if active:
        logger.info(f"Turbo Mode enabled - LLM calls will use: {base_url}")


def get_turbo_mode_state() -> dict:
    """Get current turbo mode state."""
    return _turbo_state.copy()


def is_turbo_mode_active() -> bool:
    """Check if turbo mode is currently active."""
    return _turbo_state["active"] and _turbo_state["base_url"] is not None


def get_llm_config(fast_mode: bool = False) -> LLMConfig:
    """
    Get the appropriate LLM configuration based on turbo mode status.
    
    When Turbo Mode is active:
    - Uses the Compute3 GPU instance base URL
    - Uses JWT tokens generated from job_key for authentication
    
    When Turbo Mode is inactive:
    - Uses the default OpenAI settings from configuration
    
    Args:
        fast_mode: If True, uses the fast mode model (OPENAI_MODEL_FAST_MODE)
                   for quicker/cheaper responses. Only applies when not in turbo mode.
    
    Returns:
        LLMConfig with api_key, base_url, model, and is_turbo flag
    """
    settings = get_settings()
    default_mode = ReasoningMode.parse(settings.default_reasoning_mode)

    # Check if turbo mode is active
    if _turbo_state["active"] and _turbo_state["base_url"]:
        # Use the auth token fetched from Compute3 API
        api_key = _turbo_state["api_key"] or settings.compute3_api_key

        return LLMConfig(
            api_key=api_key,
            base_url=_turbo_state["base_url"],
            model=settings.compute3_model,
            is_turbo=True,
            reasoning_mode=default_mode,
        )

    # Use default OpenAI settings, with optional fast mode model
    model = settings.fast_mode_model if fast_mode else settings.openai_model

    return LLMConfig(
        api_key=settings.openai_api_key,
        base_url=settings.openai_api_base,
        model=model,
        is_turbo=False,
        reasoning_mode=default_mode,
    )


def get_extraction_llm_config() -> LLMConfig:
    """
    Get LLM config for graph extraction operations.

    Turbo mode ALWAYS takes priority — designed for massive-scale ingestion.
    Otherwise falls back to dedicated extraction config, then main OpenAI config.
    """
    settings = get_settings()
    extraction_mode = ReasoningMode.parse(settings.extraction_reasoning_mode)

    # Turbo mode ALWAYS overrides — even if dedicated extraction endpoint is set
    if _turbo_state["active"] and _turbo_state["base_url"]:
        api_key = _turbo_state["api_key"] or settings.compute3_api_key
        return LLMConfig(
            api_key=api_key,
            base_url=_turbo_state["base_url"],
            model=settings.compute3_model,
            is_turbo=True,
            reasoning_mode=extraction_mode,
        )

    # Use dedicated extraction config (properties fall back to main config if empty)
    return LLMConfig(
        api_key=settings.extraction_api_key,
        base_url=settings.extraction_api_base,
        model=settings.extraction_model,
        is_turbo=False,
        reasoning_mode=extraction_mode,
    )


def get_relationship_llm_config() -> LLMConfig:
    """
    Get LLM config for per-chunk relationship extraction.

    Falls back to extraction config, then main config.
    Turbo mode takes priority when active.
    """
    settings = get_settings()
    relationship_mode = ReasoningMode.parse(settings.relationship_reasoning_mode)

    if _turbo_state["active"] and _turbo_state["base_url"]:
        api_key = _turbo_state["api_key"] or settings.compute3_api_key
        return LLMConfig(
            api_key=api_key,
            base_url=_turbo_state["base_url"],
            model=settings.compute3_model,
            is_turbo=True,
            reasoning_mode=relationship_mode,
        )

    return LLMConfig(
        api_key=settings.rel_extraction_api_key,
        base_url=settings.rel_extraction_api_base,
        model=settings.rel_extraction_model,
        is_turbo=False,
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
    400s otherwise). For all other models (Venice, vLLM/turbo, older OpenAI),
    the standard params are passed through unchanged. Shares one implementation
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
