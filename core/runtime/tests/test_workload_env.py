"""A spec names WHICH workspaces a workload sees; the runtime decides where they come from.

The store backing (VEXA_WORKSPACE_MOUNT_*) and Pod scheduling (RUNTIME_K8S_*) are the runtime's own
configuration: a caller's values are dropped, every mount must sit under the runtime's store root,
and a mount with its own host source must name a source the runtime was configured to allow.
"""
import json
import os
import sys

import pytest

from runtime_kernel import Runtime
from runtime_kernel.api import create_app
from runtime_kernel.k8s_backend import build_pod
from runtime_kernel.mounts import workspace_binds
from runtime_kernel.process_backend import ProcessBackend
from dataclasses import replace

from runtime_kernel.profiles import Runnable
from runtime_kernel.workload_env import (
    MountRefused,
    StoreConfig,
    child_environment,
    forwarded_env,
    workload_env,
)

import _profiles
from _caller import TOKEN, caller_client

STORE = StoreConfig(source="vexa_agent-workspaces", target="/workspaces",
                    extra_sources=("/srv/vexa-global",))


def _mounts(*entries: dict) -> str:
    return json.dumps(list(entries))


def _mount(path: str, **kw) -> dict:
    return {"slug": path.rsplit("/", 1)[-1], "path": path, "role": "private", "write": True, **kw}


# ── runtime-owned keys ──────────────────────────────────────────────────────────────────────────

def test_runtime_owned_keys_in_a_spec_are_dropped():
    env = workload_env({
        "RUNTIME_K8S_SECRET_MOUNTS": '[{"secret": "vexa-secrets", "mountPath": "/s"}]',
        "RUNTIME_K8S_TOLERATIONS": "[]",
        "RUNTIME_K8S_NODE_SELECTOR": "{}",
        "VEXA_WORKSPACE_MOUNT_SOURCE": "/",
        "VEXA_WORKSPACE_MOUNT_TARGET": "/",
        "VEXA_BOT_CONFIG": "{}",
    }, STORE)
    assert env == {"VEXA_BOT_CONFIG": "{}"}


def test_the_store_backing_is_the_runtimes_own():
    env = workload_env({
        "VEXA_WORKSPACE_MOUNT_SOURCE": "/var/run",
        "VEXA_WORKSPACE_MOUNT_TARGET": "/",
        "VEXA_MOUNTS": _mounts(_mount("/workspaces/57")),
    }, STORE)
    assert env["VEXA_WORKSPACE_MOUNT_SOURCE"] == "vexa_agent-workspaces"
    assert env["VEXA_WORKSPACE_MOUNT_TARGET"] == "/workspaces"
    binds = workspace_binds(env)
    assert [(b.source, b.target, b.volume_subpath) for b in binds] == [
        ("vexa_agent-workspaces", "/workspaces/57", "57")]


def test_a_spec_without_a_mount_set_gets_no_store():
    env = workload_env({"VEXA_BOT_CONFIG": "{}"}, STORE)
    assert "VEXA_WORKSPACE_MOUNT_SOURCE" not in env and "VEXA_WORKSPACE_MOUNT_TARGET" not in env


def test_a_spec_cannot_add_runtime_scheduling_or_secret_mounts_to_a_pod():
    env = workload_env({"RUNTIME_K8S_SECRET_MOUNTS": '[{"secret": "vexa-secrets", "mountPath": "/s"}]'},
                       STORE)
    pod = build_pod(name="vexa-agent-x", workload_id="agent-x", namespace=None, resources=None,
                    runnable=_profiles.agent("img"), env=env)
    assert "volumes" not in pod["spec"]


# ── the mount set ───────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("entry", [
    _mount("/var/run/docker.sock"),
    _mount("/"),
    _mount("/workspaces"),                               # the whole store
    _mount("/workspaces/../etc"),
    _mount("workspaces/57"),                             # relative
    _mount("/workspacesX/57"),                           # a sibling that shares the prefix
    _mount("/workspaces/_global", source="/"),           # a source nobody configured
    _mount("/workspaces/_global", source="/srv/vexa-global/../.."),
    _mount("/etc", source="/etc"),
])
def test_a_mount_outside_what_the_runtime_serves_is_refused(entry):
    with pytest.raises(MountRefused):
        workload_env({"VEXA_MOUNTS": _mounts(_mount("/workspaces/57"), entry)}, STORE)


@pytest.mark.parametrize("raw", ["not json", "{}", '"x"', "[1]", '[{"slug": "a"}]', '[{"path": 3}]'])
def test_a_malformed_mount_set_is_refused(raw):
    with pytest.raises(MountRefused):
        workload_env({"VEXA_MOUNTS": raw}, STORE)


def test_a_cwd_outside_the_store_is_refused():
    with pytest.raises(MountRefused):
        workload_env({"VEXA_WORKSPACE_PATH": "/root"}, STORE)


