"""Public profiles (R4, PLAN-8H lane 8; registry ADR § "the only shared layer is self-declared profiles").

Identity is the ONE writer of a person's public profile: each field (name, photo, role, company,
links, bio) is published on its own, the whole profile can be withdrawn, and the owner sees only HOW
MANY orgs link to it — never which, never their notes. The directory finds published profiles by a
query (at least 3 characters, at most 10 answers, rate-limited), by opaque handle only, so nobody can
enumerate people. Behind `public_profiles` (explicit: not switched on by `all`).

Offline: the pure module and the doors with the DB stubbed; the Postgres round trip is in the stack suite."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from admin_api.app import db as app_db
from admin_api.app import public_profile as pp
from admin_api.app import profile_doors
from admin_api.app.main import create_app, get_current_person, get_current_person_for_update


def _published(name="Jonas Weber", **extra):
    data = pp.apply({}, {"name": {"value": name, "public": True},
                         "role": {"value": "Head of Workplace IT", "public": True},
                         "company": {"value": "NWB", "public": True},
                         "bio": {"value": "Runs the Exchange team.", "public": False}, **extra}, publish=True)
    return data


# ── the profile: field by field, opt-in, withdrawable ─────────────────────────────────────────
def test_nothing_is_public_until_published_field_by_field():
    assert pp.public_view({}) is None                                   # an account is not a profile
    draft = pp.apply({}, {"name": {"value": "Jonas", "public": True}}, publish=False)
    assert pp.public_view(draft) is None                                # saved, not published
    data = _published()
    view = pp.public_view(data)
    assert view["fields"] == {"name": "Jonas Weber", "role": "Head of Workplace IT", "company": "NWB"}
    assert "bio" not in view["fields"]                                  # private field stays private
    assert view["handle"] and view["handle"] == data[pp.DATA_KEY]["handle"]


def test_the_vocabulary_is_closed_and_values_are_bounded():
    with pytest.raises(pp.Refused):
        pp.apply({}, {"email": {"value": "x@y.z", "public": True}}, publish=True)
    with pytest.raises(pp.Refused):
        pp.apply({}, {"bio": {"value": "x" * 2000, "public": True}}, publish=True)
    with pytest.raises(pp.Refused):
        pp.apply({}, {"links": {"value": ["javascript:alert(1)"], "public": True}}, publish=True)
    ok = pp.apply({}, {"links": {"value": ["https://example.org/me"], "public": True}}, publish=True)
    assert pp.public_view(ok)["fields"]["links"] == ["https://example.org/me"]


def test_withdraw_hides_everything_and_keeps_the_handle_dead():
    data = _published()
    handle = data[pp.DATA_KEY]["handle"]
    gone = pp.withdraw(data)
    assert pp.public_view(gone) is None
    assert pp.find(gone_rows := [(1, gone)], handle) is None            # by handle: not found, as if never there
    assert pp.search(gone_rows, "Jonas") == []
    again = pp.apply(gone, {}, publish=True)                            # republishing keeps the same handle
    assert pp.public_view(again)["handle"] == handle


def test_the_owner_sees_how_many_orgs_link_never_which():
    data = _published()
    data = pp.link(data, "org-a", True)
    data = pp.link(data, "org-b", True)
    data = pp.link(data, "org-a", True)                                 # idempotent
    own = pp.read_own(data)
    assert own["linked_by"] == 2
    text = repr(own) + repr(pp.public_view(data))
    assert "org-a" not in text and "org-b" not in text                  # only a count, keyed by a hash
    data = pp.link(data, "org-b", False)
    assert pp.read_own(data)["linked_by"] == 1


# ── the directory: no enumeration ─────────────────────────────────────────────────────────────
def _rows():
    return [(1, _published("Jonas Weber")), (2, _published("Ines Duarte")), (3, pp.apply({}, {
        "name": {"value": "Hidden Person", "public": True}}, publish=False)), (4, {})]


def test_search_needs_a_real_query_and_answers_few():
    rows = _rows()
    assert pp.search(rows, "") == [] and pp.search(rows, "jo") == []    # under 3 characters: nothing
    assert pp.search(rows, "*") == [] and pp.search(rows, "%%%") == []  # no wildcard listing
    hits = pp.search(rows, "jonas")
    assert [h["fields"]["name"] for h in hits] == ["Jonas Weber"]
    assert pp.search(rows, "hidden") == []                              # unpublished never found
    many = [(i, _published(f"Alex Number{i}")) for i in range(40)]
    assert len(pp.search(many, "alex")) == pp.MAX_RESULTS == 10
    # no user id, no email, no count of matches leaks: handles only
    assert all(set(h) == {"handle", "fields", "company_verified"} for h in hits)


def test_search_matches_public_fields_only():
    rows = _rows()
    assert pp.search(rows, "Exchange") == []                            # the bio is private


def test_handles_are_unguessable():
    h = _published()[pp.DATA_KEY]["handle"]
    assert len(h) >= 16 and not h.isdigit()


def test_the_rate_limit_stops_a_scraper():
    lim = pp.RateLimiter(per_minute=5)
    now = 1000.0
    assert all(lim.allow("u1", now) for _ in range(5))
    assert not lim.allow("u1", now)
    assert lim.allow("u2", now)                                         # per person
    assert lim.allow("u1", now + 61)


# ── verified companies: data and a flow, no payment ───────────────────────────────────────────
def test_domain_verification_by_dns_txt_or_an_admin_email_on_the_domain():
    data = pp.start_verification({}, "Example.ORG")
    v = data[pp.VERIFY_KEY]
    assert v["domain"] == "example.org" and v["txt"].startswith("vexa-verify=")
    assert pp.check_verification(data, txt_records=["other"], email="")[pp.VERIFY_KEY].get("verified_at") is None
    ok = pp.check_verification(data, txt_records=[v["txt"]], email="", now=5.0)
    assert ok[pp.VERIFY_KEY]["verified_at"] == 5.0 and ok[pp.VERIFY_KEY]["method"] == "dns_txt"
    by_mail = pp.check_verification(data, txt_records=[], email="admin@example.org", email_verified=True, now=6.0)
    assert by_mail[pp.VERIFY_KEY]["method"] == "admin_email"
    assert pp.check_verification(data, txt_records=[], email="admin@example.org", email_verified=False)[pp.VERIFY_KEY].get("verified_at") is None
    with pytest.raises(pp.Refused):
        pp.start_verification({}, "not a domain")
    # the verified domain shows on the public profile only beside a public company field
    pub = pp.record_email_verified(_published(), "a@example.org", via="magic_link", now=7.0)
    pub = pp.check_verification(pp.start_verification(pub, "example.org"), txt_records=[], email="a@example.org", email_verified=True, now=7.0)
    assert pp.public_view(pub, email="a@example.org", now=8.0)["company_verified"] == "example.org"


def test_the_feature_is_explicit_not_in_all():
    assert pp.feature_on({"VEXA_FEATURES": "all"}) is False
    assert pp.feature_on({"VEXA_FEATURES": "all,public_profiles"}) is True
    assert pp.feature_on({"VEXA_FEATURES": "public_profiles,-public_profiles"}) is False
    assert pp.feature_on({}) is False


# ── the doors ─────────────────────────────────────────────────────────────────────────────────
class _User:
    def __init__(self, uid, data=None, email="jonas@example.org"):
        self.id, self.data, self.email = uid, data or {}, email


class _DB:
    def add(self, _):
        pass

    async def commit(self):
        pass


@pytest.fixture()
def doors(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "s3cret")
    monkeypatch.setenv("VEXA_FEATURES", "public_profiles")
    app = create_app()
    me = _User(7)
    others = {1: _User(1, _published("Ines Duarte")), 7: me}

    async def _db():
        yield _DB()

    async def _me():
        return me
    app.dependency_overrides[app_db.get_db] = _db
    app.dependency_overrides[get_current_person] = _me
    app.dependency_overrides[get_current_person_for_update] = _me

    async def rows(_db):
        return [(u.id, u.data, u.email) for u in others.values()]

    async def by_handle(_db, handle, for_update=False):
        for u in others.values():
            if (u.data.get(pp.DATA_KEY) or {}).get("handle") == handle:
                return u
        return None
    monkeypatch.setattr(profile_doors, "published_rows", rows)
    monkeypatch.setattr(profile_doors, "user_by_handle", by_handle)
    profile_doors.LIMITER.reset()
    with TestClient(app) as c:
        yield c, me, others


def test_publish_read_withdraw_through_the_doors(doors):
    c, me, _ = doors
    r = c.put("/user/profile", json={"fields": {"name": {"value": "Jonas Weber", "public": True}}, "publish": True})
    assert r.status_code == 200 and r.json()["published"] is True and r.json()["linked_by"] == 0
    handle = r.json()["handle"]
    assert c.get(f"/user/directory/profiles/{handle}").json()["fields"]["name"] == "Jonas Weber"
    assert c.post("/user/profile/withdraw").json()["published"] is False
    assert c.get(f"/user/directory/profiles/{handle}").status_code == 404


def test_the_directory_door_refuses_enumeration(doors):
    c, *_ = doors
    assert c.get("/user/directory/profiles", params={"q": "in"}).json()["profiles"] == []
    hits = c.get("/user/directory/profiles", params={"q": "ines"}).json()["profiles"]
    assert [h["fields"]["name"] for h in hits] == ["Ines Duarte"]
    assert all(set(h) == {"handle", "fields", "company_verified"} for h in hits)   # no user id, no email
    assert c.get("/user/directory/profiles/1").status_code == 404        # a user id is not a handle
    for _ in range(profile_doors.LIMITER.per_minute):
        c.get("/user/directory/profiles", params={"q": "ines"})
    assert c.get("/user/directory/profiles", params={"q": "ines"}).status_code == 429


def test_the_internal_link_door_counts_and_takes_the_secret(doors):
    c, _, others = doors
    handle = others[1].data[pp.DATA_KEY]["handle"]
    assert c.post(f"/internal/profiles/{handle}/links", json={"org": "org-a", "linked": True}).status_code == 403
    ok = c.post(f"/internal/profiles/{handle}/links", json={"org": "org-a", "linked": True},
                headers={"X-Internal-Secret": "s3cret"})
    assert ok.status_code == 200 and ok.json() == {"linked": True}      # nothing about the count to the org
    assert pp.read_own(others[1].data)["linked_by"] == 1
    pub = c.get(f"/internal/profiles/{handle}", headers={"X-Internal-Secret": "s3cret"}).json()
    assert pub["fields"]["name"] == "Ines Duarte" and "linked_by" not in pub


def test_every_door_refuses_with_the_feature_off(doors, monkeypatch):
    c, *_ = doors
    monkeypatch.setenv("VEXA_FEATURES", "all")
    for method, path in (("GET", "/user/profile"), ("PUT", "/user/profile"), ("POST", "/user/profile/withdraw"),
                         ("GET", "/user/directory/profiles?q=ines"), ("GET", "/user/directory/profiles/x"),
                         ("GET", "/internal/profiles/x")):
        r = c.request(method, path, json={})
        assert r.status_code == 403 and r.json()["detail"]["reason"] == "feature_disabled", (method, path)


# ── verified emails: recorded at sign-in, so an admin email on the domain verifies it ─────────
def test_an_email_is_verified_only_as_recorded_at_sign_in_and_only_while_it_is_the_accounts():
    assert pp.email_is_verified({}, "a@example.org") is False
    data = pp.record_email_verified({}, "A@Example.org", via="magic_link", now=1.0)
    assert pp.email_is_verified(data, "a@example.org") is True
    assert pp.email_is_verified(data, "other@example.org") is False      # the address changed: lapses
    with pytest.raises(pp.Refused):
        pp.record_email_verified({}, "a@example.org", via="password")    # only proof-of-inbox ways


def test_the_internal_email_verified_door_records_it_and_takes_the_secret(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", "s3cret")
    app = create_app()
    u = _User(9, {}, email="Dana@Example.org")

    class _Rows:
        def scalars(self):
            return self

        def first(self):
            return u

        def scalar_one_or_none(self):
            return u

    class _UserDB(_DB):
        async def execute(self, _statement):
            return _Rows()

    async def _db():
        yield _UserDB()
    app.dependency_overrides[app_db.get_db] = _db
    with TestClient(app) as c:
        body = {"via": "oidc:google"}
        assert c.put("/internal/users/9/email-verified", json=body).status_code == 403
        r = c.put("/internal/users/9/email-verified", json=body, headers={"X-Internal-Secret": "s3cret"})
        assert r.status_code == 200 and r.json() == {"verified": "dana@example.org"}
        assert c.put("/internal/users/9/email-verified", json={"via": "nope"},
                     headers={"X-Internal-Secret": "s3cret"}).status_code == 422
    assert pp.email_is_verified(u.data, "dana@example.org")
    # and so a domain is verified through that email, with no TXT record
    data = pp.start_verification(u.data, "example.org")
    ok = pp.check_verification(data, txt_records=[], email=u.email,
                               email_verified=pp.email_is_verified(data, u.email), now=3.0)
    assert ok[pp.VERIFY_KEY]["method"] == "admin_email"


# ── company verification is bound and lapses (review 2026-10-16) ──────────────────────────────
def _verified_by_email(email="a@acme-corp.example", company="Acme", now=10.0):
    data = pp.apply({}, {"name": {"value": "Ann", "public": True}, "company": {"value": company, "public": True}},
                    publish=True)
    data = pp.record_email_verified(data, email, via="magic_link", now=now)
    data = pp.start_verification(data, email.rsplit("@", 1)[1])
    return pp.check_verification(data, txt_records=[], email=email, email_verified=True, now=now)


def test_a_free_mail_domain_is_never_verified_through_an_address_on_it():
    for d in ("gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "proton.me", "gmx.de"):
        data = pp.record_email_verified({}, f"someone@{d}", via="magic_link", now=1.0)
        data = {**data, pp.VERIFY_KEY: {"domain": d, "txt": "vexa-verify=x"}}       # as if started before the list
        out = pp.check_verification(data, txt_records=[], email=f"someone@{d}", email_verified=True, now=1.0)
        assert out[pp.VERIFY_KEY].get("verified_at") is None, d
    # by a TXT record nobody can add to gmail.com either: the domain itself is refused
    import pytest as _p
    with _p.raises(pp.Refused):
        pp.start_verification({}, "gmail.com")


def test_the_badge_is_bound_to_the_company_text_it_was_verified_with():
    data = _verified_by_email(company="Acme")
    assert pp.public_view(data, email="a@acme-corp.example", now=11.0)["company_verified"] == "acme-corp.example"
    renamed = pp.apply(data, {"company": {"value": "Globex", "public": True}})
    assert pp.public_view(renamed, email="a@acme-corp.example", now=11.0)["company_verified"] is None


def test_an_email_verification_lapses_when_the_address_changes():
    data = _verified_by_email()
    assert pp.public_view(data, email="a@acme-corp.example", now=11.0)["company_verified"]
    assert pp.public_view(data, email="a@other.example", now=11.0)["company_verified"] is None


def test_a_txt_verification_lapses_unless_rechecked():
    data = pp.apply({}, {"name": {"value": "Ann", "public": True}, "company": {"value": "Acme", "public": True}},
                    publish=True)
    data = pp.start_verification(data, "acme-corp.example")
    data = pp.check_verification(data, txt_records=[data[pp.VERIFY_KEY]["txt"]], now=100.0)
    assert pp.public_view(data, now=100.0 + 3600)["company_verified"] == "acme-corp.example"
    assert pp.public_view(data, now=100.0 + pp.VERIFY_TTL_S + 1)["company_verified"] is None
    # a recheck without the record clears it at once
    gone = pp.check_verification(data, txt_records=[], now=200.0)
    assert gone[pp.VERIFY_KEY].get("verified_at") is None


# ── the DNS answer is parsed defensively ──────────────────────────────────────────────────────
def test_a_dns_answer_that_is_truncated_an_error_or_for_another_question_is_ignored():
    import struct as _s
    q = b"".join(bytes([len(p)]) + p.encode() for p in "acme-corp.example".split(".")) + b"\0"
    def answer(qid, flags, qname=q, txt=b"vexa-verify=x"):
        head = _s.pack(">HHHHHH", qid, flags, 1, 1, 0, 0)
        question = qname + _s.pack(">HH", 16, 1)
        rr = b"\xc0\x0c" + _s.pack(">HHIH", 16, 1, 60, len(txt) + 1) + bytes([len(txt)]) + txt
        return head + question + rr
    ok = profile_doors.parse_txt(answer(7, 0x8180), 7, "acme-corp.example")
    assert ok == ["vexa-verify=x"]
    assert profile_doors.parse_txt(answer(8, 0x8180), 7, "acme-corp.example") == []            # another id
    assert profile_doors.parse_txt(answer(7, 0x8183), 7, "acme-corp.example") == []            # NXDOMAIN
    assert profile_doors.parse_txt(answer(7, 0x8380), 7, "acme-corp.example") == []            # truncated
    other = b"".join(bytes([len(p)]) + p.encode() for p in "evil.example".split(".")) + b"\0"
    assert profile_doors.parse_txt(answer(7, 0x8180, qname=other), 7, "acme-corp.example") == []
    assert profile_doors.parse_txt(b"\x00\x07garbage", 7, "acme-corp.example") == []            # never raises


def test_the_query_id_is_unpredictable():
    import inspect
    src = inspect.getsource(profile_doors.txt_records)
    assert "secrets." in src and "random." not in src


def test_handle_reads_take_no_row_lock_and_the_handle_is_indexed():
    import inspect
    from admin_api.schema.models import User
    assert "with_for_update" not in inspect.getsource(profile_doors.user_by_handle).split("if for_update")[0]
    names = {getattr(i, "name", "") for i in User.__table_args__ if hasattr(i, "name")}
    assert "ix_users_public_profile_handle" in names
