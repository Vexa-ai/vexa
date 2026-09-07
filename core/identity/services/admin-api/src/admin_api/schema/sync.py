"""Idempotent Postgres schema convergence — the parent's `ensure_schema()` discipline.

Derived from `libs/schema-sync/schema_sync/sync.py` (re-read, reimplemented clean): the
parent does NO alembic — it converges the DB to match SQLAlchemy model metadata without ever
dropping tables, columns, or data:

  empty DB        → create_all (FK order)
  partial DB      → add missing tables, then missing columns, then missing indexes
  current DB      → no-op (idempotent)

This v0.12 carve keeps `create_all(checkfirst=True)` + the additive column/index sync. We do
NOT need the `prerequisites=` two-base bridge the parent used (it split identity vs meeting
bases) because the v0.12 schema co-locates both in one `Base.metadata` — create_all already
emits tables in FK order.
"""
import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from .errors import SchemaInvariantError

logger = logging.getLogger("admin_api.schema.sync")

__all__ = ["SchemaInvariantError", "ensure_schema", "ensure_schema_sync"]

# SQLAlchemy type name → Postgres column type (for additive ALTER TABLE).
_TYPE_MAP = {
    "VARCHAR": lambda c: f"VARCHAR({c.type.length})" if getattr(c.type, "length", None) else "VARCHAR",
    "STRING": lambda c: f"VARCHAR({c.type.length})" if getattr(c.type, "length", None) else "VARCHAR",
    "TEXT": lambda c: "TEXT",
    "INTEGER": lambda c: "INTEGER",
    "BIGINT": lambda c: "BIGINT",
    "FLOAT": lambda c: "DOUBLE PRECISION",
    "BOOLEAN": lambda c: "BOOLEAN",
    "DATETIME": lambda c: "TIMESTAMP WITHOUT TIME ZONE",
    "TIMESTAMP": lambda c: "TIMESTAMP WITHOUT TIME ZONE",
    "JSONB": lambda c: "JSONB",
    "JSON": lambda c: "JSON",
    "ARRAY": lambda c: _array_type(c),
}


def _array_type(col):
    item_type_name = type(col.type.item_type).__name__.upper()
    inner = _TYPE_MAP.get(item_type_name, lambda c: item_type_name)(col)
    return f"{inner}[]"


def _pg_type(col):
    return _TYPE_MAP.get(type(col.type).__name__.upper(), lambda c: type(col.type).__name__.upper())(col)


def _col_default_sql(col):
    sd = col.server_default
    if sd is not None and hasattr(sd, "arg"):
        arg = sd.arg
        if callable(arg):
            return ""
        if hasattr(arg, "text"):
            return f" DEFAULT {arg.text}"
        return f" DEFAULT {arg}"
    return ""


def _sync_columns(conn: Connection, base):
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    for table in base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing_cols:
                continue
            pg_type = _pg_type(col)
            nullable = "" if col.nullable else " NOT NULL"
            default = _col_default_sql(col)
            if not col.nullable and not default:
                if "INT" in pg_type:
                    default = " DEFAULT 0"
                elif "VARCHAR" in pg_type or pg_type == "TEXT":
                    default = " DEFAULT ''"
                elif "[]" in pg_type:
                    default = " DEFAULT '{}'"
                elif pg_type in ("JSONB", "JSON"):
                    default = " DEFAULT '{}'"
            stmt = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {pg_type}{nullable}{default}'
            logger.info("schema-sync add column: %s", stmt)
            conn.execute(text(stmt))


def _sync_indexes(conn: Connection, base):
    """Additive index convergence — tolerant for non-unique, FAIL CLOSED for unique (#1186).

    A non-unique index is a performance hint: losing one degrades latency, nothing else, so a
    failed CREATE stays tolerated at DEBUG. A UNIQUE index is an *invariant the application code
    relies on* — ``uq_meeting_active_user_platform_native`` is the documented DB backstop for the
    one-bot-per-room spawn guard (meeting-api ``bot_spawn/adapters.py``). Logging that one and
    continuing produced exactly the failure #1186 records: the index never existed in production
    because 4 stale duplicate rows blocked it, every restart re-attempted and re-swallowed it, and
    the WARNING log-rotated away inside a day while the service reported itself healthy.

    So a unique-index failure now raises ``SchemaInvariantError``, which aborts ``ensure_schema``
    and therefore the admin-api startup hook — the process never binds, /health never answers, the
    startup/readiness probes never pass. That is intentional: a database whose duplicate rows block
    a load-bearing unique index MUST NOT be served by a process that assumes the index exists. The
    operator's next step is in the message.

    Failures are collected across all tables before raising, so one restart surfaces every blocking
    index rather than one per fix-and-retry cycle.
    """
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    unique_failures: list[tuple[str, str, str]] = []   # (table, index, underlying error)
    for table in base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing = {idx["name"] for idx in inspector.get_indexes(table.name) if idx["name"]}
        for index in table.indexes:
            if index.name and index.name in existing:
                continue
            # Per-index SAVEPOINT: a failed CREATE INDEX (e.g. a UNIQUE index on a table that still
            # holds rows violating it) must NOT poison the surrounding convergence transaction —
            # without the nested begin, the aborted txn would roll back the whole ensure_schema pass
            # before we can report WHICH index failed and why.
            try:
                with conn.begin_nested():
                    index.create(conn)
            except Exception as e:
                if getattr(index, "unique", False):
                    logger.error(
                        "schema-sync: UNIQUE index %s on %s NOT created: %s",
                        index.name, table.name, e,
                    )
                    unique_failures.append((table.name, str(index.name), str(e)))
                    continue
                # Non-unique: most often a benign race (index already present under a different
                # detection path). The savepoint rolled back; the rest of the convergence applies.
                logger.debug("index %s not created: %s", index.name, e)
    if unique_failures:
        raise SchemaInvariantError(_unique_index_failure_message(unique_failures))


