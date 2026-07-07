"""Real adapters for the workspace seams (O-AG-2).

These fill the ``WorkspacePort`` / ``VcsPort`` holes with real ``git`` against a LOCAL directory —
derived from the parent ``agent/workspace.py`` (git clone/add/commit + token-in-remote push),
reimplemented clean against the v0.12 ports. No network is required: ``clone`` takes a local repo
path (``file://`` or a directory), and the GitHub push targets a bare local repo in the eval.

Discipline (P15): the per-user GitHub token is a BROKERED secret (identity ``SecretsPort``). It is
``reveal()``-ed ONLY to assemble the authenticated remote URL for a single push, and is NEVER
logged, never written into the repo's persisted remote (we strip it afterward, as the parent does).
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

import yaml

from shared.gitenv import scrubbed_git_env
from shared.models import WorkspaceWrite
from shared.ports import IdentityPort, RuntimePort, SchedulerPort, StreamReader, VcsPort, WorkspacePort

logger = logging.getLogger("agent_api.adapters")

# The remote name the VcsPort pushes the user workspace to (kept distinct from the clone's origin).
_PUSH_REMOTE = "vexa-vcs"


# ── markdown entity (frontmatter + body) serialization ───────────────────────

def render_entity(write: WorkspaceWrite) -> str:
    """Render a WorkspaceWrite as a YAML-frontmatter markdown document (the workspace.v1 layout)."""
    fm = yaml.safe_dump(write.frontmatter, sort_keys=True, default_flow_style=False).strip()
    return f"---\n{fm}\n---\n{write.body}"


def parse_entity(text: str) -> tuple[dict, str]:
    """Inverse of ``render_entity`` — return (frontmatter, body) from a frontmatter markdown doc."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    return yaml.safe_load(m.group(1)) or {}, m.group(2)


# ── git helpers ──────────────────────────────────────────────────────────────

def _git(cwd: Path, *args: str, token: str | None = None) -> str:
    """Run a git command in ``cwd``; return trimmed stdout. ``token`` (if given) is passed via env
    for the duration of the call only and is NEVER placed on the argv (which can leak via ps).
    Always runs on a scrubbed env — a hook-exported GIT_DIR must never re-point the workspace op
    at the hook's repo (see shared/gitenv.py)."""
    env = scrubbed_git_env(GIT_ASKPASS="true") if token is not None else scrubbed_git_env()
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


# ── Lane W: the per-shared-workspace merge-coordinator WRITE point ────────────────────────────────
# A shared workspace is ONE git repo at <root>/<id> that every member's dispatch mounts (see
# control_plane/workspace_attach.shared_active_mounts) — NOT a per-member clone. So two members' agent
# turns commit the SAME repo concurrently, and without serialization their stage+commit race on git's
# index.lock and a turn's write can be lost (the "concurrent shared writes are not yet serialized"
# gap in control_plane/dispatch.py). This flock is that serialization point: it gives cross-process
# mutual exclusion for the commit critical section on ONE node (the shared workspace volume is a POSIX
# fs, so flock is the right primitive — the same reasoning workspace_membership.py uses for policy/,
# lifted from a process-local threading.Lock to a cross-process file lock).
#
# SCOPE (honest): this makes concurrent commits SAFE (no index corruption / lost turn). It does NOT yet
# isolate overlapping edits to the SAME entity — two turns editing one file still last-writer-wins at
# the file level; true per-turn isolation (worktree-per-turn + git three-way on merge) is the next step.
# MULTI-NODE: an flock is node-local; a multi-replica agent-api additionally needs a shared lock
# (redis/advisory) or push-as-CAS (documented gap — workspace_membership.py MULTI-REPLICA NOTE).
WRITE_LOCK_TIMEOUT_S = float(os.environ.get("VEXA_WS_WRITE_LOCK_TIMEOUT_S", "30"))


class WorkspaceBusyError(RuntimeError):
    """The per-workspace write lock could not be acquired within the timeout — the coordinator is busy
    with another member's commit. Retryable: the caller re-attempts the turn-commit."""


@contextlib.contextmanager
def workspace_write_lock(work_dir: Path, timeout: float = WRITE_LOCK_TIMEOUT_S):
    """Serialize the commit critical section for the workspace repo at ``work_dir`` across processes on
    this node (Lane W). Blocks up to ``timeout`` seconds, then raises ``WorkspaceBusyError`` so a wedged
    holder can never stall a workspace forever (liveness). A no-op-safe wrapper: releases on any exit."""
    import fcntl  # POSIX-only; imported lazily so non-Linux dev hosts import the module fine

    # Lockfile lives in .git (never staged/committed) when the repo exists, else beside the tree.
    git_dir = work_dir / ".git"
    lock_path = (git_dir if git_dir.exists() else work_dir) / ".vexa-writer.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        start = time.monotonic()
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() - start > timeout:
                    raise WorkspaceBusyError(f"workspace write lock busy after {timeout}s: {work_dir}")
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class GitPushError(RuntimeError):
    """A token-authenticated push failed. The message is REDACTED of the token (P15) so it is safe
    to surface in an API error body / log."""


