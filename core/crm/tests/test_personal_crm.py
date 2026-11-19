"""An individual account's personal CRM (architecture A1; registry ADR "individuals have their own CRM").

- OFF (the default, and under ``all``): every caller is served from the install org, exactly as
  before, and identity is never asked for an org.
- ON (``personal_crm`` named): a person whose home org in identity is THEIR org of one is served from
  that org's rows only. The company's records are unreachable from it, and its records are
  unreachable from the company, both ways and for an instance admin too.
- Identity decides; a caller cannot choose an org, and an identity that cannot answer is a 503, never
  a fallback to the company's CRM.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from crm import features, migrate
from crm import schema as s
from crm.api import Identity, create_app
from crm.bootstrap import bootstrap_org
from crm.orgs import PERSONAL_POLICY, OrgResolver
from test_store import store  # noqa: F401 — the sqlite fixture

INSTALL = "one"
SOLO = "777"          # an individual account (identity user ids are numbers)
SOLO_ORG = f"p-{SOLO}"
INTERNAL = "internal-test-only"


def _identity_transport(orgs_asked, *, org_status=200):
    def handle(request):
        if request.url.path == "/internal/validate":
            token = json.loads(request.content)["token"]
            if token not in ("owner", SOLO, "evil"):
                return httpx.Response(401)
            return httpx.Response(200, json={"user_id": token, "is_admin": token == "owner"})
        subject = request.url.path.split("/")[3]
        orgs_asked.append(subject)
        if org_status != 200:
            return httpx.Response(org_status)
        if subject == SOLO:
            return httpx.Response(200, json={"org": {"id": SOLO_ORG, "kind": "personal"}})
        if subject == "evil":     # identity never says this, but a wrong answer must not open a door
            return httpx.Response(200, json={"org": {"id": SOLO_ORG, "kind": "personal"}})
        return httpx.Response(200, json={"org": {"id": "default", "kind": "org"}})
    return httpx.MockTransport(handle)


def _client(store, monkeypatch, flags, *, org_status=200):
    monkeypatch.setenv("VEXA_FEATURES", flags)
    asked = []
    transport = _identity_transport(asked, org_status=org_status)
    resolver = OrgResolver(INSTALL, "http://identity", INTERNAL, transport=transport,
                           ensure_org=lambda org, name: bootstrap_org(store.engine, org, name, PERSONAL_POLICY))
    app = create_app(store, Identity("http://identity", INTERNAL, transport), INSTALL, INTERNAL,
                     org_resolver=resolver)
    return TestClient(app), asked


def _create(client, who, name, key):
    # the install fixture's Company has `name`; a personal CRM is bootstrapped with the real `Name`
    field = "Name" if who == SOLO else "name"
    r = client.post("/change", headers={"X-API-Key": who}, json={
        "action": "create", "object_type": "Company", "fields": {field: name}, "reason": "t",
        "idempotency_key": key})
    assert r.status_code == 200, r.text
    return r.json()["record_id"]


def _names(client, who):
    r = client.post("/search", headers={"X-API-Key": who}, json={"object_type": "Company", "filters": {}})
    assert r.status_code == 200, r.text
    return sorted(x["fields"].get("name") or x["fields"].get("Name") for x in r.json()["records"])


@pytest.mark.parametrize("flags", ["crm", "all", "all,personal_crm,-personal_crm"])
def test_off_everyone_is_served_from_the_install_org_and_identity_is_not_asked(store, monkeypatch, flags):
    client, asked = _client(store, monkeypatch, flags)
    _create(client, "owner", "Acme", "k1")
    assert client.post("/describe", headers={"X-API-Key": SOLO}, json={}).status_code in (200, 403)
    assert asked == []
    with store.engine.connect() as c:
        assert c.execute(s.orgs.select().where(s.orgs.c.org_id == SOLO_ORG)).first() is None


def test_the_flag_is_explicit():
    assert not features.personal_crm_on({"VEXA_FEATURES": "all"})
    assert features.personal_crm_on({"VEXA_FEATURES": "crm,personal_crm"})
    assert not features.personal_crm_on({"VEXA_FEATURES": "personal_crm"})       # needs the CRM
    assert not features.personal_crm_on({"VEXA_FEATURES": "crm,personal_crm,-personal_crm"})


def test_on_an_individual_has_their_own_crm_and_the_company_never_meets_it(store, monkeypatch):
    client, _ = _client(store, monkeypatch, "crm,personal_crm")
    company = _create(client, "owner", "Acme", "k1")
    mine = _create(client, SOLO, "My Lead", "k2")
    assert _names(client, SOLO) == ["My Lead"]                 # only their own CRM
    assert _names(client, "owner") == ["Acme"]                 # the company never sees it
    # by id, both ways: another org's record answers like one that does not exist
    assert client.post("/read", headers={"X-API-Key": SOLO}, json={"record_id": company}).status_code == 404
    assert client.post("/read", headers={"X-API-Key": "owner"}, json={"record_id": mine}).status_code == 404
    with store.engine.connect() as c:
        row = c.execute(s.records.select().where(s.records.c.id == mine)).mappings().first()
    assert row["org_id"] == SOLO_ORG


def test_a_caller_cannot_name_an_org(store, monkeypatch):
    client, _ = _client(store, monkeypatch, "crm,personal_crm")
    _create(client, "owner", "Acme", "k1")
    r = client.post("/search", headers={"X-API-Key": SOLO, "X-CRM-Org": INSTALL},
                    json={"object_type": "Company", "filters": {}})
    assert r.status_code == 200 and r.json()["records"] == []
    r = client.post("/search", headers={"X-API-Key": SOLO, "X-CRM-Org": SOLO_ORG},
                    json={"object_type": "Company", "filters": {}})
    assert r.status_code == 403                                # the binding header is the install's
    assert client.post("/read", headers={"X-API-Key": SOLO}, json={"record_id": "x", "org_id": INSTALL}).status_code == 422


def test_only_ones_own_org_of_one_is_served(store, monkeypatch):
    """An answer naming somebody else's org of one is served as the install org, never as theirs."""
    client, _ = _client(store, monkeypatch, "crm,personal_crm")
    mine = _create(client, SOLO, "My Lead", "k2")
    assert client.post("/read", headers={"X-API-Key": "evil"}, json={"record_id": mine}).status_code in (403, 404)


