"""MVP3 event-ingress eval — event.v1 → unit.v1 → the one Dispatcher, TOOL-AGNOSTIC.

Proves: any external event maps to a CONFORMANT unit.v1 dispatch carrying an OPAQUE ref (never the
bytes — a tool resolves it in-container); the same Dispatcher spawns it (agents run in containers, never
in-process); the HTTP front door behaves; and agent-api embeds NO per-tool behaviour (an event with no
plan is a 422, not a hardcoded default).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from jsonschema.exceptions import ValidationError

import contracts
from control_plane import events
from control_plane.api import create_app
from shared.config import load_settings
from control_plane.dispatch import Dispatcher


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


def test_event_with_inline_plan_maps_to_conformant_unit_with_opaque_ref():
    event = {
        "name": "email.received", "subject": "u_jane",
        "source": {"uri": "mailbox://u_jane/INBOX/AB12CD"},
        "plan": {"prompt": "Read the email at the source ref with your email tool and triage it."},
    }
    inv = events.event_to_invocation(event)
    contracts.validate_unit_invocation(inv)
    assert inv["trigger"] == "event"
    assert inv["context"]["kind"] == "generic"
    assert inv["context"]["ref"]["uri"].endswith("AB12CD")   # the OPAQUE ref rides through; no bytes
    assert inv["start"]["entrypoint"]["inline"].startswith("Read the email")
    assert inv["workspaces"] == [{"id": "u_jane", "mode": "ro"}]  # untrusted event ⇒ propose-only (ro)


def test_meeting_completed_maps_to_transcription_trigger():
    event = {
        "name": "meeting.completed", "subject": "u_jane",
        "meeting": {"meeting_id": "m1", "session_uid": "s1"},
        "plan": {"prompt": "Summarize the meeting."},
    }
    inv = events.event_to_invocation(event)
    contracts.validate_unit_invocation(inv)
    assert inv["trigger"] == "transcription"
    assert inv["context"]["kind"] == "meeting"


def test_event_without_a_plan_raises():
    # agent-api embeds NO per-tool default — the source (or a matching event-routine) must carry the plan.
    with pytest.raises(ValueError):
        events.event_to_invocation({"name": "email.received", "subject": "u_jane"})


def test_bad_event_envelope_raises():
    with pytest.raises(ValidationError):
        events.event_to_invocation({"subject": "u_jane"})  # missing name


def test_events_endpoint_dispatches():
    rt = _FakeRuntime()
    dispatcher = Dispatcher(load_settings(), rt, _FakeIdentity())
    client = TestClient(create_app(dispatcher))

    r = client.post("/events", json={
        "name": "email.received", "subject": "u_jane",
        "source": {"uri": "mailbox://u_jane/INBOX/AB12CD"},
        "plan": {"prompt": "triage this"},
    })
    assert r.status_code == 202, r.text
    assert r.json()["trigger"] == "event"
    assert rt.spawned and rt.spawned[0][2]["VEXA_OWNER"] == "u_jane"  # spawned an isolated container

    # No plan → 422 (fail loud), never a silent default.
    assert client.post("/events", json={"name": "x.happened", "subject": "u_jane"}).status_code == 422
