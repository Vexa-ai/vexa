"""Where the runtime's spawned Pods land is profile data the operator sets, per class, at boot.

An operator who runs bots and agent workers on their own tainted pool, pulled with a private
registry's Secret and at a priority below the workloads they must never displace, sets
``RUNTIME_K8S_BOT_*`` / ``RUNTIME_K8S_AGENT_WORKER_*``. These tests hold that:

* unset (the default), a spawned Pod is exactly what it was before — nothing new on it;
* each class gets its own values, and a class's node selector or tolerations replace the
  runtime-wide ``RUNTIME_K8S_NODE_SELECTOR`` / ``RUNTIME_K8S_TOLERATIONS`` for that class only;
* a malformed value stops the boot by name, before any workload exists;
* a caller cannot choose any of it — not through the spec's env, not through a spec field.
"""
from __future__ import annotations

import json

import pytest

import runtime_kernel.k8s_backend as k8s_backend
from runtime_kernel import Runtime
from runtime_kernel.api import create_app
from runtime_kernel.k8s_backend import K8sBackend, build_pod
from runtime_kernel.models import WorkloadSpec
from runtime_kernel.pod_scheduling import PodScheduling
from runtime_kernel.profiles import Runnable, default_registry
from runtime_kernel.workload_env import StoreConfig

from _caller import TOKEN, caller_client
from test_worker_image import _create_payload

BOT = "RUNTIME_K8S_BOT_"
WORKER = "RUNTIME_K8S_AGENT_WORKER_"
STEALTH_TOLERATION = {"key": "vexa.ai/pool", "operator": "Equal", "value": "stealth", "effect": "NoSchedule"}
PLACEMENT_FIELDS = ("nodeSelector", "tolerations", "priorityClassName", "imagePullSecrets")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("BROWSER_IMAGE", "bot:test")
    monkeypatch.setenv("AGENT_IMAGE", "vexaai/v012-agent-api:test")
    for prefix in (BOT, WORKER):
        for suffix in ("NODE_SELECTOR", "TOLERATIONS", "PRIORITY_CLASS_NAME", "IMAGE_PULL_SECRETS"):
            monkeypatch.delenv(prefix + suffix, raising=False)
    monkeypatch.delenv(k8s_backend.TOLERATIONS_ENV, raising=False)
    monkeypatch.delenv(k8s_backend.NODE_SELECTOR_ENV, raising=False)


def _pod(profile: str, overlay_env=None) -> dict:
    runnable = default_registry().get(profile).runnable
    return build_pod(name="vexa-w", workload_id="w", runnable=runnable, env={}, namespace="ns",
                     resources=None, overlay_env=overlay_env)


def _stealth(monkeypatch, prefix: str, *, priority: str, secret: str) -> None:
    monkeypatch.setenv(prefix + "NODE_SELECTOR", json.dumps({"vexa.ai/pool": "stealth"}))
    monkeypatch.setenv(prefix + "TOLERATIONS", json.dumps([STEALTH_TOLERATION]))
    monkeypatch.setenv(prefix + "PRIORITY_CLASS_NAME", priority)
    monkeypatch.setenv(prefix + "IMAGE_PULL_SECRETS", json.dumps([secret]))


# ── defaults: nothing changes ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("profile", ["meeting-bot", "agent"])
@pytest.mark.parametrize("rendered_empty", [False, True])
def test_unset_or_empty_adds_nothing_to_the_pod(monkeypatch, profile, rendered_empty):
    if rendered_empty:   # the chart's default render: "{}", "[]", "", "[]"
        for prefix in (BOT, WORKER):
            monkeypatch.setenv(prefix + "NODE_SELECTOR", "{}")
            monkeypatch.setenv(prefix + "TOLERATIONS", "[]")
            monkeypatch.setenv(prefix + "PRIORITY_CLASS_NAME", "")
            monkeypatch.setenv(prefix + "IMAGE_PULL_SECRETS", "[]")
    assert default_registry().get(profile).runnable.scheduling == PodScheduling()
    spec = _pod(profile)["spec"]
    assert not any(f in spec for f in PLACEMENT_FIELDS)


def test_unset_keeps_the_runtime_wide_placement(monkeypatch):
    runtime_wide = {k8s_backend.TOLERATIONS_ENV: json.dumps([{"key": "main", "operator": "Exists"}]),
                    k8s_backend.NODE_SELECTOR_ENV: json.dumps({"pool": "main"})}
    spec = _pod("meeting-bot", overlay_env=runtime_wide)["spec"]
    assert spec["tolerations"] == [{"key": "main", "operator": "Exists"}]
    assert spec["nodeSelector"] == {"pool": "main"}
    assert "priorityClassName" not in spec and "imagePullSecrets" not in spec


# ── per class ────────────────────────────────────────────────────────────────────────────────────

