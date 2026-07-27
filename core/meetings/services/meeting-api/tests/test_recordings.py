"""recordings — chunk upload + finalize → master in meeting.data JSONB (recording.v1).

Drives the SHIPPED ``upload_chunk`` / ``finalize_master`` / ``build_router`` over the in-memory
fakes, OFFLINE (no MinIO, no DB): chunks fold into the recording's JSONB payload, the master is
built by the golden-locked codec and the media-file stamped finalized, and the upload-token auth +
session-resolution seams behave.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api.bot_spawn import mint_meeting_token
from meeting_api.recording_codec import build_recording_master
from meeting_api.recordings import build_router, finalize_master, upload_chunk
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage
from meeting_api.recordings.jsonb import apply_chunk_to_recording, chunk_storage_key

SECRET = "test-admin-token"
USER = 7
MEETING_ID = 1
SESSION_UID = "conn-abc"

# A minimal valid wav file (44-byte RIFF header + 4 bytes of PCM) so the wav master codec runs.
def _wav(n_data: int = 4) -> bytes:
    import struct

    data = b"\x00" * n_data
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
    chunk = struct.pack("<4sI", b"data", len(data)) + data
    riff_len = 4 + len(fmt) + len(chunk)
    return struct.pack("<4sI4s", b"RIFF", riff_len, b"WAVE") + fmt + chunk


# A deterministic COUNTING-PATTERN wav part (#509): part k's PCM is the byte value k repeated
# n_data times, so the assembled master's PCM is arithmetic — any dropped / duplicated /
# overwritten / reordered part is a byte-count or pattern mismatch, every byte accounted for.
_PART_PCM_LEN = 8


def _counting_wav(byte_val: int, n_data: int = _PART_PCM_LEN) -> bytes:
    import struct

    data = bytes([byte_val % 256]) * n_data
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
    chunk = struct.pack("<4sI", b"data", len(data)) + data
    riff_len = 4 + len(fmt) + len(chunk)
    return struct.pack("<4sI4s", b"RIFF", riff_len, b"WAVE") + fmt + chunk


def _seeded():
    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    return repo, InMemoryStorage()


def _client_for(repo, storage):
    """A TestClient over the SAME repo+storage a test already uploaded chunks into (so the user read
    path GET /recordings -> /master -> /raw sees what upload_chunk wrote)."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(build_router(repo, storage, token_secret=SECRET))
    return TestClient(app)


# ── flow: upload folds chunks into JSONB; finalize builds the master ─────────────────────────────

async def test_upload_chunk_writes_recording_jsonb():
    repo, storage = _seeded()
    receipt = await upload_chunk(
        repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
        data=_wav(), media_type="audio", media_format="wav", chunk_seq=0, is_final=False,
    )
    assert receipt["status"] == "in_progress"
    recs = await repo.get_recordings(MEETING_ID)
    assert len(recs) == 1
    mf = recs[0]["media_files"][0]
    assert mf["type"] == "audio"
    assert mf["chunk_count"] == 1
    # The chunk landed in storage under the parent key scheme.
    assert mf["storage_path"] in storage.blobs


async def test_final_chunk_completes_recording():
    repo, storage = _seeded()
    await upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                       data=_wav(), media_format="wav", chunk_seq=0, is_final=False)
    receipt = await upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                                 data=_wav(), media_format="wav", chunk_seq=1, is_final=True)
    assert receipt["status"] == "completed"


async def test_finalize_master_builds_and_stamps():
    repo, storage = _seeded()
    rid = None
    for seq in range(3):
        receipt = await upload_chunk(
            repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
            data=_wav(), media_format="wav", chunk_seq=seq, is_final=False,
        )
        rid = receipt["recording_id"]
    master_key = await finalize_master(repo, storage, meeting_id=MEETING_ID, recording_id=rid)
    assert master_key.endswith("/audio/master.wav")
    assert master_key in storage.blobs  # the codec-built master was uploaded
    recs = await repo.get_recordings(MEETING_ID)
    mf = recs[0]["media_files"][0]
    assert mf["is_final"] is True
    assert mf["finalized_by"] == "recording_finalizer.master"
    assert mf["storage_path"] == master_key


