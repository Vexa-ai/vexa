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
import secrets
import subprocess
import time
from typing import Optional

from . import pod_scheduling, workload_env
from .backend import WorkloadHandle
from .models import Resources
from .mounts import k8s_volume_mounts
from .profiles import MODEL_ROUTE_ENV, ROUTE_SUBSCRIPTION, ProfileResources, Runnable
from .workload_env import forwarded_env

MANAGED_LABEL = "runtime.managed"
#: Adopt Pods that carry no ``runtime.instance`` label (spawned by a 0.12 runtime). Default on.
ADOPT_UNLABELLED_ENV = "RUNTIME_K8S_ADOPT_UNLABELLED"


def adopt_unlabelled() -> bool:
    return (os.environ.get(ADOPT_UNLABELLED_ENV) or "true").strip().lower() not in ("0", "false", "no", "off")
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
# File-shaped credentials for spawned workloads: a harness that reads a SUBSCRIPTION file (codex:
# ~/.codex/auth.json) gets it from a Secret, since a bare `kubectl run` Pod inherits no mounts. JSON
# array of {"secret": <name>, "file": <the one key>, "mountPath": <the file's path>}, mounted read-only
# — a token refresh cannot persist, so keep the Secret fresh operator-side.
#
# ONE NAMED KEY PER MOUNT, AND NEVER A MODEL KEY (H5/W1). A whole-Secret mount puts every key of that
# Secret into the worker, and the model's shell reads whatever the worker holds; so each mount names
# the one key it carries, and a key that is a model-provider API key, the model proxy's sealing key or
# the internal tier's secret is refused — those live with agent-api and the model proxy alone. A
# refused entry fails the spawn loudly, never quietly mounting less.
SECRET_MOUNTS_ENV = "RUNTIME_K8S_SECRET_MOUNTS"  # JSON array of {secret, file, mountPath}
CRED_VOLUME_PREFIX = "cred-"
def refused_secret_mount(entry: object) -> Optional[str]:
    """Why a ``RUNTIME_K8S_SECRET_MOUNTS`` entry may not be mounted, or None when it may."""
    if not (isinstance(entry, dict) and entry.get("secret") and entry.get("mountPath")):
        return "must be {secret, file, mountPath}"
    key = entry.get("file")
    if not key:
        return ("names no `file`: a whole Secret is never mounted into a workload — name the one key "
                "it carries")
    if key in workload_env.REFUSED_SECRET_KEYS or str(key).startswith(workload_env.REFUSED_SECRET_KEY_PREFIX):
        return (f"key {key!r} is a model credential or a control-plane secret, and no workload holds "
                "one (H5/W1)")
    return None
# THE KEY'S CONTENT, CHECKED IN THE POD. The runtime holds no right to read Secrets, so a key name
# (``auth.json``) says nothing about what the key holds; a model API key stored under it would reach
# the worker. Every Pod that carries a Secret mount therefore runs an init container first (the
# workload's own image and hardening) that reads each mounted key and stops the Pod when it holds a
# model-provider API key or a control-plane secret: an ``sk-`` key (an Anthropic subscription's OAuth
# ``sk-ant-oat``/``sk-ant-ort`` tokens excepted — a subscription file is what this seam is for), or a
# non-empty value assigned to a name ``refused_secret_mount`` refuses. The worker never starts with
# it, and the check never prints what it read.
SECRET_MOUNT_CHECK_DIR = "/etc/vexa/secret-mount-check"
_REFUSED_ASSIGNMENT = (
    "(" + "|".join(sorted(workload_env.REFUSED_SECRET_KEYS)) + "|"
    + workload_env.REFUSED_SECRET_KEY_PREFIX + "[A-Za-z0-9_]+)"
    + "\"?[[:space:]]*(:[[:space:]]*\"[^\"]|=[[:space:]]*\"?[^\"[:space:]])")
_API_KEY_TOKEN = "(^|[^A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}"
_SUBSCRIPTION_TOKEN = "sk-ant-o[ar]t[0-9]"
SECRET_MOUNT_CHECK_SCRIPT = (
    "set -u\n"
    "refused=0\n"
    'for f in "$@"; do\n'
    f"  if grep -Eq '{_REFUSED_ASSIGNMENT}' \"$f\" || "
    f"grep -oE '{_API_KEY_TOKEN}' \"$f\" | grep -vqE '{_SUBSCRIPTION_TOKEN}'; then\n"
    '    echo "secret mount $f refused: it holds a model-provider API key or a control-plane secret, '
    'and no worker holds one (H5/W1)" >&2\n'
    "    refused=1\n"
    "  fi\n"
    "done\n"
    'exit "$refused"\n'
)


