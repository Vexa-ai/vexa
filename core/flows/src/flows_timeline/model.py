"""WHAT HAPPENED TO ONE PERSON, IN ORDER — the pure half of the timeline (PRD decision 31).

Founder, 2026-09-02 15:5xZ: *"does the agent have temporal awareness of the last events and future
events? scheduled meetings, the things that actually get logged in the flows data"*. It did not.
The engine already writes every one of those moments down — a reaction is a fact that arrived, a
receipt is an effect that landed — but nothing ever read them back along the ONE axis a person
thinks in, which is time, and nothing ever scoped them to the person they happened to.

Everything here is stdlib-only and takes ROWS, not a database: the merge, the scoping and the
ordering are the part that can be wrong in a way tests can catch, so they are kept away from the
I/O that cannot be. `service.py` does the reading.

Three sources, one list:

  * a REACTION is the fact itself — an invite arrived, a meeting finished, a reply came back.
  * an EFFECT RECEIPT is what we did about it — the prepare mail, the minutes mail, the desk drop.
    Only the steps a PERSON would recognise become events; `ensure_user` and `require_workspace`
    are machinery, and a timeline that lists machinery is a log, not an awareness.
  * a MEETING ROW is the calendar half — what is scheduled and has not happened yet, which no fact
    can carry because the fact for it has not been admitted.

A failure is never machinery: any receipt in state `failed` becomes a `reaction.failed` event
whatever its step, because the one thing an agent must not do is talk about a report it never
delivered.
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from typing import Any, Optional

# ── the vocabulary ───────────────────────────────────────────────────────────────────────────────
# Deliberately SMALL and stated once. Each key is what the engine calls the thing; each value is
# what a person calls it. An event type with no entry keeps its own name rather than being dropped:
# a flow submitted over the API reacts to event types this file has never heard of, and silence
# about them would make the timeline lie by omission.
EVENT_KINDS = {
    "invite.received": "invite.received",
    "meeting.upcoming": "meeting.scheduled",
    "meeting.completed": "meeting.held",
    "mail.reply": "reply.handled",
    "onboarding.person.needed": "onboarding.started",
    "onboarding.group.needed": "onboarding.started",
}

# A step ABSENT from this map is machinery and produces no event. That is a whitelist on purpose:
# the failure mode of a blacklist here is a timeline that fills with `ensure_user` the first time
# somebody adds a step, and nobody notices because it still looks like a timeline.
STEP_KINDS = {
    "rsvp_accept": "invite.accepted",
    "ack_by_email": "mail.sent",
    "prepare_meeting": "mail.sent",
    "email_minutes": "report.delivered",
    "email_attendees": "report.delivered",
    "drop_to_attendees": "report.delivered",
    "email_reply": "mail.sent",
    "drive_person": "mail.sent",
    "drive_group": "mail.sent",
    "process_meeting": "report.written",
    "dispatch_bot": "meeting.joined",
}

# The kinds that describe a MEETING rather than a message. One meeting produces at most one of each
# of these, whichever source saw it first — see `merge`.
_MEETING_KINDS = ("meeting.scheduled", "meeting.held", "meeting.joined")

_RECEIPT_STATUS = {"confirmed": "done", "reserved": "in_flight", "failed": "failed"}


@dataclass
class Event:
    """One moment on one person's timeline."""
    at: float                                   # epoch seconds — the ONE sort key
    kind: str
    title: str
    status: str
    meeting_id: Optional[str] = None
    produced: dict = field(default_factory=dict)
    flow: Optional[str] = None
    source: str = ""                            # reaction | receipt | meeting — provenance, not UI
    detail: Optional[str] = None                # a reason, when there is one

    def as_dict(self) -> dict:
        out = {
            "at": iso(self.at),
            "at_epoch": round(self.at, 3),
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "produced": self.produced,
            "source": self.source,
        }
        if self.meeting_id:
            out["meeting_id"] = str(self.meeting_id)
        if self.flow:
            out["flow"] = self.flow
        if self.detail:
            out["detail"] = self.detail
        return out


# ── small conversions ────────────────────────────────────────────────────────────────────────────

def iso(epoch: float) -> str:
    """UTC, ISO-8601, `Z`. The wire format: one zone on the wire, the person's zone at the edge."""
    return (datetime.datetime.fromtimestamp(float(epoch), datetime.timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))


