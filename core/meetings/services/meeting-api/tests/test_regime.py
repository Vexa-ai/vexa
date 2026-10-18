"""The verbs that need a person in the loop, refused to a worker dispatched without one.

A worker's signed identity carries its dispatch's regime (gateway-identity.v1 `delegation`). Putting
a bot into a meeting, sharing a transcript, binding a meeting to a shared workspace and deleting a
meeting or a recording run for a person's own credential, the internal tier, and a worker in the
`human` regime — never for an unwatched one (`meeting_api/regime.py`).
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient

from meeting_api import create_app, identity_token
from meeting_api.regime import REFUSAL

KEY = identity_token.generate_signing_key()
INTERNAL = "meeting-test-internal-secret"

GATED = [
    ("POST", "/bots", {"platform": "google_meet", "native_meeting_id": "abc-defg-hij"}),
    ("DELETE", "/meetings/1", None),
    ("DELETE", "/meetings/google_meet/abc-defg-hij", None),
    ("DELETE", "/recordings/1", None),
    ("POST", "/meetings/1/share", {}),
    ("POST", "/meetings/google_meet/abc-defg-hij/share", {}),
    ("POST", "/meetings/google_meet/abc-defg-hij/workspace", {"workspace_id": "ws_1"}),
    # the owner's side of a share (#1801): seeing and changing who can read a meeting
    ("GET", "/meetings/1/access", None),
    ("PATCH", "/meetings/1/access", {"recording": True}),
    ("DELETE", "/meetings/1/share/g1", None),
    ("DELETE", "/meetings/1/viewers/8", None),
    # a voice note is spoken by the person: the recorder opens only where somebody is (#1783)
    ("POST", "/voice-notes/recorder", None),
]


@pytest.fixture
def client():
    # A route that gets past the gate may fail further in on this bare app (no admin token to mint a
    # bot's meeting token); that is the route answering, which is all these tests need to see.
    return TestClient(create_app(identity_key=KEY.public_key(), internal_secret=INTERNAL),
                      raise_server_exceptions=False)


def _signed(**delegation) -> dict:
    claims = {"sub": "7", "scopes": ["bot", "tx"], "limits": 3, "workspaces": ["ws_1"],
              "writable_workspaces": ["ws_1"]}
    if delegation:
        claims["delegation"] = delegation
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


def _send(client, method, path, body, headers):
    return client.request(method, path, headers=headers, json=body)


@pytest.mark.parametrize("method,path,body", GATED)
@pytest.mark.parametrize("regime", ["autonomous", "", "Human-ish"])
def test_an_unwatched_worker_is_refused(client, method, path, body, regime):
    r = _send(client, method, path, body,
              _signed(regime=regime, workspaces=["ws_1"], target="ws_1"))
    assert r.status_code == 403
    assert r.json()["detail"] == REFUSAL


@pytest.mark.parametrize("method,path,body", GATED)
def test_a_worker_with_its_person_in_the_chat_reaches_the_route(client, method, path, body):
    r = _send(client, method, path, body, _signed(regime="human", workspaces="*"))
    assert r.status_code != 403, r.text


@pytest.mark.parametrize("method,path,body", GATED)
def test_a_person_s_own_credential_and_the_internal_tier_reach_the_route(client, method, path, body):
    for headers in (_signed(), {"X-User-Id": "7", "X-Internal-Secret": INTERNAL,
                                        "X-User-Writable-Workspaces": "ws_1"}):
        r = _send(client, method, path, body, headers)
        assert r.status_code != 403, r.text


def test_reads_stay_open_to_an_unwatched_worker(client):
    r = client.get("/meetings", headers=_signed(regime="autonomous", workspaces=["ws_1"]))
    assert r.status_code == 200


@pytest.mark.parametrize("method,path,body", GATED)
def test_a_present_but_empty_regime_counts_as_delegated(client, method, path, body):
    """The rule agent-api's `ceiling.is_delegated` shares: the regime header present with nothing in
    it marks a delegated identity, and an empty regime is not `human`, so the verb is refused (the
    closed direction). The gateway never signs an empty regime; only the internal tier can send one."""
    headers = {"X-User-Id": "7", "X-Internal-Secret": INTERNAL, "x-user-regime": ""}
    r = _send(client, method, path, body, headers)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == REFUSAL


# ── ADR-0048 decision 4: a turn other people read reaches none of the person's meetings ───────
SHARED_READS = [
    ("GET", "/meetings", None),
    ("GET", "/meetings/1", None),
    # one meeting's transcript is decided by its workspace (tests/test_shared_transcript_read.py)
    ("GET", "/transcripts/search?q=budget", None),
    ("GET", "/recordings", None),
    ("GET", "/recordings/1", None),
    ("GET", "/bots/status", None),
    ("GET", "/meetings/google_meet/abc-defg-hij/participants", None),
] + GATED


@pytest.mark.parametrize("method,path,body", SHARED_READS)
def test_a_shared_turn_is_refused_the_person_s_meetings(client, method, path, body):
    from meeting_api.identity_token import SHARED_REFUSAL

    r = _send(client, method, path, body,
              _signed(regime="human", workspaces=["ws_1"], shared=True))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == SHARED_REFUSAL


def test_a_private_turn_still_reads_its_person_s_meetings(client):
    r = client.get("/meetings", headers=_signed(regime="human", workspaces="*"))
    assert r.status_code == 200


def test_the_shared_rule_also_guards_a_socket():
    """`refuse_shared` is installed on the app, so it runs on websocket routes too (a voice note's
    socket): a socket is served to a person and refused to a shared worker — never a TypeError."""
    from starlette.websockets import WebSocketDisconnect

    from meeting_api.regime import refuse_shared

    app = FastAPI(dependencies=[Depends(refuse_shared)])

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("ok")
        await socket.receive()      # until the client closes

    c = TestClient(app)
    with c.websocket_connect("/ws") as s:
        assert s.receive_text() == "ok"
    # what the identity door writes from a signed `delegation.shared` (the app has no door here)
    shared = {"x-user-regime": "human", "x-user-delegation-shared": "1"}
    with pytest.raises(WebSocketDisconnect) as refused:
        with c.websocket_connect("/ws", headers=shared) as s:
            s.receive_text()
    assert refused.value.code == 1008


# A BOT INTO THE SHARED WORKSPACE, FROM THE CHAT ITS MEMBERS SHARE (#1783, collaboration rehearsal
# 2026-10-14): the meeting is the workspace's, so every person in the chat reads it — allowed. A bot
# bound to a workspace outside the shared turn's ceiling, or to nobody's workspace, stays refused.
def test_a_shared_turn_may_send_a_bot_into_a_workspace_everyone_in_it_reads(client):
    from meeting_api.identity_token import SHARED_REFUSAL

    r = _send(client, "POST", "/bots",
              {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "workspace_id": "ws_1"},
              _signed(regime="human", workspaces=["ws_1"], shared=True))
    assert not (r.status_code == 403 and r.json().get("detail") == SHARED_REFUSAL), r.text


def test_a_shared_turn_may_not_send_a_bot_into_a_workspace_outside_its_ceiling(client):
    from meeting_api.identity_token import SHARED_REFUSAL

    r = _send(client, "POST", "/bots",
              {"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "workspace_id": "ws_1"},
              _signed(regime="human", workspaces=["ws_2"], shared=True))
    assert r.status_code == 403 and r.json()["detail"] == SHARED_REFUSAL, r.text


# ── a routine's delegated authority (flows phase 0, founder 2026-10-14) ─────────────────────────
# A routine its person made in chat to send a bot at 12:13 was refused here, and its run said done.
# Its person granted it `bot.request` when they made it; that grant, and only that grant, opens
# `POST /bots` to that routine's own unwatched turn.

def _routine(*grants) -> dict:
    return _signed(regime="autonomous", workspaces=["7"], grants=list(grants), flow="user:7/bot-at-1213")


def test_a_routine_granted_bot_request_reaches_post_bots(client):
    r = _send(client, "POST", "/bots", GATED[0][2], _routine("bot.request"))
    assert r.status_code != 403, r.text


@pytest.mark.parametrize("method,path,body", GATED[1:])
def test_the_bot_grant_opens_nothing_else(client, method, path, body):
    r = _send(client, method, path, body, _routine("bot.request", "bot.stop", "flow.retire_self"))
    assert r.status_code == 403 and r.json()["detail"] == REFUSAL


def test_a_routine_without_the_grant_is_still_refused(client):
    for headers in (_routine("flow.retire_self"),
                    _signed(regime="autonomous", workspaces=["7"], grants=["bot.request"])):
        r = _send(client, "POST", "/bots", GATED[0][2], headers)
        assert r.status_code == 403 and r.json()["detail"] == REFUSAL