def apply_secret_mount_check(spec: dict, container: dict) -> None:
    """Run ``SECRET_MOUNT_CHECK_SCRIPT`` over every Secret-mounted key before the workload starts."""
    mounts = [vm for vm in container.get("volumeMounts", []) if vm.get("name", "").startswith(CRED_VOLUME_PREFIX)]
    if not mounts:
        return
    security = json.loads(json.dumps(container.get("securityContext") or {}))
    (security.get("capabilities") or {}).pop("add", None)     # reading files needs none
    check: dict = {
        "name": "vexa-secret-mount-check",
        "image": container["image"],
        "command": ["/bin/sh", "-c", SECRET_MOUNT_CHECK_SCRIPT, "secret-mount-check",
                    *(f"{SECRET_MOUNT_CHECK_DIR}/{i}/{vm['subPath']}" for i, vm in enumerate(mounts))],
        "volumeMounts": [{"name": vm["name"], "mountPath": f"{SECRET_MOUNT_CHECK_DIR}/{i}", "readOnly": True}
                         for i, vm in enumerate(mounts)],
        "securityContext": security,
    }
    if container.get("resources"):
        check["resources"] = json.loads(json.dumps(container["resources"]))
    spec["initContainers"] = [check, *spec.get("initContainers", [])]


# Corporate TLS inspection (the chart's global.extraCA): every outbound HTTPS call is re-signed by
# the operator's own root CA, so every spawned Pod must trust it exactly as the chart's own Pods do.
# JSON {"configMap" | "secret": <name>, "key": <key holding the PEM>, "awsCaBundle": <bool>}. Unset ⇒
# the Pod is unchanged. Malformed is fatal at boot (K8sBackend.__init__), never silently dropped.
EXTRA_CA_ENV = "RUNTIME_K8S_EXTRA_CA"
EXTRA_CA_DIR = "/etc/vexa/extra-ca"
EXTRA_CA_FILE = f"{EXTRA_CA_DIR}/ca.crt"
CA_BUNDLE_DIR = "/etc/vexa/ca-bundle"
CA_BUNDLE_FILE = f"{CA_BUNDLE_DIR}/ca-bundle.crt"
#: The bundle writer, run by an init container from the workload's OWN image: that image's system
#: bundle (Debian/Ubuntu, then RHEL, then Alpine paths) plus the operator's PEM, into an emptyDir.
#: Same script as the chart's (templates/_helpers.tpl vexa.extraCA.script). No root, no writable
#: root filesystem; a PEM without a certificate stops the Pod instead of starting it untrusting.
EXTRA_CA_SCRIPT = (
    "set -eu\n"
    f"out={CA_BUNDLE_FILE}\n"
    f"grep -q 'BEGIN CERTIFICATE' {EXTRA_CA_FILE} || "
    "{ echo 'global.extraCA: the key holds no PEM certificate' >&2; exit 1; }\n"
    ': > "$out"\n'
    "for f in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/cert.pem; do\n"
    '  if [ -s "$f" ]; then cat "$f" >> "$out"; echo >> "$out"; break; fi\n'
    "done\n"
    f'cat {EXTRA_CA_FILE} >> "$out"\n'
    "echo \"ca-bundle: $(grep -c 'BEGIN CERTIFICATE' \"$out\") certificates\""
)
_K8S_OBJECT_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
_CONFIG_KEY = re.compile(r"^[-._a-zA-Z0-9]{1,253}$")


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


def extra_ca(env: dict[str, str]) -> Optional[dict]:
    """The operator's extra root CA (``RUNTIME_K8S_EXTRA_CA``), validated, or None when unset."""
    raw = env.get(EXTRA_CA_ENV)
    if raw is None or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"{EXTRA_CA_ENV} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{EXTRA_CA_ENV} must be a JSON object, got {type(value).__name__}")
    config_map = str(value.get("configMap") or "").strip()
    secret = str(value.get("secret") or "").strip()
    key = str(value.get("key") or "ca.crt").strip()
    if bool(config_map) == bool(secret):
        raise ValueError(f"{EXTRA_CA_ENV} must name exactly one of configMap or secret")
    name = config_map or secret
    if not _K8S_OBJECT_NAME.match(name):
        raise ValueError(f"{EXTRA_CA_ENV}: {name!r} is not a Kubernetes object name")
    if not _CONFIG_KEY.match(key):
        raise ValueError(f"{EXTRA_CA_ENV}: {key!r} is not a ConfigMap/Secret key")
    if secret and (key in workload_env.REFUSED_SECRET_KEYS
                   or key.startswith(workload_env.REFUSED_SECRET_KEY_PREFIX)):
        # the CA's key is mounted into every spawned Pod, so it is held to RUNTIME_K8S_SECRET_MOUNTS'
        # rule: a model credential or a control-plane secret never reaches a workload (H5/W1)
        raise ValueError(f"{EXTRA_CA_ENV}: key {key!r} is a model credential or a control-plane secret")
    return {"configMap": config_map, "secret": secret, "key": key,
            "awsCaBundle": bool(value.get("awsCaBundle", True))}


