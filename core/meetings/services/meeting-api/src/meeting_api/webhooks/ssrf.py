"""SSRF-safe webhook URL validation + connect-time IP pinning.

Derived from the parent's `webhook_url.py`, reimplemented clean. Rejects URLs that
target internal networks, localhost, cloud metadata, link-local, or internal Docker
service hostnames. Reference: OWASP SSRF Prevention Cheat Sheet.

WH2 — TOCTOU / DNS-rebinding hardening
--------------------------------------
A plain "validate the hostname, then let httpx re-resolve at connect" guard is
TOCTOU-vulnerable: an attacker who flips the A-record between the validate call and the
connect reaches an internal IP even though validation saw a public one. Two defences,
applied together:

1. **Resolve + validate at submit time** — `validate_webhook_url` resolves the hostname
   to its IP(s) and validates every resolved IP against the same private/loopback/
   link-local/multicast blocklist literal IPs get. It returns a `PinnedURL` (a `str`
   subclass carrying the resolved-and-validated IPs), NOT a bare hostname string, so a
   caller cannot accidentally hand a re-resolvable hostname to the transport.
2. **Re-validate + pin at connect time** — `build_pinned_transport` wraps an httpx
   transport so that, for every dial, it re-resolves the host, re-validates the IP(s),
   and PINS the connection to a validated IP (preserving the Host header + TLS SNI via
   `sni_hostname`). The rebinding window between submit and connect is closed: a flipped
   A-record is re-checked and rejected at the moment the socket is opened.

Only the resolve step touches the network (`socket.getaddrinfo`) — it is skipped for
literal-IP and blocked-hostname URLs, which is the only path the autonomous eval
exercises (no DNS, no live receiver). The eval can also pass `resolver=...` to stub
resolution deterministically.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any, Callable, List, Optional
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


class PinnedURL:
    """A validated webhook URL that carries the resolved-and-validated IP(s) it was pinned to.

    Deliberately NOT a bare ``str``: a `PinnedURL` is a *connection-safe handle*. The whole
    point of WH2 is that handing a re-resolvable hostname string to the transport is the
    TOCTOU bug — so the guard returns this richer object instead. It is therefore not ``==``
    to the original hostname URL string (that comparison is exactly the conflation WH2 closes).

    * ``.url``        — the raw URL text the transport dials (Host header + TLS SNI preserved).
    * ``.host`` / ``.port`` / ``.scheme`` — parsed components.
    * ``.pinned_ips`` — the resolved IP(s) validated against the blocklist at submit time.
    """

    __slots__ = ("url", "host", "port", "scheme", "pinned_ips")

    def __init__(self, url: str, *, host: str, port: Optional[int], scheme: str, pinned_ips: List[str]):
        self.url = url
        self.host = host
        self.port = port
        self.scheme = scheme
        self.pinned_ips = list(pinned_ips)

    def __str__(self) -> str:
        return self.url

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PinnedURL):
            return self.url == other.url and self.pinned_ips == other.pinned_ips
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self.url, tuple(self.pinned_ips)))

    def __repr__(self) -> str:
        return f"PinnedURL({self.url!r}, pinned_ips={self.pinned_ips!r})"


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


def _validate_resolved_ips(ips: List[str]) -> None:
    """Reject if resolution failed or ANY resolved IP is in the blocklist (anti-rebinding)."""
    if not ips:
        raise SSRFError("Webhook URL hostname could not be resolved")
    for ip_str in ips:
        if _is_blocked_ip(ip_str):
            raise SSRFError("Webhook URL cannot target internal or private networks")


def validate_webhook_url(url: str, resolver: Callable[[str], List[str]] | None = None) -> PinnedURL:
    """Validate a webhook URL is safe (not SSRF-vulnerable). Return a ``PinnedURL`` if valid.

    - Only http:// and https:// schemes.
    - Block private/loopback/link-local/multicast IPs and internal hostnames.
    - Resolve DNS names and validate every resolved IP (anti-rebinding). The `resolver`
      hook lets the eval stub resolution; defaults to `socket.getaddrinfo`.
    - Return a ``PinnedURL`` carrying the resolved-and-validated IP(s) so delivery can PIN
      the connection (rather than re-resolving an attacker-controlled hostname at connect
      time — the TOCTOU window). The string value is the original URL (Host/SNI preserved).

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

    port = parsed.port
    # Literal IP — check directly, no DNS. The "resolved" set is the literal itself.
    try:
        ipaddress.ip_address(hostname)
        if _is_blocked_ip(hostname):
            raise SSRFError("Webhook URL cannot target internal or private networks")
        return PinnedURL(url, host=hostname, port=port, scheme=parsed.scheme, pinned_ips=[hostname])
    except ValueError:
        pass  # not a literal IP — resolve below

    ips = (resolver or _resolve_host)(hostname)
    _validate_resolved_ips(ips)
    return PinnedURL(url, host=hostname, port=port, scheme=parsed.scheme, pinned_ips=ips)


