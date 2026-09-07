"""A CHAT THAT CREATES A MEETING BECOMES THAT MEETING'S CHAT (Vexa-ai/vexa#1597).

Founder, 2026-09-06, in a live Google Meet he had started FROM a chat — the chat sent the bot, the
transcript canvas opened beside it, *"which is fantastic"* — and then:

    *"i seem to have closed the transcript and now can't find one, if chat is a specific meeting —
    and that's a chat feature that it gets after creating meeting from itself — this transcript
    should be pinned. and the chat itself should be Live (left sidebar), while there is no need to
    create a new chat for that — we already have meeting owner, just attach the status to it"*

His rail had TWO rows for one meeting: the conversation that sent the bot, and an auto-created
`Google Meet · cqb-egsq… live` row beside it. The reason is one missing fact and it is a fact only
this service ever holds. The terminal names a meeting's own session `meet-<row>` and reads the ref
back off that id (#1591), which answers for the meeting somebody OPENED from the rail and answers
nothing at all for the chat that MADE one — that chat has an ordinary `pchat-…` id.

So the binding is written here, on the turn's own event stream, where the subject, the session and
the send's result are all in hand at once. The client half — the rail's dedup, the `live` status,
the pinned transcript and the standing chips — is pinned in
`clients/terminal/src/minutes/__tests__/chatMeeting.test.ts`.

L2: a real FastAPI app over fakes, no redis, no runtime, no claude.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from control_plane.api import _Sessions, create_app
from control_plane.api_shared import meeting_binding
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from llm.claude_code import _bot_artifact
from shared.config import load_settings


# ── 1. what the send says, and what it means ─────────────────────────────────────────────────────

def _result(payload):
    return json.dumps(payload)


def test_a_successful_send_carries_the_row_AND_the_native_id():
    """The row is how the panel addresses a meeting; the native id is how meeting-api does. The
    binding is the one record that has to answer both, so the send hands over both."""
    ev = _bot_artifact(_result({"sent": True, "platform": "google_meet",
                                "meeting": "cqb-egsq-vmt", "meeting_row": 118}))
    assert ev == {"type": "artifact", "path": "meeting:118", "pin": True, "focus": True,
                  "native": "cqb-egsq-vmt"}
    # the panel move it always earned is untouched — pin KEEPS the transcript, focus FRONTS it
    assert ev["pin"] is True and ev["focus"] is True


def test_a_send_that_resolved_no_native_still_binds_by_row():
    ev = _bot_artifact(_result({"sent": True, "meeting_row": "118"}))
    assert ev["path"] == "meeting:118" and "native" not in ev
    assert meeting_binding(ev) == ("118", "")


def test_the_artifact_naming_a_meeting_is_the_binding_and_nothing_else_is():
    assert meeting_binding({"type": "artifact", "path": "meeting:118", "native": "cqb"}) == ("118", "cqb")
    # a file the turn wrote is a file, however the panel treats it
    assert meeting_binding({"type": "artifact", "path": "kg/entities/person/ada.md"}) is None
    # …and an OPEN is a person asking to LOOK at a meeting. Same `meeting:<row>` dialect, different
    # act: reading somebody's transcript must never take that meeting's identity for this chat.
    assert meeting_binding({"type": "open", "path": "meeting:118"}) is None
    assert meeting_binding({"type": "message-delta", "text": "meeting:118"}) is None


def test_a_binding_aimed_at_a_guess_is_refused():
    # `meeting:` with nothing after it, and a "row" that is really a path. Same refusal the client's
    # own `pageForArtifact` makes — a chat put permanently in a room that does not exist is worse
    # than a chat that was never bound.
    assert meeting_binding({"type": "artifact", "path": "meeting:"}) is None
    assert meeting_binding({"type": "artifact", "path": "meeting:118/../9"}) is None
    assert meeting_binding("not an event") is None


# ── 2. the index holds it, once ──────────────────────────────────────────────────────────────────

class _FakeRedis:
    """The three primitives `_Sessions` uses, and nothing else."""

    def __init__(self):
        self.hashes: dict[str, dict] = {}
        self.sets: dict[str, set] = {}

    def hset(self, key, mapping=None, **kw):
        self.hashes.setdefault(key, {}).update(mapping or {})

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def delete(self, key):
        self.hashes.pop(key, None)


def _index_cases():
    """Both backings, every time — the in-memory fallback the unit tests run on and the redis hash
    production runs on. A field that lands in one and not the other is a binding that works on a
    developer's laptop only."""
    return [_Sessions(), _Sessions(_FakeRedis())]


def test_the_chat_that_sent_the_bot_carries_the_meeting():
    for sess in _index_cases():
        sess.upsert("u1", "pchat-abc", title="send a bot to https://meet.google.com/cqb-egsq-vmt")
        sess.upsert("u1", "pchat-abc", meeting="118", meeting_native="cqb-egsq-vmt")
        row = sess.list("u1")[0]
        assert row["meeting"] == "118" and row["meeting_native"] == "cqb-egsq-vmt"
        # …and it is still the same chat: nothing about its name or its history moved
        assert row["session"] == "pchat-abc"
        assert row["title"] == "send a bot to https://meet.google.com/cqb-egsq-vmt"


def test_a_session_nobody_bound_answers_null_rather_than_going_quiet():
    for sess in _index_cases():
        sess.upsert("u1", "pchat-abc", title="Plan the launch")
        assert sess.list("u1")[0]["meeting"] is None
        assert sess.list("u1")[0]["meeting_native"] is None


