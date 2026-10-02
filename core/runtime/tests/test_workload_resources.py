"""#1005 — resource intent reaches the substrate, so a quota-controlled namespace admits both
shipped workload classes (meeting-bot and agent worker).

Three seams, all provable OFFLINE (no cluster):

  * C1 — an accepted ``WorkloadSpec.resources`` crosses the Backend port (the kernel used to drop it),
    and a profile's chart-configured default applies when the caller omits it;
  * C2 — the k8s backend submits a COMPLETE Pod object carrying ``resources.requests`` and
    ``resources.limits`` alongside every field the spawn already generated (image, command, env,
    labels, volumes/volumeMounts, tolerations, nodeSelector);
  * C3 — the two profiles are sized INDEPENDENTLY from the runtime's own env, and an unset profile
    preserves the optional contract (no resources emitted — today's behaviour).

runtime.v1 carries ONE cpu and ONE memoryMb per workload; the Kubernetes mapping sets BOTH the
request and the limit from that single value (Guaranteed QoS). The sealed v1 contract does not model
separate request/limit semantics and this change does not invent them.
"""
from __future__ import annotations

import json

import pytest

import runtime_kernel.k8s_backend as k8s_backend
from runtime_kernel import Runtime
from runtime_kernel.k8s_backend import K8sBackend, build_pod, resource_requirements
from runtime_kernel.models import Resources, WorkloadSpec
from runtime_kernel.profiles import Profile, Runnable, default_registry


# ── C1 · resource intent crosses the Backend port ────────────────────────────────────────────
class _RecordingBackend:
    """A fake backend that records exactly what the kernel handed it."""

    name = "process"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def start(self, workload_id, runnable, env, resources=None):
        self.calls.append({"workload_id": workload_id, "env": env, "resources": resources})
        return k8s_backend.WorkloadHandle(id=workload_id, impl=workload_id)

    def exit_code(self, h):
        return None

    def terminate(self, h):
        pass

    def kill(self, h):
        pass

    def cleanup(self, h):
        pass


def _runtime(backend, profiles) -> Runtime:
    return Runtime(backend=backend, profiles=profiles, grace_sec=0.1)


def test_spec_resources_reach_the_backend():
    """The exact resources the caller accepted are the resources the backend is asked to enforce."""
    be = _RecordingBackend()
    rt = _runtime(be, {"meeting-bot": Runnable(image="bot")})
    rt.create(WorkloadSpec(
        workloadId="mtg-1", profile="meeting-bot", env={},
        resources=Resources(cpu=1.5, memoryMb=3072),
    ))
    assert be.calls[0]["resources"] == Resources(cpu=1.5, memoryMb=3072)


def test_profile_default_applies_when_the_caller_omits_resources():
    """C3: per-profile deployment sizing applies to callers that emit no resources — the meeting
    and agent producers stay resource-agnostic; the operator sizes each class in the chart."""
    be = _RecordingBackend()
    rt = _runtime(be, {
        "meeting-bot": Profile(name="meeting-bot", runnable=Runnable(image="bot"),
                               resources=Resources(cpu=1.0, memoryMb=2048)),
        "agent": Profile(name="agent", runnable=Runnable(image="worker"),
                         resources=Resources(cpu=0.5, memoryMb=1024)),
    })
    rt.create(WorkloadSpec(workloadId="mtg-2", profile="meeting-bot", env={}))
    rt.create(WorkloadSpec(workloadId="agent-2", profile="agent", env={}))
    assert be.calls[0]["resources"] == Resources(cpu=1.0, memoryMb=2048)
    assert be.calls[1]["resources"] == Resources(cpu=0.5, memoryMb=1024)  # INDEPENDENTLY sized


def test_explicit_spec_resources_override_the_profile_default():
    be = _RecordingBackend()
    rt = _runtime(be, {"meeting-bot": Profile(
        name="meeting-bot", runnable=Runnable(image="bot"),
        resources=Resources(cpu=1.0, memoryMb=2048),
    )})
    rt.create(WorkloadSpec(
        workloadId="mtg-3", profile="meeting-bot", env={},
        resources=Resources(cpu=4.0, memoryMb=8192),
    ))
    assert be.calls[0]["resources"] == Resources(cpu=4.0, memoryMb=8192)


def test_unconfigured_profile_preserves_the_optional_contract():
    """No profile default and no spec resources ⇒ None reaches the backend: the pre-#1005 shape,
    so an operator who sizes nothing keeps today's (unbounded) behaviour."""
    be = _RecordingBackend()
    rt = _runtime(be, {"meeting-bot": Runnable(image="bot")})
    rt.create(WorkloadSpec(workloadId="mtg-4", profile="meeting-bot", env={}))
    assert be.calls[0]["resources"] is None


