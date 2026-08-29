"""captured-signal tapes (O-TEL-1) — the upload leg and the keep-side budget janitor.

Drives the SHIPPED ``upload_signal_tape`` / ``build_router`` / ``sweep_signal_tapes`` over the
in-memory fakes, OFFLINE (no MinIO, no DB). Two halves, and the second is the one that matters:

  * **upload** — a tape lands under its own ``signal/`` keyspace and does NOT become a recording.
    The negative assertion is the point: fixture collection is default ON, so if a tape folded into
    ``meeting.data['recordings']`` every user would grow a phantom recording for every meeting they
    never asked to record.
  * **the budget** — collection is unbounded on the capture side by design, so the janitor is the
    ONLY thing keeping the bucket finite. Oldest-unpromoted out, promoted spared, whole tapes only,
    in-flight uploads untouched, and a loud refusal when nothing is left to reclaim.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import mint_meeting_token
from meeting_api.recordings import (
    SIGNAL_PROMOTED_MARKER,
    build_router,
    signal_tape_prefix,
    sweep_signal_tapes,
    upload_signal_tape,
)
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage

SECRET = "test-admin-token"
INTERNAL = "test-internal-secret"
USER = 7
MEETING_ID = 1
SESSION_UID = "conn-abc"

TAPE = b'{"type":"captured_signal_header","v":1}\n{"seq":0,"pcm":"AA=="}\n'
STT_TAPE = b'{"at":"2026-08-11T00:00:00Z","ok":true,"text":"hello"}\n'
CAPTION_TAPE = b'{"type":"caption","t":1,"platform":"teams","name":"Jacob","text":"hi","stable":true,"lane":"mixed"}\n'
CSRC_TAPE = b'{"type":"csrc","t":1,"csrc":424242,"active":true,"lane":"mixed"}\n'
OBSERVATION_TAPE = (
    b'{"type":"observation","t":1,"source":"csrc","lane":"mixed",'
    b'"observation":{"kind":"csrc-poll-error"}}\n'
)


def _seeded():
    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    return repo, InMemoryStorage()


def _client_for(repo, storage):
    app = FastAPI()
    app.include_router(build_router(repo, storage, token_secret=SECRET))
    return TestClient(app)


def _post_tape(client, *, part, data=TAPE, session_uid=SESSION_UID, fmt="jsonl", auth=None):
    token = auth or mint_meeting_token(MEETING_ID, USER, "google_meet", "abc", secret=SECRET)
    return client.post(
        "/internal/recordings/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (f"{part}.{fmt}", data, "application/x-ndjson")},
        data={"metadata": '{"session_uid": "%s", "media_type": "signal", '
                          '"media_format": "%s", "part": "%s"}' % (session_uid, fmt, part)},
    )


# ── the upload leg ──────────────────────────────────────────────────────────────────────────────

async def test_signal_tape_lands_in_its_own_keyspace_and_is_not_a_recording():
    repo, storage = _seeded()
    receipt = await upload_signal_tape(
        repo, storage, token_meeting_id=None, session_uid=SESSION_UID,
        data=TAPE, part="captured-signal",
    )
    prefix = signal_tape_prefix(user_id=USER, meeting_id=MEETING_ID, session_uid=SESSION_UID)
    assert receipt["storage_path"] == f"{prefix}captured-signal.jsonl"
    assert receipt["bytes"] == len(TAPE)
    assert storage.blobs[receipt["storage_path"]] == TAPE
    assert storage.content_types[receipt["storage_path"]] == "application/x-ndjson"

    # THE point of the separate path: a tape must never surface as one of the user's recordings.
    assert await repo.get_recordings(MEETING_ID) == []
    assert await repo.list_meeting_recordings(USER) == []


async def test_every_tape_part_shares_one_prefix():
    """The frame tape and its sidecars are ONE fixture — audio, speaker hints, STT round-trips and
    Teams captions, transport transitions and the capture path's own observations together are what
    lets a replay reproduce the decision the live bot made — and what it noticed while making it.
    They must land together, or a curator reconstructs the pairing by hand and the janitor (which
    evicts by prefix) treats them as unrelated objects."""
    repo, storage = _seeded()
    keys = []
    for part, data in (("captured-signal", TAPE), ("stt", STT_TAPE), ("captions", CAPTION_TAPE),
                       ("csrc", CSRC_TAPE), ("observations", OBSERVATION_TAPE)):
        r = await upload_signal_tape(repo, storage, token_meeting_id=None,
                                     session_uid=SESSION_UID, data=data, part=part)
        keys.append(r["storage_path"])
    assert len({k.rsplit("/", 1)[0] for k in keys}) == 1, keys
    assert sorted(k.rsplit("/", 1)[-1] for k in keys) == [
        "captions.jsonl", "captured-signal.jsonl", "csrc.jsonl", "observations.jsonl", "stt.jsonl",
    ]


async def test_signal_tape_upload_is_idempotent_by_key():
    """A re-upload overwrites rather than accumulating — the bot uploads once at teardown, but a
    retry at any layer must not double the bucket's tape bytes."""
    repo, storage = _seeded()
    for _ in range(3):
        await upload_signal_tape(repo, storage, token_meeting_id=None, session_uid=SESSION_UID,
                                 data=TAPE, part="captured-signal")
    assert len([k for k in storage.blobs if k.startswith("signal/")]) == 1


