"""Who may hand agent-api a turn to run — the dispatch sinks and the chat door.

`POST /invocations` and `POST /events` take a body that names the person the turn runs as, so the
CALLER is authenticated: the internal tier, and nobody else (`control_plane/dispatch_sink.py`). The
signed routine job is gone with the routine scheduler path (ADR-0046): a signature opens nothing. A chat turn (`/api/chat`, `/api/chat/submit`) is started by a person,
never by a worker acting for one, and an unwatched worker does not arm a routine.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from control_plane import identity_token
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from shared.config import load_settings
from shared.units import entrypoint, make_dispatch
from tests.test_routine_flows import _FakeScheduler

SECRET = "agent-test-internal-secret"
KEY = identity_token.generate_signing_key()


class _Runtime:
    def __init__(self):
        self.spawned = []

    def spawn(self, workload_id, profile, env):
        self.spawned.append((workload_id, profile, env))
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


def _routine_body(subject="u_jane", trigger="scheduled"):
    body = make_dispatch(subject=subject, trigger="scheduled", start=entrypoint(inline="go"),
                         context={"kind": "generic"}, launcher="schedule:rt_daily")
    body["trigger"] = trigger
    return body


@pytest.fixture
def sink(monkeypatch, tmp_path):
    public = tmp_path / "identity-public-key.pem"
    public.write_bytes(identity_token.public_key_pem(KEY))
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    runtime = _Runtime()
    client = TestClient(create_app(Dispatcher(load_settings(), runtime, _Identity()),
                                   scheduler=_FakeScheduler(),
                                   invocations_url="http://agent-api:8100/invocations"))
    return client, runtime


# ── /invocations ─────────────────────────────────────────────────────────────────────────────────
def test_a_caller_with_no_credential_is_refused_and_nothing_spawns(sink):
    client, runtime = sink
    for headers in ({}, {"X-Internal-Secret": "wrong"}, {"X-Vexa-Dispatch-Signature": "v1=00"},
                    {"X-User-Id": "7", "X-Internal-Secret": "wrong"}):
        r = client.post("/invocations", json=_routine_body(), headers=headers)
        assert r.status_code == 401, (headers, r.status_code)
    assert runtime.spawned == []


def test_the_internal_tier_dispatches(sink):
    client, runtime = sink
    r = client.post("/invocations", json=_routine_body(trigger="message"),
                    headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 202
    assert len(runtime.spawned) == 1


def test_a_job_signed_the_old_way_is_refused(sink):
    """Routine jobs were once signed by agent-api and fired back by the runtime's scheduler; that path
    is removed, so the header they carried opens nothing — whatever it says."""
    client, runtime = sink
    for trigger in ("scheduled", "message"):
        r = client.post("/invocations", json=_routine_body(trigger=trigger),
                        headers={"X-Vexa-Dispatch-Signature": "v1=" + "0" * 64})
        assert r.status_code == 401
    assert runtime.spawned == []


def test_a_signed_identity_does_not_open_the_sink(sink):
    """The door rebuilds x-user-* for a signed identity; the sink reads none of it and still asks
    who the CALLER is."""
    client, runtime = sink
    signed = {identity_token.HEADER: identity_token.sign(KEY, {"sub": "7"})}
    assert client.post("/invocations", json=_routine_body(), headers=signed).status_code == 401
    assert runtime.spawned == []


# ── /events ──────────────────────────────────────────────────────────────────────────────────────
EVENT = {"name": "email.received", "subject": "u_jane", "plan": {"prompt": "triage this"}}


def test_the_event_sink_takes_the_internal_tier_only(sink):
    client, runtime = sink
    assert client.post("/events", json=EVENT).status_code == 401
    assert client.post("/events", json=EVENT,
                       headers={"X-Vexa-Dispatch-Signature": "v1=00"}).status_code == 401
    assert runtime.spawned == []
    r = client.post("/events", json=EVENT, headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 202 and r.json()["trigger"] == "event"


def test_with_no_internal_secret_configured_nothing_opens_either_sink(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "")
    runtime = _Runtime()
    client = TestClient(create_app(Dispatcher(load_settings(), runtime, _Identity())))
    assert client.post("/invocations", json=_routine_body(),
                       headers={"X-Internal-Secret": ""}).status_code == 401
    assert client.post("/events", json=EVENT, headers={"X-Internal-Secret": ""}).status_code == 401
    assert runtime.spawned == []


# ── the chat door and routines ───────────────────────────────────────────────────────────────────
def _worker(regime=None, **extra):
    delegation = {"workspaces": ["ws_1"], **extra}
    if regime is not None:
        delegation["regime"] = regime
    return {identity_token.HEADER: identity_token.sign(KEY, {"sub": "7", "delegation": delegation})}


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/submit"])
@pytest.mark.parametrize("regime", ["human", "autonomous", None])
def test_a_worker_starts_no_chat_turn(sink, path, regime):
    client, runtime = sink
    r = client.post(path, json={"prompt": "do it as the person"}, headers=_worker(regime))
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "delegated_dispatch"
    assert runtime.spawned == []


def test_a_person_s_own_signed_identity_still_reaches_the_chat_door(sink):
    client, _ = sink
    r = client.post("/api/chat/submit", json={"prompt": "hi"},
                    headers={identity_token.HEADER: identity_token.sign(KEY, {"sub": "7"})})
    assert r.status_code != 403


@pytest.mark.parametrize("regime", ["autonomous", None])
def test_an_unwatched_worker_arms_no_routine(sink, regime):
    client, runtime = sink
    r = client.post("/api/routines", json={"name": "x", "cron": "0 9 * * *", "prompt": "go",
                                           "run_now": True}, headers=_worker(regime))
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "human_session_required"
    assert runtime.spawned == []
