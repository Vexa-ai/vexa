"""O-RT-2 scheduler eval — the redis sorted-set scheduler, deterministic via fakeredis + FakeClock.
No background asyncio task, no wall clock: schedule(), advance the clock, tick(), inspect.

Covers the parent scheduler's real behaviour:
  • due-firing — a job scheduled for the future does NOT fire until the clock reaches execute_at, then
    tick() dispatches the captured request;
  • cron re-arm — a cron job re-schedules itself after a successful run;
  • retry/backoff — a dispatch that fails (e.g. 500) is retried up to max_attempts, then marked failed;
  • idempotency — a duplicate idempotency_key returns the existing job (no second schedule).
"""
import fakeredis

from runtime_kernel import DispatchError, FakeClock, Scheduler
from runtime_kernel.scheduler import JOBS_KEY


def _scheduler(dispatch, clock):
    return Scheduler(fakeredis.FakeStrictRedis(decode_responses=True), dispatch=dispatch, clock=clock)


def test_one_shot_fires_only_when_due():
    captured = []
    clock = FakeClock(start=1000.0)
    sched = _scheduler(lambda req: captured.append(req) or {"status_code": 200}, clock)

    job = sched.schedule(
        {
            "execute_at": 1300.0,
            "request": {"method": "POST", "url": "http://svc/dispatch", "body": {"x": 1}},
        }
    )
    assert job["status"] == "pending"

    # Not due yet → nothing fires.
    clock.set(1299.0)
    assert sched.tick() == 0
    assert captured == []

    # Reach execute_at → the captured request is dispatched.
    clock.set(1300.0)
    assert sched.tick() == 1
    assert len(captured) == 1
    assert captured[0]["url"] == "http://svc/dispatch"
    assert captured[0]["body"] == {"x": 1}
    assert sched.get(job["job_id"])["status"] == "completed"


def test_cron_job_rearms_after_run():
    captured = []
    clock = FakeClock(start=0.0)
    sched = _scheduler(lambda req: captured.append(req) or {"status_code": 200}, clock)

    # Every minute. First run is scheduled at the next minute boundary (60s).
    job = sched.schedule({"cron": "* * * * *", "request": {"url": "http://svc/cron"}})
    first_at = job["execute_at"]
    assert first_at == 60.0

    clock.set(first_at)
    assert sched.tick() == 1
    assert len(captured) == 1

    # The cron job re-armed itself: a new pending job exists for the next minute.
    pending = sched.list(status="pending")
    assert len(pending) == 1
    assert pending[0]["execute_at"] == 120.0
    assert pending[0]["cron"] == "* * * * *"

    # Advancing to the next slot fires it again.
    clock.set(120.0)
    assert sched.tick() == 1
    assert len(captured) == 2


def test_failed_dispatch_retries_then_fails():
    attempts = []

    def flaky(req):
        attempts.append(req)
        raise DispatchError("500 from receiver")

    clock = FakeClock(start=0.0)
    sched = _scheduler(flaky, clock)

    job = sched.schedule(
        {
            "execute_at": 0.0,
            "request": {"url": "http://svc/flaky"},
            "retry": {"max_attempts": 3, "backoff": [10, 20, 30]},
        }
    )
    jid = job["job_id"]

    # Attempt 1 fails → re-queued for now+10s, still pending.
    assert sched.tick() == 1
    assert len(attempts) == 1
    assert sched.get(jid)["status"] == "pending"

    # Before the backoff elapses, nothing fires.
    clock.set(5.0)
    assert sched.tick() == 0
    assert len(attempts) == 1

    # Attempt 2 at +10s → fails → re-queued for +20s.
    clock.set(10.0)
    assert sched.tick() == 1
    assert len(attempts) == 2

    # Attempt 3 at +30s → max_attempts reached → failed (no further requeue).
    clock.set(30.0)
    assert sched.tick() == 1
    assert len(attempts) == 3
    final = sched.get(jid)
    assert final["status"] == "failed"
    assert "500" in final["error"]
    assert sched.list(status="pending") == []


def test_retry_then_success_completes():
    """Fails twice, then succeeds on the 3rd attempt → completed."""
    calls = {"n": 0}

    def twice_then_ok(req):
        calls["n"] += 1
        if calls["n"] < 3:
            raise DispatchError("transient")
        return {"status_code": 200}

    clock = FakeClock(start=0.0)
    sched = _scheduler(twice_then_ok, clock)
    job = sched.schedule(
        {"execute_at": 0.0, "request": {"url": "http://svc/x"}, "retry": {"max_attempts": 3, "backoff": [10, 10, 10]}}
    )
    sched.tick()            # attempt 1 fails
    clock.set(10.0); sched.tick()   # attempt 2 fails
    clock.set(20.0); sched.tick()   # attempt 3 succeeds
    assert calls["n"] == 3
    assert sched.get(job["job_id"])["status"] == "completed"


def test_idempotency_key_dedups():
    clock = FakeClock(start=0.0)
    sched = _scheduler(lambda req: {"status_code": 200}, clock)

    spec = {"execute_at": 100.0, "request": {"url": "http://svc/once"}, "idempotency_key": "dispatch-42"}
    first = sched.schedule(spec)
    second = sched.schedule(spec)  # same key → returns the existing job, no second schedule
    assert first["job_id"] == second["job_id"]
    assert len(sched.list(status="pending")) == 1


def test_orphan_recovery_requeues_inflight_jobs():
    redis = fakeredis.FakeStrictRedis(decode_responses=True)
    clock = FakeClock(start=500.0)
    sched = Scheduler(redis, dispatch=lambda req: {"status_code": 200}, clock=clock)

    # Simulate a job that was mid-flight when the process died: it sits in EXECUTING, not in JOBS.
    import json
    from runtime_kernel.scheduler import EXECUTING_KEY
    orphan = {"job_id": "job_orphan", "execute_at": 100.0, "status": "executing",
              "request": {"url": "http://svc/o"}, "retry": {"max_attempts": 3, "backoff": [1], "attempt": 0}}
    redis.hset(EXECUTING_KEY, "job_orphan", json.dumps(orphan))

    assert sched.recover_orphans() == 1
    # Re-queued into JOBS at ~now, due immediately → tick fires it.
    assert redis.hlen(EXECUTING_KEY) == 0
    assert sched.tick() == 1
    assert sched.get("job_orphan")["status"] == "completed"


def test_cancel_removes_job_so_it_never_fires():
    """cancel() pulls a pending job out of the sorted set and records it cancelled, so advancing
    the clock past execute_at fires nothing. (Parity with the meeting-api scheduler twin, which
    already tests cancel — the runtime kernel's cancel() was implemented but unexercised.)"""
    captured = []
    clock = FakeClock(start=0.0)
    sched = _scheduler(lambda req: captured.append(req) or {"status_code": 200}, clock)

    job = sched.schedule({"execute_at": 100.0, "request": {"url": "http://svc/cancel-me"}})
    jid = job["job_id"]
    assert len(sched.list(status="pending")) == 1

    cancelled = sched.cancel(jid)
    assert cancelled is not None and cancelled["status"] == "cancelled"
    assert sched.list(status="pending") == []
    assert sched.get(jid)["status"] == "cancelled"

    # Advanced well past execute_at, the cancelled job never fires.
    clock.set(10_000.0)
    assert sched.tick() == 0
    assert captured == []

    # Cancelling an unknown / already-cancelled job is a None no-op.
    assert sched.cancel("job_does_not_exist") is None
    assert sched.cancel(jid) is None
