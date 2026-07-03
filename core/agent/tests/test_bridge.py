"""O-AG-3 — the meeting WS → agent bridge (invoke.v1).

Publishes ``transcript.v1`` goldens onto a fake bus → the bridge validates them at the meetings⊥agent
seam → on the ``meeting.completed`` trigger it spawns the ``agent`` workload via ``FakeRuntime`` with
the runtime.v1 env (per spec-agent.json). A non-conformant payload is DROPPED at the seam. The
``invoke.v1`` goldens themselves conform.

The transcript.v1 goldens are loaded BY PATH — the same ``meetings ⊥ agent`` boundary the production
bridge keeps (no meetings import).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import contracts
from control_plane.bridge import TranscriptBridge
from shared.config import load_settings

from .fakes import FakeBus, FakeRuntime

WORKSPACE_REPO = "https://git.example.com/acme/company-memory.git"
TOKEN = "scoped-jwt-token"


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "meetings/contracts/transcript.v1/golden").is_dir():
            return parent
    raise FileNotFoundError("monorepo root not found")


def _transcript_golden(name: str) -> dict:
    return json.loads((_repo_root() / "meetings/contracts/transcript.v1/golden" / name).read_text())


def _invoke_golden(name: str) -> dict:
    return json.loads((_repo_root() / "agent/contracts/invoke.v1/golden" / name).read_text())


def _bridge(runtime: FakeRuntime) -> TranscriptBridge:
    settings = load_settings(agent_identity_token=TOKEN, workspace_ref="main")
    return TranscriptBridge(settings, runtime, resolver=lambda meeting_id: WORKSPACE_REPO)


def test_session_end_golden_triggers_agent_spawn_with_correct_env():
    runtime = FakeRuntime()
    bridge = _bridge(runtime)
    bus = FakeBus([_transcript_golden("SessionEnd.basic.json")])

    spawned = bridge.run_once(bus)

    # the bridge spawned exactly one agent workload
    assert len(runtime.spawned) == 1
    workload_id, profile, env = runtime.spawned[0]
    assert spawned == [workload_id]
    assert profile == "agent"
    # the runtime.v1 env matches spec-agent.json: workspace repo + scoped identity token + ref/path
    assert env == {
        "VEXA_AGENT_IDENTITY_TOKEN": TOKEN,
        "VEXA_WORKSPACE_REPO": WORKSPACE_REPO,
        "VEXA_WORKSPACE_REF": "main",
        "VEXA_WORKSPACE_PATH": "/workspace",
    }
    # an invoke.v1-conformant Invocation fired the spawn
    assert len(bridge.invoked) == 1
    contracts.validate_invocation(bridge.invoked[0])
    assert bridge.invoked[0]["on"] == "meeting.completed"


def test_transcription_golden_validates_but_does_not_trigger():
    """A Transcription batch is content flowing through — validated, but not itself a trigger."""
    runtime = FakeRuntime()
    bridge = _bridge(runtime)
    bus = FakeBus([_transcript_golden("Transcription.confirmed.json")])

    spawned = bridge.run_once(bus)

    assert spawned == []
    assert runtime.spawned == []       # no spawn
    assert bridge.dropped == []        # but NOT dropped — it conformed, just isn't a trigger


def test_non_conformant_payload_is_dropped_at_the_seam():
    """A payload that violates transcript.v1 never reaches the trigger logic or the runtime."""
    runtime = FakeRuntime()
    bridge = _bridge(runtime)
    # claims to be a transcription but is missing required fields → rejected at the seam
    bad = {"type": "transcription", "segments": [{"speaker": "x"}]}
    bus = FakeBus([bad])

    spawned = bridge.run_once(bus)

    assert spawned == []
    assert runtime.spawned == []       # the seam stopped it before any spawn
    assert bridge.dropped == [bad]


def test_unknown_type_is_dropped_not_spawned():
    runtime = FakeRuntime()
    bridge = _bridge(runtime)
    bus = FakeBus([{"type": "not_a_transcript_thing"}])
    bridge.run_once(bus)
    assert runtime.spawned == []
    assert len(bridge.dropped) == 1


@pytest.mark.parametrize(
    "name",
    [
        "Invocation.meeting-completed.json",
        "Invocation.chat-invocation.json",
        "Invocation.scheduled.json",
    ],
)
def test_invoke_v1_goldens_conform(name):
    contracts.validate_invocation(_invoke_golden(name))


def test_mixed_stream_spawns_only_on_trigger():
    """A realistic stream: a Transcription then a SessionEnd → exactly one spawn, on the SessionEnd."""
    runtime = FakeRuntime()
    bridge = _bridge(runtime)
    bus = FakeBus([
        _transcript_golden("Transcription.confirmed.json"),
        _transcript_golden("SessionEnd.basic.json"),
    ])
    spawned = bridge.run_once(bus)
    assert len(spawned) == 1
    assert len(runtime.spawned) == 1
