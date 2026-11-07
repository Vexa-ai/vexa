"""client.py — a worker's side of the workspace store (``backend: s3``). Standard library only.

A worker never holds a bucket credential. Its ``/workspaces`` is an emptyDir; at boot it hydrates the
mounts of its own dispatch (``VEXA_MOUNTS``) from agent-api's store service, and every commit it makes
in a writable mount is pushed back through agent-api before ``run_git`` returns — so the turn's
commit event, and its success, come after the change is durable in the bucket. A push that fails
un-commits locally (``reset --soft`` to the durable base) and raises :class:`WorkspaceStoreError`.

    GET  <store>/fetch?path=<mount path>[&have=<sha>]   → a git bundle (204: nothing new)
    POST <store>/push?path=<mount path>&base=<sha>      ← a git bundle of base..HEAD

``<store>`` is ``VEXA_WORKSPACE_STORE_URL`` (an http(s) URL — the gateway's ``/agent/workspace-store``);
the bearer is the dispatch's delegation token.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from typing import Callable, Iterator, Optional

from workspaces.shared import gitexec
from workspaces.shared import workspace_paths as wpaths

log = logging.getLogger("workspaces.store.client")
MARKER = ".git/vexa-store.json"


class WorkspaceStoreError(RuntimeError):
    """A worker's commit could not be made durable; the turn must not report it saved."""


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get("VEXA_WORKSPACE_STORE_URL") or "").startswith(("http://", "https://"))


