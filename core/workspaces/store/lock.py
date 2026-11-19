"""lock.py — the per-workspace write lock, the guard that works on every S3.

One Redis key per workspace, ``wsstore:lock:<path>``: ``SET NX PX`` with a random token, extended
while a publish runs, released by compare-and-delete; a fencing counter ``INCR`` per acquisition is
stamped into the generation it guards. Re-entrant within one thread, so a route that holds the lock
around a whole operation can run git commands whose own observer asks for it again.

Redis unreachable → :class:`LockUnavailable`, and the write is refused (P18). There is no fallback
to "no lock".
"""
from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional



class LockBusy(RuntimeError):
    """Another writer holds the workspace."""


class LockUnavailable(RuntimeError):
    """The lock service cannot be reached; writes stop."""


class LockLost(RuntimeError):
    """The lock expired or was taken over before the write could be made durable."""


class Held:
    def __init__(self, locks: "WorkspaceLocks", path: str, token: str, fence: int) -> None:
        self.locks, self.path, self.token, self.fence = locks, path, token, fence
        self.depth = 1

    def check(self) -> None:
        """Still ours? Extends the TTL when it is; raises :class:`LockLost` when it is not."""
        self.locks._extend(self)


class WorkspaceLocks:
    """``client`` is a redis-py client (fakeredis in tests); None keeps the locks in this process,
    which is correct only for a single process (tests, one-replica dev)."""

    def __init__(self, client=None, *, ttl_ms: int = 60_000, wait_s: float = 10.0) -> None:
        self._r = client
        self.ttl_ms = ttl_ms
        self.wait_s = wait_s
        self._tls = threading.local()
        self._local: dict[str, str] = {}
        self._local_fence = 0
        self._mu = threading.Lock()

    def _held(self) -> dict:
        if not hasattr(self._tls, "held"):
            self._tls.held = {}
        return self._tls.held

    def current(self, path: str) -> Optional[Held]:
        return self._held().get(path)

    def _try(self, key: str, token: str) -> bool:
        if self._r is None:
            with self._mu:
                if key in self._local:
                    return False
                self._local[key] = token
                return True
        try:
            return bool(self._r.set(key, token, nx=True, px=self.ttl_ms))
        except Exception as exc:  # noqa: BLE001
            raise LockUnavailable(f"workspace lock service unreachable: {type(exc).__name__}") from exc

    def _fence(self, path: str) -> int:
        if self._r is None:
            with self._mu:
                self._local_fence += 1
                return self._local_fence
        try:
            return int(self._r.incr(f"wsstore:fence:{path}"))
        except Exception as exc:  # noqa: BLE001
            raise LockUnavailable(f"workspace lock service unreachable: {type(exc).__name__}") from exc

    def advance_fence(self, path: str, at_least: int) -> None:
        """Move the fencing counter past ``at_least`` (a Redis that lost its data restarted it below
        what the bucket already records). The next acquisition gets a larger token."""
        if self._r is None:
            with self._mu:
                self._local_fence = max(self._local_fence, at_least)
            return
        try:
            key = f"wsstore:fence:{path}"
            cur = int(self._r.get(key) or 0)
            if cur < at_least:
                self._r.set(key, at_least)
        except Exception as exc:  # noqa: BLE001
            raise LockUnavailable(f"workspace lock service unreachable: {type(exc).__name__}") from exc

    def _extend(self, held: Held) -> None:
        key = f"wsstore:lock:{held.path}"
        if self._r is None:
            if self._local.get(key) != held.token:
                raise LockLost(held.path)
            return
        try:
            ok = self._cas(key, held.token, lambda pipe: pipe.pexpire(key, self.ttl_ms))
        except Exception as exc:  # noqa: BLE001
            raise LockUnavailable(f"workspace lock service unreachable: {type(exc).__name__}") from exc
        if not ok:
            raise LockLost(held.path)

    def _release(self, held: Held) -> None:
        key = f"wsstore:lock:{held.path}"
        if self._r is None:
            with self._mu:
                if self._local.get(key) == held.token:
                    del self._local[key]
            return
        try:
            self._cas(key, held.token, lambda pipe: pipe.delete(key))
        except Exception:  # noqa: BLE001 — the TTL frees it
            pass

    def _cas(self, key: str, token: str, op) -> bool:
        """Run ``op`` on a MULTI only while ``key`` still holds ``token`` (WATCH: optimistic, no Lua,
        so any Redis — and a Redis that forbids scripts — serves it)."""
        import redis as _redis  # noqa: PLC0415

        with self._r.pipeline() as pipe:
            try:
                pipe.watch(key)
                if pipe.get(key) != token:
                    pipe.unwatch()
                    return False
                pipe.multi()
                op(pipe)
                pipe.execute()
                return True
            except _redis.WatchError:
                return False

    def acquire(self, path: str, *, wait_s: Optional[float] = None) -> Held:
        held = self.current(path)
        if held is not None:
            held.depth += 1
            return held
        key = f"wsstore:lock:{path}"
        token = uuid.uuid4().hex
        deadline = time.monotonic() + (self.wait_s if wait_s is None else wait_s)
        while not self._try(key, token):
            if time.monotonic() >= deadline:
                raise LockBusy(path)
            time.sleep(0.05)
        held = Held(self, path, token, self._fence(path))
        self._held()[path] = held
        return held

    def release(self, held: Held) -> None:
        held.depth -= 1
        if held.depth > 0:
            return
        self._held().pop(held.path, None)
        self._release(held)

    @contextmanager
    def hold(self, path: str, *, wait_s: Optional[float] = None) -> Iterator[Held]:
        held = self.acquire(path, wait_s=wait_s)
        try:
            yield held
        finally:
            self.release(held)
