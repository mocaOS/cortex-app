"""Tests for the SSRF egress guard (app/services/ssrf_guard.py).

Covers the address classification used to gate the agent http_request tool,
Web Import URLs, and git provider base_url. DNS-resolving hosts (localhost,
example.com) are exercised alongside literal IPs.
"""

from __future__ import annotations

import pytest

from app.services.ssrf_guard import SSRFError, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://localhost:7474",                      # loopback (via DNS)
        "http://127.0.0.1:8000",
        "http://[::1]/",                               # ipv6 loopback
        "http://0.0.0.0/",                             # unspecified
        "http://[::ffff:169.254.169.254]/",            # ipv4-mapped metadata
    ],
)
def test_blocks_internal_targets_by_default(url):
    with pytest.raises(SSRFError):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "gopher://x/", "ftp://host/"],
)
def test_blocks_non_http_schemes(url):
    with pytest.raises(SSRFError):
        validate_url(url)


def test_private_blocked_unless_allowed():
    with pytest.raises(SSRFError):
        validate_url("http://10.0.0.5/", allow_private=False)
    # allow_private lets RFC1918 through...
    validate_url("http://10.0.0.5/", allow_private=True)


def test_metadata_blocked_even_when_private_allowed():
    # Link-local/metadata is never reachable, regardless of allow_private.
    with pytest.raises(SSRFError):
        validate_url("http://169.254.169.254/", allow_private=True)


def test_allowlist_bypasses_check():
    # An explicitly trusted internal host is permitted.
    validate_url("http://10.0.0.5/", allow_private=False, allowlist="10.0.0.5")
    validate_url("http://10.0.0.5/", allow_private=False, allowlist=["10.0.0.5"])


def test_public_host_allowed():
    validate_url("https://example.com/")
