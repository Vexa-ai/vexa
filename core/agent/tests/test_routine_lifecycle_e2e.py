"""End-to-end durable-routine lifecycle proof (CREATION + EXECUTION) over in-memory fakes.

This is a NEW test file (does not touch shared src or existing tests). It drives the full chain a
workspace-authored routine travels:

    routines/<name>.md  --reconcile-->  schedule.v1 job in the scheduler   [CREATION]
    schedule.v1 job  --tick fires-->  POST /invocations  --dispatch-->  unit spawn  [EXECUTION]

The runtime's durable cron (``runtime_kernel.Scheduler``) lives in a sibling service with its own
deps (croniter/redis) that are not installed in agent-api's venv, so we cannot import it here. Instead
we reproduce the tick semantics faithfully: the runtime tick does ``ZRANGEBYSCORE now`` then HTTP-POSTs
``job["request"]`` to ``invocations_url`` (see core/runtime/src/runtime_kernel/scheduler.py:212-220 and
core/runtime/src/runtime_kernel/__main__.py:24-48 ``_http_dispatch``). We POST that exact request body
to the real agent-api ``/invocations`` route (api.py:241) over a TestClient and assert the routine's
prompt reaches the spawned unit's env (``VEXA_START``).
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from control_plane.api import create_app
from shared.config import load_settings
from control_plane.dispatch import Dispatcher
from control_plane.workspace_routines import reconcile_workspace_routines
from tests.test_routines import _FakeScheduler


ROUTINE_PROMPT = "Append the current UTC timestamp as a new bullet line to notes/heartbeat-log.md"
INVOCATIONS_URL = "http://agent-api:8100/invocations"


class _RecordingRuntime:
    """Captures spawned (workload_id, profile, env) so we can assert the prompt propagated."""

    def __init__(self):
        self.spawned = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append((workload_id, profile, env))
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _write_routine(workspaces, subject, name, body):
    p = workspaces / subject / "routines" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_full_routine_lifecycle_creation_then_execution(tmp_path):
    workspaces = tmp_path / "workspaces"
    _write_routine(
        workspaces,
        "u_live",
        "heartbeat",
        "---\n"
        "enabled: true\n"
        "cron: '* * * * *'\n"
        f"prompt: {ROUTINE_PROMPT}\n"
        "---\n",
    )

    # ── CREATION ──────────────────────────────────────────────────────────────────────────────────
    scheduler = _FakeScheduler()
    result = reconcile_workspace_routines(
        "u_live",
        scheduler=scheduler,
        invocations_url=INVOCATIONS_URL,
        workspaces_dir=workspaces,
    )

    assert result.scanned == 1
    assert result.scheduled == 1, "reconcile must create exactly one schedule.v1 job"
    assert len(scheduler.jobs) == 1
    job = scheduler.jobs[0]

    # It is a schedule.v1 cron job whose request POSTs a unit.v1 invocation to /invocations.
    assert job["cron"] == "* * * * *"
    assert job["request"]["method"] == "POST"
    assert job["request"]["url"] == INVOCATIONS_URL
    assert job["metadata"]["source"] == "workspace-routine"
    assert job["metadata"]["owner"] == "u_live"
    assert job["metadata"]["name"] == "heartbeat"
    # The routine's prompt is carried inline in the unit.v1 dispatch body.
    assert job["request"]["body"]["start"]["entrypoint"]["inline"] == ROUTINE_PROMPT
    assert job["request"]["body"]["trigger"] == "scheduled"

    # Reconcile is idempotent — a second pass keeps the job, schedules nothing new.
    second = reconcile_workspace_routines(
        "u_live", scheduler=scheduler, invocations_url=INVOCATIONS_URL, workspaces_dir=workspaces
    )
    assert second.scheduled == 0 and second.kept == 1 and len(scheduler.jobs) == 1

    # ── EXECUTION ─────────────────────────────────────────────────────────────────────────────────
    # Simulate the durable tick firing the due job: it POSTs job["request"]["body"] to /invocations.
    runtime = _RecordingRuntime()
    client = TestClient(
        create_app(Dispatcher(load_settings(), runtime, _FakeIdentity()))
    )

    fired_body = job["request"]["body"]
    resp = client.post("/invocations", json=fired_body)

    assert resp.status_code == 202, resp.text
    workload_id = resp.json()["workload_id"]

    # A unit spawned, and it carries the routine's prompt (VEXA_START holds the entrypoint).
    assert len(runtime.spawned) == 1, "the fired routine must spawn exactly one unit"
    spawned_id, _profile, env = runtime.spawned[0]
    assert spawned_id == workload_id
    assert env["VEXA_OWNER"] == "u_live"
    assert env["VEXA_UNIT_TRIGGER"] == "scheduled"
    start = json.loads(env["VEXA_START"])
    assert start["entrypoint"]["inline"] == ROUTINE_PROMPT, (
        "the spawned unit must run the routine's authored prompt"
    )
