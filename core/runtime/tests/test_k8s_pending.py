"""A Pending bot Pod's placement facts (runtime.v1 ``WorkloadStatus.pending``) — the evidence
meeting-api queues a bot on when the cluster is full (founder, 4 Nov 2026).

The facts are the scheduler's and the cluster autoscaler's own: ``PodScheduled=False`` with reason
``Unschedulable``, a nominated node (a preemption making room), and the autoscaler's latest Event on
the Pod — ``TriggeredScaleUp`` (a node is coming) or ``NotTriggerScaleUp`` (no node group can grow).
Hosted prod runs LKE's managed autoscaler, the upstream cluster-autoscaler, which writes its Pod
Events into ``vexa-production`` (read-only check 4 Nov 2026: a ``ScaleDown`` Event from component
``cluster-autoscaler`` on a capacity-reserve Pod). An image pull or a container being created is not
a capacity fact. The Pod shapes below are those of the Kubernetes API; kubectl is faked.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

from runtime_kernel import Runtime, k8s_backend
from runtime_kernel.backend import WorkloadHandle
from runtime_kernel.k8s_backend import K8sBackend, autoscaler_verdict, pending_from_pod
from runtime_kernel.models import RuntimeState, WorkloadSpec
from runtime_kernel.profiles import Runnable

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "contracts" / "runtime.v1"
                     / "runtime.schema.json").read_text())
CREATED = "2026-11-04T10:00:00Z"
UNSCHEDULABLE_AT = "2026-11-04T10:00:01Z"


def _status_errors(obj: dict) -> list:
    registry = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))
    cls = jsonschema.validators.validator_for(SCHEMA)
    shape = {"$ref": f"{SCHEMA['$id']}#/$defs/WorkloadStatus"}
    return list(cls(shape, registry=registry, format_checker=cls.FORMAT_CHECKER).iter_errors(obj))


def _pod(phase="Pending", *, unschedulable=False, nominated=None, waiting=None, scheduled=False):
    status: dict = {"phase": phase, "conditions": []}
    if unschedulable:
        status["conditions"].append({
            "type": "PodScheduled", "status": "False", "reason": "Unschedulable",
            "lastTransitionTime": UNSCHEDULABLE_AT,
            "message": "0/4 nodes are available: 4 Insufficient memory. preemption: 0/4 nodes are "
                       "available: 4 No preemption victims found for incoming pod.",
        })
    elif scheduled:
        status["conditions"].append({"type": "PodScheduled", "status": "True",
                                     "lastTransitionTime": UNSCHEDULABLE_AT})
    if nominated:
        status["nominatedNodeName"] = nominated
    if waiting:
        status["containerStatuses"] = [{"name": "bot", "state": {"waiting": {
            "reason": waiting, "message": f"{waiting}: back-off pulling image"}}}]
    return {"metadata": {"name": "vexa-mtg-1-a1", "uid": "uid-1", "creationTimestamp": CREATED},
            "status": status}


def _event(reason, at, *, component="cluster-autoscaler", message=None):
    return {"reason": reason, "source": {"component": component}, "lastTimestamp": at,
            "message": message or {"NotTriggerScaleUp": "pod didn't trigger scale-up: 1 max node group size reached",
                                   "TriggeredScaleUp": "pod triggered scale-up: [{lke590708-865130 4->5 (max: 20)}]"
                                   }.get(reason, reason)}


# ── the pure reading of a Pod ─────────────────────────────────────────────────────────────────────

def test_an_unschedulable_pod_the_autoscaler_cannot_grow_for():
    facts = pending_from_pod(_pod(unschedulable=True),
                             [_event("NotTriggerScaleUp", "2026-11-04T10:00:11Z")])
    assert facts == {"reason": "unschedulable", "since": UNSCHEDULABLE_AT, "nominated": False,
                     "autoscaler": "no_scale_up",
                     "detail": "pod didn't trigger scale-up: 1 max node group size reached"}


def test_a_normal_scale_up_wait_reads_scale_up():
    facts = pending_from_pod(_pod(unschedulable=True),
                             [_event("TriggeredScaleUp", "2026-11-04T10:00:11Z")])
    assert facts["reason"] == "unschedulable" and facts["autoscaler"] == "scale_up"


def test_the_autoscalers_latest_verdict_wins():
    grew_then_capped = [_event("TriggeredScaleUp", "2026-11-04T10:00:11Z"),
                        _event("NotTriggerScaleUp", "2026-11-04T10:03:00Z")]
    assert autoscaler_verdict(grew_then_capped)[0] == "no_scale_up"
    capped_then_grew = [_event("NotTriggerScaleUp", "2026-11-04T10:00:11Z"),
                        _event("TriggeredScaleUp", "2026-11-04T10:03:00Z")]
    assert autoscaler_verdict(capped_then_grew)[0] == "scale_up"


def test_only_the_autoscaler_speaks_for_the_autoscaler():
    forged = [_event("NotTriggerScaleUp", "2026-11-04T10:00:11Z", component="default-scheduler")]
    facts = pending_from_pod(_pod(unschedulable=True), forged)
    assert "autoscaler" not in facts
    assert facts["detail"].startswith("0/4 nodes are available"), "the scheduler's own message"


def test_no_verdict_yet():
    facts = pending_from_pod(_pod(unschedulable=True), [])
    assert facts["reason"] == "unschedulable" and "autoscaler" not in facts


def test_a_preemption_in_progress_is_nominated():
    facts = pending_from_pod(_pod(unschedulable=True, nominated="lke590708-865130-sstv6"), [])
    assert facts["nominated"] is True


def test_an_image_pull_is_not_a_capacity_fact():
    facts = pending_from_pod(_pod(scheduled=True, waiting="ImagePullBackOff"),
                             [_event("NotTriggerScaleUp", "2026-11-04T10:00:11Z")])
    assert facts == {"reason": "image_pull", "since": UNSCHEDULABLE_AT,
                     "detail": "ImagePullBackOff: back-off pulling image"}


def test_container_creating_is_starting():
    # Prod, 4 Nov 2026: bot Pods wait a moment for their env Secret (CreateContainerConfigError).
    for waiting in ("ContainerCreating", "CreateContainerConfigError", None):
        assert pending_from_pod(_pod(scheduled=True, waiting=waiting))["reason"] == "starting"


def test_a_running_or_finished_pod_has_no_pending_facts():
    for phase in ("Running", "Succeeded", "Failed"):
        assert pending_from_pod(_pod(phase=phase)) is None


def test_the_facts_conform_to_runtime_v1():
    facts = pending_from_pod(_pod(unschedulable=True),
                             [_event("NotTriggerScaleUp", "2026-11-04T10:00:11Z")])
    status = {"workloadId": "mtg-1-a1", "profile": "meeting-bot", "state": "running",
              "backend": "k8s", "pending": facts}
    assert _status_errors(status) == []
    assert _status_errors({**status, "pending": {"reason": "queued"}}) != [], \
        "a verdict is not a fact: the reasons are closed"


# ── the backend: one Pod read per get, Events read only when they can say something ─────────────

class FakeKubectl:
    def __init__(self, pod: dict, events: list, *, events_refused: bool = False):
        self.pod, self.events, self.events_refused = pod, events, events_refused
        self.calls: list[tuple] = []

    def __call__(self, *args, check=True, stdin=None):
        self.calls.append(args)

        class R:  # noqa: N801
            returncode, stdout, stderr = 0, "", ""
        r = R()
        if args[:2] == ("get", "pod"):
            r.stdout = json.dumps(self.pod)
        elif args[:2] == ("get", "events"):
            if self.events_refused:
                r.returncode = 1
                r.stderr = ('Error from server (Forbidden): events is forbidden: User '
                            '"system:serviceaccount:vexa-production:vexa-runtime" cannot list resource '
                            '"events"')
            else:
                r.stdout = json.dumps({"items": self.events})
        return r


def _backend(monkeypatch, fake) -> K8sBackend:
    monkeypatch.setattr(k8s_backend, "_kubectl", fake)
    return K8sBackend(namespace="vexa-production")


def test_pending_reuses_the_pod_exit_code_just_read_and_selects_this_incarnations_events(monkeypatch):
    fake = FakeKubectl(_pod(unschedulable=True), [_event("NotTriggerScaleUp", "2026-11-04T10:00:11Z")])
    backend = _backend(monkeypatch, fake)
    h = WorkloadHandle(id="mtg-1-a1", impl="vexa-mtg-1-a1")

    assert backend.exit_code(h) is None
    facts = backend.pending(h)

    assert facts["autoscaler"] == "no_scale_up"
    pod_reads = [c for c in fake.calls if c[:2] == ("get", "pod")]
    assert len(pod_reads) == 1, "the Pod is read once per get"
    (events_call,) = [c for c in fake.calls if c[:2] == ("get", "events")]
    selector = events_call[events_call.index("--field-selector") + 1]
    assert selector == "involvedObject.kind=Pod,involvedObject.name=vexa-mtg-1-a1,involvedObject.uid=uid-1"
    assert "watch" not in " ".join(events_call)


def test_events_are_not_read_for_a_pod_that_is_not_unschedulable(monkeypatch):
    fake = FakeKubectl(_pod(scheduled=True, waiting="ContainerCreating"), [])
    backend = _backend(monkeypatch, fake)
    assert backend.pending(WorkloadHandle(id="mtg-1-a1", impl="vexa-mtg-1-a1"))["reason"] == "starting"
    assert not [c for c in fake.calls if c[:2] == ("get", "events")]


def test_unreadable_events_give_facts_without_a_verdict_and_say_so_once(monkeypatch, capsys):
    fake = FakeKubectl(_pod(unschedulable=True), [], events_refused=True)
    backend = _backend(monkeypatch, fake)
    h = WorkloadHandle(id="mtg-1-a1", impl="vexa-mtg-1-a1")
    first, second = backend.pending(h), backend.pending(h)
    assert first == second and first["reason"] == "unschedulable" and "autoscaler" not in first
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert [line["event"] for line in lines].count("pod_events_unreadable") == 1


# ── the kernel: GET /workloads/{id} carries the facts while the workload waits ──────────────────

class _PendingBackend:
    name = "k8s"

    def __init__(self, facts):
        self.facts = facts

    def start(self, workload_id, runnable, env, resources=None, avoid_nodes=()):
        return WorkloadHandle(id=workload_id, impl=workload_id)

    def exit_code(self, h):
        return None

    def pending(self, h):
        return self.facts

    def terminate(self, h):
        pass

    def kill(self, h):
        pass

    def cleanup(self, h):
        pass


def test_the_kernel_reports_pending_facts_and_never_stores_them():
    facts = pending_from_pod(_pod(unschedulable=True),
                             [_event("NotTriggerScaleUp", "2026-11-04T10:00:11Z")])
    backend = _PendingBackend(facts)
    rt = Runtime(backend=backend, profiles={"meeting-bot": Runnable(image="vexaai/vexa-bot:test")})
    rt.create(WorkloadSpec(workloadId="mtg-1-a1", profile="meeting-bot", env={}))

    status = rt.get("mtg-1-a1")

    assert status.state is RuntimeState.running
    assert status.pending is not None and status.pending.autoscaler.value == "no_scale_up"
    assert _status_errors(status.model_dump(exclude_none=True, mode="json")) == []
    assert rt.store.get("mtg-1-a1").status.pending is None, "a moment's facts are never persisted"
    backend.facts = None                       # the Pod scheduled and runs
    assert rt.get("mtg-1-a1").pending is None


def test_a_backend_fault_in_pending_leaves_the_answer_as_it_was():
    class _Broken(_PendingBackend):
        def pending(self, h):
            raise RuntimeError("kubectl: connection refused")

    rt = Runtime(backend=_Broken(None), profiles={"meeting-bot": Runnable(image="vexaai/vexa-bot:test")})
    rt.create(WorkloadSpec(workloadId="mtg-1-a1", profile="meeting-bot", env={}))
    status = rt.get("mtg-1-a1")
    assert status.state is RuntimeState.running and status.pending is None


def test_a_torn_down_pod_leaves_no_cached_read(monkeypatch):
    fake = FakeKubectl(_pod(unschedulable=True), [])
    backend = _backend(monkeypatch, fake)
    h = WorkloadHandle(id="mtg-1-a1", impl="vexa-mtg-1-a1")
    for teardown in (backend.cleanup, backend.kill, backend.terminate):
        backend.exit_code(h)
        assert "vexa-mtg-1-a1" in backend._last_read
        teardown(h)
        assert "vexa-mtg-1-a1" not in backend._last_read, teardown.__name__
