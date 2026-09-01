"""config.v1 (ADR-0026) — the runtime's declaration, capability tri-states (scheduler · bot_spawn ·
agent_spawn · model_inference), the credentials-FILE probe (the 'Model inference failed: Not logged
in' incident: docker bind-mounts a MISSING host path as an empty directory in the worker), and the
ADDITIVE /health capability rows next to the existing checks.

All offline: file probes run against tmp_path; env-level tri-state tests pass explicit env dicts.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from runtime_kernel import Runtime
from runtime_kernel import config_preflight as cp
from runtime_kernel.api import create_app


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    cp._reset_probe_cache()
    yield
    cp._reset_probe_cache()


def test_declaration_loads_and_is_internally_consistent():
    decl = cp.load_declaration()
    assert decl["service"] == "runtime"
    caps = decl["capabilities"]
    assert set(caps) == {"scheduler", "bot_spawn", "agent_spawn", "model_inference"}
    # model credentials are ALTERNATIVE paths (subscription mount OR an API-style key)
    assert caps["model_inference"]["mode"] == "any"
    assert caps["model_inference"]["probe"]["kind"] == "file"
    # the runtime has no required-explicit keys: it boots on defaults, capabilities gate features
    assert [k for k in decl["keys"] if k["class"] == "required-explicit"] == []


def test_capability_tri_states():
    assert cp.capability_states({})["scheduler"] == cp.NOT_CONFIGURED
    assert cp.capability_states({"REDIS_URL": "redis://r"})["scheduler"] == cp.CONFIGURED
    assert cp.capability_states({"BROWSER_IMAGE": "vexaai/vexa-bot:v012"})["bot_spawn"] == cp.CONFIGURED
    assert cp.capability_states({})["agent_spawn"] == cp.NOT_CONFIGURED
    # mode=any: any ONE credential path configures model_inference; none ⇒ not_configured
    assert cp.capability_states({})["model_inference"] == cp.NOT_CONFIGURED
    assert cp.capability_states({"ANTHROPIC_API_KEY": "sk-x"})["model_inference"] == cp.CONFIGURED
    assert cp.capability_states({"HOST_CLAUDE_CREDENTIALS": "/x.json"})["model_inference"] == cp.CONFIGURED


# ── the credentials-file probe (incident: SET path, absent host file) ────────────────────────────


def test_file_probe_ok_on_real_credentials_json(tmp_path):
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok"}}))
    env = {"HOST_CLAUDE_CREDENTIALS": str(creds)}
    rows = cp.capability_health(env)
    assert rows["model_inference"]["state"] == cp.CONFIGURED
    assert rows["model_inference"]["probe"]["ok"] is True


def test_file_probe_flags_missing_host_file(tmp_path):
    env = {"HOST_CLAUDE_CREDENTIALS": str(tmp_path / "nope" / "credentials.json")}
    rows = cp.capability_health(env)
    assert rows["model_inference"]["state"] == cp.MISCONFIGURED, (
        "a SET credentials path whose file is absent must show as misconfigured on /health — "
        "otherwise it only surfaces as 'Not logged in' inside a spawned worker"
    )
    assert "not found" in rows["model_inference"]["probe"]["reason"]


def test_file_probe_flags_directory_the_docker_missing_mount_signature(tmp_path):
    # docker bind-mounts a MISSING host path as an empty DIRECTORY — the exact worker failure mode
    mount = tmp_path / "mirror"
    mount.mkdir()
    env = {"HOST_CLAUDE_CREDENTIALS": str(mount)}
    rows = cp.capability_health(env)
    assert rows["model_inference"]["state"] == cp.MISCONFIGURED
    assert "not a regular file" in rows["model_inference"]["probe"]["reason"]


def test_file_probe_skipped_when_credential_rides_an_api_key():
    # mode=any satisfied by ANTHROPIC_API_KEY alone: the file probe must SKIP, not fail
    rows = cp.capability_health({"ANTHROPIC_API_KEY": "sk-x"})
    assert rows["model_inference"]["state"] == cp.CONFIGURED
    assert rows["model_inference"]["probe"].get("skipped")


def test_file_probe_uses_the_mirror_mount_fallback(tmp_path):
    # in compose the runtime cannot see the DOCKER-HOST path itself — it checks the mirror mount
    mirror = tmp_path / "host-claude-credentials"
    mirror.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok"}}))
    decl = cp.load_declaration()
    spec = decl["capabilities"]["model_inference"]["probe"]["file"]
    result = cp._file_probe({**spec, "fallback_paths": [str(mirror)]},
                            {"HOST_CLAUDE_CREDENTIALS": "/home/user/.claude/.credentials.json"}, 2)
    assert result["ok"] is True and result["path"] == str(mirror)


# ── the inode defect (dogfood 2026-08-31 → 09-01, ~32h of "Not logged in") ───────────────────────


def test_declared_mirrors_prefer_the_directory_mount_over_the_legacy_file():
    """A single-FILE bind is pinned to the inode it was created with; the claude CLI refreshes a
    token by rename(2)-ing a NEW inode over .credentials.json, so a long-lived container reads the
    pre-refresh token forever while `claude` works fine on the host. The DIRECTORY mirror resolves
    the filename live — so it is tried FIRST, and the file stays only as the compat fallback."""
    spec = cp.load_declaration()["capabilities"]["model_inference"]["probe"]["file"]
    assert spec["fallback_paths"] == [
        "/var/lib/vexa/host-claude/.credentials.json",
        "/var/lib/vexa/host-claude-credentials",
    ]


def test_file_probe_reads_the_directory_mirror_through_an_inode_swap(tmp_path):
    d = tmp_path / "host-claude"
    d.mkdir()
    creds = d / ".credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "before"}}))
    spec = {**cp.load_declaration()["capabilities"]["model_inference"]["probe"]["file"],
            "fallback_paths": [str(creds), str(tmp_path / "legacy")]}
    env = {"HOST_CLAUDE_CREDENTIALS": "/home/user/.claude/.credentials.json"}  # host path, unseen in-container
    assert cp._file_probe(spec, env, 2)["ok"] is True
    tmp = d / ".credentials.json.tmp"
    tmp.write_text(json.dumps({"claudeAiOauth": {"accessToken": "after"}}))
    tmp.rename(creds)                       # the refresh: atomic replace, NEW inode
    out = cp._file_probe(spec, env, 2)
    assert out["ok"] is True and out["path"] == str(creds)
    assert json.loads(creds.read_text())["claudeAiOauth"]["accessToken"] == "after"


def test_worker_credential_bind_falls_back_to_the_directory():
    """The worker bind is created fresh at every spawn, so it never went stale — but a deployment
    that configures only HOST_CLAUDE_DIR must still produce an authenticated worker."""
    from runtime_kernel.docker_backend import host_claude_credentials as hcc

    assert hcc({}) is None
    assert hcc({"HOST_CLAUDE_CREDENTIALS": "/h/.claude/.credentials.json"}) == "/h/.claude/.credentials.json"
    assert hcc({"HOST_CLAUDE_DIR": "/h/.claude"}) == "/h/.claude/.credentials.json"
    assert hcc({"HOST_CLAUDE_DIR": "/h/.claude/"}) == "/h/.claude/.credentials.json"
    # an explicit file always wins over the derived one
    assert hcc({"HOST_CLAUDE_DIR": "/h/.claude",
                "HOST_CLAUDE_CREDENTIALS": "/elsewhere/creds.json"}) == "/elsewhere/creds.json"


# ── /health rows (ADDITIVE next to the existing checks) ──────────────────────────────────────────


def test_health_carries_capability_rows_additively(monkeypatch):
    for k in ("REDIS_URL", "BROWSER_IMAGE", "AGENT_IMAGE", "HOST_CLAUDE_CREDENTIALS",
              "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "VEXA_LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    app = create_app(Runtime(profiles={"test": ["sleep", "30"]}))
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    # the pre-existing consumers' keys are untouched (O-RT-2)
    assert body["status"] == "ok"
    assert body["checks"]["backend"] is True and body["checks"]["store"] is True
    # the additive config.v1 rows; unconfigured capabilities NEVER degrade status (feature ≠ process)
    caps = body["capabilities"]
    assert caps["scheduler"]["state"] == cp.NOT_CONFIGURED
    assert caps["model_inference"]["state"] == cp.NOT_CONFIGURED
    assert set(caps) == {"scheduler", "bot_spawn", "agent_spawn", "model_inference"}
