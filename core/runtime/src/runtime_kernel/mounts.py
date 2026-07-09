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
    mount's write flag (a viewer-role / ro mount binds ``:ro``).

    ``volume_subpath`` (strict isolation, named-volume store only): ``source`` is the store VOLUME and
    only this store-relative subpath of it is exposed — the docker backend materializes it via the
    Mounts API's ``VolumeOptions.Subpath`` (engine ≥ v26). ``None`` = a plain source→target bind."""

    source: str
    target: str
    read_only: bool = False
    volume_subpath: Optional[str] = None


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
    """The ordered binds a backend must materialize for one dispatch — one bind PER MOUNT in the set,
    so the container's filesystem physically contains ONLY the dispatch's declared workspaces: another
    tenant's data simply isn't reachable (tenant isolation enforced by the mount table, not by prompt
    instructions). The whole-store root bind is never emitted.

    An in-store mount is exposed as the store's subpath: a host-path store joins the path; a
    named-volume store rides ``volume_subpath`` (docker ``VolumeOptions.Subpath`` — REQUIRES engine
    ≥ v26; older engines fail the container create loudly). Read-only roles bind ``:ro`` — enforced.
    De-duplicated, order-preserving."""
    binds: list[MountBind] = []
    seen: set[tuple[str, str]] = set()

    def add(source: str, target: str, read_only: bool, volume_subpath: Optional[str] = None) -> None:
        key = (source, target)
        if source and target and key not in seen:
            seen.add(key)
            binds.append(MountBind(source, target, read_only, volume_subpath))

    src = env.get("VEXA_WORKSPACE_MOUNT_SOURCE")
    root = env.get("VEXA_WORKSPACE_MOUNT_TARGET")

    for m in mount_set(env):
        path = m["path"]
        source = m.get("source")
        read_only = not m.get("write", True)
        if not source and root and _under(path, root):
            if not src:
                continue  # no store backing declared (process backend env) — nothing to bind
            rel = os.path.relpath(os.path.normpath(path), os.path.normpath(root))
            if rel == ".":
                continue  # a mount AT the root would re-expose the whole store — never emit it
            if src.startswith("/"):
                # host-path store: expose exactly this workspace by joining the subpath
                add(os.path.join(src, rel), path, read_only)
            else:
                # named-volume store: the backend mounts the volume's subpath (docker ≥ v26)
                add(src, path, read_only, volume_subpath=rel)
            continue
        # A mount with its OWN host source (the _global GLOBAL SYSTEM tier, a future cross-store shared
        # workspace) — or one outside the store root — binds source→target directly.
        add(source or path, path, read_only)

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

    The workspace store is ONE RWX PVC (``pvc_name``): one ``volumeMount`` PER MOUNT in the set — same
    PVC, per-mount ``subPath`` + ``readOnly`` — so the Pod's filesystem contains only the dispatch's
    declared workspaces (``subPath`` is native k8s; no version caveat). The whole-store root mount is
    never emitted. A mount with its OWN host source (``_global``) is SKIPPED with a warning — hostPath
    volumes were never emitted here (k8s deployments bake ``_global`` differently); an out-of-store
    mount on its own PVC is a later WP. Pure + env-driven (no kubectl) so the k8s mount plumbing is
    unit-tested offline.

    Returns ``(volumes, volume_mounts)`` ready to splat into a Pod ``--overrides`` spec."""
    if not pvc_name or not store_target:
        return [], []
    vol_name = "workspace-store"
    volumes = [{"name": vol_name, "persistentVolumeClaim": {"claimName": pvc_name}}]
    volume_mounts: list[dict] = []
    seen: set[str] = set()
    for m in mount_set(env):
        path = m["path"]
        if m.get("source") or not _under(path, store_target):
            logger.warning("k8s: mount %s has its own source / sits outside the store — not exposed "
                           "(hostPath is never emitted; give it a PVC in a later WP)", m.get("slug"))
            continue
        rel = os.path.relpath(os.path.normpath(path), os.path.normpath(store_target))
        if rel == "." or path in seen:
            continue  # never re-expose the whole store; de-dup targets
        seen.add(path)
        volume_mounts.append({"name": vol_name, "mountPath": path, "subPath": rel,
                              "readOnly": not m.get("write", True)})
    return volumes, volume_mounts
