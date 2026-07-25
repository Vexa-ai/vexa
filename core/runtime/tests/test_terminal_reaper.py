"""#934 — terminal workload objects are reclaimed by the runtime, without a caller DELETE.

The meeting bot owns its graceful exit: it flushes recording state, posts the terminal lifecycle
callback, and then exits.  A bare Kubernetes Pod does not disappear merely because its process
finished, so the runtime must observe that terminal process and reclaim the substrate object on a
bounded production tick.  These tests keep the oracle below Kubernetes: the backend is deterministic
and records exactly which handles the runtime tried to reclaim.
"""
from __future__ import annotations

import json
import subprocess
import threading

from runtime_kernel import Runtime, RuntimeState, WorkloadSpec
from runtime_kernel.backend import WorkloadHandle
from runtime_kernel.__main__ import _start_terminal_reaper
from runtime_kernel.k8s_backend import K8sBackend
from runtime_kernel.profiles import Runnable
import runtime_kernel.k8s_backend as k8s_backend


class _TerminalReapBackend:
    name = "k8s"
    reaps_terminal_workloads = True

    def __init__(self) -> None:
        self.exit_codes: dict[str, int | None] = {}
        self.cleaned: list[str] = []
        self.fail_cleanup = False
        self.before_cleanup = lambda: None

    def start(self, workload_id, runnable, env):
        self.exit_codes[workload_id] = None
        return WorkloadHandle(id=workload_id, impl=f"vexa-{workload_id}")

    def exit_code(self, handle):
        return self.exit_codes[handle.id]

    def terminate(self, handle):
        self.exit_codes[handle.id] = 0

    def kill(self, handle):
        self.exit_codes[handle.id] = 137

    def cleanup(self, handle):
        self.before_cleanup()
        if self.fail_cleanup:
            raise RuntimeError("delete not confirmed")
        self.cleaned.append(handle.id)

    def terminal_reap_snapshot(self):
        return dict(self.exit_codes)


def _runtime():
    backend = _TerminalReapBackend()
    runtime = Runtime(backend=backend, profiles={"bot": ["run"]})
    runtime.create(WorkloadSpec(workloadId="mtg-1", profile="bot", env={}))
    return runtime, backend


def test_terminal_reap_reflects_exit_then_reclaims_exactly_once():
    runtime, backend = _runtime()
    backend.exit_codes["mtg-1"] = 0

    assert runtime.reap_terminal() == ["mtg-1"]
    assert runtime.get("mtg-1").state is RuntimeState.stopped
    assert backend.cleaned == ["mtg-1"]

    assert runtime.reap_terminal() == []
    assert backend.cleaned == ["mtg-1"]


def test_terminal_state_alone_never_deletes_a_still_live_substrate():
    runtime, backend = _runtime()
    record = runtime.store.get("mtg-1")
    assert record is not None
    record.status.state = RuntimeState.stopped
    runtime.store.set(record)
    backend.exit_codes["mtg-1"] = None

    assert runtime.reap_terminal() == []
    assert backend.cleaned == []


def test_terminal_reap_never_deletes_a_running_workload():
    runtime, backend = _runtime()

    assert runtime.reap_terminal() == []
    assert runtime.get("mtg-1").state is RuntimeState.running
    assert backend.cleaned == []


def test_unconfirmed_terminal_delete_is_retried_without_losing_the_handle():
    runtime, backend = _runtime()
    backend.exit_codes["mtg-1"] = 0
    backend.fail_cleanup = True

    assert runtime.reap_terminal() == []
    assert runtime.get("mtg-1").state is RuntimeState.stopped
    assert backend.cleaned == []

    backend.fail_cleanup = False
    assert runtime.reap_terminal() == ["mtg-1"]
    assert backend.cleaned == ["mtg-1"]


