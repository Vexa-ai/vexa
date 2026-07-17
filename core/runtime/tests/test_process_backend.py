"""ProcessBackend output capture — the process analog of `docker logs` (release-eyeball fix).

The original backend spawned workloads with stdout/stderr=DEVNULL, so a worker that died at startup
(lite's agent launcher hitting `ModuleNotFoundError: No module named 'llm'`) was undiagnosable BY
DESIGN: the terminal showed only "No chat output arrived before the stream closed". These tests pin
the fix: every workload's output lands in a per-workload log file (PROCESS_LOG_DIR, default
<tempdir>/vexa-workloads), and a nonzero self-exit surfaces the tail at ERROR level through the
runtime's own logs — exactly once, and never for a backend-initiated stop (SIGTERM/SIGKILL is an
expected nonzero, not a crash)."""
from __future__ import annotations

import logging
import os
import sys
import tempfile
import time

import pytest

from runtime_kernel.process_backend import ProcessBackend, _log_dir
from runtime_kernel.profiles import Runnable


def _py(code: str) -> Runnable:
    return Runnable(command=[sys.executable, "-c", code])


def _sh(script: str) -> Runnable:
    return Runnable(command=["sh", "-c", script])


def _alive(pid: int) -> bool:
    """True while `pid` exists (signal 0 probes without delivering)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal


def _wait_dead(pids, timeout: float = 5.0) -> None:
    """Poll until every pid is gone (a SIGKILL'd child is reaped asynchronously)."""
    deadline = time.time() + timeout
    while time.time() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.02)


def _read_pids(*paths) -> list[int]:
    return [int(p.read_text().strip()) for p in paths]


def _start_and_wait(backend: ProcessBackend, workload_id: str, runnable: Runnable):
    h = backend.start(workload_id, runnable, {})
    h._impl.wait(timeout=10)
    return h


# ── the log dir seam ──────────────────────────────────────────────────────────────────────────────
def test_log_dir_defaults_under_tempdir(monkeypatch):
    monkeypatch.delenv("PROCESS_LOG_DIR", raising=False)
    assert _log_dir() == os.path.join(tempfile.gettempdir(), "vexa-workloads")


def test_log_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PROCESS_LOG_DIR", str(tmp_path / "wl"))
    assert _log_dir() == str(tmp_path / "wl")


# ── capture + failure tail ────────────────────────────────────────────────────────────────────────
def test_failed_spawn_output_lands_in_log_file_and_error_tail(monkeypatch, tmp_path, caplog):
    """The release-blocker shape: a worker that prints and dies nonzero. Its stdout AND stderr must
    be on disk, and the tail must reach the runtime logger at ERROR."""
    monkeypatch.setenv("PROCESS_LOG_DIR", str(tmp_path))
    backend = ProcessBackend()
    h = _start_and_wait(
        backend, "w-crash",
        _py("import sys; print('boom-stdout'); sys.stderr.write('boom-stderr\\n'); sys.exit(3)"),
    )
    with caplog.at_level(logging.ERROR, logger="runtime_kernel.process"):
        assert backend.exit_code(h) == 3

    text = (tmp_path / "w-crash.log").read_text()
    assert "boom-stdout" in text and "boom-stderr" in text  # both streams, interleaved

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    msg = errors[0].getMessage()
    assert "w-crash" in msg and "exited 3" in msg
    assert "boom-stderr" in msg                              # the tail itself
    assert str(tmp_path / "w-crash.log") in msg              # where the full log lives


def test_failure_tail_logged_once(monkeypatch, tmp_path, caplog):
    """exit_code is POLLED (kernel.get / stop loop) — the tail must not repeat per poll."""
    monkeypatch.setenv("PROCESS_LOG_DIR", str(tmp_path))
    backend = ProcessBackend()
    h = _start_and_wait(backend, "w-poll", _py("raise SystemExit(1)"))
    with caplog.at_level(logging.ERROR, logger="runtime_kernel.process"):
        for _ in range(3):
            assert backend.exit_code(h) == 1
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


def test_clean_exit_captures_output_without_error(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("PROCESS_LOG_DIR", str(tmp_path))
    backend = ProcessBackend()
    h = _start_and_wait(backend, "w-ok", _py("print('all fine')"))
    with caplog.at_level(logging.WARNING, logger="runtime_kernel.process"):
        assert backend.exit_code(h) == 0
    assert "all fine" in (tmp_path / "w-ok.log").read_text()  # captured even on success
    assert not caplog.records


def test_backend_initiated_stop_is_not_reported_as_failure(monkeypatch, tmp_path, caplog):
    """kernel.stop() terminates/kills — the resulting signal exit is EXPECTED, not a crash tail."""
    monkeypatch.setenv("PROCESS_LOG_DIR", str(tmp_path))
    backend = ProcessBackend()
    h = backend.start("w-stop", _py("import time; time.sleep(30)"), {})
    with caplog.at_level(logging.ERROR, logger="runtime_kernel.process"):
        backend.terminate(h)
        h._impl.wait(timeout=10)
        code = backend.exit_code(h)
    assert code is not None and code != 0
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]
    backend.cleanup(h)


