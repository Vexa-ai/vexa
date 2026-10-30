"""Whether a chat line posted — ``POST …/chat`` answers a ``request_id`` and ``GET …/chat/{request_id}``
reads the outcome the bot reported.

  * POST records the send as ``queued`` before the act goes out, puts the same id on the acts.v1
    ``chat_send``, and answers it; a send nothing received leaves no record.
  * The bot's ``chat_send_result`` (transcript.v1 ``ChatSendResult``, signed on the
    ``transcription_segments`` stream) settles it: ``posted``, or ``failed`` with the bot's reason
    (a reason outside ``ChatSendFailure`` is recorded as ``unknown``).
  * A result changes nothing for an id that was never sent, for a send into another meeting (the
    entry's meeting is the one its token names), or for a send already settled.
  * GET answers the sender only, under the platform and native id the send was made to: anything else
    is 404. A send still ``queued`` ``UNCONFIRMED_AFTER_SEC`` after it went out reads ``unconfirmed``.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from meeting_api import create_app, identity_token
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.chat_delivery import (
    CHAT_SEND_TTL_SEC, UNCONFIRMED_AFTER_SEC, ChatDeliveryLedger, chat_send_key,
)
from meeting_api.collector import consume_segments
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore

from _segment_auth import admin_token, signed_xadd, token_for  # noqa: F401

USER = 7
H = {"x-user-id": str(USER)}
PLAT, NATIVE = "google_meet", "abc-defg-hij"


def _contract(rel: str) -> dict:
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_file():
            return json.loads((parent / rel).read_text())
    raise FileNotFoundError(rel)


def _validator(rel: str, ref: str) -> Draft202012Validator:
    root = _contract(rel)
    uri = root.get("$id") or "urn:api.v1"
    resource = Resource.from_contents(root, default_specification=DRAFT202012)
    return Draft202012Validator({"$ref": f"{uri}#{ref}"},
                                registry=Registry().with_resource(uri=uri, resource=resource))


ACT = _validator("core/meetings/contracts/acts.v1/acts.schema.json", "/$defs/Act")
RESULT = _validator("core/meetings/contracts/transcript.v1/transcript.schema.json", "/$defs/ChatSendResult")
SEND_RESPONSE = _validator("core/gateway/contracts/api.v1/api.schema.json", "/components/schemas/ChatSendResponse")
SEND_STATUS = _validator("core/gateway/contracts/api.v1/api.schema.json", "/components/schemas/ChatSendStatus")


class _ListeningRedis:
    """The command bus as meeting-api holds it — one Redis client — with the meeting's bot subscribed:
    PUBLISH answers one receiver. Everything else is the shared fakeredis client the collector reads."""

    def __init__(self, client, receivers: int = 1) -> None:
        self._client = client
        self.receivers = receivers
        self.published: list[tuple[str, str]] = []

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return self.receivers


def _seed(repo, *, user=USER, native=NATIVE):
    m = asyncio.run(repo.create_meeting(user_id=user, platform=PLAT, native_meeting_id=native, data={}))
    sid = f"sess-{m['id']}"
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=sid))
    asyncio.run(repo.update_meeting_status(session_uid=sid, status="active"))
    return m


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def rig(redis):
    repo, pub = InMemoryMeetingRepo(), _ListeningRedis(redis)
    client = TestClient(create_app(meeting_repo=repo, command_publisher=pub))
    return client, repo, pub


def _send(client, text="hello room", headers=H, native=NATIVE):
    r = client.post(f"/bots/{PLAT}/{native}/chat", headers=headers, json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def _status(client, request_id, headers=H, native=NATIVE, platform=PLAT):
    return client.get(f"/bots/{platform}/{native}/chat/{request_id}", headers=headers)


def _result(meeting_id, request_id, *, posted, reason=None) -> dict:
    data = {"type": "chat_send_result", "meeting_id": meeting_id, "id": request_id, "posted": posted}
    if reason is not None:
        data["reason"] = reason
    return data


def _bot_reports(redis, data: dict, *, token=None) -> None:
    """The bot's signed XADD, then one collector pass over the stream."""
    async def run():
        bus = FakeRedisBus(redis)
        await signed_xadd(bus, data, token=token)
        await consume_segments(InMemoryTranscriptStore(), bus)
    asyncio.run(run())


# ── POST answers a request id, carries it on the act, records the send ──────────────────────────


def test_post_answers_a_request_id_and_puts_it_on_the_act(rig):
    client, repo, pub = rig
    m = _seed(repo)
    body = _send(client)
    SEND_RESPONSE.validate(body)
    assert body["status"] == "queued" and body["meeting_id"] == m["id"]
    act = json.loads(pub.published[-1][1])
    ACT.validate(act)
    assert act == {"action": "chat_send", "text": "hello room", "id": body["request_id"]}


def test_a_fresh_send_reads_queued(rig):
    client, repo, _pub = rig
    m = _seed(repo)
    rid = _send(client)["request_id"]
    r = _status(client, rid)
    assert r.status_code == 200, r.text
    SEND_STATUS.validate(r.json())
    assert r.json() == {"request_id": rid, "status": "queued", "meeting_id": m["id"],
                        "native_meeting_id": NATIVE}


