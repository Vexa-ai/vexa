"""An account is bound to the OAuth identity that first signed in to it (app/provider_subjects.py,
PUT /internal/users/{id}/provider-subject, signin.v1 ProviderSubjectBindRequest/Response).

Inside one Microsoft tenant the tenant's administrator decides what the ID token's `email` says, so
an email match alone let that administrator sign in to any account at that address. The first
sign-in through a provider now records its stable subject on the account; a later one must carry it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from admin_api.app import db as app_db
from admin_api.app import provider_subjects as ps
from admin_api.app.main import create_app
from admin_api.schema.models import User

SECRET = "internal-secret-for-binding"
TENANT = "11111111-2222-3333-4444-555555555555"
OID_A = "99999999-8888-7777-6666-555555555555"
OID_B = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MS_A = f"microsoft:{TENANT}:{OID_A}"
MS_B = f"microsoft:{TENANT}:{OID_B}"
OIDC_A = "oidc:" + "a" * 64
OIDC_B = "oidc:" + "0123456789abcdef" * 4


# ── the rule ──────────────────────────────────────────────────────────────────────────────────

def test_the_first_subject_binds_and_the_same_one_passes():
    data, bound = ps.bind({"is_admin": False}, MS_A)
    assert bound == "first" and data == {"is_admin": False, "provider_subjects": {"microsoft": MS_A}}
    again, bound = ps.bind(data, MS_A)
    assert bound == "same" and again == data


def test_another_subject_of_a_bound_provider_is_refused():
    data, _ = ps.bind({}, MS_A)
    with pytest.raises(ps.Mismatch):
        ps.bind(data, MS_B)


def test_providers_are_bound_independently():
    data, _ = ps.bind({}, MS_A)
    data, bound = ps.bind(data, "google:1234567890")
    assert bound == "first" and data["provider_subjects"] == {"microsoft": MS_A, "google": "google:1234567890"}


def test_a_generic_oidc_subject_binds_and_another_is_refused():
    data, bound = ps.bind({"provider_subjects": {"microsoft": MS_A}}, OIDC_A)
    assert bound == "first" and data["provider_subjects"] == {"microsoft": MS_A, "oidc": OIDC_A}
    assert ps.bind(data, OIDC_A)[1] == "same"
    with pytest.raises(ps.Mismatch):
        ps.bind(data, OIDC_B)


@pytest.mark.parametrize("subject", ["", "microsoft:x:y", f"microsoft:{TENANT}", "github:1", "google:",
                                     "google:a b", f"MICROSOFT:{TENANT}:{OID_A}", "oidc:", "oidc:" + "a" * 63,
                                     "oidc:" + "A" * 64, "oidc:" + "a" * 65, "oidc:ZiV3uFjC0b8xQB0/2pF5Nk8="])
def test_only_the_subjects_the_terminal_makes_are_accepted(subject):
    with pytest.raises(ValueError):
        ps.bind({}, subject)


# ── the door ──────────────────────────────────────────────────────────────────────────────────

class _Result:
    def __init__(self, user):
        self.user = user

    def scalar_one_or_none(self):
        return self.user


class _DB:
    """Enough of an AsyncSession for `_load_user` and one commit."""

    def __init__(self, user):
        self.user, self.commits = user, 0

    async def execute(self, _statement):
        return _Result(self.user)

    def add(self, _row):
        pass

    async def commit(self):
        self.commits += 1


@pytest.fixture()
def door(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_SECRET", SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    clients = []

    def _make(user):
        app = create_app()
        db = _DB(user)

        async def _db():
            yield db

        app.dependency_overrides[app_db.get_db] = _db
        c = TestClient(app)
        clients.append(c)
        return c, db

    yield _make
    for c in clients:
        c.close()


def _put(c, subject, user_id=7, secret=SECRET):
    headers = {"X-Internal-Secret": secret} if secret is not None else {}
    return c.put(f"/internal/users/{user_id}/provider-subject", headers=headers, json={"subject": subject})


def test_the_door_binds_once_then_answers_same(door):
    user = User(id=7, email="ana@example.com", data={})
    c, db = door(user)
    first = _put(c, MS_A)
    assert first.status_code == 200 and first.json() == {"bound": "first"}
    assert user.data["provider_subjects"] == {"microsoft": MS_A} and db.commits == 1
    same = _put(c, MS_A)
    assert same.status_code == 200 and same.json() == {"bound": "same"} and db.commits == 1


def test_the_door_refuses_another_oidc_identity_and_changes_nothing(door):
    user = User(id=7, email="ana@example.com", data={"provider_subjects": {"oidc": OIDC_A}})
    c, db = door(user)
    assert _put(c, OIDC_B).status_code == 409
    assert user.data == {"provider_subjects": {"oidc": OIDC_A}} and db.commits == 0
    assert _put(c, OIDC_A).json() == {"bound": "same"}


def test_the_door_refuses_another_identity_and_changes_nothing(door):
    user = User(id=7, email="ana@example.com", data={"provider_subjects": {"microsoft": MS_A}})
    c, db = door(user)
    r = _put(c, MS_B)
    assert r.status_code == 409
    assert user.data == {"provider_subjects": {"microsoft": MS_A}} and db.commits == 0


@pytest.mark.parametrize("secret", [None, "wrong"])
def test_the_door_needs_the_internal_secret(door, secret):
    user = User(id=7, email="ana@example.com", data={})
    c, db = door(user)
    assert _put(c, MS_A, secret=secret).status_code == 403
    assert user.data == {} and db.commits == 0


def test_an_unknown_account_is_404_and_a_malformed_subject_422(door):
    c, _ = door(None)
    assert _put(c, MS_A).status_code == 404
    c2, _ = door(User(id=7, email="ana@example.com", data={}))
    assert _put(c2, "github:1").status_code == 422


def _contract():
    for parent in Path(__file__).resolve().parents:
        p = parent / "contracts" / "signin.v1" / "signin.schema.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))["$defs"]
    raise FileNotFoundError("signin.v1")


def test_the_rule_and_the_sealed_shape_agree():
    defs = _contract()
    assert defs["ProviderSubjectBindRequest"]["properties"]["subject"]["pattern"] == ps.SUBJECT_RE.pattern
    assert defs["ProviderSubjectBindResponse"]["properties"]["bound"]["enum"] == ["first", "same"]
