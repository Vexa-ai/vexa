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
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Optional

from shared.atomic_json import write_json_atomic
from shared.git_redaction import redact
from control_plane.repo_ref import assert_not_credential, assert_public_host, valid_ref
from shared.gitenv import transport_env
from shared.gitexec import run_git
from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed
from shared.token_destination import embed_token
from workspaces.shared import workspace_paths as wpaths

log = logging.getLogger(__name__)

STORE_DIRNAME = ".attached"
STATE_FILENAME = "state.json"
SEED_SLOT = "seed"  # the reserved slug for the original template-seeded workspace
SEED_BACKUP_SLOT = "seed-prev"  # where 'start fresh' tucks the displaced default so it stays recoverable
PERSONAL_ALIAS = "personal"    # what the terminal and the MCP call a person's own desk (see workspace_dir_for)

# The subject's PRIVATE baseline — the workspace at ``<root>/<subject>`` that a turn always mounts. Its
# slug in the active set is whatever ``state.active`` points at (the seed by default). This slug marks
# the workspace that lives at the legacy in-place path and is ALWAYS active + non-deactivatable.
PRIVATE_ROLE = "private"
# A SHARED workspace mount (Lane A): a workspace the subject is a MEMBER of (not their own), resolved from
# the membership index + authoritatively re-checked against the workspace's own policy/members.json. Write
# access is gated by the member's role (contributor/owner write; viewer is read-only).
SHARED_ROLE = "shared"
# THE WORKSPACE NAME RULE is ``workspace_paths.is_workspace_name`` — one name, never a separator, never
# a dotname, never ``.`` or ``..``. Every slug and every shared workspace id this module joins onto a
# path meets it, and never as a platform tier (``tier=False``): ``_global`` and ``_system`` are not
# slots, attachable trees or shareable workspaces.

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


@dataclass(frozen=True)
class ActiveMount:
    """One member of the additive mount set (WP-A1.1). The dispatch turns this into a worker mount at a
    deterministic path; the worker harness declares slug/role/write verbatim to the model."""

    slug: str
    repo: Optional[str]
    ref: Optional[str]
    role: str           # 'private' (the subject's own baseline / attached repos) — 'shared'/'system' land in later WPs
    path: str           # ABSOLUTE on-disk path inside the mounted store root (<root>/<subject> or <root>/.attached/<subject>/<slug>)
    write: bool = True  # the subject may always write their own private workspaces; membership gates land later
    primary: bool = False  # True == the DEFAULT HOME (first active workspace = the turn's cwd); dynamic, not a rank
    name: Optional[str] = None  # the DISPLAY label (switcher rename), so KNOWLEDGE shows the same name as the switcher — not the raw slug
    #: THE PERSON'S OWN DESK — the tree at ``<root>/<subject>`` (Vexa-ai/vexa#1783, founder 2026-10-15:
    #: "badges disagree personal = biz"). Not the same fact as ``primary``: primary is merely the first
    #: active member (the turn's cwd), and when that is an attached repo, calling it "Personal" named a
    #: biz clone after the desk. Personal is the desk, always, and this is how every reader knows it.
    desk: bool = False


@dataclass(frozen=True)
class ActiveResult:
    """Outcome of one activate/deactivate — for the API body and tests."""

    subject: str
    slug: str
    changed: bool             # False == already in the desired state (idempotent no-op)
    cloned: bool = False      # True == a fresh git clone happened (activate of a never-seen repo)


def _slot(store: Path, slug: Optional[str]) -> Path:
    """``store/<slug>`` for a slug that names ONE slot of this store, or ``KeyError``.

    Every path this module builds from a slug goes through here. A slug is one name under the
    workspace name rule (``workspace_paths.is_workspace_name``: no separator, no leading dot, so never ``.`` or
    ``..``), and the joined path must still resolve inside ``store`` — the subject's own store, or
    the one shared workspace's store. Anything else is not a workspace of this store."""
    s = (slug or "").strip()
    if not wpaths.is_workspace_name(s, tier=False):
        raise KeyError(s)
    try:
        wpaths.resolve_inside(store, s)
    except wpaths.PathRefused:
        raise KeyError(s) from None
    return store / s


def _slug_dir(root: Path, subject: str, state: dict, slug: str) -> Path:
    """Where a slug's workspace tree lives on disk: the SEED-SLOT occupant sits in place at
    ``<root>/<subject>``; every other slug lives in its store slot ``<root>/.attached/<subject>/<slug>``.
    A pure storage-location detail (not a rank) — active + parked members alike resolve through here."""
    if slug == _seed_slot_slug(state):
        return _safe_subject_dir(root, subject)
    return _slot(_store(root, subject), slug)


