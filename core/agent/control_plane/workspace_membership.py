"""workspace_membership.py — Lane M: membership + invites + roles (the access layer for shared workspaces).

The foundation for shared workspaces and shared meeting sessions. A workspace owner can grant another
user **read (viewer)** or **read/write (contributor)** access; access is granted on the fly via scoped,
single-use-by-default invite tokens (D2 in plans/shared-meeting-workspace.md).

Two stores for MEMBERSHIP, written together on every change (the policy store is authoritative, the
index is derived), and a third, separate one for INVITES:

  1. **Authoritative** — the platform's POLICY STORE, ``<store-root>/.policy/<workspace_id>/members.json``
     (with ``visibility.json`` and ``connections.json`` beside it), OUTSIDE every workspace mount
     (PRD 0.13.4 D5 — see ``POLICY_STORE_DIR`` for why, and for the read-compatible migration)::

        [{"subject", "role": "owner"|"contributor"|"viewer", "added_by", "added_at"}]

     The workspace's own git repo keeps a platform-written MIRROR at ``policy/members.json``, committed
     with the deciding person as author: the audit trail, and what the worker counts readers from.
     The mirror is never read for access, and no caller route can write it.

  2. **Index copy** — the user row's JSONB ``users.data.memberships[]`` = ``[{workspace_id, role, added_at}]``,
     for listing "workspaces shared with me". agent-api has no DB, so this is reached through the injected
     ``MembershipIndex`` port (real adapter → identity admin-api; a fake in tests).

  3. **Invites** — ``<store-root>/.invites/<workspace_id>.json``, OUTSIDE every workspace mount, holding
     only the sha256 of each token and never the token::

        [{"id", "hash", "role", "expires_at", "max_uses", "uses", "created_by", "created_at", "revoked"}]

     They used to live at ``policy/invites.json``, inside the workspace, and the worker's turn write-back
     deleted every one of them a second after it was minted (Vexa-ai/vexa#1645). See ``INVITE_STORE_DIR``
     for what moved, what did not, and why — the roster is workspace knowledge and stays; an invite is
     capability material and does not belong in a tree that gets published, attached and reseeded.

**policy/ is PLATFORM-WRITE-ONLY.** It is a reserved directory for every caller route
(``workspace_paths.RESERVED_DIRS``: the page editor answers 403), an external-remote pull that changes it
is refused (``workspace_git_sync``), and agent turns must never modify it. The membership module here IS
the platform writer (it commits the mirror directly). The worker's turn-commit path
(``llm/ports.run_harness_turn``) reverts any ``policy/`` change an agent turn produced before it commits,
and — since #1645 — never deletes a ``policy/`` write the platform made while the turn ran. That is the
enforcement seam. See ``POLICY_DIR`` (shared constant).

**Role enforcement** — ``require_role(root, workspace_id, subject, min_role)``: owner > contributor > viewer.
The SYSTEM workspace and a user's own private workspaces are never shareable — ``assert_shareable`` refuses
invites/membership on reserved/own-private slugs.

**Lane A seam** — ``is_member(root, workspace_id, subject) -> role | None`` is the ONE function Lane A
(mount-resolution + transcript-subscribe-by-membership) calls to decide whether a subject may mount a
workspace they don't own. This module deliberately does NOT touch the mount set / dispatch — membership
DATA + APIs only.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

from workspaces.shared import workspace_paths as wpaths

log = logging.getLogger(__name__)

# The platform-write-only subtree inside every workspace repo. Shared with the worker's turn-commit
# guard (llm/ports imports the string, not this module — that module stays product-import-free).
POLICY_DIR = "policy"
MEMBERS_FILE = f"{POLICY_DIR}/members.json"
VISIBILITY_FILE = f"{POLICY_DIR}/visibility.json"
CONNECTIONS_FILE = f"{POLICY_DIR}/connections.json"
#: ``policy/`` is a RESERVED directory for every caller route (``workspace_paths.RESERVED_DIRS``);
#: this module is the platform writer of its mirror and names the folder it owns.
_POLICY_ALLOW = (POLICY_DIR,)

# ── WHERE A WORKSPACE'S POLICY LIVES NOW, AND WHY (PRD 0.13.4 D5, security review C3) ──────────────
#
# The roster, the visibility and the connections used to be AUTHORITATIVE in the workspace's own
# tree (``policy/*.json``). That tree is the same directory a contributor's page editor writes and a
# contributor's agent runs in, and on 2026-10-17 a contributor's ``PUT /api/workspace/file
# policy/members.json`` made them owner at once: ``require_role`` read the work tree with no cache,
# and the guard that reverts ``policy/`` edits runs only after an agent turn. Every "owners sign"
# gate rested on that file.
#
# So the authority leaves the tree, exactly as the invites did for Vexa-ai/vexa#1645 (see
# ``INVITE_STORE_DIR``): ``<store-root>/.policy/<workspace_id>/{members,visibility,connections}.json``.
#
# WHY THIS STORE AND NOT A DATABASE. agent-api has no database of its own (ports pattern). The two
# alternatives were (a) the ``MembershipIndex`` port to identity admin-api — the ``users.data``
# JSONB mirror — and (b) a new table behind a new port. (a) is keyed by SUBJECT, not by workspace,
# carries no visibility or connections, is best-effort by contract ("a failure to update the index
# MUST NOT lose the grant") and is unreachable in exactly the moments a fail-closed read must still
# answer; making it authoritative would change identity's contract (S1-B's lane). (b) is a contract
# and migration in another service for state agent-api already owns. The store-root dot-namespace is
# what agent-api already uses for capability material (``.invites/``, ``.attached/``): every
# enumerator skips it, and the runtime binds one subpath per mounted workspace and never the store
# root (``runtime_kernel.mounts.workspace_binds``), so it is not merely unwritten by a worker, it is
# not present in one. Nothing a caller route or an agent turn can reach writes it.
#
# THE TREE KEEPS A MIRROR. The platform still writes ``policy/*.json`` into the workspace and commits
# it (author = the person who decided, #1632), because three readers want the roster IN the tree:
# the worker's private-write hold counts readers there (``llm/private_writes.other_readers``), a
# group is told from a desk by it (``workspace_ids.kind_of``), and the workspace's own history is the
# audit of who added whom. The mirror is NEVER read for access. Editing it changes nothing.
#
# MIGRATION, READ-COMPATIBLE. A workspace with no store directory yet has its legacy in-tree files
# ADOPTED once — all three together, under the workspace lock, at agent-api start
# (``migrate_policy_store``) and on first read. After that the store answers alone. Adoption from
# the tree is a window: a tree file written after the platform last wrote it would be believed once.
# ``VEXA_POLICY_LEGACY_READ=0`` closes it after the release that migrated every instance.
#
# FAIL CLOSED (founder FD3, 2026-10-17): a store file that exists and cannot be read refuses
# (``MembershipError`` 503, ``policy_unreadable``) — it is never "no members", never "open".
POLICY_STORE_DIR = ".policy"
#: What the store holds per workspace (plus ``invites``). The evaluator's rule FILES stay in git,
#: platform-only (``read_rules``): git keeps content and rule files, the store keeps who is in.
POLICY_NAMES = ("members", "visibility", "connections")
_ADOPTED_NAMES = POLICY_NAMES
#: ONE INTERFACE (``PolicyStore``) for membership policy and invites: the identity registry in a
#: deployment, the file backend (``.policy/`` + ``.invites/``) without one.
#: ``kind`` (0.13.4 S4, FD4): ``{"kind": "pair", "people": [a, b], "bills_to": a}`` for the hidden
#: workspace behind a room of two; absent for an ordinary workspace. Never mirrored into the tree.
STORE_NAMES = POLICY_NAMES + ("invites", "kind")
#: CLOSED ROOMS' MEMBERS (0.13.4 D36): the registry's ``room:<id>`` rows. The file backend keeps
#: them at ``<root>/.rooms/<room id>.json``, beside ``.policy/`` and outside every mount.
ROOM_STORE_DIR = ".rooms"
POLICY_UNREADABLE = "policy_unreadable"
LEGACY_READ_ENV = "VEXA_POLICY_LEGACY_READ"

# ── WHERE INVITES LIVE, AND WHY IT IS NOT IN THE WORKSPACE (Vexa-ai/vexa#1645) ───────────────────
#
# Founder, 2026-09-07, opening an invite minted a minute earlier: *"This invite link is not valid.
# Ask whoever sent it for a new one."* Invites lived at ``policy/invites.json`` INSIDE the workspace
# repo, which is the same directory the worker's turn runs in — and the turn's write-back restored
# ``policy/`` to its pre-turn state, deleting every invite that had been minted while the turn ran.
# The workspace's own history said it out loud, once per mint:
#
#     policy: mint invite 41cdb3b6a5841ffc (contributor) for bank-b5e60c     19:28:08
#     bank-b5e60c: policy/invites.json — removed                             19:28:09
#
# The write-back is fixed (``llm/ports._policy_anchor``), and that fix alone would have been enough
# to stop the deletion. It is not enough to stop the CLASS: a file inside a workspace mount is inside
# the agent's own filesystem, and every process that syncs, reverts, publishes or reseeds that tree is
# a second writer on it. So the invite store leaves the tree.
#
# THE THREE CANDIDATES, AND WHY THIS ONE:
#
#   * ``policy/`` — where it was. Inside the mount. The turn's write-back was only the writer we
#     caught; ``workspace_publish``, the attach/carry paths and the reseed route all rewrite that
#     tree too, and an agent with Bash can delete the file outright. Keeping capability material
#     there means keeping a store whose contents any of them may drop.
#   * ``.vexa/`` — the workspace's machinery dot-dir. Same tree, same sweep: the turn commit drops
#     only ``.claude/`` from the index, and ``.vexa/workspace.json`` is deliberately committed
#     (``workspace_ids._commit_identity``). Moving the file there changes which path the sweep
#     deletes, not whether it does. Placement inside the mount was never the fix.
#   * the memberships store — ``policy/members.json``. It STAYS where it is, and invites do not join
#     it, because they are different things. The roster is workspace KNOWLEDGE: who is on this group,
#     readable in the workspace, auditable in its history, portable with it (Q6 — it survives a DB
#     loss because it travels with the repo). An invite is CAPABILITY MATERIAL: a token hash, an
#     expiry, a use count. Nobody reads it in the workspace, and it must NOT travel when a workspace
#     is published to GitHub or attached to somebody else's repository.
#
# So: ``<store-root>/.invites/<workspace_id>.json``, in the store's dot-namespace — the same
# convention ``.attached/`` already uses, and one every enumerator here already skips. The runtime
# binds ONE SUBPATH PER MOUNTED WORKSPACE and never the store root
# (``runtime_kernel.mounts.workspace_binds``: *"a mount AT the root would re-expose the whole store —
# never emit it"*), so this path is not merely unwritten by a worker, it is not present in one.
INVITE_STORE_DIR = ".invites"

#: Where invites lived before the move. READ-ONLY from here on: an invite minted before this change
#: still previews and still redeems (``read_invites`` falls back to it), and the first write for that
#: workspace carries the rows into the new store. Never written again.
LEGACY_INVITES_FILE = f"{POLICY_DIR}/invites.json"
INVITES_FILE = LEGACY_INVITES_FILE  # kept for callers/tests that name the legacy path

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
#: THE CAPS (PRD 0.13.4 D5, security review C3/test 6). The legacy mint route had no upper bound: a
#: contributor minted ``max_uses=1000000`` and a ten-year expiry. The bounds are the newer link
#: door's (``join_links.MAX_MAX_USES`` / ``MAX_EXPIRES_IN_SEC``), so every door that mints agrees.
MAX_INVITE_USES = 1000
MAX_INVITE_EXPIRES_IN_SEC = 365 * 86400

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
_WS_LOCKS: dict[str, "threading.RLock"] = {}
_WS_LOCKS_GUARD = threading.Lock()

#: THE SHARED HALF OF THE LOCK (PRD 0.13.4 M6, security review: "rate limits and locks are
#: process-local"). ``None`` keeps the process-local behaviour above. agent-api's ``main()`` installs
#: a Redis-backed factory (``RedisWorkspaceLock``) when it has a Redis, so two replicas redeeming
#: the same invite serialise on one key instead of each trusting its own RLock. The factory takes a
#: workspace id and returns a context manager; it is entered once per thread however deep the
#: re-entrant local lock nests.
_DISTRIBUTED_LOCK: Optional[Callable[[str], object]] = None
_HELD = threading.local()


def set_distributed_lock(factory: Optional[Callable[[str], object]]) -> None:
    """Install (or, with ``None``, remove) the shared lock every ``_ws_lock`` also takes."""
    global _DISTRIBUTED_LOCK
    _DISTRIBUTED_LOCK = factory


class _WsLock:
    """``with _ws_lock(ws):`` — the process-local RLock, then the shared lock when one is installed.

    The shared lock is taken by the OUTERMOST entry on a thread only (a dict of depths per thread),
    because the local lock is re-entrant and a Redis lock is not: a nested ``read_members`` inside a
    held ``set_role`` must not wait on itself."""

    def __init__(self, workspace_id: str, local: "threading.RLock"):
        self.workspace_id, self.local, self._shared = workspace_id, local, None

    def __enter__(self):
        self.local.acquire()
        depth = getattr(_HELD, "depth", None)
        if depth is None:
            depth = _HELD.depth = {}
        n = depth.get(self.workspace_id, 0)
        if n == 0 and _DISTRIBUTED_LOCK is not None:
            try:
                shared = _DISTRIBUTED_LOCK(self.workspace_id)
                shared.__enter__()
            except BaseException:
                self.local.release()
                raise
            depth.setdefault("_shared", {})[self.workspace_id] = shared
        depth[self.workspace_id] = n + 1
        return self

    def __exit__(self, *exc):
        depth = _HELD.depth
        n = depth.get(self.workspace_id, 1) - 1
        try:
            if n <= 0:
                depth.pop(self.workspace_id, None)
                shared = depth.get("_shared", {}).pop(self.workspace_id, None)
                if shared is not None:
                    shared.__exit__(*exc)
            else:
                depth[self.workspace_id] = n
        finally:
            self.local.release()
        return False

    # The RLock interface a few callers and tests still use.
    def acquire(self, *a, **k):
        return self.local.acquire(*a, **k)

    def release(self):
        return self.local.release()


def _ws_lock(workspace_id: str) -> _WsLock:
    """The lock serializing the read-modify-write-commit of a workspace's policy.

    RE-ENTRANT since 0.13.4 (D5): the one-time adoption of a legacy in-tree roster into the policy
    store runs under this lock (so it cannot overwrite a platform write that landed meanwhile), and a
    read inside a held critical section — ``set_role`` reading the roster it is about to change —
    may be the read that adopts. SHARED since S2 (M6) when a distributed lock is installed."""
    with _WS_LOCKS_GUARD:
        lk = _WS_LOCKS.get(workspace_id)
        if lk is None:
            lk = _WS_LOCKS[workspace_id] = threading.RLock()
    return _WsLock(workspace_id, lk)


class RedisWorkspaceLock:
    """A Redis lock on ``vexa:ws-lock:<workspace>`` (SET NX PX, released only by its holder).

    Waits up to ``wait_s`` and then refuses with ``MembershipError(503, policy_busy)`` rather than
    acting unserialised — the fail-closed direction for a redeem. ``ttl_ms`` bounds a holder that
    died mid-section."""

    PREFIX = "vexa:ws-lock:"

    def __init__(self, client, workspace_id: str, *, ttl_ms: int = 10_000, wait_s: float = 8.0):
        self.client, self.key = client, self.PREFIX + str(workspace_id)
        self.ttl_ms, self.wait_s, self.token = ttl_ms, wait_s, secrets.token_hex(12)

    def __enter__(self):
        deadline = time.monotonic() + self.wait_s
        while True:
            try:
                if self.client.set(self.key, self.token, nx=True, px=self.ttl_ms):
                    return self
            except Exception as exc:  # noqa: BLE001 — an unreachable lock is not a lock
                raise MembershipError(f"policy_busy: the workspace lock is unavailable "
                                      f"({type(exc).__name__})", status=503) from None
            if time.monotonic() >= deadline:
                raise MembershipError("policy_busy: the workspace is being changed; try again",
                                      status=503)
            time.sleep(0.02)

    def __exit__(self, *exc):
        # Compare-and-delete under WATCH: only the holder's own token is removed, never a lock a
        # later holder took after this one's TTL ran out.
        try:
            with self.client.pipeline() as p:
                p.watch(self.key)
                if p.get(self.key) == self.token:
                    p.multi()
                    p.delete(self.key)
                    p.execute()
                else:
                    p.unwatch()
        except Exception as e:  # noqa: BLE001 — the key expires on its own
            log.warning("workspace lock release failed (%s); it expires in %d ms", type(e).__name__,
                        self.ttl_ms)
        return False


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
    from shared.gitexec import run_git

    env = {
        "GIT_AUTHOR_NAME": author_name, "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": "vexa-platform", "GIT_COMMITTER_EMAIL": "platform@vexa.ai",
    }
    ws = Path(ws)
    if not (ws / ".git").exists():
        run_git(ws, "init", "-q", env=env, check=True)
    added = run_git(ws, "add", "--", POLICY_DIR, env=env)
    if added.returncode != 0:
        # policy/ is EXCLUDED in this clone — the workspace has an ATTACHED external repo as its tree
        # (workspace_attach.carry_policy), where the member list is deliberately untracked so it is
        # never pushed to somebody else's repository. The write to disk already happened and
        # ``read_members`` reads the working tree, so this is a legitimate state, not a failure.
        #
        # ⚠ AND THE OTHER REASON `git add` EXITS NON-ZERO IS NOT THAT (Vexa-ai/vexa#1645). An
        # `index.lock` held by the worker's own turn commit lands here too, and a silent `return`
        # reported it as the benign case — the write stayed on disk, uncommitted, and looked to
        # everything downstream like a commit that never needed to happen. An anomaly is a finding:
        # anything that is not the documented exclusion is logged with git's own words.
        stderr = (added.stderr or "").strip()
        if "ignored by one of your .gitignore" not in stderr and "excluded" not in stderr:
            log.warning("policy commit could not stage %s in %s: %s", POLICY_DIR, ws, stderr)
        return
    # commit only if policy/ actually changed (staged diff non-empty)
    staged = run_git(ws, "diff", "--cached", "--quiet", "--", POLICY_DIR, env=env)
    if staged.returncode != 0:  # non-zero == there IS a staged change
        run_git(ws, "commit", "-q", "-m", message, "--", POLICY_DIR, env=env, check=True)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def hash_token(token: str) -> str:
    """The sha256 hex of an invite token — the ONLY form persisted (a leak of policy/invites.json must
    not leak the capability; mirrors the share-link surface WP0.1)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ws_dir(root: Path, workspace_id: str) -> Path:
    """The on-disk workspace repo for ``workspace_id`` (``<root>/<workspace_id>``), traversal-guarded —
    the workspace id is the owner subject's slug (the dir the dispatch mounts).

    A workspace id is ONE top-level name: no separator and no leading dot. A dotted or multi-segment
    id would name a tree inside a store (``.attached/<subject>/<slot>``) — a private workspace that is
    nobody's shared workspace, whatever a ``policy/members.json`` left inside it says."""
    wid = str(workspace_id or "")
    if not wpaths.is_workspace_name(wid):               # the one workspace name rule
        raise MembershipError("invalid workspace id", status=400)
    root = Path(root).resolve()
    ws = (root / workspace_id).resolve()
    if ws != root and root not in ws.parents:
        raise MembershipError("invalid workspace id", status=400)
    return ws


