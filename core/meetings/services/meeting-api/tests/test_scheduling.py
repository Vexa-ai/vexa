"""O-MTG-3 eval — meeting scheduling (recurring/scheduled bots).

Autonomous: no docker, no live meeting, no network. The four cases prove the plan's seam:

1. compile a `ScheduledBot{at: <time>}` → a `schedule.v1`-valid ScheduleJob whose `request`
   is the `POST /bots` call (conformance asserted via the compiler's schema-by-path validator
   AND an independent jsonschema check against the same contract);
2. a `FakeClock` advanced past the fire time fires the captured request EXACTLY once — we
   assert the captured POST /bots payload and that NO real bot spawns (capturing dispatch);
3. a `{cron: …}` recurring job RE-ARMS for the next occurrence after firing;
4. CANCEL removes the job so it never fires.
"""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

from meeting_api.scheduling import (
    DEFAULT_BOTS_URL,
    FakeClock,
    ScheduledBot,
    Scheduler,
    compile_scheduled_bot,
    conforms,
)


# --- schedule.v1 schema, loaded independently by path (a second witness to conformance) ----

def _schedule_schema() -> dict:
    rel = Path("runtime") / "contracts" / "schedule.v1" / "schedule.schema.json"
    for parent in Path(__file__).resolve().parents:
        candidate = parent / rel
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise FileNotFoundError(f"monorepo root with {rel} not found")


_SCHEMA = _schedule_schema()
_REGISTRY = Registry().with_resource(_SCHEMA["$id"], Resource.from_contents(_SCHEMA))


def _validate_schedule_job(job: dict) -> None:
    """Independent conformance witness: validate `job` against schedule.v1#/$defs/ScheduleJob."""
    jsonschema.Draft202012Validator(
        {"$ref": f"{_SCHEMA['$id']}#/$defs/ScheduleJob"}, registry=_REGISTRY
    ).validate(job)


# --- a capturing dispatch — the fire ACTION, no real bot spawn -----------------------------

