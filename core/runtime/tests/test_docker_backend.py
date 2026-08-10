"""Validate runtime in ISOLATION against the REAL docker substrate — spawn an actual container, drive
it through the runtime.v1 lifecycle, and assert it genuinely ran, stopped, and was removed. Skipped
where the docker daemon is unavailable (e.g. CI without Docker)."""
import shutil
import os
import subprocess
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
