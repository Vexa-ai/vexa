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

    @property
    def refill_per_sec(self) -> float:
        return self._refill

    def _refilled(self, key: str) -> "_Bucket":
        now = self._clock()
        b = self._buckets.get(key)
        if b is None:
            b = self._buckets[key] = _Bucket(self._capacity, now)
        b.tokens = min(self._capacity, b.tokens + (now - b.last) * self._refill)
        b.last = now
        return b

    def has_token(self, key: str) -> bool:
        """Whether ``key`` could spend a token now, without spending it."""
        return self._refilled(key).tokens >= 1.0

    def debit(self, key: str) -> None:
        """Spend one token after the fact (a start that succeeded). Concurrent successes may take
        the bucket below zero; it then refills from there, so the debt is paid, never forgiven."""
        self._refilled(key).tokens -= 1.0

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


#: The ad-hoc bot start, per account (founder, 4 Nov 2026): ``POST /bots`` on this edge. A second
#: bucket beside the request-rate one, keyed the same way (the resolved user id), in memory — so a
#: replica counts only the starts it sees, and N replicas let up to N times the burst through.
#: Scheduled starts never pass here: meeting-api's auto-join sweep spawns in process, and Flows calls
#: the service door, its own listener. Defaults sized from hosted prod's ad-hoc starts (30 days to
#: 4 Nov 2026: an account's busiest minute held at most 4, its busiest 10 minutes 6, its busiest hour
#: 13; this bucket would have refused none of them) and equal to meeting-api ``limits.py``.
BOT_START_PATH = "/bots"
DEFAULT_BOT_START_BURST = 10
DEFAULT_BOT_START_PER_MIN = 0.5


def is_bot_start(method: str, path: str) -> bool:
    return method.upper() == "POST" and (path.rstrip("/") or "/") == BOT_START_PATH


def bot_start_key(user_data: dict) -> str:
    """Whose bucket a start spends: the account that pays for the bot. A room's principal bills to
    its payer (``delegation.bills_to``), so every principal of one payer shares one bucket."""
    dlg = user_data.get("delegation") if isinstance(user_data.get("delegation"), dict) else {}
    payer = dlg.get("bills_to")
    if isinstance(payer, int) and not isinstance(payer, bool):
        return str(payer)
    return str(user_data["user_id"])


def bot_start_from_env(getenv: Callable[[str, str], str] = None) -> Optional["PerUserRateLimiter"]:
    """The bot-start bucket from env: ``GATEWAY_BOT_START_BURST`` starts (default 10), refilled at
    ``GATEWAY_BOT_START_PER_MIN`` (default 0.5) a minute; ``GATEWAY_BOT_START_LIMIT_DISABLED=1`` → off."""
    import os as _os

    g = getenv or _os.getenv
    if env_truthy(g("GATEWAY_BOT_START_LIMIT_DISABLED", "")):
        return None
    burst = float(g("GATEWAY_BOT_START_BURST", str(DEFAULT_BOT_START_BURST)))
    per_min = float(g("GATEWAY_BOT_START_PER_MIN", str(DEFAULT_BOT_START_PER_MIN)))
    return PerUserRateLimiter(capacity=burst, refill_per_sec=per_min / 60.0)


def retry_after_s(limiter: "PerUserRateLimiter") -> int:
    """Seconds until one token is back in an empty bucket."""
    import math

    return max(1, math.ceil(1.0 / limiter.refill_per_sec)) if limiter.refill_per_sec > 0 else 3600


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
