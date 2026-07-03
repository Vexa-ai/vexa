"""Standing regression guards for the v0.12 GATEWAY public edge (``gateway.create_app`` +
``gateway.run_multiplex``).

The gateway is the only door to the backend, so its failure modes are its contract. After the
edge was HARDENED (auth fail-closed + header anti-spoofing, upstream-fault mapping to 502/504,
and a /ws control loop that survives every malformed/abusive frame), this suite pins the CORRECT,
now-fixed behavior so a future change that regresses it goes RED.

Everything drives the SHIPPED app over the existing conformance fakes — no docker, no real
backend, no sockets:

  * REST is driven via ``TestClient(build_gateway())`` (and, for the upstream-fault legs, via
    ``gateway.create_app`` injected with a fault-raising ``DownstreamClient`` whose ``.request()``
    raises ``httpx.ConnectError`` / ``httpx.TimeoutException``).
  * /ws is driven via the ``WSMultiplexHarness`` (FakeWebSocket + FakeRedis + the port
    ``Authorizer``) — the same harness ``test_ws_protocol.py`` uses.

Test groups:
  1. REST auth + anti-spoofing      — missing/invalid/empty key fail-closed; client identity headers stripped
  2. REST proxy faults              — 5xx verbatim; ConnectError→502; TimeoutException→504; non-JSON body; oversized body
  3. WS auth                        — missing/empty key; valid ?api_key=; authz non-200 + authz raise (socket survives)
  4. WS protocol abuse              — invalid_json (incl. non-object JSON); unknown/missing action; bad subscribe/unsubscribe; hostile redis payload
  5. WS lifecycle                   — ping/pong; subscribe→deliver→unsubscribe; disconnect mid-stream cleans up fan-in
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import create_app
from gateway_conformance.contracts import assert_ws_conforms
from gateway_conformance.fake_meeting_api import VALID_API_KEY, build_fake_downstream
from gateway_conformance.gateway_app import (
    _ConformanceAuthorizer,
    _ConformanceDownstream,
    _NullRedis,
    build_gateway,
)
from gateway_conformance.ws_harness import (
    FakeAuthorizer,
    FakeRedis,
    FakeWebSocket,
    WSMultiplexHarness,
)

AUTH = {"x-api-key": VALID_API_KEY}

# The /ws subscribe maps (platform, native_id) → backing meeting_id + user_id, as the authorizer does.
AUTH_MAP = {("google_meet", "g5-ws-gate-room"): {"meeting_id": 42, "user_id": 7}}
WS_API_KEY = "vxa_test_conformance_key"
SUBSCRIBE = {"action": "subscribe",
             "meetings": [{"platform": "google_meet", "native_id": "g5-ws-gate-room"}]}


# ---------------------------------------------------------------------------
# Shared fakes for the REST upstream-fault legs.
# ---------------------------------------------------------------------------
class _RaisingDownstream:
    """A ``DownstreamClient`` whose ``.request()`` always raises the given transport error —
    models an unreachable / slow upstream so the gateway's 502/504 mapping is exercised."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def request(self, method, url, *, headers=None, params=None, content=None):
        raise self._exc


class _StatusDownstream:
    """A ``DownstreamClient`` returning a canned status + body verbatim (5xx pass-through,
    non-JSON body, oversized body)."""

    def __init__(self, status: int, content: bytes, content_type: str = "application/json"):
        self._status = status
        self._content = content
        self._content_type = content_type

    async def request(self, method, url, *, headers=None, params=None, content=None):
        return httpx.Response(
            status_code=self._status,
            content=self._content,
            headers={"content-type": self._content_type},
        )


class _CapturingDownstream:
    """Captures the headers the gateway forwarded downstream, then returns a trivial 200 — lets
    a test assert the gateway-resolved identity was injected (and a spoofed one stripped)."""

    def __init__(self):
        self.seen_headers: dict | None = None

    async def request(self, method, url, *, headers=None, params=None, content=None):
        self.seen_headers = dict(headers or {})
        return httpx.Response(status_code=200, content=b'{"ok": true}',
                              headers={"content-type": "application/json"})


