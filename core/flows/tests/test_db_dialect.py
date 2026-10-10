"""Postgres is the only production dialect (2026-09-03) — `flows.db_from_url` refuses anything
else by name, and `flows.SqliteDB` does not exist any more: the offline/storm double moved to
`tests/sqlite_double.py`, a TEST fixture, not a thing the product module exports or constructs
from a URL. See `flows/db.py`'s module docstring and biz's "the flows engine ships only the
database it runs on" ledger entry for the why.

OFFLINE, stdlib only — this file never touches a real database. `postgres_db`'s laziness (connects
and applies schema on first real use, not at construction) is what makes
`db_from_url("postgresql+pg8000://...")` safe to call here against an address nothing is
listening on: constructing the adapter must not raise, and it must not be lazy ONLY on some other
test's word for it — pinned directly."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flows                    # noqa: E402
from flows.db import DB, UnsupportedDialect, db_from_url  # noqa: E402


def test_sqlite_url_is_refused_by_the_product_module():
    """The double used to be one call away from any deployment (`db_from_url("sqlite://...")`
    silently built an in-memory database nobody had asked for in production). Now it names the
    scheme and refuses."""
    with pytest.raises(UnsupportedDialect) as e:
        db_from_url("sqlite://")
    assert "sqlite" in str(e.value)


@pytest.mark.parametrize("bad_url", ["mysql://x/y", "", "not-a-url-at-all"])
def test_every_non_postgres_scheme_is_refused_by_name(bad_url):
    with pytest.raises(UnsupportedDialect):
        db_from_url(bad_url)


def test_a_postgres_url_composes_lazily_against_an_unreachable_host():
    """UNREACHABLE on purpose (port 1 is never a service): constructing the adapter must not touch
    the network at all — that is the whole fix gate:health needed. Only a real `execute` would
    fail against this address, and nothing here calls one.

    `db_from_url` routes any `postgres`/`postgresql*` scheme to `postgres_db` — the refusal under
    test is about non-Postgres schemes, not about which driver a deployment names (the driver is
    always pg8000; see the tests below)."""
    db = db_from_url("postgresql+pg8000://u:p@127.0.0.1:1/flows")
    assert db.dialect == "postgres"


def test_sqlite_db_is_not_exported_from_the_product_module():
    """The double is a test fixture now (`tests/sqlite_double.py`) — it must not be reachable
    through `flows` at all, or anything that imports the package can still reach for it."""
    assert "SqliteDB" not in flows.__all__
    assert not hasattr(flows, "SqliteDB")


def test_the_db_protocol_and_postgres_factory_are_still_the_front_door():
    """The removal should be surgical: everything else `db.py` exported stays exported."""
    assert DB is not None
    assert hasattr(flows, "postgres_db")
    assert hasattr(flows, "db_from_url")


# ── the driver is this package's: pg8000, whatever the URL names ───────────────────────────────
# `pg8000_connection` is pure (no network), so the URL a deployment writes and the TLS it asks for
# are pinned here offline. A live Postgres round-trip of the same adapter is the flows image smoke.

from flows.db import SSL_MODES, pg8000_connection  # noqa: E402


@pytest.mark.parametrize("url", [
    "postgres://u:p@db:5432/flows",
    "postgresql://u:p@db:5432/flows",
    "postgresql+psycopg://u:p@db:5432/flows",
    "postgresql+pg8000://u:p@db:5432/flows",
])
def test_every_postgres_url_connects_through_pg8000(url):
    """flows ships one Postgres DBAPI (pg8000, BSD-3-Clause). A URL that still names another driver
    — an operator override written before the switch — reaches the same database through it
    rather than failing on an import the image cannot satisfy."""
    target, _, _ = pg8000_connection(url)
    assert target.drivername == "postgresql+pg8000"
    assert (target.username, target.password, target.host, target.port, target.database) == \
        ("u", "p", "db", 5432, "flows")


def test_sslmode_disable_and_allow_connect_in_plaintext():
    for mode in ("disable", "allow"):
        target, args, fallback = pg8000_connection(f"postgresql+pg8000://u:p@db/flows?sslmode={mode}")
        assert "ssl_context" not in args and not fallback
        assert "sslmode" not in target.query


def test_sslmode_prefer_is_the_default_and_falls_back_to_plaintext():
    """libpq's default. TLS is offered unverified; the engine retries in plaintext only when the
    server refuses TLS (`pg_engine`'s do_connect hook)."""
    import ssl
    for url in ("postgresql+pg8000://u:p@db/flows", "postgresql+pg8000://u:p@db/flows?sslmode=prefer"):
        _, args, fallback = pg8000_connection(url)
        assert fallback is True
        assert args["ssl_context"].verify_mode == ssl.CERT_NONE


def test_sslmode_require_encrypts_without_verifying():
    import ssl
    _, args, fallback = pg8000_connection("postgresql+pg8000://u:p@db/flows?sslmode=require")
    assert not fallback
    assert args["ssl_context"].verify_mode == ssl.CERT_NONE
    assert args["ssl_context"].check_hostname is False


def test_sslmode_verify_full_checks_the_chain_and_the_host_name():
    import ssl
    _, args, _ = pg8000_connection("postgresql+pg8000://u:p@db/flows?sslmode=verify-full")
    assert args["ssl_context"].verify_mode == ssl.CERT_REQUIRED
    assert args["ssl_context"].check_hostname is True


def test_sslmode_verify_ca_checks_the_chain_only():
    import ssl
    _, args, _ = pg8000_connection("postgresql+pg8000://u:p@db/flows?sslmode=verify-ca")
    assert args["ssl_context"].verify_mode == ssl.CERT_REQUIRED
    assert args["ssl_context"].check_hostname is False


def test_an_unknown_sslmode_is_refused_by_name():
    with pytest.raises(UnsupportedDialect) as e:
        pg8000_connection("postgresql+pg8000://u:p@db/flows?sslmode=always")
    assert "always" in str(e.value)
    assert set(SSL_MODES) == {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}


def test_connect_timeout_becomes_pg8000_timeout():
    target, args, _ = pg8000_connection("postgresql+pg8000://u:p@db/flows?sslmode=disable&connect_timeout=7")
    assert args == {"timeout": 7}
    assert dict(target.query) == {}
