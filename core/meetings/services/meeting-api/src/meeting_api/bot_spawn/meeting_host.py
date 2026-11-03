"""Where a bot's browser may be sent — the host rule for a caller-supplied ``meeting_url``.

The bot's Chromium navigates to the meeting URL it is given, and an authenticated caller chooses
that URL. So the URL is held to two rules before a bot is spawned for it, on every path that
spawns one (``POST /bots``, the MCP and agent tools that call it, and the calendar auto-join):

1. **The host belongs to the platform.** Google Meet joins on ``meet.google.com``; Teams on its
   web-client hosts (``teams.microsoft.com``, ``teams.live.com``, the GCC-High/DoD clouds and the
   ``cloud.microsoft`` rename); Zoom on ``zoom.us``, ``zoom.com`` or ``zoomgov.com`` (an
   organisation's vanity ``<org>.zoom.us`` included). Two platforms also live on hosts an
   organisation chooses: Jitsi on any deployment (``meet.jit.si`` is only the public one), and Zoom
   behind an organisation's own front door when the link has Zoom's own join shape
   (``collector.meeting_link``). Those are accepted on any host that resolves publicly, or on a
   host the operator names in ``VEXA_MEETING_HOST_ALLOW``.
2. **The host reaches the internet, not the deployment.** Every address the host resolves to is
   checked (a host this service cannot resolve passes here; the bot checks it again at navigation) by the outbound URL guard (``webhooks/ssrf.py``, the vendored outbound-url.v1 rule):
   loopback, private, CGNAT, link-local and cloud metadata, and reserved addresses refuse the URL.
   ``VEXA_MEETING_HOST_ALLOW`` (an ``OperatorAllowance``: exact host names and CIDRs) admits an
   on-prem meeting server inside the operator's network; it never admits loopback or metadata.

The bot holds the same rule at navigation time (``meetingHostAllow`` on invocation.v1 carries the
operator's entries): a navigation or redirect that leaves the platform's hosts, or that resolves
inside, is aborted before it leaves the browser.
"""
from __future__ import annotations

import asyncio
import os
from typing import Callable, List, Optional
from urllib.parse import urlsplit

from ..collector.meeting_link import parse_meeting_url
from ..webhooks import ssrf

MEETING_HOST_ALLOW_ENV = "VEXA_MEETING_HOST_ALLOW"

#: Teams web-client host suffixes — the same set the join layer recognizes as "this page IS Teams"
#: (``join/src/msteams/auth-redirect.ts`` ``TEAMS_HOST_SUFFIXES``).
TEAMS_HOST_SUFFIXES = (
    "teams.microsoft.com",
    "teams.live.com",
    "teams.microsoft.us",
    "teams.cloud.microsoft",
)

#: The domains each platform's meetings are joined on (a domain also covers its subdomains).
PLATFORM_DOMAINS = {
    "google_meet": ("meet.google.com",),
    "teams": TEAMS_HOST_SUFFIXES,
    "zoom": ("zoom.us", "zoom.com", "zoomgov.com"),
    "jitsi": ("meet.jit.si",),
}

#: Platforms whose meetings may also live on a host the organisation chose.
CUSTOM_HOST_PLATFORMS = frozenset({"zoom", "jitsi"})

#: The DNS seam. ``None`` is the system resolver; the test suite replaces it.
resolver: Optional[Callable[[str], List[str]]] = None


class MeetingHostRefused(ValueError):
    """The meeting URL may not be given to a bot. The message is for the person who sent it."""


def meeting_host_allowance(raw: Optional[str] = None) -> ssrf.OperatorAllowance:
    """The operator's ``VEXA_MEETING_HOST_ALLOW``, parsed (``raw`` is the test seam). Raises
    ``ValueError`` naming a bad entry; the boot calls this once so a typo refuses the boot."""
    value = os.getenv(MEETING_HOST_ALLOW_ENV, "") if raw is None else raw
    return ssrf.parse_allowance(value, what=MEETING_HOST_ALLOW_ENV)


def allowance_entries(raw: Optional[str] = None) -> List[str]:
    """The operator's entries as the bot receives them (``meetingHostAllow``), or ``[]``."""
    allow = meeting_host_allowance(raw)
    return sorted(allow.hosts) + [str(n) for n in allow.networks]


def _on_domains(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _platform_host_reason(platform: str, url: str, host: str,
                          allow: ssrf.OperatorAllowance) -> Optional[str]:
    """Why ``host`` is not a ``platform`` meeting host, or ``None`` when it is."""
    domains = PLATFORM_DOMAINS.get(platform)
    if domains is None:
        return f"platform '{platform}' joins no meeting URL"
    if _on_domains(host, domains):
        return None
    if platform in CUSTOM_HOST_PLATFORMS:
        if allow.admits_host(host):
            return None
        if platform == "jitsi":
            return None
        parsed = parse_meeting_url(url)
        if parsed is not None and parsed[0] == "zoom":
            return None
        return (f"'{host}' is not a Zoom host — use the zoom.us link, or ask the operator to list "
                f"the host in {MEETING_HOST_ALLOW_ENV}")
    names = ", ".join(domains)
    return f"'{host}' is not a {platform} host — the meeting URL must be on {names}"


def check_meeting_url(platform: str, url: str, *,
                      allow: Optional[ssrf.OperatorAllowance] = None,
                      resolve: Optional[Callable[[str], List[str]]] = None) -> List[str]:
    """Hold ``url`` to both rules for ``platform``; the checked addresses, or ``MeetingHostRefused``.

    Blocking (DNS). ``check_meeting_url_async`` runs it off the event loop."""
    allow = meeting_host_allowance() if allow is None else allow
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        raise MeetingHostRefused("meeting_url does not parse as a URL") from None
    if parts.scheme.lower() != "https" or not host:
        raise MeetingHostRefused("meeting_url must be an https:// URL with a host")
    literal = ssrf.literal_address(host)
    if literal is not None:
        if platform in CUSTOM_HOST_PLATFORMS and allow.admits_ip(literal):
            return [str(literal)]
        raise MeetingHostRefused(
            "meeting_url cannot be an IP literal — use the deployment's hostname, or ask the "
            f"operator to list its network in {MEETING_HOST_ALLOW_ENV}")
    reason = _platform_host_reason(platform, url, host, allow)
    if reason:
        raise MeetingHostRefused(f"meeting_url refused: {reason}")
    try:
        pinned = ssrf.validate_url(url, resolve or resolver, what="meeting_url",
                                   allow=allow or None)
    except ssrf.SSRFError as e:
        if "could not be resolved" in str(e):
            # Nothing to reach from here. A deployment whose egress goes through a proxy may not
            # resolve public names at all; the bot checks every navigation again with its own
            # resolver, and a name nothing resolves fails to load there.
            return []
        raise MeetingHostRefused(
            f"meeting_url refused: '{host}' reaches an internal or private address — an on-prem "
            f"meeting server is admitted only when the operator lists it in {MEETING_HOST_ALLOW_ENV}"
        ) from None
    return list(pinned.pinned_ips)


async def check_meeting_url_async(platform: str, url: str, *,
                                  allow: Optional[ssrf.OperatorAllowance] = None) -> List[str]:
    """``check_meeting_url`` with its DNS lookup off the event loop."""
    return await asyncio.to_thread(check_meeting_url, platform, url, allow=allow)
