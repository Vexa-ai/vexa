"""workspace_membership.py — Lane M: membership + invites + roles (the access layer for shared workspaces).

The foundation for shared workspaces and shared meeting sessions. A workspace owner can grant another
user **read (viewer)** or **read/write (contributor)** access; access is granted on the fly via scoped,
single-use-by-default invite tokens (D2 in plans/shared-meeting-workspace.md).

Two stores, written together on every change (the git file is authoritative, the index is derived):

  1. **Authoritative** — the workspace's OWN git repo at ``policy/members.json``::

        [{"subject", "role": "owner"|"contributor"|"viewer", "added_by", "added_at"}]

     Git is the source of truth: auditable, travels with the workspace, survives a DB loss (Q6). Invite
     token HASHES live beside it in ``policy/invites.json`` (only the sha256 of the token, never the token)::

        [{"id", "hash", "role", "expires_at", "max_uses", "uses", "created_by", "created_at", "revoked"}]

  2. **Index copy** — the user row's JSONB ``users.data.memberships[]`` = ``[{workspace_id, role, added_at}]``,
     for listing "workspaces shared with me". agent-api has no DB, so this is reached through the injected
     ``MembershipIndex`` port (real adapter → identity admin-api; a fake in tests).

**policy/ is PLATFORM-WRITE-ONLY.** Agent turns must never modify ``policy/``. The membership module here IS
the platform writer (it commits members.json/invites.json directly). The worker's turn-commit path
(``llm/ports.run_harness_turn``) reverts any ``policy/`` change an agent turn produced before it commits —
the enforcement seam. See ``POLICY_DIR`` (shared constant).

**Role enforcement** — ``require_role(root, workspace_id, subject, min_role)``: owner > contributor > viewer.
The SYSTEM workspace and a user's own private workspaces are never shareable — ``assert_shareable`` refuses
invites/membership on reserved/own-private slugs.

**Lane A seam** — ``is_member(root, workspace_id, subject) -> role | None`` is the ONE function Lane A
(mount-resolution + transcript-subscribe-by-membership) calls to decide whether a subject may mount a
workspace they don't own. This module deliberately does NOT touch the mount set / dispatch — membership
DATA + APIs only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

log = logging.getLogger(__name__)

# The platform-write-only subtree inside every workspace repo. Shared with the worker's turn-commit
# guard (llm/ports imports the string, not this module — that module stays product-import-free).
POLICY_DIR = "policy"
MEMBERS_FILE = f"{POLICY_DIR}/members.json"
INVITES_FILE = f"{POLICY_DIR}/invites.json"

# Role lattice: owner > contributor > viewer. The STORED spelling, unchanged: ``viewer`` is what every
# ``policy/members.json`` on every running instance already says, and renaming a value on disk is a
# migration nobody asked for.
ROLES = ("viewer", "contributor", "owner")
_RANK = {r: i for i, r in enumerate(ROLES)}

# ── THE THREE ROLES, IN THE WORDS THE PRODUCT USES (Vexa-ai/vexa#1632) ───────────────────────────
#
# ⚠ WHAT THIS REPLACED, AND WHAT IT COST. ``INVITABLE_ROLES`` was ``("contributor",)`` — the
# single-rank model of the 2026-07-07 owner ruling, where every member is read/write and ``owner`` is
# just the creator. On 2026-09-06 the founder pressed **Add a member…** on a workspace front page and
# read this back:
#
#     invite role must be one of ('contributor',)
#
# The panel was minting a ``viewer`` invite against a set that held one value. Two defects in one
# sentence: a control that could not work, and a python tuple repr shown to a person as an answer.
#
# THE SET IS THE PRIMITIVES' NOW, and it is not an invention — it is read off the seed
# ``behavior/global/POLICIES.md``, under *What is not yours to choose*: **"a member reads a group; an
# owner or contributor writes it"**. That sentence names exactly three ranks, so these are the three.
#
# `reader` IS `viewer`, one layer up. The lattice keeps its stored spelling and the product says
# ``reader``, because that is the word the terminal has said since #1623 (`workspaceReadme.roleLabel`
# maps `viewer` → `reader`) and the word a person recognises. Every value crossing this module's
# boundary is normalised on the way IN (``normalize_role``) and worded on the way OUT
# (``role_word``), so no caller has to know that the two spellings exist and no second reader of the
# lattice can disagree with this one.
ROLE_WORDS = ("owner", "contributor", "reader")

#: The outward word → the lattice spelling. Both spellings of the read-only rank are accepted on the
#: way in, because an existing record says ``viewer`` and a person says ``reader``.
_ROLE_IN = {"owner": "owner", "contributor": "contributor", "reader": "viewer", "viewer": "viewer"}

#: The lattice spelling → the outward word. The inverse, written out rather than derived, because a
#: derived inverse of a many-to-one map picks a winner by dict order.
_ROLE_OUT = {"owner": "owner", "contributor": "contributor", "viewer": "reader"}

#: WHAT EACH ROLE MEANS, in one sentence, DERIVED from the POLICIES.md line quoted above and from
#: nothing else. It is here rather than in the caller that shows it because three surfaces say it —
#: the agent's confirmation before it acts, the verb's own answer, and the invite mail a stranger
#: reads — and a person who is told one thing by the chat and another by the mail has been told
#: nothing. Present tense, no "can": these are what the role IS.
ROLE_SENTENCES = {
    "owner": "an owner writes this group and can add or remove its members",
    "contributor": "a contributor writes this group",
    "reader": "a reader reads this group and does not write it",
}

#: Every role an invite may be minted for, and every role a membership may be set to. The three the
#: product has, in rank order, in the words a person reads — so the refusal sentence this appears in
#: names roles somebody can actually type.
INVITABLE_ROLES = ROLE_WORDS


def normalize_role(role) -> str:
    """One role as the LATTICE spells it, or ``MembershipError`` naming the three that exist.

    THE ONE DOOR a role comes through. Callers hand in whatever a person, a route body or an old
    record said; everything below this line sees ``owner``/``contributor``/``viewer`` and nothing
    else. Case and surrounding space are the caller's accident, not their meaning."""
    word = str(role or "").strip().lower()
    lattice = _ROLE_IN.get(word)
    if not lattice:
        raise MembershipError(
            f"{role!r} is not a role here — the roles are "
            + ", ".join(ROLE_WORDS)
            + ". " + "; ".join(f"{r}: {ROLE_SENTENCES[r]}" for r in ROLE_WORDS),
            status=400)
    return lattice


