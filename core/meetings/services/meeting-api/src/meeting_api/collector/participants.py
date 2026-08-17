"""The ROSTER layer of the meeting record — who was *in* a meeting, as identities.

Identity in this product is three things, and conflating them is the bug this module exists to
avoid:

* a **participant** is an email on an invitation — this module;
* a **speaker** is an attributed voice in the record (``transcriptions.speaker``, the capture
  lane's ``speaker_events``) — untouched here;
* a **user** is someone with sessions.

**No resolver lives here, and none should.** There is no function that maps a speaker label onto a
participant, no confidence score, and no "best guess" fallback: that resolution is an AGENTIC job,
done with the roster and the record both in context, not a system one (founder ruling — see
``admin-api/.../schema/MIGRATION-0005-meeting-participants.md``). The record carries both layers
side by side and says which is which; unattributed speech stays unattributed.

What this module owns is the pure part — normalization and composition — so the store adapters and
the in-memory fake share ONE definition of the participant shape and can never drift:

* :func:`normalize_participant` — one caller-supplied roster entry → the storable row. The
  mailroom's captured shape (``{email, name?, role?, partstat?}``) maps with no transformation:
  ``email`` and ``name`` land as columns, iCalendar ``ROLE`` normalizes to the product's
  ``organizer``/``required``/``optional`` vocabulary with the raw value kept, and everything else
  the caller sent — ``partstat`` included — rides into ``data`` rather than being dropped.
* :func:`compose` — stored rows + the meeting's pre-existing JSONB stores → the read-path answer.

**Absence is not an empty roster.** Meetings that never had a roster captured are real (three in
the current corpus), and "we never looked" must not read as "nobody was there". :func:`compose`
answers with an explicit ``participants_source``: ``"none"`` means no roster was ever captured;
any other value means one WAS, so ``participants: []`` under it is a genuinely empty roster.

**Nothing that writes today is rewired.** ``calendar_sync`` keeps writing ``data['attendees']`` and
the capture lane keeps writing ``data['participants']``; :func:`compose` PROJECTS both read-only
when a meeting carries no stored rows, so a deployment that never attaches a roster still answers
the same read path from the identities it already has.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

# Where a participant identity came from. A consumer weighs an invited email differently from a
# display name scraped off a participant panel, so the provenance is a first-class column, never
# flattened away.
SOURCE_INVITE = "invite"        # an invitation's ATTENDEE roster (the mailroom, a calendar feed)
SOURCE_PLATFORM = "platform"    # observed in the meeting UI by the capture lane
SOURCE_INFERRED = "inferred"    # derived, not observed — must never masquerade as either above
PARTICIPANT_SOURCES = (SOURCE_INVITE, SOURCE_PLATFORM, SOURCE_INFERRED)

# `participants_source` on a read, when no roster was EVER captured. Distinct from a captured roster
# that happens to be empty — see the module docstring.
SOURCE_NONE = "none"
SOURCE_MIXED = "mixed"

# The `meetings.data` key that records THAT a roster was attached, per source, even when the roster
# itself was empty (no rows to find). `{"invite": {"count": 0, "at": "..."}}`.
ROSTER_CAPTURE_KEY = "roster_capture"

# The pre-existing JSONB stores this module reads. Neither is written here.
ATTENDEES_KEY = "attendees"          # calendar_sync: [{email, name?, partstat?}]
OBSERVED_KEY = "participants"        # the capture lane's observed roster (names, or dicts)

ROLE_ORGANIZER = "organizer"
ROLE_REQUIRED = "required"
ROLE_OPTIONAL = "optional"
ROLE_NON_PARTICIPANT = "non_participant"
PARTICIPANT_ROLES = (ROLE_ORGANIZER, ROLE_REQUIRED, ROLE_OPTIONAL, ROLE_NON_PARTICIPANT)

# iCalendar ROLE → the product's vocabulary. A deterministic rename, not an inference: RFC 5545
# defines exactly these four values and the mapping is one-to-one. An unrecognized ROLE normalizes
# to None and survives verbatim in `data['role_raw']` — inventing a role would be a guess.
_ICAL_ROLES = {
    "CHAIR": ROLE_ORGANIZER,
    "REQ-PARTICIPANT": ROLE_REQUIRED,
    "OPT-PARTICIPANT": ROLE_OPTIONAL,
    "NON-PARTICIPANT": ROLE_NON_PARTICIPANT,
}

# Keys with a column of their own; everything else a caller sends is preserved in `data`.
_COLUMN_KEYS = frozenset({"email", "name", "role", "source", "joined_at", "left_at"})


def parse_iso8601(value: Any) -> Optional[datetime]:
    """ISO-8601 → an aware UTC datetime, or ``None`` when it does not parse.

    Accepts the trailing ``Z`` spelling and treats a naive timestamp as UTC (every timestamp this
    service stores and serves is UTC).

    NOTE for the merge with #1164, which introduces a private ``_parse_iso8601`` in ``app.py`` for
    its ``updated_after`` cursor: these are the same function and should collapse into one after
    both land. Kept separate here so this change does not depend on that one.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso_utc(value: Any) -> Optional[str]:
    """A stored datetime → the ``...Z`` string the API serves, or ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_email(value: Any) -> Optional[str]:
    """An email as a stable identity key: trimmed, ``mailto:``-stripped, lowercased.

    Returns ``None`` for anything that is not address-shaped, because a participant with no email
    is a legitimate row (a display name off a participant panel) and a malformed string must not be
    stored as if it were an identity.
    """
    if not isinstance(value, str):
        return None
    email = value.strip()
    if email.lower().startswith("mailto:"):
        email = email[7:].strip()
    email = email.strip("<>").strip().lower()
    return email if "@" in email else None


def normalize_role(value: Any) -> tuple[Optional[str], Optional[str]]:
    """``(normalized_role, raw_role_to_preserve)``.

    Accepts the product's own vocabulary verbatim and the four iCalendar ``ROLE`` values by the map
    above. Anything else normalizes to ``None`` and is returned as the raw value so the caller's
    input survives in ``data['role_raw']``.
    """
    if not isinstance(value, str) or not value.strip():
        return None, None
    raw = value.strip()
    lowered = raw.lower()
    if lowered in PARTICIPANT_ROLES:
        return lowered, None
    mapped = _ICAL_ROLES.get(raw.upper())
    if mapped:
        return mapped, raw.upper()
    return None, raw


def normalize_source(value: Any) -> Optional[str]:
    """One of :data:`PARTICIPANT_SOURCES`, or ``None`` — the route maps ``None`` → 400."""
    if not isinstance(value, str):
        return None
    source = value.strip().lower()
    return source if source in PARTICIPANT_SOURCES else None


class ParticipantError(ValueError):
    """A roster the service refuses to store, with the reason the route returns to the caller."""


def normalize_participant(raw: Any, *, source: str, index: int = 0) -> dict:
    """One caller-supplied roster entry → the storable row.

    Raises :class:`ParticipantError` when the entry identifies nobody (neither an email nor a
    name). Refusing is deliberate: silently dropping it would make the stored roster quietly
    smaller than the one the caller sent, which is the failure mode this whole change exists to
    end.
    """
    if not isinstance(raw, dict):
        raise ParticipantError(f"participants[{index}] must be an object")

    email = normalize_email(raw.get("email"))
    name = raw.get("name")
    name = name.strip() if isinstance(name, str) and name.strip() else None
    if email is None and name is None:
        raise ParticipantError(
            f"participants[{index}] identifies nobody — an entry needs an email or a name"
        )

    role, role_raw = normalize_role(raw.get("role"))
    joined_at = parse_iso8601(raw.get("joined_at"))
    left_at = parse_iso8601(raw.get("left_at"))
    for key, value in (("joined_at", raw.get("joined_at")), ("left_at", raw.get("left_at"))):
        if value is not None and parse_iso8601(value) is None:
            raise ParticipantError(
                f"participants[{index}].{key} must be an ISO-8601 timestamp (e.g. 2026-08-16T09:00:00Z)"
            )

    # Everything the caller sent that has no column — `partstat` above all — is kept. A source that
    # carries more than we modelled must not lose it on the way in.
    extra = {k: v for k, v in raw.items() if k not in _COLUMN_KEYS and v is not None}
    partstat = extra.pop("partstat", None)
    if isinstance(partstat, str) and partstat.strip():
        extra["partstat"] = partstat.strip().upper()
    if role_raw:
        extra["role_raw"] = role_raw

    return {
        "email": email,
        "name": name,
        "role": role,
        "source": source,
        # Stored naive-UTC to match `meetings.start_time` / `end_time` (timestamp WITHOUT time zone
        # holding UTC).
        "joined_at": joined_at.replace(tzinfo=None) if joined_at else None,
        "left_at": left_at.replace(tzinfo=None) if left_at else None,
        "data": extra,
    }


def normalize_roster(raw_participants: Any, *, source: str) -> list[dict]:
    """A whole roster → storable rows, deduped by email within the source (last entry wins).

    Dedup is by email only; two entries with no email and different names are two people.
    """
    if raw_participants is None:
        raw_participants = []
    if not isinstance(raw_participants, list):
        raise ParticipantError("'participants' must be a list")
    rows: list[dict] = []
    by_email: dict[str, int] = {}
    for i, raw in enumerate(raw_participants):
        row = normalize_participant(raw, source=source, index=i)
        email = row["email"]
        if email is not None and email in by_email:
            rows[by_email[email]] = row
            continue
        if email is not None:
            by_email[email] = len(rows)
        rows.append(row)
    return rows


def as_api_row(row: dict) -> dict:
    """A stored row → the shape the API serves. ``data``'s extras are flattened alongside the
    columns (so ``partstat`` reads where a consumer expects it) but never over them."""
    out = dict(row.get("data") or {})
    out.update({
        "email": row.get("email"),
        "name": row.get("name"),
        "role": row.get("role"),
        "source": row.get("source"),
        "joined_at": iso_utc(row.get("joined_at")),
        "left_at": iso_utc(row.get("left_at")),
    })
    return out


def project_attendees(data: Any) -> list[dict]:
    """``meetings.data['attendees']`` (calendar_sync's store) → ``invite`` participants, read-only.

    This is the "do not duplicate a store that exists" half: the calendar feed's roster keeps
    living where its writer put it, and surfaces here without being copied.
    """
    if not isinstance(data, dict):
        return []
    rows = []
    for i, raw in enumerate(data.get(ATTENDEES_KEY) or []):
        try:
            rows.append(normalize_participant(raw, source=SOURCE_INVITE, index=i))
        except ParticipantError:
            continue  # a malformed pre-existing entry is not a reason to fail a read
    return rows


def project_observed(data: Any) -> list[dict]:
    """``meetings.data['participants']`` (the capture lane's observed roster) → ``platform`` rows.

    Tolerates both spellings found in the tree: a list of display-name strings, and a list of
    dicts. A display name is not an email and is stored as exactly that — a name with no identity.
    """
    if not isinstance(data, dict):
        return []
    rows = []
    for i, raw in enumerate(data.get(OBSERVED_KEY) or []):
        entry = {"name": raw} if isinstance(raw, str) else raw
        try:
            rows.append(normalize_participant(entry, source=SOURCE_PLATFORM, index=i))
        except ParticipantError:
            continue
    return rows


def _captured_sources(data: Any) -> set[str]:
    """Sources for which a roster is known to have been captured, INCLUDING empty ones."""
    if not isinstance(data, dict):
        return set()
    captured = set()
    stamp = data.get(ROSTER_CAPTURE_KEY)
    if isinstance(stamp, dict):
        captured |= {s for s in stamp if s in PARTICIPANT_SOURCES}
    if data.get(ATTENDEES_KEY):
        captured.add(SOURCE_INVITE)
    if data.get(OBSERVED_KEY):
        captured.add(SOURCE_PLATFORM)
    return captured


def compose(data: Any, stored_rows: Optional[Iterable[dict]] = None) -> tuple[list[dict], str]:
    """``(participants, participants_source)`` for one meeting — the read-path answer.

    Stored rows are authoritative. The pre-existing JSONB stores are projected only for sources the
    stored rows do not already cover, so attaching an invite roster supersedes the calendar feed's
    copy of it rather than doubling it.

    ``participants_source`` is ``"none"`` ONLY when no roster was ever captured for this meeting.
    A captured-but-empty roster answers with its source and an empty list — the two are different
    facts and the API says which one it is.
    """
    rows = list(stored_rows or [])
    stored_sources = {r.get("source") for r in rows}
    if SOURCE_INVITE not in stored_sources:
        rows.extend(project_attendees(data))
    if SOURCE_PLATFORM not in stored_sources:
        rows.extend(project_observed(data))

    sources = {r.get("source") for r in rows if r.get("source")} | _captured_sources(data)
    if not sources:
        source = SOURCE_NONE
    elif len(sources) == 1:
        source = next(iter(sources))
    else:
        source = SOURCE_MIXED
    return [as_api_row(r) for r in rows], source


def capture_stamp(data: Any, *, source: str, count: int, at: str) -> dict:
    """``meetings.data`` with the roster-capture stamp for ``source`` recorded (non-mutating).

    The stamp is what makes an EMPTY captured roster distinguishable from a meeting that never had
    one: zero rows leave nothing in the relation to find.
    """
    out = dict(data) if isinstance(data, dict) else {}
    stamp = dict(out.get(ROSTER_CAPTURE_KEY) or {})
    stamp[source] = {"count": int(count), "at": at}
    out[ROSTER_CAPTURE_KEY] = stamp
    return out
