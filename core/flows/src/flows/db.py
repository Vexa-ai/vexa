"""The DB seam. Engine speaks textual SQL through this thin protocol (the house pattern:
SQLAlchemy is the CONNECTION layer in production, imported lazily so the offline gates and the
stdlib test rig never need it; statements stay explicit SQL).

Two adapters:
  * SqliteDB  — stdlib sqlite3, serialized writes: the offline fixture/storm dialect.
  * postgres_db() — lazy factory returning a SQLAlchemy-engine-backed adapter (production).

Dialect note: claiming uses `FOR UPDATE SKIP LOCKED` on Postgres; sqlite serializes on the
connection lock instead (BEGIN IMMEDIATE), which preserves the same one-claimer invariant the
storm asserts."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol

SCHEMA = (Path(__file__).resolve().parents[2] / "schema.sql").read_text()


class DB(Protocol):
    dialect: str
    def execute(self, sql: str, params: dict | None = None) -> list[tuple]: ...
    def executescript(self, sql: str) -> None: ...


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


def postgres_db(url: str):  # pragma: no cover — production composition; lazy import by design
    from sqlalchemy import create_engine, text

    engine = create_engine(url, pool_pre_ping=True)

    class _Pg:
        dialect = "postgres"

        def execute(self, sql: str, params: dict | None = None) -> list[tuple]:
            with engine.begin() as c:
                res = c.execute(text(sql), params or {})
                return [tuple(r) for r in res] if res.returns_rows else []

        def executescript(self, sql: str) -> None:
            # strip comment lines BEFORE splitting on ';' — comments legitimately contain
            # semicolons and a naive split feeds Postgres mid-sentence fragments
            clean = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
            with engine.begin() as c:
                for stmt in clean.split(";"):
                    if stmt.strip():
                        c.execute(text(stmt))

    db = _Pg()
    db.executescript(SCHEMA)
    return db


def dumps(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), sort_keys=True)


def loads(v: Optional[str]) -> dict:
    return json.loads(v) if v else {}
