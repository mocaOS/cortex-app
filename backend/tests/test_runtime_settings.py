"""Endpoint tests for the admin runtime-settings toggle (ingestion injection scan).

The scan is an experimental feature gated behind ENABLE_INGESTION_INJECTION_SCAN
(default off). With the gate off the effective value is always false and PATCH
rejects the toggle; with it on, GET surfaces the effective value and PATCH
persists an override. Neo4j is mocked with a tiny in-memory store to simulate
persistence.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def scan_enabled(_isolate_env):
    """Opt in to the experimental ingestion injection scan for a test."""
    _isolate_env.enable_ingestion_injection_scan = True
    return _isolate_env


def test_get_config_scan_absent_by_default(client, mock_neo4j):
    """Default state: master flag off → feature reported absent, effective false."""
    mock_neo4j.get_runtime_setting.side_effect = lambda key, default: default
    r = client.get("/api/admin/config")
    assert r.status_code == 200
    body = r.json()
    assert body["enable_ingestion_injection_scan"] is False
    assert body["ingestion_injection_scan"] is False


def test_patch_rejected_when_scan_disabled(client, mock_neo4j):
    """Default state: toggling the scan is rejected, nothing is persisted."""
    r = client.patch("/api/admin/config", json={"ingestion_injection_scan": True})
    assert r.status_code == 400
    assert "experimental" in r.json()["detail"].lower()
    mock_neo4j.set_runtime_setting.assert_not_called()


def test_get_config_includes_injection_scan(client, mock_neo4j, scan_enabled):
    mock_neo4j.get_runtime_setting.return_value = True
    r = client.get("/api/admin/config")
    assert r.status_code == 200
    body = r.json()
    assert body["enable_ingestion_injection_scan"] is True
    assert body["ingestion_injection_scan"] is True


def test_patch_toggles_injection_scan(client, mock_neo4j, scan_enabled):
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
