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

# Sentinel substituted for any user/model authored text when content masking is
# on (LANGFUSE_LOG_EXTENDED=false, the default).
_REDACTED = "[REDACTED]"

# Keys whose VALUES are structural and safe to keep verbatim. Everything else is
# treated as authored content and redacted. Kept deliberately small + explicit
# so the policy is deny-by-default.
_KEEP_MESSAGE_KEYS = frozenset(
    {"role", "name", "tool_call_id", "finish_reason", "type", "index", "id"}
)
_KEEP_METADATA_KEYS = frozenset({"stage", "endpoint", "mode", "provider"})
# Top-level chat-request parameters that are structural (NOT authored content) and
# kept verbatim. Numerics/bools survive regardless; this list covers string-valued
# params (chiefly `model`). Unknown string params still fail closed.
_KEEP_PARAM_KEYS = frozenset(
    {
        "model",
        "tool_choice",
        "reasoning_effort",
        "service_tier",
        "user",
    }
)


def _mask_content(*, data: Any) -> Any:
    """Redact all authored text from a Langfuse field, keeping only structure.

    Registered as the SDK's legacy ``mask=`` hook, so it runs **client-side
    before export** once per field (``input`` / ``output`` / ``metadata``) and is
    NOT told which field it is — classification is purely structural (object
    shape + keys + message ``role``). Receives the real Python object (dict /
    list / str), not stringified JSON.

    Policy (deny-by-default): redact every message ``content``, tool-call
    argument **values**, tool/function **description** strings, embedding inputs,
    vision text, and any unclassifiable string leaf. Keep roles, names,
    tool/function names + argument/parameter **keys**, allow-listed metadata
    keys, and all numeric/bool values (tokens, cost, latency).

    **Total** — never raises. On any internal error or ambiguity it returns
    ``_REDACTED`` (fail closed). The SDK also fails closed if a mask hook raises,
    but we keep structure rather than nuke the whole field.
    """
    try:
        return _mask(data)
    except Exception:  # noqa: BLE001 — masking must never raise (fail closed)
        return _REDACTED


def _mask(data: Any) -> Any:
    # Pass through structural scalars untouched (token usage, cost, latency, flags).
    if data is None or isinstance(data, (bool, int, float)):
        return data
    # Any bare string is authored content (embedding input, XML/JSON output, vision).
    if isinstance(data, str):
        return _REDACTED
    if isinstance(data, list):
        return [_mask(item) for item in data]
    if isinstance(data, dict):
        return _mask_dict(data)
    # Unknown leaf type → fail closed.
    return _REDACTED


def _mask_dict(data: dict) -> dict:
    keys = data.keys()
    # Chat input: a request dict carrying messages / tools / functions.
    if "messages" in keys or "tools" in keys or "functions" in keys:
        out: dict[str, Any] = {}
        for key, value in data.items():
            if key == "messages":
                out[key] = [_mask_message(m) for m in value] if isinstance(value, list) else _mask(value)
            elif key in ("tools", "functions"):
                out[key] = [_mask_tool_def(t) for t in value] if isinstance(value, list) else _mask(value)
            elif key in _KEEP_PARAM_KEYS:
                out[key] = value  # structural request param (e.g. model)
            else:
                # Other request params: keep numerics/bools/nested numerics, redact
                # stray strings (fail closed for anything unexpected).
                out[key] = _mask_metadata_value(key, value)
        return out
    # A single chat message or a completion output (content / tool_calls).
    if "role" in keys or "content" in keys or "tool_calls" in keys or "function_call" in keys:
        return _mask_message(data)
    # A tool/function definition standing alone.
    if "function" in keys or "parameters" in keys:
        return _mask_tool_def(data)
    # Otherwise treat as a metadata dict: keep allow-listed keys + numerics.
    return {k: _mask_metadata_value(k, v) for k, v in data.items()}


def _mask_message(msg: Any) -> Any:
    """Mask one chat message / completion: keep role & friends, redact content."""
    if not isinstance(msg, dict):
        return _mask(msg)
    out: dict[str, Any] = {}
    for key, value in msg.items():
        if key in _KEEP_MESSAGE_KEYS:
            out[key] = value
        elif key == "content":
            out[key] = _REDACTED if value is not None else None
        elif key == "tool_calls":
            out[key] = [_mask_tool_call(tc) for tc in value] if isinstance(value, list) else _mask(value)
        elif key == "function_call":
            out[key] = _mask_tool_call({"function": value}).get("function") if isinstance(value, dict) else _mask(value)
        else:
            # Unknown message field → fail closed.
            out[key] = _mask(value)
    return out


