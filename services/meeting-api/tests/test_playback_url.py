"""
AIS-159 — Spec-driven test fail-first: endpoint correcto para playback_url

El endpoint de download real NO tiene /api/vexa prefix.
La ruta correcta: /recordings/{rec_id}/media/{file_id}/download

Este test DEBE FALLAR con el codigo actual (que usa /api/vexa).
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from httpx import AsyncClient
from datetime import datetime
from meeting_api.models import Meeting


@pytest.mark.asyncio
async def test_playback_url_uses_correct_endpoint_path(client: AsyncClient, mock_db):
    """
    GIVEN: DB returns meeting con recordings + media_files

    WHEN: GET /transcripts/google_meet/aaa-bbbb-ccc

    THEN: playback_url.audio apunta a /recordings/{rec_id}/media/{file_id}/download
          (sin /api/vexa prefix — el router no tiene ese prefix)
    """
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

    mock_meeting = MagicMock(spec=Meeting)
    for k, v in meeting_dict.items():
        setattr(mock_meeting, k, v)

    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = mock_meeting
    mock_result.scalars.return_value.all.return_value = []

    async def fake_execute(stmt):
        return mock_result

    mock_db.execute = fake_execute
    mock_db.get = AsyncMock(return_value=mock_meeting)

    from meeting_api.main import app
    from meeting_api.database import get_db

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db

    response = await client.get("/transcripts/google_meet/aaa-bbbb-ccc")

    assert response.status_code == 200
    data = response.json()

    assert "recordings" in data
    assert len(data["recordings"]) == 1

    rec = data["recordings"][0]
    assert "playback_url" in rec
    assert rec["playback_url"] is not None

    # SPEC: URL correcta SIN /api/vexa prefix
    audio_url = rec["playback_url"]["audio"]
    video_url = rec["playback_url"]["video"]

    # El endpoint de download real es /recordings/{id}/media/{file_id}/download
    assert audio_url == "/recordings/100/media/201/download", \
        f"URL audio incorrecta: expected /recordings/100/media/201/download, got {audio_url}"

    assert video_url == "/recordings/100/media/202/download", \
        f"URL video incorrecta: expected /recordings/100/media/202/download, got {video_url}"

    app.dependency_overrides.clear()
