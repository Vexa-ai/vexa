"""
Idempotent schema convergence for Postgres.

ensure_schema() brings the database in line with SQLAlchemy model metadata
without ever dropping tables, columns, or data.  It handles:

- Empty DB → creates all tables in FK order
- Main-branch DB → adds missing columns/indexes, creates new tables
- Current branch DB → no-op
- Partial DB → completes missing tables without FK errors
"""

import logging
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeMeta

logger = logging.getLogger("schema_sync")

# Mapping from SQLAlchemy types to Postgres column types for ALTER TABLE
_TYPE_MAP = {
    "VARCHAR": lambda col: f"VARCHAR({col.type.length})" if getattr(col.type, 'length', None) else "VARCHAR",
    "STRING": lambda col: f"VARCHAR({col.type.length})" if getattr(col.type, 'length', None) else "VARCHAR",
    "TEXT": lambda col: "TEXT",
    "INTEGER": lambda col: "INTEGER",
    "BIGINT": lambda col: "BIGINT",
    "SMALLINT": lambda col: "SMALLINT",
    "FLOAT": lambda col: "DOUBLE PRECISION",
    "BOOLEAN": lambda col: "BOOLEAN",
    "DATETIME": lambda col: "TIMESTAMP WITHOUT TIME ZONE",
    "TIMESTAMP": lambda col: "TIMESTAMP WITHOUT TIME ZONE",
    "DATE": lambda col: "DATE",
    "JSONB": lambda col: "JSONB",
    "JSON": lambda col: "JSON",
    "ARRAY": lambda col: _array_type(col),
}


def _array_type(col):
    """Resolve ARRAY column to Postgres type like TEXT[]."""
    item_type = col.type.item_type
    type_name = type(item_type).__name__.upper()
    pg = _TYPE_MAP.get(type_name, lambda c: type_name)(col)
    return f"{pg}[]"


def _pg_type(col):
    """Convert a SQLAlchemy Column to a Postgres type string."""
    sa_type_name = type(col.type).__name__.upper()
    resolver = _TYPE_MAP.get(sa_type_name)
    if resolver:
        return resolver(col)
    return sa_type_name


def _col_default_sql(col):
    """Return the DEFAULT clause for a column, or empty string."""
    if col.server_default is not None:
        sd = col.server_default
        if hasattr(sd, "arg"):
            arg = sd.arg
            if callable(arg):
                return ""
            if hasattr(arg, "text"):
                return f" DEFAULT {arg.text}"
            return f" DEFAULT {arg}"
    return ""


def _sync_tables(conn: Connection, base):
    """Create missing tables via create_all(checkfirst=True)."""
    base.metadata.create_all(conn, checkfirst=True)


def _sync_columns(conn: Connection, base):
    """Add missing columns to existing tables."""
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

            # If NOT NULL with no default, use a safe default to avoid failing on existing rows
            if not col.nullable and not default:
                if "INT" in pg_type:
                    default = " DEFAULT 0"
                elif "VARCHAR" in pg_type or pg_type == "TEXT":
                    default = " DEFAULT ''"
                elif "[]" in pg_type:
                    default = " DEFAULT '{}'"
                elif pg_type == "BOOLEAN":
                    default = " DEFAULT false"
                elif pg_type == "JSONB" or pg_type == "JSON":
                    default = " DEFAULT '{}'"

            stmt = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {pg_type}{nullable}{default}'
            logger.info(f"Adding column: {stmt}")
            conn.execute(text(stmt))


def _sync_indexes(conn: Connection, base):
    """Add missing indexes (skips existing ones by name)."""
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())

    for table in base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue

        existing_indexes = {idx["name"] for idx in inspector.get_indexes(table.name) if idx["name"]}

        for index in table.indexes:
            if index.name and index.name in existing_indexes:
                continue
            try:
                index.create(conn)
                logger.info(f"Created index: {index.name} on {table.name}")
            except Exception as e:
                # Index might already exist under a different detection path
                logger.debug(f"Index {index.name} on {table.name} skipped: {e}")


def _ensure_schema_sync(conn: Connection, base, prerequisites=None):
    """Synchronous implementation called inside run_sync."""
    if prerequisites is not None:
        logger.info("Creating prerequisite tables...")
        prerequisites.metadata.create_all(conn, checkfirst=True)

    logger.info("Creating missing tables...")
    _sync_tables(conn, base)

    logger.info("Adding missing columns...")
    _sync_columns(conn, base)

    logger.info("Adding missing indexes...")
    _sync_indexes(conn, base)

    logger.info("Schema sync complete.")


async def ensure_schema(engine, base, prerequisites=None):
    """
    Converge database to match models defined in base.metadata.

    1. If prerequisites given, create those tables first (e.g., admin Base
       so meeting-api can reference users via FK).
    2. create_all(checkfirst=True) for the main base — creates missing tables.
    3. Inspect existing tables, ADD any missing columns.
    4. Create any missing indexes.

    Never drops tables, columns, or data. Idempotent.
    """
    async with engine.begin() as conn:
        await conn.run_sync(_ensure_schema_sync, base, prerequisites)
