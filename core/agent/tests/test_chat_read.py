"""An agent reads the person's own chats (founder, 2026-10-13): `chats_search` and `chat_read` over
the conversations the speaker can read (ADR-0048 gate 1), the person's words and the agent's replies
from the turn records — never a raw harness transcript. In a shared turn only conversations every
current participant can read; a run with nobody in the loop reads its owner's own chats only; every
read is audited with the session it came from."""
from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from tests._session_fakes import MemBlobs
from control_plane import identity_token
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

KEY = identity_token.generate_signing_key()
REC = ".claude/sessions/conversation.turns.jsonl"


class _Runtime:
    def spawn(self, workload_id, profile, env):
        return workload_id


class _Identity:
    def mint(self, *a, **k):
        return "tok"


@pytest.fixture
def env(tmp_path, monkeypatch):
    public = tmp_path / "pub.pem"
    public.write_bytes(identity_token.public_key_pem(KEY))
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("INTERNAL_API_SECRET", "s")
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("VEXA_WORKSPACES_DIR", str(root))
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    app = create_app(Dispatcher(load_settings(workspaces_dir=str(root), redis_url=""), _Runtime(), _Identity()),
                     reader=WorkspaceReader(str(root)), sessions=_Sessions(), session_store=store)
    yield TestClient(app), store
    store.close()


def _conv(store, owner, title, turns, *, members=()):
    row = store.create(owner, name=None, title=title)
    for m in members:
        store.add_participant(row["id"], owner, m)
    lines = "".join(json.dumps({"key": f"k{i}", "user_text": u, "reply": r}) + "\n" for i, (u, r) in enumerate(turns))
    store.save_bundle(row["id"], 0, {REC: lines.encode()})
    return row["id"]


def _as(sub, **dlg):
    claims = {"sub": sub, "email": f"{sub}@acme.test"}
    if dlg:
        claims["delegation"] = dlg
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


def test_search_finds_my_chats_by_their_words_and_reads_them(env):
    client, store = env
    sid = _conv(store, "ana", "Pricing", [("What did Acme say about pricing?", "They want annual billing.")])
    _conv(store, "ana", "Other", [("hello", "hi")])
    r = client.get("/api/chats/search", params={"query": "annual billing"}, headers=_as("ana"))
    assert r.status_code == 200, r.text
    hits = r.json()["chats"]
    assert [h["session"] for h in hits] == [sid] and "annual billing" in hits[0]["snippet"]
    turns = client.get(f"/api/chats/{sid}/turns", headers=_as("ana")).json()["turns"]
    assert [t["role"] for t in turns] == ["user", "agent"] and turns[1]["text"] == "They want annual billing."


def test_a_chat_i_am_not_in_is_neither_found_nor_read(env):
    client, store = env
    sid = _conv(store, "bob", "Bob's", [("secret plan", "noted")])
    assert client.get("/api/chats/search", params={"query": "secret"}, headers=_as("ana")).json()["chats"] == []
    assert client.get(f"/api/chats/{sid}/turns", headers=_as("ana")).status_code == 404


def test_a_shared_turn_reads_only_chats_every_participant_can_read(env):
    client, store = env
    here = _conv(store, "ana", "Room", [("hi", "hello")], members=["bob"])
    both = _conv(store, "ana", "Both of us", [("budget", "ok")], members=["bob"])
    mine = _conv(store, "ana", "Mine only", [("budget too", "ok")])
    shared = _as("ana", regime="human", workspaces=["team"], shared=True, unit=f"agent-ana-chat-{here}")
    # D3 (0.13.4): chat search and chat reads are person-wide routes, refused to every shared token
    # before the route runs — even a chat every participant could read (`routes.v1.json` shared).
    r = client.get("/api/chats/search", params={"query": "budget"}, headers=shared)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "shared_conversation"
    assert both not in r.text
    for sid in (mine, both):
        r = client.get(f"/api/chats/{sid}/turns", headers=shared)
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "shared_conversation"


def test_a_run_with_nobody_in_the_loop_reads_only_its_owner_s_own_chats(env):
    client, store = env
    owned = _conv(store, "ana", "Mine", [("plan", "x")])
    joined = _conv(store, "bob", "Bob invited me", [("plan b", "y")], members=["ana"])
    routine = _as("ana", regime="autonomous", workspaces=["desk"])
    found = {h["session"] for h in client.get("/api/chats/search", params={"query": "plan"}, headers=routine).json()["chats"]}
    assert found == {owned}
    assert client.get(f"/api/chats/{joined}/turns", headers=routine).status_code == 404


