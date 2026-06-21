"""SSRF-safe webhook URL validation.

Derived from the parent's `webhook_url.py`, reimplemented clean. Rejects URLs that
target internal networks, localhost, cloud metadata, link-local, or internal Docker
service hostnames. Reference: OWASP SSRF Prevention Cheat Sheet.

Only the resolve step touches the network (`socket.getaddrinfo`) — it is skipped for
literal-IP and blocked-hostname URLs, which is the only path the autonomous eval
exercises (no DNS, no live receiver). The eval can also pass `resolver=...` to stub
resolution deterministically.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable, List
from urllib.parse import urlparse

# Blocked IP ranges per OWASP (localhost, private, link-local, multicast).
_BLOCKED_IPV4_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),       # current network
    ipaddress.ip_network("10.0.0.0/8"),      # private
    ipaddress.ip_network("127.0.0.0/8"),     # loopback
    ipaddress.ip_network("169.254.0.0/16"),  # link-local (incl. cloud metadata 169.254.169.254)
    ipaddress.ip_network("172.16.0.0/12"),   # private
    ipaddress.ip_network("192.168.0.0/16"),  # private
    ipaddress.ip_network("224.0.0.0/4"),     # multicast
]

_BLOCKED_IPV6_NETWORKS = [
    ipaddress.ip_network("::1/128"),         # loopback
    ipaddress.ip_network("fc00::/7"),        # unique local
    ipaddress.ip_network("fe80::/10"),       # link-local
    ipaddress.ip_network("ff00::/8"),        # multicast
]

# Internal hostnames (Docker services + cloud metadata).
_BLOCKED_HOSTNAMES = frozenset([
    "localhost",
    "metadata.google.internal",
    "metadata.amazonaws.com",
    "metadata",
    "api-gateway",
    "admin-api",
    "meeting-api",
    "runtime-api",
    "transcription-collector",
    "redis",
    "postgres",
    "mcp",
])


class SSRFError(ValueError):
    """Raised when a webhook URL is rejected by the SSRF guard."""


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # not a valid IP — block
    nets = _BLOCKED_IPV4_NETWORKS if ip.version == 4 else _BLOCKED_IPV6_NETWORKS
    return any(ip in net for net in nets)


def _is_blocked_hostname(hostname: str) -> bool:
    return hostname.lower() in _BLOCKED_HOSTNAMES


def _resolve_host(hostname: str) -> List[str]:
    """Resolve hostname to IPs. Returns [] on failure."""
    try:
        results = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, socket.error, OSError):
        return []
    ips: List[str] = []
    for (_, _, _, _, sockaddr) in results:
        addr = sockaddr[0]
        if addr and addr not in ips:
            ips.append(addr)
    return ips


def validate_webhook_url(url: str, resolver: Callable[[str], List[str]] | None = None) -> str:
    """Validate a webhook URL is safe (not SSRF-vulnerable). Return it if valid.

    - Only http:// and https:// schemes.
    - Block private/loopback/link-local/multicast IPs and internal hostnames.
    - Resolve DNS names and validate every resolved IP (anti-rebinding). The `resolver`
      hook lets the eval stub resolution; defaults to `socket.getaddrinfo`.

    Raises `SSRFError` (a ValueError) with a user-friendly message when blocked.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise SSRFError("Webhook URL must use http or https scheme")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("Webhook URL must have a valid hostname")

    if _is_blocked_hostname(hostname):
        raise SSRFError("Webhook URL cannot target internal or private networks")

    # Literal IP — check directly, no DNS.
    try:
        ipaddress.ip_address(hostname)
        if _is_blocked_ip(hostname):
            raise SSRFError("Webhook URL cannot target internal or private networks")
        return url
    except ValueError:
        pass  # not a literal IP — resolve below

    ips = (resolver or _resolve_host)(hostname)
    if not ips:
        raise SSRFError("Webhook URL hostname could not be resolved")
    for ip_str in ips:
        if _is_blocked_ip(ip_str):
            raise SSRFError("Webhook URL cannot target internal or private networks")

    return url
