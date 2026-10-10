"""What a kind of workload is given beyond its spec is profile data, applied the same way by every
backend: labels, the network setting it joins, the runtime settings forwarded into it, and the
credential files and settings it receives. No backend knows what a bot or an agent is, or which
harness reads which credential — a profile it has never heard of gets exactly what its data says,
and a bare runnable gets nothing.
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import re
import sys
import tokenize

from runtime_kernel.k8s_backend import build_pod
from runtime_kernel.process_backend import ProcessBackend
from runtime_kernel.profiles import CredentialFile, Runnable

from test_worker_image import _create_payload

TUNED = Runnable(image="tuned:1", labels={"tier": "batch"}, network_env="TUNED_NETWORK",
                 forward_env=("TUNED_DIAL",), credential_mounts=True,
                 credential_files=(CredentialFile(source="/host/auth.json", target="/creds/auth.json",
                                                  home_path=".creds/auth.json"),),
                 credential_env={"CRED_HOME": "/creds"})
BARE = Runnable(image="bare:1")


def _env(payload):
    return dict(item.split("=", 1) for item in payload["Env"])


def test_docker_applies_a_profiles_data_and_nothing_more(monkeypatch):
    monkeypatch.setenv("DOCKER_NETWORK", "default_net")
    monkeypatch.setenv("TUNED_NETWORK", "tuned_net")
    monkeypatch.setenv("TUNED_DIAL", "7")
    tuned = _create_payload(monkeypatch, TUNED, "job-1")
    assert tuned["Labels"]["tier"] == "batch"
    assert tuned["HostConfig"]["NetworkMode"] == "tuned_net"
    assert _env(tuned)["TUNED_DIAL"] == "7" and _env(tuned)["CRED_HOME"] == "/creds"
    assert tuned["HostConfig"]["Binds"] == ["/host/auth.json:/creds/auth.json:ro"]

    bare = _create_payload(monkeypatch, BARE, "job-2")
    assert "tier" not in bare["Labels"]
    assert bare["HostConfig"]["NetworkMode"] == "default_net"
    assert "TUNED_DIAL" not in _env(bare) and "CRED_HOME" not in _env(bare)
    assert not bare["HostConfig"].get("Binds")


def test_credential_data_reaches_only_a_profile_that_asks_for_credentials(monkeypatch):
    """The files and settings ride the data, and ``credential_mounts`` is what hands them over."""
    withheld = Runnable(**{**TUNED.__dict__, "credential_mounts": False})
    payload = _create_payload(monkeypatch, withheld, "job-3")
    assert not payload["HostConfig"].get("Binds") and "CRED_HOME" not in _env(payload)
    pod = build_pod(name="vexa-job-3", workload_id="job-3", runnable=withheld, env={}, namespace=None,
                    resources=None)
    assert all(e["name"] != "CRED_HOME" for e in pod["spec"]["containers"][0]["env"])


def test_k8s_applies_a_profiles_data_and_nothing_more():
    overlay = {"RUNTIME_K8S_SECRET_MOUNTS": '[{"secret": "cred", "mountPath": "/m"}]'}
    tuned = build_pod(name="vexa-job-1", workload_id="job-1", runnable=TUNED, env={}, namespace=None,
                      resources=None, overlay_env=overlay)
    assert tuned["metadata"]["labels"]["tier"] == "batch"
    container = tuned["spec"]["containers"][0]
    assert {"name": "CRED_HOME", "value": "/creds"} in container["env"]
    assert any(m["mountPath"] == "/m" for m in container["volumeMounts"])

    bare = build_pod(name="vexa-job-2", workload_id="job-2", runnable=BARE, env={}, namespace=None,
                     resources=None, overlay_env=overlay)
    assert "tier" not in bare["metadata"]["labels"]
    assert "volumeMounts" not in bare["spec"]["containers"][0]
    assert all(e["name"] != "CRED_HOME" for e in bare["spec"]["containers"][0]["env"])


def test_the_process_backend_forwards_a_profiles_list_and_nothing_more(monkeypatch, tmp_path):
    monkeypatch.setenv("TUNED_DIAL", "7")
    monkeypatch.setenv("PROCESS_LOG_DIR", str(tmp_path / "logs"))
    drop = tmp_path / "drop"
    drop.mkdir()
    os.chmod(drop, 0o1777)                     # under a root runtime the child is another uid
    for d in (tmp_path, tmp_path.parent, tmp_path.parent.parent):   # pytest's own dirs are 0700
        os.chmod(d, 0o755)
    out = drop / "env.json"
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


def _code_tokens(path: pathlib.Path) -> list[str]:
    """The names and string literals of a module's code — comments and docstrings left out."""
    toks = [t for t in tokenize.generate_tokens(io.StringIO(path.read_text()).readline)
            if t.type not in (tokenize.COMMENT, tokenize.NL)]
    out = []
    for i, t in enumerate(toks):
        if t.type == tokenize.STRING:
            before = toks[i - 1].type if i else tokenize.NEWLINE
            after = toks[i + 1].type if i + 1 < len(toks) else tokenize.NEWLINE
            if before in (tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT) and after == tokenize.NEWLINE:
                continue                                       # a docstring
        if t.type in (tokenize.NAME, tokenize.STRING):
            out.append(t.string)
    return out


