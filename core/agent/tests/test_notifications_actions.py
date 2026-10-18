"""Notifications a person can ACT on, in one tap (#1783).

Founder, 2026-10-13: notifications "work but are not actionable" — an invite he could not accept
from its own notification. Each row now carries its `actions`; `POST /api/notifications/act` does one
of them as the person it was sent to (the same doors as the join page), marks the row read, records
what was done (`state`), and answers what to open next. `notifications_act` is the same route as an
agent tool. Meeting facts (a meeting shared with you, a bot scheduled for or joined to your meeting)
arrive from flows on the internal tier and are written by the same one writer.

L2: FastAPI over a sqlite session store, real git workspaces, a list for a redis bus."""
from __future__ import annotations

import json

from control_plane import workspace_membership as m
from tests.test_notifications_routes import (  # noqa: F401 — `env` is a fixture
    ALICE, DAVE, JOE, MAIL, SECRET, _h, _mine, _share, env)


def _invite(client, who=DAVE):
    r = client.post("/api/workspace/invite", json={"slug": "acme", "email": MAIL[who], "role": "reader"},
                    headers=_h(ALICE))
    assert r.status_code == 200, r.text
    [n] = [x for x in _mine(client, who)["notifications"] if x["kind"] == "workspace_invited"]
    return n


def _act(client, who, nid, action, **extra):
    return client.post("/api/notifications/act", json={"id": nid, "action": action}, headers=_h(who, **extra))


# ── a workspace invite: Accept opens the workspace, Decline revokes it ──────────────────────────
def test_an_invite_notice_offers_accept_decline_open(env):
    client, *_ = env
    n = _invite(client)
    assert n["actions"] == ["accept", "decline", "open"]
    assert "state" not in n


def test_accept_from_the_notice_makes_them_a_member_and_answers_the_workspace_to_open(env):
    client, _, bus, root = env
    n = _invite(client)
    bus.clear()
    r = _act(client, DAVE, n["id"], "accept")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["open"]["kind"] == "workspace" and body["open"]["id"] == "acme"
    assert body["notification"]["state"] == "accepted" and body["notification"]["read"] is True
    assert body["notification"]["actions"] == ["open"]
    assert m.is_member(root, "acme", DAVE) is not None
    # the inviter is told, and every tab of the invitee sees the row flip
    assert [x["kind"] for x in _mine(client, ALICE)["notifications"]] == ["invite_accepted"]
    ups = [f for f in bus.to(DAVE) if f["type"] == "notification.update"]
    assert ups and ups[-1]["notification"]["state"] == "accepted" and ups[-1]["unread"] == 0


def test_decline_from_the_notice_revokes_the_invite_and_tells_the_inviter(env):
    client, *_ = env
    n = _invite(client)
    r = _act(client, DAVE, n["id"], "decline")
    assert r.status_code == 200, r.text
    assert r.json()["open"] is None and r.json()["notification"]["state"] == "declined"
    assert r.json()["notification"]["actions"] == []
    assert [x["kind"] for x in _mine(client, ALICE)["notifications"]] == ["invite_declined"]
    # the row holds no token (M2): the invite is gone, so accepting it again from the notice is refused
    assert "url" not in n["object"]
    assert _act(client, DAVE, n["id"], "accept").status_code == 409


def test_nobody_acts_on_another_persons_notice(env):
    client, *_ = env
    n = _invite(client)
    assert _act(client, JOE, n["id"], "accept").status_code == 404
    assert _act(client, ALICE, n["id"], "decline").status_code == 404


def test_an_act_the_notice_does_not_offer_is_refused(env):
    client, *_ = env
    n = _invite(client)
    r = _act(client, DAVE, n["id"], "join")
    assert r.status_code == 409 and r.json()["detail"]["refused"] == "not_an_action"
    _act(client, DAVE, n["id"], "accept")
    assert _act(client, DAVE, n["id"], "accept").status_code == 409


def test_a_routine_cannot_accept_an_invite(env):
    client, *_ = env
    n = _invite(client)
    # a worker dispatched with no person in the loop (the regime the identity door writes)
    r = _act(client, DAVE, n["id"], "accept", **{"x-user-regime": "autonomous", "x-internal-secret": SECRET})
    assert r.status_code == 403, r.text
    assert _act(client, DAVE, n["id"], "accept", **{"x-user-regime": "human", "x-internal-secret": SECRET}
                ).status_code == 200