async def test_signal_tape_rejects_unknown_part_and_format():
    from meeting_api.recordings import InvalidSignalTape

    repo, storage = _seeded()
    # The part name lands in an object key, so it is a closed set — never caller-shaped.
    with pytest.raises(InvalidSignalTape):
        await upload_signal_tape(repo, storage, token_meeting_id=None, session_uid=SESSION_UID,
                                 data=TAPE, part="../../etc/passwd")
    with pytest.raises(InvalidSignalTape):
        await upload_signal_tape(repo, storage, token_meeting_id=None, session_uid=SESSION_UID,
                                 data=TAPE, part="captured-signal", media_format="wav")
    assert storage.blobs == {}


async def test_signal_tape_for_unknown_session_is_refused():
    from meeting_api.recordings import SessionNotFound

    repo, storage = _seeded()
    with pytest.raises(SessionNotFound):
        await upload_signal_tape(repo, storage, token_meeting_id=None, session_uid="no-such",
                                 data=TAPE, part="captured-signal")
    assert storage.blobs == {}


async def test_signal_tape_token_scoped_to_its_own_meeting():
    """Fail closed, exactly like a chunk upload: a MeetingToken minted for another meeting must not
    be able to write into this session's prefix."""
    from meeting_api.recordings import SessionNotFound

    repo, storage = _seeded()
    with pytest.raises(SessionNotFound):
        await upload_signal_tape(repo, storage, token_meeting_id=MEETING_ID + 999,
                                 session_uid=SESSION_UID, data=TAPE, part="captured-signal")


def test_upload_route_accepts_a_signal_tape(monkeypatch):
    repo, storage = _seeded()
    client = _client_for(repo, storage)
    r = _post_tape(client, part="captured-signal")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "stored"
    assert body["storage_path"] == (
        signal_tape_prefix(user_id=USER, meeting_id=MEETING_ID, session_uid=SESSION_UID)
        + "captured-signal.jsonl"
    )
    # The user-facing listing stays empty — no phantom recording.
    assert client.get("/recordings", headers={"x-user-id": str(USER)}).json()["recordings"] == []


def test_upload_route_accepts_the_internal_secret(monkeypatch):
    """The bot authenticates its tape with INTERNAL_API_SECRET, the same credential the recording
    chunks and the lifecycle callback already use — no new secret crosses to the bot for this."""
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL)
    repo, storage = _seeded()
    r = _post_tape(_client_for(repo, storage), part="stt", data=STT_TAPE, auth=INTERNAL)
    assert r.status_code == 200, r.text


