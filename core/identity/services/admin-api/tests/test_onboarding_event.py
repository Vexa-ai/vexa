"""`onboarding.completed` — published by IDENTITY, once, at the one point a person enters.

Founder ruling, 2026-09-02: this event triggers billing on the paid product. That makes it a
contract rather than a convenience, and it pins five things at once — one producer, an exact
payload, exactly-once, every configuration, and no agent code on the path.

WHY HERE AND NOWHERE ELSE. Five independent paths onboard a person today: the control MCP's sign-in
verbs, its OAuth door, its shared `account_for` helper, the terminal's own auth, and the flows mail
door when an invite arrives from a stranger. None of them published anything, and three seeded a
desk inline. They look like five places to add an event — and they are not, because ALL FIVE create
the account through `POST /admin/users`. The single point they already share is where the fact
belongs, so nothing else had to be refactored to make this true.

FIRE-AND-FORGET, AND A PUBLISH EDGE IS NOT A DEPENDENCY. Identity does not call flows; it tells
flows. The publish is best-effort, bounded, and swallowed: a deployment with no flows domain still
onboards people, and so does one where flows is simply down. What may never happen is the reverse —
onboarding completing without the event — because that is a person who is signed in and has no seat.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from admin_api.app import db as app_db
from admin_api.app import events as events_mod
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker


@pytest.fixture()
def published(monkeypatch):
    """Every event identity published, recorded at the seam. No network."""
    sent = []

    def fake(event_type, source_event_id, subject_refs, **kw):
        sent.append({"event_type": event_type, "source_event_id": source_event_id,
                     "subject_refs": subject_refs})
        return True

    monkeypatch.setattr(events_mod, "publish", fake)
    return sent


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
    sync_engine = create_engine(pg_url)
    Base.metadata.drop_all(sync_engine)
    ensure_schema_sync(sync_engine, Base)
    sync_engine.dispose()
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


def _create(client, email):
    return client.post("/admin/users", headers=_admin(), json={"email": email})


# ── the fact, and its shape ──────────────────────────────────────────────────────────────────
def test_creating_a_person_publishes_onboarding_completed(client, published):
    r = _create(client, "new@vexa.ai")
    assert r.status_code in (200, 201), r.text
    assert [e["event_type"] for e in published] == ["onboarding.completed"]


def test_the_payload_is_subject_org_and_seat(client, published):
    """The founder's three fields. `seat` is what a billing domain charges for; `org` is what it
    charges. Both are stated rather than left for a consumer to infer, because a consumer that
    infers them is a second place the answer lives."""
    uid = _create(client, "shape@vexa.ai").json()["id"]
    refs = published[0]["subject_refs"]
    assert set(refs) >= {"subject", "org", "seat"}
    assert str(refs["subject"]) == str(uid)


def test_org_is_present_and_empty_because_identity_holds_no_org(client, published):
    """A CHARACTERISATION TEST, deliberately — it pins behaviour that was already right by
    accident and makes it right on purpose.

    Identity has no organisation concept: no column, no create field, nothing. The publisher used
    to read `u.data.get("org")` on the dict assigned two lines above it, so the value was never
    anything but None while LOOKING like a lookup — which is the worst version of this, because it
    reads as though somebody checked, and the next person to need an org would have gone looking
    for where it was set. The ref is emitted EMPTY rather than omitted, because a consumer that
    finds the key missing cannot tell "identity has no org" from "identity did not look", and the
    one that cannot tell will infer one from the email domain."""
    _create(client, "noorg@corp.example.com")
    refs = published[0]["subject_refs"]
    assert refs["org"] == "", "an org appeared from somewhere identity does not have one"
    assert refs["seat"] == "member", "every person through this door gets the same seat"


def test_the_source_event_id_is_the_subject_so_a_replay_dedupes(client, published):
    """flows admits on (source_event_id, flow). Keying the id to the person means a re-delivery of
    this fact is a no-op there as well as here — belt and braces, because a double charge is not a
    thing you can take back with an apology."""
    uid = _create(client, "dedupe@vexa.ai").json()["id"]
    assert str(uid) in published[0]["source_event_id"]


# ── exactly once ─────────────────────────────────────────────────────────────────────────────
def test_a_returning_person_publishes_nothing(client, published):
    """Sign-in is not onboarding. Every one of the five paths looks a person up before creating
    them, so without a stamp the second sign-in of the day would bill them again."""
    _create(client, "twice@vexa.ai")
    published.clear()
    assert client.get("/admin/users/email/twice@vexa.ai", headers=_admin()).status_code == 200
    assert published == []


def test_the_stamp_is_recorded_on_the_person(client, published):
    uid = _create(client, "stamped@vexa.ai").json()["id"]
    row = client.get(f"/admin/users/{uid}", headers=_admin()).json()
    assert row["data"].get("onboarding_completed_at"), "nothing records that this fact was emitted"


def test_a_second_create_for_the_same_person_publishes_nothing(client, published):
    """THE GUARD IS THE CREATE PATH, and it is worth being exact about which half does what,
    because the two are easy to swap and only one of them is load-bearing at publish time.

    The GUARD is this route's own case-folded email lookup: a second POST for the same address
    returns the existing person and never reaches the publish. The STAMP is not a guard — nothing
    consults it before publishing — it is the durable RECORD that the fact was emitted, written in
    the same transaction as the account so a publish that never landed can be swept and replayed
    from it, and so that a second producer added later has something to consult instead of
    guessing. Saying the stamp is the guarantee would be a claim this file does not prove."""
    uid = _create(client, "again@vexa.ai").json()["id"]
    published.clear()
    r = client.post("/admin/users", headers=_admin(), json={"email": "again@vexa.ai"})
    assert r.status_code in (200, 201, 409)
    assert published == [], "the same person was onboarded twice"
    assert client.get(f"/admin/users/{uid}", headers=_admin()).status_code == 200


def test_the_stamp_is_not_rewritten_when_the_same_person_comes_back(client, published):
    """It records WHEN onboarding completed, so a second sign-in must not move it. A stamp that
    slides forward on every visit is a timestamp of the last sign-in wearing the name of the
    first, and any sweep or billing question asked of it gets a confidently wrong answer."""
    uid = _create(client, "stable@vexa.ai").json()["id"]
    first = client.get(f"/admin/users/{uid}", headers=_admin()).json()["data"]["onboarding_completed_at"]
    client.post("/admin/users", headers=_admin(), json={"email": "stable@vexa.ai"})
    again = client.get(f"/admin/users/{uid}", headers=_admin()).json()["data"]["onboarding_completed_at"]
    assert again == first, "the onboarding stamp moved on a return visit"


def test_the_carrier_the_route_publishes_is_the_one_identity_owns_in_the_census(client, published):
    """The publisher and the carrier census are two halves of one fact. gate:config-contract checks
    the declaration against the census; this checks the CODE against it, so the event name cannot
    drift from the registry through a path no gate reads."""
    import json as _json
    import pathlib as _pathlib
    census = _pathlib.Path(__file__).resolve().parents[5] / "core/flows/contracts/flows.v1/carriers.json"
    owners = {c["event"]: c for c in _json.loads(census.read_text())["carriers"]}
    _create(client, "census@vexa.ai")
    event = published[0]["event_type"]
    assert event in owners, f"{event} is published by identity and registered nowhere"
    assert owners[event]["owner"] == "identity"
    assert set(published[0]["subject_refs"]) >= set(owners[event]["refs"]), \
        "the payload omits a ref the census promises a consumer"


# ── it fires in every configuration ──────────────────────────────────────────────────────────
def test_it_fires_with_no_flows_domain_and_no_agent_domain(client, monkeypatch):
    """THE POINT OF MOVING IT HERE. The publish is attempted whatever else is deployed; with no
    flows it is dropped, and onboarding still completes. No agent code is on the path at all."""
    monkeypatch.delenv("FLOWS_API_URL", raising=False)
    monkeypatch.delenv("AGENT_API_URL", raising=False)
    r = _create(client, "alone@vexa.ai")
    assert r.status_code in (200, 201), r.text
    uid = r.json()["id"]
    assert client.get(f"/admin/users/{uid}", headers=_admin()).json()["data"] \
        .get("onboarding_completed_at"), "the fact was not recorded"


def test_a_publish_that_fails_never_fails_the_person(client, monkeypatch):
    """A publish edge is not a dependency. Identity tells flows; it does not ask it."""
    def boom(*a, **kw):
        raise RuntimeError("flows is down")

    monkeypatch.setattr(events_mod, "publish", boom)
    assert _create(client, "resilient@vexa.ai").status_code in (200, 201)


def test_the_publisher_itself_swallows_everything(monkeypatch):
    """Asserted on the publisher, not only through the route: the next caller of `publish` gets the
    same guarantee without having to know to wrap it."""
    monkeypatch.setenv("FLOWS_API_URL", "http://127.0.0.1:9")   # nothing listens
    assert events_mod.publish("onboarding.completed", "x", {"subject": "1"}) is False
