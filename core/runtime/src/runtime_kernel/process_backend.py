"""ProcessBackend — runs a workload as a child process (single-host / no Docker). The leanest real
backend; satisfies the runtime.v1 lifecycle. (docker/k8s backends are ported from 0.11 when needed.)

Output capture: each workload's stdout+stderr goes to a per-workload log file under
``PROCESS_LOG_DIR`` (default ``<tempdir>/vexa-workloads``) — the process analog of ``docker logs``.
A workload that exits nonzero gets its log tail surfaced at ERROR level through the runtime's own
logs the first time the exit is observed, so a crashed worker (e.g. an ImportError at startup) is
diagnosable from the runtime service logs instead of vanishing into /dev/null.

Group-scoped teardown: each workload is spawned as its own process-group leader
(``start_new_session=True`` → leader pid == pgid). Every path that ends a workload — an observed
self-exit, ``kill``/``cleanup``, and the kernel's stop sequence — signals the whole *group*
(``os.killpg``), so a workload's children (e.g. the bot's Chromium tree) are reaped with it instead
of being reparented to PID 1 and stranded on the shared host. Declared limitation: descendants that
detach into their OWN process group (the join module's debug-view x11vnc/websockify, spawned
``detached: true``) are out of any group signal's reach — a container/host restart still clears
those."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import tempfile
from typing import Optional

from .backend import WorkloadHandle
from .isolation import (
    HOMES_ROOT, ChildIdentity, IsolationRefused, StagedFile, WorkloadUids, apply_process_isolation,
    child_env_for, group_ids, make_home, open_trusted_dir, plan_process_isolation, preexec_for,
    remove_home, sweep_homes,
)
from .models import Resources
from .mounts import mount_set
from .profiles import Runnable
from .workload_env import child_environment, name_component

log = logging.getLogger("runtime_kernel.process")

# How much of a failed workload's log lands in the runtime log line (the full file stays on disk).
_TAIL_BYTES = 4096


def _log_dir() -> str:
    return os.environ.get("PROCESS_LOG_DIR") or os.path.join(tempfile.gettempdir(), "vexa-workloads")


def _open_log(workload_id: str) -> tuple[str, int]:
    """The workload's log file, opened for append. Under a root runtime the directory must be one
    only root controls (made 0700) and the file is opened without following a link, 0600, so no
    child can read another's output or point root's writes elsewhere."""
    log_dir = _log_dir()
    # One file name inside the log dir, whatever the id holds (a chat id carries the client's
    # session, which may contain `/` or `..`): name_component never yields `/` or a leading `.`.
    name = f"{name_component(workload_id)}.log"
    if os.geteuid() != 0:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, name)
        return path, os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o644)
    dir_fd = open_trusted_dir(log_dir, create_mode=0o700)
    try:
        st = os.fstat(dir_fd)
        if st.st_uid != 0:
            raise OSError(f"{log_dir} is not root's")
        if st.st_mode & 0o077:
            os.fchmod(dir_fd, 0o700)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                     dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    return os.path.join(os.path.realpath(log_dir), name), fd


def _tail(path: str, limit: int = _TAIL_BYTES) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _signal_group(pgid: int, sig: int) -> bool:
    """Signal a whole process group by its pgid, which for our workloads equals the leader pid
    (spawned ``start_new_session=True``, so the leader IS the group leader — pid == pgid, and stays
    the pgid even after the leader dies, as long as any group member lives). Returns True if the
    signal was delivered, False if the group is already gone (``ProcessLookupError`` = the common,
    well-behaved case: every member already exited — nothing to reap). A non-root runtime that
    cannot reach a per-subject-uid group degrades LOUDLY (the module's stated convention) and never
    crashes the caller."""
    try:
        os.killpg(pgid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError as e:
        log.error("workload group %d: cannot signal (%s) — orphans may survive (non-root runtime?)",
                  pgid, e)
        return False


class ProcessBackend:
    name = "process"

    def __init__(self, *, homes_root: str = HOMES_ROOT, uids: Optional[WorkloadUids] = None) -> None:
        # Per-workload capture state: workloadId → (log path | None, failure already reported?).
        # Process-local, like the kernel's handle map — absent after a restart, which is fine:
        # exit codes are unobservable without a live handle anyway.
        self._capture: dict[str, dict] = {}
        self._homes_root = homes_root
        self._uids = uids or WorkloadUids()
        if os.geteuid() == 0:
            removed = sweep_homes(homes_root=homes_root)
            if removed:
                log.info("removed %d HOME(s) left by children that are gone", removed)

    def _identity(self, workload_id: str, runnable: Runnable, env: dict[str, str]) -> ChildIdentity:
        """Who the child runs as under a ROOT runtime — never root. A workspace dispatch runs as its
        subject's uid on its isolated store; any other workload as a uid of its own with only the
        groups its profile names. Every child gets a fresh private HOME holding the profile's
        credential files. Raises when any of it cannot be done: the spawn is refused, never
        degraded to a root child."""
        plan = plan_process_isolation(env, euid=0)
        if plan is not None:
            plan = apply_process_isolation(plan)
            uid, groups = plan.uid, plan.groups
        else:
            uid, groups = self._uids.acquire(workload_id), group_ids(runnable.process_groups)
        staged = [StagedFile(source=c.source, home_path=c.home_path)
                  for c in runnable.credential_files if c.home_path]
        home, tmp = make_home(uid, uid, homes_root=self._homes_root, staged=staged)
        return ChildIdentity(uid=uid, gid=uid, groups=tuple(groups), home=home, tmp=tmp)

    def start(
        self,
        workload_id: str,
        runnable: Runnable,
        env: dict[str, str],
        resources: Optional[Resources] = None,
    ) -> WorkloadHandle:
        """``resources`` is accepted and NOT enforced: a child process has no admission gate to
        satisfy, and cgroup limits are the host's concern on this substrate. The enforcement
        boundary is k8s-only and no parity is claimed."""
        if not runnable.command:
            raise ValueError("process backend requires a command")
        # Workspace mount set (WP-A1.1): the lite/process backend shares the HOST filesystem — there is
        # nothing to bind, so tenant isolation is POSIX instead (runtime_kernel.isolation): under a
        # root runtime every child drops to a non-root uid — its subject's for a workspace dispatch,
        # its own for anything else — with a fresh private HOME. Anything that stops that refuses
        # the spawn. A non-root runtime's children run as its own uid (said loudly for a dispatch).
        mounts = mount_set(env)
        if len(mounts) > 1:
            log.info("workload %s: %d active workspace mounts: %s",
                     workload_id, len(mounts), ", ".join(m.get("slug", "?") for m in mounts))
        # The child's environment is built from scratch (workload_env.child_environment): host
        # plumbing, the profile's forward list, and the workload's own env. The runtime's own
        # environment — its caller token, and whatever service secrets the host process was started
        # with — never reaches a child.
        child_env = child_environment(env, forward=runnable.forward_env)
        preexec = None
        identity: Optional[ChildIdentity] = None
        if os.geteuid() == 0:
            try:
                identity = self._identity(workload_id, runnable, env)
                preexec = preexec_for(identity)
            except Exception as e:
                self._uids.release(workload_id)
                if identity is not None:
                    remove_home(identity.home, homes_root=self._homes_root)
                log.error("workload %s: REFUSED — isolation could not be applied (%s); a root runtime "
                          "never starts a root child", workload_id, e)
                raise IsolationRefused(f"isolation could not be applied: {e}") from e
            child_env = child_env_for(identity, child_env)
            log.info("workload %s: runs as uid %d (%d supplementary group(s))",
                     workload_id, identity.uid, len(identity.groups))
        else:
            plan_process_isolation(env)          # warns once for a workspace dispatch
        # Capture the child's output to a per-workload file (both streams interleaved, like
        # `docker logs`). Fail-open: if the log dir is unusable we fall back to DEVNULL rather
        # than refusing to start the workload.
        log_path: Optional[str] = None
        out_fd: Optional[int] = None
        try:
            log_path, out_fd = _open_log(workload_id)
        except (OSError, IsolationRefused) as e:
            log.warning("workload %s: cannot capture output (%s) — falling back to DEVNULL", workload_id, e)
            log_path = None
        try:
            proc = subprocess.Popen(
                runnable.command,
                env=child_env,
                stdout=out_fd if out_fd is not None else subprocess.DEVNULL,
                stderr=subprocess.STDOUT if out_fd is not None else subprocess.DEVNULL,
                start_new_session=True,
                preexec_fn=preexec,   # None only under a non-root runtime
            )
        except Exception:
            self._uids.release(workload_id)
            if identity is not None:
                remove_home(identity.home, homes_root=self._homes_root)
            raise
        finally:
            if out_fd is not None:
                os.close(out_fd)  # the child holds its own fd; ours would only leak
        self._capture[workload_id] = {"log_path": log_path, "reported": False, "reaped": False,
                                      "home": identity.home if identity else None}
        return WorkloadHandle(id=workload_id, impl=proc)

    def _release(self, workload_id: str) -> None:
        """Give back what the child held: its workload uid and its HOME."""
        state = self._capture.get(workload_id) or {}
        home = state.pop("home", None)
        if home:
            remove_home(home, homes_root=self._homes_root)
        self._uids.release(workload_id)

    def exit_code(self, h: WorkloadHandle) -> Optional[int]:
        code = h._impl.poll()  # type: ignore[attr-defined]
        if code is not None:
            # A workload that ended — for ANY reason, clean or not — reaps its group on first
            # observation (P22: teardown is guaranteed at the boundary, not hoped for). The leader
            # is gone, so its children are already orphaned; SIGKILL the group with no grace. The
            # one-shot guard mirrors the failure-report pattern: exit_code is polled repeatedly.
            self._reap_group_once(h)
            if code != 0:
                self._report_failure(h.id, code)
        return code

    def _reap_group_once(self, h: WorkloadHandle) -> None:
        """Sweep the workload's process group exactly once, on first observation of its exit."""
        state = self._capture.get(h.id)
        if state is None or state["reaped"]:
            return
        state["reaped"] = True
        _signal_group(h._impl.pid, signal.SIGKILL)  # type: ignore[attr-defined]
        self._release(h.id)

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
        # Leader-only SIGTERM (fork B1): the bot's graceful-leave contract runs on the leader's
        # SIGTERM handler; a group-wide SIGTERM would also hit Chromium mid-leave. The group sweep
        # rides the observed exit (exit_code) and the kill() escalation, so children never survive.
        if h._impl.poll() is None:  # type: ignore[attr-defined]
            self._suppress_report(h.id)
            h._impl.terminate()  # type: ignore[attr-defined]

    def kill(self, h: WorkloadHandle) -> None:
        # Force path: SIGKILL the whole group, not just the leader — this is what reaps a child tree
        # the leader would otherwise strand (the grace-expiry escalation of kernel.stop, and
        # cleanup). The leader is a member of its own group, so this covers it too.
        self._suppress_report(h.id)
        _signal_group(h._impl.pid, signal.SIGKILL)  # type: ignore[attr-defined]

    def cleanup(self, h: WorkloadHandle) -> None:
        self.kill(h)
        try:
            h._impl.wait(timeout=2)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._release(h.id)
        self._capture.pop(h.id, None)
