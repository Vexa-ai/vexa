"""A meeting's join retry lands away from the nodes its earlier attempts ran on (WorkloadSpec.avoidNodes).

Google Meet refuses some anonymous knocks outright (HTTP 403, "You can't join this video call"). The
founder ruled on 1 Nov 2026 that the platform retries the join from a different node before telling the
user it failed: meeting-api spawns the next attempt and names the nodes already tried. The runtime turns
that into a PREFERRED anti-affinity (never required: with every node tried, the Pod still schedules and
the autoscaler is not asked for a node), merged with whatever placement the operator configured. A bot
Pod also carries VEXA_NODE_NAME, so an attempt can report where it ran.
"""
import inspect
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from referencing import Registry, Resource

import runtime_kernel.k8s_backend as k8s_backend
from runtime_kernel import Runtime
from runtime_kernel.docker_backend import DockerBackend
from runtime_kernel.k8s_backend import build_pod
from runtime_kernel.models import WorkloadSpec
from runtime_kernel.pod_scheduling import MAX_AVOID_NODES, apply_avoid_nodes
from runtime_kernel.process_backend import ProcessBackend
from runtime_kernel.profiles import NODE_NAME_ENV, Runnable

from _profiles import agent, bot

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "runtime.v1"
SCHEMA = json.loads((CONTRACT / "runtime.schema.json").read_text())
NODES = ["lke590708-865130-0a1b2c3d0000", "lke590708-865130-4e5f6a7b0000"]
TERM = {"weight": 100, "preference": {"matchExpressions": [
    {"key": "kubernetes.io/hostname", "operator": "NotIn", "values": NODES}]}}


def _spec(**extra) -> dict:
    return {"workloadId": "mtg-1-a2", "profile": "meeting-bot", "env": {}, **extra}


def _schema_errors(obj: dict) -> list:
    registry = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))
    shape = {"$ref": f"{SCHEMA['$id']}#/$defs/WorkloadSpec"}
    cls = jsonschema.validators.validator_for(SCHEMA)
    return list(cls(shape, registry=registry, format_checker=cls.FORMAT_CHECKER).iter_errors(obj))


def _pod(runnable: Runnable, avoid=(), env=None) -> dict:
    return build_pod(name="vexa-mtg-1-a2", workload_id="mtg-1-a2", runnable=runnable, env=env or {},
                     namespace=None, resources=None, avoid_nodes=tuple(avoid))


# ── validation: the model and the sealed schema agree ─────────────────────────────────────────────
def test_a_list_of_node_names_is_accepted_and_deduplicated():
    spec = WorkloadSpec(**_spec(avoidNodes=NODES + [NODES[0]]))
    assert spec.avoidNodes == NODES
    assert _schema_errors(_spec(avoidNodes=NODES)) == []


def test_the_field_is_optional():
    assert WorkloadSpec(**_spec()).avoidNodes is None
    assert _schema_errors(_spec()) == []


@pytest.mark.parametrize("bad", [
    ["Node_A"],                       # not DNS-1123 (uppercase, underscore)
    ["-starts-with-dash"],
    [""],
    ["a" * 254],                      # over 253
    "lke590708-865130-0a1b2c3d0000",  # a string, not a list
])
def test_a_value_that_is_not_a_node_name_refuses_the_spec(bad):
    with pytest.raises(ValidationError) as exc:
        WorkloadSpec(**_spec(avoidNodes=bad))
    assert "avoidNodes" in str(exc.value)
    assert _schema_errors(_spec(avoidNodes=bad)) != []


def test_more_than_the_bound_refuses_the_spec():
    too_many = [f"node-{i}" for i in range(MAX_AVOID_NODES + 1)]
    with pytest.raises(ValidationError, match=f"at most {MAX_AVOID_NODES}"):
        WorkloadSpec(**_spec(avoidNodes=too_many))
    assert _schema_errors(_spec(avoidNodes=too_many)) != []
    assert _schema_errors(_spec(avoidNodes=too_many[:MAX_AVOID_NODES])) == []


def test_the_retry_golden_conforms():
    golden = json.loads((CONTRACT / "golden" / "spec-meeting-bot-retry.json").read_text())
    assert _schema_errors(golden) == []
    assert WorkloadSpec(**golden).avoidNodes == golden["avoidNodes"]


# ── rendering: a preferred anti-affinity, merged, k8s only ────────────────────────────────────────
def test_avoid_nodes_render_as_a_preferred_anti_affinity_never_required():
    affinity = _pod(bot(), avoid=NODES)["spec"]["affinity"]
    assert affinity == {"nodeAffinity": {"preferredDuringSchedulingIgnoredDuringExecution": [TERM]}}
    assert "requiredDuringSchedulingIgnoredDuringExecution" not in affinity["nodeAffinity"]


