"""Meeting-link → ``(platform, native_meeting_id)`` parsing — the server-side twin of the
terminal's ``clients/terminal/src/surfaces/meetingId.ts`` (same id formats, same platforms).

Used by ``POST /meetings`` / ``PATCH /meetings/{id}`` (a planned meeting created from a pasted
link) and by ``calendar_sync`` (extracting the joinable link out of an ICS event's LOCATION /
DESCRIPTION). Pure string logic — no I/O, no framework imports; the one config read is
``VEXA_JITSI_HOSTS`` (P14, declared in config.v1.json), consulted per call so tests and
reloads see the live env.

Hosts are matched EXACTLY or as a dotted subdomain (``_host_matches``) — never by substring,
which would read ``meet.google.com.attacker.example`` as Google Meet. ``vexa_mailroom``'s
vendored copy carries the same helper; the two parsers are meant to agree.

Id formats (mirrors the dashboard join-form):
  * google_meet → ``abc-defg-hij``
  * zoom        → 9–11 digits
  * teams       → the ``19:meeting_…@thread.v2`` thread id, or the ``/meet/<id>`` short-link segment
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
# A Jitsi room is the URL path's single segment; permissive by design (jitsi accepts nearly any
# room string) but excludes separators/whitespace so a mangled URL never yields a bogus room.
_JITSI_ROOM = re.compile(r"^[^/?#\s]+$")

# Zoom's meeting domains. Canonical zoom.us (+ every regional subdomain: us02web, us05web, a
# customer's company.zoom.us) and the US-government tenant. Matches the join brick's own rule
# (``modules/join/src/index.ts::resolvePlatform``) — a white-label portal is not inferable from
# its URL, so it is not listed here and never was.
_ZOOM_DOMAINS = ("zoom.us", "zoomgov.com")
# Every domain a hosted platform claims. Used both to match and to recognise a lookalike.
_PLATFORM_DOMAINS = ("meet.google.com", *_ZOOM_DOMAINS, "teams.microsoft.com", "teams.live.com")


def _host_matches(host: str, *domains: str) -> bool:
    """Exact host, or a subdomain of one of ``domains`` — never a substring match.

    A substring test (``"meet.google.com" in host``) also accepts
    ``meet.google.com.attacker.example``: the platform name is a *prefix* of an arbitrary
    domain the submitter controls, so anyone who can post a meeting URL chooses the host the
    bot's browser later opens. The registrable domain is the rightmost part of a hostname, so
    the only sound test is equality or a dotted suffix.

    The mailroom's vendored copy (``vexa_mailroom.meeting_link``) carries the same helper; the
    two parsers must agree, since the mailroom hands its ``meeting_url`` to ``POST /meetings``
    for a second parse.
    """
    return any(host == d or host.endswith("." + d) for d in domains)


def _is_platform_lookalike(host: str) -> bool:
    """True when ``host`` merely CONTAINS a platform's domain without being it or a subdomain
    of it — ``meet.google.com.attacker.example``, ``zoom.us.attacker.example``.

    Such a host is not the platform, and it must not reach the jitsi naming heuristics either:
    ``meet.google.com.attacker.example`` carries a "meet" LABEL, so the self-hosted fallback
    below would otherwise adopt it as a jitsi room and hand the bot the same attacker-chosen
    host the exact-match rule just refused. Explicitly declared hosts (``VEXA_JITSI_HOSTS``)
    are unaffected — an operator naming their own deployment is not a guess.
    """
    return any(d in host and not _host_matches(host, d) for d in _PLATFORM_DOMAINS)


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
        if _host_matches(host, "meet.google.com"):
            code = next((p for p in reversed(parsed.path.split("/")) if p), "").lower()
            return ("google_meet", code) if _GMEET_ID.match(code) else None
        if _host_matches(host, *_ZOOM_DOMAINS):
            m = _ZOOM_ID.search(parsed.path) or _ZOOM_ID.search(parsed.query)
            return ("zoom", m.group(0)) if m else None
        if _host_matches(host, "teams.microsoft.com", "teams.live.com"):
            # Classic deep link carries the thread id (…/l/meetup-join/19:meeting_…@thread.v2).
            thread = _TEAMS_THREAD.search(unquote(value))
            if thread:
                return ("teams", thread.group(0))
            # New short meeting link: teams.microsoft.com/meet/<id>?p=<passcode>.
            short = _TEAMS_SHORT.search(parsed.path)
            if short:
                return ("teams", short.group(1))
            return None
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
            # hosts above count; VEXA_JITSI_HOSTS is the opt-in. A host that merely LOOKS like
            # a hosted platform is excluded outright: the branches above already refused it by
            # name, and meet.google.com.attacker.example carries a "meet" label that would
            # otherwise let it back in through this door.
            or (
                generic_hosts
                and not _is_platform_lookalike(host)
                and ("jitsi" in host or "meet" in host.split("."))
            )
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
