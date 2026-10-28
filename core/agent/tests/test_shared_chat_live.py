"""A shared chat is live both ways (#1783, collaboration rehearsal 2026-10-14).

A message one person posts in a shared chat without addressing the agent was stored and never
shown: the terminal had no reader for it. The chat now reads `GET /api/sessions/{s}/messages` while
it is open: it answers who is asking (`you`), the conversation's `version` (it moves when anyone's
turn lands, so the other person's tab re-reads the conversation) and each person's name.
"""
from __future__ import annotations

from tests.test_access_sessions_share import env  # noqa: F401 — the fixture


def test_the_other_person_reads_the_line_with_who_wrote_it(env):  # noqa: F811
    c, store, sid = env
    r = c.post(f"/api/sessions/{sid}/participants", headers={"X-User-Id": "alice"},
               json={"email": "bob@acme.test"})
    assert r.status_code == 200, r.text
    shared = r.json()["session_id"]
    store.add_note(shared, "bob", "hi alice")
    m = c.get(f"/api/sessions/{shared}/messages", headers={"X-User-Id": "alice"})
    assert m.status_code == 200, m.text
    d = m.json()
    assert d["you"] == "alice"
    assert [x["text"] for x in d["messages"]] == ["hi alice"] and d["messages"][0]["author"] == "bob"
    assert isinstance(d["version"], int) and "names" in d


def test_the_version_moves_when_a_turn_lands(env):  # noqa: F811
    c, store, sid = env
    shared = c.post(f"/api/sessions/{sid}/participants", headers={"X-User-Id": "alice"},
                    json={"email": "bob@acme.test"}).json()["session_id"]
    before = c.get(f"/api/sessions/{shared}/messages", headers={"X-User-Id": "bob"}).json()["version"]
    head = int((store.get(shared) or {}).get("version") or 0)
    store.save_bundle(shared, head, {".claude/sessions/conversation.session": b"sid-2\n"})
    after = c.get(f"/api/sessions/{shared}/messages", headers={"X-User-Id": "bob"}).json()["version"]
    assert after > before


def test_participants_carry_names(env):  # noqa: F811
    c, _store, sid = env
    v = c.post(f"/api/sessions/{sid}/participants", headers={"X-User-Id": "alice"},
               json={"email": "bob@acme.test"}).json()
    assert "names" in v and isinstance(v["names"], dict)


def test_a_line_carries_the_person_s_words_never_the_terminal_s_context(env):  # noqa: F811
    """The terminal folds an "Active context: …" line and a sentinel in front of what the person typed;
    a line posted to the people in the chat keeps only the person's words (rehearsal: the other person
    read "Active context: the user is viewing the workspace file README.md …")."""
    c, store, sid = env
    shared = c.post(f"/api/sessions/{sid}/participants", headers={"X-User-Id": "alice"},
                    json={"email": "bob@acme.test"}).json()["session_id"]
    r = c.post("/api/chat/submit", headers={"X-User-Id": "bob"}, json={
        "prompt": "Active context: the user is viewing README.md. --- <!--vexa:user-input-below-->hi alice",
        "session": shared})
    assert r.status_code == 200, r.text
    store.add_note(shared, "bob", "old: <!--vexa:user-input-below-->kept")
    lines = c.get(f"/api/sessions/{shared}/messages", headers={"X-User-Id": "alice"}).json()["messages"]
    assert [x["text"] for x in lines] == ["hi alice", "kept"]