def workspace_slot_dir(root: str | Path, subject: str, slug: str) -> Path:
    """PUBLIC: where one of a subject's OWN workspaces lives on disk, seed slot or store slot alike.

    Added for the workspace-identity sync (PRD decision 26): after an un-share the tree is no longer
    at ``<root>/<workspace_id>`` and the registry has to be re-pointed at wherever it landed. The
    caller could compute ``<root>/.attached/<subject>/<slug>`` itself — and that is exactly the
    problem: the store layout would then be spelled in two modules, and the second spelling is the
    one nobody updates. This module owns the layout; here is the read of it."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    return _slug_dir(rootp, subject, _load_state(_store(rootp, subject)), slug)


def _slug(repo_url: str) -> str:
    """A stable, filesystem-safe slug for a repo URL — a readable tail plus a short hash so two repos
    whose tails collide (``a/proj`` vs ``b/proj``) never share a parking slot."""
    tail = re.sub(r"\.git$", "", repo_url.strip().rstrip("/"))
    tail = re.sub(r"[^a-z0-9]+", "-", tail.lower().rsplit("/", 1)[-1]).strip("-") or "repo"
    digest = hashlib.sha1(repo_url.strip().encode()).hexdigest()[:8]
    return f"{tail}-{digest}"


def _git_clone(repo_url: str, ref: str, dest: Path, token: Optional[str] = None,
               *, ssh_env: Optional[dict] = None) -> None:
    """Default clone: clone then checkout ``ref`` (kept separate so a non-default branch/tag/sha works
    regardless of the remote's default branch).

    TWO credential shapes, and the URL decides which:

    * ``ssh_env`` (``GIT_SSH_COMMAND`` from ``deploy_keys.ssh_env``) authenticates an ``ssh://`` /
      ``git@host:`` remote as the workspace's DEPLOY KEY. Nothing is embedded in the URL, so nothing can
      be persisted; the private key exists on disk only while the context manager that built this env is
      open. This is the primary path — the person adds our public key, we never hold theirs.
    * ``token`` (a PAT, the fallback) is embedded in an https clone URL for the network op ONLY, then
      the persisted ``origin`` is reset to the token-free URL so the credential never lands in the
      cloned ``.git/config`` or the synced workspace (P15 — mirrors ``GitHubVcs.push``).

    Git is run with prompts disabled so a missing/invalid credential FAILS LOUD instead of hanging on a
    terminal prompt. Any failure raises ``CloneError`` with the token redacted from the message.

    The repository is validated BEFORE any subprocess exists: ``git clone`` is a fetch this server
    performs, so a URL naming an address only this server can reach turns the attach dialog into a
    server-side request forge (``http://169.254.169.254/…``, ``http://admin-api:8001/…``) with the
    outcome readable in the clone error. The check is repeated here rather than trusted from the route
    so it also covers the MCP, a future route, and a test that forgot — the same stance the token
    redaction takes. The transport allow-list (``transport_env``) is the second half: a URL may not
    reach a transport that runs a command (``ext::``) or reads this host's disk (``file://``), and an
    ``https`` clone may not redirect down into one."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # scrubbed env: a hook-exported GIT_DIR would re-point every op below at the hook's repo
    # (see shared/gitenv.py); prompts stay disabled so a bad credential fails loud; the transport
    # allow-list is pinned to what this URL legitimately needs.
    assert_not_credential(repo_url)   # never hand a secret to a subprocess (see repo_ref)
    assert_public_host(repo_url)      # …nor make this server fetch its own neighbours (R-D15)
    ref = valid_ref(ref)              # …and never hand it a git OPTION either (R-E14)
    env = {**transport_env(repo_url), "GIT_ASKPASS": "true", "GIT_TERMINAL_PROMPT": "0",
           **(ssh_env or {})}
    url = embed_token(repo_url, token)

    try:
        # `--` so a repository beginning with `-` is a repository and not an option to `git clone`.
        run_git(None, "clone", "--quiet", "--", url, str(dest), env=env, check=True)
        if token:  # never persist the credential in the cloned repo's origin (P15)
            run_git(dest, "remote", "set-url", "origin", repo_url, env=env, check=True)
        if ref:
            run_git(dest, "checkout", "--quiet", ref, env=env, check=True)
    except subprocess.CalledProcessError as exc:
        # SHAPE-BASED redaction, not "replace the token we were given": the credential that leaked on
        # 2026-09-02 arrived as the repo ARGUMENT, so `token` was None and the old replace() was a no-op
        # over a message containing the secret. See git_redaction.
        raise CloneError(redact((exc.stderr or str(exc)).strip(), token)) from None


def _safe_subject_dir(root: Path, subject: str) -> Path:
    if not subject or "/" in subject or "\\" in subject:   # one name, never a path
        raise ValueError("invalid subject")
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
    data: dict = {"active": None, "slots": {}, "active_set": []}
    if f.exists():
        try:
            loaded = json.loads(f.read_text())
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            pass
    data.setdefault("active", None)
    data.setdefault("slots", {})
    # ``active_set`` (the mount set) is an ORDERED list of active slugs, all EQUAL RANK.
    if not isinstance(data.get("active_set"), list):
        data["active_set"] = []
    # ── MIGRATION to the FLAT, equal-rank model ────────────────────────────────────────────────────────
    # There is no "baseline"/"primary". Every workspace is the same rank; the seed-slot workspace (the one
    # whose tree lives at <root>/<subject>) is just a NORMAL member of ``active_set``, auto-added at startup.
    # Older states never listed it explicitly (it was an implicit always-first baseline) and switched it off
    # with a ``baseline_hidden`` flag. Rewrite (in-memory, idempotent) to the flat form: the seed slug is a
    # member unless it was deliberately switched off; the legacy flag is dropped.
    # ONE-TIME (``_flat_v1`` marker): once migrated, ``active_set`` is authoritative — never re-inject the
    # seed, or a deliberate deactivation of Personal would be silently undone on the next read.
    if not data.get("_flat_v1"):
        seed_slot = data.get("active") or SEED_SLOT
        hidden = bool(data.pop("baseline_hidden", False))
        aset = [s for s in data["active_set"] if isinstance(s, str)]
        if hidden:
            aset = [s for s in aset if s != seed_slot]      # was deliberately off → not a member
        elif seed_slot not in aset:
            aset = [seed_slot, *aset]                        # active by default → make it an explicit member
        data["active_set"] = aset
        data["_flat_v1"] = True
    return data


def _seed_slot_slug(state: dict) -> str:
    """PATH RESOLUTION ONLY (not a rank): the slug whose tree lives at ``<root>/<subject>`` — the seed, or
    whatever repo has been swapped into that slot. Every OTHER slug lives under ``.attached/<subject>/<slug>``.
    A never-swapped subject (``active`` is None) has the seed there."""
    return state.get("active") or SEED_SLOT


def _normalized_active_set(state: dict) -> list[str]:
    """The subject's ACTIVE SET — a FLAT list of EQUAL-RANK workspaces (no baseline, no forced-first, no
    primary). The seed-slot workspace is a normal member like any other; switching it off simply removes it
    from the list (``_load_state`` migrates older states into this shape). Duplicates and unknown slugs (no
    slot record and not the seed-slot occupant) are dropped; order is the subject's own."""
    seed_slot = _seed_slot_slug(state)
    ordered: list[str] = []
    for slug in state.get("active_set", []):
        if not isinstance(slug, str) or slug in ordered:
            continue
        if slug == seed_slot or (slug in state.get("slots", {}) and wpaths.is_workspace_name(slug, tier=False)):
            ordered.append(slug)
    return ordered


def _save_state(store: Path, state: dict) -> None:
    store.mkdir(parents=True, exist_ok=True)
    write_json_atomic(store / STATE_FILENAME, state, indent=2, sort_keys=True)


def attached_workspaces(root: str | Path, subject: str) -> dict:
    """The subject's attachment view: the parked slots (slug → repo/ref) and the ordered ``active_set`` (the
    flat, equal-rank mount set). Read-only; safe to call before any attach (returns the empty shape). The
    terminal renders the per-row active toggle directly off ``active_set``."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    state = _load_state(_store(rootp, subject))
    state["active_set"] = _normalized_active_set(state)
    return state


def workspace_dir_for(root: str | Path, subject: str, slug: Optional[str]) -> Path:
    """Resolve one of the subject's OWN workspace slots — ACTIVE or PARKED — to its on-disk dir, so a
    management op (git push/pull/status, purpose) can address a workspace by slug without it having to be
    mounted. The seed-slot occupant resolves in place at ``<root>/<subject>``; every other slug to its
    store slot. An empty slug / the subject / the seed sentinel all resolve to the seed slot. Raises
    ``KeyError`` for a slug that is not one of the subject's slots (never another user's workspace)."""
    rootp = Path(root)
    state = _load_state(_store(rootp, subject))
    seed = _seed_slot_slug(state)
    target = (slug or "").strip()
    if not target or target in (subject, SEED_SLOT, seed):
        return _safe_subject_dir(rootp, subject)
    if target in state.get("slots", {}):
        return _slug_dir(rootp, subject, state, target)
    if target == PERSONAL_ALIAS:
        # "personal" is what the TERMINAL's workspace chip and the MCP verbs call a person's own desk;
        # the store calls it the seed slot. Resolved AFTER the slot lookup so a real slug of that name
        # would still win, and only here — the store's own vocabulary is unchanged.
        return _safe_subject_dir(rootp, subject)
    raise KeyError(target)


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
    assert_not_credential(repo_url)   # a secret must never reach git (repo_ref)
    rootp = Path(root)
    active_dir = _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)

    target_slug = (slug or "").strip() or (SEED_SLOT if not repo_url else _slug(repo_url))
    parked_target = _slot(store, target_slug)      # KeyError: not a slot of this subject's store
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
    cloned = False
    staged: Path                       # the ready-to-activate tree we'll move into active_dir
    restore = False                    # True == staged is the parked slot itself (swap-back; move, don't rebuild)
    if not fresh_seed and parked_target.exists():
        staged, restore = parked_target, True
    elif target_slug == SEED_SLOT:     # fresh start, or first-ever seed with nothing parked → reseed
        staged = store / ".staging-seed"
        if staged.exists():
            wpaths.remove_tree(staged)
        _reseed(staged)
    elif repo_url:                     # first attach of this repo → clone it fresh
        staged = store / f".staging-{target_slug}"
        if staged.exists():
            wpaths.remove_tree(staged)
        cloned = _build_attached(staged, repo_url, ref, token, clone)  # may raise CloneError (safe)
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
                wpaths.remove_tree(active_dir)                    # empty husk
    elif has_active:
        parked_slug = state.get("active") or SEED_SLOT  # (never equals target here — that's the no-op above)
        _park(store, parked_slug, active_dir)
        state["slots"].setdefault(parked_slug, {"repo": None, "ref": None})
    elif active_dir.exists():
        wpaths.remove_tree(active_dir)  # empty husk — clear the way for the attach

    shutil.move(str(staged), str(active_dir))
    if not restore:
        slot = state["slots"].get(target_slug, {})   # preserve a display name across a content rebuild
        slot.update({"repo": repo_url, "ref": ref})
        state["slots"][target_slug] = slot

    # Flat model: a swap replaces the SEED-SLOT occupant. In the active set, the new occupant takes the old
    # one's place; the displaced workspace is parked (drops out of the set — it survives as a parked slot).
    # Every OTHER active member is untouched (a swap must not silently drop the rest of the mount set).
    old_seed_slot = _seed_slot_slug(state)
    state["active"] = target_slug
    new_set = [target_slug if s == old_seed_slot else s for s in state.get("active_set", [])]
    if target_slug not in new_set:
        new_set.append(target_slug)
    state["active_set"] = _normalized_active_set({**state, "active_set": new_set, "_flat_v1": True})
    _save_state(store, state)
    slot = state["slots"].get(target_slug, {})
    return SwapResult(subject, target_slug, slot.get("repo"), slot.get("ref"),
                      swapped=True, cloned=cloned, parked_slug=parked_slug)


# ── the additive mount set (WP-A2.1) — activate ADDS to the set without parking the others ──────────

def active_workspaces(root: str | Path, subject: str) -> list[ActiveMount]:
    """The subject's ordered ACTIVE SET (the mount set the next dispatch materializes). The private
    baseline (``<root>/<subject>``) is always first; every other member is a secondary active workspace
    living in its store slot. Read-only; a never-swapped subject yields just the private baseline (its
    on-disk tree may not exist yet — the worker seeds it on first dispatch, exactly as today)."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    hidden = _hidden_of(state)
    ordered = [s for s in _normalized_active_set(state) if s not in hidden]   # HIDDEN is never mounted
    # No baseline: ``primary`` marks the DEFAULT HOME — the first active workspace (the cwd a turn opens in).
    # It is dynamic (moves as workspaces are activated/deactivated), not a fixed rank; empty set ⇒ no home.
    home = ordered[0] if ordered else None
    desk_slug = _seed_slot_slug(state)
    mounts: list[ActiveMount] = []
    for slug in ordered:
        slot = state["slots"].get(slug, {})
        mounts.append(ActiveMount(
            slug=slug,
            repo=slot.get("repo"),
            ref=slot.get("ref"),
            role=PRIVATE_ROLE,   # every workspace a subject owns is 'private' for now; 'shared'/'system' land later
            path=str(_slug_dir(rootp, subject, state, slug)),
            write=True,
            primary=(slug == home),
            name=(slot.get("name") or "").strip() or None,   # the switcher's display label (rename), if set
            desk=(slug == desk_slug),
        ))
    return mounts


def _slugify_name(name: str) -> str:
    """A safe workspace-id base from a human name: lowercase alnum, spaces/punct → '-', trimmed."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s[:40] or "shared"


def create_shared_workspace_dir(root: str | Path, name: str) -> str:
    """CREATE a new TOP-LEVEL shared workspace at ``<root>/<workspace_id>`` (where shared workspaces are
    addressed — NOT under a subject's .attached store), git-inited + seeded from the layout template.
    Returns the fresh ``workspace_id``. Ownership is NOT set here — the caller (the /shared/new route)
    calls ``workspace_membership.ensure_owner`` next, so dir-creation (attach domain) and the owner grant
    (membership domain) stay separated. Materialize-then-move so a seed failure leaves the root untouched."""
    rootp = Path(root)
    base = _slugify_name(name)
    wid = f"{base}-{secrets.token_hex(3)}"
    while (rootp / wid).exists():
        wid = f"{base}-{secrets.token_hex(3)}"
    staged = rootp / f".staging-shared-{wid}"
    if staged.exists():
        wpaths.remove_tree(staged)
    _reseed(staged)  # git init + seed the layout template
    (rootp / wid).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staged), str(rootp / wid))
    return wid


def hidden_shared_set(root: str | Path, subject: str) -> set[str]:
    """The workspace_ids the subject has SWITCHED OFF (deactivated from their active set) — a per-user UI
    preference stored in their attach state (``hidden_shared``). Membership is unchanged; the workspace is
    just not mounted/shown until re-activated. Fails soft to an empty set (never breaks the active-set read)."""
    try:
        rootp = Path(root)
        _safe_subject_dir(rootp, subject)
        return set(_load_state(_store(rootp, subject)).get("hidden_shared", []))
    except Exception:  # noqa: BLE001
        return set()


def set_shared_active(root: str | Path, subject: str, workspace_id: str, active: bool) -> None:
    """Switch a shared workspace ON (mount it) or OFF (hide from the active set) for THIS subject. Toggles
    membership in the ``hidden_shared`` list in their attach state — does NOT touch the membership grant."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    hidden = set(state.get("hidden_shared", []))
    if active:
        if workspace_id in _hidden_of(state):
            return      # a HIDDEN workspace comes back only by unhiding it (set_workspace_state)
        hidden.discard(workspace_id)
    else:
        hidden.add(workspace_id)
    state["hidden_shared"] = sorted(hidden)
    _save_state(store, state)


def own_private_slot(root: str | Path, subject: str, slug: Optional[str]) -> Optional[dict]:
    """The subject's OWN private, non-primary workspace slot named ``slug`` — its slot record
    (``name``, ``repo``…) — or ``None``. The primary (personal) workspace, the seed slots, a shared
    workspace and anything that is not one of this subject's slots all answer ``None``. Never
    raises: a question about a slug is not a reason to fail the request asking it."""
    target = (slug or "").strip()
    if not target or target in (str(subject), PERSONAL_ALIAS, SEED_SLOT, SEED_BACKUP_SLOT):
        return None
    try:
        rootp = Path(root)
        _safe_subject_dir(rootp, subject)
        state = _load_state(_store(rootp, subject))
    except Exception:  # noqa: BLE001
        return None
    if target == _seed_slot_slug(state):
        return None
    slot = (state.get("slots") or {}).get(target)
    return dict(slot) if isinstance(slot, dict) else None


def ensure_workspace_shareable(root: str | Path, subject: str, slug: str) -> tuple[str, bool]:
    """Make ONE of the subject's OWN workspaces shareable — the "any workspace can be shared after" op, so
    there is no share-vs-not decision at CREATE time. Returns ``(workspace_id, promoted)``:

    - Already a top-level shared workspace the subject belongs to → returns ``(slug, False)`` (no-op).
    - A private NON-PRIMARY workspace (a ``.attached`` slot) → PROMOTES it: moves its tree to a fresh
      top-level ``<root>/<workspace_id>``, drops the private slot from the subject's active set, and returns
      ``(new_id, True)`` so the caller records the owner membership. The tree (git history) is preserved.
    - The PRIVATE BASELINE (primary) is refused — it is the subject's personal home; create a new workspace
      to share instead.
    """
    from control_plane import workspace_membership as membership

    rootp = Path(root).resolve()
    _safe_subject_dir(rootp, subject)
    if not wpaths.is_workspace_name(slug or "", tier=False):
        raise KeyError(slug)
    store = _store(rootp, subject)
    state = _load_state(store)
    primary = _seed_slot_slug(state)

    if slug == primary:
        raise ValueError("the baseline workspace can't be shared — create a new workspace to share")
    # the invisible system tiers (_global / _system) are platform-owned, never a shareable user workspace
    if slug in membership.RESERVED_SLUGS:
        raise ValueError(f"'{slug}' is a system workspace and can't be shared")
    # already a top-level shared workspace the caller is a member of → nothing to do
    if (rootp / slug).exists() and membership.is_member(rootp, slug, subject) is not None:
        return slug, False

    # ONLY A SLOT THIS STORE RECORDS. Anything else that happens to sit in the subject's store (a
    # leftover, a staging tree, something a volume brought) is not one of their workspaces.
    if slug not in (state.get("slots") or {}):
        raise KeyError(slug)
    src = _slug_dir(rootp, subject, state, slug)  # the private slot's real on-disk path (.attached/…)
    if not src.exists():
        raise KeyError(slug)

    base = _slugify_name((state["slots"].get(slug, {}).get("name") or slug))
    new_id = f"{base}-{secrets.token_hex(3)}"
    while (rootp / new_id).exists():
        new_id = f"{base}-{secrets.token_hex(3)}"
    shutil.move(str(src), str(rootp / new_id))  # re-home the tree top-level (git history intact)

    # drop the now-migrated private slot from the subject's state — it re-enters as a shared MEMBERSHIP.
    state["slots"].pop(slug, None)
    state["active_set"] = [s for s in state.get("active_set", []) if s != slug]
    _save_state(store, state)
    return new_id, True


def set_archived(root: str | Path, subject: str, slug: str, archived: bool) -> None:
    """ARCHIVE / un-archive one of the subject's OWN workspaces — a persistent ``archived`` flag on the
    slot. Archiving also unmounts it (drops it from the active set) so it stops loading; the tree is KEPT
    (collapsed under 'Archived' in the UI). The baseline is refused (it's the personal home)."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    if slug == _seed_slot_slug(state):
        raise ValueError("the baseline workspace can't be archived")
    slot = state["slots"].get(slug)
    if slot is None:
        raise KeyError(slug)
    slot["archived"] = bool(archived)
    state["slots"][slug] = slot
    hidden = {str(s) for s in (state.get("hidden") or [])}
    if archived:
        state["active_set"] = [s for s in state.get("active_set", []) if s != slug]
        hidden.add(slug)                     # ARCHIVED IS HIDDEN (see WORKSPACE STATES)
    else:
        hidden.discard(slug)
    state["hidden"] = sorted(hidden)
    _save_state(store, state)


def delete_workspace(root: str | Path, subject: str, slug: str) -> None:
    """DELETE one of the subject's OWN workspaces — REMOVE its tree from the private store and drop the slot.
    DESTRUCTIVE + irreversible. The baseline / seed slot is refused. Guarded so it can only remove a path
    UNDER the subject's own store (never the baseline, never outside root)."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    if slug in (_seed_slot_slug(state), SEED_SLOT):
        raise ValueError("the baseline workspace can't be deleted")
    if slug not in state["slots"]:
        raise KeyError(slug)
    slot_dir = _slot(store, slug).resolve()
    if store.resolve() in slot_dir.parents and slot_dir.exists():  # only ever a slot under this subject's store
        wpaths.remove_tree(slot_dir, ignore_errors=True)
    state["slots"].pop(slug, None)
    state["active_set"] = [s for s in state.get("active_set", []) if s != slug]
    state["hidden"] = sorted(s for s in (state.get("hidden") or []) if s != slug)
    _save_state(store, state)


def delete_shared_workspace(root: str | Path, subject: str, workspace_id: str) -> None:
    """DELETE a SHARED workspace's tree for everyone — its pages, history and parked repositories
    (Vexa-ai/vexa#1783). The caller must be its owner and must have confirmed the other members by
    name; both are the ROUTE's checks. The same guards as un-sharing: never a person's desk, never a
    private store, never a system tier, never a path outside ``root``. Memberships, invites and the
    registry row are the caller's to clear."""
    rootp = Path(root).resolve()
    _safe_subject_dir(rootp, subject)
    from control_plane import workspace_membership as membership
    if (not wpaths.is_workspace_name(workspace_id or "", tier=False)
            or workspace_id in membership.RESERVED_SLUGS):
        raise KeyError(workspace_id)
    if (workspace_id == subject or (rootp / STORE_DIRNAME / workspace_id).is_dir()
            or (rootp / ".system" / workspace_id).is_dir()):
        raise KeyError(workspace_id)
    ws = (rootp / workspace_id).resolve()
    if not ws.is_dir() or rootp not in ws.parents:
        raise KeyError(workspace_id)
    wpaths.remove_tree(ws)
    parked = (rootp / SHARED_STORE_DIRNAME / workspace_id)
    if parked.is_dir() and rootp in parked.resolve().parents:
        wpaths.remove_tree(parked, ignore_errors=True)


def ensure_workspace_private(root: str | Path, subject: str, workspace_id: str) -> str:
    """UN-SHARE — the mirror of ``ensure_workspace_shareable``. Move a top-level shared workspace back into
    the caller's private store as a normal workspace (git history intact) and return its new private slug.
    The caller must be the owner (the route enforces it); membership records are cleaned up by the caller.
    After this, the workspace is private again — other members lose access (its top-level path is gone)."""
    rootp = Path(root).resolve()
    _safe_subject_dir(rootp, subject)
    if not wpaths.is_workspace_name(workspace_id or "", tier=False):
        raise KeyError(workspace_id)
    # A DESK IS NEVER UN-SHARED. A subject's own `<root>/<subject>` is their baseline, whatever a
    # member list inside it says: a subject is known by its private store or its system tier.
    if (workspace_id == subject or (rootp / STORE_DIRNAME / workspace_id).is_dir()
            or (rootp / ".system" / workspace_id).is_dir()):
        raise KeyError(workspace_id)
    ws = (rootp / workspace_id).resolve()
    if not ws.exists() or rootp not in ws.parents:
        raise KeyError(workspace_id)
    store = _store(rootp, subject)
    state = _load_state(store)
    new_slug = _unique_new_slug(state)
    slot_dir = store / new_slug
    slot_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(ws), str(slot_dir))  # re-home the tree into the private .attached store
    from control_plane import workspace_membership as membership
    membership.strip_policy(slot_dir)    # a private tree carries no member list
    state["slots"][new_slug] = {"repo": None, "ref": None, "name": workspace_id}  # keep the name as the label
    state["active_set"] = _normalized_active_set({**state, "active_set": [*state.get("active_set", []), new_slug]})
    _save_state(store, state)
    return new_slug


def shared_active_mounts(root: str | Path, subject: str, memberships: list[dict], *,
                         include_off: bool = False) -> list[ActiveMount]:
    """The SHARED workspaces the subject is a MEMBER of (Lane A) — the seam that turns a membership grant
    into a mount in the active set. ``memberships`` is the derived index ``users.data.memberships[]`` (each
    ``{workspace_id, role, ...}``), used ONLY to ENUMERATE candidate workspaces; the role that gates WRITE
    is re-read AUTHORITATIVELY from each workspace's own ``policy/members.json`` (via ``is_member``), because
    the index is a convenience copy and must never be trusted for authorization.

    A candidate is dropped (never mounted) when: it is the subject's own baseline, a reserved/dot slug, its
    repo is not materialized on this node, or the authoritative check says the subject is NOT a member (a
    stale index entry). Surviving members mount READ-ONLY for viewers, READ-WRITE for contributor/owner.
    Pure + path-driven (no DB, no network) so the mapping is unit-tested offline."""
    # Deferred import to keep the module-load order clean (workspace_membership owns the authoritative
    # policy/members.json read + the reserved-slug set); neither module imports the other at top level.
    from control_plane import workspace_membership as membership

    rootp = Path(root).resolve()  # resolve up front so the traversal guard compares like-for-like (macOS /var→/private/var)
    # Switched OFF (inactive) or HIDDEN are not mounted. ``include_off`` is a chat that NAMED an
    # inactive one in its own reads (``chat_read_mounts``): inactive is a default, hidden is not.
    hidden = hidden_set(rootp, subject) | (set() if include_off else hidden_shared_set(rootp, subject))
    mounts: list[ActiveMount] = []
    for entry in memberships:
        ws_id = (entry.get("workspace_id") or "").strip() if isinstance(entry, dict) else ""
        if (not wpaths.is_workspace_name(ws_id) or ws_id == subject
                or ws_id in membership.RESERVED_SLUGS):
            continue  # own baseline / reserved / dot-namespaced / not one name are never shared mounts
        if ws_id in hidden:
            continue  # switched off by the user — keep the membership, just don't mount it
        ws_dir = (rootp / ws_id).resolve()
        if not ws_dir.exists() or rootp not in ws_dir.parents:
            continue  # not materialized on this node, or a traversal attempt — skip, never raise
        role = membership.is_member(rootp, ws_id, subject)  # AUTHORITATIVE (git), not the index copy
        if role is None:
            continue  # index is stale — the authoritative member list disagrees; do not mount
        mounts.append(ActiveMount(
            slug=ws_id,
            repo=None,
            ref=None,
            role=SHARED_ROLE,
            path=str(ws_dir),
            write=role in ("contributor", "owner"),  # viewer = read-only; the write gate lands here
            primary=False,  # a shared workspace is never the private baseline
            name=ws_id,  # a shared workspace's id IS its name (no per-user rename label)
        ))
    return mounts


def chat_read_mounts(root: str | Path, subject: str, reads: list[str],
                     memberships: Optional[list[dict]]) -> list[ActiveMount]:
    """The mounts a chat with its OWN reads gets (Vexa-ai/vexa#1783): exactly the workspaces it names,
    in its order, the personal one first when named. Inactive ones may be named (inactive is only the
    default for new chats); HIDDEN ones never mount, and a shared one mounts only for a member, with
    the write bit from its authoritative roster (``shared_active_mounts``). Nothing outside ``reads``."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    state = _load_state(_store(rootp, subject))
    hidden = _hidden_of(state)
    personal = _personal_names(state, subject)
    seed = _seed_slot_slug(state)
    # "personal" IS THE DESK (one name per workspace id) — and a desk the person HID (switched off,
    # deleted) is NOTHING: it is not mounted and nothing stands in for it (#1783, founder 2026-10-15:
    # after he deleted Personal, every chat naming "personal" still mounted and wrote it).
    seed_hidden = seed in hidden
    wanted: list[str] = []
    for r in reads or []:
        w = str(r or "").strip()
        if w in personal:
            if seed_hidden:
                continue
            w = seed
        if w and w not in wanted:
            wanted.append(w)
    out: list[ActiveMount] = []
    if seed in wanted and seed not in hidden:
        slot = state["slots"].get(seed, {})
        out.append(ActiveMount(slug=seed, repo=slot.get("repo"), ref=slot.get("ref"), role=PRIVATE_ROLE,
                               path=str(_slug_dir(rootp, subject, state, seed)), write=True, primary=True,
                               name=(slot.get("name") or "").strip() or None, desk=True))
    shared_wanted = []
    for w in wanted:
        if w == seed:
            continue
        if w in (state.get("slots") or {}):
            if w in hidden or w == SEED_SLOT:
                continue
            try:
                path = _slug_dir(rootp, subject, state, w)
            except KeyError:
                continue
            if not path.exists():
                continue
            slot = state["slots"].get(w, {})
            out.append(ActiveMount(slug=w, repo=slot.get("repo"), ref=slot.get("ref"), role=PRIVATE_ROLE,
                                   path=str(path), write=True, primary=False,
                                   name=(slot.get("name") or "").strip() or None))
        else:
            shared_wanted.append(w)
    if shared_wanted and memberships is not None:
        rows = [m for m in memberships if isinstance(m, dict)
                and str(m.get("workspace_id") or "").strip() in shared_wanted]
        by = {m.slug: m for m in shared_active_mounts(rootp, subject, rows, include_off=True)}
        out.extend(by[w] for w in shared_wanted if w in by)
    if out and not any(m.primary for m in out) and out[0].role == PRIVATE_ROLE:
        out[0] = replace(out[0], primary=True)   # the home is the first own one
    return out


def activate_workspace(
    root: str | Path,
    subject: str,
    repo_url: Optional[str],
    ref: str = "main",
    *,
    slug: Optional[str] = None,
    token: Optional[str] = None,
    clone: CloneFn = _git_clone,
) -> ActiveResult:
    """ADD a workspace to the subject's active set WITHOUT parking the others (the additive counterpart of
    ``swap_workspace``). Clone/restore the target into its store slot if it isn't materialized, then mark
    it active. Idempotent: activating an already-active slug is a no-op (``changed=False``).

    ``repo_url`` clones a repo (first time) / restores its slot (thereafter); pass ``slug`` to activate an
    already-parked slot directly (a repo whose tree is parked, or a ``SEED_BACKUP_SLOT`` backup). The
    private baseline is always active and needs no activation — activating its slug is a no-op.

    A clone failure raises ``CloneError`` (token-redacted, P15) WITHOUT mutating the active set."""
    assert_not_credential(repo_url)   # a secret must never reach git (repo_ref)
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)

    target_slug = (slug or "").strip() or (SEED_SLOT if not repo_url else _slug(repo_url))
    slot_dir = _slot(store, target_slug)           # KeyError: not a slot of this subject's store

    if target_slug in _normalized_active_set(state):
        return ActiveResult(subject, target_slug, changed=False)   # already active — uniform for every slug

    # The seed-slot occupant's tree already lives at <root>/<subject> — re-activating it (e.g. Personal
    # switched back on) just re-adds it to the set; never a destructive re-clone into a store slot.
    if target_slug == _seed_slot_slug(state):
        state["active_set"] = _normalized_active_set(
            {**state, "active_set": [*state.get("active_set", []), target_slug]}
        )
        _save_state(store, state)
        return ActiveResult(subject, target_slug, changed=True)

    # Materialize the slot tree OUT OF PLACE if it isn't already parked — a clone failure raises here
    # leaving the active set untouched (mirrors swap's phase-1 discipline).
    cloned = False
    if not slot_dir.exists():
        if repo_url:
            staged = store / f".staging-{target_slug}"
            if staged.exists():
                wpaths.remove_tree(staged)
            cloned = _build_attached(staged, repo_url, ref, token, clone)  # may raise CloneError (safe)
            slot_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), str(slot_dir))
        else:
            raise KeyError(target_slug)  # asked to activate a slot with no parked tree and no repo to clone

    slot = state["slots"].get(target_slug, {})
    if repo_url is not None:
        slot.update({"repo": repo_url, "ref": ref})
    else:
        slot.setdefault("repo", None)
        slot.setdefault("ref", None)
    state["slots"][target_slug] = slot
    state["active_set"] = _normalized_active_set(
        {**state, "active_set": [*state.get("active_set", []), target_slug]}
    )
    _save_state(store, state)
    return ActiveResult(subject, target_slug, changed=True, cloned=cloned)


NEW_SLUG_PREFIX = "workspace"        # fresh blank workspaces get slugs workspace-1, workspace-2, …
DEFAULT_NEW_NAME = "New workspace"   # …and this display name (New workspace, New workspace 2, …)


def _unique_new_slug(state: dict) -> str:
    """Mint a fresh, never-used slug for a brand-new blank workspace: ``workspace-<n>`` with ``n`` the
    lowest positive integer whose slug isn't already a known slot (avoids colliding with a parked/active
    workspace, the seed, or a repo slug). Deterministic and filesystem-safe."""
    taken = set(state.get("slots", {})) | {SEED_SLOT, SEED_BACKUP_SLOT, _seed_slot_slug(state)}
    n = 1
    while f"{NEW_SLUG_PREFIX}-{n}" in taken:
        n += 1
    return f"{NEW_SLUG_PREFIX}-{n}"


#: Words a NAMED workspace's slug never takes, because they already mean something when a verb
#: names a workspace: the person's own desk, the platform tiers, the seed slots.
_UNNAMEABLE_SLUGS = frozenset({PERSONAL_ALIAS, "desk", SEED_SLOT, SEED_BACKUP_SLOT,
                               "sys", "system", "_system", "global", "_global", "shared", "new"})


def _named_new_slug(state: dict, subject: str, name: str) -> Optional[str]:
    """The slug a NAMED new workspace is addressed by: its name, kebab-cased and made unique among
    the subject's slots (``eugenia-dmitry``, then ``eugenia-dmitry-2``) — so "create a workspace
    called X" gives the agent a slug the person would recognise, instead of ``workspace-1``.
    ``None`` when the name yields nothing usable (all punctuation, a reserved word, a bare number
    that could read as a person's id); the caller then falls back to the numbered slug."""
    full = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    base = full[:40].strip("-")
    if len(full) > 40 and full[40] != "-" and "-" in base:
        base = base.rsplit("-", 1)[0]       # never cut a word in half (Vexa-ai/vexa#1783)
    if not base or base.isdigit() or base in _UNNAMEABLE_SLUGS or base == str(subject):
        return None
    if not wpaths.is_workspace_name(base, tier=False):
        return None
    taken = set(state.get("slots", {})) | {SEED_SLOT, SEED_BACKUP_SLOT, _seed_slot_slug(state)}
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _unique_new_name(state: dict, base: str = DEFAULT_NEW_NAME) -> str:
    """A display name not already used by another slot: ``New workspace``, then ``New workspace 2``, …
    Keeps the list readable when several blanks are created (labels are only cosmetic, so a soft-unique
    default is enough — a user rename always wins)."""
    names = {slot.get("name") for slot in state.get("slots", {}).values() if slot.get("name")}
    if base not in names:
        return base
    n = 2
    while f"{base} {n}" in names:
        n += 1
    return f"{base} {n}"


def create_workspace(
    root: str | Path,
    subject: str,
    *,
    name: Optional[str] = None,
    slug: Optional[str] = None,
) -> ActiveResult:
    """CREATE a brand-new BLANK workspace, seeded from the validated template, at a fresh unique slug and
    ADD it to the subject's active set (the additive-model counterpart of "start fresh" — WP-A1).

    This is NOT a swap: the private baseline (``<root>/<subject>``) and every other active workspace are
    left completely untouched — nothing is parked, rebuilt, or backed up. The new workspace is materialized
    in its own store slot (``<root>/.attached/<subject>/<slug>``), git-initialized + seed-committed by the
    single seeding primitive, given a display name (``name`` if provided, else a unique "New workspace"),
    and marked active. Returns its slug (``ActiveResult.changed`` is always True — a create is never a no-op).
    """
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)

    if slug:
        # A CHOSEN SLUG (Vexa-ai/vexa#1783): taken as given, or refused — never silently changed.
        chosen = slug.strip()
        taken = set(state.get("slots", {})) | {SEED_SLOT, SEED_BACKUP_SLOT, _seed_slot_slug(state)}
        if (not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", chosen) or chosen in _UNNAMEABLE_SLUGS
                or chosen == str(subject) or chosen.isdigit()):
            raise ValueError("a slug is lowercase letters, digits and dashes, and not a reserved word")
        if chosen in taken:
            raise FileExistsError(chosen)
        target_slug = chosen
    else:
        target_slug = _named_new_slug(state, subject, name or "") or _unique_new_slug(state)
    slot_dir = store / target_slug

    # Materialize OUT OF PLACE, then move into the slot — a seeding failure leaves the active set untouched
    # (mirrors activate's phase discipline). The seed primitive git-inits + commits the fresh tree.
    staged = store / f".staging-{target_slug}"
    if staged.exists():
        wpaths.remove_tree(staged)
    _reseed(staged)
    slot_dir.parent.mkdir(parents=True, exist_ok=True)
    if slot_dir.exists():
        wpaths.remove_tree(slot_dir)
    shutil.move(str(staged), str(slot_dir))

    slot = {"repo": None, "ref": None, "name": (name or "").strip()[:80] or _unique_new_name(state)}
    state["slots"][target_slug] = slot
    state["active_set"] = _normalized_active_set(
        {**state, "active_set": [*state.get("active_set", []), target_slug]}
    )
    _save_state(store, state)
    return ActiveResult(subject, target_slug, changed=True, cloned=False)


