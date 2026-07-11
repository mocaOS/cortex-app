"""Provider abstraction for the git connector.

One small interface implemented by GitHub / GitLab / Gitea adapters. Ingestion
(clone + diff) is provider-agnostic and only needs `authenticated_clone_url`,
`default_branch`, and wiki access; the REST methods cover credential
verification, repo browsing, and the agent's write actions.

The PAT is held inside the provider instance and injected server-side into
every request — callers (and the LLM) never construct auth headers.
"""

from __future__ import annotations

import base64
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


@dataclass
class GitRepoRef:
    """A repository reference for browsing/connection setup."""
    owner: str
    name: str
    full_name: str
    default_branch: Optional[str] = None
    private: bool = False
    web_url: Optional[str] = None


@dataclass
class VerifyResult:
    valid: bool
    login: Optional[str] = None
    message: Optional[str] = None


@dataclass
class WikiPage:
    """A wiki page fetched via API (GitLab/Gitea). `slug` doubles as the path key."""
    slug: str
    title: str
    content: str


@dataclass
class GitWriteResult:
    """Result of a write action (e.g. opening a PR/MR)."""
    branch: Optional[str] = None
    url: Optional[str] = None
    number: Optional[int] = None
    extra: dict = field(default_factory=dict)


class GitProviderError(Exception):
    """Raised on provider API failures, with the PAT already scrubbed."""


class GitProvider(ABC):
    """Abstract base for git hosting providers.

    Subclasses set `vendor`, `default_host`, and `api_path`, and implement the
    request shaping (`_auth_headers`) plus the endpoint methods.
    """

    vendor: str = ""
    default_host: str = ""   # host used when no base_url is configured
    api_path: str = ""       # path appended to the host to reach the API root

    def __init__(
        self,
        token: str,
        base_url: Optional[str] = None,
        timeout: int = 30,
        insecure_hosts: Optional[set[str]] = None,
    ):
        self._token = token
        self._base_url = base_url.rstrip("/") if base_url else None
        self._timeout = timeout
        self._insecure_hosts = insecure_hosts or set()

    # ----- host / url helpers ------------------------------------------------

    @property
    def host(self) -> str:
        """Bare hostname for this connection (from base_url or the vendor default)."""
        if self._base_url:
            return urlparse(self._base_url).netloc or self.default_host
        return self.default_host

    @property
    def api_root(self) -> str:
        """Full API root URL."""
        if self._base_url:
            return f"{self._base_url}{self.api_path}"
        return f"https://{self.default_host}{self.api_path}"

    @property
    def _verify_tls(self) -> bool:
        return self.host not in self._insecure_hosts

    def authenticated_clone_url(self, owner: str, name: str) -> str:
        """https URL with the PAT embedded, per the vendor's clone-auth scheme."""
        return f"https://{self._clone_userinfo()}@{self.host}/{owner}/{name}.git"

    @abstractmethod
    def _clone_userinfo(self) -> str:
        """The `user:pass` portion of the authenticated clone URL for this vendor."""

    def wiki_clone_url(self, owner: str, name: str) -> Optional[str]:
        """Authenticated wiki clone URL, or None if the vendor has no git wiki repo."""
        return None

    # ----- http plumbing -----------------------------------------------------

    @abstractmethod
    def _auth_headers(self) -> dict:
        """Authorization headers for REST calls (vendor-specific)."""

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "***") if self._token else text

    async def _request(
        self, method: str, path: str, *, json: dict = None, params: dict = None
    ) -> httpx.Response:
        """Issue an authenticated request to `api_root + path`. Raises GitProviderError."""
        url = path if path.startswith("http") else f"{self.api_root}{path}"
        # SSRF guard: base_url is caller-supplied for self-hosted GitLab/Gitea.
        # Block loopback/link-local/metadata (and re-validate redirect hops so a
        # public base_url can't 3xx-bounce to an internal target). Private ranges
        # stay allowed — self-hosted git on an internal IP is legitimate — as are
        # hosts the operator already trusts via GIT_HTTP_INSECURE_HOSTS.
        from app.services.ssrf_guard import async_request_hook, SSRFError
        _ssrf_hook = async_request_hook(allow_private=True, allowlist=self._insecure_hosts)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                verify=self._verify_tls,
                follow_redirects=True,
                event_hooks={"request": [_ssrf_hook]},
            ) as client:
                resp = await client.request(
                    method, url, headers=self._auth_headers(), json=json, params=params
                )
            if resp.status_code >= 400:
                raise GitProviderError(
                    self._scrub(f"{self.vendor} {method} {url} → HTTP "
                                f"{resp.status_code}: {resp.text[:300]}")
                )
            return resp
        except SSRFError as e:
            raise GitProviderError(f"{self.vendor} request blocked: {e}") from e
        except httpx.HTTPError as e:
            raise GitProviderError(self._scrub(f"{self.vendor} request failed: {e}")) from e

    @staticmethod
    def _b64(content: str) -> str:
        return base64.b64encode(content.encode("utf-8")).decode("ascii")

    # ----- read interface ----------------------------------------------------

    @abstractmethod
    async def verify(self) -> VerifyResult: ...

    @abstractmethod
    async def list_repos(self, page: int = 1) -> list[GitRepoRef]: ...

    @abstractmethod
    async def default_branch(self, owner: str, name: str) -> Optional[str]: ...

    @abstractmethod
    async def get_file_content(self, owner: str, name: str, path: str,
                               ref: Optional[str] = None) -> str:
        """Fetch a single file's current text content from the repo."""

    async def list_wiki_pages(self, owner: str, name: str) -> Optional[list[WikiPage]]:
        """API-based wiki listing (GitLab/Gitea). None means 'use wiki_clone_url instead'."""
        return None

    # ----- write interface (used by the agent git_repo tool) -----------------

    @abstractmethod
    async def create_branch(self, owner: str, name: str, new_branch: str, from_branch: str) -> None: ...

    @abstractmethod
    async def commit_files(
        self, owner: str, name: str, branch: str, files: list[tuple[str, str]], message: str
    ) -> None:
        """Commit a set of (path, content) edits to `branch` (which must already exist)."""

    @abstractmethod
    async def open_pull_request(
        self, owner: str, name: str, head: str, base: str, title: str, body: str
    ) -> GitWriteResult: ...

    @abstractmethod
    async def comment(self, owner: str, name: str, number: int, body: str) -> GitWriteResult: ...
