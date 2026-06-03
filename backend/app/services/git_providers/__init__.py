"""Git hosting provider adapters (GitHub / GitLab / Gitea)."""

from __future__ import annotations

from typing import Optional

from ...config import get_settings
from .base import (
    GitProvider,
    GitProviderError,
    GitRepoRef,
    GitWriteResult,
    VerifyResult,
    WikiPage,
)
from .gitea import GiteaProvider
from .github import GitHubProvider
from .gitlab import GitLabProvider

_REGISTRY = {
    "github": GitHubProvider,
    "gitlab": GitLabProvider,
    "gitea": GiteaProvider,
}


def parse_insecure_hosts() -> set[str]:
    """Hosts allowed to skip TLS verification, from GIT_HTTP_INSECURE_HOSTS."""
    raw = get_settings().git_http_insecure_hosts or ""
    return {h.strip() for h in raw.split(",") if h.strip()}


def get_provider(vendor: str, token: str, base_url: Optional[str] = None) -> GitProvider:
    """Construct a provider for `vendor`, wired with timeout + TLS policy from settings."""
    cls = _REGISTRY.get((vendor or "").lower())
    if cls is None:
        raise ValueError(f"Unsupported git vendor: {vendor!r}")
    settings = get_settings()
    return cls(
        token=token,
        base_url=base_url,
        timeout=settings.git_http_timeout,
        insecure_hosts=parse_insecure_hosts(),
    )


__all__ = [
    "GitProvider",
    "GitProviderError",
    "GitRepoRef",
    "GitWriteResult",
    "VerifyResult",
    "WikiPage",
    "get_provider",
    "parse_insecure_hosts",
]
