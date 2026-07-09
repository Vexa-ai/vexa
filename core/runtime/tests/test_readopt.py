"""Boot re-adoption (the orphaned-live-bot fix) — pure unit tests, NO docker daemon / cluster.

The incident: a live meeting bot's container survived a runtime recreate untouched, but the fresh
runtime's in-memory registry knew nothing about it — ``GET /workloads/{id}`` 404'd, the control
plane misread the 404 as "bot gone", completed the meeting, and its ``DELETE`` 404'd too, leaving a
ghost attendee capturing audio with no way to stop it from the UI.

These tests prove, against a FAKE docker socket / fake kubectl:
  • the DockerBackend discovers its own workload containers (label-first, guarded name fallback);
  • the kernel re-adopts them: GET answers truthfully post-restart, stop/destroy reach the REAL
    container again (via re-attached handles / the ``find`` fallback);
  • an EXITED container adopts as ``stopped`` with its real exit code — evidence, not amnesia;
  • the K8sBackend stamps adoption labels and discovers by them.
"""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from fastapi.testclient import TestClient

from runtime_kernel import Runtime
from runtime_kernel.api import create_app
from runtime_kernel.backend import WorkloadHandle
from runtime_kernel.docker_backend import DockerBackend
from runtime_kernel.models import RuntimeState, WorkloadSpec, WorkloadStatus
from runtime_kernel.store import InMemoryStore, WorkloadRecord


# ── a fake requests_unixsocket session over a canned docker daemon ───────────────────────────────
class _Resp:
    def __init__(self, status_code: int, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeDockerSession:
    """Answers the exact socket-API calls DockerBackend makes, from a canned container table.

    ``containers`` : name(leaf) -> {"labels": {...}, "running": bool, "exit_code": int|None,
    "network": str|None (the compose network the container is attached to)}
    """

    def __init__(self, containers: dict[str, dict]):
        self.containers = containers
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kw):
        parsed = urlparse(url)
        path = unquote(parsed.path)
        # strip the encoded socket prefix requests_unixsocket bakes into the netloc-less path
        path = path[path.index("/containers"):] if "/containers" in path else path
        self.calls.append((method, path + ("?" + parsed.query if parsed.query else "")))
        if method == "GET" and path == "/containers/json":
            q = parse_qs(parsed.query)
            entries = []
            label_filter = None
            network_filter = None
            if "filters" in q:
                filters = json.loads(q["filters"][0])
                label_filter = filters.get("label", [])
                network_filter = filters.get("network", [])
            for name, c in self.containers.items():
                labels = c.get("labels", {})
                if label_filter and not all(
                    labels.get(k) == v for k, v in (f.split("=", 1) for f in label_filter)
                ):
                    continue
                # the daemon's network filter: only containers attached to that network match
                if network_filter and c.get("network") not in network_filter:
                    continue
                entries.append({
                    "Names": [f"/{name}"],
                    "Labels": labels,
                    "State": "running" if c.get("running") else "exited",
                })
            return _Resp(200, entries)
        if method == "GET" and path.endswith("/json"):        # inspect
            name = path.split("/")[2]
            c = self.containers.get(name)
            if c is None:
                return _Resp(404, {"message": "no such container"})
            net = c.get("network")
            return _Resp(200, {
                "State": {
                    "Running": bool(c.get("running")),
                    "ExitCode": c.get("exit_code", 0),
                },
                "HostConfig": {"NetworkMode": net or "default"},
                "NetworkSettings": {"Networks": {net: {}} if net else {}},
            })
        if method == "POST" and "/stop" in path:
            name = path.split("/")[2]
            c = self.containers.get(name)
            if c is None:
                return _Resp(404, {"message": "no such container"})
            c["running"] = False
            c.setdefault("exit_code", 0)
            return _Resp(204)
        if method == "DELETE":
            name = path.split("/")[2]
            if self.containers.pop(name, None) is None:
                return _Resp(404, {"message": "no such container"})
            return _Resp(204)
        return _Resp(500, {"message": f"unhandled {method} {path}"})


def _backend(containers: dict[str, dict]) -> tuple[DockerBackend, FakeDockerSession]:
    be = DockerBackend()
    fake = FakeDockerSession(containers)
    be._session = fake  # inject the fake socket
    return be, fake


LABELS = {"runtime.managed": "true", "runtime.workload_id": "mtg-2-d93eee39"}


# ── DockerBackend discovery ──────────────────────────────────────────────────────────────────────
def test_docker_discovers_labelled_running_container():
    be, _ = _backend({"vexa-mtg-2-d93eee39": {"labels": dict(LABELS), "running": True}})
    found = be.list_workload_containers()
    assert found == [{
        "workload_id": "mtg-2-d93eee39", "name": "vexa-mtg-2-d93eee39",
        "running": True, "exit_code": None,
    }]