# The policy files sit in the work tree, which the model's tools may write during a turn. So below
# the workspace root nothing is reached through a link (``workspace_paths``): each folder is opened
# without following one, the file is read only when it is a regular file with no other hard link,
# and a write creates a new file and renames it over the name — a link planted at the file is
# replaced, never written through, and a link planted at a folder refuses the write. A list that
# cannot be read that way is empty: no members, never somebody else's.
def _read_json_list(ws: Path, rel: str) -> list[dict]:
    raw = wpaths.read_bytes_inside(ws, rel, allow=_POLICY_ALLOW)
    if raw is None:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("could not parse %s in %s; treating as empty", rel, ws)
        return []
    return data if isinstance(data, list) else []


def _write_json_list(ws: Path, rel: str, rows: list[dict]) -> None:
    try:
        wpaths.write_text_inside(ws, rel, json.dumps(rows, indent=2, sort_keys=False) + "\n",
                                 allow=_POLICY_ALLOW)
    except wpaths.PathRefused as exc:
        raise MembershipError(f"{rel.rsplit('/', 1)[0]} in this workspace is not a plain folder",
                              status=409) from exc


# ── the policy store: ONE interface, two backends ────────────────────────────────────────────────
#
# TARGET (coordinator, 2026-10-17, "as simple as optimal"): the identity database's registry —
# ``workspaces``, ``memberships``, ``invites`` — with admin-api their one writer and agent-api reaching
# them through its port (``shared.adapters.AdminApiPolicyStore``, ``admin_api/app/workspace_registry``).
# A deployment with ``VEXA_ADMIN_API_URL`` uses it (``set_policy_store`` at boot). A deployment
# without identity (tests, a bare single process) keeps the file backend below, outside every
# workspace mount. Both answer the same five names: ``members`` · ``visibility`` · ``connections`` ·
# ``invites`` (values, not bytes), plus ``has`` / ``drop`` / ``member_workspaces`` /
# ``invite_by_hash``. Every call is wrapped: a backend that fails answers ``MembershipError`` 503
# ``policy_unreadable`` — fail closed (FD3), never "no members".
class PolicyStore(Protocol):
    # rooms (S4): ``room_get`` answers None for a room the store has no rows for, never [] by guess
    def room_get(self, root: Path, room_id: str) -> Optional[list]: ...
    def room_put(self, root: Path, room_id: str, rows: list) -> None: ...
    def room_drop(self, root: Path, room_id: str) -> bool: ...
    def principal_rooms(self, root: Path, subject: str) -> list[dict]: ...
    def has(self, root: Path, workspace_id: str) -> bool: ...
    def get(self, root: Path, workspace_id: str, name: str): ...
    def put(self, root: Path, workspace_id: str, name: str, value) -> None: ...
    def drop(self, root: Path, workspace_id: str) -> bool: ...
    def member_workspaces(self, root: Path, subject: str) -> list[dict]: ...
    def invite_by_hash(self, root: Path, token_hash: str) -> Optional[tuple[str, dict]]: ...


