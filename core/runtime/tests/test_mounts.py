"""L2: the runtime's workspace MOUNT-SET plumbing (WP-A1.1) — the ONE env-driven mount computation the
docker/k8s/process backends share, proven offline (no docker, no kubectl).

STRICT isolation (default): one bind PER MOUNT — the worker's filesystem contains ONLY the dispatch's
declared workspaces (tenant isolation enforced by the mount table). A named-volume store rides
``volume_subpath`` (docker VolumeOptions.Subpath, engine ≥ v26); a host-path store joins the subpath;
k8s uses native ``subPath`` + ``readOnly``. The whole-store bind is never emitted."""
from __future__ import annotations

import json

import pytest

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


# ── one bind per mount, nothing else reachable ───────────────────────────────

def test_strict_emits_one_volume_subpath_bind_per_in_store_mount():
    """The tenant-isolation core: NO store-root bind; each in-store mount becomes its own bind of the
    store volume's subpath, so another tenant's workspace is simply not in the container."""
    mounts = [
        {"slug": "seed", "path": "/workspaces/u1", "role": "private", "write": True, "primary": True},
        {"slug": "x", "path": "/workspaces/.attached/u1/x", "role": "private", "write": True},
        {"slug": "deal-9", "path": "/workspaces/deal-9", "role": "shared", "write": False},
    ]
    binds = workspace_binds(_env(mounts))
    assert binds == [
        MountBind("agent-workspaces", "/workspaces/u1", read_only=False, volume_subpath="u1"),
        MountBind("agent-workspaces", "/workspaces/.attached/u1/x", read_only=False,
                  volume_subpath=".attached/u1/x"),
        MountBind("agent-workspaces", "/workspaces/deal-9", read_only=True, volume_subpath="deal-9"),
    ]
    # the whole-store bind is GONE
    assert not any(b.target == "/workspaces" for b in binds)


def test_strict_read_only_role_is_enforced_by_the_bind():
    """A viewer-role shared mount binds :ro — enforced by the substrate now, not just the commit token."""
    mounts = [{"slug": "deal-9", "path": "/workspaces/deal-9", "role": "shared", "write": False}]
    [b] = workspace_binds(_env(mounts))
    assert b.read_only is True


def test_strict_host_path_store_joins_the_subpath():
    """A host-path store (source starts with /) needs no volume subpath — plain source-join bind, which
    also means NO docker-engine version requirement on such deployments."""
    mounts = [{"slug": "seed", "path": "/workspaces/u1", "role": "private", "write": True, "primary": True}]
    [b] = workspace_binds(_env(mounts, source="/srv/vexa/workspaces"))
    assert b == MountBind("/srv/vexa/workspaces/u1", "/workspaces/u1", read_only=False)
    assert b.volume_subpath is None


def test_strict_global_and_out_of_store_mounts_bind_their_own_source():
    """_global (own host source) and out-of-store mounts bind source→target directly — same as legacy."""
    mounts = [
        {"slug": "_global", "source": "/srv/vexa-global", "path": "/workspaces/_global",
         "role": "global", "write": False},
        {"slug": "shared-z", "path": "/shared-store/team-z", "role": "shared", "write": False},
        {"slug": "seed", "path": "/workspaces/u1", "role": "private", "write": True, "primary": True},
    ]
    binds = workspace_binds(_env(mounts))
    assert MountBind("/srv/vexa-global", "/workspaces/_global", read_only=True) in binds
    assert MountBind("/shared-store/team-z", "/shared-store/team-z", read_only=True) in binds
    assert MountBind("agent-workspaces", "/workspaces/u1", read_only=False, volume_subpath="u1") in binds


def test_strict_never_re_exposes_the_store_root():
    """A (mis)declared mount AT the store root must not silently re-open the whole store."""
    mounts = [{"slug": "evil", "path": "/workspaces", "role": "private", "write": True}]
    assert workspace_binds(_env(mounts)) == []


