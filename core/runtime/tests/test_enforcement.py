"""O-RT-2 enforcement eval — the reaper + the quota check, both deterministic via the FakeClock.

  • idle/max-lifetime sweep — a real `sleep 300` child process with idleTimeoutSec=1: advancing the
    FakeClock past the limit and sweeping stops it with stopReason=idle_timeout (the process is really
    SIGTERM'd; the status is the sealed runtime.v1 enum value, no schema change).
  • quota — with owner_quota=2, the 3rd active workload for the same owner is rejected (QuotaExceeded).
"""
import time

import pytest

from runtime_kernel import (
    Enforcer,
    FakeClock,
    ProcessBackend,
    QuotaExceeded,
    Runtime,
    RuntimeState,
    StopReason,
    WorkloadSpec,
)
from runtime_kernel.profiles import ProfileRegistry, Runnable


def _runtime(clock, quota=None):
    return Runtime(
        backend=ProcessBackend(),
        profiles={"long-sleep": ["sleep", "300"]},
        clock=clock,
        grace_sec=2.0,
        owner_quota=quota,
    )


def test_idle_timeout_stops_workload():
    clock = FakeClock(start=1000.0)
    rt = _runtime(clock)
    enforcer = Enforcer(rt, clock=clock)

    rt.create(WorkloadSpec(workloadId="w1", profile="long-sleep", env={}, idleTimeoutSec=1))
    enforcer.track("w1")
    assert rt.get("w1").state is RuntimeState.running  # real child process is up

    # Not yet past the idle limit → no-op.
    clock.advance(0.5)
    assert enforcer.sweep() == []
    assert rt.get("w1").state is RuntimeState.running

    # Advance past idleTimeoutSec=1 → the sweep stops it.
    clock.advance(1.0)  # now 1.5s idle, limit is 1s
    stopped = enforcer.sweep()
    assert stopped == ["w1"]

    status = rt.get("w1")
    assert status.state is RuntimeState.stopped
    assert status.stopReason is StopReason.idle_timeout


def test_touch_resets_idle_clock():
    clock = FakeClock(start=0.0)
    rt = _runtime(clock)
    enforcer = Enforcer(rt, clock=clock)

    rt.create(WorkloadSpec(workloadId="w1", profile="long-sleep", env={}, idleTimeoutSec=10))
    enforcer.track("w1")

    clock.advance(9)
    enforcer.touch("w1")        # heartbeat — resets last_active to now
    clock.advance(9)            # 9s since touch, still under 10s
    assert enforcer.sweep() == []
    assert rt.get("w1").state is RuntimeState.running

    clock.advance(2)            # now 11s since touch > 10s
    assert enforcer.sweep() == ["w1"]
    assert rt.get("w1").stopReason is StopReason.idle_timeout


def test_max_lifetime_stops_workload_regardless_of_activity():
    clock = FakeClock(start=0.0)
    rt = _runtime(clock)
    enforcer = Enforcer(rt, clock=clock)

    # No idle timeout, but a hard 5s lifetime cap.
    rt.create(WorkloadSpec(workloadId="w1", profile="long-sleep", env={}, maxLifetimeSec=5))
    enforcer.track("w1")

    clock.advance(3)
    enforcer.touch("w1")        # active — but max_lifetime ignores activity
    assert enforcer.sweep() == []

    clock.advance(3)            # 6s alive > 5s cap
    assert enforcer.sweep() == ["w1"]
    assert rt.get("w1").stopReason is StopReason.max_lifetime


def test_profile_idle_timeout_zero_disables_idle_limit():
    """meeting-bot pins idle_timeout 0 (managed externally) → enforcement never idle-stops it."""
    clock = FakeClock(start=0.0)
    reg = ProfileRegistry(
        {"meeting-bot": __import__("runtime_kernel.profiles", fromlist=["Profile"]).Profile(
            name="meeting-bot", runnable=Runnable(command=["sleep", "300"]), idle_timeout_sec=0
        )}
    )
    rt = Runtime(backend=ProcessBackend(), profiles=reg, clock=clock, grace_sec=2.0)
    enforcer = Enforcer(rt, clock=clock)

    rt.create(WorkloadSpec(workloadId="bot", profile="meeting-bot", env={}))
    enforcer.track("bot")
    clock.advance(100000)
    assert enforcer.sweep() == []
    assert rt.get("bot").state is RuntimeState.running
    rt.stop("bot")  # cleanup the real child


def test_sweep_stopped_destroys_self_exited_workload_after_retention():
    """A workload whose process exited on its own (reflected `stopped` with its exit code) is
    DESTROYED once it stays stopped past the retention window — the substrate object is reclaimed
    so no `exited` containers accumulate. Retention 0 simulates a window already elapsed."""
    rt = Runtime(backend=ProcessBackend(), profiles={"quick-exit": ["true"]}, grace_sec=2.0)
    enforcer = Enforcer(rt)

    rt.create(WorkloadSpec(workloadId="w1", profile="quick-exit", env={}))
    # The child exits immediately; get() reflects the exit (code 0) and stamps stoppedAt.
    status = None
    for _ in range(100):  # bounded poll — the child needs a moment to be reaped
        status = rt.get("w1")
        if status.state is RuntimeState.stopped:
            break
        time.sleep(0.05)
    assert status is not None and status.state is RuntimeState.stopped
    assert status.exitCode == 0

    # Inside the retention window → the exit-code evidence is preserved, nothing destroyed.
    assert enforcer.sweep_stopped(retention_sec=3600) == []
    assert rt.get("w1").state is RuntimeState.stopped

    # Past the retention window → destroyed (container removed).
    assert enforcer.sweep_stopped(retention_sec=0) == ["w1"]
    assert rt.get("w1").state is RuntimeState.destroyed


def test_sweep_stopped_keeps_running_workloads():
    """Running workloads are never touched by the stopped-reaper."""
    clock = FakeClock(start=0.0)
    rt = Runtime(backend=ProcessBackend(), profiles={"long-sleep": ["sleep", "300"]}, clock=clock, grace_sec=2.0)
    enforcer = Enforcer(rt, clock=clock)
    rt.create(WorkloadSpec(workloadId="w1", profile="long-sleep", env={}))
    assert enforcer.sweep_stopped(retention_sec=0) == []
    assert rt.get("w1").state is RuntimeState.running
    rt.stop("w1")  # cleanup the real child


def test_quota_rejects_n_plus_first():
    clock = FakeClock(start=0.0)
    rt = _runtime(clock, quota=2)

    def spec(i):
        return WorkloadSpec(
            workloadId=f"w{i}", profile="long-sleep", env={"VEXA_OWNER": "alice"}
        )

    rt.create(spec(1))
    rt.create(spec(2))
    with pytest.raises(QuotaExceeded):
        rt.create(spec(3))  # 3rd active for alice → rejected

    # A different owner is unaffected.
    rt.create(WorkloadSpec(workloadId="b1", profile="long-sleep", env={"VEXA_OWNER": "bob"}))

    # Freeing a slot (stop) lets alice create again.
    rt.stop("w1")
    rt.create(spec(4))  # now only w2 + w4 active for alice → allowed
    assert rt.get("w4").state is RuntimeState.running

    # cleanup real child processes
    for wid in ("w2", "w4", "b1"):
        try:
            rt.stop(wid)
        except Exception:
            pass