def test_every_read_is_audited_with_its_session(env, caplog):
    client, store = env
    sid = _conv(store, "ana", "A", [("one", "two"), ("three", "four")])
    with caplog.at_level(logging.INFO):
        client.get(f"/api/chats/{sid}/turns", headers=_as("ana", regime="human", workspaces="*", unit="agent-ana-chat-main"))
    rows = [json.loads(r.getMessage()) for r in caplog.records if "chat_read" in r.getMessage()]
    assert rows and rows[-1]["who"] == "ana" and rows[-1]["session"] == sid and rows[-1]["turns"] == 4
    assert rows[-1]["from"] == "agent-ana-chat-main"


# ── defects found in use (frictions 2026-10-13 23:00–23:01) ──────────────────────────────────────

SENT = "<!--vexa:user-input-below-->"


@pytest.fixture
def env2(tmp_path, monkeypatch):
    """`env` plus the chat index (the rail) and the workspaces root, for a chat the store has no
    row for — one recorded in the old layout, before the session store."""
    public = tmp_path / "pub.pem"
    public.write_bytes(identity_token.public_key_pem(KEY))
    monkeypatch.setenv("VEXA_GATEWAY_IDENTITY_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("INTERNAL_API_SECRET", "s")
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("VEXA_WORKSPACES_DIR", str(root))
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    sess = _Sessions()
    app = create_app(Dispatcher(load_settings(workspaces_dir=str(root), redis_url=""), _Runtime(), _Identity()),
                     reader=WorkspaceReader(str(root)), sessions=sess, session_store=store)
    yield TestClient(app), store, sess, root
    store.close()


def _old_layout(root, subject, name, turns, *, title="Old chat"):
    """A chat as the worker kept it before the session store: a pointer, the harness transcript and
    the turn records under the person's private system tier — and its row on the rail."""
    base = root / ".system" / subject / ".claude"
    (base / "sessions").mkdir(parents=True, exist_ok=True)
    (base / "projects" / "-ws").mkdir(parents=True, exist_ok=True)
    (base / "sessions" / f"{name}.session").write_text("abc123")
    lines, recs = [], []
    for composed, said, reply in turns:
        lines.append(json.dumps({"type": "user", "message": {"role": "user", "content": composed}}))
        lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                                  "content": [{"type": "text", "text": reply}]}}))
        if said is not None:
            import hashlib
            recs.append(json.dumps({"key": hashlib.sha256(composed.encode()).hexdigest(), "user_text": said}))
    (base / "projects" / "-ws" / "abc123.jsonl").write_text("\n".join(lines) + "\n")
    if recs:
        (base / "sessions" / f"{name}.turns.jsonl").write_text("\n".join(recs) + "\n")


def test_an_empty_query_lists_the_recent_chats(env):
    """`chats_search` with no words is "what have I been talking about": the newest chats, not a 422."""
    client, store = env
    a = _conv(store, "ana", "First", [("one", "two")])
    b = _conv(store, "ana", "Second", [("three", "four")])
    r = client.get("/api/chats/search", params={"query": ""}, headers=_as("ana"))
    assert r.status_code == 200, r.text
    assert {h["session"] for h in r.json()["chats"]} == {a, b}
    r = client.get("/api/chats/search", headers=_as("ana"))
    assert r.status_code == 200, r.text and len(r.json()["chats"]) == 2


def test_every_chat_the_folders_list_returns_is_readable_by_its_owner(env2):
    """`chat_folders_list` lists the rail, which still carries chats recorded before the session
    store; `chat_read` answered 404 for those. Each row it returns reads back, by the id it gives."""
    client, store, sess, root = env2
    _old_layout(root, "ana", "pchat-old1", [("what did we decide on pricing?", None, "Annual billing.")])
    sess.upsert("ana", "pchat-old1", title="Pricing")
    listed = client.get("/api/chat/folders", headers=_as("ana")).json()["chats"]
    rows = [c for c in listed if c["row"] == "c:pchat-old1"]
    assert rows, listed
    for ref in ("pchat-old1", rows[0]["row"], rows[0].get("session") or "pchat-old1"):
        r = client.get(f"/api/chats/{ref}/turns", headers=_as("ana"))
        assert r.status_code == 200, (ref, r.text)
        assert [t["text"] for t in r.json()["turns"]] == ["what did we decide on pricing?", "Annual billing."]
    found = client.get("/api/chats/search", params={"query": "annual"}, headers=_as("ana")).json()["chats"]
    assert [h["session"] for h in found] == ["pchat-old1"]


