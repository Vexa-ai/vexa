"""seeding.py — materialize a per-subject workspace from a VALIDATED template, plus the 'passes checks'
gate for any candidate seed folder.

The seed source can be ANY folder that clears `validate_seed` (carries a CLAUDE.md governance root); the
rest of the template (agents/, views/, skills/) is optional and domain-specific. `seed_workspace` is the
single seeding primitive — copy the template tree, then `git init` + a seed commit — idempotent on an
existing repo. (Extracted from the retired MVP0 chat_runner; wired into the worker's first-dispatch
seeding in the seed-consolidation phase.)
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from shared.gitenv import scrubbed_git_env

# A folder may serve as a workspace seed only if it carries these — the minimum a workspace needs to be
# governable. CLAUDE.md is the auto-loaded root memory/contract every turn reads; without it the
# workspace has no governance root.
REQUIRED_SEED_PATHS = ("CLAUDE.md",)

# ONE seed (``workspace-seeds/default/``): a light scaffold whose README is the dashboard the user
# first sees, whose kg/ ships empty (entity SHAPES live in kg/templates/), and whose flows/ carries
# the scaffolding conversations — the flow read at setup is chosen by WHY the workspace is being
# created (personal | shared | global), not by which tree was copied. Flavors were removed
# deliberately: variation lives in flows and content written after seeding, never in parallel
# template trees that drift. ``VEXA_WORKSPACE_SEED_DIR`` remains for tests/special deploys.
DEFAULT_TEMPLATE = "default"
DEFAULT_SEEDS_ROOT = "/app/workspace-seeds"


def resolve_seed_dir(template: "str | None" = None, *, seeds_root: "str | Path | None" = None) -> Path:
    """Resolve which seed template a workspace is materialized from. Precedence:

    1. ``VEXA_WORKSPACE_SEED_DIR`` — an explicit, already-resolved seed dir (tests / special deploys);
       overrides selection entirely.
    2. ``<seeds_root>/<template>`` — pick a named template out of the registry root. ``seeds_root``
       falls back to ``VEXA_WORKSPACE_SEEDS_DIR`` then ``/app/workspace-seeds``; ``template`` falls back
       to ``default``.
    """
    explicit = os.environ.get("VEXA_WORKSPACE_SEED_DIR")
    if explicit:
        return Path(explicit)
    root = Path(seeds_root or os.environ.get("VEXA_WORKSPACE_SEEDS_DIR", DEFAULT_SEEDS_ROOT))
    return root / (template or DEFAULT_TEMPLATE)


def list_templates(seeds_root: "str | Path | None" = None) -> list[str]:
    """The named templates available in the registry root (each a valid seed subdir)."""
    root = Path(seeds_root or os.environ.get("VEXA_WORKSPACE_SEEDS_DIR", DEFAULT_SEEDS_ROOT))
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and not validate_seed(d))


def validate_seed(seed: Path) -> list[str]:
    """Return the problems that disqualify `seed` as a workspace template; empty list == valid.
    The 'passes checks' gate: point the seed at any folder, and it's accepted iff this is empty."""
    if not seed.exists() or not seed.is_dir():
        return [f"seed path is not a directory: {seed}"]
    return [f"missing required seed file: {rel}"
            for rel in REQUIRED_SEED_PATHS if not (seed / rel).is_file()]


# ── what a NEW DESK does NOT start with ──────────────────────────────────────────────────────────
# ⚠ 2026-09-02. A new user signed in and the right panel rendered his desk's README as a page:
#     "# (unset) — this workspace has not been set up yet … Purpose (unset) … Objective (unset)"
# That file is a TEMPLATE. It shipped in the seed, was copied into every desk as ordinary content,
# and the panel — correctly, by its own rules — rendered the first document it found. The founder
# saw a form he had never filled in, presented as his workspace. "not happy about that."
#
# A desk starts EMPTY of pages. The agent writes the README when it has something to put in it (the
# company-setup chat did exactly that for the admin's desk, and it read well because it was written,
# not filled in). Index pages are the same shape of defect one level down: `kg/index.md` and
# `kg/entities/*/index.md` are scaffolding for a graph with no entities in it yet.
#
# MACHINERY IS NOT CONTENT and stays: CLAUDE.md (the agent's conventions for this repo), flows/,
# skills/, routines/, views/, and kg/templates/ — the agent READS those; a person never opens them
# as a page. Dropping them would change how every desk behaves, which is a bigger change than the
# defect asks for.
_SEED_PAGES_NOT_COPIED = frozenset({
    "README.md",                     # the "(unset)" page the founder was shown
    "kg/index.md",
    "kg/entities/index.md",
    "kg/entities/person/index.md",
    "kg/entities/company/index.md",
    "kg/entities/meeting/index.md",
})


def _seed_pairs(seed_dir: Path):
    """(source, workspace-relative path) for every seed file that should actually be copied."""
    for src in sorted(seed_dir.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(seed_dir).as_posix()
        if rel in _SEED_PAGES_NOT_COPIED:
            continue
        yield src, rel


def seed_workspace(ws: Path, seed_dir: "Path | None") -> Path:
    """Initialize `ws` as a git repo seeded from `seed_dir` (the validated template). Idempotent: a
    workspace that already has `.git` is returned untouched. Copies the template tree, then `git init`
    + a seed commit so a governed turn has a HEAD to commit onto."""
    if (ws / ".git").exists():
        return ws
    ws.mkdir(parents=True, exist_ok=True)
    if seed_dir and seed_dir.exists():
        # File-by-file rather than copytree, so the page exclusions above are actually applied —
        # a directory copy cannot skip one document inside a tree it is copying wholesale.
        for src, rel in _seed_pairs(seed_dir):
            dst = ws / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        # `kg/entities/` must EXIST and be empty: the agent writes entities into it, and a missing
        # directory is a different failure from an empty one.
        (ws / "kg" / "entities").mkdir(parents=True, exist_ok=True)
    # scrubbed_git_env: a hook-exported GIT_DIR would otherwise re-point init/add/commit at the
    # HOOK's repo (with `ws` as its work tree) and rewrite that repo's branch — see shared/gitenv.py.
    env = scrubbed_git_env()
    for args in (("init", "-q"), ("config", "user.email", "agent@vexa"), ("config", "user.name", "vexa-agent")):
        subprocess.run(["git", *args], cwd=str(ws), check=True, capture_output=True, text=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=str(ws), check=True, capture_output=True, text=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "seed", "--allow-empty"], cwd=str(ws), check=True, capture_output=True, text=True, env=env)
    return ws
