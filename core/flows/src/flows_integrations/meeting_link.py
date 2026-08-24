"""Meeting-link extraction for the INVITE lane — ``(platform, native_meeting_id, passcode)``
out of an ICS, for any platform, with "I do not support this one" as a TYPED answer rather than
a guess.

WHY THIS EXISTS. The invite intake used to carry a single regex —
``https://meet\\.google\\.com/[a-z-]+`` — so a Microsoft Teams invite (which is what an Outlook
mailbox in a bank emits) fell off the end of the parser: ``parse_ics`` returned None, the mail
was logged as "ignored", and nobody was told. The alternative failure was worse: the id fallback
in ``dispatch_bot`` was ``url.rsplit("/", 1)[1]``, which on a Teams deep link yields the
percent-encoded thread blob plus its ``?context=…`` query — a confident dispatch at a meeting
that does not exist.

THE RULES ARE NOT DERIVED HERE. They are mirrored, deliberately and literally, from the
product's own parser — ``core/meetings/services/meeting-api/src/meeting_api/collector/
meeting_link.py`` (``parse_meeting_url`` / ``find_meeting_link``) — because the gateway is what
must ultimately accept the id, and two parsers that disagree produce a 422 at best and a bot in
the wrong room at worst. flows is a separate deployable and imports no meeting code (steps reach
domains by HTTP only), so the rules are copied with their provenance rather than imported:

  * ``google_meet`` → the dash code ``abc-defg-hij`` (``_GMEET_ID``)
  * ``teams``       → the deep-link thread id ``19:meeting_…@thread.v2`` (``_TEAMS_THREAD``,
                      matched AFTER percent-decoding, exactly as the product does), or the new
                      short link's ``/meet/<id>`` path segment (``_TEAMS_SHORT``)
  * ``zoom``        → 9–11 digits — RECOGNIZED, and deliberately NOT supported here (below)
  * passcode        → the ``?p=`` (teams) / ``?pwd=`` (zoom) query param, named ``passcode``,
                      mirroring ``bot_spawn/router.py::_passcode_from_url``
  * an id may never carry ``? # & = /`` — ``bot_spawn/router.py::NATIVE_MEETING_ID_URL_CHARS``
    (#892: a Teams passcode left on the id built an unjoinable URL and an unfindable row)

ONE DELIBERATE DIVERGENCE FROM THE PRODUCT PARSER, and it is an ICS fact rather than a taste:
a real Outlook VEVENT carries ``X-MICROSOFT-SCHEDULINGSERVICEUPDATEURL`` —
``https://api.scheduler.teams.microsoft.com/teams/<tid>/19:meeting_…@thread.v2/0`` — whose host
passes the product's substring gate and whose path holds the thread id UNENCODED. The product's
scan never meets it (it reads LOCATION/DESCRIPTION, not the X- properties); this lane reads the
whole VEVENT as a fallback, so it would parse that management endpoint as the join link — and
the gateway PREFERS a supplied ``meeting_url`` over its own template
(``bot_spawn/service.py``: ``constructed_url = meeting_url or construct_meeting_url(…)``), so the
bot would be pointed at a scheduler API. Teams links are therefore accepted only on the two join
paths the product can actually join: ``/l/meetup-join/…`` and ``/meet/<id>``.

SUPPORTED, IN THIS LANE, IS A SHORTER LIST THAN THE API'S. ``bot_spawn/service.py::
_URL_TEMPLATES`` has exactly two entries — ``google_meet`` and ``teams`` — and every other
platform is joinable only when the CALLER supplies a full ``meeting_url`` it vouches for. An
unattended invite lane cannot vouch: the link arrived in someone else's calendar entry. So the
flows lane supports the two constructible platforms and answers *unsupported* for the rest,
which is a fact the organizer gets told, not a silent drop.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

#: The platforms this lane will dispatch a bot at — the constructible set from the product's
#: ``bot_spawn/service.py::_URL_TEMPLATES``. Everything else is recognized and refused.
SUPPORTED_PLATFORMS = ("google_meet", "teams")

#: Mirrors ``bot_spawn/router.py::NATIVE_MEETING_ID_URL_CHARS`` — the id is interpolated into a
#: URL path segment and reused as a lookup key, so none of these may survive into it.
NATIVE_MEETING_ID_URL_CHARS = "?#&=/"

#: Mirrors ``bot_spawn/router.py::NATIVE_MEETING_ID_MAX_LEN`` — ``meetings.platform_specific_id``
#: is varchar(255), so a longer id is a 422 at dispatch. Refuse it here, where there is still a
#: human to tell.
NATIVE_MEETING_ID_MAX_LEN = 255

_GMEET_ID = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$")
_ZOOM_ID = re.compile(r"\d{9,11}")
_TEAMS_THREAD = re.compile(r"19:meeting_[^@%\s/]+@thread\.v2", re.IGNORECASE)
_TEAMS_SHORT = re.compile(r"/meet/([^/?#]+)", re.IGNORECASE)

_URL = re.compile(r"https?://[^\s<>\"'\\]+")

#: RFC 5545 §3.1 folding — a CRLF followed by one space or tab is a continuation, not a break.
#: Outlook folds hard at 75 octets, so a Teams deep link routinely arrives split in three.
_FOLD = re.compile(r"\r?\n[ \t]")


@dataclass(frozen=True)
class MeetingLink:
    """A link we recognized. ``supported`` is the whole point of the type: an unsupported
    platform is a NAMED platform with a URL we can quote back to the organizer, never a None
    that reads as "no meeting here"."""

    platform: str
    native_meeting_id: str
    url: str
    passcode: Optional[str] = None

    @property
    def supported(self) -> bool:
        return self.platform in SUPPORTED_PLATFORMS


def unfold(text: str) -> str:
    """RFC 5545 line unfolding. Must run before any regex or half a URL matches."""
    return _FOLD.sub("", (text or "").replace("\r\n", "\n"))


def unescape(text: str) -> str:
    """ICS text-value escaping (``\\,`` ``\\;`` ``\\n``) plus HTML entities.

    Both matter for the SAME field: Outlook writes the joinable link into DESCRIPTION with ICS
    escapes and into ``X-ALT-DESC;FMTTYPE=text/html`` as HTML, where ``&`` is ``&amp;`` — a
    Teams URL carrying ``?context=…&anon=…`` is mangled in exactly the place the passcode
    lives. ``\\n`` becomes a real newline so it TERMINATES a URL match (an escaped newline is a
    line break in the value, not a URL character)."""
    if not text:
        return ""
    out = text.replace("\\n", "\n").replace("\\N", "\n")
    out = out.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    return html.unescape(out)


def _prop(block: str, name: str) -> str:
    """The value of an ICS property (already-unfolded block), or "". Property parameters
    (``;FMTTYPE=…``, ``;TZID=…``) sit between the name and the colon."""
    m = re.search(rf"^{re.escape(name)}(?:;[^:\n]*)?:(.*)$", block, re.I | re.M)
    return m.group(1) if m else ""


def _passcode_from_url(url: str) -> Optional[str]:
    """Mirrors ``bot_spawn/router.py::_passcode_from_url`` — teams ``?p=``, zoom ``?pwd=``."""
    try:
        query = parse_qs(urlparse(url).query)
    except Exception:  # noqa: BLE001 — a malformed URL is a data fact, never an exception here
        return None
    for key in ("pwd", "p"):
        values = query.get(key)
        if values and values[0]:
            return values[0]
    return None


def _clean_id(native_id: str) -> Optional[str]:
    """Reject an id the gateway would reject — URL structure (#892), control characters, or
    over-length — so the refusal happens at intake with a reason, not as a 422 at dispatch
    time with a bot already promised. Mirrors the three id validations in
    ``bot_spawn/router.py`` (``NATIVE_MEETING_ID_URL_CHARS``, the control-char scan, and
    ``NATIVE_MEETING_ID_MAX_LEN``). A parser that has to strip these has matched the wrong
    thing — better a typed None than an unjoinable dispatch."""
    if not native_id:
        return None
    if len(native_id) > NATIVE_MEETING_ID_MAX_LEN:
        return None
    if any(c in native_id for c in NATIVE_MEETING_ID_URL_CHARS) or any(
        c.isspace() for c in native_id
    ):
        return None
    if any(c == "\x7f" or c < " " for c in native_id):
        return None
    return native_id


def parse_meeting_url(raw: str) -> Optional[MeetingLink]:
    """One URL → a ``MeetingLink`` (supported or not), or None when it is not a meeting link.

    Host-gated exactly like the product's ``parse_meeting_url``. Jitsi's naming heuristics are
    deliberately NOT reproduced: the product itself disables them for the ICS scan
    (``find_meeting_link`` passes ``generic_hosts=False``) because a calendar description full
    of arbitrary links would import vendor ``meet.*`` products as joinable rooms."""
    value = (raw or "").strip().rstrip(").,;>\"'")
    if not value:
        return None
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if not host:
        return None

    if "meet.google.com" in host:
        code = next((p for p in reversed(parsed.path.split("/")) if p), "").lower()
        if not _GMEET_ID.match(code):
            return None
        return MeetingLink("google_meet", code, value)

    if "teams.microsoft.com" in host or "teams.live.com" in host:
        # NOT every teams.microsoft.com URL in an Outlook invite is joinable, and this lane must
        # care because the gateway prefers a supplied meeting_url over its own template
        # (``bot_spawn/service.py``: ``constructed_url = meeting_url or construct_meeting_url(…)``).
        # A real Outlook VEVENT carries X-MICROSOFT-SCHEDULINGSERVICEUPDATEURL —
        # ``https://api.scheduler.teams.microsoft.com/teams/<tid>/19:meeting_…@thread.v2/0`` —
        # which contains the thread id UNENCODED and would otherwise parse as the join link,
        # sending the bot to a scheduler API endpoint. The join paths are the only two the
        # product knows how to join, so require one of them.
        path = parsed.path
        # Classic deep link: …/l/meetup-join/19%3ameeting_…%40thread.v2?context=…
        # Percent-decode FIRST — Outlook writes the thread id encoded, and the id the gateway
        # stores is the DECODED one (product parser does the same ``unquote``).
        thread = _TEAMS_THREAD.search(unquote(value)) if "/meetup-join/" in path.lower() else None
        native = thread.group(0) if thread else None
        if native is None:
            short = _TEAMS_SHORT.search(parsed.path)          # teams.microsoft.com/meet/<id>?p=…
            native = short.group(1) if short else None
        native = _clean_id(native or "")
        if native is None:
            return None
        return MeetingLink("teams", native, value, _passcode_from_url(value))

    if "zoom" in host:
        m = _ZOOM_ID.search(parsed.path) or _ZOOM_ID.search(parsed.query)
        if not m:
            return None
        # Recognized, NOT supported — see the module docstring. Returned so the organizer can be
        # told which platform we saw, instead of being ignored.
        return MeetingLink("zoom", m.group(0), value, _passcode_from_url(value))

    return None


def _scan(text: str) -> Optional[MeetingLink]:
    """First recognizable meeting URL in a blob of free text, preferring a SUPPORTED one.

    "First hit wins" alone is wrong here: an Outlook body can carry a dial-in vendor link, a
    Zoom link from a forwarded thread and the real Teams link in any order. A supported platform
    anywhere in the blob beats an unsupported one earlier in it; the unsupported hit is kept so
    a Zoom-only invite still reports *zoom*, not silence."""
    if not text:
        return None
    fallback: Optional[MeetingLink] = None
    for m in _URL.finditer(unescape(text)):
        link = parse_meeting_url(m.group(0))
        if link is None:
            continue
        if link.supported:
            return link
        fallback = fallback or link
    return fallback


def find_in_ics(ics: str, vevent: str) -> Optional[MeetingLink]:
    """The joinable link of an invite, looked for where Outlook and Google actually put it.

    Three ICS locations carry a Teams link and they disagree with each other on a forwarded or
    edited invite, so the order is a PREFERENCE, not a search:

      1. ``X-MICROSOFT-SKYPETEAMSMEETINGURL`` — Outlook's own explicit statement of the join
         URL. When present it is the answer; nothing in the body outranks it.
      2. ``X-GOOGLE-CONFERENCE`` — the same explicit statement on the Google side.
      3. ``LOCATION`` — where a Teams invite usually lands ("Microsoft Teams Meeting" plus URL).
      4. ``DESCRIPTION`` / ``X-ALT-DESC`` — the human body, folded and (in X-ALT-DESC) HTML-
         escaped; the "Click here to join the meeting" anchor.
      5. the whole VEVENT, then the whole VCALENDAR — the pre-existing fallback, kept so no
         invite that parsed before this change stops parsing.

    A supported platform found at ANY level beats an unsupported one found earlier."""
    ics = unfold(ics or "")
    vevent = unfold(vevent or "")
    fallback: Optional[MeetingLink] = None
    candidates = (
        _prop(vevent, "X-MICROSOFT-SKYPETEAMSMEETINGURL"),
        _prop(vevent, "X-GOOGLE-CONFERENCE"),
        _prop(vevent, "LOCATION"),
        _prop(vevent, "DESCRIPTION"),
        _prop(vevent, "X-ALT-DESC"),
        vevent,
        ics,
    )
    for text in candidates:
        link = _scan(text)
        if link is None:
            continue
        if link.supported:
            return link
        fallback = fallback or link
    return fallback
