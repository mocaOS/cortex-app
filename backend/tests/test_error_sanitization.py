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
