"""A turn with nobody in the loop cannot stop, reconfigure or speak through the person's live bot.

An inbound mail starts a turn (D25), anyone audible in a call shapes a meeting signal's turn, and a
routine acts on what it reads. Each runs on a delegation whose regime is not ``human``, and each
carries the ``bot`` scope. These verbs act on a meeting other people are in, right now, so
meeting-api refuses them to every such turn (`regime.LIVE_BOT_VERBS`):

* stop: refused unless the routine's person granted it ``bot.stop`` (flows phase 0);
* reconfigure: always refused;
* chat (and speak, when it has a backend): refused, since no signed opt-in for them is minted.

The person's own credential, the internal tier and a turn the person watches keep every verb.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from meeting_api import create_app, identity_token
from meeting_api.bot_spawn.fakes import InMemoryMeetingRepo
from meeting_api.lifecycle.stop_router import InMemoryCommandPublisher
from meeting_api.regime import LIVE_BOT_VERBS, REFUSAL

KEY = identity_token.generate_signing_key()
INTERNAL = "meeting-test-internal-secret"
USER = 7
PLAT, NATIVE = "google_meet", "abc-defg-hij"
CHAT = "agent-7-chat-s1"

STOP = ("DELETE", f"/bots/{PLAT}/{NATIVE}", None)
CONFIG = ("PUT", f"/bots/{PLAT}/{NATIVE}/config", {"language": "en"})
SEND = ("POST", f"/bots/{PLAT}/{NATIVE}/chat", {"text": "please stop recording"})
VERBS = {"stop": STOP, "config": CONFIG, "chat": SEND}


class _ListeningBus(InMemoryCommandPublisher):
    """The command bus with the meeting's bot subscribed: one receiver per publish."""

    async def publish(self, channel, message):
        await super().publish(channel, message)
        return 1


def _app():
    repo, bus = InMemoryMeetingRepo(), _ListeningBus()
    m = asyncio.run(repo.create_meeting(user_id=USER, platform=PLAT, native_meeting_id=NATIVE, data={}))
    sid = f"sess-{m['id']}"
    asyncio.run(repo.create_session(meeting_id=m["id"], session_uid=sid))
    asyncio.run(repo.update_meeting_status(session_uid=sid, status="active"))
    app = create_app(identity_key=KEY.public_key(), internal_secret=INTERNAL,
                     meeting_repo=repo, command_publisher=bus)
    return TestClient(app, raise_server_exceptions=False), bus


def _signed(**delegation) -> dict:
    claims = {"sub": str(USER), "scopes": ["bot", "tx"], "limits": 3, "workspaces": ["7"],
              "writable_workspaces": ["7"]}
    if delegation:
        claims["delegation"] = delegation
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


# What agent-api mints for each kind of turn (`dispatch.build_unit_env`, `delegation.mint_delegation`):
# a mail turn and a signal turn run least-privilege (no grants), a routine runs autonomous.
MAIL = _signed(regime="autonomous", workspaces=[], unit=f"{CHAT}-mail")
SIGNAL = _signed(regime="autonomous", workspaces=[], unit=f"{CHAT}-signal")
ROUTINE = _signed(regime="autonomous", workspaces=["7"], unit=f"{CHAT}-auto")
GRANTED_ROUTINE = _signed(regime="autonomous", workspaces=["7"], unit=f"{CHAT}-auto",
                          grants=["bot.request", "bot.stop", "flow.retire_self"],
                          flow="user:7/stop-at-1300")
WATCHED = _signed(regime="human", workspaces="*", unit=CHAT)
PERSON = _signed()
INTERNAL_TIER = {"X-User-Id": str(USER), "X-Internal-Secret": INTERNAL}

UNATTENDED = {"mail": MAIL, "signal": SIGNAL, "routine": ROUTINE}
ATTENDED = {"watched_turn": WATCHED, "person": PERSON, "internal_tier": INTERNAL_TIER}


def _send(client, verb, headers):
    method, path, body = VERBS[verb]
    return client.request(method, path, headers=headers, json=body)


@pytest.mark.parametrize("verb", sorted(VERBS))
@pytest.mark.parametrize("who", sorted(UNATTENDED))
def test_an_unattended_turn_cannot_act_on_the_live_bot(verb, who):
    client, bus = _app()
    r = _send(client, verb, UNATTENDED[who])
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == REFUSAL
    assert bus.published == [], "nothing may reach the bot's command channel"


@pytest.mark.parametrize("verb", ["config", "chat"])
def test_a_routine_granted_every_verb_still_cannot_reconfigure_or_chat(verb):
    """`bot.stop` is the only grant a live-bot verb honours; no grant opens config or chat."""
    client, bus = _app()
    r = _send(client, verb, GRANTED_ROUTINE)
    assert r.status_code == 403 and r.json()["detail"] == REFUSAL, r.text
    assert bus.published == []


def test_a_routine_its_person_granted_bot_stop_stops_the_bot():
    """Flows phase 0: "stop the bot at 13:00", granted by name when the person made the routine."""
    client, bus = _app()
    r = _send(client, "stop", GRANTED_ROUTINE)
    assert r.status_code < 300, r.text
    assert any('"leave"' in msg for _, msg in bus.published)


@pytest.mark.parametrize("verb", sorted(VERBS))
@pytest.mark.parametrize("who", sorted(ATTENDED))
def test_the_person_and_a_turn_they_watch_keep_every_verb(verb, who):
    client, bus = _app()
    r = _send(client, verb, ATTENDED[who])
    assert r.status_code < 300, r.text
    assert bus.published, "the act reached the bot's command channel"


# ── the guard: no mutating live-bot route ships without the regime check ─────────────────────────
LIVE_PREFIX = "/bots/{platform}/{native_meeting_id}"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _live_bot_routes(app) -> set:
    """Every (method, path) the app serves under the live bot's prefix, through included routers
    too (the walk `test_route_scopes._served` makes)."""
    def walk(routes):
        out = set()
        for r in routes:
            if getattr(r, "include_context", None) is not None:
                out |= walk(r.original_router.routes)
            elif isinstance(r, APIRoute) and r.path.startswith(LIVE_PREFIX):
                out |= {(m, r.path) for m in (r.methods or set()) & MUTATING}
        return out
    return walk(app.routes)


def test_every_mutating_live_bot_route_is_in_the_guarded_table():
    client, _ = _app()
    routes = _live_bot_routes(client.app)
    assert {k for k in LIVE_BOT_VERBS if k in routes} >= {
        ("DELETE", LIVE_PREFIX), ("PUT", LIVE_PREFIX + "/config"), ("POST", LIVE_PREFIX + "/chat")}
    unguarded = routes - set(LIVE_BOT_VERBS)
    assert not unguarded, (
        f"{sorted(unguarded)} act on a live bot and are not in regime.LIVE_BOT_VERBS: add them with "
        "`dependencies=[Depends(live_bot_guard(method, path))]`")


def test_every_mutating_live_bot_route_refuses_a_mail_turn():
    """Behavioural, over whatever the app serves: a route listed in the table but not wired to its
    guard is caught here too."""
    client, _ = _app()
    routes = _live_bot_routes(client.app)
    assert routes, "the walk found no live-bot route: it would pass vacuously"
    for method, path in sorted(routes):
        url = path.replace("{platform}", PLAT).replace("{native_meeting_id}", NATIVE)
        url = url.replace("{request_id}", "r1")
        r = client.request(method, url, headers=MAIL, json={"text": "x", "language": "en"})
        assert r.status_code == 403 and r.json().get("detail") == REFUSAL, (method, path, r.text)