def role_word(role) -> str:
    """One role as the PRODUCT says it — ``viewer`` on disk reads ``reader`` to a person.

    Never raises: an unrecognised stored value is returned as it is. A roster that shows a strange
    word is a finding; a roster that refuses to render because one row is odd is a bug."""
    word = str(role or "").strip().lower()
    return _ROLE_OUT.get(word, word)


def role_sentence(role) -> str:
    """The one sentence ``role`` means, or ``""`` for a role with no sentence to give."""
    return ROLE_SENTENCES.get(role_word(role), "")

DEFAULT_EXPIRES_IN_SEC = 604800  # 7 days
DEFAULT_MAX_USES = 1

# Invite ACCESS MODES (AMENDMENT 5). ``open`` = any authenticated user with the link redeems (bounded
# by expires_at + max_uses). ``restricted`` = redeem succeeds only if the authenticated user's VERIFIED
# email is in the invite's ``allowed_emails[]`` (else refused even with a valid link + auth).
INVITE_MODES = ("open", "restricted")
DEFAULT_INVITE_MODE = "open"

# Reserved workspace slugs that are NEVER shareable: the per-user SYSTEM workspace + the attach store's
# dot-namespace + the seed slot. A subject's OWN private workspace is refused separately (see
# ``assert_shareable``): sharing is opt-in via an owner membership record, not implicit on a bare subject.
RESERVED_SLUGS = frozenset({"sys", "_system", "system", "_global", "global", "seed", "seed-prev"})


class MembershipError(RuntimeError):
    """A membership/invite operation was refused for a domain reason (not shareable, bad role, …).
    Carries an HTTP-ish ``status`` so the API layer maps it without a translation table."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ── the index port (users.data.memberships[]) ───────────────────────────────────────────────────
class MembershipIndex(Protocol):
    """The derived index of "workspaces shared with me" — the user row's ``users.data.memberships[]``.

    agent-api has no DB; the real adapter reaches the identity admin-api over its internal edge, and the
    unit tests inject an in-memory fake. Writes here are best-effort mirrors of the authoritative git
    file: a failure to update the index MUST NOT lose the git-committed grant (Q6 — the index is
    rebuildable from the git files). The API layer logs an index-write failure and proceeds."""

    def add(self, subject: str, workspace_id: str, role: str, added_at: str) -> None:
        """Upsert ``{workspace_id, role, added_at}`` into ``subject``'s memberships (idempotent per ws)."""
        ...

    def remove(self, subject: str, workspace_id: str) -> None:
        """Drop the ``workspace_id`` entry from ``subject``'s memberships (idempotent)."""
        ...

    def list(self, subject: str) -> list[dict]:
        """Return ``subject``'s ``memberships[]`` (``[]`` if none / unknown)."""
        ...


class InMemoryMembershipIndex:
    """A trivial ``MembershipIndex`` for tests / a single-process deploy with no identity service.
    Also the composition-root default when the admin-api edge is not configured — the git files stay
    authoritative, so an unconfigured index only costs the "shared with me" listing, not the grant."""

    def __init__(self) -> None:
        self._by_subject: dict[str, dict[str, dict]] = {}

    def add(self, subject: str, workspace_id: str, role: str, added_at: str) -> None:
        self._by_subject.setdefault(subject, {})[workspace_id] = {
            "workspace_id": workspace_id, "role": role, "added_at": added_at,
        }

    def remove(self, subject: str, workspace_id: str) -> None:
        self._by_subject.get(subject, {}).pop(workspace_id, None)

    def list(self, subject: str) -> list[dict]:
        return list(self._by_subject.get(subject, {}).values())


# ── git-backed authoritative store ──────────────────────────────────────────────────────────────
# The commit primitive is injected (a Callable) so the store is offline-provable with a fake and so
# the module owns no git-subprocess coupling in its logic. Signature: (workspace_dir, message) -> None.
CommitFn = Callable[[Path, str], None]


