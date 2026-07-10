"""Meeting-link → ``(platform, native_meeting_id)`` parsing — the server-side twin of the
terminal's ``clients/terminal/src/surfaces/meetingId.ts`` (same id formats, same platforms).

Used by ``POST /meetings`` / ``PATCH /meetings/{id}`` (a planned meeting created from a pasted
link) and by ``calendar_sync`` (extracting the joinable link out of an ICS event's LOCATION /
DESCRIPTION). Pure string logic — no I/O, no framework imports.

Id formats (mirrors the dashboard join-form):
  * google_meet → ``abc-defg-hij``
  * zoom        → 9–11 digits
  * teams       → the ``19:meeting_…@thread.v2`` thread id, or the ``/meet/<id>`` short-link segment
  * jitsi       → the room name (the URL path on meet.jit.si; self-hosted deployments are not
                  host-inferable — those ride an explicit ``meeting_url`` + ``platform=jitsi``)
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import unquote, urlparse

_GMEET_ID = re.compile(r"^[a-z]{3}-[a-z]{4}-[a-z]{3}$")
_ZOOM_ID = re.compile(r"\d{9,11}")
_TEAMS_THREAD = re.compile(r"19:meeting_[^@%\s/]+@thread\.v2", re.IGNORECASE)
_TEAMS_SHORT = re.compile(r"/meet/([^/?#]+)", re.IGNORECASE)
# A Jitsi room is the URL path's single segment; permissive by design (jitsi accepts nearly any
# room string) but excludes separators/whitespace so a mangled URL never yields a bogus room.
_JITSI_ROOM = re.compile(r"^[^/?#\s]+$")


def parse_meeting_url(raw: str) -> Optional[tuple[str, str]]:
    """Parse a pasted meeting URL (or bare id) → ``(platform, native_meeting_id)``, or ``None``
    when nothing valid can be extracted. Accepts the same inputs the terminal's
    ``parseMeetingInput`` accepts, so a link that validates client-side also validates here."""
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
        if host == "meet.jit.si":
            # Canonical public Jitsi: the room is the path's single segment, kept EXACTLY as it
            # appears in the URL (case + percent-encoding preserved) — the native id is embedded
            # back into the construct-URL template and the DELETE path param, so it must stay
            # URL-safe; decoding here would corrupt rooms with encoded characters.
            room = parsed.path.strip("/")
            return ("jitsi", room) if room and _JITSI_ROOM.match(room) else None
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
        parsed = parse_meeting_url(url)
        if parsed:
            return (parsed[0], parsed[1], url)
    return None
