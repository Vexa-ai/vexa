"""Real adapters for the workspace seams (O-AG-2).

These fill the ``WorkspacePort`` / ``VcsPort`` holes with real ``git`` against a LOCAL directory —
derived from the parent ``agent_api/workspace.py`` (git clone/add/commit + token-in-remote push),
reimplemented clean against the v0.12 ports. No network is required: ``clone`` takes a local repo
path (``file://`` or a directory), and the GitHub push targets a bare local repo in the eval.

Discipline (P15): the per-user GitHub token is a BROKERED secret (identity ``SecretsPort``). It is
``reveal()``-ed ONLY to assemble the authenticated remote URL for a single push, and is NEVER
logged, never written into the repo's persisted remote (we strip it afterward, as the parent does).
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

import yaml

from .models import WorkspaceWrite
from .ports import RuntimePort, SchedulerPort, VcsPort, WorkspacePort

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
    for the duration of the call only and is NEVER placed on the argv (which can leak via ps)."""
    env = None
    if token is not None:
        import os

        env = {**os.environ, "GIT_ASKPASS": "true"}  # never interactively prompt
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

    def clone(self, repo_url: str, ref: str) -> None:
        self.work_dir.parent.mkdir(parents=True, exist_ok=True)
        if (self.work_dir / ".git").exists():
            _git(self.work_dir, "checkout", ref)
            return
        # Local clone (file path or file:// URL) — derived from parent git_clone_init.
        subprocess.run(
            ["git", "clone", repo_url, str(self.work_dir)],
            capture_output=True, text=True, check=True,
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
        _git(self.work_dir, "add", "--", write.path)

    def commit(self, message: str) -> str:
        # Mirror the parent's "if [ -n "$STATUS" ]" guard: a clean tree → no-op commit returns "".
        if not _git(self.work_dir, "status", "--porcelain"):
            return ""
        _git(self.work_dir, "commit", "-m", message)
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
        if "://" in remote_url:
            proto, rest = remote_url.split("://", 1)
            auth_url = f"{proto}://{brokered.reveal()}@{rest}"
        else:
            auth_url = remote_url
        # A dedicated remote so we never clobber the clone's ``origin``; the token lives on it only
        # for the duration of the push, then we replace it with the token-free URL (P15).
        _git(work, "remote", "add", _PUSH_REMOTE, auth_url, token=brokered.reveal())
        try:
            _git(work, "push", _PUSH_REMOTE, ref, token=brokered.reveal())
            return _git(work, "rev-parse", "HEAD")
        finally:
            # Strip the token from the persisted remote so it can't leak to the repo/object store.
            _git(work, "remote", "set-url", _PUSH_REMOTE, remote_url)


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
