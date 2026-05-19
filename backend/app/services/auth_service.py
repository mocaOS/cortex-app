"""Authentication service for API key validation and permission checking.

This module provides:
- Admin API key validation against environment variables
- Generated API key validation against Neo4j stored keys
- Permission checking for different access levels (READ, MANAGE, ADMIN)
"""

import secrets
import hashlib
import logging
from typing import Optional, List, Tuple
from enum import Enum

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from app.config import get_settings
from app.services.neo4j_service import get_neo4j_service
from app.models import APIKeyPermission

logger = logging.getLogger(__name__)

# API Key header extractor
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


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
        allowed_collections: List[str] = None
    ):
        self.is_authenticated = is_authenticated
        self.is_admin = is_admin
        self.permissions = permissions or []
        self.key_id = key_id
        self.error = error
        self.key_name = key_name
        self.collection_scope = collection_scope
        self.allowed_collections = allowed_collections or []
    
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


async def validate_api_key(api_key: Optional[str]) -> AuthResult:
    """
    Validate an API key and return authentication result.
    
    Checks in order:
    1. Admin API key from environment
    2. Generated API keys stored in Neo4j
    
    Returns:
        AuthResult with authentication status and permissions
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
    
    # Check generated API keys in Neo4j
    try:
        neo4j_service = get_neo4j_service()
        
        # Extract prefix from the key for lookup
        key_prefix = api_key[:12] if len(api_key) >= 12 else api_key
        
        # Get potential matching keys by prefix
        matching_keys = neo4j_service.get_api_key_by_prefix(key_prefix)
        
        for stored_key in matching_keys:
            if verify_api_key_hash(api_key, stored_key["key_hash"]):
                # Valid key found - update last used timestamp
                neo4j_service.update_api_key_last_used(stored_key["id"])
                
                # Convert permission strings to enum
                permissions = [
                    APIKeyPermission(p) for p in stored_key["permissions"]
                    if p in [e.value for e in APIKeyPermission]
                ]
                
                # Get collection scope info
                collection_scope = stored_key.get("collection_scope", "all")
                allowed_collections = stored_key.get("allowed_collections", []) or []
                
                logger.debug(f"API key authenticated: {stored_key['name']} ({stored_key['id']})")
                return AuthResult(
                    is_authenticated=True,
                    is_admin=False,
                    permissions=permissions,
                    key_id=stored_key["id"],
                    key_name=stored_key["name"],
                    collection_scope=collection_scope,
                    allowed_collections=allowed_collections
                )
        
        # No matching key found
        return AuthResult(
            is_authenticated=False,
            error="Invalid API key"
        )
        
    except Exception as e:
        logger.error(f"Error validating API key: {e}")
        return AuthResult(
            is_authenticated=False,
            error="Authentication service error"
        )


# =============================================================================
# FastAPI Dependencies for Route Protection
# =============================================================================

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
    
    if not auth.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=auth.error or "Invalid API key",
            headers={"WWW-Authenticate": "APIKey"}
        )
    
    return auth


async def require_read_permission(api_key: Optional[str] = Depends(api_key_header)) -> AuthResult:
    """
    Dependency that requires READ permission.
    Admin keys automatically have this permission.
    """
    auth = await validate_api_key(api_key)
    
    if not auth.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=auth.error or "Invalid API key",
            headers={"WWW-Authenticate": "APIKey"}
        )
    
    if not auth.has_permission(APIKeyPermission.READ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions: READ access required"
        )
    
    return auth


async def require_manage_permission(api_key: Optional[str] = Depends(api_key_header)) -> AuthResult:
    """
    Dependency that requires MANAGE permission.
    Admin keys automatically have this permission.
    """
    auth = await validate_api_key(api_key)
    
    if not auth.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=auth.error or "Invalid API key",
            headers={"WWW-Authenticate": "APIKey"}
        )
    
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
    
    if not auth.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=auth.error or "Invalid API key",
            headers={"WWW-Authenticate": "APIKey"}
        )
    
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
