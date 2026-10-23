"""session_schema.py — the session store's tables and its own Postgres role (ADR-0048).

agent-api never holds the instance's admin credential. The MIGRATIONS PATH runs this module with
that credential — the compose `agent-sessions-init` service, the Helm migrations Job, the Lite
entrypoint — and it:

1. creates (or re-asserts) a LOGIN role, by default ``vexa_agent_sessions``, with no admin power
   (NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS) and the password it is given.
   Before anything else it checks that the credential may do that (``plan_role``) and otherwise
   stops, naming the grant the database team has to run: CREATEROLE to create the role; on
   Postgres 16+ also the ADMIN option on a role it did not create; or a role pre-created with the
   password it is given, which then needs no role privilege at all;
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


class PrivilegeMissing(ValueError):
    """The migration credential may not make or re-assert the store's role. The message names the
    statement the database team runs; nothing has been written when it is raised."""


DOCS = "https://docs.vexa.ai/deployment-kubernetes#database-privileges-on-an-external-postgres"
#: The role attributes the store's role must not hold, as ``pg_roles`` columns → the keyword.
_POWERS = (("rolsuper", "SUPERUSER"), ("rolcreatedb", "CREATEDB"), ("rolcreaterole", "CREATEROLE"),
           ("rolreplication", "REPLICATION"), ("rolbypassrls", "BYPASSRLS"))


def _x(conn, sql: str, args=()):
    cur = conn.cursor()
    cur.execute(sql, tuple(args))
    return cur


def _check_role(role: str) -> str:
    if not _ROLE.fullmatch(role or "") or role in _RESERVED or role.startswith("pg_"):
        raise ValueError("not a role name the session store may use")
    return role


def plan_role(conn, *, role: str, placeholder: str = "%s", logs_in=None) -> str:
    """What the migration credential does to the store's role, decided before anything is written:
    ``"create"``; ``"reassert"`` (a superuser sets every limit and the password again); ``"alter"``
    (LOGIN, NOINHERIT and the password only); or ``"keep"`` (a role the database team pre-created,
    whose password already logs in). Raises ``PrivilegeMissing`` naming the grant
    when the credential can do none of them.

    A non-superuser may not name SUPERUSER, REPLICATION or BYPASSRLS in ALTER ROLE at all, even to
    switch them off (Postgres 16+ adds CREATEDB), so only a superuser re-asserts the full list; a
    CREATEROLE credential creates the role with every power off and later sets only LOGIN, NOINHERIT
    and the password. From Postgres 16 CREATEROLE reaches only roles the credential holds the ADMIN
    option on, which it gets on the roles it creates. ``logs_in`` answers whether the given password
    already logs in as the role."""
    me = _x(conn, "SELECT current_user, rolsuper, rolcreaterole, "
                  "current_setting('server_version_num')::int FROM pg_roles WHERE rolname = current_user").fetchone()
    if me is None:
        raise PrivilegeMissing("cannot read the migration credential's own role in pg_roles")
    user, superuser, createrole, version = me[0], bool(me[1]), bool(me[2]), int(me[3])
    if superuser:
        return "create" if _target(conn, role, placeholder) is None else "reassert"
    q, u = f'"{role}"', f'"{user}"'
    target = _target(conn, role, placeholder)
    if target is None:
        if createrole:
            return "create"
        raise PrivilegeMissing(
            f"database user {u} may not create the session store's role {q}: it lacks CREATEROLE. "
            f"Have the database team run `ALTER ROLE {u} CREATEROLE;`, or pre-create the role with "
            f"the password in the session store's Secret: `CREATE ROLE {q} LOGIN NOINHERIT "
            f"PASSWORD '<that password>';`. See {DOCS}")
    powers = [kw for (col, kw), on in zip(_POWERS, target) if on]
    if powers:
        raise PrivilegeMissing(
            f"role {q} holds {', '.join(powers)}; the session store refuses a role with admin power "
            f"and database user {u} may not remove it. Have the database team run "
            f"`ALTER ROLE {q} {' '.join('NO' + p for p in powers)};`. See {DOCS}")
    admin = version < 160000 or bool(_x(
        conn, f"SELECT pg_has_role(current_user, {placeholder}, 'MEMBER WITH ADMIN OPTION')", (role,)).fetchone()[0])
    if createrole and admin:
        return "alter"
    inherit, login = bool(target[5]), bool(target[6])
    if login and not inherit and logs_in is not None and logs_in():
        return "keep"
    need = " and ".join(([f"`ALTER ROLE {u} CREATEROLE;`"] if not createrole else [])
                        + ([f"`GRANT {q} TO {u} WITH ADMIN OPTION;`"] if not admin else []))
    why = "Postgres 16+ needs CREATEROLE and the ADMIN option on it" if version >= 160000 else "it lacks CREATEROLE"
    raise PrivilegeMissing(
        f"database user {u} may not change role {q} ({why}), "
        f"and the session store's password does not log in as a LOGIN NOINHERIT {q}. Have the "
        f"database team run {need}, or set the role to the Secret's password: "
        f"`ALTER ROLE {q} LOGIN NOINHERIT PASSWORD '<that password>';`. See {DOCS}")


def _target(conn, role: str, placeholder: str):
    return _x(conn, "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolinherit, "
                    f"rolcanlogin FROM pg_roles WHERE rolname = {placeholder}", (role,)).fetchone()


def init(conn, *, role: str, password: str, database: str, placeholder: str = "%s", logs_in=None) -> None:
    """Run with the instance's admin credential. Idempotent: re-running re-asserts the role's limits
    and password, creates what is missing and re-grants. Checks first that the credential may make
    or re-assert the role (``plan_role``) and writes nothing when it may not."""
    role = _check_role(role)
    if not _PASSWORD.fullmatch(password or ""):
        raise ValueError("the session store's database password must be 24-256 URL-safe characters")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,62}", database or ""):
        raise ValueError("not a database name")
    q = f'"{role}"'
    plan = plan_role(conn, role=role, placeholder=placeholder, logs_in=logs_in)
    if plan == "create":
        _x(conn, f"CREATE ROLE {q} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                 f"NOREPLICATION NOBYPASSRLS PASSWORD '{password}'")
    elif plan == "reassert":
        _x(conn, f"ALTER ROLE {q} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                 f"NOREPLICATION NOBYPASSRLS PASSWORD '{password}'")
    elif plan == "alter":
        _x(conn, f"ALTER ROLE {q} WITH LOGIN NOINHERIT PASSWORD '{password}'")
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
        raise SchemaNotReady(f"the session store's schema is not there yet ({type(exc).__name__}); the "
                             "migrations path (`session_schema init`) has not finished, and its log names "
                             "any database privilege it lacks") from None
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


def _logs_in() -> bool:
    try:
        _role_conn().close()
        return True
    except Exception:  # noqa: BLE001 — a wrong password, no LOGIN, no such role
        return False


def _pg_message(exc: Exception) -> str:
    """pg8000 carries the server's fields as a dict (``M`` the message, ``C`` the SQLSTATE)."""
    arg = exc.args[0] if exc.args else None
    if isinstance(arg, dict) and arg.get("M"):
        return f"{arg['M']} ({arg.get('C', '')})"
    return type(exc).__name__


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
            init(conn, role=role, password=password, database=os.environ.get("DB_NAME", "vexa"),
                 logs_in=lambda: _logs_in())
        except ValueError as exc:
            print(f"[session-schema] STOP: {exc}", flush=True)
            return 1
        except Exception as exc:  # noqa: BLE001 — a refusal from Postgres, in its own words
            print(f"[session-schema] STOP: Postgres refused: {_pg_message(exc)}. See {DOCS}", flush=True)
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
                    why = exc if isinstance(exc, SchemaNotReady) else (
                        f"cannot log in as the session store's role ({_pg_message(exc)}); the migrations "
                        "path (`session_schema init`) has not made it, and its log names any database "
                        "privilege it lacks")
                    print(f"[session-schema] not ready: {why}", flush=True)
                    return 1
                time.sleep(3)
    print("usage: python -m control_plane.session_schema init|wait", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
