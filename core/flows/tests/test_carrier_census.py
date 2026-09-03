"""THE CARRIER CENSUS AND THE REPOSITORY AGREE — in both directions.

A carrier is an event type with exactly ONE producing domain. `core/flows/contracts/flows.v1/
carriers.json` is where that ownership is written down, and it is worth exactly as much as its
agreement with the two other places the same fact appears: each domain's own manifest
(`core/*/mcp.tools.v1.json` → `publishes_events`) and each service's config declaration
(`config.v1.json` → a `publish-edge` key's `publishes_events`, checked by gate:config-contract).

WHY BOTH DIRECTIONS. A census that is merely a superset drifts into a wish-list — entries for facts
nobody publishes any more, which is the failure gate:contract-version records for the seal file (a
pin with nothing behind it freezes nothing). A census that is merely a subset is worse: it means a
domain is publishing a fact that no consumer contract describes, and the first thing anybody knows
about it is a consumer acting on refs that were never promised.

Offline, stdlib only — this reads committed files and nothing else.
"""
from __future__ import annotations

import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
CENSUS = REPO / "core" / "flows" / "contracts" / "flows.v1" / "carriers.json"
MANIFESTS = sorted(REPO.glob("core/*/mcp.tools.v1.json")) + \
            sorted(REPO.glob("core/*/services/*/mcp.tools.v1.json"))
DECLARATIONS = sorted(REPO.glob("core/*/services/*/src/*/config.v1.json")) + \
               sorted(REPO.glob("core/*/*/config.v1.json")) + \
               sorted(REPO.glob("core/*/src/*/config.v1.json"))


def _census() -> dict:
    return json.loads(CENSUS.read_text())


def _owners() -> dict:
    return {c["event"]: c["owner"] for c in _census()["carriers"]}


def test_the_census_exists_and_is_a_flows_v1_registry():
    assert CENSUS.is_file(), f"no carrier census at {CENSUS.relative_to(REPO)}"
    assert _census()["contract"] == "flows.v1"


def test_every_carrier_has_exactly_one_producing_domain():
    """The census's first promise, and the one JSON Schema cannot state: `uniqueItems` compares
    whole objects, so two entries for one event with different owners are two distinct items and
    both pass. A second producer is precisely how a consumer that must act once acts twice."""
    events = [c["event"] for c in _census()["carriers"]]
    assert len(events) == len(set(events)), f"a carrier is registered twice: {events}"


def test_an_exactly_once_carrier_names_the_stamp_behind_the_claim():
    """`exactly_once_per_subject` is a promise about a durable record, not about a code path. An
    entry that claims it without naming where the record lives is a promise with nothing behind
    it — and it would read as satisfied by whatever the producing code happens to do today."""
    for c in _census()["carriers"]:
        if c["cardinality"] == "exactly_once_per_subject":
            assert c.get("stamp"), f"{c['event']} claims exactly-once and names no stamp"


def test_onboarding_completed_is_identity_owned_and_carries_subject_org_seat():
    """PRD decision 42 item 2, pinned where a consumer can read it. `seat` is what a billing domain
    charges for and `org` is what it charges; both are STATED by the producer rather than left for a
    consumer to infer, because a consumer that infers a field is a second place the answer lives."""
    by_event = {c["event"]: c for c in _census()["carriers"]}
    assert "onboarding.completed" in by_event, "the fact this slice exists to publish is unregistered"
    c = by_event["onboarding.completed"]
    assert c["owner"] == "identity", "a person entering is identity's to know"
    assert set(c["refs"]) == {"subject", "org", "seat"}
    assert c["cardinality"] == "exactly_once_per_subject", "this fact triggers billing"


# ── census ↔ the domains' own manifests ───────────────────────────────────────────────────────
@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_every_event_a_manifest_publishes_is_registered_to_that_domain(path):
    doc = json.loads(path.read_text())
    owners = _owners()
    for entry in doc.get("publishes_events") or []:
        event = entry["event"] if isinstance(entry, dict) else entry
        assert event in owners, (
            f"{path.relative_to(REPO)} publishes {event}, which is in no census — "
            f"register it in {CENSUS.relative_to(REPO)}")
        assert owners[event] == doc["domain"], (
            f"{path.relative_to(REPO)} ({doc['domain']}) publishes {event}, "
            f"which the census records as owned by {owners[event]}")


def test_no_carrier_is_registered_that_nothing_in_the_repository_publishes():
    """The other direction: an entry with no producer behind it is a wish, and it ages into a
    consumer contract for a fact that never arrives."""
    published = set()
    for path in MANIFESTS:
        doc = json.loads(path.read_text())
        for entry in doc.get("publishes_events") or []:
            published.add(entry["event"] if isinstance(entry, dict) else entry)
    for path in DECLARATIONS:
        for key in json.loads(path.read_text()).get("keys") or []:
            if key.get("class") == "publish-edge":
                published.update(key.get("publishes_events") or [])
    orphans = sorted({c["event"] for c in _census()["carriers"]} - published)
    assert not orphans, (
        f"registered but published by nothing in this repository: {orphans}. Either a producer "
        f"declares it (a manifest's publishes_events, or a config.v1 publish-edge key) or the "
        f"entry comes out.")


# ── census ↔ the publishing services' config declarations ─────────────────────────────────────
def test_identity_declares_the_publish_edge_that_carries_onboarding_completed():
    """The edge and the carrier are two halves of one fact, and each is checkable from the other.
    gate:config-contract enforces the declaration→census direction; this states the reason inside
    the domain that consumes the census, so the two cannot be silently decoupled by a refactor of
    the gate."""
    decl = json.loads(
        (REPO / "core/identity/services/admin-api/src/admin_api/config.v1.json").read_text())
    edges = [k for k in decl["keys"] if k.get("class") == "publish-edge"]
    assert edges, "admin-api declares no publish edge — identity tells nobody it onboarded anyone"
    assert any("onboarding.completed" in (k.get("publishes_events") or []) for k in edges)
    for k in edges:
        assert "default" not in k, (
            f"{k['key']} carries a default — a fallback address to publish to, invented by us, in a "
            f"deployment that deliberately runs no flows domain. Absent means absent.")