def deactivate_workspace(root: str | Path, subject: str, slug: str) -> ActiveResult:
    """REMOVE a workspace from the subject's active set (park it — never destroyed). UNIFORM for every
    workspace, including the seed-slot one (Personal): dropping it from the set is all 'park' means; the
    tree stays on disk (a store slot, or <root>/<subject> for the seed slot), ready to re-activate.
    Idempotent: deactivating a not-active slug is a no-op."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    slug = (slug or "").strip()

    if slug not in _normalized_active_set(state):
        return ActiveResult(subject, slug, changed=False)
    state["active_set"] = [s for s in _normalized_active_set(state) if s != slug]
    _save_state(store, state)
    return ActiveResult(subject, slug, changed=True)


def _build_attached(dest: Path, repo_url: str, ref: str, token: Optional[str], clone: CloneFn) -> bool:
    """Clone a repository unchanged at the workspace root, preserving its Git metadata. True: a fresh
    clone happened."""
    incoming = dest.parent / f"{dest.name}.clone"
    if incoming.exists():
        wpaths.remove_tree(incoming)
    incoming.parent.mkdir(parents=True, exist_ok=True)
    clone(repo_url, ref, incoming, token)
    shutil.move(str(incoming), str(dest))
    return True


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
    dst = _slot(store, slug)
    if dst.exists():
        wpaths.remove_tree(dst)  # supersede a stale park (its live copy was the active one)
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
        wpaths.remove_tree(backup)
    prev_name = state["slots"].get(SEED_SLOT, {}).get("name")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(backup))
    state["slots"][SEED_BACKUP_SLOT] = {
        "repo": None, "ref": None,
        "name": f"{prev_name} (previous)" if prev_name else "default (previous)",
    }


# ── WORKSPACE STATES (Vexa-ai/vexa#1783, founder 2026-10-14) ─────────────────────────────────────────
#
# *"I can't use the software if the pile of workspaces is growing, I can't get rid of them, and I can't
# properly manage agent context."* Every workspace a person has is in exactly one of three states, per
# person, kept HERE — this attach state is the one store, extended rather than joined by a second:
#
#   ACTIVE    mounted by default: in ``active_set`` (their own) / not switched off (a shared one).
#   INACTIVE  listed, not mounted: off ``active_set`` / in ``hidden_shared`` (the per-member switch).
#   HIDDEN    not mounted, not listed, the agent is not told about it — reversible, history kept. In
#             ``hidden`` (one list for own and shared alike). The old per-slot ``archived`` flag was the
#             same state under another name and reads as hidden, so nothing needs migrating.
#
# Hiding a SHARED workspace is the member's own preference: membership and every other member's view
# are untouched. The PERSONAL workspace is never hidden (nor shared, nor deleted) — it is where a chat
# with nothing else to write to writes. Unhide means ACTIVE again.

STATES = ("active", "inactive", "hidden")
PERSONAL_REFUSAL = ("your personal workspace can't be deleted or shared. You can hide it instead: it is "
                    "then not mounted, not listed, and nothing writes to it (say \"hide Personal\"; "
                    "\"show Personal again\" brings it back)")


def _hidden_of(state: dict) -> set[str]:
    """The ids this person HID: the ``hidden`` list ∪ every slot still carrying the old ``archived``."""
    out = {str(s) for s in (state.get("hidden") or []) if isinstance(s, str)}
    for slug, slot in (state.get("slots") or {}).items():
        if isinstance(slot, dict) and slot.get("archived"):
            out.add(slug)
    return out


def hidden_set(root: str | Path, subject: str) -> set[str]:
    """The workspaces ``subject`` hid (own and shared). Fails soft to empty: a read of the set never
    breaks the read it decorates."""
    try:
        rootp = Path(root)
        _safe_subject_dir(rootp, subject)
        return _hidden_of(_load_state(_store(rootp, subject)))
    except Exception:  # noqa: BLE001
        return set()


def _personal_names(state: dict, subject: str) -> set[str]:
    return {PERSONAL_ALIAS, str(subject), SEED_SLOT, _seed_slot_slug(state)}


def workspace_states(root: str | Path, subject: str, memberships: list[dict]) -> list[dict]:
    """Every workspace ``subject`` has, with its STATE: ``{slug, kind, state}`` — the personal one
    first (slug ``personal``), then their own, then each shared one in ``memberships`` (enumeration
    only; the caller has already reconciled the membership stores). Hidden ones are included: this is
    the one answer the state verbs and "show hidden" read."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    state = _load_state(_store(rootp, subject))
    active = set(_normalized_active_set(state))
    hidden = _hidden_of(state)
    off_shared = {str(s) for s in (state.get("hidden_shared") or [])}
    seed = _seed_slot_slug(state)
    rows = [{"slug": PERSONAL_ALIAS, "kind": "personal",
             "state": "hidden" if seed in hidden else ("active" if seed in active else "inactive")}]
    for slug in sorted(state.get("slots") or {}):
        if slug in (seed, SEED_SLOT) or not isinstance(state["slots"].get(slug), dict):
            continue
        st = "hidden" if slug in hidden else ("active" if slug in active else "inactive")
        rows.append({"slug": slug, "kind": "private", "state": st})
    seen = {r["slug"] for r in rows}
    from control_plane import workspace_membership as membership
    for entry in memberships or []:
        wid = str((entry or {}).get("workspace_id") or "").strip() if isinstance(entry, dict) else ""
        if (not wid or wid in seen or wid == str(subject) or wid in membership.RESERVED_SLUGS
                or not wpaths.is_workspace_name(wid)):
            continue
        st = "hidden" if wid in hidden else ("inactive" if wid in off_shared else "active")
        rows.append({"slug": wid, "kind": "shared", "state": st,
                     "role": (entry.get("role") or "member")})
        seen.add(wid)
    return rows


