"""WHO MAY SIGN IN — the sign-in allow-list and `POST /internal/signin-admission` (Vexa-ai/vexa#1783).

Removing the company-layer gate removed the only thing that controlled who could sign in: every
terminal door ends in find-or-create, so anybody who could finish one got an account, an API token,
agent turns and bot launches. The rule now: an EXISTING user, an ADMIN (claimed, or named by
`VEXA_ADMIN_EMAILS`), or an address on the allow-list (`VEXA_SIGNIN_ALLOW` + the admin-edited
`signin.allow` setting) — and, on an instance nobody has claimed and nothing has been configured for,
the next sign-in, because that sign-in is the claim. Who may CLAIM is decided here too.

Offline, no docker: the rule is pure (`app/signin_allow.py`), and the two doors are driven through
the real FastAPI app with the DB dependency replaced by an in-memory double that answers exactly the
three reads the admission makes (the user row, the `signin` settings row, "is there an admin").
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from admin_api.app import db as app_db
from admin_api.app import claim_code
from admin_api.app import signin_allow as sa
from admin_api.app.main import create_app

SECRET = "internal-secret-for-the-signin-test"
ADMIN = "admin-token-for-the-signin-test"


# ── the rule, pure ──────────────────────────────────────────────────────────────────────────────

def _decide(email, *, users=(), admins=(), admin_exists=True, allow=(), admin_emails=(), code=False):
    e = email.lower()
    return sa.decide(email, user_exists=e in users or e in admins, is_admin=e in admins,
                     admin_claimed=admin_exists, allow=allow, admins=admin_emails, claim_code_ok=code)


def test_an_unknown_address_is_refused_once_an_admin_exists():
    assert _decide("stranger@example.com") == (False, sa.WHY_NOT_ALLOWED)


def test_an_existing_user_is_admitted_whatever_the_list_says():
    """Upgrading must lock out nobody who already has an account — the list is empty here."""
    assert _decide("member@example.com", users={"member@example.com"}) == (True, sa.WHY_EXISTING_USER)
    assert _decide("Member@Example.COM", users={"member@example.com"}) == (True, sa.WHY_EXISTING_USER)


def test_the_claimed_admin_is_admitted():
    assert _decide("boss@example.com", admins={"boss@example.com"}) == (True, sa.WHY_ADMIN)


def test_a_domain_entry_admits_exactly_that_domain():
    allow = ["@bank.example"]
    assert _decide("anna@bank.example", allow=allow) == (True, sa.WHY_ALLOW_LIST)
    assert _decide("ANNA@BANK.EXAMPLE", allow=allow) == (True, sa.WHY_ALLOW_LIST)
    # lookalikes and subdomains are not that domain
    for other in ("anna@sub.bank.example", "anna@evil-bank.example", "anna@bank.example.evil.com",
                  "bank.example@evil.com", "anna@bank.examplex"):
        assert _decide(other, allow=allow) == (False, sa.WHY_NOT_ALLOWED), other


def test_an_exact_entry_admits_that_address_only():
    allow = ["alice@example.org"]
    assert _decide("alice@example.org", allow=allow) == (True, sa.WHY_ALLOW_LIST)
    assert _decide("bob@example.org", allow=allow) == (False, sa.WHY_NOT_ALLOWED)
    # no plus-address folding: an exact entry is exact
    assert _decide("alice+x@example.org", allow=allow) == (False, sa.WHY_NOT_ALLOWED)


def test_an_unclaimed_instance_admits_nobody_new_without_the_claim_code():
    """M7: the first visitor to an exposed instance is a stranger, not its owner."""
    assert _decide("first@anywhere.net", admin_exists=False) == (False, sa.WHY_NOT_ALLOWED)


def test_an_unclaimed_instance_admits_the_sign_in_that_holds_the_claim_code():
    assert _decide("first@anywhere.net", admin_exists=False, code=True) == (True, sa.WHY_CLAIM_CODE)
    # …and the code opens nothing once the instance is claimed or a list is configured
    assert _decide("first@anywhere.net", admin_exists=True, code=True) == (False, sa.WHY_NOT_ALLOWED)
    assert _decide("first@anywhere.net", admin_exists=False, code=True,
                   allow=["@bank.example"]) == (False, sa.WHY_NOT_ALLOWED)
    assert _decide("first@anywhere.net", admin_exists=False, code=True,
                   admin_emails=["owner@example.com"]) == (False, sa.WHY_NOT_ALLOWED)


def test_an_address_in_admin_emails_is_admitted_as_an_admin():
    assert _decide("owner@example.com", admin_emails=["owner@example.com"]) == (True, sa.WHY_ADMIN_EMAIL)
    assert _decide("Owner@Example.com", admin_emails=["owner@example.com"]) == (True, sa.WHY_ADMIN_EMAIL)


def test_a_configured_admin_list_closes_the_unclaimed_door():
    """VEXA_ADMIN_EMAILS names the admins: a stranger on an unclaimed instance is refused."""
    assert _decide("stranger@anywhere.net", admin_exists=False,
                   admin_emails=["owner@example.com"]) == (False, sa.WHY_NOT_ALLOWED)


def test_a_configured_allow_list_closes_the_unclaimed_door():
    """VEXA_SIGNIN_ALLOW (or the settings half) is set: only those addresses, claimed or not."""
    assert _decide("stranger@anywhere.net", admin_exists=False,
                   allow=["@bank.example"]) == (False, sa.WHY_NOT_ALLOWED)
    assert _decide("anna@bank.example", admin_exists=False, allow=["@bank.example"]) == (True, sa.WHY_ALLOW_LIST)


def test_who_may_claim_the_admin_role():
    # nothing configured, nobody claimed: the claim is open — to the claim code, and only to it
    assert sa.may_claim("first@anywhere.net", admin_claimed=False, allow=[]) == (False, sa.CLAIM_BAD_CODE)
    assert sa.may_claim("first@anywhere.net", admin_claimed=False, allow=[], code_ok=True) == (True, sa.CLAIMED)
    # somebody holds it
    assert sa.may_claim("first@anywhere.net", admin_claimed=True, allow=[],
                        code_ok=True) == (False, sa.CLAIM_ADMIN_EXISTS)
    # VEXA_ADMIN_EMAILS names the admins: nobody claims, not even an allowed address with the code
    assert sa.may_claim("anna@bank.example", admin_claimed=False, allow=["@bank.example"],
                        admins=["owner@example.com"], code_ok=True) == (False, sa.CLAIM_ADMIN_EXISTS)
    # an allow-list is configured: only an address on it, and still only with the code
    assert sa.may_claim("stranger@anywhere.net", admin_claimed=False, allow=["@bank.example"],
                        code_ok=True) == (False, sa.CLAIM_NOT_ALLOWED)
    assert sa.may_claim("anna@bank.example", admin_claimed=False, allow=["@bank.example"]) == (False, sa.CLAIM_BAD_CODE)
    assert sa.may_claim("anna@bank.example", admin_claimed=False, allow=["@bank.example"],
                        code_ok=True) == (True, sa.CLAIMED)


def test_the_admin_test_is_the_claimed_role_or_the_named_list():
    assert sa.is_admin("boss@example.com", {"is_admin": True}, []) is True
    assert sa.is_admin("Owner@Example.com", {}, ["owner@example.com"]) is True
    assert sa.is_admin("member@example.com", {}, ["owner@example.com"]) is False
    assert sa.is_admin("member@example.com", None, []) is False


def test_admin_emails_are_addresses_never_domains(monkeypatch):
    monkeypatch.setenv("VEXA_ADMIN_EMAILS", " Owner@Example.com, @example.com, not-an-address ")
    valid, problems = sa.admin_emails()
    assert valid == ["owner@example.com"]
    assert len(problems) == 2 and any("@example.com" in p and "domain" in p for p in problems)
    monkeypatch.delenv("VEXA_ADMIN_EMAILS")
    assert sa.admin_emails() == ([], [])


def test_a_non_address_is_refused_even_on_an_unclaimed_instance():
    for junk in ("", "   ", "no-at-sign", "@example.com", "a@b", "a b@example.com"):
        assert _decide(junk, admin_exists=False) == (False, sa.WHY_NOT_ALLOWED), junk


def test_the_env_and_the_settings_lists_merge(monkeypatch):
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "@bank.example, alice@example.com")
    env_valid, problems = sa.env_entries()
    assert env_valid == ["@bank.example", "alice@example.com"] and problems == []
    allow = sa.effective(env_valid, "bob@example.net\n@partner.example, alice@example.com")
    assert allow == ["@bank.example", "alice@example.com", "bob@example.net", "@partner.example"]
    for ok in ("x@bank.example", "alice@example.com", "bob@example.net", "y@partner.example"):
        assert _decide(ok, allow=allow)[0] is True, ok
    assert _decide("carol@example.com", allow=allow) == (False, sa.WHY_NOT_ALLOWED)


def test_an_invalid_env_entry_never_matches_and_is_reported(monkeypatch):
    """`bank.example` without the @ is a typo, not a domain: guessing it into one would widen the list
    the operator wrote. It is reported instead."""
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "bank.example,ok@example.com")
    env_valid, problems = sa.env_entries()
    assert env_valid == ["ok@example.com"]
    assert len(problems) == 1 and "@bank.example" in problems[0]
    assert _decide("anna@bank.example", allow=env_valid) == (False, sa.WHY_NOT_ALLOWED)


def test_unset_env_is_an_empty_list(monkeypatch):
    monkeypatch.delenv("VEXA_SIGNIN_ALLOW", raising=False)
    assert sa.env_entries() == ([], [])


def test_a_settings_write_is_canonicalised_and_all_or_nothing():
    assert sa.normalize_setting(" Alice@Example.com ,\n@BANK.example;alice@example.com ") == \
        "alice@example.com, @bank.example"
    assert sa.normalize_setting("") == ""
    assert sa.normalize_setting(["a@b.co", "@c.co"]) == "a@b.co, @c.co"
    with pytest.raises(sa.InvalidAllowList) as bad:
        sa.normalize_setting("ok@example.com, bank.example, @nodot, x@@y.z, *")
    # every problem at once — one per bad entry, the good one not among them. `*` is one of them
    # here: in the admin-edited list it is refused as the operator's opt-in (wildcard tests below).
    assert len(bad.value.problems) == 4
    assert sa.WILDCARD_OPERATOR_ONLY in bad.value.problems
    assert not any("ok@example.com" in p for p in bad.value.problems)


# ── the doors, through the real app with an in-memory DB double ────────────────────────────────

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self.first()


class FakeDB:
    """Answers the admission's three reads, and holds platform_settings rows for the settings door."""

    def __init__(self, *, users=(), admins=(), admin_exists=True, signin_allow=""):
        self.users = {u: SimpleNamespace(id=i, email=u, data={}) for i, u in enumerate(users, 1)}
        for a in admins:
            self.users[a] = SimpleNamespace(id=len(self.users) + 1, email=a, data={"is_admin": True})
        self.admin_exists = admin_exists or bool(admins)
        self.rows = {}
        if signin_allow:
            self.rows["signin"] = SimpleNamespace(key="signin", value={"allow": signin_allow})
        self.asked_admin_exists = 0

    async def execute(self, stmt, params=None):
        if not hasattr(stmt, "selected_columns"):          # the claim's advisory lock
            return _Result([])
        if len(stmt.selected_columns) == 1:                 # _admin_exists: select(User.id)…
            self.asked_admin_exists += 1
            return _Result([(1,)] if self.admin_exists else [])
        params = stmt.compile(dialect=postgresql.dialect()).params
        uid = next((v for v in params.values() if isinstance(v, int) and not isinstance(v, bool)), None)
        if uid is not None and not any(isinstance(v, str) for v in params.values()):   # _load_user
            user = next((u for u in self.users.values() if u.id == uid), None)
            return _Result([user] if user else [])
        email = next((v for v in params.values() if isinstance(v, str)), "")
        user = self.users.get(email)
        return _Result([user] if user else [])


    async def get(self, _model, key):
        return self.rows.get(key)

    def add(self, row):
        if hasattr(row, "key"):                              # a platform_settings row
            self.rows[row.key] = row
        elif isinstance(getattr(row, "data", None), dict) and row.data.get("is_admin") is True:
            self.admin_exists = True                         # a user row the claim just promoted

    async def commit(self):
        pass