# ── per-workspace serialization of read-modify-write-commit on policy/ ────────────────────────────
# The authoritative store is the workspace git repo (policy/{members,invites}.json). A redeem is a
# read → check(uses<max) → grant → uses++ → commit; two concurrent redeems of a max_uses=K invite must
# grant AT MOST K (a lost-update here over-grants — a max_uses=1 link would admit N users). We serialize
# that critical section per workspace_id with a process-local lock: correct for the common single-process
# deploy (one uvicorn worker; sync endpoints run in Starlette's threadpool, so the contention is between
# THREADS — a threading.Lock is the right primitive, not asyncio). The lock guards a fresh re-read from
# disk (never a stale in-memory copy) + the commit, so the check and the increment cannot interleave.
#
# MULTI-REPLICA NOTE: across separate PROCESSES/replicas a process-local lock does not serialize. The
# durable guard there is the git commit acting as the compare-and-set — two replicas racing the redeem
# collide on the workspace's index.lock / a non-fast-forward, and the loser must re-read + re-check
# (uses<max) before retrying. ``accept_invite`` re-reads under the lock and is structured to be retried;
# a multi-replica deploy MUST additionally front this with a shared lock (redis/advisory) or make the
# git push the CAS. Today agent-api is pinned single-replica (helm agentApi.replicaCount: 1 on an RWO
# PVC), so the process-local lock is sufficient and this is a documented, not open, gap.
_WS_LOCKS: dict[str, threading.Lock] = {}
_WS_LOCKS_GUARD = threading.Lock()


def _ws_lock(workspace_id: str) -> threading.Lock:
    """The process-local lock serializing the read-modify-write-commit of policy/ for one workspace."""
    with _WS_LOCKS_GUARD:
        lk = _WS_LOCKS.get(workspace_id)
        if lk is None:
            lk = _WS_LOCKS[workspace_id] = threading.Lock()
        return lk


def policy_commit(ws: Path, message: str) -> None:
    """The default platform writer: stage + commit ONLY ``policy/`` (never sweep an agent's in-progress
    tree) with the PLATFORM identity as committer. This is the control-plane commit path that policy/ is
    write-restricted TO — distinct from the agent turn-commit. Best-effort no-op on an empty policy diff.
    Scrubbed git env: a hook-exported GIT_DIR must never redirect this commit (see shared/gitenv)."""
    _policy_commit(ws, message, author_name="vexa-platform", author_email="platform@vexa.ai")


def policy_commit_as(author_name: str, author_email: str) -> CommitFn:
    """A ``policy/`` writer whose AUTHOR is the person who asked for the change (Vexa-ai/vexa#1632).

    THE COMMITTER STAYS THE PLATFORM AND THE AUTHOR BECOMES THE HUMAN — git's two identities, used
    for the two things they are for. The platform is what physically made the commit (it holds the
    only write on ``policy/``); the person is who decided. Before this, every membership change in
    every workspace's history read ``vexa-platform``, so *who added this person* was answerable only
    by reading the JSON diff and hoping ``added_by`` was still there. The issue asks for the act to
    be recorded "with the inviter as author", and this is that, literally — `git log --format=%an`
    on `policy/members.json` now names them.

    ``_global.commit`` already does exactly this for the company layer, one directory over. Same
    shape, same reason, and neither invented it: a workspace is a git repository and this is what git
    authorship is.

    A blank name or address falls back to the platform identity rather than committing with an empty
    author, which git accepts and nobody can read."""
    name = " ".join(str(author_name or "").split()) or "vexa-platform"
    email = str(author_email or "").strip() or "platform@vexa.ai"

    def _commit_as(ws: Path, message: str) -> None:
        _policy_commit(ws, message, author_name=name, author_email=email)

    return _commit_as


def _policy_commit(ws: Path, message: str, *, author_name: str, author_email: str) -> None:
    """The one commit body both writers share — extracted verbatim when the authored variant arrived,
    because a second copy of a git-env-scrubbing commit path is a second place the scrub can be
    forgotten."""
    import subprocess
    from shared.gitenv import scrubbed_git_env

    env = scrubbed_git_env(
        GIT_AUTHOR_NAME=author_name, GIT_AUTHOR_EMAIL=author_email,
        GIT_COMMITTER_NAME="vexa-platform", GIT_COMMITTER_EMAIL="platform@vexa.ai",
    )
    ws = Path(ws)
    if not (ws / ".git").exists():
        subprocess.run(["git", "-C", str(ws), "init", "-q"], check=True,
                       capture_output=True, text=True, env=env)
    added = subprocess.run(["git", "-C", str(ws), "add", "--", POLICY_DIR],
                           capture_output=True, text=True, env=env)
    if added.returncode != 0:
        # policy/ is EXCLUDED in this clone — the workspace has an ATTACHED external repo as its tree
        # (workspace_attach.carry_policy), where the member list is deliberately untracked so it is
        # never pushed to somebody else's repository. The write to disk already happened and
        # ``read_members`` reads the working tree, so this is a legitimate state, not a failure.
        return
    # commit only if policy/ actually changed (staged diff non-empty)
    staged = subprocess.run(["git", "-C", str(ws), "diff", "--cached", "--quiet", "--", POLICY_DIR],
                            capture_output=True, text=True, env=env)
    if staged.returncode != 0:  # non-zero == there IS a staged change
        subprocess.run(["git", "-C", str(ws), "commit", "-q", "-m", message, "--", POLICY_DIR],
                       check=True, capture_output=True, text=True, env=env)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def hash_token(token: str) -> str:
    """The sha256 hex of an invite token — the ONLY form persisted (a leak of policy/invites.json must
    not leak the capability; mirrors the share-link surface WP0.1)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ws_dir(root: Path, workspace_id: str) -> Path:
    """The on-disk workspace repo for ``workspace_id`` (``<root>/<workspace_id>``), traversal-guarded —
    the workspace id is the owner subject's slug (the dir the dispatch mounts)."""
    root = Path(root).resolve()
    ws = (root / workspace_id).resolve()
    if ws != root and root not in ws.parents:
        raise MembershipError("invalid workspace id", status=400)
    return ws