def test_an_old_layout_chat_is_nobody_else_s_and_not_for_a_shared_turn(env2):
    client, store, sess, root = env2
    _old_layout(root, "ana", "pchat-old2", [("private words", None, "ok")])
    sess.upsert("ana", "pchat-old2", title="Mine")
    assert client.get("/api/chats/pchat-old2/turns", headers=_as("bob")).status_code == 404
    here = _conv(store, "ana", "Room", [("hi", "hello")], members=["bob"])
    shared = _as("ana", regime="human", workspaces=["team"], shared=True, unit=f"agent-ana-chat-{here}")
    r = client.get("/api/chats/pchat-old2/turns", headers=shared)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "shared_conversation"
    r = client.get("/api/chats/search", params={"query": "private"}, headers=shared)
    assert r.status_code == 403 and "private words" not in r.text


def test_a_turn_reads_as_the_person_s_words_never_the_context_line(env2):
    """The terminal folds "Active context: …" and a sentinel in front of what the person typed; a read
    turn carries the person's words only — from the records, and from a transcript with no record."""
    client, store, sess, root = env2
    sid = _conv(store, "ana", "Ctx", [(f"Active context: the user is viewing README.md. --- {SENT}hi there", "hello")])
    assert client.get(f"/api/chats/{sid}/turns", headers=_as("ana")).json()["turns"][0]["text"] == "hi there"
    _old_layout(root, "ana", "pchat-ctx", [
        (f"Active context: the user is viewing kg/x.md.\n\n---\n{SENT}what is due?", None, "Nothing."),
        ("Active context: the user is viewing kg/y.md.\n\n---\nand tomorrow?", None, "Two calls.")])
    sess.upsert("ana", "pchat-ctx", title="Ctx")
    turns = client.get("/api/chats/pchat-ctx/turns", headers=_as("ana")).json()["turns"]
    assert [t["text"] for t in turns if t["role"] == "user"] == ["what is due?", "and tomorrow?"]


# -- a ROOM's own agent reads ITS OWN room (app.dev 2026-10-16) --------------------------------------
# The room's turn runs as the workspace's agent (`agent-ws-<w>-room-<session>[-signal]`), shared and
# unwatched. It answered "I can't read the full history of this room" — chat_read only knew
# `agent-<subject>-chat-<session>` units, so the room's own history read as someone else's. The room's
# agent reads its own room: the turns, the people's messages, and the room's call transcript; any
# other room stays under the leak rule.


def _room(store, owner, title, members, notes=()):
    sid = _conv(store, owner, title, [("earlier question", "earlier answer")], members=members)
    store.put_room(sid, visibility="closed", workspace="team")
    for who, text in notes:
        store.add_note(sid, who, text)
    return sid


def test_a_room_turn_reads_its_own_room_and_never_another(env):
    """int-s1 (0.13.4): `chat_read` is `shared: allow` so a room's turn reads its OWN room — its
    turns and its people's messages — and every other conversation stays refused to a shared token
    (`shared_conversation`)."""
    client, store = env
    here = _room(store, "ana", "Pilot room", ["bob"], notes=[("bob", "We agreed the pilot starts on the 3rd.")])
    other = _room(store, "ana", "Board room", ["carol"], notes=[("carol", "Salary bands are confidential.")])
    turn = _as("ana", regime="autonomous", workspaces=["team"], shared=True,
               unit=f"agent-ws-team-room-{here}-signal")
    r = client.get(f"/api/chats/{here}/turns", headers=turn)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [t["text"] for t in body["turns"]] == ["earlier question", "earlier answer"]
    assert [(m["author"], m["text"]) for m in body["messages"]] == [("bob", "We agreed the pilot starts on the 3rd.")]
    r = client.get(f"/api/chats/{other}/turns", headers=turn)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "shared_conversation", r.text
    assert "Salary" not in r.text


def test_a_quiet_signal_reply_is_never_read_back_as_a_line():
    # LIVE app.dev 2026-10-18: the turn record of a signal wake that chose silence holds
    # `reply: "[vexa-quiet]"`. The marker is machinery: an agent reading the chat never sees it.
    from control_plane.routers.chat_read import _turns_of
    from shared.marks import QUIET_REPLY

    lines = (json.dumps({"key": "k0", "user_text": "From the meeting", "reply": QUIET_REPLY}) + "\n"
             + json.dumps({"key": "k1", "user_text": "and the plan?", "reply": f"The plan stands. {QUIET_REPLY}"}) + "\n")
    turns = _turns_of({REC: lines.encode()})
    assert QUIET_REPLY not in json.dumps(turns)
    assert [t["text"] for t in turns if t["role"] == "agent"] == ["The plan stands."]