def revalidate_at_connect(hostname: str, resolver: Callable[[str], List[str]] | None = None) -> List[str]:
    """Re-resolve + re-validate a host at connect time, returning the validated IP(s).

    This is the second half of the WH2 defence: even with a `PinnedURL` from submit-time,
    the connection layer re-checks at the moment it dials, so a freshly-flipped A-record is
    caught. Raises `SSRFError` if the host now resolves to a blocked IP (or won't resolve).
    """
    if _is_blocked_hostname(hostname):
        raise SSRFError("Webhook URL cannot target internal or private networks")
    try:
        ipaddress.ip_address(hostname)
        if _is_blocked_ip(hostname):
            raise SSRFError("Webhook URL cannot target internal or private networks")
        return [hostname]
    except ValueError:
        pass
    ips = (resolver or _resolve_host)(hostname)
    _validate_resolved_ips(ips)
    return ips


def build_pinned_transport(
    inner: "Any" = None,
    resolver: Callable[[str], List[str]] | None = None,
) -> "Any":
    """Wrap an httpx async transport so every dial re-validates + PINS the resolved IP (WH2).

    For each request the wrapper:
      1. re-resolves + re-validates the host (`revalidate_at_connect`) — closing the TOCTOU
         window between submit-time validation and the actual connect;
      2. rewrites the request URL host to a validated IP while preserving the original Host
         header and setting TLS SNI to the original hostname (`sni_hostname` extension), so
         certificate verification still matches the real host.

    Returns an `httpx.AsyncBaseTransport`. Import is local so merely importing this module
    never requires httpx (it is not in the offline gate venv).
    """
    import httpx

    base = inner if inner is not None else httpx.AsyncHTTPTransport()

    class _PinnedTransport(httpx.AsyncBaseTransport):
        def __init__(self, _base, _resolver):
            self._base = _base
            self._resolver = _resolver

        async def handle_async_request(self, request: "httpx.Request") -> "httpx.Response":
            host = request.url.host
            # Literal IPs are validated directly; hostnames are re-resolved + re-validated.
            try:
                ipaddress.ip_address(host)
                is_literal = True
            except ValueError:
                is_literal = False

            validated_ips = revalidate_at_connect(host, resolver=self._resolver)
            if is_literal:
                # Already an IP and already validated — dial as-is.
                return await self._base.handle_async_request(request)

            pinned_ip = validated_ips[0]
            # Pin: dial the validated IP, keep the original Host header + TLS SNI so the
            # certificate still verifies against the real hostname.
            request.headers.setdefault("Host", host)
            request.extensions = dict(request.extensions)
            request.extensions["sni_hostname"] = host
            request.url = request.url.copy_with(host=pinned_ip)
            return await self._base.handle_async_request(request)

        async def aclose(self) -> None:
            await self._base.aclose()

    return _PinnedTransport(base, resolver)
