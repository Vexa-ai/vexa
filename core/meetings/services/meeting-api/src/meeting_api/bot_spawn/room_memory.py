"""How a Google Meet room admits a bot, read from that room's own past meetings (D402, founder 13 Nov 2026;
design fact 30142e12 on desk:meet-join).

Google refuses or admits an anonymous bot PER ROOM, and it does so deterministically: a room whose host
policy shuts anonymous guests out answers ``CreateMeetingDevice`` 403 to every anonymous knock, from any
pod. So the bot's join mode is chosen per room, never by trying anonymous first and falling back:

  * a room that REFUSED an anonymous bot in the last WINDOW_DAYS → ``needs_sign_in``: the first bot joins
    signed-in, with a company bot account;
  * a room that ADMITTED an anonymous bot → ``anonymous_ok``: anonymous, as before;
  * a room never seen in the window → ``unseen``: anonymous; a 403 then marks it (the refused attempt on
    its row) and the next pod joins signed-in.

The latest decisive outcome inside the window wins. Only an ANONYMOUS bot's outcome decides: a signed-in
bot getting in says nothing about whether an anonymous one would. Nothing new is stored: the outcomes
are what the meetings table already holds (``data.meet_respawn.attempts`` for refusals, ``start_time``
and ``data.auth_userdata_path`` for admissions, ``data.completion_reason`` for a refusal recorded
without a respawn block), read across every person's meetings of that room (platform +
platform_specific_id, indexed) — only the verdict leaves this module, never another person's meeting.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

WINDOW_DAYS = 30
#: Rows read at most per decision (newest first): a room's last month of bots, bounded.
HISTORY_LIMIT = 50
NEEDS_SIGN_IN, ANONYMOUS_OK, UNSEEN = "needs_sign_in", "anonymous_ok", "unseen"
REFUSED = "platform_join_refused"
#: Row statuses an admitted bot's meeting goes through (a start_time is set on admission).
_ADMITTED = frozenset({"active", "stopping", "completed"})


def _t(v: Any) -> Optional[datetime]:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def since(now: datetime) -> datetime:
    return now - timedelta(days=WINDOW_DAYS)


def outcomes(row: Mapping[str, Any]) -> list[tuple[datetime, str]]:
    """The decisive anonymous outcomes one meeting row records, each with when it happened."""
    data = row.get("data") if isinstance(row.get("data"), Mapping) else {}
    out: list[tuple[datetime, str]] = []
    block = data.get("meet_respawn") if isinstance(data.get("meet_respawn"), Mapping) else {}
    attempts = [a for a in (block.get("attempts") or []) if isinstance(a, Mapping)]
    for a in attempts:
        at = _t(a.get("at"))
        if at is not None and not a.get("signed_in"):
            out.append((at, NEEDS_SIGN_IN))
    signed_in = bool(str(data.get("auth_userdata_path") or ""))
    start = _t(row.get("start_time"))
    if start is not None and row.get("status") in _ADMITTED and not signed_in:
        out.append((start, ANONYMOUS_OK))
    if not attempts and data.get("completion_reason") == REFUSED and not signed_in:
        at = _t(row.get("end_time")) or _t(row.get("updated_at"))
        if at is not None:
            out.append((at, NEEDS_SIGN_IN))
    return out


def verdict(rows: Iterable[Mapping[str, Any]], now: Optional[datetime] = None) -> str:
    """``needs_sign_in`` / ``anonymous_ok`` / ``unseen`` for a room, from its meetings' rows."""
    now = now or datetime.now(timezone.utc)
    floor = since(now)
    seen = [o for r in rows for o in outcomes(r) if floor <= o[0] <= now + timedelta(minutes=5)]
    if not seen:
        return UNSEEN
    return max(seen, key=lambda o: o[0])[1]


async def room_verdict(repo: Any, platform: str, native_meeting_id: str,
                       now: Optional[datetime] = None) -> Optional[str]:
    """The verdict for a Google Meet room, or None when this isn't a Meet room or the repo can't say
    (a lookup that fails never fails a spawn: the bot then joins as it did before this rule)."""
    if platform != "google_meet" or not native_meeting_id:
        return None
    reader = getattr(repo, "room_history", None)
    if reader is None:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        rows = await reader(platform, native_meeting_id, since(now))
    except Exception:  # noqa: BLE001
        return None
    return verdict(rows, now)
