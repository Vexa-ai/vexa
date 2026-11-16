"""``chat_on_join`` on ``POST /bots`` (#1961 s1): accepted, refused to unattended and shared turns, stored.

The line the bot posts once in the meeting chat when it is admitted arrives with the bot request and
is stored as ``data.chat_on_join = {text, request_id}`` (the send itself is s2). It is the chat verb
deferred to admission, so it is open to exactly the callers ``POST /bots/{p}/{n}/chat`` is open to:

* an unattended turn (a routine granted ``bot.request`` included) gets ``403 human_session_required``;
  the routine still sends the bot without the line;
* a shared turn and a workspace principal get ``403 shared_conversation``;
* the person's own key and a turn they watch get ``201``; ``null`` is absent.

A blank, non-string or over-long value, or one on a platform with no meeting chat, is ``422`` naming
the field, before any DB or runtime work. Logs carry the refusal reason, never the text.
"""
from __future__ import annotations

import logging
import re

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app, identity_token
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo
from meeting_api.bot_spawn.router import MAX_CHAT_ON_JOIN_CHARS
from meeting_api.collector.fakes import InMemoryTranscriptStore
from meeting_api.regime import REFUSAL, SHARED_REFUSAL

KEY = identity_token.generate_signing_key()
INTERNAL = "meeting-test-internal-secret"
USER = 7
PLAT, NATIVE = "google_meet", "abc-defg-hij"
LINE = "This call is being recorded by Vexa"
BODY = {"platform": PLAT, "native_meeting_id": NATIVE}
HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _signed(**delegation) -> dict:
    # The claims the gateway signs for a key (gateway-identity.v1), as the other regime tests mint them.
    claims = {"sub": str(USER), "scopes": ["bot", "tx"], "limits": 3, "workspaces": ["ws_1"],
              "writable_workspaces": ["ws_1"]}
    if delegation:
        claims["delegation"] = delegation
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


PERSON = _signed()
WATCHED = _signed(regime="human", workspaces="*", unit="agent-7-chat-s1")
GRANTED_ROUTINE = _signed(regime="autonomous", workspaces=["7"], unit="agent-7-chat-auto",
                          grants=["bot.request", "bot.stop", "flow.retire_self"], flow="user:7/bot-at-1213")
SHARED = _signed(regime="human", workspaces=["ws_1"], shared=True)
PRINCIPAL = {identity_token.HEADER: identity_token.sign(KEY, {
    "sub": "900", "scopes": ["bot", "tx"], "limits": 2, "workspaces": ["ws_1"],
    "writable_workspaces": ["ws_1"],
    "delegation": {"regime": "human", "workspaces": ["ws_1"], "shared": True,
                   "principal_kind": "workspace", "principal_workspace": "ws_1",
                   "on_behalf_of": "8", "bills_to": USER}})}
IN_WS = {**BODY, "workspace_id": "ws_1"}   # a shared turn's bot must land in a workspace it reads


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")   # the bot's meeting token is minted from it
    repo, runtime, store = InMemoryMeetingRepo(), FakeRuntimeClient(), InMemoryTranscriptStore()
    app = create_app(identity_key=KEY.public_key(), internal_secret=INTERNAL,
                     meeting_repo=repo, runtime=runtime, transcript_store=store)
    return TestClient(app, raise_server_exceptions=False), repo, runtime, store


def _post(client, headers, **fields):
    return client.post("/bots", headers=headers, json={**BODY, **fields})


def _nothing_happened(repo, runtime):
    assert repo._meetings == {}, "refused BEFORE the meeting row"
    assert runtime.specs == [], "nothing spawned"


# ── who may set it (T1) ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("regime", ["autonomous", "event", ""])
def test_an_unattended_turn_is_refused_chat_on_join(rig, regime):
    client, repo, runtime, _ = rig
    r = _post(client, _signed(regime=regime, workspaces=["7"], unit="agent-7-u"), chat_on_join=LINE)
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == REFUSAL
    assert r.json()["detail"]["reason"] == "human_session_required"
    _nothing_happened(repo, runtime)


def test_a_routine_granted_bot_request_is_refused_chat_on_join_but_sends_the_bot_without_it(rig):
    """`bot.request` sends the bot; it never opens the chat line (no grant does, `LIVE_BOT_VERBS`)."""
    client, repo, runtime, _ = rig
    r = _post(client, GRANTED_ROUTINE, chat_on_join=LINE)
    assert r.status_code == 403 and r.json()["detail"]["reason"] == "human_session_required", r.text
    _nothing_happened(repo, runtime)

    ok = _post(client, GRANTED_ROUTINE)
    assert ok.status_code == 201, ok.text
    assert "chat_on_join" not in ok.json()["data"]
    assert len(runtime.specs) == 1


def test_a_shared_turn_is_refused_chat_on_join(rig):
    client, repo, runtime, _ = rig
    r = client.post("/bots", headers=SHARED, json={**IN_WS, "chat_on_join": LINE})
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == SHARED_REFUSAL
    assert r.json()["detail"]["reason"] == "shared_conversation"
    _nothing_happened(repo, runtime)
    # the `POST /bots` exception for a shared turn still sends the bot, without the line
    assert client.post("/bots", headers=SHARED, json=IN_WS).status_code == 201


def test_a_workspace_principal_is_refused_chat_on_join(rig):
    client, repo, runtime, _ = rig
    r = client.post("/bots", headers=PRINCIPAL, json={**IN_WS, "chat_on_join": LINE})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["reason"] == "shared_conversation"
    _nothing_happened(repo, runtime)
    ok = client.post("/bots", headers=PRINCIPAL, json=IN_WS)
    assert ok.status_code == 201, ok.text
    assert ok.json()["user_id"] == USER       # the room's bot is the payer's, as before


