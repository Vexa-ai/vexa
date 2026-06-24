"""MVP2 routines eval — the authoring→cron→unit loop, proven in-process.

The cron TIMING + the HTTP firing of a due job live in the runtime package's test_schedule_api.py
(FakeClock + fakeredis advance past the cron → the request POSTs). HERE we prove the agent-api half:
a routine compiles to a CONFORMANT schedule.v1 job whose body is a unit.v1 Invocation, and firing that
body runs the unit over the subject's workspace and COMMITS (the governance path). Together they are the
L4 claim: "advance past a cron → invoke emitted + commit landed."
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from agent_api import contracts, routines as R
from agent_api.api import create_app
from agent_api.config import load_settings
from agent_api.dispatch import Dispatcher


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeRunner:
    """Stands in for the in-container claude turn: records the call and 'commits'."""
    def __init__(self):
        self.calls = []

    def run(self, prompt, *, subject, session=None):
        self.calls.append({"prompt": prompt, "subject": subject})
        yield {"type": "message-delta", "text": "done"}
        yield {"type": "commit", "sha": "deadbeef"}


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
    job = R.compile_to_job(routine, invocations_url="http://agent-api:8100/invocations", workspace_repo="local:u_jane")

    assert job["cron"] == "0 8 * * *"
    assert job["request"]["url"].endswith("/invocations")
    assert job["idempotency_key"] == routine["id"]
    # The job body is a unit.v1 Invocation (this is what fires at /invocations when due).
    contracts.validate_unit_invocation(job["request"]["body"])
    assert job["request"]["body"]["trigger"] == "scheduled"
    assert job["request"]["body"]["plan"]["prompt"].startswith("Summarize")


def test_routine_card_round_trips_from_job():
    routine = R.make_routine(subject="u_jane", name="Inbox triage", cron="*/15 * * * *", prompt="triage")
    job = R.compile_to_job(routine, invocations_url="http://x/invocations", workspace_repo="local:u_jane")
    job = {**job, "job_id": "job_x", "execute_at": 123.0, "status": "pending"}
    card = R.routine_card_from_job(job)
    assert card["id"] == routine["id"]
    assert card["owner"] == "u_jane"
    assert card["name"] == "Inbox triage"
    assert card["cron"] == "*/15 * * * *"
    assert card["next_run"] == 123.0


def test_firing_a_compiled_job_runs_the_unit_and_commits():
    """The L4 commit-landed half: take the compiled job's body (what the cron POSTs) and dispatch it →
    the unit runs in-container and commits."""
    runner = _FakeRunner()
    dispatcher = Dispatcher(load_settings(), _FakeRuntime(), local_runner=runner, local_sync=True)

    routine = R.make_routine(subject="u_jane", name="Brief", cron="0 9 * * *", prompt="do the brief")
    job = R.compile_to_job(routine, invocations_url="http://x/invocations", workspace_repo="local:u_jane")

    uid = dispatcher.dispatch(job["request"]["body"])  # simulate the cron firing the request body

    assert uid.startswith("agent-")
    assert runner.calls == [{"prompt": "do the brief", "subject": "u_jane"}]
    assert dispatcher.dispatched and dispatcher.dispatched[0]["trigger"] == "scheduled"


def _app(scheduler, runner):
    dispatcher = Dispatcher(load_settings(), _FakeRuntime(), local_runner=runner, local_sync=True)
    return TestClient(create_app(
        dispatcher, scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
    )), dispatcher


def test_create_and_list_routines_over_http():
    scheduler, runner = _FakeScheduler(), _FakeRunner()
    client, dispatcher = _app(scheduler, runner)

    r = client.post("/api/routines", json={
        "subject": "u_jane", "name": "Morning brief", "cron": "0 8 * * *",
        "prompt": "Summarize overnight emails into tasks.",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["job_id"] == "job_0"
    assert body["ran_now"] is True            # the immediate demo run fired the unit
    assert runner.calls and runner.calls[0]["subject"] == "u_jane"
    assert len(scheduler.jobs) == 1           # the cron job is registered

    listed = client.get("/api/routines", params={"subject": "u_jane"}).json()["routines"]
    assert len(listed) == 1 and listed[0]["name"] == "Morning brief"

    # A different subject sees none (owner-scoped).
    assert client.get("/api/routines", params={"subject": "u_bob"}).json()["routines"] == []

    # Delete cancels the scheduled job.
    rid = body["routine"]["id"]
    assert client.delete(f"/api/routines/{rid}", params={"subject": "u_jane"}).status_code == 200
    assert scheduler.jobs == []


def test_routines_501_when_scheduler_not_wired():
    runner = _FakeRunner()
    dispatcher = Dispatcher(load_settings(), _FakeRuntime(), local_runner=runner, local_sync=True)
    client = TestClient(create_app(dispatcher))  # no scheduler
    assert client.post("/api/routines", json={
        "subject": "u_jane", "name": "x", "cron": "* * * * *", "prompt": "y",
    }).status_code == 501
    assert client.get("/api/routines", params={"subject": "u_jane"}).json() == {"routines": []}
