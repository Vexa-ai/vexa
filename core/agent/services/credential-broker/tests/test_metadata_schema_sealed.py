"""metadata.sqlite's schema is sealed like every other database schema in the repository.

`scripts/schema_digest.py` reads `metadata.py`'s DDL (the tables and the additive columns), builds it
in an in-memory SQLite, and records every column in `schema.seal.json`; `gate:db-schema` fails on any
drift until a deliberate `pnpm seal:schema`. This test holds the seal to what `open_schema` actually
creates, so the digest cannot describe a schema the broker does not build.
"""
import json
import sqlite3
from pathlib import Path

from credential_broker import metadata

REPO = Path(__file__).resolve().parents[5]
KEY = "core/agent/services/credential-broker/src/credential_broker/metadata.py"


def _built() -> dict:
    c = sqlite3.connect(":memory:")
    metadata.open_schema(c)
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {t: sorted(r[1] for r in c.execute(f"PRAGMA table_info({t})")) for t in sorted(tables)}


def test_the_seal_holds_every_table_and_column_open_schema_creates():
    seal = json.loads((REPO / "schema.seal.json").read_text())
    assert KEY in seal, "metadata.sqlite's DDL is not in schema.seal.json — run `pnpm seal:schema`"
    assert {t: sorted(cols) for t, cols in seal[KEY].items()} == _built()
