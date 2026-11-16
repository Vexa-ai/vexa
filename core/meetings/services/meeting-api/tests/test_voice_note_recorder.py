"""Record a voice note, asked of the agent: `record_voice_note` -> `POST /voice-notes/recorder`.

The agent has no microphone. The tool's whole effect is an answer the person's open Minutes tab acts
on (`ui_action: open_voice_note`, the shape `connection_request` answers with), so the recording is
always the person's own press. A worker with nobody in the loop is refused: nobody is there to speak.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app, identity_token
from meeting_api.regime import REFUSAL

KEY = identity_token.generate_signing_key()
INTERNAL = "meeting-test-internal-secret"
MEETINGS = pathlib.Path(__file__).resolve().parents[3]


@pytest.fixture
def client():
    return TestClient(create_app(identity_key=KEY.public_key(), internal_secret=INTERNAL))


def _signed(**delegation) -> dict:
    claims = {"sub": "7", "scopes": ["bot", "tx"], "limits": 3, "workspaces": ["ws_1"],
              "writable_workspaces": ["ws_1"]}
    if delegation:
        claims["delegation"] = delegation
    return {identity_token.HEADER: identity_token.sign(KEY, claims)}


@pytest.mark.parametrize("regime", ["autonomous", "", "event"])
def test_a_background_worker_is_refused(client, regime):
    r = client.post("/voice-notes/recorder", headers=_signed(regime=regime, workspaces=["ws_1"]))
    assert r.status_code == 403
    assert r.json()["detail"] == REFUSAL


def test_a_worker_with_its_person_in_the_chat_opens_the_recorder(client):
    r = client.post("/voice-notes/recorder", headers=_signed(regime="human", workspaces="*"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ui_action"] == "open_voice_note"
    assert body["status"] == "awaiting_user"
    # The answer tells the agent it recorded nothing, and where the note will be.
    assert "list_meetings" in body["instruction"]
    assert "voice_note" in body["instruction"]


def test_a_person_s_own_credential_opens_the_recorder(client):
    r = client.post("/voice-notes/recorder", headers=_signed())
    assert r.status_code == 200, r.text
    assert r.json()["ui_action"] == "open_voice_note"


def test_no_identity_is_no_recorder(client):
    assert client.post("/voice-notes/recorder").status_code == 401


def test_the_tool_is_declared_and_described_for_an_agent():
    manifest = json.loads((MEETINGS / "mcp.tools.v1.json").read_text())
    tool = next(t for t in manifest["tools"] if t["name"] == "record_voice_note")
    assert tool["route"] == {"method": "POST", "path": "/voice-notes/recorder"}
    slice_ = json.loads((MEETINGS / "mcp.tools.v1.openapi.json").read_text())
    desc = slice_["paths"]["/voice-notes/recorder"]["post"]["description"]
    assert len([s for s in re.split(r"(?<=[.!?])\s+", desc.strip()) if s]) >= 2, desc


def test_the_gateway_fronts_the_route_for_the_mcp():
    routes = json.loads((MEETINGS / "routes.v1.json").read_text())["routes"]
    row = next(r for r in routes if r["method"] == "POST" and r["path"] == "/voice-notes/recorder")
    assert row["scopes"] == ["bot", "tx"]
    assert row.get("mcp_reentry") is True