async def test_upload_before_session_is_pending():
    repo, storage = _seeded()
    receipt = await upload_chunk(
        repo, storage, token_meeting_id=MEETING_ID, session_uid="unknown-session",
        data=_wav(), media_format="wav", chunk_seq=0, is_final=False,
    )
    assert receipt == {"status": "pending"}


# ── route: the upload endpoint authenticates the MeetingToken ────────────────────────────────────

def _client():
    from fastapi import FastAPI

    repo, storage = _seeded()
    app = FastAPI()
    app.include_router(build_router(repo, storage, token_secret=SECRET))
    return TestClient(app)


def test_upload_route_requires_token():
    client = _client()
    r = client.post(
        "/internal/recordings/upload",
        data={"session_uid": SESSION_UID, "media_format": "wav", "chunk_seq": 0, "is_final": "true"},
        files={"file": ("c.wav", _wav(), "audio/wav")},
    )
    assert r.status_code == 401  # missing Authorization


def test_upload_route_accepts_valid_token():
    client = _client()
    token = mint_meeting_token(MEETING_ID, USER, "google_meet", "abc", secret=SECRET)
    r = client.post(
        "/internal/recordings/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={"session_uid": SESSION_UID, "media_format": "wav", "chunk_seq": 0, "is_final": "true"},
        files={"file": ("c.wav", _wav(), "audio/wav")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"


# ── G4: object-storage I/O must not block the event loop ─────────────────────────────────────────


class _BlockingS3Client:
    """A stub boto3 client whose put_object BLOCKS (sync) — stands in for a slow S3 round-trip."""

    def __init__(self, block_s: float):
        self._block_s = block_s
        self.calls = 0

    def put_object(self, **kw):
        import time

        time.sleep(self._block_s)  # a real, blocking, synchronous call (what boto3 does)
        self.calls += 1
        return {}


async def test_s3_storage_does_not_block_the_event_loop():
    """G4: a blocking boto3 call must run OFF the loop (asyncio.to_thread), so the control plane keeps
    serving lifecycle/webhook/ws traffic during a slow/large S3 op. We run a ~0.3s blocking upload
    concurrently with a 5ms heartbeat — a non-blocking loop ticks many times; a blocked loop ~never."""
    import asyncio

    from meeting_api.recordings.adapters import S3Storage

    class _StubS3(S3Storage):
        def __init__(self, client):
            super().__init__(bucket="b")
            self._stub = client

        def _c(self):
            return self._stub

        # NB: _run is INHERITED (asyncio.to_thread) — that's exactly what's under test.

    storage = _StubS3(_BlockingS3Client(block_s=0.3))
    ticks = {"n": 0}
    stop = {"v": False}

    async def heartbeat():
        while not stop["v"]:
            ticks["n"] += 1
            await asyncio.sleep(0.005)

    hb = asyncio.create_task(heartbeat())
    try:
        await storage.upload("k", b"x" * 1024, content_type="audio/wav")
    finally:
        stop["v"] = True
        await hb

    assert storage._stub.calls == 1
    assert ticks["n"] >= 20, (
        f"event loop appears BLOCKED during the S3 upload (only {ticks['n']} heartbeats in ~0.3s) — "
        "the boto3 call is not being offloaded to a thread"
    )


# ── G3: concurrent chunk folds must not lose updates (atomic read→modify→write) ──────────────────


class _YieldingStorage(InMemoryStorage):
    """An InMemoryStorage whose upload YIELDS the event loop, so two concurrent uploads genuinely
    interleave (forcing the read→modify→write race the atomic mutate must serialize)."""

    async def upload(self, key, data, *, content_type, tags=None):
        import asyncio

        await asyncio.sleep(0)
        await super().upload(key, data, content_type=content_type, tags=tags)


async def test_concurrent_chunk_uploads_do_not_lose_updates():
    """G3: two chunk uploads racing on the SAME recording must BOTH be folded. The old
    get_recordings → apply → put_recordings ran in SEPARATE transactions, so the second put clobbered
    the first (lost update → chunk_count stuck at 2). The atomic mutate_recordings re-reads the LIVE
    list under one lock and folds cumulatively → chunk_count 3."""
    import asyncio

    repo = InMemoryRecordingRepo()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    storage = _YieldingStorage()

    # chunk 0 (sequential) establishes the recording.
    await upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                       data=_wav(), media_format="wav", chunk_seq=0, is_final=False)
    # chunks 1 + 2 race.
    await asyncio.gather(
        upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                     data=_wav(), media_format="wav", chunk_seq=1, is_final=False),
        upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                     data=_wav(), media_format="wav", chunk_seq=2, is_final=False),
    )

    recs = await repo.get_recordings(MEETING_ID)
    bot_recs = [r for r in recs if r.get("source") == "bot"]
    assert len(bot_recs) == 1, f"exactly one recording for the session, got {len(bot_recs)}"
    mf = next(m for m in bot_recs[0]["media_files"] if m["type"] == "audio")
    assert mf["chunk_count"] == 3, f"all 3 chunks must be folded (no lost update), got {mf['chunk_count']}"


