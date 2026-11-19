"""What meeting-api writes into the per-meeting feed conforms to the SEALED transcript.v1 contract.

``tc:meeting:{row}`` has one owner on the writing side (the meetings domain) and two readers that act
on it (agent-api's transcription watcher, the terminal's live view through agent-api). The contract
states its shape: every entry is a ``FeedEntry`` — one ``payload`` field — whose JSON is a
``FeedTranscription``, a ``FeedRetract``, a ``FeedSessionStart`` or a ``SessionEnd``. The golden validator
(``transcript.v1/validate.mjs``) checks the goldens against that; nothing checked the WRITERS, and
they drifted: the end marker went out as ``{"type": "session_end", "uid": …}`` while the sealed
``SessionEnd`` (additionalProperties: false) requires ``session_uid``. A reader written from the
contract would have read nothing.

So every writer is driven here and every entry it leaves is validated the way ``validate.mjs`` does
it — the ``FeedEntry`` wrapper, then the inner shape picked by its ``type``:

  * the collector's ingest: a segment, a retract, and a bot's own ``session_end``;
  * the transcript import's completion marker;
  * the lifecycle's markers: ``session_start`` when a meeting goes active, the reap
    ``session_end`` when it lands terminal. The start marker was written as ``{"uid": …}`` and
    was in no feed shape at all until transcript.v1 gained ``FeedSessionStart``.

The schema is loaded BY PATH off the contract directory (the collector conforms to it, never edits it).
"""
from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from pathlib import Path

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from meeting_api import create_app as create_meeting_app
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.collector import create_app as create_collector_app
from meeting_api.collector import ingest
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore

# The same inner-shape table as transcript.v1/validate.mjs.
INNER = {"transcription": "FeedTranscription", "retract": "FeedRetract",
         "session_start": "FeedSessionStart", "session_end": "SessionEnd"}


@lru_cache(maxsize=None)
def _schema() -> dict:
    rel = Path("contracts") / "transcript.v1" / "transcript.schema.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"sealed contract not found by path: {rel}")


def _validator(shape: str) -> Draft202012Validator:
    root = _schema()
    assert shape in root["$defs"], f"transcript.v1 has no $defs/{shape}"
    return Draft202012Validator({**root, "$ref": f"#/$defs/{shape}"})


def assert_feed_entry(fields: dict) -> dict:
    """One raw feed entry (its stream fields) → the inner payload, or an AssertionError naming why."""
    errors = list(_validator("FeedEntry").iter_errors(fields))
    assert not errors, f"not a FeedEntry {fields!r}: {[e.message for e in errors]}"
    inner = json.loads(fields["payload"])
    shape = INNER.get(inner.get("type"))
    assert shape, f"feed payload type {inner.get('type')!r} is not one transcript.v1 admits: {inner!r}"
    errors = list(_validator(shape).iter_errors(inner))
    assert not errors, f"not a {shape} {inner!r}: {[e.message for e in errors]}"
    return inner


# ── the collector's ingest ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def store():
    s = InMemoryTranscriptStore()
    s.seed_meeting(user_id=7, platform="google_meet", native_meeting_id="abc-defg-hij")
    return s


@pytest.fixture
async def bus():
    client = fakeredis.aioredis.FakeRedis()
    yield FakeRedisBus(client)
    await client.aclose()


def _bot(body: dict) -> dict:
    return {"payload": json.dumps(body)}


async def _feed(client, meeting_id) -> list[dict]:
    out = []
    for _id, fields in await client.xrange(f"tc:meeting:{meeting_id}"):
        out.append({(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                    for k, v in fields.items()})
    return out


async def test_every_entry_the_collector_writes_is_a_sealed_feed_entry(store, bus):
    await ingest(store, bus, _bot({"type": "transcription", "meeting_id": "1", "segments": [
        {"speaker": "Alice", "text": "Hello", "start": 1.0, "end": 2.5, "completed": True,
         "language": "en", "segment_id": "a", "absolute_start_time": None},
        {"speaker": "Bob", "text": "Hi there", "start": 2.5, "end": 4.0, "completed": True,
         "language": "en", "segment_id": "b"},
    ]}))
    await ingest(store, bus, _bot({"type": "transcript_retract", "meeting_id": "1", "segment_ids": ["b"]}))
    await ingest(store, bus, _bot({"type": "session_end", "meeting_id": "1",
                                   "native_meeting_id": "abc-defg-hij"}))

    entries = await _feed(bus._client, 1)
    kinds = [assert_feed_entry(e)["type"] for e in entries]
    # all three kinds were exercised, so a writer that drifts on any one of them fails here
    assert {"transcription", "retract", "session_end"} <= set(kinds), kinds
    end = json.loads(entries[-1]["payload"])
    assert end == {"type": "session_end", "session_uid": "abc-defg-hij"}


# ── the transcript import's completion marker ───────────────────────────────────────────────────

class _Recorder:
    """A redis double that keeps what each writer hands it, wrapped the way the production adapter
    (``RedisStreamBus.xadd``) puts it on the stream: one ``payload`` field."""

    def __init__(self):
        self.streams: dict[str, list[dict]] = {}

    async def publish(self, channel, data):
        return 1

    async def xadd(self, stream, payload):
        self.streams.setdefault(stream, []).append({"payload": json.dumps(payload)})
        return f"{len(self.streams[stream])}-0"


def test_the_import_marker_is_a_sealed_session_end():
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=11, platform="jitsi", native_meeting_id="sync-2026-08-03",
                             status="scheduled", start_time=None, end_time=None)
    redis = _Recorder()
    client = TestClient(create_collector_app(store, redis=redis))
    r = client.post(f"/meetings/{mid}/transcript-import", headers={"x-user-id": "11"}, json={
        "segments": [{"start": 0.0, "end": 2.0, "speaker": "Larry", "text": "Welcome."}],
        "started_at": "2026-08-03T14:00:00Z", "source": "import"})
    assert r.status_code == 200, r.text
    entries = redis.streams.get(f"tc:meeting:{mid}", [])
    assert entries, f"no completion marker on the row-keyed feed: {list(redis.streams)}"
    assert [assert_feed_entry(e)["type"] for e in entries] == ["session_end"]


# ── the lifecycle's reap marker ─────────────────────────────────────────────────────────────────

def test_the_lifecycle_markers_are_sealed_feed_entries():
    repo = InMemoryMeetingRepo()
    m = asyncio.run(repo.create_meeting(user_id=1, platform="google_meet", native_meeting_id="m1", data={}))
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid="sess-uid"))
    repo.set_status(m["id"], "requested")
    redis = _Recorder()
    client = TestClient(create_meeting_app(open_callbacks=True, meeting_repo=repo, redis=redis))
    for status in ("joining", "active", "completed"):
        ev = {"connection_id": "sess-uid", "status": status}
        if status == "completed":
            ev["completion_reason"] = "stopped"
        assert client.post("/bots/internal/callback/lifecycle", json=ev).status_code == 200

    entries = redis.streams.get(f"tc:meeting:{m['id']}", [])
    inner = [assert_feed_entry(e) for e in entries]          # EVERY entry the lifecycle wrote
    assert {"type": "session_start", "session_uid": "m1"} in inner, inner
    assert [p for p in inner if p["type"] == "session_end"] == [{"type": "session_end", "session_uid": "m1"}]
