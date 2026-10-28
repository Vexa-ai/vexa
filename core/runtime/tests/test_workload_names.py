"""A workload id is the caller's; a Pod name, a container name and a label value are the substrate's.

A chat unit's id carries the client's session (`agent-<subject>-chat-scaffold-<token_urlsafe(32)>`:
capitals, `_`, 71 characters with the `vexa-` prefix), and Kubernetes refused that Pod — the name is
not DNS-1123, the label value is over 63, and so is the container name — so the onboarding chat never
started an agent. Every name the runtime submits is now valid, an id the substrate already accepts
keeps its name, two ids never share one, and adoption still recovers the id itself. The same holds
for Docker's container names and for the process backend's log file (Lite), where a session holding
`/` or `..` used to point the file outside the log directory.
"""
from __future__ import annotations

import json
import re

import pytest

import runtime_kernel.k8s_backend as k8s_backend
from runtime_kernel.docker_backend import DockerBackend, docker_name
from runtime_kernel.k8s_backend import K8sBackend, k8s_label_value, k8s_name
from runtime_kernel.profiles import Runnable

DNS1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
LABEL_VALUE = re.compile(r"^[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$")
DOCKER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]+$")

#: The shape that failed live: a scaffold chat's warm unit (secrets.token_urlsafe(32) session).
SCAFFOLD = "agent-7-chat-scaffold-Zk3_Qp9xLm2-Vb8NcR4tYw1_HsD6fJ0aGeU5iOq7KlP"
LONG = "mtg-" + "a" * 120
VALID = ["mtg-9", "mtg-2-d93eee39", "agent-58-chat", "agent-meet-5f0c2a", "agent-7-event-0a1b2c3d4e"]


def _submit(monkeypatch, workload_id: str) -> dict:
    submitted = []

    def fake_kubectl(*args, check=True, stdin=None):
        submitted.append(json.loads(stdin))

    monkeypatch.setattr(k8s_backend, "_kubectl", fake_kubectl)
    K8sBackend(namespace="ns").start(workload_id, Runnable(image="img"), {})
    return submitted[0]


def _k8s_valid(pod: dict) -> None:
    name = pod["metadata"]["name"]
    assert len(name) <= 63 and DNS1123.match(name), name
    container = pod["spec"]["containers"][0]["name"]
    assert len(container) <= 63 and DNS1123.match(container), container
    label = pod["metadata"]["labels"][k8s_backend.WORKLOAD_ID_LABEL]
    assert len(label) <= 63 and LABEL_VALUE.match(label), label


@pytest.mark.parametrize("workload_id", [SCAFFOLD, LONG, "agent-7-chat-Main", "agent-7-chat-a_b.c"])
def test_every_submitted_name_is_one_kubernetes_accepts(monkeypatch, workload_id):
    pod = _submit(monkeypatch, workload_id)
    _k8s_valid(pod)
    assert pod["metadata"]["annotations"] == {k8s_backend.WORKLOAD_ID_LABEL: workload_id}


@pytest.mark.parametrize("workload_id", VALID)
def test_an_id_kubernetes_already_accepts_keeps_its_names(monkeypatch, workload_id):
    pod = _submit(monkeypatch, workload_id)
    assert pod["metadata"]["name"] == f"vexa-{workload_id}"
    assert pod["spec"]["containers"][0]["name"] == f"vexa-{workload_id}"
    assert pod["metadata"]["labels"][k8s_backend.WORKLOAD_ID_LABEL] == workload_id
    assert k8s_name(f"vexa-{workload_id}") == f"vexa-{workload_id}"


def test_ids_differing_only_in_case_or_cut_get_distinct_names():
    upper, lower = "agent-7-chat-scaffold-AbC", "agent-7-chat-scaffold-abc"
    assert k8s_name("vexa-" + upper) != k8s_name("vexa-" + lower)
    assert k8s_label_value(upper) != k8s_label_value(lower)
    a, b = "mtg-" + "a" * 100 + "1", "mtg-" + "a" * 100 + "2"           # equal after the cut
    assert k8s_name(a) != k8s_name(b)
    assert k8s_name(SCAFFOLD) == k8s_name(SCAFFOLD)                       # deterministic


