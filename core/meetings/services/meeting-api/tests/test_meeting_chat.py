"""In-meeting chat, server half (#1089) — read, write, and the live frame.

  * ``POST /bots/{platform}/{native}/chat`` publishes an ``acts.v1`` ``chat_send`` on the ACTIVE
    meeting's ``bot_commands:meeting:{id}`` channel (owner-resolved like the user stop), with the
    refusals a client codes against: 404 no active meeting (or not the caller's) · 409 bot not yet
    admitted, or no bot listening · 422 bad body / too long / unsupported or chatless platform ·
    503 command bus down.
  * ``GET /bots/{platform}/{native}/chat`` answers the meeting's ``source:'chat'`` transcript
    segments as sealed api.v1 ``ChatMessage`` rows, oldest first — and ONLY those.
  * ingesting a ``source:'chat'`` segment publishes a ws.v1 ``ChatMessage`` frame on
    ``va:meeting:{id}:chat`` (the channel the gateway ``/ws`` fans in); speech never does.
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

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.collector import create_app as create_collector_app
from meeting_api.collector import ingest
from meeting_api.collector.db_writer import db_writer_tick
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore
from meeting_api.lifecycle.chat_router import (
    CHAT_POSTS_PER_WINDOW, CHAT_WINDOW_SEC, MAX_CHAT_CHARS, ChatRateLimit,
)
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher

from collector_contracts import assert_api_conforms

USER = 7
H = {"x-user-id": str(USER)}
PLAT, NATIVE = "google_meet", "abc-defg-hij"
CHAT_AT = 1_789_000_000.0  # epoch seconds — a chat segment's timing, like the audio lanes


def _contract(rel: str) -> dict:
    for parent in Path(__file__).resolve().parents:
        if (parent / rel).is_file():
            return json.loads((parent / rel).read_text())
    raise FileNotFoundError(rel)


def _validator(rel: str, defn: str) -> Draft202012Validator:
    root = _contract(rel)
    resource = Resource.from_contents(root, default_specification=DRAFT202012)
    registry = Registry().with_resource(uri=root["$id"], resource=resource)
    return Draft202012Validator({"$ref": f"{root['$id']}#/$defs/{defn}"}, registry=registry)


ACT = _validator("core/meetings/contracts/acts.v1/acts.schema.json", "Act")
WS_CHAT = _validator("core/gateway/contracts/ws.v1/ws.schema.json", "ChatMessage")


def _chat_segment(n: int, sender: str, text: str) -> dict:
    """The bot's chat publish (``services/bot/src/index.ts`` publishChat): a point-in-time,
    already-completed transcript.v1 segment with ``source:'chat'``."""
    at = CHAT_AT + n
    return {
        "segment_id": f"sess-1:chat:{int(at * 1000)}:{n}", "speaker": sender,
        "speaker_key": f"chat:{sender}", "text": text, "start": at, "end": at,
        "completed": True, "source": "chat",
    }


def _speech_segment() -> dict:
    return {"segment_id": "ch-0:1:a", "start": 1.0, "end": 2.5, "text": "spoken words",
            "language": "en", "speaker": "Alice", "completed": True}


def _message(meeting_id: int, segments: list[dict]) -> dict:
    return {"payload": json.dumps({
        "type": "transcription", "meeting_id": str(meeting_id), "uid": "sess-1",
        "platform": PLAT, "native_meeting_id": NATIVE, "segments": segments,
    })}


# ── POST /bots/{platform}/{native}/chat → acts.v1 chat_send ─────────────────────────────────────


def _seed(repo, *, status="active", native=NATIVE):
    m = asyncio.run(repo.create_meeting(user_id=USER, platform=PLAT, native_meeting_id=native, data={}))
    sid = f"sess-{m['id']}"
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=sid))
    if status != "requested":
        asyncio.run(repo.update_meeting_status(session_uid=sid, status=status))
    return m


class _ListeningBus(InMemoryCommandPublisher):
    """The command bus with the meeting's bot subscribed: redis answers one receiver."""

    async def publish(self, channel, message):
        await super().publish(channel, message)
        return 1


def _app(publisher=None):
    repo, pub = InMemoryMeetingRepo(), publisher or _ListeningBus()
    return TestClient(create_app(meeting_repo=repo, command_publisher=pub)), repo, pub


def test_post_chat_publishes_a_chat_send_act_on_the_meetings_command_channel():
    client, repo, pub = _app()
    m = _seed(repo)

    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=H, json={"text": "hello room"})

    assert r.status_code == 200, r.text
    assert r.json() == {"status": "queued", "meeting_id": m["id"], "native_meeting_id": NATIVE}
    assert len(pub.published) == 1
    channel, raw = pub.published[0]
    assert channel == f"bot_commands:meeting:{m['id']}"
    act = json.loads(raw)
    assert act == {"action": "chat_send", "text": "hello room"}
    ACT.validate(act)


