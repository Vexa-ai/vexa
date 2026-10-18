"""Hand-off of meetings a 0.12 bot is still transcribing when meeting-api is upgraded to 0.13.

0.13's collector admits a ``transcription_segments`` entry only when it carries the writing session's
MeetingToken (``auth``) and an HMAC over its payload (``sig``) — ``ingest._admitted``. A 0.12 bot
writes neither, so every segment a bot started before the upgrade writes after it would be refused:
the live ``/ws`` frames still flow (the bot publishes them itself), but nothing reaches the
transcript the API and the dashboard read afterwards. Bots are not restarted by a deploy, so on a
production cut-over every meeting in flight would lose the rest of its transcript.

This module admits exactly those entries, and only for a bounded time:

* **Which meetings.** At boot, meeting-api records the meetings that are in flight (requested,
  joining, awaiting admission, active, needs help, stopping) AND were spawned before 0.13. A 0.13
  spawn always stores ``data.transcription_language`` on the meeting row (every spawn path:
  ``bot_spawn.service``), and 0.12 never wrote that key, so its absence marks a 0.12 spawn. A
  meeting spawned after the upgrade is never on the list, whatever its status.
* **Which entries.** Only an entry with NO ``auth`` and NO ``sig`` field — the 0.12 shape. An entry
  that carries a signature which does not verify is still refused.
* **How long.** ``VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S`` seconds from boot (default 14400, four
  hours; production's 7-day p95 bot run is about 90 minutes). ``0`` turns the hand-off off.

Each admitted entry is logged (``segment_entry_legacy_admitted``) so the cut-over watch can see the
hand-off working and see it end.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Iterable, Optional

WINDOW_ENV = "VEXA_COLLECTOR_LEGACY_UNSIGNED_WINDOW_S"
DEFAULT_WINDOW_S = 14400

#: Statuses of a meeting whose bot may still be writing segments.
IN_FLIGHT_STATUSES = ("requested", "joining", "awaiting_admission", "active", "needs_help", "stopping")
#: The key every 0.13 spawn writes into ``meetings.data``; a 0.12 spawn never did.
MARKER_0_13 = "transcription_language"

log = logging.getLogger(__name__)


def window_seconds(env: Optional[dict] = None) -> int:
    raw = ((env if env is not None else os.environ).get(WINDOW_ENV) or "").strip()
    if not raw:
        return DEFAULT_WINDOW_S
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning("%s: not a number of seconds; using %d", WINDOW_ENV, DEFAULT_WINDOW_S)
        return DEFAULT_WINDOW_S


class LegacyUnsignedWindow:
    """The meetings whose unsigned (0.12-shaped) entries are admitted, and until when."""

    def __init__(self) -> None:
        self.meeting_ids: frozenset[int] = frozenset()
        self.until: float = 0.0

    def open(self, meeting_ids: Iterable[int], window_s: int, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self.meeting_ids = frozenset(int(m) for m in meeting_ids) if window_s > 0 else frozenset()
        self.until = now + window_s if window_s > 0 else 0.0

    def close(self) -> None:
        self.meeting_ids, self.until = frozenset(), 0.0

    def admits(self, fields: dict, now: Optional[float] = None) -> bool:
        """True for a 0.12-shaped entry (no ``auth``, no ``sig``) of a listed meeting, inside the window."""
        if not self.meeting_ids:
            return False
        if "auth" in fields or "sig" in fields:
            return False
        if (time.time() if now is None else now) >= self.until:
            return False
        try:
            payload = fields.get("payload")
            data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
            meeting_id = int(data.get("meeting_id"))
        except (TypeError, ValueError, AttributeError):
            return False
        if meeting_id not in self.meeting_ids:
            return False
        try:
            from ..obs import log_event

            log_event("segment_entry_legacy_admitted", audience="system", level="info",
                      span="collector.ingest", fields={"meeting_id": meeting_id})
        except Exception:  # noqa: BLE001 — logging never stops an admission
            pass
        return True


#: The process-wide window the stream-facing callers consult (``ingest._admitted``).
WINDOW = LegacyUnsignedWindow()


def _sql(query: str):
    """``sqlalchemy.text`` when SQLAlchemy is installed (the service image); the bare string where it is
    not (the offline unit venv, which drives this with a fake session)."""
    try:
        from sqlalchemy import text
    except ImportError:  # pragma: no cover — the offline venv only
        return query
    return text(query)


async def _in_flight_0_12(session_factory) -> list[int]:
    statuses = ", ".join(f"'{s}'" for s in IN_FLIGHT_STATUSES)
    query = _sql(
        f"SELECT id FROM meetings WHERE status IN ({statuses}) "
        f"AND NOT (COALESCE(data::jsonb, '{{}}'::jsonb) ? :marker)"
    )
    async with session_factory() as session:
        rows = (await session.execute(query, {"marker": MARKER_0_13})).all()
    return [int(r[0]) for r in rows]


async def open_from_db(session_factory, env: Optional[dict] = None,
                       window: LegacyUnsignedWindow = WINDOW) -> int:
    """At boot: read the in-flight 0.12 meetings and open the window over them. Returns how many."""
    seconds = window_seconds(env)
    if seconds <= 0 or session_factory is None:
        window.close()
        return 0
    ids = await _in_flight_0_12(session_factory)
    window.open(ids, seconds)
    if ids:
        log.warning("collector: admitting unsigned segment entries of %d meeting(s) spawned before "
                    "0.13 for %d s (%s)", len(ids), seconds, WINDOW_ENV)
    return len(ids)


async def refresh_from_db(session_factory, window: LegacyUnsignedWindow = WINDOW,
                          now: Optional[float] = None) -> int:
    """While the window is open, add in-flight 0.12 meetings that appeared since boot — during a
    rolling upgrade a 0.12 replica may still spawn a 0.12 bot after this replica booted. The window's
    end never moves, and a 0.13 spawn is never added (it carries the marker)."""
    now = time.time() if now is None else now
    if session_factory is None or not window.until or now >= window.until:
        return 0
    added = set(await _in_flight_0_12(session_factory)) - set(window.meeting_ids)
    if added:
        window.meeting_ids = window.meeting_ids | frozenset(added)
    return len(added)
