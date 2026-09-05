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

import ast
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


# ── census ↔ the goldens gate:schema validates ────────────────────────────────────────────────
GOLDEN = CENSUS.parent / "golden"


def _golden_name(event: str) -> str:
    return f"Carrier.{event.replace('.', '-')}.json"


def test_every_registered_carrier_has_a_golden():
    """P8. `gate:schema` validates the live census plus whatever is in `golden/`, and for three of
    the seven carriers there was nothing in `golden/` — so the only document pinning those shapes
    was the census itself, which is the file under change rather than the reference a change is
    checked against. A spec that validates only the thing being edited is not a spec."""
    missing = sorted(c["event"] for c in _census()["carriers"]
                     if not (GOLDEN / _golden_name(c["event"])).is_file())
    assert not missing, (
        f"no golden for {missing} — add {[_golden_name(e) for e in missing]} to "
        f"{GOLDEN.relative_to(REPO)} so gate:schema covers every registered carrier")


def _contract_of(c: dict) -> dict:
    """The part of a carrier entry that IS the contract — what a consumer builds against.

    Deliberately not the whole object. `description` and `source_event_id` are prose, the two
    meeting goldens already carry a longer version of theirs than the census does (the empty-room
    race on the calendar-intake path is written out in the golden and summarised in the census),
    and requiring byte-equality would force that detail out of the reference instance to satisfy a
    test — which is the reference getting worse to keep a check green. What may never differ is the
    shape: who owns it, how often it fires, which refs a consumer may rely on, whether a stamp
    stands behind an exactly-once claim, and whether a producer in this tree publishes it."""
    return {"event": c["event"], "owner": c["owner"], "cardinality": c["cardinality"],
            "refs": list(c["refs"]),
            "published_by": c.get("published_by", "repository"),
            "has_stamp": bool(c.get("stamp")),
            "has_source_event_id": bool(c.get("source_event_id"))}


@pytest.mark.parametrize("carrier", _census()["carriers"], ids=lambda c: c["event"])
def test_a_golden_agrees_with_the_census_on_the_contract(carrier):
    """Two files, one fact. A golden that has drifted from the census in SHAPE is a reference that
    pins the carrier a consumer used to get, and it would go on passing gate:schema while saying
    something the live registry no longer says."""
    path = GOLDEN / _golden_name(carrier["event"])
    if not path.is_file():
        pytest.skip("covered by test_every_registered_carrier_has_a_golden")
    assert _contract_of(json.loads(path.read_text())) == _contract_of(carrier), (
        f"{path.relative_to(REPO)} and the census disagree about {carrier['event']}")


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


def _published_in_repository() -> set:
    """Every event some producer in THIS tree declares it publishes."""
    published = set()
    for path in MANIFESTS:
        doc = json.loads(path.read_text())
        for entry in doc.get("publishes_events") or []:
            published.add(entry["event"] if isinstance(entry, dict) else entry)
    for path in DECLARATIONS:
        for key in json.loads(path.read_text()).get("keys") or []:
            if key.get("class") == "publish-edge":
                published.update(key.get("publishes_events") or [])
    return published


def test_no_carrier_is_registered_that_nothing_in_the_repository_publishes():
    """The other direction: an entry with no producer behind it is a wish, and it ages into a
    consumer contract for a fact that never arrives.

    `published_by: "private"` is the one exemption, and it is narrow on purpose — the entries that
    take it are checked by the two tests below rather than waved through here. A cut of this
    repository that omits a whole producing surface (this one omits the agent domain's control
    plane) leaves carriers whose consumer contract is still true — a deployment that mounts that
    domain receives the fact — and whose producer no file here can point at. The two dishonest
    answers are deleting the entry and switching this direction off for everybody; the honest one
    is a field on the entry saying which case it is."""
    census = _census()["carriers"]
    in_tree = {c["event"] for c in census if c.get("published_by", "repository") == "repository"}
    orphans = sorted(in_tree - _published_in_repository())
    assert not orphans, (
        f"registered but published by nothing in this repository: {orphans}. Either a producer "
        f"declares it (a manifest's publishes_events, or a config.v1 publish-edge key), or the "
        f"entry comes out, or — only if its producing surface is genuinely not in this tree — it "
        f'says so with "published_by": "private".')