def push_with_token(work_dir: str | Path, remote_url: str, ref: str, token: str | None,
                    *, remote: str = _PUSH_REMOTE) -> str:
    """THE shared governed-push mechanic: push ``ref`` to ``remote_url`` over a DEDICATED remote so
    the repo's ``origin`` is never clobbered. The token (if any) rides on the remote URL for the
    push's duration ONLY, then the persisted remote is scrubbed back to the token-free URL (P15).
    Returns the pushed HEAD sha. NEVER forces — a non-fast-forward push fails loud. Failures raise
    ``GitPushError`` with the token redacted from the message.

    Both credential flows converge here: ``GitHubVcs.push`` (brokered secret store) and the
    per-call-token workspace publish (``control_plane.workspace_publish``)."""
    work = Path(work_dir)
    if token and "://" in remote_url:
        proto, rest = remote_url.split("://", 1)
        auth_url = f"{proto}://{token}@{rest}"
    else:
        auth_url = remote_url

    def redact(text: str) -> str:
        return text.replace(token, "***") if token else text

    try:
        # (Re-)point the dedicated remote at the authenticated URL — `set-url` on a re-publish,
        # `add` the first time (either may be the one that fails, hence the fallback order).
        try:
            _git(work, "remote", "set-url", remote, auth_url, token=token)
        except RuntimeError:
            _git(work, "remote", "add", remote, auth_url, token=token)
        try:
            _git(work, "push", remote, ref, token=token)
            return _git(work, "rev-parse", "HEAD", token=token)
        finally:
            # Strip the token from the persisted remote so it can't leak to the repo/object store.
            _git(work, "remote", "set-url", remote, remote_url, token=token)
    except RuntimeError as exc:
        raise GitPushError(redact(str(exc))) from None


class RealGitWorkspace(WorkspacePort):
    """``WorkspacePort`` backed by real ``git`` on a local working tree.

    Reimplements the parent ``workspace.py`` git lifecycle (clone → add → commit) against the v0.12
    port. ``clone`` pulls a LOCAL source repo (a path / file URL — no network in the eval). ``write``
    stages an entity markdown file; ``commit`` returns the real sha, or ``""`` when there is nothing
    to commit (the no-op contract the port promises). ``read`` round-trips the staged file text.
    """

    def __init__(self, work_dir: str | Path) -> None:
        self.work_dir = Path(work_dir)
        self._identity = ("vexa", "vexa@system")  # the parent's commit identity
        # Paths written this session. Staging is DEFERRED to commit() (under the Lane W write lock) so a
        # concurrent member's turn on a SHARED repo can't race us on git's index.lock — see write/commit.
        self._pending: list[str] = []

    def clone(self, repo_url: str, ref: str) -> None:
        self.work_dir.parent.mkdir(parents=True, exist_ok=True)
        if (self.work_dir / ".git").exists():
            _git(self.work_dir, "checkout", ref)
            return
        # Local clone (file path or file:// URL) — derived from parent git_clone_init.
        subprocess.run(
            ["git", "clone", repo_url, str(self.work_dir)],
            capture_output=True, text=True, check=True, env=scrubbed_git_env(),
        )
        name, email = self._identity
        _git(self.work_dir, "config", "user.name", name)
        _git(self.work_dir, "config", "user.email", email)
        # `ref` may not exist yet on a fresh repo; only switch if it resolves.
        try:
            _git(self.work_dir, "checkout", ref)
        except RuntimeError:
            pass

    def read(self, path: str) -> str | None:
        f = self.work_dir / path
        return f.read_text() if f.exists() else None

    def write(self, write: WorkspaceWrite) -> None:
        f = self.work_dir / write.path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(render_entity(write))
        # NOTE: deliberately no ``git add`` here. On a SHARED repo, staging grabs .git/index.lock and
        # would race a concurrent member's turn (proven: writes get dropped with "index.lock: File
        # exists"). Record the path; commit() stages it under the write lock (Lane W).
        if write.path not in self._pending:
            self._pending.append(write.path)

    def commit(self, message: str) -> str:
        # Lane W: ALL index mutation (stage + commit) happens under the per-workspace write lock, so a
        # concurrent member's turn on the SAME shared repo can never race us on git's index.lock.
        with workspace_write_lock(self.work_dir):
            paths = list(self._pending)
            if paths:
                # Stage + commit ONLY this worker's own paths (pathspec) so a commit carries exactly its
                # writes — never a concurrent member's half-written file, even sharing one working tree.
                _git(self.work_dir, "add", "--", *paths)
                dirty = _git(self.work_dir, "status", "--porcelain", "--", *paths)
            else:
                # Back-compat: no deferred writes → fall back to the whole-index behavior (e.g. a caller
                # that staged externally). Mirrors the parent's "if [ -n "$STATUS" ]" no-op guard.
                dirty = _git(self.work_dir, "status", "--porcelain")
            if not dirty:                 # nothing of ours changed vs HEAD → the no-op contract ("")
                self._pending.clear()
                return ""
            if paths:
                _git(self.work_dir, "commit", "-m", message, "--", *paths)
            else:
                _git(self.work_dir, "commit", "-m", message)
            self._pending.clear()
            return _git(self.work_dir, "rev-parse", "HEAD")


