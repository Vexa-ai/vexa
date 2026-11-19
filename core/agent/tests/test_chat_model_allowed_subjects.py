"""A MODEL OPEN TO NAMED PEOPLE ONLY, END TO END OVER THE PICKER'S ROUTES.

The owner of a personal credential sees and runs the entry; anyone else does not see it, is refused
a pick with the typed `not_permitted` fault, and a turn on a stored pick of it is refused before
anything is spawned. Who the caller is comes from identity (`Dispatcher.identities`).

L2: a real FastAPI app over fakes; no redis, no runtime, no model.
"""
from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from control_plane.api import _Sessions, create_app
from control_plane.model_providers import parse
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests.model_catalogs import ENV, EXAMPLE
from tests.test_chat_model_routes import _Identity, _ModelConfig, _Reader, _Runtime
from control_plane.dispatch import Dispatcher

OWNER, OTHER = "u_owner", "u_other"
EMAILS = {OWNER: "owner@example.test", OTHER: "other@example.test"}


class _Dispatcher(Dispatcher):
    def is_admin(self, subject):
        return False

    def identities(self, subject):
        return [subject, EMAILS.get(subject, "")]


@pytest.fixture
def stack(tmp_path, monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "deployment-key")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    decl = copy.deepcopy(EXAMPLE)
    decl["models"][2]["allowed_subjects"] = ["Owner@example.test"]
    runtime = _Runtime()
    dispatcher = _Dispatcher(load_settings(workspaces_dir=str(root)), runtime, _Identity(),
                             model_config=_ModelConfig(), catalog=parse(json.dumps(decl), ENV))
    sessions = _Sessions()
    app = create_app(dispatcher, stream_reader=_Reader(), reader=WorkspaceReader(str(root)),
                     sessions=sessions)
    return {"client": TestClient(app), "runtime": runtime, "sessions": sessions}


def _as(who):
    return {"X-User-Id": who}


def _ids(stack, who):
    return [m["id"] for m in stack["client"].get("/api/models/catalog", headers=_as(who)).json()["models"]]


def test_the_owner_sees_it_and_others_do_not(stack):
    assert "claude" in _ids(stack, OWNER)
    assert _ids(stack, OTHER) == ["qwen3-32b"]


def test_the_listing_never_carries_the_list(stack):
    text = stack["client"].get("/api/models/catalog", headers=_as(OWNER)).text
    assert "allowed_subjects" not in text and "owner@example.test" not in text.lower()


def test_others_are_refused_a_pick(stack):
    r = stack["client"].post("/api/chat/model", json={"session": "s1", "model": "claude"},
                             headers=_as(OTHER))
    assert r.status_code == 403 and r.json()["fault"]["kind"] == "not_permitted"
    assert stack["sessions"].model(OTHER, "s1") == ""


def test_the_owner_runs_a_turn_on_it(stack):
    c = stack["client"]
    assert c.post("/api/chat/model", json={"session": "s1", "model": "claude"},
                  headers=_as(OWNER)).status_code == 200
    assert c.post("/api/chat", json={"prompt": "hi", "session": "s1"}, headers=_as(OWNER)).status_code == 200
    assert stack["runtime"].envs[-1]["VEXA_RUNNER"] == "claude-code"


def test_a_stored_pick_by_someone_else_refuses_the_turn_before_spawning(stack):
    stack["sessions"].set_model(OTHER, "s1", "claude")
    r = stack["client"].post("/api/chat", json={"prompt": "hi", "session": "s1"}, headers=_as(OTHER))
    assert r.status_code == 403 and r.json()["fault"]["kind"] == "not_permitted"
    assert stack["runtime"].spawned == []