@pytest.fixture()
def make_client(monkeypatch):
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN)
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_SIGNIN_ALLOW", raising=False)
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    clients = []

    def _make(db: FakeDB) -> TestClient:
        app = create_app()

        async def _db():
            yield db

        app.dependency_overrides[app_db.get_db] = _db
        c = TestClient(app)
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.close()


def _ask(client, email, secret=SECRET):
    headers = {"X-Internal-Secret": secret} if secret is not None else {}
    return client.post("/internal/signin-admission", headers=headers, json={"email": email})


def test_admission_refuses_an_unknown_address(make_client):
    r = _ask(make_client(FakeDB(users={"member@example.com"})), "stranger@example.com")
    assert r.status_code == 200
    assert r.json() == {"admitted": False, "why": "not-allowed"}


def test_admission_admits_an_existing_user_and_the_admin(make_client):
    c = make_client(FakeDB(users={"member@example.com"}, admins={"boss@example.com"}))
    assert _ask(c, "Member@Example.com").json() == {"admitted": True, "why": "existing-user"}
    assert _ask(c, "boss@example.com").json() == {"admitted": True, "why": "admin"}


def test_admission_reads_the_env_and_the_settings_lists_together(make_client, monkeypatch):
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "@bank.example")
    c = make_client(FakeDB(signin_allow="alice@example.com"))
    assert _ask(c, "anna@bank.example").json() == {"admitted": True, "why": "allow-list"}
    assert _ask(c, "alice@example.com").json() == {"admitted": True, "why": "allow-list"}
    assert _ask(c, "bob@example.com").json() == {"admitted": False, "why": "not-allowed"}