def test_upload_route_rejects_a_bad_part_and_an_unknown_session():
    repo, storage = _seeded()
    client = _client_for(repo, storage)
    assert _post_tape(client, part="bogus").status_code == 422
    assert _post_tape(client, part="captured-signal", session_uid="nope").status_code == 404
    assert storage.blobs == {}


def test_upload_route_still_serves_normal_recording_chunks():
    """Regression guard on the branch itself: the signal path is an early return inside the shared
    endpoint, so an ordinary audio chunk must be untouched by it."""
    import struct

    def _wav(n=4):
        data = b"\x00" * n
        fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
        chunk = struct.pack("<4sI", b"data", len(data)) + data
        riff = 4 + len(fmt) + len(chunk)
        return struct.pack("<4sI4s", b"RIFF", riff, b"WAVE") + fmt + chunk

    repo, storage = _seeded()
    client = _client_for(repo, storage)
    token = mint_meeting_token(MEETING_ID, USER, "google_meet", "abc", secret=SECRET)
    r = client.post(
        "/internal/recordings/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("chunk.wav", _wav(), "audio/wav")},
        data={"metadata": '{"session_uid": "%s", "format": "wav"}' % SESSION_UID},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"
    assert r.json()["storage_path"].startswith("recordings/")


# ── the keep-side budget ────────────────────────────────────────────────────────────────────────

def _seed_tape(storage: InMemoryStorage, *, meeting_id: int, size: int, mtime: float,
               promoted: bool = False, user_id: int = USER) -> str:
    prefix = signal_tape_prefix(user_id=user_id, meeting_id=meeting_id,
                                session_uid=f"sess-{meeting_id}")
    storage.blobs[f"{prefix}captured-signal.jsonl"] = b"x" * size
    storage.mtimes[f"{prefix}captured-signal.jsonl"] = mtime
    storage.blobs[f"{prefix}stt.jsonl"] = b"y" * size
    storage.mtimes[f"{prefix}stt.jsonl"] = mtime
    if promoted:
        storage.blobs[f"{prefix}{SIGNAL_PROMOTED_MARKER}"] = b""
        storage.mtimes[f"{prefix}{SIGNAL_PROMOTED_MARKER}"] = mtime
    return prefix


NOW = 1_000_000.0
OLD_ENOUGH = NOW - 10_000.0  # comfortably past min_age_s


async def test_janitor_is_a_no_op_under_budget():
    storage = InMemoryStorage()
    _seed_tape(storage, meeting_id=1, size=100, mtime=OLD_ENOUGH)
    out = await sweep_signal_tapes(storage, budget_bytes=10_000, now=NOW)
    assert out == {"tapes": 1, "bytes": 200, "evicted": 0, "evicted_bytes": 0,
                   "over_budget": False}
    assert storage.deleted == []


async def test_janitor_evicts_oldest_first_until_it_fits():
    storage = InMemoryStorage()
    p1 = _seed_tape(storage, meeting_id=1, size=100, mtime=OLD_ENOUGH - 300)   # oldest
    p2 = _seed_tape(storage, meeting_id=2, size=100, mtime=OLD_ENOUGH - 200)
    p3 = _seed_tape(storage, meeting_id=3, size=100, mtime=OLD_ENOUGH - 100)   # newest
    # 600 bytes total, budget 450 → one tape (200B) evicted takes us to 400, and it stops there
    # rather than draining to empty.
    out = await sweep_signal_tapes(storage, budget_bytes=450, now=NOW)
    assert out["evicted"] == 1 and out["bytes"] == 400 and out["over_budget"] is False
    assert not any(k.startswith(p1) for k in storage.blobs), "the oldest tape should be gone"
    assert any(k.startswith(p2) for k in storage.blobs)
    assert any(k.startswith(p3) for k in storage.blobs)


async def test_janitor_evicts_a_tape_whole():
    """Half a tape is not a smaller fixture, it is a broken one — frames with no STT round-trips
    beside them cannot bisect anything."""
    storage = InMemoryStorage()
    p1 = _seed_tape(storage, meeting_id=1, size=100, mtime=OLD_ENOUGH)
    _seed_tape(storage, meeting_id=2, size=100, mtime=OLD_ENOUGH + 10)
    await sweep_signal_tapes(storage, budget_bytes=250, now=NOW)
    assert [k for k in storage.blobs if k.startswith(p1)] == []
    assert sorted(storage.deleted) == [f"{p1}captured-signal.jsonl", f"{p1}stt.jsonl"]


async def test_janitor_spares_promoted_tapes_even_when_they_are_the_oldest():
    """Promotion is the curation decision. The budget must never be able to delete the regression
    library to satisfy an arithmetic target."""
    storage = InMemoryStorage()
    promoted = _seed_tape(storage, meeting_id=1, size=100, mtime=OLD_ENOUGH - 999, promoted=True)
    raw = _seed_tape(storage, meeting_id=2, size=100, mtime=OLD_ENOUGH)
    out = await sweep_signal_tapes(storage, budget_bytes=250, now=NOW)
    assert out["evicted"] == 1
    assert any(k.startswith(promoted) for k in storage.blobs), "promoted tape was evicted"
    assert not any(k.startswith(raw) for k in storage.blobs)


async def test_janitor_refuses_loudly_when_only_promoted_tapes_remain(capsys):
    """The state where the budget stops being self-enforcing. It must be a log line a human can act
    on, not silent unbounded growth — which is the exact failure this module exists to prevent."""
    storage = InMemoryStorage()
    _seed_tape(storage, meeting_id=1, size=500, mtime=OLD_ENOUGH, promoted=True)
    out = await sweep_signal_tapes(storage, budget_bytes=100, now=NOW)
    assert out["evicted"] == 0 and out["over_budget"] is True
    assert storage.deleted == []
    assert "signal_tape_budget_unreclaimable" in capsys.readouterr().out


async def test_janitor_never_touches_an_in_flight_tape():
    """A sweep firing while a bot is uploading must not half-delete the tape arriving right now."""
    storage = InMemoryStorage()
    fresh = _seed_tape(storage, meeting_id=1, size=500, mtime=NOW - 5)  # seconds old
    out = await sweep_signal_tapes(storage, budget_bytes=100, min_age_s=600, now=NOW)
    assert out["evicted"] == 0 and out["over_budget"] is True
    assert len([k for k in storage.blobs if k.startswith(fresh)]) == 2


async def test_janitor_only_ever_looks_at_the_signal_prefix():
    """The blast-radius assertion. A janitor that deletes is one bad prefix away from destroying
    customer recordings, so this pins that recordings are not merely spared but never listed."""
    storage = InMemoryStorage()
    storage.blobs["recordings/7/1/conn/audio/000000.wav"] = b"z" * 10_000
    storage.mtimes["recordings/7/1/conn/audio/000000.wav"] = OLD_ENOUGH - 99_999
    _seed_tape(storage, meeting_id=1, size=100, mtime=OLD_ENOUGH)
    out = await sweep_signal_tapes(storage, budget_bytes=1, now=NOW)
    assert out["bytes"] == 0, "recording bytes must not count toward the tape budget"
    assert storage.blobs["recordings/7/1/conn/audio/000000.wav"] == b"z" * 10_000
    assert all(k.startswith("signal/") for k in storage.deleted)


async def test_janitor_ignores_stray_objects_under_the_signal_prefix():
    """An unrecognized key shape is LEFT ALONE rather than swept up by a delete loop that assumed
    everything it listed was a tape part."""
    storage = InMemoryStorage()
    storage.blobs["signal/README"] = b"not a tape"
    storage.mtimes["signal/README"] = OLD_ENOUGH
    p1 = _seed_tape(storage, meeting_id=1, size=100, mtime=OLD_ENOUGH)
    await sweep_signal_tapes(storage, budget_bytes=1, now=NOW)
    assert storage.blobs["signal/README"] == b"not a tape"
    assert not any(k.startswith(p1) for k in storage.blobs)
