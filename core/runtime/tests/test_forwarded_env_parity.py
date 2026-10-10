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

FORWARD = ("VEXA_AGENT_MAX_OUTPUT_TOKENS", "VEXA_AGENT_MAX_TOOL_CALLS", "VEXA_AGENT_MODEL", "VEXA_UNSET_DIAL",
           "VEXA_LLM_API_KEY", "ANTHROPIC_API_KEY")
SECRET_VALUE = "sk-forwarded-credential-0123456789"
RUNNABLE = Runnable(image="worker:1", command=["true"], forward_env=FORWARD)
SPEC = {"VEXA_AGENT_MODEL": "the-dispatchs-own-model", "VEXA_OWNER": "4242"}
EXPECTED = {"VEXA_AGENT_MAX_OUTPUT_TOKENS": "4096", "VEXA_AGENT_MAX_TOOL_CALLS": "25",
            "VEXA_AGENT_MODEL": "the-dispatchs-own-model", "VEXA_OWNER": "4242",
            "VEXA_LLM_API_KEY": SECRET_VALUE, "ANTHROPIC_API_KEY": SECRET_VALUE + "-a"}


@pytest.fixture
def runtime_env(monkeypatch):
    monkeypatch.setenv("VEXA_AGENT_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("VEXA_AGENT_MAX_TOOL_CALLS", "25")
    monkeypatch.setenv("VEXA_AGENT_MODEL", "the-runtimes-default")
    monkeypatch.delenv("VEXA_UNSET_DIAL", raising=False)
    monkeypatch.setenv("VEXA_LLM_API_KEY", SECRET_VALUE)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET_VALUE + "-a")


def _k8s_submit(monkeypatch, forward=FORWARD):
    """Start a worker on a fake cluster; returns (the Pod as submitted, the Secrets created)."""
    pods, secrets = [], []

    def kubectl(*args, check=True, stdin=None):
        class R:  # noqa: N801
            returncode, stdout, stderr = 0, "", ""
        r = R()
        if args[0] == "create":
            obj = json.loads(stdin)
            if obj["kind"] == "Pod":
                pods.append(obj)
                r.stdout = json.dumps({**obj, "metadata": {**obj["metadata"], "uid": "pod-uid-1"}})
            else:
                secrets.append(obj)
        return r
    monkeypatch.setattr(k8s_backend, "_kubectl", kubectl)
    K8sBackend().start("agent-1-chat", Runnable(image="worker:1", command=["true"], forward_env=forward), dict(SPEC))
    (pod,) = pods
    return pod, secrets


def _k8s_env(monkeypatch) -> dict[str, str]:
    """The environment the container gets: plain values, and secretKeyRefs resolved from the Secret."""
    pod, secrets = _k8s_submit(monkeypatch)
    data = {s["metadata"]["name"]: s["stringData"] for s in secrets}
    out = {}
    for e in pod["spec"]["containers"][0]["env"]:
        if "value" in e:
            out[e["name"]] = e["value"]
        else:
            ref = e["valueFrom"]["secretKeyRef"]
            out[e["name"]] = data[ref["name"]][ref["key"]]
    return out


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


#: A worker's env as agent-api's catalog dispatch stamps it (control_plane/dispatch.py): the subject's
#: model route under every harness's names — credentials included, an unused one stamped EMPTY so the
#: runtime never refills it — the unit's own tokens and keys, and plain settings.
CATALOG_DISPATCH = {
    "ANTHROPIC_AUTH_TOKEN": "sk-or-subject-route-0123456789",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
    "VEXA_LLM_API_KEY": "sk-or-subject-route-0123456789",
    "VEXA_LLM_BASE_URL": "https://openrouter.ai/api/v1",
    "VEXA_LLM_MODEL": "z-ai/glm-4.6",
    "VEXA_AGENT_IDENTITY_TOKEN": "vxi_identity-0123456789",
    "VEXA_MCP_DELEGATION_TOKEN": "vxd_delegation-0123456789",
    "VEXA_FLOWS_TIMELINE_KEY": "flows-timeline-key-0123456789",
    "VEXA_UNIT_IN_KEY": "unit-in-key-0123456789",
    "VEXA_AGENT_MAX_OUTPUT_TOKENS": "4096",
    "VEXA_OWNER": "4242",
}
CREDENTIAL_KEYS = ("ANTHROPIC_AUTH_TOKEN", "VEXA_LLM_API_KEY", "VEXA_AGENT_IDENTITY_TOKEN",
                   "VEXA_MCP_DELEGATION_TOKEN", "VEXA_FLOWS_TIMELINE_KEY", "VEXA_UNIT_IN_KEY")
