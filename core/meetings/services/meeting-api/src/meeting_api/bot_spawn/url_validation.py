"""Shared validation for meeting URLs that a bot browser will navigate to."""
from __future__ import annotations

import ipaddress
import os
import re
from urllib.parse import urlparse


class UnsafeMeetingUrl(ValueError):
    """A caller-supplied meeting URL is not safe to pass to the bot runtime."""


_PLATFORM_HOSTS = {
    "google_meet": {"meet.google.com"},
}

# Teams' web client keeps the meeting id in the URL fragment (…/v2#/meet/<digits>). This mirrors
# the sealed ``_TEAMS_MEET_PATH`` predicate in ``zaki_control.router``; it is duplicated here rather
# than imported because ``router`` imports this module, so this module must not import back from it.
_TEAMS_V2_FRAGMENT_MEET = re.compile(r"^/meet/\d{10,15}/?$")


def _zoom_host_is_approved(host: str) -> bool:
    """Mirror the sealed zaki-control.v1 zoom host predicate (validate.mjs /
    meeting_url_matches_platform): commercial and US-gov Zoom hosts.

    Same class of gate-disagreement the teams helper above documents: the sealed control
    predicate admits zoomgov.com / *.zoomgov.com, so a US-gov Zoom URL passed the sealed
    gate and was then refused HERE, surfacing as a generic 422 invalid_request.
    """
    return (
        host == "zoom.us" or host.endswith(".zoom.us")
        or host == "zoomgov.com" or host.endswith(".zoomgov.com")
    )


def _teams_host_is_approved(host: str) -> bool:
    """Mirror the sealed zaki-control.v1 teams host predicate (validate.mjs /
    meeting_url_matches_platform): consumer, enterprise-subdomain and US-gov/DoD Teams hosts.

    The prior exact-match set ({teams.microsoft.com, teams.live.com}) silently narrowed this
    bot_spawn gate below what the sealed control predicate admits, so an enterprise tenant on a
    *.teams.microsoft.com subdomain (or a *.teams.microsoft.us gov/DoD host) passed the sealed
    predicate and was then refused here — the two admission gates disagreed.
    """
    return (
        host == "teams.live.com" or host.endswith(".teams.live.com")
        or host == "teams.microsoft.com" or host.endswith(".teams.microsoft.com")
        or host == "gov.teams.microsoft.us" or host == "dod.teams.microsoft.us"
        or host.endswith(".teams.microsoft.us")
    )


def _host_is_approved(host: str, platform: object) -> bool:
    if platform == "zoom":
        return _zoom_host_is_approved(host)
    if platform == "teams":
        return _teams_host_is_approved(host)
    if platform == "jitsi":
        configured = {
            value.strip().lower().rstrip(".")
            for value in os.getenv("VEXA_JITSI_HOSTS", "").split(",")
            if value.strip()
        }
        return host == "meet.jit.si" or host in configured
    return host in _PLATFORM_HOSTS.get(platform, set())


