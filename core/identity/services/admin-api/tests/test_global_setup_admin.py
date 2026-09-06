"""F-D15: the operator door onto the company-layer row — `PUT /admin/instance/global-setup`.

agent-api's onboarding wizard (`POST /api/global/ready` -> `PUT /internal/settings/global_setup`)
is the ONLY writer of `global_setup` today (see the comment on `_GLOBAL_SETUP_FIELDS` in
`admin_api/app/main.py`). A no-agents deployment has no wizard and no internal secret handed to an
operator, so without this route the company layer could never be committed by hand short of
reaching for the internal-only edge — this route is the admin-credentialed equivalent of that
write, gated the same way every other `/admin/*` route already is.

Same testcontainers-PG harness as the other suites (skips without docker).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from admin_api.app import db as app_db
from admin_api.app.main import create_app
from admin_api.schema.models import Base
from admin_api.schema.sync import ensure_schema_sync

from conftest import requires_docker
from test_stack_admin_api import ADMIN_TOKEN, INTERNAL_SECRET, _admin, _dispose_async_engine

pytestmark = requires_docker


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


def test_the_route_writes_the_row_and_get_admin_instance_reflects_it(client):
    # fresh instance: gate missing
    before = client.get("/admin/instance", headers=_admin()).json()
    assert before["global_setup"] == "missing"

    r = client.put("/admin/instance/global-setup", headers=_admin(), json={"company": "Acme Co"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["value"]["state"] == "completed"
    assert body["value"]["company"] == "Acme Co"
    assert body["value"]["completed_at"]        # stamped server-side when the caller omits it

    after = client.get("/admin/instance", headers=_admin()).json()
    assert after["global_setup"] == "completed"
    assert after["company"] == "Acme Co"


def test_company_is_required(client):
    r = client.put("/admin/instance/global-setup", headers=_admin(), json={})
    assert r.status_code == 422


def test_an_explicit_completed_at_is_kept_verbatim(client):
    r = client.put("/admin/instance/global-setup", headers=_admin(),
                   json={"company": "Acme Co", "completed_at": "2020-01-01T00:00:00Z"})
    assert r.status_code == 200, r.text
    assert r.json()["value"]["completed_at"] == "2020-01-01T00:00:00Z"


def test_an_unknown_field_alongside_a_known_one_is_dropped_not_rejected(client):
    r = client.put("/admin/instance/global-setup", headers=_admin(),
                   json={"company": "Acme Co", "not_a_real_field": "x"})
    assert r.status_code == 200, r.text
    assert "not_a_real_field" not in r.json()["value"]


def test_non_admin_is_refused(client):
    # no credential at all
    r = client.put("/admin/instance/global-setup", json={"company": "Acme Co"})
    assert r.status_code in (401, 403)
    # wrong credential
    r = client.put("/admin/instance/global-setup",
                   headers={"X-Admin-API-Key": "wrong-token"}, json={"company": "Acme Co"})
    assert r.status_code in (401, 403)
    # the internal secret is NOT a substitute for the admin key on this door
    r = client.put("/admin/instance/global-setup",
                   headers={"X-Internal-Secret": INTERNAL_SECRET}, json={"company": "Acme Co"})
    assert r.status_code in (401, 403)
