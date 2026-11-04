"""A class can carry ONE required placement rule: the nodes its Pods must never land on.

On 4 Nov 2026 (scal-1104 stand, run fix-4) a coredns replica the coredns-autoscaler added as nodes
joined found the two system-host nodes full of meeting bots and PREEMPTED a bot that was in a live
meeting. Hosted prod (LKE 590708) has the same shape today: its pool is untainted and bots select only
``vexa.ai/pool=main``, so they land on the two ``vexa.ai/role=system-host`` nodes beside admin-api,
meeting-api, the gateway and kube-system. A node selector can say where a Pod may go, never where it
must not, so ``RUNTIME_K8S_<CLASS>_NODE_AFFINITY`` renders a REQUIRED node-affinity term:

* unset or ``[]`` (the chart default) ⇒ the Pod carries no affinity at all, exactly as before;
* set ⇒ one required term with exactly those requirements, on that class's Pods only;
* a join retry's preferred anti-affinity (avoidNodes) is added beside it, never replacing it;
* a malformed value stops the boot by name; a spec's env cannot set it.
"""
from __future__ import annotations

import json

import pytest

import runtime_kernel.k8s_backend as k8s_backend
from runtime_kernel import Runtime
from runtime_kernel.k8s_backend import K8sBackend, build_pod
from runtime_kernel.models import WorkloadSpec
from runtime_kernel.profiles import default_registry
from runtime_kernel.workload_env import StoreConfig

BOT = "RUNTIME_K8S_BOT_"
WORKER = "RUNTIME_K8S_AGENT_WORKER_"
OFF_SYSTEM_HOST = [{"key": "vexa.ai/role", "operator": "NotIn", "values": ["system-host"]}]
REQUIRED = "requiredDuringSchedulingIgnoredDuringExecution"
PREFERRED = "preferredDuringSchedulingIgnoredDuringExecution"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("BROWSER_IMAGE", "bot:test")
    monkeypatch.setenv("AGENT_IMAGE", "vexaai/vexa-agent-api:test")
    for prefix in (BOT, WORKER):
        for suffix in ("NODE_SELECTOR", "NODE_AFFINITY", "TOLERATIONS", "PRIORITY_CLASS_NAME",
                       "IMAGE_PULL_SECRETS"):
            monkeypatch.delenv(prefix + suffix, raising=False)
    monkeypatch.delenv(k8s_backend.TOLERATIONS_ENV, raising=False)
    monkeypatch.delenv(k8s_backend.NODE_SELECTOR_ENV, raising=False)


def _pod(profile: str, avoid_nodes=()) -> dict:
    runnable = default_registry().get(profile).runnable
    return build_pod(name="vexa-w", workload_id="w", runnable=runnable, env={}, namespace="ns",
                     resources=None, avoid_nodes=tuple(avoid_nodes))


