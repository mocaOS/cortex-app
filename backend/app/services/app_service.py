"""
Apps subsystem — install, serve, and broker access for in-instance apps.

An "app" is a self-contained static bundle (built from cortex-app-template)
installed from a zip: app.json manifest + icon + dist/. Apps run in a
sandboxed iframe and reach the Cortex API exclusively through the app proxy
(main.py), which validates short-lived app tokens and attaches the app's
server-side minted API key. The browser never holds a real key.

Storage is filesystem-only (the apps_data volume), mirroring the skills
layout — no Neo4j schema:

    {apps_dir}/{app_id}/
        app.json        manifest (verbatim from the package)
        icon.svg        (whatever manifest.icon names)
        dist/           static bundle; manifest.entry lives here
        install.json    install record: minted key (encrypted), grants, state
        config.json     admin-configured values (secrets encrypted)

Contract reference: cortex-registry ECOSYSTEM.md §4; docs in
.claude/domain/apps.md.
"""

import base64
import hashlib
import hmac
import io
import json
import logging
import re
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings
from app.models import (
    APIKeyPermission,
    AppGrantInfo,
    AppInfo,
    CollectionScope,
)
from app.services.crypto_service import get_crypto_service

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?$")
_CONFIG_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_MASK = "••••••••"

# Slugs the frontend claims under /apps/ — the launcher host page lives at
# /apps/launch/{id}, and the Next rewrite excludes this segment.
_RESERVED_IDS = {"launch"}

# App classes the runtime can host today. "service" apps are never hosted by
# cortex-app at all (they ship as containers via compose templates).
_SUPPORTED_TYPES = {"static", "platform"}

# Platform capabilities implemented so far. Declaring anything else is
# rejected at install with a clear message.
_SUPPORTED_CAPABILITIES = {"http", "tasks", "storage", "llm"}

TOKEN_VERSION = 1
GRANT_TOKEN_PREFIX = "cag_"  # grant (share-link) bearer string
APP_TOKEN_PREFIX = "cat_"  # short-lived app token


