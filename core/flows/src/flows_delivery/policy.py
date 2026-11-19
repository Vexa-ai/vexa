"""THE PERSON'S NOTIFICATION POLICY, clamped to the deployment's floors and ceilings (D15, D32, D38).

WHERE IT LIVES. Identity person settings, key ``notifications`` (D15: "recipient working hours and
preferences live in identity person settings"). identity validates the vocabulary on write
(``admin_api.app.notification_policy``); this module reads it back, fills defaults and CLAMPS it to
the deployment's global limits, because the limits are the deployment's and not the person's: the
person's rule sets the cap, the platform keeps a floor under it and a ceiling over it.

The shape (every key optional; an absent or malformed key reads as its default):

    channels   {"email": true, "whatsapp": false}   whatsapp is "coming" (decision 8, 0.13.5)
    idle_min   10                                   in-app first; email only after this many unseen minutes
    digest     "hourly" | "daily" | "off"           the batched digest of what is still unseen
    digest_at  "18:00"                              the daily digest's local time
    quiet      {"on": true, "from": "22:00", "to": "08:00"}   no email inside, in the person's own zone
    budget     6                                    deliveries outside the Inbox per local day (D32)
    rooms      {"room:<id>" | "ws:<slug>": "all" | "mentions" | "none"}   the bell, per room/workspace
    sounds     {"on": true, "chime": true, "tick": true, "ring": true, "volume": 0.6}   client-side

WHAT INTERRUPTS. A card addressed to you, a mention of you, a call ringing you, a join request you
can approve, a slip on a card you own or wait on. A plain room message never does — at any level it
is an unread count, never a toast, a sound or an email. ``none`` mutes the room's interruptions: the
item still sits in the Inbox, which costs no attention (D32).
"""
from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: The kinds of item the gate knows. Everything but ``message`` interrupts (founder mock 87, S6 brief).
INTERRUPTING = ("card", "mention", "call", "join_request", "slip")
KINDS = INTERRUPTING + ("message",)
LEVELS = ("all", "mentions", "none")
DIGESTS = ("hourly", "daily", "off")
#: A room or workspace with no bell of its own. Interrupting kinds pass at `all` and `mentions` alike.
DEFAULT_LEVEL = "mentions"

DEFAULT_POLICY: dict = {
    "channels": {"email": True, "whatsapp": False},
    "idle_min": 10,
    "digest": "hourly",
    "digest_at": "18:00",
    "quiet": {"on": True, "from": "22:00", "to": "08:00"},
    "budget": 6,
    "rooms": {},
    "sounds": {"on": True, "chime": True, "tick": True, "ring": True, "volume": 0.6},
}

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class Limits:
    """The deployment's global floors and ceilings. A person's value outside them is clamped, never
    refused here (identity refuses nonsense on write; this is the platform's bound on sense)."""
    idle_floor_min: int = 5
    idle_ceiling_min: int = 240
    budget_floor: int = 1
    budget_ceiling: int = 12
    #: no single item is emailed on its own more than once; everything after rides the digest
    emails_per_item: int = 1


def limits() -> Limits:
    """The deployment's limits, from ``flows_config`` (declared keys)."""
    import flows_config
    floor = max(1, flows_config.get_int("VEXA_FLOWS_NUDGE_IDLE_FLOOR_MIN"))
    ceiling = max(1, flows_config.get_int("VEXA_FLOWS_NUDGE_BUDGET_CEILING"))
    return Limits(idle_floor_min=floor, budget_ceiling=ceiling)


def _int(v, default: int) -> int:
    if isinstance(v, bool):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _hhmm(v, default: str) -> str:
    return v if isinstance(v, str) and _HHMM.match(v) else default


