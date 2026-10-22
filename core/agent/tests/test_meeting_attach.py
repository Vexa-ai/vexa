"""A CHAT LISTENS TO A LIVE CALL (design 2026-10-22, «A chat listening to a live call»).

Meeting 207, «Call with vũ hoàng», 22 Oct 2026: the calendar sent the bot, so no chat had sent it,
the meeting→chat binding was empty, and «Hey, Vexa…» woke three turns in the hidden `meet-207` the
founder never sees. Every live call now has exactly one ATTACHED chat (`meeting_attach`):

(1) the default — the chat that sent the bot, else the owner's most recently active private chat;
(2) a person who can read the meeting moves it to their own chat («Слушать здесь»);
(3) the owner alone sets the call's opt-ins, and a signal turn is told them;
(4) `/listen` names the chat AND its person, so the wake runs as that person;
(5) the chat's grounding follows the live call it listens to, not the meeting it was bound to.

L2: a real FastAPI app over fakes, no redis, no runtime, no claude.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import meeting_attach, meeting_listen
from control_plane.api import create_app
from control_plane.api_shared import _Sessions
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime
from tests.test_chat_meeting_binding import _FakeRedis, _SendingReader

OWNER, MEMBER, STRANGER = "7", "8", "9"
ROW = "207"
SECRET = "s" * 32


class _KvRedis(_FakeRedis):
    """`_FakeRedis` plus the string keys the binding and the reverse pointer use."""

    def __init__(self):
        super().__init__()
        self.kv: dict[str, str] = {}

    def set(self, key, value, **_kw):
        self.kv[key] = value

    def get(self, key):
        return self.kv.get(key)

    def delete(self, key):
        self.kv.pop(key, None)
        super().delete(key)

    def exists(self, key):
        return key in self.hashes or key in self.kv


def _stores():
    """Both backings, every time — the in-memory fallback and the redis shape production runs."""
    out = []
    for redis_client in (None, _KvRedis()):
        sess = _Sessions(redis_client)
        out.append((sess, meeting_attach.MeetingAttachments(redis_client, sess)))
    return out


# ── 1. the store: the default, and the move ──────────────────────────────────────────────────────

def test_a_bot_sent_from_a_chat_attaches_to_that_chat():
    for sess, att in _stores():
        sess.upsert(OWNER, "pchat-newer", title="something else", touched=True)
        sess.bind_meeting_chat(OWNER, ROW, "pchat-sender")
        assert att.resolve_default(OWNER, ROW) == ("pchat-sender", "sent")


def test_a_calendar_bot_attaches_to_the_owners_most_recent_private_chat():
    for sess, att in _stores():
        sess.upsert(OWNER, "pchat-older", title="prep for the 10:15", meeting="208", touched=True)
        sess.upsert(OWNER, "meet-206", title="a meeting's own chat")
        sess.upsert(OWNER, "pchat-newest", title="today", touched=True)
        sess.upsert(OWNER, "routine-x", routine={"flow": "f", "scope": "s", "version": 1})
        assert att.resolve_default(OWNER, ROW) == ("pchat-newest", "auto")


def test_an_owner_with_no_chat_has_no_default():
    for _sess, att in _stores():
        assert att.resolve_default(OWNER, ROW) is None


def test_attaching_binds_the_chat_and_moving_keeps_the_opt_ins():
    for sess, att in _stores():
        att.attach(ROW, OWNER, "pchat-a", how="auto")
        att.set_optins(ROW, speak=True)
        moved = att.attach(ROW, MEMBER, "pchat-b", by=MEMBER, how="chose")
        assert (moved["subject"], moved["session"], moved["how"], moved["speak"]) == \
            (MEMBER, "pchat-b", "chose", True)
        assert sess.meeting_chat(MEMBER, ROW) == "pchat-b"        # the after-call note lands there
        assert att.attached_row(MEMBER, "pchat-b") == ROW
        assert att.attached_row(OWNER, "pchat-a") == ""          # the old chat listens to nothing


# ── 2. the routes ────────────────────────────────────────────────────────────────────────────────

def _world(tmp_path: Path, *, status="active", readers=(OWNER, MEMBER)):
    root = tmp_path / "ws"
    (root / "_global").mkdir(parents=True)
    (root / "_global" / "POLICIES.md").write_text(
        "---\nkind: policies\ntranscript_signals: on\nsignals_wakes_per_meeting: 30\n---\n# Policies\n")
    settings = load_settings(workspaces_dir=str(root), global_system_workspace_path=str(root / "_global"),
                             internal_api_secret=SECRET, ui_url="https://app.example.test", redis_url="")
    rt = _FakeRuntime()
    dispatcher = Dispatcher(settings, rt, _FakeIdentity())
    row = {"id": int(ROW), "user_id": int(OWNER), "status": status, "platform": "google_meet",
           "native_meeting_id": "abc-defg-hij", "data": {"title": "Call with vũ hoàng"}}
    allowed = set(readers)
    lookup = lambda uid, mid, workspaces=None: dict(row) if str(mid) == ROW and str(uid) in allowed else None  # noqa: E731
    sess = _Sessions()
    app = create_app(dispatcher, reader=WorkspaceReader(str(root)), sessions=sess, meeting_owner_lookup=lookup,
                     stream_reader=_SendingReader([{"type": "turn-complete"}]))
    client = TestClient(app, raise_server_exceptions=False)
    return client, sess, app.state.meeting_attachments, rt, dispatcher, allowed


def _as(subject: str) -> dict:
    return {"X-User-Id": subject}


def _listen(client):
    r = client.get(f"/internal/meetings/{ROW}/listen?subject={OWNER}", headers={"X-Internal-Secret": SECRET})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_22_oct_calendar_call_wakes_the_owners_chat_not_the_hidden_one(tmp_path):
    """RED on the tree before the attachment: `chat_session` was `meet-207`."""
    client, sess, _att, *_ = _world(tmp_path)
    sess.upsert(OWNER, "pchat-founder", title="prep for the 10:15 call", meeting="208", touched=True)
    got = _listen(client)
    assert got["chat_session"] != f"meet-{ROW}"
    assert got["chat_session"] == "pchat-founder" and got["chat_subject"] == OWNER
    assert (got["speak"], got["post_chat"]) == (False, False)


def test_the_default_is_a_latch_taken_once_while_the_call_is_live(tmp_path):
    client, sess, att, *_ = _world(tmp_path)
    sess.upsert(OWNER, "pchat-first", touched=True)
    assert _listen(client)["chat_session"] == "pchat-first"
    sess.upsert(OWNER, "pchat-later", touched=True)       # a newer chat does not steal the call
    assert _listen(client)["chat_session"] == "pchat-first"
    assert att.get(ROW)["how"] == "auto"


def test_an_ended_meeting_gets_no_attachment(tmp_path):
    client, sess, att, *_ = _world(tmp_path, status="completed")
    sess.upsert(OWNER, "pchat-any", touched=True)
    assert _listen(client)["chat_session"] == f"meet-{ROW}"
    assert att.get(ROW) is None


def test_a_reader_moves_the_call_to_their_chat_and_the_wake_runs_as_them(tmp_path):
    client, sess, att, *_ = _world(tmp_path)
    sess.upsert(OWNER, "pchat-owner", touched=True)
    _listen(client)
    sess.upsert(MEMBER, "pchat-member", touched=True)
    r = client.post(f"/api/meeting/{ROW}/attachment", json={"session": "pchat-member"}, headers=_as(MEMBER))
    assert r.status_code == 200, r.text
    assert r.json()["attached_here"] is True and r.json()["how"] == "chose"
    got = _listen(client)
    assert (got["chat_session"], got["chat_subject"]) == ("pchat-member", MEMBER)
    # the owner sees where it went, but not another person's chat title or id
    view = client.get(f"/api/meeting/{ROW}/attachment?session=pchat-owner", headers=_as(OWNER)).json()
    assert view["attached"] and not view["attached_here"] and view["chat"] is None
    assert view["said"] == "another person's chat"
    mine = client.get(f"/api/meeting/{ROW}/attachment?session=pchat-member", headers=_as(MEMBER)).json()
    assert mine["attached_here"] and mine["chat"]["session"] == "pchat-member"


def test_a_person_who_cannot_read_the_meeting_sees_and_moves_nothing(tmp_path):
    client, sess, att, *_ = _world(tmp_path)
    sess.upsert(STRANGER, "pchat-stranger", touched=True)
    assert client.get(f"/api/meeting/{ROW}/attachment", headers=_as(STRANGER)).status_code == 404
    r = client.post(f"/api/meeting/{ROW}/attachment", json={"session": "pchat-stranger"}, headers=_as(STRANGER))
    assert r.status_code == 404 and att.get(ROW) is None


def test_a_chat_that_is_not_the_callers_cannot_be_attached(tmp_path):
    client, sess, att, *_ = _world(tmp_path)
    r = client.post(f"/api/meeting/{ROW}/attachment", json={"session": "pchat-nobodys"}, headers=_as(MEMBER))
    assert r.status_code == 404 and att.get(ROW) is None


def test_only_the_owner_sets_the_opt_ins(tmp_path):
    client, sess, att, *_ = _world(tmp_path)
    sess.upsert(OWNER, "pchat-owner", touched=True)
    _listen(client)
    r = client.patch(f"/api/meeting/{ROW}/attachment", json={"speak": True}, headers=_as(MEMBER))
    assert r.status_code == 403 and att.get(ROW)["speak"] is False
    r = client.patch(f"/api/meeting/{ROW}/attachment", json={"speak": True}, headers=_as(OWNER))
    assert r.status_code == 200 and r.json()["speak"] is True and r.json()["post_chat"] is False
    assert _listen(client)["speak"] is True


def test_a_person_who_lost_access_loses_the_call_to_the_owner(tmp_path):
    client, sess, att, _rt, _d, allowed = _world(tmp_path)
    sess.upsert(OWNER, "pchat-owner", touched=True)
    att.attach(ROW, MEMBER, "pchat-member", by=MEMBER, how="chose")
    allowed.discard(MEMBER)
    got = _listen(client)
    assert (got["chat_session"], got["chat_subject"]) == ("pchat-owner", OWNER)


# ── 3. the opt-ins are enforced where the signal turn's tools are decided ────────────────────────

def _wake(client, subject, chat):
    ev = {"name": "meeting.signal", "subject": subject, "target": {"chat": chat},
          "plan": {"prompt": "Someone addressed you in the call."},
          "data": {"meeting_row": ROW, "score": 1.0, "reason": "addressed", "brief_version": 0,
                   "buffer": [{"segment_id": f"{ROW}:1", "speaker": "Vu", "text": "Hey Vexa, say hi",
                               "start": 1.0}]}}
    return client.post("/events", json=ev, headers={"X-Internal-Secret": SECRET})


def _capture(dispatcher):
    seen, real = [], dispatcher.dispatch

    def dispatch(inv, **kw):
        seen.append(inv)
        return real(inv, **kw)
    dispatcher.dispatch = dispatch
    return seen


@pytest.mark.parametrize("speak", [False, True])
def test_a_signal_turn_is_told_the_calls_opt_ins(tmp_path, speak):
    client, sess, att, rt, dispatcher, _ = _world(tmp_path)
    sess.upsert(OWNER, "pchat-owner", touched=True)
    _listen(client)
    att.set_optins(ROW, speak=speak)
    seen = _capture(dispatcher)
    r = _wake(client, OWNER, "pchat-owner")
    assert r.status_code == 202, r.text
    inline = seen[-1]["start"]["entrypoint"]["inline"]
    assert "THIS CALL'S OPT-INS" in inline
    assert ("you may NOT speak in the meeting" in inline) is (not speak)


# ── 4. grounding follows the live call the chat listens to ───────────────────────────────────────

def test_the_chats_grounding_reads_the_live_call_it_listens_to_not_its_bound_meeting(tmp_path, monkeypatch):
    """22 Oct: the founder's chat was bound to meeting 208 and the grounding read 208 while 207
    was the live call talking to it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    from control_plane.routers import chats as chats_mod
    client, sess, att, *_ = _world(tmp_path)
    sess.upsert(OWNER, "pchat-founder", meeting="208", touched=True)
    att.attach(ROW, OWNER, "pchat-founder", how="auto")
    focused = []
    real = chats_mod._context_grounding

    def grounding(body, *a, **kw):
        focus = body.context.focus if body.context is not None else None
        focused.append(json.loads(json.dumps(focus, default=str)) if focus is not None else None)
        return real(body, *a, **kw)
    monkeypatch.setattr(chats_mod, "_context_grounding", grounding)
    client.post("/api/chat", json={"prompt": "what did they just ask?", "session": "pchat-founder"},
                headers=_as(OWNER))
    assert focused and focused[-1]["meeting_id"] == ROW


