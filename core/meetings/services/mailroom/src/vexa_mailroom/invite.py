"""``parse_invite(raw) -> ParsedMail`` — the pure half of the mailroom: RFC-822 bytes → an invite.

One function, no I/O: an email message (exactly the bytes an IMAP fetch, an inbound-SMTP hook or
the Mailpit ``/raw`` endpoint yields) in, a ``ParsedMail`` out. Everything the mailroom decides
downstream — which workspace, create/update/cancel, or a notice — is decided from this struct, so
the corpus tests drive the SHIPPED parser with no transport at all.

What it reads
-------------
* **The envelope recipients** — ``To`` / ``Cc`` / ``Delivered-To`` / ``X-Original-To`` /
  ``X-Forwarded-To``. The invited workspace address is usually also an ICS ``ATTENDEE``, but not
  always (a forwarded invite, an Exchange distribution expansion), so both sets are collected and
  the resolver may use either.
* **The calendar part** — ``text/calendar`` (the Google and Outlook flavours both send one, with
  ``method=`` on the content-type) or an ``.ics`` file attachment (Outlook's ``invite.ics``,
  ``application/ics``, ``application/octet-stream`` named ``*.ics``). The part's ``METHOD``
  property wins over the content-type parameter when the two disagree.
* **The event** — ``UID`` (the series identity), ``SEQUENCE`` (the update counter), ``DTSTART``,
  ``SUMMARY``, ``RRULE`` (present ⇒ the invite binds a series), ``STATUS``, ``ATTENDEE``,
  ``ORGANIZER``, and the joinable link, looked for in ``X-GOOGLE-CONFERENCE`` → ``LOCATION`` →
  ``DESCRIPTION`` → ``X-MICROSOFT-SKYPETEAMSMEETINGURL`` (the same order + parser meeting-api's
  ``calendar_sync`` uses, so a link that imports from a feed imports from an invite).

A recurring series is ONE ``ParsedMail``: the master VEVENT (the one with no ``RECURRENCE-ID``)
carries the identity, and ``recurring`` records that the binding covers every occurrence. v0
schedules the NEXT occurrence only — the same rule ``calendar_sync`` follows, and for the same
reason (two active rows on one native id violate the control plane's unique index).

Nothing here raises: an unparseable message comes back as ``ParsedMail(rejection=...)``, because
the fail-safe rule is *no group effect + a notice*, never a crashed poll loop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from email import message_from_bytes, policy
from email.utils import getaddresses
from typing import Any, Optional

from .meeting_link import find_meeting_link, parse_meeting_url

# Rejection reasons — the closed vocabulary a notice carries. Kept small and stable: each one is a
# different sentence to the sender, and the corpus README's negative rows map onto these.
REASON_NO_CALENDAR_PART = "no_calendar_part"
REASON_UNPARSEABLE_ICS = "unparseable_ics"
REASON_NO_EVENT = "no_event"
REASON_NO_UID = "no_uid"
REASON_UNSUPPORTED_METHOD = "unsupported_method"
REASON_NO_MEETING_LINK = "no_meeting_link"
REASON_UNKNOWN_WORKSPACE = "unknown_workspace"  # raised by the resolver, not here
REASON_NO_START_TIME = "no_start_time"
REASON_FLOATING_START = "floating_start_time"

# Methods the mailroom acts on. REPLY/COUNTER/REFRESH are other people's RSVPs to an invite we
# were copied on — they change no binding, and answering them would be broadcasting.
METHOD_REQUEST = "REQUEST"
METHOD_CANCEL = "CANCEL"
_ACTIONABLE_METHODS = frozenset({METHOD_REQUEST, METHOD_CANCEL})

_RECIPIENT_HEADERS = ("to", "cc", "delivered-to", "x-original-to", "x-forwarded-to",
                      "x-envelope-to", "envelope-to", "resent-to")
_ICS_NAME = re.compile(r"\.ics$", re.IGNORECASE)


@dataclass(frozen=True)
class Rejection:
    """Why a message produced no group effect. The v0 notice payload (§ notices.py)."""
    reason: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"reason": self.reason, "detail": self.detail}


@dataclass(frozen=True)
class ParsedMail:
    """One message, parsed. Either ``rejection`` is set (no group effect) or the invite fields are.

    ``recipients`` and ``attendees`` are both lowercase email lists; the resolver looks for the
    workspace address in their union. ``sequence`` defaults to 0 (RFC 5545's default), which is
    what makes SEQUENCE-based idempotency work on senders that omit the property entirely.
    """
    message_id: str = ""
    subject: str = ""
    sender: Optional[str] = None
    recipients: tuple[str, ...] = ()
    method: Optional[str] = None
    uid: Optional[str] = None
    sequence: int = 0
    summary: Optional[str] = None
    dtstart: Optional[str] = None          # ISO-8601 UTC, the NEXT occurrence for a series
    recurring: bool = False
    rrule: Optional[str] = None
    status: Optional[str] = None
    attendees: tuple[str, ...] = ()
    participants: tuple[dict, ...] = ()    # the roster: {email, name?, role?, partstat?}
    organizer: Optional[str] = None
    series_start: Optional[str] = None     # the master DTSTART (ISO UTC) — the RRULE's anchor
    floating_start: bool = False           # DTSTART carried no zone: refused, never guessed
    description: Optional[str] = None      # the event DESCRIPTION, verbatim (organiser-authored)
    group_tag: Optional[str] = None        # `#group:<name>` parsed from the description, or None
    platform: Optional[str] = None
    native_meeting_id: Optional[str] = None
    meeting_url: Optional[str] = None
    rejection: Optional[Rejection] = None
    warnings: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return self.rejection is None

    @property
    def invited_addresses(self) -> tuple[str, ...]:
        """The addresses that may BIND a meeting: the ICS ``ATTENDEE`` list, and only that.

        **The envelope is deliberately not here.** Binding on the SMTP recipient would mean anyone
        who forwards or BCCs an invitation to the workspace address silently puts a bot into a
        third party's meeting — the invite has to name us. The envelope is still read (see
        ``addressed_to``), but only to decide whether a message that cannot bind deserves a notice.
        """
        return self.attendees

    @property
    def addressed_to(self) -> tuple[str, ...]:
        """Every address the message reached us by — envelope headers ∪ ICS attendees.

        Used for the notice decision only: mail addressed to the workspace mailbox that cannot
        bind gets an explanation; mail that never named the mailbox is not ours to answer.
        """
        seen, out = set(), []
        for a in (*self.recipients, *self.attendees):
            if a and a not in seen:
                seen.add(a)
                out.append(a)
        return tuple(out)


# ── helpers ───────────────────────────────────────────────────────────────────────────────────

def _group_tag(description: str) -> Optional[str]:
    """``#group:<name>`` from the organiser-authored description — the in-band series binding.

    First occurrence wins; the name is slug-shaped (lowercase alnum + dash). Anything else is not
    a tag: a malformed tag NEVER binds (the caller refuses loudly, it does not guess)."""
    import re as _re
    m = _re.search(r"#group:([A-Za-z0-9][A-Za-z0-9-]*)", description or "")
    return m.group(1).lower() if m else None


def _addresses(value: Any) -> list[str]:
    """Header value(s) → lowercase bare addresses (``Name <a@b>``, ``mailto:a@b``, folded lists)."""
    values = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for _name, addr in getaddresses([str(v) for v in values if v]):
        addr = addr.strip()
        if addr.lower().startswith("mailto:"):
            addr = addr[7:].strip()
        addr = addr.strip("<>").lower()
        if "@" in addr:
            out.append(addr)
    return out


def _as_utc(value: Any) -> Optional[datetime]:
    """An icalendar DTSTART (datetime or all-day date) → tz-aware UTC datetime."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time(0, 0), tzinfo=timezone.utc)
    return None


