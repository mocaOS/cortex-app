"""API Usage tracking service.

This module provides high-level API usage tracking:
- Recording API requests per key
- Categorizing requests by endpoint
- Error tracking
- Aggregating statistics
"""

import logging
from typing import Optional
from datetime import datetime

from app.config import get_settings
from app.services.neo4j_service import get_neo4j_service
from app.models import (
    APIKeyStats,
    APIKeyUsageDataPoint,
    APIKeyUsageHistoryResponse,
    AdminStatsOverview,
    APIKeyWithStats,
    APIKeyPermission,
    CollectionScope,
)

logger = logging.getLogger(__name__)


# Endpoint category mapping
ENDPOINT_CATEGORIES = {
    # Ask/RAG endpoints
    "/api/ask": "ask",
    "/api/ask/stream": "ask",
    "/api/ask/stream/thinking": "ask",
    
    # Search endpoints
    "/api/search": "search",
    "/api/graph/search": "search",
    
    # Upload/Document management
    "/api/upload": "upload",
    "/api/documents": "documents",
    "/api/custom-input": "upload",
    "/api/custom-inputs": "documents",
    
    # Graph endpoints
    "/api/graph": "graph",
    "/api/graph/visualization": "graph",
    "/api/graph/entity": "graph",
    "/api/graph/entities": "graph",
    "/api/graph/communities": "graph",
    "/api/graph/subgraph": "graph",
    
    # Collection endpoints
    "/api/collections": "collections",
    
    # Admin endpoints
    "/api/admin": "admin",
    "/api/stats": "stats",
    
    # Turbo mode
    "/api/turbo": "turbo",
}


def categorize_endpoint(path: str) -> str:
    """
    Categorize an API endpoint path into a usage category.
    
    Args:
        path: The request path (e.g., "/api/ask/stream")
        
    Returns:
        Category string (ask, search, upload, documents, graph, collections, admin, other)
    """
    # Check for exact matches first
    if path in ENDPOINT_CATEGORIES:
        return ENDPOINT_CATEGORIES[path]
    
    # Check for prefix matches
    for prefix, category in ENDPOINT_CATEGORIES.items():
        if path.startswith(prefix):
            return category
    
    return "other"


