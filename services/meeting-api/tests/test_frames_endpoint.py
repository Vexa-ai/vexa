"""Tests for /recordings/{id}/frames and /recordings/{id}/frames/{frame_id}/url endpoints."""

import os
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import (
    TEST_USER_ID,
    TEST_MEETING_ID,
    make_meeting,
    make_user,
    MockResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_recording_with_frames(recording_id=1001):
    """Build a meeting.data dict with a completed recording and frames_status."""
    return {
        "recordings": [
            {
                "id": recording_id,
                "meeting_id": TEST_MEETING_ID,
                "user_id": TEST_USER_ID,
                "session_uid": "sess-1",
                "source": "bot",
                "status": "completed",
                "created_at": "2025-01-01T00:00:00",
                "completed_at": "2025-01-01T00:05:00",
                "frames_status": "complete",
                "media_files": [
                    {
                        "id": 1,
                        "type": "video",
                        "format": "webm",
                        "storage_path": "recordings/5/1001/sess-1/master.webm",
                        "is_final": True,
                        "finalized_by": "recording_finalizer.master",
                    }
                ],
            }
        ]
    }


def _make_frame_orm(frame_id=1, timestamp_s=0, storage_path="recordings/5/1001/sess-1/frames/000001.webp"):
    """Create a mock RecordingFrame ORM object."""
    frame = MagicMock()
    frame.id = frame_id
    frame.timestamp_s = timestamp_s
    frame.storage_path = storage_path
    frame.recording_id = 1001
    frame.meeting_id = TEST_MEETING_ID
    frame.session_uid = "sess-1"
    return frame


# ---------------------------------------------------------------------------
# Test: GET /recordings/{id}/frames
# ---------------------------------------------------------------------------

class TestGetRecordingFrames:

    @pytest.mark.asyncio
    async def test_returns_correct_shape(self, client, mock_db):
        """GET /recordings/{id}/frames returns {extraction_status, frames[], total}."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "true"}):
            with patch("meeting_api.recordings.get_recording_metadata_mode", return_value="meeting_data"):
                meeting = make_meeting(data=_make_recording_with_frames())
                mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

                # Mock frame rows
                frame1 = _make_frame_orm(1, 0)
                frame2 = _make_frame_orm(2, 30)
                frame_result = MagicMock()
                frame_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[frame1, frame2])))
                mock_db.execute = AsyncMock(side_effect=[
                    MockResult([meeting]),  # First call: _find_meeting_data_recording
                    frame_result,            # Second call: RecordingFrame query
                ])

                with patch("meeting_api.recordings.create_storage_client") as mock_storage:
                    mock_storage_instance = MagicMock()
                    mock_storage_instance.get_presigned_url = MagicMock(
                        return_value="https://minio:9000/vexa/frame.webp?X-Amz-Signature=abc&X-Amz-Expires=900"
                    )
                    mock_storage.return_value = mock_storage_instance

                    resp = await client.get("/recordings/1001/frames")

        assert resp.status_code == 200
        data = resp.json()
        assert "extraction_status" in data
        assert "frames" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_auth_required(self, unauthed_client):
        """GET /recordings/{id}/frames without auth returns 403."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "true"}):
            resp = await unauthed_client.get("/recordings/1001/frames")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_disabled_returns_404(self, client, mock_db):
        """SNAPSHOTS_ENABLED=false returns 404 with 'snapshots disabled'."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "false"}):
            resp = await client.get("/recordings/1001/frames")
        assert resp.status_code == 404
        data = resp.json()
        assert "snapshots" in data.get("detail", "").lower() or "disabled" in data.get("detail", "").lower()

    @pytest.mark.asyncio
    async def test_processing_status(self, client, mock_db):
        """When extraction_status is 'processing', returns status and empty frames."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "true"}):
            processing_data = {
                "recordings": [{
                    "id": 1001,
                    "meeting_id": TEST_MEETING_ID,
                    "user_id": TEST_USER_ID,
                    "session_uid": "sess-1",
                    "source": "bot",
                    "status": "completed",
                    "frames_status": "processing",
                    "media_files": [],
                }]
            }
            meeting = make_meeting(data=processing_data)
            mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

            with patch("meeting_api.recordings.get_recording_metadata_mode", return_value="meeting_data"):
                resp = await client.get("/recordings/1001/frames")

        assert resp.status_code == 200
        data = resp.json()
        assert data["extraction_status"] == "processing"
        assert data["total"] == 0
        assert data["frames"] == []


# ---------------------------------------------------------------------------
# Test: GET /recordings/{id}/frames/{frame_id}/url
# ---------------------------------------------------------------------------

class TestGetFrameUrl:

    @pytest.mark.asyncio
    async def test_returns_presigned_url(self, client, mock_db):
        """GET /recordings/{id}/frames/{frame_id}/url returns single presigned URL."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "true"}):
            with patch("meeting_api.recordings.get_recording_metadata_mode", return_value="meeting_data"):
                meeting = make_meeting(data=_make_recording_with_frames())
                mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

                frame = _make_frame_orm(1, 0)
                frame_result = MagicMock()
                frame_result.scalars = MagicMock(return_value=MagicMock(first=MagicMock(return_value=frame)))
                mock_db.execute = AsyncMock(side_effect=[
                    MockResult([meeting]),  # _find_meeting_data_recording
                    frame_result,             # RecordingFrame query
                ])

                with patch("meeting_api.recordings.create_storage_client") as mock_storage:
                    mock_storage_instance = MagicMock()
                    mock_storage_instance.get_presigned_url = MagicMock(
                        return_value="https://minio:9000/vexa/frame.webp?X-Amz-Signature=abc&X-Amz-Expires=900"
                    )
                    mock_storage.return_value = mock_storage_instance

                    resp = await client.get("/recordings/1001/frames/1/url")

        assert resp.status_code == 200
        data = resp.json()
        assert "url" in data
        assert data["url"].startswith("http")

    @pytest.mark.asyncio
    async def test_frame_url_auth_required(self, unauthed_client):
        """GET /recordings/{id}/frames/{frame_id}/url without auth returns 403."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "true"}):
            resp = await unauthed_client.get("/recordings/1001/frames/1/url")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_frame_url_disabled_returns_404(self, client, mock_db):
        """SNAPSHOTS_ENABLED=false on JIT URL endpoint returns 404."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "false"}):
            resp = await client.get("/recordings/1001/frames/1/url")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_frame_url_not_found_for_other_user(self, client, mock_db):
        """Frame URL for a recording not owned by user returns 404."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "true"}):
            with patch("meeting_api.recordings.get_recording_metadata_mode", return_value="meeting_data"):
                # _find_meeting_data_recording returns None for other user's recording
                mock_db.execute = AsyncMock(return_value=MockResult([]))

                resp = await client.get("/recordings/99999/frames/1/url")

        assert resp.status_code == 404