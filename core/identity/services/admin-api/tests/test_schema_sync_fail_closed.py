"""#1186 · a UNIQUE index that cannot be created must FAIL CLOSED.

Prod evidence (2026-08-17): `uq_meeting_active_user_platform_native` — the DB backstop the spawn
path documents relying on (meeting-api `bot_spawn/adapters.py`) — had NEVER existed. Four stale
duplicate active rows blocked it, `_sync_indexes` caught the failure, logged one WARNING and
continued, every restart repeated the cycle, and the WARNING log-rotated away inside a day. The
service reported itself healthy the entire time.

Discriminating red→green (the old code passes NONE of the unique cases):
  * a UNIQUE index failing to create   → raises, message names the index, the table, the
    underlying error, and the remediation (before: WARNING + continue)
  * several unique failures            → ONE raise listing all of them (one restart, one fix pass)
  * a NON-unique index failing         → still tolerated, convergence continues (performance
    hint, not an invariant)
  * nothing failing                    → no raise

No DB and no docker: these exercise `_sync_indexes`' branch logic directly with fakes. The real
Postgres round-trip (drop the index, plant duplicates, re-converge) is the docker-gated
`test_meeting_active_unique_index_blocked_fails_closed` in `test_stack_postgres.py`.
"""
from __future__ import annotations

import logging
from contextlib import nullcontext

import pytest

from admin_api.schema import sync as sync_mod
from admin_api.schema.errors import SchemaInvariantError

BLOCKED = "could not create unique index \"uq_x\"\nDETAIL:  Key (a)=(1) is duplicated."


class _FakeIndex:
    def __init__(self, name, *, unique, error=None):
        self.name = name
        self.unique = unique
        self._error = error
        self.created = False

    def create(self, conn):
        if self._error is not None:
            raise self._error
        self.created = True


class _FakeTable:
    def __init__(self, name, indexes):
        self.name = name
        self.indexes = indexes


class _FakeBase:
    def __init__(self, tables):
        self.metadata = type("_M", (), {"sorted_tables": tables})()


class _FakeConn:
    """Only what `_sync_indexes` touches: a savepoint context manager."""

    def __init__(self):
        self.savepoints = 0

    def begin_nested(self):
        self.savepoints += 1
        return nullcontext()


class _FakeInspector:
    def __init__(self, tables):
        self._tables = [t.name for t in tables]

    def get_table_names(self):
        return list(self._tables)

    def get_indexes(self, table_name):
        return []          # nothing pre-exists → every index is attempted


@pytest.fixture()
def patched_inspect(monkeypatch):
    def _apply(tables):
        monkeypatch.setattr(sync_mod, "inspect", lambda conn: _FakeInspector(tables))
    return _apply


def _run(tables, patched_inspect):
    patched_inspect(tables)
    conn = _FakeConn()
    sync_mod._sync_indexes(conn, _FakeBase(tables))
    return conn


def test_unique_index_failure_fails_closed(patched_inspect):
    """The #1186 case: the unique index cannot be built → raise, don't log-and-continue."""
    idx = _FakeIndex("uq_meeting_active_user_platform_native", unique=True,
                     error=Exception(BLOCKED))
    tables = [_FakeTable("meetings", [idx])]

    with pytest.raises(SchemaInvariantError) as ei:
        _run(tables, patched_inspect)

    msg = str(ei.value)
    assert "uq_meeting_active_user_platform_native" in msg, "message must NAME the index"
    assert "meetings" in msg, "message must name the table"
    assert "is duplicated" in msg, "message must carry the underlying DB error"
    assert "duplicate rows block this unique index" in msg, "message must state the remediation"
    assert "restart" in msg
    assert "github.com/Vexa-ai/vexa/issues/1186" in msg


def test_non_unique_index_failure_is_still_tolerated(patched_inspect, caplog):
    """A plain index is a performance hint — losing it must NOT stop the service booting."""
    failing = _FakeIndex("ix_meetings_created_at", unique=False, error=Exception("boom"))
    following = _FakeIndex("ix_meetings_user_id", unique=False)
    tables = [_FakeTable("meetings", [failing, following])]

    with caplog.at_level(logging.DEBUG, logger="admin_api.schema.sync"):
        _run(tables, patched_inspect)          # no raise

    assert following.created, "convergence must continue past a tolerated failure"
    assert any("ix_meetings_created_at" in r.getMessage() for r in caplog.records)


def test_all_unique_failures_reported_in_one_raise(patched_inspect):
    """One restart surfaces every blocking index — not one per fix-and-retry cycle."""
    tables = [
        _FakeTable("meetings", [_FakeIndex("uq_a", unique=True, error=Exception("dup a"))]),
        _FakeTable("users", [_FakeIndex("uq_b", unique=True, error=Exception("dup b"))]),
    ]
    with pytest.raises(SchemaInvariantError) as ei:
        _run(tables, patched_inspect)
    msg = str(ei.value)
    assert "uq_a" in msg and "uq_b" in msg
    assert "2 UNIQUE index(es)" in msg


def test_unique_failure_does_not_abort_the_rest_of_the_index_pass(patched_inspect):
    """The raise happens AFTER the loop, so non-unique convergence still gets attempted —
    the operator sees the complete picture in one pass."""
    later = _FakeIndex("ix_after", unique=False)
    tables = [_FakeTable("meetings", [
        _FakeIndex("uq_blocked", unique=True, error=Exception("dup")),
        later,
    ])]
    with pytest.raises(SchemaInvariantError):
        _run(tables, patched_inspect)
    assert later.created


def test_clean_convergence_does_not_raise(patched_inspect):
    tables = [_FakeTable("meetings", [
        _FakeIndex("uq_ok", unique=True),
        _FakeIndex("ix_ok", unique=False),
    ])]
    _run(tables, patched_inspect)      # no raise


def test_schema_invariant_error_is_not_treated_as_a_transient_connect_error():
    """#901's boot retry must NOT burn its budget on a data problem (and must not swallow it)."""
    from admin_api.__main__ import _is_transient_connect_error

    err = SchemaInvariantError("blocked")
    err.__cause__ = ConnectionResetError("driver socket noise")   # chain that would match #901
    assert _is_transient_connect_error(err) is False
