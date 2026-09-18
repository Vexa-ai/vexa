"""Postgres is the only production dialect (2026-09-03) — `flows.db_from_url` refuses anything
else by name, and `flows.SqliteDB` does not exist any more: the offline/storm double moved to
`tests/sqlite_double.py`, a TEST fixture, not a thing the product module exports or constructs
from a URL. See `flows/db.py`'s module docstring and biz's "the flows engine ships only the
database it runs on" ledger entry for the why.

OFFLINE — this file never touches a real database. `postgres_db`'s laziness (connects
and applies schema on first real use, not at construction) is what makes
`db_from_url("postgresql+pg8000://...")` safe to call here against an address nothing is
listening on: constructing the adapter must not raise, and it must not be lazy ONLY on some other
test's word for it — pinned directly."""
from __future__ import annotations

import ssl
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flows                    # noqa: E402
from flows.db import DB, UnsupportedDialect, db_from_url, translate_pg8000_url  # noqa: E402


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

    `postgresql+pg8000://` selects the package's declared pure-Python driver. A bare
    `postgresql://` selects SQLAlchemy's default DBAPI, which is not installed; `postgres`
    is not a SQLAlchemy dialect alias. `db_from_url` itself routes any `postgres`/`postgresql*`
    scheme to `postgres_db` — the refusal under test is about non-Postgres schemes, not about
    which driver a deployment names."""
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


@pytest.mark.parametrize("mode", [None, "disable", "allow", "prefer", "require", "verify-ca", "verify-full"])
def test_pg8000_ssl_modes_preserve_the_operator_url(mode):
    base = "postgresql+pg8000://u:p@127.0.0.1:1/flows"
    query = "application_name=flow%20worker&tag=a&tag=b&empty="
    url = f"{base}?{query}" if mode is None else f"{base}?sslmode={mode}&{query}"

    clean_url, args = translate_pg8000_url(url)

    assert clean_url == f"{base}?{query}"
    if mode in (None, "allow", "prefer"):
        assert args == {}
    elif mode == "disable":
        assert args == {"ssl_context": False}
    else:
        assert set(args) == {"ssl_context"}
        context = args["ssl_context"]
        assert isinstance(context, ssl.SSLContext)
        assert context.check_hostname is (mode != "require")
        assert context.verify_mode == (ssl.CERT_NONE if mode == "require" else ssl.CERT_REQUIRED)


@pytest.mark.parametrize("mode", ["invalid", ""])
def test_pg8000_rejects_unknown_ssl_modes(mode):
    with pytest.raises(ValueError) as exc:
        translate_pg8000_url(f"postgresql+pg8000://u:p@127.0.0.1:1/flows?sslmode={mode}")
    assert repr(mode) in str(exc.value)


@pytest.mark.parametrize("url", [
    "postgresql+asyncpg://u:p@127.0.0.1:1/flows?sslmode=require",
    "postgresql+asyncpg://u:p@127.0.0.1:1/flows?sslmode=invalid",
    "sqlite://",
])
def test_other_drivers_keep_their_url_contract(url):
    assert translate_pg8000_url(url) == (url, {})


def test_pg8000_translation_without_query():
    url = "postgresql+pg8000://u:p@127.0.0.1:1/flows"
    assert translate_pg8000_url(url) == (url, {})


def test_pg8000_require_engine_resolves_without_connecting():
    from sqlalchemy import create_engine

    url, args = translate_pg8000_url("postgresql+pg8000://u:p@127.0.0.1:1/flows?sslmode=require")
    engine = create_engine(url, connect_args=args)
    assert engine.dialect.driver == "pg8000"


def test_postgres_factory_passes_translated_ssl_settings(monkeypatch):
    import sqlalchemy

    observed = {}

    def capture_engine(url, **kwargs):
        observed.update(url=url, **kwargs)

    monkeypatch.setattr(sqlalchemy, "create_engine", capture_engine)
    db = db_from_url("postgresql+pg8000://u:p@127.0.0.1:1/flows?sslmode=disable")
    assert db.dialect == "postgres"
    assert observed == {
        "url": "postgresql+pg8000://u:p@127.0.0.1:1/flows",
        "pool_pre_ping": True,
        "connect_args": {"ssl_context": False},
    }
