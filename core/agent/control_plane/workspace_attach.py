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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from shared.gitenv import scrubbed_git_env
from shared.seeding import resolve_seed_dir, seed_workspace, validate_seed

log = logging.getLogger(__name__)

STORE_DIRNAME = ".attached"
STATE_FILENAME = "state.json"
SEED_SLOT = "seed"  # the reserved slug for the original template-seeded workspace
SEED_BACKUP_SLOT = "seed-prev"  # where 'start fresh' tucks the displaced default so it stays recoverable

# The subject's PRIVATE baseline — the workspace at ``<root>/<subject>`` that a turn always mounts. Its
# slug in the active set is whatever ``state.active`` points at (the seed by default). This slug marks
# the workspace that lives at the legacy in-place path and is ALWAYS active + non-deactivatable.
PRIVATE_ROLE = "private"
# A SHARED workspace mount (Lane A): a workspace the subject is a MEMBER of (not their own), resolved from
# the membership index + authoritatively re-checked against the workspace's own policy/members.json. Write
# access is gated by the member's role (contributor/owner write; viewer is read-only).
SHARED_ROLE = "shared"

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
    primary: bool = False  # True == the private baseline at <root>/<subject> (always active, never deactivatable)
    name: Optional[str] = None  # the DISPLAY label (switcher rename), so KNOWLEDGE shows the same name as the switcher — not the raw slug


@dataclass(frozen=True)
class ActiveResult:
    """Outcome of one activate/deactivate — for the API body and tests."""

    subject: str
    slug: str
    changed: bool             # False == already in the desired state (idempotent no-op)
    cloned: bool = False      # True == a fresh git clone happened (activate of a never-seen repo)
    nested: bool = False


def _slug_dir(root: Path, subject: str, state: dict, slug: str) -> Path:
    """Where a slug's workspace tree lives on disk: the PRIMARY (the private baseline) is in place at
    ``<root>/<subject>``; every other slug lives in its store slot ``<root>/.attached/<subject>/<slug>``.
    Active-but-secondary and parked members share the store slot — the only difference is set membership."""
    if slug == _primary_slug(state):
        return _safe_subject_dir(root, subject)
    return _store(root, subject) / slug


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
    # scrubbed env: a hook-exported GIT_DIR would re-point every op below at the hook's repo
    # (see shared/gitenv.py); prompts stay disabled so a bad credential fails loud.
    env = scrubbed_git_env(GIT_ASKPASS="true", GIT_TERMINAL_PROMPT="0")
    url = _authenticated_url(repo_url, token)

    def redact(text: str) -> str:
        return text.replace(token, "***") if token else text

    try:
        subprocess.run(["git", "clone", "--quiet", url, str(dest)],
                       check=True, capture_output=True, text=True, env=env)
        if token:  # never persist the credential in the cloned repo's origin (P15)
            subprocess.run(["git", "-C", str(dest), "remote", "set-url", "origin", repo_url],
                           check=True, capture_output=True, text=True, env=env)
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
        return {"active": None, "slots": {}, "active_set": []}
    try:
        data = json.loads(f.read_text())
    except (OSError, json.JSONDecodeError):
        return {"active": None, "slots": {}, "active_set": []}
    if not isinstance(data, dict):
        return {"active": None, "slots": {}, "active_set": []}
    data.setdefault("active", None)
    data.setdefault("slots", {})
    # ``active_set`` (the additive mount set — WP-A2.1) is an ORDERED list of active slugs. A state.json
    # written before this field existed carries none; back-compat is derive-from-``active``: the single
    # active workspace becomes the sole member of the set. The private baseline (``active`` / the seed)
    # is always the FIRST member (normalized on every read/write). Unknown/duplicate slugs are dropped.
    if not isinstance(data.get("active_set"), list):
        data["active_set"] = []
    return data


