"""SSRF egress guard — validate outbound-request targets before we connect.

Applied to request targets that are influenced by untrusted input: the agent
``http_request`` tool (URL chosen by the LLM, steerable via prompt injection in
ingested content), Web Import URLs, and the git provider ``base_url``. It
resolves the target host and rejects addresses that have no legitimate business
being reached from user/agent-controlled input:

* loopback (127.0.0.0/8, ::1)
* link-local — including the ``169.254.169.254`` cloud-metadata endpoint
* unspecified / multicast / reserved ranges

Private (RFC1918 / ULA) ranges are blocked or allowed per ``allow_private`` so
that self-hosted internal services (a GitLab on a private IP, an intranet crawl)
can still be reached where that is legitimate. A per-call hostname allowlist is
an explicit escape hatch for a specific internal host.

All addresses a host resolves to are checked — if *any* is disallowed the URL is
rejected, which also defeats round-robin / DNS-rebinding toward an internal
address. Callers that follow redirects should re-validate every hop (see
``async_request_hook``), since an allowed public URL can 3xx-bounce to an
internal one.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Iterable, Optional, Union
from urllib.parse import urlsplit


class SSRFError(Exception):
    """Raised when an outbound URL targets a disallowed address."""


def _parse_hosts(raw: Union[str, Iterable[str], None]) -> set:
    if not raw:
        return set()
    if isinstance(raw, str):
        return {h.strip().lower() for h in raw.split(",") if h.strip()}
    return {h.strip().lower() for h in raw if h and str(h).strip()}


def _addr_reason(ip: ipaddress._BaseAddress, allow_private: bool) -> Optional[str]:
    """Return a block reason for ``ip``, or None if it is allowed."""
    # Unwrap IPv4-mapped / 6to4-style embedded IPv4 so ::ffff:169.254.169.254
    # can't slip past the IPv4 checks.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _addr_reason(mapped, allow_private)

    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local/metadata"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_reserved:
        return "reserved"
    if not allow_private:
        if ip.is_private:
            return "private"
        # Safety net: reject anything the stdlib doesn't consider globally
        # routable (e.g. shared CGNAT space) when private targets are disallowed.
        if not ip.is_global:
            return "non-global"
    return None


def validate_url(
    url: str,
    *,
    allow_private: bool = False,
    allowlist: Union[str, Iterable[str], None] = None,
) -> None:
    """Raise :class:`SSRFError` if ``url`` targets a disallowed address.

    ``allow_private`` permits RFC1918 / ULA targets (still blocks loopback,
    link-local/metadata, and reserved ranges). ``allowlist`` is a set/CSV of
    hostnames that bypass the check entirely (explicit operator opt-in).
    """
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise SSRFError(f"blocked URL scheme: {scheme or '(none)'!r}")
    host = parts.hostname
    if not host:
        raise SSRFError("URL has no host")

    if host.lower() in _parse_hosts(allowlist):
        return

    port = parts.port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SSRFError(f"cannot resolve host {host!r}: {e}") from e
    if not infos:
        raise SSRFError(f"host {host!r} did not resolve")

    for info in infos:
        ip_str = info[4][0]
        # Drop an IPv6 scope id if present (e.g. "fe80::1%eth0").
        ip_str = ip_str.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as e:
            raise SSRFError(f"host {host!r} resolved to non-IP {ip_str!r}") from e
        reason = _addr_reason(ip, allow_private)
        if reason:
            raise SSRFError(
                f"blocked outbound target {host!r} -> {ip_str} ({reason})"
            )


def async_request_hook(
    *,
    allow_private: bool = False,
    allowlist: Union[str, Iterable[str], None] = None,
):
    """Build an httpx ``event_hooks['request']`` callback that validates every
    outbound request — including each redirect hop — with :func:`validate_url`.

    Usage::

        httpx.AsyncClient(event_hooks={"request": [async_request_hook(...)]})

    Validation runs in a thread so the brief DNS lookup doesn't block the loop.
    """
    allow = _parse_hosts(allowlist)

    async def _hook(request) -> None:
        await asyncio.to_thread(
            validate_url,
            str(request.url),
            allow_private=allow_private,
            allowlist=allow,
        )

    return _hook
