"""A file attached to a chat message (founder, 2026-10-13: a pasted screenshot rendered as the text
``Attached files: 9caa6e9e467d2341-Screenshot_….png`` and the agent's turn opened with a Glob).

Pinned here, from the server side:
- the mark the terminal writes is normalised by agent-api to files the speaker's own upload stored;
- history serves the attachments as a field and the person's words without them (old turns too);
- the worker hands the agent an EXACT absolute path, and each harness sends a picture as a picture;
- the attachment route answers by SESSION PARTICIPATION, never by workspace membership.
"""
from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from shared import attachments as att

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
STORED = "assets/9caa6e9e467d2341-Screenshot_2026-10-13_at_15.26.52.png"
NAME = "Screenshot 2026-10-13 at 15.26.52.png"


# ── the mark ─────────────────────────────────────────────────────────────────────────────────
def test_the_original_name_has_no_hash_prefix():
    assert att.original_name(STORED) == "Screenshot_2026-10-13_at_15.26.52.png"
    assert att.original_name("uploads/0123456789abcdef-q3.pdf") == "q3.pdf"


def test_normalise_keeps_only_the_speakers_own_stored_files_and_fills_in_the_facts():
    prompt = "what is wrong here?\n\n" + att.compose([
        {"path": STORED, "name": NAME, "ws": "someone_else", "size": 1},
        {"path": "assets/../../u_bob/x.png", "name": "x"},
        {"path": "uploads/0123456789abcdef-missing.pdf", "name": "gone"},
    ])
    sizes = {STORED: len(PNG)}
    out, kept = att.normalise(prompt, owner="u_alice", stat=sizes.get)
    assert kept == [{"ws": "u_alice", "path": STORED, "name": NAME, "type": "image/png", "size": len(PNG)}]
    assert out.startswith("what is wrong here?") and att.read(out)[0]["ws"] == "u_alice"


def test_strip_leaves_exactly_what_the_person_typed():
    prompt = "look\n\n" + att.compose([{"path": STORED, "name": NAME}])
    assert att.strip(prompt) == "look"
    expanded = att.expand(prompt, lambda ws, p: f"/workspaces/u_alice/{p}")
    assert att.strip(expanded) == "look"
    assert att.strip(att.compose([{"path": STORED}])) == ""


def test_the_legacy_attached_files_lines_read_as_attachments():
    old = ("is this right?\n\nAttached files:\n"
           f"- ![9caa6e9e467d2341-Screenshot_2026-10-13_at_15.26.52.png]({STORED}) — already in this "
           "workspace; reference it like that on a page\n- @file:uploads/0123456789abcdef-q3.pdf")
    items = att.read(old)
    assert [(i["name"], i["kind"]) for i in items] == [
        ("Screenshot_2026-10-13_at_15.26.52.png", "image"), ("q3.pdf", "file")]
    assert att.strip(old) == "is this right?"
    assert att.strip(old.split("\n\n", 1)[1]) == ""


def test_expand_gives_the_agent_the_exact_absolute_path():
    prompt = "see\n\n" + att.compose([{"ws": "u_alice", "path": STORED, "name": NAME,
                                       "type": "image/png", "size": 2048}])
    out = att.expand(prompt, lambda ws, p: f"/workspaces/{ws}/{p}" if ws == "u_alice" else None)
    assert f"`/workspaces/u_alice/{STORED}`" in out
    assert "never search" in out and "9caa6e9e467d2341" not in out.split("## Attached")[1].split("`")[1]
    assert att.images(out) == [{"path": f"/workspaces/u_alice/{STORED}", "type": "image/png", "name": NAME}]


def test_expand_says_plainly_when_the_turn_cannot_read_the_file():
    prompt = att.compose([{"ws": "u_alice", "path": STORED, "name": NAME}])
    out = att.expand(prompt, lambda ws, p: None)
    assert "not readable in this conversation" in out and att.images(out) == []


