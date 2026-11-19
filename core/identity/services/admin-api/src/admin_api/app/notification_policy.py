"""THE PERSON'S NOTIFICATION POLICY — identity owns it, flows' delivery gate reads it (PRD 0.13.4 D15, S6).

D15: "recipient working hours and preferences live in identity person settings". It is stored as the
person setting ``notifications`` (``users.data.person_settings.notifications``), so the internal read
flows already makes before it mails anybody (``GET /internal/users/{id}/settings``) carries it, and
the person changes it from Settings → Notifications through ``GET``/``PUT /user/notifications``.

The shape (a PUT may send any subset of the top-level keys; nested objects merge):

    channels   {"email": bool, "whatsapp": false}     WhatsApp is shown as coming — 0.13.5 (decision 8)
    idle_min   int                                    minutes unseen before the one email
    digest     "hourly" | "daily" | "off"
    digest_at  "HH:MM"                                the daily digest's local time
    quiet      {"on": bool, "from": "HH:MM", "to": "HH:MM"}
    budget     int                                    deliveries outside the Inbox per local day (D32)
    rooms      {"room:<id>" | "ws:<slug>": "all" | "mentions" | "none"}
    sounds     {"on", "chime", "tick", "ring": bool, "volume": 0..1}

THE VOCABULARY IS CLOSED and validated ALL-OR-NOTHING, like every other person setting: an unknown key
or a bad value refuses the whole change with the reason. The bounds here are the vocabulary's; the
deployment's global floors and ceilings are flows' (``VEXA_FLOWS_NUDGE_*``), which clamps on read —
the person's rule sets the cap, the platform keeps it inside its floor and ceiling.
"""
from __future__ import annotations

import copy
import re
from typing import Any

KEY = "notifications"

DEFAULT: dict = {
    "channels": {"email": True, "whatsapp": False},
    "idle_min": 10,
    "digest": "hourly",
    "digest_at": "18:00",
    "quiet": {"on": True, "from": "22:00", "to": "08:00"},
    "budget": 6,
    "rooms": {},
    "sounds": {"on": True, "chime": True, "tick": True, "ring": True, "volume": 0.6},
}

#: The vocabulary's bounds (what a value may be at all).
IDLE_RANGE = (1, 240)
BUDGET_RANGE = (1, 50)
ROOMS_MAX = 500
#: The published defaults of flows' global limits, so the page can offer only what will hold. The
#: deployment's own values live in flows and win there.
GLOBAL_LIMITS = {"idle_floor_min": 5, "budget_ceiling": 12}

DIGESTS = ("hourly", "daily", "off")
LEVELS = ("all", "mentions", "none")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_ROOM_KEY = re.compile(r"^(room|ws):[A-Za-z0-9._:@+=-]{1,190}$")


class Refused(ValueError):
    def __init__(self, detail: dict) -> None:
        super().__init__(str(detail))
        self.detail = detail


def _refuse(why: str, **more) -> None:
    raise Refused({"refused": why, **more})


def _bool(v, name: str) -> bool:
    if isinstance(v, bool):
        return v
    _refuse(f"{name} is true or false", you_sent=v)


def _int(v, name: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        _refuse(f"{name} is a whole number from {lo} to {hi}", you_sent=v)
    return v


def _hhmm(v, name: str) -> str:
    if not isinstance(v, str) or not _HHMM.match(v):
        _refuse(f"{name} is a time like 22:00", you_sent=v)
    return v


def _obj(v, name: str) -> dict:
    if not isinstance(v, dict):
        _refuse(f"{name} is an object", you_sent=v)
    return v


def read(data: dict | None) -> dict:
    """The stored policy over the defaults — always the whole shape."""
    raw = ((data or {}).get("person_settings") or {}).get(KEY)
    out = copy.deepcopy(DEFAULT)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict) and k != "rooms":
                out[k].update({kk: vv for kk, vv in v.items() if kk in out[k]})
            elif k in out:
                out[k] = v
    out["channels"]["whatsapp"] = False
    return out


