"""workspace_ids.py — the server's registry of workspace identities: id → where it is NOW.

PRD decision 26.1. The id itself lives inside the workspace (``shared/workspace_id.py``, the
authoritative half). This module is the **derived index**: given an id, which slug and directory
holds it today, what is it called, what kind is it, and whose is it.

Two halves, and the split is the point:

    the FILE      immutable, travels with the tree through park/restore/clone/promote
    the REGISTRY  mutable, answers "where is it now" and "what is it called now"

A NAME IS ONLY IN THE REGISTRY. Not in the file, not in the directory name. That is what makes a
rename a one-line write instead of a migration, and it is the whole of decision 26's promise that
"names, slugs and paths are display and may change". ``create_shared_workspace_dir`` already threw
the human name away — it slugified it into a directory and nothing kept the original — so the
registry is not a new store here, it is the first place that name has ever been kept.

REBUILDABLE, ALWAYS. Every field except ``name`` is recomputable by walking the workspace root
(``migrate``), so a redis loss costs the display names and nothing else. That is deliberate: an
index you cannot rebuild is a second source of truth, and a second source of truth is what this
whole decision exists to remove.

Backed by redis when a client is wired, in memory otherwise — the same shape and the same fallback
discipline as ``_Sessions`` and ``ScaffoldStore``, so the unit tests need no redis.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from shared.gitenv import scrubbed_git_env
from shared.workspace_id import (KINDS, WORKSPACE_JSON, ensure_workspace_json, is_workspace_id,
                                 read_workspace_json, write_workspace_json)

logger = logging.getLogger("agent_api.workspace_ids")

# The three access answers a reader can get for a link (decision 26.3). They are a CLOSED set and
# none of them is an error: "not yours" is a normal, designed outcome — *"If a workspace is not
# available, it's okay — by design."*
ACCESS_READABLE = "readable"
ACCESS_NOT_YOURS = "not-yours"
ACCESS_GONE = "gone"

GLOBAL_SLUG = "_global"
# Never a workspace with an identity: the private system tier (chats/sessions/settings), the attach
# store, and the seed slots. Nothing links into any of them, and giving them ids would invite it.
_SKIP_SLUGS = frozenset({"_system", "system", "sys", "seed", "seed-prev"})
_MEMBERS_FILE = "policy/members.json"


class WorkspaceRegistry:
    """id → ``{id, slug, dir, name, kind, owner, updated}``, plus a slug → id lookup.

    Redis keys ``agent:workspace:<id>`` and ``agent:workspace:slug:<slug>``; an id set at
    ``agent:workspaces`` so ``all()`` answers without a keyspace scan (``KEYS`` on a shared redis is
    a stall, not a query)."""

    def __init__(self, redis_client=None) -> None:
        self._redis = redis_client
        self._mem: dict[str, dict] = {}
        self._by_slug: dict[str, str] = {}

    # ── keys ──
    @staticmethod
    def _key(workspace_id: str) -> str:
        return f"agent:workspace:{workspace_id}"

    @staticmethod
    def _slug_key(slug: str) -> str:
        return f"agent:workspace:slug:{slug}"

    _INDEX = "agent:workspaces"

    # ── writes ──
    def put(self, record: dict) -> dict:
        """Upsert one record, stamping ``updated``. The id must already exist — this index never
        mints one; minting is the FILE's job (``shared/workspace_id.ensure_workspace_json``), so
        there is exactly one place an id is born."""
        rec = dict(record)
        wid = str(rec.get("id") or "")
        if not is_workspace_id(wid):
            raise ValueError(f"{wid!r} is not a workspace id")
        rec["updated"] = time.time()
        old = self.get(wid)
        if self._redis is not None:
            self._redis.set(self._key(wid), json.dumps(rec))
            self._redis.sadd(self._INDEX, wid)
            if rec.get("slug"):
                self._redis.set(self._slug_key(str(rec["slug"])), wid)
            if old and old.get("slug") and old["slug"] != rec.get("slug"):
                self._redis.delete(self._slug_key(str(old["slug"])))
        else:
            self._mem[wid] = rec
            if old and old.get("slug") and old["slug"] != rec.get("slug"):
                self._by_slug.pop(str(old["slug"]), None)
            if rec.get("slug"):
                self._by_slug[str(rec["slug"])] = wid
        return rec

    def forget(self, workspace_id: str) -> None:
        """Drop a record. Used when a workspace is DELETED — its links then resolve to ``gone``,
        which renders the last known title as plain text rather than a chip that opens nothing."""
        rec = self.get(workspace_id)
        if self._redis is not None:
            self._redis.delete(self._key(workspace_id))
            self._redis.srem(self._INDEX, workspace_id)
            if rec and rec.get("slug"):
                self._redis.delete(self._slug_key(str(rec["slug"])))
        else:
            self._mem.pop(workspace_id, None)
            if rec and rec.get("slug"):
                self._by_slug.pop(str(rec["slug"]), None)

    # ── reads ──
    def get(self, workspace_id: str) -> Optional[dict]:
        if not workspace_id:
            return None
        if self._redis is not None:
            raw = self._redis.get(self._key(workspace_id))
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                logger.warning("workspace record %s is unreadable in the store", workspace_id)
                return None
        return self._mem.get(workspace_id)

    def by_slug(self, slug: str) -> Optional[dict]:
        if not slug:
            return None
        if self._redis is not None:
            wid = self._redis.get(self._slug_key(slug))
            return self.get(wid) if wid else None
        wid = self._by_slug.get(slug)
        return self.get(wid) if wid else None

    def all(self) -> list[dict]:
        if self._redis is not None:
            ids: Iterable[str] = self._redis.smembers(self._INDEX) or set()
        else:
            ids = list(self._mem)
        return [r for r in (self.get(i) for i in ids) if r]


# ── classifying what is on the volume ────────────────────────────────────────────────────────────

def classify(ws_dir) -> str:
    """``desk`` | ``group`` | ``global`` for one directory on the workspace volume.

    Read off the FILES and nothing else: a group is a workspace with a members roster
    (``policy/members.json``), which is precisely what ``/api/workspace/shared/new`` writes and what
    ``ensure_workspace_shareable`` writes when a private workspace is promoted. A desk is what is
    left. Guessing from the directory NAME was the alternative and it is wrong on the live instance
    twice over — a desk is named by an opaque subject id and a group by a slugified human name, and
    neither shape is a rule."""
    p = Path(ws_dir)
    if p.name == GLOBAL_SLUG:
        return "global"
    return "group" if (p / _MEMBERS_FILE).is_file() else "desk"


def _default_name(slug: str, kind: str) -> str:
    """The name a workspace gets when nobody has given it one.

    For a group this is its slug, which at least carries the human name it was slugified from. For
    a desk it is deliberately NOT the bare subject id: `126` is the string the founder saw in the
    chat header (F49), and it is the directory name showing through — the exact defect the registry
    exists to stop. Until something knows the person's name, "Desk 126" is at least a sentence."""
    if kind == "global":
        return "The organisation"
    return slug if kind == "group" else f"Desk {slug}"


