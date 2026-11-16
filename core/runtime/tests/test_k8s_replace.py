"""A chat's next turn reuses its workload id, so its Pod name too (k8s Pod names are deterministic).
The previous turn's Pod may still be there: finished and ours → removed and started again; still
running and ours → that IS the workload, no duplicate; not ours → never touched. And a finished Pod
is removed once its exit is observed, so they do not pile up. The API server is faked
(k8s_fake.FakeApi): a tiny Pod table, so create/get/delete answer as the real one does."""
from __future__ import annotations

import pytest
from k8s_fake import FakeApi

from runtime_kernel import k8s_backend
from runtime_kernel.k8s_backend import K8sBackend
from runtime_kernel.profiles import Runnable


@pytest.fixture
def cluster(monkeypatch):
    c = FakeApi().install(monkeypatch)
    monkeypatch.setattr(k8s_backend.time, "sleep", lambda s: None)
    return c


RUN = Runnable(image="img")


def _first_turn(cluster, backend, phase):
    h = backend.start("agent-7-chat-main", RUN, {})
    cluster.pods[h._impl]["status"] = {"phase": phase}
    return h


def test_a_finished_pod_of_ours_is_replaced(cluster):
    backend = K8sBackend(instance="rel")
    h = _first_turn(cluster, backend, "Succeeded")
    h2 = backend.start("agent-7-chat-main", RUN, {"LOG_LEVEL": "2"})
    assert h2._impl == h._impl
    pod = cluster.pods[h._impl]
    assert pod["status"] == {"phase": "Pending"}                     # a new Pod, this turn's
    assert {"name": "LOG_LEVEL", "value": "2"} in pod["spec"]["containers"][0]["env"]
    assert [c[0] for c in cluster.calls].count("DELETE") == 1


def test_a_running_pod_of_ours_is_the_workload(cluster):
    backend = K8sBackend(instance="rel")
    h = _first_turn(cluster, backend, "Running")
    before = cluster.pods[h._impl]
    h2 = backend.start("agent-7-chat-main", RUN, {"LOG_LEVEL": "2"})
    assert h2._impl == h._impl and cluster.pods[h._impl] is before
    assert "DELETE" not in [c[0] for c in cluster.calls]


@pytest.mark.parametrize("change", [
    lambda m: m["labels"].pop("runtime.managed"),                       # not a managed Pod
    lambda m: m["annotations"].update({"runtime.workload_id": "agent-8-chat-main"}),  # another workload
    lambda m: m["labels"].update({"runtime.instance": "other-release"}),  # another release's
])
@pytest.mark.parametrize("phase", ["Succeeded", "Running"])
def test_a_pod_that_is_not_ours_is_never_touched(cluster, change, phase):
    backend = K8sBackend(instance="rel")
    h = _first_turn(cluster, backend, phase)
    change(cluster.pods[h._impl]["metadata"])
    with pytest.raises(RuntimeError, match="not replacing"):
        backend.start("agent-7-chat-main", RUN, {})
    assert h._impl in cluster.pods and "DELETE" not in [c[0] for c in cluster.calls]


def test_a_finished_pod_is_removed_once_its_exit_is_observed(cluster):
    backend = K8sBackend(instance="rel")
    h = backend.start("mtg-1", RUN, {})
    assert backend.exit_code(h) is None                 # still pending: nothing removed
    cluster.pods[h._impl]["status"] = {"phase": "Failed", "containerStatuses": [
        {"state": {"terminated": {"exitCode": 3}}}]}
    assert backend.exit_code(h) == 3
    assert h._impl not in cluster.pods                  # removed after the exit was read
    assert backend.exit_code(h) == 3                    # later polls keep the recorded code
    backend.start("mtg-1", RUN, {})                     # and the name is free for the next run
    assert backend.exit_code(h) is None
