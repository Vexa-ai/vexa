"""The per-user delivery ledger — the queryable record of webhook delivery outcomes.

#841: deliveries reach the receiver, but the dashboard's Delivery History was blind to them —
the core reported every outcome (#815→#817) only as a ``logevent.v1`` system log, which rotates
and is not a user-facing surface. The dashboard read a *different* store that only its own Test
button (and a legacy-era path) ever wrote, so "Delivered 27 / Failed 0" was an artifact of stale
rows, not a statement about current deliveries.

This is the user-facing completion of #815→#817: outcome → observable → **queryable**. Each
delivery attempt (the initial POST at the lifecycle callback) is recorded here, keyed by the
subscriber's ``user_id``, and read back by ``GET /webhooks/deliveries`` (gateway-fronted at
``/user/webhook/deliveries``). Logs rotate; users need history.

**What a row carries (P14 — never a URL or a secret):** ``event_type``, ``event_id``, the target
**host only** (a webhook URL can carry a token in its path/query — the same rule #817's logs
follow), ``outcome`` (the #817 taxonomy: ``delivered | queued | suppressed | blocked | failed``),
``status_code``, ``attempt``, and ``created_at``. Never ``webhook_url`` or ``webhook_secret``.

The store is a port. ``InMemoryDeliveryLedger`` backs the app-factory / conformance path (no
redis); ``RedisDeliveryLedger`` is the production adapter — a per-user capped Redis list, the same
shape the RetryQueue uses (LPUSH newest-first, LTRIM to the cap). Recording is best-effort and MUST
never fail the bot's lifecycle callback (P3a): a ledger hiccup is swallowed by the caller.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Keep the newest N deliveries per user. History, not an audit log of all time — the dashboard
# shows a recent window, and an unbounded per-user list would grow without limit.
DEFAULT_MAX_PER_USER = 100

# The fields a ledger row is allowed to carry. Enforced by `build_delivery_record` so a URL or a
# secret can never leak into the ledger even if a caller passes extra keys (P14).
_ALLOWED_FIELDS = (
    "event_type", "event_id", "target_host", "outcome",
    "status_code", "attempt", "created_at", "meeting_id",
)


def build_delivery_record(
    *,
    event_type: Optional[str],
    event_id: Optional[str],
    target_host: str,
    outcome: str,
    status_code: Optional[int] = None,
    attempt: int = 0,
    meeting_id: Optional[Any] = None,
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one ledger row from a delivery outcome — host only, never the URL or secret (P14)."""
    return {
        "event_type": event_type,
        "event_id": event_id,
        "target_host": target_host,
        "outcome": outcome,
        "status_code": status_code,
        "attempt": attempt,
        "meeting_id": meeting_id,
        "created_at": created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _sanitize(record: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only the allowed fields — a belt-and-braces guard against a URL/secret ever landing."""
    return {k: record.get(k) for k in _ALLOWED_FIELDS}


class InMemoryDeliveryLedger:
    """In-process per-user ledger (the app-factory / conformance default; no redis)."""

    def __init__(self, max_per_user: int = DEFAULT_MAX_PER_USER):
        self.max_per_user = max_per_user
        self._by_user: Dict[str, List[Dict[str, Any]]] = {}

    async def record(self, user_id: Any, record: Dict[str, Any]) -> None:
        if user_id is None:
            return
        rows = self._by_user.setdefault(str(user_id), [])
        rows.insert(0, _sanitize(record))  # newest first
        del rows[self.max_per_user:]  # cap

    async def list(self, user_id: Any, limit: int = DEFAULT_MAX_PER_USER) -> List[Dict[str, Any]]:
        if user_id is None:
            return []
        return list(self._by_user.get(str(user_id), [])[:limit])


class RedisDeliveryLedger:
    """Production adapter: a per-user capped Redis list (LPUSH newest-first + LTRIM to the cap).

    Keyed ``webhook:deliveries:{user_id}``. The redis client is async (``redis.asyncio``); recording
    is best-effort — the caller swallows any error so a ledger outage never fails a delivery."""

    def __init__(self, redis: Any, max_per_user: int = DEFAULT_MAX_PER_USER,
                 key_prefix: str = "webhook:deliveries:"):
        self.redis = redis
        self.max_per_user = max_per_user
        self.key_prefix = key_prefix

    def _key(self, user_id: Any) -> str:
        return f"{self.key_prefix}{user_id}"

    async def record(self, user_id: Any, record: Dict[str, Any]) -> None:
        if user_id is None:
            return
        key = self._key(user_id)
        await self.redis.lpush(key, json.dumps(_sanitize(record)))
        await self.redis.ltrim(key, 0, self.max_per_user - 1)

    async def list(self, user_id: Any, limit: int = DEFAULT_MAX_PER_USER) -> List[Dict[str, Any]]:
        if user_id is None:
            return []
        raw = await self.redis.lrange(self._key(user_id), 0, limit - 1)
        out: List[Dict[str, Any]] = []
        for item in raw or []:
            if isinstance(item, (bytes, bytearray)):
                item = item.decode()
            try:
                out.append(json.loads(item))
            except (TypeError, ValueError):
                continue
        return out