# ── history ──────────────────────────────────────────────────────────────────────────────────
def _transcript(*prompts: str) -> str:
    return "\n".join(json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": p}]}}) for p in prompts)


def _rec(prompt: str, said: str) -> dict:
    import hashlib
    return {"key": hashlib.sha256(prompt.encode()).hexdigest(), "user_text": said}


def test_history_serves_attachments_and_never_the_mark():
    from control_plane.workspace_reader import parse_history

    prompt = att.expand("what is this?\n\n" + att.compose([
        {"ws": "u_alice", "path": STORED, "name": NAME, "type": "image/png", "size": 70}]),
        lambda ws, p: f"/workspaces/u_alice/{p}")
    turns = parse_history(_transcript(prompt), [_rec(prompt, "what is this?")])
    (t,) = turns
    assert t["user_text"] == "what is this?"
    assert t["attachments"] == [{"name": NAME, "path": STORED, "type": "image/png", "kind": "image",
                                 "ws": "u_alice", "size": 70}]
    assert "/workspaces/" not in json.dumps(t["attachments"])


def test_history_of_an_attachment_only_message_has_empty_words():
    from control_plane.workspace_reader import parse_history

    prompt = att.compose([{"ws": "u_a", "path": STORED, "name": NAME}])
    (t,) = parse_history(_transcript(prompt), [_rec(prompt, prompt)])   # an older worker kept the mark
    assert t["user_text"] == "" and t["attachments"][0]["name"] == NAME
    (t,) = parse_history(_transcript(prompt), [])                        # no record at all
    assert "vexa:attachments" not in t["text"] and t["attachments"][0]["name"] == NAME


def test_history_reads_old_turns_too():
    from control_plane.workspace_reader import parse_history

    old = f"Attached files:\n- ![x]({STORED}) — already in this workspace; reference it like that on a page"
    (t,) = parse_history(_transcript(old), [{"key": "k", "user_text": old}])
    assert t["user_text"] == "" and t["attachments"][0]["name"] == "Screenshot_2026-10-13_at_15.26.52.png"


def test_the_worker_records_the_persons_words_without_the_block(tmp_path):
    from worker import engine

    prompt = "hi\n\n" + att.compose([{"ws": "u_a", "path": STORED, "name": NAME}])
    engine.record_user_text(tmp_path, "main", "composed", engine.recorded_words(prompt))
    rec = json.loads((tmp_path / ".claude/sessions/main.turns.jsonl").read_text().splitlines()[-1])
    assert rec["user_text"] == "hi"


def test_the_worker_resolves_the_speakers_desk_in_its_mounts(tmp_path, monkeypatch):
    from worker import engine

    desk = tmp_path / "u_alice"
    (desk / "assets").mkdir(parents=True)
    (desk / STORED).write_bytes(PNG)
    monkeypatch.setenv("VEXA_MOUNTS", json.dumps([{"slug": "u_alice", "path": str(desk), "role": "private",
                                                   "write": True, "primary": True}]))
    prompt = "see\n\n" + att.compose([{"ws": "u_alice", "path": STORED, "name": NAME, "type": "image/png"}])
    out = engine.expand_attachments(prompt)
    assert f"`{desk / STORED}`" in out
    monkeypatch.setenv("VEXA_MOUNTS", json.dumps([{"slug": "team", "path": str(tmp_path / "team")}]))
    assert "not readable in this conversation" in engine.expand_attachments(prompt)


# ── the harnesses: a picture goes as a picture ───────────────────────────────────────────────
def _resolved(tmp_path) -> str:
    p = tmp_path / "shot.png"
    p.write_bytes(PNG)
    return att.expand("what is this?\n\n" + att.compose([
        {"ws": "u", "path": STORED, "name": NAME, "type": "image/png", "size": len(PNG)}]),
        lambda ws, rel: str(p))


def test_claude_code_sends_the_picture_as_an_image_block(tmp_path):
    from llm import claude_code

    msg = json.loads(claude_code._user_message_json(_resolved(tmp_path)))
    blocks = msg["message"]["content"]
    assert blocks[0]["type"] == "text" and "what is this?" in blocks[0]["text"]
    assert blocks[1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                     "data": base64.b64encode(PNG).decode()}}
    plain = json.loads(claude_code._user_message_json("hello"))
    assert plain["message"]["content"] == [{"type": "text", "text": "hello"}]