def test_each_class_gets_its_own_placement(monkeypatch):
    _stealth(monkeypatch, BOT, priority="vexa-stealth-bot", secret="regcred")
    monkeypatch.setenv(WORKER + "PRIORITY_CLASS_NAME", "vexa-stealth-worker")
    monkeypatch.setenv(WORKER + "IMAGE_PULL_SECRETS", json.dumps([{"name": "worker-reg"}, "worker-reg"]))
    bot = _pod("meeting-bot")["spec"]
    assert bot["nodeSelector"] == {"vexa.ai/pool": "stealth"}
    assert bot["tolerations"] == [STEALTH_TOLERATION]
    assert bot["priorityClassName"] == "vexa-stealth-bot"
    assert bot["imagePullSecrets"] == [{"name": "regcred"}]
    worker = _pod("agent")["spec"]
    assert worker["priorityClassName"] == "vexa-stealth-worker"
    assert worker["imagePullSecrets"] == [{"name": "worker-reg"}]          # one, not two
    assert "nodeSelector" not in worker and "tolerations" not in worker


def test_a_class_value_replaces_the_runtime_wide_one_for_that_field_only(monkeypatch):
    monkeypatch.setenv(BOT + "NODE_SELECTOR", json.dumps({"vexa.ai/pool": "stealth"}))
    runtime_wide = {k8s_backend.TOLERATIONS_ENV: json.dumps([{"key": "main", "operator": "Exists"}]),
                    k8s_backend.NODE_SELECTOR_ENV: json.dumps({"pool": "main"})}
    bot = _pod("meeting-bot", overlay_env=runtime_wide)["spec"]
    assert bot["nodeSelector"] == {"vexa.ai/pool": "stealth"}               # replaced, not merged
    assert bot["tolerations"] == [{"key": "main", "operator": "Exists"}]  # untouched
    worker = _pod("agent", overlay_env=runtime_wide)["spec"]
    assert worker["nodeSelector"] == {"pool": "main"}


def test_kubernetes_native_shapes_are_accepted(monkeypatch):
    monkeypatch.setenv("RUNTIME_K8S_ALLOW_BROAD_TOLERATIONS", "true")        # the operator opted in
    tolerations = [
        {"operator": "Exists"},                                              # tolerate everything
        {"key": "node.kubernetes.io/not-ready", "operator": "Exists", "effect": "NoExecute",
         "tolerationSeconds": 300},
        {"key": "dedicated", "value": "bots"},                               # operator defaults to Equal
    ]
    monkeypatch.setenv(BOT + "TOLERATIONS", json.dumps(tolerations))
    monkeypatch.setenv(BOT + "NODE_SELECTOR", json.dumps({"kubernetes.io/arch": "amd64", "spot": ""}))
    assert _pod("meeting-bot")["spec"]["tolerations"] == tolerations


def test_the_submitted_pod_carries_the_profiles_placement(monkeypatch):
    """End to end through the kernel and the k8s backend's real spawn path (kubectl faked)."""
    _stealth(monkeypatch, BOT, priority="vexa-stealth", secret="regcred")
    submitted = []
    monkeypatch.setattr(k8s_backend, "_kubectl",
                        lambda *a, check=True, stdin=None: submitted.append(json.loads(stdin)) if stdin else None)
    rt = Runtime(backend=K8sBackend(namespace="ns"), profiles=default_registry(), grace_sec=0.1,
                 workspace_store=StoreConfig())
    rt.create(WorkloadSpec(workloadId="mtg-1", profile="meeting-bot", env={"VEXA_BOT_CONFIG": "{}"}))
    spec = submitted[0]["spec"]
    assert spec["priorityClassName"] == "vexa-stealth"
    assert spec["imagePullSecrets"] == [{"name": "regcred"}]
    assert spec["nodeSelector"] == {"vexa.ai/pool": "stealth"}


# ── boot validation ──────────────────────────────────────────────────────────────────────────────

