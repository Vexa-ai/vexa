"""L2: the runtime's workspace MOUNT-SET plumbing (WP-A1.1) — the ONE env-driven mount computation the
docker/k8s/process backends share, proven offline (no docker, no kubectl). Asserts the plumbing
generalizes to N mounts on all three backends and never special-cases "two"."""
from __future__ import annotations

import json

from runtime_kernel.mounts import MountBind, k8s_volume_mounts, mount_set, workspace_binds
from runtime_kernel.docker_backend import DockerBackend  # for the docker bind-string shape
from runtime_kernel.k8s_backend import pod_overrides
from runtime_kernel.profiles import Runnable


def _env(mounts=None, *, source="agent-workspaces", target="/workspaces", path="/workspaces/u1"):
    e = {"VEXA_WORKSPACE_MOUNT_SOURCE": source, "VEXA_WORKSPACE_MOUNT_TARGET": target,
         "VEXA_WORKSPACE_PATH": path}
    if mounts is not None:
        e["VEXA_MOUNTS"] = json.dumps(mounts)
    return e


# ── the shared bind computation ──────────────────────────────────────────────

def test_store_bind_alone_exposes_every_in_store_mount():
    """All active mounts live under the store root → the single store bind exposes them all; N mounts
    add NO extra binds (the generalization is: bind the store once, not once-per-workspace)."""
    mounts = [
        {"slug": "seed", "path": "/workspaces/u1", "role": "private", "write": True, "primary": True},
        {"slug": "shared-x", "path": "/workspaces/.attached/u1/shared-x", "role": "private", "write": True},
        {"slug": "shared-y", "path": "/workspaces/.attached/u1/shared-y", "role": "private", "write": True},
    ]
    binds = workspace_binds(_env(mounts))
    assert binds == [MountBind("agent-workspaces", "/workspaces", read_only=False)]


def test_out_of_store_mount_gets_its_own_bind():
    """A mount whose path is OUTSIDE the store root (a future cross-store shared workspace) gets its OWN
    bind — the plumbing already supports N distinct source→target binds, not just the store root."""
    mounts = [
        {"slug": "seed", "path": "/workspaces/u1", "role": "private", "write": True, "primary": True},
        {"slug": "shared-z", "path": "/shared-store/team-z", "role": "shared", "write": False},
    ]
    binds = workspace_binds(_env(mounts))
    assert binds[0] == MountBind("agent-workspaces", "/workspaces", read_only=False)
    assert binds[1] == MountBind("/shared-store/team-z", "/shared-store/team-z", read_only=True)


def test_global_system_mount_binds_its_own_source_read_only():
    """The _global GLOBAL SYSTEM tier (AMENDMENT 4) lives OUTSIDE the store root and carries a distinct
    host ``source`` (the platform-owned repo) → it gets its own READ-ONLY bind ``source→path``. Every
    worker gets _global mounted read-only; agents never write it."""
    mounts = [
        {"slug": "_global", "source": "/srv/vexa-global", "path": "/workspaces/_global",
         "role": "global", "write": False, "primary": False},
        {"slug": "seed", "path": "/workspaces/u1", "role": "private", "write": True, "primary": True},
        {"slug": "_system", "path": "/workspaces/.system/u1", "role": "system", "write": True},
    ]
    binds = workspace_binds(_env(mounts))
    # the store-root bind exposes the in-store active baseline AND _system (both under /workspaces)
    assert MountBind("agent-workspaces", "/workspaces", read_only=False) in binds
    # _global is out-of-store → its own bind, host source → container path, READ-ONLY
    assert MountBind("/srv/vexa-global", "/workspaces/_global", read_only=True) in binds
    # docker bind-string shape: the :ro suffix rides through
    strings = [f"{b.source}:{b.target}:ro" if b.read_only else f"{b.source}:{b.target}" for b in binds]
    assert "/srv/vexa-global:/workspaces/_global:ro" in strings


def test_no_store_configured_yields_no_binds():
    assert workspace_binds({"VEXA_MOUNTS": json.dumps([{"slug": "a", "path": "/x", "write": True}])}) == [
        MountBind("/x", "/x", read_only=False)
    ]
    assert workspace_binds({}) == []


