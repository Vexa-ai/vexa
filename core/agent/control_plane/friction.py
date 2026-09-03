"""friction.py (control plane) — WHERE THE ROUGH EDGES LIVE (PRD decision 33).

The record's shape, dedup key and status machine are in `shared/friction.py`; this is only the
store. Redis when a client is wired (`agent:friction:<id>` holds the JSON record, `agent:friction:
key:<dedup>` maps a dedup key to the id that owns it, `agent:friction:all` is a sorted set scored by
report time), in-memory otherwise — the same three-key shape and the same fallback discipline as
`ScaffoldStore` and `_Sessions`, for the same reason: the unit tests need no redis and the
deployment needs no second store.

WHY REDIS AND NOT THE FLOWS `friction` TABLE the rig has been writing: `shared/friction.py`'s module
docstring states the four reasons in full. The short one is that the people half of decision 33
posts to THIS service, and this service cannot reach the flows lane.

WHY A SORTED SET AND NOT A KEY SCAN. Every read here is "everything since <t>, optionally filtered
by status" — the dump, `friction_so_far`, and the fixing agent's next pass. That is a range query,
and `SCAN agent:friction:*` answers it by reading every record on the instance and throwing most of
them away. The score is the LAST report time, so a defect filed at nine and hit again at five sorts
where a reader expects it.

RETENTION: none, deliberately. This is a defect ledger; a row is interesting until it is fixed and
then it is evidence that it was. Nothing here expires, and the blank script must not delete it
either — the environment being reset is exactly when the record of what broke matters.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Optional

from shared import friction as fr

logger = logging.getLogger("agent_api.friction")

ID_BYTES = 8            # short enough to paste into a `friction_fixed([...])` call by hand


class FrictionStore:
    """Durable friction records, keyed by id, deduplicated by `shared.friction.dedup_key`."""

    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client
        self._mem: dict[str, dict] = {}
        self._by_key: dict[str, str] = {}

    # ── keys ──
    @staticmethod
    def _key(rid: str) -> str:
        return f"agent:friction:{rid}"

    @staticmethod
    def _dedup_key(dk: str) -> str:
        return f"agent:friction:key:{dk}"

    INDEX = "agent:friction:all"

    # ── writes ──
    def file(self, raw: dict, *, now: float | None = None) -> dict:
        """File one report. Returns the stored record — NEW or folded into the one it duplicates.

        The caller is told which by reading `recurrence`: 1 means this is the first time anyone has
        seen it. That matters to the reporter — an agent that files the same edge for the fourth
        time should be told the count, not thanked as if it were news."""
        rec = fr.normalize(raw, now=now)
        dk = fr.dedup_key(rec)
        existing = self.get(self._id_for_key(dk)) if self._id_for_key(dk) else None
        merged = fr.apply_report(existing, rec, now=now)
        merged["id"] = (existing or {}).get("id") or f"fr_{secrets.token_hex(ID_BYTES)}"
        merged["dedup_key"] = dk
        self._put(merged)
        self._bind_key(dk, merged["id"])
        return merged

    def fix(self, rid: str, fix_ref: str, *, now: float | None = None) -> Optional[dict]:
        """Close one record against the change that addressed it. None when the id is unknown."""
        rec = self.get(rid)
        if rec is None:
            return None
        out = fr.apply_fix(rec, fix_ref, now=now)
        self._put(out)
        return out

    # ── reads ──
    def get(self, rid: str) -> Optional[dict]:
        if not rid:
            return None
        if self._redis is not None:
            raw = self._redis.get(self._key(rid))
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                logger.warning("friction record %s is unreadable in the store", rid)
                return None
        return self._mem.get(rid)

    def since(self, ts: float = 0.0, *, status: str = "", limit: int = 500) -> list[dict]:
        """Records reported at or after `ts`, newest first, optionally one status.

        `status="open"` means OPEN OR RECURRING, and that is not a shortcut. A fixing agent asking
        for "what is open" wants the work; a recurring row is the most urgent work there is, and
        excluding it because its status string differs would hide exactly the rows that say a
        previous pass was wrong. `status="recurring"` still selects only those."""
        if self._redis is not None:
            ids = self._redis.zrevrangebyscore(self.INDEX, "+inf", ts, start=0, num=limit) or []
        else:
            ids = [r["id"] for r in sorted(self._mem.values(),
                                           key=lambda r: float(r.get("at") or 0), reverse=True)
                   if float(r.get("at") or 0) >= ts][:limit]
        rows = [r for r in (self.get(i) for i in ids) if r]
        want = str(status or "").strip().lower()
        if want == "open":
            rows = [r for r in rows if r.get("status") in ("open", "recurring")]
        elif want in fr.STATUSES:
            rows = [r for r in rows if r.get("status") == want]
        return rows

    # ── internals ──
    def _put(self, rec: dict) -> None:
        if self._redis is not None:
            self._redis.set(self._key(rec["id"]), json.dumps(rec))
            self._redis.zadd(self.INDEX, {rec["id"]: float(rec.get("at") or time.time())})
        else:
            self._mem[rec["id"]] = rec

    def _bind_key(self, dk: str, rid: str) -> None:
        if self._redis is not None:
            self._redis.set(self._dedup_key(dk), rid)
        else:
            self._by_key[dk] = rid

    def _id_for_key(self, dk: str) -> str:
        if self._redis is not None:
            return self._redis.get(self._dedup_key(dk)) or ""
        return self._by_key.get(dk, "")


def parse_since(since: str, *, now: float | None = None) -> float:
    """`""` → everything · `900` / `15m` / `2h` / `3d` → that long ago · an ISO instant → itself.

    A dump asked for "since 1h" and given a wall-clock epoch it could not parse would silently
    return the whole ledger, which reads as "nothing was fixed today". Unparseable input therefore
    means EVERYTHING and the caller is told so by the dump's own scope line, rather than being
    handed a plausible wrong window."""
    s = str(since or "").strip()
    if not s:
        return 0.0
    now = float(now if now is not None else time.time())
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if s[-1:].lower() in units and s[:-1].replace(".", "", 1).isdigit():
        return now - float(s[:-1]) * units[s[-1].lower()]
    if s.replace(".", "", 1).isdigit():
        v = float(s)
        # A bare number is an epoch when it looks like one (>= 2001) and a duration otherwise.
        return v if v > 1_000_000_000 else now - v
    try:
        from datetime import datetime, timezone
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.timestamp()
    except ValueError:
        logger.warning("friction dump: unparseable since=%r — returning everything", s)
        return 0.0