def test_stopped_state_and_event_are_visible_before_destructive_cleanup():
    events = []
    backend = _TerminalReapBackend()
    runtime = Runtime(
        backend=backend, profiles={"bot": ["run"]}, on_event=events.append,
    )
    runtime.create(WorkloadSpec(workloadId="mtg-1", profile="bot", env={}))
    backend.exit_codes["mtg-1"] = 0
    observed = []
    backend.before_cleanup = lambda: observed.append((
        runtime.store.get("mtg-1").status.state,
        [event.state for event in events],
    ))

    assert runtime.reap_terminal() == ["mtg-1"]
    assert observed == [(
        RuntimeState.stopped,
        [RuntimeState.starting, RuntimeState.running, RuntimeState.stopped],
    )]


def test_backends_without_terminal_reap_policy_are_untouched():
    runtime, backend = _runtime()
    backend.reaps_terminal_workloads = False
    backend.exit_codes["mtg-1"] = 0

    assert runtime.reap_terminal() == []
    assert runtime.get("mtg-1").state is RuntimeState.stopped
    assert backend.cleaned == []


def test_production_reaper_is_wired_only_for_opted_in_backend(monkeypatch):
    started = []

    class _Thread:
        def __init__(self, *, target, name, daemon):
            started.append((target, name, daemon))

        def start(self):
            return None

    monkeypatch.setattr("runtime_kernel.__main__.threading.Thread", _Thread)
    runtime, _ = _runtime()
    _start_terminal_reaper(runtime)
    assert [(name, daemon) for _, name, daemon in started] == [
        ("terminal-workload-reap", True),
    ]

    runtime.backend.reaps_terminal_workloads = False
    _start_terminal_reaper(runtime)
    assert len(started) == 1


def test_k8s_observation_error_never_becomes_a_completed_exit(monkeypatch):
    class _Result:
        returncode = 1
        stdout = ""
        stderr = "Error from server (Forbidden): pods is forbidden"

    monkeypatch.setattr(k8s_backend, "_kubectl", lambda *args, **kwargs: _Result())
    backend = K8sBackend(namespace="task-ns")
    handle = WorkloadHandle(id="mtg-1", impl="vexa-mtg-1")

    try:
        backend.exit_code(handle)
        assert False, "a transient/RBAC observation error must stay unknown, not become exit 0"
    except RuntimeError as exc:
        assert "pods is forbidden" in str(exc)


def test_k8s_true_not_found_is_a_terminal_absence(monkeypatch):
    calls = []

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_kubectl(*args, **kwargs):
        calls.append((list(args), kwargs))
        return _Result()

    monkeypatch.setattr(k8s_backend, "_kubectl", fake_kubectl)
    backend = K8sBackend(namespace="task-ns")

    assert backend.exit_code(WorkloadHandle(id="mtg-1", impl="vexa-mtg-1")) == 0
    assert calls == [([
        "get", "pod", "vexa-mtg-1", "--ignore-not-found", "--request-timeout=3s",
        "-o", "json", "-n", "task-ns",
    ], {"check": False, "timeout": 4})]


def test_k8s_transient_get_error_neither_transitions_nor_deletes(monkeypatch):
    calls = []

    class _Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_kubectl(*args, **kwargs):
        calls.append(list(args))
        if args[0] == "run":
            return _Result()
        if args[:2] == ("get", "pods"):
            return _Result(returncode=1, stderr="dial tcp: API timeout")
        raise AssertionError(f"destructive call reached after failed observation: {args}")

    monkeypatch.setattr(k8s_backend, "_kubectl", fake_kubectl)
    runtime = Runtime(
        backend=K8sBackend(namespace="task-ns"),
        profiles={"bot": Runnable(image="bot:exact")},
    )
    runtime.create(WorkloadSpec(workloadId="mtg-1", profile="bot", env={}))

    assert runtime.reap_terminal() == []
    record = runtime.store.get("mtg-1")
    assert record is not None and record.status.state is RuntimeState.running
    assert not any(call[0] == "delete" for call in calls)