def _live_code(db: FakeDB, code: str = "ABCD-EFGH-JKMN-PQRS") -> str:
    """Put a live claim code in the fake's admin_claim row, as the boot hook would."""
    from datetime import datetime, timezone
    db.rows[claim_code.ROW_KEY] = SimpleNamespace(
        key=claim_code.ROW_KEY,
        value=claim_code.record(code, host="boot-host", now=datetime.now(timezone.utc)))
    return code


def _ask_with(client, email, code):
    return client.post("/internal/signin-admission", headers={"X-Internal-Secret": SECRET},
                       json={"email": email, "claim_code": code})


def test_admission_on_an_unclaimed_instance_needs_the_claim_code(make_client):
    db = FakeDB(admin_exists=False)
    code = _live_code(db)
    c = make_client(db)
    assert _ask(c, "first@anywhere.net").json() == {"admitted": False, "why": "not-allowed"}
    assert _ask_with(c, "first@anywhere.net", "WRONG-CODE-0000-0000").json() == \
        {"admitted": False, "why": "not-allowed"}
    assert _ask_with(c, "first@anywhere.net", code.lower().replace("-", " ")).json() == \
        {"admitted": True, "why": "claim-code"}


def test_admission_on_a_claimed_instance_ignores_the_code(make_client):
    db = FakeDB(admin_exists=True)
    code = _live_code(db)
    assert _ask_with(make_client(db), "first@anywhere.net", code).json() == \
        {"admitted": False, "why": "not-allowed"}


