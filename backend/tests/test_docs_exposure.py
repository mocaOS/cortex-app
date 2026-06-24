"""Tests for interactive-docs exposure gating (D-004 hardening).

A directly-exposed backend served /docs, /redoc and /openapi.json to anonymous
callers, disclosing the full API schema. `Settings.docs_enabled` now defaults to
"auto" (on in dev, off in production) with an EXPOSE_API_DOCS override. These
pin that resolution; the FastAPI app wires docs_url/openapi_url from it.
"""

from __future__ import annotations

import pytest
from pydantic_settings import SettingsConfigDict

from app.config import Settings


def _settings(**overrides) -> Settings:
    class _T(Settings):
        model_config = SettingsConfigDict(env_file=None, case_sensitive=False, extra="ignore")

    # Production boot enforces strong secrets; supply one so we can test the
    # docs gating itself rather than the (separately-tested) secret guard.
    overrides.setdefault("neo4j_password", "a-strong-non-default-password")
    return _T(**overrides)


def test_docs_on_in_development_by_default():
    assert _settings(environment="development", expose_api_docs="auto").docs_enabled is True


def test_docs_off_in_production_by_default():
    assert _settings(environment="production", expose_api_docs="auto").docs_enabled is False
    assert _settings(environment="prod", expose_api_docs="auto").docs_enabled is False


@pytest.mark.parametrize("val", ["true", "1", "yes", "on", "TRUE"])
def test_explicit_enable_overrides_production(val):
    assert _settings(environment="production", expose_api_docs=val).docs_enabled is True


@pytest.mark.parametrize("val", ["false", "0", "no", "off"])
def test_explicit_disable_overrides_development(val):
    assert _settings(environment="development", expose_api_docs=val).docs_enabled is False


def test_default_field_value_is_auto():
    # Unset EXPOSE_API_DOCS -> auto -> follows environment.
    assert _settings(environment="development").docs_enabled is True
    assert _settings(environment="production").docs_enabled is False
