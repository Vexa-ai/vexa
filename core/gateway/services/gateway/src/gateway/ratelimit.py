"""Per-user request rate limiting (WS-6) — the gateway edge's DoS guard.

A valid API key could otherwise fire UNLIMITED REST requests; the only prior cap was
``max_concurrent_bots`` (a resource cap on active bots, NOT a request-rate cap). This is a per-user
token bucket: ``capacity`` burst tokens, refilled at ``refill_per_sec``; one token per request, a 429
when the bucket is empty. Pure + clock-injectable, so it unit-tests without real time.

Wired at the single REST funnel (``app._forward``, keyed by the resolved ``user_id``) and constructed
from env in ``adapters.build_production_app``. Injectable into ``create_app`` so tests drive a tight
bucket; ``None`` (the default) disables it — existing harnesses that build ``create_app`` directly are
unaffected.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, Optional

# The single source of truth for "truthy" env values (case-insensitive). Shared by
# ``edge_guard._env_bool`` and ``app.py``'s ``GUARD_WS_ENABLED`` check so the three env-bool
# readers (guard enable, WS guard enable, rate-limit disable) agree on what "on" means.
_ENV_TRUTHY = frozenset(("1", "true", "yes", "on"))


def env_truthy(raw: str | None) -> bool:
    """True iff ``raw`` (stripped, lowercased) is one of ``1/true/yes/on``; ``None`` → False."""
    return raw is not None and raw.strip().lower() in _ENV_TRUTHY


class _Bucket:
    __slots__ = ("tokens", "last")

    def __init__(self, tokens: float, last: float):
        self.tokens = tokens
        self.last = last


class PerUserRateLimiter:
    """A per-key token bucket. ``allow(key)`` consumes one token, returning False when empty."""

    def __init__(self, *, capacity: float, refill_per_sec: float,
                 clock: Optional[Callable[[], float]] = None):
        if capacity <= 0 or refill_per_sec < 0:
            raise ValueError("capacity must be > 0 and refill_per_sec >= 0")
        self._capacity = float(capacity)
        self._refill = float(refill_per_sec)
        self._clock = clock or time.monotonic
        self._buckets: Dict[str, _Bucket] = {}

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = self._clock()
        b = self._buckets.get(key)
        if b is None:
            b = _Bucket(self._capacity, now)
            self._buckets[key] = b
        # Refill for the elapsed time, capped at capacity.
        b.tokens = min(self._capacity, b.tokens + (now - b.last) * self._refill)
        b.last = now
        if b.tokens >= cost:
            b.tokens -= cost
            return True
        return False


def from_env(getenv: Callable[[str, str], str] = None) -> Optional["PerUserRateLimiter"]:
    """Build the production limiter from env (generous per-user defaults), or ``None`` when disabled.

    ``GATEWAY_RATE_LIMIT_DISABLED=1`` → off. Else a per-user bucket of ``GATEWAY_RATE_LIMIT_BURST``
    (default 120) tokens refilled at ``GATEWAY_RATE_LIMIT_RPS`` (default 40)/s — high enough for normal
    dashboards, low enough to stop a single key from hammering the control plane."""
    import os as _os

    g = getenv or _os.getenv
    if env_truthy(g("GATEWAY_RATE_LIMIT_DISABLED", "")):
        return None
    burst = float(g("GATEWAY_RATE_LIMIT_BURST", "120"))
    rps = float(g("GATEWAY_RATE_LIMIT_RPS", "40"))
    return PerUserRateLimiter(capacity=burst, refill_per_sec=rps)
