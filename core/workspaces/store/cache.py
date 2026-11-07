"""cache.py — agent-api's workspace cache: hydrated on first use, evicted least-recently-used.

The bucket is the truth, so the cache may drop any workspace that holds nothing unpublished and
bring it back on demand. A workspace not in use is a **stub**: its folders, an empty placeholder for
every file of HEAD (existence checks answer as for the real tree), and a ``.git`` holding only the
marker (``repostore.make_stub``). The first access through ``workspace_paths`` (every primitive opens
its folder through ``dir_fd_inside``) or ``gitexec`` (every command) hydrates it: built in a sibling
folder and swapped in by rename, so a reader never sees a half-built tree.

Eviction keeps the total under ``max_bytes``: least recently accessed first, never a pinned one
(``_global`` is read on every request), never one accessed in the last ``min_idle_s``, never one
whose lock is held, that has uncommitted changes, or whose refs are not the published generation's.
Durability is unaffected: an evicted tree is exactly a published generation.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from workspaces.shared import gitexec

from .lock import LockBusy
from .objects import StoreUnavailable
from .repostore import STUB_FLAG, RepoStore, _swap_in, _transient_name, local_refs

logger = logging.getLogger("workspaces.store.cache")
_MAX_DEPTH = 4


def tree_bytes(path: Path) -> int:
    """Bytes on disk under ``path`` (the store's own cache folder), links not followed."""
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


class CacheManager:
    def __init__(self, store: RepoStore, *, max_bytes: int = 0, min_idle_s: float = 60.0,
                 pinned: tuple = ("_global",)) -> None:
        self.store = store
        self.root = os.path.realpath(str(store.cache_root))
        self.max_bytes = max_bytes
        self.min_idle_s = min_idle_s
        self.pinned = set(pinned)
        self.touched: dict[str, float] = {}
        self.sizes: dict[str, int] = {}
        self.hydrations = 0
        self.evictions = 0
        self._mu = threading.RLock()

    # ── which workspace a path is in ─────────────────────────────────────────────────────────
    def repo_of(self, path: str) -> Optional[tuple[str, str, bool]]:
        """``(rel, folder, is_stub)`` of the repository ``path`` is in or at, else None. Pure
        ``lstat``s on at most four folders below the cache root."""
        real = os.path.abspath(path)
        if not real.startswith(self.root.rstrip("/") + "/"):
            real = os.path.realpath(path)
            if not real.startswith(self.root.rstrip("/") + "/"):
                return None
        parts = real[len(self.root.rstrip("/")) + 1:].split("/")
        for i in range(1, min(len(parts), _MAX_DEPTH) + 1):
            if _transient_name(parts[i - 1]):
                return None
            folder = os.path.join(self.root, *parts[:i])
            if os.path.isdir(os.path.join(folder, ".git")):
                return "/".join(parts[:i]), folder, os.path.exists(os.path.join(folder, STUB_FLAG))
        return None

    # ── first use ────────────────────────────────────────────────────────────────────────────
    def access(self, path: str) -> None:
        """Called before any folder is opened and before any git command (the store observer)."""
        if self.store.is_paused():
            return
        hit = self.repo_of(path)
        if hit is None:
            return
        rel, folder, stub = hit
        with self._mu:
            self.touched[rel] = time.monotonic()
        if stub:
            self.materialize(rel, Path(folder))

    def materialize(self, rel: str, folder: Path) -> None:
        """Hydrate the stub at ``folder``. A stub moved by a whole-tree rename names its source in
        its marker; it is hydrated from there (the next sync publishes it at its new place)."""
        with self.store.locks.hold(rel):
            if not os.path.exists(os.path.join(folder, STUB_FLAG)):
                return                                    # another thread got there first
            mark = self.store.marker(rel, at=folder) or {}
            source = mark.get("rel") or rel
            gen = self.store.latest(source)
            if gen is None:
                raise StoreUnavailable(f"workspace {source} has a stub in the cache but no generation "
                                       "in the bucket")
            self.store._hydrate_stub(source, folder, gen)
        with self._mu:
            self.hydrations += 1
            self.sizes[rel] = tree_bytes(folder)
        logger.info("workspace store: hydrated %s on first use", rel)
        self.maybe_evict(keep=rel)

    # ── eviction ─────────────────────────────────────────────────────────────────────────────
    def total(self) -> int:
        with self._mu:
            return sum(self.sizes.values())

    def refresh_sizes(self) -> None:
        from .repostore import scan_cache  # noqa: PLC0415

        repos, _ = scan_cache(self.store.cache_root, self.store)
        sizes = {}
        for rel in repos:
            folder = self.store.path(rel)
            if os.path.exists(os.path.join(folder, STUB_FLAG)):
                continue
            sizes[rel] = tree_bytes(folder)
        with self._mu:
            self.sizes = sizes

    def maybe_evict(self, *, keep: str = "") -> int:
        """Evict least-recently-used workspaces until the cache fits ``max_bytes``."""
        if not self.max_bytes or self.total() <= self.max_bytes:
            return 0
        now = time.monotonic()
        with self._mu:
            candidates = sorted((self.touched.get(rel, 0.0), rel) for rel in self.sizes
                                if rel not in self.pinned and rel != keep
                                and now - self.touched.get(rel, 0.0) >= self.min_idle_s)
        n = 0
        for _, rel in candidates:
            if self.total() <= self.max_bytes:
                break
            if self.evict(rel):
                n += 1
        return n

    def evict(self, rel: str) -> bool:
        """Replace ``rel``'s tree with its stub, if nothing unpublished is in it. True when done."""
        store = self.store
        folder = store.path(rel)
        if rel in self.pinned or os.path.exists(os.path.join(folder, STUB_FLAG)):
            return False
        try:
            held = store.locks.acquire(rel, wait_s=0)
        except LockBusy:
            return False
        try:
            with store.paused():
                if gitexec.run_git(str(folder), "status", "--porcelain", check=False,
                                   env={"GIT_OPTIONAL_LOCKS": "0"}).stdout.strip():
                    return False                          # uncommitted edits: not evictable
                mark = store.marker(rel)
                if not mark or mark.get("rel") != rel or local_refs(folder)[0] != (mark.get("refs") or {}):
                    return False                          # not exactly the published generation
            gen = store.latest(rel)
            if gen is None or gen.gen != int(mark.get("gen", -1)):
                return False
            tmp = folder.parent / f".{folder.name}.hydrating-{uuid.uuid4().hex[:8]}"
            with store.paused():
                store.make_stub(rel, gen, at=tmp)
                _swap_in(tmp, folder)
        except StoreUnavailable:
            return False                                  # cannot confirm it is published: keep it
        finally:
            store.locks.release(held)
        with self._mu:
            self.sizes.pop(rel, None)
            self.evictions += 1
        logger.info("workspace store: evicted %s (least recently used)", rel)
        return True