def _is_floating(comp) -> bool:
    """True when DTSTART is a local time with NO zone (RFC 5545 §3.3.5 form one) or a bare DATE.

    ``icalendar`` hands back a naive ``datetime`` for ``DTSTART:20260821T170000`` and a ``date``
    for an all-day event; neither can be turned into an instant without inventing a zone, and this
    service refuses to invent one (the alternative is a bot dialling in an hour off).
    """
    prop = comp.get("DTSTART")
    value = getattr(prop, "dt", None) if prop is not None else None
    if isinstance(value, datetime):
        return value.tzinfo is None
    return isinstance(value, date)


def _text(comp, key: str) -> str:
    v = comp.get(key)
    return str(v) if v is not None else ""


def _calendar_parts(msg) -> list[bytes]:
    """Every calendar payload in the message, in the order a client would prefer them.

    ``text/calendar`` first (Google sends only this; Outlook sends it AND an ``invite.ics``
    attachment carrying the same event), then ``.ics``-named attachments of any content-type —
    Exchange relays that transcode the body still leave the attachment intact, and some mail
    gateways ship the invite ONLY as ``application/octet-stream; name=invite.ics``.
    """
    calendar, attached = [], []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ctype = (part.get_content_type() or "").lower()
        filename = part.get_filename() or ""
        try:
            payload = part.get_payload(decode=True)
        except Exception:                                   # pragma: no cover - defensive
            payload = None
        if not payload:
            continue
        if ctype == "text/calendar":
            calendar.append(payload)
        elif ctype in ("application/ics", "text/x-vcalendar") or _ICS_NAME.search(filename):
            attached.append(payload)
    return calendar + attached