def test_the_mount_set_agent_api_builds_is_served():
    mounts = [
        _mount("/workspaces/_global", role="global", write=False, source="/srv/vexa-global"),
        _mount("/workspaces/57", primary=True),
        _mount("/workspaces/.attached/57/repo"),
        _mount("/workspaces/.system/57", role="system"),
    ]
    env = workload_env({"VEXA_MOUNTS": json.dumps(mounts), "VEXA_WORKSPACE_PATH": "/workspaces/57"},
                       STORE)
    targets = [b.target for b in workspace_binds(env)]
    assert targets == ["/workspaces/_global", "/workspaces/57", "/workspaces/.attached/57/repo",
                       "/workspaces/.system/57"]


@pytest.mark.parametrize("target", [
    "/root/.ssh",                                        # anywhere outside the store
    "/var/run/docker.sock",
    "/workspaces",                                       # the store root itself
    "/workspacesX/_global",                              # a sibling that shares the prefix
    "/workspaces/../app",
])
def test_an_allowed_source_still_binds_only_inside_the_store(target):
    """runtime.v1: EVERY mount path sits strictly under the store — a mount naming a source the
    runtime serves is no exception, because its path is where that source is bound."""
    with pytest.raises(MountRefused):
        workload_env({"VEXA_MOUNTS": _mounts(_mount(target, role="global", write=False,
                                                     source="/srv/vexa-global"))}, STORE)


def test_an_unconfigured_global_source_is_refused():
    plain = StoreConfig(source="vexa_agent-workspaces", target="/workspaces")
    with pytest.raises(MountRefused):
        workload_env({"VEXA_MOUNTS": _mounts(_mount("/workspaces/_global", source="/srv/vexa-global"))},
                     plain)


def test_store_config_reads_the_runtime_env():
    cfg = StoreConfig.from_env({"VEXA_WORKSPACE_MOUNT_SOURCE": "pvc-x",
                                "VEXA_WORKSPACE_MOUNT_TARGET": "/workspaces/",
                                "RUNTIME_EXTRA_MOUNT_SOURCES": "/srv/g, /srv/h/ ,"})
    assert cfg == StoreConfig(source="pvc-x", target="/workspaces", extra_sources=("/srv/g", "/srv/h"))
    assert StoreConfig.from_env({}) == StoreConfig(source="", target="/workspaces", extra_sources=())


# ── over the API ────────────────────────────────────────────────────────────────────────────────

class _RecordingBackend:
    name = "process"

    def __init__(self):
        self.started = []

    def start(self, workload_id, runnable, env, resources=None):
        from runtime_kernel.backend import WorkloadHandle

        self.started.append(env)
        return WorkloadHandle(workload_id, object())

    def exit_code(self, h):
        return None

    def terminate(self, h): ...
    def kill(self, h): ...
    def cleanup(self, h): ...


def test_a_refused_mount_is_a_400_that_reaches_nothing():
    backend = _RecordingBackend()
    rt = Runtime(backend=backend, profiles={"agent": replace(_profiles.agent(), command=["true"])},
                 workspace_store=STORE)
    client = caller_client(create_app(rt, caller_token=TOKEN))
    r = client.post("/workloads", json={"workloadId": "agent-1", "profile": "agent", "env": {
        "VEXA_MOUNTS": _mounts(_mount("/var/run/docker.sock"))}})
    assert r.status_code == 400
    assert backend.started == [] and rt.store.get("agent-1") is None


def test_the_backend_receives_the_runtime_owned_env():
    backend = _RecordingBackend()
    rt = Runtime(backend=backend, profiles={"agent": replace(_profiles.agent(), command=["true"])},
                 workspace_store=STORE)
    client = caller_client(create_app(rt, caller_token=TOKEN))
    r = client.post("/workloads", json={"workloadId": "agent-1", "profile": "agent", "env": {
        "VEXA_MOUNTS": _mounts(_mount("/workspaces/57")),
        "VEXA_WORKSPACE_MOUNT_SOURCE": "/", "RUNTIME_K8S_SECRET_MOUNTS": "[]"}})
    assert r.status_code == 201
    (env,) = backend.started
    assert env["VEXA_WORKSPACE_MOUNT_SOURCE"] == "vexa_agent-workspaces"
    assert "RUNTIME_K8S_SECRET_MOUNTS" not in env


# ── the process backend's child environment ─────────────────────────────────────────────────────

_RUNTIME_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/root",
    "DISPLAY": ":99",
    "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright",
    "RUNTIME_API_TOKEN": "x" * 40,
    "INTERNAL_API_SECRET": "x" * 40,
    "ADMIN_API_TOKEN": "x" * 40,
    "DB_PASSWORD": "x" * 40,
    "VEXA_MCP_DELEGATION_SECRET": "x" * 40,
    "NEXTAUTH_SECRET": "x" * 40,
    "MINIO_SECRET_KEY": "x" * 40,
    "VEXA_MAIL_SMTP_PASSWORD": "x" * 40,
    "ANTHROPIC_API_KEY": "model-credential",
    "VEXA_AGENT_MODEL": "sonnet",
}


