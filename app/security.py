"""Request authentication and rate limiting.

Two layers, both intentionally simple:

  * A shared secret in `X-API-Key`, compared with `hmac.compare_digest` to
    avoid a timing side channel. Disabled when API_KEY is empty so the project
    runs out of the box; the README says to set it before deploying.
  * An in-process sliding-window rate limiter on the public intake path. It is
    per-worker, not distributed - fine for a single container, and the place
    you would swap in Redis if you ran several. Being explicit about that
    limitation is more useful than pretending it scales.

An optional HMAC-SHA256 body signature is also provided for the n8n -> API
hop, which is the right control when the two services are not on the same
private network.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict, deque
from typing import Deque

from fastapi import Header, HTTPException, Request, status

from .config import get_settings


async def require_api_key(x_api_key: str = Header(default="")) -> None:
    """Dependency guarding mutating endpoints."""
    expected = get_settings().api_key
    if not expected:
        return  # auth disabled for local development
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-API-Key",
        )


def sign_body(secret: str, body: bytes) -> str:
    """HMAC-SHA256 hex digest of a raw request body."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, provided: str) -> bool:
    if not secret:
        return True
    return hmac.compare_digest(sign_body(secret, body), (provided or "").strip())


class SlidingWindowLimiter:
    """Per-key sliding window counter."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > self.window:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True

    def retry_after(self, key: str) -> int:
        bucket = self._hits.get(key)
        if not bucket:
            return 0
        return max(1, int(self.window - (time.monotonic() - bucket[0])))


_limiter: SlidingWindowLimiter | None = None


def get_limiter() -> SlidingWindowLimiter:
    global _limiter
    if _limiter is None:
        s = get_settings()
        _limiter = SlidingWindowLimiter(s.rate_limit_requests, s.rate_limit_window_seconds)
    return _limiter


async def rate_limit(request: Request) -> None:
    limiter = get_limiter()
    key = request.client.host if request.client else "unknown"
    if not limiter.allow(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": str(limiter.retry_after(key))},
        )
