"""recordings — a read is a read: finalize writes once, and the raw route streams.

Two faults on the recording read path (brain card 98e0be7c):

  * Every ``GET .../media/{id}/raw`` called ``finalize_master`` and every ``finalize_master`` call
    restamped the media file (``finalized_at = now``) through ``mutate_recordings`` — a
    ``SELECT … FOR UPDATE`` plus a rewrite of the whole ``meeting.data`` row, once per HTTP Range
    request of a playing recording.
  * A 206 answer fetched its whole window into memory (``get_range`` → ``bytes``) and a 200 the
    whole object; the browser's first ``Range: bytes=0-`` is the whole file.

These drive the SHIPPED ``build_router`` / ``finalize_master`` over the in-memory fakes and the
``S3Storage`` adapter over a stub client, offline.
"""
from __future__ import annotations

import asyncio
import io
import struct
import tracemalloc

from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.recording_codec import build_recording_master
from meeting_api.recordings import build_router, finalize_master, upload_chunk
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage
from meeting_api.recordings.service import master_is_current

USER = 7
MEETING_ID = 1
SESSION_UID = "conn-reads"
HDRS = {"x-user-id": str(USER)}


def _wav(byte_val: int, n_data: int = 4096) -> bytes:
    data = bytes([byte_val % 256]) * n_data
    fmt = struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16)
    chunk = struct.pack("<4sI", b"data", len(data)) + data
    return struct.pack("<4sI4s", b"RIFF", 4 + len(fmt) + len(chunk), b"WAVE") + fmt + chunk


class CountingRepo(InMemoryRecordingRepo):
    """The in-memory repo, counting its DB-shaped calls: ``mutate_recordings`` is the ONLY write of
    ``meeting.data['recordings']`` (the SQL adapter's ``SELECT … FOR UPDATE`` + UPDATE + COMMIT);
    ``get_recordings`` is the SQL adapter's locking read."""

    def __init__(self):
        super().__init__()
        self.writes = 0
        self.locked_reads = 0

    async def mutate_recordings(self, meeting_id, mutator):
        self.writes += 1
        return await super().mutate_recordings(meeting_id, mutator)

    async def get_recordings(self, meeting_id):
        self.locked_reads += 1
        return await super().get_recordings(meeting_id)


async def _recorded(n_parts: int = 3):
    """A finished bot recording: ``n_parts`` data chunks, then the empty is_final signal chunk."""
    repo, storage = CountingRepo(), InMemoryStorage()
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid=SESSION_UID)
    parts = [_wav(k) for k in range(n_parts)]
    rid = None
    for seq, part in enumerate(parts):
        r = await upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                               data=part, media_type="audio", media_format="wav", chunk_seq=seq,
                               is_final=False)
        rid = r["recording_id"]
    await upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                       data=b"", media_type="audio", media_format="wav", chunk_seq=n_parts,
                       is_final=True)
    return repo, storage, rid, parts


def _client(repo, storage) -> TestClient:
    app = FastAPI()
    app.include_router(build_router(repo, storage))
    return TestClient(app)


async def _media(repo, rid) -> dict:
    rec = next(r for r in await repo.get_recordings(MEETING_ID) if r["id"] == rid)
    return next(m for m in rec["media_files"] if m["type"] == "audio")


# ── finalize at most once ──────────────────────────────────────────────────────────────────────


