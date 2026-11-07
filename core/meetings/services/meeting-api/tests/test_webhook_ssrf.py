"""O-MTG-2 eval (SSRF) — the URL-guard blocks private/internal targets.

Asserts: localhost / loopback / link-local (incl. cloud metadata) / private CIDRs /
internal Docker hostnames / non-http schemes are BLOCKED; public targets pass; a
WebhookSink delivery to a blocked URL returns `blocked` and never touches the transport.
"""
from __future__ import annotations

import pytest

from meeting_api.webhooks import (
    SSRFError,
    WebhookSink,
    build_envelope,
    validate_webhook_url,
)

# Resolver stubs so the guard is deterministic + offline.
_LOOPBACK = lambda host: ["127.0.0.1"]      # noqa: E731 — DNS rebinding to loopback
_PUBLIC = lambda host: ["93.184.216.34"]    # noqa: E731 — a public IP


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/hook",
        "http://localhost:8080/hook",
        "https://127.0.0.1/hook",
        "http://127.0.0.1:9000/x",
        "http://10.0.0.5/hook",            # private
        "http://172.16.0.1/hook",          # private
        "http://192.168.1.10/hook",        # private
        "http://169.254.169.254/latest",   # cloud metadata (link-local)
        "https://[::1]/hook",              # ipv6 loopback
        "http://redis/hook",               # internal Docker service
        "http://meeting-api/internal",     # internal Docker service
        "http://metadata.google.internal/", # cloud metadata hostname
        "ftp://example.com/hook",          # non-http scheme
        "file:///etc/passwd",              # non-http scheme
    ],
)
def test_blocked_urls(url):
    with pytest.raises(SSRFError):
        # literal-IP / blocked-hostname / bad-scheme cases never reach the resolver;
        # a public-looking name that rebinds to loopback is caught via the resolver.
        validate_webhook_url(url, resolver=_LOOPBACK)


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example.com/vexa",
        "http://api.customer.io/webhooks/123",
        "https://93.184.216.34/hook",  # literal public IP
    ],
)
def test_allowed_urls(url):
    # WH2: the guard returns a PinnedURL (connection-safe handle); .url is the original URL.
    out = validate_webhook_url(url, resolver=_PUBLIC)
    assert out.url == url
    assert out.pinned_ips, "a valid URL must carry the resolved+validated pinned IP(s)"


def test_dns_rebinding_to_private_blocked():
    """A public-looking hostname that RESOLVES to a private IP is blocked (anti-rebinding)."""
    with pytest.raises(SSRFError):
        validate_webhook_url("https://evil.example.com/hook", resolver=lambda h: ["10.1.2.3"])


def test_unresolvable_host_blocked():
    with pytest.raises(SSRFError):
        validate_webhook_url("https://nope.invalid/hook", resolver=lambda h: [])


async def test_sink_blocks_ssrf_without_touching_transport(receiver):
    """A blocked URL short-circuits in the sink — the transport is never called."""
    sink = WebhookSink(transport=receiver, resolver=_LOOPBACK)
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    result = await sink.deliver("http://localhost/hook", env, "s", events_config={"meeting.completed": True})
    assert result.status == "blocked"
    assert receiver.received == []


# ── every form an internal IPv4 address can be written in ─────────────────────────────────────────
# Each literal reaches 127.0.0.1, 10.0.0.1 or the cloud metadata address 169.254.169.254 when a
# socket is dialled at it. The guard checks what an address REACHES, not how it is written.

_INTERNAL_FORMS = [
    "[::ffff:127.0.0.1]",                 # IPv4-mapped
    "[::ffff:a9fe:a9fe]",                 # IPv4-mapped, hex
    "[::ffff:10.0.0.1]",
    "[::127.0.0.1]",                      # IPv4-compatible
    "[::a9fe:a9fe]",
    "[::ffff:0:a9fe:a9fe]",               # IPv4-translated
    "[2002:7f00:1::1]",                   # 6to4 around 127.0.0.1
    "[2002:a9fe:a9fe::]",                 # 6to4 around the metadata address
    "[2001:0:4136:e378:8000:63bf:3fff:fdd2]",  # Teredo
    "[64:ff9b::7f00:1]",                  # NAT64 well-known prefix
    "[64:ff9b::a9fe:a9fe]",
    "[64:ff9b:1::a00:1]",                 # local-use NAT64
    "[2a00:1450::5efe:a9fe:a9fe]",        # ISATAP interface identifier
    "[fe80::1%25eth0]",                   # link-local with a zone
    "[fd00:ec2::254]",                    # cloud metadata (IPv6)
    "[::]", "[::1]",
    "2130706433",                         # 127.0.0.1 as one decimal number
    "0x7f.0.0.1",                         # hex part
    "0177.0.0.1",                         # octal part
    "127.1",                              # short form
    "100.100.100.200",                    # shared address space (a cloud metadata address)
    "198.18.0.1", "192.0.0.192", "240.0.0.1", "0.0.0.0",
    "localhost.", "a.localhost", "metadata.google.internal.", "instance-data",
    "internal-receiver",                  # a single label: only this deployment resolves it
]


@pytest.mark.parametrize("host", _INTERNAL_FORMS)
def test_every_form_of_an_internal_address_is_refused(host):
    with pytest.raises(SSRFError):
        validate_webhook_url(f"https://{host}/hook", resolver=_PUBLIC)


@pytest.mark.parametrize("resolved", [
    "::ffff:127.0.0.1", "64:ff9b::a9fe:a9fe", "2002:a9fe:a9fe::1", "::10.0.0.1", "fd00:ec2::254",
])
def test_a_name_resolving_to_any_form_of_an_internal_address_is_refused(resolved):
    with pytest.raises(SSRFError):
        validate_webhook_url("https://hooks.example.com/hook", resolver=lambda h: ["93.184.216.34", resolved])


@pytest.mark.parametrize("host", [
    "[::ffff:93.184.216.34]", "[2002:5db8:d822::1]", "[64:ff9b::5db8:d822]", "[2606:4700::1111]",
])
def test_a_public_destination_in_a_transition_form_is_allowed(host):
    assert validate_webhook_url(f"https://{host}/hook", resolver=_PUBLIC).pinned_ips


@pytest.mark.parametrize("resolved", ["64:ff9b::7f00:1", "::ffff:169.254.169.254", "2002:0a00:0001::"])
async def test_a_connect_time_rebind_to_a_transition_form_never_dials(resolved):
    import httpx

    from meeting_api.webhooks.ssrf import build_pinned_transport

    dialled = []

    class _Recorder(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            dialled.append(request)
            return httpx.Response(200, request=request)

    async with httpx.AsyncClient(transport=build_pinned_transport(_Recorder(), resolver=lambda h: [resolved])) as c:
        with pytest.raises(SSRFError):
            await c.post("https://rebind.example.com/hook", content=b"{}")
    assert dialled == []
