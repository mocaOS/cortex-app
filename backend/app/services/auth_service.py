"""Authentication service for API key validation and permission checking.

This module provides:
- Admin API key validation against environment variables
- Generated API key validation against Neo4j stored keys
- Permission checking for different access levels (READ, MANAGE, ADMIN)
"""

import secrets
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional, List, Tuple
from enum import Enum

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from app.config import get_settings
from app.services.neo4j_service import get_neo4j_service
from app.models import APIKeyPermission

logger = logging.getLogger(__name__)

# API Key header extractor
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# The ONLY endpoints a monetized (x402-priced) key may call. Priced keys are
# public by design, so plain READ is too broad: it would hand out the raw
# corpus (document content/file download) for free next to the paid retrieval
# endpoints. /api/stats is deliberately NOT listed — internal data.
MONETIZED_KEY_ALLOWED_PATHS = frozenset({
    "/api/search",
    "/api/ask",
    "/api/ask/stream",
    "/api/ask/stream/thinking",
})


class AuthResult:
    """Result of an authentication attempt."""

    def __init__(
        self,
        is_authenticated: bool,
        is_admin: bool = False,
        permissions: List[APIKeyPermission] = None,
        key_id: Optional[str] = None,
        error: Optional[str] = None,
        key_name: Optional[str] = None,
        collection_scope: str = "all",
        allowed_collections: List[str] = None,
        service_error: bool = False,
        price_per_query: Optional[str] = None
    ):
        self.is_authenticated = is_authenticated
        self.is_admin = is_admin
        self.permissions = permissions or []
        self.key_id = key_id
        self.error = error
        self.key_name = key_name
        self.collection_scope = collection_scope
        self.allowed_collections = allowed_collections or []
        # True when the key could not be checked at all (auth store down),
        # as opposed to checked-and-rejected. Callers map this to 503, never
        # 401 — a transient Neo4j failure must not read as "invalid key".
        self.service_error = service_error
        # x402 price in human asset units. A priced key is a "monetized public
        # key": read-only, restricted to MONETIZED_KEY_ALLOWED_PATHS, and
        # gated by enforce_x402_payment (x402_service).
        self.price_per_query = price_per_query

    @property
    def is_monetized(self) -> bool:
        """True for x402-priced keys (never for admin)."""
        return bool(self.price_per_query) and not self.is_admin

    def has_permission(self, permission: APIKeyPermission) -> bool:
        """Check if this auth result has a specific permission."""
        if self.is_admin:
            return True  # Admin has all permissions
        return permission in self.permissions
    
    def can_access_collection(self, collection_id: Optional[str]) -> bool:
        """Check if this auth result can access a specific collection.
        
        Args:
            collection_id: The collection ID to check. None means no specific collection
                          (e.g., a global query across all accessible collections).
        
        Returns:
            True if access is allowed, False otherwise.
        """
        # Admin always has access
        if self.is_admin:
            return True
        # Unrestricted keys can access everything
        if self.collection_scope == "all":
            return True
        # For restricted keys, None means "query all accessible collections" which is allowed
        if not collection_id:
            return True
        # Check if the specific collection is in the allowed list
        return collection_id in self.allowed_collections
    
    def get_collection_filter(self) -> Optional[List[str]]:
        """Get the list of collections to filter results by.
        
        Returns:
            None if unrestricted (no filtering needed), or list of allowed collection IDs.
        """
        if self.is_admin or self.collection_scope == "all":
            return None
        return self.allowed_collections


