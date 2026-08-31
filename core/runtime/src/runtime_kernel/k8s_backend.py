"""K8sBackend — runs a workload as a real Kubernetes Pod (the cluster substrate). Uses the kubectl CLI
via subprocess (no client lib), matching the DockerBackend approach. Implements the same Backend port,
so the kernel's runtime.v1 lifecycle is identical to process/docker. A workload is a bare Pod with
restart=Never; the kernel owns restart policy, so the Pod must not resurrect itself.

The spawn submits a COMPLETE Pod manifest (``build_pod`` → ``kubectl create -f -``). Owning the whole
object is what lets a container carry CPU/memory requests and limits — the declaration a namespace
ResourceQuota admits on — WITHOUT a partial ``kubectl run --overrides`` containers entry, whose JSON
merge replaces the generated container wholesale and strips its image, env and command."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

from .backend import WorkloadHandle
from .models import Resources
from .mounts import k8s_volume_mounts
from .profiles import Runnable

MANAGED_LABEL = "runtime.managed"
WORKLOAD_ID_LABEL = "runtime.workload_id"

# The extended-resource name a GPU request carries. Kubernetes requires extended resources on the
# LIMITS side; the request is set equal to the limit automatically, and a requests-side entry that
# differs is rejected — so runtime.v1's single `gpu` count maps to limits only.
GPU_RESOURCE = "nvidia.com/gpu"

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


def _kubectl(*args: str, check: bool = True, stdin: Optional[str] = None) -> subprocess.CompletedProcess:
    r = subprocess.run(["kubectl", *args], capture_output=True, text=True, input=stdin)
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
    """The env-derived OVERLAY ``build_pod`` merges onto a spawned Pod's spec. It carries two
    independent seams:

      * the workspace store mount set (WP-A1.1): the store PVC (``VEXA_WORKSPACE_MOUNT_SOURCE`` = the
        claim name on k8s) exposes every in-store workspace via per-mount subPath volumeMounts;
      * the runtime's scheduling constraints (``RUNTIME_K8S_TOLERATIONS`` / ``RUNTIME_K8S_NODE_SELECTOR``)
        so the bare ``kubectl run`` Pod — which inherits none of the runtime Deployment's scheduling —
        lands where the runtime itself is allowed to run instead of stranding Pending on a tainted pool.

    The overlay is built whenever EITHER seam is present; returns None only when neither is (nothing
    to merge). Building it for scheduling alone is load-bearing: a plain meeting bot has no workspace
    PVC, so a volumes-only early return would silently drop its tolerations and re-create the bug.
    Pure/env-driven → unit-tested offline (no kubectl)."""
    pvc = env.get("VEXA_WORKSPACE_MOUNT_SOURCE")
    root = env.get("VEXA_WORKSPACE_MOUNT_TARGET")
    volumes, volume_mounts = k8s_volume_mounts(env, pvc_name=pvc or "", store_target=root or "")
    tolerations = _scheduling_json(env, TOLERATIONS_ENV, list)
    node_selector = _scheduling_json(env, NODE_SELECTOR_ENV, dict)
    # Meeting-bot Pods run Chromium's NAMESPACE sandbox as a non-root user (bot image + entrypoint.sh)
    # instead of --no-sandbox — which needs user namespaces, so relax seccomp for the BOT Pod only
    # (identified by VEXA_BOT_CONFIG). Pod-level securityContext, so `kubectl run --overrides` json-merges
    # it WITHOUT touching the generated containers list (same merge seam as tolerations/nodeSelector).
    # CHROME_NO_SANDBOX=1 keeps the legacy root + --no-sandbox path — no override needed.
    bot_unconfined = (
        "VEXA_BOT_CONFIG" in env
        and (env.get("CHROME_NO_SANDBOX") or "").strip().lower() not in ("1", "true", "yes")
    )
    if not volumes and not tolerations and not node_selector and not bot_unconfined:
        return None
    # ``containers`` is emitted ONLY when volumeMounts force it (the workspace-store seam);
    # pod-level fields (tolerations/nodeSelector) shape the Pod without touching the list. Keeping
    # the overlay minimal is what lets ``build_pod`` merge it BY CONTAINER NAME onto the generated
    # container instead of replacing it.
    spec: dict = {}
    if volume_mounts:
        spec["containers"] = [{"name": container_name, "volumeMounts": volume_mounts}]
    if volumes:
        spec["volumes"] = volumes
    if tolerations:
        spec["tolerations"] = tolerations
    if node_selector:
        spec["nodeSelector"] = node_selector
    if bot_unconfined:
        spec["securityContext"] = {"seccompProfile": {"type": "Unconfined"}}
    return {"spec": spec}


def resource_requirements(resources: Optional[Resources]) -> Optional[dict]:
    """Map runtime.v1 ``Resources`` to a container's ``resources`` block.

    v1 carries ONE value per dimension, so cpu/memory set BOTH the request and the limit — the
    minimum non-breaking contract for a namespace whose ResourceQuota requires each container to
    declare both, and Guaranteed QoS for the workload. The sealed contract does not model separate
    request/limit semantics and this mapping does not invent them.

    ``0`` is schema-legal but meaningless as a Kubernetes quantity (a zero request is not "unset" to
    a quota), so it is treated as unset. All-unset ⇒ None: no ``resources`` key is emitted at all
    and the spawn is byte-identical to the pre-sizing behaviour."""
    if resources is None:
        return None
    requests: dict[str, str] = {}
    limits: dict[str, str] = {}
    if resources.cpu:
        # millicores: the canonical k8s CPU quantity, and exact for the fractional values v1 allows
        # (0.5 → "500m") where a bare float would serialize as an unstable "0.5".
        quantity = f"{round(resources.cpu * 1000)}m"
        requests["cpu"] = limits["cpu"] = quantity
    if resources.memoryMb:
        quantity = f"{resources.memoryMb}Mi"
        requests["memory"] = limits["memory"] = quantity
    if resources.gpu:
        limits[GPU_RESOURCE] = str(resources.gpu)          # extended resource: limits side only
    block: dict[str, dict[str, str]] = {}
    if requests:
        block["requests"] = requests
    if limits:
        block["limits"] = limits
    return block or None


def build_pod(
    *,
    name: str,
    workload_id: str,
    runnable: Runnable,
    env: dict[str, str],
    namespace: Optional[str],
    resources: Optional[Resources],
    overlay_env: Optional[dict[str, str]] = None,
) -> dict:
    """The COMPLETE Pod object a spawn submits — every field the workload needs, in one manifest.

    Why a whole object rather than ``kubectl run --overrides``: that flag merges the container LIST
    by REPLACEMENT (JSON merge patch), so any partial ``containers`` entry erases the generated
    container's image/env/command and the API server rejects the Pod outright. Owning the object
    makes the merge OURS and deterministic — the overlay's per-container fields are merged BY
    CONTAINER NAME onto the generated container, so resources, workspace volumeMounts, image,
    command, env, labels and scheduling all coexist instead of clobbering each other.

    ``env`` is the container's env VERBATIM; ``overlay_env`` (default: ``env``) is the wider env the
    pod-shaping overlay is derived from. They differ because the runtime's own scheduling knobs live
    in its process env, not in the workload's — and must shape the Pod without being injected into
    the workload's container as config.

    Pure and env-driven ⇒ the whole manifest is asserted offline, with no cluster and no kubectl.
    (``kubectl run --dry-run=client`` is NOT a viable generator here: v1.34 performs API discovery
    before generating and exits 1 with no output when no server is reachable.)"""
    container: dict = {
        "name": name,
        "image": runnable.image,
        "env": [{"name": k, "value": v} for k, v in env.items()],
    }
    if runnable.command:
        # Explicit argv REPLACES the image ENTRYPOINT. Absent ⇒ the image's own entrypoint boots,
        # which is what the shipped meeting-bot image requires (#675).
        container["command"] = list(runnable.command)
    requirements = resource_requirements(resources)
    if requirements:
        container["resources"] = requirements

    metadata: dict = {
        "name": name,
        # Adoption labels (the orphaned-live-bot fix): a recreated runtime re-discovers its
        # still-running Pods by this label pair and re-registers them (see the kernel's adopt()).
        "labels": {MANAGED_LABEL: "true", WORKLOAD_ID_LABEL: workload_id},
    }
    if namespace:
        metadata["namespace"] = namespace

    # restart=Never: the kernel owns restart policy, so the Pod must not resurrect itself.
    pod: dict = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": metadata,
        "spec": {"containers": [container], "restartPolicy": "Never"},
    }

    overlay_source = env if overlay_env is None else overlay_env
    overlay = (pod_overrides(overlay_source, container_name=name) or {}).get("spec", {})
    for key in ("volumes", "tolerations", "nodeSelector"):
        if overlay.get(key):
            pod["spec"][key] = overlay[key]
    for overlay_container in overlay.get("containers", []):
        if overlay_container.get("name") != name:
            continue                                       # merge BY NAME — never by position
        for key, value in overlay_container.items():
            if key != "name":
                container[key] = value
    return pod


class K8sBackend:
    name = "k8s"

    def __init__(self, name_prefix: str = "vexa-", namespace: Optional[str] = None) -> None:
        self._prefix = name_prefix
        self._ns = namespace

    def _pname(self, workload_id: str) -> str:
        return f"{self._prefix}{workload_id}"            # must be DNS-1123 (lowercase alnum + '-')

    def _ns_args(self) -> list[str]:
        return ["-n", self._ns] if self._ns else []

    def start(
        self,
        workload_id: str,
        runnable: Runnable,
        env: dict[str, str],
        resources: Optional[Resources] = None,
    ) -> WorkloadHandle:
        """Submit the workload's complete Pod manifest. ``resources`` (the kernel's effective sizing:
        the spec's own, else the profile's chart-configured default) becomes the container's
        requests+limits, which is what a namespace ResourceQuota admits on."""
        if not runnable.image:
            raise ValueError("k8s backend requires an image")
        name = self._pname(workload_id)
        # The workspace mount set and the runtime's OWN scheduling constraints both shape the Pod.
        # The latter live in the runtime's PROCESS env (the chart sets them on the runtime
        # Deployment), not in the per-workload spec.env — which is built per-workload by different
        # producers (meeting-api for a bot, agent-api for a worker) — so they ride overlay_env: they
        # shape the Pod without becoming container config the workload never asked for.
        pod = build_pod(
            name=name,
            workload_id=workload_id,
            runnable=runnable,
            env=env,
            namespace=self._ns,
            resources=resources,
            overlay_env={**env, **_runtime_scheduling_env()},
        )
        _kubectl("create", "-f", "-", *self._ns_args(), stdin=json.dumps(pod))
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
