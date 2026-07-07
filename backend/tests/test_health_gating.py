"""/health must gate at the transport level: 200 only when actually healthy.

Compose healthchecks (`curl -f`), `depends_on: service_healthy`, and Traefik's
health-aware routing key off the status code — a degraded instance (Neo4j
unreachable, schema unconfirmed) answering 200 defeats every deploy gate.
"""

from app import main as app_main


class TestHealthGating:
    def test_healthy_returns_200(self, client, mock_neo4j):
        mock_neo4j.verify_connectivity.return_value = True
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "healthy"
        assert body["neo4j_connected"] is True
        assert body["schema_initialized"] is True

    def test_neo4j_down_returns_503(self, client, mock_neo4j):
        mock_neo4j.verify_connectivity.return_value = False
        r = client.get("/health")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["neo4j_connected"] is False

    def test_schema_uninitialized_returns_503(self, client, mock_neo4j, monkeypatch):
        mock_neo4j.verify_connectivity.return_value = True
        monkeypatch.setattr(app_main, "_schema_initialized", False)
        r = client.get("/health")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["schema_initialized"] is False