def _read_json_list(ws: Path, rel: str) -> list[dict]:
    f = ws / rel
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        log.warning("could not parse %s in %s; treating as empty", rel, ws)
        return []


def _write_json_list(ws: Path, rel: str, rows: list[dict]) -> None:
    f = ws / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rows, indent=2, sort_keys=False) + "\n")


def _valid_subject(subject: str) -> bool:
    return bool(subject) and "/" not in subject and "\\" not in subject and subject not in ("", ".", "..")


def assert_shareable(root: Path, workspace_id: str) -> Path:
    """Guard: refuse membership/invite operations on a NON-shareable workspace and return its dir.

    Refused: a reserved slug (the SYSTEM workspace, the attach store's dot-namespace, the seed slot) and
    any dot-prefixed slug. A subject's OWN private workspace is shareable ONLY once it has been made a
    shared workspace — i.e. it carries a ``policy/members.json`` with an ``owner`` record. A bare subject
    workspace with no members file is treated as private and refused for INVITE minting, but membership
    bootstrapping (the owner's first grant) is allowed via ``ensure_owner``. See callers."""
    if not workspace_id or workspace_id.startswith("."):
        raise MembershipError("workspace is not shareable (reserved namespace)", status=403)
    if workspace_id in RESERVED_SLUGS:
        raise MembershipError("workspace is not shareable (reserved workspace)", status=403)
    return _ws_dir(root, workspace_id)


# ── membership reads ────────────────────────────────────────────────────────────────────────────
def read_members(root: Path, workspace_id: str) -> list[dict]:
    """The authoritative member list from ``policy/members.json`` (``[]`` if none)."""
    return _read_json_list(_ws_dir(root, workspace_id), MEMBERS_FILE)


def backfill_member_email(root: Path, workspace_id: str, subject: str, email: Optional[str], *,
                          commit_fn: Optional[CommitFn] = None) -> bool:
    """Best-effort: stamp ``subject``'s VERIFIED email onto their EXISTING member record when it's missing
    or stale. Self-heals members granted before emails were stored — each member's row fills in the first
    time they open the manage panel (which carries their gateway-verified ``X-User-Email``). Returns True
    iff it wrote. Never creates a membership and never raises for a non-member — purely a label refresh."""
    _email = _normalize_email(email)
    if not _email:
        return False
    ws = _ws_dir(root, workspace_id)
    with _ws_lock(workspace_id):
        members = _read_json_list(ws, MEMBERS_FILE)
        target = next((m for m in members if m.get("subject") == subject), None)
        if target is None or target.get("email") == _email:
            return False
        target["email"] = _email
        _write_json_list(ws, MEMBERS_FILE, members)
        _commit(commit_fn, ws, f"policy: email for {subject} in {workspace_id}")
    return True


def is_member(root: Path, workspace_id: str, subject: str) -> Optional[str]:
    """**The Lane A seam.** The subject's role in the workspace (``owner``/``contributor``/``viewer``),
    or ``None`` if not a member. Resolved from the authoritative git file — the one call Lane A uses to
    decide whether a subject may mount / subscribe to a workspace they don't own."""
    for m in read_members(root, workspace_id):
        if m.get("subject") == subject:
            return m.get("role")
    return None


def member_by_email(root: Path, workspace_id: str, email: str) -> Optional[dict]:
    """The member record whose stored email is ``email``, or None (Vexa-ai/vexa#1632).

    THE ROSTER IS ADDRESSED BY EMAIL NOW, because that is what a person says. Every route above takes
    a ``subject`` — the opaque platform id — which is right for a panel that read the roster and has
    the id in its hand, and useless to an agent whose person just typed a name out loud. So the acts
    resolve an address here first, and only fall back to asking identity for a subject when this
    workspace has never seen the address.

    Compared normalised on both sides: a member added from a form and one added from a mail header
    differ by case and nothing else, and matching them as strings would silently create a second
    membership for one person."""
    wanted = _normalize_email(email)
    if not wanted:
        return None
    for m in read_members(root, workspace_id):
        if _normalize_email(m.get("email")) == wanted:
            return dict(m)
    return None


