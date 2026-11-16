"""#1348: `transcriptions.session_uid` is gone from the model; `meeting_sessions.session_uid` is not.

Convergence never drops, so a database that already holds the column keeps it until an operator
runs MIGRATION-0017. The note has to carry the statements and say why the order matters."""
import pathlib

from admin_api.schema.models import MeetingSession, Transcription

_NOTE = (pathlib.Path(__file__).resolve().parents[1]
         / "src/admin_api/schema/MIGRATION-0017-drop-transcription-session-uid.md")


def test_the_transcript_row_has_no_session_uid_and_the_bot_session_keeps_its_own():
    assert "session_uid" not in Transcription.__table__.columns
    assert not any("session_uid" in {c.name for c in ix.columns} for ix in Transcription.__table__.indexes)
    assert "session_uid" in MeetingSession.__table__.columns


def test_the_migration_note_drops_the_index_before_the_column_and_says_when():
    body = _NOTE.read_text()
    index = body.index("DROP INDEX CONCURRENTLY IF EXISTS ix_transcriptions_session_uid;")
    column = body.index("ALTER TABLE transcriptions DROP COLUMN IF EXISTS session_uid;")
    assert index < column
    assert "ADD COLUMN IF NOT EXISTS session_uid" in body       # the rollback
    assert "## Order" in body
