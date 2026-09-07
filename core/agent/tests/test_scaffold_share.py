"""R-A08 — the transcript share stops travelling in the link's query string.

`core/agent/worker/engine.py` states the rule one file away, about the MCP delegation token: *"THE
TOKEN TRAVELS IN A HEADER, never in the URL… a credential in a query string leaks into every access
log and proxy trace it passes through"*. The scaffold mail was the weaker spelling of the same rule
on the MORE exposed artefact — `{ui}/?s=<id>&tshare=<share token>` crosses a public hostname, a
recipient's mail provider, and whoever they forward it to.

So the share is delivered by REDEMPTION BOUND TO THE SCAFFOLD ID: the record holds it, and the
recipient asks for it once over an authenticated request. The link is an id and nothing else — which
is what `test_the_url_carries_the_id_and_nothing_else` in `test_scaffold.py` already claimed, while
one test below it pinned the exception.

L2 throughout: a real FastAPI app over fakes, no redis, no runtime, no claude.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_scaffold import (  # the fixtures this row shares with the rest of the scaffold suite
    INTERNAL, _as, _mint, client, stack,  # noqa: F401 — pytest fixtures by name
)


def _params(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


PRIYA = _as("priya@acme.test", "u_priya")
STRANGER = _as("mallory@elsewhere.test", "u_mallory")


# ── the link ─────────────────────────────────────────────────────────────────────────────────────

def test_the_link_carries_no_share_token(client):
    """The whole row in one assertion: an id, and nothing that is a credential."""
    url = _mint(client, share_token="97.tok-for-priya").json()["url"]
    assert set(_params(url)) == {"s"}
    assert "97.tok-for-priya" not in url


def test_the_link_is_still_the_same_link_when_there_is_no_share(client):
    url = _mint(client).json()["url"]
    assert set(_params(url)) == {"s"}


# ── the redemption ───────────────────────────────────────────────────────────────────────────────

def test_the_recipient_redeems_the_share_against_the_scaffold_id(client):
    sid = _mint(client, share_token="97.tok-for-priya").json()["id"]
    r = client.post(f"/api/scaffolds/{sid}/share", headers=PRIYA)
    assert r.status_code == 200, r.text
    assert r.json()["token"] == "97.tok-for-priya"


def test_a_scaffold_with_no_share_answers_null_not_an_error(client):
    """A null token is a fact, not a failure: most scaffolds are about the reader's own meeting and
    carry no capability at all. A 404 here would make the client treat the ordinary case as broken."""
    sid = _mint(client).json()["id"]
    r = client.post(f"/api/scaffolds/{sid}/share", headers=PRIYA)
    assert r.status_code == 200, r.text
    assert r.json()["token"] is None


def test_a_stranger_gets_no_token_and_cannot_learn_the_scaffold_exists(client):
    sid = _mint(client, share_token="97.tok-for-priya").json()["id"]
    r = client.post(f"/api/scaffolds/{sid}/share", headers=STRANGER)
    assert r.status_code == 404
    assert "97.tok-for-priya" not in r.text


def test_an_admin_may_read_the_record_and_may_never_hold_the_capability(client, monkeypatch):
    """The read route admits the instance admin — a scaffold is a support surface. The SHARE is not:
    it is a bearer grant on somebody else's meeting transcript, and 'can debug this record' is not
    'may watch this meeting'."""
    from control_plane import global_layer
    monkeypatch.setattr(global_layer, "is_admin", lambda *a, **k: True)
    sid = _mint(client, share_token="97.tok-for-priya").json()["id"]
    admin = _as("root@acme.test", "u_admin")
    assert client.get(f"/api/scaffolds/{sid}", headers=admin).status_code == 200
    r = client.post(f"/api/scaffolds/{sid}/share", headers=admin)
    assert r.status_code == 404
    assert "97.tok-for-priya" not in r.text


def test_the_internal_tier_cannot_pull_a_share_back_out(client):
    """The mint hands the token IN. Nothing hands it back out to the service tier — a flow that
    needed it already had it, and a route that returned it would be a second way to reach it."""
    sid = _mint(client, share_token="97.tok-for-priya").json()["id"]
    r = client.post(f"/api/scaffolds/{sid}/share", headers={"X-Internal-Secret": INTERNAL})
    assert r.status_code in (401, 404)
    assert "97.tok-for-priya" not in r.text


def test_a_reload_is_not_a_second_redemption(client):
    """Idempotent FOR THE SAME RECIPIENT, and deliberately so — the same reasoning `redeem` already
    applies to `redeemed_at`. Making it strictly single-use would turn one dropped response into a
    person permanently unable to see the meeting they were invited to, which is a worse failure than
    the one the row is about; the property that matters is that the token is never in a URL and is
    only ever handed to the identity the record is bound to."""
    sid = _mint(client, share_token="97.tok-for-priya").json()["id"]
    first = client.post(f"/api/scaffolds/{sid}/share", headers=PRIYA).json()["token"]
    second = client.post(f"/api/scaffolds/{sid}/share", headers=PRIYA).json()["token"]
    assert first == second == "97.tok-for-priya"


def test_the_hand_out_is_recorded_on_the_record(client):
    """So an operator can answer 'did this person ever get the capability' without guessing."""
    from control_plane.api import create_app  # noqa: F401 — the store is the app's; read it back
    sid = _mint(client, share_token="97.tok-for-priya").json()["id"]
    client.post(f"/api/scaffolds/{sid}/share", headers=PRIYA)
    rec = client.get(f"/api/scaffolds/{sid}", headers=PRIYA).json()
    assert rec.get("share_handed_at")


def test_the_record_never_shows_the_token_on_the_read_route(client):
    """The read is the surface a panel polls. A capability that rides every read is a capability in
    every log the panel's traffic touches — the shape this row exists to remove."""
    sid = _mint(client, share_token="97.tok-for-priya").json()["id"]
    body = client.get(f"/api/scaffolds/{sid}", headers=PRIYA).text
    assert "97.tok-for-priya" not in body