def require_role(root: Path, workspace_id: str, subject: str, min_role: str) -> str:
    """Assert ``subject`` holds AT LEAST ``min_role`` in ``workspace_id``; return their actual role.

    owner > contributor > viewer. Raises ``MembershipError(status=403)`` if the subject is not a member
    or ranks below ``min_role``. The ONE place every ``/api/workspace/*`` shared route gates through."""
    # ``reader`` is what a person says and ``viewer`` is what the lattice stores (Vexa-ai/vexa#1632);
    # a gate that took only one of them would refuse a rank that exists.
    min_role = _ROLE_IN.get(str(min_role or "").strip().lower(), min_role)
    if min_role not in _RANK:
        raise MembershipError(f"unknown role {min_role!r}", status=400)
    role = is_member(root, workspace_id, subject)
    if role is None or _RANK.get(role, -1) < _RANK[min_role]:
        raise MembershipError("insufficient role for this workspace", status=403)
    return role


def list_memberships(root: Path, subject: str) -> list[dict]:
    """Every shareable workspace on this host whose AUTHORITATIVE ``policy/members.json`` names
    ``subject``, in the row shape the index returns (``workspace_id``/``role``/``added_at``).

    The RECOVERY read for the derived index (Q6). Grants are written to BOTH stores, but only the git
    file is authoritative — so a listing that consults ONLY the mirror reports "no shared workspaces"
    whenever the internal edge to the identity service is unreachable, losing a grant the system
    demonstrably holds. Reserved slugs and the dot-namespace are skipped exactly as ``assert_shareable``
    refuses them, so the SYSTEM/seed workspaces never surface as memberships."""
    root = Path(root)
    if not root.exists():
        return []
    out: list[dict] = []
    for child in sorted(c for c in root.iterdir() if c.is_dir()):
        slug = child.name
        if slug.startswith(".") or slug in RESERVED_SLUGS:
            continue
        for member in _read_json_list(child, MEMBERS_FILE):
            if member.get("subject") == subject:
                out.append({"workspace_id": slug, "role": member.get("role"),
                            "added_at": member.get("added_at")})
                break
    return out


def reconciled_memberships(root: Path, subject: str,
                           index_list: Callable[[str], list]) -> tuple[list[dict], bool]:
    """The membership ENUMERATION for consumers that turn grants into MOUNTS (the active-set route, the
    dispatch mount builder): the derived index UNIONed with the authoritative ``list_memberships`` scan.
    Returns ``(rows, index_degraded)``.

    Same reconciliation contract as the shared-workspace listing (Q6): a UNION, never a subtraction — an
    index row with no local dir is a workspace on another host and must still be enumerated, so the git
    store only ever ADDS rows the index is missing. NEVER raises: a dead index leg flips
    ``index_degraded`` (the git rows still answer), a dead git leg still serves the index rows — so an
    unreachable or incomplete index cannot silently drop a locally-held grant from the enumeration.
    Consumers keep re-checking the role authoritatively per workspace (``is_member``); this feeds
    CANDIDATES, never access."""
    degraded = False
    try:
        rows = list(index_list(subject) or [])
    except Exception as exc:  # noqa: BLE001 — the authoritative store still answers
        log.warning("membership index list failed for subject=%s: %s — enumerating from policy/members.json only",
                    subject, exc)
        rows, degraded = [], True
    seen = {r.get("workspace_id") for r in rows if isinstance(r, dict)}
    try:
        rows += [row for row in list_memberships(root, subject) if row["workspace_id"] not in seen]
    except Exception as exc:  # noqa: BLE001 — enumeration must never break a mount path; the index rows still serve
        log.warning("authoritative membership scan failed for subject=%s: %s — serving the index rows only",
                    subject, exc)
    return rows, degraded


# ── membership writes (both stores) ─────────────────────────────────────────────────────────────
def _commit(commit_fn: Optional[CommitFn], ws: Path, message: str) -> None:
    if commit_fn is not None:
        try:
            commit_fn(ws, message)
        except Exception as exc:  # a commit failure must not corrupt the on-disk file we just wrote
            log.warning("policy commit failed in %s: %s", ws, exc)


def ensure_owner(root: Path, workspace_id: str, owner_subject: str, *,
                 index: MembershipIndex, email: Optional[str] = None,
                 commit_fn: Optional[CommitFn] = None) -> None:
    """Idempotently record ``owner_subject`` as the workspace's owner — the bootstrap that turns a bare
    private workspace into a SHARED workspace (the first grant). Safe to call repeatedly. ``email`` is the
    owner's VERIFIED email (when the caller has it), stored on the record for a human-readable roster."""
    ws = _ws_dir(root, workspace_id)
    with _ws_lock(workspace_id):
        _ensure_owner_locked(root, workspace_id, owner_subject, ws=ws, index=index, email=email,
                             commit_fn=commit_fn)