def set_workspace_state(root: str | Path, subject: str, slug: str, new_state: str) -> bool:
    """Put ONE workspace in ``new_state`` (``active`` · ``inactive`` · ``hidden``) for ``subject``; returns
    whether anything changed. Raises ``ValueError`` for a state that is not one, and for hiding the
    personal workspace (``PERSONAL_REFUSAL``); ``KeyError`` for a workspace this person does not have.

    A SHARED id is any top-level workspace — membership is the ROUTE's question (activating needs it,
    switching off or hiding never does, which is how a person clears one they were removed from)."""
    if new_state not in STATES:
        raise ValueError(f"state must be one of {', '.join(STATES)}")
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    slug = (slug or "").strip()
    before = json.dumps(state, sort_keys=True)
    hidden = _hidden_of(state)
    seed = _seed_slot_slug(state)

    def _unhide(s: str) -> None:
        hidden.discard(s)
        slot = state["slots"].get(s)
        if isinstance(slot, dict) and slot.get("archived"):
            slot["archived"] = False

    if slug in _personal_names(state, subject):
        # PERSONAL CAN BE HIDDEN (founder 2026-10-15), never deleted or shared: hidden, it is not
        # mounted, not listed, not a chat's target, and the desk writers have nowhere to write.
        aset = _normalized_active_set(state)
        if new_state == "active":
            hidden.discard(seed)
            if seed not in aset:
                state["active_set"] = [*aset, seed]
        elif new_state == "inactive":
            hidden.discard(seed)
            state["active_set"] = [s for s in aset if s != seed]
        else:
            hidden.add(seed)
            state["active_set"] = [s for s in aset if s != seed]
    elif slug in (state.get("slots") or {}) and slug != SEED_SLOT:
        aset = _normalized_active_set(state)
        if new_state == "active":
            _unhide(slug)
            if slug not in aset:
                if not _slot(store, slug).exists():
                    raise KeyError(slug)          # a slot record with no tree is nothing to mount
                aset = [*aset, slug]
        else:
            aset = [s for s in aset if s != slug]
            if new_state == "hidden":
                hidden.add(slug)
                state["slots"][slug]["archived"] = True   # the older Files view groups it as archived
            else:
                _unhide(slug)
        state["active_set"] = aset
    else:
        from control_plane import workspace_membership as membership
        if (not wpaths.is_workspace_name(slug, tier=False) or slug in membership.RESERVED_SLUGS
                or slug in (SEED_BACKUP_SLOT,)):
            raise KeyError(slug)
        known = (rootp / slug).is_dir() or slug in hidden or slug in (state.get("hidden_shared") or [])
        if not known:
            raise KeyError(slug)
        off = {str(s) for s in (state.get("hidden_shared") or [])}
        if new_state == "active":
            hidden.discard(slug)
            off.discard(slug)
        elif new_state == "inactive":
            hidden.discard(slug)
            off.add(slug)
        else:
            hidden.add(slug)
            off.add(slug)      # a reader of the older switch alone still sees it as off
        state["hidden_shared"] = sorted(off)
    state["hidden"] = sorted(hidden)
    changed = json.dumps(state, sort_keys=True) != before
    if changed:
        _save_state(store, state)
    return changed


