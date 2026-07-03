"""Validate runtime in ISOLATION against a REAL Kubernetes cluster — create a Pod via the kernel, drive
it through the runtime.v1 lifecycle, and assert it genuinely scheduled, ran, and was removed. Skipped
where no cluster is reachable (e.g. laptop/CI without a KUBECONFIG)."""
import shutil
import subprocess
import time

import pytest

from runtime_kernel import Runtime
from runtime_kernel.k8s_backend import K8sBackend
from runtime_kernel.models import RuntimeState, WorkloadSpec
from runtime_kernel.profiles import Runnable


def _k8s_ok() -> bool:
    if not shutil.which("kubectl"):
        return False
    return subprocess.run(["kubectl", "get", "nodes"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _k8s_ok(), reason="no reachable kubernetes cluster")


def _pod_exists(name: str) -> bool:
    return subprocess.run(["kubectl", "get", "pod", name], capture_output=True).returncode == 0


def _phase(name: str) -> str:
    return subprocess.run(
        ["kubectl", "get", "pod", name, "-o", "jsonpath={.status.phase}"],
        capture_output=True, text=True,
    ).stdout.strip()


def test_k8s_backend_real_pod_lifecycle():
    name = "vexa-rt-k8stest"
    subprocess.run(["kubectl", "delete", "pod", name, "--ignore-not-found",
                    "--grace-period=0", "--force"], capture_output=True)  # clean slate
    rt = Runtime(
        backend=K8sBackend(),
        profiles={"test": Runnable(image="alpine", command=["sleep", "30"])},
        grace_sec=30.0,
    )
    spec = WorkloadSpec(workloadId="rt-k8stest", profile="test", env={"VEXA_X": "y"})
    try:
        rt.create(spec)
        assert rt.get("rt-k8stest").state is RuntimeState.running
        assert _pod_exists(name)                              # a REAL pod object exists

        deadline = time.time() + 90                           # wait for it to actually schedule & run
        while time.time() < deadline and _phase(name) != "Running":
            time.sleep(1)
        assert _phase(name) == "Running"                      # genuinely scheduled & running

        rt.stop("rt-k8stest")
        assert rt.get("rt-k8stest").state is RuntimeState.stopped

        rt.destroy("rt-k8stest")
        assert rt.get("rt-k8stest").state is RuntimeState.destroyed
        assert not _pod_exists(name)                          # pod actually removed
    finally:
        subprocess.run(["kubectl", "delete", "pod", name, "--ignore-not-found",
                        "--grace-period=0", "--force"], capture_output=True)
