"""A person's presence opt-out is read from IDENTITY, per request, failing closed (#1783).

`POST /api/home/presence` used to record a `presence:on|off` event in the home's log, and the home's
people strip and the desktop read it back. The choice is a fact about the person, so identity keeps
it (`users.data.person_settings.presence`, `GET /internal/presence`); agent-api:

  * writes it there when the person switches it — with their own credential only, never a worker's;
  * reads it per request, briefly cached and batched, and treats "identity cannot answer" as OFF:
    a colleague's dot is never shown on a guess;
  * carries the old event over once: a person whose latest home event says off and who never chose
    in identity is written off there, so nobody who opted out reappears;
  * no longer writes the home event.

L2: the home router over a sqlite session store; identity is the in-memory fake every agent test
gets from `conftest.py` (`presence_identity`). No docker, no network.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from control_plane import home, identity_presence
from control_plane.identity_token import DELEGATION_HEADERS
from control_plane.routers import home as home_router
from control_plane.routers.home import presence_switched_off
from control_plane.session_store import SessionStore
from tests._session_fakes import MemBlobs


@pytest.fixture
def store(tmp_path):
    s = SessionStore.open(f"sqlite:///{tmp_path / 's.db'}", MemBlobs())
    yield s
    s.close()


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(home_router.build(subject_of=lambda r: r.headers["x-user-id"], session_store=store))
    return TestClient(app)


def _off(store, who):
    return presence_switched_off(home.HomeStore(store), who)


# ── the read ────────────────────────────────────────────────────────────────────────────────────

def test_identity_off_hides_and_on_or_never_chosen_shows(store, presence_identity):
    presence_identity.choices.update({"7": False, "8": True, "9": None})
    assert _off(store, "7") is True
    assert _off(store, "8") is False
    assert _off(store, "9") is False


def test_a_person_identity_does_not_know_is_hidden(store, presence_identity):
    presence_identity.choices.update({"7": True})
    presence_identity.known_only = True
    assert _off(store, "999") is True


def test_identity_unreachable_hides(store, presence_identity):
    presence_identity.choices.update({"8": True})
    presence_identity.down = True
    assert _off(store, "8") is True


def test_identity_not_configured_hides(store, monkeypatch):
    monkeypatch.setattr(identity_presence, "_transport", identity_presence._http)
    identity_presence.configure(None)
    identity_presence.reset()
    assert _off(store, "8") is True


def test_one_desktop_read_asks_identity_once_for_its_members(presence_identity):
    presence_identity.choices.update({"1": True, "2": False, "3": None})
    got = identity_presence.prefetch(["1", "2", "3", "2"])
    assert got == {"1": True, "2": False, "3": None}
    assert presence_identity.reads == [["1", "2", "3"]]
    for who in ("1", "2", "3"):
        identity_presence.choice(who)
    assert len(presence_identity.reads) == 1, "a cached answer is not asked again within the TTL"


# ── the carry-over from the home event ─────────────────────────────────────────────────────────

def test_an_old_opt_out_carries_over_once_and_stays_off(store, presence_identity):
    presence_identity.choices.update({"7": None})
    home.HomeStore(store).record("7", card="presence:off", event="evidence")
    assert _off(store, "7") is True
    assert presence_identity.writes == [("7", {"presence": False})]
    identity_presence.reset()
    assert _off(store, "7") is True
    assert presence_identity.writes == [("7", {"presence": False})], "carried over once, not on every read"


def test_a_choice_made_in_identity_wins_over_the_old_event(store, presence_identity):
    presence_identity.choices.update({"7": True})
    home.HomeStore(store).record("7", card="presence:off", event="evidence")
    assert _off(store, "7") is False
    assert presence_identity.writes == []


def test_an_old_opt_out_stays_hidden_even_when_the_carry_over_write_fails(store, presence_identity):
    presence_identity.choices.update({"7": None})
    presence_identity.refuse_writes = True
    home.HomeStore(store).record("7", card="presence:off", event="evidence")
    assert _off(store, "7") is True


# ── the write ───────────────────────────────────────────────────────────────────────────────────

def test_switching_presence_writes_identity_and_no_home_event(client, store, presence_identity):
    presence_identity.choices.update({"7": None})
    r = client.post("/api/home/presence", headers={"x-user-id": "7"}, json={"visible": False})
    assert r.status_code == 200, r.text
    assert r.json() == {"visible": False}
    assert presence_identity.writes == [("7", {"presence": False})]
    assert home.HomeStore(store).last_of("7", ("presence:on", "presence:off")) is None
    assert _off(store, "7") is True, "the next read sees the new choice, not a stale cache"
    assert client.post("/api/home/presence", headers={"x-user-id": "7"}, json={"visible": True}).status_code == 200
    assert _off(store, "7") is False


@pytest.mark.parametrize("regime", ["human", "autonomous", ""])
def test_a_worker_never_switches_a_persons_presence(client, presence_identity, regime):
    h = {"x-user-id": "7", DELEGATION_HEADERS["regime"]: regime}
    r = client.post("/api/home/presence", headers=h, json={"visible": False})
    assert r.status_code == 403, r.text
    assert presence_identity.writes == []


def test_switching_presence_when_identity_is_down_says_so(client, presence_identity):
    presence_identity.down = True
    r = client.post("/api/home/presence", headers={"x-user-id": "7"}, json={"visible": False})
    assert r.status_code == 503, r.text
