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
import asyncio
import logging
import os
import time

from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
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
    "LARGEBINARY": lambda c: "BYTEA",
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


def _sync_indexes(conn: Connection, base, deferred: list | None = None):
    """Additive index convergence — tolerant for non-unique, FAIL CLOSED for unique (#1186).

    On Postgres a missing NON-UNIQUE index is not built here: it is appended to ``deferred`` and
    built after this transaction commits, ``CONCURRENTLY`` (``_build_deferred_indexes``). This
    transaction can hold ACCESS EXCLUSIVE on ``users`` and ``api_tokens`` (an additive ``ADD
    COLUMN``) until it commits, so an index built inside it would hold every ``/internal/validate``
    for the whole build. An INVALID leftover of a failed concurrent build under a non-unique model
    name is deferred the same way, to be dropped and rebuilt. UNIQUE indexes stay in-band: they are
    invariants the code relies on, and their failure stops the convergence (below).

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
    invalid = _invalid_index_names(conn)
    unique_failures: list[tuple[str, str, str]] = []   # (table, index, underlying error)
    for table in base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing = {idx["name"] for idx in inspector.get_indexes(table.name) if idx["name"]}
        for index in table.indexes:
            unique = bool(getattr(index, "unique", False))
            if index.name and index.name in existing:
                if index.name not in invalid:
                    continue
                if unique or deferred is None:
                    logger.error(_invalid_index_message(table.name, str(index.name)))
                    continue
            if deferred is not None and not unique:
                deferred.append(index)
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


#: Reflection lists an index by name whether or not Postgres can use it, so the INVALID leftover of a
#: failed ``CREATE INDEX CONCURRENTLY`` would otherwise read as present and never be rebuilt.
_INVALID_INDEXES_SQL = (
    "SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE NOT i.indisvalid AND n.nspname = current_schema()"
)


def _invalid_index_names(conn: Connection) -> set[str]:
    if getattr(getattr(conn, "dialect", None), "name", None) != "postgresql":
        return set()
    return {row[0] for row in conn.execute(text(_INVALID_INDEXES_SQL))}


def _invalid_index_message(table: str, index: str) -> str:
    return (f"schema-sync: index {index} on {table} exists but is INVALID (a failed CREATE INDEX "
            f"CONCURRENTLY); Postgres never uses it. Rebuild it: DROP INDEX CONCURRENTLY {index}; "
            f"then restart admin-api (or run python -m admin_api.migrate).")


#: The concurrent builds run on their own autocommit connection after the convergence commits.
#: One process builds at a time (every admin-api replica and the migrations Job converge): the
#: others find the advisory lock held and leave the work to it. A build first waits for every
#: transaction already writing the table; ``CONCURRENT_LOCK_TIMEOUT_MS`` bounds each of those waits,
#: so a long-open writer delays a boot by seconds, never indefinitely. The lock it takes (SHARE
#: UPDATE EXCLUSIVE) conflicts with no read or write, so nobody queues behind a build.
CONCURRENT_INDEX_LOCK_KEY = 0x76786131          # "vxa1"
CONCURRENT_LOCK_TIMEOUT_MS = 5000


def _concurrent_index_sql(index, dialect) -> str:
    from sqlalchemy.schema import CreateIndex  # noqa: PLC0415

    ddl = str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect))
    prefix = "CREATE INDEX IF NOT EXISTS "
    if not ddl.startswith(prefix):
        raise ValueError(f"unexpected index DDL for {index.name}: {ddl[:80]}")
    return "CREATE INDEX CONCURRENTLY IF NOT EXISTS " + ddl[len(prefix):]


def _build_deferred_indexes(conn: Connection, indexes: list) -> None:
    """Build each deferred non-unique index CONCURRENTLY. ``conn`` must be in AUTOCOMMIT.

    A failed build is logged with the statement to run by hand and does not fail the convergence:
    a non-unique index is a performance hint. Postgres leaves an INVALID index behind a failed
    concurrent build; the next convergence drops and rebuilds it."""
    if not indexes:
        return
    if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                        {"k": CONCURRENT_INDEX_LOCK_KEY}).scalar():
        logger.warning("schema-sync: another process is building indexes concurrently; leaving %s "
                       "to it", ", ".join(str(i.name) for i in indexes))
        return
    try:
        conn.execute(text(f"SET lock_timeout = '{CONCURRENT_LOCK_TIMEOUT_MS}ms'"))
        invalid = _invalid_index_names(conn)
        for index in indexes:
            sql = _concurrent_index_sql(index, conn.dialect)
            try:
                if index.name in invalid:
                    conn.execute(text(f'DROP INDEX CONCURRENTLY IF EXISTS "{index.name}"'))
                started = time.monotonic()
                conn.execute(text(sql))
                logger.info("schema-sync: built index %s on %s concurrently in %.2fs",
                            index.name, index.table.name, time.monotonic() - started)
            except DBAPIError as e:
                logger.error("schema-sync: index %s on %s NOT built (%s); the next convergence "
                             "retries it. By hand: DROP INDEX CONCURRENTLY IF EXISTS %s; %s",
                             index.name, index.table.name, getattr(e, "orig", e), index.name, sql)
    finally:
        conn.execute(text("RESET lock_timeout"))
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": CONCURRENT_INDEX_LOCK_KEY})


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
# 0.10's api_tokens had no `scopes` column; keys were unscoped = allow-all. The `scopes ARRAY(Text)
# NOT NULL server_default '{}'` column, added by the additive `ADD COLUMN`, fills every pre-existing
# row with an empty array, which the read path (`get_current_user`, `/internal/validate`) treats as
# no access. Those rows, and only those, are converged to the full valid-scope set, in the same
# transaction that adds the column, mirroring 0.10's allow-all.
#
# A row whose `scopes` column already exists is never rewritten. An empty array there is a key with
# no access, and it keeps none: the read path denies `{}` and NULL alike, so rewriting it would grant
# access the key never had.
#
# See MIGRATION-0004-backfill-token-scopes.md. Kept in step with app.main.VALID_SCOPES.
# Bound as a Python list (not a '{...}' literal): asyncpg maps a list → text[], and rejects the
# literal string ("a sized iterable container expected"); psycopg accepts either. A list is the
# one form both drivers take.
_FULL_TOKEN_SCOPES = ["bot", "tx", "browser"]


def _tokens_predate_scopes(conn: Connection) -> bool:
    """True when ``api_tokens`` exists without a ``scopes`` column: this pass adds it."""
    inspector = inspect(conn)
    if "api_tokens" not in set(inspector.get_table_names()):
        return False
    return "scopes" not in {c["name"] for c in inspector.get_columns("api_tokens")}


def _backfill_token_scopes(conn: Connection, predate_scopes: bool):
    if not predate_scopes:
        return
    result = conn.execute(text(
        "UPDATE api_tokens SET scopes = :full WHERE scopes = '{}'::text[]"
    ), {"full": _FULL_TOKEN_SCOPES})
    if result.rowcount:
        logger.info("schema-sync backfill token scopes (MIGRATION-0004): %d pre-scope row(s) "
                    "grandfathered to %s", result.rowcount, _FULL_TOKEN_SCOPES)


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


# MIGRATION-0008 — the org as a first-class object. The convergence above makes `orgs` and
# `org_members` (new tables) and adds `users.org_id` with a constant default (a catalog change on
# Postgres >= 11: no rewrite, no per-row UPDATE). This makes sure the default org EXISTS, so every
# `users.org_id` points at a real row. Idempotent: ON CONFLICT DO NOTHING, and the CRM binding is
# stamped only while it is still unset, so an operator's later choice is never overwritten.
DEFAULT_ORG_NAME = "Default"


def _ensure_default_org(conn: Connection):
    from .models import DEFAULT_ORG_ID  # noqa: PLC0415 — models imports nothing from here

    inspector = inspect(conn)
    if "orgs" not in set(inspector.get_table_names()):
        return
    crm = (os.environ.get("CRM_ORG_ID") or "").strip() or None
    conn.execute(text(
        "INSERT INTO orgs (id, name, policy_source, crm_org_id, data, created_at) "
        "VALUES (:id, :name, '_global', :crm, '{}'::jsonb, now()) ON CONFLICT (id) DO NOTHING"
    ), {"id": DEFAULT_ORG_ID, "name": DEFAULT_ORG_NAME, "crm": crm})
    if crm:
        conn.execute(text("UPDATE orgs SET crm_org_id = :crm WHERE id = :id AND crm_org_id IS NULL"),
                     {"id": DEFAULT_ORG_ID, "crm": crm})


# MIGRATION-0011 — every sign-in credential ends with the cookie that carries it (B1).
#
# A session minted before sessions had a lifetime, and the `terminal-login` KEY a browser was signed
# in with before sessions existed (or on a deployment without them), have `expires_at` NULL: a
# standing credential, though the browser cookie that carried it lived 30 days. Each gets the end its
# cookie had — `created_at` + 30 days, which may already be past (that cookie is gone) — and a row
# with no `created_at` gets 30 days from this convergence. Only rows with no end are touched, so the
# pass is idempotent and an operator's own expiry is never moved. Self-serve keys (any other name)
# are the person's to bound, and are left alone.
LOGIN_LIFETIME_SECONDS = 60 * 60 * 24 * 30
SIGN_IN_TOKEN_NAME = "terminal-login"


def _bound_sign_in_credentials(conn: Connection):
    if conn.dialect.name != "postgresql":
        return
    inspector = inspect(conn)
    if "api_tokens" not in set(inspector.get_table_names()):
        return
    cols = {c["name"] for c in inspector.get_columns("api_tokens")}
    if not {"kind", "expires_at", "created_at", "name", "scopes"} <= cols:
        return
    result = conn.execute(text(
        "UPDATE api_tokens "
        "SET expires_at = COALESCE(created_at, now() AT TIME ZONE 'utc') + make_interval(secs => :life) "
        "WHERE expires_at IS NULL AND (kind = 'session' "
        "OR (kind = 'key' AND name = :name AND 'browser' = ANY(scopes)))"
    ), {"life": LOGIN_LIFETIME_SECONDS, "name": SIGN_IN_TOKEN_NAME})
    if result.rowcount:
        logger.info("schema-sync (MIGRATION-0011): %d sign-in credential(s) with no end now end with "
                    "their cookie (created_at + 30 days)", result.rowcount)


# MIGRATION-0013 — keys are held as their SHA-256, never as their value (`key_store.py`).
#
# Every row that still holds a plaintext `token` gets `token_hash` (SHA-256 of the value, hex) and
# `token_hint` (prefix + last four), and its `token` is cleared. The key itself is unchanged: a caller
# presents the same value, identity hashes it, and the row matches. `token` loses its NOT NULL first,
# a catalog-only change. Idempotent: it reads only rows whose `token` is set, so a second boot finds
# none, and a row a not-yet-upgraded writer adds in plaintext is hashed on the next one. One-way:
# nothing can restore a value from its hash.
def _hash_api_tokens(conn: Connection):
    from ..key_store import hash_token, token_hint  # noqa: PLC0415 — key_store imports nothing from here

    inspector = inspect(conn)
    if "api_tokens" not in set(inspector.get_table_names()):
        return
    cols = {c["name"]: c for c in inspector.get_columns("api_tokens")}
    if not {"token", "token_hash", "token_hint"} <= set(cols):
        return
    if conn.dialect.name == "postgresql" and not cols["token"]["nullable"]:
        conn.execute(text('ALTER TABLE "api_tokens" ALTER COLUMN "token" DROP NOT NULL'))
    rows = conn.execute(text("SELECT id, token FROM api_tokens WHERE token IS NOT NULL")).all()
    if not rows:
        return
    conn.execute(text("UPDATE api_tokens SET token_hash = :h, token_hint = :hint, token = NULL "
                      "WHERE id = :id"),
                 [{"id": r[0], "h": hash_token(r[1]), "hint": token_hint(r[1])} for r in rows])
    logger.info("schema-sync (MIGRATION-0013): %d key(s) now held as their SHA-256; plaintext cleared",
                len(rows))


# MIGRATION-0014 — a person sees only the keys they made; the product's own credentials end and go.
#
# Rows from before the `system` class are classified once more on every boot (idempotent: it reads
# only `kind = 'key'` rows that match):
#   * a key named for a sign-in surface (`key_store.SIGN_IN_KEY_NAMES`) is the key a cookie carried;
#   * an unnamed key that lived a day or less is one flows minted for itself (15 minutes each).
# Each becomes `system`; one with no end gets its cookie's (`created_at` + 30 days). Then every
# expired `session` and `system` row is deleted: nothing authenticates with it, nobody is shown it.
# Unnamed keys with no end are NOT touched: they cannot be told apart from a key a person made, and
# whether to retire them is the founder's call (identity PRD, "старые бессрочные ключи").
def _classify_and_sweep_keys(conn: Connection):
    from ..key_store import HIDDEN_KINDS, SIGN_IN_KEY_NAMES, SYSTEM_KEY_LIFETIME_SECONDS  # noqa: PLC0415

    if conn.dialect.name != "postgresql":
        return
    inspector = inspect(conn)
    if "api_tokens" not in set(inspector.get_table_names()):
        return
    classified = conn.execute(text(
        "UPDATE api_tokens SET kind = 'system', "
        "expires_at = COALESCE(expires_at, COALESCE(created_at, now() AT TIME ZONE 'utc') "
        "+ make_interval(secs => :life)) "
        "WHERE kind = 'key' AND (lower(name) = ANY(:names) OR (name IS NULL AND expires_at IS NOT NULL "
        "AND created_at IS NOT NULL AND expires_at - created_at <= interval '1 day'))"
    ), {"life": SYSTEM_KEY_LIFETIME_SECONDS, "names": sorted(SIGN_IN_KEY_NAMES)})
    swept = conn.execute(text(
        "DELETE FROM api_tokens WHERE kind = ANY(:kinds) AND expires_at IS NOT NULL "
        "AND expires_at < now() AT TIME ZONE 'utc'"
    ), {"kinds": list(HIDDEN_KINDS)})
    if classified.rowcount or swept.rowcount:
        logger.info("schema-sync (MIGRATION-0014): %d key(s) classified system; %d expired session/system "
                    "credential(s) deleted", classified.rowcount, swept.rowcount)


def _sync_functions(conn: Connection):
    conn.execute(text(_MEETING_EVENT_TIME_FN))


#: A CONVERGENCE NEVER QUEUES LIVE READS BEHIND A LOCK IT IS WAITING FOR (2026-10-13: validate at
#: 23–38 s with Postgres sessions waiting on a lock during deploys). A waiting `ALTER TABLE` keeps its
#: place in the table's lock queue and every later reader — every `/internal/validate` — waits behind
#: it. Each lock is therefore asked for with this timeout (below validate's own 1 s read bound), and
#: a convergence that could not get one is rolled back and tried again after LOCK_RETRY_BACKOFF_S.
LOCK_TIMEOUT_MS = 500
LOCK_RETRY_BACKOFF_S = (0.5, 1, 2, 4, 8, 15)
_LOCK_NOT_AVAILABLE = "55P03"


def _lock_not_available(e: Exception) -> bool:
    orig = getattr(e, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code == _LOCK_NOT_AVAILABLE or "lock timeout" in str(e).lower()


def _bound_locks(conn: Connection):
    if conn.dialect.name == "postgresql":
        conn.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'"))


def _ensure_schema_sync(conn: Connection, base, deferred: list | None = None):
    _bound_locks(conn)
    _sync_functions(conn)                              # MIGRATION-0005 fn (indexes reference it)
    predate_scopes = _tokens_predate_scopes(conn)
    base.metadata.create_all(conn, checkfirst=True)   # missing tables, FK order
    _sync_columns(conn, base)                          # additive columns
    _backfill_token_scopes(conn, predate_scopes)       # MIGRATION-0004 data backfill
    _ensure_default_org(conn)                          # MIGRATION-0008 the default org row
    _bound_sign_in_credentials(conn)                   # MIGRATION-0011 sign-in credentials end
    _hash_api_tokens(conn)                             # MIGRATION-0013 keys held as SHA-256
    _classify_and_sweep_keys(conn)                     # MIGRATION-0014 hidden credentials end and go
    _sync_indexes(conn, base, deferred)                # additive indexes (non-unique: deferred)


def _deferred_list(engine) -> list | None:
    return [] if getattr(getattr(engine, "dialect", None), "name", None) == "postgresql" else None


async def ensure_schema(engine, base):
    """Converge the DB to `base.metadata`. Never drops. Idempotent. async-engine entry."""
    for wait in (*LOCK_RETRY_BACKOFF_S, None):
        deferred = _deferred_list(engine)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(_ensure_schema_sync, base, deferred)
        except DBAPIError as e:
            if wait is None or not _lock_not_available(e):
                raise
            logger.warning("schema-sync: a lock was not available in %d ms; retrying in %ss",
                           LOCK_TIMEOUT_MS, wait)
            await asyncio.sleep(wait)
            continue
        if deferred:
            try:
                async with engine.connect() as conn:
                    conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
                    await conn.run_sync(_build_deferred_indexes, deferred)
            except Exception as e:  # noqa: BLE001 — a performance hint never fails the convergence
                logger.error("schema-sync: concurrent index builds did not run: %s", e)
        return


def ensure_schema_sync(engine, base):
    """Sync-engine entry (same convergence and retry) — used by the testcontainers evals."""
    for wait in (*LOCK_RETRY_BACKOFF_S, None):
        deferred = _deferred_list(engine)
        try:
            with engine.begin() as conn:
                _ensure_schema_sync(conn, base, deferred)
        except DBAPIError as e:
            if wait is None or not _lock_not_available(e):
                raise
            logger.warning("schema-sync: a lock was not available in %d ms; retrying in %ss",
                           LOCK_TIMEOUT_MS, wait)
            time.sleep(wait)
            continue
        if deferred:
            try:
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    _build_deferred_indexes(conn, deferred)
            except Exception as e:  # noqa: BLE001 — a performance hint never fails the convergence
                logger.error("schema-sync: concurrent index builds did not run: %s", e)
        return