def test_docker_discovers_exited_container_with_exit_code():
    be, _ = _backend({
        "vexa-mtg-9-dead": {
            "labels": {"runtime.managed": "true", "runtime.workload_id": "mtg-9-dead"},
            "running": False, "exit_code": 137,
        },
    })
    (info,) = be.list_workload_containers()
    assert info["running"] is False
    assert info["exit_code"] == 137                    # real evidence, via inspect


def test_docker_name_fallback_adopts_labelless_stray_but_never_compose_services():
    be, _ = _backend({
        # a pre-label stray spawned by an older runtime — adoptable by name
        "vexa-mtg-1-38a5a399": {"labels": {}, "running": True},
        # an agent worker stray: leaf worker-* maps back to the agent-* workload id
        "vexa-worker-meet-ab12-chat": {"labels": {}, "running": True},
        # the compose stack's own services share the prefix — MUST NOT be adopted
        "vexa-meeting-api-1": {
            "labels": {"com.docker.compose.project": "vexa"}, "running": True,
        },
        # unrelated container, different prefix — ignored
        "someone-elses": {"labels": {}, "running": True},
    })
    ids = sorted(i["workload_id"] for i in be.list_workload_containers())
    assert ids == ["agent-meet-ab12-chat", "mtg-1-38a5a399"]


def test_docker_find_rederives_handle_and_404_means_absent():
    be, _ = _backend({"vexa-mtg-2-d93eee39": {"labels": dict(LABELS), "running": True}})
    h = be.find("mtg-2-d93eee39")
    assert h is not None and h._impl == "vexa-mtg-2-d93eee39"
    assert be.find("mtg-nope") is None


# ── stack scoping on a SHARED daemon (two vexa stacks, one docker) ───────────────────────────────
# The managed label and the vexa- name prefix are the SAME constants in every stack, so on a shared
# daemon (the release host: prod + eyeball) label-only discovery adopts the OTHER stack's live bots —
# and a network-blind find() would let this stack's stop/destroy reach them. DOCKER_NETWORK (the same
# env start() uses for HostConfig.NetworkMode) is the stack discriminator: it scopes both passes of
# discovery AND find(), and works retroactively for label-less incident-era containers.

def test_docker_discovery_is_scoped_to_the_stack_network(monkeypatch):
    """Two containers, SAME labels, different networks: only the one on THIS stack's network is
    adopted — the foreign stack's bot stays invisible."""
    monkeypatch.setenv("DOCKER_NETWORK", "vexa_prod_default")
    be, _ = _backend({
        "vexa-mtg-2-d93eee39": {
            "labels": dict(LABELS), "running": True, "network": "vexa_prod_default",
        },
        "vexa-mtg-7-eyeball": {
            "labels": {"runtime.managed": "true", "runtime.workload_id": "mtg-7-eyeball"},
            "running": True, "network": "vexa_eyeball_default",   # the OTHER stack's bot
        },
    })
    found = be.list_workload_containers()
    assert [i["workload_id"] for i in found] == ["mtg-2-d93eee39"]


def test_docker_name_fallback_is_scoped_to_the_stack_network(monkeypatch):
    """The label-less name-fallback pass (incident-era strays) is network-scoped too — a foreign
    stack's pre-label stray with the shared vexa- prefix is never adopted."""
    monkeypatch.setenv("DOCKER_NETWORK", "vexa_prod_default")
    be, _ = _backend({
        "vexa-mtg-1-38a5a399": {"labels": {}, "running": True, "network": "vexa_prod_default"},
        "vexa-mtg-6-foreign": {"labels": {}, "running": True, "network": "vexa_eyeball_default"},
    })
    ids = [i["workload_id"] for i in be.list_workload_containers()]
    assert ids == ["mtg-1-38a5a399"]


def test_docker_find_never_reaches_a_foreign_network_container(monkeypatch):
    """find() must not re-derive a handle for a same-named container in ANOTHER stack's network —
    otherwise this stack's stop/destroy would reach the foreign stack's live bot with a 204."""
    monkeypatch.setenv("DOCKER_NETWORK", "vexa_prod_default")
    be, _ = _backend({
        "vexa-mtg-2-d93eee39": {
            "labels": dict(LABELS), "running": True, "network": "vexa_eyeball_default",
        },
    })
    assert be.find("mtg-2-d93eee39") is None            # exists, but not ours to touch

    # …and the same container IS findable when it is on OUR network.
    monkeypatch.setenv("DOCKER_NETWORK", "vexa_eyeball_default")
    h = be.find("mtg-2-d93eee39")
    assert h is not None and h._impl == "vexa-mtg-2-d93eee39"