def test_strict_legacy_dispatch_still_binds_its_baseline():
    """A dispatch predating VEXA_MOUNTS (only VEXA_WORKSPACE_PATH) gets its one private-baseline bind."""
    binds = workspace_binds(_env())
    assert binds == [MountBind("agent-workspaces", "/workspaces/u1", read_only=False, volume_subpath="u1")]


def test_no_store_configured_yields_direct_binds_only():
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
    # falls back to the baseline mount (strict: its own subpath bind) — never raises
    assert workspace_binds(e) == [
        MountBind("agent-workspaces", "/workspaces/u1", read_only=False, volume_subpath="u1")
    ]
    assert mount_set(e)[0]["slug"] == "u1"


# ── docker: bind strings + Mounts API entries ─────────────────────────────────

def test_docker_bind_strings_cover_the_non_subpath_binds():
    mounts = [
        {"slug": "seed", "path": "/workspaces/u1", "primary": True, "write": True},
        {"slug": "shared-z", "path": "/shared-store/team-z", "write": False},
    ]
    binds = workspace_binds(_env(mounts))
    strings = [f"{b.source}:{b.target}:ro" if b.read_only else f"{b.source}:{b.target}"
               for b in binds if not b.volume_subpath]
    assert strings == ["/shared-store/team-z:/shared-store/team-z:ro"]
    subpathed = [b for b in binds if b.volume_subpath]
    assert [(b.source, b.target, b.volume_subpath) for b in subpathed] == [
        ("agent-workspaces", "/workspaces/u1", "u1")
    ]
    # sanity: the backend can be constructed (no daemon needed for this pure check)
    assert DockerBackend().name == "docker"


# ── k8s: per-mount subPath volumeMounts (native isolation) ────────────────────

def test_k8s_strict_emits_per_mount_subpath_readonly():
    mounts = [
        {"slug": "seed", "path": "/workspaces/u1", "role": "private", "write": True, "primary": True},
        {"slug": "deal-9", "path": "/workspaces/deal-9", "role": "shared", "write": False},
    ]
    volumes, vmounts = k8s_volume_mounts(_env(mounts), pvc_name="vexa-agent-workspaces",
                                         store_target="/workspaces")
    assert volumes == [{"name": "workspace-store", "persistentVolumeClaim": {"claimName": "vexa-agent-workspaces"}}]
    assert vmounts == [
        {"name": "workspace-store", "mountPath": "/workspaces/u1", "subPath": "u1", "readOnly": False},
        {"name": "workspace-store", "mountPath": "/workspaces/deal-9", "subPath": "deal-9", "readOnly": True},
    ]
    assert not any(vm["mountPath"] == "/workspaces" for vm in vmounts)   # whole store never mounted


def test_k8s_pod_overrides_carry_the_per_mount_spec():
    """The Pod spec the worker gets: per-mount subPath volumeMounts against the ONE store PVC."""
    ov = pod_overrides(_env(source="vexa-agent-workspaces"), container_name="vexa-worker-u1")
    spec = ov["spec"]
    assert spec["volumes"][0]["persistentVolumeClaim"]["claimName"] == "vexa-agent-workspaces"
    c = spec["containers"][0]
    assert c["name"] == "vexa-worker-u1"
    assert c["volumeMounts"] == [
        {"name": "workspace-store", "mountPath": "/workspaces/u1", "subPath": "u1", "readOnly": False}
    ]


def test_k8s_pod_overrides_none_when_no_store_configured():
    assert pod_overrides({}, container_name="x") is None


# ── k8s: scheduling constraints merged into the spawn override (#673) ──────────
# A spawned Pod is a bare `kubectl run` Pod — NOT a Deployment child — so it inherits none of the
# runtime Deployment's nodeSelector/tolerations. Without these it strands Pending forever on an
# all-tainted pool and the meeting silently fails. RUNTIME_K8S_TOLERATIONS / RUNTIME_K8S_NODE_SELECTOR
# (JSON, from the chart's global.*) let the override carry the runtime's own constraints.

_TOL = [{"key": "vexa.ai/pool", "operator": "Equal", "value": "main", "effect": "NoSchedule"}]
_SEL = {"vexa.ai/pool": "main"}


