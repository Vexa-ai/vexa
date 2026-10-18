"""Who may hand agent-api a turn to run — the dispatch sinks and the chat door.

`POST /invocations` and `POST /events` take a body that names the person the turn runs as, so the
CALLER is authenticated: the internal tier, or (for `/invocations`) a routine job agent-api signed
when it compiled it (`control_plane/dispatch_sink.py`). Nobody else is heard, and a signed job never
asks for a person in the loop. A chat turn (`/api/chat`, `/api/chat/submit`) is started by a person,
never by a worker acting for one, and an unwatched worker does not arm a routine.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from control_plane import dispatch_sink, identity_token
from control_plane import routines as routines_mod
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_routines import reconcile_workspace_routines
from shared.config import load_settings
from tests.test_routines import _FakeScheduler

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
    routine = routines_mod.make_routine(subject=subject, name="daily", cron="0 9 * * *", prompt="go")
    body = routines_mod.build_invocation(routine)
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


# ── the signature ────────────────────────────────────────────────────────────────────────────────
def test_a_signature_binds_the_whole_body_and_survives_a_json_round_trip():
    body = _routine_body()
    sig = dispatch_sink.sign(SECRET, body)
    assert dispatch_sink.verify(SECRET, json.loads(json.dumps(body, indent=2)), sig)
    assert dispatch_sink.verify(SECRET, dict(reversed(list(body.items()))), sig)
    for changed in ({**body, "trigger": "message"},
                    {**body, "identity": {**body["identity"], "subject": "u_admin"}},
                    {**body, "start": {"entrypoint": {"inline": "something else"}}}):
        assert not dispatch_sink.verify(SECRET, changed, sig)
    assert not dispatch_sink.verify("another-secret", body, sig)
    assert not dispatch_sink.verify("", body, sig)
    assert not dispatch_sink.verify(SECRET, body, "")
    assert not dispatch_sink.verify(SECRET, body, "v1=é")
    assert SECRET not in sig
    with pytest.raises(ValueError):
        dispatch_sink.sign("", body)


def test_a_compiled_routine_carries_its_signature_and_only_when_a_secret_is_configured():
    routine = routines_mod.make_routine(subject="u_jane", name="daily", cron="0 9 * * *", prompt="go")
    job = routines_mod.compile_to_job(routine, invocations_url="http://x/invocations",
                                      signing_secret=SECRET)
    stored = json.loads(json.dumps(job))  # what the scheduler keeps and fires back
    assert dispatch_sink.verify(SECRET, stored["request"]["body"],
                                stored["request"]["headers"][dispatch_sink.HEADER])
    assert "headers" not in routines_mod.compile_to_job(routine, invocations_url="http://x/invocations")["request"]


def test_a_routine_armed_before_signing_is_re_armed_signed(tmp_path):
    p = tmp_path / "u_live" / "routines" / "beat.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\nenabled: true\ncron: '* * * * *'\nprompt: hi\n---\n")
    scheduler = _FakeScheduler()
    reconcile_workspace_routines("u_live", scheduler=scheduler, invocations_url="http://x/invocations",
                                 workspaces_dir=tmp_path)
    assert "headers" not in scheduler.jobs[-1]["request"]
    again = reconcile_workspace_routines("u_live", scheduler=scheduler,
                                         invocations_url="http://x/invocations",
                                         workspaces_dir=tmp_path, signing_secret=SECRET)
    assert again.scheduled == 1 and again.cancelled == 1
    live = [j for j in scheduler.jobs if j.get("status") != "cancelled"]
    assert dispatch_sink.HEADER in live[-1]["request"]["headers"]


# ── /invocations ─────────────────────────────────────────────────────────────────────────────────
def test_a_caller_with_no_credential_is_refused_and_nothing_spawns(sink):
    client, runtime = sink
    for headers in ({}, {"X-Internal-Secret": "wrong"}, {dispatch_sink.HEADER: "v1=00"},
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


def test_a_signed_routine_job_dispatches_and_only_as_itself(sink):
    client, runtime = sink
    body = _routine_body()
    sig = {dispatch_sink.HEADER: dispatch_sink.sign(SECRET, body)}
    assert client.post("/invocations", json=body, headers=sig).status_code == 202
    other = {**body, "identity": {**body["identity"], "subject": "u_admin"}}
    assert client.post("/invocations", json=other, headers=sig).status_code == 401
    assert len(runtime.spawned) == 1


def test_a_signed_job_never_runs_with_a_person_in_the_loop(sink):
    client, runtime = sink
    body = _routine_body(trigger="message")
    r = client.post("/invocations", json=body,
                    headers={dispatch_sink.HEADER: dispatch_sink.sign(SECRET, body)})
    assert r.status_code == 403
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
                       headers={dispatch_sink.HEADER: "v1=00"}).status_code == 401
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
