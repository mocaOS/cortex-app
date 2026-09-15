"""Tests for POST /api/llm/completions — the admin-gated raw completion
passthrough (used by trusted first-party services like cortex-chat's
personality generator).

Covers: non-stream + stream happy paths (OpenAI-chunk SSE framing +
[DONE] terminator), admin gating (401 without a key), request validation,
and the monthly-quota 429 gate.
"""

import json

import pytest


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeChunk:
    def __init__(self, token):
        self._token = token

    def model_dump_json(self):
        return json.dumps({"choices": [{"delta": {"content": self._token}}]})


class _FakeStream:
    def __init__(self, tokens):
        self._tokens = list(tokens)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._tokens:
            raise StopAsyncIteration
        return _FakeChunk(self._tokens.pop(0))


class _FakeCompletions:
    async def create(self, *, stream=False, **kwargs):
        if stream:
            return _FakeStream(["Hello", " world"])
        return _FakeCompletion("Hello world")


class _FakeClient:
    def __init__(self):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions()


@pytest.fixture
def fake_llm(monkeypatch):
    """Patch the client factory at its main.py call site."""
    client = _FakeClient()
    monkeypatch.setattr("app.main.make_async_openai_client", lambda **kw: client)
    return client


def test_non_stream_completion(client, fake_llm):
    resp = client.post(
        "/api/llm/completions",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["content"] == "Hello world"
    assert "model" in body


def test_stream_completion_openai_chunks(client, fake_llm):
    with client.stream(
        "POST",
        "/api/llm/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        raw = "".join(resp.iter_text())

    tokens = []
    saw_done = False
    for line in raw.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload == "[DONE]":
            saw_done = True
            continue
        data = json.loads(payload)
        delta = data.get("choices", [{}])[0].get("delta", {})
        if delta.get("content"):
            tokens.append(delta["content"])
    assert "".join(tokens) == "Hello world"
    assert saw_done


def test_requires_admin_key(mock_neo4j, mock_processors, fake_llm):
    """No dependency override here — a keyless request must 401."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as anonymous:
        resp = anonymous.post(
            "/api/llm/completions",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 401


def test_rejects_invalid_role(client, fake_llm):
    resp = client.post(
        "/api/llm/completions",
        json={"messages": [{"role": "tool", "content": "hi"}]},
    )
    assert resp.status_code == 422


def test_quota_gate_returns_429(client, fake_llm, monkeypatch):
    async def exhausted():
        return True

    monkeypatch.setattr("app.main._quota_exceeded", exhausted)
    resp = client.post(
        "/api/llm/completions",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


class _RecordingCompletions(_FakeCompletions):
    """Fake that records the kwargs passed to create()."""

    def __init__(self):
        self.calls = []

    async def create(self, *, stream=False, **kwargs):
        self.calls.append({"stream": stream, **kwargs})
        return await super().create(stream=stream, **kwargs)


@pytest.fixture
def recording_llm(monkeypatch):
    """Venice + thinking-by-default Qwen: the config that hung cortex-chat's
    personality generator (hidden reasoning ate the whole token budget)."""
    from app.services import reasoning_config
    from app.services.llm_config import LLMConfig

    client = _FakeClient()
    client.chat.completions = _RecordingCompletions()
    monkeypatch.setattr("app.main.make_async_openai_client", lambda **kw: client)
    monkeypatch.setattr(
        "app.main.get_llm_config",
        lambda fast_mode=False: LLMConfig(
            api_key="k", base_url="https://api.venice.ai/api/v1", model="qwen3-6-35b-a3b"
        ),
    )
    reasoning_config._unsupported_reasoning_models.clear()
    return client.chat.completions


def test_applies_default_reasoning_mode_like_chat(client, recording_llm, monkeypatch):
    """The passthrough must follow DEFAULT_REASONING_MODE (default OFF) exactly
    like /api/ask — otherwise Qwen-class models think for minutes in a
    `reasoning_content` channel the caller never sees."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "default_reasoning_mode", "off")

    resp = client.post(
        "/api/llm/completions",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "Hello world"

    (call,) = recording_llm.calls
    assert call["model"] == "qwen3-6-35b-a3b"
    assert call["extra_body"]["venice_parameters"]["disable_thinking"] is True

    # Streaming takes the same path.
    with client.stream(
        "POST",
        "/api/llm/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
    ) as resp:
        assert resp.status_code == 200
        "".join(resp.iter_text())
    assert recording_llm.calls[-1]["stream"] is True
    assert recording_llm.calls[-1]["extra_body"]["venice_parameters"]["disable_thinking"] is True


def test_reasoning_mode_auto_injects_nothing(client, recording_llm, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "default_reasoning_mode", "auto")
    resp = client.post(
        "/api/llm/completions",
        json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
    )
    assert resp.status_code == 200
    (call,) = recording_llm.calls
    assert "extra_body" not in call
    assert "reasoning_effort" not in call