def set_workspace_states(root: str | Path, subject: str, slugs, new_state: str,
                         memberships: list[dict]) -> list[dict]:
    """``set_workspace_state`` for many: ``slugs`` is a list, or ``"all"`` — every workspace the person
    has (``workspace_states``). Under ``"all"`` the personal workspace is SKIPPED for ``hidden`` (hide all
    = everything that can be hidden), and ``active`` means every hidden or inactive one comes back.
    Returns ``[{slug, state, changed}]``; a named workspace that fails raises (nothing after it runs)."""
    if slugs == "all":
        rows = workspace_states(root, subject, memberships)
        targets = [r["slug"] for r in rows if not (new_state == "hidden" and r["kind"] == "personal")]
    else:
        targets = [str(s).strip() for s in (slugs or []) if str(s).strip()]
    out = []
    for s in targets:
        out.append({"slug": s, "state": new_state,
                    "changed": set_workspace_state(root, subject, s, new_state)})
    return out


# ── CHAT PROFILES (Vexa-ai/vexa#1783, founder 2026-10-14) ────────────────────────────────────────
#
# A profile is a named preset of a chat's scope — where it writes, what it reads — plus a memory
# mode. Kept in this same attach state (``profiles``, ``default_profile``): no new store. The four
# starters exist for everyone; a person edits work / study / hobby. ``incognito`` is fixed: no
# workspaces, no memory, never read by another chat, out of chat search.

