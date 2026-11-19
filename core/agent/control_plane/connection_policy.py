"""connection_policy.py — what an agent may do with a person's connected mail and calendar, read from
the administrator's `_global/POLICIES.md` and narrowed, never widened, by the person.

Founder, 2026-10-12: *"this should be policy configurable."* Every default the calendar-write and
richer-draft work introduced is a key in the same front matter the meeting rules live in
(`behavior/global/POLICIES.md`, read by `flows_steps.policies` for the meeting keys). This module is
the reader of the connection keys, and the only one: agent-api resolves them per request, forwards
the effective answer to the credential broker with the action, and the broker enforces it again.

THE KEYS, typed, each with its default (the seeded file carries exactly these values; `gate:fact-parity`
holds every copy — `DEFAULTS` below, `flows_steps.policies.DEFAULTS`, the seed, the policies wizard's
table and the Connections docs table — to one answer per key, `connection-policy-default-*` in
`scripts/parity.json`):

    connection_actions             all       actions allowed while the person is present
    connection_actions_unattended  none      reads allowed in a background routine (never a write)
    calendar_notify_attendees      off       whether an agent may ask Google to email attendees
    calendar_attendee_domains      any       which domains an agent may add as attendees
    calendar_ids                   primary   which calendars an agent may change
    draft_recipient_domains        any       which domains a draft may address (to, cc, bcc)
    draft_attachments              off       whether a draft may carry a workspace file
    draft_attachment_max_kb        5120      the size cap per draft, all attachments together

LOUD, NOT SOFT. The meeting keys fail soft (a mail is better sent at the default than not sent).
These are the opposite: they decide whether software may write to a person's calendar or mailbox,
so a value this module cannot read, or a key in this family it does not know (a typo is the common
case), is `PolicyUnreadable` — every connection action is refused, naming the key, until the file is
fixed. An absent file is not unreadable: it is "no answers on file", which is the defaults.

THE PERSON MAY ONLY NARROW. `narrow` intersects the admin's answer with the person's own setting
(stored by the broker, per person); `check_narrowing` refuses a setting that would widen beyond the
admin's answer, naming the key, so "I allow everything" is never written down as if it meant more
than the admin allowed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from control_plane import front_page

POLICIES_FILE = "POLICIES.md"

#: Every connection action, in the order the docs walk them. Reads first, then writes.
READS = ("gmail.search", "gmail.read", "gmail.thread", "calendar.events")
WRITES = ("gmail.draft", "gmail.draft.update",
          "calendar.event.create", "calendar.event.update", "calendar.event.cancel")
ACTIONS = READS + WRITES

ANY = "any"

#: The routes whose regime and action are decided by the connection policy (`connection_policy`),
#: not by the person gate (re-exported as `routers.connections.POLICY_ROUTES`): they are declared `person: false` in routes.v1.json, and each one calls
#: `_authorize` before it reaches the broker. `tests/test_connection_policy.py` holds the two lists
#: to each other and refuses a worker on every one at the default policy.
ROUTES = {
    '/api/connections/gmail/search': 'gmail.search', '/api/connections/gmail/inbox': 'gmail.search',
    '/api/connections/gmail/read': 'gmail.read', '/api/connections/gmail/thread': 'gmail.thread',
    '/api/connections/calendar/events': 'calendar.events', '/api/connections/gmail/draft': 'gmail.draft',
    '/api/connections/gmail/draft/update': 'gmail.draft.update',
    '/api/connections/calendar/event/create': 'calendar.event.create',
    '/api/connections/calendar/event/update': 'calendar.event.update',
    '/api/connections/calendar/event/cancel': 'calendar.event.cancel',
}

#: The connection keys and their defaults, as the seeded file spells them.
DEFAULTS: dict = {
    "connection_actions": "all",
    "connection_actions_unattended": "none",
    "calendar_notify_attendees": False,
    "calendar_attendee_domains": "any",
    "calendar_ids": "primary",
    "draft_recipient_domains": "any",
    "draft_attachments": False,
    "draft_attachment_max_kb": 5120,
}

#: A key in one of these families that is not in `DEFAULTS` is refused loudly: it is almost always a
#: misspelling of one that is, and a misspelt control silently does nothing.
FAMILIES = ("connection_", "calendar_", "draft_")

#: The largest attachment cap an administrator may set: Gmail's own message limit is 25 MB.
MAX_ATTACHMENT_KB = 20480

_TRUE = frozenset({"true", "yes", "on", "1"})
_FALSE = frozenset({"false", "no", "off", "0"})


class PolicyUnreadable(Exception):
    """`_global/POLICIES.md` answers a connection key with something this module cannot read."""

    def __init__(self, key: str, why: str):
        super().__init__(f"{key}: {why}")
        self.key = key
        self.why = why


class Widening(Exception):
    """A person's setting that would allow more than the administrator's policy does."""

    def __init__(self, key: str, why: str):
        super().__init__(f"{key}: {why}")
        self.key = key
        self.why = why


# ── parsing, typed ──────────────────────────────────────────────────────────────────────────────

def _actions(key: str, value: str) -> frozenset:
    v = value.strip().lower()
    if v == "all":
        return frozenset(ACTIONS)
    if v == "none":
        return frozenset()
    items = [a.strip() for a in v.split(",") if a.strip()]
    unknown = [a for a in items if a not in ACTIONS]
    if unknown or not items:
        raise PolicyUnreadable(key, f"{', '.join(unknown) or value!r} is not an action; use all, none, "
                                    f"or a comma-separated list of {', '.join(ACTIONS)}")
    return frozenset(items)


def _unattended(key: str, value: str) -> frozenset:
    """Reads only. A write to a person's mailbox or calendar always needs the person in the loop
    (ADR-0040): the routes are person-gated and the broker refuses an unwatched write whatever it is
    forwarded, so a value naming a write (`all` included) is unreadable, never quietly less."""
    v = value.strip().lower()
    if v == "all":
        raise PolicyUnreadable(key, "cannot be all: only reads may run unattended; list them "
                                    f"({', '.join(READS)}) or use none")
    actions = _actions(key, value)
    writes = [a for a in WRITES if a in actions]
    if writes:
        raise PolicyUnreadable(key, f"{', '.join(writes)} is a write, and a write always needs a person in "
                                    f"the loop; only {', '.join(READS)} may run unattended")
    return actions


def _bool(key: str, value: str) -> bool:
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise PolicyUnreadable(key, f"{value!r} is neither on nor off")


def _domains(key: str, value: str):
    """`any`, or a tuple of lower-case domains (a leading `@` is dropped)."""
    v = value.strip().lower()
    if v == ANY:
        return ANY
    items = tuple(d.strip().lstrip("@") for d in v.split(",") if d.strip())
    if not items or any(not d or "." not in d or " " in d or "@" in d for d in items):
        raise PolicyUnreadable(key, f"{value!r} is neither any nor a comma-separated list of domains")
    return items


def _calendars(key: str, value: str):
    v = value.strip()
    if v.lower() == ANY:
        return ANY
    items = tuple(c.strip() for c in v.split(",") if c.strip())
    if not items or any(len(c) > 256 or " " in c for c in items):
        raise PolicyUnreadable(key, f"{value!r} is neither any nor a comma-separated list of calendar ids")
    return items


def _kb(key: str, value: str) -> int:
    v = value.strip()
    if not v.isdigit() or int(v) > MAX_ATTACHMENT_KB:
        raise PolicyUnreadable(key, f"{value!r} is not a whole number of kilobytes up to {MAX_ATTACHMENT_KB}")
    return int(v)


_PARSE = {
    "connection_actions": _actions, "connection_actions_unattended": _unattended,
    "calendar_notify_attendees": _bool, "calendar_attendee_domains": _domains,
    "calendar_ids": _calendars, "draft_recipient_domains": _domains,
    "draft_attachments": _bool, "draft_attachment_max_kb": _kb,
}


def resolve(raw: Optional[str]) -> dict:
    """The administrator's typed answers. Absent file or empty value = the default; anything this
    module cannot read raises `PolicyUnreadable` naming the key."""
    attrs = front_page.front_matter(raw or "")
    for key in attrs:
        if key.startswith(FAMILIES) and key not in DEFAULTS:
            raise PolicyUnreadable(key, "is not a connection policy key; check its spelling against "
                                        + ", ".join(DEFAULTS))
    out = {}
    for key, default in DEFAULTS.items():
        value = str(attrs.get(key, "")).strip()
        out[key] = _PARSE[key](key, value if value else str(default if not isinstance(default, bool)
                                                            else ("on" if default else "off")))
    return out


def read(global_dir: Optional[Path]) -> dict:
    """The administrator's answers as `_global` holds them now. Read per request, never cached: an
    admin who turns a key off expects the next action to see it."""
    from workspaces.shared import workspace_paths as wpaths

    raw = None
    if global_dir is not None and Path(global_dir).is_dir():
        raw = wpaths.read_text_inside(global_dir, POLICIES_FILE, max_bytes=1_000_000)
        if raw is None and wpaths.stat_inside(global_dir, POLICIES_FILE) is not None:
            # There, and not readable as a file inside `_global` (a link out, too large, not a
            # regular file): unreadable, never "no answers".
            raise PolicyUnreadable(POLICIES_FILE, "is present but cannot be read as a file inside _global")
    return resolve(raw)


# ── the person's setting: narrowing only ────────────────────────────────────────────────────────

#: The person's setting, the shape the broker stores (`ActionPreferences` in credential-broker.v1):
#: each key optional; absent means "as the administrator allows".
PERSON_KEYS = tuple(DEFAULTS)


def _person_value(key: str, value):
    """A person's stored value in the typed form `resolve` produces."""
    if key in ("connection_actions", "connection_actions_unattended"):
        unknown = [a for a in value if a not in ACTIONS]
        if unknown:
            raise Widening(key, f"{', '.join(unknown)} is not an action")
        return frozenset(value)
    if key in ("calendar_attendee_domains", "draft_recipient_domains"):
        return ANY if value == ANY else tuple(str(d).strip().lower().lstrip("@") for d in value)
    if key == "calendar_ids":
        return ANY if value == ANY else tuple(str(c).strip() for c in value)
    return value