def test_codex_sends_the_picture_as_a_local_image(tmp_path):
    from llm import codex

    items = codex._turn_input(_resolved(tmp_path))
    assert items[0]["type"] == "text"
    assert items[1] == {"type": "localImage", "path": str(tmp_path / "shot.png")}
    assert codex._turn_input("hello") == [{"type": "text", "text": "hello"}]


def test_openai_agent_sends_the_picture_when_the_endpoint_takes_images(tmp_path, monkeypatch):
    from llm import openai_agent

    prompt = _resolved(tmp_path)
    monkeypatch.setenv("VEXA_LLM_IMAGES", "on")
    content = openai_agent._user_content(prompt)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    monkeypatch.setenv("VEXA_LLM_IMAGES", "off")
    assert openai_agent._user_content(prompt) == prompt   # the exact path is still in the text


# ── the route: participation, not membership ─────────────────────────────────────────────────
@pytest.fixture
def env(tmp_path, monkeypatch):
    from tests.test_session_routes import SUBJECTS, _FakeIdentity, _FakeReader, _FakeRuntime
    from control_plane.api import _Sessions, create_app
    from control_plane.dispatch import Dispatcher
    from control_plane.session_store import SessionStore
    from control_plane.workspace_reader import WorkspaceReader
    from shared.config import load_settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-model-credential")
    root = tmp_path / "ws"
    root.mkdir()
    from tests._session_fakes import MemBlobs
    store = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    settings = load_settings(workspaces_dir=str(root), redis_url="",
                             global_system_workspace_path=str(root / "_global"))
    dispatcher = Dispatcher(settings, _FakeRuntime(), _FakeIdentity())
    calls = []
    dispatcher.dispatch = lambda inv, **kw: calls.append((inv, kw)) or "unit-1"
    app = create_app(dispatcher, stream_reader=_FakeReader(), reader=WorkspaceReader(str(root)),
                     sessions=_Sessions(), session_store=store,
                     email_subject_lookup=lambda a: SUBJECTS.get(str(a).lower()))
    store.test_dispatcher = dispatcher   # the bundle a dispatch would carry is the dispatcher's
    yield TestClient(app), store, calls, root
    store.close()


def _h(who):
    return {"X-User-Id": who}


def _upload(client, who, name="Screenshot 2026-10-13 at 15.26.52.png", data=PNG, mime="image/png"):
    r = client.post("/api/workspace/upload", headers=_h(who), files=[("files", (name, data, mime))])
    assert r.status_code == 200, r.text
    return r.json()["files"][0]


def test_the_upload_answers_the_original_name_and_size(env):
    client, *_ = env
    f = _upload(client, "alice")
    assert f["original"] == "Screenshot 2026-10-13 at 15.26.52.png" and f["size"] == len(PNG)
    assert f["type"] == "image/png" and f["path"].startswith("assets/")


def test_a_private_chat_dispatches_the_normalised_mark(env):
    client, store, calls, _ = env
    f = _upload(client, "alice")
    prompt = "what is this?\n\n" + att.compose([{"path": f["path"], "name": f["original"]},
                                                {"path": "assets/0123456789abcdef-nope.png"}])
    r = client.post("/api/chat/submit", json={"session": "pchat-9", "prompt": prompt}, headers=_h("alice"))
    assert r.status_code == 200, r.text
    (inv, _kw), = calls
    items = att.read(inv["start"]["entrypoint"]["inline"])
    assert [(i["path"], i["ws"], i["name"]) for i in items] == [(f["path"], "alice", f["original"])]


def test_the_poster_reads_their_own_attachment(env):
    client, *_ = env
    f = _upload(client, "alice")
    r = client.get(f"/api/sessions/pchat-9/attachment?path={f['path']}", headers=_h("alice"))
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["x-content-type-options"] == "nosniff"


