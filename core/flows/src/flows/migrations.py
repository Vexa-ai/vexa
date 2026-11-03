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

TWO PHASES. `apply` runs in the boot's one transaction, so a half-swapped key is never what the next
boot finds; everything in it is a catalogue change that reads no rows. What has to READ the table —
checking existing rows against a constraint, building an index — runs in `after`, outside that
transaction, statement by statement, under locks that let reads and writes go on (Vexa-ai/vexa#1996:
a CHECK added in one step holds the whole table while it scans):

* `keyed_status` is added `NOT VALID` in `apply` (new writes are checked at once) and validated in
  `after` (`VALIDATE CONSTRAINT` takes SHARE UPDATE EXCLUSIVE, not ACCESS EXCLUSIVE);
* `keyed_schedule`'s key gains `person` (a row is its person's, see `flows.keyed`): the unique index
  is built `CONCURRENTLY` in `after`, then becomes the primary key in one short statement.

`after` holds a session advisory lock while it works, so two processes booting at once do it once.

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
    # subject-ticket.v1: the routine grant a person's flow presents on every run
    "ALTER TABLE flow_version ADD COLUMN IF NOT EXISTS subject_grant TEXT",
    # ADR-0052 §7: a keyed row is missed only past its deadline, when it has one
    "ALTER TABLE keyed_schedule ADD COLUMN IF NOT EXISTS deadline DOUBLE PRECISION",
)

#: ADR-0052 §7: `retired` joins the keyed statuses. Postgres cannot widen a CHECK in place, so the
#: constraint is swapped once, after the catalogue says the current one lacks it. Added `NOT VALID`:
#: no scan here, the rows already there are checked by `after`.
KEYED_STATUS_HAS_RETIRED = """SELECT COUNT(*) FROM information_schema.check_constraints
                              WHERE constraint_name = 'keyed_status'
                                AND POSITION('retired' IN check_clause) > 0"""

SWAP_KEYED_STATUS = (
    "ALTER TABLE keyed_schedule DROP CONSTRAINT IF EXISTS keyed_status",
    "ALTER TABLE keyed_schedule ADD CONSTRAINT keyed_status CHECK (status IN "
    "('armed','fired','cancelled','missed','retired')) NOT VALID",
)

#: `after`: a `keyed_status` added NOT VALID (above) and not validated yet.
KEYED_STATUS_UNVALIDATED = """SELECT COUNT(*) FROM pg_constraint
                              WHERE conrelid = 'keyed_schedule'::regclass
                                AND conname = 'keyed_status' AND NOT convalidated"""

VALIDATE_KEYED_STATUS = "ALTER TABLE keyed_schedule VALIDATE CONSTRAINT keyed_status"

#: `after`: `keyed_schedule`'s key gains `person` (Vexa-ai/vexa#1996, see `flows.keyed`). The unique
#: index is built CONCURRENTLY, then the key is swapped onto it in one statement: a short ACCESS
#: EXCLUSIVE and no scan, every key column being NOT NULL already. A build a crashed boot left half
#: done is an INVALID index that `IF NOT EXISTS` would keep, so it is dropped first.
KEYED_KEY_HAS_PERSON = """SELECT COUNT(*) FROM information_schema.key_column_usage k
                          JOIN information_schema.table_constraints t
                            ON t.constraint_name = k.constraint_name AND t.table_name = k.table_name
                          WHERE t.table_name = 'keyed_schedule' AND t.constraint_type = 'PRIMARY KEY'
                            AND k.column_name = 'person'"""

SWAP_KEYED_KEY = (
    "DROP INDEX CONCURRENTLY IF EXISTS keyed_identity",
    "CREATE UNIQUE INDEX CONCURRENTLY keyed_identity ON keyed_schedule (scope, flow, key, person)",
    "ALTER TABLE keyed_schedule DROP CONSTRAINT IF EXISTS keyed_schedule_pkey, "
    "ADD CONSTRAINT keyed_schedule_pkey PRIMARY KEY USING INDEX keyed_identity",
)

#: The session advisory lock `after` holds, so two processes booting together do its work once
#: (any fixed number; this one spells "flow").
AFTER_LOCK = 0x666C6F77

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
    did = {"key_swapped": False, "keyed_status_swapped": False}
    rows = execute(KEYED_STATUS_HAS_RETIRED)
    if not (rows and int(rows[0][0] or 0) > 0):
        for stmt in SWAP_KEYED_STATUS:
            execute(stmt)
        did["keyed_status_swapped"] = True
    rows = execute(KEY_HAS_SCOPE)
    if rows and int(rows[0][0] or 0) > 0:
        return did
    for stmt in SWAP_KEY:
        execute(stmt)
    did["key_swapped"] = True
    return did


def after(execute: Callable) -> dict:
    """The changes that read the table: run AFTER `apply`'s transaction committed, with an `execute`
    that commits each statement on its own (CONCURRENTLY refuses a transaction block). Idempotent:
    each one runs only while the catalogue says it is still needed. Returns what it changed."""
    did = {"keyed_key_swapped": False, "keyed_status_validated": False}
    execute(f"SELECT pg_advisory_lock({AFTER_LOCK})")
    try:
        rows = execute(KEYED_KEY_HAS_PERSON)
        if not (rows and int(rows[0][0] or 0) > 0):
            for stmt in SWAP_KEYED_KEY:
                execute(stmt)
            did["keyed_key_swapped"] = True
        rows = execute(KEYED_STATUS_UNVALIDATED)
        if rows and int(rows[0][0] or 0) > 0:
            execute(VALIDATE_KEYED_STATUS)
            did["keyed_status_validated"] = True
    finally:
        execute(f"SELECT pg_advisory_unlock({AFTER_LOCK})")
    return did