def normalize(raw, lim: Limits | None = None) -> dict:
    """The effective policy: defaults filled, malformed keys reset, numbers clamped to ``lim``."""
    lim = lim or Limits()
    src = raw if isinstance(raw, dict) else {}
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_POLICY.items()}
    ch = src.get("channels") if isinstance(src.get("channels"), dict) else {}
    out["channels"] = {"email": bool(ch.get("email", True)),
                       # 0.13.4 delivers by email only (decision 8): WhatsApp is shown, never sent
                       "whatsapp": False}
    out["idle_min"] = min(max(_int(src.get("idle_min"), DEFAULT_POLICY["idle_min"]), lim.idle_floor_min),
                          lim.idle_ceiling_min)
    out["digest"] = src.get("digest") if src.get("digest") in DIGESTS else DEFAULT_POLICY["digest"]
    out["digest_at"] = _hhmm(src.get("digest_at"), DEFAULT_POLICY["digest_at"])
    q = src.get("quiet") if isinstance(src.get("quiet"), dict) else {}
    out["quiet"] = {"on": bool(q.get("on", True)), "from": _hhmm(q.get("from"), "22:00"),
                    "to": _hhmm(q.get("to"), "08:00")}
    out["budget"] = min(max(_int(src.get("budget"), DEFAULT_POLICY["budget"]), lim.budget_floor),
                        lim.budget_ceiling)
    rooms = src.get("rooms") if isinstance(src.get("rooms"), dict) else {}
    out["rooms"] = {str(k)[:200]: v for k, v in list(rooms.items())[:500] if v in LEVELS}
    s = src.get("sounds") if isinstance(src.get("sounds"), dict) else {}
    vol = s.get("volume", 0.6)
    out["sounds"] = {"on": bool(s.get("on", True)), "chime": bool(s.get("chime", True)),
                     "tick": bool(s.get("tick", True)), "ring": bool(s.get("ring", True)),
                     "volume": min(max(float(vol) if isinstance(vol, (int, float)) and not isinstance(vol, bool)
                                       else 0.6, 0.0), 1.0)}
    return out


# ── levels: what a room's bell lets through ─────────────────────────────────────────────────────

def level_for(policy: dict, *, room: str = "", workspace: str = "") -> str:
    """The bell for an item: the room's own override, else its workspace's, else ``mentions`` (the
    Settings page's default, "Mentions and cards")."""
    rooms = (policy or {}).get("rooms") or {}
    for key in ((f"room:{room}" if room else ""), (f"ws:{workspace}" if workspace else "")):
        if key and rooms.get(key) in LEVELS:
            return rooms[key]
    return DEFAULT_LEVEL


def interrupts(kind: str, level: str) -> bool:
    """Whether an item of ``kind`` may leave the Inbox (toast, sound, email) at bell ``level``.
    A message never interrupts; ``none`` lets nothing through; ``all`` and ``mentions`` let every
    interrupting kind through (they differ only in whether plain messages count as unread)."""
    if kind not in INTERRUPTING:
        return False
    return level in ("all", "mentions")


# ── local time ──────────────────────────────────────────────────────────────────────────────────

def zone(tz: str):
    """The person's zone, or UTC when they never set one or set nonsense."""
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(tz) if tz else timezone.utc
    except Exception:  # noqa: BLE001 — an unknown zone is stated in UTC, never a crash
        return timezone.utc


def _local(now: float, tz: str) -> datetime:
    return datetime.fromtimestamp(now, zone(tz))


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def in_quiet(now: float, policy: dict, tz: str) -> bool:
    """Whether ``now`` falls inside the person's quiet hours, in their own clock. A window that
    crosses midnight (22:00–08:00) is the common case."""
    q = (policy or {}).get("quiet") or {}
    if not q.get("on"):
        return False
    a, b = _minutes(q.get("from", "22:00")), _minutes(q.get("to", "08:00"))
    if a == b:
        return False
    t = _local(now, tz)
    m = t.hour * 60 + t.minute
    return (a <= m < b) if a < b else (m >= a or m < b)


def quiet_end(now: float, policy: dict, tz: str) -> float:
    """The first instant at or after ``now`` that is outside quiet hours."""
    if not in_quiet(now, policy, tz):
        return now
    b = _minutes(((policy or {}).get("quiet") or {}).get("to", "08:00"))
    t = _local(now, tz)
    end = t.replace(hour=b // 60, minute=b % 60, second=0, microsecond=0)
    if end <= t:
        end = end + timedelta(days=1)
    return end.timestamp()


def local_day(now: float, tz: str) -> str:
    """The person's calendar day — the budget resets at their midnight, not at UTC's."""
    return _local(now, tz).strftime("%Y-%m-%d")


def next_day(now: float, tz: str) -> float:
    """The person's next local midnight."""
    t = _local(now, tz)
    nxt = (t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.timestamp()


def next_digest_at(now: float, policy: dict, tz: str) -> "float | None":
    """When the next digest goes, strictly after ``now``, moved out of quiet hours. ``None`` = off.
    Hourly is the top of the next local hour; daily is the next ``digest_at``."""
    mode = (policy or {}).get("digest", "hourly")
    if mode == "off":
        return None
    t = _local(now, tz)
    if mode == "hourly":
        at = (t + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        m = _minutes((policy or {}).get("digest_at", "18:00"))
        at = t.replace(hour=m // 60, minute=m % 60, second=0, microsecond=0)
        if at <= t:
            at = at + timedelta(days=1)
    return quiet_end(at.timestamp(), policy, tz)


def utc(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> float:
    """A UTC instant, for tests and fixtures."""
    return float(calendar.timegm((y, mo, d, h, mi, 0)))


def now() -> float:
    return time.time()
