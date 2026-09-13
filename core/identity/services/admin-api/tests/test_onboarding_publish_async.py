"""A8 — the onboarding publish is AWAITED, and a hanging flows never stalls identity.

THE DEFECT. `events.publish` was `urllib.request.urlopen(req, timeout=2.0)`, called from inside
`async def create_user`. That is a BLOCKING call on the event loop, so the 2 s bound was per
REQUEST and the stall was per PROCESS: while one sign-in waited on flows, every other request
this admin-api serves — `/internal/validate`, which the gateway asks on every single API call, and
`/health` — waited with it. And urllib's `timeout` is a SOCKET timeout: name resolution is not
covered by it, so an unresolvable `VEXA_FLOWS_API_URL` was not bounded at 2 s or at anything.

Offline, stdlib + httpx only — no docker, unlike test_onboarding_event.py's stack-backed suite.
This is the publisher, not the route; the route's wiring is pinned there.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import socket
import time

import httpx
import pytest

from admin_api.app import events as events_mod


@pytest.fixture()
def wire(monkeypatch):
    """Every request the publisher put on the wire, with no socket involved."""
    calls = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url),
                      "headers": {k.lower(): v for k, v in request.headers.items()},
                      "body": json.loads(request.content.decode())})
        return httpx.Response(202)

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    # `events.publish` does `import httpx` at call time — there is deliberately no module-level
    # client, so patching the module attribute is exactly what the publisher will see.
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return calls


def test_the_publisher_is_a_coroutine_function():
    """The property the fix turns on: a blocking publisher cannot be awaited, and an awaited one
    cannot block. Asserted directly so a later edit back to `urllib` fails here first."""
    assert inspect.iscoroutinefunction(events_mod.publish)


def test_it_puts_the_fact_on_the_wire_under_the_operator_header(monkeypatch, wire):
    monkeypatch.setenv(events_mod.FLOWS_API_URL_ENV, "http://flows.example")
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", "op-key")

    landed = asyncio.run(events_mod.publish(
        events_mod.EVENT_ONBOARDING_COMPLETED, "onboarding-7",
        events_mod.onboarding_refs(7, events_mod.NO_ORG, events_mod.DEFAULT_SEAT)))

    assert landed is True
    assert wire[0]["url"] == "http://flows.example/events"
    assert wire[0]["body"] == {
        "event_type": "onboarding.completed", "source_event_id": "onboarding-7",
        # `refs`, never `subject_refs` — F142's own regression, kept pinned here too.
        "refs": {"subject": "7", "org": "", "seat": "member"},
    }
    assert wire[0]["headers"].get("x-flows-operator-key") == "op-key"


def test_no_flows_domain_opens_no_client_at_all(monkeypatch):
    """Unset is a PROFILE. A deployment with no flows domain onboards people and makes no call."""
    monkeypatch.delenv(events_mod.FLOWS_API_URL_ENV, raising=False)
    for legacy in events_mod.FLOWS_API_URL_ENV_DEPRECATED:
        monkeypatch.delenv(legacy, raising=False)

    def _no_client(*a, **kw):
        raise AssertionError("publish opened an HTTP client with no flows domain configured")

    monkeypatch.setattr(httpx, "AsyncClient", _no_client)
    assert asyncio.run(events_mod.publish("onboarding.completed", "onboarding-1", {})) is False


def _dead_listener():
    """A socket that ACCEPTS into the backlog and answers nothing, ever — the shape a hung flows
    presents. A closed port would be refused instantly and prove nothing about the bound."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return server


def test_a_hanging_flows_does_not_block_the_event_loop(monkeypatch):
    """The whole point. Pre-fix the heartbeat below could not tick until the publish returned, so
    its elapsed time was the PUBLISH's timeout rather than its own ~0.2 s of sleeps."""
    server = _dead_listener()
    host, port = server.getsockname()
    monkeypatch.setenv(events_mod.FLOWS_API_URL_ENV, f"http://{host}:{port}")
    try:
        async def scenario():
            ticks = []

            async def heartbeat():
                for _ in range(10):
                    await asyncio.sleep(0.02)
                    ticks.append(time.monotonic())

            publishing = asyncio.create_task(
                events_mod.publish("onboarding.completed", "onboarding-1", {}, timeout=1.5))
            started = time.monotonic()
            await heartbeat()
            heartbeat_took = time.monotonic() - started
            in_flight = not publishing.done()
            return ticks, heartbeat_took, in_flight, await publishing

        ticks, heartbeat_took, in_flight, landed = asyncio.run(scenario())
        assert len(ticks) == 10
        assert heartbeat_took < 1.0, (
            f"the loop was blocked for {heartbeat_took:.2f}s by a hanging publish — "
            "10 × 20ms of other work should take ~0.2s")
        assert in_flight, "the publish should still be in flight, bounded by ITS timeout"
        assert landed is False
    finally:
        server.close()


def test_the_hanging_publish_returns_within_its_own_bound(monkeypatch):
    server = _dead_listener()
    host, port = server.getsockname()
    monkeypatch.setenv(events_mod.FLOWS_API_URL_ENV, f"http://{host}:{port}")
    try:
        started = time.monotonic()
        landed = asyncio.run(events_mod.publish(
            "onboarding.completed", "onboarding-1", {}, timeout=0.25))
        elapsed = time.monotonic() - started
        assert landed is False
        assert elapsed < 3.0, f"publish took {elapsed:.2f}s against a 0.25s bound"
    finally:
        server.close()


def test_a_publish_that_cannot_connect_is_swallowed(monkeypatch):
    monkeypatch.setenv(events_mod.FLOWS_API_URL_ENV, "http://127.0.0.1:9")  # nothing listens
    assert asyncio.run(events_mod.publish("onboarding.completed", "onboarding-1", {})) is False