# ── #509 C2: retrieval serves the assembled master, never the empty final-signal chunk ────────────
# V1/#491 — a confirmed multi-chunk upload downloads BYTE-COMPLETE via /master and /raw.
# V2/#412 — every uploaded part is kept; a crashed (no-final) recording still retrieves its parts.

_HDRS = {"x-user-id": str(USER)}


def _first_audio(rec: dict) -> dict:
    return next(m for m in rec["media_files"] if m["type"] == "audio")


async def test_multichunk_plus_empty_final_raw_serves_master_not_signal_chunk():
    """A2 (V1/#491): N counting-pattern data chunks + an empty is_final signal, all folded, then
    GET .../media/{id}/raw serves the ASSEMBLED master BYTE-COMPLETE — never the zero-byte signal
    chunk. RED at base: /raw trusted the media-file is_final flag and served storage_path (the
    0-byte final chunk) directly."""
    repo, storage = _seeded()
    n = 5
    parts = [_counting_wav(k) for k in range(n)]
    for k, part in enumerate(parts):
        await upload_chunk(
            repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
            data=part, media_type="audio", media_format="wav", chunk_seq=k, is_final=False,
        )
    # The empty is_final "signal" chunk — a zero-byte COMPLETED marker, NOT playable bytes.
    receipt = await upload_chunk(
        repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
        data=b"", media_type="audio", media_format="wav", chunk_seq=n, is_final=True,
    )
    assert receipt["status"] == "completed"

    client = _client_for(repo, storage)
    listed = client.get("/recordings", headers=_HDRS)
    assert listed.status_code == 200, listed.text
    recs = listed.json()["recordings"]
    assert len(recs) == 1
    rec = recs[0]
    rid, mf = rec["id"], _first_audio(rec)
    # A3 defence-in-depth: the LISTED pointer must never be the zero-byte final-signal chunk.
    assert not mf["storage_path"].endswith(f"/audio/{n:06d}.wav"), mf["storage_path"]

    # Hit /raw DIRECTLY (no prior /master) — finalize-on-read must assemble + serve the master.
    raw = client.get(f"/recordings/{rid}/media/{mf['id']}/raw?type=audio", headers=_HDRS)
    assert raw.status_code == 200, raw.text
    oracle = build_recording_master(parts, "wav")
    assert raw.content == oracle, "raw must byte-equal the codec master oracle"
    # Independent arithmetic oracle: the PCM payload is exactly each part's counting bytes, in order.
    assert raw.content[44:] == b"".join(bytes([k]) * _PART_PCM_LEN for k in range(n))
    assert len(raw.content) > 44, "must not be the zero-byte signal chunk"


