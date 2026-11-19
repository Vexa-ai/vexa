"""A contact links to a person's PUBLISHED profile (R4, `public_profiles`, registry ADR § "a CRM record
may link to a public profile; it never copies it").

The contact then shows the profile's public fields LIVE (read from identity on every read) next to the
org's own private notes; a withdrawn profile shows nothing and the contact is the org's own data
again. Identity learns only a salted count. Nothing an org wrote ever reaches identity or another org.

Red tests: isolation (another org can never see this org's notes through a profile), withdraw, and
that the link is refused with the feature off."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from crm.api import Identity, create_app
from crm.profiles import ProfileClient
from test_store import store  # noqa: F401 — the fixture


class Identityd:
    """identity's two internal profile doors, and what crm-api sent to them."""

    def __init__(self):
        self.profiles = {"h-jonas-0123456789": {"handle": "h-jonas-0123456789", "fields": {
            "name": "Jonas Weber", "role": "Head of Workplace IT", "company": "NWB"}, "company_verified": None}}
        self.sent: list = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.headers.get("x-internal-secret") != "internal-test-only":
            return httpx.Response(403)
        if path == "/internal/validate":
            token = json.loads(request.content)["token"]
            return httpx.Response(200, json={"user_id": token})
        self.sent.append((request.method, path, request.content.decode() if request.content else ""))
        handle = path.split("/")[3]
        prof = self.profiles.get(handle)
        if request.method == "GET":
            return httpx.Response(200, json=prof) if prof else httpx.Response(404)
        return httpx.Response(200, json={"linked": True}) if prof else httpx.Response(404)


def _client(store, org, idd):
    transport = httpx.MockTransport(idd)
    identity = Identity("http://identity", "internal-test-only", transport)
    return TestClient(create_app(store, identity, org,
                                 profiles=ProfileClient("http://identity", "internal-test-only", transport)))


def _contact(client, name, notes):
    r = client.post("/change", headers={"X-API-Key": "owner"}, json={
        "action": "create", "object_type": "Company", "fields": {"name": name}, "narrative": notes,
        "reason": "test", "idempotency_key": f"k-{name}-{notes[:8]}"})
    assert r.status_code == 200, r.text
    return r.json()["record_id"]


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all,public_profiles")


def test_a_linked_contact_shows_the_public_fields_live_next_to_the_orgs_own_data(store, on):
    idd = Identityd()
    one = _client(store, "one", idd)
    rid = _contact(one, "Jonas Weber", "Pilot champion; prefers short written updates")
    h = {"X-API-Key": "owner"}
    assert one.post("/profile/link", headers=h, json={"record_id": rid, "handle": "h-jonas-0123456789"}).status_code == 200
    got = one.post("/profile/read", headers=h, json={"record_id": rid}).json()
    assert got["linked"] is True and got["profile"]["fields"]["role"] == "Head of Workplace IT"
    # live, not copied: identity changes the role, the next read shows it
    idd.profiles["h-jonas-0123456789"]["fields"]["role"] = "CIO"
    assert one.post("/profile/read", headers=h, json={"record_id": rid}).json()["profile"]["fields"]["role"] == "CIO"
    # the org's own record is untouched by the link: no profile field was written into it
    rec = one.post("/read", headers=h, json={"record_id": rid}).json()
    assert rec["fields"] == {"name": "Jonas Weber"} and "CIO" not in json.dumps(rec)


def test_withdrawing_the_profile_reverts_the_contact_to_the_orgs_own_data(store, on):
    idd = Identityd()
    one = _client(store, "one", idd)
    rid = _contact(one, "Jonas Weber", "Security review owner is Lena")
    h = {"X-API-Key": "owner"}
    one.post("/profile/link", headers=h, json={"record_id": rid, "handle": "h-jonas-0123456789"})
    del idd.profiles["h-jonas-0123456789"]                               # Jonas withdrew
    got = one.post("/profile/read", headers=h, json={"record_id": rid}).json()
    assert got["profile"] is None and got["withdrawn"] is True
    assert one.post("/read", headers=h, json={"record_id": rid}).json()["narrative"] == "Security review owner is Lena"


def test_isolation_one_org_never_sees_anothers_notes_through_a_profile(store, on):
    idd = Identityd()
    one, two = _client(store, "one", idd), _client(store, "two", idd)
    h = {"X-API-Key": "owner"}
    secret = "SECRET-NOTE pilot budget 40k, champion is leaving"
    rid1 = _contact(one, "Jonas Weber", secret)
    rid2 = _contact(two, "Jonas W.", "met at a meetup")
    assert one.post("/profile/link", headers=h, json={"record_id": rid1, "handle": "h-jonas-0123456789"}).status_code == 200
    assert two.post("/profile/link", headers=h, json={"record_id": rid2, "handle": "h-jonas-0123456789"}).status_code == 200
    seen_by_two = json.dumps(two.post("/profile/read", headers=h, json={"record_id": rid2}).json())
    assert "SECRET-NOTE" not in seen_by_two and "Jonas Weber" in seen_by_two      # the public name, from Jonas
    # org two cannot reach org one's record through the profile routes
    assert two.post("/profile/read", headers=h, json={"record_id": rid1}).status_code == 404
    assert two.post("/profile/link", headers=h, json={"record_id": rid1, "handle": "h-jonas-0123456789"}).status_code == 404
    # identity never received anything an org wrote: only the handle, the org key and the switch
    sent = json.dumps(idd.sent)
    assert "SECRET-NOTE" not in sent and "met at a meetup" not in sent and rid1 not in sent and rid2 not in sent


def test_linking_needs_a_published_profile_and_write_access(store, on):
    idd = Identityd()
    one = _client(store, "one", idd)
    rid = _contact(one, "Jonas Weber", "x")
    r = one.post("/profile/link", headers={"X-API-Key": "owner"}, json={"record_id": rid, "handle": "h-nobody-000000000"})
    assert r.status_code == 404
    r = one.post("/profile/link", headers={"X-API-Key": "reader"}, json={"record_id": rid, "handle": "h-jonas-0123456789"})
    assert r.status_code in (403, 404)


def test_unlinking_tells_identity_and_shows_only_the_orgs_data(store, on):
    idd = Identityd()
    one = _client(store, "one", idd)
    h = {"X-API-Key": "owner"}
    rid = _contact(one, "Jonas Weber", "x")
    one.post("/profile/link", headers=h, json={"record_id": rid, "handle": "h-jonas-0123456789"})
    assert one.post("/profile/link", headers=h, json={"record_id": rid, "handle": "h-jonas-0123456789", "linked": False}).status_code == 200
    assert one.post("/profile/read", headers=h, json={"record_id": rid}).json() == {"record_id": rid, "linked": False, "profile": None, "withdrawn": False}
    assert [json.loads(b)["linked"] for m, p, b in idd.sent if m == "POST"] == [True, False]


def test_the_profile_routes_refuse_with_the_feature_off(store, monkeypatch):
    monkeypatch.setenv("VEXA_FEATURES", "all")                           # explicit: `all` is not enough
    one = _client(store, "one", Identityd())
    r = one.post("/profile/read", headers={"X-API-Key": "owner"}, json={"record_id": "x"})
    assert r.status_code == 403 and r.json()["detail"]["feature"] == "public_profiles"