def _unique_index_failure_message(failures: list[tuple[str, str, str]]) -> str:
    """Operator-facing text: which index, on what table, the underlying error, what to do next."""
    lines = [
        "schema-sync FAILED CLOSED: {n} UNIQUE index(es) could not be created, and admin-api "
        "will not start without them — application code treats these as invariants, not as "
        "performance hints.".format(n=len(failures)),
    ]
    for table, index, err in failures:
        lines.append(f"  - UNIQUE index {index} on table {table}: {err}")
    lines.append(
        "Remediation: duplicate rows block this unique index; resolve them (find the rows that "
        "collide on the index's key columns and delete or terminate the extras), then restart. "
        "See https://github.com/Vexa-ai/vexa/issues/1186"
    )
    return "\n".join(lines)


# MIGRATION-0004-backfill-token-scopes — grandfather pre-scope (0.10-era) API tokens.
#
# 0.10's api_tokens had no `scopes` column; keys were unscoped = allow-all. 0.12 adds
# `scopes ARRAY(Text) NOT NULL server_default '{}'`, so the additive `ADD COLUMN` above fills
# every pre-existing row with an empty array — which the gateway/validate path reads as
# no-access and 403s on every core route. This one-shot, idempotent backfill converges those
# empty/NULL rows to the full valid-scope set, mirroring 0.10's allow-all behavior. It only
# touches empties, so newly-minted (already-scoped) tokens are never widened, and re-running
# ensure_schema is a no-op once there are no empty rows left.
#
# See MIGRATION-0004-backfill-token-scopes.md. Kept in step with app.main.VALID_SCOPES.
# Bound as a Python list (not a '{...}' literal): asyncpg maps a list → text[], and rejects the
# literal string ("a sized iterable container expected"); psycopg accepts either. A list is the
# one form both drivers take.
_FULL_TOKEN_SCOPES = ["bot", "tx", "browser"]


def _backfill_token_scopes(conn: Connection):
    inspector = inspect(conn)
    if "api_tokens" not in set(inspector.get_table_names()):
        return
    result = conn.execute(text(
        "UPDATE api_tokens SET scopes = :full "
        "WHERE scopes = '{}'::text[] OR scopes IS NULL"
    ), {"full": _FULL_TOKEN_SCOPES})
    if result.rowcount:
        logger.info("schema-sync backfill token scopes (MIGRATION-0004): %d row(s) grandfathered "
                    "to %s", result.rowcount, _FULL_TOKEN_SCOPES)


# MIGRATION-0005 — the meetings-list EVENT-time ordering (#1222).
#
# The list orders by COALESCE(data->>'scheduled_at', start_time, created_at) with non-terminal
# rows pinned first. That COALESCE must live in an expression INDEX (the list's union branches are
# top-N index walks, #800), and an index expression must be IMMUTABLE — a bare text→timestamptz
# cast is only STABLE (it reads DateStyle/TimeZone). This wrapper declares the cast IMMUTABLE,
# which is sound here because `scheduled_at` is written as ISO-8601 (calendar sync + the meetings
# API validate it) and any unparsable value falls through the exception guard instead of failing
# row writes via index maintenance. Naive-UTC `timestamp` return to match the column types.
#
# CREATE OR REPLACE, run BEFORE create_all/_sync_indexes: the two ix_meeting_*_event_order index
# expressions (models.py) reference it, and both the fresh-DB and the additive path must find it.
# Prod builds it out-of-band first — see MIGRATION-0005-meeting-event-order.md.
_MEETING_EVENT_TIME_FN = """
CREATE OR REPLACE FUNCTION meeting_event_time(
    data jsonb, start_time timestamp, created_at timestamp
) RETURNS timestamp
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $fn$
BEGIN
    RETURN COALESCE(
        ((data ->> 'scheduled_at')::timestamptz AT TIME ZONE 'UTC'),
        start_time,
        created_at
    );
EXCEPTION WHEN OTHERS THEN
    RETURN COALESCE(start_time, created_at);
END
$fn$
"""


def _sync_functions(conn: Connection):
    conn.execute(text(_MEETING_EVENT_TIME_FN))


def _ensure_schema_sync(conn: Connection, base):
    _sync_functions(conn)                              # MIGRATION-0005 fn (indexes reference it)
    base.metadata.create_all(conn, checkfirst=True)   # missing tables, FK order
    _sync_columns(conn, base)                          # additive columns
    _backfill_token_scopes(conn)                       # MIGRATION-0004 data backfill
    _sync_indexes(conn, base)                          # additive indexes


async def ensure_schema(engine, base):
    """Converge the DB to `base.metadata`. Never drops. Idempotent. async-engine entry."""
    async with engine.begin() as conn:
        await conn.run_sync(_ensure_schema_sync, base)


def ensure_schema_sync(engine, base):
    """Sync-engine entry (same convergence) — used by the testcontainers evals."""
    with engine.begin() as conn:
        _ensure_schema_sync(conn, base)
