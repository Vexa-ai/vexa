"""Foundation L2 tests — the unit dispatcher + the claude turn's post-write governance, over fakes.

Proves the load-bearing guarantee offline: a tool-using model that writes a NON-conformant entity has
its write rejected and reverted (the workspace.v1 gate can't be bypassed by the model), while a
conformant write commits. Plus the unit.v1 seam validation and the stream-json normalization.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from agent_api import contracts, dispatch
from agent_api.adapters import LocalIdentityMinter
from agent_api.config import load_settings
from agent_api.decision_claude import parse_stream_json, run_unit_turn


def _git(d: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=str(d), check=True, capture_output=True, text=True)


def _init_repo(d: Path) -> None:
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (d / "AGENT.md").write_text("seed\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "seed")


def _entity(fm: dict, body: str = "body") -> str:
    return "---\n" + yaml.safe_dump(fm, sort_keys=True).strip() + "\n---\n" + body


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


GOOD = {"type": "person", "id": "jane-liu", "title": "Jane Liu"}
BAD = {"title": "no type or id"}  # missing required type + id → workspace.v1 violation

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


# ── stream-json normalization ────────────────────────────────────────────────

def test_parse_stream_json_normalizes():
    lines = [
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"path": "x"}, "id": "t1"}]}}),
        json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}),
        json.dumps({"type": "result", "subtype": "success", "result": "done", "session_id": "s1"}),
        "not json — skipped",
    ]
    evs = list(parse_stream_json(lines))
    assert [e["type"] for e in evs] == ["message-delta", "tool-call", "tool-result", "done"]
    assert evs[0]["text"] == "hello"
    assert evs[1]["tool"] == "Write" and evs[1]["callId"] == "t1"
    assert evs[2]["ok"] is True
    assert evs[3]["sessionId"] == "s1" and evs[3]["ok"] is True


def test_parse_stream_json_partial_messages_stream_incrementally():
    # Captured --include-partial-messages JSONL shape: stream_event(content_block_delta/text_delta)*
    # then the consolidated assistant text block, then result. The deltas must surface incrementally
    # AND the trailing full block must NOT re-emit (else the text doubles).
    lines = [
        json.dumps({"type": "stream_event", "event": {"type": "message_start"}}),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_start", "index": 0}}),
        json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}}}),
        json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo "}}}),
        json.dumps({"type": "stream_event", "event": {
            "type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "world"}}}),
        json.dumps({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}}),
        # the consolidated assistant message claude emits at block close — must be suppressed:
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Hello world"}]}}),
        json.dumps({"type": "result", "subtype": "success", "result": "Hello world", "session_id": "s2"}),
    ]
    evs = list(parse_stream_json(lines))
    assert [e["type"] for e in evs] == ["message-delta", "message-delta", "message-delta", "done"]
    assert [e["text"] for e in evs[:3]] == ["Hel", "lo ", "world"]
    # incremental deltas concatenate to the full text with no duplication:
    assert "".join(e["text"] for e in evs[:3]) == "Hello world"
    # the result still carries the full reply (commit messages / non-streaming consumers):
    assert evs[3]["reply"] == "Hello world"


# ── the governance: conformant commits, non-conformant is reverted ───────────

def test_run_unit_turn_commits_conformant(tmp_path: Path):
    repo = tmp_path / "ws"
    repo.mkdir()
    _init_repo(repo)

    def fake_exec(argv, cwd):  # the "model" writes a conformant entity via its tools, then finishes
        f = Path(cwd) / "kg/entities/person/jane-liu.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_entity(GOOD))
        yield json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "wrote jane"}]}})
        yield json.dumps({"type": "result", "subtype": "success", "result": "wrote jane", "session_id": "s1"})

    evs = list(run_unit_turn(repo, "create jane", fake_exec))
    assert any(e["type"] == "commit" for e in evs)
    assert (repo / "kg/entities/person/jane-liu.md").exists()
    log = subprocess.run(["git", "log", "--oneline"], cwd=str(repo), capture_output=True, text=True).stdout
    assert "wrote jane" in log


def test_run_unit_turn_commits_nonconformant_free_zone(tmp_path: Path):
    """Free zone: governance is prompt-only — a non-conformant entity write is NO LONGER reverted;
    it commits like any other write (no enforcement gate)."""
    repo = tmp_path / "ws"
    repo.mkdir()
    _init_repo(repo)

    def fake_exec(argv, cwd):  # the "model" writes a non-conformant entity (missing type+id)
        f = Path(cwd) / "kg/entities/person/bad.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_entity(BAD))
        yield json.dumps({"type": "result", "subtype": "success", "result": "x", "session_id": "s1"})

    evs = list(run_unit_turn(repo, "create bad", fake_exec))
    assert not any(e["type"] == "rejected" for e in evs), "free zone never rejects"
    assert any(e["type"] == "commit" for e in evs), "the write must commit"
    # the write survived: the file is present and committed
    assert (repo / "kg/entities/person/bad.md").exists()


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
