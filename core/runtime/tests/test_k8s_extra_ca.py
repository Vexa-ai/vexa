"""Corporate TLS inspection (chart global.extraCA → RUNTIME_K8S_EXTRA_CA): every Pod the runtime
spawns — meeting bots and agent workers — trusts the operator's root CA the way the chart's own Pods
do. Offline: the whole Pod manifest is asserted, no cluster."""
from __future__ import annotations

import json
import subprocess

import pytest

from runtime_kernel.k8s_backend import (CA_BUNDLE_FILE, EXTRA_CA_ENV, EXTRA_CA_FILE, EXTRA_CA_SCRIPT,
                                        K8sBackend, build_pod, extra_ca)
from runtime_kernel.models import Resources
from runtime_kernel.profiles import Runnable

CM = json.dumps({"configMap": "corp-ca", "key": "ca.crt", "awsCaBundle": True})


def _pod(env=None, overlay=None, resources=None, runnable=None):
    return build_pod(name="vexa-w", workload_id="w", runnable=runnable or Runnable(image="vexaai/vexa-bot:t"),
                     env=env or {"LOG_LEVEL": "info"}, namespace="ns", resources=resources,
                     overlay_env=overlay)


def test_unset_leaves_the_pod_unchanged():
    assert _pod() == _pod(overlay={"LOG_LEVEL": "info"})
    spec = _pod()["spec"]
    assert "initContainers" not in spec and "volumes" not in spec
    assert not any(e["name"] in ("SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS") for e in spec["containers"][0]["env"])


def test_set_mounts_bundles_and_points_every_runtime_at_it():
    pod = _pod(overlay={EXTRA_CA_ENV: CM})
    spec = pod["spec"]
    c = spec["containers"][0]
    vols = {v["name"]: v for v in spec["volumes"]}
    assert vols["vexa-extra-ca"]["configMap"] == {"name": "corp-ca", "defaultMode": 0o444,
                                                  "items": [{"key": "ca.crt", "path": "ca.crt"}]}
    assert vols["vexa-ca-bundle"] == {"name": "vexa-ca-bundle", "emptyDir": {"sizeLimit": "8Mi"}}
    init = spec["initContainers"][0]
    assert init["name"] == "vexa-ca-bundle" and init["image"] == "vexaai/vexa-bot:t"   # the image's own bundle
    assert init["command"] == ["/bin/sh", "-c", EXTRA_CA_SCRIPT]
    assert all(m.get("readOnly") for m in c["volumeMounts"])
    env = {e["name"]: e["value"] for e in c["env"]}
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "GIT_SSL_CAINFO", "AWS_CA_BUNDLE"):
        assert env[k] == CA_BUNDLE_FILE
    assert env["NODE_EXTRA_CA_CERTS"] == EXTRA_CA_FILE and env["VEXA_EXTRA_CA_FILE"] == EXTRA_CA_FILE
    assert env["LOG_LEVEL"] == "info"


def test_init_container_is_as_hardened_and_sized_as_the_workload():
    pod = _pod(overlay={EXTRA_CA_ENV: CM}, resources=Resources(cpu=1, memoryMb=512),
               runnable=Runnable(image="w:t", capabilities=("CHOWN",)))
    c, init = pod["spec"]["containers"][0], pod["spec"]["initContainers"][0]
    assert init["resources"] == c["resources"]
    assert init["securityContext"]["allowPrivilegeEscalation"] is False
    assert init["securityContext"]["capabilities"] == {"drop": ["ALL"]}          # no add for the writer
    assert c["securityContext"]["capabilities"]["add"] == ["CHOWN"]              # the workload keeps its own


def test_secret_source_and_aws_opt_out():
    pod = _pod(overlay={EXTRA_CA_ENV: json.dumps({"secret": "corp-ca", "key": "root.pem", "awsCaBundle": False})})
    vol = {v["name"]: v for v in pod["spec"]["volumes"]}["vexa-extra-ca"]
    assert vol["secret"]["secretName"] == "corp-ca" and vol["secret"]["items"][0]["key"] == "root.pem"
    assert "AWS_CA_BUNDLE" not in {e["name"] for e in pod["spec"]["containers"][0]["env"]}


