"""Endpoint tests for the admin runtime-settings toggle (ingestion injection scan).

Verifies the GET config surfaces the effective value and the PATCH persists an
override. Neo4j is mocked with a tiny in-memory store to simulate persistence.
"""

from __future__ import annotations


def test_get_config_includes_injection_scan(client, mock_neo4j):
    mock_neo4j.get_runtime_setting.return_value = True
    r = client.get("/api/admin/config")
    assert r.status_code == 200
    assert r.json()["ingestion_injection_scan"] is True


def test_patch_toggles_injection_scan(client, mock_neo4j):
    store: dict = {}
    mock_neo4j.set_runtime_setting.side_effect = lambda key, value: store.__setitem__(key, value)
    mock_neo4j.get_runtime_setting.side_effect = lambda key, default: store.get(key, default)

    # Disable
    r = client.patch("/api/admin/config", json={"ingestion_injection_scan": False})
    assert r.status_code == 200
    assert r.json()["ingestion_injection_scan"] is False
    assert store["ingestion_injection_scan"] is False

    # Re-enable
    r2 = client.patch("/api/admin/config", json={"ingestion_injection_scan": True})
    assert r2.status_code == 200
    assert r2.json()["ingestion_injection_scan"] is True


def test_patch_without_fields_is_noop(client, mock_neo4j):
    mock_neo4j.get_runtime_setting.side_effect = lambda key, default: default
    r = client.patch("/api/admin/config", json={})
    assert r.status_code == 200
    mock_neo4j.set_runtime_setting.assert_not_called()


def test_get_config_includes_prompt_guard(client, mock_neo4j):
    mock_neo4j.get_runtime_setting.side_effect = lambda key, default: default
    r = client.get("/api/admin/config")
    assert r.status_code == 200
    assert "prompt_guard" in r.json()


def test_patch_toggles_prompt_guard(client, mock_neo4j):
    store: dict = {}
    mock_neo4j.set_runtime_setting.side_effect = lambda key, value: store.__setitem__(key, value)
    mock_neo4j.get_runtime_setting.side_effect = lambda key, default: store.get(key, default)

    r = client.patch("/api/admin/config", json={"prompt_guard": False})
    assert r.status_code == 200
    assert r.json()["prompt_guard"] is False
    assert store["prompt_guard"] is False

    r2 = client.patch("/api/admin/config", json={"prompt_guard": True})
    assert r2.status_code == 200
    assert r2.json()["prompt_guard"] is True