def validate(patch: Any, current: dict | None = None) -> dict:  # noqa: C901 — one closed vocabulary
    """The merged, validated policy after ``patch``. Raises ``Refused`` naming the first bad value."""
    if not isinstance(patch, dict) or not patch:
        _refuse("send at least one of: " + ", ".join(DEFAULT))
    unknown = sorted(set(patch) - set(DEFAULT))
    if unknown:
        _refuse(f"there is no notification setting called {unknown[0]!r}", the_settings_that_exist=list(DEFAULT))
    out = copy.deepcopy(current if isinstance(current, dict) else DEFAULT)
    for key, value in patch.items():
        if key == "channels":
            v = _obj(value, "channels")
            bad = sorted(set(v) - {"email", "whatsapp"})
            if bad:
                _refuse(f"there is no channel called {bad[0]!r}", channels=["email", "whatsapp"])
            if "email" in v:
                out["channels"]["email"] = _bool(v["email"], "channels.email")
            if v.get("whatsapp") not in (None, False):
                _refuse("WhatsApp nudges come in a later release; email is the channel today",
                        decision="0.13.4 decision 8")
        elif key == "idle_min":
            out["idle_min"] = _int(value, "idle_min", *IDLE_RANGE)
        elif key == "digest":
            if value not in DIGESTS:
                _refuse("digest is hourly, daily or off", you_sent=value)
            out["digest"] = value
        elif key == "digest_at":
            out["digest_at"] = _hhmm(value, "digest_at")
        elif key == "quiet":
            v = _obj(value, "quiet")
            bad = sorted(set(v) - {"on", "from", "to"})
            if bad:
                _refuse(f"quiet has on, from and to; not {bad[0]!r}")
            if "on" in v:
                out["quiet"]["on"] = _bool(v["on"], "quiet.on")
            for side in ("from", "to"):
                if side in v:
                    out["quiet"][side] = _hhmm(v[side], f"quiet.{side}")
        elif key == "budget":
            out["budget"] = _int(value, "budget", *BUDGET_RANGE)
        elif key == "rooms":
            v = _obj(value, "rooms")
            merged = dict(out.get("rooms") or {})
            for rk, level in v.items():
                if not isinstance(rk, str) or not _ROOM_KEY.match(rk):
                    _refuse("a room is named room:<id> or ws:<slug>", you_sent=rk)
                if level is None:
                    merged.pop(rk, None)          # back to the default
                    continue
                if level not in LEVELS:
                    _refuse("a room's bell is all, mentions or none", you_sent=level)
                merged[rk] = level
            if len(merged) > ROOMS_MAX:
                _refuse(f"at most {ROOMS_MAX} rooms and workspaces carry their own bell")
            out["rooms"] = merged
        elif key == "sounds":
            v = _obj(value, "sounds")
            bad = sorted(set(v) - {"on", "chime", "tick", "ring", "volume"})
            if bad:
                _refuse(f"sounds has on, chime, tick, ring and volume; not {bad[0]!r}")
            for s in ("on", "chime", "tick", "ring"):
                if s in v:
                    out["sounds"][s] = _bool(v[s], f"sounds.{s}")
            if "volume" in v:
                vol = v["volume"]
                if isinstance(vol, bool) or not isinstance(vol, (int, float)) or not 0 <= vol <= 1:
                    _refuse("sounds.volume is a number from 0 to 1", you_sent=vol)
                out["sounds"]["volume"] = float(vol)
    out["channels"]["whatsapp"] = False
    return out


def apply(data: dict | None, patch: Any) -> dict:
    """The new ``users.data`` with the change applied. Validates everything before writing anything."""
    merged = validate(patch, read(data))
    out = dict(data or {})
    stored = dict(out.get("person_settings") or {})
    stored[KEY] = merged
    out["person_settings"] = stored
    return out


def answer(data: dict | None) -> dict:
    """What the Settings page reads: the policy, the clock it is stated in, and the global limits."""
    tz = ((data or {}).get("person_settings") or {}).get("timezone") or ""
    return {"notifications": read(data), "timezone": tz, "limits": dict(GLOBAL_LIMITS)}