def test_a_key_the_workload_sets_wins():
    pod = _pod(env={"NODE_EXTRA_CA_CERTS": "/own.pem"}, overlay={EXTRA_CA_ENV: CM, "NODE_EXTRA_CA_CERTS": "/own.pem"})
    names = [e["name"] for e in pod["spec"]["containers"][0]["env"]]
    assert names.count("NODE_EXTRA_CA_CERTS") == 1
    assert {e["name"]: e.get("value") for e in pod["spec"]["containers"][0]["env"]}["NODE_EXTRA_CA_CERTS"] == "/own.pem"


def test_coexists_with_workspace_mounts():
    overlay = {EXTRA_CA_ENV: CM, "VEXA_WORKSPACE_MOUNT_SOURCE": "pvc", "VEXA_WORKSPACE_MOUNT_TARGET": "/workspaces",
               "VEXA_MOUNTS": json.dumps([{"target": "/workspaces/a"}])}
    pod = _pod(overlay=overlay)
    names = [v["name"] for v in pod["spec"]["volumes"]]
    assert "vexa-extra-ca" in names and "vexa-ca-bundle" in names


@pytest.mark.parametrize("raw", ["{", "[]", json.dumps({"key": "ca.crt"}),
                                 json.dumps({"configMap": "a", "secret": "b"}),
                                 json.dumps({"configMap": "Bad_Name"}), json.dumps({"configMap": "a", "key": "a/b"})])
def test_malformed_is_fatal_at_boot(monkeypatch, raw):
    with pytest.raises(ValueError):
        extra_ca({EXTRA_CA_ENV: raw})
    monkeypatch.setenv(EXTRA_CA_ENV, raw)
    with pytest.raises(ValueError):
        K8sBackend(namespace="ns")


def test_runtime_process_env_reaches_the_spawned_pod(monkeypatch):
    monkeypatch.setenv(EXTRA_CA_ENV, CM)
    seen = {}

    def fake(*args, check=True, stdin=None):
        if args[:1] == ("create",):
            seen["pod"] = json.loads(stdin)
            return subprocess.CompletedProcess(args, 0, "pod/x created", "")
        return subprocess.CompletedProcess(args, 0, "{}", "")
    monkeypatch.setattr("runtime_kernel.k8s_backend._kubectl", fake)
    K8sBackend(namespace="ns").start("mtg-1", Runnable(image="bot:t"), env={"LOG_LEVEL": "b"})
    spec = seen["pod"]["spec"]
    assert spec["initContainers"][0]["name"] == "vexa-ca-bundle"
    # the runtime's knob shapes the Pod; it is not handed to the workload as config
    assert EXTRA_CA_ENV not in {e["name"] for e in spec["containers"][0]["env"]}


def test_bundle_script_writes_system_bundle_plus_extra(tmp_path):
    """The script itself, run under /bin/sh against a fake filesystem."""
    sysb, extra, out = tmp_path / "sys.crt", tmp_path / "ca.crt", tmp_path / "out"
    sysb.write_text("-----BEGIN CERTIFICATE-----\nSYS\n-----END CERTIFICATE-----")
    extra.write_text("-----BEGIN CERTIFICATE-----\nCORP\n-----END CERTIFICATE-----\n")
    out.mkdir()
    script = (EXTRA_CA_SCRIPT.replace("/etc/ssl/certs/ca-certificates.crt", str(sysb))
              .replace(EXTRA_CA_FILE, str(extra)).replace(CA_BUNDLE_FILE, str(out / "b.crt")))
    r = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    body = (out / "b.crt").read_text()
    assert body.count("BEGIN CERTIFICATE") == 2 and body.index("SYS") < body.index("CORP")
    extra.write_text("not a pem")
    assert subprocess.run(["/bin/sh", "-c", script], capture_output=True).returncode == 1


def test_a_secret_key_that_is_a_model_or_control_plane_secret_is_refused():
    from runtime_kernel import workload_env
    key = sorted(workload_env.REFUSED_SECRET_KEYS)[0]
    with pytest.raises(ValueError):
        extra_ca({EXTRA_CA_ENV: json.dumps({"secret": "vexa-secrets", "key": key})})
    # the same name on a ConfigMap carries no secret and is the operator's own choice of key
    assert extra_ca({EXTRA_CA_ENV: json.dumps({"configMap": "corp-ca", "key": "ca-bundle.crt"})})