def test_no_avoid_nodes_means_no_affinity():
    assert "affinity" not in _pod(bot())["spec"]
    assert "affinity" not in _pod(bot(), avoid=())["spec"]


def test_it_merges_with_affinity_already_on_the_pod():
    """The term is appended; required node terms, other preferred terms and pod (anti-)affinity stay."""
    required = {"nodeSelectorTerms": [{"matchExpressions": [
        {"key": "pool", "operator": "In", "values": ["bots"]}]}]}
    other = {"weight": 10, "preference": {"matchExpressions": [
        {"key": "zone", "operator": "In", "values": ["a"]}]}}
    pod_anti = {"preferredDuringSchedulingIgnoredDuringExecution": [{"weight": 5, "podAffinityTerm": {
        "labelSelector": {"matchLabels": {"vexa.role": "bot"}}, "topologyKey": "kubernetes.io/hostname"}}]}
    spec = {"affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": required,
                                          "preferredDuringSchedulingIgnoredDuringExecution": [other]},
                         "podAntiAffinity": pod_anti}}
    apply_avoid_nodes(spec, NODES)
    node = spec["affinity"]["nodeAffinity"]
    assert node["requiredDuringSchedulingIgnoredDuringExecution"] == required
    assert node["preferredDuringSchedulingIgnoredDuringExecution"] == [other, TERM]
    assert spec["affinity"]["podAntiAffinity"] == pod_anti


def test_the_profiles_own_placement_still_applies():
    from dataclasses import replace
    from runtime_kernel.pod_scheduling import PodScheduling
    runnable = replace(bot(), scheduling=PodScheduling(node_selector={"pool": "bots"},
                                                       priority_class_name="vexa-bots"))
    spec = _pod(runnable, avoid=NODES)["spec"]
    assert spec["nodeSelector"] == {"pool": "bots"}
    assert spec["priorityClassName"] == "vexa-bots"
    assert spec["affinity"]["nodeAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"] == [TERM]


# ── VEXA_NODE_NAME on bot Pods ────────────────────────────────────────────────────────────────────
def _env(pod: dict) -> list:
    return pod["spec"]["containers"][0]["env"]


def test_a_bot_pod_reads_its_node_from_the_downward_api():
    assert NODE_NAME_ENV == "VEXA_NODE_NAME"
    entries = [e for e in _env(_pod(bot())) if e["name"] == "VEXA_NODE_NAME"]
    assert entries == [{"name": "VEXA_NODE_NAME", "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}}}]


def test_a_caller_cannot_set_the_node_name():
    entries = [e for e in _env(_pod(bot(), env={"VEXA_NODE_NAME": "spoofed"})) if e["name"] == "VEXA_NODE_NAME"]
    assert entries == [{"name": "VEXA_NODE_NAME", "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}}}]


def test_an_agent_worker_pod_gets_no_node_name():
    assert all(e["name"] != "VEXA_NODE_NAME" for e in _env(_pod(agent())))


# ── the kernel and the other backends ─────────────────────────────────────────────────────────────
class _RecordingBackend:
    """A backend written before avoid_nodes existed: start() has no such parameter."""
    name = "process"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def start(self, workload_id, runnable, env, resources=None, **kw):
        self.calls.append({"workload_id": workload_id, **kw})
        return k8s_backend.WorkloadHandle(id=workload_id, impl=workload_id)

    def exit_code(self, h):
        return None

    def terminate(self, h):
        pass

    def kill(self, h):
        pass

    def cleanup(self, h):
        pass


class _OldBackend(_RecordingBackend):
    def start(self, workload_id, runnable, env, resources=None):  # no **kw: the old port exactly
        self.calls.append({"workload_id": workload_id})
        return k8s_backend.WorkloadHandle(id=workload_id, impl=workload_id)


def test_the_kernel_hands_the_hint_to_the_backend():
    be = _RecordingBackend()
    Runtime(backend=be, profiles={"meeting-bot": Runnable(image="bot")}, grace_sec=0.1).create(
        WorkloadSpec(**_spec(avoidNodes=NODES)))
    assert be.calls == [{"workload_id": "mtg-1-a2", "avoid_nodes": tuple(NODES)}]


def test_a_spec_without_the_hint_calls_the_backend_exactly_as_before():
    be = _OldBackend()
    Runtime(backend=be, profiles={"meeting-bot": Runnable(image="bot")}, grace_sec=0.1).create(
        WorkloadSpec(**_spec()))
    assert be.calls == [{"workload_id": "mtg-1-a2"}]


@pytest.mark.parametrize("backend", [DockerBackend, ProcessBackend])
def test_docker_and_process_accept_and_ignore_it(backend):
    param = inspect.signature(backend.start).parameters["avoid_nodes"]
    assert param.default == ()
