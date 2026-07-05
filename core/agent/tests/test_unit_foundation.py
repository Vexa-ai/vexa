"""Foundation L2 tests — the unit dispatcher over fakes, plus the unit.v1 seam validation.

(The harness-turn governance + stream-json normalization tests moved to test_llm_claude_code.py
with the llm module split.)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest

import contracts
from control_plane import dispatch
from shared.adapters import LocalIdentityMinter
from shared.config import load_settings


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


VALID_INV = {
    "identity": {"subject": "u_jane", "launcher": "user:u_jane"},
    "runner": "claude-code",
    "workspaces": [{"id": "u_jane", "mode": "rw"}],
    "trigger": "message",
    "context": {"kind": "none"},
    "start": {"entrypoint": {"inline": "hi"}},
}


# ── unit.v1 seam ─────────────────────────────────────────────────────────────

def test_validate_unit_invocation_ok():
    contracts.validate_unit_invocation(VALID_INV)  # must not raise


def test_validate_unit_invocation_rejects_missing_identity():
    bad = {k: v for k, v in VALID_INV.items() if k != "identity"}
    with pytest.raises(Exception):
        contracts.validate_unit_invocation(bad)


# ── the dispatcher: unit.v1 → runtime.v1 spawn, quota keyed on the person ─────

class _FakeRuntime:
    def __init__(self):
        self.spawned = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append((workload_id, profile, env))
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def test_dispatcher_spawns_isolated_container_with_minted_token():
    settings = load_settings()
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(settings, rt, _FakeIdentity())
    wid = d.dispatch(VALID_INV)
    assert wid and rt.spawned
    _, profile, env = rt.spawned[0]
    assert profile == settings.agent_profile
    assert env["VEXA_OWNER"] == "u_jane"                       # quota axis = the person
    assert env["VEXA_LAUNCHER"] == "user:u_jane"
    assert env["VEXA_AGENT_IDENTITY_TOKEN"] == "tok"           # the per-dispatch minted token, injected
    assert env["VEXA_UNIT_TRIGGER"] == "message"
    assert '"id": "u_jane"' in env["VEXA_WORKSPACES"] and '"mode": "rw"' in env["VEXA_WORKSPACES"]
    assert env["VEXA_UNIT_OUT_TOPIC"] == f"unit:{wid}:out"


def test_dispatcher_worker_env_carries_configured_model():
    settings = load_settings(agent_model="deepseek/deepseek-v4-pro")
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(settings, rt, _FakeIdentity())
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    assert env["VEXA_AGENT_MODEL"] == "deepseek/deepseek-v4-pro"


def test_dispatcher_worker_env_carries_configured_meeting_model():
    settings = load_settings(meeting_model="openrouter/free")
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(settings, rt, _FakeIdentity())
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    assert env["VEXA_MEETING_MODEL"] == "openrouter/free"


def test_dispatcher_worker_env_carries_meeting_transcript_cursor():
    settings = load_settings()
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(settings, rt, _FakeIdentity())
    inv = {
        **VALID_INV,
        "trigger": "transcription",
        "identity": {"subject": "u_jane", "launcher": "integration:meetings"},
        "workspaces": [{"id": "u_jane", "mode": "ro"}],
        "context": {"kind": "meeting", "meeting": {
            "meeting_id": "abc-defg-hij",
            "session_uid": "abc-defg-hij",
            "platform": "google_meet",
            "transcript_start_id": "42-0",
        }},
    }
    d.dispatch(inv)
    _, _profile, env = rt.spawned[0]
    assert env["VEXA_TRANSCRIPT_STREAM"] == "tc:meeting:abc-defg-hij"
    assert env["VEXA_TRANSCRIPT_START_ID"] == "42-0"
    assert env["VEXA_IDLE_TIMEOUT_SEC"] == str(4 * 60 * 60)


def test_local_identity_minter_emits_signed_dispatch_claims():
    token = LocalIdentityMinter("secret", ttl_sec=60).mint(
        "u_jane",
        "user:u_jane",
        [{"id": "u_jane", "mode": "rw"}],
        ["workspace.write"],
    )
    header, payload, signature = token.split(".")
    claims = json.loads(_b64u_decode(payload))
    assert claims["sub"] == "u_jane"
    assert claims["lch"] == "user:u_jane"
    assert claims["ws"] == [{"id": "u_jane", "mode": "rw"}]
    assert claims["tools"] == ["workspace.write"]
    assert claims["exp"] - claims["iat"] == 60
    expected = hmac.new(b"secret", f"{header}.{payload}".encode("ascii"), hashlib.sha256).digest()
    assert hmac.compare_digest(expected, _b64u_decode(signature))


def test_dispatcher_rejects_nonconformant_invocation():
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(load_settings(), rt, _FakeIdentity())
    with pytest.raises(Exception):
        d.dispatch({"trigger": "message"})  # missing required fields
    assert not rt.spawned


def test_dispatcher_worker_env_carries_numeric_meeting_id():
    """`numeric_meeting_id` (the meetings-domain ROW id — unique per meeting run, unlike the native
    id a re-sent bot reuses) is an INTERNAL routing hint: it must reach the worker env
    (VEXA_MEETING_NUMERIC_ID → the proc:meeting:{numeric} processed-notes key the meeting-api
    db-writer persists) while being STRIPPED before the sealed unit.v1 check
    (MeetingRef is additionalProperties:false) — exactly like transcript_start_id."""
    settings = load_settings()
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(settings, rt, _FakeIdentity())
    inv = {
        **VALID_INV,
        "trigger": "transcription",
        "identity": {"subject": "u_jane", "launcher": "integration:meetings"},
        "workspaces": [{"id": "u_jane", "mode": "ro"}],
        "context": {"kind": "meeting", "meeting": {
            "meeting_id": "abc-defg-hij",
            "session_uid": "abc-defg-hij",
            "platform": "google_meet",
            "numeric_meeting_id": "17",
        }},
    }
    d.dispatch(inv)  # would raise at the seam if the hint leaked into the contract check
    _, _profile, env = rt.spawned[0]
    assert env["VEXA_MEETING_NUMERIC_ID"] == "17"
    assert env["VEXA_TRANSCRIPT_STREAM"] == "tc:meeting:abc-defg-hij"  # transcript key stays NATIVE

# ── model-auth passthrough: agent-api env → worker spec env (the k8s/helm credential seam) ────

def test_dispatcher_worker_env_passes_model_auth_allowlist(monkeypatch):
    """Every allowlisted model-auth var set on agent-api lands in the worker spec env — this is the
    ONLY credential path on the k8s/process backends (no bind-mount, no runtime brokering)."""
    for key in dispatch.MODEL_AUTH_ENV_ALLOWLIST:
        monkeypatch.setenv(key, f"val-{key}")
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(load_settings(), rt, _FakeIdentity())
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    for key in dispatch.MODEL_AUTH_ENV_ALLOWLIST:
        assert env[key] == f"val-{key}"


def test_dispatcher_worker_env_omits_unset_model_auth(monkeypatch):
    """Unset (or blank) auth stays ABSENT — a creds-less CI stack must boot fine; the worker fails
    at inference with the existing actionable auth/preflight error, not a poisoned empty var."""
    for key in dispatch.MODEL_AUTH_ENV_ALLOWLIST:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")  # blank-only value must be skipped too
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(load_settings(), rt, _FakeIdentity())
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    assert not any(key in env for key in dispatch.MODEL_AUTH_ENV_ALLOWLIST)


def test_dispatcher_worker_env_forwards_only_the_allowlist(monkeypatch):
    """No blanket env forwarding (P14/P15): a non-allowlisted secret in agent-api's environment
    never reaches the worker."""
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "never-forward-me")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sub-token")
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(load_settings(), rt, _FakeIdentity())
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sub-token"


# ── the additive mount set in the dispatch (WP-A1.1) ──────────────────────────

def _seed_ws(root, subject, marker="SEED"):
    """A seeded private baseline at <root>/<subject> (mirrors the workspace_attach test helper)."""
    from shared.seeding import seed_workspace
    ws = root / subject
    ws.mkdir(parents=True)
    (ws / "CLAUDE.md").write_text(marker)
    seed_workspace(ws, None)
    return ws


def test_dispatcher_worker_env_carries_the_baseline_plus_system_tier(tmp_path):
    """No activated extras, no _global configured → VEXA_MOUNTS is the three-tier stack degraded to the
    private baseline + the always-present PRIVATE SYSTEM tier (AMENDMENT 4). The active portion is exactly
    today's single-workspace behavior; _system is appended (create-if-absent, read-write)."""
    settings = load_settings(workspaces_dir=str(tmp_path / "workspaces"))
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(settings, rt, _FakeIdentity())
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    mounts = json.loads(env["VEXA_MOUNTS"])
    assert [m["role"] for m in mounts] == ["private", "system"]  # no _global (unconfigured)
    assert mounts[0]["primary"] is True and mounts[0]["path"].endswith("/u_jane")
    sysm = mounts[-1]
    assert sysm["slug"] == "_system" and sysm["write"] is True and sysm["primary"] is False