class AppValidationError(ValueError):
    """Manifest/package rejected — carries every issue found, not just the first."""

    def __init__(self, issues: List[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class AppService:
    """Filesystem-backed app lifecycle + token/grant broker."""

    def __init__(self):
        self.settings = get_settings()
        self._apps_dir = self._resolve_apps_dir()
        self._crypto = get_crypto_service()
        # (manifest, install-record) cache keyed by app_id → (mtime, data)
        self._cache: Dict[str, Tuple[float, dict, dict]] = {}

    # ------------------------------------------------------------------
    # Paths & records
    # ------------------------------------------------------------------

    def _resolve_apps_dir(self) -> Path:
        raw = self.settings.apps_dir
        path = Path(raw)
        if not path.is_absolute():
            # app/services/ → project root (backend/ locally, /app in the
            # container), matching skill_service._resolve_skills_dir
            path = (Path(__file__).resolve().parents[2] / raw).resolve()
        return path

    def _app_dir(self, app_id: str) -> Path:
        if not _SLUG_RE.match(app_id or ""):
            raise AppValidationError([f"invalid app id: {app_id!r}"])
        return self._apps_dir / app_id

    def _load(self, app_id: str) -> Optional[Tuple[dict, dict]]:
        """Return (manifest, install_record) or None if not installed."""
        app_dir = self._app_dir(app_id)
        install_path = app_dir / "install.json"
        manifest_path = app_dir / "app.json"
        if not install_path.exists() or not manifest_path.exists():
            return None
        mtime = max(install_path.stat().st_mtime, manifest_path.stat().st_mtime)
        cached = self._cache.get(app_id)
        if cached and cached[0] == mtime:
            return cached[1], cached[2]
        try:
            manifest = json.loads(manifest_path.read_text())
            record = json.loads(install_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"App '{app_id}' has unreadable metadata: {e}")
            return None
        self._cache[app_id] = (mtime, manifest, record)
        return manifest, record

    def _save_record(self, app_id: str, record: dict) -> None:
        path = self._app_dir(app_id) / "install.json"
        path.write_text(json.dumps(record, indent=2, default=str))
        self._cache.pop(app_id, None)

    # ------------------------------------------------------------------
    # Manifest validation (server-side twin of the template's validate.mjs)
    # ------------------------------------------------------------------

    def validate_manifest(self, manifest: Any) -> List[str]:
        issues: List[str] = []
        if not isinstance(manifest, dict):
            return ["app.json is not a JSON object"]

        app_id = manifest.get("id")
        if not isinstance(app_id, str) or not _SLUG_RE.match(app_id) or len(app_id) > 64:
            issues.append(f'"id" must be a kebab-case slug (got {app_id!r})')
        elif app_id in _RESERVED_IDS:
            issues.append(f'"id" {app_id!r} is reserved (frontend routing)')
        if not isinstance(manifest.get("name"), str) or not (1 <= len(manifest["name"]) <= 80):
            issues.append('"name" is required (1-80 chars)')
        if not isinstance(manifest.get("version"), str) or not _SEMVER_RE.match(manifest["version"]):
            issues.append('"version" must be semver')
        app_type = manifest.get("type")
        if app_type not in ("static", "platform", "service"):
            issues.append('"type" must be static | platform | service')
        elif app_type not in _SUPPORTED_TYPES:
            issues.append(
                f'"type": "{app_type}" apps cannot be hosted by this instance yet '
                f'(supported: {", ".join(sorted(_SUPPORTED_TYPES))})'
            )
        if not isinstance(manifest.get("description"), str) or not (1 <= len(manifest["description"]) <= 200):
            issues.append('"description" is required (1-200 chars)')
        publisher = manifest.get("publisher")
        if not isinstance(publisher, dict) or not publisher.get("name"):
            issues.append('"publisher.name" is required')
        icon = manifest.get("icon")
        if not isinstance(icon, str) or not icon.endswith(".svg") or "/" in icon:
            issues.append('"icon" must be a top-level .svg filename')
        entry = manifest.get("entry", "index.html")
        if not isinstance(entry, str) or not entry.endswith(".html"):
            issues.append('"entry" must be an .html file inside dist/')

        cortex = manifest.get("cortex")
        if not isinstance(cortex, dict):
            issues.append('"cortex" block is required')
        else:
            if cortex.get("keyScope") not in ("read", "read_write"):
                issues.append('"cortex.keyScope" must be read | read_write')
            endpoints = cortex.get("endpoints")
            if (
                not isinstance(endpoints, list)
                or not endpoints
                or not all(isinstance(e, str) and e and not e.startswith("/") for e in endpoints)
            ):
                issues.append('"cortex.endpoints" must be a non-empty list of /api/-relative paths')
            collections = cortex.get("collections")
            if collections not in ("user-selected", "all") and not (
                isinstance(collections, list) and collections
            ):
                issues.append('"cortex.collections" must be "user-selected" | "all" | [names]')

        for var in manifest.get("config", []) or []:
            if not isinstance(var, dict) or not _CONFIG_VAR_RE.match(var.get("name", "")):
                issues.append(f'config var {var.get("name")!r} must be UPPER_SNAKE')
            elif var.get("type") not in ("text", "secret"):
                issues.append(f'config var {var["name"]}: "type" must be text | secret')
            elif "auth_host" in var and (
                not isinstance(var["auth_host"], str) or not var["auth_host"].strip()
            ):
                issues.append(
                    f'config var {var["name"]}: "auth_host" must be a hostname '
                    "or ${CONFIG_VAR} reference"
                )

        external = manifest.get("externalHosts", [])
        if not isinstance(external, list) or not all(isinstance(h, str) for h in external):
            issues.append('"externalHosts" must be a list of hostnames')

        capabilities = manifest.get("capabilities") or {}
        if capabilities and app_type != "platform":
            issues.append('"capabilities" is only valid for type: "platform"')
        if app_type == "platform":
            if not capabilities:
                issues.append("platform apps must declare at least one capability")
            for cap, spec in capabilities.items():
                if cap not in _SUPPORTED_CAPABILITIES:
                    issues.append(
                        f'capability "{cap}" is not supported by this instance yet '
                        f'(supported: {", ".join(sorted(_SUPPORTED_CAPABILITIES))})'
                    )
                elif cap == "http":
                    hosts = (spec or {}).get("hosts")
                    if not isinstance(hosts, list) or not hosts or not all(
                        isinstance(h, str) and h for h in hosts
                    ):
                        issues.append(
                            'capability "http" requires a non-empty "hosts" list '
                            "(literal hostnames or ${CONFIG_VAR} references)"
                        )
                elif spec not in (None, {}):
                    # tasks/storage/llm take no config in v1 — reject stray
                    # fields loudly rather than silently ignoring them
                    issues.append(f'capability "{cap}" takes no configuration (use {{}})')

        return issues

    # ------------------------------------------------------------------
    # Install / lifecycle
    # ------------------------------------------------------------------

    def install_from_zip(
        self, data: bytes, collections: Optional[List[str]] = None
    ) -> AppInfo:
        """Install (or upgrade) an app from package bytes.

        Upgrades (same id already installed) keep the minted key, config, and
        grants; only the bundle + manifest are replaced.
        """
        cap = self.settings.app_max_package_mb * 1024 * 1024
        if len(data) > cap:
            raise AppValidationError(
                [f"package is {len(data) / 1024 / 1024:.1f} MB — exceeds the {self.settings.app_max_package_mb} MB cap"]
            )

        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise AppValidationError(["not a valid zip file"])

        # --- zip safety: paths + uncompressed size ---
        issues: List[str] = []
        total_uncompressed = 0
        names = zf.namelist()
        for info in zf.infolist():
            name = info.filename
            if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
                issues.append(f"unsafe path in package: {name!r}")
            total_uncompressed += info.file_size
        if total_uncompressed > cap * 4:
            issues.append("uncompressed contents exceed 4x the package cap (zip bomb?)")
        if "app.json" not in names:
            issues.append("package is missing app.json at the root")
        if issues:
            raise AppValidationError(issues)

        try:
            manifest = json.loads(zf.read("app.json"))
        except json.JSONDecodeError as e:
            raise AppValidationError([f"app.json does not parse: {e}"])

        issues = self.validate_manifest(manifest)
        if issues:
            raise AppValidationError(issues)

        app_id = manifest["id"]
        entry = manifest.get("entry", "index.html")
        icon = manifest["icon"]
        if f"dist/{entry}" not in names:
            raise AppValidationError([f"package is missing dist/{entry} (the manifest entry)"])
        if icon not in names:
            raise AppValidationError([f"package is missing {icon} (the manifest icon)"])

        existing = self._load(app_id)
        app_dir = self._app_dir(app_id)

        # --- extract to a staging dir, then swap ---
        staging = self._apps_dir / f".staging-{app_id}-{uuid.uuid4().hex[:8]}"
        staging.mkdir(parents=True, exist_ok=True)
        try:
            for name in names:
                if name.endswith("/"):
                    continue
                if not (name == "app.json" or name == icon or name.startswith("dist/")):
                    continue  # ignore stray files rather than failing
                target = (staging / name).resolve()
                if not str(target).startswith(str(staging.resolve())):
                    raise AppValidationError([f"unsafe path in package: {name!r}"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))

            if existing:
                # upgrade: carry over install record, config, AND platform
                # state — storage.sqlite (KV: cursors, dedup keys, results)
                # and tasks/ (definitions incl. schedules, run history).
                # Losing these on a version bump would make e.g. a sync app
                # re-upload its entire archive after every upgrade.
                record = existing[1]
                record["version"] = manifest["version"]
                record["updated_at"] = _utcnow().isoformat()
                for name in ("config.json", "storage.sqlite",
                             "storage.sqlite-wal", "storage.sqlite-shm"):
                    src = app_dir / name
                    if src.exists():
                        shutil.copy2(src, staging / name)
                tasks_dir = app_dir / "tasks"
                if tasks_dir.is_dir():
                    shutil.copytree(tasks_dir, staging / "tasks")
                (staging / "install.json").write_text(json.dumps(record, indent=2, default=str))
                shutil.rmtree(app_dir)
                staging.rename(app_dir)
            else:
                record = self._create_install_record(manifest, collections)
                (staging / "install.json").write_text(json.dumps(record, indent=2, default=str))
                if app_dir.exists():
                    shutil.rmtree(app_dir)
                staging.rename(app_dir)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        self._cache.pop(app_id, None)

        logger.info(
            f"App '{app_id}' v{manifest['version']} {'upgraded' if existing else 'installed'} "
            f"(scope={manifest['cortex']['keyScope']}, endpoints={manifest['cortex']['endpoints']})"
        )
        return self._to_info(manifest, self._load(app_id)[1])

    def _create_install_record(
        self, manifest: dict, collections: Optional[List[str]]
    ) -> dict:
        """Mint the app's dedicated scoped API key and build install.json."""
        from app.services.api_key_service import APIKeyService

        cortex = manifest["cortex"]
        permissions = [APIKeyPermission.READ]
        if cortex["keyScope"] == "read_write":
            permissions.append(APIKeyPermission.MANAGE)

        declared = cortex.get("collections")
        allowed: List[str] = []
        if isinstance(declared, list):
            allowed = declared
        elif declared == "user-selected":
            allowed = collections or []
        scope = CollectionScope.RESTRICTED if allowed else CollectionScope.ALL

        key_response = APIKeyService().create_api_key(
            name=f"app:{manifest['id']}",
            permissions=permissions,
            created_by="apps",
            collection_scope=scope,
            allowed_collections=allowed or None,
        )
        if not key_response:
            raise RuntimeError("failed to mint the app's API key")

        # The proxy must present the full key on upstream calls, so the
        # plaintext is retained server-side — encrypted at rest when an
        # ENCRYPTION_KEY is configured (same posture as skill secrets).
        stored_key = self._crypto.encrypt(key_response.key) or key_response.key

        return {
            "installed_at": _utcnow().isoformat(),
            "version": manifest["version"],
            "enabled": True,
            "key_id": key_response.id,
            "key_prefix": key_response.key_prefix,
            "api_key": stored_key,
            "collections": allowed,
            "grants": [],
        }

    def list_apps(self) -> List[AppInfo]:
        if not self._apps_dir.exists():
            return []
        apps = []
        for entry in sorted(self._apps_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            loaded = self._load(entry.name)
            if loaded:
                apps.append(self._to_info(*loaded))
        return apps

    def get_app(self, app_id: str) -> Optional[AppInfo]:
        loaded = self._load(app_id)
        return self._to_info(*loaded) if loaded else None

    def set_enabled(self, app_id: str, enabled: bool) -> Optional[AppInfo]:
        loaded = self._load(app_id)
        if not loaded:
            return None
        manifest, record = loaded
        record["enabled"] = enabled
        self._save_record(app_id, record)
        return self._to_info(manifest, record)

    def delete_app(self, app_id: str) -> bool:
        loaded = self._load(app_id)
        if not loaded:
            return False
        _, record = loaded
        try:  # stop any live platform-task runs before the files disappear
            from app.services.app_task_service import get_app_task_service

            get_app_task_service().stop_all(app_id)
        except Exception as e:
            logger.warning(f"App '{app_id}': failed to stop live tasks: {e}")
        key_id = record.get("key_id")
        if key_id:
            try:
                from app.services.api_key_service import APIKeyService

                APIKeyService().revoke_api_key(key_id)
            except Exception as e:
                logger.warning(f"App '{app_id}': failed to revoke key {key_id}: {e}")
        shutil.rmtree(self._app_dir(app_id), ignore_errors=True)
        self._cache.pop(app_id, None)
        logger.info(f"App '{app_id}' deleted (key {key_id} revoked)")
        return True

    def _to_info(self, manifest: dict, record: dict) -> AppInfo:
        config_vars = manifest.get("config") or []
        config_status = None
        if config_vars:
            values = self._read_config(manifest["id"])
            required = [v["name"] for v in config_vars if v.get("required")]
            config_status = (
                "configured" if all(values.get(n) for n in required) else "needs_setup"
            )
        installed_at = record.get("installed_at")
        return AppInfo(
            id=manifest["id"],
            name=manifest["name"],
            version=record.get("version", manifest["version"]),
            type=manifest["type"],
            description=manifest["description"],
            publisher=manifest.get("publisher", {}),
            entry=manifest.get("entry", "index.html"),
            enabled=record.get("enabled", True),
            installed_at=datetime.fromisoformat(installed_at) if installed_at else None,
            key_prefix=record.get("key_prefix"),
            key_scope=manifest["cortex"]["keyScope"],
            endpoints=manifest["cortex"]["endpoints"],
            external_hosts=manifest.get("externalHosts", []),
            collections=record.get("collections", []),
            sharing_links=bool((manifest.get("sharing") or {}).get("links")),
            grants_count=sum(1 for g in record.get("grants", []) if not g.get("revoked")),
            config_status=config_status,
        )

    # ------------------------------------------------------------------
    # Config (secrets encrypted at rest, mask-preserving saves)
    # ------------------------------------------------------------------

    def _secret_names(self, manifest: dict) -> set:
        return {v["name"] for v in (manifest.get("config") or []) if v.get("type") == "secret"}

    def _read_config(self, app_id: str) -> Dict[str, str]:
        path = self._app_dir(app_id) / "config.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def get_config(self, app_id: str) -> Optional[dict]:
        """Schema + masked values for the admin UI. Only vars declared in the
        CURRENT manifest are exposed — upgrades preserve config.json, and a
        var that used to be secret-typed must not surface unmasked once a new
        manifest stops declaring it (found live: renamed secret leaked)."""
        loaded = self._load(app_id)
        if not loaded:
            return None
        manifest, _ = loaded
        stored = self._read_config(app_id)
        declared = {v.get("name") for v in (manifest.get("config") or [])}
        secrets_names = self._secret_names(manifest)
        values = {
            name: (_MASK if name in secrets_names and value else value)
            for name, value in stored.items()
            if name in declared
        }
        return {"variables": manifest.get("config") or [], "values": values}

    def save_config(self, app_id: str, values: Dict[str, str]) -> bool:
        loaded = self._load(app_id)
        if not loaded:
            return False
        manifest, _ = loaded
        declared = {v["name"] for v in (manifest.get("config") or [])}
        secrets_names = self._secret_names(manifest)
        stored = self._read_config(app_id)
        for name, value in values.items():
            if name not in declared:
                continue
            if name in secrets_names:
                if value == _MASK:
                    continue  # unchanged masked secret
                stored[name] = self._crypto.encrypt(value) or value
            else:
                stored[name] = value
        (self._app_dir(app_id) / "config.json").write_text(json.dumps(stored, indent=2))
        self._cache.pop(app_id, None)
        return True

    def _decrypted_config(self, app_id: str, manifest: dict) -> Dict[str, str]:
        stored = self._read_config(app_id)
        secrets_names = self._secret_names(manifest)
        return {
            name: (self._crypto.decrypt(value) or value) if name in secrets_names else value
            for name, value in stored.items()
        }

    # ------------------------------------------------------------------
    # Tokens (short-lived, HMAC) & share-link grants
    # ------------------------------------------------------------------

    def _token_secret(self) -> bytes:
        material = self.settings.session_secret or self.settings.encryption_key or self.settings.admin_api_key
        if not material:
            raise RuntimeError(
                "App tokens need SESSION_SECRET (or ENCRYPTION_KEY/ADMIN_API_KEY) to sign with"
            )
        return hashlib.sha256(f"cortex-app-tokens:{material}".encode()).digest()

    def _sign(self, payload: dict, prefix: str) -> str:
        # The prefix is bound INTO the signature, so a token of one type
        # cannot be re-prefixed and replayed as the other (a grant token
        # re-labeled `cat_` would otherwise pass as an app token and skip
        # revocation). Same body under a different prefix → different MAC.
        body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        sig = _b64url(
            hmac.new(self._token_secret(), f"{prefix}{body}".encode(), hashlib.sha256).digest()
        )
        return f"{prefix}{body}.{sig}"

    def _verify(self, token: str, prefix: str) -> Optional[dict]:
        if not token or not token.startswith(prefix) or "." not in token:
            return None
        body, _, sig = token[len(prefix):].partition(".")
        expected = _b64url(
            hmac.new(self._token_secret(), f"{prefix}{body}".encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(sig, expected):
            return None
        try:
            return json.loads(_b64url_decode(body))
        except (ValueError, json.JSONDecodeError):
            return None

    def issue_token(self, app_id: str, principal: str, role: str) -> Tuple[str, datetime]:
        """Mint a short-lived app token; only the app proxy accepts it."""
        now = int(time.time())
        exp = now + self.settings.app_token_ttl_seconds
        payload = {
            "v": TOKEN_VERSION,
            "app": app_id,
            "principal": principal,
            "role": role,
            "iat": now,
            "exp": exp,
            "jti": uuid.uuid4().hex[:12],
        }
        return self._sign(payload, APP_TOKEN_PREFIX), datetime.fromtimestamp(exp, tz=timezone.utc)

    def validate_token(self, token: str, app_id: str) -> Optional[dict]:
        payload = self._verify(token, APP_TOKEN_PREFIX)
        if not payload:
            return None
        if payload.get("v") != TOKEN_VERSION or payload.get("app") != app_id:
            return None
        if payload.get("exp", 0) < time.time():
            return None
        # An app token must carry a recognized principal. This also rejects a
        # grant-body payload (which has no principal) defensively, on top of
        # the prefix-bound signature.
        principal = payload.get("principal", "")
        if not (principal == "owner" or principal.startswith(("link:", "appuser:"))):
            return None
        # grant-derived tokens die with their grant
        if principal.startswith("link:"):
            loaded = self._load(app_id)
            if not loaded:
                return None
            grant_id = principal[5:]
            grant = next((g for g in loaded[1].get("grants", []) if g["id"] == grant_id), None)
            if not grant or grant.get("revoked"):
                return None
        return payload

    def create_grant(
        self, app_id: str, label: str, role: str, expires_hours: Optional[int]
    ) -> Optional[dict]:
        loaded = self._load(app_id)
        if not loaded:
            return None
        manifest, record = loaded
        if not (manifest.get("sharing") or {}).get("links"):
            raise AppValidationError(["this app does not allow share links (manifest sharing.links)"])
        if role not in ("viewer", "editor"):
            raise AppValidationError(["grant role must be viewer | editor"])
        grant_id = uuid.uuid4().hex[:12]
        expires_at = (
            datetime.fromtimestamp(time.time() + expires_hours * 3600, tz=timezone.utc)
            if expires_hours
            else None
        )
        record.setdefault("grants", []).append(
            {
                "id": grant_id,
                "label": label,
                "role": role,
                "created_at": _utcnow().isoformat(),
                "expires_at": expires_at.isoformat() if expires_at else None,
                "revoked": False,
            }
        )
        self._save_record(app_id, record)
        grant_token = self._sign(
            {
                "v": TOKEN_VERSION,
                "app": app_id,
                "grant": grant_id,
                "exp": int(expires_at.timestamp()) if expires_at else 0,  # 0 = no expiry
            },
            GRANT_TOKEN_PREFIX,
        )
        return {"id": grant_id, "grant_token": grant_token, "role": role, "expires_at": expires_at}

    def list_grants(self, app_id: str) -> Optional[List[AppGrantInfo]]:
        loaded = self._load(app_id)
        if not loaded:
            return None
        grants = []
        for g in loaded[1].get("grants", []):
            grants.append(
                AppGrantInfo(
                    id=g["id"],
                    label=g.get("label", ""),
                    role=g.get("role", "viewer"),
                    created_at=datetime.fromisoformat(g["created_at"]) if g.get("created_at") else None,
                    expires_at=datetime.fromisoformat(g["expires_at"]) if g.get("expires_at") else None,
                    revoked=g.get("revoked", False),
                )
            )
        return grants

    def revoke_grant(self, app_id: str, grant_id: str) -> bool:
        loaded = self._load(app_id)
        if not loaded:
            return False
        _, record = loaded
        for g in record.get("grants", []):
            if g["id"] == grant_id:
                g["revoked"] = True
                self._save_record(app_id, record)
                return True
        return False

    def exchange_grant(self, app_id: str, grant_token: str) -> Optional[Tuple[str, datetime, str]]:
        """Share-link visitor: grant token → short-lived app token.

        Returns (token, expires_at, role) or None if the grant is invalid,
        revoked, expired, or the app is disabled.
        """
        loaded = self._load(app_id)
        if not loaded or not loaded[1].get("enabled", True):
            return None
        payload = self._verify(grant_token, GRANT_TOKEN_PREFIX)
        if not payload or payload.get("app") != app_id:
            return None
        exp = payload.get("exp", 0)
        if exp and exp < time.time():
            return None
        grant = next(
            (g for g in loaded[1].get("grants", []) if g["id"] == payload.get("grant")), None
        )
        if not grant or grant.get("revoked"):
            return None
        token, expires_at = self.issue_token(
            app_id, principal=f"link:{grant['id']}", role=grant.get("role", "viewer")
        )
        return token, expires_at, grant.get("role", "viewer")

    # ------------------------------------------------------------------
    # Serving & proxy support
    # ------------------------------------------------------------------

    def resolve_static(self, app_id: str, rel_path: str) -> Optional[Path]:
        """Map a request path to a file inside the app's dist/, or the entry
        for SPA-style extensionless paths. None = not found/unsafe."""
        loaded = self._load(app_id)
        if not loaded or not loaded[1].get("enabled", True):
            return None
        manifest, _ = loaded
        dist = (self._app_dir(app_id) / "dist").resolve()
        rel_path = rel_path.lstrip("/") or manifest.get("entry", "index.html")
        candidate = (dist / rel_path).resolve()
        if not str(candidate).startswith(str(dist)):
            return None
        if candidate.is_file():
            return candidate
        if "." not in Path(rel_path).name:  # SPA route → entry
            entry = dist / manifest.get("entry", "index.html")
            return entry if entry.is_file() else None
        return None

    def csp_header(self, app_id: str) -> str:
        """Per-app CSP: self-contained bundle + declared external hosts
        (with ${VAR} references resolved from app config)."""
        loaded = self._load(app_id)
        connect = ["'self'"]
        if loaded:
            manifest, _ = loaded
            config = self._decrypted_config(app_id, manifest)
            for host in manifest.get("externalHosts", []) or []:
                resolved = re.sub(
                    r"\$\{([A-Z][A-Z0-9_]*)\}", lambda m: config.get(m.group(1), ""), host
                ).strip()
                if resolved:
                    connect.append(resolved)
        return (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            f"connect-src {' '.join(connect)}; "
            "frame-ancestors 'self'"
        )

    def endpoint_allowed(self, app_id: str, api_path: str) -> bool:
        """Prefix-match the requested /api/-relative path against the
        manifest allowlist: a declared "ask" covers "ask" and "ask/…".

        Fully decodes the path first and rejects any traversal segment. A raw
        "documents/../search" is normalized by uvicorn before routing, but a
        percent-encoded "documents/..%2fsearch" reaches here intact, passes a
        naive prefix check, then gets normalized to "search" by the upstream —
        escaping the allowlist. Decoding + rejecting ".." closes that."""
        from urllib.parse import unquote

        loaded = self._load(app_id)
        if not loaded:
            return False
        decoded = api_path
        for _ in range(3):  # defeat multi-layer encoding (..%252f etc.)
            nxt = unquote(decoded)
            if nxt == decoded:
                break
            decoded = nxt
        path = decoded.split("?")[0].strip("/")
        segments = path.split("/")
        if ".." in segments or "." in segments or "" in segments[1:]:
            return False  # traversal or empty segment (…//…) — never legitimate
        for declared in loaded[0]["cortex"]["endpoints"]:
            declared = declared.strip("/")
            if path == declared or path.startswith(declared + "/"):
                return True
        return False

    # ------------------------------------------------------------------
    # Platform capabilities
    # ------------------------------------------------------------------

    def has_capability(self, app_id: str, capability: str) -> bool:
        loaded = self._load(app_id)
        if not loaded or not loaded[1].get("enabled", True):
            return False
        manifest = loaded[0]
        return manifest.get("type") == "platform" and capability in (
            manifest.get("capabilities") or {}
        )

    def public_config(self, app_id: str) -> Optional[Dict[str, str]]:
        """Non-secret config values, readable by the app itself
        (GET platform/config). Secrets never cross this boundary.

        Only vars DECLARED (non-secret) in the CURRENT manifest are returned —
        upgrades preserve config.json, so a var that used to be secret-typed
        would otherwise lose its masking the moment a new manifest stops
        declaring it (found live: a renamed secret leaked as plaintext)."""
        loaded = self._load(app_id)
        if not loaded or not loaded[1].get("enabled", True):
            return None
        manifest, _ = loaded
        declared_public = {
            var.get("name")
            for var in manifest.get("config", []) or []
            if var.get("type") != "secret"
        }
        stored = self._read_config(app_id)
        return {k: v for k, v in stored.items() if k in declared_public}

    def _resolve_config_refs(self, value: str, config: Dict[str, str]) -> str:
        return re.sub(
            r"\$\{([A-Z][A-Z0-9_]*)\}", lambda m: config.get(m.group(1), ""), value
        ).strip()

    def allowed_http_hosts(self, app_id: str) -> set:
        """Hostnames the platform http capability may call, resolved from the
        manifest's declared hosts (literals or ${CONFIG_VAR} references)."""
        loaded = self._load(app_id)
        if not loaded:
            return set()
        manifest, _ = loaded
        declared = ((manifest.get("capabilities") or {}).get("http") or {}).get("hosts", [])
        config = self._decrypted_config(app_id, manifest)
        hosts = set()
        for entry in declared:
            resolved = self._resolve_config_refs(entry, config)
            if not resolved:
                continue
            # accept bare hostnames or full URLs
            from urllib.parse import urlsplit

            host = urlsplit(resolved).hostname if "//" in resolved else resolved.split("/")[0]
            if host:
                hosts.add(host.lower())
        return hosts

    def platform_auth_headers(
        self, app_id: str, target_host: Optional[str] = None
    ) -> Dict[str, str]:
        """Headers to inject on platform http calls, built from config vars
        with an ``auth_header`` template ("Authorization: Token VAR_NAME") —
        same semantics as skill auth injection. Secrets stay server-side.

        A var may carry ``auth_host`` (literal hostname/URL or ${CONFIG_VAR}
        reference): its header is then injected ONLY when the call targets
        that host — a multi-host app (e.g. venice + youtube) must not leak
        one service's credential to the other. Vars without auth_host keep
        the inject-everywhere behavior (fine for single-host apps).

        Templates may reference ANY config var by name (not just the carrying
        var) and wrap parts in ``base64(...)`` — evaluated after substitution.
        This is how Basic-auth apps take a plain login + password/app-password
        as config instead of making users hand-encode credentials (an error
        magnet): ``Authorization: Basic base64(NC_USER:NC_APP_PASSWORD)``.
        Everything renders server-side from encrypted config; nothing new
        reaches the browser."""
        from urllib.parse import urlsplit

        loaded = self._load(app_id)
        if not loaded:
            return {}
        manifest, _ = loaded
        config = self._decrypted_config(app_id, manifest)
        headers: Dict[str, str] = {}
        for var in manifest.get("config", []) or []:
            template = var.get("auth_header")
            value = config.get(var.get("name", ""))
            if not template or not value or ":" not in template:
                continue
            scope = var.get("auth_host")
            if scope:
                resolved = self._resolve_config_refs(scope, config)
                host = (
                    (urlsplit(resolved).hostname or "")
                    if "//" in resolved
                    else resolved.split("/")[0]
                ).lower()
                if not host or host != (target_host or "").lower():
                    continue
            header_name, _, header_value = template.partition(":")
            rendered = header_value.strip()
            # google_sa_token(VAR, scopes) must reach the async minting stage
            # (execute_app_http) carrying the VAR NAME — shield it from the
            # substitution below, which would inline the raw key JSON
            sa_exprs = re.findall(r"google_sa_token\([^()]*\)", rendered)
            for j, expr in enumerate(sa_exprs):
                rendered = rendered.replace(expr, f"\x00GSA{j}\x00")
            # longest name first — var names may be prefixes of each other
            for name in sorted(config, key=len, reverse=True):
                if config.get(name):
                    rendered = rendered.replace(name, config[name])
            rendered = re.sub(
                r"base64\(([^()]*)\)",
                lambda m: base64.b64encode(m.group(1).encode()).decode(),
                rendered,
            )
            for j, expr in enumerate(sa_exprs):
                rendered = rendered.replace(f"\x00GSA{j}\x00", expr)
            headers[header_name.strip()] = rendered
        return headers

    # ------------------------------------------------------------------
    # Google service-account tokens (auth_header transform)
    # ------------------------------------------------------------------

    _GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
    _sa_token_cache: Dict[tuple, tuple] = {}

    async def resolve_google_sa_tokens(self, app_id: str, header_value: str) -> str:
        """Resolve every ``google_sa_token(VAR, scope [scope…])`` expression in
        a rendered auth header to a Bearer token minted from the app's
        service-account key (a config secret).

        The whole exchange happens server-side: the key never enters task
        context or the browser, only the short-lived access token goes on the
        wire — and only to hosts the manifest allowlists. token_uri is pinned
        to Google's endpoint so a hostile "key" JSON can't steer the platform
        anywhere else. Tokens are cached until shortly before expiry."""

        async def mint(match: "re.Match[str]") -> str:
            inner = match.group(1)
            var_name, _, scopes = inner.partition(",")
            var_name = var_name.strip()
            scopes = " ".join(scopes.split())
            if not scopes:
                raise ValueError("google_sa_token needs explicit scopes: "
                                 "google_sa_token(VAR, <scope> [scope…])")
            cache_key = (app_id, var_name, scopes)
            cached = self._sa_token_cache.get(cache_key)
            if cached and time.monotonic() < cached[1]:
                return cached[0]

            loaded = self._load(app_id)
            if not loaded:
                raise ValueError("app not found")
            config = self._decrypted_config(app_id, loaded[0])
            raw = config.get(var_name)
            if not raw:
                raise ValueError(f"config var {var_name} is not set")
            try:
                key_data = json.loads(raw)
            except ValueError:
                raise ValueError(f"config var {var_name} is not a service-account JSON key")
            token_uri = key_data.get("token_uri", self._GOOGLE_TOKEN_URI)
            if token_uri != self._GOOGLE_TOKEN_URI:
                raise ValueError("service-account token_uri must be Google's endpoint")
            client_email = key_data.get("client_email")
            private_key_pem = key_data.get("private_key")
            if not client_email or not private_key_pem:
                raise ValueError(f"{var_name} is missing client_email/private_key")

            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding

            def b64url(data: bytes) -> str:
                return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

            now = int(time.time())
            segments = [
                b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()),
                b64url(json.dumps({
                    "iss": client_email, "scope": scopes, "aud": token_uri,
                    "iat": now, "exp": now + 3600,
                }).encode()),
            ]
            signing_input = ".".join(segments).encode()
            private_key = serialization.load_pem_private_key(
                private_key_pem.encode(), password=None
            )
            signature = private_key.sign(
                signing_input, padding.PKCS1v15(), hashes.SHA256()
            )
            assertion = f"{signing_input.decode()}.{b64url(signature)}"

            import httpx

            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.post(token_uri, data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                })
            if response.status_code != 200:
                raise ValueError(
                    f"Google token exchange failed ({response.status_code}): "
                    f"{response.text[:200]}"
                )
            body = response.json()
            token = body.get("access_token", "")
            if not token:
                raise ValueError("Google token exchange returned no access_token")
            expires_in = int(body.get("expires_in", 3600))
            self._sa_token_cache[cache_key] = (
                token, time.monotonic() + max(60, expires_in - 300)
            )
            return token

        result = header_value
        for match in re.finditer(r"google_sa_token\(([^()]*)\)", header_value):
            token = await mint(match)
            result = result.replace(match.group(0), token)
        return result

    def upstream_api_key(self, app_id: str) -> Optional[str]:
        """The app's minted key, decrypted, for the proxy to attach."""
        loaded = self._load(app_id)
        if not loaded or not loaded[1].get("enabled", True):
            return None
        stored = loaded[1].get("api_key")
        if not stored:
            return None
        return self._crypto.decrypt(stored) or stored


_app_service: Optional[AppService] = None


def get_app_service() -> AppService:
    global _app_service
    if _app_service is None:
        _app_service = AppService()
    return _app_service
