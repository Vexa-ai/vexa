"""`company_context` — what a person has stood behind about their company (Vexa-ai/vexa#1783).

The dogfood control rig served it by reading the claim book itself; the book is the agent domain's
(`control_plane.claims`), so the read is a route there and the one MCP serves it. What has to be true:

* only claims a person confirmed or corrected are context; proposed ones are counted, never shown;
* a desk with no book is an empty answer, not an error;
* the manifest serves it as `company_context`, with no argument — the caller's desk is the only one.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.test_claim_verdicts import H, _client, _propose

AGENT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((AGENT / "mcp.tools.v1.json").read_text())


def test_only_what_a_person_stood_behind_is_context(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    ids = _propose(c, "They sell to banks.", "They have 40 staff.", "They are in Lisbon.")
    r = c.post("/api/claims/verdicts", headers=H, json={"verdicts": [
        {"id": ids[0], "verdict": "confirmed"},
        {"id": ids[1], "verdict": "corrected", "note": "About 60."},
    ]})
    assert r.status_code == 200, r.text
    r = c.get("/api/claims", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [v["claim"] for v in body["validated"]] == ["They sell to banks.", "They have 40 staff."]
    assert body["validated"][1]["note"] == "About 60."
    assert body["still_proposed"] == 1 and body["rejected"] == 0


def test_a_desk_with_no_book_has_no_context(tmp_path, monkeypatch):
    r = _client(tmp_path, monkeypatch).get("/api/claims", headers=H)
    assert r.status_code == 200, r.text
    assert r.json() == {"validated": [], "still_proposed": 0, "rejected": 0}


def test_the_manifest_serves_it_as_company_context():
    tool = next((t for t in MANIFEST["tools"] if t["name"] == "company_context"), None)
    assert tool is not None
    assert tool["route"] == {"method": "GET", "path": "/api/claims"}
    assert not tool.get("arguments")
