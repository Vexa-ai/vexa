"""Every webhook delivery is held to the outbound-url.v1 guard, whenever its URL was saved.

Shared address space (100.64/10), documentation and benchmarking addresses are refused like every
other internal target; ``VEXA_WEBHOOK_STORED_URL_POLICY`` is not read, so setting it changes nothing.
"""
from __future__ import annotations

import httpx
import pytest

from meeting_api.webhooks import WebhookSink, build_envelope
from meeting_api.webhooks.ssrf import SSRFError, build_pinned_transport

_PUBLIC = lambda host: ["93.184.216.34"]   # noqa: E731
EVENTS = {"meeting.completed": True}


@pytest.fixture(params=[None, "v012"], ids=["unset", "old-setting-v012"])
def old_setting(request, monkeypatch):
    if request.param:
        monkeypatch.setenv("VEXA_WEBHOOK_STORED_URL_POLICY", request.param)
    else:
        monkeypatch.delenv("VEXA_WEBHOOK_STORED_URL_POLICY", raising=False)


async def _deliver(receiver, url, resolver=_PUBLIC, scope="per-client"):
    sink = WebhookSink(transport=receiver, resolver=resolver)
    env = build_envelope("meeting.completed", {"meeting": {"id": 1}})
    kwargs = {"events_config": EVENTS} if scope == "per-client" else {}
    return await sink.deliver(url, env, "s", scope=scope, **kwargs)


_V012_DELIVERED = [
    "http://100.64.1.5/hook",        # shared address space (CGNAT; Tailscale)
    "https://100.127.255.1/hook",
    "http://198.18.0.7/hook",        # benchmarking
    "http://203.0.113.10/hook",      # documentation
    "http://192.0.2.44/hook",
]


_SHARED_AND_DOCUMENTATION = [
    "http://100.64.1.5/hook",        # shared address space (CGNAT; Tailscale)
    "https://100.127.255.1/hook",
    "http://198.18.0.7/hook",        # benchmarking
    "http://203.0.113.10/hook",      # documentation
    "http://192.0.2.44/hook",
]


@pytest.mark.parametrize("url", _SHARED_AND_DOCUMENTATION)
async def test_shared_and_documentation_ranges_are_blocked(receiver, old_setting, url):
    result = await _deliver(receiver, url)
    assert result.status == "blocked", result
    assert receiver.received == []


async def test_a_name_resolving_into_cgnat_is_blocked(receiver, old_setting):
    result = await _deliver(receiver, "https://tailnet-hook.example.com/x", resolver=lambda h: ["100.80.1.2"])
    assert result.status == "blocked"


_STILL_REFUSED = [
    "http://localhost/hook", "http://127.0.0.1/hook", "http://10.0.0.5/hook", "http://172.16.0.1/hook",
    "http://192.168.1.10/hook", "http://169.254.169.254/latest", "http://100.100.100.200/latest",
    "http://[::1]/hook", "http://[::ffff:127.0.0.1]/hook", "http://[::ffff:a9fe:a9fe]/",
    "http://[64:ff9b::a9fe:a9fe]/", "http://[2002:7f00:1::1]/", "http://[::ffff:100.100.100.200]/",
    "http://2130706433/", "http://0x7f.0.0.1/", "http://240.0.0.1/", "http://0.0.0.0/",
    "http://redis/hook", "http://internal-receiver/hook", "http://a.localhost/",
    "http://metadata.google.internal/", "ftp://example.com/hook",
]


@pytest.mark.parametrize("url", _STILL_REFUSED)
async def test_nothing_internal_is_reachable(receiver, old_setting, url):
    result = await _deliver(receiver, url)
    assert result.status == "blocked", url
    assert receiver.received == []


async def test_a_public_url_is_delivered(receiver, old_setting):
    result = await _deliver(receiver, "https://hooks.example.com/x")
    assert result.status != "blocked", result
    assert len(receiver.received) == 1


@pytest.mark.parametrize("resolved,dials", [("93.184.216.34", True), ("100.64.0.9", False),
                                            ("127.0.0.1", False), ("::ffff:169.254.169.254", False)])
async def test_the_connection_is_pinned_and_rechecked(old_setting, resolved, dials):
    dialled = []

    class _Recorder(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            dialled.append(str(request.url))
            return httpx.Response(200, request=request)

    transport = build_pinned_transport(_Recorder(), resolver=lambda h: [resolved])
    async with httpx.AsyncClient(transport=transport) as c:
        if dials:
            assert (await c.post("https://hook.example.com/x", content=b"{}")).status_code == 200
        else:
            with pytest.raises(SSRFError):
                await c.post("https://hook.example.com/x", content=b"{}")
    assert bool(dialled) is dials