def test_post_chat_without_an_active_meeting_is_404_and_publishes_nothing():
    client, _repo, pub = _app()
    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=H, json={"text": "anyone?"})
    assert r.status_code == 404
    assert pub.published == []


def test_post_chat_on_another_users_meeting_is_404():
    client, repo, pub = _app()
    _seed(repo)
    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers={"x-user-id": "8"}, json={"text": "hi"})
    assert r.status_code == 404
    assert pub.published == []


@pytest.mark.parametrize("stage", ["requested", "joining", "awaiting_admission"])
def test_post_chat_before_the_bot_is_admitted_is_409(stage):
    client, repo, pub = _app()
    _seed(repo, status=stage)
    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=H, json={"text": "too early"})
    assert r.status_code == 409, r.text
    assert pub.published == []


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "   "}, {"text": 42}, {"text": None}, ["hi"]])
def test_post_chat_rejects_a_body_without_text_with_422(body):
    client, repo, pub = _app()
    _seed(repo)
    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=H, json=body)
    assert r.status_code == 422, r.text
    assert pub.published == []


def test_post_chat_rejects_an_unsupported_platform_with_422():
    client, _repo, pub = _app()
    r = client.post(f"/bots/skype/{NATIVE}/chat", headers=H, json={"text": "hi"})
    assert r.status_code == 422
    assert pub.published == []


@pytest.mark.parametrize("platform", ["zoom", "teams", "vexa_call"])
def test_post_chat_on_a_platform_whose_bot_has_no_chat_sender_is_422(platform):
    """In the Platform enum, but nothing in that meeting would type the line: refused, not queued."""
    client, repo, pub = _app()
    asyncio.run(repo.create_meeting(user_id=USER, platform=platform, native_meeting_id=NATIVE, data={}))
    r = client.post(f"/bots/{platform}/{NATIVE}/chat", headers=H, json={"text": "hi"})
    assert r.status_code == 422, r.text
    assert pub.published == []


def test_post_chat_longer_than_the_cap_is_422():
    client, repo, pub = _app()
    _seed(repo)
    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=H, json={"text": "x" * (MAX_CHAT_CHARS + 1)})
    assert r.status_code == 422, r.text
    assert pub.published == []
    ok = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=H, json={"text": "x" * MAX_CHAT_CHARS})
    assert ok.status_code == 200, ok.text


def test_post_chat_that_no_bot_received_is_409():
    """Redis answered zero receivers: the bot has not subscribed yet, or has gone. Not `queued`."""
    client, repo, _pub = _app(publisher=InMemoryCommandPublisher())   # answers 0 receivers
    _seed(repo)
    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=H, json={"text": "anyone?"})
    assert r.status_code == 409, r.text


def test_post_chat_without_identity_is_401():
    client, _repo, _pub = _app()
    assert client.post(f"/bots/{PLAT}/{NATIVE}/chat", json={"text": "hi"}).status_code == 401


def test_post_chat_when_the_command_bus_is_down_is_503():
    class _Down:
        async def publish(self, channel, message):
            raise ConnectionError("redis unreachable")

    client, repo, _pub = _app(publisher=_Down())
    _seed(repo)
    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=H, json={"text": "hi"})
    assert r.status_code == 503


# ── the per-meeting rate limit: CHAT_POSTS_PER_WINDOW posts per CHAT_WINDOW_SEC, then 429 ────────


def test_post_chat_past_the_meetings_limit_is_429_with_retry_after_and_sends_nothing():
    client, repo, pub = _app()
    _seed(repo)
    other = _seed(repo, native="xyz-wxyz-xyz")

    for n in range(CHAT_POSTS_PER_WINDOW):
        ok = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=H, json={"text": f"line {n}"})
        assert ok.status_code == 200, (n, ok.text)
    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=H, json={"text": "one too many"})

    assert r.status_code == 429, r.text
    assert 1 <= int(r.headers["retry-after"]) <= CHAT_WINDOW_SEC
    assert len(pub.published) == CHAT_POSTS_PER_WINDOW
    # The count is the meeting's own: another meeting of the same owner still sends.
    r2 = client.post(f"/bots/{PLAT}/xyz-wxyz-xyz/chat", headers=H, json={"text": "elsewhere"})
    assert r2.status_code == 200, r2.text
    assert pub.published[-1][0] == f"bot_commands:meeting:{other['id']}"


class _Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


