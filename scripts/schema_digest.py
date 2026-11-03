#!/usr/bin/env python3
"""Canonical JSON digest of the SQLAlchemy DB schema (tables + columns), from the model SOURCE —
no SQLAlchemy import, no database. stdlib ``ast`` only, so it runs on the CI runner with nothing
installed.

The authoritative schema is admin-api's models (it owns the tables; its ``ensure_schema`` creates
them) plus meeting-api's byte-faithful mirror, agent-api's session store DDL, the flows unit's
``schema.sql`` and each service-private SQLite whose DDL is a source constant (``SQLITE_DDL_FILES``:
the credential broker's ``metadata.sqlite``). ``gate:db-schema`` diffs this digest against
``schema.seal.json``; ANY table/column add, drop, or change trips the gate and requires a deliberate
``pnpm seal:schema`` re-seal — a human review step. So the DB schema cannot drift silently and a
stray migration/model edit is caught in CI (the "no unreviewed database changes" rule, enforced).

Emits ``{ "<model-file>": { "<table>": { "<column>": "<normalized Column(...) source>" } } }`` with
tables and columns sorted, so only a real structural change moves the bytes (comments/formatting/
column-reordering do not).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The schema-of-record + its mirror. Add a file here only when a NEW service owns real tables.
MODEL_FILES = [
    "core/identity/services/admin-api/src/admin_api/schema/models.py",
    "core/meetings/services/meeting-api/src/meeting_api/sessions/models.py",
]

# SQLite databases a service creates from DDL it carries as source constants, not models: the file,
# the `executescript` constant, and the additive-column constants — `(column, decl)` pairs for the
# first table named in `columns_table`, `(table, column, decl)` triples in the others. The constants
# are read with `ast.literal_eval` (never imported) and built in an in-memory SQLite, so what is
# sealed is what SQLite makes of the DDL: each column's declared type, NOT NULL, default and key.
SQLITE_DDL_FILES = [
    {"path": "core/agent/services/credential-broker/src/credential_broker/metadata.py",
     "script": "_SCHEMA", "columns": "_COLUMNS", "columns_table": "connections",
     "other_columns": "_OTHER_COLUMNS"},
]


# Services that own tables through plain DDL rather than SQLAlchemy models: the module-level `_DDL`
# tuple of `CREATE TABLE IF NOT EXISTS` statements is the schema of record (ADR-0048's session store).
DDL_FILES = [
    "core/agent/control_plane/session_store.py",
]

# Services whose schema of record is a `.sql` file of `CREATE TABLE IF NOT EXISTS` statements (the
# flows unit: flows-api, flows-worker and the mailbox share it), each with the module that carries its
# in-place changes. `CREATE TABLE IF NOT EXISTS` is a no-op on a database that already holds the
# table, so a column added to an existing table reaches a deployment only through an
# `ALTER TABLE <t> ADD COLUMN IF NOT EXISTS <c>` there — `migration_gaps` holds the two together.
SQL_FILES = {
    "core/flows/schema.sql": "core/flows/src/flows/migrations.py",
}


def _ddl_tables(rel: str) -> dict:
    """``{table: {column: "<normalized definition>"}}`` from a module's ``_DDL`` statements."""
    tree = ast.parse((ROOT / rel).read_text(), filename=rel)
    stmts: list = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_DDL" for t in node.targets):
            stmts = [e.value for e in getattr(node.value, "elts", []) if isinstance(e, ast.Constant)]
    return _create_tables(stmts)


def _sql_tables(rel: str) -> dict:
    """The same shape from a `.sql` file: its statements split on `;`, `--` comments dropped."""
    import re

    text = re.sub(r"--[^\n]*", "", (ROOT / rel).read_text())
    return _create_tables([s for s in text.split(";") if s.strip()])


def _create_tables(stmts: list) -> dict:
    import re

    tables: dict = {}
    for sql in stmts:
        m = re.match(r"\s*CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*)\)\s*$", sql, re.S)
        if not m:
            continue
        cols: dict = {}
        depth, part, parts = 0, "", []
        for ch in m.group(2):
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                parts.append(part)
                part = ""
            else:
                part += ch
        parts.append(part)
        for p in parts:
            p = " ".join(p.split())
            if not p:
                continue
            head = p.split(" ", 1)
            key = p if head[0].upper() in ("PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT", "CHECK") else head[0]
            cols[key] = p
        tables[m.group(1)] = dict(sorted(cols.items()))
    return dict(sorted(tables.items()))


