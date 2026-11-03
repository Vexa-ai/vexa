"""`keyed_schedule` migrated in place on REAL Postgres — skipped unless `FLOWS_TEST_PG_URL` names one.

`test_flow_scope_schema.py` proves which statements `flows.migrations` sends, against a recorder.
This file proves what they DO on the production dialect, through the production adapter
(`flows.db.postgres_db`), starting from the table as the release before Vexa-ai/vexa#1996's review
left it: key (scope, flow, key), a status check without `retired`, no `deadline`. After one boot:

* the key is (scope, flow, key, person), so two people's rows of one key both fit;
* `keyed_status` has `retired` and is VALID (added NOT VALID, validated after the boot transaction);
* the rows already there are all still there; a second boot changes nothing.

Run it against a throwaway database, never a shared one: it DROPS and re-creates `keyed_schedule`.

    FLOWS_TEST_PG_URL=postgresql+pg8000://postgres:pw@127.0.0.1:55432/postgres \
        uv run pytest -q tests/test_keyed_migration_postgres.py
"""
from __future__ import annotations

import os

import pytest

PG_URL = os.environ.get("FLOWS_TEST_PG_URL", "").strip()
pytestmark = pytest.mark.skipif(not PG_URL, reason="FLOWS_TEST_PG_URL not set — no real Postgres")

OLD_TABLE = """CREATE TABLE keyed_schedule (
    scope TEXT NOT NULL, flow TEXT NOT NULL, key TEXT NOT NULL, fire_at DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL, version INTEGER NOT NULL, event_type TEXT NOT NULL,
    person TEXT NOT NULL DEFAULT '', payload TEXT, fired_for DOUBLE PRECISION,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (scope, flow, key),
    CONSTRAINT keyed_status CHECK (status IN ('armed','fired','cancelled','missed')))"""

ROW = ("INSERT INTO keyed_schedule (scope, flow, key, fire_at, status, version, event_type, person, "
       "updated_at) VALUES ('instance', 'invite_join', :k, 1.0, :st, 1, 'invite.received', :p, 1.0)")


@pytest.fixture()
def old_table():
    from sqlalchemy import text

    from flows.db import pg_engine
    engine = pg_engine(PG_URL)
    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS keyed_schedule"))
        c.execute(text(OLD_TABLE))
        for i in range(50):
            c.execute(text(ROW), {"k": f"uid-{i}", "st": "armed" if i % 2 else "fired",
                                  "p": "alice@vexa.test"})
    yield engine
    engine.dispose()


def _one(engine, sql, params=None):
    from sqlalchemy import text
    with engine.begin() as c:
        res = c.execute(text(sql), params or {})
        return [tuple(r) for r in res] if res.returns_rows else []


def _key_columns(engine):
    return [r[0] for r in _one(engine, """
        SELECT a.attname FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = 'keyed_schedule'::regclass AND i.indisprimary
        ORDER BY array_position(i.indkey::int2[], a.attnum)""")]


def test_one_boot_brings_the_old_table_to_the_new_key_and_a_valid_check(old_table):
    from flows.db import postgres_db
    postgres_db(PG_URL).execute("SELECT 1")                  # the first use migrates
    assert _key_columns(old_table) == ["scope", "flow", "key", "person"]
    (valid, clause), = _one(old_table, "SELECT convalidated, pg_get_constraintdef(oid) FROM "
                                       "pg_constraint WHERE conrelid = 'keyed_schedule'::regclass "
                                       "AND conname = 'keyed_status'")
    assert valid and "retired" in clause
    assert _one(old_table, "SELECT COUNT(*) FROM keyed_schedule") == [(50,)]
    assert _one(old_table, "SELECT COUNT(*) FROM pg_class WHERE relname = 'keyed_identity'") == [(0,)]
    _one(old_table, ROW, {"k": "uid-1", "st": "armed", "p": "mallory@vexa.test"})
    assert _one(old_table, "SELECT person FROM keyed_schedule WHERE key = 'uid-1' "
                           "ORDER BY person") == [("alice@vexa.test",), ("mallory@vexa.test",)]
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError, match="keyed_schedule_pkey"):
        _one(old_table, ROW, {"k": "uid-1", "st": "armed", "p": "mallory@vexa.test"})
    with pytest.raises(DBAPIError, match="keyed_status"):
        _one(old_table, ROW, {"k": "uid-x", "st": "bogus", "p": "alice@vexa.test"})


def test_two_peoples_rows_of_one_key_arm_cancel_and_fire_apart_on_postgres(old_table):
    import uuid

    from flows import Done, FakeClock, Registry, admit, keyed
    from flows.db import postgres_db
    from flows.registry import EventType
    db, clock, reg = postgres_db(PG_URL), FakeClock(1_000_000.0), Registry()

    @reg.step
    def note(ctx):  # noqa: ARG001
        return Done({})
    reg.flow(name="booked", version=1, on=EventType("planned.changed"), steps=[note],
             params={"_trigger": {"kind": "relative", "at": "payload.start - 600s",
                                  "key": "payload.meeting_id"}})
    mid = f"m-{uuid.uuid4().hex}"
    for uid in ("7", "8", "9"):
        admit(db, reg, clock, source_event_id=f"{mid}-{uid}", event_type="planned.changed",
              subject_refs={"meeting_id": mid, "start": 1_000_900.0, "uid": uid})
    admit(db, reg, clock, source_event_id=f"{mid}-9-off", event_type="planned.changed",
          subject_refs={"meeting_id": mid, "start": 1_000_900.0, "uid": "9", "status": "cancelled"})
    clock._t = 1_000_301.0
    assert keyed.fire_due(db, reg, clock, page=1) == 2, "paged one row at a time, none skipped"
    assert db.execute("SELECT person, status FROM keyed_schedule WHERE key = :k ORDER BY person",
                      {"k": mid}) == [("7", "fired"), ("8", "fired"), ("9", "cancelled")]


def test_a_second_boot_changes_nothing(old_table):
    from sqlalchemy import text
    from sqlalchemy.pool import NullPool

    from flows import migrations
    from flows.db import pg_engine, postgres_db
    postgres_db(PG_URL).execute("SELECT 1")
    once = pg_engine(PG_URL, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    with once.connect() as c:
        did = migrations.after(lambda sql, params=None: (
            lambda res: [tuple(r) for r in res] if res.returns_rows else [])(
                c.execute(text(sql), params or {})))
    once.dispose()
    assert did == {"keyed_key_swapped": False, "keyed_status_validated": False}
    assert _key_columns(old_table) == ["scope", "flow", "key", "person"]