def _content_type_method(msg) -> Optional[str]:
    """The ``method=`` parameter on a ``text/calendar`` content-type, when present."""
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if (part.get_content_type() or "").lower() == "text/calendar":
            m = part.get_param("method")
            if m:
                return str(m).upper()
    return None


def _next_occurrence(comp, *, now: datetime, horizon_days: int, lookback_s: float) -> Optional[datetime]:
    """DTSTART for a one-off; the next in-window RRULE occurrence for a series (EXDATE-respecting).

    Mirrors ``meeting_api.calendar_sync.service._next_occurrence``. A series whose next occurrence
    is beyond the horizon still BINDS (the binding is the series); only the scheduled time is left
    unset until a later sweep — which is why this may return ``None`` without being a rejection.
    """
    dtstart = _as_utc(comp.get("DTSTART") and comp.get("DTSTART").dt)
    if dtstart is None:
        return None
    rrule_prop = comp.get("RRULE")
    window_start = now - timedelta(seconds=lookback_s)
    window_end = now + timedelta(days=horizon_days)
    if not rrule_prop:
        return dtstart
    from dateutil.rrule import rrulestr

    try:
        rule = rrulestr(rrule_prop.to_ical().decode(), dtstart=dtstart)
    except (ValueError, TypeError):
        return dtstart
    exdates: set[datetime] = set()
    ex_prop = comp.get("EXDATE")
    for ex in (ex_prop if isinstance(ex_prop, list) else [ex_prop] if ex_prop else []):
        for d in getattr(ex, "dts", []):
            ex_utc = _as_utc(d.dt)
            if ex_utc:
                exdates.add(ex_utc)
    occurrence = rule.after(window_start, inc=True)
    while occurrence is not None:
        occ = _as_utc(occurrence)
        if occ is None or occ > window_end:
            return None
        if occ not in exdates:
            return occ
        occurrence = rule.after(occurrence)
    return None


