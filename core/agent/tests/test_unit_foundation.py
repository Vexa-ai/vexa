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
