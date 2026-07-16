"""K8sBackend — runs a workload as a real Kubernetes Pod (the cluster substrate). Uses the kubectl CLI
via subprocess (no client lib), matching the DockerBackend approach. Implements the same Backend port,
so the kernel's runtime.v1 lifecycle is identical to process/docker. A workload is a bare Pod with
restart=Never; the kernel owns restart policy, so the Pod must not resurrect itself."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

from .backend import WorkloadHandle
from .mounts import k8s_volume_mounts
from .profiles import Runnable

MANAGED_LABEL = "runtime.managed"
WORKLOAD_ID_LABEL = "runtime.workload_id"

# The runtime's OWN scheduling constraints, serialized as JSON by the chart from
# global.tolerations / global.nodeSelector (see deployment-runtime.yaml). A spawned workload is a bare
# `kubectl run` Pod — NOT a Deployment child — so it inherits none of the runtime Deployment's
# scheduling directives; on an all-tainted pool it sits Pending forever and the meeting silently fails.
# These knobs let the spawn override carry the runtime's own constraints so the Pod schedules wherever
# the runtime itself is allowed to run.
TOLERATIONS_ENV = "RUNTIME_K8S_TOLERATIONS"      # JSON array of toleration objects
NODE_SELECTOR_ENV = "RUNTIME_K8S_NODE_SELECTOR"  # JSON object of node-label selectors


def _scheduling_json(env: dict[str, str], key: str, expected: type) -> Optional[object]:
    """Parse one scheduling knob (``key``) from ``env`` as JSON of ``expected`` shape. Unset or empty
    (the chart's default ``[]`` / ``{}`` serialize to ``"[]"`` / ``"{}"``) ⇒ None (no constraint,
    today's behaviour). Malformed JSON or a wrong shape is FATAL (raise) — a scheduling constraint
    silently dropped is exactly the bug this fixes (a stranded Pending Pod, a silent meeting failure),
    so it must fail loud at spawn, never fail open like the workspace mount set."""
    raw = env.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"{key} is not valid JSON: {exc}") from exc
    if not isinstance(value, expected):
        raise ValueError(
            f"{key} must be a JSON {expected.__name__}, got {type(value).__name__}: {raw!r}"
        )
    return value or None                                 # empty [] / {} ⇒ treat as unset


def _runtime_scheduling_env() -> dict[str, str]:
    """The runtime's own scheduling knobs from its PROCESS env (set by the chart on the runtime
    Deployment). Overlaid onto the per-workload spawn env for ``pod_overrides`` — spec.env cannot
    carry these: it is built per-workload by different producers (meeting-api for a bot, agent-api for
    an agent worker), whereas the scheduling constraints are a property of the runtime/backend."""
    return {k: os.environ[k] for k in (TOLERATIONS_ENV, NODE_SELECTOR_ENV) if os.environ.get(k)}


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


def pod_overrides(env: dict[str, str], *, container_name: str) -> Optional[dict]:
    """The ``kubectl run --overrides`` spec for a spawned Pod, built from the SAME env. It carries two
    independent seams:

      * the workspace store mount set (WP-A1.1): the store PVC (``VEXA_WORKSPACE_MOUNT_SOURCE`` = the
        claim name on k8s) exposes every in-store workspace via per-mount subPath volumeMounts;
      * the runtime's scheduling constraints (``RUNTIME_K8S_TOLERATIONS`` / ``RUNTIME_K8S_NODE_SELECTOR``)
        so the bare ``kubectl run`` Pod — which inherits none of the runtime Deployment's scheduling —
        lands where the runtime itself is allowed to run instead of stranding Pending on a tainted pool.

    The spec is built whenever EITHER seam is present; returns None only when neither is (no override
    needed). Building it for scheduling alone is load-bearing: a plain meeting bot has no workspace PVC,
    so a volumes-only early return would silently drop its tolerations and re-create the bug. Pure/
    env-driven → unit-tested offline (no kubectl)."""
    pvc = env.get("VEXA_WORKSPACE_MOUNT_SOURCE")
    root = env.get("VEXA_WORKSPACE_MOUNT_TARGET")
    volumes, volume_mounts = k8s_volume_mounts(env, pvc_name=pvc or "", store_target=root or "")
    tolerations = _scheduling_json(env, TOLERATIONS_ENV, list)
    node_selector = _scheduling_json(env, NODE_SELECTOR_ENV, dict)
    if not volumes and not tolerations and not node_selector:
        return None
    container: dict = {"name": container_name}
    if volume_mounts:
        container["volumeMounts"] = volume_mounts
    spec: dict = {"containers": [container]}
    if volumes:
        spec["volumes"] = volumes
    if tolerations:
        spec["tolerations"] = tolerations
    if node_selector:
        spec["nodeSelector"] = node_selector
    return {"spec": spec}


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
        args = [
            "run", name, f"--image={runnable.image}", "--restart=Never",
            # Adoption labels (the orphaned-live-bot fix): a recreated runtime re-discovers its
            # still-running Pods by this label pair and re-registers them (see the kernel's adopt()).
            f"--labels={MANAGED_LABEL}=true,{WORKLOAD_ID_LABEL}={workload_id}",
            *self._ns_args(),
        ]
        for k, v in env.items():
            args += [f"--env={k}={v}"]
        # The --overrides spec carries the workspace mount set (WP-A1.1: the store PVC bound per-mount,
        # container name = Pod name for a `run` Pod) AND the runtime's own scheduling constraints. The
        # latter live in the runtime's PROCESS env (the chart sets them on the runtime Deployment), not
        # in the per-workload spec.env, so overlay them here; the workload's own --env (above) is left
        # untouched — scheduling shapes the Pod, it is not container config.
        overrides = pod_overrides({**env, **_runtime_scheduling_env()}, container_name=name)
        if overrides:
            args += ["--overrides", json.dumps(overrides)]
        if runnable.command:
            args += ["--command", "--", *runnable.command]
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
