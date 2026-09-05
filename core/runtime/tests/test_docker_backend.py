"""Validate runtime in ISOLATION against the REAL docker substrate — spawn an actual container, drive
it through the runtime.v1 lifecycle, and assert it genuinely ran, stopped, and was removed. Skipped
where the docker daemon is unavailable (e.g. CI without Docker)."""
import shutil
import os
import subprocess
import time
import uuid

import pytest

from runtime_kernel import Runtime
from runtime_kernel.docker_backend import DockerBackend
from runtime_kernel.models import RuntimeState, WorkloadSpec
from runtime_kernel.profiles import Runnable


def _docker_ok() -> bool:
    return bool(shutil.which("docker")) and subprocess.run(
        ["docker", "info"], capture_output=True
    ).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ok(), reason="docker daemon not available")


def _exists(name: str) -> bool:
    out = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        capture_output=True, text=True,
    ).stdout
    return name in out.split()


def test_docker_backend_real_container_lifecycle():
    # UNIQUE per run: the container name is a shared mutable resource on the host daemon, and
    # docker removal is ASYNC — a concurrent suite (or one starting while a previous container is
    # still being removed) used to collide on a fixed name and fail with a 409 "removal ... already
    # in progress", accusing whatever diff happened to be under test. (#864)
    wid = f"rt-dockertest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    name = f"vexa-{wid}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)  # clean slate
    rt = Runtime(
        backend=DockerBackend(),
        profiles={"test": Runnable(image="alpine", command=["sleep", "30"])},
        grace_sec=10.0,
    )
    spec = WorkloadSpec(workloadId=wid, profile="test", env={"VEXA_X": "y"})
    try:
        rt.create(spec)
        assert rt.get(wid).state is RuntimeState.running
        assert _exists(name)                                  # a REAL container is running

        rt.stop(wid)
        assert rt.get(wid).state is RuntimeState.stopped

        rt.destroy(wid)
        assert rt.get(wid).state is RuntimeState.destroyed
        assert not _exists(name)                              # container actually removed
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def test_docker_reclaim_races_a_real_concurrent_remover(caplog):
    # The staged race behind the 409 reclaim contract, against the REAL daemon: another remover
    # force-deletes the container at the same moment cleanup does. Whichever DELETE loses gets
    # 409 "removal already in progress"; cleanup must succeed either way, and only because the
    # container is provably GONE. The fat writable layer widens the removal window so the race
    # reliably overlaps.
    import threading

    from runtime_kernel.backend import WorkloadHandle
    from runtime_kernel.docker_backend import DockerBackend

    name = f"vexa-rt-reclaimtest-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)  # clean slate
    try:
        subprocess.run(
            ["docker", "run", "-d", "--name", name, "alpine",
             "sh", "-c", "dd if=/dev/zero of=/big bs=1M count=300 2>/dev/null && sleep 300"],
            check=True, capture_output=True,
        )
        # wait for the layer to be written, so removal has real work to do
        for _ in range(100):
            probe = subprocess.run(
                ["docker", "exec", name, "test", "-f", "/big"], capture_output=True
            )
            if probe.returncode == 0:
                break
            time.sleep(0.1)
        remover = threading.Thread(
            target=subprocess.run, args=(["docker", "rm", "-f", name],),
            kwargs={"capture_output": True},
        )
        remover.start()
        # head start: let the remover's DELETE get in flight (CLI startup + API call), so
        # cleanup's own DELETE arrives mid-removal and takes the 409 confirm-absence path —
        # the fat layer keeps the removal window open far longer than this delay
        time.sleep(0.5)
        with caplog.at_level("WARNING", logger="runtime_kernel.docker_backend"):
            DockerBackend().cleanup(WorkloadHandle(name, name))  # must not raise, whoever wins
        remover.join(timeout=60)
        assert not _exists(name)                              # absence, the only success condition
        if not any("removal already in progress" in r.getMessage() for r in caplog.records):
            # a lost race is still a valid pass of the invariant (cleanup succeeded, container
            # gone) but it never entered the 409 branch — say so instead of passing silently;
            # the scripted suite pins the branch deterministically either way
            pytest.skip("race did not overlap on this host — 409 branch pinned by the scripted suite")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
