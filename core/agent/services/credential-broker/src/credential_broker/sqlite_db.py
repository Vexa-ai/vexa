"""How the broker opens its two SQLite files (metadata.sqlite and secrets.sqlite).

Every connection waits up to `BUSY_TIMEOUT_MS` for a competing writer instead of failing with
"database is locked". `open_wal` puts a file in write-ahead-log mode once, when its schema is
opened: the mode is stored in the file, so every later connection uses it, and a file created in
rollback-journal mode converts in place on the first open. In WAL mode readers never block the
writer, and the backup job (`credential_broker.backup`) reads a consistent snapshot while the
broker keeps writing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

#: 30 s: a busy writer on a loaded host commits in ~200 ms (fsync), and SQLite's busy handler is not
#: fair, so two writers can starve each other past 10 s (seen on a host at load 16, 2026-10-24). The
#: store is written rarely; waiting is better than refusing a person's connection.
BUSY_TIMEOUT_MS = 30_000


def connect(path: "str | Path", **kwargs) -> sqlite3.Connection:
    c = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, **kwargs)
    c.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return c


def open_wal(c: sqlite3.Connection) -> None:
    """Switch the file to WAL (outside any transaction). Raises if SQLite keeps another mode, so a
    filesystem that cannot hold the WAL's shared-memory index stops the broker at boot."""
    mode = c.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        raise sqlite3.OperationalError(f"journal_mode is {mode}, not wal")
