"""5xx sanitization: production hides exception internals, development keeps them."""

from app.config import get_settings

SECRET = "bolt://user:hunter2@neo4j:7687"
GENERIC = "Internal server error. Check server logs for details."


def _force_search_500(mock_processors):
    mock_processors.query.hybrid_search.side_effect = RuntimeError(SECRET)


class TestProduction:
    def test_500_detail_is_generic(self, client, mock_processors, monkeypatch):
        monkeypatch.setattr(get_settings(), "environment", "production")
        _force_search_500(mock_processors)
        resp = client.post("/api/search", json={"query": "hello"})
        assert resp.status_code == 500
        body = resp.json()
        assert SECRET not in body["detail"]
        assert "request_id" in body

    def test_4xx_detail_untouched(self, client, monkeypatch):
        monkeypatch.setattr(get_settings(), "environment", "production")
        resp = client.get("/api/integrations/git/connections/nope")
        # git integration may be disabled (400) or connection missing (404);
        # either way the 4xx detail must pass through unsanitized.
        assert resp.status_code in (400, 404)
        assert resp.json()["detail"] != GENERIC


class TestDevelopment:
    def test_500_detail_preserved(self, client, mock_processors, monkeypatch):
        monkeypatch.setattr(get_settings(), "environment", "development")
        _force_search_500(mock_processors)
        resp = client.post("/api/search", json={"query": "hello"})
        assert resp.status_code == 500
        assert SECRET in resp.json()["detail"]


class TestStructured5xxPassThrough:
    """A handler that raises a dict detail with an `error` code authored that
    body deliberately (504 deadline_exceeded, 500 ask_failed) — production must
    keep it, or the documented codes never reach clients."""

    def test_structured_detail_survives_production(self, client, mock_processors, monkeypatch):
        import asyncio
        from unittest.mock import AsyncMock

        monkeypatch.setattr(get_settings(), "environment", "production")
        mock_processors.query.rag_query = AsyncMock(side_effect=asyncio.TimeoutError())
        resp = client.post("/api/ask", json={"question": "hello"})
        assert resp.status_code == 504
        body = resp.json()
        assert body["detail"]["error"] == "deadline_exceeded"
        assert "request_id" in body

    def test_free_text_500_still_sanitized(self, client, mock_processors, monkeypatch):
        monkeypatch.setattr(get_settings(), "environment", "production")
        _force_search_500(mock_processors)
        assert client.post("/api/search", json={"query": "x"}).json()["detail"] == GENERIC
