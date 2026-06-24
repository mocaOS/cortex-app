"""Langfuse observability wiring.

Single place that owns the Langfuse client lifecycle. Activation is fully
env-driven: when ``settings.langfuse_tracing_active`` is False every function
here is a no-op and the OpenAI client factory (see ``llm_config``) returns the
plain, untraced client. The same image therefore runs identically traced or
untraced.

The client is constructed **explicitly** from settings rather than relying on
the SDK's env-var auto-init, because in local/.env deployments the
``LANGFUSE_*`` values are loaded by pydantic-settings and may never reach
``os.environ`` where the SDK would look. Constructing it ourselves makes the
wiring deterministic across Docker and local dev.

See .claude/domain/observability.md for the full design.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from app.config import get_settings

logger = logging.getLogger(__name__)

# Module-level singleton. None when tracing is inactive or not yet initialized.
_langfuse_client: Optional[Any] = None


def init_langfuse() -> Optional[Any]:
    """Initialize the global Langfuse client from settings (idempotent).

    Returns the client when tracing is active, else None. Call once at app
    startup, before any traced LLM call. Constructing the ``Langfuse(...)``
    singleton here registers it globally so the ``langfuse.openai`` drop-in and
    the ``@observe`` decorator pick it up automatically.
    """
    global _langfuse_client
    if _langfuse_client is not None:
        return _langfuse_client

    settings = get_settings()
    if not settings.langfuse_tracing_active:
        logger.info("Langfuse tracing disabled (no LANGFUSE_* credentials set).")
        return None

    # Per-tenant trace segmentation: the control plane injects
    # LANGFUSE_TRACING_ENVIRONMENT=<tenant-slug>, so each tenant's traces land under
    # their own "environment" filter in a shared Langfuse project. Fall back to the
    # deployment ENVIRONMENT (production/development) when unset (single-tenant /
    # self-host). We must pass this explicitly — the SDK only auto-reads
    # LANGFUSE_TRACING_ENVIRONMENT from os.environ, which pydantic-settings' .env
    # loading can bypass (the same reason the keys/base_url are passed explicitly).
    tracing_environment = settings.langfuse_tracing_environment or settings.environment

    try:
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            sample_rate=settings.langfuse_sample_rate,
            environment=tracing_environment,
        )
        # Eagerly apply the global OpenAI instrumentation so EVERY openai-SDK
        # call is auto-traced — including libraries that build their own client
        # (Haystack's embedders). Done at startup so it's active before the first
        # embedding/LLM call, independent of order. (The client factory's
        # langfuse.openai import is then belt-and-suspenders.)
        import langfuse.openai  # noqa: F401
        logger.info(
            "Langfuse tracing ACTIVE → %s (sample_rate=%s, environment=%s)",
            settings.langfuse_base_url,
            settings.langfuse_sample_rate,
            tracing_environment,
        )
    except Exception as exc:  # noqa: BLE001 — observability must never break boot
        logger.warning("Failed to initialize Langfuse; continuing untraced: %s", exc)
        _langfuse_client = None

    return _langfuse_client


def get_langfuse() -> Optional[Any]:
    """Return the global Langfuse client, or None when tracing is inactive."""
    return _langfuse_client


def shutdown_langfuse() -> None:
    """Flush buffered events and shut the client down. Safe to call always."""
    global _langfuse_client
    if _langfuse_client is None:
        return
    try:
        _langfuse_client.flush()
        _langfuse_client.shutdown()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Error during Langfuse shutdown: %s", exc)
    finally:
        _langfuse_client = None


def provider_from_base_url(base_url: Optional[str]) -> str:
    """Derive a coarse provider tag from an OpenAI-compatible base URL.

    Powers the cost/usage "by provider" breakdown independent of pricing. Falls
    back to the URL host so unrecognized gateways are still distinguishable.
    """
    if not base_url:
        return "unknown"
    host = (urlparse(base_url).hostname or base_url).lower()
    if "venice" in host:
        return "venice"
    if "openrouter" in host:
        return "openrouter"
    if "openai.com" in host:
        return "openai"
    return host


@contextmanager
def observed_trace(
    name: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    metadata: Optional[dict] = None,
):
    """Open a root span so nested LLM generations group into one trace.

    No-op (yields None) when tracing is inactive. Trace-level attributes
    (user_id/session_id/tags) are stamped on the enclosing trace via
    ``update_current_trace``. Defensive: any SDK-shape mismatch is swallowed so
    observability can never break a request.
    """
    client = get_langfuse()
    if client is None:
        yield None
        return
    try:
        span_cm = client.start_as_current_span(name=name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse start_as_current_span failed: %s", exc)
        yield None
        return
    with span_cm as span:
        try:
            attrs: dict[str, Any] = {"name": name}
            if user_id:
                attrs["user_id"] = user_id
            if session_id:
                attrs["session_id"] = session_id
            if tags:
                attrs["tags"] = list(tags)
            if metadata:
                attrs["metadata"] = metadata
            client.update_current_trace(**attrs)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Langfuse update_current_trace failed: %s", exc)
        yield span


def traced_sse(
    agen,
    name: str,
    *,
    user_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    metadata: Optional[dict] = None,
):
    """Wrap an async generator so every nested generation lands in one trace.

    Returns ``agen`` unchanged when tracing is inactive (zero overhead). The
    root span stays open for the lifetime of the stream — the nested
    ``langfuse.openai`` generations fired inside ``agen`` attach to it because
    they execute in the same task while the span is the current context.
    """
    if get_langfuse() is None:
        return agen

    async def _wrapped():
        with observed_trace(
            name,
            user_id=user_id,
            session_id=session_id,
            tags=tags,
            metadata=metadata,
        ):
            async for item in agen:
                yield item

    return _wrapped()


def _map_usage(usage: Optional[dict]) -> Optional[dict]:
    """Map an OpenAI-style usage dict to Langfuse ``usage_details`` keys."""
    if not usage:
        return None
    out: dict[str, Any] = {}
    if usage.get("prompt_tokens") is not None:
        out["input"] = usage["prompt_tokens"]
    if usage.get("completion_tokens") is not None:
        out["output"] = usage["completion_tokens"]
    if usage.get("total_tokens") is not None:
        out["total"] = usage["total_tokens"]
    return out or None


def record_generation(
    *,
    name: str,
    model: Optional[str],
    usage: Optional[dict] = None,
    input: Any = None,
    output: Any = None,
    metadata: Optional[dict] = None,
) -> None:
    """Record a one-shot generation that bypasses the OpenAI drop-in.

    For LLM/embedding calls the drop-in can't see — the Haystack embedders
    (their own internal client) and the raw-``httpx`` vision call. Creates a
    single generation carrying ``model`` + token ``usage`` so Langfuse can cost
    it. Nests under the current trace when one is active, else stands alone.
    No-op when tracing is inactive; never raises.
    """
    client = get_langfuse()
    if client is None:
        return
    try:
        gen = client.start_generation(
            name=name,
            model=model,
            input=input,
            output=output,
            metadata=metadata,
            usage_details=_map_usage(usage),
        )
        gen.end()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Langfuse record_generation failed: %s", exc)


def reset_for_tests() -> None:
    """Test-only: drop the singleton so a fresh init can run."""
    global _langfuse_client
    _langfuse_client = None