# Founder 2026-10-15: "remove the prebuilt profiles, they will create one if needed." Incognito is the
# one built-in (it is not a scope a person could build from workspaces). Starters that were never
# edited are therefore simply gone; one a person edited is stored and stays.
STARTER_PROFILES = ("incognito",)
INCOGNITO = "incognito"
SHARED_WORK = "shared work"
#: A profile may name VARIABLES — workspace references bound when it is applied ("use shared work for
#: Collab Room"). `{{workspace}}` is the one the built-ins use.
WORKSPACE_VAR = "{{workspace}}"
#: The scope a SHARED chat gets — writes to and reads only the workspace it is bound to. Not a listed
#: profile any more (no prebuilt profiles); applied unnamed when a link is opened or a chat is shared.
STARTER_SCOPES = {SHARED_WORK: {"target": WORKSPACE_VAR, "reads": [WORKSPACE_VAR]}}


def chat_profiles(root: str | Path, subject: str,
                  company: Optional[dict] = None) -> tuple[list[dict], str]:
    """``([{name, target, reads, incognito, variables, company}], default_profile)``. ``target`` None =
    Personal; ``reads`` None = the person's default active set. ``company`` is the profiles the
    company seeds from `_global/PROFILES.md`: a starter's scope < the company's < the person's edit."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    state = _load_state(_store(rootp, subject))
    stored = state.get("profiles") if isinstance(state.get("profiles"), dict) else {}
    company = {str(k).strip().lower(): v for k, v in (company or {}).items()
               if isinstance(v, dict) and str(k).strip().lower() != INCOGNITO}
    names = [*STARTER_PROFILES]
    names += [n for n in sorted(company) if n not in names]
    names += [n for n in sorted(stored) if n not in names]
    out = []
    for name in names:
        rec = {**STARTER_SCOPES.get(name, {}),
               **{k: v for k, v in company.get(name, {}).items() if k in ("target", "reads")},
               **(stored.get(name) if isinstance(stored.get(name), dict) else {})}
        inc = name == INCOGNITO
        target = None if inc else (rec.get("target") or None)
        reads = [] if inc else (rec.get("reads") if isinstance(rec.get("reads"), list) else None)
        variables = sorted({v for v in [target, *(reads or [])] if isinstance(v, str)
                            and v.startswith("{{") and v.endswith("}}")})
        out.append({"name": name, "target": target, "reads": reads, "incognito": inc,
                    "variables": variables, "company": name in company})
    default = str(state.get("default_profile") or "")
    # a default naming a profile that no longer exists (a never-edited starter) is no default
    return out, (default if any(p["name"] == default for p in out) else "")


def delete_chat_profile(root: str | Path, subject: str, name: str) -> bool:
    """Remove a profile the person made, or put a starter back as it was. A default that named it is
    cleared. Returns whether anything changed."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    name = (name or "").strip().lower()
    profiles = state.get("profiles") if isinstance(state.get("profiles"), dict) else {}
    changed = profiles.pop(name, None) is not None
    if state.get("default_profile") == name:
        state["default_profile"] = ""
        changed = True
    if changed:
        state["profiles"] = profiles
        _save_state(store, state)
    return changed


