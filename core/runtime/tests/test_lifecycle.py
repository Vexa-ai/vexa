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


# ── create() idempotency (ADR 0027) — workloadId is the idempotency key runtime.v1 documents ──────
# A create for a still-running workload is a TOUCH: the live status comes back, nothing respawns,
# the persisted spec stays the first caller's, and a keep-alive touch can never trip the quota gate.
# Before this, every re-dispatch reached the docker backend's name-conflict path, which force-deleted
# the RUNNING container — the live-copilot churn defect (4 spawns in 32s on meeting row 46).

from runtime_kernel.backend import WorkloadHandle  # noqa: E402


class _FakeBackend:
    """Counts start() calls; exit codes are settable so a test can 'exit' a workload. `find` is
    present so a fresh kernel over a shared store re-derives handles (the restart path). `name`
    must be a valid BackendKind (WorkloadStatus.backend is enum-validated)."""
    name = "process"

    def __init__(self) -> None:
        self.starts: list[str] = []
        self.envs: dict[str, dict[str, str]] = {}
        self.exit_codes: dict[str, int | None] = {}

    def start(self, workload_id, runnable, env):
        self.starts.append(workload_id)
        self.envs[workload_id] = dict(env)
        self.exit_codes[workload_id] = None
        return WorkloadHandle(id=workload_id, impl=workload_id)

    def exit_code(self, h):
        return self.exit_codes.get(h.id)

    def terminate(self, h):
        self.exit_codes[h.id] = 0

    def kill(self, h):
        self.exit_codes[h.id] = 137

    def cleanup(self, h):
        pass

    def find(self, workload_id):
        return WorkloadHandle(id=workload_id, impl=workload_id) if workload_id in self.exit_codes else None


def test_create_is_idempotent_touch_while_running():
    be = _FakeBackend()
    rt = Runtime(backend=be, profiles={"test": ["true"]})
    first = rt.create(WorkloadSpec(workloadId="w1", profile="test", env={"A": "1"}))
    assert first.state is RuntimeState.running

    touched = rt.create(WorkloadSpec(workloadId="w1", profile="test", env={"A": "2"}))
    assert touched.state is RuntimeState.running
    assert be.starts == ["w1"]                          # ONE spawn — the second create touched
    assert rt.store.get("w1").spec.env == {"A": "1"}    # the running workload keeps its original spec


def test_touch_at_quota_cap_never_raises():
    be = _FakeBackend()
    rt = Runtime(backend=be, profiles={"test": ["true"]}, owner_quota=1)
    rt.create(WorkloadSpec(workloadId="w1", profile="test", env={}))
    # The owner is at cap with w1 itself active — a keep-alive touch of w1 must not 429.
    touched = rt.create(WorkloadSpec(workloadId="w1", profile="test", env={}))
    assert touched.state is RuntimeState.running
    assert be.starts == ["w1"]


def test_create_respawns_after_self_exit():
    be = _FakeBackend()
    rt = Runtime(backend=be, profiles={"test": ["true"]})
    rt.create(WorkloadSpec(workloadId="w1", profile="test", env={}))
    be.exit_codes["w1"] = 0                             # the workload exited on its own
    respawned = rt.create(WorkloadSpec(workloadId="w1", profile="test", env={}))
    assert respawned.state is RuntimeState.running
    assert be.starts == ["w1", "w1"]                    # exit-reflection let the re-create spawn


def test_profile_base_env_reaches_the_spawn_env():
    """A profile's base_env (e.g. meeting-bot's BOT_SPEAKER_* tuning rendered onto the runtime pod)
    must be merged into the spawned workload's env, with the per-workload spec.env layered on top
    (spec wins). Without the merge the chart's tuning never reaches the bot pod (issue #771)."""
    from runtime_kernel.profiles import Profile, ProfileRegistry, Runnable

    reg = ProfileRegistry({
        "meeting-bot": Profile(
            name="meeting-bot",
            runnable=Runnable(image="bot:img", command=None),
            base_env={"BOT_SPEAKER_MIN_AUDIO_SEC": "1.5", "BOT_SPEAKER_MAX_BUFFER_SEC": "8"},
        )
    })
    be = _FakeBackend()
    rt = Runtime(backend=be, profiles=reg)
    rt.create(WorkloadSpec(
        workloadId="w1", profile="meeting-bot",
        env={"VEXA_BOT_CONFIG": "{}", "BOT_SPEAKER_MAX_BUFFER_SEC": "12"},
    ))
    spawned = be.envs["w1"]
    assert spawned["BOT_SPEAKER_MIN_AUDIO_SEC"] == "1.5"   # base_env floor reaches the pod
    assert spawned["VEXA_BOT_CONFIG"] == "{}"              # spec.env preserved
    assert spawned["BOT_SPEAKER_MAX_BUFFER_SEC"] == "12"   # spec.env wins over base_env


def test_create_touches_across_a_runtime_restart():
    """A fresh kernel (empty handle map) over the same store + substrate re-derives the handle via
    backend.find, sees the workload still running, and touches — no duplicate spawn post-restart."""
    be = _FakeBackend()
    store_holder = Runtime(backend=be, profiles={"test": ["true"]})
    store_holder.create(WorkloadSpec(workloadId="w1", profile="test", env={}))

    reborn = Runtime(backend=be, profiles={"test": ["true"]}, store=store_holder.store)
    touched = reborn.create(WorkloadSpec(workloadId="w1", profile="test", env={}))
    assert touched.state is RuntimeState.running
    assert be.starts == ["w1"]