# ── C1 · malformed / negative values fail before the spawn ───────────────────────────────────
@pytest.mark.parametrize("bad", [{"cpu": -1}, {"memoryMb": -512}, {"gpu": -1}])
def test_negative_resources_are_rejected_before_spawn(bad):
    """The sealed schema floors every resource at 0; the models reject a negative at parse time,
    so a malformed spec never reaches a backend."""
    with pytest.raises(Exception):
        WorkloadSpec(workloadId="w", profile="meeting-bot", env={}, resources=Resources(**bad))


def test_zero_resources_are_not_emitted():
    """0 is schema-legal but meaningless as a k8s quantity — treat it as unset rather than
    submitting `cpu: 0` (which a quota reads as a zero request)."""
    assert resource_requirements(Resources(cpu=0, memoryMb=0)) is None


# ── C2 · the k8s Pod carries requests+limits AND every generated field ───────────────────────
def test_resource_requirements_maps_v1_to_requests_and_limits():
    req = resource_requirements(Resources(cpu=1.5, memoryMb=2048))
    assert req == {
        "requests": {"cpu": "1500m", "memory": "2048Mi"},
        "limits": {"cpu": "1500m", "memory": "2048Mi"},
    }


def test_gpu_maps_to_the_limits_side_only():
    """`nvidia.com/gpu` is an extended resource: Kubernetes requires the limit, and the request is
    set equal to it automatically. Emitting it only under limits is the documented positive mapping."""
    req = resource_requirements(Resources(gpu=1))
    assert req["limits"]["nvidia.com/gpu"] == "1"
    assert "nvidia.com/gpu" not in req.get("requests", {})


def test_pod_carries_resources_and_every_pre_existing_field():
    """A3: resource injection must not erase image, command, env, labels, scheduling or mounts —
    the exact failure mode a partial `containers` entry in `kubectl run --overrides` produces."""
    env = {
        "VEXA_BOT_CONFIG": "{}",
        "VEXA_WORKSPACE_MOUNT_SOURCE": "vexa-workspaces",
        "VEXA_WORKSPACE_MOUNT_TARGET": "/workspaces",
        "VEXA_MOUNTS": json.dumps([
            {"slug": "u1", "path": "/workspaces/u1", "role": "private", "write": True,
             "primary": True},
        ]),
        k8s_backend.TOLERATIONS_ENV: json.dumps([{"key": "vexa", "operator": "Exists"}]),
        k8s_backend.NODE_SELECTOR_ENV: json.dumps({"pool": "bots"}),
    }
    pod = build_pod(
        name="vexa-mtg-9",
        workload_id="mtg-9",
        runnable=Runnable(image="vexaai/vexa-bot:test", command=["python", "-m", "worker"]),
        env=env,
        namespace="vexa",
        resources=Resources(cpu=1, memoryMb=2048),
    )
    assert pod["apiVersion"] == "v1" and pod["kind"] == "Pod"
    assert pod["metadata"]["name"] == "vexa-mtg-9"
    assert pod["metadata"]["namespace"] == "vexa"
    assert pod["metadata"]["labels"] == {
        k8s_backend.MANAGED_LABEL: "true", k8s_backend.WORKLOAD_ID_LABEL: "mtg-9",
    }
    spec = pod["spec"]
    assert spec["restartPolicy"] == "Never"          # the kernel owns restart policy
    assert spec["tolerations"] == [{"key": "vexa", "operator": "Exists"}]
    assert spec["nodeSelector"] == {"pool": "bots"}
    assert spec["volumes"], "workspace store volume survived"
    c = spec["containers"][0]
    assert c["name"] == "vexa-mtg-9"
    assert c["image"] == "vexaai/vexa-bot:test"       # NOT erased
    assert c["command"] == ["python", "-m", "worker"]  # NOT erased
    assert {"name": "VEXA_BOT_CONFIG", "value": "{}"} in c["env"]  # NOT erased
    assert c["volumeMounts"], "workspace volumeMounts survived"
    assert c["resources"] == {
        "requests": {"cpu": "1000m", "memory": "2048Mi"},
        "limits": {"cpu": "1000m", "memory": "2048Mi"},
    }


def test_pod_without_resources_omits_the_field_entirely():
    pod = build_pod(name="vexa-w", workload_id="w", runnable=Runnable(image="img"),
                    env={}, namespace=None, resources=None)
    assert "resources" not in pod["spec"]["containers"][0]
    assert "namespace" not in pod["metadata"]
    assert "command" not in pod["spec"]["containers"][0]   # image ENTRYPOINT is authoritative


