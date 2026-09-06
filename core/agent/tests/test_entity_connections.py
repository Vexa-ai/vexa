"""A call `entity_upsert` cannot read is refused BY NAME, with a 400, having written nothing.

Vexa-ai/vexa#1589, from the founder's walk on 2026-09-06 (`report_friction`, sessions
`tommy-burnette-extend` and `james-spadafora-extend`). One call carried three defects, and they are
three different failure classes, so each gets its own tests here:

  1. **A 500 where a shape belonged.** `connections=[{"from": "tommy-burnette", "type": "works_at"}]`
     — a plausible guess — reached `c["name"]` inside the writing loop and raised `KeyError: 'name'`.
     The agent got "internal server error": no shape named, nothing to fix, nothing to retry.
  2. **A gate nothing named.** The refusal said "every fact needs a source" to an agent that had
     written `— source: …` at the end of every fact it passed. Neither the message nor the tool
     description said which of the two the code reads. It reads `source=`, and now says so.
  3. **A refused call that still changed the workspace.** The reciprocal chip for the entry before
     the malformed one was already on the neighbour's page when the exception went up — a one-sided
     link, on a page the call was only passing through, with no message anywhere.

The third is the one worth keeping a guard on forever: the fix is not "catch the error", it is that
NOTHING IS WRITTEN UNTIL EVERY ARGUMENT HAS BEEN READ, so a future refusal added anywhere in this
function is safe by construction.
"""
from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings
from workspaces.shared import entities as E


# ── the shape, refused by name ───────────────────────────────────────────────────────────────────

def test_a_connection_without_a_name_says_what_a_connection_is(tmp_path):
    with pytest.raises(E.EntityMalformed) as e:
        E.upsert_entity(tmp_path, "person", "Tommy Burnette", ["Chairs it."], "the call",
                        connections=[{"from": "tommy-burnette", "type": "works_at"}])
    msg = str(e.value)
    # The keys it actually sent, named back to it — the agent has to see WHICH argument was wrong.
    assert "from" in msg and "type" in msg
    # …and the shape it should have used, so the next call is the same facts in a form that lands.
    assert '"name": "Acme"' in msg and "relation" in msg and "reverse" in msg


def test_an_unknown_key_is_refused_rather_than_dropped(tmp_path):
    """A `{"name": "Acme", "type": "works_at"}` that returned 200 would leave the caller believing
    it had recorded a relation this writer never looked at. Silence is the expensive answer."""
    with pytest.raises(E.EntityMalformed) as e:
        E.upsert_entity(tmp_path, "person", "Jane Liu", ["x"], "s",
                        connections=[{"name": "Acme", "type": "works_at"}])
    assert "type" in str(e.value)


def test_an_empty_name_is_refused_instead_of_writing_an_empty_chip(tmp_path):
    """`- [[]] — works at` is what the old code wrote for this: a bullet linking to nothing."""
    for bad in ({"name": ""}, {"name": "   "}, "", "   ", {}):
        with pytest.raises(E.EntityMalformed):
            E.upsert_entity(tmp_path, "person", "Jane Liu", ["x"], "s", connections=[bad])


def test_a_name_that_slugifies_to_nothing_is_refused(tmp_path):
    with pytest.raises(E.EntityMalformed) as e:
        E.upsert_entity(tmp_path, "person", "Jane Liu", ["x"], "s", connections=["!!!"])
    assert "nothing to file it under" in str(e.value)


def test_both_shapes_the_writer_actually_reads_still_work(tmp_path):
    """The refusal is worth nothing if it also refuses what the description promises."""
    E.upsert_entity(tmp_path, "company", "Acme", [], "the web", fields={"what": "a bank"})
    r = E.upsert_entity(tmp_path, "person", "Jane Liu", [], "her message",
                        connections=["Acme", {"name": "Bo Chen", "relation": "reports to"}])
    page = (tmp_path / r["path"]).read_text()
    assert "- [[Acme]]" in page and "- [[Bo Chen]] — reports to" in page
    assert r["back_links"] == ["kg/entities/company/acme.md"]
    assert "- [[Jane Liu]] — person" in (tmp_path / "kg/entities/company/acme.md").read_text()


def test_a_caller_supplied_reverse_is_read_from_the_end_it_names(tmp_path):
    E.upsert_entity(tmp_path, "company", "Acme", [], "the web", fields={"what": "a bank"})
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "her message",
                    connections=[{"name": "Acme", "relation": "works at", "reverse": "works here"}])
    assert "- [[Jane Liu]] — works here" in (tmp_path / "kg/entities/company/acme.md").read_text()


