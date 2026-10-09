"""What a kind of workload is given beyond its spec is profile data, applied the same way by every
backend: labels, the network setting it joins, the runtime settings forwarded into it, and whether the
runtime's credential files are mounted into it. No backend knows what a bot or an agent is — a
profile it has never heard of gets exactly what its data says, and a bare runnable gets nothing.
"""
from __future__ import annotations

import json
import sys

from runtime_kernel.k8s_backend import build_pod
from runtime_kernel.process_backend import ProcessBackend
from runtime_kernel.profiles import Runnable
from runtime_kernel.workload_env import CODEX_HOME_ENV, WORKER_CODEX_HOME

from test_worker_image import _create_payload

TUNED = Runnable(image="tuned:1", labels={"tier": "batch"}, network_env="TUNED_NETWORK",
                 forward_env=("TUNED_DIAL",), credential_mounts=True)
BARE = Runnable(image="bare:1")


def _env(payload):
    return dict(item.split("=", 1) for item in payload["Env"])


def test_docker_applies_a_profiles_data_and_nothing_more(monkeypatch):
    monkeypatch.setenv("DOCKER_NETWORK", "default_net")
    monkeypatch.setenv("TUNED_NETWORK", "tuned_net")
    monkeypatch.setenv("TUNED_DIAL", "7")
    monkeypatch.setenv("HOST_CODEX_CREDENTIALS", "/host/auth.json")
    tuned = _create_payload(monkeypatch, TUNED, "job-1")
    assert tuned["Labels"]["tier"] == "batch"
    assert tuned["HostConfig"]["NetworkMode"] == "tuned_net"
    assert _env(tuned)["TUNED_DIAL"] == "7" and _env(tuned)[CODEX_HOME_ENV] == WORKER_CODEX_HOME
    assert f"/host/auth.json:{WORKER_CODEX_HOME}/auth.json:ro" in tuned["HostConfig"]["Binds"]

    bare = _create_payload(monkeypatch, BARE, "job-2")
    assert "tier" not in bare["Labels"]
    assert bare["HostConfig"]["NetworkMode"] == "default_net"
    assert "TUNED_DIAL" not in _env(bare) and CODEX_HOME_ENV not in _env(bare)
    assert not bare["HostConfig"].get("Binds")


def test_k8s_applies_a_profiles_data_and_nothing_more():
    overlay = {"RUNTIME_K8S_SECRET_MOUNTS": '[{"secret": "cred", "mountPath": "/m"}]'}
    tuned = build_pod(name="vexa-job-1", workload_id="job-1", runnable=TUNED, env={}, namespace=None,
                      resources=None, overlay_env=overlay)
    assert tuned["metadata"]["labels"]["tier"] == "batch"
    container = tuned["spec"]["containers"][0]
    assert {"name": CODEX_HOME_ENV, "value": WORKER_CODEX_HOME} in container["env"]
    assert any(m["mountPath"] == "/m" for m in container["volumeMounts"])

    bare = build_pod(name="vexa-job-2", workload_id="job-2", runnable=BARE, env={}, namespace=None,
                     resources=None, overlay_env=overlay)
    assert "tier" not in bare["metadata"]["labels"]
    assert "volumeMounts" not in bare["spec"]["containers"][0]
    assert all(e["name"] != CODEX_HOME_ENV for e in bare["spec"]["containers"][0]["env"])


def test_the_process_backend_forwards_a_profiles_list_and_nothing_more(monkeypatch, tmp_path):
    monkeypatch.setenv("TUNED_DIAL", "7")
    monkeypatch.setenv("PROCESS_LOG_DIR", str(tmp_path))
    out = tmp_path / "env.json"
    code = f"import json, os; json.dump(dict(os.environ), open({str(out)!r}, 'w'))"
    for name, runnable, expected in (("tuned", TUNED, "7"), ("bare", BARE, None)):
        h = ProcessBackend().start(f"job-{name}", Runnable(**{**runnable.__dict__,
                                                              "command": [sys.executable, "-c", code]}), {})
        h._impl.wait(timeout=20)
        assert json.loads(out.read_text()).get("TUNED_DIAL") == expected


def test_the_shipped_profiles_carry_the_class_label_the_chart_selects_on():
    from runtime_kernel import default_registry
    from runtime_kernel.profiles import CLASS_LABEL

    registry = default_registry()
    assert registry.get("agent").runnable.labels == {CLASS_LABEL: "worker"}
    assert registry.get("meeting-bot").runnable.labels == {CLASS_LABEL: "bot"}
