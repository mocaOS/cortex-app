"""Tests for MAX_COLLECTIONS enforcement on POST /api/collections.

PRICING.md §4.4 promises that MAX_COLLECTIONS caps the total collection count
globally (the auto-created `default` collection counts toward the cap), with
sentinel `0` meaning unlimited.
"""

from __future__ import annotations


def _payload(name: str = "My collection") -> dict:
    return {"name": name, "description": "test"}


# ---------------------------------------------------------------------------
# /api/collections
# ---------------------------------------------------------------------------

def test_create_collection_unlimited_when_max_collections_zero(client, mock_neo4j):
    mock_neo4j.set_collection_count(5_000)

    response = client.post("/api/collections", json=_payload())

    assert response.status_code == 200, response.text
    assert response.json()["id"] == "fake-collection-id"


def test_create_collection_allowed_just_below_cap(
    client, mock_neo4j, override_max_collections,
):
    override_max_collections(10)
    mock_neo4j.set_collection_count(9)

    response = client.post("/api/collections", json=_payload())

    assert response.status_code == 200, response.text
    assert response.json()["id"] == "fake-collection-id"


def test_create_collection_rejected_at_cap(
    client, mock_neo4j, override_max_collections,
):
    override_max_collections(10)
    mock_neo4j.set_collection_count(10)

    response = client.post("/api/collections", json=_payload())

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert "Collection limit reached" in detail
    assert "10" in detail
    mock_neo4j.create_collection.assert_not_called()


def test_create_collection_rejected_over_cap_defensive(
    client, mock_neo4j, override_max_collections,
):
    override_max_collections(10)
    mock_neo4j.set_collection_count(11)

    response = client.post("/api/collections", json=_payload())

    assert response.status_code == 403
    assert "Collection limit reached" in response.json()["detail"]
    mock_neo4j.create_collection.assert_not_called()
