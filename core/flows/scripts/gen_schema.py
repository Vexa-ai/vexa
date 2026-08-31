#!/usr/bin/env python3
"""Generate schema.sql FROM the declarative models (the SSOT) — house-style schema definition,
stdlib-pure engine preserved. Run after any model change; the drift test fails until you do."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sqlalchemy import create_mock_engine  # noqa: E402
from flows_schema import Base  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "schema.sql"
stmts: list[str] = []


def dump(sql, *a, **kw):
    s = str(sql.compile(dialect=engine.dialect)).strip()
    if s:
        stmts.append(s.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS")
                      .replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS") + ";")


engine = create_mock_engine("postgresql+psycopg://", dump)
Base.metadata.create_all(engine, checkfirst=False)

header = ("-- GENERATED from src/flows/schema_models.py (the SSOT) by scripts/gen_schema.py.\n"
          "-- DO NOT EDIT BY HAND — edit the models and regenerate. The engine and the sqlite\n"
          "-- test rig consume this file so they stay stdlib-pure; the drift gate keeps it honest.\n\n")
OUT.write_text(header + "\n\n".join(stmts) + "\n")
print(f"wrote {OUT} · {len(stmts)} statements")