def _leaks(env: dict) -> list[str]:
    return sorted(k for k in env if ("SECRET" in k or "PASSWORD" in k or k.endswith("_TOKEN")
                                     or k.startswith("ADMIN_")) and k in _RUNTIME_ENV)


_WORKER_FORWARD = _profiles.agent().forward_env
_BOT_FORWARD = _profiles.bot().forward_env


def test_no_service_secret_reaches_a_child_process():
    for forward in (_WORKER_FORWARD, _BOT_FORWARD):
        env = child_environment({"VEXA_UNIT_ID": "u1"}, forward=forward, parent=_RUNTIME_ENV)
        assert _leaks(env) == []
        assert env["PATH"] == "/usr/bin:/bin"
        assert "DISPLAY" not in env          # no child gets the runtime's display; a bot names its own
        assert env["VEXA_UNIT_ID"] == "u1"


def test_model_credentials_reach_workers_only():
    worker = child_environment({}, forward=_WORKER_FORWARD, parent=_RUNTIME_ENV)
    bot = child_environment({}, forward=_BOT_FORWARD, parent=_RUNTIME_ENV)
    assert worker["ANTHROPIC_API_KEY"] == "model-credential" and worker["VEXA_AGENT_MODEL"] == "sonnet"
    assert "ANTHROPIC_API_KEY" not in bot and "VEXA_AGENT_MODEL" not in bot


def test_a_dispatch_stamped_value_wins_over_the_forwarded_one():
    env = child_environment({"VEXA_AGENT_MODEL": "opus"}, forward=_WORKER_FORWARD, parent=_RUNTIME_ENV)
    assert env["VEXA_AGENT_MODEL"] == "opus"


def test_an_explicitly_empty_dispatch_value_is_never_refilled():
    """A subject's own model endpoint pins the whole route, and "no key" or "no extra body" arrives
    as an empty string. Refilling it from the deployment's environment would hand the deployment's
    credential to the subject's endpoint, or the deployment's endpoint the subject's key. Both
    merges (the process backend's, and docker's spawn_env update) go through ``forwarded_env``."""
    parent = {**_RUNTIME_ENV, "VEXA_LLM_BASE_URL": "http://deployment:8001/v1",
              "VEXA_LLM_API_KEY": "deployment-key", "VEXA_LLM_MODEL": "deployment-model",
              "VEXA_LLM_EXTRA_BODY": '{"x": 1}', "ANTHROPIC_AUTH_TOKEN": "deployment-token"}
    spec = {"VEXA_LLM_BASE_URL": "https://subject.example/v1", "VEXA_LLM_API_KEY": "",
            "VEXA_LLM_MODEL": "", "VEXA_LLM_EXTRA_BODY": "", "ANTHROPIC_AUTH_TOKEN": "",
            "ANTHROPIC_API_KEY": ""}
    assert forwarded_env(_WORKER_FORWARD, parent, spec).keys().isdisjoint(spec)
    env = child_environment(spec, forward=_WORKER_FORWARD, parent=parent)
    assert {k: env[k] for k in spec} == spec


def test_a_real_child_sees_none_of_the_runtimes_secrets(monkeypatch, tmp_path):
    for k, v in _RUNTIME_ENV.items():
        if k not in ("PATH", "HOME"):
            monkeypatch.setenv(k, v)
    monkeypatch.setenv("PROCESS_LOG_DIR", str(tmp_path / "logs"))
    drop = tmp_path / "drop"                       # under a root runtime the child is another uid
    drop.mkdir()
    os.chmod(drop, 0o1777)
    for d in (tmp_path, tmp_path.parent, tmp_path.parent.parent):
        os.chmod(d, 0o755)
    out = drop / "env.json"
    code = f"import json, os; json.dump(dict(os.environ), open({str(out)!r}, 'w'))"
    for name, profile in (("worker", _profiles.agent()), ("bot", _profiles.bot())):
        b = ProcessBackend()
        h = b.start(f"w-{name}", replace(profile, command=[sys.executable, "-c", code]), {"MARKER": "1"})
        h._impl.wait(timeout=20)
        seen = json.loads(out.read_text())
        assert seen["MARKER"] == "1"
        assert _leaks(seen) == []


@pytest.mark.parametrize("raw", ["agent-1-chat\n", "agent-1-chat\nx", "\nagent"])
def test_a_name_component_never_keeps_a_newline(raw):
    from runtime_kernel.workload_env import name_component

    out = name_component(raw)
    assert "\n" not in out and "/" not in out and not out.startswith(".")