def check_narrowing(admin: dict, person: dict) -> None:
    """Raise `Widening`, naming the key, when the person's setting allows anything the admin's does
    not. The person may always say less; never more."""
    for key, value in (person or {}).items():
        if value is None:
            continue
        if key not in DEFAULTS:
            raise Widening(key, "is not a connection policy key")
        mine, theirs = _person_value(key, value), admin[key]
        if isinstance(theirs, bool):
            if mine and not theirs:
                raise Widening(key, "is off in this deployment's policy; a person cannot turn it on")
        elif isinstance(theirs, int):
            if mine > theirs:
                raise Widening(key, f"the policy allows at most {theirs} KB")
        elif isinstance(theirs, frozenset):
            extra = sorted(mine - theirs)
            if extra:
                raise Widening(key, f"{', '.join(extra)} is not allowed by this deployment's policy")
        else:   # domains / calendars: ANY or a tuple
            if theirs == ANY:
                continue
            if mine == ANY:
                raise Widening(key, f"the policy allows only {', '.join(theirs)}")
            extra = sorted(set(mine) - set(theirs))
            if extra:
                raise Widening(key, f"{', '.join(extra)} is not allowed by this deployment's policy")


def narrow(admin: dict, person: Optional[dict]) -> dict:
    """The effective answer: the admin's, intersected with the person's. A stored person setting
    that has since become wider than the admin's (the admin narrowed later) is clipped, not refused:
    the admin's change takes effect at once."""
    out = dict(admin)
    for key, value in (person or {}).items():
        if value is None or key not in DEFAULTS:
            continue
        mine, theirs = _person_value(key, value), admin[key]
        if isinstance(theirs, bool):
            out[key] = theirs and bool(mine)
        elif isinstance(theirs, int):
            out[key] = min(theirs, int(mine))
        elif isinstance(theirs, frozenset):
            out[key] = theirs & mine
        elif theirs == ANY:
            out[key] = mine
        elif mine != ANY:
            out[key] = tuple(d for d in theirs if d in set(mine))
    return out