class SecretsBrokerProtocol(Protocol):
    """The shape of identity's ``SecretsPort`` we consume — broker a redacted, audited credential."""

    def get_secret(self, subject: str, secret_name: str, *, scope: str): ...


class GitHubVcs(VcsPort):
    """``VcsPort`` that pushes a user's workspace to their own GitHub repo over a BROKERED token.

    The token is fetched from identity's ``SecretsPort`` as a ``BrokeredSecret`` (redacted repr) and
    ``reveal()``-ed ONLY to build the authenticated remote URL for one push. We log metadata only,
    and — as the parent does after clone — reset the persisted remote to the token-free URL so the
    credential never lands in the repo config or the synced object store (P15).
    """

    def __init__(
        self,
        secrets: SecretsBrokerProtocol,
        *,
        subject: str,
        secret_name: str = "workspace_git.token",
        scope: str = "repo:push",
    ) -> None:
        self._secrets = secrets
        self._subject = subject
        self._secret_name = secret_name
        self._scope = scope

    def push(self, local_dir: str, remote_url: str, ref: str) -> str:
        work = Path(local_dir)
        brokered = self._secrets.get_secret(
            self._subject, self._secret_name, scope=self._scope
        )
        # METADATA ONLY — the value is never interpolated into a log record (P15).
        logger.info(
            "github push subject=%s remote=%s ref=%s token=%r",
            self._subject, remote_url, ref, brokered,  # %r → BrokeredSecret redacts itself
        )
        # The one shared governed-push mechanic (push_with_token): dedicated remote so the clone's
        # ``origin`` is never clobbered, token on the remote URL only for the push's duration, then
        # scrubbed back to the token-free URL; failure messages token-redacted (P15).
        return push_with_token(work, remote_url, ref, brokered.reveal(), remote=_PUSH_REMOTE)