def test_docker_discovery_unscoped_when_no_network_configured(monkeypatch):
    """Back-compat: without DOCKER_NETWORK (single-stack / default bridge) discovery and find are
    unscoped — exactly the pre-fix behavior."""
    monkeypatch.delenv("DOCKER_NETWORK", raising=False)
    be, _ = _backend({
        "vexa-mtg-2-d93eee39": {"labels": dict(LABELS), "running": True, "network": "net-a"},
        "vexa-mtg-9-other": {
            "labels": {"runtime.managed": "true", "runtime.workload_id": "mtg-9-other"},
            "running": True, "network": "net-b",
        },
    })
    ids = sorted(i["workload_id"] for i in be.list_workload_containers())
    assert ids == ["mtg-2-d93eee39", "mtg-9-other"]
    assert be.find("mtg-2-d93eee39") is not None


def test_docker_cleanup_raises_when_delete_fails():
    """`destroyed` must never be a lie: an unconfirmed reclaim raises instead of passing silently."""
    be, fake = _backend({})

    class _Boom(FakeDockerSession):
        def request(self, method, url, **kw):
            if method == "DELETE":
                return _Resp(500, {"message": "daemon exploded"})
            return super().request(method, url, **kw)

    be._session = _Boom({})
    try:
        be.cleanup(WorkloadHandle(id="w", impl="vexa-w"))
        assert False, "expected cleanup to raise on an unconfirmed delete"
    except RuntimeError:
        pass


# ── kernel re-adoption (the registry half of the fix) ────────────────────────────────────────────
def _restarted_runtime(containers: dict[str, dict]) -> tuple[Runtime, FakeDockerSession]:
    """A FRESH Runtime (empty in-memory store + handle map — exactly the post-recreate state)
    over a substrate that still has the containers."""
    be, fake = _backend(containers)
    rt = Runtime(backend=be, profiles={}, grace_sec=0.1)
    return rt, fake


def test_adopt_makes_get_truthful_after_restart():
    """THE INCIDENT, FIXED: after a runtime recreate, GET /workloads/{id} must answer `running`
    for a still-running bot container — never 404 over a live bot."""
    rt, _ = _restarted_runtime({"vexa-mtg-2-d93eee39": {"labels": dict(LABELS), "running": True}})
    assert rt.adopt() == 1
    status = rt.get("mtg-2-d93eee39")
    assert status.state is RuntimeState.running
    assert status.profile == "adopted"

    # …and over HTTP, exactly what meeting-api polls:
    client = TestClient(create_app(rt))
    r = client.get("/workloads/mtg-2-d93eee39")
    assert r.status_code == 200, "recreated runtime must not 404 a live workload"
    assert r.json()["state"] == "running"


def test_adopt_exited_container_reports_stopped_with_evidence():
    """A container that EXITED while the runtime was down adopts as `stopped` + its real exit
    code — the evidence the control plane's lifecycle needs (vs the old blanket 404)."""
    rt, _ = _restarted_runtime({
        "vexa-mtg-9-dead": {
            "labels": {"runtime.managed": "true", "runtime.workload_id": "mtg-9-dead"},
            "running": False, "exit_code": 1,
        },
    })
    assert rt.adopt() == 1
    status = rt.get("mtg-9-dead")
    assert status.state is RuntimeState.stopped
    assert status.exitCode == 1


def test_stop_after_adoption_reaches_the_real_container():
    """A post-restart stop must terminate the REAL container (not just flip registry state)."""
    containers = {"vexa-mtg-2-d93eee39": {"labels": dict(LABELS), "running": True}}
    rt, fake = _restarted_runtime(containers)
    rt.adopt()
    status = rt.stop("mtg-2-d93eee39")
    assert status.state is RuntimeState.stopped
    assert containers["vexa-mtg-2-d93eee39"]["running"] is False      # actually stopped
    assert any(m == "POST" and "/stop" in p for m, p in fake.calls)


def test_destroy_after_adoption_removes_the_real_container():
    containers = {"vexa-mtg-2-d93eee39": {"labels": dict(LABELS), "running": True}}
    rt, _ = _restarted_runtime(containers)
    rt.adopt()
    assert rt.destroy("mtg-2-d93eee39").state is RuntimeState.destroyed
    assert "vexa-mtg-2-d93eee39" not in containers                    # actually removed