def test_a_participant_reads_an_attachment_posted_in_the_shared_chat(env):
    client, store, calls, root = env
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test"},
                      headers=_h("alice")).json()["session_id"]
    f = _upload(client, "alice")
    other = _upload(client, "alice", name="private.png", data=PNG + b"x")
    prompt = "@vexa look\n\n" + att.compose([{"path": f["path"], "name": f["original"]}])
    assert client.post("/api/chat/submit", json={"session": sid, "prompt": prompt},
                       headers=_h("alice")).status_code == 200
    url = f"/api/sessions/{sid}/attachment?path={f['path']}"
    assert client.get(url, headers=_h("bob")).content == PNG               # a participant
    # …but only what was POSTED here: another file on alice's desk is not his to read
    assert client.get(f"/api/sessions/{sid}/attachment?path={other['path']}",
                      headers=_h("bob")).status_code == 404
    # someone who is not in the conversation gets the answer a missing one gets
    assert client.get(url, headers=_h("carol")).status_code == 404


def test_workspace_membership_does_not_open_an_attachment(env, monkeypatch):
    client, store, calls, root = env
    from control_plane import workspace_membership

    f = _upload(client, "alice")
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test"},
                      headers=_h("alice")).json()["session_id"]
    client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa x\n\n" + att.compose(
        [{"path": f["path"]}])}, headers=_h("alice"))
    # even if carol were a member of every workspace there is, she is not in this conversation
    monkeypatch.setattr(workspace_membership, "is_member", lambda *a, **k: {"role": "owner"})
    assert client.get(f"/api/sessions/{sid}/attachment?path={f['path']}",
                      headers=_h("carol")).status_code == 404
    assert client.get(f"/api/sessions/{sid}/attachment?path={f['path']}",
                      headers=_h("bob")).status_code == 200


def test_the_route_refuses_paths_outside_the_attachment_folders(env):
    client, *_ = env
    for bad in ("README.md", "../alice/assets/x.png", ".claude/sessions/main.turns.jsonl"):
        r = client.get(f"/api/sessions/pchat-9/attachment?path={bad}", headers=_h("alice"))
        assert r.status_code in (400, 404), bad


# ── a shared chat: what was posted in it travels with it, and nothing else does ───────────────
def test_in_a_two_person_chat_b_asks_about_the_picture_a_posted(env, tmp_path, monkeypatch):
    client, store, calls, root = env
    sid = client.post("/api/sessions/pchat-1/participants", json={"email": "bob@acme.test"},
                      headers=_h("alice")).json()["session_id"]
    f = _upload(client, "alice")
    private = _upload(client, "alice", name="private.png", data=PNG + b"private")
    # A posts the picture without addressing the agent: a message everyone in the chat reads
    r = client.post("/api/chat/submit", json={"session": sid, "prompt": "here it is\n\n" + att.compose(
        [{"path": f["path"], "name": f["original"]}])}, headers=_h("alice"))
    assert r.status_code == 200 and r.json()["spoken"] is False and calls == []
    # B asks the agent about it
    r = client.post("/api/chat/submit", json={"session": sid, "prompt": "@vexa what's in the picture?"},
                    headers=_h("bob"))
    assert r.status_code == 200, r.text
    (inv, kw), = calls
    prompt = inv["start"]["entrypoint"]["inline"]
    assert "alice" not in kw["shared_scope"].allowed           # her desk is not mounted…
    assert [(i["path"], i["ws"]) for i in att.read(prompt)] == [(f["path"], "alice")]
    assert "vexa:attachments" not in prompt.split("Said in this conversation")[1].split("\n\n")[1]
    # …but the file she posted travels with the conversation, and nothing else of hers does
    bundle = store.test_dispatcher._session_bundle(inv)
    assert bundle is not None and set(bundle["attachments"]) == {f["path"]}
    assert base64.b64decode(bundle["attachments"][f["path"]]) == PNG
    assert private["path"] not in bundle["attachments"]

    # THE WORKER: B's turn materialises the conversation, finds the picture, and sends it as an image
    from worker import engine, session_carry
    from llm import claude_code

    carry = session_carry.Carry(tmp_path / "home" / ".vexa-session", session=sid)
    monkeypatch.setattr(engine, "_CARRY", carry)
    monkeypatch.setenv("VEXA_MOUNTS", json.dumps([{"slug": "bob", "path": str(tmp_path / "bobdesk")}]))
    carry.receive(bundle)
    expanded = engine.expand_attachments(prompt)
    where = str(carry.root / "attachments" / f["path"])
    assert f"`{where}`" in expanded and "not readable" not in expanded
    blocks = json.loads(claude_code._user_message_json(expanded))["message"]["content"]
    assert blocks[1]["type"] == "image" and base64.b64decode(blocks[1]["source"]["data"]) == PNG
    # and the attachments are never handed back as part of the transcript
    assert all(not k.startswith("attachments/") for k in carry.collect())


