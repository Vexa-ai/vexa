"""Meeting-link → ``(platform, native_meeting_id)`` parsing — the server-side twin of the
terminal's ``clients/terminal/src/surfaces/meetingId.ts`` (same id formats, same platforms).

Used by ``POST /meetings`` / ``PATCH /meetings/{id}`` (a planned meeting created from a pasted
link) and by ``calendar_sync`` (extracting the joinable link out of an ICS event's LOCATION /
DESCRIPTION). Pure string logic — no I/O, no framework imports; the one config read is
``VEXA_JITSI_HOSTS`` (P14, declared in config.v1.json), consulted per call so tests and
reloads see the live env.

Id formats (mirrors the dashboard join-form):
  * google_meet → ``abc-defg-hij``
  * zoom        → 9–11 digits
  * teams       → the ``19:meeting_…@thread.v2`` thread id, or the ``/meet/<id>`` short-link segment
  * telemost    → the 10-digit ``telemost.yandex.ru/j/<id>`` meeting number
  * jitsi       → the room name (the URL's path segment). Hosts: meet.jit.si and
                  ``VEXA_JITSI_HOSTS``-declared deployments always; *jitsi* / meet-labelled
                  hosts on pasted links only. The room name is deployment-scoped, so the raw
                  URL rides alongside as ``meeting_url`` — never reconstructed from the id.
"""
from __future__ import annotations

import os
import re
from typing import Optional
from urllib.parse import unquote, urlparse

_GMEET_ID = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$")
_ZOOM_ID = re.compile(r"\d{9,11}")
_TEAMS_THREAD = re.compile(r"19:meeting_[^@%\s/]+@thread\.v2", re.IGNORECASE)
_TEAMS_SHORT = re.compile(r"/meet/([^/?#]+)", re.IGNORECASE)
_TELEMOST_PATH = re.compile(r"^/j/(\d{10})/?$")
# A Jitsi room is the URL path's single segment; permissive by design (jitsi accepts nearly any
# room string) but excludes separators/whitespace so a mangled URL never yields a bogus room.
_JITSI_ROOM = re.compile(r"^[^/?#\s]+$")


def _configured_jitsi_hosts() -> set[str]:
    """Deployment-declared Jitsi hostnames (``VEXA_JITSI_HOSTS``, comma-separated) — for
    self-hosted deployments whose hostname carries neither "jitsi" nor a "meet" label. A
    listed host is as explicit as meet.jit.si, so it is honoured in EVERY mode, including
    the calendar (ICS) free-text scan."""
    raw = os.getenv("VEXA_JITSI_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def parse_meeting_url(raw: str, *, generic_hosts: bool = True) -> Optional[tuple[str, str]]:
    """Parse a pasted meeting URL (or bare id) → ``(platform, native_meeting_id)``, or ``None``
    when nothing valid can be extracted. Accepts the same inputs the terminal's
    ``parseMeetingInput`` accepts, so a link that validates client-side also validates here.

    ``generic_hosts`` widens jitsi inference to the self-hosted conventions (a host containing
    "jitsi", or a bare ``meet.*`` host) — right for a DELIBERATELY pasted link, too loose for the
    ICS free-text scan (``find_meeting_link`` passes False so a calendar full of arbitrary links
    never imports a non-meeting as a jitsi room)."""
    value = (raw or "").strip()
    if not value:
        return None

    # Bare Google Meet code, e.g. "abc-defg-hij"
    if _GMEET_ID.match(value.lower()):
        return ("google_meet", value.lower())

    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if host:
        if "meet.google.com" in host:
            code = next((p for p in reversed(parsed.path.split("/")) if p), "").lower()
            return ("google_meet", code) if _GMEET_ID.match(code) else None
        if "zoom" in host:
            m = _ZOOM_ID.search(parsed.path) or _ZOOM_ID.search(parsed.query)
            return ("zoom", m.group(0)) if m else None
        if "teams.microsoft.com" in host or "teams.live.com" in host:
            # Classic deep link carries the thread id (…/l/meetup-join/19:meeting_…@thread.v2).
            thread = _TEAMS_THREAD.search(unquote(value))
            if thread:
                return ("teams", thread.group(0))
            # New short meeting link: teams.microsoft.com/meet/<id>?p=<passcode>.
            short = _TEAMS_SHORT.search(parsed.path)
            if short:
                return ("teams", short.group(1))
            return None
        if host == "telemost.yandex.ru":
            match = _TELEMOST_PATH.match(parsed.path)
            return ("telemost", match.group(1)) if match else None
        # Jitsi: the canonical public deployment, plus (for a deliberately pasted link) the common
        # self-hosted conventions — a host containing "jitsi", or a bare ``meet.*`` host (jitsi's
        # own recommended naming). Known platforms are matched ABOVE, so this only fires for
        # unclaimed hosts. The room is the path's single segment, kept EXACTLY as it appears in
        # the URL (case + percent-encoding preserved) — the native id is embedded back into the
        # construct-URL template and the DELETE path param, so it must stay URL-safe; decoding
        # here would corrupt rooms with encoded characters. Callers keep the raw URL alongside
        # (``meeting_url``) so a self-hosted room joins on ITS deployment, not the template's.
        is_jitsi_host = (
            host == "meet.jit.si"
            or host in _configured_jitsi_hosts()     # deployment-declared (VEXA_JITSI_HOSTS)
            # Naming HEURISTICS — pasted-link-only (a deliberate user action): a host naming
            # jitsi, or a "meet" hostname LABEL anywhere (meet.example.org, eu.meet.example.org —
            # jitsi's recommended naming, regionalized). Both are too loose for the ICS scan,
            # where an event description full of arbitrary links (jitsi.github.io docs, vendor
            # meet.* products) must not import as joinable rooms — there, only the explicit
            # hosts above count; VEXA_JITSI_HOSTS is the opt-in.
            or (generic_hosts and ("jitsi" in host or "meet" in host.split(".")))
        )
        if is_jitsi_host:
            room = parsed.path.strip("/")
            if not room or not _JITSI_ROOM.match(room):
                return None
            # A jitsi room name is deployment-scoped, so the native id embeds the host for
            # every non-canonical deployment (room@host — jitsi's own XMPP identity shape).
            # A bare room would make meet.jit.si/daily and video.corp/daily collide on every
            # (platform, native_meeting_id) key: duplicate checks, calendar adoption, MCP
            # idempotency. meet.jit.si keeps the bare room (canonical, unambiguous).
            return ("jitsi", room if host == "meet.jit.si" else f"{room}@{host}")
        return None

    # Bare numeric id → assume Zoom
    if re.fullmatch(r"\d{9,11}", value):
        return ("zoom", value)

    return None


def find_meeting_link(text: str) -> Optional[tuple[str, str, str]]:
    """Scan free text (an ICS LOCATION/DESCRIPTION) for the FIRST recognizable meeting URL →
    ``(platform, native_meeting_id, url)``, or ``None``. Only http(s) URLs are considered."""
    if not text:
        return None
    for m in re.finditer(r"https?://[^\s<>\"']+", text):
        url = m.group(0).rstrip(").,;")
        # Free-text scan: hold jitsi to the explicit hosts (meet.jit.si + VEXA_JITSI_HOSTS) —
        # a calendar description is full of arbitrary links, and the pasted-link naming
        # heuristics (*jitsi* / ``meet.*``) would misread them as rooms.
        parsed = parse_meeting_url(url, generic_hosts=False)
        if parsed:
            return (parsed[0], parsed[1], url)
    return None
