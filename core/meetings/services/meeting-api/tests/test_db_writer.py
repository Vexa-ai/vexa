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
  * completion finalization — the lifecycle callback's terminal advance flushes EVERYTHING left;
  * processed-doc durability — ``proc:meeting:{row_id}`` notes persist into ``data['processed']``,
    and a SECOND meeting on the same native link never clobbers the first row's persisted doc.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from meeting_api.collector import consume_segments
from meeting_api.collector.ports import TranscriptWriteRefused
from meeting_api.collector.db_writer import (
    ACTIVE_MEETINGS_KEY,
    PROC_PENDING_KEY,
    PROC_VIEW_ID,
    db_writer_tick,
    finalize_meeting,
    flush_meeting_processed,
    flush_meeting_segments,
    proc_stream_key,
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


async def test_db_writer_discovers_hash_missing_from_active_set(store, redis_c):
    """Self-healing discovery: a hash written before the sweep set existed (mid-upgrade) is still
    found by the key scan and drained."""
    seg = {**_seg("s9", 3.0, "orphaned"), "updated_at": "2026-06-20T09:00:00Z"}
    await redis_c.hset(segments_hash_key(1), "s9", json.dumps(seg))  # NO sadd
    assert await db_writer_tick(redis_c, store, now=LATER) == 1
    assert _durable_texts(store) == ["orphaned"]


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


async def test_withdrawal_refusal_purges_buffer_instead_of_retrying_pii(store, bus, redis_c):
    await bus.xadd(
        "transcription_segments",
        json.loads(_message(1, [_seg("s1", 1.0, "do not retain")])["payload"]),
    )
    await consume_segments(store, bus)

    class _WithdrawnSink:
        async def upsert_segments(self, meeting_id, segments):
            raise TranscriptWriteRefused("meeting is not writable")

    assert await flush_meeting_segments(redis_c, _WithdrawnSink(), 1, now=LATER) == 0
    assert await redis_c.hlen(segments_hash_key(1)) == 0
    assert await redis_c.smembers(ACTIVE_MEETINGS_KEY) == set()


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


# ── (d) processed-doc durability + the re-send clobber fix ──────────────────────────────────────

def _note(nid: str, text: str) -> dict:
    return {"id": nid, "speaker": "Alice", "text": text}


def _view(store, meeting_id: int, view_id: str = PROC_VIEW_ID) -> dict:
    """The persisted processed VIEW — data.processed.views[] upserted by id (the addressable,
    versioned multi-consumer shape the release DoD rules)."""
    views = store._meetings[meeting_id]["data"]["processed"]["views"]
    return next(v for v in views if v["id"] == view_id)


async def test_processed_doc_persists_into_meeting_data_as_versioned_view(store, redis_c):
    params = {"provider": "anthropic", "model": "claude-x", "pipeline": "meeting-copilot/proc-notes", "version": 1}
    await redis_c.xadd(proc_stream_key(1), {"note": json.dumps(_note("s1", "Cleaned one.")),
                                            "params": json.dumps(params)})
    await redis_c.xadd(proc_stream_key(1), {"note": json.dumps(_note("s2", "Cleaned two.")),
                                            "params": json.dumps(params)})

    assert await flush_meeting_processed(redis_c, store, 1) == 2
    view = _view(store, 1)
    assert view["kind"] == "cleaned_transcript"
    assert [n["text"] for n in view["doc"]["notes"]] == ["Cleaned one.", "Cleaned two."]
    assert view["params"] == params        # the processing metadata APPLIED — reproducibility
    assert view["source_cursor"]           # the stream position this view reflects
    assert view["updated_at"]

    # Cursor resume: nothing new ⇒ nothing re-merged; a refining re-emit UPDATES in place.
    assert await flush_meeting_processed(redis_c, store, 1) == 0
    await redis_c.xadd(proc_stream_key(1), {"note": json.dumps(_note("s2", "Cleaned two, better."))})
    assert await flush_meeting_processed(redis_c, store, 1) == 1
    view = _view(store, 1)
    assert [n["text"] for n in view["doc"]["notes"]] == ["Cleaned one.", "Cleaned two, better."]
    assert view["params"] == params        # a params-less drain never erases provenance


async def test_withdrawal_refuses_processed_notes_and_purges_pending_pii(store, redis_c):
    store._meetings[1]["data"]["zaki_capture"] = {"state": "withdrawn"}
    await redis_c.xadd(
        proc_stream_key(1),
        {"note": json.dumps(_note("s1", "participant private detail"))},
    )
    await redis_c.zadd(PROC_PENDING_KEY, {"1": 9_999_999_999})

    merged = await flush_meeting_processed(redis_c, store, 1)

    assert merged == 0
    assert "processed" not in store._meetings[1]["data"]
    assert await redis_c.xlen(proc_stream_key(1)) == 0
    assert await redis_c.zscore(PROC_PENDING_KEY, "1") is None


async def test_processed_views_are_multi_consumer_other_views_preserved(store, redis_c):
    """The views LIST is the multi-consumer seam: a future per-workspace/other processing's view
    must survive the copilot view's upsert untouched."""
    other = {"id": "ws-team:summary", "kind": "summary", "params": {"model": "m"},
             "doc": {"text": "…"}, "source_cursor": "9-0", "updated_at": "2026-06-20T09:00:00Z"}
    store._meetings[1]["data"]["processed"] = {"views": [dict(other)]}
    await redis_c.xadd(proc_stream_key(1), {"note": json.dumps(_note("s1", "Copilot note."))})

    await flush_meeting_processed(redis_c, store, 1)
    views = store._meetings[1]["data"]["processed"]["views"]
    assert [v["id"] for v in views] == ["ws-team:summary", PROC_VIEW_ID]
    assert views[0] == other  # untouched


async def test_second_meeting_on_same_native_does_not_clobber_first_processed_doc(redis_c):
    """The clobber defect: proc docs were keyed by the NATIVE id, which a re-sent bot REUSES —
    meeting 2's copilot output landed on meeting 1's doc. Keyed by the ROW id and persisted per
    row, each meeting keeps its own processed doc across completion and a re-send."""
    store = InMemoryTranscriptStore(redis_client=redis_c)
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id=NATIVE, meeting_id=1,
                       created_at="2026-06-20T08:59:00Z")
    await redis_c.xadd(proc_stream_key(1), {"note": json.dumps(_note("a1", "First meeting note."))})
    await finalize_meeting(redis_c, store, 1)  # meeting 1 completes; its doc is durable

    # The bot is RE-SENT to the same native link → a NEW meeting row (id 2), its own proc stream.
    store.seed_meeting(user_id=USER, platform="google_meet", native_meeting_id=NATIVE, meeting_id=2,
                       created_at="2026-06-20T10:00:00Z")
    await redis_c.xadd(proc_stream_key(2), {"note": json.dumps(_note("b1", "Second meeting note."))})
    await finalize_meeting(redis_c, store, 2)

    first = _view(store, 1)["doc"]["notes"]
    second = _view(store, 2)["doc"]["notes"]
    assert [n["text"] for n in first] == ["First meeting note."]    # SURVIVED the re-send
    assert [n["text"] for n in second] == ["Second meeting note."]


