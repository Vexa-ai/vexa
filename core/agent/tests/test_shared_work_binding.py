"""SHARED WORK IS A PROFILE, NOT A SPECIAL RULE (Vexa-ai/vexa#1783, founder 2026-10-14). Since PRD
0.13.4 D26, opening a workspace invite/join link no longer moves the person's next chat; sharing a chat (adding a person) binds that person's view of it to the workspace the chat
writes to. The header chip says "Shared work · writes to <workspace>"."""
from __future__ import annotations

from tests.test_join_links import B, HOST, _h, _link, _token, env  # noqa: F401 — the fixture


def _states(client, who, session):
    return client.get(f"/api/workspace/states?session={session}", headers=_h(who)).json()


# REVERSED by PRD 0.13.4 D26 (security review H4): opening a link used to start the person's next new
# chat writing into the linked workspace, so one link sent to a signed-in person moved their writes
# into a workspace the sender reads. Joining adds the workspace; only the person switches a chat's
# target. (Sharing a CHAT, below, still binds that chat — it is the chat they were added to.)
def test_opening_a_workspace_link_never_moves_the_next_new_chats_target(env):
    client, root, _ = env
    tok = _token(_link(client)["url"])
    assert client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(B)).status_code == 200
    assert client.post("/api/chat", json={"prompt": "hi", "session": "pchat-b1"}, headers=_h(B)).status_code == 200
    assert _states(client, B, "pchat-b1")["chat"]["target"] != "dna"


def test_accepting_by_the_join_page_never_moves_it_either(env):
    client, root, _ = env
    tok = _token(_link(client)["url"])
    assert client.post("/api/workspace/invites/accept", json={"token": tok}, headers=_h(B)).status_code == 200
    client.post("/api/chat", json={"prompt": "hi", "session": "pchat-b2"}, headers=_h(B))
    assert _states(client, B, "pchat-b2")["chat"]["target"] != "dna"


def test_sharing_a_chat_that_writes_to_a_workspace_binds_the_newcomer_to_it(env):
    client, root, _ = env
    tok = _token(_link(client)["url"])
    client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(B))      # B is in dna
    assert client.post("/api/chat/target", json={"session": "pchat-h", "workspace": "dna"},
                       headers=_h(HOST)).status_code == 200
    client.post("/api/chat", json={"prompt": "hi", "session": "pchat-h"}, headers=_h(HOST))
    r = client.post("/api/sessions/pchat-h/participants", json={"subject": B}, headers=_h(HOST))
    assert r.status_code == 200, r.text
    shared = r.json()["session_id"]
    v = _states(client, B, shared)
    assert v["chat"]["target"] == "dna" and v["chat"]["reads"] == ["dna"]


def test_opening_a_chat_link_binds_the_newcomer_to_the_workspace_the_chat_writes_to(env):
    from control_plane import join_links
    client, root, _ = env
    tok = _token(_link(client)["url"])
    client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(B))      # B is in dna
    client.post("/api/chat/target", json={"session": "pchat-h2", "workspace": "dna"}, headers=_h(HOST))
    client.post("/api/chat", json={"prompt": "hi", "session": "pchat-h2"}, headers=_h(HOST))
    sid = client.get("/api/sessions/pchat-h2/participants", headers=_h(HOST)).json()["session_id"]
    members: set = set()
    join_links.register_chat_target(join_links.ChatTarget(
        may_link=lambda s, c: s == HOST and c == sid, is_in=lambda s, c: s in members,
        join=lambda s, e, c, role: members.add(s), title=lambda c: "Board prep"))
    out = client.post("/api/join-links", json={"target_kind": "chat", "target_id": sid}, headers=_h(HOST)).json()
    assert client.post("/api/join-links/redeem", json={"token": _token(out["url"])}, headers=_h(B)).status_code == 200
    v = _states(client, B, sid)
    assert v["chat"]["target"] == "dna" and v["chat"]["reads"] == ["dna"]


def test_in_a_shared_chat_only_its_owner_switches_the_profile(env):
    client, root, _ = env
    tok = _token(_link(client)["url"])
    client.post("/api/join-links/redeem", json={"token": tok}, headers=_h(B))
    client.post("/api/chat/target", json={"session": "pchat-o", "workspace": "dna"}, headers=_h(HOST))
    client.post("/api/chat", json={"prompt": "hi", "session": "pchat-o"}, headers=_h(HOST))
    shared = client.post("/api/sessions/pchat-o/participants", json={"subject": B},
                         headers=_h(HOST)).json()["session_id"]
    r = client.post("/api/workspace/profile", json={"name": "incognito", "session": shared}, headers=_h(B))
    assert r.status_code == 403 and "owner" in r.json()["detail"]


# ── founder 2026-10-15, live: "create a shared workspace and work in it — it should not read any of
#    my private data". The agent made it, the chat kept writing to Personal, and the agent told him
#    to go to another chat. A workspace made from a chat is where that chat works, in the same turn;
#    once it is shared, the chat reads only it.

def _turn(who, chat):
    return {**_h(who), "x-user-regime": "human", "x-user-delegation-unit": f"agent-{who}-chat-{chat}-g1"}


def _states(client, who, session):
    return client.get(f"/api/workspace/states?session={session}", headers=_h(who)).json()


def test_a_workspace_created_from_a_chat_is_where_that_chat_writes_in_the_same_turn(env):
    client, root, _ = env
    r = client.post("/api/workspace/create", json={"name": "FINOS research"}, headers=_turn(HOST, "pchat-f"))
    assert r.status_code == 201, r.text
    slug = r.json()["slug"]
    v = _states(client, HOST, "pchat-f")
    assert v["chat"]["target"] == slug and slug in v["chat"]["reads"]
    assert r.json()["say"].startswith("This chat now works in FINOS research")


def test_sharing_the_chats_workspace_makes_the_chat_read_only_it(env):
    client, root, _ = env
    slug = client.post("/api/workspace/create", json={"name": "FINOS research"}, headers=_turn(HOST, "pchat-g")).json()["slug"]
    # H3 (0.13.4 S4): the chat's agent cannot mint the link — it proposes; the person's own session mints
    r = client.post("/api/workspace/invite-link", json={"slug": slug}, headers=_turn(HOST, "pchat-g"))
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "invite_needs_the_person"
    r = client.post("/api/workspace/invite-link", json={"slug": slug}, headers=_h(HOST))
    assert r.status_code == 201, r.text
    wid = r.json()["workspace_id"]
    assert wid != slug                         # promoted to a shared workspace with a new id
    v = _states(client, HOST, "pchat-g")
    assert v["chat"]["target"] == wid and v["chat"]["reads"] == [wid], v["chat"]


def test_a_workspace_created_with_use_in_this_chat_false_leaves_the_chat_alone(env):
    client, root, _ = env
    r = client.post("/api/workspace/create", json={"name": "Side", "use_in_this_chat": False},
                    headers=_turn(HOST, "pchat-k"))
    assert r.status_code == 201, r.text
    assert _states(client, HOST, "pchat-k")["chat"]["target"] == "personal"
