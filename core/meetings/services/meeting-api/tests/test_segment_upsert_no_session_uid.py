"""#1348: the durable transcript write names no `session_uid` column, and no segment carries one."""
from meeting_api.collector.adapters import _SEGMENT_UPSERT_SQL, _segment_upsert_rows
from meeting_api.collector.transcript_import import normalize_segments


def test_the_upsert_binds_exactly_the_columns_it_names():
    rows = _segment_upsert_rows(7, [{"segment_id": "s1", "start": 0.0, "end": 1.0, "text": "hi",
                                     "session_uid": "stray", "source": "bot"}])
    assert len(rows) == 1
    assert set(rows[0]) == {"mid", "start", "end", "text", "speaker", "lang", "segid", "source", "created"}
    assert "session_uid" not in _SEGMENT_UPSERT_SQL and ":uid" not in _SEGMENT_UPSERT_SQL


def test_an_imported_segment_carries_no_session_uid():
    segments, reason = normalize_segments([{"start": 0, "end": 1, "text": "hi"}], "import-import-7")
    assert reason is None
    assert segments[0]["segment_id"] == "import-import-7-000000"
    assert "session_uid" not in segments[0]

