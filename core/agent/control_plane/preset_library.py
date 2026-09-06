"""preset_library.py — THE ASK LIBRARY: the preset files a scaffold's `opening` names, and the one
rule that stops a running instance's copy of them from falling behind the image that ships them.

⚠ THE FAILURE THIS EXISTS TO PREVENT, 2026-09-05, live, on the dogfood stack. A person signed in
with no link. The terminal's redeem route minted a `first-visit` scaffold — the touch nobody sent,
added to the catalogue on 2026-09-03 — and agent-api answered:

    preset asks/first-visit.md cannot be read here (FileNotFoundError) — the link would open nothing

They landed on an empty desk, which is the exact state F42 ruled against. Nothing was broken: the
preset existed in the repo at `behavior/asks/first-visit.md`, and the instance's `_global/asks/`
had been populated on 2026-09-02 — one day before the file was written. The library was not wrong,
it was BEHIND, and nothing in the product could tell the difference or close the gap.

── HOW IT CAME TO BE BEHIND ─────────────────────────────────────────────────────────────────────
Nothing seeded it. `_global/asks/` reached that volume by hand: its git history names the commits
`presets and mail templates from the line (<sha>)` — a human copying a checkout into a running
instance and committing it. The agent-api image did not carry `behavior/` at all, so no deploy
could ever top it up, and `deploy/dogfood/bin/blank-instance.sh` deliberately KEEPS `asks/` across
a wipe (the admin owns those files and may edit them). Every mechanism preserved the library; none
supplied it. A preset added to the repo therefore reached a running instance only when somebody
remembered to carry it across, and the way you found out they had not was a visitor meeting an
empty chat.

── THE RULE NOW ─────────────────────────────────────────────────────────────────────────────────
The image ships the library, and `_global/asks/` is where an admin OVERRIDES it. Two halves:

  1. **TOP-UP, ADDITIVELY.** At agent-api start and at global-ready, every preset the image carries
     that `_global/asks/` does not have is copied in. A file that is already there is NEVER
     touched — not compared, not merged, not overwritten. Its content is the admin's, and an
     admin edit changes how every agent in the deployment behaves; a top-up that could silently
     revert one would be a deploy quietly undoing a human decision. Absence is the whole test.

  2. **FALL BACK, ON ABSENCE ONLY.** `scaffolds.read_preset` looks in `_global/asks/` first and in
     the image's copy second, so a mint never fails on a library that is merely behind. The
     top-up is best-effort by construction — a read-only `_global` mirror is a legitimate
     deployment shape (compose binds it `:ro`; see `api._global_store`) — so the fallback is what
     actually holds when the write cannot happen. An EMPTY file in `_global` still raises: that is
     a present-but-broken admin file, and falling through it would mean ignoring the admin's own
     edit, which is the opposite of admin-owned.

The two are not redundant. The fallback alone would leave the library INVISIBLE — an admin cannot
edit a file they cannot see, and `_global/asks/` is the surface the product tells them to edit. The
top-up alone would leave every read-only deployment exactly where 2026-09-05 was.

WHAT DOES NOT CHANGE: a name that exists in NEITHER root still raises `ScaffoldError` at MINT,
where a step can still refuse to send, rather than at click, where a person meets an empty chat.
Only the definition of "does not exist" widens from one root to two.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger("agent_api.preset_library")

#: The library's directory name under the organisation tier — `_global/asks/`. `scaffolds.preset_path`
#: joins the same segment; they are pinned together by `test_preset_library.py`.
ASKS_DIRNAME = "asks"

#: Where the image keeps its own copy of `behavior/asks/`. The agent-api Dockerfile COPYs
#: `behavior/asks` to exactly this path, and `test_preset_library.py` re-derives that requirement
#: from this constant rather than restating it — the same discipline
#: `test_agent_api_image_manifest.py` applies to every by-path read (F221).
DEFAULT_IMAGE_ASKS_DIR = "/app/behavior/asks"

#: Operator override, for a deployment that mounts the library from somewhere else. Mirrors
#: `VEXA_WORKSPACE_SEEDS_DIR`, which does the same job for `behavior/workspaces`.
ENV_IMAGE_ASKS_DIR = "VEXA_PRESET_LIBRARY_DIR"


def image_asks_dir() -> Optional[Path]:
    """The image's own copy of the preset library, or None when this build carries none.

    None is a real answer, not an error: an older image predates the COPY line, and a test process
    is not an image at all. Every caller treats it as "there is nothing to fall back to", which is
    precisely today's behaviour."""
    p = Path(os.environ.get(ENV_IMAGE_ASKS_DIR, "") or DEFAULT_IMAGE_ASKS_DIR)
    return p if p.is_dir() else None


