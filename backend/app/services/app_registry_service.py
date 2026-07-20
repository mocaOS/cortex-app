"""Registry consumer — browse the public app catalog and install from it.

The registry (github.com/mocaOS/cortex-registry) is git-native: the catalog
is an aggregated ``index.json`` of listings, each carrying the app's manifest
verbatim plus an artifact block ``{url, sha256, size}`` pointing at the
publisher's GitHub release zip.

Trust model on the consuming side: the catalog URL is operator-configured
(``APP_REGISTRY_URL``), and every install re-downloads the artifact and
**verifies the pinned sha256 + size before a single byte is unpacked** —
a compromised mirror or moved release asset fails closed. The manifest the
admin approves in the browser is the one CI proved equal to the zip's.
"""

import asyncio
import hashlib
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 300.0


class RegistryError(Exception):
    """Registry fetch/verify failure; maps to a 4xx/502 at the endpoint."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class AppRegistryService:
    def __init__(self):
        self._cache: Optional[Tuple[float, List[dict]]] = None
        self._lock = asyncio.Lock()

    async def listings(self, *, force_refresh: bool = False) -> List[dict]:
        """The catalog's active listings (cached for a few minutes)."""
        settings = get_settings()
        url = settings.app_registry_url
        if not url:
            raise RegistryError(404, "No app registry configured (APP_REGISTRY_URL)")

        async with self._lock:
            if (
                not force_refresh
                and self._cache
                and time.monotonic() - self._cache[0] < _CACHE_TTL_S
            ):
                return self._cache[1]
            try:
                async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                    response = await client.get(url, headers={"Accept": "application/json"})
            except httpx.HTTPError as e:
                raise RegistryError(502, f"Registry unreachable: {type(e).__name__}")
            if response.status_code != 200:
                raise RegistryError(502, f"Registry answered {response.status_code}")
            try:
                index = response.json()
                apps = index["apps"]
                assert isinstance(apps, list)
            except (ValueError, KeyError, AssertionError):
                raise RegistryError(502, "Registry index is not a valid catalog")

            active = [
                listing
                for listing in apps
                if isinstance(listing, dict)
                and listing.get("status") == "active"
                and isinstance(listing.get("app"), dict)
                and isinstance(listing.get("artifact"), dict)
            ]
            self._cache = (time.monotonic(), active)
            return active

    async def get_listing(self, slug: str) -> dict:
        for listing in await self.listings():
            if listing.get("slug") == slug or listing["app"].get("id") == slug:
                return listing
        raise RegistryError(404, f"App '{slug}' not found in the registry")

    async def fetch_verified_artifact(self, listing: dict) -> bytes:
        """Download the release zip and verify it against the pinned digest.

        Size is enforced while streaming (a lying Content-Length can't make
        us buffer more than the listed size), and the sha256 must match the
        listing exactly — only then are the bytes handed to the installer.
        """
        artifact = listing["artifact"]
        url = str(artifact.get("url", ""))
        expected_sha = str(artifact.get("sha256", ""))
        expected_size = int(artifact.get("size", 0))
        settings = get_settings()
        cap = settings.app_max_package_mb * 1024 * 1024
        if not url.startswith("https://"):
            raise RegistryError(400, "Artifact URL must be https")
        if expected_size <= 0 or expected_size > cap:
            raise RegistryError(400, f"Artifact size exceeds the {settings.app_max_package_mb} MB cap")

        chunks: List[bytes] = []
        received = 0
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise RegistryError(502, f"Artifact fetch failed: {response.status_code}")
                    async for chunk in response.aiter_bytes():
                        received += len(chunk)
                        if received > expected_size:
                            raise RegistryError(
                                502, "Artifact is larger than its listed size — refusing"
                            )
                        chunks.append(chunk)
        except httpx.HTTPError as e:
            raise RegistryError(502, f"Artifact fetch failed: {type(e).__name__}")

        data = b"".join(chunks)
        if len(data) != expected_size:
            raise RegistryError(
                502, f"Artifact size mismatch: listed {expected_size}, got {len(data)}"
            )
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha:
            raise RegistryError(
                502,
                "Artifact checksum mismatch — the published zip does not match the "
                f"registry's pinned sha256 (expected {expected_sha[:12]}…, got {digest[:12]}…)",
            )
        logger.info(
            f"Registry artifact for '{listing['app'].get('id')}' verified "
            f"({len(data) // 1024} KB, sha256 {digest[:12]}…)"
        )
        return data

    def summarize(self, listing: dict, installed: Dict[str, str]) -> Dict[str, Any]:
        """Shape a listing for the admin browser, with install-state joined."""
        app = listing["app"]
        app_id = app.get("id", "")
        return {
            "slug": listing.get("slug") or app_id,
            "name": app.get("name"),
            "version": app.get("version"),
            "type": app.get("type"),
            "description": app.get("description"),
            "publisher": app.get("publisher", {}),
            "repo": listing.get("repo"),
            "tags": listing.get("tags", []),
            "key_scope": (app.get("cortex") or {}).get("keyScope"),
            "endpoints": (app.get("cortex") or {}).get("endpoints", []),
            "capabilities": sorted((app.get("capabilities") or {}).keys()),
            "config_vars": [v.get("name") for v in (app.get("config") or [])],
            "artifact_size": (listing.get("artifact") or {}).get("size"),
            "installed_version": installed.get(app_id),
            "update_available": (
                installed.get(app_id) is not None
                and installed.get(app_id) != app.get("version")
            ),
        }


_registry_service: Optional[AppRegistryService] = None


def get_app_registry_service() -> AppRegistryService:
    global _registry_service
    if _registry_service is None:
        _registry_service = AppRegistryService()
    return _registry_service
