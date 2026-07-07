"""Unit tests for auth_service key generation, hashing, and access control.

This security-critical module previously had no direct coverage — the suite only
exercised it indirectly via the `client` fixture's dependency overrides. These
tests pin the pure logic: SHA-256 hashing + constant-time verify, key generation
(prefix/length/uniqueness), permission checks, collection scoping, and the
fail-closed behaviour of validate_api_key.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models import APIKeyPermission
from app.services.auth_service import (
    AuthResult,
    generate_api_key,
    hash_api_key,
    invalidate_api_key_cache,
    require_read_permission,
    validate_api_key,
    validate_collection_access,
    verify_api_key_hash,
)


# --- hashing -----------------------------------------------------------------

def test_hash_is_sha256_hex_and_deterministic():
    h = hash_api_key("cortex_ro_abc")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert h == hash_api_key("cortex_ro_abc")


def test_verify_hash_constant_time_match_and_mismatch():
    key = "cortex_rw_" + "a" * 64
    assert verify_api_key_hash(key, hash_api_key(key)) is True
    assert verify_api_key_hash(key + "x", hash_api_key(key)) is False


# --- key generation ----------------------------------------------------------

def test_generate_api_key_prefix_length_and_identifier():
    full, prefix = generate_api_key("cortex_rw_")
    assert full.startswith("cortex_rw_")
    assert prefix == full[:12]
    # 32 random bytes -> 64 hex chars after the prefix
    assert len(full) == len("cortex_rw_") + 64


def test_generate_api_key_is_unique():
    keys = {generate_api_key("cortex_ro_")[0] for _ in range(50)}
    assert len(keys) == 50


# --- permission checks -------------------------------------------------------

def test_admin_has_all_permissions():
    admin = AuthResult(is_authenticated=True, is_admin=True)
    assert admin.has_permission(APIKeyPermission.READ)
    assert admin.has_permission(APIKeyPermission.MANAGE)


def test_scoped_key_only_granted_permissions():
    ro = AuthResult(is_authenticated=True, permissions=[APIKeyPermission.READ])
    assert ro.has_permission(APIKeyPermission.READ)
    assert not ro.has_permission(APIKeyPermission.MANAGE)


# --- collection scoping ------------------------------------------------------

def test_can_access_collection_admin_and_all_scope():
    assert AuthResult(is_authenticated=True, is_admin=True).can_access_collection("c1")
    assert AuthResult(is_authenticated=True, collection_scope="all").can_access_collection("c1")


def test_restricted_key_collection_access():
    auth = AuthResult(
        is_authenticated=True,
        collection_scope="restricted",
        allowed_collections=["c1", "c2"],
    )
    assert auth.can_access_collection("c1") is True
    assert auth.can_access_collection("c3") is False
    # None == "query all accessible" is allowed even for restricted keys
    assert auth.can_access_collection(None) is True


def test_get_collection_filter():
    assert AuthResult(is_authenticated=True, is_admin=True).get_collection_filter() is None
    assert AuthResult(is_authenticated=True, collection_scope="all").get_collection_filter() is None
    restricted = AuthResult(
        is_authenticated=True, collection_scope="restricted", allowed_collections=["c1"]
    )
    assert restricted.get_collection_filter() == ["c1"]


def test_validate_collection_access_raises_403_when_denied():
    auth = AuthResult(
        is_authenticated=True, collection_scope="restricted", allowed_collections=["c1"]
    )
    with pytest.raises(HTTPException) as exc:
        validate_collection_access(auth, "c9", action="upload to")
    assert exc.value.status_code == 403
    assert "upload to collection: c9" in exc.value.detail
    # allowed collection does not raise
    validate_collection_access(auth, "c1")


# --- validate_api_key --------------------------------------------------------

async def test_validate_api_key_missing_key_fails_closed():
    res = await validate_api_key(None)
    assert res.is_authenticated is False and res.error


async def test_validate_api_key_admin_path(_isolate_env):
    # _isolate_env sets settings.admin_api_key = "test-admin-key"
    res = await validate_api_key("test-admin-key")
    assert res.is_authenticated and res.is_admin and res.key_id == "admin"
    assert APIKeyPermission.READ in res.permissions
    assert APIKeyPermission.MANAGE in res.permissions


async def test_validate_api_key_unknown_key_returns_invalid(mock_neo4j):
    mock_neo4j.get_api_key_by_prefix.return_value = []
    res = await validate_api_key("cortex_ro_" + "b" * 64)
    assert res.is_authenticated is False
    assert res.service_error is False  # authoritative rejection, not an outage


async def test_validate_api_key_fails_closed_on_backend_error(mock_neo4j):
    mock_neo4j.get_api_key_by_prefix.side_effect = RuntimeError("neo4j down")
    res = await validate_api_key("cortex_ro_" + "c" * 64)
    # Fail closed, but flagged as a service outage — dependencies answer 503,
    # never 401 (a Neo4j blip must not read as "invalid key" to clients).
    assert res.is_authenticated is False
    assert res.service_error is True


async def test_validate_api_key_generated_key_success_path(mock_neo4j):
    key = "cortex_rw_" + "d" * 64
    mock_neo4j.get_api_key_by_prefix.return_value = [
        {
            "id": "key_abc",
            "name": "CI key",
            "key_hash": hash_api_key(key),
            "permissions": ["read", "manage", "bogus"],  # bogus filtered out
            "collection_scope": "restricted",
            "allowed_collections": ["c1"],
        }
    ]
    res = await validate_api_key(key)
    assert res.is_authenticated and res.is_admin is False
    assert res.key_id == "key_abc"
    assert res.permissions == [APIKeyPermission.READ, APIKeyPermission.MANAGE]
    assert res.collection_scope == "restricted" and res.allowed_collections == ["c1"]
    mock_neo4j.update_api_key_last_used.assert_called_once_with("key_abc")


# --- 401 vs 503 mapping in the require_* dependencies -------------------------

async def test_dependency_returns_503_when_auth_store_down(mock_neo4j):
    mock_neo4j.get_api_key_by_prefix.side_effect = RuntimeError("neo4j down")
    with pytest.raises(HTTPException) as exc:
        await require_read_permission("cortex_ro_" + "e" * 64)
    assert exc.value.status_code == 503
    assert exc.value.headers.get("Retry-After") == "2"


async def test_dependency_returns_401_for_unknown_key(mock_neo4j):
    mock_neo4j.get_api_key_by_prefix.return_value = []
    with pytest.raises(HTTPException) as exc:
        await require_read_permission("cortex_ro_" + "f" * 64)
    assert exc.value.status_code == 401


# --- validation cache ----------------------------------------------------------

def _stored_key(key: str, key_id: str = "key_cached") -> dict:
    return {
        "id": key_id,
        "name": "cache test",
        "key_hash": hash_api_key(key),
        "permissions": ["read"],
        "collection_scope": "all",
        "allowed_collections": [],
    }


async def test_repeat_validations_hit_neo4j_once(mock_neo4j):
    key = "cortex_ro_" + "1" * 64
    mock_neo4j.get_api_key_by_prefix.return_value = [_stored_key(key)]
    first = await validate_api_key(key)
    second = await validate_api_key(key)
    assert first.is_authenticated and second.is_authenticated
    assert mock_neo4j.get_api_key_by_prefix.call_count == 1


async def test_cache_invalidation_forces_revalidation(mock_neo4j):
    key = "cortex_ro_" + "2" * 64
    mock_neo4j.get_api_key_by_prefix.return_value = [_stored_key(key)]
    await validate_api_key(key)
    invalidate_api_key_cache()
    # Simulate revocation: the key vanishes from the store.
    mock_neo4j.get_api_key_by_prefix.return_value = []
    res = await validate_api_key(key)
    assert res.is_authenticated is False
    assert mock_neo4j.get_api_key_by_prefix.call_count == 2


async def test_cache_disabled_with_zero_ttl(mock_neo4j, _isolate_env):
    _isolate_env.api_key_cache_ttl_seconds = 0
    key = "cortex_ro_" + "3" * 64
    mock_neo4j.get_api_key_by_prefix.return_value = [_stored_key(key)]
    await validate_api_key(key)
    await validate_api_key(key)
    assert mock_neo4j.get_api_key_by_prefix.call_count == 2


async def test_invalid_keys_are_not_cached(mock_neo4j):
    key = "cortex_ro_" + "4" * 64
    mock_neo4j.get_api_key_by_prefix.return_value = []
    assert (await validate_api_key(key)).is_authenticated is False
    # Key gets minted between attempts — next validation must see it.
    mock_neo4j.get_api_key_by_prefix.return_value = [_stored_key(key)]
    assert (await validate_api_key(key)).is_authenticated is True


# --- last_used_at bookkeeping is telemetry, not auth ---------------------------

async def test_last_used_write_skipped_when_fresh(mock_neo4j):
    from datetime import datetime, timezone

    key = "cortex_ro_" + "5" * 64
    stored = _stored_key(key)
    stored["last_used_at"] = datetime.now(timezone.utc)
    mock_neo4j.get_api_key_by_prefix.return_value = [stored]
    res = await validate_api_key(key)
    assert res.is_authenticated
    mock_neo4j.update_api_key_last_used.assert_not_called()


async def test_last_used_write_failure_does_not_reject_key(mock_neo4j):
    key = "cortex_ro_" + "6" * 64
    mock_neo4j.get_api_key_by_prefix.return_value = [_stored_key(key)]
    mock_neo4j.update_api_key_last_used.side_effect = RuntimeError("lock timeout")
    res = await validate_api_key(key)
    assert res.is_authenticated is True
    assert res.service_error is False