def extra_ca_env(ca: dict) -> dict[str, str]:
    """The trust env every runtime in a Vexa Pod reads: OpenSSL, requests, curl, git and botocore
    take the full bundle; Node adds the PEM to its own roots; the bot imports the PEM into
    Chromium's NSS store (entrypoint.sh), since Chromium on Linux reads no bundle file."""
    env = {"SSL_CERT_FILE": CA_BUNDLE_FILE, "REQUESTS_CA_BUNDLE": CA_BUNDLE_FILE,
           "CURL_CA_BUNDLE": CA_BUNDLE_FILE, "GIT_SSL_CAINFO": CA_BUNDLE_FILE,
           "NODE_EXTRA_CA_CERTS": EXTRA_CA_FILE, "VEXA_EXTRA_CA_FILE": EXTRA_CA_FILE}
    if ca.get("awsCaBundle"):
        env["AWS_CA_BUNDLE"] = CA_BUNDLE_FILE
    return env


def apply_extra_ca(spec: dict, container: dict, ca: dict) -> None:
    """Give a spawned Pod the operator's CA: the PEM mounted read-only, an init container (the
    workload's own image, its own hardening and sizing) writing image bundle + PEM into an emptyDir
    mounted read-only, and the trust env. A key the workload already sets is never overridden."""
    source = ({"configMap": {"name": ca["configMap"], "defaultMode": 0o444,
                             "items": [{"key": ca["key"], "path": "ca.crt"}]}}
              if ca["configMap"] else
              {"secret": {"secretName": ca["secret"], "defaultMode": 0o444,
                          "items": [{"key": ca["key"], "path": "ca.crt"}]}})
    spec.setdefault("volumes", [])
    spec["volumes"] += [{"name": "vexa-extra-ca", **source},
                        {"name": "vexa-ca-bundle", "emptyDir": {"sizeLimit": "8Mi"}}]
    security = json.loads(json.dumps(container.get("securityContext") or {}))
    (security.get("capabilities") or {}).pop("add", None)     # the bundle writer needs none
    init: dict = {
        "name": "vexa-ca-bundle",
        "image": container["image"],
        "command": ["/bin/sh", "-c", EXTRA_CA_SCRIPT],
        "volumeMounts": [{"name": "vexa-extra-ca", "mountPath": EXTRA_CA_DIR, "readOnly": True},
                         {"name": "vexa-ca-bundle", "mountPath": CA_BUNDLE_DIR}],
        "securityContext": security,
    }
    if container.get("resources"):
        # the same sizing as the workload: a quota that requires it on every container admits the
        # Pod, and the Pod's effective request (max of init, sum of containers) does not grow
        init["resources"] = json.loads(json.dumps(container["resources"]))
    spec["initContainers"] = [init, *spec.get("initContainers", [])]
    container.setdefault("volumeMounts", [])
    container["volumeMounts"] += [
        {"name": "vexa-extra-ca", "mountPath": EXTRA_CA_DIR, "readOnly": True},
        {"name": "vexa-ca-bundle", "mountPath": CA_BUNDLE_DIR, "readOnly": True}]
    present = {e.get("name") for e in container.get("env", [])}
    container.setdefault("env", [])
    container["env"] += [{"name": k, "value": v} for k, v in extra_ca_env(ca).items() if k not in present]


def _runtime_scheduling_env() -> dict[str, str]:
    """The runtime's own scheduling knobs from its PROCESS env (set by the chart on the runtime
    Deployment). Overlaid onto the per-workload spawn env for ``pod_overrides`` — spec.env cannot
    carry these: it is built per-workload by different producers (meeting-api for a bot, agent-api for
    an agent worker), whereas the scheduling constraints are a property of the runtime/backend."""
    return {k: os.environ[k] for k in (TOLERATIONS_ENV, NODE_SELECTOR_ENV, SECRET_MOUNTS_ENV, EXTRA_CA_ENV)
            if os.environ.get(k)}


