"""In-memory fakes for the ports — what makes the L2 unit test possible (ARCHITECTURE.md §5).

Each fake implements a port's protocol with a dict/list, no I/O. The core can't tell them from a
real git repo or the runtime kernel, so its logic is proved offline.
"""
from __future__ import annotations

from agent_api.models import WorkspaceWrite
from agent_api.ports import RuntimePort, WorkspacePort


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
