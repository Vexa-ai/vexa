"""observer.py — how agent-api's writes reach the bucket before they are acknowledged.

Two paths, one rule (nothing is acknowledged before it is durable):

* :class:`StoreObserver` is installed as ``gitexec``'s ref observer. Every git command that can move a
  ref takes the workspace's lock, checks the cache is at the bucket's current generation, runs, and
  publishes the new refs as the next generation before ``run_git`` returns. A publish that fails
  rolls the cached tree back to the last durable generation and raises
  :class:`WorkspaceStoreError`, which agent-api answers 503 (409 for a concurrent writer).
* :class:`CacheSync` covers what is not a git command: platform files at the store root (attach
  state, ``.policy/``, import status), and whole trees moved, made or removed as units
  (``shutil.move`` of a slot, ``remove_tree`` of a deleted workspace). agent-api runs it after every
  state-changing request, before the response leaves, and on a short timer for background writers.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from workspaces.shared import workspace_paths as wpaths

from .lock import LockBusy, LockLost, LockUnavailable
from .objects import StoreUnavailable
from .repostore import NotFastForward, RepoStore, ReservedPath, StaleBase, _transient_name, scan_cache

logger = logging.getLogger("workspaces.store")


class WorkspaceStoreError(RuntimeError):
    """A write that could not be made durable. ``status``: 503 (the bucket or the lock service is
    unreachable), 409 (another writer got there first), 403 (a reserved path), 400 (a bad push)."""

    def __init__(self, message: str, *, status: int, reason: str) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


# ── no store write after the response has begun ───────────────────────────────────────────────
#: Set by agent-api's innermost ASGI wrapper when ``http.response.start`` goes out. A write after
#: that point would be acknowledged (the 200 is already sent) before it was durable, so it raises.
RESPONSE_STARTED: "contextvars.ContextVar[bool]" = contextvars.ContextVar("wsstore_response_started",
                                                                         default=False)
_STREAM_ACKNOWLEDGED: "contextvars.ContextVar[bool]" = contextvars.ContextVar("wsstore_stream_ack",
                                                                             default=False)


@contextlib.contextmanager
def durable_in_stream():
    """The ONE sanctioned store write inside a streamed body: a write that completes — durable in
    the bucket — before the stream emits the event that announces it, so that event, not the
    response status, is its acknowledgement. Each use is listed with its reason in
    ``core/agent/tests/test_no_store_write_in_a_streamed_body.py``."""
    token = _STREAM_ACKNOWLEDGED.set(True)
    try:
        yield
    finally:
        _STREAM_ACKNOWLEDGED.reset(token)


def guard_response_not_started(what: str) -> None:
    if RESPONSE_STARTED.get() and not _STREAM_ACKNOWLEDGED.get():
        raise WorkspaceStoreError(
            f"A workspace write ({what}) was attempted after the response had begun; it could be "
            "acknowledged before it is durable, so it is refused.", status=500,
            reason="write_after_response_started")


class StreamGuard:
    """Innermost ASGI wrapper: marks the request's context once its response has begun."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        async def guarded_send(message):
            if message.get("type") == "http.response.start":
                RESPONSE_STARTED.set(True)
            await send(message)

        return await self.app(scope, receive, guarded_send)


def classify(exc: BaseException) -> WorkspaceStoreError:
    if isinstance(exc, WorkspaceStoreError):
        return exc
    if isinstance(exc, (StaleBase, LockBusy, LockLost)):
        return WorkspaceStoreError(
            "This workspace was changed by another writer at the same moment; nothing was saved. "
            "Try again.", status=409, reason="workspace_changed")
    if isinstance(exc, ReservedPath):
        return WorkspaceStoreError(str(exc), status=403, reason="reserved_path")
    if isinstance(exc, NotFastForward):
        return WorkspaceStoreError(str(exc), status=400, reason="not_fast_forward")
    return WorkspaceStoreError(
        "The workspace store is unreachable, so the change was not saved. It will work again once "
        f"the store is back ({type(exc).__name__}).", status=503, reason="workspace_store_unavailable")


_STORE_ERRORS = (StoreUnavailable, LockUnavailable, StaleBase, LockBusy, LockLost, ReservedPath,
                 NotFastForward, OSError)


def transient(rel: str) -> bool:
    """A tree mid-move (a staging slot, a clone about to be renamed, the store's own hydrate/trash
    folders): published once it lands, if ever."""
    return any(_transient_name(p) for p in rel.strip("/").split("/"))