SECRET_VALUES = sorted({CATALOG_DISPATCH[k] for k in CREDENTIAL_KEYS} | {SECRET_VALUE})


def _submit(monkeypatch, spec: dict, forward=FORWARD):
    pods, secrets = [], []

    def kubectl(*args, check=True, stdin=None):
        class R:  # noqa: N801
            returncode, stdout, stderr = 0, "", ""
        r = R()
        if args[0] == "create":
            obj = json.loads(stdin)
            if obj["kind"] == "Pod":
                pods.append(obj)
                r.stdout = json.dumps({**obj, "metadata": {**obj["metadata"], "uid": "pod-uid-1"}})
            else:
                secrets.append(obj)
        return r
    monkeypatch.setattr(k8s_backend, "_kubectl", kubectl)
    K8sBackend().start("agent-1-chat", Runnable(image="worker:1", command=["true"], forward_env=forward), dict(spec))
    (pod,) = pods
    return pod, secrets


def test_no_credential_of_a_catalog_dispatch_appears_in_the_pod_spec(runtime_env, monkeypatch):
    """Every credential the container receives — stamped by the dispatch (its model route, the unit's
    tokens) or forwarded by the runtime — rides a Secret the Pod owns and is read by secretKeyRef; no
    credential value is anywhere in the Pod object, and no env entry carries one as its value."""
    pod, secrets = _submit(monkeypatch, CATALOG_DISPATCH)
    manifest = json.dumps(pod)
    for value in SECRET_VALUES:
        assert value not in manifest
    env = {e["name"]: e for e in pod["spec"]["containers"][0]["env"]}
    for key in CREDENTIAL_KEYS:
        assert "value" not in env[key], key
        assert env[key]["valueFrom"]["secretKeyRef"]["key"] == key
    assert env["VEXA_AGENT_MAX_OUTPUT_TOKENS"]["value"] == "4096"         # declared, not secret
    assert env["ANTHROPIC_API_KEY"]["value"] == ""                        # empty: carries nothing
    (secret,) = secrets
    assert secret["stringData"]["VEXA_MCP_DELEGATION_TOKEN"] == "vxd_delegation-0123456789"
    assert secret["metadata"]["ownerReferences"] == [
        {"apiVersion": "v1", "kind": "Pod", "name": pod["metadata"]["name"], "uid": "pod-uid-1"}]
    assert {e["valueFrom"]["secretKeyRef"]["name"] for e in env.values() if "valueFrom" in e} \
        == {secret["metadata"]["name"]}


def test_the_secret_class_comes_from_the_config_contract(runtime_env, monkeypatch):
    """Not a hand list: a key the contract marks secret is hidden, one it declares plain is not, and
    one it does not declare at all is treated as secret."""
    from runtime_kernel.config_preflight import load_declaration

    declared = {k["key"]: bool(k.get("secret")) for k in load_declaration()["keys"]}
    assert declared["VEXA_LLM_API_KEY"] and declared["ANTHROPIC_API_KEY"] and not declared["VEXA_AGENT_MAX_OUTPUT_TOKENS"]
    monkeypatch.setenv("VEXA_NOT_IN_THE_CONTRACT", "unknown-value")
    pod, secrets = _submit(monkeypatch, {"LOG_LEVEL": "info"},
                           forward=("VEXA_NOT_IN_THE_CONTRACT", "VEXA_AGENT_MAX_TOOL_CALLS"))
    assert "unknown-value" not in json.dumps(pod)
    assert secrets[0]["stringData"] == {"VEXA_NOT_IN_THE_CONTRACT": "unknown-value"}
    env = {e["name"]: e for e in pod["spec"]["containers"][0]["env"]}
    assert env["LOG_LEVEL"]["value"] == "info" and env["VEXA_AGENT_MAX_TOOL_CALLS"]["value"] == "25"


def test_a_secret_that_cannot_be_created_takes_its_pod_down(runtime_env, monkeypatch):
    calls = []

    def kubectl(*args, check=True, stdin=None):
        calls.append(args[:2])

        class R:  # noqa: N801
            returncode, stdout, stderr = 0, "", ""
        r = R()
        if args[0] == "create" and json.loads(stdin)["kind"] == "Secret":
            raise RuntimeError("kubectl create failed: forbidden")
        if args[0] == "create":
            r.stdout = json.dumps({"metadata": {"uid": "u"}})
        return r
    monkeypatch.setattr(k8s_backend, "_kubectl", kubectl)
    with pytest.raises(RuntimeError):
        K8sBackend().start("agent-1-chat", RUNNABLE, dict(SPEC))
    assert ("delete", "pod") in calls
