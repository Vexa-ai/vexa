"""ADR-0048 step A — the session store: metadata in agent-api's database, transcripts in object
storage, one immutable object per turn version, a refused conflict instead of a silent merge."""
from __future__ import annotations

import pytest

from tests._session_fakes import MemBlobs
from control_plane import session_blobs, session_store
from control_plane.session_store import SessionConflict, SessionStore


@pytest.fixture()
def store(tmp_path):
    s = SessionStore.open(f"sqlite:///{tmp_path / 'sessions.db'}",
                          MemBlobs())
    yield s
    s.close()


def test_schema_migrates_idempotently(tmp_path):
    url = f"sqlite:///{tmp_path / 's.db'}"
    blobs = MemBlobs()
    a = SessionStore.open(url, blobs)
    sid = a.create("alice", name="main")["id"]
    a.close()
    b = SessionStore.open(url, blobs)          # second open re-runs the migration: a no-op
    assert b.get(sid)["owner"] == "alice"
    assert b.schema_version() == session_store.SCHEMA_VERSION
    b.close()


def test_create_makes_the_owner_a_participant(store):
    row = store.create("alice", name="pchat-1", title="Pricing", workspaces=["acme"])
    assert row["owner"] == "alice" and row["title"] == "Pricing" and row["workspaces"] == ["acme"]
    assert store.role_of(row["id"], "alice") == "owner"
    assert store.by_name("alice", "pchat-1")["id"] == row["id"]
    assert store.by_name("bob", "pchat-1") is None   # a name is per owner, never global


def test_create_is_idempotent_per_owner_and_name(store):
    a = store.create("alice", name="main")
    b = store.create("alice", name="main")
    assert a["id"] == b["id"]


def test_bundle_round_trip_and_versions(store):
    sid = store.create("alice", name="main")["id"]
    assert store.load_bundle(sid) == {"version": 0, "files": {}}
    v1 = store.save_bundle(sid, 0, {".claude/sessions/main.session": b"abc\n"})
    assert v1 == 1
    got = store.load_bundle(sid)
    assert got["version"] == 1 and got["files"] == {".claude/sessions/main.session": b"abc\n"}
    v2 = store.save_bundle(sid, 1, {".claude/sessions/main.session": b"def\n"})
    assert v2 == 2
    assert store.load_bundle(sid)["files"][".claude/sessions/main.session"] == b"def\n"
    # every version stays readable — versioned per turn, never overwritten
    assert store.load_bundle(sid, version=1)["files"][".claude/sessions/main.session"] == b"abc\n"


def test_a_stale_base_is_refused_never_merged(store):
    sid = store.create("alice", name="main")["id"]
    store.save_bundle(sid, 0, {"a": b"1"})
    with pytest.raises(SessionConflict) as exc:
        store.save_bundle(sid, 0, {"a": b"2"})
    assert exc.value.head == 1 and exc.value.base == 0
    assert store.load_bundle(sid)["files"] == {"a": b"1"}


def test_bundle_paths_are_plain_relative_paths(store):
    sid = store.create("alice", name="main")["id"]
    for bad in ("../x", "/etc/passwd", "a/../../b", "", "a\\b"):
        with pytest.raises(ValueError):
            store.save_bundle(sid, 0, {bad: b"x"})


def test_bundle_size_is_bounded(store, monkeypatch):
    monkeypatch.setattr(session_store, "MAX_BUNDLE_BYTES", 10)
    sid = store.create("alice", name="main")["id"]
    with pytest.raises(ValueError):
        store.save_bundle(sid, 0, {"a": b"x" * 11})


def test_list_for_is_participant_scoped(store):
    a = store.create("alice", name="main")["id"]
    store.create("bob", name="main")
    assert [r["id"] for r in store.list_for("alice")] == [a]


def test_delete_removes_metadata_and_blobs(store, tmp_path):
    sid = store.create("alice", name="main")["id"]
    store.save_bundle(sid, 0, {"a": b"1"})
    store.delete(sid)
    assert store.get(sid) is None
    assert not store._blobs.objects


def test_a_database_without_an_s3_endpoint_is_off():
    """ADR-0038: transcripts go to S3 only; a database alone does not turn the store on."""
    class _S:
        sessions_db_url = ""
        db_host = "postgres"
        sessions_s3_endpoint = ""
    assert session_store.from_settings(_S()) is None


def test_open_from_settings_is_off_without_configuration():
    class _S:
        sessions_db_url = ""
        sessions_s3_endpoint = ""
    assert session_store.from_settings(_S()) is None


def test_unaddressed_messages_are_kept_and_heard_once(store):
    sid = store.create("alice", name="main")["id"]
    store.add_note(sid, "bob", "I'll take the migration")
    store.add_note(sid, "alice", "thanks")
    assert [n["text"] for n in store.notes(sid)] == ["I'll take the migration", "thanks"]
    heard = store.take_unheard(sid)
    assert [n["author"] for n in heard] == ["bob", "alice"]
    assert store.take_unheard(sid) == []
    with pytest.raises(ValueError):
        store.add_note(sid, "bob", "   ")


def test_participants_owner_adds_and_anyone_leaves(store):
    sid = store.create("alice", name="main")["id"]
    with pytest.raises(session_store.SessionDenied):
        store.add_participant(sid, "bob", "carol")              # only the owner adds
    store.add_participant(sid, "alice", "bob", role="member")
    store.add_participant(sid, "alice", "scout", kind="agent", role="member")
    assert store.humans(sid) == ["alice", "bob"]
    assert store.can_write(sid, "bob") and not store.can_read(sid, "carol")
    with pytest.raises(session_store.SessionDenied):
        store.remove_participant(sid, "bob", "alice")            # the owner is never removed
    assert store.remove_participant(sid, "bob", "bob")           # anyone may leave
    assert store.role_of(sid, "bob") is None
    assert [r["id"] for r in store.list_for("scout")] == [sid]


def test_store_is_on_with_the_instance_database_parts(monkeypatch):
    seen = {}

    class _S:
        sessions_db_url = ""
        db_host, db_port, db_name = "postgres", 5432, "vexa"
        sessions_db_user, sessions_db_password = "vexa_agent_sessions", "pw"
        sessions_s3_endpoint, sessions_s3_secure, sessions_s3_bucket = "storage:9000", False, "vexa"
        sessions_s3_region, sessions_s3_access_key, sessions_s3_secret_key = "us-east-1", "a", "b"

    def _open(blobs, **params):
        seen.update(params, endpoint=blobs._endpoint)
        return "store"
    monkeypatch.setattr(SessionStore, "open_postgres", classmethod(lambda cls, blobs, **p: _open(blobs, **p)))
    assert session_store.from_settings(_S()) == "store"
    assert seen["host"] == "postgres" and seen["password"] == "pw" and seen["database"] == "vexa"
    assert seen["user"] == "vexa_agent_sessions"
    assert seen["endpoint"] == "http://storage:9000"


def test_a_dropped_connection_is_opened_again_once(tmp_path):
    import sqlite3
    url = f"sqlite:///{tmp_path / 's.db'}"
    s = SessionStore.open(url, MemBlobs())
    sid = s.create("alice", name="main")["id"]

    class _Dead:
        def cursor(self):
            raise ConnectionResetError("gone")
    s._conn = _Dead()
    assert s.get(sid)["owner"] == "alice"           # reconnected and answered
    s.close()
