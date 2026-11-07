"""The person's OWN settings door — `settings`, `settings_set`, `workspace_regime` and
`workspace_regime_set` on the one MCP (Vexa-ai/vexa#1783).

The dogfood control rig served these by calling `/internal/users/{id}/settings` with the internal
secret on the caller's behalf, and kept the workspace regime in a rig-local store. Both are facts
about a PERSON, so identity owns them (`app/person_settings.py`), and the MCP reaches them through a
USER-scoped door: the caller's own credential names the person, never a path parameter.

Offline: the DB session and the caller are stubbed, so no Postgres and no docker.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from admin_api.app import db as app_db
from admin_api.app import main as main_mod
from admin_api.app import workspace_regime as wr
from admin_api.app.main import create_app
from admin_api.schema.models import User

SERVICE = pathlib.Path(__file__).resolve().parents[1]
IDENTITY = SERVICE.parents[1]


class _Session:
    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass


@pytest.fixture()
def person():
    return User(id=7, data={"person_settings": {"mail_join": True}})


@pytest.fixture()
def client(monkeypatch, person):
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-token-for-the-user-settings-test")
    app = create_app()

    async def _db():
        yield _Session()

    async def _me():
        return person

    app.dependency_overrides[app_db.get_db] = _db
    app.dependency_overrides[main_mod.get_current_user] = _me
    app.dependency_overrides[main_mod.get_current_user_for_update] = _me
    app.dependency_overrides[main_mod.get_current_person] = _me
    app.dependency_overrides[main_mod.get_current_person_for_update] = _me
    with TestClient(app) as c:
        yield c


# ── settings ───────────────────────────────────────────────────────────────────────────────────

def test_reading_settings_answers_the_person_facts_and_what_each_means(client):
    r = client.get("/user/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["settings"]) == {"timezone", "mail_minutes", "mail_join", "mail_rsvp", "mail_prep", "presence"}
    assert body["settings"]["mail_join"] is True
    assert set(body["what_each_means"]) == set(body["settings"])
    assert all(body["what_each_means"].values()), "a setting with no meaning reads as one that does nothing"


def test_setting_one_changes_only_that_one(client, person):
    r = client.put("/user/settings", json={"setting": "mail_minutes", "value": "off"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] == {"mail_minutes": False}
    assert body["settings"]["mail_join"] is True
    assert person.data["person_settings"]["mail_minutes"] is False


def test_an_unknown_setting_is_refused_with_the_vocabulary(client, person):
    before = json.dumps(person.data, sort_keys=True)
    r = client.put("/user/settings", json={"setting": "dark_mode", "value": "on"})
    assert r.status_code == 422
    assert "the_settings_that_exist" in r.json()["detail"]
    assert json.dumps(person.data, sort_keys=True) == before


def test_bot_name_is_not_a_person_setting_here_either(client):
    r = client.put("/user/settings", json={"setting": "bot_name", "value": "Notes"})
    assert r.status_code == 422
    assert r.json()["detail"]["refused"] == "bot_name is not a person setting"


# ── workspace regime ───────────────────────────────────────────────────────────────────────────

def test_the_regime_defaults_to_cloud(client):
    r = client.get("/user/workspace-regime")
    assert r.status_code == 200
    assert r.json()["regime"] == {"mode": "cloud"}


def test_local_needs_an_absolute_path(client, person):
    r = client.put("/user/workspace-regime", json={"mode": "local", "local_path": "notes"})
    assert r.status_code == 422
    assert wr.DATA_KEY not in person.data


def test_local_with_a_path_is_remembered_and_cloud_clears_it(client, person):
    r = client.put("/user/workspace-regime", json={"mode": "local", "local_path": "/Users/ada/vexa"})
    assert r.status_code == 200, r.text
    assert r.json()["regime"]["mode"] == "local"
    assert person.data[wr.DATA_KEY]["local_path"] == "/Users/ada/vexa"
    assert client.get("/user/workspace-regime").json()["regime"]["local_path"] == "/Users/ada/vexa"

    r = client.put("/user/workspace-regime", json={"mode": "cloud"})
    assert r.json()["regime"]["mode"] == "cloud"
    assert "local_path" not in person.data[wr.DATA_KEY]


def test_local_guidance_looks_before_it_pulls():
    """The agent's sandbox cannot run git, and a pull moves the branch: the session-start instruction
    names the look-only verb first and the pull as what follows when there is something to bring in."""
    said = " ".join(wr.guidance({"mode": "local", "local_path": "/home/p/kg"})["for_you_the_agent"])
    assert "workspace_status" in said
    assert said.index("workspace_status") < said.index("workspace_pull")
    assert "git yourself" in said


def test_an_unknown_mode_is_refused(client):
    assert client.put("/user/workspace-regime", json={"mode": "hybrid"}).status_code == 422


# ── declared at the edge and on the one MCP ────────────────────────────────────────────────────

USER_ROUTES = {("GET", "/user/settings"), ("PUT", "/user/settings"),
               ("GET", "/user/workspace-regime"), ("PUT", "/user/workspace-regime")}


def test_the_edge_registers_the_four_user_routes():
    rows = json.loads((IDENTITY / "routes.v1.json").read_text())["routes"]
    assert USER_ROUTES <= {(r["method"], r["path"]) for r in rows}


def test_the_identity_manifest_names_the_four_tools_on_those_routes():
    doc = json.loads((SERVICE / "src" / "admin_api" / "mcp.tools.v1.json").read_text())
    assert doc["domain"] == "identity" and doc["base_url_env"] == "ADMIN_API_URL"
    rows = {t["name"]: (t["route"]["method"], t["route"]["path"]) for t in doc["tools"]}
    assert rows == {"settings": ("GET", "/user/settings"), "settings_set": ("PUT", "/user/settings"),
                    "workspace_regime": ("GET", "/user/workspace-regime"),
                    "workspace_regime_set": ("PUT", "/user/workspace-regime")}


def test_the_manifest_is_served(client):
    r = client.get("/.well-known/mcp-tools.json")
    assert r.status_code == 200
    assert r.json()["domain"] == "identity"
