"""Shared validation for meeting URLs that a bot browser will navigate to."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


class UnsafeMeetingUrl(ValueError):
    """A caller-supplied meeting URL is not safe to pass to the bot runtime."""


def validate_meeting_url(url: object) -> str:
    """Require an HTTPS hostname and reject localhost or IP-literal targets."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeMeetingUrl("meeting_url must be a non-empty string")
    raw = url.strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        raise UnsafeMeetingUrl("meeting_url does not parse as a URL") from None
    if parsed.scheme != "https":
        raise UnsafeMeetingUrl("meeting_url must use https:// — the bot only joins TLS deployments")
    try:
        host = parsed.hostname
    except ValueError:
        host = None
    if not host:
        raise UnsafeMeetingUrl("meeting_url must have a valid hostname")
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        raise UnsafeMeetingUrl("meeting_url cannot target localhost")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UnsafeMeetingUrl(
            "meeting_url cannot be an IP literal — use the deployment's hostname"
        )
    return raw
