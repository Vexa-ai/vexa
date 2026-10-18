"""session_schema.py — the session store's tables and its own Postgres role (ADR-0048).

agent-api never holds the instance's admin credential. The MIGRATIONS PATH runs this module with
that credential — the compose `agent-sessions-init` service, the Helm migrations Job, the Lite
entrypoint — and it:

1. creates (or re-asserts) a LOGIN role, by default ``vexa_agent_sessions``, with no admin power
   (NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS) and the password it is given;
2. creates the ``agent_session*`` tables (``session_store._DDL``, the schema of record) and records
   the schema version;
3. grants that role SELECT/INSERT/UPDATE/DELETE on those tables and SELECT on the version table —
   nothing else, so it cannot read accounts, tokens or meetings.

agent-api then logs in as that role and only CHECKS (``check``): the login must not be a superuser,
and the recorded schema version must be the one this code knows. It runs no DDL against Postgres.

    python -m control_plane.session_schema init   # admin DB_* + VEXA_SESSIONS_DB_USER/_PASSWORD
    python -m control_plane.session_schema wait   # the role's own login; exits 0 once the schema is ready
"""
from __future__ import annotations

import os
import re
import sys
import time

from control_plane.session_store import _ADDED_COLUMNS, _DDL, SCHEMA_VERSION