async def test_db_writer_tick_also_drains_processed_notes(store, bus, redis_c):
    """The periodic tick persists the processed doc for ACTIVE meetings too (not only at
    completion) — a crash mid-meeting keeps everything cleaned so far."""
    await bus.xadd("transcription_segments", json.loads(_message(1, [_seg("s1", 1.0, "raw")])["payload"]))
    await consume_segments(store, bus)  # puts meeting 1 in active_meetings
    await redis_c.xadd(proc_stream_key(1), {"note": json.dumps(_note("s1", "Cleaned mid-meeting."))})

    await db_writer_tick(redis_c, store, now=LATER)
    assert [n["text"] for n in _view(store, 1)["doc"]["notes"]] == ["Cleaned mid-meeting."]


# ── the end-of-processing protocol (ADR 0027 / processed-notes.v1 view_end) ─────────────────────
# The copilot's final beat lands ~10s AFTER session_end, i.e. AFTER finalize_meeting's inline drain
# — and the meeting then leaves the tick's sweep, so those notes were stranded in redis forever
# (run-46: durable cursor froze below the stream tail). The protocol: finalize PARKS an
# incomplete meeting in `processed_pending`; the tick re-drains it until the worker's `view_end`
# marker is drained-through (or a bounded deadline passes — the dead-worker guarantee).

async def _pending_ids(redis_c):
    return [m.decode() if isinstance(m, bytes) else m
            for m in await redis_c.zrange(PROC_PENDING_KEY, 0, -1)]


async def test_late_final_beat_notes_land_after_finalize_via_view_end(store, redis_c):
    """The run-46 regression: notes written AFTER the completion flush reach the durable row on the
    next tick, and the parking clears exactly when the view_end marker is drained-through."""
    await redis_c.xadd(proc_stream_key(1), {"note": json.dumps(_note("s1", "Mid-meeting note."))})
    await finalize_meeting(redis_c, store, 1)
    assert [n["text"] for n in _view(store, 1)["doc"]["notes"]] == ["Mid-meeting note."]
    assert await _pending_ids(redis_c) == ["1"]        # no marker yet → parked, not forgotten

    # The final post-session_end beat lands AFTER the finalize drain — then the marker.
    await redis_c.xadd(proc_stream_key(1), {"note": json.dumps(_note("s1", "Final polished note."))})
    marker_id = await redis_c.xadd(proc_stream_key(1), {"type": "view_end", "cursor": "9-0"})

    await db_writer_tick(redis_c, store, now=LATER)
    view = _view(store, 1)
    assert [n["text"] for n in view["doc"]["notes"]] == ["Final polished note."]  # upgraded in place
    assert view["source_cursor"] == (marker_id.decode() if isinstance(marker_id, bytes) else marker_id)
    assert await _pending_ids(redis_c) == []           # drained-through the marker → unparked


