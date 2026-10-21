"""Deny vectors: adding people with an @-mention, and sharing by one sentence (ADR-0048).

- The composer's picker shows only people the caller shares a group workspace with
  (``/api/people/mentionable``); a person outside that set is never named, and adding one by id
  creates nothing (the same answer an unknown address gets).
- Adding by mention obeys the participant rules the header control did: only the owner adds, a
  private chat becomes shared by forking (S-H2), and a worker without a person in the loop is
  refused.
- A routine's chat is shared like any other: the shared chat takes the routine's binding and thread
  name, so its runs land there and are SHARED turns; the confirmation names the private reads it
  would lose; a routine over a shared workspace keeps everything it reads.
- ``workspace_member_add``: only the workspace's owner adds; an id must be one of the owner's
  mentionable people; nobody without an account is added.
- One sentence ("share acme with @joe and share this chat with him") is both shares, with the
  account id the mention carried.

Pure unit tests: FastAPI over a sqlite session store, real git workspaces on disk, fakes; no network.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import people_directory, session_fork, session_sync
from control_plane import workspace_membership as m
from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.session_store import SessionStore
from control_plane.session_turn import plan_turn
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from control_plane.identity_token import DELEGATION_HEADERS
from tests._session_fakes import MemBlobs

ALICE, JOE, DAVE = "u_alice", "u_joe", "u_dave"
MAIL = {ALICE: "alice@acme.test", JOE: "joe@acme.test", DAVE: "dave@other.test"}
SECRET = "internal-s"


class _FakeRuntime:
    def spawn(self, workload_id, profile, env):
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


class _FakeReader:
    def read(self, unit_id, resume=None, fresh=False):
        yield {"type": "message-delta", "text": "ok"}
        yield {"type": "turn-complete"}


def _git(work: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True, check=True)


def _ws(root: Path, slug: str, owner: str, idx, *members: tuple[str, str]) -> None:
    ws = root / slug
    ws.mkdir(parents=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t")
    _git(ws, "config", "user.name", "t")
    (ws / "README.md").write_text("hi\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "seed")
    m.ensure_owner(root, slug, owner, index=idx, email=MAIL[owner], commit_fn=m.policy_commit)
    for who, role in members:
        m.grant_membership(root, slug, who, role, added_by=owner, index=idx, email=MAIL[who])


def _gmail_routine_bundle(prompt: str, tool: "str | None") -> dict:
    lines = [{"type": "user", "message": {"content": prompt}}]
    if tool:
        lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": tool, "input": {"q": "is:unread"}}]}})
        lines.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "PRIVATE-MAIL-4410"}]}})
    lines.append({"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}})
    rec = {"key": hashlib.sha256(prompt.encode()).hexdigest(), "user_text": prompt}
    return {
        ".claude/sessions/conversation.session": b"sid-r\n",
        ".claude/sessions/conversation.turns.jsonl": (json.dumps(rec) + "\n").encode(),
        ".claude/projects/-w/sid-r.jsonl": "\n".join(json.dumps(x) for x in lines).encode(),
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "ws"
    root.mkdir()
    idx = m.InMemoryMembershipIndex()
    _ws(root, "acme", ALICE, idx, (JOE, "viewer"))
    _ws(root, "other", DAVE, idx)
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="",
                             global_system_workspace_path=str(root / "_global"),
                             internal_api_secret=SECRET)
    dispatcher = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    seen: list = []
    dispatcher.dispatch = lambda inv, **kw: seen.append((inv, kw)) or "unit-1"
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store, membership_index=idx,
                     email_subject_lookup=lambda a: {v: k for k, v in MAIL.items()}.get(str(a).lower()))
    yield TestClient(app), store, root, idx, seen
    store.close()


def _h(who: str, **extra) -> dict:
    return {"X-User-Id": who, "X-User-Email": MAIL.get(who, ""), **extra}


def _worker(who: str, *, regime: str = "human", workspaces: str = "acme") -> dict:
    """A worker acting for ``who``: the delegation headers the identity door stamps."""
    return _h(who, **{DELEGATION_HEADERS["regime"]: regime,
                      DELEGATION_HEADERS["workspaces"]: workspaces})


# ── the picker ─────────────────────────────────────────────────────────────────────────────────
def test_the_picker_names_only_people_who_share_a_workspace(env):
    client, *_ = env
    r = client.get("/api/people/mentionable", headers=_h(ALICE))
    assert r.status_code == 200, r.text
    people = r.json()["people"]
    assert [p["subject"] for p in people] == [JOE]
    assert people[0]["email"] == MAIL[JOE] and people[0]["workspaces"] == ["acme"]
    assert DAVE not in json.dumps(r.json()) and MAIL[DAVE] not in json.dumps(r.json())
    assert {"name": "vexa"} in r.json()["agents"]
    # and dave, who shares nothing with alice, sees nobody
    assert client.get("/api/people/mentionable", headers=_h(DAVE)).json()["people"] == []


def test_adding_someone_outside_the_picker_by_id_creates_nothing(env):
    client, store, *_ = env
    store.create(ALICE, name="pchat")
    r = client.post("/api/sessions/pchat/participants", json={"subject": DAVE, "history": "all"},
                    headers=_h(ALICE))
    assert r.status_code == 200
    assert [p["principal"] for p in r.json()["participants"]] == [ALICE]
    assert all(DAVE not in [p["principal"] for p in store.participants(s["id"])]
               for s in store.list_for(ALICE))
    assert store.list_for(DAVE) == []


# ── the mention path obeys the participant rules ───────────────────────────────────────────────
def test_a_mention_add_of_a_private_chat_forks_it(env):
    client, store, *_ = env
    private = store.create(ALICE, name="pchat")["id"]
    store.save_bundle(private, 0, _gmail_routine_bundle("summarise my inbox", "gmail_search"))
    pv = client.get("/api/sessions/pchat/share-preview", headers=_h(ALICE)).json()
    assert pv["forks"] is True and pv["routine"] is None
    r = client.post("/api/sessions/pchat/participants", json={"subject": JOE, "history": "none"},
                    headers=_h(ALICE))
    shared = r.json()["session_id"]
    assert shared != private
    assert [p["principal"] for p in store.participants(shared)] == [ALICE, JOE]
    assert [p["principal"] for p in store.participants(private)] == [ALICE]
    assert session_sync.bundle_for(store, JOE, private) is None
    assert store.load_bundle(shared) == {"version": 0, "files": {}}


def test_a_member_adding_by_mention_starts_their_own_chat(env):
    client, store, *_ = env
    sid = store.create(ALICE, name="team")["id"]
    store.add_participant(sid, ALICE, JOE)
    r = client.post(f"/api/sessions/{sid}/participants", json={"subject": ALICE, "history": "all"},
                    headers=_h(JOE))
    assert r.status_code == 403 and r.json()["detail"] == "history_is_the_owners"
    r = client.post(f"/api/sessions/{sid}/participants", json={"subject": ALICE}, headers=_h(JOE))
    assert r.status_code == 200 and r.json()["session_id"] != sid and r.json()["owner"] == JOE
    assert [p["principal"] for p in store.participants(sid)] == [ALICE, JOE]
    assert client.get(f"/api/sessions/{sid}/share-preview", headers=_h(JOE)).status_code == 403


def test_a_worker_without_a_person_is_refused_the_add(env):
    client, store, *_ = env
    store.create(ALICE, name="pchat")
    r = client.post("/api/sessions/pchat/participants", json={"subject": JOE},
                    headers=_worker(ALICE, regime="routine"))
    assert r.status_code == 403 and "human_session_required" in r.text
    assert len(store.list_for(JOE)) == 0


def test_a_worker_for_a_speaker_who_is_not_the_owner_cannot_share_the_history(env):
    client, store, *_ = env
    sid = store.create(ALICE, name="team")["id"]
    store.add_participant(sid, ALICE, JOE)
    r = client.post(f"/api/sessions/{sid}/participants", json={"subject": ALICE, "history": "all"},
                    headers=_worker(JOE))
    assert r.status_code == 403
    assert [p["principal"] for p in store.participants(sid)] == [ALICE, JOE]


# ── a routine's chat ─────────────────────────────────────────────────────────────────────────────
def _routine(store, prompt: str, tool: "str | None") -> dict:
    row = store.create(ALICE, name="flow-abc123", title="Inbox digest", flow="user:u_alice/inbox-digest")
    store.save_bundle(row["id"], 0, _gmail_routine_bundle(prompt, tool))
    return store.get(row["id"])


def test_a_private_connection_routine_warns_then_runs_restricted(env):
    client, store, *_ = env
    private = _routine(store, "Check my Gmail for unread threads and list them.", "mcp__vexa__gmail_search")
    pv = client.get("/api/sessions/flow-abc123/share-preview", headers=_h(ALICE)).json()
    assert pv["routine"] == {"name": "inbox-digest"} and pv["private_reads"] == ["Gmail"]
    r = client.post("/api/sessions/flow-abc123/participants", json={"subject": JOE, "history": "none"},
                    headers=_h(ALICE))
    shared = r.json()["session_id"]
    # the shared chat holds the routine now: its binding and the thread name its runs are posted to
    assert store.get(shared)["flow"] == "user:u_alice/inbox-digest"
    assert store.by_name(ALICE, "flow-abc123")["id"] == shared
    old = store.get(private["id"])
    assert old["flow"] is None and old["name"].startswith("flow-abc123.private-")
    assert [p["principal"] for p in store.participants(private["id"])] == [ALICE]
    # the routine's next run: posted by flows to the same thread name, answered without a mention,
    # and SHARED — the owner's desk and _system withheld, told to report a refused read
    is_member = lambda ws, who: m.is_member(env[2], ws, who) is not None  # noqa: E731
    p = plan_turn(store, subject=ALICE, session="flow-abc123", text="Check my Gmail.",
                  candidates=[ALICE, "_system", "acme", "_global"], is_member=is_member,
                  addressed=True)
    assert p.speak and p.scope is not None and p.row["id"] == shared
    assert ALICE in p.scope.withheld and "_system" in p.scope.withheld
    assert "shared_conversation" in p.preamble and "which read was refused" in p.preamble
    assert "PRIVATE-MAIL-4410" not in json.dumps(store.load_bundle(shared))


def test_a_shared_workspace_routine_shares_without_loss(env):
    client, store, *_ = env
    _routine(store, "Update the Lisbon weather table in acme.", "mcp__vexa__workspace_write")
    pv = client.get("/api/sessions/flow-abc123/share-preview", headers=_h(ALICE)).json()
    assert pv["routine"] == {"name": "inbox-digest"} and pv["private_reads"] == []
    client.post("/api/sessions/flow-abc123/participants", json={"subject": JOE, "history": "none"},
                headers=_h(ALICE))
    is_member = lambda ws, who: m.is_member(env[2], ws, who) is not None  # noqa: E731
    p = plan_turn(store, subject=ALICE, session="flow-abc123", text="Update the weather table.",
                  candidates=["acme", "_global"], is_member=is_member, addressed=True)
    assert p.speak and p.scope.allowed == ["acme", "_global"] and p.scope.withheld == []


def test_a_routine_run_needs_the_internal_tier_to_be_answered_unmentioned(env):
    client, store, *_ = env
    sid = store.create(ALICE, name="team")["id"]
    store.add_participant(sid, ALICE, JOE)
    # a person claiming to be a schedule is refused, or kept as a message — never answered as a run
    r = client.post("/api/chat/submit", json={"prompt": "run", "session": sid},
                    headers=_h(ALICE, **{"x-vexa-flow-regime": "autonomous"}))
    assert r.status_code == 403 or (r.status_code == 200 and r.json().get("spoken") is False), r.text


def test_the_private_read_classifier():
    files = _gmail_routine_bundle("Every morning: my calendar for today.", "mcp__vexa__calendar_events")
    assert session_fork.private_reads(files) == ["Calendar"]
    assert session_fork.private_reads(_gmail_routine_bundle("Lisbon weather", None)) == []


# ── workspace_member_add ──────────────────────────────────────────────────────────────────────
def test_the_owner_adds_a_mentioned_person_by_id(env):
    client, _store, root, *_ = env
    m.grant_membership(root, "other", ALICE, "owner", added_by=DAVE, index=env[3], email=MAIL[ALICE])
    r = client.post("/api/workspace/members/add",
                    json={"workspace": "other", "person": JOE, "role": "write"}, headers=_h(ALICE))
    assert r.status_code == 200, r.text
    assert r.json()["added"] is True and m.is_member(root, "other", JOE) == "contributor"


def test_a_workspace_is_named_by_its_name_or_slug(env):
    client, _store, root, *_ = env
    r = client.post("/api/workspace/members/add",
                    json={"workspace": "Acme", "person": JOE, "role": "write"}, headers=_h(ALICE))
    assert r.status_code == 200, r.text
    assert m.is_member(root, "acme", JOE) == "contributor"
    assert "can now read and write acme" in r.json()["said"]


def test_a_non_owner_cannot_add_to_a_workspace(env):
    client, _store, root, *_ = env
    r = client.post("/api/workspace/members/add",
                    json={"workspace": "acme", "person": ALICE, "role": "write"}, headers=_h(JOE))
    assert r.status_code == 403
    r = client.post("/api/workspace/members/add",
                    json={"workspace": "other", "person": JOE, "role": "read"}, headers=_h(ALICE))
    assert r.status_code == 404           # not one of alice's workspaces: not even named back
    assert m.is_member(root, "other", JOE) is None


def test_an_id_outside_the_owners_people_or_an_unknown_address_adds_nobody(env):
    client, _store, root, *_ = env
    for person in (DAVE, "nobody@acme.test"):
        r = client.post("/api/workspace/members/add",
                        json={"workspace": "acme", "person": person, "role": "read"},
                        headers=_h(ALICE))
        assert r.status_code == 404, r.text
    assert m.is_member(root, "acme", DAVE) is None


def test_a_background_worker_is_refused_the_workspace_add(env):
    client, _store, root, *_ = env
    r = client.post("/api/workspace/members/add",
                    json={"workspace": "acme", "person": JOE, "role": "write"},
                    headers=_worker(ALICE, regime="routine"))
    assert r.status_code == 403 and "human_session_required" in r.text
    assert m.is_member(root, "acme", JOE) == "viewer"


# ── one sentence, both shares ────────────────────────────────────────────────────────────────────
def test_one_sentence_makes_both_shares_with_the_mentioned_account(env):
    """The person types "share acme with @joe and share this chat with him" with Joe picked from
    the composer. The turn tells the agent Joe's account id; a fake agent that does what the prompt
    says calls the two tools as a worker with the person in the loop — the chat share on the person's
    press of the card naming Joe; both shares land on Joe."""
    client, store, root, _idx, seen = env
    store.create(ALICE, name="pchat")
    sentence = "share acme with @joe and share this chat with him"
    r = client.post("/api/chat", json={"prompt": sentence, "session": "pchat", "mentions": [JOE, DAVE]},
                    headers=_h(ALICE))
    assert r.status_code == 200, r.text
    prompt = json.dumps(seen[-1][0])
    assert f"account id `{JOE}`" in prompt and DAVE not in prompt   # dave is not alice's to mention
    # the fake agent: read the id off the mention block, call the two tools
    w = _worker(ALICE, workspaces="acme")
    a = client.post("/api/workspace/members/add",
                    json={"workspace": "acme", "person": JOE, "role": "write"}, headers=w)
    # the chat share is a card naming Joe (what in the history is sensitive, in one line); the
    # person's press is what the agent acts on — the typed sentence alone adds no one
    assert client.post("/api/sessions/pchat/participants", json={"subject": JOE},
                       headers=w).status_code == 403
    chat = store.by_name(ALICE, "pchat")["id"]
    card = {"id": "share-joe", "question": f"Share this chat, history included, with {MAIL[JOE]}?",
            "primary": {"label": "Share"}, "secondary": {"label": "Not now"}}
    store.save_bundle(chat, store.head(chat), {".claude/sessions/conversation.turns.jsonl": json.dumps(
        {"user_text": sentence, "ts": 1.0, "reply": "```ask\n" + json.dumps(card) + "\n```\n"}).encode()})
    app = _h(ALICE, **{"x-user-credential-class": "session"})          # the press is the person's, in the app
    item = client.post("/api/asks/open", json={"session": chat, "ask_id": "share-joe"}, headers=app).json()
    pressed = client.post(f"/api/asks/{item['pending_id']}/answer", headers=app, json={
        "nonce": item["nonce"], "content_hash": item["content_hash"], "reply": {"label": "Share"}})
    assert pressed.status_code == 200, pressed.text
    b = client.post("/api/sessions/pchat/participants", json={"subject": JOE, "pressed": item["pending_id"]},
                    headers=w)
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    assert m.is_member(root, "acme", JOE) == "contributor"
    assert b.json()["session_id"] == chat                         # shared in place, history and all
    assert [p["principal"] for p in store.participants(chat)] == [ALICE, JOE]


def test_the_first_message_of_a_new_shared_chat_is_the_agents(env):
    client, store, _root, _idx, seen = env
    sid = store.create(ALICE, name="team")["id"]
    store.add_participant(sid, ALICE, JOE)
    r = client.post("/api/chat/submit", json={"prompt": "share it with him", "session": sid, "to_agent": True},
                    headers=_h(ALICE))
    assert r.status_code == 200 and r.json().get("spoken") is not False and seen


def test_the_mention_block_drops_ids_it_cannot_vouch_for():
    people = [{"subject": JOE, "name": "joe", "email": MAIL[JOE], "workspaces": ["acme"]}]
    out = people_directory.mention_block(people, [JOE, DAVE], in_chat=[ALICE])
    assert f"`{JOE}`" in out and DAVE not in out and "not in this chat" in out
    assert people_directory.mention_block(people, [DAVE]) == ""


def test_a_button_press_in_a_shared_chat_goes_to_the_agent_and_never_becomes_a_line(env):
    """LIVE APP.DEV: "Update notes" in a shared meeting chat posted the whole internal instruction
    (`rewrite the content BETWEEN the <!-- meeting:<key>:start --> …`) as a line every participant
    read. A turn that carries an INTENT is a press of a button addressed to the agent: it is the
    agent's turn, and no room line is written from its prompt."""
    client, store, _root, _idx, seen = env
    sid = store.create(ALICE, name="team")["id"]
    store.add_participant(sid, ALICE, JOE)
    instruction = ("Extend: kg/meeting.md\n\nThis is meeting 100's own page … rewrite the content BETWEEN "
                   "the `<!-- meeting:<key>:start -->` / `:end` markers … set `transcript_cursor` to the next_index")
    r = client.post("/api/chat/submit", json={"prompt": instruction, "session": sid,
                                              "intent": {"kind": "extend", "path": "kg/meeting.md"}},
                    headers=_h(ALICE))
    assert r.status_code == 200, r.text
    assert r.json().get("spoken") is not False and seen
    assert all("meeting:<key>" not in (n.get("text") or "") for n in store.notes(sid))
