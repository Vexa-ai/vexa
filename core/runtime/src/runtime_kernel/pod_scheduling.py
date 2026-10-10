"""pod_scheduling.py — where the k8s backend places one kind of workload's Pods, as profile data.

An operator running bots and agent workers on their own node pool (a dedicated, tainted pool; a
pull secret for a private registry; a priority class below the customer's) sets these per profile.
They are the runtime's own configuration, never the caller's:

* read from the runtime's process environment, ``RUNTIME_K8S_<CLASS>_*``, once, when the profile
  registry is built at boot (``profiles.default_registry``);
* validated there, so a malformed value stops the boot by name instead of stranding a meeting's Pod
  at spawn time;
* carried on the profile's ``Runnable``. A spec cannot reach them: ``WorkloadSpec`` has no such
  field, and a spec env key under ``RUNTIME_K8S_`` is dropped (``workload_env``).

A toleration with no key (it tolerates every taint) or for a control-plane or system taint
(``node-role.kubernetes.io/control-plane``, ``…/master``, ``CriticalAddonsOnly``) is refused unless
the operator sets ``RUNTIME_K8S_ALLOW_BROAD_TOLERATIONS=true``; the runtime-wide settings are held to
the same rules at boot (:func:`validate_runtime_wide`).

Empty is the default for every field and means "nothing of this profile's own": the Pod carries
the runtime-wide ``RUNTIME_K8S_NODE_SELECTOR`` / ``RUNTIME_K8S_TOLERATIONS`` exactly as before. A
profile's own node selector or tolerations, when set, replace the runtime-wide value for that field.

Only the k8s backend applies this; the docker and process backends have no scheduler to tell.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

#: Suffixes of the settings, after the profile's prefix (``RUNTIME_K8S_BOT_`` …).
NODE_SELECTOR = "NODE_SELECTOR"
TOLERATIONS = "TOLERATIONS"
PRIORITY_CLASS_NAME = "PRIORITY_CLASS_NAME"
IMAGE_PULL_SECRETS = "IMAGE_PULL_SECRETS"
CAPABILITIES = "CAPABILITIES"
SECCOMP_PROFILE = "SECCOMP_PROFILE"
SUFFIXES = (NODE_SELECTOR, TOLERATIONS, PRIORITY_CLASS_NAME, IMAGE_PULL_SECRETS, CAPABILITIES, SECCOMP_PROFILE)
#: The runtime-wide settings, validated at boot by the same rules as a profile's own.
RUNTIME_WIDE_PREFIX = "RUNTIME_K8S_"
#: The operator's explicit opt-in to tolerations that reach any node or the cluster's own nodes.
ALLOW_BROAD_TOLERATIONS = "RUNTIME_K8S_ALLOW_BROAD_TOLERATIONS"
#: Taints that keep ordinary workloads off control-plane and system nodes. A spawned bot or worker
#: tolerating them would land beside the cluster's own components.
_SYSTEM_TAINT_KEYS = frozenset({"node-role.kubernetes.io/control-plane", "node-role.kubernetes.io/master",
                                "CriticalAddonsOnly"})
_CAPABILITY = re.compile(r"^[A-Z][A-Z0-9_]*$")
#: A Localhost seccomp profile's path, relative to the kubelet's seccomp root: plain segments only.
_LOCALHOST_PROFILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$")

# Kubernetes' own name rules (apimachinery validation): a DNS-1123 subdomain for object names, a
# qualified name for label and taint keys, a label value for selector and toleration values.
_DNS1123_SUBDOMAIN = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$")
_QUALIFIED_NAME_PART = re.compile(r"^([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9]$")
_TOLERATION_FIELDS = frozenset({"key", "operator", "value", "effect", "tolerationSeconds"})
_TOLERATION_OPERATORS = frozenset({"Equal", "Exists"})
_TAINT_EFFECTS = frozenset({"", "NoSchedule", "PreferNoSchedule", "NoExecute"})
#: Kubernetes reserves this prefix for its own critical classes (system-node-critical,
#: system-cluster-critical). A spawned workload must never preempt those.
_RESERVED_PRIORITY_PREFIX = "system-"


@dataclass(frozen=True)
class PodScheduling:
    """One profile's Pod placement. Every field empty ⇒ the profile adds nothing to its Pods."""

    node_selector: Mapping[str, str] = field(default_factory=dict)
    tolerations: tuple[Mapping[str, object], ...] = ()
    priority_class_name: str = ""
    image_pull_secrets: tuple[str, ...] = ()
    #: The Linux capabilities the class's containers keep (all others dropped), when the operator
    #: overrides the profile's own list — ``None`` keeps the profile's. OpenShift's restricted SCC
    #: admits no added capability: set ``[]`` there.
    capabilities: Optional[tuple[str, ...]] = None
    #: A Localhost seccomp profile the class's containers run under instead of the runtime's default
    #: (a path relative to the kubelet's seccomp root, the file present on the node). The chart sets
    #: it for meeting bots: the profile that lets Chromium build its sandbox. Empty ⇒ RuntimeDefault.
    seccomp_profile: str = ""

    def apply(self, pod_spec: dict) -> None:
        """Lay this profile's placement onto a Pod ``spec`` (in place). A set node selector or
        toleration list replaces whatever the runtime-wide setting put there; empty fields leave
        the spec as it is."""
        if self.node_selector:
            pod_spec["nodeSelector"] = dict(self.node_selector)
        if self.tolerations:
            pod_spec["tolerations"] = [dict(t) for t in self.tolerations]
        if self.priority_class_name:
            pod_spec["priorityClassName"] = self.priority_class_name
        if self.image_pull_secrets:
            pod_spec["imagePullSecrets"] = [{"name": n} for n in self.image_pull_secrets]