def test_no_backend_knows_a_harness():
    """Which harness reads which credential file is profile data (``profiles.configured_credentials``);
    the backends and the process isolation only apply it."""
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "runtime_kernel"
    for name in ("docker_backend.py", "k8s_backend.py", "process_backend.py", "isolation.py"):
        named = [t for t in _code_tokens(src / name) if re.search(r"claude|codex", t, flags=re.I)]
        assert not named, (name, named)


def test_the_shipped_agent_profile_carries_the_configured_credentials(monkeypatch):
    from runtime_kernel import default_registry
    from runtime_kernel.workload_env import CODEX_HOME_ENV, WORKER_CODEX_HOME

    monkeypatch.setenv("HOST_CLAUDE_DIR", "/host/.claude")
    monkeypatch.setenv("HOST_CODEX_CREDENTIALS", "/host/.codex/auth.json")
    registry = default_registry()
    agent, bot = registry.get("agent").runnable, registry.get("meeting-bot").runnable
    assert agent.credential_files == (
        CredentialFile("/host/.claude/.credentials.json", "/root/.claude/.credentials.json",
                       ".claude/.credentials.json"),
        CredentialFile("/host/.codex/auth.json", f"{WORKER_CODEX_HOME}/auth.json", ".codex/auth.json"),
    )
    assert agent.credential_env == {CODEX_HOME_ENV: WORKER_CODEX_HOME}
    assert not bot.credential_mounts and bot.credential_files == () and bot.process_groups == ("pulse-access",)


def test_the_process_backend_stages_credential_files_only_for_a_profile_that_asks(monkeypatch, tmp_path):
    """As on docker and k8s, ``credential_mounts`` is what hands a profile's files over: a root
    process backend stages them into the child's fresh HOME only when it is set."""
    from runtime_kernel import process_backend as pb

    staged: list = []
    monkeypatch.setattr(pb, "plan_process_isolation", lambda env, euid=None: None)
    monkeypatch.setattr(pb, "group_ids", lambda names: ())
    monkeypatch.setattr(pb, "make_home", lambda uid, gid, **kw: staged.append(kw["staged"]) or ("/h", "/h/tmp"))
    backend = ProcessBackend(homes_root=str(tmp_path / "homes"))
    backend._identity("job-tuned", TUNED, {})
    backend._identity("job-withheld", Runnable(**{**TUNED.__dict__, "credential_mounts": False}), {})
    assert [[f.home_path for f in s] for s in staged] == [[".creds/auth.json"], []]
