"""Strict ZAKI meeting-bot Pod contract consumed by the Kubernetes runtime backend.

The runtime normally receives an image and command through its profile registry.  ZAKI's meeting
bot adds a deliberately narrower deployment contract: one packaged JSON document supplies the
immutable bot image and the restricted Pod settings that accompany it.  The contract is parsed at
boot, before a workload can be created, so an incomplete or broadened document cannot fall back to
a generic browser Pod.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping


BOT_CONTRACT_ENV = "ZAKI_MINUTES_BOT_CONTRACT_JSON"
_BOT_IMAGE = re.compile(
    r"^ghcr\.io/projectnuggets/zaki-minutes-bot:sha-[0-9a-f]{40}@sha256:[0-9a-f]{64}$"
)
_RESOURCE_KEYS = {"cpu", "memory", "ephemeral-storage"}
_POD_VOLUME_NAMES = {"tmp", "dshm"}
_POD_MOUNTS = {"tmp": "/tmp", "dshm": "/dev/shm"}


class BotPodContractError(ValueError):
    """The packaged ZAKI bot Pod contract is absent, malformed, or too permissive."""


def _error(field: str, detail: str) -> None:
    raise BotPodContractError(f"ZAKI bot Pod contract {field}: {detail}")


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _error(field, "must be an object")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        _error(field, "must be a list")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _error(field, "must be a non-empty string")
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _error(field, "must be a positive integer")
    return value


@dataclass(frozen=True)
class BotPodContract:
    """Validated fields the runtime materializes into one `meeting-bot` Pod."""

    image: str
    image_pull_policy: str
    image_pull_secret: str
    service_account_name: str
    labels: dict[str, str]
    termination_grace_period_seconds: int
    security_context: dict[str, Any]
    container_security_context: dict[str, Any]
    resources: dict[str, dict[str, str]]
    volumes: list[dict[str, Any]]
    volume_mounts: list[dict[str, Any]]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BotPodContract | None":
        """Parse the optional packaged document, failing closed when it is present but invalid."""
        environment = os.environ if env is None else env
        raw = (environment.get(BOT_CONTRACT_ENV) or "").strip()
        if not raw:
            return None
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BotPodContractError("ZAKI bot Pod contract is not valid JSON") from exc
        return cls.from_document(document)

    @classmethod
    def from_document(cls, document: Any) -> "BotPodContract":
        """Validate contract version 1 without accepting a broader Pod specification."""
        doc = _mapping(document, "document")
        if doc.get("contractVersion") != 1:
            _error("contractVersion", "must be 1")

        image = _string(doc.get("image"), "image")
        if not _BOT_IMAGE.fullmatch(image):
            _error(
                "image",
                "must be the immutable ghcr.io/projectnuggets/zaki-minutes-bot source tag and digest",
            )

        image_pull_policy = _string(doc.get("imagePullPolicy"), "imagePullPolicy")
        if image_pull_policy not in {"Always", "IfNotPresent", "Never"}:
            _error("imagePullPolicy", "must be Always, IfNotPresent, or Never")
        image_pull_secret = _string(doc.get("imagePullSecret"), "imagePullSecret")

        service_account_name = _string(doc.get("serviceAccountName"), "serviceAccountName")
        if service_account_name != "zaki-minutes-bot":
            _error("serviceAccountName", "must be zaki-minutes-bot")
        if doc.get("automountServiceAccountToken") is not False:
            _error("automountServiceAccountToken", "must be false")

        labels = _mapping(doc.get("labels"), "labels")
        if labels != {"app.kubernetes.io/name": "zaki-minutes-bot"}:
            _error("labels", "must identify only app.kubernetes.io/name=zaki-minutes-bot")
        typed_labels = {str(key): value for key, value in labels.items()}
        if not all(isinstance(value, str) and value for value in typed_labels.values()):
            _error("labels", "values must be non-empty strings")

        termination_grace_period_seconds = _positive_int(
            doc.get("terminationGracePeriodSeconds"), "terminationGracePeriodSeconds"
        )
        security_context = _validate_pod_security_context(doc.get("securityContext"))
        container_security_context = _validate_container_security_context(
            doc.get("containerSecurityContext")
        )
        resources = _validate_resources(doc.get("resources"))
        volumes = _validate_volumes(doc.get("volumes"))
        volume_mounts = _validate_volume_mounts(doc.get("volumeMounts"))

        return cls(
            image=image,
            image_pull_policy=image_pull_policy,
            image_pull_secret=image_pull_secret,
            service_account_name=service_account_name,
            labels=typed_labels,
            termination_grace_period_seconds=termination_grace_period_seconds,
            security_context=security_context,
            container_security_context=container_security_context,
            resources=resources,
            volumes=volumes,
            volume_mounts=volume_mounts,
        )


def _validate_pod_security_context(value: Any) -> dict[str, Any]:
    context = _mapping(value, "securityContext")
    if set(context) != {"runAsNonRoot", "runAsUser", "runAsGroup", "fsGroup", "seccompProfile"}:
        _error("securityContext", "must contain only the restricted-PSA settings")
    if context.get("runAsNonRoot") is not True:
        _error("securityContext.runAsNonRoot", "must be true")
    for key in ("runAsUser", "runAsGroup", "fsGroup"):
        _positive_int(context.get(key), f"securityContext.{key}")
    seccomp = _mapping(context.get("seccompProfile"), "securityContext.seccompProfile")
    if seccomp != {"type": "RuntimeDefault"}:
        _error("securityContext.seccompProfile", "must be exactly type=RuntimeDefault")
    return context


def _validate_container_security_context(value: Any) -> dict[str, Any]:
    context = _mapping(value, "containerSecurityContext")
    if set(context) != {"allowPrivilegeEscalation", "readOnlyRootFilesystem", "capabilities"}:
        _error("containerSecurityContext", "must contain only the restricted-PSA settings")
    if context.get("allowPrivilegeEscalation") is not False:
        _error("containerSecurityContext.allowPrivilegeEscalation", "must be false")
    if context.get("readOnlyRootFilesystem") is not True:
        _error("containerSecurityContext.readOnlyRootFilesystem", "must be true")
    capabilities = _mapping(context.get("capabilities"), "containerSecurityContext.capabilities")
    if capabilities != {"drop": ["ALL"]}:
        _error("containerSecurityContext.capabilities", "must be exactly drop=[ALL]")
    return context


def _validate_resources(value: Any) -> dict[str, dict[str, str]]:
    resources = _mapping(value, "resources")
    if set(resources) != {"requests", "limits"}:
        _error("resources", "must contain exactly requests and limits")
    typed: dict[str, dict[str, str]] = {}
    for kind in ("requests", "limits"):
        quantities = _mapping(resources.get(kind), f"resources.{kind}")
        if set(quantities) != _RESOURCE_KEYS:
            _error(f"resources.{kind}", "must define cpu, memory, and ephemeral-storage")
        typed[kind] = {
            key: _string(quantities.get(key), f"resources.{kind}.{key}") for key in _RESOURCE_KEYS
        }
    return typed


def _validate_volumes(value: Any) -> list[dict[str, Any]]:
    volumes = _list(value, "volumes")
    if len(volumes) != len(_POD_VOLUME_NAMES):
        _error("volumes", "must define only tmp and dshm emptyDir volumes")
    typed: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, raw_volume in enumerate(volumes):
        volume = _mapping(raw_volume, f"volumes[{index}]")
        if set(volume) != {"name", "emptyDir"}:
            _error(f"volumes[{index}]", "must contain only name and emptyDir")
        name = _string(volume.get("name"), f"volumes[{index}].name")
        empty_dir = _mapping(volume.get("emptyDir"), f"volumes[{index}].emptyDir")
        if name == "tmp":
            if set(empty_dir) != {"sizeLimit"}:
                _error("volumes.tmp.emptyDir", "must contain only sizeLimit")
        elif name == "dshm":
            if set(empty_dir) != {"medium", "sizeLimit"} or empty_dir.get("medium") != "Memory":
                _error("volumes.dshm.emptyDir", "must contain medium=Memory and sizeLimit")
        else:
            _error(f"volumes[{index}].name", "must be tmp or dshm")
        _string(empty_dir.get("sizeLimit"), f"volumes.{name}.emptyDir.sizeLimit")
        names.add(name)
        typed.append(volume)
    if names != _POD_VOLUME_NAMES:
        _error("volumes", "must define one tmp and one dshm volume")
    return typed


def _validate_volume_mounts(value: Any) -> list[dict[str, Any]]:
    mounts = _list(value, "volumeMounts")
    if len(mounts) != len(_POD_MOUNTS):
        _error("volumeMounts", "must define only /tmp and /dev/shm mounts")
    typed: list[dict[str, Any]] = []
    mounted: dict[str, str] = {}
    for index, raw_mount in enumerate(mounts):
        mount = _mapping(raw_mount, f"volumeMounts[{index}]")
        if set(mount) != {"name", "mountPath"}:
            _error(f"volumeMounts[{index}]", "must contain only name and mountPath")
        name = _string(mount.get("name"), f"volumeMounts[{index}].name")
        mount_path = _string(mount.get("mountPath"), f"volumeMounts[{index}].mountPath")
        mounted[name] = mount_path
        typed.append(mount)
    if mounted != _POD_MOUNTS:
        _error("volumeMounts", "must mount tmp at /tmp and dshm at /dev/shm")
    return typed
