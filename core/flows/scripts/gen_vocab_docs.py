#!/usr/bin/env python3
"""Generate the step-vocabulary docs page FROM the registry docstrings — the contract lives in
the code, the page is derived; run after step changes (make vocab-docs)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from flows import Registry
from flows_defs import production
from sqlite_double import SqliteDB

reg = Registry()
production.build(reg, SqliteDB())
out = Path(__file__).resolve().parents[3] / "docs" / "docs" / "flows" / "vocabulary.mdx"
lines = ['---', 'title: "Step vocabulary"',
         'description: "The deployed capabilities flows compose — generated from the step '
         'docstrings in the image; every name here is submittable, nothing here is folklore."',
         '---', '',
         'A flow is a list of these names. This page is **generated from the registry** '
         '(`make vocab-docs`) — the docstring in the code is the contract, and `GET /flows` '
         'serves the same text at runtime.', '']
for name in sorted(reg.steps):
    doc = " ".join((reg.steps[name].__doc__ or "⚠ undocumented — fix the docstring").split())
    lines.append(f"### `{name}`\n\n{doc}\n")
out.write_text("\n".join(lines))
undocumented = [n for n in reg.steps if not reg.steps[n].__doc__]
print(f"wrote {out.name} · {len(reg.steps)} steps · undocumented: {undocumented or 'none'}")