class RuntimeHttpClient(RuntimePort):
    """A ``RuntimePort`` over runtime.v1's HTTP surface (``POST /workloads``) — the control-plane→kernel
    edge. agent-api never runs a worker in-process (P7); it asks the runtime kernel to spawn the
    ``agent`` workload. Uses stdlib urllib (no extra dep); the spec body is the runtime.v1 WorkloadSpec.
    """

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def spawn(self, workload_id: str, profile: str, env: dict[str, str]) -> str:
        body = json.dumps({"workloadId": workload_id, "profile": profile, "env": env}).encode()
        req = urllib.request.Request(
            f"{self._base}/workloads", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            status = json.loads(r.read())
        return status.get("workloadId", workload_id)

    def await_done(self, workload_id: str, timeout_sec: float = 0.0) -> str:
        req = urllib.request.Request(f"{self._base}/workloads/{workload_id}", method="GET")
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            status = json.loads(r.read())
        return status.get("state", "unknown")


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _signing_key(key: str | bytes) -> bytes:
    return key.encode("utf-8") if isinstance(key, str) else key


def _canon(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


class LocalIdentityMinter(IdentityPort):
    """Dev-tier ``IdentityPort`` — signs a per-dispatch token with a shared key (HS256)."""

    def __init__(self, signing_key: str, *, ttl_sec: int = 900) -> None:
        self._key = signing_key
        self._ttl = ttl_sec

    def mint(self, subject: str, launcher: str, workspaces: list[dict], tools: list[str]) -> str:
        if not subject:
            raise ValueError("dispatch token subject is required")
        if not launcher:
            raise ValueError("dispatch token launcher is required")
        grants = []
        for grant in workspaces:
            mode = str(grant.get("mode", ""))
            if mode not in ("ro", "rw"):
                raise ValueError(f"workspace grant mode must be ro|rw, got {mode!r}")
            workspace_id = str(grant.get("id", ""))
            if not workspace_id:
                raise ValueError("workspace grant id is required")
            grants.append({"id": workspace_id, "mode": mode})
        iat = int(time.time())
        payload = {
            "sub": subject,
            "lch": launcher,
            "ws": grants,
            "tools": list(tools or ()),
            "iat": iat,
            "exp": iat + int(self._ttl),
        }
        header = {"alg": "HS256", "typ": "vxd"}
        signing_input = f"{_b64u(_canon(header))}.{_b64u(_canon(payload))}"
        sig = hmac.new(_signing_key(self._key), signing_input.encode("ascii"), hashlib.sha256).digest()
        return f"{signing_input}.{_b64u(sig)}"


class RedisStreamReader(StreamReader):
    """Dev-tier ``StreamReader`` — ``XREAD`` a dispatch's output Stream ``unit:<id>:out`` and yield each
    UnitEvent until a terminal event (``done`` / ``turn-complete``) or an idle give-up. ``redis`` is
    imported LAZILY (the unit tests inject a fake reader)."""

    def __init__(self, redis_url: str, *, block_ms: int = 30000, idle_giveup_ms: int = 120000) -> None:
        self._url = redis_url
        self._block = block_ms
        self._giveup = idle_giveup_ms

    def read(self, unit_id: str, *, resume: str | None = None):
        import redis

        client = redis.from_url(self._url, decode_responses=True)
        topic = f"unit:{unit_id}:out"
        # Fresh connect → ``$`` (only events from now on). Reconnect → the client's last-seen Stream id
        # (Last-Event-ID): XREAD gaplessly delivers everything published while the reader was gone —
        # crucial when a per-dispatch worker cold-starts AFTER the SSE dropped (the false 'No chat
        # output arrived' failure was the reader giving up / the stream ending before any resume).
        last_id = resume or "$"
        waited = 0
        while True:
            resp = client.xread({topic: last_id}, count=50, block=self._block)
            if not resp:
                waited += self._block
                if waited >= self._giveup:
                    return
                continue
            waited = 0
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    last_id = entry_id
                    ev = json.loads(fields.get("event", "{}"))
                    # Surface the Stream id as the SSE cursor (``id:``) so a dropped view resumes here.
                    yield (ev, entry_id)
                    # `turn-complete` is the worker's terminal marker — it comes AFTER `done` + `commit`,
                    # so stopping on `done` would drop the commit. Close the view only on turn-complete.
                    if ev.get("type") == "turn-complete":
                        return


class SchedulerHttpClient(SchedulerPort):
    """A ``SchedulerPort`` over the runtime's ``/schedule`` surface (schedule.v1) — the control-plane→cron
    edge. agent-api authors routine jobs here; the runtime owns the durable cron. Stdlib urllib, no dep."""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def schedule(self, job: dict) -> dict:
        body = json.dumps(job).encode()
        req = urllib.request.Request(
            f"{self._base}/schedule", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return json.loads(r.read())

    def list_jobs(self, *, status: str | None = None, limit: int = 50) -> list[dict]:
        q = f"?limit={limit}" + (f"&status={status}" if status else "")
        req = urllib.request.Request(f"{self._base}/schedule{q}", method="GET")
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return json.loads(r.read())

    def cancel_job(self, job_id: str) -> dict | None:
        req = urllib.request.Request(f"{self._base}/schedule/{job_id}", method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise


class AdminApiMembershipIndex:
    """A ``MembershipIndex`` (control_plane.workspace_membership) over the identity admin-api's internal
    edge — the DERIVED ``users.data.memberships[]`` mirror of the authoritative ``policy/members.json``.

    agent-api has no DB; the memberships index lives in the identity service's Postgres. This adapter
    POSTs the mirror updates over the admin-api's internal tier (``X-Internal-Secret``, same shape as the
    gateway→admin-api authz oracle). Best-effort by contract: the caller catches and logs failures — the
    git file is the recovery source (Q6), so a down index never loses a grant. Stdlib urllib (no dep).
    """

    def __init__(self, base_url: str, internal_secret: str, *, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._secret = internal_secret
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._secret:
            h["X-Internal-Secret"] = self._secret
        return h

    def add(self, subject: str, workspace_id: str, role: str, added_at: str) -> None:
        body = json.dumps({"workspace_id": workspace_id, "role": role, "added_at": added_at}).encode()
        req = urllib.request.Request(
            f"{self._base}/internal/users/{subject}/memberships",
            data=body, headers=self._headers(), method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout):
            pass

    def remove(self, subject: str, workspace_id: str) -> None:
        req = urllib.request.Request(
            f"{self._base}/internal/users/{subject}/memberships/{workspace_id}",
            headers=self._headers(), method="DELETE",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout):
                pass
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise

    def list(self, subject: str) -> list[dict]:
        req = urllib.request.Request(
            f"{self._base}/internal/users/{subject}/memberships",
            headers=self._headers(), method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                data = json.loads(r.read())
            return data.get("memberships", []) if isinstance(data, dict) else (data or [])
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            raise