class StoreObserver:
    """agent-api's observer with ``backend: s3``, on ``gitexec`` (``access`` before every command,
    ``before``/``after`` around a ref-moving one) and on ``workspace_paths`` (``access`` before a
    folder is opened, ``written``/``removed`` after a change)."""

    def __init__(self, store: RepoStore, *, cache=None, sync: "Optional[CacheSync]" = None) -> None:
        self.store = store
        self.cache = cache
        self.sync = sync
        self._root = os.path.realpath(str(store.cache_root))

    # ── first use (lazy hydration) ───────────────────────────────────────────────────────────
    def access(self, path: str) -> None:
        if self.cache is None or self.store.is_paused():
            return
        try:
            self.cache.access(path)
        except _STORE_ERRORS as exc:
            self.store.last_error = f"{type(exc).__name__}: {exc}"
            raise classify(exc) from exc

    # ── a file written or removed outside git: durable before the call returns ──────────────
    def written(self, path: str) -> None:
        if self.store.is_paused() or self.rel_of(path) is None:
            return
        try:
            guard_response_not_started(f"write {path}")
        except WorkspaceStoreError:
            if self.sync is not None:
                self.sync.revert_path(path)       # not acknowledged, so not kept
            raise
        if self.sync is not None:
            self.sync.path_written(path)

    def removed(self, path: str) -> None:
        if self.store.is_paused() or self.rel_of(path) is None:
            return
        guard_response_not_started(f"remove {path}")
        if self.sync is not None:
            self.sync.path_removed(path)

    def rel_of(self, path: str) -> Optional[str]:
        real = os.path.realpath(path)
        if not real.startswith(self._root.rstrip("/") + "/"):
            return None
        rel = real[len(self._root.rstrip("/")) + 1:]
        return None if not rel or transient(rel) else rel

    def before(self, root: str, sub: str, rest: tuple):
        if self.store.is_paused():
            return None
        replace = sub == "clone"
        target = os.path.join(root, [a for a in rest if not a.startswith("-")][-1]) if replace and rest else root
        rel = self.rel_of(target)
        if rel is None:
            return None
        guard_response_not_started(f"git {sub} in {rel}")
        held = self.store.locks.acquire(rel)
        try:
            if not replace:
                self.store.ensure_fresh(rel)
        except BaseException as exc:
            try:
                if isinstance(exc, _STORE_ERRORS):
                    self.store.last_error = f"{type(exc).__name__}: {exc}"
                    self.store.rollback(rel)     # the staged change of a refused write is not kept
                    raise classify(exc) from exc
                raise
            finally:
                self.store.locks.release(held)
        return rel, held, replace

    def after(self, token, ok: bool) -> None:
        if token is None:
            return
        rel, held, replace = token
        try:
            if ok:
                self.store.publish(rel, held=held, replace=replace)
                self.store.last_error = ""
        except BaseException as exc:
            self.store.last_error = f"{type(exc).__name__}: {exc}"
            logger.error("workspace store: %s not made durable (%s); rolled back", rel, exc)
            self.store.rollback(rel)
            if isinstance(exc, _STORE_ERRORS):
                raise classify(exc) from exc
            raise
        finally:
            self.store.locks.release(held)