class CapturingDispatch:
    """Stands in for the real `POST /bots` HTTP call: records every request, spawns nothing."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, request: dict) -> dict:
        self.calls.append(request)
        return {"status": 201, "captured": True}


BOT_BODY = {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "bot_name": "Vexa"}


# --- case 1: compile ScheduledBot{at} → schedule.v1-valid job whose request is POST /bots ---

def test_compile_at_emits_schedule_v1_job_targeting_post_bots():
    sched = ScheduledBot(bot=BOT_BODY, at=1_750_000_000, api_key="sk-test")
    job = compile_scheduled_bot(sched)

    # Conformance — both the compiler's seam validator and an independent jsonschema check.
    conforms(job, "ScheduleJob")          # raises on non-conformance
    _validate_schedule_job(job)            # second witness against the same contract

    # The seam: the job's request IS the POST /bots call carrying the bot-spawn body.
    assert job["execute_at"] == 1_750_000_000
    assert "cron" not in job
    req = job["request"]
    assert req["method"] == "POST"
    assert req["url"] == DEFAULT_BOTS_URL and req["url"].endswith("/bots")
    assert req["headers"]["Content-Type"] == "application/json"
    assert req["headers"]["X-API-Key"] == "sk-test"
    assert req["body"] == BOT_BODY


def test_compile_requires_exactly_one_of_at_or_cron():
    with pytest.raises(ValueError):
        ScheduledBot(bot=BOT_BODY)                       # neither
    with pytest.raises(ValueError):
        ScheduledBot(bot=BOT_BODY, at=1, cron="* * * * *")  # both


# --- case 2: FakeClock past fire time fires the captured request EXACTLY once ---------------

def test_fakeclock_fires_one_shot_exactly_once_no_spawn():
    clock = FakeClock(start=1_750_000_000 - 100)   # before the fire time
    dispatch = CapturingDispatch()
    scheduler = Scheduler(dispatch=dispatch, clock=clock)

    job = compile_scheduled_bot(ScheduledBot(bot=BOT_BODY, at=1_750_000_000))
    scheduler.schedule(job)

    # Not yet due → nothing fires.
    assert scheduler.tick() == 0
    assert dispatch.calls == []

    # Advance PAST the fire time → fires exactly once.
    clock.set(1_750_000_001)
    assert scheduler.tick() == 1
    assert len(dispatch.calls) == 1

    # The captured POST /bots payload is the bot-spawn request, verbatim. No real bot spawned.
    captured = dispatch.calls[0]
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/bots")
    assert captured["body"] == BOT_BODY

    # Idempotent re-ticks do NOT re-fire (one-shot is consumed).
    assert scheduler.tick() == 0
    assert len(dispatch.calls) == 1


def test_idempotency_key_dedups_one_shot():
    clock = FakeClock(start=0)
    scheduler = Scheduler(dispatch=CapturingDispatch(), clock=clock)
    spec = compile_scheduled_bot(
        ScheduledBot(bot=BOT_BODY, at=1_750_000_000, idempotency_key="bot-abc-defg-hij")
    )
    first = scheduler.schedule(spec)
    second = scheduler.schedule(spec)        # duplicate → returns the existing job
    assert first["job_id"] == second["job_id"]
    assert len(scheduler.list(status="pending")) == 1


# --- case 3: cron recurring job re-arms for the next occurrence after firing ----------------

def test_cron_job_rearms_after_firing():
    # Daily at 09:00 UTC. Start just before the first 09:00.
    start = 1_750_000_000.0  # arbitrary epoch
    clock = FakeClock(start=start)
    dispatch = CapturingDispatch()
    scheduler = Scheduler(dispatch=dispatch, clock=clock)

    job = compile_scheduled_bot(ScheduledBot(bot=BOT_BODY, cron="0 9 * * *"))
    _validate_schedule_job(job)
    assert job["cron"] == "0 9 * * *" and "execute_at" not in job
    placed = scheduler.schedule(job)

    first_at = placed["execute_at"]
    assert first_at > start                       # first occurrence is in the future

    # Advance to the first occurrence → fires once AND re-arms.
    clock.set(first_at)
    assert scheduler.tick() == 1
    assert len(dispatch.calls) == 1

    pending = scheduler.list(status="pending")
    assert len(pending) == 1                       # re-armed for the next day
    next_at = pending[0]["execute_at"]
    assert next_at > first_at
    assert next_at - first_at == pytest.approx(86_400, abs=1)  # +1 day
    assert pending[0]["cron"] == "0 9 * * *"       # the recurrence rule carries forward

    # Advance to the next occurrence → fires the second time (the bot-spawn body persists).
    clock.set(next_at)
    assert scheduler.tick() == 1
    assert len(dispatch.calls) == 2
    assert dispatch.calls[1]["body"] == BOT_BODY


# --- case 4: cancel removes the job so it never fires ---------------------------------------

def test_cancel_removes_job_so_it_never_fires():
    clock = FakeClock(start=0)
    dispatch = CapturingDispatch()
    scheduler = Scheduler(dispatch=dispatch, clock=clock)

    job = compile_scheduled_bot(ScheduledBot(bot=BOT_BODY, at=1_750_000_000))
    placed = scheduler.schedule(job)
    job_id = placed["job_id"]
    assert len(scheduler.list(status="pending")) == 1

    cancelled = scheduler.cancel(job_id)
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert scheduler.list(status="pending") == []

    # Even advanced well past the fire time, a cancelled job NEVER fires.
    clock.set(1_750_000_000 + 10_000)
    assert scheduler.tick() == 0
    assert dispatch.calls == []

    # Cancelling an unknown / already-cancelled job is a no-op (returns None).
    assert scheduler.cancel(job_id) is None
    assert scheduler.cancel("job_does_not_exist") is None
