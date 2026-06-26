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


# ---------------------------------------------------------------------------
# Content masking (_mask_content) — pure-function, no network.
#
# Policy: redact ALL authored text (deny-by-default); keep only structure —
# roles, model/params, tool names + arg/param KEYS, allow-listed metadata keys,
# numerics/bools. The hook runs once per field and is NOT told which field, so
# classification is purely structural. Must be total (never raise → fail closed).
# ---------------------------------------------------------------------------

R = obs._REDACTED
SECRET = "SECRET_d2c1f0e9"  # planted leak marker — must appear NOWHERE in output


def test_mask_bare_string_redacted():
    assert obs._mask_content(data=SECRET) == R


def test_mask_list_of_strings_redacted():
    # Embedding batch shape.
    assert obs._mask_content(data=[SECRET, "another query"]) == [R, R]


def test_mask_scalars_pass_through():
    # Token usage / cost / latency / flags are structural — keep verbatim.
    assert obs._mask_content(data=None) is None
    assert obs._mask_content(data=42) == 42
    assert obs._mask_content(data=0.0017) == 0.0017
    assert obs._mask_content(data=True) is True


def test_mask_message_list_keeps_roles_redacts_content():
    msgs = [
        {"role": "system", "content": f"You are helpful {SECRET}"},
        {"role": "user", "content": SECRET},
        {"role": "assistant", "content": f"answer {SECRET}"},
        {"role": "tool", "tool_call_id": "call_1", "content": SECRET},
    ]
    out = obs._mask_content(data=msgs)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert all(m["content"] == R for m in out)
    assert out[3]["tool_call_id"] == "call_1"  # structural id kept
    assert SECRET not in repr(out)


def test_mask_input_dict_with_tools_keeps_names_and_param_keys():
    data = {
        "model": "gpt-x",
        "temperature": 0.2,
        "messages": [{"role": "user", "content": SECRET}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "knowledge_search",
                    "description": f"search the graph {SECRET}",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": SECRET},
                            "limit": {"type": "integer"},
                        },
                        "required": ["query"],
                    },
                },
            }
        ],
    }
    out = obs._mask_content(data=data)
    assert out["model"] == "gpt-x"  # model + params kept
    assert out["temperature"] == 0.2
    assert out["messages"][0]["content"] == R
    fn = out["tools"][0]["function"]
    assert fn["name"] == "knowledge_search"  # tool name kept
    assert fn["description"] == R  # description redacted
    props = fn["parameters"]["properties"]
    assert set(props.keys()) == {"query", "limit"}  # param KEYS kept
    assert props["query"]["type"] == "string"  # types kept
    assert props["query"]["description"] == R  # param description redacted
    assert fn["parameters"]["required"] == ["query"]  # required kept (keys, not content)
    assert SECRET not in repr(out)


def test_mask_output_dict_with_tool_calls_keeps_name_redacts_arg_values():
    data = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_42",
                "type": "function",
                "function": {
                    "name": "entity_lookup",
                    "arguments": '{"entity_name": "' + SECRET + '", "depth": 2}',
                },
            }
        ],
    }
    out = obs._mask_content(data=data)
    assert out["role"] == "assistant"
    assert out["content"] is None
    tc = out["tool_calls"][0]
    assert tc["id"] == "call_42"
    assert tc["function"]["name"] == "entity_lookup"  # name kept
    # arguments re-emitted with KEYS kept, VALUES redacted
    import json

    parsed = json.loads(tc["function"]["arguments"])
    assert set(parsed.keys()) == {"entity_name", "depth"}
    assert parsed["entity_name"] == R
    assert parsed["depth"] == R
    assert SECRET not in repr(out)


def test_mask_metadata_dict_keeps_allowlist_redacts_rest():
    data = {
        "stage": "graph_extraction",
        "endpoint": "/api/ask/stream",
        "mode": "agentic",
        "provider": "venice",
        "latency_ms": 1234,
        "note": f"freeform {SECRET}",
    }
    out = obs._mask_content(data=data)
    assert out["stage"] == "graph_extraction"
    assert out["endpoint"] == "/api/ask/stream"
    assert out["mode"] == "agentic"
    assert out["provider"] == "venice"
    assert out["latency_ms"] == 1234  # numeric kept
    assert out["note"] == R  # non-allowlisted string redacted
    assert SECRET not in repr(out)


def test_mask_deeply_nested_unknown_key_redacted():
    # Content hidden under an unexpected key must still be redacted (fail closed).
    data = {"weird": {"deeper": {"payload": SECRET}}}
    out = obs._mask_content(data=data)
    assert SECRET not in repr(out)
    assert out["weird"]["deeper"]["payload"] == R


def test_mask_is_total_never_raises():
    # An object that explodes on iteration must fail closed, not propagate.
    class Boom:
        def __repr__(self):  # pragma: no cover - defensive
            raise RuntimeError("nope")

    assert obs._mask_content(data=Boom()) == R