def test_admission_admits_an_address_the_deployment_names_as_admin(make_client, monkeypatch):
    monkeypatch.setenv("VEXA_ADMIN_EMAILS", "Owner@Example.com")
    assert _ask(make_client(FakeDB()), "owner@example.com").json() == {"admitted": True, "why": "admin-email"}


def test_admission_on_an_unclaimed_instance_with_an_allow_list_admits_only_the_list(make_client, monkeypatch):
    """The security requirement on #1784: a configured list closes the unclaimed door."""
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "@bank.example")
    c = make_client(FakeDB(admin_exists=False))
    assert _ask(c, "stranger@anywhere.net").json() == {"admitted": False, "why": "not-allowed"}
    assert _ask(c, "anna@bank.example").json() == {"admitted": True, "why": "allow-list"}


def test_admission_on_an_unclaimed_instance_with_admin_emails_admits_only_them(make_client, monkeypatch):
    monkeypatch.setenv("VEXA_ADMIN_EMAILS", "owner@example.com")
    c = make_client(FakeDB(admin_exists=False))
    assert _ask(c, "stranger@anywhere.net").json() == {"admitted": False, "why": "not-allowed"}
    assert _ask(c, "owner@example.com").json() == {"admitted": True, "why": "admin-email"}


def test_the_instance_has_an_admin_when_the_deployment_names_one(make_client, monkeypatch):
    c = make_client(FakeDB(admin_exists=False))
    headers = {"X-Internal-Secret": SECRET}
    assert c.get("/internal/instance", headers=headers).json() == {"admin_exists": False}
    monkeypatch.setenv("VEXA_ADMIN_EMAILS", "owner@example.com")
    assert c.get("/internal/instance", headers=headers).json() == {"admin_exists": True}