def test_the_binding_is_a_LATCH_because_the_chat_IS_its_meeting():
    """A second send in the same conversation is a second meeting, and a second meeting is a second
    chat. Letting it overwrite would move the room, the pinned transcript and the note out from
    under a reader who is looking at them."""
    for sess in _index_cases():
        sess.upsert("u1", "pchat-abc", meeting="118", meeting_native="cqb-egsq-vmt")
        sess.upsert("u1", "pchat-abc", meeting="119", meeting_native="zzz-zzzz-zzz")
        row = sess.list("u1")[0]
        assert row["meeting"] == "118" and row["meeting_native"] == "cqb-egsq-vmt"


def test_binding_does_not_disturb_what_the_rail_already_reads():
    for sess in _index_cases():
        sess.upsert("u1", "s", title="First prompt", workspaces=["personal"],
                    scaffold={"kind": "first-visit", "id": "SC1"}, touched=True)
        sess.upsert("u1", "s", meeting="118")
        row = sess.list("u1")[0]
        assert row["title"] == "First prompt" and row["workspaces"] == ["personal"]
        assert row["scaffold"] == {"kind": "first-visit", "id": "SC1"} and row["touched"] is True


def test_a_row_older_than_the_field_simply_has_no_meeting():
    r = _FakeRedis()
    r.hashes["agent:session:u1:legacy"] = {"created": "1.0", "last_active": "2.0", "title": "old"}
    r.sets["agent:sessions:u1"] = {"legacy"}
    assert _Sessions(r).list("u1")[0]["meeting"] is None


# ── 3. the turn's own stream writes it ───────────────────────────────────────────────────────────

INTERNAL = "internal-tier-secret-for-tests"

SEND_TURN = [
    {"type": "message-delta", "text": "Sending the bot."},
    {"type": "artifact", "path": "meeting:118", "pin": True, "focus": True, "native": "cqb-egsq-vmt"},
    {"type": "turn-complete"},
]


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _SendingReader:
    """A worker whose turn sends a bot — the events agent-api relays, verbatim."""

    def __init__(self, events):
        self.events = events

    def read(self, unit_id, resume=None):
        yield from self.events


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    return {"root": root, "sessions": _Sessions()}


def _client(stack, events):
    settings = load_settings(
        workspaces_dir=str(stack["root"]),
        global_system_workspace_path=str(stack["root"] / "_global"),
        internal_api_secret=INTERNAL,
        ui_url="https://app.example.test",
        redis_url="",
    )
    app = create_app(Dispatcher(settings, _FakeRuntime(), _FakeIdentity()),
                     stream_reader=_SendingReader(events),
                     reader=WorkspaceReader(str(stack["root"])),
                     sessions=stack["sessions"])
    return TestClient(app)


def _turn(client, session, prompt="send a bot to https://meet.google.com/cqb-egsq-vmt"):
    return client.post("/api/chat", json={"prompt": prompt, "session": session},
                       headers={"X-User-Id": "u_priya"})


def test_a_turn_that_sends_a_bot_binds_the_meeting_to_THAT_chats_session(stack):
    client = _client(stack, SEND_TURN)
    assert _turn(client, "pchat-abc").status_code == 200
    rows = client.get("/api/sessions", headers={"X-User-Id": "u_priya"}).json()["sessions"]
    assert len(rows) == 1, "one meeting is one chat — there is no second row to make"
    assert rows[0]["session"] == "pchat-abc"
    assert rows[0]["meeting"] == "118" and rows[0]["meeting_native"] == "cqb-egsq-vmt"


def test_the_client_still_gets_every_event_it_always_got(stack):
    """A READ, not a reroute. The client binds off this same event for the render it is doing now;
    the index write is what makes the binding outlive this browser."""
    client = _client(stack, SEND_TURN)
    body = _turn(client, "pchat-abc").text
    assert '"path": "meeting:118"' in body
    assert '"pin": true' in body and '"focus": true' in body
    assert "Sending the bot." in body and "turn-complete" in body


def test_a_turn_that_sends_nothing_binds_nothing(stack):
    client = _client(stack, [{"type": "message-delta", "text": "hello"}, {"type": "turn-complete"}])
    assert _turn(client, "pchat-abc", prompt="hello").status_code == 200
    rows = client.get("/api/sessions", headers={"X-User-Id": "u_priya"}).json()["sessions"]
    assert rows[0]["meeting"] is None


def test_the_binding_lands_on_the_session_that_SENT_never_on_whatever_is_in_front(stack):
    """The session id comes off THIS request, so two conversations open at once cannot cross: the
    chat that sent the bot is bound and the one beside it is untouched."""
    client = _client(stack, SEND_TURN)
    _turn(client, "pchat-sender")
    quiet = _client(stack, [{"type": "turn-complete"}])
    _turn(quiet, "pchat-bystander", prompt="something else")
    rows = {r["session"]: r for r in
            client.get("/api/sessions", headers={"X-User-Id": "u_priya"}).json()["sessions"]}
    assert rows["pchat-sender"]["meeting"] == "118"
    assert rows["pchat-bystander"]["meeting"] is None


def test_an_index_that_refuses_the_write_does_not_cost_the_person_their_turn(stack):
    """The binding is furniture; the turn is what they are waiting for."""
    class _Broken(_Sessions):
        def upsert(self, subject, session, **kw):
            if kw.get("meeting"):
                raise RuntimeError("index down")
            return super().upsert(subject, session, **kw)

    stack["sessions"] = _Broken()
    client = _client(stack, SEND_TURN)
    r = _turn(client, "pchat-abc")
    assert r.status_code == 200
    assert '"path": "meeting:118"' in r.text and "turn-complete" in r.text