def test_an_identity_that_cannot_answer_is_a_503_never_the_company_crm(store, monkeypatch):
    client, _ = _client(store, monkeypatch, "crm,personal_crm", org_status=503)
    _create_status = client.post("/search", headers={"X-API-Key": SOLO},
                                 json={"object_type": "Company", "filters": {}}).status_code
    assert _create_status == 503


def _rows(store, org):
    with store.engine.connect() as c:
        return sum(len(c.execute(t.select().where(t.c.org_id == org)).all())
                   for t in s.metadata.sorted_tables if "org_id" in t.c)


def _by_table(store, org):
    with store.engine.connect() as c:
        return {t.name: len(c.execute(t.select().where(t.c.org_id == org)).all())
                for t in s.metadata.sorted_tables if "org_id" in t.c}


@pytest.mark.parametrize("flags", ["crm,personal_crm", ""])
def test_account_delete_erases_the_personal_crm_but_its_append_only_rows_and_nothing_else(store, monkeypatch, flags):
    """Everything keyed to the org of one goes, except the append-only tables, which stay untouched
    until their erasure is decided (``migrate.ERASE_APPEND_ONLY``) and are named in the answer; the
    records their revisions point at stay as empty, deleted rows."""
    client, _ = _client(store, monkeypatch, "crm,personal_crm")
    _create(client, "owner", "Acme", "k1")
    mine = _create(client, SOLO, "My Lead", "k2")
    client.post("/change", headers={"X-API-Key": SOLO}, json={
        "action": "update", "record_id": mine, "expected_revision": 1, "fields": {"Name": "Lead 2"}, "reason": "t"})
    company_before = _rows(store, INSTALL)
    assert _rows(store, SOLO_ORG) > 0
    monkeypatch.setenv("VEXA_FEATURES", flags)          # erasure works even with the CRM switched off
    h = {"X-Internal-Secret": INTERNAL}
    before = _by_table(store, SOLO_ORG)
    r = client.post(f"/internal/orgs/{SOLO_ORG}/erase", headers=h)
    assert r.status_code == 200, r.text
    out, after = r.json(), _by_table(store, SOLO_ORG)
    assert out["rows"] > 0 and out["org"] == SOLO_ORG
    assert out["left"] == {t: before[t] for t in migrate.APPEND_ONLY} and out["left"]["crm_revisions"] > 0
    assert {t: after[t] for t in migrate.APPEND_ONLY} == out["left"]            # untouched
    kept = set(migrate.APPEND_ONLY) | {"crm_records", "crm_object_definitions"}
    assert {t: n for t, n in after.items() if t not in kept and n} == {}        # everything else is gone
    with store.engine.connect() as c:
        rows = c.execute(s.records.select().where(s.records.c.org_id == SOLO_ORG)).mappings().all()
    assert rows and all(x["deleted"] and x["fields"] == {} and x["narrative"] == "" and x["account_id"] is None
                        for x in rows)
    assert _rows(store, INSTALL) == company_before       # the company's CRM is untouched
    assert client.post(f"/internal/orgs/{SOLO_ORG}/erase", headers=h).json()["rows"] == 0   # idempotent


@pytest.mark.parametrize("org", [INSTALL, "default", "p-", "p-12x", "two", "p-1/../one"])
def test_erase_never_touches_any_other_org(store, monkeypatch, org):
    client, _ = _client(store, monkeypatch, "crm,personal_crm")
    _create(client, "owner", "Acme", "k1")
    before = _rows(store, INSTALL)
    r = client.post(f"/internal/orgs/{org}/erase", headers={"X-Internal-Secret": INTERNAL})
    assert r.status_code in (400, 403, 404)
    assert _rows(store, INSTALL) == before


def test_erase_needs_the_internal_secret(store, monkeypatch):
    client, _ = _client(store, monkeypatch, "crm,personal_crm")
    assert client.post(f"/internal/orgs/{SOLO_ORG}/erase").status_code == 403
    assert client.post(f"/internal/orgs/{SOLO_ORG}/erase", headers={"X-API-Key": SOLO}).status_code == 403


def test_a_personal_accounts_workspace_settings_live_in_its_own_crm_and_go_with_it(store, monkeypatch):
    client, _ = _client(store, monkeypatch, "all,personal_crm")
    h = {"X-API-Key": SOLO}
    r = client.post("/desk/workspaces/set", headers=h, json={"workspace": "personal", "settings": {"pinned": True}})
    assert r.status_code == 200, r.text
    assert client.post("/desk/workspaces/read", headers=h, json={}).json()["workspaces"]["personal"]["pinned"] is True
    assert client.post("/desk/workspaces/read", headers={"X-API-Key": "owner"}, json={}).json()["workspaces"] == {}
    with store.engine.connect() as c:
        assert {r.org_id for r in c.execute(s.member_settings.select()).all()} == {SOLO_ORG}
    assert client.post(f"/internal/orgs/{SOLO_ORG}/erase", headers={"X-Internal-Secret": INTERNAL}).status_code == 200
    with store.engine.connect() as c:
        assert c.execute(s.member_settings.select()).all() == []
