"""Request body size enforcement.

Pure-ASGI middleware that rejects oversized request bodies with 413 before
they can pressure the container's memory. Two checks:

1. Content-Length header (cheap, covers well-behaved clients) — rejected
   before the endpoint ever runs.
2. Streamed byte count (covers chunked/streaming bodies with no
   Content-Length) — the wrapped `receive` aborts mid-stream once the
   limit is crossed.

Limits are resolved per path:
- library import routes (`/api/admin/import*`) stream to disk, so they get
  the (much larger) `max_import_body_mb`;
- file-upload routes (`/api/upload*`, `/api/documents/{id}/reprocess`) get
  `max_file_size_mb` plus slack for multipart framing;
- everything else gets `max_request_body_mb`.

A limit of 0 disables enforcement for that class of route.
"""

import json
import logging

logger = logging.getLogger(__name__)

_MULTIPART_SLACK_BYTES = 8 * 1024 * 1024  # multipart boundaries/headers around the file


class _BodyTooLarge(Exception):
    def __init__(self, limit: int):
        self.limit = limit


def _resolve_limit_bytes(path: str, settings) -> int:
    """Return the body limit in bytes for this request path (0 = unlimited)."""
    if path.startswith("/api/admin/import"):
        return settings.max_import_body_mb * 1024 * 1024
    if path.startswith("/api/upload") or (
        path.startswith("/api/documents/") and path.endswith("/reprocess")
    ):
        if settings.max_file_size_mb <= 0:
            return 0
        return settings.max_file_size_mb * 1024 * 1024 + _MULTIPART_SLACK_BYTES
    return settings.max_request_body_mb * 1024 * 1024


class BodySizeLimitMiddleware:
    """ASGI middleware enforcing per-route request body size limits."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from app.config import get_settings

        settings = get_settings()
        if settings.max_request_body_mb <= 0:
            # Middleware disabled globally.
            await self.app(scope, receive, send)
            return

        limit = _resolve_limit_bytes(scope.get("path", ""), settings)
        if limit <= 0:
            await self.app(scope, receive, send)
            return

        # Fast path: reject on declared Content-Length.
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    break
                if declared > limit:
                    await self._send_413(scope, send, limit)
                    return
                break

        # Slow path: count streamed body bytes and abort once over limit.
        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge(limit)
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await self._send_413(scope, send, limit)
            # If the response already started there is nothing safe to send;
            # the connection is torn down by the server.

    @staticmethod
    async def _send_413(scope, send, limit: int):
        logger.warning(
            "Rejected oversized request body on %s (limit %d bytes)",
            scope.get("path", "?"),
            limit,
        )
        body = json.dumps(
            {"detail": f"Request body too large. Maximum size: {limit // (1024 * 1024)}MB"}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
