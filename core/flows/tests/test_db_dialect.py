"""Postgres is the only production dialect (2026-09-03) — `flows.db_from_url` refuses anything
else by name, and `flows.SqliteDB` does not exist any more: the offline/storm double moved to
`tests/sqlite_double.py`, a TEST fixture, not a thing the product module exports or constructs
from a URL. See `flows/db.py`'s module docstring and biz's "the flows engine ships only the
database it runs on" ledger entry for the why.

OFFLINE, stdlib only — this file never touches a real database. `postgres_db`'s laziness (connects
and applies schema on first real use, not at construction) is what makes
`db_from_url("postgresql+psycopg://...")` safe to call here against an address nothing is
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

    `postgresql+psycopg://` specifically (not bare `postgres://`/`postgresql://`): psycopg
    (v3, binary) is this package's only declared DB driver (pyproject.toml) — SQLAlchemy defaults
    the driver-less schemes to psycopg2, which is not installed, and no longer recognises
    `postgres` as a dialect alias at all. `db_from_url` itself routes any `postgres`/`postgresql*`
    scheme to `postgres_db` — the refusal under test is about non-Postgres schemes, not about
    which driver a deployment names."""
    db = db_from_url("postgresql+psycopg://u:p@127.0.0.1:1/flows")
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
