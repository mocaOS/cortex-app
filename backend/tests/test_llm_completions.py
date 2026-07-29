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
