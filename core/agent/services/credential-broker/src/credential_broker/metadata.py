"""The broker's state directory: the metadata database's schema, its additive migration, and the
broker-private HMAC key.

`metadata.sqlite` holds connection metadata, the audit trail, pending OAuth states, draft
idempotency rows (drafts and calendar changes), the drafts this broker created, each person's
narrower connection-policy setting, the grants each person gave flows' sensors to read one
calendar connection (`sensor_grants`), the assertion nonces the replay check remembers, and `broker_meta` (one-time
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
CREATE TABLE IF NOT EXISTS drafts (draft_id TEXT PRIMARY KEY, actor TEXT NOT NULL, connection TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS preferences (actor TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sensor_grants (id TEXT PRIMARY KEY, actor TEXT NOT NULL, connection TEXT NOT NULL, scope TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, revoked REAL NOT NULL DEFAULT 0, issued_via TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS sensor_grants_by_person ON sensor_grants(actor, connection);
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
    # The optional permissions the stored grant carries, space-separated ("drafts", "calendar_write").
    ("grants", "TEXT NOT NULL DEFAULT ''"),
    # When an unfinished setup was last asked for (setup, request, prepare); 0 means `created`.
    ("touched", "REAL NOT NULL DEFAULT 0"),
)
#: Columns other tables gained after their first release.
_OTHER_COLUMNS = (
    # The additional permission a pending consent asked for ("calendar_write"), checked at callback.
    ("oauth_states", "grant_name", "TEXT NOT NULL DEFAULT ''"),
)


def open_schema(c: sqlite3.Connection) -> None:
    """Create every table, then add each column an older database lacks (inside the caller's
    transaction)."""
    c.executescript(_SCHEMA)
    present = {r[1] for r in c.execute("PRAGMA table_info(connections)")}
    for name, decl in _COLUMNS:
        if name not in present:
            c.execute(f"ALTER TABLE connections ADD COLUMN {name} {decl}")
    for table, name, decl in _OTHER_COLUMNS:
        if name not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    # Drafts created before the drafts table existed are this broker's too: their ids are in the
    # idempotency rows' results. Recorded once, so updating one of them is not refused.
    c.execute("INSERT OR IGNORE INTO drafts(draft_id, actor, connection, created)"
              " SELECT json_extract(result, '$.draft_id'), actor, connection, 0 FROM draft_requests"
              " WHERE result != '' AND json_valid(result) AND json_extract(result, '$.draft_id') IS NOT NULL")


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
