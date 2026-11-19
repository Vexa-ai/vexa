"""DEV_MODE never opens a door that changes who holds what, or that reads one named person's data.

`check_internal` lets `DEV_MODE=true` with no `INTERNAL_API_SECRET` through. That is a convenience
for doors that answer about the instance and name nobody. On `/internal/release-admin` it was an
instance takeover from network reach alone: the route strips the admin role AND answers a fresh
claim code in its body. Every door below must answer 503 in dev mode without a secret, and 403 with
a wrong one, before it reads or writes anything.
"""
from __future__ import annotations

import pytest

from test_signin_allow import SECRET, FakeDB, make_client  # noqa: F401 — the fixture is used by name

DOORS = [
    ("post", "/internal/release-admin", {"user_id": "1"}),
    ("post", "/internal/bootstrap-admin", {"user_id": 1, "claim_code": "x"}),
    ("post", "/internal/users/1/memberships", {"workspace_id": "w1", "role": "owner"}),
    ("delete", "/internal/users/1/memberships/w1", None),
    ("get", "/internal/users/1/memberships", None),
    ("get", "/internal/users/1/is-admin", None),
    ("get", "/internal/users/by-email/member@example.com", None),
    ("get", "/internal/users/1/model-config", None),
    ("get", "/internal/users/1/bot-context", None),
    ("get", "/internal/calendar-configs", None),
    ("get", "/internal/settings/signin", None),
    ("put", "/internal/settings/signin", {"allow": "*"}),
]


def _call(client, method, path, body, headers):
    kw = {"headers": headers}
    if body is not None:
        kw["json"] = body
    return getattr(client, method)(path, **kw)


@pytest.mark.parametrize("method,path,body", DOORS, ids=[f"{m} {p}" for m, p, _ in DOORS])
def test_dev_mode_without_a_secret_does_not_open_the_door(make_client, monkeypatch, method, path, body):
    db = FakeDB(users={"member@example.com"}, admins=("admin@example.com",))
    c = make_client(db)
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.delenv("INTERNAL_API_SECRET")
    r = _call(c, method, path, body, {})
    assert r.status_code == 503, (r.status_code, r.text)
    assert "claim_code" not in r.text
    # nothing was written: the admin still holds the role
    assert any(u.data.get("is_admin") for u in db.users.values())


@pytest.mark.parametrize("method,path,body", DOORS, ids=[f"{m} {p}" for m, p, _ in DOORS])
def test_a_wrong_secret_is_refused(make_client, method, path, body):
    c = make_client(FakeDB(users={"member@example.com"}))
    assert _call(c, method, path, body, {"X-Internal-Secret": "wrong"}).status_code == 403
    assert SECRET != "wrong"


def test_the_instance_doors_keep_the_dev_convenience(make_client, monkeypatch):
    """`/internal/instance` names nobody; it stays open in dev mode with no secret."""
    c = make_client(FakeDB(admin_exists=False))
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.delenv("INTERNAL_API_SECRET")
    assert c.get("/internal/instance").status_code == 200