def test_dispatcher_mount_stack_is_global_then_active_then_system(tmp_path):
    """The full THREE-TIER stack (AMENDMENT 4): _global (RO) first, then the ORDERED active set (private
    baseline + the activated extra), then _system (RW) last — VEXA_MOUNTS is the whole stack, a LIST."""
    from control_plane.workspace_attach import activate_workspace
    import subprocess
    root = tmp_path / "workspaces"
    _seed_ws(root, "u_jane")
    # a local git repo to activate as a second, additive workspace (no network)
    origin = tmp_path / "shared"
    origin.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True)
    run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (origin / "CLAUDE.md").write_text("SHARED"); run("add", "-A"); run("commit", "-q", "-m", "s")
    slug = activate_workspace(root, "u_jane", str(origin), "main").slug
    # a platform-owned _global repo, mounted READ-ONLY into every worker
    gdir = tmp_path / "global"
    gdir.mkdir()
    grun = lambda *a: subprocess.run(["git", *a], cwd=gdir, check=True, capture_output=True)
    grun("init", "-q", "-b", "main"); grun("config", "user.email", "p@p"); grun("config", "user.name", "p")
    (gdir / "CLAUDE.md").write_text("GLOBAL"); grun("add", "-A"); grun("commit", "-q", "-m", "g")

    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(gdir))
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(settings, rt, _FakeIdentity())
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    mounts = json.loads(env["VEXA_MOUNTS"])
    roles = [m["role"] for m in mounts]
    assert roles == ["global", "private", "private", "system"]  # the three-tier stack, in order
    g, base, extra, sysm = mounts
    # tier 1 — _global: READ-ONLY, its own host source, absent from the active-set commit path
    assert g["slug"] == "_global" and g["write"] is False and g["source"] == str(gdir)
    assert g["path"] == str(root / "_global")
    # tier 2 — the active set: private baseline (in place) + the activated extra (store slot)
    assert base["primary"] is True and base["path"] == str(root / "u_jane")
    assert extra["slug"] == slug and extra["primary"] is False
    assert extra["path"] == str(root / ".attached" / "u_jane" / slug)
    # tier 3 — _system: read-write, per-user, always last
    assert sysm["slug"] == "_system" and sysm["write"] is True


