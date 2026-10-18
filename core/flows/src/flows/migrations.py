"""In-place changes to tables that already exist — the half `schema.sql` cannot do.

`schema.sql` is `CREATE TABLE IF NOT EXISTS`: right for a fresh database, a no-op for one that
already holds the table. A column added to the models therefore reaches an existing deployment
only through a statement here, run once after the schema on every boot and idempotent by
construction.

ADR-0046 decision 4: `flow_version` gains `scope` (every existing row is the instance's) and
`cron`, and its key becomes (scope, name, version). Postgres has no `ADD PRIMARY KEY IF NOT
EXISTS`, so the key is swapped only after the catalogue says the current one lacks `scope`.

`friction_occurrence` gains `admitted` (the dedup's in-flight marker). Rows already there predate
that marker — their first report was admitted, or forgotten, synchronously — so they arrive as `1`;
the default is then dropped so the column matches a fresh install, where the code always writes it.

`execute` is any `(sql, params) -> rows` callable: the Postgres adapter's in production, a
recorder in the offline suite."""
from __future__ import annotations

from typing import Callable

ADD_COLUMNS = (
    "ALTER TABLE flow_version ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'instance'",
    "ALTER TABLE flow_version ADD COLUMN IF NOT EXISTS cron TEXT",
    "ALTER TABLE friction_occurrence ADD COLUMN IF NOT EXISTS admitted INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE friction_occurrence ALTER COLUMN admitted DROP DEFAULT",
    # ADR-0050: the per-person keyed cap (a stack that ran the first cut of keyed_schedule)
    "ALTER TABLE keyed_schedule ADD COLUMN IF NOT EXISTS person TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS keyed_person ON keyed_schedule (scope, person)",
)

KEY_HAS_SCOPE = """SELECT COUNT(*) FROM information_schema.key_column_usage k
                   JOIN information_schema.table_constraints t
                     ON t.constraint_name = k.constraint_name AND t.table_name = k.table_name
                   WHERE t.table_name = 'flow_version' AND t.constraint_type = 'PRIMARY KEY'
                     AND k.column_name = 'scope'"""

SWAP_KEY = (
    "ALTER TABLE flow_version DROP CONSTRAINT IF EXISTS flow_version_pkey",
    "ALTER TABLE flow_version ADD PRIMARY KEY (scope, name, version)",
)


def apply(execute: Callable) -> dict:
    """Bring an existing database up to the models. Returns what it changed (no row data)."""
    for stmt in ADD_COLUMNS:
        execute(stmt)
    rows = execute(KEY_HAS_SCOPE)
    if rows and int(rows[0][0] or 0) > 0:
        return {"key_swapped": False}
    for stmt in SWAP_KEY:
        execute(stmt)
    return {"key_swapped": True}
