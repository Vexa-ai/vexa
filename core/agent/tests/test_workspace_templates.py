"""Workspace templates (Vexa-ai/vexa#1783, founder 2026-10-14): list, create from a template with the
host's answers, a person's own template winning over the company's, save-as-template, and the seeds
reaching `_global` additively."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane import global_seed
from control_plane import workspace_templates as tpl
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

REPO = Path(__file__).resolve().parents[3]
SEEDS = REPO / "behavior" / "workspace-templates"
H = {"X-User-Id": "u_jane"}


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    seed = tmp_path / "seed"; seed.mkdir(); (seed / "CLAUDE.md").write_text("SEED\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    monkeypatch.setenv("VEXA_WORKSPACE_TEMPLATES_DIR", str(SEEDS))
    monkeypatch.delenv("VEXA_GLOBAL_SYSTEM_WORKSPACE_PATH", raising=False)
    ws = tmp_path / "ws"
    c = TestClient(create_app(Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
                              reader=WorkspaceReader(str(ws))))
    c.post("/api/workspace/init", headers=H)
    return c, ws


def _company(ws: Path) -> Path:
    g = ws / "_global"
    g.mkdir(parents=True, exist_ok=True)
    global_seed.top_up(g, [(SEEDS, global_seed.WORKSPACE_TEMPLATES_DIRNAME)])
    return g


# ── the seeds themselves ───────────────────────────────────────────────────────────────────────

def test_the_four_starter_templates_parse_with_questions_and_pages():
    found = {t.name: t for t in tpl._read_dir(SEEDS, "", "built-in").values()}
    assert set(found) >= {"event-organization", "client-project", "meeting-series", "research"}
    ev = found["event-organization"]
    assert ev.title == "Event organization" and ev.description
    assert [q.key for q in ev.questions] == ["event_name", "event_date", "venue", "capacity", "organizers"]
    assert ev.questions[0].default == "Our event"
    assert any("RSVP" in r for r in ev.routines) and any("digest" in r for r in ev.routines)
    for t in found.values():
        pages = list(tpl._pages(t))
        assert pages and tpl.MANIFEST not in pages
        # every placeholder a page uses is a question or a built-in — nothing renders as {{x}}
        keys = {q.key for q in t.questions} | {"workspace_name", "today"}
        for rel in pages:
            used = set(tpl._PLACEHOLDER.findall((t.path / rel).read_text()))
            assert used <= keys, f"{t.name}/{rel} uses {used - keys}"


def test_the_agent_api_image_ships_the_workspace_templates():
    text = (REPO / "core/agent/services/agent-api/Dockerfile").read_text()
    rel = global_seed.DEFAULT_IMAGE_WORKSPACE_TEMPLATES_DIR[len("/app/"):]
    assert any(l.strip().startswith("COPY ") and l.split()[-1].lstrip("./") == rel for l in text.splitlines())


def test_seeds_reach_global_additively_and_an_edit_is_never_overwritten(tmp_path):
    g = tmp_path / "_global"; g.mkdir()
    added = global_seed.top_up(g, [(SEEDS, global_seed.WORKSPACE_TEMPLATES_DIRNAME)])
    assert "workspace-templates/event-organization/TEMPLATE.md" in added
    edited = g / "workspace-templates/event-organization/overview.md"
    edited.write_text("# our own shape\n")
    assert global_seed.top_up(g, [(SEEDS, global_seed.WORKSPACE_TEMPLATES_DIRNAME)]) == []
    assert edited.read_text() == "# our own shape\n"


# ── the routes ─────────────────────────────────────────────────────────────────────────────────

def test_list_offers_the_company_templates_with_descriptions_and_questions(env):
    c, ws = env
    _company(ws)
    r = c.get("/api/workspace/templates", headers=H)
    assert r.status_code == 200
    by = {t["name"]: t for t in r.json()["templates"]}
    assert by["event-organization"]["source"] == "company"
    assert by["event-organization"]["description"].startswith("Plan and run an event")
    assert by["event-organization"]["questions"][0]["key"] == "event_name"
    assert by["event-organization"]["suggested_routines"]


def test_list_falls_back_to_the_image_when_global_has_none(env):
    c, _ws = env
    by = {t["name"]: t for t in c.get("/api/workspace/templates", headers=H).json()["templates"]}
    assert by["research"]["source"] == "built-in"


def test_create_from_template_fills_answers_and_commits(env):
    c, ws = env
    _company(ws)
    r = c.post("/api/workspace/create", headers=H, json={
        "name": "AI meetup October", "template": "Event organization",
        "answers": {"event_name": "AI Tinkerers Lisbon", "event_date": "2026-10-30 18:30",
                    "capacity": "80", "nonsense": "x"}})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["created"] == b["slug"] == "ai-meetup-october" and b["template"] == "event-organization"
    assert "overview.md" in b["pages"] and "TEMPLATE.md" not in b["pages"]
    assert b["unanswered"] == ["organizers"]          # venue has a default
    assert b["ignored_answers"] == ["nonsense"]
    assert b["suggested_routines"]
    slot = ws / ".attached" / "u_jane" / b["slug"]
    ov = (slot / "overview.md").read_text()
    assert "# AI Tinkerers Lisbon" in ov and "| **Capacity** | 80 |" in ov
    assert "venue to confirm" in ov and tpl.UNANSWERED in ov and "{{" not in ov
    assert (slot / "CLAUDE.md").read_text() == "SEED\n"   # the ordinary seed is still there
    assert _git(slot, "status", "--porcelain") == ""
    assert "template: event-organization" in _git(slot, "log", "--format=%s")
    view = c.get("/api/workspace/attached", headers=H).json()
    assert view["slots"]["ai-meetup-october"]["name"] == "AI meetup October"


def test_create_without_template_is_a_blank_workspace(env):
    c, ws = env
    b = c.post("/api/workspace/create", headers=H, json={"name": "Scratch"}).json()
    assert b["slug"] == "scratch" and "template" not in b


def test_create_with_unknown_template_names_the_ones_there_are(env):
    c, ws = env
    _company(ws)
    r = c.post("/api/workspace/create", headers=H, json={"template": "wedding"})
    assert r.status_code == 404 and "event-organization" in r.json()["detail"]
    assert not (ws / ".attached" / "u_jane" / "workspace-1").exists()


def test_a_persons_own_template_wins_over_the_company_one(env):
    c, ws = env
    _company(ws)
    mine = ws / "u_jane" / tpl.PERSONAL_DIR / "event-organization"
    mine.mkdir(parents=True)
    (mine / "TEMPLATE.md").write_text("---\ntitle: Event organization\ndescription: My way.\n---\n"
                                      "## Questions\n- `event_name` — Name?\n")
    (mine / "plan.md").write_text("# {{event_name}} plan\n")
    by = {t["name"]: t for t in c.get("/api/workspace/templates", headers=H).json()["templates"]}
    assert by["event-organization"]["source"] == "yours" and by["event-organization"]["description"] == "My way."
    b = c.post("/api/workspace/create", headers=H, json={"template": "event-organization",
                                                          "answers": {"event_name": "Demo night"}}).json()
    assert b["pages"] == ["plan.md"]
    assert (ws / ".attached" / "u_jane" / b["slug"] / "plan.md").read_text() == "# Demo night plan\n"


def test_save_as_template_then_create_from_it(env):
    c, ws = env
    _company(ws)
    b = c.post("/api/workspace/create", headers=H, json={"name": "Board", "template": "meeting-series",
                                                          "answers": {"series_name": "Board"}}).json()
    slot = ws / ".attached" / "u_jane" / b["slug"]
    (slot / "kg" / "entities").mkdir(parents=True, exist_ok=True)
    (slot / "kg" / "entities" / "someone.md").write_text("private record\n")
    r = c.post("/api/workspace/templates/save", headers=H,
               json={"name": "Board meeting", "slug": b["slug"], "description": "Our board."})
    assert r.status_code == 201, r.text
    saved = r.json()
    assert saved["name"] == "board-meeting" and "overview.md" in saved["pages"]
    assert not any(p.startswith("kg/") for p in saved["pages"]) and "CLAUDE.md" not in saved["pages"]
    home = ws / "u_jane"
    assert (home / tpl.PERSONAL_DIR / "board-meeting" / "TEMPLATE.md").is_file()
    assert _git(home, "status", "--porcelain", "--", tpl.PERSONAL_DIR) == ""
    again = c.post("/api/workspace/templates/save", headers=H, json={"name": "Board meeting", "slug": b["slug"]})
    assert again.status_code == 409 and "replace" in again.json()["detail"]
    by = {t["name"]: t for t in c.get("/api/workspace/templates", headers=H).json()["templates"]}
    assert by["board-meeting"]["source"] == "yours" and by["board-meeting"]["description"] == "Our board."
    nb = c.post("/api/workspace/create", headers=H, json={"template": "board-meeting", "name": "Board 2"}).json()
    assert "overview.md" in nb["pages"]


def test_a_workspace_created_from_a_template_joins_the_chat_like_workspace_new():
    """`workspace_create` earns the same `focus` event as `workspace_new`: made in a chat, the chat
    writes to it from then on."""
    import json
    from llm.tool_events import _FOCUS_TOOLS, _workspace_focus
    assert {"workspace_create", "mcp__vexa__workspace_create"} <= _FOCUS_TOOLS
    ev = _workspace_focus(json.dumps({"created": "ai-meetup", "name": "AI meetup"}))
    assert ev and ev["workspace"] == "ai-meetup"
