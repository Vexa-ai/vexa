"""O-STACK-1 — Postgres backing-stack eval (testcontainers-postgres).

Asserts the REAL parent schema behavior on an ephemeral Postgres:
  1. ensure_schema() converges an empty DB → the expected table set, idempotently.
  2. The `recordings` + `media_files` tables are DEAD (NOT in the v0.12 schema).
  3. FK integrity: api_tokens.user_id → users.id, transcriptions/meeting_sessions → meetings.id
     (orphan inserts are rejected by the DB).
  4. CRUD golden round-trips: user → token; meeting → transcription → session.
  5. The JSONB recording path: recordings live in meetings.data['recordings'][] (the real writer
     target), not a table.
"""
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from admin_api.schema.models import (
    APIToken, Base, Meeting, MeetingSession, Transcription, User,
)
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker

pytestmark = requires_docker

EXPECTED_TABLES = {"users", "api_tokens", "meetings", "transcriptions", "meeting_sessions"}
DEAD_TABLES = {"recordings", "media_files"}


@pytest.fixture()
def engine(pg_url):
    eng = create_engine(pg_url)
    # Clean slate per test — drop everything, re-converge.
    Base.metadata.drop_all(eng)
    ensure_schema_sync(eng, Base)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


def test_ensure_schema_creates_expected_tables(engine):
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES <= tables, f"missing tables: {EXPECTED_TABLES - tables}"


def test_recordings_table_is_dead(engine):
    """The `recordings`/`media_files` tables are write-never dead columns — the v0.12 schema
    must NOT create them. Recordings live in meetings.data['recordings'][] JSONB."""
    tables = set(inspect(engine).get_table_names())
    leaked = DEAD_TABLES & tables
    assert not leaked, f"dead recordings table(s) present in v0.12 schema: {leaked}"
    # And to_regclass agrees they don't exist (the parent's own existence guard).
    with engine.connect() as conn:
        for t in DEAD_TABLES:
            exists = conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"),
                                  {"t": f"public.{t}"}).scalar()
            assert exists is False, f"to_regclass found dead table public.{t}"


def test_ensure_schema_is_idempotent(engine):
    before = set(inspect(engine).get_table_names())
    ensure_schema_sync(engine, Base)   # second run = no-op
    ensure_schema_sync(engine, Base)   # third run = no-op
    after = set(inspect(engine).get_table_names())
    assert before == after


def test_fk_orphan_token_rejected(engine):
    """api_tokens.user_id → users.id: an orphan token must be rejected by the FK."""
    from sqlalchemy.exc import IntegrityError
    with Session(engine) as s:
        s.add(APIToken(token="vxa_bot_orphan", user_id=999999, scopes=["bot"]))
        with pytest.raises(IntegrityError):
            s.commit()


def test_fk_orphan_transcription_rejected(engine):
    from sqlalchemy.exc import IntegrityError
    with Session(engine) as s:
        s.add(Transcription(meeting_id=999999, start_time=0.0, end_time=1.0, text="x"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_crud_user_token_roundtrip(engine):
    """Golden round-trip: create a user, mint a scoped token, read it back via the FK relation."""
    with Session(engine) as s:
        u = User(email="alice@vexa.ai", name="Alice", max_concurrent_bots=3)
        s.add(u)
        s.flush()
        s.add(APIToken(token="vxa_bot_alice", user_id=u.id, scopes=["bot", "tx"]))
        s.commit()
        uid = u.id

    with Session(engine) as s:
        u = s.get(User, uid)
        assert u.email == "alice@vexa.ai"
        assert u.max_concurrent_bots == 3
        assert u.data == {}                       # server_default '{}'::jsonb
        assert len(u.api_tokens) == 1
        tok = u.api_tokens[0]
        assert tok.token == "vxa_bot_alice"
        assert set(tok.scopes) == {"bot", "tx"}   # text[] round-trips


def test_crud_meeting_transcription_session_roundtrip(engine):
    """Golden round-trip: meeting → transcription → session, FK-linked + readable back."""
    with Session(engine) as s:
        m = Meeting(user_id=1, platform="google_meet", platform_specific_id="abc-defg-hij",
                    status="active")
        s.add(m)
        s.flush()
        s.add(Transcription(meeting_id=m.id, start_time=19.0, end_time=34.0,
                            text="Hello, this is the transcript", speaker="Alice",
                            language="en", session_uid="sess-1", segment_id="seg-1"))
        s.add(MeetingSession(meeting_id=m.id, session_uid="sess-1"))
        s.commit()
        mid = m.id

    with Session(engine) as s:
        m = s.get(Meeting, mid)
        assert m.platform == "google_meet"
        assert len(m.transcriptions) == 1
        assert m.transcriptions[0].text == "Hello, this is the transcript"
        assert len(m.sessions) == 1
        assert m.sessions[0].session_uid == "sess-1"


def test_meeting_session_unique_constraint(engine):
    """_meeting_session_uc — (meeting_id, session_uid) is unique."""
    from sqlalchemy.exc import IntegrityError
    with Session(engine) as s:
        m = Meeting(user_id=1, platform="zoom", status="active")
        s.add(m)
        s.flush()
        s.add(MeetingSession(meeting_id=m.id, session_uid="dup"))
        s.add(MeetingSession(meeting_id=m.id, session_uid="dup"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_recordings_live_in_meeting_data_jsonb(engine):
    """The REAL recording target: meetings.data['recordings'][] (mirrors
    `recordings.internal_upload_recording`). Assert a recording payload round-trips through
    JSONB — and is queryable via the `@>` containment the parent uses."""
    rec = {
        "id": 100000000001, "meeting_id": None, "user_id": 1,
        "session_uid": "sess-1", "source": "bot", "status": "completed",
        "media_files": [{"id": 1, "type": "audio", "format": "webm",
                         "storage_path": "recordings/1/100000000001/sess-1/audio/000000.webm"}],
    }
    with Session(engine) as s:
        m = Meeting(user_id=1, platform="google_meet", status="completed",
                    data={"recordings": [rec]})
        s.add(m)
        s.commit()
        mid = m.id

    with Session(engine) as s:
        # JSONB containment query — the parent's _find_meeting_data_recording pattern.
        found = s.execute(text(
            "SELECT id FROM meetings WHERE data->'recordings' @> "
            "cast(:pat as jsonb)"
        ), {"pat": '[{"id": 100000000001}]'}).scalar()
        assert found == mid
        m = s.get(Meeting, mid)
        assert m.data["recordings"][0]["status"] == "completed"
        assert m.data["recordings"][0]["media_files"][0]["type"] == "audio"
