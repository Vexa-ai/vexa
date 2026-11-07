"""`propose` and `validate` — the claim book's two verbs, both served by the one MCP.

The desk setup card (`behavior/queue/desk_setup.human.md`) asks for ONE `propose(claims=[...])` call
and then ONE `validate(verdicts=[...])` call with the person's whole answer; the question card
(`desk_claim.human.md`) asks for the second. `propose` was a route with an untyped body the edge could
not bind; `validate` had no route at all — it lived only in the dogfood rig, so on a standard
deployment a person could answer every question and nothing would record it.

What has to be true:

* `POST /api/claims` publishes a named body and the manifest serves it as `propose`;
* `POST /api/claims/verdicts` records a person's word on each claim — `confirmed` → `validated`,
  `corrected` keeps the original beside the note, `rejected` — and the manifest serves it as
  `validate`;
* a claim the person stood behind makes the desk READY (`.scaffolded`), once — the setup card's whole
  ask — and a batch of rejections does not;
* an unknown id or an unknown verdict is reported per item and never aborts the rest of the answer.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

AGENT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((AGENT / "mcp.tools.v1.json").read_text())
H = {"X-User-Id": "u_jane"}


def _client(tmp_path, monkeypatch) -> TestClient:
    seed = tmp_path / "seed"
    (seed / "agents").mkdir(parents=True)
    (seed / "CLAUDE.md").write_text("root\n")
    (seed / "agents" / "meeting.md").write_text("cfg\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    monkeypatch.delenv("VEXA_FLOWS_API_URL", raising=False)
    c = TestClient(create_app(Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
                              reader=WorkspaceReader(str(tmp_path / "ws"))))
    c.post("/api/workspace/init", headers=H)
    return c


def _desk(tmp_path) -> Path:
    return tmp_path / "ws" / "u_jane"


def _book(tmp_path) -> dict:
    return json.loads((_desk(tmp_path) / "_pending" / "claims.json").read_text())


def _propose(c, *claims):
    r = c.post("/api/claims", headers=H, json={"claims": list(claims)})
    assert r.status_code == 200, r.text
    return r.json()["ids"]


def _tool(name):
    return next((t for t in MANIFEST["tools"] if t["name"] == name), None)


# ── the edge serves both verbs ───────────────────────────────────────────────────────────────────

def test_propose_and_validate_are_served_under_the_names_the_desk_cards_use(tmp_path, monkeypatch):
    spec = _client(tmp_path, monkeypatch).app.openapi()
    for name, path, arg in (("propose", "/api/claims", "claims"),
                            ("validate", "/api/claims/verdicts", "verdicts")):
        tool = _tool(name)
        assert tool is not None, f"{name} is named by behavior/queue and not served"
        assert tool["route"] == {"method": "POST", "path": path}
        assert tool["arguments"] == [arg]
        schema = spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
        body = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
        assert arg in body["properties"], f"{path} publishes no `{arg}` for the edge to bind"


# ── a person's word, recorded ────────────────────────────────────────────────────────────────────

def test_each_verdict_moves_its_claim(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _propose(c, "They run treasury on Teams", "Four-week pilot", "Based in Vienna")
    r = c.post("/api/claims/verdicts", headers=H, json={"verdicts": [
        {"id": "c001", "verdict": "confirmed"},
        {"id": "c002", "verdict": "corrected", "note": "six weeks, not four"},
        {"id": "c003", "verdict": "rejected"}]})
    assert r.status_code == 200, r.text
    assert [(v["id"], v["state"], v["usable_as_context"]) for v in r.json()["recorded"]] == [
        ("c001", "validated", True), ("c002", "corrected", True), ("c003", "rejected", False)]
    book = {c_["id"]: c_ for c_ in _book(tmp_path)["claims"]}
    assert book["c001"]["state"] == "validated"
    # `corrected` keeps the original beside the person's correction
    assert book["c002"]["claim"] == "Four-week pilot"
    assert book["c002"]["human_note"] == "six weeks, not four"
    assert book["c003"]["state"] == "rejected"
    assert all(book[i]["validated_at"] for i in book)


def test_a_claim_a_person_stood_behind_makes_the_desk_ready_once(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _propose(c, "one", "two")
    marker = _desk(tmp_path) / ".scaffolded"
    assert not marker.exists()
    r = c.post("/api/claims/verdicts", headers=H,
               json={"verdicts": [{"id": "c001", "verdict": "confirmed"}]})
    assert r.json()["workspace_ready"] is True
    first = json.loads(marker.read_text())
    assert first["ready"] is True and first["validated_claims"] == 1
    r = c.post("/api/claims/verdicts", headers=H,
               json={"verdicts": [{"id": "c002", "verdict": "confirmed"}]})
    assert "workspace_ready" not in r.json()
    assert json.loads(marker.read_text()) == first, "a second answer rewrote the ready marker"


def test_rejections_alone_do_not_make_the_desk_ready(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _propose(c, "one")
    r = c.post("/api/claims/verdicts", headers=H,
               json={"verdicts": [{"id": "c001", "verdict": "rejected"}]})
    assert r.status_code == 200
    assert not (_desk(tmp_path) / ".scaffolded").exists()


def test_a_bad_item_is_reported_and_the_rest_of_the_answer_still_lands(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _propose(c, "one")
    r = c.post("/api/claims/verdicts", headers=H, json={"verdicts": [
        {"id": "c404", "verdict": "confirmed"},
        {"id": "c001", "verdict": "maybe"},
        {"id": "c001", "verdict": "confirmed"}]})
    assert r.status_code == 200
    body = r.json()
    assert [e["id"] for e in body["errors"]] == ["c404", "c001"]
    assert [v["id"] for v in body["recorded"]] == ["c001"]


def test_an_empty_answer_is_refused(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    _propose(c, "one")
    assert c.post("/api/claims/verdicts", headers=H, json={"verdicts": []}).status_code == 400
