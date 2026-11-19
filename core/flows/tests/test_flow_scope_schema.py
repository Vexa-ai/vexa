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
    assert did == {"key_swapped": True}


def test_the_migration_is_idempotent():
    rec = _Recorder(key_has_scope=True)
    did = migrations.apply(rec)
    assert not any("ADD PRIMARY KEY" in s or "DROP CONSTRAINT" in s for s in rec.sql)
    assert did == {"key_swapped": False}
