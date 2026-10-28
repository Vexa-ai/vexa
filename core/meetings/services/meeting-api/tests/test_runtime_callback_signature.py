"""A /runtime/callback is the runtime's only when it carries the runtime's signature, made now, for
this door.

The runtime signs each RuntimeEvent it delivers with an HMAC over the signing time, the delivery URL
and the event, keyed from the runtime caller credential both services hold. An unsigned, forged,
stale, misdirected or replayed callback moves no meeting. The vector is the one the runtime's signer
is pinned against (core/runtime/tests/test_caller_auth.py).
"""
from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api import runtime_signature
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

TOKEN = "runtime-caller-token-for-tests-0123456789abcdef"
EVENT = {"workloadId": "mtg-1-abcdef12", "state": "stopped", "at": "2026-10-09T00:00:00+00:00",
         "exitCode": 0, "stopReason": "completed"}
URL = "http://meeting-api:8080/runtime/callback"
TIMESTAMP = 1791504000
SIGNATURE = "t=1791504000,v2=f1ed1283cda387b2dbe249719eb9546925955eba96f7829baee866ab264c41c0"
DOOR = "http://testserver/runtime/callback"            # the URL the test client reaches the door on
OTHER = "another-token-of-similar-length-0123456789ab"


def test_the_verifier_matches_the_runtimes_signer():
    assert runtime_signature.sign(TOKEN, EVENT, URL, TIMESTAMP) == SIGNATURE
    assert runtime_signature.verify(TOKEN, EVENT, SIGNATURE, URL, now=TIMESTAMP)
    assert not runtime_signature.verify(TOKEN, {**EVENT, "workloadId": "mtg-2-abcdef12"}, SIGNATURE, URL, now=TIMESTAMP)
    assert not runtime_signature.verify(TOKEN, EVENT, SIGNATURE, "http://elsewhere/runtime/callback", now=TIMESTAMP)
    assert not runtime_signature.verify("", EVENT, SIGNATURE, URL, now=TIMESTAMP)
    assert not runtime_signature.verify(TOKEN, EVENT, "", URL, now=TIMESTAMP)


def test_a_signature_outside_the_window_is_stale():
    assert runtime_signature.check(TOKEN, EVENT, SIGNATURE, URL, now=TIMESTAMP + runtime_signature.SKEW_SEC) is None
    assert runtime_signature.check(TOKEN, EVENT, SIGNATURE, URL, now=TIMESTAMP + runtime_signature.SKEW_SEC + 1) == "stale"
    assert runtime_signature.check(TOKEN, EVENT, SIGNATURE, URL, now=TIMESTAMP - runtime_signature.SKEW_SEC - 1) == "stale"


def test_the_old_form_is_refused():
    """The v1 form (an HMAC over the event alone) is no longer a signature."""
    assert runtime_signature.check(TOKEN, EVENT, "v1=" + "0" * 64, URL) == "unsigned"


def _signed(event=EVENT, url=DOOR, token=TOKEN, at=None):
    return {runtime_signature.HEADER: runtime_signature.sign(token, event, url, int(time.time() if at is None else at))}


def _client(repo=None):
    return TestClient(create_app(meeting_repo=repo or InMemoryMeetingRepo(), runtime=FakeRuntimeClient(),
                                 runtime_callback_token=TOKEN))


def test_an_unsigned_forged_stale_misdirected_or_replayed_callback_is_refused():
    client = _client()
    assert client.post("/runtime/callback", json=EVENT).status_code == 401
    assert client.post("/runtime/callback", json=EVENT, headers=_signed(token=OTHER)).status_code == 401
    assert client.post("/runtime/callback", json=EVENT, headers=_signed(at=time.time() - 3600)).status_code == 401
    assert client.post("/runtime/callback", json=EVENT, headers=_signed(url=URL)).status_code == 401
    headers = _signed()
    assert client.post("/runtime/callback", json=EVENT, headers=headers).status_code == 200
    assert client.post("/runtime/callback", json=EVENT, headers=headers).status_code == 401   # the same one again


def test_a_signed_callback_is_accepted():
    r = _client().post("/runtime/callback", json=EVENT, headers=_signed())
    assert r.status_code == 200 and r.json()["status"] == "accepted"


def test_the_replay_guard_shares_its_memory_through_redis():
    """Each meeting-api replica asks Redis (SET NX with the window as expiry), so a callback replayed
    to another replica is refused too."""
    class FakeRedis:
        def __init__(self):
            self.keys = {}

        async def set(self, key, value, nx=False, ex=None):
            if nx and key in self.keys:
                return None
            self.keys[key] = (value, ex)
            return True

    shared = FakeRedis()
    a, b = runtime_signature.ReplayGuard(shared), runtime_signature.ReplayGuard(shared)
    assert asyncio.run(a.first(SIGNATURE)) is True
    assert asyncio.run(b.first(SIGNATURE)) is False
    (_, ex), = shared.keys.values()
    assert ex == 2 * runtime_signature.SKEW_SEC


def test_a_refused_callback_moves_no_meeting():
    """The 401 is not only an answer: a refused terminal callback leaves the meeting it names exactly
    where it was, and the same event signed now ends it."""
    repo = InMemoryMeetingRepo()

    async def seed():
        m = await repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="m1", data={})
        await repo.create_session(meeting_id=m["id"], session_uid="sess-uid")
        await repo.set_bot_container(meeting_id=m["id"], bot_container_id=EVENT["workloadId"])
        repo.set_status(m["id"], "joining")
        return m

    meeting = asyncio.run(seed())
    client = _client(repo)
    for headers in ({}, _signed(token=OTHER), {runtime_signature.HEADER: "v1=" + "0" * 64},
                    _signed(at=time.time() - 3600), _signed(url=URL)):
        assert client.post("/runtime/callback", json=EVENT, headers=headers).status_code == 401
        assert repo._meetings[meeting["id"]]["status"] == "joining"
    assert client.post("/runtime/callback", json=EVENT, headers=_signed()).status_code == 200
    assert repo._meetings[meeting["id"]]["status"] == "failed"