def _commit_identity(ws_dir: Path) -> None:
    """Commit `.vexa/workspace.json`, BY PATHSPEC, in the workspace it belongs to.

    By pathspec for the standing reason rather than a local nicety: `git commit` commits THE INDEX,
    so a bare add+commit here would sweep in whatever a concurrently running agent turn had staged
    in the same repo and file it under this message. Naming the path is how this writer stays in
    its own lane.

    Best-effort — a workspace that is not a git repo, or one whose git refuses, still HAS its id in
    the working tree, and the next startup reads it back. An identity is not worth failing a boot
    over."""
    if not (ws_dir / ".git").is_dir():
        return
    env = {**os.environ, **scrubbed_git_env(),
           "GIT_AUTHOR_NAME": "Vexa", "GIT_AUTHOR_EMAIL": "platform@vexa.ai",
           "GIT_COMMITTER_NAME": "Vexa", "GIT_COMMITTER_EMAIL": "platform@vexa.ai"}

    def git(*args):
        return subprocess.run(["git", "-C", str(ws_dir), *args], capture_output=True, text=True, env=env)

    try:
        git("add", "--", WORKSPACE_JSON)
        if git("diff", "--cached", "--name-only", "--", WORKSPACE_JSON).stdout.strip():
            git("commit", "-q", "-m", f"{ws_dir.name}: workspace identity", "--", WORKSPACE_JSON)
    except OSError as exc:  # noqa: BLE001 — no git on PATH is a deployment shape, not a failure
        logger.info("could not commit the workspace identity in %s: %s", ws_dir, exc)


def sync_workspace(root, slug: str, *, registry: WorkspaceRegistry, kind: Optional[str] = None,
                   name: Optional[str] = None, owner: Optional[str] = None,
                   ws_dir=None, created: Optional[str] = None) -> Optional[dict]:
    """Ensure ONE workspace has an id (minting into its tree if it has none) and that the registry
    points that id at where it is now. Returns the registry record, or ``None`` if the tree is gone.

    THE PRESERVATION SEAM. Called after every act that MOVES a workspace — create, swap, park,
    restore, promote, un-share — it reads the identity out of the tree that is now in place. A
    parked tree brings its id back with it; a freshly cloned repo that already carries
    ``.vexa/workspace.json`` keeps ITS id, and the registry re-points to the new slug. That is how
    an attached repo stays the same workspace instead of becoming a new one wearing its name."""
    d = Path(ws_dir) if ws_dir is not None else Path(root) / slug
    if not d.is_dir():
        return None
    kind = (kind or classify(d)).strip().lower()
    if kind not in KINDS:
        kind = "desk"
    rec, minted = ensure_workspace_json(d, kind=kind, created=created or _dt.date.today().isoformat())
    # The stored kind can be behind reality (a desk workspace promoted to a group); the FILE is
    # corrected explicitly here rather than by a guess elsewhere.
    if rec.get("kind") != kind:
        rec = write_workspace_json(d, id=rec["id"], kind=kind, created=rec.get("created", ""))
        minted = True
    if minted:
        _commit_identity(d)
    prev = registry.get(rec["id"]) or {}
    return registry.put({
        "id": rec["id"],
        "slug": slug,
        "dir": str(d),
        "kind": kind,
        # A name already in the registry WINS over a default: a rename must not be undone by the
        # next sync, and every sync after a swap would otherwise re-derive the slug's default.
        "name": (name or prev.get("name") or _default_name(slug, kind)),
        "owner": (owner if owner is not None else prev.get("owner")) or (slug if kind == "desk" else None),
        "created": rec.get("created", ""),
    })


