"""Unit tests for the crawl4ai client (MDHarvest powered by Crawl4ai).

Pure-logic coverage — link normalization (same-host, skip-patterns, relative
resolution), title extraction, and markdown response parsing — none of which
touch the network.
"""

import pytest

from app.services import crawl_client


class TestNormalizeLink:
    BASE = "https://example.com/blog/post"

    def test_resolves_relative(self):
        assert crawl_client._normalize_link("/page?x=1", self.BASE) == "https://example.com/page?x=1"

    def test_keeps_absolute_same_host(self):
        assert crawl_client._normalize_link("https://example.com/other", self.BASE) == "https://example.com/other"

    def test_drops_cross_host(self):
        assert crawl_client._normalize_link("https://other.com/p", self.BASE) is None

    def test_drops_skip_pattern(self):
        assert crawl_client._normalize_link("/login", self.BASE) is None
        assert crawl_client._normalize_link("/account/settings", self.BASE) is None

    def test_drops_asset_extensions(self):
        assert crawl_client._normalize_link("/a/b.png", self.BASE) is None
        assert crawl_client._normalize_link("/x.pdf?v=2", self.BASE) is None

    def test_strips_fragment(self):
        assert crawl_client._normalize_link("/p#section", self.BASE) == "https://example.com/p"

    def test_empty(self):
        assert crawl_client._normalize_link("", self.BASE) is None


class TestTitleFromMarkdown:
    def test_uses_first_h1(self):
        md = "intro\n# Real Title\nbody"
        assert crawl_client._title_from_markdown(md, "https://x.com/a") == "Real Title"

    def test_falls_back_to_url(self):
        md = "no heading here"
        assert crawl_client._title_from_markdown(md, "https://x.com/my-page") == "my page"

    def test_url_host_when_no_path(self):
        assert crawl_client._title_from_url("https://example.com/") == "example.com"


@pytest.mark.asyncio
async def test_crawl_markdown_parses_string(monkeypatch):
    async def fake_post(path, payload, op):
        assert path == "/md"
        assert payload["c"] == "0"  # cache-bypass enforced
        assert payload["f"] == "fit"
        return {"success": True, "markdown": "# Hello\n\nworld"}

    monkeypatch.setattr(crawl_client, "_post", fake_post)
    res = await crawl_client.crawl_markdown("https://x.com/p", content_filter="fit")
    assert res["title"] == "Hello"
    assert "world" in res["markdown"]
    assert res["url"] == "https://x.com/p"


@pytest.mark.asyncio
async def test_crawl_markdown_empty_raises(monkeypatch):
    async def fake_post(path, payload, op):
        return {"success": True, "markdown": "   "}

    monkeypatch.setattr(crawl_client, "_post", fake_post)
    with pytest.raises(crawl_client.CrawlUnavailableError):
        await crawl_client.crawl_markdown("https://x.com/p")


@pytest.mark.asyncio
async def test_discover_links_filters(monkeypatch):
    async def fake_post(path, payload, op):
        assert path == "/crawl"
        assert payload["urls"] == ["https://example.com/start"]
        return {"results": [{"success": True, "links": {"internal": [
            {"href": "https://example.com/a", "text": "Page A"},
            {"href": "/b", "text": "Page B"},
            {"href": "https://other.com/x", "text": "External"},
            {"href": "/login", "text": "Login"},
            {"href": "https://example.com/start", "text": "Self"},
        ]}}]}

    monkeypatch.setattr(crawl_client, "_post", fake_post)
    out = await crawl_client.discover_links("https://example.com/start")
    urls = [l["url"] for l in out["links"]]
    assert urls == ["https://example.com/a", "https://example.com/b"]
    assert out["domain"] == "example.com"


# --- resolve_redirect_chain (SSRF pre-flight for crawl4ai) -------------------
#
# crawl4ai follows redirects in its own container, so the chain is walked here
# with per-hop ssrf_guard validation before a URL is handed over. Literal IPs
# keep these hermetic — no DNS needed for global/link-local classification.

class _ScriptedResponse:
    def __init__(self, status, location=None):
        self.status_code = status
        self.headers = {"location": location} if location else {}