def hash_api_key(api_key: str) -> str:
    """Hash an API key using SHA-256 for secure storage."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def verify_api_key_hash(api_key: str, stored_hash: str) -> bool:
    """Verify an API key against its stored hash using constant-time comparison."""
    computed_hash = hash_api_key(api_key)
    return secrets.compare_digest(computed_hash, stored_hash)


def generate_api_key(prefix: str = "cortex_") -> Tuple[str, str]:
    """
    Generate a new API key with a prefix.

    Returns:
        Tuple of (full_key, key_prefix) where key_prefix is for identification
    """
    # Generate 32 random bytes = 64 hex characters
    random_part = secrets.token_hex(32)
    full_key = f"{prefix}{random_part}"
    key_prefix = full_key[:12]  # First 12 chars for identification
    return full_key, key_prefix


# ---------------------------------------------------------------------------
# Validation cache
#
# Every request validates its key twice (usage middleware + route dependency),
# and a chat page load fires several requests with the SAME group key — without
# a cache that is a burst of identical Neo4j reads plus last-used writes on one
# APIKey node. Successful validations are cached by key hash for a short TTL;
# negatives are never cached. CRUD mutations call invalidate_api_key_cache().
# ---------------------------------------------------------------------------

_validation_cache: dict[str, Tuple[float, AuthResult]] = {}

# Write last_used_at at most this often per key. The usage middleware already
# stamps it on every tracked request; this one only matters as a fallback.
_LAST_USED_MIN_INTERVAL_SECONDS = 60


def invalidate_api_key_cache() -> None:
    """Drop all cached validations (called on any API-key CRUD mutation)."""
    _validation_cache.clear()


def _cache_get(cache_key: str) -> Optional[AuthResult]:
    entry = _validation_cache.get(cache_key)
    if entry is None:
        return None
    expires_at, result = entry
    if time.monotonic() >= expires_at:
        _validation_cache.pop(cache_key, None)
        return None
    return result


def _cache_put(cache_key: str, result: AuthResult, ttl: float) -> None:
    # Only valid keys are cached, so this stays at "number of minted keys"
    # entries; the sweep is a cheap safety net, not a real eviction policy.
    if len(_validation_cache) > 512:
        now = time.monotonic()
        for k in [k for k, (exp, _) in _validation_cache.items() if exp <= now]:
            _validation_cache.pop(k, None)
    _validation_cache[cache_key] = (time.monotonic() + ttl, result)


def _last_used_is_fresh(last_used_at) -> bool:
    """True if the stored last_used_at is recent enough to skip the write.

    Neo4j returns a neo4j.time.DateTime; imports/tests may yield a native
    datetime or an ISO string. Anything unparsable counts as stale (write).
    """
    if last_used_at is None:
        return False
    try:
        value = last_used_at
        if hasattr(value, "to_native"):
            value = value.to_native()
        elif isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - value).total_seconds()
        return 0 <= age < _LAST_USED_MIN_INTERVAL_SECONDS
    except Exception:
        return False


async def validate_api_key(api_key: Optional[str]) -> AuthResult:
    """
    Validate an API key and return authentication result.

    Checks in order:
    1. Admin API key from environment
    2. Cached prior validation (short TTL)
    3. Generated API keys stored in Neo4j

    Fail-closed: never authenticates on error. But infra failures return
    `service_error=True` (callers answer 503), NOT a plain rejection — an
    unreachable auth store does not mean the credential is invalid.
    """
    if not api_key:
        return AuthResult(
            is_authenticated=False,
            error="API key required"
        )

    settings = get_settings()

    # Check if it's the admin API key
    if settings.admin_api_key and secrets.compare_digest(api_key, settings.admin_api_key):
        logger.debug("Admin API key authenticated")
        return AuthResult(
            is_authenticated=True,
            is_admin=True,
            permissions=[APIKeyPermission.READ, APIKeyPermission.MANAGE],
            key_id="admin"
        )

    cache_ttl = max(0, settings.api_key_cache_ttl_seconds)
    cache_key = hash_api_key(api_key)
    if cache_ttl:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    # Check generated API keys in Neo4j
    try:
        neo4j_service = get_neo4j_service()

        # Extract prefix from the key for lookup
        key_prefix = api_key[:12] if len(api_key) >= 12 else api_key

        # Get potential matching keys by prefix
        matching_keys = neo4j_service.get_api_key_by_prefix(key_prefix)

        for stored_key in matching_keys:
            if verify_api_key_hash(api_key, stored_key["key_hash"]):
                # Convert permission strings to enum
                permissions = [
                    APIKeyPermission(p) for p in stored_key["permissions"]
                    if p in [e.value for e in APIKeyPermission]
                ]

                # Get collection scope info
                collection_scope = stored_key.get("collection_scope", "all")
                allowed_collections = stored_key.get("allowed_collections", []) or []

                # Monetized keys are read-only BY CONSTRUCTION: the CRUD layer
                # rejects price+MANAGE, and this strip is defense in depth —
                # a hand-edited APIKey node can never authenticate read-write.
                price_per_query = stored_key.get("price_per_query") or None
                if price_per_query and APIKeyPermission.MANAGE in permissions:
                    logger.warning(
                        f"Monetized API key {stored_key['id']} carried MANAGE "
                        f"permission — stripped at validation time"
                    )
                    permissions = [p for p in permissions if p != APIKeyPermission.MANAGE]

                logger.debug(f"API key authenticated: {stored_key['name']} ({stored_key['id']})")
                result = AuthResult(
                    is_authenticated=True,
                    is_admin=False,
                    permissions=permissions,
                    key_id=stored_key["id"],
                    key_name=stored_key["name"],
                    collection_scope=collection_scope,
                    allowed_collections=allowed_collections,
                    price_per_query=price_per_query
                )

                # Telemetry, not auth: throttled, and a failure here must
                # never reject a key that just verified.
                if not _last_used_is_fresh(stored_key.get("last_used_at")):
                    try:
                        neo4j_service.update_api_key_last_used(stored_key["id"])
                    except Exception as e:
                        logger.warning(
                            f"Failed to update last_used_at for key "
                            f"{stored_key['id']}: {e}"
                        )

                if cache_ttl:
                    _cache_put(cache_key, result, cache_ttl)
                return result

        # No matching key found
        return AuthResult(
            is_authenticated=False,
            error="Invalid API key"
        )

    except Exception as e:
        logger.error(f"Error validating API key (auth store unavailable?): {e}")
        return AuthResult(
            is_authenticated=False,
            error="Authentication service temporarily unavailable",
            service_error=True
        )


# =============================================================================
# FastAPI Dependencies for Route Protection
# =============================================================================

def _raise_if_unauthenticated(auth: AuthResult) -> None:
    """Shared 401/503 mapping for the require_* dependencies.

    503 (with Retry-After) when the auth store couldn't be consulted — clients
    treat it as transient and retry. 401 only for missing/rejected keys, which
    IS authoritative: clients must not retry those.
    """
    if auth.is_authenticated:
        return
    if auth.service_error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=auth.error or "Authentication service temporarily unavailable",
            headers={"Retry-After": "2"}
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=auth.error or "Invalid API key",
        headers={"WWW-Authenticate": "APIKey"}
    )


async def get_current_auth(api_key: Optional[str] = Depends(api_key_header)) -> AuthResult:
    """
    Dependency to get current authentication state.
    Does not raise an error if not authenticated.
    """
    return await validate_api_key(api_key)


async def require_api_key(api_key: Optional[str] = Depends(api_key_header)) -> AuthResult:
    """
    Dependency that requires any valid API key.
    Raises HTTPException if not authenticated.
    """
    auth = await validate_api_key(api_key)
    _raise_if_unauthenticated(auth)
    return auth


async def require_read_permission(
    request: Request,
    api_key: Optional[str] = Depends(api_key_header)
) -> AuthResult:
    """
    Dependency that requires READ permission.
    Admin keys automatically have this permission.

    Monetized (x402-priced) keys are additionally restricted to the retrieval
    endpoints in MONETIZED_KEY_ALLOWED_PATHS — a public paid key must not get
    free READ access to the rest of the API (raw document/file access would
    undercut the paid retrieval it fronts).
    """
    auth = await validate_api_key(api_key)
    _raise_if_unauthenticated(auth)

    if not auth.has_permission(APIKeyPermission.READ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions: READ access required"
        )

    if auth.is_monetized and request.url.path not in MONETIZED_KEY_ALLOWED_PATHS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This monetized API key only permits the retrieval endpoints: "
                + ", ".join(sorted(MONETIZED_KEY_ALLOWED_PATHS))
            )
        )

    return auth


async def require_manage_permission(api_key: Optional[str] = Depends(api_key_header)) -> AuthResult:
    """
    Dependency that requires MANAGE permission.
    Admin keys automatically have this permission.
    """
    auth = await validate_api_key(api_key)
    _raise_if_unauthenticated(auth)

    if not auth.has_permission(APIKeyPermission.MANAGE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions: MANAGE access required"
        )

    return auth


async def require_admin(api_key: Optional[str] = Depends(api_key_header)) -> AuthResult:
    """
    Dependency that requires admin-level access.
    Only the admin API key from environment has this.
    """
    auth = await validate_api_key(api_key)
    _raise_if_unauthenticated(auth)

    if not auth.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )

    return auth


def validate_collection_access(auth: AuthResult, collection_id: Optional[str], action: str = "access") -> None:
    """
    Validate that the authenticated user can access a specific collection.
    
    Args:
        auth: The authentication result
        collection_id: The collection ID to check access for
        action: A verb describing the action (for error message), e.g., "read", "upload to"
    
    Raises:
        HTTPException: If access is denied
    """
    if not auth.can_access_collection(collection_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API key does not have permission to {action} collection: {collection_id}"
        )