def test_a_bare_string_is_still_one_connection_not_a_list_of_letters(tmp_path):
    E.upsert_entity(tmp_path, "company", "Acme", [], "the web", fields={"what": "a bank"})
    r = E.upsert_entity(tmp_path, "person", "Jane Liu", [], "s", connections="Acme")
    assert "- [[Acme]]" in (tmp_path / r["path"]).read_text()


# ── a refused call writes nothing ────────────────────────────────────────────────────────────────

def test_a_refused_connection_leaves_the_neighbour_exactly_as_it_was(tmp_path):
    """THE REGRESSION THAT COST THE WALK. The good entry comes FIRST, so under the old order its
    reciprocal chip was already on Acme's page when the second entry raised."""
    E.upsert_entity(tmp_path, "company", "Acme", [], "the web", fields={"what": "a bank"})
    acme = tmp_path / "kg/entities/company/acme.md"
    before = acme.read_text()
    with pytest.raises(E.EntityMalformed):
        E.upsert_entity(tmp_path, "person", "Jane Liu", ["Joined in March."], "her message",
                        connections=[{"name": "Acme", "relation": "works at"},
                                     {"from": "jane-liu", "type": "works_at"}])
    assert acme.read_text() == before
    assert not (tmp_path / "kg/entities/person/jane-liu.md").exists()


def test_a_refused_call_onto_an_existing_page_changes_no_byte_of_it(tmp_path):
    E.upsert_entity(tmp_path, "company", "Acme", [], "the web", fields={"what": "a bank"})
    E.upsert_entity(tmp_path, "person", "Jane Liu", ["Joined in March."], "her message")
    jane = tmp_path / "kg/entities/person/jane-liu.md"
    before = jane.read_text()
    with pytest.raises(E.EntityMalformed):
        E.upsert_entity(tmp_path, "person", "Jane Liu", ["Moved to Berlin."], "her message",
                        connections=[{"name": "Acme"}, {"name": ""}])
    assert jane.read_text() == before


def test_planning_a_reciprocal_chip_writes_nothing(tmp_path):
    """The property the ordering fix rests on: the neighbour's new text is COMPUTED here, and only
    `upsert_entity`'s last three lines — after everything that can refuse — put it on disk."""
    E.upsert_entity(tmp_path, "company", "Acme", [], "the web", fields={"what": "a bank"})
    acme = tmp_path / "kg/entities/company/acme.md"
    before = acme.read_text()
    rel, text = E.plan_link_back(tmp_path, "Acme", "Jane Liu", "works here")
    assert rel == "kg/entities/company/acme.md"
    assert "- [[Jane Liu]] — works here" in text
    assert acme.read_text() == before


def test_a_missing_reciprocal_chip_is_a_change_the_caller_can_commit(tmp_path):
    """A pending back-link used to be written and then reported under `changed: False`. The endpoint
    commits only when something changed, so the neighbour's page was left modified and uncommitted —
    the same partial-write class, arrived at from the other side."""
    E.upsert_entity(tmp_path, "company", "Acme", [], "the web", fields={"what": "a bank"})
    args = dict(fields={"company": "[[Acme]]"})
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "her message", **args)
    acme = tmp_path / "kg/entities/company/acme.md"
    acme.write_text(acme.read_text().replace("- [[Jane Liu]] — works here\n", ""), encoding="utf-8")
    again = E.upsert_entity(tmp_path, "person", "Jane Liu", [], "her message", **args)
    assert again["changed"] is True
    assert again["back_links"] == ["kg/entities/company/acme.md"]
    assert "- [[Jane Liu]] — works here" in acme.read_text()


def test_nothing_new_at_all_is_still_a_no_op(tmp_path):
    """…and the clause above did not turn every repeat call into a write."""
    E.upsert_entity(tmp_path, "company", "Acme", [], "the web", fields={"what": "a bank"})
    args = dict(fields={"company": "[[Acme]]"})
    E.upsert_entity(tmp_path, "person", "Jane Liu", [], "her message", **args)
    jane = tmp_path / "kg/entities/person/jane-liu.md"
    before = jane.read_text()
    again = E.upsert_entity(tmp_path, "person", "Jane Liu", [], "her message", **args)
    assert again["changed"] is False and again["back_links"] == []
    assert jane.read_text() == before


# ── the source gate is the argument, and says so ─────────────────────────────────────────────────

