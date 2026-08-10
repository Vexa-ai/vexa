"""#718 C1 — a workload that is DEAD AT START must not read as spawned.

The docker backend raises loudly when the image is absent (``docker create … (404): No such
image``). The kernel records the honest ``stopped``/``start_failed`` state (so ``GET /workloads``
and the callback stream stay truthful) but must NOT answer the create as success: the kernel
raises ``StartFailed`` and the HTTP API maps it to a 502 that NAMES the cause, logging
``workload_spawn_failed`` instead of ``workload_spawned``.

Negative control (the bug this fixes): before the fix, ``create()`` caught the start failure and
RETURNED the stopped status, so ``POST /workloads`` answered 201 + ``workload_spawned`` over a
workload that never came up.
"""
import json

from fastapi.testclient import TestClient

from runtime_kernel import Runtime, RuntimeState, StartFailed, StopReason, WorkloadSpec
from runtime_kernel.api import create_app
from runtime_kernel.backend import WorkloadHandle

_MISSING_IMAGE_MSG = "docker create vexa-w1 failed (404): No such image: vexaai/vexa-bot:dev"


class _DeadAtStartBackend:
    """A backend whose ``start`` raises exactly as the docker backend does for an absent image."""

    name = "docker"

    def start(self, workload_id, runnable, env):
        raise RuntimeError(_MISSING_IMAGE_MSG)

    def exit_code(self, h):  # pragma: no cover — never reached (start raised)
        return None

    def terminate(self, h):  # pragma: no cover
        pass

    def kill(self, h):  # pragma: no cover
        pass

    def cleanup(self, h):  # pragma: no cover
        pass

    def find(self, workload_id):  # pragma: no cover
        return None


def test_create_raises_start_failed_but_records_the_honest_state():
    """Kernel level: ``create`` RAISES ``StartFailed`` (carrying the backend reason), yet the
    persisted record and the emitted event are the honest ``stopped``/``start_failed`` — the
    truthful record is kept, the false success is refused."""
    events = []
    rt = Runtime(backend=_DeadAtStartBackend(), profiles={"bot": ["run"]}, on_event=events.append)

    try:
        rt.create(WorkloadSpec(workloadId="w1", profile="bot", env={}))
        assert False, "expected StartFailed"
    except StartFailed as e:
        assert "No such image" in str(e)

    # The record is honest — GET /workloads must still tell the truth.
    rec = rt.store.get("w1")
    assert rec.status.state is RuntimeState.stopped
    assert rec.status.stopReason is StopReason.start_failed
    # The lifecycle stream saw starting → stopped(start_failed), never running.
    assert [e.state.value for e in events] == ["starting", "stopped"]
    assert events[-1].stopReason is StopReason.start_failed


def test_post_workloads_dead_at_start_is_502_not_a_false_201(capsys):
    """API level (the point of introduction): ``POST /workloads`` for a dead-at-start workload is a
    502 naming the missing image — NOT a bare 201 — and logs ``workload_spawn_failed``, never
    ``workload_spawned``."""
    app = create_app(Runtime(backend=_DeadAtStartBackend(), profiles={"bot": ["run"]}))
    client = TestClient(app)

    r = client.post("/workloads", json={"workloadId": "w1", "profile": "bot", "env": {}})

    assert r.status_code == 502, f"a dead-at-start spawn must not 201; got {r.status_code}"
    assert "No such image" in r.json()["detail"]

    events = [json.loads(line)["event"] for line in capsys.readouterr().out.splitlines() if line.strip().startswith("{")]
    assert "workload_spawn_failed" in events
    assert "workload_spawned" not in events, "the success log must not fire over a dead workload"


def test_post_workloads_healthy_spawn_still_201():
    """Negative control the other way: a backend that starts cleanly still answers 201 + running —
    the fix does not turn healthy spawns into 502s."""

    class _OkBackend(_DeadAtStartBackend):
        def start(self, workload_id, runnable, env):
            return WorkloadHandle(id=workload_id, impl=workload_id)

        def exit_code(self, h):
            return None

    app = create_app(Runtime(backend=_OkBackend(), profiles={"bot": ["run"]}))
    r = TestClient(app).post("/workloads", json={"workloadId": "w1", "profile": "bot", "env": {}})
    assert r.status_code == 201 and r.json()["state"] == "running"