class StoreClient:
    def __init__(self, base: str, token: Callable[[], str], *, timeout: float = 60.0, opener=None) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def _req(self, method: str, op: str, params: dict, body: Optional[bytes] = None):
        url = f"{self.base}/{op}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, data=body, method=method, headers={
            "authorization": f"Bearer {self.token()}", "content-type": "application/octet-stream"})
        try:
            with self._open(req, timeout=self.timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            detail = e.read()[:500].decode("utf-8", "replace")
            raise WorkspaceStoreError(f"workspace store {op} {params.get('path')}: HTTP {e.code} {detail}") from e
        except (urllib.error.URLError, OSError) as e:
            raise WorkspaceStoreError(f"workspace store {op} {params.get('path')}: {type(e).__name__}: {e}") from e

    def fetch(self, path: str, have: str = "") -> Optional[bytes]:
        status, body = self._req("GET", "fetch", {"path": path, **({"have": have} if have else {})})
        return None if status == 204 or not body else body

    def push(self, path: str, base: str, data: bytes) -> dict:
        _, body = self._req("POST", "push", {"path": path, "base": base}, data)
        return json.loads(body or b"{}")


def _git(repo, *args, check=True, **kw):
    return gitexec.run_git(str(repo), *args, check=check, **kw)


@contextmanager
def _temp(data: bytes) -> Iterator[str]:
    fd, path = tempfile.mkstemp(suffix=".bundle")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        yield path
    finally:
        os.unlink(path)


class WorkerCache:
    """The worker's hydrated mounts and its push-back observer."""

    def __init__(self, client: StoreClient, mounts: list[dict]) -> None:
        self.client = client
        # a mount with its own host source (an operator-managed _global) is not in the store
        self.mounts = {os.path.realpath(m["path"]): m for m in mounts if m.get("path") and not m.get("source")}
        self._tls = threading.local()

    # ── boot: only this dispatch's workspaces ────────────────────────────────────────────────
    def hydrate(self) -> int:
        n = 0
        for path, m in self.mounts.items():
            data = self.client.fetch(m["path"])
            if data is None:
                os.close(wpaths.dir_fd_inside(path, [], create=True))
                continue
            os.close(wpaths.dir_fd_inside(path, [], create=True))
            with self._paused():
                _git(path, "init", "-q")
                with _temp(data) as b:
                    _git(path, "fetch", "-q", "--no-tags", "--no-write-fetch-head", b,
                         "+refs/heads/*:refs/heads/*", env={"GIT_ALLOW_PROTOCOL": "file"})
                    heads = _git(path, "bundle", "list-heads", b, env={"GIT_ALLOW_PROTOCOL": "file"}).stdout
                head_ref = next((ln.split()[1] for ln in heads.splitlines()
                                 if ln.split()[1:] and ln.split()[1].startswith("refs/heads/")), "refs/heads/main")
                _git(path, "symbolic-ref", "HEAD", head_ref)
                _git(path, "reset", "-q", "--hard")
            self._mark(path, self._head(path))
            n += 1
        return n

    def freshen(self) -> None:
        """Before a turn: bring each mount up to what agent-api holds (a route may have written it
        since). Nothing new → nothing fetched."""
        for path, m in self.mounts.items():
            base = (self._marker(path) or {}).get("base", "")
            data = self.client.fetch(m["path"], have=base)
            if data is None:
                continue
            with self._paused(), _temp(data) as b:
                _git(path, "fetch", "-q", "--no-tags", "--no-write-fetch-head", b,
                     "+HEAD:refs/vexa-store/incoming", env={"GIT_ALLOW_PROTOCOL": "file"})
                _git(path, "merge", "-q", "--ff-only", "refs/vexa-store/incoming")
                _git(path, "update-ref", "-d", "refs/vexa-store/incoming", check=False)
            self._mark(path, self._head(path))

    # ── gitexec ref observer: push every commit back before it counts ────────────────────────
    def before(self, root: str, sub: str, rest: tuple):
        if getattr(self._tls, "paused", False):
            return None
        m = self.mounts.get(os.path.realpath(root))
        if m is None or not m.get("write", True):
            return None
        return root, (self._marker(root) or {}).get("base", "")

    def after(self, token, ok: bool) -> None:
        if token is None or not ok:
            return
        root, base = token
        head = self._head(root)
        if not head or head == base:
            return
        ref = _git(root, "symbolic-ref", "-q", "HEAD", check=False).stdout.strip() or "HEAD"
        try:
            fd, path = tempfile.mkstemp(suffix=".bundle")
            os.close(fd)
            try:
                _git(root, "bundle", "create", "-q", path, ref, *([f"^{base}"] if base else []))
                with open(path, "rb") as fh:
                    data = fh.read()
            finally:
                os.unlink(path)
            self.client.push(self.mounts[os.path.realpath(root)]["path"], base, data)
        except Exception as exc:
            with self._paused():
                if base:
                    _git(root, "reset", "-q", "--soft", base, check=False)
            raise exc if isinstance(exc, WorkspaceStoreError) else WorkspaceStoreError(str(exc)) from exc
        self._mark(root, head)

    # ── helpers ──────────────────────────────────────────────────────────────────────────────
    @contextmanager
    def _paused(self) -> Iterator[None]:
        prev = getattr(self._tls, "paused", False)
        self._tls.paused = True
        try:
            yield
        finally:
            self._tls.paused = prev

    def _head(self, repo: str) -> str:
        return _git(repo, "rev-parse", "-q", "--verify", "HEAD", check=False).stdout.strip()

    def _marker(self, repo: str) -> Optional[dict]:
        raw = wpaths.read_text_inside(repo, MARKER, allow=(".git",))
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None

    def _mark(self, repo: str, base: str) -> None:
        wpaths.write_text_inside(repo, MARKER, json.dumps({"base": base}), allow=(".git",))


def install_observer(observer) -> list:
    """Install ``observer`` on every copy of gitexec this process has (the worker commits through
    ``llm.gitexec``, the README refresh through ``workspaces.shared.gitexec``)."""
    import importlib

    done = []
    for name in ("workspaces.shared.gitexec", "llm.gitexec", "shared.gitexec"):
        try:
            mod = importlib.import_module(name)
        except Exception:  # noqa: BLE001 — a copy this image does not ship
            continue
        mod.set_ref_observer(observer)
        done.append(name)
    return done


def install_path_observer(observer) -> list:
    """Install ``observer`` on every copy of workspace_paths this process has (agent-api only)."""
    import importlib

    done = []
    for name in ("workspaces.shared.workspace_paths", "llm.workspace_paths"):
        try:
            mod = importlib.import_module(name)
        except Exception:  # noqa: BLE001
            continue
        mod.set_store_observer(observer)
        done.append(name)
    return done


def boot(token: Callable[[], str], env=None) -> Optional[WorkerCache]:
    """At worker start with ``backend: s3``: hydrate this dispatch's mounts and install the push-back
    observer. A hydrate that fails stops the worker (P18) — a turn over an empty mount would write
    a workspace that is not the durable one."""
    env = os.environ if env is None else env
    if not enabled(env):
        return None
    try:
        mounts = json.loads(env.get("VEXA_MOUNTS") or "[]")
    except ValueError:
        mounts = []
    cache = WorkerCache(StoreClient(env["VEXA_WORKSPACE_STORE_URL"], token), mounts)
    n = cache.hydrate()
    install_observer(cache)
    log.info("workspace store: hydrated %d of %d mounts from agent-api", n, len(mounts))
    return cache
