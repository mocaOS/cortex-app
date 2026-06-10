"""Per-key request rate limiting (opt-in guardrail, not billing).

A dependency-free in-process token bucket keyed by API key (falling back to
client IP). Disabled by default (`RATE_LIMIT_QPM=0`); billing-grade quota
remains `MAX_QUERIES_PER_MONTH`. State is per-process — fine for the default
single-worker deployment; multi-worker stacks get per-worker buckets, which
still bounds abuse to workers × limit.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Optional, Tuple

_MAX_BUCKETS = 10_000


class _Bucket:
    __slots__ = ("tokens", "updated_at")

    def __init__(self, tokens: float, updated_at: float):
        self.tokens = tokens
        self.updated_at = updated_at


class RateLimiter:
    """Token bucket per key: capacity = burst, refill = qpm/60 tokens/sec."""

    def __init__(self):
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}

    def check(self, key: str, qpm: int, burst: int) -> Tuple[bool, float]:
        """Consume one token for `key`.

        Returns (allowed, retry_after_seconds).
        """
        if qpm <= 0:
            return True, 0.0
        rate = qpm / 60.0
        capacity = max(1, burst)
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= _MAX_BUCKETS:
                    self._evict_stale(now)
                bucket = _Bucket(tokens=float(capacity), updated_at=now)
                self._buckets[key] = bucket
            else:
                bucket.tokens = min(
                    capacity, bucket.tokens + (now - bucket.updated_at) * rate
                )
                bucket.updated_at = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0
            retry_after = (1.0 - bucket.tokens) / rate
            return False, retry_after

    def _evict_stale(self, now: float) -> None:
        """Drop the oldest half of the buckets (called under the lock)."""
        by_age = sorted(self._buckets.items(), key=lambda kv: kv[1].updated_at)
        for k, _ in by_age[: len(by_age) // 2]:
            del self._buckets[k]


_rate_limiter: Optional[RateLimiter] = None
_limiter_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        with _limiter_lock:
            if _rate_limiter is None:
                _rate_limiter = RateLimiter()
    return _rate_limiter


def rate_limit_key(api_key: Optional[str], client_ip: Optional[str]) -> str:
    """Bucket identity: hashed API key when present, else client IP."""
    if api_key:
        return "key:" + hashlib.sha256(api_key.encode()).hexdigest()[:16]
    return f"ip:{client_ip or 'unknown'}"