def test_an_owned_elsewhere_carrier_really_has_no_producer_here():
    """THE GUARD ON THE EXEMPTION, and without it the field above is just a way to switch the check
    off one row at a time.

    A carrier marked `private` must have NO declaration in this tree. The moment its producer lands
    here — the surface is merged in, or the cut changes — the flag is wrong and this fails, which is
    the only thing that makes anybody take it off again."""
    published = _published_in_repository()
    for c in _census()["carriers"]:
        if c.get("published_by") != "private":
            continue
        assert c["event"] not in published, (
            f"{c['event']} is marked published_by:private but a producer in this repository "
            f"declares it — drop the flag, the census can check this one for real now")


def test_the_privately_published_carriers_are_exactly_the_ones_this_cut_omits():
    """WHICH entries take the exemption, written down where a reviewer reads the census rather than
    left to whoever edits the file next. Both are the agent domain's, both are consumed by flows
    that only `flows_defs/production_agent.py` registers, and both producing routes live in the
    agent-api control plane this cut does not carry."""
    private = {c["event"] for c in _census()["carriers"] if c.get("published_by") == "private"}
    assert private == {"desk.unscaffolded", "claim.proposed"}
    assert all(c["owner"] == "agent" for c in _census()["carriers"]
               if c["event"] in private), "the exemption is not a licence for any other domain"


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


# ── census ↔ the wire the producers actually put the fact on ──────────────────────────────────
INTAKE = REPO / "core" / "flows" / "src" / "flows_integrations" / "flows_api.py"


def _intake_fields() -> set:
    """The field names `POST /events` reads, off the intake model's own source.

    Read rather than imported: importing `flows_api` builds the FastAPI app and wants an
    environment, and this file is stdlib-only by design."""
    for node in ast.walk(ast.parse(INTAKE.read_text())):
        if isinstance(node, ast.ClassDef) and node.name == "EventSubmission":
            return {n.target.id for n in node.body if isinstance(n, ast.AnnAssign)}
    raise AssertionError(f"no EventSubmission model in {INTAKE.relative_to(REPO)}")


def _publisher_bodies():
    """Every (file, body-keys) a publishing service builds for the intake.

    DERIVED, not listed: a publisher is a service whose `config.v1.json` declares a `publish-edge`
    key, and its body is the dict literal in that service's source carrying an `event_type` key.
    A new publisher is therefore covered the moment it declares its edge, which is the same moment
    the rest of this file starts checking its carriers."""
    for decl in DECLARATIONS:
        keys = json.loads(decl.read_text()).get("keys") or []
        if not any(k.get("class") == "publish-edge" for k in keys):
            continue
        for f in sorted(decl.parent.rglob("*.py")):
            if "/tests/" in str(f) or "/.venv/" in str(f):
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:                       # noqa: PERF203 - vendored oddities
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                names = {k.value for k in node.keys
                         if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if "event_type" in names and "source_event_id" in names:
                    yield f.relative_to(REPO), names, node.lineno


def test_every_publisher_names_the_refs_field_the_intake_actually_reads():
    """THE ONE FAILURE ON THIS EDGE THAT REPORTS ITSELF AS WORKING.

    `EventSubmission` is a plain pydantic model, so an unknown key is IGNORED rather than refused.
    A publisher that spells the refs field `subject_refs` — the name every one of them uses for the
    same value INSIDE its own python — gets a `202` and admits a fact with `refs == {}`. The
    consumer step then fails typed and non-retryable on a missing ref, on every single occurrence,
    while the producer's logs, the intake's receipt and the reaction row all look ordinary.

    Nothing else on this edge can catch it: the whole point of a publish edge is that the producer
    does not import the consumer, so there is no shared type to disagree with. This test is the
    substitute for that type, and it is worth exactly as much as its derivation — hence both
    halves are read off source, and neither names a service by hand."""
    fields = _intake_fields()
    assert "refs" in fields, "the intake stopped calling it refs; every publisher below is now wrong"
    bodies = list(_publisher_bodies())
    assert bodies, "no publishing service builds an intake body — the derivation found nothing"
    for path, names, line in bodies:
        assert names <= fields, (
            f"{path}:{line} sends {sorted(names - fields)}, which POST /events ignores rather than "
            f"refuses — the fact is admitted with those values dropped. The intake reads "
            f"{sorted(fields)}.")
