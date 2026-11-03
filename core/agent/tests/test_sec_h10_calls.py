"""H10 for a call's proposals: a change the post-meeting run lands on its own, or the owner's agent
accepts, says an agent wrote it; the owner's own acceptance carries no agent trailer."""
from __future__ import annotations

import subprocess

from tests.test_sec_h10_commit_sites import assert_agents, assert_persons
from tests.test_session_calls import H, env  # noqa: F401  (fixture: session alice+bob, internal secret)

INTERNAL = "internal-test-secret-0123456789abcdef"
AGENT_OF_BOB = {"x-user-id": "bob", "x-user-regime": "human", "x-user-delegation-unit": "agent-bob-chat-c9"}


def _desk(root):
    desk = root / "bob"
    desk.mkdir()
    subprocess.run(["git", "init", "-q", str(desk)], check=True)
    return desk


def _proposal(client, sid, path, headers=None):
    r = client.post(f"/api/sessions/{sid}/proposals", headers=headers or {"x-internal-secret": INTERNAL}, json={
        "owner": "bob", "kind": "page", "workspace": "bob", "path": path, "title": "Call notes",
        "body": "# Call\n", "meeting_id": 41})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_proposal_the_post_meeting_run_lands_on_its_own_is_attributed(env):
    client, _store, sid, _r, root = env
    desk = _desk(root)
    (root / "_global").mkdir(exist_ok=True)
    (root / "_global" / "POLICIES.md").write_text("---\npost_meeting_auto_accept: on\n---\n")
    out = _proposal(client, sid, "notes/auto.md", headers={"x-internal-secret": INTERNAL, "x-user-id": "bob"})
    assert out.get("auto_accepted") is True, out
    # the post-meeting run names no session of its own: the trailers say who and that an agent wrote it
    assert_agents(desk, "accept: Call notes", person="bob", unit="")


def test_a_proposal_the_owners_agent_accepts_is_attributed(env):
    client, _store, sid, _r, root = env
    desk = _desk(root)
    p = _proposal(client, sid, "notes/call.md")["proposal"]
    r = client.post(f"/api/sessions/{sid}/proposals/{p['id']}/accept", headers=AGENT_OF_BOB, json={})
    assert r.status_code == 200, r.text
    assert_agents(desk, "accept: Call notes", person="bob", unit="agent-bob-chat-c9")


def test_the_owners_own_acceptance_carries_no_agent_trailer(env):
    client, _store, sid, _r, root = env
    desk = _desk(root)
    p = _proposal(client, sid, "notes/call.md")["proposal"]
    r = client.post(f"/api/sessions/{sid}/proposals/{p['id']}/accept", headers=H("bob"), json={})
    assert r.status_code == 200, r.text
    assert_persons(desk, "accept: Call notes")
