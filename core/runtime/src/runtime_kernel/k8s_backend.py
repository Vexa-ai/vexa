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

# The security context a spawned workload declares, serialized as JSON by the chart from
# runtime.workloadSecurityContext.{pod,container}. A spawned Pod carries NO securityContext today —
# no runAsNonRoot, no dropped capabilities, no seccompProfile — which is the likeliest reason a
# restricted admission policy (OpenShift's `restricted-v2` SCC, or the upstream Pod Security
# "restricted" profile) rejects it outright. That policy shape cannot be tested on k3d, which has no
# SCC admission at all, so this is a CONFIGURABLE seam and not a guess about what the policy wants.
#
# DEFAULT IS ABSENT, on purpose. This repository's images carry no `USER` directive (0 of 15
# Dockerfiles), so they run as root; a defaulted `runAsNonRoot: true` would stop every existing
# operator's bots from starting in order to satisfy one cluster's policy. Whether these images can
# run as an arbitrary non-root UID at all is untested and tracked separately.
POD_SECURITY_CONTEXT_ENV = "RUNTIME_K8S_POD_SECURITY_CONTEXT"              # JSON PodSecurityContext
CONTAINER_SECURITY_CONTEXT_ENV = "RUNTIME_K8S_CONTAINER_SECURITY_CONTEXT"  # JSON SecurityContext


def _scheduling_json(env: dict[str, str], key: str, expected: type) -> Optional[object]:
    """Parse one pod-shaping knob (``key``) from ``env`` as JSON of ``expected`` shape. Unset or empty
    (the chart's default ``[]`` / ``{}`` serialize to ``"[]"`` / ``"{}"``) ⇒ None (no constraint,
    today's behaviour). Malformed JSON or a wrong shape is FATAL (raise) — a scheduling constraint
    silently dropped is exactly the bug this fixes (a stranded Pending Pod, a silent meeting failure),
    so it must fail loud at spawn, never fail open like the workspace mount set.

    Named for its first caller (scheduling); it now also parses the security-context knobs, which
    need exactly the same contract — a security context silently dropped is a Pod rejected at
    admission with a cause the operator cannot see."""
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
    """The runtime's own pod-shaping knobs from its PROCESS env (set by the chart on the runtime
    Deployment). Overlaid onto the per-workload spawn env for ``pod_overrides`` — spec.env cannot
    carry these: it is built per-workload by different producers (meeting-api for a bot, agent-api for
    an agent worker), whereas the scheduling constraints and the security context are properties of
    the runtime/backend deployment, decided by the operator who installs the chart."""
    keys = (TOLERATIONS_ENV, NODE_SELECTOR_ENV, POD_SECURITY_CONTEXT_ENV, CONTAINER_SECURITY_CONTEXT_ENV)
    return {k: os.environ[k] for k in keys if os.environ.get(k)}


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
    """The ``kubectl run --overrides`` spec for a spawned Pod, built from the SAME env. It carries three
    independent seams:

      * the workspace store mount set (WP-A1.1): the store PVC (``VEXA_WORKSPACE_MOUNT_SOURCE`` = the
        claim name on k8s) exposes every in-store workspace via per-mount subPath volumeMounts;
      * the runtime's scheduling constraints (``RUNTIME_K8S_TOLERATIONS`` / ``RUNTIME_K8S_NODE_SELECTOR``)
        so the bare ``kubectl run`` Pod — which inherits none of the runtime Deployment's scheduling —
        lands where the runtime itself is allowed to run instead of stranding Pending on a tainted pool;
      * the workload security context (``RUNTIME_K8S_POD_SECURITY_CONTEXT`` /
        ``RUNTIME_K8S_CONTAINER_SECURITY_CONTEXT``) so an operator whose namespace enforces a
        restricted admission policy can declare what that policy demands, instead of the spawn
        carrying no security context at all and being rejected with no configurable remedy.

    The spec is built whenever ANY seam is present; returns None only when none is (no override
    needed). Building it for scheduling alone is load-bearing: a plain meeting bot has no workspace PVC,
    so a volumes-only early return would silently drop its tolerations and re-create the bug. Pure/
    env-driven → unit-tested offline (no kubectl).

    Both security-context knobs default to ABSENT (the chart's ``{}`` serializes to ``"{}"`` ⇒ None),
    so an install that does not set them produces exactly the spec it produced before this seam
    existed — byte-identical."""
    pvc = env.get("VEXA_WORKSPACE_MOUNT_SOURCE")
    root = env.get("VEXA_WORKSPACE_MOUNT_TARGET")
    volumes, volume_mounts = k8s_volume_mounts(env, pvc_name=pvc or "", store_target=root or "")
    tolerations = _scheduling_json(env, TOLERATIONS_ENV, list)
    node_selector = _scheduling_json(env, NODE_SELECTOR_ENV, dict)
    pod_security_context = _scheduling_json(env, POD_SECURITY_CONTEXT_ENV, dict)
    container_security_context = _scheduling_json(env, CONTAINER_SECURITY_CONTEXT_ENV, dict)
    if not any((volumes, tolerations, node_selector, pod_security_context, container_security_context)):
        return None
    # ``kubectl run --overrides`` merges the containers LIST by replacement (json-merge, not
    # strategic), so a containers entry here wipes the generated container — image, env, command —
    # and the API server rejects the Pod (`spec.containers[0].image: Required value`), killing the
    # spawn instantly. Emit ``containers`` ONLY when a container-scoped field forces it (the
    # workspace-store mounts, or the container security context); pod-level fields (tolerations /
    # nodeSelector / pod securityContext) merge fine without touching the list.
    #
    # KNOWN ORDERING DEPENDENCY: that clobbering is a real defect of the ``--overrides`` submission
    # path, not a hypothetical — it is what #1005's PR (#1093) fixes by submitting a complete Pod
    # manifest and merging the overlay BY CONTAINER NAME. Until that lands, ANY containers entry
    # here — including the pre-existing workspace-mount one — is replaced wholesale. So the
    # CONTAINER-level knob is wired and shaped correctly but is only safe to switch on once the
    # whole-manifest submission is in place; the POD-level knob is safe today. Both default to
    # absent, so no existing install is affected either way.
    spec: dict = {}
    container: dict = {"name": container_name}
    if volume_mounts:
        container["volumeMounts"] = volume_mounts
    if container_security_context:
        container["securityContext"] = container_security_context
    if len(container) > 1:                               # more than just the name ⇒ worth emitting
        spec["containers"] = [container]
    if volumes:
        spec["volumes"] = volumes
    if tolerations:
        spec["tolerations"] = tolerations
    if node_selector:
        spec["nodeSelector"] = node_selector
    if pod_security_context:
        spec["securityContext"] = pod_security_context
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
