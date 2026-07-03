"""Stage-2 gate — drive a real workload through the runtime.v1 lifecycle on the process backend,
and prove every emitted event conforms to the frozen contract (runtime.schema.json)."""
import json
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

from runtime_kernel import Runtime, WorkloadSpec, RuntimeState

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "contracts" / "runtime.v1" / "runtime.schema.json").read_text()
)
_REGISTRY = Registry().with_resource(SCHEMA["$id"], Resource.from_contents(SCHEMA))


def _conforms(event_json: dict, shape: str) -> None:
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"{SCHEMA['$id']}#/$defs/{shape}"}, registry=_REGISTRY
    )
    validator.validate(event_json)


def test_workload_lifecycle_conforms_to_runtime_v1():
    events = []
    rt = Runtime(profiles={"test": ["sleep", "30"]}, on_event=events.append, grace_sec=3.0)

    spec = WorkloadSpec(workloadId="w1", profile="test", env={})
    rt.create(spec)
    assert rt.get("w1").state is RuntimeState.running        # real child process is up

    rt.stop("w1")
    stopped = rt.get("w1")
    assert stopped.state is RuntimeState.stopped
    assert stopped.exitCode is not None                       # process actually exited

    rt.destroy("w1")
    assert rt.get("w1").state is RuntimeState.destroyed

    # the full legal lifecycle was emitted, in order
    assert [e.state.value for e in events] == [
        "starting", "running", "stopping", "stopped", "destroyed",
    ]
    # every emitted event conforms to the frozen runtime.v1 contract
    for e in events:
        _conforms(json.loads(e.model_dump_json(exclude_none=True)), "RuntimeEvent")


def test_unknown_profile_rejected():
    rt = Runtime(profiles={})
    try:
        rt.create(WorkloadSpec(workloadId="x", profile="nope", env={}))
        assert False, "expected unknown-profile error"
    except ValueError:
        pass