def _is_column_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
    return name in ("Column", "mapped_column")


def _tablename(cls: ast.ClassDef):
    for stmt in cls.body:
        targets = (
            stmt.targets if isinstance(stmt, ast.Assign)
            else [stmt.target] if isinstance(stmt, ast.AnnAssign) else []
        )
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "__tablename__":
                if isinstance(stmt.value, ast.Constant):
                    return stmt.value.value
    return None


def _columns(cls: ast.ClassDef) -> dict:
    cols: dict = {}
    for stmt in cls.body:
        target = value = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            target, value = stmt.targets[0].id, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            target, value = stmt.target.id, stmt.value
        if target and _is_column_call(value):
            cols[target] = ast.unparse(value)  # normalized column definition (type + flags)
    return cols


def _constants(tree: ast.Module) -> dict:
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def sqlite_digest(spec: dict) -> dict:
    import sqlite3

    consts = _constants(ast.parse((ROOT / spec["path"]).read_text(), filename=spec["path"]))
    c = sqlite3.connect(":memory:")
    c.executescript(consts[spec["script"]])
    for name, decl in consts.get(spec.get("columns", ""), ()):
        c.execute(f"ALTER TABLE {spec['columns_table']} ADD COLUMN {name} {decl}")
    for table, name, decl in consts.get(spec.get("other_columns", ""), ()):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    tables: dict = {}
    names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in sorted(names):
        cols = {}
        for _cid, col, typ, notnull, default, pk in c.execute(f"PRAGMA table_info({t})"):
            cols[col] = " ".join(x for x in (typ, "NOT NULL" if notnull else "",
                                             f"DEFAULT {default}" if default is not None else "",
                                             "PRIMARY KEY" if pk else "") if x)
        tables[t] = dict(sorted(cols.items()))
    return tables


def digest() -> dict:
    out: dict = {}
    for spec in SQLITE_DDL_FILES:
        out[spec["path"]] = sqlite_digest(spec)
    for rel in MODEL_FILES:
        tree = ast.parse((ROOT / rel).read_text(), filename=rel)
        tables: dict = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                tn = _tablename(node)
                if tn:
                    tables[tn] = dict(sorted(_columns(node).items()))
        out[rel] = dict(sorted(tables.items()))
    for rel in DDL_FILES:
        out[rel] = _ddl_tables(rel)
    for rel in SQL_FILES:
        out[rel] = _sql_tables(rel)
    return out


def migration_gaps(sealed: dict) -> list:
    """Columns a `.sql` schema adds to a table the seal already holds with no
    `ALTER TABLE <table> ADD COLUMN IF NOT EXISTS <column>` in that schema's migrations module: a
    column a fresh install gets and an existing deployment never does."""
    import re

    gaps = []
    cur = digest()
    for rel, mig in SQL_FILES.items():
        text = (ROOT / mig).read_text() if (ROOT / mig).exists() else ""
        have = {(t.lower(), c.lower()) for t, c in
                re.findall(r"ALTER TABLE\s+(\w+)\s+ADD COLUMN\s+(?:IF NOT EXISTS\s+)?(\w+)", text, re.I)}
        for table, cols in cur.get(rel, {}).items():
            old = (sealed.get(rel) or {}).get(table)
            if old is None:
                continue                      # a new table: CREATE TABLE IF NOT EXISTS makes it everywhere
            for col in cols:
                if col in old or " " in col:  # sealed already, or a table constraint
                    continue
                if (table.lower(), col.lower()) not in have:
                    gaps.append(f"{rel}: {table}.{col} is new on an existing table and {mig} has no "
                                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col}")
    return gaps


if __name__ == "__main__":
    import sys

    if sys.argv[1:2] == ["--gaps"]:          # gate:db-schema: the sealed digest on stdin
        print(json.dumps(migration_gaps(json.load(sys.stdin))))
    else:
        print(json.dumps(digest(), indent=2, sort_keys=True))
