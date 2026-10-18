"""The agent's `notify` tool (#1783): founder, looking at the bell — "agents need tooling to use that".

An agent (a chat turn or a routine's run) posts a notification to its OWNER by default; `to` names
other participants of the same chat only, never anyone else. Kind `agent`, the chat or routine as its
source. Guards: ≤200 characters on one line; 20 an hour per person; a routine's run posts at most
one; identical text to the same person within ten minutes is posted once; `_global/POLICIES.md`
`agent_notifications: off` turns it off; a link to a page goes only to people who can read it.

L2: FastAPI over a sqlite session store, real git workspaces, a list for the redis bus."""
from __future__ import annotations

import json

import pytest

from control_plane import notifications as nt
from control_plane.identity_token import DELEGATION_HEADERS
from tests.test_notifications_routes import ALICE, DAVE, JOE, MAIL, _Bus, _h, _ws, env  # noqa: F401


def _worker(who: str, unit: str, regime: str = "human") -> dict:
    return _h(who, **{DELEGATION_HEADERS["regime"]: regime, DELEGATION_HEADERS["workspaces"]: "acme",
                      DELEGATION_HEADERS["unit"]: unit})


def _mine(client, who):
    return client.get("/api/notifications", headers=_h(who)).json()["notifications"]


def _shared(store):
    sid = store.create(ALICE, name="team", title="Board prep")["id"]
    store.add_participant(sid, ALICE, JOE)
    return sid


def test_an_agent_notifies_its_owner_by_default_with_the_chat_as_its_link(env):
    client, store, bus, _ = env
    sid = _shared(store)
    r = client.post("/api/notifications/notify", json={"text": "Three urgent emails from the bank", "session": sid},
                    headers=_worker(ALICE, f"agent-{ALICE}-chat-{sid}"))
    assert r.status_code == 200, r.text
    assert r.json()["to"] == [ALICE]
    [n] = _mine(client, ALICE)
    assert n["kind"] == "agent" and n["text"] == "Three urgent emails from the bank"
    assert n["object"] == {"kind": "chat", "id": sid, "name": "Board prep"}
    assert bus.to(ALICE)[0]["type"] == "notification"
    assert _mine(client, JOE) == []


def test_to_names_participants_of_the_same_chat_only(env):
    client, store, _, _ = env
    sid = _shared(store)
    w = _worker(ALICE, f"agent-{ALICE}-chat-{sid}")
    r = client.post("/api/notifications/notify", json={"text": "Draft is ready", "session": sid, "to": [JOE]}, headers=w)
    assert r.status_code == 200 and r.json()["to"] == [JOE]
    assert [n["kind"] for n in _mine(client, JOE)] == ["agent"]
    r = client.post("/api/notifications/notify", json={"text": "Hi", "session": sid, "to": [DAVE]}, headers=w)
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_a_participant"
    assert _mine(client, DAVE) == []
    # a chat the caller is not in names nobody
    other = store.create(DAVE, name="dave-only", title="x")["id"]
    r = client.post("/api/notifications/notify", json={"text": "Hi", "session": other}, headers=_h(ALICE))
    assert r.status_code == 404


def test_no_chat_means_the_owner_only(env):
    client, _, _, _ = env
    r = client.post("/api/notifications/notify", json={"text": "Done", "to": [JOE]}, headers=_h(ALICE))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_a_participant"
    assert client.post("/api/notifications/notify", json={"text": "Done"}, headers=_h(ALICE)).status_code == 200


def test_the_text_is_one_line_of_at_most_200_characters(env):
    client, _, _, _ = env
    assert client.post("/api/notifications/notify", json={"text": "x" * 201}, headers=_h(ALICE)).status_code == 422
    assert client.post("/api/notifications/notify", json={"text": "  "}, headers=_h(ALICE)).status_code == 422
    client.post("/api/notifications/notify", json={"text": "line one\nline two"}, headers=_h(ALICE))
    assert _mine(client, ALICE)[0]["text"] == "line one line two"