async def test_repeated_range_reads_write_the_row_once():
    """The fault itself: a player seeking through a finished recording. The first read assembles
    and stamps the master (ONE write); the next 40 Range reads and 5 /master reads write nothing,
    leave ``finalized_at`` as it was, and the raw reads take no locking read either. RED on base:
    every one of the 45 reads called ``mutate_recordings``."""
    repo, storage, rid, parts = await _recorded()
    client = _client(repo, storage)
    master = build_recording_master(parts, "wav")
    mf_id = (await _media(repo, rid))["id"]
    raw = f"/recordings/{rid}/media/{mf_id}/raw?type=audio"

    writes_before = repo.writes
    first = client.get(raw, headers={**HDRS, "Range": "bytes=0-"})
    assert first.status_code == 206 and first.content == master
    assert repo.writes == writes_before + 1, "the first read assembles and stamps, once"
    stamped = await _media(repo, rid)
    assert master_is_current(stamped)

    writes, locked = repo.writes, repo.locked_reads
    for i in range(40):
        start = (i * 997) % len(master)
        r = client.get(raw, headers={**HDRS, "Range": f"bytes={start}-{start + 1500}"})
        assert r.status_code == 206, r.text
        assert r.content == master[start:start + 1501]
    assert repo.writes == writes, "Range reads of a current master must not write meeting.data"
    assert repo.locked_reads == locked, "nor take finalize's locking read"
    for _ in range(5):
        assert client.get(f"/recordings/{rid}/master?type=audio", headers=HDRS).status_code == 200
    assert repo.writes == writes, "a /master read of a current master must not write either"
    assert (await _media(repo, rid))["finalized_at"] == stamped["finalized_at"]


async def test_finalize_master_writes_nothing_when_the_stamp_is_current():
    """At the service: the second finalize of an unchanged recording lists, finds nothing new, and
    returns the master key without ``mutate_recordings``. The bundle export and /master call it."""
    repo, storage, rid, _ = await _recorded()
    key = await finalize_master(repo, storage, meeting_id=MEETING_ID, recording_id=rid)
    writes = repo.writes
    for _ in range(3):
        assert await finalize_master(repo, storage, meeting_id=MEETING_ID, recording_id=rid) == key
    assert repo.writes == writes


async def test_a_chunk_after_the_read_reassembles_once_then_reads_stop_writing():
    """#768 still holds through the read fast path: a chunk folded after the master was assembled
    bumps ``chunk_count`` (and the fold drops ``assembled_chunk_count``), so the next raw read
    reassembles — one write — and serves every chunk; the reads after it write nothing."""
    repo, storage, rid, parts = await _recorded(n_parts=2)
    client = _client(repo, storage)
    mf_id = (await _media(repo, rid))["id"]
    raw = f"/recordings/{rid}/media/{mf_id}/raw?type=audio"
    assert client.get(raw, headers=HDRS).content == build_recording_master(parts, "wav")

    late = _wav(9)
    await upload_chunk(repo, storage, token_meeting_id=MEETING_ID, session_uid=SESSION_UID,
                       data=late, media_type="audio", media_format="wav", chunk_seq=7,
                       is_final=False)
    assert not master_is_current(await _media(repo, rid))
    writes = repo.writes
    served = client.get(raw, headers=HDRS).content
    assert served == build_recording_master(parts + [late], "wav"), "the late chunk is served"
    assert repo.writes == writes + 1
    for _ in range(10):
        assert client.get(raw, headers={**HDRS, "Range": "bytes=0-99"}).status_code == 206
    assert repo.writes == writes + 1


def test_master_is_current_needs_both_counts_equal_and_present():
    base = {"storage_path": "recordings/7/1/s/audio/master.wav", "format": "wav", "is_final": True,
            "finalized_by": "recording_finalizer.master", "chunk_count": 4,
            "assembled_chunk_count": 4}
    assert master_is_current(base)
    assert not master_is_current({**base, "assembled_chunk_count": 3})
    assert not master_is_current({k: v for k, v in base.items() if k != "assembled_chunk_count"})
    assert not master_is_current({k: v for k, v in base.items()
                                  if k not in ("assembled_chunk_count", "chunk_count")})
    assert not master_is_current({**base, "storage_path": "recordings/7/1/s/audio/000003.wav"})
    assert not master_is_current({**base, "is_final": False})
    webm = {**base, "format": "webm", "storage_path": "recordings/7/1/s/audio/master.webm"}
    assert not master_is_current(webm), "a WebM master must be the seekable remux first"
    assert master_is_current({**webm, "seekable_version": 1})


