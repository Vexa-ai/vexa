"""governance.py — the DORMANT hard-enforcement hook for workspace entity writes.

The workspace is currently a FREE ZONE: governance is PROMPT-ONLY (workspace conventions guide the
agent) and ``llm.ports.run_harness_turn`` commits whatever the turn wrote. These helpers are the
hard-enforcement path kept warm: re-validate every changed ``kg/entities/**.md`` against
``workspace.v1`` and revert non-conformant writes before commit. No production caller today —
re-wire them into the turn path to restore hard enforcement (P8).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

import contracts
from shared.adapters import _git, parse_entity

# index.md / log.md are OKF v0.1 reserved files — listings/history WITHOUT frontmatter, not entities.
_ENTITY_RE = re.compile(r"^kg/entities/(?!(?:.*/)?(?:index|log)\.md$).+\.md$")


def changed_entity_files(work_dir: str | Path) -> list[str]:
    """The `kg/entities/**.md` paths changed in the working tree vs HEAD (porcelain).

    ``--untracked-files=all`` is REQUIRED: without it git collapses a fully-untracked directory to a
    single ``?? kg/`` line, hiding the individual new entity files the model just wrote.
    """
    out = _git(Path(work_dir), "status", "--porcelain", "--untracked-files=all")
    paths: list[str] = []
    for line in out.splitlines():
        p = line[3:].strip() if len(line) > 3 else ""
        if " -> " in p:  # rename: XY orig -> new
            p = p.split(" -> ", 1)[1]
        p = p.strip().strip('"')
        if _ENTITY_RE.match(p):
            paths.append(p)
    return paths


def revalidate_entities(work_dir: str | Path, paths: Optional[Iterable[str]] = None) -> list[tuple[str, str]]:
    """Validate each changed entity's frontmatter against workspace.v1. Returns (path, error) violations."""
    work = Path(work_dir)
    targets = list(paths) if paths is not None else changed_entity_files(work)
    violations: list[tuple[str, str]] = []
    for rel in targets:
        f = work / rel
        if not f.exists():  # deleted — nothing to validate
            continue
        frontmatter, _body = parse_entity(f.read_text())
        try:
            contracts.validate_entity_frontmatter(frontmatter)
        except Exception as e:  # jsonschema ValidationError — surface the path + reason (P18)
            violations.append((rel, str(e).splitlines()[0]))
    return violations
