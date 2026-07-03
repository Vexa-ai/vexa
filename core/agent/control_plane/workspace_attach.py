"""workspace_attach.py — attach a CUSTOM external git repo as a subject's workspace, swappable.

The active workspace a turn mounts is always ``<root>/<subject>``. A subject may *attach* their own
git repo (``workspace.v1`` is a user-owned repo) in place of the seeded default; that is a **swap**:

  * the currently-active workspace is *parked* (moved aside under ``<root>/.attached/<subject>/<slug>``)
    so it stays available to swap back to — nothing is destroyed,
  * the requested repo is *attached* by restoring a previously-parked clone of it, or — first time —
    cloning it fresh into ``<root>/<subject>``.

Swapping back is just swapping to a repo already parked: its slug matches, so the parked tree is moved
back into place with NO re-clone (local changes/commits the subject made while detached persist). The
original seeded workspace is parked under the reserved ``seed`` slug; pass ``repo_url=None`` to swap
back to it (restored if parked, else re-seeded from the template).

The store dir (``<root>/.attached``) is dot-prefixed, so it is invisible to ``scan_workspace_subjects``
and the Workspace tree (both skip dotnames) — parked workspaces never masquerade as subjects.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed

log = logging.getLogger(__name__)

STORE_DIRNAME = ".attached"
STATE_FILENAME = "state.json"
SEED_SLOT = "seed"  # the reserved slug for the original template-seeded workspace
SEED_BACKUP_SLOT = "seed-prev"  # where 'start fresh' tucks the displaced default so it stays recoverable

# Inject the actual clone for tests (a local file repo, no network). Signature: (repo_url, ref, dest, token).
CloneFn = Callable[[str, str, Path, Optional[str]], None]


class CloneError(RuntimeError):
    """A clone failed. The message is REDACTED of any access token (P15) so it is safe to surface in an
    API error body / log."""


@dataclass(frozen=True)
class SwapResult:
    """Outcome of one swap, useful for the API body and tests."""

    subject: str
    active_slug: str
    repo: Optional[str]
    ref: Optional[str]
    swapped: bool          # False == requested repo was already the active workspace (no-op)
    cloned: bool           # True == a fresh git clone happened (vs restoring a parked tree)
    parked_slug: Optional[str]  # the slug the previously-active workspace was parked under
    nested: bool = False   # True == the clone wasn't a compliant workspace, so it was nested under kg/


def _repo_name(repo_url: str) -> str:
    """The readable tail of a repo URL — used as the ``kg/<name>/`` subdir when a non-compliant clone is
    nested inside a fresh template workspace."""
    tail = re.sub(r"\.git$", "", repo_url.strip().rstrip("/")).rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]+", "-", tail).strip("-") or "repo"


def _slug(repo_url: str) -> str:
    """A stable, filesystem-safe slug for a repo URL — a readable tail plus a short hash so two repos
    whose tails collide (``a/proj`` vs ``b/proj``) never share a parking slot."""
    tail = re.sub(r"\.git$", "", repo_url.strip().rstrip("/"))
    tail = re.sub(r"[^a-z0-9]+", "-", tail.lower().rsplit("/", 1)[-1]).strip("-") or "repo"
    digest = hashlib.sha1(repo_url.strip().encode()).hexdigest()[:8]
    return f"{tail}-{digest}"


def _authenticated_url(repo_url: str, token: Optional[str]) -> str:
    """Embed ``token`` as HTTP basic-auth in an https(/http) URL so a PRIVATE repo can be cloned. SSH/scp
    URLs (``git@host:org/repo``) and tokenless calls are returned unchanged (key-auth / public)."""
    if not token or "://" not in repo_url:
        return repo_url
    proto, rest = repo_url.split("://", 1)
    return f"{proto}://{token}@{rest}"


def _git_clone(repo_url: str, ref: str, dest: Path, token: Optional[str] = None) -> None:
    """Default clone: clone then checkout ``ref`` (kept separate so a non-default branch/tag/sha works
    regardless of the remote's default branch).

    PRIVATE repos: when a ``token`` is given it is embedded in the clone URL for the network op ONLY, then
    the persisted ``origin`` is reset to the token-free URL so the credential never lands in the cloned
    ``.git/config`` or the synced workspace (P15 — mirrors ``GitHubVcs.push``). Git is run with prompts
    disabled so a missing/invalid credential FAILS LOUD instead of hanging on a terminal prompt. Any
    failure raises ``CloneError`` with the token redacted from the message."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_ASKPASS": "true", "GIT_TERMINAL_PROMPT": "0"}
    url = _authenticated_url(repo_url, token)

    def redact(text: str) -> str:
        return text.replace(token, "***") if token else text

    try:
        subprocess.run(["git", "clone", "--quiet", url, str(dest)],
                       check=True, capture_output=True, text=True, env=env)
        if token:  # never persist the credential in the cloned repo's origin (P15)
            subprocess.run(["git", "-C", str(dest), "remote", "set-url", "origin", repo_url],
                           check=True, capture_output=True, text=True)
        if ref:
            subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", ref],
                           check=True, capture_output=True, text=True, env=env)
    except subprocess.CalledProcessError as exc:
        raise CloneError(redact((exc.stderr or str(exc)).strip())) from None


