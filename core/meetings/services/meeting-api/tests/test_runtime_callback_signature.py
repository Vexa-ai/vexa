"""A /runtime/callback is the runtime's only when it carries the runtime's signature.

The runtime signs each RuntimeEvent it delivers with an HMAC keyed from the runtime caller credential
both services hold. An unsigned or forged callback moves no meeting. The vector is the one the
runtime's signer is pinned against (core/runtime/tests/test_caller_auth.py).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api import runtime_signature
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

TOKEN = "runtime-caller-token-for-tests-0123456789abcdef"
EVENT = {"workloadId": "mtg-1-abcdef12", "state": "stopped", "at": "2026-10-09T00:00:00+00:00",
         "exitCode": 0, "stopReason": "completed"}
SIGNATURE = "v1=2d3b0c1312be4c69a6144019dbd480b5c0f21c905b818e496c3f98cd30a2e286"


def test_the_verifier_matches_the_runtimes_signer():
    assert runtime_signature.sign(TOKEN, EVENT) == SIGNATURE
    assert runtime_signature.verify(TOKEN, EVENT, SIGNATURE)
    assert not runtime_signature.verify(TOKEN, {**EVENT, "workloadId": "mtg-2-abcdef12"}, SIGNATURE)
    assert not runtime_signature.verify("", EVENT, SIGNATURE)
    assert not runtime_signature.verify(TOKEN, EVENT, "")


def _client():
    return TestClient(create_app(meeting_repo=InMemoryMeetingRepo(), runtime=FakeRuntimeClient(),
                                 runtime_callback_token=TOKEN))


def test_an_unsigned_or_forged_callback_is_refused():
    client = _client()
    assert client.post("/runtime/callback", json=EVENT).status_code == 401
    forged = runtime_signature.sign("another-token-of-similar-length-0123456789ab", EVENT)
    assert client.post("/runtime/callback", json=EVENT,
                       headers={runtime_signature.HEADER: forged}).status_code == 401


def test_a_signed_callback_is_accepted():
    r = _client().post("/runtime/callback", json=EVENT, headers={runtime_signature.HEADER: SIGNATURE})
    assert r.status_code == 200 and r.json()["status"] == "accepted"
