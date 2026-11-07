"""An open `/ws` subscription is RE-AUTHORIZED while it streams (P20, ws.v1 `subscription_revoked`).

The owner removes a reader mid-meeting: the reader's open socket must stop receiving that meeting's
transcript, with an explicit frame, while the socket itself and every other subscription stay up.
Driven directly against the shipped multiplex with the in-process fakes; the re-check interval is
shortened so the test runs in milliseconds.
"""
from __future__ import annotations

import asyncio
import json

import gateway.multiplex as mux
from gateway.multiplex import _run_multiplex
from gateway.ports import AuthUnavailable
from conftest import FakeAuthorizer, FakeRedis
from test_multiplex import API_KEY, SUBSCRIBE, _WS


async def _settle(n=30):
    for _ in range(n):
        await asyncio.sleep(0)


async def _wait_reauth():
    await asyncio.sleep(mux.WS_REAUTH_INTERVAL_SEC * 3)
    await _settle()


def _fast(monkeypatch):
    monkeypatch.setattr(mux, "WS_REAUTH_INTERVAL_SEC", 0.01)


async def test_a_removed_reader_stops_receiving_and_is_told(monkeypatch):
    _fast(monkeypatch)
    auth_map = {("google_meet", "room-1"): {"meeting_id": 42, "user_id": 8}}
    auth, redis = FakeAuthorizer(valid_key=API_KEY, auth_map=auth_map), FakeRedis()
    ws = _WS(inbound=[SUBSCRIBE], api_key=API_KEY, close_when_drained=False)
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    await _settle()
    await redis.publish("tc:meeting:42:mutable", json.dumps({"type": "transcription_segment", "text": "before"}))
    await _settle()
    assert any(f.get("text") == "before" for f in ws.sent)

    auth_map.clear()                       # the owner removes this reader
    await _wait_reauth()
    revoked = [f for f in ws.sent if f.get("error") == "subscription_revoked"]
    assert revoked and revoked[0]["details"] == {"platform": "google_meet", "native_id": "room-1"}

    await redis.publish("tc:meeting:42:mutable", json.dumps({"type": "transcription_segment", "text": "after"}))
    await _settle()
    assert not any(f.get("text") == "after" for f in ws.sent), "no transcript after removal"
    assert ws.close_code is None, "the socket itself stays open"
    ws.disconnect()
    await task


async def test_a_reader_who_still_has_access_keeps_streaming(monkeypatch):
    _fast(monkeypatch)
    auth = FakeAuthorizer(valid_key=API_KEY, auth_map={("google_meet", "room-1"): {"meeting_id": 42, "user_id": 8}})
    redis = FakeRedis()
    ws = _WS(inbound=[SUBSCRIBE], api_key=API_KEY, close_when_drained=False)
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    await _wait_reauth()
    await redis.publish("tc:meeting:42:mutable", json.dumps({"type": "transcription_segment", "text": "still"}))
    await _settle()
    assert any(f.get("text") == "still" for f in ws.sent)
    assert not any(f.get("error") == "subscription_revoked" for f in ws.sent)
    ws.disconnect()
    await task


async def test_an_ended_workspace_membership_stops_that_workspaces_status_channel(monkeypatch):
    _fast(monkeypatch)
    user = {"user_id": 8, "scopes": ["tx"], "max_concurrent": 3, "workspaces": ["ws-team"]}
    auth, redis = FakeAuthorizer(user=user, valid_key=API_KEY), FakeRedis()
    ws = _WS(inbound=[], api_key=API_KEY, close_when_drained=False)
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    await _settle()
    await redis.publish("w:ws-team:meetings", json.dumps({"type": "meeting.status", "status": "active"}))
    await _settle()
    assert any(f.get("status") == "active" for f in ws.sent)

    auth._user["workspaces"] = []           # removed from the workspace
    await _wait_reauth()
    await redis.publish("w:ws-team:meetings", json.dumps({"type": "meeting.status", "status": "completed"}))
    await _settle()
    assert not any(f.get("status") == "completed" for f in ws.sent)
    ws.disconnect()
    await task


async def test_a_key_that_stops_resolving_closes_the_socket(monkeypatch):
    _fast(monkeypatch)
    auth, redis = FakeAuthorizer(valid_key=API_KEY), FakeRedis()
    ws = _WS(inbound=[], api_key=API_KEY, close_when_drained=False)
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    await _settle()
    auth._valid_key = "rotated"
    await _wait_reauth()
    assert ws.close_code == 4401 and any(f.get("error") == "invalid_api_key" for f in ws.sent)
    ws.disconnect()
    await task


class _FlakyAuthorizer(FakeAuthorizer):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.down = False

    async def authorize_subscribe(self, api_key, meetings):
        if self.down:
            return {"authorized": [], "errors": ["authorization_call_failed:boom"]}
        return await super().authorize_subscribe(api_key, meetings)


async def test_one_failed_hop_does_not_revoke_but_a_sustained_outage_fails_closed(monkeypatch):
    _fast(monkeypatch)
    auth = _FlakyAuthorizer(valid_key=API_KEY, auth_map={("google_meet", "room-1"): {"meeting_id": 42, "user_id": 8}})
    redis = FakeRedis()
    ws = _WS(inbound=[SUBSCRIBE], api_key=API_KEY, close_when_drained=False)
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    await _settle()
    auth.down = True
    await _wait_reauth()
    await _wait_reauth()
    errs = [f.get("error") for f in ws.sent if f.get("type") == "error"]
    assert "authorization_call_failed" in errs and "subscription_revoked" not in errs
    await redis.publish("tc:meeting:42:mutable", json.dumps({"type": "transcription_segment", "text": "x"}))
    await _settle()
    assert not any(f.get("text") == "x" for f in ws.sent), "fail closed after a sustained outage"
    ws.disconnect()
    await task


async def test_a_reader_removed_from_one_row_is_cut_even_if_the_same_code_reaches_another(monkeypatch):
    """R1801-1: the re-check compares the ROW being streamed. After removal the authorizer still
    answers for the same (platform, native) — with a DIFFERENT row the reader can reach (a recurring
    meeting code, or their own bot in the same call). The stream of the removed row must stop."""
    _fast(monkeypatch)
    auth_map = {("google_meet", "room-1"): {"meeting_id": 42, "user_id": 8}}
    auth, redis = FakeAuthorizer(valid_key=API_KEY, auth_map=auth_map), FakeRedis()
    ws = _WS(inbound=[SUBSCRIBE], api_key=API_KEY, close_when_drained=False)
    task = asyncio.ensure_future(_run_multiplex(ws, auth, redis))
    await _settle()
    auth_map[("google_meet", "room-1")] = {"meeting_id": 77, "user_id": 8}   # same code, other row
    await _wait_reauth()
    assert any(f.get("error") == "subscription_revoked" for f in ws.sent)
    await redis.publish("tc:meeting:42:mutable", json.dumps({"type": "transcription_segment", "text": "leak"}))
    await _settle()
    assert not any(f.get("text") == "leak" for f in ws.sent), "the removed row's transcript must stop"
    ws.disconnect()
    await task