def _sched_env(*, tolerations=None, node_selector=None, **kw):
    e = _env(**kw) if kw else {}
    if tolerations is not None:
        e["RUNTIME_K8S_TOLERATIONS"] = json.dumps(tolerations)
    if node_selector is not None:
        e["RUNTIME_K8S_NODE_SELECTOR"] = json.dumps(node_selector)
    return e


def test_k8s_pod_overrides_tolerations_only_no_pvc_builds_a_valid_spec():
    """The load-bearing case: a plain meeting bot has NO workspace PVC, yet its spawn override MUST
    still carry tolerations — a volumes-only early return would silently drop them and re-create #673.
    Negative control: the pre-#673 pod_overrides returned None here (no store ⇒ no override at all)."""
    ov = pod_overrides(_sched_env(tolerations=_TOL, node_selector=_SEL), container_name="vexa-mtg-6")
    assert ov is not None                                  # NOT None — the pre-#673 bug returned None
    spec = ov["spec"]
    assert spec["tolerations"] == _TOL
    assert spec["nodeSelector"] == _SEL
    assert "volumes" not in spec                           # no PVC ⇒ no volumes, but scheduling stands
    # LOAD-BEARING (witnessed live, v0.12.8 staging): `kubectl run --overrides` merges the containers
    # LIST by replacement, so ANY containers entry here wipes the generated container's image/env/command
    # and the API server rejects the Pod (`spec.containers[0].image: Required value`) — the spawn dies in
    # <1s. Scheduling-only overrides must NOT touch the containers list.
    assert "containers" not in spec


def test_k8s_pod_overrides_merges_pvc_and_scheduling():
    """An agent worker with a workspace PVC: the override carries BOTH the per-mount volumeMounts AND
    the scheduling constraints — the two seams coexist."""
    ov = pod_overrides(
        _sched_env(tolerations=_TOL, node_selector=_SEL, source="vexa-agent-workspaces"),
        container_name="vexa-worker-u1",
    )
    spec = ov["spec"]
    assert spec["volumes"][0]["persistentVolumeClaim"]["claimName"] == "vexa-agent-workspaces"
    assert spec["containers"][0]["volumeMounts"] == [
        {"name": "workspace-store", "mountPath": "/workspaces/u1", "subPath": "u1", "readOnly": False}
    ]
    assert spec["tolerations"] == _TOL
    assert spec["nodeSelector"] == _SEL


def test_k8s_pod_overrides_empty_scheduling_is_unchanged_from_today():
    """The chart's DEFAULT deployment serializes global.tolerations=[] / global.nodeSelector={} to the
    strings "[]" / "{}". These are treated as unset — no scheduling appears, and a no-store spawn still
    returns None — so an untainted cluster sees exactly today's behaviour (no regression)."""
    assert pod_overrides({"RUNTIME_K8S_TOLERATIONS": "[]", "RUNTIME_K8S_NODE_SELECTOR": "{}"},
                         container_name="x") is None
    ov = pod_overrides(_sched_env(tolerations=[], node_selector={}, source="vexa-agent-workspaces"),
                       container_name="vexa-worker-u1")
    assert "tolerations" not in ov["spec"] and "nodeSelector" not in ov["spec"]
    assert ov["spec"]["volumes"]                           # volumes-only spec, exactly as before #673


def test_k8s_pod_overrides_malformed_scheduling_json_fails_loud():
    """Malformed scheduling JSON is FATAL (raise), not fail-open like the workspace mount set: a
    silently dropped toleration is precisely the stranded-Pod / silent-meeting-failure bug."""
    with pytest.raises(ValueError, match="RUNTIME_K8S_TOLERATIONS"):
        pod_overrides({"RUNTIME_K8S_TOLERATIONS": "{not json"}, container_name="x")
    # wrong shape (object where an array is required) is caught too
    with pytest.raises(ValueError, match="RUNTIME_K8S_NODE_SELECTOR"):
        pod_overrides({"RUNTIME_K8S_NODE_SELECTOR": "[1,2]"}, container_name="x")