async def test_chat_rate_limit_in_process_refuses_until_the_window_turns():
    clock = _Clock(1000.0)
    limit = ChatRateLimit(limit=2, window=10, clock=clock)
    assert await limit.take(1) is None
    assert await limit.take(1) is None
    clock.t = 1003.2
    assert await limit.take(1) == 7          # the window [1000, 1010) turns in 6.8 s
    assert await limit.take(2) is None       # another meeting has its own count
    clock.t = 1010.0
    assert await limit.take(1) is None       # a new window, a new count


async def test_chat_rate_limit_counts_in_redis_across_replicas():
    """Two meeting-api replicas on one Redis draw on the same per-meeting count."""
    redis = fakeredis.aioredis.FakeRedis()
    clock = _Clock(2000.0)
    a = ChatRateLimit(redis, limit=3, window=10, clock=clock)
    b = ChatRateLimit(redis, limit=3, window=10, clock=clock)
    assert [await a.take(42), await b.take(42), await a.take(42)] == [None, None, None]
    assert await b.take(42) == 10
    assert 0 < await redis.ttl(f"{ChatRateLimit.PREFIX}42:200") <= 11   # the window's key expires
    clock.t = 2010.0
    assert await b.take(42) is None


# ── who may send: the gateway's signed identity, the `bot` scope, the owner ─────────────────────


def _guarded():
    from meeting_api import identity_token

    key = identity_token.generate_signing_key()
    repo, pub = InMemoryMeetingRepo(), _ListeningBus()
    client = TestClient(create_app(meeting_repo=repo, command_publisher=pub,
                                   identity_key=key.public_key(), internal_secret="chat-internal"))

    def signed(scopes, sub=str(USER)):
        return {identity_token.HEADER: identity_token.sign(
            key, {"sub": sub, "scopes": list(scopes), "limits": 3})}

    return client, repo, pub, signed


def test_post_chat_believes_only_the_gateways_signed_identity():
    """An `x-user-id` the caller typed is refused at the door (IdentityGuard), never trusted."""
    client, repo, pub, _signed = _guarded()
    _seed(repo)
    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=H, json={"text": "spoofed"})
    assert r.status_code == 401, r.text
    assert pub.published == []


def test_post_chat_needs_the_bot_scope_on_the_signed_identity():
    client, repo, pub, signed = _guarded()
    _seed(repo)
    assert client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=signed(["tx"]),
                       json={"text": "no bot scope"}).status_code == 403
    assert pub.published == []
    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=signed(["bot"]), json={"text": "mine"})
    assert r.status_code == 200, r.text
    assert len(pub.published) == 1


def test_post_chat_as_another_signed_user_is_404():
    client, repo, pub, signed = _guarded()
    _seed(repo)
    r = client.post(f"/bots/{PLAT}/{NATIVE}/chat", headers=signed(["bot"], sub="8"), json={"text": "hi"})
    assert r.status_code == 404
    assert pub.published == []


# ── GET /bots/{platform}/{native}/chat → the persisted chat segments ────────────────────────────


def _collector(store):
    class _Capture:
        async def publish(self, channel, data):
            pass

    return TestClient(create_collector_app(store, redis=_Capture()))


async def test_get_chat_returns_the_meetings_chat_segments_oldest_first_and_nothing_else():
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE)
    bus = FakeRedisBus(fakeredis.aioredis.FakeRedis())
    await ingest(store, bus, _message(mid, [_chat_segment(2, "Bob", "second"), _speech_segment(),
                                            _chat_segment(1, "Alice", "first")]))

    r = _collector(store).get(f"/bots/{PLAT}/{NATIVE}/chat", headers=H)

    assert r.status_code == 200, r.text
    body = r.json()
    assert_api_conforms("ChatMessagesResponse", body)
    assert body["messages"] == [
        {"sender": "Alice", "text": "first", "timestamp": (CHAT_AT + 1) * 1000, "is_from_bot": False},
        {"sender": "Bob", "text": "second", "timestamp": (CHAT_AT + 2) * 1000, "is_from_bot": False},
    ]


async def test_get_chat_still_answers_after_the_db_writer_made_the_segments_durable():
    """The chat survives the flush from the live redis hash into the durable rows: the read is a
    query over what is STORED, not over the in-flight window."""
    client = fakeredis.aioredis.FakeRedis()
    store = InMemoryTranscriptStore(redis_client=client)
    mid = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE)
    await ingest(store, FakeRedisBus(client), _message(mid, [_chat_segment(1, "Alice", "kept")]))
    await db_writer_tick(client, store, immutability_threshold=0.0)
    assert await client.hlen(f"meeting:{mid}:segments") == 0  # nothing left in flight

    r = _collector(store).get(f"/bots/{PLAT}/{NATIVE}/chat", headers=H)

    assert [m["text"] for m in r.json()["messages"]] == ["kept"]


