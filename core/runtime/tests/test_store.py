"""O-RT-1 store eval — the WorkloadStore port. ONE shared contract suite runs against BOTH adapters
(InMemoryStore and a fakeredis-backed RedisStore), proving they are behaviourally interchangeable. The
restart test proves Redis persistence: a fresh Runtime built over the same Redis re-reads the
workloads after the in-process handle map is gone."""
import fakeredis
import pytest

from runtime_kernel import (
    InMemoryStore,
    RedisStore,
    Runtime,
    RuntimeState,
    WorkloadRecord,
    WorkloadSpec,
    WorkloadStatus,
)
from runtime_kernel.models import BackendKind


def _record(workload_id: str, owner: str = "", state: RuntimeState = RuntimeState.running) -> WorkloadRecord:
    spec = WorkloadSpec(workloadId=workload_id, profile="test", env={"VEXA_OWNER": owner} if owner else {})
    status = WorkloadStatus(
        workloadId=workload_id, profile="test", state=state, backend=BackendKind.process
    )
    return WorkloadRecord(spec=spec, status=status, owner=owner)


@pytest.fixture(params=["inmemory", "redis"])
def store(request):
    if request.param == "inmemory":
        return InMemoryStore()
    return RedisStore(fakeredis.FakeStrictRedis(decode_responses=True))


# ── ONE shared port-contract suite, run against both adapters ────────────────

def test_set_get_roundtrip(store):
    rec = _record("w1", owner="alice")
    store.set(rec)
    got = store.get("w1")
    assert got is not None
    assert got.spec.workloadId == "w1"
    assert got.status.state is RuntimeState.running
    assert got.owner == "alice"


def test_get_missing_returns_none(store):
    assert store.get("nope") is None


def test_list_returns_all(store):
    store.set(_record("a"))
    store.set(_record("b"))
    store.set(_record("c"))
    ids = {r.spec.workloadId for r in store.list()}
    assert ids == {"a", "b", "c"}


def test_set_overwrites(store):
    store.set(_record("w1", state=RuntimeState.running))
    store.set(_record("w1", state=RuntimeState.stopped))
    assert store.get("w1").status.state is RuntimeState.stopped
    assert len(store.list()) == 1


def test_delete(store):
    store.set(_record("w1"))
    store.delete("w1")
    assert store.get("w1") is None
    assert store.list() == []


def test_delete_missing_is_noop(store):
    store.delete("ghost")  # must not raise


def test_count_for_owner_counts_only_active(store):
    store.set(_record("a", owner="alice", state=RuntimeState.running))
    store.set(_record("b", owner="alice", state=RuntimeState.starting))
    store.set(_record("c", owner="alice", state=RuntimeState.stopped))   # terminal → not counted
    store.set(_record("d", owner="alice", state=RuntimeState.destroyed)) # terminal → not counted
    store.set(_record("e", owner="bob", state=RuntimeState.running))     # different owner
    assert store.count_for_owner("alice") == 2
    assert store.count_for_owner("bob") == 1
    assert store.count_for_owner("nobody") == 0


# ── restart test — Redis persistence survives a fresh Runtime ────────────────

def test_redis_restart_persists_workloads():
    """Populate a store, build a fresh Runtime over the SAME redis, drop the original
    in-process state, and assert list() still returns the persisted workloads."""
    redis = fakeredis.FakeStrictRedis(decode_responses=True)

    # First runtime: create two workloads on the process backend.
    rt1 = Runtime(profiles={"test": ["sleep", "30"]}, store=RedisStore(redis), grace_sec=3.0)
    rt1.create(WorkloadSpec(workloadId="w1", profile="test", env={}))
    rt1.create(WorkloadSpec(workloadId="w2", profile="test", env={}))
    assert {s.workloadId for s in rt1.list()} == {"w1", "w2"}

    # Simulate a process restart: throw rt1 (and its in-process _handles map) away,
    # build a brand-new Runtime over the SAME redis store.
    del rt1
    rt2 = Runtime(profiles={"test": ["sleep", "30"]}, store=RedisStore(redis), grace_sec=3.0)

    persisted = {s.workloadId: s for s in rt2.list()}
    assert set(persisted) == {"w1", "w2"}
    # The reloaded statuses describe what was RUNNING before the restart — not just the id/profile.
    # state + startedAt are reconstructed FROM THE STORE, so a regression that dropped the running
    # state (the load-bearing claim) on reload is caught here, not silently passed.
    assert persisted["w1"].profile == "test"
    assert persisted["w2"].profile == "test"
    assert persisted["w1"].state is RuntimeState.running
    assert persisted["w2"].state is RuntimeState.running
    assert persisted["w1"].startedAt is not None

    # The fresh runtime owns no live handles, but can still drive the persisted workloads:
    # stopping one (no handle ⇒ no exit code) transitions it to stopped and re-persists.
    rt2.stop("w1")
    assert rt2.get("w1").state is RuntimeState.stopped

    # And a third runtime over the same redis sees that transition.
    rt3 = Runtime(profiles={"test": ["sleep", "30"]}, store=RedisStore(redis), grace_sec=3.0)
    assert rt3.get("w1").state is RuntimeState.stopped
