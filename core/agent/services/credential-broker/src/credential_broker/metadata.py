"""The broker's state directory: the metadata database's schema, its additive migration, and the
broker-private HMAC key.

`metadata.sqlite` holds connection metadata, the audit trail, pending OAuth states, draft
idempotency rows, the assertion nonces the replay check remembers, and `broker_meta` (one-time
upgrade steps the broker has completed). `open_schema` creates the
tables and adds any column a database from an earlier release lacks; it never drops or rewrites one.
"""
from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (id TEXT PRIMARY KEY, actor TEXT, session TEXT, label TEXT, status TEXT, version INTEGER DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, actor TEXT, session TEXT, operation_id TEXT, connection TEXT, action TEXT, outcome TEXT, vault_request_id TEXT, version INTEGER, receipt TEXT);
CREATE TABLE IF NOT EXISTS oauth_states (state_hash TEXT PRIMARY KEY, actor TEXT, session TEXT, connection TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS draft_requests (id TEXT PRIMARY KEY, actor TEXT, connection TEXT, fingerprint TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS assertion_nonces (nonce TEXT PRIMARY KEY, expires REAL);
CREATE TABLE IF NOT EXISTS broker_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
# Columns the 0.13.2 development harness added one release at a time. Kept, with the same names and
# defaults, so a deployment that ran the harness opens its existing metadata.sqlite unchanged.
_COLUMNS = (
    ("provider", "TEXT NOT NULL DEFAULT 'custom_secret'"),
    ("setup_request", "TEXT NOT NULL DEFAULT ''"),
    ("account", "TEXT NOT NULL DEFAULT ''"),
    ("setup_spec", "TEXT NOT NULL DEFAULT ''"),
    ("oauth_app_version", "INTEGER NOT NULL DEFAULT 0"),
    ("approved_host", "TEXT NOT NULL DEFAULT ''"),
)


def open_schema(c: sqlite3.Connection) -> None:
    """Create every table, then add each column an older database lacks (inside the caller's
    transaction)."""
    c.executescript(_SCHEMA)
    present = {r[1] for r in c.execute("PRAGMA table_info(connections)")}
    for name, decl in _COLUMNS:
        if name not in present:
            c.execute(f"ALTER TABLE connections ADD COLUMN {name} {decl}")


def state_key(path: Path) -> bytes:
    """A broker-private HMAC key, generated once into the state directory (0600).

    It derives PKCE verifiers and the draft idempotency fingerprint. It is deliberately NOT the
    store key and not a store token, so rotating either never invalidates a pending consent, and
    deleting this file costs only the consents in flight (L5)."""
    try:
        key = path.read_bytes()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_bytes(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(key)
    tmp.chmod(0o600)
    tmp.replace(path)
    return key