def test_mount_set_falls_back_to_the_private_baseline():
    """A dispatch predating VEXA_MOUNTS → the single private baseline from VEXA_WORKSPACE_PATH."""
    assert mount_set(_env()) == [
        {"slug": "u1", "path": "/workspaces/u1", "role": "private", "write": True, "primary": True}
    ]
    got = mount_set(_env([{"slug": "seed", "path": "/workspaces/u1", "primary": True, "write": True},
                          {"slug": "x", "path": "/workspaces/.attached/u1/x", "write": True}]))
    assert [m["slug"] for m in got] == ["seed", "x"]


def test_malformed_vexa_mounts_is_ignored_not_fatal():
    e = _env()
    e["VEXA_MOUNTS"] = "{not json"
    # falls back to the single store bind + baseline mount set — never raises
    assert workspace_binds(e) == [MountBind("agent-workspaces", "/workspaces", read_only=False)]
    assert mount_set(e)[0]["slug"] == "u1"


# ── docker: the bind STRINGS the daemon receives (source:target[:ro]) ─────────

def test_docker_bind_strings_cover_the_whole_set():
    mounts = [
        {"slug": "seed", "path": "/workspaces/u1", "primary": True, "write": True},
        {"slug": "shared-z", "path": "/shared-store/team-z", "write": False},
    ]
    binds = [f"{b.source}:{b.target}:ro" if b.read_only else f"{b.source}:{b.target}"
             for b in workspace_binds(_env(mounts))]
    assert "agent-workspaces:/workspaces" in binds
    assert "/shared-store/team-z:/shared-store/team-z:ro" in binds
    # sanity: the backend can be constructed (no daemon needed for this pure check)
    assert DockerBackend().name == "docker"


# ── k8s: the volume overrides the Pod receives ───────────────────────────────

def test_k8s_volume_mounts_bind_the_store_pvc_at_the_root():
    volumes, vmounts = k8s_volume_mounts(_env(), pvc_name="vexa-agent-workspaces", store_target="/workspaces")
    assert volumes == [{"name": "workspace-store", "persistentVolumeClaim": {"claimName": "vexa-agent-workspaces"}}]
    assert vmounts == [{"name": "workspace-store", "mountPath": "/workspaces"}]


def test_k8s_pod_overrides_mounts_the_store_into_the_worker():
    """The whole active set is exposed by mounting the store PVC at the root (every workspace lives
    under it) — the k8s twin of the docker store bind, generalizing to N mounts with no per-workspace
    volume. Container name = the Pod name kubectl uses for `run`."""
    ov = pod_overrides(_env(source="vexa-agent-workspaces"), container_name="vexa-worker-u1")
    spec = ov["spec"]
    assert spec["volumes"][0]["persistentVolumeClaim"]["claimName"] == "vexa-agent-workspaces"
    c = spec["containers"][0]
    assert c["name"] == "vexa-worker-u1"
    assert c["volumeMounts"][0]["mountPath"] == "/workspaces"


def test_k8s_pod_overrides_none_when_no_store_configured():
    assert pod_overrides({}, container_name="x") is None


# ── process: shares the host FS — no binds, but N-mount aware (parity) ────────

def test_process_backend_reads_the_mount_set_without_binding(tmp_path):
    """The lite/process backend shares the host FS: it materializes NO binds but still resolves the
    mount set (parity with docker/k8s) so the plumbing is N-mount-aware on all three, not special-cased."""
    from runtime_kernel.process_backend import ProcessBackend
    mounts = [
        {"slug": "seed", "path": str(tmp_path / "u1"), "primary": True, "write": True},
        {"slug": "shared-x", "path": str(tmp_path / "shared"), "write": True},
    ]
    # the set resolves the same way the backend logs it
    assert [m["slug"] for m in mount_set(_env(mounts))] == ["seed", "shared-x"]
    # the backend starts a plain child (a trivial command) — no bind machinery, no daemon
    b = ProcessBackend()
    h = b.start("rt-mnt", Runnable(command=["true"]), _env(mounts))
    try:
        assert h.id == "rt-mnt"
    finally:
        b.cleanup(h)