def test_the_carry_refreshes_attachments_even_when_the_transcript_did_not_move(tmp_path):
    from worker import session_carry

    c = session_carry.Carry(tmp_path / "s", session="main")
    c.receive({"id": "s-1", "version": 3, "files": {}})
    c.receive({"id": "s-1", "version": 3, "files": {},
               "attachments": {STORED: base64.b64encode(PNG).decode()}})
    assert c.attachment_path(STORED) and open(c.attachment_path(STORED), "rb").read() == PNG
    assert c.attachment_path("../../etc/passwd") is None
    c.receive({"id": "s-1", "version": 3, "files": {}, "attachments": {"../x": "AA=="}})
    assert c.attachment_path(STORED) is None


# ── WHERE AN ATTACHMENT LANDS (Vexa-ai/vexa#1783, founder 2026-10-15): in the chat's target
#    workspace, never Personal when the chat does not read it or it is hidden; an incognito chat
#    keeps its files out of every workspace and they go with the chat. ──────────────────────────

def _upload_in(client, who, session, name="shot.png"):
    r = client.post(f"/api/workspace/upload?session={session}", headers=_h(who),
                    files=[("files", (name, PNG, "image/png"))])
    assert r.status_code == 200, r.text
    return r.json()["files"][0]


def test_an_attachment_lands_in_the_chats_target_and_the_turn_reads_it_there(env):
    from control_plane import workspace_attach as wa
    client, store, calls, root = env
    (root / "alice").mkdir(exist_ok=True)
    w = wa.create_workspace(root, "alice", name="FINOS").slug
    assert client.post("/api/chat/target", json={"session": "pchat-t", "workspace": w}, headers=_h("alice")).status_code == 200
    f = _upload_in(client, "alice", "pchat-t")
    assert f["ws"] == w
    assert (wa.workspace_slot_dir(root, "alice", w) / f["path"]).is_file()
    assert not (root / "alice" / f["path"]).exists(), "not in Personal"
    prompt = "see\n\n" + att.compose([{"path": f["path"], "name": f["original"]}])
    assert client.post("/api/chat/submit", json={"session": "pchat-t", "prompt": prompt}, headers=_h("alice")).status_code == 200
    (inv, _kw), = calls
    assert att.read(inv["start"]["entrypoint"]["inline"])[0]["ws"] == w
    r = client.get(f"/api/sessions/pchat-t/attachment?path={f['path']}", headers=_h("alice"))
    assert r.status_code == 200 and r.content == PNG


def test_an_incognito_chats_attachment_is_in_no_workspace_and_goes_with_the_chat(env):
    from control_plane import system_mounts
    client, store, calls, root = env
    client.post("/api/workspace/profile", headers=_h("alice"), json={"name": "incognito", "session": "pchat-i"})
    f = _upload_in(client, "alice", "pchat-i")
    assert f["ws"] == "_system"
    assert not (root / "alice" / f["path"]).exists()
    home = system_mounts.ensure_system_workspace(str(root), "alice")
    assert (home / f["path"]).is_file()
    r = client.get(f"/api/sessions/pchat-i/attachment?path={f['path']}", headers=_h("alice"))
    assert r.status_code == 200


def test_an_incognito_chats_attachment_is_deleted_with_the_chat(env, monkeypatch):
    from control_plane import system_mounts
    client, store, calls, root = env
    client.post("/api/workspace/profile", headers=_h("alice"), json={"name": "incognito", "session": "pchat-j"})
    f = _upload_in(client, "alice", "pchat-j")
    home = system_mounts.ensure_system_workspace(str(root), "alice")
    assert (home / f["path"]).is_file()
    import time as _t
    real = _t.time
    monkeypatch.setattr(_t, "time", lambda: real() + 30 * 86400)
    client.get("/api/sessions", headers=_h("alice"))
    assert not (home / f["path"]).exists()