def test_dispatcher_stamps_principal_for_attribution(monkeypatch):
    """The dispatch principal (VEXA_PRINCIPAL_*) is stamped for per-mount commit attribution (D4).
    Absent an explicit principal it defaults to the subject."""
    monkeypatch.delenv("VEXA_PRINCIPAL_NAME", raising=False)
    monkeypatch.delenv("VEXA_PRINCIPAL_EMAIL", raising=False)
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(load_settings(), rt, _FakeIdentity())
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    assert env["VEXA_PRINCIPAL_NAME"] == "u_jane"
    assert env["VEXA_PRINCIPAL_EMAIL"] == "u_jane@vexa.local"


def test_dispatcher_principal_env_wins_over_subject(monkeypatch):
    monkeypatch.setenv("VEXA_PRINCIPAL_NAME", "Jane Doe")
    monkeypatch.setenv("VEXA_PRINCIPAL_EMAIL", "jane@example.com")
    rt = _FakeRuntime()
    d = dispatch.Dispatcher(load_settings(), rt, _FakeIdentity())
    d.dispatch(VALID_INV)
    _, _profile, env = rt.spawned[0]
    assert env["VEXA_PRINCIPAL_NAME"] == "Jane Doe"
    assert env["VEXA_PRINCIPAL_EMAIL"] == "jane@example.com"
