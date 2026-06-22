"""Unit tests for API usage endpoint categorization.

Covers `categorize_endpoint` — exact matches, prefix matches, the
longest-prefix-wins rule (regression for D-003: "/api/custom-inputs/{id}" must
categorize as "documents", not "upload"), and the "other" fallback. This pure
function feeds per-key usage analytics, so mislabels are a data-integrity issue.
"""

from __future__ import annotations

import pytest

from app.services.api_usage_service import categorize_endpoint


@pytest.mark.parametrize(
    "path,expected",
    [
        # exact matches
        ("/api/ask", "ask"),
        ("/api/ask/stream", "ask"),
        ("/api/ask/stream/thinking", "ask"),
        ("/api/search", "search"),
        ("/api/graph/search", "search"),
        ("/api/upload", "upload"),
        ("/api/documents", "documents"),
        ("/api/custom-input", "upload"),
        ("/api/custom-inputs", "documents"),
        ("/api/collections", "collections"),
        ("/api/stats", "stats"),
        # prefix matches
        ("/api/documents/abc/content", "documents"),
        ("/api/graph/communities/123", "graph"),
        ("/api/admin/reset", "admin"),
        ("/api/turbo/start", "turbo"),
        ("/api/collections/c1/entities", "collections"),
        # fallback
        ("/api/totally-unknown", "other"),
        ("/health", "other"),
    ],
)
def test_categorize_endpoint(path, expected):
    assert categorize_endpoint(path) == expected


def test_custom_inputs_detail_is_documents_not_upload():
    """D-003 regression: longest-prefix must win so the custom-inputs detail
    path is not swallowed by the shorter '/api/custom-input' (upload) prefix."""
    assert categorize_endpoint("/api/custom-inputs/abc123") == "documents"
    # the singular create endpoint and its sub-paths stay 'upload'
    assert categorize_endpoint("/api/custom-input/generate-topic") == "upload"