def test_runtime_scheduling_env_shapes_the_pod_without_becoming_container_config(monkeypatch):
    """The runtime's OWN scheduling knobs must reach the Pod spec and STOP there — a workload's
    container env is the producer's contract, not a dumping ground for the backend's config."""
    calls: list[dict] = []

    def fake_kubectl(*args, check=True, stdin=None):
        calls.append({"args": list(args), "stdin": stdin})

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(k8s_backend, "_kubectl", fake_kubectl)
    monkeypatch.setenv(k8s_backend.TOLERATIONS_ENV, json.dumps([{"key": "vexa", "operator": "Exists"}]))
    monkeypatch.setenv(k8s_backend.NODE_SELECTOR_ENV, json.dumps({"pool": "bots"}))
    K8sBackend(namespace="ns").start("mtg-6", Runnable(image="img"), env={"A": "b"})
    pod = json.loads(calls[0]["stdin"])
    assert pod["spec"]["tolerations"] == [{"key": "vexa", "operator": "Exists"}]
    assert pod["spec"]["nodeSelector"] == {"pool": "bots"}
    assert pod["spec"]["containers"][0]["env"] == [{"name": "A", "value": "b"}]


def test_k8s_start_submits_the_pod_via_create_stdin(monkeypatch):
    """The spawn submits a complete manifest on stdin (`kubectl create -f -`) — not a partial
    `--overrides` merge, which json-merge-REPLACES the generated containers list."""
    calls: list[dict] = []

    def fake_kubectl(*args, check=True, stdin=None):
        calls.append({"args": list(args), "stdin": stdin})

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(k8s_backend, "_kubectl", fake_kubectl)
    K8sBackend(namespace="ns").start(
        "mtg-5", Runnable(image="img"), env={"A": "b"}, resources=Resources(cpu=2, memoryMb=4096),
    )
    assert calls[0]["args"] == ["create", "-f", "-", "-n", "ns"]
    pod = json.loads(calls[0]["stdin"])
    assert pod["spec"]["containers"][0]["resources"]["requests"] == {"cpu": "2000m", "memory": "4096Mi"}
    assert pod["spec"]["containers"][0]["resources"]["limits"] == {"cpu": "2000m", "memory": "4096Mi"}


# ── C3 · the two profiles are sized independently from the runtime's env ─────────────────────
def test_default_registry_sizes_the_two_profiles_independently(monkeypatch):
    monkeypatch.setenv("BROWSER_IMAGE", "bot:test")
    monkeypatch.setenv("AGENT_IMAGE", "vexaai/v012-agent-api:test")
    monkeypatch.setenv("RUNTIME_BOT_CPU", "1")
    monkeypatch.setenv("RUNTIME_BOT_MEMORY_MB", "2048")
    monkeypatch.setenv("RUNTIME_AGENT_WORKER_CPU", "0.5")
    monkeypatch.setenv("RUNTIME_AGENT_WORKER_MEMORY_MB", "1024")
    reg = default_registry()
    assert reg.get("meeting-bot").resources == Resources(cpu=1.0, memoryMb=2048)
    assert reg.get("agent").resources == Resources(cpu=0.5, memoryMb=1024)


def test_default_registry_emits_no_resources_when_unset(monkeypatch):
    """The chart's unset case: no env ⇒ no profile default ⇒ the optional contract is preserved."""
    monkeypatch.setenv("BROWSER_IMAGE", "bot:test")
    monkeypatch.setenv("AGENT_IMAGE", "vexaai/v012-agent-api:test")
    for key in ("RUNTIME_BOT_CPU", "RUNTIME_BOT_MEMORY_MB",
                "RUNTIME_AGENT_WORKER_CPU", "RUNTIME_AGENT_WORKER_MEMORY_MB"):
        monkeypatch.delenv(key, raising=False)
    reg = default_registry()
    assert reg.get("meeting-bot").resources is None
    assert reg.get("agent").resources is None


def test_partial_profile_sizing_is_honoured(monkeypatch):
    """Memory only (the common quota shape: memory is the scarce resource) is a legal sizing."""
    monkeypatch.setenv("BROWSER_IMAGE", "bot:test")
    monkeypatch.setenv("AGENT_IMAGE", "a:test")
    monkeypatch.delenv("RUNTIME_BOT_CPU", raising=False)
    monkeypatch.setenv("RUNTIME_BOT_MEMORY_MB", "2048")
    reg = default_registry()
    assert reg.get("meeting-bot").resources == Resources(memoryMb=2048)
    assert resource_requirements(reg.get("meeting-bot").resources) == {
        "requests": {"memory": "2048Mi"}, "limits": {"memory": "2048Mi"},
    }


def test_malformed_profile_sizing_is_fatal_at_boot(monkeypatch):
    """A silently dropped resource constraint is exactly the bug this fixes — a non-numeric knob
    must fail loud at registry construction, never fail open into an unsized spawn."""
    monkeypatch.setenv("BROWSER_IMAGE", "bot:test")
    monkeypatch.setenv("AGENT_IMAGE", "a:test")
    monkeypatch.setenv("RUNTIME_BOT_CPU", "one-and-a-half")
    with pytest.raises(ValueError, match="RUNTIME_BOT_CPU"):
        default_registry()
