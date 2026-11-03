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

ONE PROCESS AT A TIME (`migrate`): a session advisory lock is held from before `schema.sql` to the
end of `after`, so processes that boot together migrate one after the other. It is TRIED, never
waited for, and the waiting process holds nothing between tries (see `migrate`).

`execute` is any `(sql, params) -> rows` callable: the Postgres adapter's in production, a
recorder in the offline suite."""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

_log = logging.getLogger(__name__)

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

#: The session advisory lock the whole migration runs under (`migrate`), so two processes booting
#: together migrate one at a time (any fixed number; this one spells "flow").
MIGRATION_LOCK = 0x666C6F77
#: How long `migrate` waits between two tries for that lock, and how often a waiting process says
#: who holds it.
LOCK_POLL_S = 0.25
LOCK_SAY_EVERY_S = 30.0
#: How long a booting process waits for another one's migration before it fails, named
#: (`MigrationLockTimeout`). The composition passes `VEXA_FLOWS_MIGRATION_WAIT_S`; this is its
#: default: an hour, many times a CONCURRENTLY build on a large table.
MIGRATION_WAIT_S = 3600.0

#: Who holds `MIGRATION_LOCK`, and what the oldest open snapshot is: the one a CONCURRENTLY build
#: under that lock waits for (an idle transaction, a backup). Read only while waiting.
LOCK_HOLDER = f"""SELECT a.pid, a.application_name, a.state,
                         COALESCE(EXTRACT(EPOCH FROM now() - a.xact_start), 0),
                         COALESCE(a.wait_event_type || '/' || a.wait_event, '')
                  FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
                  WHERE l.locktype = 'advisory' AND l.granted AND l.classid = 0
                    AND l.objid = {MIGRATION_LOCK} AND l.objsubid = 1"""
OLDEST_SNAPSHOT = """SELECT pid, application_name, state,
                            COALESCE(EXTRACT(EPOCH FROM now() - xact_start), 0)
                     FROM pg_stat_activity
                     WHERE backend_xmin IS NOT NULL AND pid <> pg_backend_pid()
                     ORDER BY xact_start NULLS LAST LIMIT 1"""


class MigrationLockTimeout(RuntimeError):
    """`migration_lock_timeout`: another process held the migration lock longer than the wait this
    one was given. The message names the holder and the oldest open snapshot, and what to do."""
    code = "migration_lock_timeout"

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
    that commits each statement on its own (CONCURRENTLY refuses a transaction block), by a caller
    holding `MIGRATION_LOCK` (`migrate`). Idempotent: each one runs only while the catalogue says it
    is still needed. Returns what it changed."""
    did = {"keyed_key_swapped": False, "keyed_status_validated": False}
    rows = execute(KEYED_KEY_HAS_PERSON)
    if not (rows and int(rows[0][0] or 0) > 0):
        for stmt in SWAP_KEYED_KEY:
            execute(stmt)
        did["keyed_key_swapped"] = True
    rows = execute(KEYED_STATUS_UNVALIDATED)
    if rows and int(rows[0][0] or 0) > 0:
        execute(VALIDATE_KEYED_STATUS)
        did["keyed_status_validated"] = True
    return did


def migrate(held: Callable, script: Callable, transaction: Callable, wait: Callable, *,
            now: Optional[Callable] = None, limit_s: float = MIGRATION_WAIT_S,
            state: Optional[Callable] = None) -> dict:
    """THE WHOLE MIGRATION, ONE PROCESS AT A TIME. `held` executes on ONE autocommit session, which
    holds `MIGRATION_LOCK` from before the schema to the end; `script()` applies `schema.sql`;
    `transaction(fn)` runs `fn(execute)` in one transaction and returns its answer (`apply`); `after`
    runs on `held`. The lock is released whatever happens. Returns what `apply` and `after` changed.
    `state(word)` hears `waiting`, then `migrating` (the readiness a process reports meanwhile).

    THE LOCK IS TRIED, NEVER WAITED FOR (Vexa-ai/vexa#1996 review). A session blocked in
    `pg_advisory_lock` is a statement holding a snapshot, and `CREATE INDEX CONCURRENTLY` waits for
    every older snapshot, so a process queued on the lock and the one building the index under it
    waited on each other: "deadlock detected" (PG 16: 2 processes booting together in 5 boots of 6,
    3 in 3 of 4). A process that does not get the lock waits `wait(LOCK_POLL_S)` with no statement
    open and tries again, so it holds nothing the index build waits on; and it touches the schema
    only once it has the lock, so its own `apply` never holds the table under a build either.

    THE WAIT IS SAID AND BOUNDED (second re-review, CEO 2026-11-03). An old snapshot open elsewhere
    (an idle transaction, a backup) stalls the build under the lock, and every other process would
    wait for it in silence. Every `LOCK_SAY_EVERY_S` a waiting process logs who holds the lock and
    the oldest open snapshot; past `limit_s` it fails with `MigrationLockTimeout`, naming both and
    what to do, instead of waiting forever."""
    clock = now or time.monotonic
    started = clock()
    said = started
    tries = 0
    while not _granted(held(f"SELECT pg_try_advisory_lock({MIGRATION_LOCK})")):
        if tries == 0:
            _log.info("schema migration: another process holds it; waiting for it to finish")
            if state:
                state("waiting")
        tries += 1
        waited = clock() - started
        if waited > limit_s:
            who = _holder(held)
            raise MigrationLockTimeout(
                f"{MigrationLockTimeout.code}: waited {waited:.0f}s (VEXA_FLOWS_MIGRATION_WAIT_S="
                f"{limit_s:.0f}) for the schema migration another process is running. {who} It is "
                "most often a CONCURRENTLY index build waiting for that old snapshot: end it (or "
                "let it finish), or raise VEXA_FLOWS_MIGRATION_WAIT_S, then start this process "
                "again.")
        if clock() - said >= LOCK_SAY_EVERY_S:
            said = clock()
            _log.warning("schema migration: waited %.0fs so far. %s", waited, _holder(held))
        wait(LOCK_POLL_S)
    if state:
        state("migrating")
    try:
        script()
        did = dict(transaction(apply))
        did.update(after(held))
        return did
    finally:
        held(f"SELECT pg_advisory_unlock({MIGRATION_LOCK})")


def _holder(held: Callable) -> str:
    """One sentence: who holds the migration lock, and the oldest open snapshot."""
    def one(rows, what):
        if not rows:
            return f"{what}: none seen"
        pid, app, st, age = rows[0][:4]
        extra = f", waiting on {rows[0][4]}" if len(rows[0]) > 4 and rows[0][4] else ""
        return (f"{what}: pid {pid} ({app or 'no application_name'}, {st or 'no state'}, "
                f"transaction open {float(age or 0):.0f}s{extra})")
    try:
        return (one(held(LOCK_HOLDER), "Lock held by") + "; "
                + one(held(OLDEST_SNAPSHOT), "oldest open snapshot") + ".")
    except Exception as e:  # noqa: BLE001 — a catalogue we may not read must not hide the wait itself
        return f"Who holds it could not be read ({type(e).__name__})."


def _granted(rows) -> bool:
    return bool(rows and rows[0][0] in (True, 1, "t", "true"))
