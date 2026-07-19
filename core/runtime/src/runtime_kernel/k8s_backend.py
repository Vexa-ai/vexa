"""K8sBackend — runs a workload as a real Kubernetes Pod (the cluster substrate). Uses the kubectl CLI
via subprocess (no client lib), matching the DockerBackend approach. Implements the same Backend port,
so the kernel's runtime.v1 lifecycle is identical to process/docker. A workload is a bare Pod with
restart=Never; the kernel owns restart policy, so the Pod must not resurrect itself."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Optional

from .backend import WorkloadHandle
from .mounts import k8s_volume_mounts
from .profiles import Runnable

MANAGED_LABEL = "runtime.managed"
WORKLOAD_ID_LABEL = "runtime.workload_id"


def _kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["kubectl", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def _stop_grace_sec() -> int:
    """Graceful-delete window (SIGTERM → SIGKILL). Same env knob as the Docker backend
    (RUNTIME_STOP_GRACE_SEC, default 30) so a live meeting bot can honour SIGTERM — leave the
    meeting, flush, POST its terminal callback (<25s by its own watchdog) — before the kubelet
    SIGKILLs it."""
    try:
        return max(1, int(float(os.getenv("RUNTIME_STOP_GRACE_SEC", "30"))))
    except ValueError:
        return 30


def _merge_volumes(base: list[dict[str, Any]], additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join Pod volumes without allowing workspace plumbing to replace a contracted volume."""
    names = [volume.get("name") for volume in [*base, *additions]]
    if len(names) != len(set(names)):
        raise ValueError("k8s Pod volumes must have unique names")
    return [*base, *additions]


