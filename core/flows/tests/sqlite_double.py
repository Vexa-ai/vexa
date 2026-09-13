"""The offline/storm dialect — stdlib sqlite3, serialized writes on one connection lock.

Moved here from `flows/db.py` on 2026-09-03 (biz#TBD, "the flows engine ships only the database
it runs on"): production is Postgres, and only Postgres — claiming uses `FOR UPDATE SKIP LOCKED`,
a Postgres-only clause — so `SqliteDB` was never a second production dialect. It was a TEST
double living in the product module, exported from `flows.__init__` and importable by anything
that imported `flows` at all. It belongs to the test tree, next to the fixtures and the storm
that are its only real callers.

Dialect note (unchanged from the old home): `postgres_db` claims with `FOR UPDATE SKIP LOCKED`;
this adapter serializes on the connection lock instead (every call takes `self._lock` and the
connection itself runs `isolation_level=None` / autocommit-per-statement with `BEGIN IMMEDIATE`
semantics on write), which preserves the same one-claimer invariant the storm asserts, on a
dialect that has no `SKIP LOCKED` of its own.

Import from here directly (`from sqlite_double import SqliteDB`) — never through `flows`, which
no longer exports it, and never from `src/` (the engine imports stdlib only at module scope;
this file is not on that side of the line)."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

#: `core/flows/schema.sql`, the same file `flows.db.SCHEMA` reads — one schema, two dialects.
SCHEMA = (Path(__file__).resolve().parents[1] / "schema.sql").read_text()


class SqliteDB:
    dialect = "sqlite"

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self.executescript(_sqlite_schema())

    def execute(self, sql: str, params: dict | None = None) -> list[tuple]:
        with self._lock:
            cur = self._conn.execute(sql, params or {})
            rows = cur.fetchall()
            cur.close()
            return rows

    def executescript(self, sql: str) -> None:
        with self._lock:
            self._conn.executescript(sql)


def _sqlite_schema() -> str:
    # sqlite accepts the portable subset directly; strip the partial-index WHERE (supported) and
    # double precision (affinity REAL) both parse — only `double precision` needs mapping.
    return SCHEMA.replace("double precision", "REAL")
