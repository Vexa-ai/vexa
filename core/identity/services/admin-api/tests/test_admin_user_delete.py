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

FIRST = {"meetings": 2, "transcripts": 1, "sessions": 1, "objects": 3, "cache_keys": 2,
         "webhook_deliveries": 0, "reader_entries": 0}
NOTHING = {k: 0 for k in FIRST}


@pytest.fixture(autouse=True)
def meeting_api(monkeypatch):
    """meeting-api's erase door (DELETE /internal/users/{id}/meetings), faked at the HTTP seam: its
    own suite proves what it deletes (meeting-api tests/test_account_erase*.py); this proves the order
    admin-api follows and what it refuses. The first erase finds meetings; later ones find nothing.
    ``state["fail_on"]`` makes the n-th call (1-based) answer ``state["status"]``."""
    state = {"calls": [], "status": 200, "fail_on": None, "running": [],
             "first": FIRST, "bots": 1, "retained": {"room_meetings": 1}}

    def handle(request):
        state["calls"].append((request.method, request.url.path,
                               request.headers.get("x-internal-secret"), dict(request.url.params)))
        n = len(state["calls"])
        if state["fail_on"] in (n, "all"):
            if state["status"] == "down":
                raise httpx.ConnectError("meeting-api is down", request=request)
            return httpx.Response(state["status"], json={"running": state["running"], "detail": "no"})
        return httpx.Response(200, json={"deleted": state["first"] if n == 1 else NOTHING,
                                         "retained": state["retained"],
                                         "bot_accounts": {"deleted": state["bots"] if n == 1 else 0}})

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
            "stripe_customer_id": "cus_TEST", "stripe_subscription_id": "sub_TEST",
            "subscription_status": "canceled", **data}
    _sql(client, "UPDATE users SET data = CAST(:d AS jsonb) WHERE id = :u", d=json.dumps(blob), u=uid)
    _sql(client, "INSERT INTO memberships (principal_id, resource, role) VALUES (:p, 'workspace:w1', 'owner')",
         p=str(uid))
    _sql(client, "INSERT INTO workspaces (id, slug, owner) VALUES (:w, :w, :o)", w=f"ws-{uid}", o=str(uid))
    return uid


def _left(client, uid):
    data = _sql(client, "SELECT data FROM users WHERE id = :u", u=uid)
    return {
        "users": len(data),
        "api_tokens": len(_sql(client, "SELECT id FROM api_tokens WHERE user_id = :u", u=uid)),
        "memberships": len(_sql(client, "SELECT resource FROM memberships WHERE principal_id = :p", p=str(uid))),
        "owned_workspaces": len(_sql(client, "SELECT id FROM workspaces WHERE owner = :o", o=str(uid))),
    }


def _data(client, uid):
    return _sql(client, "SELECT data FROM users WHERE id = :u", u=uid)[0][0]


UNTOUCHED = {"users": 1, "api_tokens": 1, "memberships": 1, "owned_workspaces": 1}


def test_delete_freezes_then_erases_meetings_then_the_account_then_sweeps_again(client, meeting_api):
    uid = _person(client, "gone@rehearse.test")
    r = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert r.status_code == 200, r.text
    hop = ("DELETE", f"/internal/users/{uid}/meetings", INTERNAL_SECRET, {"wait_s": "20.0"})
    assert meeting_api["calls"] == [hop, hop], "the erase, then the final sweep"
    assert r.json() == {
        "user_id": uid,
        "deleted": {"api_tokens": 1, "calendar_connections": 2, "webhook": 1, "meetings": FIRST,
                    "workspace_memberships": 1, "workspaces_unowned": 1, "user": 1,
                    "final_sweep": NOTHING},
        "retained": {"room_meetings": 1},
        "bot_accounts": {"deleted": 1},
        "stripe": {"stripe_customer_id": "cus_TEST", "stripe_subscription_id": "sub_TEST"},
    }
    assert _left(client, uid) == {"users": 0, "api_tokens": 0, "memberships": 0, "owned_workspaces": 0}
    # the workspace itself is not this route's to remove: only its owner mark goes
    assert len(_sql(client, "SELECT id FROM workspaces WHERE id = :w", w=f"ws-{uid}")) == 1


def test_a_failed_meetings_hop_leaves_the_account_frozen_and_says_incomplete(client, meeting_api):
    uid = _person(client, "frozen@rehearse.test")
    meeting_api.update(fail_on=1, status=409, running=[{"id": 5, "status": "active"}])
    r = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "bot_still_running" and detail["incomplete"] is True
    assert detail["running"] == [{"id": 5, "status": "active"}]
    assert detail["done"] == {"api_tokens": 1, "calendar_connections": 2, "webhook": 1, "frozen": True}
    # FROZEN: no key, no calendar, no webhook, marked erasing — and bot-context says so to a spawn
    assert _left(client, uid) == {"users": 1, "api_tokens": 0, "memberships": 1, "owned_workspaces": 1}
    data = _data(client, uid)
    assert data["erasing"] is True
    assert not {"calendar_connections", "calendar_ics_url", "webhook_url", "webhook_secret"} & set(data)
    ctx = client.get(f"/internal/users/{uid}/bot-context", headers=_internal()).json()
    assert ctx["erasing"] is True
    # the retry finishes the job
    meeting_api["fail_on"] = None
    done = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert done.status_code == 200, done.text
    assert _left(client, uid)["users"] == 0


