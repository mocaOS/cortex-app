"""GlitchTip / Sentry error-tracking wiring.

Single place that owns sentry-sdk initialization. Activation is fully
env-driven: when ``SENTRY_DSN`` is unset (the default) ``init_sentry`` is a
no-op and the SDK is never imported, so the exact same image runs tracked or
untracked. GlitchTip speaks the Sentry protocol, so the stock SDK works
unchanged — only the DSN points at the GlitchTip instance.

Configuration is read from ``app.config.Settings`` (which also loads .env
files that never reach ``os.environ`` — same pitfall Langfuse hit, see
``observability.py``), with a raw-env fallback so minimal entrypoints like the
docling worker work even if importing the full Settings ever fails.

What the integration gives us with no per-endpoint code:

- Unhandled exceptions in any endpoint become events (the Starlette/FastAPI
  integrations capture them *before* main.py's ``unhandled_exception_handler``
  sanitizes the response, so tracking and client-facing sanitization coexist).
- ``logger.error(...)``/``logger.exception(...)`` anywhere (background tasks,
  pipeline stages, the docling worker) become events too; WARNING and below
  ride along as breadcrumbs on the next event.
- Python events carry source context (the lines around each stack frame) read
  from the container's own files at capture time — readable tracebacks need no
  upload step, unlike the frontend's source maps.
- Every event is tagged with the originating ``service`` (backend /
  docling-worker) and, when the logging contextvar has one, the ``request_id``
  that also appears in log lines and ``X-Request-ID`` response headers — so a
  GlitchTip issue correlates 1:1 with server logs.

Privacy follows the Langfuse precedent (deny by default): request bodies are
never attached unless ``SENTRY_MAX_REQUEST_BODY_SIZE`` is raised, and PII
capture (user ids, IPs, cookies) requires an explicit
``SENTRY_SEND_DEFAULT_PII=true``.

See .claude/domain/observability.md for the full design.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# True once sentry_sdk.init() has run in this process.
_initialized = False


def _stamp_request_id(event: dict, hint: dict) -> Optional[dict]:
    """before_send hook: tag events with the current X-Request-ID.

    Reads the logging contextvar (set by the request middleware and inherited
    by tasks spawned from a request), so events from streaming generators and
    background work correlate with log lines without any per-callsite code.
    Total — an import/lookup failure must never drop the event.
    """
    try:
        from app.logging_setup import get_request_id

        rid = get_request_id()
        if rid:
            event.setdefault("tags", {})["request_id"] = rid
    except Exception:  # noqa: BLE001 — tagging is best-effort
        pass
    return event


def _sentry_config() -> dict[str, Any]:
    """Resolve sentry settings, preferring Settings (loads .env) over raw env."""
    try:
        from app.config import get_settings

        s = get_settings()
        return {
            "dsn": s.sentry_dsn.strip(),
            "environment": (s.sentry_environment or s.environment or "").strip(),
            "release": s.sentry_release.strip(),
            "traces_sample_rate": s.sentry_traces_sample_rate,
            "send_default_pii": s.sentry_send_default_pii,
            "max_request_body_size": s.sentry_max_request_body_size,
        }
    except Exception:  # noqa: BLE001 — worker fallback: raw env, same names
        try:
            rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0") or 0)
        except ValueError:
            rate = 0.0
        return {
            "dsn": os.environ.get("SENTRY_DSN", "").strip(),
            "environment": (
                os.environ.get("SENTRY_ENVIRONMENT", "").strip()
                or os.environ.get("ENVIRONMENT", "").strip()
            ),
            "release": os.environ.get("SENTRY_RELEASE", "").strip(),
            "traces_sample_rate": rate,
            "send_default_pii": os.environ.get(
                "SENTRY_SEND_DEFAULT_PII", ""
            ).lower()
            in ("1", "true", "yes"),
            "max_request_body_size": os.environ.get(
                "SENTRY_MAX_REQUEST_BODY_SIZE", "never"
            ),
        }


def init_sentry(*, service: str = "backend") -> bool:
    """Initialize sentry-sdk if SENTRY_DSN is configured. Returns True if active.

    In the FastAPI process this must run BEFORE ``app = FastAPI(...)`` so the
    SDK's Starlette/FastAPI integrations hook the app while it is constructed.
    Also called by the docling worker subprocess, which shares the container
    env and therefore the same DSN.
    """
    global _initialized
    cfg = _sentry_config()
    if not cfg["dsn"]:
        return False
    if _initialized:
        return True

    try:
        import sentry_sdk
    except ImportError:
        logger.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed; "
            "error tracking disabled"
        )
        return False

    environment = cfg["environment"] or "development"
    kwargs: dict[str, Any] = {}
    # Deploy/version tag; optional — GlitchTip resolves frontend source maps
    # via debug IDs and backend tracebacks via local files, so events remain
    # readable without a release.
    if cfg["release"]:
        kwargs["release"] = cfg["release"]
    # Performance transactions (GlitchTip supports them). Default 0 = errors
    # only; tracing stays fully disabled rather than "enabled, sampling 0%".
    if cfg["traces_sample_rate"] > 0:
        kwargs["traces_sample_rate"] = min(cfg["traces_sample_rate"], 1.0)

    sentry_sdk.init(
        dsn=cfg["dsn"],
        environment=environment,
        # Privacy deny-by-default, mirroring the Langfuse content masking:
        # no request bodies (ask questions / document text are authored
        # content) and no PII (IPs, cookies, user ids) unless opted in.
        max_request_body_size=cfg["max_request_body_size"],
        send_default_pii=cfg["send_default_pii"],
        # GlitchTip has no session/release-health product; don't send sessions.
        auto_session_tracking=False,
        before_send=_stamp_request_id,
        **kwargs,
    )
    sentry_sdk.set_tag("service", service)
    _initialized = True
    logger.info(
        f"Error tracking active (service={service}, environment={environment})"
    )
    return True
