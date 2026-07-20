"""Shared validation for meeting URLs that a bot browser will navigate to."""
from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse


class UnsafeMeetingUrl(ValueError):
    """A caller-supplied meeting URL is not safe to pass to the bot runtime."""


_PLATFORM_HOSTS = {
    "google_meet": {"meet.google.com"},
    "teams": {"teams.microsoft.com", "teams.live.com"},
}


def _host_is_approved(host: str, platform: object) -> bool:
    if platform == "zoom":
        return host == "zoom.us" or host.endswith(".zoom.us")
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
    HTTPS ports and ignores fragments/query decorations that do not identify a meeting. Callers
    scope the returned identity to their own tenant boundary before persisting it.
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
    return raw, f"https://{host}{path}"
