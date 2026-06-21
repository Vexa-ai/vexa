"""O-RT-2 schedule.v1 conformance — the scheduler's job specs match the sealed-in-development
schedule.v1 contract. Both goldens validate, and a job built by the Scheduler from a golden spec is a
valid ScheduleJob shape (the Python side and the contract agree)."""
import json
from pathlib import Path

import fakeredis
import jsonschema
from referencing import Registry, Resource

from runtime_kernel import FakeClock, Scheduler

CONTRACT = Path(__file__).resolve().parents[1] / "contracts" / "schedule.v1"
SCHEMA = json.loads((CONTRACT / "schedule.schema.json").read_text())
_REGISTRY = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))


def _conforms(obj: dict, shape: str) -> None:
    jsonschema.Draft202012Validator(
        {"$ref": f"{SCHEMA['$id']}#/$defs/{shape}"}, registry=_REGISTRY
    ).validate(obj)


def test_goldens_conform():
    golden_dir = CONTRACT / "golden"
    files = [p for p in golden_dir.glob("*.json")]
    assert files, "expected golden vectors"
    for p in files:
        shape = p.name.split(".")[0]
        _conforms(json.loads(p.read_text()), shape)


def test_scheduler_accepts_each_golden_spec():
    """Each golden is a legal schedule() input; the scheduler turns it into a job."""
    clock = FakeClock(start=1000.0)
    sched = Scheduler(
        fakeredis.FakeStrictRedis(decode_responses=True),
        dispatch=lambda req: {"status_code": 200},
        clock=clock,
    )
    for p in (CONTRACT / "golden").glob("ScheduleJob.*.json"):
        spec = json.loads(p.read_text())
        job = sched.schedule(spec)
        assert job["job_id"].startswith("job_")
        assert job["request"]["url"] == spec["request"]["url"]