def test_find_and_stop_address_the_pod_by_the_name_it_was_given(monkeypatch):
    calls = []

    def fake_kubectl(*args, check=True, stdin=None):
        calls.append(args)

        class R:  # noqa: N801
            returncode, stdout, stderr = 0, "", ""
        return R()

    monkeypatch.setattr(k8s_backend, "_kubectl", fake_kubectl)
    be = K8sBackend()
    handle = be.start(SCAFFOLD, Runnable(image="img"), {})
    found = be.find(SCAFFOLD)
    be.terminate(found)
    name = k8s_name("vexa-" + SCAFFOLD)
    assert handle._impl == found._impl == name                   # type: ignore[attr-defined]
    assert name in calls[1] and name in calls[2]


def test_adoption_reads_the_id_from_the_annotation(monkeypatch):
    name = k8s_name("vexa-" + SCAFFOLD)
    pods = {"items": [
        {"metadata": {"name": name,
                      "labels": {"runtime.managed": "true",
                                 "runtime.workload_id": k8s_label_value(SCAFFOLD)},
                      "annotations": {"runtime.workload_id": SCAFFOLD}},
         "status": {"phase": "Running"}},
        # A Pod from before the annotation: its label was the id itself.
        {"metadata": {"name": "vexa-mtg-9", "labels": {"runtime.managed": "true",
                                                       "runtime.workload_id": "mtg-9"}},
         "status": {"phase": "Running"}},
    ]}

    def fake_kubectl(*args, check=True, stdin=None):
        class R:  # noqa: N801
            returncode, stdout, stderr = 0, json.dumps(pods), ""
        return R()

    monkeypatch.setattr(k8s_backend, "_kubectl", fake_kubectl)
    found = {i["workload_id"]: i for i in K8sBackend().list_workload_containers()}
    assert set(found) == {SCAFFOLD, "mtg-9"}
    assert found[SCAFFOLD]["name"] == name


# ── docker: its own rule, and no 63-character limit ──────────────────────────────────────────────

def test_docker_keeps_every_name_it_already_accepts():
    for raw in ["vexa-" + SCAFFOLD, "vexa-" + LONG, "vexa-worker-58-chat", "vexa-mtg-9"]:
        assert docker_name(raw) == raw
    assert DockerBackend()._cname(SCAFFOLD) == "vexa-worker-7-chat-scaffold-" + SCAFFOLD.split("scaffold-")[1]


@pytest.mark.parametrize("session", ["a b", "a/b", "x:y", "émoji", "../up"])
def test_docker_names_an_id_it_would_refuse_validly_and_distinctly(session):
    raw = f"vexa-worker-7-chat-{session}"
    name = docker_name(raw)
    assert DOCKER.match(name), name
    assert name != docker_name(raw + "x")
    assert docker_name("vexa-worker-7-chat-a b") != docker_name("vexa-worker-7-chat-a/b")


# ── process (Lite): the log file is one name inside the log dir ──────────────────────────────────

@pytest.mark.parametrize("workload_id", ["agent-7-chat-../../escape", "agent-7-chat-a/b", "w-ok"])
def test_the_process_backend_logs_inside_its_log_dir(monkeypatch, tmp_path, workload_id):
    import sys

    from runtime_kernel.process_backend import ProcessBackend

    log_dir = tmp_path / "logs"
    monkeypatch.setenv("PROCESS_LOG_DIR", str(log_dir))
    h = ProcessBackend().start(workload_id, Runnable(command=[sys.executable, "-c", "print('hi')"]), {})
    h._impl.wait(timeout=20)                                       # type: ignore[attr-defined]
    written = [p for p in tmp_path.rglob("*.log")]
    assert len(written) == 1 and written[0].parent == log_dir, written
    if workload_id == "w-ok":
        assert written[0].name == "w-ok.log"                       # a valid id keeps its file name
