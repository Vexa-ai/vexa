"""Tests for recording endpoints — /recordings/*."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import (
    TEST_USER_ID,
    TEST_MEETING_ID,
    TEST_PLATFORM,
    TEST_NATIVE_MEETING_ID,
    make_meeting,
    MockResult,
)


# ===================================================================
# GET /recordings
# ===================================================================


class TestListRecordings:

    @pytest.mark.asyncio
    async def test_list_recordings_empty(self, client, mock_db):
        """GET /recordings when no recordings → empty list."""
        mock_db.execute = AsyncMock(return_value=MockResult([]))

        resp = await client.get("/recordings")

        assert resp.status_code == 200
        data = resp.json()
        assert "recordings" in data
        assert data["recordings"] == []

    @pytest.mark.asyncio
    async def test_list_recordings_with_meeting_data(self, client, mock_db):
        """GET /recordings returns recordings from meeting.data."""
        meeting = make_meeting(data={
            "recordings": [{
                "id": 1001,
                "meeting_id": TEST_MEETING_ID,
                "user_id": TEST_USER_ID,
                "session_uid": "sess-1",
                "source": "bot",
                "status": "completed",
                "created_at": "2025-01-01T00:00:00",
                "completed_at": "2025-01-01T00:05:00",
                "media_files": [],
            }],
        })
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        with patch("meeting_api.recordings.get_recording_metadata_mode", return_value="meeting_data"):
            resp = await client.get("/recordings")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["recordings"]) == 1
        rec = data["recordings"][0]
        # Frozen field set
        expected_fields = {
            "id", "meeting_id", "user_id", "session_uid", "source",
            "status", "error_message", "created_at", "completed_at",
            "media_files",
        }
        assert expected_fields.issubset(set(rec.keys()))

    @pytest.mark.asyncio
    async def test_list_recordings_auth_required(self, unauthed_client):
        """GET /recordings without auth → 403."""
        resp = await unauthed_client.get("/recordings")
        assert resp.status_code == 403


# ===================================================================
# GET /recordings/{id}
# ===================================================================


class TestGetRecording:

    @pytest.mark.asyncio
    async def test_get_recording_not_found(self, client, mock_db):
        """GET /recordings/{id} for nonexistent → 404."""
        mock_db.execute = AsyncMock(return_value=MockResult([]))
        mock_db.get = AsyncMock(return_value=None)

        with patch("meeting_api.recordings.get_recording_metadata_mode", return_value="meeting_data"):
            resp = await client.get("/recordings/99999")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_recording_success(self, client, mock_db):
        """GET /recordings/{id} returns RecordingResponse shape."""
        meeting = make_meeting(data={
            "recordings": [{
                "id": 1001,
                "meeting_id": TEST_MEETING_ID,
                "user_id": TEST_USER_ID,
                "session_uid": "sess-1",
                "source": "bot",
                "status": "completed",
                "created_at": "2025-01-01T00:00:00",
                "completed_at": "2025-01-01T00:05:00",
                "media_files": [{
                    "id": 2001,
                    "type": "audio",
                    "format": "wav",
                    "storage_backend": "minio",
                    "file_size_bytes": 1024,
                    "duration_seconds": 60.0,
                    "metadata": {},
                    "created_at": "2025-01-01T00:05:00",
                }],
            }],
        })
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        with patch("meeting_api.recordings.get_recording_metadata_mode", return_value="meeting_data"):
            resp = await client.get("/recordings/1001")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1001
        assert len(data["media_files"]) == 1


# ===================================================================
# DELETE /recordings/{id}
# ===================================================================


class TestDeleteRecording:

    @pytest.mark.asyncio
    async def test_delete_recording_not_found(self, client, mock_db):
        """DELETE /recordings/{id} for nonexistent → 404."""
        mock_db.execute = AsyncMock(return_value=MockResult([]))
        mock_db.get = AsyncMock(return_value=None)

        with patch("meeting_api.recordings.get_recording_metadata_mode", return_value="meeting_data"):
            resp = await client.delete("/recordings/99999")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_recording_success(self, client, mock_db):
        """DELETE /recordings/{id} → removes from meeting.data."""
        meeting = make_meeting(data={
            "recordings": [{
                "id": 1001,
                "meeting_id": TEST_MEETING_ID,
                "user_id": TEST_USER_ID,
                "session_uid": "sess-1",
                "source": "bot",
                "status": "completed",
                "media_files": [],
            }],
        })
        mock_db.execute = AsyncMock(return_value=MockResult([meeting]))

        with patch("meeting_api.recordings.get_recording_metadata_mode", return_value="meeting_data"):
            mock_storage = MagicMock()
            with patch("meeting_api.recordings.get_storage_client", return_value=mock_storage):
                with patch("meeting_api.recordings.attributes.flag_modified", MagicMock()):
                    resp = await client.delete("/recordings/1001")

        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
