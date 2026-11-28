"""The two admin-tier routes PRD decision 38 needs: delete ONE person, and bind ONE person's
model config.

Both existed nowhere. Removing somebody meant `blank-instance.sh`, which deletes every person on
the stack — the instrument for "reset one test subject" was a wipe of the whole instance. And
pinning a config to somebody else meant the platform setting, which changes the model for everyone.

Same testcontainers-PG harness as the rest of this suite (skips without docker).
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

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
        c.pg_url = pg_url
        yield c
    _dispose_async_engine()


def _internal():
    return {"X-Internal-Secret": INTERNAL_SECRET}


def _user(client, email):
    return client.post("/admin/users", headers=_admin(), json={"email": email}).json()["id"]


def _sql(client, statement, **params):
    """Raw SQL beside the app, for the rows no route creates. Reads return rows; writes return []."""
    engine = create_engine(client.pg_url)
    try:
        with engine.begin() as conn:
            result = conn.execute(text(statement), params)
            return list(result) if result.returns_rows else []
    finally:
        engine.dispose()


# ── DELETE /admin/users/{id} ─────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def meeting_api(monkeypatch):
    """meeting-api's erase door (DELETE /internal/users/{id}/meetings), faked at the HTTP seam: its
    own suite proves what it deletes (meeting-api tests/test_account_erase*.py); this proves admin-api
    asks it FIRST and deletes nothing unless it confirms."""
    state = {"calls": [], "status": 200,
             "body": {"deleted": {"meetings": 2, "transcripts": 1, "sessions": 1,
                                  "objects": 3, "cache_keys": 2}}}

    def handle(request):
        state["calls"].append((request.method, request.url.path,
                               request.headers.get("x-internal-secret"), dict(request.url.params)))
        if state["status"] == "down":
            raise httpx.ConnectError("meeting-api is down", request=request)
        return httpx.Response(state["status"], json=state["body"])

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handle)}))
    monkeypatch.setenv("MEETING_API_URL", "http://meeting-api:8080")
    return state


def _person(client, email, **data):
    """A person with a key, a calendar connection, a webhook, a workspace membership and Stripe ids."""
    uid = _user(client, email)
    client.post(f"/admin/users/{uid}/tokens?scopes=bot", headers=_admin())
    blob = {"calendar_connections": [{"id": "cal-1", "name": "Work", "ics_url": "https://cal.test/a.ics",
                                      "auto_join": True, "enabled": True},
                                     {"id": "cal-2", "name": "Old", "deleted": True}],
            "calendar_ics_url": "https://cal.test/a.ics",
            "webhook_url": "https://hooks.test/x", "webhook_secret": "whsec-test",
            "stripe_customer_id": "cus_TEST", "stripe_subscription_id": "sub_TEST", **data}
    _sql(client, "UPDATE users SET data = CAST(:d AS jsonb) WHERE id = :u", d=json.dumps(blob), u=uid)
    _sql(client, "INSERT INTO memberships (principal_id, resource, role) VALUES (:p, 'workspace:w1', 'owner')",
         p=str(uid))
    _sql(client, "INSERT INTO workspaces (id, slug, owner) VALUES (:w, :w, :o)", w=f"ws-{uid}", o=str(uid))
    return uid


def _left(client, uid):
    return {
        "users": len(_sql(client, "SELECT id FROM users WHERE id = :u", u=uid)),
        "api_tokens": len(_sql(client, "SELECT id FROM api_tokens WHERE user_id = :u", u=uid)),
        "memberships": len(_sql(client, "SELECT resource FROM memberships WHERE principal_id = :p", p=str(uid))),
        "owned_workspaces": len(_sql(client, "SELECT id FROM workspaces WHERE owner = :o", o=str(uid))),
    }


def test_delete_asks_meeting_api_first_then_erases_everything_keyed_to_the_person(client, meeting_api):
    uid = _person(client, "gone@rehearse.test")
    r = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert r.status_code == 200, r.text
    assert meeting_api["calls"] == [("DELETE", f"/internal/users/{uid}/meetings", INTERNAL_SECRET,
                                     {"wait_s": "20.0"})]
    body = r.json()
    assert body == {
        "user_id": uid,
        "deleted": {"user": 1, "meetings": meeting_api["body"]["deleted"], "api_tokens": 1,
                    "calendar_connections": 2, "workspace_memberships": 1, "webhook": 1,
                    "workspaces_unowned": 1},
        "stripe": {"stripe_customer_id": "cus_TEST", "stripe_subscription_id": "sub_TEST"},
    }
    assert _left(client, uid) == {"users": 0, "api_tokens": 0, "memberships": 0, "owned_workspaces": 0}
    assert client.get(f"/admin/users/{uid}", headers=_admin()).status_code == 404
    # the workspace itself is not this route's to remove: only its owner mark goes
    assert len(_sql(client, "SELECT id FROM workspaces WHERE id = :w", w=f"ws-{uid}")) == 1


def test_a_second_delete_is_a_404_and_asks_meeting_api_nothing(client, meeting_api):
    uid = _person(client, "twice@rehearse.test")
    assert client.delete(f"/admin/users/{uid}", headers=_admin()).status_code == 200
    assert client.delete(f"/admin/users/{uid}", headers=_admin()).status_code == 404
    assert len(meeting_api["calls"]) == 1


def test_delete_leaves_everyone_else_exactly_where_it_found_them(client):
    keep = _person(client, "keep@rehearse.test")
    gone = _person(client, "gone2@rehearse.test")
    assert client.delete(f"/admin/users/{gone}", headers=_admin()).status_code == 200
    assert client.get(f"/admin/users/{keep}", headers=_admin()).status_code == 200
    assert _left(client, keep) == {"users": 1, "api_tokens": 1, "memberships": 1, "owned_workspaces": 1}


def test_a_bot_still_running_is_a_409_and_nothing_is_deleted(client, meeting_api):
    uid = _person(client, "live@rehearse.test")
    meeting_api["status"] = 409
    meeting_api["body"] = {"user_id": uid, "running": [{"id": 5, "status": "active"}],
                           "stop_requested": [5], "detail": "x"}
    r = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["running"] == [{"id": 5, "status": "active"}]
    assert _left(client, uid) == {"users": 1, "api_tokens": 1, "memberships": 1, "owned_workspaces": 1}


@pytest.mark.parametrize("failure", ["down", 500, 503])
def test_if_meeting_api_does_not_confirm_nothing_is_deleted(client, meeting_api, failure):
    uid = _person(client, f"unconfirmed-{failure}@rehearse.test")
    meeting_api["status"] = failure
    meeting_api["body"] = {"detail": "no"}
    assert client.delete(f"/admin/users/{uid}", headers=_admin()).status_code == 502
    assert _left(client, uid) == {"users": 1, "api_tokens": 1, "memberships": 1, "owned_workspaces": 1}


def test_an_account_that_pays_for_a_workspace_is_refused_before_anything_is_asked(client, meeting_api):
    uid = _person(client, "payer@rehearse.test")
    principal = _user(client, "ws-pay@principal.invalid")
    _sql(client, "INSERT INTO principals (id, kind, workspace_id, bills_to) VALUES (:p, 'workspace', 'w-pay', :u)",
         p=principal, u=uid)
    r = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["principals"] == [principal]
    assert meeting_api["calls"] == []
    assert _left(client, uid)["users"] == 1


def test_with_no_meetings_domain_the_receipt_says_so(client, meeting_api, monkeypatch):
    """MEETING_API_URL unset is the no-meetings profile (capability account_meetings_erase)."""
    monkeypatch.delenv("MEETING_API_URL")
    uid = _person(client, "nomeetings@rehearse.test")
    r = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert r.status_code == 200, r.text
    assert r.json()["deleted"]["meetings"] == "not_present"
    assert meeting_api["calls"] == []
    assert _left(client, uid)["users"] == 0


def test_delete_needs_the_admin_token(client, meeting_api):
    uid = _user(client, "guarded@rehearse.test")
    assert client.delete(f"/admin/users/{uid}").status_code in (401, 403)
    assert client.get(f"/admin/users/{uid}", headers=_admin()).status_code == 200
    assert meeting_api["calls"] == []


def test_deleting_an_unknown_user_is_a_404_not_a_silent_success(client, meeting_api):
    assert client.delete("/admin/users/424242", headers=_admin()).status_code == 404
    assert meeting_api["calls"] == []


# ── PUT /admin/users/{id}/models ─────────────────────────────────────────────────────────────────

def test_an_admin_can_bind_one_subject_to_a_runner_and_an_endpoint(client):
    uid = _user(client, "qwen@rehearse.test")
    r = client.put(f"/admin/users/{uid}/models", headers=_admin(), json={
        "runner": "openai-agent", "mode": "custom",
        "base_url": "http://192.168.1.6:8001/v1", "model": "qwen3.8-27b",
        "extra_body": '{"chat_template_kwargs":{"enable_thinking":false}}'})
    assert r.status_code == 200, r.text
    eff = client.get(f"/internal/users/{uid}/model-config", headers=_internal()).json()["models"]
    assert eff["runner"] == "openai-agent"
    assert eff["model"] == "qwen3.8-27b"
    assert eff["base_url"] == "http://192.168.1.6:8001/v1"
    assert eff["extra_body"] == '{"chat_template_kwargs":{"enable_thinking":false}}'


def test_binding_one_subject_leaves_every_other_subject_on_the_deployment_default(client):
    """The whole point of the route: the founder's dispatches are untouched by CONSTRUCTION.
    A platform setting would have changed the model for everybody."""
    scratch = _user(client, "scratch@rehearse.test")
    founder = _user(client, "founder@vexa.ai")
    client.put(f"/admin/users/{scratch}/models", headers=_admin(),
               json={"runner": "openai-agent", "mode": "custom",
                     "base_url": "http://192.168.1.6:8001/v1"})
    other = client.get(f"/internal/users/{founder}/model-config",
                       headers=_internal()).json()["models"]
    assert other == {}


def test_an_empty_string_clears_a_field(client):
    """How `runner="claude-code"` returns a subject to the deployment's own harness: the custom
    endpoint is CLEARED, not left pointing at a model the subject no longer runs."""
    uid = _user(client, "clearme@rehearse.test")
    client.put(f"/admin/users/{uid}/models", headers=_admin(),
               json={"runner": "openai-agent", "mode": "custom",
                     "base_url": "http://192.168.1.6:8001/v1", "model": "qwen3.8-27b"})
    client.put(f"/admin/users/{uid}/models", headers=_admin(),
               json={"runner": "claude-code", "mode": "", "base_url": "", "model": "",
                     "extra_body": ""})
    eff = client.get(f"/internal/users/{uid}/model-config", headers=_internal()).json()["models"]
    assert eff == {"runner": "claude-code"}


def test_the_admin_route_masks_the_secret_like_the_self_serve_one(client):
    uid = _user(client, "secret@rehearse.test")
    body = client.put(f"/admin/users/{uid}/models", headers=_admin(),
                      json={"mode": "custom", "base_url": "https://gw.test/v1",
                            "api_key": "sk-super-secret-value"}).json()
    assert body["api_key_set"] is True
    assert "sk-super-secret-value" not in str(body)


def test_it_needs_the_admin_token_and_404s_on_an_unknown_user(client):
    uid = _user(client, "guard2@rehearse.test")
    assert client.put(f"/admin/users/{uid}/models", json={"runner": "openai-agent"}).status_code \
        in (401, 403)
    assert client.put("/admin/users/424242/models", headers=_admin(),
                      json={"runner": "openai-agent"}).status_code == 404
