"""invited_meetings reads the meetings database through pg8000 (BSD-3-Clause), never psycopg.

psycopg is LGPL-3.0-only and is not in this domain's lock, so the old `import psycopg` could only
ever fail. These tests pin the driver, how a libpq-style URL becomes pg8000's arguments (TLS
included), and the query and answer shape, against a fake pg8000 connection: no database needed.
"""
from __future__ import annotations

import json
import secrets
import ssl
from urllib.parse import quote

import pg8000.dbapi
import pytest

from control_plane import scaffolds


class _Cursor:
    def __init__(self, rows, seen):
        self.rows, self.seen = rows, seen

    def execute(self, sql, params):
        self.seen.append((sql, params))

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, rows, seen):
        self.rows, self.seen, self.closed = rows, seen, False

    def cursor(self):
        return _Cursor(self.rows, self.seen)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_pg(monkeypatch):
    calls, seen, conns = [], [], []

    def connect(**kwargs):
        calls.append(kwargs)
        conn = _Conn([(31, {"title": "DNA TSC", "scheduled_at": "Thu 14:00"}), (7, None)], seen)
        conns.append(conn)
        return conn

    monkeypatch.setattr(pg8000.dbapi, "connect", connect)
    return calls, seen, conns


def test_invited_meetings_reads_through_pg8000(monkeypatch, fake_pg):
    calls, seen, conns = fake_pg
    # A throwaway credential with a character that must be URL-decoded; built at run time so no
    # credential-shaped literal sits in the tree.
    credential = secrets.token_hex(4) + "@"
    monkeypatch.setenv("VEXA_MEETINGS_DB_URL", f"postgresql://reader:{quote(credential)}@db:5433/vexa?sslmode=disable")
    got = scaffolds.invited_meetings("  Ann@Example.com ")
    assert got == [{"meeting": "31", "title": "DNA TSC", "when": "Thu 14:00"},
                   {"meeting": "7", "title": "", "when": ""}]
    assert calls == [{"user": "reader", "password": credential, "host": "db", "port": 5433,
                      "database": "vexa", "timeout": 5}]
    sql, params = seen[0]
    assert "data->'attendees' @> %s::jsonb" in sql
    assert json.loads(params[0]) == [{"email": "ann@example.com"}]
    assert conns[0].closed


def test_unset_url_or_address_reads_nothing(monkeypatch, fake_pg):
    calls, _, _ = fake_pg
    monkeypatch.delenv("VEXA_MEETINGS_DB_URL", raising=False)
    assert scaffolds.invited_meetings("ann@example.com") == []
    monkeypatch.setenv("VEXA_MEETINGS_DB_URL", "postgresql://u@db/vexa")
    assert scaffolds.invited_meetings("  ") == []
    assert calls == []


def test_prefer_is_the_default_and_falls_back_to_plaintext(monkeypatch):
    calls = []

    def connect(**kwargs):
        calls.append(kwargs)
        if "ssl_context" in kwargs:
            raise pg8000.dbapi.InterfaceError("Server refuses SSL")
        return "plaintext"

    monkeypatch.setattr(pg8000.dbapi, "connect", connect)
    assert scaffolds.meetings_db_connect("postgres://u@db/vexa") == "plaintext"
    assert calls[0]["ssl_context"].verify_mode == ssl.CERT_NONE
    assert "ssl_context" not in calls[1]


@pytest.mark.parametrize("mode,verify,hostname", [
    ("require", ssl.CERT_NONE, False),
    ("verify-ca", ssl.CERT_REQUIRED, False),
    ("verify-full", ssl.CERT_REQUIRED, True),
])
def test_sslmode_becomes_an_ssl_context(monkeypatch, mode, verify, hostname):
    calls = []
    monkeypatch.setattr(pg8000.dbapi, "connect", lambda **kw: calls.append(kw) or "conn")
    scaffolds.meetings_db_connect(f"postgresql://u@db/vexa?sslmode={mode}")
    context = calls[0]["ssl_context"]
    assert context.verify_mode == verify and context.check_hostname is hostname


def test_a_refused_tls_under_require_is_not_retried(monkeypatch):
    def connect(**kwargs):
        raise pg8000.dbapi.InterfaceError("Server refuses SSL")

    monkeypatch.setattr(pg8000.dbapi, "connect", connect)
    with pytest.raises(pg8000.dbapi.InterfaceError):
        scaffolds.meetings_db_connect("postgresql://u@db/vexa?sslmode=require")


@pytest.mark.parametrize("url", ["mysql://u@db/vexa", "postgresql://u@db/vexa?sslmode=always"])
def test_a_url_this_reader_cannot_honour_is_refused(url):
    with pytest.raises(ValueError):
        scaffolds.meetings_db_connect(url)