@pytest.mark.parametrize("value", [None, "", "[]"])
def test_unset_or_empty_adds_no_affinity(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv(BOT + "NODE_AFFINITY", value)
    assert "affinity" not in _pod("meeting-bot")["spec"]


def test_bots_must_not_land_on_system_host_nodes(monkeypatch):
    monkeypatch.setenv(BOT + "NODE_AFFINITY", json.dumps(OFF_SYSTEM_HOST))
    spec = _pod("meeting-bot")["spec"]
    assert spec["affinity"] == {"nodeAffinity": {REQUIRED: {
        "nodeSelectorTerms": [{"matchExpressions": OFF_SYSTEM_HOST}]}}}


def test_the_rule_is_the_classs_own(monkeypatch):
    monkeypatch.setenv(BOT + "NODE_AFFINITY", json.dumps(OFF_SYSTEM_HOST))
    assert "affinity" not in _pod("agent")["spec"]
    monkeypatch.setenv(WORKER + "NODE_AFFINITY", json.dumps([{"key": "gpu", "operator": "Exists"}]))
    worker = _pod("agent")["spec"]["affinity"]["nodeAffinity"][REQUIRED]
    assert worker == {"nodeSelectorTerms": [{"matchExpressions": [{"key": "gpu", "operator": "Exists"}]}]}


def test_a_join_retry_keeps_the_rule_and_adds_its_preference(monkeypatch):
    monkeypatch.setenv(BOT + "NODE_AFFINITY", json.dumps(OFF_SYSTEM_HOST))
    na = _pod("meeting-bot", avoid_nodes=["lke-1"])["spec"]["affinity"]["nodeAffinity"]
    assert na[REQUIRED] == {"nodeSelectorTerms": [{"matchExpressions": OFF_SYSTEM_HOST}]}
    assert na[PREFERRED] == [{"weight": 100, "preference": {"matchExpressions": [
        {"key": "kubernetes.io/hostname", "operator": "NotIn", "values": ["lke-1"]}]}}]


def test_the_submitted_pod_carries_the_rule(monkeypatch):
    """Through the kernel and the k8s backend's real spawn path (kubectl faked)."""
    monkeypatch.setenv(BOT + "NODE_AFFINITY", json.dumps(OFF_SYSTEM_HOST))
    submitted: list = []

    class _R:
        returncode, stderr = 0, ""

        def __init__(self, stdout=""):
            self.stdout = stdout

    def fake(*args, check=True, stdin=None):
        obj = json.loads(stdin) if stdin else {}
        if stdin and obj.get("kind") == "Pod":
            submitted.append(obj)
        return _R(json.dumps({"metadata": {"uid": "pod-uid"}}) if args[:1] == ("create",) else "")

    monkeypatch.setattr(k8s_backend, "_kubectl", fake)
    rt = Runtime(backend=K8sBackend(namespace="ns"), profiles=default_registry(), grace_sec=0.1,
                 workspace_store=StoreConfig())
    rt.create(WorkloadSpec(workloadId="mtg-1", profile="meeting-bot", env={"VEXA_BOT_CONFIG": "{}"}))
    terms = submitted[0]["spec"]["affinity"]["nodeAffinity"][REQUIRED]["nodeSelectorTerms"]
    assert terms == [{"matchExpressions": OFF_SYSTEM_HOST}]


def test_a_spec_cannot_set_it(monkeypatch):
    submitted: list = []

    class _R:
        returncode, stderr, stdout = 0, "", json.dumps({"metadata": {"uid": "u"}})

    def fake(*args, check=True, stdin=None):
        obj = json.loads(stdin) if stdin else {}
        if stdin and obj.get("kind") == "Pod":
            submitted.append(obj)
        return _R()

    monkeypatch.setattr(k8s_backend, "_kubectl", fake)
    rt = Runtime(backend=K8sBackend(namespace="ns"), profiles=default_registry(), grace_sec=0.1,
                 workspace_store=StoreConfig())
    rt.create(WorkloadSpec(workloadId="mtg-2", profile="meeting-bot",
                           env={"VEXA_BOT_CONFIG": "{}", BOT + "NODE_AFFINITY": json.dumps(OFF_SYSTEM_HOST)}))
    assert "affinity" not in submitted[0]["spec"]


MALFORMED = [
    '{"key": "vexa.ai/role"}',                                                  # not an array
    '["vexa.ai/role"]',                                                         # not an object
    '[{"key": "vexa.ai/role", "operator": "NotIn"}]',                           # NotIn needs values
    '[{"key": "vexa.ai/role", "operator": "In", "values": []}]',
    '[{"key": "vexa.ai/role", "operator": "Exists", "values": ["x"]}]',         # Exists takes none
    '[{"key": "vexa.ai/role", "operator": "Gt", "values": ["1"]}]',
    '[{"key": "bad key!", "operator": "Exists"}]',
    '[{"key": "vexa.ai/role", "operator": "NotIn", "values": ["has space"]}]',
    '[{"key": "vexa.ai/role", "operator": "NotIn", "values": "system-host"}]',  # values not a list
    '[{"key": "vexa.ai/role", "operator": "Exists", "weight": 1}]',             # unknown field
    "vexa.ai/role!=system-host",                                                # not JSON
]


@pytest.mark.parametrize("prefix", [BOT, WORKER])
@pytest.mark.parametrize("value", MALFORMED)
def test_a_malformed_rule_stops_the_registry_by_name(monkeypatch, prefix, value):
    monkeypatch.setenv(prefix + "NODE_AFFINITY", value)
    with pytest.raises(ValueError, match=prefix + "NODE_AFFINITY"):
        default_registry()
