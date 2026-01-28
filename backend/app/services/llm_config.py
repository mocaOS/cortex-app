"""LLM Configuration utility for Turbo Mode support.

This module provides utilities to get the appropriate LLM configuration
based on whether Turbo Mode is enabled (running Compute3 GPU instance).
"""

import logging
from typing import Optional, Tuple
from dataclasses import dataclass

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    """LLM configuration for API calls."""
    api_key: str
    base_url: str
    model: str
    is_turbo: bool = False


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
    else:
        logger.info("Turbo Mode disabled - LLM calls will use default OpenAI settings")


def get_turbo_mode_state() -> dict:
    """Get current turbo mode state."""
    return _turbo_state.copy()


def is_turbo_mode_active() -> bool:
    """Check if turbo mode is currently active."""
    return _turbo_state["active"] and _turbo_state["base_url"] is not None


def get_llm_config() -> LLMConfig:
    """
    Get the appropriate LLM configuration based on turbo mode status.
    
    When Turbo Mode is active:
    - Uses the Compute3 GPU instance base URL
    - Uses JWT tokens generated from job_key for authentication
    
    When Turbo Mode is inactive:
    - Uses the default OpenAI settings from configuration
    
    Returns:
        LLMConfig with api_key, base_url, model, and is_turbo flag
    """
    settings = get_settings()
    
    # Check if turbo mode is active
    if _turbo_state["active"] and _turbo_state["base_url"]:
        # Use the auth token fetched from Compute3 API
        api_key = _turbo_state["api_key"] or settings.compute3_api_key
        
        return LLMConfig(
            api_key=api_key,
            base_url=_turbo_state["base_url"],
            model=settings.compute3_model,
            is_turbo=True,
        )
    
    # Use default OpenAI settings
    return LLMConfig(
        api_key=settings.openai_api_key,
        base_url=settings.openai_api_base,
        model=settings.openai_model,
        is_turbo=False,
    )


def get_llm_config_tuple() -> Tuple[str, str, str]:
    """
    Get LLM configuration as a tuple for backward compatibility.
    
    Returns:
        Tuple of (api_key, base_url, model)
    """
    config = get_llm_config()
    return (config.api_key, config.base_url, config.model)
