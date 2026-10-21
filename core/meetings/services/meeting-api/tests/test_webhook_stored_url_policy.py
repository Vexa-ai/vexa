"""VEXA_WEBHOOK_STORED_URL_POLICY — the default is the 0.13 guard; ``v012`` is an operator's opt-in.

0.12.28 delivered to shared-address-space (100.64/10), documentation and benchmarking addresses;
0.13's guard refuses them. The default, ``strict``, is the 0.13 guard alone. Under the explicit
opt-in ``v012`` a stored URL in those ranges is still delivered, and nothing 0.12 refused, nothing
that reaches this host or its cloud in any written form, becomes reachable.
"""
from __future__ import annotations

import httpx
import pytest

from meeting_api.webhooks import WebhookSink, build_envelope
from meeting_api.webhooks import stored_url_policy as sup
from meeting_api.webhooks.ssrf import SSRFError

_PUBLIC = lambda host: ["93.184.216.34"]   # noqa: E731
EVENTS = {"meeting.completed": True}


@pytest.fixture(autouse=True)
def _default_policy(monkeypatch):
    monkeypatch.delenv(sup.POLICY_ENV, raising=False)


@pytest.fixture
def v012(monkeypatch):
    monkeypatch.setenv(sup.POLICY_ENV, "v012")


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


@pytest.mark.parametrize("url", _V012_DELIVERED)
async def test_a_stored_url_0_12_delivered_to_is_delivered_under_the_v012_opt_in(receiver, v012, url):
    result = await _deliver(receiver, url)
    assert result.status != "blocked", result
    assert len(receiver.received) == 1


@pytest.mark.parametrize("mode", ["strict", None])
@pytest.mark.parametrize("url", _V012_DELIVERED)
async def test_strict_and_the_default_block_them(receiver, monkeypatch, url, mode):
    """10-B: with nothing configured, CGNAT and TEST-NET stay refused (the wc3r guard)."""
    if mode:
        monkeypatch.setenv(sup.POLICY_ENV, mode)
    result = await _deliver(receiver, url)
    assert result.status == "blocked"
    assert receiver.received == []


async def test_a_name_resolving_into_a_0_12_range_is_delivered_under_v012(receiver, v012):
    result = await _deliver(receiver, "https://tailnet-hook.example.com/x", resolver=lambda h: ["100.80.1.2"])
    assert result.status != "blocked"


async def test_a_name_resolving_into_cgnat_is_blocked_by_default(receiver):
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
async def test_nothing_internal_becomes_reachable_under_v012(receiver, v012, url):
    result = await _deliver(receiver, url)
    assert result.status == "blocked", url
    assert receiver.received == []


async def test_a_name_resolving_to_metadata_or_loopback_stays_refused(receiver, v012):
    for resolved in (["100.100.100.200"], ["127.0.0.1"], ["100.64.0.1", "10.0.0.1"]):
        result = await _deliver(receiver, "https://hooks.example.com/x", resolver=lambda h, r=resolved: r)
        assert result.status == "blocked", resolved


async def test_system_webhooks_never_use_the_stored_url_policy(receiver, v012):
    result = await _deliver(receiver, "http://100.64.1.5/hook", scope="system")
    assert result.status == "blocked"


def test_unset_or_unknown_policy_reads_as_strict():
    assert sup.policy({sup.POLICY_ENV: "V012"}) == "v012"
    assert sup.policy({sup.POLICY_ENV: "nope"}) == "strict"
    assert sup.policy({}) == "strict"


@pytest.mark.parametrize("resolved,dials", [("100.64.0.9", True), ("127.0.0.1", False),
                                            ("100.100.100.200", False), ("::ffff:169.254.169.254", False)])
async def test_the_connection_is_still_pinned_and_rechecked(v012, resolved, dials):
    dialled = []

    class _Recorder(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            dialled.append(str(request.url))
            return httpx.Response(200, request=request)

    transport = sup.pinned_transport_for(_Recorder(), resolver=lambda h: [resolved])
    async with httpx.AsyncClient(transport=transport) as c:
        if dials:
            assert (await c.post("https://hook.example.com/x", content=b"{}")).status_code == 200
        else:
            with pytest.raises(SSRFError):
                await c.post("https://hook.example.com/x", content=b"{}")
    assert bool(dialled) is dials
