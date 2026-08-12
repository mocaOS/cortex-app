"""SSRF egress-guard tests for skill-originated HTTP.

A skill's tools.json (execution.type=http) and install-from-URL both fetch
server-side on user-influenced URLs, so they run through the same ssrf_guard
policy as the agent's built-in http_request tool. These tests prove blocked
targets (metadata/loopback/private) never reach httpx, and that the operator
escape hatches (SKILL_HTTP_ALLOW_PRIVATE / SKILL_HTTP_ALLOWED_HOSTS) still work.

Literal IPs keep the tests hermetic — no DNS needed for link-local/loopback/
RFC1918 classification.
"""

from __future__ import annotations

import pytest

from app.services.skill_service import SkillService


class _NetworkRaiser:
    """Any httpx construction means the guard let a request through."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("httpx client constructed for a blocked URL")


class _FakeResponse:
    def __init__(self, text: str = "ok", status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        pass


class _FakeClient:
    """Minimal async stand-in recording which requests would have run."""

    def __init__(self, *args, **kwargs):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        self.calls.append(("GET", str(url)))
        return _FakeResponse()

    async def request(self, method, url, **kwargs):
        self.calls.append((method, str(url)))
        return _FakeResponse()


# --- _execute_http_tool -------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (link-local)
        "http://127.0.0.1:8000/internal",            # loopback
        "http://[::1]/admin",                        # ipv6 loopback
        "http://10.0.0.5:9000/",                     # RFC1918 (private disallowed by default)
    ],
)
async def test_http_tool_blocks_internal_targets(monkeypatch, url):
    monkeypatch.setattr("app.services.skill_service.httpx.AsyncClient", _NetworkRaiser)
    svc = SkillService()
    result = await svc._execute_http_tool(
        {"type": "http", "method": "GET", "url": url}, {}
    )
    assert result.startswith("Error: request blocked")


async def test_http_tool_allow_private_escape_hatch(_isolate_env, monkeypatch):
    _isolate_env.skill_http_allow_private = True
    monkeypatch.setattr("app.services.skill_service.httpx.AsyncClient", _FakeClient)
    svc = SkillService()
    result = await svc._execute_http_tool(
        {"type": "http", "method": "GET", "url": "http://10.0.0.5:9000/"}, {}
    )
    assert result == "ok"


async def test_http_tool_allowlisted_host_still_allowed(_isolate_env, monkeypatch):
    """SKILL_HTTP_ALLOWED_HOSTS exempts one host (allowlist short-circuits DNS)."""
    _isolate_env.skill_http_allowed_hosts = "internal.example"
    monkeypatch.setattr("app.services.skill_service.httpx.AsyncClient", _FakeClient)
    svc = SkillService()
    result = await svc._execute_http_tool(
        {"type": "http", "method": "POST", "url": "http://internal.example/hook"},
        {"q": 1},
    )
    assert result == "ok"


# --- install_from_url ---------------------------------------------------------


async def test_install_from_url_blocks_metadata_target(monkeypatch):
    monkeypatch.setattr("app.services.skill_service.httpx.AsyncClient", _NetworkRaiser)
    svc = SkillService()
    with pytest.raises(ValueError, match="Skill URL not allowed"):
        await svc.install_from_url("http://169.254.169.254/SKILL.md")


async def test_install_from_url_blocks_loopback(monkeypatch):
    monkeypatch.setattr("app.services.skill_service.httpx.AsyncClient", _NetworkRaiser)
    svc = SkillService()
    with pytest.raises(ValueError, match="Skill URL not allowed"):
        await svc.install_from_url("http://127.0.0.1:8080/SKILL.md")
