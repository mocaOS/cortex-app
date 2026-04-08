"""API Key management service for CRUD operations.

This module provides high-level API key management:
- Creating new API keys with permissions
- Listing and retrieving API keys
- Updating API key permissions and status
- Deleting/revoking API keys
"""

import uuid
import logging
from typing import Optional, List, Any
from datetime import datetime

from app.services.neo4j_service import get_neo4j_service
from app.services.auth_service import generate_api_key, hash_api_key
from app.models import (
    APIKey,
    APIKeyPermission,
    CollectionScope,
    CreateAPIKeyRequest,
    CreateAPIKeyResponse,
    APIKeyListItem,
    UpdateAPIKeyRequest,
)

logger = logging.getLogger(__name__)


def _convert_neo4j_datetime(value: Any) -> Optional[datetime]:
    """Convert Neo4j DateTime to Python datetime.
    
    Neo4j returns its own DateTime type which Pydantic doesn't understand.
    This helper converts it to a standard Python datetime.
    """
    if value is None:
        return None
    
    # If it's already a Python datetime, return as-is
    if isinstance(value, datetime):
        return value
    
    # Neo4j DateTime has a to_native() method that returns a Python datetime
    if hasattr(value, 'to_native'):
        return value.to_native()
    
    # Fallback: try to convert via ISO format string
    if hasattr(value, 'isoformat'):
        return datetime.fromisoformat(str(value.isoformat()))
    
    # Last resort: return current time
    logger.warning(f"Could not convert datetime value: {value} (type: {type(value)})")
    return datetime.utcnow()