# ── the raw route streams ──────────────────────────────────────────────────────────────────────


class GeneratedStorage(InMemoryStorage):
    """One master far larger than any buffer we accept, never held anywhere: ``open_range`` makes
    each piece on demand, and ``get`` / ``get_range`` refuse — a read that materializes the object
    fails the test instead of passing slowly."""

    PIECE = 256 * 1024

    def __init__(self, key: str, total: int):
        super().__init__()
        self.key, self.total = key, total
        self.blobs[key] = b""  # exists / list see it; its bytes come from open_range

    async def size(self, key):
        assert key == self.key
        return self.total

    async def get(self, key):
        raise AssertionError(f"whole-object get({key!r}) on the read path")

    async def get_range(self, key, start, end):
        raise AssertionError(f"buffered get_range({key!r}, {start}, {end}) on the read path")

    async def open_range(self, key, start, end):
        assert key == self.key and 0 <= start <= end < self.total

        async def pieces():
            at = start
            while at <= end:
                n = min(self.PIECE, end - at + 1)
                yield bytes([at % 251]) * n
                at += n

        return pieces()


async def _asgi_get(app, path: str, query: str, headers: dict) -> dict:
    """One GET straight through the ASGI app, counting body bytes and dropping them as they arrive,
    so the client side holds nothing either (TestClient / httpx's ASGI transport buffer the body)."""
    out = {"status": None, "headers": {}, "bytes": 0, "messages": 0, "largest": 0}
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": query.encode(), "root_path": "", "client": ("test", 1),
        "server": ("test", 80),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # the client never leaves; cancelled when the answer is done

    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
            out["headers"] = {k.decode(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            out["bytes"] += len(body)
            out["messages"] += 1
            out["largest"] = max(out["largest"], len(body))

    await app(scope, receive, send)
    return out


def _current_media(key: str, total: int) -> dict:
    return {"id": 11, "type": "audio", "format": "wav", "is_final": True, "storage_path": key,
            "finalized_by": "recording_finalizer.master", "finalized_at": "2026-11-03T00:00:00Z",
            "chunk_count": 3, "assembled_chunk_count": 3, "file_size_bytes": total}


async def test_a_range_read_of_a_large_master_streams_in_bounded_memory():
    """256 MiB through the raw route, as the browser's first ``Range: bytes=0-`` and as a plain 200:
    every byte arrives, in many pieces none larger than one storage piece, and the process never
    holds more than a few MiB (tracemalloc peak). RED on base: the route called ``get_range`` /
    ``get`` and answered from one ``bytes`` of the whole window."""
    total = 256 * 1024 * 1024
    key = "recordings/7/100/conn-big/audio/master.wav"
    repo, storage = InMemoryRecordingRepo(), GeneratedStorage(key, total)
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid="conn-big")
    repo._meetings[MEETING_ID]["recordings"] = [{
        "id": 100, "session_uid": "conn-big", "source": "bot", "status": "completed",
        "playback_url": {"audio": "/recordings/100/master?type=audio", "video": None},
        "media_files": [_current_media(key, total)],
    }]
    app = FastAPI()
    app.include_router(build_router(repo, storage))
    path = "/recordings/100/media/11/raw"

    warm = await _asgi_get(app, path, "type=audio", {**HDRS, "Range": "bytes=0-1023"})
    assert warm["status"] == 206 and warm["bytes"] == 1024

    for rng, status, expect in (
        ("bytes=0-", 206, total),
        ("bytes=1000-", 206, total - 1000),
        (None, 200, total),
    ):
        headers = {**HDRS, **({"Range": rng} if rng else {})}
        tracemalloc.start()
        try:
            got = await _asgi_get(app, path, "type=audio", headers)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert got["status"] == status, got
        assert got["bytes"] == expect
        assert got["headers"]["content-length"] == str(expect)
        if rng:
            assert got["headers"]["content-range"] == f"bytes {total - expect}-{total - 1}/{total}"
        assert got["largest"] <= GeneratedStorage.PIECE
        assert got["messages"] >= expect // GeneratedStorage.PIECE
        assert peak < 8 * 1024 * 1024, f"peak {peak} bytes while streaming {expect}"


def test_a_range_answer_is_the_exact_window_across_pieces():
    """The in-memory fake streams in 64 KiB pieces; a window spanning several pieces, a suffix range
    and a full read come back byte-exact with the right headers."""
    key = "recordings/7/100/conn-x/audio/master.wav"
    blob = bytes(range(256)) * 1024  # 256 KiB, position-checkable
    repo, storage = InMemoryRecordingRepo(), InMemoryStorage()
    storage.blobs[key] = blob
    repo.seed(meeting_id=MEETING_ID, user_id=USER, session_uid="conn-x")
    repo._meetings[MEETING_ID]["recordings"] = [{
        "id": 100, "session_uid": "conn-x", "source": "bot", "status": "completed",
        "media_files": [_current_media(key, len(blob))],
    }]
    client = _client(repo, storage)
    url = "/recordings/100/media/11/raw?type=audio"
    r = client.get(url, headers={**HDRS, "Range": "bytes=70000-200000"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 70000-200000/{len(blob)}"
    assert r.headers["content-length"] == str(200000 - 70000 + 1)
    assert r.content == blob[70000:200001]
    r = client.get(url, headers={**HDRS, "Range": "bytes=-100"})
    assert r.status_code == 206 and r.content == blob[-100:]
    r = client.get(url, headers=HDRS)
    assert r.status_code == 200 and r.content == blob
    assert r.headers["accept-ranges"] == "bytes"


# ── the S3 adapter ─────────────────────────────────────────────────────────────────────────────


class _Body:
    def __init__(self, data: bytes):
        self._buf = io.BytesIO(data)
        self.reads: list[int] = []
        self.closed = False

    def read(self, amt=None):
        self.reads.append(amt)
        return self._buf.read() if amt is None else self._buf.read(amt)

    def close(self):
        self.closed = True


class _StubS3:
    def __init__(self, blob: bytes):
        self.blob = blob
        self.calls: list[dict] = []
        self.bodies: list[_Body] = []

    def get_object(self, **kw):
        self.calls.append(kw)
        start, end = (int(x) for x in kw["Range"].removeprefix("bytes=").split("-"))
        body = _Body(self.blob[start:end + 1])
        self.bodies.append(body)
        return {"Body": body}


def _s3(blob: bytes):
    from meeting_api.recordings.adapters import S3Storage

    stub = _StubS3(blob)

    class _Stub(S3Storage):
        def _c(self):
            return stub

    storage = _Stub(bucket="recordings")
    storage.STREAM_PIECE_BYTES = 1000
    return storage, stub


async def test_s3_open_range_sends_the_range_and_reads_in_pieces():
    blob = bytes(range(256)) * 40  # 10240 bytes
    storage, stub = _s3(blob)
    it = await storage.open_range("k", 100, 5099)
    assert stub.calls == [{"Bucket": "recordings", "Key": "k", "Range": "bytes=100-5099"}], \
        "the GET is made when the range is opened, with the window passed through"
    got = [p async for p in it]
    assert b"".join(got) == blob[100:5100]
    body = stub.bodies[0]
    assert all(n == 1000 for n in body.reads), "every read is bounded, never read() of all"
    assert max(len(p) for p in got) <= 1000
    assert body.closed


async def test_s3_open_range_closes_the_body_when_the_reader_stops_early():
    storage, stub = _s3(bytes(10000))
    it = await storage.open_range("k", 0, 9999)
    assert len(await it.__anext__()) == 1000
    await it.aclose()  # the client went away mid-answer
    assert stub.bodies[0].closed
    assert len(stub.bodies[0].reads) == 1