def test_k8s_hung_observation_is_unknown_and_next_tick_recovers(monkeypatch):
    calls = []
    list_attempts = 0

    class _Result:
        def __init__(self, stdout=""):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    terminal_pod = json.dumps({
        "status": {
            "phase": "Succeeded",
            "containerStatuses": [{"state": {"terminated": {"exitCode": 0}}}],
        },
    })

    def fake_kubectl(*args, **kwargs):
        nonlocal list_attempts
        calls.append(list(args))
        if args[0] == "run":
            return _Result()
        if args[:2] == ("get", "pods"):
            list_attempts += 1
            if list_attempts == 1:
                raise subprocess.TimeoutExpired(["kubectl", *args], timeout=4)
            return _Result(stdout=json.dumps({
                "items": [{
                    "metadata": {
                        "labels": {
                            "runtime.managed": "true",
                            "runtime.workload_id": "mtg-1",
                        },
                    },
                    **json.loads(terminal_pod),
                }],
            }))
        if args[:2] == ("get", "pod"):
            return _Result(stdout=terminal_pod)
        if args[:2] == ("delete", "pod"):
            return _Result()
        raise AssertionError(args)

    monkeypatch.setattr(k8s_backend, "_kubectl", fake_kubectl)
    runtime = Runtime(
        backend=K8sBackend(namespace="task-ns"),
        profiles={"bot": Runnable(image="bot:exact")},
    )
    runtime.create(WorkloadSpec(workloadId="mtg-1", profile="bot", env={}))

    assert runtime.reap_terminal() == []
    assert runtime.store.get("mtg-1").status.state is RuntimeState.running
    assert not any(call[0] == "delete" for call in calls)

    assert runtime.reap_terminal() == ["mtg-1"]
    assert runtime.store.get("mtg-1").status.state is RuntimeState.stopped
    assert sum(call[0] == "delete" for call in calls) == 1


class _PausedCleanupBackend:
    name = "k8s"
    reaps_terminal_workloads = True

    def __init__(self) -> None:
        self.starts = 0
        self.exit_codes = {}
        self.cleanup_entered = threading.Event()
        self.release_cleanup = threading.Event()
        self.cleaned = []
        self.current = {}

    def start(self, workload_id, runnable, env):
        self.starts += 1
        handle = WorkloadHandle(id=workload_id, impl=f"{workload_id}-generation-{self.starts}")
        self.exit_codes[handle._impl] = None
        self.current[workload_id] = handle
        return handle

    def exit_code(self, handle):
        return self.exit_codes[handle._impl]

    def terminate(self, handle):
        self.exit_codes[handle._impl] = 0

    def kill(self, handle):
        self.exit_codes[handle._impl] = 137

    def cleanup(self, handle):
        self.cleanup_entered.set()
        assert self.release_cleanup.wait(timeout=2), "test did not release paused cleanup"
        self.cleaned.append(handle._impl)

    def terminal_reap_snapshot(self):
        return {
            workload_id: self.exit_codes[handle._impl]
            for workload_id, handle in self.current.items()
        }

    def find(self, workload_id):
        return None


def test_same_id_respawn_cannot_aba_replace_handle_during_terminal_cleanup():
    backend = _PausedCleanupBackend()
    runtime = Runtime(backend=backend, profiles={"bot": ["run"]})
    spec = WorkloadSpec(workloadId="mtg-1", profile="bot", env={})
    runtime.create(spec)
    old_handle = runtime._handles["mtg-1"]
    backend.exit_codes[old_handle._impl] = 0

    reap_result = []
    reap_thread = threading.Thread(target=lambda: reap_result.extend(runtime.reap_terminal()))
    reap_thread.start()
    assert backend.cleanup_entered.wait(timeout=1)

    respawn_done = threading.Event()

    def respawn():
        runtime.create(spec)
        respawn_done.set()

    respawn_thread = threading.Thread(target=respawn)
    respawn_thread.start()
    assert not respawn_done.wait(timeout=0.05), "respawn crossed terminal cleanup lock"

    backend.release_cleanup.set()
    reap_thread.join(timeout=2)
    respawn_thread.join(timeout=2)
    assert not reap_thread.is_alive() and not respawn_thread.is_alive()
    assert reap_result == ["mtg-1"]
    assert respawn_done.is_set()

    new_handle = runtime._handles["mtg-1"]
    assert new_handle is not old_handle
    assert backend.exit_code(new_handle) is None
    assert runtime.store.get("mtg-1").status.state is RuntimeState.running
    assert backend.cleaned == [old_handle._impl]


