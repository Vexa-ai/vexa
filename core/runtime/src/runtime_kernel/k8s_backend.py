"""K8sBackend — runs a workload as a real Kubernetes Pod (the cluster substrate). Uses the kubectl CLI
via subprocess (no client lib), matching the DockerBackend approach. Implements the same Backend port,
so the kernel's runtime.v1 lifecycle is identical to process/docker. A workload is a bare Pod with
restart=Never; the kernel owns restart policy, so the Pod must not resurrect itself.

The spawn submits a COMPLETE Pod manifest (``build_pod`` → ``kubectl create -f -``). Owning the whole
object is what lets a container carry CPU/memory requests and limits — the declaration a namespace
ResourceQuota admits on — WITHOUT a partial ``kubectl run --overrides`` containers entry, whose JSON
merge replaces the generated container wholesale and strips its image, env and command."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from typing import Optional

from . import pod_scheduling
from .backend import WorkloadHandle
from .models import Resources
from .mounts import k8s_volume_mounts
from .profiles import Runnable

MANAGED_LABEL = "runtime.managed"
#: Which runtime spawned the Pod (the Helm release); adoption selects on it, so two releases in one
#: namespace never adopt each other's Pods.
INSTANCE_LABEL = "runtime.instance"
INSTANCE_ENV = "RUNTIME_K8S_INSTANCE"
#: Pod phases after which the workload will not run again.
TERMINAL_PHASES = ("Succeeded", "Failed")
#: The workload id rides both a label (selectable, so it must be a valid label value) and an
#: annotation of the same key (the id verbatim, whatever its shape). Adoption reads the annotation.
WORKLOAD_ID_LABEL = "runtime.workload_id"

# A workload id is the caller's (a chat unit is `agent-<subject>-chat-scaffold-<token_urlsafe>`:
# capitals, `_`, past 63 characters), but a Pod name, a container name and a label value are not.
_DNS1123_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_LABEL_VALUE = re.compile(r"^[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$")
_NAME_MAX = 63
_HASH_LEN = 10


def k8s_name(raw: str) -> str:
    """A Pod or container name for ``raw``: ``raw`` itself when it is already a DNS-1123 label of at
    most 63 characters (so bots and existing Pods keep their names), else lowercased, reduced to
    ``[a-z0-9-]``, cut to fit and suffixed with a hash of ``raw``, so two ids that differ only in case
    or in a dropped character never share a name. Deterministic: a restarted runtime re-derives it."""
    if len(raw) <= _NAME_MAX and _DNS1123_LABEL.match(raw):
        return raw
    base = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", raw.lower())).strip("-")
    digest = hashlib.sha256(raw.encode()).hexdigest()[:_HASH_LEN]
    base = base[: _NAME_MAX - _HASH_LEN - 1].rstrip("-")
    return f"{base}-{digest}" if base else digest


def k8s_label_value(raw: str) -> str:
    """A label value for ``raw``: ``raw`` itself when valid, else :func:`k8s_name` of it."""
    if raw == "" or (len(raw) <= _NAME_MAX and _LABEL_VALUE.match(raw)):
        return raw
    return k8s_name(raw)

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
# File-shaped credentials for spawned workloads (the twin of dispatch's MODEL_AUTH_ENV_ALLOWLIST,
# which covers env-shaped ones): some harnesses read a credential FILE (codex: ~/.codex/auth.json),
# and a bare `kubectl run` Pod inherits no mounts. JSON array of {"secret": <name>, "mountPath": <dir>}.
# Mounted read-only — a token refresh cannot persist, so keep the Secret fresh operator-side.
SECRET_MOUNTS_ENV = "RUNTIME_K8S_SECRET_MOUNTS"  # JSON array of {secret, mountPath}


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
    return {k: os.environ[k] for k in (TOLERATIONS_ENV, NODE_SELECTOR_ENV, SECRET_MOUNTS_ENV)
            if os.environ.get(k)}


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


def pod_overrides(env: dict[str, str], *, container_name: str,
                  credential_mounts: bool = False) -> Optional[dict]:
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
    ``credential_mounts`` (the profile's, never the spec's) decides whether the runtime's
    credential-file Secrets are mounted. Pure/env-driven → unit-tested offline (no kubectl)."""
    pvc = env.get("VEXA_WORKSPACE_MOUNT_SOURCE")
    root = env.get("VEXA_WORKSPACE_MOUNT_TARGET")
    volumes, volume_mounts = k8s_volume_mounts(env, pvc_name=pvc or "", store_target=root or "")
    tolerations = _scheduling_json(env, TOLERATIONS_ENV, list)
    node_selector = _scheduling_json(env, NODE_SELECTOR_ENV, dict)
    # credential files go only to a workload whose profile asks for them; a meeting bot never needs
    # a model credential and must not carry one
    secret_mounts = _scheduling_json(env, SECRET_MOUNTS_ENV, list) if credential_mounts else None
    for i, sm in enumerate(secret_mounts or ()):
        if not (isinstance(sm, dict) and sm.get("secret") and sm.get("mountPath")):
            raise ValueError(f"{SECRET_MOUNTS_ENV}[{i}] must be {{secret, mountPath[, file]}}, got {sm!r}")
        name = f"cred-{i}-{sm['secret']}"[:63].rstrip("-")
        volumes.append({"name": name, "secret": {"secretName": sm["secret"]}})
        mount = {"name": name, "mountPath": sm["mountPath"], "readOnly": True}
        if sm.get("file"):
            # single-FILE mount (subPath): the surrounding directory stays writable — required by
            # harnesses that treat their config dir as state (codex: sqlite under ~/.codex);
            # mountPath is then the file's full path and `file` is the Secret key
            mount["subPath"] = sm["file"]
        volume_mounts.append(mount)
    if not volumes and not tolerations and not node_selector:
        return None
    # ``containers`` is emitted ONLY when volumeMounts force it (the workspace-store seam);
    # pod-level fields (tolerations/nodeSelector) shape the Pod without touching the list. Keeping
    # the overlay minimal is what lets ``build_pod`` merge it BY CONTAINER NAME onto the generated
    # container instead of replacing it.
    #
    # Why the merge has to be OURS (the failure this minimal shape is only safe against because
    # ``build_pod`` exists): ``kubectl run --overrides`` merges the containers LIST by replacement
    # (json-merge, not strategic), so a partial containers entry under that path wipes the generated
    # container — image, env, command — and the API server rejects the Pod
    # (`spec.containers[0].image: Required value`), killing the spawn instantly. Proven live
    # 2026-08-23: the volumeMounts-only entry here was rejected by the API server on the first real
    # agent-worker spawn on k8s (bots never mount, so the M2 bot proof never exercised it). The
    # answer was briefly to emit the COMPLETE container here; owning the whole Pod object in
    # ``build_pod`` replaced that workaround and let the overlay go back to being minimal.
    spec: dict = {}
    if volume_mounts:
        spec["containers"] = [{"name": container_name, "volumeMounts": volume_mounts}]
    if volumes:
        spec["volumes"] = volumes
    if tolerations:
        spec["tolerations"] = tolerations
    if node_selector:
        spec["nodeSelector"] = node_selector
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
    instance: str = "",
) -> dict:
    """The COMPLETE Pod object a spawn submits — every field the workload needs, in one manifest.

    Why a whole object rather than ``kubectl run --overrides``: that flag merges the container LIST
    by REPLACEMENT (JSON merge patch), so any partial ``containers`` entry erases the generated
    container's image/env/command and the API server rejects the Pod outright. Owning the object
    makes the merge OURS and deterministic — the overlay's per-container fields are merged BY
    CONTAINER NAME onto the generated container, so resources, workspace volumeMounts, image,
    command, env, labels and scheduling all coexist instead of clobbering each other.

    ``env`` is the container's env VERBATIM; ``overlay_env`` (default: ``env``) is the wider env the
    pod-shaping overlay is derived from. The profile's ``runnable.scheduling`` is laid on last. They differ because the runtime's own scheduling knobs live
    in its process env, not in the workload's — and must shape the Pod without being injected into
    the workload's container as config.

    Pure and env-driven ⇒ the whole manifest is asserted offline, with no cluster and no kubectl.
    (``kubectl run --dry-run=client`` is NOT a viable generator here: v1.34 performs API discovery
    before generating and exits 1 with no output when no server is reachable.)"""
    if runnable.credential_mounts:
        # Where the workload's harness finds the credential Secrets is profile data; a value the
        # spec already sets wins.
        env = {**runnable.credential_env, **env}
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
        "labels": {MANAGED_LABEL: "true", WORKLOAD_ID_LABEL: k8s_label_value(workload_id),
                   **({INSTANCE_LABEL: k8s_label_value(instance)} if instance else {}),
                   **runnable.labels},
        # The id verbatim — the label above may be its safe form.
        "annotations": {WORKLOAD_ID_LABEL: workload_id},
    }
    if namespace:
        metadata["namespace"] = namespace

    # restart=Never: the kernel owns restart policy, so the Pod must not resurrect itself.
    # A workload is not a cluster client: it gets no ServiceAccount token, and no service-link env
    # enumerating every Service in the namespace.
    pod: dict = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": metadata,
        "spec": {"containers": [container], "restartPolicy": "Never",
                 "automountServiceAccountToken": False, "enableServiceLinks": False},
    }

    overlay_source = env if overlay_env is None else overlay_env
    overlay = (pod_overrides(overlay_source, container_name=name,
                             credential_mounts=runnable.credential_mounts) or {}).get("spec", {})
    for key in ("volumes", "tolerations", "nodeSelector"):
        if overlay.get(key):
            pod["spec"][key] = overlay[key]
    for overlay_container in overlay.get("containers", []):
        if overlay_container.get("name") != name:
            continue                                       # merge BY NAME — never by position
        for key, value in overlay_container.items():
            if key != "name":
                container[key] = value
    # The profile's own placement (operator configuration, validated at boot): its node selector
    # and tolerations, when set, replace the runtime-wide ones above; its priority class and pull
    # secrets have no runtime-wide counterpart. Nothing here comes from the workload's env.
    runnable.scheduling.apply(pod["spec"])
    # Hardening, last so no overlay replaces it: every capability dropped but the ones the profile
    # keeps (the operator may narrow them per class, e.g. to none under OpenShift's restricted SCC),
    # no privilege escalation, the runtime's default seccomp profile unless the operator names the
    # class's own (meeting bots: the node-installed profile Chromium's sandbox needs), and non-root
    # where the image runs as one.
    keep = runnable.capabilities if runnable.scheduling.capabilities is None else runnable.scheduling.capabilities
    seccomp = ({"type": "Localhost", "localhostProfile": runnable.scheduling.seccomp_profile}
               if runnable.scheduling.seccomp_profile else {"type": "RuntimeDefault"})
    security: dict = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]},
                      "seccompProfile": seccomp}
    if keep:
        security["capabilities"]["add"] = list(keep)
    if runnable.run_as_non_root:
        security["runAsNonRoot"] = True
    container["securityContext"] = security
    return pod


