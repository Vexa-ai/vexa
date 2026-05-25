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

    storage = MagicMock()
    storage.list_objects_bounded.return_value = [
        "recordings/1523/735125303957/213160c7-e317-4427-a928-ffbeb5ae61d8/audio/000000.webm",
        "recordings/1523/735125303957/213160c7-e317-4427-a928-ffbeb5ae61d8/audio/master.webm",
        "recordings/1523/999999999999/other-session/audio/000000.webm",
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
    storage.list_objects_bounded.assert_not_called()
    db.commit.assert_not_called()
    finalize.assert_awaited_once_with(10063, db)
