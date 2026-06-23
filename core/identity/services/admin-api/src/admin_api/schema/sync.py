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

logger = logging.getLogger("admin_api.schema.sync")

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
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    for table in base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing = {idx["name"] for idx in inspector.get_indexes(table.name) if idx["name"]}
        for index in table.indexes:
            if index.name and index.name in existing:
                continue
            # Per-index SAVEPOINT: a failed CREATE INDEX (e.g. a UNIQUE index on a table that still
            # holds rows violating it) must NOT poison the surrounding convergence transaction —
            # without the nested begin, the aborted txn would roll back the whole ensure_schema pass.
            try:
                with conn.begin_nested():
                    index.create(conn)
            except Exception as e:
                # Most often a benign race (index already present under a different detection path) —
                # but a UNIQUE index failing on duplicate data is a real, actionable miss, so surface
                # it at WARNING rather than swallowing it at debug. The savepoint rolled back, so the
                # rest of the convergence still applies.
                level = logging.WARNING if getattr(index, "unique", False) else logging.DEBUG
                logger.log(level, "index %s not created: %s", index.name, e)


def _ensure_schema_sync(conn: Connection, base):
    base.metadata.create_all(conn, checkfirst=True)   # missing tables, FK order
    _sync_columns(conn, base)                          # additive columns
    _sync_indexes(conn, base)                          # additive indexes


async def ensure_schema(engine, base):
    """Converge the DB to `base.metadata`. Never drops. Idempotent. async-engine entry."""
    async with engine.begin() as conn:
        await conn.run_sync(_ensure_schema_sync, base)


def ensure_schema_sync(engine, base):
    """Sync-engine entry (same convergence) — used by the testcontainers evals."""
    with engine.begin() as conn:
        _ensure_schema_sync(conn, base)