class K8sBackend:
    name = "k8s"

    def __init__(self, name_prefix: str = "vexa-", namespace: Optional[str] = None,
                 instance: Optional[str] = None) -> None:
        self._prefix = name_prefix
        self._ns = namespace
        # Exit codes of Pods this backend removed once their exit was observed (name → code).
        self._exited: dict[str, int] = {}
        # Which runtime this is (the Helm release). Every Pod it spawns carries it, and adoption
        # selects on it.
        self._instance = (instance if instance is not None else os.environ.get(INSTANCE_ENV, "")).strip() or "default"
        # The runtime-wide placement every spawned Pod carries is held to the profiles' rules here,
        # at boot, so a bad value stops the runtime by name instead of placing Pods.
        pod_scheduling.validate_runtime_wide(os.environ)

    def _pname(self, workload_id: str) -> str:
        return k8s_name(f"{self._prefix}{workload_id}")  # always a DNS-1123 label (the container's too)

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
            instance=self._instance,
        )
        manifest = json.dumps(pod)
        self._exited.pop(name, None)
        try:
            _kubectl("create", "-f", "-", *self._ns_args(), stdin=manifest)
            return WorkloadHandle(id=workload_id, impl=name)
        except RuntimeError as exc:
            if "AlreadyExists" not in str(exc):
                raise
        # The name is deterministic, so a workload id that ran before (a chat's next turn) finds its
        # previous Pod still there. Ours and finished: remove it and start again. Ours and still
        # running: that IS the workload — no duplicate. Not ours: never touched.
        existing = self._pod(name)
        if existing is not None:
            if not self._ours(existing, workload_id):
                raise RuntimeError(f"pod {name} exists and is not this runtime's workload {workload_id!r}; "
                                   f"not replacing it")
            if (existing.get("status") or {}).get("phase") not in TERMINAL_PHASES:
                return WorkloadHandle(id=workload_id, impl=name)
            self._delete_and_wait(name)
        _kubectl("create", "-f", "-", *self._ns_args(), stdin=manifest)
        return WorkloadHandle(id=workload_id, impl=name)

    def _pod(self, name: str) -> Optional[dict]:
        r = _kubectl("get", "pod", name, "-o", "json", *self._ns_args(), check=False)
        if r.returncode != 0:
            return None
        try:
            return json.loads(r.stdout)
        except ValueError:
            return None

    def _ours(self, pod: dict, workload_id: str) -> bool:
        """Spawned by this runtime for this workload: the managed label, this workload id (the
        annotation, else the label a pre-annotation Pod carries), and this instance (or none, a Pod
        from before the instance label)."""
        meta = pod.get("metadata") or {}
        labels, notes = meta.get("labels") or {}, meta.get("annotations") or {}
        if labels.get(MANAGED_LABEL) != "true":
            return False
        if (notes.get(WORKLOAD_ID_LABEL) or labels.get(WORKLOAD_ID_LABEL)) not in (
                workload_id, k8s_label_value(workload_id)):
            return False
        return labels.get(INSTANCE_LABEL) in (None, k8s_label_value(self._instance))

    def _delete_and_wait(self, name: str, timeout: float = 60.0) -> None:
        _kubectl("delete", "pod", name, "--ignore-not-found", "--wait=true", f"--timeout={int(timeout)}s",
                 *self._ns_args(), check=False)
        deadline = time.time() + timeout
        while self._pod(name) is not None:
            if time.time() > deadline:
                raise RuntimeError(f"pod {name} did not go away within {int(timeout)}s")
            time.sleep(0.5)

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
        only (``runtime.managed=true`` and this runtime's ``runtime.instance``): a name-prefix
        fallback is unsafe in a shared namespace (the chart's own service Pods can share the prefix),
        and another release's Pods are not ours, so Pods spawned by a runtime that did not label
        them are not re-adopted. Never raises."""
        try:
            r = _kubectl(
                "get", "pods", "-l", f"{MANAGED_LABEL}=true,{INSTANCE_LABEL}={k8s_label_value(self._instance)}",
                "-o", "json",
                *self._ns_args(), check=False,
            )
            if r.returncode != 0:
                return []
            out = []
            for pod in json.loads(r.stdout).get("items", []):
                meta = pod.get("metadata", {})
                # The annotation carries the id verbatim; the label may be its safe form (and a Pod
                # from before the annotation carries only the label, which was then the id itself).
                wid = ((meta.get("annotations") or {}).get(WORKLOAD_ID_LABEL)
                       or (meta.get("labels") or {}).get(WORKLOAD_ID_LABEL))
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
        name = h._impl  # type: ignore[attr-defined]
        if name in self._exited:
            return self._exited[name]
        r = _kubectl("get", "pod", name, "-o", "json", *self._ns_args(), check=False)
        if r.returncode != 0:
            return 0                                     # gone (deleted/never-found) → no longer running
        status = json.loads(r.stdout).get("status", {})
        phase = status.get("phase")
        if phase not in TERMINAL_PHASES:
            return None                                  # still scheduling / running
        code = 0
        if phase == "Failed":
            code = 1
            for cs in status.get("containerStatuses", []):
                term = cs.get("state", {}).get("terminated")
                if term and "exitCode" in term:
                    code = int(term["exitCode"])
                    break
        # The exit is now observed (the kernel records it from this answer): the finished Pod is
        # removed so finished workloads do not pile up, and its code is kept for later polls.
        self._exited[name] = code
        _kubectl("delete", "pod", name, "--ignore-not-found", "--wait=false", *self._ns_args(), check=False)
        return code

    def terminate(self, h: WorkloadHandle) -> None:      # graceful: SIGTERM + grace, then SIGKILL
        _kubectl("delete", "pod", h._impl, f"--grace-period={_stop_grace_sec()}", "--wait=false",
                 *self._ns_args(), check=False)  # type: ignore[attr-defined]

    def kill(self, h: WorkloadHandle) -> None:           # force: immediate SIGKILL + drop the object
        _kubectl("delete", "pod", h._impl, "--grace-period=0", "--force", "--wait=false",
                 *self._ns_args(), check=False)  # type: ignore[attr-defined]

    def cleanup(self, h: WorkloadHandle) -> None:
        _kubectl("delete", "pod", h._impl, "--ignore-not-found", "--grace-period=0", "--force",
                 "--wait=false", *self._ns_args(), check=False)  # type: ignore[attr-defined]