async def test_multichunk_full_read_path_master_then_raw_byte_complete():
    """A2 (V1/#491) full player path: GET /recordings -> /recordings/{id} -> /master -> /raw, each
    step succeeds and the bytes are the complete assembled master."""
    repo, storage = _seeded()
    n = 4
    parts = [_counting_wav(k) for k in range(n)]
    for k, part in enumerate(parts):
        await upload_chunk(
            repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
            data=part, media_type="audio", media_format="wav", chunk_seq=k, is_final=False,
        )
    await upload_chunk(
        repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
        data=b"", media_type="audio", media_format="wav", chunk_seq=n, is_final=True,
    )
    client = _client_for(repo, storage)
    rec = client.get("/recordings", headers=_HDRS).json()["recordings"][0]
    rid = rec["id"]
    detail = client.get(f"/recordings/{rid}", headers=_HDRS)
    assert detail.status_code == 200, detail.text

    master = client.get(f"/recordings/{rid}/master?type=audio", headers=_HDRS)
    assert master.status_code == 200, master.text
    body = master.json()
    assert body["storage_path"].endswith("/audio/master.wav"), body["storage_path"]
    assert body["raw_url"], body

    raw = client.get(body["raw_url"], headers=_HDRS)
    assert raw.status_code == 200, raw.text
    assert raw.content == build_recording_master(parts, "wav")


async def test_crash_no_final_master_serves_uploaded_parts():
    """A1 (V2/#412) offline: a bot killed after part 3 (NO is_final) leaves parts 0-2 durable; the
    recording stays in_progress but /raw finalizes-on-read to EXACTLY those 3 parts concatenated —
    nothing lost, no all-or-nothing. Download must NOT require status==completed."""
    repo, storage = _seeded()
    parts = [_counting_wav(k) for k in range(3)]
    rid = None
    for k, part in enumerate(parts):
        r = await upload_chunk(
            repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
            data=part, media_type="audio", media_format="wav", chunk_seq=k, is_final=False,
        )
        rid = r["recording_id"]
    # No final chunk (SIGKILL) — status stays IN_PROGRESS.
    recs = await repo.get_recordings(MEETING_ID)
    rec = next(r for r in recs if r["id"] == rid)
    assert rec["status"] == "in_progress"

    client = _client_for(repo, storage)
    listed = client.get("/recordings", headers=_HDRS).json()["recordings"][0]
    mf = _first_audio(listed)
    raw = client.get(f"/recordings/{rid}/media/{mf['id']}/raw?type=audio", headers=_HDRS)
    assert raw.status_code == 200, raw.text
    assert raw.content == build_recording_master(parts, "wav")
    assert raw.content[44:] == b"".join(bytes([k]) * _PART_PCM_LEN for k in range(3))


def test_empty_final_fold_never_points_storage_at_signal_chunk():
    """A3 (unit): folding an empty is_final chunk keeps storage_path on the prior DATA chunk, never
    the zero-byte signal object, and still flips the recording to completed. Reverting the jsonb hunk
    makes storage_path the empty chunk key -> red."""
    data_key = chunk_storage_key(
        user_id=USER, recording_id=123, session_uid=SESSION_UID,
        media_type="audio", media_format="wav", chunk_seq=0,
    )
    rec, _ = apply_chunk_to_recording(
        None, recording_id=123, meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID,
        media_type="audio", media_format="wav", storage_path=data_key, file_size=100,
        chunk_seq=0, is_final=False, duration_seconds=None, sample_rate=None,
    )
    signal_key = chunk_storage_key(
        user_id=USER, recording_id=123, session_uid=SESSION_UID,
        media_type="audio", media_format="wav", chunk_seq=1,
    )
    rec2, transitioned = apply_chunk_to_recording(
        rec, recording_id=123, meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID,
        media_type="audio", media_format="wav", storage_path=signal_key, file_size=0,
        chunk_seq=1, is_final=True, duration_seconds=None, sample_rate=None,
    )
    mf = next(m for m in rec2["media_files"] if m["type"] == "audio")
    assert mf["storage_path"] == data_key, "kept the data chunk, not the zero-byte signal object"
    assert mf["storage_path"] != signal_key
    assert rec2["status"] == "completed", "empty final still completes the recording"
    assert transitioned is True


async def test_single_final_chunk_downloads_byte_complete():
    """A4 (no-regression): today's single-master-equivalent writer (ONE is_final chunk carrying
    data) still lists, masters, and /raw-downloads byte-complete."""
    repo, storage = _seeded()
    part = _counting_wav(9, n_data=16)
    r = await upload_chunk(
        repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
        data=part, media_type="audio", media_format="wav", chunk_seq=0, is_final=True,
    )
    assert r["status"] == "completed"
    rid = r["recording_id"]

    client = _client_for(repo, storage)
    rec = client.get("/recordings", headers=_HDRS).json()["recordings"][0]
    mf = _first_audio(rec)
    raw = client.get(f"/recordings/{rid}/media/{mf['id']}/raw?type=audio", headers=_HDRS)
    assert raw.status_code == 200, raw.text
    assert raw.content == build_recording_master([part], "wav")
    assert raw.content[44:] == bytes([9]) * 16


