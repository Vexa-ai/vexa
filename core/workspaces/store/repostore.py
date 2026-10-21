"""repostore.py — workspace git repositories held durably in an S3 bucket, with a disposable cache.

Representation (design: ``~/dev/biz/drafts/briefs-0133/ws-s3-design.md``), under the store prefix:

    repos/<rel>/@/gen/<000000000042>.json   immutable manifest of one generation
    repos/<rel>/@/bundles/<sha256>.bundle   immutable, content-addressed git bundle
    files/<rel>                             a platform file at the store root (attach state, .policy …)

``<rel>`` is the repository's path under the store root (``17``, ``_global``, ``.attached/17/deal``);
``@`` cannot occur in a workspace name, so no repository's keys can shadow another's.

THE GUARANTEE. :meth:`RepoStore.publish` returns only after the new generation object has been PUT
and read back; nothing calls a write done before that. The cache (a work tree per repository under
``cache_root``) is rebuilt from the bucket by :meth:`hydrate` whenever it is missing or behind.

THE GUARD — no conditional PUT needed, so it holds on Linode Object Storage and every enterprise S3.
A publish holds the workspace's Redis lock (``lock.WorkspaceLocks``) and its monotonically increasing
fencing token; refuses unless the cache is based on the current generation N (:class:`StaleBase`);
re-checks the lock; re-reads the current generation and refuses if it moved or carries a newer token
(a stalled writer that outlived its lock TTL); then PUTs ``gen/<N+1>-<token>.json`` — a key of its own,
never overwriting anyone — and acknowledges only if that object is ALONE at N+1 afterwards. The
current generation, for every reader, is the highest one and, within it, the lowest token
(:func:`current_key`), which is always the acknowledged object when there is one.

Every git command runs through ``gitexec`` (no hooks, no helpers). Cache paths are reached through
``workspace_paths``; bundles are staged in this process's own temp directory.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

from workspaces.shared import gitexec
from workspaces.shared import workspace_paths as wpaths

from .lock import Held, LockLost, WorkspaceLocks
from .objects import ObjectStore, StoreUnavailable

logger = logging.getLogger("workspaces.store")

MARKER = ".git/vexa-store.json"
#: Present only in an evicted tree's ``.git``: one ``lstat`` tells the access hook to hydrate.
STUB_FLAG = ".git/vexa-store-stub"
GEN_DIGITS = 12
#: Paths a pushed commit range may not touch: platform-owned (S1), the agent's runtime plumbing
#: (``.claude``, never history) or git's own. Compared casefolded: on a case-insensitive filesystem
#: ``Policy/members.json`` is the roster file.
RESERVED_PUSH_PREFIXES = ("policy/", ".vexa/", ".claude/", ".git/", ".gitmodules")
_NO_REPO = {"lost+found", ".wsstore"}


class StaleBase(RuntimeError):
    """The cache is not based on the bucket's current generation: another writer got there first.
    The write is refused and the cache entry is to be refreshed."""


class ReservedPath(PermissionError):
    """A pushed commit range touches a platform-owned path (``policy/``, ``.vexa/``, ``.claude/``,
    ``.git``), in any case."""


class NotFastForward(RuntimeError):
    """A pushed range does not descend from the base it claims."""


@dataclass
class Generation:
    gen: int
    refs: dict
    head: str
    bundles: list
    writer: str = ""
    fence: int = 0
    parent: int = 0
    at: float = 0.0
    paths: list = field(default_factory=list)   # HEAD's files: what an evicted tree's stub shows

    @property
    def tips(self) -> list[str]:
        return sorted(set(self.refs.values()))

    def to_bytes(self) -> bytes:
        return json.dumps({"gen": self.gen, "refs": self.refs, "head": self.head,
                           "bundles": self.bundles, "writer": self.writer, "fence": self.fence,
                           "parent": self.parent, "at": self.at, "paths": self.paths},
                          sort_keys=True).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Generation":
        d = json.loads(raw)
        return cls(gen=int(d["gen"]), refs=dict(d.get("refs") or {}), head=d.get("head") or "",
                   bundles=list(d.get("bundles") or []), writer=d.get("writer") or "",
                   fence=int(d.get("fence") or 0), parent=int(d.get("parent") or 0),
                   at=float(d.get("at") or 0.0), paths=list(d.get("paths") or []))


def repo_prefix(rel: str) -> str:
    rel = rel.strip("/")
    if not rel or "@" in rel or ".." in rel.split("/"):
        raise ValueError(f"not a store-relative repository path: {rel!r}")
    return f"repos/{rel}/@/"


def gen_key(rel: str, gen: int, fence: int = 0) -> str:
    """``gen/<gen>-<fence>.json``: one object per (generation, writer's fencing token), never
    overwritten — two writers racing for the same generation leave two objects, not one."""
    return f"{repo_prefix(rel)}gen/{gen:0{GEN_DIGITS}d}-{fence:0{GEN_DIGITS + 2}d}.json"


def parse_gen_key(key: str) -> Optional[tuple[int, int]]:
    name = key.rsplit("/", 1)[-1]
    if not name.endswith(".json"):
        return None
    stem = name[:-5]
    gen, _, fence = stem.partition("-")
    try:
        return int(gen), int(fence or 0)
    except ValueError:
        return None


def current_key(keys: list) -> Optional[str]:
    """THE ELECTION RULE every reader applies: the highest generation; within it, the lowest
    fencing token. A writer acknowledges only when its object was alone at its generation after its
    PUT, so whenever an acknowledged object exists it is the one this rule picks."""
    parsed = [(parse_gen_key(k), k) for k in keys]
    parsed = [(g, f, k) for (gf, k) in parsed if gf for g, f in [gf]]
    if not parsed:
        return None
    top = max(g for g, _, _ in parsed)
    return min((f, k) for g, f, k in parsed if g == top)[1]


def _git(repo, *args, check=True, **kw):
    return gitexec.run_git(str(repo), *args, check=check, **kw)


def local_refs(repo) -> tuple[dict, str]:
    """``({ref: sha}, head)`` for the branches and tags of ``repo``; head is the symbolic HEAD."""
    out = _git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags").stdout
    refs = dict(line.split(" ", 1) for line in out.splitlines() if " " in line)
    head = _git(repo, "symbolic-ref", "-q", "HEAD", check=False).stdout.strip()
    return refs, head


def _is_ancestor(repo, a: str, b: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", a, b, check=False).returncode == 0


@dataclass
class RepoStore:
    """The bucket side and the cache side of every workspace repository.

    ``objects`` is the bucket (``objects.S3Objects`` / ``MemoryObjects``), ``locks`` the per-workspace
    lock, ``cache_root`` the directory whose subtrees are the cached work trees (agent-api's
    ``VEXA_WORKSPACES_DIR`` with ``backend: s3``). ``conditional``: whether the store honours
    ``If-None-Match`` (boot self-test) — reported on /health, not relied on."""

    objects: ObjectStore
    locks: WorkspaceLocks
    cache_root: Path
    conditional: bool = False
    compact_after: int = 32
    keep_gens: int = 8
    markers: bool = True              # False: never write into the cached .git (the migration's source)
    last_error: str = ""
    _paused: threading.local = field(default_factory=threading.local, repr=False)

    # ── reading the bucket ───────────────────────────────────────────────────────────────────
    def latest(self, rel: str) -> Optional[Generation]:
        key = current_key(self.objects.list(f"{repo_prefix(rel)}gen/"))
        if key is None:
            return None
        raw = self.objects.get(key)
        if raw is None:
            raise StoreUnavailable(f"generation {key} listed but not readable")
        return Generation.from_bytes(raw)

    def repos(self) -> list[str]:
        """Every repository the bucket holds (its store-relative path)."""
        out = set()
        for k in self.objects.list("repos/"):
            rel, sep, _ = k[len("repos/"):].partition("/@/")
            if sep:
                out.add(rel)
        return sorted(out)

    # ── the cache ────────────────────────────────────────────────────────────────────────────
    def path(self, rel: str) -> Path:
        return Path(self.cache_root) / rel

    def marker(self, rel: str, *, at: Optional[Path] = None) -> Optional[dict]:
        where = at if at is not None else self.path(rel)
        with self.paused():
            raw = wpaths.read_text_inside(where, MARKER, allow=(".git",)) \
                if wpaths.is_dir_inside(where, ".git", allow=(".git",)) else None
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def _write_marker(self, rel: str, gen: Generation, *, at: Optional[Path] = None) -> None:
        if not self.markers:
            return
        with self.paused():
            wpaths.write_text_inside(at if at is not None else self.path(rel), MARKER,
                                     json.dumps({"rel": rel, "gen": gen.gen, "fence": gen.fence,
                                                 "refs": gen.refs}),
                                     allow=(".git",))

    @contextmanager
    def paused(self) -> Iterator[None]:
        """Git commands run inside are the store's own (hydrate, rollback): the observer ignores them."""
        prev = getattr(self._paused, "on", False)
        self._paused.on = True
        try:
            yield
        finally:
            self._paused.on = prev

    def is_paused(self) -> bool:
        return getattr(self._paused, "on", False)

    def hydrate(self, rel: str, *, gen: Optional[Generation] = None,
                keep_edits: bool = False, dest: Optional[Path] = None) -> Optional[Generation]:
        """Make the cached work tree of ``rel`` equal to the bucket's current generation: cold (no
        tree) builds it from the bundle chain, warm fetches only the bundles it lacks. Returns the
        generation, or None when the bucket has no such repository.

        Commits the cache holds beyond the generation were never acknowledged and are dropped. With
        ``keep_edits`` (a refresh right before a write) uncommitted edits survive a fast-forward,
        and an edit that would collide with the newer generation refuses (:class:`StaleBase`)."""
        gen = gen or self.latest(rel)
        if gen is None:
            return None
        repo = Path(dest) if dest is not None else self.path(rel)
        with self.paused():
            cold = not wpaths.is_dir_inside(repo, ".git", allow=(".git",))
        mark = None if cold else self.marker(rel, at=repo)
        if mark and mark.get("stub"):
            return self._hydrate_stub(rel, repo, gen)
        same = bool(mark and mark.get("rel") == rel)
        if same and int(mark.get("gen", 0)) == gen.gen and local_refs(repo)[0] == gen.refs:
            return gen
        target = gen.refs.get(gen.head, "")
        with self.paused():
            if cold:
                os.makedirs(repo, exist_ok=True)  # the store's own cache folder
                _git(repo, "init", "-q")
            have = set(self._bundles_of(rel, int(mark.get("gen", 0)), int(mark.get("fence", 0)))) \
                if same else set()
            for key in gen.bundles:
                if key not in have:
                    self._fetch_bundle(repo, key)
            local, _ = local_refs(repo)
            current = local.get(gen.head, "")
            durable_before = (mark.get("refs") or {}).get(gen.head, "") if same else ""
            merged = False
            if keep_edits and target and current and current == durable_before:
                if not _is_ancestor(repo, current, target):
                    raise StaleBase(rel)          # rewritten elsewhere; the edit is on dead history
                if _git(repo, "merge", "-q", "--ff-only", target, check=False).returncode != 0:
                    raise StaleBase(rel)          # an uncommitted edit collides with the newer state
                merged = True
                local, _ = local_refs(repo)
            for ref, sha in gen.refs.items():
                if local.get(ref) != sha:
                    _git(repo, "update-ref", ref, sha)
            for ref in set(local) - set(gen.refs):
                _git(repo, "update-ref", "-d", ref)
            if gen.head:
                _git(repo, "symbolic-ref", "HEAD", gen.head)
            if target and not merged:
                _git(repo, "reset", "-q", "--hard", target)
            self._write_marker(rel, gen, at=repo)
        return gen

    def _hydrate_stub(self, rel: str, stub: Path, gen: Generation) -> Generation:
        """An evicted tree comes back: built cold in a sibling folder, then swapped in by rename, so
        a reader never sees a half-built tree (one that already opened the stub keeps reading it)."""
        tmp = stub.parent / f".{stub.name}.hydrating-{uuid.uuid4().hex[:8]}"
        try:
            self.hydrate(rel, gen=gen, dest=tmp)
            _swap_in(tmp, stub)
        finally:
            with self.paused():
                wpaths.remove_tree(tmp, ignore_errors=True)
        return gen

    def make_stub(self, rel: str, gen: Generation, *, at: Optional[Path] = None) -> Path:
        """The evicted (or never-loaded) form of ``rel``: its folders, an empty placeholder for every
        file of HEAD, and a ``.git`` holding only the marker. Existence checks answer as for the real
        tree; the first read through ``workspace_paths`` or ``gitexec`` hydrates it."""
        where = Path(at) if at is not None else self.path(rel)
        os.makedirs(where / ".git", exist_ok=True)
        with self.paused():
            for p in gen.paths:
                try:
                    wpaths.write_new_inside(where, p, b"", allow=(".vexa", ".claude"))
                except (wpaths.PathRefused, OSError):
                    continue
            wpaths.write_text_inside(where, STUB_FLAG, "", allow=(".git",))
            wpaths.write_text_inside(where, MARKER, json.dumps(
                {"rel": rel, "gen": gen.gen, "fence": gen.fence, "refs": gen.refs, "stub": True}),
                allow=(".git",))
        return where

    def _bundles_of(self, rel: str, gen: int, fence: int = 0) -> list:
        raw = self.objects.get(gen_key(rel, gen, fence)) if gen else None
        return Generation.from_bytes(raw).bundles if raw else []

    def _fetch_bundle(self, repo: Path, key: str) -> None:
        data = self.objects.get(key)
        if data is None:
            raise StoreUnavailable(f"bundle {key} named by a generation is missing")
        if hashlib.sha256(data).hexdigest() not in key:
            raise StoreUnavailable(f"bundle {key} does not match its content address")
        with _temp_file(data, ".bundle") as path:
            _git(repo, "fetch", "-q", "--no-tags", "--no-write-fetch-head",
                 path, "+refs/*:refs/vexa-store/fetched/*", env={"GIT_ALLOW_PROTOCOL": "file"})
        # the temporary refs are not state; drop them so no later bundle carries them
        out = _git(repo, "for-each-ref", "--format=%(refname)", "refs/vexa-store/").stdout.split()
        for ref in out:
            _git(repo, "update-ref", "-d", ref)

    def ensure_fresh(self, rel: str) -> Optional[Generation]:
        """Before a write: the cache must be at the bucket's current generation. Behind and clean →
        refreshed; behind with local changes → :class:`StaleBase`. A repository the bucket does not
        know yet is fine (its first publish creates it)."""
        cur = self.latest(rel)
        if cur is None:
            return None
        if not wpaths.is_dir_inside(self.path(rel), ".git", allow=(".git",)):
            return self.hydrate(rel, gen=cur)          # not cached (cold pod, evicted): fetch it
        mark = self.marker(rel)
        if mark is None or mark.get("rel") != rel:
            return cur                                  # a new or moved tree: its publish replaces
        if int(mark.get("gen", 0)) == cur.gen:
            return cur
        refs, _ = local_refs(self.path(rel))
        if refs != (mark.get("refs") or {}):
            raise StaleBase(rel)                        # behind AND holding unpublished commits
        return self.hydrate(rel, gen=cur, keep_edits=True)

    # ── writing the bucket ───────────────────────────────────────────────────────────────────
    def publish(self, rel: str, *, held: Held, replace: bool = False) -> Optional[Generation]:
        """Make the cached repository's current refs durable as generation N+1, and return only
        when they are. ``replace``: the tree is new at ``rel`` (a clone, a move, a migration) and
        supersedes whatever the bucket held there."""
        if held.path != rel:
            raise LockLost(f"publish of {rel} under the lock of {held.path}")
        repo = self.path(rel)
        refs, head = local_refs(repo)
        cur = self.latest(rel)
        mark = self.marker(rel)
        if cur is not None and not replace:
            based = mark is not None and mark.get("rel") == rel and int(mark.get("gen", -1)) == cur.gen
            if mark is None or mark.get("rel") != rel:
                replace = True  # a tree that came from elsewhere (moved in) replaces the bucket's
            elif not based:
                raise StaleBase(rel)
        if cur is not None and cur.refs == refs and cur.head == head:
            if not mark or int(mark.get("gen", -1)) != cur.gen or mark.get("rel") != rel:
                self._write_marker(rel, cur)
            return cur
        if not refs:
            return cur
        incremental = (cur is not None and not replace and cur.bundles
                       and len(cur.bundles) < self.compact_after
                       and all(any(_is_ancestor(repo, old, new) for new in refs.values()) for old in cur.tips))
        bundles: list = list(cur.bundles) if incremental else []
        data = _bundle(repo, sorted(refs), exclude=cur.tips if incremental else [])
        if data is not None:
            key = f"{repo_prefix(rel)}bundles/{hashlib.sha256(data).hexdigest()}.bundle"
            self.objects.put(key, data)
            if key not in bundles:
                bundles.append(key)
        elif not incremental:
            raise StoreUnavailable(f"{rel}: git produced no bundle for a non-empty repository")
        paths = _git(repo, "ls-tree", "-r", "-z", "--name-only", refs[head]).stdout.split("\0") \
            if refs.get(head) else []
        nxt = Generation(gen=(cur.gen + 1) if cur else 1, refs=refs, head=head, bundles=bundles,
                         writer=uuid.uuid4().hex, fence=held.fence, parent=cur.gen if cur else 0,
                         at=time.time(), paths=[p for p in paths if p])
        held.check()                      # still ours, right before the commit point
        # FENCING (no conditional PUT needed): re-read the current generation. Anything newer than
        # our base, or carrying a newer fencing token than ours, means a writer that acquired the
        # lock after us has published — we stalled past the TTL. Refuse; nothing is acknowledged.
        now = self.latest(rel)
        if now is not None and ((cur is None or now.gen != cur.gen) or now.fence > held.fence):
            if now.fence > held.fence:
                self.locks.advance_fence(rel, now.fence)   # a reset counter heals for the retry
            raise LockLost(f"{rel}: generation {now.gen} (fence {now.fence}) is newer than this "
                           f"writer's base (fence {held.fence})")
        key = gen_key(rel, nxt.gen, held.fence)
        self.objects.put(key, nxt.to_bytes())
        # THE ELECTION: acknowledge only when ours is the ONLY object at this generation. A racing
        # writer that also got here sees ours and refuses; whichever saw the other first loses, and
        # at most one of two can ever see itself alone (LIST is strongly consistent after PUT).
        rivals = [k for k in self.objects.list(f"{repo_prefix(rel)}gen/")
                  if (parse_gen_key(k) or (0, 0))[0] == nxt.gen and k != key]
        if rivals:
            try:
                self.objects.delete(key)
            except StoreUnavailable:
                pass
            raise LockLost(f"{rel}: another writer published generation {nxt.gen} at the same time")
        back = self.objects.get(key)
        if back is None or Generation.from_bytes(back).writer != nxt.writer:
            raise LockLost(f"{rel}: generation {nxt.gen} was not read back as written")
        self._write_marker(rel, nxt)
        if cur is not None and not incremental:
            self._collect(rel, keep=set(bundles), rewrite=replace)
        self._trim_gens(rel, nxt.gen)
        return nxt

    def _collect(self, rel: str, *, keep: set, rewrite: bool) -> None:
        """Delete bundles no current generation names (after a compaction or a rewrite). The only
        reader of the bucket is agent-api itself, under the same lock, so nothing is mid-read."""
        for k in self.objects.list(f"{repo_prefix(rel)}bundles/"):
            if k not in keep:
                try:
                    self.objects.delete(k)
                except StoreUnavailable:
                    logger.warning("workspace store: superseded bundle %s not deleted yet", k)

    def _trim_gens(self, rel: str, newest: int) -> None:
        for k in self.objects.list(f"{repo_prefix(rel)}gen/"):
            gf = parse_gen_key(k)
            if gf is None:
                continue
            n = gf[0]
            if n <= newest - self.keep_gens:
                try:
                    self.objects.delete(k)
                except StoreUnavailable:
                    return

    def delete_repo(self, rel: str) -> None:
        """The workspace is gone: every object of it leaves the bucket (versioning, when on, keeps
        noncurrent versions until the lifecycle rule expires them)."""
        for k in self.objects.list(repo_prefix(rel)):
            self.objects.delete(k)

    def rollback(self, rel: str) -> None:
        """A write that could not be made durable is not kept. The refs go back to the last
        published generation, and exactly the files that write changed (committed or staged) go
        back to their durable content; other uncommitted edits in the tree are left alone."""
        mark = self.marker(rel)
        if not mark or mark.get("rel") != rel:
            return
        refs = mark.get("refs") or {}
        repo = self.path(rel)
        with self.paused():
            try:
                local, head = local_refs(repo)
                durable = refs.get(head, "")
                paths = set()
                if durable and local.get(head) and local.get(head) != durable:
                    paths |= set(_git(repo, "diff", "--name-only", "--no-renames", durable,
                                      local[head]).stdout.splitlines())
                paths |= set(_git(repo, "diff", "--cached", "--name-only", "--no-renames").stdout.splitlines())
                for ref in set(local) - set(refs):
                    _git(repo, "update-ref", "-d", ref, check=False)
                for ref, sha in refs.items():
                    _git(repo, "update-ref", ref, sha, check=False)
                paths = sorted(p for p in paths if p.strip())
                if paths and durable:
                    _git(repo, "reset", "-q", durable, "--", *paths, check=False)
                    known = set(_git(repo, "ls-tree", "-r", "--name-only", durable, "--", *paths).stdout.splitlines())
                    if known:
                        _git(repo, "checkout", durable, "--", *sorted(known), check=False)
                    for p in set(paths) - known:
                        wpaths.unlink_inside(repo, p)
            except Exception:  # noqa: BLE001 — the tree is evicted when it cannot be put back
                logger.exception("workspace store: rollback of %s failed; evicting it", rel)
                wpaths.remove_tree(repo, ignore_errors=True)

    # ── worker push / fetch, served by agent-api ─────────────────────────────────────────────
    def bundle_for(self, rel: str, *, have: str = "") -> Optional[bytes]:
        """A bundle of ``rel``'s cached repository for a worker: everything, or only what descends
        from ``have`` when that is an ancestor of HEAD. None when there is nothing new."""
        repo = self.path(rel)
        refs, head = local_refs(repo)
        if not refs:
            return None
        if have and refs.get(head) == have:
            return None
        exclude = [have] if have and all(_is_ancestor(repo, have, t) for t in [refs.get(head, "")]) else []
        return _bundle(repo, sorted(refs) + (["HEAD"] if head else []), exclude=exclude)

    def accept_push(self, rel: str, data: bytes, *, base: str, held: Held,
                    admit: Optional[Callable[[Path, str, str], None]] = None) -> Generation:
        """Land a worker's commits on ``rel``: fast-forward from ``base`` only, no reserved path
        touched by any commit in the range, ``admit(repo, base, new)`` — the caller's own rule over
        the range — raising nothing, durable before returning."""
        repo = self.path(rel)
        refs, head = local_refs(repo)
        if not head or refs.get(head) != base:
            raise StaleBase(rel)
        with _temp_file(data, ".bundle") as path, self.paused():
            ok = _git(repo, "bundle", "verify", "-q", path, check=False, env={"GIT_ALLOW_PROTOCOL": "file"})
            if ok.returncode != 0:
                raise NotFastForward(f"{rel}: the pushed bundle does not apply here ({ok.stderr.strip()[:200]})")
            _git(repo, "-c", "transfer.fsckObjects=true", "fetch", "-q", "--no-tags",
                 "--no-write-fetch-head", path, f"+{head}:refs/vexa-store/incoming",
                 env={"GIT_ALLOW_PROTOCOL": "file"})
        new = _git(repo, "rev-parse", "refs/vexa-store/incoming").stdout.strip()
        try:
            if new == base:
                return self.latest(rel) or self.publish(rel, held=held)
            if not _is_ancestor(repo, base, new):
                raise NotFastForward(rel)
            touched = _git(repo, "log", "-m", "--format=", "--name-only", "-z", "--no-renames",
                           f"{base}..{new}").stdout
            bad = sorted({p for p in touched.split("\0") if p.strip() and
                          any(p.casefold() == r.rstrip("/") or p.casefold().startswith(r)
                              for r in RESERVED_PUSH_PREFIXES)})
            if bad:
                raise ReservedPath(f"{rel}: a pushed commit touches platform-owned paths: {', '.join(bad[:5])}")
            if admit is not None:
                admit(repo, base, new)
            with self.paused():
                _git(repo, "merge", "-q", "--ff-only", new)
            try:
                gen = self.publish(rel, held=held)
            except Exception:
                self.rollback(rel)
                raise
            return gen
        finally:
            with self.paused():
                _git(repo, "update-ref", "-d", "refs/vexa-store/incoming", check=False)

    # ── platform files at the store root ─────────────────────────────────────────────────────
    def put_file(self, rel: str, data: bytes) -> None:
        self.objects.put(f"files/{rel}", data)

    def delete_file(self, rel: str) -> None:
        self.objects.delete(f"files/{rel}")

    def files(self) -> list[str]:
        return [k[len("files/"):] for k in self.objects.list("files/")]

    def hydrate_files(self) -> int:
        n = 0
        with self.paused():
            for rel in self.files():
                data = self.objects.get(f"files/{rel}")
                if data is None:
                    continue
                wpaths.write_bytes_inside(self.cache_root, rel, data, mode=0o600)
                n += 1
        return n

    def hydrate_all(self, *, lazy: bool = False, eager: tuple = ()) -> dict:
        """A cold pod, from the bucket: every platform file, and every repository — as a real tree,
        or with ``lazy`` as a stub hydrated on first use (``eager`` ones always real)."""
        repos = self.repos()
        stubs = 0
        for rel in repos:
            if lazy and rel not in eager:
                gen = self.latest(rel)
                if gen is None:
                    continue
                mark = self.marker(rel) if self.path(rel).exists() else None
                if mark and mark.get("rel") == rel and int(mark.get("gen", 0)) == gen.gen:
                    continue                      # a warm cache (container restart) already matches
                if mark and not mark.get("stub"):
                    self.hydrate(rel, gen=gen)     # a real tree that is behind: refresh it
                    continue
                with self.paused():
                    wpaths.remove_tree(self.path(rel), ignore_errors=True)
                self.make_stub(rel, gen)
                stubs += 1
            else:
                self.hydrate(rel)
        files = self.hydrate_files()
        # Whatever the cache holds that the bucket does not was never acknowledged (a crash between
        # a local write and its publish, a workspace deleted meanwhile): it goes.
        cached_repos, cached_files = scan_cache(self.cache_root, self)
        bucket_files = set(self.files())
        pruned = 0
        with self.paused():
            for rel in cached_repos:
                if rel not in repos:
                    wpaths.remove_tree(self.path(rel), ignore_errors=True)
                    pruned += 1
            for rel in cached_files:
                if rel not in bucket_files:
                    wpaths.unlink_inside(self.cache_root, rel)
                    pruned += 1
        return {"repos": len(repos), "stubs": stubs, "files": files, "pruned": pruned}


def _transient_name(name: str) -> bool:
    return (name in _NO_REPO or name.startswith(".staging") or name.endswith(".clone")
            or ".hydrating-" in name or ".trash-" in name)


def scan_cache(root, store: "Optional[RepoStore]" = None) -> tuple[list[str], list[str]]:
    """``(repositories, platform files)`` under ``root``: a folder holding ``.git`` is a repository
    (an evicted stub included) and is not descended into; every other regular file is a platform
    file. Temp files mid-rename (``*.tmp``) and the store's own scratch are skipped. With ``store``
    the walk is the store's own (the access hook does not hydrate what it passes)."""
    repos: list[str] = []

    def skip(rel: str) -> bool:
        name = rel.rsplit("/", 1)[-1]
        if _transient_name(name):
            return True
        if wpaths.is_dir_inside(root, f"{rel}/.git", allow=(".git",)):
            repos.append(rel)
            return True
        return False

    ctx = store.paused() if store is not None else contextlib.nullcontext()
    with ctx:
        files = [f for f in wpaths.walk_files_inside(root, skip_dir=skip) if not f.endswith(".tmp")]
    return sorted(repos), sorted(files)


def _bundle(repo: Path, refs: list, *, exclude: list) -> Optional[bytes]:
    """``git bundle create`` of ``refs`` minus ``exclude``; None when it would be empty."""
    fd, path = tempfile.mkstemp(suffix=".bundle")
    os.close(fd)
    try:
        args = ["bundle", "create", "-q", path, *refs, *[f"^{x}" for x in exclude if x]]
        proc = _git(repo, *args, check=False)
        if proc.returncode != 0:
            if "empty bundle" in (proc.stderr or ""):
                return None
            raise StoreUnavailable(f"git bundle create failed: {(proc.stderr or '').strip()[:300]}")
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


def _swap_in(new: Path, old: Path) -> None:
    """Replace the folder ``old`` with ``new`` by two renames (the gap is one rename long); the
    displaced tree is removed afterwards. Both are the store's own cache folders."""
    trash = old.parent / f".{old.name}.trash-{uuid.uuid4().hex[:8]}"
    os.rename(old, trash)
    os.rename(new, old)
    wpaths.remove_tree(trash, ignore_errors=True)


@contextmanager
def _temp_file(data: bytes, suffix: str) -> Iterator[str]:
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        yield path
    finally:
        os.unlink(path)