# ── a chat: Open lands on the chat (a mention, at its message) ──────────────────────────────────
def test_open_on_a_chat_notice_answers_the_chat_and_marks_it_read(env):
    client, store, *_ = env
    sid = _share(client, store)
    [n] = _mine(client, JOE)["notifications"]
    assert n["actions"] == ["open"]
    r = _act(client, JOE, n["id"], "open")
    assert r.status_code == 200, r.text
    assert r.json()["open"] == {"kind": "chat", "id": sid, "name": "Board prep"}
    assert _mine(client, JOE)["unread"] == 0


# ── meeting facts, from flows on the internal tier ──────────────────────────────────────────────
def _post_internal(client, secret=SECRET, **body):
    return client.post("/internal/notifications", json=body,
                       headers={"x-internal-secret": secret} if secret else {})


def test_the_internal_door_needs_the_internal_secret(env):
    client, *_ = env
    assert _post_internal(client, secret="", kind="bot_joined", to=JOE, meeting_id="7",
                          title="Weekly").status_code == 403
    assert _post_internal(client, secret="wrong", kind="bot_joined", to=JOE, meeting_id="7",
                          title="Weekly").status_code == 403


def test_a_meeting_shared_with_an_address_reaches_its_account_with_open_transcript(env):
    client, _, bus, _ = env
    r = _post_internal(client, kind="meeting_shared", email=MAIL[JOE].upper(), actor=ALICE,
                       actor_name="Alice", meeting_id="42", title="Weekly sync", url="/?tshare=abc")
    assert r.status_code == 200 and r.json()["delivered"] == 1, r.text
    [n] = _mine(client, JOE)["notifications"]
    assert n["kind"] == "meeting_shared" and n["object"] == {"kind": "meeting", "id": "42", "name": "Weekly sync",
                                                              "url": "/?tshare=abc"}
    assert n["text"] == "Alice shared the meeting “Weekly sync” with you" and n["actions"] == ["open"]
    r = _act(client, JOE, n["id"], "open")
    assert r.json()["open"] == {"kind": "meeting", "id": "42", "name": "Weekly sync", "url": "/?tshare=abc"}


def test_an_address_with_no_account_is_told_nothing_and_that_is_not_an_error(env):
    client, *_ = env
    r = _post_internal(client, kind="meeting_shared", email="nobody@else.test", actor=ALICE,
                       actor_name="Alice", meeting_id="42", title="Weekly")
    assert r.status_code == 200 and r.json()["delivered"] == 0


def test_bot_scheduled_and_joined_reach_the_meetings_owner(env):
    client, *_ = env
    assert _post_internal(client, kind="bot_scheduled", to=JOE, meeting_id="cal-1", title="Design review",
                          object_kind="none").json()["delivered"] == 1
    assert _post_internal(client, kind="bot_joined", to=JOE, meeting_id="43", title="Design review").json()[
        "delivered"] == 1
    got = _mine(client, JOE)["notifications"]
    assert [x["kind"] for x in got] == ["bot_joined", "bot_scheduled"]
    assert got[0]["text"] == "Vexa joined “Design review”" and got[0]["object"]["kind"] == "meeting"
    assert got[1]["text"] == "Vexa will join “Design review” when it starts"


def test_the_internal_door_writes_meeting_kinds_only_and_never_a_link_off_this_app(env):
    client, *_ = env
    assert _post_internal(client, kind="chat_added", to=JOE, meeting_id="1", title="x").status_code == 422
    assert _post_internal(client, kind="bot_joined", to=JOE, meeting_id="1", title="x",
                          url="https://evil.example/x").status_code == 422
    assert _post_internal(client, kind="bot_joined", meeting_id="1", title="x").status_code == 422


def test_meeting_notices_carry_the_title_only_never_content(env):
    client, *_ = env
    _post_internal(client, kind="bot_joined", to=JOE, meeting_id="43", title="A\nB" + "x" * 300)
    [n] = _mine(client, JOE)["notifications"]
    assert "\n" not in n["text"] and len(n["object"]["name"]) <= 80
    assert json.dumps(n).count("x" * 100) == 0