def _kubectl(*args: str, check: bool = True, stdin: Optional[str] = None) -> subprocess.CompletedProcess:
    r = subprocess.run(["kubectl", *args], capture_output=True, text=True, input=stdin)
    if check and r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def _secret_class(env: dict[str, str]) -> set[str]:
    """The keys of a container env that are credentials: those the runtime's config contract marks
    ``secret`` — and any it does not declare at all (a dispatch's tokens, a bot's constructor),
    which are treated as secret rather than guessed to be harmless. An empty value carries nothing
    and stays plain."""
    from .config_preflight import load_declaration

    declared = {k["key"]: bool(k.get("secret")) for k in load_declaration().get("keys") or []}
    return {k for k, v in env.items() if v and declared.get(k, True)}


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
    credential-file Secrets are mounted, and only into a workload whose model route is the
    subscription (``profiles.hands_subscription``). Pure/env-driven → unit-tested offline (no kubectl)."""
    pvc = env.get("VEXA_WORKSPACE_MOUNT_SOURCE")
    root = env.get("VEXA_WORKSPACE_MOUNT_TARGET")
    volumes, volume_mounts = k8s_volume_mounts(env, pvc_name=pvc or "", store_target=root or "")
    tolerations = _scheduling_json(env, TOLERATIONS_ENV, list)
    node_selector = _scheduling_json(env, NODE_SELECTOR_ENV, dict)
    # credential files go only to a workload whose profile asks for them; a meeting bot never needs
    # a model credential and must not carry one
    secret_mounts = _scheduling_json(env, SECRET_MOUNTS_ENV, list) if credential_mounts else None
    for i, sm in enumerate(secret_mounts or ()):
        why = refused_secret_mount(sm)
        if why:
            raise ValueError(f"{SECRET_MOUNTS_ENV}[{i}] refused: {why}")
    # the Secrets are subscription files: checked for every worker, mounted only into one whose
    # model route is the subscription (profiles.hands_subscription)
    if (env.get(MODEL_ROUTE_ENV) or "") != ROUTE_SUBSCRIPTION:
        secret_mounts = None
    for i, sm in enumerate(secret_mounts or ()):
        name = f"{CRED_VOLUME_PREFIX}{i}-{sm['secret']}"[:63].rstrip("-")
        # only the named key is projected into the volume, so nothing else of the Secret is there
        volumes.append({"name": name, "secret": {"secretName": sm["secret"],
                                                 "items": [{"key": sm["file"], "path": sm["file"]}]}})
        # single-FILE mount (subPath): the surrounding directory stays writable — required by
        # harnesses that treat their config dir as state (codex: sqlite under ~/.codex); mountPath
        # is the file's full path and `file` is the Secret key
        volume_mounts.append({"name": name, "mountPath": sm["mountPath"], "readOnly": True,
                              "subPath": sm["file"]})
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
    """Map the effective ``Resources`` to a container's ``resources`` block.

    v1 carries ONE value per dimension, so cpu/memory set BOTH the request and the limit — the
    minimum non-breaking contract for a namespace whose ResourceQuota requires each container to
    declare both, and Guaranteed QoS for the workload. The sealed contract does not model separate
    request/limit semantics and this mapping does not invent them. The one exception is deployment
    config, not contract: a profile's ``ProfileResources.cpuLimit`` (``RUNTIME_BOT_CPU_LIMIT`` for
    the meeting bot) raises the CPU limit above the CPU request, which makes the Pod Burstable.

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
        requests["cpu"] = f"{round(resources.cpu * 1000)}m"
        cpu_limit = resources.cpuLimit if isinstance(resources, ProfileResources) else None
        limits["cpu"] = f"{round((cpu_limit or resources.cpu) * 1000)}m"
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
    secret_env: Optional[tuple[str, tuple[str, ...]]] = None,
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
    hidden_keys = set(secret_env[1]) if secret_env else set()
    if runnable.credential_mounts:
        # Where the workload's harness finds the credential Secrets is profile data; a value the
        # spec already sets wins (also when that value rides the workload's Secret).
        env = {**{k: v for k, v in runnable.credential_env.items() if k not in hidden_keys}, **env}
    container: dict = {
        "name": name,
        "image": runnable.image,
        "env": [{"name": k, "value": v} for k, v in env.items()],
    }
    if secret_env:
        # ``(secret name, keys)``: values that are credentials reach the container by reference to
        # the workload's own Secret, never as literal values in the Pod spec.
        secret_name, keys = secret_env
        container["env"] += [{"name": k, "valueFrom": {"secretKeyRef": {"name": secret_name, "key": k}}}
                             for k in keys]
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
    # The Secret-mounted keys' content, after hardening so the init container copies it.
    apply_secret_mount_check(pod["spec"], container)
    # Corporate TLS inspection: the operator's CA, after hardening so the init container copies it.
    ca = extra_ca(overlay_source)
    if ca:
        apply_extra_ca(pod["spec"], container, ca)
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
        extra_ca(dict(os.environ))                       # a malformed extra CA stops the boot by name

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
        # The profile's forward list (the worker's model, its dials and caps), from the runtime's own
        # environment, exactly as the docker and process backends forward it: a key the spec
        # already carries is never refilled. A key the runtime's config contract marks secret goes
        # into a Secret of this Pod's own (created once the Pod exists, owned by it, so it is
        # collected with it) and reaches the container by secretKeyRef; the rest stays plain env.
        # The split covers the WHOLE container env — what the spec stamps (a catalog dispatch carries
        # its credentials and the unit's tokens itself) as well as what the runtime forwards.
        full = {**env, **forwarded_env(runnable.forward_env, os.environ, env)}
        secret_keys = _secret_class(full)
        hidden = {k: v for k, v in full.items() if k in secret_keys}
        env = {k: v for k, v in full.items() if k not in secret_keys}
        secret_name = f"{name[:240]}-env-{secrets.token_hex(4)}" if hidden else None
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
            overlay_env={**full, **_runtime_scheduling_env()},
            instance=self._instance,
            secret_env=(secret_name, tuple(sorted(hidden))) if hidden else None,
        )
        manifest = json.dumps(pod)
        self._exited.pop(name, None)
        try:
            self._create(name, workload_id, manifest, secret_name, hidden)
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
        self._create(name, workload_id, manifest, secret_name, hidden)
        return WorkloadHandle(id=workload_id, impl=name)

    def _create(self, name: str, workload_id: str, manifest: str, secret_name: Optional[str],
                hidden: dict[str, str]) -> None:
        """Create the Pod, then (when it has secret-class env) its Secret, owned by the Pod so the
        cluster deletes it with the Pod. The Pod's container waits for the Secret it references; a
        Secret that cannot be created takes the Pod down with it. The Secret is named per
        incarnation, so the runtime only ever creates Secrets: it never reads or deletes one."""
        if not secret_name:
            _kubectl("create", "-f", "-", *self._ns_args(), stdin=manifest)
            return
        r = _kubectl("create", "-f", "-", "-o", "json", *self._ns_args(), stdin=manifest)
        try:
            uid = ((json.loads(r.stdout or "{}").get("metadata") or {}).get("uid")
                   or ((self._pod(name) or {}).get("metadata") or {}).get("uid"))
            if not uid:
                raise RuntimeError(f"pod {name} has no uid to own its env Secret")
            secret = {
                "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                "metadata": {
                    "name": secret_name,
                    "labels": {MANAGED_LABEL: "true", WORKLOAD_ID_LABEL: k8s_label_value(workload_id),
                               INSTANCE_LABEL: k8s_label_value(self._instance)},
                    "ownerReferences": [{"apiVersion": "v1", "kind": "Pod", "name": name, "uid": uid}],
                },
                "stringData": dict(hidden),
            }
            _kubectl("create", "-f", "-", *self._ns_args(), stdin=json.dumps(secret))
        except Exception:
            _kubectl("delete", "pod", name, "--ignore-not-found", "--wait=false", *self._ns_args(), check=False)
            raise

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
        and another release's Pods are not ours. Never raises.

        Pods with NO ``runtime.instance`` label were spawned by a runtime from before the label
        (0.12): a bot still in its meeting across the upgrade. ``_owns`` already treats such a Pod
        as this runtime's (find/stop reach it), so adoption takes it too — otherwise meeting-api
        sees the live bot as untracked and, after its escalation window, fails the meeting under it.
        ``RUNTIME_K8S_ADOPT_UNLABELLED=false`` leaves them out (two releases sharing a namespace
        across an upgrade)."""
        selectors = [f"{MANAGED_LABEL}=true,{INSTANCE_LABEL}={k8s_label_value(self._instance)}"]
        if adopt_unlabelled():
            selectors.append(f"{MANAGED_LABEL}=true,!{INSTANCE_LABEL}")
        out: list[dict] = []
        seen: set[str] = set()
        for selector in selectors:
            for item in self._list_by_selector(selector):
                if item["name"] not in seen:
                    seen.add(item["name"])
                    out.append(item)
        return out

    def _list_by_selector(self, selector: str) -> list[dict]:
        try:
            r = _kubectl(
                "get", "pods", "-l", selector,
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
