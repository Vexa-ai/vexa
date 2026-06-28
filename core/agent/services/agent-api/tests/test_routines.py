"""MVP2 routines eval — the authoring→cron→unit loop, proven over fakes.

The cron TIMING + the HTTP firing of a due job live in the runtime package's test_schedule_api.py
(FakeClock + fakeredis advance past the cron → the request POSTs). HERE we prove the agent-api half:
a routine compiles to a CONFORMANT schedule.v1 job whose body is a unit.v1 dispatch, and firing that
body SPAWNS the unit (the container path). Together they are the L4 claim: "advance past a cron →
dispatch emitted + container spawned."
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from agent_api import contracts, routines as R
from agent_api.api import create_app
from agent_api.config import load_settings
from agent_api.dispatch import Dispatcher
from agent_api.workspace_reader import WorkspaceReader
from agent_api.workspace_routines import reconcile_workspace_routines


class _FakeRuntime:
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


class _FakeScheduler:
    """In-memory SchedulerPort — records scheduled jobs, lists them, cancels by id."""
    def __init__(self):
        self.jobs = []

    def schedule(self, job):
        stored = {**job, "job_id": f"job_{len(self.jobs)}", "status": "pending", "execute_at": 1000.0}
        self.jobs.append(stored)
        return stored

    def list_jobs(self, *, status=None, limit=50):
        return list(self.jobs)

    def cancel_job(self, job_id):
        for j in self.jobs:
            if j["job_id"] == job_id:
                self.jobs.remove(j)
                return j
        return None


def test_compile_produces_conformant_job_and_invocation():
    routine = R.make_routine(
        subject="u_jane", name="Morning brief", cron="0 8 * * *",
        prompt="Summarize my new emails and record follow-ups as tasks.",
    )
    contracts.validate_routine(routine)  # the authored routine is routine.v1-conformant
    job = R.compile_to_job(routine, invocations_url="http://agent-api:8100/invocations")

    assert job["cron"] == "0 8 * * *"
    assert job["request"]["url"].endswith("/invocations")
    assert job["idempotency_key"] == routine["id"]
    # The job body is a unit.v1 dispatch (this is what fires at /invocations when due).
    contracts.validate_unit_invocation(job["request"]["body"])
    assert job["request"]["body"]["trigger"] == "scheduled"
    assert job["request"]["body"]["start"]["entrypoint"]["inline"].startswith("Summarize")


def test_routine_card_round_trips_from_job():
    routine = R.make_routine(subject="u_jane", name="Inbox triage", cron="*/15 * * * *", prompt="triage")
    job = R.compile_to_job(routine, invocations_url="http://x/invocations")
    job = {**job, "job_id": "job_x", "execute_at": 123.0, "status": "pending"}
    card = R.routine_card_from_job(job)
    assert card["id"] == routine["id"]
    assert card["owner"] == "u_jane"
    assert card["name"] == "Inbox triage"
    assert card["cron"] == "*/15 * * * *"
    assert card["next_run"] == 123.0


def test_firing_a_compiled_job_spawns_the_unit():
    """The L4 spawn half: take the compiled job's body (what the cron POSTs) and dispatch it →
    the unit spawns in an isolated container."""
    rt = _FakeRuntime()
    dispatcher = Dispatcher(load_settings(), rt, _FakeIdentity())

    routine = R.make_routine(subject="u_jane", name="Brief", cron="0 9 * * *", prompt="do the brief")
    job = R.compile_to_job(routine, invocations_url="http://x/invocations")

    uid = dispatcher.dispatch(job["request"]["body"])  # simulate the cron firing the request body

    assert uid.startswith("agent-")
    assert rt.spawned and rt.spawned[0][2]["VEXA_OWNER"] == "u_jane"
    assert dispatcher.dispatched and dispatcher.dispatched[0]["trigger"] == "scheduled"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _app(scheduler, *, reader=None):
    dispatcher = Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity())
    return TestClient(create_app(
        dispatcher, scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        reader=reader,
    )), dispatcher


def test_create_and_list_routines_over_http():
    scheduler = _FakeScheduler()
    client, dispatcher = _app(scheduler)

    r = client.post("/api/routines", json={
        "subject": "u_jane", "name": "Morning brief", "cron": "0 8 * * *",
        "prompt": "Summarize overnight emails into tasks.",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["job_id"] == "job_0"
    assert body["ran_now"] is True            # the immediate demo run dispatched the unit
    assert dispatcher.dispatched and dispatcher.dispatched[0]["identity"]["subject"] == "u_jane"
    assert len(scheduler.jobs) == 1           # the cron job is registered

    listed = client.get("/api/routines", params={"subject": "u_jane"}).json()["routines"]
    assert len(listed) == 1 and listed[0]["name"] == "Morning brief"
    assert listed[0]["enabled"] is True
    assert listed[0]["routine_name"] == "Morning brief"

    # A different subject sees none (owner-scoped). Subject is the authenticated X-User-Id (P20).
    assert client.get("/api/routines", headers={"X-User-Id": "u_bob"}).json()["routines"] == []

    # Delete cancels the scheduled job.
    rid = body["routine"]["id"]
    assert client.delete(f"/api/routines/{rid}", params={"subject": "u_jane"}).status_code == 200
    assert scheduler.jobs == []


def test_routines_501_when_scheduler_not_wired():
    dispatcher = Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity())
    client = TestClient(create_app(dispatcher))  # no scheduler
    assert client.post("/api/routines", json={
        "subject": "u_jane", "name": "x", "cron": "* * * * *", "prompt": "y",
    }).status_code == 501
    assert client.get("/api/routines", params={"subject": "u_jane"}).json() == {"routines": []}


def test_workspace_routine_enabled_patch_rewrites_file_and_reconciles(tmp_path):
    workspaces = tmp_path / "workspaces"
    routine_path = workspaces / "u_jane" / "routines" / "brief.md"
    _write(
        routine_path,
        "---\n"
        "enabled: true\n"
        "cron: '0 9 * * *'\n"
        "prompt: Do the brief.\n"
        "---\n"
        "Use the workspace context.\n",
    )
    scheduler = _FakeScheduler()
    client, _ = _app(scheduler, reader=WorkspaceReader(str(workspaces)))
    reconcile_workspace_routines(
        "u_jane",
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces,
    )
    assert len(scheduler.jobs) == 1

    listed = client.get("/api/routines", params={"subject": "u_jane"}).json()["routines"]
    assert listed == [{
        "id": listed[0]["id"],
        "owner": "u_jane",
        "name": "brief",
        "cron": "0 9 * * *",
        "kind": "scheduled",
        "lifecycle": "oneshot",
        "plan_kind": "prompt",
        "plan_summary": "Do the brief.\n\nUse the workspace context.",
        "job_id": "job_0",
        "next_run": 1000.0,
        "status": "pending",
        "routine_name": "brief",
        "enabled": True,
    }]

    disabled = client.patch(
        "/api/routines/brief/enabled",
        params={"subject": "u_jane"},
        json={"enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["reconcile"]["cancelled"] == 1
    assert "enabled: false\n" in routine_path.read_text()
    assert scheduler.jobs == []

    disabled_list = client.get("/api/routines", params={"subject": "u_jane"}).json()["routines"]
    assert len(disabled_list) == 1
    assert disabled_list[0]["routine_name"] == "brief"
    assert disabled_list[0]["enabled"] is False
    assert disabled_list[0]["status"] == "disabled"
    assert disabled_list[0]["job_id"] is None

    enabled = client.patch(
        "/api/routines/brief/enabled",
        params={"subject": "u_jane"},
        json={"enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["reconcile"]["scheduled"] == 1
    assert "enabled: true\n" in routine_path.read_text()
    assert len(scheduler.jobs) == 1

    enabled_list = client.get("/api/routines", params={"subject": "u_jane"}).json()["routines"]
    assert enabled_list[0]["enabled"] is True
    assert enabled_list[0]["job_id"] == "job_0"

    absent = client.patch(
        "/api/routines/missing/enabled",
        params={"subject": "u_jane"},
        json={"enabled": False},
    )
    assert absent.status_code == 404
