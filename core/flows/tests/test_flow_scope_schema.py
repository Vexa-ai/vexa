"""ADR-0046 decision 4 — `flow_version` is scoped, and keyed (scope, name, version).

Two people and the instance may each own a flow of the same name. Existing rows become `instance`:
a fresh table defaults the column, and an existing Postgres table is migrated in place by
`flows.migrations` (ADD COLUMN IF NOT EXISTS, then the key swapped once)."""
from __future__ import annotations

import json
import sqlite3

import pytest

from flows import migrations
from sqlite_double import SqliteDB


def _row(db, scope, name="morning-brief", version=1, cron=None):
    db.execute(
        "INSERT INTO flow_version (scope, name, version, on_event, steps, params, status, cron, "
        "created_by, created_at) VALUES (:sc,:n,:v,'schedule.due',:s,'{}','active',:c,'t',1.0)",
        {"sc": scope, "n": name, "v": version, "s": json.dumps(["agent_run"]), "c": cron})


def test_two_people_and_the_instance_each_own_a_flow_of_the_same_name():
    db = SqliteDB()
    _row(db, "instance")
    _row(db, "user:1")
    _row(db, "user:2")
    assert db.execute("SELECT COUNT(*) FROM flow_version WHERE name='morning-brief'")[0][0] == 3


def test_the_same_scope_name_and_version_is_one_row():
    db = SqliteDB()
    _row(db, "user:1")
    with pytest.raises(sqlite3.IntegrityError):
        _row(db, "user:1")


def test_a_row_written_without_a_scope_is_the_instances():
    """Every INSERT written before scope existed keeps meaning what it meant."""
    db = SqliteDB()
    db.execute("INSERT INTO flow_version (name, version, on_event, steps, status, created_at) "
               "VALUES ('x', 1, 'meeting.completed', '[]', 'active', 1.0)")
    assert db.execute("SELECT scope, cron FROM flow_version WHERE name='x'") == [("instance", None)]


class _Recorder:
    """A Postgres stand-in that answers the one catalogue question the migration asks."""

    def __init__(self, key_has_scope: bool):
        self.key_has_scope = key_has_scope
        self.sql: list[str] = []

    def __call__(self, sql: str, params=None):
        self.sql.append(" ".join(sql.split()))
        if "information_schema" in sql:
            return [(1,)] if self.key_has_scope else [(0,)]
        return []


def test_an_existing_table_gains_the_columns_and_the_new_key():
    rec = _Recorder(key_has_scope=False)
    did = migrations.apply(rec)
    joined = "\n".join(rec.sql)
    assert "ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'instance'" in joined
    assert "ADD COLUMN IF NOT EXISTS cron TEXT" in joined
    assert "DROP CONSTRAINT IF EXISTS flow_version_pkey" in joined
    assert "ADD PRIMARY KEY (scope, name, version)" in joined
    # ADR-0052 §7: the keyed statuses gain `retired`, and keyed rows a deadline
    assert "ADD COLUMN IF NOT EXISTS deadline DOUBLE PRECISION" in joined
    assert "DROP CONSTRAINT IF EXISTS keyed_status" in joined
    assert "'missed','retired'" in joined
    assert did == {"key_swapped": True, "keyed_status_swapped": True}


def test_the_migration_is_idempotent():
    rec = _Recorder(key_has_scope=True)
    did = migrations.apply(rec)
    assert not any("ADD PRIMARY KEY" in s or "DROP CONSTRAINT" in s for s in rec.sql)
    assert did == {"key_swapped": False, "keyed_status_swapped": False}


# ── what reads the table runs outside the boot transaction (Vexa-ai/vexa#1996 review) ─────────────

class _Catalogue:
    """A Postgres stand-in for `after`: answers whether keyed_schedule's key has `person` and
    whether `keyed_status` still waits for its validation."""

    def __init__(self, key_has_person: bool, unvalidated: bool):
        self.key_has_person, self.unvalidated = key_has_person, unvalidated
        self.sql: list[str] = []

    def __call__(self, sql: str, params=None):
        self.sql.append(" ".join(sql.split()))
        if "column_name = 'person'" in sql:
            return [(1,)] if self.key_has_person else [(0,)]
        if "convalidated" in sql:
            return [(1,)] if self.unvalidated else [(0,)]
        return []


def test_the_keyed_status_check_is_added_without_a_scan_and_validated_apart():
    """A CHECK added in one step holds ACCESS EXCLUSIVE on the table while it scans every row. Added
    NOT VALID it scans nothing; VALIDATE runs later, under a lock that lets reads and writes go on."""
    rec = _Recorder(key_has_scope=False)                     # the catalogue: no `retired` yet
    migrations.apply(rec)
    added = [s for s in rec.sql if "ADD CONSTRAINT keyed_status" in s]
    assert added and added[0].endswith("NOT VALID")
    assert not any("VALIDATE" in s for s in rec.sql), "nothing in the boot transaction scans"
    cat = _Catalogue(key_has_person=True, unvalidated=True)
    assert migrations.after(cat) == {"keyed_key_swapped": False, "keyed_status_validated": True}
    assert "ALTER TABLE keyed_schedule VALIDATE CONSTRAINT keyed_status" in cat.sql
    cat = _Catalogue(key_has_person=True, unvalidated=False)
    migrations.after(cat)
    assert not any("VALIDATE" in s for s in cat.sql), "a valid check is not validated again"


