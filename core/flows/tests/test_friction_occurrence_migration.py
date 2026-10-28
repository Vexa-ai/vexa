"""`friction_occurrence.admitted` reaches a table created before the column existed.

`schema.sql` is `CREATE TABLE IF NOT EXISTS`, so a deployment whose `friction_occurrence` was
created by an earlier build keeps the old column set, and the dedup upsert (which writes
`admitted`) then fails on every report. `flows.migrations` adds the column in place, idempotently.

Rows already in the table were written before the in-flight notion existed: their first report
was admitted (or forgotten) synchronously, so they are migrated as admitted (`1`). The column then
carries no database default, exactly as a fresh install creates it — the code always writes it.

The offline half runs everywhere. The Postgres half runs only when `FLOWS_TEST_PG_URL` names a
throwaway server it may create databases on: it builds the OLD table in a fresh database, keeps a
row in it, boots the production adapter there, and proves the insert succeeds and the old row is
intact."""
from __future__ import annotations

import os
import uuid

import pytest

from flows import migrations


class _Recorder:
    def __init__(self):
        self.sql: list[str] = []

    def __call__(self, sql: str, params=None):
        self.sql.append(" ".join(sql.split()))
        return [(1,)] if "information_schema" in sql else []


def test_the_migration_adds_admitted_to_an_existing_friction_table():
    rec = _Recorder()
    migrations.apply(rec)
    joined = "\n".join(rec.sql)
    assert ("ALTER TABLE friction_occurrence ADD COLUMN IF NOT EXISTS admitted INTEGER NOT NULL "
            "DEFAULT 1") in joined
    assert "ALTER TABLE friction_occurrence ALTER COLUMN admitted DROP DEFAULT" in joined
    add = next(i for i, s in enumerate(rec.sql) if "ADD COLUMN IF NOT EXISTS admitted" in s)
    drop = next(i for i, s in enumerate(rec.sql) if "admitted DROP DEFAULT" in s)
    assert add < drop


def test_every_statement_is_safe_to_run_again():
    """Run on every boot: each friction statement is a no-op on a table that already has it."""
    rec = _Recorder()
    migrations.apply(rec)
    for s in rec.sql:
        if "friction_occurrence" in s:
            assert "IF NOT EXISTS" in s or "DROP DEFAULT" in s, s


PG_URL = os.environ.get("FLOWS_TEST_PG_URL", "").strip()

OLD_TABLE = """CREATE TABLE friction_occurrence (
    dedup_key TEXT NOT NULL, friction_id TEXT NOT NULL, uid TEXT NOT NULL,
    occurrences INTEGER NOT NULL, first_seen DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION NOT NULL, PRIMARY KEY (dedup_key))"""


@pytest.fixture()
def old_schema_db():
    if not PG_URL:
        pytest.skip("FLOWS_TEST_PG_URL not set — no real Postgres")
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    name = f"flows_drift_{uuid.uuid4().hex[:10]}"
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    url = make_url(PG_URL).set(database=name)
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text(OLD_TABLE))
        c.execute(text("INSERT INTO friction_occurrence VALUES "
                       "('old-key', 'fr_old', 'u-old', 3, 10.0, 20.0)"))
    eng.dispose()
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_a_database_with_the_old_table_is_migrated_and_a_report_inserts(old_schema_db):
    from flows.db import postgres_db
    from flows_integrations import friction_dedup

    db = postgres_db(old_schema_db)
    fid = "fr_new00001"
    assert friction_dedup.record_occurrence(db, key="new-key", friction_id=fid, uid="u-new",
                                            now=1000.0) == (fid, 1)
    friction_dedup.mark_admitted(db, key="new-key", friction_id=fid)
    rows = db.execute("SELECT dedup_key, friction_id, uid, occurrences, first_seen, last_seen, "
                      "admitted FROM friction_occurrence ORDER BY dedup_key")
    assert rows == [("new-key", fid, "u-new", 1, 1000.0, 1000.0, 1),
                    ("old-key", "fr_old", "u-old", 3, 10.0, 20.0, 1)]
    default = db.execute("SELECT column_default FROM information_schema.columns "
                         "WHERE table_name = 'friction_occurrence' AND column_name = 'admitted'")
    assert default == [(None,)]


def test_booting_twice_on_the_migrated_database_changes_nothing(old_schema_db):
    from flows.db import postgres_db

    postgres_db(old_schema_db).execute("SELECT 1")
    db = postgres_db(old_schema_db)
    assert db.execute("SELECT friction_id, occurrences, admitted FROM friction_occurrence") == [
        ("fr_old", 3, 1)]
