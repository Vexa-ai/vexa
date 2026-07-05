"""mounts.py — the runtime's workspace MOUNT-SET plumbing, shared by all three backends.

A dispatch carries an ORDERED mount set (agent-api WP-A1.1): the private baseline plus every workspace
the subject activated (WP-A2.1). The env delivers it in two layers, and this module turns BOTH into the
``(source, target)`` bind pairs a backend materializes — so the docker/k8s/process backends share ONE
mount-computing path instead of each open-coding it (and each special-casing "two"):

  * ``VEXA_WORKSPACE_MOUNT_SOURCE`` + ``VEXA_WORKSPACE_MOUNT_TARGET`` — the store BACKING bind: the host
    path / named volume that holds the whole workspace store, bound once at the store root. Every mount
    in the set lives UNDER this root today, so this single bind already exposes all N of them.
  * ``VEXA_MOUNTS`` — the ordered ``[{slug, path, role, write, primary}]`` set. ``path`` is the absolute
    container path of each active workspace. When a mount's path is NOT under the bound store root (a
    future shared workspace backed by a DIFFERENT store — later WPs), it needs its OWN bind; this module
    emits one per such out-of-store mount. In-store mounts add no extra bind (the root bind covers them).

The result is a de-duplicated, ORDER-PRESERVING list of ``MountBind(source, target, read_only)``. Pure +
env-driven, so all three backends are mount-tested offline with a plain env dict (no docker/kubectl).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Mapping, Optional

logger = logging.getLogger("runtime_kernel.mounts")


@dataclass(frozen=True)
class MountBind:
    """One substrate bind: expose host ``source`` at container ``target``. ``read_only`` reflects the
    mount's write flag (a viewer-role / ro mount binds ``:ro``); the store backing bind is always rw
    (the token at the agent boundary is the real write gate — the bind just ports the bytes in)."""

    source: str
    target: str
    read_only: bool = False


def _parse_mounts(raw: Optional[str]) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("VEXA_MOUNTS is not valid JSON — ignoring the extra mount set")
        return []
    return [m for m in data if isinstance(m, dict) and m.get("path")] if isinstance(data, list) else []


def _under(path: str, root: str) -> bool:
    """Is ``path`` at or below ``root`` (so the store-root bind already exposes it)?"""
    if not root:
        return False
    p = os.path.normpath(path)
    r = os.path.normpath(root)
    return p == r or p.startswith(r.rstrip("/") + "/")


def workspace_binds(env: Mapping[str, str]) -> list[MountBind]:
    """The ordered store + mount-set binds a backend must materialize for one dispatch.

    Always starts with the store-backing bind (source→target) when both are set — that single bind
    exposes every in-store mount. Then, for each ``VEXA_MOUNTS`` entry whose path is OUTSIDE that root,
    one extra bind (a future cross-store shared workspace). De-duplicated, order-preserving."""
    binds: list[MountBind] = []
    seen: set[tuple[str, str]] = set()

    def add(source: str, target: str, read_only: bool) -> None:
        key = (source, target)
        if source and target and key not in seen:
            seen.add(key)
            binds.append(MountBind(source, target, read_only))

    src = env.get("VEXA_WORKSPACE_MOUNT_SOURCE")
    root = env.get("VEXA_WORKSPACE_MOUNT_TARGET")
    if src and root:
        add(src, root, read_only=False)  # the whole store; per-workspace write is gated by the token

    for m in _parse_mounts(env.get("VEXA_MOUNTS")):
        path = m["path"]
        source = m.get("source")
        # A mount rides the store-root bind ONLY when it lives under the root AND has no distinct host
        # source. The _global GLOBAL SYSTEM tier is bound at ``<root>/_global`` (under the root) but is
        # backed by a SEPARATE host repo (``source``) — so it needs its OWN read-only bind even though
        # its target sits under the store root; likewise a future cross-store shared workspace.
        if not source and root and _under(path, root):
            continue  # already exposed by the store-root bind — no extra bind needed
        add(source or path, path, read_only=not m.get("write", True))

    return binds


def mount_set(env: Mapping[str, str]) -> list[dict]:
    """The ordered active mount set as declared by the dispatch (``VEXA_MOUNTS``), or the lone private
    baseline derived from the legacy env when a dispatch predates the set. A stable view for BOTH the
    backends (what to expose) and observability/tests."""
    mounts = _parse_mounts(env.get("VEXA_MOUNTS"))
    if mounts:
        return mounts
    path = env.get("VEXA_WORKSPACE_PATH")
    if path:
        return [{"slug": os.path.basename(path.rstrip("/")), "path": path,
                 "role": "private", "write": True, "primary": True}]
    return []


def k8s_volume_mounts(env: Mapping[str, str], *, pvc_name: str, store_target: str) -> tuple[list[dict], list[dict]]:
    """The k8s ``volumes`` + ``volumeMounts`` for the mount set, from the SAME env the docker binds read.

    The whole workspace store is ONE RWX PVC (``pvc_name``) bound at the store root (``store_target``) —
    that single volumeMount exposes every in-store active workspace (they all live under the root), so N
    mounts need no extra k8s volumes. An out-of-store mount (a future cross-store shared workspace on its
    OWN PVC) would add a volume here; none exist yet, so this returns the single store volume. Pure +
    env-driven (no kubectl) so the k8s mount plumbing is unit-tested offline.

    Returns ``(volumes, volume_mounts)`` ready to splat into a Pod ``--overrides`` spec."""
    if not pvc_name or not store_target:
        return [], []
    vol_name = "workspace-store"
    volumes = [{"name": vol_name, "persistentVolumeClaim": {"claimName": pvc_name}}]
    volume_mounts = [{"name": vol_name, "mountPath": store_target}]
    return volumes, volume_mounts