def test_the_keyed_key_gains_person_on_an_index_built_concurrently():
    cat = _Catalogue(key_has_person=False, unvalidated=False)
    assert migrations.after(cat)["keyed_key_swapped"] is True
    assert cat.sql[1:] == [
        "DROP INDEX CONCURRENTLY IF EXISTS keyed_identity",
        "CREATE UNIQUE INDEX CONCURRENTLY keyed_identity ON keyed_schedule (scope, flow, key, person)",
        "ALTER TABLE keyed_schedule DROP CONSTRAINT IF EXISTS keyed_schedule_pkey, ADD CONSTRAINT "
        "keyed_schedule_pkey PRIMARY KEY USING INDEX keyed_identity",
        cat.sql[-1]]                                         # then the validation question
    cat = _Catalogue(key_has_person=True, unvalidated=False)
    assert migrations.after(cat) == {"keyed_key_swapped": False, "keyed_status_validated": False}
    assert not any("INDEX" in s for s in cat.sql)


# ── one process at a time, and a waiter holds nothing (Vexa-ai/vexa#1996 re-review) ──────────────

class _Boot:
    """One booting process, offline: `held` is its autocommit session, `script` applies schema.sql,
    `transaction` runs `apply` in one transaction. Every call is written to one shared `log`, tagged
    with where it ran, so the order of the whole boot can be read back."""

    def __init__(self, grant_after: int, key_has_person=False, unvalidated=True):
        self.grant_after, self.tries, self.log = grant_after, 0, []
        self.cat = _Catalogue(key_has_person=key_has_person, unvalidated=unvalidated)

    def held(self, sql, params=None):
        self.log.append(("held", " ".join(sql.split())))
        if "pg_try_advisory_lock" in sql:
            self.tries += 1
            return [(self.tries > self.grant_after,)]
        return self.cat(sql, params)

    def script(self):
        self.log.append(("script", "schema.sql"))

    def transaction(self, fn):
        self.log.append(("transaction", "begin"))
        rec = _Recorder(key_has_scope=True)
        did = fn(rec)
        self.log.extend(("transaction", q) for q in rec.sql)
        self.log.append(("transaction", "commit"))
        return did

    def wait(self, s):
        self.log.append(("wait", s))


def test_a_booting_process_tries_the_lock_and_holds_nothing_while_it_waits():
    """A session blocked in pg_advisory_lock holds a snapshot that CREATE INDEX CONCURRENTLY waits
    for: the deadlock the re-review measured on PG 16. So the lock is only ever TRIED; between two
    tries the process sleeps with no statement open, and touches the schema only once it has it."""
    boot = _Boot(grant_after=2)
    migrations.migrate(boot.held, boot.script, boot.transaction, boot.wait)
    assert not any("pg_advisory_lock(" in str(q) for _, q in boot.log), "never a blocking lock"
    first = [where for where, _ in boot.log[:5]]
    assert first == ["held", "wait", "held", "wait", "held"], "try, sleep, try, sleep, granted"
    assert boot.log[5] == ("script", "schema.sql"), "the schema only after the lock"
    assert boot.log[-1] == ("held", f"SELECT pg_advisory_unlock({migrations.MIGRATION_LOCK})")


def test_the_index_build_and_the_validation_run_on_the_locked_session_outside_the_transaction():
    """Offline twin of what the Postgres test proves: `apply` runs inside the one transaction, and
    what reads the table (CONCURRENTLY, VALIDATE) on the autocommit session that holds the lock."""
    boot = _Boot(grant_after=0)
    did = migrations.migrate(boot.held, boot.script, boot.transaction, boot.wait)
    assert did["keyed_key_swapped"] and did["keyed_status_validated"]
    build = ("held", "CREATE UNIQUE INDEX CONCURRENTLY keyed_identity ON keyed_schedule "
                     "(scope, flow, key, person)")
    assert build in boot.log
    assert ("held", "ALTER TABLE keyed_schedule VALIDATE CONSTRAINT keyed_status") in boot.log
    inside = [q for w, q in boot.log if w == "transaction"]
    assert any("ADD COLUMN IF NOT EXISTS deadline" in q for q in inside)
    assert not any("CONCURRENTLY" in q or "VALIDATE" in q for q in inside)
    assert boot.log.index(("transaction", "commit")) < boot.log.index(build), "after the commit"


def test_a_failing_step_still_releases_the_lock():
    boot = _Boot(grant_after=0)

    def broken():
        raise RuntimeError("schema.sql failed")
    with pytest.raises(RuntimeError):
        migrations.migrate(boot.held, broken, boot.transaction, boot.wait)
    assert boot.log[-1] == ("held", f"SELECT pg_advisory_unlock({migrations.MIGRATION_LOCK})")