def test_identical_text_within_ten_minutes_is_posted_once(env):
    client, _, bus, _ = env
    for _ in range(3):
        r = client.post("/api/notifications/notify", json={"text": "Same thing"}, headers=_h(ALICE))
        assert r.status_code == 200
    assert len(_mine(client, ALICE)) == 1 and r.json()["deduped"] == [ALICE]
    assert len([f for f in bus.to(ALICE) if f["type"] == "notification"]) == 1


def test_twenty_an_hour_per_person(env, monkeypatch):
    client, _, _, _ = env
    monkeypatch.setattr(nt, "AGENT_PER_HOUR", 3)
    for i in range(3):
        assert client.post("/api/notifications/notify", json={"text": f"n{i}"}, headers=_h(ALICE)).status_code == 200
    r = client.post("/api/notifications/notify", json={"text": "n4"}, headers=_h(ALICE))
    assert r.status_code == 429 and r.json()["detail"]["refused"] == "rate_limited"
    # another person's budget is their own
    assert client.post("/api/notifications/notify", json={"text": "n4"}, headers=_h(JOE)).status_code == 200


def test_a_routine_run_posts_at_most_one(env):
    client, store, _, _ = env
    row = store.create(ALICE, name="flow-abc123", title="Inbox digest", flow="user:u_alice/check-emails")
    unit = f"agent-{ALICE}-chat-flow-abc123-auto"
    w = _worker(ALICE, unit, regime="routine")
    r = client.post("/api/notifications/notify", json={"text": "Important: invoice overdue"}, headers=w)
    assert r.status_code == 200, r.text
    n = _mine(client, ALICE)[0]
    assert n["object"]["id"] == row["id"] and n["actor"]["name"] == "check-emails"
    r = client.post("/api/notifications/notify", json={"text": "Important: second one"}, headers=w)
    assert r.status_code == 429 and r.json()["detail"]["refused"] == "one_per_run"


def test_the_policy_turns_it_off(env):
    client, _, _, root = env
    g = root / "_global"
    g.mkdir(exist_ok=True)
    (g / "POLICIES.md").write_text("---\nagent_notifications: off\n---\n# Policies\n")
    r = client.post("/api/notifications/notify", json={"text": "Hi"}, headers=_h(ALICE))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "agent_notifications_off"
    assert _mine(client, ALICE) == []


def test_a_page_link_goes_only_to_people_who_can_read_it(env):
    client, store, _, root = env
    sid = _shared(store)
    w = _worker(ALICE, f"agent-{ALICE}-chat-{sid}")
    r = client.post("/api/notifications/notify", json={"text": "Notes updated", "session": sid, "to": [JOE],
                                                       "link": "page:acme/README.md"}, headers=w)
    assert r.status_code == 200, r.text
    n = _mine(client, JOE)[0]
    assert n["object"]["kind"] == "page" and n["object"]["url"] == "/w/acme/README.md"
    # dave is in the chat but not in acme: refused, nothing posted
    store.add_participant(sid, ALICE, DAVE)
    r = client.post("/api/notifications/notify", json={"text": "Notes updated", "session": sid, "to": [DAVE],
                                                       "link": "page:acme/README.md"}, headers=w)
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "link_not_readable"
    assert _mine(client, DAVE) == []
    r = client.post("/api/notifications/notify", json={"text": "x", "link": "https://evil.test/"}, headers=_h(ALICE))
    assert r.status_code == 422


def test_a_worker_cannot_name_a_chat_other_than_its_own(env):
    client, store, _, _ = env
    sid = _shared(store)
    other = store.create(ALICE, name="other", title="Other")["id"]
    r = client.post("/api/notifications/notify", json={"text": "x", "session": other, "to": [JOE]},
                    headers=_worker(ALICE, f"agent-{ALICE}-chat-{sid}"))
    assert r.status_code == 403 and r.json()["detail"]["refused"] == "not_this_chat"


def test_the_tool_is_in_the_manifest_and_the_toolbelt():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    tools = {t["name"]: t for t in json.loads((root / "mcp.tools.v1.json").read_text())["tools"]}
    assert tools["notify"]["route"] == {"method": "POST", "path": "/api/notifications/notify"}
    assert "notify" in json.loads((root / "worker" / "mcp_tools.v1.json").read_text())["tools"]
