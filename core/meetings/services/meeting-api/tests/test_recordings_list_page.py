"""``GET /recordings`` is a PAGE, newest first, in a list shape — fr_db203061a7a1d953.

An agent called the MCP's ``list_recordings(limit=3)`` on the production deployment and received
all 201 of the account's recordings: 1.6 MB, oldest first, each row carrying the per-chunk upload
bookkeeping of every media file it was assembled from. Three faults, and the first is the one worth
naming: ``limit`` and ``offset`` were forwarded correctly by the MCP tool AND by the gateway, and
then dropped in silence by this route, because FastAPI ignores a query parameter the handler does
not declare. **An argument that is accepted and ignored is worse than one that is refused** — the
caller cannot tell that the answer is not the one they asked for.

The other two are what makes the answer expensive when it is right: no ordering at all (there is no
recordings table — they live in ``meeting.data`` JSONB, and ``service.py`` appends the newest to the
TAIL, so the natural order is oldest-first), and no list projection (``storage_path``,
``chunk_seq``, ``chunk_count``, ``first_chunk_at``, … per media file — how the bytes were assembled,
which nothing in a list renders).

The full detail is still on ``GET /recordings/{id}``, which is where a caller goes when they want
one recording. That split is the point: this file asserts both halves.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_api.recordings import build_router
from meeting_api.recordings.fakes import InMemoryRecordingRepo, InMemoryStorage
from meeting_api.recordings.router import LIST_MEDIA_FILE_KEYS, LIST_RECORDING_KEYS

USER, OTHER = 7, 8
HDRS = {"x-user-id": str(USER)}

#: Keys the LIST must NOT carry on a media file: per-chunk assembly bookkeeping and storage
#: pointers. Named explicitly rather than derived, so widening the list shape is a deliberate edit
#: to this line rather than a silent consequence of a change somewhere else.
BOOKKEEPING_KEYS = (
    "storage_path", "storage_backend", "chunk_seq", "chunk_count", "last_chunk_size_bytes",
    "first_chunk_at", "is_final", "finalized_at", "finalized_by", "metadata",
)


def _rec(n: int, *, meeting_id: int) -> dict:
    """A recording row the shape ``recordings/jsonb.py`` writes — content next to bookkeeping."""
    return {
        "id": 1000 + n,
        "meeting_id": meeting_id,
        "user_id": USER,
        "session_uid": f"sess-{n}",
        "source": "bot",
        "status": "completed",
        # Zero-padded so lexicographic order IS chronological, as `_now_iso()` guarantees in prod.
        "created_at": f"2026-09-{n + 1:02d}T10:00:00Z",
        "completed_at": f"2026-09-{n + 1:02d}T11:00:00Z",
        "playback_url": {"audio": f"/recordings/{1000 + n}/master?type=audio", "video": None},
        "media_files": [{
            "id": 9000 + n, "type": "audio", "format": "wav",
            "duration_seconds": 60.0 + n, "file_size_bytes": 1024,
            "storage_path": f"recordings/{meeting_id}/audio/000000.wav",
            "storage_backend": "minio", "chunk_seq": 11, "chunk_count": 12,
            "last_chunk_size_bytes": 88, "first_chunk_at": "2026-09-01T10:00:00Z",
            "metadata": {"sample_rate": 16000}, "created_at": "2026-09-01T10:00:00Z",
            "is_final": True, "finalized_at": None, "finalized_by": None,
        }],
    }


def _client(count: int = 5, *, meetings: int = 1, user_id: int = USER):
    """``count`` recordings spread over ``meetings`` meetings, planted OLDEST FIRST — the order the
    JSONB actually holds, so a test that sees newest-first is seeing the sort and not the fixture."""
    repo = InMemoryRecordingRepo()
    for n in range(count):
        mid = 100 + (n % meetings)
        repo.seed(meeting_id=mid, user_id=user_id, session_uid=f"sess-{n}")
        repo._meetings[mid]["recordings"].append(_rec(n, meeting_id=mid))
    app = FastAPI()
    app.include_router(build_router(repo, InMemoryStorage(), token_secret="test-secret"))
    return repo, TestClient(app)


# ── the limit ────────────────────────────────────────────────────────────────────────────────────

def test_limit_is_honoured():
    """THE friction, in one line: ask for three, get three."""
    _repo, client = _client(count=10)
    body = client.get("/recordings?limit=3", headers=HDRS).json()
    assert len(body["recordings"]) == 3
    assert (body["limit"], body["offset"], body["total"]) == (3, 0, 10)
    assert body["has_more"] is True


def test_offset_pages_without_overlap_or_gap():
    _repo, client = _client(count=10)
    first = client.get("/recordings?limit=4&offset=0", headers=HDRS).json()["recordings"]
    second = client.get("/recordings?limit=4&offset=4", headers=HDRS).json()["recordings"]
    last = client.get("/recordings?limit=4&offset=8", headers=HDRS).json()
    ids = [r["id"] for r in first + second + last["recordings"]]
    assert len(ids) == 10 and len(set(ids)) == 10, "a page boundary duplicated or dropped a row"
    assert last["has_more"] is False, "the final page must say it is the final page"


def test_a_default_page_size_applies_when_the_caller_asks_for_nothing():
    """The caller who did not know to ask is the one the 1.6 MB reply landed on."""
    _repo, client = _client(count=120)
    body = client.get("/recordings", headers=HDRS).json()
    assert len(body["recordings"]) == 50 and body["total"] == 120 and body["has_more"] is True


def test_an_out_of_range_limit_is_refused_not_silently_clamped():
    """Refusing is the whole lesson of this friction: a page argument that is accepted and then
    ignored leaves the caller unable to tell the answer is not the one they asked for."""
    _repo, client = _client(count=3)
    assert client.get("/recordings?limit=5000", headers=HDRS).status_code == 422
    assert client.get("/recordings?limit=0", headers=HDRS).status_code == 422
    assert client.get("/recordings?offset=-1", headers=HDRS).status_code == 422


# ── the order ────────────────────────────────────────────────────────────────────────────────────

def test_newest_first():
    """Planted oldest-first (as the JSONB holds them), served newest-first."""
    _repo, client = _client(count=5)
    rows = client.get("/recordings", headers=HDRS).json()["recordings"]
    assert [r["created_at"] for r in rows] == sorted(
        (r["created_at"] for r in rows), reverse=True,
    ), "the list must lead with the most recent recording"
    assert rows[0]["id"] == 1004 and rows[-1]["id"] == 1000


def test_the_first_page_is_the_newest_page():
    """The two faults compound: without the sort, `limit=3` would return the three OLDEST — a
    correct page of the wrong end, which is the failure a caller is least likely to notice."""
    _repo, client = _client(count=10)
    rows = client.get("/recordings?limit=3", headers=HDRS).json()["recordings"]
    assert [r["id"] for r in rows] == [1009, 1008, 1007]


# ── the list shape ───────────────────────────────────────────────────────────────────────────────

def test_the_list_row_drops_per_chunk_bookkeeping():
    _repo, client = _client(count=1)
    media = client.get("/recordings", headers=HDRS).json()["recordings"][0]["media_files"][0]
    leaked = [k for k in BOOKKEEPING_KEYS if k in media]
    assert not leaked, f"the list row still carries upload bookkeeping: {leaked}"
    assert set(media) <= set(LIST_MEDIA_FILE_KEYS)


def test_the_list_row_keeps_what_a_list_renders():
    _repo, client = _client(count=1)
    row = client.get("/recordings", headers=HDRS).json()["recordings"][0]
    for key in ("id", "meeting_id", "status", "created_at", "completed_at", "playback_url"):
        assert key in row, f"the list row lost {key}"
    assert row["duration_seconds"] == 60.0, "a list wants to know how long the recording is"
    media = row["media_files"][0]
    assert media["id"] == 9000 and media["type"] == "audio", (
        "the media file's id and type must survive — they are how a caller reaches /media/{id}/raw"
    )
    assert set(row) <= set(LIST_RECORDING_KEYS) | {"media_files", "duration_seconds"}


def test_the_detail_route_still_carries_everything():
    """The projection is a LIST shape, not a deletion — one recording still answers in full."""
    _repo, client = _client(count=1)
    detail = client.get("/recordings/1000", headers=HDRS).json()
    media = detail["media_files"][0]
    for key in BOOKKEEPING_KEYS:
        assert key in media, f"the detail route lost {key} — it is the full-fidelity read"
    assert detail["session_uid"] == "sess-0"


def test_the_page_is_far_smaller_than_the_full_read():
    """The number that made this a blocker rather than an annoyance."""
    _repo, client = _client(count=200)
    assert len(client.get("/recordings?limit=3", headers=HDRS).content) < 2_000


# ── the meeting filter, forwarded all along and dropped here too ─────────────────────────────────

def test_the_meeting_filter_is_honoured():
    """The MCP tool has been sending `meeting_db_id` as `meeting_id` since it was written; this
    route declared no such parameter, so it was discarded with the paging arguments."""
    _repo, client = _client(count=6, meetings=3)
    body = client.get("/recordings?meeting_id=100", headers=HDRS).json()
    assert body["recordings"], "the filter must not empty the list"
    assert {r["meeting_id"] for r in body["recordings"]} == {100}
    assert body["total"] == 2, "`total` must count the FILTERED set, not the account"


def test_the_filter_still_only_sees_the_callers_own_recordings():
    """Scoping is the repo's, not the filter's — assert it did not move."""
    _repo, client = _client(count=4)
    assert client.get("/recordings", headers={"x-user-id": str(OTHER)}).json()["recordings"] == []
