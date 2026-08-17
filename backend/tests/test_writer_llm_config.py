"""Tests for the optional dedicated writer model (WRITER_MODEL /
WRITER_API_BASE / WRITER_API_KEY) — `get_writer_llm_config` resolves each
property independently against the researcher/main config, same idiom as the
extraction/relationship tiers.
"""

from types import SimpleNamespace

from app.services.llm_config import LLMConfig, get_writer_llm_config
from app.services.reasoning_config import ReasoningMode

BASE = LLMConfig(
    api_key="base-key",
    base_url="https://base.example/v1",
    model="base-model",
    reasoning_mode=ReasoningMode.OFF,
)


def _settings(**overrides):
    fields = {"writer_model": "", "writer_api_base": "", "writer_api_key": ""}
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_unset_falls_back_to_base_entirely():
    cfg = get_writer_llm_config(base=BASE, settings=_settings())
    assert (cfg.api_key, cfg.base_url, cfg.model) == (
        "base-key",
        "https://base.example/v1",
        "base-model",
    )
    assert cfg.reasoning_mode is ReasoningMode.OFF


def test_model_only_override_reuses_base_gateway_and_key():
    cfg = get_writer_llm_config(
        base=BASE, settings=_settings(writer_model="prose-model")
    )
    assert cfg.model == "prose-model"
    assert cfg.api_key == "base-key"
    assert cfg.base_url == "https://base.example/v1"


def test_full_override():
    cfg = get_writer_llm_config(
        base=BASE,
        settings=_settings(
            writer_model="prose-model",
            writer_api_base="https://writer.example/v1",
            writer_api_key="writer-key",
        ),
    )
    assert (cfg.api_key, cfg.base_url, cfg.model) == (
        "writer-key",
        "https://writer.example/v1",
        "prose-model",
    )


def test_missing_attrs_on_stub_settings_fall_back():
    """Test stubs without the writer fields must not break the pipeline."""
    cfg = get_writer_llm_config(base=BASE, settings=SimpleNamespace())
    assert (cfg.api_key, cfg.base_url, cfg.model) == (
        "base-key",
        "https://base.example/v1",
        "base-model",
    )
