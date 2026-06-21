"""In-memory fakes for the ports — what makes the L2 unit test possible (ARCHITECTURE.md §5).

Each fake implements a port's protocol with a dict/list, no I/O. The core can't tell them from a
real git repo or the runtime kernel, so its logic is proved offline.
"""
from __future__ import annotations

from pathlib import Path

from agent_api.models import AgentAction, WorkspaceWrite
from agent_api.ports import (
    AgentDecisionPort,
    RuntimePort,
    WorkspacePort,
    WorkspaceStoragePort,
)


class FakeWorkspace(WorkspacePort):
    """A workspace.v1 user repo, in memory. Records clones, files, and commits."""

    def __init__(self) -> None:
        self.cloned: tuple[str, str] | None = None
        self.files: dict[str, WorkspaceWrite] = {}
        self.commits: list[str] = []
        self._staged = False

    def clone(self, repo_url: str, ref: str) -> None:
        self.cloned = (repo_url, ref)

    def read(self, path: str) -> str | None:
        w = self.files.get(path)
        return w.body if w else None

    def write(self, write: WorkspaceWrite) -> None:
        self.files[write.path] = write
        self._staged = True

    def commit(self, message: str) -> str:
        if not self._staged:
            return ""  # no-op commit (mirrors real git "nothing to commit")
        self._staged = False
        commit_id = f"commit-{len(self.commits) + 1}"
        self.commits.append(message)
        return commit_id


class FakeRuntime(RuntimePort):
    """The runtime kernel, in memory. Records spawns; reports a terminal state immediately."""

    def __init__(self) -> None:
        self.spawned: list[tuple[str, str, dict[str, str]]] = []

    def spawn(self, workload_id: str, profile: str, env: dict[str, str]) -> str:
        self.spawned.append((workload_id, profile, env))
        return workload_id

    def await_done(self, workload_id: str, timeout_sec: float = 0.0) -> str:
        return "stopped"


class FakeLLM(AgentDecisionPort):
    """A scripted ``AgentDecisionPort`` standing in for the LLM — NEVER calls a real model.

    It returns a pre-set ``AgentAction`` regardless of the transcript, so an eval can hand the core
    a deliberately MALICIOUS or NON-CONFORMANT decision and prove the governance seam catches it.
    The LLM is the untrusted party here: whatever this returns, ``core.run`` re-validates against
    workspace.v1 before anything touches the user repo.
    """

    def __init__(self, action: AgentAction) -> None:
        self.action = action
        self.calls: list[dict] = []

    def decide(self, payload: dict) -> AgentAction:
        self.calls.append(payload)
        return self.action


def _is_excluded(rel: str, excludes: tuple[str, ...]) -> bool:
    return any(rel == e or rel.startswith(e.rstrip("/") + "/") for e in excludes)


class FakeStorage(WorkspaceStoragePort):
    """An S3/MinIO object store, in memory — a FAKE TRANSPORT for the storage seam.

    Records the object ops a real ``aws s3 sync`` would issue (``sync_down`` GETs into the local dir,
    ``sync_up`` PUTs the local tree, deleting extraneous keys), and honors the exclude list (the
    ``.claude/.session`` ephemera the parent excludes). No boto3, no network.
    """

    def __init__(self, objects: dict[str, str] | None = None) -> None:
        self.objects: dict[str, str] = dict(objects or {})
        self.ops: list[str] = []  # ordered op log: "GET <key>" / "PUT <key>" / "DELETE <key>"

    def sync_down(self, local_dir: str, *, excludes: tuple[str, ...] = ()) -> list[str]:
        root = Path(local_dir)
        fetched: list[str] = []
        for key, content in sorted(self.objects.items()):
            if _is_excluded(key, excludes):
                continue
            dest = root / key
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
            self.ops.append(f"GET {key}")
            fetched.append(key)
        return fetched

    def sync_up(self, local_dir: str, *, excludes: tuple[str, ...] = ()) -> list[str]:
        root = Path(local_dir)
        local: dict[str, str] = {}
        for f in sorted(root.rglob("*")):
            if not f.is_file() or ".git/" in f.relative_to(root).as_posix() + "/":
                continue
            rel = f.relative_to(root).as_posix()
            if _is_excluded(rel, excludes):
                continue
            local[rel] = f.read_text()
        put: list[str] = []
        for key, content in local.items():
            self.objects[key] = content
            self.ops.append(f"PUT {key}")
            put.append(key)
        # `--delete`: drop store keys no longer present locally (excluded keys are left untouched).
        for key in sorted(set(self.objects) - set(local)):
            if _is_excluded(key, excludes):
                continue
            del self.objects[key]
            self.ops.append(f"DELETE {key}")
        return put


class FakeBrokeredSecret:
    """Mirrors identity's ``BrokeredSecret`` shape: value reachable ONLY via ``reveal()``; repr is
    redacted so an accidental log/f-string leaks nothing (the contract the VcsPort relies on)."""

    __slots__ = ("_value", "name", "scope", "subject")
    _REDACTED = "***REDACTED***"

    def __init__(self, value: str, *, name: str, scope: str, subject: str) -> None:
        self._value = value
        self.name = name
        self.scope = scope
        self.subject = subject

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"BrokeredSecret(name={self.name!r}, scope={self.scope!r}, value={self._REDACTED})"

    __str__ = __repr__

    def __format__(self, _spec: str) -> str:
        return self._REDACTED


class FakeSecretsBroker:
    """A FAKE identity ``SecretsPort`` — brokers a redacted, audited credential, value never logged.

    Mirrors ``PassthroughSecretsBroker``'s observable behavior (redacted secret + a metadata-only
    audit trail) so the GitHub VcsPort can be proved against the documented broker SHAPE without
    importing identity code across the boundary.
    """

    def __init__(self, store: dict[str, str] | None = None) -> None:
        self._store = dict(store or {})
        self.audit: list[tuple[str, str, str]] = []  # (subject, secret_name, scope) — never value

    def get_secret(self, subject: str, secret_name: str, *, scope: str) -> FakeBrokeredSecret:
        self.audit.append((subject, secret_name, scope))
        return FakeBrokeredSecret(
            self._store[secret_name], name=secret_name, scope=scope, subject=subject
        )


class FakeBus:
    """A fake meetings transcript egress — delivers a queued list of transcript.v1 payloads.

    Stands in for the real redis/bus the bridge subscribes; ``poll`` drains what's been published.
    No transport, no network — the bridge can't tell it from the real egress (it only sees dicts).
    """

    def __init__(self, payloads: list[dict] | None = None) -> None:
        self._queue: list[dict] = list(payloads or [])

    def publish(self, payload: dict) -> None:
        self._queue.append(payload)

    def poll(self) -> list[dict]:
        drained, self._queue = self._queue, []
        return drained