def _spawned(monkeypatch, profile: str, env: dict) -> dict:
    """Start the REAL registry's profile through K8sBackend, with the chart's runtime env."""
    from runtime_kernel import default_registry
    monkeypatch.setenv("BROWSER_IMAGE", "vexaai/vexa-bot:t")
    monkeypatch.setenv("AGENT_IMAGE", "vexaai/agent-api:t")
    monkeypatch.setenv(EXTRA_CA_ENV, json.dumps({"configMap": "corp-ca", "key": "ca-bundle.crt",
                                                 "awsCaBundle": True}))
    seen = {}

    def fake(*args, check=True, stdin=None):
        if args[:1] == ("create",):
            seen["pod"] = json.loads(stdin)
            return subprocess.CompletedProcess(args, 0, "pod/x created", "")
        return subprocess.CompletedProcess(args, 0, "{}", "")
    monkeypatch.setattr("runtime_kernel.k8s_backend._kubectl", fake)
    runnable = default_registry().resolve(profile)
    K8sBackend(namespace="ns").start(f"{profile}-1", runnable, env=env)
    return seen["pod"]


@pytest.mark.parametrize("profile", ["meeting-bot", "agent"])
def test_every_spawned_class_trusts_the_ca(monkeypatch, profile):
    """A meeting bot and an agent worker, from the deployment's own registry: Python (requests,
    httpx through SSL_CERT_FILE), Node (NODE_EXTRA_CA_CERTS) and the bot's Chromium (VEXA_EXTRA_CA_FILE,
    imported into NSS by entrypoint.sh) all read the operator's CA; the bundle writer runs the
    workload's own image under the workload's own hardening."""
    pod = _spawned(monkeypatch, profile, {"LOG_LEVEL": "info"})
    spec, c = pod["spec"], pod["spec"]["containers"][0]
    init = spec["initContainers"][0]
    assert init["name"] == "vexa-ca-bundle" and init["image"] == c["image"]
    assert init["securityContext"]["allowPrivilegeEscalation"] is False
    assert "add" not in init["securityContext"]["capabilities"]
    env = {e["name"]: e.get("value") for e in c["env"]}
    for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "GIT_SSL_CAINFO", "AWS_CA_BUNDLE"):
        assert env[k] == CA_BUNDLE_FILE, k
    assert env["NODE_EXTRA_CA_CERTS"] == EXTRA_CA_FILE and env["VEXA_EXTRA_CA_FILE"] == EXTRA_CA_FILE
    vols = {v["name"]: v for v in spec["volumes"]}
    assert vols["vexa-extra-ca"]["configMap"]["items"] == [{"key": "ca-bundle.crt", "path": "ca.crt"}]
    mounts = {m["name"]: m for m in c["volumeMounts"]}
    assert mounts["vexa-extra-ca"]["readOnly"] and mounts["vexa-ca-bundle"]["readOnly"]


def test_spawned_pod_keeps_its_secret_mounts_beside_the_ca(monkeypatch):
    """RUNTIME_K8S_SECRET_MOUNTS (one named key per mount) and the CA land in the same Pod."""
    monkeypatch.setenv("VEXA_ALLOW_SUBSCRIPTION_TOKEN_PROXY", "true")
    monkeypatch.setenv("RUNTIME_K8S_SECRET_MOUNTS",
                       json.dumps([{"secret": "codex-auth", "file": "auth.json", "mountPath": "/home/w/.codex/auth.json"}]))
    pod = _spawned(monkeypatch, "agent", {"LOG_LEVEL": "info"})
    names = [v["name"] for v in pod["spec"]["volumes"]]
    assert "vexa-extra-ca" in names and "vexa-ca-bundle" in names and len(names) == len(set(names))
    assert any(v.get("secret", {}).get("secretName") == "codex-auth" for v in pod["spec"]["volumes"])


def test_spawned_pod_carries_no_secret_mount_unless_the_install_allows_it(monkeypatch):
    """The credential-file Secrets are subscription files: with VEXA_ALLOW_SUBSCRIPTION_TOKEN_PROXY
    off (the default) none is mounted into a worker Pod, whatever RUNTIME_K8S_SECRET_MOUNTS names."""
    monkeypatch.delenv("VEXA_ALLOW_SUBSCRIPTION_TOKEN_PROXY", raising=False)
    monkeypatch.setenv("RUNTIME_K8S_SECRET_MOUNTS",
                       json.dumps([{"secret": "codex-auth", "file": "auth.json", "mountPath": "/home/w/.codex/auth.json"}]))
    pod = _spawned(monkeypatch, "agent", {"LOG_LEVEL": "info"})
    assert not any(v.get("secret", {}).get("secretName") == "codex-auth"
                   for v in pod["spec"].get("volumes", []))