def _shippable(src: Path):
    """Every file in the image's library worth copying: top-level regular files, no dotfiles, no
    subdirectories. The library is flat by construction (`read_preset` resolves exactly one
    segment, `asks/<name>.md`), and `README.md` rides along on purpose — it is what tells an admin
    what these files are and that they may edit them."""
    for f in sorted(src.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            yield f


def summary(added: list[str]) -> str:
    """One sentence naming what `top_up` did — for the boot call site, which cannot use the logger.

    ⚠ `agent_api.*` INFO RECORDS GO NOWHERE IN THIS SERVICE. Nothing configures the root logger, so
    Python\'s `logging.lastResort` handler emits WARNING and above to stderr and drops everything
    below — which is why `workspace_routines`\' cron warnings appear in `docker logs` and no INFO
    line from any control-plane module ever has. `_build_production_app` runs before uvicorn
    configures its own logging on top of that, so a boot-time record is dropped twice over.

    The level is not the thing to change: adding presets is a normal, healthy deploy-time event, and
    raising it to WARNING to make it visible would cry wolf on every fresh instance and devalue a
    channel operators actually read. So the record stays INFO where it belongs — correct for tests
    and for any caller that has configured logging — and the ONE caller that must be seen regardless
    prints this. A top-up changed what every agent in this deployment reads; a log line nobody can
    read is not a log line."""
    if not added:
        return "preset library: nothing to add — _global/asks/ already has every preset this image ships"
    return (f"preset library: added {len(added)} preset(s) to _global/asks/ — {', '.join(added)}"
            " (in this image, not on the store; an admin may edit them there)")


def top_up(global_root: "str | Path", image_dir: "str | Path | None" = None) -> list[str]:
    """Copy every preset the image ships that `_global/asks/` does NOT already have. ADDITIVE ONLY.

    Returns the names actually added, in order, and logs ONE line per file — a deploy that changed
    what every agent in the company reads should say so in its own log, by name, rather than being
    inferred later from a directory listing.

    Never raises. A `_global` that is read-only here is a legitimate deployment shape and must not
    stop the service from booting (the same reason `global_layer.ensure_repo` is best-effort at the
    same call site); a library that could not be topped up is reported by the return value and the
    log, and `read_preset`'s fallback is what keeps the mint working meanwhile."""
    src = Path(image_dir) if image_dir is not None else image_asks_dir()
    if src is None or not Path(src).is_dir():
        logger.debug("preset_library: this build carries no preset library — nothing to top up")
        return []
    dst = Path(global_root) / ASKS_DIRNAME
    added: list[str] = []
    try:
        dst.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("preset_library: cannot create %s (%s) — the image's copy is what "
                       "read_preset will fall back on", dst, e)
        return []
    for f in _shippable(Path(src)):
        target = dst / f.name
        # EXISTENCE IS THE WHOLE TEST. Not mtime, not size, not content — a file that is there is
        # the admin's, and a deploy must not be able to revert their edit.
        if target.exists():
            continue
        try:
            shutil.copy2(f, target)
        except OSError as e:
            logger.warning("preset_library: could not add %s to %s (%s)", f.name, dst, e)
            continue
        added.append(f.name)
        logger.info("preset_library: added %s to %s — it is in this image and was not on the "
                    "store; an admin may edit it there", f.name, dst)
    if not added:
        logger.info("preset_library: nothing to add — %s already has every preset this image "
                    "ships", dst)
    return added
