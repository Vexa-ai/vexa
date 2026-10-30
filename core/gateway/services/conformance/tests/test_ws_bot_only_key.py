"""A `bot`-only key streams its OWN meeting over `/ws` and nobody else's, end to end through the
gateway and meeting-api.

`{bot}` is the hosted webapp's key (and what many API users hold). One key does everything its owner
may do (#1922, one_key_scope): `GET /transcripts/{platform}/{native_meeting_id}` takes `bot` or `tx`,
and a `/ws` subscription is held to the same row, so the default `bot`-only key subscribes and its
ownership check decides each meeting exactly as for a `tx` key. A `browser`-only key gets
`insufficient_scope` with what to do instead; `VEXA_WS_SCOPE_MODE` is not read by either service.

This drives the SHIPPED pieces together, offline:
  the gateway's `/ws` loop (`gateway.run_multiplex`)
  → its production authorizer (`gateway.adapters.AdminApiAuthorizer`): `/internal/validate`, then the
    signed `/ws/authorize-subscribe` hop (gateway-identity.v1)
  → the unified meeting-api (`meeting_api.create_app` with the gateway's public key), whose
    route-scope gate and ownership check decide.
Only `/internal/validate` is a stub: it answers what identity answers for the key's scopes.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from gateway.adapters import AdminApiAuthorizer
from gateway_conformance.ws_harness import FakeRedis, FakeWebSocket
from gateway import run_multiplex
from meeting_api import create_app, identity_token
from meeting_api.collector.fakes import InMemoryTranscriptStore

KEY = identity_token.generate_signing_key()
API_KEY = "vxa_bot_only_key"
MINE = {"platform": "google_meet", "native_id": "abc-defg-hij"}
THEIRS = {"platform": "google_meet", "native_id": "xyz-wxyz-xyz"}


def _validate_app(scopes):
    app = FastAPI()

    @app.post("/internal/validate")
    async def validate():
        return {"user_id": 7, "email": "seven@example.com", "scopes": list(scopes), "max_concurrent": 3}

    return app


class _Route(httpx.AsyncBaseTransport):
    """One client, two in-process services, routed by host — as the gateway's single client is."""

    def __init__(self, admin, meetings):
        self._by_host = {"admin-api": httpx.ASGITransport(app=admin),
                         "meeting-api": httpx.ASGITransport(app=meetings)}

    async def handle_async_request(self, request):
        return await self._by_host[request.url.host].handle_async_request(request)


def _authorizer(scopes):
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij",
                       meeting_id=42, status="active")
    store.seed_meeting(user_id=8, platform="google_meet", native_meeting_id="xyz-wxyz-xyz",
                       meeting_id=43, status="active")
    meetings = create_app(transcript_store=store, identity_key=KEY.public_key(),
                          internal_secret="conformance-internal", open_callbacks=True)
    client = httpx.AsyncClient(transport=_Route(_validate_app(scopes), meetings))
    return AdminApiAuthorizer(client, "http://admin-api", "http://meeting-api", identity_key=KEY)


async def _subscribe(scopes, meeting, publish_to=None):
    """Subscribe with a key holding ``scopes``; then, when ``publish_to`` names a meeting row, publish
    one transcript frame on its channel and wait for it to come through (or not)."""
    ws = FakeWebSocket(inbound=[
        '{"action": "subscribe", "meetings": [%s]}' % (
            '{"platform": "%s", "native_id": "%s"}' % (meeting["platform"], meeting["native_id"]))
    ], api_key=API_KEY, close_when_drained=False)
    redis = FakeRedis()
    task = asyncio.ensure_future(run_multiplex(ws, _authorizer(scopes), redis))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if any(f.get("type") in ("subscribed", "error") for f in ws.sent):
            break
    if publish_to is not None:
        channel = f"tc:meeting:{publish_to}:mutable"
        if any(f.get("type") == "subscribed" and f.get("meetings") for f in ws.sent):
            await redis.wait_for_subscriber(channel, timeout=2)
        await redis.publish(channel, '{"type": "transcript", "speaker": "Ann", "confirmed": [], "pending": []}')
        for _ in range(50):
            await asyncio.sleep(0.01)
            if any(f.get("type") == "transcript" for f in ws.sent):
                break
    ws.disconnect()
    await asyncio.wait_for(task, timeout=5)
    return ws


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "conformance-internal")


@pytest.mark.parametrize("old_setting", [None, "legacy", "strict"])
async def test_a_bot_only_key_subscribes_to_its_own_meeting_and_receives_its_frames(monkeypatch, old_setting):
    if old_setting:
        monkeypatch.setenv("VEXA_WS_SCOPE_MODE", old_setting)
    ws = await _subscribe(["bot"], MINE, publish_to=42)
    assert not [f for f in ws.sent if f.get("type") == "error"], ws.sent
    assert {"type": "subscribed", "meetings": [MINE]} in ws.sent, ws.sent
    assert any(f.get("type") == "transcript" and f.get("speaker") == "Ann" for f in ws.sent), ws.sent


async def test_a_bot_only_key_is_refused_a_meeting_it_does_not_own():
    ws = await _subscribe(["bot"], THEIRS, publish_to=43)
    assert not any(f.get("type") == "subscribed" and f.get("meetings") for f in ws.sent), ws.sent
    assert not any(f.get("type") == "transcript" for f in ws.sent), ws.sent


async def test_a_browser_only_key_is_refused_with_what_to_do():
    ws = await _subscribe(["browser"], MINE)
    refused = [f for f in ws.sent if f.get("error") == "insufficient_scope"]
    assert refused, ws.sent
    assert refused[0]["changed_in"] == "0.13.4"
    assert "bot or tx" in refused[0]["do_instead"] and "scope" in refused[0]["do_instead"]
    assert refused[0]["docs"] == "https://docs.vexa.ai/changes-0-13-4-auth"
    assert not any(f.get("type") == "subscribed" and f.get("meetings") for f in ws.sent)


async def test_a_transcript_key_subscribes_to_its_own_meeting():
    ws = await _subscribe(["tx"], MINE)
    assert not [f for f in ws.sent if f.get("type") == "error"], ws.sent
    assert any(f.get("type") == "subscribed" and f.get("meetings") for f in ws.sent), ws.sent


async def test_a_transcript_key_is_refused_a_meeting_it_may_not_read():
    ws = await _subscribe(["tx"], THEIRS)
    assert not any(f.get("type") == "subscribed" and f.get("meetings") for f in ws.sent), ws.sent
