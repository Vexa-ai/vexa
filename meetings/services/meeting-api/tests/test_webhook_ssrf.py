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
    assert validate_webhook_url(url, resolver=_PUBLIC) == url


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