def _ensure_owner_locked(root: Path, workspace_id: str, owner_subject: str, *, ws: Path,
                         index: MembershipIndex, email: Optional[str] = None,
                         commit_fn: Optional[CommitFn] = None) -> None:
    members = _read_json_list(ws, MEMBERS_FILE)
    _email = _normalize_email(email)
    existing = next((m for m in members if m.get("subject") == owner_subject), None)
    already_owner = existing is not None and existing.get("role") == "owner"
    # Nothing to do only when the record is already an owner AND its email is already current.
    if already_owner and (not _email or existing.get("email") == _email):
        return
    now = _now_iso()
    # An existing entry for the subject is upgraded to owner; else appended. Store the verified email
    # (when known) so members.json — the authoritative, portable store — carries a human label, not
    # just the opaque subject id.
    if existing is not None:
        existing["role"] = "owner"
        if _email:
            existing["email"] = _email
    else:
        rec = {"subject": owner_subject, "role": "owner", "added_by": owner_subject, "added_at": now}
        if _email:
            rec["email"] = _email
        members.append(rec)
    _write_json_list(ws, MEMBERS_FILE, members)
    _commit(commit_fn, ws, f"policy: owner {owner_subject} for {workspace_id}")
    _index_add(index, owner_subject, workspace_id, "owner", (existing or {}).get("added_at", now))


def grant_membership(root: Path, workspace_id: str, subject: str, role: str, *,
                     added_by: str, index: MembershipIndex, email: Optional[str] = None,
                     commit_fn: Optional[CommitFn] = None) -> dict:
    """Grant (or re-grant) ``subject`` the given ``role`` — writes BOTH stores. Idempotent per subject:
    an existing member is updated in place (accepting an invite twice = still one membership). ``email``
    is the grantee's VERIFIED email (from the redeem request), stored so the roster shows a human label
    rather than the opaque subject id; a re-grant refreshes it when a newer value is supplied."""
    role = normalize_role(role)
    if not _valid_subject(subject):
        raise MembershipError("invalid subject", status=400)
    ws = assert_shareable(root, workspace_id)
    members = _read_json_list(ws, MEMBERS_FILE)
    now = _now_iso()
    _email = _normalize_email(email)
    for m in members:
        if m.get("subject") == subject:
            m["role"] = role  # role flip / idempotent re-accept
            if _email:
                m["email"] = _email
            record = m
            break
    else:
        record = {"subject": subject, "role": role, "added_by": added_by, "added_at": now}
        if _email:
            record["email"] = _email
        members.append(record)
    _write_json_list(ws, MEMBERS_FILE, members)
    _commit(commit_fn, ws, f"policy: {role} for {subject} in {workspace_id}")
    _index_add(index, subject, workspace_id, role, record.get("added_at", now))
    return dict(record)


def set_role(root: Path, workspace_id: str, subject: str, role: str, *,
             changed_by: str, index: MembershipIndex, commit_fn: Optional[CommitFn] = None) -> dict:
    """Flip a member's role (the "easily change read/write permissions" DoD item). Owner-only at the API
    layer. Refuses to demote/alter a non-member and refuses to strip the LAST owner (a workspace must
    always retain an owner)."""
    role = normalize_role(role)
    ws = assert_shareable(root, workspace_id)
    with _ws_lock(workspace_id):  # serialize the members.json read-modify-write (last-owner check + flip)
        members = _read_json_list(ws, MEMBERS_FILE)
        target = next((m for m in members if m.get("subject") == subject), None)
        if target is None:
            raise MembershipError("not a member", status=404)
        if target.get("role") == "owner" and role != "owner":
            owners = [m for m in members if m.get("role") == "owner"]
            if len(owners) <= 1:
                raise MembershipError("cannot remove the last owner", status=409)
        target["role"] = role
        _write_json_list(ws, MEMBERS_FILE, members)
        _commit(commit_fn, ws, f"policy: role {subject} -> {role} in {workspace_id}")
        _index_add(index, subject, workspace_id, role, target.get("added_at", _now_iso()))
        return dict(target)


def remove_member(root: Path, workspace_id: str, subject: str, *,
                  index: MembershipIndex, commit_fn: Optional[CommitFn] = None) -> None:
    """Remove a member from BOTH stores. Refuses to remove the last owner."""
    ws = assert_shareable(root, workspace_id)
    with _ws_lock(workspace_id):  # serialize the members.json read-modify-write (last-owner check + drop)
        members = _read_json_list(ws, MEMBERS_FILE)
        target = next((m for m in members if m.get("subject") == subject), None)
        if target is None:
            return  # idempotent
        if target.get("role") == "owner":
            owners = [m for m in members if m.get("role") == "owner"]
            if len(owners) <= 1:
                raise MembershipError("cannot remove the last owner", status=409)
        members = [m for m in members if m.get("subject") != subject]
        _write_json_list(ws, MEMBERS_FILE, members)
        _commit(commit_fn, ws, f"policy: remove {subject} from {workspace_id}")
        _index_remove(index, subject, workspace_id)


# ── invites ─────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MintedInvite:
    """The result of minting — the ``token`` is returned to the caller ONCE and never stored."""
    id: str
    token: str
    role: str
    expires_at: int
    max_uses: int


def _normalize_email(email: Optional[str]) -> str:
    """Case-fold + trim an email for membership comparison (emails are case-insensitive in practice)."""
    return (email or "").strip().lower()