def test_a_calendar_call_gets_its_page_so_the_agent_can_set_its_rules(tmp_path, monkeypatch):
    """Live check 22 Oct, meeting 210: a bot sent without a chat had no page, and `meeting_listen`
    refused «no page yet», so the agent could not add «wake me when pricing comes up». The attachment
    makes the page, and setting the brief makes it too when nothing has yet."""
    monkeypatch.setenv("VEXA_FEATURES", "all,transcript_signals")
    client, sess, *_ = _world(tmp_path)
    sess.upsert(OWNER, "pchat-founder", touched=True)
    assert meeting_listen.locate(tmp_path / "ws", OWNER, {"id": int(ROW)}) is None
    _listen(client)
    assert meeting_listen.locate(tmp_path / "ws", OWNER, {"id": int(ROW)}) is not None
    r = client.post("/api/meeting/listen", headers=_as(OWNER),
                    json={"meeting": ROW, "brief": {"labels": [{"name": "pricing", "question": "Does someone talk about pricing?", "wake": True}]}})
    assert r.status_code == 200, r.text


def test_setting_the_brief_of_a_meeting_with_no_page_makes_the_page(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,transcript_signals")
    client, *_ = _world(tmp_path, status="completed")
    r = client.post("/api/meeting/listen", headers=_as(OWNER),
                    json={"meeting": ROW, "brief": {"focus": "pricing"}})
    assert r.status_code == 200, r.text