MALFORMED = [
    ("NODE_SELECTOR", "pool=stealth"),                                       # not JSON
    ("NODE_SELECTOR", '["stealth"]'),                                        # not an object
    ("NODE_SELECTOR", '{"bad key!": "x"}'),
    ("NODE_SELECTOR", '{"pool": "has space"}'),
    ("NODE_SELECTOR", '{"pool": 1}'),
    ("TOLERATIONS", '{"key": "a"}'),                                         # not an array
    ("TOLERATIONS", '["a"]'),
    ("TOLERATIONS", '[{"key": "a", "operator": "Like"}]'),
    ("TOLERATIONS", '[{"key": "a", "operator": "Equal", "value": "x", "effect": "NoRun"}]'),
    ("TOLERATIONS", '[{"value": "x"}]'),                                     # no key ⇒ Exists only
    ("TOLERATIONS", '[{"key": "a", "operator": "Exists", "value": "x"}]'),
    ("TOLERATIONS", '[{"key": "a", "effect": "NoSchedule", "tolerationSeconds": 30}]'),
    ("TOLERATIONS", '[{"key": "a", "effect": "NoExecute", "tolerationSeconds": "30"}]'),
    ("TOLERATIONS", '[{"key": "a", "operator": "Exists", "nodeName": "n1"}]'),  # unknown field
    ("PRIORITY_CLASS_NAME", "Vexa_Low"),
    ("PRIORITY_CLASS_NAME", "system-node-critical"),
    ("PRIORITY_CLASS_NAME", "system-cluster-critical"),
    ("IMAGE_PULL_SECRETS", "regcred"),                                       # not JSON
    ("IMAGE_PULL_SECRETS", '{"name": "regcred"}'),                           # not an array
    ("IMAGE_PULL_SECRETS", '["Reg_Cred"]'),
    ("IMAGE_PULL_SECRETS", '[{"name": "regcred", "namespace": "other"}]'),
    ("IMAGE_PULL_SECRETS", '[""]'),
]


@pytest.mark.parametrize("prefix", [BOT, WORKER])
@pytest.mark.parametrize("suffix,value", MALFORMED)
def test_a_malformed_value_stops_the_registry_by_name(monkeypatch, prefix, suffix, value):
    monkeypatch.setenv(prefix + suffix, value)
    with pytest.raises(ValueError, match=prefix + suffix):
        default_registry()


def test_a_malformed_value_stops_the_production_boot(monkeypatch):
    from runtime_kernel.__main__ import build_production_app

    monkeypatch.setenv("RUNTIME_BACKEND", "process")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("AGENT_IMAGE", raising=False)
    monkeypatch.setenv("RUNTIME_API_TOKEN", "a-runtime-caller-token-of-sufficient-length-0123")
    monkeypatch.setenv(BOT + "PRIORITY_CLASS_NAME", "system-cluster-critical")
    with pytest.raises(ValueError, match=BOT + "PRIORITY_CLASS_NAME"):
        build_production_app()


# ── the caller cannot choose ─────────────────────────────────────────────────────────────────────

def test_a_spec_env_cannot_place_the_pod(monkeypatch):
    monkeypatch.setenv(BOT + "PRIORITY_CLASS_NAME", "vexa-stealth")
    submitted = []
    monkeypatch.setattr(k8s_backend, "_kubectl",
                        lambda *a, check=True, stdin=None: submitted.append(json.loads(stdin)) if stdin else None)
    rt = Runtime(backend=K8sBackend(namespace="ns"), profiles=default_registry(), grace_sec=0.1,
                 workspace_store=StoreConfig())
    hostile = {
        BOT + "PRIORITY_CLASS_NAME": "system-node-critical",
        BOT + "NODE_SELECTOR": json.dumps({"pool": "customer"}),
        BOT + "TOLERATIONS": json.dumps([{"operator": "Exists"}]),
        BOT + "IMAGE_PULL_SECRETS": json.dumps(["someone-elses"]),
        k8s_backend.NODE_SELECTOR_ENV: json.dumps({"pool": "customer"}),
        k8s_backend.TOLERATIONS_ENV: json.dumps([{"operator": "Exists"}]),
    }
    rt.create(WorkloadSpec(workloadId="mtg-2", profile="meeting-bot", env=hostile))
    pod = submitted[0]
    assert pod["spec"]["priorityClassName"] == "vexa-stealth"
    assert not any(f in pod["spec"] for f in ("nodeSelector", "tolerations", "imagePullSecrets"))
    names = {e["name"] for e in pod["spec"]["containers"][0]["env"]}
    assert not names & set(hostile)


@pytest.mark.parametrize("field", ["nodeSelector", "tolerations", "priorityClassName", "imagePullSecrets",
                                   "scheduling"])
def test_a_spec_field_cannot_place_the_pod(field):
    """``WorkloadSpec`` names no placement field, and refuses any it does not name (422)."""
    client = caller_client(create_app(Runtime(profiles={"test": ["sleep", "30"]}, grace_sec=0.1),
                                      deliver=lambda e: None, caller_token=TOKEN))
    r = client.post("/workloads", json={"workloadId": "w1", "profile": "test", "env": {},
                                        field: "system-node-critical"})
    assert r.status_code == 422, r.text


# ── only Kubernetes has a scheduler to tell ──────────────────────────────────────────────────────

def test_the_docker_backend_ignores_placement(monkeypatch):
    monkeypatch.setenv("DOCKER_NETWORK", "net")
    placed = Runnable(image="bot:1", scheduling=PodScheduling(priority_class_name="vexa-stealth",
                                                                image_pull_secrets=("regcred",)))
    assert _create_payload(monkeypatch, placed, "job-1") == _create_payload(monkeypatch, Runnable(image="bot:1"), "job-1")



