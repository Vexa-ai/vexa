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

import ast
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
    assert body["refs"]["uid"] == "u_jane"               # the ref `await_scaffold` requires
    assert body["source_event_id"] == "desk-u_jane"      # keyed to the PERSON: a redelivery dedupes
    assert told[0]["url"].endswith("/events")
    assert told[0]["headers"]["x-flows-operator-key"] == "test-operator-key"


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


# WHAT THIS DOMAIN PUBLISHES IS DERIVED FROM ITS OWN SOURCE, never from a list written here.
# A list in a test is a third place the answer lives, and it agrees with the declaration for
# exactly as long as one person edits both: the list is right, the declaration is right, and the
# CODE has quietly grown a third event nobody registered. #1482 shipped the inverse of this — two
# event types registered in flows with nothing publishing them — and it passed every test it had,
# because every test named the two events by hand.
def _published_events() -> set:
    """The event types this domain actually hands over, read off `control_plane/publish.py`.

    The module's `EVENT_*` constants ARE the set: nothing may publish a bare string (asserted
    below), so a constant is the only way an event leaves this domain and adding one is the only
    way to grow the set."""
    tree = ast.parse((REPO / "core/agent/control_plane/publish.py").read_text())
    out = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.startswith("EVENT_"):
                    out.add(node.value.value)
    return out


def _agent_sources() -> list:
    return [f for f in sorted((REPO / "core/agent").rglob("*.py"))
            if "/tests/" not in str(f) and "/.venv/" not in str(f)]


def test_every_event_this_domain_publishes_is_declared_on_the_publish_edge():
    """Derived → declared. A new `EVENT_*` with no entry in `publishes_events` is a fact leaving
    the building that no config contract, no carrier census and no consumer knows about."""
    declared = set()
    for k in _agent_config()["keys"]:
        if k.get("class") == "publish-edge":
            declared |= set(k.get("publishes_events") or [])
    published = _published_events()
    assert published, "control_plane/publish.py defines no EVENT_* constant at all"
    assert published == declared, (
        f"the source publishes {sorted(published)} and the declaration names {sorted(declared)}; "
        f"declare it in core/agent/control_plane/config.v1.json or stop publishing it")


def test_every_event_the_declaration_names_is_registered_to_this_domain_in_the_census():
    """Declared → census. The other direction, and the one that keeps `publishes_events` from
    being a comment: a carrier is an event type with exactly ONE producing domain."""
    carriers = _carriers()
    for event in _published_events():
        assert event in carriers, (
            f"{event} is published by this domain and registered in no census — add it to "
            f"core/flows/contracts/flows.v1/carriers.json")
        assert carriers[event]["owner"] == "agent", (
            f"{event} is published here and the census records it as owned by "
            f"{carriers[event]['owner']}")
        assert "uid" in carriers[event]["refs"], (
            f"{event} promises no uid, and both desk steps look a person up by one")


def test_nothing_publishes_a_bare_event_string():
    """What makes the derivation above sound. `publish("desk.unscaffolded", ...)` written inline
    would be a fact this domain hands over that `_published_events` cannot see, and every check on
    this page would stay green while the census went out of date."""
    offenders = []
    for f in _agent_sources():
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8", errors="ignore"))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name != "publish" or not node.args:
                continue
            if isinstance(node.args[0], ast.Constant):
                offenders.append(f"{f.relative_to(REPO)}:{node.lineno}")
    assert offenders == [], (
        f"a publish call names its event type inline instead of an EVENT_* constant: {offenders}")


def test_the_publish_edge_carries_no_default_and_names_a_deploy_surface():
    """Two properties of the KEY rather than of the event.

    NO DEFAULT, because a fallback address to publish to — invented by us, in a deployment that
    deliberately runs no such domain — is the one value this class must not have.

    AND A SURFACE. `targets: []` is how a declaration passes gate:config-contract while reaching no
    deployment at all: the gate checks a key against the surfaces its own `targets` names, so an
    empty list asks it to check nothing. This edge was declared, gated, tested and configured
    NOWHERE, and everything was green — the queue would simply have had no desk cards in it."""
    edges = [k for k in _agent_config()["keys"] if k.get("class") == "publish-edge"]
    assert edges, "agent-api declares no publish edge at all"
    for k in edges:
        assert "default" not in k, (
            f"{k['key']} is a publish edge with a default — a fallback address to publish to, "
            "invented by us, in a deployment that deliberately runs no such domain")
        assert "compose" in (k.get("targets") or []), (
            f"{k['key']} names no deploy surface, so no gate ever checks that a deployment sets "
            f"it and the facts are dropped in every configuration we ship")
    # That the compose agent-api service SETS them is asserted where a test may read a deploy
    # path — `deploy/compose/tests/flows_wiring_test.py` — because nothing under core/ may
    # (tests/test_no_deploy_reads.py). gate:config-contract checks it a third time, believing
    # `targets`, which is the field the whole failure was hiding in.


def test_the_body_carries_refs_under_the_name_the_intake_reads(tmp_path, monkeypatch, flows):
    """THE ONE FAILURE ON THIS PATH THAT REPORTS ITSELF AS WORKING.

    flows' intake is `EventSubmission(event_type, source_event_id, refs)` — a plain pydantic
    BaseModel, so an unknown key is IGNORED, not refused. A body that spells the field
    `subject_refs` is admitted with `202` and `refs == {}`; `await_scaffold` then raises
    *"desk.unscaffolded carried no uid"* non-retryably on every card, and `await_claim` raises its
    equivalent. Nothing on either side reports an error: the publisher sees 202, flows records an
    admitted fact, and the queue is empty.

    So the field name is asserted here rather than left to read correctly. The producing domain
    cannot import the consuming one to check — that is the point of a publish edge — which is
    exactly why the spelling needs a test on each side instead of a shared type."""
    c = _client(tmp_path, monkeypatch)
    c.post("/api/workspace/init", headers={"X-User-Id": "u_jane"})
    body = _of(flows, "desk.unscaffolded")[0]["body"]
    assert "subject_refs" not in body, (
        "the intake ignores this key rather than refusing it — the card would be admitted with no "
        "uid and die non-retryably, with a 202 on our side and nothing on the queue")
    assert set(body) == {"event_type", "source_event_id", "refs"}, (
        f"the intake reads exactly these three fields; this body has {sorted(body)}")


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
