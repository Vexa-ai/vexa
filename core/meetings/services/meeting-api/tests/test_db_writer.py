"""db-writer eval — the RESTORED redis→durable flush loop (the 0.12 release-blocking data-loss fix).

The 0.12 carve ported the segment consumer, the read merge, and the store — but never the parent's
background db-writer (0.10 ``collector/db_writer.py`` ``process_redis_to_postgres``). Segments
lived ONLY in the redis hash ``meeting:{id}:segments``; the ``transcriptions`` table stayed empty;
a redis eviction was unrecoverable transcript loss (verified live: 6 meetings, zero rows, 3 hashes
already gone). These evals drive the restored writer deterministically (explicit ticks, fakeredis,
the redis-wired in-memory store mirroring the prod topology — no docker):

  * consumer tick + db-writer tick ⇒ segments land in the DURABLE store, redis trimmed only after;
  * the FLIPPED INCIDENT — redis wiped after a flush ⇒ GET /transcripts still serves from durable;
  * parent semantics — the mutable tail (young ``updated_at``) stays in redis; empty text is
    dropped not stored; a failed durable write leaves the hash INTACT (trim-after-confirm);
  * completion finalization — the lifecycle callback's terminal advance flushes EVERYTHING left.

A whole section used to sit beside these: the processed-doc drain — ``proc:meeting:{row_id}``
notes into ``data['processed']``, its ``view_end`` end-of-processing protocol and the bounded
``processed_pending`` re-drain. PRD decision 34 removed the producer of that stream, so there is
one body of a transcript and one drain to prove.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from meeting_api.collector import consume_segments
from meeting_api.collector.db_writer import (
    ACTIVE_MEETINGS_KEY,
    db_writer_tick,
    finalize_meeting,
    flush_meeting_segments,
    segments_hash_key,
)
from meeting_api.collector.fakes import FakeRedisBus, InMemoryTranscriptStore

USER = 7
NATIVE = "abc-defg-hij"
LATER = datetime.now(timezone.utc) + timedelta(seconds=120)  # every ingested segment is immutable by then


@pytest.fixture
async def redis_c():
    client = fakeredis.aioredis.FakeRedis()
    yield client
    await client.aclose()


@pytest.fixture
def store(redis_c):
    """The PROD-topology store: append_segment → redis hash; the durable dict stands in for the
    transcriptions table; get_transcript merges durable + hash exactly like the SQL adapter."""
    s = InMemoryTranscriptStore(redis_client=redis_c)
    s.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id=NATIVE, meeting_id=1)
    return s


@pytest.fixture
def bus(redis_c):
    return FakeRedisBus(redis_c)


def _message(meeting_id: int, segments: list[dict]) -> dict:
    return {"payload": json.dumps({
        "type": "transcription", "meeting_id": str(meeting_id), "uid": "sess-1",
        "platform": "google_meet", "segments": segments,
    })}


def _seg(sid: str, start: float, text: str, *, completed: bool = True) -> dict:
    return {"segment_id": sid, "start": start, "end": start + 1.5, "text": text,
            "language": "en", "speaker": "Alice", "completed": completed}


def _durable_texts(store, meeting_id: int = 1) -> list[str]:
    rows = store._meetings[meeting_id]["segments"]
    return [rows[k]["text"] for k in sorted(rows)]


# ── (a) consumer tick + db-writer tick ⇒ durable ────────────────────────────────────────────────

async def test_consumer_tick_then_db_writer_tick_lands_segments_durably(store, bus, redis_c):
    await bus.xadd("transcription_segments", json.loads(_message(1, [
        _seg("s1", 1.0, "Hello"), _seg("s2", 2.5, "world"),
    ])["payload"]))
    assert await consume_segments(store, bus) == 2

    # After the consumer tick the segments are ONLY in the live redis hash — durable is empty
    # (exactly the pre-fix production state).
    assert await redis_c.hlen(segments_hash_key(1)) == 2
    assert _durable_texts(store) == []

    stored = await db_writer_tick(redis_c, store, now=LATER)
    assert stored == 2
    assert _durable_texts(store) == ["Hello", "world"]
    # trim policy: flushed fields leave the hash; the drained meeting leaves active_meetings.
    assert await redis_c.hlen(segments_hash_key(1)) == 0
    assert await redis_c.smembers(ACTIVE_MEETINGS_KEY) == set()


async def test_db_writer_tick_is_idempotent_and_upserts_rewrites(store, bus, redis_c):
    await bus.xadd("transcription_segments", json.loads(_message(1, [_seg("s1", 1.0, "draft")])["payload"]))
    await consume_segments(store, bus)
    await db_writer_tick(redis_c, store, now=LATER)
    # A refining rewrite of the SAME segment_id re-enters the hash…
    await bus.xadd("transcription_segments", json.loads(_message(1, [_seg("s1", 1.0, "polished")])["payload"]))
    await consume_segments(store, bus)
    await db_writer_tick(redis_c, store, now=LATER)
    await db_writer_tick(redis_c, store, now=LATER)  # an extra tick changes nothing
    # …and lands as an UPDATE on the segment identity — one row, latest text, never a duplicate.
    assert _durable_texts(store) == ["polished"]


async def test_db_writer_discovers_hash_missing_from_active_set_only_on_reconcile(store, redis_c):
    """Self-healing discovery (#893): a hash written before the sweep set existed (mid-upgrade) — NO
    sadd, so it is invisible to the authoritative ``active_meetings`` set — is drained by the
    ``meeting:*:segments`` key scan. That scan is now OFF the per-tick hot path: a plain tick
    (``reconcile=False``) does NOT find the orphan; only a ``reconcile=True`` tick does."""
    seg = {**_seg("s9", 3.0, "orphaned"), "updated_at": "2026-06-20T09:00:00Z"}
    await redis_c.hset(segments_hash_key(1), "s9", json.dumps(seg))  # NO sadd → not in active_meetings

    # hot path — the set-only sweep never scans the keyspace, so the orphan is NOT discovered.
    assert await db_writer_tick(redis_c, store, now=LATER) == 0
    assert _durable_texts(store) == []

    # reconcile tick — the self-healing scan runs and drains it (the intent, preserved off the hot path).
    assert await db_writer_tick(redis_c, store, now=LATER, reconcile=True) == 1
    assert _durable_texts(store) == ["orphaned"]


class _ScanCountingRedis:
    """Forwards every call to the wrapped client but COUNTS ``scan_iter`` invocations — the direct
    proof that the O(keyspace) ``meeting:*:segments`` SCAN is off the db-writer hot path (#893). The
    per-tick scan was the #1 redis command in prod (31.6M calls), saturating redis and starving the
    bounded /health PING/XINFO/XPENDING past the 5s probe timeout, restarting healthy pods."""

    def __init__(self, inner):
        self._inner = inner
        self.scan_calls = 0

    def scan_iter(self, *args, **kwargs):
        self.scan_calls += 1
        return self._inner.scan_iter(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


async def test_hot_tick_does_not_scan_keyspace_reconcile_does(store, bus, redis_c):
    """#893 bounded-behavior: a plain db-writer tick issues ZERO keyspace scans while still flushing
    the active-set meetings (the set is authoritative — ``append_segment`` SADDs atomically with the
    hash write). The self-healing scan fires ONLY on a reconcile tick. This is the saturation fix:
    N hot ticks cost 0 scans instead of N, so a busy redis no longer starves the health probe."""
    await bus.xadd("transcription_segments", json.loads(_message(1, [_seg("s1", 0.0, "hello")])["payload"]))
    assert await consume_segments(store, bus) == 1

    spy = _ScanCountingRedis(redis_c)

    # Many hot ticks: the set is swept every time, the keyspace SCAN never runs.
    for _ in range(20):
        await db_writer_tick(spy, store, now=LATER)
    assert spy.scan_calls == 0
    assert _durable_texts(store) == ["hello"]  # set-only discovery still flushes durably

    # A reconcile tick is the ONLY place the O(keyspace) scan is paid.
    await db_writer_tick(spy, store, now=LATER, reconcile=True)
    assert spy.scan_calls == 1


# ── (b) the flipped incident — redis wiped after the flush ──────────────────────────────────────

async def test_flipped_incident_redis_wiped_after_flush_get_transcript_survives(store, bus, redis_c):
    """THE incident, flipped: segments flushed to durable, then redis loses everything (eviction /
    restart — live rc.4 had 3 of 6 hashes already gone). GET /transcripts must still serve the
    transcript from the durable store."""
    from meeting_api import create_app

    await bus.xadd("transcription_segments", json.loads(_message(1, [
        _seg("s1", 1.0, "Hello"), _seg("s2", 2.5, "world"),
    ])["payload"]))
    await consume_segments(store, bus)
    await db_writer_tick(redis_c, store, now=LATER)

    await redis_c.flushall()  # redis is GONE — the pre-fix stack lost the transcript here

    client = TestClient(create_app(transcript_store=store))
    r = client.get(f"/transcripts/google_meet/{NATIVE}", headers={"x-user-id": str(USER)})
    assert r.status_code == 200
    assert [s["text"] for s in r.json()["segments"]] == ["Hello", "world"]


async def test_unflushed_segments_are_lost_without_the_db_writer(store, bus, redis_c):
    """The control: WITHOUT a db-writer tick a redis wipe loses everything — this is exactly the
    production defect; the writer tick is what makes the difference in the test above."""
    await bus.xadd("transcription_segments", json.loads(_message(1, [_seg("s1", 1.0, "Hello")])["payload"]))
    await consume_segments(store, bus)
    await redis_c.flushall()
    doc = await store.get_transcript(USER, "google_meet", NATIVE)
    assert doc["segments"] == []


# ── parent semantics: mutable tail, empty text, trim-after-confirm ───────────────────────────────

async def test_mutable_tail_stays_in_redis_until_it_settles(store, bus, redis_c):
    """IMMUTABILITY_THRESHOLD (parent): a segment updated moments ago is still being refined —
    it must NOT flush yet, but the read path still serves it live from the hash merge."""
    await bus.xadd("transcription_segments", json.loads(_message(1, [_seg("s1", 1.0, "fresh")])["payload"]))
    await consume_segments(store, bus)

    stored = await db_writer_tick(redis_c, store)  # real `now` — the segment is seconds old
    assert stored == 0
    assert await redis_c.hlen(segments_hash_key(1)) == 1   # untouched, still mutable
    members = {m.decode() if isinstance(m, bytes) else m
               for m in await redis_c.smembers(ACTIVE_MEETINGS_KEY)}
    assert "1" in members                                   # stays in the sweep set for the next tick
    doc = await store.get_transcript(USER, "google_meet", NATIVE)
    assert [s["text"] for s in doc["segments"]] == ["fresh"]  # live read merge


# ── M21: the hold is per-LANE, because only a lane that refines in place needs it ───────────────

async def test_teams_csrc_confirmed_segment_flushes_without_waiting_out_the_hold(store, bus, redis_c):
    """The Teams CSRC lane retracts drafts instead of refining rows in place, so a CONFIRMED
    segment of its is durable-safe the moment it exists. Measured cost of
    the old behaviour on prod meeting 26088: 36.2 s of a 39.4 s wait, against 2.0 s of model."""
    seg = {**_seg("csrc-201:3:0", 1.0, "shipped now"), "source": "merged", "speaker_key": "csrc:201"}
    await bus.xadd("transcription_segments", json.loads(_message(1, [seg])["payload"]))
    await consume_segments(store, bus)

    stored = await db_writer_tick(redis_c, store)  # real `now` — the segment is milliseconds old
    assert stored == 1
    assert _durable_texts(store) == ["shipped now"]


async def test_teams_csrc_PENDING_still_waits_out_the_hold(store, bus, redis_c):
    """A draft must never become a durable row: the durable read reports every stored row as
    completed, so a flushed pending would read back as final until its retract caught up."""
    seg = {**_seg("csrc-201:3:p0", 1.0, "still forming", completed=False), "source": "merged", "speaker_key": "csrc:201"}
    await bus.xadd("transcription_segments", json.loads(_message(1, [seg])["payload"]))
    await consume_segments(store, bus)

    assert await db_writer_tick(redis_c, store) == 0
    assert await redis_c.hlen(segments_hash_key(1)) == 1


async def test_gmeet_lane_confirmed_segment_still_waits_out_the_hold(store, bus, redis_c):
    """THE FALSIFIER. The gmeet lane republishes confirmed text under a rotated id and withdraws
    the old row with EMPTY TEXT, which never deletes an already-flushed row — so its hold is the
    only thing preventing an orphaned stale row, and this change must not touch it."""
    seg = {**_seg("ch-1:5:1200", 1.0, "refined in place"), "source": "glow-bound"}
    await bus.xadd("transcription_segments", json.loads(_message(1, [seg])["payload"]))
    await consume_segments(store, bus)

    assert await db_writer_tick(redis_c, store) == 0
    assert await redis_c.hlen(segments_hash_key(1)) == 1
    assert await flush_meeting_segments(redis_c, store, 1, now=LATER) == 1  # settles normally


async def test_legacy_mixed_lane_confirmed_segment_keeps_the_hold(store, bus, redis_c):
    """A source=merged row without a CSRC speaker key is Zoom/Jitsi legacy traffic and remains
    outside the Teams-only release blast radius."""
    seg = {**_seg("turn:3:0", 1.0, "legacy mixed"), "source": "merged"}
    await bus.xadd("transcription_segments", json.loads(_message(1, [seg])["payload"]))
    await consume_segments(store, bus)

    assert await db_writer_tick(redis_c, store) == 0
    assert await redis_c.hlen(segments_hash_key(1)) == 1


async def test_finalize_still_flushes_everything_including_other_lanes(store, bus, redis_c):
    """threshold_for never RAISES a caller's threshold — finalize (threshold 0) still takes the
    whole tail, mixed pendings and gmeet rows included."""
    await bus.xadd("transcription_segments", json.loads(_message(1, [
        {**_seg("turn:9:p0", 1.0, "draft tail", completed=False), "source": "merged"},
        {**_seg("ch-1:9:900", 2.0, "gmeet tail"), "source": "glow-bound"},
    ])["payload"]))
    await consume_segments(store, bus)
    assert await flush_meeting_segments(redis_c, store, 1, immutability_threshold=0) == 2


async def test_empty_text_segments_are_dropped_not_stored(store, redis_c):
    seg = {**_seg("s1", 1.0, "   "), "updated_at": "2026-06-20T09:00:00Z"}
    await redis_c.hset(segments_hash_key(1), "s1", json.dumps(seg))
    assert await flush_meeting_segments(redis_c, store, 1, now=LATER) == 0
    assert _durable_texts(store) == []
    assert await redis_c.hlen(segments_hash_key(1)) == 0  # trimmed from the hash all the same


async def test_redis_is_trimmed_only_after_a_confirmed_durable_write(store, bus, redis_c):
    """Trim-after-confirm: a failing durable sink leaves the hash INTACT for the next tick —
    a flaky Postgres must never cost the transcript its redis copy."""
    await bus.xadd("transcription_segments", json.loads(_message(1, [_seg("s1", 1.0, "keep me")])["payload"]))
    await consume_segments(store, bus)

    class _FailingSink:
        async def upsert_segments(self, meeting_id, segments):
            raise RuntimeError("postgres is down")

    with pytest.raises(RuntimeError):
        await flush_meeting_segments(redis_c, _FailingSink(), 1, now=LATER)
    assert await redis_c.hlen(segments_hash_key(1)) == 1  # NOT trimmed — nothing was confirmed

    # The next (healthy) tick drains it.
    assert await db_writer_tick(redis_c, store, now=LATER) == 1
    assert _durable_texts(store) == ["keep me"]


# ── (c) completion finalizes — terminal lifecycle advance ⇒ immediate durable flush ─────────────

async def _terminal_app_and_stores(redis_c):
    """The unified app wired the way __main__ wires production: a redis-topology store + the
    db_writer finalizer hooked into the lifecycle callback."""
    from meeting_api import create_app
    from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo

    store = InMemoryTranscriptStore(redis_client=redis_c)
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id=NATIVE, meeting_id=1)
    repo = InMemoryMeetingRepo()
    m = await repo.create_meeting(user_id=USER, platform="google_meet",
                                  native_meeting_id=NATIVE, data={})
    assert m["id"] == 1  # the repo row and the store meeting are the SAME meeting
    await repo.create_session(meeting_id=1, session_uid="sess-uid")

    async def _finalizer(meeting_id: int) -> None:
        await finalize_meeting(redis_c, store, meeting_id)

    app = create_app(transcript_store=store, meeting_repo=repo, transcript_finalizer=_finalizer)
    return TestClient(app), store


async def test_completed_meeting_transcript_is_flushed_immediately(redis_c, goldens):
    """The bot's terminal callback ⇒ the finalizer flushes EVERYTHING still in the hash (threshold
    0 — mutable tail included; nothing else is coming) so the finished transcript is durable at the
    moment of completion, not `whenever the next periodic tick runs`."""
    client, store = await _terminal_app_and_stores(redis_c)
    # Live segments seconds old (still "mutable") — the periodic tick would have skipped them.
    await store.append_segment(1, {**_seg("s1", 1.0, "last words"),
                                   "updated_at": datetime.now(timezone.utc).isoformat()})

    for case in ("joining", "active", "completed-stopped"):
        assert client.post("/bots/internal/callback/lifecycle", json=goldens[case]).status_code == 200

    assert _durable_texts(store) == ["last words"]          # durable NOW
    assert await redis_c.hlen(segments_hash_key(1)) == 0    # hash drained


async def test_nonterminal_advance_does_not_finalize(redis_c, goldens):
    client, store = await _terminal_app_and_stores(redis_c)
    await store.append_segment(1, {**_seg("s1", 1.0, "mid-meeting"),
                                   "updated_at": datetime.now(timezone.utc).isoformat()})
    for case in ("joining", "active"):
        client.post("/bots/internal/callback/lifecycle", json=goldens[case])
    assert _durable_texts(store) == []                      # not finalized — the meeting is live
    assert await redis_c.hlen(segments_hash_key(1)) == 1


# ── the REST surface, during AND after (DoD 8): api.v1 responses, both phases ───────────────────

def _rest(store):
    from meeting_api import create_app

    return TestClient(create_app(transcript_store=store))


async def test_rest_mid_meeting_serves_merged_postgres_plus_redis_tail(store, bus, redis_c):
    """DURING the meeting: GET /transcripts merges the durable rows (flushed by earlier ticks) with
    the still-mutable redis tail — the caller sees ONE complete live transcript, and the body
    conforms to the sealed api.v1 TranscriptionResponse."""
    from collector_contracts import assert_api_conforms

    # An older utterance, already flushed durable by a previous tick…
    await bus.xadd("transcription_segments", json.loads(_message(1, [_seg("s1", 1.0, "flushed part")])["payload"]))
    await consume_segments(store, bus)
    await db_writer_tick(redis_c, store, now=LATER)
    # …and the live tail, seconds old, still ONLY in the redis hash.
    await bus.xadd("transcription_segments", json.loads(_message(1, [_seg("s2", 2.5, "live tail")])["payload"]))
    await consume_segments(store, bus)
    assert await redis_c.hlen(segments_hash_key(1)) == 1

    r = _rest(store).get(f"/transcripts/google_meet/{NATIVE}", headers={"x-user-id": str(USER)})
    assert r.status_code == 200
    body = r.json()
    assert [s["text"] for s in body["segments"]] == ["flushed part", "live tail"]
    assert_api_conforms("TranscriptionResponse", body)


async def test_rest_after_completion_with_redis_wiped_serves_the_transcript(redis_c, goldens):
    """AFTER the meeting — the observed defect: stop the bot, redis evicted ⇒ (pre-fix) the
    transcript read EMPTY. Post-fix: completion finalizes it into postgres and GET /transcripts
    serves the full transcript from the durable row alone — conformant to the sealed api.v1 shape.

    It also used to assert a second body on the same response: ``data.processed.views[]``, the
    copilot's cleaned view. PRD decision 34 removed the producer; a legacy row may still carry the
    key (the free-form ``data`` field is unchanged), but nothing writes or reads one."""
    from collector_contracts import assert_api_conforms

    client, store = await _terminal_app_and_stores(redis_c)
    await store.append_segment(1, {**_seg("s1", 1.0, "closing words"),
                                   "updated_at": datetime.now(timezone.utc).isoformat()})

    for case in ("joining", "active", "completed-stopped"):
        assert client.post("/bots/internal/callback/lifecycle", json=goldens[case]).status_code == 200

    await redis_c.flushall()  # the eviction that used to be unrecoverable

    r = client.get(f"/transcripts/google_meet/{NATIVE}", headers={"x-user-id": str(USER)})
    assert r.status_code == 200
    body = r.json()
    assert [s["text"] for s in body["segments"]] == ["closing words"]
    assert not (body.get("data") or {}).get("processed")
    assert_api_conforms("TranscriptionResponse", body)