@pytest.mark.parametrize("failure", ["down", 500, 503])
def test_if_meeting_api_does_not_confirm_the_receipt_is_incomplete(client, meeting_api, failure):
    uid = _person(client, f"unconfirmed-{failure}@rehearse.test")
    meeting_api.update(fail_on=1, status=failure)
    r = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert r.status_code == 502
    assert r.json()["detail"]["incomplete"] is True
    assert _left(client, uid) == {"users": 1, "api_tokens": 0, "memberships": 1, "owned_workspaces": 1}


def test_a_failed_final_sweep_is_incomplete_and_a_retry_sweeps_with_the_row_gone(client, meeting_api):
    uid = _person(client, "sweep@rehearse.test")
    meeting_api.update(fail_on=2, status="down")
    r = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert detail["incomplete"] is True and detail["done"]["user"] == 1
    assert _left(client, uid)["users"] == 0
    # the retry: the users row is gone, the sweep still runs; it finds something → 200 sweep_only
    meeting_api.update(fail_on=None, first=FIRST)
    meeting_api["calls"].clear()
    again = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert again.status_code == 200, again.text
    assert again.json()["sweep_only"] is True and again.json()["deleted"]["user"] == 0


def test_a_second_delete_sweeps_once_and_is_a_404_when_nothing_is_left(client, meeting_api):
    uid = _person(client, "twice@rehearse.test")
    assert client.delete(f"/admin/users/{uid}", headers=_admin()).status_code == 200
    assert client.delete(f"/admin/users/{uid}", headers=_admin()).status_code == 404
    assert len(meeting_api["calls"]) == 3, "erase, final sweep, and the retry's sweep"


def test_delete_leaves_everyone_else_exactly_where_it_found_them(client):
    keep = _person(client, "keep@rehearse.test")
    gone = _person(client, "gone2@rehearse.test")
    assert client.delete(f"/admin/users/{gone}", headers=_admin()).status_code == 200
    assert client.get(f"/admin/users/{keep}", headers=_admin()).status_code == 200
    assert _left(client, keep) == UNTOUCHED
    assert "erasing" not in _data(client, keep)


@pytest.mark.parametrize("field", ["subscription_status", "tx_subscription_status"])
def test_a_subscription_that_can_still_charge_is_refused_before_anything(client, meeting_api, field):
    uid = _person(client, f"paying-{field}@rehearse.test", **{field: "active"})
    r = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "active_subscription"
    assert r.json()["detail"]["incomplete"] is False
    assert meeting_api["calls"] == []
    assert _left(client, uid) == UNTOUCHED and "erasing" not in _data(client, uid)


def test_an_account_that_pays_for_a_workspace_is_refused_before_anything_is_asked(client, meeting_api):
    uid = _person(client, "payer@rehearse.test")
    principal = _user(client, "ws-pay@principal.invalid")
    _sql(client, "INSERT INTO principals (id, kind, workspace_id, bills_to) VALUES (:p, 'workspace', 'w-pay', :u)",
         p=principal, u=uid)
    r = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["principals"] == [principal]
    assert meeting_api["calls"] == []
    assert _left(client, uid) == UNTOUCHED


def test_with_no_meetings_domain_and_no_meeting_rows_the_account_goes(client, meeting_api, monkeypatch):
    """MEETING_API_URL unset is the no-meetings profile (capability account_meetings_erase)."""
    monkeypatch.delenv("MEETING_API_URL")
    uid = _person(client, "nomeetings@rehearse.test")
    r = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert r.status_code == 200, r.text
    assert r.json()["deleted"]["meetings"] == "not_present"
    assert "final_sweep" not in r.json()["deleted"]
    assert meeting_api["calls"] == []
    assert _left(client, uid)["users"] == 0


def test_with_no_meetings_domain_but_meeting_rows_it_fails_closed(client, meeting_api, monkeypatch):
    monkeypatch.delenv("MEETING_API_URL")
    uid = _person(client, "orphaned@rehearse.test")
    _sql(client, "INSERT INTO meetings (id, user_id, platform, status) VALUES (9001, :u, 'jitsi', 'completed')",
         u=uid)
    r = client.delete(f"/admin/users/{uid}", headers=_admin())
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["error"] == "meetings_domain_not_configured"
    assert _left(client, uid) == UNTOUCHED


def test_delete_needs_the_admin_token(client, meeting_api):
    uid = _user(client, "guarded@rehearse.test")
    assert client.delete(f"/admin/users/{uid}").status_code in (401, 403)
    assert client.get(f"/admin/users/{uid}", headers=_admin()).status_code == 200
    assert meeting_api["calls"] == []


def test_deleting_an_unknown_user_is_a_404_not_a_silent_success(client, meeting_api):
    meeting_api.update(first=NOTHING, bots=0)
    assert client.delete("/admin/users/424242", headers=_admin()).status_code == 404


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
