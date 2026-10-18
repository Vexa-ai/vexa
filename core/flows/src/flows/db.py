"""The DB seam. Engine speaks textual SQL through this thin protocol (the house pattern:
SQLAlchemy is the CONNECTION layer, imported lazily so an import-only caller — a liveness probe,
gate:health — never needs it to be reachable; statements stay explicit SQL).

Production is Postgres, and ONLY Postgres (2026-09-03): claiming uses `FOR UPDATE SKIP LOCKED`,
which is a Postgres-only clause, so there never was a second dialect this engine could actually
run on. `postgres_db()` is lazy in two senses — the SQLAlchemy engine connects on first use, and
the schema is applied on first real `execute`/`executescript` rather than at construction — so
composing the app object (import, `/health`) never touches the network. `db_from_url` refuses
any URL that does not name Postgres.

The offline/storm double that used to live here — stdlib sqlite3, serialized writes, one process
per file lock — moved to `core/flows/tests/sqlite_double.py` on 2026-09-03: it is a TEST fixture
(fixtures.py, the storm, the witness harnesses), not a deployment target, and a test double
exported from the product package (`flows.SqliteDB`) was importable by anything that imported
`flows` at all. Tests construct it directly from the test tree; this module never sees it."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Protocol

SCHEMA = (Path(__file__).resolve().parents[2] / "schema.sql").read_text()


class DB(Protocol):
    dialect: str
    def execute(self, sql: str, params: dict | None = None) -> list[tuple]: ...
    def executescript(self, sql: str) -> None: ...


class UnsupportedDialect(ValueError):
    """A `db_from_url` URL naming a scheme this engine does not run in production, or a Postgres
    URL asking for something its driver cannot honour (an `sslmode` libpq does not define).

    Declared HERE rather than reusing `flows_config.ConfigError` on purpose: this module lives in
    `src/flows/`, the engine core that `core/flows/scripts/check-isolation.js` (gate:isolation)
    holds to stdlib-only imports at module scope, so it cannot import `flows_config` — even for
    an exception class — without becoming the violation it would be reporting. Same intent as
    `ConfigError` (refuse loudly, name what was wrong), declared where the isolation law allows.
    """


#: libpq's `sslmode` values, in libpq's order (least to most protection).
SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")


def pg8000_connection(url: str):
    """The SQLAlchemy URL and connect arguments that reach the database `url` names through pg8000.

    The URL names the database; the driver is this package's. flows ships pg8000 (BSD-3-Clause)
    and no other Postgres DBAPI, so `postgres://`, `postgresql://` and any `postgresql+<driver>://`
    all connect through it. pg8000 takes TLS as an `ssl_context`, not as libpq's query parameters,
    so `sslmode` and `sslrootcert` are read here and turned into one (`connect_timeout` becomes
    pg8000's `timeout`):

      disable, allow   no TLS
      prefer (default) TLS when the server offers it, unverified; plaintext when it refuses
      require          TLS, unverified; verified like verify-ca when sslrootcert names a CA file
      verify-ca        TLS, certificate chain checked against the CA file sslrootcert names
                       (required: libpq refuses verify-ca with no root certificate)
      verify-full      the chain checked against sslrootcert, and the server's host name checked
                       against its certificate. With no sslrootcert, or sslrootcert=system, the
                       system store is the trust root, as libpq's sslrootcert=system

    sslrootcert=system is accepted with verify-full only, as libpq does: a public CA is trusted
    only together with the host name check.

    Returns `(url, connect_args, fallback_to_plaintext)`. Pure: nothing here touches the network.
    """
    import ssl
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    query = {k: (v[-1] if isinstance(v, tuple) else v) for k, v in parsed.query.items()}
    mode = query.pop("sslmode", None) or "prefer"
    rootcert = query.pop("sslrootcert", None)
    if mode not in SSL_MODES:
        raise UnsupportedDialect(f"sslmode={mode!r} is not one of {', '.join(SSL_MODES)}")
    connect_args: dict[str, Any] = {}
    timeout = query.pop("connect_timeout", None)
    if timeout:
        connect_args["timeout"] = int(timeout)
    if rootcert == "system" and mode != "verify-full":
        raise UnsupportedDialect(f"sslrootcert=system needs sslmode=verify-full, not {mode!r}: the system "
                                 "store trusts every public CA, so the host name must be checked too")
    if mode == "verify-ca" and not rootcert:
        raise UnsupportedDialect("sslmode=verify-ca needs sslrootcert naming the CA file to check the "
                                 "server against (libpq refuses verify-ca without one); use verify-full "
                                 "to trust the system store with the host name checked")
    if mode in ("verify-ca", "verify-full") or (mode == "require" and rootcert):
        cafile = None if rootcert in (None, "", "system") else rootcert
        context = ssl.create_default_context(cafile=cafile)
        context.check_hostname = mode == "verify-full"
        connect_args["ssl_context"] = context
    elif mode in ("prefer", "require"):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connect_args["ssl_context"] = context
    return (parsed.set(drivername="postgresql+pg8000", query=query), connect_args, mode == "prefer")


def pg_engine(url: str, **engine_kwargs):  # pragma: no cover — exercised against a live Postgres
    """A SQLAlchemy engine on the database `url` names, connected through pg8000 (see
    `pg8000_connection` for how the URL and its TLS parameters are read). Lazy like any
    `create_engine`: no connection until first use."""
    from sqlalchemy import create_engine, event

    target, connect_args, fallback = pg8000_connection(url)
    engine = create_engine(target, connect_args=connect_args, **engine_kwargs)
    if fallback:
        @event.listens_for(engine, "do_connect")
        def _plaintext_when_tls_is_refused(dialect, _record, cargs, cparams):
            try:
                return dialect.dbapi.connect(*cargs, **cparams)
            except dialect.dbapi.InterfaceError as exc:
                if "refuses SSL" not in str(exc):
                    raise
                return dialect.dbapi.connect(*cargs, **{k: v for k, v in cparams.items() if k != "ssl_context"})
    return engine


def postgres_db(url: str):  # pragma: no cover — production composition; lazy import by design
    """The Postgres adapter. `create_engine` itself is already lazy (no connection until first
    query); what this function adds is making SCHEMA APPLICATION lazy too, behind a one-shot
    guard on first `execute`/`executescript` — it used to run at construction, which meant
    `flows-api`'s module-scope `db = db_from_url(db_url())` could not even be IMPORTED against a
    Postgres that was not reachable yet, and `/health` is answered by the in-memory step registry
    and never calls either method — so a flows-api that cannot reach Postgres still answers
    liveness. The first caller that actually touches the DB (a real request, the worker's first
    claim) pays for the schema application once; every caller after it is free."""
    from sqlalchemy import text

    engine = pg_engine(url, pool_pre_ping=True)

    class _Pg:
        dialect = "postgres"

        def __init__(self) -> None:
            self._schema_applied = False

        def _ensure_schema(self) -> None:
            if self._schema_applied:
                return
            self._schema_applied = True
            self._run_script(SCHEMA)

        def _run_script(self, sql: str) -> None:
            # strip comment lines BEFORE splitting on ';' — comments legitimately contain
            # semicolons and a naive split feeds Postgres mid-sentence fragments
            clean = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
            with engine.begin() as c:
                for stmt in clean.split(";"):
                    if stmt.strip():
                        c.execute(text(stmt))

        def execute(self, sql: str, params: dict | None = None) -> list[tuple]:
            self._ensure_schema()
            with engine.begin() as c:
                res = c.execute(text(sql), params or {})
                return [tuple(r) for r in res] if res.returns_rows else []

        def executescript(self, sql: str) -> None:
            self._ensure_schema()
            self._run_script(sql)

    return _Pg()


def db_from_url(url: str):
    """The Postgres adapter the URL names — the ONE seam every composition site calls
    (`db_from_url(db_url())`), kept as a function rather than each site calling `postgres_db`
    directly so the choice of adapter stays a property of the CONFIGURATION, not the caller.

    Postgres is the only production dialect: a `postgres://`/`postgresql://` URL (its
    `+driver` variants included, e.g. `postgresql+pg8000://`) gets `postgres_db`; anything else
    is refused by name. There is no second branch — the offline/storm dialect (`SqliteDB`) is a
    test double now (`core/flows/tests/sqlite_double.py`), and a test constructs it directly
    rather than routing a `sqlite://` URL through here."""
    scheme = url.split("://", 1)[0] if "://" in url else url
    if scheme == "postgres" or scheme.startswith("postgresql"):
        return postgres_db(url)
    raise UnsupportedDialect(
        f"VEXA_FLOWS_DB_URL names the {scheme!r} scheme — this engine runs on Postgres only "
        "('postgres://' or 'postgresql[+driver]://'). The offline/storm dialect (SqliteDB) is "
        "a test double now (core/flows/tests/sqlite_double.py); construct it directly there, "
        "never through this function.")


def dumps(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), sort_keys=True)


def loads(v: Optional[str]) -> dict:
    return json.loads(v) if v else {}