def test_get_chat_with_no_chat_is_an_empty_list():
    store = InMemoryTranscriptStore()
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE)
    r = _collector(store).get(f"/bots/{PLAT}/{NATIVE}/chat", headers=H)
    assert r.status_code == 200
    assert r.json() == {"messages": []}


# ── ingest → va:meeting:{id}:chat (the ws.v1 ChatMessage producer) ──────────────────────────────


async def test_ingesting_a_chat_segment_publishes_a_ws_chat_message_frame():
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE)
    bus = FakeRedisBus(fakeredis.aioredis.FakeRedis())

    await ingest(store, bus, _message(mid, [_chat_segment(1, "Alice", "hi bot"), _speech_segment()]))

    frames = [json.loads(raw) for ch, raw in bus.published if ch == f"va:meeting:{mid}:chat"]
    assert frames == [{
        "type": "chat_message",
        "meeting": {"id": mid, "native_id": NATIVE, "platform": PLAT},
        "sender": "Alice", "text": "hi bot",
        "timestamp": (CHAT_AT + 1) * 1000, "is_from_bot": False,
    }]
    WS_CHAT.validate(frames[0])


async def test_the_chat_frame_names_the_platform_when_the_bot_stamps_only_the_native_id():
    # The bot's real payload (adapters/transcript-redis.ts) carries meeting_id + native_meeting_id and
    # no platform; the frame's meeting block must still name it (seen null on the w0134 rig).
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform="jitsi", native_meeting_id=NATIVE)
    bus = FakeRedisBus(fakeredis.aioredis.FakeRedis())
    msg = {"payload": json.dumps({
        "type": "transcription", "meeting_id": str(mid), "native_meeting_id": NATIVE,
        "segments": [_chat_segment(1, "Alice", "hi bot")],
    })}

    await ingest(store, bus, msg)

    frames = [json.loads(raw) for ch, raw in bus.published if ch == f"va:meeting:{mid}:chat"]
    assert [f["meeting"] for f in frames] == [{"id": mid, "native_id": NATIVE, "platform": "jitsi"}]
    WS_CHAT.validate(frames[0])


async def test_ingesting_speech_publishes_no_chat_frame():
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE)
    bus = FakeRedisBus(fakeredis.aioredis.FakeRedis())
    await ingest(store, bus, _message(mid, [_speech_segment()]))
    assert not [ch for ch, _ in bus.published if ch.startswith("va:")]


async def test_a_failed_chat_publish_never_fails_ingest():
    class _Flaky(FakeRedisBus):
        async def publish(self, channel, data):
            if channel.startswith("va:"):
                raise ConnectionError("pubsub down")
            return await super().publish(channel, data)

    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE)
    n = await ingest(store, _Flaky(fakeredis.aioredis.FakeRedis()),
                     _message(mid, [_chat_segment(1, "Alice", "still stored")]))
    assert n == 1
    doc = await store.get_transcript(USER, PLAT, NATIVE)
    assert [s["text"] for s in doc["segments"]] == ["still stored"]


# ── the durable half: Postgres keeps `source`, so a flushed chat line is still a chat line ───────


def test_the_transcriptions_upsert_carries_the_segment_source():
    from meeting_api.collector.adapters import _SEGMENT_UPSERT_SQL, _segment_upsert_rows

    [row] = _segment_upsert_rows(5, [_chat_segment(1, "Alice", "kept")])
    assert row["source"] == "chat"
    assert ":source" in _SEGMENT_UPSERT_SQL and "source = EXCLUDED.source" in _SEGMENT_UPSERT_SQL


def test_a_durable_transcriptions_row_reads_back_its_source():
    from types import SimpleNamespace

    from meeting_api.collector.adapters import _persisted_segment, _segment_to_api

    row = SimpleNamespace(start_time=CHAT_AT, end_time=CHAT_AT, text="kept", language=None,
                          speaker="Alice", segment_id="sess-1:chat:1", source="chat")
    assert _segment_to_api(_persisted_segment(row))["source"] == "chat"


def test_both_schema_models_declare_the_transcriptions_source_column():
    """admin-api's models own the table (its schema sync adds the column); meeting-api's mirror must
    match it byte-for-byte or the db-writer's INSERT names a column the mirror does not know."""
    for rel in ("core/identity/services/admin-api/src/admin_api/schema/models.py",
                "core/meetings/services/meeting-api/src/meeting_api/sessions/models.py"):
        for parent in Path(__file__).resolve().parents:
            if (parent / rel).is_file():
                assert "    source = Column(String(32), nullable=True)\n" in (parent / rel).read_text(), rel
                break
        else:
            raise FileNotFoundError(rel)