def test_the_refusal_names_the_argument_that_is_the_gate(tmp_path):
    """What the walking agent had in front of it: every fact attributed inline, no `source=`, and a
    refusal that told it to do the thing it had just done."""
    with pytest.raises(E.EntityRefused) as e:
        E.upsert_entity(tmp_path, "person", "Tommy Burnette",
                        ["Chairs the TSC — source: the 2026-09-06 call"], "")
    msg = str(e.value)
    assert "`source=`" in msg
    assert "— source: …" in msg and "is not read" in msg
    assert "kg/MISSING.md" in msg


def test_a_fact_that_carries_its_own_source_suffix_is_attributed_once(tmp_path):
    """`## Sources` and the `sources:` frontmatter are both built from the ARGUMENT, so a suffix the
    caller typed reaches neither — and printing ours after theirs put the clause on the line twice."""
    r = E.upsert_entity(tmp_path, "person", "Tommy Burnette",
                        ["Chairs the TSC — source: the call"], "the 2026-09-06 call",
                        today="2026-09-06")
    page = (tmp_path / r["path"]).read_text()
    assert "- Chairs the TSC — source: the 2026-09-06 call" in page
    assert page.count("— source:") == 1
    assert "sources: [the 2026-09-06 call]" in page


def test_the_same_fact_restated_bare_is_still_the_same_fact(tmp_path):
    E.upsert_entity(tmp_path, "person", "Tommy Burnette",
                    ["Chairs the TSC — source: the call"], "the call")
    again = E.upsert_entity(tmp_path, "person", "Tommy Burnette", ["Chairs the TSC"], "the call")
    assert again["changed"] is False


# ── the description and the code cannot drift ────────────────────────────────────────────────────

def test_the_generated_connection_shape_matches_its_declared_file():
    """`shared/entity_connection.v1.txt` is what the MCP's `entity_upsert` description must state.

    Same mechanism as `entity_sections.v1.txt`, for the same reason and now with an incident behind
    it. This half says the file still matches the generator; the rig's own suite says its
    description carries every line. Neither side reads the other's source."""
    decl = pathlib.Path(__file__).resolve().parents[1] / "shared" / "entity_connection.v1.txt"
    body = "\n".join(ln for ln in decl.read_text().splitlines() if not ln.startswith("#"))
    assert body.strip() == E.tool_connection_text().strip()


def test_the_declared_shape_names_every_key_the_writer_reads():
    for key in E.CONNECTION_KEYS:
        assert f"`{key}`" in E.tool_connection_text()


# ── the endpoint: a shape it could not read is a 400, not a 500 ──────────────────────────────────

class _FakeRuntime:
    def spawn(self, *a, **kw):
        return "unit-1"

    def stop(self, *a, **kw):
        return None


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools):
        return "tok"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXA_WORKSPACES_DIR", str(tmp_path))
    return TestClient(create_app(
        Dispatcher(load_settings(), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(tmp_path))))


H = {"X-User-Id": "u_jane"}


def test_a_connection_without_a_name_is_a_400_naming_the_shape(client, tmp_path):
    """It was a 500 — `KeyError: 'name'` off the end of an uncaught exception."""
    r = client.post("/api/workspace/entity", headers=H, json={
        "kind": "person", "name": "Tommy Burnette", "facts": ["Chairs it."], "source": "the call",
        "connections": [{"from": "tommy-burnette", "type": "works_at"}]})
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "from" in detail and "type" in detail and '"name": "Acme"' in detail
    assert not (tmp_path / "u_jane" / "kg" / "entities").exists()


def test_the_400_leaves_a_neighbours_page_untouched(client, tmp_path):
    client.post("/api/workspace/entity", headers=H, json={
        "kind": "company", "name": "Acme", "facts": ["A bank."], "source": "the web"})
    acme = tmp_path / "u_jane" / "kg/entities/company/acme.md"
    before = acme.read_text()
    r = client.post("/api/workspace/entity", headers=H, json={
        "kind": "person", "name": "Jane Liu", "facts": ["Joined."], "source": "her message",
        "connections": [{"name": "Acme", "relation": "works at"}, {"type": "works_at"}]})
    assert r.status_code == 400, r.text
    assert acme.read_text() == before
    assert not (tmp_path / "u_jane" / "kg/entities/person/jane-liu.md").exists()


def test_the_sourceless_refusal_stays_422_and_names_the_argument(client):
    """The two refusals are different answers: 400 is "the argument was unreadable, send it again in
    this shape"; 422 is "the request was fine and the rule says no". Only one of them is a retry."""
    r = client.post("/api/workspace/entity", headers=H, json={
        "kind": "person", "name": "Somebody", "source": "",
        "facts": ["a claim — source: the call"]})
    assert r.status_code == 422
    assert "`source=`" in r.json()["detail"]