@pytest.fixture()
def plain_rows(monkeypatch):
    """The claim flags its JSONB column modified; the fake's rows are plain objects."""
    from sqlalchemy.orm import attributes
    monkeypatch.setattr(attributes, "flag_modified", lambda *_a, **_k: None)


def _claim(client, user_id, code=None):
    body = {"user_id": user_id}
    if code is not None:
        body["claim_code"] = code
    return client.post("/internal/bootstrap-admin", headers={"X-Internal-Secret": SECRET},
                       json=body).json()


def test_the_claim_is_decided_here_with_the_same_lists(make_client, monkeypatch, plain_rows):
    db = FakeDB(users={"stranger@anywhere.net", "anna@bank.example"}, admin_exists=False)
    code = _live_code(db)
    c = make_client(db)
    ids = {u.email: u.id for u in db.users.values()}
    # an allow-list is configured: the first admin is one of its people, code or not
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "@bank.example")
    assert _claim(c, ids["stranger@anywhere.net"], code) == \
        {"claimed": False, "admin_exists": False, "why": "not-allowed"}
    # VEXA_ADMIN_EMAILS names the admins: nobody claims
    monkeypatch.setenv("VEXA_ADMIN_EMAILS", "owner@example.com")
    assert _claim(c, ids["anna@bank.example"], code) == \
        {"claimed": False, "admin_exists": True, "why": "admin-exists"}
    monkeypatch.delenv("VEXA_ADMIN_EMAILS")
    r = _claim(c, ids["anna@bank.example"], code)
    assert r == {"claimed": True, "admin_exists": True, "why": "claimed"}
    assert db.users["anna@bank.example"].data == {"is_admin": True}