def _primary_slug(state: dict) -> str:
    """The PRIVATE baseline slug — the workspace that lives at ``<root>/<subject>`` and is always active
    + non-deactivatable. A never-swapped subject (``active`` is None) is on the seed."""
    return state.get("active") or SEED_SLOT


def _normalized_active_set(state: dict) -> list[str]:
    """The ordered active set with the private baseline FORCED first and present, duplicates and unknown
    slugs (no parked tree, not the primary) dropped. Back-compat: an empty set (pre-``active_set`` state)
    yields just the primary — exactly today's single-active behavior."""
    primary = _primary_slug(state)
    ordered: list[str] = [primary]
    for slug in state.get("active_set", []):
        if slug == primary or slug in ordered:
            continue
        # a secondary member must have a parked tree (its on-disk home) OR a slot record (repo to restore)
        if slug in state.get("slots", {}):
            ordered.append(slug)
    return ordered


def _save_state(store: Path, state: dict) -> None:
    store.mkdir(parents=True, exist_ok=True)
    (store / STATE_FILENAME).write_text(json.dumps(state, indent=2, sort_keys=True))


def attached_workspaces(root: str | Path, subject: str) -> dict:
    """The subject's attachment view: which slug is active (the private baseline), the parked slots
    (slug → repo/ref), and the ordered ``active_set`` (the mount set — WP-A2.1). Read-only; safe to call
    before any swap (returns the empty shape). The ``active_set`` is normalized (primary first) so the
    terminal can render the per-row active toggle directly off it."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    state = _load_state(_store(rootp, subject))
    state["active_set"] = _normalized_active_set(state)
    return state


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
    # The PRIMARY moved: the new active is the private baseline (first in the set). Any SECONDARY active
    # members (added via ``activate_workspace``) survive the swap — a swap only re-homes the primary, it
    # must not silently drop the rest of the mount set. ``_normalized_active_set`` re-orders with the new
    # primary first and prunes the old primary if it is no longer a set member.
    prior_secondaries = [s for s in state.get("active_set", []) if s != target_slug]
    state["active_set"] = _normalized_active_set({**state, "active_set": prior_secondaries})
    _save_state(store, state)
    slot = state["slots"].get(target_slug, {})
    return SwapResult(subject, target_slug, slot.get("repo"), slot.get("ref"),
                      swapped=True, cloned=cloned, parked_slug=parked_slug, nested=bool(slot.get("nested")))


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
    primary = _primary_slug(state)
    mounts: list[ActiveMount] = []
    for slug in _normalized_active_set(state):
        slot = state["slots"].get(slug, {})
        mounts.append(ActiveMount(
            slug=slug,
            repo=slot.get("repo"),
            ref=slot.get("ref"),
            role=PRIVATE_ROLE,   # every workspace a subject owns is 'private' for now; 'shared'/'system' land later
            path=str(_slug_dir(rootp, subject, state, slug)),
            write=True,
            primary=(slug == primary),
            name=(slot.get("name") or "").strip() or None,   # the switcher's display label (rename), if set
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
        shutil.rmtree(staged)
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
        hidden.discard(workspace_id)
    else:
        hidden.add(workspace_id)
    state["hidden_shared"] = sorted(hidden)
    _save_state(store, state)


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
    store = _store(rootp, subject)
    state = _load_state(store)
    primary = _primary_slug(state)

    if slug == primary:
        raise ValueError("the baseline workspace can't be shared — create a new workspace to share")
    # already a top-level shared workspace the caller is a member of → nothing to do
    if (rootp / slug).exists() and membership.is_member(rootp, slug, subject) is not None:
        return slug, False

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
    if slug == _primary_slug(state):
        raise ValueError("the baseline workspace can't be archived")
    slot = state["slots"].get(slug)
    if slot is None:
        raise KeyError(slug)
    slot["archived"] = bool(archived)
    state["slots"][slug] = slot
    if archived:
        state["active_set"] = [s for s in state.get("active_set", []) if s != slug]
    _save_state(store, state)


def delete_workspace(root: str | Path, subject: str, slug: str) -> None:
    """DELETE one of the subject's OWN workspaces — REMOVE its tree from the private store and drop the slot.
    DESTRUCTIVE + irreversible. The baseline / seed slot is refused. Guarded so it can only remove a path
    UNDER the subject's own store (never the baseline, never outside root)."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    if slug in (_primary_slug(state), SEED_SLOT):
        raise ValueError("the baseline workspace can't be deleted")
    if slug not in state["slots"]:
        raise KeyError(slug)
    slot_dir = (store / slug).resolve()
    if store.resolve() in slot_dir.parents and slot_dir.exists():  # only ever a slot under this subject's store
        shutil.rmtree(slot_dir, ignore_errors=True)
    state["slots"].pop(slug, None)
    state["active_set"] = [s for s in state.get("active_set", []) if s != slug]
    _save_state(store, state)