class APIKeyService:
    """Service for managing API keys."""
    
    def __init__(self):
        self.neo4j_service = get_neo4j_service()
    
    def create_api_key(
        self,
        name: str,
        permissions: List[APIKeyPermission],
        created_by: str = "admin",
        collection_scope: CollectionScope = CollectionScope.ALL,
        allowed_collections: Optional[List[str]] = None
    ) -> Optional[CreateAPIKeyResponse]:
        """
        Create a new API key with the specified permissions.
        
        Args:
            name: Human-readable name for the key
            permissions: List of permissions to grant
            created_by: Who is creating this key
            collection_scope: 'all' for unrestricted, 'restricted' for collection-specific
            allowed_collections: List of collection IDs when scope is 'restricted'
            
        Returns:
            CreateAPIKeyResponse with the actual key (shown only once)
        """
        # Generate unique ID
        key_id = f"key_{uuid.uuid4().hex[:16]}"
        
        # Determine prefix based on permissions
        if APIKeyPermission.MANAGE in permissions:
            prefix = "moca_rw_"  # Read-write key
        else:
            prefix = "moca_ro_"  # Read-only key
        
        # Generate the actual API key
        full_key, key_prefix = generate_api_key(prefix)
        
        # Hash the key for storage
        key_hash = hash_api_key(full_key)
        
        # Convert permissions to string list for storage
        permission_strings = [p.value for p in permissions]
        
        # Store in Neo4j
        result = self.neo4j_service.create_api_key(
            key_id=key_id,
            name=name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            permissions=permission_strings,
            created_by=created_by,
            collection_scope=collection_scope.value,
            allowed_collections=allowed_collections or []
        )
        
        if not result:
            return None
        
        # Return response with the actual key (shown only once!)
        return CreateAPIKeyResponse(
            id=key_id,
            name=name,
            key=full_key,
            key_prefix=key_prefix,
            permissions=permissions,
            created_at=_convert_neo4j_datetime(result.get("created_at")) or datetime.utcnow(),
            collection_scope=collection_scope,
            allowed_collections=result.get("allowed_collections", [])
        )
    
    def list_api_keys(self) -> List[APIKeyListItem]:
        """
        List all API keys without exposing the actual keys.
        
        Returns:
            List of API key information
        """
        keys = self.neo4j_service.list_api_keys()
        
        result = []
        for key_data in keys:
            # Convert permission strings to enum
            permissions = [
                APIKeyPermission(p) for p in key_data.get("permissions", [])
                if p in [e.value for e in APIKeyPermission]
            ]
            
            # Convert collection scope string to enum
            scope_str = key_data.get("collection_scope", "all")
            collection_scope = CollectionScope(scope_str) if scope_str in [e.value for e in CollectionScope] else CollectionScope.ALL
            
            result.append(APIKeyListItem(
                id=key_data["id"],
                name=key_data["name"],
                key_prefix=key_data["key_prefix"],
                permissions=permissions,
                is_active=key_data.get("is_active", True),
                created_at=_convert_neo4j_datetime(key_data.get("created_at")) or datetime.utcnow(),
                last_used_at=_convert_neo4j_datetime(key_data.get("last_used_at")),
                created_by=key_data.get("created_by", "admin"),
                collection_scope=collection_scope,
                allowed_collections=key_data.get("allowed_collections", []),
                allowed_collection_names=key_data.get("allowed_collection_names")
            ))
        
        return result
    
    def get_api_key(self, key_id: str) -> Optional[APIKeyListItem]:
        """
        Get a single API key by ID.
        
        Returns:
            API key information (without the actual key)
        """
        key_data = self.neo4j_service.get_api_key_by_id(key_id)
        
        if not key_data:
            return None
        
        # Convert permission strings to enum
        permissions = [
            APIKeyPermission(p) for p in key_data.get("permissions", [])
            if p in [e.value for e in APIKeyPermission]
        ]
        
        # Convert collection scope string to enum
        scope_str = key_data.get("collection_scope", "all")
        collection_scope = CollectionScope(scope_str) if scope_str in [e.value for e in CollectionScope] else CollectionScope.ALL
        
        return APIKeyListItem(
            id=key_data["id"],
            name=key_data["name"],
            key_prefix=key_data["key_prefix"],
            permissions=permissions,
            is_active=key_data.get("is_active", True),
            created_at=_convert_neo4j_datetime(key_data.get("created_at")) or datetime.utcnow(),
            last_used_at=_convert_neo4j_datetime(key_data.get("last_used_at")),
            created_by=key_data.get("created_by", "admin"),
            collection_scope=collection_scope,
            allowed_collections=key_data.get("allowed_collections", []),
            allowed_collection_names=key_data.get("allowed_collection_names")
        )
    
    def update_api_key(
        self,
        key_id: str,
        request: UpdateAPIKeyRequest
    ) -> Optional[APIKeyListItem]:
        """
        Update an API key's name, permissions, active status, or collection scope.
        
        Args:
            key_id: The API key ID to update
            request: Update request with optional new values
            
        Returns:
            Updated API key information
        """
        # Convert permissions to string list if provided
        permission_strings = None
        if request.permissions is not None:
            permission_strings = [p.value for p in request.permissions]
        
        # Convert collection scope to string if provided
        collection_scope_str = None
        if request.collection_scope is not None:
            collection_scope_str = request.collection_scope.value
        
        result = self.neo4j_service.update_api_key(
            key_id=key_id,
            name=request.name,
            permissions=permission_strings,
            is_active=request.is_active,
            collection_scope=collection_scope_str,
            allowed_collections=request.allowed_collections
        )
        
        if not result:
            return None
        
        # Convert back to response model
        permissions = [
            APIKeyPermission(p) for p in result.get("permissions", [])
            if p in [e.value for e in APIKeyPermission]
        ]
        
        # Convert collection scope string to enum
        scope_str = result.get("collection_scope", "all")
        collection_scope = CollectionScope(scope_str) if scope_str in [e.value for e in CollectionScope] else CollectionScope.ALL
        
        return APIKeyListItem(
            id=result["id"],
            name=result["name"],
            key_prefix=result["key_prefix"],
            permissions=permissions,
            is_active=result.get("is_active", True),
            created_at=_convert_neo4j_datetime(result.get("created_at")) or datetime.utcnow(),
            last_used_at=_convert_neo4j_datetime(result.get("last_used_at")),
            created_by=result.get("created_by", "admin"),
            collection_scope=collection_scope,
            allowed_collections=result.get("allowed_collections", []),
            allowed_collection_names=result.get("allowed_collection_names")
        )
    
    def delete_api_key(self, key_id: str) -> bool:
        """
        Delete an API key permanently.
        
        Args:
            key_id: The API key ID to delete
            
        Returns:
            True if deleted, False if not found
        """
        return self.neo4j_service.delete_api_key(key_id)
    
    def revoke_api_key(self, key_id: str) -> Optional[APIKeyListItem]:
        """
        Revoke an API key (set is_active to False).
        The key remains in the database but can no longer be used.
        
        Args:
            key_id: The API key ID to revoke
            
        Returns:
            Updated API key information
        """
        return self.update_api_key(
            key_id,
            UpdateAPIKeyRequest(is_active=False)
        )
    
    def activate_api_key(self, key_id: str) -> Optional[APIKeyListItem]:
        """
        Re-activate a revoked API key.
        
        Args:
            key_id: The API key ID to activate
            
        Returns:
            Updated API key information
        """
        return self.update_api_key(
            key_id,
            UpdateAPIKeyRequest(is_active=True)
        )


# Singleton instance
_api_key_service: Optional[APIKeyService] = None


def get_api_key_service() -> APIKeyService:
    """Get the singleton API key service instance."""
    global _api_key_service
    if _api_key_service is None:
        _api_key_service = APIKeyService()
    return _api_key_service
