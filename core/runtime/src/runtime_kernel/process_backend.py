"""ProcessBackend — runs a workload as a child process (single-host / no Docker). The leanest real
backend; satisfies the runtime.v1 lifecycle. (docker/k8s backends are ported from 0.11 when needed.)"""
from __future__ import annotations

import os
import subprocess
from typing import Optional

from .backend import WorkloadHandle
from .profiles import Runnable


class ProcessBackend:
    name = "process"

    def start(self, workload_id: str, runnable: Runnable, env: dict[str, str]) -> WorkloadHandle:
        if not runnable.command:
            raise ValueError("process backend requires a command")
        proc = subprocess.Popen(
            runnable.command,
            env={**os.environ, **env},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return WorkloadHandle(id=workload_id, impl=proc)

    def exit_code(self, h: WorkloadHandle) -> Optional[int]:
        return h._impl.poll()  # type: ignore[attr-defined]

    def terminate(self, h: WorkloadHandle) -> None:
        if h._impl.poll() is None:  # type: ignore[attr-defined]
            h._impl.terminate()  # type: ignore[attr-defined]

    def kill(self, h: WorkloadHandle) -> None:
        if h._impl.poll() is None:  # type: ignore[attr-defined]
            h._impl.kill()  # type: ignore[attr-defined]

    def cleanup(self, h: WorkloadHandle) -> None:
        self.kill(h)
        try:
            h._impl.wait(timeout=2)  # type: ignore[attr-defined]
        except Exception:
            pass
