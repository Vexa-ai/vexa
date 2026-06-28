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

# A folder may serve as a workspace seed only if it carries these — the minimum a workspace needs to be
# governable. CLAUDE.md is the auto-loaded root memory/contract every turn reads; without it the
# workspace has no governance root.
REQUIRED_SEED_PATHS = ("CLAUDE.md",)

# The seeds ROOT is a registry of named templates — one validated seed per subdir
# (``workspace-seeds/<name>/``). Today only ``default`` ships; adding a flavor (e.g. a business
# workspace) is a new subdir, no code change. ``resolve_seed_dir`` is the single selection seam.
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


def seed_workspace(ws: Path, seed_dir: "Path | None") -> Path:
    """Initialize `ws` as a git repo seeded from `seed_dir` (the validated template). Idempotent: a
    workspace that already has `.git` is returned untouched. Copies the template tree, then `git init`
    + a seed commit so a governed turn has a HEAD to commit onto."""
    if (ws / ".git").exists():
        return ws
    ws.mkdir(parents=True, exist_ok=True)
    if seed_dir and seed_dir.exists():
        for item in seed_dir.iterdir():
            dst = ws / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
    for args in (("init", "-q"), ("config", "user.email", "agent@vexa"), ("config", "user.name", "vexa-agent")):
        subprocess.run(["git", *args], cwd=str(ws), check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "-A"], cwd=str(ws), check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed", "--allow-empty"], cwd=str(ws), check=True, capture_output=True, text=True)
    return ws