DEFAULT_ROLE = "vexa_agent_sessions"
VERSION_TABLE = "agent_session_schema"
#: Every table the schema of record creates, but the version table — read off ``_DDL`` so a table a
#: later change adds (calls, proposals) is granted with it, never left to the admin alone.
TABLES = tuple(m.group(1) for ddl in _DDL for m in [re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", ddl)]
               if m and m.group(1) != VERSION_TABLE)
_ROLE = re.compile(r"^[a-z_][a-z0-9_]{2,62}$")
#: Minted secrets are hex; anything that would need quoting is refused rather than escaped.
_PASSWORD = re.compile(r"^[A-Za-z0-9._~+/=-]{24,256}$")
_RESERVED = {"postgres", "root", "admin", "rdsadmin", "azure_superuser", "cloudsqlsuperuser"}


class SchemaNotReady(RuntimeError):
    """The store must stay off: no schema yet, an unknown version, or a superuser login."""


def _x(conn, sql: str, args=()):
    cur = conn.cursor()
    cur.execute(sql, tuple(args))
    return cur


def _check_role(role: str) -> str:
    if not _ROLE.fullmatch(role or "") or role in _RESERVED or role.startswith("pg_"):
        raise ValueError("not a role name the session store may use")
    return role


def init(conn, *, role: str, password: str, database: str, placeholder: str = "%s") -> None:
    """Run with the instance's admin credential. Idempotent: re-running re-asserts the role's limits
    and password, creates what is missing and re-grants."""
    role = _check_role(role)
    if not _PASSWORD.fullmatch(password or ""):
        raise ValueError("the session store's database password must be 24-256 URL-safe characters")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,62}", database or ""):
        raise ValueError("not a database name")
    q = f'"{role}"'
    exists = _x(conn, f"SELECT 1 FROM pg_roles WHERE rolname = {placeholder}", (role,)).fetchone()
    if not exists:
        _x(conn, f"CREATE ROLE {q}")
    _x(conn, f"ALTER ROLE {q} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
             f"NOREPLICATION NOBYPASSRLS PASSWORD '{password}'")
    for ddl in _DDL:
        _x(conn, ddl)
    for table, col, decl in _ADDED_COLUMNS:
        _x(conn, f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {decl}")
    if _x(conn, f"SELECT version FROM {VERSION_TABLE}").fetchone() is None:
        _x(conn, f"INSERT INTO {VERSION_TABLE} (version) VALUES ({placeholder})", (SCHEMA_VERSION,))
    _x(conn, f'GRANT CONNECT ON DATABASE "{database}" TO {q}')
    _x(conn, f"GRANT USAGE ON SCHEMA public TO {q}")
    _x(conn, f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {', '.join(TABLES)} TO {q}")
    _x(conn, f"GRANT SELECT ON TABLE {VERSION_TABLE} TO {q}")


def check(conn) -> int:
    """agent-api's boot check, as its own role: never a superuser, and the schema version this
    code knows. Returns the version; raises ``SchemaNotReady`` otherwise."""
    try:
        row = _x(conn, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
    except Exception as exc:  # noqa: BLE001
        raise SchemaNotReady(f"cannot read the login's role ({type(exc).__name__})") from None
    if row is None or bool(row[0]):
        raise SchemaNotReady("the session store refuses a superuser login; give agent-api its own role "
                             "(VEXA_SESSIONS_DB_USER, created by the migrations path)")
    try:
        got = _x(conn, f"SELECT version FROM {VERSION_TABLE}").fetchone()
    except Exception as exc:  # noqa: BLE001 — no table yet: the migrations path has not run
        raise SchemaNotReady(f"the session store's schema is not there yet ({type(exc).__name__})") from None
    if got is None or int(got[0]) != SCHEMA_VERSION:
        raise SchemaNotReady(f"the session store's schema version is {got[0] if got else 'unset'}, "
                             f"this agent-api knows {SCHEMA_VERSION}")
    return SCHEMA_VERSION


def _admin_conn():
    import pg8000.dbapi  # noqa: PLC0415

    conn = pg8000.dbapi.connect(user=os.environ.get("DB_USER", "postgres"),
                                password=os.environ.get("DB_PASSWORD", ""),
                                host=os.environ.get("DB_HOST", "localhost"),
                                port=int(os.environ.get("DB_PORT", "5432") or 5432),
                                database=os.environ.get("DB_NAME", "vexa"))
    conn.autocommit = True
    return conn


def _role_conn():
    import pg8000.dbapi  # noqa: PLC0415

    conn = pg8000.dbapi.connect(user=os.environ.get("VEXA_SESSIONS_DB_USER", DEFAULT_ROLE),
                                password=os.environ.get("VEXA_SESSIONS_DB_PASSWORD", ""),
                                host=os.environ.get("DB_HOST", "localhost"),
                                port=int(os.environ.get("DB_PORT", "5432") or 5432),
                                database=os.environ.get("DB_NAME", "vexa"))
    conn.autocommit = True
    return conn


def main(argv: "list[str]") -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "init":
        role = os.environ.get("VEXA_SESSIONS_DB_USER", DEFAULT_ROLE)
        password = os.environ.get("VEXA_SESSIONS_DB_PASSWORD", "")
        if not password:
            print("[session-schema] VEXA_SESSIONS_DB_PASSWORD is unset; the session store stays off", flush=True)
            return 0
        conn = None
        for attempt in range(30):
            try:
                conn = _admin_conn()
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 29:
                    print(f"[session-schema] STOP: the database did not answer ({type(exc).__name__})", flush=True)
                    return 1
                time.sleep(2)
        try:
            init(conn, role=role, password=password, database=os.environ.get("DB_NAME", "vexa"))
        except ValueError as exc:
            print(f"[session-schema] STOP: {exc}", flush=True)
            return 1
        finally:
            conn.close()
        print(f"[session-schema] role {role} and the agent_session* tables are ready "
              f"(schema version {SCHEMA_VERSION}; grants on those tables only)", flush=True)
        return 0
    if cmd == "wait":
        deadline = time.time() + float(os.environ.get("VEXA_SESSIONS_WAIT_SEC", "600") or 600)
        while True:
            try:
                conn = _role_conn()
                try:
                    check(conn)
                finally:
                    conn.close()
                print("[session-schema] ready", flush=True)
                return 0
            except Exception as exc:  # noqa: BLE001
                if time.time() > deadline:
                    print(f"[session-schema] not ready: {exc if isinstance(exc, SchemaNotReady) else type(exc).__name__}",
                          flush=True)
                    return 1
                time.sleep(3)
    print("usage: python -m control_plane.session_schema init|wait", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
