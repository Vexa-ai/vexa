from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_api import sweeps
from meeting_api.schemas import MeetingStatus

from .conftest import MockResult, make_meeting, make_session


class FetchAllResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


@pytest.mark.asyncio
async def test_sweep_unfinalized_recordings_recovers_missing_jsonb_from_storage_chunks():
    meeting = make_meeting(
        id=10062,
        user_id=1523,
        status=MeetingStatus.COMPLETED.value,
        data={"recording_enabled": True},
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )
    session = make_session(
        meeting_id=10062,
        session_uid="213160c7-e317-4427-a928-ffbeb5ae61d8",
    )

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            FetchAllResult([(10062,)]),
            MockResult(items=[meeting]),
            MockResult(items=[session]),
        ]
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    @asynccontextmanager
    async def db_session_factory():
        yield db

    # The recording id sits BETWEEN user and session in the key layout, so the
    # sweep enumerates per-recording prefixes, then lists only the matching
    # <user>/<rec>/<session>/ slice — never the whole-user prefix.
    storage = MagicMock()
    storage.list_common_prefixes.return_value = ["recordings/1523/735125303957/"]
    storage.list_objects_bounded.return_value = [
        "recordings/1523/735125303957/213160c7-e317-4427-a928-ffbeb5ae61d8/audio/000000.webm",
        "recordings/1523/735125303957/213160c7-e317-4427-a928-ffbeb5ae61d8/audio/master.webm",
    ]

    with patch("meeting_api.storage.create_storage_client", return_value=storage), \
         patch("meeting_api.recording_finalizer.finalize_recording_master", new=AsyncMock()) as finalize, \
         patch.object(sweeps.attributes, "flag_modified", new=MagicMock()) as flag_modified:
        swept = await sweeps._sweep_unfinalized_recordings(db_session_factory)

    assert swept == 1
    assert meeting.data["recordings"][0]["id"] == 735125303957
    assert meeting.data["recordings"][0]["session_uid"] == session.session_uid
    assert meeting.data["recordings"][0]["media_files"][0]["storage_path"].endswith("/audio/000000.webm")
    assert meeting.data["recordings"][0]["media_files"][0]["is_final"] is False
    # Scoping: the only object listing is the session-scoped slice, not the
    # user-wide prefix that previously froze the event loop / truncated at 10k.
    storage.list_common_prefixes.assert_called_once_with("recordings/1523/")
    storage.list_objects_bounded.assert_called_once_with(
        "recordings/1523/735125303957/213160c7-e317-4427-a928-ffbeb5ae61d8/"
    )
    db.commit.assert_awaited_once()
    finalize.assert_awaited_once_with(10062, db)
    flag_modified.assert_called_once_with(meeting, "data")


@pytest.mark.asyncio
async def test_sweep_unfinalized_recordings_finalizes_existing_jsonb_without_storage_recovery():
    meeting = make_meeting(
        id=10063,
        user_id=1523,
        status=MeetingStatus.COMPLETED.value,
        data={
            "recording_enabled": True,
            "recordings": [{
                "id": 735125303958,
                "session_uid": "sess-existing",
                "status": "completed",
                "media_files": [{
                    "type": "audio",
                    "format": "webm",
                    "storage_path": "recordings/1523/735125303958/sess-existing/audio/000000.webm",
                }],
            }],
        },
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            FetchAllResult([(10063,)]),
            MockResult(items=[meeting]),
            MockResult(items=[]),
        ]
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    @asynccontextmanager
    async def db_session_factory():
        yield db

    storage = MagicMock()

    with patch("meeting_api.storage.create_storage_client", return_value=storage), \
         patch("meeting_api.recording_finalizer.finalize_recording_master", new=AsyncMock()) as finalize:
        swept = await sweeps._sweep_unfinalized_recordings(db_session_factory)

    assert swept == 1
    # JSONB already present → no storage walk at all.
    storage.list_common_prefixes.assert_not_called()
    storage.list_objects_bounded.assert_not_called()
    db.commit.assert_not_called()
    finalize.assert_awaited_once_with(10063, db)


@pytest.mark.asyncio
async def test_list_session_chunks_is_session_scoped_and_offloaded(monkeypatch):
    """The hot path that caused the outage: prove the chunk listing is scoped
    to <user>/<rec>/<session>/ (never the bare user prefix) AND that every
    blocking boto3 call is dispatched through asyncio.to_thread so the event
    loop (and its /health + /readyz probes) never stalls."""
    storage = MagicMock()
    storage.list_common_prefixes.return_value = ["recordings/9/aaa/", "recordings/9/bbb/"]

    def fake_bounded(prefix):
        return ["recordings/9/bbb/sess-X/audio/000000.webm"] if "bbb/sess-X/" in prefix else []

    storage.list_objects_bounded.side_effect = fake_bounded

    offloaded = []
    real_to_thread = sweeps.asyncio.to_thread

    async def spy_to_thread(fn, *args, **kwargs):
        offloaded.append(fn)
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(sweeps.asyncio, "to_thread", spy_to_thread)

    keys = await sweeps._list_session_chunks(storage, 9, "sess-X")

    assert keys == ["recordings/9/bbb/sess-X/audio/000000.webm"]
    # User prefix used only for the cheap CommonPrefixes enumeration.
    storage.list_common_prefixes.assert_called_once_with("recordings/9/")
    # Object listing is scoped per <rec>/<session>/ — and crucially is NEVER
    # the bare "recordings/9/" user prefix.
    listed = [c.args[0] for c in storage.list_objects_bounded.call_args_list]
    assert listed == ["recordings/9/aaa/sess-X/", "recordings/9/bbb/sess-X/"]
    assert "recordings/9/" not in listed
    assert all(p.endswith("/sess-X/") for p in listed)
    # Both blocking calls went through to_thread — no synchronous boto3 on the loop.
    assert storage.list_common_prefixes in offloaded
    assert storage.list_objects_bounded in offloaded