def _mask_tool_call(tc: Any) -> Any:
    """Keep a tool call's function name + argument KEYS; redact argument values."""
    if not isinstance(tc, dict):
        return _mask(tc)
    out: dict[str, Any] = {}
    for key, value in tc.items():
        if key == "function" and isinstance(value, dict):
            fn: dict[str, Any] = {}
            for fk, fv in value.items():
                if fk == "name":
                    fn[fk] = fv
                elif fk == "arguments":
                    fn[fk] = _mask_arguments(fv)
                else:
                    fn[fk] = _mask(fv)
            out[key] = fn
        elif key in ("id", "type", "index"):
            out[key] = value
        else:
            out[key] = _mask(value)
    return out


def _mask_arguments(args: Any) -> Any:
    """Redact tool-call argument VALUES while keeping the KEYS.

    Arguments arrive either as a dict or as a JSON string (the OpenAI SDK shape).
    For a JSON string we parse to recover the keys, then re-emit them with redacted
    values; if it won't parse, fail closed.
    """
    if isinstance(args, dict):
        return {k: _REDACTED for k in args}
    if isinstance(args, str):
        import json

        try:
            parsed = json.loads(args)
        except Exception:  # noqa: BLE001
            return _REDACTED
        if isinstance(parsed, dict):
            return json.dumps({k: _REDACTED for k in parsed})
        return _REDACTED
    return _REDACTED


def _mask_tool_def(tool: Any) -> Any:
    """Keep a tool/function definition's name + parameter property KEYS; redact
    all description strings and any default/example values."""
    if not isinstance(tool, dict):
        return _mask(tool)
    out: dict[str, Any] = {}
    for key, value in tool.items():
        if key == "type":
            out[key] = value
        elif key == "function" and isinstance(value, dict):
            out[key] = _mask_function_spec(value)
        elif key in ("name", "parameters"):
            # A bare function spec (no "function" wrapper).
            out.update(_mask_function_spec(tool))
            return out
        else:
            out[key] = _mask(value)
    return out


def _mask_function_spec(spec: dict) -> dict:
    out: dict[str, Any] = {}
    for key, value in spec.items():
        if key == "name":
            out[key] = value
        elif key == "description":
            out[key] = _REDACTED
        elif key == "parameters":
            out[key] = _mask_json_schema(value)
        else:
            out[key] = _mask(value)
    return out


def _mask_json_schema(schema: Any) -> Any:
    """Keep JSON-schema structure (property KEYS, ``type``, ``required``); redact
    ``description``/``enum``/``default``/``example`` and any other string leaf."""
    if isinstance(schema, list):
        return [_mask_json_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema if isinstance(schema, (bool, int, float)) or schema is None else _REDACTED
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in ("type", "required", "additionalProperties"):
            out[key] = value
        elif key == "properties" and isinstance(value, dict):
            # Keep property NAMES; recurse into each property's schema.
            out[key] = {prop: _mask_json_schema(sub) for prop, sub in value.items()}
        elif key in ("items", "$defs", "definitions"):
            out[key] = _mask_json_schema(value)
        else:
            # description / enum / default / example / title → authored, redact.
            out[key] = _mask(value)
    return out


def _mask_metadata_value(key: str, value: Any) -> Any:
    """Keep allow-listed metadata keys and numerics/bools; redact other strings."""
    if key in _KEEP_METADATA_KEYS:
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (dict, list)):
        # Recurse: nested numerics survive, nested strings get redacted.
        return _mask(value)
    return _REDACTED


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

    # Content masking. By default (LANGFUSE_LOG_EXTENDED=false) we wire the
    # client-side `mask` hook so ALL user/model authored text is redacted before
    # export — only structure (roles, model/params, tool names + arg keys,
    # tokens, cost, latency) reaches the server. Set LANGFUSE_LOG_EXTENDED=true to
    # log full content for local debugging (no mask hook). The hook covers every
    # call site at once because the langfuse.openai drop-in routes input/output
    # through it; no per-call-site changes needed.
    mask = None if settings.langfuse_log_extended else _mask_content

    try:
        from langfuse import Langfuse

        _langfuse_client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            sample_rate=settings.langfuse_sample_rate,
            environment=tracing_environment,
            mask=mask,
        )
        # Eagerly apply the global OpenAI instrumentation so EVERY openai-SDK
        # call is auto-traced — including libraries that build their own client
        # (Haystack's embedders). Done at startup so it's active before the first
        # embedding/LLM call, independent of order. (The client factory's
        # langfuse.openai import is then belt-and-suspenders.)
        import langfuse.openai  # noqa: F401
        logger.info(
            "Langfuse tracing ACTIVE → %s (sample_rate=%s, environment=%s, content=%s)",
            settings.langfuse_base_url,
            settings.langfuse_sample_rate,
            tracing_environment,
            "extended" if settings.langfuse_log_extended else "masked",
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