def _store_rel(workspace_id: str, name: str = "") -> str:
    """The FILE backend's spelling: ``.policy/<workspace_id>[/<name>.json]``, and
    ``.invites/<workspace_id>.json`` for invites."""
    wid = str(workspace_id or "").strip()
    if not wpaths.is_workspace_name(wid):
        raise MembershipError("invalid workspace id", status=400)
    if name and name not in STORE_NAMES:
        raise ValueError(f"unknown policy record {name!r}")
    if name == "invites":
        return f"{INVITE_STORE_DIR}/{wid}.json"
    return f"{POLICY_STORE_DIR}/{wid}" + (f"/{name}.json" if name else "")


class FilePolicyStore:
    """The file backend: ``<root>/.policy/<ws>/`` and ``<root>/.invites/<ws>.json``, beside the
    workspaces and outside every mount (the runtime binds one subpath per workspace, never the root)."""

    def has(self, root, workspace_id):
        return wpaths.is_dir_inside(Path(root), _store_rel(workspace_id))

    def get(self, root, workspace_id, name):
        rel = _store_rel(workspace_id, name)
        raw = wpaths.read_bytes_inside(Path(root), rel)
        if raw is None:
            if wpaths.stat_inside(Path(root), rel) is not None:     # a link, a folder: not ours to read
                raise MembershipError(f"{POLICY_UNREADABLE}: {name} cannot be read", status=503)
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise MembershipError(f"{POLICY_UNREADABLE}: {name} cannot be read", status=503) from None

    def put(self, root, workspace_id, name, value):
        try:
            wpaths.write_text_inside(Path(root), _store_rel(workspace_id, name),
                                     json.dumps(value, indent=2, sort_keys=False) + "\n")
        except (wpaths.PathRefused, OSError) as exc:
            raise MembershipError(f"{POLICY_UNREADABLE}: the policy store could not be written",
                                  status=503) from exc

    def drop(self, root, workspace_id):
        root = Path(root)
        dropped = False
        for name in STORE_NAMES:
            dropped = wpaths.unlink_inside(root, _store_rel(workspace_id, name)) or dropped
        try:
            os.rmdir(root / _store_rel(workspace_id))
        except OSError:
            pass
        return dropped

    def member_workspaces(self, root, subject):
        out = []
        for slug in wpaths.list_dirs_inside(Path(root), POLICY_STORE_DIR):
            if not wpaths.is_workspace_name(slug):
                continue
            try:
                rows = self.get(root, slug, "members") or []
            except MembershipError as exc:          # unreadable: not listed (fail closed)
                log.warning("membership listing skipped %s: %s", slug, exc)
                continue
            for r in rows if isinstance(rows, list) else []:
                if isinstance(r, dict) and r.get("subject") == subject:
                    out.append({"workspace_id": slug, "role": r.get("role"), "added_at": r.get("added_at")})
                    break
        return out

    # ── closed rooms' members (S4, D36) ─────────────────────────────────────────────────────
    @staticmethod
    def _room_rel(room_id: str) -> str:
        rid = str(room_id or "").strip()
        if not wpaths.is_workspace_name(rid, tier=False):
            raise MembershipError("invalid room id", status=400)
        return f"{ROOM_STORE_DIR}/{rid}.json"

    def room_get(self, root, room_id):
        rel = self._room_rel(room_id)
        raw = wpaths.read_bytes_inside(Path(root), rel)
        if raw is None:
            if wpaths.stat_inside(Path(root), rel) is not None:
                raise MembershipError(f"{POLICY_UNREADABLE}: room members cannot be read", status=503)
            return None
        try:
            rows = json.loads(raw)
        except ValueError:
            raise MembershipError(f"{POLICY_UNREADABLE}: room members cannot be read", status=503) from None
        if not isinstance(rows, list):
            raise MembershipError(f"{POLICY_UNREADABLE}: room members cannot be read", status=503)
        return rows

    def room_put(self, root, room_id, rows):
        try:
            wpaths.write_text_inside(Path(root), self._room_rel(room_id), json.dumps(list(rows), indent=2) + "\n")
        except (wpaths.PathRefused, OSError) as exc:
            raise MembershipError(f"{POLICY_UNREADABLE}: room members could not be written", status=503) from exc

    def room_drop(self, root, room_id):
        return wpaths.unlink_inside(Path(root), self._room_rel(room_id))

    def principal_rooms(self, root, subject):
        out = []
        for name in wpaths.list_files_inside(Path(root), ROOM_STORE_DIR, suffix=".json"):
            rid = name[:-len(".json")]
            try:
                rows = self.room_get(root, rid) or []
            except MembershipError:
                continue
            for r in rows:
                if isinstance(r, dict) and r.get("subject") == subject:
                    out.append({"room_id": rid, "role": r.get("role"), "added_at": r.get("added_at")})
                    break
        return out

    def invite_by_hash(self, root, token_hash):
        for name in wpaths.list_files_inside(Path(root), INVITE_STORE_DIR, suffix=".json"):
            slug = name[:-len(".json")]
            if not wpaths.is_workspace_name(slug) or slug in RESERVED_SLUGS:
                continue
            try:
                rows = self.get(root, slug, "invites") or []
            except MembershipError:
                continue
            for rec in rows if isinstance(rows, list) else []:
                if isinstance(rec, dict) and rec.get("hash") == token_hash:
                    return slug, rec
        return None


