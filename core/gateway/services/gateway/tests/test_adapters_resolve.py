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

from gateway.adapters import AdminApiAuthorizer, HttpxDownstreamClient, build_auth_and_downstream
from gateway.ports import AuthUnavailable

ADMIN = "http://admin-api:8001"


def test_build_wires_separate_pools():
    """#495 acceptance A1 — the GENUINE negative control (redis-free). The production wiring uses
    TWO distinct httpx clients, so forwarding load cannot starve validation. Reverting the fix (one
    shared client passed to both authorizer and downstream) turns this RED — unlike the await-level
    isolation test below, this one exercises the real composition seam (build_auth_and_downstream)."""
    authorizer, downstream = build_auth_and_downstream(ADMIN, "http://meeting-api:8080")
    assert authorizer._client is not downstream._client, "auth and forward must use separate clients/pools"
    # And distinct pool sizing / timeouts (the auth pool is its own, shorter-timeout budget).
    assert authorizer._client is not None and downstream._client is not None
    assert authorizer._client.timeout != downstream._client.timeout


def test_build_wires_a_dedicated_unbounded_read_stream_client():
    """#795 — the SSE relay leg gets its OWN client, with ``read=None``.

    The buffered client's bounded read timeout is correct for request/response legs and WRONG for
    a relayed stream: an MCP ``GET /mcp`` SSE stream sends its headers and then idles until the
    server has something to push, so a bounded read timeout fires on a perfectly healthy stream —
    and ``httpx.TimeoutException ⊂ httpx.RequestError``, which is how the deployed gateway turned
    it into ``503 MCP service unavailable``. Its own pool also keeps hours-long streams from
    consuming buffered-forward slots. Collapsing the two clients back into one turns this RED."""
    _, downstream = build_auth_and_downstream(ADMIN, "http://meeting-api:8080")
    assert downstream._stream_client is not downstream._client, "the stream leg needs its own pool"
    stream_timeout = downstream._stream_client.timeout
    assert stream_timeout.read is None, "a silent stream is a healthy stream — no read deadline"
    # …while the legs that CAN hang stay bounded.
    assert stream_timeout.connect is not None and stream_timeout.write is not None
    assert stream_timeout.pool is not None
    assert downstream._client.timeout.read is not None, "the buffered leg keeps its read deadline"


def test_downstream_client_defaults_to_one_client_when_no_stream_client_given():
    """The adapter stays constructible with a single client (the conformance/unit wiring)."""
    client = httpx.AsyncClient()
    downstream = HttpxDownstreamClient(client)
    assert downstream._stream_client is client


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


@pytest.mark.parametrize("code", [301, 302, 307])
async def test_resolve_redirect_is_no_verdict_unavailable(code):
    """A 3xx is NOT a 'key is invalid' verdict — treat it as no-verdict → 503, never 401 (finding 4)."""
    auth = _authorizer(lambda req: httpx.Response(code, headers={"location": "/elsewhere"}))
    with pytest.raises(AuthUnavailable):
        await auth.resolve("vxa_bot_ok")


async def test_resolve_unparseable_200_body_raises_unavailable():
    """A 200 with a non-JSON body is not a verdict either — no-verdict → 503, not an unhandled 500
    (finding 3: resp.json() used to run outside the guard)."""
    auth = _authorizer(lambda req: httpx.Response(200, text="<html>not json</html>"))
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