# WHERE AN INVITE IS REDEEMED — the terminal's page, and the ONE spelling of it (Vexa-ai/vexa#1635).
# The founder minted an invite, opened the link and read *"not found"*: the base was the MCP host and
# no page served the path. The base is now the deployment's declared public app URL and the path is
# this constant, in ONE place, because a link is composed by two callers (the mint route and the
# invite act) and two spellings of a path is the same defect one layer in.
JOIN_PATH = "/join"


def invite_link(ui_url: str, token: str) -> str:
    """The link a person opens to redeem ``token``, on ``ui_url`` (VEXA_UI_URL — the deployment's
    declared public app URL). Empty ``ui_url`` ⇒ empty string: a link with no origin is not a link,
    and every caller refuses rather than handing one over."""
    base = str(ui_url or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}{JOIN_PATH}?i={urllib.parse.quote(token, safe='')}"


def mint_invite(root: Path, workspace_id: str, *, role: str, created_by: str,
                expires_in_sec: int = DEFAULT_EXPIRES_IN_SEC, max_uses: int = DEFAULT_MAX_USES,
                mode: str = DEFAULT_INVITE_MODE, allowed_emails: Optional[list[str]] = None,
                commit_fn: Optional[CommitFn] = None, now: Optional[float] = None) -> MintedInvite:
    """Mint a scoped invite token for ``workspace_id``. Stores ONLY the token's sha256 hash + metadata in
    ``policy/invites.json``; returns the plaintext token once (the caller builds the invite URL). The
    workspace must be shareable (reserved/own-private refused).

    ACCESS MODES (AMENDMENT 5): ``mode="open"`` = anyone-with-link (authenticated) redeems;
    ``mode="restricted"`` = redeem allowed only for an authenticated user whose VERIFIED email is in
    ``allowed_emails``. A restricted invite with an empty ``allowed_emails`` admits no one (fail-closed)."""
    # THE THREE ROLES, NOT THE ONE (Vexa-ai/vexa#1632). This line used to read
    # ``if role not in INVITABLE_ROLES`` against ``("contributor",)`` and put the tuple's own repr in
    # front of a person. ``normalize_role`` refuses with the three words and what each one means.
    role = normalize_role(role)
    if max_uses < 1:
        raise MembershipError("max_uses must be >= 1", status=400)
    if expires_in_sec <= 0:
        raise MembershipError("expires_in_sec must be > 0", status=400)
    if mode not in INVITE_MODES:
        raise MembershipError(f"invite mode must be one of {INVITE_MODES}", status=400)
    emails = sorted({_normalize_email(e) for e in (allowed_emails or []) if _normalize_email(e)})
    if mode == "restricted" and not emails:
        raise MembershipError("restricted invite requires allowed_emails", status=400)
    ws = assert_shareable(root, workspace_id)
    t = now if now is not None else time.time()
    token = secrets.token_urlsafe(32)
    invite_id = secrets.token_hex(8)
    rec = {
        "id": invite_id,
        "hash": hash_token(token),
        "role": role,
        "mode": mode,
        "allowed_emails": emails,
        "expires_at": int(t + expires_in_sec),
        "max_uses": int(max_uses),
        "uses": 0,
        "created_by": created_by,
        "created_at": _now_iso(),
        "revoked": False,
    }
    invites = _read_json_list(ws, INVITES_FILE)
    invites.append(rec)
    _write_json_list(ws, INVITES_FILE, invites)
    _commit(commit_fn, ws, f"policy: mint invite {invite_id} ({role}) for {workspace_id}")
    return MintedInvite(id=invite_id, token=token, role=role,
                        expires_at=rec["expires_at"], max_uses=rec["max_uses"])


def preview_invite(root: Path, token: str, *, now: Optional[float] = None) -> Optional[dict]:
    """Resolve an invite TOKEN to a READ-ONLY preview — the target workspace + terms (role · mode ·
    expiry · validity) — WITHOUT granting membership. Capability-gated by the token itself: whoever holds
    the link may see what they are being invited to, which is exactly what the pre-join consent screen
    needs (it renders before login). Returns ``None`` when no invite matches the token (never leaks which
    workspaces exist). Read-only: no writes, no use-count change, no membership."""
    t = now if now is not None else time.time()
    h = hash_token(token)
    if not root.exists():
        return None
    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        slug = child.name
        if slug.startswith(".") or slug in RESERVED_SLUGS:
            continue
        for rec in _read_json_list(child, INVITES_FILE):
            if rec.get("hash") != h:
                continue
            expired = int(rec.get("expires_at", 0)) < t
            used_up = int(rec.get("uses", 0)) >= int(rec.get("max_uses", 1))
            reason = ("revoked" if rec.get("revoked") else "expired" if expired
                      else "used_up" if used_up else None)
            return {
                "workspace_id": slug,
                "role": rec.get("role", "viewer"),
                "mode": rec.get("mode", DEFAULT_INVITE_MODE),
                # The addresses the invite is BOUND to. Surfaced because the join page has to put the
                # bound address into the sign-in field and lock it (Vexa-ai/vexa#1635): a person who
                # types a different one gets refused at redeem with nothing on screen explaining why.
                # It is a disclosure to whoever holds the token — which is the person the link was
                # sent to — of the address it was sent to. Empty for an open invite.
                "allowed_emails": list(rec.get("allowed_emails") or []),
                "expires_at": rec.get("expires_at"),
                "created_by": rec.get("created_by"),
                "valid": reason is None,
                "reason": reason,
            }
    return None