def test_no_code_no_claim(make_client, plain_rows):
    db = FakeDB(users={"first@anywhere.net"}, admin_exists=False)
    _live_code(db)
    c = make_client(db)
    uid = db.users["first@anywhere.net"].id
    assert _claim(c, uid) == {"claimed": False, "admin_exists": False, "why": "bad-code"}
    assert _claim(c, uid, "WRONG-CODE-0000-0000") == {"claimed": False, "admin_exists": False, "why": "bad-code"}
    assert db.users["first@anywhere.net"].data == {}


def test_the_claim_code_works_once(make_client, plain_rows):
    db = FakeDB(users={"first@anywhere.net", "second@anywhere.net"}, admin_exists=False)
    code = _live_code(db)
    c = make_client(db)
    first, second = db.users["first@anywhere.net"].id, db.users["second@anywhere.net"].id
    assert _claim(c, first, code)["claimed"] is True
    assert not claim_code.is_live(db.rows[claim_code.ROW_KEY].value), "the claim retires the code"
    # even with the role released again, the spent code opens nothing
    db.admin_exists = False
    db.users["first@anywhere.net"].data = {}
    assert _claim(c, second, code) == {"claimed": False, "admin_exists": False, "why": "bad-code"}


def test_the_claim_screen_can_check_a_code_before_a_sign_in_carries_it(make_client, monkeypatch):
    db = FakeDB(admin_exists=False)
    code = _live_code(db)
    c = make_client(db)

    def check(value):
        return c.post("/internal/admin-claim/check", headers={"X-Internal-Secret": SECRET},
                      json={"claim_code": value}).json()
    assert check(code) == {"valid": True}
    assert check("oops") == {"valid": False}
    assert check("") == {"valid": False}
    monkeypatch.setenv("VEXA_ADMIN_EMAILS", "owner@example.com")
    assert check(code) == {"valid": False}, "no claim is open while the deployment names the admins"
    assert c.post("/internal/admin-claim/check", json={"claim_code": code}).status_code == 403


def test_the_claim_row_is_not_a_settings_key(make_client):
    """The code's digest is never readable or writable through the generic settings door."""
    c = make_client(FakeDB())
    h = {"X-Internal-Secret": SECRET}
    assert c.get(f"/internal/settings/{claim_code.ROW_KEY}", headers=h).status_code == 404
    assert c.put(f"/internal/settings/{claim_code.ROW_KEY}", headers=h, json={"sha256": "x"}).status_code == 404


def test_admission_does_not_ask_about_the_admin_when_the_answer_is_already_yes(make_client):
    db = FakeDB(users={"member@example.com"}, admin_exists=False)
    assert _ask(make_client(db), "member@example.com").json()["admitted"] is True
    assert db.asked_admin_exists == 0


def test_admission_is_internal_tier_with_no_dev_mode_escape(make_client, monkeypatch):
    """The answer says whether an address has an account here — exactly what the sign-in form is
    built not to reveal. Never open without the secret, dev mode included."""
    c = make_client(FakeDB(users={"member@example.com"}))
    assert _ask(c, "member@example.com", secret=None).status_code == 403
    assert _ask(c, "member@example.com", secret="wrong").status_code == 403
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.delenv("INTERNAL_API_SECRET")
    assert _ask(c, "member@example.com", secret=None).status_code == 503


def test_the_old_company_layer_door_is_still_gone(make_client):
    c = make_client(FakeDB())
    r = c.post("/internal/signin-allowed", headers={"X-Internal-Secret": SECRET}, json={"email": "x@y.z"})
    assert r.status_code in (404, 405)


def test_the_instance_state_is_served_on_the_internal_tier_only(make_client):
    """`GET /internal/instance` is the one door for "does this instance have an admin". The same
    answer over the admin key had no product caller and is gone."""
    c = make_client(FakeDB(admin_exists=False))
    assert c.get("/admin/instance", headers={"X-Admin-API-Key": ADMIN}).status_code == 404
    assert c.get("/internal/instance", headers={"X-Internal-Secret": SECRET}).json() == {
        "admin_exists": False}