def ensure_workspace_private(root: str | Path, subject: str, workspace_id: str) -> str:
    """UN-SHARE — the mirror of ``ensure_workspace_shareable``. Move a top-level shared workspace back into
    the caller's private store as a normal workspace (git history intact) and return its new private slug.
    The caller must be the owner (the route enforces it); membership records are cleaned up by the caller.
    After this, the workspace is private again — other members lose access (its top-level path is gone)."""
    rootp = Path(root).resolve()
    _safe_subject_dir(rootp, subject)
    ws = (rootp / workspace_id).resolve()
    if not ws.exists() or rootp not in ws.parents:
        raise KeyError(workspace_id)
    store = _store(rootp, subject)
    state = _load_state(store)
    new_slug = _unique_new_slug(state)
    slot_dir = store / new_slug
    slot_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(ws), str(slot_dir))  # re-home the tree into the private .attached store
    state["slots"][new_slug] = {"repo": None, "ref": None, "name": workspace_id}  # keep the name as the label
    state["active_set"] = _normalized_active_set({**state, "active_set": [*state.get("active_set", []), new_slug]})
    _save_state(store, state)
    return new_slug


def shared_active_mounts(root: str | Path, subject: str, memberships: list[dict]) -> list[ActiveMount]:
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
    hidden = hidden_shared_set(rootp, subject)  # workspaces the subject switched OFF (not mounted until re-enabled)
    mounts: list[ActiveMount] = []
    for entry in memberships:
        ws_id = (entry.get("workspace_id") or "").strip() if isinstance(entry, dict) else ""
        if not ws_id or ws_id == subject or ws_id.startswith(".") or ws_id in membership.RESERVED_SLUGS:
            continue  # own baseline / reserved / dot-namespaced are never shared mounts
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
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)

    target_slug = (slug or "").strip() or (SEED_SLOT if not repo_url else _slug(repo_url))
    primary = _primary_slug(state)

    # The private baseline is unconditionally active — activating it (or the seed on a never-swapped
    # subject) is a no-op, never a destructive re-clone into the store slot.
    already = target_slug in _normalized_active_set(state)
    if target_slug == primary or already:
        return ActiveResult(subject, target_slug, changed=False)

    # Materialize the slot tree OUT OF PLACE if it isn't already parked — a clone failure raises here
    # leaving the active set untouched (mirrors swap's phase-1 discipline).
    slot_dir = store / target_slug
    cloned = nested = False
    if not slot_dir.exists():
        if repo_url:
            staged = store / f".staging-{target_slug}"
            if staged.exists():
                shutil.rmtree(staged)
            cloned, nested = _build_attached(staged, repo_url, ref, token, clone)  # may raise CloneError (safe)
            slot_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged), str(slot_dir))
        else:
            raise KeyError(target_slug)  # asked to activate a slot with no parked tree and no repo to clone

    slot = state["slots"].get(target_slug, {})
    if repo_url is not None:
        slot.update({"repo": repo_url, "ref": ref, "nested": nested})
    else:
        slot.setdefault("repo", None)
        slot.setdefault("ref", None)
    state["slots"][target_slug] = slot
    state["active_set"] = _normalized_active_set(
        {**state, "active_set": [*state.get("active_set", []), target_slug]}
    )
    _save_state(store, state)
    return ActiveResult(subject, target_slug, changed=True, cloned=cloned, nested=nested)