# ── tolerations that reach every node or the cluster's own nodes need an explicit opt-in (RT5-1) ──

@pytest.mark.parametrize("toleration", [
    {"operator": "Exists"},
    {"operator": "Exists", "effect": "NoSchedule"},
    {"key": "node-role.kubernetes.io/control-plane", "operator": "Exists", "effect": "NoSchedule"},
    {"key": "node-role.kubernetes.io/master", "operator": "Exists"},
    {"key": "CriticalAddonsOnly", "operator": "Exists"},
])
@pytest.mark.parametrize("prefix", [BOT, "RUNTIME_K8S_AGENT_WORKER_"])
def test_a_broad_toleration_is_refused_without_the_opt_in(monkeypatch, toleration, prefix):
    monkeypatch.setenv(prefix + "TOLERATIONS", json.dumps([toleration]))
    with pytest.raises(ValueError, match="RUNTIME_K8S_ALLOW_BROAD_TOLERATIONS"):
        default_registry()
    monkeypatch.setenv("RUNTIME_K8S_ALLOW_BROAD_TOLERATIONS", "true")
    default_registry()


@pytest.mark.parametrize("env", [
    {"RUNTIME_K8S_TOLERATIONS": '[{"operator": "Exists"}]'},
    {"RUNTIME_K8S_TOLERATIONS": '[{"key": "node-role.kubernetes.io/control-plane", "operator": "Exists"}]'},
    {"RUNTIME_K8S_TOLERATIONS": '[{"key": "bad key!", "operator": "Exists"}]'},
    {"RUNTIME_K8S_NODE_SELECTOR": '{"bad key!": "x"}'},
    {"RUNTIME_K8S_NODE_SELECTOR": '["not", "an", "object"]'},
    {"RUNTIME_K8S_ALLOW_BROAD_TOLERATIONS": "maybe"},
])
def test_the_runtime_wide_placement_is_validated_at_boot(monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        K8sBackend()


def test_a_valid_runtime_wide_placement_boots(monkeypatch):
    monkeypatch.setenv("RUNTIME_K8S_TOLERATIONS", '[{"key": "dedicated", "value": "vexa", "effect": "NoSchedule"}]')
    monkeypatch.setenv("RUNTIME_K8S_NODE_SELECTOR", '{"vexa.ai/pool": "workloads"}')
    K8sBackend()


# ── spawned Pods are hardened (RT5-6) ───────────────────────────────────────────────────────────

def test_spawned_pods_drop_every_capability_but_the_profiles(monkeypatch):
    from runtime_kernel.profiles import WORKER_CAPABILITIES

    bot = _pod("meeting-bot")["spec"]["containers"][0]["securityContext"]
    worker = _pod("agent")["spec"]["containers"][0]["securityContext"]
    for sc in (bot, worker):
        assert sc["allowPrivilegeEscalation"] is False
        assert sc["capabilities"]["drop"] == ["ALL"]
        assert sc["seccompProfile"] == {"type": "RuntimeDefault"}
        assert "runAsNonRoot" not in sc                      # both shipped images start as root
    assert "add" not in bot["capabilities"]
    assert worker["capabilities"]["add"] == list(WORKER_CAPABILITIES)


def test_the_operator_narrows_a_class_capabilities(monkeypatch):
    monkeypatch.setenv("RUNTIME_K8S_AGENT_WORKER_CAPABILITIES", "[]")          # OpenShift restricted SCC
    worker = _pod("agent")["spec"]["containers"][0]["securityContext"]
    assert "add" not in worker["capabilities"] and worker["capabilities"]["drop"] == ["ALL"]
    monkeypatch.setenv("RUNTIME_K8S_AGENT_WORKER_CAPABILITIES", '["CAP_KILL", "SETUID"]')
    assert _pod("agent")["spec"]["containers"][0]["securityContext"]["capabilities"]["add"] == ["KILL", "SETUID"]
    for bad in ('["SYS_ADMIN"]', '["ALL"]', '["kill"]', '{"a": 1}'):
        monkeypatch.setenv("RUNTIME_K8S_AGENT_WORKER_CAPABILITIES", bad)
        with pytest.raises(ValueError):
            default_registry()


def test_a_profile_whose_image_runs_non_root_requires_it():
    from runtime_kernel.k8s_backend import build_pod
    from runtime_kernel.profiles import Runnable

    pod = build_pod(name="p", workload_id="w", runnable=Runnable(image="i", run_as_non_root=True), env={},
                    namespace=None, resources=None)
    assert pod["spec"]["containers"][0]["securityContext"]["runAsNonRoot"] is True
