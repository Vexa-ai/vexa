"""schedule.v1 over HTTP — the runtime API's /schedule surface (the control plane registers routine
jobs here). Deterministic via fakeredis + FakeClock + a capturing dispatch; the production ticker
thread is replaced by explicit scheduler.tick() calls."""
import fakeredis
from fastapi.testclient import TestClient

from runtime_kernel import FakeClock, Scheduler
from runtime_kernel.api import create_app
from runtime_kernel.kernel import Runtime


def _client(dispatch, clock):
    sched = Scheduler(fakeredis.FakeStrictRedis(decode_responses=True), dispatch=dispatch, clock=clock)
    app = create_app(Runtime(), scheduler=sched)
    return TestClient(app), sched


def test_schedule_register_list_and_fire():
    captured = []
    clock = FakeClock(start=1000.0)
    client, sched = _client(lambda req: captured.append(req) or {"status_code": 202}, clock)

    # health reports the scheduler live.
    assert client.get("/health").json()["checks"]["scheduler"] is True

    # Register a cron job whose request is a unit.v1 Invocation POSTed to agent-api /invocations.
    body = {"trigger": "scheduled", "subject": "u_jane", "workspace_repo": "/repo"}
    r = client.post("/schedule", json={
        "cron": "* * * * *",
        "request": {"url": "http://agent-api:8100/invocations", "body": body},
        "metadata": {"routine_id": "rt_demo"},
    })
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["job_id"].startswith("job_")

    # It is listed as pending and has not fired yet.
    listed = client.get("/schedule").json()
    assert any(j["job_id"] == job["job_id"] for j in listed)
    assert captured == []

    # Reach the next minute boundary → tick fires the dispatch with our Invocation body.
    clock.set(job["execute_at"])
    sched.tick()
    assert len(captured) == 1
    assert captured[0]["url"] == "http://agent-api:8100/invocations"
    assert captured[0]["body"]["subject"] == "u_jane"


def test_schedule_bad_spec_is_400():
    client, _ = _client(lambda req: {"status_code": 202}, FakeClock(start=0.0))
    # No request.url → the scheduler raises ValueError → 400 (fail loud).
    r = client.post("/schedule", json={"cron": "* * * * *", "request": {}})
    assert r.status_code == 400


def test_schedule_503_when_not_wired():
    client = TestClient(create_app(Runtime()))  # no scheduler
    assert client.post("/schedule", json={"cron": "* * * * *", "request": {"url": "http://x/y"}}).status_code == 503
    assert client.get("/health").json()["checks"].get("scheduler") is None
