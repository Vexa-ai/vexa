"""global_seed.py — `_global` IS SCAFFOLDED FROM A SEED, the way a desk is.

Founder, 2026-09-06: *"what we want is the global be scaffolded from seed"* — and the reason is the
sentence after it: *"the setup chat fills blanks and confirms, never composes structure."* A layer
whose shape depends on how one conversation went is a layer nobody can review, and the shape is not
the part a human has anything to say about. So the structure arrives, and the conversation writes
into it.

WHAT THIS IS NOT. It is not `shared/seeding.py`. That one materializes a DESK — a fresh git repo
from a template, once, and it refuses to touch a workspace that already has a `.git`. `_global` is
not created once and left: it exists on every running instance already, an admin has edited files
in it by hand, and a deploy that carried a new file to it must not undo any of that. So this module
is the SAME RULE `preset_library` established one directory down, applied to the whole tree:

  **ADDITIVE ONLY. Existence is the whole test.** A file that is on the store is the admin's — not
  compared, not merged, not overwritten, whatever the image thinks it should say. A file that is
  absent is copied in. Nothing else is a difference this module can see, and that is deliberate:
  content comparison would let a deploy quietly revert a human decision about how every agent in
  the deployment behaves.

  **BEST EFFORT.** A read-only `_global` (the host-path mirror, bound `:ro` by compose) is a real
  deployment shape and must not stop the service from booting. What could not be written is
  reported by the return value and the log; every reader of these files already falls back to the
  copy in the image.

TWO SOURCES, ONE TREE. `behavior/global/` is the seed proper — the layer files with their unwritten
regions, `POLICIES.md`, and the generated flow pages. `behavior/mail/` is copied to `_global/mail/`
rather than duplicated into the seed: that directory's README already says the repo copy is the
SOURCE and `/workspaces/_global/mail/<name>.md` is the live copy, *"same content, edited in both, or
the source lies"* — and until now the only thing that carried one to the other was a person
remembering to. `asks/` is `preset_library`'s and stays there; it had this rule first.

WHAT THE SEED MUST NOT DO IS LIFT THE GATE. Every layer file arrives carrying
`global_layer.UNWRITTEN_MARKER`, and `global_layer.state` counts a file that still carries it as not
yet written. Before the seed, "empty" was what told the gate a file had not been written; a seeded
placeholder is non-empty, and without the marker rule an instance could have accepted five files
nobody had filled in.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agent_api.global_seed")

#: Where the image keeps the organisation-tier seed. The agent-api Dockerfile COPYs
#: `behavior/global` to exactly this path, and `test_global_seed.py` re-derives that requirement
#: from this constant rather than restating it — the discipline `test_preset_library.py` applies to
#: its own COPY line, and `test_agent_api_image_manifest.py` applies to every by-path read (F221).
DEFAULT_IMAGE_SEED_DIR = "/app/behavior/global"

#: Where the image keeps the mail templates. Same COPY discipline, same test.
DEFAULT_IMAGE_MAIL_DIR = "/app/behavior/mail"

#: Operator overrides, for a deployment that mounts either from somewhere else. They mirror
#: `VEXA_PRESET_LIBRARY_DIR` and `VEXA_WORKSPACE_SEEDS_DIR`, which do the same job for the two
#: trees that already had one.
ENV_IMAGE_SEED_DIR = "VEXA_GLOBAL_SEED_DIR"
ENV_IMAGE_MAIL_DIR = "VEXA_GLOBAL_MAIL_DIR"

#: The live directory name each source lands in, relative to `_global`. Empty string = the root.
MAIL_DIRNAME = "mail"


def _dir(env_name: str, default: str) -> Optional[Path]:
    p = Path(os.environ.get(env_name, "") or default)
    return p if p.is_dir() else None


def image_seed_dir() -> Optional[Path]:
    """The image's copy of the organisation-tier seed, or None when this build carries none.

    None is a real answer and not an error: an older image predates the COPY line, and a test
    process is not an image at all. Every caller reads it as "there is nothing to copy in"."""
    return _dir(ENV_IMAGE_SEED_DIR, DEFAULT_IMAGE_SEED_DIR)


def image_mail_dir() -> Optional[Path]:
    """The image's copy of the mail templates, or None when this build carries none."""
    return _dir(ENV_IMAGE_MAIL_DIR, DEFAULT_IMAGE_MAIL_DIR)


def sources() -> list[tuple[Path, str]]:
    """(source directory, destination relative to `_global`) for every tree this module carries."""
    out: list[tuple[Path, str]] = []
    seed = image_seed_dir()
    if seed is not None:
        out.append((seed, ""))
    mail = image_mail_dir()
    if mail is not None:
        out.append((mail, MAIL_DIRNAME))
    return out


def _shippable(src: Path):
    """Every file under `src` worth copying, as (file, path relative to `src`).

    Recursive — unlike the preset library, which is flat by construction — because the seed has
    `flows/` under it and a page per flow is the point of that directory. Dotfiles and dot-
    directories are skipped at every level: `.git` in a checkout used as a seed source would
    otherwise be copied file by file into a repository that already has one."""
    for f in sorted(src.rglob("*")):
        rel = f.relative_to(src)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if f.is_file():
            yield f, rel.as_posix()


def summary(added: list[str]) -> str:
    """One sentence naming what `top_up` did — for the boot call site, which cannot use the logger.

    ⚠ `agent_api.*` INFO RECORDS GO NOWHERE IN THIS SERVICE: nothing configures the root logger, so
    `logging.lastResort` emits WARNING and above and drops the rest, and `_build_production_app`
    runs before uvicorn configures its own logging on top of that. `preset_library.summary` carries
    the same note and the same reason for printing rather than logging: a copy-in changed what every
    agent in this deployment reads, and a log line nobody can read is not a log line."""
    if not added:
        return "organisation tier: nothing to add — _global already has every file this image seeds"
    return (f"organisation tier: added {len(added)} file(s) to _global — {', '.join(added)}"
            " (from this image, not from the store; an admin may edit them there)")


def top_up(global_root: "str | Path",
           srcs: "list[tuple[Path, str]] | None" = None) -> list[str]:
    """Copy every seed file that `_global` does NOT already have. ADDITIVE ONLY. Never raises.

    Returns the destination paths actually added, relative to `_global`, in order — a deploy that
    changed what every agent in the company reads should say so in its own log, by name, rather
    than being inferred later from a directory listing."""
    root = Path(global_root)
    added: list[str] = []
    for src, prefix in (sources() if srcs is None else srcs):
        base = root / prefix if prefix else root
        for f, rel in _shippable(Path(src)):
            target = base / rel
            dest_name = f"{prefix}/{rel}" if prefix else rel
            # EXISTENCE IS THE WHOLE TEST — see the module docstring. Not mtime, not size, not
            # content: a file that is there is the admin's, and a deploy must not revert their edit.
            if target.exists():
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
            except OSError as e:
                logger.warning("global_seed: could not add %s to %s (%s) — the image's copy is "
                               "what a reader falls back on", dest_name, root, e)
                continue
            added.append(dest_name)
            logger.info("global_seed: added %s to %s — it is in this image and was not on the "
                        "store; an admin may edit it there", dest_name, root)
    if not added:
        logger.info("global_seed: nothing to add — %s already has every file this image seeds", root)
    return added
