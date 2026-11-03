from meeting_api.recordings.jsonb import apply_chunk_to_recording
from meeting_api.recordings.router import _project_list_recording


def test_capture_origin_survives_later_chunks_and_list_projection():
    common = dict(recording_id=1, meeting_id=2, user_id=3, session_uid="test", media_type="audio", media_format="webm", storage_path="part", file_size=4, duration_seconds=None, sample_rate=None)
    rec, _ = apply_chunk_to_recording(None, **common, chunk_seq=0, is_final=False, capture_started_at_ms=1791460000123)
    rec, _ = apply_chunk_to_recording(rec, **common, chunk_seq=1, is_final=False, capture_started_at_ms=1791460009999)
    rec, _ = apply_chunk_to_recording(rec, **common, chunk_seq=2, is_final=True)
    assert _project_list_recording(rec)["media_files"][0]["capture_started_at_ms"] == 1791460000123


def test_invalid_or_missing_origin_is_not_invented():
    common = dict(recording_id=1, meeting_id=2, user_id=3, session_uid="test", media_type="audio", media_format="webm", storage_path="part", file_size=4, duration_seconds=None, sample_rate=None, chunk_seq=0, is_final=False)
    for value in (None, float("nan"), float("inf"), -1, True, "bad"):
        rec, _ = apply_chunk_to_recording(None, **common, capture_started_at_ms=value)
        assert "capture_started_at_ms" not in _project_list_recording(rec)["media_files"][0]