_FILE_STORE = FilePolicyStore()
_STORE: PolicyStore = _FILE_STORE
#: Workspaces this process has seen in the store — so a listing never asks the backend again.
_KNOWN: set[tuple[str, str]] = set()

#: ONE READ SCOPE (2026-10-18): inside ``read_scope()`` a workspace found to have nothing to adopt,
#: and whether a workspace is a pair, are remembered until the scope ends. Neither depends on who is
#: asking, so a request that enumerates several people's memberships (the chat list names every
#: other person on it) scans the host's folders once instead of once per person. Outside a scope
#: nothing is remembered.
_SCOPE: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("membership_read_scope", default=None)


@contextlib.contextmanager
def read_scope():
    """Remember subject-independent policy reads until the block ends (re-entrant)."""
    if _SCOPE.get() is not None:
        yield
        return
    token = _SCOPE.set({})
    try:
        yield
    finally:
        _SCOPE.reset(token)


def scope_memo(key: tuple, compute):
    """``compute()`` once per read scope for ``key`` (e.g. every roster on the host, or one person's
    mount set — the key names whose); outside a scope it is simply called. A policy write inside the scope forgets every
    memo, so a request that writes and then reads sees its own write."""
    scope = _SCOPE.get()
    if scope is None:
        return compute()
    k = ("memo", *key)
    if k in scope:
        return scope[k]
    value = compute()
    scope[k] = value
    return value


def _scope_forget() -> None:
    scope = _SCOPE.get()
    if scope:
        for k in [k for k in scope if k and k[0] == "memo"]:
            del scope[k]


def set_policy_store(store: Optional[PolicyStore]) -> None:
    """Choose the backend (agent-api's composition root: the identity registry when admin-api is
    configured). ``None`` restores the file backend."""
    global _STORE
    _STORE = store if store is not None else _FILE_STORE
    _KNOWN.clear()


def policy_store() -> PolicyStore:
    return _STORE


def _call(fn, *args):
    try:
        return fn(*args)
    except MembershipError:
        raise
    except Exception as exc:  # noqa: BLE001 — an unreachable store refuses (FD3)
        log.error("policy store %s failed: %s", getattr(fn, "__name__", "call"), exc)
        raise MembershipError(f"{POLICY_UNREADABLE}: the membership store did not answer",
                              status=503) from None


def room_store_get(root: Path, room_id: str) -> Optional[list]:
    """A closed room's member rows from the store, or None when it holds none (fail closed: 503)."""
    return _call(_STORE.room_get, Path(root), room_id)


def room_store_put(root: Path, room_id: str, rows: list) -> None:
    _call(_STORE.room_put, Path(root), room_id, list(rows))


def room_store_drop(root: Path, room_id: str) -> bool:
    dropped = bool(_call(_STORE.room_drop, Path(root), room_id))
    if _STORE is not _FILE_STORE:                       # and the interim file, so nothing re-adopts it
        dropped = bool(_FILE_STORE.room_drop(Path(root), room_id)) or dropped
    return dropped


def legacy_room_rows(root: Path, room_id: str) -> Optional[list]:
    """A closed room's rows in the INTERIM file store (``.rooms/<id>.json``) when the registry is the
    backend — what a room decided before its deployment had identity, adopted before the participant
    projection is ever guessed from. None when there is no such file or it cannot be read."""
    if _STORE is _FILE_STORE:
        return None
    try:
        rows = _FILE_STORE.room_get(Path(root), room_id)
    except MembershipError as exc:
        log.warning("room store: interim rows of %s not adopted: %s", room_id, exc)
        return None
    return [r for r in rows if isinstance(r, dict) and r.get("subject")] if rows is not None else None


def is_pair(root: Path, workspace_id: str) -> bool:
    """Is ``workspace_id`` the hidden workspace behind a room of two (0.13.4 FD4)? A store that cannot
    answer says no here; the membership reads that follow refuse on their own (FD3)."""
    scope = _SCOPE.get()
    key = ("pair", str(root), str(workspace_id or ""))
    if scope is not None and key in scope:
        return scope[key]
    try:
        if not wpaths.is_workspace_name(str(workspace_id or "")):
            return False
        v = _call(_STORE.get, Path(root), workspace_id, "kind")
    except MembershipError:
        return False
    got = isinstance(v, dict) and v.get("kind") == "pair"
    if scope is not None:
        scope[key] = got
    return got


def store_get(root: Path, workspace_id: str, name: str):
    return _call(_STORE.get, Path(root), workspace_id, name)


def store_put(root: Path, workspace_id: str, name: str, value) -> None:
    _call(_STORE.put, Path(root), workspace_id, name, value)
    _KNOWN.add((str(root), workspace_id))
    _scope_forget()


def _legacy_read() -> bool:
    return os.environ.get(LEGACY_READ_ENV, "1").strip().lower() not in ("0", "false", "no", "off")


def policy_in_store(root: Path, workspace_id: str) -> bool:
    """True once ``workspace_id``'s policy lives in the store (migrated or written since 0.13.4)."""
    _store_rel(workspace_id)                        # the workspace-id rule
    if (str(root), workspace_id) in _KNOWN:
        return True
    if _call(_STORE.has, Path(root), workspace_id):
        _KNOWN.add((str(root), workspace_id))
        return True
    return False