@pytest.mark.parametrize("who", ["person", "watched_turn"])
def test_a_person_and_a_watched_turn_may_set_chat_on_join(rig, who):
    client, repo, runtime, _ = rig
    r = _post(client, {"person": PERSON, "watched_turn": WATCHED}[who], chat_on_join=LINE)
    assert r.status_code == 201, r.text
    stored = r.json()["data"]["chat_on_join"]
    assert stored["text"] == LINE
    assert HEX32.match(stored["request_id"]), stored
    assert set(stored) == {"text", "request_id"}, "no status until admission"
    assert repo._meetings[r.json()["id"]]["data"]["chat_on_join"] == stored
    assert len(runtime.specs) == 1


def test_chat_on_join_null_is_absent(rig):
    client, _, runtime, _ = rig
    for headers in (PERSON, GRANTED_ROUTINE):   # null never trips the refusal either
        r = _post(client, headers, chat_on_join=None, native_meeting_id=f"nul-{len(runtime.specs)}a-bcd")
        assert r.status_code == 201, r.text
        assert "chat_on_join" not in r.json()["data"]


def test_the_refusal_log_names_the_reason_and_never_the_text(rig, caplog):
    client, _, _, _ = rig
    with caplog.at_level(logging.INFO, logger="meeting_api.bot_spawn.router"):
        _post(client, GRANTED_ROUTINE, chat_on_join=LINE)
    assert "chat_on_join_refused reason=human_session_required" in caplog.text
    assert LINE not in caplog.text


# ── A1: stored on the meeting, read back where the meeting is read ───────────────────────────────


def test_the_stored_line_reads_back_on_get_meeting_and_bots_status(rig):
    client, repo, _, store = rig
    r = _post(client, PERSON, chat_on_join=LINE)
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    # The collector reads the same row (one `meetings` table in prod); the fakes are two dicts, so the
    # row's own `data` is seeded into the store and both projections are checked to keep the key.
    store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE, status="requested",
                       meeting_id=mid, data=repo._meetings[mid]["data"])

    detail = client.get(f"/meetings/{mid}", headers=PERSON)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["chat_on_join"]["text"] == LINE
    assert HEX32.match(detail.json()["data"]["chat_on_join"]["request_id"])

    status = client.get("/bots/status", headers=PERSON)
    assert status.status_code == 200, status.text
    [running] = status.json()["running"]
    assert running["id"] == mid
    assert running["data"]["chat_on_join"] == detail.json()["data"]["chat_on_join"]


def test_the_stored_text_is_stripped_and_the_cap_is_inclusive(rig):
    client, _, _, _ = rig
    r = _post(client, PERSON, chat_on_join=f"  {LINE}\n")
    assert r.status_code == 201 and r.json()["data"]["chat_on_join"]["text"] == LINE, r.text
    full = _post(client, PERSON, native_meeting_id="xyz-wxyz-xyz", chat_on_join="x" * MAX_CHAT_ON_JOIN_CHARS)
    assert full.status_code == 201, full.text


# ── A2: a bad value is 422 naming the field, and no bot starts ───────────────────────────────────


@pytest.mark.parametrize("value", ["", "   ", "\n\t", 42, True, ["hi"], {"text": "hi"},
                                   "x" * (MAX_CHAT_ON_JOIN_CHARS + 1)])
def test_a_blank_non_string_or_over_long_chat_on_join_is_422(rig, value):
    client, repo, runtime, _ = rig
    r = _post(client, PERSON, chat_on_join=value)
    assert r.status_code == 422, r.text
    assert "chat_on_join" in str(r.json()["detail"])
    _nothing_happened(repo, runtime)
    assert client.get("/bots/status", headers=PERSON).json()["running"] == []


@pytest.mark.parametrize("platform", ["voice_note", "vexa_call"])
def test_chat_on_join_on_a_platform_without_a_meeting_chat_is_422(rig, platform):
    client, repo, runtime, _ = rig
    r = client.post("/bots", headers=PERSON, json={"platform": platform, "chat_on_join": LINE})
    assert r.status_code == 422, r.text
    assert "chat_on_join" in str(r.json()["detail"])
    _nothing_happened(repo, runtime)


def test_a_bot_without_chat_on_join_stores_no_key(rig):
    client, repo, _, _ = rig
    r = _post(client, PERSON)
    assert r.status_code == 201, r.text
    assert "chat_on_join" not in r.json()["data"]
    assert "chat_on_join" not in repo._meetings[r.json()["id"]]["data"]


# ── a re-sent bot on a reused row: a fresh line and request_id, or the old one dropped ───────────


def test_a_resent_bot_on_a_reused_row_gets_a_fresh_line_or_drops_the_old_one(rig):
    client, repo, _, _ = rig
    first = _post(client, PERSON, chat_on_join=LINE).json()
    repo._meetings[first["id"]]["status"] = "completed"

    again = _post(client, PERSON, continue_meeting=True, chat_on_join="Second run, recorded too")
    assert again.status_code == 201 and again.json()["id"] == first["id"], again.text
    fresh = again.json()["data"]["chat_on_join"]
    assert fresh["text"] == "Second run, recorded too"
    assert fresh["request_id"] != first["data"]["chat_on_join"]["request_id"]
    assert "status" not in fresh

    repo._meetings[first["id"]]["status"] = "completed"
    plain = _post(client, PERSON, continue_meeting=True)
    assert plain.status_code == 201 and plain.json()["id"] == first["id"], plain.text
    assert "chat_on_join" not in plain.json()["data"]
    assert "chat_on_join" not in repo._meetings[first["id"]]["data"]
