"""`POST /api/workspace/entity` — the endpoint `entity_upsert` is a thin forward to.

PRD §3.3's porting rule, applied ahead of the port rather than after it: every host-reaching rig
tool is a missing HTTP endpoint in an owning service wearing a shell command. `workspace_write`'s
`docker exec` double is the shape this deliberately does not copy — so the tool has nothing to do
but pass the arguments on, and the rules live where the workspace does.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from shared.config import load_settings
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader


class _FakeRuntime:
    def spawn(self, *a, **kw):
        return "unit-1"

    def stop(self, *a, **kw):
        return None


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_WORKSPACES_DIR", str(tmp_path))
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(tmp_path))))


H = {"X-User-Id": "u_jane"}


def test_creates_the_page_and_the_index_and_reports_both(client, tmp_path):
    r = client.post("/api/workspace/entity", headers=H, json={
        "kind": "person", "name": "Olga Avramenko",
        "facts": ["Chairs the DNA TSC agenda."], "source": "the 2026-03-02 call"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["path"] == "kg/entities/person/olga-avramenko.md"
    assert body["index"] == "kg/INDEX.md"
    page = (tmp_path / "u_jane" / body["path"]).read_text()
    assert "title: Olga Avramenko" in page
    assert "source: the 2026-03-02 call" in page
    assert "Olga Avramenko" in (tmp_path / "u_jane" / "kg" / "INDEX.md").read_text()


def test_a_second_call_appends_rather_than_replacing(client, tmp_path):
    for fact in ("Chairs the DNA TSC agenda.", "Asked for a standard CLA."):
        client.post("/api/workspace/entity", headers=H, json={
            "kind": "person", "name": "Olga Avramenko", "facts": [fact], "source": "a call"})
    page = (tmp_path / "u_jane" / "kg/entities/person/olga-avramenko.md").read_text()
    assert "Chairs the DNA TSC agenda." in page and "Asked for a standard CLA." in page


def test_a_sourceless_fact_is_refused_with_the_reason_the_agent_has_to_act_on(client, tmp_path):
    r = client.post("/api/workspace/entity", headers=H, json={
        "kind": "person", "name": "Somebody", "facts": ["a claim"], "source": ""})
    # 422, not 400: the request is well-formed and the refusal IS the product — the agent reads the
    # sentence and fixes the fact rather than retrying the call.
    assert r.status_code == 422
    assert "kg/MISSING.md" in r.json()["detail"]
    assert not (tmp_path / "u_jane" / "kg" / "entities").exists()


def test_an_unknown_kind_is_refused_rather_than_guessed_into_a_new_directory(client):
    r = client.post("/api/workspace/entity", headers=H, json={
        "kind": "vendor", "name": "Acme", "facts": ["a fact"], "source": "the web"})
    assert r.status_code == 422


def test_global_is_the_organisation_tier_and_refuses_entities(client):
    r = client.post("/api/workspace/entity", headers=H, json={
        "kind": "company", "name": "Acme", "facts": ["a fact"], "source": "the web",
        "slug": "_global"})
    assert r.status_code == 403


def test_repeating_a_fact_is_a_no_op_the_caller_can_see(client):
    payload = {"kind": "company", "name": "Vexa", "facts": ["Ships a meeting bot."],
               "source": "the README"}
    assert client.post("/api/workspace/entity", headers=H, json=payload).json()["changed"] is True
    second = client.post("/api/workspace/entity", headers=H, json=payload).json()
    assert second["changed"] is False and second["already_recorded"] == 1


def test_unresolved_wikilinks_come_back_as_the_next_calls(client):
    body = client.post("/api/workspace/entity", headers=H, json={
        "kind": "person", "name": "Olga", "facts": ["Works with [[Cottalango Leon]]."],
        "source": "the call"}).json()
    assert body["links_missing"] == ["Cottalango Leon"]
