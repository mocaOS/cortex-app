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
