"""Langfuse observability wiring — gating + no-op contract.

The load-bearing invariant: with no LANGFUSE_* credentials the integration is a
transparent no-op (plain OpenAI client, inert helpers), so the same image runs
identically traced or untraced. These tests lock that down without touching the
network or a real Langfuse client.
"""

import asyncio

import pytest

from app.config import get_settings
from app.services import observability as obs
from app.services import llm_config


@pytest.fixture
def lf_settings():
    """Yield settings with LANGFUSE_* cleared; restore after. Also resets the
    module singleton so each test starts from a clean slate."""
    s = get_settings()
    saved = {
        "langfuse_public_key": s.langfuse_public_key,
        "langfuse_secret_key": s.langfuse_secret_key,
        "langfuse_base_url": s.langfuse_base_url,
        "langfuse_tracing_enabled": s.langfuse_tracing_enabled,
    }
    s.langfuse_public_key = ""
    s.langfuse_secret_key = ""
    s.langfuse_base_url = ""
    s.langfuse_tracing_enabled = True
    obs.reset_for_tests()
    yield s
    for k, v in saved.items():
        setattr(s, k, v)
    obs.reset_for_tests()


def _activate(s):
    s.langfuse_public_key = "pk-lf-test"
    s.langfuse_secret_key = "sk-lf-test"
    s.langfuse_base_url = "https://langfuse.invalid"


def test_tracing_active_requires_all_three(lf_settings):
    s = lf_settings
    assert s.langfuse_tracing_active is False
    s.langfuse_public_key = "pk-lf-test"
    assert s.langfuse_tracing_active is False  # secret + base_url still missing
    _activate(s)
    assert s.langfuse_tracing_active is True
    s.langfuse_tracing_enabled = False  # master off-switch overrides
    assert s.langfuse_tracing_active is False


def test_init_langfuse_noop_when_inactive(lf_settings):
    assert obs.init_langfuse() is None
    assert obs.get_langfuse() is None


def test_helpers_are_inert_when_inactive(lf_settings):
    # observed_trace yields None and never raises
    with obs.observed_trace("t", user_id="u", tags=["x"]) as span:
        assert span is None
    # record_generation is a silent no-op
    assert obs.record_generation(name="e", model="m", usage={"prompt_tokens": 1}) is None


def test_traced_sse_passthrough_when_inactive(lf_settings):
    async def gen():
        yield 1
        yield 2

    g = gen()
    assert obs.traced_sse(g, "n") is g  # same object, no wrapping

    async def drain():
        return [x async for x in obs.traced_sse(gen(), "n")]

    assert asyncio.run(drain()) == [1, 2]


def test_stream_usage_kwargs_gated(lf_settings):
    s = lf_settings
    assert llm_config.stream_usage_kwargs() == {}
    _activate(s)
    assert llm_config.stream_usage_kwargs() == {"stream_options": {"include_usage": True}}


def test_factory_returns_plain_client_when_inactive(lf_settings, monkeypatch):
    # The autouse mock_llm fixture replaces openai.OpenAI with a raiser; assert
    # the factory routes to the plain (untraced) import rather than langfuse's.
    captured = {}

    def _fake_openai(**kwargs):
        captured["called"] = "plain"
        return object()

    monkeypatch.setattr("openai.OpenAI", _fake_openai)
    llm_config.make_openai_client(api_key="x", base_url="http://localhost")
    assert captured["called"] == "plain"


def test_map_usage_maps_openai_keys():
    assert obs._map_usage(None) is None
    assert obs._map_usage({}) is None
    assert obs._map_usage(
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    ) == {"input": 10, "output": 5, "total": 15}


def test_provider_from_base_url():
    assert obs.provider_from_base_url("https://api.venice.ai/api/v1") == "venice"
    assert obs.provider_from_base_url("https://openrouter.ai/api/v1") == "openrouter"
    assert obs.provider_from_base_url("https://api.openai.com/v1") == "openai"
    assert obs.provider_from_base_url("") == "unknown"
