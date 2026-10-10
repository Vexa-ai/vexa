"""A chat folds a meeting's transcript only when its caller may read that meeting.

The meeting a chat is grounded in arrives from the client (`context.focus` / legacy `active`), and
the fold reads `tc:meeting:{row id}` straight out of redis. So the row id is checked with the SAME
access decision the live transcript stream makes — owner, transcript-share recipient, or member of
the meeting's bound workspace — before anything is folded. A caller who is none of those gets a
plain turn with no meeting in it.

Offline: a real FastAPI app over fakes, fakeredis for the transcript stream, no meeting-api.
"""
from __future__ import annotations

import json

import fakeredis
import pytest
from fastapi.testclient import TestClient

from control_plane.api import ChatBody, ChatContextBody, _context_grounding, create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

OWNER, MEMBER, STRANGER = "401", "402", "403"
WS, ROW = "team-notes", "7001"
SECRET_LINE = "the acquisition closes on Friday"


def _seeded_redis():
    r = fakeredis.FakeRedis(decode_responses=True)
    r.xadd(f"tc:meeting:{ROW}", {"payload": json.dumps({"type": "transcription", "segments": [
        {"segment_id": "s1", "speaker": "Ana", "text": SECRET_LINE}]})})
    return r


@pytest.fixture
def fake_redis(monkeypatch):
    import redis
    r = _seeded_redis()
    monkeypatch.setattr(redis, "from_url", lambda *a, **k: r)
    return r


def _row(status="completed"):
    return {"id": int(ROW), "status": status, "platform": "google_meet",
            "native_meeting_id": "abc-defg-hij", "user_id": OWNER,
            "data": {"title": "Board prep", "workspace_id": WS}}


def _lookup(user_id, meeting_id, workspaces=None):
    """meeting-api's access union in miniature: the owner, or a member of the bound workspace."""
    if str(meeting_id) != ROW:
        return None
    if str(user_id) == OWNER or WS in (workspaces or []):
        return _row()
    return None


def _focus(status="completed", meeting_id=ROW):
    return {"kind": "meeting", "native_id": "abc-defg-hij", "meeting_id": meeting_id,
            "platform": "google_meet", "status": status}


def _body(focus):
    return ChatBody(prompt="what was decided?", context=ChatContextBody(focus=focus))


def _ground(focus, access):
    return _context_grounding(_body(focus), "s1", "redis://fake", schedule_rows=lambda: [],
                              workspace_mounts=lambda: [], meeting_access=access)[2]


# ── the grounding function ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["completed", "active", ""])
def test_a_meeting_the_caller_cannot_read_folds_no_transcript(fake_redis, status):
    prompt = _ground(_focus(status), lambda mid: None)
    assert SECRET_LINE not in prompt
    assert prompt == "what was decided?"


@pytest.mark.parametrize("status", ["completed", "active"])
def test_a_meeting_the_caller_may_read_folds_its_transcript(fake_redis, status):
    prompt = _ground(_focus(status), lambda mid: _row() if mid == ROW else None)
    assert SECRET_LINE in prompt
    assert prompt.endswith("what was decided?")


def test_without_an_access_check_no_transcript_is_folded(fake_redis):
    prompt = _context_grounding(_body(_focus()), "s1", "redis://fake", schedule_rows=lambda: [],
                                workspace_mounts=lambda: [])[2]
    assert SECRET_LINE not in prompt


def test_a_row_id_that_is_not_a_number_is_refused_before_the_check(fake_redis):
    seen = []
    prompt = _ground(_focus(meeting_id="tc:meeting:7001"), lambda mid: seen.append(mid) or _row())
    assert seen == [] and SECRET_LINE not in prompt


def test_an_access_check_that_raises_folds_nothing(fake_redis):
    def boom(mid):
        raise RuntimeError("meeting-api down")
    assert SECRET_LINE not in _ground(_focus(), boom)


def test_the_checked_row_decides_the_phase_not_the_client(fake_redis):
    """A client claiming a planned meeting is live still gets only what the server row says."""
    planned = {**_row(status="scheduled")}
    prompt = _ground(_focus(status="active"), lambda mid: planned)
    assert SECRET_LINE not in prompt
    assert "PREPARE" in prompt


def test_the_legacy_native_slot_is_checked_like_a_row_id(fake_redis):
    focus = {"kind": "meeting", "native_id": ROW, "platform": "google_meet", "status": "completed"}
    assert SECRET_LINE not in _ground(focus, lambda mid: None)
    assert SECRET_LINE in _ground(focus, lambda mid: _row() if mid == ROW else None)


# ── the /api/chat route ──────────────────────────────────────────────────────────────────────────

class _Runtime:
    def __init__(self):
        self.envs: list[dict] = []

    def spawn(self, workload_id, profile, env):
        self.envs.append(env)
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _Identity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _Reader:
    def read(self, unit_id, *, resume=None):
        yield {"type": "turn-complete"}


@pytest.fixture
def stack(tmp_path, monkeypatch, fake_redis):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    for uid in (OWNER, MEMBER, STRANGER):
        (tmp_path / uid).mkdir(parents=True)
    (tmp_path / "_global").mkdir()
    ws = tmp_path / WS
    (ws / "policy").mkdir(parents=True)
    (ws / "policy" / "members.json").write_text(json.dumps([
        {"subject": OWNER, "role": "owner", "email": "owner@example.test"},
        {"subject": MEMBER, "role": "reader", "email": "member@example.test"},
    ]))
    runtime = _Runtime()
    settings = load_settings(workspaces_dir=str(tmp_path),
                             global_system_workspace_path=str(tmp_path / "_global"),
                             internal_api_secret="s", redis_url="")
    client = TestClient(create_app(Dispatcher(settings, runtime, _Identity()), stream_reader=_Reader(),
                                   reader=WorkspaceReader(str(tmp_path)), meeting_owner_lookup=_lookup,
                                   schedule_source=lambda subject: [], redis_url="redis://fake"))
    return client, runtime


def _sent(runtime) -> str:
    return json.loads(runtime.envs[-1]["VEXA_START"])["entrypoint"]["inline"]


def _chat(client, subject, session):
    return client.post("/api/chat", headers={"X-User-Id": subject}, json={
        "prompt": "what was decided?", "session": session, "context": {"focus": _focus()}})


def test_chat_route_folds_another_owners_meeting_for_nobody_else(stack):
    client, runtime = stack
    r = _chat(client, STRANGER, "s-stranger")
    assert r.status_code == 200
    assert SECRET_LINE not in _sent(runtime)


def test_chat_route_folds_the_meeting_for_its_owner_and_workspace_members(stack):
    client, runtime = stack
    for uid in (OWNER, MEMBER):
        r = _chat(client, uid, f"s-{uid}")
        assert r.status_code == 200
        assert SECRET_LINE in _sent(runtime), f"{uid} lost the grounding they are entitled to"