def _event_link(comp) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(platform, native_meeting_id, url) from the event's conference properties, or (None,)*3.

    Property order is the one ``calendar_sync`` uses, plus the two Microsoft conference properties
    an Outlook/Teams invite carries — Teams puts the join URL in the body HTML *and* in
    ``X-MICROSOFT-SKYPETEAMSMEETINGURL``, and the property is the unambiguous one.
    """
    for key in ("X-GOOGLE-CONFERENCE", "LOCATION", "X-MICROSOFT-SKYPETEAMSMEETINGURL", "DESCRIPTION"):
        raw = _text(comp, key)
        if not raw:
            continue
        direct = parse_meeting_url(raw.strip(), generic_hosts=False) if raw.strip().startswith("http") else None
        if direct:
            return (direct[0], direct[1], raw.strip())
        found = find_meeting_link(raw)
        if found:
            return found
    # CONFERENCE (RFC 7986) — the standards-track property; its value is the URI itself.
    conf = comp.get("CONFERENCE")
    for c in (conf if isinstance(conf, list) else [conf] if conf is not None else []):
        parsed = parse_meeting_url(str(c).strip(), generic_hosts=False)
        if parsed:
            return (parsed[0], parsed[1], str(c).strip())
    return (None, None, None)


def _roster(comp) -> list[dict]:
    """The ATTENDEE list as participant rows — ``{email, name?, role?, partstat?}``.

    Rooms and resources are dropped (``calendar_sync._attendees``' rule); emails are lowercased,
    which is what makes them a stable identity key. ``ROLE`` is KEPT rather than filtered on: a
    notetaker is nearly always invited as ``OPT-PARTICIPANT``, so treating optional as "don't
    attend" would break the common case — the role rides along so a later policy can distinguish.
    """
    props = comp.get("ATTENDEE")
    out, seen = [], set()
    for a in (props if isinstance(props, list) else [props] if props is not None else []):
        params = getattr(a, "params", {}) or {}
        if str(params.get("CUTYPE", "INDIVIDUAL")).upper() in ("RESOURCE", "ROOM"):
            continue
        email = str(a).strip()
        if email.lower().startswith("mailto:"):
            email = email[7:].strip()
        email = email.strip("<>").lower()
        if "@" not in email or email in seen:
            continue
        seen.add(email)
        row: dict = {"email": email}
        cn = str(params.get("CN", "")).strip()
        if cn and cn.lower() != email:
            row["name"] = cn
        role = str(params.get("ROLE", "")).strip()
        if role:
            row["role"] = role.upper()
        partstat = str(params.get("PARTSTAT", "")).strip()
        if partstat:
            row["partstat"] = partstat.upper()
        out.append(row)
    return out


# ── the entry point ───────────────────────────────────────────────────────────────────────────

def parse_invite(raw: bytes, *, now: Optional[datetime] = None,
                 horizon_days: int = 60, lookback_s: float = 900.0) -> ParsedMail:
    """RFC-822 bytes → ``ParsedMail``. Never raises; a failure is a ``rejection``."""
    now = now or datetime.now(timezone.utc)
    try:
        msg = message_from_bytes(raw, policy=policy.default)
    except Exception as e:                                  # pragma: no cover - defensive
        return ParsedMail(rejection=Rejection(REASON_UNPARSEABLE_ICS, f"unreadable message: {e}"))

    header = lambda k: str(msg.get(k) or "")                # noqa: E731
    recipients: list[str] = []
    for h in _RECIPIENT_HEADERS:
        recipients.extend(_addresses(msg.get_all(h) or []))
    base = ParsedMail(
        message_id=header("Message-ID").strip(),
        subject=header("Subject").strip(),
        sender=next(iter(_addresses(msg.get_all("from") or [])), None),
        recipients=tuple(dict.fromkeys(recipients)),
    )

    payloads = _calendar_parts(msg)
    if not payloads:
        return _with(base, rejection=Rejection(REASON_NO_CALENDAR_PART,
                                               "message carries no text/calendar part or .ics attachment"))

    from icalendar import Calendar

    cal = None
    for payload in payloads:
        try:
            cal = Calendar.from_ical(payload)
            break
        except Exception:
            continue
    if cal is None:
        return _with(base, rejection=Rejection(REASON_UNPARSEABLE_ICS,
                                               "calendar part is not parseable as iCalendar"))

    method = (str(cal.get("METHOD")) if cal.get("METHOD") is not None else None) or _content_type_method(msg)
    method = method.upper() if method else None

    # The master VEVENT owns the series identity. RECURRENCE-ID components are single-occurrence
    # overrides; v0 binds series, so an override-only message is honoured as an update to its UID.
    events = list(cal.walk("VEVENT"))
    if not events:
        return _with(base, method=method,
                     rejection=Rejection(REASON_NO_EVENT, "calendar carries no VEVENT"))
    comp = next((c for c in events if c.get("RECURRENCE-ID") is None), events[0])

    uid = _text(comp, "UID").strip()
    status = _text(comp, "STATUS").strip().upper() or None
    try:
        sequence = int(str(comp.get("SEQUENCE") or 0))
    except (TypeError, ValueError):
        sequence = 0
    rrule_prop = comp.get("RRULE")
    rrule = rrule_prop.to_ical().decode() if rrule_prop is not None else None
    occurrence = _next_occurrence(comp, now=now, horizon_days=horizon_days, lookback_s=lookback_s)
    series_start = _as_utc(comp.get("DTSTART") and comp.get("DTSTART").dt)
    platform, native_id, url = _event_link(comp)
    roster = _roster(comp)

    parsed = _with(
        base,
        method=method,
        uid=uid or None,
        sequence=sequence,
        summary=_text(comp, "SUMMARY").strip() or None,
        description=_text(comp, "DESCRIPTION") or None,
        group_tag=_group_tag(_text(comp, "DESCRIPTION")),
        dtstart=occurrence.isoformat() if occurrence else None,
        series_start=series_start.isoformat() if series_start else None,
        floating_start=_is_floating(comp),
        recurring=rrule is not None,
        rrule=rrule,
        status=status,
        attendees=tuple(r["email"] for r in roster),
        participants=tuple(roster),
        organizer=next(iter(_addresses([comp.get("ORGANIZER")] if comp.get("ORGANIZER") else [])), None),
        platform=platform,
        native_meeting_id=native_id,
        meeting_url=url,
    )

    # STATUS:CANCELLED without METHOD:CANCEL is how some senders retract — treat it as a cancel.
    if method is None and status == "CANCELLED":
        parsed = _with(parsed, method=METHOD_CANCEL)
    # A REQUEST with no method header at all (a bare .ics forward) is still an invitation.
    if parsed.method is None:
        parsed = _with(parsed, method=METHOD_REQUEST,
                       warnings=parsed.warnings + ("no METHOD property — assumed REQUEST",))

    if not uid:
        return _with(parsed, rejection=Rejection(REASON_NO_UID, "VEVENT carries no UID"))
    if parsed.method not in _ACTIONABLE_METHODS:
        return _with(parsed, rejection=Rejection(REASON_UNSUPPORTED_METHOD,
                                                 f"METHOD:{parsed.method} changes no binding"))
    if parsed.method == METHOD_REQUEST:
        if parsed.floating_start:
            # RFC 5545 §3.3.5 form one: a local time with no zone. Legal, and unusable — a
            # workspace default would let us bind it, but a wrong-zone binding sends a bot at the
            # wrong hour and reads as a product failure, where a notice reads as a precise error.
            return _with(parsed, rejection=Rejection(
                REASON_FLOATING_START,
                "DTSTART carries neither TZID nor UTC — the start time is ambiguous"))
        if not native_id:
            return _with(parsed, rejection=Rejection(
                REASON_NO_MEETING_LINK,
                "no Meet/Teams/Zoom link in the conference property, location or description"))
        if not parsed.dtstart:
            return _with(parsed, rejection=Rejection(REASON_NO_START_TIME,
                                                     "no DTSTART inside the scheduling horizon"))
    return parsed


def _with(p: ParsedMail, **changes) -> ParsedMail:
    """Frozen-dataclass replace (the struct is immutable so a corpus row can't be mutated)."""
    from dataclasses import replace
    return replace(p, **changes)
