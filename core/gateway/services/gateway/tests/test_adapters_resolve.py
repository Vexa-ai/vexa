"""#495 — the root-cause behavior at the adapter: ``AdminApiAuthorizer.resolve`` must translate
the admin-api validation hop into a THREE-way verdict, not the old two-way (200 → ok, anything
else → None-that-becomes-401):

  * 200                    → the resolved user dict            (valid key)
  * 4xx (401/403/404/422)  → ``None``                          (genuinely invalid key → app 401)
  * 5xx  / transport error / timeout → raise ``AuthUnavailable`` (no verdict → app 503, retry)

The last row is the fix: under load/outage the hop times out, and reporting that as an invalid
key mass-401'd valid keys in production (#483/#495). These use httpx.MockTransport so no network
and no redis are needed (build_production_app is exercised at container boot / conformance).
"""
import asyncio

import pytest

httpx = pytest.importorskip("httpx")

from gateway.adapters import AdminApiAuthorizer, HttpxDownstreamClient
from gateway.ports import AuthUnavailable

ADMIN = "http://admin-api:8001"


def _authorizer(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AdminApiAuthorizer(client, ADMIN, "http://meeting-api:8080")


async def test_resolve_200_returns_user():
    auth = _authorizer(lambda req: httpx.Response(200, json={"user_id": 7, "scopes": ["bot"]}))
    assert (await auth.resolve("vxa_bot_ok"))["user_id"] == 7


@pytest.mark.parametrize("code", [401, 403, 404, 422])
async def test_resolve_client_error_is_invalid_key_none(code):
    """A definitive client-error answer from admin-api: the key is genuinely invalid → None → 401."""
    auth = _authorizer(lambda req: httpx.Response(code, json={"detail": "nope"}))
    assert await auth.resolve("vxa_bot_bad") is None


@pytest.mark.parametrize("code", [500, 502, 503])
async def test_resolve_server_error_raises_unavailable(code):
    """admin-api answered a FAULT (5xx): no verdict on the key → AuthUnavailable → 503, not 401."""
    auth = _authorizer(lambda req: httpx.Response(code, text="boom"))
    with pytest.raises(AuthUnavailable):
        await auth.resolve("vxa_bot_ok")


def _raise(exc):
    def _handler(req):
        raise exc
    return _handler


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("refused"),
    httpx.ReadTimeout("slow"),
    httpx.PoolTimeout("pool exhausted"),  # the exact #495 mechanism: shared pool starved
])
async def test_resolve_transport_failure_raises_unavailable(exc):
    """Transport failure / timeout (incl. PoolTimeout — the shared-pool starvation itself):
    no verdict → AuthUnavailable → 503, never a 401 that blames a valid key."""
    auth = _authorizer(_raise(exc))
    with pytest.raises(AuthUnavailable):
        await auth.resolve("vxa_bot_ok")


async def test_auth_isolated_from_slow_downstream():
    """#495 acceptance A1 (unit arm) — validation is decoupled from a slow forward.

    A forward request to meeting-api HANGS in flight; a concurrent validation on the authorizer's
    OWN client resolves promptly and is never blocked behind it. (httpx.MockTransport does not
    model real connection-pool exhaustion — that PoolTimeout→503 mapping is proven directly in
    test_resolve_transport_failure_raises_unavailable, and the end-to-end pool-saturation
    red→green is the issue's A4 LIVE burst eval. This arm proves the structural decoupling: the
    authorizer and the downstream forward do not share a client.)"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_forward(request):
        started.set()
        await release.wait()  # a slow meeting-api holding the forward connection open
        return httpx.Response(200, json={"ok": True})

    forward_client = httpx.AsyncClient(transport=httpx.MockTransport(slow_forward))
    auth = _authorizer(lambda req: httpx.Response(200, json={"user_id": 7, "scopes": ["bot"]}))
    downstream = HttpxDownstreamClient(forward_client)
    assert auth._client is not downstream._client, "authorizer and forward must not share a client"

    hang = asyncio.create_task(downstream.request("GET", "http://meeting-api:8080/meetings"))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    user = await asyncio.wait_for(auth.resolve("vxa_bot_ok"), timeout=1.0)
    assert user["user_id"] == 7
    release.set()
    await hang
    await forward_client.aclose()