def accept_invite(root: Path, workspace_id: str, *, token: str, subject: str,
                  index: MembershipIndex, subject_email: Optional[str] = None,
                  commit_fn: Optional[CommitFn] = None, now: Optional[float] = None) -> dict:
    """Redeem ``token`` for ``subject``: validate (hash lookup, not revoked, not expired, uses<max_uses,
    AND mode==open OR subject_email ∈ allowed_emails), grant the invite's role (both stores), increment
    ``uses``. Idempotent per user — a subject who is already a member does NOT consume a use again
    (accepting twice = one membership).

    ``subject_email`` is the caller's VERIFIED email (from the auth provider; dev-login trusts the typed
    email in dev only). Required for a ``restricted`` invite; ignored for ``open``.

    Returns ``{"workspace_id", "role", "already_member"}``.

    ATOMICITY: the read → check(uses<max) → grant → uses++ → commit runs under the per-workspace lock so
    N concurrent redeems of a max_uses=K invite grant AT MOST K memberships (no lost-update over-grant).
    The invites file is re-read from DISK inside the lock — never a copy captured before it — so the
    use-count check sees every prior redeem's increment. (Multi-replica: see ``_ws_lock``.)"""
    ws = assert_shareable(root, workspace_id)
    t = now if now is not None else time.time()
    h = hash_token(token)
    with _ws_lock(workspace_id):
        # Fresh read UNDER the lock: the authoritative uses counter, not a value read before we waited.
        invites = _read_json_list(ws, INVITES_FILE)
        rec = next((i for i in invites if i.get("hash") == h), None)
        if rec is None:
            raise MembershipError("invalid invite", status=404)
        if rec.get("revoked"):
            raise MembershipError("invite revoked", status=410)
        if int(rec.get("expires_at", 0)) < t:
            raise MembershipError("invite expired", status=410)

        # AMENDMENT 5: restricted invites admit only an authenticated user whose VERIFIED email is listed.
        if rec.get("mode", DEFAULT_INVITE_MODE) == "restricted":
            allowed = {_normalize_email(e) for e in (rec.get("allowed_emails") or [])}
            if _normalize_email(subject_email) not in allowed:
                raise MembershipError("this invite is restricted to specific email addresses", status=403)

        already = is_member(root, workspace_id, subject) is not None
        if not already and int(rec.get("uses", 0)) >= int(rec.get("max_uses", 1)):
            raise MembershipError("invite fully used", status=410)

        role = rec.get("role", "viewer")
        grant_membership(root, workspace_id, subject, role, added_by=rec.get("created_by", "invite"),
                         index=index, email=subject_email, commit_fn=commit_fn)
        if not already:
            # Consume a use only for a NEW membership — re-accept is a no-op on the counter (idempotent).
            # This write + commit lands the increment durably before the lock is released, so the next
            # waiter re-reads the bumped counter and a max_uses=1 invite refuses the second grant.
            rec["uses"] = int(rec.get("uses", 0)) + 1
            _write_json_list(ws, INVITES_FILE, invites)
            _commit(commit_fn, ws, f"policy: invite {rec.get('id')} used ({subject})")
        return {"workspace_id": workspace_id, "role": role, "already_member": already}


def revoke_invite(root: Path, workspace_id: str, invite_id: str, *,
                  commit_fn: Optional[CommitFn] = None) -> None:
    """Revoke an invite by id (sets ``revoked``); a revoked invite fails ``accept`` with 410."""
    ws = assert_shareable(root, workspace_id)
    invites = _read_json_list(ws, INVITES_FILE)
    rec = next((i for i in invites if i.get("id") == invite_id), None)
    if rec is None:
        raise MembershipError("unknown invite", status=404)
    if not rec.get("revoked"):
        rec["revoked"] = True
        _write_json_list(ws, INVITES_FILE, invites)
        _commit(commit_fn, ws, f"policy: revoke invite {invite_id} for {workspace_id}")


def list_invites(root: Path, workspace_id: str) -> list[dict]:
    """The workspace's invites WITHOUT the hash (never surface the stored capability material)."""
    out = []
    for i in _read_json_list(_ws_dir(root, workspace_id), INVITES_FILE):
        out.append({k: v for k, v in i.items() if k != "hash"})
    return out


# ── index helpers (best-effort; the git file is authoritative) ──────────────────────────────────
def _index_add(index: MembershipIndex, subject: str, workspace_id: str, role: str, added_at: str) -> None:
    try:
        index.add(subject, workspace_id, role, added_at)
    except Exception as exc:  # Q6: the git file is the recovery source; never fail the grant on the index
        log.warning("membership index add failed (%s/%s): %s", subject, workspace_id, exc)


def _index_remove(index: MembershipIndex, subject: str, workspace_id: str) -> None:
    try:
        index.remove(subject, workspace_id)
    except Exception as exc:
        log.warning("membership index remove failed (%s/%s): %s", subject, workspace_id, exc)