def queue_profile(root: str | Path, subject: str, name: str, bind: dict) -> None:
    """The NEXT new chat of this person starts in ``name`` with ``bind`` (Vexa-ai/vexa#1783): what
    opening a workspace invite link does — "shared work" bound to that workspace. Consumed once."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    state["queued_profile"] = {"name": str(name), "bind": {str(k): str(v) for k, v in (bind or {}).items()}}
    _save_state(store, state)


def take_queued_profile(root: str | Path, subject: str) -> Optional[dict]:
    """The queued profile, removed — or None."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    q = state.pop("queued_profile", None)
    if q is None:
        return None
    _save_state(store, state)
    return q if isinstance(q, dict) and q.get("name") else None


def edit_chat_profile(root: str | Path, subject: str, name: str, *, target=..., reads=...,
                      default: Optional[bool] = None) -> dict:
    """Change one profile (``...`` leaves a field alone). Incognito's scope cannot be edited; it can
    be the default. A new name makes a new profile."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    name = (name or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9 _-]{0,31}", name):
        raise ValueError("a profile name is a short word")
    profiles = state.get("profiles") if isinstance(state.get("profiles"), dict) else {}
    rec = dict(profiles.get(name) or {})
    if name == INCOGNITO and (target is not ... or reads is not ...):
        raise ValueError("incognito has no workspaces; it cannot be given any")
    if target is not ...:
        rec["target"] = target or None
    if reads is not ...:
        rec["reads"] = list(reads) if reads is not None else None
    profiles[name] = rec
    state["profiles"] = profiles
    if default is True:
        state["default_profile"] = name
    elif default is False and state.get("default_profile") == name:
        state["default_profile"] = ""
    _save_state(store, state)
    return {"name": name, **rec}


def bind_repository_credential(root: str | Path, subject: str, slug: str, credential_workspace: str) -> None:
    """Persist a credential reference, never key material; callers enforce ownership."""
    store = _store(Path(root), subject)
    state = _load_state(store)
    if slug not in state["slots"]:
        raise KeyError(slug)
    state["slots"][slug]["credential_workspace"] = credential_workspace
    _save_state(store, state)


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


# ── ATTACHING AN EXISTING REPO TO A *SHARED* WORKSPACE (the group lane) ────────────────────────────
#
# Everything above is written against ONE subject's store: the tree at ``<root>/<subject>`` and the
# parking slots under ``<root>/.attached/<subject>/``. A shared (group) workspace has neither — it is a
# top-level tree at ``<root>/<workspace_id>`` owned by a member list, not by a subject.
#
# So the MECHANIC is lifted out of the subject: ``attach_repo_at(active_dir, store, …)`` parks
# whatever is live and clones/restores the requested repo, over any pair of paths. ``swap_workspace``
# keeps its own copy of that dance because it also has to maintain the subject's active-set bookkeeping
# (which a shared workspace does not have); everything that actually touches disk — ``_build_attached``,
# ``_park``, ``_reseed``, ``_slug``, the state file — is the same code in both.
#
# ONE thing is genuinely different, and it is load-bearing: a shared workspace's MEMBER LIST lives
# INSIDE its git tree (``policy/members.json`` — see workspace_membership; its INVITES do not, they
# live outside every workspace since Vexa-ai/vexa#1645 and so survive an attach without being
# carried anywhere).
# Replacing that tree with somebody's repo would therefore delete everyone's access along with it:
# ``is_member`` would answer None for every member, the workspace would vanish from every active set,
# and the only person who could still reach it would be nobody. So the policy directory is CARRIED
# ACROSS every attach — copied out of the tree being parked, into the tree being activated, and
# committed. Membership survives a repo swap; the repo never carries our policy home to GitHub as a
# surprise either, because it arrives as an ordinary commit the group can see.

SHARED_STORE_DIRNAME = ".attached-shared"   # <root>/.attached-shared/<workspace_id>/<slug> (dot ⇒ skipped by every scan)
POLICY_DIRNAME = "policy"                   # the member list — travels across an attach


def shared_store(root: str | Path, workspace_id: str) -> Path:
    """Where a shared workspace's parked trees live. Dot-prefixed at the top level, so it is invisible
    to ``scan_workspace_subjects``, the Workspace tree and the membership listing alike."""
    return Path(root) / SHARED_STORE_DIRNAME / _safe_workspace_id(workspace_id)


def _safe_workspace_id(workspace_id: str) -> str:
    wid = (workspace_id or "").strip()
    if not wpaths.is_workspace_name(wid, tier=False) or wid in ("seed", SEED_BACKUP_SLOT):
        raise ValueError("invalid workspace id")
    return wid


def _shared_dir(root: Path, workspace_id: str) -> Path:
    """``<root>/<workspace_id>``, traversal-guarded (mirrors ``workspace_membership._ws_dir``)."""
    rootp = Path(root).resolve()
    ws = (rootp / _safe_workspace_id(workspace_id)).resolve()
    if ws == rootp or rootp not in ws.parents:
        raise ValueError("invalid workspace id")
    return ws


def _plain_state(store: Path) -> dict:
    """A shared workspace's attach state — ``{"active": slug|None, "slots": {slug: {repo, ref, …}}}``.
    Deliberately NOT ``_load_state``: the active-set/flat-model migration there is about a subject's
    mount set, and a shared workspace has no mount set of its own (each member mounts it or does not)."""
    f = store / STATE_FILENAME
    data: dict = {"active": None, "slots": {}}
    if f.exists():
        try:
            loaded = json.loads(f.read_text())
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            pass
    data.setdefault("active", None)
    if not isinstance(data.get("slots"), dict):
        data["slots"] = {}
    return data


def carry_policy(src: Path, dest: Path) -> list[str]:
    """Copy the membership policy (``policy/*.json``) from the tree being parked into the tree being
    activated. Returns the relative paths carried (empty when there was no policy).

    Without this an attach is an ACCESS WIPE: the member list is a file inside the workspace's own tree,
    so a clone that replaces the tree replaces the member list with nothing — ``is_member`` would answer
    None for everyone and the workspace would drop out of every member's active set.

    The carried policy is deliberately left UNTRACKED, and ``policy/`` is added to the clone's
    ``.git/info/exclude`` (a local-only ignore — their ``.gitignore`` is not touched). Two reasons, both
    load-bearing:

    * **It is not theirs to receive.** ``members.json`` is our internal subject ids and member emails.
      Committing it would put it in the next push to somebody else's repository.
    * **A commit would diverge the workspace on arrival.** An attached clone is level with its remote;
      one local commit on top means the first ``pull`` after an attach is refused as a divergence — the
      exact thing a person does immediately after loading an existing workspace.

    Authority is unaffected: ``workspace_membership.read_members`` reads the file from the working tree,
    not from git history."""
    carried: list[str] = []
    # BOTH TREES ARE WRITABLE BY THE MODEL'S TOOLS, so the copy is read and written through
    # `workspace_paths`: a `policy` that is a link in either tree, or a `*.json` that is a link,
    # is neither read through (another workspace's list carried in) nor written through (this one
    # carried out). Only regular files directly in a real `policy/` are carried.
    for name in wpaths.list_files_inside(src, POLICY_DIRNAME, suffix=".json"):
        rel = f"{POLICY_DIRNAME}/{name}"
        text = wpaths.read_text_inside(src, rel)
        if text is None:
            continue
        try:
            wpaths.write_text_inside(dest, rel, text)
        except (wpaths.PathRefused, OSError) as exc:
            log.warning("membership policy %s not carried into %s: %s", rel, dest, exc)
            continue
        carried.append(rel)
    if carried:
        _exclude_locally(dest, f"/{POLICY_DIRNAME}/")
    return carried


def _exclude_locally(ws: Path, pattern: str) -> None:
    """Add ``pattern`` to ``.git/info/exclude`` — git's per-clone ignore file. Local to this checkout,
    never committed, and it does not touch a repository's own ``.gitignore``. Idempotent. Never
    through a link at ``.git``, ``info`` or the file (``workspace_paths.ensure_git_exclude``)."""
    try:
        wpaths.ensure_git_exclude(ws, pattern)
    except (wpaths.PathRefused, OSError):
        log.warning("could not write .git/info/exclude in %s", ws)


def attach_repo_at(
    active_dir: Path,
    store: Path,
    repo_url: Optional[str],
    ref: str = "main",
    *,
    slug: Optional[str] = None,
    token: Optional[str] = None,
    clone: CloneFn = _git_clone,
    carry: Optional[Path] = None,
) -> SwapResult:
    """THE MECHANIC, over any pair of paths: park what is live at ``active_dir`` into ``store``, then
    put ``repo_url`` there — restored from a prior park if we have seen it, cloned fresh if not.

    Same two-phase discipline as ``swap_workspace``: phase 1 builds the new tree OUT OF PLACE, so a
    clone failure (bad ref, private repo, no credential) raises with the live tree untouched; phase 2
    is local moves only. ``slug`` addresses a parked slot directly (that is how you swap BACK, with no
    re-clone and every local commit intact); ``repo_url=None`` with no slug means the seed slot.

    ``carry`` names a directory to copy from the parked tree into the new one before it goes live —
    the shared lane passes ``policy/`` so a repo attach cannot delete the member list."""
    assert_not_credential(repo_url)   # a secret must never reach git (repo_ref)
    active_dir = Path(active_dir)
    store = Path(store)
    state = _plain_state(store)
    target_slug = (slug or "").strip() or (SEED_SLOT if not repo_url else _slug(repo_url))
    parked_target = _slot(store, target_slug)      # KeyError: not a slot of this workspace's store

    live = (active_dir / ".git").exists()
    if state.get("active") == target_slug and live:
        slot = state["slots"].get(target_slug, {})
        return SwapResult(active_dir.name, target_slug, slot.get("repo"), slot.get("ref"),
                          swapped=False, cloned=False, parked_slug=None)
    if target_slug == SEED_SLOT and state.get("active") is None and live:
        return SwapResult(active_dir.name, SEED_SLOT, None, None,
                          swapped=False, cloned=False, parked_slug=None)

    # ── PHASE 1 — build out of place (a failure here leaves everything exactly as it was) ──────────
    cloned = False
    restore = False
    if parked_target.exists():
        staged, restore = parked_target, True
    elif target_slug == SEED_SLOT:
        staged = store / ".staging-seed"
        if staged.exists():
            wpaths.remove_tree(staged)
        _reseed(staged)
    elif repo_url:
        staged = store / f".staging-{target_slug}"
        if staged.exists():
            wpaths.remove_tree(staged)
        cloned = _build_attached(staged, repo_url, ref, token, clone)  # may raise CloneError
    else:
        raise KeyError(target_slug)

    # ── PHASE 2 — commit: park the live tree (kept, never destroyed), then activate the staged one ──
    parked_slug: Optional[str] = None
    has_active = live or (active_dir.exists() and any(active_dir.iterdir()))
    if has_active:
        parked_slug = state.get("active") or SEED_SLOT
        _park(store, parked_slug, active_dir)
        state["slots"].setdefault(parked_slug, {"repo": None, "ref": None})
    elif active_dir.exists():
        wpaths.remove_tree(active_dir)

    active_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staged), str(active_dir))
    if has_active and carry is not None:
        carry_policy(store / parked_slug, active_dir)   # the parked tree is where the live one now is

    if not restore:
        slot = state["slots"].get(target_slug, {})
        slot.update({"repo": repo_url, "ref": ref})
        state["slots"][target_slug] = slot
    state["active"] = target_slug
    _save_state(store, state)
    slot = state["slots"].get(target_slug, {})
    return SwapResult(active_dir.name, target_slug, slot.get("repo"), slot.get("ref"),
                      swapped=True, cloned=cloned, parked_slug=parked_slug)


def attach_shared_workspace(
    root: str | Path,
    workspace_id: str,
    repo_url: Optional[str],
    ref: str = "main",
    *,
    slug: Optional[str] = None,
    token: Optional[str] = None,
    clone: CloneFn = _git_clone,
) -> SwapResult:
    """Load an EXISTING git repo into a shared (group) workspace, keeping the group's membership.

    The group's current tree is parked under ``<root>/.attached-shared/<workspace_id>/<slug>`` (kept,
    swappable-back), the repo is cloned into ``<root>/<workspace_id>``, and ``policy/`` is carried
    across so nobody loses access. Authorization is the ROUTE's job — this function is the mechanic and
    trusts its caller, exactly as ``swap_workspace`` trusts ``subject_of(request)``."""
    rootp = Path(root)
    ws = _shared_dir(rootp, workspace_id)
    return attach_repo_at(ws, shared_store(rootp, workspace_id), repo_url, ref,
                          slug=slug, token=token, clone=clone, carry=Path(POLICY_DIRNAME))


def shared_attached_state(root: str | Path, workspace_id: str) -> dict:
    """A shared workspace's attachment view — the active slug plus the parked slots available to swap
    back to. Read-only and safe before any attach (a never-attached workspace returns the empty shape)."""
    rootp = Path(root)
    _shared_dir(rootp, workspace_id)   # traversal guard, and a 400 for a nonsense id
    state = _plain_state(shared_store(rootp, workspace_id))
    return {"workspace_id": workspace_id, "active": state.get("active"), "slots": state.get("slots", {})}


def desk_hidden(root: str | Path, subject: str) -> bool:
    """Has the person HIDDEN (switched off, deleted) their Personal workspace — the tree at
    ``<root>/<subject>``? A Personal that is merely not in the active set is still theirs and still
    the desk; a hidden one is never read as "personal" nor written to (#1783)."""
    rootp = Path(root)
    try:
        state = _load_state(_store(rootp, subject))
    except Exception:  # noqa: BLE001
        return False
    return _seed_slot_slug(state) in _hidden_of(state)


class NoHome(Exception):
    """A write that names no workspace, for a person whose Personal workspace is hidden and whose
    request carries no target workspace (#1783): refused, never sent to a workspace picked by order
    — a shared one is somebody else's room too."""
    message = ("Your Personal workspace is switched off and nothing names a workspace for this. "
               "Choose one (the chat's \"Writes to\" chip), or turn Personal back on.")


def home_dir(root: str | Path, subject: str) -> Path:
    """THE PERSON'S HOME — where a write that names no workspace lands (#1783): their Personal
    workspace (the desk, ``<root>/<subject>``) while it is on. Once they HIDE it (switch it off,
    delete it) there is NO home: ``NoHome`` is raised, and the caller names a workspace (the chat's
    target) or refuses. Never a workspace picked by its place in a list, never a shared one."""
    rootp = Path(root)
    desk = _safe_subject_dir(rootp, subject)
    if desk_hidden(rootp, subject):
        raise NoHome(subject)
    return desk


def home_slug(root: str | Path, subject: str) -> str:
    """The slug that names ``home_dir`` on the routes: ``""`` (the desk). Raises ``NoHome`` alike."""
    home_dir(root, subject)
    return ""
