"""DockerBackend — runs a workload as a real Docker container (the bot's actual substrate). Uses the
docker CLI via subprocess (no docker-py dependency), matching the proven 0.11 approach. Implements the
same Backend port as ProcessBackend, so the kernel's lifecycle is identical regardless of substrate."""
from __future__ import annotations

import subprocess
from typing import Optional

from .backend import WorkloadHandle
from .profiles import Runnable


def _docker(*args: str, check: bool = True) -> str:
    r = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed: {r.stderr.strip()}")
    return (r.stdout or "").strip()


class DockerBackend:
    name = "docker"

    def __init__(self, name_prefix: str = "vexa-") -> None:
        self._prefix = name_prefix

    def _cname(self, workload_id: str) -> str:
        return f"{self._prefix}{workload_id}"

    def start(self, workload_id: str, runnable: Runnable, env: dict[str, str]) -> WorkloadHandle:
        if not runnable.image:
            raise ValueError("docker backend requires an image")
        name = self._cname(workload_id)
        args = ["run", "-d", "--name", name]
        for k, v in env.items():
            args += ["-e", f"{k}={v}"]
        args.append(runnable.image)
        if runnable.command:
            args += runnable.command
        _docker(*args)
        return WorkloadHandle(id=workload_id, impl=name)

    def exit_code(self, h: WorkloadHandle) -> Optional[int]:
        running = _docker("inspect", "-f", "{{.State.Running}}", h._impl, check=False)  # type: ignore[attr-defined]
        if running != "false":            # "true", or "" if already gone (treat as still-resolving → None)
            return None
        try:
            return int(_docker("inspect", "-f", "{{.State.ExitCode}}", h._impl, check=False))  # type: ignore[attr-defined]
        except ValueError:
            return None

    def terminate(self, h: WorkloadHandle) -> None:
        _docker("stop", "-t", "5", h._impl, check=False)  # type: ignore[attr-defined]  # SIGTERM + grace, then SIGKILL

    def kill(self, h: WorkloadHandle) -> None:
        _docker("kill", h._impl, check=False)  # type: ignore[attr-defined]

    def cleanup(self, h: WorkloadHandle) -> None:
        _docker("rm", "-f", h._impl, check=False)  # type: ignore[attr-defined]
