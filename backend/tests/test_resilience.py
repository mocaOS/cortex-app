"""Tests for the Phase-3 resilience layer: helper-client circuit breaker,
strict-remote conversion mode, and the per-key rate limiter."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import get_settings
from app.services.helper_client import (
    CircuitBreaker,
    HelperUnavailableError,
    _is_retryable,
)
from app.services.rate_limiter import RateLimiter, rate_limit_key


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_opens_after_threshold_and_recovers(self):
        cb = CircuitBreaker("test", failure_threshold=3, recovery_seconds=0.05)
        assert cb.state == "closed"
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "open"
        assert not cb.allow()
        import time
        time.sleep(0.06)
        assert cb.state == "half-open"
        assert cb.allow()  # one probe allowed
        cb.record_success()
        assert cb.state == "closed"

    def test_failed_probe_reopens(self):
        import time
        cb = CircuitBreaker("test", failure_threshold=1, recovery_seconds=0.05)
        cb.record_failure()
        time.sleep(0.06)
        assert cb.state == "half-open"
        cb.record_failure()
        assert cb.state == "open"

    def test_success_resets_consecutive_count(self):
        cb = CircuitBreaker("test", failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"  # never hit 3 consecutive


class TestRetryClassification:
    def test_transient_errors_retryable(self):
        assert _is_retryable(httpx.ConnectError("boom"))
        assert _is_retryable(httpx.ReadTimeout("slow"))

    def test_http_status_classification(self):
        req = httpx.Request("POST", "http://h/convert")

        def status_error(code):
            return httpx.HTTPStatusError(
                "err", request=req, response=httpx.Response(code, request=req)
            )

        assert _is_retryable(status_error(503))
        assert _is_retryable(status_error(500))
        assert not _is_retryable(status_error(413))  # oversized file → caller's problem
        assert not _is_retryable(status_error(401))

    def test_other_exceptions_not_retryable(self):
        assert not _is_retryable(ValueError("nope"))


# ---------------------------------------------------------------------------
# Strict-remote conversion mode
# ---------------------------------------------------------------------------

class TestStrictRemote:
    async def test_strict_mode_raises_instead_of_fallback(self, monkeypatch):
        from app.services import document_processor as dp

        settings = get_settings()
        settings.docling_service_url = "http://helper:3030"
        settings.helper_strict_remote = True

        async def _failing_convert(file_path, use_vision):
            raise HelperUnavailableError("down")

        monkeypatch.setattr(dp, "_convert_via_service", _failing_convert)
        try:
            with pytest.raises(RuntimeError, match="HELPER_STRICT_REMOTE"):
                await dp._convert_document_subprocess("/tmp/x.pdf", False)
        finally:
            settings.docling_service_url = ""
            settings.helper_strict_remote = False


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_disabled_when_qpm_zero(self):
        rl = RateLimiter()
        for _ in range(100):
            allowed, _ = rl.check("k", qpm=0, burst=1)
            assert allowed

    def test_burst_then_429_with_retry_after(self):
        rl = RateLimiter()
        for _ in range(5):
            allowed, _ = rl.check("k", qpm=60, burst=5)
            assert allowed
        allowed, retry_after = rl.check("k", qpm=60, burst=5)
        assert not allowed
        assert 0 < retry_after <= 1.0  # 60qpm = 1 token/sec

    def test_keys_are_isolated(self):
        rl = RateLimiter()
        for _ in range(5):
            rl.check("a", qpm=60, burst=5)
        allowed, _ = rl.check("b", qpm=60, burst=5)
        assert allowed

    def test_tokens_refill_over_time(self, monkeypatch):
        import app.services.rate_limiter as rlm

        now = [100.0]
        monkeypatch.setattr(rlm.time, "monotonic", lambda: now[0])
        rl = RateLimiter()
        for _ in range(5):
            rl.check("k", qpm=60, burst=5)
        assert rl.check("k", qpm=60, burst=5)[0] is False
        now[0] += 2.0  # 2 tokens refilled
        assert rl.check("k", qpm=60, burst=5)[0] is True
        assert rl.check("k", qpm=60, burst=5)[0] is True
        assert rl.check("k", qpm=60, burst=5)[0] is False

    def test_key_derivation(self):
        a = rate_limit_key("secret-key", "1.2.3.4")
        b = rate_limit_key("secret-key", "5.6.7.8")
        assert a == b  # api key wins over ip
        assert a.startswith("key:")
        assert "secret-key" not in a  # hashed, never stored raw
        assert rate_limit_key(None, "1.2.3.4") == "ip:1.2.3.4"


class TestRateLimitEndpointWiring:
    def test_429_surface(self, client, monkeypatch):
        """With a 0-burst limit the ask endpoint returns 429 + Retry-After."""
        settings = get_settings()
        settings.rate_limit_qpm = 1
        settings.rate_limit_burst = 1
        try:
            r1 = client.post("/api/ask", json={"question": "hi"})
            assert r1.status_code != 429  # first request passes the limiter
            r2 = client.post("/api/ask", json={"question": "hi"})
            assert r2.status_code == 429
            assert "Retry-After" in r2.headers
        finally:
            settings.rate_limit_qpm = 0
            # reset the singleton so other tests start clean
            import app.services.rate_limiter as rlm
            rlm._rate_limiter = None