def _legacy_sources(root: Path, workspace_id: str) -> dict:
    """What an unmigrated workspace has elsewhere: the interim file store (when the backend is not
    it) first, else the workspace tree's ``policy/*.json``; and its invites (``.invites`` file, else
    the tree's ``policy/invites.json``). ``{name: value}``; unparseable files are skipped, said."""
    found: dict = {}
    if _STORE is not _FILE_STORE and _FILE_STORE.has(root, workspace_id):
        # the interim store also knows a room of two's hidden workspace (``kind``, never in the tree)
        for name in _ADOPTED_NAMES + ("kind",):
            try:
                v = _FILE_STORE.get(root, workspace_id, name)
            except MembershipError:
                continue
            if v is not None:
                found[name] = v
    else:
        try:
            ws = _ws_dir(root, workspace_id)
        except MembershipError:
            ws = None
        for name in _ADOPTED_NAMES if ws is not None else ():
            raw = wpaths.read_text_inside(ws, f"{POLICY_DIR}/{name}.json", allow=_POLICY_ALLOW)
            if raw is None:
                continue
            try:
                found[name] = json.loads(raw)
            except ValueError:
                log.warning("policy store: legacy %s/%s.json in %s does not parse; not adopted",
                            POLICY_DIR, name, workspace_id)
    if _STORE is not _FILE_STORE and found:
        try:
            inv = _FILE_STORE.get(root, workspace_id, "invites")
        except MembershipError:
            inv = None
        if inv is None:
            try:
                inv = _read_json_list(_ws_dir(root, workspace_id), LEGACY_INVITES_FILE) or None
            except MembershipError:
                inv = None
        if isinstance(inv, list) and inv:
            found["invites"] = inv
    return found


def _ensure_migrated(root: Path, workspace_id: str) -> bool:
    """Adopt an unmigrated workspace's policy (and, for the registry, its invites) into the store,
    once and all together. True when the store holds this workspace's policy afterwards. Under the
    workspace lock, re-checked inside, so an adoption never overwrites a write that landed meanwhile."""
    root = Path(root)
    if policy_in_store(root, workspace_id):
        return True
    scope = _SCOPE.get()
    key = ("unadoptable", str(root), workspace_id)
    if scope is not None and key in scope:
        return False
    adopted = _adopt(root, workspace_id)
    if scope is not None and not adopted:
        scope[key] = True
    return adopted


def _adopt(root: Path, workspace_id: str) -> bool:
    with _ws_lock(workspace_id):
        if policy_in_store(root, workspace_id):
            return True
        if not _legacy_read():
            return False
        found = _legacy_sources(root, workspace_id)
        if not found:
            return False
        for name in ("members", "visibility", "connections", "kind", "invites"):
            if name in found:
                store_put(root, workspace_id, name, found[name])
        log.info("policy store: adopted %s for %s", sorted(found), workspace_id)
        return True


def read_policy(root: Path, workspace_id: str, name: str, default):
    """THE ONE READER of a workspace's policy (``members`` · ``visibility`` · ``connections``).

    The store answers; a workspace not yet in it has its legacy records adopted first (see the
    header). ``default`` when the workspace has no such policy. A store that cannot answer raises
    ``MembershipError`` 503 ``policy_unreadable`` — fail closed (FD3)."""
    root = Path(root)
    _store_rel(workspace_id, name)
    if not _ensure_migrated(root, workspace_id):
        return default
    value = store_get(root, workspace_id, name)
    return default if value is None else value


def write_policy(root: Path, workspace_id: str, name: str, value, *,
                 commit_fn: Optional[CommitFn] = None, message: str = "") -> None:
    """THE ONE WRITER: the store (authoritative), then the tree's ``policy/<name>.json`` mirror and its
    commit. A workspace still on legacy records is migrated first, so writing one never strands the
    others."""
    root = Path(root)
    with _ws_lock(workspace_id):
        _ensure_migrated(root, workspace_id)
        store_put(root, workspace_id, name, value)
        try:
            ws = _ws_dir(root, workspace_id)
        except MembershipError:
            return
        if not ws.is_dir():
            return
        text = json.dumps(value, indent=2, sort_keys=False) + "\n"
        try:
            wpaths.write_text_inside(ws, f"{POLICY_DIR}/{name}.json", text, allow=_POLICY_ALLOW)
        except (wpaths.PathRefused, OSError) as exc:
            # The store holds the decision; only the mirror is stale. Said, never swallowed silently.
            log.warning("policy mirror %s/%s.json not written in %s: %s", POLICY_DIR, name, workspace_id, exc)
            return
        _commit(commit_fn, ws, message or f"policy: {name} for {workspace_id}")


def drop_policy_store(root: Path, workspace_id: str) -> bool:
    """Forget a workspace's policy and invites in the store — for a workspace that stopped being a
    group (un-share, delete). A later workspace with the same id starts with no roster, never this
    one's. True when there was something to drop."""
    _store_rel(workspace_id)
    _KNOWN.discard((str(root), workspace_id))
    _scope_forget()
    dropped = bool(_call(_STORE.drop, Path(root), workspace_id))
    if _STORE is not _FILE_STORE:                       # and the interim files, so nothing re-adopts them
        dropped = _FILE_STORE.drop(Path(root), workspace_id) or dropped
    return dropped


def read_rules(root: Path, workspace_id: str) -> list:
    """A workspace's own policy RULES for the evaluator (``policy_decide``, D7). Rule files stay in
    git, platform-only (``policy/rules.json``: reserved for every caller route, reverted when a turn
    touches it). ``[]`` when there are none; ``MembershipError`` 503 when the file cannot be read."""
    ws = _ws_dir(root, workspace_id)
    rel = f"{POLICY_DIR}/rules.json"
    raw = wpaths.read_text_inside(ws, rel, allow=_POLICY_ALLOW)
    if raw is None:
        if wpaths.stat_inside(ws, rel, allow=_POLICY_ALLOW) is not None:
            raise MembershipError(f"{POLICY_UNREADABLE}: rules cannot be read", status=503)
        return []
    try:
        rules = json.loads(raw)
    except ValueError:
        raise MembershipError(f"{POLICY_UNREADABLE}: rules cannot be read", status=503) from None
    if not isinstance(rules, list):
        raise MembershipError(f"{POLICY_UNREADABLE}: rules cannot be read", status=503)
    return rules


def migrate_policy_store(root: Path) -> list[str]:
    """Adopt every workspace's legacy policy (interim files, else tree files; and their invites)
    into the store — run once at agent-api start, so the read-compatible window lasts no longer than
    the boot. Idempotent. Never raises. The workspace ids adopted by this call."""
    root = Path(root)
    adopted: list[str] = []
    if not _legacy_read():
        return adopted
    try:
        names = set(wpaths.list_dirs_inside(root, ""))
        if _STORE is not _FILE_STORE:
            names |= set(wpaths.list_dirs_inside(root, POLICY_STORE_DIR))
    except Exception:  # noqa: BLE001
        return adopted
    for slug in sorted(names):
        if slug.startswith(".") or slug in RESERVED_SLUGS or not wpaths.is_workspace_name(slug):
            continue
        try:
            if policy_in_store(root, slug):
                continue
            if _ensure_migrated(root, slug):
                adopted.append(slug)
        except Exception as exc:  # noqa: BLE001 — one bad tree never stops the rest
            log.warning("policy store: %s not migrated: %s", slug, exc)
    if adopted:
        log.info("policy store: migrated %d workspace(s)", len(adopted))
    return adopted


def _members(root: Path, workspace_id: str) -> list[dict]:
    rows = read_policy(root, workspace_id, "members", [])
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _save_members(root: Path, workspace_id: str, rows: list[dict], *,
                  commit_fn: Optional[CommitFn], message: str) -> None:
    write_policy(root, workspace_id, "members", rows, commit_fn=commit_fn, message=message)


# ── the invite store (outside every workspace mount — see INVITE_STORE_DIR above) ────────────────
def invites_path(root: Path, workspace_id: str) -> Path:
    """THE ONE DOOR to a workspace's invites: ``<root>/.invites/<workspace_id>.json``.

    Every route that mints, previews, accepts, lists or revokes resolves the file through this
    function and no other, so "persisted somewhere the preview does not read" is not a state this
    code can be in. Traversal-guarded on the workspace id for the same reason ``_ws_dir`` is."""
    slug = str(workspace_id or "").strip()
    if not wpaths.is_workspace_name(slug):              # the one workspace name rule
        raise MembershipError("invalid workspace id", status=400)
    root = Path(root).resolve()
    path = (root / INVITE_STORE_DIR / f"{slug}.json").resolve()
    if root not in path.parents:
        raise MembershipError("invalid workspace id", status=400)
    return path