# ── #768: a mid-recording read must NOT freeze the master (finalize is re-assemblable) ────────────


async def test_finalize_after_midread_reassembles_all_chunks():
    """#768 (the exact prod scenario): a GET /master while the meeting is STILL recording must not
    permanently freeze the master. Two chunks land; a mid-recording finalize assembles a 2-chunk
    partial master; three more chunks arrive and the recording completes; the next finalize must
    REBUILD the master to contain ALL five chunks. RED on base: finalize short-circuits on
    ``storage.exists(master_key)`` and never rebuilds → the served master stays the 2-chunk partial
    (in prod: a 4h meeting frozen at 49s, 6.6% of the audio)."""
    repo, storage = _seeded()
    early = [_counting_wav(k) for k in range(2)]
    rid = None
    for k, part in enumerate(early):
        r = await upload_chunk(
            repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
            data=part, media_type="audio", media_format="wav", chunk_seq=k, is_final=False,
        )
        rid = r["recording_id"]
    # Mid-recording read: assemble a PARTIAL master (the prod "check on the recording" gesture).
    mid_key = await finalize_master(repo, storage, meeting_id=MEETING_ID, recording_id=rid)
    assert storage.blobs[mid_key] == build_recording_master(early, "wav"), "partial master = 2 chunks"

    # More chunks arrive AFTER the read, then the meeting ends (empty is_final signal).
    late = [_counting_wav(k) for k in range(2, 5)]
    for k, part in enumerate(late, start=2):
        await upload_chunk(
            repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
            data=part, media_type="audio", media_format="wav", chunk_seq=k, is_final=False,
        )
    await upload_chunk(
        repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
        data=b"", media_type="audio", media_format="wav", chunk_seq=5, is_final=True,
    )

    # The finalize on completion must REASSEMBLE all five chunks — not serve the frozen partial.
    final_key = await finalize_master(repo, storage, meeting_id=MEETING_ID, recording_id=rid)
    all_parts = early + late
    assert storage.blobs[final_key] == build_recording_master(all_parts, "wav"), (
        "the completed master must contain ALL chunks, not the mid-read partial"
    )
    # Independent arithmetic oracle: the PCM is each part's counting bytes, in order, none dropped.
    assert storage.blobs[final_key][44:] == b"".join(bytes([k]) * _PART_PCM_LEN for k in range(5))


async def test_master_route_reflects_late_chunks_after_midread():
    """#768 at the route altitude: GET /master mid-recording, then more chunks + completion, then
    GET .../raw serves the byte-complete master. RED on base: the first /master freezes it."""
    repo, storage = _seeded()
    early = [_counting_wav(k) for k in range(2)]
    rid = None
    for k, part in enumerate(early):
        r = await upload_chunk(
            repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
            data=part, media_type="audio", media_format="wav", chunk_seq=k, is_final=False,
        )
        rid = r["recording_id"]
    client = _client_for(repo, storage)
    # Mid-recording read via the route (this is what froze prod).
    assert client.get(f"/recordings/{rid}/master?type=audio", headers=_HDRS).status_code == 200
    late = [_counting_wav(k) for k in range(2, 5)]
    for k, part in enumerate(late, start=2):
        await upload_chunk(
            repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
            data=part, media_type="audio", media_format="wav", chunk_seq=k, is_final=False,
        )
    await upload_chunk(
        repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
        data=b"", media_type="audio", media_format="wav", chunk_seq=5, is_final=True,
    )
    rec = client.get("/recordings", headers=_HDRS).json()["recordings"][0]
    mf = _first_audio(rec)
    raw = client.get(f"/recordings/{rid}/media/{mf['id']}/raw?type=audio", headers=_HDRS)
    assert raw.status_code == 200, raw.text
    assert raw.content == build_recording_master(early + late, "wav")


