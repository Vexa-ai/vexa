"""Every backend forwards a profile's settings into its workloads the same way.

A profile names the settings a workload receives from the runtime's own environment (the agent
worker's model, its output-token and tool-call caps, …: ``Runnable.forward_env``). The docker and
process backends forwarded them; the k8s backend did not, so a worker Pod ran without the caps the
runtime was configured with. Each backend now forwards exactly the keys the runtime holds a value for
and never refills a key the spec already carries.
"""
from __future__ import annotations

import json

import pytest

from runtime_kernel import k8s_backend
from runtime_kernel.k8s_backend import K8sBackend
from runtime_kernel.profiles import Runnable
from runtime_kernel.workload_env import child_environment

FORWARD = ("VEXA_AGENT_MAX_OUTPUT_TOKENS", "VEXA_AGENT_MAX_TOOL_CALLS", "VEXA_AGENT_MODEL", "VEXA_UNSET_DIAL")
RUNNABLE = Runnable(image="worker:1", command=["true"], forward_env=FORWARD)
SPEC = {"VEXA_AGENT_MODEL": "the-dispatchs-own-model", "VEXA_OWNER": "4242"}
EXPECTED = {"VEXA_AGENT_MAX_OUTPUT_TOKENS": "4096", "VEXA_AGENT_MAX_TOOL_CALLS": "25",
            "VEXA_AGENT_MODEL": "the-dispatchs-own-model", "VEXA_OWNER": "4242"}


@pytest.fixture
def runtime_env(monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("VEXA_AGENT_MAX_TOOL_CALLS", "25")
    monkeypatch.setenv("VEXA_AGENT_MODEL", "the-runtimes-default")
    monkeypatch.delenv("VEXA_UNSET_DIAL", raising=False)


def _k8s_env(monkeypatch) -> dict[str, str]:
    created = []

    def kubectl(*args, check=True, stdin=None):
        class R:  # noqa: N801
            returncode, stdout, stderr = 0, "", ""
        if args[0] == "create":
            created.append(json.loads(stdin))
        return R()
    monkeypatch.setattr(k8s_backend, "_kubectl", kubectl)
    K8sBackend().start("agent-1-chat", RUNNABLE, dict(SPEC))
    (pod,) = created
    return {e["name"]: e["value"] for e in pod["spec"]["containers"][0]["env"]}


def _docker_env(monkeypatch) -> dict[str, str]:
    from test_worker_image import FakeResp, _backend

    b, sess = _backend({("POST", "/containers/create"): FakeResp(201, body={"Id": "cid123"}),
                        ("POST", "/containers/cid123/start"): FakeResp(204)})
    captured, orig = {}, sess.request

    def spy(method, url, **kw):
        if method == "POST" and "/containers/create" in url:
            captured.update(kw.get("json", {}))
        return orig(method, url, **kw)
    sess.request = spy
    b.start("agent-1-chat", RUNNABLE, dict(SPEC))
    return dict(e.split("=", 1) for e in captured["Env"])


def _process_env() -> dict[str, str]:
    return child_environment(dict(SPEC), forward=RUNNABLE.forward_env)


def _ours(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k in set(FORWARD) | set(SPEC)}


def test_a_k8s_worker_pod_receives_the_profiles_forwarded_settings(runtime_env, monkeypatch):
    assert _ours(_k8s_env(monkeypatch)) == EXPECTED


def test_all_three_backends_forward_the_same_set(runtime_env, monkeypatch):
    k8s, docker, process = _ours(_k8s_env(monkeypatch)), _ours(_docker_env(monkeypatch)), _ours(_process_env())
    assert k8s == docker == process == EXPECTED