class CacheSync:
    """Mirror what is not a git command: platform files, and trees created, moved or removed whole."""

    def __init__(self, store: RepoStore) -> None:
        self.store = store
        self.root = Path(store.cache_root)
        self._mu = threading.RLock()
        self._root_real = os.path.realpath(str(store.cache_root))
        self.known_repos: set[str] = set()
        self.files: dict[str, str] = {}
        self.durable: dict[str, bytes] = {}

    def baseline(self) -> None:
        """After a cold hydrate: what the cache holds now is what the bucket holds."""
        repos, files = scan_cache(self.root, self.store)
        self.known_repos = {r for r in repos if not transient(r)}
        self.durable = {f: wpaths.read_bytes_inside(self.root, f) or b"" for f in files}
        self.files = {f: hashlib.sha256(d).hexdigest() for f, d in self.durable.items()}

    # ── synchronous: one path, from the workspace_paths / atomic_json observer ───────────────
    def _rel(self, path: str) -> Optional[str]:
        real = os.path.abspath(path)
        base = self._root_real.rstrip("/") + "/"
        if not real.startswith(base):
            real = os.path.realpath(path)
            if not real.startswith(base):
                return None
        rel = real[len(base):]
        return None if not rel or transient(rel) else rel

    def _in_repo(self, rel: str) -> Optional[str]:
        """The repository ``rel`` sits strictly inside, if any."""
        parts = rel.split("/")
        for i in range(1, len(parts)):
            if os.path.isdir(os.path.join(self._root_real, *parts[:i], ".git")):
                return "/".join(parts[:i])
        return None

    def path_written(self, path: str) -> None:
        """A platform file outside every repository was written: in the bucket before this returns,
        or reverted to its last durable bytes and refused. A file inside a repository is git's."""
        rel = self._rel(path)
        if rel is None or self._in_repo(rel) is not None:
            return
        with self._mu:
            data = wpaths.read_bytes_inside(self.root, rel)
            if data is None:
                return
            digest = hashlib.sha256(data).hexdigest()
            if self.files.get(rel) == digest:
                return
            try:
                self.store.put_file(rel, data)
            except _STORE_ERRORS as exc:
                self.store.last_error = f"{type(exc).__name__}: {exc}"
                with self.store.paused():
                    if rel in self.durable:
                        wpaths.write_bytes_inside(self.root, rel, self.durable[rel], mode=0o600)
                    else:
                        wpaths.unlink_inside(self.root, rel)
                raise classify(exc) from exc
            self.durable[rel] = data
            self.files[rel] = digest

    def revert_path(self, path: str) -> None:
        """Put one platform file back to its last durable bytes (or remove it if it never was)."""
        rel = self._rel(path)
        if rel is None or self._in_repo(rel) is not None:
            return
        with self._mu, self.store.paused():
            if rel in self.durable:
                wpaths.write_bytes_inside(self.root, rel, self.durable[rel], mode=0o600)
            else:
                wpaths.unlink_inside(self.root, rel)

    def path_removed(self, path: str) -> None:
        """A file or a whole folder was removed: every repository and platform file at or under it
        leaves the bucket before this returns. Inside a repository it is git's."""
        rel = self._rel(path)
        if rel is None or self._in_repo(rel) is not None:
            return
        under = lambda r: r == rel or r.startswith(rel + "/")  # noqa: E731
        with self._mu:
            try:
                for repo in sorted(r for r in self.known_repos if under(r)):
                    with self.store.locks.hold(repo):
                        self.store.delete_repo(repo)
                    self.known_repos.discard(repo)
                for f in sorted(f for f in self.files if under(f)):
                    self.store.delete_file(f)
                    self.files.pop(f, None)
                    self.durable.pop(f, None)
            except _STORE_ERRORS as exc:
                self.store.last_error = f"{type(exc).__name__}: {exc}"
                raise classify(exc) from exc

    def _revert_files(self) -> None:
        """A platform-file change the bucket did not take is not kept: back to the last durable bytes."""
        with self.store.paused():
            _, files = scan_cache(self.root, self.store)
            for rel in files:
                data = wpaths.read_bytes_inside(self.root, rel)
                if rel not in self.durable:
                    wpaths.unlink_inside(self.root, rel)
                elif data != self.durable[rel]:
                    wpaths.write_bytes_inside(self.root, rel, self.durable[rel], mode=0o600)
            for rel, data in self.durable.items():
                if rel not in files:
                    wpaths.write_bytes_inside(self.root, rel, data, mode=0o600)

    def sync(self) -> dict:
        """Make the bucket match the cache for everything the ref observer does not cover. Raises
        :class:`WorkspaceStoreError` on failure, so the request that caused the change fails loudly."""
        with self._mu:
            try:
                return self._sync()
            except _STORE_ERRORS as exc:
                self.store.last_error = f"{type(exc).__name__}: {exc}"
                try:
                    self._revert_files()
                except Exception:  # noqa: BLE001
                    logger.exception("workspace store: platform files could not be reverted")
                raise classify(exc) from exc

    def _sync(self) -> dict:
        done = {"published": 0, "deleted": 0, "files_put": 0, "files_deleted": 0}
        repos, files = scan_cache(self.root, self.store)
        live = {r for r in repos if not transient(r)}
        for rel in sorted(live):
            mark = self.store.marker(rel)
            if mark is not None and mark.get("rel") == rel:
                continue
            with self.store.locks.hold(rel) as held:
                if self.store.publish(rel, held=held, replace=True) is not None:
                    done["published"] += 1
        for rel in sorted(self.known_repos - live):
            with self.store.locks.hold(rel):
                self.store.delete_repo(rel)
            done["deleted"] += 1
        self.known_repos = live
        current = {}
        for rel in files:
            data = wpaths.read_bytes_inside(self.root, rel)
            if data is None:
                continue
            digest = hashlib.sha256(data).hexdigest()
            current[rel] = digest
            if self.files.get(rel) != digest:
                self.store.put_file(rel, data)
                self.durable[rel] = data
                self.files[rel] = digest
                done["files_put"] += 1
        for rel in sorted(set(self.files) - set(current)):
            self.store.delete_file(rel)
            self.durable.pop(rel, None)
            self.files.pop(rel, None)
            done["files_deleted"] += 1
        self.files = current
        return done
