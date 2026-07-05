"""ProcessBackend — runs a workload as a child process (single-host / no Docker). The leanest real
backend; satisfies the runtime.v1 lifecycle. (docker/k8s backends are ported from 0.11 when needed.)

Output capture: each workload's stdout+stderr goes to a per-workload log file under
``PROCESS_LOG_DIR`` (default ``<tempdir>/vexa-workloads``) — the process analog of ``docker logs``.
A workload that exits nonzero gets its log tail surfaced at ERROR level through the runtime's own
logs the first time the exit is observed, so a crashed worker (e.g. an ImportError at startup) is
diagnosable from the runtime service logs instead of vanishing into /dev/null."""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import Optional

from .backend import WorkloadHandle
from .profiles import Runnable

log = logging.getLogger("runtime_kernel.process")

# How much of a failed workload's log lands in the runtime log line (the full file stays on disk).
_TAIL_BYTES = 4096


def _log_dir() -> str:
    return os.environ.get("PROCESS_LOG_DIR") or os.path.join(tempfile.gettempdir(), "vexa-workloads")


def _tail(path: str, limit: int = _TAIL_BYTES) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


class ProcessBackend:
    name = "process"

    def __init__(self) -> None:
        # Per-workload capture state: workloadId → (log path | None, failure already reported?).
        # Process-local, like the kernel's handle map — absent after a restart, which is fine:
        # exit codes are unobservable without a live handle anyway.
        self._capture: dict[str, dict] = {}

    def start(self, workload_id: str, runnable: Runnable, env: dict[str, str]) -> WorkloadHandle:
        if not runnable.command:
            raise ValueError("process backend requires a command")
        # Capture the child's output to a per-workload file (both streams interleaved, like
        # `docker logs`). Fail-open: if the log dir is unwritable we fall back to DEVNULL rather
        # than refusing to start the workload.
        log_path: Optional[str] = None
        out_fh = None
        try:
            log_dir = _log_dir()
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, f"{workload_id}.log")
            out_fh = open(log_path, "ab")
        except OSError as e:
            log.warning("workload %s: cannot capture output (%s) — falling back to DEVNULL", workload_id, e)
            log_path = None
        try:
            proc = subprocess.Popen(
                runnable.command,
                env={**os.environ, **env},
                stdout=out_fh if out_fh is not None else subprocess.DEVNULL,
                stderr=subprocess.STDOUT if out_fh is not None else subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            if out_fh is not None:
                out_fh.close()  # the child holds its own fd; ours would only leak
        self._capture[workload_id] = {"log_path": log_path, "reported": False}
        return WorkloadHandle(id=workload_id, impl=proc)

    def exit_code(self, h: WorkloadHandle) -> Optional[int]:
        code = h._impl.poll()  # type: ignore[attr-defined]
        if code is not None and code != 0:
            self._report_failure(h.id, code)
        return code

    def _report_failure(self, workload_id: str, code: int) -> None:
        """Log the failed workload's output tail — once per workload (exit_code is polled)."""
        state = self._capture.get(workload_id)
        if state is None or state["reported"]:
            return
        state["reported"] = True
        log_path = state["log_path"]
        tail = _tail(log_path) if log_path else ""
        log.error(
            "workload %s exited %d — output tail (full log: %s):\n%s",
            workload_id, code, log_path or "not captured", tail or "<no output captured>",
        )

    def _suppress_report(self, workload_id: str) -> None:
        """A backend-initiated stop makes the nonzero (signal) exit expected — not an error to tail."""
        state = self._capture.get(workload_id)
        if state is not None:
            state["reported"] = True

    def terminate(self, h: WorkloadHandle) -> None:
        if h._impl.poll() is None:  # type: ignore[attr-defined]
            self._suppress_report(h.id)
            h._impl.terminate()  # type: ignore[attr-defined]

    def kill(self, h: WorkloadHandle) -> None:
        if h._impl.poll() is None:  # type: ignore[attr-defined]
            self._suppress_report(h.id)
            h._impl.kill()  # type: ignore[attr-defined]

    def cleanup(self, h: WorkloadHandle) -> None:
        self.kill(h)
        try:
            h._impl.wait(timeout=2)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._capture.pop(h.id, None)