def test_k8s_start_overlays_runtime_process_scheduling_env(monkeypatch):
    """start() overlays the runtime's OWN process env (RUNTIME_K8S_* set by the chart on the runtime
    Deployment) onto the spawn override — spec.env, built per-workload by meeting-api/agent-api, cannot
    carry these. Proven via _runtime_scheduling_env without a cluster."""
    from runtime_kernel.k8s_backend import _runtime_scheduling_env
    monkeypatch.setenv("RUNTIME_K8S_TOLERATIONS", json.dumps(_TOL))
    monkeypatch.setenv("RUNTIME_K8S_NODE_SELECTOR", json.dumps(_SEL))
    overlay = _runtime_scheduling_env()
    # the workload's own spec.env has no scheduling; the overlay supplies it → the override carries it
    ov = pod_overrides({**_env(source="vexa-agent-workspaces"), **overlay}, container_name="w")
    assert ov["spec"]["tolerations"] == _TOL
    assert ov["spec"]["nodeSelector"] == _SEL


# ── spawned-workload security context: configurable, default ABSENT ──────────
#
# A spawned Pod carries NO securityContext today, which is the likeliest reason a restricted
# admission policy (OpenShift restricted-v2 / Pod Security "restricted") rejects it. The seam below
# lets an operator declare one. It DEFAULTS TO ABSENT on purpose: this repo's images carry no
# Dockerfile `USER`, so they run as root, and a defaulted runAsNonRoot would break every existing
# install. Whether the images tolerate an arbitrary non-root UID is untested and tracked separately.

_POD_SC = {"runAsNonRoot": True, "runAsUser": 1000740000,
           "seccompProfile": {"type": "RuntimeDefault"}}
_CTR_SC = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}


def _sc_env(*, pod=None, container=None, **kw):
    e = _env(**kw) if kw else {}
    if pod is not None:
        e["RUNTIME_K8S_POD_SECURITY_CONTEXT"] = json.dumps(pod)
    if container is not None:
        e["RUNTIME_K8S_CONTAINER_SECURITY_CONTEXT"] = json.dumps(container)
    return e


def test_k8s_pod_overrides_default_emits_no_security_context():
    """THE DEFAULT-ABSENT GUARANTEE. Unset, and the chart's empty default (`{}` → the template emits
    no env at all; `"{}"` if one ever reaches us) both produce a spec with no securityContext at any
    level — byte-identical to the pre-seam spawn. This is the assertion that keeps the fix from
    shipping a break to every operator whose root-running images start fine today."""
    assert pod_overrides({}, container_name="x") is None
    assert pod_overrides({"RUNTIME_K8S_POD_SECURITY_CONTEXT": "{}",
                          "RUNTIME_K8S_CONTAINER_SECURITY_CONTEXT": "{}"},
                         container_name="x") is None
    # and with another seam present, the spec still carries no security context anywhere
    ov = pod_overrides(_sc_env(pod={}, container={}, source="vexa-agent-workspaces"),
                       container_name="vexa-worker-u1")
    assert "securityContext" not in ov["spec"]
    assert "securityContext" not in ov["spec"]["containers"][0]


def test_k8s_pod_overrides_pod_level_security_context_needs_no_containers_entry():
    """A POD-level security context is a pod-scoped field, so it merges without touching the
    containers LIST — the same property that makes tolerations/nodeSelector safe under
    `kubectl run --overrides`, whose JSON merge replaces that list wholesale."""
    ov = pod_overrides(_sc_env(pod=_POD_SC), container_name="vexa-mtg-9")
    assert ov["spec"]["securityContext"] == _POD_SC
    assert "containers" not in ov["spec"]                  # list untouched ⇒ image/env/command survive