class APIUsageService:
    """Service for tracking and reporting API usage."""
    
    def __init__(self):
        self.neo4j_service = get_neo4j_service()
    
    def record_request(
        self,
        key_id: str,
        endpoint_path: str,
        is_error: bool = False,
        error_message: Optional[str] = None
    ) -> None:
        """
        Record an API request for usage tracking.
        
        Args:
            key_id: The API key ID
            endpoint_path: The request path
            is_error: Whether the request resulted in an error
            error_message: Error message if is_error is True
        """
        try:
            category = categorize_endpoint(endpoint_path)
            self.neo4j_service.record_api_key_usage_simple(
                key_id=key_id,
                endpoint_category=category,
                is_error=is_error,
                error_message=error_message
            )
        except Exception as e:
            # Don't let usage tracking failures break the API
            logger.error(f"Failed to record API usage: {e}")
    
    def get_key_stats(self, key_id: str) -> Optional[APIKeyStats]:
        """
        Get usage statistics for a specific API key.
        
        Args:
            key_id: The API key ID
            
        Returns:
            APIKeyStats or None if key not found
        """
        stats_dict = self.neo4j_service.get_api_key_stats(key_id)
        if not stats_dict:
            return None
        
        return APIKeyStats(
            total_requests=stats_dict.get("total_requests", 0),
            requests_today=stats_dict.get("requests_today", 0),
            requests_this_week=stats_dict.get("requests_this_week", 0),
            requests_this_month=stats_dict.get("requests_this_month", 0),
            error_count=stats_dict.get("error_count", 0),
            last_error_at=_convert_neo4j_datetime(stats_dict.get("last_error_at")),
            last_error_message=stats_dict.get("last_error_message"),
            endpoint_breakdown=stats_dict.get("endpoint_breakdown", {})
        )
    
    def get_key_usage_history(
        self,
        key_id: str,
        days: int = 30
    ) -> Optional[APIKeyUsageHistoryResponse]:
        """
        Get daily usage history for an API key.
        
        Args:
            key_id: The API key ID
            days: Number of days of history
            
        Returns:
            APIKeyUsageHistoryResponse or None if key not found
        """
        # First check if key exists
        key_data = self.neo4j_service.get_api_key_by_id(key_id)
        if not key_data:
            return None
        
        history = self.neo4j_service.get_api_key_usage_history(key_id, days)
        
        return APIKeyUsageHistoryResponse(
            key_id=key_id,
            key_name=key_data.get("name", "Unknown"),
            history=[
                APIKeyUsageDataPoint(
                    date=item["date"],
                    requests=item["requests"],
                    errors=item["errors"]
                )
                for item in history
            ],
            period_days=days
        )
    
    def get_admin_overview(self) -> AdminStatsOverview:
        """
        Get aggregated statistics across all API keys.
        
        Returns:
            AdminStatsOverview with aggregated stats
        """
        stats = self.neo4j_service.get_admin_stats_overview()
        
        return AdminStatsOverview(
            total_keys=stats.get("total_keys", 0),
            active_keys=stats.get("active_keys", 0),
            total_requests_all_time=stats.get("total_requests_all_time", 0),
            total_requests_today=stats.get("total_requests_today", 0),
            total_requests_this_week=stats.get("total_requests_this_week", 0),
            total_requests_this_month=stats.get("total_requests_this_month", 0),
            total_errors=stats.get("total_errors", 0),
            most_active_key=stats.get("most_active_key"),
            endpoint_breakdown=stats.get("endpoint_breakdown", {})
        )
    
    def list_keys_with_stats(self) -> list[APIKeyWithStats]:
        """
        List all API keys with their usage statistics.
        
        Returns:
            List of APIKeyWithStats
        """
        settings = get_settings()
        keys_data = self.neo4j_service.list_api_keys_with_stats()
        
        result = []
        for key_data in keys_data:
            # Skip admin key if tracking is disabled
            if key_data["id"] == "admin" and not settings.track_admin_api_key_usage:
                continue
            # Convert permission strings to enum
            permissions = [
                APIKeyPermission(p) for p in key_data.get("permissions", [])
                if p in [e.value for e in APIKeyPermission]
            ]
            
            stats_data = key_data.get("stats", {})
            stats = APIKeyStats(
                total_requests=stats_data.get("total_requests", 0),
                requests_today=stats_data.get("requests_today", 0),
                requests_this_week=stats_data.get("requests_this_week", 0),
                requests_this_month=stats_data.get("requests_this_month", 0),
                error_count=stats_data.get("error_count", 0),
                last_error_at=_convert_neo4j_datetime(stats_data.get("last_error_at")),
                last_error_message=stats_data.get("last_error_message"),
                endpoint_breakdown=stats_data.get("endpoint_breakdown", {})
            )
            
            # Convert collection scope string to enum
            scope_str = key_data.get("collection_scope", "all")
            collection_scope = CollectionScope(scope_str) if scope_str in [e.value for e in CollectionScope] else CollectionScope.ALL
            
            result.append(APIKeyWithStats(
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
                allowed_collection_names=key_data.get("allowed_collection_names"),
                stats=stats
            ))
        
        return result


def _convert_neo4j_datetime(value) -> Optional[datetime]:
    """Convert Neo4j DateTime to Python datetime."""
    if value is None:
        return None
    
    if isinstance(value, datetime):
        return value
    
    if hasattr(value, 'to_native'):
        return value.to_native()
    
    if hasattr(value, 'isoformat'):
        return datetime.fromisoformat(str(value.isoformat()))
    
    return None


# Singleton instance
_api_usage_service: Optional[APIUsageService] = None


def get_api_usage_service() -> APIUsageService:
    """Get the singleton API usage service instance."""
    global _api_usage_service
    if _api_usage_service is None:
        _api_usage_service = APIUsageService()
    return _api_usage_service