def rename(registry: WorkspaceRegistry, workspace_id: str, name: str) -> Optional[dict]:
    """Set a workspace's display name. The id and every link into it are untouched — which is the
    single behaviour decision 26 was asked for."""
    rec = registry.get(workspace_id)
    if rec is None:
        return None
    return registry.put({**rec, "name": str(name).strip()[:120] or rec.get("name")})


def migrate(root, registry: WorkspaceRegistry, *, created: Optional[str] = None) -> dict:
    """Give every workspace on the volume an id, and index them all. Run at startup, idempotent.

    Walks the workspace root once: ``_global``, then every non-dot top-level directory that is a
    git workspace. Dot-directories are platform plumbing (``.attached``, ``.system``) and the
    reserved system slugs are skipped — see ``_SKIP_SLUGS``.

    PARKED TREES ARE ALSO WALKED (``.attached/<subject>/<slug>``) but only to MINT their id file,
    never to index them: a parked workspace is not addressable, and a registry row pointing at a
    slot inside somebody's attach store would resolve links to a tree nobody can open. Minting it
    now is what makes the id survive the swap that brings it back."""
    rootp = Path(root)
    out = {"indexed": [], "minted": [], "parked_minted": []}
    if not rootp.is_dir():
        return out
    day = created or _dt.date.today().isoformat()
    for d in sorted(rootp.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d.name in _SKIP_SLUGS:
            continue
        had = read_workspace_json(d) is not None
        rec = sync_workspace(rootp, d.name, registry=registry, created=day)
        if rec is None:
            continue
        out["indexed"].append(rec)
        if not had:
            out["minted"].append(rec)
    # The attach store: `<root>/.attached/<subject>/<slug>` — one level of subject, one of slug.
    store = rootp / ".attached"
    if store.is_dir():
        for subject_dir in sorted(p for p in store.iterdir() if p.is_dir()):
            for slot in sorted(p for p in subject_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
                if read_workspace_json(slot) is not None:
                    continue
                rec, minted = ensure_workspace_json(slot, kind=classify(slot), created=day)
                if minted:
                    out["parked_minted"].append({"slug": slot.name, "subject": subject_dir.name, **rec})
    return out


# ── access, as the reader sees it ────────────────────────────────────────────────────────────────

# A membership check: (root, workspace_id/slug, subject) -> role | None. Injected so this module
# owns no import of the membership store and the tests need no policy files.
MemberCheck = Callable[[Path, str, str], Optional[str]]


def access_for(record: Optional[dict], subject: str, *, root=None,
               is_member: Optional[MemberCheck] = None) -> str:
    """``readable`` | ``not-yours`` | ``gone`` for one reader and one workspace.

    ``gone`` means the registry has no record, or it has one and the tree it points at is no longer
    there. ``not-yours`` means it exists and this reader has no claim on it — a normal answer, and
    the renderer shows the last known title greyed rather than an error.

    A desk is readable by its OWNER only. Not by the group's other members, not by somebody who was
    in a meeting with them: decision 21 says a desk is company knowledge held by one person and the
    company's AGENTS may read it for a meeting — a mount an autonomous run is granted, never a link
    a person clicks."""
    if not record:
        return ACCESS_GONE
    d = record.get("dir")
    if d and not Path(d).is_dir():
        return ACCESS_GONE
    kind, owner = record.get("kind"), record.get("owner")
    if kind == "global":
        return ACCESS_READABLE          # the org tier is mounted into every worker and every chat
    if kind == "desk":
        return ACCESS_READABLE if owner and str(owner) == str(subject) else ACCESS_NOT_YOURS
    if is_member is not None and root is not None:
        try:
            if is_member(Path(root), str(record.get("slug") or ""), str(subject)) is not None:
                return ACCESS_READABLE
        except Exception:  # noqa: BLE001 — an unreadable roster is "not yours", never a 500
            logger.warning("membership check failed for %s", record.get("slug"))
    return ACCESS_NOT_YOURS


def view(record: Optional[dict], access: str, *, workspace_id: str = "") -> dict:
    """The wire shape of ``GET /api/workspaces/{id}``: ``{id, name, kind, access}``.

    A ``gone`` workspace still answers with its LAST KNOWN name when the registry remembers one —
    that is what lets a link to a deleted workspace render as its own title in plain text instead
    of an opaque id."""
    if not record:
        return {"id": workspace_id, "name": None, "kind": None, "access": ACCESS_GONE}
    return {"id": record.get("id", workspace_id), "name": record.get("name"),
            "kind": record.get("kind"), "slug": record.get("slug"), "access": access}