def _browser_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse the legacy numeric IPv4 forms accepted by the WHATWG URL algorithm."""

    pieces = host.split(".")
    if pieces[-1] == "":
        pieces.pop()
    if not pieces or len(pieces) > 4:
        return None

    numbers: list[int] = []
    for piece in pieces:
        if not piece:
            return None
        base = 10
        digits = piece
        if piece.lower().startswith("0x"):
            base = 16
            digits = piece[2:]
        elif len(piece) > 1 and piece.startswith("0"):
            base = 8
            digits = piece[1:]
        if not digits:
            digits = "0"
        try:
            numbers.append(int(digits, base))
        except ValueError:
            return None

    if any(number > 255 for number in numbers[:-1]):
        raise UnsafeMeetingUrl("meeting_url contains an invalid numeric IP host")
    remaining_bytes = 5 - len(numbers)
    if numbers[-1] >= 256**remaining_bytes:
        raise UnsafeMeetingUrl("meeting_url contains an invalid numeric IP host")

    value = numbers[-1]
    for index, number in enumerate(numbers[:-1]):
        value += number * 256 ** (3 - index)
    return ipaddress.IPv4Address(value)


def validate_meeting_url(url: object, *, platform: object) -> str:
    """Require HTTPS and bind browser navigation to an approved host for the platform."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeMeetingUrl("meeting_url must be a non-empty string")
    raw = url.strip()
    # Chromium applies the WHATWG URL parser, where a backslash in an HTTPS authority is a
    # path separator.  ``urllib.parse`` does not, so accepting it here can validate one host and
    # navigate to another.  Controls have similar parser-dependent normalization behaviour.
    if "\\" in raw or any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise UnsafeMeetingUrl("meeting_url contains a browser-normalized delimiter")
    try:
        parsed = urlparse(raw)
    except ValueError:
        raise UnsafeMeetingUrl("meeting_url does not parse as a URL") from None
    if parsed.scheme != "https":
        raise UnsafeMeetingUrl("meeting_url must use https:// — the bot only joins TLS deployments")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeMeetingUrl("meeting_url cannot contain credentials")
    try:
        host = parsed.hostname
    except ValueError:
        host = None
    if not host:
        raise UnsafeMeetingUrl("meeting_url must have a valid hostname")
    if "%" in host:
        raise UnsafeMeetingUrl("meeting_url hostname cannot use percent encoding")
    try:
        host.encode("ascii")
    except UnicodeEncodeError:
        raise UnsafeMeetingUrl(
            "meeting_url hostname must use its ASCII IDNA form"
        ) from None
    canonical_host = host.lower().rstrip(".")
    if canonical_host == "localhost" or canonical_host.endswith(".localhost"):
        raise UnsafeMeetingUrl("meeting_url cannot target localhost")
    if _browser_ipv4(canonical_host) is not None:
        raise UnsafeMeetingUrl(
            "meeting_url cannot be a browser-normalized IP literal — use the deployment's hostname"
        )
    try:
        ipaddress.ip_address(canonical_host)
    except ValueError:
        pass
    else:
        raise UnsafeMeetingUrl(
            "meeting_url cannot be an IP literal — use the deployment's hostname"
        )
    if not _host_is_approved(canonical_host, platform):
        raise UnsafeMeetingUrl(
            f"meeting_url hostname is not approved for platform {platform!r}"
        )
    return raw


def canonical_meeting_identity(url: object, *, platform: object) -> tuple[str, str]:
    """Return the navigation URL and a stable provider identity for capture deduplication.

    Navigation keeps the approved, whitespace-trimmed URL intact so provider query parameters still
    reach the bot. The opaque identity deliberately normalizes host casing/trailing dots/default
    HTTPS ports and ignores query decorations that do not identify a meeting. Fragments are ignored
    too, EXCEPT the Teams web-client ``…/v2#/meet/<digits>`` form, whose meeting id lives in the
    fragment — that segment is folded in so distinct v2 meetings do not collide on one identity.
    Callers scope the returned identity to their own tenant boundary before persisting it.
    """
    raw = validate_meeting_url(url, platform=platform)
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        raise UnsafeMeetingUrl("meeting_url has an invalid port") from None
    if port not in (None, 443):
        raise UnsafeMeetingUrl("meeting_url must use the default HTTPS port")
    path = parsed.path.rstrip("/") or "/"
    # Teams routes the meeting inside the URL fragment (https://<host>/v2#/meet/<digits>), so
    # urlparse leaves every distinct v2 meeting with the same "/v2" path; without folding the
    # fragment's meeting segment in, they would all collide on one identity within a tenant. Take
    # only the ``/meet/<digits>`` segment (dropping any ?passcode decoration) so distinct meetings
    # get distinct identities and the same meeting stays stable. Other platforms carry the meeting
    # in the path, so their fragments remain ignored decoration.
    if platform == "teams" and path == "/v2" and parsed.fragment:
        fragment_path = urlparse(f"https://x{parsed.fragment}").path.rstrip("/")
        if _TEAMS_V2_FRAGMENT_MEET.match(fragment_path):
            path = f"/v2{fragment_path}"
    return raw, f"https://{host}{path}"
