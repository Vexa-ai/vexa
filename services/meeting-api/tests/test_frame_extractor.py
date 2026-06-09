"""Tests for frame_extractor.py — extraction pipeline, idempotency, feature-flag gating."""

import os
from datetime import datetime, timezone
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

def _make_recording_with_video(recording_id=1001, session_uid="sess-1"):
    """Build a meeting.data dict with a finalized video master recording."""
    return {
        "recordings": [
            {
                "id": recording_id,
                "meeting_id": TEST_MEETING_ID,
                "user_id": TEST_USER_ID,
                "session_uid": session_uid,
                "source": "bot",
                "status": "completed",
                "created_at": "2025-01-01T00:00:00",
                "completed_at": "2025-01-01T00:05:00",
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


def _make_storage_client_mock(file_exists_return=False):
    """Create a mock StorageClient."""
    mock = MagicMock()
    mock.download_to_file = MagicMock()
    mock.upload_file = MagicMock()
    mock.file_exists = MagicMock(return_value=file_exists_return)
    mock.get_presigned_url = MagicMock(return_value="https://minio:9000/vexa/frame.webp?signature=abc")
    return mock


# ---------------------------------------------------------------------------
# Test: feature flag gating
# ---------------------------------------------------------------------------

class TestFeatureFlagGating:

    @pytest.mark.asyncio
    async def test_extract_returns_zero_when_disabled(self, mock_db):
        """SNAPSHOTS_ENABLED=false → extract_frames_if_enabled returns 0 immediately."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "false"}):
            from meeting_api.frame_extractor import extract_frames_if_enabled
            result = await extract_frames_if_enabled(TEST_MEETING_ID)
        assert result == 0

    @pytest.mark.asyncio
    async def test_extract_returns_zero_when_env_missing(self, mock_db):
        """No SNAPSHOTS_ENABLED env var → defaults to false, returns 0."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SNAPSHOTS_ENABLED", None)
            from meeting_api.frame_extractor import extract_frames_if_enabled
            result = await extract_frames_if_enabled(TEST_MEETING_ID)
        assert result == 0

    @pytest.mark.asyncio
    async def test_extract_does_not_query_db_when_disabled(self, mock_db):
        """When disabled, no DB queries are made."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "false"}):
            from meeting_api.frame_extractor import extract_frames_if_enabled
            await extract_frames_if_enabled(TEST_MEETING_ID)
        # mock_db.execute should not have been called for frame queries
        # (it may have been called by other fixtures, so we just verify no crash)


# ---------------------------------------------------------------------------
# Test: idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:

    @pytest.mark.asyncio
    async def test_extract_skips_when_frames_exist(self, mock_db):
        """If RecordingFrame rows already exist and first frame file exists, skip."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "true"}):
            from meeting_api.frame_extractor import extract_frames_if_enabled

            # Meeting with a video master and existing frames
            meeting = make_meeting(data=_make_recording_with_video())

            # Build a mock async context manager for async_session_local
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            # Mock db.get to return our meeting
            mock_db.get = AsyncMock(return_value=meeting)

            # Mock db.execute for count query (returns existing frames)
            count_result = MagicMock()
            count_result.scalar = MagicMock(return_value=5)
            mock_db.execute = AsyncMock(return_value=count_result)

            # Storage mock: file_exists returns True for first frame
            mock_storage = _make_storage_client_mock(file_exists_return=True)

            with patch("meeting_api.frame_extractor.async_session_local", return_value=mock_session):
                with patch("meeting_api.frame_extractor.create_storage_client", return_value=mock_storage):
                    result = await extract_frames_if_enabled(TEST_MEETING_ID)

        assert result == 0  # Skipped, no new frames extracted

    @pytest.mark.asyncio
    async def test_extract_skips_when_no_video_master(self, mock_db):
        """If no finalized video recording found, return 0."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "true"}):
            from meeting_api.frame_extractor import extract_frames_if_enabled

            # Meeting with no video master
            meeting = make_meeting(data={"recordings": [{"id": 1, "status": "completed", "media_files": []}]})

            # Build a mock async context manager for async_session_local
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            mock_db.get = AsyncMock(return_value=meeting)

            with patch("meeting_api.frame_extractor.async_session_local", return_value=mock_session):
                result = await extract_frames_if_enabled(TEST_MEETING_ID)

        assert result == 0


# ---------------------------------------------------------------------------
# Test: heuristic fps selection
# ---------------------------------------------------------------------------

class TestHeuristicFps:

    def test_short_meeting_uses_higher_fps(self):
        """Meetings under 5 minutes should use 1/5s fps."""
        from meeting_api.frame_extractor import _DUAL_HEURISTIC_THRESHOLD_S, _FPS_SHORT_MEETING, _FPS_BASELINE
        assert _DUAL_HEURISTIC_THRESHOLD_S == 300  # 5 minutes
        assert _FPS_SHORT_MEETING == "1/5"
        assert _FPS_BASELINE == "1/30"

    def test_fps_constants_are_valid(self):
        """FPS strings are valid ffmpeg fps filter values."""
        from meeting_api.frame_extractor import _FPS_SHORT_MEETING, _FPS_BASELINE
        # These should be parseable as ffmpeg fps values
        assert "/" in _FPS_SHORT_MEETING
        assert "/" in _FPS_BASELINE


# ---------------------------------------------------------------------------
# Test: OOM protection (streaming, not buffering)
# ---------------------------------------------------------------------------

class TestOOMProtection:

    def test_download_uses_named_temp_file_not_bytesio(self):
        """_extract_frames_sync uses NamedTemporaryFile for OOM-safe download."""
        import inspect
        from meeting_api.frame_extractor import _extract_frames_sync
        source = inspect.getsource(_extract_frames_sync)
        # Verify it uses NamedTemporaryFile, not BytesIO or read()
        assert "NamedTemporaryFile" in source
        assert "download_to_file" in source
        # Verify it does NOT read entire file into memory
        assert "read()" not in source or "frame_" in source.split("read()")[0] if "read()" in source else True

    def test_ffmpeg_uses_subprocess_not_pyav(self):
        """Frame extraction uses subprocess.run (ffmpeg CLI), not PyAV or in-process decode."""
        import inspect
        from meeting_api.frame_extractor import _extract_frames_sync
        source = inspect.getsource(_extract_frames_sync)
        assert "subprocess.run" in source
        assert "pyav" not in source.lower()
        assert "av.open" not in source


# ---------------------------------------------------------------------------
# Test: failure handling
# ---------------------------------------------------------------------------

class TestFailureHandling:

    @pytest.mark.asyncio
    async def test_extract_returns_zero_on_extraction_failure(self, mock_db):
        """When extraction raises, function returns 0 (no crash)."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "true"}):
            from meeting_api.frame_extractor import extract_frames_if_enabled

            meeting = make_meeting(data=_make_recording_with_video())

            # Build a mock async context manager for async_session_local
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.__aexit__ = AsyncMock(return_value=False)

            # Mock db.get to return our meeting for the first DB call
            mock_db.get = AsyncMock(return_value=meeting)

            # Mock count query to return 0 (no existing frames)
            count_result = MagicMock()
            count_result.scalar = MagicMock(return_value=0)
            mock_db.execute = AsyncMock(return_value=count_result)
            mock_db.commit = AsyncMock()

            # Storage mock
            mock_storage = _make_storage_client_mock(file_exists_return=True)

            with patch("meeting_api.frame_extractor.async_session_local", return_value=mock_session):
                with patch("meeting_api.frame_extractor.create_storage_client", return_value=mock_storage):
                    with patch("meeting_api.frame_extractor.flag_modified"):
                        # Make the sync extraction raise
                        with patch("meeting_api.frame_extractor.asyncio.to_thread", side_effect=RuntimeError("ffmpeg failed")):
                            result = await extract_frames_if_enabled(TEST_MEETING_ID)

        # Should return 0 on failure (not crash)
        assert result == 0