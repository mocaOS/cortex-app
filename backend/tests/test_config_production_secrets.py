"""Tests for production secret enforcement (config._enforce_production_secrets).

A production instance must refuse to boot on weak/placeholder secrets — these
are publicly known (shipped in example env files), so they are equivalent to no
secret at all. Development keeps convenient defaults.
"""

from __future__ import annotations

import pytest

from app.config import Settings

# Strong, non-placeholder values used to isolate the field under test.
STRONG = "9f3c1a7b5e2d48c6b0a1f4e8d7c2b6a5"  # 32 hex chars
STRONG_SECRET = STRONG + STRONG  # 64 chars


def _make(**overrides):
    base = dict(
        _env_file=None,
        environment="production",
        neo4j_password=STRONG,
        admin_password=STRONG,
        admin_api_key=STRONG,
        session_secret=STRONG_SECRET,
    )
    base.update(overrides)
    return Settings(**base)


def test_strong_production_config_boots():
    s = _make()
    assert s.is_production is True


def test_development_allows_weak_secrets():
    # The whole point of the dev default: convenience, no enforcement.
    s = _make(environment="development", session_secret="secret",
              neo4j_password="password123", admin_api_key="custom-api-keyyy")
    assert s.is_production is False


@pytest.mark.parametrize("secret", ["secret", "CHANGE_ME_run_openssl_rand_hex_32",
                                    "default-secret-key-min-32-characters-long"])
def test_placeholder_session_secret_rejected(secret):
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        _make(session_secret=secret)


def test_short_session_secret_rejected():
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        _make(session_secret="tooshort")


@pytest.mark.parametrize("pw", ["", "password123", "another-pass",
                                "CHANGE_ME_strong_neo4j_password"])
def test_placeholder_neo4j_password_rejected(pw):
    with pytest.raises(ValueError, match="NEO4J_PASSWORD"):
        _make(neo4j_password=pw)


@pytest.mark.parametrize("key", ["custom-api-keyyy", "CHANGE_ME_run_openssl_rand_hex_32",
                                 "cortex_admin_your-secure-api-key-here"])
def test_placeholder_admin_api_key_rejected(key):
    with pytest.raises(ValueError, match="ADMIN_API_KEY"):
        _make(admin_api_key=key)


def test_empty_admin_api_key_allowed_fail_closed():
    # Empty ADMIN_API_KEY disables that credential path entirely (fail closed);
    # it is not a placeholder, so it must not block boot.
    s = _make(admin_api_key="")
    assert s.is_production is True
