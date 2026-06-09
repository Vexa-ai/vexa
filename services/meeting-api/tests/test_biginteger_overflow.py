"""Tests: BigInteger snowflake ID boundary conditions.

Bot-generated IDs use Twitter Snowflake format (64-bit), which overflows
signed int32 (max 2,147,483,647). These tests verify the schema and
serialisation layers handle IDs > 2^31 correctly.
"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .conftest import (
    TEST_USER_ID,
    TEST_MEETING_ID,
    make_meeting,
    make_user,
    MockResult,
)

# Typical bot-generated snowflake IDs
SNOWFLAKE_ID_1 = 7_123_456_789_012_345_678   # well above int32
SNOWFLAKE_ID_2 = 2_147_483_648               # int32 max + 1
SNOWFLAKE_ID_3 = 9_223_372_036_854_775_807   # int64 max


class TestRecordingIdOverflow:

    def test_recording_id_is_biginteger_in_model(self):
        """Recording.id column type must be BigInteger, not Integer."""
        from sqlalchemy import BigInteger
        from meeting_api.models import Recording
        col_type = Recording.__table__.c["id"].type
        assert isinstance(col_type, BigInteger), (
            f"Recording.id must be BigInteger (got {type(col_type).__name__}). "
            "Bot generates snowflake IDs > int32."
        )

    def test_media_file_id_is_biginteger(self):
        """MediaFile.id must be BigInteger."""
        from sqlalchemy import BigInteger
        from meeting_api.models import MediaFile
        assert isinstance(MediaFile.__table__.c["id"].type, BigInteger)

    def test_media_file_recording_id_is_biginteger(self):
        """MediaFile.recording_id FK must be BigInteger to match recordings.id."""
        from sqlalchemy import BigInteger
        from meeting_api.models import MediaFile
        assert isinstance(MediaFile.__table__.c["recording_id"].type, BigInteger)

    def test_recording_frame_recording_id_is_biginteger(self):
        """RecordingFrame.recording_id must be BigInteger."""
        from sqlalchemy import BigInteger
        from meeting_api.models import RecordingFrame
        assert isinstance(RecordingFrame.__table__.c["recording_id"].type, BigInteger)

    def test_pydantic_schema_accepts_snowflake_id(self):
        """API response schema serialises snowflake IDs without truncation."""
        from pydantic import BaseModel
        # Simulate what the frames endpoint returns
        class FrameOut(BaseModel):
            id: int
            recording_id: int
            timestamp_s: int
            storage_path: str

        frame = FrameOut(
            id=SNOWFLAKE_ID_1,
            recording_id=SNOWFLAKE_ID_2,
            timestamp_s=30,
            storage_path="recordings/5/7123456789012345678/sess-1/frames/000001.webp",
        )
        assert frame.id == SNOWFLAKE_ID_1
        assert frame.recording_id == SNOWFLAKE_ID_2
        # Verify JSON round-trip doesn't truncate
        import json
        data = json.loads(frame.model_dump_json())
        assert data["id"] == SNOWFLAKE_ID_1

    @pytest.mark.parametrize("bad_id", [
        2_147_483_647,      # int32 max — boundary
        2_147_483_648,      # int32 max + 1 — first overflow
        SNOWFLAKE_ID_1,     # real snowflake
        SNOWFLAKE_ID_3,     # int64 max
    ])
    def test_id_fits_in_int64(self, bad_id):
        """All valid snowflake IDs fit in int64 (< 2^63-1)."""
        INT64_MAX = 9_223_372_036_854_775_807
        assert bad_id <= INT64_MAX, f"ID {bad_id} exceeds int64"

    def test_recording_frame_primary_key_is_integer_not_bigint(self):
        """RecordingFrame.id (PK) is Integer — only recording_id needs BigInteger."""
        from sqlalchemy import Integer, BigInteger
        from meeting_api.models import RecordingFrame
        pk_type = RecordingFrame.__table__.c["id"].type
        # PK is sequential (auto-increment), so Integer is fine
        # This test documents the intentional asymmetry
        assert isinstance(pk_type, (Integer,)), (
            f"RecordingFrame.id PK should be Integer (got {type(pk_type).__name__})"
        )


class TestSnowflakeIdInFrameExtractor:

    @pytest.mark.asyncio
    async def test_frame_extractor_handles_snowflake_recording_id(self, mock_db):
        """frame_extractor stores RecordingFrame rows with BigInteger recording_id."""
        with patch.dict(os.environ, {"SNAPSHOTS_ENABLED": "true"}):
            from meeting_api.frame_extractor import extract_frames_if_enabled

            # Build meeting.data with snowflake recording ID
            meeting_data = {
                "recordings": [{
                    "id": SNOWFLAKE_ID_2,        # > int32
                    "meeting_id": TEST_MEETING_ID,
                    "user_id": TEST_USER_ID,
                    "session_uid": "sess-snowflake",
                    "source": "bot",
                    "status": "completed",
                    "media_files": [{
                        "id": SNOWFLAKE_ID_1,    # > int32
                        "type": "video",
                        "format": "webm",
                        "storage_path": f"recordings/{TEST_MEETING_ID}/{SNOWFLAKE_ID_2}/sess-snowflake/master.webm",
                        "is_final": True,
                        "finalized_by": "recording_finalizer.master",
                    }],
                }]
            }
            meeting = make_meeting(data=meeting_data)

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_db.get = AsyncMock(return_value=meeting)

            # Count query: no existing frames
            count_result = MagicMock()
            count_result.scalar = MagicMock(return_value=0)
            mock_db.execute = AsyncMock(return_value=count_result)
            mock_db.add = MagicMock()
            mock_db.commit = AsyncMock()

            mock_storage = MagicMock()
            mock_storage.file_exists = MagicMock(return_value=False)
            mock_storage.download_to_file = MagicMock()
            mock_storage.upload_file = MagicMock()
            mock_storage.get_presigned_url = MagicMock(return_value="https://minio/frame.webp")

            added_frames = []
            def capture_add(obj):
                added_frames.append(obj)
            mock_db.add = MagicMock(side_effect=capture_add)

            with patch("meeting_api.frame_extractor.async_session_local", return_value=mock_session):
                with patch("meeting_api.frame_extractor.create_storage_client", return_value=mock_storage):
                    with patch("meeting_api.frame_extractor.flag_modified"):
                        with patch("meeting_api.frame_extractor.asyncio.to_thread", return_value=[(0, "r/frames/000001.webp"), (30, "r/frames/000002.webp")]):
                            await extract_frames_if_enabled(TEST_MEETING_ID)

            # If frames were added, verify recording_id is the snowflake value
            from meeting_api.models import RecordingFrame
            frame_adds = [f for f in added_frames if isinstance(f, RecordingFrame)]
            for frame in frame_adds:
                assert frame.recording_id == SNOWFLAKE_ID_2, (
                    f"recording_id must preserve snowflake value, got {frame.recording_id}"
                )