def test_k8s_pod_overrides_container_level_security_context_rides_the_named_container():
    """A CONTAINER-level security context is container-scoped, so it must ride a containers entry —
    keyed BY NAME, exactly like the workspace-mount seam it shares the entry with."""
    ov = pod_overrides(_sc_env(container=_CTR_SC), container_name="vexa-mtg-9")
    (container,) = ov["spec"]["containers"]
    assert container["name"] == "vexa-mtg-9"
    assert container["securityContext"] == _CTR_SC
    assert "volumeMounts" not in container                 # no PVC ⇒ no mounts, security context stands


def test_k8s_pod_overrides_security_context_coexists_with_every_other_seam():
    """All four seams in one spawn — workspace mounts, scheduling, pod- and container-level security
    context — with the two container-scoped fields sharing ONE by-name entry rather than two."""
    ov = pod_overrides(
        {**_sched_env(tolerations=_TOL, node_selector=_SEL, source="vexa-agent-workspaces"),
         **_sc_env(pod=_POD_SC, container=_CTR_SC)},
        container_name="vexa-worker-u1",
    )
    spec = ov["spec"]
    assert spec["securityContext"] == _POD_SC
    assert spec["tolerations"] == _TOL and spec["nodeSelector"] == _SEL
    assert spec["volumes"][0]["persistentVolumeClaim"]["claimName"] == "vexa-agent-workspaces"
    (container,) = spec["containers"]                      # ONE entry, not one per container-scoped seam
    assert container["securityContext"] == _CTR_SC
    assert container["volumeMounts"][0]["mountPath"] == "/workspaces/u1"


def test_k8s_pod_overrides_malformed_security_context_json_fails_loud():
    """Same contract as the scheduling knobs: a security context silently dropped is a Pod rejected at
    admission with a cause the operator cannot see, so malformed input raises at spawn."""
    with pytest.raises(ValueError, match="RUNTIME_K8S_POD_SECURITY_CONTEXT"):
        pod_overrides({"RUNTIME_K8S_POD_SECURITY_CONTEXT": "{not json"}, container_name="x")
    with pytest.raises(ValueError, match="RUNTIME_K8S_CONTAINER_SECURITY_CONTEXT"):
        pod_overrides({"RUNTIME_K8S_CONTAINER_SECURITY_CONTEXT": "[1,2]"}, container_name="x")


def test_k8s_start_overlays_runtime_process_security_context_env(monkeypatch):
    """The security context is a property of the RUNTIME deployment (the operator installing the
    chart), not of the workload spec built per-workload by meeting-api/agent-api — so it rides the
    same process-env overlay the scheduling constraints do."""
    from runtime_kernel.k8s_backend import _runtime_scheduling_env
    monkeypatch.setenv("RUNTIME_K8S_POD_SECURITY_CONTEXT", json.dumps(_POD_SC))
    monkeypatch.setenv("RUNTIME_K8S_CONTAINER_SECURITY_CONTEXT", json.dumps(_CTR_SC))
    overlay = _runtime_scheduling_env()
    ov = pod_overrides({**_env(source="vexa-agent-workspaces"), **overlay}, container_name="w")
    assert ov["spec"]["securityContext"] == _POD_SC
    assert ov["spec"]["containers"][0]["securityContext"] == _CTR_SC


# ── process: shares the host FS — no binds, but N-mount aware (parity) ────────

def test_process_backend_reads_the_mount_set_without_binding(tmp_path):
    """The lite/process backend shares the host FS: it materializes NO binds but still resolves the
    mount set (POSIX isolation is its wall — see test_isolation.py). Non-root test run → the isolation
    plan is unavailable and the spawn degrades loudly to shared-trust, which is exactly this path."""
    from runtime_kernel.process_backend import ProcessBackend
    mounts = [
        {"slug": "seed", "path": str(tmp_path / "u1"), "primary": True, "write": True},
        {"slug": "shared-x", "path": str(tmp_path / "shared"), "write": True},
    ]
    assert [m["slug"] for m in mount_set(_env(mounts))] == ["seed", "shared-x"]
    b = ProcessBackend()
    h = b.start("rt-mnt", Runnable(command=["true"]), _env(mounts))
    try:
        assert h.id == "rt-mnt"
    finally:
        b.cleanup(h)