def _gateway_with_downstream(downstream) -> TestClient:
    """Build the SHIPPED gateway with the real conformance Authorizer (fake admin-api) but a
    custom DownstreamClient — so the auth hop is genuine and only the proxy target is faulted."""
    downstream_app = build_fake_downstream()
    transport = httpx.ASGITransport(app=downstream_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://downstream")
    app = create_app(_ConformanceAuthorizer(client), downstream, _NullRedis())
    return TestClient(app)


# ---------------------------------------------------------------------------
# Custom /ws Authorizer doubles (port-level) for the WS auth-error legs. The hardened
# run_multiplex must turn a downstream-authz fault into a protocol error frame WITHOUT
# crashing the socket.
# ---------------------------------------------------------------------------
class _ServiceErrorAuthorizer:
    """authorize_subscribe RETURNS a non-200-style result: nothing authorized, errors carried.
    The gateway must emit an `authorization_service_error` frame (NOT a misleading empty
    `subscribed` ack that hides the auth backend being down)."""

    async def resolve(self, api_key):
        return {"user_id": 7, "scopes": ["tx"], "max_concurrent": 1}

    async def authorize_subscribe(self, api_key, meetings):
        return {"authorized": [], "errors": ["authorization_service_error:503"]}


class _RaisingAuthorizer:
    """authorize_subscribe RAISES. The gateway must emit an `authorization_call_failed` frame and
    KEEP the socket alive (the loop continues; a later valid frame still works)."""

    async def resolve(self, api_key):
        return {"user_id": 7, "scopes": ["tx"], "max_concurrent": 1}

    async def authorize_subscribe(self, api_key, meetings):
        raise RuntimeError("collector unreachable")


def _ws_harness(inbound, api_key=WS_API_KEY, close_when_drained=True, authorizer=None):
    ws = FakeWebSocket(
        inbound=[json.dumps(m) if isinstance(m, dict) else m for m in inbound],
        api_key=api_key, close_when_drained=close_when_drained)
    return ws, WSMultiplexHarness(ws, FakeRedis(), authorizer or FakeAuthorizer(AUTH_MAP))


# ===========================================================================
# GROUP 1 — REST auth + anti-spoofing (fail-closed)
# ===========================================================================
def test_rest_missing_api_key_is_401():
    """No x-api-key → 401 before any downstream call (fail-closed)."""
    c = build_gateway()
    tc = TestClient(c)
    resp = tc.get("/bots/status", headers={})
    assert resp.status_code == 401
    assert resp.json().get("detail") == "Missing API key"


def test_rest_empty_api_key_is_401():
    """An EMPTY x-api-key is falsy → treated as missing → 401 (not forwarded as a real key)."""
    tc = TestClient(build_gateway())
    resp = tc.get("/bots/status", headers={"x-api-key": ""})
    assert resp.status_code == 401
    assert resp.json().get("detail") == "Missing API key"


def test_rest_invalid_api_key_is_401_or_403():
    """A present-but-bogus key does not validate → rejected (401 here; 401/403 family)."""
    tc = TestClient(build_gateway())
    resp = tc.get("/bots/status", headers={"x-api-key": "vxa_not_a_real_key"})
    assert resp.status_code in (401, 403)
    assert resp.json().get("detail") == "Invalid API key"


def test_rest_strips_spoofed_identity_headers_and_injects_resolved():
    """ANTI-SPOOFING: a client-sent x-user-id / x-user-scopes / x-user-limits / x-user-webhook-*
    is STRIPPED, and the gateway-resolved identity (from the validated key) is injected downstream.
    The downstream must see the RESOLVED id (7), never the spoofed one."""
    cap = _CapturingDownstream()
    tc = _gateway_with_downstream(cap)
    resp = tc.get("/bots/status", headers={
        **AUTH,
        "x-user-id": "999999",
        "x-user-scopes": "admin,root",
        "x-user-limits": "9999",
        "x-user-webhook-url": "https://evil.example/hook",
        "x-user-webhook-secret": "pwned",
        "x-user-webhook-events": "[\"*\"]",
    })
    assert resp.status_code == 200
    seen = cap.seen_headers
    assert seen is not None, "downstream was never called"
    # The gateway-resolved identity replaced the spoof.
    assert seen.get("x-user-id") == "7", f"spoofed x-user-id leaked: {seen.get('x-user-id')}"
    assert seen.get("x-user-id") != "999999"
    # The spoofed scopes/limits/webhook are replaced by the resolved user's values (VALID_USER).
    assert "admin" not in seen.get("x-user-scopes", "")
    assert "root" not in seen.get("x-user-scopes", "")
    assert seen.get("x-user-limits") == "3"  # VALID_USER max_concurrent, not the spoofed 9999
    # VALID_USER carries no webhook config → the spoofed webhook headers are gone entirely.
    assert "x-user-webhook-url" not in seen
    assert "x-user-webhook-secret" not in seen
    assert "x-user-webhook-events" not in seen


# ===========================================================================
# GROUP 2 — REST proxy faults (the edge must not leak a 500 for an upstream fault)
# ===========================================================================
def test_rest_upstream_5xx_passed_through_verbatim():
    """An upstream 5xx (a real downstream error, not a transport fault) is forwarded VERBATIM —
    same status + body — so the client sees the backend's own error, not a gateway rewrite."""
    body = b'{"detail": "meeting-api exploded"}'
    tc = _gateway_with_downstream(_StatusDownstream(503, body))
    resp = tc.get("/bots/status", headers=AUTH)
    assert resp.status_code == 503
    assert resp.content == body


def test_rest_connection_refused_maps_to_502():
    """An unreachable / connection-refused upstream (httpx.ConnectError, a RequestError) →
    502 Bad Gateway (NOT a leaked 500): the client can tell 'backend down' from 'gateway broke'."""
    tc = _gateway_with_downstream(_RaisingDownstream(httpx.ConnectError("connection refused")))
    resp = tc.get("/bots/status", headers=AUTH)
    assert resp.status_code == 502, f"expected 502, got {resp.status_code}: {resp.text}"
    assert "upstream" in resp.json().get("detail", "").lower()


def test_rest_timeout_maps_to_504():
    """A slow upstream (httpx.TimeoutException, a RequestError subclass caught first) →
    504 Gateway Timeout (a retryable signal), not a leaked 500."""
    tc = _gateway_with_downstream(_RaisingDownstream(httpx.TimeoutException("read timeout")))
    resp = tc.get("/bots/status", headers=AUTH)
    assert resp.status_code == 504, f"expected 504, got {resp.status_code}: {resp.text}"
    assert "timeout" in resp.json().get("detail", "").lower()


def test_rest_non_json_upstream_body_is_forwarded():
    """A non-JSON upstream body (e.g. an HTML error page, raw bytes) is forwarded raw — the
    gateway proxies bytes, it does not require the body to parse as JSON."""
    html = b"<html><body>502 Bad Gateway from nginx</body></html>"
    tc = _gateway_with_downstream(_StatusDownstream(200, html, content_type="text/html"))
    resp = tc.get("/bots/status", headers=AUTH)
    assert resp.status_code == 200
    assert resp.content == html
    assert "text/html" in resp.headers.get("content-type", "")


def test_rest_oversized_body_is_forwarded_ok():
    """A large upstream body is forwarded intact (no truncation, no size-based 500)."""
    big = b'{"blob": "' + b"x" * (2 * 1024 * 1024) + b'"}'  # ~2 MiB
    tc = _gateway_with_downstream(_StatusDownstream(200, big))
    resp = tc.get("/bots/status", headers=AUTH)
    assert resp.status_code == 200
    assert len(resp.content) == len(big)
    assert resp.content == big


# ===========================================================================
# GROUP 3 — WS auth
# ===========================================================================
async def test_ws_missing_api_key_header_and_query_closes_4401():
    """No x-api-key header AND no ?api_key= → missing_api_key error frame + close 4401."""
    ws = FakeWebSocket(inbound=[], api_key=None)
    h = WSMultiplexHarness(ws, FakeRedis(), FakeAuthorizer(AUTH_MAP))
    await h.run()
    assert ws.sent and ws.sent[0]["error"] == "missing_api_key"
    assert_ws_conforms("Error", ws.sent[0])
    assert ws.close_code == 4401


async def test_ws_empty_api_key_is_rejected():
    """An EMPTY api_key (falsy) is treated as missing → missing_api_key + close 4401."""
    ws = FakeWebSocket(inbound=[], api_key="")  # empty header value → not set on FakeWebSocket
    h = WSMultiplexHarness(ws, FakeRedis(), FakeAuthorizer(AUTH_MAP))
    await h.run()
    assert ws.sent and ws.sent[0]["error"] == "missing_api_key"
    assert_ws_conforms("Error", ws.sent[0])
    assert ws.close_code == 4401


async def test_ws_query_param_api_key_is_accepted():
    """A valid ?api_key= (no header) is accepted — the loop runs and acks a subscribe."""
    ws = FakeWebSocket(inbound=[json.dumps(SUBSCRIBE)], api_key=None)
    ws.query_params = {"api_key": WS_API_KEY}
    h = WSMultiplexHarness(ws, FakeRedis(), FakeAuthorizer(AUTH_MAP))
    await h.run()
    assert not any(f.get("error") == "missing_api_key" for f in ws.sent), f"frames={ws.sent}"
    acks = [f for f in ws.sent if f.get("type") == "subscribed"]
    assert acks, f"no subscribed ack with ?api_key=; frames={ws.sent}"
    assert_ws_conforms("Subscribed", acks[0])


async def test_ws_authz_service_error_yields_error_frame_not_empty_ack():
    """The downstream authorize hop carries errors and authorizes NOTHING (e.g. backend 5xx) →
    an `authorization_service_error` Error frame, NOT a misleading empty `subscribed` ack that
    hides the auth backend being down."""
    ws, h = _ws_harness([SUBSCRIBE], authorizer=_ServiceErrorAuthorizer())
    await h.run()
    errs = [f for f in ws.sent if f.get("type") == "error"]
    assert errs and errs[0]["error"] == "authorization_service_error", f"frames={ws.sent}"
    assert_ws_conforms("Error", errs[0])
    # Crucially: NO empty subscribed ack masquerading as success.
    assert not [f for f in ws.sent if f.get("type") == "subscribed"], \
        f"empty subscribed ack hid the auth error; frames={ws.sent}"


async def test_ws_authz_raise_yields_call_failed_and_socket_survives():
    """authorize_subscribe RAISES → an `authorization_call_failed` Error frame AND the socket
    SURVIVES: the loop continues, so a subsequent valid frame (ping) still gets a pong."""
    ws, h = _ws_harness([SUBSCRIBE, {"action": "ping"}], authorizer=_RaisingAuthorizer())
    await h.run()
    errs = [f for f in ws.sent if f.get("type") == "error"]
    assert errs and errs[0]["error"] == "authorization_call_failed", f"frames={ws.sent}"
    assert_ws_conforms("Error", errs[0])
    # The loop continued past the crash: the later ping was still serviced.
    assert any(f.get("type") == "pong" for f in ws.sent), \
        f"socket did not survive the authz raise — no pong after; frames={ws.sent}"


# ===========================================================================
# GROUP 4 — WS protocol abuse (socket must SURVIVE each; the loop continues)
# ===========================================================================
async def test_ws_malformed_json_yields_invalid_json_and_survives():
    """Malformed (non-JSON) input → invalid_json Error, then the loop continues and a valid
    ping is still answered."""
    ws, h = _ws_harness(["this-is-not-json{", {"action": "ping"}])
    await h.run()
    errs = [f for f in ws.sent if f.get("type") == "error"]
    assert errs and errs[0]["error"] == "invalid_json", f"frames={ws.sent}"
    assert_ws_conforms("Error", errs[0])
    assert any(f.get("type") == "pong" for f in ws.sent), f"loop died after invalid_json; {ws.sent}"


@pytest.mark.parametrize("payload", ["[1, 2, 3]", "42", '"x"', "null", "3.14", "true"])
async def test_ws_non_object_json_yields_invalid_json_not_crash(payload):
    """Syntactically-valid but NON-OBJECT JSON ([1,2,3], 42, "x", null, …) → invalid_json Error,
    NOT an AttributeError that kills the socket (a trivial public-edge DoS before the fix)."""
    ws, h = _ws_harness([payload, {"action": "ping"}])
    await h.run()
    errs = [f for f in ws.sent if f.get("type") == "error"]
    assert errs and errs[0]["error"] == "invalid_json", f"payload={payload!r} frames={ws.sent}"
    assert_ws_conforms("Error", errs[0])
    # The non-object payload did not crash the loop: the trailing ping still got a pong.
    assert any(f.get("type") == "pong" for f in ws.sent), \
        f"non-object JSON {payload!r} killed the socket; frames={ws.sent}"


async def test_ws_unknown_action_yields_error():
    """An unknown action → unknown_action Error frame (socket survives)."""
    ws, h = _ws_harness([{"action": "frobnicate"}, {"action": "ping"}])
    await h.run()
    errs = [f for f in ws.sent if f.get("type") == "error"]
    assert errs and errs[0]["error"] == "unknown_action", f"frames={ws.sent}"
    assert_ws_conforms("Error", errs[0])
    assert any(f.get("type") == "pong" for f in ws.sent), f"loop died; {ws.sent}"


async def test_ws_missing_action_yields_error():
    """A valid object with NO action key → unknown_action (action defaults to None)."""
    ws, h = _ws_harness([{"meetings": []}, {"action": "ping"}])
    await h.run()
    errs = [f for f in ws.sent if f.get("type") == "error"]
    assert errs and errs[0]["error"] == "unknown_action", f"frames={ws.sent}"
    assert_ws_conforms("Error", errs[0])
    assert any(f.get("type") == "pong" for f in ws.sent), f"loop died; {ws.sent}"


@pytest.mark.parametrize("meetings,case", [
    ("not-a-list", "non-list"),
    ([], "empty"),
    ([1, 2, 3], "junk-only-items"),
    ([{"platform": "", "native_id": ""}], "blank-fields"),
])
async def test_ws_invalid_subscribe_payload_yields_error(meetings, case):
    """subscribe with non-list / empty / junk-only / blank meetings → invalid_subscribe_payload
    Error (no subscribed ack, socket survives)."""
    ws, h = _ws_harness([{"action": "subscribe", "meetings": meetings}, {"action": "ping"}])
    await h.run()
    errs = [f for f in ws.sent if f.get("type") == "error"]
    assert errs and errs[0]["error"] == "invalid_subscribe_payload", f"case={case} frames={ws.sent}"
    assert_ws_conforms("Error", errs[0])
    assert not [f for f in ws.sent if f.get("type") == "subscribed"], f"case={case} frames={ws.sent}"
    assert any(f.get("type") == "pong" for f in ws.sent), f"case={case} loop died; {ws.sent}"


async def test_ws_duplicate_subscribe_is_idempotent_and_survives():
    """A duplicate subscribe (same meeting twice) is handled idempotently — both acked, no crash,
    the loop survives (the second registers no second fan-in but still acks)."""
    ws, h = _ws_harness([SUBSCRIBE, SUBSCRIBE, {"action": "ping"}])
    await h.run()
    acks = [f for f in ws.sent if f.get("type") == "subscribed"]
    assert len(acks) == 2, f"both subscribes should ack; frames={ws.sent}"
    for a in acks:
        assert_ws_conforms("Subscribed", a)
    assert any(f.get("type") == "pong" for f in ws.sent), f"loop died after dup subscribe; {ws.sent}"


async def test_ws_unsubscribe_never_subscribed_yields_error():
    """unsubscribe for a meeting that was never subscribed → invalid_unsubscribe_payload Error
    (errors and no unsubscribed); the loop survives."""
    ws, h = _ws_harness([
        {"action": "unsubscribe",
         "meetings": [{"platform": "zoom", "native_id": "never-subscribed"}]},
        {"action": "ping"}])
    await h.run()
    errs = [f for f in ws.sent if f.get("type") == "error"]
    assert errs and errs[0]["error"] == "invalid_unsubscribe_payload", f"frames={ws.sent}"
    assert_ws_conforms("Error", errs[0])
    assert any(f.get("type") == "pong" for f in ws.sent), f"loop died; {ws.sent}"


async def test_ws_hostile_large_redis_payload_forwarded_raw_without_crash():
    """A subscribed channel receives a HOSTILE, oversized, non-conforming redis payload — the
    fan-in forwards it RAW to the socket without parsing/validating, and the socket survives
    (the gateway is a dumb pipe for redis payloads; it does not crash on junk)."""
    ws, h = _ws_harness([SUBSCRIBE], close_when_drained=False)
    task = asyncio.ensure_future(h.run())
    hostile = {"type": "transcription_segment", "junk": "z" * (512 * 1024),
               "nested": {"deep": [1, 2, 3, {"x": None}]}, "evil": "</script>\x00"}
    await h.deliver("tc:meeting:42:mutable", hostile)
    ws.disconnect()
    await task
    # Forwarded raw: the exact hostile payload reached the socket (raw frame), no crash.
    assert any(isinstance(r, str) and '"junk"' in r for r in ws.sent_raw), \
        f"hostile payload not forwarded raw; raw={[r[:80] for r in ws.sent_raw]}"


# ===========================================================================
# GROUP 5 — WS lifecycle
# ===========================================================================
async def test_ws_ping_pong_keepalive():
    """ping → pong (keepalive)."""
    ws, h = _ws_harness([{"action": "ping"}])
    await h.run()
    assert any(f.get("type") == "pong" for f in ws.sent), f"no pong; frames={ws.sent}"


async def test_ws_subscribe_deliver_unsubscribe_stops_fanin():
    """subscribe → a delivered redis payload is forwarded → unsubscribe → the fan-in STOPS
    (a payload published after the unsubscribe is NOT forwarded). Validates the Subscribed +
    Unsubscribed acks against the sealed ws.v1 $defs."""
    ws, h = _ws_harness([SUBSCRIBE], close_when_drained=False)
    task = asyncio.ensure_future(h.run())

    acks = [f for f in ws.sent if f.get("type") == "subscribed"]
    # subscribe ack may not be present until the loop turns; wait for it deterministically.
    await ws.wait_for_sent(1)
    acks = [f for f in ws.sent if f.get("type") == "subscribed"]
    assert acks, f"no subscribed ack; frames={ws.sent}"
    assert_ws_conforms("Subscribed", acks[0])

    # While subscribed, a delivered frame is forwarded.
    await h.deliver("tc:meeting:42:mutable", {
        "type": "transcription_segment", "speaker": "Alice", "text": "before unsubscribe",
        "start_time": "2026-06-21T10:00:00Z", "end_time": 1.0, "language": "en",
        "completed": True, "segment_id": "ch-0:1:a"})
    assert [f for f in ws.sent if f.get("type") == "transcription_segment"], \
        f"frame not forwarded while subscribed; {ws.sent}"

    # unsubscribe and wait for the unsubscribed ack (sent only after fan_in is cancelled).
    before_unsub = len(ws.sent)
    ws._inbound.put_nowait(json.dumps({
        "action": "unsubscribe",
        "meetings": [{"platform": "google_meet", "native_id": "g5-ws-gate-room"}]}))
    await ws.wait_for_sent(before_unsub + 1)
    unacks = [f for f in ws.sent if f.get("type") == "unsubscribed"]
    assert unacks, f"no unsubscribed ack; frames={ws.sent}"
    assert_ws_conforms("Unsubscribed", unacks[0])

    # fan-in STOPPED: a payload published now is dropped (no subscriber).
    await h.deliver("tc:meeting:42:mutable", {
        "type": "transcription_segment", "speaker": "Alice", "text": "after unsubscribe",
        "start_time": "2026-06-21T10:00:01Z", "end_time": 2.0, "language": "en",
        "completed": True, "segment_id": "ch-0:1:b"}, expect_forward=False)
    assert not [f for f in ws.sent if f.get("type") == "transcription_segment"
                and f.get("text") == "after unsubscribe"], \
        f"frame forwarded AFTER unsubscribe — fan-in did not stop; {ws.sent}"

    ws.disconnect()
    await task


async def test_ws_client_disconnect_midstream_cleans_up_fanin():
    """A client that subscribes then disconnects mid-stream → the fan-in task(s) are cancelled in
    the loop's `finally`, leaving NO leaked task (the channel has no live subscriber afterward)."""
    redis = FakeRedis()
    ws = FakeWebSocket(inbound=[json.dumps(SUBSCRIBE)], api_key=WS_API_KEY,
                       close_when_drained=False)
    h = WSMultiplexHarness(ws, redis, FakeAuthorizer(AUTH_MAP))
    task = asyncio.ensure_future(h.run())

    # Wait until the fan-in has registered (subscription live), then disconnect mid-stream.
    await redis.wait_for_subscriber("tc:meeting:42:mutable")
    assert redis.has_subscriber("tc:meeting:42:mutable")
    ws.disconnect()
    await task  # run_multiplex returns → its `finally` cancelled every sub_task

    # No leak: let the cancelled fan_in's finally run (its `pubsub.unsubscribe` removes the queue),
    # then assert the channel has no live subscriber.
    for _ in range(5):
        await asyncio.sleep(0)
    assert not redis.has_subscriber("tc:meeting:42:mutable"), \
        "fan-in task leaked — channel still has a subscriber after disconnect"
    # No pending tasks reference the harness fan_in either.
    leaked = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert not leaked, f"leaked tasks after disconnect: {leaked}"