# ── #769: chunk listing must paginate past the S3 1000-key cap ────────────────────────────────────


class _PagedS3Client:
    """A stub boto3 S3 client whose ``list_objects_v2`` paginates at ``PAGE`` keys — mirroring the
    real S3/S3-compatible 1000-key response cap — signalling more via ``IsTruncated`` +
    ``NextContinuationToken`` (an opaque offset here)."""

    PAGE = 1000

    def __init__(self, keys):
        self._keys = sorted(keys)

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None, **kw):
        matched = [k for k in self._keys if k.startswith(Prefix)]
        start = int(ContinuationToken) if ContinuationToken else 0
        page = matched[start : start + self.PAGE]
        resp = {"Contents": [{"Key": k} for k in page]}
        nxt = start + self.PAGE
        if nxt < len(matched):
            resp["IsTruncated"] = True
            resp["NextContinuationToken"] = str(nxt)
        else:
            resp["IsTruncated"] = False
        return resp


async def test_s3_storage_list_paginates_past_1000_keys():
    """#769: a single ``list_objects_v2`` caps at 1000 keys and signals more via IsTruncated /
    NextContinuationToken. ``S3Storage.list`` must loop to exhaustion. RED on base: the single
    unpaginated call returns only the first 1000 of 1500 keys, silently dropping 500 chunks."""
    from meeting_api.recordings.adapters import S3Storage

    prefix = "recordings/7/42/sess/audio/"
    keys = [f"{prefix}{i:06d}.wav" for i in range(1500)]

    class _Stub(S3Storage):
        def __init__(self, client):
            super().__init__(bucket="b")
            self._stub = client

        def _c(self):
            return self._stub

    storage = _Stub(_PagedS3Client(keys))
    listed = await storage.list(prefix)
    assert len(listed) == 1500, f"expected all 1500 keys across pages, got {len(listed)}"
    assert listed == sorted(keys)


# ── retention: objects are TAGGED by media type ────────────────────────────────────────────────
# There is no deletion service here on purpose — an operator expresses retention as a bucket
# lifecycle rule ("expire media=video after 90 days"). That rule can only select on a tag: the
# media type sits deep inside the key (recordings/{user}/{rec}/{session}/{media_type}/...) and
# lifecycle prefix filters are literal prefixes, so they cannot match a middle path segment.

@pytest.mark.asyncio
async def test_chunks_and_masters_are_tagged_by_media_type():
    repo, storage = InMemoryRecordingRepo(), InMemoryStorage()
    for media_type, fmt, blob in (("audio", "wav", _wav()), ("video", "webm", b"\x1a\x45\xdf\xa3video")):
        repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=f"{SESSION_UID}-{media_type}")
        await upload_chunk(
            repo, storage, token_meeting_id=MEETING_ID, session_uid=f"{SESSION_UID}-{media_type}",
            data=blob, media_type=media_type, media_format=fmt, chunk_seq=0, is_final=True,
        )
    tagged = {k: v.get("media") for k, v in storage.tags.items()}
    assert tagged, "nothing was uploaded"
    # Every object carries a media tag, and it matches the media type in its own key.
    for key, media in tagged.items():
        assert media in ("audio", "video"), (key, media)
        assert f"/{media}/" in key, f"tag {media!r} disagrees with key {key}"
    assert set(tagged.values()) == {"audio", "video"}


@pytest.mark.asyncio
async def test_nothing_expires_by_default():
    """Negative control: tagging is the MECHANISM, not a policy. We attach no expiry, no TTL and no
    lifecycle rule — an operator opts in deliberately, and until then recordings are kept."""
    repo, storage = InMemoryRecordingRepo(), InMemoryStorage()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    await upload_chunk(
        repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID, data=_wav(),
        media_type="audio", media_format="wav", chunk_seq=0, is_final=True,
    )
    for tags in storage.tags.values():
        assert set(tags) == {"media"}, f"unexpected retention metadata attached: {tags}"


