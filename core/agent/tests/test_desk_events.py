"""THE AGENT DOMAIN TELLS FLOWS WHAT CHANGED ON A DESK — the two carriers slice 4 declared.

PRD ruling 9: desk cards are agent events in the FULL profile. #1482 registered `desk.unscaffolded`
and `claim.proposed` as flows event types with `desk_setup` and `desk_claim` behind them, and left a
hole where the producer should be: the flows definitions can react to both facts and nothing in the
repository publishes either. A queue that cannot see a desk waiting is a queue that tells a person
they have nothing to do while their own setup sits half-finished — and a person's own Claude Code
and the cloud desk then disagree about what is waiting, which is the one thing the queue exists to
prevent.

A PUBLISH EDGE IS NOT A DEPENDENCY (`deploy/contracts/config.v1/README.md`). Everything below is a
statement of that: the facts are handed over best-effort and swallowed, agent-api runs identically
with no flows deployed, and the no-agents profile publishes nothing because it carries no agent code
at all.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings

from tests.test_api import _FakeIdentity, _FakeRuntime

REPO = next(p for p in Path(__file__).resolve().parents if (p / "core" / "agent").is_dir())


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


# ── desk.unscaffolded — once per desk, and only for a desk with no scaffold ──────────────────────

def test_a_new_desk_without_a_scaffold_tells_flows_once(tmp_path, monkeypatch, flows):
    """ONCE PER CAUSE, and the cause is the desk being created. `ws_init` is called on EVERY login
    (its own docstring: *"Idempotent — safe to call on every login"*), so a publish that fired per
    call would put one card on the queue per sign-in, forever, for a person who never finished
    setup — the queue would fill with the same card and the person would learn to ignore it."""
    c = _client(tmp_path, monkeypatch)
    assert c.post("/api/workspace/init", headers={"X-User-Id": "u_jane"}).status_code == 201
    assert c.post("/api/workspace/init", headers={"X-User-Id": "u_jane"}).status_code == 201

    told = _of(flows, "desk.unscaffolded")
    assert len(told) == 1, [t["body"] for t in told]
    body = told[0]["body"]
    assert body["subject_refs"]["uid"] == "u_jane"       # the ref `await_scaffold` requires
    assert body["source_event_id"] == "desk-u_jane"      # keyed to the PERSON: a redelivery dedupes
    assert told[0]["url"].endswith("/events")
    assert told[0]["headers"]["x-flows-admin-key"] == "test-operator-key"


def test_a_desk_that_already_carries_a_scaffold_tells_flows_nothing(tmp_path, monkeypatch, flows):
    """The half a blanket publish would get wrong. `.scaffolded` is the marker a finished setup
    leaves; a desk that has one is not waiting for anything, and a card asking a person to finish
    what they already finished is worse than no card."""
    c = _client(tmp_path, monkeypatch)
    ws = tmp_path / "ws" / "u_jane"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".scaffolded").write_text("done\n")
    c.post("/api/workspace/init", headers={"X-User-Id": "u_jane"})
    assert _of(flows, "desk.unscaffolded") == []


def test_a_desk_is_still_created_when_flows_is_not_deployed(tmp_path, monkeypatch):
    """A PUBLISH IS NOT A DEPENDENCY. With no flows domain configured the fact is dropped and the
    desk is provisioned exactly as it is with one — this is the no-flows PROFILE, not a degraded
    state, and the route must not know the difference."""
    monkeypatch.delenv("VEXA_FLOWS_API_URL", raising=False)
    monkeypatch.delenv("VEXA_FLOWS_API_KEY", raising=False)
    c = _client(tmp_path, monkeypatch)
    r = c.post("/api/workspace/init", headers={"X-User-Id": "u_jane"})
    assert r.status_code == 201 and r.json()["seeded"] is True
    assert (tmp_path / "ws" / "u_jane" / ".git").exists()


def test_a_flows_that_refuses_or_hangs_never_costs_a_person_their_desk(tmp_path, monkeypatch):
    """The other half: configured, and broken. Swallowed — the alternative is refusing to provision
    somebody's desk over a message they never asked us to send."""
    from control_plane import publish as publish_mod

    def _boom(req, timeout=None):                     # noqa: ANN001
        raise OSError("flows is down")

    monkeypatch.setattr(publish_mod.urllib.request, "urlopen", _boom)
    monkeypatch.setenv("VEXA_FLOWS_API_URL", "http://flows:18200")
    c = _client(tmp_path, monkeypatch)
    assert c.post("/api/workspace/init", headers={"X-User-Id": "u_jane"}).status_code == 201


# ── the declaration: who owns these facts, and where they may be published from ──────────────────

def _carriers() -> dict:
    return {c["event"]: c for c in json.loads(
        (REPO / "core/flows/contracts/flows.v1/carriers.json").read_text())["carriers"]}


def _agent_config() -> dict:
    return json.loads((REPO / "core/agent/control_plane/config.v1.json").read_text())


@pytest.mark.parametrize("event", ["desk.unscaffolded", "claim.proposed"])
def test_the_carrier_is_registered_and_owned_by_the_agent_domain(event):
    """One carrier, one producing domain — the census is what makes that answerable from a
    declaration instead of from grep."""
    c = _carriers()[event]
    assert c["owner"] == "agent"
    assert "uid" in c["refs"]


@pytest.mark.parametrize("event", ["desk.unscaffolded", "claim.proposed"])
def test_agent_api_declares_the_publish_edge_it_hands_them_over(event):
    edges = [k for k in _agent_config()["keys"] if k.get("class") == "publish-edge"]
    assert edges, "agent-api declares no publish edge at all"
    assert any(event in (k.get("publishes_events") or []) for k in edges)
    for k in edges:
        assert "default" not in k, (
            f"{k['key']} is a publish edge with a default — a fallback address to publish to, "
            "invented by us, in a deployment that deliberately runs no such domain")


def test_the_no_agents_profile_publishes_neither_because_it_carries_no_agent_code():
    """PRD decision 40.6: the no-agents product is gateway + meetings + flows + identity. These two
    facts are the AGENT domain's to know, so the only code that publishes them lives under
    `core/agent/` — a deployment without the agent domain does not have a switched-off publisher,
    it has no publisher. The check is structural because that is the only way to state it: there is
    no runtime in which to observe code that is not there."""
    others = []
    for root in ("core/flows/src", "core/meetings", "core/identity", "core/gateway", "core/runtime"):
        for f in sorted((REPO / root).rglob("*.py")):
            text = f.read_text(encoding="utf-8", errors="ignore")
            if "desk.unscaffolded" in text or "claim.proposed" in text:
                others.append(str(f.relative_to(REPO)))
    # flows CONSUMES both (it registers the event types and the flows behind them) — consuming is
    # not publishing, so the definitions are named here as the known, correct exception.
    others = [o for o in others if o != "core/flows/src/flows_defs/production.py"]
    assert others == [], f"a non-agent domain names these carriers: {others}"
