"""
AIS-159 — test fail-first: playback_url = None aunque media_files existen

Este test valida que el backend deriva playback_url desde media_files.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from httpx import AsyncClient
from datetime import datetime
from meeting_api.models import Meeting


@pytest.mark.asyncio
async def test_completed_recording_has_playback_url(client: AsyncClient, mock_db):
    """
    GIVEN: DB returns meeting with recordings + media_files (no playback_url)

    WHEN: GET /transcripts/google_meet/aaa-bbbb-ccc

    THEN: response includes playback_url.audio and playback_url.video derived from media_files
    """
    # GIVEN: mock meeting with recordings + media_files
    meeting_dict = {
        "id": 42,
        "user_id": 1,
        "platform": "google_meet",
        "native_meeting_id": "aaa-bbbb-ccc",
        "platform_specific_id": "aaa-bbbb-ccc",
        "constructed_meeting_url": "https://meet.google.com/aaa-bbbb-ccc",
        "status": "completed",
        "start_time": datetime.utcnow(),
        "end_time": datetime.utcnow(),
        "bot_container_id": None,
        "data": {
            "recordings": [
                {
                    "id": 100,
                    "status": "completed",
                    "source": "bot",
                    "media_files": [
                        {
                            "id": 201,
                            "type": "audio",
                            "format": "webm",
                            "storage_path": "recordings/1/100/session-123/audio/master.webm",
                            "storage_backend": "minio",
                        },
                        {
                            "id": 202,
                            "type": "video",
                            "format": "webm",
                            "storage_path": "recordings/1/100/session-123/video/master.webm",
                            "storage_backend": "minio",
                        },
                    ]
                }
            ],
            "notes": None,
            "speaker_events": []
        },
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }

    # Crear MagicMock del modelo Meeting
    mock_meeting = MagicMock(spec=Meeting)
    for k, v in meeting_dict.items():
        setattr(mock_meeting, k, v)

    # Configurar mock_db.execute para devolver la meeting
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_meeting
    mock_result.scalars.return_value.all.return_value = []  # Empty segments

    async def fake_execute(stmt):
        return mock_result

    mock_db.execute = fake_execute
    mock_db.get = AsyncMock(return_value=mock_meeting)

    # Reaplicar overrides (conftest fixture solo se ejecuta una vez)
    from meeting_api.main import app
    from meeting_api.database import get_db

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db

    # WHEN: GET /transcripts/google_meet/aaa-bbbb-ccc
    response = await client.get("/transcripts/google_meet/aaa-bbbb-ccc")

    # THEN: 200 OK
    assert response.status_code == 200

    data = response.json()

    # THEN: hay recordings
    assert "recordings" in data
    assert len(data["recordings"]) == 1

    rec = data["recordings"][0]

    # THEN: playback_url está derivado (NO es None)
    assert "playback_url" in rec, "playback_urlDebe estar presente"
    assert rec["playback_url"] is not None, "playback_url no debe ser None"

    # THEN: audio y video URLs están pobladas
    assert rec["playback_url"]["audio"] is not None, "playback_url.audio no debe ser None"
    assert rec["playback_url"]["video"] is not None, "playback_url.video no debe ser None"

    # THEN: URLs apuntan al endpoint correcto
    audio_url = rec["playback_url"]["audio"]
    video_url = rec["playback_url"]["video"]

    assert "/api/vexa/recordings/100/media/201/download" in audio_url
    assert "/api/vexa/recordings/100/media/202/download" in video_url

    # Cleanup
    app.dependency_overrides.clear()