def read_invites(root: Path, workspace_id: str) -> list[dict]:
    """A workspace's invite records (``[]`` if none), from the store. The file backend falls back to
    the LEGACY in-tree ``policy/invites.json`` while that workspace has no invite file yet; the
    registry adopts invites together with the roster (``_ensure_migrated``)."""
    invites_path(root, workspace_id)                # the traversal guard, as before
    if _STORE is not _FILE_STORE:
        _ensure_migrated(Path(root), workspace_id)
    rows = store_get(root, workspace_id, "invites")
    if rows is not None:
        return rows if isinstance(rows, list) else []
    if _STORE is not _FILE_STORE:
        return []
    return _read_json_list(_ws_dir(root, workspace_id), LEGACY_INVITES_FILE)


def _write_invites(root: Path, workspace_id: str, rows: list[dict]) -> None:
    """Persist a workspace's invites. Always to the store — never back to the legacy in-tree file —
    so the first write after the move migrates whatever the fallback read."""
    invites_path(root, workspace_id)
    _call(_STORE.put, Path(root), workspace_id, "invites", rows)


def drop_invites(root: Path, workspace_id: str) -> bool:
    """Remove a workspace's invites, so no invite minted for it can be redeemed. True when there
    were any. For a workspace that stopped being a group (un-share, delete)."""
    invites_path(root, workspace_id)
    if _STORE is _FILE_STORE:
        return wpaths.unlink_inside(Path(root), _store_rel(workspace_id, "invites"))
    had = bool(store_get(root, workspace_id, "invites"))
    _call(_STORE.put, Path(root), workspace_id, "invites", [])
    return had


def strip_policy(ws: Path, *, commit_fn: Optional[CommitFn] = None) -> list[str]:
    """Remove the member list (and the legacy in-tree invite list) from a tree that is no longer a
    shared workspace — un-share moves it into its owner's private store, where a member list left
    inside would still name people who no longer have access. Each file is removed through the policy
    folder's descriptor, never through a link; the removal is committed (as the platform, unless
    ``commit_fn`` says otherwise) so the tree's history records it. The relative paths removed."""
    # each removed through the folders' descriptors (``workspace_paths.unlink_inside``): a link where
    # the policy folder should be removes nothing, and a link at a file is removed itself
    removed = [rel for rel in (MEMBERS_FILE, VISIBILITY_FILE, CONNECTIONS_FILE, LEGACY_INVITES_FILE)
               if wpaths.unlink_inside(Path(ws), rel, allow=_POLICY_ALLOW)]
    if removed:
        _commit(commit_fn or policy_commit, Path(ws), "unshare: the member list leaves with the group")
    return removed


def workspaces_with_invites(root: Path) -> list[str]:
    """Every shareable workspace id that has invites to look at — store files first, then any
    workspace still on the legacy in-tree file. Sorted and de-duplicated; reserved and dot-prefixed
    slugs are skipped exactly as ``assert_shareable`` refuses them."""
    root = Path(root)
    found: set[str] = set()
    store_dir = root / INVITE_STORE_DIR
    if store_dir.is_dir():
        found |= {p.stem for p in store_dir.glob("*.json")}
    if root.exists():
        for child in root.iterdir():
            if child.is_dir() and (child / LEGACY_INVITES_FILE).exists():
                found.add(child.name)
    return sorted(s for s in found if s and not s.startswith(".") and s not in RESERVED_SLUGS)


def find_invite(root: Path, token: str) -> Optional[tuple[str, dict]]:
    """``(workspace_id, record)`` for the invite ``token`` resolves to, or ``None``.

    The ONE resolver: ``preview`` and ``accept`` both call it, so a token can never resolve for one
    and not the other. Matched on the sha256 hash — the only form persisted. The store answers first;
    an invite still in a legacy file (read-compatible migration) is found by scanning, and its
    workspace is adopted on the way."""
    h = hash_token(token)
    root = Path(root)
    hit = _call(_STORE.invite_by_hash, root, h)
    if hit is not None:
        return hit
    for slug in workspaces_with_invites(root):
        if _STORE is not _FILE_STORE:
            _ensure_migrated(root, slug)
        for rec in read_invites(root, slug):
            if rec.get("hash") == h:
                return slug, rec
    return None


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
    """The authoritative member list (``[]`` if none) — from the policy store, never from the tree's
    ``policy/members.json`` mirror (D5). Raises ``MembershipError`` 503 when the store cannot be read."""
    _ws_dir(root, workspace_id)                     # the workspace-id rule, as before
    return _members(root, workspace_id)


def backfill_member_email(root: Path, workspace_id: str, subject: str, email: Optional[str], *,
                          commit_fn: Optional[CommitFn] = None) -> bool:
    """Best-effort: stamp ``subject``'s VERIFIED email onto their EXISTING member record when it's missing
    or stale. Self-heals members granted before emails were stored — each member's row fills in the first
    time they open the manage panel (which carries their gateway-verified ``X-User-Email``). Returns True
    iff it wrote. Never creates a membership and never raises for a non-member — purely a label refresh."""
    _email = _normalize_email(email)
    if not _email:
        return False
    _ws_dir(root, workspace_id)
    with _ws_lock(workspace_id):
        members = _members(root, workspace_id)
        target = next((m for m in members if m.get("subject") == subject), None)
        if target is None or target.get("email") == _email:
            return False
        target["email"] = _email
        _save_members(root, workspace_id, members, commit_fn=commit_fn,
                      message=f"policy: email for {subject} in {workspace_id}")
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
    out = list(_call(_STORE.member_workspaces, root, subject) or [])
    seen = {r.get("workspace_id") for r in out}
    # Read-compatible migration: a tree (or interim file) not yet adopted is adopted here, then asked.
    if _legacy_read():
        candidates = {c.name for c in root.iterdir() if c.is_dir()}
        if _STORE is not _FILE_STORE:
            candidates |= set(wpaths.list_dirs_inside(root, POLICY_STORE_DIR))
        for slug in sorted(candidates - seen):
            if slug.startswith(".") or slug in RESERVED_SLUGS or not wpaths.is_workspace_name(slug):
                continue
            try:
                if policy_in_store(root, slug) or not _ensure_migrated(root, slug):
                    continue
                for member in _members(root, slug):
                    if member.get("subject") == subject:
                        out.append({"workspace_id": slug, "role": member.get("role"),
                                    "added_at": member.get("added_at")})
                        break
            except MembershipError as exc:          # unreadable store: not listed (fail closed)
                log.warning("membership listing skipped %s: %s", slug, exc)
    # A PAIR WORKSPACE IS HIDDEN (0.13.4 FD4): never a workspace in a list, a mount or the people
    # directory — its two people reach it only through their room of two.
    out = [r for r in out if not is_pair(root, str(r.get("workspace_id") or ""))]
    return sorted(out, key=lambda r: str(r.get("workspace_id")))


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
    rows = [r for r in rows if not (isinstance(r, dict) and is_pair(root, str(r.get("workspace_id") or "")))]
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
    members = _members(root, workspace_id)
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
    _save_members(root, workspace_id, members, commit_fn=commit_fn,
                  message=f"policy: owner {owner_subject} for {workspace_id}")
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
    assert_shareable(root, workspace_id)
    with _ws_lock(workspace_id):
        return _grant_locked(root, workspace_id, subject, role, added_by=added_by, index=index,
                             email=email, commit_fn=commit_fn)


def _grant_locked(root: Path, workspace_id: str, subject: str, role: str, *, added_by: str,
                  index: MembershipIndex, email: Optional[str], commit_fn: Optional[CommitFn]) -> dict:
    members = _members(root, workspace_id)
    if is_pair(root, workspace_id) and not any(m.get("subject") == subject for m in members):
        # A ROOM OF TWO HAS TWO PEOPLE (0.13.4 FD4, SF-11): a third is never granted, by any door.
        raise MembershipError("a room of two has exactly two people; start a room in a workspace "
                              "to talk with more", status=409)
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
    _save_members(root, workspace_id, members, commit_fn=commit_fn,
                  message=f"policy: {role} for {subject} in {workspace_id}")
    _index_add(index, subject, workspace_id, role, record.get("added_at", now))
    return dict(record)