def test_concurrent_destroy_and_reap_cleanup_exactly_once():
    backend = _PausedCleanupBackend()
    runtime = Runtime(backend=backend, profiles={"bot": ["run"]})
    spec = WorkloadSpec(workloadId="mtg-1", profile="bot", env={})
    runtime.create(spec)
    handle = runtime._handles["mtg-1"]
    backend.exit_codes[handle._impl] = 0

    reap_thread = threading.Thread(target=runtime.reap_terminal)
    reap_thread.start()
    assert backend.cleanup_entered.wait(timeout=1)

    destroy_done = threading.Event()

    def destroy():
        runtime.destroy("mtg-1")
        destroy_done.set()

    destroy_thread = threading.Thread(target=destroy)
    destroy_thread.start()
    assert not destroy_done.wait(timeout=0.05), "destroy crossed terminal cleanup lock"

    backend.release_cleanup.set()
    reap_thread.join(timeout=2)
    destroy_thread.join(timeout=2)
    assert not reap_thread.is_alive() and not destroy_thread.is_alive()
    assert destroy_done.is_set()
    assert backend.cleaned == [handle._impl]
    assert runtime.store.get("mtg-1").status.state is RuntimeState.destroyed
    assert "mtg-1" not in runtime._handles


def test_batch_discovery_does_not_poll_live_workloads_ahead_of_terminal_candidate(monkeypatch):
    calls = []
    workload_ids = ["live-1", "live-2", "live-3", "terminal-1"]
    terminal_status = {
        "phase": "Succeeded",
        "containerStatuses": [{"state": {"terminated": {"exitCode": 0}}}],
    }

    class _Result:
        def __init__(self, stdout=""):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_kubectl(*args, **kwargs):
        calls.append(list(args))
        if args[0] == "run":
            return _Result()
        if args[:2] == ("get", "pods"):
            items = []
            for workload_id in workload_ids:
                items.append({
                    "metadata": {
                        "labels": {
                            "runtime.managed": "true",
                            "runtime.workload_id": workload_id,
                        },
                    },
                    "status": (
                        terminal_status if workload_id == "terminal-1" else {"phase": "Running"}
                    ),
                })
            return _Result(json.dumps({"items": items}))
        if args[:2] == ("get", "pod"):
            assert args[2] == "vexa-terminal-1", (
                f"live workload received per-id GET instead of batch-only observation: {args}"
            )
            return _Result(json.dumps({"status": terminal_status}))
        if args[:2] == ("delete", "pod"):
            assert args[2] == "vexa-terminal-1"
            return _Result()
        raise AssertionError(args)

    monkeypatch.setattr(k8s_backend, "_kubectl", fake_kubectl)
    runtime = Runtime(
        backend=K8sBackend(namespace="task-ns"),
        profiles={"bot": Runnable(image="bot:exact")},
    )
    for workload_id in workload_ids:
        runtime.create(WorkloadSpec(workloadId=workload_id, profile="bot", env={}))

    assert runtime.reap_terminal() == ["terminal-1"]
    assert runtime.store.get("terminal-1").status.state is RuntimeState.stopped
    for workload_id in workload_ids[:-1]:
        assert runtime.store.get(workload_id).status.state is RuntimeState.running

    batch_calls = [call for call in calls if call[:2] == ["get", "pods"]]
    per_id_calls = [call for call in calls if call[:2] == ["get", "pod"]]
    delete_calls = [call for call in calls if call[:2] == ["delete", "pod"]]
    assert len(batch_calls) == 1
    assert [call[2] for call in per_id_calls] == ["vexa-terminal-1"]
    assert [call[2] for call in delete_calls] == ["vexa-terminal-1"]
