"""A MODEL PICKED MID-CHAT APPLIES ON THE NEXT TURN, AND THE COMPOSER SAYS WHAT RAN (0.13.3).

Live, 2026-10-14: a chat's worker started on the catalog default (a local Qwen); the person picked a
Claude model a minute later; every later turn stayed on the Qwen worker while the composer showed the
pick. Three rules:

  1. a turn whose chat's model differs from the model its running worker was started on goes to a
     fresh worker on the new model — whatever happened to the stale flag in between;
  2. `GET /api/models/catalog?session=` says which model the chat's worker runs (`running`), so the
     composer shows that and "switching to X on your next message" while the pick is pending; a
     submission queued behind a running turn says the switch is pending;
  3. a person named on a Claude entry starts on Claude, not on the open local model, unless
     `_global/POLICIES.md` says `model_chat_default: catalog`.

L2: a real FastAPI app over fakes; no redis, no runtime, no model.
"""
from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient

from control_plane.api import _Sessions, create_app
from control_plane.dispatch import Dispatcher
from control_plane.model_providers import parse
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from tests.model_catalogs import ENV, EXAMPLE
from tests.test_chat_model_routes import _Identity, _ModelConfig, _Reader

ME = "u_me"
EMAIL = "me@example.test"


class _LiveRuntime:
    """A runtime whose spawned workers stay running — the warm window the defect lives in."""

    def __init__(self):
        self.envs: list[dict] = []
        self.spawned: list[str] = []

    def spawn(self, workload_id, profile, env):
        if workload_id not in self.spawned:
            self.spawned.append(workload_id)
            self.envs.append(env)
        return workload_id

    def await_done(self, workload_id, timeout_sec=0.0):
        return "running" if workload_id in self.spawned else "completed"


class _Dispatcher(Dispatcher):
    def is_admin(self, subject):
        return False

    def identities(self, subject):
        return [subject, EMAIL]


def _stack(tmp_path, monkeypatch, *, named=False, policy=None, global_path=True):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "deployment-key")
    root = tmp_path / "workspaces"
    (root / "_global").mkdir(parents=True)
    if policy is not None:
        (root / "_global" / "POLICIES.md").write_text(f"---\nmodel_chat_default: {policy}\n---\n# Policies\n")
    decl = copy.deepcopy(EXAMPLE)
    if named:
        decl["models"][2]["allowed_subjects"] = [EMAIL]
    runtime = _LiveRuntime()
    extra = {"global_system_workspace_path": str(root / "_global")} if global_path else {}
    dispatcher = _Dispatcher(load_settings(workspaces_dir=str(root), **extra),
                             runtime, _Identity(), model_config=_ModelConfig(),
                             catalog=parse(json.dumps(decl), ENV))
    sessions = _Sessions()
    app = create_app(dispatcher, stream_reader=_Reader(), reader=WorkspaceReader(str(root)),
                     sessions=sessions)
    return {"client": TestClient(app), "runtime": runtime, "sessions": sessions}


def _as():
    return {"X-User-Id": ME}


def _turn(c, path="/api/chat"):
    return c.post(path, json={"prompt": "hello", "session": "s1"}, headers=_as())


def _listing(c):
    return c.get("/api/models/catalog", params={"session": "s1"}, headers=_as()).json()


def test_a_warm_worker_on_another_model_never_answers_the_next_turn(tmp_path, monkeypatch):
    st = _stack(tmp_path, monkeypatch)
    c, rt, sess = st["client"], st["runtime"], st["sessions"]
    assert _turn(c).status_code == 200
    first = rt.spawned[-1]
    assert rt.envs[-1]["VEXA_AGENT_MODEL"] == "Qwen/Qwen3-32B"
    # The pick lands without the stale flag (restart=False) — the flag is not what the rule rests on.
    sess.set_model(ME, "s1", "claude", "", restart=False)
    assert _turn(c).status_code == 200
    assert rt.spawned[-1] != first, "the next turn went to the warm worker still running the old model"
    assert rt.envs[-1]["VEXA_AGENT_MODEL"] == "claude-sonnet-4-6"


def test_a_turn_on_the_model_already_running_keeps_its_warm_worker(tmp_path, monkeypatch):
    st = _stack(tmp_path, monkeypatch)
    c, rt = st["client"], st["runtime"]
    _turn(c)
    _turn(c)
    assert len(rt.spawned) == 1


def test_the_listing_says_what_ran_and_what_is_next(tmp_path, monkeypatch):
    st = _stack(tmp_path, monkeypatch)
    c = st["client"]
    _turn(c)
    assert _listing(c)["running"] == "qwen3-32b"
    c.post("/api/chat/model", json={"session": "s1", "model": "claude"}, headers=_as())
    body = _listing(c)
    assert (body["running"], body["selected"]) == ("qwen3-32b", "claude")
    _turn(c)
    assert _listing(c)["running"] == "claude"


def test_a_submission_behind_a_running_turn_says_the_switch_is_pending(tmp_path, monkeypatch):
    st = _stack(tmp_path, monkeypatch)
    c = st["client"]
    _turn(c)
    c.post("/api/chat/model", json={"session": "s1", "model": "claude"}, headers=_as())
    r = _turn(c, "/api/chat/submit")
    assert r.status_code == 200, r.text
    assert (r.json()["running"], r.json()["model_pending"]) == ("qwen3-32b", "claude")


def test_a_person_named_on_claude_starts_on_claude(tmp_path, monkeypatch):
    st = _stack(tmp_path, monkeypatch, named=True)
    assert _listing(st["client"])["default"] == "claude"
    assert _turn(st["client"]).status_code == 200
    assert st["runtime"].envs[-1]["VEXA_AGENT_MODEL"] == "claude-sonnet-4-6"


def test_the_company_policy_can_keep_the_catalog_default(tmp_path, monkeypatch):
    st = _stack(tmp_path, monkeypatch, named=True, policy="catalog")
    assert _listing(st["client"])["default"] == "qwen3-32b"


def test_a_person_not_named_keeps_the_catalog_default(tmp_path, monkeypatch):
    st = _stack(tmp_path, monkeypatch)
    assert _listing(st["client"])["default"] == "qwen3-32b"


def test_the_company_policy_is_read_from_the_in_store_global_by_default(tmp_path, monkeypatch):
    # The default install names no out-of-store `_global` (VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH unset):
    # `_global` is the in-store directory (`system_mounts.global_root`), and its policy still holds.
    monkeypatch.delenv("VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH", raising=False)
    st = _stack(tmp_path, monkeypatch, named=True, policy="catalog", global_path=False)
    assert _listing(st["client"])["default"] == "qwen3-32b"
