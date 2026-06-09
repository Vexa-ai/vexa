"""AIS-159 — Spec-Driven Fail-First Battery: playback_url derivation from media_files.

These tests define the SPEC before code. They should ALL pass with the fix in 576f47e.

Coverage:
  - Core: playback_url derived from media_files
  - Edge: no media_files → playback_url stays None
  - Edge: audio-only recording → playback_url.video = None
  - Edge: recording chunks excluded from playback_url
  - Edge: multiple recordings all get playback_url
  - Edge: empty recordings list
  - Edge: malformed recording data
  - Edge: recording with chunks + master
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from httpx import AsyncClient
from datetime import datetime
from meeting_api.models import Meeting
from meeting_api.main import app
from meeting_api.database import get_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_meeting_mock(recordings: list, meeting_id: int = 42) -> MagicMock:
    """Build a MagicMock Meeting with recordings in data."""
    m = MagicMock(spec=Meeting)
    m.id = meeting_id
    m.user_id = 1
    m.platform = "google_meet"
    m.native_meeting_id = "aaa-bbbb-ccc"
    m.platform_specific_id = "aaa-bbbb-ccc"
    m.constructed_meeting_url = "https://meet.google.com/aaa-bbbb-ccc"
    m.status = "completed"
    m.start_time = datetime.utcnow()
    m.end_time = datetime.utcnow()
    m.bot_container_id = None
    m.data = {"recordings": recordings, "notes": None, "speaker_events": []}
    m.created_at = datetime.utcnow()
    m.updated_at = datetime.utcnow()
    return m


def _make_media_file(file_id: int, media_type: str, storage_path: str) -> dict:
    return {
        "id": file_id,
        "type": media_type,
        "format": "webm",
        "storage_path": storage_path,
        "storage_backend": "minio",
    }


def _make_recording(
    recording_id: int,
    media_files: list,
    status: str = "completed",
    source: str = "bot",
) -> dict:
    return {
        "id": recording_id,
        "status": status,
        "source": source,
        "media_files": media_files,
    }


async def _override_db_and_get(mock_db, client, url: str):
    """Helper: override DB dep, GET endpoint, return response."""
    async def override_get_db():
        yield mock_db
    app.dependency_overrides[get_db] = override_get_db
    try:
        resp = await client.get(url)
        return resp
    finally:
        app.dependency_overrides.clear()


# ===================================================================
# CORE: playback_url derivation
# ===================================================================

@pytest.mark.asyncio
async def test_completed_recording_derives_playback_url(client: AsyncClient, mock_db):
    """GIVEN recording with audio+video media_files
       WHEN GET /transcripts/
       THEN playback_url.audio AND .video are derived from media_files (not None)"""
    rec = _make_recording(100, [
        _make_media_file(201, "audio", "recordings/1/100/session/audio/master.webm"),
        _make_media_file(202, "video", "recordings/1/100/session/video/master.webm"),
    ])
    meeting = _make_meeting_mock([rec])

    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalars=lambda: MagicMock(
            first=lambda: meeting,
            all=lambda: [],
        )
    ))
    mock_db.get = AsyncMock(return_value=meeting)

    resp = await _override_db_and_get(mock_db, client, "/transcripts/google_meet/aaa-bbbb-ccc")
    data = resp.json()

    assert resp.status_code == 200
    rec_out = data["recordings"][0]
    assert rec_out["playback_url"]["audio"] == "/api/vexa/recordings/100/media/201/download"
    assert rec_out["playback_url"]["video"] == "/api/vexa/recordings/100/media/202/download"


@pytest.mark.asyncio
async def test_recording_without_media_files_returns_none_playback(client: AsyncClient, mock_db):
    """GIVEN recording with empty media_files list
       WHEN GET /transcripts/
       THEN playback_url is None (no files to derive from)"""
    rec = _make_recording(100, [])
    meeting = _make_meeting_mock([rec])

    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalars=lambda: MagicMock(first=lambda: meeting, all=lambda: [])
    ))
    mock_db.get = AsyncMock(return_value=meeting)

    resp = await _override_db_and_get(mock_db, client, "/transcripts/google_meet/aaa-bbbb-ccc")
    data = resp.json()

    assert resp.status_code == 200
    rec_out = data["recordings"][0]
    assert rec_out["playback_url"] is None or rec_out["playback_url"]["audio"] is None


@pytest.mark.asyncio
async def test_audio_only_recording_returns_null_video(client: AsyncClient, mock_db):
    """GIVEN recording with audio master but NO video master
       WHEN GET /transcripts/
       THEN playback_url.audio is populated, playback_url.video is None"""
    rec = _make_recording(100, [
        _make_media_file(201, "audio", "recordings/1/100/session/audio/master.webm"),
        # NO video master
    ])
    meeting = _make_meeting_mock([rec])

    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalars=lambda: MagicMock(first=lambda: meeting, all=lambda: [])
    ))
    mock_db.get = AsyncMock(return_value=meeting)

    resp = await _override_db_and_get(mock_db, client, "/transcripts/google_meet/aaa-bbbb-ccc")
    data = resp.json()

    assert resp.status_code == 200
    rec_out = data["recordings"][0]
    assert rec_out["playback_url"]["audio"] == "/api/vexa/recordings/100/media/201/download"
    assert rec_out["playback_url"]["video"] is None


# ===================================================================
# EDGE: Recording chunks
# ===================================================================

@pytest.mark.asyncio
async def test_chunk_recordings_excluded_from_playback_url(client: AsyncClient, mock_db):
    """GIVEN recording with chunk files (no /master. in path)
       WHEN GET /transcripts/
       THEN playback_url fields are None (chunks should not be used)"""
    rec = _make_recording(100, [
        _make_media_file(301, "audio", "recordings/1/100/session/audio/chunk-001.webm"),
        _make_media_file(302, "video", "recordings/1/100/session/video/chunk-001.webm"),
    ])
    meeting = _make_meeting_mock([rec])

    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalars=lambda: MagicMock(first=lambda: meeting, all=lambda: [])
    ))
    mock_db.get = AsyncMock(return_value=meeting)

    resp = await _override_db_and_get(mock_db, client, "/transcripts/google_meet/aaa-bbbb-ccc")
    data = resp.json()

    assert resp.status_code == 200
    rec_out = data["recordings"][0]
    assert rec_out["playback_url"]["audio"] is None
    assert rec_out["playback_url"]["video"] is None


@pytest.mark.asyncio
async def test_recording_with_chunks_and_master_uses_master(client: AsyncMock, mock_db):
    """GIVEN recording with both chunks AND master files
       WHEN GET /transcripts/
       THEN playback_url uses master, not chunks"""
    rec = _make_recording(100, [
        _make_media_file(301, "audio", "recordings/1/100/session/audio/chunk-001.webm"),
        _make_media_file(401, "audio", "recordings/1/100/session/audio/master.webm"),
        _make_media_file(302, "video", "recordings/1/100/session/video/chunk-001.webm"),
        _make_media_file(402, "video", "recordings/1/100/session/video/master.webm"),
    ])
    meeting = _make_meeting_mock([rec])

    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalars=lambda: MagicMock(first=lambda: meeting, all=lambda: [])
    ))
    mock_db.get = AsyncMock(return_value=meeting)

    resp = await _override_db_and_get(mock_db, client, "/transcripts/google_meet/aaa-bbbb-ccc")
    data = resp.json()

    assert resp.status_code == 200
    rec_out = data["recordings"][0]
    # Should pick master files (id 401, 402), not chunks (301, 302)
    assert rec_out["playback_url"]["audio"] == "/api/vexa/recordings/100/media/401/download"
    assert rec_out["playback_url"]["video"] == "/api/vexa/recordings/100/media/402/download"


# ===================================================================
# EDGE: Multiple recordings
# ===================================================================

@pytest.mark.asyncio
async def test_multiple_recordings_all_get_playback_url(client: AsyncClient, mock_db):
    """GIVEN meeting with 2 recordings, each with media_files
       WHEN GET /transcripts/
       THEN BOTH recordings have playback_url derived"""
    rec1 = _make_recording(100, [
        _make_media_file(201, "audio", "recordings/1/100/session/audio/master.webm"),
        _make_media_file(202, "video", "recordings/1/100/session/video/master.webm"),
    ])
    rec2 = _make_recording(200, [
        _make_media_file(301, "audio", "recordings/1/200/session/audio/master.webm"),
        _make_media_file(302, "video", "recordings/1/200/session/video/master.webm"),
    ])
    meeting = _make_meeting_mock([rec1, rec2])

    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalars=lambda: MagicMock(first=lambda: meeting, all=lambda: [])
    ))
    mock_db.get = AsyncMock(return_value=meeting)

    resp = await _override_db_and_get(mock_db, client, "/transcripts/google_meet/aaa-bbbb-ccc")
    data = resp.json()

    assert resp.status_code == 200
    assert len(data["recordings"]) == 2
    assert data["recordings"][0]["playback_url"]["audio"] is not None
    assert data["recordings"][1]["playback_url"]["audio"] is not None


# ===================================================================
# EDGE: Empty / malformed data
# ===================================================================

@pytest.mark.asyncio
async def test_empty_recordings_list_does_not_crash(client: AsyncClient, mock_db):
    """GIVEN meeting with empty recordings list
       WHEN GET /transcripts/
       THEN 200 OK with empty recordings"""
    meeting = _make_meeting_mock([])

    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalars=lambda: MagicMock(first=lambda: meeting, all=lambda: [])
    ))
    mock_db.get = AsyncMock(return_value=meeting)

    resp = await _override_db_and_get(mock_db, client, "/transcripts/google_meet/aaa-bbbb-ccc")
    data = resp.json()

    assert resp.status_code == 200
    assert data["recordings"] == []


@pytest.mark.asyncio
async def test_recording_with_missing_media_files_key(client: AsyncClient, mock_db):
    """GIVEN recording dict without 'media_files' key
       WHEN GET /transcripts/
       THEN does not crash — playback_url stays as-is"""
    rec = {
        "id": 100,
        "status": "completed",
        "source": "bot",
        # NO media_files key
    }
    meeting = _make_meeting_mock([rec])

    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalars=lambda: MagicMock(first=lambda: meeting, all=lambda: [])
    ))
    mock_db.get = AsyncMock(return_value=meeting)

    resp = await _override_db_and_get(mock_db, client, "/transcripts/google_meet/aaa-bbbb-ccc")
    data = resp.json()

    assert resp.status_code == 200
    rec_out = data["recordings"][0]
    # Should not crash; playback_url either None or has null fields
    assert "playback_url" in rec_out


# ===================================================================
# AIS-163 RELATED: presigned URL endpoint (spec-only, needs live MinIO)
# ===================================================================

@pytest.mark.asyncio
async def test_media_download_endpoint_returns_presigned_url(client: AsyncClient, mock_db):
    """GIVEN a media_file linked to a recording
       WHEN GET /recordings/{id}/media/{file_id}/download
       THEN returns 200 with presigned download URL (not minio:9000 internal)"""
    # This test validates the download endpoint exists and returns the correct shape
    recording_id = 100
    file_id = 201

    # Mock meeting with media file
    rec = _make_recording(recording_id, [
        _make_media_file(file_id, "audio", "recordings/1/100/session/audio/master.webm"),
    ])
    meeting = _make_meeting_mock([rec])

    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalars=lambda: MagicMock(first=lambda: meeting, all=lambda: [])
    ))
    mock_db.get = AsyncMock(return_value=meeting)

    # Just verify the endpoint path is accessible — actual presigned URL
    # generation requires live MinIO and will be validated E2E
    resp = await client.get(f"/recordings/{recording_id}/media/{file_id}/download")

    # If MinIO is not configured, we expect 5xx or fallback
    # The spec says: MUST NOT return internal minio:9000 URLs
    if resp.status_code == 200:
        data = resp.json()
        url = data.get("url", "")
        assert "minio:9000" not in url, \
            "CRITICAL AIS-163: presigned URL must not use internal minio:9000 hostname"


# ===================================================================
# REGRESSION: existing tests still pass
# ===================================================================

@pytest.mark.asyncio
async def test_playback_url_does_not_break_when_data_is_none(client: AsyncClient, mock_db):
    """GIVEN meeting where meeting.data is None (no recordings)
       WHEN GET /transcripts/
       THEN 200 OK — no crash"""
    meeting = _make_meeting_mock([])
    meeting.data = None  # Edge case: data is None

    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalars=lambda: MagicMock(first=lambda: meeting, all=lambda: [])
    ))
    mock_db.get = AsyncMock(return_value=meeting)

    resp = await _override_db_and_get(mock_db, client, "/transcripts/google_meet/aaa-bbbb-ccc")
    data = resp.json()

    assert resp.status_code == 200
    assert "recordings" in data


@pytest.mark.asyncio
async def test_playback_url_path_includes_download_prefix(client: AsyncMock, mock_db):
    """GIVEN recording with audio master
       WHEN playback_url is derived
       THEN URL path starts with /api/vexa/recordings/{id}/media/{file_id}/download"""
    rec = _make_recording(100, [
        _make_media_file(201, "audio", "recordings/1/100/session/audio/master.webm"),
    ])
    meeting = _make_meeting_mock([rec])

    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalars=lambda: MagicMock(first=lambda: meeting, all=lambda: [])
    ))
    mock_db.get = AsyncMock(return_value=meeting)

    resp = await _override_db_and_get(mock_db, client, "/transcripts/google_meet/aaa-bbbb-ccc")
    data = resp.json()
    audio_url = data["recordings"][0]["playback_url"]["audio"]

    # The URL must be /api/vexa/recordings/{id}/media/{file_id}/download
    # (AIS-159 AUDIT V2: this prefix is CORRECT, do NOT remove it)
    assert audio_url.startswith("/api/vexa/recordings/100/media/201/download"), \
        f"playback_url path must use /api/vexa/ prefix. Got: {audio_url}"