def _merge_volume_mounts(base: list[dict[str, Any]], additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join mounts without allowing two volumes to claim the same destination path."""
    paths = [mount.get("mountPath") for mount in [*base, *additions]]
    if len(paths) != len(set(paths)):
        raise ValueError("k8s Pod volume mounts must have unique mount paths")
    return [*base, *additions]


def pod_overrides(
    env: dict[str, str],
    *,
    container_name: str,
    runnable: Optional[Runnable] = None,
    labels: Optional[dict[str, str]] = None,
) -> Optional[dict]:
    """Build the K8s Pod overrides for a workload.

    Supplying a Runnable emits the complete generated container. ``kubectl run`` merges the Pod
    but replaces its ``containers`` list, so image, environment, command, and mounts travel as one
    object. The no-Runnable form remains the small workspace-only helper used by mount plumbing
    checks.
    """
    pvc = env.get("VEXA_WORKSPACE_MOUNT_SOURCE")
    root = env.get("VEXA_WORKSPACE_MOUNT_TARGET")
    workspace_volumes, workspace_mounts = k8s_volume_mounts(
        env, pvc_name=pvc or "", store_target=root or ""
    )
    if runnable is None:
        if not workspace_volumes:
            return None
        return {
            "spec": {
                "containers": [{"name": container_name, "volumeMounts": workspace_mounts}],
                "volumes": workspace_volumes,
            }
        }

    if not runnable.image:
        raise ValueError("k8s backend requires an image")
    contract = runnable.bot_pod_contract
    contract_volumes = contract.volumes if contract else []
    contract_mounts = contract.volume_mounts if contract else []
    volumes = _merge_volumes(contract_volumes, workspace_volumes)
    volume_mounts = _merge_volume_mounts(contract_mounts, workspace_mounts)

    container: dict[str, Any] = {
        "name": container_name,
        "image": runnable.image,
        "env": [{"name": key, "value": value} for key, value in sorted(env.items())],
    }
    if runnable.command:
        container["command"] = runnable.command
    if volume_mounts:
        container["volumeMounts"] = volume_mounts
    if contract:
        container.update(
            {
                "imagePullPolicy": contract.image_pull_policy,
                "securityContext": contract.container_security_context,
                "resources": contract.resources,
            }
        )

    spec: dict[str, Any] = {"restartPolicy": "Never", "containers": [container]}
    if volumes:
        spec["volumes"] = volumes
    if contract:
        spec.update(
            {
                "serviceAccountName": contract.service_account_name,
                "automountServiceAccountToken": False,
                "terminationGracePeriodSeconds": contract.termination_grace_period_seconds,
                "securityContext": contract.security_context,
                "imagePullSecrets": [{"name": contract.image_pull_secret}],
            }
        )
    overrides: dict[str, Any] = {"spec": spec}
    if labels:
        overrides["metadata"] = {"labels": labels}
    return overrides


class K8sBackend:
    name = "k8s"

    def __init__(self, name_prefix: str = "vexa-", namespace: Optional[str] = None) -> None:
        self._prefix = name_prefix
        self._ns = namespace

    def _pname(self, workload_id: str) -> str:
        return f"{self._prefix}{workload_id}"            # must be DNS-1123 (lowercase alnum + '-')

    def _ns_args(self) -> list[str]:
        return ["-n", self._ns] if self._ns else []

    def start(self, workload_id: str, runnable: Runnable, env: dict[str, str]) -> WorkloadHandle:
        if not runnable.image:
            raise ValueError("k8s backend requires an image")
        name = self._pname(workload_id)
        runtime_labels = {
            MANAGED_LABEL: "true",
            WORKLOAD_ID_LABEL: workload_id,
        }
        labels = {**(runnable.bot_pod_contract.labels if runnable.bot_pod_contract else {}), **runtime_labels}
        label_arg = ",".join(f"{key}={value}" for key, value in sorted(labels.items()))
        args = [
            "run", name, f"--image={runnable.image}", "--restart=Never",
            f"--labels={label_arg}",
            *self._ns_args(),
        ]
        overrides = pod_overrides(
            env,
            container_name=name,
            runnable=runnable,
            labels=labels,
        )
        args += ["--overrides", json.dumps(overrides, sort_keys=True)]
        _kubectl(*args)
        return WorkloadHandle(id=workload_id, impl=name)

    def find(self, workload_id: str) -> Optional[WorkloadHandle]:
        """Re-derive a handle for a workload whose in-process handle was lost (restart): the Pod
        name is deterministic (``prefix + workload_id``); an existing Pod (any phase) is found."""
        name = self._pname(workload_id)
        r = _kubectl("get", "pod", name, "-o", "name", *self._ns_args(), check=False)
        if r.returncode != 0:
            return None
        return WorkloadHandle(id=workload_id, impl=name)

    def list_workload_containers(self) -> list[dict]:
        """Discover the workload Pods THIS backend spawned — for boot re-adoption. Label-selected
        only (``runtime.managed=true``): a name-prefix fallback is unsafe in a shared namespace
        (the chart's own service Pods can share the prefix), so Pods spawned by a pre-label runtime
        are not re-adopted. Never raises."""
        try:
            r = _kubectl(
                "get", "pods", "-l", f"{MANAGED_LABEL}=true", "-o", "json",
                *self._ns_args(), check=False,
            )
            if r.returncode != 0:
                return []
            out = []
            for pod in json.loads(r.stdout).get("items", []):
                meta = pod.get("metadata", {})
                wid = (meta.get("labels") or {}).get(WORKLOAD_ID_LABEL)
                if not wid:
                    continue
                phase = pod.get("status", {}).get("phase")
                running = phase in ("Pending", "Running")
                exit_code: Optional[int] = None
                if not running:
                    exit_code = 0 if phase == "Succeeded" else 1
                    for cs in pod.get("status", {}).get("containerStatuses", []):
                        term = cs.get("state", {}).get("terminated")
                        if term and "exitCode" in term:
                            exit_code = int(term["exitCode"])
                out.append({
                    "workload_id": wid,
                    "name": meta.get("name", self._pname(wid)),
                    "running": running,
                    "exit_code": exit_code,
                })
            return out
        except Exception:  # noqa: BLE001 — discovery is a boot aid; it must never crash the boot
            return []

    def exit_code(self, h: WorkloadHandle) -> Optional[int]:
        r = _kubectl("get", "pod", h._impl, "-o", "json", *self._ns_args(), check=False)  # type: ignore[attr-defined]
        if r.returncode != 0:
            return 0                                     # gone (deleted/never-found) → no longer running
        status = json.loads(r.stdout).get("status", {})
        phase = status.get("phase")
        if phase in ("Pending", "Running"):
            return None                                  # still scheduling / running
        if phase == "Succeeded":
            return 0
        if phase == "Failed":
            for cs in status.get("containerStatuses", []):
                term = cs.get("state", {}).get("terminated")
                if term and "exitCode" in term:
                    return int(term["exitCode"])
            return 1
        return None

    def terminate(self, h: WorkloadHandle) -> None:      # graceful: SIGTERM + grace, then SIGKILL
        _kubectl("delete", "pod", h._impl, f"--grace-period={_stop_grace_sec()}", "--wait=false",
                 *self._ns_args(), check=False)  # type: ignore[attr-defined]

    def kill(self, h: WorkloadHandle) -> None:           # force: immediate SIGKILL + drop the object
        _kubectl("delete", "pod", h._impl, "--grace-period=0", "--force", "--wait=false",
                 *self._ns_args(), check=False)  # type: ignore[attr-defined]

    def cleanup(self, h: WorkloadHandle) -> None:
        _kubectl("delete", "pod", h._impl, "--ignore-not-found", "--grace-period=0", "--force",
                 "--wait=false", *self._ns_args(), check=False)  # type: ignore[attr-defined]