NEW_SLUG_PREFIX = "workspace"        # fresh blank workspaces get slugs workspace-1, workspace-2, …
DEFAULT_NEW_NAME = "New workspace"   # …and this display name (New workspace, New workspace 2, …)


def _unique_new_slug(state: dict) -> str:
    """Mint a fresh, never-used slug for a brand-new blank workspace: ``workspace-<n>`` with ``n`` the
    lowest positive integer whose slug isn't already a known slot (avoids colliding with a parked/active
    workspace, the seed, or a repo slug). Deterministic and filesystem-safe."""
    taken = set(state.get("slots", {})) | {SEED_SLOT, SEED_BACKUP_SLOT, _primary_slug(state)}
    n = 1
    while f"{NEW_SLUG_PREFIX}-{n}" in taken:
        n += 1
    return f"{NEW_SLUG_PREFIX}-{n}"


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

    target_slug = _unique_new_slug(state)
    slot_dir = store / target_slug

    # Materialize OUT OF PLACE, then move into the slot — a seeding failure leaves the active set untouched
    # (mirrors activate's phase discipline). The seed primitive git-inits + commits the fresh tree.
    staged = store / f".staging-{target_slug}"
    if staged.exists():
        shutil.rmtree(staged)
    _reseed(staged)
    slot_dir.parent.mkdir(parents=True, exist_ok=True)
    if slot_dir.exists():
        shutil.rmtree(slot_dir)
    shutil.move(str(staged), str(slot_dir))

    slot = {"repo": None, "ref": None, "name": (name or "").strip()[:80] or _unique_new_name(state)}
    state["slots"][target_slug] = slot
    state["active_set"] = _normalized_active_set(
        {**state, "active_set": [*state.get("active_set", []), target_slug]}
    )
    _save_state(store, state)
    return ActiveResult(subject, target_slug, changed=True, cloned=False)


def deactivate_workspace(root: str | Path, subject: str, slug: str) -> ActiveResult:
    """REMOVE a workspace from the subject's active set (park it — never destroyed). The parked tree stays
    in its store slot, ready to re-activate. Idempotent: deactivating a not-active slug is a no-op.

    The private baseline (``<root>/<subject>``) is ALWAYS active and cannot be deactivated — that is the
    subject's durable memory root; attempting it raises ``ValueError``."""
    rootp = Path(root)
    _safe_subject_dir(rootp, subject)
    store = _store(rootp, subject)
    state = _load_state(store)
    slug = (slug or "").strip()

    if slug == _primary_slug(state):
        raise ValueError("cannot deactivate the private baseline workspace")
    if slug not in _normalized_active_set(state):
        return ActiveResult(subject, slug, changed=False)

    # The secondary member already lives in its store slot (activate materialized it there) — dropping it
    # from the set is all that 'park' means here; nothing to move, nothing destroyed.
    state["active_set"] = [s for s in _normalized_active_set(state) if s != slug]
    _save_state(store, state)
    return ActiveResult(subject, slug, changed=True)


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
    an empty diff. Scrubbed env — a hook-exported GIT_DIR must never make this commit land on the
    hook's repo (see shared/gitenv.py)."""
    env = scrubbed_git_env()
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True, capture_output=True, text=True, env=env)
    subprocess.run(["git", "-C", str(ws), "commit", "-q", "-m", message, "--allow-empty"],
                   check=True, capture_output=True, text=True, env=env)


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
