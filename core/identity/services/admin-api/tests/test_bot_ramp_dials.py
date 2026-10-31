"""PER-ACCOUNT BOT RAMP DIALS (#1962 s1) — admin-api's half of the ramp.

`PATCH /admin/users/{id}` takes `bot_ramp_burst` (>=1), `bot_ramp_share` (0..1) and
`bot_ramp_window_s` (10..3600). They merge into `users.data.bot_ramp` as `burst`/`share`/`window_s`;
an explicit null clears a key, and once every key is cleared `bot_ramp` is gone. bot-context states
the override so meeting-api can fill the rest from its deployment defaults.

The pure half runs anywhere; the route half is the testcontainers-PG harness the other suites use.
"""
import pytest
from pydantic import ValidationError

from admin_api.app.main import UserAdminPatch, merge_bot_ramp


# ── the pure rule ─────────────────────────────────────────────────────────────────────────────

def test_dials_merge_into_bot_ramp_and_keep_other_data():
    patch = UserAdminPatch(bot_ramp_burst=3, bot_ramp_share=0.2, bot_ramp_window_s=120)
    out = merge_bot_ramp({"keep": 1}, patch)
    assert out == {"keep": 1, "bot_ramp": {"burst": 3, "share": 0.2, "window_s": 120}}


def test_a_subset_merges_and_null_clears_one_key():
    data = {"bot_ramp": {"burst": 3, "share": 0.2, "window_s": 120}}
    assert merge_bot_ramp(data, UserAdminPatch(bot_ramp_burst=7))["bot_ramp"] == {
        "burst": 7, "share": 0.2, "window_s": 120}
    assert merge_bot_ramp(data, UserAdminPatch(bot_ramp_share=None))["bot_ramp"] == {
        "burst": 3, "window_s": 120}


def test_clearing_every_dial_removes_the_key():
    data = {"x": 1, "bot_ramp": {"burst": 3, "share": 0.2, "window_s": 120}}
    patch = UserAdminPatch(bot_ramp_burst=None, bot_ramp_share=None, bot_ramp_window_s=None)
    assert merge_bot_ramp(data, patch) == {"x": 1}


def test_a_patch_without_dials_leaves_data_alone():
    data = {"bot_ramp": {"burst": 3}}
    assert merge_bot_ramp(data, UserAdminPatch(max_concurrent_bots=25)) == data


def test_a_lone_null_dial_is_a_change():
    UserAdminPatch(bot_ramp_window_s=None)                    # require_change accepts it
    with pytest.raises(ValidationError):
        UserAdminPatch()                                       # an empty patch still refuses


@pytest.mark.parametrize("body", [
    {"bot_ramp_burst": 0}, {"bot_ramp_share": 1.5}, {"bot_ramp_share": -0.1},
    {"bot_ramp_window_s": 5}, {"bot_ramp_window_s": 3601}, {"bot_ramp": {"burst": 3}},
])
def test_out_of_range_or_unknown_dials_refuse(body):
    with pytest.raises(ValidationError):
        UserAdminPatch(**body)


# ── the routes, on a real database ──────────────────────────────────────────────────────────

try:
    from conftest import requires_docker
except Exception:  # pragma: no cover — the harness is always importable in this suite
    requires_docker = pytest.mark.skip(reason="no harness")


@pytest.fixture()
def client(pg_url, pg_async_url, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    from admin_api.app import db as app_db
    from admin_api.app.main import create_app
    from admin_api.schema.models import Base
    from admin_api.schema.sync import ensure_schema_sync
    from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _dispose_async_engine

    sync_engine = create_engine(pg_url)
    Base.metadata.drop_all(sync_engine)
    ensure_schema_sync(sync_engine, Base)
    sync_engine.dispose()
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setenv("INTERNAL_API_SECRET", INTERNAL_SECRET)
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.delenv("VEXA_ADMIN_EMAILS", raising=False)
    monkeypatch.delenv("VEXA_SIGNIN_ALLOW", raising=False)
    app_db.configure(pg_async_url)
    with TestClient(create_app()) as c:
        yield c
    _dispose_async_engine()


def _admin():
    from test_stack_admin_api import _admin as h
    return h()


def _internal():
    from test_stack_admin_api import INTERNAL_SECRET
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _mk(client, email="ramp@example.com"):
    return client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]


def _ctx(client, uid):
    r = client.get(f"/internal/users/{uid}/bot-context", headers=_internal())
    assert r.status_code == 200
    return r.json()


@requires_docker
def test_patch_sets_the_dials_and_bot_context_states_them(client):
    uid = _mk(client)
    assert "bot_ramp" not in _ctx(client, uid)                  # no override → deployment defaults
    r = client.patch(f"/admin/users/{uid}", headers=_admin(), json={
        "max_concurrent_bots": 25, "bot_ramp_burst": 3, "bot_ramp_share": 0.2,
        "bot_ramp_window_s": 120})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["max_concurrent_bots"] == 25
    assert body["data"]["bot_ramp"] == {"burst": 3, "share": 0.2, "window_s": 120}
    assert client.get(f"/admin/users/{uid}", headers=_admin()).json()["data"]["bot_ramp"] == {
        "burst": 3, "share": 0.2, "window_s": 120}
    ctx = _ctx(client, uid)
    assert ctx["bot_ramp"] == {"burst": 3, "share": 0.2, "window_s": 120}
    assert ctx["max_concurrent"] == 25


@requires_docker
def test_null_clears_back_to_the_deployment(client):
    uid = _mk(client)
    client.patch(f"/admin/users/{uid}", headers=_admin(), json={
        "bot_ramp_burst": 3, "bot_ramp_share": 0.2, "bot_ramp_window_s": 120})
    r = client.patch(f"/admin/users/{uid}", headers=_admin(), json={"bot_ramp_share": None})
    assert r.status_code == 200 and r.json()["data"]["bot_ramp"] == {"burst": 3, "window_s": 120}
    r = client.patch(f"/admin/users/{uid}", headers=_admin(), json={
        "bot_ramp_burst": None, "bot_ramp_share": None, "bot_ramp_window_s": None})
    assert r.status_code == 200, r.text
    assert "bot_ramp" not in (r.json()["data"] or {})
    assert "bot_ramp" not in _ctx(client, uid)


@requires_docker
@pytest.mark.parametrize("body", [
    {"bot_ramp_burst": 0}, {"bot_ramp_share": 1.5}, {"bot_ramp_window_s": 5}])
def test_out_of_range_dials_are_422_and_change_nothing(client, body):
    uid = _mk(client)
    r = client.patch(f"/admin/users/{uid}", headers=_admin(), json=body)
    assert r.status_code == 422
    assert "bot_ramp" not in (client.get(f"/admin/users/{uid}", headers=_admin()).json()["data"] or {})


@requires_docker
def test_a_single_dial_patch_is_accepted(client):
    uid = _mk(client)
    r = client.patch(f"/admin/users/{uid}", headers=_admin(), json={"bot_ramp_burst": 4})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["bot_ramp"] == {"burst": 4}