class _ScriptedStream:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        return False


def _scripted_client(monkeypatch, routes, calls):
    """Patch crawl_client's httpx with a fake serving {(method, url): (status, location) | Exception}."""

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def head(self, url, **kwargs):
            calls.append(("HEAD", url))
            outcome = routes[("HEAD", url)]
            if isinstance(outcome, Exception):
                raise outcome
            return _ScriptedResponse(*outcome)

        def stream(self, method, url, **kwargs):
            calls.append((method, url))
            outcome = routes[(method, url)]
            if isinstance(outcome, Exception):
                raise outcome
            return _ScriptedStream(_ScriptedResponse(*outcome))

    monkeypatch.setattr(crawl_client.httpx, "AsyncClient", _Client)


@pytest.mark.asyncio
async def test_resolve_chain_blocks_metadata_hop(monkeypatch):
    """A public URL bouncing to cloud metadata must never reach crawl4ai."""
    routes = {("HEAD", "http://93.184.216.34/start"): (301, "http://169.254.169.254/latest/meta-data")}
    _scripted_client(monkeypatch, routes, [])
    from app.services.ssrf_guard import SSRFError
    with pytest.raises(SSRFError):
        await crawl_client.resolve_redirect_chain("http://93.184.216.34/start")


@pytest.mark.asyncio
async def test_resolve_chain_blocks_loopback_hop(monkeypatch):
    routes = {("HEAD", "http://93.184.216.34/x"): (302, "http://127.0.0.1:8000/admin")}
    _scripted_client(monkeypatch, routes, [])
    from app.services.ssrf_guard import SSRFError
    with pytest.raises(SSRFError):
        await crawl_client.resolve_redirect_chain("http://93.184.216.34/x")


@pytest.mark.asyncio
async def test_resolve_chain_returns_final_public_url(monkeypatch):
    """Relative and absolute public hops resolve to the final fetch target."""
    routes = {
        ("HEAD", "http://93.184.216.34/a"): (301, "/b"),  # relative redirect
        ("HEAD", "http://93.184.216.34/b"): (301, "http://93.184.216.34/c"),
        ("HEAD", "http://93.184.216.34/c"): (200, None),
    }
    _scripted_client(monkeypatch, routes, [])
    final = await crawl_client.resolve_redirect_chain("http://93.184.216.34/a")
    assert final == "http://93.184.216.34/c"


@pytest.mark.asyncio
async def test_resolve_chain_detects_loop(monkeypatch):
    routes = {
        ("HEAD", "http://93.184.216.34/x"): (301, "http://93.184.216.34/y"),
        ("HEAD", "http://93.184.216.34/y"): (301, "http://93.184.216.34/x"),
    }
    _scripted_client(monkeypatch, routes, [])
    with pytest.raises(crawl_client.CrawlUnavailableError, match="redirect loop"):
        await crawl_client.resolve_redirect_chain("http://93.184.216.34/x")


@pytest.mark.asyncio
async def test_resolve_chain_head_405_falls_back_to_get(monkeypatch):
    calls = []
    routes = {
        ("HEAD", "http://93.184.216.34/a"): (405, None),
        ("GET", "http://93.184.216.34/a"): (301, "http://93.184.216.34/b"),
        ("HEAD", "http://93.184.216.34/b"): (200, None),
    }
    _scripted_client(monkeypatch, routes, calls)
    final = await crawl_client.resolve_redirect_chain("http://93.184.216.34/a")
    assert final == "http://93.184.216.34/b"
    assert ("GET", "http://93.184.216.34/a") in calls


@pytest.mark.asyncio
async def test_resolve_chain_fails_open_on_transport_error(monkeypatch):
    """Probe failures must not break legitimate imports: last validated URL wins."""
    import httpx as real_httpx

    routes = {("HEAD", "http://93.184.216.34/slow"): real_httpx.ConnectError("boom")}
    _scripted_client(monkeypatch, routes, [])
    final = await crawl_client.resolve_redirect_chain("http://93.184.216.34/slow")
    assert final == "http://93.184.216.34/slow"  # crawl4ai proceeds as before