def test_the_record_is_kept_for_its_ttl(rig, redis):
    client, repo, _pub = rig
    _seed(repo)
    rid = _send(client)["request_id"]
    assert 0 < asyncio.run(redis.ttl(chat_send_key(rid))) <= CHAT_SEND_TTL_SEC


def test_a_send_nothing_received_leaves_no_record(redis):
    repo, pub = InMemoryMeetingRepo(), _ListeningRedis(redis, receivers=0)
    client = TestClient(create_app(meeting_repo=repo, command_publisher=pub))
    _seed(repo)
    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=H, json={"text": "anyone?"})
    assert r.status_code == 409
    rid = json.loads(pub.published[-1][1])["id"]
    assert asyncio.run(redis.get(chat_send_key(rid))) is None
    assert asyncio.run(redis.keys("chat:send:*")) == []


# ── the bot's result settles the send ───────────────────────────────────────────────────────────


def test_a_posted_result_reads_posted(rig, redis, admin_token):
    client, repo, _pub = rig
    m = _seed(repo)
    rid = _send(client)["request_id"]
    data = _result(m["id"], rid, posted=True)
    RESULT.validate(data)
    _bot_reports(redis, data)
    r = _status(client, rid)
    assert r.json()["status"] == "posted", r.text
    assert "reason" not in r.json()
    SEND_STATUS.validate(r.json())


def test_a_failed_result_reads_failed_with_the_bots_reason(rig, redis, admin_token):
    client, repo, _pub = rig
    m = _seed(repo)
    rid = _send(client)["request_id"]
    data = _result(m["id"], rid, posted=False, reason="not_landed")
    RESULT.validate(data)
    _bot_reports(redis, data)
    r = _status(client, rid)
    assert r.json() == {"request_id": rid, "status": "failed", "reason": "not_landed",
                        "meeting_id": m["id"], "native_meeting_id": NATIVE}
    SEND_STATUS.validate(r.json())


def test_a_reason_outside_the_failure_set_is_recorded_as_unknown(rig, redis, admin_token):
    client, repo, _pub = rig
    m = _seed(repo)
    rid = _send(client)["request_id"]
    _bot_reports(redis, _result(m["id"], rid, posted=False, reason="<script>"))
    r = _status(client, rid)
    assert (r.json()["status"], r.json()["reason"]) == ("failed", "unknown")
    SEND_STATUS.validate(r.json())


def test_settling_keeps_the_sends_ttl(rig, redis, admin_token):
    client, repo, _pub = rig
    m = _seed(repo)
    rid = _send(client)["request_id"]
    _bot_reports(redis, _result(m["id"], rid, posted=True))
    assert 0 < asyncio.run(redis.ttl(chat_send_key(rid))) <= CHAT_SEND_TTL_SEC


# ── what a result may not do (negative controls) ────────────────────────────────────────────────


def test_a_result_for_an_id_never_sent_creates_nothing(rig, redis, admin_token):
    client, repo, _pub = rig
    m = _seed(repo)
    _bot_reports(redis, _result(m["id"], "0f1e2d3c4b5a69788796a5b4c3d2e1f0", posted=True))
    assert asyncio.run(redis.keys("chat:send:*")) == []
    assert _status(client, "0f1e2d3c4b5a69788796a5b4c3d2e1f0").status_code == 404


def test_another_meetings_bot_cannot_settle_a_send(rig, redis, admin_token):
    """The other meeting's bot signs a result naming the send, for its OWN meeting (the collector
    admits it) — the send belongs to a different meeting, so it stays queued."""
    client, repo, _pub = rig
    m = _seed(repo)
    other = _seed(repo, user=8, native="xyz-wxyz-xyz")
    rid = _send(client)["request_id"]
    _bot_reports(redis, _result(other["id"], rid, posted=False, reason="no_composer"))
    assert _status(client, rid).json()["status"] == "queued"
    # and a result naming the send's meeting but signed by the other meeting's token is not admitted
    _bot_reports(redis, _result(m["id"], rid, posted=False, reason="no_composer"),
                 token=token_for(other["id"]))
    assert _status(client, rid).json()["status"] == "queued"
    # positive control: the meeting's own bot settles it
    _bot_reports(redis, _result(m["id"], rid, posted=True))
    assert _status(client, rid).json()["status"] == "posted"


def test_a_settled_send_is_not_overwritten(rig, redis, admin_token):
    client, repo, _pub = rig
    m = _seed(repo)
    rid = _send(client)["request_id"]
    _bot_reports(redis, _result(m["id"], rid, posted=True))
    _bot_reports(redis, _result(m["id"], rid, posted=False, reason="page_error"))
    assert _status(client, rid).json()["status"] == "posted"