def set_role(root: Path, workspace_id: str, subject: str, role: str, *,
             changed_by: str, index: MembershipIndex, commit_fn: Optional[CommitFn] = None) -> dict:
    """Flip a member's role (the "easily change read/write permissions" DoD item). Owner-only at the API
    layer. Refuses to demote/alter a non-member and refuses to strip the LAST owner (a workspace must
    always retain an owner)."""
    role = normalize_role(role)
    assert_shareable(root, workspace_id)
    with _ws_lock(workspace_id):  # serialize the roster read-modify-write (last-owner check + flip)
        members = _members(root, workspace_id)
        target = next((m for m in members if m.get("subject") == subject), None)
        if target is None:
            raise MembershipError("not a member", status=404)
        if target.get("role") == "owner" and role != "owner":
            owners = [m for m in members if m.get("role") == "owner"]
            if len(owners) <= 1:
                raise MembershipError("cannot remove the last owner", status=409)
        target["role"] = role
        _save_members(root, workspace_id, members, commit_fn=commit_fn,
                      message=f"policy: role {subject} -> {role} in {workspace_id}")
        _index_add(index, subject, workspace_id, role, target.get("added_at", _now_iso()))
        return dict(target)


def remove_member(root: Path, workspace_id: str, subject: str, *,
                  index: MembershipIndex, commit_fn: Optional[CommitFn] = None) -> None:
    """Remove a member from BOTH stores. Refuses to remove the last owner."""
    assert_shareable(root, workspace_id)
    with _ws_lock(workspace_id):  # serialize the roster read-modify-write (last-owner check + drop)
        members = _members(root, workspace_id)
        target = next((m for m in members if m.get("subject") == subject), None)
        if target is None:
            return  # idempotent
        if target.get("role") == "owner":
            owners = [m for m in members if m.get("role") == "owner"]
            if len(owners) <= 1:
                raise MembershipError("cannot remove the last owner", status=409)
        members = [m for m in members if m.get("subject") != subject]
        _save_members(root, workspace_id, members, commit_fn=commit_fn,
                      message=f"policy: remove {subject} from {workspace_id}")
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


def invite_role_allowed(root: Path, workspace_id: str, inviter: str, role: str, *,
                        owner_act: bool = False) -> str:
    """The invite role ``inviter`` may hand out in ``workspace_id``, or ``MembershipError`` 403.

    Two rules (PRD 0.13.4 D5): **an invite never outranks its sender** — a contributor invites
    contributors and readers, a reader nobody above a reader — and **an owner is made only through
    the owner-gated act** (``owner_act=True``: ``membership_acts.invite``, behind
    ``assert_may_manage``), never through a link or the generic mint route, whoever calls it. Checked
    at mint and again at redeem, against the sender's role THEN, so an invite whose sender has since
    been demoted or removed grants no more than they now hold."""
    role = normalize_role(role)
    if role == "owner" and not owner_act:
        raise MembershipError("an owner is made only by an owner's own invite to a named person, "
                              "never by a link", status=403)
    held = is_member(root, workspace_id, inviter)
    if held is None or _RANK.get(held, -1) < _RANK[role]:
        raise MembershipError(f"an invite cannot give more than its sender holds ({role_word(held) or 'none'})",
                              status=403)
    return role


def mint_invite(root: Path, workspace_id: str, *, role: str, created_by: str,
                expires_in_sec: int = DEFAULT_EXPIRES_IN_SEC, max_uses: int = DEFAULT_MAX_USES,
                mode: str = DEFAULT_INVITE_MODE, allowed_emails: Optional[list[str]] = None,
                commit_fn: Optional[CommitFn] = None, now: Optional[float] = None,
                owner_act: bool = False, room=None) -> MintedInvite:
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
    if max_uses < 1 or max_uses > MAX_INVITE_USES:
        raise MembershipError(f"max_uses must be between 1 and {MAX_INVITE_USES}", status=400)
    if expires_in_sec <= 0 or expires_in_sec > MAX_INVITE_EXPIRES_IN_SEC:
        raise MembershipError(f"expires_in_sec must be between 1 and {MAX_INVITE_EXPIRES_IN_SEC}",
                              status=400)
    if mode not in INVITE_MODES:
        raise MembershipError(f"invite mode must be one of {INVITE_MODES}", status=400)
    emails = sorted({_normalize_email(e) for e in (allowed_emails or []) if _normalize_email(e)})
    if mode == "restricted" and not emails:
        raise MembershipError("restricted invite requires allowed_emails", status=400)
    assert_shareable(root, workspace_id)
    invite_role_allowed(root, workspace_id, created_by, role, owner_act=owner_act)
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
    if owner_act:
        rec["owner_act"] = True
    if isinstance(room, str) and room.strip():
        room = {"id": room}               # a tag's invite (S4, D21) names the room by id
    if isinstance(room, dict) and str(room.get("id") or "").strip():
        # THE ROOM THE INVITE LANDS IN (PRD 0.13.4 S2; a tag's invite, S4 D21 case 3/4): a room of this
        # workspace, checked by the caller. Pending room membership, never a grant before they accept.
        rec["room"] = {"id": str(room["id"]).strip()[:128], "title": str(room.get("title") or "")[:200]}
    # ONE STORE, AND IT IS NOT THE WORKSPACE REPO (Vexa-ai/vexa#1645). ``commit_fn`` is deliberately
    # not called here any more: there is nothing in the workspace tree to commit, and an invite is not
    # workspace history — the audit of who was invited lands in ``members.json`` (``added_by``) the
    # moment the link is redeemed. Callers still pass their writer because the same call grants
    # memberships elsewhere in this module; here it has nothing to write.
    with _ws_lock(workspace_id):
        invites = read_invites(root, workspace_id)
        invites.append(rec)
        _write_invites(root, workspace_id, invites)
    return MintedInvite(id=invite_id, token=token, role=role,
                        expires_at=rec["expires_at"], max_uses=rec["max_uses"])


def preview_invite(root: Path, token: str, *, now: Optional[float] = None) -> Optional[dict]:
    """Resolve an invite TOKEN to a READ-ONLY preview — the target workspace + terms (role · mode ·
    expiry · validity) — WITHOUT granting membership. Capability-gated by the token itself: whoever holds
    the link may see what they are being invited to, which is exactly what the pre-join consent screen
    needs (it renders before login). Returns ``None`` when no invite matches the token (never leaks which
    workspaces exist). A revoked, expired or used-up token returns ``{"valid": False, "reason"}`` and
    no terms. Read-only: no writes, no use-count change, no membership."""
    t = now if now is not None else time.time()
    found = find_invite(Path(root), token)
    if found is None:
        return None
    slug, rec = found
    expired = int(rec.get("expires_at", 0)) < t
    used_up = int(rec.get("uses", 0)) >= int(rec.get("max_uses", 1))
    reason = ("revoked" if rec.get("revoked") else "expired" if expired
              else "used_up" if used_up else None)
    if reason is not None:
        # A token that can no longer be redeemed says why, and nothing about the workspace.
        return {"valid": False, "reason": reason}
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
        "room": dict(rec["room"]) if isinstance(rec.get("room"), dict) else None,
        # Whether the link itself may sign its one address in, now (``signin_eligible``).
        "signin": signin_eligible(rec, now=t),
    }


# ── THE LINK THAT SIGNS ITS ONE ADDRESS IN (PRD 0.13.4 S2, E1) ─────────────────────────────────
#
# A restricted invite to ONE address, which this deployment MAILED to that address (delivery
# ``delivered``), proves control of the mailbox exactly as the emailed sign-in link does. So the
# invitee with no account signs in and joins with one click on the join page, instead of asking for a
# second mail. Three rules keep it one credential, used once, by the right person:
#
#   * only a mailed invite gets ``signin_until`` (``mark_invite_signin``). An invite whose link was
#     handed back to the inviter — captured mail, no mail, an internal address — never does, so an
#     inviter can never sign in as the person they invited;
#   * the window is ``SIGNIN_TTL_SEC`` (and never past the invite's own expiry);
#   * ``claim_invite_signin`` spends it ONCE, under the workspace lock, on the POST behind the
#     join page's button — never on a GET, so a mail scanner that opens the link changes nothing.
SIGNIN_TTL_SEC = 7 * 86400
SIGNIN_TTL_MAX_SEC = 30 * 86400