def test_the_settings_door_canonicalises_the_list(make_client):
    db = FakeDB()
    c = make_client(db)
    r = c.put("/internal/settings/signin", headers={"X-Internal-Secret": SECRET},
              json={"allow": "Alice@Example.com\n@BANK.example"})
    assert r.status_code == 200, r.text
    assert r.json()["value"] == {"allow": "alice@example.com, @bank.example"}
    # …and the admission reads what was written
    assert _ask(c, "anna@bank.example").json()["admitted"] is True
    # clearing it is the empty string, as for every other settings field
    r = c.put("/internal/settings/signin", headers={"X-Internal-Secret": SECRET}, json={"allow": ""})
    assert r.json()["value"] == {}


def test_the_settings_door_refuses_a_bad_entry_and_stores_nothing(make_client):
    db = FakeDB(signin_allow="keep@example.com")
    c = make_client(db)
    r = c.put("/internal/settings/signin", headers={"X-Internal-Secret": SECRET},
              json={"allow": "new@example.com, bank.example"})
    assert r.status_code == 422
    assert "@bank.example" in r.json()["detail"]
    assert db.rows["signin"].value == {"allow": "keep@example.com"}


def test_the_settings_read_shows_the_env_half_and_its_problems(make_client, monkeypatch):
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "@bank.example,typo.example")
    c = make_client(FakeDB(signin_allow="alice@example.com"))
    body = c.get("/internal/settings/signin", headers={"X-Internal-Secret": SECRET}).json()
    assert body["value"] == {"allow": "alice@example.com"}
    assert body["env"] == {"allow": "@bank.example"}
    assert len(body["env_problems"]) == 1 and "typo.example" in body["env_problems"][0]


# ── the wire is signin.v1: the reasons are the contract's ─────────────────────────────────────────

def test_every_reason_the_rule_can_give_is_in_the_contract():
    """signin_wire.py is generated from core/identity/contracts/signin.v1 — the same file the
    terminal's signinWire.ts comes from. A reason the rule returns that the contract does not know
    would be refused by the terminal without anyone saying why."""
    from admin_api.app import signin_wire
    admitted = {sa.WHY_ADMIN, sa.WHY_ADMIN_EMAIL, sa.WHY_EXISTING_USER, sa.WHY_ALLOW_LIST, sa.WHY_CLAIM_CODE}
    assert admitted == set(signin_wire.ADMITTED_REASONS)
    assert {sa.WHY_NOT_ALLOWED} == set(signin_wire.REFUSED_REASONS)
    assert {sa.CLAIMED, sa.CLAIM_ADMIN_EXISTS, sa.CLAIM_BAD_CODE, sa.CLAIM_NOT_ALLOWED} == set(signin_wire.CLAIM_REASONS)


def test_a_reason_outside_the_contract_fails_the_response_loudly(make_client, monkeypatch):
    """The route serves the sealed shape: a reason the schema does not know is a 500 here, not a
    body the terminal reads as a refusal."""
    monkeypatch.setattr(sa, "decide", lambda *a, **k: (True, "unclaimed-instance"))
    c = make_client(FakeDB())
    with pytest.raises(Exception):
        _ask(c, "anyone@example.com")


def test_the_routes_take_the_contract_request_shapes(make_client):
    c = make_client(FakeDB(users={"member@example.com"}))
    h = {"X-Internal-Secret": SECRET}
    assert c.post("/internal/signin-admission", headers=h,
                  json={"email": "member@example.com", "unexpected": 1}).status_code == 422
    assert c.post("/internal/signin-admission", headers=h, json={}).status_code == 422
    assert c.post("/internal/bootstrap-admin", headers=h, json={"claim_code": "x"}).status_code == 422
    assert c.post("/internal/admin-claim/check", headers=h, json={"claim_code": "x" * 65}).status_code == 422


# ── the explicit wildcard: `*` alone opens sign-in to everyone (dev/demo opt-in) ────────────────

