"""`claim.proposed` — an agent needs a person's word, and the queue is how it asks.

PRD ruling 9: desk cards are agent events in the FULL profile. #1482 registered the event type with
`desk_claim` behind it and left the producer's side empty, and the reason it was empty is worth
stating: the claim book was written through agent-api's GENERIC file route, so agent-api held the
bytes and knew nothing about what they meant. A claim being proposed was indistinguishable from any
other file write, at the only place that could have published it.

`POST /api/claims` owns the book and publishes ONE event per claim; the rig's `propose` forwards to
it. A PUBLISH EDGE IS NOT A DEPENDENCY (`deploy/contracts/config.v1/README.md`): the fact is handed
over best-effort and swallowed, and a deployment with no flows domain records claims identically.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

# ── the rig ──────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def flows(monkeypatch):
    """A flows intake that records what it was handed. Nothing here asserts on HTTP: the publisher
    is the unit, and what matters is WHICH facts left the building and how many times."""
    sent: list = []

    def _fake_urlopen(req, timeout=None):            # noqa: ANN001
        sent.append({"url": req.full_url,
                     "headers": {k.lower(): v for k, v in req.header_items()},
                     "body": json.loads(req.data.decode())})

        class _R:
            status = 200

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _R()

    from control_plane import publish as publish_mod
    monkeypatch.setattr(publish_mod.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://flows:18200")
    monkeypatch.setenv("VEXA_FLOWS_API_KEY", "test-operator-key")
    return sent


def _client(tmp_path, monkeypatch) -> TestClient:
    seed = tmp_path / "seed"
    (seed / "agents").mkdir(parents=True)
    (seed / "CLAUDE.md").write_text("root\n")
    (seed / "agents" / "meeting.md").write_text("cfg\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(tmp_path / "ws")),
    ))


def _of(sent, event_type):
    return [s for s in sent if s["body"]["event_type"] == event_type]


# ── claim.proposed — one per claim, from a route that owns the book ──────────────────────────────

def test_proposing_claims_writes_the_book_and_tells_flows_once_per_claim(tmp_path, monkeypatch, flows):
    """ONE EVENT PER CLAIM, because the queue card is one claim: `await_claim` looks a `claim_id` up
    in the book and blocks on that claim's own words. A single event for a batch would put one card
    in front of a person for three questions and there would be no way to answer two of them."""
    c = _client(tmp_path, monkeypatch)
    c.post("/api/workspace/init", headers={"X-User-Id": "u_jane"})
    r = c.post("/api/claims", headers={"X-User-Id": "u_jane"},
               json={"claims": [{"claim": "They run treasury on Teams", "source": "the invite"},
                                {"claim": "Four-week pilot"},
                                "Security review before the pilot"]})
    assert r.status_code == 200, r.text
    assert r.json()["ids"] == ["c001", "c002", "c003"]

    book = json.loads((tmp_path / "ws" / "u_jane" / "_pending" / "claims.json").read_text())
    assert [c_["id"] for c_ in book["claims"]] == ["c001", "c002", "c003"]
    assert all(c_["state"] == "proposed" for c_ in book["claims"])

    told = _of(flows, "claim.proposed")
    # `refs`, the field flows' EventSubmission actually reads — see test_desk_events.py's
    # test_the_body_carries_refs_under_the_name_the_intake_reads for what the other spelling costs.
    assert [t["body"]["refs"]["claim_id"] for t in told] == ["c001", "c002", "c003"]
    assert {t["body"]["refs"]["uid"] for t in told} == {"u_jane"}
    # keyed to (person, claim) so a redelivery dedupes at the flows intake
    assert [t["body"]["source_event_id"] for t in told] == \
        ["claim-u_jane-c001", "claim-u_jane-c002", "claim-u_jane-c003"]


def test_a_second_call_tells_flows_only_about_the_new_claims(tmp_path, monkeypatch, flows):
    """Once per CAUSE, again: the cause is a claim being proposed, not a call being made."""
    c = _client(tmp_path, monkeypatch)
    c.post("/api/workspace/init", headers={"X-User-Id": "u_jane"})
    c.post("/api/claims", headers={"X-User-Id": "u_jane"}, json={"claims": ["one"]})
    flows.clear()
    c.post("/api/claims", headers={"X-User-Id": "u_jane"}, json={"claims": ["two"]})
    told = _of(flows, "claim.proposed")
    assert [t["body"]["refs"]["claim_id"] for t in told] == ["c002"]


def test_claims_are_recorded_when_flows_is_not_deployed(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("VEXA_FLOWS_API_URL", raising=False)
    c.post("/api/workspace/init", headers={"X-User-Id": "u_jane"})
    r = c.post("/api/claims", headers={"X-User-Id": "u_jane"}, json={"claims": ["one"]})
    assert r.status_code == 200
    book = json.loads((tmp_path / "ws" / "u_jane" / "_pending" / "claims.json").read_text())
    assert book["claims"][0]["claim"] == "one"


def test_an_empty_batch_is_refused_rather_than_published(tmp_path, monkeypatch, flows):
    c = _client(tmp_path, monkeypatch)
    c.post("/api/workspace/init", headers={"X-User-Id": "u_jane"})
    assert c.post("/api/claims", headers={"X-User-Id": "u_jane"},
                  json={"claims": []}).status_code == 400
    assert _of(flows, "claim.proposed") == []