# ── chunk pruning: the master supersedes the parts it was built from ───────────────────────────
# Chunks exist so a bot killed mid-meeting still leaves every finished part durable (#412). Once
# the recording is COMPLETE and a verified master holds the same bytes, keeping both doubles the
# stored size of every recording anyone ever plays. These pin that the delete is safe: it happens
# only after the bot's final chunk, never while a recording is still growing.

async def _record(repo, storage, *, parts: int, final: bool, session=SESSION_UID):
    """Upload `parts` chunks; the last carries is_final=`final`. Returns the recording id."""
    rid = None
    for seq in range(parts):
        receipt = await upload_chunk(
            repo, storage, token_meeting_id=MEETING_ID, session_uid=session, data=_wav(),
            media_format="wav", chunk_seq=seq, is_final=(final and seq == parts - 1),
        )
        rid = receipt["recording_id"]
    return rid


def _chunk_keys(storage):
    return [k for k in storage.blobs if not k.rsplit("/", 1)[-1].startswith("master.")]


@pytest.mark.asyncio
async def test_completed_recording_prunes_its_chunks_once_the_master_holds_them():
    repo, storage = _seeded()
    rid = await _record(repo, storage, parts=3, final=True)
    expected = build_recording_master([storage.blobs[k] for k in sorted(_chunk_keys(storage))], "wav")

    master_key = await finalize_master(repo, storage, meeting_id=MEETING_ID, recording_id=rid)

    assert _chunk_keys(storage) == [], "the superseded chunks should be gone"
    assert storage.blobs[master_key] == expected, "the master must still hold every byte"
    # one object, not N+1 — the whole point
    assert list(storage.blobs) == [master_key]

    mf = (await repo.get_recordings(MEETING_ID))[0]["media_files"][0]
    assert mf["chunks_pruned_count"] == 3 and mf["chunks_pruned_at"]


@pytest.mark.asyncio
async def test_a_recording_still_in_progress_is_NEVER_pruned():
    """THE safety case. A master read mid-meeting assembles a PARTIAL master and sets the media
    file's own is_final — pruning on that signal would truncate a live recording to whatever had
    arrived, permanently. Only the recording's `completed` status (the bot's final chunk) may
    authorise the delete."""
    repo, storage = _seeded()
    rid = await _record(repo, storage, parts=2, final=False)   # bot still recording

    await finalize_master(repo, storage, meeting_id=MEETING_ID, recording_id=rid)
    assert len(_chunk_keys(storage)) == 2, "chunks of a live recording must survive a master read"

    # the rest of the meeting arrives and must still assemble into a COMPLETE master
    for seq in (2, 3):
        await upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                           data=_wav(), media_format="wav", chunk_seq=seq, is_final=(seq == 3))
    expected = build_recording_master([storage.blobs[k] for k in sorted(_chunk_keys(storage))], "wav")
    master_key = await finalize_master(repo, storage, meeting_id=MEETING_ID, recording_id=rid)
    assert storage.blobs[master_key] == expected, "all four parts must be in the final master"
    assert _chunk_keys(storage) == [], "now complete → now prunable"


@pytest.mark.asyncio
async def test_rewatching_after_a_prune_serves_the_same_master():
    """A second read finds zero chunks. It must serve the existing master as-is, never rebuild
    from nothing and never report a chunk-count mismatch."""
    repo, storage = _seeded()
    rid = await _record(repo, storage, parts=3, final=True)
    first = await finalize_master(repo, storage, meeting_id=MEETING_ID, recording_id=rid)
    bytes_after_first = storage.blobs[first]

    second = await finalize_master(repo, storage, meeting_id=MEETING_ID, recording_id=rid)
    assert second == first
    assert storage.blobs[second] == bytes_after_first, "a rewatch must not alter the master"
    assert list(storage.blobs) == [first]


@pytest.mark.asyncio
async def test_prune_can_be_switched_off(monkeypatch):
    """The delete is irreversible, so an operator can refuse it and keep both copies."""
    monkeypatch.setenv("RECORDING_PRUNE_CHUNKS", "false")
    repo, storage = _seeded()
    rid = await _record(repo, storage, parts=3, final=True)
    await finalize_master(repo, storage, meeting_id=MEETING_ID, recording_id=rid)
    assert len(_chunk_keys(storage)) == 3, "opted out → chunks kept"