def test_the_wildcard_entry_is_valid_and_admits_any_address(monkeypatch):
    assert sa.entry_problem("*") is None
    assert sa.parse("*") == (["*"], [])
    assert sa.matches("test@test.com", ["*"]) is True
    assert _decide("stranger@example.com", allow=["*"]) == (True, sa.WHY_ALLOW_LIST)
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "*")
    assert sa.open_to_everyone() is True
    assert sa.boot_problems() == []


def test_the_wildcard_is_never_the_default_and_never_a_pattern(monkeypatch):
    monkeypatch.delenv("VEXA_SIGNIN_ALLOW", raising=False)
    assert sa.open_to_everyone() is False
    assert _decide("stranger@example.com") == (False, sa.WHY_NOT_ALLOWED)
    # `*` is a whole entry, never a pattern inside one
    assert sa.matches("someone@example.com", ["*@example.com"]) is False
    assert sa.entry_problem("@*.example.com") is not None
    assert sa.matches("not-an-address", ["*"]) is False


# ── R6-2: `*` is the OPERATOR's opt-in — the admin-edited setting can neither set it nor use it ──

def test_the_settings_door_refuses_the_wildcard_as_operator_only(make_client):
    db = FakeDB(signin_allow="keep@example.com")
    c = make_client(db)
    for value in ("*", "alice@example.com, *", ["*"]):
        r = c.put("/internal/settings/signin", headers={"X-Internal-Secret": SECRET},
                  json={"allow": value})
        assert r.status_code == 422, (value, r.text)
        assert "operator-only" in r.json()["detail"] and "VEXA_SIGNIN_ALLOW" in r.json()["detail"]
        assert db.rows["signin"].value == {"allow": "keep@example.com"}
    assert _ask(c, "stranger@example.com").json() == {"admitted": False, "why": "not-allowed"}


def test_a_stored_wildcard_opens_nothing(make_client):
    """A `*` written to the setting before it was refused (or written around the door) is ignored:
    a stranger is still turned away, and the rest of the stored list still works."""
    c = make_client(FakeDB(signin_allow="*, alice@example.com"))
    assert _ask(c, "stranger@example.com").json() == {"admitted": False, "why": "not-allowed"}
    assert _ask(c, "alice@example.com").json() == {"admitted": True, "why": "allow-list"}
    assert sa.effective([], "*, alice@example.com") == ["alice@example.com"]
    assert sa.stored_wildcard("*, alice@example.com") is True
    assert sa.stored_wildcard("alice@example.com") is False


def test_the_operators_wildcard_still_opens_sign_in(make_client, monkeypatch):
    monkeypatch.setenv("VEXA_SIGNIN_ALLOW", "*")
    c = make_client(FakeDB())
    assert _ask(c, "stranger@example.com").json() == {"admitted": True, "why": "allow-list"}
    assert sa.effective(["*"], "") == ["*"]


def test_a_stored_wildcard_is_named_at_boot(monkeypatch, caplog):
    import asyncio
    import contextlib
    import logging

    from admin_api import __main__ as boot
    from admin_api.app import db as app_db

    for stored, warned in (("*, alice@example.com", True), ("alice@example.com", False)):
        fake = FakeDB(signin_allow=stored)

        @contextlib.asynccontextmanager
        async def _session(fake=fake):
            yield fake

        monkeypatch.setattr(app_db, "session", _session)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="admin_api.boot"):
            asyncio.run(boot._warn_stored_open_signin())
        said = "IGNORED" in caplog.text and "VEXA_SIGNIN_ALLOW" in caplog.text
        assert said is warned, (stored, caplog.text)


def test_the_wildcard_is_not_an_admin(monkeypatch):
    monkeypatch.setenv("VEXA_ADMIN_EMAILS", "*")
    valid, problems = sa.admin_emails()
    assert valid == [] and problems