def test_stop_without_adoption_still_finds_the_container_via_find():
    """Belt-and-braces: even if adopt() was skipped, a stop on a store-known workload re-derives
    the handle from the substrate (backend.find) instead of no-op'ing over a live container."""
    containers = {"vexa-mtg-2-d93eee39": {"labels": dict(LABELS), "running": True}}
    be, _ = _backend(containers)
    store = InMemoryStore()
    spec = WorkloadSpec(workloadId="mtg-2-d93eee39", profile="meeting-bot", env={})
    store.set(WorkloadRecord(
        spec=spec,
        status=WorkloadStatus(workloadId="mtg-2-d93eee39", profile="meeting-bot",
                              state=RuntimeState.running, backend="docker"),
        owner="",
    ))
    rt = Runtime(backend=be, profiles={}, store=store, grace_sec=0.1)   # durable store, no handles
    status = rt.stop("mtg-2-d93eee39")
    assert status.state is RuntimeState.stopped
    assert containers["vexa-mtg-2-d93eee39"]["running"] is False


def test_adopt_preserves_records_a_durable_store_kept():
    """With a durable (redis) store the record survives — adoption must only re-attach the live
    handle, never clobber the real spec with the synthesized 'adopted' one."""
    containers = {"vexa-mtg-2-d93eee39": {"labels": dict(LABELS), "running": True}}
    be, _ = _backend(containers)
    store = InMemoryStore()
    spec = WorkloadSpec(workloadId="mtg-2-d93eee39", profile="meeting-bot", env={"VEXA_OWNER": "u1"})
    store.set(WorkloadRecord(
        spec=spec,
        status=WorkloadStatus(workloadId="mtg-2-d93eee39", profile="meeting-bot",
                              state=RuntimeState.running, backend="docker"),
        owner="u1",
    ))
    rt = Runtime(backend=be, profiles={}, store=store, grace_sec=0.1)
    assert rt.adopt() == 0                              # nothing re-registered…
    assert rt.get("mtg-2-d93eee39").profile == "meeting-bot"   # …spec intact
    assert "mtg-2-d93eee39" in rt._handles              # …but the handle is live again


def test_adopt_is_idempotent():
    rt, _ = _restarted_runtime({"vexa-mtg-2-d93eee39": {"labels": dict(LABELS), "running": True}})
    assert rt.adopt() == 1
    assert rt.adopt() == 0
    assert len(rt.list()) == 1


def test_adopt_noop_on_backends_without_discovery():
    """ProcessBackend has no discovery — adopt() is a clean no-op (processes died with the old
    runtime process anyway; there is nothing on the substrate to re-adopt)."""
    rt = Runtime(profiles={})
    assert rt.adopt() == 0


# ── K8sBackend: labels stamped at start; discovery by label ─────────────────────────────────────
def test_k8s_start_stamps_adoption_labels(monkeypatch):
    from runtime_kernel import k8s_backend

    calls: list[tuple[str, ...]] = []

    def fake_kubectl(*args: str, check: bool = True):
        calls.append(args)
        class R:  # noqa: N801 — minimal stand-in
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(k8s_backend, "_kubectl", fake_kubectl)
    from runtime_kernel.profiles import Runnable

    be = k8s_backend.K8sBackend()
    be.start("mtg-2-d93eee39", Runnable(image="img"), {})
    (run_args,) = calls
    assert "--labels=runtime.managed=true,runtime.workload_id=mtg-2-d93eee39" in run_args


def test_k8s_discovers_pods_by_label(monkeypatch):
    from runtime_kernel import k8s_backend

    pods = {"items": [
        {"metadata": {"name": "vexa-mtg-2-d93eee39",
                      "labels": {"runtime.managed": "true",
                                 "runtime.workload_id": "mtg-2-d93eee39"}},
         "status": {"phase": "Running"}},
        {"metadata": {"name": "vexa-mtg-9-dead",
                      "labels": {"runtime.managed": "true",
                                 "runtime.workload_id": "mtg-9-dead"}},
         "status": {"phase": "Failed",
                    "containerStatuses": [{"state": {"terminated": {"exitCode": 137}}}]}},
    ]}

    def fake_kubectl(*args: str, check: bool = True):
        class R:  # noqa: N801
            returncode = 0
            stdout = json.dumps(pods)
            stderr = ""
        assert "-l" in args and "runtime.managed=true" in args
        return R()

    monkeypatch.setattr(k8s_backend, "_kubectl", fake_kubectl)
    be = k8s_backend.K8sBackend()
    found = {i["workload_id"]: i for i in be.list_workload_containers()}
    assert found["mtg-2-d93eee39"]["running"] is True
    assert found["mtg-9-dead"] == {
        "workload_id": "mtg-9-dead", "name": "vexa-mtg-9-dead",
        "running": False, "exit_code": 137,
    }