# ── one action, decided ─────────────────────────────────────────────────────────────────────────

def allowed(effective: dict, action: str, *, unattended: bool) -> Optional[str]:
    """None when `action` may run; otherwise the policy key that refuses it."""
    if action not in effective["connection_actions"]:
        return "connection_actions"
    if unattended and action not in effective["connection_actions_unattended"]:
        return "connection_actions_unattended"
    return None


def forwarded(effective: dict, action: str, *, unattended: bool) -> dict:
    """The answer the broker receives with the action (`ActionPolicy` in credential-broker.v1), and
    enforces again: null means any."""
    def listed(v):
        return None if v == ANY else list(v)
    return {
        "unattended": bool(unattended and action in effective["connection_actions_unattended"]),
        "notify_attendees": bool(effective["calendar_notify_attendees"]),
        "attendee_domains": listed(effective["calendar_attendee_domains"]),
        "calendars": listed(effective["calendar_ids"]),
        "recipient_domains": listed(effective["draft_recipient_domains"]),
        "attachments": bool(effective["draft_attachments"]),
        "attachment_max_bytes": int(effective["draft_attachment_max_kb"]) * 1024,
    }


def public(effective: dict) -> dict:
    """The effective answers as JSON a person or an agent reads."""
    out = {}
    for key, value in effective.items():
        if isinstance(value, frozenset):
            out[key] = [a for a in ACTIONS if a in value]
        elif isinstance(value, tuple):
            out[key] = list(value)
        else:
            out[key] = value
    return out


# ── a change to the file: who may make it, and the notice it leaves ──────────────────────────────

def changed_keys(before: Optional[str], after: Optional[str]) -> list:
    """The connection keys (`FAMILIES`) whose value differs between two versions of `POLICIES.md`,
    sorted. ``None`` is no file: every key it set returns to its default, which is a change too.
    Compared as written, so a front matter this module cannot read still counts as changed."""
    def keys(raw):
        attrs = front_page.front_matter(raw or "")
        return {k: str(v).strip() for k, v in attrs.items() if str(k).startswith(FAMILIES)}
    old, new = keys(before), keys(after)
    return sorted(k for k in set(old) | set(new) if old.get(k, "") != new.get(k, ""))


def change_notice(keys: list, subject: str, *, delegated: bool) -> str:
    """The line a commit to `_global` carries when it changes a connection key: which keys, and who.
    `_global`'s history is the audit trail an administrator already reads."""
    who = "an agent, person in the loop" if delegated else "the person"
    return f"connection policy changed: {', '.join(keys)} — by {subject} ({who})"