def test_a_malformed_result_never_aborts_the_batch(rig, redis, admin_token):
    """A result with a non-boolean `posted` is dropped; the transcript entry behind it still lands."""
    client, repo, _pub = rig
    m = _seed(repo)
    rid = _send(client)["request_id"]

    async def run():
        bus = FakeRedisBus(redis)
        store = InMemoryTranscriptStore()
        store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE)
        await signed_xadd(bus, {"type": "chat_send_result", "meeting_id": m["id"], "id": rid,
                                "posted": "yes"})
        await signed_xadd(bus, {"type": "transcription", "meeting_id": m["id"], "segments": [
            {"segment_id": "s-1", "start": 0.0, "end": 1.0, "text": "still here", "completed": True}]})
        return await consume_segments(store, bus)

    assert asyncio.run(run()) == 1
    assert _status(client, rid).json()["status"] == "queued"


# ── who may read ────────────────────────────────────────────────────────────────────────────────


def test_another_users_read_is_404(rig):
    client, repo, _pub = rig
    _seed(repo)
    rid = _send(client)["request_id"]
    assert _status(client, rid, headers={"x-user-id": "8"}).status_code == 404
    assert _status(client, rid).status_code == 200   # positive control: the sender reads it


def test_a_read_under_another_meeting_or_platform_is_404(rig):
    client, repo, _pub = rig
    _seed(repo)
    rid = _send(client)["request_id"]
    assert _status(client, rid, native="xyz-wxyz-xyz").status_code == 404
    assert _status(client, rid, platform="jitsi").status_code == 404


@pytest.mark.parametrize("rid", ["0f1e2d3c4b5a69788796a5b4c3d2e1f0", "not..an..id", "x" * 65])
def test_an_unknown_or_malformed_id_is_404(rig, rid):
    client, repo, _pub = rig
    _seed(repo)
    assert _status(client, rid).status_code == 404


def test_an_unsupported_platform_is_422(rig):
    client, _repo, _pub = rig
    assert _status(client, "abc", platform="skype").status_code == 422


def test_a_read_without_identity_is_401(rig):
    client, _repo, _pub = rig
    assert client.get(f"/bots/{PLAT}/{NATIVE}/chat/abc").status_code == 401


def test_the_read_needs_the_bot_scope_and_refuses_a_shared_turn(redis):
    key = identity_token.generate_signing_key()
    repo, pub = InMemoryMeetingRepo(), _ListeningRedis(redis)
    client = TestClient(create_app(meeting_repo=repo, command_publisher=pub,
                                   identity_key=key.public_key(), internal_secret="chat-internal"))

    def signed(scopes, **delegation):
        claims = {"sub": str(USER), "scopes": list(scopes), "limits": 3}
        if delegation:
            claims["delegation"] = delegation
        return {identity_token.HEADER: identity_token.sign(key, claims)}

    _seed(repo)
    rid = _send(client, headers=signed(["bot"]))["request_id"]
    assert _status(client, rid, headers=signed(["browser"])).status_code == 403
    shared = _status(client, rid, headers=signed(["bot"], regime="human", workspaces=["ws_1"],
                                                  shared=True))
    assert shared.status_code == 403, shared.text
    assert _status(client, rid, headers=signed(["bot"])).status_code == 200


# ── unconfirmed: no result within the bound ─────────────────────────────────────────────────────


class _Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


async def test_a_send_still_queued_past_the_bound_reads_unconfirmed(redis):
    clock = _Clock(5000.0)
    ledger = ChatDeliveryLedger(redis, clock=clock)
    await ledger.record("r1", user_id=USER, meeting_id=1, platform=PLAT, native_meeting_id=NATIVE)
    clock.t = 5000.0 + UNCONFIRMED_AFTER_SEC - 1
    assert (await ledger.read("r1"))["status"] == "queued"
    clock.t = 5000.0 + UNCONFIRMED_AFTER_SEC
    assert (await ledger.read("r1"))["status"] == "unconfirmed"
    # a result that does arrive late still settles it: unconfirmed is a reading, not a state
    assert await ledger.settle("r1", meeting_id=1, posted=True)
    assert (await ledger.read("r1"))["status"] == "posted"


def test_the_route_reads_an_old_queued_send_as_unconfirmed(rig, redis):
    client, repo, _pub = rig
    m = _seed(repo)
    asyncio.run(ChatDeliveryLedger(redis, clock=lambda: 1_000.0).record(
        "old-send", user_id=USER, meeting_id=m["id"], platform=PLAT, native_meeting_id=NATIVE))
    r = _status(client, "old-send")
    assert r.json()["status"] == "unconfirmed", r.text
    SEND_STATUS.validate(r.json())


async def test_the_in_process_ledger_answers_the_same_way():
    clock = _Clock(100.0)
    ledger = ChatDeliveryLedger(clock=clock)
    await ledger.record("r2", user_id=USER, meeting_id=3, platform=PLAT, native_meeting_id=NATIVE)
    assert not await ledger.settle("r2", meeting_id=4, posted=True)
    assert await ledger.settle("r2", meeting_id=3, posted=False, reason="no_chat_api")
    assert not await ledger.settle("r2", meeting_id=3, posted=True)
    got = await ledger.read("r2")
    assert (got["status"], got["reason"]) == ("failed", "no_chat_api")
    clock.t = 100.0 + CHAT_SEND_TTL_SEC
    assert await ledger.read("r2") is None