def to_epoch(value: Any) -> Optional[float]:
    """Epoch seconds from an epoch, an ISO-8601 string (with or without `Z`), or None.

    Naive strings are read as UTC — every timestamp this system writes is UTC, and guessing local
    would silently move a meeting by hours on a machine whose clock is not the deployment's.
    """
    if value in (None, "", []):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        dt = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()


def loads(raw: Any) -> dict:
    """A JSON column, defensively. A row we cannot parse must never take the timeline down."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        out = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return out if isinstance(out, dict) else {}


# ── scoping: is this the person's? ───────────────────────────────────────────────────────────────

def _emails(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip().lower()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip().lower() for v in value if str(v).strip()]
    return []


def concerns(refs: dict, uid: str = "", email: str = "") -> bool:
    """Is this fact ABOUT this person — as its subject, its organizer, or one of its attendees?

    Both identifiers are needed and neither is sufficient. The facts the mailbox admits
    (`invite.received`, `meeting.upcoming`) carry an ORGANIZER and a participant list and no uid at
    all, because at that moment the person may not be a platform user yet; the facts the engine
    emits (`meeting.completed`, `mail.reply`) carry a uid and, in the invite lineage, the emails
    too. Scoping on one of them alone silently drops half of the person's own day — which is
    exactly the shape of the bug this function exists to not have.
    """
    uid = str(uid or "").strip()
    email = str(email or "").strip().lower()
    if not uid and not email:
        return False
    if uid:
        for key in ("uid", "user_id", "subject", "owner"):
            if str(refs.get(key) or "").strip() == uid:
                return True
    if email:
        for key in ("organizer", "inviter", "from", "to", "recipient", "email"):
            if email in _emails(refs.get(key)):
                return True
        for key in ("participants", "attendees", "recipients"):
            if email in _emails(refs.get(key)):
                return True
    return False


# ── the three mappers ────────────────────────────────────────────────────────────────────────────

def _title(refs: dict, fallback: str) -> str:
    for key in ("title", "subject", "summary", "name"):
        v = str(refs.get(key) or "").strip()
        if v:
            return v
    return fallback


def _meeting_id(refs: dict, result: dict | None = None) -> Optional[str]:
    for src in (result or {}, refs):
        for key in ("meeting_id", "meeting_ref", "meeting"):
            v = src.get(key)
            if v not in (None, "", []):
                return str(v)
    return None


def _produced(result: dict, provider_ref: str = "") -> dict:
    """What the effect left behind, in the three words the founder used: a link, a mail, a note."""
    out: dict = {}
    for key in ("link", "url"):
        v = str(result.get(key) or "").strip()
        if v:
            out["link"] = v
            break
    for key in ("subject", "mail_subject"):
        v = str(result.get(key) or "").strip()
        if v:
            out["mail_subject"] = v
            break
    for key in ("entity", "note_path", "note", "path"):
        v = str(result.get(key) or "").strip()
        if v:
            out["note_path"] = v
            break
    mid = str(result.get("message_id") or "").strip() or str(provider_ref or "").strip()
    if mid.startswith("<"):
        out["message_id"] = mid
    return out


def events_from_reaction(row: dict) -> list[Event]:
    """The FACT itself, plus its failure if it has one.

    Two events out of one row is deliberate: a reaction that failed happened TWICE as far as the
    person is concerned — the invite did arrive at 11:23, and the thing we were going to do about
    it died at 11:41 — and collapsing them into a single "failed invite" loses the first half.
    """
    refs = loads(row.get("subject_refs"))
    created = to_epoch(row.get("created_at"))
    if created is None:
        return []
    kind = EVENT_KINDS.get(str(row.get("event_type") or ""), str(row.get("event_type") or "event"))
    status = str(row.get("status") or "")
    out = [Event(at=created, kind=kind, title=_title(refs, kind), status=status,
                 meeting_id=_meeting_id(refs), flow=str(row.get("flow") or "") or None,
                 source="reaction")]
    if status == "failed":
        at = to_epoch(row.get("updated_at")) or created
        out.append(Event(at=at, kind="reaction.failed",
                         title=_title(refs, kind), status="failed",
                         meeting_id=_meeting_id(refs), flow=str(row.get("flow") or "") or None,
                         source="reaction", detail=(str(row.get("reason") or "") or None)[:300]
                         if row.get("reason") else None))
    return out


def event_from_receipt(row: dict, refs: dict, flow: str = "") -> Optional[Event]:
    """What we DID, when the step is one a person would recognise — or a failure, always."""
    at = to_epoch(row.get("attempted_at"))
    if at is None:
        return None
    confirmed = to_epoch(row.get("confirmed_at"))
    step = str(row.get("step") or "")
    state = str(row.get("state") or "")
    result = loads(row.get("result"))
    kind = STEP_KINDS.get(step)
    if state == "failed":
        kind = "reaction.failed"
    if not kind:
        return None
    if result.get("skipped"):
        status = "skipped"
    elif result.get("coalesced"):
        status = "coalesced"
    else:
        status = _RECEIPT_STATUS.get(state, state or "unknown")
    return Event(at=confirmed if (confirmed and state == "confirmed") else at,
                 kind=kind, title=_title(refs, step), status=status,
                 meeting_id=_meeting_id(refs, result),
                 produced=_produced(result, str(row.get("provider_ref") or "")),
                 flow=flow or None, source="receipt",
                 detail=(str(result.get("skipped") or "")[:300] or None))


# A meeting row is HELD once it can no longer happen again — the platform's own terminal statuses.
_TERMINAL = {"completed", "failed"}


def event_from_meeting(row: dict) -> Optional[Event]:
    """The calendar half: one row, one event — scheduled until it is terminal, then held.

    Never both. A completed meeting's `scheduled_at` is not news; the person lived through it, and
    the invite that created it is already on the timeline from the facts.
    """
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    status = str(row.get("status") or "").strip().lower()
    held = status in _TERMINAL
    if held:
        at = (to_epoch(row.get("end_time")) or to_epoch(row.get("start_time"))
              or to_epoch(data.get("scheduled_at")) or to_epoch(row.get("created_at")))
        kind = "meeting.held"
    else:
        at = (to_epoch(row.get("scheduled_at")) or to_epoch(data.get("scheduled_at"))
              or to_epoch(row.get("start_time")) or to_epoch(row.get("created_at")))
        kind = "meeting.scheduled"
    if at is None:
        return None
    title = (str(row.get("title") or "").strip() or str(data.get("title") or "").strip()
             or str(row.get("native_meeting_id") or "").strip() or "meeting")
    mid = row.get("id")
    return Event(at=at, kind=kind, title=title, status=status or "scheduled",
                 meeting_id=str(mid) if mid not in (None, "") else None,
                 source="meeting")


# ── the merge ────────────────────────────────────────────────────────────────────────────────────

def _key(e: Event) -> tuple:
    """The identity a duplicate would share. Two sources see one meeting; the person sees one."""
    if e.kind in _MEETING_KINDS and e.meeting_id:
        return (e.kind, e.meeting_id)
    return (e.kind, e.source, round(e.at, 3), e.title, e.meeting_id or "")


def merge(events, *, since: Optional[float] = None, until: Optional[float] = None,
          limit: int = 20) -> list[Event]:
    """Window, de-duplicate, order oldest-first, and keep the LAST `limit`.

    Keeping the last rather than the first is the whole point of a limit on a timeline: a person
    asking for twenty events wants the twenty nearest to now, not the twenty oldest rows the engine
    still holds. The result stays ascending so "in order" means the same thing to every reader.

    The duplicate rule keeps the EARLIER sighting of a meeting, because the earlier one is the one
    that came from a fact — the moment the system learned it — and the meetings table is a mirror
    of state, not of when that state was reached.
    """
    picked: dict[tuple, Event] = {}
    for e in events:
        if e is None:
            continue
        if since is not None and e.at < since:
            continue
        if until is not None and e.at > until:
            continue
        k = _key(e)
        prior = picked.get(k)
        if prior is None or e.at < prior.at:
            picked[k] = e
    ordered = sorted(picked.values(), key=lambda e: (e.at, e.kind, e.title))
    if limit and limit > 0 and len(ordered) > limit:
        ordered = ordered[-limit:]
    return ordered


def split_around(events, now: float, *, back: int = 5, ahead: int = 5) -> tuple[list, list]:
    """`(the last `back`, the next `ahead`)` — the two halves the preamble states, in one pass."""
    past = [e for e in events if e.at <= now]
    future = [e for e in events if e.at > now]
    return past[-back:] if back else [], future[:ahead] if ahead else []