@pytest.mark.asyncio
async def test_list_session_chunks_caps_pathological_prefix_count(monkeypatch):
    """A user with more recording prefixes than the cap is scanned in a bounded
    slice (newest tail) — never an unbounded scan."""
    n = sweeps.SESSION_CHUNK_SCAN_PREFIX_CAP + 50
    storage = MagicMock()
    storage.list_common_prefixes.return_value = [f"recordings/9/{i:06d}/" for i in range(n)]
    storage.list_objects_bounded.return_value = []

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(sweeps.asyncio, "to_thread", fake_to_thread)

    await sweeps._list_session_chunks(storage, 9, "sess-X")

    # Only the capped slice was probed for chunks.
    assert storage.list_objects_bounded.call_count == sweeps.SESSION_CHUNK_SCAN_PREFIX_CAP


@pytest.mark.asyncio
async def test_sweep_unfinalized_recordings_increments_attempt_when_no_chunks():
    """A terminal meeting whose session has no reconcilable chunks must count an
    attempt (toward the terminal cap) rather than silently re-list forever."""
    meeting = make_meeting(
        id=10070,
        user_id=1523,
        status=MeetingStatus.COMPLETED.value,
        data={"recording_enabled": True},
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )
    session = make_session(meeting_id=10070, session_uid="sess-nochunks")

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            FetchAllResult([(10070,)]),
            MockResult(items=[meeting]),
            MockResult(items=[session]),
        ]
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    @asynccontextmanager
    async def db_session_factory():
        yield db

    storage = MagicMock()
    storage.list_common_prefixes.return_value = ["recordings/1523/111/"]
    storage.list_objects_bounded.return_value = []  # nothing for this session

    with patch("meeting_api.storage.create_storage_client", return_value=storage), \
         patch("meeting_api.recording_finalizer.finalize_recording_master", new=AsyncMock()) as finalize, \
         patch.object(sweeps.attributes, "flag_modified", new=MagicMock()):
        swept = await sweeps._sweep_unfinalized_recordings(db_session_factory)

    assert swept == 0
    assert meeting.data[sweeps.UNFINALIZED_RECORDINGS_ATTEMPTS_KEY] == 1
    assert sweeps.UNFINALIZED_RECORDINGS_ABANDONED_KEY not in meeting.data
    finalize.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sweep_unfinalized_recordings_abandons_after_max_attempts():
    """At the attempt cap the meeting is flagged abandoned so the selection
    query stops returning it — converging the forever-loop."""
    meeting = make_meeting(
        id=10071,
        user_id=1523,
        status=MeetingStatus.COMPLETED.value,
        data={
            "recording_enabled": True,
            sweeps.UNFINALIZED_RECORDINGS_ATTEMPTS_KEY: sweeps.UNFINALIZED_RECORDINGS_MAX_ATTEMPTS - 1,
        },
        created_at=datetime.utcnow() - timedelta(minutes=10),
    )
    session = make_session(meeting_id=10071, session_uid="sess-nochunks")

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            FetchAllResult([(10071,)]),
            MockResult(items=[meeting]),
            MockResult(items=[session]),
        ]
    )
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    @asynccontextmanager
    async def db_session_factory():
        yield db

    storage = MagicMock()
    storage.list_common_prefixes.return_value = ["recordings/1523/111/"]
    storage.list_objects_bounded.return_value = []

    with patch("meeting_api.storage.create_storage_client", return_value=storage), \
         patch("meeting_api.recording_finalizer.finalize_recording_master", new=AsyncMock()), \
         patch.object(sweeps.attributes, "flag_modified", new=MagicMock()):
        swept = await sweeps._sweep_unfinalized_recordings(db_session_factory)

    assert swept == 0
    assert meeting.data[sweeps.UNFINALIZED_RECORDINGS_ATTEMPTS_KEY] == sweeps.UNFINALIZED_RECORDINGS_MAX_ATTEMPTS
    assert meeting.data[sweeps.UNFINALIZED_RECORDINGS_ABANDONED_KEY] is True
    assert f"{sweeps.UNFINALIZED_RECORDINGS_ABANDONED_KEY}_at" in meeting.data