def test_unwritable_log_dir_falls_back_to_devnull(monkeypatch, tmp_path, caplog):
    """Capture is fail-open: an unusable PROCESS_LOG_DIR must not stop workloads from starting."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("file, not dir")
    monkeypatch.setenv("PROCESS_LOG_DIR", str(blocker / "sub"))
    backend = ProcessBackend()
    with caplog.at_level(logging.WARNING, logger="runtime_kernel.process"):
        h = _start_and_wait(backend, "w-nolog", _py("raise SystemExit(2)"))
        assert backend.exit_code(h) == 2
    assert any("cannot capture output" in r.getMessage() for r in caplog.records)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "not captured" in errors[0].getMessage()


# ── group-scoped teardown (#546) ────────────────────────────────────────────────────────────────
# Each workload is spawned as its own process-group leader (start_new_session=True). Every path that
# ends a workload must reap the whole GROUP, not just the leader — otherwise a self-exiting or stopped
# bot orphans every child it spawned onto the shared Lite host. These fixtures are #486's verbatim,
# with pid capture so any survivor is arithmetic (RED on base a6ef9e23, GREEN after the fix).

def test_self_exit_reaps_whole_group(monkeypatch, tmp_path, caplog):
    """A1 (V1) — a workload that exits on its own leaves NO children. The leader forks two long
    sleeps and exits 1; once exit_code() first observes the exit (plus cleanup), both captured child
    pids are dead. On base (leader-only signal) both sleeps survive — RED."""
    monkeypatch.setenv("PROCESS_LOG_DIR", str(tmp_path))
    f1, f2 = tmp_path / "c1.pid", tmp_path / "c2.pid"
    backend = ProcessBackend()
    h = backend.start(
        "w-orphan",
        _sh(f"sleep 600 & echo $! > {f1}; sleep 600 & echo $! > {f2}; exit 1"),
        {},
    )
    h._impl.wait(timeout=10)                     # leader gone; children reparented to PID 1
    while not (f1.exists() and f2.exists()):
        time.sleep(0.01)
    child_pids = _read_pids(f1, f2)
    assert all(_alive(p) for p in child_pids)    # precondition: children outlived the leader

    with caplog.at_level(logging.ERROR, logger="runtime_kernel.process"):
        assert backend.exit_code(h) == 1         # first observation triggers the group sweep
    backend.cleanup(h)

    _wait_dead(child_pids)
    assert not any(_alive(p) for p in child_pids), f"orphaned children survived: {child_pids}"


def test_stop_path_reaps_whole_group(monkeypatch, tmp_path):
    """A2 (V2) — the kernel.stop() shape (terminate → grace → kill) tears down the whole tree, not
    just the leader. The leader waits on its children (never self-exits), so only a group signal can
    end them. On base (leader-only terminate/kill) both sleeps survive — RED."""
    monkeypatch.setenv("PROCESS_LOG_DIR", str(tmp_path))
    f1, f2 = tmp_path / "s1.pid", tmp_path / "s2.pid"
    backend = ProcessBackend()
    h = backend.start(
        "w-stopgrp",
        _sh(f"sleep 600 & echo $! > {f1}; sleep 600 & echo $! > {f2}; wait"),
        {},
    )
    while not (f1.exists() and f2.exists()):
        time.sleep(0.01)
    child_pids = _read_pids(f1, f2)
    assert all(_alive(p) for p in child_pids)

    # mirror kernel.stop(): graceful terminate, brief grace, then force kill()
    backend.terminate(h)
    deadline = time.time() + 1.0
    while backend.exit_code(h) is None and time.time() < deadline:
        time.sleep(0.02)
    if backend.exit_code(h) is None:
        backend.kill(h)
    backend.cleanup(h)

    _wait_dead(child_pids)
    assert not any(_alive(p) for p in child_pids), f"children survived stop: {child_pids}"


def test_childless_workload_reap_is_silent(monkeypatch, tmp_path, caplog):
    """Edge: a workload with no children (the common, well-behaved case). The group sweep finds an
    empty/gone group (ProcessLookupError) and stays silent — no error log, no crash."""
    monkeypatch.setenv("PROCESS_LOG_DIR", str(tmp_path))
    backend = ProcessBackend()
    h = _start_and_wait(backend, "w-lonely", _py("import sys; sys.exit(0)"))
    with caplog.at_level(logging.ERROR, logger="runtime_kernel.process"):
        assert backend.exit_code(h) == 0
        backend.cleanup(h)
    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


def test_workload_env_still_layered_over_process_env(monkeypatch, tmp_path):
    """The capture change must not disturb the env contract: spec env wins over os.environ."""
    monkeypatch.setenv("PROCESS_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MARKER", "from-os")
    backend = ProcessBackend()
    echo = Runnable(command=[sys.executable, "-c", "import os; print(os.environ['MARKER'])"])
    h = backend.start("w-env", echo, {"MARKER": "from-spec"})
    h._impl.wait(timeout=10)
    assert backend.exit_code(h) == 0
    assert "from-spec" in (tmp_path / "w-env.log").read_text()