def _dns1123_subdomain(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 253 and bool(_DNS1123_SUBDOMAIN.match(value))


def _qualified_name(value: object) -> bool:
    """A label or taint key: ``[prefix/]name``, prefix a DNS-1123 subdomain, name ≤ 63."""
    if not isinstance(value, str) or not value:
        return False
    prefix, slash, name = value.rpartition("/")
    if slash and not _dns1123_subdomain(prefix):
        return False
    return len(name) <= 63 and bool(_QUALIFIED_NAME_PART.match(name))


def _label_value(value: object) -> bool:
    return isinstance(value, str) and (value == "" or (len(value) <= 63 and bool(_QUALIFIED_NAME_PART.match(value))))


def _json(env: Mapping[str, str], key: str, expected: type) -> Optional[object]:
    raw = (env.get(key) or "").strip()
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{key} is not valid JSON") from exc
    if not isinstance(value, expected):
        raise ValueError(f"{key} must be a JSON {expected.__name__}, got {type(value).__name__}")
    return value


def _node_selector(env: Mapping[str, str], key: str) -> dict[str, str]:
    value = _json(env, key, dict) or {}
    for label, wanted in value.items():
        if not _qualified_name(label):
            raise ValueError(f"{key}: {label!r} is not a valid label key")
        if not _label_value(wanted):
            raise ValueError(f"{key}: the value for {label!r} is not a valid label value")
    return dict(value)


def _toleration(key: str, i: int, t: object, allow_broad: bool = False) -> dict:
    where = f"{key}[{i}]"
    if not isinstance(t, dict):
        raise ValueError(f"{where} must be a toleration object")
    unknown = sorted(set(t) - _TOLERATION_FIELDS)
    if unknown:
        raise ValueError(f"{where} has unknown field(s) {unknown}")
    operator = t.get("operator", "Equal")
    if operator not in _TOLERATION_OPERATORS:
        raise ValueError(f"{where}.operator must be Equal or Exists")
    taint_key = t.get("key", "")
    if taint_key == "":
        if operator != "Exists":
            raise ValueError(f"{where}: a toleration without a key must use operator Exists")
        if not allow_broad:
            raise ValueError(f"{where}: a toleration with no key tolerates every taint; set "
                             f"{ALLOW_BROAD_TOLERATIONS}=true to allow it")
    elif not _qualified_name(taint_key):
        raise ValueError(f"{where}.key is not a valid taint key")
    elif taint_key in _SYSTEM_TAINT_KEYS and not allow_broad:
        raise ValueError(f"{where}: {taint_key!r} keeps workloads off control-plane and system nodes; "
                         f"set {ALLOW_BROAD_TOLERATIONS}=true to allow it")
    value = t.get("value", "")
    if operator == "Exists" and value not in ("", None):
        raise ValueError(f"{where}: operator Exists takes no value")
    if operator == "Equal" and not _label_value(value):
        raise ValueError(f"{where}.value is not a valid taint value")
    effect = t.get("effect", "")
    if effect not in _TAINT_EFFECTS:
        raise ValueError(f"{where}.effect must be one of NoSchedule, PreferNoSchedule, NoExecute")
    if "tolerationSeconds" in t:
        seconds = t["tolerationSeconds"]
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            raise ValueError(f"{where}.tolerationSeconds must be an integer")
        if effect != "NoExecute":
            raise ValueError(f"{where}.tolerationSeconds is only valid with effect NoExecute")
    return dict(t)


def _allow_broad(env: Mapping[str, str]) -> bool:
    raw = (env.get(ALLOW_BROAD_TOLERATIONS) or "").strip().lower()
    if raw not in ("", "true", "false"):
        raise ValueError(f"{ALLOW_BROAD_TOLERATIONS} must be true or false")
    return raw == "true"


def _tolerations(env: Mapping[str, str], key: str) -> tuple[dict, ...]:
    allow_broad = _allow_broad(env)
    return tuple(_toleration(key, i, t, allow_broad) for i, t in enumerate(_json(env, key, list) or []))


def _capabilities(env: Mapping[str, str], key: str) -> Optional[tuple[str, ...]]:
    if not (env.get(key) or "").strip():
        return None
    names = []
    for i, item in enumerate(_json(env, key, list) or []):
        name = item[4:] if isinstance(item, str) and item.startswith("CAP_") else item
        if not isinstance(name, str) or not _CAPABILITY.match(name):
            raise ValueError(f"{key}[{i}] must be a capability name (e.g. CHOWN)")
        if name in ("ALL", "SYS_ADMIN", "SYS_PTRACE", "SYS_MODULE", "NET_ADMIN", "SYS_RAWIO", "BPF"):
            raise ValueError(f"{key}[{i}]: {name} is not a capability a spawned workload may keep")
        names.append(name)
    return tuple(dict.fromkeys(names))


def _seccomp_profile(env: Mapping[str, str], key: str) -> str:
    value = (env.get(key) or "").strip()
    if not value:
        return ""
    if len(value) > 253 or ".." in value.split("/") or not _LOCALHOST_PROFILE.match(value):
        raise ValueError(f"{key} must be a profile path relative to the kubelet's seccomp root "
                         f"(e.g. vexa/seccomp-userns.json)")
    return value


def _priority_class(env: Mapping[str, str], key: str) -> str:
    value = (env.get(key) or "").strip()
    if not value:
        return ""
    if not _dns1123_subdomain(value):
        raise ValueError(f"{key} is not a valid PriorityClass name")
    if value.startswith(_RESERVED_PRIORITY_PREFIX):
        raise ValueError(f"{key}: {value!r} is one of Kubernetes' own critical classes; "
                         f"a spawned workload must not preempt those")
    return value


def _pull_secrets(env: Mapping[str, str], key: str) -> tuple[str, ...]:
    names = []
    for i, item in enumerate(_json(env, key, list) or []):
        # A Secret name, or the Pod spec's own shape {"name": ...}.
        if isinstance(item, dict) and set(item) == {"name"}:
            item = item["name"]
        if not _dns1123_subdomain(item):
            raise ValueError(f"{key}[{i}] must be a Secret name")
        names.append(item)
    return tuple(dict.fromkeys(names))


def from_env(prefix: str, env: Mapping[str, str]) -> PodScheduling:
    """The placement the settings ``<prefix>NODE_SELECTOR`` … ``<prefix>IMAGE_PULL_SECRETS`` in
    ``env`` describe. Unset or empty ⇒ that field is empty. Anything malformed raises
    :class:`ValueError` naming the setting (and never echoing a whole value back)."""
    return PodScheduling(
        node_selector=_node_selector(env, prefix + NODE_SELECTOR),
        tolerations=_tolerations(env, prefix + TOLERATIONS),
        priority_class_name=_priority_class(env, prefix + PRIORITY_CLASS_NAME),
        image_pull_secrets=_pull_secrets(env, prefix + IMAGE_PULL_SECRETS),
        capabilities=_capabilities(env, prefix + CAPABILITIES),
        seccomp_profile=_seccomp_profile(env, prefix + SECCOMP_PROFILE),
    )


def validate_runtime_wide(env: Mapping[str, str]) -> None:
    """Validate the runtime-wide ``RUNTIME_K8S_NODE_SELECTOR`` / ``RUNTIME_K8S_TOLERATIONS`` (every
    spawned Pod carries them unless its profile sets its own) by the same rules, at boot. Raises
    :class:`ValueError` naming the setting."""
    _allow_broad(env)
    _node_selector(env, RUNTIME_WIDE_PREFIX + NODE_SELECTOR)
    _tolerations(env, RUNTIME_WIDE_PREFIX + TOLERATIONS)
