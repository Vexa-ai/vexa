"""Both callbacks fail closed: with no credential wired, a callback moves nothing.

The bot's lifecycle callback is admitted by the session's MeetingToken (key: ``token_secret``) and
the runtime's workload callback by the runtime's signature (key: ``runtime_callback_token``). An app
built without a key used to admit every callback on that door. Now it refuses (401) and logs the
refusal, unless the in-process harness asks for open doors in so many words (``open_callbacks``).
The production entrypoint never passes the flag.
"""
from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

LIFECYCLE = "/bots/internal/callback/lifecycle"
RUNTIME = "/runtime/callback"


def _seeded(status="requested"):
    repo = InMemoryMeetingRepo()

    async def seed():
        m = await repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="m1", data={})
        await repo.create_session(meeting_id=m["id"], session_uid="sess-uid")
        await repo.set_bot_container(meeting_id=m["id"], bot_container_id="wl-1")
        repo.set_status(m["id"], status)
        return m

    return repo, asyncio.run(seed())


def _status(repo, meeting):
    return repo._meetings[meeting["id"]]["status"]


def _events(out: str, name: str) -> list[dict]:
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return [e for e in lines if e.get("event") == name]


def test_no_meeting_token_key_refuses_every_lifecycle_callback(capsys):
    repo, meeting = _seeded()
    client = TestClient(create_app(meeting_repo=repo, runtime=FakeRuntimeClient()))
    r = client.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": "joining"})
    assert r.status_code == 401
    assert _status(repo, meeting) == "requested"
    refused = _events(capsys.readouterr().out, "lifecycle_event_rejected")
    assert refused and refused[-1]["fields"]["reason"] == "no_credential_configured"


def test_no_runtime_key_refuses_every_runtime_callback(capsys):
    repo, meeting = _seeded()
    client = TestClient(create_app(meeting_repo=repo, runtime=FakeRuntimeClient()))
    r = client.post(RUNTIME, json={"workloadId": "wl-1", "state": "failed"})
    assert r.status_code == 401
    assert _status(repo, meeting) == "requested"
    refused = _events(capsys.readouterr().out, "runtime_callback_rejected")
    assert refused and refused[-1]["fields"]["reason"] == "no_credential_configured"


def test_an_empty_key_is_no_key():
    repo, meeting = _seeded()
    client = TestClient(create_app(meeting_repo=repo, runtime=FakeRuntimeClient(),
                                   token_secret="", runtime_callback_token=""))
    assert client.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": "joining"}).status_code == 401
    assert client.post(RUNTIME, json={"workloadId": "wl-1", "state": "failed"}).status_code == 401
    assert _status(repo, meeting) == "requested"


def test_the_internal_tier_is_admitted_without_a_meeting_token_key():
    repo, meeting = _seeded()
    client = TestClient(create_app(meeting_repo=repo, runtime=FakeRuntimeClient(),
                                   internal_secret="internal-secret-0123456789abcdef"))
    r = client.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": "joining"},
                    headers={"x-internal-secret": "internal-secret-0123456789abcdef"})
    assert r.status_code == 200, r.text


def test_the_harness_opens_both_doors_only_when_it_says_so():
    repo, meeting = _seeded()
    client = TestClient(create_app(meeting_repo=repo, runtime=FakeRuntimeClient(), open_callbacks=True))
    assert client.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": "joining"}).status_code == 200
    assert client.post(RUNTIME, json={"workloadId": "wl-1", "state": "failed"}).status_code == 200


def test_a_wired_key_is_checked_even_with_open_doors():
    repo, meeting = _seeded()
    client = TestClient(create_app(meeting_repo=repo, runtime=FakeRuntimeClient(), open_callbacks=True,
                                   token_secret="admin-key", runtime_callback_token="runtime-key-0123456789"))
    assert client.post(LIFECYCLE, json={"connection_id": "sess-uid", "status": "joining"}).status_code == 401
    assert client.post(RUNTIME, json={"workloadId": "wl-1", "state": "failed"}).status_code == 401
    assert _status(repo, meeting) == "requested"


def test_the_production_entrypoint_never_opens_the_doors():
    import inspect

    import meeting_api.__main__ as entry

    assert "open_callbacks" not in inspect.getsource(entry)
