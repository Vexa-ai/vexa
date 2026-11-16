"""Validate runtime in ISOLATION against a REAL Kubernetes cluster — create a Pod via the kernel, drive
it through the runtime.v1 lifecycle, and assert it genuinely scheduled, ran, and was removed. It runs
where the runtime runs: inside a Pod, under a ServiceAccount allowed to create, get, list and delete
Pods in its namespace (the chart's runtime RBAC). Skipped anywhere else (laptop/CI without a cluster)."""
import os
import time

import pytest

from runtime_kernel import Runtime, k8s_backend
from runtime_kernel.k8s_backend import K8sBackend
from runtime_kernel.models import RuntimeState, WorkloadSpec
from runtime_kernel.profiles import Runnable


def _k8s_ok() -> bool:
    if not os.environ.get("KUBERNETES_SERVICE_HOST") or not os.path.exists(os.path.join(k8s_backend.SA_DIR, "token")):
        return False
    try:
        status, _ = k8s_backend._api("GET", K8sBackend()._path("pods"), params={"limit": "1"})
    except Exception:  # noqa: BLE001
        return False
    return status == 200


pytestmark = pytest.mark.skipif(not _k8s_ok(), reason="no reachable kubernetes cluster")


def test_k8s_backend_real_pod_lifecycle():
    name = "vexa-rt-k8stest"
    be = K8sBackend()
    be._delete_pod(name, grace_sec=0)                     # clean slate
    rt = Runtime(
        backend=be,
        profiles={"test": Runnable(image=os.environ.get("K8S_TEST_IMAGE", "alpine"), command=["sleep", "30"])},
        grace_sec=30.0,
    )
    spec = WorkloadSpec(workloadId="rt-k8stest", profile="test", env={"VEXA_X": "y"})

    def phase():
        return ((be._pod(name) or {}).get("status") or {}).get("phase")
    try:
        rt.create(spec)
        assert rt.get("rt-k8stest").state is RuntimeState.running
        assert be._pod(name) is not None                  # a REAL pod object exists

        deadline = time.time() + 90                       # wait for it to actually schedule & run
        while time.time() < deadline and phase() != "Running":
            time.sleep(1)
        assert phase() == "Running"                       # genuinely scheduled & running

        rt.stop("rt-k8stest")
        assert rt.get("rt-k8stest").state is RuntimeState.stopped

        rt.destroy("rt-k8stest")
        assert rt.get("rt-k8stest").state is RuntimeState.destroyed
        deadline = time.time() + 60
        while time.time() < deadline and be._pod(name) is not None:
            time.sleep(1)
        assert be._pod(name) is None                      # pod actually removed
    finally:
        be._delete_pod(name, grace_sec=0)