async def test_finalize_already_marker_complete_does_not_park(store, redis_c):
    """A worker that finished BEFORE the terminal callback (marker already on the stream): the
    finalize drain goes through the marker in one pass — nothing parks."""
    await redis_c.xadd(proc_stream_key(1), {"note": json.dumps(_note("s1", "Done early."))})
    await redis_c.xadd(proc_stream_key(1), {"type": "view_end"})

    await finalize_meeting(redis_c, store, 1)
    assert [n["text"] for n in _view(store, 1)["doc"]["notes"]] == ["Done early."]
    assert await _pending_ids(redis_c) == []


async def test_pending_redrain_gives_up_at_deadline_keeping_what_arrived(store, redis_c):
    """A worker that died markerless: the parking expires at its deadline (bounded, P22's hard
    guarantee) — everything that DID arrive is durable, the zset never grows unbounded."""
    await redis_c.xadd(proc_stream_key(1), {"note": json.dumps(_note("s1", "Only note."))})
    await finalize_meeting(redis_c, store, 1)
    assert await _pending_ids(redis_c) == ["1"]

    past_deadline = datetime.now(timezone.utc) + timedelta(seconds=600)  # > PROC_PENDING_GRACE_SEC
    await db_writer_tick(redis_c, store, now=past_deadline)
    assert await _pending_ids(redis_c) == []                             # gave up, loudly (logged)
    assert [n["text"] for n in _view(store, 1)["doc"]["notes"]] == ["Only note."]  # kept


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


async def test_rest_after_completion_with_redis_wiped_serves_transcript_and_processed_view(redis_c, goldens):
    """AFTER the meeting — the observed defects, both: stop the bot, redis evicted ⇒ (pre-fix) the
    transcript read EMPTY and the processed output was UNREACHABLE. Post-fix: completion finalizes
    both into postgres (meeting.data JSONB), and GET /transcripts serves the full transcript AND the
    processed view from the durable row alone — conformant to the sealed api.v1 shape."""
    from collector_contracts import assert_api_conforms

    client, store = await _terminal_app_and_stores(redis_c)
    await store.append_segment(1, {**_seg("s1", 1.0, "closing words"),
                                   "updated_at": datetime.now(timezone.utc).isoformat()})
    await redis_c.xadd(proc_stream_key(1), {"note": json.dumps(_note("s1", "Closing words, cleaned.")),
                                            "params": json.dumps({"model": "claude-x"})})

    for case in ("joining", "active", "completed-stopped"):
        assert client.post("/bots/internal/callback/lifecycle", json=goldens[case]).status_code == 200

    await redis_c.flushall()  # the eviction that used to be unrecoverable

    r = client.get(f"/transcripts/google_meet/{NATIVE}", headers={"x-user-id": str(USER)})
    assert r.status_code == 200
    body = r.json()
    assert [s["text"] for s in body["segments"]] == ["closing words"]
    views = body["data"]["processed"]["views"]  # rides the existing free-form data field — no new surface
    assert views[0]["id"] == PROC_VIEW_ID and views[0]["kind"] == "cleaned_transcript"
    assert [n["text"] for n in views[0]["doc"]["notes"]] == ["Closing words, cleaned."]
    assert views[0]["params"] == {"model": "claude-x"}
    assert_api_conforms("TranscriptionResponse", body)


# ── stop is not withdrawal (owner round-4: "the bot was there, I spoke, nothing surfaced") ──────

async def test_stopped_meeting_still_flushes_its_transcript(store, bus, redis_c):
    """An ordinary STOP tombstones the capture AUTHORITY (state=withdrawn, reason
    capture_stopped) so no future capture rides the old grant — but the segments already
    captured under valid consent MUST land durably. Before the reason split, the delayed
    db-writer flush hit the privacy barrier on every stopped meeting and silently purged
    the whole buffered transcript."""
    await bus.xadd("transcription_segments", json.loads(_message(1, [
        _seg("s1", 1.0, "Thank you if you can hear me."),
    ])["payload"]))
    await consume_segments(store, bus)
    store._meetings[1]["data"]["zaki_capture"] = {
        "state": "withdrawn", "withdrawal_reason": "capture_stopped",
        "withdrawn_at": "2026-07-21T13:27:00+00:00",
    }

    stored = await flush_meeting_segments(redis_c, store, 1, now=LATER)

    assert stored == 1
    assert _durable_texts(store) == ["Thank you if you can hear me."]


async def test_privacy_withdrawal_still_purges_buffered_segments(store, bus, redis_c):
    """The REAL privacy barrier is unchanged: a consent withdrawal refuses the flush and
    purges the buffer — and an unknown/missing reason fails closed the same way."""
    await bus.xadd("transcription_segments", json.loads(_message(1, [
        _seg("s1", 1.0, "private detail"),
    ])["payload"]))
    await consume_segments(store, bus)
    store._meetings[1]["data"]["zaki_capture"] = {
        "state": "withdrawn", "withdrawal_reason": "consent_withdrawn",
    }

    stored = await flush_meeting_segments(redis_c, store, 1, now=LATER)

    assert stored == 0
    assert _durable_texts(store) == []
    assert await redis_c.hlen("meeting:1:segments") == 0
