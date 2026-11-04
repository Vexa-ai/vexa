"""A meeting bot the kernel's OOM killer ended at its memory limit is reported as ``stopReason: oom``.

runtime.v1 has carried ``oom`` in its sealed StopReason since v1, and the k8s backend never produced
it: every non-zero exit read ``failed``, so a bot OOM-killed mid-meeting (exit 137, the container's
``terminated.reason: OOMKilled``) reached meeting-api as an anonymous ``failed`` and its meeting row
as ``completed / left_alone`` — a claim that the bot was left alone in the room. The Pod's own status
names the cause; the kernel now passes it on, on the read that observes the exit and on boot
re-adoption. Every other exit is unchanged: 0 → completed, anything else → failed.
"""
from __future__ import annotations

import json

import pytest

import runtime_kernel.k8s_backend as k8s_backend
from runtime_kernel import Runtime
from runtime_kernel.k8s_backend import K8sBackend
from runtime_kernel.models import RuntimeState, StopReason, WorkloadSpec
from runtime_kernel.profiles import default_registry
from runtime_kernel.workload_env import StoreConfig


def _terminated(code: int, reason: str) -> dict:
    return {"phase": "Failed" if code else "Succeeded",
            "containerStatuses": [{"name": "c", "state": {"terminated": {"exitCode": code, "reason": reason}}}]}


class _Cluster:
    """A kubectl stand-in holding one Pod whose status the test sets."""

    def __init__(self):
        self.status: dict = {"phase": "Running"}
        self.labels = {k8s_backend.MANAGED_LABEL: "true", k8s_backend.INSTANCE_LABEL: "default"}

    def __call__(self, *args, check=True, stdin=None):
        class _R:
            returncode, stderr = 0, ""

            def __init__(self, stdout=""):
                self.stdout = stdout

        if args[:1] == ("create",):
            return _R(json.dumps({"metadata": {"uid": "pod-uid"}}))
        if args[:2] == ("get", "pod"):
            return _R(json.dumps({"metadata": {"name": args[2]}, "status": self.status}))
        if args[:2] == ("get", "pods"):
            return _R(json.dumps({"items": [{"metadata": {"name": "vexa-mtg-9", "labels": self.labels,
                                                          "annotations": {k8s_backend.WORKLOAD_ID_LABEL: "mtg-9"}},
                                             "status": self.status}]}))
        return _R("")


@pytest.fixture
def cluster(monkeypatch):
    monkeypatch.setenv("BROWSER_IMAGE", "bot:test")
    c = _Cluster()
    monkeypatch.setattr(k8s_backend, "_kubectl", c)
    return c


def _runtime(events: list) -> Runtime:
    return Runtime(backend=K8sBackend(namespace="ns"), profiles=default_registry(), grace_sec=0.1,
                   workspace_store=StoreConfig(), on_event=events.append)


@pytest.mark.parametrize("code,reason,want", [
    (137, "OOMKilled", StopReason.oom),
    (137, "Error", StopReason.failed),       # a SIGKILL that is not the OOM killer stays failed
    (1, "Error", StopReason.failed),
    (0, "Completed", StopReason.completed),
])
def test_the_read_that_observes_the_exit_names_the_oom_kill(cluster, code, reason, want):
    events: list = []
    rt = _runtime(events)
    rt.create(WorkloadSpec(workloadId="mtg-9", profile="meeting-bot", env={"VEXA_BOT_CONFIG": "{}"}))
    cluster.status = _terminated(code, reason)
    status = rt.get("mtg-9")
    assert (status.state, status.exitCode, status.stopReason) == (RuntimeState.stopped, code, want)
    stopped = [e for e in events if e.state is RuntimeState.stopped]
    assert [(e.exitCode, e.stopReason) for e in stopped] == [(code, want)]
    # observed once: a later read answers from the kernel's record, same reason
    assert rt.get("mtg-9").stopReason is want


def test_a_restarted_runtime_adopts_an_oom_killed_bot_as_oom(cluster):
    cluster.status = _terminated(137, "OOMKilled")
    rt = _runtime([])
    assert rt.adopt() == 1
    status = rt.get("mtg-9")
    assert (status.state, status.exitCode, status.stopReason) == (RuntimeState.stopped, 137, StopReason.oom)