def _safe_subject_dir(root: Path, subject: str) -> Path:
    ws = (root / subject).resolve()
    if ws != root.resolve() and root.resolve() not in ws.parents:
        raise ValueError("invalid subject")
    if subject.startswith("."):  # reserved namespace (the store lives at a dotname)
        raise ValueError("invalid subject")
    return ws


def _store(root: Path, subject: str) -> Path:
    return root / STORE_DIRNAME / subject


def _load_state(store: Path) -> dict:
    f = store / STATE_FILENAME
    if not f.exists():
        return {"active": None, "slots": {}}
    try:
        data = json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return {"active": None, "slots": {}}
    if not isinstance(data, dict):
        return {"active": None, "slots": {}}
    data.setdefault("active", None)
    data.setdefault("slots", {})
    return data


def _save_state(store: Path, state: dict) -> None:
    store.mkdir(parents=True, exist_ok=True)
    (store / STATE_FILENAME).write_text(json.dumps(state, indent=2, sort_keys=True))


def attached_workspaces(root: str | Path, subject: str) -> dict:
    """The subject's attachment view: which slug is active and the parked slots (slug → repo/ref).
    Read-only; safe to call before any swap (returns the empty shape)."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    return _load_state(_store(rootp, subject))


def swap_workspace(
    root: str | Path,
    subject: str,
    repo_url: Optional[str],
    ref: str = "main",
    *,
    slug: Optional[str] = None,
    fresh: bool = False,
    token: Optional[str] = None,
    clone: CloneFn = _git_clone,
) -> SwapResult:
    """Swap the subject's active workspace to ``repo_url`` (or back to the seed when ``repo_url`` is None).

    Parks the currently-active workspace under its slug (kept available), then restores the requested
    repo's parked tree if present, else clones it fresh. ``token`` (optional) authenticates the clone of a
    PRIVATE repo — used only for the network op, never persisted/stored (P15). Idempotent: requesting the
    already-active repo is a no-op (``swapped=False``).

    ``fresh=True`` (only meaningful when swapping to the seed) is the **start-fresh** path: rebuild the
    default from the template instead of restoring the parked seed. It is NEVER a no-op — the displaced
    default (the live tree if we're on it, else the previously-parked seed) is tucked under
    ``SEED_BACKUP_SLOT`` so it stays recoverable; nothing is destroyed.

    ``slug`` targets a parked slot DIRECTLY (overriding the repo→slug derivation) — the way to swap back
    to a slot that carries no repo URL (the seed, or a ``SEED_BACKUP_SLOT`` backup). A parked tree is
    restored (no re-clone); an unknown slug with no repo to clone raises ``KeyError``."""
    rootp = Path(root)
    active_dir = _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)

    target_slug = (slug or "").strip() or (SEED_SLOT if not repo_url else _slug(repo_url))
    fresh_seed = bool(fresh) and target_slug == SEED_SLOT  # 'start fresh' only applies to the default

    # No-op: the requested repo is already mounted (and really present on disk). A never-swapped subject
    # (state.active is None) is already ON the seed, so swapping to the seed is ALSO a no-op. Without this
    # guard the live (init-seeded) workspace gets parked under the "seed" slug and replaced by a blank
    # re-seed — and since the park slug equals the now-active slug, the subject's real data is orphaned.
    # A 'start fresh' is an explicit rebuild, so it bypasses the no-op (and keeps a recoverable backup).
    already_on_seed = target_slug == SEED_SLOT and state.get("active") is None
    if not fresh_seed and (state.get("active") == target_slug or already_on_seed) and (active_dir / ".git").exists():
        slot = state["slots"].get(target_slug, {})
        return SwapResult(subject, target_slug, slot.get("repo"), slot.get("ref"),
                          swapped=False, cloned=False, parked_slug=None)

    # ── PHASE 1: build the target tree OUT OF PLACE — the live workspace is NOT touched yet, so a clone
    # failure (private repo, bad token, network) raises here leaving everything exactly as it was. ──────
    parked_target = store / target_slug
    cloned = nested = False
    staged: Path                       # the ready-to-activate tree we'll move into active_dir
    restore = False                    # True == staged is the parked slot itself (swap-back; move, don't rebuild)
    if not fresh_seed and parked_target.exists():
        staged, restore = parked_target, True
    elif target_slug == SEED_SLOT:     # fresh start, or first-ever seed with nothing parked → reseed
        staged = store / ".staging-seed"
        if staged.exists():
            shutil.rmtree(staged)
        _reseed(staged)
    elif repo_url:                     # first attach of this repo → clone it fresh
        staged = store / f".staging-{target_slug}"
        if staged.exists():
            shutil.rmtree(staged)
        cloned, nested = _build_attached(staged, repo_url, ref, token, clone)  # may raise CloneError (safe)
    else:
        raise KeyError(target_slug)    # asked to restore a slot that isn't parked and has no repo to clone

    # ── PHASE 2: COMMIT the swap — only local moves now (low failure risk). Park the live workspace so it
    # stays available to swap back to, then activate the staged tree. ───────────────────────────────────
    parked_slug: Optional[str] = None
    has_active = (active_dir / ".git").exists() or (active_dir.exists() and any(active_dir.iterdir()))

    if fresh_seed:
        # The displaced default goes to SEED_BACKUP_SLOT (recoverable). If we're leaving an attached repo,
        # that repo is parked under its own slug and the previously-parked seed is what we back up.
        if has_active and state.get("active") not in (None, SEED_SLOT):
            parked_slug = state["active"]
            _park(store, parked_slug, active_dir)
            state["slots"].setdefault(parked_slug, {"repo": None, "ref": None})
            _backup_default(store, parked_target, state)   # store/seed → seed-prev (if present)
        elif has_active:                                    # we're ON the default — the live tree IS it
            _backup_default(store, active_dir, state)       # active_dir → seed-prev
        else:
            _backup_default(store, parked_target, state)    # store/seed → seed-prev (if present)
            if active_dir.exists():
                shutil.rmtree(active_dir)                    # empty husk
    elif has_active:
        parked_slug = state.get("active") or SEED_SLOT  # (never equals target here — that's the no-op above)
        _park(store, parked_slug, active_dir)
        state["slots"].setdefault(parked_slug, {"repo": None, "ref": None})
    elif active_dir.exists():
        shutil.rmtree(active_dir)  # empty husk — clear the way for the attach

    shutil.move(str(staged), str(active_dir))
    if not restore:
        slot = state["slots"].get(target_slug, {})   # preserve a display name across a content rebuild
        slot.update({"repo": repo_url, "ref": ref, "nested": nested})
        state["slots"][target_slug] = slot

    state["active"] = target_slug
    _save_state(store, state)
    slot = state["slots"].get(target_slug, {})
    return SwapResult(subject, target_slug, slot.get("repo"), slot.get("ref"),
                      swapped=True, cloned=cloned, parked_slug=parked_slug, nested=bool(slot.get("nested")))


def _build_attached(dest: Path, repo_url: str, ref: str, token: Optional[str], clone: CloneFn) -> tuple[bool, bool]:
    """Build the workspace tree for an attached repo AT ``dest`` (out of the live workspace's way).

    COMPLIANCE GATE: a workspace must carry a governance root (``validate_seed`` — i.e. a ``CLAUDE.md``).
    A compliant clone becomes the tree as-is. A non-compliant one is wrapped: a fresh template workspace
    is materialized at ``dest`` and the clone is nested under ``kg/<repo-name>/`` (its own ``.git`` dropped
    so it folds into the governed workspace) and committed. Returns ``(cloned, nested)``. Raises
    ``CloneError`` on a failed clone WITHOUT having created ``dest`` (caller's active workspace untouched)."""
    incoming = dest.parent / f"{dest.name}.clone"
    if incoming.exists():
        shutil.rmtree(incoming)
    incoming.parent.mkdir(parents=True, exist_ok=True)
    clone(repo_url, ref, incoming, token)          # raises CloneError on failure — nothing placed yet

    if not validate_seed(incoming):                # compliant workspace → use as-is
        shutil.move(str(incoming), str(dest))
        return True, False

    # Non-compliant → wrap in a fresh template workspace, nest the clone under kg/.
    _reseed(dest)
    shutil.rmtree(incoming / ".git", ignore_errors=True)   # fold into the governed workspace's git
    sub = dest / "kg" / _repo_name(repo_url)
    sub.parent.mkdir(parents=True, exist_ok=True)
    if sub.exists():
        shutil.rmtree(sub)
    shutil.move(str(incoming), str(sub))
    _git_commit_all(dest, f"attach non-compliant repo {repo_url} under kg/{_repo_name(repo_url)}")
    return True, True


def _git_commit_all(ws: Path, message: str) -> None:
    """Stage + commit everything in the workspace repo (the nested-import commit). Best-effort no-op on
    an empty diff."""
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-q", "-m", message, "--allow-empty"],
                   check=True, capture_output=True, text=True)


def _reseed(active_dir: Path) -> None:
    """Re-materialize the seed workspace from the validated template (the swap-back-to-default path when
    no parked seed tree exists). Mirrors the worker/init seeding fallback."""
    seed_dir = resolve_seed_dir()
    if validate_seed(seed_dir):
        log.warning("seed template %s invalid — seeding a bare workspace", seed_dir)
        seed_dir = None
    seed_workspace(active_dir, seed_dir)


def _park(store: Path, slug: str, src: Path) -> None:
    """Move the live tree ``src`` into its parking slot ``store/slug`` (superseding a stale park there)."""
    dst = store / slug
    if dst.exists():
        shutil.rmtree(dst)  # supersede a stale park (its live copy was the active one)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _backup_default(store: Path, src: Path, state: dict) -> None:
    """Tuck the displaced default tree ``src`` under ``SEED_BACKUP_SLOT`` so 'start fresh' stays
    recoverable (one level — a second start-fresh supersedes the prior backup). No-op if ``src`` is
    missing/empty. The current seed's display name (if any) carries onto the backup."""
    has_content = src.exists() and ((src / ".git").exists() or any(src.iterdir()))
    if not has_content:
        return
    backup = store / SEED_BACKUP_SLOT
    if backup.exists():
        shutil.rmtree(backup)
    prev_name = state["slots"].get(SEED_SLOT, {}).get("name")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(backup))
    state["slots"][SEED_BACKUP_SLOT] = {
        "repo": None, "ref": None,
        "name": f"{prev_name} (previous)" if prev_name else "default (previous)",
    }


def rename_workspace(root: str | Path, subject: str, slug: str, name: Optional[str]) -> dict:
    """Set a slot's DISPLAY name — a label only. The slug and the parked directory are unchanged, so
    swap-back and repo re-attach keep matching by URL. An empty/whitespace ``name`` clears the label
    (reverting to the default). The reserved seed slot may be named too. Returns the updated view."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    slug = (slug or "").strip()
    label = (name or "").strip()[:80]
    if slug != SEED_SLOT and slug not in state["slots"]:
        raise KeyError(slug)  # unknown workspace
    slot = state["slots"].setdefault(slug, {"repo": None, "ref": None})
    if label:
        slot["name"] = label
    else:
        slot.pop("name", None)
    _save_state(store, state)
    return state