def signin_eligible(rec: dict, *, now: Optional[float] = None) -> bool:
    """May this invite's link sign its one address in, now? Read-only."""
    t = now if now is not None else time.time()
    emails = [e for e in (rec.get("allowed_emails") or []) if str(e).strip()]
    return bool(rec.get("mode") == "restricted" and len(emails) == 1
                and float(rec.get("signin_until") or 0) > t
                and not rec.get("signin_claimed") and not rec.get("revoked")
                and int(rec.get("expires_at", 0)) >= t
                and int(rec.get("uses", 0)) < int(rec.get("max_uses", 1)))


def mark_invite_signin(root: Path, workspace_id: str, invite_id: str, *,
                       ttl_sec: Optional[int] = None, now: Optional[float] = None) -> bool:
    """Let a MAILED restricted single-address invite sign its address in for ``ttl_sec``. Returns
    whether the mark was made (an invite that is not restricted to exactly one address is left
    alone)."""
    t = now if now is not None else time.time()
    ttl = max(1, min(int(ttl_sec if ttl_sec is not None else SIGNIN_TTL_SEC), SIGNIN_TTL_MAX_SEC))
    with _ws_lock(workspace_id):
        invites = read_invites(root, workspace_id)
        rec = next((i for i in invites if i.get("id") == invite_id), None)
        if rec is None or rec.get("mode") != "restricted" or len(rec.get("allowed_emails") or []) != 1:
            return False
        rec["signin_until"] = int(min(t + ttl, int(rec.get("expires_at", t + ttl))))
        _write_invites(root, workspace_id, invites)
    return True


def claim_invite_signin(root: Path, token: str, *, now: Optional[float] = None) -> dict:
    """Spend the sign-in half of ``token`` ONCE. Returns ``{email, workspace_id, room, role}`` for
    the one address it may sign in, or ``MembershipError`` (404 unknown, 410 used or out of its
    window, 409 not a sign-in link). The membership itself is granted by the ordinary accept, as the
    signed-in person, right after."""
    t = now if now is not None else time.time()
    found = find_invite(Path(root), token)
    if found is None:
        raise MembershipError("invalid invite", status=404)
    slug = found[0]
    h = hash_token(token)
    with _ws_lock(slug):
        invites = read_invites(root, slug)
        rec = next((i for i in invites if i.get("hash") == h), None)
        if rec is None:
            raise MembershipError("invalid invite", status=404)
        if rec.get("signin_claimed"):
            raise MembershipError("this link has already been used to sign in", status=410)
        if not rec.get("signin_until"):
            raise MembershipError("this invite link does not sign anybody in", status=409)
        if not signin_eligible(rec, now=t):
            raise MembershipError("this link can no longer sign you in", status=410)
        rec["signin_claimed"] = int(t)
        _write_invites(root, slug, invites)
        email = _normalize_email((rec.get("allowed_emails") or [""])[0])
        return {"email": email, "workspace_id": slug, "role": rec.get("role"),
                "room": dict(rec["room"]) if isinstance(rec.get("room"), dict) else None}


def invite_by_id(root: Path, workspace_id: str, invite_id: str) -> Optional[dict]:
    """The invite record ``invite_id`` of ``workspace_id`` (hash included — internal use only)."""
    return next((i for i in read_invites(root, workspace_id) if i.get("id") == invite_id), None)


def accept_invite(root: Path, workspace_id: str, *, token: str = "", subject: str,
                  index: MembershipIndex, subject_email: Optional[str] = None,
                  commit_fn: Optional[CommitFn] = None, now: Optional[float] = None,
                  invite_id: str = "") -> dict:
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
    assert_shareable(root, workspace_id)
    t = now if now is not None else time.time()
    # BY TOKEN (the link) or BY ID (the invitee's own notification, PRD 0.13.4 M2: the row stores
    # the invite's id, never the token, and the act resolves it here — still bound to the verified
    # address below, so an id grants nothing a token would not).
    h = hash_token(token) if token else ""
    if not h and not invite_id:
        raise MembershipError("invalid invite", status=404)
    with _ws_lock(workspace_id):
        # Fresh read UNDER the lock: the authoritative uses counter, not a value read before we waited.
        invites = read_invites(root, workspace_id)
        rec = next((i for i in invites if (i.get("hash") == h if h else i.get("id") == invite_id)), None)
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
        # THE SENDER'S RANK, NOW (D5): an invite never grants more than its sender holds TODAY — an
        # owner invite a contributor minted before the caps, or one whose sender has since been
        # demoted or removed, is refused. (``owner_act=True`` here: the rank check alone decides, so
        # an owner's own owner invite still redeems while they are an owner.)
        try:
            invite_role_allowed(root, workspace_id, str(rec.get("created_by") or ""), role, owner_act=True)
        except MembershipError as exc:
            if already:
                return {"workspace_id": workspace_id, "role": is_member(root, workspace_id, subject),
                        "already_member": True}
            raise MembershipError(f"this invite can no longer be used: {exc}", status=410) from None
        grant_membership(root, workspace_id, subject, role, added_by=rec.get("created_by", "invite"),
                         index=index, email=subject_email, commit_fn=commit_fn)
        if not already:
            # Consume a use only for a NEW membership — re-accept is a no-op on the counter (idempotent).
            # This write + commit lands the increment durably before the lock is released, so the next
            # waiter re-reads the bumped counter and a max_uses=1 invite refuses the second grant.
            rec["uses"] = int(rec.get("uses", 0)) + 1
            _write_invites(root, workspace_id, invites)
        out = {"workspace_id": workspace_id, "role": role, "already_member": already}
        rr = rec.get("room")
        if isinstance(rr, str) and rr:
            rr = {"id": rr, "title": ""}
        if isinstance(rr, dict) and rr.get("id"):
            out["room"] = dict(rr)
            out["invited_by"] = str(rec.get("created_by") or "")
        return out


def revoke_invite(root: Path, workspace_id: str, invite_id: str, *,
                  commit_fn: Optional[CommitFn] = None) -> None:
    """Revoke an invite by id (sets ``revoked``); a revoked invite fails ``accept`` with 410."""
    assert_shareable(root, workspace_id)
    with _ws_lock(workspace_id):
        invites = read_invites(root, workspace_id)
        rec = next((i for i in invites if i.get("id") == invite_id), None)
        if rec is None:
            raise MembershipError("unknown invite", status=404)
        if not rec.get("revoked"):
            rec["revoked"] = True
            _write_invites(root, workspace_id, invites)


def list_invites(root: Path, workspace_id: str) -> list[dict]:
    """The workspace's invites WITHOUT the hash (never surface the stored capability material)."""
    return [{k: v for k, v in i.items() if k != "hash"} for i in read_invites(root, workspace_id)]


def voided_invite_ids(root: Path, workspace_id: str, *, now: Optional[float] = None) -> list[str]:
    """Invite ids this workspace's own history says were minted, that the store no longer holds and
    that would still be live — the links a fresh mint has to warn about (Vexa-ai/vexa#1645 point 4).

    POSITIVE EVIDENCE, from the record that survived the loss. Every mint used to leave
    ``policy: mint invite <id> (<role>) for <ws>`` in the workspace's git log, and those commits are
    still there after the invite file they described was deleted — it is how the founder's report was
    diagnosed at all. An id in that log with no record in the store is a link somebody is holding that
    can never be redeemed.

    Bounded to the invite lifetime so it is self-limiting and truthful: a link minted more than
    ``DEFAULT_EXPIRES_IN_SEC`` ago is expired on its own terms, and saying it is void adds nothing.
    New mints leave no such commit, so this never counts an invite that is currently working."""
    import subprocess
    from shared.gitexec import run_git

    ws = _ws_dir(root, workspace_id)
    if not (ws / ".git").exists():
        return []
    t = now if now is not None else time.time()
    try:
        out = run_git(ws, "log", "--format=%ct%x09%s", "--grep=^policy: mint invite ", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    held = {str(i.get("id")) for i in read_invites(root, workspace_id)}
    lost: list[str] = []
    for line in out.stdout.splitlines():
        stamp, _, subject = line.partition("\t")
        parts = subject.split()
        if len(parts) < 4:
            continue
        invite_id = parts[3]
        try:
            when = int(stamp)
        except ValueError:
            continue
        if t - when > DEFAULT_EXPIRES_IN_SEC:
            continue
        if invite_id not in held and invite_id not in lost:
            lost.append(invite_id)
    return lost


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
